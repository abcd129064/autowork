# -*- coding: utf-8 -*-
"""远程页 RemoteHub（二期，2026-09-21 重构）——统一远程会话中心

设计稿：design/remote_page_v3_065.html（frps 0.65 能力锁定版）
形态：与工具页同风格——横排 Pivot 二级切换（无图标），六视图堆叠：
    会话总览 │ 连接 │ 连接质量 │ frps 代理 │ 隧道配置 │ RDP
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
  - RDP = 预留页（2026-10-04 用户需求）：现有 xtcp visitor 的 bindPort 固定
          指向远端 22（SSH/SFTP 复用），3389 需专门的 rdp_<serverName> 隧道
          （该能力为后续功能，见 core/frp_remote 尚无端口映射字段），故会话
          总览操作列的 RDP 链接已移除，本页承载能力说明与后续接入位置

与设置分工（设计稿）：设置-远程连接 = 凭据/FRP 静态配置；本页 = 会话与
隧道的操作面。frpc 服务器参数两处共享同一 settings 键，避免双写分叉。

线程模型：本模块所有槽都在 GUI 线程，既不 sleep 也不等结果；耗时对象（frps
感知、RTT 探测、球桌检索、frpc 健康检查）都在 core 层用后台线程执行，回调经
信号回 GUI。新增功能须沿用这条边界，否则一次网络超时就会冻住整个远程页。

刷新节奏：frps 感知 30s 一轮、RTT 探测 30s 一轮、frpc 状态由进程事件驱动，
三者可能同帧到达。会话总览用 _refresh_pending + singleShot(0) 把连发合并成
一次重建，其余视图只做可见性门控——隐藏页收到信号不重绘，切回来时 showEvent
补一次，省掉看不见的开销又不留旧数据。
"""
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QTableWidgetItem, QAbstractItemView, QHeaderView,
                               QListWidget, QListWidgetItem)
from qfluentwidgets import (TitleLabel, CaptionLabel, BodyLabel, StrongBodyLabel,
                            CardWidget, LineEdit, PasswordLineEdit, PushButton,
                            PrimaryPushButton, ComboBox, TableWidget,
                            ToolButton, MessageBox, FluentIcon, InfoBar,
                            InfoBarPosition, CheckBox, SearchLineEdit,
                            SegmentedWidget)

from main_window.pivot_page import PivotPage
from main_window.tool_hub import _transparent, _make_terminal
from core import credentials as cred
from core.frp_remote import (get_session_manager, SOURCE_MANUAL,
                             SOURCE_SNK, _FRPC_SERVER_DEFAULTS)
from core.frps_admin import get_frps_client
from core.visitor_probe import get_prober
from core.ops_link_delegate import LINKS_ROLE, install_ops_links
from core.spin_fit_patch import FittedSpinBox
from database import table_db
from workers.aftersale_worker import AftersaleDBWorker
from windows.aftersale.common import _style_cand_list

# 语义色取值对齐 core/design_tokens，共 success/warning/danger/info/accent 五种
_C_SUCCESS = QColor(0x1a, 0x9e, 0x6c)
_C_WARNING = QColor(0xc9, 0x8a, 0x2d)
_C_DANGER = QColor(0xcf, 0x44, 0x52)
_C_ACCENT = QColor(0x00, 0x83, 0x8f)
_C_MUTED = QColor(0x6b, 0x72, 0x80)
_C_INFO = QColor(0x2f, 0x6b, 0xd6)

# frps 感知四态 → 展示文案/颜色。None 单列一态，不并入 offline：没配置感知
# 时显示「离线」会把「没数据」误判成「设备掉线」，让人白排查一轮。
_FRPS_CELL = {
    "online": ("● online", _C_SUCCESS),
    "offline": ("● offline", _C_DANGER),
    "unregistered": ("○ 未注册", _C_WARNING),
    None: ("— 无感知", _C_MUTED),
}

# 质量评级 → 文案/颜色，口径来自 core.visitor_probe：等级由成功率与 P95
# 共同决定；本页只做映射，将来调阈值也不用动 UI 层。
_GRADE_CELL = {
    "good": ("优", _C_SUCCESS), "nice": ("良", _C_INFO),
    "fair": ("一般", _C_WARNING), "poor": ("差", _C_WARNING),
    "bad": ("异常", _C_DANGER), "unknown": ("—", _C_MUTED),
}


# ==================== TCP 直连共享工具，与主面板 remote_mixin 同源 ====================

def _app_settings_merged() -> dict:
    """读配置门面的合并视图；任何异常都退化成空 dict。

    本文件大量动作只是「顺带同步」配置（回填表单、记忆连接模式），
    配置层损坏时应该只是这些联动失效，绝不能让整个远程页崩掉。
    """
    try:
        from core import app_settings
        return app_settings.get_merged()
    except Exception:
        return {}


def load_tcp_servers() -> list:
    """保存的 TCP 直连服务器列表（settings 键 tcp_servers）。

    条目是 "host:port" 字符串，与主面板 remote_mixin 同键同格式，
    任一入口增删都会在对方下次读取时自然生效，不需要额外同步。
    逐条过滤非字符串与空值：配置被手改成脏数据时只丢坏条目，
    不至于让整个列表都不可用。
    """
    servers = _app_settings_merged().get("tcp_servers", [])
    if not isinstance(servers, list):
        return []
    return [s for s in servers if isinstance(s, str) and s.strip()]


