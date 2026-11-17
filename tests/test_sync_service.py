# -*- coding: utf-8 -*-
"""sync_service 离线单测：内存 SQLite，不碰真实业务库。"""
import sqlite3

import pytest

from database import sync_service as svc
from database import sync_transport as st


def _conn():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE aftersale_records ("
              "id INTEGER PRIMARY KEY AUTOINCREMENT, "
              "created_at TEXT, creator TEXT, table_no TEXT, problem TEXT, "
              "updated_at TEXT, solution TEXT, deleted INTEGER DEFAULT 0)")
    c.execute("CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT)")
    return c


class FakeTransport:
    def __init__(self, pages=None, version=st.CLIENT_CONTRACT_VERSION):
        self.pages = pages or {}
        self.calls = []
        self.version = version

    def check_contract(self):
        if self.version != st.CLIENT_CONTRACT_VERSION:
            raise st.SyncTransportError("contract mismatch")
        return {"contract_version": self.version}

    def query(self, table, cursor=None, page_size=200, active_only=False):
        self.calls.append((table, cursor, page_size, active_only))
        return self.pages.get(cursor, {"rows": [], "count": 0, "has_more": False,
                                       "next_cursor": None})


def _row(problem, ts, solution=""):
    return {"id": 1, "created_at": "2026-11-18 09:00:00", "creator": "u",
            "table_no": "1", "problem": problem, "updated_at": ts,
            "solution": solution, "deleted": 0,
            "mysql_only_column": "ignored"}


def test_first_pull_inserts_rows_and_writes_watermark():
    c = _conn()
    t = FakeTransport({None: {
        "rows": [_row("p1", "2026-11-18 10:00:00"),
                 _row("p2", "2026-11-18 10:00:00")],
        "has_more": False, "next_cursor": "2026-11-18 10:00:00|2"}})
    r = svc.SyncService(t, c).pull_incremental(page_size=2)
    assert (r["pages"], r["rows"], r["inserted"], r["updated"]) == (1, 2, 2, 0)
    assert c.execute("SELECT COUNT(*) FROM aftersale_records").fetchone()[0] == 2
    assert c.execute("SELECT value FROM sync_meta WHERE key=?",
                     (svc.WATERMARK_KEY,)).fetchone()[0] == "2026-11-18 10:00:00|2"
    assert t.calls[0][1] is None  # 无水位 = 全量起点


def test_second_pull_starts_from_watermark_and_lww_skips_local_newer():
    c = _conn()
    c.execute("INSERT INTO sync_meta (key,value) VALUES (?,?)",
              (svc.WATERMARK_KEY, "2026-11-18 10:00:00|2"))
    c.execute("INSERT INTO aftersale_records "
              "(created_at,creator,table_no,problem,updated_at,solution) "
              "VALUES ('2026-11-18 09:00:00','u','1','p1',"
              "'2026-11-18 12:00:00','local-newer')")
    t = FakeTransport({"2026-11-18 10:00:00|2": {
        "rows": [_row("p1", "2026-11-18 11:00:00", "server-older")],
        "has_more": False, "next_cursor": "2026-11-18 11:00:00|3"}})
    r = svc.SyncService(t, c).pull_incremental()
    assert t.calls[0][1] == "2026-11-18 10:00:00|2"
    assert r["skipped"] == 1  # 本地更新，服务端不得覆盖
    assert c.execute("SELECT solution FROM aftersale_records "
                     "WHERE problem='p1'").fetchone()[0] == "local-newer"


def test_server_newer_updates_only_shared_columns():
    c = _conn()
    c.execute("INSERT INTO aftersale_records "
              "(created_at,creator,table_no,problem,updated_at,solution) "
              "VALUES ('2026-11-18 09:00:00','u','1','p1',"
              "'2026-11-18 09:00:00','old')")
    t = FakeTransport({None: {
        "rows": [_row("p1", "2026-11-18 10:00:00", "new")],
        "has_more": False, "next_cursor": "2026-11-18 10:00:00|9"}})
    r = svc.SyncService(t, c).pull_incremental()
    assert r["updated"] == 1
    assert c.execute("SELECT solution FROM aftersale_records "
                     "WHERE problem='p1'").fetchone()[0] == "new"


def test_zero_rows_returns_visible_notice():
    c = _conn()
    r = svc.SyncService(FakeTransport(), c).pull_incremental()
    assert r["rows"] == 0 and "0 条" in r["notice"]


def test_contract_mismatch_blocks_sync():
    c = _conn()
    t = FakeTransport(version="9.9.9")
    with pytest.raises(st.SyncTransportError, match="contract mismatch"):
        svc.SyncService(t, c).pull_incremental()
    assert c.execute("SELECT COUNT(*) FROM aftersale_records").fetchone()[0] == 0


def test_batch_cap_returns_notice():
    c = _conn()
    page = {"rows": [_row("p", "2026-11-18 10:00:00")], "has_more": True,
            "next_cursor": "x|1"}

    class Endless(FakeTransport):
        def query(self, *a, **kw):
            return page

    r = svc.SyncService(Endless(), c, max_batches=3).pull_incremental()
    assert r["pages"] == 3 and "分批上限" in r["notice"]


def test_from_settings_disabled_returns_none(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(st, "app_settings", SimpleNamespace(get_merged=lambda: {}))
    assert svc.SyncService.from_settings(_conn()) is None
