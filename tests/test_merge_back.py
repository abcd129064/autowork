# -*- coding: utf-8 -*-
"""merge_back LWW 合并逻辑测试

验证售后/设备映射的 LWW 判定（updated_at 较新者覆盖较旧者）、
运维表退化 SQLite 优先。用 fake MySQL cursor 隔离，不触达真实库。
"""

import sqlite3
from datetime import datetime

import database.merge_back as mb


class _FakeCursor:
    """模拟 MySQL cursor：按预设 SELECT 结果返回，记录执行过的 SQL/params"""
    def __init__(self, select_results=None):
        self._select_results = select_results or {}  # sql片段 → row
        self.executed = []  # [(sql, params)]
        self._rowcount = None

    def execute(self, sql, params=None):
        for frag, row in self._select_results.items():
            if frag in sql:
                self._last_row = row
                self.executed.append((sql, params))
                return self
        self._last_row = None
        self.executed.append((sql, params))
        return self

    def executemany(self, sql, seq):
        for p in seq:
            self.executed.append((sql, p))
        return self

    def fetchone(self):
        return getattr(self, "_last_row", None)

    def fetchall(self):
        return []

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    """模拟 MySQL 连接：返回预设 cursor；commit/close 空操作"""
    def __init__(self, select_results=None):
        self._cur = _FakeCursor(select_results or {})

    def cursor(self):
        return self._cur

    def execute(self, sql):
        # merge_ops_tables 用 conn.execute 取列名
        return _FakeCursor()

    def commit(self):
        pass

    def close(self):
        pass


def _sqlite_with_aftersale(db, rows):
    """建 aftersale_records 表并插入测试行"""
    sl = sqlite3.connect(db)
    sl.execute("""CREATE TABLE aftersale_records(
        created_at TEXT, occurred_at TEXT, creator TEXT, issue_type TEXT,
        table_no TEXT, room_name TEXT, region TEXT, problem TEXT, cause TEXT,
        resolved TEXT, is_initiative TEXT, is_our_problem TEXT, solution TEXT,
        resolver TEXT, response_time TEXT, snk_code TEXT, device_code TEXT,
        cycle_start TEXT, updated_at TEXT)""")
    sl.executemany(
        "INSERT INTO aftersale_records VALUES(" + ",".join(["?"] * 19) + ")",
        rows)
    sl.commit()
    sl.close()


def _row(**kw):
    """构造一行 19 列的售后记录（默认空，按 kw 填充）"""
    cols = mb._AFTERSALE_COLS
    return tuple(kw.get(c, "") for c in cols)


def test_aftersale_insert_when_mysql_missing(monkeypatch, tmp_path):
    """MySQL 无该业务键 → INSERT"""
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(mb, "DB_PATH", db)
    _sqlite_with_aftersale(db, [
        _row(created_at="2026-08-20 10:00:00", creator="A",
             table_no="T1", problem="P1", updated_at="2026-08-20 10:00:00")
    ])
    # MySQL SELECT 返回 None（不存在）
    conn = _FakeConn({"SELECT updated_at FROM": None})
    n = mb.merge_aftersale(conn)
    assert n == 1
    sqls = [s for s, _ in conn._cur.executed if "INSERT" in s]
    assert len(sqls) == 1


def test_aftersale_update_when_local_newer(monkeypatch, tmp_path):
    """MySQL 有该键且 updated_at 更旧 → UPDATE（本地较新覆盖）"""
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(mb, "DB_PATH", db)
    _sqlite_with_aftersale(db, [
        _row(created_at="2026-08-20 10:00:00", creator="A",
             table_no="T1", problem="P1",
             updated_at="2026-08-20 12:00:00")  # 本地较新
    ])
    # MySQL 返回更旧的时间
    conn = _FakeConn({"SELECT updated_at FROM": ("2026-08-20 10:00:00",)})
    n = mb.merge_aftersale(conn)
    assert n == 1
    sqls = [s for s, _ in conn._cur.executed if "UPDATE" in s]
    assert len(sqls) == 1


def test_aftersale_skip_when_mysql_newer(monkeypatch, tmp_path):
    """MySQL 有该键且 updated_at 更新 → 跳过（不覆盖他人）"""
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(mb, "DB_PATH", db)
    _sqlite_with_aftersale(db, [
        _row(created_at="2026-08-20 10:00:00", creator="A",
             table_no="T1", problem="P1",
             updated_at="2026-08-20 10:00:00")  # 本地较旧
    ])
    conn = _FakeConn({"SELECT updated_at FROM": ("2026-08-20 12:00:00",)})
    n = mb.merge_aftersale(conn)
    assert n == 0  # 跳过
    # 不应有 INSERT 或 UPDATE
    assert not any("INSERT" in s or "UPDATE" in s
                   for s, _ in conn._cur.executed)


def test_parse_dt_invalid_returns_min():
    assert mb._parse_dt("") == datetime.min
    assert mb._parse_dt(None) == datetime.min
    assert mb._parse_dt("garbage") == datetime.min
    assert mb._parse_dt("2026-08-20 10:00:00") == datetime(2026, 8, 20, 10, 0, 0)


def test_merge_back_skips_when_not_online(monkeypatch):
    from database import backend
    monkeypatch.setattr(backend, "get_state",
                        lambda: backend.STATE_DEGRADED)
    ok, msg, n = mb.merge_back()
    assert ok is False and n == 0
