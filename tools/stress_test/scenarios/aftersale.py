# -*- coding: utf-8 -*-
"""场景一：售后工单（创建 / 查询 / 统计 / 批量处理）

压测跑在**真实业务函数**上（database.aftersale_db 的 insert_record /
query_page / query_with_stats / mark_resolved_batch），仅把 `_conn()`
替换为内存 SQLite 连接，从而在不建物理库的前提下量化规模化性能。

重点暴露的已知风险：`aftersale_db.query_page` 无 SQL LIMIT（全量取回 +
Python 侧周期过滤 + 内存切片），`query_with_stats` 还会再跑一次全表统计，
二者耗时/内存随数据量线性增长——本场景用三档规模把它量化出来。
"""

import gc
import os
import sqlite3
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from database import aftersale_db as adb  # noqa: E402
from tools.stress_test import metrics  # noqa: E402
from tools.stress_test.data_gen import (  # noqa: E402
    build_memory_db, iter_aftersale_records)

SCENARIO = "aftersale"


def _new_record(i: int) -> dict:
    """构造一条待创建工单（字段与 insert_record 期望一致）"""
    for rec in iter_aftersale_records(1, seed=90000 + i):
        rec["occurred_at"] = "2026-08-28 10:00:00"
        rec["created_at"] = "2026-08-28 10:05:00"
        rec["cycle_start"] = "2026-08-28"
        return rec
    return {}


def run(scale: int, conn: sqlite3.Connection | None = None,
        repeat: int = 100, rss_limit_mb: int = 500,
        create_ops: int = 200, batch_sizes=(50, 200, 1000)) -> dict:
    """执行售后工单压测，返回结构化结果"""
    own_conn = conn is None
    if conn is None:
        conn = build_memory_db(scale)

    orig_conn = adb._conn
    adb._conn = lambda: conn          # 注入内存库（真实函数不改）

    sampler = metrics.ResourceSampler(rss_limit_mb=rss_limit_mb)
    timers = {}
    aborted = False
    sampler.start()
    try:
        # ---------- 1) 工单创建 ----------
        t = metrics.Timer("create")
        for i in range(create_ops):
            rec = _new_record(i)
            t.measure(adb.insert_record, rec)
            if sampler.over_limit:
                aborted = True
                break
        timers["create"] = t

        # ---------- 2) 分页查询（四种口径） ----------
        queries = {
            "query_all": dict(page_no=1, page_size=50),
            "query_keyword": dict(page_no=1, page_size=50, keyword="校准"),
            "query_type": dict(page_no=1, page_size=50, issue_type="球桌问题"),
            "query_midpage": dict(page_no=max(1, scale // 100), page_size=50),
        }
        for name, kw in queries.items():
            t = metrics.Timer(name)
            for _ in range(min(repeat, 50)):
                cost, _ = t.measure(adb.query_page, **kw)
                if sampler.over_limit:
                    aborted = True
                    break
            timers[name] = t
            gc.collect()          # 每轮查询后回收（query_page 全量取回会产生大列表）
            if aborted:
                break

        # ---------- 3) 列表 + 统计（同一次请求的两个热点） ----------
        if not aborted:
            t = metrics.Timer("query_with_stats")
            for _ in range(min(repeat, 30)):
                t.measure(adb.query_with_stats, 1, 50)
                if sampler.over_limit:
                    aborted = True
                    break
            timers["query_with_stats"] = t
            gc.collect()

        # ---------- 4) 批量标记已解决 ----------
        if not aborted:
            ids = [r[0] for r in conn.execute(
                "SELECT id FROM aftersale_records WHERE resolved='否' LIMIT 2000")]
            for size in batch_sizes:
                if len(ids) < size or sampler.over_limit:
                    continue
                t = metrics.Timer(f"batch_resolve_{size}")
                for _ in range(10):
                    t.measure(adb.mark_resolved_batch, ids[:size])
                    if sampler.over_limit:
                        aborted = True
                        break
                timers[f"batch_resolve_{size}"] = t
    finally:
        sampler.stop()
        adb._conn = orig_conn
        if own_conn:
            conn.close()
        gc.collect()

    return {
        "scenario": SCENARIO,
        "scale": scale,
        "repeat": repeat,
        "aborted_by_rss_guard": aborted,
        "timers": {k: v.summary() for k, v in timers.items()},
        "resources": sampler.summary(),
        "rss_trend_mb": sampler.trend(),
        "rows": scale,
    }


def analyze(result: dict) -> list:
    """基于结果给出结论（瓶颈判定）"""
    tips = []
    t = result["timers"]
    q = t.get("query_all", {})
    s = t.get("query_with_stats", {})
    if q.get("p95_ms"):
        tips.append(
            f"分页查询 p95={q['p95_ms']}ms（QPS~{q['qps']}）"
            "——query_page 无 SQL LIMIT，全量取回后再切片，"
            "耗时随数据量线性上升；建议改为 SQL LIMIT/OFFSET + COUNT 聚合。")
    if s.get("p95_ms") and q.get("p95_ms"):
        ratio = s["p95_ms"] / max(q["p95_ms"], 0.01)
        if ratio > 1.6:
            tips.append(
                f"列表+统计 p95={s['p95_ms']}ms，是纯列表的 {ratio:.1f} 倍"
                "——统计额外跑一次全表扫描与 Python 聚合，建议合并为 SQL 聚合计数。")
    if result["resources"].get("rss_peak_mb"):
        tips.append(
            f"进程内存峰值 {result['resources']['rss_peak_mb']}MB"
            f"（护栏 {result['resources'].get('over_limit')} 触发="
            f"{result['aborted_by_rss_guard']}）。")
    return tips
