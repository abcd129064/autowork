# -*- coding: utf-8 -*-
"""settings 模块（从 windows/aftersale_panel.py 拆出，逻辑未改动）"""

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
    SpinBox, SegmentedWidget, ProgressBar, FlowLayout, SwitchButton)

from core.design_tokens import SEMANTIC, lighten, darken
from core.flow_widgets import FlowToolbarScrollArea
from core.perf import (is_acrylic_enabled, get_table_smooth, set_table_smooth,
                       get_animation, set_animation)
from core.theme_qss import current_accent_hex
from core.utils import show_info_bar
from database import aftersale_db, table_db
from workers.aftersale_worker import AftersaleDBWorker
from windows.mysql_sync_card import MysqlSyncCard

from windows.aftersale.common import *  # noqa: F401,F403

# ==================== 周期设置页 ====================

class CycleSettingsPage(QWidget):
    """周期设置卡片：统计周期模式（周二起默认 / 自然周 / 自定义 / 自然月），保存即生效

    saved 信号：保存成功后发出，供设置面板通知记录页刷新周期下拉
    """

    saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def showEvent(self, event):
        """卡片每次显示时回显最新配置（不依赖导航信号，Qt 原生事件更稳）"""
        super().showEvent(event)
        self.load()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)  # 作为设置面板卡片嵌入，外边距由面板控制

        card = CardWidget(self)
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(10)
        vbox.addWidget(BodyLabel("统计周期设置", card))
        vbox.addWidget(CaptionLabel(
            "周期决定售后记录按发生时间归属的统计区间（周二起周一止为原默认）；"
            "保存后立即生效，历史记录的归属按新规则重新计算，无需手工修改", card))

        # 周期模式单选
        self._rb_tue = RadioButton("周二 ~ 周一（原默认，每周二开始）", card)
        self._rb_mon = RadioButton("周一 ~ 周日（自然周）", card)
        self._rb_custom = RadioButton("自定义（指定起始日与周期天数）", card)
        self._rb_month = RadioButton("自然月（按月统计，每月 1 号起）", card)
        self._rb_tue.setChecked(True)
        for rb in (self._rb_tue, self._rb_mon, self._rb_custom,
                   self._rb_month):
            vbox.addWidget(rb)
        self._rb_custom.toggled.connect(self._on_custom_toggled)

        # 自定义参数区（仅自定义模式显示）
        custom_wrap = QWidget(card)
        custom_lay = QHBoxLayout(custom_wrap)
        custom_lay.setContentsMargins(0, 0, 0, 0)
        custom_lay.setSpacing(8)
        custom_lay.addWidget(QLabel("起始日:", custom_wrap))
        self._start_picker = ZhDatePicker(custom_wrap)
        self._start_picker.setFixedWidth(150)
        custom_lay.addWidget(self._start_picker)
        custom_lay.addSpacing(16)
        custom_lay.addWidget(QLabel("周期天数:", custom_wrap))
        self._span_spin = SpinBox(custom_wrap)
        self._span_spin.setRange(1, 365)
        self._span_spin.setValue(7)
        self._span_spin.setFixedWidth(100)
        custom_lay.addWidget(self._span_spin)
        custom_lay.addStretch(1)
        vbox.addWidget(custom_wrap)
        self._custom_wrap = custom_wrap
        self._custom_wrap.setVisible(False)

        # 保存按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_save = PrimaryPushButton(FluentIcon.ACCEPT, "保存", card)
        self._btn_save.setToolTip("保存后立即生效：列表/统计/周期下拉按新规则重新归属")
        self._btn_save.setFixedHeight(36)
        self._btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self._btn_save)
        vbox.addLayout(btn_row)

        root.addWidget(card)
        root.addStretch(1)

    def _on_custom_toggled(self, checked: bool):
        """自定义模式切换：显示/隐藏起始日与周期天数参数区"""
        self._custom_wrap.setVisible(checked)

    def load(self):
        """回显当前周期配置（外部改动后进入页面刷新最新值）"""
        cfg = aftersale_db.load_cycle_mode()
        mode = cfg.get("type", "tue")
        self._rb_tue.setChecked(mode == "tue")
        self._rb_mon.setChecked(mode == "mon")
        self._rb_custom.setChecked(mode == "custom")
        self._rb_month.setChecked(mode == "month")
        start = str(cfg.get("start") or "")
        d = QDate.fromString(start, "yyyy-MM-dd")
        if d.isValid():
            self._start_picker.setDate(d)
        self._span_spin.setValue(int(cfg.get("span") or 7))
        self._custom_wrap.setVisible(mode == "custom")

    def _on_save(self):
        """保存周期设置：写 settings.json（合并写保留其他字段）并即时生效"""
        if self._rb_mon.isChecked():
            mode = "mon"
        elif self._rb_custom.isChecked():
            mode = "custom"
        elif self._rb_month.isChecked():
            mode = "month"
        else:
            mode = "tue"
        aftersale_db.save_cycle_mode({
            "type": mode,
            "start": self._start_picker.date.toString("yyyy-MM-dd"),
            "span": self._span_spin.value(),
        })
        show_info_bar("周期设置已保存，列表/统计已按新周期重新归属",
                      "success", title="周期设置", parent=self, duration=3000)
        self.saved.emit()  # 通知记录页刷新周期下拉与统计


