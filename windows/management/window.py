# -*- coding: utf-8 -*-
"""window 模块（从 windows/management_panel.py 拆出，逻辑未改动）"""

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
from windows.management.moyu_widgets import (Game2048Widget, SnakeWidget,
                                                  MoyuReaderWidget)
from windows.management.image_viewer import is_image_file

logger = logging.getLogger(__name__)

from windows.management.common import *  # noqa: F401,F403
from windows.management.table_page import TablePage
from windows.management.device_page import FileListPanel, DevicePage
from windows.management.settings_page import AdminSettingsPage
from windows.management.health_page import TrendPage, HealthPage
from windows.management.moyu_page import GamePage

# ==================== 主窗口 ====================

class ManagementPanelWindow(FluentWindow):
    """运维管理面板：FluentWindow + 左侧导航 + 六个功能页面（球桌/设备/健康度/趋势/设置/小游戏）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("运维管理面板")
        self.resize(1150, 680)
        self.setMinimumSize(900, 520)

        # 创建子页面
        self.table_page = TablePage(self)
        self.table_page.setObjectName("tablePage")
        self.device_page = DevicePage(self)
        self.device_page.setObjectName("devicePage")
        self.trend_page = TrendPage(self)
        self.trend_page.setObjectName("trendPage")
        self.health_page = HealthPage(self)
        self.health_page.setObjectName("healthPage")
        self.game_page = GamePage(self)
        self.game_page.setObjectName("gamePage")
        self.settings_page = AdminSettingsPage(self)
        self.settings_page.setObjectName("adminSettingsPage")

        # 注册导航
        self.addSubInterface(self.table_page, FluentIcon.LIBRARY, "球桌管理")
        self.addSubInterface(self.device_page, FluentIcon.IOT, "设备状态")
        # 隐藏「健康趋势」页导航入口，恢复时取消下行注释即可
        # （TrendPage 实例仍会构建但无导航入口，不会被展示；closeEvent 清理不受影响）
        # self.addSubInterface(self.trend_page, FluentIcon.PIE_SINGLE, "健康趋势")
        self.addSubInterface(self.health_page, FluentIcon.PIE_SINGLE, "设备健康度管理")
        self.addSubInterface(self.settings_page, FluentIcon.SETTING, "管理设置")
        self.addSubInterface(self.game_page, FluentIcon.GAME, "小游戏")

        # 导航亚克力与「性能选项」联动：关闭 perf_acrylic 后不再强制开启，
        # 避免关闭菜单亚克力后导航栏仍有额外核显消耗
        self.navigationInterface.setAcrylicEnabled(is_acrylic_enabled())
        self.navigationInterface.setCurrentItem(self.table_page.objectName())

        # 远程会话中心：设备状态页右键菜单按 snk 建立 frp xtcp 隧道并打开会话
        # （全局单例，与主窗口远程面板/球桌面板共享同一 frpc 进程）
        self._remote_bridge = get_session_manager()

    def showEvent(self, event):
        super().showEvent(event)
        self._reset_titlebar_button_state()

    def _reset_titlebar_button_state(self):
        """重置标题栏按钮状态，修复关闭按钮卡在 PRESSED 导致窗口无法拖动。

        qframelesswindow 的 TitleBarButton 仅在 mousePressEvent 置 PRESSED，
        没有 mouseReleaseEvent 复位（只能靠 leaveEvent 恢复）。面板关闭（hide）
        复用时关闭按钮可能停在 PRESSED，TitleBar.canDrag() 因此返回 False，
        标题栏无法拖动；鼠标移入按钮触发 enterEvent 后才恢复。每次显示主动复位。
        """
        try:
            from qframelesswindow.titlebar.title_bar_buttons import (
                TitleBarButton, TitleBarButtonState)
            for btn in self.titleBar.findChildren(TitleBarButton):
                if btn.isPressed():
                    btn.setState(TitleBarButtonState.NORMAL)
        except Exception:
            pass

    def closeEvent(self, event):
        """关闭窗口时快速清理所有 Worker（目标 <300ms）

        注意：远程会话中心为全局单例，frpc/隧道可能被其他入口使用中，
        面板关闭不 shutdown，统一由主窗口 closeEvent 关闭。

        各 Worker 均为「run() 直接跑同步函数」的 QThread，无事件循环，
        quit() 是 no-op，wait(N) 只能干等函数自然跑完（最长 N 毫秒/
        每个运行中 worker）——之前对多个 worker 各 wait(2000) 导致关闭
        卡顿约 2 秒的回归。现改为：先 disconnect 防止关闭后回调，再
        requestInterruption + 一次性短等待（200ms），未退出的直接放弃；
        worker 持有引用由 GC 回收，落库操作幂等（历史补漏下次打开续补）。
        table_db 是模块级单连接，面板关闭不关。
        """
        # 停止补漏队列（防止关闭后继续拉取）
        dev = self.device_page
        getattr(dev, "_backfill_queue", []).clear()
        dev._backfill_running = False

        def _detach(worker):
            """断开信号并请求中断；未运行的 worker 直接跳过不等待。

            worker 均挂在页面属性上，引用随面板关闭一并回收；
            若被 GC 在 run() 执行中途销毁，Qt 层 C++ QThread 对象仍存活
            至线程结束，不会崩溃，落库操作幂等可重试。
            """
            if not (worker and worker.isRunning()):
                return
            try:
                # PySide6 不支持无参 QObject.disconnect()：指定接收者断开全部信号
                worker.disconnect(self)
            except (RuntimeError, TypeError):
                pass
            try:
                worker.requestInterruption()
            except RuntimeError:
                pass

        for page in (self.table_page, dev, self.trend_page,
                     self.settings_page):
            for attr in ("_worker", "_migrate_worker", "_refresh_worker",
                         "_test_worker", "_upload_worker", "_query_worker",
                         "_save_worker", "_meta_worker", "_export_worker",
                         "_time_worker", "_backfill_worker", "_backfill_save_worker",
                         "_alerts_worker", "_cand_worker", "_trend_worker",
                         "_rank_worker", "_hourly_worker"):
                _detach(getattr(page, attr, None))
        # 收集 Worker（不同设备可并行，列表管理）
        for worker in list(getattr(dev, "_collect_workers", [])):
            _detach(worker)
        # 一次性短等待：所有 worker 同时给 200ms 自行收尾；未退出的直接放弃，
        # 绝不在关闭路径上串行 wait（旧实现对每个运行中 worker 各 wait(2000)，
        # 是无事件循环线程的干等，累积出 ~2s 关闭卡顿的根因）
        QThread.msleep(200)
        super().closeEvent(event)
