# -*- coding: utf-8 -*-
"""T03 迁移注册表 + 读侧/键定义统一测试

覆盖：
- schema.MIGRATIONS 注册表：覆盖历史迁移列、两方言列集合一致、ALTER SQL 格式
- SQLite 侧 / MySQL 侧迁移驱动：同一「旧库结构」补列结果一致
- aftersale_db.RECORD_KEY_COLS 售后业务键定义
- merge_back 直读收敛到 sqlite_io（无 _read_sqlite_rows / _AFTERSALE_KEY 残留）
- table_db._setup_fts 的 fts_built 写入改走 _upsert_sync_meta
"""

import re
import sqlite3

import database.aftersale_db as aftersale_db
import database.merge_back as mb
import database.table_db as table_db
from database import schema


# ==================== 售后业务键 ====================

def test_record_key_cols_definition():
    """RECORD_KEY_COLS 为售后业务键单一来源"""
    assert aftersale_db.RECORD_KEY_COLS == (
        "created_at", "creator", "table_no", "problem")


def test_merge_back_uses_record_key_cols():
    """merge_back 不再自持业务键，引用 aftersale_db.RECORD_KEY_COLS"""
    assert not hasattr(mb, "_AFTERSALE_KEY")
    assert mb.merge_aftersale.__code__.co_names  # 可正常调用（import 面）
    import inspect
    src = inspect.getsource(mb)
    assert "aftersale_db.RECORD_KEY_COLS" in src
    assert "_AFTERSALE_KEY_COLS" not in src  # 不再引用已删除的 mysql_sync 常量


# ==================== MIGRATIONS 注册表 ====================

def test_migrations_cover_historical_columns():
    """注册表覆盖历史迁移的全部列（对照 backend.MYSQL_DDL / table_db 迁移块）"""
    by_table = {t: {m.col for m in migs}
                for t, migs in schema.MIGRATIONS.items()}
    # billiard_tables：snk_code / code / city
    assert {"snk_code", "code", "city"} <= by_table["billiard_tables"]
    # aftersale_records：is_initiative / is_our_problem / occurred_at / updated_at
    assert {"is_initiative", "is_our_problem", "occurred_at", "updated_at"} \
        <= by_table["aftersale_records"]
    # xqzg_status / kd_status：device_code（扩展字段入口）+ file_path
    assert "device_code" in by_table["xqzg_status"]
    assert "file_path" in by_table["xqzg_status"]
    assert "device_code" in by_table["kd_status"]
    assert "file_path" in by_table["kd_status"]
    # 8 类文件 JSON 列
    for f in table_db.KD_FILE_FIELDS:
        assert f in by_table["xqzg_status"]
        assert f in by_table["kd_status"]


def test_migrations_dialect_column_sets_match():
    """两方言共用同一列集合（SQLite / MySQL 补列后收敛到一致结构）"""
    for table, migs in schema.MIGRATIONS.items():
        for m in migs:
            assert m.table == table  # 注册条目表名与键一致
            assert m.sqlite_type and m.mysql_type
            # LONGTEXT 不允许 DEFAULT，其余列应有默认值
            if m.mysql_type == "LONGTEXT":
                assert m.mysql_default is None
            else:
                assert m.mysql_default is not None


def test_alter_sql_formats():
    """ALTER SQL 格式与历史迁移逐字等价（normalize 后）"""
    def norm(s):
        return re.sub(r"\s+", " ", s.strip())
    # SQLite：普通列带默认值；文件列默认 '[]'；MySQL LONGTEXT 无默认
    assert norm(schema.sqlite_alter_for("kd_status", "device_code")) == \
        norm("ALTER TABLE kd_status ADD COLUMN device_code TEXT DEFAULT ''")
    assert norm(schema.sqlite_alter_for("kd_status", "normal_files")) == \
        norm("ALTER TABLE kd_status ADD COLUMN normal_files TEXT DEFAULT '[]'")
    assert norm(schema.mysql_alter_for("kd_status", "device_code")) == \
        norm("ALTER TABLE kd_status ADD COLUMN device_code VARCHAR(255) DEFAULT ''")
    assert norm(schema.mysql_alter_for("kd_status", "normal_files")) == \
        norm("ALTER TABLE kd_status ADD COLUMN normal_files LONGTEXT")
    # aftersale 中文字面量默认值
    assert norm(schema.mysql_alter_for("aftersale_records", "is_initiative")) == \
        norm("ALTER TABLE aftersale_records ADD COLUMN is_initiative "
             "VARCHAR(255) DEFAULT '否'")
    # 未登记列抛 KeyError
    import pytest
    with pytest.raises(KeyError):
        schema.sqlite_alter_for("kd_status", "no_such_col")


