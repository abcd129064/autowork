# -*- coding: utf-8 -*-
"""单杆视频对话框（工具菜单「单杆视频」，收编自 single_json 项目）

选择 .log 日志文件 → 填写场次/选手参数 → 后台 SingleVideoWorker
解析单杆得分并生成带计分水印的单杆视频（逐帧渲染，CPU 密集）。

yesButton 状态机：开始生成 → 生成中（禁用，禁止关闭）→ 完成/失败恢复。
与 NewLogDialog 同模式：Worker 由主窗口持有，日志直写本对话框文本区。
"""
import os
import random
import re
from datetime import datetime

from PySide6.QtWidgets import (QHBoxLayout, QGridLayout, QFileDialog)
from PySide6.QtGui import QFont
from qfluentwidgets import (MessageBoxBase, BodyLabel, CaptionLabel,
    LineEdit, PushButton, FluentIcon, CompactSpinBox, ComboBox, TextEdit)

# 与 single_json 原 CLI 一致的默认值
_DEFAULT_SESSION_CODE = "20260322230533_HF75QY2CNPE10097102W1"
_DEFAULT_FORMAT = "0(3)0"
_DEFAULT_AVATAR_0 = "playerA.jpg"
_DEFAULT_AVATAR_1 = "playerB.jpg"
_DEFAULT_USER_0 = "甲方"
_DEFAULT_USER_1 = "乙方"
_DEFAULT_PENDING_ROOT = "D:\\pending"
_DEFAULT_VIDEOS_ROOT = "D:\\videos"

# 随机串字符集：与球桌接口 code 字段同格式（如 J4Y1J13CNPE1009AR06FW）
_SESSION_RAND_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_SESSION_RAND_LEN = 21


def _random_session_suffix(n: int = _SESSION_RAND_LEN) -> str:
    """生成与球桌接口 code 同格式的随机串（默认 21 位 [A-Z0-9]）"""
    return "".join(random.choice(_SESSION_RAND_CHARS) for _ in range(n))


