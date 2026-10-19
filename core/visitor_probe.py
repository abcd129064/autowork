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
  冷洞首拍例外：无样本隧道的首轮用 3s 长超时且不计失败（打洞 1~3s 属常态）；
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
# 冷洞首拍长超时：从未连过的隧道首个 connect 要承担 1~3s XTCP 打洞，
# 套 800ms 口径必然判超时；给足 3s 顺带把洞打穿，结果不进质量样本
_COLD_FIRST_TIMEOUT_MS = 3000


def _load_quality() -> dict:
    """读取 frp_quality 配置并夹逼到安全区间，坏配置一律回落默认值。

    探测是旁路功能，任何一项配错都不该影响被观测的隧道本身，
    因此这里吞掉异常、逐项夹逼，而不是校验失败就抛给调用方。
    """
    try:
        from core import app_settings
        q = app_settings.get("frp_quality") or {}
    except Exception:
        q = {}
    if not isinstance(q, dict):
        q = {}
    # 夹逼而非只做类型转换：手改配置可能写出 0 或 99999，
    # 前者让探测空转，后者会把每一轮都拖成分钟级
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
    """测一次到目标端口的 TCP connect 耗时（ms），失败或超时返回 None。

    XTCP 直连时 connect 完成等于打洞与端到端握手都已完成，这个耗时就是
    用户 SSH 建连的体感；frps 看不到 visitor 侧流量，服务端拿不到该指标。
    失败不抛异常：探测需要区分「离线」与「在线但不可达」，抛异常会让整轮中断。
    计时用 perf_counter 而非 time.time，避免系统时钟回拨把 RTT 算成负数。
    模块级函数是刻意的：单测 monkeypatch 它即可注入假结果，不碰真实网络。
    """
    deadline = timeout_ms / 1000.0
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # 关掉 Nagle：握手只有一两个往返，等凑包只会虚增 RTT 读数
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
    """visitor RTT 探测调度器，进程级唯一实例，由 get_prober() 持有。

    targets provider 约定：可调用 → {snk: bindPort}，返回当前应探测的隧道
    （默认接 RemoteSessionManager：仅 frpc 运行中且已注册的 visitor）。
    探测放在独立守护线程：一轮里每条隧道都要阻塞等 connect，
    放到 UI 线程会直接把界面冻住。
    """

    sample = Signal(str, object)   # (snk, rtt_ms 或 None=超时)
    changed = Signal()             # 一轮探测完成（UI 刷新触发）
    verdict_changed = Signal(str)  # snk 判定翻转（ok↔bad）时告警刷新

    def __init__(self, targets_provider=None, parent=None):
        super().__init__(parent)
        self._targets_provider = targets_provider or self._default_targets
        # 探测线程写、UI 线程读，deque 本身不保证跨线程安全，必须配锁
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
        # 未启用或已在运行就直接返回：重复 start 不能起出第二个线程
        if not q["enabled"] or self._thread is not None:
            return
        self._timeout_ms = q["timeout_ms"]
        self._fail_bad = q["fail_bad"]
        # 先清停止标志再起线程，否则新线程第一轮 wait 立刻返回就直接退出
        self._stop.clear()
        self._interval = q["interval_sec"]
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="visitor-probe")
        self._thread.start()

    def stop(self):
        self._stop.set()
        # join 必须带超时：探测可能正卡在一次 connect 上，无限等待会拖住退出流程
        th, self._thread = self._thread, None
        if th is not None and th.is_alive():
            th.join(timeout=2.0)

    @property
    def running(self) -> bool:
        return self._thread is not None

    def _run(self):
        # 启动只短等 2s 就探首轮：等满 interval 会让刚打开的页面空转半分钟，
        # frpc 通常已在运行，秒级就能出样本；targets 为空时 run_once 自然 no-op。
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
        """返回 {snk: bindPort}，只含 frpc 运行中且已注册的 visitor。

        任一环取不到（frpc 没跑、frp_remote 导入失败）都返回空字典，
        让 run_once 退化成 no-op；探测失败不该反过来影响会话管理。
        """
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
        # 先拷一份：provider 可能返回共享字典，探测期间被改会在迭代时抛异常
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
        # 本轮没有可探隧道：只有清理过样本时才通知 UI，否则会刷出大量空刷新
        if not targets:
            if seen_stale:
                self.changed.emit()
            return
        for snk, port in targets.items():
            # 冷洞首拍判定（P1-3）：该 snk 从未探测过 → 用长超时 connect，
            # 给首次 XTCP 打洞留足时间（正常轮次仍用配置的 RTT 口径）
            with self._lock:
                ent = self._data.get(snk)
                cold = ent is None or not ent.get("warmed")
            timeout_ms = (max(self._timeout_ms, _COLD_FIRST_TIMEOUT_MS)
                          if cold else self._timeout_ms)
            try:
                # 固定连本地回环：visitor 的 bindPort 只监听 127.0.0.1，
                # 连远端地址测到的是链路质量而不是打洞质量
                rtt = probe("127.0.0.1", port, timeout_ms)
            except Exception:
                rtt = None
            self._record(snk, rtt, cold=cold)
            self.sample.emit(snk, rtt)
        self.changed.emit()

    def _record(self, snk: str, rtt, cold: bool = False):
        """把一次探测结果并入样本，并在判定翻转时通知界面。

        判定是有状态的：连续 fail_bad 次超时才置 bad，任意一次成功立刻回 ok；
        中间的超时样本保留为 None 用来算丢失率，因此不能直接丢弃。
        """
        flip = None
        with self._lock:
            ent = self._data.setdefault(snk, {
                "rtts": deque(maxlen=_RING_POINTS),
                "bad_streak": 0, "verdict": "unknown"})
            if cold:
                # 无论成败，本轮起视为「已暖」：冷首拍只此一次，后续轮次
                # 回归正常 800ms 口径（超时照常计入连续失败）
                ent["warmed"] = True
                if rtt is None:
                    # 冷洞首拍超时（P1-3）：打洞未完成≠隧道异常，不进样本、
                    # 不计连续失败——下一轮洞已打穿（或预热完成），按正常口径探
                    return
            ent["rtts"].append(rtt)
            if rtt is None:
                ent["bad_streak"] += 1
            else:
                ent["bad_streak"] = 0
            bad = ent["bad_streak"] >= self._fail_bad
            # 未达阈值时的超时不改写判定：一次抖动不该把「良」直接打成「异常」
            new_v = "bad" if bad else ("ok" if rtt is not None
                                       else ent["verdict"])
            if new_v != ent["verdict"]:
                ent["verdict"] = new_v
                flip = snk
        if flip:
            self.verdict_changed.emit(flip)

    # ---------- 查询（主线程 UI 侧） ----------

    def stats(self, snk: str) -> dict:
        """返回单隧道质量快照，grade 由平均 RTT 与丢失率共同决定。

        p95 取排序后的近似秩位，样本少于 20 条时只是参考值；
        verdict 为 bad 时直接压过 RTT 分级，不再用平均值粉饰成「良」。
        """
        with self._lock:
            ent = self._data.get(snk)
            rtts = list(ent["rtts"]) if ent else []
            verdict = ent["verdict"] if ent else "unknown"
        good = [r for r in rtts if r is not None]
        n = len(rtts)
        avg = sum(good) / len(good) if good else None
        p95 = None
        if good:
            # 近似秩位而不是插值：样本量小时插值会显得比实测更平滑
            srt = sorted(good)
            p95 = srt[min(len(srt) - 1, int(round(0.95 * (len(srt) - 1))))]
        grade = "unknown"
        # 判过异常就直接压到最差档：连不上不该因为历史均值漂亮而显示为良
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
        """读当前判定，从未探测过的隧道返回 unknown。

        调用方据此显示「未测」，与「已测且正常」区分开，
        否则新建隧道会被误报成绿色。
        """
        with self._lock:
            ent = self._data.get(snk)
            return ent["verdict"] if ent else "unknown"


_prober: VisitorProber | None = None


def get_prober() -> VisitorProber:
    """取进程级探测单例，首次调用时创建。

    单例是刻意的：探测线程会向所有窗口发信号，多实例会重复打洞并互相覆盖样本，
    还会让每个页面各自维护一条停不掉的线程。
    """
    global _prober
    if _prober is None:
        _prober = VisitorProber()
    return _prober
