# -*- coding: utf-8 -*-
"""压测指标采集：响应耗时分位数 / QPS / RSS / CPU / 内存护栏与回收

资源约束设计（服务器仅 1GB 内存）：
- ResourceSampler：后台线程按间隔采样进程 RSS(MB) 与 CPU%，给出峰值/均值/趋势，
  并在超过 `rss_limit_mb` 时置位 over_limit，供场景循环提前中止（护栏）。
- recycle()：显式删除大引用 + gc.collect()，每个场景结束强制调用，
  避免上一场景的 10 万条 dict 常驻导致下一场景 OOM。
"""

import gc
import os
import statistics
import threading
import time


class Timer:
    """累计记录每次操作耗时（ms），输出分位数与吞吐"""

    def __init__(self, name: str):
        self.name = name
        self.samples = []

    def add(self, ms: float):
        self.samples.append(ms)

    def measure(self, fn, *args, **kwargs):
        """执行 fn 并计时，返回 (耗时ms, fn 返回值)"""
        t0 = time.perf_counter()
        ret = fn(*args, **kwargs)
        cost = (time.perf_counter() - t0) * 1000
        self.add(cost)
        return cost, ret

    def _pct(self, p: float) -> float:
        """线性插值分位数（p ∈ [0,1]）"""
        if not self.samples:
            return 0.0
        s = sorted(self.samples)
        if len(s) == 1:
            return s[0]
        idx = (len(s) - 1) * p
        lo, hi = int(idx), min(int(idx) + 1, len(s) - 1)
        return s[lo] + (s[hi] - s[lo]) * (idx - lo)

    def summary(self) -> dict:
        if not self.samples:
            return {"name": self.name, "count": 0}
        s = self.samples
        total_s = sum(s) / 1000
        return {
            "name": self.name,
            "count": len(s),
            "min_ms": round(min(s), 2),
            "p50_ms": round(self._pct(0.50), 2),
            "p95_ms": round(self._pct(0.95), 2),
            "p99_ms": round(self._pct(0.99), 2),
            "max_ms": round(max(s), 2),
            "avg_ms": round(statistics.fmean(s), 2),
            "qps": round(len(s) / total_s, 2) if total_s > 0 else 0.0,
        }


class ResourceSampler:
    """后台采样进程 RSS(MB) / CPU%；支持超限标记（内存护栏）

    CPU% 用 psutil 的进程级 cpu_percent（相对单核，可 >100%）；
    首次调用 cpu_percent(None) 内部会与上次比较，这里显式 interval=None
    并在采样线程内周期性调用，取相对增量。
    """

    def __init__(self, interval: float = 0.1, rss_limit_mb: int = 500):
        self.interval = interval
        self.rss_limit_mb = rss_limit_mb
        self.rss_samples: list = []
        self.cpu_samples: list = []
        self.over_limit = False
        self._proc = None
        try:
            import psutil
            self._proc = psutil.Process(os.getpid())
            self._proc.cpu_percent(None)  # 初始化基准
        except Exception:
            self._proc = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self):
        while not self._stop.is_set():
            self.sample()
            self._stop.wait(self.interval)

    def sample(self):
        if self._proc is None:
            return
        try:
            rss = self._proc.memory_info().rss / (1024 * 1024)
            cpu = self._proc.cpu_percent(None)
            self.rss_samples.append(round(rss, 1))
            self.cpu_samples.append(round(cpu, 1))
            if rss > self.rss_limit_mb:
                self.over_limit = True
        except Exception:
            pass

    def start(self):
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def summary(self) -> dict:
        if not self.rss_samples:
            return {"rss_peak_mb": 0, "rss_avg_mb": 0,
                    "cpu_avg_pct": 0, "cpu_peak_pct": 0, "over_limit": False}
        return {
            "rss_peak_mb": round(max(self.rss_samples), 1),
            "rss_avg_mb": round(statistics.fmean(self.rss_samples), 1),
            "rss_end_mb": round(self.rss_samples[-1], 1),
            "cpu_avg_pct": round(statistics.fmean(self.cpu_samples), 1),
            "cpu_peak_pct": round(max(self.cpu_samples), 1),
            "samples": len(self.rss_samples),
            "over_limit": self.over_limit,
        }

    def trend(self, buckets: int = 10) -> list:
        """RSS 趋势（等分成 buckets 段取均值），用于观察内存是否单调增长"""
        if not self.rss_samples or buckets < 1:
            return []
        size = max(1, len(self.rss_samples) // buckets)
        return [round(statistics.fmean(self.rss_samples[i:i + size]), 1)
                for i in range(0, len(self.rss_samples), size)][:buckets]


def recycle(*objs):
    """释放大对象并强制回收（每场景结束调用）"""
    for o in objs:
        try:
            del o
        except Exception:
            pass
    gc.collect()


def rss_mb() -> float:
    """当前进程 RSS(MB)；psutil 不可用时返回 0"""
    try:
        import psutil
        return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 1)
    except Exception:
        return 0.0
