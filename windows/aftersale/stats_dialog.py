# -*- coding: utf-8 -*-
"""售后统计弹窗（「售后总数」卡点击后打开）

AfterSaleStatsDialog：独立可缩放非模态窗口（与双击行编辑面板同范式，
非 Qt 原生控件）——套用全局 QSS 的独立顶层 QDialog，可拖动/缩放、
非模态不阻塞记录页操作。内容：

- KPI 行：售后总数 / 已解决 / 未解决 / 解决率 / 主动发起 / 我方问题
- 每日售后趋势：QPainter 自绘柱状图（默认展示当前筛选周期跨度，
  可切换 近7天 / 近30天 / 全部），悬停显示当日条数与已解决数
- 地区分布：QPainter 自绘水平条形（数量 + 占比，降序）
- 问题类型分布：表格（数量 / 已解决 / 未解决）+ 堆叠条 delegate

统计口径与四张概览卡完全一致（keyword + issue_type + cycle_start 动态
归属过滤，不带 resolved/is_initiative/is_our_problem 筛选），数据经
``aftersale_db.query_stats_detail`` 一次取回（异步 Worker，不阻塞 UI）。

图表全部 QPainter 自绘（零新依赖、深浅主题自适应）；若视觉效果不达预期，
可降级为打开本地 HTML 网页（预留注释标记切换点）。
"""

import math
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QRect, QRectF, QSize, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QScrollArea, QSizePolicy,
    QStyledItemDelegate, QStyle, QTableWidgetItem, QToolTip,
    QVBoxLayout, QWidget)

from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, FluentIcon,
    PushButton, SegmentedWidget, TableWidget, isDarkTheme)

from core.design_tokens import SEMANTIC, darken
from core.theme_qss import apply_window_qss, current_accent_hex
from database import aftersale_db
from workers.aftersale_worker import AftersaleDBWorker

# 主题取色辅助（浅/深主题各自一套，避免硬编码）
# QSS 能写主题变量，但 QPainter 取色必须落到具体色值，故统一收口在这里
def _text_color() -> str:
    """正文与轴标签色：图中所有文字共用，保证与当前主题一致。"""
    return "#6B7280" if not isDarkTheme() else "#8892a2"


def _grid_color() -> str:
    """网格线与空条底色；刻意比正文浅，避免盖过数据本身。"""
    return "#E0E0E0" if not isDarkTheme() else "#383838"


def _muted_color() -> str:
    """次要说明文字色（加载提示、图例说明）。"""
    return "#9CA3AF" if not isDarkTheme() else "#555f6b"


# ==================== 每日趋势柱状图（QPainter 自绘） ====================

