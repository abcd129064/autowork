# -*- coding: utf-8 -*-
"""主题/外观配置的纯逻辑解析（无 Qt / 无 qfluentwidgets 依赖）。

从 main_window/ui_mixin.py 抽出的静态判定，供主窗口、启动入口与
单测共用；UI 层只负责把结果应用到 qfluentwidgets，不再内联配置解析。
"""
from __future__ import annotations

DEFAULT_THEME_COLOR = "#00BCD4"

VALID_THEME_MODES = ("auto", "light", "dark")


def parse_theme_color(settings, default=DEFAULT_THEME_COLOR):
    """解析主题强调色：优先 theme_color（HEX），兼容旧 highlight_color（RGB 列表）。

    与旧 UIMixin._parse_theme_color 行为一致：
    - theme_color 为井号开头的字符串时原样返回（不校验 HEX 合法性）；
    - 否则尝试 highlight_color 三元组，逐通道钳制到 0..255 后转小写 HEX；
    - 都不满足时返回 default。
    """
    color = settings.get("theme_color")
    if isinstance(color, str) and color.startswith("#"):
        return color
    rgb = settings.get("highlight_color")
    if isinstance(rgb, (list, tuple)) and len(rgb) == 3:
        return "#{:02x}{:02x}{:02x}".format(
            *(max(0, min(255, int(c))) for c in rgb))
    return default


def theme_mode(settings):
    """获取主题模式：'auto' / 'light' / 'dark'。

    兼容旧字段 dark_theme：新字段非法或缺失时，dark_theme=True → 'dark'，
    否则视为 'auto'。
    """
    mode = settings.get("theme_mode", "")
    if mode in VALID_THEME_MODES:
        return mode
    return "dark" if settings.get("dark_theme", False) else "auto"


def system_is_dark():
    """检测 Windows 系统当前是否为深色主题；darkdetect 缺失/异常时返回 False。"""
    try:
        import darkdetect
        return bool(darkdetect.isDark())
    except Exception:
        return False


def effective_is_dark(settings, system_dark_fn=None):
    """解析实际生效的深色状态。

    system_dark_fn 可注入（默认 system_is_dark），便于单测不依赖真实系统主题。
    """
    mode = theme_mode(settings)
    if mode == "dark":
        return True
    if mode == "light":
        return False
    fn = system_dark_fn or system_is_dark
    return bool(fn())


__all__ = [
    "DEFAULT_THEME_COLOR",
    "VALID_THEME_MODES",
    "effective_is_dark",
    "parse_theme_color",
    "system_is_dark",
    "theme_mode",
]