def save_tcp_server(entry: str) -> bool:
    """追加一条服务器并持久化；重复或写入失败都返回 False。

    返回值只表达「有没有新增」：调用方据此决定提示保存成功还是「已存在」；
    写配置失败同样返回 False，避免 UI 谎报保存成功。
    """
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
    """删除一条服务器并持久化；不存在返回 False。

    先读全量再整表写回：settings 存的是列表而非单键，没有原子删除接口，
    只能以「读-改-写」保证其余条目不受影响。
    """
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

    disabled 优先于 registered：「已断开」的记录仍在注册表里，若先判存在就
    会显示成已注册启用，用户会误以为端口还在监听。

    Returns:
        (state, rec)：state ∈ "registered"（已注册启用）/"disabled"（已断开
        保留注册）/"none"（未注册）；rec 为匹配的注册表记录（无则 {}）。
    """
    name = str(name or "").strip()
    for rec in mgr.records():
        if str(rec.get("serverName", "")) == name:
            return ("disabled" if rec.get("disabled") else "registered", rec)
    return ("none", {})

# 感知通道状态 → 人话。它只描述通道本身，不代表任何一条隧道的好坏：
# 通道不可达时所有隧道都显示「无感知」，而不是离线。
_CHANNEL_TEXT = {
    "ok": "frps 感知通道 在线",
    "unreachable": "frps 感知通道 不可达",
    "unauthorized": "frps 感知通道 凭据被拒",
    "error": "frps 感知通道 响应异常",
    "unconfigured": "frps 感知通道 未配置",
}


def _frps_cell(snk: str) -> tuple:
    """snk → (状态文案, 颜色)；感知不可用时 online() 返回 None。

    None 走「— 无感知」分支而不是 offline：缓存过期、通道断开都属于我们
    这边暂时看不到，不能据此断言对方掉线。
    """
    return _FRPS_CELL.get(get_frps_client().online(snk), _FRPS_CELL[None])


def _fmt_traffic(nbytes: int) -> str:
    """字节数 → 概览卡用的紧凑流量文本（MB 保留一位，其余取整到 KB）。

    概览卡一格只有几个字符宽，精确到字节没有可读性；但 0 要显示 0
    而不是 0KB，否则会被当成「没有数据」而非「没有流量」。
    """
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
    """RTT 序列 → 字符小柱状图（▁▂▄▆█，超时记为 ·）。

    用 Unicode 方块字符代替自绘控件：表格里逐行画 sparkline 需要自绘
    delegate，代价远高于收益；字符图在任何字体和缩放下都不会错位。
    每轮按当前窗口的 min/max 归一化，只表达相对起伏，不表达绝对值。
    """
    blocks = "▁▂▄▆█"
    out = []
    good = [v for v in vals if v is not None]
    lo = min(good) if good else 0
    hi = max(good) if good else 1
    span = max(1.0, hi - lo)
    # 只取最近 width 个样本：更早的历史已经不影响「现在稳不稳」的判断
    for v in list(vals)[-width:]:
        if v is None:
            out.append("·")
        else:
            idx = int((v - lo) / span * (len(blocks) - 1) + 0.5)
            out.append(blocks[idx])
    return "".join(out)


def _stat_card(parent, color):
    """统计数字卡（大数字 + 小标签），返回三元组供调用方后续改值与配色。

    拆开返回而不是封装成 setter：各视图刷新时要按状态改数字颜色
    （在线绿、未启用灰），直接拿到控件最省事。
    """
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
    """会话总览页：本地 frpc 状态、frps 概览、统计条与隧道/会话表。

    表格一行对应一条注册隧道，「操作」列是唯一的连接入口；数据全部来自
    session manager 与 frps 客户端的内存快照，本页自己不发起网络请求。
    """

    def __init__(self, win, hub, parent=None):
        """搭会话总览页：状态行 + frps 概览卡 + 5 张统计卡 + 9 列隧道表

        状态行只放全局动作（启动/感知/会话窗口/新建），单条隧道的操作全在
        表内自绘链接里——页面上不出现两套入口，用户不用猜点哪个。
        """
        super().__init__(parent)
        self.setObjectName("remoteSessionWork")
        # objectName 供 QSS 精确命中本页；_transparent 让页面透出亚克力底，
        # 否则 Pivot 容器会盖上一层实色，与设计稿的通透感不符
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
        # 两个状态标签刻意分开：frpc 是本机进程状态，frps 是管理通道的感知
        # 状态；合并显示会让人把「感知断了」误当成「隧道断了」
        self.lbl_frpc = BodyLabel("frpc 未启动", body)
        head.addWidget(self.lbl_frpc)
        self.lbl_frps = CaptionLabel("", body)
        head.addWidget(self.lbl_frps)
        head.addStretch(1)
        # 总开关随运行态翻转文案与动作：未运行就启动，按注册表 apply，运行中
        # 优雅停止，走两段式 /api/stop，与隧道配置页同口径，断会话前先确认
        # 停止走两段式而不是直接杀进程：frpc 要先从服务端注销隧道，
        # 强杀会让服务端以为连接还活着，新连接被拒。
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

        # ---------- frps 概览卡，数据来自 /api/serverinfo，随感知刷新 ----------
        lay.addWidget(self._build_overview(body))

        # ---------- 统计条，5 卡整行铺满并等分 ----------
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
        # 单选 + 整行：操作链接按行号取记录，多选会让「点的是哪一行」歧义；
        # 禁编辑、禁换行同理，行高固定才能让自绘链接的命中区域稳定。
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
        # 操作列委托自绘文字链接，共 SSH/SFTP/断开/删除 四项，零子控件——
        # 2026-09-24 P0-2 委托化：原先 330px 是为容纳 5 个自适应宽按钮，
        # 改成文字链接后密度高得多，260px 就够。RDP 链接后来从 _add_row 里
        # 摘掉了，这一列现在只剩 SSH/SFTP/断开/删除 四种动作。
        self.table.setColumnWidth(8, 260)
        self.table.setMinimumHeight(260)
        cl.addWidget(self.table, 1)
        lay.addWidget(card, 1)
        # 操作列视图级委托，setItemDelegate 不能按列设置，否则会丢 hover 高亮；
        # 点击回调经 _row_recs 行映射取当前记录，行号失效的问题靠刷新时重建映射解决
        # 行映射必须在每次刷新后重建：模型行号在排序或筛选后会变，
        # 缓存旧映射会点到别的行上，删错会话。
        self._row_recs: dict = {}
        install_ops_links(self.table, self._on_ops_link)

        self.lbl_hint = CaptionLabel("", body)
        self.lbl_hint.setTextColor(QColor(0, 0, 0, 170), QColor(255, 255, 255, 170))
        lay.addWidget(self.lbl_hint)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(body)

        # 信号联动：bound method 随本 widget 销毁自动断开，lambda 也不会泄漏；
        # 五个来源合并进同一个 refresh 入口，刷新口径只留一处。
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
        # 探测只改 RTT 列，但仍走整表刷新：单列更新会让「在线但超时」的行
        # 留下旧的状态色，比多刷一次更糟。
        # 合帧门控。感知、探测、注册表变更等五个信号源常常在同一帧里
        # 连发，这里只允许合并成一次重建，具体合并在 refresh 里做。
        self._refresh_pending = False

    def _on_probe_round(self):
        """探测轮次结束：只在页面可见时重建表格。

        探测每 30s 一轮且页面常驻，不可见时重建纯属浪费；但可见时跳过会
        让 RTT 列停在旧值，所以只做可见性门控，不做额外节流。
        """
        # 质量视图非当前页时不整表重绘，探测每 30s 跑一轮，成本可忽略但保持克制
        # 探测是定时自动跑的，用户切走后还在重绘等于白烧 CPU，
        # 长会话列表下一次全表重绘要几百毫秒。
        if self.isVisible():
            self.refresh()

    def _on_probe_now(self):
        """手动发起一次 frps 感知，并武装下一次完成回执。

        先置 _receipt_armed 再发请求：请求可能在当前栈返回前就完成，
        顺序反过来会漏掉回执，按钮就变成「点了没反应」。
        """
        # 回执统一由 _on_refresh_finished 出口：成功失败都要有下文，
        # 否则数据没变化时看起来像没反应。
        self._receipt_armed = True
        self._frps.request_refresh()
        self._win._show_info_bar("已发起 frps 感知请求", "info", duration=2000)

    def _on_refresh_finished(self, state):
        """感知完成的唯一提示出口：仅在有人等结果时弹一次。

        周期刷新也走这个信号，若不加 _receipt_armed 门控，每 30s 就会
        自动弹一条提示，把信息栏刷成噪声。
        """
        # 未挂接回执说明是周期刷新，静默处理
        if not getattr(self, "_receipt_armed", False):
            return
        # 页面已切走时不再提示：回执是给正看着这一页的人看的
        if not self.isVisible():
            return
        # 下面这组判定与上面重复，是历史遗留的复制粘贴，逻辑上等价。留着的
        # 代价是改门控条件时必须记得两处一起改，否则会出现漏弹或重复弹。
        # 重复的第二个 isVisible 是双重保险：刷新完成可能跨过一次 show/hide，
        # 第一道门放行之后页面又恰好被切走，此时再拦一次。
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
        """每次露出页面都对齐一次数据。

        页面隐藏期间信号仍在累积，重新露出时如果不刷新，
        第一眼看到的会是很久以前的快照。
        """
        super().showEvent(event)
        self.refresh()

    # ---------- frp 总开关 ----------

    def _on_frp_toggle(self):
        """总开关：运行中=优雅停止，未运行=按注册表启动。

        停止会断开所有已建立的会话，所以必须确认；启动失败只提示不重试，
        避免在配置本身有问题时反复拉起进程。
        """
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
        # 没有注册表就没有 apply 的输入，直接引导去注册，比只报错有用
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
            # 这是断开保留注册的新口径，提示用户重连即恢复
            self._win._show_info_bar(
                "全部隧道处于「已断开」状态：点该行的 SSH/SFTP 重连，"
                "frpc 会自动随首条重连隧道启动", "warning", duration=5000)
            return
        self._win._show_info_bar("frpc 已启动（按注册表应用配置）", "success")
        self._win._append_log("[远程] 总开关启动 frpc")

    def _update_frp_toggle(self):
        """按运行态同步按钮文案与图标。

        文案和图标必须同源更新：只改文字会留下「显示停止却挂着播放键」
        的错位，用户会怀疑按钮失灵。
        """
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
        # 字段顺序照 frps 网页面板排：版本→客户端→连接→流量→代理，
        # 由粗到细，运维两边对照时不用重新找位置。
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
        """把 serverinfo 快照写进概览卡；无数据时整卡置灰。

        serverinfo 由感知线程维护，这里只读快照不触发请求；
        RuntimeError 只会在客户端已销毁时出现，安静返回即可。
        """
        try:
            info = self._frps.serverinfo()
        except RuntimeError:
            return
        # 感知未配置或首轮未回来：整卡显示「—」而不是保留上一轮数字，
        # 否则用户分不清「真的没数据」和「数据已过期」
        if not info:
            for num in self.ov_fields.values():
                num.setText("—")
                num.setStyleSheet(
                    f"color: {_C_MUTED.name()}; font-size: 17px; font-weight: 700;")
            self.ov_src.setText("GET /api/serverinfo · 暂无数据")
            return
        counts = info.get("proxyTypeCounts") or {}
        # 总数取自 serverinfo 的类型计数，在线数取自代理快照：两个来源不同，
        # 感知断连时会呈现「总数有、在线 0」，这是预期而不是统计错误
        xtcp_total = int(counts.get("xtcp") or 0)
        snap = self._frps.snapshot()
        xtcp_online = sum(1 for it in snap["proxies"].values()
                          if it.get("status") == "online")

        def _set(key, text, color):
            """写一格概览数字；字号粗体统一在这里，避免五格各写一套样式"""
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
        """把五个信号源的刷新请求合帧到一个 singleShot(0) 里

        重建成本约每行 1ms 起，五连发就是五倍白费，而且肉眼可见地闪。
        """
        # 五个刷新源同帧连发时归并到下一轮事件循环，只整表重建一次：
        # 重建成本约每行 1ms+，五连发就是五倍白费，而且肉眼可见地闪
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QTimer.singleShot(0, self._refresh_now)

    def _refresh_now(self):
        """真正执行一次整表重建（由 refresh 合帧后调用）。

        先清 pending 再重建：重建过程中新到的信号应该排在下一帧，
        而不是被本次吞掉，否则最后一轮变更会丢。
        """
        self._refresh_pending = False
        mgr = self._mgr
        try:
            records = mgr.records()
        except RuntimeError:
            return
        running = mgr.is_running()
        # 已断开的隧道仍留在注册表，重连即恢复，统计必须与启用项分开，
        # 合在一起会让用户以为少配了隧道，实际只是临时断了。
        # 否则「3 条启用」里混进断开的行，用户会找不到哪条真的没启动
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
        # 每行 online() 只算一次并缓存：旧实现循环里两次、_add_row 里再两次，
        # 每轮多打四倍缓存查询，行数一多就能看出卡顿
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
            # 失联取三者之一：frps 权威 offline、探测判 bad、frpc 未运行。
            # 对会话而言结果都是连不上，统一上报让自愈逻辑不必区分原因
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
        # 设备在线卡：感知不可用时显示 —。这里绝不能显示 0/N，用户会把它
        # 读成「全部离线」而跑去排查设备。
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
        # 中位数而非均值：一次 2s 的打洞尖刺就能把均值拉爆，中位数更贴近
        # 实际体感，也避免一条坏隧道拖累整页指标
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

        # 整表重建而不是逐行 diff：行数只有几十，重建比维护 diff 简单；但行号
        # 映射必须同步清空，否则点击会落到上一轮已失效的记录上
        self.table.setRowCount(0)
        self._row_recs.clear()
        for rec in records:
            self._add_row(rec, running,
                          frps_states.get(str(rec.get("serverName", ""))))
        if not records:
            self.lbl_hint.setText("暂无隧道 —— 点右上「新建隧道」注册 xtcp visitor")
        else:
            self.lbl_hint.setText(
                "「连接」复用球桌一键直连（SSH/SFTP 标签窗口）；"
                "断开=关会话并释放端口（保留注册，点 SSH/SFTP 即重连），"
                "删除=彻底移除注册与持久化配置（需确认、不可恢复）")

    def _add_row(self, rec, running, frps_state=None):
        """把一条注册记录渲染成表格行。

        列顺序必须与表头严格一致，setItem 全部按列号硬编码；frps_state 由
        refresh 统一传入，None 表示这轮没算到，按「无感知」渲染。
        """
        mgr = self._mgr
        sn = str(rec.get("serverName", ""))
        port = int(rec.get("bindPort", 0) or 0)
        sessions = mgr.sessions_on_port(port)
        table = self.table
        r = table.rowCount()
        table.insertRow(r)
        # 状态优先级：已断开 > 未启动 > 会话中 > 已预热 > 已连接。顺序不能乱，
        # 例如 frpc 没跑时即便有残留会话，也该显示「未启动」而不是「会话中」
        if rec.get("disabled"):
            # 已断开并保留注册：隧道已从 frpc 摘除、端口已释放，
            # 与「删除」不同——注册还在，SSH/SFTP 点击即重连
            status, color = "已断开", _C_WARNING
        elif not running:
            status, color = "未启动", _C_MUTED
        elif sessions:
            status, color = "会话中", _C_SUCCESS
        elif rec.get("prewarmedAt"):
            # P2-6：本轮预热打洞已完成，点击连接免打洞等待，体感上是秒连
            # 打洞通常要几秒，预热过的连接给用户的体感是「点开就进」，
            # 这是访客面板最容易被感知的体验指标。
            status, color = "已预热", _C_INFO
        else:
            status, color = "已连接", _C_ACCENT
        it = QTableWidgetItem(status)
        it.setForeground(color)
        if rec.get("prewarmedAt") and not sessions:
            it.setToolTip(f"预热打洞完成于 {rec.get('prewarmedAt')}，"
                          "点击连接免打洞等待")
        table.setItem(r, 0, it)

        # frps 感知列四态：online/offline/未注册/无感知。状态由 refresh 循环
        # 统一算好传入，这里不再经 _frps_cell 重复探测，避免每行多两次查询
        frps_text, frps_color = _FRPS_CELL.get(frps_state, _FRPS_CELL[None])
        it_frps = QTableWidgetItem(frps_text)
        it_frps.setForeground(frps_color)
        info = self._frps.info(sn)
        if info:
            it_frps.setToolTip(
                f"当前连接 {info['curConns']} · 最近上线 {info['lastStartTime'] or '—'}")
        table.setItem(r, 1, it_frps)

        # RTT 与质量列合并成一格：均值 + 评级文字 + 迷你趋势。趋势图要求
        # 至少 4 个探测样本，样本不足时只留文字，否则两三个点画出来的折线
        # 更像噪声而不是趋势。
        st = self._prober.stats(sn)
        g_text, g_color = _GRADE_CELL.get(st["grade"], _GRADE_CELL["unknown"])
        avg = st["avg_ms"]
        rtt_text = (f"{avg:.0f}ms {g_text}" if avg is not None else f"— {g_text}")
        # 少于 4 个样本不画趋势：两三个点连不出形状，反而占掉列宽挤掉数字
        spark = _spark(st["recent"]) if st["samples"] >= 4 else ""
        it_rtt = QTableWidgetItem(f"{rtt_text} {spark}".rstrip())
        it_rtt.setForeground(g_color if avg is not None else _C_MUTED)
        p95 = st["p95_ms"]
        it_rtt.setToolTip(
            f"样本 {st['samples']}（成功 {st['ok']}） · "
            f"P95 {('%.0fms' % p95) if p95 is not None else '—'}")
        table.setItem(r, 5, it_rtt)

        # 流量复用上面的 info，不再查一次：同一行两次查询可能落在不同快照上，
        # 会出现「连接数」和「流量」来自不同轮次的错位
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

        # frps 权威离线时省略打开类链接：点了只会盲等超时。无感知/未注册不省略，
        # 因为 visitor 可以先注册、设备后上线；已断开行保留链接，点它会重新启用
        confirmed_offline = (frps_state == "offline"
                             and not rec.get("disabled"))
        disabled_row = bool(rec.get("disabled"))
        self._row_recs[r] = rec
        # 操作列用委托自绘文字链接，零子控件；委托不支持置灰，禁用语义改为省略链接。
        # RDP 不在行内：visitor 的 bindPort 只映射远端 22，mstsc 打 SSH 端口必然失败
        links = []
        if not confirmed_offline:
            links += [("SSH", "primary", "ssh"), ("SFTP", "primary", "sftp")]
        if not disabled_row:
            links.append(("断开", "ghost", "disconnect"))
        links.append(("删除", "danger", "delete"))
        cell = QTableWidgetItem("")
        cell.setData(LINKS_ROLE, tuple(links))
        table.setItem(r, 8, cell)

    def _on_ops_link(self, row, _col, action):
        """操作列链接回调：按行号找回记录再分发。

        行号只是当下视图的索引，重建后就失效，所以一律经 _row_recs 取记录；
        取不到说明点击落在重建间隙，直接忽略。
        """
        rec = self._row_recs.get(row)
        if rec is None:
            return  # 行重建竞态：映射里已无此行，忽略本次点击
        sn = str(rec.get("serverName", ""))
        # rdp 仍留在可接受集合内：行链接已不再生成，但 core.frp_remote.
        # open_session 本就支持 'rdp'，3389 专用隧道落地后恢复入口零改动
        if action in ("ssh", "sftp", "rdp"):
            self._open(action, sn, rec)
        elif action == "disconnect":
            self._disconnect(sn)
        elif action == "delete":
            self._delete(sn)

    # ---------- 操作 ----------

    def _open(self, kind, sn, rec):
        """按 kind 打开远程会话；失败提示由 notifier（主窗口）统一弹出。

        本方法不捕获异常：open_session 内部已把错误转成信息栏，
        这里再捕获只会产生两份同样的失败信息。
        """
        table_id = str(rec.get("tableId", "") or "")
        self._mgr.open_session(kind, sn, table_id, notifier=self._win)
        self._win._append_log(f"[远程] 经隧道 {sn} 打开 {kind.upper()} 会话")

    def _confirm_transfer(self, sn):
        """断开/删除前确认：端口上有传输或驻留会话时要求二次确认。

        is_busy_on_port 覆盖 SFTP 传输与 SSH 取证两类任务，它们占着同一条隧道，
        静默断开会让正在拷的文件半途而废，也会中断取证现场。
        """
        rec = next((r for r in self._mgr.records()
                    if r.get("serverName") == sn), None)
        if rec is None:
            return False
        if not self._mgr.is_busy_on_port(rec.get("bindPort", 0)):
            return True
        dlg = MessageBox(
            "会话任务进行中",
            f"隧道「{sn}」上有文件传输 / 终端会话任务正在进行。\n"
            "继续将立即中断任务并关闭相关会话。确定吗？", self)
        dlg.yesButton.setText("继续")
        dlg.cancelButton.setText("取消")
        return bool(dlg.exec())

    def _disconnect(self, sn):
        """断开隧道：关会话、释放端口，但保留注册。

        apply 走后台线程，结果回 GUI 线程再提示；这里先 refresh 一次，
        是为了让行状态立刻变「已断开」，不必等后台结果。
        """
        mgr = self._mgr
        if not mgr.is_running():
            # frpc 未运行：隧道本就未建立，无需断开（新口径下注册仍保留，
            # 与「删除」不再是唯一选项——重新启动 frp 即可恢复全部隧道）
            self._info("当前 frpc 未启动，隧道未建立，无需断开", "warning")
            return
        if not self._confirm_transfer(sn):
            return
        # P0-2：断开的 apply 走热重载 HTTP，已移后台，结果回 GUI 线程后提示；
        # 行内状态立即置「已断开」，refresh 由 visitors_changed 信号顺带触发
        # 热重载会阻塞几百毫秒，放在 GUI 线程会让整个窗口卡住，
        # 用户以为程序挂了。
        def _done(result: str):
            """后台断开回调：按结果给不同措辞，成功时明确「注册仍在」"""
            if result == "ok":
                self._info(f"已断开隧道 {sn}：会话已关闭、端口已释放，"
                           "注册保留，点 SSH/SFTP 可重连", "success")
            elif result == "not_running":
                self._info("当前 frpc 未启动", "warning")
            else:
                self._info(f"隧道 {sn} 断开失败", "error")
            self.refresh()

        mgr.disconnect_visitor_async(sn, _done)
        self.refresh()

    def _delete(self, sn):
        """彻底删除隧道：注册表与持久化配置一起移除，不可恢复。

        与「断开」的差别就在这里，所以必须弹确认；apply 同样后台化，
        避免删除多个 visitor 时界面长时间无响应。
        """
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
            """后台删除回调：失败带上原因码，避免只报「删除失败」无从排查"""
            if result == "ok":
                self._info(f"已删除隧道 {sn}", "success")
            else:
                self._info(f"隧道 {sn} 删除失败（{result}）", "error")
            self.refresh()

        mgr.delete_visitor_async(sn, _done)

    def _info(self, msg, kind):
        """统一走主窗口信息栏，固定 4 秒自动消失。

        这里是高频操作区，短提示不打断操作；需要用户决策的一律用
        MessageBox，两种反馈不混用。
        """
        self._win._show_info_bar(msg, kind, duration=4000)


# ==================== 视图 2：连接，XTCP 与 TCP 双模 ====================

class VisitorWork(QWidget):
    """连接视图（原「P2P 访客」，2026-09-24 P1 双模化）：XTCP 访客 + TCP 直连

    - XTCP 模式：visitor 注册表 + 添加访客表单（写入统一 TOML 并落盘），
      含**设备** SSH 账号/密码（settings ssh_user/ssh_pass，与主面板 P2P 表单、
      统一设置页同键同源；2026-10-04 补，此前 xtcp 侧无凭据入口）
    - TCP 模式：保存的服务器列表（settings tcp_servers，与主面板同源）
      + host/port/凭据表单 → open_direct_session 直连（不经 frpc）；凭据独立存
      settings tcp_ssh_user/tcp_ssh_pass，**不覆盖**设备凭据（2026-10-04 拆分）
    模式选择记忆到配置（remote_conn_mode），下次进入保持。
    """

    def __init__(self, win, hub, parent=None):
        """搭「连接」页：XTCP 注册表 + TCP 直连两套互斥表单

        两模式共用一块版面，切换时整块替换而不是并排堆叠：并排会让用户把
        设备凭据（xtcp）填进直连卡（tcp），两边账号语义完全不同。
        """
        super().__init__(parent)
        self.setObjectName("remoteVisitorWork")
        _transparent(self)
        self._win = win
        self._hub = hub
        self._mgr = get_session_manager()
        # 记录 SSH 账号/密码表单是否被用户改过，改过就不做回填，免得刷新冲掉输入
        self._cred_dirty = False

        body = QWidget(self)
        body.setAutoFillBackground(False)
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 8, 20, 12)
        lay.setSpacing(12)

        # 连接模式切换。选中的模式会写进 settings，下次进这一页沿用上次
        # 的选择，不让用户每次重新切一遍。
        self.mode_seg = SegmentedWidget(body)
        self.mode_seg.addItem("xtcp", "XTCP 访客（P2P 打洞）")
        self.mode_seg.addItem("tcp", "TCP 直连（经 frps / 局域网）")
        self.mode_seg.currentItemChanged.connect(self._on_conn_mode_changed)
        lay.addWidget(self.mode_seg)
        # 两个模式互斥且共用同一块版面：只留一个分段控件、切换时整块替换下方卡片，
        # 避免两套表单同时摊在屏幕上，用户把设备凭据填进直连卡。

        # ---------- 添加访客卡，置于注册表上方，球桌号搜索可联动带出 serverName ----------
        self.add_card = CardWidget(body)
        add_card = self.add_card  # 局部别名（下方大量 parent 引用沿用原名）
        al = QVBoxLayout(self.add_card)
        al.setContentsMargins(16, 14, 16, 14)
        al.setSpacing(10)
        al.addWidget(BodyLabel("添加访客", self.add_card))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        # 球桌号搜索与球桌管理搜索栏同款，动态搜索 300ms 防抖加异步查库：
        # 命中球桌后带出 snk_code 填入 serverName，并同步关联球桌号
        self.edit_table_search = SearchLineEdit(add_card)
        self.edit_table_search.setPlaceholderText("搜索球桌号带出 serverName")
        self.edit_table_search.setFixedWidth(260)
        self.edit_table_search.textChanged.connect(self._on_table_search_changed)
        self.edit_table_search.clearSignal.connect(self._hide_table_candidates)
        # serverName 必须与球桌 frpc_xtcp_panel.toml 里的 proxy 名逐字一致：访客侧
        # 按名字找对端，拼错不会报错，只表现为访客一直连不上。
        self.edit_name = LineEdit(add_card)
        self.edit_name.setPlaceholderText("snk 标识（与球桌 frps proxy 同名）")
        self.edit_name.setFixedWidth(260)
        # 留空即沿用 frpc 默认 secretKey；两侧填不一致时打洞建不起来，
        # 日志里只有一行连接失败，看不出是密钥问题。
        self.edit_key = PasswordLineEdit(add_card)
        self.edit_key.setPlaceholderText("secretKey（留空用默认）")
        self.edit_key.setFixedWidth(200)
        # FittedSpinBox：端口是 4~5 位数字，qfw SpinBox 不为右侧自绘按钮预留
        # 文本空间，固定宽度在大字号/高 DPI 下会把数字压到箭头下面，
        # 具体原因见 FittedSpinBox 自己的模块说明。
        self.spin_port = FittedSpinBox(add_card)
        self.spin_port.setRange(0, 65535)
        self.spin_port.setValue(0)
        self.spin_port.setSpecialValueText("随机")
        # 选填：填了才能在注册表里按球桌号反查；留空注册照样成功，
        # 只是这一列显示「—」，事后无法从隧道名回溯是哪张球桌。
        self.edit_table_id = LineEdit(add_card)
        self.edit_table_id.setPlaceholderText("关联球桌号（选填）")
        self.edit_table_id.setFixedWidth(130)
        # SSH 账号/密码。xtcp 隧道的 SSH/SFTP 用的是**设备自己**的账号
        # 密码，也就是 settings 里的 ssh_user/ssh_pass，与主面板 P2P 表单、
        # 统一设置页同键同源。此前 xtcp 侧没有入口，只能到 TCP 直连卡里改，
        # 一改就把隧道路径的凭据覆盖成直连主机的账号，比如 root，隧道 SSH
        # 随即报认证失败，所以在这里单独开一份并和直连侧彻底分开。
        self.xtcp_user = LineEdit(add_card)
        self.xtcp_user.setPlaceholderText("设备 SSH 账号（如 newbv）")
        self.xtcp_user.setFixedWidth(260)
        self.xtcp_pass = PasswordLineEdit(add_card)
        self.xtcp_pass.setPlaceholderText("设备 SSH 密码")
        self.xtcp_pass.setFixedWidth(200)
        # textEdited 仅用户输入时触发，setText 不会触发，所以不会把回填当作用户修改
        # 换成 textChanged 的话，每次刷新回填都会被判成「用户改过」，
        # 保存按钮会一直亮着，用户以为有未保存的修改。
        self.xtcp_user.textEdited.connect(lambda _t: self._mark_cred_dirty(True))
        self.xtcp_pass.textEdited.connect(lambda _t: self._mark_cred_dirty(True))
        # 来源显式化 + 「记住密码」开关 + 显式清除入口，三者是一起做的。
        # 此前任何一次连接都会把表单值写回 credentials.json：用户为试一次临时密码
        # 敲进去的值，会在毫不知情时永久替换掉已保存的密码，且没有任何清除入口。
        # 默认勾选，行为与从前一致；取消勾选后密码只进内存会话表，
        # 不写进 credentials.json。
        self.xtcp_remember = CheckBox("记住密码", add_card)
        self.xtcp_remember.setChecked(True)
        self.xtcp_remember.setToolTip(
            "勾选：连接/注册后把设备账号密码写入 config/credentials.json"
            "（DPAPI 加密，重启后仍在）\n"
            "取消：只用于本次运行，关闭应用即丢失，不改动已保存的密码")
        self.xtcp_pass.textEdited.connect(
            lambda _t: self._refresh_cred_hint(cred.KIND_DEVICE))
        self.xtcp_remember.toggled.connect(
            lambda _c: self._refresh_cred_hint(cred.KIND_DEVICE))
        self.xtcp_cred_hint = CaptionLabel("", add_card)
        self.xtcp_cred_hint.setTextColor(_C_MUTED, _C_MUTED)
        lbl0 = BodyLabel("球桌搜索:", add_card)
        lbl1 = BodyLabel("serverName:", add_card)
        lbl2 = BodyLabel("secretKey:", add_card)
        lbl3 = BodyLabel("本地端口:", add_card)
        lbl4 = BodyLabel("关联球桌:", add_card)
        lbl5 = BodyLabel("SSH 账号:", add_card)
        lbl6 = BodyLabel("SSH 密码:", add_card)
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
        grid.addWidget(lbl5, 3, 0)
        grid.addWidget(self.xtcp_user, 3, 1)
        grid.addWidget(lbl6, 3, 2)
        grid.addWidget(self.xtcp_pass, 3, 3, Qt.AlignLeft)
        grid.addWidget(self.xtcp_remember, 4, 1, Qt.AlignLeft)
        grid.setColumnStretch(4, 1)
        al.addLayout(grid)
        al.addWidget(self.xtcp_cred_hint)
        # 构造期回填 settings 里的设备凭据，主面板或设置页改过的也能带出来
        self._load_ssh_credentials()
        # 球桌候选列表默认隐藏，固定 132px：出现/消失时下方按钮不跳动；
        # 搜索恰好命中唯一球桌时直接带出，不弹这个列表。
        self._cand_list = QListWidget(add_card)
        self._cand_list.setFixedHeight(132)
        self._cand_list.setVisible(False)
        self._cand_list.itemClicked.connect(self._on_table_candidate_clicked)
        _style_cand_list(self._cand_list)
        al.addWidget(self._cand_list)

        # 注册与连接拆成两个按钮：注册只写 toml，可离线批量登记多台球桌，
        # 只有连接才拉起 frpc 占隧道，避免登记动作就把隧道配额吃掉。
        btns = QHBoxLayout()
        self.btn_add = PrimaryPushButton(FluentIcon.ADD, "添加并注册", add_card)
        self.btn_add.clicked.connect(self._on_add)
        btns.addWidget(self.btn_add)
        self.btn_add_connect = PushButton(FluentIcon.PLAY, "添加并连接 SSH", add_card)
        self.btn_add_connect.clicked.connect(self._on_add_connect)
        btns.addWidget(self.btn_add_connect)
        btns.addStretch(1)
        # 显式清除入口。此前想忘掉一个密码只能靠「输入新密码覆盖」，
        # 既忘不掉也清不掉，所以单独给一个按钮。
        self.btn_xtcp_clear = PushButton(FluentIcon.DELETE, "清除已保存凭据",
                                         add_card)
        self.btn_xtcp_clear.setToolTip(
            "清空 config/credentials.json 里保存的设备 SSH 密码（ssh_pass），"
            "账号保留")
        self.btn_xtcp_clear.clicked.connect(
            lambda: self._clear_credentials(cred.KIND_DEVICE))
        btns.addWidget(self.btn_xtcp_clear)
        al.addLayout(btns)
        cap = CaptionLabel(
            "注册仅写入 frpc_xtcp_panel.toml（不拉起 frpc）；「添加并连接」经一键直连建立隧道。"
            "搜索球桌号可带出 serverName（snk 标识）与关联球桌。"
            "SSH 账号/密码是**设备凭据**（与主面板、设置页同源），TCP 直连卡里填的是"
            "直连主机自己的凭据，两边互不覆盖。取消「记住密码」可只对本次运行生效。",
            add_card)
        cap.setTextColor(QColor(0, 0, 0, 170), QColor(255, 255, 255, 170))
        al.addWidget(cap)
        lay.addWidget(self.add_card)

        # TCP 直连卡。这一卡与主面板的 TCP 模式是同一套配置同一套语义，
        # 任一侧改动都会在另一侧下次读取时自然生效。
        self.tcp_card = CardWidget(body)
        tl = QVBoxLayout(self.tcp_card)
        tl.setContentsMargins(16, 14, 16, 14)
        tl.setSpacing(10)
        tl.addWidget(BodyLabel("TCP 直连", self.tcp_card))
        tcp_grid = QGridLayout()
        tcp_grid.setHorizontalSpacing(12)
        tcp_grid.setVerticalSpacing(8)
        # 存储键是「host:port」整串，落在 settings tcp_servers，主机名里不能再带冒号；
        # 带冒号的主机名会让键解析出错，两台机器被误判成同一台，
        # 凭据互相覆盖。
        # 端口单独成列，改端口等于新增一条记录，而不是覆盖同一主机的旧值。
        self.tcp_host = LineEdit(self.tcp_card)
        self.tcp_host.setPlaceholderText("主机地址（IP 或域名）")
        self.tcp_host.setFixedWidth(220)
        self.tcp_port = FittedSpinBox(self.tcp_card)
        self.tcp_port.setRange(1, 65535)
        self.tcp_port.setValue(22)
        self.tcp_user = LineEdit(self.tcp_card)
        self.tcp_user.setPlaceholderText("SSH 用户名")
        self.tcp_user.setFixedWidth(150)
        self.tcp_pass = PasswordLineEdit(self.tcp_card)
        self.tcp_pass.setPlaceholderText("SSH 密码")
        self.tcp_pass.setFixedWidth(150)
        # 读合并视图而不是裸 settings.json：缺键时能拿到默认值，文件半写坏也不抛错。
        # 这张卡在主窗口初始化阶段构造，一次读取异常会连带整个远程面板起不来。
        _merged = _app_settings_merged()
        # 直连凭据独立存放，键名 tcp_ssh_user 与 tcp_ssh_pass，2026-10-04 拆分：直连
        # 主机多是 frps 服务器，登录账号是 root，设备凭据用 newbv，两者不能共用
        # 一把钥匙会同时开错两扇门：设备用 newbv 登录，frps 用 root，
        # 共用时改一边就另一边认证失败。
        # ssh_user/ssh_pass 会互相覆盖，表现为「改过 TCP 账号后隧道 SSH 认证失败」
        self.tcp_user.setText(str(_merged.get("tcp_ssh_user", "") or ""))
        self.tcp_pass.setText(str(_merged.get("tcp_ssh_pass", "") or ""))
        # 与 XTCP 卡同一口径：默认勾选，语义与从前一致；取消勾选则密码
        # 只对本次运行生效，不落盘。
        self.tcp_remember = CheckBox("记住密码", self.tcp_card)
        self.tcp_remember.setChecked(True)
        self.tcp_remember.setToolTip(
            "勾选：连接后把直连主机账号密码写入 config/credentials.json"
            "（DPAPI 加密，重启后仍在）\n"
            "取消：只用于本次运行，关闭应用即丢失")
        self.tcp_pass.textEdited.connect(
            lambda _t: self._refresh_cred_hint(cred.KIND_DIRECT))
        self.tcp_user.textEdited.connect(
            lambda _t: self._refresh_cred_hint(cred.KIND_DIRECT))
        self.tcp_remember.toggled.connect(
            lambda _c: self._refresh_cred_hint(cred.KIND_DIRECT))
        self.tcp_cred_hint = CaptionLabel("", self.tcp_card)
        self.tcp_cred_hint.setTextColor(_C_MUTED, _C_MUTED)
        tcp_grid.addWidget(BodyLabel("主机:", self.tcp_card), 0, 0)
        tcp_grid.addWidget(self.tcp_host, 0, 1)
        tcp_grid.addWidget(BodyLabel("端口:", self.tcp_card), 0, 2)
        tcp_grid.addWidget(self.tcp_port, 0, 3, Qt.AlignLeft)
        tcp_grid.addWidget(BodyLabel("用户:", self.tcp_card), 1, 0)
        tcp_grid.addWidget(self.tcp_user, 1, 1)
        tcp_grid.addWidget(BodyLabel("密码:", self.tcp_card), 1, 2)
        tcp_grid.addWidget(self.tcp_pass, 1, 3, Qt.AlignLeft)
        tcp_grid.addWidget(self.tcp_remember, 2, 1, Qt.AlignLeft)
        tcp_grid.setColumnStretch(4, 1)
        tl.addLayout(tcp_grid)
        tl.addWidget(self.tcp_cred_hint)
        tcp_btns = QHBoxLayout()
        tcp_btns.setSpacing(8)
        # P0-4：图标口径与 windows/management/table_page.py 统一
        # 取值约定为 SSH 走 COMMAND_PROMPT，SFTP 走 FOLDER
        self.btn_tcp_ssh = PrimaryPushButton(FluentIcon.COMMAND_PROMPT, "连接 SSH", self.tcp_card)
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
        self.btn_tcp_clear = PushButton(FluentIcon.DELETE, "清除已保存凭据",
                                       self.tcp_card)
        self.btn_tcp_clear.setToolTip(
            "清空 config/credentials.json 里保存的直连主机 SSH 密码"
            "（tcp_ssh_pass），账号保留")
        self.btn_tcp_clear.clicked.connect(
            lambda: self._clear_credentials(cred.KIND_DIRECT))
        tcp_btns.addWidget(self.btn_tcp_clear)
        tl.addLayout(tcp_btns)
        # 保存的服务器表落在 settings tcp_servers，与主面板完全同源
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
        # 高度按 3~5 行给：条目少时不留大片空白，条目多则在表内滚动，
        # 不把整页顶长——TCP 卡与 XTCP 卡共用同一块版面，谁都不能独占高度。
        self.tcp_table.cellClicked.connect(self._on_tcp_row_clicked)
        tl.addWidget(self.tcp_table)
        tl.addSpacing(8)    # 表格与说明文字间距
        tcp_cap = CaptionLabel(
            "TCP 直连不经 frpc：局域网地址或 frps 转发端口（frps 代理页 tcp 页签可一键存入）。"
            "用户/密码是**直连主机自己**的凭据（存 tcp_ssh_user/tcp_ssh_pass，"
            "连接时写回、下次自动带出），不会覆盖 XTCP 卡的设备凭据；"
            "连接成功进全局会话窗口。取消「记住密码」可只对本次运行生效。",
            self.tcp_card)
        tcp_cap.setTextColor(QColor(0, 0, 0, 170), QColor(255, 255, 255, 170))
        tl.addWidget(tcp_cap)
        tl.addStretch(1)    # 剩余高度沉底：控件间隙不被均摊拉大
        self.tcp_card.setVisible(False)   # 默认 XTCP 模式
        lay.addWidget(self.tcp_card)

        # 球桌搜索防抖：停止输入 300ms 后才查库，避免逐字触发同步查询
        # 与球桌管理搜索栏、售后面板球房搜索是同一套范式
        self._search_kw = ""
        self._cand_rows = []
        self._cand_worker = None
        self._search_timer = QTimer(self)
        self._search_timer.setInterval(300)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._do_table_search)

        # ---------- 访客表，数据来自 XTCP 注册表，TCP 模式下隐藏 ----------
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

        # 「类型」列由 refresh 固定填 xtcp：注册表只登记 XTCP 访客，
        # 保留这一列是为了行结构与主面板会话表一致，读表的人不用换习惯。
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

        # 初始模式读记忆，缺省 XTCP；setCurrentItem 在构造期不触发
        # currentItemChanged 需要补一次，信号在 connect 之后才手动应用
        self.mode_seg.setCurrentItem(self._saved_conn_mode())
        self._apply_conn_mode(self._saved_conn_mode(), save=False)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(body)

        # 注册表增删与 frpc 启停都要刷表：前者决定列表行，后者决定「已断开」后缀
        self._mgr.visitors_changed.connect(self.refresh)
        self._mgr.frpc_state_changed.connect(lambda _b: self.refresh())

    def showEvent(self, event):
        """露脸时重载凭据并刷表：凭据可能被别的入口改过"""
        super().showEvent(event)
        # 别的入口改过凭据也要同步过来，包括主面板 P2P 表单与统一设置页
        # 不同步的话用户在主面板改了密码，这里还拿着旧值，
        # 连接时提示认证失败却查不出原因。
        self._load_ssh_credentials()
        self.refresh()

    # 连接模式切换，XTCP 与 TCP 双模共用。

    def _saved_conn_mode(self) -> str:
        """上次的连接模式（remote_conn_mode，缺省 xtcp）

        只有明确存过 "tcp" 才回 TCP，键缺失或值异常一律按 xtcp 处理，
        避免旧配置里残留的未知值让面板一打开就落在空白的直连页。
        """
        return "tcp" if _app_settings_merged().get(
            "remote_conn_mode") == "tcp" else "xtcp"

    def _on_conn_mode_changed(self, key):
        """分段控件切换 → 转发给 _apply_conn_mode 并记忆模式

        信号传进来的是 item key（"xtcp"/"tcp"），这里不直接改控件状态：
        显隐、落盘、刷表都收在一处，构造期恢复才能复用同一条路径。
        """
        self._apply_conn_mode(str(key))

    def _apply_conn_mode(self, key: str, save: bool = True):
        """切换 XTCP/TCP 双模显隐并记忆（save=False 用于构造期恢复）

        TCP 模式下把 XTCP 注册表一起隐藏：它是 XTCP 专属视图，留着会让人
        以为直连也走 visitor 隧道。落盘失败只吞异常，模式记忆是体验项，
        不能因为它让切换本身失败。
        """
        tcp = (key == "tcp")
        self.add_card.setVisible(not tcp)      # xtcp 添加访客表单
        self.visitor_card.setVisible(not tcp)  # xtcp 注册表
        self.tcp_card.setVisible(tcp)          # TCP 直连卡
        if save:
            try:
                from core import app_settings
                app_settings.set("remote_conn_mode", key)
            except Exception:
                # 记忆模式属于锦上添花，写不进去也不该拦住切换本身
                pass
        if tcp:
            self._refresh_tcp_table()

    # SSH 凭据。设备侧走 XTCP，直连侧走 TCP，两侧自 2026-10-04 起分开存。

    def _mark_cred_dirty(self, dirty: bool):
        """标记用户是否手改过凭据表单，决定 showEvent 回填时是否覆盖输入"""
        self._cred_dirty = bool(dirty)

    def _settings_snapshot(self) -> dict:
        """当前设置快照（优先主窗口缓存，异常回退配置门面）

        测试替身和启动早期可能没有 _load_settings，异常必须回退而不是上抛：
        构造期一次属性缺失就会让整个面板空白，比读到旧值严重得多。
        """
        try:
            return dict(self._win._load_settings() or {})
        except Exception:
            return _app_settings_merged()

    def _load_ssh_credentials(self, force: bool = False):
        """把设置里的**设备**SSH 凭据回填到 XTCP 表单

        force=False 且用户已改过（_cred_dirty）时不动，避免刷新冲掉输入。

        只回填设备凭据；直连凭据由 TCP 卡自己的键维护，两边不交叉覆盖。
        """
        if self._cred_dirty and not force:
            return
        s = self._settings_snapshot()
        user_key, pass_key = cred.credential_keys(cred.KIND_DEVICE)
        self.xtcp_user.setText(str(s.get(user_key, "") or ""))
        self.xtcp_pass.setText(str(s.get(pass_key, "") or ""))
        self._cred_dirty = False
        self._refresh_cred_hint(cred.KIND_DEVICE)

    def _persist_settings(self, data: dict) -> bool:
        """写回设置：优先主窗口 _save_settings（顺带刷新其内存缓存）

        直接走 app_settings.set 只是兜底：主窗口缓存不更新的话，本次运行内
        再打开凭据卡会读回旧值，看起来像「保存没生效」。
        """
        if not data:
            return False
        saver = getattr(self._win, "_save_settings", None)
        try:
            if callable(saver):
                saver(data)
                return True
            from core import app_settings
            for k, v in data.items():
                app_settings.set(k, v)
            return True
        except Exception:
            return False

    def _save_ssh_credentials(self) -> bool:
        """设备凭据写回 ssh_user/ssh_pass（非空才覆盖，与主面板同语义）

        P1-8 阶段 1：取消「记住密码」时**不写密码键**（构造上不可能落盘），
        密码只进 ``core.credentials`` 的进程内会话表，本次运行内仍然可用。
        """
        user = self.xtcp_user.text().strip()
        password = self.xtcp_pass.text()
        remember = self.xtcp_remember.isChecked()
        if password and not remember:
            cred.get_session_store().set(cred.KIND_DEVICE, user, password)
            self._win._append_log(
                "[远程] 设备 SSH 凭据仅用于本次会话（未勾选记住密码，未写入 ssh_pass）")
        data = cred.credential_patch(cred.KIND_DEVICE, user, password,
                                    remember=remember)
        if not data:
            return False
        # 只有值真的变了才写日志：每次连接前都会走保存路径，无变化也刷日志
        # 会把「已更新凭据」刷成噪音，真正改动的那次反而被淹掉。
        changed = any(str(self._settings_snapshot().get(k, "")) != str(v)
                      for k, v in data.items())
        if not self._persist_settings(data):
            return False
        self._cred_dirty = False
        if changed:
            self._win._append_log("[远程] 设备 SSH 凭据已更新（ssh_user/ssh_pass）")
        self._refresh_cred_hint(cred.KIND_DEVICE)
        return True

    def _refresh_cred_hint(self, kind: str):
        """刷新「凭据来源」提示（P1-8 阶段 1）

        来源口径 = 表单 > 本次会话 > 已保存（``core.credentials.resolve_credentials``），
        文案不含量明文，只说明来自哪里、是否已落盘。
        """
        hint = (getattr(self, "xtcp_cred_hint", None) if kind == cred.KIND_DEVICE
                else getattr(self, "tcp_cred_hint", None))
        if hint is None:      # 构造未完成（部分测试替身）时静默跳过
            return
        if kind == cred.KIND_DEVICE:
            user, pwd = self.xtcp_user.text(), self.xtcp_pass.text()
            remember = self.xtcp_remember.isChecked()
        else:
            user, pwd = self.tcp_user.text(), self.tcp_pass.text()
            remember = self.tcp_remember.isChecked()
        settings = self._settings_snapshot()
        user_key, pass_key = cred.credential_keys(kind)
        # 逐字段判定「表单值是不是用户自己敲的」：与已保存值相同的回填不算输入，
        # 否则卡片一打开就会显示成「本次输入」，而这个值其实来自 settings。
        # 用户会以为这台机器他已经连过一次，其实那只是上次保存的值。
        creds = cred.resolve_credentials(
            kind,
            form_username=user if user != str(settings.get(user_key, "") or "") else "",
            form_password=pwd if pwd != str(settings.get(pass_key, "") or "") else "",
            settings=settings, session=cred.get_session_store())
        text = cred.source_hint(kind, creds)
        # 有密码但没勾「记住密码」时补一句状态：不加的话用户看到「来自表单」
        # 会以为已经落盘，重启后才发现还得重新输。
        if creds.has_password and not remember:
            text += " · 已取消「记住密码」"
        hint.setText(text)

    def _clear_credentials(self, kind: str):
        """清除已保存的凭据密码（P1-8 阶段 1：显式清除入口）

        写空串即等于清除（``secrets.encrypt_secret("")`` 原样返回空串，读取侧对
        空值按「未配置」处理）；同时清掉表单与本次会话里的值，避免下次连接又把
        刚清除的密码原样写回去。
        """
        if not self._confirm_clear_credentials(kind):
            return
        user_key, pass_key = cred.credential_keys(kind)
        ok = self._persist_settings(cred.clear_patch(kind))
        # 会话表必须一起清：否则下次连接 resolve 仍会拿到内存里的旧密码，
        # 用户会以为「清除」没生效，再点一次还是同样的结果。
        cred.get_session_store().clear(kind)
        if kind == cred.KIND_DEVICE:
            self.xtcp_pass.setText("")
            self._cred_dirty = False
        else:
            self.tcp_pass.setText("")
        if ok:
            self._win._append_log(
                f"[远程] 已清除保存的{cred.kind_label(kind)}凭据（{pass_key} 已置空，"
                f"{user_key} 保留）")
            self._win._show_info_bar(
                f"已清除保存的{cred.kind_label(kind)}密码，下次连接需重新输入",
                "success")
        else:
            self._win._show_info_bar("清除失败：无法写入配置文件", "error")
        self._refresh_cred_hint(kind)

    def _confirm_clear_credentials(self, kind: str) -> bool:
        """清除前的二次确认（与删除远程文件的确认口径一致）

        密码落盘后无法回显，误清只能重新找运维要账号，所以这里不做「一键清」。
        """
        user_key, _pass_key = cred.credential_keys(kind)
        dlg = MessageBox(
            "确认清除凭据",
            f"将清空配置文件里保存的{cred.kind_label(kind)} SSH 密码，"
            f"账号（{user_key}）保留。\n清除后下次连接需要重新输入密码，是否继续？",
            self)
        dlg.yesButton.setText("清除")
        dlg.cancelButton.setText("取消")
        return bool(dlg.exec())

    # ---------- TCP 直连，与主面板 TCP 模式同源同语义 ----------

    def _refresh_tcp_table(self):
        """保存的服务器列表 ← settings tcp_servers（双端同源）

        整表重建而不是增量更新：条目来自配置文件、可能被主面板改过，
        重建一次即可对齐，代价只是几十个控件对象。
        """
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
            # e=entry 用默认参数把当前条目绑死：闭包晚绑定会让每个按钮都删最后一行
            btn.clicked.connect(lambda _=False, e=entry:
                                self._tcp_delete_entry(e))
            h.addWidget(btn, 0, Qt.AlignCenter)
            self.tcp_table.setCellWidget(r, 1, wrap)

    def _on_tcp_row_clicked(self, row, _col):
        """点击服务器行 → 填充表单（主面板同交互）

        行号就是 tcp_servers 下标（表由该列表逐行生成）；越界说明配置刚被
        别处改短，直接返回比填出一个错主机安全。
        """
        servers = load_tcp_servers()
        if not (0 <= row < len(servers)):
            return
        entry = servers[row]
        # 兼容不带端口的旧条目，这些都是手工写进配置的：解析失败按 22 兜底，
        # 比弹错误框更符合「点一行先看看」的预期。
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
        """当前 host:port 存入服务器列表（去重，双端同源）

        去重键就是 entry 整串：同一主机换端口算另一条记录，与主面板一致；
        想改端口请先删旧条目，否则列表里会留下两个指向同一台机器的项。
        """
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
        """删除当前选中行对应的服务器

        选中行与 load_tcp_servers() 顺序一致；配置在别处被改短时越界即忽略，
        不按行内容反删，避免删掉同名但不同端口的另一条。
        """
        servers = load_tcp_servers()
        row = self.tcp_table.currentRow()
        if 0 <= row < len(servers):
            self._tcp_delete_entry(servers[row])

    def _tcp_delete_entry(self, entry: str):
        """按条目整串删除（表内删除按钮与「删除选中」共用这一入口）"""
        if not delete_tcp_server(entry):
            return
        self._win._show_info_bar(f"已删除服务器 {entry}", "success")
        self._win._append_log(f"[远程] 已删除服务器: {entry}")
        self._refresh_tcp_table()

    def _tcp_connect(self, kind: str):
        """TCP 直连 SSH/SFTP（凭据独立于隧道设备凭据）

        直连不经 frpc，账号密码写 tcp_ssh_*；与 XTCP 的设备凭据分开存，
        两端互不覆盖。表单为空时回退已保存/会话凭据，不拿空账号去认证。
        """
        host = self.tcp_host.text().strip()
        if not host:
            self._win._show_info_bar("请输入主机地址", "warning")
            self.tcp_host.setFocus()
            return
        # 直连凭据写回 tcp_ssh_user 与 tcp_ssh_pass，非空才覆盖——**不再**写
        # ssh_user/ssh_pass：直连主机常是 frps 服务器，登录用 root，设备则是 newbv，
        # 共用一键会互相覆盖，导致改过 TCP 后隧道 SSH 报认证失败，这是用户反馈过的。
        # 非空才覆盖是给「只填了一半」留路：用户清空密码框时
        # 不该把已保存的密码一起抹掉。
        # 取消「记住密码」时不写密码键，只留在本次运行的内存会话表里。
        remember = self.tcp_remember.isChecked()
        password = self.tcp_pass.text()
        if password and not remember:
            cred.get_session_store().set(cred.KIND_DIRECT, self.tcp_user.text(),
                                         password)
            self._win._append_log(
                "[远程] 直连凭据仅用于本次会话（未勾选记住密码，未写入 tcp_ssh_pass）")
        self._persist_settings(cred.credential_patch(
            cred.KIND_DIRECT, self.tcp_user.text(), password, remember=remember))
        self._refresh_cred_hint(cred.KIND_DIRECT)
        # 表单为空时回退已保存或会话凭据，此前会拿空账号去认证
        # 拿空账号去认证只会得到一句「认证失败」，用户完全看不出
        # 是自己没填还是填错了。
        creds = cred.resolve_credentials(
            cred.KIND_DIRECT, form_username=self.tcp_user.text(),
            form_password=password, settings=self._settings_snapshot(),
            session=cred.get_session_store())
        self._win._append_log(f"[远程] TCP 直连 {kind.upper()} {host}:"
                              f"{self.tcp_port.value()}")
        self._mgr.open_direct_session(
            kind, host, self.tcp_port.value(), name=host, notifier=self._win,
            username=creds.username, password=creds.password)

    def refresh(self):
        """重绘访客注册表（XTCP）"""
        # 整表重建而不是按 snk 差量更新：记录最多几十条，且「已断开」与来源
        # 会随 frpc 状态整体翻转，差量更新反而要额外处理行错位。
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

    # ---------- 球桌搜索联动，沿用球桌管理动态搜索范式 ----------

    def _on_table_search_changed(self, text=""):
        """球桌号输入变动：失效旧关联，防抖后异步搜索球桌管理库

        关键词一变就先收起候选：不收起的话列表会停在旧结果上，
        用户点选后填进来的是上一轮搜到的球桌。空输入直接返回，不查库。
        """
        self._search_kw = str(text or "").strip()
        if not self._search_kw:
            self._hide_table_candidates()
            return
        self._search_timer.start()

    def _do_table_search(self):
        """防抖到期：按关键词异步查球桌（含关键词快照，过期结果丢弃）

        kw 在起线程前取值、回调再与 self._search_kw 比对：连续输入时，
        先返回的旧查询不能覆盖新关键词的候选，否则表单会被旧球桌带偏。
        """
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
        """异步查询返回：先比关键词，再按命中数决定自动带出还是列候选"""
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
        """点选候选 → 带出 snk 与球桌号并收起列表（行号即 _cand_rows 下标）"""
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
        """收起候选列表（清空搜索框、选中一条、查询失败都走这里）"""
        self._cand_list.setVisible(False)

    # ---------- 操作 ----------

    def _register(self):
        """表单 → register_visitor + persist；返回 serverName（失败空串）

        失败返回空串而不抛异常：调用方据此决定要不要继续建会话，错误提示
        在本函数内一次说完，上层不必重复弹窗。
        """
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
            # frpc 已在运行时必须热重载：注册只写 TOML，新 visitor 不进进程，
            # 本地 bindPort 就没有监听，「连接」复用该端口直连会被拒。未运行则不
            # apply，保持「添加并注册」不拉起 frpc 的语义，连接时自会冷启动。
            if self._mgr.is_running():
                self._mgr.apply()
        except (OSError, RuntimeError, ValueError) as e:
            self._win._show_info_bar(f"注册失败：{e}", "error", duration=5000)
            return ""
        # 注册成功后清空表单：serverName 已被占用，留着旧值下次注册会撞名；
        # 端口回 0（随机）同理，避免两条 visitor 抢同一个本地端口。
        self.edit_name.clear()
        self.edit_key.clear()
        self.edit_table_id.clear()
        self.edit_table_search.clear()
        self.spin_port.setValue(0)
        self.refresh()
        return sn

    def _on_add(self):
        """「添加并注册」：先落设备凭据再注册，注册成功才提示"""
        self._save_ssh_credentials()
        sn = self._register()
        if sn:
            self._win._show_info_bar(f"已注册访客 {sn}", "success")
            self._win._append_log(f"[远程] 注册 visitor {sn}")

    def _on_add_connect(self):
        """「添加并连接」：注册成功后按 serverName 反查 tableId 再开 SSH

        反查而不是直接用表单值：_register 成功会清空表单，球桌号只能从
        注册表记录里取，否则会话标题会丢掉关联球桌。
        """
        # 先落设备凭据：open_session 走 _do_open 读的就是 settings ssh_user/ssh_pass
        self._save_ssh_credentials()
        sn = self._register()
        if not sn:
            return
        rec = next((r for r in self._mgr.records()
                    if r.get("serverName") == sn), {})
        self._mgr.open_session("ssh", sn, str(rec.get("tableId", "") or ""),
                               notifier=self._win)
        self._win._append_log(f"[远程] 注册并连接 visitor {sn}（SSH）")


# ==================== 视图 3：连接质量，二期 P1 ====================

class QualityWork(QWidget):
    """连接质量：visitor RTT 统计卡 + 明细表（sparkline 趋势）

    数据源 core/visitor_probe.VisitorProber（本地 bindPort TCP connect
    计时）+ frps 感知交叉验证（frps online 但 RTT 全超时 = 打洞异常）。
    """

    def __init__(self, win, hub, parent=None):
        """搭「连接质量」页：状态行 + 四张概览卡 + RTT 明细表

        数据全部来自 prober 单例的快照，本页不自己发探测请求；手动按钮只是
        让用户换网络后立刻验一次，不改变 30s 的周期节奏。
        """
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
        # 顶部只留状态与两个手动入口：定时探测由 VisitorProber 驱动，
        # 手动按钮用于「换了网络马上验一次」，不必等下一个 30s 周期。
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
        # 四张卡固定顺序：探测状态 → 中位数 → 良好 → 超时；颜色取自 _C_*
        # 语义常量，换主题只改常量，不回头动这里的布局。
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
        # 表全列只读：数据由探测线程写进 prober，表格只做投影；
        # 放开编辑会造出「改了但下一轮探测又覆盖」的假象。
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

        # 探测结果与 frps 名单都会改变「对端可达性」，合到 _on_round 做可见性门控；
        # frpc 启停则必须无条件重绘，否则状态行会停在「运行中」骗人。
        self._prober.changed.connect(self._on_round)
        self._frps.proxies_changed.connect(lambda _d: self._on_round())
        self._mgr.frpc_state_changed.connect(lambda _b: self.refresh())
        self._mgr.visitors_changed.connect(self.refresh)

    def showEvent(self, event):
        """露脸时重算卡片：探测线程在后台一直在跑，旧数字别留着"""
        super().showEvent(event)
        self.refresh()

    def _on_round(self):
        """探测/感知变化 → 仅在页面可见时重绘，隐藏页重绘纯属浪费"""
        if self.isVisible():
            self.refresh()

    def _on_probe_once(self):
        """「立即探测一轮」：守护线程跑 run_once，主线程只负责提示"""
        # 后台线程串行探测，主线程只刷 UI，run_once 幂等，耗时约隧道数乘 800ms
        import threading
        threading.Thread(target=self._prober.run_once, daemon=True,
                         name="visitor-probe-manual").start()
        self._win._show_info_bar("已发起一轮 RTT 探测", "info", duration=3000)

    def _on_row_activated(self, row, _col):
        """双击行 → 按第 0 列 serverName 反查记录并开 SSH"""
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
        """按 prober 统计重绘四张概览卡与明细表

        RuntimeError 只在管理器已销毁（窗口关闭途中）时出现，此时直接返回：
        关闭动画期间仍会收到 frpc/visitor 信号，不拦会连续抛错。
        """
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
            # 成功率只在有样本时算：0/0 会抛 ZeroDivisionError，把「没测过」
            # 写成 0% 又会被当成故障。
            rate = ("%.0f%%" % (st["ok"] / st["samples"] * 100)
                    if st["samples"] else "—")
            it_rate = QTableWidgetItem(rate)
            if st["samples"] and st["ok"] < st["samples"]:
                it_rate.setForeground(_C_WARNING)
            table.setItem(r, 5, it_rate)
            # 无样本时给「—」而不是空 sparkline：空串肉眼看不见，
            # 会让这一行看起来像渲染失败。
            spark = _spark(st["recent"], 12) if st["samples"] else "—"
            it_s = QTableWidgetItem(spark)
            it_s.setForeground(g_color)
            table.setItem(r, 6, it_s)
            table.setItem(r, 7, QTableWidgetItem(str(rec.get("bindPort", "") or "—")))


# ==================== 视图 5：frps 代理，与网页面板同源 ====================

# 类型页签顺序与 frps 网页面板 Proxies 页一致，两边对照着看不用在心里
# 重排。
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
        """搭 frps 代理清单页：关键词框 + 类型页签 + 9 列代理表

        页签按 frps 的 proxy type 划分而不是按业务分：这里看到的就是服务端
        实际的代理清单，与 frps 网页面板一一对应，排查时不靠转译。
        """
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
        # 输入防抖 300ms。逐键触发整表重建是大清单卡顿的主因，等用户
        # 停下来再查一次就够。
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

        # 七个类型平铺成页签而非下拉：一眼可见全部类型，少一次点击；
        # 数量再多就该换下拉，目前七项正好排满一行。
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
        # 列名与顺序直接照抄 frps Proxies 页，英文原名不翻译，方便两边
        # 对照同一列的数字；本地、操作两列是本项目追加的落地信息。
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
        # 操作列：委托自绘文字链接。2026-09-24 P0-2 委托化时把旧的
        # cellWidget 按钮组移掉了，那一套每行都要建五六个子控件，长列表下
        # 建表就能感觉到卡。
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

        # 两个代理信号都要接：all_proxies 覆盖全部七种类型，proxies 只带 xtcp；
        # 少接一个就会出现「切到 tcp 页签后数据停在上一次感知」的错觉。
        self._frps.all_proxies_changed.connect(lambda _d: self.refresh())
        self._frps.proxies_changed.connect(lambda _d: self.refresh())
        self._frps.channel_state_changed.connect(lambda _s: self.refresh())

    def _on_tab_changed(self, key):
        """页签切换 → 记下当前代理类型并重绘（_PROXY_TABS 之外的 key 忽略）"""
        if key in _PROXY_TABS:
            self._tab = key
            self.refresh()

    def showEvent(self, event):
        """切回本页时重绘：隐藏期间感知可能已更新，旧表会误导"""
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        """重绘当前页签的代理表：关键词过滤 + 本地三态 + 行内动作

        行号→记录映射另存 _row_recs：自绘链接回调只带行号，没有映射
        就还原不出点的是哪条代理，且表随时会被下一次感知重绘打乱。
        """
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
            # 状态列只分在线/非在线两色：frps 还有 waiting 之类中间态，
            # 逐态配色在深浅主题下都难分辨，按「能不能连」区分更实用。
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if col == 6:
                    item.setForeground(
                        _C_SUCCESS if status == "online" else _C_DANGER)
                elif col == 7:
                    item.setForeground(loc_color)
                self.table.setItem(r, col, item)
            # 「操作」列委托自绘文字链接，仅 xtcp 与 tcp 页签有内容，其余留空
            links = self._action_links(rec) if self._tab in ("xtcp", "tcp") else None
            if links:
                cell = QTableWidgetItem("")
                cell.setData(LINKS_ROLE, tuple(links))
                self.table.setItem(r, 8, cell)
        self.lbl_state.setText(
            f"frps 代理清单 · {self._tab.upper()} {len(rows)} 条"
            + ("" if snap_state == "ok" else f"（感知通道 {snap_state}）"))

    # 行内动作。2026-09-24 与 P0-2 委托化一起调整成当前形式。

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
        """未注册的 xtcp 代理 → 用默认参数补注册，随后直接开 SSH

        参数全取默认：现场设备侧只按 proxy 名配对，本地端口/密钥预填
        反而容易和 frpc.toml 现有值冲突，导致注册成功却打不通。
        """
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
        """删除本地注册（彻底移除；frps 侧 proxy 由现场设备管理，不受影响）

        先确认再删：这一步会同时改写持久化配置且没有撤销入口，
        误删只能重新「注册并连」补回来。
        """
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
        """tcp 代理 → 对 frps serverAddr:remotePort 直连（不经 frpc）

        remotePort 取自 frps 快照，可能是字符串，这里统一转 int 再下传。
        """
        self._win._append_log(f"[frps 代理] TCP 直连 {addr}:{port}（{name}）")
        self._mgr.open_direct_session(kind, addr, int(port), name=name,
                                      notifier=self._win)

    def _save_server(self, entry: str):
        """tcp 代理 → 保存到服务器列表（与主面板/「连接」视图同源）

        已存在时只提示、不报错：同一条代理被反复点「存服务器」是常态。
        """
        if save_tcp_server(entry):
            self._win._show_info_bar(f"已保存服务器 {entry}", "success")
            self._win._append_log(f"[frps 代理] 已保存服务器 {entry}")
        else:
            self._win._show_info_bar(f"{entry} 已在服务器列表", "info", duration=3000)
        self.refresh()


# ==================== 视图 4：隧道配置 ====================
# 连接诊断不属于本页，2026-09-07 定下的边界。入口保留在 设置-工具
# 的「连接诊断」行，点它会用 win._on_open_conn_diag 打开 ConnDiagPanel
# 独立窗口。

class TunnelConfWork(QWidget):
    """隧道配置：frpc 服务器参数（与设置-远程连接同键）+ frpc 控制 + 实时日志"""

    def __init__(self, win, hub, parent=None):
        """搭隧道配置页：frpc 接入点卡 + 管理通道卡 + 运行日志卡

        三张卡按「配一次 → 出问题才看 → 一直看」的使用频率自上而下排，
        日志占满剩余高度，滚动时不会把上面的表单顶出视野。
        """
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

        # 缺键用默认值兜底：设置里没有 frpc_server 时表单也得是可提交的
        # 完整状态，留空地址保存会被下面 _on_save 拦下，体验更差。
        frpc = dict(self._win._load_settings().get("frpc_server", {}) or {})
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self.edit_addr = LineEdit(card)
        self.edit_addr.setText(str(frpc.get("serverAddr",
                                            _FRPC_SERVER_DEFAULTS["serverAddr"])))
        self.edit_addr.setFixedWidth(200)
        self.spin_port = FittedSpinBox(card)
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(int(frpc.get("serverPort",
                                             _FRPC_SERVER_DEFAULTS["serverPort"])))
        # auth_method 只有 token 与 none 两态，这是 frps 支持的全部：配成 token
        # 而 frps 侧没开，握手会被直接拒；所以改这一项必须走「保存并重连」。
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
        # 开机静默预连开关。启动 6s 后自动拉起 frpc，把启用过的隧道恢复
        # 起来并预热打洞，点 SSH/SFTP 就是秒连。当场勾选则立即执行一次。
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

        # ---------- 管理通道卡，二期 P0 与 P1，含 frps 感知和 frpc admin ----------
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

        # frps_admin 走 credentials 域，口令做 DPAPI 加密，与上面 frpc_server
        # 的明文 token 分属两套配置，不能合并存储。
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
        # 用按钮而非复选框：探测默认开启，按钮文案直接写「点下会发生什么」，
        # 复选框还要先读当前勾选态才知道自己在开还是在关。
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

        # ---------- 运行日志卡，整宽铺开占满剩余空间 ----------
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
        # 只读终端控件而不是 QTextEdit：复用 ANSI 渲染与自动滚动，
        # 日志里 frpc 自己带的颜色码不会被显示成乱码。
        self.log_view = _make_terminal(out_card, None)
        ol.addWidget(self.log_view, 1)
        lay.addWidget(out_card, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(body)

        # 日志由 manager 统一发出，总开关与自动重连等入口的操作也进同一份
        # 视图，排障时不用去翻不同页面的提示条。
        self._mgr.log_message.connect(self._append_log)
        self._mgr.frpc_state_changed.connect(lambda _b: self._update_state())
        self._frps = get_frps_client()
        self._frps.channel_state_changed.connect(lambda _s: self._update_state())
        self._frps.refresh_finished.connect(self._on_channel_finished)
        self._test_armed = False
        self._prober = get_prober()
        self._update_state()

    def showEvent(self, event):
        """每次显示都重读状态：frpc 可能已被别的入口启动或停止"""
        super().showEvent(event)
        self._update_state()

    def _update_state(self):
        """刷新状态灯、按钮可用性与通道/探测文案（显示时与每次操作后调用）"""
        running = self._mgr.is_running()
        self.lbl_state.setText("● frpc 运行中" if running else "○ frpc 未启动")
        self.lbl_state.setTextColor(
            _C_SUCCESS if running else _C_MUTED,
            _C_SUCCESS if running else _C_MUTED)
        self.btn_stop.setEnabled(running)
        # 「启用隧道」的判定：仅剩已断开即 disabled 时，apply 只会 stopped，
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
        """把 manager 的日志行追加到只读终端；右端空白先裁掉，避免滚动抖动"""
        self.log_view.append(str(msg).rstrip())

    # ---------- 操作 ----------

    def _collect_frpc(self):
        """收集表单 → frpc_server 配置（只覆盖本页四个字段，其余键原样保留）

        在已存配置上合并而不是重建：frpc_server 里还有日志、TLS 等键，
        整块替换会抹掉它们，下次 apply 直接起不来。
        """
        merged = dict(self._win._load_settings().get("frpc_server", {}) or {})
        merged.update({
            "serverAddr": self.edit_addr.text().strip(),
            "serverPort": int(self.spin_port.value()),
            "auth_method": self.combo_auth.currentText(),
            "auth_token": self.edit_token.text(),
        })
        return merged

    def _on_save(self):
        """保存 frpc 服务器参数；frpc 在跑且有空隧道时立即按新参数重启

        只在这两个条件都成立时重启：否则会为空注册表拉起 frpc，
        状态灯从「未启动」变成异常，比不重启更难解释。
        """
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
        """「启用隧道」：按注册表 apply；无记录时只提示，不拉起空 frpc"""
        if not self._mgr.records():
            self._win._show_info_bar("无已注册隧道，无需启动 frpc", "warning")
            return
        self._apply_frpc("frpc 已按注册表重启")

    def _apply_frpc(self, ok_msg):
        """统一 apply 入口：把 OSError/RuntimeError 转成提示条，不让异常冒进事件循环"""
        try:
            self._mgr.apply()
        except (OSError, RuntimeError) as e:
            self._win._show_info_bar(f"frpc 启动失败：{e}", "error", duration=6000)
            return
        self._win._show_info_bar(ok_msg, "success")

    def _on_stop(self):
        """优雅停止：确认 → 断会话 → /api/stop 两段式，注册表保留

        不走「清空注册表再 apply」的老绕行：那会让 visitors_changed 连发、
        与持久化抢写，状态灯还会闪成「全空」一下。
        """
        dlg = MessageBox("优雅停止 frpc",
                         "停止后所有隧道失效，已打开的 SSH/SFTP/RDP 会话将断开。\n"
                         "先经 /api/stop 让 frpc 自行收尾（端口干净释放），"
                         "2.5s 未退出才强杀。\n注册表保留，下次连接或重启 frpc 可恢复。"
                         "确定停止吗？", self)
        dlg.yesButton.setText("停止")
        dlg.cancelButton.setText("取消")
        if not dlg.exec():
            return
        # 先断会话再停进程：反过来会让已开的 SSH/SFTP 挂在半死连接上，
        # 用户看到的是「终端卡住」而不是「已停止」。
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
            # 就绪轮询后预热。bindPort 一旦监听成功就打洞，不必死等固定 3s。
            self._mgr.prewarm_when_ready()
            self._win._show_info_bar(
                "已立即执行一次静默预连并预热打洞", "success")
        elif result == "skipped_running":
            self._win._show_info_bar("已开启；frpc 正在运行，无需重复启动", "info")
        else:
            # 结果可能是 skipped_no_tunnel 或 failed，失败细节已进日志区
            self._win._show_info_bar(
                "已开启；当前无启用隧道可预连（已断开的隧道不自动复活，"
                "点 SSH/SFTP 即重连）" if result == "skipped_no_tunnel"
                else "已开启；本次预连未成功（详见 frpc 日志）", "warning")
        self._update_state()

    # ---------- 管理通道操作 ----------

    def _on_channel_save(self):
        """保存 frps 管理通道配置，并立即重启感知定时器

        清空 base_url 等于关闭感知，所以保存后必须 restart_timer：
        否则旧轮询还拿着刚删掉的地址继续打请求，一直报通道异常。
        """
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
        """「测试连接」：只置 _test_armed 再触发感知，结果由完成回调统一回执"""
        # 异步测试加完成回执，与「立即感知」同口径，GUI 全程零阻塞
        self._test_armed = True
        self._frps.request_refresh()
        self._win._show_info_bar("正在测试感知通道…", "info", duration=2000)

    def _on_channel_finished(self, state):
        """感知完成回调：只有 armed（用户点了测试）且页面可见时才弹提示"""
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
        """检测本机 frpc 管理通道（两次 HTTP 往返全部丢到后台线程）"""
        if not self._mgr.is_running():
            self._win._show_info_bar("frpc 未运行，无本机管理通道", "warning")
            return
        # 健康检查要两次 HTTP 往返，最坏 ~6s，全部移到后台线程，受理即反馈。
        # 结果经 manager 的 async_done 信号回 GUI 线程再提示。
        self._win._show_info_bar("正在检测 frpc 管理通道…", "info", duration=2000)
        admin_port = self._mgr.admin_port

        def _job():
            """后台两次往返：/healthz 探活，通过才读 /api/status 取详情"""
            code, _body = self._mgr.ping_admin("/healthz")
            if code != 200:
                return ("fail", code)
            code2, body2 = self._mgr.ping_admin("/api/status")
            return ("ok", code2, body2)

        def _done(result):
            """回 GUI 线程出结论：不可达时报端口号，正常时截 60 字符状态体"""
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
        """启停 RTT 质量探测（与质量页共用同一个 prober 单例）"""
        if self._prober.running:
            self._prober.stop()
            self._win._show_info_bar("RTT 质量探测已暂停", "warning")
        else:
            self._prober.start()
            self._win._show_info_bar("RTT 质量探测已启动", "success")
        self._update_state()


# ==================== 视图 6：RDP，预留，等用户需求落地再填内容 ====================


class RdpWork(QWidget):
    """RDP：远程桌面专用页（预留位；3389 隧道能力落地前为说明页）

    背景（2026-10-04 用户需求）：会话总览操作列里的 RDP 链接已移除——现有 frp
    xtcp visitor 的 bindPort 固定映射远端 22（SSH/SFTP 复用），RDP 需要 3389 的
    专门隧道（约定命名 rdp_<serverName>），该能力尚未实现、属后续功能。
    本页先承载「当前可用入口 + 后续接入清单」；3389 隧道落地后在此列出可 RDP
    设备，行内入口直接复用 core.frp_remote.open_session('rdp', serverName, tableId)
    （RDPPanel 的 mstsc 嵌入/免密/看门狗已实现，无需改动）。
    """

    def __init__(self, win, hub, parent=None):
        """搭 RDP 说明页：统计条 + 三张说明卡，不提供任何 RDP 入口

        操作列的 RDP 链接已移除，但页面保留下来解释原因与后续计划：
        直接删页会让用户以为功能丢了，比留一个说明页更迷惑。
        """
        super().__init__(parent)
        self.setObjectName("remoteRdpWork")
        _transparent(self)
        self._win = win
        self._hub = hub
        self._mgr = get_session_manager()
        self._frps = get_frps_client()

        body = QWidget(self)
        body.setAutoFillBackground(False)
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 8, 20, 12)
        lay.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        self.lbl_title = BodyLabel("远程桌面（RDP）—— 3389 隧道未开通", body)
        head.addWidget(self.lbl_title)
        head.addStretch(1)
        # 已开的 RDP 会话统一由会话中心接管，这里只做跳转入口，
        # 不再重复实现打开/嵌入逻辑，否则两处状态会各自漂移。
        btn_sessions = PushButton(FluentIcon.VIDEO, "打开会话中心", body)
        btn_sessions.setToolTip(
            "已打开的 RDP 会话（含主面板「远程」菜单发起的）都在会话中心里")
        btn_sessions.clicked.connect(self._on_open_sessions)
        head.addWidget(btn_sessions)
        lay.addLayout(head)

        strip = QHBoxLayout()
        strip.setSpacing(12)
        # 三张卡回答三个问题：注册了几条、有几条能 RDP、现在通不通；
        # 「可用」恒为 0 是当前能力的真实反映，不做成绿色成功态。
        self.card_tunnels, self.num_tunnels, self.lbl_tunnels = _stat_card(
            body, _C_ACCENT)
        self.card_rdp, self.num_rdp, self.lbl_rdp = _stat_card(body, _C_MUTED)
        self.card_state, self.num_state, self.lbl_state = _stat_card(
            body, _C_WARNING)
        for c in (self.card_tunnels, self.card_rdp, self.card_state):
            strip.addWidget(c, 1)
        lay.addLayout(strip)

        card1 = CardWidget(body)
        c1 = QVBoxLayout(card1)
        c1.setContentsMargins(16, 14, 16, 14)
        c1.setSpacing(8)
        c1.addWidget(StrongBodyLabel("为什么操作列里没有 RDP 了", card1))
        t1 = CaptionLabel(
            "会话总览里的隧道由 frp xtcp visitor 提供，visitor 的 bindPort 固定"
            "指向远端 22 端口（SSH/SFTP 复用同一端口），因此点 RDP 会连到 SSH "
            "端口并必然失败。该链接已按需求从操作列移除；RDP 的 3389 映射需要"
            "单独的隧道，属于后续功能。", card1)
        t1.setWordWrap(True)
        c1.addWidget(t1)
        lay.addWidget(card1)

        card2 = CardWidget(body)
        c2 = QVBoxLayout(card2)
        c2.setContentsMargins(16, 14, 16, 14)
        c2.setSpacing(8)
        c2.addWidget(StrongBodyLabel("现在能用什么", card2))
        t2 = CaptionLabel(
            "· 主面板「远程」菜单 → 远程桌面：TCP 模式下按手填 host/port 直连，"
            "目标可达的 3389 可以正常打开；XTCP 模式下同样走 visitor bindPort"
            "（22），不可用。\n"
            "· 已打开的 RDP 会话由会话中心接管（标签页 + mstsc 窗口嵌入 + 免密"
            "+ 断开/删除）。", card2)
        t2.setWordWrap(True)
        c2.addWidget(t2)
        lay.addWidget(card2)

        card3 = CardWidget(body)
        c3 = QVBoxLayout(card3)
        c3.setContentsMargins(16, 14, 16, 14)
        c3.setSpacing(8)
        c3.addWidget(StrongBodyLabel("后续接入清单（3389 隧道落地后）", card3))
        t3 = CaptionLabel(
            "1. frpc 侧为每台设备新增一个映射远端 3389 的 xtcp visitor"
            "（约定 rdp_<serverName>）；\n"
            "2. 注册表记录补充远端映射端口字段，本页据此列出「可 RDP」设备；\n"
            "3. 本页行内打开入口复用 core.frp_remote.open_session('rdp', "
            "serverName, tableId)，RDPPanel 无需改动。", card3)
        t3.setWordWrap(True)
        c3.addWidget(t3)
        lay.addWidget(card3)

        lay.addStretch(1)
        self.lbl_hint = CaptionLabel("", body)
        self.lbl_hint.setWordWrap(True)
        lay.addWidget(self.lbl_hint)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(body)

        self._mgr.visitors_changed.connect(self.refresh)
        self.refresh()

    def showEvent(self, event):
        """切回本页重算统计：注册表可能已被别处改动"""
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        """刷新统计卡：注册隧道总数 / 其中带 3389 映射的条数

        「可用」恒为 0 是预期状态而非 bug：现有 visitor 只映射远端 22，
        这里先把口径写死，3389 隧道上线后不必回头改本页。
        """
        records = self._mgr.records()
        self.num_tunnels.setText(str(len(records)))
        self.lbl_tunnels.setText("注册隧道（frpc xtcp visitor）")
        ready = self._rdp_capable(records)
        self.num_rdp.setText(str(len(ready)))
        color = _C_SUCCESS if ready else _C_MUTED
        self.num_rdp.setStyleSheet(
            f"color: {color.name()}; font-size: 22px; font-weight: 700;")
        self.lbl_rdp.setText("RDP 可用隧道（3389 映射）")
        self.num_state.setText("已开通" if ready else "未开通")
        self.num_state.setStyleSheet(
            f"color: {(_C_SUCCESS if ready else _C_WARNING).name()}; "
            "font-size: 22px; font-weight: 700;")
        self.lbl_state.setText("RDP 隧道状态")
        self.lbl_hint.setText(
            "说明：本页为 RDP 预留页。会话总览操作列已移除 RDP 链接（现有隧道只穿"
            " 22，没有 3389 映射）；RDP 能力随 3389 专用隧道一起上线。")

    @staticmethod
    def _rdp_capable(records):
        """挑出带 3389 映射的隧道（当前注册表无远端映射字段 ⇒ 恒为空）

        这是后续功能的检测位：3389 隧道落地后，只要注册记录带上远端映射端口
        （或沿用 rdp_ 前缀命名），本页统计与列表就会自动反映真实数量，
        不需要再改这里的口径。
        """
        out = []
        for rec in records or []:
            # 两个判据取「或」：新隧道带 remotePort 字段，历史命名走 rdp_
            # 前缀；只认一种会漏掉现场手工建的隧道。
            if str(rec.get("remotePort", "") or "").strip() == "3389":
                out.append(rec)
            elif str(rec.get("serverName", "")).startswith("rdp_"):
                out.append(rec)
        return out

    def _on_open_sessions(self):
        """打开会话中心（RDP 会话在那里以标签页承载）

        嵌入式宿主没有 _ensure_session_window，这种情况只提示不抛错。
        """
        fn = getattr(self._win, "_ensure_session_window", None)
        if fn is None:
            self._info("当前宿主窗口不支持会话中心", "warning")
            return
        try:
            fn()
        except (OSError, RuntimeError) as e:
            self._info(f"打开会话中心失败：{e}", "error")

    def _info(self, msg, kind):
        """统一走主窗口信息栏，4 秒自动消失（与操作区其它页同口径）"""
        self._win._show_info_bar(msg, kind, duration=4000)


# ==================== RemoteHub 容器 ====================

class RemoteHub(PivotPage):
    """远程页：横排 Pivot 六视图（与会话中心单例实时联动）

    二期（2026-09-21）：3→4 视图，新增「连接质量」；2026-09-24 加「frps 代理」
    （→5）；2026-10-04 加「RDP」预留页（→6，见 RdpWork）。进入本页即启动
    frps 在线感知（周期 GET）与本机 RTT 探测线程（幂等 start）。
    """

    def __init__(self, parent=None):
        """构造六视图并启动感知/探测单例（start 幂等，重复进入不重复起线程）"""
        super().__init__(parent)
        self.setObjectName("remoteHub")
        self._win = parent

        # 六个视图构造期一次建好：切换页签只做 show/hide，重建会丢掉正在
        # 编辑的凭据表单和未点选的球桌候选。
        self.session_work = SessionWork(self._win, self, self)
        self.visitor_work = VisitorWork(self._win, self, self)
        self.quality_work = QualityWork(self._win, self, self)
        self.frps_proxies_work = FrpsProxiesWork(self._win, self, self)
        self.tunnel_conf_work = TunnelConfWork(self._win, self, self)
        self.rdp_work = RdpWork(self._win, self, self)

        self.addPage(self.session_work, "会话总览")
        self.addPage(self.visitor_work, "连接")
        self.addPage(self.quality_work, "连接质量")
        self.addPage(self.frps_proxies_work, "frps 代理")
        self.addPage(self.tunnel_conf_work, "隧道配置")
        self.addPage(self.rdp_work, "RDP")
        # 统一六页宽度：Pivot 按内容自适应会让切页时整页横向跳动
        self.lock_pivot_width()
        self.switchTo(self.session_work)

        # 感知与探测随页面启动，单例幂等，配置缺失时内部自行静默
        get_frps_client().start()
        get_prober().start()

    def _apply_table_smooth_all(self):
        """统一设置页平滑开关联动：本页四张表（不含会话中心窗口里的同名表）"""
        from core.perf import apply_table_smooth_mode
        for t in (self.session_work.table, self.visitor_work.table,
                  self.quality_work.table, self.frps_proxies_work.table):
            apply_table_smooth_mode(t, panel="remote")
