# -*- coding: utf-8 -*-
"""sqlite_io.read_sqlite_table 单元测试

覆盖：正常读取、列交集（请求列超出实际列）、缺列补 None、表不存在返回 []、
请求列与实际列无交集时保留行数。全部使用内存 sqlite3，不触达真实库。
"""

import sqlite3

from database.sqlite_io import read_sqlite_table


def _mem_conn():
    """内存 SQLite + 临时表（两列：a, b）"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (a TEXT, b TEXT)")
    conn.executemany(
        "INSERT INTO t (a, b) VALUES (?, ?)",
        [("x1", "y1"), ("x2", "y2"), ("x3", "y3")])
    conn.commit()
    return conn


def test_reads_all_rows_with_requested_columns():
    """正常读取：返回全部行，键顺序与请求列一致"""
    conn = _mem_conn()
    rows = read_sqlite_table(conn, "t", ("a", "b"))
    assert rows == [
        {"a": "x1", "b": "y1"},
        {"a": "x2", "b": "y2"},
        {"a": "x3", "b": "y3"},
    ]


def test_column_intersection_missing_columns_padded_none():
    """列交集：请求列包含实际表不存在的列时，该列每行补 None"""
    conn = _mem_conn()
    rows = read_sqlite_table(conn, "t", ("a", "c", "b"))
    # 键顺序保持请求列顺序（a, c, b）
    assert [list(r.keys()) for r in rows] == [
        ["a", "c", "b"], ["a", "c", "b"], ["a", "c", "b"]]
    assert rows[0] == {"a": "x1", "c": None, "b": "y1"}
    assert rows[2] == {"a": "x3", "c": None, "b": "y3"}


def test_no_overlap_keeps_row_count_all_none():
    """请求列与实际列无交集：保留行数，所有请求列补 None"""
    conn = _mem_conn()
    rows = read_sqlite_table(conn, "t", ("c", "d"))
    assert len(rows) == 3
    assert rows[0] == {"c": None, "d": None}


def test_missing_table_returns_empty():
    """表不存在：返回空列表"""
    conn = sqlite3.connect(":memory:")
    assert read_sqlite_table(conn, "no_such_table", ("a",)) == []


def test_empty_table_returns_empty_rows():
    """表存在但无数据：返回空列表"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE empty_t (a TEXT)")
    assert read_sqlite_table(conn, "empty_t", ("a",)) == []


def test_read_only_does_not_write():
    """只读工具：读取后行数与内容不变，且未创建新表/写数据"""
    conn = _mem_conn()
    before = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    read_sqlite_table(conn, "t", ("a", "b"))
    after = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert before == after == 3
    assert tables == {"t"}
