# -*- coding: utf-8 -*-
"""sync_meta 时间戳写入（双后端兼容 upsert）回归测试

背景：MySQL 模式下 backend.convert_insert_or_replace 把 INSERT OR REPLACE
转为普通 INSERT，对已存在的 last_sync_xqzg / last_sync_kd_YYYYMMDD 等键重复
刷新会抛 1062（Duplicate entry ... for key 'sync_meta.PRIMARY'），导致
xqzg 接口刷新/日期切换整条链路失败（kd 因键按日期分区只在同日重刷时触发，
xqzg 用固定键每次刷新都触发）。

修复：统一改走 _upsert_sync_meta（先 INSERT，失败回退 UPDATE），语义等价于
SQLite INSERT OR REPLACE / MySQL ON DUPLICATE KEY UPDATE。

- 单元用例用内存 SQLite 验证 upsert 控制流（控制流与后端无关，sqlite3
  IntegrityError 与 MySQL 1062 同为异常、均被 except 捕获；MySQL 侧 SQL
  转换由 test_backend_sql_convert 覆盖）。
- 集成用例在临时库上对 save_xqzg 连续刷新两次，验证端到端不再抛主键冲突、
  sync_meta 仅留一行且值为最新。
"""
import sqlite3

import database.backend as backend
import database.table_db as table_db


