# -*- coding: utf-8 -*-
"""工具页 ToolHub（二期，2026-09-07）——「设置-工具」4 条动作行升级为独立工作区

设计稿：design/tools_page_v2.html
形态（2026-09-07 反馈修订）：与运维/售后/跑视频同风格——横排 Pivot 二级切换
（无图标），四工作区堆叠在下方：
    单杆视频 │ 端口占用 │ 上传清单 │ 视频与日志批量整理

复用原则（后端零改动）：
  - 单杆视频 → workers.single_video_worker.SingleVideoWorker（参数校验复刻
    SingleVideoDialog.collect_params 同一套规则；路径记忆键不变）
  - 端口占用 → 真实 socket 监听（TCP listen / UDP bind），表格化管理
  - 上传清单 → 扫描 {videos_dir}/upload，勾选打包走 ZipUploadWorker(files=白名单)
  - 批量整理 → NewLogWorker + 整理产物 ZipUploadWorker（zip_prefix=newlog）

busy 守卫：Worker 引用统一挂主窗口属性（_single_video_worker/_newlog_worker/
_newlog_upload_worker），与菜单栏弹窗入口互斥，防止双开。
"""
import os
import re
import random
import socket
import shutil
from datetime import datetime

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QFrame, QScrollArea, QFileDialog, QHeaderView,
                               QTableWidgetItem, QAbstractItemView, QSizePolicy,
                               QSplitter)
from qfluentwidgets import (TitleLabel, CaptionLabel, BodyLabel, CardWidget,
                            LineEdit, PushButton, PrimaryPushButton, ComboBox,
                            CompactSpinBox, SpinBox, TextEdit, TableWidget,
                            CheckBox, ProgressBar, FluentIcon, EditableComboBox,
                            ToolButton, MessageBox)

from main_window.pivot_page import PivotPage
from windows.single_video_dialog import (
    _DEFAULT_SESSION_CODE, _DEFAULT_FORMAT, _DEFAULT_AVATAR_0,
    _DEFAULT_AVATAR_1, _DEFAULT_USER_0, _DEFAULT_USER_1,
    _DEFAULT_PENDING_ROOT, _DEFAULT_VIDEOS_ROOT, _random_session_suffix)
from workers.newlog_worker import NewLogWorker
from workers.single_video_worker import SingleVideoWorker
from workers.collect_worker import ZipUploadWorker


# 语义色（对齐 core/design_tokens.py：success/warning/danger/info）
_C_SUCCESS = QColor(0x1a, 0x9e, 0x6c)
_C_DANGER = QColor(0xcf, 0x44, 0x52)
_C_ACCENT = QColor(0x00, 0x83, 0x8f)
_C_MUTED = QColor(0x6b, 0x72, 0x80)


def _term_font():
    mono = QFont("Consolas")
    mono.setStyleHint(QFont.Monospace)
    return mono


def _make_terminal(parent, height=200):
    """黑底终端风格输出区（设计稿：TextEdit 黑底，等宽字体）

    height=None → 不设固定高度、垂直 Expanding（占满剩余空间）。
    """
    tv = TextEdit(parent)
    tv.setReadOnly(True)
    if height is not None:
        tv.setFixedHeight(height)
    else:
        tv.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        tv.setMinimumHeight(180)
    tv.setFont(_term_font())
    tv.setStyleSheet(
        "TextEdit{background:#1b1e23;color:#d4d8de;border:1px solid #2b2f36;"
        "border-radius:6px;padding:6px 8px;}")
    return tv


def _make_vsplitter(parent, top_widget, bottom_widget):
    """上下两块之间插入可拖动分隔条（2026-09-19 反馈：拖动改高度）

    外观**对齐主界面列表之间的间隔**：工作台 4 列的 splitter 就是默认 QSplitter
    + handleWidth=2（渲染出来约 4px 的细缝，不做任何配色），这里照抄同一套，
    不再自造粗色条；鼠标移到缝上会变成上下调整光标。
    - 上块保持自身高度，下块（输出区）吃掉窗口多余高度，不改原有比例；
    - childrenCollapsible=False：两块都不会被拖没，最短高度各自的最小尺寸兜底。
    """
    sp = QSplitter(Qt.Orientation.Vertical, parent)
    sp.setChildrenCollapsible(False)
    sp.setHandleWidth(2)
    sp.addWidget(top_widget)
    sp.addWidget(bottom_widget)
    sp.setStretchFactor(0, 0)
    sp.setStretchFactor(1, 1)
    return sp


