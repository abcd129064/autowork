# -*- coding: utf-8 -*-
"""fallback_backup 周备份模块测试

验证 MySQL→SQLite 全量备份、7 天到期判定、last_backup 持久化。
用临时 SQLite + fake MySQL 连接隔离，不触达真实库。
"""

import re
import sqlite3
from datetime import datetime

import database.backend as backend
import database.fallback_backup as fb


class _FakeCur:
    def __init__(self, rows, cols):
        self._rows = rows
        self._cols = cols

    @property
    def description(self):
        return self._cols

    def fetchall(self):
        return self._rows


class _FakeConn:
    """按表名返回预设 (rows, cols)；close 为空操作"""
    def __init__(self, data):
        self._data = data

    def execute(self, sql):
        m = re.search(r"FROM `(\w+)`", sql)
        tbl = m.group(1) if m else ""
        rows, cols = self._data.get(tbl, ([], []))
        return _FakeCur(rows, cols)

    def close(self):
        pass


def _prep_local(db):
    sl = sqlite3.connect(db)
    sl.execute("CREATE TABLE sync_meta(key VARCHAR(128) PRIMARY KEY, value TEXT)")
    sl.execute("CREATE TABLE billiard_tables(id INT, name TEXT, city TEXT)")
    sl.commit()
    sl.close()


def test_backup_writes_sqlite_and_marks_time(monkeypatch, tmp_path):
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(fb, "DB_PATH", db)
    monkeypatch.setattr(backend, "get_state", lambda: backend.STATE_ONLINE)
    monkeypatch.setattr(fb.table_db, "_ensure_initialized", lambda c: None)
    _prep_local(db)

    data = {"billiard_tables": ([(1, "n", "c")],
                                [("id",), ("name",), ("city",)])}
    monkeypatch.setattr(backend, "create_mysql_connection",
                        lambda: _FakeConn(data))

    ok, msg, n = fb.backup_mysql_to_sqlite()
    assert ok is True and n == 1
    assert fb.get_last_backup_time() != ""

    sl = sqlite3.connect(db)
    assert sl.execute("SELECT count(*) FROM billiard_tables").fetchone()[0] == 1
    assert sl.execute("SELECT name FROM billiard_tables").fetchone()[0] == "n"
    sl.close()


def test_backup_skipped_when_not_online(monkeypatch):
    monkeypatch.setattr(backend, "get_state", lambda: backend.STATE_DEGRADED)
    ok, msg, n = fb.backup_mysql_to_sqlite()
    assert ok is False and n == 0


def test_backup_mysql_failure_marks_degraded(monkeypatch, tmp_path):
    monkeypatch.setattr(fb, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(backend, "get_state", lambda: backend.STATE_ONLINE)

    def boom():
        raise RuntimeError("mysql down")
    monkeypatch.setattr(backend, "create_mysql_connection", boom)
    degraded = {"v": False}
    monkeypatch.setattr(backend, "mark_degraded",
                        lambda: degraded.__setitem__("v", True))

    ok, msg, n = fb.backup_mysql_to_sqlite()
    assert ok is False and n == 0
    assert degraded["v"] is True


def test_is_backup_due_first_time(monkeypatch, tmp_path):
    monkeypatch.setattr(fb, "DB_PATH", str(tmp_path / "no.db"))
    assert fb.is_backup_due() is True


def test_maybe_backup_skips_when_not_due(monkeypatch, tmp_path):
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(fb, "DB_PATH", db)
    _prep_local(db)
    fb.set_last_backup_time(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    ok, msg, n = fb.maybe_backup()
    assert ok is True and n == 0
    assert "未到期" in msg or "跳过" in msg


def test_last_backup_time_roundtrip(monkeypatch, tmp_path):
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(fb, "DB_PATH", db)
    _prep_local(db)
    assert fb.get_last_backup_time() == ""
    fb.set_last_backup_time("2026-08-22 01:00:00")
    assert fb.get_last_backup_time() == "2026-08-22 01:00:00"
