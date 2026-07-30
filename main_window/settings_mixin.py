# -*- coding: utf-8 -*-
"""MainWindow 设置管理 Mixin：配置读写、路径加载、快捷键配置"""

import os
import json

from PySide6.QtCore import QTimer
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import QLineEdit

from core.app_paths import get_app_dir


class SettingsMixin:
    """设置管理相关方法"""

    # 默认路径配置，首次运行时自动写入 settings.json
    DEFAULT_PATHS = {
        "exe_dir": r"C:\Users\shen_zhe\Desktop\snooker\bin64",
        "videos_dir": r"C:\Users\shen_zhe\Desktop\videos",
        "cipher_tool": r"C:\Users\shen_zhe\Desktop\videos\AESBase64CipherTool.exe",
        "front_exe": r"C:\Users\shen_zhe\Desktop\snooker\win-unpacked\SnookerNewbvMaster.exe",
        "backend_exe": r"C:\Users\shen_zhe\Desktop\snooker\backend\SnookerServer.exe",
    }
    # 默认快捷键配置
    DEFAULT_SHORTCUTS = {
        "shortcut_flush": "F5",
        "shortcut_start": "Space",
        "shortcut_open_dir": "Ctrl+O",
        "shortcut_pause": "P",
        "shortcut_focus_frame": "Ctrl+G",
        "shortcut_start_three": "Ctrl+T",
        "shortcut_open_daily": "Ctrl+L",
        "shortcut_open_config": "Ctrl+,",
        "shortcut_p2p_panel": "F9",
    }
    # 默认高亮颜色（橙色）
    DEFAULT_HIGHLIGHT_COLOR = [220, 80, 20]

    @staticmethod
    def _get_app_dir():
        """获取应用程序所在目录（兼容 PyInstaller 打包后的路径）"""
        return get_app_dir()

    def _get_settings_path(self):
        """获取配置文件路径，与 main.py / .exe 同目录"""
        return os.path.join(self._get_app_dir(), "settings.json")

    def _reload_settings_cache(self):
        """从 settings.json 一次性加载到内存缓存"""
        path = self._get_settings_path()
        self._settings_cache = dict(self.DEFAULT_PATHS)  # 默认值作为基础
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    self._settings_cache.update(json.load(f))
            except Exception:
                pass

    def _load_settings(self):
        """返回缓存的配置（不再读磁盘，如需刷新请调用 _reload_settings_cache）"""
        if not hasattr(self, '_settings_cache'):
            self._reload_settings_cache()
        return self._settings_cache

    def _save_settings(self, data):
        """将配置写入 settings.json，同时更新内存缓存"""
        path = self._get_settings_path()
        try:
            self._load_settings()  # 确保缓存已初始化
            self._settings_cache.update(data)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self._settings_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._append_log(f"[警告] 保存配置失败: {e}")

    def _load_paths(self):
        """从配置加载路径，并设置实例属性"""
        settings = self._load_settings()
        self.exe_dir = settings.get("exe_dir", self.DEFAULT_PATHS["exe_dir"])
        self.videos_dir = settings.get("videos_dir", self.DEFAULT_PATHS["videos_dir"])
        self.cipher_tool = settings.get("cipher_tool", self.DEFAULT_PATHS["cipher_tool"])
        self.front_exe = settings.get("front_exe", self.DEFAULT_PATHS["front_exe"])
        self.backend_exe = settings.get("backend_exe", self.DEFAULT_PATHS["backend_exe"])
        # 确保首次运行时将默认路径写入 settings.json
        if not os.path.exists(self._get_settings_path()):
            self._save_settings(self.DEFAULT_PATHS)

    def _restore_exe_selection(self):
        """从配置文件恢复上次选择的程序"""
        settings = self._load_settings()
        saved_exe = settings.get("last_exe", "")
        if saved_exe:
            for i in range(self.ui.choose_exe.count()):
                if self.ui.choose_exe.itemText(i) == saved_exe:
                    self.ui.choose_exe.setCurrentIndex(i)
                    self._append_log(f"[配置] 已恢复上次程序: {saved_exe}")
                    return

    # ==================== 快捷键 ====================

    def _get_shortcut_settings(self):
        """从 settings.json 获取快捷键配置，缺失字段用默认值"""
        settings = self._load_settings()
        return {
            "shortcut_flush": settings.get("shortcut_flush", self.DEFAULT_SHORTCUTS["shortcut_flush"]),
            "shortcut_start": settings.get("shortcut_start", self.DEFAULT_SHORTCUTS["shortcut_start"]),
            "shortcut_open_dir": settings.get("shortcut_open_dir", self.DEFAULT_SHORTCUTS["shortcut_open_dir"]),
            "shortcut_pause": settings.get("shortcut_pause", self.DEFAULT_SHORTCUTS["shortcut_pause"]),
            "shortcut_focus_frame": settings.get("shortcut_focus_frame", self.DEFAULT_SHORTCUTS["shortcut_focus_frame"]),
            "shortcut_start_three": settings.get("shortcut_start_three", self.DEFAULT_SHORTCUTS["shortcut_start_three"]),
            "shortcut_open_daily": settings.get("shortcut_open_daily", self.DEFAULT_SHORTCUTS["shortcut_open_daily"]),
            "shortcut_open_config": settings.get("shortcut_open_config", self.DEFAULT_SHORTCUTS["shortcut_open_config"]),
            "shortcut_p2p_panel": settings.get("shortcut_p2p_panel", self.DEFAULT_SHORTCUTS["shortcut_p2p_panel"]),
        }

    def _init_shortcuts(self):
        """绑定全局快捷键（从 settings.json 读取配置）"""
        # 清除旧快捷键引用
        self._shortcuts = []
        sc = self._get_shortcut_settings()
        # 刷新
        s1 = QShortcut(QKeySequence(sc["shortcut_flush"]), self)
        s1.activated.connect(self.on_flush_clicked)
        self._shortcuts.append(s1)
        # 打开目录
        s2 = QShortcut(QKeySequence(sc["shortcut_open_dir"]), self)
        s2.activated.connect(self.on_open_dir_clicked)
        self._shortcuts.append(s2)
        # 空格切换播放/结束
        self._space_shortcut = QShortcut(QKeySequence(sc["shortcut_start"]), self)
        self._space_shortcut.activated.connect(self._on_space_pressed)
        self._shortcuts.append(self._space_shortcut)
        # 暂停/恢复
        s_pause = QShortcut(QKeySequence(sc["shortcut_pause"]), self)
        s_pause.activated.connect(self._on_pause_shortcut)
        self._shortcuts.append(s_pause)
        # 聚焦帧数输入框
        s_frame = QShortcut(QKeySequence(sc["shortcut_focus_frame"]), self)
        s_frame.activated.connect(self._on_focus_frame_shortcut)
        self._shortcuts.append(s_frame)
        # 启动三端
        s_three = QShortcut(QKeySequence(sc["shortcut_start_three"]), self)
        s_three.activated.connect(self.on_start_three_clicked)
        self._shortcuts.append(s_three)
        # 查看 CPP 日志
        s_daily = QShortcut(QKeySequence(sc["shortcut_open_daily"]), self)
        s_daily.activated.connect(self.on_open_daily_clicked)
        self._shortcuts.append(s_daily)
        # 打开配置文件（弹模态对话框，用 singleShot 避免事件循环冲突）
        s_config = QShortcut(QKeySequence(sc["shortcut_open_config"]), self)
        s_config.activated.connect(lambda: QTimer.singleShot(0, self.on_open_config_clicked))
        self._shortcuts.append(s_config)
        # 切换 P2P 远程面板
        s_p2p = QShortcut(QKeySequence(sc["shortcut_p2p_panel"]), self)
        s_p2p.activated.connect(self.ui.p2p_btn.toggle)
        self._shortcuts.append(s_p2p)

    def _on_pause_shortcut(self):
        """暂停/恢复快捷键：焦点在输入框时不触发，避免打字误触"""
        if isinstance(self.focusWidget(), QLineEdit):
            return
        self._on_pause_clicked()

    def _on_focus_frame_shortcut(self):
        """聚焦帧数输入框并全选，便于直接输入新帧号（自动切换为自定义模式，记住原焦点位置，回车可恢复）"""
        w = self.focusWidget()
        if w is not self.ui.input_frame:
            self._frame_prev_focus = w
        self.ui.input_frame_custom.setChecked(True)
        self.ui.input_frame.setFocus()
        self.ui.input_frame.selectAll()

    def _on_frame_input_confirmed(self):
        """帧数输入框按回车：恢复焦点到之前的位置，使空格可直接播放"""
        prev = getattr(self, '_frame_prev_focus', None)
        if prev is not None and prev is not self.ui.input_frame and prev.isVisible():
            prev.setFocus()
        else:
            self.ui.input_frame.clearFocus()
        self._frame_prev_focus = None

    def _on_space_pressed(self):
        """空格键切换播放/结束，焦点在输入框时不触发"""
        if self.focusWidget() is self.ui.input_frame:
            return
        self.on_start_clicked()
