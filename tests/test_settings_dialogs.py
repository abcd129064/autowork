# -*- coding: utf-8 -*-
"""main_window.ui.settings_dialogs 的离屏回归（G5/G6）。

覆盖快捷键保存、主题色选择/还原/应用、三个内联外观回调，以及 UIMixin
的薄委托。不构造 MainWindow；host 只实现被调用的属性/方法。
"""
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("qfluentwidgets")

from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import QApplication, QWidget

from core.theme_policy import DEFAULT_THEME_COLOR
from main_window.ui import settings_dialogs
from main_window.ui_mixin import UIMixin

_qapp = None


def _ensure_qapp():
    global _qapp
    _qapp = QApplication.instance() or QApplication(sys.argv[:1])
    return _qapp


@pytest.fixture(scope="module")
def qapp():
    return _ensure_qapp()


_SHORTCUTS = {
    "shortcut_flush": "F5",
    "shortcut_start": "Space",
    "shortcut_open_dir": "Ctrl+O",
    "shortcut_pause": "P",
    "shortcut_focus_frame": "Ctrl+G",
    "shortcut_start_three": "Ctrl+T",
    "shortcut_open_daily": "Ctrl+L",
    "shortcut_open_config": "Ctrl+,",
    "shortcut_p2p_panel": "F9",
    "shortcut_ledger_panel": "Ctrl+1",
    "shortcut_aftersale_panel": "Ctrl+2",
    "shortcut_table_panel": "Ctrl+3",
}


class _Host(QWidget):
    def __init__(self, theme_color=DEFAULT_THEME_COLOR, shortcuts=None):
        super().__init__()
        self._theme_color = theme_color
        self._shortcut_settings = dict(shortcuts or _SHORTCUTS)
        self.saved = []
        self.logs = []
        self.infos = []
        self.applied_theme = 0
        self.applied_font_size = 0
        self.applied_font_family = 0
        self.shortcuts_rebuilt = 0

    def _get_shortcut_settings(self):
        return dict(self._shortcut_settings)

    def _load_settings(self):
        return {"theme_color": self._theme_color}

    def _save_settings(self, data):
        self.saved.append(dict(data))

    def _init_shortcuts(self):
        self.shortcuts_rebuilt += 1

    def _append_log(self, msg):
        self.logs.append(msg)

    def _show_info_bar(self, msg, kind="info"):
        self.infos.append((msg, kind))

    def _apply_theme(self):
        self.applied_theme += 1

    def _apply_font_size(self):
        self.applied_font_size += 1

    def _apply_font_family(self):
        self.applied_font_family += 1


# ==================== G5 快捷键 ====================

def test_modify_shortcuts_saves_edited_sequences(qapp, monkeypatch):
    host = _Host()

    def fake_exec(self):
        self.editors["shortcut_flush"]._sequence = QKeySequence("Ctrl+Shift+F")
        return 1

    monkeypatch.setattr(settings_dialogs.MessageBoxBase, "exec", fake_exec)
    settings_dialogs.modify_shortcuts(host)

    assert len(host.saved) == 1
    assert len(host.saved[0]) == 12
    assert host.saved[0]["shortcut_flush"] == "Ctrl+Shift+F"
    assert host.saved[0]["shortcut_start"] == "Space"
    assert host.shortcuts_rebuilt == 1
    assert "已更新快捷键" in host.logs[-1]


# ==================== G6 主题色 ====================

def test_theme_color_passes_current_and_applies(qapp, monkeypatch):
    seen = {}

    class FakeColorDialog:
        def __init__(self, color, title, parent):
            seen["color"] = color
            seen["title"] = title
            seen["parent"] = parent
            self.color = QColor("#112233")

        def exec(self):
            return 1

    applied = []
    monkeypatch.setattr(settings_dialogs, "ColorDialog", FakeColorDialog)
    monkeypatch.setattr(settings_dialogs, "apply_theme_color_set",
                        lambda host, color: applied.append((host, color)))

    host = _Host(theme_color="#AABBCC")
    settings_dialogs.theme_color(host)

    assert seen["color"].name() == "#aabbcc"
    assert seen["title"] == "选择主题强调色"
    assert seen["parent"] is host
    assert applied == [(host, "#112233")]


