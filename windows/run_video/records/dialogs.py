# -*- coding: utf-8 -*-
"""跑视频记录页的编辑/新增弹窗与署名统计弹窗

自 records/page.py 逐字搬移（EditLedgerDialog 原 L34-143，SignerStatsDialog
原 L146-182），行为与搬移前一致。依赖按需引入：page.py 的设计令牌、表格
委托、统计开窗等与弹窗无关，拆分后不再牵连本模块。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView,
                               QTableWidgetItem)

from qfluentwidgets import (TableWidget, TitleLabel, CaptionLabel,
                            MessageBoxBase)

from core.utils import show_info_bar
from database import ledger_db
from workers.aftersale_worker import AftersaleDBWorker

from windows.run_video.form import LedgerForm

__all__ = ["EditLedgerDialog", "SignerStatsDialog"]


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
