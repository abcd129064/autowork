# -*- coding: utf-8 -*-
"""二期 P0/P1 回归：frps 在线感知、RTT 质量探测、优雅停止、连接预检

覆盖模块：
- core/frps_admin.py  —— /api/proxy/xtcp 解析、四态口径、熔断、后台刷新
- core/visitor_probe.py —— TCP RTT 探测、评级、连续超时判 bad、样本清理
- core/frp_remote.py  —— /api/stop 两段式优雅停止 + 管理通道公开 API
- open_session P0 预检 —— 仅确认 offline 拦截，None/unregistered 放行
- remote_hub 纯函数 —— 流量格式化 / sparkline / 四态单元格

隔离方式：全部 monkeypatch 模块级函数（_http_get / tcp_connect_rtt_ms /
_load_config），不发真实外网请求、不起真实 frpc。
"""
import threading
import time

import pytest

import core.frps_admin as fa
import core.visitor_probe as vp
import core.frp_remote as fr
from core.frps_admin import FrpsAdminClient, _parse_proxies
from core.visitor_probe import VisitorProber, tcp_connect_rtt_ms


def _wait_until(cond, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


# ==================== frps_admin：解析与四态 ====================

_CFG = {"base_url": "http://h:7500", "user": "u", "password": "p"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(fa, "_load_config", lambda: dict(_CFG))
    monkeypatch.setattr(fa, "_load_quality",
                        lambda: {"enabled": True, "interval_sec": 45})
    c = FrpsAdminClient()
    yield c
    c.stop()


def test_timer_interval_follows_quality_config(client):
    assert client._timer.interval() == 45000


def test_parse_proxies_065_shape():
    body = ('{"proxies":[{"name":"snk_1","status":"online","curConns":2,'
            '"lastStartTime":"2026-09-21T10:00:00Z","todayTrafficIn":2048,'
            '"todayTrafficOut":1024},{"name":"snk_2","status":"offline"}]}')
    d = _parse_proxies(body)
    assert d["snk_1"]["status"] == "online"
    assert d["snk_1"]["curConns"] == 2
    assert d["snk_1"]["todayTrafficIn"] == 2048
    assert d["snk_2"]["status"] == "offline"
    assert d["snk_2"]["curConns"] == 0


@pytest.mark.parametrize("body", ["not json", '{"proxy":[]}', '[1,2]', '""'])
def test_parse_proxies_bad_shape_returns_none(body):
    assert _parse_proxies(body) is None


def test_online_matrix(client, monkeypatch):
    body = ('{"proxies":[{"name":"snk_on","status":"online"},'
            '{"name":"snk_off","status":"offline"}]}')
    monkeypatch.setattr(fa, "_http_get",
                        lambda *a, **k: (200, body))
    assert client.refresh() == "ok"
    assert client.online("snk_on") == "online"
    assert client.online("snk_off") == "offline"
    assert client.online("snk_none") == "unregistered"
    assert client.online("") is None


def test_lookup_supports_user_prefixed_name(client, monkeypatch):
    # frps 面板开 user 隔离时 proxy 全名为 "{user}.{snk}"
    monkeypatch.setattr(
        fa, "_http_get",
        lambda *a, **k: (200, '{"proxies":[{"name":"autowork.snk_9",'
                              '"status":"online"}]}'))
    client.refresh()
    assert client.online("snk_9") == "online"


def test_stale_cache_degrades_to_none(client, monkeypatch):
    monkeypatch.setattr(fa, "_http_get",
                        lambda *a, **k: (200, '{"proxies":[{"name":"a","status":"online"}]}'))
    client.refresh()
    assert client.online("a") == "online"
    client._fetched_at -= (fa._CACHE_TTL_SEC + 5)   # 模拟缓存过期
    assert client.online("a") is None               # 过期→未知，绝不拿旧数据误判


def test_bad_body_200_counted_as_fail(client, monkeypatch):
    # SPA 回退页/内容类型守卫思路：200 但解析失败按不可达计
    monkeypatch.setattr(fa, "_http_get", lambda *a, **k: (200, "<html>.."))
    assert client.refresh() == "unreachable"
    assert client.online("any") is None


def test_unconfigured_state(client, monkeypatch):
    monkeypatch.setattr(fa, "_load_config", lambda: {})
    assert client.refresh() == "unconfigured"
    assert client.configured() is False


def test_circuit_breaker_opens_and_recovers(client, monkeypatch):
    calls = []

    def dead(url, *a, **k):
        # 只计 proxies 权威请求：失败轮不会走到 serverinfo/all_proxies，
        # 口径与恢复轮一致
        if "/api/proxy/xtcp" in url:
            calls.append("dead")
        return (0, "")
    monkeypatch.setattr(fa, "_http_get", dead)
    for _ in range(fa._CIRCUIT_FAILS):
        client.refresh()
    assert len(calls) == fa._CIRCUIT_FAILS
    # 熔断窗口内：不再发请求
    client.refresh()
    assert len(calls) == fa._CIRCUIT_FAILS
    # 窗口过后恢复（成功轮会追加一次 best-effort serverinfo GET，
    # 按 URL 区分只计 proxies 权威请求）
    client._circuit_until = 0.0
    def ok(url, *a, **k):
        # 成功轮会追加 serverinfo + 全类型清单（/api/proxy/{type} ×8 +
        # /api/clients）等 best-effort GET：按 URL 区分只计 xtcp 权威请求
        if "/api/proxy/xtcp" in url:
            calls.append("ok")
            return (200, '{"proxies":[]}')
        return (200, '{"proxies":[]}' if "/api/proxy/" in url else "{}")
    monkeypatch.setattr(fa, "_http_get", ok)
    assert client.refresh() == "ok"
    assert calls == ["dead"] * fa._CIRCUIT_FAILS + ["ok"]


def test_401_maps_to_unauthorized(client, monkeypatch):
    monkeypatch.setattr(fa, "_http_get", lambda *a, **k: (401, ""))
    assert client.refresh() == "unauthorized"


# ==================== frps_admin：/api/serverinfo 概览 ====================

_SINFO = ('{"version":"0.65.0","bindPort":7000,"curConns":12,'
          '"clientCounts":9,"totalTrafficIn":2097152,"totalTrafficOut":1048576,'
          '"proxyTypeCounts":{"xtcp":767,"tcp":3}}')


def test_parse_serverinfo_065_shape():
    d = fa._parse_serverinfo(_SINFO)
    assert d["version"] == "0.65.0"
    assert d["clientCounts"] == 9
    assert d["totalTrafficIn"] == 2097152
    assert d["proxyTypeCounts"]["xtcp"] == 767


@pytest.mark.parametrize("body", ["not json", '{"proxies":[]}',
                                  '[1,2]', '{"bindPort":7000}'])
def test_parse_serverinfo_bad_shape_returns_none(body):
    assert fa._parse_serverinfo(body) is None


def test_serverinfo_fetched_on_success(client, monkeypatch):
    seen = []

    def fake_get(url, *a, **k):
        if "serverinfo" in url:
            seen.append(url)
            return (200, _SINFO)
        return (200, '{"proxies":[{"name":"a","status":"online"}]}')
    monkeypatch.setattr(fa, "_http_get", fake_get)
    got = []
    client.serverinfo_changed.connect(got.append)
    assert client.refresh() == "ok"
    assert seen and seen[0].endswith("/api/serverinfo")
    assert client.serverinfo()["version"] == "0.65.0"
    assert got and got[-1]["clientCounts"] == 9
    assert client.snapshot()["serverinfo"]["curConns"] == 12


def test_serverinfo_failure_never_degrades_proxies(client, monkeypatch):
    # 概览端点不可用（404/结构异常/网络错）：通道仍 ok、名单照常、概览为 None
    def fake_get(url, *a, **k):
        if "serverinfo" in url:
            return (404, "not found")
        return (200, '{"proxies":[{"name":"a","status":"online"}]}')
    monkeypatch.setattr(fa, "_http_get", fake_get)
    assert client.refresh() == "ok"
    assert client.online("a") == "online"
    assert client.serverinfo() is None
    assert client._fails == 0          # 不计熔断


def test_serverinfo_stale_degrades_to_none(client, monkeypatch):
    monkeypatch.setattr(
        fa, "_http_get",
        lambda url, *a, **k: (200, _SINFO) if "serverinfo" in url
        else (200, '{"proxies":[]}'))
    client.refresh()
    assert client.serverinfo() is not None
    client._fetched_at -= (fa._CACHE_TTL_SEC + 5)
    assert client.serverinfo() is None


def test_restart_timer_clears_serverinfo(client, monkeypatch):
    monkeypatch.setattr(
        fa, "_http_get",
        lambda url, *a, **k: (200, _SINFO) if "serverinfo" in url
        else (200, '{"proxies":[]}'))
    client.refresh()
    assert client.serverinfo() is not None
    monkeypatch.setattr(fa, "_http_get", lambda *a, **k: (0, ""))  # 卡住重取
    client.restart_timer()             # 同步清 _serverinfo 后再后台重取
    assert client.serverinfo() is None
    client.stop()


# ==================== frps_admin：全类型代理清单（网页面板同源） ====================

_ALL_TCP = ('{"proxies":[{"name":"147_cam1","conf":{"remotePort":4241},'
            '"clientID":"cid1","status":"online","curConns":0,'
            '"todayTrafficIn":362,"todayTrafficOut":0}]}')
_CLIENTS = ('{"clients":[{"key":"k","clientID":"cid1","version":"0.65.0",'
            '"online":true}]}')


def test_parse_proxy_list_065_shape():
    d = fa._parse_proxy_list(_ALL_TCP)
    assert len(d) == 1
    p = d[0]
    assert p["name"] == "147_cam1"
    assert p["conf"]["remotePort"] == 4241
    assert p["clientID"] == "cid1"
    assert p["status"] == "online"
    assert p["todayTrafficIn"] == 362


@pytest.mark.parametrize("body", ["not json", '{"proxy":[]}', '[1,2]', '""'])
def test_parse_proxy_list_bad_shape_returns_none(body):
    assert fa._parse_proxy_list(body) is None


def test_parse_clients_maps_version():
    assert fa._parse_clients(_CLIENTS) == {"cid1": "0.65.0"}
    assert fa._parse_clients("nope") is None
    assert fa._parse_clients('{"clients":{}}') is None


def test_all_proxies_fetched_and_version_joined(client, monkeypatch):
    def fake_get(url, *a, **k):
        if "/api/proxy/xtcp" in url:
            return (200, '{"proxies":[{"name":"snk_1","status":"online"}]}')
        if url.endswith("/api/proxy/tcp"):
            return (200, _ALL_TCP)
        if "/api/clients" in url:
            return (200, _CLIENTS)
        return (200, '{"proxies":[]}')
    monkeypatch.setattr(fa, "_http_get", fake_get)
    assert client.refresh() == "ok"
    allp = client.all_proxies()
    assert len(allp["tcp"]) == 1
    assert allp["tcp"][0]["name"] == "147_cam1"
    assert client.client_version("cid1") == "0.65.0"
    assert client.client_version("missing") == ""


def test_all_proxies_failure_never_degrades_perception(client, monkeypatch):
    # 全类型端点全挂（旧版 frps/网络抖动）：通道仍 ok、名单照常、不计熔断；
    # 清单仅剩复用权威名单的 xtcp 页签（零额外 GET），其余类型空
    def fake_get(url, *a, **k):
        if "/api/proxy/xtcp" in url:
            return (200, '{"proxies":[{"name":"a","status":"online"}]}')
        return (0, "")
    monkeypatch.setattr(fa, "_http_get", fake_get)
    assert client.refresh() == "ok"
    assert client.online("a") == "online"
    allp = client.all_proxies()
    assert set(allp) == {"xtcp"}
    assert allp["xtcp"][0]["name"] == "a"
    assert client._fails == 0          # best-effort：不计熔断


def test_all_proxies_stale_degrades_to_empty(client, monkeypatch):
    monkeypatch.setattr(
        fa, "_http_get",
        lambda url, *a, **k: (200, _ALL_TCP) if url.endswith("/api/proxy/tcp")
        else (200, '{"proxies":[]}'))
    client.refresh()
    assert client.all_proxies().get("tcp")
    client._fetched_at -= (fa._CACHE_TTL_SEC + 5)
    assert client.all_proxies() == {}


# ==================== frps_admin：后台线程刷新 ====================

def test_request_refresh_runs_off_main_thread(client, monkeypatch):
    got = {}

    def fake_get(url, user, password, timeout):
        got["thread"] = threading.current_thread().name
        return (200, '{"proxies":[{"name":"x","status":"online"}]}')
    monkeypatch.setattr(fa, "_http_get", fake_get)
    client.request_refresh()
    assert _wait_until(lambda: "thread" in got)
    assert got["thread"] != threading.current_thread().name
    assert _wait_until(lambda: not client._bg_busy)
    assert client._state == "ok"


def test_request_refresh_dedups_inflight(client, monkeypatch):
    calls = []
    release = threading.Event()

    def slow_get(url, *a, **k):
        # 只数权威名单端点：serverinfo/全类型清单/clients 均为 best-effort 追加 GET
        if "/api/proxy/xtcp" not in url:
            return (200, "{}" if "serverinfo" in url else '{"proxies":[]}')
        calls.append(1)
        release.wait(2.0)
        return (200, '{"proxies":[]}')
    monkeypatch.setattr(fa, "_http_get", slow_get)
    client.request_refresh()
    _wait_until(lambda: len(calls) == 1)
    client.request_refresh()   # 前一轮未完成 → 直接跳过
    assert client._bg_busy is True
    release.set()
    _wait_until(lambda: not client._bg_busy)
    assert len(calls) == 1     # 没有并发第二发（追加 GET 不计）


def test_start_and_restart_timer_refetch(client, monkeypatch):
    n = []
    monkeypatch.setattr(
        fa, "_http_get",
        lambda url, *a, **k: (n.append(1), (200, '{"proxies":[]}'))[1]
        if "/api/proxy/xtcp" in url else (200, "{}"))
    client.start()
    assert _wait_until(lambda: len(n) == 1)
    assert _wait_until(lambda: not client._bg_busy)   # 等本轮线程收尾
    client.start()             # 幂等：不重复启动
    time.sleep(0.05)
    assert len(n) == 1
    client.restart_timer()     # 清缓存 + 立即再感知一次
    assert _wait_until(lambda: len(n) == 2)


# ==================== visitor_probe ====================

def test_tcp_probe_real_listener_and_refused():
    import socket
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        rtt = tcp_connect_rtt_ms("127.0.0.1", port, 800)
        assert rtt is not None and rtt >= 0
    finally:
        srv.close()
    assert tcp_connect_rtt_ms("127.0.0.1", port, 800) is None  # 已关→拒连


def test_probe_grades_and_p95(monkeypatch):
    vals = iter([40.0, 50.0, 200.0])
    monkeypatch.setattr(vp, "tcp_connect_rtt_ms",
                        lambda h, p, t: next(vals))
    pr = VisitorProber(targets_provider=lambda: {"snk_a": 40001})
    pr.run_once()
    st = pr.stats("snk_a")
    assert st["avg_ms"] == pytest.approx(40.0)
    assert st["grade"] == "good"           # ≤60ms 且无丢失
    pr.run_once()
    pr.run_once()
    st = pr.stats("snk_a")
    assert st["avg_ms"] == pytest.approx((40 + 50 + 200) / 3)
    assert st["p95_ms"] == 200.0
    assert st["grade"] == "nice"           # 96.7ms ≤150
    assert pr.stats("nope")["grade"] == "unknown"


def test_probe_consecutive_timeouts_flip_bad(monkeypatch):
    calls = []
    monkeypatch.setattr(vp, "tcp_connect_rtt_ms",
                        lambda h, p, t: calls.append(t) or None)
    pr = VisitorProber(targets_provider=lambda: {"snk_b": 40002})
    flips = []
    pr.verdict_changed.connect(flips.append)
    pr.run_once()
    # 冷洞首拍（P1-3）：长超时给打洞留时间，超时不计入连续失败
    assert calls == [vp._COLD_FIRST_TIMEOUT_MS]
    assert pr.verdict("snk_b") == "unknown"
    assert pr.stats("snk_b")["samples"] == 0   # 首拍不进样本
    pr.run_once()
    pr.run_once()
    pr.run_once()
    assert pr.verdict("snk_b") == "bad"    # fail_bad=3（冷首拍除外）
    assert pr.stats("snk_b")["grade"] == "bad"
    assert flips == ["snk_b"]
    pr.run_once()
    assert flips == ["snk_b"]              # 已是 bad 不再重复翻转


def test_probe_cold_first_shot_long_timeout_success(monkeypatch):
    """冷洞首拍成功：用长超时且样本正常入环（打洞完成后的真实 RTT）"""
    calls = []
    monkeypatch.setattr(vp, "tcp_connect_rtt_ms",
                        lambda h, p, t: calls.append(t) or 120.0)
    pr = VisitorProber(targets_provider=lambda: {"snk_cold": 40005})
    pr.run_once()
    assert calls == [vp._COLD_FIRST_TIMEOUT_MS]
    st = pr.stats("snk_cold")
    assert st["samples"] == 1 and st["verdict"] == "ok"
    pr.run_once()
    assert calls[1] == 800                  # 第二轮回归正常 RTT 口径


def test_probe_recovers_after_bad(monkeypatch):
    seq = iter([None, None, None, None, 30.0])  # 首个 None=冷首拍（不计失败）
    monkeypatch.setattr(vp, "tcp_connect_rtt_ms",
                        lambda h, p, t: next(seq))
    pr = VisitorProber(targets_provider=lambda: {"snk_c": 40003})
    for _ in range(5):
        pr.run_once()
    assert pr.verdict("snk_c") == "ok"     # 一次成功即解除 bad


def test_probe_stale_targets_cleaned(monkeypatch):
    monkeypatch.setattr(vp, "tcp_connect_rtt_ms", lambda h, p, t: 10.0)
    targets = {"snk_d": 40004}
    pr = VisitorProber(targets_provider=lambda: dict(targets))
    pr.run_once()
    assert pr.stats("snk_d")["samples"] == 1
    targets.clear()
    pr.run_once()                          # 隧道消失：样本随之清理
    assert pr.stats("snk_d")["samples"] == 0
    assert pr.stats("snk_d")["verdict"] == "unknown"


def test_probe_first_round_fast(monkeypatch):
    """start 后首轮 ≈2s 出样本（旧实现先等满 interval，开页 30s RTT 恒空）"""
    import socket
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    monkeypatch.setattr(vp, "_load_quality", lambda: {
        "enabled": True, "interval_sec": 600,
        "timeout_ms": 800, "fail_bad": 3})
    pr = VisitorProber(targets_provider=lambda: {"snk_f": port})
    try:
        pr.start()
        assert _wait_until(lambda: pr.stats("snk_f")["samples"] >= 1, 6)
    finally:
        pr.stop()
        srv.close()


# ==================== ensure_visitor 自愈（真机 snk_4007 案例） ====================

def test_ensure_visitor_self_heals_dead_port(mgr, monkeypatch):
    # 注册表有 snk、frpc「在运行」，但本地端口没监听（旧版添加并注册不 apply
    # 的遗留）→ 必须补 apply 拉起端口，而不是直接复用死端口返回
    from unittest.mock import MagicMock
    mgr._frpc_process = MagicMock()
    mgr._visitors["snk_x"] = {
        "serverName": "snk_x", "bindPort": 17569, "secretKey": "sk",
        "tableId": "", "source": fr.SOURCE_MANUAL, "lastUsed": ""}
    applies = []
    mgr.apply = lambda: applies.append(1)
    monkeypatch.setattr("p2p.is_port_in_use", lambda p, host="127.0.0.1": False)
    port, cold = mgr.ensure_visitor("snk_x")
    assert applies == [1] and port == 17569 and cold is False
    assert any("未监听" in s for s in mgr._logs)


def test_ensure_visitor_reuses_live_port(mgr, monkeypatch):
    from unittest.mock import MagicMock
    mgr._frpc_process = MagicMock()
    mgr._visitors["snk_y"] = {
        "serverName": "snk_y", "bindPort": 40001, "secretKey": "sk",
        "tableId": "", "source": fr.SOURCE_MANUAL, "lastUsed": ""}
    applies = []
    mgr.apply = lambda: applies.append(1)
    monkeypatch.setattr("p2p.is_port_in_use", lambda p, host="127.0.0.1": True)
    port, cold = mgr.ensure_visitor("snk_y")
    assert applies == [] and port == 40001 and cold is False


# ==================== frp_remote：优雅停止 ====================

@pytest.fixture
def mgr(monkeypatch, tmp_path):
    monkeypatch.setattr(fr, "get_app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(
        fr, "_load_settings",
        lambda: {"frpc_server": {"serverAddr": "10.0.0.1", "serverPort": 7000,
                                 "auth_method": "token", "auth_token": "t"},
                 "xtcp_secret_key": "sk"})
    m = fr.RemoteSessionManager()
    m._logs = []
    m.log_message.connect(m._logs.append)
    m._admin_port = 18888
    return m


def _fake_proc():
    from unittest.mock import MagicMock
    p = MagicMock()
    p.state.return_value = fr.QProcess.ProcessState.NotRunning
    return p


class _FakeModuleTimer:
    """替换 fr.QTimer：singleShot 只登记回调不排期（Shiboken 类不宜直接 setattr）"""

    def __init__(self):
        self.scheduled = []

    def singleShot(self, ms, fn):
        self.scheduled.append((ms, fn))


def test_graceful_stop_prefers_api_then_quit(mgr, monkeypatch):
    timer = _FakeModuleTimer()
    monkeypatch.setattr(fr, "QTimer", timer)
    api = []

    def fake_api(path, method="GET"):
        api.append((path, method))
        return (200, "")
    mgr._request_admin_api = fake_api
    proc = _fake_proc()
    mgr._frpc_process = proc
    states = []
    mgr.frpc_state_changed.connect(states.append)

    mgr._stop_frpc()
    assert api == [("/api/stop", "POST")]
    proc.quit.assert_called_once()
    proc.kill.assert_not_called()
    assert mgr._frpc_process is None
    assert states == [False]

    # 回检兜底：进程已自行退出 → 不强杀
    ms, fn = timer.scheduled[0]
    assert ms == 2500
    fn()
    proc.kill.assert_not_called()


def test_graceful_stop_fallback_kill_when_hung(mgr, monkeypatch):
    from unittest.mock import MagicMock
    timer = _FakeModuleTimer()
    monkeypatch.setattr(fr, "QTimer", timer)
    mgr._request_admin_api = lambda p, method="GET": (200, "")
    proc = MagicMock()          # state() 返回 MagicMock ≠ NotRunning → 视为仍在跑
    mgr._frpc_process = proc
    mgr._stop_frpc()
    proc.quit.assert_called_once()
    proc.kill.assert_not_called()
    timer.scheduled[0][1]()     # 2.5s 回检：未退出 → 强杀兜底
    proc.kill.assert_called_once()
    assert any("强制结束" in s for s in mgr._logs)


def test_stop_kill_direct_when_admin_unreachable(mgr):
    mgr._request_admin_api = lambda p, method="GET": (0, "")
    proc = _fake_proc()
    mgr._frpc_process = proc
    mgr._stop_frpc()
    proc.kill.assert_called_once()   # 无从优雅停止，同日一期直接强杀
    proc.quit.assert_not_called()


def test_public_stop_api(mgr, monkeypatch):
    calls = []
    monkeypatch.setattr(mgr, "_stop_frpc", lambda: calls.append(1)) \
        if False else None
    mgr._stop_frpc = lambda: calls.append(1)
    mgr.stop_frpc()
    assert calls == [1]
    assert mgr.admin_port == 18888
    mgr._request_admin_api = lambda p, method="GET": (200, "healthy")
    assert mgr.ping_admin() == (200, "healthy")


# ==================== open_session P0 预检 ====================

class _FakeFrps:
    def __init__(self, states, configured_flag=True):
        self._states = list(states)
        self._configured = configured_flag
        self.refresh_calls = 0

    def online(self, snk):
        return self._states.pop(0) if self._states else None

    def configured(self):
        return self._configured

    def refresh(self):
        self.refresh_calls += 1
        return "ok"


def _preflight_env(mgr, monkeypatch, fake):
    monkeypatch.setattr(fr, "PARAMIKO_AVAILABLE", True)
    monkeypatch.setattr(fa, "get_frps_client", lambda: fake)
    notices = []
    mgr._notify = lambda title, msg, error=False, notifier=None: \
        notices.append((title, error))
    ensured = []
    mgr.ensure_visitor = lambda snk, table_id="", source=fr.SOURCE_SNK: \
        (ensured.append(snk) or (40001, False))
    timer = _FakeModuleTimer()
    monkeypatch.setattr(fr, "QTimer", timer)
    return notices, ensured, timer


def test_preflight_blocks_confirmed_offline(mgr, monkeypatch):
    notices, ensured, timer = _preflight_env(
        mgr, monkeypatch, _FakeFrps(["offline"]))
    mgr.open_session("ssh", "snk_x", "")
    assert notices == [("设备未在线", True)]
    assert ensured == [] and timer.scheduled == []
    assert any("预检拦截" in s for s in mgr._logs)


def test_preflight_allows_online(mgr, monkeypatch):
    notices, ensured, timer = _preflight_env(
        mgr, monkeypatch, _FakeFrps(["online"]))
    mgr.open_session("ssh", "snk_x", "")
    assert ensured == ["snk_x"]
    assert len(timer.scheduled) == 1


def test_preflight_allows_unregistered(mgr, monkeypatch):
    # visitor 先于设备上线注册是正常时序 → 放行
    _notices, ensured, _sched = _preflight_env(
        mgr, monkeypatch, _FakeFrps(["unregistered"]))
    mgr.open_session("ssh", "snk_x", "")
    assert ensured == ["snk_x"]


def test_preflight_none_not_configured_passes(mgr, monkeypatch):
    # 感知未配置：绝不阻塞（一期行为）且不发多余 refresh
    fake = _FakeFrps([None], configured_flag=False)
    _n, ensured, _s = _preflight_env(mgr, monkeypatch, fake)
    mgr.open_session("ssh", "snk_x", "")
    assert ensured == ["snk_x"]
    assert fake.refresh_calls == 0


def test_preflight_stale_cache_refresh_once_then_block(mgr, monkeypatch):
    # 缓存过期（None）但已配置 → 当场刷一次；刷出 offline 仍拦截
    fake = _FakeFrps([None, "offline"])
    _n, ensured, _s = _preflight_env(mgr, monkeypatch, fake)
    mgr.open_session("ssh", "snk_x", "")
    assert fake.refresh_calls == 1
    assert ensured == []


def test_preflight_exception_fail_open(mgr, monkeypatch):
    class Boom:
        def online(self, snk):
            raise RuntimeError("网络炸了")

        def configured(self):
            raise RuntimeError

    monkeypatch.setattr(fr, "PARAMIKO_AVAILABLE", True)
    monkeypatch.setattr(fa, "get_frps_client", lambda: Boom())
    mgr._notify = lambda *a, **k: None
    ensured = []
    mgr.ensure_visitor = lambda snk, table_id="", source=fr.SOURCE_SNK: \
        (ensured.append(snk) or (40001, False))
    import core.frp_remote as _f
    monkeypatch.setattr(_f.QTimer, "singleShot",
                        staticmethod(lambda ms, fn: None))
    mgr.open_session("ssh", "snk_x", "")
    assert ensured == ["snk_x"]            # 感知异常绝不拖垮连接主流程


# ==================== remote_hub 纯函数 ====================

def test_fmt_traffic():
    import windows.remote_session.remote_hub as rh
    assert rh._fmt_traffic(0) == "0"
    assert rh._fmt_traffic(2048) == "2KB"
    assert rh._fmt_traffic(5 * 1048576) == "5.0MB"


def test_fmt_traffic_bytes_panel_style():
    import windows.remote_session.remote_hub as rh
    assert rh._fmt_traffic_bytes(0) == "0 bytes"
    assert rh._fmt_traffic_bytes(362) == "362 bytes"   # 网页面板口径
    assert rh._fmt_traffic_bytes(3072) == "3KB"
    assert rh._fmt_traffic_bytes(5 * 1048576) == "5.0MB"


def test_proxy_port_text_by_type():
    import windows.remote_session.remote_hub as rh
    assert rh._proxy_port_text({"conf": {"remotePort": 4241}}) == "4241"
    assert rh._proxy_port_text(
        {"conf": {"customDomains": ["a.cn", "b.cn"]}}) == "a.cn,b.cn"
    assert rh._proxy_port_text({"conf": {"subdomain": "cam1"}}) == "cam1"
    assert rh._proxy_port_text({"conf": {}}) == "—"     # stcp/sudp/xtcp 无端口
    assert rh._proxy_port_text({}) == "—"


def test_spark_pattern():
    import windows.remote_session.remote_hub as rh
    assert rh._spark([None, None], 4) == "··"   # 不足 width 不补齐
    s = rh._spark([10, 500], 2)
    assert s[0] == "▁" and s[-1] == "█"    # 最低/最高锚定两端
    # 只保留末 width 个点，且长度恒 ≤ width
    long = rh._spark(list(range(40)), 8)
    assert len(long) == 8
    assert rh._spark([], 8) == ""


def test_frps_cell_mapping(monkeypatch):
    import windows.remote_session.remote_hub as rh

    class F:
        def __init__(self, s):
            self.s = s

        def online(self, snk):
            return self.s
    for state, text in (("online", "● online"), ("offline", "● offline"),
                        ("unregistered", "○ 未注册"), (None, "— 无感知")):
        monkeypatch.setattr(rh, "get_frps_client", lambda s=state: F(s))
        assert rh._frps_cell("snk_1")[0] == text


# ==================== 表结构：frps在线 末位列 ====================

def test_table_columns_frps_online_last():
    from windows.management.common import TABLE_COLUMNS
    assert TABLE_COLUMNS[-1][0] == "frps_online"
    assert TABLE_COLUMNS[-1][1] == "frps在线"
    # 默认隐藏集合不覆盖新列（插列不改隐藏口径）
    assert len(TABLE_COLUMNS) == 11
