# -*- coding: utf-8 -*-
"""MainWindow UI Mixin：状态栏、右键菜单、菜单栏、主题/字体/布局、设置对话框

独立的关于/整理对话框、快捷键录入框与亚克力菜单已迁至 main_window/ui/；
本模块继续 re-export 这些名字，外部导入路径保持不变。
"""

import os
import sys
import json
import ctypes

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QFontDatabase
from qfluentwidgets import (setThemeColor,
    MessageBox, MessageBoxBase, ColorDialog, SpinBox, ComboBox, LineEdit,
    BodyLabel, CaptionLabel, TitleLabel, SwitchButton,
    PushButton, ScrollArea, TextEdit, ProgressBar)

from core import theme_policy, ui_scale

from core.perf import is_acrylic_enabled, is_animation_enabled
from workers.collect_worker import ZipUploadWorker
from workers.newlog_worker import NewLogWorker
from workers.single_video_worker import SingleVideoWorker
from core.log_rules import DEFAULT_LOG_RULES, compile_log_rules

# 模块级 UI 资产已迁出；此处 re-export 保持 main_window.ui_mixin.X 旧路径可用
from .ui import appearance, context_menus, menu_actions, qss_runtime, status_bar
from .ui.about_dialog import (APP_VERSION, ABOUT_LINKS, ABOUT_OSS_LIBS,
    GITHUB_REPO, AboutDialog)
from .ui.acrylic_menu import (_ensure_menu_fit, _patch_acrylic_exec,
    _VisibleAcrylicView, VisibleAcrylicMenu)
from .ui.shortcut_edit import _ShortcutKeyEdit
from .ui.newlog_dialog import NewLogDialog


if sys.platform == 'win32':
    from win_api.windows_api import _DwmSetWindowAttribute


