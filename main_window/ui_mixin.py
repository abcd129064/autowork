# -*- coding: utf-8 -*-
"""MainWindow UI Mixin：状态栏、右键菜单、菜单栏、主题/字体/布局、设置对话框"""

import os
import sys
import json
import ctypes
import subprocess

from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QHBoxLayout,
    QMenuBar, QDialog, QVBoxLayout, QKeySequenceEdit, QDialogButtonBox,
    QColorDialog, QFontDialog, QInputDialog, QMessageBox, QFrame)
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import (QColor, QShortcut, QKeySequence, QFont,
    QAction, QActionGroup)
from qfluentwidgets import (setTheme, setThemeColor, Theme, InfoBar, InfoBarPosition,
    RoundMenu, Action, MenuAnimationType, FluentIcon, setFontFamilies)

from core.app_paths import get_app_dir

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
        """设备列表右键菜单"""
        menu = RoundMenu(parent=self)
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
        menu = RoundMenu(parent=self)
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
        menu = RoundMenu(parent=self)
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
        """初始化顶部菜单栏"""
        self._menubar_widget = QMenuBar()
        self._menubar_widget.setObjectName(u"menubar_widget")
        self._menubar_widget.setFixedHeight(24)
        menubar = self._menubar_widget

        # 「功能」菜单
        func_menu = menubar.addMenu("功能")
        act_sc = func_menu.addAction("修改快捷键")
        act_sc.triggered.connect(lambda: QTimer.singleShot(0, self._on_modify_shortcuts))
        act_hc = func_menu.addAction("高亮颜色设置")
        act_hc.triggered.connect(lambda: QTimer.singleShot(0, self._on_highlight_color))

        # 「视图」菜单
        view_menu = menubar.addMenu("视图")
        settings = self._load_settings()

        # 布局子菜单
        layout_menu = view_menu.addMenu("布局")
        self._layout_group = QActionGroup(self)
        self._layout_group.setExclusive(True)
        self._act_layout_panel = QAction("面板布局", self)
        self._act_layout_panel.setCheckable(True)
        self._act_layout_panel.setChecked(not settings.get("classic_layout", True))
        self._layout_group.addAction(self._act_layout_panel)
        layout_menu.addAction(self._act_layout_panel)
        self._act_layout_classic = QAction("经典布局", self)
        self._act_layout_classic.setCheckable(True)
        self._act_layout_classic.setChecked(settings.get("classic_layout", True))
        self._layout_group.addAction(self._act_layout_classic)
        layout_menu.addAction(self._act_layout_classic)
        self._layout_group.triggered.connect(self._on_layout_selected)

        # 主题子菜单
        theme_menu = view_menu.addMenu("主题")
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        _theme_mode = self._get_theme_mode(settings)
        self._act_theme_auto = QAction("跟随系统", self)
        self._act_theme_auto.setCheckable(True)
        self._act_theme_auto.setChecked(_theme_mode == "auto")
        self._theme_group.addAction(self._act_theme_auto)
        theme_menu.addAction(self._act_theme_auto)
        self._act_theme_light = QAction("浅色主题", self)
        self._act_theme_light.setCheckable(True)
        self._act_theme_light.setChecked(_theme_mode == "light")
        self._theme_group.addAction(self._act_theme_light)
        theme_menu.addAction(self._act_theme_light)
        self._act_theme_dark = QAction("深色主题", self)
        self._act_theme_dark.setCheckable(True)
        self._act_theme_dark.setChecked(_theme_mode == "dark")
        self._theme_group.addAction(self._act_theme_dark)
        theme_menu.addAction(self._act_theme_dark)
        self._theme_group.triggered.connect(self._on_theme_selected)

        # 字号/缩放/字体
        view_menu.addSeparator()
        act_fs = view_menu.addAction("字号大小")
        act_fs.triggered.connect(lambda: QTimer.singleShot(0, self._on_font_size))
        act_scale = view_menu.addAction("界面缩放")
        act_scale.triggered.connect(lambda: QTimer.singleShot(0, self._on_dpi_scale))
        act_ff = view_menu.addAction("字体设置")
        act_ff.triggered.connect(lambda: QTimer.singleShot(0, self._on_font_family))

        # 「帮助」菜单
        help_menu = menubar.addMenu("帮助")
        act_about = help_menu.addAction("关于")
        act_about.triggered.connect(lambda: QTimer.singleShot(0, self._on_about))

    # ==================== 设置对话框 ====================

    def _on_modify_shortcuts(self):
        """弹出快捷键修改对话框"""
        dlg = QDialog(self)
        dlg.setWindowTitle("修改快捷键")
        layout = QVBoxLayout(dlg)
        sc = self._get_shortcut_settings()
        fields = [
            ("刷新", "shortcut_flush", sc["shortcut_flush"]),
            ("播放/结束", "shortcut_start", sc["shortcut_start"]),
            ("打开目录", "shortcut_open_dir", sc["shortcut_open_dir"]),
        ]
        editors = {}
        for label, key, default in fields:
            row = QHBoxLayout()
            row.addWidget(QLabel(label + ":"))
            edit = QKeySequenceEdit(QKeySequence(default))
            row.addWidget(edit)
            editors[key] = edit
            layout.addLayout(row)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)
        if dlg.exec() == QDialog.Accepted:
            new_sc = {k: v.keySequence().toString() for k, v in editors.items()}
            self._save_settings(new_sc)
            self._init_shortcuts()
            self._append_log(f"[配置] 已更新快捷键: {new_sc}")

    def _on_highlight_color(self):
        """弹出颜色选择对话框"""
        current = self.highlight_color
        color = QColorDialog.getColor(current, self, "选择高亮颜色")
        if color.isValid():
            self.highlight_color = color
            self._save_settings({
                "highlight_color": [color.red(), color.green(), color.blue()]
            })
            self._append_log(
                f"[配置] 已更新高亮颜色: RGB({color.red()},{color.green()},{color.blue()})")
            self._show_info_bar(f"[配置] 已更新高亮颜色: RGB({color.red()},{color.green()},{color.blue()})")

    def _on_font_size(self):
        """弹出字号选择对话框"""
        settings = self._load_settings()
        current = settings.get("font_size", 10)
        val, ok = QInputDialog.getInt(self, "字号大小", "请输入字号 (10~20):", current, 10, 20, 1)
        if ok:
            self._save_settings({"font_size": val})
            self._apply_font_size()
            self._append_log(f"[配置] 已更新字号: {val}pt")
            self._show_info_bar(f"[配置] 已更新字号: {val}pt")

    def _on_dpi_scale(self):
        """弹出 DPI 缩放比例选择对话框"""
        settings = self._load_settings()
        current = settings.get("dpi_scale", 100)
        options = [100, 125, 150, 175, 200]
        idx, ok = QInputDialog.getItem(
            self, "界面缩放", "选择缩放比例:",
            [f"{o}%" for o in options],
            options.index(current) if current in options else 0,
            editable=False)
        if ok:
            val = int(idx.replace("%", ""))
            self._save_settings({"dpi_scale": val})
            QMessageBox.information(self, "界面缩放", "缩放设置已保存，重启应用后生效。")
            self._append_log(f"[配置] 已设置缩放: {val}%（重启后生效）")
            self._show_info_bar(f"[配置] 已设置缩放: {val}%,需重启")

    def _on_font_family(self):
        """弹出字体选择对话框"""
        settings = self._load_settings()
        current_family = settings.get("font_family", "")
        current_font = QFont(current_family) if current_family else QFont()
        result = QFontDialog.getFont(current_font, self, "选择字体")
        if isinstance(result[0], QFont):
            font, ok = result[0], result[1]
        else:
            ok, font = result[0], result[1]
        if ok:
            self._save_settings({"font_family": font.family()})
            self._apply_font_family()
            self._show_info_bar(f"[配置] 已更新字体: {font.family()}")
            self._append_log(f"[配置] 已更新字体: {font.family()}")

    def _on_about(self):
        """显示关于对话框"""
        QMessageBox.about(
            self, "关于",
            "AutoWork - 自动化工作工具\n"
            "版本: 1.5.6\n\n"
            "用于视频播放、日志管理与数据记录的桌面自动化工具。"
        )

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

        if not is_dark:
            light_stylesheet = self._load_qss('light')
            self.setStyleSheet(light_stylesheet)
            self.style().unpolish(self)
            self.style().polish(self)
            for w in self.findChildren(QWidget):
                w.update()
            self._apply_global_font()
            return

        stylesheet = self._load_qss('dark')
        self.setStyleSheet(stylesheet)
        self._apply_global_font()

    def _load_qss(self, theme_name):
        """从 styles/ 目录加载 QSS 文件，找不到时返回空字符串"""
        qss_path = os.path.join(get_app_dir(), 'styles', f'{theme_name}.qss')
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
