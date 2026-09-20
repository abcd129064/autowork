# -*- coding: utf-8 -*-
"""远程页 RemoteHub（二期，2026-09-07）——统一远程会话中心

设计稿：design/remote_page_v2.html
形态：与工具页同风格——横排 Pivot 二级切换（无图标），三视图堆叠：
    会话总览 │ P2P 访客 │ 隧道配置
（连接诊断不属本页，2026-09-07 用户定稿：入口保留在 设置-工具）

数据源（后端零改动，全部复用 core/frp_remote.RemoteSessionManager 单例）：
  - 会话总览 = mgr.records() + sessions_on_port() 活跃会话联动
  - P2P 访客 = visitor 注册表增删（register_visitor/persist/delete_visitor）
  - 隧道配置 = settings.frpc_server 嵌套 dict（与 设置-远程连接 同键，
    单点写回）+ frpc 进程控制（apply 重启）+ log_message 实时日志

与设置分工（设计稿）：设置-远程连接 = 凭据/FRP 静态配置；本页 = 会话与
隧道的操作面。frpc 服务器参数两处共享同一 settings 键，避免双写分叉。
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QTableWidgetItem, QAbstractItemView, QHeaderView)
from qfluentwidgets import (TitleLabel, CaptionLabel, BodyLabel, StrongBodyLabel,
                            CardWidget, LineEdit, PasswordLineEdit, PushButton,
                            PrimaryPushButton, ComboBox, SpinBox, TableWidget,
                            ToolButton, MessageBox, FluentIcon, InfoBar,
                            InfoBarPosition)

from main_window.pivot_page import PivotPage
from main_window.tool_hub import _transparent, _make_terminal
from core.frp_remote import (get_session_manager, SOURCE_MANUAL,
                             _FRPC_SERVER_DEFAULTS)

# 语义色（对齐 core/design_tokens：success/warning/danger/info/accent）
_C_SUCCESS = QColor(0x1a, 0x9e, 0x6c)
_C_WARNING = QColor(0xc9, 0x8a, 0x2d)
_C_DANGER = QColor(0xcf, 0x44, 0x52)
_C_ACCENT = QColor(0x00, 0x83, 0x8f)
_C_MUTED = QColor(0x6b, 0x72, 0x80)


def _stat_card(parent, color):
    """统计数字卡（设计稿 stat-strip：大数字 + 小标签）"""
    card = CardWidget(parent)
    v = QVBoxLayout(card)
    v.setContentsMargins(16, 12, 16, 12)
    v.setSpacing(2)
    num = StrongBodyLabel("0", card)
    num.setStyleSheet(f"color: {color.name()}; font-size: 22px; font-weight: 700;")
    lbl = CaptionLabel("", card)
    v.addWidget(num)
    v.addWidget(lbl)
    return card, num, lbl


def _row_buttons(parent, specs):
    """操作列按钮组（specs=[(文本, 回调, tooltip)]），返回容器 widget

    按钮宽度按字体度量自适应（2026-09-20 修复：原默认 sizeHint 在
    大字号/缩放环境下偏窄，「SSH/SFTP/RDP/断开/删除」文字被裁切显示不全）。
    """
    holder = QWidget(parent)
    h = QHBoxLayout(holder)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)
    for text, cb, tip in specs:
        b = PushButton(text, holder)
        b.setFixedHeight(26)
        b.setToolTip(tip)
        b.clicked.connect(cb)
        fm = b.fontMetrics()
        b.setFixedWidth(fm.horizontalAdvance(text) + 24)
        h.addWidget(b)
    return holder


# ==================== 视图 1：会话总览 ====================

class SessionWork(QWidget):
    """会话总览：frpc 状态 + 统计条 + 隧道/会话表（连接/断开/删除入口）"""

    def __init__(self, win, hub, parent=None):
        super().__init__(parent)
        self.setObjectName("remoteSessionWork")
        _transparent(self)
        self._win = win
        self._hub = hub
        self._mgr = get_session_manager()

        body = QWidget(self)
        body.setAutoFillBackground(False)
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 8, 20, 12)
        lay.setSpacing(12)

        # ---------- 状态行 + 快捷操作 ----------
        head = QHBoxLayout()
        head.setSpacing(10)
        self.lbl_frpc = BodyLabel("frpc 未启动", body)
        head.addWidget(self.lbl_frpc)
        head.addStretch(1)
        btn_session_win = PushButton(FluentIcon.DOCUMENT, "会话窗口", body)
        btn_session_win.setToolTip("打开/置顶远程会话标签窗口（SSH/SFTP/RDP）")
        btn_session_win.clicked.connect(lambda: self._mgr.ensure_session_window())
        head.addWidget(btn_session_win)
        btn_add = PrimaryPushButton(FluentIcon.ADD, "新建隧道", body)
        btn_add.setToolTip("切到「P2P 访客」视图注册新隧道")
        btn_add.clicked.connect(lambda: self._hub.switchTo(self._hub.visitor_work))
        head.addWidget(btn_add)
        lay.addLayout(head)

        # ---------- 统计条（4 卡整行铺满，等分） ----------
        strip = QHBoxLayout()
        strip.setSpacing(12)
        self.card_run, self.num_run, self.lbl_run = _stat_card(body, _C_ACCENT)
        self.card_tunnel, self.num_tunnel, self.lbl_tunnel = _stat_card(body, _C_SUCCESS)
        self.card_sess, self.num_sess, self.lbl_sess = _stat_card(body, _C_WARNING)
        self.card_visitor, self.num_visitor, self.lbl_visitor = _stat_card(body, _C_MUTED)
        for c in (self.card_run, self.card_tunnel, self.card_sess, self.card_visitor):
            strip.addWidget(c, 1)
        lay.addLayout(strip)

        # ---------- 隧道/会话表 ----------
        card = CardWidget(body)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)
        self.table = TableWidget(card)
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["状态", "serverName", "关联球桌", "本地端口", "来源", "最近使用", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 96)
        self.table.setColumnWidth(2, 110)
        self.table.setColumnWidth(3, 80)
        self.table.setColumnWidth(4, 96)
        self.table.setColumnWidth(5, 100)
        # 操作列需容纳 5 个自适应宽按钮（SSH/SFTP/RDP/断开/删除 + 间距），
        # 250px 在默认字号下即不足（2026-09-20 字体裁切修复）
        self.table.setColumnWidth(6, 330)
        self.table.setMinimumHeight(260)
        cl.addWidget(self.table, 1)
        lay.addWidget(card, 1)

        self.lbl_hint = CaptionLabel("", body)
        self.lbl_hint.setTextColor(QColor(0, 0, 0, 170), QColor(255, 255, 255, 170))
        lay.addWidget(self.lbl_hint)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(body)

        # 信号联动（bound method，随本 widget 销毁自动断开）
        self._mgr.visitors_changed.connect(self.refresh)
        self._mgr.frpc_state_changed.connect(lambda _b: self.refresh())

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    # ---------- 刷新 ----------

    def refresh(self):
        mgr = self._mgr
        try:
            records = mgr.records()
        except RuntimeError:
            return
        running = mgr.is_running()
        self.lbl_frpc.setText(
            f"frpc {'运行中' if running else '未启动'} · {len(records)} 条隧道")
        self.lbl_frpc.setTextColor(
            _C_SUCCESS if running else _C_MUTED,
            _C_SUCCESS if running else _C_MUTED)

        sess_total = 0
        for rec in records:
            sess_total += len(mgr.sessions_on_port(rec.get("bindPort", 0)))
        self.num_run.setText("运行中" if running else "未启动")
        self.num_run.setStyleSheet(
            f"color: {(_C_SUCCESS if running else _C_MUTED).name()};"
            " font-size: 20px; font-weight: 700;")
        self.lbl_run.setText("frpc 进程")
        self.num_tunnel.setText(str(len(records)))
        self.lbl_tunnel.setText("已注册隧道")
        self.num_sess.setText(str(sess_total))
        self.lbl_sess.setText("打开的会话（SSH/SFTP/RDP）")
        last = max((str(r.get("lastUsed", "") or "") for r in records),
                   default="—")
        self.num_visitor.setText(last or "—")
        self.num_visitor.setStyleSheet(
            f"color: {_C_MUTED.name()}; font-size: 15px; font-weight: 700;")
        self.lbl_visitor.setText("最近使用时间")

        self.table.setRowCount(0)
        for rec in records:
            self._add_row(rec, running)
        if not records:
            self.lbl_hint.setText("暂无隧道 —— 点右上「新建隧道」注册 xtcp visitor")
        else:
            self.lbl_hint.setText(
                "「连接」复用球桌一键直连（SSH/SFTP/RDP 标签窗口）；"
                "断开仅关该隧道会话并释放端口，删除则彻底移除注册与持久化配置")

    def _add_row(self, rec, running):
        mgr = self._mgr
        sn = str(rec.get("serverName", ""))
        port = int(rec.get("bindPort", 0) or 0)
        sessions = mgr.sessions_on_port(port)
        table = self.table
        r = table.rowCount()
        table.insertRow(r)
        if not running:
            status, color = "未启动", _C_MUTED
        elif sessions:
            status, color = "会话中", _C_SUCCESS
        else:
            status, color = "已连接", _C_ACCENT
        it = QTableWidgetItem(status)
        it.setForeground(color)
        table.setItem(r, 0, it)
        for col, text in ((1, sn), (2, rec.get("tableId", "") or "—"),
                          (3, str(port)), (4, rec.get("source", "") or "—"),
                          (5, rec.get("lastUsed", "") or "—")):
            item = QTableWidgetItem(text)
            item.setToolTip(text)
            table.setItem(r, col, item)
        table.setCellWidget(r, 6, _row_buttons(table, [
            ("SSH", lambda _=False, s=sn, t=rec: self._open("ssh", s, t),
             "通过该隧道打开 SSH 终端"),
            ("SFTP", lambda _=False, s=sn, t=rec: self._open("sftp", s, t),
             "通过该隧道打开 SFTP 文件传输"),
            ("RDP", lambda _=False, s=sn, t=rec: self._open("rdp", s, t),
             "通过该隧道打开远程桌面"),
            ("断开", lambda _=False, s=sn: self._disconnect(s),
             f"断开隧道 {sn}：关闭相关会话并释放本地端口"),
            ("删除", lambda _=False, s=sn: self._delete(s),
             f"删除隧道 {sn}：移除注册与持久化配置"),
        ]))

    # ---------- 操作 ----------

    def _open(self, kind, sn, rec):
        table_id = str(rec.get("tableId", "") or "")
        self._mgr.open_session(kind, sn, table_id, notifier=self._win)
        self._win._append_log(f"[远程] 经隧道 {sn} 打开 {kind.upper()} 会话")

    def _confirm_transfer(self, sn):
        rec = next((r for r in self._mgr.records()
                    if r.get("serverName") == sn), None)
        if rec is None:
            return False
        if not self._mgr.is_transferring_on_port(rec.get("bindPort", 0)):
            return True
        dlg = MessageBox(
            "文件传输进行中",
            f"隧道「{sn}」上有 SFTP 文件传输正在进行。\n"
            "继续将立即中断传输并关闭相关会话。确定吗？", self)
        dlg.yesButton.setText("继续")
        dlg.cancelButton.setText("取消")
        return bool(dlg.exec())

    def _disconnect(self, sn):
        mgr = self._mgr
        if not mgr.is_running():
            self._info("当前 frpc 未启动，隧道未建立，无需断开", "warning")
            return
        if not self._confirm_transfer(sn):
            return
        result = mgr.disconnect_visitor(sn)
        if result == "ok":
            self._info(f"已断开隧道 {sn}，相关会话已关闭、端口已释放", "success")
        elif result == "not_running":
            self._info("当前 frpc 未启动", "warning")
        else:
            self._info(f"隧道 {sn} 断开失败", "error")
        self.refresh()

    def _delete(self, sn):
        mgr = self._mgr
        if not self._confirm_transfer(sn):
            return
        dlg = MessageBox(
            "删除隧道",
            f"确定删除隧道「{sn}」吗？\n删除后将从注册表与持久化配置中移除，"
            "下次启动不再恢复。", self)
        dlg.yesButton.setText("删除")
        dlg.cancelButton.setText("取消")
        if not dlg.exec():
            return
        result = mgr.delete_visitor(sn)
        if result == "ok":
            self._info(f"已删除隧道 {sn}", "success")
        else:
            self._info(f"隧道 {sn} 删除失败（{result}）", "error")
        self.refresh()

    def _info(self, msg, kind):
        self._win._show_info_bar(msg, kind, duration=4000)


# ==================== 视图 2：P2P 访客 ====================

class VisitorWork(QWidget):
    """P2P 访客：visitor 注册表 + 添加访客表单（写入统一 TOML 并落盘）"""

    def __init__(self, win, hub, parent=None):
        super().__init__(parent)
        self.setObjectName("remoteVisitorWork")
        _transparent(self)
        self._win = win
        self._hub = hub
        self._mgr = get_session_manager()

        body = QWidget(self)
        body.setAutoFillBackground(False)
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 8, 20, 12)
        lay.setSpacing(12)

        # ---------- 访客表 ----------
        card = CardWidget(body)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(BodyLabel("xtcp visitor 注册表", card))
        head.addStretch(1)
        btn_refresh = ToolButton(FluentIcon.SYNC, card)
        btn_refresh.setToolTip("重新读取注册表")
        btn_refresh.clicked.connect(self.refresh)
        head.addWidget(btn_refresh)
        cl.addLayout(head)

        self.table = TableWidget(card)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["serverName", "类型", "bindPort", "关联球桌", "来源", "最近使用"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 70)
        self.table.setColumnWidth(2, 80)
        self.table.setColumnWidth(3, 110)
        self.table.setColumnWidth(4, 96)
        self.table.setColumnWidth(5, 100)
        self.table.setMinimumHeight(200)
        cl.addWidget(self.table, 1)
        lay.addWidget(card, 1)

        # ---------- 添加访客卡（卡铺满、控件定宽） ----------
        add_card = CardWidget(body)
        al = QVBoxLayout(add_card)
        al.setContentsMargins(16, 14, 16, 14)
        al.setSpacing(10)
        al.addWidget(BodyLabel("添加访客", add_card))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        self.edit_name = LineEdit(add_card)
        self.edit_name.setPlaceholderText("snk 标识（与球桌 frps proxy 同名）")
        self.edit_name.setFixedWidth(260)
        self.edit_key = PasswordLineEdit(add_card)
        self.edit_key.setPlaceholderText("secretKey（留空用默认）")
        self.edit_key.setFixedWidth(200)
        self.spin_port = SpinBox(add_card)
        self.spin_port.setRange(0, 65535)
        self.spin_port.setValue(0)
        self.spin_port.setSpecialValueText("随机")
        self.spin_port.setFixedWidth(130)
        self.edit_table_id = LineEdit(add_card)
        self.edit_table_id.setPlaceholderText("关联球桌号（选填）")
        self.edit_table_id.setFixedWidth(130)
        lbl1 = BodyLabel("serverName:", add_card)
        lbl2 = BodyLabel("secretKey:", add_card)
        lbl3 = BodyLabel("本地端口:", add_card)
        lbl4 = BodyLabel("关联球桌:", add_card)
        grid.addWidget(lbl1, 0, 0)
        grid.addWidget(self.edit_name, 0, 1)
        grid.addWidget(lbl2, 0, 2)
        grid.addWidget(self.edit_key, 0, 3)
        grid.addWidget(lbl3, 1, 0)
        grid.addWidget(self.spin_port, 1, 1, Qt.AlignLeft)
        grid.addWidget(lbl4, 1, 2)
        grid.addWidget(self.edit_table_id, 1, 3, Qt.AlignLeft)
        grid.setColumnStretch(4, 1)
        al.addLayout(grid)

        btns = QHBoxLayout()
        self.btn_add = PrimaryPushButton(FluentIcon.ADD, "添加并注册", add_card)
        self.btn_add.clicked.connect(self._on_add)
        btns.addWidget(self.btn_add)
        self.btn_add_connect = PushButton(FluentIcon.PLAY, "添加并连接 SSH", add_card)
        self.btn_add_connect.clicked.connect(self._on_add_connect)
        btns.addWidget(self.btn_add_connect)
        btns.addStretch(1)
        al.addLayout(btns)
        cap = CaptionLabel(
            "注册仅写入 frpc_xtcp_panel.toml（不拉起 frpc）；「添加并连接」经一键直连建立隧道。",
            add_card)
        cap.setTextColor(QColor(0, 0, 0, 170), QColor(255, 255, 255, 170))
        al.addWidget(cap)
        lay.addWidget(add_card)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(body)

        self._mgr.visitors_changed.connect(self.refresh)
        self._mgr.frpc_state_changed.connect(lambda _b: self.refresh())

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        self.table.setRowCount(0)
        for rec in self._mgr.records():
            r = self.table.rowCount()
            self.table.insertRow(r)
            values = (str(rec.get("serverName", "")), "xtcp",
                      str(rec.get("bindPort", "")),
                      rec.get("tableId", "") or "—",
                      rec.get("source", "") or "—",
                      rec.get("lastUsed", "") or "—")
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if col == 1:
                    item.setForeground(_C_ACCENT)
                self.table.setItem(r, col, item)

    # ---------- 操作 ----------

    def _register(self):
        """表单 → register_visitor + persist；返回 serverName（失败空串）"""
        sn = self.edit_name.text().strip()
        if not sn:
            self._win._show_info_bar("请输入 serverName（snk 标识）", "warning")
            self.edit_name.setFocus()
            return ""
        try:
            port, _changed = self._mgr.register_visitor(
                sn,
                bind_port=self.spin_port.value() or None,
                secret_key=self.edit_key.text().strip() or None,
                source=SOURCE_MANUAL,
                table_id=self.edit_table_id.text().strip())
            self._mgr.persist()
        except (OSError, RuntimeError, ValueError) as e:
            self._win._show_info_bar(f"注册失败：{e}", "error", duration=5000)
            return ""
        self.edit_name.clear()
        self.edit_key.clear()
        self.edit_table_id.clear()
        self.spin_port.setValue(0)
        self.refresh()
        return sn

    def _on_add(self):
        sn = self._register()
        if sn:
            self._win._show_info_bar(f"已注册访客 {sn}", "success")
            self._win._append_log(f"[远程] 注册 visitor {sn}")

    def _on_add_connect(self):
        sn = self._register()
        if not sn:
            return
        rec = next((r for r in self._mgr.records()
                    if r.get("serverName") == sn), {})
        self._mgr.open_session("ssh", sn, str(rec.get("tableId", "") or ""),
                               notifier=self._win)
        self._win._append_log(f"[远程] 注册并连接 visitor {sn}（SSH）")


# ==================== 视图 3：隧道配置 ====================
# 连接诊断不属于本页（2026-09-07 用户定稿）：入口保留在 设置-工具
# 「连接诊断」行（win._on_open_conn_diag 打开 ConnDiagPanel 独立窗口）。

class TunnelConfWork(QWidget):
    """隧道配置：frpc 服务器参数（与设置-远程连接同键）+ frpc 控制 + 实时日志"""

    def __init__(self, win, hub, parent=None):
        super().__init__(parent)
        self.setObjectName("remoteTunnelConfWork")
        _transparent(self)
        self._win = win
        self._hub = hub
        self._mgr = get_session_manager()

        body = QWidget(self)
        body.setAutoFillBackground(False)
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 8, 20, 12)
        lay.setSpacing(12)

        # ---------- frpc 服务器卡 ----------
        card = CardWidget(body)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(BodyLabel("frpc 服务器", card))
        cap0 = CaptionLabel("所有 visitor 共享的 frps 接入点（与 设置-远程连接 同键）", card)
        cap0.setTextColor(QColor(0, 0, 0, 170), QColor(255, 255, 255, 170))
        head.addWidget(cap0)
        head.addStretch(1)
        self.lbl_state = CaptionLabel("", card)
        head.addWidget(self.lbl_state)
        cl.addLayout(head)

        frpc = dict(self._win._load_settings().get("frpc_server", {}) or {})
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self.edit_addr = LineEdit(card)
        self.edit_addr.setText(str(frpc.get("serverAddr",
                                            _FRPC_SERVER_DEFAULTS["serverAddr"])))
        self.edit_addr.setFixedWidth(200)
        self.spin_port = SpinBox(card)
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(int(frpc.get("serverPort",
                                             _FRPC_SERVER_DEFAULTS["serverPort"])))
        self.spin_port.setFixedWidth(130)
        self.combo_auth = ComboBox(card)
        self.combo_auth.addItems(["token", "none"])
        self.combo_auth.setCurrentText(
            str(frpc.get("auth_method", "token") or "token"))
        self.combo_auth.setFixedWidth(120)
        self.edit_token = PasswordLineEdit(card)
        self.edit_token.setText(str(frpc.get("auth_token", "") or ""))
        self.edit_token.setPlaceholderText("认证 Token")
        self.edit_token.setFixedWidth(200)
        grid.addWidget(BodyLabel("serverAddr:", card), 0, 0)
        grid.addWidget(self.edit_addr, 0, 1)
        grid.addWidget(BodyLabel("serverPort:", card), 0, 2)
        grid.addWidget(self.spin_port, 0, 3, Qt.AlignLeft)
        grid.addWidget(BodyLabel("auth.method:", card), 1, 0)
        grid.addWidget(self.combo_auth, 1, 1, Qt.AlignLeft)
        grid.addWidget(BodyLabel("auth.token:", card), 1, 2)
        grid.addWidget(self.edit_token, 1, 3)
        grid.setColumnStretch(4, 1)
        cl.addLayout(grid)

        btns = QHBoxLayout()
        self.btn_save = PrimaryPushButton(FluentIcon.SAVE, "保存并重连", card)
        self.btn_save.setToolTip("写回 settings.frpc_server；frpc 运行中则按新配置重启")
        self.btn_save.clicked.connect(self._on_save)
        btns.addWidget(self.btn_save)
        self.btn_restart = PushButton(FluentIcon.SYNC, "重启 frpc", card)
        self.btn_restart.setToolTip("按注册表与当前配置重写 TOML 并重启 frpc")
        self.btn_restart.clicked.connect(self._on_restart)
        btns.addWidget(self.btn_restart)
        self.btn_stop = PushButton(FluentIcon.POWER_BUTTON, "停止 frpc", card)
        self.btn_stop.setToolTip("停止 frpc（隧道全部失效；注册表保留）")
        self.btn_stop.clicked.connect(self._on_stop)
        btns.addWidget(self.btn_stop)
        btns.addStretch(1)
        cl.addLayout(btns)
        lay.addWidget(card)

        # ---------- 运行日志卡（整宽，占满剩余空间） ----------
        out_card = CardWidget(body)
        ol = QVBoxLayout(out_card)
        ol.setContentsMargins(16, 14, 16, 14)
        ol.setSpacing(8)
        head2 = QHBoxLayout()
        head2.addWidget(CaptionLabel("frpc 运行日志:", out_card))
        head2.addStretch(1)
        btn_clear = ToolButton(FluentIcon.DELETE, out_card)
        btn_clear.setToolTip("清空日志显示")
        btn_clear.clicked.connect(lambda: self.log_view.clear())
        head2.addWidget(btn_clear)
        ol.addLayout(head2)
        self.log_view = _make_terminal(out_card, None)
        ol.addWidget(self.log_view, 1)
        lay.addWidget(out_card, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(body)

        self._mgr.log_message.connect(self._append_log)
        self._mgr.frpc_state_changed.connect(lambda _b: self._update_state())
        self._update_state()

    def showEvent(self, event):
        super().showEvent(event)
        self._update_state()

    def _update_state(self):
        running = self._mgr.is_running()
        self.lbl_state.setText("● frpc 运行中" if running else "○ frpc 未启动")
        self.lbl_state.setTextColor(
            _C_SUCCESS if running else _C_MUTED,
            _C_SUCCESS if running else _C_MUTED)
        self.btn_stop.setEnabled(running)
        self.btn_restart.setEnabled(running or bool(self._mgr.records()))

    def _append_log(self, msg):
        self.log_view.append(str(msg).rstrip())

    # ---------- 操作 ----------

    def _collect_frpc(self):
        merged = dict(self._win._load_settings().get("frpc_server", {}) or {})
        merged.update({
            "serverAddr": self.edit_addr.text().strip(),
            "serverPort": int(self.spin_port.value()),
            "auth_method": self.combo_auth.currentText(),
            "auth_token": self.edit_token.text(),
        })
        return merged

    def _on_save(self):
        if not self.edit_addr.text().strip():
            self._win._show_info_bar("serverAddr 不能为空", "warning")
            return
        self._win._save_settings({"frpc_server": self._collect_frpc()})
        self._win._append_log("[远程] frpc 服务器配置已保存")
        if self._mgr.is_running() and self._mgr.records():
            self._apply_frpc("配置已保存，frpc 按新参数重启")
        else:
            self._win._show_info_bar("配置已保存（frpc 未运行，下次连接生效）",
                                     "success")

    def _on_restart(self):
        if not self._mgr.records():
            self._win._show_info_bar("无已注册隧道，无需启动 frpc", "warning")
            return
        self._apply_frpc("frpc 已按注册表重启")

    def _apply_frpc(self, ok_msg):
        try:
            self._mgr.apply()
        except (OSError, RuntimeError) as e:
            self._win._show_info_bar(f"frpc 启动失败：{e}", "error", duration=6000)
            return
        self._win._show_info_bar(ok_msg, "success")

    def _on_stop(self):
        dlg = MessageBox("停止 frpc",
                         "停止后所有隧道失效，已打开的 SSH/SFTP/RDP 会话将断开。\n"
                         "注册表保留，下次连接或重启 frpc 可恢复。确定停止吗？", self)
        dlg.yesButton.setText("停止")
        dlg.cancelButton.setText("取消")
        if not dlg.exec():
            return
        self._mgr.close_all_sessions("手动停止 frpc")
        # 空注册表 apply 会停掉 frpc；这里直接走内部停止：先清进程再 apply
        records = list(self._mgr.records())
        for rec in records:
            self._mgr.remove_visitor(rec.get("serverName", ""))
        try:
            self._mgr.apply()  # 注册表为空 → _stop_frpc
        except (OSError, RuntimeError):
            pass
        # 恢复注册表（仅停进程，不丢配置）
        for rec in records:
            self._mgr.register_visitor(
                rec.get("serverName", ""), bind_port=rec.get("bindPort"),
                secret_key=rec.get("secretKey"), source=rec.get("source"),
                table_id=rec.get("tableId", ""))
        self._mgr.persist()
        self._update_state()
        self._win._show_info_bar("frpc 已停止（注册表保留）", "success")
        self._win._append_log("[远程] 手动停止 frpc")


# ==================== RemoteHub 容器 ====================

class RemoteHub(PivotPage):
    """远程页：横排 Pivot 三视图（与会话中心单例实时联动）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("remoteHub")
        self._win = parent

        self.session_work = SessionWork(self._win, self, self)
        self.visitor_work = VisitorWork(self._win, self, self)
        self.tunnel_conf_work = TunnelConfWork(self._win, self, self)

        self.addPage(self.session_work, "会话总览")
        self.addPage(self.visitor_work, "P2P 访客")
        self.addPage(self.tunnel_conf_work, "隧道配置")
        self.lock_pivot_width()
        self.switchTo(self.session_work)

    def _apply_table_smooth_all(self):
        """统一设置页平滑开关联动：本页两张表"""
        from core.perf import apply_table_smooth_mode
        for t in (self.session_work.table, self.visitor_work.table):
            apply_table_smooth_mode(t, panel="remote")
