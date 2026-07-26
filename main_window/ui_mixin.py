# -*- coding: utf-8 -*-
"""MainWindow UI Mixin：状态栏、右键菜单、菜单栏、主题/字体/布局、设置对话框"""

import os
import sys
import json
import ctypes
import subprocess

from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QHBoxLayout)
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import (QColor, QShortcut, QKeySequence, QFont, QActionGroup,
    QFontDatabase)
from qfluentwidgets import (setTheme, setThemeColor, Theme, InfoBar, InfoBarPosition,
    Action, MenuAnimationType, FluentIcon, setFontFamilies,
    TransparentDropDownPushButton, setCustomStyleSheet,
    MessageBox, MessageBoxBase, ColorDialog, SpinBox, ComboBox, LineEdit,
    BodyLabel, setFont, isDarkTheme)
from qfluentwidgets.components.material import AcrylicMenu
from qfluentwidgets.components.material.acrylic_menu import (AcrylicMenuBase,
    AcrylicMenuActionListWidget)

from core.app_paths import get_app_dir, get_resource_dir


def _patch_acrylic_exec(menu):
    """PySide6/Shiboken 会将实例级 menu.exec 解析到 C++ QMenu.exec()，
    导致 AcrylicMenuBase.exec()（截屏→模糊→绘制亚克力）永不执行。
    给实例绑定 Python 级 exec 属性可绕过此劫持。"""
    def _exec(pos, ani=True, aniType=MenuAnimationType.DROP_DOWN):
        AcrylicMenuBase.exec(menu, pos, ani=ani, aniType=aniType)
    menu.exec = _exec


