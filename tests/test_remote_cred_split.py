# -*- coding: utf-8 -*-
"""XTCP 设备凭据 / TCP 直连凭据分离回归（2026-10-04 用户反馈）

用户原话：「发现一个bug，xtcp中没有修改账号密码的逻辑，如果我修改了tcp那边的
账号密码，tcp这边就会显示账号密码错误」。

根因（实测日志 logs/autowork_conn.log:5281
`[SSH] 127.0.0.1:48087 user=root | AuthenticationException | 认证失败，请检查用户名和密码`）：
隧道（XTCP）SSH/SFTP 与 TCP 直连**共用** settings ssh_user/ssh_pass，而两侧目标
主机不是同一台——直连主机是 frps 服务器（root），隧道尽头是球桌设备（newbv）：
TCP 卡一连接就把 root 写进共享键，隧道随即拿 root 去打设备，必然认证失败；而
XTCP 卡当时**没有任何凭据入口**，用户无处改回（改 TCP 卡又再次覆盖）。

本文件守住四条不变量：
1. XTCP 卡有设备凭据输入框（xtcp_user/xtcp_pass），连接/注册时写回 ssh_user/ssh_pass；
2. TCP 卡凭据存 tcp_ssh_user/tcp_ssh_pass，**不再**碰 ssh_user/ssh_pass；
3. TCP 直连把表单里的凭据显式传给 open_direct_session（不再靠全局键兜底）；
4. _do_open 显式凭据优先，未传才回退 settings（frps 代理页直连设备端口走回退）。
"""
import gc
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QWidget

import core.frp_remote as fr
from core import app_settings as fas
from core.secrets import SENSITIVE_KEYS
from core.visitor_probe import VisitorProber
import windows.remote_session.remote_hub as rh

_RECORDS = [
    {"serverName": "snk_11601", "bindPort": 14687, "tableId": "116-01",
     "source": "snk"},
]

# 设备凭据（隧道尽头，球桌）与直连凭据（frps 服务器）**不同**——这正是 bug 现场
_SETTINGS = {
    "ssh_user": "newbv",
    "ssh_pass": "devpw",
    "tcp_ssh_user": "root",
    "tcp_ssh_pass": "srvpw",
    "tcp_servers": ["203.0.113.9:22"],
    "frpc_server": {},
    "videos_dir": "",
}


class _FakeWin(QWidget):
    """主窗口替身：必须是 QWidget（RemoteHub 以宿主为 Qt parent）"""

    def __init__(self, settings):
        super().__init__()
        self.settings = dict(settings)
        self.saved = []
        self.logs = []
        self.bars = []

    def _load_settings(self):
        return self.settings

    def _save_settings(self, patch):
        self.saved.append(dict(patch))
        self.settings.update(patch)

    def _append_log(self, msg):
        self.logs.append(msg)

    def _show_info_bar(self, msg, kind, duration=4000):
        self.bars.append((msg, kind))

    def _ensure_session_window(self):
        return QObject()


@pytest.fixture
def qapp():
    yield QApplication.instance() or QApplication(sys.argv[:1])


