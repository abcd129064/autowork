# -*- coding: utf-8 -*-
"""跑视频面板：设置页（默认署名 + 数据库设置）

默认署名读写 settings.json 的 newlog_target_name（与主界面
NewLog 批量整理共用同一键，_default_creator 预填口径一致）；
数据库卡片复用 MysqlSyncCard（sync_scope="run_video"，仅推跑视频记录）。
"""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from PySide6.QtCore import Signal

from qfluentwidgets import (ScrollArea, CardWidget, BodyLabel, CaptionLabel,
                            LineEdit, PushButton, FluentIcon, SwitchButton,
                            ComboBox)

from core.perf import (get_table_smooth, set_table_smooth,
                       get_animation, set_animation)
from core.utils import show_info_bar
from database import ledger_db
from windows.mysql_sync_card import (MysqlSyncCard, _load_settings,
                                     _save_settings)


class SignerSettingsCard(CardWidget):
    """默认署名卡片：跑视频/售后表单署名预填值，保存到 settings.json"""

    def __init__(self, parent=None):
        super().__init__(parent)
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(8)
        vbox.addWidget(BodyLabel("默认署名", self))
        vbox.addWidget(CaptionLabel(
            "填写录入页「署名」栏的预填值；与主界面视频/日志批量整理的"
            "筛选署名共用同一配置，两处保持一致", self))

        row = QHBoxLayout()
        row.setSpacing(8)
        self.sign_edit = LineEdit(self)
        self.sign_edit.setPlaceholderText("如：沈喆")
        self.sign_edit.setFixedWidth(200)
        row.addWidget(self.sign_edit)
        self._btn_save = PushButton(FluentIcon.SAVE, "保存", self)
        self._btn_save.setToolTip("写入 settings.json，下一次打开表单即生效")
        self._btn_save.setFixedHeight(33)
        self._btn_save.clicked.connect(self._on_save)
        row.addWidget(self._btn_save)
        row.addStretch(1)
        vbox.addLayout(row)

    def load(self):
        """回显当前默认署名（外部改动后进入页面刷新最新值）"""
        self.sign_edit.setText(
            str(_load_settings().get("newlog_target_name", "") or ""))

    def _on_save(self):
        _save_settings({"newlog_target_name": self.sign_edit.text().strip()})
        show_info_bar("默认署名已保存，填写录入页将自动预填",
                      "success", title="默认署名", parent=self, duration=3000)


class AutoRefreshCard(CardWidget):
    """自动刷新卡片（需求4）：开关 + 间隔下拉，写 ledger_db 配置并发信号

    与售后「自动刷新记录」同范式：定时比对全表指纹，仅在数据真的变化时
    静默重查记录页，他人填写免手动同步。开关/间隔变更即时转发记录页生效。
    """

    # 开关或间隔变更后发出（窗口据此调记录页 _sync_auto_timer）
    changed = Signal()

    _INTERVALS = (("15 秒", 15), ("30 秒", 30),
                  ("1 分钟", 60), ("5 分钟", 300))

    def __init__(self, parent=None):
        super().__init__(parent)
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(8)
        vbox.addWidget(BodyLabel("自动刷新", self))
        vbox.addWidget(CaptionLabel(
            "定时检查数据库变化（他人填写的记录自动出现，无需手动同步）；"
            "仅在数据真正变化时刷新，不打扰当前浏览", self))

        row_sw = QHBoxLayout()
        row_sw.setSpacing(8)
        lbl_sw = BodyLabel("自动刷新记录", self)
        row_sw.addWidget(lbl_sw, 1)
        self.sw_enabled = SwitchButton(self)
        self.sw_enabled.setOnText("开")
        self.sw_enabled.setOffText("关")
        self.sw_enabled.setChecked(ledger_db.auto_refresh_enabled())
        self.sw_enabled.checkedChanged.connect(self._on_toggle)
        row_sw.addWidget(self.sw_enabled)
        vbox.addLayout(row_sw)

        row_iv = QHBoxLayout()
        row_iv.setSpacing(8)
        lbl_iv = BodyLabel("刷新间隔", self)
        row_iv.addWidget(lbl_iv, 1)
        self.combo_interval = ComboBox(self)
        for label, _v in self._INTERVALS:
            self.combo_interval.addItem(label)
        cur = ledger_db.auto_refresh_interval()
        idx = next((i for i, (_l, v) in enumerate(self._INTERVALS)
                    if v == cur), 1)
        self.combo_interval.setCurrentIndex(idx)
        self.combo_interval.setFixedWidth(120)
        self.combo_interval.currentIndexChanged.connect(self._on_interval)
        row_iv.addWidget(self.combo_interval)
        vbox.addLayout(row_iv)

    def load(self):
        """回显当前配置（外部改动后进入页面刷新；blockSignals 防误触发持久化）"""
        self.sw_enabled.blockSignals(True)
        self.sw_enabled.setChecked(ledger_db.auto_refresh_enabled())
        self.sw_enabled.blockSignals(False)
        cur = ledger_db.auto_refresh_interval()
        idx = next((i for i, (_l, v) in enumerate(self._INTERVALS)
                    if v == cur), 1)
        self.combo_interval.blockSignals(True)
        self.combo_interval.setCurrentIndex(idx)
        self.combo_interval.blockSignals(False)

    def _on_toggle(self, checked):
        ledger_db.set_auto_refresh(bool(checked))
        self.changed.emit()

    def _on_interval(self, index):
        if 0 <= index < len(self._INTERVALS):
            ledger_db.set_auto_refresh_interval(self._INTERVALS[index][1])
            self.changed.emit()


