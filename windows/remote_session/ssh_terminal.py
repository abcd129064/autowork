# -*- coding: utf-8 -*-
"""SSH 终端（invoke_shell 交互式 PTY + ANSI 虚拟终端 + 直接键盘输入）

体验与 Windows Terminal / Xshell 一致：
- 直接在终端区域打字，shell 回显
- Tab 命令/路径补全
- 上下键命令历史
- Ctrl+C/D/L 等控制键
- ANSI 彩色输出正确渲染
- nano/vim 全屏应用（备用屏幕切换）
- 会话记录器：终端输出与用户命令同步落盘 logs/ssh_sessions/
- 断线重连：断开后顶部显示重连条，一键重建连接并开启新会话日志
- 常用命令条：命令列表存 settings.json，支持增删管理与可选自动执行

安全关闭策略（规避 C 层 Use-After-Free 崩溃）：
- channel 上设置 0.1s recv 超时，reader 线程可被 stop 标志及时中断
- shutdown() 仅设置 stop 标志并等待 reader 线程退出，绝不从主线程操作 channel
- reader 线程退出后再关闭 transport，消除并发竞态

架构：
- SSHTerminalPanel(QWidget)：可嵌入标签页的核心面板
- SSHTerminalWindow(QDialog)：独立窗口薄壳（向后兼容）
"""

import codecs
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import threading
from datetime import datetime

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QWidget, QFrame,
                               QLabel, QListWidget)
from PySide6.QtGui import QAction
from PySide6.QtCore import QTimer, Qt, Signal
from qfluentwidgets import (PushButton, PrimaryPushButton, DropDownPushButton,
                            TransparentToolButton, LineEdit, FluentIcon, MessageBox,
                            RoundMenu, BodyLabel)

from core.app_paths import get_app_dir
from core.conn_logger import conn_logger
from core.theme_qss import apply_window_qss
from core.utils import safe_close_transport, cleanup_log_dir, show_info_bar
from workers.network_workers import SSHConnectWorker
from windows.remote_session.ansi_terminal import ANSITerminalWidget
from windows.remote_session.forensic_report import ForensicWorker, get_forensic_dir

# 模块级强引用集合：防止窗口关闭后 Python GC 回收仍在运行的 QThread 导致崩溃
_pending_workers: set = set()


def _safe_release_worker(w):
    """将 worker 放入 pending 集合，线程结束后自动移除并 deleteLater"""
    _pending_workers.add(w)
    w.finished.connect(lambda: (_pending_workers.discard(w), w.deleteLater()))


# ─── 配置门面读写（常用命令条配置） ─────────────────────────────────

# 常用命令默认占位示例（配置门面无 ssh_commands 键时使用）
DEFAULT_SSH_COMMANDS = ["top", "df -h", "journalctl -n 50"]


def _load_settings() -> dict:
    """读取配置门面合并视图，失败时返回空字典"""
    try:
        from core import app_settings
        return app_settings.get_merged()
    except Exception:
        return {}


def _save_settings(data: dict):
    """按键合并写入配置门面（自动路由域文件；敏感字段加密落盘）"""
    try:
        from core import app_settings
        for k, v in data.items():
            app_settings.set(k, v)
    except Exception:
        pass


# ─── 会话日志工具 ─────────────────────────────────────────────────────────

def get_session_log_dir() -> str:
    """SSH 会话日志目录：{app_dir}/logs/ssh_sessions（与 conn_logger 的 logs/ 同级机制）"""
    return os.path.join(get_app_dir(), "logs", "ssh_sessions")


# ANSI 转义序列剥离正则（会话日志写纯文本，便于检索）
# 说明：CSI 的终止字节不限于字母（\E[1P / \E[@ / \E[2~ 等），
# 字符集设计符 \E(0 / \E(B 是 3 字节序列，必须单独匹配——
# 否则日志里会残留字面 "B"（2026-10-04 用户反馈的 nano 截图即由它造成）。
_ANSI_RE = re.compile(
    r'\x1b\[[0-9;?<>=]*[ -/]*[@-~]'          # CSI 序列（参数 + 中间字节 + 终止字节）
    r'|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?'  # OSC 序列
    r'|\x1b[()*+\-./][0-9A-Za-z]'           # 字符集设计符（\E(0 \E(B \E)0 ...）
    r'|\x1b[ -~]'                            # 其他两字节转义（\E= \E> \E7 ...）
)


def _strip_ansi(text: str) -> str:
    """剥离 ANSI 转义序列，返回纯文本"""
    return _ANSI_RE.sub('', text)


# 取证按钮提示（init 与 _restore_forensic_btn 共用，避免两处文案漂移）
_FORENSIC_BTN_TIP = (
    "后台运行预置诊断命令组（含 dmesg/journalctl/syslog 系统错误日志）"
    "并汇总会话/连接日志、设备状态，调用 AI 大模型分析生成故障取证报告"
    "（厂商可在设置面板「AI 分析」页配置，仅连接建立后可用；运行中点击可取消）"
)


