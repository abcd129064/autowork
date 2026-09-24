# -*- coding: utf-8 -*-
"""moyu_page 模块（从 windows/management_panel.py 拆出，逻辑未改动）"""

import csv
import difflib
import json
import logging
import math
import os
import re
import shutil
import subprocess
from datetime import datetime

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QLabel, QApplication,
    QTextEdit, QDialog, QPushButton, QCheckBox, QTextBrowser, QTreeWidgetItem,
    QFileDialog, QToolTip, QFrame, QListWidget, QListWidgetItem, QAbstractScrollArea,
    QStackedWidget)
from PySide6.QtCore import (Qt, QItemSelectionModel, QDate, QPoint, QPropertyAnimation,
    QEasingCurve, QTimer, QEvent, QThread, Signal, QRectF, QSize, QDateTime)
from PySide6.QtGui import (QColor, QShortcut, QKeySequence, QPalette, QCursor,
    QPainter, QPen, QFont, QFontMetrics, QBrush)
from qfluentwidgets import (TableWidget, SearchLineEdit, PushButton,
    PrimaryPushButton, ToolButton, FluentIcon, ComboBox, RoundMenu, CheckBox,
    Action, TransparentDropDownPushButton, LineEdit, PlainTextEdit,
    FluentWindow, NavigationItemPosition, ProgressBar, TitleLabel,
    BodyLabel, CaptionLabel, CalendarPicker, Pivot, PasswordLineEdit, ScrollArea,
    CardWidget, setCustomStyleSheet, qconfig, isDarkTheme, MessageBox, TreeWidget,
    MessageBoxBase, MenuAnimationType, SwitchButton)
from qfluentwidgets.components.widgets.table_view import TableItemDelegate

from core.app_paths import get_app_dir
from core.design_tokens import SEMANTIC
from core.frp_remote import get_session_manager
from core.perf import is_acrylic_enabled, is_animation_enabled
from core.secrets import decrypt_settings, encrypt_settings
from core.utils import launch_sibling_app, show_info_bar
from workers.table_worker import (TableFetchWorker, DevicesFetchWorker,
                                  SnookerOmFetchWorker, MigrateImageWorker,
                                  LoginTestWorker, get_active_api_source,
                                  CATEGORY_DIRS)
from workers.collect_worker import (CollectFilesWorker, ZipUploadWorker,
                                    clip_base_name, date_from_base,
                                    resolve_device_dir,
                                    fuzzy_match_device_dir, norm_device_suffix)
from database import table_db
from windows.mysql_sync_card import MysqlSyncCard
from windows.management.moyu_widgets import (Game2048Widget, SnakeWidget,
                                                  MinesweeperWidget,
                                                  MoyuReaderWidget)
from windows.management.image_viewer import is_image_file

logger = logging.getLogger(__name__)

from windows.management.common import *  # noqa: F401,F403

class GamePage(QWidget):
    """摸鱼中心：小说/2048/贪吃蛇/扫雷 页签容器"""

    # (routeKey, 页签标题, 控件属性名)，顺序即 Pivot 与堆栈的展示顺序
    _TABS = (("reader", "小说阅读", "reader"),
             ("2048", "2048", "game_2048"),
             ("snake", "贪吃蛇", "game_snake"),
             ("mines", "扫雷", "game_mines"))

    def __init__(self, parent=None):
        super().__init__(parent)
        # 懒构建（2026-09-25）：打开面板时只建壳，首次显示（切到本页）才装配
        # 4 个游戏。GamePage 全建 38ms，占 ManagementPanelWindow 构造 96ms 的
        # ~40%，且是构造期最大可削减项；与 DevicePage/AdminSettingsPage 的
        # _lazy_built 范式一致。游戏无跨页状态依赖，可安全延迟
        self._lazy_built = False
        self._pages = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

    def showEvent(self, event):
        """首次显示时才装配 Pivot + 4 游戏堆栈（懒构建）"""
        super().showEvent(event)
        if not self._lazy_built:
            self._lazy_built = True
            self._build_content()

    def _build_content(self):
        """原 __init__ 装配逻辑：ScrollArea + Pivot + 游戏堆栈"""
        layout = self.layout()

        area = ScrollArea(self)
        area.setWidgetResizable(True)
        area.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        box = QVBoxLayout(container)
        box.setContentsMargins(10, 6, 10, 10)

        # 页面切换用 Pivot + QStackedWidget 替换原生 QTabWidget（与组件测试页 TestPage 一致）
        # 注：游戏控件必须真正 addWidget 进堆栈，只建实例不入栈会被叠着画出来
        self.pivot = Pivot(container)
        self.stack = QStackedWidget(container)
        self.reader = MoyuReaderWidget(self.stack)
        self.game_2048 = Game2048Widget(self.stack)
        self.game_snake = SnakeWidget(self.stack)
        self.game_mines = MinesweeperWidget(self.stack)
        for key, title, attr in self._TABS:
            page = getattr(self, attr)
            self._pages[key] = page
            self.pivot.addItem(routeKey=key, text=title,
                               onClick=lambda *_, k=key: self._switch_game(k))
            self.stack.addWidget(page)
        self.pivot.setCurrentItem("reader")
        box.addWidget(self.pivot, 0, Qt.AlignmentFlag.AlignLeft)
        box.addWidget(self.stack, 1)

        area.setWidget(container)
        layout.addWidget(area)
        self.stack.currentChanged.connect(self._on_tab_changed)

    def _switch_game(self, key):
        """Pivot 页签切换：同步高亮与内容堆栈（懒构建前空表无页可切）"""
        page = self._pages.get(key)
        if page is None:
            return
        self.pivot.setCurrentItem(key)
        self.stack.setCurrentWidget(page)

    def _on_tab_changed(self, index):
        """页签切换：游戏控件取键盘焦点；贪吃蛇/扫雷切走暂停、切回恢复"""
        current = self.stack.widget(index)
        for game in (self.game_snake, self.game_mines):
            if current is game:
                game.auto_resume()
            else:
                game.auto_pause()
        if current is not self.reader:
            current.setFocus()
