# -*- coding: utf-8 -*-
"""售后设置面板：统计周期设置、性能开关与数据库卡片。

周期设置与性能开关原本挤在 windows/aftersale_panel.py 里，拆出来是为了让
它们单独演进；本模块只负责收集与回显配置，周期归属与重算逻辑都在 aftersale_db。
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
    SegmentedWidget, ProgressBar, FlowLayout, SwitchButton)

from core.design_tokens import SEMANTIC, lighten, darken
from core.flow_widgets import FlowToolbarScrollArea
from core.perf import (is_acrylic_enabled, get_table_smooth, set_table_smooth,
                       get_animation, set_animation)
from core.spin_fit_patch import FittedSpinBox
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

    # 重算结束后才发出：收到它的记录页可以直接按新周期刷新，不必自己等重算
    saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._recalc_worker = None  # 周期物化列重算 worker（保存后触发）
        self._init_ui()

    def showEvent(self, event):
        """每次显示都重新回显配置，外部改动下次进入即自动同步。

        不用导航切换信号驱动：周期配置也可能被脚本或另一个面板改掉，
        Qt 原生显示事件覆盖所有进入路径，比只挂在信号上更可靠。
        """
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
        # 这里的取值只是占位，真正的模式由 load() 按配置覆盖
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
        # sample 给 FittedSpinBox 一个三位数宽度基准，否则 365 会被挤出边界；
        # 上限 365 与 aftersale_db 的周期校验保持一致，放宽也会被后端拒绝
        self._span_spin = FittedSpinBox(custom_wrap, sample="365")
        self._span_spin.setRange(1, 365)
        self._span_spin.setValue(7)
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
        """自定义模式切换时显示或隐藏参数区，减少无关控件的视觉干扰。

        隐藏不影响取值与保存逻辑，所以这里不需要清理起始日与天数；
        参数可见性只跟随当前模式，别处不必再单独维护。
        """
        self._custom_wrap.setVisible(checked)

    def load(self):
        """把保存的周期配置回填到控件，字段残缺时逐项退回默认值。

        配置来自 settings.json，可能被手改坏或被旧版本写成半套字段，
        因此每项单独兜底，不能因为一个字段异常就让整页停在空状态。
        """
        cfg = aftersale_db.load_cycle_mode()
        mode = cfg.get("type", "tue")
        # 同一父控件的单选项自动互斥，逐个 setChecked 即可完成回显
        self._rb_tue.setChecked(mode == "tue")
        self._rb_mon.setChecked(mode == "mon")
        self._rb_custom.setChecked(mode == "custom")
        self._rb_month.setChecked(mode == "month")
        start = str(cfg.get("start") or "")
        d = QDate.fromString(start, "yyyy-MM-dd")
        # 非法日期串会解析成无效 QDate，此时保留控件现值，不能无条件 setDate
        if d.isValid():
            self._start_picker.setDate(d)
        self._span_spin.setValue(int(cfg.get("span") or 7))
        # 参数区可见性必须跟回显出的模式一致，否则回显后界面与配置对不上
        self._custom_wrap.setVisible(mode == "custom")

    def _on_save(self):
        """保存周期设置并立刻重算记录归属，重算完成后才通知记录页刷新。

        顺序必须是「保存 → 后台重算 cycle_start → 发 saved」：周期筛选走索引
        等值匹配物化列，先刷新就会出现新规则配旧数据的错位。
        重算交给 worker，几百条记录的重写不会卡住 UI 线程。
        失败也有兜底：保存时置上的 recalc_pending 标志只有重算成功才清除；
        若重算中途失败，标志就残留到下次打开面板，记录页首载检测到它便自动
        全量重算并清标志，把「配置已存但归属没跟上」的窗口收敛掉。
        记录页的 recalc_cycle_starts_on_load 与本方法共用同一个重算入口，
        避免两处各写一份周期归属规则而慢慢分叉。
        """
        # 以单选组当前状态为准，不另存模式变量：两份状态迟早会不同步
        if self._rb_mon.isChecked():
            mode = "mon"
        elif self._rb_custom.isChecked():
            mode = "custom"
        elif self._rb_month.isChecked():
            mode = "month"
        else:
            mode = "tue"
        # 合并写：只覆盖 type/start/span 三项，settings.json 里的其它配置原样保留
        aftersale_db.save_cycle_mode({
            "type": mode,
            "start": self._start_picker.date.toString("yyyy-MM-dd"),
            "span": self._span_spin.value(),
        })
        show_info_bar("周期设置已保存，正在按新周期重算记录归属…",
                      "success", title="周期设置", parent=self, duration=3000)
        # 重算要几秒，期间禁用保存按钮，否则连点会并发起多个重算 worker
        self._btn_save.setEnabled(False)
        self._recalc_worker = AftersaleDBWorker(
            aftersale_db.recalc_cycle_starts)
        self._recalc_worker.result_ready.connect(self._on_recalc_done)
        self._recalc_worker.error.connect(self._on_recalc_error)
        self._recalc_worker.start()

    def _on_recalc_done(self, updated: int):
        """重算成功后才发 saved，保证记录页刷新时用的已是新周期口径。

        走到这里说明 pending 标志已被清除，记录页不会再有残留的自愈动作。
        """
        self._btn_save.setEnabled(True)
        self.saved.emit()  # 通知记录页刷新周期下拉与统计

    def _on_recalc_error(self, msg: str):
        """重算失败时仍然刷新，并把「口径暂时落后」明确告知用户。

        保存本身已经成功，只是物化列没跟上，所以既不能撤回配置也不能吞掉错误：
        此时 pending 标志仍然残留，记录页下次首载会自动全量重算，
        界面只需说明当前按旧口径过滤。
        """
        self._btn_save.setEnabled(True)
        # 不在这里重试：重试会掩盖持续失败，交给记录页首载的自愈入口统一处理
        show_info_bar(
            "周期重算失败，列表过滤暂按旧周期口径，重新打开面板后将自动追平",
            "error", title="周期设置", parent=self, duration=5000)
        self.saved.emit()


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

        # 内容包进 ScrollArea：小分辨率下设置项可以滚动查看，而不是被裁掉
        scroll = ScrollArea(self)
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }")
        root.addWidget(scroll)

    def showEvent(self, event):
        """每次进入设置面板都重新回显数据库卡片与两个性能开关的生效值。

        不用导航信号驱动：部分 qfluentwidgets 版本的导航切换不发信号，
        挂在 Qt 原生显示事件上才对所有进入路径都成立。
        """
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
        """构建性能卡片，只放影响本面板的两个开关，不越权改动全局配置。

        两个开关都写入 aftersale 作用域，未单独拨动时继承主界面全局值，
        因此这里只做回显与持久化。
        """
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
        # 信号接在 setChecked 之后：先取值再连接，初始化赋值不会被当成拨动写盘
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
        """只持久化本面板的覆盖值，不做即时重绘。

        动画开关在每次弹出菜单或下拉时才被读取，此刻刷新反而会打断
        正在进行的交互，因此下一次弹出自然生效。
        """
        set_animation("aftersale", checked)

    def _on_table_smooth_toggled(self, checked):
        """持久化本面板覆盖值并广播出去，让记录页表格立刻切换滚动模式。

        与动画开关不同，滚动模式的差异用户马上就能感知，必须即时生效，
        所以这里要额外发信号，而不是等下次打开面板。
        """
        set_table_smooth("aftersale", checked)
        self.table_smooth_changed.emit(checked)