class SettingsPage(QWidget):
    """设置面板：默认署名 + 性能（动画/表格平滑滚动）+ 数据库设置（MySQL 同步，仅推跑视频记录）"""

    # 表格平滑滚动开关变更（窗口据此刷新记录页表格滚动模式）
    table_smooth_changed = Signal(bool)
    # 自动刷新开关/间隔变更（窗口据此调记录页 _sync_auto_timer，需求4）
    auto_refresh_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        content = QWidget(self)
        cl = QVBoxLayout(content)
        cl.setContentsMargins(24, 20, 24, 20)
        cl.setSpacing(14)

        # 默认署名卡片
        self.signer_card = SignerSettingsCard(content)
        self.signer_card.load()
        cl.addWidget(self.signer_card)

        # 自动刷新卡片（需求4）
        self.auto_refresh_card = AutoRefreshCard(content)
        self.auto_refresh_card.changed.connect(self.auto_refresh_changed.emit)
        cl.addWidget(self.auto_refresh_card)

        # 性能卡片（动画/表格平滑滚动，仅影响本面板）
        self._perf_card = self._make_perf_card(content)
        cl.addWidget(self._perf_card)

        # 数据库设置卡片（MySQL 开关/连接/测试；scope 文案为跑视频记录）
        self.mysql_card = MysqlSyncCard(content, sync_scope="run_video")
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
        self.signer_card.load()
        self.mysql_card.load()
        # 回显两个性能开关当前生效值（覆盖→全局），blockSignals 避免误触发持久化
        self.sw_table_smooth.blockSignals(True)
        self.sw_table_smooth.setChecked(get_table_smooth("video"))
        self.sw_table_smooth.blockSignals(False)
        self.sw_animation.blockSignals(True)
        self.sw_animation.setChecked(get_animation("video"))
        self.sw_animation.blockSignals(False)

    def _make_perf_card(self, parent):
        """性能卡片：菜单弹出动画 + 表格平滑滚动开关（仅影响本面板）"""
        card = CardWidget(parent)
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(8)
        vbox.addWidget(BodyLabel("性能", card))
        vbox.addWidget(CaptionLabel(
            "仅影响跑视频面板的菜单/下拉动画与记录列表滚动表现；"
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
        self.sw_animation.setChecked(get_animation("video"))
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
        self.sw_table_smooth.setChecked(get_table_smooth("video"))
        row.addWidget(self.sw_table_smooth)
        vbox.addLayout(row)
        self.sw_table_smooth.checkedChanged.connect(
            self._on_table_smooth_toggled)
        return card

    def _on_animation_toggled(self, checked):
        """拨动动画开关：持久化本面板覆盖值（下一次菜单弹出即生效）"""
        set_animation("video", checked)

    def _on_table_smooth_toggled(self, checked):
        """拨动开关：持久化本面板覆盖值并通知窗口刷新记录页表格滚动模式"""
        set_table_smooth("video", checked)
        self.table_smooth_changed.emit(checked)
