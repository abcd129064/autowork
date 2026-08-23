# -*- coding: utf-8 -*-
"""台账面板：记录与统计页（指标卡 + 筛选/分页 + 行内编辑/删除 + 署名统计 + 导出）"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QHeaderView, QAbstractItemView, QFileDialog,
                               QDialog, QComboBox as _QComboBox)

from qfluentwidgets import (TableWidget, SearchLineEdit, PushButton,
                            ToolButton, FluentIcon, TitleLabel, CaptionLabel,
                            BodyLabel, CardWidget, MessageBox, MessageBoxBase)

from core.design_tokens import SEMANTIC
from core.flow_widgets import FlowToolbarScrollArea
from core.utils import show_info_bar
from database import ledger_db
from workers.aftersale_worker import AftersaleDBWorker

from windows.ledger.common import *  # noqa: F401,F403
from windows.ledger.form import LedgerForm


class EditLedgerDialog(QDialog):
    """台账编辑弹窗：复用 LedgerForm，保存走 ledger_db.update_record"""

    def __init__(self, record: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑台账记录")
        self.resize(720, 520)
        self._record = record
        self._save_worker = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(10)
        self.form = LedgerForm(self)
        self.form.set_values(record)
        lay.addWidget(self.form)
        bar = QHBoxLayout()
        bar.addStretch(1)
        self._btn_cancel = PushButton("取消", self)
        self._btn_cancel.clicked.connect(self.reject)
        bar.addWidget(self._btn_cancel)
        self._btn_save = PushButton(FluentIcon.SAVE, "保存", self)
        self._btn_save.clicked.connect(self._on_save)
        bar.addWidget(self._btn_save)
        lay.addLayout(bar)

    def _on_save(self):
        missing = self.form.validate()
        if missing:
            show_info_bar(f"请先填写必填项: {'、'.join(missing)}", "warning",
                          title="无法保存", parent=self, duration=3000)
            return
        if self._save_worker and self._save_worker.isRunning():
            return
        self._btn_save.setEnabled(False)
        self._save_worker = AftersaleDBWorker(
            ledger_db.update_record, int(self._record["id"]),
            self.form.collect())
        self._save_worker.result_ready.connect(self._on_saved)
        self._save_worker.error.connect(self._on_save_error)
        self._save_worker.start()

    def _on_saved(self, _ok):
        self.accept()

    def _on_save_error(self, msg):
        self._btn_save.setEnabled(True)
        show_info_bar(msg, "error", title="保存失败",
                      parent=self, duration=4000)


class SignerStatsDialog(MessageBoxBase):
    """署名统计弹窗：按署名汇总四分类计数（模板「计数」sheet 的电子版）"""

    def __init__(self, stats: list, parent=None):
        super().__init__(parent)
        self.titleLabel.setText("署名统计")
        self.contentLabel.setText(
            "每人 问题/未复现/精度/使用 计数与总和（与在线模板「计数」sheet 同口径）")
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
        from PySide6.QtWidgets import QTableWidgetItem
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
        self._manual_refresh = False  # 手动刷新标志：完成/失败时弹 infobar 反馈
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 12)
        root.setSpacing(8)

        # --- 页头：标题 + 说明 + 数据源状态 ---
        head = QHBoxLayout()
        head.setSpacing(10)
        head_box = QVBoxLayout()
        head_box.setSpacing(1)
        head_box.addWidget(TitleLabel("记录与统计", self))
        head_box.addWidget(CaptionLabel(
            "台账问题记录：分类（问题/未复现/精度/使用）与在线模板同口径", self))
        head.addLayout(head_box)
        head.addStretch(1)
        self._lbl_source = CaptionLabel("", self)
        head.addWidget(self._lbl_source, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(head)

        # --- 四分类指标卡 + 总数卡 ---
        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)
        self._cards = {}
        for key, title in (("total", "总记录"), ("问题", "问题"),
                           ("未复现", "未复现"), ("精度", "精度"),
                           ("使用", "使用")):
            card, _l, num = self._make_stats_card(cards_row, title)
            self._cards[key] = num
        root.addLayout(cards_row)

        # --- 筛选工具栏（与售后记录页同范式） ---
        toolbar_scroll = FlowToolbarScrollArea(self)
        toolbar_scroll.setWidgetResizable(True)
        toolbar_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        toolbar_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        toolbar_scroll.setMaximumHeight(96)
        toolbar_widget = QWidget(self)
        toolbar_scroll.setWidget(toolbar_widget)
        from qfluentwidgets import FlowLayout
        toolbar = FlowLayout(toolbar_widget)
        toolbar.setHorizontalSpacing(6)
        toolbar.setVerticalSpacing(6)
        toolbar.setContentsMargins(2, 4, 2, 4)

        # 分类筛选
        self._cat_combo = FluentCombo(self)
        self._cat_combo.addItem("全部分类")
        self._cat_combo.addItems(ledger_db.CATEGORIES)
        self._cat_combo.setFixedWidth(130)
        self._cat_combo.currentIndexChanged.connect(
            lambda _i: self._on_cat_changed())
        toolbar.addWidget(self._cat_combo)

        # 类别筛选（随分类联动候选）
        self._kind_combo = FluentCombo(self)
        self._kind_combo.setEditable(True)
        self._kind_combo.setInsertPolicy(_QComboBox.InsertPolicy.NoInsert)
        self._kind_combo.addItem("全部类别")
        self._kind_combo.setFixedWidth(170)
        self._kind_combo.currentIndexChanged.connect(
            lambda _i: self._on_filter_changed())
        self._kind_combo.currentTextChanged.connect(
            lambda _t: self._search_timer.start())
        toolbar.addWidget(self._kind_combo)

        # 署名筛选
        self._signer_combo = FluentCombo(self)
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

        self._btn_stats = PushButton(FluentIcon.PIE_SINGLE, "署名统计", self)
        self._btn_stats.setToolTip("按署名统计四分类计数（模板「计数」sheet）")
        self._btn_stats.clicked.connect(self._on_show_stats)
        toolbar.addWidget(self._btn_stats)

        self._btn_export = PushButton(FluentIcon.DOWNLOAD, "导出 xlsx", self)
        self._btn_export.setToolTip("按分类分 sheet 导出（结构与在线模板一致）")
        self._btn_export.clicked.connect(self._on_export)
        toolbar.addWidget(self._btn_export)

        self._btn_refresh = PushButton(FluentIcon.SYNC, "刷新", self)
        self._btn_refresh.setToolTip("重新查询数据库（多人协作看新数据）")
        self._btn_refresh.clicked.connect(self._on_refresh)
        toolbar.addWidget(self._btn_refresh)

        root.addWidget(toolbar_scroll)

        # 搜索防抖定时器
        self._search_timer = QTimer(self)
        self._search_timer.setInterval(300)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._on_filter_changed)

        # --- 表格 ---
        self._table = TableWidget(self)
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

    # ---------- 数据源指示 ----------

    def _update_source_label(self):
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

    # ---------- 筛选条件 ----------

    def _current_filters(self) -> dict:
        cat = self._cat_combo.currentText().strip()
        kind = self._kind_combo.currentText().strip()
        signer = self._signer_combo.currentText().strip()
        return {
            "category": "" if cat == "全部分类" else cat,
            "kind": "" if kind == "全部类别" else kind,
            "signer": "" if signer == "全部署名" else signer,
            "keyword": self._search_edit.text().strip(),
        }

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
            self._kind_combo.addItems(list(cands or ()))
            if cur:
                self._kind_combo.setEditText(cur)
            else:
                self._kind_combo.setCurrentIndex(0)
            self._kind_combo.blockSignals(False)
            self._on_filter_changed()

        if not category:
            for c in ledger_db.KIND_CANDIDATES.values():
                pass  # 全部分类：候选为全部并集（下方同步填充）
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

    # ---------- 加载 ----------

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
            filters["kind"], filters["signer"])
        self._worker.result_ready.connect(self._on_loaded)
        self._worker.error.connect(self._on_load_error)
        self._worker.start()
        # 指标卡（全库口径：分类计数不受筛选影响）
        self._stats_worker = AftersaleDBWorker(self._count_by_category)
        self._stats_worker.result_ready.connect(self._on_stats_loaded)
        self._stats_worker.error.connect(lambda _m: None)
        self._stats_worker.start()

    @staticmethod
    def _count_by_category() -> dict:
        """全库分类计数（指标卡数据源）"""
        from database import table_db
        conn = table_db.get_conn()
        counts = {"total": 0}
        for c in ledger_db.CATEGORIES:
            counts[c] = 0
        for cat, n in conn.execute(
                "SELECT category, COUNT(*) FROM ledger_records "
                "GROUP BY category").fetchall():
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
            from PySide6.QtWidgets import QTableWidgetItem
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
                    if c == 1:  # 分类徽章
                        w = QWidget(self._table)
                        lay = QHBoxLayout(w)
                        lay.setContentsMargins(2, 4, 2, 4)
                        lay.setSpacing(0)
                        lay.addWidget(_category_badge(str(v), w))
                        lay.addStretch(1)
                        self._table.setCellWidget(r, c, w)
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
                self._table.setCellWidget(r, _COL_OPS,
                                          self._make_ops_widget(row))
        finally:
            self._table.setUpdatesEnabled(True)
            self._table.blockSignals(False)
        # 刷新时 cellWidget 定位可能残留陈旧偏移（行内按钮整列下沉），
        # 延迟重排让 Qt 在事件循环中按最新布局重新定位所有 cellWidget
        QTimer.singleShot(0, self._table.scheduleDelayedItemsLayout)

    @staticmethod
    def _short_dt(val) -> str:
        """2026-08-22 21:14:33 → 08-22 21:14（紧凑展示）"""
        s = str(val or "").strip()
        return s[5:16] if len(s) >= 16 else s

    def _make_ops_widget(self, row) -> QWidget:
        """行内操作列：编辑（primary）/ 删除（danger）"""
        w = QWidget(self._table)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(6)
        btn_edit = _row_btn("编辑", "primary",
                            lambda r=row: self._on_edit(r), w)
        btn_del = _row_btn("删除", "danger",
                           lambda r=row: self._on_delete(r), w)
        lay.addWidget(btn_edit)
        lay.addWidget(btn_del)
        lay.addStretch(1)
        return w

    # ---------- 编辑 / 删除 ----------

    def _on_edit(self, row=None):
        if row is None:
            idx = self._table.currentRow()
            if idx < 0 or idx >= len(self._rows):
                return
            row = self._rows[idx]
        dlg = EditLedgerDialog(dict(row), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            show_info_bar("已保存修改", "success", title="编辑",
                          parent=self, duration=2500)
            self._load()

    def _on_delete(self, row):
        box = MessageBox("删除确认",
                         f"确定删除这条台账记录吗？\n"
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
            self, "导出台账", "台账_export.xlsx", "Excel 文件 (*.xlsx)")
        if not path:
            return
        self._export_worker = AftersaleDBWorker(ledger_db.export_xlsx, path)
        self._export_worker.result_ready.connect(
            lambda n: show_info_bar(
                f"已导出 {n} 条（按分类分 sheet，结构与在线模板一致）",
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
