# -*- coding: utf-8 -*-
"""远程页「RDP」页签 + 操作列移除 RDP 链接回归（2026-10-04 用户需求）

需求（用户原话）：「远程面板中，将操作的 rdp 移除，并单独建一个 rdp 页签」。
原因：现有 frp xtcp visitor 的 bindPort 只映射远端 22（SSH/SFTP 复用同一端口），
RDP 需要 3389 的专门隧道（约定 rdp_<serverName>），属后续功能——操作列上的
RDP 链接点了必然连到 SSH 端口，因此从操作列移除并另立页签承载。

本文件守住三条不变量：
1. 会话总览表操作列的文字链接清单里不再出现 RDP；
2. RemoteHub 注册的 Pivot 页签恰为六项、末位是「RDP」；
3. 弹出面板的 nav_icons 键必须覆盖全部页签（缺一个就退回内嵌 Pivot 形态）。
另守「能力不丢」：_on_ops_link 仍接受 'rdp'（3389 隧道落地后恢复入口零改动）。
"""
import ast
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QWidget

import windows.remote_session.remote_hub as rh
from core.ops_link_delegate import LINKS_ROLE
from core.visitor_probe import VisitorProber

# 三种行样本：普通 / 已预热 / 已断开（保留注册）
_RECORDS = [
    {"serverName": "snk_11601", "bindPort": 14687, "tableId": "116-01",
     "source": "snk"},
    {"serverName": "snk_4008", "bindPort": 12547, "tableId": "40-08",
     "source": "snk", "prewarmedAt": "2026-10-04 10:00"},
    {"serverName": "snk_12006", "bindPort": 26600, "tableId": "",
     "source": "snk", "disabled": True},
]

_EXPECT_TABS = ["会话总览", "连接", "连接质量", "frps 代理", "隧道配置", "RDP"]


class _FakeWin(QWidget):
    """主窗口替身：记录日志/提示条，并提供会话中心入口计数

    必须是 QWidget：RemoteHub 以宿主窗口为 Qt parent，且 TunnelConfWork 在
    构造期就读 `self._win._load_settings()`（视图构造期即触碰宿主，故不能
    先建 hub 再补 _win）。
    """

    def __init__(self):
        super().__init__()
        self.logs = []
        self.bars = []
        self.session_window_calls = 0

    def _load_settings(self):
        return {}

    def _save_settings(self, _patch):
        return None

    def _append_log(self, msg):
        self.logs.append(msg)

    def _show_info_bar(self, msg, kind, duration=4000):
        self.bars.append((msg, kind))

    def _ensure_session_window(self):
        self.session_window_calls += 1
        return QObject()


@pytest.fixture
def qapp():
    yield QApplication.instance() or QApplication(sys.argv[:1])


@pytest.fixture
def hub(qapp, monkeypatch):
    """真实 RemoteHub；manager / frps 感知 / 质量探测全换替身（绝不联网启停 frpc）

    manager 用 MagicMock：视图构造期会连接它的一堆信号（visitors_changed、
    frpc_state_changed、log_message）并读取 admin_port 等，逐个手写会一直漏。
    """
    mgr = MagicMock()
    mgr.records.return_value = [dict(r) for r in _RECORDS]
    mgr.is_running.return_value = False
    mgr.sessions_on_port.return_value = []
    mgr.is_transferring_on_port.return_value = False
    mgr.frps_server_addr.return_value = "127.0.0.1:7000"
    mgr.admin_port = 7400

    frps = MagicMock()
    frps.snapshot.return_value = {"proxies": {}, "state": "unconfigured"}
    frps.all_proxies.return_value = {}
    frps.serverinfo.return_value = None
    frps.online.return_value = None
    frps.info.return_value = None
    frps.client_version.return_value = None

    # 质量探测器用真实类：构造不起线程（start() 才起），stats() 直接给出
    # 权威空数据形态（grade/samples/recent 等键齐全，替身容易漏键）
    prober = VisitorProber()

    monkeypatch.setattr(rh, "get_session_manager", lambda: mgr)
    monkeypatch.setattr(rh, "get_frps_client", lambda: frps)
    monkeypatch.setattr(rh, "get_prober", lambda: prober)

    win = _FakeWin()
    h = rh.RemoteHub(win)
    h._win = win
    for page in h.pages():
        page._win = win
    return h


def _row_links(work, row):
    item = work.table.item(row, 8)
    assert item is not None, f"第 {row} 行操作列没有委托数据"
    return [link[0] for link in item.data(LINKS_ROLE)]


