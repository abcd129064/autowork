# -*- coding: utf-8 -*-
"""MainWindow UI Mixin：状态栏、右键菜单、菜单栏、主题/字体/布局、设置对话框"""

import os
import sys
import json
import ctypes
import subprocess

from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QHBoxLayout,
    QVBoxLayout, QFormLayout, QFrame)
from PySide6.QtCore import QTimer, Qt, QRect
from PySide6.QtGui import (QColor, QKeySequence, QFont, QActionGroup,
    QFontDatabase, QPainter)
from qfluentwidgets import (setTheme, setThemeColor, Theme,
    Action, MenuAnimationType, FluentIcon, setFontFamilies,
    TransparentDropDownPushButton, setCustomStyleSheet,
    MessageBox, MessageBoxBase, ColorDialog, SpinBox, ComboBox, LineEdit,
    BodyLabel, CaptionLabel, TitleLabel, isDarkTheme, RoundMenu, SwitchButton,
    PushButton, ScrollArea)
from qfluentwidgets.components.material import AcrylicMenu
from qfluentwidgets.components.material.acrylic_menu import (AcrylicMenuBase,
    AcrylicMenuActionListWidget)
from qfluentwidgets.components.widgets.menu import MenuActionListWidget, MenuAnimationManager

from core.app_paths import get_resource_dir
from core.perf import is_acrylic_enabled, is_animation_enabled
from workers.collect_worker import CollectFilesWorker, FileCopyWorker
from main_window.settings_dialog import SettingsDialog

# ==================== 版本信息（帮助→关于） ====================
# 仓库暂无 git tag，版本号按语义化手工维护：新增功能→小版本+1
# 2.4.0 之后新增：收集上传工作流、上传清单、打包上传字节进度、SNK 标识等
APP_VERSION = "2.5.0"
GITHUB_REPO = "https://github.com/abcd129064/autowork"

# 关于弹窗「链接」区
ABOUT_LINKS = [
    ("源代码 (GitHub)", GITHUB_REPO),
    ("问题反馈 (Issues)", f"{GITHUB_REPO}/issues"),
    ("更新日志 (Commits)", f"{GITHUB_REPO}/commits"),
]

# 关于弹窗「开源库」区（运行时依赖）
ABOUT_OSS_LIBS = [
    ("PySide6 (Qt for Python)", "https://www.qt.io/qt-for-python"),
    ("PyQt-Fluent-Widgets", "https://github.com/zhiyiYo/PyQt-Fluent-Widgets"),
    ("paramiko", "https://github.com/paramiko/paramiko"),
    ("darkdetect", "https://github.com/albertosottile/darkdetect"),
    ("PyInstaller", "https://github.com/pyinstaller/pyinstaller"),
]


