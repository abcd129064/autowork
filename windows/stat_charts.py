# -*- coding: utf-8 -*-
"""统计图表共享模块（matplotlib FigureCanvasQTAgg 内嵌）

售后/跑视频面板共用的统计图表页：builder 返回 list[dict] 聚合数据，
由 matplotlib 在主线程同步渲染成 Figure（零 GPU 合成、零 DirectComposition，
与 FluentWindowBase 的 Mica DWM backdrop 物理兼容）。

方案 B 重写背景：上一轮 QWebEngineView 路线（即使设 --disable-direct-composition）
在 PySide6 6.11 下不仅没救 Mica，还引入了导航栏变黑 + WebEngine viewport 异常
导致图表挤压到 ~100px 宽。matplotlib 走 QPainter 软件绘制，与 Mica 无任何
GPU/DComp 冲突，物理上不可能产生黑屏/退色问题。代价是失去 ECharts 交互
（tooltip/图例点击），保留 NavigationToolbar 缩放/拖动/另存为。
"""
from collections import defaultdict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel

from qfluentwidgets import isDarkTheme, qconfig


def _theme_colors(dark: bool) -> dict:
    """统计图表统一配色：文字/坐标轴/分割线/图表区背景/系列 6 色/成功/中性。

    系列色浅深色共用（蓝/绿/橙/红/紫/青），对比度足够；bg 与 design_tokens
    同源（深色 #2a2a2a / 浅色 #f5f5f5）。
    """
    return {
        "text": "#e8eaed" if dark else "#1f1f1f",
        "axis": "#9aa0a6" if dark else "#888888",
        "split": "#666666" if dark else "#cccccc",
        "bg": "#2a2a2a" if dark else "#f5f5f5",
        # 6 色系列：蓝/绿/橙/红/紫/青（饼/柱/线通用）
        "series": ["#4a90e2", "#50c878", "#f5a623", "#cf4452", "#9b59b6", "#1abc9c"],
        "success": "#50c878",
        "neutral": "#888888",
    }