class SSHTerminalPanel(QWidget):
    """SSH 终端面板（可嵌入标签页容器，也可独立使用）

    核心逻辑：invoke_shell 交互式 PTY + ANSI 渲染 + 直接键盘输入。
    资源清理统一由 shutdown() 方法负责，容器关闭标签时调用。
    """

    # reader 线程通过此信号将输出安全投递到 GUI 线程
    _output_signal = Signal(str)
    # P2-4：reader 线程退出/异常 → GUI 线程进入断线态的真实事件源
    # （此前靠显示文本 '[连接已断开]' 字符串匹配触发，远端程序打印同串会误判）
    _link_lost_signal = Signal()
    # P1-2：把当前远端工作目录交给上层开 SFTP 标签（与 SFTP 的
    # open_terminal_here 方向相反；目录取自 shell 上报的 OSC 7）
    open_sftp_here = Signal(str)

    def __init__(self, host, port, username, password, log_callback=None, parent=None,
                 server_name='', start_dir=''):
        super().__init__(parent)
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._server_name = server_name
        # P1-1：起始目录（SFTP「终端」按钮跳转用；连接建立后注入 cd 命令）
        self._start_dir = str(start_dir or '').strip()
        # P1-2：最近一次上报的远端工作目录（OSC 7）；连接前先显示起始目录
        self._last_cwd = self._start_dir
        self._log = log_callback or (lambda msg: None)
        self._client = None
        self._channel = None
        self._connect_worker = None
        self._reader_thread = None
        self._stop_event = threading.Event()
        self._closing = False
        # 会话日志（会话记录器）：仅 GUI 线程读写，无并发
        self._session_file = None
        self._session_path = None
        # 用户输入命令行重组缓冲（写入会话日志带 '> ' 前缀）
        self._cmd_buf = []
        # 转义序列消费状态：0=正常 1=刚见 ESC 2=CSI/OSC 内部 3=三字节序列收尾
        self._cmd_esc = 0
        # PTY 尺寸同步：拖动窗口会连续触发网格变化，逐次 resize_pty 会让远端
        # 全屏应用反复重绘（SIGWINCH 风暴）→ 去抖合并，并记住"已同步尺寸"
        self._pty_timer = QTimer(self)
        self._pty_timer.setSingleShot(True)
        self._pty_timer.setInterval(120)
        self._pty_timer.timeout.connect(self._apply_pty_size)
        self._pending_pty_size = None
        self._pty_size_sent = None
        # 断线重连状态
        self._disconnected = False
        self._reconnecting = False
        self._auto_run = False
        # 一键取证（D2）后台 worker 引用
        self._forensic_worker = None
        self._init_ui()
        self._refresh_cwd_label()
        self._output_signal.connect(self._on_shell_output)
        self._link_lost_signal.connect(self._on_link_lost)  # P2-4
        QTimer.singleShot(100, self._connect_ssh)

    def focusNextPrevChild(self, next_: bool) -> bool:
        """禁止 Tab 焦点导航，确保 Tab 始终发送到终端"""
        return False

    @property
    def tab_title(self) -> str:
        """返回适合标签页显示的标题（P0-4：始终带 host:port，同目标多标签可区分）"""
        if getattr(self, '_server_name', ''):
            return f"SSH - {self._server_name}（{self._host}:{self._port}）"
        return f"SSH - {self._host}:{self._port}"

    def _target_desc(self) -> str:
        """日志/确认弹窗用的目标描述（server_name 优先）"""
        if getattr(self, '_server_name', ''):
            return self._server_name
        return f"{self._host}:{self._port}"

    def busy_state(self):
        """(busy, 描述)——P0-1 统一契约：取证任务运行中视为 busy

        供标签容器（关闭前确认）与 core.frp_remote.is_busy_on_port（断隧道前
        确认）查询；交互 shell 会话本身不算 busy（关标签即关会话，无任务损失）。
        """
        w = self._forensic_worker
        if w is not None and w.isRunning():
            return True, '取证任务运行中'
        return False, ''

    @property
    def is_connected(self) -> bool:
        """P2-6：只读连通探针（会话恢复后延迟体检用；连接中/断开均为 False）"""
        return bool(not self._disconnected and not self._closing
                    and self._client is not None)

    @property
    def remote_path(self) -> str:
        """当前远端工作目录（OSC 7 上报；还没上报时回退起始目录）

        会话存档（remote_mixin._extract_session_info）用它记住"上次在哪个目录"，
        恢复时作为 start_dir 注入 cd。
        """
        live = ''
        try:
            live = self._terminal.cwd
        except RuntimeError:          # 控件已销毁（标签已关）
            live = ''
        return live or self._last_cwd or self._start_dir

    # ─── 远端工作目录（P1-2，OSC 7） ──────────────────────────────────────

    def _on_cwd_changed(self, path):
        """shell 上报了新的工作目录 → 刷新状态条 chip"""
        self._last_cwd = str(path or '')
        self._refresh_cwd_label()

    def _display_cwd(self) -> str:
        """把 home 目录缩写成 ~，省状态条横向空间"""
        cwd = self.remote_path
        if not cwd:
            return ''
        user = str(getattr(self, '_username', '') or '')
        home = '/root' if user == 'root' else (f'/home/{user}' if user else '')
        if home and (cwd == home or cwd.startswith(home + '/')):
            return '~' + cwd[len(home):]
        return cwd

    def _refresh_cwd_label(self):
        """刷新 cwd chip 文案/提示/按钮可用性（控件缺失或已销毁时静默返回）"""
        cwd = self.remote_path
        try:
            self._lbl_cwd.setText(self._display_cwd())
            if cwd:
                self._lbl_cwd.setToolTip(f'远端工作目录：{cwd}（点右侧文件夹在 SFTP 中打开）')
            else:
                self._lbl_cwd.setToolTip(
                    '远端工作目录未知：远端 shell 未上报 OSC 7\n'
                    'bash 可在 ~/.bashrc 里加：'
                    'PROMPT_COMMAND=\'printf "\\033]7;file://%s%s\\033\\\\" '
                    '"$HOSTNAME" "$PWD"\'')
            self._btn_sftp_here.setEnabled(bool(cwd))
        except (AttributeError, RuntimeError):
            pass

    def _open_sftp_here(self):
        """把当前远端目录交给上层开 SFTP 标签（remote_mixin._open_sftp_at）"""
        cwd = self.remote_path
        if not cwd:
            return
        self._log(f"[SSH] 在 SFTP 中打开: {cwd}")
        self.open_sftp_here.emit(cwd)

    # ─── UI ───────────────────────────────────────────────────────────────

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # 断线通知条（默认隐藏，连接断开/连接失败时显示）
        self._reconnect_bar = QFrame(self)
        self._reconnect_bar.setStyleSheet(
            "QFrame { background-color: rgba(255, 152, 0, 0.15);"
            " border: 1px solid #ff9800; border-radius: 4px; }"
        )
        bar_layout = QHBoxLayout(self._reconnect_bar)
        bar_layout.setContentsMargins(8, 2, 8, 2)
        bar_layout.setSpacing(6)
        self._reconnect_label = QLabel("连接已断开")
        bar_layout.addWidget(self._reconnect_label)
        bar_layout.addStretch()
        self._reconnect_btn = PrimaryPushButton("重新连接")
        self._reconnect_btn.setFocusPolicy(Qt.NoFocus)
        self._reconnect_btn.setFixedWidth(96)
        self._reconnect_btn.clicked.connect(self._reconnect_clicked)
        bar_layout.addWidget(self._reconnect_btn)
        self._reconnect_bar.hide()
        layout.addWidget(self._reconnect_bar)

        # ANSI 终端（既是显示区域也是输入区域）
        self._terminal = ANSITerminalWidget(self)
        self._terminal.key_input.connect(self._on_key_input)
        # 网格尺寸变化 → 同步远端 PTY（全屏应用按 PTY 尺寸排版，尺寸不对就会错位）
        self._terminal.grid_resized.connect(self._on_grid_resized)
        # P1-2：shell 上报 OSC 7（工作目录）→ 更新状态条 chip
        self._terminal.cwd_changed.connect(self._on_cwd_changed)
        layout.addWidget(self._terminal, stretch=1)

        # 底部工具按钮（Fluent 风格，极简）
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)

        # 常用命令条：下拉按钮 + 管理入口
        self._cmd_menu_btn = DropDownPushButton("常用命令")
        self._cmd_menu_btn.setFocusPolicy(Qt.NoFocus)
        btn_layout.addWidget(self._cmd_menu_btn)

        self._manage_cmd_btn = TransparentToolButton(FluentIcon.EDIT, self)
        self._manage_cmd_btn.setToolTip("管理常用命令（增删，保存到 settings.json）")
        self._manage_cmd_btn.setFocusPolicy(Qt.NoFocus)
        self._manage_cmd_btn.clicked.connect(self._manage_commands)
        btn_layout.addWidget(self._manage_cmd_btn)

        btn_layout.addStretch()

        # P1-2：远端工作目录 chip + 一键在同目录开 SFTP（目录由 shell 上报 OSC 7；
        # 远端没配 PROMPT_COMMAND 时 chip 为空、按钮置灰，终端行为不受影响）
        self._lbl_cwd = BodyLabel('')
        self._lbl_cwd.setStyleSheet('color: gray;')
        self._lbl_cwd.setToolTip('远端工作目录（shell 通过 OSC 7 上报）')
        btn_layout.addWidget(self._lbl_cwd)

        self._btn_sftp_here = TransparentToolButton(FluentIcon.FOLDER, self)
        self._btn_sftp_here.setFocusPolicy(Qt.NoFocus)
        self._btn_sftp_here.setToolTip('在 SFTP 中打开此目录')
        self._btn_sftp_here.setEnabled(False)
        self._btn_sftp_here.clicked.connect(self._open_sftp_here)
        btn_layout.addWidget(self._btn_sftp_here)

        self._forensic_btn = PushButton("一键取证")
        self._forensic_btn.setFocusPolicy(Qt.NoFocus)
        self._forensic_btn.setToolTip(_FORENSIC_BTN_TIP)
        self._forensic_btn.setEnabled(False)
        self._forensic_btn.clicked.connect(self._start_forensic)
        btn_layout.addWidget(self._forensic_btn)

        self._session_dir_btn = PushButton("会话记录")
        self._session_dir_btn.setFocusPolicy(Qt.NoFocus)
        self._session_dir_btn.setToolTip("在资源管理器中打开会话日志目录")
        self._session_dir_btn.clicked.connect(self._open_session_dir)
        btn_layout.addWidget(self._session_dir_btn)

        # P2-3：取证报告目录常驻入口（此前只有 InfoBar 上的瞬时动作可打开，
        # 6s 后报告路径无处可寻，只能手动翻 logs/forensic）
        self._forensic_dir_btn = PushButton("取证记录")
        self._forensic_dir_btn.setFocusPolicy(Qt.NoFocus)
        self._forensic_dir_btn.setToolTip("在资源管理器中打开取证报告目录（logs/forensic）")
        self._forensic_dir_btn.clicked.connect(self._open_forensic_dir)
        btn_layout.addWidget(self._forensic_dir_btn)

        self._cmd_btn = PushButton("CMD 打开")
        self._cmd_btn.setFocusPolicy(Qt.NoFocus)  # 不抢焦点
        self._cmd_btn.clicked.connect(self._open_in_cmd)
        btn_layout.addWidget(self._cmd_btn)

        self._xshell_btn = PushButton("Xshell 打开")
        self._xshell_btn.setFocusPolicy(Qt.NoFocus)  # 不抢焦点
        self._xshell_btn.clicked.connect(self._open_in_xshell)
        btn_layout.addWidget(self._xshell_btn)

        # P1-7：常驻连接状态灯（读现有状态，不新增探测通道；与 SFTP 面板
        # 同色语义/同周期：绿=已连接、红=已断开、灰=连接中）
        self._lbl_health = BodyLabel('●')
        self._lbl_health.setStyleSheet('color: gray; font-size: 14px;')
        self._lbl_health.setToolTip('连接状态: 未连接')
        btn_layout.addWidget(self._lbl_health)
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(5000)
        self._health_timer.timeout.connect(self._update_conn_health)
        self._health_timer.start()

        layout.addLayout(btn_layout)

        # 初始化常用命令下拉菜单（从 settings.json 读取）
        self._rebuild_cmd_menu()

    # ─── SSH 连接 ─────────────────────────────────────────────────────────

    def _connect_ssh(self):
        """异步建立 SSH 连接"""
        self._terminal.write_output(f"正在连接 {self._host}:{self._port} ...\r\n")
        worker = SSHConnectWorker(self._host, self._port, self._username, self._password)
        worker.connected.connect(self._on_connected)
        worker.error.connect(self._on_connect_error)
        self._connect_worker = worker
        worker.start()

    def _on_connected(self, client):
        """SSH 连接成功 → 创建交互式 shell"""
        self._client = client
        self._cleanup_connect_worker()
        try:
            # 尺寸权威：开 PTY 之前先把网格对齐到当前视口，再把这个尺寸开给 PTY。
            # 远端 ncurses 全屏应用（nano/vim/less）完全按 PTY 尺寸排版，
            # 写死 120x40 会与控件可视区不符（隐藏页签/恢复会话时会立刻看出错位）。
            self._terminal.sync_grid(force=True)
            cols, rows = self._terminal.grid_size()
            channel = client.invoke_shell(term='xterm-256color', width=cols, height=rows)
            self._pty_size_sent = (cols, rows)
            channel.settimeout(0.1)  # 短超时：reader 线程可及时响应 stop 标志
            self._channel = channel
            # 启动 reader 守护线程
            self._stop_event.clear()
            self._reader_thread = threading.Thread(
                target=self._reader_loop, daemon=True, name='ssh-shell-reader'
            )
            self._reader_thread.start()
            # 启用终端键盘输入并聚焦
            self._terminal.set_input_enabled(True)
            self._terminal.setFocus()
            # 连接成功：隐藏重连条，开启新会话日志文件
            self._disconnected = False
            self._reconnecting = False
            self._cmd_buf.clear()
            self._cmd_esc = 0
            self._reconnect_btn.setEnabled(True)
            self._reconnect_btn.setText("重新连接")
            self._reconnect_bar.hide()
            # 连接就绪：启用一键取证按钮（重连成功后同样恢复）
            self._forensic_btn.setText("一键取证")
            self._forensic_btn.setEnabled(True)
            self._open_session_log()
            # P0-3：连接成功进主窗口日志（此前 SSH 连接事件零日志）
            self._log(f"[SSH] 已连接: {self._target_desc()}")
            self._set_health('green', '已连接')
            # P1-1：起始目录注入——shell 建立后发送 cd（PTY 输入缓冲排队，安全；
            # 经 _feed_session_input 让命令进会话日志，与手打一致）
            if self._start_dir:
                cmd = f"cd {shlex.quote(self._start_dir)}\r"
                try:
                    self._feed_session_input(cmd)
                    channel.send(cmd)
                except Exception:
                    pass
        except Exception as e:
            self._terminal.write_output(f"[错误] 创建交互式 Shell 失败: {e}\r\n")
            conn_logger.exception('SSH', '创建交互式 Shell 失败', exc=e,
                                  host=self._host, port=self._port)
            # Shell 创建失败也进入断线态，提供重连入口
            self._reconnecting = False
            self._disconnected = False
            self._on_link_lost()
            self._reconnect_label.setText("连接异常")

    def _on_connect_error(self, error):
        """SSH 连接失败 → 显示重连条（含初次连接与重连失败）"""
        self._cleanup_connect_worker()
        self._terminal.write_output(f"[连接失败] {error}\r\n")
        # P0-3：连接失败进主窗口日志
        self._log(f"[SSH] 连接失败: {self._target_desc()} - {error}")
        self._reconnecting = False
        self._disconnected = True
        self._forensic_btn.setEnabled(False)
        self._reconnect_label.setText("连接失败")
        self._reconnect_btn.setEnabled(True)
        self._reconnect_btn.setText("重新连接")
        self._reconnect_bar.show()
        self._set_health('red', '连接失败')

    def _cleanup_connect_worker(self):
        """非阻塞清理连接 worker（保持强引用直到线程真正结束）"""
        if self._connect_worker is not None:
            w = self._connect_worker
            self._connect_worker = None
            if hasattr(w, 'abort'):
                w.abort()
            if w.isRunning():
                _safe_release_worker(w)
            else:
                w.deleteLater()

    # ─── 交互式 Shell I/O ─────────────────────────────────────────────────

    def _reader_loop(self):
        """后台线程：持续读取 shell 输出并通过信号投递到 GUI 线程

        安全保证：
        - channel 上已设置 0.1s 超时，recv 不会无限阻塞
        - 所有 channel 操作仅在此线程内执行，主线程绝不触碰 channel
        """
        channel = self._channel
        # 增量解码：一个中文/emoji 字符可能被 recv 切成两块，
        # 逐块 decode 会把它们变成 U+FFFD（nano 中文界面尤其明显）
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        while not self._stop_event.is_set():
            try:
                if channel.recv_ready():
                    data = channel.recv(65536)
                    if not data:
                        if not self._closing:
                            self._output_signal.emit("\r\n[连接已断开]\r\n")
                            self._link_lost_signal.emit()  # P2-4：事件驱动断线态
                        break
                    text = decoder.decode(data)
                    if text:
                        self._output_signal.emit(text)
                else:
                    self._stop_event.wait(0.05)
            except socket.timeout:
                continue
            except OSError:
                if not self._closing and not self._stop_event.is_set():
                    self._output_signal.emit("\r\n[连接已断开]\r\n")
                    self._link_lost_signal.emit()  # P2-4
                break
            except Exception:
                if not self._stop_event.is_set():
                    self._output_signal.emit("\r\n[连接异常断开]\r\n")
                    self._link_lost_signal.emit()  # P2-4
                break

    def _on_shell_output(self, text: str):
        """GUI 线程槽：将 shell 输出写入 ANSI 终端控件，并同步追加到会话日志

        会话文件仅由 GUI 线程写入（reader 线程只投递信号），无并发。
        追加写 + flush 开销极小，不影响终端渲染。
        P2-4：断线判定改由 _link_lost_signal 事件驱动——此处若按显示文本
        匹配 '[连接已断开]'，远端程序（cat 日志/echo）打印同串会误入断线态。
        """
        if self._closing:
            return
        self._terminal.write_output(text)
        self._write_session(text)

    def _on_link_lost(self):
        """连接断开：关闭会话日志、禁用输入、显示重连条"""
        if self._disconnected:
            return
        self._disconnected = True
        self._close_session_log("断开")
        self._terminal.set_input_enabled(False)
        self._forensic_btn.setEnabled(False)
        self._reconnect_label.setText("连接已断开")
        self._reconnect_btn.setEnabled(True)
        self._reconnect_btn.setText("重新连接")
        self._reconnect_bar.show()
        # P0-3：断开事件进主窗口日志（此前终端断线在主窗口侧完全不可见）
        self._log(f"[SSH] 连接已断开: {self._target_desc()}")
        self._set_health('red', '连接已断开')

    def _on_key_input(self, data: str):
        """终端控件键盘输入 → 重组命令行写入会话日志，并发送到远端 shell"""
        self._feed_session_input(data)
        if self._channel is None or self._channel.closed:
            return
        try:
            self._channel.send(data)
        except Exception:
            pass

    def _on_grid_resized(self, cols: int, rows: int):
        """终端网格尺寸变化 → 通知远端 PTY 重排（SIGWINCH，带去抖）

        全屏应用（nano/vim/less/top）按 PTY 尺寸决定排版；不同步就会出现
        内容按旧尺寸画、控件按新尺寸显示导致的错位。
        """
        self._pending_pty_size = (cols, rows)
        if not self._pty_timer.isActive():
            self._pty_timer.start()

    def _apply_pty_size(self):
        """去抖后真正下发尺寸；与"已同步尺寸"相同则不发（避免无谓重绘）"""
        size = self._pending_pty_size
        channel = self._channel
        if size is None or channel is None or channel.closed:
            return
        if size == self._pty_size_sent:
            return
        try:
            channel.resize_pty(width=size[0], height=size[1])
            self._pty_size_sent = size
        except Exception:
            pass  # 尺寸同步失败不影响会话，下次变化再试

    # ─── 会话记录器（A4） ─────────────────────────────────────────

    def _open_session_log(self):
        """创建本次会话的日志文件（连接成功后调用，重连会开启新文件）"""
        self._close_session_log()
        try:
            session_dir = get_session_log_dir()
            os.makedirs(session_dir, exist_ok=True)
            self._cleanup_session_logs(session_dir)
            now = datetime.now()
            tag = self._server_name or self._host
            tag = re.sub(r'[\\/:*?"<>|\s]+', '_', tag).strip('_') or 'ssh'
            path = os.path.join(session_dir, f"{now:%Y%m%d_%H%M%S}_{tag}.log")
            self._session_file = open(path, 'a', encoding='utf-8', errors='replace')
            self._session_path = path
            target = f"{self._username}@{self._host}:{self._port}"
            if self._server_name:
                target += f" ({self._server_name})"
            self._write_session(
                "================ SSH 会话开始 ================\n"
                f"时间 : {now:%Y-%m-%d %H:%M:%S}\n"
                f"目标 : {target}\n\n"
            )
        except Exception as e:
            self._session_file = None
            self._session_path = None
            conn_logger.exception('SSH', '创建会话日志文件失败', exc=e,
                                  host=self._host, port=self._port)

    @staticmethod
    def _cleanup_session_logs(session_dir: str):
        """会话日志闭环清理：每次新建会话前执行，防止目录无限增长占满磁盘。
        默认保留 30 天内且不超过 500 个，可用 settings.json 的
        ssh_session_log_retention_days / ssh_session_log_max_files 调整。"""
        try:
            settings = _load_settings()
            max_age = int(settings.get("ssh_session_log_retention_days", 30))
            max_files = int(settings.get("ssh_session_log_max_files", 500))
            removed = cleanup_log_dir(session_dir, max_files=max_files,
                                      max_age_days=max_age, suffix='.log')
            if removed:
                conn_logger.info('SSH', f'会话日志闭环清理: 删除 {removed} 个历史文件')
        except Exception:
            pass  # 清理失败不影响会话

    def _write_session(self, text: str):
        """剥离 ANSI 后追加写入会话日志（仅 GUI 线程调用，写入失败静默降级）"""
        if self._session_file is None:
            return
        try:
            stripped = _strip_ansi(text)
            if self._terminal.alt_screen:
                # 全屏应用（nano/vim/top）不靠 \n 换行，只有裸 \r + 定位序列；
                # 不归一化的话整屏日志会挤成一行。普通 shell 保留 \r
                # （进度条靠它原地刷新，不能变成一堆换行）。
                stripped = stripped.replace('\r\n', '\n').replace('\r', '\n')
            self._session_file.write(stripped)
            self._session_file.flush()
        except Exception:
            pass

    def _close_session_log(self, reason: str = "结束"):
        """写入结束标记并关闭会话日志文件（幂等）"""
        if self._session_file is None:
            return
        try:
            self._session_file.write(
                f"\n================ 会话{reason} "
                f"{datetime.now():%Y-%m-%d %H:%M:%S} ================\n"
            )
            self._session_file.flush()
            self._session_file.close()
        except Exception:
            pass
        self._session_file = None
        self._session_path = None

    def _feed_session_input(self, data: str):
        """从键流重组命令行，回车时以 '> ' 前缀写入会话日志

        处理退格/Ctrl+C/Ctrl+U 编辑行为，跳过转义序列字符（方向键/功能键不进日志），
        使日志中的命令行与用户最终确认的内容一致。
        终止判据按 VT 规范：CSI 的终止字节是 0x40-0x7E（不只是字母），
        否则 ``\\EOA``（应用模式方向键）会漏一个 'A' 到日志里。
        """
        buf = self._cmd_buf
        for ch in data:
            if self._cmd_esc == 1:                      # ESC 之后第一个字节
                if ch in '([)*+-./#%O':
                    self._cmd_esc = 3                   # 字符集设计符 / SS3：再吃一字节
                elif ch in '[]':
                    self._cmd_esc = 2                   # CSI / OSC：吃到终止字节
                else:
                    self._cmd_esc = 0                   # 两字节转义，到此为止
                continue
            if self._cmd_esc == 2:                      # CSI / OSC 内部
                if ch == '\x1b':
                    self._cmd_esc = 1
                elif ch == '\x07' or '\x40' <= ch <= '\x7e':
                    self._cmd_esc = 0
                continue
            if self._cmd_esc == 3:                      # 三字节序列收尾
                self._cmd_esc = 0
                continue
            if ch == '\x1b':
                self._cmd_esc = 1
            elif ch == '\r':
                line = ''.join(buf).strip()
                if line:
                    self._write_session(f"> {line}\n")
                buf.clear()
            elif ch in ('\x7f', '\b'):
                if buf:
                    buf.pop()
            elif ch in ('\x03', '\x15'):  # Ctrl+C / Ctrl+U 清空当前行
                buf.clear()
            elif ch.isprintable():
                buf.append(ch)

    def _open_session_dir(self):
        """在资源管理器中打开会话日志目录"""
        path = get_session_log_dir()
        try:
            os.makedirs(path, exist_ok=True)
            subprocess.Popen(['explorer', path])
        except Exception as e:
            self._log(f"[SSH] 打开会话日志目录失败: {e}")

    # ─── 断线重连（B2） ─────────────────────────────────────────────

    def _reconnect_clicked(self):
        """点击重新连接：禁用输入，清理已死连接，复用现有连接路径重建"""
        if self._reconnecting or self._closing:
            return
        self._reconnecting = True
        self._reconnect_btn.setEnabled(False)
        self._reconnect_btn.setText("重连中...")
        self._terminal.set_input_enabled(False)
        # P0-3：重连动作进主窗口日志
        self._log(f"[SSH] 正在重新连接: {self._target_desc()}")
        self._set_health('gray', '连接中...')
        self._cleanup_dead_connection()
        self._connect_ssh()

    # ─── 连接状态指示（P1-7） ─────────────────────────────────────────────

    def _update_conn_health(self):
        """定时读现有连接状态更新指示灯（只读探针，不新增探测通道）"""
        if self._closing:
            return
        if self._client is not None:
            try:
                t = self._client.get_transport()
                active = bool(t is not None and t.is_active())
            except Exception:
                active = False
        else:
            active = False
        if active:
            self._set_health('green', '已连接')
        elif self._disconnected:
            self._set_health('red', '连接已断开')
        else:
            self._set_health('gray', '连接中...')

    def _set_health(self, color, text):
        """设置状态灯颜色与提示（色值与 SFTP 面板健康灯一致）"""
        color_map = {'green': '#4CAF50', 'orange': '#FF9800',
                     'red': '#F44336', 'gray': 'gray'}
        try:
            self._lbl_health.setStyleSheet(
                f'color: {color_map.get(color, "gray")}; font-size: 14px;')
            self._lbl_health.setToolTip(f'连接状态: {text}')
        except Exception:
            pass

    def _cleanup_dead_connection(self):
        """清理已断开的 channel/client（reader 线程已退出/即将退出，先等待确保无并发）"""
        self._stop_event.set()
        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)
        self._reader_thread = None
        if self._channel is not None:
            try:
                self._channel.close()
            except Exception:
                pass
            self._channel = None
        if self._client is not None:
            try:
                safe_close_transport(self._client.get_transport())
            except Exception:
                pass
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    # ─── 一键取证（D2） ───────────────────────────────────────────────────

    def _start_forensic(self):
        """启动后台取证：进度显示在按钮上，运行中再点 = 请求取消（P2-3）

        三重守卫保留：closing / disconnected / client·channel 失效时不启动。
        """
        if (self._closing or self._disconnected or self._client is None
                or self._channel is None or self._channel.closed):
            return
        w = self._forensic_worker
        if w is not None and w.isRunning():
            # P2-3：运行中点击 = 取消（当前命令执行完/超时后停止，不生成报告）
            w.cancel()
            self._forensic_btn.setEnabled(False)
            self._forensic_btn.setText("正在取消...")
            self._log(f"[SSH] 已请求取消取证: {self._target_desc()}")
            return
        # P2-3：保持可点以支持取消；tooltip 提示
        self._forensic_btn.setEnabled(True)
        self._forensic_btn.setText("取证中...")
        self._forensic_btn.setToolTip("点击取消取证")
        # P0-3：取证开始进主窗口日志
        self._log(f"[SSH] 取证开始: {self._target_desc()}")
        worker = ForensicWorker(
            self._client, self._host, self._port, self._username,
            server_name=self._server_name, session_log_path=self._session_path)
        worker.progress.connect(self._on_forensic_progress)
        worker.report_ready.connect(self._on_forensic_done)
        worker.failed.connect(self._on_forensic_failed)
        worker.cancelled.connect(self._on_forensic_cancelled)  # P2-3
        self._forensic_worker = worker
        _safe_release_worker(worker)
        worker.start()

    def _on_forensic_progress(self, idx: int, total: int, title: str):
        """取证进度：按钮文字显示 (当前/总数)，保持可点（点击=取消）"""
        self._forensic_btn.setText(f"取证中 {idx}/{total}")

    def _on_forensic_cancelled(self):
        """P2-3：取证被用户取消——恢复按钮（连接存活即可重新发起）"""
        self._restore_forensic_btn()
        self._log(f"[SSH] 取证已取消（未生成报告）: {self._target_desc()}")
        show_info_bar("取证已取消，未生成报告", "warning",
                      title="一键取证", parent=self, duration=4000)

    def _on_forensic_done(self, path: str):
        """取证完成：InfoBar 成功提示 + “打开报告文件”动作"""
        self._restore_forensic_btn()
        self._log(f"[SSH] 取证完成，报告: {path}")
        bar = show_info_bar(f"报告已生成：{os.path.basename(path)}", "success",
                            title="取证完成", parent=self, duration=6000)
        act = QAction("打开报告文件", bar)
        act.triggered.connect(lambda checked=False, p=path: self._reveal_forensic_report(p))
        bar.addAction(act)

    def _on_forensic_failed(self, msg: str):
        """取证失败：恢复按钮并提示错误首行"""
        self._restore_forensic_btn()
        first_line = str(msg or "未知错误").splitlines()[0] if msg else "未知错误"
        self._log(f"[SSH] 取证失败: {first_line}")
        show_info_bar(first_line, "error", title="取证失败", parent=self, duration=5000)

    def _restore_forensic_btn(self):
        """恢复取证按钮文字/可用性/提示（仅连接存活时可用；失败后再点即重试）"""
        self._forensic_btn.setText("一键取证")
        self._forensic_btn.setToolTip(_FORENSIC_BTN_TIP)
        connected = (self._client is not None and not self._disconnected
                     and not self._closing)
        self._forensic_btn.setEnabled(connected)

    def _open_forensic_dir(self):
        """P2-3：打开取证报告目录（InfoBar 6s 消失后的常驻回访入口）"""
        path = get_forensic_dir()
        try:
            os.makedirs(path, exist_ok=True)
            subprocess.Popen(['explorer', path])
        except Exception as e:
            self._log(f"[SSH] 打开取证报告目录失败: {e}")

    def _reveal_forensic_report(self, path: str):
        """资源管理器中定位并选中报告文件"""
        try:
            subprocess.Popen(['explorer', f'/select,{path}'])
        except Exception as e:
            self._log(f"[SSH] 打开报告文件失败: {e}")

    # ─── 常用命令条（B4） ─────────────────────────────────────────────

    def _rebuild_cmd_menu(self):
        """从 settings.json 重建常用命令下拉菜单"""
        settings = _load_settings()
        commands = settings.get("ssh_commands")
        if not isinstance(commands, list) or not commands:
            commands = list(DEFAULT_SSH_COMMANDS)
        self._auto_run = bool(settings.get("ssh_cmd_auto_run", False))

        menu = RoundMenu("常用命令", self)
        for cmd in commands:
            act = QAction(str(cmd), menu)
            act.triggered.connect(lambda checked=False, c=str(cmd): self._send_command(c))
            menu.addAction(act)
        menu.addSeparator()
        self._auto_run_act = QAction("发送后自动执行（追加回车）", menu)
        self._auto_run_act.setCheckable(True)
        self._auto_run_act.setChecked(self._auto_run)
        self._auto_run_act.toggled.connect(self._toggle_auto_run)
        menu.addAction(self._auto_run_act)
        manage_act = QAction("管理命令...", menu)
        manage_act.triggered.connect(self._manage_commands)
        menu.addAction(manage_act)
        self._cmd_menu_btn.setMenu(menu)

    def _send_command(self, cmd: str):
        """通过现有键盘注入通道发送命令（默认不自动回车，供用户确认）"""
        if self._channel is None or self._channel.closed:
            self._terminal.write_output("[提示] 未连接，无法发送命令\r\n")
            return
        payload = cmd + ('\r' if self._auto_run else '')
        self._on_key_input(payload)
        self._terminal.setFocus()

    def _toggle_auto_run(self, checked: bool):
        """切换“发送并执行”选项，持久化到 settings.json"""
        self._auto_run = checked
        _save_settings({"ssh_cmd_auto_run": checked})

    def _manage_commands(self):
        """打开常用命令管理对话框，保存后即时刷新下拉菜单"""
        settings = _load_settings()
        commands = settings.get("ssh_commands")
        if not isinstance(commands, list) or not commands:
            commands = list(DEFAULT_SSH_COMMANDS)
        dlg = SshCommandEditDialog(commands, self)
        if dlg.exec() == QDialog.Accepted:
            _save_settings({"ssh_commands": dlg.commands()})
            self._rebuild_cmd_menu()

    # ─── 外部客户端 ───────────────────────────────────────────────────────

    def _open_in_cmd(self):
        """在系统 CMD 中打开 SSH 连接"""
        if not shutil.which('ssh'):
            w = MessageBox(
                "未找到 SSH 客户端",
                "系统中未安装 OpenSSH 客户端。\n"
                "请在 Windows 设置 > 应用 > 可选功能 中安装 OpenSSH 客户端。",
                self
            )
            w.yesButton.setText("确定")
            w.cancelButton.hide()
            w.exec()
            return
        cmd = f'ssh -p {self._port} {self._username}@{self._host}'
        try:
            subprocess.Popen(['cmd', '/k', cmd], creationflags=subprocess.CREATE_NEW_CONSOLE)
        except Exception as e:
            conn_logger.exception('SSH', '启动 CMD 终端失败', exc=e,
                                  host=self._host, port=self._port)

    def _open_in_xshell(self):
        """使用 Xshell 打开 SSH 连接"""
        xshell_path = shutil.which('xshell') or shutil.which('Xshell')
        if not xshell_path:
            self._log("[SSH] 未找到 Xshell，请确认已安装并加入系统 PATH")
            return
        xshell_url = f'ssh://{self._username}:{self._password}@{self._host}:{self._port}'
        try:
            subprocess.Popen([xshell_path, '-url', xshell_url])
        except Exception as e:
            self._log(f"[SSH] 启动 Xshell 失败: {e}")

    # ─── 生命周期 ─────────────────────────────────────────────────────────

    def shutdown(self):
        """安全关闭：先停 reader 线程，再关 transport，消除 C 层并发竞态。

        由容器（标签页关闭）或 QDialog.closeEvent 调用。可重复调用，幂等安全。
        """
        if self._closing:
            return
        self._closing = True
        self._pty_timer.stop()
        # P1-7：停状态灯轮询
        if hasattr(self, '_health_timer'):
            self._health_timer.stop()
        self._pending_pty_size = None
        self._pty_size_sent = None
        self._terminal.set_input_enabled(False)
        # 1. 通知 reader 线程退出
        self._stop_event.set()
        # 2. 等待 reader 线程结束（recv 超时 0.1s + 循环检查，正常 ~0.5s 内退出）。
        #    P1-3（2026-09-24）：上限 2.0s→0.8s——超时后旧代码同样继续关
        #    channel，缩短上限只减最坏阻塞不改变语义（关闭标签不卡 GUI）
        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=0.8)
        self._reader_thread = None
        # 3. reader 已退出，安全关闭 channel（此时无并发访问）
        if self._channel:
            try:
                self._channel.close()
            except Exception:
                pass
            self._channel = None
        # 4. 取证 worker：不再等待（P1-3：旧 wait(2000) 白白拖慢关闭）——
        #    连接关闭后其逐条命令会快速失败自结束，生命周期已由
        #    _safe_release_worker 的 _pending_workers 强引用托管
        #    （finished→deleteLater），面板引用置空即可
        self._forensic_worker = None
        # 5. 关闭 SSH client（close+join 等待 transport 后台线程退出）
        if self._client:
            try:
                transport = self._client.get_transport()
                safe_close_transport(transport)
            except Exception:
                pass
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        # 6. 清理 connect worker
        self._cleanup_connect_worker()
        # 7. 关闭会话日志（写入结束标记）
        self._close_session_log("关闭")