class _VisibleAcrylicView(AcrylicMenuActionListWidget):
    """增强亚克力菜单视图：优化模糊半径/噪点/着色层参数，使磨砂玻璃效果清晰可见。

    库默认参数（模糊半径35 + 噪点0.03 + 着色alpha 150/200）在浅色均匀背景上
    会把模糊结果洗成近乎纯色，看不出磨砂感；此处调整为：
    适度模糊保留背景轮廓 + 可见噪点纹理 + 更低着色不透明度。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # 库默认模糊半径 35 过强，背景内容糊成均匀色；15 保留更多背景细节，
        # 让磨砂层能透出背后内容的明暗变化
        self.acrylicBrush.setBlurRadius(15)
        # 库默认噪点不透明度 0.03 几乎不可见；0.15 呈现亚克力标志性颗粒感，
        # 即使弹出位置背后是纯色背景（如设备列表空白区）也能看出材质纹理
        self.acrylicBrush.noiseOpacity = 0.03

    def _updateAcrylicColor(self):
        if isDarkTheme():
            # 深色模式：着色层 90/255≈35%，让模糊内容透出更多
            self.acrylicBrush.tintColor = QColor(32, 32, 32, 90)
            self.acrylicBrush.luminosityColor = QColor(0, 0, 0, 0)
        else:
            # 浅色模式：着色层 70/255≈27%，避免库默认 59% 白色把模糊细节洗白
            self.acrylicBrush.tintColor = QColor(255, 255, 255, 70)
            self.acrylicBrush.luminosityColor = QColor(255, 255, 255, 0)


class VisibleAcrylicMenu(AcrylicMenu):
    """亚克力菜单（增强可见度）：深色/浅色主题均有明显磨砂玻璃效果"""

    def __init__(self, title="", parent=None):
        super().__init__(title, parent)
        self.setUpMenu(_VisibleAcrylicView(self))
        _patch_acrylic_exec(self)

class _ShortcutKeyEdit(LineEdit):
    """Fluent 风格快捷键录入框：点击聚焦后按下目标组合键即记录，替代原生 QKeySequenceEdit。

    Backspace/Delete 清空快捷键；单独按修饰键（Ctrl/Shift/Alt）不记录，等待实际按键。"""

    def __init__(self, sequence="", parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setClearButtonEnabled(False)
        self.setPlaceholderText("点击此处后按下组合键")
        self._sequence = QKeySequence(sequence)
        self.setText(self._sequence.toString())

    def keySequence(self):
        return self._sequence

    def keyPressEvent(self, e):
        key = e.key()
        # 单独按下修饰键/锁定键时不记录，等待实际按键
        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta,
                   Qt.Key_CapsLock, Qt.Key_NumLock, Qt.Key_ScrollLock):
            return
        # Backspace/Delete 清空快捷键
        if key in (Qt.Key_Backspace, Qt.Key_Delete):
            self._sequence = QKeySequence()
            self.setText("")
            e.accept()
            return
        if key == Qt.Key_unknown:
            return
        self._sequence = QKeySequence(e.modifiers() | Qt.Key(key))
        self.setText(self._sequence.toString())
        e.accept()


if sys.platform == 'win32':
    from win_api.windows_api import _DwmSetWindowAttribute


class UIMixin:
    """UI 相关方法：状态栏、右键菜单、菜单栏、主题、字体、布局"""

    # ==================== 状态栏 ====================

    def _set_dark_titlebar(self):
        """Windows 深色标题栏适配（DWM API）"""
        if sys.platform != 'win32':
            return
        try:
            hwnd = int(self.winId())
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            value = ctypes.c_int(1)
            _DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(value), ctypes.sizeof(value)
            )
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            preference = ctypes.c_int(2)
            _DwmSetWindowAttribute(
                hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(preference), ctypes.sizeof(preference)
            )
        except Exception:
            pass

    def _init_statusbar(self):
        """初始化底部状态栏"""
        self._statusbar_widget = QWidget()
        self._statusbar_widget.setObjectName(u"statusbar_widget")
        self._statusbar_widget.setFixedHeight(24)
        _sb_layout = QHBoxLayout(self._statusbar_widget)
        _sb_layout.setContentsMargins(8, 0, 8, 0)
        _sb_layout.setSpacing(0)
        self._status_message = QLabel("")
        _sb_layout.addWidget(self._status_message, 1)
        self.status_device = QLabel("设备: 未选择")
        self.status_state = QLabel("状态: 空闲")
        self.status_logs = QLabel("日志: 0 行")
        _sb_layout.addWidget(self.status_device)
        _sb_layout.addWidget(QLabel(" | "))
        _sb_layout.addWidget(self.status_state)
        _sb_layout.addWidget(QLabel(" | "))
        _sb_layout.addWidget(self.status_logs)
        self._show_status_message("就绪", 3000)

    def _show_status_message(self, msg, timeout=0):
        self._status_message.setText(msg)
        if timeout > 0:
            QTimer.singleShot(timeout, lambda: (
                self._status_message.setText("")
                if self._status_message.text() == msg else None))

    def _update_status_device(self, device_code):
        self.status_device.setText(f"设备: {device_code}")
        self.ui.log_status_device.setText(f"设备: {device_code}")

    def _update_status_running(self, exe_name):
        self.status_state.setText(f"状态: 运行中 - {exe_name}")

    def _update_status_idle(self):
        self.status_state.setText("状态: 空闲")

    def _update_status_paused(self, exe_name):
        self.status_state.setText(f"状态: 已暂停 - {exe_name}")

    def _update_status_logs(self, count):
        self.status_logs.setText(f"日志: {count} 行")
        self.ui.log_status_count.setText(f"日志: {count} 条")

    # ==================== 右键菜单 ====================

    def _init_context_menus(self):
        """为列表控件设置自定义右键菜单策略"""
        self.ui.id_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.ui.log_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.ui.loacl_video_list.setContextMenuPolicy(Qt.CustomContextMenu)

    def _id_list_context_menu(self, pos):
        """设备列表右键菜单（亚克力材质）"""
        menu = VisibleAcrylicMenu(parent=self)
        action_open_dir = Action(FluentIcon.FOLDER, "打开目录")
        action_cpp_log = Action(FluentIcon.DOCUMENT, "查看 CPP 日志")
        action_open_dir.triggered.connect(self.on_open_dir_clicked)
        action_cpp_log.triggered.connect(self.on_open_daily_clicked)
        menu.addAction(action_open_dir)
        menu.addAction(action_cpp_log)
        menu.exec(self.ui.id_list.mapToGlobal(pos), aniType=MenuAnimationType.DROP_DOWN)

    def _log_list_context_menu(self, pos):
        """日志内容列表右键菜单"""
        import re
        item = self.ui.log_list.currentItem()
        if item is None:
            return
        menu = VisibleAcrylicMenu(parent=self)
        action_copy = Action(FluentIcon.COPY, "复制此行")
        action_copy_frame = Action(FluentIcon.LIBRARY, "复制帧数")
        action_locate = Action(FluentIcon.PEOPLE, "在文件管理器中定位")

        def _do_copy():
            QApplication.clipboard().setText(item.text())
            self._show_status_message("已复制到剪贴板", 2000)
            self._append_log("[复制] 已复制当前行文本到剪贴板")

        def _do_copy_frame():
            frame_match = re.search(r'frame_id:(\d+)', item.text())
            if frame_match:
                frame_id = frame_match.group(1)
                QApplication.clipboard().setText(frame_id)
                self._show_status_message(f"帧数 {frame_id} 已复制到剪贴板", 2000)
                self._append_log(f"[复制] 帧数 {frame_id} 已复制到剪贴板")
            else:
                self._append_log("[提示] 当前行未找到 frame_id")

        def _do_locate():
            if self._current_log_path and os.path.exists(self._current_log_path):
                subprocess.run(['explorer', '/select,', self._current_log_path])
            else:
                self._append_log("[提示] 无法定位日志文件")

        action_copy.triggered.connect(_do_copy)
        action_copy_frame.triggered.connect(_do_copy_frame)
        action_locate.triggered.connect(_do_locate)
        menu.addAction(action_copy)
        menu.addAction(action_copy_frame)
        menu.addSeparator()
        menu.addAction(action_locate)
        menu.exec(self.ui.log_list.mapToGlobal(pos), aniType=MenuAnimationType.DROP_DOWN)

    def _loacl_video_list_context_menu(self, pos):
        """日志文件列表右键菜单"""
        item = self.ui.loacl_video_list.currentItem()
        if item is None:
            return
        menu = VisibleAcrylicMenu(parent=self)
        action_copy_name = Action(FluentIcon.COPY, "复制视频名")

        def _do_copy_name():
            pure_name = os.path.splitext(item.text())[0]
            QApplication.clipboard().setText(pure_name)
            self._show_status_message(f"文件名 {pure_name} 已复制到剪贴板", 2000)
            self._append_log(f"[复制] 文件名 {pure_name} 已复制到剪贴板")

        action_copy_name.triggered.connect(_do_copy_name)
        menu.addAction(action_copy_name)
        menu.exec(self.ui.loacl_video_list.mapToGlobal(pos), aniType=MenuAnimationType.DROP_DOWN)

    # ==================== 菜单栏 ====================

    def _init_menubar(self):
        """初始化顶部菜单栏（Fluent 风格：TransparentDropDownPushButton + AcrylicMenu）"""
        self._menubar_widget = QWidget()
        self._menubar_widget.setObjectName(u"menubar_widget")
        self._menubar_widget.setFixedHeight(26)
        _mb_layout = QHBoxLayout(self._menubar_widget)
        _mb_layout.setContentsMargins(6, 0, 6, 0)
        _mb_layout.setSpacing(2)

        # 「功能」菜单（亚克力材质）
        func_menu = VisibleAcrylicMenu("功能", self)
        act_sc = Action(FluentIcon.EDIT, "修改快捷键", self)
        act_sc.triggered.connect(lambda: QTimer.singleShot(0, self._on_modify_shortcuts))
        act_hc = Action(FluentIcon.HIGHTLIGHT, "高亮颜色设置", self)
        act_hc.triggered.connect(lambda: QTimer.singleShot(0, self._on_highlight_color))
        func_menu.addAction(act_sc)
        func_menu.addAction(act_hc)
        func_btn = TransparentDropDownPushButton("功能", self._menubar_widget)
        func_btn.setMenu(func_menu)
        _mb_layout.addWidget(func_btn)

        # 「视图」菜单（亚克力材质）
        view_menu = VisibleAcrylicMenu("视图", self)
        settings = self._load_settings()

        # 布局子菜单
        layout_menu = VisibleAcrylicMenu("布局", self)
        self._layout_group = QActionGroup(self)
        self._layout_group.setExclusive(True)
        self._act_layout_panel = Action(FluentIcon.TILES, "面板布局", self)
        self._act_layout_panel.setCheckable(True)
        self._act_layout_panel.setChecked(not settings.get("classic_layout", True))
        self._layout_group.addAction(self._act_layout_panel)
        layout_menu.addAction(self._act_layout_panel)
        self._act_layout_classic = Action(FluentIcon.LAYOUT, "经典布局", self)
        self._act_layout_classic.setCheckable(True)
        self._act_layout_classic.setChecked(settings.get("classic_layout", True))
        self._layout_group.addAction(self._act_layout_classic)
        layout_menu.addAction(self._act_layout_classic)
        self._layout_group.triggered.connect(self._on_layout_selected)
        view_menu.addMenu(layout_menu)

        # 主题子菜单
        theme_menu = VisibleAcrylicMenu("主题", self)
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        _theme_mode = self._get_theme_mode(settings)
        self._act_theme_auto = Action(FluentIcon.SYNC, "跟随系统", self)
        self._act_theme_auto.setCheckable(True)
        self._act_theme_auto.setChecked(_theme_mode == "auto")
        self._theme_group.addAction(self._act_theme_auto)
        theme_menu.addAction(self._act_theme_auto)
        self._act_theme_light = Action(FluentIcon.BRIGHTNESS, "浅色主题", self)
        self._act_theme_light.setCheckable(True)
        self._act_theme_light.setChecked(_theme_mode == "light")
        self._theme_group.addAction(self._act_theme_light)
        theme_menu.addAction(self._act_theme_light)
        self._act_theme_dark = Action(FluentIcon.QUIET_HOURS, "深色主题", self)
        self._act_theme_dark.setCheckable(True)
        self._act_theme_dark.setChecked(_theme_mode == "dark")
        self._theme_group.addAction(self._act_theme_dark)
        theme_menu.addAction(self._act_theme_dark)
        self._theme_group.triggered.connect(self._on_theme_selected)
        view_menu.addMenu(theme_menu)

        # 字号/缩放/字体
        view_menu.addSeparator()
        act_fs = Action(FluentIcon.FONT_SIZE, "字号大小", self)
        act_fs.triggered.connect(lambda: QTimer.singleShot(0, self._on_font_size))
        view_menu.addAction(act_fs)
        act_scale = Action(FluentIcon.ZOOM, "界面缩放", self)
        act_scale.triggered.connect(lambda: QTimer.singleShot(0, self._on_dpi_scale))
        view_menu.addAction(act_scale)
        act_ff = Action(FluentIcon.FONT, "字体设置", self)
        act_ff.triggered.connect(lambda: QTimer.singleShot(0, self._on_font_family))
        view_menu.addAction(act_ff)
        view_btn = TransparentDropDownPushButton("视图", self._menubar_widget)
        view_btn.setMenu(view_menu)
        _mb_layout.addWidget(view_btn)

        # 「帮助」菜单（亚克力材质）
        help_menu = VisibleAcrylicMenu("帮助", self)
        act_about = Action(FluentIcon.INFO, "关于", self)
        act_about.triggered.connect(lambda: QTimer.singleShot(0, self._on_about))
        help_menu.addAction(act_about)
        help_btn = TransparentDropDownPushButton("帮助", self._menubar_widget)
        help_btn.setMenu(help_menu)
        _mb_layout.addWidget(help_btn)

        _mb_layout.addStretch(1)

    # ==================== 设置对话框（Fluent 风格） ====================

    def _on_modify_shortcuts(self):
        """弹出快捷键修改对话框（Fluent MessageBoxBase）"""

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

        sc = self._get_shortcut_settings()
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
        ]
        dlg = ShortcutDialog(self, fields)
        dlg.yesButton.setText("确定")
        dlg.cancelButton.setText("取消")
        dlg.widget.setMinimumWidth(340)
        if dlg.exec():
            new_sc = {k: v.keySequence().toString() for k, v in dlg.editors.items()}
            self._save_settings(new_sc)
            self._init_shortcuts()
            self._append_log(f"[配置] 已更新快捷键: {new_sc}")

    def _on_highlight_color(self):
        """弹出颜色选择对话框（Fluent ColorDialog）"""
        current = self.highlight_color
        dlg = ColorDialog(current, "选择高亮颜色", self)
        if dlg.exec():
            color = dlg.color
            self.highlight_color = color
            self._save_settings({
                "highlight_color": [color.red(), color.green(), color.blue()]
            })
            self._append_log(
                f"[配置] 已更新高亮颜色: RGB({color.red()},{color.green()},{color.blue()})")
            self._show_info_bar(f"[配置] 已更新高亮颜色: RGB({color.red()},{color.green()},{color.blue()})")

    def _on_font_size(self):
        """弹出字号选择对话框（Fluent SpinBox）"""

        class FontSizeDialog(MessageBoxBase):
            def __init__(self, parent, current):
                super().__init__(parent)
                self.titleLabel = BodyLabel("字号大小", self)
                self.viewLayout.addWidget(self.titleLabel)
                self.spinBox = SpinBox(self)
                self.spinBox.setRange(10, 20)
                self.spinBox.setValue(current)
                self.spinBox.setSuffix(" pt")
                self.viewLayout.addWidget(self.spinBox)

        settings = self._load_settings()
        current = settings.get("font_size", 10)
        dlg = FontSizeDialog(self, current)
        dlg.yesButton.setText("确定")
        dlg.cancelButton.setText("取消")
        dlg.widget.setMinimumWidth(320)
        if dlg.exec():
            val = dlg.spinBox.value()
            self._save_settings({"font_size": val})
            self._apply_font_size()
            self._append_log(f"[配置] 已更新字号: {val}pt")
            self._show_info_bar(f"[配置] 已更新字号: {val}pt")

    def _on_dpi_scale(self):
        """弹出 DPI 缩放比例选择对话框（Fluent ComboBox）"""

        class ScaleDialog(MessageBoxBase):
            def __init__(self, parent, options, current_idx):
                super().__init__(parent)
                self.titleLabel = BodyLabel("界面缩放", self)
                self.viewLayout.addWidget(self.titleLabel)
                self.comboBox = ComboBox(self)
                self.comboBox.addItems([f"{o}%" for o in options])
                self.comboBox.setCurrentIndex(current_idx)
                self.viewLayout.addWidget(self.comboBox)

        settings = self._load_settings()
        current = settings.get("dpi_scale", 100)
        options = [100, 125, 150, 175, 200]
        dlg = ScaleDialog(self, options,
                          options.index(current) if current in options else 0)
        dlg.yesButton.setText("确定")
        dlg.cancelButton.setText("取消")
        dlg.widget.setMinimumWidth(320)
        if dlg.exec():
            val = int(dlg.comboBox.currentText().replace("%", ""))
            self._save_settings({"dpi_scale": val})
            w = MessageBox("界面缩放", "缩放设置已保存，重启应用后生效。", self)
            w.yesButton.setText("确定")
            w.cancelButton.hide()
            w.exec()
            self._append_log(f"[配置] 已设置缩放: {val}%（重启后生效）")
            self._show_info_bar(f"[配置] 已设置缩放: {val}%,需重启")

    def _on_font_family(self):
        """弹出字体选择对话框（Fluent ComboBox 列出系统字体）"""

        class FontFamilyDialog(MessageBoxBase):
            def __init__(self, parent, current_family):
                super().__init__(parent)
                self.titleLabel = BodyLabel("选择字体", self)
                self.viewLayout.addWidget(self.titleLabel)
                self.comboBox = ComboBox(self)
                db = QFontDatabase()
                families = sorted(set(db.families()))
                self.comboBox.addItems(families)
                if current_family and current_family in families:
                    self.comboBox.setCurrentIndex(families.index(current_family))
                self.comboBox.setMinimumWidth(260)
                self.viewLayout.addWidget(self.comboBox)

        settings = self._load_settings()
        current_family = settings.get("font_family", "")
        dlg = FontFamilyDialog(self, current_family)
        dlg.yesButton.setText("确定")
        dlg.cancelButton.setText("取消")
        dlg.widget.setMinimumWidth(380)
        if dlg.exec():
            family = dlg.comboBox.currentText()
            self._save_settings({"font_family": family})
            self._apply_font_family()
            self._show_info_bar(f"[配置] 已更新字体: {family}")
            self._append_log(f"[配置] 已更新字体: {family}")

    def _on_about(self):
        """显示关于对话框（Fluent MessageBox）"""
        w = MessageBox(
            "关于",
            "AutoWork - 自动化工作工具\n"
            "版本: 2.0.3\n\n"
            "用于视频播放、日志管理与数据记录的桌面自动化工具。",
            self
        )
        w.yesButton.setText("确定")
        w.cancelButton.hide()
        w.exec()

    # ==================== 设置应用 ====================

    def _apply_highlight_color(self):
        """从 settings.json 加载高亮颜色"""
        settings = self._load_settings()
        rgb = settings.get("highlight_color", self.DEFAULT_HIGHLIGHT_COLOR)
        self.highlight_color = QColor(rgb[0], rgb[1], rgb[2])

    def _apply_font_size(self):
        self._apply_global_font()

    def _apply_font_family(self):
        self._apply_global_font()

    def _apply_global_font(self):
        """统一应用全局字体（族 + 字号）"""
        settings = self._load_settings()
        family = settings.get("font_family", None)
        size = settings.get("font_size", None)
        if not family and not size:
            return
        if family:
            setFontFamilies([family], save=False)
        app_font = QFont()
        if family:
            app_font.setFamilies([family])
        else:
            app_font.setFamilies(QApplication.font().families())
        if size:
            pixel_size = max(12, int(int(size) * 4 / 3))
        else:
            cur_px = QApplication.font().pixelSize()
            pixel_size = cur_px if cur_px > 0 else 14
        app_font.setPixelSize(pixel_size)
        QApplication.setFont(app_font)
        for window in QApplication.topLevelWidgets():
            if not isinstance(window, QWidget):
                continue
            self._set_widget_font_recursive(window, app_font)
            window.update()

    def _set_widget_font_recursive(self, widget, app_font):
        widget.setFont(app_font)
        for child in widget.findChildren(QWidget):
            child.setFont(app_font)
            child.update()

    # ==================== 主题 ====================

    @staticmethod
    def _get_theme_mode(settings):
        """获取主题模式：'auto' / 'light' / 'dark'"""
        mode = settings.get("theme_mode", "")
        if mode in ("auto", "light", "dark"):
            return mode
        return "dark" if settings.get("dark_theme", False) else "auto"

    @staticmethod
    def _system_is_dark():
        """检测 Windows 系统当前是否为深色主题"""
        try:
            import darkdetect
            return bool(darkdetect.isDark())
        except Exception:
            return False

    @staticmethod
    def _effective_is_dark(settings):
        """解析实际生效的深色状态"""
        from main_window.main_window import MainWindow
        mode = MainWindow._get_theme_mode(settings)
        if mode == "dark":
            return True
        if mode == "light":
            return False
        return MainWindow._system_is_dark()

    def _apply_theme(self):
        """根据 settings.json 中的 theme_mode 字段应用 Fluent 主题 + 补充 QSS"""
        settings = self._load_settings()
        is_dark = self._effective_is_dark(settings)

        setTheme(Theme.DARK if is_dark else Theme.LIGHT)
        setThemeColor("#00BCD4", lazy=True)
        QApplication.styleHints().setColorScheme(
            Qt.ColorScheme.Dark if is_dark else Qt.ColorScheme.Light)

        stylesheet = self._load_qss('dark' if is_dark else 'light')
        self.setStyleSheet(stylesheet)
        if not is_dark:
            self.style().unpolish(self)
            self.style().polish(self)
            for w in self.findChildren(QWidget):
                w.update()
        self._apply_global_font()
        # setTheme 会重置控件内部 QSS，重新强制工具栏单选按钮行高
        self._enforce_toolbar_radio_height()

    def _enforce_toolbar_radio_height(self):
        """工具栏 RadioButton 固定 32px 行高（与按钮中线对齐）。
        使用 setCustomStyleSheet 注册到主题管理器，setTheme 后自动重应用，
        不会被库内部 QSS (min/max-height: 24px) 覆盖。"""
        qss = "QRadioButton { min-height: 32px; max-height: 32px; }"
        for rb in (self.ui.input_frame_before,
                   self.ui.input_frame_set,
                   self.ui.input_frame_custom):
            setCustomStyleSheet(rb, qss, qss)

    def _load_qss(self, theme_name):
        """从 styles/ 目录加载 QSS 文件，找不到时返回空字符串。
        打包环境下 styles/ 位于 sys._MEIPASS（_internal/），需用 get_resource_dir()"""
        qss_path = os.path.join(get_resource_dir(), 'styles', f'{theme_name}.qss')
        try:
            with open(qss_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception:
            return ''

    # ==================== 系统主题监听 ====================

    def _init_system_theme_monitor(self):
        """初始化系统主题变化轮询"""
        self._last_applied_dark = None
        self._theme_poll_timer = QTimer(self)
        self._theme_poll_timer.setInterval(2000)
        self._theme_poll_timer.timeout.connect(self._poll_system_theme)
        self._sync_theme_polling()

    def _sync_theme_polling(self):
        settings = self._load_settings()
        if self._get_theme_mode(settings) == "auto":
            self._last_applied_dark = self._effective_is_dark(settings)
            if not self._theme_poll_timer.isActive():
                self._theme_poll_timer.start()
        else:
            self._theme_poll_timer.stop()

    def _poll_system_theme(self):
        try:
            sys_dark = self._system_is_dark()
            if sys_dark != self._last_applied_dark:
                self._last_applied_dark = sys_dark
                self._apply_theme()
                actual = "深色" if sys_dark else "浅色"
                self._append_log(f"[主题] 检测到系统主题变化，已自动切换为{actual}")
        except KeyboardInterrupt:
            pass

    def _on_theme_selected(self, action):
        """主题子菜单互斥选择"""
        if action == self._act_theme_dark:
            mode = "dark"
        elif action == self._act_theme_light:
            mode = "light"
        else:
            mode = "auto"
        self._save_settings({"theme_mode": mode})
        self._apply_theme()
        self._sync_theme_polling()
        if mode == "dark":
            self._append_log("[主题] 已切换为深色主题")
        elif mode == "light":
            self._append_log("[主题] 已切换为浅色主题")
        else:
            actual = "深色" if self._system_is_dark() else "浅色"
            self._append_log(f"[主题] 已切换为跟随系统（当前系统为{actual}）")

    # ==================== 布局 ====================

    def _on_layout_selected(self, action):
        """布局子菜单互斥选择"""
        is_classic = (action == self._act_layout_classic)
        self._save_settings({"classic_layout": is_classic})
        self.ui.switch_layout(classic=is_classic)
        self._apply_theme()
        layout_name = "经典布局" if is_classic else "面板布局"
        self._append_log(f"[布局] 已切换为{layout_name}")

    def _apply_layout(self):
        """从 settings.json 加载并应用布局偏好"""
        settings = self._load_settings()
        classic = settings.get("classic_layout", True)
        if classic:
            self.ui.switch_layout(classic=True)

    @staticmethod
    def apply_dpi_scale(settings_path):
        """在 QApplication 创建后应用 DPI 缩放"""
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                settings = json.load(f)
            scale = settings.get("dpi_scale", 100)
            if scale != 100:
                os.environ["QT_SCALE_FACTOR"] = str(scale / 100.0)
        except Exception:
            pass