class ChartPage(QWidget):
    """统计图表页：matplotlib FigureCanvas + NavigationToolbar 渲染聚合数据。

    builder: 无参函数，返回 list[dict] 聚合数据（每个 dict 含 kind/title/data 等）。
    kind: 'aftersale' 或 'ledger'，分派到对应 _render_* 方法。
    showEvent 首次显示才 _ensure_canvas（避免窗口构造时即创建 matplotlib 资源）。
    主题切换通过 qconfig.themeChanged 重渲（不重新查询数据）。
    """

    def __init__(self, builder, kind, parent=None):
        super().__init__(parent)
        self._builder = builder
        self._kind = kind
        self._data = []
        self._worker = None
        self._figure = None
        self._canvas = None
        self._toolbar = None
        self._loaded_once = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        # 占位：canvas 创建前显示加载提示
        self._placeholder = QLabel("图表加载中…", self)
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet("color:#888;font-family:Microsoft YaHei;")
        lay.addWidget(self._placeholder)
        # 主题切换重渲
        try:
            qconfig.themeChanged.connect(self._reapply)
        except Exception:
            pass

    def _ensure_canvas(self):
        """懒创建 matplotlib FigureCanvas + NavigationToolbar（首次进入统计页时）。"""
        if self._canvas is not None:
            return
        import matplotlib
        matplotlib.use("QtAgg")
        # 中文字体配置：Windows 优先 Microsoft YaHei（系统自带），
        # 兜底 DejaVu Sans（matplotlib 默认）。offscreen 冒烟环境无 YaHei
        # 会触发 glyph missing warning，但不影响 Figure 生成。
        import matplotlib.pyplot as plt
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
        from matplotlib.backends.backend_qtagg import (
            FigureCanvasQTAgg, NavigationToolbar2QT)
        from matplotlib.figure import Figure
        self._figure = Figure(figsize=(7, 9), dpi=100, tight_layout=True)
        self._canvas = FigureCanvasQTAgg(self._figure)
        # 替换占位
        self.layout().removeWidget(self._placeholder)
        self._placeholder.setParent(None)
        self._placeholder.deleteLater()
        self._placeholder = None
        self.layout().addWidget(self._canvas)
        self._toolbar = NavigationToolbar2QT(self._canvas, self)
        self.layout().addWidget(self._toolbar)

    def _reapply(self, _theme=None):
        """主题切换后按当前主题重渲染（已加载数据不重新查询）。"""
        if self._canvas is not None and self._data:
            self._render(self._data)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded_once:
            self._loaded_once = True
            self._ensure_canvas()
            self.load()

    def load(self):
        from workers.aftersale_worker import AftersaleDBWorker
        self._worker = AftersaleDBWorker(self._builder)
        self._worker.result_ready.connect(self._on_loaded)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_loaded(self, data):
        self._data = list(data or [])
        if self._canvas is not None:
            self._render(self._data)

    def _on_error(self, msg):
        if self._figure is None or self._canvas is None:
            return
        self._figure.clear()
        ax = self._figure.add_subplot(111)
        ax.set_axis_off()
        c = _theme_colors(isDarkTheme())
        self._figure.patch.set_facecolor(c["bg"])
        ax.text(0.5, 0.5, f"统计加载失败：{msg}",
                ha="center", va="center",
                color="#cf4452", fontsize=12)
        self._canvas.draw_idle()

    def _render(self, data):
        if self._kind == "aftersale":
            self._render_aftersale(data)
        elif self._kind == "ledger":
            self._render_ledger(data)

    # ==================== 售后 4 图 ====================

    def _render_aftersale(self, data):
        """售后 4 图：解决率趋势 / 类型分布 / 地区 TOP10 / 我方问题·主动发起双饼。"""
        self._figure.clear()
        c = _theme_colors(isDarkTheme())
        bg = c["bg"]
        self._figure.patch.set_facecolor(bg)
        n = max(1, len(data))
        for i, chart in enumerate(data, 1):
            ax = self._figure.add_subplot(n, 1, i)
            ax.set_facecolor(bg)
            self._style_axes(ax, c)
            kind = chart.get("kind")
            if kind == "line":
                ax.plot(chart["x"], chart["y"],
                        color=c["series"][0], marker="o", linewidth=2)
                ax.set_title(chart["title"], color=c["text"],
                             fontsize=12, loc="left")
                ax.set_ylabel("%", color=c["axis"])
                ax.set_ylim(0, 100)
                ax.grid(True, color=c["split"], linewidth=0.5)
                for label in ax.get_xticklabels():
                    label.set_rotation(30)
                    label.set_ha("right")
            elif kind == "pie":
                labels = [str(k) for k, _ in chart["data"]]
                values = [v for _, v in chart["data"]]
                colors = c["series"][:len(values)]
                ax.pie(values, labels=labels, colors=colors,
                       autopct="%1.0f%%",
                       textprops={"color": c["text"], "fontsize": 9},
                       wedgeprops={"edgecolor": bg, "linewidth": 1})
                ax.set_title(chart["title"], color=c["text"],
                             fontsize=12, loc="left")
            elif kind == "barh":
                labels = chart["labels"]
                values = chart["values"]
                y_pos = list(range(len(labels)))
                ax.barh(y_pos, values, color=c["series"][0])
                ax.set_yticks(y_pos)
                ax.set_yticklabels(labels, color=c["axis"], fontsize=9)
                ax.set_title(chart["title"], color=c["text"],
                             fontsize=12, loc="left")
                ax.invert_yaxis()
                ax.grid(True, axis="x", color=c["split"], linewidth=0.5)
            elif kind == "pie_pair":
                ax.set_axis_off()
                inset_axes = [ax.inset_axes([0.0, 0.0, 0.45, 1.0]),
                              ax.inset_axes([0.55, 0.0, 0.45, 1.0])]
                for sub_ax, (name, items) in zip(inset_axes, chart["pairs"]):
                    values = [v for _, v in items]
                    sub_ax.pie(values, labels=["是", "否"],
                               colors=[c["success"], c["neutral"]],
                               autopct="%1.0f%%",
                               textprops={"color": c["text"], "fontsize": 9},
                               wedgeprops={"edgecolor": bg, "linewidth": 1})
                    sub_ax.set_title(name, color=c["text"], fontsize=10)
                ax.set_title(chart["title"], color=c["text"],
                             fontsize=12, loc="left")
        self._canvas.draw_idle()

    # ==================== 跑视频 3 图 ====================

    def _render_ledger(self, data):
        """跑视频 3 图：四分类趋势 / 分类占比 / 署名工作量 TOP15 stacked。"""
        self._figure.clear()
        c = _theme_colors(isDarkTheme())
        bg = c["bg"]
        self._figure.patch.set_facecolor(bg)
        n = max(1, len(data))
        for i, chart in enumerate(data, 1):
            ax = self._figure.add_subplot(n, 1, i)
            ax.set_facecolor(bg)
            self._style_axes(ax, c)
            kind = chart.get("kind")
            if kind == "line_multi":
                for j, ser in enumerate(chart["series"]):
                    ax.plot(chart["x"], ser["values"],
                            color=c["series"][j % len(c["series"])],
                            marker="o", linewidth=1.5, label=ser["name"])
                ax.set_title(chart["title"], color=c["text"],
                             fontsize=12, loc="left")
                ax.legend(loc="upper left", frameon=False, fontsize=9,
                          labelcolor=c["text"])
                ax.grid(True, color=c["split"], linewidth=0.5)
                for label in ax.get_xticklabels():
                    label.set_rotation(30)
                    label.set_ha("right")
            elif kind == "pie":
                labels = [str(k) for k, _ in chart["data"]]
                values = [v for _, v in chart["data"]]
                colors = c["series"][:len(values)]
                ax.pie(values, labels=labels, colors=colors,
                       autopct="%1.0f%%",
                       textprops={"color": c["text"], "fontsize": 9},
                       wedgeprops={"edgecolor": bg, "linewidth": 1})
                ax.set_title(chart["title"], color=c["text"],
                             fontsize=12, loc="left")
            elif kind == "bar_stacked":
                labels = chart["labels"]
                bottoms = [0] * len(labels)
                for j, ser in enumerate(chart["series"]):
                    ax.bar(labels, ser["values"], bottom=bottoms,
                           color=c["series"][j % len(c["series"])],
                           label=ser["name"])
                    bottoms = [b + v for b, v in zip(bottoms, ser["values"])]
                ax.set_title(chart["title"], color=c["text"],
                             fontsize=12, loc="left")
                ax.legend(loc="upper right", frameon=False, fontsize=9,
                          labelcolor=c["text"])
                ax.tick_params(axis="x", colors=c["axis"], labelsize=8)
                for label in ax.get_xticklabels():
                    label.set_rotation(30)
                    label.set_ha("right")
                ax.grid(True, axis="y", color=c["split"], linewidth=0.5)
        self._canvas.draw_idle()

    @staticmethod
    def _style_axes(ax, c):
        """统一 axes 边框/刻度颜色。"""
        ax.tick_params(axis="both", colors=c["axis"], labelsize=9)
        for spine in ax.spines.values():
            spine.set_color(c["axis"])
            spine.set_linewidth(0.8)

    def refresh(self):
        """手动刷新：重新查询并渲染（canvas 未创建时等下次 showEvent 再加载）。"""
        self._data = []
        if self._canvas is not None:
            self.load()


