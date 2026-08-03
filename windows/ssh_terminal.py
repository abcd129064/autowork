# -*- coding: utf-8 -*-
"""SSH 终端（invoke_shell 交互式 PTY + ANSI 虚拟终端 + 直接键盘输入）

体验与 Windows Terminal / Xshell 一致：
- 直接在终端区域打字，shell 回显
- Tab 命令/路径补全
- 上下键命令历史
- Ctrl+C/D/L 等控制键
- ANSI 彩色输出正确渲染
- nano/vim 全屏应用（备用屏幕切换）

安全关闭策略（规避 C 层 Use-After-Free 崩溃）：
- channel 上设置 0.1s recv 超时，reader 线程可被 stop 标志及时中断
- shutdown() 仅设置 stop 标志并等待 reader 线程退出，绝不从主线程操作 channel
- reader 线程退出后再关闭 transport，消除并发竞态

架构：
- SSHTerminalPanel(QWidget)：可嵌入标签页的核心面板
- SSHTerminalWindow(QDialog)：独立窗口薄壳（向后兼容）
"""

import shutil
import socket
import subprocess
import threading

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QWidget
from PySide6.QtCore import QTimer, Qt, Signal
from qfluentwidgets import PushButton, MessageBox, setFont

from core.conn_logger import conn_logger
from core.utils import safe_close_transport
from workers.network_workers import SSHConnectWorker
from windows.ansi_terminal import ANSITerminalWidget

# 模块级强引用集合：防止窗口关闭后 Python GC 回收仍在运行的 QThread 导致崩溃
_pending_workers: set = set()


def _safe_release_worker(w):
    """将 worker 放入 pending 集合，线程结束后自动移除并 deleteLater"""
    _pending_workers.add(w)
    w.finished.connect(lambda: (_pending_workers.discard(w), w.deleteLater()))


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

        # ANSI 终端（既是显示区域也是输入区域）
        self._terminal = ANSITerminalWidget(self)
        self._terminal.key_input.connect(self._on_key_input)
        layout.addWidget(self._terminal, stretch=1)

        # 底部工具按钮（Fluent 风格，极简）
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)
        btn_layout.addStretch()

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
        except Exception as e:
            self._terminal.write_output(f"[错误] 创建交互式 Shell 失败: {e}\r\n")
            conn_logger.exception('SSH', '创建交互式 Shell 失败', exc=e,
                                  host=self._host, port=self._port)

    def _on_connect_error(self, error):
        """SSH 连接失败"""
        self._cleanup_connect_worker()
        self._terminal.write_output(f"[连接失败] {error}\r\n")

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
                break
            except Exception:
                if not self._stop_event.is_set():
                    self._output_signal.emit("\r\n[连接异常断开]\r\n")
                break

    def _on_shell_output(self, text: str):
        """GUI 线程槽：将 shell 输出写入 ANSI 终端控件"""
        if self._closing:
            return
        self._terminal.write_output(text)

    def _on_key_input(self, data: str):
        """终端控件键盘输入 → 发送到远端 shell"""
        if self._channel is None or self._channel.closed:
            return
        try:
            self._channel.send(data)
        except Exception:
            pass

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
        # 4. 关闭 SSH client（close+join 等待 transport 后台线程退出）
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
        # 5. 清理 connect worker
        self._cleanup_connect_worker()


class SSHTerminalWindow(QDialog):
    """SSH 终端独立窗口（向后兼容的薄壳，内部委托 SSHTerminalPanel）"""

    def __init__(self, host, port, username, password, log_callback=None, parent=None):
        super().__init__(parent)
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
