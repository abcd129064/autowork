# -*- coding: utf-8 -*-
"""售后统计弹窗数据层（query_stats_detail / cycle_date_range）回归测试

覆盖「售后总数」卡点击后统计弹窗的数据接口：
- query_stats_detail：summary/daily/regions/types 聚合正确性
  （与 query_with_stats 同口径：keyword + issue_type + cycle_start 动态归属，
   不带 resolved/is_initiative/is_our_problem 筛选）
- daily：按发生日期聚合（occurred_at 优先，缺失回退 created_at），升序；
  trend_start/trend_end 仅过滤 daily
- regions/types：降序，未填地区/未分类兜底标签
- cycle_date_range：tue 模式 7 天跨度 / month 模式整月

隔离方式同 test_aftersale_batch_ops：tmp SQLite + monkeypatch，
不触碰真实 tables.db；周期模式固定为周二起保证归属确定性。
"""
import sqlite3
from datetime import date

import pytest

import database.backend as backend
import database.table_db as table_db
import database.aftersale_db as adb
from database import schema

TUE = {"type": "tue", "start": "", "span": 7}
MONTH = {"type": "month", "start": "", "span": 7}


@pytest.fixture
def db(monkeypatch, tmp_path):
    """临时库：建 aftersale_records 表 + 固定周期模式，隔离 settings.json"""
    path = str(tmp_path / "t.db")
    monkeypatch.setattr(table_db, "DB_PATH", path)
    monkeypatch.setattr(table_db, "_conn", None)
    monkeypatch.setattr(table_db, "_ensure_initialized", lambda c: None)
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)
    monkeypatch.setattr(adb, "load_cycle_mode", lambda: dict(TUE))
    sl = sqlite3.connect(path)
    sl.executescript(schema.to_sqlite_ddl("aftersale_records"))
    sl.commit()
    sl.close()
    return path


def _insert(db, rows):
    """批量插入售后记录。rows: [{occurred_at, created_at, region, issue_type,
    resolved, is_initiative, is_our_problem, ...}]"""
    sl = sqlite3.connect(db)
    for r in rows:
        sl.execute(
            "INSERT INTO aftersale_records "
            "(created_at, occurred_at, creator, issue_type, table_no, "
            "room_name, region, problem, cause, resolved, is_initiative, "
            "is_our_problem, solution, resolver, response_time, snk_code, "
            "device_code, cycle_start) "
            "VALUES (?, ?, 'tester', ?, '', '', ?, '问题X', '', ?, '', '', "
            "'', '', '', '', '', '')",
            (r.get("created_at") or (r.get("occurred_at") or "2026-08-20") + " 10:00:00",
             r.get("occurred_at") or r.get("created_at")[:10],
             r.get("issue_type", "硬件问题"),
             r.get("region") or "",
             r.get("resolved") or "否"))
    sl.commit()
    sl.close()


# ==================== query_stats_detail 基本聚合 ====================

def test_stats_detail_basic_aggregation(db):
    """summary 与 query_with_stats 同口径；daily/regions/types 聚合正确"""
    _insert(db, [
        {"occurred_at": "2026-08-18", "region": "广东", "issue_type": "硬件问题", "resolved": "是"},
        {"occurred_at": "2026-08-18", "region": "广东", "issue_type": "硬件问题", "resolved": "否"},
        {"occurred_at": "2026-08-19", "region": "上海", "issue_type": "程序相关", "resolved": "是"},
        {"occurred_at": "2026-08-19", "region": "广东", "issue_type": "硬件问题", "resolved": "是"},
    ])
    r = adb.query_stats_detail()
    s = r["summary"]
    assert s["total"] == 4 and s["resolved"] == 3 and s["unresolved"] == 1
    assert s["rate"] == 75
    # 与 query_with_stats 完全一致
    _t, _rows, stats = adb.query_with_stats(1, 50)
    assert {k: s[k] for k in ("total", "resolved", "unresolved", "rate")} == \
        {k: stats[k] for k in ("total", "resolved", "unresolved", "rate")}

    assert [d["date"] for d in r["daily"]] == ["2026-08-18", "2026-08-19"]
    assert r["daily"][0]["count"] == 2 and r["daily"][0]["resolved"] == 1
    assert r["daily"][1]["count"] == 2 and r["daily"][1]["resolved"] == 2

    assert [x["region"] for x in r["regions"]] == ["广东", "上海"]
    assert r["regions"][0]["count"] == 3 and r["regions"][1]["count"] == 1

    assert [x["issue_type"] for x in r["types"]] == ["硬件问题", "程序相关"]
    t0 = r["types"][0]
    assert (t0["count"], t0["resolved"], t0["unresolved"]) == (3, 2, 1)


