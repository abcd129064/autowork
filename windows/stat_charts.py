# -*- coding: utf-8 -*-
"""统计图表共享模块（pyecharts + QWebEngineView 内嵌）

售后/跑视频面板共用的统计图表页：pyecharts 在 Python 侧生成 ECharts
option，内联本地 vendor/echarts.min.js 拼成单文件 HTML，由 QWebEngineView
渲染。离线无 CDN，深浅主题自适应。数据经 AftersaleDBWorker 后台聚合，
不阻塞 UI。
"""
import json
import os
import sys
from collections import defaultdict

from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QWidget, QVBoxLayout

from qfluentwidgets import isDarkTheme, qconfig

_ECHARTS_JS = None


def _resource_path(rel: str) -> str:
    """开发取项目根，打包取 sys._MEIPASS（spec datas 需包含 vendor/）"""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def _echarts_js() -> str:
    global _ECHARTS_JS
    if _ECHARTS_JS is None:
        with open(_resource_path("vendor/echarts.min.js"), "r",
                  encoding="utf-8") as f:
            _ECHARTS_JS = f.read()
    return _ECHARTS_JS


def _theme_colors(dark: bool) -> dict:
    return {
        "text": "#e8eaed" if dark else "#1f1f1f",
        "axis": "#9aa0a6" if dark else "#888888",
        "split": "rgba(128,128,128,0.15)",
    }


def _polish(option: dict, dark: bool) -> dict:
    """统一主题：透明底 + 文字/坐标轴/图例颜色跟随深浅色"""
    c = _theme_colors(dark)
    option = dict(option)
    option.setdefault("backgroundColor", "transparent")
    option.setdefault("textStyle", {}).setdefault("color", c["text"])
    for key in ("xAxis", "yAxis"):
        ax = option.get(key)
        if isinstance(ax, dict):
            ax.setdefault("axisLabel", {}).setdefault("color", c["axis"])
            ax.setdefault("axisLabel", {}).setdefault("fontSize", 11)
    for key in ("legend", "title"):
        node = option.get(key)
        if isinstance(node, dict):
            node.setdefault("textStyle", {}).setdefault("color", c["text"])
    return option


