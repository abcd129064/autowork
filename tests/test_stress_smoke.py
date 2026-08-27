# -*- coding: utf-8 -*-
"""压测套件 pytest 接入（小规模冒烟，CI 可跑）

- 以极小规模（2k）跑 stress_test 的售后/运维场景，断言返回结构与基本指标，
  防止压测套件本身在重构数据层后被破坏；
- 双后端 SQL 兼容性：aftersale 查询改造（COUNT + LIMIT ? OFFSET ? /
  SUM(bool) / 周期范围 CASE 表达式）生成的 SQL 片段不包含 SQLite 专属
  语法，确保 MySQL 侧同样可执行（pymysql 参数绑定 ? 通用）。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tools.stress_test.scenarios import aftersale as sa
from tools.stress_test.scenarios import ops_panel as so
from tools.stress_test.data_gen import build_memory_db, build_ops_dataset


def test_stress_aftersale_smoke():
    """2k 规模售后场景：结构完整 + 基本指标存在 + 不触发护栏"""
    res = sa.run(2000, repeat=3, create_ops=10, batch_sizes=(50,))
    assert res["scenario"] == "aftersale"
    assert res["aborted_by_rss_guard"] is False
    assert "query_all" in res["timers"]
    q = res["timers"]["query_all"]
    assert q["count"] >= 1 and q["p50_ms"] is not None
    assert res["resources"]["rss_peak_mb"] > 0
    # 结论列表非空（至少给出分页建议）
    assert len(sa.analyze(res)) >= 1


def test_stress_ops_smoke():
    """2k 规模运维场景：LIMIT 分页远快于全量聚合（正确范式对照）"""
    res = so.run(2000, repeat=3, refresh_rounds=5, chart_points=200)
    t = res["timers"]
    assert "ops_page_limited" in t and "ops_group_by" in t
    page = t["ops_page_limited"]["p50_ms"]
    grp = t["ops_group_by"]["p50_ms"]
    assert page <= grp, "LIMIT 分页应不慢于全量聚合"


def test_cycle_options_fast_and_exact():
    """get_cycle_options：DISTINCT 日期实现返回正确周期且快速"""
    import time
    from database import aftersale_db as adb
    conn = build_memory_db(2000, verbose=False)
    orig = adb._conn
    adb._conn = lambda: conn
    try:
        t0 = time.perf_counter()
        opts = adb.get_cycle_options()
        cost_ms = (time.perf_counter() - t0) * 1000
        assert isinstance(opts, list) and len(opts) > 0
        assert cost_ms < 500, f"周期下拉应 <500ms，实际 {cost_ms:.0f}ms"
        # 与逐行归属口径一致（抽样比对）
        rows = conn.execute(
            "SELECT occurred_at, created_at FROM aftersale_records LIMIT 50"
        ).fetchall()
        for occ, cre in rows:
            c = adb._record_cycle(occ, cre)
            if c:
                assert c in opts
    finally:
        adb._conn = orig
        conn.close()


def test_paging_sql_mysql_compatible():
    """P0 改造后的查询 SQL 片段不依赖 SQLite 专属语法（MySQL 可执行）"""
    from database import aftersale_db as adb
    where, params = adb._build_where("kw", "球桌问题", "否")
    where, params = adb._append_cycle_where(where, params, "2026/08/01")
    sql = ("SELECT COUNT(*) FROM aftersale_records" + where +
           " LIMIT ? OFFSET ?")
    # 参数占位符统一为 ?（pymysql 与 sqlite3 通用）
    assert sql.count("?") == len(params) + 2
    # 不应出现 SQLite 专属语法
    for banned in ("PRAGMA", "IF NOT EXISTS", "AUTOINCREMENT", "`"):
        assert banned not in sql, f"SQL 含 SQLite/MySQL 专属语法: {banned}"
    # 周期范围表达式用标准 substr/CASE/LIKE（两方言均有）
    assert "substr(" in sql and "CASE WHEN" in sql
