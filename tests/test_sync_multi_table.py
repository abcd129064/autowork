# -*- coding: utf-8 -*-
"""步骤 4 泛化单测：双端表配置一致性、按表 SQL/校验、客户端多表接入。"""
import os
import sqlite3
import sys
from types import SimpleNamespace

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "web", "aftersale_api"))
import sync_core as core  # noqa: E402
from database import sync_service as svc  # noqa: E402
from database import sync_transport as st  # noqa: E402


def test_client_server_table_specs_agree():
    """双端业务键、水位、软删除与策略必须逐表一致，否则会定位到错行。"""
    assert set(svc.TABLE_SPECS) == set(core.TABLE_SPECS)
    assert len(core.TABLE_SPECS) == 8
    for name, client in svc.TABLE_SPECS.items():
        server = core.TABLE_SPECS[name]
        assert tuple(client["key"]) == tuple(server["key"]), name
        assert client["watermark"] == server["watermark"], name
        assert client["has_deleted"] == server["has_deleted"], name
        assert client["strategy"] == server["strategy"], name


def test_scope_and_watermark_helpers():
    assert core.read_scope("ledger_records") == "ledger_records:read"
    assert core.write_scope("ledger_records") == "ledger_records:write"
    assert core.watermark_key("ledger_records") == "last_sync_api_ledger_records"
    assert core.watermark_key("nope") == ""


def test_upsert_sql_for_ledger():
    sql = core.build_upsert_sql("ledger_records")
    spec = core.TABLE_SPECS["ledger_records"]
    assert "`ledger_records`" in sql["insert"]
    assert sql["insert"].count("%s") == len(spec["cols"])
    assert sql["select"].count("%s") == len(spec["key"])
    assert sql["watermark_key"] == spec["watermark"]
    assert "ON DUPLICATE KEY UPDATE" in sql["watermark"]
    assert sql["insert"] != core.build_upsert_sql("aftersale_records")["insert"]


def test_normalize_rows_uses_table_columns():
    ledger_row = {"created_at": "2026-11-18 09:00:00", "signer": "u",
                  "category": "A", "kind": "k", "video_name": "v.mp4",
                  "updated_at": "2026-11-18 10:00:00", "id": 99}
    good, errors = core.normalize_upsert_rows([ledger_row], "ledger_records")
    assert not errors and len(good[0]) == len(core.LEDGER_COLS)
    assert 99 not in good[0]
    assert core.normalize_upsert_rows([ledger_row], "unknown")[0] == []


def test_contract_exposes_table_specs():
    c = core.contract()
    assert c["table_specs"]["ledger_records"]["key"] == list(core.LEDGER_KEY_COLS)
    assert c["table_specs"]["aftersale_records"]["has_deleted"] is True
    assert c["table_specs"]["ledger_records"]["has_deleted"] is False


class FakeTransport:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    def check_contract(self):
        return {"contract_version": st.CLIENT_CONTRACT_VERSION}

    def query(self, table, cursor=None, page_size=200, active_only=False):
        self.calls.append((table, cursor, page_size, active_only))
        return {"rows": self.rows, "count": len(self.rows), "has_more": False,
                "next_cursor": "ts|9"}


def _ledger_conn():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE ledger_records ("
              "id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, kind TEXT, "
              "room_name TEXT, video_name TEXT, frame TEXT, description TEXT, "
              "repro TEXT, new_program TEXT, remark TEXT, signer TEXT, "
              "occurred_at TEXT, created_at TEXT, updated_at TEXT)")
    c.execute("CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT)")
    return c


def test_sync_service_pulls_ledger_with_own_key_and_watermark():
    c = _ledger_conn()
    row = {"id": 1, "created_at": "2026-11-18 09:00:00", "signer": "u",
           "category": "A", "kind": "k", "video_name": "v.mp4",
           "updated_at": "2026-11-18 10:00:00", "description": "d"}
    t = FakeTransport([row])
    service = svc.SyncService(t, c, table="ledger_records")
    r = service.pull_incremental()
    assert r["inserted"] == 1
    assert t.calls[0][0] == "ledger_records"
    assert t.calls[0][3] is False  # 无软删除列，不请求 active_only
    wm = c.execute("SELECT value FROM sync_meta WHERE key=?",
                   (svc.TABLE_SPECS["ledger_records"]["watermark"],)).fetchone()
    assert wm[0] == "ts|9"
    assert c.execute("SELECT description FROM ledger_records").fetchone()[0] == "d"


def test_run_if_enabled_disabled_by_default(monkeypatch):
    monkeypatch.setattr(st, "app_settings", SimpleNamespace(get_merged=lambda: {}))
    assert svc.SyncService.run_if_enabled(_ledger_conn(),
                                           table="ledger_records") is None


