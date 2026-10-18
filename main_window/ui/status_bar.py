# -*- coding: utf-8 -*-
"""底部状态栏原语（无 UIMixin 依赖）。

从 main_window.ui_mixin 下沉；host 需提供 .ui（含 log_status_device /
log_status_count）以及由 init_statusbar 创建的 5 个属性。外部仍按原属性名
访问 _statusbar_widget / _status_message / status_device / status_state /
status_logs，main_window.py 的 addWidget 与 smoke 断言不变。
"""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget


def init_statusbar(host):
    """初始化底部状态栏，并显示 3 秒“就绪”。"""
    host._statusbar_widget = QWidget()
    host._statusbar_widget.setObjectName(u"statusbar_widget")
    host._statusbar_widget.setFixedHeight(24)
    layout = QHBoxLayout(host._statusbar_widget)
    layout.setContentsMargins(8, 0, 8, 0)
    layout.setSpacing(0)
    host._status_message = QLabel("")
    layout.addWidget(host._status_message, 1)
    host.status_device = QLabel("设备: 未选择")
    host.status_state = QLabel("状态: 空闲")
    host.status_logs = QLabel("日志: 0 行")
    layout.addWidget(host.status_device)
    layout.addWidget(QLabel(" | "))
    layout.addWidget(host.status_state)
    layout.addWidget(QLabel(" | "))
    layout.addWidget(host.status_logs)
    show_status_message(host, "就绪", 3000)


def show_status_message(host, msg, timeout=0):
    """状态栏临时消息：到时自清（仅当未被新消息覆盖）。"""
    host._status_message.setText(msg)
    if timeout > 0:
        QTimer.singleShot(timeout, lambda: (
            host._status_message.setText("")
            if host._status_message.text() == msg else None))


def update_status_device(host, device_code):
    """同步状态栏与日志面板头的当前设备。"""
    host.status_device.setText(f"设备: {device_code}")
    host.ui.log_status_device.setText(f"设备: {device_code}")


def update_status_running(host, exe_name):
    """状态栏置运行中（带程序名）。"""
    host.status_state.setText(f"状态: 运行中 - {exe_name}")


def update_status_idle(host):
    """状态栏置空闲。"""
    host.status_state.setText("状态: 空闲")


def update_status_paused(host, exe_name):
    """状态栏置已暂停。"""
    host.status_state.setText(f"状态: 已暂停 - {exe_name}")


def update_status_logs(host, count):
    """同步日志行数到状态栏与日志面板头。"""
    host.status_logs.setText(f"日志: {count} 行")
    host.ui.log_status_count.setText(f"日志: {count} 条")
