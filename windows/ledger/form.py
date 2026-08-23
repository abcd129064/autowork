# -*- coding: utf-8 -*-
"""跑视频表单（录入页与编辑弹窗复用）

字段与在线模板.xlsx 数据 sheet 对齐：分类（sheet 名）→ 类别 → 球房 →
视频名 → 帧数 → 日期 → 描述 → 复现（是/否，需求6）→ 新程序 → 备注 → 署名。
分类切换时类别候选联动（模板解析预置 + 库中历史自由输入）。
「日期」为视频日期（需求7：看昨天的视频可指定昨天，默认当天）。
"""
from PySide6.QtCore import Qt, QTimer, QDate
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                               QLabel)

from qfluentwidgets import (LineEdit, PlainTextEdit, ScrollArea, CardWidget,
                            PrimaryPushButton, PushButton, BodyLabel,
                            CaptionLabel, FluentIcon, TableWidget,
                            ProgressBar, ZhDatePicker, ComboBox,
                            EditableComboBox)

from core.design_tokens import SEMANTIC
from core.utils import show_info_bar
from database import ledger_db
from workers.aftersale_worker import AftersaleDBWorker

from windows.ledger.common import *  # noqa: F401,F403

# 新程序取值（模板「新程序」列宽仅 8.5，是/否 标记）
NEW_PROGRAM_VALUES = ("", "是", "否")


