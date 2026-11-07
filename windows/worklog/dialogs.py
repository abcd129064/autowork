# -*- coding: utf-8 -*-
"""工作留痕录入对话框（稿 642-674）：新增 / 编辑 + 连续录入

连续录入的语义照抄售后面板 EditRecordDialog(continuous=True) 的先例：
保存成功不关窗，只清掉「这一条」的字段等下一条；编辑已有记录时即使调用方
传了 continuous 也必须保存即关窗，否则用户会以为没存上而重复录入。

与先例的两处不同（稿与先例冲突时以稿为准）：
* 底部是三个按钮且「保存并继续录入」靠左（稿 672-674），MessageBoxBase 的
  按钮盒给不了这个排布，所以本对话框是裸 QDialog 自排头/身/尾三段；
* 尺寸取 DIALOG_SIZE 的 S 档宽 480，高度按内容撑开（8 个字段行加头尾约
  460px，S 档的 380 高装不下，硬压会把备注行裁掉）。

成员 / 分类 / 项目候选由调用方传入：对话框自己不查库（§4.2 的读也要走
worker，页面已经用 controller 拉过一份 options，复用即可）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QWidget,
                               QLabel)
from qfluentwidgets import (ComboBox, EditableComboBox, LineEdit, PushButton,
                            PrimaryPushButton, Slider, CaptionLabel,
                            TitleLabel, FluentIcon, ZhDatePicker)

from core.design_tokens import DIALOG_SIZE, SPACE, TYPE_SCALE_PX, FONT_WEIGHTS
from core.spin_fit_patch import FittedDoubleSpinBox
from core.utils import show_info_bar
from database import worklog_db
from workers.worklog_worker import spawn
from windows.worklog.common import (DateBox, Hairline, accent_press,
                                    accent_soft, px_font, text_color_tier,
                                    CATEGORIES, STATUSES)

# 稿 216 的 .fld>label 固定宽 76
_LABEL_W = 76
# 稿 224 的 .prog-line .pv 固定宽 40
_PCT_W = 40


class _FieldRow(QWidget):
    """一个字段行（稿 213-218）：76px 标签 + 弹性控件区，行间距 11"""

    def __init__(self, label: str, parent=None, required: bool = False,
                 top_align: bool = False):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE[3])            # 稿 .fld 的 gap:12
        lbl = QLabel(label + (" *" if required else ""), self)
        lbl.setFixedWidth(_LABEL_W)
        lbl.setFont(px_font(TYPE_SCALE_PX["base"]))
        if top_align:
            # 稿 228：备注标签与输入框首行对齐（padding-top:6px）
            lbl.setContentsMargins(0, 6, 0, 0)
            lbl.setAlignment(Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignTop)
        else:
            lbl.setAlignment(Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(lbl)
        self.ctl = QHBoxLayout()
        self.ctl.setContentsMargins(0, 0, 0, 0)
        self.ctl.setSpacing(SPACE[2])       # 稿 .fld .ctl 的 gap:10
        row.addLayout(self.ctl, 1)


class EditWorklogDialog(QDialog):
    """新增 / 编辑一条工作记录；continuous=True 时保存后留窗等下一条"""

    def __init__(self, record: dict = None, parent=None,
                 continuous: bool = False, on_saved=None,
                 members: list = None, categories: list = None,
                 projects: list = None):
        super().__init__(parent)
        self._record = dict(record or {})
        # 连续录入只适用于新增：带 id 的是已有记录，保存即关窗
        self._continuous = bool(continuous) and not self._record.get("id")
        self._busy = False
        self._save_worker = None
        self._on_saved_cb = on_saved
        self.setWindowTitle("编辑工作记录" if self._record.get("id")
                            else "新增工作记录")
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._build_head(root)
        self._build_body(root)
        self._build_foot(root)

        self._fill_options(members or [], categories or [], projects or [])
        self.set_values(self._record)

        w, h = DIALOG_SIZE["S"]
        # 宽度照 S 档；高度取内容所需与 S 档的较大值（8 行字段压不进 380）
        self.resize(w, max(h, self.sizeHint().height()))

    # ---------------- 三段布局 ----------------

    def _build_head(self, root):
        """稿 214-215：padding 16/20/10，16px/700 标题 + 连续录入标签靠右"""
        head = QWidget(self)
        lay = QHBoxLayout(head)
        lay.setContentsMargins(SPACE[4], SPACE[4], SPACE[4], SPACE[2])
        lay.setSpacing(SPACE[2])
        lay.addWidget(TitleLabel(self.windowTitle(), head))
        if self._continuous:
            tag = QLabel("连续录入", head)
            tag.setFont(px_font(TYPE_SCALE_PX["sm"], FONT_WEIGHTS["semibold"]))
            tag.setStyleSheet(
                "QLabel { color: %s; background: %s; border-radius: 4px; "
                "padding: 1px 7px; }" % (accent_press(), accent_soft()))
            lay.addWidget(tag)
        lay.addStretch(1)
        root.addWidget(head)

    def _build_body(self, root):
        """稿 215 的 .dlg-body：padding 6/20/16，字段行间距 11"""
        body = QWidget(self)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(SPACE[4], SPACE[1], SPACE[4], SPACE[4])
        lay.setSpacing(SPACE[3])

        r = _FieldRow("成员", body, required=True)
        self.cbo_member = ComboBox(body)
        r.ctl.addWidget(self.cbo_member, 1)
        lay.addWidget(r)

        r = _FieldRow("日期", body, required=True)
        # 稿 649 点名 ZhDatePicker；工具栏那处用 CalendarPicker（按钮态）
        self.date_box = DateBox(body, steps=False, picker_cls=ZhDatePicker)
        r.ctl.addWidget(self.date_box, 1)
        lay.addWidget(r)

        r = _FieldRow("工作内容", body, required=True)
        self.edt_title = LineEdit(body)
        self.edt_title.setPlaceholderText("做了什么，一句话讲清")
        r.ctl.addWidget(self.edt_title, 1)
        lay.addWidget(r)

        r = _FieldRow("项目", body)
        # 可编辑组合框：历史项目名记下来，老项目不用每次重敲
        self.cbo_project = EditableComboBox(body)
        r.ctl.addWidget(self.cbo_project, 1)
        lay.addWidget(r)

        r = _FieldRow("分类 / 状态", body)
        self.cbo_category = ComboBox(body)
        self.cbo_status = ComboBox(body)
        r.ctl.addWidget(self.cbo_category, 1)
        r.ctl.addSpacing(SPACE[1])          # 稿 220：两框之间 gap 12
        r.ctl.addWidget(self.cbo_status, 1)
        lay.addWidget(r)

        r = _FieldRow("工时(h)", body)
        # 0.5 步进是小数，qfw SpinBox 无 setDecimals，只能落在 DoubleSpinBox 上
        self.spin_hours = FittedDoubleSpinBox(body, sample="24.0")
        self.spin_hours.setRange(0, 24)
        self.spin_hours.setDecimals(1)
        self.spin_hours.setSingleStep(0.5)
        self.spin_hours.refit(90)           # 稿 221：宽 90，禁 setFixedWidth 宽区间
        r.ctl.addWidget(self.spin_hours)
        note = CaptionLabel("步进 0.5 · 上限 24", body)
        note.setFont(px_font(TYPE_SCALE_PX["sm"]))
        note.setTextColor(*text_color_tier(3))
        r.ctl.addWidget(note)
        r.ctl.addStretch(1)
        lay.addWidget(r)

        r = _FieldRow("完成进度", body)
        self.slider = Slider(body)
        self.slider.setOrientation(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(0)
        r.ctl.addWidget(self.slider, 1)
        self.lbl_pct = QLabel("0%", body)
        self.lbl_pct.setFixedWidth(_PCT_W)
        self.lbl_pct.setAlignment(Qt.AlignmentFlag.AlignRight
                                  | Qt.AlignmentFlag.AlignVCenter)
        font = px_font(TYPE_SCALE_PX["base"], FONT_WEIGHTS["bold"])
        self.lbl_pct.setFont(font)
        self.lbl_pct.setStyleSheet("color: %s;" % accent_press())
        r.ctl.addWidget(self.lbl_pct)
        self.slider.valueChanged.connect(self._on_progress)
        lay.addWidget(r)

        r = _FieldRow("备注", body, top_align=True)
        self.edt_note = LineEdit(body)
        self.edt_note.setPlaceholderText("做了什么、结果如何、遗留问题…")
        r.ctl.addWidget(self.edt_note, 1)
        lay.addWidget(r)

        root.addWidget(body, 1)

    def _build_foot(self, root):
        """稿 229-231：1px 上沿 + 白底，「保存并继续」靠左、取消/保存靠右"""
        root.addWidget(Hairline(self))
        foot = QWidget(self)
        lay = QHBoxLayout(foot)
        lay.setContentsMargins(SPACE[4], SPACE[3], SPACE[4], SPACE[3])
        lay.setSpacing(SPACE[2])
        self.btn_continue = PrimaryPushButton(FluentIcon.SAVE, "保存并继续录入",
                                              foot)
        self.btn_continue.clicked.connect(lambda _=False: self._submit(True))
        lay.addWidget(self.btn_continue)
        lay.addStretch(1)                   # 稿 .dlg-foot 里它 margin-right:auto
        self.btn_cancel = PushButton("取消", foot)
        self.btn_cancel.clicked.connect(self.reject)
        lay.addWidget(self.btn_cancel)
        self.btn_save = PrimaryPushButton(FluentIcon.SAVE, "保存", foot)
        self.btn_save.clicked.connect(lambda _=False: self._submit(False))
        lay.addWidget(self.btn_save)
        # 编辑模式没有「继续」语义：留着只会让人犹豫点哪个
        self.btn_continue.setVisible(self._continuous)
        root.addWidget(foot)

    # ---------------- 取值 / 填值 ----------------

    def _fill_options(self, members, categories, projects):
        self._members = list(members)
        self.cbo_member.clear()
        for m in self._members:
            self.cbo_member.addItem(m.get("alias") or m.get("name") or "",
                                    userData=m.get("id"))
        cats = [c.get("name") if isinstance(c, dict) else str(c)
                for c in categories] or list(CATEGORIES)
        self.cbo_category.clear()
        self.cbo_category.addItems(cats)
        self.cbo_status.clear()
        self.cbo_status.addItems(list(STATUSES))
        self.cbo_project.clear()
        self.cbo_project.addItems([str(p) for p in projects])

    def set_values(self, record: dict):
        rec = record or {}
        mid = rec.get("member_id")
        idx = self.cbo_member.findData(mid) if mid else -1
        self.cbo_member.setCurrentIndex(max(0, idx))
        if rec.get("work_date"):
            from windows.worklog.common import parse_iso
            d = parse_iso(rec["work_date"])
            if d.isValid():
                self.date_box.setDate(d)
        self.edt_title.setText(str(rec.get("title") or ""))
        if rec.get("project"):
            self.cbo_project.setCurrentText(str(rec["project"]))
        if rec.get("category"):
            i = self.cbo_category.findText(str(rec["category"]))
            self.cbo_category.setCurrentIndex(max(0, i))
        if rec.get("status"):
            i = self.cbo_status.findText(str(rec["status"]))
            self.cbo_status.setCurrentIndex(max(0, i))
        self.spin_hours.setValue(float(rec.get("hours") or 0))
        # 直接设 slider 会触发 _on_progress 把状态改成已完成，先断开再恢复
        self.slider.valueChanged.disconnect(self._on_progress)
        self.slider.setValue(int(rec.get("progress") or 0))
        self.slider.valueChanged.connect(self._on_progress)
        self._on_progress(self.slider.value())
        self.edt_note.setText(str(rec.get("note") or ""))

    def collect(self) -> dict:
        return {
            "id": self._record.get("id"),
            "member_id": self.cbo_member.currentData() or 0,
            "work_date": self.date_box.date().toString("yyyy-MM-dd"),
            "title": self.edt_title.text().strip(),
            "project": self.cbo_project.currentText().strip(),
            "category": self.cbo_category.currentText().strip(),
            "status": self.cbo_status.currentText().strip(),
            "hours": self.spin_hours.value(),
            "progress": self.slider.value(),
            "note": self.edt_note.text().strip(),
        }

    def clear_form(self):
        """连续录入的清场：保留成员/日期/项目/分类，清掉内容与工时（稿 674）"""
        self.edt_title.clear()
        self.edt_note.clear()
        self.spin_hours.setValue(0)
        self.slider.valueChanged.disconnect(self._on_progress)
        self.slider.setValue(0)
        self.slider.valueChanged.connect(self._on_progress)
        self._on_progress(0)
        i = self.cbo_status.findText("进行中")
        self.cbo_status.setCurrentIndex(max(0, i))
        self.edt_title.setFocus()

    # ---------------- 校验与落库 ----------------

    def _on_progress(self, value):
        self.lbl_pct.setText("%d%%" % value)
        # 稿 668：拖到 100% 自动落「已完成」；往回拖不反向改状态
        if value >= 100:
            i = self.cbo_status.findText("已完成")
            if i >= 0 and self.cbo_status.currentIndex() != i:
                self.cbo_status.setCurrentIndex(i)

    def validate(self):
        """必填校验，返回缺失项名单；空名单即通过

        做成独立方法而不是塞进提交槽：连续录入与单次保存都要先过这一关，
        校验失败只提示并聚焦，不改窗口状态，用户输入不丢。
        """
        missing = []
        if not self.cbo_member.currentData():
            missing.append(("成员", self.cbo_member))
        if not self.date_box.date().isValid():
            missing.append(("日期", self.date_box.picker))
        if not self.edt_title.text().strip():
            missing.append(("工作内容", self.edt_title))
        if missing:
            show_info_bar("请先填写必填项: %s"
                          % "、".join(m[0] for m in missing), "warning",
                          title="无法保存", parent=self, duration=3000)
            missing[0][1].setFocus()
        return [m[0] for m in missing]

    def _submit(self, continue_after: bool):
        if self._busy:
            return
        if self.validate():
            return
        record = self.collect()
        # 编辑走 update、新增走 insert；两条路都异步，落库期间锁住三个按钮
        self._busy = True
        for btn in (self.btn_continue, self.btn_cancel, self.btn_save):
            btn.setEnabled(False)
        if record.get("id"):
            func, args = worklog_db.update_record, (record["id"], record)
        else:
            func, args = worklog_db.insert_record, (record,)
        self._save_worker = spawn(
            func,
            lambda payload, _c=continue_after: self._on_saved(payload, _c),
            self._on_save_error, *args)

    def _on_saved(self, payload, continue_after):
        self._busy = False
        for btn in (self.btn_continue, self.btn_cancel, self.btn_save):
            btn.setEnabled(True)
        if not continue_after:
            self._notify()
            self.accept()
            return
        show_info_bar("已保存，可继续录入下一条", "success", title="连续录入",
                      parent=self, duration=2500)
        self._notify()
        self.clear_form()

    def _on_save_error(self, msg):
        # 失败也必须复位按钮，否则用户会被永久锁在无法保存的状态
        self._busy = False
        for btn in (self.btn_continue, self.btn_cancel, self.btn_save):
            btn.setEnabled(True)
        show_info_bar(msg, "error", title="保存失败", parent=self, duration=4000)

    def _notify(self):
        if self._on_saved_cb is None:
            return
        try:
            self._on_saved_cb()
        except Exception:
            pass


__all__ = ["EditWorklogDialog"]