class SshCommandEditDialog(QDialog):
    """常用命令管理对话框：增删命令，保存写入 settings.json 的 ssh_commands"""

    def __init__(self, commands, parent=None):
        super().__init__(parent)
        self.setWindowTitle("管理常用命令")
        self.resize(420, 360)
        self._commands = [str(c) for c in commands]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self._list = QListWidget(self)
        self._list.addItems(self._commands)
        layout.addWidget(self._list, stretch=1)

        add_row = QHBoxLayout()
        self._edit = LineEdit(self)
        self._edit.setPlaceholderText("输入新命令，如 systemctl status snooker")
        self._edit.returnPressed.connect(self._add)
        add_row.addWidget(self._edit, stretch=1)
        add_btn = PushButton("添加")
        add_btn.clicked.connect(self._add)
        add_row.addWidget(add_btn)
        layout.addLayout(add_row)

        op_row = QHBoxLayout()
        del_btn = PushButton("删除选中")
        del_btn.clicked.connect(self._remove)
        op_row.addWidget(del_btn)
        op_row.addStretch()
        save_btn = PrimaryPushButton("保存")
        save_btn.clicked.connect(self.accept)
        op_row.addWidget(save_btn)
        layout.addLayout(op_row)

    def _add(self):
        text = self._edit.text().strip()
        if not text:
            return
        self._commands.append(text)
        self._list.addItem(text)
        self._edit.clear()
        self._edit.setFocus()

    def _remove(self):
        row = self._list.currentRow()
        if row >= 0:
            self._list.takeItem(row)
            del self._commands[row]

    def commands(self):
        """返回当前命令列表（保持顺序）"""
        return list(self._commands)


class SSHTerminalWindow(QDialog):
    """SSH 终端独立窗口（向后兼容的薄壳，内部委托 SSHTerminalPanel）"""

    def __init__(self, host, port, username, password, log_callback=None, parent=None,
                 start_dir=''):
        super().__init__(parent)
        apply_window_qss(self)
        self.setWindowTitle(f"SSH 终端 - {host}:{port}")
        self.resize(900, 560)
        self._panel = SSHTerminalPanel(
            host, port, username, password,
            log_callback=log_callback, parent=self, start_dir=start_dir
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._panel)

    def focusNextPrevChild(self, next_: bool) -> bool:
        return False

    def closeEvent(self, event):
        self._panel.shutdown()
        super().closeEvent(event)
