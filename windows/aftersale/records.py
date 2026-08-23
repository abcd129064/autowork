# -*- coding: utf-8 -*-
"""records 模块（从 windows/aftersale_panel.py 拆出，逻辑未改动）"""

import os
from datetime import datetime, timedelta

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QLabel, QListWidget,
    QListWidgetItem, QFileDialog, QComboBox as _QComboBox, QApplication,
    QDialog, QPushButton as _QPushButton, QGridLayout)
from PySide6.QtCore import Qt, QTimer, QThread, QPointF, QDate, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QFont
from qfluentwidgets import (TableWidget, SearchLineEdit, PushButton,
    PrimaryPushButton, ToolButton, FluentIcon, RoundMenu, Action,
    LineEdit, PlainTextEdit, BodyLabel, CaptionLabel, TitleLabel,
    ScrollArea, CardWidget, MessageBox, MessageBoxBase, CheckBox,
    FluentWindow, NavigationItemPosition, MenuAnimationType,
    setCustomStyleSheet, qconfig, isDarkTheme, ZhDatePicker, RadioButton,
    SpinBox, SegmentedWidget, ProgressBar, FlowLayout)

from core.design_tokens import SEMANTIC, lighten, darken
from core.flow_widgets import FlowToolbarScrollArea
from core.perf import is_acrylic_enabled
from core.theme_qss import current_accent_hex
from core.utils import show_info_bar
from database import aftersale_db, table_db
from workers.aftersale_worker import AftersaleDBWorker
from windows.mysql_sync_card import MysqlSyncCard

from windows.aftersale.common import *  # noqa: F401,F403
from windows.aftersale.dialogs import EditRecordDialog, ImportPreviewDialog

# ==================== 板块二：记录与统计页 ====================

