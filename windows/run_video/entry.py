# -*- coding: utf-8 -*-
"""跑视频面板填写录入页：表单 + 必填进度 + 提交/清空。

与售后录入页共用 LedgerForm 与进度条交互，差异只在数据表与文案；
页头标题默认隐藏，交给面板外壳统一展示。
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout

from qfluentwidgets import (ScrollArea, TitleLabel, CaptionLabel,
                            ProgressBar, PushButton, PrimaryPushButton,
                            FluentIcon)

from core.design_tokens import SEMANTIC
from core.utils import show_info_bar
from database import ledger_db
from workers.aftersale_worker import AftersaleDBWorker

from windows.run_video.common import *  # noqa: F401,F403
from windows.run_video.form import LedgerForm


class EntryPage(QWidget):
    """填写录入页：页头 + 表单 + 随内容操作条。

    saved 信号在提交成功后发出，由面板转发给记录页触发刷新；
    本页不直接持有记录页引用，避免两个页面互相引用。
    """

    saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._save_worker = None
        self._init_ui()

    def _init_ui(self):
        """搭骨架：页头、可滚表单区、底部进度与按钮条。

        表单放进 ScrollArea 是为了小窗口下仍能滚到操作条；操作条
        放在滚动区外，保证提交按钮始终可见。
        """
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 12)
        root.setSpacing(10)

        # 页头
        head = QHBoxLayout()
        head.setSpacing(12)
        head_l = QVBoxLayout()
        head_l.setSpacing(2)
        # head_l.addWidget(TitleLabel("填写录入", self))
        # head_l.addWidget(CaptionLabel(
        #     "提交后写入数据库，"
        #     "多人协作刷新可见", self))
        head.addLayout(head_l)
        head.addStretch(1)
        root.addLayout(head)

        # 表单滚动区
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }")
        view = QWidget()
        view.setStyleSheet("QWidget { background: transparent; }")
        scroll.setWidget(view)
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(12)
        self.form = LedgerForm(view)
        layout.addWidget(self.form)

        # 操作条：必填进度 + 清空/提交
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self._prog = ProgressBar(view)
        self._prog.setRange(0, 3)
        self._prog.setValue(0)
        self._prog.setTextVisible(False)
        self._prog.setFixedWidth(72)
        bar.addWidget(self._prog)
        self._prog_text = CaptionLabel("必填项 0/3 已填写", view)
        bar.addWidget(self._prog_text)
        bar.addStretch(1)
        self._btn_clear = PushButton(FluentIcon.DELETE, "清空", view)
        self._btn_clear.setToolTip("清空表单全部内容")
        self._btn_clear.setFixedHeight(34)
        self._btn_clear.clicked.connect(self._on_clear)
        bar.addWidget(self._btn_clear)
        self._btn_submit = PrimaryPushButton(FluentIcon.ACCEPT, "提交记录", view)
        self._btn_submit.setToolTip("校验必填项后写入数据库")
        self._btn_submit.setFixedHeight(34)
        self._btn_submit.clicked.connect(self._on_submit)
        bar.addWidget(self._btn_submit)
        layout.addLayout(bar)
        layout.addStretch(1)
        root.addWidget(scroll, 1)
        self._scroll = scroll

        # 必填进度联动（需求12：kind_combo 已为 EditableComboBox（LineEdit 子类），
        # 手输触发 textChanged 而非 QComboBox 的 editTextChanged）
        f = self.form
        for sig in (f.kind_combo.currentTextChanged,
                    f.kind_combo.textChanged,
                    f.room_edit.textChanged):
            try:
                sig.connect(self._update_required_progress)
            except Exception:
                pass

    # ---------- 必填进度 ----------

    def _update_required_progress(self, *_a):
        """静默校验三项必填并刷新进度条与文案，不弹红框。

        用户还在填的时候就报错会打断输入，所以这里只做提示；
        真正拦截在提交时，由 _on_submit 统一给出错误项。
        """
        bad = self.form.validate(show_errors=False)
        done = 3 - len(bad)
        self._prog.setValue(done)
        # chunk 颜色必须整段重设样式表，只改文字颜色不影响填充色
        if not bad:
            self._prog_text.setText("必填项 3/3 已填齐")
            self._prog_text.setStyleSheet("color: %s;" % SEMANTIC["success"])
            self._prog.setStyleSheet(
                "QProgressBar { background: %s; border: none;"
                " border-radius: 2px; } QProgressBar::chunk {"
                " background: %s; border-radius: 2px; }" % (
                    _hex_rgba(SEMANTIC["success"], 18),
                    SEMANTIC["success"]))
        else:
            self._prog_text.setText("还差 %d 项：%s" % (
                len(bad), "、".join(bad)))
            self._prog_text.setStyleSheet("color: %s;" % SEMANTIC["warning"])
            self._prog.setStyleSheet(
                "QProgressBar { background: %s; border: none;"
                " border-radius: 2px; } QProgressBar::chunk {"
                " background: %s; border-radius: 2px; }" % (
                    _hex_rgba(SEMANTIC["warning"], 18),
                    SEMANTIC["warning"]))

    def _on_clear(self):
        """清空表单后立即重算进度。

        不重算的话进度条会停在上一份数据上，用户以为还有内容没清掉。
        """
        self.form.clear_form()
        self._update_required_progress()

    # ---------- 提交 ----------

    def _on_submit(self):
        """校验必填后后台写库，成功再清表单。

        校验失败时滚动到首个错误控件并聚焦，长表单里用户才知道
        该改哪里；写库期间禁用按钮，防止连点产生重复记录。
        """
        missing = self.form.validate()
        if missing:
            show_info_bar(f"请先填写必填项: {'、'.join(missing)}", "warning",
                          title="无法提交", parent=self, duration=3000)
            if self.form.first_error is not None:
                self.form.first_error.setFocus()
                self._scroll.ensureWidgetVisible(
                    self.form.first_error, 0, 90)
            return
        # 上一笔还在写库时吞掉点击，连点会插两条相同记录
        if self._save_worker and self._save_worker.isRunning():
            return
        self._btn_submit.setEnabled(False)
        record = self.form.collect()
        self._save_worker = AftersaleDBWorker(ledger_db.insert_record, record)
        self._save_worker.result_ready.connect(
            lambda rid, occ=record.get("occurred_at"): self._on_saved(rid, occ))
        self._save_worker.error.connect(self._on_save_error)
        self._save_worker.start()

    def _on_saved(self, _new_id, occurred_at=None):
        """写库成功：恢复按钮、记住本次日期、清表单并通知记录页。

        日期只在有值时记忆，空值不覆盖上一条，保证连续录入默认沿用
        最近一次记录的日期。
        """
        self._btn_submit.setEnabled(True)
        # 记住本次视频日期（需求3：与售后连续录入同逻辑——下一条默认沿用）
        if occurred_at:
            ledger_db.save_last_occurred(str(occurred_at))
        show_info_bar("已提交跑视频记录（列表页刷新可见）", "success",
                      title="提交成功", parent=self, duration=3000)
        self.form.clear_form()
        self._update_required_progress()
        self.saved.emit()

    def _on_save_error(self, msg):
        """写库失败：恢复提交按钮并保留表单内容。

        错误详情交给 InfoBar，表单不清是为了让用户直接重试。
        """
        self._btn_submit.setEnabled(True)
        show_info_bar(msg, "error", title="提交失败",
                      parent=self, duration=4000)

    # ---------- 外部入口 ----------

    def prefill(self, ctx: dict):
        """主界面入口调用：预填当前球桌会话上下文。

        预填后立刻重算进度，否则进度条与实际已填内容不一致。
        """
        self.form.prefill(ctx)
        self._update_required_progress()