def test_apply_theme_color_set_persists_and_applies(qapp, monkeypatch):
    theme_calls = []
    monkeypatch.setattr(settings_dialogs, "setThemeColor",
                        lambda color, lazy=False: theme_calls.append((color, lazy)))

    host = _Host(theme_color="#000000")
    settings_dialogs.apply_theme_color_set(host, "#123456")

    assert host._theme_color == "#123456"
    assert host.saved == [{"theme_color": "#123456"}]
    assert theme_calls == [("#123456", True)]
    assert host.applied_theme == 1
    assert "已更新主题强调色" in host.logs[-1]
    assert host.infos[-1][0] == "[配置] 已更新主题强调色: #123456"


def test_theme_color_reset_default_and_custom(qapp, monkeypatch):
    applied = []
    monkeypatch.setattr(settings_dialogs, "apply_theme_color_set",
                        lambda host, color: applied.append(color))

    host = _Host(theme_color=DEFAULT_THEME_COLOR.upper())
    settings_dialogs.theme_color_reset(host)
    assert applied == []
    assert "已是默认色" in host.logs[-1]
    assert host.infos[-1] == ("当前已是默认主题强调色", "info")

    host._theme_color = "#010203"
    settings_dialogs.theme_color_reset(host)
    assert applied == [DEFAULT_THEME_COLOR]


# ==================== 内联外观回调 ====================

def test_inline_callbacks_share_single_source(qapp):
    host = _Host()

    settings_dialogs.set_font_size_inline(host, "12")
    assert host.saved[-1] == {"font_size": 12}
    assert host.applied_font_size == 1
    assert "已更新字号: 12pt" in host.logs[-1]

    settings_dialogs.set_font_family_inline(host, "Microsoft YaHei")
    assert host.saved[-1] == {"font_family": "Microsoft YaHei"}
    assert host.applied_font_family == 1
    assert "已更新字体: Microsoft YaHei" in host.logs[-1]

    settings_dialogs.set_font_family_inline(host, "")
    assert host.saved[-1] == {"font_family": ""}
    assert "已更新字体: 默认" in host.logs[-1]

    settings_dialogs.set_dpi_scale_inline(host, "125")
    assert host.saved[-1] == {"dpi_scale": 125}
    assert "已设置缩放: 125%" in host.logs[-1]
    assert host.infos[-1] == ("[配置] 已设置缩放: 125%,需重启", "info")


# ==================== UIMixin 委托 ====================

def test_uimixin_delegates_to_settings_dialogs(monkeypatch):
    calls = []

    def record(name):
        def _fn(host, *args):
            calls.append((name, host, args))
        return _fn

    for name in ("modify_shortcuts", "theme_color", "theme_color_reset",
                 "apply_theme_color_set", "set_font_size_inline",
                 "set_font_family_inline", "set_dpi_scale_inline"):
        monkeypatch.setattr(settings_dialogs, name, record(name))

    host = object()
    UIMixin._on_modify_shortcuts(host)
    UIMixin._on_theme_color(host)
    UIMixin._on_theme_color_reset(host)
    UIMixin._apply_theme_color_set(host, "#abcdef")
    UIMixin._set_font_size_inline(host, 11)
    UIMixin._set_font_family_inline(host, "SimSun")
    UIMixin._set_dpi_scale_inline(host, 125)

    assert calls == [
        ("modify_shortcuts", host, ()),
        ("theme_color", host, ()),
        ("theme_color_reset", host, ()),
        ("apply_theme_color_set", host, ("#abcdef",)),
        ("set_font_size_inline", host, (11,)),
        ("set_font_family_inline", host, ("SimSun",)),
        ("set_dpi_scale_inline", host, (125,)),
    ]
