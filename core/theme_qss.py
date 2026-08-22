# -*- coding: utf-8 -*-
"""窗口级 QSS 应用工具

主窗口的 dark.qss/light.qss 过去只 setStyleSheet 在 MainWindow 上，
导致 SFTP/SSH/球桌管理等独立 QDialog 的原生控件（QPushButton、
QSplitter、QListWidget）掉出主题、退回 Fusion 默认灰。

本模块提供 apply_window_qss()：
1. 按当前 Fluent 主题（isDarkTheme）加载同一份 styles/{dark|light}.qss
2. setStyleSheet 到目标窗口（覆盖其全部子控件）
3. 订阅 qconfig.themeChanged，主题切换后自动重应用

用法（对话框 __init__ 里一行）：
    from core.theme_qss import apply_window_qss
    apply_window_qss(self)
"""

import os
import re

from qfluentwidgets import isDarkTheme, qconfig

from core.app_paths import get_resource_dir
from core.design_tokens import ACCENT_FALLBACK

try:
    from shiboken6 import isValid as _is_valid
except Exception:
    def _is_valid(w):  # 兜底：无法判活时假定存活，靠异常止损
        return True

# QSS 中的固定强调色锚点，加载时替换为用户当前主题色
_ACCENT_PLACEHOLDER = "#00BCD4"


def current_accent_hex() -> str:
    """读取当前 Fluent 主题强调色（失败回退令牌默认青）"""
    try:
        from PySide6.QtGui import QColor
        tc = qconfig.themeColor
        if hasattr(tc, "value"):
            tc = tc.value
        if isinstance(tc, QColor) and tc.isValid():
            return tc.name()
    except Exception:
        pass
    return ACCENT_FALLBACK


def substitute_accent(qss_text: str) -> str:
    """把 QSS 中的固定青色锚点替换为当前主题强调色"""
    accent = current_accent_hex()
    if accent.lower() == _ACCENT_PLACEHOLDER.lower():
        return qss_text
    return re.sub(re.escape(_ACCENT_PLACEHOLDER), accent, qss_text,
                  flags=re.IGNORECASE)


def load_window_qss() -> str:
    """按当前主题加载窗口 QSS 文本（强调色已替换），找不到文件时返回空串"""
    name = 'dark' if isDarkTheme() else 'light'
    path = os.path.join(get_resource_dir(), 'styles', f'{name}.qss')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
    except Exception:
        return ''
    return substitute_accent(text)


def apply_window_qss(widget) -> None:
    """给独立窗口应用全局 QSS 并跟随主题切换重应用。

    注意：只对「需要原生控件主题化」的顶层窗口调用一次；
    Fluent 子控件自带主题样式，本 QSS 按 objectName/类型选择，
    不会覆盖 Fluent 控件外观。
    """
    def _apply():
        if not _is_valid(widget):
            return  # 窗口已销毁，跳过（信号无法自动断开，靠守卫止损）
        try:
            widget.setStyleSheet(load_window_qss())
        except Exception:
            pass

    _apply()
    try:
        # themeChanged：深/浅模式切换；themeColorChanged：强调色更换
        qconfig.themeChanged.connect(_apply)
        qconfig.themeColorChanged.connect(_apply)
    except Exception:
        pass