@pytest.fixture(scope="module")
def _hub_env():
    """整模块只构造**一次**真实 RemoteHub 并复用。

    教训：原先每个用例各建一个 RemoteHub（10 次，每次 6 个视图、含 FrpsProxiesWork
    的八页签表格），Qt/Python 引用环使旧实例不释放，把全量 pytest 进程 RSS 顶过
    `tools/stress_test/metrics.py` 的 500MB 护栏 ⇒ 排在本文件之后的
    `tests/test_stress_smoke.py` 因 `over_limit` 提前 break 而失败
    （662 passed / 2 failed；去掉本文件即 650 passed / 0 failed）。

    替身依赖必须手动替换并恢复：function 级 monkeypatch 不能用于 module 级 fixture。
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
    for name in ("serverinfo", "online", "info", "client_version"):
        getattr(frps, name).return_value = None

    saved = {
        "rh.get_session_manager": rh.get_session_manager,
        "rh.get_frps_client": rh.get_frps_client,
        "rh.get_prober": rh.get_prober,
        "rh._app_settings_merged": rh._app_settings_merged,
        "fas.get_merged": fas.get_merged,
        "fas.get": fas.get,
        "fas.set": fas.set,
    }
    rh.get_session_manager = lambda: mgr
    rh.get_frps_client = lambda: frps
    # 真实 VisitorProber：构造不起线程（start() 才起），stats() 键集权威
    rh.get_prober = lambda: VisitorProber()
    rh._app_settings_merged = lambda: dict(_SETTINGS)
    fas.get_merged = lambda: dict(_SETTINGS)
    fas.get = lambda k, default=None: _SETTINGS.get(k, default)
    fas.set = lambda k, v: True

    app = QApplication.instance() or QApplication(sys.argv[:1])
    win = _FakeWin(_SETTINGS)
    h = rh.RemoteHub(win)
    h._win = win
    for page in h.pages():
        page._win = win
    try:
        yield win, h, mgr
    finally:
        for key, orig in saved.items():
            mod, name = key.split(".")
            setattr(rh if mod == "rh" else fas, name, orig)
        h.close()
        win.close()
        h.deleteLater()
        win.deleteLater()
        app.processEvents()
        gc.collect()


@pytest.fixture
def hub(_hub_env):
    """逐用例重置复用的 hub：设置/日志/输入框/调用记录回到初始态"""
    win, h, mgr = _hub_env
    win.settings.clear()
    win.settings.update(_SETTINGS)
    win.saved.clear()
    win.logs.clear()
    win.bars.clear()

    work = h.visitor_work
    work._cred_dirty = False
    work.xtcp_user.setText(_SETTINGS["ssh_user"])
    work.xtcp_pass.setText(_SETTINGS["ssh_pass"])
    work.tcp_host.clear()
    work.tcp_port.setValue(22)
    work.tcp_user.setText(_SETTINGS["tcp_ssh_user"])
    work.tcp_pass.setText(_SETTINGS["tcp_ssh_pass"])
    work._load_ssh_credentials(force=True)
    mgr.reset_mock()
    return h


@pytest.fixture
def mgr(monkeypatch, tmp_path):
    """真实 RemoteSessionManager + 隔离配置；_do_open / open_direct_session 可用"""
    monkeypatch.setattr(fr, "get_app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(fr, "show_info_bar", lambda *a, **k: None)
    monkeypatch.setattr(fr, "_load_settings",
                        lambda: {"ssh_user": "newbv", "ssh_pass": "devpw",
                                 "videos_dir": ""})
    m = fr.RemoteSessionManager()
    m.log_message.connect(lambda _msg: None)
    m._panels = []
    monkeypatch.setattr(
        m, "ensure_session_window",
        lambda: SimpleNamespace(add_session=m._panels.append))
    return m


def _install_fake_ssh_panel(monkeypatch, panels):
    """把 ssh_terminal.SSHTerminalPanel 换成记录参数的替身（不导入真面板）"""
    class FakePanel:
        def __init__(self, host, port, user, pwd, **kw):
            panels.append({"host": host, "port": port, "user": user,
                           "pwd": pwd})

    fake_mod = types.ModuleType("windows.remote_session.ssh_terminal")
    fake_mod.SSHTerminalPanel = FakePanel
    monkeypatch.setitem(sys.modules, "windows.remote_session.ssh_terminal",
                        fake_mod)


# ---------------- 不变量 4：_do_open / open_direct_session 凭据传递 ----------------


def test_do_open_explicit_credentials_win(mgr, monkeypatch):
    panels = []
    _install_fake_ssh_panel(monkeypatch, panels)
    mgr._do_open("ssh", "snk_11601", "", 12345, username="root",
                 password="srvpw")
    assert panels == [{"host": "127.0.0.1", "port": 12345, "user": "root",
                       "pwd": "srvpw"}]


def test_do_open_falls_back_to_settings_when_creds_absent(mgr, monkeypatch):
    panels = []
    _install_fake_ssh_panel(monkeypatch, panels)
    mgr._do_open("ssh", "snk_11601", "", 22222)
    assert panels == [{"host": "127.0.0.1", "port": 22222, "user": "newbv",
                       "pwd": "devpw"}]


def test_open_direct_session_forwards_explicit_credentials(mgr, monkeypatch):
    monkeypatch.setattr(fr, "PARAMIKO_AVAILABLE", True)
    seen = {}
    monkeypatch.setattr(mgr, "_do_open",
                        lambda *a, **kw: seen.update(kw, args=a))
    mgr.open_direct_session("ssh", "203.0.113.9", 22, name="srv",
                            username="root", password="srvpw")
    assert seen["args"][0] == "ssh"
    assert seen["host"] == "203.0.113.9"
    assert seen["username"] == "root" and seen["password"] == "srvpw"


def test_open_direct_session_defaults_creds_to_none(mgr, monkeypatch):
    """未传凭据（frps 代理页直连设备端口）→ 显式 None，由 _do_open 回退"""
    monkeypatch.setattr(fr, "PARAMIKO_AVAILABLE", True)
    seen = {}
    monkeypatch.setattr(mgr, "_do_open",
                        lambda *a, **kw: seen.update(kw, args=a))
    mgr.open_direct_session("ssh", "203.0.113.9", 6000)
    assert seen["username"] is None and seen["password"] is None


# ---------------- 不变量 1：XTCP 卡有设备凭据入口 ----------------


def test_cards_prefill_their_own_credentials(hub):
    work = hub.visitor_work
    assert work.xtcp_user.text() == "newbv"
    assert work.xtcp_pass.text() == "devpw"
    assert work.tcp_user.text() == "root"      # 直连凭据，不是 ssh_user
    assert work.tcp_pass.text() == "srvpw"


def test_save_device_credentials_writes_ssh_keys_only(hub):
    work = hub.visitor_work
    win = work._win
    work.xtcp_user.setText("newbv2")
    work.xtcp_pass.setText("pw2")
    assert work._save_ssh_credentials() is True
    assert win.saved[-1] == {"ssh_user": "newbv2", "ssh_pass": "pw2"}
    assert "tcp_ssh_user" not in win.saved[-1]
    assert any("设备 SSH 凭据已更新" in m for m in win.logs)


def test_save_device_credentials_skips_empty_fields(hub):
    work = hub.visitor_work
    win = work._win
    work.xtcp_user.setText("")
    work.xtcp_pass.setText("")
    assert work._save_ssh_credentials() is False
    assert win.saved == []


def test_add_connect_saves_credentials_before_register(hub, monkeypatch):
    work = hub.visitor_work
    order = []

    def _fake_save():
        order.append("creds")
        return True

    monkeypatch.setattr(work, "_save_ssh_credentials", _fake_save)
    monkeypatch.setattr(work, "_register",
                        lambda: (order.append("register"), "snk_11601")[1])
    work._on_add_connect()
    assert order == ["creds", "register"]
    # 打开会话仍按注册结果走隧道
    assert work._mgr.open_session.call_args[0][0] == "ssh"
    assert work._mgr.open_session.call_args[0][1] == "snk_11601"


def test_add_saves_credentials_too(hub, monkeypatch):
    work = hub.visitor_work
    win = work._win
    work.xtcp_user.setText("newbv3")
    work.xtcp_pass.setText("pw3")
    monkeypatch.setattr(work, "_register", lambda: "snk_11601")
    work._on_add()
    assert win.saved[-1] == {"ssh_user": "newbv3", "ssh_pass": "pw3"}


def test_load_credentials_respects_dirty_flag(hub):
    work = hub.visitor_work
    work.xtcp_user.setText("typed-by-user")
    work._mark_cred_dirty(True)
    work._win.settings["ssh_user"] = "changed-elsewhere"
    work._load_ssh_credentials()
    assert work.xtcp_user.text() == "typed-by-user"
    work._load_ssh_credentials(force=True)
    assert work.xtcp_user.text() == "changed-elsewhere"


# ---------------- 不变量 2：TCP 卡不再污染设备凭据 ----------------


def test_tcp_connect_writes_new_keys_and_keeps_device_creds(hub):
    work = hub.visitor_work
    win = work._win
    work.tcp_host.setText("203.0.113.9")
    work.tcp_port.setValue(2222)
    work.tcp_user.setText("root2")
    work.tcp_pass.setText("srvpw2")
    work._tcp_connect("ssh")

    patch = win.saved[-1]
    assert patch == {"tcp_ssh_user": "root2", "tcp_ssh_pass": "srvpw2"}
    assert "ssh_user" not in patch and "ssh_pass" not in patch
    # 设备凭据键在设置里原样不动
    assert win.settings["ssh_user"] == "newbv"
    assert win.settings["ssh_pass"] == "devpw"


def test_tcp_connect_passes_form_credentials_explicitly(hub):
    work = hub.visitor_work
    win = work._win
    work.tcp_host.setText("203.0.113.9")
    work.tcp_port.setValue(2222)
    work.tcp_user.setText("root")
    work.tcp_pass.setText("srvpw")
    work._tcp_connect("sftp")

    args, kwargs = work._mgr.open_direct_session.call_args
    assert args == ("sftp", "203.0.113.9", 2222)
    assert kwargs["username"] == "root"
    assert kwargs["password"] == "srvpw"
    assert kwargs["notifier"] is win


def test_tcp_connect_without_host_does_nothing(hub):
    work = hub.visitor_work
    work.tcp_host.setText("   ")
    work._tcp_connect("ssh")
    assert work._mgr.open_direct_session.call_count == 0
    assert work._win.saved == []


# ---------------- 键登记：新直连凭据必须进 credentials 域且加密 ----------------


def test_new_credential_keys_registered_and_sensitive():
    assert fas.domain_of("tcp_ssh_user") == "credentials"
    assert fas.domain_of("tcp_ssh_pass") == "credentials"
    assert "tcp_ssh_pass" in SENSITIVE_KEYS
    assert "tcp_ssh_user" not in SENSITIVE_KEYS   # 用户名不加密，与 ssh_user 一致
