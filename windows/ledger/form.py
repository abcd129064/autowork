# -*- coding: utf-8 -*-
"""台账表单（录入页与编辑弹窗复用）

字段与在线模板.xlsx 数据 sheet 对齐：分类（sheet 名）→ 类别 → 球房 →
视频名 → 帧数 → 描述 → 复现（精度/使用）→ 新程序 → 备注 → 署名。
分类切换时类别候选联动（模板解析预置 + 库中历史自由输入），
「复现」输入仅在 精度/使用 分类显示。
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                               QLabel, QComboBox as _QComboBox)

from qfluentwidgets import (LineEdit, PlainTextEdit, ScrollArea, CardWidget,
                            PrimaryPushButton, PushButton, BodyLabel,
                            CaptionLabel, FluentIcon, TableWidget,
                            ProgressBar)

from core.design_tokens import SEMANTIC
from core.utils import show_info_bar
from database import ledger_db
from workers.aftersale_worker import AftersaleDBWorker

from windows.ledger.common import *  # noqa: F401,F403

# 新程序取值（模板「新程序」列宽仅 8.5，是/否 标记）
NEW_PROGRAM_VALUES = ("", "是", "否")


class LedgerForm(QWidget):
    """台账表单：分类/类别/球房必填，其余选填；支持会话上下文预填"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cand_worker = None
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # ---------- 板块一：分类与基础信息 ----------
        sec1 = _SectionCard("分类与基础信息", self)
        row1 = QHBoxLayout()
        row1.setSpacing(16)
        col_cat = QVBoxLayout()
        col_cat.setSpacing(3)
        col_cat.addWidget(_field_label("分类", True, self))
        self.category_combo = FluentCombo(self)
        self.category_combo.addItems(ledger_db.CATEGORIES)
        self.category_combo.setFixedWidth(130)
        self.category_combo.currentTextChanged.connect(
            self._on_category_changed)
        col_cat.addWidget(self.category_combo)
        self._err_category = _inline_error("必填项未填写", self)
        col_cat.addWidget(self._err_category)
        row1.addLayout(col_cat)
        col_kind = QVBoxLayout()
        col_kind.setSpacing(3)
        col_kind.addWidget(_field_label("类别", True, self))
        self.kind_combo = FluentCombo(self)
        self.kind_combo.setEditable(True)
        self.kind_combo.setInsertPolicy(_QComboBox.InsertPolicy.NoInsert)
        self.kind_combo.setFixedWidth(320)
        col_kind.addWidget(self.kind_combo)
        self._err_kind = _inline_error("必填项未填写", self)
        col_kind.addWidget(self._err_kind)
        row1.addLayout(col_kind)
        row1.addStretch(1)
        sec1.content_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(16)
        col_room = QVBoxLayout()
        col_room.setSpacing(3)
        col_room.addWidget(_field_label("球房", True, self))
        self.room_edit = LineEdit(self)
        self.room_edit.setPlaceholderText("设备代码/球房名")
        self.room_edit.setFixedWidth(160)
        col_room.addWidget(self.room_edit)
        self._err_room = _inline_error("必填项未填写", self)
        col_room.addWidget(self._err_room)
        row2.addLayout(col_room)
        col_video = QVBoxLayout()
        col_video.setSpacing(3)
        col_video.addWidget(_field_label("视频名", False, self))
        self.video_edit = LineEdit(self)
        self.video_edit.setPlaceholderText("当前视频/日志文件名")
        self.video_edit.setFixedWidth(300)
        col_video.addWidget(self.video_edit)
        row2.addLayout(col_video)
        col_frame = QVBoxLayout()
        col_frame.setSpacing(3)
        col_frame.addWidget(_field_label("帧数", False, self))
        self.frame_edit = LineEdit(self)
        self.frame_edit.setPlaceholderText("400")
        self.frame_edit.setFixedWidth(80)
        col_frame.addWidget(self.frame_edit)
        row2.addLayout(col_frame)
        row2.addStretch(1)
        sec1.content_layout.addLayout(row2)
        root.addWidget(sec1)

        # ---------- 板块二：描述与备注 ----------
        sec2 = _SectionCard("描述与备注", self)
        v2 = sec2.content_layout
        v2.addWidget(_field_label("描述", False, self))
        self.desc_edit = PlainTextEdit(self)
        self.desc_edit.setFixedHeight(64)
        self.desc_edit.setPlaceholderText("问题现象/使用场景描述")
        v2.addWidget(self.desc_edit)
        self._repro_label = _field_label("复现", False, self)
        v2.addWidget(self._repro_label)
        self.repro_edit = PlainTextEdit(self)
        self.repro_edit.setFixedHeight(56)
        self.repro_edit.setPlaceholderText("复现步骤/精度现象（仅精度/使用分类）")
        v2.addWidget(self.repro_edit)
        txt_row = QHBoxLayout()
        txt_row.setSpacing(16)
        col_new = QVBoxLayout()
        col_new.setSpacing(3)
        col_new.addWidget(_field_label("新程序", False, self))
        self.new_program_combo = FluentCombo(self)
        self.new_program_combo.addItems(["是", "否"])
        self.new_program_combo.setFixedWidth(90)
        col_new.addWidget(self.new_program_combo)
        txt_row.addLayout(col_new)
        col_remark = QVBoxLayout()
        col_remark.setSpacing(3)
        col_remark.addWidget(_field_label("备注", False, self))
        self.remark_edit = LineEdit(self)
        self.remark_edit.setPlaceholderText("备注")
        col_remark.addWidget(self.remark_edit)
        txt_row.addLayout(col_remark, 1)
        col_signer = QVBoxLayout()
        col_signer.setSpacing(3)
        col_signer.addWidget(_field_label("署名", False, self))
        self.signer_edit = LineEdit(self)
        self.signer_edit.setText(_default_creator())  # 默认取配置，可改
        self.signer_edit.setFixedWidth(120)
        col_signer.addWidget(self.signer_edit)
        txt_row.addLayout(col_signer)
        v2.addLayout(txt_row)
        root.addWidget(sec2)

        self.first_error = None  # 最近一次校验的首个错误控件（供滚动聚焦）
        self._on_category_changed(self.category_combo.currentText())

    # ---------- 分类联动 ----------

    def _on_category_changed(self, category: str):
        """分类切换：类别候选联动 + 「复现」输入仅在 精度/使用 显示"""
        if not category:
            return
        self._load_kind_candidates(category)
        show_repro = category in ("精度", "使用")
        self._repro_label.setVisible(show_repro)
        self.repro_edit.setVisible(show_repro)

    def _load_kind_candidates(self, category: str):
        """异步拉取类别候选（模板预置 + 历史自由输入），保留用户已输入文本"""
        cur = self.kind_combo.currentText().strip()
        self._cand_worker = AftersaleDBWorker(
            ledger_db.get_kind_candidates, category)
        self._cand_worker.result_ready.connect(
            lambda cands, k=category, c=cur: self._on_kind_candidates(
                k, cands, c))
        self._cand_worker.error.connect(
            lambda _m: self._on_kind_candidates(
                category, ledger_db.KIND_CANDIDATES.get(category, ()), cur))
        self._cand_worker.start()

    def _on_kind_candidates(self, category, cands, keep_text):
        """类别候选填充：过期分类结果丢弃；保留用户已输入文本"""
        if self.category_combo.currentText() != category:
            return
        self.kind_combo.clear()
        self.kind_combo.addItems(list(cands or ()))
        if keep_text:
            self.kind_combo.setEditText(keep_text)

    # ---------- 校验与收集 ----------

    def validate(self) -> list:
        """必填校验：分类/类别/球房；返回缺失项名称列表（并触发红框）"""
        missing = []
        self._err_category.setVisible(False)
        self._err_kind.setVisible(False)
        self._err_room.setVisible(False)
        self.first_error = None
        if not self.category_combo.currentText().strip():
            missing.append("分类")
            self._err_category.setVisible(True)
            if self.first_error is None:
                self.first_error = self.category_combo
        if not self.kind_combo.currentText().strip():
            missing.append("类别")
            self._err_kind.setVisible(True)
            if self.first_error is None:
                self.first_error = self.kind_combo
        if not self.room_edit.text().strip():
            missing.append("球房")
            self._err_room.setVisible(True)
            if self.first_error is None:
                self.first_error = self.room_edit
        return missing

    def collect(self) -> dict:
        """收集表单为记录 dict（与 ledger_db.insert_record 入参一致）"""
        frame = self.frame_edit.text().strip()
        return {
            "category": self.category_combo.currentText().strip(),
            "kind": self.kind_combo.currentText().strip(),
            "room_name": self.room_edit.text().strip(),
            "video_name": self.video_edit.text().strip(),
            "frame": frame,
            "description": self.desc_edit.toPlainText().strip(),
            "repro": self.repro_edit.toPlainText().strip(),
            "new_program": self.new_program_combo.currentText().strip(),
            "remark": self.remark_edit.text().strip(),
            "signer": self.signer_edit.text().strip(),
        }

    def clear_form(self):
        """清空表单（保留分类与署名默认值）"""
        self.kind_combo.setEditText("")
        self.room_edit.clear()
        self.video_edit.clear()
        self.frame_edit.clear()
        self.desc_edit.clear()
        self.repro_edit.clear()
        self.new_program_combo.setCurrentIndex(0)
        self.remark_edit.clear()

    # ---------- 预填 / 回显 ----------

    def prefill(self, ctx: dict):
        """按主界面会话上下文预填（只填空值，不覆盖已输入内容）"""
        if not self.room_edit.text().strip():
            self.room_edit.setText(str(ctx.get("room_name", "") or ""))
        if not self.video_edit.text().strip():
            self.video_edit.setText(str(ctx.get("video_name", "") or ""))
        if not self.frame_edit.text().strip():
            self.frame_edit.setText(str(ctx.get("frame", "") or ""))
        if not self.signer_edit.text().strip():
            self.signer_edit.setText(str(ctx.get("signer", "") or ""))
        category = str(ctx.get("category", "") or "")
        if category in ledger_db.CATEGORIES:
            self.category_combo.setCurrentText(category)

    def set_values(self, record: dict):
        """编辑回显：按记录 dict 填充全部字段"""
        category = str(record.get("category") or "问题")
        if category in ledger_db.CATEGORIES:
            self.category_combo.setCurrentText(category)
        self.kind_combo.setEditText(str(record.get("kind") or ""))
        self.room_edit.setText(str(record.get("room_name") or ""))
        self.video_edit.setText(str(record.get("video_name") or ""))
        self.frame_edit.setText(str(record.get("frame") or ""))
        self.desc_edit.setPlainText(str(record.get("description") or ""))
        self.repro_edit.setPlainText(str(record.get("repro") or ""))
        np_ = str(record.get("new_program") or "")
        if np_ in ("是", "否"):
            self.new_program_combo.setCurrentText(np_)
        self.remark_edit.setText(str(record.get("remark") or ""))
        self.signer_edit.setText(str(record.get("signer") or ""))