# ==================== 售后统计 ====================

def _aftersale_options() -> list:
    """售后统计聚合数据（4 图：line/pie/barh/pie_pair），返回 list[dict]。

    数据经 aftersale_db.query_page 拉全量 + Python 侧聚合（双后端兼容）。
    """
    from database import aftersale_db

    _, rows = aftersale_db.query_page(1, 100000)

    # 1) 解决率趋势（按 occurred_at 日期，回退 created_at）
    by_date = defaultdict(lambda: [0, 0])
    for r in rows:
        d = str(r.get("occurred_at") or r.get("created_at") or "")[:10]
        if not d:
            continue
        by_date[d][0] += 1
        if str(r.get("resolved") or "") == "是":
            by_date[d][1] += 1
    dates = sorted(by_date)
    total_s = [by_date[d][0] for d in dates]
    res_s = [by_date[d][1] for d in dates]
    rate = [round(b / a * 100) if a else 0 for a, b in zip(total_s, res_s)]
    chart1 = {"kind": "line", "title": "售后解决率趋势",
              "x": dates, "y": rate}

    # 2) 问题类型分布
    cnt = defaultdict(int)
    for r in rows:
        cnt[str(r.get("issue_type") or "未填")] += 1
    chart2 = {"kind": "pie", "title": "问题类型分布",
              "data": list(cnt.items())}

    # 3) 地区 TOP10
    reg = defaultdict(int)
    for r in rows:
        reg[str(r.get("region") or "未填")] += 1
    reg_top = sorted(reg.items(), key=lambda x: -x[1])[:10]
    chart3 = {"kind": "barh", "title": "地区分布 TOP10",
              "labels": [k for k, _ in reg_top],
              "values": [v for _, v in reg_top]}

    # 4) 我方问题 / 主动发起 双饼
    n = len(rows) or 1
    our = sum(1 for r in rows if str(r.get("is_our_problem") or "") == "是")
    ini = sum(1 for r in rows if str(r.get("is_initiative") or "") == "是")
    chart4 = {"kind": "pie_pair", "title": "我方问题 / 主动发起占比",
              "pairs": [
                  ("我方问题", [("是", our), ("否", n - our)]),
                  ("主动发起", [("是", ini), ("否", n - ini)]),
              ]}

    return [chart1, chart2, chart3, chart4]