# ==================== 设置面板（周期 + 数据库） ====================

class SettingsPage(QWidget):
    """设置面板：统计周期设置 + 性能（动画/表格平滑滚动）+ 数据库设置（原 MySQL 设置更名）

    周期设置保存后通过 cycle_page.saved 信号通知记录页刷新周期下拉；
    表格平滑滚动开关经 table_smooth_changed 信号联动记录页实时刷新滚动模式；
    动画开关在菜单下一次弹出时动态读取生效值，无需信号联动；
    数据库卡片复用 MysqlSyncCard（仅推售后记录）。
    整体包在 ScrollArea 里：小分辨率下设置项可滚动查看，不再被裁剪。
    """

    # 表格平滑滚动开关变更（窗口据此刷新记录页表格滚动模式）
    table_smooth_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        content = QWidget(self)
        cl = QVBoxLayout(content)
        cl.setContentsMargins(24, 20, 24, 20)
        cl.setSpacing(14)

        # 周期设置卡片
        self.cycle_page = CycleSettingsPage(content)
        cl.addWidget(self.cycle_page)

        # 性能卡片（动画/表格平滑滚动，仅影响本面板）
        self._perf_card = self._make_perf_card(content)
        cl.addWidget(self._perf_card)

        # 数据库设置卡片
        self.mysql_card = MysqlSyncCard(content, sync_scope="aftersale")
        self.mysql_card.load()
        cl.addWidget(self.mysql_card)
        cl.addStretch(1)

        scroll = ScrollArea(self)
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }")
        root.addWidget(scroll)

    def showEvent(self, event):
        """进入设置面板回显最新配置（导航信号部分版本缺失，用 Qt 原生事件兜底）"""
        super().showEvent(event)
        self.mysql_card.load()
        # 回显两个性能开关当前生效值（覆盖→全局），blockSignals 避免误触发持久化
        self.sw_table_smooth.blockSignals(True)
        self.sw_table_smooth.setChecked(get_table_smooth("aftersale"))
        self.sw_table_smooth.blockSignals(False)
        self.sw_animation.blockSignals(True)
        self.sw_animation.setChecked(get_animation("aftersale"))
        self.sw_animation.blockSignals(False)
    
    def _make_perf_card(self, parent):
        """性能卡片：菜单弹出动画 + 表格平滑滚动开关（仅影响本面板）"""
        card = CardWidget(parent)
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(8)
        vbox.addWidget(BodyLabel("性能", card))
        vbox.addWidget(CaptionLabel(
            "仅影响售后面板的菜单/下拉动画与记录列表滚动表现；"
            "未单独拨动时跟随主界面全局开关", card))
        # 菜单弹出动画（下一次弹出即生效，无需信号联动）
        row_ani = QHBoxLayout()
        row_ani.setSpacing(8)
        lbl_ani = BodyLabel("菜单弹出动画", card)
        lbl_ani.setToolTip("关闭后本面板的右键菜单/下拉框直接弹出，无过渡动画")
        row_ani.addWidget(lbl_ani, 1)
        self.sw_animation = SwitchButton(card)
        self.sw_animation.setOnText("开")
        self.sw_animation.setOffText("关")
        # 先回显当前生效值，再连接信号，避免初始化 setChecked 误触发持久化
        self.sw_animation.setChecked(get_animation("aftersale"))
        row_ani.addWidget(self.sw_animation)
        vbox.addLayout(row_ani)
        self.sw_animation.checkedChanged.connect(self._on_animation_toggled)
        # 表格平滑滚动
        row = QHBoxLayout()
        row.setSpacing(8)
        lbl = BodyLabel("表格平滑滚动", card)
        lbl.setToolTip("开启后滚动带动画；关闭走原生滚动更快（大表格更流畅）")
        row.addWidget(lbl, 1)
        self.sw_table_smooth = SwitchButton(card)
        self.sw_table_smooth.setOnText("开")
        self.sw_table_smooth.setOffText("关")
        # 先回显当前生效值，再连接信号，避免初始化 setChecked 误触发持久化
        self.sw_table_smooth.setChecked(get_table_smooth("aftersale"))
        row.addWidget(self.sw_table_smooth)
        vbox.addLayout(row)
        self.sw_table_smooth.checkedChanged.connect(
            self._on_table_smooth_toggled)
        return card
    
    def _on_animation_toggled(self, checked):
        """拨动动画开关：持久化本面板覆盖值（下一次菜单弹出即生效）"""
        set_animation("aftersale", checked)

    def _on_table_smooth_toggled(self, checked):
        """拨动开关：持久化本面板覆盖值并通知窗口刷新记录页表格滚动模式"""
        set_table_smooth("aftersale", checked)
        self.table_smooth_changed.emit(checked)
