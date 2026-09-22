# -*- coding: utf-8 -*-
"""frpc visitor 热重载（apply 路径选择）回归测试

验证 core/frp_remote.py 的动态 [[visitors]] 能力：
- frpc 运行中且 server/auth 公共配置未变 → apply 走 admin API /api/reload
  热重载，不触碰 QProcess（现有隧道零中断）
- reload 不可用（admin 连不上）/公共配置变化 → 回退「停旧起新」重启
- reload 返回 4xx（配置非法）→ 保持原进程、抛 RuntimeError
- 生成的 TOML 含 webServer 管理段（127.0.0.1 + BasicAuth），
  auth token 变更签名口径覆盖 serverAddr/serverPort/auth
隔离方式：monkeypatch get_app_dir → tmp_path、_load_settings → 固定字典、
_request_admin_api → 受控返回、_restart_frpc → 记录调用（不起真实进程）。
"""
import base64
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock

import pytest

import core.frp_remote as fr


SETTINGS = {
    "frpc_server": {
        "serverAddr": "10.0.0.1",
        "serverPort": 7000,
        "auth_method": "token",
        "auth_token": "tok-A",
    },
    "xtcp_secret_key": "sk-default",
}


@pytest.fixture
def mgr(monkeypatch, tmp_path):
    monkeypatch.setattr(fr, "get_app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(fr, "_load_settings", lambda: dict(SETTINGS))
    # 造一个假的 frpc.exe 满足存在性检查
    (tmp_path / "frpc.exe").write_text("", encoding="utf-8")
    m = fr.RemoteSessionManager()
    m._logs = []
    m.log_message.connect(m._logs.append)
    m._admin_port = 18888
    m._admin_password = "test-pass"
    # 默认重启路径打桩：记录调用、伪造「进程已启动」状态（MagicMock 让
    # 真实 _stop_frpc 的 disconnect/kill 调用安全通过）
    m.restarts = []

    def fake_restart(signature):
        m.restarts.append(signature)
        m._frpc_process = MagicMock()
        m._applied_signature = signature
    m._restart_frpc = fake_restart
    # 默认 admin API 不可达（回退重启），单个测试自行覆盖
    m.reload_calls = []

    def fake_request(path, method="GET"):
        m.reload_calls.append(path)
        return m._reload_result
    m._request_admin_api = fake_request
    m._reload_result = (0, "")
    return m


def _add_visitor(m, name="snk_1", port=40001):
    m._visitors[name] = {
        "serverName": name, "bindPort": port, "secretKey": "sk",
        "tableId": "", "source": fr.SOURCE_SNK, "lastUsed": "",
    }


def _read_panel_toml(m, tmp_path):
    return (tmp_path / fr._PANEL_TOML_NAME).read_text(encoding="utf-8")


# ==================== 首次启动：走重启路径 + TOML 含 admin 段 ====================

def test_first_apply_starts_and_writes_admin_section(mgr, tmp_path):
    _add_visitor(mgr)
    assert mgr.apply() == "started"
    assert len(mgr.restarts) == 1
    assert mgr.reload_calls == []     # 未运行，不尝试 reload
    assert mgr.is_running()
    toml = _read_panel_toml(mgr, tmp_path)
    assert 'webServer.addr = "127.0.0.1"' in toml
    assert f"webServer.port = {mgr._admin_port}" in toml
    assert 'webServer.user = "autowork"' in toml
    assert f'webServer.password = "{mgr._admin_password}"' in toml
    assert '[[visitors]]' in toml and 'serverName = "snk_1"' in toml


# ==================== 运行中新增 visitor：热重载成功，不重启 ====================

def test_add_visitor_hot_reloads_without_restart(mgr):
    _add_visitor(mgr, "snk_1")
    mgr.apply()
    before = len(mgr.restarts)        # 仅首启那一次

    mgr._reload_result = (200, "")
    _add_visitor(mgr, "snk_2", 40002)
    assert mgr.apply() == "reloaded"
    assert len(mgr.restarts) == before  # 关键：没有停旧起新
    assert mgr.reload_calls == ["/api/reload"]


# ==================== 运行中删除 visitor：同样热重载 ====================

def test_remove_visitor_hot_reloads(mgr):
    _add_visitor(mgr, "snk_1")
    _add_visitor(mgr, "snk_2", 40002)
    mgr.apply()
    mgr._reload_result = (200, "")
    mgr.remove_visitor("snk_2")
    assert mgr.apply() == "reloaded"
    assert len(mgr.restarts) == 1  # 仍是首启那一次


# ==================== 公共配置变化：跳过 reload，直接重启 ====================

def test_auth_token_change_forces_restart(mgr):
    _add_visitor(mgr)
    mgr.apply()
    # 改 token（模拟 settings 变更）
    SETTINGS["frpc_server"] = dict(SETTINGS["frpc_server"], auth_token="tok-B")
    _add_visitor(mgr, "snk_2", 40002)
    assert mgr.apply() == "restarted"
    assert mgr.reload_calls == []      # 根本没尝试 reload
    assert len(mgr.restarts) == 2      # 走了停旧起新


def test_server_addr_change_forces_restart(mgr):
    _add_visitor(mgr)
    mgr.apply()
    SETTINGS["frpc_server"] = dict(SETTINGS["frpc_server"], serverAddr="10.9.9.9")
    assert mgr.apply() == "restarted"
    assert mgr.reload_calls == []


# ==================== reload 不可用：回退重启 ====================

def test_reload_unreachable_falls_back_to_restart(mgr):
    _add_visitor(mgr)
    mgr.apply()
    mgr._reload_result = (0, "")       # admin 连不上
    _add_visitor(mgr, "snk_2", 40002)
    assert mgr.apply() == "restarted"
    assert len(mgr.restarts) == 2


def test_reload_5xx_falls_back_to_restart(mgr):
    _add_visitor(mgr)
    mgr.apply()
    mgr._reload_result = (500, "internal")
    _add_visitor(mgr, "snk_2", 40002)
    assert mgr.apply() == "restarted"
    assert len(mgr.restarts) == 2


# ==================== reload 4xx：配置非法，保进程报错，绝不重启 ====================

def test_reload_4xx_keeps_process_and_raises(mgr):
    _add_visitor(mgr)
    mgr.apply()
    mgr._reload_result = (400, "bad toml")
    _add_visitor(mgr, "snk_2", 40002)
    with pytest.raises(RuntimeError) as e:
        mgr.apply()
    assert "bad toml" in str(e.value)
    assert len(mgr.restarts) == 1      # 未回退重启
    assert mgr.is_running()            # 原进程与原隧道保持


# ==================== 注册表清空：停止 frpc，签名复位 ====================

def test_apply_stops_when_registry_empty(mgr):
    _add_visitor(mgr)
    mgr.apply()
    assert mgr.is_running() and mgr._applied_signature
    mgr._visitors.clear()
    # apply 内部经 _stop_frpc 停掉桩进程（MagicMock 哨兵安全通过 kill 路径）
    assert mgr.apply() == "stopped"
    assert not mgr.is_running()
    assert mgr._applied_signature is None


# ==================== 断开=置 disabled 保留注册（2026-09-22 修复：
# 断开与删除行为等价的真机 bug）====================

def test_disconnect_keeps_registry_and_drops_block_from_toml(mgr, tmp_path):
    _add_visitor(mgr, "snk_1")
    _add_visitor(mgr, "snk_2", 40002)
    mgr.apply()
    mgr._reload_result = (200, "")
    assert mgr.disconnect_visitor("snk_1") == "ok"
    # 注册保留（与 delete 的本质区别）
    assert "snk_1" in mgr._visitors and mgr._visitors["snk_1"]["disabled"]
    assert "snk_1" in {r["serverName"] for r in mgr.records()}
    # frpc 配置摘除：TOML 不含 snk_1 块、仍含 snk_2 块
    toml = _read_panel_toml(mgr, tmp_path)
    assert 'serverName = "snk_1"' not in toml
    assert 'serverName = "snk_2"' in toml
    # disabled 记录进侧车，重启后可恢复
    import json
    side = json.loads((tmp_path / fr._DISABLED_NAME).read_text(encoding="utf-8"))
    assert [v["serverName"] for v in side] == ["snk_1"]
    # frpc 仍在运行（还有启用隧道）
    assert mgr.is_running()


def test_disconnect_idempotent_and_not_running_guard(mgr):
    _add_visitor(mgr, "snk_1")
    assert mgr.disconnect_visitor("snk_1") == "not_running"
    assert not mgr._visitors["snk_1"].get("disabled")  # 未运行绝不动标记
    mgr.apply()
    mgr._reload_result = (200, "")
    assert mgr.disconnect_visitor("snk_1") == "ok"
    assert mgr.disconnect_visitor("snk_1") == "ok"     # 幂等：已断开再点


def test_disconnect_apply_failure_rolls_back_flag(mgr):
    _add_visitor(mgr, "snk_1")
    _add_visitor(mgr, "snk_2", 40002)
    mgr.apply()
    mgr._reload_result = (400, "bad config")   # 热重载拒绝 → apply 抛错
    assert mgr.disconnect_visitor("snk_1") == "error"
    assert not mgr._visitors["snk_1"].get("disabled")  # 回滚，UI 不裂脑


def test_all_disconnect_stops_frpc_but_keeps_registry(mgr):
    _add_visitor(mgr, "snk_1")
    _add_visitor(mgr, "snk_2", 40002)
    mgr.apply()
    mgr._reload_result = (200, "")
    assert mgr.disconnect_visitor("snk_1") == "ok"
    assert mgr.disconnect_visitor("snk_2") == "ok"   # 末条：无启用项
    assert not mgr.is_running()                      # apply 自动停 frpc
    assert len(mgr.records()) == 2                   # 注册全部保留


def test_disabled_survives_reload_and_reconnect_reenables(mgr, tmp_path):
    _add_visitor(mgr, "snk_1")
    mgr.apply()
    mgr._reload_result = (200, "")
    assert mgr.disconnect_visitor("snk_1") == "ok"
    assert not mgr.is_running()
    # 模拟重启：新 manager 从 TOML + 侧车合并恢复（disabled 态不复活隧道）
    m2 = fr.RemoteSessionManager()
    # 与 fixture 同口径打桩（真实实例不能起真实 frpc.exe）
    m2._admin_port = 18888
    m2._admin_password = "test-pass"
    m2.restarts = []

    def fake_restart(signature):
        m2.restarts.append(signature)
        m2._frpc_process = MagicMock()
        m2._applied_signature = signature
    m2._restart_frpc = fake_restart
    m2._reload_result = (200, "")
    m2._request_admin_api = lambda path, method="GET": m2._reload_result
    assert "snk_1" in m2._visitors
    assert m2._visitors["snk_1"]["disabled"]
    assert not m2.is_running()
    # 重连（ensure_visitor）自动重新启用并拉起 frpc
    port, _cold = m2.ensure_visitor("snk_1")
    assert not m2._visitors["snk_1"].get("disabled")
    assert m2.is_running()
    toml = _read_panel_toml(m2, tmp_path)
    assert 'serverName = "snk_1"' in toml
    # 侧车随之清空（无 disabled 记录时删除文件）
    import os
    assert not os.path.exists(os.path.join(str(tmp_path), fr._DISABLED_NAME))


def test_delete_removes_even_disabled_and_cleans_sidecar(mgr, tmp_path):
    _add_visitor(mgr, "snk_1")
    mgr.apply()
    mgr._reload_result = (200, "")
    assert mgr.disconnect_visitor("snk_1") == "ok"
    assert mgr.delete_visitor("snk_1") == "ok"
    assert "snk_1" not in mgr._visitors
    import os
    assert not os.path.exists(os.path.join(str(tmp_path), fr._DISABLED_NAME))


# ==================== ensure_visitor 冷启动标志口径 ====================

def test_ensure_visitor_hot_reload_not_cold(mgr):
    _add_visitor(mgr, "snk_1")
    mgr.apply()                       # 冷启动一次
    mgr._reload_result = (200, "")
    port, cold = mgr.ensure_visitor("snk_2")
    assert cold is False              # 热重载成功：无需 2500ms 冷启动延时
    assert port == mgr._visitors["snk_2"]["bindPort"]


def test_ensure_visitor_unavailable_admin_is_cold(mgr):
    _add_visitor(mgr, "snk_1")
    mgr.apply()
    mgr._reload_result = (0, "")      # 回退重启 → 视为冷启动
    _, cold = mgr.ensure_visitor("snk_2")
    assert cold is True


# ==================== 热重载失败回退重启时 frpc.exe 缺失语义保持 ====================

def test_restart_missing_exe_raises_oserror(mgr, tmp_path):
    (tmp_path / "frpc.exe").unlink()
    # 还原真实重启实现
    del mgr._restart_frpc
    _add_visitor(mgr)
    with pytest.raises(OSError):
        mgr.apply()


# ==================== 真实 _request_admin_api：回环 HTTP + BasicAuth ====================

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静音
        pass

    def do_GET(self):
        auth = self.headers.get("Authorization", "")
        expect = "Basic " + base64.b64encode(
            b"autowork:test-pass").decode()
        if auth != expect:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"unauthorized")
            return
        if self.path.startswith("/api/reload"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok-reload")
        elif self.path.startswith("/api/bad"):
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"bad-config")
        else:
            self.send_response(500)
            self.end_headers()


