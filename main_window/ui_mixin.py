# -*- coding: utf-8 -*-
"""MainWindow UI Mixin：状态栏、右键菜单、菜单栏、主题/字体/布局、设置对话框

独立的关于/整理对话框、快捷键录入框与亚克力菜单已迁至 main_window/ui/；
本模块继续 re-export 这些名字，外部导入路径保持不变。
"""

from core import theme_policy, ui_scale

# 模块级 UI 资产已迁出；此处 re-export 保持 main_window.ui_mixin.X 旧路径可用
from .ui import (appearance, context_menus, menu_actions, navigation,
                 settings_dialogs, status_bar, qss_runtime)
from .ui.about_dialog import (APP_VERSION, ABOUT_LINKS, ABOUT_OSS_LIBS,
    GITHUB_REPO, AboutDialog)
from .ui.acrylic_menu import (_ensure_menu_fit, _patch_acrylic_exec,
    _VisibleAcrylicView, VisibleAcrylicMenu)
from .ui.shortcut_edit import _ShortcutKeyEdit
from .ui.newlog_dialog import NewLogDialog


class UIMixin:
    """UI 相关方法：状态栏、右键菜单、菜单栏、主题、字体、布局"""
    ui: 'Ui_MainWindow'
    videos_dir: str
    # ==================== 状态栏 ====================

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


    # ==================== 菜单栏 ====================

    def _init_menubar(self):
        """初始化顶部菜单栏 Action 与空壳（实现见 ui.menu_actions）。"""
        return menu_actions.init_menubar(self)

    # ==================== 设置对话框（Fluent 风格） ====================

    def _on_modify_shortcuts(self):
        """弹出快捷键修改对话框（实现见 ui.settings_dialogs）。"""
        return settings_dialogs.modify_shortcuts(self)

    def _on_theme_color(self):
        """弹出主题强调色选择对话框（实现见 ui.settings_dialogs）。"""
        return settings_dialogs.theme_color(self)

    def _on_theme_color_reset(self):
        """还原默认主题强调色（实现见 ui.settings_dialogs）。"""
        return settings_dialogs.theme_color_reset(self)

    def _apply_theme_color_set(self, color):
        """应用主题强调色（实现见 ui.settings_dialogs）。"""
        return settings_dialogs.apply_theme_color_set(self, color)

    def _set_font_size_inline(self, val):
        """内联字号回调（实现见 ui.settings_dialogs）。"""
        return settings_dialogs.set_font_size_inline(self, val)

    def _set_font_family_inline(self, family):
        """内联字体回调（实现见 ui.settings_dialogs）。"""
        return settings_dialogs.set_font_family_inline(self, family)

    def _set_dpi_scale_inline(self, val):
        """内联缩放回调（实现见 ui.settings_dialogs）。"""
        return settings_dialogs.set_dpi_scale_inline(self, val)


    def _panel_entry_popout_enabled(self):
        """面板入口是否弹出独立窗口（实现见 ui.navigation）。"""
        return navigation.panel_entry_popout_enabled(self)

    def _on_open_table_panel(self):
        """打开球桌管理（实现见 ui.navigation）。"""
        return navigation.open_table_panel(self)

    def _on_open_aftersale(self):
        """打开售后面板（实现见 ui.navigation）。"""
        return navigation.open_aftersale(self)

    def _on_open_conn_diag(self):
        """打开连接诊断面板（实现见 ui.navigation）。"""
        return navigation.open_conn_diag(self)

    # ==================== 端口占用（工具菜单） ====================

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

    def _on_open_tool_hub(self, work=None):
        """工具入口统一跳转工具页（实现见 ui.navigation）。"""
        return navigation.open_tool_hub(self, work)


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