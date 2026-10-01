# -*- coding: utf-8 -*-
"""球桌搜索支持「版本号」（deviceVersion）—— 2026-10-04 新增

背景：面板搜索框此前只覆盖 FIELDS（name / roomName / onlineStatusName /
remark / cameraPassExt / snk_code / code），而「版本号」列 deviceVersion
（wechat listext 字段，形如 200070-20061-100836）不在其中，用户搜
200070 / 20058 查不到任何球桌。

修复：新增 SEARCH_FIELDS = FIELDS + ("deviceVersion",)，同时接入 FTS5
（tables_fts 列集变更）与 LIKE 回退两条路径；老库 tables_fts 用
IF NOT EXISTS 创建、不会自动补列，故补一条「缺 deviceVersion 则删除重建」
迁移，否则搜索引擎会静默查不到版本号。

覆盖：
- LIKE 路径（FTS 不可用 / 关键词 < 3 字符）：版本号子串命中
- FTS5 trigram 路径：版本号子串命中，且与测试球房/手动设备等筛选叠加
- 老库迁移：tables_fts 缺 deviceVersion 时由 _ensure_initialized 重建索引
"""

import sqlite3

import database.backend as backend
import database.table_db as table_db
from database import schema

# 版本号样本：用户实报搜索词 200070 / 20058 分别对应两台球桌
_ROWS = (
    ("A01", "球房甲", "200070-20061-100836", "0"),
    ("B02", "球房乙", "20058-10001-100002", "0"),
    ("C03", "球房丙", "", "0"),
)


def _isolate_like_path(monkeypatch, tmp_path):
    """隔离到临时库并禁用 FTS（模拟 MySQL 兜底 / 无 FTS5），只走 LIKE 路径

    _fts_available 是模块级全局，全量跑时可能被前面的 FTS 用例置 True，
    必须显式压成 False，否则 _fts_cond 会去查本临时库里不存在的 tables_fts。
    """
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(table_db, "DB_PATH", db)
    monkeypatch.setattr(table_db, "_conn", None)
    monkeypatch.setattr(table_db, "_ensure_initialized", lambda c: None)
    monkeypatch.setattr(table_db, "_fts_available", False)
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)
    sl = sqlite3.connect(db)
    sl.executescript(schema.to_sqlite_ddl("billiard_tables"))
    sl.execute("CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT)")
    sl.commit()
    sl.close()
    return db


def _isolate_real_init(monkeypatch, tmp_path):
    """隔离到临时库，走真实 _ensure_initialized（含 _setup_fts 建索引）"""
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(table_db, "DB_PATH", db)
    monkeypatch.setattr(table_db, "_initialized", False)
    monkeypatch.setattr(table_db, "_fts_available", False)
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)
    return db


def _insert_rows(conn, rows=_ROWS):
    conn.executemany(
        "INSERT INTO billiard_tables (name, roomName, deviceVersion, status) "
        "VALUES (?, ?, ?, ?)", rows)
    conn.commit()


# ==================== LIKE 回退路径 ====================

def test_like_path_matches_device_version(monkeypatch, tmp_path):
    """FTS 不可用时，版本号由 SEARCH_FIELDS 的多列 LIKE 命中"""
    _isolate_like_path(monkeypatch, tmp_path)
    conn = table_db._get_conn()
    _insert_rows(conn)

    total, rows = table_db.query_page(1, 50, "200070")
    assert total == 1
    assert rows[0]["name"] == "A01"

    total, rows = table_db.query_page(1, 50, "20058")
    assert total == 1
    assert rows[0]["name"] == "B02"

    # 版本号为空的球桌不应被版本号关键词命中
    assert table_db.query_page(1, 50, "200070")[0] == 1


def test_like_path_search_fields_contains_device_version():
    """搜索列集必须包含 deviceVersion（LIKE 与 FTS 两条路径的单一来源）"""
    assert "deviceVersion" in table_db.SEARCH_FIELDS
    assert table_db.SEARCH_FIELDS[:len(table_db.FIELDS)] == table_db.FIELDS
    assert table_db._FTS_MAP["billiard_tables"] == ("tables_fts", table_db.SEARCH_FIELDS)


# ==================== FTS5 trigram 路径 ====================

def test_fts_path_matches_device_version(monkeypatch, tmp_path):
    """真实 FTS5 索引下，版本号子串命中（关键词 ≥ 3 字符走 FTS）"""
    _isolate_real_init(monkeypatch, tmp_path)
    conn = table_db._get_conn()
    assert table_db._fts_available is True, "本机 SQLite 应支持 fts5 trigram"
    _insert_rows(conn)

    # 确认真走 FTS：索引里能 MATCH 到版本号
    hit = conn.execute(
        "SELECT COUNT(*) FROM tables_fts WHERE tables_fts MATCH ?", ("200070",)
    ).fetchone()[0]
    assert hit == 1

    total, rows = table_db.query_page(1, 50, "200070")
    assert total == 1
    assert rows[0]["name"] == "A01"
    assert rows[0]["deviceVersion"] == "200070-20061-100836"

    total, rows = table_db.query_page(1, 50, "20058")
    assert total == 1
    assert rows[0]["name"] == "B02"