def _fmt_size(n: float) -> str:
    """字节数 → 人类可读（与运维面板 common._fmt_size 同规则）"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024


def _make_scroll(body_widget, parent):
    """工作区通用滚动容器（透明背景，与 SettingsHubPage._make_page 同模式）"""
    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    scroll.viewport().setAutoFillBackground(False)
    scroll.setStyleSheet("QScrollArea { background: transparent; }")
    scroll.setWidget(body_widget)
    # setWidget() 会重建视口，透明设置必须在其后补（否则深色主题下视口呈黑块）
    scroll.viewport().setStyleSheet("background: transparent;")
    return scroll


def _transparent(page_widget):
    """工作区页面本体透明（消除卡片外直角底色块，2026-09-07 反馈）：
    普通 QWidget 未注册进 styleSheetManager，widget 级 setStyleSheet 合法
    且不会被 qfw 主题轮询/合并源重置。"""
    page_widget.setAttribute(Qt.WA_StyledBackground, False)
    page_widget.setAutoFillBackground(False)
    page_widget.setStyleSheet("background: transparent;")


def _pair(grid, row_idx, label0, w0, label1, w1, parent):
    """两列参数网格行（复刻 SingleVideoDialog._pair）"""
    grid.addWidget(BodyLabel(label0, parent), row_idx, 0)
    grid.addWidget(w0, row_idx, 1)
    grid.addWidget(BodyLabel(label1, parent), row_idx, 2)
    grid.addWidget(w1, row_idx, 3)


def _pick_dir(win, line_edit, start_dir=""):
    """异步选择目录（QFileDialog.open() 非阻塞，防 exec 冻结主线程）"""
    dlg = QFileDialog(win, "选择目录", start_dir or line_edit.text().strip())
    dlg.setFileMode(QFileDialog.FileMode.Directory)
    dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
    dlg.setAttribute(Qt.WA_DeleteOnClose)

    def _sel(paths):
        if paths:
            line_edit.setText(paths[0])

    dlg.filesSelected.connect(_sel)
    dlg.open()
    return dlg


# ==================== 工作区 1：单杆视频 ====================

class SingleVideoWork(QWidget):
    """单杆视频工作区：日志选择 + JSON 参数表单 + 终端输出 + 后台 Worker"""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.setObjectName("singleVideoWork")
        _transparent(self)
        self._win = win
        self._phase = "idle"          # idle/running/browsing/done
        self._out_dir = ""
        self._file_dlg = None
        settings = win._load_settings()

        body = QWidget(self)
        body.setAutoFillBackground(False)
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 8, 20, 12)
        lay.setSpacing(12)

        card = CardWidget(body)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)

        cl.addWidget(BodyLabel(
            "从 .log 日志解析单杆得分，生成带计分水印的单杆视频（逐帧渲染，CPU 密集）。",
            card))

        # ---------- 表单区：左列参数 + 右列日志预览（2026-09-19 反馈） ----------
        # 左列沿用「控件定宽、尾部空列吃剩余」的既定规范；右侧预览面板吃掉剩下的
        # 宽度、与左列表单等高等宽，用来在生成前核对日志内容（帧区间/进球记录）。
        form_row = QHBoxLayout()
        form_row.setSpacing(16)
        left_col = QVBoxLayout()
        left_col.setSpacing(10)
        form_row.addLayout(left_col, 0)
        form_row.addWidget(self._build_log_preview(card), 1)

        # ---------- 日志文件选择（控件定宽，参考跑视频录入表单） ----------
        row = QHBoxLayout()
        row.addWidget(BodyLabel("日志文件:", card))
        self.log_path_edit = LineEdit(card)
        self.log_path_edit.setReadOnly(True)
        self.log_path_edit.setPlaceholderText("选择 .log 日志文件")
        self.log_path_edit.setFixedWidth(320)
        row.addWidget(self.log_path_edit)
        self.btn_browse = PushButton(FluentIcon.FOLDER, "浏览", card)
        self.btn_browse.clicked.connect(self._on_browse)
        row.addWidget(self.btn_browse)
        row.addStretch(1)
        left_col.addLayout(row)

        # ---------- 参数表单 ----------
        left_col.addWidget(CaptionLabel("JSON 字段:", card))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        self.spin_start = CompactSpinBox(card)
        self.spin_start.setRange(0, 99_999_999)
        self.spin_start.setValue(0)
        self.spin_start.setFixedWidth(130)
        self.spin_end = CompactSpinBox(card)
        self.spin_end.setRange(0, 99_999_999)
        self.spin_end.setValue(65535)
        self.spin_end.setFixedWidth(130)
        _pair(grid, 0, "起始帧:", self.spin_start, "结束帧:", self.spin_end, card)

        self.spin_player = CompactSpinBox(card)
        self.spin_player.setRange(0, 1)
        self.spin_player.setValue(1)
        self.spin_player.setFixedWidth(130)
        self.spin_round = CompactSpinBox(card)
        self.spin_round.setRange(1, 999)
        self.spin_round.setValue(1)
        self.spin_round.setFixedWidth(130)
        _pair(grid, 1, "甲乙方 (0/1):", self.spin_player, "轮次:",
              self.spin_round, card)

        self.edit_format = LineEdit(card)
        self.edit_format.setText(_DEFAULT_FORMAT)
        self.edit_format.setFixedWidth(130)
        self.edit_date = LineEdit(card)
        self.edit_date.setText(datetime.now().strftime("%Y%m%d"))
        self.edit_date.setFixedWidth(130)
        _pair(grid, 2, "赛制:", self.edit_format, "session_date:",
              self.edit_date, card)

        self.edit_session_name = LineEdit(card)
        self.edit_session_name.setText("第1场")
        self.edit_session_name.setFixedWidth(160)
        self.edit_session_code = LineEdit(card)
        self.edit_session_code.setText(_DEFAULT_SESSION_CODE)
        # 内容自适应宽度（2026-09-07 反馈）：按文本像素宽调整，夹在上下限之间
        self._autosize_session_code()
        self.edit_session_code.textChanged.connect(
            lambda _t: self._autosize_session_code())
        _pair(grid, 3, "session_name:", self.edit_session_name,
              "session_code:", self.edit_session_code, card)

        self.edit_ava_0 = LineEdit(card)
        self.edit_ava_0.setText(_DEFAULT_AVATAR_0)
        self.edit_ava_0.setFixedWidth(160)
        self.edit_ava_1 = LineEdit(card)
        self.edit_ava_1.setText(_DEFAULT_AVATAR_1)
        self.edit_ava_1.setFixedWidth(160)
        _pair(grid, 4, "选手0头像:", self.edit_ava_0, "选手1头像:",
              self.edit_ava_1, card)

        self.edit_user_0 = LineEdit(card)
        self.edit_user_0.setText(_DEFAULT_USER_0)
        self.edit_user_0.setFixedWidth(130)
        self.edit_user_1 = LineEdit(card)
        self.edit_user_1.setText(_DEFAULT_USER_1)
        self.edit_user_1.setFixedWidth(130)
        _pair(grid, 5, "选手0姓名:", self.edit_user_0, "选手1姓名:",
              self.edit_user_1, card)

        # 待处理目录 / 视频输出目录：各带浏览按钮（2026-09-07 需求）
        self.edit_pending = LineEdit(card)
        self.edit_pending.setText(
            str(settings.get("single_pending_root") or _DEFAULT_PENDING_ROOT))
        self.edit_pending.setFixedWidth(200)
        self.btn_pending = ToolButton(FluentIcon.FOLDER_ADD, card)
        self.btn_pending.setToolTip("浏览选择待处理目录")
        self.btn_pending.clicked.connect(
            lambda: _pick_dir(self._win, self.edit_pending))
        self.edit_videos = LineEdit(card)
        self.edit_videos.setText(
            str(settings.get("single_videos_root") or _DEFAULT_VIDEOS_ROOT))
        self.edit_videos.setFixedWidth(200)
        self.btn_videos = ToolButton(FluentIcon.FOLDER_ADD, card)
        self.btn_videos.setToolTip("浏览选择视频输出目录")
        self.btn_videos.clicked.connect(
            lambda: _pick_dir(self._win, self.edit_videos))
        _pair(grid, 6, "待处理目录:", self._dir_cell(self.edit_pending, self.btn_pending, card),
              "视频输出目录:", self._dir_cell(self.edit_videos, self.btn_videos, card), card)
        # 控件定宽：网格整体靠左，尾部空列吃掉左列剩余宽度
        grid.setColumnStretch(4, 1)
        left_col.addLayout(grid)

        # ---------- 操作行 ----------
        btns = QHBoxLayout()
        self.btn_start = PrimaryPushButton(FluentIcon.PLAY, "开始生成", card)
        self.btn_start.clicked.connect(self._on_start)
        btns.addWidget(self.btn_start)
        self.btn_open_out = PushButton(FluentIcon.FOLDER, "打开视频目录", card)
        self.btn_open_out.clicked.connect(self._on_open_out)
        self.btn_open_out.hide()
        btns.addWidget(self.btn_open_out)
        btns.addStretch(1)
        left_col.addLayout(btns)

        # 左列表单 + 右列日志预览合成一行
        cl.addLayout(form_row)

        # ---------- 布局（2026-09-07 反馈）：卡铺满整行、卡内控件定宽，
        # 运行输出独立整宽卡片占满剩余空间（不套滚动）；
        # 2026-09-19：参数区与运行输出之间改由 QSplitter 分隔，可上下拖动改高度 ----------
        out_card = CardWidget(self)
        ol = QVBoxLayout(out_card)
        ol.setContentsMargins(16, 14, 16, 14)
        ol.setSpacing(8)
        ol.addWidget(CaptionLabel("运行输出:", out_card))
        self.log_view = _make_terminal(out_card, None)
        ol.addWidget(self.log_view, 1)

        self.splitter = _make_vsplitter(body, card, out_card)
        lay.addWidget(self.splitter, 1)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(body)

        self._refresh_log_preview()      # 初始为「未选择日志」提示态

    # ---------- 右侧日志预览（2026-09-19 反馈：卡右侧空白区） ----------

    _LOG_PREVIEW_MAX_BYTES = 256 * 1024   # 读取上限，防超大日志把界面卡住

    def _build_log_preview(self, parent):
        """右列「日志预览」：与左列表单等高的只读日志视图（样式同运行输出）"""
        box = QWidget(parent)
        box.setMinimumWidth(300)
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(CaptionLabel("日志预览:", box))
        self.log_preview_meta = CaptionLabel("未选择日志文件", box)
        self.log_preview_meta.setMaximumWidth(420)   # 超长文件名不撑破面板
        head.addWidget(self.log_preview_meta)
        head.addStretch(1)
        self.btn_log_refresh = ToolButton(FluentIcon.SYNC, box)
        self.btn_log_refresh.setToolTip("重新读取日志文件")
        self.btn_log_refresh.clicked.connect(self._refresh_log_preview)
        head.addWidget(self.btn_log_refresh)
        v.addLayout(head)

        self.log_preview = _make_terminal(box, None)
        self.log_preview.setPlaceholderText("选择 .log 日志文件后，在此预览内容")
        v.addWidget(self.log_preview, 1)
        return box

    def _refresh_log_preview(self):
        """把所选日志读进预览区（限 _LOG_PREVIEW_MAX_BYTES 字节，超出加截断提示）"""
        path = self.log_path_edit.text().strip()
        if not path or not os.path.isfile(path):
            self.log_preview_meta.setText("未选择日志文件")
            self.log_preview_meta.setToolTip("")
            self.log_preview.clear()
            self.btn_log_refresh.setEnabled(False)
            return
        self.btn_log_refresh.setEnabled(True)
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                raw = f.read(self._LOG_PREVIEW_MAX_BYTES)
        except OSError as e:
            self.log_preview_meta.setText(f"读取失败: {e}")
            self.log_preview.clear()
            return
        text = raw.decode("utf-8", errors="replace")
        if len(raw) < size:
            text += (f"\n\n… 日志较大（{_fmt_size(size)}），"
                     f"仅预览前 {_fmt_size(self._LOG_PREVIEW_MAX_BYTES)}")
        # 面板本身较窄，头部只放「大小 · 行数」，文件名挂悬停提示（左侧字段已显示路径）
        info = f"{_fmt_size(size)} · {text.count(chr(10)) + 1} 行"
        self.log_preview_meta.setText(info)
        self.log_preview_meta.setToolTip(f"{path}\n{info}")
        self.log_preview.setPlainText(text)
        self.log_preview.verticalScrollBar().setValue(0)   # 预览回到日志开头

    @staticmethod
    def _dir_cell(edit, btn, parent):
        """LineEdit + 浏览按钮组合成一个单元格控件"""
        w = QWidget(parent)
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        h.addWidget(edit, 1)
        h.addWidget(btn, 0)
        return w

    _CODE_MIN_W = 180
    _CODE_MAX_W = 360

    def _autosize_session_code(self):
        """session_code 按文本内容自适应宽度（夹在 min/max 之间）"""
        fm = self.edit_session_code.fontMetrics()
        w = fm.horizontalAdvance(self.edit_session_code.text()) + 44
        w = max(self._CODE_MIN_W, min(w, self._CODE_MAX_W))
        self.edit_session_code.setFixedWidth(w)

    # ---------- 文件选择（异步 open()，防 exec 冻结主线程） ----------

    def _on_browse(self):
        if self._file_dlg is not None:
            return
        start_dir = self.edit_pending.text().strip() or _DEFAULT_PENDING_ROOT
        if not os.path.isdir(start_dir):
            start_dir = ""
        self._phase = "browsing"
        self.btn_start.setEnabled(False)
        self.btn_browse.setEnabled(False)
        dlg = QFileDialog(self, "选择日志文件", start_dir,
                          "日志文件 (*.log);;所有文件 (*.*)")
        dlg.setFileMode(QFileDialog.FileMode.ExistingFile)
        dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        dlg.fileSelected.connect(self._on_file_selected)
        dlg.finished.connect(self._on_picker_finished)
        self._file_dlg = dlg
        dlg.open()

    def _on_file_selected(self, path):
        if not path:
            return
        self.log_path_edit.setText(path)
        self._refresh_log_preview()      # 右列日志预览随选择联动
        video_name = os.path.basename(path).replace('.log', '.mp4')
        self.append_line(f"[选择] 日志: {path}")
        self.append_line(f"[选择] 自动推断视频: "
                         f"{os.path.join(self.edit_pending.text().strip(), video_name)}")
        base = os.path.splitext(os.path.basename(path))[0]
        m = re.match(r"^(\d{8})", base)
        if m:
            date = m.group(1)
            self.edit_date.setText(date)
            # 随机生成 session_code 开关（设置 → 工具，2026-09-07）
            if self._random_code_enabled():
                self.edit_session_code.setText(f"{date}_{_random_session_suffix()}")
                self.append_line(f"[选择] 自动识别 session_date={date}，session_code 已随机生成")
            else:
                self.append_line(f"[选择] 自动识别 session_date={date}")

    def _random_code_enabled(self):
        return bool(self._win._load_settings().get("single_random_session_code", True))

    def _auto_open_enabled(self):
        return bool(self._win._load_settings().get("single_auto_open_dir", False))

    def _on_picker_finished(self, _result):
        self._file_dlg = None
        self._phase = "idle"
        self.btn_start.setEnabled(True)
        self.btn_start.setText("开始生成")
        self.btn_browse.setEnabled(True)

    # ---------- 参数收集（校验规则与 SingleVideoDialog.collect_params 一致） ----------

    def collect_params(self):
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

    # ---------- 生成 ----------

    def _on_start(self):
        win = self._win
        worker = getattr(win, "_single_video_worker", None)
        if worker is not None and worker.isRunning():
            win._show_info_bar("单杆视频正在生成中，请等待完成", "warning")
            return
        try:
            import cv2  # noqa: F401  依赖守卫（与弹窗入口一致）
            import numpy  # noqa: F401
        except ImportError as e:
            win._show_info_bar(
                f"无法加载单杆视频依赖（请确认已安装 opencv-python、numpy）: {e}",
                "error", duration=5000)
            return
        params = self.collect_params()
        if not params:
            return
        # 记住本次路径配置，下次直接作为默认值
        win._save_settings({"single_pending_root": params["pending_root"],
                           "single_videos_root": params["videos_root"]})
        self._enter_running()
        w = SingleVideoWorker(params)
        win._single_video_worker = w          # 与弹窗入口共享 busy 守卫
        w.line.connect(self.append_line)
        w.finished_ok.connect(self._on_finished)
        w.error.connect(self._on_failed)
        w.finished.connect(lambda w=w: win._release_worker_safe(w))
        w.start()
        win._append_log(f"[单杆] 开始生成单杆视频: {params['video_name']}")

    def _on_finished(self, video_path):
        win = self._win
        w = getattr(win, "_single_video_worker", None)
        win._single_video_worker = None
        if w is not None:
            win._release_worker_safe(w)
        self.append_line("")
        self.append_line(f"✔ 单杆视频生成完成: {video_path}")
        self._phase = "done"
        self._out_dir = os.path.dirname(video_path) if video_path else ""
        self.btn_start.setEnabled(True)
        self.btn_start.setText("开始生成")
        self.btn_browse.setEnabled(True)
        self.btn_open_out.setVisible(bool(self._out_dir))
        win._append_log(f"[单杆] 单杆视频生成完成: {video_path}")
        win._show_info_bar("单杆视频生成完成，可点击「打开视频目录」查看",
                           "success", duration=4000)
        # 生成后自动打开所在目录（设置 → 工具，2026-09-07）
        if self._auto_open_enabled() and self._out_dir and os.path.isdir(self._out_dir):
            try:
                os.startfile(self._out_dir)
                self.append_line("▶ 已自动打开视频所在目录")
            except Exception:
                pass

    def _on_failed(self, msg):
        win = self._win
        w = getattr(win, "_single_video_worker", None)
        win._single_video_worker = None
        if w is not None:
            win._release_worker_safe(w)
        self.append_line("")
        self.append_line(f"✘ {msg}")
        self._phase = "idle"
        self.btn_start.setEnabled(True)
        self.btn_start.setText("开始生成")
        self.btn_browse.setEnabled(True)
        win._append_log(f"[单杆] 单杆视频生成失败: {msg}")
        win._show_info_bar(msg, "error", duration=4000)

    def _enter_running(self):
        self._phase = "running"
        self.btn_start.setEnabled(False)
        self.btn_start.setText("生成中...")
        self.btn_browse.setEnabled(False)
        self.btn_open_out.hide()

    def _on_open_out(self):
        if self._out_dir and os.path.isdir(self._out_dir):
            try:
                os.startfile(self._out_dir)
            except Exception:
                pass

    def append_line(self, text):
        self.log_view.append(text)


# ==================== 工作区 2：端口占用 ====================

class PortFakeWork(QWidget):
    """端口占用工作区（2026-09-07 按设计稿改表格）：
    自定义端口/协议/绑定地址 → 真实 socket 监听 → 表格管理，全部释放/逐行释放。
    离开页面自动释放（对齐原「关闭弹窗自动释放」语义）。
    """

    _RANDOM_MIN, _RANDOM_MAX = 20000, 60000

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.setObjectName("portFakeWork")
        _transparent(self)
        self._win = win
        self._sockets = {}   # key=(port,proto) -> socket

        body = QWidget(self)
        body.setAutoFillBackground(False)
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 8, 20, 12)
        lay.setSpacing(12)

        card = CardWidget(body)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(12)

        cl.addWidget(BodyLabel(
            "点击「占用」后端口被真实监听，netstat -ano 可见 LISTENING，模拟本机有服务在监听；"
            "支持多端口同时占用，离开本页自动全部释放。", card))

        # 参数行：端口 / 协议 / 绑定地址 / 随机 / 占用 / 全部释放
        prow = QHBoxLayout()
        prow.addWidget(BodyLabel("端口:", card))
        self.spin_port = SpinBox(card)
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(self._random_port())
        self.spin_port.setFixedWidth(150)
        prow.addWidget(self.spin_port)
        prow.addSpacing(10)
        prow.addWidget(BodyLabel("协议:", card))
        self.combo_proto = ComboBox(card)
        self.combo_proto.addItems(["TCP", "UDP"])
        self.combo_proto.setFixedWidth(90)
        prow.addWidget(self.combo_proto)
        prow.addSpacing(10)
        prow.addWidget(BodyLabel("绑定地址:", card))
        self.combo_addr = EditableComboBox(card)
        self.combo_addr.addItems(["0.0.0.0", "127.0.0.1"])
        self.combo_addr.setCurrentText("0.0.0.0")
        self.combo_addr.setFixedWidth(140)
        prow.addWidget(self.combo_addr)
        prow.addStretch(1)
        btn_random = PushButton(FluentIcon.SYNC, "随机端口", card)
        btn_random.clicked.connect(lambda: self.spin_port.setValue(self._random_port()))
        prow.addWidget(btn_random)
        self.btn_occupy = PrimaryPushButton(FluentIcon.CONNECT, "占用", card)
        self.btn_occupy.clicked.connect(self._occupy)
        prow.addWidget(self.btn_occupy)
        self.btn_release = PushButton(FluentIcon.DELETE, "全部释放", card)
        self.btn_release.setEnabled(False)
        self.btn_release.clicked.connect(lambda: self._release_all(notify=True))
        prow.addWidget(self.btn_release)
        cl.addLayout(prow)

        # 表格：端口 / 协议 / 状态 / 绑定地址 / 操作
        self.table = TableWidget(card)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["端口", "协议", "状态", "绑定地址", "操作"])
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(4, 96)
        self.table.setMinimumHeight(220)
        cl.addWidget(self.table, 1)

        self.lbl_stat = CaptionLabel("未占用任何端口", card)
        cl.addWidget(self.lbl_stat)

        lay.addWidget(card, 1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(body)

    # ---------- 端口操作 ----------

    def _random_port(self):
        for _ in range(20):
            port = random.randint(self._RANDOM_MIN, self._RANDOM_MAX)
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.bind(("0.0.0.0", port))
                s.close()
                return port
            except OSError:
                s.close()
        return random.randint(self._RANDOM_MIN, self._RANDOM_MAX)

    def _occupy(self):
        port = self.spin_port.value()
        proto = self.combo_proto.currentText().strip() or "TCP"
        addr = self.combo_addr.currentText().strip() or "0.0.0.0"
        key = (port, proto, addr)
        if key in self._sockets:
            self._win._show_info_bar(f"端口 {port} ({proto} {addr}) 已在占用中",
                                     "warning", duration=2000)
            return
        sock_type = socket.SOCK_STREAM if proto == "TCP" else socket.SOCK_DGRAM
        s = socket.socket(socket.AF_INET, sock_type)
        status = ""
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((addr, port))
            if proto == "TCP":
                s.listen(5)
                s.setblocking(False)
                status = "LISTENING"
            else:
                s.setblocking(False)
                status = "BOUND"
        except OSError as e:
            s.close()
            self._win._show_info_bar(f"端口 {port} ({proto}) 占用失败：{e.strerror or e}",
                                     "error", title="失败", duration=3000)
            return
        self._sockets[key] = s
        self._add_row(key, status)
        self.btn_release.setEnabled(True)
        self._update_stat()
        self._win._show_info_bar(f"端口 {port} ({proto} {addr}) 已占用（{status}）",
                                 "success", title="占用成功", duration=2500)

    def _add_row(self, key, status):
        port, proto, addr = key
        r = self.table.rowCount()
        self.table.insertRow(r)
        it = QTableWidgetItem(str(port))
        it.setFont(_term_font())
        self.table.setItem(r, 0, it)
        self.table.setItem(r, 1, QTableWidgetItem(proto))
        st = QTableWidgetItem(status)
        st.setForeground(_C_SUCCESS)
        self.table.setItem(r, 2, st)
        at = QTableWidgetItem(addr)
        at.setFont(_term_font())
        self.table.setItem(r, 3, at)
        btn = PushButton("释放", self.table)
        btn.setFixedHeight(26)
        btn.clicked.connect(lambda checked=False, k=key: self._release_one(k))
        self.table.setCellWidget(r, 4, btn)

    def _release_one(self, key):
        s = self._sockets.pop(key, None)
        if s is not None:
            try:
                s.close()
            except OSError:
                pass
        self._remove_row(key)
        self._update_stat()
        if not self._sockets:
            self.btn_release.setEnabled(False)

    def _remove_row(self, key):
        port, proto, addr = key
        for r in range(self.table.rowCount()):
            it0 = self.table.item(r, 0)
            if (it0 and it0.text() == str(port)
                    and self.table.item(r, 1) and self.table.item(r, 1).text() == proto
                    and self.table.item(r, 3) and self.table.item(r, 3).text() == addr):
                self.table.removeRow(r)
                return

    def _release_all(self, notify=True):
        for key in list(self._sockets):
            s = self._sockets.pop(key, None)
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        self.table.setRowCount(0)
        self.btn_release.setEnabled(False)
        self._update_stat()
        if notify:
            self._win._show_info_bar("已释放全部端口", "success",
                                     title="释放成功", duration=2000)

    def _update_stat(self):
        n = len(self._sockets)
        if n == 0:
            self.lbl_stat.setText("未占用任何端口")
            return
        tcp = sum(1 for (_p, pr, _a) in self._sockets if pr == "TCP")
        udp = n - tcp
        self.lbl_stat.setText(f"已占用 {n} 个端口 · TCP {tcp} · UDP {udp}")

    def hideEvent(self, e):
        super().hideEvent(e)
        self._release_all(notify=False)

    def closeEvent(self, e):
        self._release_all(notify=False)
        super().closeEvent(e)


# ==================== 工作区 3：上传清单 ====================

class UploadListWork(QWidget):
    """上传清单工作区（2026-09-07 按设计稿改复选框列表）：
    扫描 {videos_dir}/upload 下文件 → 复选框/文件名/类型/大小 表格 + 表头全选
    → 勾选打包上传（ZipUploadWorker files 白名单，仅删已上传文件）。
    """

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.setObjectName("uploadListWork")
        _transparent(self)
        self._win = win
        self._worker = None
        self._rows = []          # [(abs_path, size)] 与表格行同序

        body = QWidget(self)
        body.setAutoFillBackground(False)
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 8, 20, 12)
        lay.setSpacing(12)

        card = CardWidget(body)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)

        self.lbl_root = BodyLabel("收集目录: —", card)
        cl.addWidget(self.lbl_root)

        # 表格：复选框 / 文件名 / 类型 / 大小
        self.table = TableWidget(card)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["", "文件名", "类型", "大小"])
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # 列宽（2026-09-07 反馈）：文件名列 Stretch 吃满剩余宽度→表格铺满卡片，
        # 文字保持左对齐；复选固定 40，类型/大小按内容自适应，末列不重复拉伸
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setStretchLastSection(False)
        self.table.setColumnWidth(0, 40)
        self.table.setMinimumHeight(260)
        cl.addWidget(self.table, 1)
        # 表头第 0 列放全选复选框（常开三态：部分勾选时显示半选）
        self.chk_all = CheckBox("", self.table.horizontalHeader())
        self.chk_all.setTristate(True)  # 仅为回显半选；用户单击由 clicked 接管
        self._syncing = False           # 程序同步勾选态时抑制 clicked/stateChanged 递归
        self.chk_all.clicked.connect(self._on_header_clicked)

        self.progress_bar = ProgressBar(card)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.hide()
        cl.addWidget(self.progress_bar)

        bottom = QHBoxLayout()
        self.lbl_total = CaptionLabel("", card)
        bottom.addWidget(self.lbl_total)
        self.lbl_progress = CaptionLabel("", card)
        bottom.addWidget(self.lbl_progress)
        bottom.addStretch(1)
        btn_refresh = PushButton(FluentIcon.SYNC, "刷新", card)
        btn_refresh.clicked.connect(self.refresh)
        bottom.addWidget(btn_refresh)
        btn_open = PushButton(FluentIcon.FOLDER, "打开目录", card)
        btn_open.clicked.connect(self._open_dir)
        bottom.addWidget(btn_open)
        self.btn_delete = PushButton(FluentIcon.DELETE, "删除所选", card)
        self.btn_delete.setToolTip("删除勾选的文件（弹出二次确认，仅删 upload 目录内文件）")
        self.btn_delete.clicked.connect(self._on_delete_checked)
        bottom.addWidget(self.btn_delete)
        self.btn_package = PrimaryPushButton(FluentIcon.SEND, "打包上传", card)
        self.btn_package.setToolTip(
            "将勾选的文件打包 zip 上传服务器，成功后删除已上传文件")
        self.btn_package.clicked.connect(self._on_package_upload)
        bottom.addWidget(self.btn_package)
        cl.addLayout(bottom)

        lay.addWidget(card, 1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(body)

        self.refresh()

    # ---------- 清单扫描 ----------

    def _upload_root(self):
        videos_dir = getattr(self._win, "videos_dir", "") or ""
        return os.path.join(videos_dir, "upload") if videos_dir else ""

    @staticmethod
    def _type_of(name):
        ext = os.path.splitext(name)[1].lower()
        if ext in (".mp4", ".avi", ".mkv", ".mov"):
            return "视频"
        if ext in (".log", ".txt"):
            return "日志"
        if ext in (".json",):
            return "清单"
        return "其他"

    def refresh(self):
        """扫描 upload 目录（递归）构建复选框表格"""
        self.table.setRowCount(0)
        self._rows = []
        root = self._upload_root()
        if not root:
            self.lbl_root.setText("收集目录: —（请先在设置 → 应用配置 中配置视频/日志目录）")
            self.lbl_total.setText("")
            return
        self.lbl_root.setText(f"收集目录: {root}")
        if not os.path.isdir(root):
            self.lbl_total.setText("暂无待上传文件，请先右键日志文件→添加到上传目录")
            return
        total_size = 0
        for cur, _dirs, files in os.walk(root):
            for name in sorted(files):
                full = os.path.join(cur, name)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                total_size += size
                rel = os.path.relpath(full, root)
                self._add_row(full, rel, self._type_of(name), size)
        self.lbl_total.setText(
            f"共 {len(self._rows)} 个文件，总大小 {_fmt_size(total_size)}")
        self._sync_check_all()
        self._position_header_checkbox()

    def _add_row(self, full, rel, typ, size):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self._rows.append((full, size))
        cb = CheckBox("", self.table)
        cb.stateChanged.connect(lambda _s: self._on_row_checked())
        cw = QWidget(self.table)
        hl = QHBoxLayout(cw)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setAlignment(Qt.AlignCenter)
        hl.addWidget(cb)
        self.table.setCellWidget(r, 0, cw)
        ft = QTableWidgetItem(rel)
        ft.setFont(_term_font())
        ft.setToolTip(full)
        self.table.setItem(r, 1, ft)
        tt = QTableWidgetItem(typ)
        tt.setForeground(_C_ACCENT if typ == "视频" else _C_MUTED)
        self.table.setItem(r, 2, tt)
        st = QTableWidgetItem(_fmt_size(size))
        st.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        st.setFont(_term_font())
        self.table.setItem(r, 3, st)

    def _row_checkbox(self, r):
        cw = self.table.cellWidget(r, 0)
        if cw is None:
            return None
        return cw.findChild(CheckBox)

    def _on_row_checked(self):
        """行复选框变化：回显表头三态 + 更新已选统计（程序同步期间跳过）"""
        if self._syncing:
            return
        self._sync_check_all()

    def _on_header_clicked(self):
        """表头复选框单击接管（2026-09-07 反馈修复）：

        三态 QCheckBox 单击默认按 未选→半选→全选 循环——空选/半选时点一次
        只到半选，需再点一次才全选。改为按各行实际勾选态决定动作：
        未全选 → 全选；已全选 → 全不选（半选仅作程序回显，用户点击不产生）。
        """
        if self._worker is not None and self._worker.isRunning():
            return
        boxes = [self._row_checkbox(r) for r in range(self.table.rowCount())]
        boxes = [b for b in boxes if b is not None]
        if not boxes:
            return
        target = not all(b.isChecked() for b in boxes)
        self._syncing = True
        try:
            for b in boxes:
                b.setChecked(target)
        finally:
            self._syncing = False
        self._sync_check_all()

    def _sync_check_all(self):
        """按各行勾选态回显表头全选框（全选/部分/未选）"""
        boxes = [self._row_checkbox(r) for r in range(self.table.rowCount())]
        boxes = [b for b in boxes if b is not None]
        n = sum(1 for b in boxes if b.isChecked())
        self.chk_all.blockSignals(True)
        if not boxes or n == 0:
            self.chk_all.setCheckState(Qt.CheckState.Unchecked)
        elif n == len(boxes):
            self.chk_all.setCheckState(Qt.CheckState.Checked)
        else:
            self.chk_all.setCheckState(Qt.CheckState.PartiallyChecked)
        self.chk_all.blockSignals(False)
        self._update_sel_count()

    def _update_sel_count(self):
        total = self.table.rowCount()
        n = sum(1 for r in range(total)
                if (b := self._row_checkbox(r)) and b.isChecked())
        sel_size = sum(self._rows[r][1] for r in range(total)
                       if (b := self._row_checkbox(r)) and b.isChecked())
        base = f"共 {total} 个文件" if total else "暂无待上传文件"
        self.lbl_progress.setText(
            f"已选 {n} 个 · {_fmt_size(sel_size)}" if n else base)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._position_header_checkbox()

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh()
        self._position_header_checkbox()

    def _position_header_checkbox(self):
        """把全选复选框摆到表头第 0 列正中"""
        try:
            hdr = self.table.horizontalHeader()
            x0 = hdr.sectionPosition(0)
            w = hdr.sectionSize(0)
            sz = self.chk_all.sizeHint()
            self.chk_all.setGeometry(
                x0 + max(0, (w - sz.width()) // 2),
                (hdr.height() - sz.height()) // 2,
                sz.width(), sz.height())
            self.chk_all.raise_()
        except Exception:
            pass

    def _open_dir(self):
        root = self._upload_root()
        if root and os.path.isdir(root):
            os.startfile(root)

    # ---------- 打包上传（勾选白名单） ----------

    @staticmethod
    def _upload_target(settings):
        host = str(settings.get("upload_host") or "49.235.34.253").strip()
        try:
            port = int(settings.get("upload_port") or 22)
        except (TypeError, ValueError):
            port = 22
        remote_dir = str(settings.get("upload_remote_dir") or "/lhcos-data/videos").strip()
        username = str(settings.get("upload_user") or "root").strip()
        password = str(settings.get("upload_pass") or "")
        return host, port, remote_dir, username, password

    def _checked_files(self):
        out = []
        for r in range(self.table.rowCount()):
            cb = self._row_checkbox(r)
            if cb is not None and cb.isChecked() and r < len(self._rows):
                out.append(self._rows[r][0])
        return out

    # ---------- 删除所选（二次确认 + 路径安全校验） ----------

    def _safe_upload_path(self, full_path):
        """路径必须位于 upload 根目录内部（防越界删除）"""
        root = self._upload_root()
        if not root:
            return None
        root = os.path.normcase(os.path.normpath(os.path.abspath(root)))
        path = os.path.normcase(os.path.normpath(os.path.abspath(full_path)))
        if path == root:
            return None
        try:
            if os.path.commonpath([root, path]) != root:
                return None
        except ValueError:
            return None
        return path

    def _on_delete_checked(self):
        """删除勾选文件：二次确认弹窗 → 逐个删除（仅 upload 目录内）→ 刷新清单"""
        if self._worker is not None and self._worker.isRunning():
            self._win._show_info_bar("打包上传进行中，请等待完成后再删除",
                                     "warning", duration=2000)
            return
        files = self._checked_files()
        if not files:
            self._win._show_info_bar("请先勾选要删除的文件", "info", duration=3000)
            return
        names = "、".join(os.path.basename(f) for f in files[:5])
        if len(files) > 5:
            names += f" 等 {len(files)} 个"
        box = MessageBox("删除确认",
                         f"确定删除勾选的 {len(files)} 个文件吗？\n{names}\n\n"
                         "该操作不可撤销，仅删除 upload 目录内的文件。", self)
        box.yesButton.setText("删除")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        ok = fail = 0
        for full in files:
            safe = self._safe_upload_path(full)
            if safe is None:
                fail += 1
                continue
            try:
                if os.path.isfile(safe):
                    os.remove(safe)
                    ok += 1
                else:
                    fail += 1
            except PermissionError:
                fail += 1
            except OSError:
                fail += 1
        self.refresh()
        if fail:
            self._win._show_info_bar(
                f"已删除 {ok} 个，{fail} 个失败（无权限或已不存在）",
                "warning", title="删除完成", duration=4000)
        else:
            self._win._show_info_bar(f"已删除 {ok} 个文件", "success",
                                     title="删除成功", duration=2500)

    def _on_package_upload(self):
        if self._worker is not None and self._worker.isRunning():
            self._on_cancel_upload()
            return
        root = self._upload_root()
        if not root:
            self._win._show_info_bar("videos_dir 未配置，请先在设置中配置视频/日志目录",
                                     "warning", duration=3000)
            return
        files = self._checked_files()
        if not files:
            self._win._show_info_bar("请先勾选要上传的文件", "info", duration=3000)
            return
        host, port, remote_dir, username, password = self._upload_target(
            self._win._load_settings())
        if not password:
            self._win._show_info_bar(
                "未配置上传密码，请先在设置 → 应用配置 → 收集与上传中填写后重试",
                "warning", duration=4000)
            return
        box = MessageBox(
            "打包上传",
            f"将把勾选的 {len(files)} 个文件打包为 zip，上传到\n{host}:{remote_dir}\n\n"
            "上传成功后将删除这些已上传文件，确定继续？", self)
        box.yesButton.setText("上传")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        self.btn_package.setText("取消上传")
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        worker = ZipUploadWorker(root, host, port, username, password, remote_dir,
                                 files=files, cleanup_after_done=True)
        self._worker = worker
        worker.progress.connect(self.lbl_progress.setText)
        worker.percent.connect(self._on_percent)
        worker.done.connect(self._on_done)
        worker.error.connect(self._on_fail)
        worker.cancelled.connect(self._restore_ui)
        worker.start()
        self._win._append_log(f"[上传清单] 开始打包上传 {len(files)} 个文件")

    def _on_cancel_upload(self):
        box = MessageBox("取消上传", "上传进行中，确定取消？", self)
        box.yesButton.setText("确定取消")
        box.cancelButton.setText("继续上传")
        if not box.exec():
            return
        if self._worker is not None and self._worker.isRunning():
            self.btn_package.setEnabled(False)
            self.lbl_progress.setText("正在取消...")
            self._worker.requestInterruption()

    def _on_percent(self, p):
        if self.progress_bar.isHidden():
            self.progress_bar.show()
        self.progress_bar.setValue(p)
        self.lbl_progress.setText(f"上传中 {p}%")

    def _restore_ui(self):
        w = self._worker
        self._worker = None
        try:
            if w is not None:
                if w.isRunning():
                    w.finished.connect(lambda w=w: w.deleteLater())
                else:
                    w.deleteLater()
        except RuntimeError:
            pass
        self.btn_package.setText("打包上传")
        self.btn_package.setEnabled(True)
        self.lbl_progress.setText("")
        self.progress_bar.hide()
        self.progress_bar.setValue(0)

    def _on_done(self, info):
        self._restore_ui()
        self.refresh()   # 已上传文件被 worker 删除 → 刷新清单
        self._win._show_info_bar(f"{info} · 已上传文件已从本地删除", "success",
                                 duration=5000)

    def _on_fail(self, msg):
        self._restore_ui()
        self._win._show_info_bar(msg.split(chr(10))[0], "error",
                                 title="上传失败", duration=5000)


# ==================== 工作区 4：视频与日志批量整理 ====================

class NewLogWork(QWidget):
    """批量整理工作区：署名筛选 → NewLogWorker 归类 → 打包上传（两段式）"""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.setObjectName("newlogWork")
        _transparent(self)
        self._win = win
        self._phase = "idle"
        self._out_path = ""
        default_name = str(win._load_settings().get("newlog_target_name", "") or "")

        body = QWidget(self)
        body.setAutoFillBackground(False)
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 8, 20, 12)
        lay.setSpacing(12)

        card = CardWidget(body)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)

        cl.addWidget(BodyLabel(
            "按 Excel「署名」列筛选记录，批量归类对应视频与日志；完成后可一键打包上传服务器。",
            card))

        row = QHBoxLayout()
        row.addWidget(BodyLabel("筛选署名:", card))
        self.target_edit = LineEdit(card)
        self.target_edit.setText(default_name)
        self.target_edit.setPlaceholderText("按 Excel 中「署名」列筛选")
        self.target_edit.setFixedWidth(320)
        row.addWidget(self.target_edit)
        row.addStretch(1)
        cl.addLayout(row)

        btns = QHBoxLayout()
        self.btn_go = PrimaryPushButton(FluentIcon.SEND, "开始整理", card)
        self.btn_go.clicked.connect(self._on_go)
        btns.addWidget(self.btn_go)
        self.btn_open_out = PushButton(FluentIcon.FOLDER, "打开结果目录", card)
        self.btn_open_out.clicked.connect(self._on_open_out)
        self.btn_open_out.hide()
        btns.addWidget(self.btn_open_out)
        btns.addStretch(1)
        cl.addLayout(btns)

        out_card = CardWidget(body)
        ol = QVBoxLayout(out_card)
        ol.setContentsMargins(16, 14, 16, 14)
        ol.setSpacing(8)
        ol.addWidget(CaptionLabel("运行输出:", out_card))
        self.log_view = _make_terminal(out_card, None)
        ol.addWidget(self.log_view, 1)

        self.progress_bar = ProgressBar(out_card)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.hide()
        ol.addWidget(self.progress_bar)

        # 与单杆视频页同构：参数区 / 运行输出之间可上下拖动改高度
        self.splitter = _make_vsplitter(body, card, out_card)
        lay.addWidget(self.splitter, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(body)

    def _on_go(self):
        if self._phase == "uploading":
            self._cancel_upload()
        elif self._phase == "organized":
            self._start_upload()
        elif self._phase == "idle":
            self._start_organize()

    def _start_organize(self):
        win = self._win
        if self._phase == "organizing":
            return
        worker = getattr(win, "_newlog_worker", None)
        if worker is not None and worker.isRunning():
            win._show_info_bar("整理任务正在运行中，请等待完成", "warning")
            return
        up = getattr(win, "_newlog_upload_worker", None)
        if up is not None and up.isRunning():
            win._show_info_bar("打包上传进行中，请等待完成或先取消", "warning")
            return
        try:
            from windows.tools import newlog  # noqa: F401  依赖守卫（newlog 顶层 import openpyxl）
        except ImportError as e:
            win._show_info_bar(f"无法加载 newlog 模块（请确认已安装 openpyxl）: {e}",
                               "error", duration=5000)
            return
        target = self.target_edit.text().strip()
        if not target:
            win._show_info_bar("请输入筛选署名", "warning")
            return
        win._save_settings({"newlog_target_name": target})
        self._phase = "organizing"
        self.target_edit.setEnabled(False)
        self.btn_go.setEnabled(False)
        self.btn_go.setText("整理中...")
        w = NewLogWorker(target)
        win._newlog_worker = w
        w.line.connect(self.append_line)
        w.finished_ok.connect(self._on_organized)
        w.error.connect(self._on_organize_failed)
        w.finished.connect(lambda w=w: win._release_worker_safe(w))
        w.start()
        win._append_log(f"[整理] 开始视频/日志批量整理（署名：{target}）")

    def _on_organized(self, out_path):
        win = self._win
        w = getattr(win, "_newlog_worker", None)
        win._newlog_worker = None
        if w is not None:
            win._release_worker_safe(w)
        self.append_line("")
        self.append_line(f"✔ 整理完成，输出目录: {out_path}")
        self._out_path = out_path
        self._phase = "organized"
        self.target_edit.setEnabled(True)
        self.btn_go.setEnabled(True)
        self.btn_go.setText("打包上传")
        self.btn_open_out.show()
        win._append_log(f"[整理] 视频/日志批量整理完成: {out_path}")
        win._show_info_bar("整理完成，可点击「打包上传」上传服务器",
                           "success", duration=4000)

    def _on_organize_failed(self, msg):
        win = self._win
        w = getattr(win, "_newlog_worker", None)
        win._newlog_worker = None
        if w is not None:
            win._release_worker_safe(w)
        self.append_line("")
        self.append_line(f"✘ {msg}")
        self._phase = "idle"
        self.target_edit.setEnabled(True)
        self.btn_go.setEnabled(True)
        self.btn_go.setText("开始整理")
        win._append_log(f"[整理] 视频/日志批量整理失败: {msg}")
        win._show_info_bar(msg, "error", duration=4000)

    def _start_upload(self):
        win = self._win
        running = getattr(win, "_newlog_upload_worker", None)
        if running is not None and running.isRunning():
            win._show_info_bar("已有上传进行中，请稍候", "warning")
            return
        if not self._out_path or not os.path.isdir(self._out_path) \
                or not os.listdir(self._out_path):
            win._show_info_bar("整理输出目录为空，无法打包上传", "warning")
            return
        videos_dir = getattr(win, "videos_dir", "") or ""
        if not videos_dir or not os.path.isdir(videos_dir):
            win._show_info_bar(
                "videos_dir 未配置或目录不存在，请先在设置中配置视频/日志目录",
                "warning", duration=4000)
            return
        host, port, remote_dir, username, password = UploadListWork._upload_target(
            win._load_settings())
        if not password:
            self.append_line("✘ 未配置上传密码（upload_pass），请在设置中配置")
            win._show_info_bar(
                "未配置上传密码，请先在设置 → 应用配置 → 收集与上传中填写后重试",
                "warning", duration=4000)
            return
        upload_root = os.path.join(videos_dir, "upload")
        worker = ZipUploadWorker(
            upload_root, host, port, username, password, remote_dir,
            content_root=self._out_path, zip_prefix="newlog", zip_dir=upload_root,
            cleanup_after_done=False, remove_zip_after_done=True)
        win._newlog_upload_worker = worker
        worker.progress.connect(self.append_line)
        worker.percent.connect(self._on_upload_percent)
        worker.done.connect(self._on_upload_done)
        worker.error.connect(self._on_upload_fail)
        worker.cancelled.connect(self._on_upload_cancelled)
        self._phase = "uploading"
        self.target_edit.setEnabled(False)
        self.btn_go.setText("取消上传")
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.append_line("")
        self.append_line(f"▶ 开始打包上传 → {host}:{remote_dir}")
        worker.start()
        win._append_log(f"[整理] 开始打包上传整理产物: {os.path.basename(self._out_path)}")

    def _cancel_upload(self):
        win = self._win
        worker = getattr(win, "_newlog_upload_worker", None)
        if worker is None or not worker.isRunning():
            return
        box = MessageBox("取消上传", "上传进行中，确定取消？", self)
        box.yesButton.setText("确定取消")
        box.cancelButton.setText("继续上传")
        if not box.exec():
            return
        if not worker.isRunning():
            return
        self.append_line("… 正在取消上传...")
        self.btn_go.setEnabled(False)
        worker.requestInterruption()

    def _release_upload_worker(self):
        win = self._win
        w = getattr(win, "_newlog_upload_worker", None)
        win._newlog_upload_worker = None
        if w is not None:
            win._release_worker_safe(w)

    def _on_upload_percent(self, p):
        if self.progress_bar.isHidden():
            self.progress_bar.show()
        self.progress_bar.setValue(p)

    def _after_upload(self, line_msg):
        self._release_upload_worker()
        self.append_line(line_msg)
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        self.btn_go.setEnabled(True)
        self._phase = "organized"
        self.target_edit.setEnabled(True)
        self.btn_go.setText("打包上传")

    def _on_upload_done(self, info):
        self._after_upload(f"✔ 上传成功: {info}（本地临时 zip 已清理）")
        self._win._append_log(f"[整理] 打包上传成功: {info}")
        self._win._show_info_bar(f"打包上传成功: {info}", "success", duration=5000)

    def _on_upload_fail(self, msg):
        self._after_upload(f"✘ 上传失败: {msg}")
        self._win._append_log(f"[整理] 打包上传失败: {msg}")
        self._win._show_info_bar(msg.split(chr(10))[0], "error", duration=4000)

    def _on_upload_cancelled(self):
        self._after_upload("✘ 上传已取消（临时 zip 已清理）")
        self._win._append_log("[整理] 打包上传已取消（临时 zip 已清理）")
        self._win._show_info_bar("上传已取消，临时 zip 已清理", "info", duration=4000)

    def _on_open_out(self):
        if self._out_path and os.path.isdir(self._out_path):
            try:
                os.startfile(self._out_path)
            except Exception:
                pass

    def append_line(self, text):
        self.log_view.append(text)


# ==================== ToolHub 容器 ====================

class ToolHub(PivotPage):
    """工具页：横排 Pivot 二级切换 + 四工作区堆叠（与运维/售后/跑视频同风格）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("toolHub")
        self._win = parent

        self.single_video_work = SingleVideoWork(self._win, self)
        self.port_fake_work = PortFakeWork(self._win, self)
        self.upload_list_work = UploadListWork(self._win, self)
        self.newlog_work = NewLogWork(self._win, self)

        self.addPage(self.single_video_work, "单杆视频")
        self.addPage(self.port_fake_work, "端口占用")
        self.addPage(self.upload_list_work, "上传清单")
        self.addPage(self.newlog_work, "视频与日志批量整理")
        self.lock_pivot_width()
        self.switchTo(self.single_video_work)
