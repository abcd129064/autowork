# -*- coding: utf-8 -*-
"""售后排行页（球房/球桌两级排行，与 Web 端 /aftersale/rank 同功能）

RankPage：售后面板 FluentWindow 第 4 个导航子页。信息架构对齐 Web 端：
① 筛选栏（时间范围/账期/地区/三个是否/关键词）② 概览 4 卡（覆盖球房/
覆盖球桌/记录总数/未解决）③ 球房/球桌分段 + TOP 深度 + 排序 + 面包屑
下钻 ④ 左 QPainter 自绘横向条形图 + 右表格（排名徽章 + 指标列 + 操作列
文字链接「明细/下钻」）。

数据经 ``aftersale_db.query_rank`` 一次取回（异步 Worker + _query_seq
代数守卫，与 RecordsPage 同范式）；图表 QPainter 自绘零新依赖（与
AfterSaleStatsDialog 同范式），深浅主题自适应。下钻为服务端口径
（query_rank room_name 参数），面包屑「全部球房」一键返回全局榜。
明细跳转发 jump_to_records(keyword) 信号，由宿主窗口切换记录页并
set_keyword 预筛选（球房级=球房名，球桌级=桌号）。
"""

from datetime import date, timedelta

from PySide6.QtCore import Qt, QRect, QRectF, QSize, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QScrollArea, QSizePolicy,
    QTableWidgetItem, QToolTip, QVBoxLayout, QWidget)

from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, ComboBox,
    FluentIcon, FlowLayout, PrimaryPushButton, PushButton, SearchLineEdit,
    SegmentedWidget, TableWidget, ZhDatePicker,
    isDarkTheme)

from core.design_tokens import SEMANTIC
from core.flow_widgets import FlowToolbarScrollArea
from core.ops_link_delegate import LINKS_ROLE, install_ops_links
from core.perf import apply_table_smooth_mode
from core.theme_qss import current_accent_hex
from core.utils import show_info_bar
from database import aftersale_db
from workers.aftersale_worker import AftersaleDBWorker

from windows.aftersale.common import (  # noqa: F401
    _FIXED_ROW_HEIGHT, YesNoSegment,
)

# 主题取色辅助（与 stats_dialog 同口径：浅/深主题各一套）
def _text_color() -> str:
    """图表刻度与空态文案的文字色

    QPainter 自绘的文字不走 QSS，没有主题自动反色，只能在这里按明暗主题
    各给一套中灰值；换成纯黑或纯白会在卡片底色上过冲，看起来比数据还重。
    """
    return "#6B7280" if not isDarkTheme() else "#8892a2"


def _muted_color() -> str:
    """次要信息色（如「最近发生」时间列）

    比 _text_color 再淡一档，用来在视觉上把时间戳这类辅助字段压到
    指标列之后，避免用户第一眼读到的是时间而不是数量。
    """
    return "#9CA3AF" if not isDarkTheme() else "#555f6b"


def _primary_text_color() -> str:
    """主文本色：与右侧表格的默认字色保持一致

    图表和表格并排放，两处正文颜色不一致时，左图会显得比右表"轻"，
    用户会下意识觉得左侧数据不重要。
    """
    return "#20242B" if not isDarkTheme() else "#C8D0DC"


# 排名前三徽章色（金/银/铜）。
# 取中明度而不是标准奖牌色：饱和的金银铜在深色主题下会发光刺眼，
# 在浅色主题下又容易糊在白底上，折中后才两个主题都能读。
_MEDAL_COLORS = ("#d4af37", "#9aa3ad", "#c08457")

# 表格列定义：(字段, 表头, 列宽)。0 表示该列吃掉剩余宽度。
# 名称列给 0 是唯一合理选择：球房名长度差异最大，固定宽度不是截断就是留白；
# 其余列都是数字或短文案，固定宽度才能让行与行之间的数字对齐好比较。
_RANK_COLUMNS = (
    ("rank", "排名", 46),
    ("name", "名称", 0),
    ("total", "总量", 60),
    ("share", "占比", 56),
    ("unresolved", "未解决", 64),
    ("our_problem", "我方问题", 76),
    ("last_occurred", "最近发生", 92),
    ("ops", "操作", 108),
)
# 名称与操作列的固定下标：这两列有特殊处理（名称放 tooltip、操作列挂链接），
# 用常量而不是散落的字面量 1 / 7，改列顺序时只需要看这一行。
_COL_NAME = 1
_COL_OPS = len(_RANK_COLUMNS) - 1


def _fetch_rank_options():
    """取周期与地区两个下拉的选项，跑在 AftersaleDBWorker 里

    地区取不到时吞掉异常返回空列表：这两个选项只是筛选辅助，
    因为地区字典表缺失就让整页查不出数据，代价明显不成比例。
    周期选项不吞异常——账期筛选依赖它，静默失败会让用户以为没有账期。
    """
    try:
        regions = list(aftersale_db.get_field_candidates().get("regions") or [])
    except Exception:
        regions = []
    return aftersale_db.get_cycle_options(), regions


# ==================== 排行横向条形图（QPainter 自绘） ====================