class RecordsPage(QWidget):
    """记录与统计页：周期概览指标卡 + 筛选/分页/批量操作/一键解决/编辑/删除/导出/导入

    表格列经过信息整合（球房+地区+桌号合入「位置」，填写人并入填写时间，
    解决人并入响应时间）；发生原因/解决方案/三个是否判定直接成列展示
    （是/否徽章），完整信息保留在行 tooltip；未解决行提供行内
    一键「标记已解决」。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page_no = 1
        self._page_size = 50
        self._total = 0
        self._rows = []
        self._worker = None
        self._export_worker = None
        self._import_worker = None
        self._cycles_loaded = False
        self._batch_worker = None
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 12)
        root.setSpacing(8)

        # --- 页头：标题 + 说明 + 数据源状态（右对齐） ---
        head = QHBoxLayout()
        head.setSpacing(10)
        head_box = QVBoxLayout()
        head_box.setSpacing(1)
        head_box.addWidget(TitleLabel("记录与统计", self))
        head_box.addWidget(CaptionLabel(
            "售后问题从上报到解决的全流程跟踪", self))
        head.addLayout(head_box)
        head.addStretch(1)
        # 数据源指示：MySQL / 本地 SQLite / 降级兜底
        self._lbl_source = CaptionLabel("", self)
        head.addWidget(self._lbl_source, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(head)

        # --- 周期概览指标卡（四张，随筛选口径实时更新） ---
        ov_head = QHBoxLayout()
        ov_head.setSpacing(8)
        self._lbl_overview = BodyLabel("概览", self)
        ov_head.addWidget(self._lbl_overview)
        ov_head.addStretch(1)
        root.addLayout(ov_head)
        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)
        self._card_total = self._make_stats_card(cards_row)
        self._card_unresolved = self._make_stats_card(cards_row)
        self._card_rate = self._make_stats_card(cards_row)
        self._card_initiative = self._make_stats_card(cards_row)
        root.addLayout(cards_row)

        # --- 工具栏（与主界面同范式：库版 FlowLayout 换行 + 滚动容器锁高，
        #     窄宽自动换行严禁重叠，折行超上限上下滚动） ---
        toolbar_scroll = FlowToolbarScrollArea(self)
        toolbar_scroll.setWidgetResizable(True)
        toolbar_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        toolbar_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        toolbar_scroll.setMaximumHeight(132)  # 约 3 行控件，超出滚动
        toolbar_widget = QWidget(self)
        toolbar_scroll.setWidget(toolbar_widget)
        toolbar = FlowLayout(toolbar_widget)
        toolbar.setHorizontalSpacing(6)
        toolbar.setVerticalSpacing(6)
        toolbar.setContentsMargins(2, 4, 2, 4)

        # 周期筛选（默认当前周期；原生 QComboBox 可靠支持 findData/currentData）
        self._cycle_combo = FluentCombo(self)
        self._cycle_combo.setFixedWidth(210)
        self._cycle_combo.currentIndexChanged.connect(
            lambda _i: self._on_filter_changed())
        toolbar.addWidget(self._cycle_combo)

        # 类型筛选
        self._type_combo = FluentCombo(self)
        self._type_combo.addItem("全部类型")
        self._type_combo.addItems(aftersale_db.ISSUE_TYPES)
        self._type_combo.setFixedWidth(130)
        self._type_combo.currentIndexChanged.connect(
            lambda _i: self._on_filter_changed())
        toolbar.addWidget(self._type_combo)

        # 是否解决筛选
        self._resolved_combo = FluentCombo(self)
        self._resolved_combo.addItem("全部状态", userData="")
        self._resolved_combo.addItem("未解决", userData="否")
        self._resolved_combo.addItem("已解决", userData="是")
        self._resolved_combo.setFixedWidth(130)
        self._resolved_combo.currentIndexChanged.connect(
            lambda _i: self._on_filter_changed())
        toolbar.addWidget(CaptionLabel("是否解决：", self))
        toolbar.addWidget(self._resolved_combo)

        # 是否是我们的问题筛选
        self._our_problem_combo = FluentCombo(self)
        self._our_problem_combo.addItem("全部", userData="")
        self._our_problem_combo.addItem("是", userData="是")
        self._our_problem_combo.addItem("否", userData="否")
        self._our_problem_combo.setToolTip("是否是我们的问题")
        self._our_problem_combo.setFixedWidth(130)
        self._our_problem_combo.currentIndexChanged.connect(
            lambda _i: self._on_filter_changed())
        toolbar.addWidget(CaptionLabel("是否是我们问题：", self))
        toolbar.addWidget(self._our_problem_combo)

        # 是否我们主动发起筛选
        self._initiative_combo = FluentCombo(self)
        self._initiative_combo.addItem("全部", userData="")
        self._initiative_combo.addItem("是", userData="是")
        self._initiative_combo.addItem("否", userData="否")
        self._initiative_combo.setToolTip("是否我们主动发起")
        self._initiative_combo.setFixedWidth(130)
        self._initiative_combo.currentIndexChanged.connect(
            lambda _i: self._on_filter_changed())
        toolbar.addWidget(CaptionLabel("是否主动发起：", self))
        toolbar.addWidget(self._initiative_combo)

        # 关键词搜索（防抖）
        self._search_edit = SearchLineEdit(self)
        self._search_edit.setPlaceholderText("搜索球房 / 问题 / 填写人")
        self._search_edit.setFixedWidth(180)
        self._search_edit.textChanged.connect(self._on_search_input)
        toolbar.addWidget(self._search_edit)

        self._btn_import = PushButton(FluentIcon.ADD, "导入 Excel", self)
        self._btn_import.setToolTip("一次性导入 售后问题汇总 xlsx 历史数据")
        self._btn_import.clicked.connect(self._on_import)
        toolbar.addWidget(self._btn_import)

        self._btn_export = PushButton(FluentIcon.DOWNLOAD, "导出 xlsx", self)
        self._btn_export.setToolTip("按当前筛选条件导出 xlsx（按类型分 Sheet + 统计图表）")
        self._btn_export.clicked.connect(self._on_export)
        toolbar.addWidget(self._btn_export)

        self._btn_refresh = PushButton(FluentIcon.SYNC, "刷新", self)
        self._btn_refresh.setToolTip("重新查询数据库")
        self._btn_refresh.clicked.connect(self._on_refresh)
        toolbar.addWidget(self._btn_refresh)

        root.addWidget(toolbar_scroll)

        # 搜索防抖定时器
        self._search_timer = QTimer(self)
        self._search_timer.setInterval(300)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._on_filter_changed)

        # --- 批量操作条（勾选行后出现：批量标记已解决 / 批量删除） ---
        self._batch_bar = self._make_batch_bar()
        self._batch_bar.setVisible(False)
        root.addWidget(self._batch_bar)

        # --- 表格 ---
        self._table = TableWidget(self)
        # 勾选列 + 数据列（列号：勾选=0，数据列 1..N，注意不要少算勾选列）
        self._table.setColumnCount(_COL_CHECK + 1 + len(TABLE_COLUMNS))
        self._table.setHorizontalHeaderLabels(
            [""] + [c[1] for c in TABLE_COLUMNS])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        # 行高放宽到 40：双行单元格（位置/填写时间/响应）需要两行文字空间
        self._table.verticalHeader().setDefaultSectionSize(40)
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(False)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._table.doubleClicked.connect(lambda _idx: self._on_edit())
        self._table.itemChanged.connect(self._on_item_changed)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        # 其他列保持 Interactive（可拖拽调宽）；最后一列（操作）stretch 拉伸填满
        # 剩余宽度，使表格铺满面板右侧不留白；操作列按钮仍靠左（addStretch 在右）。
        header.setStretchLastSection(True)
        self._table.setColumnWidth(_COL_CHECK, 36)
        for i, (_k, _h, w) in enumerate(TABLE_COLUMNS):
            self._table.setColumnWidth(_COL_CHECK + 1 + i, w)
        root.addWidget(self._table, 1)

        # --- 分页 + 状态栏 ---
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        self._lbl_stats = CaptionLabel("", self)
        bottom.addWidget(self._lbl_stats)
        bottom.addStretch(1)
        self._lbl_cycle = CaptionLabel("", self)
        bottom.addWidget(self._lbl_cycle)
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

    # ---------- 概览卡与批量条构造 ----------

    def _make_stats_card(self, layout) -> tuple:
        """单张指标卡：标签 + 大数字 + 辅助说明，返回 (card, 数字label, 说明label)"""
        card = CardWidget(self)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        lbl = CaptionLabel("", card)
        num = QLabel("0", card)
        num.setStyleSheet("font-size: 24px; font-weight: 500; background: transparent;")
        sub = CaptionLabel("", card)
        lay.addWidget(lbl)
        lay.addWidget(num)
        lay.addWidget(sub)
        layout.addWidget(card, 1)
        return (card, lbl, num, sub)

    def _make_batch_bar(self) -> QWidget:
        """批量操作条：勾选行后浮现（全选本页 / 批量标记已解决 / 批量删除 / 取消）"""
        bar = QWidget(self)
        bar.setObjectName("batchBar")
        bar.setStyleSheet(
            "QWidget#batchBar { background: "
            f"{_hex_rgba(SEMANTIC['info'], 20)};"
            f" border: 1px solid {_hex_rgba(SEMANTIC['info'], 90)};"
            " border-radius: 6px; }")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(8)
        self._chk_all = CheckBox("全选本页", bar)
        self._chk_all.stateChanged.connect(self._on_toggle_all)
        lay.addWidget(self._chk_all)
        self._lbl_selected = BodyLabel("已选 0 项", bar)
        lay.addWidget(self._lbl_selected)
        btn_resolve = _row_btn(
            "批量标记已解决", "primary", self._on_batch_resolve, bar)
        lay.addWidget(btn_resolve)
        btn_delete = _row_btn("批量删除", "danger", self._on_batch_delete, bar)
        lay.addWidget(btn_delete)
        btn_clear = _row_btn("取消选择", "ghost", self._on_clear_selection, bar)
        lay.addWidget(btn_clear)
        lay.addStretch(1)
        return bar

    @staticmethod
    def _short_dt(val: str) -> str:
        """2026-08-22 21:14:33 → 08-22 21:14（表格内紧凑展示，完整值放 tooltip）"""
        s = str(val or "").strip()
        return s[5:16] if len(s) >= 16 else s

    # ---------- 筛选条件 ----------

    def _current_filters(self) -> dict:
        """汇集当前筛选条件（周期/类型/状态/是否我们主动发起/是否我们的问题/关键词）"""
        cycle = self._cycle_combo.currentData()
        return {
            "cycle_start": str(cycle or ""),
            "issue_type": (self._type_combo.currentText()
                           if self._type_combo.currentIndex() > 0 else ""),
            "resolved": self._resolved_combo.currentData() or "",
            "is_initiative": self._initiative_combo.currentData() or "",
            "is_our_problem": self._our_problem_combo.currentData() or "",
            "keyword": self._search_edit.text().strip(),
        }

    def _on_search_input(self, _text):
        self._search_timer.start()

    def _on_filter_changed(self):
        self._page_no = 1
        self._load()

    def _update_source_label(self):
        """刷新数据源指示标签（读取时调用，反映最新后端状态）

        - MySQL 开启且可用：绿色「数据源: MySQL」
        - MySQL 未开启：灰色「数据源: 本地 SQLite」
        - MySQL 开启但不可用（降级兜底）：橙色「本地 SQLite（MySQL 不可用，降级兜底）」
        """
        from database import backend
        if not backend.is_mysql_test_mode():
            text, color = "数据源: 本地 SQLite", SEMANTIC["neutral"]
        elif backend.get_state() == backend.STATE_ONLINE:
            text, color = "数据源: MySQL", SEMANTIC["success"]
        else:
            text, color = ("数据源: 本地 SQLite（MySQL 不可用，降级兜底）",
                           SEMANTIC["warning"])
        self._lbl_source.setText(text)
        self._lbl_source.setStyleSheet(f"color: {color};")
        self._lbl_source.setToolTip(
            "关闭 MySQL 后自动读写本地 SQLite；"
            "MySQL 恢复可用时会自动切回并合并兜底增量")

    def _on_refresh(self):
        """手动刷新：重建周期选项并重查（多人协作看到他人新数据）"""
        self._cycles_loaded = False
        self._load_cycles_then_data()

    def refresh_async(self):
        """其他页面提交后静默刷新（不重置周期选择）"""
        self._load()

    def set_keyword(self, kw: str):
        """外部入口：按桌号预筛选（球桌管理右键跳转）"""
        self._search_edit.setText(str(kw or ""))
        # 跨球桌查询时周期放宽为全部，避免当前周期过滤掉历史记录
        idx = self._cycle_combo.findData("")
        if idx >= 0:
            self._cycle_combo.setCurrentIndex(idx)

    # ---------- 数据加载 ----------

    def showEvent(self, event):
        super().showEvent(event)
        if not self._cycles_loaded:
            self._load_cycles_then_data()

    def _load_cycles_then_data(self):
        """先异步拉周期选项填充下拉，再加载数据"""
        self._worker = AftersaleDBWorker(aftersale_db.get_cycle_options)
        self._worker.result_ready.connect(self._on_cycles_loaded)
        self._worker.error.connect(lambda _m: self._load())
        self._worker.start()

    def _on_cycles_loaded(self, cycle_starts):
        self._cycles_loaded = True
        prev = self._cycle_combo.currentData()
        self._cycle_combo.blockSignals(True)
        self._cycle_combo.clear()
        current = aftersale_db.current_cycle_start()
        cycles = list(cycle_starts or [])
        # 当前周期仅在库中确实存在该周期数据时才出现（不额外新建库中不存在的周期）
        if current in cycles:
            self._cycle_combo.addItem(
                f"当前周期 {aftersale_db.cycle_label(current)}", userData=current)
            cycles.remove(current)
        self._cycle_combo.addItem("全部周期", userData="")
        for cs in cycles:
            self._cycle_combo.addItem(
                aftersale_db.cycle_label(cs), userData=cs)
        # 恢复之前的选择；默认当前周期（库中有数据）否则全部周期
        idx = self._cycle_combo.findData(prev if prev is not None else current)
        self._cycle_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._cycle_combo.blockSignals(False)
        self._load()

    def _load(self):
        """按当前筛选异步查询（分页数据 + 统计一次返回）"""
        if self._worker and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.disconnect(self)
        f = self._current_filters()
        self._worker = AftersaleDBWorker(
            aftersale_db.query_with_stats,
            self._page_no, self._page_size,
            keyword=f["keyword"], cycle_start=f["cycle_start"],
            issue_type=f["issue_type"], resolved=f["resolved"],
            is_initiative=f["is_initiative"],
            is_our_problem=f["is_our_problem"])
        self._worker.result_ready.connect(self._on_loaded)
        self._worker.error.connect(self._on_load_error)
        self._worker.start()

    def _on_loaded(self, result):
        total, rows, stats = result
        self._total = total
        self._rows = rows
        self._populate(rows)
        self._update_pager()
        self._update_source_label()
        self._update_overview(stats)
        cycle = self._current_filters().get("cycle_start") or ""
        if cycle:
            self._lbl_cycle.setText(f"周期: {aftersale_db.cycle_label(cycle)}")
            self._lbl_overview.setText(
                f"概览 · {aftersale_db.cycle_label(cycle)}")
        else:
            self._lbl_cycle.setText("周期: 全部")
            self._lbl_overview.setText("概览 · 全部周期")

    def _on_load_error(self, msg):
        self._lbl_stats.setText(f"查询失败: {msg}")
        self._update_source_label()

    # ---------- 概览指标卡 ----------

    def _update_overview(self, stats: dict):
        """统计 → 四张指标卡（本周期记录/未解决/已解决率/主动发起）"""
        total = int(stats.get("total") or 0)
        unresolved = int(stats.get("unresolved") or 0)
        rate = int(stats.get("rate") or 0)
        initiative = int(stats.get("initiative") or 0)

        _card, _lbl, num, sub = self._card_total
        num.setText(str(total))
        num.setStyleSheet("font-size: 24px; font-weight: 500; background: transparent;")
        sub.setText(f"已解决 {stats.get('resolved', 0)} 条")

        _card, _lbl, num, sub = self._card_unresolved
        num.setText(str(unresolved))
        # 未解决为 0 时用常规字色，有积压时用危险红提醒
        color = SEMANTIC["danger"] if unresolved > 0 else ""
        base = "font-size: 24px; font-weight: 500; background: transparent;"
        num.setStyleSheet(base + (f" color: {color};" if color else ""))
        sub.setText("待处理" if unresolved else "无积压")

        _card, _lbl, num, sub = self._card_rate
        num.setText(f"{rate}%")
        num.setStyleSheet(base + (f" color: {SEMANTIC['success']};" if rate >= 90 else ""))
        sub.setText("已解决率")

        _card, _lbl, num, sub = self._card_initiative
        init_rate = int(round(initiative * 100 / total)) if total else 0
        num.setText(str(initiative))
        num.setStyleSheet(base)
        sub.setText(f"主动发起 · 占 {init_rate}%")

        self._lbl_stats.setText(f"共 {total} 条")

    # ---------- 表格渲染 ----------

    def _row_tooltip(self, rec: dict) -> str:
        """行 tooltip：整合字段完整信息（含移入 tooltip 的判定字段）"""
        return (
            f"填写时间: {rec.get('created_at') or ''}\n"
            f"填写人: {rec.get('creator') or ''}\n"
            f"发生时间: {rec.get('occurred_at') or ''}\n"
            f"类型: {rec.get('issue_type') or ''}\n"
            f"位置: {rec.get('room_name') or ''} · "
            f"{rec.get('region') or ''} · {rec.get('table_no') or ''}\n"
            f"问题: {rec.get('problem') or ''}\n"
            f"发生原因: {rec.get('cause') or ''}\n"
            f"是否解决: {rec.get('resolved') or '否'}\n"
            f"是否我们主动发起: {rec.get('is_initiative') or '否'}\n"
            f"是否是我们的问题: {rec.get('is_our_problem') or '否'}\n"
            f"解决方案: {rec.get('solution') or ''}\n"
            f"解决人: {rec.get('resolver') or ''}\n"
            f"响应时间: {rec.get('response_time') or ''}")

    def _populate(self, rows):
        """行数据 → 表格：勾选列 + 双行信息整合列 + 是/否徽章三列 + 行内操作按钮"""
        self._table.setUpdatesEnabled(False)
        self._table.blockSignals(True)
        try:
            self._table.setRowCount(len(rows))
            for r, item in enumerate(rows):
                tip = self._row_tooltip(item)

                # 勾选列（供批量操作）
                chk = QTableWidgetItem()
                chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable
                             | Qt.ItemFlag.ItemIsEnabled)
                chk.setCheckState(Qt.CheckState.Unchecked)
                self._table.setItem(r, _COL_CHECK, chk)

                for i, (key, _h, _w) in enumerate(TABLE_COLUMNS):
                    col = _COL_CHECK + 1 + i
                    if key == "created_at":
                        cell = QTableWidgetItem(
                            f"{self._short_dt(item.get('created_at'))}\n"
                            f"{str(item.get('creator') or '')}")
                        f = QFont(cell.font())
                        f.setPointSizeF(10.5)
                        cell.setFont(f)
                    elif key == "location":
                        cell = QTableWidgetItem(
                            f"{str(item.get('room_name') or '')}\n"
                            f"{str(item.get('region') or '')} · "
                            f"{str(item.get('table_no') or '')}")
                        f = QFont(cell.font())
                        f.setPointSizeF(10.5)
                        cell.setFont(f)
                    elif key == "response_time":
                        cell = QTableWidgetItem(
                            f"{str(item.get('response_time') or '—')}\n"
                            f"{str(item.get('resolver') or '')}")
                        f = QFont(cell.font())
                        f.setPointSizeF(10.5)
                        cell.setFont(f)
                    elif key in _YES_NO_COLORS:
                        # 是/否徽章（setCellWidget 承载，该格不设 item）
                        is_yes = str(item.get(key) or "") == "是"
                        yes_c, no_c = _YES_NO_COLORS[key]
                        badge = _badge_label(
                            "是" if is_yes else "否",
                            yes_c if is_yes else no_c,
                            self._table.viewport())
                        self._table.setCellWidget(r, col, badge)
                        continue
                    elif key == "ops":
                        self._table.setCellWidget(
                            r, col, self._make_ops_cell(r, item))
                        continue
                    else:
                        val = str(item.get(key) or "")
                        cell = QTableWidgetItem(val)
                    cell.setToolTip(tip)
                    cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self._table.setItem(r, col, cell)
        finally:
            self._table.setUpdatesEnabled(True)
            self._table.blockSignals(False)
        self._sync_batch_bar()

    def _make_ops_cell(self, row: int, rec: dict) -> QWidget:
        """操作列容器：未解决行 = [已解决 + 编辑 + 删除]，已解决行 = [编辑 + 删除]（删除常驻）"""
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(4)
        is_yes = str(rec.get("resolved") or "") == "是"
        if not is_yes:
            lay.addWidget(_row_btn(
                "已解决", "primary",
                lambda: self._quick_resolve(row), wrap))
        lay.addWidget(_row_btn("编辑", "ghost", lambda: self._on_edit(row), wrap))
        lay.addWidget(_row_btn(
            "删除", "danger", lambda: self._on_delete(row), wrap))
        lay.addStretch(1)
        return wrap

    # ---------- 勾选与批量操作 ----------

    def _checked_ids(self) -> list:
        """当前勾选行的记录 id 列表"""
        ids = []
        for r in range(self._table.rowCount()):
            it = self._table.item(r, _COL_CHECK)
            if it is not None and it.checkState() == Qt.CheckState.Checked:
                if r < len(self._rows):
                    ids.append(self._rows[r].get("id"))
        return ids

    def _on_item_changed(self, item: QTableWidgetItem):
        """勾选列变动 → 刷新批量条计数（程序化改勾选也经此，幂等安全）"""
        if item.column() == _COL_CHECK:
            self._sync_batch_bar()

    def _sync_batch_bar(self):
        """按勾选数显示/隐藏批量操作条，更新计数与全选状态"""
        n = len(self._checked_ids())
        self._batch_bar.setVisible(n > 0)
        self._lbl_selected.setText(f"已选 {n} 项")
        total_rows = self._table.rowCount()
        self._chk_all.blockSignals(True)
        if total_rows and n == total_rows:
            self._chk_all.setCheckState(Qt.CheckState.Checked)
        else:
            self._chk_all.setCheckState(Qt.CheckState.Unchecked)
        self._chk_all.blockSignals(False)

    def _on_toggle_all(self, state):
        """全选/取消全选本页

        根因修复（两处）：
        1. Qt.CheckState 在本环境是普通 enum.Enum（非 IntEnum），stateChanged 信号
           发出的是 int；旧写法 `state == Qt.CheckState.Checked` 恒为 False，
           导致「全选本页」永远把行勾选置为未勾选。这里统一取 .value 比较。
        2. 循环前 blockSignals 避免每行 setCheckState 同步触发 itemChanged →
           _sync_batch_bar 把 _chk_all 视觉状态弹回 Unchecked（blockSignals 只挡
           信号不挡视觉）的竞态；循环结束后统一同步一次。
        """
        want = (getattr(state, "value", state) == Qt.CheckState.Checked.value)
        new_state = (Qt.CheckState.Checked if want
                     else Qt.CheckState.Unchecked)
        self._table.blockSignals(True)
        try:
            for r in range(self._table.rowCount()):
                it = self._table.item(r, _COL_CHECK)
                if it is not None:
                    it.setCheckState(new_state)
        finally:
            self._table.blockSignals(False)
        self._sync_batch_bar()

    def _on_clear_selection(self):
        self._on_toggle_all(Qt.CheckState.Unchecked)

    def _on_batch_resolve(self):
        """批量标记已解决（最小化更新，仅改 resolved）"""
        ids = self._checked_ids()
        if not ids:
            return
        if self._batch_worker and self._batch_worker.isRunning():
            return
        if not MessageBox("批量标记已解决",
                          f"确定将选中的 {len(ids)} 条记录标记为已解决？",
                          self.window()).exec():
            return
        self._batch_worker = AftersaleDBWorker(
            aftersale_db.mark_resolved_batch, ids)
        self._batch_worker.result_ready.connect(
            lambda n: (show_info_bar(f"已标记 {n} 条为已解决", "success",
                                     title="批量操作", parent=self, duration=2500),
                       self._load()))
        self._batch_worker.error.connect(
            lambda m: show_info_bar(m, "error", title="批量操作失败",
                                    parent=self, duration=4000))
        self._batch_worker.start()

    def _on_batch_delete(self):
        """批量删除（确认后走 delete_records）"""
        ids = self._checked_ids()
        if not ids:
            return
        if self._batch_worker and self._batch_worker.isRunning():
            return
        if not MessageBox("批量删除",
                          f"确定删除选中的 {len(ids)} 条售后记录？\n删除后不可恢复",
                          self.window()).exec():
            return
        self._batch_worker = AftersaleDBWorker(
            aftersale_db.delete_records, ids)
        self._batch_worker.result_ready.connect(
            lambda n: (show_info_bar(f"已删除 {n} 条记录", "success",
                                     title="批量操作", parent=self, duration=2500),
                       self._load()))
        self._batch_worker.error.connect(
            lambda m: show_info_bar(m, "error", title="批量操作失败",
                                    parent=self, duration=4000))
        self._batch_worker.start()

    # ---------- 行内快捷操作 ----------

    def _quick_resolve(self, row: int):
        """一键标记已解决：最小化更新（不弹编辑窗），3 步变 1 步"""
        if row >= len(self._rows):
            return
        if self._batch_worker and self._batch_worker.isRunning():
            return
        rec = self._rows[row]
        self._batch_worker = AftersaleDBWorker(
            aftersale_db.mark_resolved_batch, [rec.get("id")])
        self._batch_worker.result_ready.connect(
            lambda _n: (show_info_bar("记录已标记为已解决", "success",
                                      title="操作成功", parent=self, duration=2000),
                        self._load()))
        self._batch_worker.error.connect(
            lambda m: show_info_bar(m, "error", title="操作失败",
                                    parent=self, duration=4000))
        self._batch_worker.start()

    def _update_pager(self):
        pages = max(1, (self._total + self._page_size - 1) // self._page_size)
        self._page_no = min(self._page_no, pages)
        self._lbl_page.setText(f"{self._page_no}/{pages} · 共 {self._total} 条")
        self._btn_prev.setEnabled(self._page_no > 1)
        self._btn_next.setEnabled(self._page_no < pages)

    def _step_page(self, delta):
        self._page_no = max(1, self._page_no + delta)
        self._load()

    # ---------- 右键菜单与编辑 ----------

    def _selected_row(self):
        rows = sorted({i.row() for i in self._table.selectedIndexes()})
        if rows and rows[0] < len(self._rows):
            return rows[0]
        return -1

    def _show_context_menu(self, pos):
        idx = self._table.indexAt(pos)
        if not idx.isValid():
            return
        self._table.selectRow(idx.row())
        menu = RoundMenu(parent=self._table)
        act_edit = Action(FluentIcon.EDIT, "编辑", self._table)
        act_edit.triggered.connect(
            lambda _=False: self._on_edit(idx.row()))
        menu.addAction(act_edit)
        rec = self._rows[idx.row()] if idx.row() < len(self._rows) else {}
        if str(rec.get("resolved") or "") != "是":
            act_done = Action(FluentIcon.ACCEPT, "标记已解决", self._table)
            act_done.triggered.connect(
                lambda _=False, r=idx.row(): self._quick_resolve(r))
            menu.addAction(act_done)
        act_del = Action(FluentIcon.DELETE, "删除", self._table)
        act_del.triggered.connect(
            lambda _=False: self._on_delete(idx.row()))
        menu.addAction(act_del)
        menu.exec_(self._table.viewport().mapToGlobal(pos),
                   aniType=_popup_ani_type())

    def _on_edit(self, row: int = -1):
        if row < 0:
            row = self._selected_row()
        if row < 0 or row >= len(self._rows):
            return
        rec = dict(self._rows[row])
        dlg = EditRecordDialog(rec, self)
        # 编辑弹窗也需动态候选
        cand_worker = AftersaleDBWorker(aftersale_db.get_field_candidates)
        cand_worker.result_ready.connect(dlg.form.load_candidates)
        cand_worker.start()
        self._edit_cand_worker = cand_worker  # 保活引用
        if dlg.exec() and getattr(dlg, "collected", None):
            collected = dlg.collected
            collected["id"] = rec.get("id")
            collected["created_at"] = rec.get("created_at")  # 保留原填写时间
            self._run_update(collected)

    def _run_update(self, record):
        self._worker = AftersaleDBWorker(aftersale_db.update_record, record)
        self._worker.result_ready.connect(
            lambda _n: (show_info_bar("记录已更新", "success",
                                      title="保存成功", parent=self, duration=2000),
                        self._load()))
        self._worker.error.connect(
            lambda m: show_info_bar(m, "error", title="保存失败",
                                    parent=self, duration=4000))
        self._worker.start()

    def _on_delete(self, row: int = -1):
        if row < 0:
            row = self._selected_row()
        if row < 0 or row >= len(self._rows):
            return
        rec = self._rows[row]
        desc = f"{rec.get('table_no') or ''} · {rec.get('problem') or ''}"
        is_yes = str(rec.get("resolved") or "") == "是"
        msg = f"确定删除售后记录「{desc}」吗？\n删除后不可恢复。"
        if is_yes:
            msg += "\n该记录已标记为解决，删除后将无法恢复。"
        dlg = MessageBox("删除售后记录", msg, self)
        dlg.yesButton.setText("删除")
        dlg.cancelButton.setText("取消")
        if not dlg.exec():
            return
        self._worker = AftersaleDBWorker(aftersale_db.delete_record, rec.get("id"))
        self._worker.result_ready.connect(
            lambda _n: (show_info_bar("记录已删除", "success",
                                      title="删除成功", parent=self, duration=2000),
                        self._load()))
        self._worker.error.connect(
            lambda m: show_info_bar(m, "error", title="删除失败",
                                    parent=self, duration=4000))
        self._worker.start()

    # ---------- 导出 / 导入 ----------

    def _on_export(self):
        if self._export_worker and self._export_worker.isRunning():
            return
        f = self._current_filters()
        default_name = "售后记录_" + datetime.now().strftime("%Y%m%d_%H%M") + ".xlsx"
        path, _sel = QFileDialog.getSaveFileName(
            self, "导出 xlsx", default_name, "Excel 文件 (*.xlsx)")
        if not path:
            return
        self._btn_export.setEnabled(False)
        self._export_worker = AftersaleDBWorker(
            aftersale_db.export_xlsx, path,
            keyword=f["keyword"], cycle_start=f["cycle_start"],
            issue_type=f["issue_type"], resolved=f["resolved"],
            is_initiative=f["is_initiative"],
            is_our_problem=f["is_our_problem"])
        self._export_worker.result_ready.connect(self._on_export_done)
        self._export_worker.error.connect(self._on_export_error)
        self._export_worker.start()

    def _on_export_done(self, count):
        self._btn_export.setEnabled(True)
        show_info_bar(f"已导出 {count} 条记录", "success",
                      title="导出成功", parent=self, duration=3000)

    def _on_export_error(self, msg):
        self._btn_export.setEnabled(True)
        show_info_bar(msg, "error", title="导出失败", parent=self, duration=4000)

    def _on_import(self):
        if self._import_worker and self._import_worker.isRunning():
            return
        path, _sel = QFileDialog.getOpenFileName(
            self, "选择售后汇总 Excel", "", "Excel 文件 (*.xlsx)")
        if not path:
            return
        # 第一步：后台解析（不写库），成功后弹预览确认
        self._btn_import.setEnabled(False)
        self._preview_path = path
        self._import_worker = AftersaleDBWorker(aftersale_db.parse_excel_rows, path)
        self._import_worker.result_ready.connect(self._on_import_preview_ready)
        self._import_worker.error.connect(self._on_import_error)
        self._import_worker.start()

    def _on_import_preview_ready(self, result):
        """解析完成：弹预览对话框，确认后真正写库"""
        self._btn_import.setEnabled(True)
        excel_headers, rows = result
        dlg = ImportPreviewDialog(excel_headers, rows, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._btn_import.setEnabled(False)
        self._import_worker = AftersaleDBWorker(
            aftersale_db.import_excel_rows, self._preview_path)
        self._import_worker.result_ready.connect(self._on_import_done)
        self._import_worker.error.connect(self._on_import_error)
        self._import_worker.start()

    def _on_import_done(self, count):
        self._btn_import.setEnabled(True)
        show_info_bar(f"已导入 {count} 条历史记录", "success",
                      title="导入成功", parent=self, duration=3000)
        self._cycles_loaded = False
        self._load_cycles_then_data()

    def _on_import_error(self, msg):
        self._btn_import.setEnabled(True)
        show_info_bar(msg, "error", title="导入失败", parent=self, duration=4000)