class SingleVideoDialog(MessageBoxBase):
    """单杆视频参数对话框：文件选择 + 参数表单 + 实时输出"""

    def __init__(self, parent, settings=None):
        super().__init__(parent)
        self._phase = "idle"  # idle/running/browsing/done
        self._out_path = ""
        self._file_dlg = None  # 异步文件对话框（open() 非阻塞，选择期间持有）
        self.on_start = None  # 由主窗口注入：校验参数并启动 Worker
        settings = settings or {}

        self.titleLabel = BodyLabel("单杆视频", self)
        self.viewLayout.addWidget(self.titleLabel)

        # ---------- 日志文件选择（自动推断同名 .mp4 视频） ----------
        row = QHBoxLayout()
        row.addWidget(BodyLabel("日志文件:", self))
        self.log_path_edit = LineEdit(self)
        self.log_path_edit.setReadOnly(True)
        self.log_path_edit.setPlaceholderText("选择 .log 日志文件")
        row.addWidget(self.log_path_edit, 1)
        self.btn_browse = PushButton(FluentIcon.FOLDER, "浏览", self)
        self.btn_browse.clicked.connect(self._on_browse)
        row.addWidget(self.btn_browse)
        self.viewLayout.addLayout(row)

        # ---------- 参数表单（两列排布） ----------
        self.viewLayout.addWidget(CaptionLabel("JSON 字段:", self))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        def _pair(row_idx, label0, w0, label1, w1):
            grid.addWidget(BodyLabel(label0, self), row_idx, 0)
            grid.addWidget(w0, row_idx, 1)
            grid.addWidget(BodyLabel(label1, self), row_idx, 2)
            grid.addWidget(w1, row_idx, 3)

        self.spin_start = CompactSpinBox(self)
        self.spin_start.setRange(0, 99_999_999)
        self.spin_start.setValue(0)
        self.spin_end = CompactSpinBox(self)
        self.spin_end.setRange(0, 99_999_999)
        self.spin_end.setValue(65535)
        _pair(0, "起始帧:", self.spin_start, "结束帧:", self.spin_end)

        self.spin_player = CompactSpinBox(self)
        self.spin_player.setRange(0, 1)
        self.spin_player.setValue(1)
        self.spin_round = CompactSpinBox(self)
        self.spin_round.setRange(1, 999)
        self.spin_round.setValue(1)
        _pair(1, "甲乙方 (0/1):", self.spin_player, "轮次:", self.spin_round)

        self.edit_format = LineEdit(self)
        self.edit_format.setText(_DEFAULT_FORMAT)
        today = datetime.now().strftime("%Y%m%d")
        self.edit_date = LineEdit(self)
        self.edit_date.setText(today)
        _pair(2, "赛制:", self.edit_format, "session_date:", self.edit_date)

        self.edit_session_name = LineEdit(self)
        self.edit_session_name.setText("第1场")
        self.edit_session_code = LineEdit(self)
        self.edit_session_code.setText(_DEFAULT_SESSION_CODE)
        _pair(3, "session_name:", self.edit_session_name,
              "session_code:", self.edit_session_code)

        self.edit_ava_0 = LineEdit(self)
        self.edit_ava_0.setText(_DEFAULT_AVATAR_0)
        self.edit_ava_1 = LineEdit(self)
        self.edit_ava_1.setText(_DEFAULT_AVATAR_1)
        _pair(4, "选手0头像:", self.edit_ava_0, "选手1头像:", self.edit_ava_1)

        self.edit_user_0 = LineEdit(self)
        self.edit_user_0.setText(_DEFAULT_USER_0)
        self.edit_user_1 = LineEdit(self)
        self.edit_user_1.setText(_DEFAULT_USER_1)
        _pair(5, "选手0姓名:", self.edit_user_0, "选手1姓名:", self.edit_user_1)

        self.edit_pending = LineEdit(self)
        self.edit_pending.setText(
            str(settings.get("single_pending_root") or _DEFAULT_PENDING_ROOT))
        self.edit_videos = LineEdit(self)
        self.edit_videos.setText(
            str(settings.get("single_videos_root") or _DEFAULT_VIDEOS_ROOT))
        _pair(6, "待处理目录:", self.edit_pending, "视频输出目录:", self.edit_videos)
        self.viewLayout.addLayout(grid)

        # ---------- 运行输出 ----------
        self.viewLayout.addWidget(CaptionLabel("运行输出:", self))
        self.log_view = TextEdit(self)
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(220)
        # 等宽字体便于对齐阅读进度日志
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.log_view.setFont(mono)
        self.viewLayout.addWidget(self.log_view)

        # 底部按钮区补充：打开输出目录（生成完成后显示）
        self.btn_open_out = PushButton(FluentIcon.FOLDER, "打开视频目录", self)
        self.btn_open_out.clicked.connect(self._on_open_out)
        self.btn_open_out.hide()
        self.buttonLayout.insertWidget(0, self.btn_open_out)

        # 接管 yesButton：不自动关闭对话框，按状态分发动作
        self.yesButton.setText("开始生成")
        self.cancelButton.setText("关闭")
        self.yesButton.clicked.disconnect()
        self.yesButton.clicked.connect(self._on_yes_clicked)

    # ---------- 控件回调 ----------

    def _on_browse(self):
        """异步弹出文件对话框（open() 非阻塞），选择完成后自动识别场次

        用 open() 而非 getOpenFileName（exec 阻塞）：原生文件对话框打开
        可能耗时数秒（目录枚举/缩略图），exec 会冻结主线程导致「未响应」；
        open() 显示后立即返回，主线程持续响应，结果经信号异步回调。
        （Qt 不允许在非 GUI 线程创建 widget，故不用子线程方案。）
        """
        if self._file_dlg is not None:
            return
        start_dir = self.edit_pending.text().strip() or _DEFAULT_PENDING_ROOT
        if not os.path.isdir(start_dir):
            start_dir = ""
        # 选择期间锁定对话框：禁止关闭/重复点击
        self._phase = "browsing"
        self.yesButton.setEnabled(False)
        self.cancelButton.setEnabled(False)
        self.btn_browse.setEnabled(False)
        dlg = QFileDialog(self, "选择日志文件", start_dir,
                          "日志文件 (*.log);;所有文件 (*.*)")
        dlg.setFileMode(QFileDialog.FileMode.ExistingFile)
        dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        dlg.fileSelected.connect(self._on_file_selected)
        dlg.rejected.connect(self._on_file_rejected)
        dlg.finished.connect(self._on_picker_finished)
        self._file_dlg = dlg
        dlg.open()  # 非阻塞：显示后立即返回，主线程继续处理事件

    def _on_file_selected(self, path):
        """文件选择完成：填充路径并自动识别 session 信息"""
        if not path:
            return
        self.log_path_edit.setText(path)
        video_name = os.path.basename(path).replace('.log', '.mp4')
        self.append_line(f"[选择] 日志: {path}")
        self.append_line(f"[选择] 自动推断视频: {os.path.join(self.edit_pending.text().strip(), video_name)}")
        # 从文件名自动识别 session_date / session_code（如 20260810_230635.log）
        base = os.path.splitext(os.path.basename(path))[0]
        m = re.match(r"^(\d{8})", base)
        if m:
            date = m.group(1)
            self.edit_date.setText(date)
            self.edit_session_code.setText(f"{date}_{_random_session_suffix()}")
            self.append_line(f"[选择] 自动识别 session_date={date}"
                             f"，session_code 已生成")

    def _on_file_rejected(self):
        """用户取消文件选择"""
        self.append_line("[选择] 已取消")

    def _on_picker_finished(self, _result):
        """文件对话框结束（选择或取消）：清理引用并恢复控件状态"""
        self._file_dlg = None
        self._phase = "idle"
        self.yesButton.setEnabled(True)
        self.yesButton.setText("开始生成")
        self.cancelButton.setEnabled(True)
        self.btn_browse.setEnabled(True)

    def _on_yes_clicked(self):
        """yesButton 分发：仅空闲态且已注入 on_start 时发起生成"""
        if self._phase == "idle" and self.on_start:
            self.on_start()

    def _on_open_out(self):
        """资源管理器打开生成视频所在目录"""
        if self._out_path and os.path.isdir(self._out_path):
            try:
                os.startfile(self._out_path)
            except Exception:
                pass

    # ---------- 参数收集与校验（主窗口启动 Worker 前调用） ----------

    def collect_params(self):
        """校验输入并组装 generate_json 参数；校验失败返回 None 并输出提示"""
        log_path = self.log_path_edit.text().strip()
        if not log_path or not os.path.isfile(log_path):
            self.append_line("✘ 请先选择有效的日志文件")
            return None
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                log_text = f.read()
        except Exception as e:
            self.append_line(f"✘ 读取日志文件失败: {e}")
            return None

        video_name = os.path.basename(log_path).replace('.log', '.mp4')
        pending_root = self.edit_pending.text().strip()
        videos_root = self.edit_videos.text().strip()
        if not os.path.isdir(pending_root):
            self.append_line(f"✘ 待处理目录不存在: {pending_root}")
            return None
        if not os.path.isdir(videos_root):
            self.append_line(f"✘ 视频输出目录不存在: {videos_root}")
            return None

        return {
            "log_text": log_text,
            "start_frame": self.spin_start.value(),
            "end_frame": self.spin_end.value(),
            "player": self.spin_player.value(),
            "session_code": self.edit_session_code.text().strip(),
            "session_date": self.edit_date.text().strip(),
            "video_name": video_name,
            "session_name": self.edit_session_name.text().strip(),
            "round_num": self.spin_round.value(),
            "format_str": self.edit_format.text().strip(),
            "user_ava_0": self.edit_ava_0.text().strip(),
            "user_ava_1": self.edit_ava_1.text().strip(),
            "user_name_0": self.edit_user_0.text().strip(),
            "user_name_1": self.edit_user_1.text().strip(),
            "pending_root": pending_root,
            "videos_root": videos_root,
        }

    # ---------- 状态机 ----------

    def append_line(self, text):
        """向运行输出区追加一行日志"""
        self.log_view.append(text)

    def enter_running(self):
        """进入生成中：禁用全部按钮防重复发起/中途关闭"""
        self._phase = "running"
        self.yesButton.setEnabled(False)
        self.yesButton.setText("生成中...")
        self.cancelButton.setEnabled(False)
        self.btn_browse.setEnabled(False)
        self.btn_open_out.hide()

    def enter_done(self, video_path):
        """生成完成：记录输出目录，恢复可再次发起"""
        self._phase = "done"
        self._out_path = os.path.dirname(video_path) if video_path else ""
        self.yesButton.setEnabled(True)
        self.yesButton.setText("开始生成")
        self.cancelButton.setEnabled(True)
        self.btn_browse.setEnabled(True)
        if self._out_path:
            self.btn_open_out.show()

    def enter_failed(self):
        """生成失败：回到空闲态可重新发起"""
        self._phase = "idle"
        self.yesButton.setEnabled(True)
        self.yesButton.setText("开始生成")
        self.cancelButton.setEnabled(True)
        self.btn_browse.setEnabled(True)

    def closeEvent(self, e):
        # 生成中禁止关闭：防止 Worker 信号打到已销毁的文本区
        if self._phase == "running":
            e.ignore()
            return
        # 文件选择中点关闭：主动取消文件对话框以复位状态，避免卡死
        # （reject 触发 finished 复位；本次关闭先拦截，复位后可再次关闭）
        if self._phase == "browsing":
            if self._file_dlg is not None:
                self._file_dlg.reject()
            e.ignore()
            return
        super().closeEvent(e)