class UIMixin:
    """UI 相关方法：状态栏、右键菜单、菜单栏、主题、字体、布局"""
    ui: 'Ui_MainWindow'
    videos_dir: str
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
        """初始化底部状态栏（实现见 main_window.ui.status_bar）。"""
        return status_bar.init_statusbar(self)

    def _show_status_message(self, msg, timeout=0):
        """状态栏临时消息（实现见 status_bar）。"""
        return status_bar.show_status_message(self, msg, timeout)

    def _update_status_device(self, device_code):
        """同步当前设备（实现见 status_bar）。"""
        return status_bar.update_status_device(self, device_code)

    def _update_status_running(self, exe_name):
        """状态栏置运行中（实现见 status_bar）。"""
        return status_bar.update_status_running(self, exe_name)

    def _update_status_idle(self):
        """状态栏置空闲（实现见 status_bar）。"""
        return status_bar.update_status_idle(self)

    def _update_status_paused(self, exe_name):
        """状态栏置已暂停（实现见 status_bar）。"""
        return status_bar.update_status_paused(self, exe_name)

    def _update_status_logs(self, count):
        """同步日志行数（实现见 status_bar）。"""
        return status_bar.update_status_logs(self, count)

    # ==================== 右键菜单 ====================

    def _init_context_menus(self):
        """为列表控件设置右键菜单策略并预构建缓存菜单（实现见 ui.context_menus）。"""
        return context_menus.init_context_menus(self)

    def _build_id_list_menu(self):
        """预构建设备列表右键菜单（实现见 ui.context_menus）。"""
        return context_menus.build_id_list_menu(self)

    def _build_log_list_menu(self):
        """预构建日志列表右键菜单（实现见 ui.context_menus）。"""
        return context_menus.build_log_list_menu(self)

    def _build_video_list_menu(self):
        """预构建本地视频列表右键菜单（实现见 ui.context_menus）。"""
        return context_menus.build_video_list_menu(self)

    def _id_list_context_menu(self, pos):
        """弹出设备列表右键菜单（实现见 ui.context_menus）。"""
        return context_menus.id_list_context_menu(self, pos)

    def _log_list_context_menu(self, pos):
        """弹出日志列表右键菜单（实现见 ui.context_menus）。"""
        return context_menus.log_list_context_menu(self, pos)

    def _local_video_list_context_menu(self, pos):
        """弹出本地视频列表右键菜单（实现见 ui.context_menus）。"""
        return context_menus.local_video_list_context_menu(self, pos)

    def _add_file_to_upload(self, file_path):
        """把日志/视频收集到上传目录（实现见 ui.context_menus）。"""
        return context_menus.add_file_to_upload(self, file_path)

    def _on_upload_collect_done(self, device_id, copied, missing, worker):
        """上传目录收集完成回调（实现见 ui.context_menus）。"""
        return context_menus.on_upload_collect_done(
            self, device_id, copied, missing, worker)

    def _on_show_upload_list(self):
        """功能菜单「上传清单」：复用运维面板 UploadListDialog 查看 upload 工作区文件"""
        videos_dir = getattr(self, "videos_dir", "") or ""
        if not videos_dir:
            self._show_info_bar("videos_dir 未配置，请先在设置中配置视频/日志目录",
                                "warning", duration=3000)
            return
        root = os.path.join(videos_dir, "upload")
        if not os.path.isdir(root) or not os.listdir(root):
            self._show_info_bar("暂无待上传文件，请先右键日志文件→添加到上传目录",
                                "info", duration=3000)
            return
        # 延迟导入：management_panel 依赖重（qfluentwidgets 整包），避免启动开销
        # 弹窗内置「打包上传」按钮，清单确认与上传在同一窗口完成
        from windows.management_panel import UploadListDialog
        UploadListDialog(root, self).exec()

    # ==================== 菜单栏 ====================

    def _init_menubar(self):
        """初始化顶部菜单栏 Action 与空壳（实现见 ui.menu_actions）。"""
        return menu_actions.init_menubar(self)

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
            # 需求21：新面板快捷入口
            ("跑视频面板", "shortcut_ledger_panel", sc["shortcut_ledger_panel"]),
            ("售后面板", "shortcut_aftersale_panel", sc["shortcut_aftersale_panel"]),
            ("运维面板", "shortcut_table_panel", sc["shortcut_table_panel"]),
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

    def _on_theme_color(self):
        """弹出主题强调色选择对话框（Fluent ColorDialog）：选择后全局即时生效并持久化"""
        current = QColor(self._theme_color)
        dlg = ColorDialog(current, "选择主题强调色", self)
        if dlg.exec():
            self._apply_theme_color_set(dlg.color.name())

    def _on_theme_color_reset(self):
        """还原默认主题强调色：立即应用并持久化"""
        from main_window.settings_mixin import SettingsMixin
        default = SettingsMixin.DEFAULT_THEME_COLOR
        if self._theme_color.lower() == default.lower():
            self._append_log("[配置] 主题强调色已是默认色，无需还原")
            self._show_info_bar("当前已是默认主题强调色", "info")
            return
        self._apply_theme_color_set(default)

    def _apply_theme_color_set(self, color):
        """应用主题强调色：持久化 + 全局即时生效 + 日志/通知（选色与还原默认共用）"""
        self._theme_color = color
        self._save_settings({"theme_color": color})
        setThemeColor(color, lazy=True)
        # QSS 中的强调色锚点需按新色重新替换应用
        self._apply_theme()
        self._append_log(f"[配置] 已更新主题强调色: {color}")
        self._show_info_bar(f"[配置] 已更新主题强调色: {color}")

    def _set_font_size_inline(self, val):
        """内联字号回调（设置页 SpinBox 直接改动；字号弹窗确定也走此单源逻辑）"""
        val = int(val)
        self._save_settings({"font_size": val})
        self._apply_font_size()
        self._append_log(f"[配置] 已更新字号: {val}pt")
        self._show_info_bar(f"[配置] 已更新字号: {val}pt")

    def _set_font_family_inline(self, family):
        """内联字体回调（family 传空串 = 恢复默认字体族；字体弹窗确定同源）"""
        self._save_settings({"font_family": family})
        self._apply_font_family()
        label = family if family else "默认"
        self._append_log(f"[配置] 已更新字体: {label}")
        self._show_info_bar(f"[配置] 已更新字体: {label}")

    def _set_dpi_scale_inline(self, val):
        """内联缩放回调：持久化 + info bar 重启提示（2026-09-07 由弹窗改为内联）"""
        val = int(val)
        self._save_settings({"dpi_scale": val})
        self._append_log(f"[配置] 已设置缩放: {val}%（重启后生效）")
        self._show_info_bar(f"[配置] 已设置缩放: {val}%,需重启")

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
            self._set_font_size_inline(dlg.spinBox.value())

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
            self._set_dpi_scale_inline(val)

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
            self._set_font_family_inline(dlg.comboBox.currentText())

    def _on_add_table_record(self):
        """手动添加球桌记录（API 失效时的兜底录入入口）"""
        from windows.management.dialogs import AddRecordDialog
        from database import table_db
        dlg = AddRecordDialog(self)
        if not dlg.exec():
            return
        record = dlg.get_record()
        table_db.insert_one(record)
        # 球桌面板已打开时同步刷新展示
        panel = getattr(self, '_table_panel', None)
        if panel is not None:
            panel._page_no = 1
            panel._load_local()
        self._show_status_message("已添加记录到本地数据库", 3000)
        self._append_log("[添加] 手动添加一条球桌记录到本地数据库")

    def _panel_entry_popout_enabled(self):
        """工具栏面板入口（跑视频/售后/球桌管理）是否默认弹出独立面板。

        设置项 panel_entry_popout（ui 域，默认 True，2026-09-20）：
        开 = 点入口弹出独立面板窗口（open_hub_popout）；
        关 = 回落为主窗口内导航跳转（switchTo）。
        """
        try:
            return bool(self._load_settings().get("panel_entry_popout", True))
        except Exception:
            return True

    def _on_open_table_panel(self):
        """打开球桌管理：默认弹出独立面板（2026-09-20 设置项
        panel_entry_popout，默认开）；关闭该设置时回落为跳转主窗口
        「运维管理」页（单窗口导航版）。

        原 FluentWindow 独立面板保留（windows/management_panel.py shim 仍可
        独立进程启动）。
        """
        hub = getattr(self, "management_hub", None)
        if hub is None:
            return
        if self._panel_entry_popout_enabled():
            self.open_hub_popout(hub)
        else:
            self.switchTo(hub)

    def _on_open_aftersale(self):
        """打开售后面板：默认弹出独立面板（2026-09-20 设置项
        panel_entry_popout，默认开）；关闭该设置时回落为跳转主窗口
        「售后」页（单窗口导航版）。

        原 AftersalePanelWindow 独立面板保留（windows/aftersale_panel.py shim
        仍可独立进程启动）。
        """
        hub = getattr(self, "aftersale_hub", None)
        if hub is None:
            return
        if self._panel_entry_popout_enabled():
            self.open_hub_popout(hub)
        else:
            self.switchTo(hub)

    def _on_open_conn_diag(self):
        """打开连接诊断面板（非模态独立窗口，单例：已打开则激活）"""
        from windows.remote_session.conn_diag_panel import ConnDiagPanel
        if not hasattr(self, '_conn_diag') or self._conn_diag is None:
            self._conn_diag = ConnDiagPanel()
            self._conn_diag.destroyed.connect(
                lambda: setattr(self, '_conn_diag', None))
        self._conn_diag.show()
        self._conn_diag.raise_()
        self._conn_diag.activateWindow()

    # ==================== 端口占用（工具菜单） ====================

    def _on_open_port_fake(self):
        """工具菜单「端口占用」：弹窗真实监听指定端口，模拟服务占用

        绑定后 netstat -ano 可见 LISTENING，用于测试端口冲突/验证服务
        检测逻辑；支持多端口同时占用，关闭弹窗自动释放全部端口。
        """
        from PySide6.QtWidgets import QDialog, QVBoxLayout
        from windows.tools.port_fake import PortFakeWidget
        dlg = QDialog(self)
        dlg.setWindowTitle("端口占用")
        dlg.resize(480, 420)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        w = PortFakeWidget(dlg)
        lay.addWidget(w)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.finished.connect(lambda: w._release_all(notify=False))
        dlg.open()

    # ==================== 单杆视频（工具菜单，收编 single_json） ====================

    def _on_open_single_video(self):
        """工具菜单「单杆视频」：弹参数对话框，后台 Worker 生成单杆视频

        逐帧渲染为 CPU 密集任务，必须由 QThread 后台执行（Worker 由
        主窗口持有，日志直写对话框文本区，生成中禁止关闭对话框）。
        """
        worker = getattr(self, "_single_video_worker", None)
        if worker is not None and worker.isRunning():
            self._show_info_bar("单杆视频正在生成中，请等待完成", "warning")
            return

        # 延迟 import：单杆功能模块依赖 cv2/numpy/Pillow，缺失时给出明确提示而非崩溃
        try:
            import cv2  # noqa: F401
            import numpy  # noqa: F401
        except ImportError as e:
            self._show_info_bar(
                f"无法加载单杆视频依赖（请确认已安装 opencv-python、numpy）: {e}",
                "error", duration=5000)
            return

        from windows.single_video_dialog import SingleVideoDialog
        dlg = SingleVideoDialog(self, self._load_settings())
        dlg.widget.setMinimumWidth(640)

        def _start():
            params = dlg.collect_params()
            if not params:
                return
            # 记住本次路径配置，下次直接作为默认值
            self._save_settings({
                "single_pending_root": params["pending_root"],
                "single_videos_root": params["videos_root"],
            })
            dlg.enter_running()
            w = SingleVideoWorker(params)
            self._single_video_worker = w
            w.line.connect(dlg.append_line)
            w.finished_ok.connect(lambda path, d=dlg: self._on_single_video_finished(d, path))
            w.error.connect(lambda msg, d=dlg: self._on_single_video_failed(d, msg))
            w.finished.connect(lambda w=w: self._release_worker_safe(w))
            w.start()
            self._append_log(f"[单杆] 开始生成单杆视频: {params['video_name']}")

        dlg.on_start = _start
        dlg.open()

    def _on_single_video_finished(self, dlg, video_path):
        """单杆视频生成完成：恢复对话框并提示输出位置"""
        w = getattr(self, "_single_video_worker", None)
        self._single_video_worker = None
        if w is not None:
            self._release_worker_safe(w)
        dlg.append_line("")
        dlg.append_line(f"✔ 单杆视频生成完成: {video_path}")
        dlg.enter_done(video_path)
        self._append_log(f"[单杆] 单杆视频生成完成: {video_path}")
        self._show_info_bar("单杆视频生成完成，可点击「打开视频目录」查看",
                            "success", duration=4000)

    def _on_single_video_failed(self, dlg, msg):
        """单杆视频 Worker 失败：错误 InfoBar + 对话框恢复可重新发起"""
        w = getattr(self, "_single_video_worker", None)
        self._single_video_worker = None
        if w is not None:
            self._release_worker_safe(w)
        dlg.append_line("")
        dlg.append_line(f"✘ {msg}")
        dlg.enter_failed()
        self._append_log(f"[单杆] 单杆视频生成失败: {msg}")
        self._show_info_bar(msg, "error", duration=4000)

    # ==================== 视频/日志批量整理（NewLog 收编，Task #40） ====================

    def _on_newlog_organize(self):
        """工具菜单「视频/日志批量整理」：弹署名确认对话框，后台 Worker 运行 NewLog

        整理完成后对话框进入「打包上传」阶段（复用运维面板
        ZipUploadWorker，Task #57），上传中可最小化/取消。
        """
        worker = getattr(self, "_newlog_worker", None)
        if worker is not None and worker.isRunning():
            self._show_info_bar("整理任务正在运行中，请等待完成", "warning")
            return
        up_worker = getattr(self, "_newlog_upload_worker", None)
        if up_worker is not None and up_worker.isRunning():
            self._show_info_bar("打包上传进行中，请等待完成或先取消", "warning")
            return

        # 延迟 import：newlog 依赖 openpyxl，缺失时给出明确提示而非崩溃
        try:
            from windows.tools import newlog  # noqa: F401 —— 仅用于探测依赖（newlog 顶层 import openpyxl）
        except ImportError as e:
            self._show_info_bar(f"无法加载 newlog 模块（请确认已安装 openpyxl）: {e}",
                                "error", duration=5000)
            return
        # 默认筛选署名：上次整理时保存的 newlog_target_name（无记录则为空）
        default_name = str(self._load_settings().get("newlog_target_name", "") or "")

        dlg = NewLogDialog(self, default_name)
        dlg.yesButton.setText("开始整理")
        dlg.cancelButton.setText("关闭")
        dlg.widget.setMinimumWidth(600)
        # 对话框引用挂主窗口：隐藏/销毁后上传回调需判断存活（防悬挂引用）
        self._newlog_dlg = dlg
        dlg.destroyed.connect(self._on_newlog_dlg_destroyed)

        def _start():
            target = dlg.target_edit.text().strip()
            if not target:
                self._show_info_bar("请输入筛选署名", "warning")
                return
            # 记住本次署名，下次直接作为默认值
            self._save_settings({"newlog_target_name": target})
            dlg.enter_running()
            w = NewLogWorker(target)
            self._newlog_worker = w
            w.line.connect(dlg.append_line)
            w.finished_ok.connect(lambda out, d=dlg: self._on_newlog_finished(d, out))
            w.error.connect(lambda msg, d=dlg: self._on_newlog_failed(d, msg))
            w.finished.connect(lambda w=w: self._release_worker_safe(w))
            w.start()
            self._append_log(f"[整理] 开始视频/日志批量整理（署名：{target}）")

        dlg.on_start = _start
        dlg.on_upload = self._start_newlog_upload
        dlg.on_cancel_upload = self._on_newlog_cancel_upload
        dlg.open()

    def _on_newlog_dlg_destroyed(self):
        """对话框销毁（含上传中被用户关闭）：清引用，后续回调不再碰它"""
        self._newlog_dlg = None

    @staticmethod
    def _release_worker_safe(worker):
        """安全释放 QThread：线程仍在运行时交由 finished → deleteLater 延迟销毁

        ZipUploadWorker 的 done/error/cancelled 信号在 run() 内部发出，信号槽
        执行时线程可能仍在收尾（关闭 SFTP/SSH 连接）。此时直接置 None 释放引用
        会让 Python GC 销毁仍在运行的 QThread，触发
        "QThread: Destroyed while thread is still running" 崩溃。

        注意：finished 必须连接 lambda 包装而非 worker.deleteLater 本身——
        PySide6 对 C++ 内建方法直连不持有 Python 引用，worker 仍会被 GC。
        """
        try:
            if worker.isRunning():
                worker.finished.connect(lambda w=worker: w.deleteLater())
            else:
                worker.deleteLater()
        except RuntimeError:
            pass

    def _on_newlog_finished(self, dlg, out_path):
        """NewLog 整理完成：进入「打包上传」待发起状态"""
        w = getattr(self, "_newlog_worker", None)
        self._newlog_worker = None
        if w is not None:
            self._release_worker_safe(w)
        dlg.append_line("")
        dlg.append_line(f"✔ 整理完成，输出目录: {out_path}")
        dlg.enter_organized(out_path)
        self._append_log(f"[整理] 视频/日志批量整理完成: {out_path}")
        self._show_info_bar("整理完成，可在对话框中点击「打包上传」上传服务器",
                            "success", duration=4000)

    def _on_newlog_failed(self, dlg, msg):
        """NewLog Worker 失败：错误 InfoBar + 对话框恢复可重新发起"""
        w = getattr(self, "_newlog_worker", None)
        self._newlog_worker = None
        if w is not None:
            self._release_worker_safe(w)
        dlg.append_line("")
        dlg.append_line(f"✘ {msg}")
        dlg.enter_failed()
        self._append_log(f"[整理] 视频/日志批量整理失败: {msg}")
        self._show_info_bar(msg, "error", duration=4000)

    # ---------- 批量整理产物打包上传（复用 ZipUploadWorker，Task #57） ----------

    @staticmethod
    def _newlog_upload_target(settings):
        """读取上传目标配置（与运维面板默认值保持一致）"""
        host = str(settings.get("upload_host") or "49.235.34.253").strip()
        try:
            port = int(settings.get("upload_port") or 22)
        except (TypeError, ValueError):
            port = 22
        remote_dir = str(settings.get("upload_remote_dir") or "/lhcos-data/videos").strip()
        username = str(settings.get("upload_user") or "root").strip()
        password = str(settings.get("upload_pass") or "")
        return host, port, remote_dir, username, password

    def _start_newlog_upload(self):
        """将整理产物打包为 zip 放入 videos/upload/ 后走 ZipUploadWorker 上传

        Excel 读取与整理产物保持不变；仅打包/上传两步复用运维面板模式。
        zip 落在 upload 目录（zip_prefix=newlog），不清空共享的 upload
        收集目录，上传成功后删除本地 zip。worker 挂主窗口引用，对话框
        隐藏后上传后台继续。
        """
        dlg = getattr(self, "_newlog_dlg", None)
        if dlg is None:
            return
        running = getattr(self, "_newlog_upload_worker", None)
        if running is not None and running.isRunning():
            self._show_info_bar("已有上传进行中，请稍候", "warning")
            return
        out_path = getattr(dlg, "_out_path", "")
        if not out_path or not os.path.isdir(out_path) or not os.listdir(out_path):
            self._show_info_bar("整理输出目录为空，无法打包上传", "warning")
            return
        videos_dir = getattr(self, "videos_dir", "") or ""
        if not videos_dir or not os.path.isdir(videos_dir):
            self._show_info_bar("videos_dir 未配置或目录不存在，请先在设置中配置视频/日志目录",
                                "warning", duration=4000)
            return
        host, port, remote_dir, username, password = self._newlog_upload_target(
            self._load_settings())
        if not password:
            # 与运维面板行为一致：凭据缺失时提示去管理设置配置
            dlg.append_line("✘ 未配置上传密码（upload_pass），请在管理设置中配置")
            self._show_info_bar("未配置上传密码，请先在管理设置中填写后重试",
                                "warning", duration=4000)
            return

        upload_root = os.path.join(videos_dir, "upload")
        worker = ZipUploadWorker(
            upload_root, host, port, username, password, remote_dir,
            content_root=out_path, zip_prefix="newlog", zip_dir=upload_root,
            cleanup_after_done=False, remove_zip_after_done=True)
        self._newlog_upload_worker = worker
        worker.progress.connect(self._on_newlog_upload_progress)
        worker.percent.connect(self._on_newlog_upload_percent)
        worker.done.connect(self._on_newlog_upload_done)
        worker.error.connect(self._on_newlog_upload_fail)
        worker.cancelled.connect(self._on_newlog_upload_cancelled)
        dlg.enter_uploading()
        dlg.append_line("")
        dlg.append_line(f"▶ 开始打包上传 → {host}:{remote_dir}")
        worker.start()
        self._append_log(f"[整理] 开始打包上传整理产物: {os.path.basename(out_path)}")

    def _on_newlog_cancel_upload(self):
        """对话框「取消上传」：二次确认后请求中断（压缩中/上传中均可）"""
        worker = getattr(self, "_newlog_upload_worker", None)
        if worker is None or not worker.isRunning():
            return
        box = MessageBox("取消上传", "上传进行中，确定取消？", self)
        box.yesButton.setText("确定取消")
        box.cancelButton.setText("继续上传")
        if not box.exec():
            return
        # 确认弹窗阻塞期间上传可能恰好完成，再确认一次避免误锁按钮
        if not worker.isRunning():
            return
        dlg = getattr(self, "_newlog_dlg", None)
        if dlg is not None:
            dlg.append_line("… 正在取消上传...")
            dlg.yesButton.setEnabled(False)
        worker.requestInterruption()

    def _reactivate_newlog_dlg(self, dlg):
        """对话框被最小化/隐藏时重新弹出并激活（后台上传完成提示入口）"""
        if not dlg.isVisible():
            dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_newlog_upload_progress(self, msg):
        """上传阶段提示 → 对话框输出区（对话框已销毁则丢弃）"""
        dlg = getattr(self, "_newlog_dlg", None)
        if dlg is not None:
            dlg.append_line(msg)

    def _on_newlog_upload_percent(self, p):
        """上传字节进度 → 对话框进度条（对话框已销毁则丢弃）"""
        dlg = getattr(self, "_newlog_dlg", None)
        if dlg is not None:
            dlg.set_upload_percent(p)

    def _on_newlog_upload_done(self, info):
        """上传成功：安全释放 worker + 主窗口 InfoBar + 重新激活对话框"""
        w = getattr(self, "_newlog_upload_worker", None)
        self._newlog_upload_worker = None
        if w is not None:
            self._release_worker_safe(w)
        self._append_log(f"[整理] 打包上传成功: {info}")
        self._show_info_bar(f"打包上传成功: {info}", "success", duration=5000)
        dlg = getattr(self, "_newlog_dlg", None)
        if dlg is None:
            return
        dlg.append_line(f"✔ 上传成功: {info}（本地临时 zip 已清理）")
        dlg.enter_organized(dlg._out_path)
        self._reactivate_newlog_dlg(dlg)

    def _on_newlog_upload_fail(self, msg):
        """上传失败：恢复对话框可再次发起，错误 InfoBar"""
        w = getattr(self, "_newlog_upload_worker", None)
        self._newlog_upload_worker = None
        if w is not None:
            self._release_worker_safe(w)
        self._append_log(f"[整理] 打包上传失败: {msg}")
        self._show_info_bar(msg.split(chr(10))[0], "error", duration=4000)
        dlg = getattr(self, "_newlog_dlg", None)
        if dlg is None:
            return
        dlg.append_line(f"✘ 上传失败: {msg}")
        dlg.enter_organized(dlg._out_path)
        self._reactivate_newlog_dlg(dlg)

    def _on_newlog_upload_cancelled(self):
        """取消完成：恢复按钮状态、安全释放 worker（临时 zip 已由 worker 删除）"""
        w = getattr(self, "_newlog_upload_worker", None)
        self._newlog_upload_worker = None
        if w is not None:
            self._release_worker_safe(w)
        self._append_log("[整理] 打包上传已取消（临时 zip 已清理）")
        self._show_info_bar("上传已取消，临时 zip 已清理", "info", duration=4000)
        dlg = getattr(self, "_newlog_dlg", None)
        if dlg is None:
            return
        dlg.append_line("✘ 上传已取消（临时 zip 已清理）")
        dlg.enter_organized(dlg._out_path)
        self._reactivate_newlog_dlg(dlg)

    def _on_open_tool_hub(self, work=None):
        """工具入口统一跳转二期独立工具页（main_window.tool_hub）。

        work 可为工作区属性名（single_video_work/port_fake_work/
        upload_list_work/newlog_work），缺省保持当前页。2026-09-07：
        菜单栏/设置-工具的 4 条旧弹窗入口全部改走本方法（弹窗代码保留
        不删，作为回退兜底）。
        """
        hub = getattr(self, "tool_hub", None)
        if hub is None:
            return
        self.switchTo(hub)
        if work:
            page = getattr(hub, work, None)
            if page is not None:
                hub.switchTo(page)

    def _on_open_settings(self):
        """统一设置入口：跳转底部设置 Hub（2026-09-07 弹窗废止）

        配置项已全部内联迁入 SettingsHubPage（编辑即存，无保存按钮）；
        原弹窗保存后的后处理已随配置项迁入对应保存回调：路径 →
        make_path_row 即存后 _load_paths；MySQL → MysqlSyncCard 卡内
        保存后 invalidate_mysql_settings_cache；日志高亮 → 规则组
        _persist 即时重编译 _log_rules；字体/缩放 → 外观组内联回调。
        """
        self.switchTo(self.settings_hub)

    def _on_about(self):
        """显示关于对话框（版本号随 git 发布手工递增，含 GitHub 与开源库链接）"""
        dlg = AboutDialog(self)
        dlg.yesButton.setText("确定")
        dlg.cancelButton.hide()
        dlg.widget.setMinimumWidth(560)
        dlg.exec()

    def _on_perf_options(self):
        """性能选项对话框：SwitchButton 独立控制亚克力/动画/表格平滑滚动/大屏模式，切换即时生效无需重启"""
        from core.perf import (is_acrylic_enabled, is_animation_enabled,
                               set_acrylic_enabled, set_animation_enabled,
                               is_table_smooth_scroll_enabled,
                               set_table_smooth_scroll_enabled,
                               apply_table_smooth_globally,
                               is_bigscreen_mode, set_bigscreen_mode,
                               is_table_blit_enabled, set_table_blit_enabled,
                               degrade_diagnostics_text,
                               table_blit_status_text)

        class PerfOptionsDialog(MessageBoxBase):
            def __init__(self, parent):
                super().__init__(parent)
                self.titleLabel = BodyLabel("性能选项", self)
                self.viewLayout.addWidget(self.titleLabel)

                # 亚克力效果开关
                row1 = QHBoxLayout()
                lbl1 = BodyLabel("亚克力磨砂效果", self)
                lbl1.setToolTip("关闭后菜单/下拉框使用纯色背景，大幅降低核显开销")
                row1.addWidget(lbl1, 1)
                self.sw_acrylic = SwitchButton(self)
                self.sw_acrylic.setOnText("开")
                self.sw_acrylic.setOffText("关")
                self.sw_acrylic.setChecked(is_acrylic_enabled())
                row1.addWidget(self.sw_acrylic)
                self.viewLayout.addLayout(row1)

                # 弹出动画开关（全局：影响未单独设置的面板）
                row2 = QHBoxLayout()
                lbl2 = BodyLabel("菜单弹出动画", self)
                lbl2.setToolTip(
                    "关闭后菜单/下拉框直接弹出，无过渡动画。\n"
                    "全局生效；售后面板/跑视频面板可在各自设置里单独覆盖")
                row2.addWidget(lbl2, 1)
                self.sw_animation = SwitchButton(self)
                self.sw_animation.setOnText("开")
                self.sw_animation.setOffText("关")
                self.sw_animation.setChecked(is_animation_enabled())
                row2.addWidget(self.sw_animation)
                self.viewLayout.addLayout(row2)

                # 表格平滑滚动开关（全局：影响未单独设置的面板）
                row3 = QHBoxLayout()
                lbl3 = BodyLabel("表格平滑滚动", self)
                lbl3.setToolTip(
                    "开启后记录列表滚动带动画；关闭走原生滚动更快。\n"
                    "全局生效；售后面板/跑视频面板可在各自设置里单独覆盖")
                row3.addWidget(lbl3, 1)
                self.sw_table_smooth = SwitchButton(self)
                self.sw_table_smooth.setOnText("开")
                self.sw_table_smooth.setOffText("关")
                self.sw_table_smooth.setChecked(is_table_smooth_scroll_enabled())
                row3.addWidget(self.sw_table_smooth)
                self.viewLayout.addLayout(row3)

                # 大屏性能模式（P1-4）：无视阈值强制降级动画（大屏一键）
                row4 = QHBoxLayout()
                lbl4 = BodyLabel("大屏性能模式", self)
                lbl4.setToolTip(
                    "4K/高缩放大屏卡顿时开启：弹窗/页面切换/菜单动画\n"
                    "自动降级保流畅（默认按分辨率智能判定，此开关为手动覆盖）")
                row4.addWidget(lbl4, 1)
                self.sw_bigscreen = SwitchButton(self)
                self.sw_bigscreen.setOnText("开")
                self.sw_bigscreen.setOffText("关")
                self.sw_bigscreen.setChecked(is_bigscreen_mode())
                row4.addWidget(self.sw_bigscreen)
                self.viewLayout.addLayout(row4)

                # 表格滚动加速（位块搬移，v2，2026-10-06）：先把 viewport 真正
                # 画满（实测取表格背后的实底颜色填充），再开不透明换位块搬移。
                # 第一版只设属性不画底，真机整片叠影，故本轮带像素等价回归。
                row5 = QHBoxLayout()
                lbl5 = BodyLabel("表格滚动加速", self)
                lbl5.setToolTip(
                    "滚动时只重绘新露出的一行（实测光栅面积降至 1/758、"
                    "每步 10.0→1.2ms）。\n"
                    "若表格底色或滚动观感不对，关掉即可完全回退")
                row5.addWidget(lbl5, 1)
                self.sw_table_blit = SwitchButton(self)
                self.sw_table_blit.setOnText("开")
                self.sw_table_blit.setOffText("关")
                self.sw_table_blit.setChecked(is_table_blit_enabled())
                row5.addWidget(self.sw_table_blit)
                self.viewLayout.addLayout(row5)

                # 渲染加速状态（只读诊断行，2026-10-06）：把两处「静默生效」
                # 的机制摊开——自适应降级阈值（本机标定后是多少、是否被
                # 配置/大屏模式接管）与表格滚动加速的实际命中张数。此前
                # 两者都只在日志里留痕，用户无从判断某张表到底有没有生效。
                status = f"{degrade_diagnostics_text()}\n{table_blit_status_text()}"
                self.statusLabel = CaptionLabel(status, self)
                self.statusLabel.setWordWrap(True)
                self.statusLabel.setToolTip(
                    "自动降级阈值：超过该物理像素数的窗口只保留最省成本\n"
                    "的动画路径（此值按本机光栅速率自动标定，配置里显式\n"
                    "指定 perf_dpi_degrade_pixels 则以其为准）。\n"
                    "表格滚动加速：只有取到表格背后实底颜色（四周需有\n"
                    "≥6px 纯色间隙、无遮挡）的表格才会生效；「已生效\n"
                    "N/M 张」随当前打开的页面变化。")
                self.viewLayout.addWidget(self.statusLabel)

        dlg = PerfOptionsDialog(self)
        dlg.yesButton.setText("完成")
        dlg.cancelButton.hide()
        dlg.widget.setMinimumWidth(340)

        # 实时应用：拨动开关的瞬间即生效并持久化
        def _on_acrylic_toggled(checked):
            set_acrylic_enabled(checked)
            state = "开启" if checked else "关闭"
            self._append_log(f"[性能] 亚克力效果已{state}（即时生效）")

        def _on_animation_toggled(checked):
            set_animation_enabled(checked)
            state = "开启" if checked else "关闭"
            self._append_log(f"[性能] 弹出动画已{state}（全局，即时生效）")

        def _on_table_smooth_toggled(checked):
            set_table_smooth_scroll_enabled(checked)
            state = "开启" if checked else "关闭"
            self._append_log(f"[性能] 表格平滑滚动已{state}（全局，即时生效）")
            # 全局变更：刷新所有未单独覆盖的已打开面板表格滚动模式
            apply_table_smooth_globally()

        def _on_bigscreen_toggled(checked):
            set_bigscreen_mode(checked)
            state = "开启" if checked else "关闭"
            self._append_log(f"[性能] 大屏性能模式已{state}（即时生效）")

        dlg.sw_acrylic.checkedChanged.connect(_on_acrylic_toggled)
        dlg.sw_animation.checkedChanged.connect(_on_animation_toggled)
        dlg.sw_table_smooth.checkedChanged.connect(_on_table_smooth_toggled)
        def _on_table_blit_toggled(checked):
            set_table_blit_enabled(checked)
            state = "开启" if checked else "关闭"
            self._append_log(f"[性能] 表格滚动加速已{state}（即时生效）")

        dlg.sw_bigscreen.checkedChanged.connect(_on_bigscreen_toggled)
        dlg.sw_table_blit.checkedChanged.connect(_on_table_blit_toggled)
        dlg.exec()

    # ==================== 设置应用 ====================

    @staticmethod
    def _parse_theme_color(settings):
        """解析主题强调色（实现见 core.theme_policy，兼容旧 highlight_color）"""
        return theme_policy.parse_theme_color(settings)

    def _apply_theme_color(self):
        """从 settings.json 加载主题强调色（实现见 ui.appearance）。"""
        return appearance.apply_theme_color(self)

    def _apply_font_size(self):
        """字号变更入口（统一走全局字体应用）"""
        self._apply_global_font()

    def _apply_font_family(self):
        """字体变更入口（统一走全局字体应用）"""
        self._apply_global_font()

    def _apply_global_font(self):
        """统一应用全局字体（实现见 ui.appearance）。"""
        return appearance.apply_global_font(self)

    def _set_widget_font_recursive(self, widget, app_font):
        """递归套字体（实现见 ui.appearance）。"""
        return appearance.set_widget_font_recursive(widget, app_font)

    # ==================== 主题 ====================

    @staticmethod
    def _get_theme_mode(settings):
        """获取主题模式（实现见 core.theme_policy）"""
        return theme_policy.theme_mode(settings)

    @staticmethod
    def _system_is_dark():
        """检测 Windows 系统当前是否为深色主题（实现见 core.theme_policy）"""
        return theme_policy.system_is_dark()

    @staticmethod
    def _effective_is_dark(settings):
        """解析实际生效的深色状态；仍走 UIMixin._system_is_dark 以保留 monkeypatch 点"""
        return theme_policy.effective_is_dark(settings, UIMixin._system_is_dark)

    def _apply_theme(self):
        """应用 Fluent 主题 + 业务 QSS（实现见 ui.appearance）。"""
        return appearance.apply_theme(self)

    def _apply_business_qss(self):
        """业务 QSS 挂到 stackedWidget（实现见 main_window.ui.qss_runtime）。"""
        return qss_runtime.apply_business_qss(self)

    def _reset_qss_compose_trees(self):
        """清理 qfw StyleSheetCompose 嵌套膨胀（实现见 qss_runtime）。"""
        return qss_runtime.reset_qss_compose_trees()

    def _enforce_toolbar_radio_height(self):
        """工具栏 RadioButton 固定 32px（实现见 qss_runtime）。"""
        return qss_runtime.enforce_toolbar_radio_height(self.ui)

    def _enforce_toolbar_button_height(self):
        """工具栏按钮/输入控件固定 32px（实现见 qss_runtime）。"""
        return qss_runtime.enforce_toolbar_button_height(self.ui)

    def _load_qss(self, theme_name):
        """加载 styles/{theme}.qss（实现见 qss_runtime）。"""
        return qss_runtime.load_qss(theme_name)

    # ==================== 系统主题监听 ====================

    def _init_system_theme_monitor(self):
        """初始化系统主题变化轮询（实现见 ui.appearance）。"""
        return appearance.init_system_theme_monitor(self)

    def _sync_theme_polling(self):
        """按主题模式启停轮询（实现见 ui.appearance）。"""
        return appearance.sync_theme_polling(self)

    def _poll_system_theme(self):
        """轮询系统深浅色（实现见 ui.appearance）。"""
        return appearance.poll_system_theme(self)

    def _on_theme_selected(self, action):
        """主题子菜单互斥选择（实现见 ui.appearance）。"""
        return appearance.on_theme_selected(self, action)

    # ==================== 布局 ====================

    def _on_layout_selected(self, action):
        """布局子菜单互斥选择（实现见 ui.appearance）。"""
        return appearance.on_layout_selected(self, action)

    def _apply_layout(self):
        """从 settings.json 加载布局偏好（实现见 ui.appearance）。"""
        return appearance.apply_layout(self)

    @staticmethod
    def apply_dpi_scale(settings):
        """在 QApplication 创建前应用 DPI 缩放（实现已下沉 core.ui_scale）"""
        return ui_scale.apply_dpi_scale(settings)