# -*- coding: utf-8 -*-
"""跑视频面板：记录与统计页（指标卡 + 日期/分类/类别/署名筛选 + 分页 + 编辑/删除 + 统计/导出）

需求5（按天统计）：工具栏新增日期筛选区（模式下拉 + 起止日期控件），
指标卡与列表同口径联动；实现参照售后面板周期筛选范式。
"""
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QTimer, QDate
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QHeaderView, QAbstractItemView, QFileDialog,
                               QTableWidgetItem)

from qfluentwidgets import (TableWidget, SearchLineEdit, PushButton,
                            ToolButton, FluentIcon, TitleLabel, CaptionLabel,
                            BodyLabel, CardWidget, MessageBox, MessageBoxBase,
                            ComboBox, EditableComboBox,
                            CalendarPicker, RoundMenu, Action)

from core.design_tokens import SEMANTIC
from core.flow_widgets import FlowToolbarScrollArea
from core.ops_link_delegate import LINKS_ROLE, install_ops_links
from core.perf import apply_table_smooth_mode
from core.utils import show_info_bar
from database import ledger_db
from workers.aftersale_worker import AftersaleDBWorker

from windows.run_video.common import *  # noqa: F401,F403
from windows.run_video.form import LedgerForm
from windows.stat_charts import StatsOpener, _ledger_options


class EditLedgerDialog(MessageBoxBase):
    """跑视频编辑/新增弹窗：复用 LedgerForm（需求3：售后面板同款 UI）

    continuous=True（新增模式）= 连续录入（与售后 EditRecordDialog 同逻辑）：
    保存成功后**不关窗**，记住本次视频日期、清空表单等待录入下一条，
    点「完成」才关窗；每条成功通过 on_saved 回调通知调用方刷新列表。
    编辑模式保持原语义：保存即关窗。
    """

    def __init__(self, record: dict, parent=None, continuous: bool = False,
                 on_saved=None):
        super().__init__(parent)
        self._record = record
        self._is_new = not record.get("id")
        # 连续录入仅用于新增（编辑保持单条语义）
        self._continuous = bool(continuous) and self._is_new
        self._busy = False          # 异步落库在途，防重复提交
        self._validated_ok = False  # 最近一次 validate() 结果（供 _on_yes 判定）
        self._on_saved_cb = on_saved
        self._save_worker = None
        title = "新增跑视频记录" if self._is_new else "编辑跑视频记录"
        self.setWindowTitle(title)
        self.form = LedgerForm(self)
        self.form.set_values(record)
        self.viewLayout.addWidget(self.form)
        # MessageBoxBase 无系统标题栏，顶部自建 TitleLabel 标明用途（售后同款）
        ttl = TitleLabel(title, self)
        self.viewLayout.insertWidget(0, ttl)
        self.yesButton.setText("保存并继续" if self._continuous else "保存")
        self.cancelButton.setText("完成" if self._continuous else "取消")
        self.yesButton.clicked.connect(self._on_yes)
        # 弹窗宽度：表单控件较宽（售后 EditRecordDialog 同口径）
        self.widget.setMinimumWidth(560)

    def _on_yes(self):
        """收集表单值 / 连续录入落库。

        必填拦截在 validate()（qfw 基类 accept 前调用）：基类槽序为先跑
        validate() → 通过才 accept → 再轮到本槽，因此走到这里时必填一定
        已填齐；未通过时弹窗保持打开、不丢输入。

        连续录入模式：validate() 恒 False → 基类永不 accept（弹窗不关），
        校验通过时由本槽异步落库，成功后清表单等待下一条。
        """
        if not self._validated_ok:
            return
        if self._continuous:
            self._submit_continue()
            return
        self.collected = self.form.collect()

    def _submit_continue(self):
        """连续录入：异步落库当前表单（防重复提交，按钮在途禁用）"""
        if self._busy:
            return
        self._busy = True
        self.yesButton.setEnabled(False)
        record = self.form.collect()
        self._save_worker = AftersaleDBWorker(ledger_db.insert_record, record)
        self._save_worker.result_ready.connect(
            lambda rid, occ=record.get("occurred_at"): self._on_saved(rid, occ))
        self._save_worker.error.connect(self._on_save_error)
        self._save_worker.start()

    def _on_saved(self, rec_id, occurred_at=None):
        """单条落库成功：记住视频日期 → 清表单 → 提示可继续录入 →
        通知调用方刷新列表。弹窗保持打开（连续录入语义）。"""
        self._busy = False
        self.yesButton.setEnabled(True)
        if occurred_at:
            ledger_db.save_last_occurred(str(occurred_at))
        self.form.clear_form()
        show_info_bar(f"已新增跑视频记录（编号 {rec_id}），可继续录入",
                      "success", title="连续录入",
                      parent=self, duration=2500)
        if self._on_saved_cb is not None:
            try:
                self._on_saved_cb()
            except Exception:
                pass

    def _on_save_error(self, msg):
        self._busy = False
        self.yesButton.setEnabled(True)
        show_info_bar(msg, "error", title="保存失败",
                      parent=self, duration=4000)

    def validate(self) -> bool:
        """必填校验：不通过返回 False → qfw 基类不 accept、弹窗不关闭。

        连续录入模式：校验通过也返回 False（基类不 accept、弹窗保持打开），
        落库改由 _on_yes 异步执行；编辑模式保持原行为（通过→accept 关窗，
        收集结果放 self.collected 由调用方落库）。
        """
        missing = self.form.validate()
        if missing:
            self._validated_ok = False
            show_info_bar(f"请先填写必填项: {'、'.join(missing)}", "warning",
                          title="无法保存", parent=self, duration=3000)
            first_error = getattr(self.form, "first_error", None)
            if first_error is not None:
                first_error.setFocus()
            return False
        self._validated_ok = True
        return not self._continuous

    def exec(self):
        self.collected = None
        self._validated_ok = False
        return super().exec()