def test_fts_short_keyword_falls_back_to_like_with_version(monkeypatch, tmp_path):
    """关键词 < 3 字符时回退 LIKE，版本号同样可命中"""
    _isolate_real_init(monkeypatch, tmp_path)
    conn = table_db._get_conn()
    _insert_rows(conn)

    assert table_db._fts_cond("tables_fts", "20") is None
    total, _ = table_db.query_page(1, 50, "20")
    assert total == 2  # 甲、乙两台版本号都含 "20"，丙为空


def test_fts_search_combines_with_filters(monkeypatch, tmp_path):
    """版本号搜索与测试球房/手动设备/退单筛选叠加（参数个数不能错位）"""
    _isolate_real_init(monkeypatch, tmp_path)
    conn = table_db._get_conn()
    _insert_rows(conn)
    _insert_rows(conn, (
        ("D04", "公司测试", "200070-99999-000001", "0"),
        ("E05", "球房戊", "200070-88888-000002", "2"),
    ))

    assert table_db.query_page(1, 50, "200070")[0] == 3
    assert table_db.query_page(1, 50, "200070", include_test=False)[0] == 2
    assert table_db.query_page(1, 50, "200070", include_tuidan=False)[0] == 2
    assert table_db.query_page(
        1, 50, "200070", include_test=False, include_tuidan=False)[0] == 1


# ==================== 老库迁移：FTS 索引缺列自动重建 ====================
def test_legacy_fts_rebuilt_when_missing_device_version(monkeypatch, tmp_path):
    """老库 tables_fts 只索引旧列集时，初始化应删除并按新列集重建"""
    db = _isolate_real_init(monkeypatch, tmp_path)
    sl = sqlite3.connect(db)
    sl.executescript(schema.to_sqlite_ddl("billiard_tables"))
    sl.executescript(schema.to_sqlite_ddl("sync_meta"))
    sl.execute(
        "INSERT INTO billiard_tables (name, roomName, deviceVersion, status) "
        "VALUES ('A01', '球房甲', '200070-20061-100836', '0')")
    # 复刻旧版（仅 FIELDS 列）的 FTS 表：版本号未被索引
    old_cols = ", ".join(table_db.FIELDS)
    sl.execute(
        f"CREATE VIRTUAL TABLE tables_fts USING fts5({old_cols}, "
        f"content='billiard_tables', content_rowid='id', tokenize='trigram')")
    sl.execute("INSERT INTO tables_fts(tables_fts) VALUES('rebuild')")
    sl.execute("INSERT INTO sync_meta (key, value) VALUES ('fts_built', '1')")
    sl.commit()
    assert [r[1] for r in sl.execute("PRAGMA table_info(tables_fts)")].count(
        "deviceVersion") == 0
    assert sl.execute(
        "SELECT COUNT(*) FROM tables_fts WHERE tables_fts MATCH ?",
        ("200070",)).fetchone()[0] == 0  # 旧索引搜不到版本号 = 用户报的现象

    table_db._ensure_initialized(sl)
    sl.commit()

    total, rows = table_db.query_page(1, 50, "200070")
    assert total == 1
    assert rows[0]["name"] == "A01"


# ==================== 老库迁移：billiard_tables 缺「版本号/退单」列 ====================

# 2026-09-20 版本号列上线前建成的本地库：无 deviceVersion / status
_OLD_BILLIARD_DDL = """
CREATE TABLE IF NOT EXISTS billiard_tables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT DEFAULT '',
    roomName TEXT DEFAULT '',
    onlineStatusName TEXT DEFAULT '',
    remark TEXT DEFAULT '',
    cameraPassExt TEXT DEFAULT '',
    snk_code TEXT DEFAULT '',
    code TEXT DEFAULT '',
    city TEXT DEFAULT ''
)
"""


def test_legacy_billiard_tables_gains_version_and_status(monkeypatch, tmp_path):
    """老库缺 deviceVersion/status 时按 schema.MIGRATIONS 补列

    此前 billiard_tables 未走注册表补列（只有 snk_code/code/city 三个硬编码块），
    真实 database/tables.db（2026-08-21 最后同步）至今缺这两列 —— SQLite 兜底
    查询会因 SELECT deviceVersion, status 直接报错，版本号搜索更无从谈起。
    """
    db = _isolate_real_init(monkeypatch, tmp_path)
    sl = sqlite3.connect(db)
    sl.executescript(_OLD_BILLIARD_DDL)
    sl.executescript(schema.to_sqlite_ddl("sync_meta"))
    sl.execute("INSERT INTO billiard_tables (name, roomName) VALUES ('A01', '球房甲')")
    sl.commit()
    assert "deviceVersion" not in [
        r[1] for r in sl.execute("PRAGMA table_info(billiard_tables)")]

    table_db._ensure_initialized(sl)
    cols = [r[1] for r in sl.execute("PRAGMA table_info(billiard_tables)")]
    assert {"deviceVersion", "status"} <= set(cols)
    sl.close()

    # 补列后查询不再因缺列报错；无版本号的老数据不误命中
    assert table_db.query_page(1, 50, "200070")[0] == 0

    # 数据补齐版本号后可命中
    conn = table_db._get_conn()
    conn.execute("UPDATE billiard_tables SET deviceVersion='200070-20061-100836'")
    conn.commit()
    total, rows = table_db.query_page(1, 50, "200070")
    assert total == 1
    assert rows[0]["name"] == "A01"