# ==================== 迁移驱动一致性（同一旧库结构） ====================

# 模拟 MySQL 连接的假 cursor / 连接（记录 ALTER，报告旧列集）
class _IterRows:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def fetchall(self):
        return self._rows


class _FakeMysqlConn:
    """记录 _ensure_mysql_tables 执行的 DDL；SHOW COLUMNS 返回预设旧列集"""

    def __init__(self, old_cols):
        self._old_cols = old_cols  # {table: set(cols)}
        self.executed = []
        self.alters = []

    def execute(self, sql, params=None):
        self.executed.append(sql)
        s = sql.strip().upper()
        if s.startswith("SHOW COLUMNS FROM"):
            table = sql.split("FROM", 1)[1].strip().strip("`")
            return _IterRows([[c] for c in self._old_cols.get(table, set())])
        if s.startswith("ALTER TABLE"):
            self.alters.append(sql)
        return _IterRows([])

    def commit(self):
        pass


# 历史「旧库结构」：只含基础统计列，缺全部迁移目标列
_OLD_SCHEMA = {
    "billiard_tables": {
        "id", "name", "roomName", "onlineStatusName", "remark",
        "cameraPassExt",
    },
    "aftersale_records": {
        "id", "created_at", "creator", "issue_type", "table_no", "room_name",
        "region", "problem", "cause", "resolved", "solution", "resolver",
        "response_time", "snk_code", "device_code", "cycle_start",
    },
    "xqzg_status": {
        "id", "file_path", "table_id", "club_name", "pic_total",
        "normal_count", "normal_total", "except_count", "operation_rate",
        "untreated_count", "operation_count", "accuracy_count",
        "already_count", "rubbish_count", "error_rate",
    },
    "kd_status": {
        "id", "file_path", "table_id", "club_name", "pic_total",
        "normal_count", "normal_total", "except_count", "operation_rate",
        "untreated_count", "operation_count", "accuracy_count",
        "already_count", "rubbish_count", "error_rate",
    },
}


def _extract_added_col(alter_sql: str) -> str:
    """从 'ALTER TABLE t ADD COLUMN col TYPE ...' 提取列名"""
    return alter_sql.split("ADD COLUMN", 1)[1].split()[0]


def test_sqlite_migration_adds_registered_columns():
    """SQLite 侧：旧库经 _migrate_sqlite_add_columns 补齐注册表列"""
    conn = sqlite3.connect(":memory:")
    for table, cols in _OLD_SCHEMA.items():
        col_defs = ", ".join(f"{c} TEXT" for c in cols)
        conn.execute(f"CREATE TABLE {table} ({col_defs})")
    for table in _OLD_SCHEMA:
        table_db._migrate_sqlite_add_columns(conn, table)
    for table, migs in schema.MIGRATIONS.items():
        cur = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        expect = {m.col for m in migs}
        assert expect <= cur, f"{table} 缺列: {expect - cur}"


