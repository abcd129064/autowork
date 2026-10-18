# -*- coding: utf-8 -*-
"""视频/日志批量整理对话框（从 ui_mixin.py 迁出）。"""

import os

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout
from qfluentwidgets import (BodyLabel, CaptionLabel, FluentIcon, LineEdit,
    MessageBoxBase, ProgressBar, PushButton, TextEdit)


class NewLogDialog(MessageBoxBase):
    """视频/日志批量整理对话框（Task #40 / #57）

    署名输入 + 运行输出实时展示；整理完成后可一键打包上传（复用运维
    面板 ZipUploadWorker）。

    yesButton 状态机：开始整理 → 整理中（禁用）→ 打包上传 →
    上传中（按钮变为「取消上传」，二次确认）。上传进行中可最小化
    （worker 由主窗口持有，后台继续，完成后 InfoBar 提示并重新激活）。
    整理中禁止关闭（Worker 输出直写本对话框文本区）；上传中允许关闭。
    """

    def __init__(self, parent, default_target):
        super().__init__(parent)
        self._phase = "idle"  # idle/organizing/organized/uploading
        self._out_path = ""
        self.on_start = None          # 由主窗口注入：启动整理
        self.on_upload = None         # 由主窗口注入：发起打包上传
        self.on_cancel_upload = None  # 由主窗口注入：取消上传（含二次确认）

        self.titleLabel = BodyLabel("视频/日志批量整理", self)
        self.viewLayout.addWidget(self.titleLabel)

        row = QHBoxLayout()
        row.addWidget(BodyLabel("筛选署名:", self))
        self.target_edit = LineEdit(self)
        self.target_edit.setText(default_target)
        self.target_edit.setPlaceholderText("按 Excel 中「署名」列筛选")
        row.addWidget(self.target_edit, 1)
        self.viewLayout.addLayout(row)

        self.viewLayout.addWidget(CaptionLabel("运行输出:", self))
        self.log_view = TextEdit(self)
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(300)
        # 等宽字体便于对齐阅读进度日志
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.log_view.setFont(mono)
        self.viewLayout.addWidget(self.log_view)

        # 上传字节进度条（仅打包上传阶段显示）
        self.progress_bar = ProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.hide()
        self.viewLayout.addWidget(self.progress_bar)

        # 底部按钮区补充：打开结果目录 + 最小化（初始隐藏/禁用）
        self.btn_open_out = PushButton(FluentIcon.FOLDER, "打开结果目录", self)
        self.btn_open_out.clicked.connect(self._on_open_out)
        self.btn_open_out.hide()
        self.btn_min = PushButton(FluentIcon.MINIMIZE, "最小化", self)
        self.btn_min.setToolTip("上传后台继续进行，完成后主窗口弹出提示")
        self.btn_min.setEnabled(False)
        self.btn_min.clicked.connect(self.hide)
        self.buttonLayout.insertWidget(0, self.btn_min)
        self.buttonLayout.insertWidget(0, self.btn_open_out)

        # 接管 yesButton：不自动关闭对话框，按状态分发动作
        self.yesButton.clicked.disconnect()
        self.yesButton.clicked.connect(self._on_yes_clicked)

    def _on_yes_clicked(self):
        """yesButton 状态分发：上传中=取消上传，整理完=打包上传，空闲=开始整理"""
        if self._phase == "uploading":
            if self.on_cancel_upload:
                self.on_cancel_upload()
            return
        if self._phase == "organized":
            if self.on_upload:
                self.on_upload()
            return
        if self._phase == "idle" and self.on_start:
            self.on_start()

    def _on_open_out(self):
        """资源管理器打开整理结果目录"""
        if self._out_path:
            try:
                os.startfile(self._out_path)
            except Exception:
                pass

    def append_line(self, text):
        """向运行输出区追加一行（Worker 日志直写）"""
        self.log_view.append(text)

    def enter_running(self):
        """进入整理中：禁用输入与全部按钮防中途关闭"""
        self._phase = "organizing"
        self.target_edit.setEnabled(False)
        self.yesButton.setEnabled(False)
        self.yesButton.setText("整理中...")
        self.cancelButton.setEnabled(False)

    def enter_organized(self, out_path):
        """整理完成待打包上传（上传取消/失败后也回到此状态，可再次发起）"""
        self._phase = "organized"
        self._out_path = out_path
        self.target_edit.setEnabled(True)
        self.yesButton.setEnabled(True)
        self.yesButton.setText("打包上传")
        self.cancelButton.setEnabled(True)
        self.btn_open_out.setEnabled(True)
        self.btn_open_out.show()
        self.btn_min.setEnabled(False)
        self.progress_bar.hide()
        self.progress_bar.setValue(0)

    def enter_uploading(self):
        """进入上传中：主按钮变取消上传，启用最小化与进度条"""
        self._phase = "uploading"
        self.target_edit.setEnabled(False)
        self.yesButton.setEnabled(True)
        self.yesButton.setText("取消上传")
        self.cancelButton.setEnabled(False)
        self.btn_open_out.setEnabled(False)
        self.btn_min.setEnabled(True)
        self.progress_bar.setValue(0)
        self.progress_bar.show()

    def set_upload_percent(self, p):
        """更新上传字节进度（首次自动显示进度条）"""
        if self.progress_bar.isHidden():
            self.progress_bar.show()
        self.progress_bar.setValue(p)

    def enter_failed(self):
        """整理失败：回到空闲态可重新发起"""
        self._phase = "idle"
        self.target_edit.setEnabled(True)
        self.yesButton.setEnabled(True)
        self.yesButton.setText("开始整理")
        self.cancelButton.setEnabled(True)

    def closeEvent(self, e):
        # 整理中禁止关闭：防止 Worker 信号打到已销毁的文本区；
        # 上传中允许关闭：worker 由主窗口持有，后台继续并 InfoBar 提示
        if self._phase == "organizing":
            e.ignore()
            return
        super().closeEvent(e)
