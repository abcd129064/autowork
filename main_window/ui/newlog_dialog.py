# -*- coding: utf-8 -*-
"""视频/日志批量整理对话框（从 ui_mixin.py 迁出）。

弹窗自身不碰 Worker：开始整理 / 打包上传 / 取消上传都由主窗口以回调注入，
因此上传可以在弹窗关闭后继续，弹窗也能脱离主窗口做离屏冒烟。
"""

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
        self._out_path = ""          # 每次进入 organized 覆盖，重试不会误用上一次的路径
        # 三个回调由主窗口注入而非本类自建：Worker 归主窗口持有，才能在上传中
        # 关掉弹窗而任务继续（「最小化」的语义就靠这一点成立）。
        self.on_start = None          # 由主窗口注入：启动整理
        self.on_upload = None         # 由主窗口注入：发起打包上传
        self.on_cancel_upload = None  # 由主窗口注入：取消上传（含二次确认）

        self.titleLabel = BodyLabel("视频/日志批量整理", self)
        self.viewLayout.addWidget(self.titleLabel)

        row = QHBoxLayout()
        row.addWidget(BodyLabel("筛选署名:", self))
        self.target_edit = LineEdit(self)
        # 默认填当前页筛选的署名，省一次输入；用户仍可改成别人
        self.target_edit.setText(default_target)
        self.target_edit.setPlaceholderText("按 Excel 中「署名」列筛选")
        row.addWidget(self.target_edit, 1)
        self.viewLayout.addLayout(row)

        self.viewLayout.addWidget(CaptionLabel("运行输出:", self))
        self.log_view = TextEdit(self)
        self.log_view.setReadOnly(True)
        # 固定高度而非随内容增长：否则每来一行日志弹窗就长高一次，抖动明显
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
        # 4px 细条：上传进度是次要信息，再高就会吃掉日志区的可视行数
        self.progress_bar.hide()
        self.viewLayout.addWidget(self.progress_bar)

        # 底部按钮区补充：打开结果目录 + 最小化（初始隐藏/禁用）
        self.btn_open_out = PushButton(FluentIcon.FOLDER, "打开结果目录", self)
        self.btn_open_out.clicked.connect(self._on_open_out)
        self.btn_open_out.hide()
        # 隐藏而非关闭：close() 会走 closeEvent 被「整理中禁止关闭」拦下，
        # 且上传中关掉自己与「后台继续」的设计相冲突。
        self.btn_min = PushButton(FluentIcon.MINIMIZE, "最小化", self)
        self.btn_min.setToolTip("上传后台继续进行，完成后主窗口弹出提示")
        self.btn_min.setEnabled(False)
        self.btn_min.clicked.connect(self.hide)
        self.buttonLayout.insertWidget(0, self.btn_min)
        self.buttonLayout.insertWidget(0, self.btn_open_out)

        # 断开 qfw 基类「点击即 accept 关闭」的默认连接，改由 _on_yes_clicked
        # 按阶段分发；否则一点开始整理弹窗就关，Worker 日志将无处可写。
        self.yesButton.clicked.disconnect()
        self.yesButton.clicked.connect(self._on_yes_clicked)

    def _on_yes_clicked(self):
        """yesButton 状态分发：上传中=取消上传，整理完=打包上传，空闲=开始整理"""
        # 先判 uploading/organized 再落到 idle：这两个阶段已有产物，不能被当成
        # 空闲再启动一次整理（会把上一次的结果目录覆盖掉）。
        if self._phase == "uploading":
            if self.on_cancel_upload:
                # 确认框在回调内部弹出：本类只有阶段状态，不知道已传多少、中断是否安全
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
            # 目录可能已被用户手动清理；打不开只是提示不到，不值得弹错误框
            try:
                os.startfile(self._out_path)
            except Exception:
                pass

    def append_line(self, text):
        """向运行输出区追加一行（Worker 日志直写）"""
        # 可能由 Worker 的跨线程信号触发，这里只做一次 append，不碰任何控件状态
        self.log_view.append(text)

    def enter_running(self):
        """进入整理中：禁用输入与全部按钮防中途关闭"""
        # 文案保留「整理中...」而不是只置灰：日志在滚动，用户要能看出没卡死
        # cancelButton 一起禁用：中途退出会让 Worker 信号打到已销毁的文本区
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
        # 「打开结果目录」只在此态出现：整理未完成时目录还不存在
        self.btn_open_out.show()
        # 最小化随之禁用：只有上传需要后台续跑，停在结果态没有可最小化的任务
        self.btn_min.setEnabled(False)
        self.progress_bar.hide()
        self.progress_bar.setValue(0)

    def enter_uploading(self):
        """进入上传中：主按钮变取消上传，启用最小化与进度条"""
        # 取消上传由 yesButton 承接（不另加按钮），所以它必须保持可点；
        # 同时关掉「打开结果目录」，避免上传中删改正在被读取的产物。
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
        # 兜底显示：进度回调可能早于按钮切到上传态，否则头几个百分点看不见
        if self.progress_bar.isHidden():
            self.progress_bar.show()
        self.progress_bar.setValue(p)

    def enter_failed(self):
        """整理失败：回到空闲态可重新发起"""
        # 只回滚按钮状态、不清空日志：失败原因留在输出区，用户可直接复制上报
        self._phase = "idle"
        self.target_edit.setEnabled(True)
        self.yesButton.setEnabled(True)
        self.yesButton.setText("开始整理")
        self.cancelButton.setEnabled(True)

    def closeEvent(self, e):
        # 整理中禁止关闭，防止 Worker 信号打到已销毁的文本区；上传中允许关闭，worker 归主窗口
        # 持有会继续跑。其余状态放行：此刻没有在跑的任务，拦下来只会让用户关不掉窗口。
        if self._phase == "organizing":
            e.ignore()
            return
        super().closeEvent(e)
