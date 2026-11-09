# -*- coding: utf-8 -*-
"""页签1 记录量化（稿 308-410）：汇总卡 + 流式工具栏 + 按天分组的记录表

取数口径（稿 309-325 与表格的对应关系）：
* 前三张「今日*」卡取**锚点日**那一条 daily_groups 聚合；
* 「本周人均工时」与整张表取锚点日所在的**自然周**（周一~周日）——
  稿的日期选择器是单日，但表格里画了两个日分组、分组头又写「8 项」却只
  排三行，可证稿是截取的示意；单日口径下表里永远只有一组，「分组带」这
  个设计元素就废了，故按周取数、按天分组。
* 三族查询（明细/分组/总计）并发发出，凑齐同一 seq 才填表，避免明细先到、
  分组后到时表格与分组头数字对不上。

表格不用 cellWidget 列（稿 404）：分组带、头像、标签胶囊、迷你进度条、
操作链接全部由 windows/worklog/delegate.py 自绘。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QClipboard
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QGridLayout, QApplication,
                               QHeaderView, QAbstractItemView, QTableWidgetItem)
from qfluentwidgets import (TableWidget, ComboBox, SearchLineEdit, PushButton,
                            PrimaryPushButton, FluentIcon, BodyLabel,
                            MessageBox)

from core.design_tokens import SPACE, DEFAULT_GAP
from core.ops_link_delegate import LINKS_ROLE
from core.utils import show_info_bar
from database import worklog_db
from windows.worklog.common import (CardBox, FlowToolbar, IconBlock, RateRing,
                                    SummaryCard, DateBox, category_tag,
                                    status_tag, member_color, accent_press,
                                    color_hex, day_label, parse_iso, weekday_zh,
                                    fmt_hours, px_font, text_color_tier)
from windows.worklog.controller import WorklogController
from windows.worklog.delegate import (ROLE_GROUP, ROLE_AVATAR, ROLE_TAG,
                                      ROLE_BAR, install_worklog_delegate)
from windows.worklog.dialogs import EditWorklogDialog

# 稿 340-346 的列宽：工作内容列吸收剩余宽度，其余定宽
_COLUMNS = (
    ("日期", 96), ("成员", 110), ("工作内容", 0), ("项目", 110), ("分类", 84),
    ("工时", 64), ("进度", 130), ("状态", 76), ("操作", 96),
)
_ROW_H = 38          # 稿 147：数据行 padding 10px 上下 + 13px 行
_GROUP_ROW_H = 30    # 稿 149：分组行 padding 6px 上下


class RecordsPage(QWidget):
    """记录量化页：本页所有后台线程挂在 ``self._ctl`` 上"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ctl = WorklogController(self)
        self._seq = 0
        self._rows = None
        self._groups = None
        self._totals = None
        self._members = []
        self._categories = []
        self._projects = []
        self._row_ids = []          # 表格行号 → 记录 id（分组行记 None）
        self._loaded_once = False
        self._pending_locate = None  # 时间轴跳转过来的记录 id，填表后选中

        root = QVBoxLayout(self)
        # 稿 84 .pv-body 的 padding：上16 左右28 下24（28 不在 SPACE 刻度上）
        root.setContentsMargins(28, SPACE[4], 28, SPACE[6])
        root.setSpacing(DEFAULT_GAP)
        self._build_cards(root)
        self._build_toolbar(root)
        self._build_table(root)
        self._connect()

        self._search_timer = QTimer(self)
        self._search_timer.setInterval(300)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self.refresh)

    # ---------------- 布局 ----------------

    def _build_cards(self, root):
        """稿 309-325：四等分汇总卡，gap 12"""
        wrap = QWidget(self)
        grid = QGridLayout(wrap)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(SPACE[3])
        self.card_hours = SummaryCard(
            "今日工时（全成员）",
            lead=IconBlock(FluentIcon.STOP_WATCH, accent_press))
        self.card_tasks = SummaryCard(
            "今日任务", lead=IconBlock(FluentIcon.QUICK_NOTE,
                                       color_hex("info")))
        self._ring = RateRing()
        self.card_rate = SummaryCard("今日完成率", lead=self._ring)
        self.card_percap = SummaryCard(
            "本周人均工时", lead=IconBlock(FluentIcon.PEOPLE,
                                           color_hex("warning")))
        for i, card in enumerate((self.card_hours, self.card_tasks,
                                  self.card_rate, self.card_percap)):
            grid.addWidget(card, 0, i)
        root.addWidget(wrap)

    def _build_toolbar(self, root):
        """稿 327-336：日期组 + 三个筛选 + 搜索，右侧复制周报/新增记录"""
        bar = FlowToolbar(self)
        self.date_box = DateBox(bar)
        self.date_box.dateChanged.connect(lambda _d: self.refresh())
        bar.addWidget(self.date_box)

        self.cbo_member = ComboBox(bar)
        self.cbo_member.setMinimumWidth(120)
        self.cbo_category = ComboBox(bar)
        self.cbo_category.setMinimumWidth(120)
        self.cbo_status = ComboBox(bar)
        self.cbo_status.setMinimumWidth(120)
        for cbo in (self.cbo_member, self.cbo_category, self.cbo_status):
            cbo.currentIndexChanged.connect(lambda _i: self.refresh())
            bar.addWidget(cbo)

        self.edt_search = SearchLineEdit(bar)
        self.edt_search.setFixedWidth(200)
        self.edt_search.setPlaceholderText("搜索内容 / 项目 / 备注")
        self.edt_search.textChanged.connect(
            lambda _t: self._search_timer.start())
        bar.addWidget(self.edt_search)

        bar.addRight()
        self.btn_report = PushButton(FluentIcon.COPY, "复制周报", bar)
        self.btn_report.clicked.connect(lambda _=False: self._on_copy_report())
        bar.addWidget(self.btn_report)
        self.btn_add = PrimaryPushButton(FluentIcon.ADD, "新增记录", bar)
        self.btn_add.clicked.connect(lambda _=False: self._on_add())
        bar.addWidget(self.btn_add)
        root.addWidget(bar)

    def _build_table(self, root):
        """稿 339-356：贴边表格卡 + 自绘委托；竖表头隐藏（qfw 默认会露出来）"""
        card = CardBox(padding=0, parent=self)
        self._table = TableWidget(card)
        self._table.setColumnCount(len(_COLUMNS))
        self._table.setHorizontalHeaderLabels([c[0] for c in _COLUMNS])
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setWordWrap(False)
        self._table.setShowGrid(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for i, (_name, w) in enumerate(_COLUMNS):
            if w:
                self._table.setColumnWidth(i, w)
            else:
                # 工作内容列吸收剩余宽度；不能用 setStretchLastSection，
                # 那会把最后的操作列拉宽
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        install_worklog_delegate(self._table, self._on_ops_link,
                                 self._on_step_progress)
        self._table.doubleClicked.connect(lambda _idx: self._on_edit())
        card.lay.addWidget(self._table)

        # 空态提示叠在表格上：表格自身没有「居中占位一行」的能力，
        # 塞进模型又会和分组带的行号算法搅在一起
        self._lbl_empty = BodyLabel("本周还没有记录，点右上角「新增记录」开始留痕",
                                    card)
        self._lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_empty.setFont(px_font(13))
        self._lbl_empty.setTextColor(*text_color_tier(3))
        self._lbl_empty.hide()
        card.installEventFilter(self)
        self._card = card
        root.addWidget(card, 1)

    def _connect(self):
        ctl = self._ctl
        ctl.rows_ready.connect(self._on_rows)
        ctl.groups_ready.connect(self._on_groups)
        ctl.totals_ready.connect(self._on_totals)
        ctl.load_failed.connect(self._on_load_failed)
        ctl.options_ready.connect(self._on_options)
        ctl.options_failed.connect(
            lambda msg: show_info_bar(msg, "error", title="候选加载失败",
                                      parent=self, duration=4000))
        ctl.mutation_done.connect(self._on_mutation_done)
        ctl.mutation_failed.connect(
            lambda tag, msg: show_info_bar(msg, "error", title="操作失败",
                                           parent=self, duration=4000))
        ctl.report_ready.connect(self._on_report_ready)
        ctl.report_failed.connect(
            lambda msg: show_info_bar(msg, "error", title="报告生成失败",
                                      parent=self, duration=4000))

    # ---------------- 取数 ----------------

    def showEvent(self, event):
        """首屏取数放在首次显示：构造时 Hub 还没挂进主窗口，查了也没处画"""
        super().showEvent(event)
        if not self._loaded_once:
            self._loaded_once = True
            self.initial_load()

    def initial_load(self):
        """Hub 切到本页时调一次：候选 + 首屏数据"""
        self._ctl.fetch_options()
        self.refresh()

    def detach_workers(self):
        """PivotPage 切页/关窗时的收尾钩子"""
        self._ctl.stop_all()

    def on_options_changed(self):
        """成员/分类被页签5 改动后由 Hub 广播：候选与数据都要重拉"""
        self._ctl.fetch_options()
        self.refresh()

    def locate_record(self, rec):
        """时间轴点条目的落点（稿 419）：已在表里直接选中，不在就把锚点日
        挪到记录所在周重查、填表后再选中（定位请求挂在 _pending_locate 上）"""
        rec = rec or {}
        rid = rec.get("id")
        if not rid:
            return
        if rid in self._row_ids:
            self._select_row_of(rid)
            return
        self._pending_locate = rid
        d = parse_iso(rec.get("work_date"))
        if d.isValid() and d != self.date_box.date():
            self.date_box.setDate(d)      # dateChanged 会带出一次 refresh
        else:
            self.refresh()

    def _filters(self) -> dict:
        start, end = worklog_db.week_range(
            self.date_box.date().toString("yyyy-MM-dd"))
        return {
            "date_from": start, "date_to": end,
            "member_id": self.cbo_member.currentData() or 0,
            "category": "" if self.cbo_category.currentIndex() <= 0
            else self.cbo_category.currentText(),
            "status": "" if self.cbo_status.currentIndex() <= 0
            else self.cbo_status.currentText(),
            "keyword": self.edt_search.text().strip(),
        }

    def refresh(self):
        """三族查询并发；seq 递增让晚到的旧结果作废"""
        self._seq += 1
        seq = self._seq
        self._rows = self._groups = self._totals = None
        f = self._filters()
        self._ctl.query_records(seq, **f)
        self._ctl.query_groups(seq, **f)
        self._ctl.query_totals(seq, **f)

    def _on_rows(self, rows, seq):
        if seq != self._seq:
            return
        self._rows = rows or []
        self._maybe_populate()

    def _on_groups(self, groups, seq):
        if seq != self._seq:
            return
        self._groups = groups or []
        self._maybe_populate()

    def _on_totals(self, totals, seq):
        if seq != self._seq:
            return
        self._totals = totals or {}
        self._maybe_populate()

    def _on_load_failed(self, msg, seq):
        if seq != self._seq:
            return
        show_info_bar(msg, "error", title="加载失败", parent=self, duration=4000)

    def _maybe_populate(self):
        """三族都到齐才填：任缺其一就等，避免表格与分组头数字对不上"""
        if self._rows is None or self._groups is None or self._totals is None:
            return
        self._fill_cards()
        self._fill_table()

    # ---------------- 填卡 / 填表 ----------------

    def _anchor_group(self) -> dict:
        anchor = self.date_box.date().toString("yyyy-MM-dd")
        for g in self._groups:
            if g.get("work_date") == anchor:
                return g
        return {}

    def _fill_cards(self):
        g = self._anchor_group()
        t = self._totals or {}
        self.card_hours.setValue(fmt_hours(g.get("hours")), "h")
        self.card_tasks.setValue(g.get("count") or 0,
                                 "项 · %d 人" % (g.get("members") or 0))
        rate = int(g.get("done_rate") or 0)
        self._ring.setValue(rate)
        self.card_rate.setValue("%d%%" % rate, "",
                                value_color=color_hex("success"))
        self.card_percap.setValue(t.get("per_capita") or 0, "h/人·日")

    def _fill_table(self):
        table = self._table
        table.setRowCount(0)
        self._row_ids = []
        groups = {g["work_date"]: g for g in (self._groups or [])}
        last_date = None
        for rec in self._rows or []:
            wd = rec.get("work_date") or ""
            if wd != last_date:
                self._append_group_row(wd, groups.get(wd))
                last_date = wd
            self._append_record_row(rec)
        empty = table.rowCount() == 0
        self._lbl_empty.setVisible(empty)
        if empty:
            self._place_empty()
        if self._pending_locate:
            rid, self._pending_locate = self._pending_locate, None
            self._select_row_of(rid)

    def _select_row_of(self, record_id):
        for row, rid in enumerate(self._row_ids):
            if rid == record_id:
                self._table.selectRow(row)
                self._table.scrollToItem(self._table.item(row, 0))
                return

    def _append_group_row(self, work_date, group):
        """分组带行（稿 348-350）：文案只挂在第 0 列，其余列只铺底色"""
        d = parse_iso(work_date)
        g = group or {}
        text = "%s %s — %d 人 · %d 项 · %sh · 完成率 %d%%" % (
            day_label(d) if d.isValid() else work_date,
            weekday_zh(d) if d.isValid() else "",
            g.get("members") or 0, g.get("count") or 0,
            fmt_hours(g.get("hours")), g.get("done_rate") or 0)
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setRowHeight(row, _GROUP_ROW_H)
        for col in range(len(_COLUMNS)):
            item = QTableWidgetItem("")
            item.setFlags(Qt.ItemFlag.NoItemFlags)   # 分组行不可选不可编辑
            if col == 0:
                item.setData(ROLE_GROUP, text)
            self._table.setItem(row, col, item)
        self._row_ids.append(None)

    def _append_record_row(self, rec):
        table = self._table
        row = table.rowCount()
        table.insertRow(row)
        table.setRowHeight(row, _ROW_H)
        d = parse_iso(rec.get("work_date"))
        values = (
            d.toString("MM-dd") if d.isValid() else rec.get("work_date") or "",
            rec.get("member_alias") or rec.get("member_name") or "",
            rec.get("title") or "",
            rec.get("project") or "",
            "",
            fmt_hours(rec.get("hours")),
            "",
            "",
            "",
        )
        for col, text in enumerate(values):
            item = QTableWidgetItem(text)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            table.setItem(row, col, item)
        name = rec.get("member_alias") or rec.get("member_name") or "？"
        table.item(row, 1).setData(
            ROLE_AVATAR, (name[:1], member_color(rec.get("member_id")), name))
        table.item(row, 4).setData(
            ROLE_TAG, (rec.get("category") or "",)
            + category_tag(rec.get("category")))
        table.item(row, 6).setData(ROLE_BAR, rec.get("progress") or 0)
        table.item(row, 7).setData(
            ROLE_TAG, (rec.get("status") or "",)
            + status_tag(rec.get("status")))
        table.item(row, 8).setData(
            LINKS_ROLE, [("编辑", "primary", "edit"),
                         ("删除", "danger", "del")])
        self._row_ids.append(rec.get("id"))

    def _place_empty(self):
        self._lbl_empty.setGeometry(0, self._table.height() // 3,
                                    self._card.width(), 40)

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if obj is self._card and ev.type() == QEvent.Type.Resize:
            if self._lbl_empty.isVisible():
                self._place_empty()
        return super().eventFilter(obj, ev)

    # ---------------- 交互 ----------------

    def _on_options(self, data):
        """候选到位：重建三个筛选框（保留当前选中项）"""
        self._members = data.get("members") or []
        self._categories = data.get("categories") or []
        self._projects = data.get("projects") or []
        self._refill_filter_combos()

    def _refill_filter_combos(self):
        keep_m = self.cbo_member.currentData() or 0
        keep_c = self.cbo_category.currentText()
        keep_s = self.cbo_status.currentText()
        for cbo in (self.cbo_member, self.cbo_category, self.cbo_status):
            cbo.blockSignals(True)
        self.cbo_member.clear()
        self.cbo_member.addItem("全部成员", userData=0)
        for m in self._members:
            self.cbo_member.addItem(m.get("alias") or m.get("name") or "",
                                    userData=m.get("id"))
        self.cbo_category.clear()
        self.cbo_category.addItem("全部分类")
        for c in self._categories:
            name = c.get("name") if isinstance(c, dict) else str(c)
            self.cbo_category.addItem(name)
        self.cbo_status.clear()
        self.cbo_status.addItem("全部状态")
        self.cbo_status.addItems(list(worklog_db.STATUSES))
        i = self.cbo_member.findData(keep_m)
        self.cbo_member.setCurrentIndex(max(0, i))
        for cbo, keep in ((self.cbo_category, keep_c),
                          (self.cbo_status, keep_s)):
            j = cbo.findText(keep)
            cbo.setCurrentIndex(max(0, j))
        for cbo in (self.cbo_member, self.cbo_category, self.cbo_status):
            cbo.blockSignals(False)

    def _rec_at_row(self, row: int):
        if 0 <= row < len(self._row_ids) and self._row_ids[row]:
            for rec in self._rows or []:
                if rec.get("id") == self._row_ids[row]:
                    return rec
        return None

    def _on_ops_link(self, row, _col, action):
        rec = self._rec_at_row(row)
        if rec is None:
            return
        if action == "edit":
            self._on_edit(rec)
        elif action == "del":
            self._on_delete(rec)

    def _on_step_progress(self, row, percent):
        """进度格滚轮/双击：落库后整页刷新（汇总卡与分组头都跟着变）"""
        rec = self._rec_at_row(row)
        if rec is None:
            return
        self._ctl.mutate("progress", worklog_db.set_progress,
                         rec.get("id"), percent)

    def _on_mutation_done(self, tag, _payload):
        if tag == "progress":
            show_info_bar("进度已更新", "success", parent=self, duration=1500)
        elif tag == "delete":
            show_info_bar("记录已删除", "success", parent=self, duration=2000)
        self.refresh()

    def _on_add(self):
        dlg = EditWorklogDialog(parent=self.window(), continuous=True,
                                on_saved=self.refresh,
                                members=self._members,
                                categories=self._categories,
                                projects=self._projects)
        dlg.exec()

    def _on_edit(self, rec=None):
        rec = rec or self._rec_at_row(self._table.currentRow())
        if rec is None:
            return
        dlg = EditWorklogDialog(record=rec, parent=self.window(),
                                on_saved=self.refresh,
                                members=self._members,
                                categories=self._categories,
                                projects=self._projects)
        dlg.exec()

    def _on_delete(self, rec):
        # 稿没画确认框，但删除是单向操作；仓库售后面板的同名操作也带确认
        if not MessageBox("删除记录",
                          "确定删除「%s」这条记录？" % (rec.get("title") or ""),
                          self.window()).exec():
            return
        self._ctl.mutate("delete", worklog_db.delete_record, rec.get("id"))

    def _on_copy_report(self):
        f = self._filters()
        self._ctl.build_report(f["date_from"], f["date_to"], f["member_id"],
                               title="周报")

    def _on_report_ready(self, text):
        if not text:
            show_info_bar("本期没有可复制的记录", "warning", parent=self,
                          duration=3000)
            return
        QApplication.clipboard().setText(text, QClipboard.Mode.Clipboard)
        show_info_bar("周报已复制到剪贴板", "success", parent=self,
                      duration=2500)


__all__ = ["RecordsPage"]
