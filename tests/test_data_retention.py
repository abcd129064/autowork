# -*- coding: utf-8 -*-
"""数据保留自动清理测试（database.data_retention）

覆盖：
- A 过期分区删除：xqzg_status / kd_status 仅删超期 file_path 分区，
  空 file_path 防御（'' 字典序最早，绝不整表误删）
- B 按大小清理：超阈值按日期桶从最早删，min_keep_days 保护期内不删，
  occurred_at 优先于 created_at 归桶
- 检查间隔：sync_meta last_size_check 控制 60 天一次
- 配置禁用 / 未知表名过滤
- dbstat 表大小统计真实路径

全部用例走 SQLite 分支（临时库 + monkeypatch DB_PATH），真实
_ensure_initialized 建表，SQL 与 MySQL 模式共用（方言转换由
test_backend_sql_convert 覆盖）。
"""
from datetime import date, timedelta

import pytest

import database.backend as backend
import database.data_retention as dr
import database.table_db as table_db


@pytest.fixture
def db(monkeypatch, tmp_path):
    """临时库连接（SQLite 分支，真实建表；每测重建）"""
    table_db.close()
    monkeypatch.setattr(table_db, "_DB_DIR", str(tmp_path))
    monkeypatch.setattr(table_db, "DB_PATH", str(tmp_path / "tables.db"))
    monkeypatch.setattr(table_db, "_initialized", False)  # 强制在临时库上建表
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)
    conn = table_db._get_conn()
    yield conn
    table_db.close()


def _cfg(**kw):
    """测试配置：DEFAULT_CONFIG + tables 白名单 + 覆盖项

    注意：真实 _load_config 会填充 tables 白名单，DEFAULT_CONFIG 本身
    不含该键——测试 helper 必须补齐，否则 _cleanup_by_size 的 for 空转。
    """
    cfg = dict(dr.DEFAULT_CONFIG)
    cfg["tables"] = list(dr._SIZE_TABLES.keys())
    cfg.update(kw)
    return cfg