class _RankHBarChart(QWidget):
    """排行横向条形图：名称 | 条形 | 数量 · 占比（TOP N 行，降序输入）

    行可点击：rowClicked 发射该行序号（与页面 _rows 同序），由页面分发
    （球房级行点击=下钻该球房，球桌级行点击=跳转明细）。悬停行淡色高亮。
    """

    # 行高 32 是「条形 16px + 上下各 8px 呼吸」的结果，再矮条形就会糊成一片。
    # 名称列 150px 恰好放得下「XX球房(XX店)」，数值列 96px 放得下「1234 · 56%」。
    ROW_H = 32
    LABEL_W = 150
    VAL_W = 96
    # 发的是行下标而不是行数据：页面持有同一份 _rows，
    # 传下标能让图表与表格共用一套序号，也避免跨线程复制 dict。
    rowClicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        # 条形色由页面按语义传入：球房榜用主题强调色，球桌级/下钻用警示色，
        # 让「层级变了」这件事在图上也能看出来，而不是只看标题文字。
        self._bar_color = None
        self._hover = -1
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

    def set_rows(self, rows, bar_color=None):
        """换一批排行数据并立即重绘

        拷贝一份 rows：调用方的列表随后还会被过滤、清空或复用，
        直接持有引用会让图上的行数和表格对不上。
        悬停高亮必须复位，否则下标还指着旧数据的某一行，会高亮错人。
        """
        self._rows = list(rows or [])
        self._bar_color = bar_color
        self._hover = -1
        # 最小高度跟着行数走：只更新 sizeHint 不够，
        # 外层滚动区已经按旧的最小高度定过视口，不抬下限照样被裁。
        self.setMinimumHeight(
            len(self._rows) * self.ROW_H + 10 if self._rows else 64)
        self.updateGeometry()   # 行数变化 → 通知父布局重算 sizeHint
        self.update()

    def sizeHint(self):
        """告诉布局本图需要多高，行数一变就跟着变

        不返回随行数增长的高度，外层 QScrollArea 会把图裁到默认尺寸，
        TOP 50 只能看到前七八行；下限 64 是给空态文案留的位置。
        """
        return QSize(320, max(64, len(self._rows) * self.ROW_H + 10))

    def mouseMoveEvent(self, e):
        """按 y 坐标换算行号，做悬停高亮与被截断名称的 tooltip

        行号用整除得到而不是逐行比较矩形：行高固定，整除是 O(1)，
        而且不会因为边界像素抖动在两个行之间反复切换。
        """
        idx = -1
        if self._rows:
            idx = int(e.position().y()) // self.ROW_H
            if not (0 <= idx < len(self._rows)):
                idx = -1
        if idx != self._hover:
            self._hover = idx
            self.update()
        # 名称被截断时悬停显示完整名 tooltip（_DailyBarChart 同范式）
        if idx >= 0:
            r = self._rows[idx]
            name = str(r.get("name") or "")
            fm = QFontMetrics(self.font())
            if fm.horizontalAdvance(name) > self.LABEL_W - 6:
                QToolTip.showText(
                    self.mapToGlobal(e.position().toPoint()),
                    f"{name} · {int(r.get('total') or 0)} 条 · "
                    f"{int(r.get('share') or 0)}%", self)
            else:
                QToolTip.hideText()
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        """鼠标离开画布：清悬停高亮并收起 tooltip

        tooltip 必须显式 hide，Qt 不会因为鼠标移出控件自动收回，
        否则它会一直挂在图上，直到用户点了别处。
        """
        if self._hover != -1:
            self._hover = -1
            self.update()
        QToolTip.hideText()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        """左键点击某一行时发出该行下标

        只认左键，右键留给上下文菜单；越界下标直接丢弃，
        免得页面拿到一个不存在的行去做下钻。
        """
        if e.button() == Qt.MouseButton.LeftButton and self._rows:
            idx = int(e.position().y()) // self.ROW_H
            if 0 <= idx < len(self._rows):
                self.rowClicked.emit(idx)
        super().mousePressEvent(e)

    def paintEvent(self, _e):
        """自绘整张条形图：悬停底 → 名称 → 条形 → 数值

        不用 QChart 是因为这里只需要横条，引一个图表模块属于杀鸡用牛刀，
        而且 QChart 的坐标轴样式要覆盖半天才能贴合 Fluent 观感。
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._rows:
            p.setPen(QColor(_text_color()))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "当前筛选下暂无排行数据")
            return
        fm = QFontMetrics(p.font())
        # 最大值兜底成 1：全是 0 条的数据会让条形宽度除零，
        # 而 0 条本就不该出现在排行里，兜底只是防御异常输入。
        max_v = max(int(r.get("total") or 0) for r in self._rows) or 1
        base = QColor(self._bar_color or current_accent_hex())
        hover_c = QColor(base)
        hover_c.setAlpha(28)
        pad_l = 8   # 左内边距：名称不贴卡片边
        bar_x = pad_l + self.LABEL_W + 8
        bar_w = max(20.0, self.width() - bar_x - self.VAL_W - 8)
        # 逐行绘制：悬停行先铺一层低透明度底色，再画名称、条形与数值。
        # 条形宽度按 max_v 归一化而不是按总记录数，这样最大的一条总是占满，
        # 行与行之间的相对差异才看得出来。
        for i, r in enumerate(self._rows):
            # 顶部留 5px：第一行的悬停底色不贴画布边缘，否则圆角会被裁掉一半。
            y = 5 + i * self.ROW_H
            if i == self._hover:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(hover_c)
                p.drawRoundedRect(
                    QRect(2, y, self.width() - 4, self.ROW_H), 4, 4)
            n = int(r.get("total") or 0)
            # 名称右对齐贴着条形起点：左对齐会让长短不一的名字离条形远近不等，
            # 视线得来回找条形从哪开始，扫读速度明显变慢。
            name = fm.elidedText(
                str(r.get("name") or ""), Qt.TextElideMode.ElideRight,
                self.LABEL_W - 6)
            p.setPen(QColor(_primary_text_color()))
            p.drawText(QRect(pad_l, y, self.LABEL_W, self.ROW_H),
                       Qt.AlignmentFlag.AlignRight
                       | Qt.AlignmentFlag.AlignVCenter, name)
            # 宽度下限 3px：数量远小于最大值时按比例算出来不到 1px，
            # 那种行会看起来「没有条形」，用户会怀疑数据丢了。
            bw = max(3.0, bar_w * n / max_v)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(base)
            p.drawRoundedRect(
                QRectF(bar_x, y + (self.ROW_H - 16) / 2, bw, 16), 4, 4)
            p.setPen(QColor(_primary_text_color()))
            p.drawText(
                QRectF(bar_x + bar_w + 8, y, self.VAL_W, self.ROW_H),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"{n} · {int(r.get('share') or 0)}%")


# ==================== 售后排行页 ====================

class RankPage(QWidget):
    """售后排行页：筛选栏 + 概览 4 卡 + 球房/球桌排行（左图右表 + 下钻）

    明细跳转经 jump_to_records(keyword) 由宿主窗口接线（切换记录页并
    关键词预筛选）；下钻为服务端口径（query_rank room_name），面包屑
    返回全局榜。
    """

    # 明细跳转只发关键词：记录页的搜索已经覆盖桌号与球房名，
    # 发结构化条件会逼记录页支持一套它本来不需要的参数。
    jump_to_records = Signal(str)

    # 平滑滚动开关按面板分组存储，售后页与记录页共用同一档设置。
    _PERF_PANEL_KEY = "aftersale"
    # TOP 档位：200 是「全量」的实用上限，再多既扫得慢也没人看得完。
    _LIMITS = (10, 20, 50, 200)
    # 排序键与中文文案分开维护，后端只认字段名，改文案不影响查询。
    _SORTS = (("total", "按总量"), ("unresolved", "按未解决"),
              ("our_problem", "按我方问题"), ("last_occurred", "按最近发生"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._options_worker = None
        # 查询代数：每次发起查询自增，回调只接受最新一代的结果。
        # 没有它时，先发的慢查询可能后到，把用户刚改的条件覆盖回旧结果。
        self._query_seq = 0
        self._rows = []
        self._summary = {}
        # 层级与下钻是两个独立维度：球桌级本身也可以针对某个球房下钻，
        # 用一个字段同时表达会丢状态。
        self._level = "room"
        self._drill = None
        self._options_loaded = False
        self._init_ui()
        # 关键词搜索防抖 300ms，与记录页、健康度页一致：
        # 每敲一个字都查一次排行，等于把最重的聚合查询连发十几次。
        self._search_timer = QTimer(self)
        self._search_timer.setInterval(300)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._on_filter_changed)

    # ---------- UI 构造 ----------

    def _init_ui(self):
        # --- 整页滚动容器：排行行数多（TOP 50/全部）时图表与卡片
        #     高度自适应增长，靠页面滚动查看，而非把行挤进固定高度 ---
        self._page_scroll = QScrollArea(self)
        self._page_scroll.setWidgetResizable(True)
        self._page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._page_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._page_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        page_lay = QVBoxLayout(self)
        page_lay.setContentsMargins(0, 0, 0, 0)
        page_lay.addWidget(self._page_scroll)
        content = QWidget(self._page_scroll)
        self._page_scroll.setWidget(content)
        # setWidget() 会重建视口，透明设置必须在其后补（否则深色主题黑块）
        self._page_scroll.viewport().setStyleSheet(
            "background: transparent;")

        root = QVBoxLayout(content)
        root.setContentsMargins(20, 14, 20, 12)
        root.setSpacing(8)

        # --- 筛选工具栏 ---
        # 用 FlowLayout 而不是 QHBoxLayout：筛选条件在窄窗口下必须换行，
        # 横排只会在小屏上把后面的按钮挤到看不见；外层滚动容器锁住最大高度，
        # 换行到第三行时改为纵向滚动，不会把下面的排行卡顶下去。
        toolbar_scroll = FlowToolbarScrollArea(self)
        toolbar_scroll.setWidgetResizable(True)
        toolbar_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        toolbar_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        toolbar_scroll.setMaximumHeight(132)
        toolbar_widget = QWidget(self)
        toolbar_scroll.setWidget(toolbar_widget)
        toolbar = FlowLayout(toolbar_widget)
        toolbar.setHorizontalSpacing(6)
        toolbar.setVerticalSpacing(6)
        toolbar.setContentsMargins(2, 4, 2, 4)

        # 时间范围的五个入口放在一个分段控件里：它们是互斥选项，
        # 拆成下拉加按钮会让人分不清哪个条件正在生效。
        toolbar.addWidget(CaptionLabel("时间范围：", self))
        self._range_seg = SegmentedWidget(self)
        for key, text in (("d30", "近 30 天"), ("d90", "近 90 天"),
                          ("all", "全部"), ("cycle", "账期"),
                          ("custom", "自定义")):
            self._range_seg.addItem(key, text)
        self._range_seg.setCurrentItem("d90")
        self._range_seg.currentItemChanged.connect(self._on_range_changed)
        toolbar.addWidget(self._range_seg)

        # 账期下拉（仅「账期」可见）与自定义起止（仅「自定义」可见）
        self._cycle_combo = ComboBox(self)
        self._cycle_combo.setFixedWidth(170)
        self._cycle_combo.currentIndexChanged.connect(
            lambda _i: self._on_filter_changed())
        toolbar.addWidget(self._cycle_combo)
        self._start_picker = ZhDatePicker(self)
        self._start_picker.setFixedWidth(140)
        self._start_picker.setDate(self._qdate_today())
        self._end_picker = ZhDatePicker(self)
        self._end_picker.setFixedWidth(140)
        self._end_picker.setDate(self._qdate_today())
        # ZhDatePicker 的日期信号在不同版本上签名不一致，连接失败也允许：
        # 少了联动顶多要手动点查询，而整体抛异常会让整页构建失败。
        try:
            self._start_picker.dateChanged.connect(
                lambda _q: self._on_filter_changed())
            self._end_picker.dateChanged.connect(
                lambda _q: self._on_filter_changed())
        except Exception:
            pass
        toolbar.addWidget(self._start_picker)
        self._lbl_to = CaptionLabel("至", self)
        toolbar.addWidget(self._lbl_to)
        toolbar.addWidget(self._end_picker)
        # 周期下拉与自定义起止默认全藏，等 _on_range_changed 按选中项放出来。
        # 藏而不是禁用：五个时间入口只可能有一个生效，摆出四个灰控件
        # 只会让人反复去点它们。
        self._cycle_combo.hide()
        self._start_picker.hide()
        self._end_picker.hide()
        self._lbl_to.hide()

        toolbar.addWidget(CaptionLabel("地区：", self))
        self._region_combo = ComboBox(self)
        self._region_combo.setFixedWidth(120)
        self._region_combo.addItem("全部地区", userData="")
        self._region_combo.currentIndexChanged.connect(
            lambda _i: self._on_filter_changed())
        toolbar.addWidget(self._region_combo)

        # 三个是否筛选都开 include_all：默认必须是「不限」，
        # 如果默认落在「是」上，进页面看到的就是被静默过滤过的排行。
        self._resolved_seg = YesNoSegment("", self, include_all=True)
        self._resolved_seg.currentItemChanged.connect(
            lambda _k: self._on_filter_changed())
        toolbar.addWidget(CaptionLabel("是否解决：", self))
        toolbar.addWidget(self._resolved_seg)
        self._our_problem_seg = YesNoSegment("", self, include_all=True)
        self._our_problem_seg.setToolTip("是否是我们的问题")
        self._our_problem_seg.currentItemChanged.connect(
            lambda _k: self._on_filter_changed())
        toolbar.addWidget(CaptionLabel("我方问题：", self))
        toolbar.addWidget(self._our_problem_seg)
        self._initiative_seg = YesNoSegment("", self, include_all=True)
        self._initiative_seg.setToolTip("是否我们主动发起")
        self._initiative_seg.currentItemChanged.connect(
            lambda _k: self._on_filter_changed())
        toolbar.addWidget(CaptionLabel("主动发起：", self))
        toolbar.addWidget(self._initiative_seg)

        self._search_edit = SearchLineEdit(self)
        self._search_edit.setPlaceholderText("球房 / 桌号")
        self._search_edit.setFixedWidth(170)
        # 文本框每敲一个字都重启计时器，只有停手 300ms 才真正查询：
        # 直接连 _on_filter_changed 会把最重的聚合查询按键盘节奏连发。
        self._search_edit.textChanged.connect(
            lambda _t: self._search_timer.start())
        toolbar.addWidget(self._search_edit)

        self._btn_query = PrimaryPushButton(FluentIcon.SEARCH, "查询", self)
        self._btn_query.setToolTip("按当前筛选重新查询排行")
        self._btn_query.clicked.connect(self._load)
        toolbar.addWidget(self._btn_query)
        self._btn_reset = PushButton(FluentIcon.SYNC, "重置", self)
        self._btn_reset.setToolTip("恢复默认筛选（近 90 天 · 全部分类）")
        self._btn_reset.clicked.connect(self._on_reset)
        toolbar.addWidget(self._btn_reset)
        self._btn_export = PushButton(FluentIcon.DOWNLOAD, "导出 xlsx", self)
        self._btn_export.setToolTip("导出当前排行（含指标列）为 xlsx")
        self._btn_export.clicked.connect(self._on_export)
        toolbar.addWidget(self._btn_export)
        root.addWidget(toolbar_scroll)

        # --- 概览 4 卡 ---
        # 四张卡等权平分一行（addWidget 的 stretch 都是 1）：数字长度接近，
        # 按内容给宽度会出现某张卡明显更宽、看起来像更重要。
        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)
        self._card_rooms = self._make_card(cards_row)
        self._card_tables = self._make_card(cards_row)
        self._card_total = self._make_card(cards_row)
        self._card_unresolved = self._make_card(cards_row)
        root.addLayout(cards_row)

        # --- 排行卡（分段 + TOP/排序 + 面包屑 + 左图右表） ---
        card = CardWidget(self)
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(14, 10, 14, 12)
        card_lay.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        self._lbl_rank_title = BodyLabel("球房售后排行", card)
        head.addWidget(self._lbl_rank_title)
        self._level_seg = SegmentedWidget(card)
        self._level_seg.addItem("room", "球房")
        self._level_seg.addItem("table", "球桌")
        self._level_seg.setCurrentItem("room")
        self._level_seg.currentItemChanged.connect(self._on_level_changed)
        head.addWidget(self._level_seg)
        # 面包屑（下钻时出现）：返回按钮 + 当前球房名。
        # 默认隐藏而不是禁用：榜单未下钻时这两件控件没有任何意义，
        # 禁用态只会让人去猜「怎么才能用」。
        self._btn_back = PushButton("‹ 全部球房", card)
        self._btn_back.setToolTip("返回全局球房排行")
        self._btn_back.clicked.connect(self._on_drill_back)
        self._btn_back.hide()
        head.addWidget(self._btn_back)
        self._lbl_drill = CaptionLabel("", card)
        self._lbl_drill.setStyleSheet(
            f"color: {current_accent_hex()}; background: transparent;")
        self._lbl_drill.hide()
        head.addWidget(self._lbl_drill)
        head.addStretch(1)
        head.addWidget(CaptionLabel("TOP", card))
        self._limit_combo = ComboBox(card)
        for n in self._LIMITS:
            self._limit_combo.addItem(str(n), userData=n)
        self._limit_combo.setFixedWidth(78)
        self._limit_combo.currentIndexChanged.connect(
            lambda _i: self._on_filter_changed())
        head.addWidget(self._limit_combo)
        head.addWidget(CaptionLabel("排序", card))
        self._sort_combo = ComboBox(card)
        for key, text in self._SORTS:
            self._sort_combo.addItem(text, userData=key)
        self._sort_combo.setFixedWidth(116)
        self._sort_combo.currentIndexChanged.connect(
            lambda _i: self._on_filter_changed())
        head.addWidget(self._sort_combo)
        card_lay.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(12)
        self._chart_scroll = QScrollArea(card)
        self._chart_scroll.setWidgetResizable(True)
        self._chart_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._chart_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._chart_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._chart = _RankHBarChart(self._chart_scroll)
        self._chart_scroll.setWidget(self._chart)
        # 视口透明（FlowToolbarScrollArea 同款）：深色主题下 QScrollArea
        # 视口默认按 Base 色绘制，会在卡片内形成突兀黑块；必须在 setWidget
        # 之后设置（setWidget 会重建视口）
        self._chart_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }")
        self._chart_scroll.viewport().setStyleSheet(
            "background: transparent;")
        self._chart.rowClicked.connect(self._on_chart_row)
        # AlignTop（2026-10-01）：布局对受 maximum/固定高度限制的子项默认
        # 垂直居中（QLayoutPrivate::alignedRect 兜底 AlignCenter）——表格
        # sizeHint 虚高把单元格撑高后，左图表被下推出现「与标题之间空白」。
        # 显式顶对齐，高度差由 _sync_table_height/_sync_chart_height 消除。
        body.addWidget(self._chart_scroll, 2, Qt.AlignmentFlag.AlignTop)

        self._table = TableWidget(card)
        install_ops_links(self._table, self._on_ops_link)
        apply_table_smooth_mode(self._table, panel=self._PERF_PANEL_KEY)
        self._table.setColumnCount(len(_RANK_COLUMNS))
        self._table.setHorizontalHeaderLabels(
            [c[1] for c in _RANK_COLUMNS])
        # 禁止选中与编辑：这页只做浏览和跳转，
        # 允许选中会让人以为单元格可以直接改数。
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setWordWrap(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_FIXED_ROW_HEIGHT)
        header = self._table.horizontalHeader()
        # 关掉「最后一列自动拉伸」：它会把操作列的宽度吃掉，
        # 文字链接被挤成两行；宽度分配统一走下面的循环。
        header.setStretchLastSection(False)
        for i, (_k, _h, w) in enumerate(_RANK_COLUMNS):
            if w == 0:
                header.setSectionResizeMode(
                    i, QHeaderView.ResizeMode.Stretch)
            else:
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
                self._table.setColumnWidth(i, w)
        body.addWidget(self._table, 3, Qt.AlignmentFlag.AlignTop)
        card_lay.addLayout(body, 1)
        # 卡片高度由内容（图表视口高度）决定，页面滚动承接超长内容
        root.addWidget(card)
        # 初始视口高度（空态）
        self._sync_chart_height()
        self._sync_table_height()

    def _sync_chart_height(self):
        """按行数设定图表视口高度

        TOP 10/20 要完整展示、不出现滚动条；行数再多就按实际高度展开，
        超过 1600px 才封顶转内部滚动。封顶值不是随手取的：
        再高整页滚动会让人失去「现在看到第几名」的定位感。
        空数据给 280px，是为了空态文案与卡片留白不塌成一条缝。
        """
        n = len(self._rows)
        h = min(n * _RankHBarChart.ROW_H + 18, 1600) if n else 280
        self._chart_scroll.setFixedHeight(h)

    def _sync_table_height(self):
        """把表格高度钉成「表头 + 行数 × 行高」

        qfw TableWidget 的 sizeHint 明显虚高，十行数据能报出约 400px 的期望高度。
        不钉死会出现两个问题：排行卡底部拖着一大片空白；右侧表格把 body
        撑高后与左边图表顶部错位，看起来像两栏没对齐。
        行数从 50 切回 10 时高度必须跟着缩，否则会残留上一次的高度。
        1600px 以上封顶转表格内滚动，与图表的策略保持一致。
        """
        hdr = self._table.horizontalHeader()
        # 表头高度取实测值，拿不到才回落到 sizeHint 与 30 的兜底：
        # 表头在首次布局前 height() 是 0，直接用会把首屏高度算矮一截。
        hh = hdr.height() or hdr.sizeHint().height() or 30
        n = len(self._rows)
        h = int(min(hh + n * _FIXED_ROW_HEIGHT + 4, 1600)) if n else 280
        self._table.setFixedHeight(h)

    @staticmethod
    def _qdate_today():
        """取今天对应的 QDate，供日期选择器初始化

        在函数内 import QDate 而不是放模块顶层：本模块只需要一个类型，
        顶层再加一行 import 会让「这页依赖哪些 Qt 类型」更难扫。
        """
        from PySide6.QtCore import QDate
        return QDate.currentDate()

    def _make_card(self, layout) -> tuple:
        """造一张概览卡并挂到给定布局上，返回四个控件的元组

        返回元组而不是自定义类，是因为这四张卡的更新方式完全一样
        （改标签、改数字、改说明），为它专门定义一个类只会多一层跳转。
        元组顺序固定为卡片、标签、数字、说明，调用方按位解包。
        """
        card = CardWidget(self)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        lbl = CaptionLabel("", card)
        num = QLabel("0", card)
        num.setStyleSheet(
            "font-size: 24px; font-weight: 500; background: transparent;")
        sub = CaptionLabel("", card)
        lay.addWidget(lbl)
        lay.addWidget(num)
        lay.addWidget(sub)
        layout.addWidget(card, 1)
        return (card, lbl, num, sub)

    # ---------- 筛选条件 ----------

    def _current_filters(self) -> dict:
        """把界面上的筛选控件翻译成查询参数字典

        时间范围的四种形态在协议层收敛成三个字段：具体区间进 start/end，
        账期进 cycle_start，两者互斥——同时给会让 SQL 条件互相矛盾。
        「近 30 天」用 29 天偏移而不是 30，是为了让区间含首尾正好 30 天，
        差一天在用户核对条数时会被当成 bug 报上来。
        下钻球房不在这里出现：它由 _drill 单独传，因为这个方法只读控件，
        而下钻是页面状态，混进来会让「筛选」与「位置」两个概念纠缠。
        """
        rng = self._range_seg.currentRouteKey()
        start = end = ""
        cycle_start = ""
        if rng == "d30":
            start = (date.today() - timedelta(days=29)).isoformat()
            end = date.today().isoformat()
        elif rng == "d90":
            start = (date.today() - timedelta(days=89)).isoformat()
            end = date.today().isoformat()
        elif rng == "cycle":
            cycle_start = str(self._cycle_combo.currentData() or "")
        elif rng == "custom":
            start = self._start_picker.date.toString("yyyy-MM-dd")
            end = self._end_picker.date.toString("yyyy-MM-dd")
            if start and end and start > end:
                start, end = end, start
        return {
            "start": start, "end": end, "cycle_start": cycle_start,
            "resolved": self._resolved_seg.value(),
            "is_initiative": self._initiative_seg.value(),
            "is_our_problem": self._our_problem_seg.value(),
            "region": str(self._region_combo.currentData() or ""),
            "keyword": self._search_edit.text().strip(),
        }

    def _on_range_changed(self, _key):
        """时间范围切换后同步相关控件的显隐，再重新查询

        账期下拉与自定义起止控件常驻在工具栏里，只在使用时才显示：
        一次性摆出来会挤掉后面的筛选条件，而工具栏宽度是有限的。
        """
        rng = self._range_seg.currentRouteKey()
        self._cycle_combo.setVisible(rng == "cycle")
        for w in (self._start_picker, self._end_picker, self._lbl_to):
            w.setVisible(rng == "custom")
        self._on_filter_changed()

    def _on_filter_changed(self):
        """任何筛选控件变化后的统一入口，直接重查

        不做额外去抖：能触发它的控件都是下拉或分段按钮，本身已经是
        离散动作；真正高频的文本输入走的是 _search_timer 那条防抖路径。
        """
        self._load()

    def _on_reset(self):
        """把全部筛选恢复默认值，然后只查一次

        每个控件在修改前都要 blockSignals：十来个控件逐个改完再统一查询，
        否则每改一个就跑一次排行聚合，重置瞬间会把数据库打出十几倍的负载。
        下钻与层级一起复位，重置的语义是「回到刚进页面时的样子」。
        """
        for seg in (self._resolved_seg, self._our_problem_seg,
                    self._initiative_seg):
            seg.blockSignals(True)
            seg.setValue("")
            seg.blockSignals(False)
        self._range_seg.blockSignals(True)
        self._range_seg.setCurrentItem("d90")
        self._range_seg.blockSignals(False)
        for combo in (self._cycle_combo, self._region_combo,
                      self._limit_combo, self._sort_combo):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self._search_edit.blockSignals(True)
        self._search_edit.clear()
        self._search_edit.blockSignals(False)
        self._cycle_combo.hide()
        for w in (self._start_picker, self._end_picker, self._lbl_to):
            w.hide()
        self._drill = None
        self._level_seg.blockSignals(True)
        self._level_seg.setCurrentItem("room")
        self._level_seg.blockSignals(False)
        self._level = "room"
        self._load()

    # ---------- 选项与数据加载 ----------

    def showEvent(self, event):
        """首次显示时加载下拉选项

        只在这里拉一次：选项来自数据库字典表，启动时就查等于替所有用户
        付一次他们可能根本不进这一页的成本。
        """
        super().showEvent(event)
        if not self._options_loaded:
            self._load_options()

    def reload_options(self):
        """周期配置改动后由宿主窗口调用，把选项标记为过期

        不可见时不立即重拉，等下次显示再走 showEvent；
        但标志位必须立刻置回，否则用户改完配置切回来还是旧账期。
        """
        self._options_loaded = False
        if self.isVisible():
            self._load_options()

    def _load_options(self):
        if (self._options_worker is not None
                and self._options_worker.isRunning()):
            return
        self._options_worker = AftersaleDBWorker(_fetch_rank_options)
        self._options_worker.result_ready.connect(self._on_options_loaded)
        self._options_worker.error.connect(lambda _m: self._load())
        # 选项拉取失败不单独报错，直接继续查排行：选项是辅助筛选，
        # 拿不到也只是少了两个下拉的候选，不该连带把主数据挡住。
        self._options_worker.start()

    def _on_options_loaded(self, opts):
        """把周期与地区选项灌进两个下拉框，然后开始查排行

        两个 combo 都先清空再补「全部」项：清空会触发 currentIndexChanged，
        不挡信号就会在中途发一次带空条件的查询，白跑一轮。
        灌完选项紧接着 _load，因为首屏本来就欠一次查询。
        """
        self._options_loaded = True
        cycles, regions = opts or ([], [])
        self._cycle_combo.blockSignals(True)
        self._cycle_combo.clear()
        self._cycle_combo.addItem("全部周期", userData="")
        for cs in cycles or []:
            self._cycle_combo.addItem(
                aftersale_db.cycle_label(cs), userData=cs)
        self._cycle_combo.blockSignals(False)
        # 地区选项来自字典表，可能是数字或带空格的历史值，
        # 统一转成字符串：ComboBox 的 userData 类型不一致时 currentData 取值会漂。
        self._region_combo.blockSignals(True)
        self._region_combo.clear()
        self._region_combo.addItem("全部地区", userData="")
        for rg in regions or []:
            self._region_combo.addItem(str(rg), userData=str(rg))
        self._region_combo.blockSignals(False)
        self._load()

    def _load(self):
        """按当前筛选发起一次异步排行查询

        旧任务先请求中断：排行是聚合查询，在 MySQL 上可能跑上几秒，
        让它在用户改完条件后继续占着连接毫无意义。
        但中断只是尽力而为，SQLite 与部分驱动并不响应，所以还需要 _query_seq：
        每轮自增一个代数，回调里对不上代数就直接丢掉结果。
        两个机制要同时在，缺了中断会拖慢数据库，缺了代数会让旧结果覆盖新结果。
        """
        if self._worker and self._worker.isRunning():
            self._worker.requestInterruption()
        self._query_seq += 1
        seq = self._query_seq
        f = self._current_filters()
        self._worker = AftersaleDBWorker(
            aftersale_db.query_rank,
            self._level, int(self._limit_combo.currentData() or 10),
            str(self._sort_combo.currentData() or "total"),
            start=f["start"], end=f["end"], cycle_start=f["cycle_start"],
            resolved=f["resolved"], is_initiative=f["is_initiative"],
            is_our_problem=f["is_our_problem"], region=f["region"],
            room_name=self._drill or "", keyword=f["keyword"])
        # 代数用默认参数在连线时固化下来，而不是在回调里读 self._query_seq：
        # 回调执行时这个属性早被下一轮查询改掉了，读到的永远是最新值，
        # 守卫等于没有。默认参数在定义那一刻求值，正好钉住本轮代数。
        self._worker.result_ready.connect(
            lambda result, _s=seq: self._on_loaded(result, _s))
        self._worker.error.connect(
            lambda msg, _s=seq: self._on_error(msg, _s))
        self._worker.start()

    def _on_loaded(self, result, seq=None):
        """查询成功：回写行数据、刷新概览卡与标题，再同步左右两栏

        先判代数再动界面：过期结果一旦落到 _rows，表格与图表会闪一版
        跟当前筛选无关的数据，用户会以为筛选没生效。
        图表与表格各自重算高度，两栏必须同步调，否则会出现高度不齐。
        """
        if seq is not None and seq != self._query_seq:
            return
        # 深拷贝一层：worker 的返回对象会被 Qt 信号持有，
        # 页面后续就地过滤时不能改动它。
        self._rows = list((result or {}).get("rows") or [])
        self._summary = dict((result or {}).get("summary") or {})
        self._update_summary_cards()
        self._update_rank_title()
        # 下钻或球桌级用警示色条形，全局球房榜用主题强调色：
        # 两种层级的图长得一样时，切完层级只有标题变了，很容易看错。
        bar = (SEMANTIC["warning"]
               if (self._drill or self._level == "table") else None)
        # 顺序不能颠倒：先灌数据再改高度，否则高度按旧行数算，
        # 换档位时会先闪一版错位的版面。
        self._chart.set_rows(self._rows, bar_color=bar)
        self._sync_chart_height()
        self._populate_table()
        self._sync_table_height()

    def _on_error(self, msg, seq=None):
        """查询失败：清空图表与表格，把错误写进标题

        失败时清空而不是保留上一批数据：留着的旧排行配上新的筛选条件，
        用户会把它当成当前结果，比空表更危险。
        错误信息只进标题，不弹窗——查询失败通常是数据库瞬断，
        弹窗会打断用户正在做的下一件事。
        """
        if seq is not None and seq != self._query_seq:
            return
        self._chart.set_rows([])
        self._sync_chart_height()
        self._table.setRowCount(0)
        self._sync_table_height()
        # 失败原因写进标题而不是状态栏：状态栏消息几秒就淡出，
        # 用户抬头看时已经不知道这页为什么是空的。
        self._lbl_rank_title.setText(f"售后排行（查询失败：{msg}）")

    # ---------- 渲染 ----------

    def _update_summary_cards(self):
        """按汇总数据刷新四张概览卡

        每个数字都重新 setStyleSheet：未解决数非零时要把数字染成警示色，
        染色与复原必须走同一条路径，否则上一次的红色会留在清零后的卡上。
        「未解决」的副标题随值变化，0 条时明确写「全部已处理」，
        而不是让用户自己从 0 去推断。
        """
        s = self._summary
        total = int(s.get("total") or 0)
        unresolved = int(s.get("unresolved") or 0)
        base = "font-size: 24px; font-weight: 500; background: transparent;"

        # 四张卡按固定顺序解包元组，元组结构由 _make_card 保证。
        _c, lbl, num, sub = self._card_rooms
        lbl.setText("覆盖球房")
        num.setText(str(int(s.get("rooms") or 0)))
        num.setStyleSheet(base)
        # 副标题写明「去重」：球房数不等于记录里出现过的球房次数，
        # 不写清楚会有人拿它跟记录数相除来核对占比。
        sub.setText("去重非空球房数")

        _c, lbl, num, sub = self._card_tables
        lbl.setText("覆盖球桌")
        num.setText(str(int(s.get("tables") or 0)))
        num.setStyleSheet(base)
        # 球桌的唯一键是「球房 + 桌号」，不是桌号本身：
        # 各家球房的 1 号桌是同一串字符，按桌号去重会把它们合成一张。
        sub.setText("去重球房·桌号对")

        _c, lbl, num, sub = self._card_total
        lbl.setText("记录总数")
        num.setText(str(total))
        num.setStyleSheet(base)
        # 总数受左侧筛选影响，副标题点明口径，
        # 免得有人拿它跟售后首页的累计数对不上就来报 bug。
        sub.setText("当前筛选口径")

        # 最后一张才可能染色，前三张固定用基础样式重置，
        # 防止上一次（比如未解决非零时）的样式残留到别的卡上。
        _c, lbl, num, sub = self._card_unresolved
        lbl.setText("未解决")
        num.setText(str(unresolved))
        # 未解决为 0 时去掉染色：0 还标红会让人以为还有积压要处理。
        num.setStyleSheet(base + (
            f" color: {SEMANTIC['danger']};" if unresolved else ""))
        sub.setText("待处理积压" if unresolved else "全部已处理")

    def _update_rank_title(self):
        """按层级与下钻状态更新标题与面包屑

        下钻时标题固定写「球桌」：下钻后的榜单本来就是该球房下的球桌，
        沿用「球房排行」会与内容自相矛盾。
        """
        if self._drill:
            self._lbl_rank_title.setText("球桌售后排行")
            self._btn_back.show()
            self._lbl_drill.setText(self._drill)
            self._lbl_drill.show()
        else:
            self._lbl_rank_title.setText(
                "球房售后排行" if self._level == "room"
                else "球桌售后排行")
            self._btn_back.hide()
            self._lbl_drill.hide()

    def _populate_table(self):
        """把排行数据铺进右侧表格

        前三名用金银铜徽章加粗，是榜单类界面最省事的「层次提示」；
        指标列按语义上色：未解决用警示红、我方问题用琥珀色，
        零值一律不上色，否则满屏都是颜色，等于没有重点。
        逐行 setItem 期间关闭更新与信号：几十行的重绘与选中变化
        全部压到最后一次，切换 TOP 档位时才不会看到表格逐行跳出来。
        """
        rows = self._rows
        table = self._table
        table.setUpdatesEnabled(False)
        table.blockSignals(True)
        try:
            table.setRowCount(len(rows))
            bold = QFont()
            bold.setBold(True)
            center = Qt.AlignmentFlag.AlignCenter
            for r, rec in enumerate(rows):
                rank = int(rec.get("rank") or (r + 1))
                # 排名优先用后端返回值，缺失才退回行号 + 1：
                # 前端自己排名会在「按未解决排序」时与真实名次不一致。
                it = QTableWidgetItem(str(rank))
                it.setTextAlignment(center)
                if rank <= 3:
                    it.setForeground(QColor(_MEDAL_COLORS[rank - 1]))
                    it.setFont(bold)
                table.setItem(r, 0, it)

                # 球桌级的名称只显示桌号，球房信息挂进 tooltip：
                # 名称列宽度有限，把「球房-桌号」拼进去会截断成一堆省略号。
                it = QTableWidgetItem(str(rec.get("name") or ""))
                if rec.get("table_no"):
                    it.setToolTip(f"球房: {rec.get('room_name') or ''}\n"
                                  f"桌号: {rec.get('table_no') or ''}")
                table.setItem(r, _COL_NAME, it)

                # 总量加粗而不上色：它是排序的主指标，需要用字重强调，
                # 但用颜色强调会和下面两张语义色列抢注意力。
                it = QTableWidgetItem(str(int(rec.get("total") or 0)))
                it.setTextAlignment(center)
                it.setFont(bold)
                table.setItem(r, 2, it)

                # 占比只显示整数百分比：行数一多，小数位就是纯噪声。
                it = QTableWidgetItem(f"{int(rec.get('share') or 0)}%")
                it.setTextAlignment(center)
                table.setItem(r, 3, it)

                # 未解决与「我方问题」只在非零时染色，零值保持默认色：
                # 全表都是颜色时，颜色就不再指示任何东西。
                un = int(rec.get("unresolved") or 0)
                it = QTableWidgetItem(str(un))
                it.setTextAlignment(center)
                if un:
                    it.setForeground(QColor(SEMANTIC["danger"]))
                table.setItem(r, 4, it)

                our = int(rec.get("our_problem") or 0)
                it = QTableWidgetItem(str(our))
                it.setTextAlignment(center)
                if our:
                    it.setForeground(QColor(SEMANTIC["warning"]))
                table.setItem(r, 5, it)

                # 缺失时间写长破折号而不是留空：空单元格会被当成渲染失败。
                it = QTableWidgetItem(str(rec.get("last_occurred") or "—"))
                it.setForeground(QColor(_muted_color()))
                table.setItem(r, 6, it)

                # 操作列：链接清单按层级取。只有球房全局榜才有「下钻」——
                # 球桌级已经是最细粒度，再下钻没有下一层；已下钻的球房榜
                # 也不给，否则会对着同一球房无限套娃。
                it = QTableWidgetItem("")
                links = [("明细", "primary", "detail")]
                if self._level == "room" and not self._drill:
                    links.append(("下钻", "primary", "drill"))
                it.setData(LINKS_ROLE, tuple(links))
                table.setItem(r, _COL_OPS, it)
        finally:
            table.setUpdatesEnabled(True)
            table.blockSignals(False)

    # ---------- 交互 ----------

    def _on_level_changed(self, key):
        """球房/球桌分段切换：清下钻并按新层级重查

        必须清 _drill：从「某球房的球桌榜」切到球房榜还留着下钻条件的话，
        球房榜会被这个条件过滤成只剩一个球房。
        """
        # 白名单式赋值：分段控件返回的 key 不可全信，
        # 直接把字符串写进 _level 会让后端拿到未知层级并静默返回空榜。
        self._level = "table" if key == "table" else "room"
        self._drill = None
        self._load()

    def _on_drill_back(self):
        """面包屑返回：清下钻回到全局球房榜

        同步把层级分段拨回「球房」并挡住信号：分段控件自己会再发一次
        currentItemChanged，不挡就会在同一个动作里查两遍。
        """
        self._drill = None
        self._level_seg.blockSignals(True)
        self._level_seg.setCurrentItem("room")
        self._level_seg.blockSignals(False)
        self._level = "room"
        self._load()

    def _on_chart_row(self, idx: int):
        """图表行点击：全局球房榜下钻，其它情况跳明细

        同一个点击在不同层级做不同的事，是因为下钻只在有下一层时才有意义：
        球桌级再钻就只能进记录了。
        """
        # 越界守卫：点击与数据刷新之间可能隔了一次查询，
        # 拿到旧下标就直接返回，别让下钻跳到一个不存在的球房。
        if not (0 <= idx < len(self._rows)):
            return
        rec = self._rows[idx]
        if self._level == "room" and not self._drill:
            self._drill_into(rec)
        else:
            self._goto_detail(rec)

    def _on_ops_link(self, row: int, _col: int, action: str):
        """操作列文字链接的点击分发

        行号来自 delegate，可能指向已经刷新过的旧行，所以先做越界检查；
        未知 action 直接忽略，避免以后新增链接忘了在这里接线时抛异常。
        """
        if not (0 <= row < len(self._rows)):
            return
        rec = self._rows[row]
        if action == "drill":
            self._drill_into(rec)
        elif action == "detail":
            self._goto_detail(rec)

    def _drill_into(self, rec: dict):
        """下钻到指定球房的球桌榜

        球房名为空就不下钻：空名下钻会查出全部球桌，看起来像功能没生效。
        下钻后把层级分段同步到「球桌」，否则界面上写的还是球房榜，
        而表格里已经是球桌，用户没法判断自己现在在哪一层。
        """
        room = str(rec.get("room_name") or "").strip()
        if not room:
            return
        # 下钻后层级必须一起切到球桌：只改 _drill 不改层级，
        # 后端仍按球房聚合，返回的还是被过滤后的单行球房榜。
        self._drill = room
        self._level = "table"
        self._level_seg.blockSignals(True)
        self._level_seg.setCurrentItem("table")
        self._level_seg.blockSignals(False)
        self._load()

    def _goto_detail(self, rec: dict):
        """跳记录页，用桌号或球房名做关键词预筛选

        桌号优先：球桌级的行带着 table_no，用它筛出来的记录最精确；
        球房级没有桌号，退回球房名，让记录页把该球房全部记录列出来。
        两者都没有时再退到名称列，避免发出一个空关键词把记录页筛空。
        """
        kw = (str(rec.get("table_no") or "").strip()
              if rec.get("table_no")
              else str(rec.get("room_name") or "").strip())
        if not kw:
            kw = str(rec.get("name") or "").strip()
        # 关键词交给记录页的搜索框，而不是发结构化条件：
        # 记录页的搜索本来就覆盖桌号与球房名，为这一个入口加参数不划算。
        self.jump_to_records.emit(kw)

    # ---------- 导出 ----------

    def _on_export(self):
        """把当前排行导出为 xlsx

        导出的是「当前所见」——筛选条件、下钻层级、排序都不重算，
        用户按什么条件看到就导出什么，避免导出的文件与屏幕上对不上。
        写文件失败只提示不中断：文件被 Excel 占用是最常见的失败原因，
        用户关掉再点一次就好，没必要把页面状态搞乱。
        """
        if not self._rows:
            show_info_bar("当前排行无数据可导出", "warning",
                          title="导出", parent=self, duration=2500)
            return
        # 默认文件名带层级与日期：同一天导出球房榜与球桌榜时，
        # 文件名不区分会让第二次导出直接覆盖第一次（对话框默认同名）。
        default_name = (f"售后排行_{'球桌' if (self._drill or self._level == 'table') else '球房'}_"
                        f"{date.today().isoformat()}.xlsx")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出排行", default_name, "Excel 工作簿 (*.xlsx)")
        if not path:
            return
        try:
            n = self._write_rank_xlsx(path)
        except Exception as e:
            show_info_bar(f"导出失败：{type(e).__name__}: {e}", "error",
                          title="导出", parent=self, duration=4000)
            return
        show_info_bar(f"已导出 {n} 行排行 → {path}", "success",
                      title="导出", parent=self, duration=3000)

    def _write_rank_xlsx(self, path: str) -> int:
        """写出 xlsx 并返回导出的行数

        openpyxl 在函数内导入：导出是低频操作，模块顶层导入会让每次启动
        都多付一次约百毫秒的导入成本。
        末行补一条口径说明，因为导出的表格脱离了界面就看不到筛选条件，
        没有这一行事后无法判断「这 3000 条是什么范围」。
        """
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill

        wb = Workbook()
        ws = wb.active
        ws.title = "售后排行"
        headers = ("排名", "名称", "总量", "占比(%)", "未解决",
                   "我方问题", "主动发起", "最近发生")
        header_font = Font(bold=True)
        header_fill = PatternFill("solid", fgColor="D9F2F4")
        # 表头用手写浅青底而不是主题色：导出的文件多半在 Excel 里看，
        # 深色主题的强调色配上黑字几乎读不出来。
        for c, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=c, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")
        for r, rec in enumerate(self._rows, 2):
            values = (
                int(rec.get("rank") or 0), str(rec.get("name") or ""),
                int(rec.get("total") or 0), int(rec.get("share") or 0),
                int(rec.get("unresolved") or 0),
                int(rec.get("our_problem") or 0),
                int(rec.get("initiative") or 0),
                str(rec.get("last_occurred") or ""))
            for c, v in enumerate(values, 1):
                ws.cell(row=r, column=c, value=v)
        # 数据从第 2 行开始，所以口径说明落在 len(rows) + 2 行上，
        # 正好在最后一行数据之下空出一行。
        s = self._summary
        last = len(self._rows) + 2
        summary_note = (f"口径：共 {int(s.get('total') or 0)} 条记录 · "
                        f"覆盖球房 {int(s.get('rooms') or 0)} · "
                        f"覆盖球桌 {int(s.get('tables') or 0)} · "
                        f"未解决 {int(s.get('unresolved') or 0)}")
        ws.cell(row=last, column=1, value=summary_note).font = header_font
        # 列宽按内容长度硬编码：openpyxl 不会自动适配，
        # 不设的话名称列会在 Excel 里显示成一串 ####。
        for c, w in enumerate((8, 32, 8, 8, 8, 10, 10, 12), 1):
            ws.column_dimensions[ws.cell(row=1, column=c).column_letter].width = w
        # 冻结表头：导出的排行动辄几十上百行，
        # 往下翻时看不到列名就只能靠记忆判断某一列是什么。
        ws.freeze_panes = "A2"
        wb.save(path)
        return len(self._rows)
