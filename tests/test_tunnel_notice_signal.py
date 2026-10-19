# -*- coding: utf-8 -*-
"""隧道失联提示条的 UI 层接线回归（B2 第一片）。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal

from windows.remote_session import tunnel_notice as tn


class _FakeMgr(QObject):
    tunnel_notice_requested = Signal(str, str)

    def __init__(self):
        super().__init__()
        self._visitors = {"x": {"serverName": "snk1", "bindPort": 40001}}
        self.panels = [object(), object()]

    def sessions_on_port(self, port):
        assert port == 40001
        return self.panels


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_install_routes_lost_and_ok_to_panels(monkeypatch):
    _qapp()
    calls = []
    monkeypatch.setattr(tn, "show", lambda p, s: calls.append(("show", p, s)))
    monkeypatch.setattr(tn, "clear", lambda p, s: calls.append(("clear", p, s)))
    mgr = _FakeMgr()
    tn.install(mgr)
    tn.install(mgr)  # 幂等，不重复连接
    assert mgr._tunnel_notice_ui_installed is True

    mgr.tunnel_notice_requested.emit("snk1", "lost")
    assert [c[0] for c in calls] == ["show", "show"]
    assert all(c[2] == "snk1" for c in calls)

    calls.clear()
    mgr.tunnel_notice_requested.emit("snk1", "ok")
    assert [c[0] for c in calls] == ["clear", "clear"]
