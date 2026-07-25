# -*- coding: utf-8 -*-
"""SSH 终端窗口（exec_command 模式，底部输入框）"""

import shutil
import subprocess

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QPlainTextEdit, QMessageBox)
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont, QTextCursor

from core.conn_logger import conn_logger
from core.utils import safe_close_transport
from workers.network_workers import SSHConnectWorker, SSHExecWorker


class SSHTerminalWindow(QDialog):
    """SSH 终端窗口（exec_command 模式，底部输入框）"""

    def __init__(self, host, port, username, password, log_callback=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"SSH 终端 - {host}:{port}")
        self.resize(800, 500)
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._log = log_callback or (lambda msg: None)
        self._client = None
        self._connect_worker = None
        self._exec_worker = None
        self._init_ui()
        QTimer.singleShot(100, self._connect_ssh)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        # 输出区域（字体通过 setFont 设置，不在 QSS 中指定，以便全局字体变更能生效）
        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        self._output.setFont(QFont("Consolas", 10))
        self._output.setStyleSheet(
            "QPlainTextEdit { background-color: #1e1e1e; color: #00ff00; }"
        )
        layout.addWidget(self._output)

        # 输入区域
        input_layout = QHBoxLayout()
        self._prompt_label = QLabel("$")
        self._prompt_label.setFont(QFont("Consolas", 10))
        self._prompt_label.setStyleSheet("color: #00ff00;")
        input_layout.addWidget(self._prompt_label)

        self._input = QLineEdit()
        self._input.setFont(QFont("Consolas", 10))
        self._input.setStyleSheet(
            "QLineEdit { background-color: #2d2d2d; color: #00ff00; }"
        )
        self._input.setPlaceholderText("输入命令，回车执行...")
        self._input.returnPressed.connect(self._execute_command)
        self._input.setEnabled(False)  # 连接成功前禁用
        input_layout.addWidget(self._input)

        self._send_btn = QPushButton("执行")
        self._send_btn.clicked.connect(self._execute_command)
        self._send_btn.setEnabled(False)
        input_layout.addWidget(self._send_btn)

        self._cmd_btn = QPushButton("CMD")
        self._cmd_btn.clicked.connect(self._open_in_cmd)
        input_layout.addWidget(self._cmd_btn)

        self._xshell_btn = QPushButton("Xshell")
        self._xshell_btn.clicked.connect(self._open_in_xshell)
        input_layout.addWidget(self._xshell_btn)

        layout.addLayout(input_layout)

    def _connect_ssh(self):
        """异步建立 SSH 连接"""
        self._append_output(f"正在连接 {self._host}:{self._port} ...\n")
        worker = SSHConnectWorker(self._host, self._port, self._username, self._password)
        worker.connected.connect(self._on_connected)
        worker.error.connect(self._on_connect_error)
        self._connect_worker = worker
        worker.start()

    def _on_connected(self, client):
        """SSH 连接成功"""
        self._client = client
        self._cleanup_connect_worker()
        self._input.setEnabled(True)
        self._send_btn.setEnabled(True)
        self._input.setFocus()
        self._append_output("[连接成功] 请输入命令\n")

    def _on_connect_error(self, error):
        """SSH 连接失败"""
        self._cleanup_connect_worker()
        self._append_output(f"[连接失败] {error}\n")

    def _cleanup_connect_worker(self):
        """非阻塞清理连接 worker"""
        if self._connect_worker is not None:
            w = self._connect_worker
            self._connect_worker = None
            if hasattr(w, 'abort'):
                w.abort()
            if w.isRunning():
                w.finished.connect(w.deleteLater)
            else:
                w.deleteLater()

    def _execute_command(self):
        """执行输入的命令"""
        cmd = self._input.text().strip()
        if not cmd or self._client is None:
            return
        self._input.clear()
        self._append_output(f"$ {cmd}\n")

        # 清理上一个 exec worker
        if self._exec_worker is not None:
            if self._exec_worker.isRunning():
                return  # 上一个命令还在执行
            self._exec_worker = None

        worker = SSHExecWorker(self._client, cmd)
        worker.output.connect(self._on_output)
        worker.error.connect(self._on_error)
        worker.done.connect(self._on_exec_finished)
        self._exec_worker = worker
        worker.start()

    def _on_output(self, text):
        """命令标准输出"""
        self._append_output(text)

    def _on_error(self, text):
        """命令标准错误"""
        self._append_output(f"[错误] {text}")

    def _on_exec_finished(self):
        """命令执行完成"""
        w = self._exec_worker
        self._exec_worker = None
        if w is not None:
            if w.isRunning():
                w.finished.connect(w.deleteLater)
            else:
                w.deleteLater()
        self._append_output("---\n")
        self._input.setFocus()

    def _append_output(self, text):
        """追加文本到输出区域"""
        self._output.moveCursor(QTextCursor.End)
        self._output.insertPlainText(text)
        self._output.moveCursor(QTextCursor.End)

    def _open_in_cmd(self):
        """在系统 CMD 中打开 SSH 连接（交互式终端）"""
        if not shutil.which('ssh'):
            QMessageBox.warning(
                self, "未找到 SSH 客户端",
                "系统中未安装 OpenSSH 客户端。\n"
                "请在 Windows 设置 > 应用 > 可选功能 中安装 OpenSSH 客户端。"
            )
            return
        cmd = f'ssh -p {self._port} {self._username}@{self._host}'
        try:
            subprocess.Popen(['cmd', '/k', cmd], creationflags=subprocess.CREATE_NEW_CONSOLE)
        except Exception as e:
            conn_logger.exception('SSH', '启动 CMD 终端失败', exc=e,
                                  host=self._host, port=self._port)
            QMessageBox.warning(self, "启动失败", f"无法打开 CMD 终端：{e}")

    def _open_in_xshell(self):
        """使用 Xshell 打开 SSH 连接"""
        xshell_path = shutil.which('xshell') or shutil.which('Xshell')
        if not xshell_path:
            msg = "[提示] 未找到 Xshell，请确认已安装并加入系统 PATH"
            self._log(msg)
            QMessageBox.warning(self, "未找到 Xshell", msg)
            return
        xshell_url = f'ssh://{self._username}:{self._password}@{self._host}:{self._port}'
        try:
            # Xshell 是 GUI 程序，无需 shell=True 和 CREATE_NEW_CONSOLE
            subprocess.Popen([xshell_path, '-url', xshell_url])
        except Exception as e:
            self._log(f"[提示] 启动 Xshell 失败: {e}")
            QMessageBox.warning(self, "打开失败", f"无法启动 Xshell：{e}")

    def closeEvent(self, event):
        # 清理 exec worker
        if self._exec_worker is not None:
            w = self._exec_worker
            self._exec_worker = None
            if w.isRunning():
                w.finished.connect(w.deleteLater)
            else:
                w.deleteLater()
        # 清理 connect worker
        self._cleanup_connect_worker()
        # 关闭 SSH client（close+join 等待 transport 后台线程退出，避免 C 层崩溃）
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
        super().closeEvent(event)
