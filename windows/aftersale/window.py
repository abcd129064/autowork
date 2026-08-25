# -*- coding: utf-8 -*-
"""window 模块（从 windows/aftersale_panel.py 拆出，逻辑未改动）"""

import os
from datetime import datetime, timedelta

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QLabel, QListWidget,
    QListWidgetItem, QFileDialog, QComboBox as _QComboBox, QApplication,
    QDialog, QPushButton as _QPushButton, QGridLayout)
from PySide6.QtCore import Qt, QTimer, QThread, QPointF, QDate, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QFont
from qfluentwidgets import (TableWidget, SearchLineEdit, PushButton,
    PrimaryPushButton, ToolButton, FluentIcon, RoundMenu, Action,
    LineEdit, PlainTextEdit, BodyLabel, CaptionLabel, TitleLabel,
    ScrollArea, CardWidget, MessageBox, MessageBoxBase, CheckBox,
    FluentWindow, NavigationItemPosition, MenuAnimationType,
    setCustomStyleSheet, qconfig, isDarkTheme, ZhDatePicker, RadioButton,
    SpinBox, SegmentedWidget, ProgressBar, FlowLayout)

from core.design_tokens import SEMANTIC, lighten, darken
from core.flow_widgets import FlowToolbarScrollArea
from core.perf import is_acrylic_enabled
from core.theme_qss import current_accent_hex
from core.utils import show_info_bar
from database import aftersale_db, table_db
from workers.aftersale_worker import AftersaleDBWorker
from windows.mysql_sync_card import MysqlSyncCard

from windows.aftersale.common import *  # noqa: F401,F403
from windows.aftersale.entry import EntryPage
from windows.aftersale.records import RecordsPage
from windows.aftersale.settings import SettingsPage

# ==================== 售后面板窗口 ====================

class AftersalePanelWindow(FluentWindow):
    """售后面板：FluentWindow + 左侧导航 + 三个功能页面（填写录入/记录与统计/设置）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("售后面板")
        self.resize(1680, 900)
        self.setMinimumSize(900, 520)

        self.entry_page = EntryPage(self)
        self.entry_page.setObjectName("aftersaleEntryPage")
        self.records_page = RecordsPage(self)
        self.records_page.setObjectName("aftersaleRecordsPage")

        self.addSubInterface(self.entry_page, FluentIcon.EDIT, "填写录入")
        self.addSubInterface(self.records_page, FluentIcon.LIBRARY, "记录与统计")

        # 设置面板：统计周期设置 + 数据库设置（原 MySQL 设置）
        self.settings_page = SettingsPage(self)
        self.settings_page.setObjectName("aftersaleSettingsPage")
        self.addSubInterface(self.settings_page, FluentIcon.SETTING, "设置")
        # 周期设置保存后，记录页周期下拉与统计立即按新周期刷新
        self.settings_page.cycle_page.saved.connect(self._on_cycle_saved)
        # 表格平滑滚动开关变更：实时刷新记录页表格滚动模式（仅本面板）
        self.settings_page.table_smooth_changed.connect(
            lambda _v: self.records_page._apply_smooth_mode())

        self.navigationInterface.setAcrylicEnabled(is_acrylic_enabled())
        self.navigationInterface.setCurrentItem(self.entry_page.objectName())
        try:
            self.navigationInterface.currentItemChanged.connect(
                self._on_nav_changed)
        except Exception:
            pass

    def _on_cycle_saved(self):
        """周期设置保存成功：记录页重建周期下拉并重查，统计页刷新（新周期立即生效）"""
        self.records_page._cycles_loaded = False
        self.records_page._load_cycles_then_data()

    def _on_nav_changed(self, current, _pre=None):
        """导航切换：进入设置面板时刷新数据库配置表单（showEvent 已兜底，此处兼容旧信号）"""
        obj = getattr(current, "routeKey", None)
        if obj == "aftersaleSettingsPage" and getattr(self, "settings_page", None):
            self.settings_page.mysql_card.load()

    def showEvent(self, event):
        super().showEvent(event)
        self._reset_titlebar_button_state()

    def _reset_titlebar_button_state(self):
        """重置标题栏按钮状态（同管理面板：修复关闭按钮卡 PRESSED 导致无法拖动）"""
        try:
            from qframelesswindow.titlebar.title_bar_buttons import (
                TitleBarButton, TitleBarButtonState)
            for btn in self.titleBar.findChildren(TitleBarButton):
                if btn.isPressed():
                    btn.setState(TitleBarButtonState.NORMAL)
        except Exception:
            pass

    def open_records_for_table(self, table_no: str):
        """球桌管理右键入口：跳转记录页并按桌号预筛选"""
        self.records_page.set_keyword(table_no)
        self.switchTo(self.records_page)
        self.records_page.refresh_async()

    def closeEvent(self, event):
        """关闭窗口快速清理 Worker（同管理面板策略：disconnect + 200ms 短等）"""
        def _detach(w):
            if w is None:
                return
            try:
                w.disconnect(self)
            except Exception:
                pass
            try:
                w.requestInterruption()
            except Exception:
                pass
        for page in (self.entry_page, self.records_page):
            for attr in dir(page):
                if attr.endswith("_worker"):
                    _detach(getattr(page, attr, None))
        QThread.msleep(200)
        super().closeEvent(event)