class _DailyBarChart(QWidget):
    """每日售后趋势柱状图：X=日期（MM-DD），Y=条数；峰值日高亮，悬停提示。

    数据按日期升序传入，柱宽随天数自适应：7 天与 90 天共用一套绘制，
    柱宽被夹在 4~30px，避免天数少时柱子过粗、天数多时细到看不见。
    """

    # 左/右/上/下留白：左侧放 Y 轴刻度，底部放 MM-DD 标签
    _M_L, _M_R, _M_T, _M_B = 44, 10, 12, 26

    def __init__(self, parent=None):
        """控件只负责画，数据由外部 setData 注入，便于刷新时整体替换。"""
        super().__init__(parent)
        self._data = []      # [{date, count, resolved}, ...] 升序
        self._hover = -1
        self.setMouseTracking(True)
        # 至少 170px：再矮下去 Y 轴刻度就会和底部日期标签叠在一起
        self.setMinimumHeight(170)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

    def sizeHint(self):
        """首选尺寸按可读柱宽估算，实际高度由卡片布局拉伸决定。"""
        return QSize(620, 190)

    def setData(self, daily):
        """整体替换数据并清空悬停；排序由调用方保证（升序）。"""
        self._data = list(daily or [])
        self._hover = -1
        self.update()

    def mouseMoveEvent(self, e):
        """按 x 落点反算列号并弹 tooltip；越界时只清理高亮。

        用 mapToGlobal 取全局坐标：tooltip 是独立顶层窗口，
        传控件内坐标会在多屏/缩放场景下飘到错误位置。
        """
        if not self._data:
            return
        n = len(self._data)
        plot_w = max(1.0, self.width() - self._M_L - self._M_R)
        slot = plot_w / n
        idx = int((e.position().x() - self._M_L) // slot) if slot else -1
        if 0 <= idx < n:
            if idx != self._hover:
                self._hover = idx
                self.update()
            d = self._data[idx]
            QToolTip.showText(
                self.mapToGlobal(e.position().toPoint()),
                f"{d['date']} · {d['count']} 条（已解决 {d['resolved']}）", self)
        else:
            QToolTip.hideText()
            if self._hover != -1:
                self._hover = -1
                self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        """离开控件即清高亮，否则鼠标移出后最后一根柱子会一直描边。"""
        self._hover = -1
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e):
        """全量重绘：网格、柱子、悬停描边与日期标签一次性画出。

        空数据单独走占位文案，避免 max() 对空序列抛异常；
        max_v 用 or 1 兜底，防止全零数据时除零。
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        if not self._data:
            p.setPen(QColor(_text_color()))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "当前趋势范围内暂无记录")
            return
        acc = current_accent_hex()
        peak_c = darken(acc, 0.16)
        plot_x, plot_y = self._M_L, self._M_T
        plot_w = w - self._M_L - self._M_R
        plot_h = h - self._M_T - self._M_B
        # max_v 用于归一化柱高；全零数据用 1 兜底，避免除零
        max_v = max(d["count"] for d in self._data) or 1
        n = len(self._data)
        slot = plot_w / n
        bar_w = min(30.0, max(4.0, slot * 0.62))

        # 水平网格线（4 段）与 Y 轴标签
        p.setPen(QPen(QColor(_grid_color()), 1))
        for i in range(4):
            y = plot_y + plot_h * i / 3
            p.drawLine(plot_x, y, plot_x + plot_w, y)
        f = p.font()
        f.setPointSizeF(8.5)
        p.setFont(f)
        p.setPen(QColor(_text_color()))
        p.drawText(QRect(0, plot_y - 4, self._M_L - 6, 14),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   str(max_v))
        p.drawText(QRect(0, plot_y + plot_h - 7, self._M_L - 6, 14),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   "0")

        # 柱体（峰值日深色高亮，悬停日描边 + 数值）
        for i, d in enumerate(self._data):
            x = plot_x + i * slot + (slot - bar_w) / 2
            bh = (d["count"] / max_v) * plot_h
            is_peak = d["count"] == max_v and max_v > 0
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(peak_c if is_peak else acc))
            p.drawRoundedRect(QRectF(x, plot_y + plot_h - bh, bar_w, bh),
                              2, 2)
            if i == self._hover:
                p.setPen(QPen(QColor(_text_color()), 1))
                p.drawRect(QRectF(x - 1, plot_y + plot_h - bh - 1,
                                  bar_w + 2, bh + 2))
                p.setPen(QColor(_text_color()))
                p.drawText(QRectF(x - 34, plot_y + plot_h - bh - 17,
                                  bar_w + 68, 14),
                           Qt.AlignmentFlag.AlignCenter, str(d["count"]))

        # 日期标签（MM-DD）：天数多时抽稀，最多约 12 个
        # 步长按"最多约 12 个标签"反推；90 天全画会把日期挤成一条黑带
        step = 1 if n <= 14 else math.ceil(n / 12)
        p.setPen(QColor(_text_color()))
        for i, d in enumerate(self._data):
            if i % step:
                continue
            cx = plot_x + i * slot + slot / 2
            p.drawText(QRectF(cx - 28, plot_y + plot_h + 6, 56, 14),
                       Qt.AlignmentFlag.AlignCenter, d["date"][5:])


# ==================== 地区水平条形图（QPainter 自绘） ====================

class _HBarList(QWidget):
    """水平条形列表：label | 条形 | 数量 · 占比（语义色轮换，降序输入）

    行可点击：rowClicked 发射该行地区名，供「点击联动」筛选记录列表。
    """

    # 行高与标签列宽固定，条形区宽度由控件宽度反推，保证多行对齐
    ROW_H = 26
    LABEL_W = 56
    rowClicked = Signal(str)   # 点击行 → 地区标签

    def __init__(self, parent=None):
        """行数据由 setRows 注入；控件本身不持有查询逻辑。"""
        super().__init__(parent)
        self._rows = []     # [(label, count)]
        self._total = 1
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击地区行：在记录列表筛选该地区（弹窗保持打开）")

    def setRows(self, rows, total):
        """替换行数据并按行数定最小高度，让外层滚动区正确计算内容高。

        total 是全部地区总量（不是当前行之和），占比因此能反映真实份额。
        """
        # 空地区并入「未填地区」：这类记录不参与联动，但数量必须计入总量
        self._rows = [(str(r.get("region") or "未填地区"),
                       int(r.get("count") or 0)) for r in (rows or [])]
        self._total = max(1, int(total or 0))
        self.setMinimumHeight(len(self._rows) * self.ROW_H + 8 if self._rows
                              else 60)
        self.update()

    def sizeHint(self):
        """宽度给条形与右侧数值留位，高度按行数累加。"""
        return QSize(300, len(self._rows) * self.ROW_H + 8)

    def mousePressEvent(self, e):
        """按 y // 行高定位行并发射地区名；空白区点击不联动。"""
        if e.button() == Qt.MouseButton.LeftButton and self._rows:
            idx = int(e.position().y()) // self.ROW_H
            if 0 <= idx < len(self._rows):
                self.rowClicked.emit(self._rows[idx][0])
        super().mousePressEvent(e)

    def paintEvent(self, _e):
        """逐行画「标签 + 条形 + 数量·占比」，条形长度相对当前最大行。

        颜色按行序轮换：地区之间没有语义色差异，轮换只为视觉可分；
        条形长度必须相对 max_v 而非 total，否则长尾地区会全部看不见。
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._rows:
            p.setPen(QColor(_text_color()))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "当前筛选下暂无地区数据")
            return
        max_v = max(c for _l, c in self._rows) or 1
        acc = current_accent_hex()
        colors = [acc, SEMANTIC["info"], SEMANTIC["warning"],
                  SEMANTIC["success"], SEMANTIC["neutral"]]
        f = p.font()
        f.setPointSizeF(9.5)
        p.setFont(f)
        text_c = QColor(_text_color())
        bar_x = self.LABEL_W + 8
        # 右侧固定留 128px 放「N 条 · 百分比」，否则数字会被条形压住
        bar_w = max(20.0, self.width() - bar_x - 128)
        for i, (label, count) in enumerate(self._rows):
            y = 4 + i * self.ROW_H
            p.setPen(text_c)
            p.drawText(QRect(0, y, self.LABEL_W - 6, 18),
                       Qt.AlignmentFlag.AlignRight
                       | Qt.AlignmentFlag.AlignVCenter, label)
            bw = max(3.0, bar_w * count / max_v)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(colors[i % len(colors)]))
            p.drawRoundedRect(QRectF(bar_x, y + 3, bw, 14), 4, 4)
            pct = int(round(count * 100 / self._total)) if self._total else 0
            p.setPen(text_c)
            p.drawText(QRectF(bar_x + bar_w + 8, y, 120, 18),
                       Qt.AlignmentFlag.AlignLeft
                       | Qt.AlignmentFlag.AlignVCenter,
                       f"{count} 条 · {pct}%")


# ==================== 类型表「构成」列堆叠条 delegate ====================

class _StackedBarDelegate(QStyledItemDelegate):
    """问题类型构成条：已解决(绿) + 未解决(红) 堆叠，宽度相对当前最大数量。

    宽度不按百分比铺满：若各类型条都按 100% 画，数量差异会被抹平，
    因此用「本行总数 / 本次最大总数」控制条长，再看内部解决/未解决比例。
    """

    def __init__(self, scale_max=1, parent=None):
        """scale_max 为本次数据的最大类型数量，用于换算条长基准。"""
        super().__init__(parent)
        self.scale_max = max(1, int(scale_max or 1))

    def paint(self, painter, option, index):
        """自绘构成条；选中背景要手动补，否则委托会盖掉整行高亮。

        两个数值放在 UserRole / UserRole+1，不占用显示文本列。
        """
        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        resolved = int(index.data(Qt.ItemDataRole.UserRole) or 0)
        unresolved = int(index.data(Qt.ItemDataRole.UserRole + 1) or 0)
        total = resolved + unresolved
        x = option.rect.x() + 8
        y = option.rect.center().y() - 4
        # 左右各留 8px 让条不贴边；avail 最小 8 保证空数据也能画灰块
        avail = max(8.0, option.rect.width() - 16)
        bw = max(8.0, avail * min(1.0, total / self.scale_max))
        if total:
            rw = bw * resolved / total
            painter.fillRect(QRectF(x, y, rw, 8),
                             QColor(SEMANTIC["success"]))
            painter.fillRect(QRectF(x + rw, y, bw - rw, 8),
                             QColor(SEMANTIC["danger"]))
        else:
            painter.fillRect(QRectF(x, y, 8, 8),
                             QColor(_grid_color()))
        painter.restore()


# ==================== 统计弹窗 ====================

class AfterSaleStatsDialog(QDialog):
    """「售后总数」详细统计弹窗：独立可缩放非模态窗口（Fluent 自绘）

    统计范围默认跟随记录页当前筛选（周期 / 类型 / 关键词），与四张概览
    卡完全同口径；「每日趋势」默认展示当前筛选周期的跨度（如 08/18-08/24
    展示该 7 天，周期模式为自然月则展示整月），可切换 近7天/近30天/全部。

    点击联动：点击地区行 → 发射 apply_filter({"keyword": 地区})；点击
    类型行 → 发射 apply_filter({"issue_type": 类型})。弹窗保持打开，
    由记录页在后台切换列表并按条件筛选（「未填地区/未分类」无真实值，
    不参与联动）。

    信号必须是 dict：记录页的筛选接口按字段增量合并，直接传字符串
    会迫使记录页猜字段名，后续加联动维度也要改签名。
    """

    apply_filter = Signal(dict)   # {"issue_type": str} / {"keyword": str}

    def __init__(self, filters: dict, parent=None):
        """复制一份初始筛选；后续 set_filters 可整体替换并立即重查。"""
        super().__init__(parent)
        self._filters = dict(filters or {})
        # 只保留最新一个 Worker 的引用：不持有时它可能被 GC，回调永远不来
        self._worker = None
        # 保留 delegate 引用：表格按列设置后并不持有它，被回收会退回默认绘制
        self._type_delegate = None
        self.setWindowTitle("售后统计")
        # 非模态是有意的：统计窗开着时仍要能回去改记录，否则联动筛选很别扭
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(1180, 680)
        # 最小尺寸保证六张 KPI 卡不换行，构成条也还有可读宽度
        self.setMinimumSize(960, 580)
        # QSS 失败不致命：缺主题资源时退回系统默认样式，功能仍可用
        try:
            apply_window_qss(self)
        except Exception:
            pass
        self._init_ui()
        self._update_scope()
        self._reload()

    # ---------- UI 构造 ----------

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 16)
        root.setSpacing(10)

        # 头部：统计范围说明 + 刷新
        head = QHBoxLayout()
        head.setSpacing(8)
        self._lbl_scope = CaptionLabel("", self)
        # 范围条做成胶囊样式：它与普通说明文字语义不同，要让人一眼看到统计口径
        self._lbl_scope.setStyleSheet(
            "background: transparent; padding: 2px 10px;"
            " border: 1px solid rgba(0,188,212,.35);"
            " border-radius: 6px;")
        head.addWidget(self._lbl_scope)
        head.addStretch(1)
        self._btn_refresh = PushButton(FluentIcon.SYNC, "刷新", self)
        # tooltip 强调「当前范围」：避免用户以为刷新会重置筛选条件
        self._btn_refresh.setToolTip("按当前统计范围重新查询")
        self._btn_refresh.clicked.connect(self._reload)
        head.addWidget(self._btn_refresh)
        root.addLayout(head)

        # KPI 行（六项）
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)
        # key → (卡片, 数字标签, 副文案) 三元组；刷新时按 key 定位，省去六组属性名
        self._kpis = {}
        # 六项 KPI 的顺序即视觉顺序，key 同时是 query_stats_detail 的 summary 字段名
        for key, label in (("total", "售后总数"), ("resolved", "已解决"),
                           ("unresolved", "未解决"), ("rate", "解决率"),
                           ("initiative", "主动发起"),
                           ("our_problem", "我方问题")):
            card, num, sub = self._make_kpi(key, label)
            self._kpis[key] = (card, num, sub)
            kpi_row.addWidget(card, 1)
        root.addLayout(kpi_row)

        # 每日售后趋势（QPainter 柱状图）
        trend_card = CardWidget(self)
        tlay = QVBoxLayout(trend_card)
        tlay.setContentsMargins(16, 12, 16, 10)
        tlay.setSpacing(6)
        thead = QHBoxLayout()
        thead.setSpacing(8)
        thead.addWidget(BodyLabel("每日售后趋势", trend_card))
        hint = CaptionLabel("按发生日期（缺失回退填写时间）· 峰值日高亮 · 悬停查看数量",
                            trend_card)
        hint.setStyleSheet("color: %s; background: transparent;" % _muted_color())
        thead.addWidget(hint)
        thead.addStretch(1)
        self._trend_seg = SegmentedWidget(trend_card)
        self._trend_seg.addItem("cycle", "当前周期")
        self._trend_seg.addItem("7d", "近 7 天")
        self._trend_seg.addItem("30d", "近 30 天")
        # 四个分段与 _trend_range 的四个分支一一对应，路由键不可随意改
        self._trend_seg.addItem("all", "全部")
        # 默认：记录页有周期筛选 → 当前周期；否则 → 全部
        self._trend_seg.setCurrentItem(
            "cycle" if (self._filters.get("cycle_start") or "") else "all")
        self._trend_seg.currentItemChanged.connect(lambda _k: self._reload())
        thead.addWidget(self._trend_seg)
        tlay.addLayout(thead)
        self._chart = _DailyBarChart(trend_card)
        tlay.addWidget(self._chart, 1)
        # 图下说明行兼作加载/错误状态位，不再单独放一个状态标签
        self._lbl_chart = CaptionLabel("统计加载中…", trend_card)
        # 居中通栏显示：加载/失败文案不应与左对齐的标题混成一片
        self._lbl_chart.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_chart.setStyleSheet(
            "color: %s; background: transparent;" % _muted_color())
        tlay.addWidget(self._lbl_chart)
        # 3:4 伸缩比：趋势图吃掉多余高度，下方两栏只需稳定可读的高度
        root.addWidget(trend_card, 3)

        # 下半区：地区分布 | 问题类型分布
        cols = QHBoxLayout()
        cols.setSpacing(12)

        region_card = CardWidget(self)
        rlay = QVBoxLayout(region_card)
        rlay.setContentsMargins(16, 12, 16, 10)
        rlay.setSpacing(6)
        rhead = QHBoxLayout()
        rhead.setSpacing(8)
        rhead.addWidget(BodyLabel("地区分布", region_card))
        rhead.addStretch(1)
        # 右上角计数随数据刷新，先建空标签占位，避免卡片高度跳变
        self._lbl_region = CaptionLabel("", region_card)
        self._lbl_region.setStyleSheet(
            "color: %s; background: transparent;" % _muted_color())
        rhead.addWidget(self._lbl_region)
        rlay.addLayout(rhead)
        # 地区数不可控：放进滚动区，卡片高度不会随地区数量膨胀
        self._region_scroll = QScrollArea(region_card)
        self._region_scroll.setWidgetResizable(True)
        self._region_scroll.setFrameShape(QFrame.Shape.NoFrame)
        # 禁横向滚动：条形宽度已按控件宽算好，出现横向滚动说明布局算错了
        self._region_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._region_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._region_list = _HBarList(region_card)
        self._region_scroll.setWidget(self._region_list)
        self._region_list.rowClicked.connect(self._on_region_clicked)
        rlay.addWidget(self._region_scroll, 1)
        cols.addWidget(region_card, 1)

        type_card = CardWidget(self)
        t2lay = QVBoxLayout(type_card)
        t2lay.setContentsMargins(16, 12, 16, 10)
        t2lay.setSpacing(6)
        t2head = QHBoxLayout()
        t2head.addWidget(BodyLabel("问题类型分布", type_card))
        t2head.addStretch(1)
        t2hint = CaptionLabel("与导出「统计图表」同口径", type_card)
        t2hint.setStyleSheet(
            "color: %s; background: transparent;" % _muted_color())
        t2head.addWidget(t2hint)
        t2lay.addLayout(t2head)
        self._type_table = TableWidget(type_card)
        # 5 列固定：前 4 列是文本/数字，第 5 列整列交给 delegate 画构成条
        self._type_table.setColumnCount(5)
        self._type_table.setHorizontalHeaderLabels(
            ["类型", "数量", "已解决", "未解决", "构成"])
        self._type_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._type_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        # 关自动换行：行高固定 30，换行会裁字并打乱构成条与行的对齐
        self._type_table.setWordWrap(False)
        self._type_table.verticalHeader().setVisible(False)
        # 行高固定，构成条按行中心定位才不会随文本长度上下抖动
        self._type_table.verticalHeader().setDefaultSectionSize(30)
        # 0/4 列拉伸，1-3 列固定 64：数字列等宽才好纵向比较
        header = self._type_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            self._type_table.setColumnWidth(c, 64)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._type_table.itemClicked.connect(self._on_type_cell_clicked)
        t2lay.addWidget(self._type_table, 1)
        cols.addWidget(type_card, 1)

        root.addLayout(cols, 4)

    def _make_kpi(self, key, label):
        """KPI 卡片：标签 + 大数字 + 副说明（返回 (card, num_label, sub_label)）。

        返回三元组而不是把控件挂到 self 上，刷新时按 key 直接定位，
        省去六组重复的属性名。
        """
        card = CardWidget(self)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        cap = CaptionLabel(label, card)
        num = QLabel("—", card)
        num.setAlignment(Qt.AlignmentFlag.AlignLeft)
        num.setStyleSheet(
            "font-size: 26px; font-weight: 600; background: transparent;")
        sub = CaptionLabel("", card)
        sub.setStyleSheet("color: %s; background: transparent;" % _muted_color())
        lay.addWidget(cap)
        lay.addWidget(num)
        lay.addWidget(sub)
        return card, num, sub

    # ---------- 范围与查询 ----------

    def _update_scope(self):
        """统计范围说明（仅列出真正参与统计口径的筛选：周期/类型/关键词）。

        说明文案必须与实际查询参数一一对应，否则用户会以为状态筛选也生效。
        """
        f = self._filters
        cycle = str(f.get("cycle_start") or "")
        # cycle_label 负责把 "2026-08" / "2026-08-18" 转成人话，避免这里判格式
        cyc_txt = aftersale_db.cycle_label(cycle) if cycle else "全部周期"
        typ_txt = f.get("issue_type") or "全部类型"
        kw = str(f.get("keyword") or "").strip()
        kw_txt = f"关键词「{kw}」" if kw else "无关键词"
        self._lbl_scope.setText(
            f"统计范围：{cyc_txt} · {typ_txt} · {kw_txt}")

    def _trend_range(self) -> tuple:
        """按趋势分段选择返回 (trend_start, trend_end)（YYYY-MM-DD，空=不过滤）

        当前周期：周期起止（month 模式整月，其余起始日 + span-1 天）；
        近 7/30 天：以今天为终点回推；全部：不过滤。
        """
        key = self._trend_seg.currentRouteKey()
        today = datetime.now().date()
        if key == "cycle":
            # 周期解析失败时返回 (None, None)，此时退回"不过滤"而不是报错
            s, e = aftersale_db.cycle_date_range(
                str(self._filters.get("cycle_start") or ""))
            if s:
                return s.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d")
            return "", ""
        # 含今天共 7 天，故回推 6 天；写成 7 会多出一天
        if key == "7d":
            return (today - timedelta(days=6)).strftime("%Y-%m-%d"), \
                today.strftime("%Y-%m-%d")
        if key == "30d":
            return (today - timedelta(days=29)).strftime("%Y-%m-%d"), \
                today.strftime("%Y-%m-%d")
        return "", ""

    def _reload(self):
        """按当前统计范围异步查询（与四卡片同口径，走 query_stats_detail）。

        重入时先打断上一个 Worker：连点刷新或切分段会叠多个查询，
        旧结果后到就会把新图表覆盖成过期数据。
        """
        # 旧 Worker 仍在跑：先断信号再打断，避免它把新一次的结果覆盖成过期数据
        if self._worker and self._worker.isRunning():
            # 先断开信号再打断：旧 Worker 的 error 回调不应把新一次加载标成失败
            self._worker.requestInterruption()
            self._worker.disconnect(self)
        f = self._filters
        ts, te = self._trend_range()
        self._lbl_chart.setText("统计加载中…")
        # 先清图再查：重查期间旧柱状图会让人误以为那就是新范围的数据
        self._chart.setData([])
        # 查询期间禁用刷新，避免连点堆出多个并发查询
        self._btn_refresh.setEnabled(False)
        # 趋势范围与汇总在同一次查询里取回，保证图表与 KPI 同一时点
        self._worker = AftersaleDBWorker(
            aftersale_db.query_stats_detail,
            keyword=f.get("keyword", ""), cycle_start=f.get("cycle_start", ""),
            issue_type=f.get("issue_type", ""),
            trend_start=ts, trend_end=te)
        self._worker.result_ready.connect(self._on_loaded)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def set_filters(self, filters: dict):
        """外部更新统计范围（记录页筛选变化后复用弹窗时调用），立即刷新。

        复用同一窗口而不新建：避免多次点击「售后总数」后堆出一排窗口。
        """
        self._filters = dict(filters or {})
        self._update_scope()
        # 周期筛选变化时趋势默认跟随「当前周期」
        if self._filters.get("cycle_start"):
            self._trend_seg.setCurrentItem("cycle")
        self._reload()

    # ---------- 点击联动 ----------

    def _on_region_clicked(self, label: str):
        """点击地区行：按地区关键词联动筛选（未填地区无真实值，不参与）。

        走 keyword 而不是新增过滤参数：地区在库里就是关键词命中的文本，
        单独加参数会与搜索框语义打架。弹窗保持打开，仅通知记录页筛选。
        """
        if label == "未填地区":
            return
        self.apply_filter.emit({"keyword": label})

    def _on_type_cell_clicked(self, item):
        """点击类型行：按类型联动筛选（未分类无真实值，不参与）。

        只有第 0 列（类型名）可点：数量/构成列点击若也联动，会误伤
        想选中数字查看的用户。弹窗保持打开，仅通知记录页筛选。
        """
        if item.column() != 0:
            return
        t = self._type_table.item(item.row(), 0).text()
        if t == "未分类":
            return
        self.apply_filter.emit({"issue_type": t})

    # ---------- 渲染 ----------

    def _on_loaded(self, result):
        """查询成功：先恢复按钮，再分别渲染 KPI、趋势、地区与类型四块。"""
        self._btn_refresh.setEnabled(True)
        s = result.get("summary") or {}
        self._update_summary(s)
        daily = result.get("daily") or []
        self._chart.setData(daily)
        total = int(s.get("total") or 0)
        if daily:
            n = len(daily)
            # 日均按有记录的天数算，而非趋势区间总天数，免得空白期拉低均值
            total_d = sum(d["count"] for d in daily)
            self._lbl_chart.setText(
                f"{n} 天有记录 · 合计 {total_d} 条 · 日均 {round(total_d / n, 1)}")
        elif total == 0:
            self._lbl_chart.setText("当前筛选下暂无售后记录")
        else:
            self._lbl_chart.setText("所选趋势范围内暂无记录")
        # 地区占比用 total 而不是地区行之和：这样「未填地区」也计入分母
        self._update_regions(result.get("regions") or [], total)
        self._update_types(result.get("types") or [])

    def _on_error(self, msg):
        """查询失败只改提示，保留上一轮数据，避免图表突然变空让人以为没数据。"""
        self._btn_refresh.setEnabled(True)
        self._lbl_chart.setText(f"统计加载失败：{msg}")

    def _update_summary(self, s: dict):
        """把汇总字典刷进六张 KPI 卡，并给每张卡补上有参照意义的副文案。

        颜色跟语义走而不是跟数值大小走：未解决为 0 时不着红，
        否则「没有积压」反而看起来像告警。
        """
        base = "font-size: 26px; font-weight: 600; background: transparent;"
        total = int(s.get("total") or 0)
        resolved = int(s.get("resolved") or 0)
        unresolved = int(s.get("unresolved") or 0)
        # rate 由后端算好，前端不再重算，避免两边四舍五入口径不一致
        rate = int(s.get("rate") or 0)
        initiative = int(s.get("initiative") or 0)
        our = int(s.get("our_problem") or 0)

        # 统一入口：先套基础样式再按需着色，避免上一次的颜色残留到下一次
        def _set(key, text, color=""):
            _c, num, _sub = self._kpis[key]
            num.setText(text)
            num.setStyleSheet(base + (f" color: {color};" if color else ""))

        # 总数不着色：它是基准量，着色反而会和其他指标抢注意力
        _set("total", str(total))
        _set("resolved", str(resolved), SEMANTIC["success"])
        _set("unresolved", str(unresolved),
             SEMANTIC["danger"] if unresolved else "")
        _set("rate", f"{rate}%", SEMANTIC["info"] if rate else "")
        _set("initiative", str(initiative), SEMANTIC["info"])
        _set("our_problem", str(our), SEMANTIC["warning"])
        # 副文案给出分母或占比，主数字才有参照系；total 为 0 时不能做除法
        self._kpis["total"][2].setText("按当前筛选口径")
        self._kpis["resolved"][2].setText(f"占 {rate}%")
        self._kpis["unresolved"][2].setText(
            "待处理积压" if unresolved else "全部已处理")
        self._kpis["rate"][2].setText("已解决 ÷ 总数")
        self._kpis["initiative"][2].setText(
            f"占 {int(round(initiative * 100 / total))}%" if total else "—")
        self._kpis["our_problem"][2].setText(
            f"占 {int(round(our * 100 / total))}%" if total else "—")

    def _update_regions(self, regions, total):
        """刷新地区条形与右上角计数；无数据时明确写「无」而不是留空。"""
        self._region_list.setRows(regions, total)
        self._lbl_region.setText(
            f"{len(regions)} 个地区" if regions else "无")

    def _update_types(self, types):
        """重建类型表：行数据、构成条基准与只读标志都在这里一次性设置。

        blockSignals 必须包住整个重建过程：itemClicked 已连到联动槽，
        逐格 setItem 会触发表格内部信号，可能在渲染中途误触发筛选。
        """
        self._type_table.blockSignals(True)
        try:
            self._type_table.setRowCount(len(types))
            # 基准取本次最大数量而不是全表，保证小样本下条长依旧可读
            max_count = max((int(t.get("count") or 0) for t in types),
                            default=0)
            # 每次重建 delegate：基准随本次最大数量变化，复用旧对象会画错条长
            self._type_delegate = _StackedBarDelegate(
                max_count, self._type_table)
            self._type_table.setItemDelegateForColumn(4, self._type_delegate)
            for r, t in enumerate(types):
                c = int(t.get("count") or 0)
                rd = int(t.get("resolved") or 0)
                un = int(t.get("unresolved") or 0)
                it0 = QTableWidgetItem(t.get("issue_type") or "未分类")
                if (t.get("issue_type") or "") == "":
                    it0.setToolTip("该分组为未填类型，无法联动筛选")
                else:
                    it0.setToolTip("点击跳转记录列表并筛选该类型")
                it1 = QTableWidgetItem(str(c))
                it1.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                # 数量列加粗：哪类问题最多要一眼可见；已解决/未解决保持常规
                f = QFont(it1.font())
                f.setBold(True)
                it1.setFont(f)
                it2 = QTableWidgetItem(str(rd))
                it2.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                it2.setForeground(QColor(SEMANTIC["success"]))
                it3 = QTableWidgetItem(str(un))
                it3.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                it3.setForeground(QColor(SEMANTIC["danger"]) if un
                                  else QColor(SEMANTIC["neutral"]))
                # 构成条数据放 UserRole，不占用可见文本列，也免去解析字符串
                it4 = QTableWidgetItem()
                it4.setData(Qt.ItemDataRole.UserRole, rd)
                it4.setData(Qt.ItemDataRole.UserRole + 1, un)
                # 统一清掉可编辑标志：整表只读，点行是联动而不是进入编辑
                for col, it in enumerate((it0, it1, it2, it3, it4)):
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self._type_table.setItem(r, col, it)
        finally:
            # 必须恢复信号，否则之后再点类型行不会触发联动
            self._type_table.blockSignals(False)