class SignerStatsDialog(MessageBoxBase):
    """署名统计弹窗：按署名汇总四分类计数（模板「计数」sheet 的电子版）"""

    def __init__(self, stats: list, parent=None):
        super().__init__(parent)
        # MessageBoxBase 无 titleLabel/contentLabel（那是 MessageBox 子类的属性，
        # 见 sftp_window 注释：qfluentwidgets 1.11+ 已移除），与其他子类一致自建标题
        self.titleLabel = TitleLabel("署名统计", self)
        self.contentLabel = CaptionLabel(
            "每人 问题/未复现/精度/使用 计数与总和"
            "（与在线模板「计数」sheet 同口径）", self)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.contentLabel)
        table = TableWidget(self)
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels(
            ["署名", "问题", "未复现", "精度", "使用", "总和"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        table.setRowCount(max(1, len(stats)))
        for r, s in enumerate(stats):
            vals = [s.get("signer", ""),
                    s.get("问题", 0), s.get("未复现", 0),
                    s.get("精度", 0), s.get("使用", 0), s.get("total", 0)]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table.setItem(r, c, it)
        table.setFixedHeight(260)
        self.viewLayout.addWidget(table)
        self.yesButton.setText("关闭")
        self.cancelButton.hide()
        self.widget.setMinimumWidth(560)


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
        self._worker = None
        self._stats_worker = None
        self._export_worker = None
        self._del_worker = None
        self._mutate_worker = None  # 编辑保存独立槽，不与查询 worker 抢占
        self._manual_refresh = False  # 手动刷新标志：完成/失败时弹 infobar 反馈
        # 自动刷新（需求4，仿售后面板范式）：轮询轻量指纹，仅在数据真的
        # 变化时静默重查并保持阅读位置。独立查询槽 _auto_worker（以 _worker
        # 结尾，closeEvent 的 detach 扫描自动覆盖）。
        self._auto_worker = None
        self._auto_timer = QTimer(self)
        self._auto_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._auto_timer.timeout.connect(self._auto_refresh_tick)
        self._last_fingerprint = None   # 上次全表指纹快照（None=需重建基线）
        self._preserve_view = None      # 静默重查待恢复的滚动偏移
        self._init_ui()
        self._sync_auto_timer()
        # pygwalker 统计图表（独立浏览器窗口，工具栏「统计图表」按钮触发）
        self._stats_opener = StatsOpener(_ledger_options, "run_video", self)
        self._stats_opener.finished.connect(self._on_stats_finished)

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
        cat = self._cat_combo.currentText().strip()
        kind = self._kind_combo.currentText().strip()
        signer = self._signer_combo.currentText().strip()
        date_from, date_to = self._date_range()
        return {
            "category": "" if cat == "全部分类" else cat,
            "kind": "" if kind == "全部类别" else kind,
            "signer": "" if signer == "全部署名" else signer,
            "keyword": self._search_edit.text().strip(),
            "date_from": date_from,
            "date_to": date_to,
            "repro": self._repro_seg.value(),  # 全部→"" / 否 / 是
        }

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
        """按日期模式与基准日期计算 (date_from, date_to)；空串表示不过滤

        需求2 重构：默认「今天」；基准日期可翻看昨天/前天等历史日期。
        - 全部：不过滤
        - 今天：基准日期当天
        - 近7天：基准日期前推 6 天（共 7 天，含基准日）
        - 近30天：基准日期前推 29 天（共 30 天，含基准日）
        - 本月：基准日期所在自然月（1 号 ~ 月末）
        - 自定义：起止日期控件（倒置时自动交换）
        """
        mode = self._date_mode_combo.currentText()
        if mode == "全部":
            return "", ""
        d = self._date_from.date.toPython()  # QDate 属性 → datetime.date
        if mode == "自定义":
            d2 = self._date_to.date.toPython()  # QDate 属性 → datetime.date
            if d2 < d:
                d, d2 = d2, d
            return d.isoformat(), d2.isoformat()
        if mode == "近7天":
            return (d - timedelta(days=6)).isoformat(), d.isoformat()
        if mode == "近30天":
            return (d - timedelta(days=29)).isoformat(), d.isoformat()
        if mode == "本月":
            import calendar
            last = calendar.monthrange(d.year, d.month)[1]
            return d.replace(day=1).isoformat(), d.replace(day=last).isoformat()
        return d.isoformat(), d.isoformat()  # 今天

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
        """异步拉取类别筛选候选（全部类别 + 该分类候选）"""
        def _fill(cands):
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

        if not category:
            cands = []
            seen = set()
            for v in ledger_db.KIND_CANDIDATES.values():
                for k in v:
                    if k not in seen:
                        seen.add(k)
                        cands.append(k)
            _fill(cands)
            return
        self._stats_worker = AftersaleDBWorker(
            ledger_db.get_kind_candidates, category)
        self._stats_worker.result_ready.connect(_fill)
        self._stats_worker.error.connect(
            lambda _m: _fill(ledger_db.KIND_CANDIDATES.get(category, ())))
        self._stats_worker.start()

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
        ——不打扰用户操作，等下个周期。
        """
        from PySide6.QtWidgets import QApplication
        if QApplication.activeModalWidget() is not None:
            return
        for w in (self._auto_worker, self._worker, self._mutate_worker,
                  self._del_worker):
            if w is not None and w.isRunning():
                return
        self._auto_worker = AftersaleDBWorker(ledger_db.change_fingerprint)
        self._auto_worker.result_ready.connect(self._on_fingerprint)
        self._auto_worker.error.connect(lambda _m: None)
        self._auto_worker.start()

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
        # 先捕获阅读位置再重查；_on_loaded 完成后还原
        offset = self._capture_view()
        self._load()
        self._preserve_view = offset

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
        """异步分页查询 + 指标卡 + 署名选项刷新"""
        if self._worker and self._worker.isRunning():
            self._worker.requestInterruption()
        self._update_source_label()
        self._load_signers()
        filters = self._current_filters()
        self._worker = AftersaleDBWorker(
            ledger_db.query_page, self._page_no, self._page_size,
            filters["keyword"], filters["category"],
            filters["kind"], filters["signer"],
            filters["date_from"], filters["date_to"],
            filters["repro"])
        self._worker.result_ready.connect(self._on_loaded)
        self._worker.error.connect(self._on_load_error)
        self._worker.start()
        # 指标卡（需求5：与列表同口径，按日期范围计数）
        self._stats_worker = AftersaleDBWorker(
            self._count_by_category, filters["date_from"], filters["date_to"])
        self._stats_worker.result_ready.connect(self._on_stats_loaded)
        self._stats_worker.error.connect(lambda _m: None)
        self._stats_worker.start()

    @staticmethod
    def _count_by_category(date_from: str = "", date_to: str = "") -> dict:
        """分类计数（指标卡数据源）；日期范围按视频日期 occurred_at 过滤
        （需求7；COALESCE 兼容旧库 occurred_at 为空回退 created_at，与
        ledger_db._build_where 同口径，保证指标卡与列表一致）"""
        from database import table_db
        conn = table_db.get_conn()
        where, params = [], []
        if date_from:
            where.append("substr(COALESCE(NULLIF(occurred_at, ''), created_at),"
                         " 1, 10) >= ?")
            params.append(str(date_from).strip())
        if date_to:
            where.append("substr(COALESCE(NULLIF(occurred_at, ''), created_at),"
                         " 1, 10) <= ?")
            params.append(str(date_to).strip())
        sql = "SELECT category, COUNT(*) FROM ledger_records"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY category"
        counts = {"total": 0}
        for c in ledger_db.CATEGORIES:
            counts[c] = 0
        for cat, n in conn.execute(sql, params).fetchall():
            counts[str(cat or "")] = int(n or 0)
            counts["total"] += int(n or 0)
        return counts

    def _load_signers(self):
        """异步刷新署名筛选候选（全部 + 库中署名）"""
        def _fill(signers):
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
        self._stats_worker = AftersaleDBWorker(self._fetch_signers)
        self._stats_worker.result_ready.connect(_fill)
        self._stats_worker.error.connect(lambda _m: None)
        self._stats_worker.start()

    @staticmethod
    def _fetch_signers() -> list:
        from database import table_db
        conn = table_db.get_conn()
        return [str(r[0] or "").strip() for r in conn.execute(
            "SELECT DISTINCT signer FROM ledger_records "
            "WHERE signer != '' ORDER BY signer").fetchall()]

    def _on_loaded(self, result):
        self._update_source_label()
        if not result:
            return
        self._total, self._rows = result
        self._populate()
        pages = max(1, (self._total + self._page_size - 1) // self._page_size)
        self._page_no = min(self._page_no, pages)
        self._lbl_page.setText(f"{self._page_no}/{pages}")
        self._lbl_stats.setText(f"共 {self._total} 条")
        self._btn_prev.setEnabled(self._page_no > 1)
        self._btn_next.setEnabled(self._page_no < pages)
        if self._manual_refresh:
            self._manual_refresh = False
            show_info_bar(f"已刷新，共 {self._total} 条记录", "success",
                          title="刷新", parent=self, duration=2500)
        # 自动刷新的静默重查：还原触发前的滚动位置（用户无感，需求4）
        pv = self._preserve_view
        if pv is not None:
            self._preserve_view = None
            self._restore_view(pv)

    def _on_load_error(self, msg):
        if self._manual_refresh:
            self._manual_refresh = False
            show_info_bar(msg, "error", title="刷新失败",
                          parent=self, duration=4000)

    def _on_stats_loaded(self, counts):
        for key, lbl in self._cards.items():
            lbl.setText(str(counts.get(key, 0)))

    # ---------- 表格填充 ----------

    def _populate(self):
        self._table.setUpdatesEnabled(False)
        self._table.blockSignals(True)
        try:
            self._table.clearContents()
            self._table.setRowCount(len(self._rows))
            # 性能（2026-08-26）：徽章文本化——分类徽章由 cellWidget 改为文本 item
            # （语义色前景 + 淡色底）；2026-09-19 操作列也改为单元格内文字链接
            # （core.ops_link_delegate 自绘 + 命中），本面板已无 cellWidget，
            # 因此不再预构建按钮 QSS
            # 徽章加粗字体（循环外构造一次：原每徽章 QFont(it.font()) 拷贝；
            # 默认应用字体加粗，渲染不变）
            bold_font = QFont()
            bold_font.setBold(True)
            for r, row in enumerate(self._rows):
                # 描述/复现过长截断 + tooltip 保留完整内容
                desc = str(row.get("description") or "")
                repro = str(row.get("repro") or "")
                short_desc = (desc[:60] + "…") if len(desc) > 60 else desc
                short_repro = (repro[:30] + "…") if len(repro) > 30 else repro
                vals = [
                    RecordsPage._short_dt(row.get("created_at")),
                    row.get("category", ""),
                    row.get("kind", ""),
                    row.get("room_name", ""),
                    row.get("video_name", ""),
                    row.get("frame", ""),
                    short_desc,
                    short_repro,
                    row.get("new_program", ""),
                    row.get("signer", ""),
                ]
                for c, v in enumerate(vals):
                    if c == 1:  # 分类徽章：文本 item + 语义色（不再用 cellWidget）
                        it = QTableWidgetItem(str(v))
                        color = _category_color(str(v))
                        it.setForeground(color)
                        bgc = QColor(color)
                        bgc.setAlpha(26)  # ~10% 透明度，近似徽章淡底
                        it.setBackground(bgc)
                        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        it.setFont(bold_font)
                        self._table.setItem(r, c, it)
                        continue
                    it = QTableWidgetItem(str(v))
                    if c == 0:
                        it.setTextAlignment(
                            Qt.AlignmentFlag.AlignVCenter
                            | Qt.AlignmentFlag.AlignLeft)
                    if c in (6, 7) and str(v) != desc and str(v) != repro:
                        pass  # 无截断不需提示
                    if c == 6 and desc != short_desc:
                        it.setToolTip(desc)
                    if c == 7 and repro != short_repro:
                        it.setToolTip(repro)
                    self._table.setItem(r, c, it)
                # 操作列：空文本 item + 链接清单，委托按矩形自绘并反查命中
                ops = QTableWidgetItem("")
                ops.setData(LINKS_ROLE, _OPS_LINKS)
                self._table.setItem(r, _COL_OPS, ops)
        finally:
            self._table.setUpdatesEnabled(True)
            self._table.blockSignals(False)
        pass

    @staticmethod
    def _short_dt(val) -> str:
        """2026-08-22 21:14:33 → 08-22 21:14（紧凑展示）"""
        s = str(val or "").strip()
        return s[5:16] if len(s) >= 16 else s

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

        编辑模式保持单条语义：validate 通过后弹窗 accept，
        collected 由本方法异步落库（update_record）。
        """
        if row is None:
            idx = self._table.currentRow()
            if idx < 0 or idx >= len(self._rows):
                return
            row = self._rows[idx]
        dlg = EditLedgerDialog(dict(row), self)
        if dlg.exec() and getattr(dlg, "collected", None):
            rec_id = int(row["id"])
            collected = dlg.collected
            self._mutate_worker = AftersaleDBWorker(
                ledger_db.update_record, rec_id, collected)
            self._mutate_worker.result_ready.connect(
                lambda _ok: (self._load(), show_info_bar(
                    "已保存修改", "success", title="编辑",
                    parent=self, duration=2500)))
            self._mutate_worker.error.connect(
                lambda m: show_info_bar(m, "error", title="保存失败",
                                        parent=self, duration=4000))
            self._mutate_worker.start()

    def _on_delete(self, row):
        box = MessageBox("删除确认",
                         f"确定删除这条跑视频记录吗？\n"
                         f"{row.get('category')} / {row.get('kind')} / "
                         f"{row.get('room_name')}", self)
        if not box.exec():
            return
        self._del_worker = AftersaleDBWorker(
            ledger_db.delete_record, int(row["id"]))
        self._del_worker.result_ready.connect(
            lambda _ok: (self._load(), show_info_bar(
                "已删除", "success", title="删除",
                parent=self, duration=2500)))
        self._del_worker.error.connect(
            lambda m: show_info_bar(m, "error", title="删除失败",
                                    parent=self, duration=4000))
        self._del_worker.start()

    # ---------- 署名统计 / 导出 ----------

    def _on_show_stats(self):
        self._stats_worker = AftersaleDBWorker(ledger_db.stats_by_signer)
        self._stats_worker.result_ready.connect(
            lambda stats: SignerStatsDialog(stats or [], self).exec())
        self._stats_worker.error.connect(
            lambda m: show_info_bar(m, "error", title="统计失败",
                                    parent=self, duration=4000))
        self._stats_worker.start()

    def _on_export(self):
        path, _sel = QFileDialog.getSaveFileName(
            self, "导出跑视频记录", "跑视频_export.xlsx", "Excel 文件 (*.xlsx)")
        if not path:
            return
        self._export_worker = AftersaleDBWorker(ledger_db.export_xlsx, path)
        self._export_worker.result_ready.connect(
            lambda n: show_info_bar(
                f"已导出 {n} 条",
                "success", title="导出成功", parent=self, duration=3000))
        self._export_worker.error.connect(
            lambda m: show_info_bar(m, "error", title="导出失败",
                                    parent=self, duration=4000))
        self._export_worker.start()

    # ---------- 分页 ----------

    def _step_page(self, delta: int):
        pages = max(1, (self._total + self._page_size - 1) // self._page_size)
        new_page = self._page_no + delta
        if 1 <= new_page <= pages:
            self._page_no = new_page
            self._load()
