# -*- coding: utf-8 -*-
"""main_window.ui.appearance 的离屏回归（不构造 MainWindow，全部 fake host）。"""
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("qfluentwidgets")

from PySide6.QtWidgets import QApplication, QLabel, QWidget

from core.design_tokens import pt_to_px
from main_window.ui import appearance


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv[:1])


class _FakeHints:
    def __init__(self):
        self.scheme = None

    def setColorScheme(self, scheme):
        self.scheme = scheme


class _FakeTimer:
    def __init__(self, *args, **kwargs):
        self.started = 0
        self.stopped = 0
        self._active = False
        self.timeout = types.SimpleNamespace(connect=lambda fn: None)
        self.interval = 0

    def setInterval(self, value):
        self.interval = value

    def isActive(self):
        return self._active

    def start(self):
        self.started += 1
        self._active = True

    def stop(self):
        self.stopped += 1
        self._active = False


def test_apply_theme_color_uses_parse_and_settings():
    host = types.SimpleNamespace(
        _load_settings=lambda: {"theme_color": "#abcdef"},
        _parse_theme_color=lambda s: s["theme_color"].upper(),
    )
    appearance.apply_theme_color(host)
    assert host._theme_color == "#ABCDEF"


def test_apply_global_font_sets_family_and_size(monkeypatch, qapp):
    seen = {}
    monkeypatch.setattr(appearance, "setFontFamilies",
                        lambda families, save=True: seen.setdefault("families", families))

    class FakeApp:
        _font = None
        _set = 0

        @classmethod
        def font(cls):
            return cls._font or QApplication.font()

        @classmethod
        def setFont(cls, font):
            cls._font = font
            cls._set += 1

        @classmethod
        def topLevelWidgets(cls):
            return []

    monkeypatch.setattr(appearance, "QApplication", FakeApp)
    host = types.SimpleNamespace(
        _load_settings=lambda: {"font_family": "Microsoft YaHei", "font_size": 12})
    appearance.apply_global_font(host)
    assert seen["families"] == ["Microsoft YaHei"]
    assert FakeApp._set == 1
    assert FakeApp._font.pixelSize() == pt_to_px(12)


def test_set_widget_font_recursive_hits_children(qapp):
    class _ExplodingUpdate(QLabel):
        def update(self):  # qfw MenuActionListWidget.update(self, index) 同型
            raise AssertionError("must call QWidget.update, not the override")

    top = _ExplodingUpdate("top")
    child = _ExplodingUpdate("x", top)
    font = top.font()
    font.setPixelSize(17)
    appearance.set_widget_font_recursive(top, font)
    assert top.font().pixelSize() == 17
    assert child.font().pixelSize() == 17


def test_apply_theme_dark_order_and_scheduled(monkeypatch, qapp):
    calls = []
    scheduled = []

    class FakeApp:
        @staticmethod
        def styleHints():
            return _FakeHints()

    class FakeTimer:
        @staticmethod
        def singleShot(delay, fn):
            scheduled.append((delay, fn))

    monkeypatch.setattr(appearance, "QApplication", FakeApp)
    monkeypatch.setattr(appearance, "QTimer", FakeTimer)
    monkeypatch.setattr(appearance, "setTheme", lambda theme: calls.append(("theme", theme)))
    monkeypatch.setattr(appearance, "setThemeColor",
                        lambda color, lazy=False: calls.append(("color", color, lazy)))

    host = types.SimpleNamespace(
        _load_settings=lambda: {"theme_mode": "dark"},
        _effective_is_dark=lambda s: True,
        _parse_theme_color=lambda s: "#123456",
        _apply_business_qss=lambda: calls.append("qss"),
        _reset_qss_compose_trees=lambda: calls.append("reset"),
        _apply_global_font=lambda: calls.append("font"),
        _enforce_toolbar_radio_height=lambda: calls.append("radio"),
        _enforce_toolbar_button_height=lambda: calls.append("button"),
        style=lambda: calls.append("style"),
        findChildren=lambda cls: calls.append("children") or [],
    )
    appearance.apply_theme(host)
    assert calls[0] == ("theme", appearance.Theme.DARK)
    assert calls[1] == ("color", "#123456", True)
    assert calls[2:] == ["qss", "reset", "font", "radio"]
    assert [d for d, _ in scheduled] == [0, 0]
    assert scheduled[0][1] == host._reset_qss_compose_trees
    assert scheduled[1][1] == host._enforce_toolbar_button_height


def test_sync_theme_polling_auto_and_manual(qapp):
    timer = _FakeTimer()
    host = types.SimpleNamespace(
        _theme_poll_timer=timer,
        _load_settings=lambda: {},
        _get_theme_mode=lambda s: "auto",
        _effective_is_dark=lambda s: True,
    )
    appearance.sync_theme_polling(host)
    assert host._last_applied_dark is True
    assert timer.started == 1

    host._get_theme_mode = lambda s: "dark"
    appearance.sync_theme_polling(host)
    assert timer.stopped == 1 and not timer.isActive()


def test_poll_system_theme_applies_on_change(qapp):
    applied = []
    logs = []
    host = types.SimpleNamespace(
        _last_applied_dark=False,
        _system_is_dark=lambda: True,
        _apply_theme=lambda: applied.append(1),
        _append_log=logs.append,
    )
    appearance.poll_system_theme(host)
    assert host._last_applied_dark is True
    assert applied == [1]
    assert logs and "已自动切换为深色" in logs[0]

    appearance.poll_system_theme(host)
    assert applied == [1]


def test_on_theme_selected_modes(qapp):
    dark = object()
    light = object()
    saved = []
    logs = []
    host = types.SimpleNamespace(
        _act_theme_dark=dark,
        _act_theme_light=light,
        _system_is_dark=lambda: False,
        _save_settings=lambda d: saved.append(d),
        _apply_theme=lambda: None,
        _sync_theme_polling=lambda: None,
        _append_log=logs.append,
    )
    appearance.on_theme_selected(host, dark)
    assert saved[-1] == {"theme_mode": "dark"}
    assert "深色主题" in logs[-1]
    appearance.on_theme_selected(host, light)
    assert saved[-1] == {"theme_mode": "light"}
    assert "浅色主题" in logs[-1]
    appearance.on_theme_selected(host, object())
    assert saved[-1] == {"theme_mode": "auto"}
    assert "跟随系统" in logs[-1]


def test_on_layout_selected_and_apply_layout(qapp):
    classic = object()
    switched = []
    saved = []
    logs = []
    host = types.SimpleNamespace(
        _act_layout_classic=classic,
        ui=types.SimpleNamespace(switch_layout=lambda classic: switched.append(classic)),
        _save_settings=lambda d: saved.append(d),
        _apply_theme=lambda: None,
        _append_log=logs.append,
    )
    appearance.on_layout_selected(host, classic)
    assert saved[-1] == {"classic_layout": True}
    assert switched[-1] is True
    assert "经典布局" in logs[-1]

    appearance.on_layout_selected(host, object())
    assert saved[-1] == {"classic_layout": False}
    assert switched[-1] is False
    assert "面板布局" in logs[-1]

    switched.clear()
    host._load_settings = lambda: {"classic_layout": True}
    appearance.apply_layout(host)
    assert switched == [True]
    host._load_settings = lambda: {"classic_layout": False}
    appearance.apply_layout(host)
    assert switched == [True]
