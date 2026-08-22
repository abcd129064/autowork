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

import json
import os
import re
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
                            setFont, RoundMenu)

from core.app_paths import get_app_dir
from core.conn_logger import conn_logger
from core.theme_qss import apply_window_qss
from core.utils import safe_close_transport, cleanup_log_dir, show_info_bar
from workers.network_workers import SSHConnectWorker
from windows.ansi_terminal import ANSITerminalWidget
from windows.forensic_report import ForensicWorker

# 模块级强引用集合：防止窗口关闭后 Python GC 回收仍在运行的 QThread 导致崩溃
_pending_workers: set = set()


def _safe_release_worker(w):
    """将 worker 放入 pending 集合，线程结束后自动移除并 deleteLater"""
    _pending_workers.add(w)
    w.finished.connect(lambda: (_pending_workers.discard(w), w.deleteLater()))


# ─── settings.json 读写（常用命令条配置） ─────────────────────────────────

# 常用命令默认占位示例（settings.json 无 ssh_commands 字段时使用）
DEFAULT_SSH_COMMANDS = ["top", "df -h", "journalctl -n 50"]


def _load_settings() -> dict:
    """读取 settings.json，失败时返回空字典"""
    try:
        with open(os.path.join(get_app_dir(), "settings.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_settings(data: dict):
    """合并写入 settings.json"""
    try:
        settings = _load_settings()
        settings.update(data)
        with open(os.path.join(get_app_dir(), "settings.json"), "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ─── 会话日志工具 ─────────────────────────────────────────────────────────

def get_session_log_dir() -> str:
    """SSH 会话日志目录：{app_dir}/logs/ssh_sessions（与 conn_logger 的 logs/ 同级机制）"""
    return os.path.join(get_app_dir(), "logs", "ssh_sessions")


# ANSI 转义序列剥离正则（会话日志写纯文本，便于检索）
_ANSI_RE = re.compile(
    r'\x1b\[[0-9;?]*[a-zA-Z]'               # CSI 序列
    r'|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?'  # OSC 序列
    r'|\x1b[@-Z\\-_]'                        # 其他两字节转义
)


def _strip_ansi(text: str) -> str:
    """剥离 ANSI 转义序列，返回纯文本"""
    return _ANSI_RE.sub('', text)


class SSHTerminalPanel(QWidget):
    """SSH 终端面板（可嵌入标签页容器，也可独立使用）

    核心逻辑：invoke_shell 交互式 PTY + ANSI 渲染 + 直接键盘输入。
    资源清理统一由 shutdown() 方法负责，容器关闭标签时调用。
    """

    # reader 线程通过此信号将输出安全投递到 GUI 线程
    _output_signal = Signal(str)

    def __init__(self, host, port, username, password, log_callback=None, parent=None,
                 server_name=''):
        super().__init__(parent)
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._server_name = server_name
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
        self._cmd_esc = False
        # 断线重连状态
        self._disconnected = False
        self._reconnecting = False
        self._auto_run = False
        # 一键取证（D2）后台 worker 引用
        self._forensic_worker = None
        self._init_ui()
        self._output_signal.connect(self._on_shell_output)
        QTimer.singleShot(100, self._connect_ssh)

    def focusNextPrevChild(self, next_: bool) -> bool:
        """禁止 Tab 焦点导航，确保 Tab 始终发送到终端"""
        return False

    @property
    def tab_title(self) -> str:
        """返回适合标签页显示的标题"""
        if getattr(self, '_server_name', ''):
            return f"SSH - {self._server_name}"
        return f"SSH - {self._host}:{self._port}"

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
        layout.addWidget(self._terminal, stretch=1)

        # 底部工具按钮（Fluent 风格，极简）
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)

        # 常用命令条：下拉按钮 + 管理入口
        self._cmd_menu_btn = DropDownPushButton("常用命令")
        setFont(self._cmd_menu_btn, 11)
        self._cmd_menu_btn.setFocusPolicy(Qt.NoFocus)
        btn_layout.addWidget(self._cmd_menu_btn)

        self._manage_cmd_btn = TransparentToolButton(FluentIcon.EDIT, self)
        self._manage_cmd_btn.setToolTip("管理常用命令（增删，保存到 settings.json）")
        self._manage_cmd_btn.setFocusPolicy(Qt.NoFocus)
        self._manage_cmd_btn.clicked.connect(self._manage_commands)
        btn_layout.addWidget(self._manage_cmd_btn)

        btn_layout.addStretch()

        self._forensic_btn = PushButton("一键取证")
        setFont(self._forensic_btn, 11)
        self._forensic_btn.setFocusPolicy(Qt.NoFocus)
        self._forensic_btn.setToolTip(
            "后台运行预置诊断命令组（含 dmesg/journalctl/syslog 系统错误日志）"
            "并汇总会话/连接日志、设备状态，调用 AI 大模型分析生成故障取证报告"
            "（厂商可在设置面板「AI 分析」页配置，仅连接建立后可用）")
        self._forensic_btn.setEnabled(False)
        self._forensic_btn.clicked.connect(self._start_forensic)
        btn_layout.addWidget(self._forensic_btn)

        self._session_dir_btn = PushButton("会话记录")
        setFont(self._session_dir_btn, 11)
        self._session_dir_btn.setFocusPolicy(Qt.NoFocus)
        self._session_dir_btn.setToolTip("在资源管理器中打开会话日志目录")
        self._session_dir_btn.clicked.connect(self._open_session_dir)
        btn_layout.addWidget(self._session_dir_btn)

        self._cmd_btn = PushButton("CMD 打开")
        setFont(self._cmd_btn, 11)
        self._cmd_btn.setFocusPolicy(Qt.NoFocus)  # 不抢焦点
        self._cmd_btn.clicked.connect(self._open_in_cmd)
        btn_layout.addWidget(self._cmd_btn)

        self._xshell_btn = PushButton("Xshell 打开")
        setFont(self._xshell_btn, 11)
        self._xshell_btn.setFocusPolicy(Qt.NoFocus)  # 不抢焦点
        self._xshell_btn.clicked.connect(self._open_in_xshell)
        btn_layout.addWidget(self._xshell_btn)

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
            channel = client.invoke_shell(term='xterm-256color', width=120, height=40)
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
            self._cmd_esc = False
            self._reconnect_btn.setEnabled(True)
            self._reconnect_btn.setText("重新连接")
            self._reconnect_bar.hide()
            # 连接就绪：启用一键取证按钮（重连成功后同样恢复）
            self._forensic_btn.setText("一键取证")
            self._forensic_btn.setEnabled(True)
            self._open_session_log()
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
        self._reconnecting = False
        self._disconnected = True
        self._forensic_btn.setEnabled(False)
        self._reconnect_label.setText("连接失败")
        self._reconnect_btn.setEnabled(True)
        self._reconnect_btn.setText("重新连接")
        self._reconnect_bar.show()

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
        while not self._stop_event.is_set():
            try:
                if channel.recv_ready():
                    data = channel.recv(65536)
                    if not data:
                        if not self._closing:
                            self._output_signal.emit("\r\n[连接已断开]\r\n")
                        break
                    self._output_signal.emit(data.decode('utf-8', errors='replace'))
                else:
                    self._stop_event.wait(0.05)
            except socket.timeout:
                continue
            except OSError:
                if not self._closing and not self._stop_event.is_set():
                    self._output_signal.emit("\r\n[连接已断开]\r\n")
                break
            except Exception:
                if not self._stop_event.is_set():
                    self._output_signal.emit("\r\n[连接异常断开]\r\n")
                break

    def _on_shell_output(self, text: str):
        """GUI 线程槽：将 shell 输出写入 ANSI 终端控件，并同步追加到会话日志

        会话文件仅由 GUI 线程写入（reader 线程只投递信号），无并发。
        追加写 + flush 开销极小，不影响终端渲染。
        """
        if self._closing:
            return
        self._terminal.write_output(text)
        self._write_session(text)
        # 检测断开标记 → 进入断线状态（显示重连条）
        if '[连接已断开]' in text or '[连接异常断开]' in text:
            self._on_link_lost()

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

    def _on_key_input(self, data: str):
        """终端控件键盘输入 → 重组命令行写入会话日志，并发送到远端 shell"""
        self._feed_session_input(data)
        if self._channel is None or self._channel.closed:
            return
        try:
            self._channel.send(data)
        except Exception:
            pass

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
            self._session_file.write(_strip_ansi(text))
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

        处理退格/Ctrl+C/Ctrl+U 编辑行为，跳过转义序列字符，
        使日志中的命令行与用户最终确认的内容一致。
        """
        buf = self._cmd_buf
        for ch in data:
            if self._cmd_esc:
                # 转义序列内部：消费至终止符（方向键/功能键等不入日志）
                if ch.isalpha() or ch == '~':
                    self._cmd_esc = False
                continue
            if ch == '\x1b':
                self._cmd_esc = True
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
            self._log(f"[提示] 打开会话日志目录失败: {e}")

    # ─── 断线重连（B2） ─────────────────────────────────────────────

    def _reconnect_clicked(self):
        """点击重新连接：禁用输入，清理已死连接，复用现有连接路径重建"""
        if self._reconnecting or self._closing:
            return
        self._reconnecting = True
        self._reconnect_btn.setEnabled(False)
        self._reconnect_btn.setText("重连中...")
        self._terminal.set_input_enabled(False)
        self._cleanup_dead_connection()
        self._connect_ssh()

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
        """启动后台取证：禁用按钮显示进度，完成后 InfoBar 提示并可打开报告"""
        if (self._closing or self._disconnected or self._client is None
                or self._channel is None or self._channel.closed):
            return
        if self._forensic_worker is not None and self._forensic_worker.isRunning():
            return
        self._forensic_btn.setEnabled(False)
        self._forensic_btn.setText("取证中...")
        worker = ForensicWorker(
            self._client, self._host, self._port, self._username,
            server_name=self._server_name, session_log_path=self._session_path)
        worker.progress.connect(self._on_forensic_progress)
        worker.report_ready.connect(self._on_forensic_done)
        worker.failed.connect(self._on_forensic_failed)
        self._forensic_worker = worker
        _safe_release_worker(worker)
        worker.start()

    def _on_forensic_progress(self, idx: int, total: int, title: str):
        """取证进度：按钮文字显示 (当前/总数)"""
        self._forensic_btn.setText(f"取证中 {idx}/{total}")

    def _on_forensic_done(self, path: str):
        """取证完成：InfoBar 成功提示 + “打开报告文件”动作"""
        self._restore_forensic_btn()
        bar = show_info_bar(f"报告已生成：{os.path.basename(path)}", "success",
                            title="取证完成", parent=self, duration=6000)
        act = QAction("打开报告文件", bar)
        act.triggered.connect(lambda checked=False, p=path: self._reveal_forensic_report(p))
        bar.addAction(act)

    def _on_forensic_failed(self, msg: str):
        """取证失败：恢复按钮并提示错误首行"""
        self._restore_forensic_btn()
        first_line = str(msg or "未知错误").splitlines()[0] if msg else "未知错误"
        show_info_bar(first_line, "error", title="取证失败", parent=self, duration=5000)

    def _restore_forensic_btn(self):
        """恢复取证按钮文字与可用性（仅连接存活时可用）"""
        self._forensic_btn.setText("一键取证")
        connected = (self._client is not None and not self._disconnected
                     and not self._closing)
        self._forensic_btn.setEnabled(connected)

    def _reveal_forensic_report(self, path: str):
        """资源管理器中定位并选中报告文件"""
        try:
            subprocess.Popen(['explorer', f'/select,{path}'])
        except Exception as e:
            self._log(f"[提示] 打开报告文件失败: {e}")

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
            self._log("[提示] 未找到 Xshell，请确认已安装并加入系统 PATH")
            return
        xshell_url = f'ssh://{self._username}:{self._password}@{self._host}:{self._port}'
        try:
            subprocess.Popen([xshell_path, '-url', xshell_url])
        except Exception as e:
            self._log(f"[提示] 启动 Xshell 失败: {e}")

    # ─── 生命周期 ─────────────────────────────────────────────────────────

    def shutdown(self):
        """安全关闭：先停 reader 线程，再关 transport，消除 C 层并发竞态。

        由容器（标签页关闭）或 QDialog.closeEvent 调用。可重复调用，幂等安全。
        """
        if self._closing:
            return
        self._closing = True
        self._terminal.set_input_enabled(False)
        # 1. 通知 reader 线程退出
        self._stop_event.set()
        # 2. 等待 reader 线程结束（recv 超时 0.1s + 循环检查，最多 ~0.5s）
        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2.0)
        self._reader_thread = None
        # 3. reader 已退出，安全关闭 channel（此时无并发访问）
        if self._channel:
            try:
                self._channel.close()
            except Exception:
                pass
            self._channel = None
        # 4. 取证 worker 短暂等待（连接关闭后其逐条命令会快速失败降级，不阻塞关闭）
        if self._forensic_worker is not None and self._forensic_worker.isRunning():
            self._forensic_worker.wait(2000)
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

    def __init__(self, host, port, username, password, log_callback=None, parent=None):
        super().__init__(parent)
        apply_window_qss(self)
        self.setWindowTitle(f"SSH 终端 - {host}:{port}")
        self.resize(900, 560)
        self._panel = SSHTerminalPanel(
            host, port, username, password,
            log_callback=log_callback, parent=self
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._panel)

    def focusNextPrevChild(self, next_: bool) -> bool:
        return False

    def closeEvent(self, event):
        self._panel.shutdown()
        super().closeEvent(event)