def build_html(options: list, dark: bool = False) -> str:
    """把多个 ECharts option 拼成单文件 HTML（内联 echarts.min.js，垂直堆叠）"""
    c = _theme_colors(dark)
    parts = []
    for i, option in enumerate(options or []):
        did = f"chart_{i}"
        opt = json.dumps(_polish(option, dark), ensure_ascii=False)
        parts.append(
            f'<div id="{did}" style="width:100%;height:300px;margin:0 0 6px 0;"></div>'
            f'<script>echarts.init(document.getElementById("{did}")).setOption({opt});</script>'
        )
    html = (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<style>html,body{margin:0;padding:14px;background:transparent;'
        f'color:{c["text"]};font-family:"Microsoft YaHei",sans-serif;}}</style>'
        '</head><body>'
        '<script>' + _echarts_js() + '</script>'
        + "".join(parts) +
        '</body></html>'
    )
    return html


class ChartPage(QWidget):
    """统计图表页：后台聚合 + WebEngine 渲染 pyecharts 图表

    builder: 无参函数，返回 list[dict]（ECharts option）。showEvent 首次
    显示时后台加载一次，之后不重复查询（数据刷新由面板入口显式触发）。
    """

    def __init__(self, builder, parent=None):
        super().__init__(parent)
        self._builder = builder
        self._options = []
        self._worker = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._view = QWebEngineView(self)
        lay.addWidget(self._view)
        # 容器与页面透明：深色模式下不残留白底（WebEngine 默认不透明白底）
        self._view.setStyleSheet("background: transparent;")
        try:
            from PySide6.QtGui import QColor
            self._view.page().setBackgroundColor(QColor(0, 0, 0, 0))
        except Exception:
            pass
        self._view.setHtml(
            '<body style="background:transparent;color:#888;'
            'font-family:Microsoft YaHei;">图表加载中…</body>')
        # 需求28：跟随全局主题切换实时重渲（深浅色文字/轴线/容器色）
        try:
            qconfig.themeChanged.connect(self._reapply)
        except Exception:
            pass

    def _reapply(self, _theme=None):
        """主题切换后按当前主题重渲染（已加载的图表不重新查询）"""
        if self._options:
            self._view.setHtml(build_html(self._options, isDarkTheme()))

    def showEvent(self, event):
        super().showEvent(event)
        if not self._options:
            self.load()

    def load(self):
        from workers.aftersale_worker import AftersaleDBWorker
        self._worker = AftersaleDBWorker(self._builder)
        self._worker.result_ready.connect(self._on_loaded)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_loaded(self, options):
        self._options = list(options or [])
        self._view.setHtml(build_html(self._options, isDarkTheme()))

    def _on_error(self, msg):
        self._view.setHtml(
            '<body style="background:transparent;color:#cf4452;'
            f'font-family:Microsoft YaHei;">统计加载失败：{msg}</body>')

    def refresh(self):
        self._options = []
        self.load()


# ==================== 售后统计 ====================

def _aftersale_options() -> list:
    from pyecharts.charts import Line, Pie, Bar
    from pyecharts import options as opts
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
    line = (
        Line().add_xaxis(dates).add_yaxis("解决率%", rate, is_smooth=True)
        .set_global_opts(
            title_opts=opts.TitleOpts(title="售后解决率趋势"),
            legend_opts=opts.LegendOpts(is_show=False),
            tooltip_opts=opts.TooltipOpts(trigger="axis"),
            yaxis_opts=opts.AxisOpts(max_=100))
    )

    # 2) 问题类型分布
    cnt = defaultdict(int)
    for r in rows:
        cnt[str(r.get("issue_type") or "未填")] += 1
    pie_type = (
        Pie().add("", list(cnt.items()), radius=["35%", "65%"])
        .set_global_opts(
            title_opts=opts.TitleOpts(title="问题类型分布"),
            legend_opts=opts.LegendOpts(orient="vertical", pos_right=0,
                                        pos_top="middle"))
    )

    # 3) 地区 TOP 分布
    reg = defaultdict(int)
    for r in rows:
        reg[str(r.get("region") or "未填")] += 1
    reg_top = sorted(reg.items(), key=lambda x: -x[1])[:10]
    bar_reg = (
        Bar().add_xaxis([k for k, _ in reg_top])
        .add_yaxis("数量", [v for _, v in reg_top])
        .reversal_axis()
        .set_global_opts(
            title_opts=opts.TitleOpts(title="地区分布 TOP10"),
            legend_opts=opts.LegendOpts(is_show=False),
            tooltip_opts=opts.TooltipOpts(trigger="axis"))
    )

    # 4) 我方问题 / 主动发起 占比
    our_p = sum(1 for r in rows if str(r.get("is_our_problem") or "") == "是")
    ini_p = sum(1 for r in rows if str(r.get("is_initiative") or "") == "是")
    n = len(rows) or 1
    pie_judge = (
        Pie().add("我方问题", [("是", our_p), ("否", n - our_p)],
                  radius=["30%", "55%"], center=["25%", "50%"])
        .add("主动发起", [("是", ini_p), ("否", n - ini_p)],
             radius=["30%", "55%"], center=["75%", "50%"])
        .set_global_opts(title_opts=opts.TitleOpts(title="我方问题 / 主动发起占比"),
                         legend_opts=opts.LegendOpts(orient="vertical", pos_right=0,
                                                     pos_top="middle"))
    )

    return [json.loads(c.dump_options()) for c in (line, pie_type, bar_reg, pie_judge)]


# ==================== 跑视频统计 ====================

def _ledger_options() -> list:
    from pyecharts.charts import Line, Pie, Bar
    from pyecharts import options as opts
    from database import ledger_db

    _, rows = ledger_db.query_page(1, 100000)

    # 1) 四分类趋势（按 occurred_at 日期，回退 created_at）
    cats = ["问题", "未复现", "精度", "使用"]
    by_date_cat = defaultdict(lambda: defaultdict(int))
    for r in rows:
        d = str(r.get("occurred_at") or r.get("created_at") or "")[:10]
        if not d:
            continue
        by_date_cat[d][str(r.get("category") or "未填")] += 1
    dates = sorted(by_date_cat)
    trend = Line()
    trend.add_xaxis(dates)
    for c in cats:
        trend.add_yaxis(c, [by_date_cat[d].get(c, 0) for d in dates],
                        is_smooth=True)
    trend.set_global_opts(
        title_opts=opts.TitleOpts(title="四分类记录趋势"),
        legend_opts=opts.LegendOpts(pos_top=24),
        tooltip_opts=opts.TooltipOpts(trigger="axis"))

    # 2) 分类占比
    dist = defaultdict(int)
    for r in rows:
        dist[str(r.get("category") or "未填")] += 1
    pie_cat = (
        Pie().add("", list(dist.items()), radius=["35%", "65%"])
        .set_global_opts(
            title_opts=opts.TitleOpts(title="分类占比"),
            legend_opts=opts.LegendOpts(orient="vertical", pos_right=0,
                                        pos_top="middle"))
    )

    # 3) 署名工作量
    signers = ledger_db.stats_by_signer() or []
    signers = sorted(signers, key=lambda s: -(s.get("total") or 0))[:15]
    bar_sign = (
        Bar().add_xaxis([s.get("signer") or "未署名" for s in signers])
        .add_yaxis("问题", [s.get("问题", 0) for s in signers])
        .add_yaxis("未复现", [s.get("未复现", 0) for s in signers])
        .add_yaxis("精度", [s.get("精度", 0) for s in signers])
        .add_yaxis("使用", [s.get("使用", 0) for s in signers])
        .set_global_opts(
            title_opts=opts.TitleOpts(title="署名工作量 TOP15"),
            legend_opts=opts.LegendOpts(pos_top=24),
            tooltip_opts=opts.TooltipOpts(trigger="axis"))
    )

    return [json.loads(c.dump_options()) for c in (trend, pie_cat, bar_sign)]