def test_stats_detail_daily_falls_back_to_created_at(db):
    """occurred_at 缺失时回退 created_at 前 10 位"""
    _insert(db, [
        {"created_at": "2026-08-20 09:30:00", "region": "四川"},
        {"created_at": "2026-08-21 09:30:00", "region": "四川"},
    ])
    r = adb.query_stats_detail()
    assert [d["date"] for d in r["daily"]] == ["2026-08-20", "2026-08-21"]


def test_stats_detail_fallback_labels(db):
    """未填地区 → 「未填地区」，未填类型 → 「未分类」"""
    _insert(db, [{"occurred_at": "2026-08-20", "region": "",
                  "issue_type": ""}])
    r = adb.query_stats_detail()
    assert r["regions"][0]["region"] == "未填地区"
    assert r["types"][0]["issue_type"] == "未分类"


def test_stats_detail_empty_table(db):
    r = adb.query_stats_detail()
    assert r["summary"] == {"total": 0, "resolved": 0, "unresolved": 0,
                            "rate": 0, "initiative": 0, "our_problem": 0}
    assert r["daily"] == [] and r["regions"] == [] and r["types"] == []


# ==================== 筛选口径 ====================

def test_stats_detail_issue_type_filter(db):
    _insert(db, [
        {"occurred_at": "2026-08-18", "region": "广东", "issue_type": "硬件问题"},
        {"occurred_at": "2026-08-18", "region": "上海", "issue_type": "程序相关"},
    ])
    r = adb.query_stats_detail(issue_type="程序相关")
    assert r["summary"]["total"] == 1
    assert [x["region"] for x in r["regions"]] == ["上海"]


def test_stats_detail_cycle_filter(db):
    """tue 模式（2026-08-18 周二起）：08-18~08-24 属周期 2026/08/18，
    08-25 起属下一周期 2026/08/25"""
    _insert(db, [
        {"occurred_at": "2026-08-20", "region": "广东"},
        {"occurred_at": "2026-08-25", "region": "上海"},
    ])
    r = adb.query_stats_detail(cycle_start="2026/08/18")
    assert r["summary"]["total"] == 1
    assert [x["region"] for x in r["regions"]] == ["广东"]


def test_stats_detail_resolved_filter_keeps_panorama(db):
    """与 query_with_stats 一致：resolved 筛选不参与统计口径"""
    _insert(db, [
        {"occurred_at": "2026-08-18", "region": "广东", "resolved": "是"},
        {"occurred_at": "2026-08-18", "region": "广东", "resolved": "否"},
    ])
    r = adb.query_stats_detail()  # resolved 参数不生效（接口本就不接收）
    assert r["summary"]["total"] == 2


# ==================== 趋势范围 ====================

def test_stats_detail_trend_range_filters_daily_only(db):
    _insert(db, [
        {"occurred_at": "2026-08-18", "region": "广东"},
        {"occurred_at": "2026-08-19", "region": "广东"},
        {"occurred_at": "2026-08-20", "region": "上海"},
    ])
    r = adb.query_stats_detail(trend_start="2026-08-19",
                               trend_end="2026-08-20")
    assert [d["date"] for d in r["daily"]] == ["2026-08-19", "2026-08-20"]
    # 趋势范围不影响 summary / regions / types（仅每日序列）
    assert r["summary"]["total"] == 3
    assert len(r["regions"]) == 2


def test_stats_detail_trend_range_empty(db):
    _insert(db, [{"occurred_at": "2026-08-18", "region": "广东"}])
    r = adb.query_stats_detail(trend_start="2026-09-01")
    assert r["daily"] == []
    assert r["summary"]["total"] == 1  # 其他统计不受影响


# ==================== cycle_date_range ====================

def test_cycle_date_range_tue_span(db):
    """tue 模式：周期 2026/08/18（周二）起 7 天 → 08-18 ~ 08-24"""
    s, e = adb.cycle_date_range("2026/08/18")
    assert s == date(2026, 8, 18) and e == date(2026, 8, 24)


def test_cycle_date_range_month_whole_month(monkeypatch, db):
    """month 模式：整月（8 月 31 天），不依赖当前时刻"""
    monkeypatch.setattr(adb, "load_cycle_mode", lambda: dict(MONTH))
    s, e = adb.cycle_date_range("2026/08/01")
    assert s == date(2026, 8, 1) and e == date(2026, 8, 31)


def test_cycle_date_range_month_historical(monkeypatch, db):
    """month 模式历史周期（2 月）按自身月份计算，不受当前时刻影响"""
    monkeypatch.setattr(adb, "load_cycle_mode", lambda: dict(MONTH))
    s, e = adb.cycle_date_range("2026/02/01")
    assert s == date(2026, 2, 1) and e == date(2026, 2, 28)


def test_cycle_date_range_invalid(db):
    s, e = adb.cycle_date_range("")
    assert s is None and e is None
    s, e = adb.cycle_date_range("bad-date")
    assert s is None and e is None
