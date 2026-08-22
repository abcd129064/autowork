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
    QTabWidget)
from PySide6.QtCore import (Qt, QItemSelectionModel, QDate, QPoint, QPropertyAnimation,
    QEasingCurve, QTimer, QEvent, QThread, Signal, QRectF, QSize, QDateTime)
from PySide6.QtGui import (QColor, QShortcut, QKeySequence, QPalette, QCursor,
    QPainter, QPen, QFont, QFontMetrics, QBrush)
from qfluentwidgets import (TableWidget, SearchLineEdit, PushButton,
    PrimaryPushButton, ToolButton, FluentIcon, ComboBox, RoundMenu, CheckBox,
    Action, TransparentDropDownPushButton, LineEdit, PlainTextEdit,
    FluentWindow, NavigationItemPosition, ProgressBar, TitleLabel,
    BodyLabel, CaptionLabel, CalendarPicker, PasswordLineEdit, ScrollArea,
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
from windows.moyu_widgets import Game2048Widget, SnakeWidget, MoyuReaderWidget
from windows.image_viewer import is_image_file

logger = logging.getLogger(__name__)

from windows.management.common import *  # noqa: F401,F403

class GamePage(QWidget):
    """摸鱼中心：小说/2048 页签容器（贪吃蛇入口暂隐藏）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        area = ScrollArea(self)
        area.setWidgetResizable(True)
        area.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        box = QVBoxLayout(container)
        box.setContentsMargins(10, 6, 10, 10)

        self.tabs = QTabWidget(container)
        self.tabs.setDocumentMode(True)
        self.game_2048 = Game2048Widget(self.tabs)
        # 隐藏「贪吃蛇」入口：实例和页签一并注释，恢复时取消下方两行即可
        # self.game_snake = SnakeWidget(self.tabs)
        # self.tabs.addTab(self.game_snake, "贪吃蛇")
        self.reader = MoyuReaderWidget(self.tabs)
        self.tabs.addTab(self.reader, "小说阅读")
        self.tabs.addTab(self.game_2048, "2048")
        box.addWidget(self.tabs)

        area.setWidget(container)
        layout.addWidget(area)
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, index):
        """页签切换：游戏控件取键盘焦点；贪吃蛇切走暂停、切回恢复"""
        current = self.tabs.widget(index)
        snake = getattr(self, "game_snake", None)
        if snake is not None:
            if current is snake:
                snake.auto_resume()
            else:
                snake.auto_pause()
        if current is not self.reader:
            current.setFocus()