class AboutDialog(MessageBoxBase):
    """关于弹窗：应用名/版本号 + GitHub 链接 + 开源依赖库清单

    链接用 QLabel 富文本 <a href> 实现（openExternalLinks，系统默认浏览器打开）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.titleLabel = TitleLabel("AutoWork", self)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(CaptionLabel(
            "用于视频播放、日志管理与数据记录的桌面自动化工具", self))
        self.viewLayout.addWidget(BodyLabel(
            f"版本: {APP_VERSION} for Windows (x64)", self))

        # 链接区
        self.viewLayout.addSpacing(8)
        self.viewLayout.addWidget(CaptionLabel("链接", self))
        links_lbl = QLabel(self._links_html(ABOUT_LINKS), self)
        links_lbl.setOpenExternalLinks(True)
        self.viewLayout.addWidget(links_lbl)

        # 开源库区
        self.viewLayout.addSpacing(6)
        self.viewLayout.addWidget(CaptionLabel("开源库", self))
        oss_lbl = QLabel(self._links_html(ABOUT_OSS_LIBS), self)
        oss_lbl.setOpenExternalLinks(True)
        oss_lbl.setWordWrap(True)
        self.viewLayout.addWidget(oss_lbl)

        self.viewLayout.addSpacing(8)
        self.viewLayout.addWidget(CaptionLabel(
            "© 2026 AutoWork · 基于 Qt/PySide6 等开源项目构建", self))

    @staticmethod
    def _links_html(links) -> str:
        """[(标题, url), ...] → 以竖线分隔的富文本链接串"""
        return "&nbsp;&nbsp;|&nbsp;&nbsp;".join(
            f'<a href="{url}">{title}</a>' for title, url in links)


def _patch_acrylic_exec(menu):
    """PySide6/Shiboken 会将实例级 menu.exec 解析到 C++ QMenu.exec()，
    导致 AcrylicMenuBase.exec()（截屏→模糊→绘制亚克力）永不执行。
    给实例绑定 Python 级 exec 属性可绕过此劫持。

    运行时动态判断：
    - 亚克力开 → AcrylicMenuBase.exec（截屏→模糊→绘制）
    - 亚克力关 → RoundMenu.exec（轻量纯色背景，零 GPU 开销）
    - 动画关 → MenuAnimationType.NONE
    切换开关后下一次弹出即生效，无需重启。"""
    def _exec(pos, ani=True, aniType=None):
        if aniType is None:
            aniType = (MenuAnimationType.DROP_DOWN if is_animation_enabled()
                       else MenuAnimationType.NONE)
        if not is_animation_enabled():
            # 无动画路径：绕过动画管理器，避免残留 mask / 视口不刷新
            mgr = MenuAnimationManager.make(menu, aniType)
            p = mgr._endPosition(pos)
            if is_acrylic_enabled():
                menu.view.acrylicBrush.grabImage(
                    QRect(p, menu.layout().sizeHint()))
            menu.clearMask()
            menu.move(p)
            menu.show()
            menu.view.viewport().update()
            if menu.isSubMenu:
                menu.menuItem.setSelected(True)
            return
        if is_acrylic_enabled():
            AcrylicMenuBase.exec(menu, pos, ani=ani, aniType=aniType)
        else:
            RoundMenu.exec(menu, pos, ani=ani, aniType=aniType)
    menu.exec = _exec


class _VisibleAcrylicView(AcrylicMenuActionListWidget):
    """增强亚克力菜单视图：优化模糊半径/噪点/着色层参数，使磨砂玻璃效果清晰可见。

    库默认参数（模糊半径35 + 噪点0.03 + 着色alpha 150/200）在浅色均匀背景上
    会把模糊结果洗成近乎纯色，看不出磨砂感；此处调整为：
    适度模糊保留背景轮廓 + 可见噪点纹理 + 更低着色不透明度。

    亚克力开关关闭时跳过 brush 绘制，直接使用普通菜单背景（即时生效）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # 库默认模糊半径 35 过强，背景内容糊成均匀色；15 保留更多背景细节，
        # 让磨砂层能透出背后内容的明暗变化
        self.acrylicBrush.setBlurRadius(15)
        # 库默认噪点不透明度 0.03 几乎不可见；实际值 0.03 保持默认，
        # 若调高到 0.15 可呈现亚克力标志性颗粒感，但当前选择保留默认
        self.acrylicBrush.noiseOpacity = 0.03

    def paintEvent(self, e):
        if not is_acrylic_enabled():
            # 亚克力关闭：绘制纯色背景替代截屏模糊（transparent 属性使
            # QSS 背景透明，不手动填充会导致菜单整体不可见）
            painter = QPainter(self.viewport())
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(43, 43, 43) if isDarkTheme()
                             else QColor(243, 243, 243))
            painter.drawRect(self.viewport().rect())
            MenuActionListWidget.paintEvent(self, e)
            return
        super().paintEvent(e)

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


def _create_menu(title="", parent=None):
    """工厂函数：统一创建亚克力菜单实例。
    亚克力/动画开关在弹出时动态判断（_patch_acrylic_exec），
    因此无需根据配置选择不同控件类型，切换即时生效。"""
    return VisibleAcrylicMenu(title, parent)