def test_run_if_enabled_wraps_errors(monkeypatch):
    cfg = {"sync_api": {"enabled": True, "base_url": "http://x",
                        "device_id": "d", "token": "t"}}
    monkeypatch.setattr(st, "app_settings",
                        SimpleNamespace(get_merged=lambda: cfg))

    class Broken(st.SyncTransport):
        def check_contract(self):
            raise st.SyncTransportError("contract mismatch")

    monkeypatch.setattr(st.SyncTransport, "from_settings",
                        classmethod(lambda cls: Broken("http://x", "d", "t")))
    r = svc.SyncService.run_if_enabled(_ledger_conn(), table="ledger_records")
    assert r["ok"] is False and "contract mismatch" in r["error"]
    assert r["table"] == "ledger_records"


# ---- 步骤 5：8 表全部本地跑通 ----

def _conn_for(table):
    """按服务端 effective_cols 造内存表，模拟本地 SQLite schema。"""
    c = sqlite3.connect(":memory:")
    cols = core.effective_cols(table)
    defs = ", ".join("id INTEGER PRIMARY KEY" if x == "id" else f"{x} TEXT"
                     for x in cols)
    c.execute(f"CREATE TABLE {table} ({defs})")
    c.execute("CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT)")
    return c


def _row_for(table, **overrides):
    spec = core.TABLE_SPECS[table]
    row = {c: "" for c in core.effective_cols(table)}
    for k in spec["key"]:
        row[k] = "K1"
    if spec["order_col"] == "id":
        row["id"] = 1
    if "updated_at" in row:
        row["updated_at"] = "2026-11-18 10:00:00"
    if table == "kd_status":
        row["file_path"] = "/p/a.jpg"
        row["device_code"] = "D1"
    row.update(overrides)
    return row


@pytest.mark.parametrize("table", list(core.TABLE_SPECS))
def test_every_table_pulls_and_tracks_its_own_watermark(table):
    c = _conn_for(table)
    t = FakeTransport([_row_for(table)])
    r = svc.SyncService(t, c, table=table).pull_incremental(active_only=True)
    assert r["inserted"] == 1 and r["rows"] == 1
    assert t.calls[0][0] == table
    # 只有带软删除列的表才向服务端请求 active_only
    assert t.calls[0][3] is core.TABLE_SPECS[table]["has_deleted"]
    wm = c.execute("SELECT value FROM sync_meta WHERE key=?",
                   (core.watermark_key(table),)).fetchone()
    assert wm[0] == "ts|9"
    assert c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1


def test_overwrite_strategy_replaces_snapshot_row():
    table = "billiard_tables"
    c = _conn_for(table)
    svc.SyncService(FakeTransport([_row_for(table, name="old")]), c,
                    table=table).pull_incremental()
    r = svc.SyncService(FakeTransport([_row_for(table, name="new")]), c,
                        table=table).pull_incremental()
    assert r["updated"] == 1
    assert c.execute(f"SELECT name FROM {table}").fetchall() == [("new",)]


def test_append_strategy_never_overwrites_existing_log():
    table = "submission_log"
    c = _conn_for(table)
    svc.SyncService(FakeTransport([_row_for(table, file_name="a.zip")]), c,
                    table=table).pull_incremental()
    r = svc.SyncService(FakeTransport([_row_for(table, file_name="b.zip")]), c,
                        table=table).pull_incremental()
    assert r["skipped"] == 1
    assert c.execute(f"SELECT file_name FROM {table}").fetchall() == [("a.zip",)]


def test_kd_strategy_updates_in_place_without_deleting_other_devices():
    table = "kd_status"
    c = _conn_for(table)
    svc.SyncService(FakeTransport([_row_for(table)]), c,
                    table=table).pull_incremental()
    c.execute(f"INSERT INTO {table} (file_path, device_code, club_name) "
              "VALUES (?, ?, ?)", ("/p/b.jpg", "D2", "club-b"))
    r = svc.SyncService(FakeTransport([_row_for(table, club_name="club-a")]), c,
                        table=table).pull_incremental()
    assert r["updated"] == 1
    rows = c.execute(f"SELECT device_code, club_name FROM {table} "
                     "ORDER BY device_code").fetchall()
    assert rows == [("D1", "club-a"), ("D2", "club-b")]


def test_lww_strategy_on_mapping_table_still_honours_timestamp():
    table = "device_mapping"
    c = _conn_for(table)
    svc.SyncService(FakeTransport(
        [_row_for(table, local_dir="/old", updated_at="2026-11-18 10:00:00")]),
        c, table=table).pull_incremental()
    r = svc.SyncService(FakeTransport(
        [_row_for(table, local_dir="/stale", updated_at="2026-11-18 09:00:00")]),
        c, table=table).pull_incremental()
    assert r["skipped"] == 1
    assert c.execute("SELECT local_dir FROM device_mapping").fetchone()[0] == "/old"


def test_contract_without_table_blocks_pull():
    c = _conn_for("health_alerts")

    class Narrow(FakeTransport):
        def check_contract(self):
            return {"contract_version": st.CLIENT_CONTRACT_VERSION,
                    "tables": ["aftersale_records"]}

    with pytest.raises(RuntimeError, match="lacks table"):
        svc.SyncService(Narrow([_row_for("health_alerts")]), c,
                        table="health_alerts").pull_incremental()
