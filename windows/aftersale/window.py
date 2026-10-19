# -*- coding: utf-8 -*-
"""window 模块：售后面板主窗口。

只负责窗口骨架与页面编排：装配四个功能页、处理跨页跳转信号、
关窗时收尾后台线程。各页自身逻辑在 entry / records / rank / settings 里。
"""

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
from windows.aftersale.rank import RankPage
from windows.aftersale.records import RecordsPage
from windows.aftersale.settings import SettingsPage

# ==================== 售后面板窗口 ====================

class AftersalePanelWindow(FluentWindow):
    """售后面板：FluentWindow + 左侧导航 + 四个功能页面（填写录入/记录与统计/售后排行/设置）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("售后面板")
        self.resize(1680, 900)
        # 最小宽度按记录页表格列数定：再窄会让操作列链接互相重叠
        self.setMinimumSize(900, 520)

        # objectName 同时是导航路由键：switchTo() 与 _on_nav_changed 都按它匹配，
        # 改名不会报错，只是页面切不过去
        self.entry_page = EntryPage(self)
        self.entry_page.setObjectName("aftersaleEntryPage")
        self.records_page = RecordsPage(self)
        self.records_page.setObjectName("aftersaleRecordsPage")
        self.rank_page = RankPage(self)
        self.rank_page.setObjectName("aftersaleRankPage")

        self.addSubInterface(self.entry_page, FluentIcon.EDIT, "填写录入")
        self.addSubInterface(self.records_page, FluentIcon.LIBRARY, "记录与统计")
        # 售后排行与 Web 端 /aftersale/rank 同功能，做球房/球桌两级排行；图标
        # 不用 VIEW，眼睛图标在小尺寸下渲染像「00」，换奖章后辨识度正常
        self.addSubInterface(self.rank_page, FluentIcon.CERTIFICATE, "售后排行")
        # 明细跳转：排行页操作列「明细」→ 记录页关键词预筛选
        self.rank_page.jump_to_records.connect(self._on_rank_jump_records)

        # 设置面板：统计周期设置 + 数据库设置（原 MySQL 设置）
        self.settings_page = SettingsPage(self)
        self.settings_page.setObjectName("aftersaleSettingsPage")
        self.addSubInterface(self.settings_page, FluentIcon.SETTING, "设置")
        # 周期保存后记录页与排行页都要按新口径重取数据，两个页面各自订阅
        self.settings_page.cycle_page.saved.connect(self._on_cycle_saved)
        # 表格平滑滚动开关变更：实时刷新记录页表格滚动模式
        self.settings_page.table_smooth_changed.connect(
            lambda _v: self.records_page._apply_smooth_mode())

        self.navigationInterface.setAcrylicEnabled(is_acrylic_enabled())
        self.navigationInterface.setCurrentItem(self.entry_page.objectName())
        try:
            self.navigationInterface.currentItemChanged.connect(
                self._on_nav_changed)
        except Exception:
            # 不同 qfluentwidgets 版本信号名有差异，连不上不影响主流程
            pass

    def _on_cycle_saved(self):
        """周期设置保存成功：记录页重建周期下拉并重查，统计页刷新（新周期立即生效）；
        排行页账期下拉选项置旧（可见时立即重拉，不可见时下次进入重拉）"""
        # 置 False 让下拉丢掉旧缓存选项：周期模式变了，旧起点列表可能已不合法
        self.records_page._cycles_loaded = False
        self.records_page._load_cycles_then_data()
        self.rank_page.reload_options()

    def _on_rank_jump_records(self, keyword: str):
        """排行页「明细」跳转：切到记录页并按关键词预筛选（同球桌管理右键入口）"""
        self.records_page.set_keyword(keyword)
        self.switchTo(self.records_page)
        self.records_page.refresh_async()

    def _on_nav_changed(self, current, _pre=None):
        """导航切换：进入设置面板时刷新数据库配置表单（showEvent 已兜底，此处兼容旧信号）"""
        obj = getattr(current, "routeKey", None)
        if obj == "aftersaleSettingsPage" and getattr(self, "settings_page", None):
            self.settings_page.mysql_card.load()

    def showEvent(self, event):
        """每次显示都校正标题栏按钮状态。

        qframelesswindow 在模态框关闭后可能把关闭按钮留在 PRESSED 态，窗口
        因此拖不动；每次 show 重置一次，比追查具体触发条件更稳。
        """
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
            # frameless 库内部结构随版本变动，取不到按钮就跳过
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
        # 按属性名后缀找 worker：各页的 worker 命名不统一，逐个枚举太脆弱
        # 新增页面只要 worker 仍以 _worker 结尾，这里不用跟着改
        for page in (self.entry_page, self.records_page, self.rank_page):
            for attr in dir(page):
                if attr.endswith("_worker"):
                    _detach(getattr(page, attr, None))
        # 只短等 200ms 而不是 wait() 到底：用户点关闭后要立刻看到窗口消失，
        # 数据库写操作在 worker 内部已有事务兜底，不会因中断丢数据
        QThread.msleep(200)
        super().closeEvent(event)