def _exec_menu(menu, global_pos):
    """统一菜单弹出：亚克力/动画行为由实例级 exec 动态决定"""
    menu.exec(global_pos)


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
        """为列表控件设置自定义右键菜单策略，并预构建缓存菜单（避免每次右键重建）"""
        self.ui.id_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.ui.log_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.ui.local_video_list.setContextMenuPolicy(Qt.CustomContextMenu)
        # 预构建三个右键菜单（Action/图标/信号仅创建一次，后续右键直接弹出）
        self._build_id_list_menu()
        self._build_log_list_menu()
        self._build_video_list_menu()

    # ---------- 预构建菜单（初始化时一次性创建，右键时零开销弹出） ----------

    def _build_id_list_menu(self):
        """预构建：设备列表右键菜单（完全静态，可安全缓存复用）"""
        menu = _create_menu(parent=self)
        action_open_dir = Action(FluentIcon.FOLDER, "打开目录", self)
        action_cpp_log = Action(FluentIcon.DOCUMENT, "查看 CPP 日志", self)
        action_open_dir.triggered.connect(self.on_open_dir_clicked)
        action_cpp_log.triggered.connect(self.on_open_daily_clicked)
        menu.addAction(action_open_dir)
        menu.addAction(action_cpp_log)
        self._ctx_id_menu = menu

    def _build_log_list_menu(self):
        """预构建：日志内容列表右键菜单（触发时动态获取当前行）"""
        import re
        menu = _create_menu(parent=self)
        action_copy = Action(FluentIcon.COPY, "复制此行", self)
        action_copy_frame = Action(FluentIcon.LIBRARY, "复制帧数", self)
        action_locate = Action(FluentIcon.PEOPLE, "在文件管理器中定位", self)

        def _do_copy():
            item = self.ui.log_list.currentItem()
            if not item:
                return
            QApplication.clipboard().setText(item.text())
            self._show_status_message("已复制到剪贴板", 2000)
            self._append_log("[复制] 已复制当前行文本到剪贴板")

        def _do_copy_frame():
            item = self.ui.log_list.currentItem()
            if not item:
                return
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

        action_add_upload_log = Action(FluentIcon.SEND, "添加到上传目录", self)
        action_add_upload_log.triggered.connect(
            lambda: self._add_file_to_upload(self._current_log_path))

        action_copy.triggered.connect(_do_copy)
        action_copy_frame.triggered.connect(_do_copy_frame)
        action_locate.triggered.connect(_do_locate)
        menu.addAction(action_copy)
        menu.addAction(action_copy_frame)
        menu.addSeparator()
        menu.addAction(action_locate)
        menu.addAction(action_add_upload_log)
        self._ctx_log_menu = menu

    def _build_video_list_menu(self):
        """预构建：日志文件列表右键菜单（触发时动态获取当前项）"""
        menu = _create_menu(parent=self)
        action_copy_name = Action(FluentIcon.COPY, "复制视频名", self)
        action_open_video_loc = Action(FluentIcon.FOLDER, "打开视频位置", self)
        action_copy_video_loc = Action(FluentIcon.LINK, "复制视频位置", self)

        def _do_copy_name():
            item = self.ui.local_video_list.currentItem()
            if not item:
                return
            pure_name = os.path.splitext(item.text())[0]
            QApplication.clipboard().setText(pure_name)
            self._show_status_message(f"文件名 {pure_name} 已复制到剪贴板", 2000)
            self._append_log(f"[复制] 文件名 {pure_name} 已复制到剪贴板")

        def _resolve_video_path():
            """根据当前选中的日志文件名推断视频完整路径"""
            item = self.ui.local_video_list.currentItem()
            if not item:
                return None
            log_filename = item.text()
            video_name = os.path.splitext(log_filename)[0] + '.mp4'
            video_path_primary = os.path.join(self.videos_dir, "videos", video_name)
            if os.path.exists(video_path_primary):
                return video_path_primary
            current_device = self.ui.id_list.currentItem()
            if current_device:
                device_code = current_device.text()
                video_path_device = os.path.join(self.videos_dir, device_code, video_name)
                if os.path.exists(video_path_device):
                    return video_path_device
            return video_path_primary

        def _do_open_video_loc():
            video_path = _resolve_video_path()
            if not video_path:
                return
            if os.path.exists(video_path):
                subprocess.run(['explorer', '/select,', video_path])
                self._append_log(f"[定位] 已定位视频: {video_path}")
            else:
                video_dir = os.path.dirname(video_path)
                if os.path.exists(video_dir):
                    os.startfile(video_dir)
                    self._append_log(f"[定位] 视频不存在，已打开目录: {video_dir}")
                else:
                    self._append_log(f"[提示] 视频路径不存在: {video_path}")

        def _do_copy_video_loc():
            video_path = _resolve_video_path()
            if not video_path:
                return
            QApplication.clipboard().setText(video_path)
            self._show_status_message("视频路径已复制到剪贴板", 2000)
            self._append_log(f"[复制] 视频路径: {video_path}")

        def _do_add_to_upload():
            """第二列右键：将当前日志文件（及对应视频）添加到上传目录"""
            item = self.ui.local_video_list.currentItem()
            if not item:
                return
            log_filename = item.text()
            device_item = self.ui.id_list.currentItem()
            if not device_item:
                self._show_info_bar("请先选择设备", "warning")
                return
            device_code = device_item.text()
            # 重建完整路径：日期子目录优先，其次设备根目录
            date_str = self._get_selected_date_str()
            full_path = os.path.join(
                self.videos_dir, device_code, date_str, log_filename)
            if not os.path.exists(full_path):
                alt_path = os.path.join(self.videos_dir, device_code, log_filename)
                if os.path.exists(alt_path):
                    full_path = alt_path
            self._add_file_to_upload(full_path)

        action_add_upload = Action(FluentIcon.SEND, "添加到上传目录", self)
        action_add_upload.triggered.connect(_do_add_to_upload)

        action_copy_name.triggered.connect(_do_copy_name)
        action_open_video_loc.triggered.connect(_do_open_video_loc)
        action_copy_video_loc.triggered.connect(_do_copy_video_loc)
        menu.addAction(action_copy_name)
        menu.addSeparator()
        menu.addAction(action_open_video_loc)
        menu.addAction(action_copy_video_loc)
        menu.addSeparator()
        menu.addAction(action_add_upload)
        self._ctx_video_menu = menu

    # ---------- 右键触发：直接弹出缓存菜单（零构建开销） ----------

    def _id_list_context_menu(self, pos):
        """设备列表右键菜单（预构建缓存，直接弹出）"""
        _exec_menu(self._ctx_id_menu, self.ui.id_list.mapToGlobal(pos))

    def _log_list_context_menu(self, pos):
        """日志内容列表右键菜单（预构建缓存，直接弹出）"""
        if self.ui.log_list.currentItem() is None:
            return
        _exec_menu(self._ctx_log_menu, self.ui.log_list.mapToGlobal(pos))

    def _local_video_list_context_menu(self, pos):
        """日志文件列表右键菜单（预构建缓存，直接弹出）"""
        if self.ui.local_video_list.currentItem() is None:
            return
        _exec_menu(self._ctx_video_menu, self.ui.local_video_list.mapToGlobal(pos))

    # ---------- 添加到上传目录（收集到 {videos_dir}/upload/{设备}/） ----------

    def _add_file_to_upload(self, file_path):
        """将指定日志/文本文件添加到上传收集目录

        .log 文件走 CollectFilesWorker：连带收集对应视频、detect.bin、
        CPP 日志（daily_*.txt），缺失项完成后警告提示；
        .txt 等其他文件直接复制文件本身（已存在跳过）。
        """
        if not file_path or not os.path.isfile(file_path):
            self._append_log("[上传] 文件不存在或未被选中，无法添加到上传目录")
            self._show_info_bar("文件不存在，无法添加到上传目录", "warning")
            return
        videos_dir = getattr(self, "videos_dir", "") or ""
        if not videos_dir or not os.path.isdir(videos_dir):
            self._show_info_bar("videos_dir 未配置或目录不存在", "warning")
            return
        device_item = self.ui.id_list.currentItem()
        device_code = device_item.text().strip() if device_item else ""
        if not device_code or not os.path.isdir(os.path.join(videos_dir, device_code)):
            self._show_info_bar(f"设备目录不存在，无法收集: {device_code or '未选择设备'}",
                                "warning")
            return

        fname = os.path.basename(file_path)
        if fname.lower().endswith(".log"):
            base = os.path.splitext(fname)[0]
            worker = CollectFilesWorker(videos_dir, device_code, [base])
            worker.done.connect(
                lambda dev, n, miss, w=worker: self._on_upload_collect_done(dev, n, miss, w))
            worker.error.connect(
                lambda msg: self._show_info_bar(msg.split(chr(10))[0], "error", duration=4000))
            if not hasattr(self, "_upload_collect_workers"):
                self._upload_collect_workers = []
            self._upload_collect_workers.append(worker)
            worker.start()
            self._append_log(f"[上传] 正在收集 {device_code}: {base} 的视频/日志...")
            return

        # .txt 等其他文件：异步复制文件本身到 upload/{设备}/
        upload_dir = os.path.join(videos_dir, "upload", device_code)
        os.makedirs(upload_dir, exist_ok=True)
        dst = os.path.join(upload_dir, fname)
        if os.path.exists(dst):
            self._show_info_bar(f"{fname} 已在上传目录中（跳过）", "info")
            self._append_log(f"[上传] 已存在跳过: {dst}")
        else:
            worker = FileCopyWorker(file_path, dst)
            def _on_copy_ok(w=worker, d=dst, n=fname):
                if hasattr(self, '_file_copy_workers') and w in self._file_copy_workers:
                    self._file_copy_workers.remove(w)
                self._show_info_bar(f"{n} 已添加到上传目录", "success")
                self._append_log(f"[上传] 已收集: {d}")
            def _on_copy_err(err, w=worker, n=fname):
                if hasattr(self, '_file_copy_workers') and w in self._file_copy_workers:
                    self._file_copy_workers.remove(w)
                self._append_log(f"[上传] 收集失败: {err}")
                self._show_info_bar(f"添加到上传目录失败: {err}", "error", duration=4000)
            worker.copy_finished.connect(_on_copy_ok)
            worker.error.connect(_on_copy_err)
            if not hasattr(self, '_file_copy_workers'):
                self._file_copy_workers = []
            self._file_copy_workers.append(worker)
            worker.start()

    def _on_upload_collect_done(self, device_id, copied, missing, worker):
        """右键收集完成：复制数 + 缺失项警告（视频/日志未找到时明确提示）"""
        workers = getattr(self, "_upload_collect_workers", [])
        if worker in workers:
            workers.remove(worker)
        if missing:
            shown = ", ".join(missing[:3]) + (" ..." if len(missing) > 3 else "")
            self._append_log(
                f"[上传] {device_id} 收集完成: 复制 {copied} 个，缺失 {len(missing)} 个: {shown}")
            self._show_info_bar(
                f"{device_id}: 复制 {copied} 个，缺失 {len(missing)} 个: {shown}",
                "warning", duration=4000)
        else:
            self._append_log(f"[上传] {device_id} 收集完成: 复制 {copied} 个文件到 upload 目录")
            self._show_info_bar(
                f"{device_id}: {copied} 个文件已添加到上传目录", "success")

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
        """初始化顶部菜单栏（Fluent 风格：TransparentDropDownPushButton + AcrylicMenu）"""
        self._menubar_widget = QWidget()
        self._menubar_widget.setObjectName(u"menubar_widget")
        self._menubar_widget.setFixedHeight(26)
        _mb_layout = QHBoxLayout(self._menubar_widget)
        _mb_layout.setContentsMargins(6, 0, 6, 0)
        _mb_layout.setSpacing(2)

        # 「功能」菜单
        func_menu = _create_menu("功能", self)
        act_settings = Action(FluentIcon.SETTING, "设置", self)
        act_settings.triggered.connect(lambda: QTimer.singleShot(0, self._on_open_settings))
        func_menu.addAction(act_settings)
        act_upload_list = Action(FluentIcon.LIBRARY, "上传清单", self)
        act_upload_list.setToolTip("查看已收集待上传的文件（视频/日志目录/upload）")
        act_upload_list.triggered.connect(lambda: QTimer.singleShot(0, self._on_show_upload_list))
        func_menu.addAction(act_upload_list)
        func_menu.addSeparator()
        act_sc = Action(FluentIcon.EDIT, "修改快捷键", self)
        act_sc.triggered.connect(lambda: QTimer.singleShot(0, self._on_modify_shortcuts))
        act_hc = Action(FluentIcon.HIGHTLIGHT, "高亮颜色设置", self)
        act_hc.triggered.connect(lambda: QTimer.singleShot(0, self._on_highlight_color))
        func_menu.addAction(act_sc)
        func_menu.addAction(act_hc)
        func_btn = TransparentDropDownPushButton("功能", self._menubar_widget)
        func_btn.setMenu(func_menu)
        _mb_layout.addWidget(func_btn)

        # 「视图」菜单
        view_menu = _create_menu("视图", self)
        settings = self._load_settings()

        # 布局子菜单
        layout_menu = _create_menu("布局", self)
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
        theme_menu = _create_menu("主题", self)
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

        # 性能选项（亚克力/动画独立开关，即时生效无需重启）
        view_menu.addSeparator()
        act_perf = Action(FluentIcon.SPEED_HIGH, "性能选项...", self)
        act_perf.triggered.connect(lambda: QTimer.singleShot(0, self._on_perf_options))
        view_menu.addAction(act_perf)

        view_btn = TransparentDropDownPushButton("视图", self._menubar_widget)
        view_btn.setMenu(view_menu)
        _mb_layout.addWidget(view_btn)

        # 「帮助」菜单
        help_menu = _create_menu("帮助", self)
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

    def _on_add_table_record(self):
        """手动添加球桌记录（API 失效时的兜底录入入口）"""
        from windows.table_panel import AddRecordDialog
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

    def _on_open_table_panel(self):
        """打开球桌管理面板（非模态独立窗口）"""
        from windows.management_panel import ManagementPanelWindow
        if not hasattr(self, '_table_panel') or self._table_panel is None:
            # 不传 parent：避免成为主窗口的 owned window 而始终盖在主窗口之上（始终置顶）
            self._table_panel = ManagementPanelWindow()
            self._table_panel.destroyed.connect(
                lambda: setattr(self, '_table_panel', None))
        self._table_panel.show()
        self._table_panel.raise_()
        self._table_panel.activateWindow()

    def _on_open_settings(self):
        """统一设置面板：分组展示所有可配置项，支持路径浏览、即时编辑"""
        settings = self._load_settings()

        dlg = SettingsDialog(self, settings)
        dlg.yesButton.setText("保存")
        dlg.cancelButton.setText("取消")
        dlg.widget.setMinimumWidth(520)
        if dlg.exec():
            new_data = dlg.collect()
            old_dpi = settings.get("dpi_scale", 100)
            old_font_size = settings.get("font_size", 11)
            old_font_family = settings.get("font_family", "")
            self._save_settings(new_data)
            self._reload_settings_cache()
            self._load_paths()
            # 外观变更即时应用
            if new_data.get("font_size") != old_font_size or new_data.get("font_family") != old_font_family:
                self._apply_global_font()
            if new_data.get("dpi_scale") != old_dpi:
                self._show_info_bar("缩放已修改，重启后生效")
            self._append_log("[配置] 设置面板已保存")
            self._show_info_bar("设置已保存")

    def _on_about(self):
        """显示关于对话框（版本号随 git 发布手工递增，含 GitHub 与开源库链接）"""
        dlg = AboutDialog(self)
        dlg.yesButton.setText("确定")
        dlg.cancelButton.hide()
        dlg.widget.setMinimumWidth(560)
        dlg.exec()

    def _on_perf_options(self):
        """性能选项对话框：SwitchButton 独立控制亚克力/动画，切换即时生效无需重启"""
        from core.perf import (is_acrylic_enabled, is_animation_enabled,
                               set_acrylic_enabled, set_animation_enabled)

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

                # 弹出动画开关
                row2 = QHBoxLayout()
                lbl2 = BodyLabel("菜单弹出动画", self)
                lbl2.setToolTip("关闭后菜单直接弹出，无过渡动画")
                row2.addWidget(lbl2, 1)
                self.sw_animation = SwitchButton(self)
                self.sw_animation.setOnText("开")
                self.sw_animation.setOffText("关")
                self.sw_animation.setChecked(is_animation_enabled())
                row2.addWidget(self.sw_animation)
                self.viewLayout.addLayout(row2)

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
            self._append_log(f"[性能] 弹出动画已{state}（即时生效）")

        dlg.sw_acrylic.checkedChanged.connect(_on_acrylic_toggled)
        dlg.sw_animation.checkedChanged.connect(_on_animation_toggled)
        dlg.exec()

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