def _d(days_ago):
    """生成 days_ago 天前的完整时间串（YYYY-MM-DD HH:MM:SS）"""
    return (date.today() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _fp(days_ago):
    """生成 days_ago 天前的分区路径（YYYY/MM/DD）"""
    return (date.today() - timedelta(days=days_ago)).strftime("%Y/%m/%d")


# ==================== A：过期分区清理 ====================

def test_age_cleanup_removes_old_partitions_only(db, monkeypatch):
    """xqzg/kd 仅删超期分区，近 60 天与空 file_path 保留"""
    monkeypatch.setattr(dr, "_load_config", lambda: _cfg(age_days=60))
    old, new = _fp(90), _fp(10)
    for table in ("xqzg_status", "kd_status"):
        db.execute(f"INSERT INTO {table} (file_path) VALUES ('{old}')")
        db.execute(f"INSERT INTO {table} (file_path) VALUES ('{new}')")
        db.execute(f"INSERT INTO {table} (file_path) VALUES ('')")
    db.commit()

    ok, msg, n = dr.run_cleanup()
    assert ok
    assert n == 2                       # 两表各删 1 行旧分区
    for table in ("xqzg_status", "kd_status"):
        rows = db.execute(
            f"SELECT file_path FROM {table}").fetchall()
        assert {r[0] for r in rows} == {new, ""}  # 空串保留，非整表误删


def test_age_cleanup_threshold_rolls_with_today(db, monkeypatch):
    """阈值随当天滚动：61 天前整分区删、59 天前保留

    删除条件为 file_path < today-age_days（严格小于）：恰好 60 天前
    的当天分区不在删除范围内。
    """
    monkeypatch.setattr(dr, "_load_config", lambda: _cfg(age_days=60))
    db.execute(f"INSERT INTO kd_status (file_path) VALUES ('{_fp(61)}')")
    db.execute(f"INSERT INTO kd_status (file_path) VALUES ('{_fp(59)}')")
    db.commit()

    ok, _, n = dr.run_cleanup()
    assert ok
    assert n == 1
    left = db.execute(
        "SELECT file_path FROM kd_status").fetchall()
    assert [r[0] for r in left] == [_fp(59)]


# ==================== B：按大小清理 ====================

def test_size_cleanup_removes_oldest_buckets_keeps_protected(db, monkeypatch):
    """超 3G 从最早日期桶删，30 天保护期内数据保留，删到 <2G 停止"""
    monkeypatch.setattr(dr, "_load_config", lambda: _cfg(
        check_interval_days=60, max_size_gb=3, min_size_gb=2,
        min_keep_days=30))
    old, recent = _d(60), _d(10)
    db.execute(f"INSERT INTO aftersale_records (created_at) VALUES ('{old}')")
    db.execute(f"INSERT INTO aftersale_records (created_at) VALUES ('{old}')")
    db.execute(f"INSERT INTO aftersale_records (created_at) VALUES ('{recent}')")
    db.commit()

    # 模拟表大小：首次 3.2G（触发），删旧桶后 2.9G（仍 >2G，查最早桶时
    # 命中保护期 break），不再查询
    sizes = iter([3.2 * 1024 ** 3, 2.9 * 1024 ** 3, 1.8 * 1024 ** 3])
    monkeypatch.setattr(dr, "_table_size_bytes",
                        lambda conn, t: next(sizes))

    ok, msg, n = dr.run_cleanup()
    assert ok
    assert n == 2                       # 60 天前同桶 2 行一次删完
    left = db.execute(
        "SELECT created_at FROM aftersale_records").fetchall()
    assert [r[0] for r in left] == [recent]   # 保护期内保留
    # 检查时间已记录（60 天检查间隔的落点）
    row = db.execute(
        "SELECT value FROM sync_meta WHERE key='last_size_check'").fetchone()
    assert row is not None
    assert row[0] == date.today().strftime("%Y-%m-%d")


def test_size_below_max_never_deletes(db, monkeypatch):
    """表大小未超阈值：即使有超期数据也不删（只走 A 清理）"""
    monkeypatch.setattr(dr, "_load_config", lambda: _cfg())
    db.execute(f"INSERT INTO aftersale_records (created_at) VALUES ('{_d(90)}')")
    db.commit()
    monkeypatch.setattr(dr, "_table_size_bytes", lambda conn, t: 1024 ** 3)

    ok, _, n = dr.run_cleanup()
    assert ok
    assert n == 0
    assert db.execute(
        "SELECT COUNT(*) FROM aftersale_records").fetchone()[0] == 1


def test_size_cleanup_uses_occurred_at_first(db, monkeypatch):
    """日期桶提取：occurred_at 优先，空则回退 created_at"""
    monkeypatch.setattr(dr, "_load_config", lambda: _cfg())
    db.execute(f"INSERT INTO ledger_records (created_at) VALUES ('{_d(90)}')")
    db.execute(f"INSERT INTO ledger_records (occurred_at) VALUES ('{_d(80)}')")
    db.commit()
    monkeypatch.setattr(dr, "_table_size_bytes", lambda conn, t: 4 * 1024 ** 3)

    ok, _, n = dr.run_cleanup()
    assert ok
    assert n == 2                       # 90 天前、80 天前两个桶都删
    assert db.execute(
        "SELECT COUNT(*) FROM ledger_records").fetchone()[0] == 0


# ==================== 检查间隔 ====================

def test_size_check_interval_controls_frequency(db, monkeypatch):
    """last_size_check 距今 <60 天不触发大小统计；满 60 天触发"""
    monkeypatch.setattr(dr, "_load_config",
                        lambda: _cfg(check_interval_days=60))
    db.execute("INSERT INTO sync_meta (key, value) VALUES (?, ?)",
               ("last_size_check",
                (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")))
    db.commit()
    calls = {"n": 0}

    def fake_size(conn, t):
        calls["n"] += 1
        return 4 * 1024 ** 3
    monkeypatch.setattr(dr, "_table_size_bytes", fake_size)

    ok, _, n = dr.run_cleanup()
    assert ok and n == 0
    assert calls["n"] == 0              # 未到期：从未查表大小

    # 61 天前检查过 → 到期：4 张表各至少查一次
    db.execute("UPDATE sync_meta SET value=? WHERE key='last_size_check'",
               ((date.today() - timedelta(days=61)).strftime("%Y-%m-%d"),))
    db.commit()
    ok, _, _ = dr.run_cleanup()
    assert ok
    assert calls["n"] >= 4


# ==================== 配置与防御 ====================

def test_disabled_config_skips_all(db, monkeypatch):
    """enabled=false：A/B 全部跳过，不产生删除"""
    monkeypatch.setattr(dr, "_load_config", lambda: _cfg(enabled=False))
    db.execute(f"INSERT INTO xqzg_status (file_path) VALUES ('2026/01/01')")
    db.commit()

    ok, msg, n = dr.run_cleanup()
    assert ok
    assert n == 0
    assert db.execute(
        "SELECT COUNT(*) FROM xqzg_status").fetchone()[0] == 1


def test_unknown_table_in_config_ignored(db, monkeypatch):
    """配置 tables 含白名单外表名：静默忽略，不抛错"""
    monkeypatch.setattr(dr, "_load_config",
                        lambda: _cfg(tables=["aftersale_records", "evil_tbl"]))
    db.execute(f"INSERT INTO aftersale_records (created_at) VALUES ('{_d(90)}')")
    db.commit()
    monkeypatch.setattr(dr, "_table_size_bytes", lambda conn, t: 4 * 1024 ** 3)

    ok, _, n = dr.run_cleanup()
    assert ok
    assert n == 1                       # 仅 aftersale_records 被清理


# ==================== 表大小统计真实路径 ====================

def test_table_size_bytes_sqlite_dbstat(db):
    """SQLite 下 dbstat 虚表可统计单表字节（无 monkeypatch 的真实路径）"""
    db.execute(f"INSERT INTO aftersale_records (created_at) VALUES ('{_d(1)}')")
    db.commit()
    size = dr._table_size_bytes(db, "aftersale_records")
    assert size is not None and size > 0
    # 不存在表 → 0（不抛错）
    assert dr._table_size_bytes(db, "no_such_table") == 0


def test_is_mysql_conn_detects_adapter_type():
    """连接类型识别：sqlite3.Connection 不是 MySQL 适配器"""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    assert dr._is_mysql_conn(conn) is False
    conn.close()