def test_real_admin_api_basic_auth_and_status(mgr):
    # 用真实 _request_admin_api 打一台本机 HTTP，验证 URL 拼接 + BasicAuth 头
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        mgr._admin_port = port
        real = fr.RemoteSessionManager._request_admin_api  # 绕过 fixture 桩
        status, body = real(mgr, "/api/reload")
        assert status == 200 and "ok-reload" in body

        status, body = real(mgr, "/api/bad")
        assert status == 400 and "bad-config" in body

        # 错误口令 → 401（HTTPError 分支）
        mgr._admin_password = "wrong"
        status, body = real(mgr, "/api/reload")
        assert status == 401

        # 端口未监听 → 不可达 (0, "")
        mgr._admin_password = "test-pass"
        mgr._admin_port = 1  # 保留端口，几乎必然连不上
        status, body = real(mgr, "/api/reload")
        assert status == 0 and body == ""
    finally:
        srv.shutdown()
        srv.server_close()


# ==================== TOML 注册表回读不受 webServer 段影响 ====================

def test_parse_visitors_toml_ignores_admin_section(mgr, tmp_path):
    _add_visitor(mgr, "snk_1")
    _add_visitor(mgr, "snk_2", 40002)
    mgr._persist_registry()
    vs = fr._parse_visitors_toml(str(tmp_path / fr._PANEL_TOML_NAME))
    names = {v["serverName"] for v in vs}
    assert names == {"snk_1", "snk_2"}
    assert all(v["bindPort"] in (40001, 40002) for v in vs)


# ==================== admin 口令不进 meta/注册表持久化字段 ====================

def test_password_not_in_visitor_meta(mgr, tmp_path):
    _add_visitor(mgr)
    mgr._persist_registry()
    toml = _read_panel_toml(mgr, tmp_path)
    # 口令只允许出现在 webServer.password 一行（admin 段），
    # 不出现在任何 # meta / visitor 块
    body_wo_web = "\n".join(
        ln for ln in toml.splitlines() if not ln.startswith("webServer."))
    assert mgr._admin_password not in body_wo_web