# ==================== 跑视频统计 ====================

def _ledger_options() -> list:
    """跑视频统计聚合数据（3 图：line_multi/pie/bar_stacked），返回 list[dict]。

    数据经 ledger_db.query_page 拉全量 + ledger_db.stats_by_signer 取署名统计。
    """
    from database import ledger_db

    _, rows = ledger_db.query_page(1, 100000)
    cats = ["问题", "未复现", "精度", "使用"]

    # 1) 四分类趋势
    by_date_cat = defaultdict(lambda: defaultdict(int))
    for r in rows:
        d = str(r.get("occurred_at") or r.get("created_at") or "")[:10]
        if not d:
            continue
        by_date_cat[d][str(r.get("category") or "未填")] += 1
    dates = sorted(by_date_cat)
    chart1 = {
        "kind": "line_multi", "title": "四分类记录趋势",
        "x": dates,
        "series": [
            {"name": cat, "values": [by_date_cat[d].get(cat, 0) for d in dates]}
            for cat in cats
        ],
    }

    # 2) 分类占比
    dist = defaultdict(int)
    for r in rows:
        dist[str(r.get("category") or "未填")] += 1
    chart2 = {"kind": "pie", "title": "分类占比", "data": list(dist.items())}

    # 3) 署名工作量 TOP15（stacked by cats）
    signers_raw = ledger_db.stats_by_signer() or []
    signers = sorted(signers_raw, key=lambda s: -(s.get("total") or 0))[:15]
    chart3 = {
        "kind": "bar_stacked", "title": "署名工作量 TOP15",
        "labels": [s.get("signer") or "未署名" for s in signers],
        "series": [{"name": cat, "values": [s.get(cat, 0) for s in signers]}
                   for cat in cats],
    }

    return [chart1, chart2, chart3]