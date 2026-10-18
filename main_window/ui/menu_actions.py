# -*- coding: utf-8 -*-
"""G4：跨页面复用的菜单 Action 与菜单栏空壳。

背景：菜单栏自 2026-09-06 起不再插入工作台顶部，但设置页的
「主题模式」「布局模式」下拉仍直接复用 `_act_theme_*` / `_act_layout_*`
五个 QAction 对象（main_window/hub_pages.py:341-342、413-415）。
这些属性名属于跨文件隐式契约，不能改名。

分层约定：不可见的菜单 QWidget/QMenu/按钮不再搬入本模块，只保留
五个 QAction 与 `_menubar_widget` 空壳；smoke_fluent_mainwindow.py:75
仍断言该空壳的 parentWidget() 不为 homeInterface。
"""
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QWidget

from qfluentwidgets import Action, FluentIcon


def init_menubar(host):
    """初始化 `_menubar_widget` 空壳与五个跨页面复用 Action。

    调用方：UIMixin._init_menubar（MainWindow 构建期调用一次）。
    host 需提供 `_load_settings()`、`_get_theme_mode(settings)`、
    `_on_theme_selected(action)`、`_on_layout_selected(action)`。
    """
    # 空壳保留原 objectName / 高度：QSS 的 QWidget#menubar_widget 仍可命中，
    # 后续若恢复顶部菜单栏也有稳定挂载点。
    host._menubar_widget = QWidget()
    host._menubar_widget.setObjectName("menubar_widget")
    host._menubar_widget.setFixedHeight(26)

    settings = host._load_settings()

    # 布局 Action：与设置页「布局模式」下拉共用
    host._layout_group = QActionGroup(host)
    host._layout_group.setExclusive(True)
    host._act_layout_panel = Action(FluentIcon.TILES, "面板布局", host)
    host._act_layout_panel.setCheckable(True)
    host._act_layout_panel.setChecked(not settings.get("classic_layout", True))
    host._layout_group.addAction(host._act_layout_panel)
    host._act_layout_classic = Action(FluentIcon.LAYOUT, "经典布局", host)
    host._act_layout_classic.setCheckable(True)
    host._act_layout_classic.setChecked(settings.get("classic_layout", True))
    host._layout_group.addAction(host._act_layout_classic)
    host._layout_group.triggered.connect(host._on_layout_selected)

    # 主题 Action：与设置页「主题模式」下拉共用
    host._theme_group = QActionGroup(host)
    host._theme_group.setExclusive(True)
    theme_mode = host._get_theme_mode(settings)
    host._act_theme_auto = Action(FluentIcon.SYNC, "跟随系统", host)
    host._act_theme_auto.setCheckable(True)
    host._act_theme_auto.setChecked(theme_mode == "auto")
    host._theme_group.addAction(host._act_theme_auto)
    host._act_theme_light = Action(FluentIcon.BRIGHTNESS, "浅色主题", host)
    host._act_theme_light.setCheckable(True)
    host._act_theme_light.setChecked(theme_mode == "light")
    host._theme_group.addAction(host._act_theme_light)
    host._act_theme_dark = Action(FluentIcon.QUIET_HOURS, "深色主题", host)
    host._act_theme_dark.setCheckable(True)
    host._act_theme_dark.setChecked(theme_mode == "dark")
    host._theme_group.addAction(host._act_theme_dark)
    host._theme_group.triggered.connect(host._on_theme_selected)

    return host._menubar_widget
