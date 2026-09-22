# -*- coding: utf-8 -*-
"""XTCP visitor 连接质量探测（二期 P1）——本地 bindPort TCP connect RTT

探测模型：向 visitor 的本地 bindPort 发起 TCP connect 并计时。XTCP 直连
场景下 connect 完成 = NAT 打洞 + 端到端 TCP 握手全部完成，该 RTT 就是用户
SSH 建连体感的忠实指标（frps 对 visitor 侧流量不可见，服务端拿不到）。

节奏与安全：
- 后台守护线程串行轮询（一次一条隧道，绝并发打爆打洞通道）；
- 默认 30s 一轮 / 单次 connect 超时 800ms（settings.frp_quality 可调）；
- frpc 未运行即停探；隧道消失自动清理其样本；
- 连续 N 次超时（默认 3）判「异常」（在线但不可达，区分于 frps 离线）；
- 环形缓冲每隧道 120 点（约 1 小时），只存内存，落库留三期。

线程约定：探测线程只 emit Qt 信号（自动 queued 到主线程），不触碰任何
Qt 控件；stats()/history() 读的是主线程侧接收 signal 前的纯数据快照，
加锁保护 deque。
"""
from __future__ import annotations

import socket
import threading
import time
from collections import deque

from PySide6.QtCore import QObject, Signal

# 评级阈值（ms）与探测参数默认值；frp_quality 配置可覆盖 timeout/interval/fail_bad
_GOOD_MS = 60        # ≤60ms 且丢失<5% → 优
_NICE_MS = 150       # ≤150ms → 良
_FAIR_MS = 300       # ≤300ms → 一般
_RING_POINTS = 120
_DEFAULT_TIMEOUT_MS = 800
_DEFAULT_INTERVAL_SEC = 30
_DEFAULT_FAIL_BAD = 3
_MAX_INTERVAL_SEC = 600


def _load_quality() -> dict:
    try:
        from core import app_settings
        q = app_settings.get("frp_quality") or {}
    except Exception:
        q = {}
    if not isinstance(q, dict):
        q = {}
    def _int(key, default, lo, hi):
        try:
            v = int(q.get(key) or default)
        except (TypeError, ValueError):
            v = default
        return max(lo, min(hi, v))
    return {
        "enabled": bool(q.get("enabled", True)),
        "interval_sec": _int("interval_sec", _DEFAULT_INTERVAL_SEC, 5,
                             _MAX_INTERVAL_SEC),
        "timeout_ms": _int("timeout_ms", _DEFAULT_TIMEOUT_MS, 100, 5000),
        "fail_bad": _int("fail_bad", _DEFAULT_FAIL_BAD, 2, 10),
    }


def tcp_connect_rtt_ms(host: str, port: int, timeout_ms: int) -> float | None:
    """TCP connect 计时（ms）；失败/超时返回 None

    模块级函数：单测 monkeypatch 注入假结果，不碰真实网络。
    """
    deadline = timeout_ms / 1000.0
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.settimeout(deadline)
    start = time.perf_counter()
    try:
        sock.connect((host, int(port)))
        return (time.perf_counter() - start) * 1000.0
    except OSError:
        return None
    finally:
        try:
            sock.close()
        except OSError:
            pass


