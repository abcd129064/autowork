# -*- coding: utf-8 -*-
"""远程页 RemoteHub（二期，2026-09-21 重构）——统一远程会话中心

设计稿：design/remote_page_v3_065.html（frps 0.65 能力锁定版）
形态：与工具页同风格——横排 Pivot 二级切换（无图标），五视图堆叠：
    会话总览 │ 连接 │ 连接质量 │ frps 代理 │ 隧道配置
（连接诊断不属本页，2026-09-07 用户定稿：入口保留在 设置-工具）

数据源：
  - 会话总览 = mgr.records() + sessions_on_port() 活跃会话联动
             + frps 在线感知（core/frps_admin，P0：proxy status=online/offline）
  - 连接 = visitor 注册表增删（register_visitor/persist/delete_visitor）
           + TCP 直连（2026-09-24 P1 双模化：XTCP|TCP Segmented）
  - 连接质量 = core/visitor_probe（P1：本地 bindPort TCP connect RTT，
               30s 一轮串行；sparkline 趋势 + 评级）
  - frps 代理 = frps 网页面板 Proxies 页同源 API（GET /api/proxy/{type}
               ×7 + /api/clients 版本关联；xtcp 复用权威名单零额外 GET）
  - 隧道配置 = settings.frpc_server 嵌套 dict（与 设置-远程连接 同键，
    单点写回）+ frpc 进程控制（apply 热重载 / /api/stop 优雅停止 P1）
    + 管理通道卡（frps_admin credentials 配置 + frpc admin 自检）+ 实时日志

与设置分工（设计稿）：设置-远程连接 = 凭据/FRP 静态配置；本页 = 会话与
隧道的操作面。frpc 服务器参数两处共享同一 settings 键，避免双写分叉。
"""
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QTableWidgetItem, QAbstractItemView, QHeaderView,
                               QListWidget, QListWidgetItem)
from qfluentwidgets import (TitleLabel, CaptionLabel, BodyLabel, StrongBodyLabel,
                            CardWidget, LineEdit, PasswordLineEdit, PushButton,
                            PrimaryPushButton, ComboBox, SpinBox, TableWidget,
                            ToolButton, MessageBox, FluentIcon, InfoBar,
                            InfoBarPosition, CheckBox, SearchLineEdit,
                            SegmentedWidget)

from main_window.pivot_page import PivotPage
from main_window.tool_hub import _transparent, _make_terminal
from core.frp_remote import (get_session_manager, SOURCE_MANUAL,
                             SOURCE_SNK, _FRPC_SERVER_DEFAULTS)
from core.frps_admin import get_frps_client
from core.visitor_probe import get_prober
from core.ops_link_delegate import LINKS_ROLE, install_ops_links
from database import table_db
from workers.aftersale_worker import AftersaleDBWorker
from windows.aftersale.common import _style_cand_list

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


# ==================== TCP 直连共享工具（与主面板 remote_mixin 同源） ====================

def _app_settings_merged() -> dict:
    """配置门面合并视图（失败空 dict——联动动作绝不因配置异常崩溃）"""
    try:
        from core import app_settings
        return app_settings.get_merged()
    except Exception:
        return {}


def load_tcp_servers() -> list:
    """保存的 TCP 服务器列表（settings 键 tcp_servers，"host:port" 字符串；
    与主面板 remote_mixin._load_tcp_servers 同键同格式，双端天然同步）"""
    servers = _app_settings_merged().get("tcp_servers", [])
    if not isinstance(servers, list):
        return []
    return [s for s in servers if isinstance(s, str) and s.strip()]


def save_tcp_server(entry: str) -> bool:
    """追加一条服务器（去重）并持久化；已存在返回 False"""
    entry = str(entry or "").strip()
    if not entry:
        return False
    servers = load_tcp_servers()
    if entry in servers:
        return False
    servers.append(entry)
    try:
        from core import app_settings
        app_settings.set("tcp_servers", servers)
    except Exception:
        return False
    return True


def delete_tcp_server(entry: str) -> bool:
    """删除一条服务器并持久化；不存在返回 False"""
    servers = load_tcp_servers()
    if entry not in servers:
        return False
    servers.remove(entry)
    try:
        from core import app_settings
        app_settings.set("tcp_servers", servers)
    except Exception:
        return False
    return True


def local_tunnel_state(mgr, name: str) -> tuple:
    """frps 代理名 → 本地注册表关联三态

    Returns:
        (state, rec)：state ∈ "registered"（已注册启用）/"disabled"（已断开
        保留注册）/"none"（未注册）；rec 为匹配的注册表记录（无则 {}）。
    """
    name = str(name or "").strip()
    for rec in mgr.records():
        if str(rec.get("serverName", "")) == name:
            return ("disabled" if rec.get("disabled") else "registered", rec)
    return ("none", {})

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