def _mem_conn():
    """内存 SQLite + sync_meta 表（控制流测试用）"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT)")
    return conn


# ==================== _upsert_sync_meta 控制流 ====================

def test_upsert_first_call_inserts():
    """键不存在：走 INSERT 分支，值落库"""
    conn = _mem_conn()
    table_db._upsert_sync_meta(conn, "last_sync_xqzg", "2026-08-22 02:00:00")
    row = conn.execute(
        "SELECT value FROM sync_meta WHERE key='last_sync_xqzg'").fetchone()
    assert row is not None
    assert row[0] == "2026-08-22 02:00:00"


def test_upsert_repeat_call_updates_without_duplicate_error():
    """重复刷新同一固定键：必须 UPDATE 而非重复 INSERT，不抛主键冲突

    这正是 xqzg 每次「点刷新」都会触发的场景——旧代码（INSERT OR REPLACE）
    在 MySQL 下被转成普通 INSERT，对已存在键必抛 1062。
    """
    conn = _mem_conn()
    table_db._upsert_sync_meta(conn, "last_sync_xqzg", "2026-08-22 02:00:00")
    # 第二次：键已存在，必须走 except → UPDATE，不能抛 IntegrityError
    table_db._upsert_sync_meta(conn, "last_sync_xqzg", "2026-08-22 02:05:00")
    rows = conn.execute(
        "SELECT value FROM sync_meta WHERE key='last_sync_xqzg'").fetchall()
    assert len(rows) == 1                       # 没有产生重复主键的第二行
    assert rows[0][0] == "2026-08-22 02:05:00"  # 值已更新为最新


def test_upsert_distinct_date_keys_coexist():
    """kd 按日期分区，每日一独立键；同日重刷只更新当天那行"""
    conn = _mem_conn()
    table_db._upsert_sync_meta(conn, "last_sync_kd_20260822", "t1")
    table_db._upsert_sync_meta(conn, "last_sync_kd_20260823", "t2")
    table_db._upsert_sync_meta(conn, "last_sync_kd_20260822", "t1b")  # 覆盖当天
    n = conn.execute("SELECT COUNT(*) FROM sync_meta").fetchone()[0]
    assert n == 2
    v = conn.execute(
        "SELECT value FROM sync_meta WHERE key='last_sync_kd_20260822'"
    ).fetchone()[0]
    assert v == "t1b"


# ==================== save_xqzg 端到端（临时库，刷新两次） ====================

def _prep_xqzg_db(db):
    """按 save_xqzg 实际写入的列预建 xqzg_status + sync_meta（隔离真实库）"""
    sl = sqlite3.connect(db)
    cols = ["id INTEGER PRIMARY KEY", "file_path TEXT DEFAULT ''"]
    cols += [f"{f} TEXT" for f in table_db.STATUS_FIELDS]
    cols += [f"{f} TEXT" for f in table_db.KD_EXTRA_FIELDS]
    sl.execute(f"CREATE TABLE xqzg_status ({', '.join(cols)})")
    sl.execute("CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT)")
    sl.commit()
    sl.close()


def test_save_xqzg_refresh_twice_no_duplicate_key(monkeypatch, tmp_path):
    """xqzg 连续刷新两次：sync_meta 走 upsert，不再抛主键冲突，仅留一行

    复现用户报障场景：面板选 xqzg 接口点刷新 → 1062。修复后第二次刷新应静默
    UPDATE last_sync_xqzg，整条链路不再中断。
    """
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(table_db, "DB_PATH", db)
    monkeypatch.setattr(table_db, "_conn", None)               # 强制在 tmp 上重开
    monkeypatch.setattr(table_db, "_ensure_initialized", lambda c: None)  # 跳过建表/FTS
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)    # 走 SQLite 分支
    _prep_xqzg_db(db)

    rows = [{"table_id": "T1", "club_name": "C1", "device_code": "D1"}]
    # 第一次刷新：INSERT last_sync_xqzg
    assert table_db.save_xqzg(list(rows)) == 1
    # 第二次刷新（固定键已存在）：旧代码 MySQL 下必 1062，现应静默 UPDATE
    assert table_db.save_xqzg(list(rows)) == 1

    conn = table_db._get_conn()
    meta = conn.execute(
        "SELECT value FROM sync_meta WHERE key='last_sync_xqzg'").fetchall()
    assert len(meta) == 1     # 没有重复主键造成的第二行
    assert meta[0][0] != ""   # 时间戳已写入并更新


# ==================== xqzg 按日期分区（2026-08-22 新增） ====================

def _prep_xqzg_db_with_path(db):
    """为日期分区测试预建含 file_path 列的 xqzg_status"""
    _prep_xqzg_db(db)


def test_save_xqzg_with_file_path_stores_date(monkeypatch, tmp_path):
    """save_xqzg(rows, file_path='2026/08/20')：数据行的 file_path 应被写入"""
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(table_db, "DB_PATH", db)
    monkeypatch.setattr(table_db, "_conn", None)
    monkeypatch.setattr(table_db, "_ensure_initialized", lambda c: None)
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)
    _prep_xqzg_db_with_path(db)

    rows = [{"table_id": "T1", "device_code": "D1"}]
    assert table_db.save_xqzg(rows, file_path="2026/08/20") == 1
    conn = table_db._get_conn()
    fp = conn.execute(
        "SELECT file_path FROM xqzg_status LIMIT 1").fetchone()[0]
    assert fp == "2026/08/20"
    # sync_meta 使用日期分区键
    keys = [r[0] for r in conn.execute(
        "SELECT key FROM sync_meta WHERE key LIKE 'last_sync_xqzg_%'").fetchall()]
    assert "last_sync_xqzg_20260820" in keys


def test_save_xqzg_different_dates_partitioned(monkeypatch, tmp_path):
    """不同日期分别 save：互不覆盖，sync_meta 各留一行"""
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(table_db, "DB_PATH", db)
    monkeypatch.setattr(table_db, "_conn", None)
    monkeypatch.setattr(table_db, "_ensure_initialized", lambda c: None)
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)
    _prep_xqzg_db_with_path(db)

    # 写两天数据，每天 1 条
    table_db.save_xqzg([{"table_id": "T1", "device_code": "D1"}],
                       file_path="2026/08/20")
    table_db.save_xqzg([{"table_id": "T2", "device_code": "D2"}],
                       file_path="2026/08/21")
    conn = table_db._get_conn()
    rows = conn.execute(
        "SELECT file_path, device_code FROM xqzg_status ORDER BY file_path").fetchall()
    assert rows == [("2026/08/20", "D1"), ("2026/08/21", "D2")]
    # 两天刷新各产生一条 sync_meta 记录
    n = conn.execute(
        "SELECT COUNT(*) FROM sync_meta WHERE key LIKE 'last_sync_xqzg_%'"
    ).fetchone()[0]
    assert n == 2


def test_query_xqzg_page_filters_by_file_path(monkeypatch, tmp_path):
    """query_xqzg_page(..., file_path='2026/08/20')：只返回该日期的行"""
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(table_db, "DB_PATH", db)
    monkeypatch.setattr(table_db, "_conn", None)
    monkeypatch.setattr(table_db, "_ensure_initialized", lambda c: None)
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)
    _prep_xqzg_db_with_path(db)

    table_db.save_xqzg([{"table_id": "T1", "device_code": "D1"}],
                       file_path="2026/08/20")
    # 21 号一次性写 2 行（save_xqzg 按日期全量替换，跨调用不累积）
    table_db.save_xqzg(
        [{"table_id": "T2", "device_code": "D2"},
         {"table_id": "T3", "device_code": "D3"}],
        file_path="2026/08/21")

    total_20, rows_20 = table_db.query_xqzg_page(
        1, 50, file_path="2026/08/20")
    assert total_20 == 1
    assert rows_20[0]["file_path"] == "2026/08/20"
    assert rows_20[0]["device_code"] == "D1"

    total_21, rows_21 = table_db.query_xqzg_page(
        1, 50, file_path="2026/08/21")
    assert total_21 == 2
    assert all(r["file_path"] == "2026/08/21" for r in rows_21)

    # 不传 file_path：返回全部
    total_all, _ = table_db.query_xqzg_page(1, 50)
    assert total_all == 3


def test_get_xqzg_dates_and_synced(monkeypatch, tmp_path):
    """get_xqzg_dates / get_xqzg_synced_dates：返回写入过的日期"""
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(table_db, "DB_PATH", db)
    monkeypatch.setattr(table_db, "_conn", None)
    monkeypatch.setattr(table_db, "_ensure_initialized", lambda c: None)
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)
    _prep_xqzg_db_with_path(db)

    table_db.save_xqzg([{"table_id": "T1", "device_code": "D1"}],
                       file_path="2026/08/20")
    table_db.save_xqzg([{"table_id": "T2", "device_code": "D2"}],
                       file_path="2026/08/21")
    assert sorted(table_db.get_xqzg_dates()) == ["2026/08/20", "2026/08/21"]
    assert sorted(table_db.get_xqzg_synced_dates()) == ["2026/08/20", "2026/08/21"]
