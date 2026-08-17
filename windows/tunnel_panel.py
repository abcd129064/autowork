# -*- coding: utf-8 -*-
"""全局「当前隧道」面板

展示统一远程会话中心（RemoteSessionManager）中所有活跃 visitor：
serverName / 关联球桌 / 本地端口 / 创建来源 / 最近使用 / 断开连接 / 删除 snk。

入口：主窗口远程面板顶部「当前隧道」按钮。
- 「断开连接」：仅 frpc 运行中生效（未启动时只提示，绝不自动拉起 frpc），
  先优雅关闭该隧道上的 SSH/SFTP/RDP 会话再移除 visitor 释放端口；
  SFTP 传输中时先弹二次确认，确认后才中断传输并断开。
- 「删除 snk」：从注册表与持久化配置中彻底移除该隧道（frpc 未运行时
  也可执行，同样不会启动 frpc）。
注册表或 frpc 状态变化时自动刷新，无需手动更新。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QTableWidgetItem,
                               QHeaderView, QAbstractItemView, QWidget)
from qfluentwidgets import (TableWidget, FluentIcon, BodyLabel,
                            CaptionLabel, MessageBox,
                            PushButton as FluentPushButton)
from qfluentwidgets.window.fluent_window import FluentTitleBar
from qframelesswindow import FramelessWindow

from core.frp_remote import get_session_manager
from core.utils import show_info_bar

_HEADERS = ["serverName", "关联球桌", "本地端口", "来源", "最近使用", "断开连接", "删除 snk"]
_COL_WIDTHS = [160, 110, 70, 90, 95, 86, 80]


class TunnelPanelWindow(FramelessWindow):
    """当前隧道列表窗口（无边框 + Fluent 标题栏，独立窗口）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("当前隧道")
        self.resize(880, 420)
        self.setMinimumSize(760, 300)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self.setTitleBar(FluentTitleBar(self))
        self.titleBar.setTitle(self.windowTitle())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 40, 12, 12)
        layout.setSpacing(8)

        # --- 顶部状态行：frpc 状态 + 隧道计数 + 全停按钮 ---
        head = QHBoxLayout()
        head.setSpacing(8)
        self._status_label = BodyLabel("frpc 未启动", self)
        head.addWidget(self._status_label)
        head.addStretch(1)
        self._stop_all_btn = FluentPushButton(FluentIcon.POWER_BUTTON, "全部断开", self)
        self._stop_all_btn.setFixedHeight(28)
        self._stop_all_btn.clicked.connect(self._on_stop_all)
        head.addWidget(self._stop_all_btn)
        layout.addLayout(head)

        # --- 隧道表格 ---
        self._table = TableWidget(self)
        self._table.setColumnCount(len(_HEADERS))
        self._table.setHorizontalHeaderLabels(_HEADERS)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(36)
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for i, w in enumerate(_COL_WIDTHS):
            self._table.setColumnWidth(i, w)
        layout.addWidget(self._table, 1)

        self._hint = CaptionLabel("暂无活跃隧道 —— 通过远程面板连接或球桌右键远程后在此显示", self)
        self._hint.setStyleSheet("color: #8a919b;")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._hint)

        # 状态变化自动刷新（全部使用 bound method，配合 closeEvent 显式断开，
        # 避免窗口销毁后 manager 单例信号回调已删除的 C++ 控件）
        mgr = get_session_manager()
        mgr.visitors_changed.connect(self.refresh)
        mgr.frpc_state_changed.connect(self._on_frpc_state_changed)

        self.refresh()

    def closeEvent(self, event):
        """关闭时显式断开 manager 信号连接，防止销毁后回调崩溃"""
        mgr = get_session_manager()
        try:
            mgr.visitors_changed.disconnect(self.refresh)
            mgr.frpc_state_changed.disconnect(self._on_frpc_state_changed)
        except (RuntimeError, TypeError):
            pass
        super().closeEvent(event)

    def _on_frpc_state_changed(self, _running: bool):
        """frpc 状态变化回调（bound method，Qt 可在接收者销毁时自动断开）"""
        self.refresh()

    # ------------------------------------------------------------------ 刷新

    def refresh(self):
        mgr = get_session_manager()
        records = mgr.records()
        running = mgr.is_running()
        self._status_label.setText(
            f"frpc {'运行中' if running else '未启动'} · {len(records)} 条隧道")
        self._stop_all_btn.setEnabled(bool(records))
        self._hint.setVisible(not records)

        self._table.setRowCount(0)
        for rec in records:
            row = self._table.rowCount()
            self._table.insertRow(row)
            values = [
                rec.get("serverName", ""),
                rec.get("tableId", "") or "—",
                str(rec.get("bindPort", "")),
                rec.get("source", ""),
                rec.get("lastUsed", "") or "—",
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if col == 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row, col, item)
            # 「断开连接」列：仅 frpc 运行中生效，未启动时只提示不自动拉起
            sn = rec.get("serverName", "")
            disc_holder = QWidget(self._table)
            dl = QHBoxLayout(disc_holder)
            dl.setContentsMargins(0, 0, 0, 0)
            dl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            disc_btn = FluentPushButton("断开", disc_holder)
            disc_btn.setFixedSize(60, 26)
            disc_btn.setToolTip(
                f"断开隧道 {sn}：关闭相关 SSH/SFTP 会话并释放本地端口")
            disc_btn.clicked.connect(
                lambda _=False, s=sn: self._on_disconnect(s))
            dl.addWidget(disc_btn)
            self._table.setCellWidget(row, 5, disc_holder)
            # 「删除 snk」列：从注册表与持久化配置中彻底移除该隧道
            del_holder = QWidget(self._table)
            el = QHBoxLayout(del_holder)
            el.setContentsMargins(0, 0, 0, 0)
            el.setAlignment(Qt.AlignmentFlag.AlignCenter)
            del_btn = FluentPushButton("删除", del_holder)
            del_btn.setFixedSize(60, 26)
            del_btn.setToolTip(
                f"删除 snk 隧道 {sn}：移除注册与配置，下次启动不再恢复")
            del_btn.clicked.connect(
                lambda _=False, s=sn: self._on_delete(s))
            el.addWidget(del_btn)
            self._table.setCellWidget(row, 6, del_holder)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self._reset_titlebar_button_state()

    def _reset_titlebar_button_state(self):
        """重置标题栏按钮 PRESSED 卡滞状态（与 RemoteSessionWindow 同款处理）"""
        try:
            from qframelesswindow.titlebar.title_bar_buttons import (
                TitleBarButton, TitleBarButtonState)
            for btn in self.titleBar.findChildren(TitleBarButton):
                if btn.isPressed():
                    btn.setState(TitleBarButtonState.NORMAL)
        except Exception:
            pass

    # ------------------------------------------------------------------ 操作

    def _find_record(self, server_name: str) -> dict:
        """按 serverName 取当前注册表记录（不存在返回空 dict，列表可能已滞后）"""
        return next((r for r in get_session_manager().records()
                     if r.get("serverName") == server_name), {})

    def _confirm_interrupt_transfer(self, server_name: str) -> bool:
        """SFTP 传输中二次确认：取消保持当前连接与传输不变，确认才允许断开"""
        dlg = MessageBox(
            "文件传输进行中",
            f"隧道「{server_name}」上有 SFTP 文件传输正在进行。\n"
            "继续操作将立即中断当前文件传输，并关闭该隧道上的\n"
            "SSH / SFTP 会话。\n\n确定要继续吗？",
            self)
        dlg.yesButton.setText("仍然断开")
        dlg.cancelButton.setText("取消")
        return bool(dlg.exec())

    def _on_disconnect(self, server_name: str):
        """断开单条隧道：仅 frpc 运行中生效，绝不自动启动 frpc

        流程：frpc 未启动 → 仅提示；SFTP 传输中 → 二次确认；
        确认后由 manager 关闭相关会话、移除 visitor 并重启/停止 frpc 释放端口。
        """
        if not server_name:
            return
        mgr = get_session_manager()
        rec = self._find_record(server_name)
        if not rec:
            self.refresh()
            return
        if not mgr.is_running():
            show_info_bar("当前 frpc 未启动，隧道未建立，无需断开",
                          "warning", title="无法断开", parent=self, duration=3500)
            return
        if mgr.is_transferring_on_port(rec.get("bindPort", 0)) \
                and not self._confirm_interrupt_transfer(server_name):
            return
        result = mgr.disconnect_visitor(server_name)
        if result == "ok":
            show_info_bar(f"已断开隧道 {server_name}，相关会话已关闭、本地端口已释放",
                          "success", title="断开成功", parent=self, duration=3000)
        elif result == "not_running":
            show_info_bar("当前 frpc 未启动", "warning",
                          title="无法断开", parent=self, duration=3500)
        else:
            show_info_bar(f"隧道 {server_name} 断开失败", "error",
                          title="断开失败", parent=self, duration=4000)

    def _on_delete(self, server_name: str):
        """删除 snk：从注册表与持久化配置中彻底移除（frpc 未运行时也可执行，
        不会启动 frpc）；运行中会先关闭相关会话，SFTP 传输中同样二次确认"""
        if not server_name:
            return
        mgr = get_session_manager()
        rec = self._find_record(server_name)
        if not rec:
            self.refresh()
            return
        transferring = mgr.is_running() and \
            mgr.is_transferring_on_port(rec.get("bindPort", 0))
        msg = (f"确定删除 snk 隧道「{server_name}」吗？\n"
               "删除后将从隧道列表与持久化配置中移除，下次启动不再恢复。")
        if transferring:
            msg += "\n\n注意：该隧道上有 SFTP 文件传输正在进行，\n删除将立即中断当前文件传输并关闭相关会话！"
        dlg = MessageBox("删除 snk 隧道", msg, self)
        dlg.yesButton.setText("删除")
        dlg.cancelButton.setText("取消")
        if not dlg.exec():
            return
        result = mgr.delete_visitor(server_name)
        if result == "ok":
            show_info_bar(f"已删除 snk 隧道 {server_name}",
                          "success", title="删除成功", parent=self, duration=3000)
        else:
            show_info_bar(f"隧道 {server_name} 删除失败", "error",
                          title="删除失败", parent=self, duration=4000)

    def _on_stop_all(self):
        """全部断开：关闭全部会话、清空注册表并停止 frpc（仅 frpc 运行中生效）"""
        mgr = get_session_manager()
        records = list(mgr.records())
        if not records:
            return
        if not mgr.is_running():
            show_info_bar("当前 frpc 未启动，无活跃隧道可断开",
                          "warning", title="无法断开", parent=self, duration=3500)
            return
        if any(mgr.is_transferring_on_port(r.get("bindPort", 0))
               for r in records) \
                and not self._confirm_interrupt_transfer("全部隧道"):
            return
        mgr.close_all_sessions("全部隧道")
        for rec in records:
            mgr.remove_visitor(rec.get("serverName", ""))
        try:
            mgr.apply()
        except (OSError, RuntimeError):
            pass
        show_info_bar("已断开全部隧道", "success", title="全部断开",
                      parent=self, duration=3000)
