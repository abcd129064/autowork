# -*- coding: utf-8 -*-
"""core.theme_policy 纯逻辑单测（无 Qt / 无 qfluentwidgets）。"""
from core.theme_policy import (
    DEFAULT_THEME_COLOR,
    effective_is_dark,
    parse_theme_color,
    theme_mode,
)


# ==================== 强调色 ====================

def test_theme_color_prefers_valid_hex_string():
    assert parse_theme_color({"theme_color": "#AABBCC"}) == "#AABBCC"


def test_theme_color_falls_back_to_legacy_rgb_list():
    assert parse_theme_color(
        {"theme_color": "bad", "highlight_color": [0, 128, 255]}
    ) == "#0080ff"


def test_theme_color_clamps_rgb_channels():
    assert parse_theme_color(
        {"highlight_color": [-20, 128, 999]}
    ) == "#0080ff"


def test_theme_color_ignores_wrong_shape():
    assert parse_theme_color(
        {"theme_color": "", "highlight_color": [1, 2]}
    ) == DEFAULT_THEME_COLOR
    assert parse_theme_color({}) == DEFAULT_THEME_COLOR


# ==================== 主题模式 ====================

def test_theme_mode_accepts_known_values():
    for mode in ("auto", "light", "dark"):
        assert theme_mode({"theme_mode": mode}) == mode


def test_theme_mode_falls_back_to_legacy_dark_theme():
    assert theme_mode({"theme_mode": "nope", "dark_theme": True}) == "dark"
    assert theme_mode({"theme_mode": "nope", "dark_theme": False}) == "auto"
    assert theme_mode({}) == "auto"


# ==================== 生效深色状态 ====================

def test_effective_is_dark_honours_explicit_modes():
    assert effective_is_dark({"theme_mode": "dark"},
                             system_dark_fn=lambda: False) is True
    assert effective_is_dark({"theme_mode": "light"},
                             system_dark_fn=lambda: True) is False


def test_effective_is_dark_auto_uses_injected_system_probe():
    assert effective_is_dark({"theme_mode": "auto"},
                             system_dark_fn=lambda: True) is True
    assert effective_is_dark({"theme_mode": "auto"},
                             system_dark_fn=lambda: False) is False


def test_effective_is_dark_legacy_dark_theme_short_circuits_probe():
    calls = []
    assert effective_is_dark(
        {"dark_theme": True},
        system_dark_fn=lambda: calls.append(1) or False,
    ) is True
    assert calls == []
