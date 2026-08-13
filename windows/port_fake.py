# -*- coding: utf-8 -*-
"""工具：虚假端口占用（模拟服务在监听）

真实 bind + listen 指定端口并保持，netstat -ano / 任务管理器等外部
检测视角均为 LISTENING，模拟「本机有服务在运行」；支持多端口同时
占用与一键随机端口，关闭弹窗时自动释放（进程退出 socket 亦由系统回收）。
"""
import logging
import random
import socket

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QListWidget, QListWidgetItem,
                               QVBoxLayout, QWidget)
from qfluentwidgets import (CaptionLabel, FluentIcon, PushButton, SpinBox,
                            ToolButton)

from core.utils import show_info_bar

logger = logging.getLogger(__name__)


class PortFakeWidget(QWidget):
    """虚假端口占用工具：真实监听端口，随时占用/释放"""

    _RANDOM_MIN, _RANDOM_MAX = 20000, 60000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sockets = {}  # port -> socket，保持引用防止 GC 关闭

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(8)

        tip = CaptionLabel(
            "点击「占用」后端口将被真实监听，netstat -ano 显示 LISTENING，"
            "模拟本机有服务在监听；可同时占用多个端口，关闭弹窗时自动释放。", self)
        tip.setWordWrap(True)
        v.addWidget(tip)

        row = QHBoxLayout()
        self._spin = SpinBox(self)
        self._spin.setRange(1, 65535)
        self._spin.setValue(self._random_port())
        self._spin.setFixedWidth(140)
        btn_random = ToolButton(FluentIcon.SYNC, self)
        btn_random.setToolTip("随机可用端口")
        btn_random.clicked.connect(self._pick_random_port)
        self._btn_occupy = PushButton("占用", self)
        self._btn_occupy.clicked.connect(self._occupy)
        self._btn_release = PushButton(FluentIcon.DELETE, "全部释放", self)
        self._btn_release.setEnabled(False)
        self._btn_release.clicked.connect(self._release_all)
        row.addWidget(self._spin)
        row.addWidget(btn_random)
        row.addWidget(self._btn_occupy)
        row.addStretch(1)
        row.addWidget(self._btn_release)
        v.addLayout(row)

        self._list = QListWidget(self)
        self._list.setAlternatingRowColors(True)
        v.addWidget(self._list, 1)

    # ---------- 端口操作 ----------

    def _random_port(self) -> int:
        """探测一个当前可绑定的随机端口"""
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

    def _pick_random_port(self):
        self._spin.setValue(self._random_port())

    def _occupy(self):
        """占用输入框端口：bind + listen 保持监听"""
        port = self._spin.value()
        if port in self._sockets:
            show_info_bar(f"端口 {port} 已在占用中", "warning",
                          title="提示", parent=self, duration=2000)
            return
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", port))
            s.listen(5)
            s.setblocking(False)
        except OSError as e:
            s.close()
            logger.warning("占用端口 %s 失败: %s", port, e)
            show_info_bar(f"端口 {port} 占用失败：{e.strerror or e}", "error",
                          title="失败", parent=self, duration=3000)
            return
        self._sockets[port] = s
        self._append_item(port)
        self._btn_release.setEnabled(True)
        show_info_bar(f"端口 {port} 已占用（LISTENING）", "success",
                      title="占用成功", parent=self, duration=2500)

    def _release_all(self, notify=True):
        """释放全部占用端口"""
        for port in list(self._sockets):
            self._release_one(port)
        self._list.clear()
        self._btn_release.setEnabled(False)
        if notify:
            show_info_bar("已释放全部端口", "success",
                          title="释放成功", parent=self, duration=2000)

    def _release_one(self, port: int):
        s = self._sockets.pop(port, None)
        if s is not None:
            try:
                s.close()
            except OSError:
                pass

    def _append_item(self, port: int):
        item = QListWidgetItem(f"端口 {port}    LISTENING（模拟服务监听中）")
        item.setData(Qt.ItemDataRole.UserRole, port)
        self._list.addItem(item)

    # ---------- 生命周期 ----------

    def closeEvent(self, e):
        """页面/窗口关闭时释放全部端口，避免占用残留"""
        self._release_all(notify=False)
        super().closeEvent(e)
