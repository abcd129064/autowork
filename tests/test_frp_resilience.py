# -*- coding: utf-8 -*-
"""frp 远程可靠性增强回归（2026-09-23 P1/P2 批次）

覆盖 core/frp_remote.py 六项优化：
- P1-1 frpc 意外退出退避自愈（5s/30s/120s，健康 ≥60s 清零，用尽放弃）
- P1-2 autostart 瞬态失败有界重试（60s/120s，成功补预热）
- P2-4 隧道端口就绪轮询（就绪即回调 / 超时兜底回调）
- P2-6 预热成功标记 prewarmedAt（仅内存 + visitors_changed 刷新）

隔离方式同 test_frp_hot_reload：monkeypatch get_app_dir/_load_settings，
QTimer 用登记型假替身（不排期、手动触发回调），绝不起真实 frpc。
"""
import time
from unittest.mock import MagicMock

import pytest

import core.frp_remote as fr


SETTINGS = {
    "frpc_server": {"serverAddr": "10.0.0.1", "serverPort": 7000,
                    "auth_method": "token", "auth_token": "t"},
    "xtcp_secret_key": "sk",
}


@pytest.fixture
def mgr(monkeypatch, tmp_path):
    monkeypatch.setattr(fr, "get_app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(fr, "_load_settings", lambda: dict(SETTINGS))
    m = fr.RemoteSessionManager()
    m._logs = []
    m.log_message.connect(m._logs.append)
    m._admin_port = 18888
    return m


def _add_visitor(m, name="snk_1", port=40001):
    m._visitors[name] = {
        "serverName": name, "bindPort": port, "secretKey": "sk",
        "tableId": "", "source": fr.SOURCE_SNK, "lastUsed": ""}


class _FakeTimer:
    """替换 fr.QTimer：singleShot 只登记回调不排期（测试手动触发）"""

    def __init__(self):
        self.scheduled = []

    def singleShot(self, ms, fn):
        self.scheduled.append((ms, fn))


# ===== 停止 frpc：进程 API 契约（防复发） =====

def test_stop_frpc_uses_real_qprocess_api(mgr, monkeypatch):
    """QProcess 没有 quit()（2026-09-24 关窗崩溃：AttributeError）

    既有测试用裸 MagicMock 当进程桩——任何属性调用都静默返回一个新
    MagicMock，所以 `proc.quit()` 这个根本不存在的 API 从未被发现。
    这里改用 spec=QProcess 的受限桩：访问类上不存在的属性会抛
    AttributeError，从而锁死「优雅停止只能调真实存在的 terminate()」。
    """
    from unittest.mock import MagicMock
    timer = _FakeTimer()
    monkeypatch.setattr(fr, "QTimer", timer)
    mgr._request_admin_api = lambda p, method="GET": (200, "")
    proc = MagicMock(spec=fr.QProcess)
    proc.state.return_value = fr.QProcess.ProcessState.NotRunning
    mgr._frpc_process = proc

    mgr._stop_frpc()   # 误用 quit() 或任何不存在的 API → 这里抛 AttributeError

    proc.terminate.assert_called_once()
    proc.kill.assert_not_called()      # admin 可达：不直接强杀
    timer.scheduled[0][1]()            # 2.5s 回检：已自行退出 → 不兜底
    proc.kill.assert_not_called()


def test_stop_frpc_terminate_then_kill_when_hung(mgr, monkeypatch):
    """admin 可达但进程卡住：terminate 后 2.5s 回检才强杀兜底"""
    from unittest.mock import MagicMock
    timer = _FakeTimer()
    monkeypatch.setattr(fr, "QTimer", timer)
    mgr._request_admin_api = lambda p, method="GET": (200, "")
    # state() 返回 MagicMock ≠ NotRunning → 视为仍在运行
    proc = MagicMock(spec=fr.QProcess)
    mgr._frpc_process = proc

    mgr._stop_frpc()
    proc.terminate.assert_called_once()
    proc.kill.assert_not_called()
    timer.scheduled[0][1]()
    proc.kill.assert_called_once()


# ==================== P1-1：意外退出退避自愈 ====================

def test_crash_schedules_first_backoff_and_recovers(mgr, monkeypatch):
    timer = _FakeTimer()
    monkeypatch.setattr(fr, "QTimer", timer)
    _add_visitor(mgr)
    mgr._frpc_process = MagicMock()
    applies = []
    mgr.apply = lambda: (applies.append(1), "started")[1]
    mgr.prewarm_async = lambda: None

    mgr._on_frpc_finished(1, 0)
    assert timer.scheduled[0][0] == fr._RECOVER_DELAYS_MS[0]   # 5s
    assert mgr._recover_fails == 1
    assert mgr._frpc_process is None

    timer.scheduled[0][1]()          # 触发自愈回调
    assert applies == [1]
    assert mgr._recover_fails == 0   # 恢复成功清零
    assert any("自动恢复完成" in s for s in mgr._logs)


def test_healthy_uptime_resets_backoff(mgr, monkeypatch):
    timer = _FakeTimer()
    monkeypatch.setattr(fr, "QTimer", timer)
    # Windows 上 monotonic()=开机秒数（可能很小），用假时钟保证差值语义
    class _FakeTime:
        now = 100000.0
        @staticmethod
        def monotonic():
            return _FakeTime.now
        @staticmethod
        def strftime(fmt):
            return time.strftime(fmt)
    monkeypatch.setattr(fr, "time", _FakeTime)
    _add_visitor(mgr)
    mgr._frpc_process = MagicMock()
    mgr.apply = lambda: "started"
    mgr.prewarm_async = lambda: None
    # 长跑 2 小时后崩：虽已耗到第二档，健康时长足 → 计数清零仍从第一档恢复
    mgr._recover_fails = 1
    mgr._frpc_started_at = _FakeTime.now - 7200
    mgr._on_frpc_finished(1, 0)
    assert timer.scheduled[0][0] == fr._RECOVER_DELAYS_MS[0]
    assert mgr._recover_fails == 1


def test_short_uptime_advances_backoff(mgr, monkeypatch):
    timer = _FakeTimer()
    monkeypatch.setattr(fr, "QTimer", timer)
    _add_visitor(mgr)
    mgr._frpc_process = MagicMock()
    mgr._recover_fails = 1
    mgr._frpc_started_at = time.monotonic()   # 刚拉起就崩（重启循环）
    mgr._on_frpc_finished(1, 0)
    assert timer.scheduled[0][0] == fr._RECOVER_DELAYS_MS[1]   # 30s 第二档
    assert mgr._recover_fails == 2


def test_backoff_exhausted_gives_up(mgr, monkeypatch):
    timer = _FakeTimer()
    monkeypatch.setattr(fr, "QTimer", timer)
    _add_visitor(mgr)
    mgr._frpc_process = MagicMock()
    mgr._recover_fails = len(fr._RECOVER_DELAYS_MS)
    mgr._frpc_started_at = time.monotonic()   # 短 uptime：不清零计数
    mgr._on_frpc_finished(1, 0)
    assert timer.scheduled == []     # 不再排期
    assert any("用尽" in s for s in mgr._logs)


def test_recover_apply_failure_continues_backoff(mgr, monkeypatch):
    timer = _FakeTimer()
    monkeypatch.setattr(fr, "QTimer", timer)
    _add_visitor(mgr)
    mgr._frpc_process = MagicMock()

    def boom():
        raise OSError("network down")
    mgr.apply = boom
    mgr._on_frpc_finished(1, 0)      # 第一次退出 → 排 5s
    timer.scheduled[0][1]()          # 自愈回调 apply 失败 → 续排下一档
    assert timer.scheduled[1][0] == fr._RECOVER_DELAYS_MS[1]   # 30s
    assert mgr._recover_fails == 2


def test_crash_with_no_active_tunnel_no_recover(mgr, monkeypatch):
    timer = _FakeTimer()
    monkeypatch.setattr(fr, "QTimer", timer)
    _add_visitor(mgr)
    mgr._visitors["snk_1"]["disabled"] = True   # 全部断开态
    mgr._frpc_process = MagicMock()
    mgr._on_frpc_finished(0, 0)
    assert timer.scheduled == []


# ==================== P1-2：autostart 有界重试 ====================

def test_autostart_failure_schedules_bounded_retry(mgr, monkeypatch):
    timer = _FakeTimer()
    monkeypatch.setattr(fr, "QTimer", timer)
    _add_visitor(mgr)

    def boom():
        raise OSError("no network")
    mgr.apply = boom
    assert mgr.autostart() == "failed"
    assert mgr._autostart_retries == 1
    assert timer.scheduled[0][0] == fr._AUTOSTART_RETRY_DELAYS_MS[0]  # 60s

    # 重试成功 → 补预热 + 计数清零
    mgr.apply = lambda: "started"
    warmed = []
    mgr.prewarm_async = lambda: warmed.append(1)
    timer.scheduled[0][1]()
    assert warmed == [1]
    assert mgr._autostart_retries == 0


def test_autostart_retry_exhausted_gives_up(mgr, monkeypatch):
    timer = _FakeTimer()
    monkeypatch.setattr(fr, "QTimer", timer)
    _add_visitor(mgr)
    mgr.apply = lambda: (_ for _ in ()).throw(OSError("still down"))
    mgr.autostart()                          # 第 1 次失败 → 排 60s
    timer.scheduled[0][1]()                  # 重试失败 → 排 120s
    assert timer.scheduled[1][0] == fr._AUTOSTART_RETRY_DELAYS_MS[1]
    timer.scheduled[1][1]()                  # 再失败 → 放弃不排期
    assert len(timer.scheduled) == 2
    assert any("用尽" in s for s in mgr._logs)


# ==================== P2-4：端口就绪轮询 ====================

def test_wait_ports_ready_immediate_when_listening(mgr, monkeypatch):
    fired = []
    monkeypatch.setattr("p2p.is_port_in_use", lambda p, host="127.0.0.1": True)
    mgr.wait_ports_ready([40001, 40002], lambda: fired.append(True))
    assert fired == [True]           # 首轮全就绪 → 同步回调，零等待


def test_wait_ports_ready_deadline_fallback(mgr, monkeypatch):
    timer = _FakeTimer()
    monkeypatch.setattr(fr, "QTimer", timer)
    monkeypatch.setattr("p2p.is_port_in_use", lambda p, host="127.0.0.1": False)
    monkeypatch.setattr(fr, "_PORT_READY_DEADLINE_MS", 0)   # 立即到限
    fired, timed_out = [], []
    mgr.wait_ports_ready([40001], lambda: fired.append(True),
                         on_deadline=lambda: timed_out.append(True))
    assert fired == [] and timed_out == [True]   # 超时走兜底回调


# ==================== P2-6：预热标记 prewarmedAt ====================

def test_mark_prewarded_updates_registry_and_notifies(mgr, tmp_path):
    _add_visitor(mgr)
    flips = []
    mgr.visitors_changed.connect(lambda: flips.append(1))
    mgr.prewarmed.emit(["snk_1", "ghost"])   # ghost 不在注册表，忽略
    rec = [r for r in mgr.records() if r["serverName"] == "snk_1"][0]
    assert rec.get("prewarmedAt")            # HH:MM 时间标记
    assert flips == [1]
    # 仅内存态：持久化 TOML 的 meta 不含 prewarmedAt（frpc 重启后洞失效）
    mgr._persist_registry()
    assert "prewarmedAt" not in _read_toml_text(mgr)


def _read_toml_text(mgr):
    from core.frp_remote import get_app_dir, _PANEL_TOML_NAME
    import os
    p = os.path.join(get_app_dir(), _PANEL_TOML_NAME)
    if not os.path.exists(p):
        return ""
    with open(p, encoding="utf-8") as f:
        return f.read()


# ==================== P2-5：失联上报翻转去重 ====================

def test_report_tunnel_issue_dedupes_and_recovers(mgr):
    lost_events, ok_events = [], []
    mgr.tunnel_issue_changed.connect(
        lambda snk, st: (lost_events if st == "lost" else ok_events).append(snk))
    mgr.report_tunnel_issue("snk_1", True)
    mgr.report_tunnel_issue("snk_1", True)   # 重复上报不重复发
    assert lost_events == ["snk_1"] and ok_events == []
    mgr.report_tunnel_issue("snk_1", False)
    assert ok_events == ["snk_1"]
    assert mgr._tunnel_issues == set()
