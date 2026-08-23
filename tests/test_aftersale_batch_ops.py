# -*- coding: utf-8 -*-
"""售后记录批量操作与扩展统计回归测试

覆盖售后面板「一键标记已解决 / 批量操作 / 概览指标卡」落地后新增的数据层：
- mark_resolved_batch：最小化更新，仅改 resolved 与 updated_at
- delete_records：按 id 批量删除
- query_with_stats：stats 增加 initiative（主动发起数）、our_problem（我方问题数）与 rate（已解决率）

隔离方式同 test_health_alerts_sync：tmp SQLite + monkeypatch，
不触碰真实 tables.db；周期模式固定为周二起保证归属确定性。
"""
import sqlite3

import pytest

import database.backend as backend
import database.table_db as table_db
import database.aftersale_db as adb
from database import schema

TUE = {"type": "tue", "start": "", "span": 7}


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


def _seed(db, n=3, resolved_prefix=1, initiative_prefix=0):
    """插入 n 条记录：前 resolved_prefix 条已解决，前 initiative_prefix 条主动发起"""
    sl = sqlite3.connect(db)
    for i in range(n):
        sl.execute(
            "INSERT INTO aftersale_records "
            "(created_at, occurred_at, creator, issue_type, table_no, "
            "room_name, region, problem, cause, resolved, is_initiative, "
            "is_our_problem, solution, resolver, response_time, snk_code, "
            "device_code, cycle_start) "
            "VALUES (?, ?, 'tester', '硬件问题', ?, ?, '', '问题X', '', "
            "?, ?, '是', '', '', '', '', '', '')",
            ("2026-08-20 10:00:00", "2026-08-20", f"T{i}", f"room{i}",
             "是" if i < resolved_prefix else "否",
             "是" if i < initiative_prefix else "否"))
    sl.commit()
    ids = [r[0] for r in sl.execute(
        "SELECT id FROM aftersale_records ORDER BY id").fetchall()]
    sl.close()
    return ids


# ==================== mark_resolved_batch ====================

def test_mark_resolved_batch_updates_only_flag(db):
    ids = _seed(db, n=3, resolved_prefix=0)
    n = adb.mark_resolved_batch(ids[:2])
    assert n == 2
    sl = sqlite3.connect(db)
    rows = sl.execute(
        "SELECT id, resolved, updated_at, problem FROM aftersale_records "
        "ORDER BY id").fetchall()
    sl.close()
    assert rows[0][1] == "是" and rows[1][1] == "是"
    assert rows[2][1] == "否"           # 未选中的不动
    assert rows[0][2] != ""             # updated_at 已写入
    assert rows[0][3] == "问题X"        # 其余字段不误改


def test_mark_resolved_batch_empty_ids(db):
    _seed(db, n=2)
    assert adb.mark_resolved_batch([]) == 0
    assert adb.mark_resolved_batch([None, ""]) == 0  # 过滤无效 id


# ==================== delete_records ====================

def test_delete_records_batch(db):
    ids = _seed(db, n=4)
    assert adb.delete_records(ids[1:3]) == 2
    sl = sqlite3.connect(db)
    left = [r[0] for r in sl.execute(
        "SELECT id FROM aftersale_records ORDER BY id")]
    sl.close()
    assert left == [ids[0], ids[3]]


def test_delete_records_empty_ids(db):
    ids = _seed(db, n=2)
    assert adb.delete_records([]) == 0
    sl = sqlite3.connect(db)
    assert len(sl.execute(
        "SELECT id FROM aftersale_records").fetchall()) == 2
    sl.close()


# ==================== query_with_stats 扩展统计 ====================

def test_query_with_stats_extended_keys(db):
    _seed(db, n=4, resolved_prefix=3, initiative_prefix=1)
    total, rows, stats = adb.query_with_stats(1, 50)
    assert total == 4 and len(rows) == 4
    assert stats["total"] == 4
    assert stats["resolved"] == 3
    assert stats["unresolved"] == 1
    assert stats["initiative"] == 1
    assert stats["our_problem"] == 4  # _seed 固定 is_our_problem='是'
    assert stats["rate"] == 75  # 3/4 → 75%


def test_query_with_stats_rate_rounds(db):
    _seed(db, n=3, resolved_prefix=2)
    _t, _r, stats = adb.query_with_stats(1, 50)
    assert stats["rate"] == 67  # 2/3 ≈ 66.67 → 四舍五入 67


def test_query_with_stats_empty_table(db):
    _t, _r, stats = adb.query_with_stats(1, 50)
    assert stats == {"total": 0, "resolved": 0, "unresolved": 0,
                     "initiative": 0, "our_problem": 0, "rate": 0}


def test_query_with_stats_resolved_filter_keeps_rate(db):
    """状态筛选只影响列表，统计口径始终为全量（否则已解决/未解决计数退化）"""
    _seed(db, n=4, resolved_prefix=3)
    total, rows, stats = adb.query_with_stats(1, 50, resolved="否")
    assert total == 1 and len(rows) == 1
    assert stats["total"] == 4 and stats["resolved"] == 3
