# -*- coding: utf-8 -*-
"""售后列表排序下沉 SQL 的数据层回归测试（P2-1）

覆盖（不依赖 Qt，直接跑 query_page / query_with_stats）：
- _build_order 白名单：升/降序、location 三列、未知键回退 id DESC
- 跨页排序：第 1/2 页顺序与全量一致，不是只排当前页
- 并列值：末尾 id DESC 兜底，翻页不重复、不漂移
- 是/否列：CASE 数值排序，避免 SQLite/MySQL 中文排序规则差异
- query_with_stats：排序参数只影响列表，统计口径与数值不变

隔离同 tests/test_aftersale_filters.py：临时 SQLite + monkeypatch，不碰
真实 tables.db；目录用默认权限自建，绕开本机 tmp_path 的 ACL 拒绝。
"""
import os
import shutil
import sqlite3
import tempfile
import uuid

import pytest

import database.backend as backend
import database.table_db as table_db
import database.aftersale_db as adb
from database import schema

_FIELDS = {
    "created_at": "2026-08-20 10:00:00", "occurred_at": "2026-08-20",
    "creator": "tester", "issue_type": "硬件问题", "table_no": "01",
    "room_name": "甲球房", "region": "广东", "problem": "问题",
    "cause": "", "resolved": "否", "is_initiative": "否",
    "is_our_problem": "是", "solution": "", "resolver": "",
    "response_time": "", "snk_code": "", "device_code": "",
    "cycle_start": "",
}


@pytest.fixture
def db(monkeypatch):
    """临时库：只建 aftersale_records，固定 SQLite 后端"""
    d = os.path.join(tempfile.gettempdir(),
                     "autowork_sort_%s" % uuid.uuid4().hex[:8])
    os.makedirs(d)
    path = os.path.join(d, "t.db")
    monkeypatch.setattr(table_db, "DB_PATH", path)
    monkeypatch.setattr(table_db, "_conn", None)
    monkeypatch.setattr(table_db, "_ensure_initialized", lambda c: None)
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)
    sl = sqlite3.connect(path)
    sl.executescript(schema.to_sqlite_ddl("aftersale_records"))
    sl.commit()
    sl.close()
    try:
        yield path
    finally:
        # 连接不关，Windows 上 rmtree 删不掉 t.db，临时目录会留在根目录
        table_db.close()
        shutil.rmtree(d, ignore_errors=True)


def _insert(db, **overrides):
    """直插一条记录，返回自增 id；字段默认值见 _FIELDS"""
    rec = dict(_FIELDS)
    rec.update(overrides)
    cols = ", ".join(rec)
    marks = ", ".join("?" for _ in rec)
    sl = sqlite3.connect(db)
    try:
        cur = sl.execute(
            "INSERT INTO aftersale_records (%s) VALUES (%s)" % (cols, marks),
            list(rec.values()))
        sl.commit()
        return cur.lastrowid
    finally:
        sl.close()


# ==================== _build_order 白名单 ====================

def test_build_order_whitelist():
    assert adb._build_order("problem", "asc") == "problem ASC, id DESC"
    assert adb._build_order("problem", "DESC") == "problem DESC, id DESC"
    assert adb._build_order("location", "asc") == (
        "room_name ASC, region ASC, table_no ASC, id DESC")
    # 操作列/空键/未知键一律回退 id DESC，避免拼接未白名单列
    assert adb._build_order("ops", "asc") == "id DESC"
    assert adb._build_order("", "") == "id DESC"
    assert adb._build_order("no_such_col", "asc") == "id DESC"
    assert "ops" not in adb.LIST_SORT_KEYS
    assert "problem" in adb.LIST_SORT_KEYS


# ==================== 跨页排序 ====================

def test_query_page_sort_asc_desc_across_pages(db):
    for i in range(65):
        _insert(db, problem="p%02d" % i)
    total, rows = adb.query_page(1, 10, sort_key="problem", sort_order="asc")
    assert total == 65
    assert [r["problem"] for r in rows] == ["p%02d" % i for i in range(10)]
    _total, rows = adb.query_page(7, 10, sort_key="problem", sort_order="asc")
    assert [r["problem"] for r in rows] == ["p%02d" % i for i in range(60, 65)]
    _total, rows = adb.query_page(1, 10, sort_key="problem", sort_order="desc")
    assert [r["problem"] for r in rows] == ["p%02d" % i
                                            for i in range(64, 54, -1)]


def test_query_page_sort_location_three_columns(db):
    # 乱序插入，排序结果应按 球房 → 地区 → 桌号 逐级排列。
    # 用 ASCII 值避开中文在不同后端排序规则下的字节序差异，
    # 本测试只锁「三列优先级」这一层语义。
    _insert(db, room_name="R2", region="Z1", table_no="01")
    _insert(db, room_name="R1", region="Z2", table_no="09")
    _insert(db, room_name="R1", region="Z1", table_no="02")
    _insert(db, room_name="R1", region="Z1", table_no="01")
    _total, rows = adb.query_page(1, 10, sort_key="location",
                                  sort_order="asc")
    assert [(r["room_name"], r["region"], r["table_no"])
            for r in rows] == [
        ("R1", "Z1", "01"), ("R1", "Z1", "02"),
        ("R1", "Z2", "09"), ("R2", "Z1", "01")]


def test_query_page_unknown_sort_falls_back_id_desc(db):
    _insert(db, problem="a")
    _insert(db, problem="b")
    _insert(db, problem="c")
    ids = [r["id"] for r in adb.query_page(
        1, 10, sort_key="ops", sort_order="asc")[1]]
    assert ids == [3, 2, 1]


def test_query_page_tie_breaks_by_id_desc(db):
    for _ in range(10):
        _insert(db, problem="same")
    _total, page1 = adb.query_page(1, 4, sort_key="problem",
                                   sort_order="asc")
    _total, page2 = adb.query_page(2, 4, sort_key="problem",
                                   sort_order="asc")
    ids1 = [r["id"] for r in page1]
    ids2 = [r["id"] for r in page2]
    assert ids1 == [10, 9, 8, 7]
    assert ids2 == [6, 5, 4, 3]
    assert set(ids1).isdisjoint(ids2)


def test_query_page_yes_no_rank(db):
    # 是=1 否=0；同为否时按 id DESC，故 否 的 id 顺序是 4、2
    _insert(db, resolved="是")
    _insert(db, resolved="否")
    _insert(db, resolved="是")
    _insert(db, resolved="否")
    _total, rows = adb.query_page(1, 10, sort_key="resolved",
                                  sort_order="asc")
    assert [(r["id"], r["resolved"]) for r in rows] == [
        (4, "否"), (2, "否"), (3, "是"), (1, "是")]
    assert "CASE resolved WHEN '是' THEN 1 WHEN '否' THEN 0" in (
        adb._build_order("resolved", "asc"))


# ==================== query_with_stats 透传 ====================

def test_query_with_stats_passes_sort_without_changing_stats(db):
    _insert(db, problem="c", resolved="是")
    _insert(db, problem="a", resolved="否")
    _insert(db, problem="b", resolved="是")
    total, rows, stats = adb.query_with_stats(
        1, 2, sort_key="problem", sort_order="asc")
    assert total == 3
    assert [r["problem"] for r in rows] == ["a", "b"]
    assert stats == {"total": 3, "resolved": 2, "unresolved": 1,
                     "initiative": 0, "our_problem": 3, "rate": 67}
    # 排序方向变化不影响统计数值
    _t, _rows, stats_desc = adb.query_with_stats(
        1, 2, sort_key="problem", sort_order="desc")
    assert stats_desc == stats
