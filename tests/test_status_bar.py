# -*- coding: utf-8 -*-
"""main_window.ui.status_bar 的离屏回归：控件结构、文案更新与临时消息自清。"""
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from main_window.ui import status_bar


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv[:1])


def _host():
    host = types.SimpleNamespace()
    host.ui = types.SimpleNamespace(log_status_device=QLabel(),
                                   log_status_count=QLabel())
    status_bar.init_statusbar(host)
    return host


def test_init_statusbar_structure(qapp):
    host = _host()
    assert host._statusbar_widget.objectName() == "statusbar_widget"
    assert host._statusbar_widget.height() == 24
    assert host.status_device.text() == "设备: 未选择"
    assert host.status_state.text() == "状态: 空闲"
    assert host.status_logs.text() == "日志: 0 行"
    assert host._status_message.text() == "就绪"


def test_update_device_syncs_two_labels(qapp):
    host = _host()
    status_bar.update_status_device(host, "C5")
    assert host.status_device.text() == "设备: C5"
    assert host.ui.log_status_device.text() == "设备: C5"


def test_running_idle_paused(qapp):
    host = _host()
    status_bar.update_status_running(host, "demo.exe")
    assert host.status_state.text() == "状态: 运行中 - demo.exe"
    status_bar.update_status_idle(host)
    assert host.status_state.text() == "状态: 空闲"
    status_bar.update_status_paused(host, "demo.exe")
    assert host.status_state.text() == "状态: 已暂停 - demo.exe"


def test_update_logs_syncs_two_labels(qapp):
    host = _host()
    status_bar.update_status_logs(host, 12)
    assert host.status_logs.text() == "日志: 12 行"
    assert host.ui.log_status_count.text() == "日志: 12 条"


def test_show_status_message_clears_after_timeout(qapp):
    host = _host()
    status_bar.show_status_message(host, "临时提示", 10)
    assert host._status_message.text() == "临时提示"
    QTest.qWait(80)
    assert host._status_message.text() == ""
