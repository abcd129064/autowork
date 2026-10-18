# -*- coding: utf-8 -*-
"""跑视频面板：记录与统计页（指标卡 + 日期/分类/类别/署名筛选 + 分页 + 编辑/删除 + 统计/导出）

需求5（按天统计）：工具栏新增日期筛选区（模式下拉 + 起止日期控件），
指标卡与列表同口径联动；实现参照售后面板周期筛选范式。
"""
from PySide6.QtCore import Qt, QTimer, QDate
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QHeaderView, QAbstractItemView, QFileDialog,
                               QTableWidgetItem)

from qfluentwidgets import (TableWidget, SearchLineEdit, PushButton,
                            ToolButton, FluentIcon, CaptionLabel,
                            BodyLabel, CardWidget, MessageBox,
                            ComboBox, EditableComboBox,
                            CalendarPicker, RoundMenu, Action)

from core.design_tokens import SEMANTIC
from core.flow_widgets import FlowToolbarScrollArea
from core.ops_link_delegate import LINKS_ROLE, install_ops_links
from core.perf import apply_table_smooth_mode
from core.utils import show_info_bar
from database import ledger_db

from windows.run_video.common import *  # noqa: F401,F403
from windows.run_video.records.controller import RecordsController
from windows.run_video.records.dialogs import (EditLedgerDialog,
                                                SignerStatsDialog)
from windows.run_video.records.query_state import (short_dt, date_range,
                                                    current_filters,
                                                    pages_of, clamp_page,
                                                    page_text, total_text)
from windows.run_video.records.table_adapter import FONT_BOLD, data_cells
from windows.stat_charts import StatsOpener, _ledger_options


