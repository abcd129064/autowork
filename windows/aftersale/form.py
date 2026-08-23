# -*- coding: utf-8 -*-
"""form 模块（从 windows/aftersale_panel.py 拆出，逻辑未改动）"""

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

# ==================== 共享表单（录入页与编辑弹窗复用） ====================

class AftersaleForm(QWidget):
    """售后记录表单：字段与 售后问题汇总8月.xlsx 对齐 + 系统附加字段

    桌号输入防抖搜索球桌管理库，候选列表点选后自动带出球房/SNK；
    精确匹配单台球桌时静默带出，无需点选。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._snk_code = ""      # 关联球桌带出（隐藏字段，随记录落库）
        self._search_kw = ""
        self._last_city = ""     # 上次由球桌带出的城市（球房变动时联动清空）
        self._cand_worker = None
        self._init_ui()

        # 球房搜索防抖：停止输入 300ms 后才查库，避免逐字触发查询
        self._search_timer = QTimer(self)
        self._search_timer.setInterval(300)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._do_room_search)

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # ---------- 板块一：基本信息（标签上置，控件固定宽度，右侧留白） ----------
        sec1 = _SectionCard("基本信息", self)
        row1 = QHBoxLayout()
        row1.setSpacing(16)
        col_type = QVBoxLayout()
        col_type.setSpacing(3)
        col_type.addWidget(_field_label("问题类型", True, self))
        # 与「问题描述-问题」同规格：可编辑下拉 + NoInsert（可选预置也可手输）
        self.type_combo = FluentCombo(self)
        self.type_combo.setEditable(True)
        self.type_combo.setInsertPolicy(_QComboBox.InsertPolicy.NoInsert)
        self.type_combo.addItems(aftersale_db.ISSUE_TYPES)
        self.type_combo.setFixedWidth(320)
        col_type.addWidget(self.type_combo)
        self._err_type = _inline_error("必填项未填写", self)
        col_type.addWidget(self._err_type)
        row1.addLayout(col_type)
        col_occ = QVBoxLayout()
        col_occ.setSpacing(3)
        col_occ.addWidget(_field_label("发生时间", True, self))
        occurred_row = QHBoxLayout()
        occurred_row.setSpacing(2)
        self.occurred_picker = ZhDatePicker(self)
        self.occurred_picker.setFixedWidth(150)
        self.occurred_picker.setFixedHeight(33)
        self.occurred_picker.setDate(QDate.currentDate())
        occurred_row.addWidget(self.occurred_picker)
        # 日期步进按钮（实心三角成组）：连续点击逐日前移/后移，
        # 补录历史发生日期（如 8/25 录 8/20 的售后）连续点 ◀ 即可回退
        self._btn_occurred_prev = ToolButton(
            FluentIcon.CARE_LEFT_SOLID, self)
        self._btn_occurred_prev.setFixedSize(28, 33)
        self._btn_occurred_prev.setToolTip("前一天")
        self._btn_occurred_prev.clicked.connect(
            lambda _=False: self._step_occurred(-1))
        occurred_row.addWidget(self._btn_occurred_prev)
        self._btn_occurred_next = ToolButton(
            FluentIcon.CARE_RIGHT_SOLID, self)
        self._btn_occurred_next.setFixedSize(28, 33)
        self._btn_occurred_next.setToolTip("后一天")
        self._btn_occurred_next.clicked.connect(
            lambda _=False: self._step_occurred(1))
        occurred_row.addWidget(self._btn_occurred_next)
        col_occ.addLayout(occurred_row)
        row1.addLayout(col_occ)
        col_creator = QVBoxLayout()
        col_creator.setSpacing(3)
        col_creator.addWidget(_field_label("填写人", False, self))
        self.creator_edit = LineEdit(self)
        self.creator_edit.setText(_default_creator())  # 默认取配置，可改
        self.creator_edit.setFixedWidth(160)
        col_creator.addWidget(self.creator_edit)
        row1.addLayout(col_creator)
        row1.addStretch(1)
        sec1.content_layout.addLayout(row1)
        root.addWidget(sec1)

        # ---------- 板块二：位置关联（控件固定宽度，带出芯片 + 关联确认条） ----------
        sec2 = _SectionCard("位置关联", self)
        row2 = QHBoxLayout()
        row2.setSpacing(16)
        room_col = QVBoxLayout()
        room_col.setSpacing(3)
        room_col.addWidget(_field_label("球房", True, self))
        self.room_edit = SearchLineEdit(self)
        self.room_edit.setPlaceholderText("输入球房名搜索球桌")
        self.room_edit.setFixedWidth(320)
        self.room_edit.textChanged.connect(self._on_room_text_changed)
        self.room_edit.clearSignal.connect(self._hide_candidates)
        room_col.addWidget(self.room_edit)
        self._err_room = _inline_error("必填项未填写", self)
        room_col.addWidget(self._err_room)
        row2.addLayout(room_col)
        tbl_col = QVBoxLayout()
        tbl_col.setSpacing(3)
        tbl_col.addWidget(_field_label("桌号", False, self))
        self.table_no_edit = LineEdit(self)
        self.table_no_edit.setPlaceholderText("选桌自动带出")
        self.table_no_edit.setFixedWidth(140)
        tbl_col.addWidget(self.table_no_edit)
        self._err_table = _inline_error("必填项未填写", self)
        tbl_col.addWidget(self._err_table)
        row2.addLayout(tbl_col)
        reg_col = QVBoxLayout()
        reg_col.setSpacing(3)
        reg_col.addWidget(_field_label("地区", True, self))
        self.region_combo = FluentCombo(self)
        self.region_combo.setEditable(True)
        self.region_combo.setInsertPolicy(_QComboBox.InsertPolicy.NoInsert)
        self.region_combo.addItems(aftersale_db.REGIONS_PRESET)
        self.region_combo.setFixedWidth(160)
        reg_col.addWidget(self.region_combo)
        self._err_region = _inline_error("必填项未填写", self)
        reg_col.addWidget(self._err_region)
        row2.addLayout(reg_col)
        row2.addStretch(1)
        sec2.content_layout.addLayout(row2)
        # 球桌候选列表（默认隐藏，搜索命中后展示）
        self._cand_list = QListWidget(self)
        self._cand_list.setFixedHeight(132)
        self._cand_list.setVisible(False)
        self._cand_list.itemClicked.connect(self._on_candidate_clicked)
        _style_cand_list(self._cand_list)
        try:
            qconfig.themeChanged.connect(
                lambda: _style_cand_list(self._cand_list))
        except Exception:
            pass
        sec2.content_layout.addWidget(self._cand_list)
        # 关联确认条：选桌后展示桌号 + SNK，隐藏字段 snk_code 的可视反馈
        self._link_bar = QLabel(self)
        self._link_bar.setObjectName("aftersaleLinkBar")
        self._link_bar.setWordWrap(True)
        self._link_bar.setStyleSheet(
            "QLabel#aftersaleLinkBar { background: %s; color: %s;"
            " border-radius: 6px; padding: 5px 10px; font-size: 12px; }"
            % (_hex_rgba(SEMANTIC["success"], 26), SEMANTIC["success"]))
        self._link_bar.setVisible(False)
        sec2.content_layout.addWidget(self._link_bar)
        root.addWidget(sec2)

        # ---------- 板块三：问题描述 ----------
        sec3 = _SectionCard("问题描述", self)
        v3 = sec3.content_layout
        v3.addWidget(_field_label("问题", True, self))
        self.problem_combo = FluentCombo(self)
        self.problem_combo.setEditable(True)
        self.problem_combo.setInsertPolicy(_QComboBox.InsertPolicy.NoInsert)
        self.problem_combo.setFixedWidth(320)
        v3.addWidget(self.problem_combo)
        self._err_problem = _inline_error("必填项未填写", self)
        v3.addWidget(self._err_problem)
        # 发生原因 / 解决方案：等宽等高多行文本框并排（64px ≈ 3 行）
        txt_row = QHBoxLayout()
        txt_row.setSpacing(14)
        col_cause = QVBoxLayout()
        col_cause.setSpacing(5)
        col_cause.addWidget(_field_label("发生原因", False, self))
        self.cause_edit = PlainTextEdit(self)
        self.cause_edit.setFixedHeight(64)
        self.cause_edit.setPlaceholderText("选填")
        col_cause.addWidget(self.cause_edit)
        txt_row.addLayout(col_cause, 1)
        col_sol = QVBoxLayout()
        col_sol.setSpacing(5)
        col_sol.addWidget(_field_label("解决方案", False, self))
        self.solution_edit = PlainTextEdit(self)
        self.solution_edit.setFixedHeight(64)
        self.solution_edit.setPlaceholderText("选填，多行文本")
        col_sol.addWidget(self.solution_edit)
        txt_row.addLayout(col_sol, 1)
        v3.addLayout(txt_row)
        # 三个是/否判定：分段开关（默认值与旧版一致：是 / 否 / 是）
        seg_row = QHBoxLayout()
        seg_row.setSpacing(18)
        seg_row.addWidget(_field_label("是否解决", True, self))
        self.resolved_combo = YesNoSegment("是", self)
        seg_row.addWidget(self.resolved_combo)
        seg_row.addWidget(_field_label("我们主动发起", False, self))
        self.is_initiative_combo = YesNoSegment("否", self)
        seg_row.addWidget(self.is_initiative_combo)
        seg_row.addWidget(_field_label("是我们的问题", False, self))
        self.is_our_problem_combo = YesNoSegment("是", self)
        seg_row.addWidget(self.is_our_problem_combo)
        seg_row.addStretch(1)
        v3.addLayout(seg_row)
        # 解决人 / 响应时间：左右平分两列
        res_row = QHBoxLayout()
        res_row.setSpacing(16)
        col_res = QVBoxLayout()
        col_res.setSpacing(5)
        col_res.addWidget(_field_label("解决人", False, self))
        self.resolver_combo = FluentCombo(self)
        self.resolver_combo.setEditable(True)
        self.resolver_combo.setInsertPolicy(_QComboBox.InsertPolicy.NoInsert)
        self.resolver_combo.setFixedWidth(220)
        col_res.addWidget(self.resolver_combo)
        res_row.addLayout(col_res)
        col_resp = QVBoxLayout()
        col_resp.setSpacing(5)
        col_resp.addWidget(_field_label("响应时间", False, self))
        self.response_combo = FluentCombo(self)
        self.response_combo.setEditable(True)
        self.response_combo.setInsertPolicy(_QComboBox.InsertPolicy.NoInsert)
        self.response_combo.addItems(aftersale_db.RESPONSE_TIME_PRESET)
        self.response_combo.setFixedWidth(220)
        col_resp.addWidget(self.response_combo)
        res_row.addLayout(col_resp)
        res_row.addStretch(1)
        v3.addLayout(res_row)
        root.addWidget(sec3)

        self.first_error = None  # 最近一次校验的首个错误控件（供滚动聚焦）

    # ---------- 候选值加载 ----------

    def load_candidates(self, cands: dict):
        """填充动态候选（问题/解决人/地区），保留用户已输入文本"""
        cur_problem = self.problem_combo.currentText().strip()
        self.problem_combo.clear()
        self.problem_combo.addItems(cands.get("problems", []))
        if cur_problem:
            self.problem_combo.setEditText(cur_problem)
        cur_resolver = self.resolver_combo.currentText().strip()
        self.resolver_combo.clear()
        self.resolver_combo.addItems(cands.get("resolvers", []))
        if cur_resolver:
            self.resolver_combo.setEditText(cur_resolver)
        # 地区：预置 + 历史新增合并
        cur_region = self.region_combo.currentText().strip()
        regions = list(aftersale_db.REGIONS_PRESET)
        for r in cands.get("regions", []):
            if r not in regions:
                regions.append(r)
        self.region_combo.clear()
        self.region_combo.addItems(regions)
        if cur_region:
            self.region_combo.setEditText(cur_region)

    # ---------- 球房搜索与带出 ----------

    def _on_room_text_changed(self, text):
        """球房输入变动：清空旧桌号/SNK/带出城市，防抖后异步搜索球桌"""
        self._search_kw = str(text or "").strip()
        self._snk_code = ""  # 文本变动后旧的关联失效
        self.table_no_edit.clear()  # 旧桌号失效，防止错带到新球房
        self._set_linked(False)  # 旧关联确认条/带出芯片失效
        # 地区：仅当当前文本是上次带出的城市时联动清空（手填/改过的地区保留）
        if (self._last_city and
                self.region_combo.currentText().strip() == self._last_city):
            self.region_combo.setEditText("")
            self._last_city = ""
        if not self._search_kw:
            self._hide_candidates()
            return
        self._search_timer.start()

    def _do_room_search(self):
        """防抖后按球房名异步搜索球桌（含关键词快照，过期结果丢弃）"""
        kw = self._search_kw
        if not kw:
            return
        self._cand_worker = AftersaleDBWorker(
            table_db.query_tables_by_room, kw, 30)
        self._cand_worker.result_ready.connect(
            lambda result, k=kw: self._on_room_candidates(k, result))
        self._cand_worker.error.connect(lambda _m: self._hide_candidates())
        self._cand_worker.start()

    def _on_room_candidates(self, kw, result):
        if kw != self._search_kw:
            return  # 输入已变化，丢弃过期结果
        rows = result or []
        # 只命中唯一球桌：静默带出，不弹候选
        if len(rows) == 1:
            self._apply_table(rows[0])
            self._hide_candidates()
            return
        self._cand_list.clear()
        if not rows:
            # 无结果提示（不可选中，不干扰后续手填）
            item = QListWidgetItem(f"未找到「{kw}」的球桌，可直接填写")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._cand_list.addItem(item)
            self._cand_list.setVisible(True)
            return
        self._cand_rows = rows
        for r in rows:
            name = str(r.get("name") or "")
            room = str(r.get("roomName") or "")
            item = QListWidgetItem(f"{name} · {room}")
            item.setToolTip(f"桌号: {name}\n球房: {room}\nSNK: {r.get('snk_code') or ''}")
            self._cand_list.addItem(item)
        self._cand_list.setVisible(True)

    def _on_candidate_clicked(self, item):
        row_idx = self._cand_list.row(item)
        rows = getattr(self, "_cand_rows", [])
        if 0 <= row_idx < len(rows):
            self._apply_table(rows[row_idx])
        self._hide_candidates()

    def _apply_table(self, row):
        """选中球桌 → 带出桌号/球房/SNK/城市（球房阻断信号避免重复触发搜索）"""
        self.table_no_edit.setText(str(row.get("name") or ""))
        room = str(row.get("roomName") or "")
        self.room_edit.blockSignals(True)
        self.room_edit.setText(room)
        self.room_edit.blockSignals(False)
        self._snk_code = str(row.get("snk_code") or "")
        self._search_kw = room.strip()
        city = str(row.get("city") or "").strip()
        if city:
            self.region_combo.setEditText(city)
            self._last_city = city
        elif self.region_combo.currentText().strip() == self._last_city:
            # 新球桌城市未知（老库未采集）：清掉上一桌带出的残留城市，
            # 避免换球房后地区仍显示旧城市；手填的地区不受影响
            self.region_combo.setEditText("")
            self._last_city = ""
        self._set_linked(True, row)

    def _set_linked(self, on, row=None):
        """切换关联确认条（隐藏字段 snk_code 的可视反馈）"""
        if not on or not row:
            self._link_bar.setVisible(False)
            return
        city = str(row.get("city") or row.get("region") or "").strip()
        parts = ["已关联球桌：%s · %s 号桌" % (
            str(row.get("roomName") or ""), str(row.get("name") or ""))]
        if str(row.get("snk_code") or ""):
            parts.append("SNK: %s" % str(row.get("snk_code")))
        if city:
            parts.append("城市自动带出")
        self._link_bar.setText("✓　" + "　|　".join(parts))
        self._link_bar.setVisible(True)

    def _hide_candidates(self):
        self._cand_list.setVisible(False)

    def _step_occurred(self, delta_days: int):
        """发生时间步进：负数前移、正数后移；collect 时取 picker.date 新值"""
        self.occurred_picker.setDate(
            self.occurred_picker.date.addDays(delta_days))

    # ---------- 值读写 ----------

    def set_values(self, rec: dict):
        """编辑模式：用已有记录填充表单"""
        self.type_combo.setCurrentText(str(rec.get("issue_type") or ""))
        occurred = str(rec.get("occurred_at") or "").strip()
        occ_d = QDate.fromString(occurred, "yyyy-MM-dd")
        if occ_d.isValid():
            self.occurred_picker.setDate(occ_d)
        self.table_no_edit.setText(str(rec.get("table_no") or ""))
        room = str(rec.get("room_name") or "")
        self.room_edit.blockSignals(True)  # 回填不触发搜索/联动清空
        self.room_edit.setText(room)
        self.room_edit.blockSignals(False)
        self._search_kw = room.strip()
        self.region_combo.setEditText(str(rec.get("region") or ""))
        self.problem_combo.setEditText(str(rec.get("problem") or ""))
        self.cause_edit.setPlainText(str(rec.get("cause") or ""))
        self.resolved_combo.setValue(rec.get("resolved") or "是")
        self.is_initiative_combo.setValue(rec.get("is_initiative") or "否")
        self.is_our_problem_combo.setValue(rec.get("is_our_problem") or "是")
        self.solution_edit.setPlainText(str(rec.get("solution") or ""))
        self.resolver_combo.setEditText(str(rec.get("resolver") or ""))
        self.response_combo.setEditText(str(rec.get("response_time") or ""))
        self.creator_edit.setText(str(rec.get("creator") or ""))
        self._snk_code = str(rec.get("snk_code") or "")
        self._last_city = ""  # 编辑回填不参与城市联动
        self.clear_error()

    def collect(self) -> dict:
        """收集表单值为记录 dict（不含必填校验）"""
        return {
            "issue_type": self.type_combo.currentText().strip(),
            "occurred_at": self.occurred_picker.date.toString("yyyy-MM-dd"),
            "table_no": self.table_no_edit.text().strip(),
            "room_name": self.room_edit.text().strip(),
            "region": self.region_combo.currentText().strip(),
            "problem": self.problem_combo.currentText().strip(),
            "cause": self.cause_edit.toPlainText().strip(),
            "resolved": self.resolved_combo.value(),
            "is_initiative": self.is_initiative_combo.value(),
            "is_our_problem": self.is_our_problem_combo.value(),
            "solution": self.solution_edit.toPlainText().strip(),
            "resolver": self.resolver_combo.currentText().strip(),
            "response_time": self.response_combo.currentText().strip(),
            "creator": self.creator_edit.text().strip(),
            "snk_code": self._snk_code,
        }

    def _required_map(self):
        """必填字段 → (取值, 错误控件, 输入控件)，顺序即校验/聚焦顺序"""
        return [
            ("类型", not self.type_combo.currentText().strip(),
             self._err_type, self.type_combo),
            ("桌号", not self.table_no_edit.text().strip(),
             self._err_table, self.table_no_edit),
            ("球房", not self.room_edit.text().strip(),
             self._err_room, self.room_edit),
            ("地区", not self.region_combo.currentText().strip(),
             self._err_region, self.region_combo),
            ("问题", not self.problem_combo.currentText().strip(),
             self._err_problem, self.problem_combo),
        ]

    def validate(self) -> list:
        """必填校验：返回缺失字段中文名列表，并驱动字段级红框/内联提示

        同时记录 first_error（首个错误输入控件），供调用方滚动聚焦。
        """
        missing = []
        self.first_error = None
        for name, bad, err_lbl, ctl in self._required_map():
            err_lbl.setVisible(bad)
            if hasattr(ctl, "setError"):
                ctl.setError(bad)
            if bad:
                missing.append(name)
                if self.first_error is None:
                    self.first_error = ctl
        return missing

    def clear_error(self):
        """清除全部字段级错误态（输入恢复后由调用方触发）"""
        for _name, _bad, err_lbl, ctl in self._required_map():
            err_lbl.setVisible(False)
            if hasattr(ctl, "setError"):
                ctl.setError(False)
        self.first_error = None

    def clear_form(self):
        """清空表单（保留填写人与下拉候选）"""
        self.room_edit.clear()
        self.table_no_edit.clear()
        self.cause_edit.clear()
        self.solution_edit.clear()
        self.problem_combo.setEditText("")
        self.resolver_combo.setEditText("")
        self.response_combo.setEditText("")
        self.resolved_combo.setValue("是")
        self.is_initiative_combo.setValue("否")
        self.is_our_problem_combo.setValue("是")
        self.type_combo.setCurrentIndex(-1)
        self.occurred_picker.setDate(QDate.currentDate())  # 默认当日
        self.region_combo.setEditText("")
        self._snk_code = ""
        self._last_city = ""
        self._set_linked(False)
        self.clear_error()
        self._hide_candidates()
