# -*- coding: utf-8 -*-
"""main_window.ui.menu_actions 的离屏回归：空壳、五个 Action 与互斥回调。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QWidget

from main_window.ui import menu_actions


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv[:1])


class _Host(QWidget):
    def __init__(self, settings):
        super().__init__()
        self._settings = dict(settings)
        self.calls = []

    def _load_settings(self):
        return dict(self._settings)

    def _get_theme_mode(self, settings):
        return settings.get("theme_mode", "auto")

    def _on_theme_selected(self, action):
        self.calls.append(("theme", action))

    def _on_layout_selected(self, action):
        self.calls.append(("layout", action))


def _host(settings=None):
    host = _Host(settings or {"classic_layout": True, "theme_mode": "auto"})
    menu_actions.init_menubar(host)
    return host


def test_menubar_shell_is_empty_and_unparented(qapp):
    host = _host()
    shell = host._menubar_widget
    assert shell.objectName() == "menubar_widget"
    assert shell.height() == 26
    assert shell.parentWidget() is None
    assert shell.layout() is None
    assert shell.findChildren(QWidget) == []


def test_five_actions_exist_and_are_checkable(qapp):
    host = _host()
    for name in ("_act_layout_panel", "_act_layout_classic",
                 "_act_theme_auto", "_act_theme_light", "_act_theme_dark"):
        action = getattr(host, name)
        assert isinstance(action, QAction), name
        assert action.isCheckable(), name


def test_initial_checked_state_follows_settings(qapp):
    host = _host({"classic_layout": False, "theme_mode": "dark"})
    assert host._act_layout_panel.isChecked()
    assert not host._act_layout_classic.isChecked()
    assert host._act_theme_dark.isChecked()
    assert not host._act_theme_auto.isChecked()
    assert not host._act_theme_light.isChecked()
    assert host._act_layout_panel.actionGroup() is host._layout_group
    assert host._act_theme_dark.actionGroup() is host._theme_group
    assert host._layout_group.isExclusive()
    assert host._theme_group.isExclusive()


def test_action_trigger_reaches_host_callbacks(qapp):
    host = _host({"classic_layout": True, "theme_mode": "auto"})
    host._act_theme_light.trigger()
    assert host.calls[-1] == ("theme", host._act_theme_light)
    host._act_layout_panel.trigger()
    assert host.calls[-1] == ("layout", host._act_layout_panel)