def test_mysql_and_sqlite_migrations_add_same_columns():
    """同一旧库结构：SQLite 侧与 MySQL 侧补列结果一致"""
    # SQLite 侧
    sl = sqlite3.connect(":memory:")
    for table, cols in _OLD_SCHEMA.items():
        col_defs = ", ".join(f"{c} TEXT" for c in cols)
        sl.execute(f"CREATE TABLE {table} ({col_defs})")
    sl_added = {}
    for table in _OLD_SCHEMA:
        table_db._migrate_sqlite_add_columns(sl, table)
        cur = {r[1] for r in sl.execute(f"PRAGMA table_info({table})")}
        sl_added[table] = cur - _OLD_SCHEMA[table]
    # MySQL 侧（fake 连接记录 ALTER）
    fake = _FakeMysqlConn(_OLD_SCHEMA)
    table_db._ensure_mysql_tables(fake)
    my_added = {t: set() for t in _OLD_SCHEMA}
    for alter in fake.alters:
        table = alter.split("TABLE", 1)[1].split()[0].strip("`")
        if table in my_added:
            my_added[table].add(_extract_added_col(alter))
    # 两方言补列集合逐表一致
    for table in _OLD_SCHEMA:
        assert sl_added[table] == my_added[table], (
            f"{table}: sqlite={sl_added[table]} mysql={my_added[table]}")


def test_mysql_migration_skips_existing_columns():
    """MySQL 侧：已存在的列不重复 ALTER（幂等）"""
    full = set()
    for migs in schema.MIGRATIONS.values():
        full |= {m.col for m in migs}
    old = {t: set(_OLD_SCHEMA[t]) | {m.col for m in schema.MIGRATIONS.get(t, [])}
           for t in _OLD_SCHEMA}
    fake = _FakeMysqlConn(old)
    table_db._ensure_mysql_tables(fake)
    assert fake.alters == []  # 全部列已存在，无任何 ALTER


def test_sqlite_migration_idempotent():
    """SQLite 侧：已补齐的库再次迁移不产生变更"""
    conn = sqlite3.connect(":memory:")
    for table, cols in _OLD_SCHEMA.items():
        col_defs = ", ".join(f"{c} TEXT" for c in cols)
        conn.execute(f"CREATE TABLE {table} ({col_defs})")
    for table in _OLD_SCHEMA:
        assert table_db._migrate_sqlite_add_columns(conn, table) is True
    for table in _OLD_SCHEMA:
        assert table_db._migrate_sqlite_add_columns(conn, table) is False


def test_migration_skips_missing_table():
    """表不存在时迁移跳过（与历史行为一致：PRAGMA 列集为空不迁移）"""
    conn = sqlite3.connect(":memory:")
    assert table_db._migrate_sqlite_add_columns(conn, "no_such_table") is False


# ==================== merge_back 直读收敛 ====================

def test_merge_back_no_local_read_impl():
    """merge_back 不再自持 PRAGMA/SELECT 读取实现，委托 sqlite_io"""
    import inspect
    src = inspect.getsource(mb)
    assert "read_sqlite_table" in src
    assert "_read_sqlite_rows" not in src  # 本地重复实现已删除
    assert hasattr(mb, "_read_rows_as_tuples")


def test_merge_back_reads_with_padding(monkeypatch, tmp_path):
    """_read_rows_as_tuples 保持缺列补空串语义（经 sqlite_io 读侧）"""
    db = str(tmp_path / "t.db")
    sl = sqlite3.connect(db)
    sl.execute("CREATE TABLE device_mapping (device_code TEXT PRIMARY KEY, "
               "local_dir TEXT)")
    sl.execute("INSERT INTO device_mapping VALUES ('D1', '/x')")
    sl.commit()
    sl.close()
    monkeypatch.setattr(mb, "DB_PATH", db)
    rows = mb._read_rows_as_tuples(
        "device_mapping",
        ("device_code", "local_dir", "source", "created_at", "updated_at"))
    assert rows == [("D1", "/x", "", "", "")]


# ==================== _setup_fts 写入走 upsert ====================

def test_fts_built_write_uses_upsert():
    """_setup_fts 不再裸 INSERT OR REPLACE 写 fts_built（防 MySQL 1062）"""
    import inspect
    src = inspect.getsource(table_db._setup_fts)
    assert "_upsert_sync_meta(conn, \"fts_built\", \"1\")" in src
    assert "INSERT OR REPLACE INTO sync_meta" not in src