class LedgerForm(QWidget):
    """跑视频表单：分类/类别/球房必填，其余选填；支持会话上下文预填"""

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
        self.category_combo = ComboBox(self)  # QFluentWidgets 原生（需求9）
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
        # 需求12：类别改用 qfluentwidgets EditableComboBox（可编辑 + 下拉候选，
        # 支持手输新类别，替代 FluentCombo 的原生 QComboBox 可编辑实现）
        self.kind_combo = EditableComboBox(self)
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
        # 日期：视频日期（需求7）——默认当天，看昨天的视频可翻到昨天再提交
        col_date = QVBoxLayout()
        col_date.setSpacing(3)
        col_date.addWidget(_field_label("日期", False, self))
        self.date_edit = ZhDatePicker(self)
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.setFixedWidth(125)
        col_date.addWidget(self.date_edit)
        row2.addLayout(col_date)
        row2.addStretch(1)
        sec1.content_layout.addLayout(row2)
        root.addWidget(sec1)

        # ---------- 板块二：描述与备注 ----------
        sec2 = _SectionCard("描述与备注", self)
        v2 = sec2.content_layout
        # 描述 / 备注：并排（70% / 30%，需求8；参照售后面板「发生原因/解决方案」
        # 等宽等高多行文本框并排范式，本处以 7:3 stretch 分配宽度）
        txt_desc = QHBoxLayout()
        txt_desc.setSpacing(14)
        col_desc = QVBoxLayout()
        col_desc.setSpacing(5)
        col_desc.addWidget(_field_label("描述", False, self))
        self.desc_edit = PlainTextEdit(self)
        self.desc_edit.setFixedHeight(64)
        self.desc_edit.setPlaceholderText("问题现象/使用场景描述")
        col_desc.addWidget(self.desc_edit)
        txt_desc.addLayout(col_desc, 7)
        col_remark = QVBoxLayout()
        col_remark.setSpacing(5)
        col_remark.addWidget(_field_label("备注", False, self))
        # 需求8：备注与描述并排等高（多行文本框，参照售后面板并排范式）
        self.remark_edit = PlainTextEdit(self)
        self.remark_edit.setFixedHeight(64)
        self.remark_edit.setPlaceholderText("备注")
        col_remark.addWidget(self.remark_edit)
        txt_desc.addLayout(col_remark, 3)
        v2.addLayout(txt_desc)
        self._repro_label = _field_label("复现", False, self)
        # 需求6：复现为「是/否」选择（售后面板同款 SegmentedWidget，默认「否」）；
        # 需求7：恒显（不再按分类隐藏，用户反馈录入界面需始终可见）
        self.repro_seg = YesNoSegment("否", self)
        self.repro_seg.setFixedWidth(110)
        self._new_program_label = _field_label("新程序", False, self)
        # 需求10：新程序与复现同款 SegmentedWidget（默认「否」）
        self.new_program_seg = YesNoSegment("否", self)
        self.new_program_seg.setFixedWidth(110)
        # 需求11：新程序与复现同列并排（两个是/否开关一行，紧凑展示）
        repro_row = QHBoxLayout()
        repro_row.setSpacing(10)
        repro_row.addWidget(self._repro_label)
        repro_row.addWidget(self.repro_seg)
        repro_row.addWidget(self._new_program_label)
        repro_row.addWidget(self.new_program_seg)
        repro_row.addStretch(1)
        v2.addLayout(repro_row)
        # 行：署名（新程序已上移与复现同列，需求11）
        txt_row = QHBoxLayout()
        txt_row.setSpacing(16)
        col_signer = QVBoxLayout()
        col_signer.setSpacing(3)
        col_signer.addWidget(_field_label("署名", False, self))
        self.signer_edit = LineEdit(self)
        self.signer_edit.setText(_default_creator())  # 默认取配置，可改
        self.signer_edit.setFixedWidth(120)
        col_signer.addWidget(self.signer_edit)
        txt_row.addLayout(col_signer)
        txt_row.addStretch(1)
        v2.addLayout(txt_row)
        root.addWidget(sec2)

        self.first_error = None  # 最近一次校验的首个错误控件（供滚动聚焦）
        self._on_category_changed(self.category_combo.currentText())

    # ---------- 分类联动 ----------

    def _on_category_changed(self, category: str):
        """分类切换：类别候选联动（复现选择恒显，见需求7）"""
        if not category:
            return
        self._load_kind_candidates(category)

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
        """类别候选填充：过期分类结果丢弃；保留用户已输入文本（EditableComboBox
        用 setText 替代 QComboBox 的 setEditText，并重建补全器）"""
        if self.category_combo.currentText() != category:
            return
        self.kind_combo.clear()
        cands = list(cands or ())
        self.kind_combo.addItems(cands)
        if keep_text:
            self.kind_combo.setText(keep_text)
        # 手输补全提示（EditableComboBox 为 LineEdit 子类，需显式挂补全器）
        try:
            from PySide6.QtWidgets import QCompleter
            self.kind_combo.setCompleter(QCompleter(cands, self.kind_combo))
        except Exception:
            pass

    # ---------- 校验与收集 ----------

    def validate(self, show_errors: bool = True) -> list:
        """必填校验：分类/类别/球房；返回缺失项名称列表

        show_errors=True（默认，提交校验）触发红框 + 设置 first_error 供滚动聚焦；
        show_errors=False（必填进度静默校验）仅返回 missing，不修改红框状态。
        """
        missing = []
        if show_errors:
            self._err_category.setVisible(False)
            self._err_kind.setVisible(False)
            self._err_room.setVisible(False)
            self.first_error = None
        if not self.category_combo.currentText().strip():
            missing.append("分类")
            if show_errors:
                self._err_category.setVisible(True)
                if self.first_error is None:
                    self.first_error = self.category_combo
        if not self.kind_combo.currentText().strip():
            missing.append("类别")
            if show_errors:
                self._err_kind.setVisible(True)
                if self.first_error is None:
                    self.first_error = self.kind_combo
        if not self.room_edit.text().strip():
            missing.append("球房")
            if show_errors:
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
            # 日期：视频日期（需求7），ZhDatePicker.date 为 QDate 属性
            "occurred_at": self.date_edit.date.toString("yyyy-MM-dd"),
            "description": self.desc_edit.toPlainText().strip(),
            "repro": self.repro_seg.value().strip(),
            "new_program": self.new_program_seg.value().strip(),
            "remark": self.remark_edit.toPlainText().strip(),
            "signer": self.signer_edit.text().strip(),
        }

    def clear_form(self):
        """清空表单（保留分类与署名默认值）"""
        self.kind_combo.setText("")  # EditableComboBox 用 setText 清空
        self.room_edit.clear()
        self.video_edit.clear()
        self.frame_edit.clear()
        self.date_edit.setDate(QDate.currentDate())  # 日期重置当天
        self.desc_edit.clear()
        self.repro_seg.setValue("否")  # 复现默认「否」（seg 无 clear，回默认）
        self.new_program_seg.setValue("否")  # 新程序默认「否」（seg 无 clear，回默认）
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
        self.kind_combo.setText(str(record.get("kind") or ""))  # setText 回显
        self.room_edit.setText(str(record.get("room_name") or ""))
        self.video_edit.setText(str(record.get("video_name") or ""))
        self.frame_edit.setText(str(record.get("frame") or ""))
        # 日期回显：occurred_at（YYYY-MM-DD 或带时间），非法/空回退当天
        occ = str(record.get("occurred_at") or "")[:10]
        qd = QDate.fromString(occ, "yyyy-MM-dd")
        if qd.isValid():
            self.date_edit.setDate(qd)
        else:
            self.date_edit.setDate(QDate.currentDate())
        self.desc_edit.setPlainText(str(record.get("description") or ""))
        # 复现：历史数据为描述文本（旧版文本框遗留）时 setValue 回退「否」
        self.repro_seg.setValue(str(record.get("repro") or ""))
        # 新程序：seg 回显（setValue 兼容「是/否」，其余回退「否」）
        self.new_program_seg.setValue(str(record.get("new_program") or ""))
        self.remark_edit.setPlainText(str(record.get("remark") or ""))
        self.signer_edit.setText(str(record.get("signer") or ""))
