# -*- coding: utf-8 -*-
"""远程页 P0+P1 联动测试（2026-09-24）

覆盖：
- remote_hub TCP 服务器存取（load/save/delete，settings tcp_servers 同键同源）
- local_tunnel_state 三态（registered/disabled/none）
- open_direct_session 入口校验 + host/port 传递
- _do_open host 保留（回归：TCP 直连主机不得被硬编码回 127.0.0.1）
- frps_server_addr（frpc_server.serverAddr）
"""
import sys
import types
from types import SimpleNamespace

import pytest

import core.frp_remote as fr
import windows.remote_session.remote_hub as rh
from core.frp_remote import RemoteSessionManager


SETTINGS = {
    "frpc_server": {"serverAddr": "10.0.0.1", "serverPort": 7000,
                    "auth_method": "token", "auth_token": "tok-A"},
    "xtcp_secret_key": "sk-default",
    "tcp_servers": ["1.2.3.4:22"],
}


@pytest.fixture
def mgr(monkeypatch, tmp_path):
    monkeypatch.setattr(fr, "get_app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(fr, "_load_settings", lambda: dict(SETTINGS))
    m = RemoteSessionManager()
    m._logs = []
    m.log_message.connect(m._logs.append)
    return m


@pytest.fixture
def settings(monkeypatch):
    """拦掉真实 app_settings 读写，防污染用户配置"""
    store = {"tcp_servers": list(SETTINGS["tcp_servers"])}
    from core import app_settings
    monkeypatch.setattr(app_settings, "get_merged",
                        lambda: {"tcp_servers": store["tcp_servers"]})
    monkeypatch.setattr(app_settings, "set",
                        lambda key, val: store.__setitem__(key, list(val)))
    return store


# ==================== tcp_servers 存取 ====================

def test_load_tcp_servers_filters_invalid(settings):
    settings["tcp_servers"] = ["a:1", "", "  ", 123, None, "b:2"]
    assert rh.load_tcp_servers() == ["a:1", "b:2"]


def test_load_tcp_servers_non_list(settings):
    settings["tcp_servers"] = "bad"
    assert rh.load_tcp_servers() == []


def test_save_tcp_server_dedup(settings):
    assert rh.save_tcp_server("5.6.7.8:22") is True
    assert rh.save_tcp_server("5.6.7.8:22") is False  # 去重
    assert rh.save_tcp_server("  ") is False  # 空串拒绝
    assert settings["tcp_servers"] == ["1.2.3.4:22", "5.6.7.8:22"]


def test_delete_tcp_server(settings):
    assert rh.delete_tcp_server("1.2.3.4:22") is True
    assert rh.delete_tcp_server("1.2.3.4:22") is False  # 不存在
    assert rh.delete_tcp_server("nope:1") is False
    assert settings["tcp_servers"] == []


# ==================== local_tunnel_state 三态 ====================

def test_local_tunnel_state_three_states(mgr):
    mgr._visitors = {
        "snk_on": {"serverName": "snk_on", "bindPort": 1001},
        "snk_off": {"serverName": "snk_off", "bindPort": 1002,
                    "disabled": True},
    }
    assert rh.local_tunnel_state(mgr, "snk_on") == (
        "registered", mgr._visitors["snk_on"])
    assert rh.local_tunnel_state(mgr, "snk_off") == (
        "disabled", mgr._visitors["snk_off"])
    state, rec = rh.local_tunnel_state(mgr, "ghost")
    assert state == "none" and rec == {}


# ==================== open_direct_session ====================

@pytest.fixture
def notify_log(monkeypatch):
    """拦掉 _notify 底层 InfoBar（无 QApplication 时 C++ 层硬崩溃）"""
    rows = []
    monkeypatch.setattr(fr, "show_info_bar",
                        lambda msg, kind, **kw: rows.append((kind, msg)))
    return rows


def test_open_direct_requires_paramiko(mgr, monkeypatch, notify_log):
    monkeypatch.setattr(fr, "PARAMIKO_AVAILABLE", False)
    mgr._do_open = lambda *a, **k: pytest.fail("不应进入打开路径")
    mgr.open_direct_session("ssh", "10.0.0.1", 22)
    assert any("paramiko" in m for _, m in notify_log)


def test_open_direct_rejects_bad_input(mgr, monkeypatch, notify_log):
    monkeypatch.setattr(fr, "PARAMIKO_AVAILABLE", True)
    mgr._do_open = lambda *a, **k: pytest.fail("不应进入打开路径")
    mgr.open_direct_session("ssh", "  ", 22)      # 空 host
    mgr.open_direct_session("ssh", "10.0.0.1", "abc")  # 非法端口
    assert any("不能为空" in m for _, m in notify_log)
    assert any("端口非法" in m for _, m in notify_log)


def test_open_direct_passes_host(mgr, monkeypatch, notify_log):
    monkeypatch.setattr(fr, "PARAMIKO_AVAILABLE", True)
    calls = []
    mgr._do_open = lambda kind, snk, tid, port, notifier=None, host="x": \
        calls.append((kind, snk, port, host))
    mgr.open_direct_session("sftp", "10.0.0.1", 6000, name="mybox")
    assert calls == [("sftp", "mybox", 6000, "10.0.0.1")]


# ==================== _do_open host 保留（回归） ====================

def test_do_open_keeps_direct_host(mgr, monkeypatch):
    """回归：_do_open 不得把传入 host 硬编码回 127.0.0.1（2026-09-24 修复）"""
    captured = {}
    fake_mod = types.ModuleType("windows.remote_session.ssh_terminal")

    class FakePanel:
        def __init__(self, host, port, user, pwd, **kw):
            captured.update(host=host, port=port, server=kw.get("server_name"))

    fake_mod.SSHTerminalPanel = FakePanel
    monkeypatch.setitem(sys.modules, "windows.remote_session.ssh_terminal",
                        fake_mod)
    monkeypatch.setattr(mgr, "ensure_session_window",
                        lambda: SimpleNamespace(add_session=lambda p: None))
    monkeypatch.setattr(fr, "_load_settings", lambda: {"ssh_user": "u",
                                                       "ssh_pass": "p"})
    mgr._do_open("ssh", "mybox", "", 22, host="10.0.0.9")
    assert captured == {"host": "10.0.0.9", "port": 22, "server": "mybox"}


def test_do_open_defaults_localhost(mgr, monkeypatch):
    """隧道路径不传 host 时保持本机回环"""
    captured = {}
    fake_mod = types.ModuleType("windows.remote_session.ssh_terminal")

    class FakePanel:
        def __init__(self, host, port, user, pwd, **kw):
            captured.update(host=host)

    fake_mod.SSHTerminalPanel = FakePanel
    monkeypatch.setitem(sys.modules, "windows.remote_session.ssh_terminal",
                        fake_mod)
    monkeypatch.setattr(mgr, "ensure_session_window",
                        lambda: SimpleNamespace(add_session=lambda p: None))
    monkeypatch.setattr(fr, "_load_settings", lambda: {})
    mgr._do_open("ssh", "snk_01", "T1", 37991)
    assert captured["host"] == "127.0.0.1"


# ==================== frps_server_addr ====================

def test_frps_server_addr(mgr):
    assert mgr.frps_server_addr() == "10.0.0.1"


def test_frps_server_addr_fallback(mgr, monkeypatch):
    monkeypatch.setattr(fr, "_load_settings", lambda: {})
    assert mgr.frps_server_addr() == fr._FRPC_SERVER_DEFAULTS["serverAddr"]
