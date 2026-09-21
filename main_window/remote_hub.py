# -*- coding: utf-8 -*-
"""远程页 RemoteHub（二期，2026-09-21 重构）——统一远程会话中心

设计稿：design/remote_page_v3_065.html（frps 0.65 能力锁定版）
形态：与工具页同风格——横排 Pivot 二级切换（无图标），四视图堆叠：
    会话总览 │ P2P 访客 │ 连接质量 │ 隧道配置
（连接诊断不属本页，2026-09-07 用户定稿：入口保留在 设置-工具）

数据源：
  - 会话总览 = mgr.records() + sessions_on_port() 活跃会话联动
             + frps 在线感知（core/frps_admin，P0：proxy status=online/offline）
  - P2P 访客 = visitor 注册表增删（register_visitor/persist/delete_visitor）
  - 连接质量 = core/visitor_probe（P1：本地 bindPort TCP connect RTT，
               30s 一轮串行；sparkline 趋势 + 评级）
  - 隧道配置 = settings.frpc_server 嵌套 dict（与 设置-远程连接 同键，
    单点写回）+ frpc 进程控制（apply 热重载 / /api/stop 优雅停止 P1）
    + 管理通道卡（frps_admin credentials 配置 + frpc admin 自检）+ 实时日志

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
from core.frps_admin import get_frps_client
from core.visitor_probe import get_prober

# 语义色（对齐 core/design_tokens：success/warning/danger/info/accent）
_C_SUCCESS = QColor(0x1a, 0x9e, 0x6c)
_C_WARNING = QColor(0xc9, 0x8a, 0x2d)
_C_DANGER = QColor(0xcf, 0x44, 0x52)
_C_ACCENT = QColor(0x00, 0x83, 0x8f)
_C_MUTED = QColor(0x6b, 0x72, 0x80)
_C_INFO = QColor(0x2f, 0x6b, 0xd6)

# frps 感知四态 → 展示文案/颜色（None=感知不可用，绝不阻塞连接）
_FRPS_CELL = {
    "online": ("● online", _C_SUCCESS),
    "offline": ("● offline", _C_DANGER),
    "unregistered": ("○ 未注册", _C_WARNING),
    None: ("— 无感知", _C_MUTED),
}

# 质量评级 → 文案/颜色（core.visitor_probe grade 口径）
_GRADE_CELL = {
    "good": ("优", _C_SUCCESS), "nice": ("良", _C_INFO),
    "fair": ("一般", _C_WARNING), "poor": ("差", _C_WARNING),
    "bad": ("异常", _C_DANGER), "unknown": ("—", _C_MUTED),
}

_CHANNEL_TEXT = {
    "ok": "frps 感知通道 在线",
    "unreachable": "frps 感知通道 不可达",
    "unauthorized": "frps 感知通道 凭据被拒",
    "error": "frps 感知通道 响应异常",
    "unconfigured": "frps 感知通道 未配置",
}


def _frps_cell(snk: str) -> tuple:
    """snk → (状态文案, 颜色)，缓存不可信时 online() 返回 None 即「无感知」"""
    return _FRPS_CELL.get(get_frps_client().online(snk), _FRPS_CELL[None])


def _fmt_traffic(nbytes: int) -> str:
    if not nbytes:
        return "0"
    if nbytes >= 1048576:
        return f"{nbytes / 1048576:.1f}MB"
    return f"{nbytes / 1024:.0f}KB"


def _spark(vals, width: int = 12) -> str:
    """RTT 序列 → 字符小 histogram（▁▂▄▆█，超时=·），零绘制成本的趋势可视化"""
    blocks = "▁▂▄▆█"
    out = []
    good = [v for v in vals if v is not None]
    lo = min(good) if good else 0
    hi = max(good) if good else 1
    span = max(1.0, hi - lo)
    for v in list(vals)[-width:]:
        if v is None:
            out.append("·")
        else:
            idx = int((v - lo) / span * (len(blocks) - 1) + 0.5)
            out.append(blocks[idx])
    return "".join(out)


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
    for spec in specs:
        text, cb, tip = spec[:3]
        enabled = spec[3] if len(spec) > 3 else True
        b = PushButton(text, holder)
        b.setFixedHeight(26)
        b.setToolTip(tip if enabled else f"{tip}（当前不可用）")
        b.clicked.connect(cb)
        b.setEnabled(enabled)
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
        self.lbl_frps = CaptionLabel("", body)
        head.addWidget(self.lbl_frps)
        head.addStretch(1)
        btn_probe = PushButton(FluentIcon.SYNC, "立即感知", body)
        btn_probe.setToolTip("立即拉取 frps xtcp proxy 名单（在线感知）")
        btn_probe.clicked.connect(self._on_probe_now)
        head.addWidget(btn_probe)
        btn_session_win = PushButton(FluentIcon.DOCUMENT, "会话窗口", body)
        btn_session_win.setToolTip("打开/置顶远程会话标签窗口（SSH/SFTP/RDP）")
        btn_session_win.clicked.connect(lambda: self._mgr.ensure_session_window())
        head.addWidget(btn_session_win)
        btn_add = PrimaryPushButton(FluentIcon.ADD, "新建隧道", body)
        btn_add.setToolTip("切到「P2P 访客」视图注册新隧道")
        btn_add.clicked.connect(lambda: self._hub.switchTo(self._hub.visitor_work))
        head.addWidget(btn_add)
        lay.addLayout(head)

        # ---------- 统计条（5 卡整行铺满，等分） ----------
        strip = QHBoxLayout()
        strip.setSpacing(12)
        self.card_run, self.num_run, self.lbl_run = _stat_card(body, _C_ACCENT)
        self.card_tunnel, self.num_tunnel, self.lbl_tunnel = _stat_card(body, _C_SUCCESS)
        self.card_online, self.num_online, self.lbl_online = _stat_card(body, _C_SUCCESS)
        self.card_sess, self.num_sess, self.lbl_sess = _stat_card(body, _C_WARNING)
        self.card_rtt, self.num_rtt, self.lbl_rtt = _stat_card(body, _C_INFO)
        for c in (self.card_run, self.card_tunnel, self.card_online,
                  self.card_sess, self.card_rtt):
            strip.addWidget(c, 1)
        lay.addLayout(strip)

        # ---------- 隧道/会话表 ----------
        card = CardWidget(body)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)
        self.table = TableWidget(card)
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            ["状态", "frps 在线", "serverName", "关联球桌", "本地端口",
             "RTT / 质量", "今日流量", "来源", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 96)
        self.table.setColumnWidth(1, 92)
        self.table.setColumnWidth(3, 96)
        self.table.setColumnWidth(4, 80)
        self.table.setColumnWidth(5, 170)
        self.table.setColumnWidth(6, 108)
        self.table.setColumnWidth(7, 90)
        # 操作列需容纳 5 个自适应宽按钮（SSH/SFTP/RDP/断开/删除 + 间距），
        # 250px 在默认字号下即不足（2026-09-20 字体裁切修复）
        self.table.setColumnWidth(8, 330)
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
        self._frps = get_frps_client()
        self._frps.proxies_changed.connect(lambda _d: self.refresh())
        self._frps.channel_state_changed.connect(lambda _s: self.refresh())
        self._prober = get_prober()
        self._prober.changed.connect(self._on_probe_round)

    def _on_probe_round(self):
        # 质量视图非当前页时不整表重绘（探测每 30s 一轮，成本可忽略但保持克制）
        if self.isVisible():
            self.refresh()

    def _on_probe_now(self):
        # 异步感知（后台线程），结果经 channel_state_changed/proxies_changed 回流刷新
        self._frps.request_refresh()
        self._win._show_info_bar("已发起 frps 感知请求", "info", duration=2500)

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
        snap = self._frps.snapshot()
        self.lbl_frps.setText(
            _CHANNEL_TEXT.get(snap["state"], snap["state"])
            + (f" · {snap['age_sec']}s 前" if snap["state"] == "ok" else ""))
        _scolor = {"ok": _C_SUCCESS, "unconfigured": _C_MUTED}.get(
            snap["state"], _C_WARNING)
        self.lbl_frps.setTextColor(_scolor, _scolor)

        sess_total = 0
        online_total = 0
        rtts = []
        for rec in records:
            sess_total += len(mgr.sessions_on_port(rec.get("bindPort", 0)))
            if self._frps.online(rec.get("serverName", "")) == "online":
                online_total += 1
            st = self._prober.stats(rec.get("serverName", ""))
            if st["avg_ms"] is not None:
                rtts.append(st["avg_ms"])
        self.num_run.setText("运行中" if running else "未启动")
        self.num_run.setStyleSheet(
            f"color: {(_C_SUCCESS if running else _C_MUTED).name()};"
            " font-size: 20px; font-weight: 700;")
        self.lbl_run.setText("frpc 进程")
        self.num_tunnel.setText(str(len(records)))
        self.lbl_tunnel.setText("已注册隧道")
        # 设备在线卡：感知不可用时显示 —（绝不显示 0/N 误导为全部离线）
        if snap["state"] == "ok" and snap["fresh"]:
            self.num_online.setText(f"{online_total}")
            self.num_online.setStyleSheet(
                f"color: {_C_SUCCESS.name()}; font-size: 22px; font-weight: 700;")
            self.lbl_online.setText(f"设备在线 / {len(records)} 隧道（frps 权威）")
        else:
            self.num_online.setText("—")
            self.num_online.setStyleSheet(
                f"color: {_C_MUTED.name()}; font-size: 22px; font-weight: 700;")
            self.lbl_online.setText("设备在线（感知未启用）")
        self.num_sess.setText(str(sess_total))
        self.lbl_sess.setText("打开的会话（SSH/SFTP/RDP）")
        if rtts:
            rtts.sort()
            self.num_rtt.setText(f"{int(rtts[len(rtts) // 2])}ms")
            self.num_rtt.setStyleSheet(
                f"color: {_C_INFO.name()}; font-size: 22px; font-weight: 700;")
            self.lbl_rtt.setText(f"RTT 中位数（{len(rtts)} 隧道有样本）")
        else:
            self.num_rtt.setText("—")
            self.num_rtt.setStyleSheet(
                f"color: {_C_MUTED.name()}; font-size: 22px; font-weight: 700;")
            self.lbl_rtt.setText("RTT 中位数（探测积累中）")

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

        # frps 在线感知列（P0）：online/offline/未注册/无感知 四态
        frps_text, frps_color = _frps_cell(sn)
        it_frps = QTableWidgetItem(frps_text)
        it_frps.setForeground(frps_color)
        info = self._frps.info(sn)
        if info:
            it_frps.setToolTip(
                f"当前连接 {info['curConns']} · 最近上线 {info['lastStartTime'] or '—'}")
        table.setItem(r, 1, it_frps)

        # RTT / 质量列（P1）：均值 + 评级 + 迷你趋势（探测样本 ≥4 点才画 spark）
        st = self._prober.stats(sn)
        g_text, g_color = _GRADE_CELL.get(st["grade"], _GRADE_CELL["unknown"])
        avg = st["avg_ms"]
        rtt_text = (f"{avg:.0f}ms {g_text}" if avg is not None else f"— {g_text}")
        spark = _spark(st["recent"]) if st["samples"] >= 4 else ""
        it_rtt = QTableWidgetItem(f"{rtt_text} {spark}".rstrip())
        it_rtt.setForeground(g_color if avg is not None else _C_MUTED)
        p95 = st["p95_ms"]
        it_rtt.setToolTip(
            f"样本 {st['samples']}（成功 {st['ok']}） · "
            f"P95 {('%.0fms' % p95) if p95 is not None else '—'}")
        table.setItem(r, 5, it_rtt)

        # 今日流量列：frps 侧 xtcp proxy 统计（感知可用时）
        if info:
            traffic = f"↓{_fmt_traffic(info['todayTrafficIn'])} ↑{_fmt_traffic(info['todayTrafficOut'])}"
        else:
            traffic = "—"
        it_tr = QTableWidgetItem(traffic)
        if not info:
            it_tr.setForeground(_C_MUTED)
        table.setItem(r, 6, it_tr)

        for col, text in ((2, sn), (3, rec.get("tableId", "") or "—"),
                          (4, str(port)), (7, rec.get("source", "") or "—")):
            item = QTableWidgetItem(text)
            item.setToolTip(text)
            table.setItem(r, col, item)

        # 设备确认离线（frps 权威）时禁用打开类按钮——点击只会盲等超时；
        # 无感知（None）/未注册（visitor 可先于设备上线注册）不禁用
        confirmed_offline = self._frps.online(sn) == "offline"
        table.setCellWidget(r, 8, _row_buttons(table, [
            ("SSH", lambda _=False, s=sn, t=rec: self._open("ssh", s, t),
             "通过该隧道打开 SSH 终端", not confirmed_offline),
            ("SFTP", lambda _=False, s=sn, t=rec: self._open("sftp", s, t),
             "通过该隧道打开 SFTP 文件传输", not confirmed_offline),
            ("RDP", lambda _=False, s=sn, t=rec: self._open("rdp", s, t),
             "通过该隧道打开远程桌面", not confirmed_offline),
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
            # 关键（2026-09-22 真机 bug）：frpc 已在运行时，注册只写了 TOML、
            # 新 visitor 不会自动进进程——必须热重载 apply，否则本地 bindPort
            # 根本没监听，随后「连接」复用注册表端口直连即被拒（snk_4007@17569）。
            # 未运行则不 apply（保持「添加并注册」不拉起 frpc 的语义，
            # 连接时 ensure_visitor 自会冷启动）。
            if self._mgr.is_running():
                self._mgr.apply()
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


# ==================== 视图 3：连接质量（二期 P1） ====================

class QualityWork(QWidget):
    """连接质量：visitor RTT 统计卡 + 明细表（sparkline 趋势）

    数据源 core/visitor_probe.VisitorProber（本地 bindPort TCP connect
    计时）+ frps 感知交叉验证（frps online 但 RTT 全超时 = 打洞异常）。
    """

    def __init__(self, win, hub, parent=None):
        super().__init__(parent)
        self.setObjectName("remoteQualityWork")
        _transparent(self)
        self._win = win
        self._hub = hub
        self._mgr = get_session_manager()
        self._prober = get_prober()
        self._frps = get_frps_client()

        body = QWidget(self)
        body.setAutoFillBackground(False)
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 8, 20, 12)
        lay.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        self.lbl_probe = BodyLabel("探测未启动", body)
        head.addWidget(self.lbl_probe)
        head.addStretch(1)
        btn_now = PushButton(FluentIcon.PLAY, "立即探测一轮", body)
        btn_now.setToolTip("对全部隧道串行做一轮 RTT 探测")
        btn_now.clicked.connect(self._on_probe_once)
        head.addWidget(btn_now)
        btn_refresh = PushButton(FluentIcon.SYNC, "刷新感知", body)
        btn_refresh.setToolTip("重新拉取 frps xtcp proxy 名单（后台线程，不阻塞界面）")
        btn_refresh.clicked.connect(lambda: self._frps.request_refresh())
        head.addWidget(btn_refresh)
        lay.addLayout(head)

        strip = QHBoxLayout()
        strip.setSpacing(12)
        self.card_probe, self.num_probe, self.lbl_probe_c = _stat_card(body, _C_ACCENT)
        self.card_med, self.num_med, self.lbl_med = _stat_card(body, _C_INFO)
        self.card_good, self.num_good, self.lbl_good = _stat_card(body, _C_SUCCESS)
        self.card_bad, self.num_bad, self.lbl_bad = _stat_card(body, _C_DANGER)
        for c in (self.card_probe, self.card_med, self.card_good, self.card_bad):
            strip.addWidget(c, 1)
        lay.addLayout(strip)

        card = CardWidget(body)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)
        self.table = TableWidget(card)
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ["serverName", "关联球桌", "frps 在线", "RTT 均值", "P95",
             "成功率", "趋势（近 12 次）", "本地端口"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 96)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 92)
        self.table.setColumnWidth(4, 80)
        self.table.setColumnWidth(5, 84)
        self.table.setColumnWidth(6, 150)
        self.table.setColumnWidth(7, 80)
        self.table.setMinimumHeight(260)
        self.table.cellDoubleClicked.connect(self._on_row_activated)
        cl.addWidget(self.table, 1)
        lay.addWidget(card, 1)

        self.lbl_hint = CaptionLabel(
            "RTT = 对隧道本地端口 TCP 建连耗时（XTCP 打洞+握手全含），≈ 你按 SSH 的回连体感；"
            "双击行可直接打开 SSH 会话", body)
        self.lbl_hint.setTextColor(QColor(0, 0, 0, 170), QColor(255, 255, 255, 170))
        lay.addWidget(self.lbl_hint)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(body)

        self._prober.changed.connect(self._on_round)
        self._frps.proxies_changed.connect(lambda _d: self._on_round())
        self._mgr.frpc_state_changed.connect(lambda _b: self.refresh())
        self._mgr.visitors_changed.connect(self.refresh)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def _on_round(self):
        if self.isVisible():
            self.refresh()

    def _on_probe_once(self):
        # 后台线程串行探测，主线程只刷 UI（run_once 幂等、耗时=隧道数×≤800ms）
        import threading
        threading.Thread(target=self._prober.run_once, daemon=True,
                         name="visitor-probe-manual").start()
        self._win._show_info_bar("已发起一轮 RTT 探测", "info", duration=3000)

    def _on_row_activated(self, row, _col):
        item = self.table.item(row, 0)
        if item is None:
            return
        sn = item.text()
        rec = next((r for r in self._mgr.records()
                    if r.get("serverName") == sn), None)
        if rec is not None:
            self._mgr.open_session("ssh", sn, str(rec.get("tableId", "") or ""),
                                   notifier=self._win)

    def refresh(self):
        try:
            records = self._mgr.records()
        except RuntimeError:
            return
        running = self._mgr.is_running()
        q_on = self._prober.running
        self.lbl_probe.setText(
            ("探测运行中 · 每 30s 一轮" if q_on else "探测未启动")
            + (" · frpc 运行中" if running else " · frpc 未启动"))
        self.lbl_probe.setTextColor(_C_SUCCESS if q_on and running else _C_MUTED,
                                    _C_SUCCESS if q_on and running else _C_MUTED)

        rows, rtts = [], []
        good_n = bad_n = 0
        for rec in records:
            sn = str(rec.get("serverName", ""))
            st = self._prober.stats(sn)
            rows.append((rec, st))
            if st["avg_ms"] is not None:
                rtts.append(st["avg_ms"])
            if st["grade"] in ("good", "nice", "fair"):
                good_n += 1
            elif st["grade"] == "bad":
                bad_n += 1

        self.num_probe.setText("运行中" if q_on and running else "未启动")
        self.num_probe.setStyleSheet(
            f"color: {(_C_SUCCESS if q_on and running else _C_MUTED).name()};"
            " font-size: 20px; font-weight: 700;")
        self.lbl_probe_c.setText("RTT 探测")
        if rtts:
            rtts.sort()
            self.num_med.setText(f"{int(rtts[len(rtts) // 2])}ms")
            self.num_med.setStyleSheet(
                f"color: {_C_INFO.name()}; font-size: 22px; font-weight: 700;")
            self.lbl_med.setText(f"RTT 中位数（{len(rtts)} 隧道有样本）")
        else:
            self.num_med.setText("—")
            self.num_med.setStyleSheet(
                f"color: {_C_MUTED.name()}; font-size: 22px; font-weight: 700;")
            self.lbl_med.setText("RTT 中位数（等待样本）")
        self.num_good.setText(str(good_n))
        self.lbl_good.setText("质量良好（优/良/一般）")
        self.num_bad.setText(str(bad_n))
        self.lbl_bad.setText("连续超时（在线但不可达）")

        table = self.table
        table.setRowCount(0)
        for rec, st in rows:
            sn = str(rec.get("serverName", ""))
            r = table.rowCount()
            table.insertRow(r)
            it = QTableWidgetItem(sn)
            it.setToolTip(sn)
            table.setItem(r, 0, it)
            item = QTableWidgetItem(str(rec.get("tableId", "") or "—"))
            table.setItem(r, 1, item)
            ftext, fcolor = _frps_cell(sn)
            it_f = QTableWidgetItem(ftext)
            it_f.setForeground(fcolor)
            table.setItem(r, 2, it_f)
            g_text, g_color = _GRADE_CELL.get(st["grade"], _GRADE_CELL["unknown"])
            avg = st["avg_ms"]
            it_a = QTableWidgetItem(f"{avg:.0f}ms {g_text}" if avg is not None
                                    else f"— {g_text}")
            it_a.setForeground(g_color if avg is not None else _C_MUTED)
            table.setItem(r, 3, it_a)
            p95 = st["p95_ms"]
            table.setItem(r, 4, QTableWidgetItem(
                f"{p95:.0f}ms" if p95 is not None else "—"))
            rate = ("%.0f%%" % (st["ok"] / st["samples"] * 100)
                    if st["samples"] else "—")
            it_rate = QTableWidgetItem(rate)
            if st["samples"] and st["ok"] < st["samples"]:
                it_rate.setForeground(_C_WARNING)
            table.setItem(r, 5, it_rate)
            spark = _spark(st["recent"], 12) if st["samples"] else "—"
            it_s = QTableWidgetItem(spark)
            it_s.setForeground(g_color)
            table.setItem(r, 6, it_s)
            table.setItem(r, 7, QTableWidgetItem(str(rec.get("bindPort", "") or "—")))


# ==================== 视图 4：隧道配置 ====================
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
        self.btn_stop = PushButton(FluentIcon.POWER_BUTTON, "优雅停止 frpc", card)
        self.btn_stop.setToolTip(
            "POST /api/stop 让 frpc 自行关闭控制连接与监听端口后退出；\n"
            "2.5s 未退出才兜底强杀（修复 kill 强杀的端口 TIME_WAIT/TOML 半写残留）")
        self.btn_stop.clicked.connect(self._on_stop)
        btns.addWidget(self.btn_stop)
        btns.addStretch(1)
        cl.addLayout(btns)
        lay.addWidget(card)

        # ---------- 管理通道卡（二期 P0/P1：frps 感知 + frpc admin） ----------
        mcard = CardWidget(body)
        ml = QVBoxLayout(mcard)
        ml.setContentsMargins(16, 14, 16, 14)
        ml.setSpacing(10)
        mhead = QHBoxLayout()
        mhead.addWidget(BodyLabel("管理通道", mcard))
        cap1 = CaptionLabel("frps 在线感知（只读 GET）+ 本机 frpc admin API", mcard)
        cap1.setTextColor(QColor(0, 0, 0, 170), QColor(255, 255, 255, 170))
        mhead.addWidget(cap1)
        mhead.addStretch(1)
        self.lbl_channel = CaptionLabel("", mcard)
        mhead.addWidget(self.lbl_channel)
        ml.addLayout(mhead)

        from core import app_settings as _as
        fcfg = _as.get("frps_admin") or {}
        mgrid = QGridLayout()
        mgrid.setHorizontalSpacing(12)
        mgrid.setVerticalSpacing(8)
        self.edit_base = LineEdit(mcard)
        self.edit_base.setPlaceholderText("http://frps-host:7500")
        self.edit_base.setText(str(fcfg.get("base_url") or ""))
        self.edit_base.setFixedWidth(280)
        self.edit_user = LineEdit(mcard)
        self.edit_user.setPlaceholderText("webServer.user")
        self.edit_user.setText(str(fcfg.get("user") or ""))
        self.edit_user.setFixedWidth(160)
        self.edit_pwd = PasswordLineEdit(mcard)
        self.edit_pwd.setPlaceholderText("webServer.password")
        self.edit_pwd.setText(str(fcfg.get("password") or ""))
        self.edit_pwd.setFixedWidth(200)
        mgrid.addWidget(BodyLabel("frps 面板 URL:", mcard), 0, 0)
        mgrid.addWidget(self.edit_base, 0, 1)
        mgrid.addWidget(BodyLabel("用户:", mcard), 0, 2)
        mgrid.addWidget(self.edit_user, 0, 3, Qt.AlignLeft)
        mgrid.addWidget(BodyLabel("口令:", mcard), 1, 0)
        mgrid.addWidget(self.edit_pwd, 1, 1)
        mgrid.setColumnStretch(4, 1)
        ml.addLayout(mgrid)

        mbtns = QHBoxLayout()
        self.btn_ch_save = PrimaryPushButton(FluentIcon.SAVE, "保存感知配置", mcard)
        self.btn_ch_save.setToolTip("写入 credentials 域（口令 DPAPI 加密）并立即感知一次")
        self.btn_ch_save.clicked.connect(self._on_channel_save)
        mbtns.addWidget(self.btn_ch_save)
        self.btn_ch_test = PushButton(FluentIcon.SYNC, "测试连接", mcard)
        self.btn_ch_test.setToolTip("GET /api/proxy/xtcp 验证 URL 与凭据")
        self.btn_ch_test.clicked.connect(self._on_channel_test)
        mbtns.addWidget(self.btn_ch_test)
        self.btn_frpc_health = PushButton(FluentIcon.INFO, "frpc 通道自检", mcard)
        self.btn_frpc_health.setToolTip(
            "对 127.0.0.1:{admin_port} 依次 GET /healthz 与 /api/status，验证本机管理通道")
        self.btn_frpc_health.clicked.connect(self._on_frpc_health)
        mbtns.addWidget(self.btn_frpc_health)
        mbtns.addStretch(1)
        self.chk_quality = PushButton(FluentIcon.CANCEL, "暂停质量探测", mcard)
        self.chk_quality.setToolTip("后台 RTT 探测线程开/关（每 30s 一轮，默认开启）")
        self.chk_quality.clicked.connect(self._on_toggle_quality)
        mbtns.addWidget(self.chk_quality)
        ml.addLayout(mbtns)
        cap2 = CaptionLabel(
            "感知通道仅发只读 GET，不触碰 frps 写端点；连续 3 次失败熔断 60s。"
            "frps 需开启 webServer（dashboard）并配置 BasicAuth。", mcard)
        cap2.setTextColor(QColor(0, 0, 0, 170), QColor(255, 255, 255, 170))
        ml.addWidget(cap2)
        lay.addWidget(mcard)

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
        self._frps = get_frps_client()
        self._frps.channel_state_changed.connect(lambda _s: self._update_state())
        self._prober = get_prober()
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
        snap = self._frps.snapshot()
        ch_text = _CHANNEL_TEXT.get(snap["state"], snap["state"])
        if snap["state"] == "ok" and snap["age_sec"] is not None:
            ch_text += f" · {snap['age_sec']}s 前刷新"
        ch_text += f"　|　frpc admin :{self._mgr.admin_port}"
        self.lbl_channel.setText(ch_text)
        self.lbl_channel.setTextColor(
            {"ok": _C_SUCCESS, "unconfigured": _C_MUTED}.get(
                snap["state"], _C_WARNING),
            {"ok": _C_SUCCESS, "unconfigured": _C_MUTED}.get(
                snap["state"], _C_WARNING))
        self.chk_quality.setText(
            "暂停质量探测" if self._prober.running else "启动质量探测")

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
        dlg = MessageBox("优雅停止 frpc",
                         "停止后所有隧道失效，已打开的 SSH/SFTP/RDP 会话将断开。\n"
                         "先经 /api/stop 让 frpc 自行收尾（端口干净释放），"
                         "2.5s 未退出才强杀。\n注册表保留，下次连接或重启 frpc 可恢复。"
                         "确定停止吗？", self)
        dlg.yesButton.setText("停止")
        dlg.cancelButton.setText("取消")
        if not dlg.exec():
            return
        self._mgr.close_all_sessions("手动停止 frpc")
        # 二期 P1：直接走两段式优雅停止（不再「清空注册表→apply→还原」绕行，
        # 避免注册表抖动引发 visitors_changed 重绘与持久化竞态）
        self._mgr.stop_frpc()
        self._update_state()
        self._win._show_info_bar("frpc 已停止（注册表保留）", "success")
        self._win._append_log("[远程] 手动优雅停止 frpc")

    # ---------- 管理通道操作 ----------

    def _on_channel_save(self):
        from core import app_settings
        base = self.edit_base.text().strip()
        if base and not base.startswith(("http://", "https://")):
            self._win._show_info_bar("frps 面板 URL 需以 http:// 开头", "warning")
            return
        ok = app_settings.set("frps_admin", {
            "base_url": base,
            "user": self.edit_user.text().strip(),
            "password": self.edit_pwd.text(),
        })
        if not ok:
            self._win._show_info_bar("感知配置保存失败", "error")
            return
        self._frps.restart_timer()   # 重读配置 + 后台立即感知一次
        self._update_state()
        self._win._show_info_bar(
            "感知配置已保存，正在后台感知…" if base else "已清空（感知关闭）",
            "success")

    def _on_channel_test(self):
        # 先临时保存再测（restart_timer 读配置）；未改配置时等效直接测
        state = self._frps.refresh()
        snap = self._frps.snapshot()
        msg = _CHANNEL_TEXT.get(state, state)
        if state == "ok":
            msg += f" · 名单 {len(snap['proxies'])} 条 xtcp proxy"
        self._win._show_info_bar(
            msg, "success" if state == "ok" else
            ("warning" if state == "unconfigured" else "error"),
            duration=5000)
        self._update_state()

    def _on_frpc_health(self):
        if not self._mgr.is_running():
            self._win._show_info_bar("frpc 未运行，无本机管理通道", "warning")
            return
        code, _body = self._mgr.ping_admin("/healthz")
        if code != 200:
            self._win._show_info_bar(
                f"frpc 管理通道不可达（HTTP {code or 'unreachable'}，"
                f"端口 {self._mgr.admin_port}）", "error", duration=5000)
            return
        code2, body2 = self._mgr.ping_admin("/api/status")
        self._win._show_info_bar(
            f"frpc 通道正常 :{self._mgr.admin_port} · /api/status "
            + ("OK" if code2 == 200 else f"HTTP {code2 or 'unreachable'}")
            + (f" {body2[:60]}" if code2 == 200 and body2 else ""),
            "success", duration=5000)

    def _on_toggle_quality(self):
        if self._prober.running:
            self._prober.stop()
            self._win._show_info_bar("RTT 质量探测已暂停", "warning")
        else:
            self._prober.start()
            self._win._show_info_bar("RTT 质量探测已启动", "success")
        self._update_state()


# ==================== RemoteHub 容器 ====================

class RemoteHub(PivotPage):
    """远程页：横排 Pivot 四视图（与会话中心单例实时联动）

    二期（2026-09-21）：3→4 视图，新增「连接质量」；进入本页即启动
    frps 在线感知（周期 GET）与本机 RTT 探测线程（幂等 start）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("remoteHub")
        self._win = parent

        self.session_work = SessionWork(self._win, self, self)
        self.visitor_work = VisitorWork(self._win, self, self)
        self.quality_work = QualityWork(self._win, self, self)
        self.tunnel_conf_work = TunnelConfWork(self._win, self, self)

        self.addPage(self.session_work, "会话总览")
        self.addPage(self.visitor_work, "P2P 访客")
        self.addPage(self.quality_work, "连接质量")
        self.addPage(self.tunnel_conf_work, "隧道配置")
        self.lock_pivot_width()
        self.switchTo(self.session_work)

        # 感知/探测随页面启动（单例幂等；配置缺失时内部自静默）
        get_frps_client().start()
        get_prober().start()

    def _apply_table_smooth_all(self):
        """统一设置页平滑开关联动：本页三张表"""
        from core.perf import apply_table_smooth_mode
        for t in (self.session_work.table, self.visitor_work.table,
                  self.quality_work.table):
            apply_table_smooth_mode(t, panel="remote")
