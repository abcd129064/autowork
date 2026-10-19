# -*- coding: utf-8 -*-
"""设置对话框与内联外观回调（G5/G6）。

从 main_window.ui_mixin 下沉：快捷键修改对话框、主题强调色选择/还原，
以及设置页外观行的三个内联回调（字号/字体/缩放）。

所有函数只通过 host 的属性/方法工作，不 import 主窗口；UIMixin 保留同名
薄委托，外部回调与旧属性路径不变。G7/G12 的旧弹窗仍是死代码，未迁入本模块。
"""

from PySide6.QtWidgets import QHBoxLayout
from qfluentwidgets import MessageBoxBase, BodyLabel, ColorDialog, setThemeColor

from core.theme_policy import DEFAULT_THEME_COLOR

from .shortcut_edit import _ShortcutKeyEdit


def modify_shortcuts(host):
    """弹出快捷键修改对话框（Fluent MessageBoxBase）。"""

    class ShortcutDialog(MessageBoxBase):
        def __init__(self, parent, fields):
            super().__init__(parent)
            self.titleLabel = BodyLabel("修改快捷键", self)
            self.viewLayout.addWidget(self.titleLabel)
            self.editors = {}
            for label, key, default in fields:
                row = QHBoxLayout()
                lbl = BodyLabel(label + ":", self)
                lbl.setFixedWidth(80)
                row.addWidget(lbl)
                edit = _ShortcutKeyEdit(default, self)
                edit.setMinimumWidth(180)
                row.addWidget(edit)
                self.editors[key] = edit
                self.viewLayout.addLayout(row)

    sc = host._get_shortcut_settings()
    fields = [
        ("刷新", "shortcut_flush", sc["shortcut_flush"]),
        ("播放/结束", "shortcut_start", sc["shortcut_start"]),
        ("打开目录", "shortcut_open_dir", sc["shortcut_open_dir"]),
        ("暂停/恢复", "shortcut_pause", sc["shortcut_pause"]),
        ("聚焦帧输入", "shortcut_focus_frame", sc["shortcut_focus_frame"]),
        ("启动三端", "shortcut_start_three", sc["shortcut_start_three"]),
        ("查看CPP日志", "shortcut_open_daily", sc["shortcut_open_daily"]),
        ("打开配置", "shortcut_open_config", sc["shortcut_open_config"]),
        ("P2P面板", "shortcut_p2p_panel", sc["shortcut_p2p_panel"]),
        # 需求21：新面板快捷入口
        ("跑视频面板", "shortcut_ledger_panel", sc["shortcut_ledger_panel"]),
        ("售后面板", "shortcut_aftersale_panel", sc["shortcut_aftersale_panel"]),
        ("运维面板", "shortcut_table_panel", sc["shortcut_table_panel"]),
    ]
    dlg = ShortcutDialog(host, fields)
    dlg.yesButton.setText("确定")
    dlg.cancelButton.setText("取消")
    dlg.widget.setMinimumWidth(340)
    if dlg.exec():
        new_sc = {k: v.keySequence().toString() for k, v in dlg.editors.items()}
        host._save_settings(new_sc)
        host._init_shortcuts()
        host._append_log(f"[配置] 已更新快捷键: {new_sc}")


def theme_color(host):
    """弹出主题强调色选择对话框（Fluent ColorDialog）：选择后全局即时生效并持久化。"""
    from PySide6.QtGui import QColor
    current = QColor(host._theme_color)
    dlg = ColorDialog(current, "选择主题强调色", host)
    if dlg.exec():
        apply_theme_color_set(host, dlg.color.name())


def theme_color_reset(host):
    """还原默认主题强调色：立即应用并持久化。"""
    default = DEFAULT_THEME_COLOR
    if host._theme_color.lower() == default.lower():
        host._append_log("[配置] 主题强调色已是默认色，无需还原")
        host._show_info_bar("当前已是默认主题强调色", "info")
        return
    apply_theme_color_set(host, default)


def apply_theme_color_set(host, color):
    """应用主题强调色：持久化 + 全局即时生效 + 日志/通知（选色与还原默认共用）。"""
    host._theme_color = color
    host._save_settings({"theme_color": color})
    setThemeColor(color, lazy=True)
    # QSS 中的强调色锚点需按新色重新替换应用
    host._apply_theme()
    host._append_log(f"[配置] 已更新主题强调色: {color}")
    host._show_info_bar(f"[配置] 已更新主题强调色: {color}")


def set_font_size_inline(host, val):
    """内联字号回调（设置页 SpinBox 直接改动；字号弹窗确定也走此单源逻辑）。"""
    val = int(val)
    host._save_settings({"font_size": val})
    host._apply_font_size()
    host._append_log(f"[配置] 已更新字号: {val}pt")
    host._show_info_bar(f"[配置] 已更新字号: {val}pt")


def set_font_family_inline(host, family):
    """内联字体回调（family 传空串 = 恢复默认字体族；字体弹窗确定同源）。"""
    host._save_settings({"font_family": family})
    host._apply_font_family()
    label = family if family else "默认"
    host._append_log(f"[配置] 已更新字体: {label}")
    host._show_info_bar(f"[配置] 已更新字体: {label}")


def set_dpi_scale_inline(host, val):
    """内联缩放回调：持久化 + info bar 重启提示（2026-09-07 由弹窗改为内联）。"""
    val = int(val)
    host._save_settings({"dpi_scale": val})
    host._append_log(f"[配置] 已设置缩放: {val}%（重启后生效）")
    host._show_info_bar(f"[配置] 已设置缩放: {val}%,需重启")
