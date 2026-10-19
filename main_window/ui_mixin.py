# -*- coding: utf-8 -*-
"""MainWindow UI Mixin：状态栏、右键菜单、菜单栏、主题/字体/布局、设置对话框

独立的关于/整理对话框、快捷键录入框与亚克力菜单已迁至 main_window/ui/；
本模块继续 re-export 这些名字，外部导入路径保持不变。
"""

from core import theme_policy, ui_scale

# UI 资产已迁进 main_window/ui/，这里只做名字 re-export：主窗口与测试
# 都按 main_window.ui_mixin.X 取符号，删掉就得全仓改调用点
from .ui import (appearance, context_menus, menu_actions, navigation,
                 settings_dialogs, status_bar, qss_runtime)
from .ui.about_dialog import (APP_VERSION, ABOUT_LINKS, ABOUT_OSS_LIBS,
    GITHUB_REPO, AboutDialog)
from .ui.acrylic_menu import (_ensure_menu_fit, _patch_acrylic_exec,
    _VisibleAcrylicView, VisibleAcrylicMenu)
from .ui.shortcut_edit import _ShortcutKeyEdit
from .ui.newlog_dialog import NewLogDialog


class UIMixin:
    """主窗口的 UI 能力装配层：状态栏、右键菜单、菜单栏、主题、字体、布局。

    方法体一律转交 main_window/ui/ 下的模块，本类只保留稳定入口名；
    MainWindow 混入后到处按 self._xxx 调用，实现放独立模块才能脱开
    主窗口单独验证，而入口名不变则调用点不用跟着动。
    """
    ui: 'Ui_MainWindow'
    videos_dir: str
    # ==================== 状态栏 ====================

    def _init_statusbar(self):
        """初始化底部状态栏（实现见 main_window.ui.status_bar）。"""
        return status_bar.init_statusbar(self)

    def _show_status_message(self, msg, timeout=0):
        """在状态栏打一条消息，timeout 原样透传给实现。

        默认值不在这里另设一份：两处各写默认值，改一处就会让
        「常驻」和「几秒后消失」的语义对不上。
        """
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
        """给各列表装右键菜单策略，并把菜单对象预先构建好缓存起来。

        不在右键时才构建：首次弹出会把构建耗时暴露成卡顿，
        信号也必须在构建期一次接好，否则第一次点击没有反应。
        """
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
        """在列表控件坐标 pos 处弹出设备列表菜单（实现见 ui.context_menus）。

        坐标换算交给实现做：本层再 mapToGlobal 一次，菜单会弹到别处。
        """
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
    # 下面这批入口的实现都在 ui.settings_dialogs，菜单 Action 按方法名连接；
    # 改名或删方法会让菜单项静默失效，Qt 连不上槽是不抛异常的

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
        """把 settings 里的强调色解析成可用颜色，兼容旧键 highlight_color。

        纯函数且是 staticmethod：不依赖控件就能单测；旧键兼容只在 core
        里判一次，避免主窗口再判一遍而两处规则慢慢漂移。
        """
        return theme_policy.parse_theme_color(settings)

    def _apply_theme_color(self):
        """从 settings.json 加载主题强调色（实现见 ui.appearance）。"""
        return appearance.apply_theme_color(self)

    def _apply_font_size(self):
        """字号变更入口，与字族一起收敛到全局字体应用。

        不逐控件套字体：qfw 会持续新建控件与弹窗，逐个设置必然漏掉，
        同一界面就会出现两种字号，只能整体下发。
        """
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
        """算出当前实际生效的深色状态，跟随系统时才探测系统值。

        刻意经 UIMixin._system_is_dark 转一手而不是直传 theme_policy 的函数：
        测试靠 patch 这个方法注入深浅色，绕过去那批用例就失效了。
        """
        return theme_policy.effective_is_dark(settings, UIMixin._system_is_dark)

    def _apply_theme(self):
        """应用 Fluent 主题 + 业务 QSS（实现见 ui.appearance）。"""
        return appearance.apply_theme(self)

    def _apply_business_qss(self):
        """业务 QSS 挂到 stackedWidget（实现见 main_window.ui.qss_runtime）。"""
        return qss_runtime.apply_business_qss(self)

    def _reset_qss_compose_trees(self):
        """清掉 qfw 的 StyleSheetCompose 嵌套膨胀（实现见 qss_runtime）。

        qfw 每次 setStyleSheet 都会再包一层 compose 节点，反复切换主题后
        树深只增不减，绘制逐次变慢，所以要主动清一次。
        """
        return qss_runtime.reset_qss_compose_trees()

    def _enforce_toolbar_radio_height(self):
        """把工具栏 RadioButton 固定到 32px（实现见 qss_runtime）。

        QSS 给的高度常被带图标 RadioButton 的 sizeHint 顶掉，
        只能建完后用代码兜底，否则工具栏会一高一低。
        """
        return qss_runtime.enforce_toolbar_radio_height(self.ui)

    def _enforce_toolbar_button_height(self):
        """工具栏按钮/输入控件固定 32px（实现见 qss_runtime）。"""
        return qss_runtime.enforce_toolbar_button_height(self.ui)

    def _load_qss(self, theme_name):
        """读取 styles 下对应主题的 qss 文本（实现见 qss_runtime）。

        每次现读文件而不缓存内容：样式文件是可直接改的调试入口，
        缓存住以后改样式必须重启才能看到效果。
        """
        return qss_runtime.load_qss(theme_name)

    # ==================== 系统主题监听 ====================
    # Qt 不暴露系统主题变更消息，只能定时轮询；手动指定深浅色时轮询会停掉

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
        """在 QApplication 建起来之前设置 DPI 缩放（实现已下沉 core.ui_scale）。

        做成 staticmethod 是因为此刻还没有 MainWindow 实例可依附；
        晚于 QApplication 再设就来不及了，Qt 已按旧缩放建好内部尺寸。
        """
        return ui_scale.apply_dpi_scale(settings)