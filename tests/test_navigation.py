# -*- coding: utf-8 -*-
"""main_window.ui.navigation 的离线回归（G8/G11 活入口）。

覆盖：面板入口弹出策略、球桌/售后面板导航、连接诊断单例、工具页跳转，
以及 UIMixin 的五个薄委托。全部 fake host，不构造 MainWindow。
"""
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("qfluentwidgets")

from main_window.ui import navigation
from main_window.ui_mixin import UIMixin


class _Host:
    def __init__(self, settings=None):
        self._settings = dict(settings or {})
        self.calls = []

    def _load_settings(self):
        return dict(self._settings)

    def open_hub_popout(self, hub):
        self.calls.append(("popout", hub))

    def switchTo(self, widget):
        self.calls.append(("switch", widget))


def test_panel_entry_popout_default_true_and_exception():
    host = _Host()
    assert navigation.panel_entry_popout_enabled(host) is True

    host._settings = {"panel_entry_popout": False}
    assert navigation.panel_entry_popout_enabled(host) is False

    def boom():
        raise RuntimeError("settings unavailable")

    host._load_settings = boom
    assert navigation.panel_entry_popout_enabled(host) is True


def test_open_table_panel_popout_and_inline():
    hub = object()
    host = _Host()
    host.management_hub = hub
    host._panel_entry_popout_enabled = lambda: True
    navigation.open_table_panel(host)
    assert host.calls == [("popout", hub)]

    host.calls.clear()
    host._panel_entry_popout_enabled = lambda: False
    navigation.open_table_panel(host)
    assert host.calls == [("switch", hub)]

    host.calls.clear()
    del host.management_hub
    navigation.open_table_panel(host)
    assert host.calls == []


def test_open_aftersale_popout_and_inline():
    hub = object()
    host = _Host()
    host.aftersale_hub = hub
    host._panel_entry_popout_enabled = lambda: True
    navigation.open_aftersale(host)
    assert host.calls == [("popout", hub)]

    host.calls.clear()
    host._panel_entry_popout_enabled = lambda: False
    navigation.open_aftersale(host)
    assert host.calls == [("switch", hub)]


def test_open_conn_diag_singleton(monkeypatch):
    class _Signal:
        def __init__(self):
            self._slots = []

        def connect(self, slot):
            self._slots.append(slot)

        def emit(self):
            for slot in list(self._slots):
                slot()

    class FakePanel:
        instances = 0

        def __init__(self):
            FakePanel.instances += 1
            self.destroyed = _Signal()
            self.shown = 0
            self.raised = 0
            self.activated = 0

        def show(self):
            self.shown += 1

        def raise_(self):
            self.raised += 1

        def activateWindow(self):
            self.activated += 1

    fake_parent = types.ModuleType("windows.remote_session")
    fake_parent.__path__ = []
    fake_mod = types.ModuleType("windows.remote_session.conn_diag_panel")
    fake_mod.ConnDiagPanel = FakePanel
    monkeypatch.setitem(sys.modules, "windows.remote_session", fake_parent)
    monkeypatch.setitem(sys.modules, "windows.remote_session.conn_diag_panel", fake_mod)

    host = _Host()
    navigation.open_conn_diag(host)
    assert FakePanel.instances == 1
    assert host._conn_diag.shown == 1
    assert host._conn_diag.raised == 1
    assert host._conn_diag.activated == 1

    first = host._conn_diag
    navigation.open_conn_diag(host)
    assert FakePanel.instances == 1
    assert first.shown == 2
    assert first.raised == 2

    first.destroyed.emit()
    assert host._conn_diag is None
    navigation.open_conn_diag(host)
    assert FakePanel.instances == 2


def test_open_tool_hub_switches_and_work_page():
    page = object()
    hub = types.SimpleNamespace(single_video_work=page, switchTo=lambda w: host.calls.append(("switch", w)))
    host = _Host()
    host.tool_hub = hub
    navigation.open_tool_hub(host)
    assert host.calls == [("switch", hub)]

    host.calls.clear()
    navigation.open_tool_hub(host, "single_video_work")
    assert host.calls == [("switch", hub), ("switch", page)]

    host.calls.clear()
    host.tool_hub = None
    navigation.open_tool_hub(host, "single_video_work")
    assert host.calls == []


def test_uimixin_delegates_to_navigation(monkeypatch):
    calls = []

    def record(name):
        def _fn(host, *args):
            calls.append((name, host, args))
        return _fn

    for name in ("panel_entry_popout_enabled", "open_table_panel",
                 "open_aftersale", "open_conn_diag", "open_tool_hub"):
        monkeypatch.setattr(navigation, name, record(name))

    host = object()
    UIMixin._panel_entry_popout_enabled(host)
    UIMixin._on_open_table_panel(host)
    UIMixin._on_open_aftersale(host)
    UIMixin._on_open_conn_diag(host)
    UIMixin._on_open_tool_hub(host, "newlog_work")

    assert calls == [
        ("panel_entry_popout_enabled", host, ()),
        ("open_table_panel", host, ()),
        ("open_aftersale", host, ()),
        ("open_conn_diag", host, ()),
        ("open_tool_hub", host, ("newlog_work",)),
    ]