def _fmt_traffic_bytes(nbytes: int) -> str:
    """frps 代理清单流量列：保留 bytes 级（网页面板口径 "362 bytes"/"3 KB"）

    _fmt_traffic 面向概览卡（KB 起），清单列照网页面板显示原始量级，
    小流量（心跳包几百字节）不被截成 0KB。
    """
    if not nbytes:
        return "0 bytes"
    if nbytes < 1024:
        return f"{nbytes} bytes"
    return _fmt_traffic(nbytes)


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
        # frp 总开关（2026-09-22 需求，置于「立即感知」左侧）：随 frpc
        # 运行态翻转文案/动作——未运行=启动（按注册表 apply），运行中=
        # 优雅停止（两段式 /api/stop，与隧道配置页停止按钮同口径，带确认）
        self.btn_frp_toggle = PushButton(FluentIcon.PLAY, "启动 frp", body)
        self.btn_frp_toggle.setToolTip("一键启动/停止 frpc")
        self.btn_frp_toggle.clicked.connect(self._on_frp_toggle)
        head.addWidget(self.btn_frp_toggle)
        btn_probe = PushButton(FluentIcon.SYNC, "立即感知", body)
        btn_probe.setToolTip("立即拉取 frps xtcp proxy 名单")
        btn_probe.clicked.connect(self._on_probe_now)
        head.addWidget(btn_probe)
        btn_session_win = PushButton(FluentIcon.DOCUMENT, "会话窗口", body)
        btn_session_win.setToolTip("打开/置顶远程会话标签窗口")
        btn_session_win.clicked.connect(lambda: self._mgr.ensure_session_window())
        head.addWidget(btn_session_win)
        btn_add = PrimaryPushButton(FluentIcon.ADD, "新建隧道", body)
        btn_add.setToolTip("切到「连接」视图注册新隧道")
        btn_add.clicked.connect(lambda: self._hub.switchTo(self._hub.visitor_work))
        head.addWidget(btn_add)
        lay.addLayout(head)

        # ---------- frps 概览卡（/api/serverinfo，随感知刷新） ----------
        lay.addWidget(self._build_overview(body))

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
        # 操作列：委托自绘文字链接（SSH/SFTP/RDP/断开/删除，零子控件）——
        # 2026-09-24 P0-2 委托化：原先 330px 是为容纳 5 个自适应宽按钮，
        # 文字链接密度高，260px 足够
        self.table.setColumnWidth(8, 260)
        self.table.setMinimumHeight(260)
        cl.addWidget(self.table, 1)
        lay.addWidget(card, 1)
        # 操作列视图级委托（setItemDelegate 不能列级：会丢 hover 高亮）；
        # 点击回调经 _row_recs 行映射取当前记录（重建后行号失效问题由刷新时重建映射解决）
        self._row_recs: dict = {}
        install_ops_links(self.table, self._on_ops_link)

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
        self._frps.serverinfo_changed.connect(lambda _d: self._update_overview())
        self._frps.refresh_finished.connect(self._on_refresh_finished)
        self._receipt_armed = False
        self._prober = get_prober()
        self._prober.changed.connect(self._on_probe_round)
        # P1-1 合帧门控：五个信号源同帧连发时只重建一次（见 refresh）
        self._refresh_pending = False

    def _on_probe_round(self):
        # 质量视图非当前页时不整表重绘（探测每 30s 一轮，成本可忽略但保持克制）
        if self.isVisible():
            self.refresh()

    def _on_probe_now(self):
        # 异步感知（后台线程）；完成回执由 _on_refresh_finished 统一处理——
        # 成功/失败都明确告知（2026-09-22 反馈：只有"已发起"没有结果，
        # 数据无变化时看起来像没反应）
        self._receipt_armed = True
        self._frps.request_refresh()
        self._win._show_info_bar("已发起 frps 感知请求", "info", duration=2000)

    def _on_refresh_finished(self, state):
        # 周期刷新的完成信号也进这里：未挂接回执时静默（绝不弹窗刷屏）
        if not getattr(self, "_receipt_armed", False):
            return
        self._receipt_armed = False
        if not self.isVisible():
            return
        n = len(self._frps.snapshot()["proxies"])
        self._win._show_info_bar(
            _CHANNEL_TEXT.get(state, state)
            + (f" · 名单 {n} 条，数据已刷新" if state == "ok" else ""),
            "success" if state == "ok" else
            ("warning" if state == "unconfigured" else "error"),
            duration=4000)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    # ---------- frp 总开关 ----------

    def _on_frp_toggle(self):
        if self._mgr.is_running():
            # 停止会断所有会话，与隧道配置页停止口径一致，必须确认
            dlg = MessageBox("停止 frp",
                             "停止后所有隧道失效，已打开的 SSH/SFTP/RDP 会话将断开。\n"
                             "经 /api/stop 优雅收尾（端口干净释放），注册表保留。\n"
                             "确定停止吗？", self)
            dlg.yesButton.setText("停止")
            dlg.cancelButton.setText("取消")
            if not dlg.exec():
                return
            self._mgr.close_all_sessions("总开关停止 frpc")
            self._mgr.stop_frpc()
            self._win._append_log("[远程] 总开关停止 frpc")
            return
        if not self._mgr.records():
            self._win._show_info_bar("无已注册隧道，请先在「连接」添加隧道",
                                     "warning")
            self._hub.switchTo(self._hub.visitor_work)
            return
        try:
            result = self._mgr.apply()
        except (OSError, RuntimeError) as e:
            self._win._show_info_bar(f"frpc 启动失败：{e}", "error",
                                     duration=6000)
            return
        if result == "stopped":
            # 注册表只剩「已断开」隧道：没有启用项，frpc 无从启动
            # （断开保留注册的新口径），提示用户重连即恢复
            self._win._show_info_bar(
                "全部隧道处于「已断开」状态：点该行的 SSH/SFTP/RDP 重连，"
                "frpc 会自动随首条重连隧道启动", "warning", duration=5000)
            return
        self._win._show_info_bar("frpc 已启动（按注册表应用配置）", "success")
        self._win._append_log("[远程] 总开关启动 frpc")

    def _update_frp_toggle(self):
        running = self._mgr.is_running()
        self.btn_frp_toggle.setText("停止 frp" if running else "启动 frp")
        self.btn_frp_toggle.setIcon(
            FluentIcon.POWER_BUTTON if running else FluentIcon.PLAY)

    # ---------- frps 概览卡 ----------

    def _build_overview(self, body):
        """frps 概览卡：版本/在线客户端/当前连接/今日流量/xtcp 代理数

        数据源 GET /api/serverinfo（core.frps_admin best-effort 拉取）。
        感知不可用或未含该端点时全部显示「—」，绝不报错。
        """
        card = CardWidget(body)
        card.setObjectName("frpsOverviewCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 10, 16, 10)
        v.setSpacing(6)
        head = QHBoxLayout()
        t = StrongBodyLabel("frps 概览", card)
        head.addWidget(t)
        head.addStretch(1)
        self.ov_src = CaptionLabel("GET /api/serverinfo", card)
        self.ov_src.setTextColor(QColor(0, 0, 0, 150), QColor(255, 255, 255, 150))
        head.addWidget(self.ov_src)
        v.addLayout(head)
        row = QHBoxLayout()
        row.setSpacing(28)
        self.ov_fields = {}
        for key, label in (("version", "frps 版本"),
                           ("clientCounts", "在线客户端"),
                           ("curConns", "当前连接"),
                           ("traffic", "今日流量↓/↑"),
                           ("xtcp", "xtcp 代理 在线/总")):
            col = QVBoxLayout()
            col.setSpacing(0)
            num = StrongBodyLabel("—", card)
            num.setStyleSheet("font-size: 17px; font-weight: 700;")
            cap = CaptionLabel(label, card)
            cap.setTextColor(QColor(0, 0, 0, 160), QColor(255, 255, 255, 160))
            col.addWidget(num)
            col.addWidget(cap)
            row.addLayout(col)
            self.ov_fields[key] = num
        row.addStretch(1)
        v.addLayout(row)
        self._overview_card = card
        return card

    def _update_overview(self):
        try:
            info = self._frps.serverinfo()
        except RuntimeError:
            return
        if not info:
            for num in self.ov_fields.values():
                num.setText("—")
                num.setStyleSheet(
                    f"color: {_C_MUTED.name()}; font-size: 17px; font-weight: 700;")
            self.ov_src.setText("GET /api/serverinfo · 暂无数据")
            return
        counts = info.get("proxyTypeCounts") or {}
        xtcp_total = int(counts.get("xtcp") or 0)
        snap = self._frps.snapshot()
        xtcp_online = sum(1 for it in snap["proxies"].values()
                          if it.get("status") == "online")

        def _set(key, text, color):
            num = self.ov_fields[key]
            num.setText(text)
            num.setStyleSheet(
                f"color: {color.name()}; font-size: 17px; font-weight: 700;")

        _set("version", info.get("version") or "—", _C_ACCENT)
        _set("clientCounts", str(info.get("clientCounts") or 0), _C_SUCCESS)
        _set("curConns", str(info.get("curConns") or 0), _C_INFO)
        _set("traffic",
             f"{_fmt_traffic(info.get('totalTrafficIn', 0))} / "
             f"{_fmt_traffic(info.get('totalTrafficOut', 0))}", _C_ACCENT)
        _set("xtcp", f"{xtcp_online} / {xtcp_total}",
             _C_SUCCESS if xtcp_online else _C_MUTED)
        age = snap.get("age_sec")
        self.ov_src.setText(f"GET /api/serverinfo"
                            + (f" · {age}s 前" if age is not None else ""))

    # ---------- 刷新 ----------

    def refresh(self):
        # P1-1（2026-09-24）：五个刷新源（visitors_changed/frpc_state/
        # proxies_changed/channel_state/probe_round）同帧连发时归并到下一轮
        # 事件循环只整表重建一次（重建成本 ~每行 1ms+，五连发即五倍白费）
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QTimer.singleShot(0, self._refresh_now)

    def _refresh_now(self):
        self._refresh_pending = False
        mgr = self._mgr
        try:
            records = mgr.records()
        except RuntimeError:
            return
        running = mgr.is_running()
        active_n = sum(1 for r in records if not r.get("disabled"))
        self.lbl_frpc.setText(
            f"frpc {'运行中' if running else '未启动'} · {active_n} 条启用"
            + (f" / {len(records) - active_n} 条已断开"
               if len(records) > active_n else ""))
        self._update_frp_toggle()
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
        self._update_overview()

        sess_total = 0
        online_total = 0
        rtts = []
        # P2-3（2026-09-24）：每行 online() 只算一次（旧实现在本循环 ×2、
        # _add_row 再 ×2——含 _frps_cell 内部一次），状态汇总给 _add_row 复用
        frps_states: dict = {}
        for rec in records:
            sess_total += len(mgr.sessions_on_port(rec.get("bindPort", 0)))
            sn_i = rec.get("serverName", "")
            frps_state = self._frps.online(sn_i)
            frps_states[sn_i] = frps_state
            if frps_state == "online":
                online_total += 1
            st = self._prober.stats(sn_i)
            if st["avg_ms"] is not None:
                rtts.append(st["avg_ms"])
            # P2-5 失联感知上报：frps 权威 offline 或探测判异常（bad）
            # 即视为失联；frpc 未运行同样视为失联（自愈中/放弃态）
            lost = (not rec.get("disabled") and (
                    not running
                    or frps_state == "offline"
                    or self._prober.verdict(sn_i) == "bad"))
            mgr.report_tunnel_issue(sn_i, lost)
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
        self._row_recs.clear()
        for rec in records:
            self._add_row(rec, running,
                          frps_states.get(str(rec.get("serverName", ""))))
        if not records:
            self.lbl_hint.setText("暂无隧道 —— 点右上「新建隧道」注册 xtcp visitor")
        else:
            self.lbl_hint.setText(
                "「连接」复用球桌一键直连（SSH/SFTP/RDP 标签窗口）；"
                "断开=关会话并释放端口（保留注册，点 SSH/SFTP/RDP 即重连），"
                "删除=彻底移除注册与持久化配置（需确认、不可恢复）")

    def _add_row(self, rec, running, frps_state=None):
        mgr = self._mgr
        sn = str(rec.get("serverName", ""))
        port = int(rec.get("bindPort", 0) or 0)
        sessions = mgr.sessions_on_port(port)
        table = self.table
        r = table.rowCount()
        table.insertRow(r)
        if rec.get("disabled"):
            # 已断开（保留注册）：隧道已从 frpc 摘除、端口已释放，
            # 与「删除」不同——注册还在，SSH/SFTP/RDP 点击即重连
            status, color = "已断开", _C_WARNING
        elif not running:
            status, color = "未启动", _C_MUTED
        elif sessions:
            status, color = "会话中", _C_SUCCESS
        elif rec.get("prewarmedAt"):
            # P2-6：本轮预热打洞已完成，点击连接免打洞等待（秒连体感）
            status, color = "已预热", _C_INFO
        else:
            status, color = "已连接", _C_ACCENT
        it = QTableWidgetItem(status)
        it.setForeground(color)
        if rec.get("prewarmedAt") and not sessions:
            it.setToolTip(f"预热打洞完成于 {rec.get('prewarmedAt')}，"
                          "点击连接免打洞等待")
        table.setItem(r, 0, it)

        # frps 在线感知列（P0）：online/offline/未注册/无感知 四态
        # （P2-3：状态由 refresh 循环算好传入，不再经 _frps_cell 重复探测）
        frps_text, frps_color = _FRPS_CELL.get(frps_state, _FRPS_CELL[None])
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

        # 设备确认离线（frps 权威）时省略打开类链接——点击只会盲等超时；
        # 无感知（None）/未注册（visitor 可先于设备上线注册）不禁用；
        # 已断开行的打开类链接保留——点击即经 ensure_visitor 重新启用并重连
        confirmed_offline = (frps_state == "offline"
                             and not rec.get("disabled"))
        disabled_row = bool(rec.get("disabled"))
        self._row_recs[r] = rec
        # 操作列：委托自绘文字链接（零子控件，旧 cellWidget 按钮组已移除）。
        # 委托不支持置灰，禁用语义改为省略（悬停提示由确认弹窗兜底）
        links = []
        if not confirmed_offline:
            links += [("SSH", "primary", "ssh"), ("SFTP", "primary", "sftp"),
                      ("RDP", "primary", "rdp")]
        if not disabled_row:
            links.append(("断开", "ghost", "disconnect"))
        links.append(("删除", "danger", "delete"))
        cell = QTableWidgetItem("")
        cell.setData(LINKS_ROLE, tuple(links))
        table.setItem(r, 8, cell)

    def _on_ops_link(self, row, _col, action):
        """会话总览表操作列文字链接回调（row → 记录映射见 _add_row）"""
        rec = self._row_recs.get(row)
        if rec is None:
            return  # 行重建竞态：映射里已无此行，忽略本次点击
        sn = str(rec.get("serverName", ""))
        if action in ("ssh", "sftp", "rdp"):
            self._open(action, sn, rec)
        elif action == "disconnect":
            self._disconnect(sn)
        elif action == "delete":
            self._delete(sn)

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
            # frpc 未运行：隧道本就未建立，无需断开（新口径下注册仍保留，
            # 与「删除」不再是唯一选项——重新启动 frp 即可恢复全部隧道）
            self._info("当前 frpc 未启动，隧道未建立，无需断开", "warning")
            return
        if not self._confirm_transfer(sn):
            return
        # P0-2：断开的 apply（热重载 HTTP）已移后台，结果回 GUI 线程后提示；
        # 行内状态立即置「已断开」（refresh 由 visitors_changed 信号顺带触发）
        def _done(result: str):
            if result == "ok":
                self._info(f"已断开隧道 {sn}：会话已关闭、端口已释放，"
                           "注册保留，点 SSH/SFTP/RDP 可重连", "success")
            elif result == "not_running":
                self._info("当前 frpc 未启动", "warning")
            else:
                self._info(f"隧道 {sn} 断开失败", "error")
            self.refresh()

        mgr.disconnect_visitor_async(sn, _done)
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
        # P0-2：删除的 apply 部分后台化，结果回 GUI 线程后提示
        def _done(result: str):
            if result == "ok":
                self._info(f"已删除隧道 {sn}", "success")
            else:
                self._info(f"隧道 {sn} 删除失败（{result}）", "error")
            self.refresh()

        mgr.delete_visitor_async(sn, _done)

    def _info(self, msg, kind):
        self._win._show_info_bar(msg, kind, duration=4000)


# ==================== 视图 2：连接（XTCP|TCP 双模） ====================

class VisitorWork(QWidget):
    """连接视图（原「P2P 访客」，2026-09-24 P1 双模化）：XTCP 访客 + TCP 直连

    - XTCP 模式：visitor 注册表 + 添加访客表单（写入统一 TOML 并落盘）
    - TCP 模式：保存的服务器列表（settings tcp_servers，与主面板同源）
      + host/port/凭据表单 → open_direct_session 直连（不经 frpc）
    模式选择记忆到配置（remote_conn_mode），下次进入保持。
    """

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

        # ---------- 连接模式切换（P1：模式记忆，下次进入保持） ----------
        self.mode_seg = SegmentedWidget(body)
        self.mode_seg.addItem("xtcp", "XTCP 访客（P2P 打洞）")
        self.mode_seg.addItem("tcp", "TCP 直连（经 frps / 局域网）")
        self.mode_seg.currentItemChanged.connect(self._on_conn_mode_changed)
        lay.addWidget(self.mode_seg)

        # ---------- 添加访客卡（置于注册表上方；球桌号搜索联动带出 serverName） ----------
        self.add_card = CardWidget(body)
        add_card = self.add_card  # 局部别名（下方大量 parent 引用沿用原名）
        al = QVBoxLayout(self.add_card)
        al.setContentsMargins(16, 14, 16, 14)
        al.setSpacing(10)
        al.addWidget(BodyLabel("添加访客", self.add_card))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        # 球桌号搜索（与球桌管理搜索栏同款动态搜索：300ms 防抖 + 异步查库）：
        # 命中球桌后带出 snk_code 填入 serverName，并同步关联球桌号
        self.edit_table_search = SearchLineEdit(add_card)
        self.edit_table_search.setPlaceholderText("搜索球桌号带出 serverName")
        self.edit_table_search.setFixedWidth(260)
        self.edit_table_search.textChanged.connect(self._on_table_search_changed)
        self.edit_table_search.clearSignal.connect(self._hide_table_candidates)
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
        lbl0 = BodyLabel("球桌搜索:", add_card)
        lbl1 = BodyLabel("serverName:", add_card)
        lbl2 = BodyLabel("secretKey:", add_card)
        lbl3 = BodyLabel("本地端口:", add_card)
        lbl4 = BodyLabel("关联球桌:", add_card)
        grid.addWidget(lbl0, 0, 0)
        grid.addWidget(self.edit_table_search, 0, 1)
        grid.addWidget(lbl2, 0, 2)
        grid.addWidget(self.edit_key, 0, 3)
        grid.addWidget(lbl1, 1, 0)
        grid.addWidget(self.edit_name, 1, 1)
        grid.addWidget(lbl3, 1, 2)
        grid.addWidget(self.spin_port, 1, 3, Qt.AlignLeft)
        grid.addWidget(lbl4, 2, 0)
        grid.addWidget(self.edit_table_id, 2, 1, Qt.AlignLeft)
        grid.setColumnStretch(4, 1)
        al.addLayout(grid)
        # 球桌候选列表（默认隐藏，搜索命中后展示；点选带出 snk/桌号）
        self._cand_list = QListWidget(add_card)
        self._cand_list.setFixedHeight(132)
        self._cand_list.setVisible(False)
        self._cand_list.itemClicked.connect(self._on_table_candidate_clicked)
        _style_cand_list(self._cand_list)
        al.addWidget(self._cand_list)

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
            "注册仅写入 frpc_xtcp_panel.toml（不拉起 frpc）；「添加并连接」经一键直连建立隧道。"
            "搜索球桌号可带出 serverName（snk 标识）与关联球桌。",
            add_card)
        cap.setTextColor(QColor(0, 0, 0, 170), QColor(255, 255, 255, 170))
        al.addWidget(cap)
        lay.addWidget(self.add_card)

        # ---------- TCP 直连卡（P1：与主面板 TCP 模式同源同语义） ----------
        self.tcp_card = CardWidget(body)
        tl = QVBoxLayout(self.tcp_card)
        tl.setContentsMargins(16, 14, 16, 14)
        tl.setSpacing(10)
        tl.addWidget(BodyLabel("TCP 直连", self.tcp_card))
        tcp_grid = QGridLayout()
        tcp_grid.setHorizontalSpacing(12)
        tcp_grid.setVerticalSpacing(8)
        self.tcp_host = LineEdit(self.tcp_card)
        self.tcp_host.setPlaceholderText("主机地址（IP 或域名）")
        self.tcp_host.setFixedWidth(220)
        self.tcp_port = SpinBox(self.tcp_card)
        self.tcp_port.setRange(1, 65535)
        self.tcp_port.setValue(22)
        self.tcp_port.setFixedWidth(110)
        self.tcp_user = LineEdit(self.tcp_card)
        self.tcp_user.setPlaceholderText("SSH 用户名")
        self.tcp_user.setFixedWidth(150)
        self.tcp_pass = PasswordLineEdit(self.tcp_card)
        self.tcp_pass.setPlaceholderText("SSH 密码")
        self.tcp_pass.setFixedWidth(150)
        _merged = _app_settings_merged()
        self.tcp_user.setText(str(_merged.get("ssh_user", "") or ""))
        self.tcp_pass.setText(str(_merged.get("ssh_pass", "") or ""))
        tcp_grid.addWidget(BodyLabel("主机:", self.tcp_card), 0, 0)
        tcp_grid.addWidget(self.tcp_host, 0, 1)
        tcp_grid.addWidget(BodyLabel("端口:", self.tcp_card), 0, 2)
        tcp_grid.addWidget(self.tcp_port, 0, 3, Qt.AlignLeft)
        tcp_grid.addWidget(BodyLabel("用户:", self.tcp_card), 1, 0)
        tcp_grid.addWidget(self.tcp_user, 1, 1)
        tcp_grid.addWidget(BodyLabel("密码:", self.tcp_card), 1, 2)
        tcp_grid.addWidget(self.tcp_pass, 1, 3, Qt.AlignLeft)
        tcp_grid.setColumnStretch(4, 1)
        tl.addLayout(tcp_grid)
        tcp_btns = QHBoxLayout()
        tcp_btns.setSpacing(8)
        self.btn_tcp_ssh = PrimaryPushButton(FluentIcon.CONNECT, "连接 SSH", self.tcp_card)
        self.btn_tcp_ssh.clicked.connect(lambda: self._tcp_connect("ssh"))
        tcp_btns.addWidget(self.btn_tcp_ssh)
        self.btn_tcp_sftp = PushButton(FluentIcon.FOLDER, "连接 SFTP", self.tcp_card)
        self.btn_tcp_sftp.clicked.connect(lambda: self._tcp_connect("sftp"))
        tcp_btns.addWidget(self.btn_tcp_sftp)
        btn_save = PushButton(FluentIcon.ADD, "保存服务器", self.tcp_card)
        btn_save.setToolTip("把当前 host:port 存入服务器列表（与主面板同源）")
        btn_save.clicked.connect(self._tcp_save)
        tcp_btns.addWidget(btn_save)
        btn_del = PushButton(FluentIcon.DELETE, "删除选中", self.tcp_card)
        btn_del.clicked.connect(self._tcp_delete_selected)
        tcp_btns.addWidget(btn_del)
        tcp_btns.addStretch(1)
        tl.addLayout(tcp_btns)
        # 保存的服务器表（settings tcp_servers，与主面板完全同源）
        tl.addSpacing(12)   # 按钮行与「保存的服务器」分段留白（卡片被拉伸时多余高度沉底，不均摊进控件间隙）
        tl.addWidget(BodyLabel("保存的服务器", self.tcp_card))
        self.tcp_table = TableWidget(self.tcp_card)
        self.tcp_table.setColumnCount(2)
        self.tcp_table.setHorizontalHeaderLabels(["主机:端口", "操作"])
        self.tcp_table.verticalHeader().setVisible(False)
        self.tcp_table.setAlternatingRowColors(True)
        self.tcp_table.setWordWrap(False)
        self.tcp_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tcp_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tcp_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.tcp_table.setColumnWidth(1, 70)
        self.tcp_table.setMinimumHeight(150)
        self.tcp_table.setMaximumHeight(230)
        self.tcp_table.cellClicked.connect(self._on_tcp_row_clicked)
        tl.addWidget(self.tcp_table)
        tl.addSpacing(8)    # 表格与说明文字间距
        tcp_cap = CaptionLabel(
            "TCP 直连不经 frpc：局域网地址或 frps 转发端口（frps 代理页 tcp 页签可一键存入）。"
            "凭据与主面板共享（ssh_user/ssh_pass），连接成功进全局会话窗口。",
            self.tcp_card)
        tcp_cap.setTextColor(QColor(0, 0, 0, 170), QColor(255, 255, 255, 170))
        tl.addWidget(tcp_cap)
        tl.addStretch(1)    # 剩余高度沉底：控件间隙不被均摊拉大
        self.tcp_card.setVisible(False)   # 默认 XTCP 模式
        lay.addWidget(self.tcp_card)

        # 球桌搜索防抖：停止输入 300ms 后才查库，避免逐字触发同步查询
        # （与球桌管理搜索栏/售后面板球房搜索同范式）
        self._search_kw = ""
        self._cand_rows = []
        self._cand_worker = None
        self._search_timer = QTimer(self)
        self._search_timer.setInterval(300)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._do_table_search)

        # ---------- 访客表（XTCP 注册表；TCP 模式下隐藏） ----------
        self.visitor_card = CardWidget(body)
        cl = QVBoxLayout(self.visitor_card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(BodyLabel("xtcp visitor 注册表", self.visitor_card))
        head.addStretch(1)
        btn_refresh = ToolButton(FluentIcon.SYNC, self.visitor_card)
        btn_refresh.setToolTip("重新读取注册表")
        btn_refresh.clicked.connect(self.refresh)
        head.addWidget(btn_refresh)
        cl.addLayout(head)

        self.table = TableWidget(self.visitor_card)
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
        lay.addWidget(self.visitor_card, 1)

        # 初始模式：读记忆（缺省 XTCP）；setCurrentItem 在构造期不触发
        # currentItemChanged（信号在 connect 后手动补一次应用）
        self.mode_seg.setCurrentItem(self._saved_conn_mode())
        self._apply_conn_mode(self._saved_conn_mode(), save=False)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(body)

        self._mgr.visitors_changed.connect(self.refresh)
        self._mgr.frpc_state_changed.connect(lambda _b: self.refresh())

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    # ---------- 连接模式切换（P1 双模） ----------

    def _saved_conn_mode(self) -> str:
        """上次的连接模式（remote_conn_mode，缺省 xtcp）"""
        return "tcp" if _app_settings_merged().get(
            "remote_conn_mode") == "tcp" else "xtcp"

    def _on_conn_mode_changed(self, key):
        self._apply_conn_mode(str(key))

    def _apply_conn_mode(self, key: str, save: bool = True):
        """切换 XTCP/TCP 双模显隐并记忆（save=False 用于构造期恢复）"""
        tcp = (key == "tcp")
        self.add_card.setVisible(not tcp)      # xtcp 添加访客表单
        self.visitor_card.setVisible(not tcp)  # xtcp 注册表
        self.tcp_card.setVisible(tcp)          # TCP 直连卡
        if save:
            try:
                from core import app_settings
                app_settings.set("remote_conn_mode", key)
            except Exception:
                pass
        if tcp:
            self._refresh_tcp_table()

    # ---------- TCP 直连（与主面板 TCP 模式同源同语义） ----------

    def _refresh_tcp_table(self):
        """保存的服务器列表 ← settings tcp_servers（双端同源）"""
        self.tcp_table.setRowCount(0)
        for entry in load_tcp_servers():
            r = self.tcp_table.rowCount()
            self.tcp_table.insertRow(r)
            it = QTableWidgetItem(entry)
            it.setToolTip("点击行填入上方表单")
            self.tcp_table.setItem(r, 0, it)
            wrap = QWidget(self.tcp_table)
            wrap.setStyleSheet("background: transparent;")
            h = QHBoxLayout(wrap)
            h.setContentsMargins(0, 0, 0, 0)
            btn = ToolButton(FluentIcon.DELETE, wrap)
            btn.setFixedSize(28, 24)
            btn.setToolTip(f"删除 {entry}")
            btn.clicked.connect(lambda _=False, e=entry:
                                self._tcp_delete_entry(e))
            h.addWidget(btn, 0, Qt.AlignCenter)
            self.tcp_table.setCellWidget(r, 1, wrap)

    def _on_tcp_row_clicked(self, row, _col):
        """点击服务器行 → 填充表单（主面板同交互）"""
        servers = load_tcp_servers()
        if not (0 <= row < len(servers)):
            return
        entry = servers[row]
        host, _, port_str = entry.rpartition(":")
        if not host:
            host, port = entry, 22
        else:
            try:
                port = int(port_str)
            except ValueError:
                host, port = entry, 22
        self.tcp_host.setText(host)
        self.tcp_port.setValue(port)

    def _tcp_save(self):
        """当前 host:port 存入服务器列表（去重，双端同源）"""
        host = self.tcp_host.text().strip()
        if not host:
            self._win._show_info_bar("请先填写主机地址", "warning")
            return
        entry = f"{host}:{self.tcp_port.value()}"
        if save_tcp_server(entry):
            self._win._show_info_bar(f"已保存服务器 {entry}", "success")
            self._win._append_log(f"[远程] 已保存服务器: {entry}")
        else:
            self._win._show_info_bar(f"{entry} 已在服务器列表", "info",
                                     duration=3000)
        self._refresh_tcp_table()

    def _tcp_delete_selected(self):
        """删除当前选中行对应的服务器"""
        servers = load_tcp_servers()
        row = self.tcp_table.currentRow()
        if 0 <= row < len(servers):
            self._tcp_delete_entry(servers[row])

    def _tcp_delete_entry(self, entry: str):
        if not delete_tcp_server(entry):
            return
        self._win._show_info_bar(f"已删除服务器 {entry}", "success")
        self._win._append_log(f"[远程] 已删除服务器: {entry}")
        self._refresh_tcp_table()

    def _tcp_connect(self, kind: str):
        """TCP 直连 SSH/SFTP（凭据写回 settings，与主面板共享）"""
        host = self.tcp_host.text().strip()
        if not host:
            self._win._show_info_bar("请输入主机地址", "warning")
            self.tcp_host.setFocus()
            return
        # 凭据写回（主面板 _save_ssh_credentials 同语义：非空才覆盖）
        data = {}
        if self.tcp_user.text().strip():
            data["ssh_user"] = self.tcp_user.text().strip()
        if self.tcp_pass.text():
            data["ssh_pass"] = self.tcp_pass.text()
        if data:
            try:
                from core import app_settings
                for k, v in data.items():
                    app_settings.set(k, v)
            except Exception:
                pass  # 凭据持久化失败不阻塞连接
        self._win._append_log(f"[远程] TCP 直连 {kind.upper()} {host}:"
                              f"{self.tcp_port.value()}")
        self._mgr.open_direct_session(
            kind, host, self.tcp_port.value(), name=host, notifier=self._win)

    def refresh(self):
        self.table.setRowCount(0)
        for rec in self._mgr.records():
            r = self.table.rowCount()
            self.table.insertRow(r)
            disabled = bool(rec.get("disabled"))
            values = (str(rec.get("serverName", ""))
                      + ("（已断开）" if disabled else ""), "xtcp",
                      str(rec.get("bindPort", "")),
                      rec.get("tableId", "") or "—",
                      rec.get("source", "") or "—",
                      rec.get("lastUsed", "") or "—")
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip("已断开：注册保留，重新连接即恢复"
                                if disabled and col == 0 else text)
                if col == 1:
                    item.setForeground(_C_ACCENT)
                if disabled and col == 0:
                    item.setForeground(_C_WARNING)
                self.table.setItem(r, col, item)

    # ---------- 球桌搜索联动（球桌管理动态搜索范式） ----------

    def _on_table_search_changed(self, text=""):
        """球桌号输入变动：失效旧关联，防抖后异步搜索球桌管理库"""
        self._search_kw = str(text or "").strip()
        if not self._search_kw:
            self._hide_table_candidates()
            return
        self._search_timer.start()

    def _do_table_search(self):
        """防抖到期：按关键词异步查球桌（含关键词快照，过期结果丢弃）"""
        kw = self._search_kw
        if not kw:
            return
        if self._cand_worker is not None and self._cand_worker.isRunning():
            # 快速连续输入：打断旧查询并断开信号，避免过期候选覆盖新结果
            self._cand_worker.requestInterruption()
            self._cand_worker.disconnect(self)
        self._cand_worker = AftersaleDBWorker(
            table_db.query_page, 1, 20, kw,
            include_test=False, include_manual=False, include_tuidan=False)
        self._cand_worker.result_ready.connect(
            lambda result, k=kw: self._on_table_candidates(k, result))
        self._cand_worker.error.connect(lambda _m: self._hide_table_candidates())
        self._cand_worker.start()

    def _on_table_candidates(self, kw, result):
        if kw != self._search_kw:
            return  # 输入已变化，丢弃过期结果
        rows = (result or ([], []))[1] or []
        # 只命中唯一球桌：静默带出，不弹候选
        if len(rows) == 1:
            self._apply_table(rows[0])
            self._hide_table_candidates()
            return
        self._cand_list.clear()
        if not rows:
            item = QListWidgetItem(f"未找到「{kw}」的球桌，可直接手填 serverName")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._cand_list.addItem(item)
            self._cand_list.setVisible(True)
            return
        self._cand_rows = rows
        for r in rows:
            name = str(r.get("name") or "")
            room = str(r.get("roomName") or "")
            snk = str(r.get("snk_code") or "")
            item = QListWidgetItem(f"{name} · {room}")
            item.setToolTip(f"桌号: {name}\n球房: {room}\nSNK: {snk or '（未登记）'}")
            self._cand_list.addItem(item)
        self._cand_list.setVisible(True)

    def _on_table_candidate_clicked(self, item):
        row_idx = self._cand_list.row(item)
        if 0 <= row_idx < len(self._cand_rows):
            self._apply_table(self._cand_rows[row_idx])
        self._hide_table_candidates()

    def _apply_table(self, row):
        """选中球桌 → 带出 serverName（snk_code）与关联球桌号

        snk 未登记时不覆盖已填 serverName，仅提示去球桌管理补录。
        """
        name = str(row.get("name") or "").strip()
        snk = str(row.get("snk_code") or "").strip()
        if name:
            self.edit_table_id.setText(name)
        if snk:
            self.edit_name.setText(snk)
        else:
            self._win._show_info_bar(
                f"球桌 {name} 未登记 snk 标识，请在球桌管理补录或手填 serverName",
                "warning", duration=4000)

    def _hide_table_candidates(self):
        self._cand_list.setVisible(False)

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
        self.edit_table_search.clear()
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


# ==================== 视图 5：frps 代理（网页面板同源） ====================

# 类型页签顺序与 frps 网页面板 Proxies 页一致（2026-09-23 截图）
_PROXY_TABS = ("tcp", "udp", "http", "https", "tcpmux", "stcp", "sudp", "xtcp")


def _proxy_port_text(proxy: dict) -> str:
    """proxy.conf → 端口/域名展示文本（对齐网页面板 Port 列）

    v1 conf（model/types.go *OutConf）：tcp/udp=remotePort；http/https/
    tcpmux=customDomains/subdomain；stcp/sudp/xtcp 无端口（P2P 语义）。
    """
    conf = proxy.get("conf") or {}
    port = conf.get("remotePort")
    if port:
        return str(port)
    domains = conf.get("customDomains") or []
    if isinstance(domains, list) and domains:
        return ",".join(str(d) for d in domains[:2])
    sub = str(conf.get("subdomain") or "")
    return sub or "—"


class FrpsProxiesWork(QWidget):
    """frps 代理清单：网页面板 Proxies 页同源 API 的桌面端呈现

    数据源 GET /api/proxy/{type}（v1，0.65 即有）+ GET /api/clients
    （clientID → frpc 版本，网页面板 ClientVersion 列同源），经
    core.frps_admin.FrpsAdminClient.all_proxies()/client_version() 读取
    （随周期感知 best-effort 拉取，本视图零额外请求）。

    2026-09-24 联动（去孤岛）：xtcp 页签按 serverName 匹配本地注册表
    三态（已注册/未注册/已断开）+ 行内 SSH/SFTP/注册并连接/重连/删注册；
    tcp 页签行内对 frps serverAddr:remotePort 直连 SSH/SFTP + 存服务器。
    全部动作复用既有权威路径（open_session / register_visitor /
    open_direct_session / settings tcp_servers），本视图仍零额外请求。
    """

    # 注册表关联三态 → 「本地」列文案/颜色
    _LOCAL_CELL = {
        "registered": ("● 已注册", _C_SUCCESS),
        "disabled": ("○ 已断开", _C_WARNING),
        "none": ("— 未注册", _C_MUTED),
    }

    def __init__(self, win, hub, parent=None):
        super().__init__(parent)
        self.setObjectName("remoteFrpsProxiesWork")
        _transparent(self)
        self._win = win
        self._hub = hub
        self._frps = get_frps_client()
        self._mgr = get_session_manager()
        self._tab = "tcp"

        body = QWidget(self)
        body.setAutoFillBackground(False)
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 8, 20, 12)
        lay.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        self.lbl_state = BodyLabel("frps 代理清单", body)
        head.addWidget(self.lbl_state)
        head.addStretch(1)
        self.edit_kw = SearchLineEdit(body)
        self.edit_kw.setPlaceholderText("搜索代理名")
        self.edit_kw.setFixedWidth(200)
        # P1-2（2026-09-24）：输入防抖 300ms——逐键触发整表重建是大清单卡顿主因
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self.refresh)
        self.edit_kw.textChanged.connect(lambda _t: self._search_timer.start())
        head.addWidget(self.edit_kw)
        btn_refresh = PushButton(FluentIcon.SYNC, "刷新感知", body)
        btn_refresh.setToolTip("重新拉取 frps 全类型代理清单（后台线程）")
        btn_refresh.clicked.connect(lambda: self._frps.request_refresh())
        head.addWidget(btn_refresh)
        lay.addLayout(head)

        self.tabs = SegmentedWidget(body)
        for t in _PROXY_TABS:
            self.tabs.addItem(t, t.upper())
        self.tabs.setCurrentItem(self._tab)
        self.tabs.currentItemChanged.connect(self._on_tab_changed)
        lay.addWidget(self.tabs)

        card = CardWidget(body)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)
        self.table = TableWidget(card)
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Port / 域名", "Connections", "Traffic In",
             "Traffic Out", "ClientVersion", "Status", "本地", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 90)
        self.table.setColumnWidth(3, 96)
        self.table.setColumnWidth(4, 96)
        self.table.setColumnWidth(5, 100)
        self.table.setColumnWidth(6, 80)
        self.table.setColumnWidth(7, 92)
        # 操作列：委托自绘文字链接（2026-09-24 P0-2 委托化，旧 cellWidget 按钮组已移除）
        self.table.setColumnWidth(8, 220)
        self.table.setMinimumHeight(260)
        cl.addWidget(self.table, 1)
        lay.addWidget(card, 1)
        self._row_recs: dict = {}
        install_ops_links(self.table, self._on_proxy_ops_link)

        self.lbl_hint = CaptionLabel(
            "数据源 GET /api/proxy/{type} + /api/clients（frps 网页面板 Proxies 页同源，"
            "随周期感知 best-effort 拉取）。xtcp/tcp 页签可直连：动作走本地注册表与"
            " TCP 直连的既有权威路径。", body)
        self.lbl_hint.setTextColor(QColor(0, 0, 0, 170), QColor(255, 255, 255, 170))
        lay.addWidget(self.lbl_hint)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(body)

        self._frps.all_proxies_changed.connect(lambda _d: self.refresh())
        self._frps.proxies_changed.connect(lambda _d: self.refresh())
        self._frps.channel_state_changed.connect(lambda _s: self.refresh())

    def _on_tab_changed(self, key):
        if key in _PROXY_TABS:
            self._tab = key
            self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        snap_state = self._frps.snapshot().get("state")
        rows = self._frps.all_proxies().get(self._tab, [])
        kw = str(self.edit_kw.text() or "").strip().lower()
        if kw:
            rows = [r for r in rows if kw in r.get("name", "").lower()]
        self.table.setRowCount(0)
        self._row_recs.clear()
        for rec in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self._row_recs[r] = rec
            status = rec.get("status", "")
            # 「本地」列：xtcp 页签与注册表实时匹配三态；其余页签「—」
            if self._tab == "xtcp":
                state, _local = local_tunnel_state(self._mgr,
                                                   rec.get("name", ""))
                loc_text, loc_color = self._LOCAL_CELL[state]
            else:
                loc_text, loc_color = "—", _C_MUTED
            values = (rec.get("name", ""), _proxy_port_text(rec),
                      str(rec.get("curConns", 0)),
                      _fmt_traffic_bytes(rec.get("todayTrafficIn", 0)),
                      _fmt_traffic_bytes(rec.get("todayTrafficOut", 0)),
                      self._frps.client_version(rec.get("clientID", "")) or "—",
                      status, loc_text)
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if col == 6:
                    item.setForeground(
                        _C_SUCCESS if status == "online" else _C_DANGER)
                elif col == 7:
                    item.setForeground(loc_color)
                self.table.setItem(r, col, item)
            # 「操作」列：委托自绘文字链接（xtcp/tcp 页签，其余页签留空）
            links = self._action_links(rec) if self._tab in ("xtcp", "tcp") else None
            if links:
                cell = QTableWidgetItem("")
                cell.setData(LINKS_ROLE, tuple(links))
                self.table.setItem(r, 8, cell)
        self.lbl_state.setText(
            f"frps 代理清单 · {self._tab.upper()} {len(rows)} 条"
            + ("" if snap_state == "ok" else f"（感知通道 {snap_state}）"))

    # ---------- 行内动作（2026-09-24 联动；P0-2 委托化） ----------

    def _action_links(self, rec):
        """按页签类型生成操作列链接清单（(文案, 色键, 动作键) 元组）

        xtcp=注册表联动（registered→SSH/SFTP，disabled→重连/删注册，
        未注册→注册并连）；tcp=直连+存服务器；无可用动作返回空清单
        （不设 LINKS_ROLE，单元格留空）。
        """
        name = str(rec.get("name", ""))
        links = []
        if self._tab == "xtcp":
            state, local = local_tunnel_state(self._mgr, name)
            if state == "registered":
                links += [("SSH", "primary", "ssh"), ("SFTP", "primary", "sftp")]
            elif state == "disabled":
                links += [("重连", "primary", "ssh"), ("删注册", "danger", "del_local")]
            else:
                links += [("＋ 注册并连", "primary", "reg_connect")]
        elif self._tab == "tcp":
            port = (rec.get("conf") or {}).get("remotePort")
            if port:
                links += [("SSH", "primary", "ssh"), ("SFTP", "primary", "sftp")]
                entry = f"{self._mgr.frps_server_addr()}:{port}"
                if entry not in load_tcp_servers():
                    links.append(("存服务器", "ghost", "save"))
        return links

    def _on_proxy_ops_link(self, row, _col, action):
        """frps 代理表操作列文字链接回调（row → 记录映射见 refresh）"""
        rec = self._row_recs.get(row)
        if rec is None:
            return  # 行重建竞态：映射里已无此行，忽略本次点击
        name = str(rec.get("name", ""))
        if action == "del_local":
            self._delete_local(name)
            return
        if action == "reg_connect":
            self._register_and_connect(name)
            return
        if action == "save":
            port = (rec.get("conf") or {}).get("remotePort")
            if port:
                self._save_server(f"{self._mgr.frps_server_addr()}:{port}")
            return
        # ssh / sftp：xtcp 页签走注册表一键直连，tcp 页签走直连
        if self._tab == "xtcp":
            _state, local = local_tunnel_state(self._mgr, name)
            self._connect_xtcp(name, action, str(local.get("tableId", "") or ""))
        else:
            port = (rec.get("conf") or {}).get("remotePort")
            if port:
                self._direct_connect(action, self._mgr.frps_server_addr(),
                                     port, name)

    def _connect_xtcp(self, name: str, kind: str, table_id: str = ""):
        """xtcp 代理 → 一键直连（复用注册表 open_session 完整链路）"""
        self._win._append_log(f"[frps 代理] {kind.upper()} 连接 {name}")
        self._mgr.open_session(kind, name, table_id, notifier=self._win)

    def _register_and_connect(self, name: str):
        """未注册的 xtcp 代理 → 默认参数注册 + 直连 SSH"""
        try:
            self._mgr.register_visitor(name, bind_port=None, secret_key=None,
                                       source=SOURCE_SNK, table_id="")
            self._mgr.persist()
            if self._mgr.is_running():
                self._mgr.apply()  # 热重载让新 visitor 端口立即监听
        except (OSError, RuntimeError, ValueError) as e:
            self._win._show_info_bar(f"注册失败：{e}", "error", duration=5000)
            return
        self.refresh()
        self._win._append_log(f"[frps 代理] 已注册 visitor {name}，发起 SSH 连接")
        self._mgr.open_session("ssh", name, "", notifier=self._win)

    def _delete_local(self, name: str):
        """删除本地注册（彻底移除；frps 侧 proxy 由现场设备管理，不受影响）"""
        box = MessageBox("删除本地注册",
                         f"将从本地注册表与持久化配置中彻底移除 {name}？\n"
                         "frps 上的代理由现场设备管理，不受影响；"
                         "如需重连可随时「注册并连」。", self._win)
        box.yesButton.setText("删除")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        try:
            self._mgr.delete_visitor(name)
        except (OSError, RuntimeError, ValueError) as e:
            self._win._show_info_bar(f"删除失败：{e}", "error", duration=5000)
            return
        self._win._show_info_bar(f"已删除本地注册 {name}", "success")
        self._win._append_log(f"[frps 代理] 删除本地注册 {name}")
        self.refresh()

    def _direct_connect(self, kind: str, addr: str, port, name: str):
        """tcp 代理 → 对 frps serverAddr:remotePort 直连（不经 frpc）"""
        self._win._append_log(f"[frps 代理] TCP 直连 {addr}:{port}（{name}）")
        self._mgr.open_direct_session(kind, addr, int(port), name=name,
                                      notifier=self._win)

    def _save_server(self, entry: str):
        """tcp 代理 → 保存到服务器列表（与主面板/「连接」视图同源）"""
        if save_tcp_server(entry):
            self._win._show_info_bar(f"已保存服务器 {entry}", "success")
            self._win._append_log(f"[frps 代理] 已保存服务器 {entry}")
        else:
            self._win._show_info_bar(f"{entry} 已在服务器列表", "info", duration=3000)
        self.refresh()


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
        # 开机静默预连开关（2026-09-23）：启动 6s 后自动拉起 frpc 恢复启用
        # 隧道并预热打洞，点 SSH/SFTP 秒连；当场勾选=立即执行一次预连
        from core import app_settings as _as0
        self.chk_autostart = CheckBox("开启时静默预连", card)
        self.chk_autostart.setChecked(
            _as0.get("frp_autostart", True) is not False)
        self.chk_autostart.setToolTip(
            "程序启动后自动拉起 frpc 并恢复启用中的隧道（跳过已断开的），\n"
            "再预热打洞——点 SSH/SFTP 直接秒连，不再等 2.5s+ 冷启动。\n"
            "全程静默：失败只进日志不打扰；勾选时立即执行一次。\n"
            "注意：会在后台常驻 frpc 进程并保持到 frps 的控制连接。")
        self.chk_autostart.clicked.connect(self._on_autostart_toggled)
        btns.addWidget(self.chk_autostart)
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
        self._frps.refresh_finished.connect(self._on_channel_finished)
        self._test_armed = False
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
        # 「启用隧道」判定：仅剩已断开（disabled）时 apply 只会 stopped，
        # 按钮置灰避免无效点击
        _active = any(not r.get("disabled") for r in self._mgr.records())
        self.btn_restart.setEnabled(running or _active)
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

    def _on_autostart_toggled(self):
        """保存「开启时静默预连」开关；当场勾选立即执行一次预连

        取消勾选只影响下次启动，不主动停止正在运行的 frpc（停有专属的
        「优雅停止 frpc」按钮，各管各的，互不越权）。
        """
        from core import app_settings as _as
        checked = self.chk_autostart.isChecked()
        _as.set("frp_autostart", bool(checked))
        self._win._append_log(f"[远程] 开机静默预连已{'开启' if checked else '关闭'}")
        if not checked:
            self._win._show_info_bar("已关闭：下次启动不再自动预连", "info")
            return
        result = self._mgr.autostart()
        if result in ("started", "restarted", "reloaded"):
            # 就绪轮询后预热（P2-4）：bindPort 监听即打洞，不等固定 3s
            self._mgr.prewarm_when_ready()
            self._win._show_info_bar(
                "已立即执行一次静默预连并预热打洞", "success")
        elif result == "skipped_running":
            self._win._show_info_bar("已开启；frpc 正在运行，无需重复启动", "info")
        else:
            # skipped_no_tunnel / failed（失败细节已进日志区）
            self._win._show_info_bar(
                "已开启；当前无启用隧道可预连（已断开的隧道不自动复活，"
                "点 SSH/SFTP 即重连）" if result == "skipped_no_tunnel"
                else "已开启；本次预连未成功（详见 frpc 日志）", "warning")
        self._update_state()

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
        # 异步测试 + 完成回执（与「立即感知」同口径，GUI 零阻塞）
        self._test_armed = True
        self._frps.request_refresh()
        self._win._show_info_bar("正在测试感知通道…", "info", duration=2000)

    def _on_channel_finished(self, state):
        if not getattr(self, "_test_armed", False):
            return
        self._test_armed = False
        if not self.isVisible():
            return
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
        # P0-3（2026-09-24）：健康检查两次 HTTP 往返（最坏 ~6s）移后台线程，
        # 受理即反馈；结果经 manager 的 async_done 信号回 GUI 线程提示
        self._win._show_info_bar("正在检测 frpc 管理通道…", "info", duration=2000)
        admin_port = self._mgr.admin_port

        def _job():
            code, _body = self._mgr.ping_admin("/healthz")
            if code != 200:
                return ("fail", code)
            code2, body2 = self._mgr.ping_admin("/api/status")
            return ("ok", code2, body2)

        def _done(result):
            if result[0] == "fail":
                self._win._show_info_bar(
                    f"frpc 管理通道不可达（HTTP {result[1] or 'unreachable'}，"
                    f"端口 {admin_port}）", "error", duration=5000)
                return
            _tag, code2, body2 = result
            self._win._show_info_bar(
                f"frpc 通道正常 :{admin_port} · /api/status "
                + ("OK" if code2 == 200 else f"HTTP {code2 or 'unreachable'}")
                + (f" {body2[:60]}" if code2 == 200 and body2 else ""),
                "success", duration=5000)

        self._mgr._run_bg(_job, on_done=_done)

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
        self.frps_proxies_work = FrpsProxiesWork(self._win, self, self)
        self.tunnel_conf_work = TunnelConfWork(self._win, self, self)

        self.addPage(self.session_work, "会话总览")
        self.addPage(self.visitor_work, "连接")
        self.addPage(self.quality_work, "连接质量")
        self.addPage(self.frps_proxies_work, "frps 代理")
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
                  self.quality_work.table, self.frps_proxies_work.table):
            apply_table_smooth_mode(t, panel="remote")