class VisitorProber(QObject):
    """visitor RTT 探测调度器（进程级单例）

    targets provider 约定：可调用 → {snk: bindPort}，返回当前应探测的隧道
    （默认接 RemoteSessionManager：仅 frpc 运行中且已注册的 visitor）。
    """

    sample = Signal(str, object)   # (snk, rtt_ms 或 None=超时)
    changed = Signal()             # 一轮探测完成（UI 刷新触发）
    verdict_changed = Signal(str)  # snk 判定翻转（ok↔bad）时告警刷新

    def __init__(self, targets_provider=None, parent=None):
        super().__init__(parent)
        self._targets_provider = targets_provider or self._default_targets
        self._lock = threading.Lock()
        # snk -> {"rtts": deque[float|None], "bad_streak": int, "verdict": str}
        self._data: dict = {}
        self._stop = threading.Event()
        self._stop.set()
        self._thread: threading.Thread | None = None
        self._timeout_ms = _DEFAULT_TIMEOUT_MS
        self._fail_bad = _DEFAULT_FAIL_BAD

    # ---------- 生命周期 ----------

    def start(self):
        q = _load_quality()
        if not q["enabled"] or self._thread is not None:
            return
        self._timeout_ms = q["timeout_ms"]
        self._fail_bad = q["fail_bad"]
        self._stop.clear()
        self._interval = q["interval_sec"]
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="visitor-probe")
        self._thread.start()

    def stop(self):
        self._stop.set()
        th, self._thread = self._thread, None
        if th is not None and th.is_alive():
            th.join(timeout=2.0)

    @property
    def running(self) -> bool:
        return self._thread is not None

    def _run(self):
        # 启动后先短等 2s 探首轮（frpc 通常在跑，秒级出样本），之后按 interval
        # 轮询；未运行窗口 run_once 内 targets 为空自动 no-op，不刷 0/0。
        # 旧实现「先等满 interval 才探」致刚开页 RTT 卡恒空 30s（2026-09-22 反馈）。
        if self._stop.wait(2.0):
            return
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                pass  # 调度线程永不因单轮异常而死
            if self._stop.wait(self._interval):
                break

    # ---------- 探测 ----------

    def _default_targets(self) -> dict:
        try:
            from core.frp_remote import get_session_manager
            m = get_session_manager()
            if not m.is_running():
                return {}
            # 跳过 disabled（已断开保留注册）：隧道已摘除、端口未监听，
            # 探测只会制造无意义的超时样本
            return {r["serverName"]: int(r.get("bindPort") or 0)
                    for r in m.records()
                    if r.get("bindPort") and not r.get("disabled")}
        except Exception:
            return {}

    def run_once(self):
        """串行探完一轮（探测线程定时调用；UI「立即探测」可直接调）。

        connect 走注入的 tcp_connect_rtt_ms（单测 monkeypatch）。
        语义 = 完整一轮，与调度线程启停无关（_stop 置位时手动轮次照样
        执行——QualityWork 的「立即探测一轮」不要求先启动后台线程）。
        """
        targets = dict(self._targets_provider() or {})
        # 模块级名延迟解析：单测 monkeypatch visitor_probe.tcp_connect_rtt_ms
        # 注入假结果（不碰真实网络）
        probe = globals()["tcp_connect_rtt_ms"]
        seen_stale = False
        with self._lock:
            stale = [k for k in self._data if k not in targets]
            for k in stale:
                self._data.pop(k, None)  # 隧道消失：样本随之清理
                seen_stale = True
        if not targets:
            if seen_stale:
                self.changed.emit()
            return
        for snk, port in targets.items():
            try:
                rtt = probe("127.0.0.1", port, self._timeout_ms)
            except Exception:
                rtt = None
            self._record(snk, rtt)
            self.sample.emit(snk, rtt)
        self.changed.emit()

    def _record(self, snk: str, rtt):
        flip = None
        with self._lock:
            ent = self._data.setdefault(snk, {
                "rtts": deque(maxlen=_RING_POINTS),
                "bad_streak": 0, "verdict": "unknown"})
            ent["rtts"].append(rtt)
            if rtt is None:
                ent["bad_streak"] += 1
            else:
                ent["bad_streak"] = 0
            bad = ent["bad_streak"] >= self._fail_bad
            new_v = "bad" if bad else ("ok" if rtt is not None
                                       else ent["verdict"])
            if new_v != ent["verdict"]:
                ent["verdict"] = new_v
                flip = snk
        if flip:
            self.verdict_changed.emit(flip)

    # ---------- 查询（主线程 UI 侧） ----------

    def stats(self, snk: str) -> dict:
        """单隧道质量快照：verdict/avg_ms/p95_ms/ok_rate/recent"""
        with self._lock:
            ent = self._data.get(snk)
            rtts = list(ent["rtts"]) if ent else []
            verdict = ent["verdict"] if ent else "unknown"
        good = [r for r in rtts if r is not None]
        n = len(rtts)
        avg = sum(good) / len(good) if good else None
        p95 = None
        if good:
            srt = sorted(good)
            p95 = srt[min(len(srt) - 1, int(round(0.95 * (len(srt) - 1))))]
        grade = "unknown"
        if verdict == "bad":
            grade = "bad"
        elif avg is not None and n:
            loss = (n - len(good)) / n
            if grade != "bad" and avg <= _GOOD_MS and loss < 0.05:
                grade = "good"
            elif avg <= _NICE_MS:
                grade = "nice"
            elif avg <= _FAIR_MS:
                grade = "fair"
            else:
                grade = "poor"
        return {"verdict": verdict, "grade": grade, "avg_ms": avg,
                "p95_ms": p95, "samples": n, "ok": len(good),
                "recent": rtts[-20:]}

    def verdict(self, snk: str) -> str:
        with self._lock:
            ent = self._data.get(snk)
            return ent["verdict"] if ent else "unknown"


_prober: VisitorProber | None = None


def get_prober() -> VisitorProber:
    global _prober
    if _prober is None:
        _prober = VisitorProber()
    return _prober