def _refresh_session(work):
    """SessionWork.refresh 只把重建排到下一轮事件循环，测试里直接跑同步实现"""
    work._refresh_now()


# ---------------- 不变量 1：操作列不再有 RDP ----------------


def test_session_ops_links_have_no_rdp(hub):
    work = hub.session_work
    _refresh_session(work)
    assert work.table.rowCount() == len(_RECORDS)
    for row in range(work.table.rowCount()):
        labels = _row_links(work, row)
        assert "RDP" not in labels, f"第 {row} 行操作列仍带 RDP：{labels}"


def test_session_ops_links_shape(hub):
    """普通行 = SSH/SFTP/断开/删除；已断开行 = SSH/SFTP/删除（无「断开」）"""
    work = hub.session_work
    _refresh_session(work)
    assert _row_links(work, 0) == ["SSH", "SFTP", "断开", "删除"]
    assert _row_links(work, 2) == ["SSH", "SFTP", "删除"]


def test_ops_link_handler_still_routes_rdp(hub):
    """能力不丢：3389 隧道落地后恢复行链接即可复用同一处理器"""
    work = hub.session_work
    _refresh_session(work)
    work._on_ops_link(0, 8, "rdp")
    work._mgr.open_session.assert_called_once_with(
        "rdp", "snk_11601", "116-01", notifier=work._win)


def test_ops_link_handler_routes_ssh(hub):
    work = hub.session_work
    _refresh_session(work)
    work._on_ops_link(1, 8, "ssh")
    work._mgr.open_session.assert_called_once_with(
        "ssh", "snk_4008", "40-08", notifier=work._win)


# ---------------- 不变量 2：RDP 页签 ----------------


def test_pivot_tabs_are_six_with_rdp_last(hub):
    labels = [text for _page, text, _icon in hub._page_meta]
    assert labels == _EXPECT_TABS
    assert hub.pages()[-1] is hub.rdp_work


def test_rdp_page_object_name_unique(hub):
    names = [page.objectName() for page in hub.pages()]
    assert len(set(names)) == len(names)
    assert hub.rdp_work.objectName() == "remoteRdpWork"


def test_rdp_page_stats_report_not_available(hub):
    """注册表没有 3389 映射字段 ⇒ RDP 可用数恒为 0、「未开通」"""
    work = hub.rdp_work
    work.refresh()
    assert work.num_tunnels.text() == str(len(_RECORDS))
    assert work.num_rdp.text() == "0"
    assert work.num_state.text() == "未开通"


def test_rdp_page_open_sessions_button(hub):
    hub.rdp_work._on_open_sessions()
    assert hub.rdp_work._win.session_window_calls == 1


# ---------------- 后续功能检测位 ----------------


def test_rdp_capable_detection():
    records = [{"serverName": "rdp_snk_1"},
               {"serverName": "snk_2", "remotePort": "3389"},
               {"serverName": "snk_3", "remotePort": 22},
               {"serverName": "snk_4"}]
    hit = rh.RdpWork._rdp_capable(records)
    assert [r["serverName"] for r in hit] == ["rdp_snk_1", "snk_2"]
    assert rh.RdpWork._rdp_capable([]) == []
    assert rh.RdpWork._rdp_capable(None) == []


# ---------------- 不变量 3：弹出面板导航图标覆盖全部页签 ----------------


def test_popout_nav_icons_cover_all_tabs(hub):
    """main_window.py 的 remoteHub nav_icons 键集必须等于页签集

    HubPopoutWindow 以 len(entries) == len(meta) 判定能否切成左侧子导航，
    缺一个键就静默退回内嵌 Pivot 形态（先例：4→5 视图时补 FluentIcon.GLOBE）。
    """
    src = Path(rh.__file__).resolve().parents[2] / "main_window" / "main_window.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    keys = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for k, v in zip(node.keys, node.values):
            if (isinstance(k, ast.Constant) and k.value == "remoteHub"
                    and isinstance(v, ast.Dict)):
                keys = {kk.value for kk in v.keys
                        if isinstance(kk, ast.Constant)}
    labels = {text for _page, text, _icon in hub._page_meta}
    assert keys, "未在 main_window.py 中找到 remoteHub 的 nav_icons 表"
    assert keys == labels, (
        f"nav_icons 键 {sorted(keys)} 与页签 {sorted(labels)} 不一致")
