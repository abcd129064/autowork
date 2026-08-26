# -*- coding: utf-8 -*-
"""跑视频面板：设置页（默认署名 + 数据库设置）

默认署名读写 settings.json 的 newlog_target_name（与主界面
NewLog 批量整理共用同一键，_default_creator 预填口径一致）；
数据库卡片复用 MysqlSyncCard（sync_scope="ledger"，仅推跑视频记录）。
"""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from PySide6.QtCore import Signal

from qfluentwidgets import (ScrollArea, CardWidget, BodyLabel, CaptionLabel,
                            LineEdit, PushButton, FluentIcon, SwitchButton)

from core.perf import (get_table_smooth, set_table_smooth,
                       get_animation, set_animation)
from core.utils import show_info_bar
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


class SettingsPage(QWidget):
    """设置面板：默认署名 + 性能（动画/表格平滑滚动）+ 数据库设置（MySQL 同步，仅推跑视频记录）"""

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

        # 默认署名卡片
        self.signer_card = SignerSettingsCard(content)
        self.signer_card.load()
        cl.addWidget(self.signer_card)

        # 性能卡片（动画/表格平滑滚动，仅影响本面板）
        self._perf_card = self._make_perf_card(content)
        cl.addWidget(self._perf_card)

        # 数据库设置卡片（MySQL 开关/连接/测试；scope 文案为跑视频记录）
        self.mysql_card = MysqlSyncCard(content, sync_scope="ledger")
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