class RecordsPage(QWidget):
    """记录与统计页：四分类指标卡 + 筛选/分页 + 行内编辑/删除 + 署名统计 + 导出"""

    # 面板标识：供 core.perf 面板级覆盖取值与全局刷新发现使用（跑视频面板）
    _PERF_PANEL_KEY = "video"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page_no = 1
        self._page_size = 50
        self._total = 0
        self._rows = []
        # 查询/指标卡的代际号：controller 的异步结果回来时先比 seq，
        # 过滤掉筛选连续切换后迟到的旧帧（详见 controller 模块 docstring）。
        self._seq = 0
        self._stats_seq = 0
        self._manual_refresh = False  # 手动刷新标志：完成/失败时弹 infobar 反馈
        # 自动刷新（需求4，仿售后面板范式）：轮询轻量指纹，仅在数据真的
        # 变化时静默重查并保持阅读位置。worker 全部归 controller 所有，
        # 页面只保留定时器与指纹快照。
        self._auto_timer = QTimer(self)
        self._auto_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._auto_timer.timeout.connect(self._auto_refresh_tick)
        self._last_fingerprint = None   # 上次全表指纹快照（None=需重建基线）
        self._preserve_view = None      # 静默重查待恢复的滚动偏移
        self._controller = RecordsController(self)
        self._connect_controller()
        self._init_ui()
        # 分类徽章字体只构造一次（循环内逐格 new QFont 会拖慢 50 行渲染）
        self._bold_font = QFont()
        self._bold_font.setBold(True)
        self._sync_auto_timer()
        # pygwalker 统计图表（独立浏览器窗口，工具栏「统计图表」按钮触发）
        self._stats_opener = StatsOpener(_ledger_options, "run_video", self)
        self._stats_opener.finished.connect(self._on_stats_finished)

    # ---------- controller 连线 / 生命周期 ----------

    def _connect_controller(self):
        """把异步编排层的信号接到视图槽；所有槽都在主线程（Qt 自动排队）。

        指纹信号先过一个动态分发器：测试会 monkeypatch 页面上的
        _on_fingerprint，若 controller 直接连旧绑定就绕过了补丁。
        """
        c = self._controller
        c.rows_ready.connect(self._on_loaded)
        c.load_failed.connect(self._on_load_error)
        c.stats_ready.connect(self._on_stats_loaded)
        c.signers_ready.connect(self._on_signers_ready)
        c.candidates_ready.connect(self._on_candidates_ready)
        c.fingerprint_ready.connect(self._dispatch_fingerprint)
        c.mutation_done.connect(self._on_mutation_done)
        c.mutation_failed.connect(self._on_mutation_failed)
        c.export_done.connect(self._on_export_done)
        c.export_failed.connect(self._on_export_failed)
        c.signer_stats_ready.connect(self._on_signer_stats_ready)
        c.signer_stats_failed.connect(self._on_signer_stats_failed)

    def _dispatch_fingerprint(self, fp):
        """稳定转调，保证 monkeypatch(self._on_fingerprint) 仍然生效。"""
        self._on_fingerprint(fp)

    def detach_workers(self):
        """切页/关窗：停自动刷新并请求中断 controller 的全部后台线程。

        主窗口 pivot_page 的目录扫描仍会兜底扫 *_worker 属性，但本页真正
        的 worker 都在 controller 里，必须显式转调 stop_all。
        """
        self._auto_timer.stop()
        self._controller.stop_all()

    @property
    def _worker(self):
        """兼容旧测试/外部探针：查询 worker 的只读视图（不再是页面属性）。"""
        return self._controller._query_worker

    @property
    def _auto_worker(self):
        """兼容旧测试/外部探针：指纹 worker 的只读视图。"""
        return self._controller._auto_worker

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 12)
        root.setSpacing(8)

        # --- 页头：标题 + 数据源状态 ---
        head = QHBoxLayout()
        head.setSpacing(10)
        head_box = QVBoxLayout()
        head_box.setSpacing(1)
        # head_box.addWidget(TitleLabel("记录与统计", self))
        # head.addLayout(head_box)
        head.addStretch(1)
        self._lbl_source = CaptionLabel("", self)
        head.addWidget(self._lbl_source, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(head)

        # --- 四分类指标卡 + 总数卡（需求5：点击跳转对应筛选） ---
        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)
        self._cards = {}
        self._card_widgets = {}
        for key, title in (("total", "总记录"), ("问题", "问题"),
                           ("未复现", "未复现"), ("精度", "精度"),
                           ("使用", "使用")):
            card, _l, num = self._make_stats_card(cards_row, title)
            self._cards[key] = num
            self._card_widgets[key] = card
            # 卡片可点击：手型光标 + tooltip；分类卡 toggle 分类筛选，
            # 总记录卡一键清空全部筛选（对齐售后「未解决」卡范式）
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            if key == "total":
                card.setToolTip("点击清空全部筛选，显示所有记录")
                card.clicked.connect(self._on_total_card_clicked)
            else:
                card.setToolTip(
                    f"点击只看「{title}」分类（再次点击恢复全部分类）")
                card.clicked.connect(
                    lambda _=False, k=key: self._on_category_card_clicked(k))
        root.addLayout(cards_row)

        # --- 筛选工具栏（与售后记录页同范式） ---
        toolbar_scroll = FlowToolbarScrollArea(self)
        toolbar_scroll.setWidgetResizable(True)
        toolbar_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        toolbar_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        toolbar_scroll.setMaximumHeight(132)  # 需求5：新增日期区后允许多行换行
        toolbar_widget = QWidget(self)
        toolbar_scroll.setWidget(toolbar_widget)
        from qfluentwidgets import FlowLayout
        toolbar = FlowLayout(toolbar_widget)
        toolbar.setHorizontalSpacing(6)
        toolbar.setVerticalSpacing(6)
        toolbar.setContentsMargins(2, 4, 2, 4)

        # --- 日期筛选（需求1+2 重构：基准日历 + 快捷范围，控件对齐主面板） ---
        # 模式：全部/今天/近7天/近30天/本月/自定义；快捷档均由「基准日期」
        # 驱动（CalendarPicker + ◀▶ 步进，可翻看昨天/前天等历史日期），
        # 仅「自定义」显示第二个结束日历。
        toolbar.addWidget(CaptionLabel("日期：", self))
        self._date_mode_combo = ComboBox(self)  # QFluentWidgets 原生（需求9）
        self._date_mode_combo.addItems(
            ["全部", "今天", "近7天", "近30天", "本月", "自定义"])
        self._date_mode_combo.setCurrentIndex(1)  # 默认「今天」
        self._date_mode_combo.setFixedWidth(110)
        self._date_mode_combo.currentIndexChanged.connect(
            lambda _i: self._on_date_mode_changed())
        toolbar.addWidget(self._date_mode_combo)
        # 基准日期：与主面板相同方式（CalendarPicker minWidth150 + 26×32 步进键）
        self._date_from = CalendarPicker(self)
        self._date_from.setMinimumWidth(150)
        self._date_from.setDate(QDate.currentDate())  # 默认当天
        self._date_from.dateChanged.connect(
            lambda _d: self._on_filter_changed())
        toolbar.addWidget(self._date_from)
        self._date_prev = ToolButton(FluentIcon.LEFT_ARROW, self)
        self._date_prev.setFixedSize(26, 32)
        self._date_prev.setToolTip("基准日期：前一天")
        self._date_prev.clicked.connect(
            lambda _=False: self._step_base_date(-1))
        toolbar.addWidget(self._date_prev)
        self._date_next = ToolButton(FluentIcon.RIGHT_ARROW, self)
        self._date_next.setFixedSize(26, 32)
        self._date_next.setToolTip("基准日期：后一天")
        self._date_next.clicked.connect(
            lambda _=False: self._step_base_date(1))
        toolbar.addWidget(self._date_next)
        # 结束日期：仅「自定义」模式显示
        self._date_to = CalendarPicker(self)
        self._date_to.setMinimumWidth(150)
        self._date_to.setDate(QDate.currentDate())
        self._date_to.dateChanged.connect(
            lambda _d: self._on_filter_changed())
        self._date_to.setVisible(False)
        toolbar.addWidget(self._date_to)

        # --- 复现筛选（需求6：售后面板同款 SegmentedWidget，三态 全部/否/是） ---
        # 与售后面板「是否解决：」筛选同范式：CaptionLabel 前缀 + YesNoSegment。
        self._repro_seg = YesNoSegment("", self, include_all=True)
        self._repro_seg.setToolTip("是否复现")
        self._repro_seg.currentItemChanged.connect(
            lambda _k: self._on_filter_changed())
        toolbar.addWidget(CaptionLabel("复现：", self))
        toolbar.addWidget(self._repro_seg)

        # 分类筛选（qfluentwidgets ComboBox，需求9；非可编辑用库原生组件）
        self._cat_combo = ComboBox(self)
        self._cat_combo.addItem("全部分类")
        self._cat_combo.addItems(ledger_db.CATEGORIES)
        self._cat_combo.setFixedWidth(130)
        self._cat_combo.currentIndexChanged.connect(
            lambda _i: self._on_cat_changed())
        toolbar.addWidget(self._cat_combo)

        # 类别筛选（随分类联动候选；需求12：EditableComboBox 替代 FluentCombo，
        # 可编辑手输 + 下拉候选，全量 qfluentwidgets 组件）
        self._kind_combo = EditableComboBox(self)
        self._kind_combo.addItem("全部类别")
        self._kind_combo.setFixedWidth(170)
        self._kind_combo.currentIndexChanged.connect(
            lambda _i: self._on_filter_changed())
        self._kind_combo.currentTextChanged.connect(
            lambda _t: self._search_timer.start())
        toolbar.addWidget(self._kind_combo)

        # 署名筛选（qfluentwidgets ComboBox，需求9；findText 兼容）
        self._signer_combo = ComboBox(self)
        self._signer_combo.addItem("全部署名")
        self._signer_combo.setFixedWidth(110)
        self._signer_combo.currentIndexChanged.connect(
            lambda _i: self._on_filter_changed())
        toolbar.addWidget(self._signer_combo)

        # 关键词搜索（防抖）
        self._search_edit = SearchLineEdit(self)
        self._search_edit.setPlaceholderText("搜索")
        self._search_edit.setFixedWidth(180)
        self._search_edit.textChanged.connect(self._on_search_input)
        toolbar.addWidget(self._search_edit)

        self._btn_add = PushButton(FluentIcon.ADD, "新增", self)
        self._btn_add.setToolTip("新增一条跑视频记录（点击直接填写并提交）")
        self._btn_add.clicked.connect(self._on_add)
        toolbar.addWidget(self._btn_add)

        self._btn_stats = PushButton(FluentIcon.PIE_SINGLE, "署名统计", self)
        self._btn_stats.setToolTip("按署名统计四分类计数（模板「计数」sheet）")
        self._btn_stats.clicked.connect(self._on_show_stats)
        toolbar.addWidget(self._btn_stats)

        self._btn_export = PushButton(FluentIcon.DOWNLOAD, "导出 xlsx", self)
        self._btn_export.setToolTip("按分类分 sheet 导出")
        self._btn_export.clicked.connect(self._on_export)
        toolbar.addWidget(self._btn_export)

        self._btn_refresh = PushButton(FluentIcon.SYNC, "刷新", self)
        self._btn_refresh.setToolTip("重新查询数据库")
        self._btn_refresh.clicked.connect(self._on_refresh)
        toolbar.addWidget(self._btn_refresh)

        # pygwalker 自助分析入口（浏览器独立窗口，拖拽字段出图）
        self._btn_stats_chart = PushButton(
            FluentIcon.HISTORY, "统计图表", self)
        self._btn_stats_chart.setToolTip(
            "打开 pygwalker 自助分析窗口（浏览器独立窗口，拖拽字段出图）")
        self._btn_stats_chart.clicked.connect(self._on_open_stats_chart)
        toolbar.addWidget(self._btn_stats_chart)

        root.addWidget(toolbar_scroll)

        # 搜索防抖定时器
        self._search_timer = QTimer(self)
        self._search_timer.setInterval(300)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._on_filter_changed)

        # --- 表格 ---
        self._table = TableWidget(self)
        # 操作列文字链接委托（2026-09-19）：单元格内自绘「编辑/删除」+ 矩形命中，
        # 取代原 cellWidget（50 行 × 1 容器 + 2 按钮 = 150 个 QWidget）。必须装为
        # 视图级委托：qfluentwidgets 的 hover/selected 行状态是视图推给视图级委托
        # 实例的，用 setItemDelegateForColumn 会丢整行高亮。
        install_ops_links(self._table, self._on_ops_link)
        # 性能（2026-08-26）：默认关闭 qfluentwidgets 平滑滚动动画——滚轮触发
        # 动画引擎逐帧 moveScrollBar，50 行 + 行内控件逐帧重绘导致滚动卡顿；
        # NO_SMOOTH 走原生滚动（压测 20 步 410ms → 7ms）。按 本面板覆盖→全局 生效，
        # 设置页开关可即时切换（见 _apply_smooth_mode）。
        self._apply_smooth_mode()
        self._table.setColumnCount(len(TABLE_COLUMNS))
        self._table.setHorizontalHeaderLabels([c[1] for c in TABLE_COLUMNS])
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(36)
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(False)
        self._table.doubleClicked.connect(lambda _idx: self._on_edit())
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for i, (_k, _h, w) in enumerate(TABLE_COLUMNS):
            self._table.setColumnWidth(i, w)
        root.addWidget(self._table, 1)

        # --- 分页 ---
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        self._lbl_stats = CaptionLabel("", self)
        bottom.addWidget(self._lbl_stats)
        bottom.addStretch(1)
        self._btn_prev = ToolButton(FluentIcon.LEFT_ARROW, self)
        self._btn_prev.setToolTip("上一页")
        self._btn_prev.clicked.connect(lambda _=False: self._step_page(-1))
        bottom.addWidget(self._btn_prev)
        self._lbl_page = CaptionLabel("1/1", self)
        bottom.addWidget(self._lbl_page)
        self._btn_next = ToolButton(FluentIcon.RIGHT_ARROW, self)
        self._btn_next.setToolTip("下一页")
        self._btn_next.clicked.connect(lambda _=False: self._step_page(1))
        bottom.addWidget(self._btn_next)
        root.addLayout(bottom)

    def _apply_smooth_mode(self):
        """按当前生效的平滑滚动设置（本面板覆盖→全局）刷新表格滚动模式（即时）"""
        apply_table_smooth_mode(self._table, panel=self._PERF_PANEL_KEY)

    # ---------- 指标卡 ----------

    def _make_stats_card(self, layout, title) -> tuple:
        card = CardWidget(self)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        lbl = CaptionLabel(title, card)
        num = QLabel("0", card)
        num.setStyleSheet(
            "font-size: 24px; font-weight: 500; background: transparent;")
        lay.addWidget(lbl)
        lay.addWidget(num)
        layout.addWidget(card, 1)
        return (card, lbl, num)

    def _on_category_card_clicked(self, category: str):
        """点击分类指标卡：联动分类筛选（需求5）

        与售后「未解决」卡同范式：当前已是该分类则恢复「全部分类」，
        否则切到该分类。走 _cat_combo.setCurrentIndex 触发
        _on_cat_changed → 类别候选联动 → _on_filter_changed 重查。
        """
        idx = self._cat_combo.findText(category)
        if idx < 0:
            return
        if self._cat_combo.currentIndex() == idx:
            self._cat_combo.setCurrentIndex(0)  # 恢复「全部分类」
        else:
            self._cat_combo.setCurrentIndex(idx)

    def _on_total_card_clicked(self):
        """点击「总记录」卡：一键清空全部筛选（需求5）

        日期档回「今天」（面板默认口径）、分类/类别/署名回「全部」、
        关键词清空、复现回「全部」。逐项 blockSignals 后统一重查一次，
        避免每改一个控件就发一条 SQL。
        """
        widgets = (self._date_mode_combo, self._cat_combo,
                   self._kind_combo, self._signer_combo, self._search_edit)
        for w in widgets:
            w.blockSignals(True)
        try:
            self._date_mode_combo.setCurrentIndex(1)   # 今天
            self._date_to.setVisible(False)            # 自定义档才显示结束日
            self._cat_combo.setCurrentIndex(0)         # 全部分类
            self._kind_combo.clear()
            self._kind_combo.addItem("全部类别")
            self._kind_combo.setCurrentIndex(0)
            self._signer_combo.setCurrentIndex(0)      # 全部署名
            self._search_edit.clear()
        finally:
            for w in widgets:
                w.blockSignals(False)
        self._repro_seg.setValue("")                   # 复现：全部
        # 类别候选随「全部分类」重建（内部会触发 _on_filter_changed 重查）
        self._load_kind_options("")
        show_info_bar("已清空全部筛选", "success", title="筛选重置",
                      parent=self, duration=2000)

    # ---------- 数据源指示 ----------

    def _update_source_label(self):
        from database import backend
        if not backend.is_mysql_test_mode():
            text, color = "数据源: 本地 SQLite", SEMANTIC["neutral"]
        elif backend.get_state() == backend.STATE_ONLINE:
            text, color = "数据源: MySQL", SEMANTIC["success"]
        else:
            text, color = ("MySQL 不可用，降级兜底 数据源: 本地 SQLite，",
                           SEMANTIC["warning"])
        self._lbl_source.setText(text)
        self._lbl_source.setStyleSheet(f"color: {color};")
        self._lbl_source.setToolTip(
            "关闭 MySQL 后自动读写本地 SQLite；"
            "MySQL 恢复可用时会自动切回并合并兜底增量")

    # ---------- 筛选条件 ----------

    def _current_filters(self) -> dict:
        """界面控件值 → 查询参数；归一规则全在 query_state，视图只搬运。"""
        date_from, date_to = self._date_range()
        return current_filters(
            self._cat_combo.currentText(),
            self._kind_combo.currentText(),
            self._signer_combo.currentText(),
            self._search_edit.text(),
            date_from, date_to,
            self._repro_seg.value())  # 全部→"" / 否 / 是

    def _on_date_mode_changed(self):
        """日期模式切换：仅「自定义」显示结束日期控件，随后重查"""
        self._date_to.setVisible(
            self._date_mode_combo.currentText() == "自定义")
        self._on_filter_changed()

    def _step_base_date(self, delta_days: int):
        """基准日期步进（需求1：与主面板 date_prev/date_next 同交互）

        负数前移、正数后移；改基准日期后快捷档（今天/近7天/近30天/本月）
        的范围随之整体平移，dateChanged 会触发 _on_filter_changed 重查。
        """
        self._date_from.setDate(self._date_from.date.addDays(delta_days))

    def _date_range(self) -> tuple:
        """按日期模式与基准日期算 (date_from, date_to)；空串=不过滤。

        具体口径（近 7 天含基准日、本月月末、自定义倒置交换）都在
        query_state.date_range 里，本方法只把 QDate 翻成 datetime.date。
        """
        return date_range(
            self._date_mode_combo.currentText(),
            self._date_from.date.toPython(),  # QDate 属性 → datetime.date
            self._date_to.date.toPython())

    def _on_search_input(self, _text):
        self._search_timer.start()

    def _on_filter_changed(self):
        self._page_no = 1
        self._load()

    def _on_cat_changed(self):
        """分类筛选变化：类别下拉联动候选后重查"""
        cat = self._cat_combo.currentText().strip()
        if cat == "全部分类":
            cat = ""
        self._load_kind_options(cat)

    def _load_kind_options(self, category: str):
        """类别候选：查询在 controller；空分类会同步发 candidates_ready。"""
        self._controller.load_kind_options(category)

    def _on_candidates_ready(self, cands, category):
        """填类别下拉；旧分类的迟到候选由 controller 代际号 + 这里双保险丢弃。"""
        current = self._cat_combo.currentText().strip()
        if current == "全部分类":
            current = ""
        if str(category or "").strip() != current:
            return
        cur = self._kind_combo.currentText().strip()
        self._kind_combo.blockSignals(True)
        self._kind_combo.clear()
        self._kind_combo.addItem("全部类别")
        cands = list(cands or ())
        self._kind_combo.addItems(cands)
        if cur:
            self._kind_combo.setText(cur)  # EditableComboBox 用 setText 回填
        else:
            self._kind_combo.setCurrentIndex(0)
        self._kind_combo.blockSignals(False)
        # 手输补全提示（EditableComboBox 为 LineEdit 子类，需显式挂补全器）
        try:
            from PySide6.QtWidgets import QCompleter
            self._kind_combo.setCompleter(
                QCompleter(["全部类别"] + cands, self._kind_combo))
        except Exception:
            pass
        self._on_filter_changed()

    # ---------- 首次显示自动加载 ----------

    def showEvent(self, event):
        """首次显示自动加载（需求18：打开「记录与统计」页即查询，无需先点筛选）

        参照售后面板 RecordsPage 的 showEvent 范式；用 _loaded_once 标志保证
        只在首次显示时加载一次（此后提交/手动刷新/筛选均显式触发 _load）。
        需求4：切回本页时若开了自动刷新，重启定时器并立刻补一次指纹比对——
        隐藏期间（在填写页/其他面板）他人的新记录即刻呈现。
        """
        super().showEvent(event)
        if not getattr(self, "_loaded_once", False):
            self._loaded_once = True
            self._load()
        if ledger_db.auto_refresh_enabled():
            self._sync_auto_timer()
            self._auto_refresh_tick()

    def hideEvent(self, event):
        super().hideEvent(event)
        # 页面隐藏（切页/关窗）：停轮询省查询；开关与指纹基线保留，
        # 下次 showEvent 恢复（需求4，售后同款）
        self._auto_timer.stop()

    # ---------- 自动刷新（需求4：他人填写免手动同步，售后同款范式） ----------

    def _sync_auto_timer(self):
        """按当前配置启停轮询定时器（开关/间隔变更后调用，也用于构造时初始化）"""
        try:
            enabled = ledger_db.auto_refresh_enabled()
            interval = ledger_db.auto_refresh_interval()
        except Exception:
            enabled, interval = False, 30
        self._auto_timer.setInterval(interval * 1000)
        # 启停同时受页面可见性约束（show/hideEvent 联动）：隐藏的页面不轮询
        should_run = enabled and self.isVisible()
        if should_run:
            if not self._auto_timer.isActive():
                self._auto_timer.start()
        elif self._auto_timer.isActive():
            self._auto_timer.stop()

    def _auto_refresh_tick(self):
        """定时器回调：前置守卫后起一条轻量指纹查询。

        跳过条件：有模态弹窗在前台（编辑/新增/确认），或本页正在查询/写库
        ——不打扰用户操作，等下个周期。worker 归属判定在 controller。
        """
        from PySide6.QtWidgets import QApplication
        if QApplication.activeModalWidget() is not None:
            return
        if self._controller.is_running(
                "query_worker", "mutate_worker", "del_worker",
                "auto_worker"):
            return
        self._controller.check_fingerprint()

    def _on_fingerprint(self, fp):
        """指纹回来：与上次快照比对，一致则完全不动表格，变化才静默重查"""
        if fp is None:
            return
        fp = tuple(fp)
        if self._last_fingerprint is None:
            self._last_fingerprint = fp  # 建立基线（首帧/重开后）不刷新
            return
        if fp == self._last_fingerprint:
            return  # 无远端变化：保留滚动位置，零重绘
        self._last_fingerprint = fp
        # 先捕获阅读位置、登记还原意图，再重查；_on_loaded 完成后还原。
        # 顺序不能反：_load 的 worker 虽异步回来，但登记晚于请求会让
        # 「请求已发出、结果先到」的极端时序丢掉滚动位置。
        self._preserve_view = self._capture_view()
        self._load()

    def _capture_view(self) -> int:
        """捕获当前阅读位置：表格垂直滚动偏移"""
        sb = self._table.verticalScrollBar()
        return sb.value() if sb is not None else 0

    def _restore_view(self, offset: int):
        """静默重查后还原滚动位置（数据变了也尽量让用户停在原地）

        滚动还原延后一个事件循环：setRowCount 后 Qt 尚未重算 scrollbar
        maximum，同步 setValue 会被陈旧上限夹小（售后同款坑）。
        """
        def _apply_scroll():
            try:
                sb = self._table.verticalScrollBar()
                if sb is not None:
                    sb.setValue(min(int(offset or 0), sb.maximum()))
            except Exception:
                pass
        QTimer.singleShot(0, _apply_scroll)

    # ---------- 加载 ----------

    def _on_open_stats_chart(self):
        """打开 pygwalker 自助分析窗口。"""
        self._btn_stats_chart.setEnabled(False)
        self._stats_opener.open_analysis(self._current_filters())

    def _on_stats_finished(self, ok, msg):
        self._btn_stats_chart.setEnabled(True)
        show_info_bar(msg, message_type="success" if ok else "error",
                      parent=self)

    def _on_refresh(self):
        """手动刷新：重查数据 + 署名选项 + 指标卡，完成弹 infobar"""
        self._manual_refresh = True
        self._load()

    def refresh_async(self):
        """其他页面提交后静默刷新（不弹 infobar）"""
        self._load()

    def _load(self):
        """异步分页查询 + 指标卡 + 署名选项刷新（worker 全在 controller）"""
        self._update_source_label()
        self._load_signers()
        filters = self._current_filters()
        self._seq += 1
        self._controller.query(filters, self._page_no, self._page_size,
                               self._seq)
        # 指标卡（需求5：与列表同口径，按日期范围计数）
        self._stats_seq += 1
        self._controller.count_by_category(
            filters["date_from"], filters["date_to"], self._stats_seq)

    def _load_signers(self):
        """刷新署名候选：查询在 controller，结果由 _on_signers_ready 填控件。"""
        self._controller.load_signers()

    def _on_signers_ready(self, signers):
        """填署名下拉并保留当前选中项；失败静默（controller 不发错误）。"""
        cur = self._signer_combo.currentText().strip()
        self._signer_combo.blockSignals(True)
        self._signer_combo.clear()
        self._signer_combo.addItem("全部署名")
        self._signer_combo.addItems(
            [s for s in (signers or []) if s and s != "全部署名"])
        if cur:
            idx = self._signer_combo.findText(cur)
            self._signer_combo.setCurrentIndex(max(0, idx))
        self._signer_combo.blockSignals(False)

    def _on_loaded(self, result, seq=None):
        """分页结果回填；seq 不是最新一帧就直接丢弃（防止旧筛选覆盖新筛选）。"""
        if seq is not None and seq != self._seq:
            return
        self._update_source_label()
        if not result:
            return
        self._total, self._rows = result
        self._populate()
        self._page_no = clamp_page(self._page_no, self._total, self._page_size)
        self._lbl_page.setText(
            page_text(self._page_no, self._total, self._page_size))
        self._lbl_stats.setText(total_text(self._total))
        self._btn_prev.setEnabled(self._page_no > 1)
        self._btn_next.setEnabled(
            self._page_no < pages_of(self._total, self._page_size))
        if self._manual_refresh:
            self._manual_refresh = False
            show_info_bar(f"已刷新，共 {self._total} 条记录", "success",
                          title="刷新", parent=self, duration=2500)
        # 自动刷新的静默重查：还原触发前的滚动位置（用户无感，需求4）
        pv = self._preserve_view
        if pv is not None:
            self._preserve_view = None
            self._restore_view(pv)

    def _on_load_error(self, msg, seq=None):
        if seq is not None and seq != self._seq:
            return
        if self._manual_refresh:
            self._manual_refresh = False
            show_info_bar(msg, "error", title="刷新失败",
                          parent=self, duration=4000)

    def _on_stats_loaded(self, counts, seq=None):
        if seq is not None and seq != self._stats_seq:
            return
        for key, lbl in self._cards.items():
            lbl.setText(str(counts.get(key, 0)))

    # ---------- 表格填充 ----------

    def _populate(self):
        """按 table_adapter 的呈现描述重建当前页表格（不再手写列值顺序）。

        列序严格跟随 TABLE_COLUMNS；呈现规则（截断/tooltip/分类色/链接）
        在 table_adapter，视图只负责把 CellSpec 落到 QTableWidgetItem。
        """
        table = self._table
        table.setUpdatesEnabled(False)
        table.blockSignals(True)
        try:
            table.clearContents()
            table.setRowCount(len(self._rows))
            for r, row in enumerate(self._rows):
                specs = data_cells(row)
                for c, (key, _header, _width) in enumerate(TABLE_COLUMNS):
                    spec = specs[c]
                    it = QTableWidgetItem(spec.text)
                    if key == "created_at":
                        it.setTextAlignment(
                            Qt.AlignmentFlag.AlignVCenter
                            | Qt.AlignmentFlag.AlignLeft)
                    if spec.font == FONT_BOLD:
                        it.setFont(self._bold_font)
                    if spec.fg is not None:
                        it.setForeground(spec.fg)
                    if spec.bg is not None:
                        it.setBackground(spec.bg)
                    if spec.align_center:
                        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    if spec.tooltip:
                        it.setToolTip(spec.tooltip)
                    if spec.links is not None:
                        it.setData(LINKS_ROLE, spec.links)
                    table.setItem(r, c, it)
        finally:
            table.setUpdatesEnabled(True)
            table.blockSignals(False)

    @staticmethod
    def _short_dt(val) -> str:
        """2026-08-22 21:14:33 → 08-22 21:14（兼容旧调用，逻辑在 query_state）"""
        return short_dt(val)

    def _on_ops_link(self, row: int, _col: int, action: str):
        """操作列文字链接点击分发（core.ops_link_delegate 的 editorEvent 回调）

        行号就是表格可视行号（_populate 按 _rows 顺序填充，本面板无表头排序），
        取到 rec 后走与双击/右键同一入口。
        """
        if not (0 <= row < len(self._rows)):
            return
        rec = self._rows[row]
        if action == "edit":
            self._on_edit(rec)
        elif action == "delete":
            self._on_delete(rec)

    # ---------- 右键菜单 ----------

    def _show_context_menu(self, pos):
        idx = self._table.indexAt(pos)
        if not idx.isValid():
            return
        self._table.selectRow(idx.row())
        menu = RoundMenu(parent=self._table)
        rec = self._rows[idx.row()] if idx.row() < len(self._rows) else None
        if rec is None:
            return
        act_edit = Action(FluentIcon.EDIT, "编辑", self._table)
        act_edit.triggered.connect(
            lambda _=False, r=rec: self._on_edit(r))
        menu.addAction(act_edit)
        act_del = Action(FluentIcon.DELETE, "删除", self._table)
        act_del.triggered.connect(
            lambda _=False, r=rec: self._on_delete(r))
        menu.addAction(act_del)
        menu.exec_(self._table.viewport().mapToGlobal(pos),
                   aniType=_popup_ani_type())

    # ---------- 编辑 / 删除 ----------

    def _on_add(self):
        """工具栏「新增」：售后同款连续录入弹窗（需求3）

        continuous=True：保存成功后不关窗、清表单等待下一条，
        每条成功经 on_saved 回调实时刷新列表；点「完成」关窗。
        """
        dlg = EditLedgerDialog({}, self, continuous=True,
                               on_saved=self._load)
        dlg.exec()

    def _on_edit(self, row=None):
        """编辑：row 为记录 dict（行内链接/右键菜单）或 None（取当前行）

        编辑模式保持单条语义：validate 通过后弹窗 accept，collected 交给
        controller 异步落库；成功/失败由 mutation_done/failed 回主线程提示。
        """
        if row is None:
            idx = self._table.currentRow()
            if idx < 0 or idx >= len(self._rows):
                return
            row = self._rows[idx]
        dlg = EditLedgerDialog(dict(row), self)
        if dlg.exec() and getattr(dlg, "collected", None):
            self._controller.update_record(int(row["id"]), dlg.collected)

    def _on_delete(self, row):
        box = MessageBox("删除确认",
                         f"确定删除这条跑视频记录吗？\n"
                         f"{row.get('category')} / {row.get('kind')} / "
                         f"{row.get('room_name')}", self)
        if not box.exec():
            return
        self._controller.delete_record(int(row["id"]))

    # ---------- 写操作 / 导出的结果回调 ----------

    def _on_mutation_done(self, tag, _payload=None):
        """写库成功：编辑/删除共用一条静默重查链，按 tag 给不同文案。"""
        if tag == "edit":
            self._load()
            show_info_bar("已保存修改", "success", title="编辑",
                          parent=self, duration=2500)
        elif tag == "delete":
            self._load()
            show_info_bar("已删除", "success", title="删除",
                          parent=self, duration=2500)

    def _on_mutation_failed(self, tag, msg):
        title = "保存失败" if tag == "edit" else "删除失败"
        show_info_bar(msg, "error", title=title, parent=self, duration=4000)

    # ---------- 署名统计 / 导出 ----------

    def _on_show_stats(self):
        """署名统计：查询在 controller，结果回来再开弹窗。"""
        self._controller.signer_stats()

    def _on_signer_stats_ready(self, stats):
        SignerStatsDialog(stats or [], self).exec()

    def _on_signer_stats_failed(self, msg):
        show_info_bar(msg, "error", title="统计失败",
                      parent=self, duration=4000)

    def _on_export(self):
        path, _sel = QFileDialog.getSaveFileName(
            self, "导出跑视频记录", "跑视频_export.xlsx", "Excel 文件 (*.xlsx)")
        if path:
            self._controller.export_xlsx(path)

    def _on_export_done(self, count):
        show_info_bar(f"已导出 {count} 条", "success",
                      title="导出成功", parent=self, duration=3000)

    def _on_export_failed(self, msg):
        show_info_bar(msg, "error", title="导出失败",
                      parent=self, duration=4000)

    # ---------- 分页 ----------

    def _step_page(self, delta: int):
        new_page = self._page_no + delta
        if 1 <= new_page <= pages_of(self._total, self._page_size):
            self._page_no = new_page
            self._load()
