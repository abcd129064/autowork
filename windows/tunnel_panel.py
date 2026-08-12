# -*- coding: utf-8 -*-
"""全局「当前隧道」面板

展示统一远程会话中心（RemoteSessionManager）中所有活跃 visitor：
serverName / 关联球桌 / 本地端口 / 创建来源 / 最近使用 / 断开操作。

入口：主窗口远程面板顶部「当前隧道」按钮。
每行「断开」移除对应 visitor 并重写 TOML（剩余为空时 frpc 自动停止）；
注册表或 frpc 状态变化时自动刷新，无需手动更新。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QTableWidgetItem,
                               QHeaderView, QAbstractItemView, QWidget)
from qfluentwidgets import (TableWidget, ToolButton, FluentIcon, BodyLabel,
                            CaptionLabel, PushButton as FluentPushButton)
from qfluentwidgets.window.fluent_window import FluentTitleBar
from qframelesswindow import FramelessWindow

from core.frp_remote import get_session_manager

_HEADERS = ["serverName", "关联球桌", "本地端口", "来源", "最近使用", "操作"]
_COL_WIDTHS = [170, 130, 80, 110, 100, 60]


class TunnelPanelWindow(FramelessWindow):
    """当前隧道列表窗口（无边框 + Fluent 标题栏，独立窗口）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("当前隧道")
        self.resize(780, 420)
        self.setMinimumSize(640, 300)
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
            # 操作列：断开按钮（移除该 visitor 并重写 TOML）
            holder = QWidget(self._table)
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            btn = ToolButton(FluentIcon.CLOSE, holder)
            btn.setToolTip(f"断开隧道 {rec.get('serverName', '')}")
            btn.setFixedSize(26, 26)
            btn.clicked.connect(
                lambda _=False, sn=rec.get("serverName", ""): self._on_disconnect(sn))
            hl.addWidget(btn)
            self._table.setCellWidget(row, len(_HEADERS) - 1, holder)

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

    def _on_disconnect(self, server_name: str):
        """断开单条隧道：移除 visitor 并立即应用（剩余为空时 frpc 自动停止）"""
        if server_name:
            get_session_manager().disconnect_visitor(server_name)

    def _on_stop_all(self):
        """全部断开：清空注册表并停止 frpc"""
        mgr = get_session_manager()
        for rec in list(mgr.records()):
            mgr.remove_visitor(rec.get("serverName", ""))
        try:
            mgr.apply()
        except (OSError, RuntimeError):
            pass
