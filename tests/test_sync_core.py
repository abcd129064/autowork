# -*- coding: utf-8 -*-
"""sync_core 离线单测：契约、设备认证、分页夹逼、增量游标。

只测纯逻辑；fastapi/pymysql 在本机 venv 缺失，HTTP 路由层留给真机冒烟。
"""
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "web", "aftersale_api"))
import sync_core as sc  # noqa: E402


def _devices(**overrides):
    rec = {
        "device_id": "desk-001",
        "token_hash": sc.hash_token("secret-token"),
        "enabled": True,
        "scopes": [sc.READ_SCOPE],
    }
    rec.update(overrides)
    return {"desk-001": rec}


def test_hash_token_stable_and_distinct():
    assert sc.hash_token("abc") == sc.hash_token("abc")
    assert sc.hash_token("abc") != sc.hash_token("abd")
    assert len(sc.hash_token("abc")) == 64


def test_verify_device_ok():
    ok, reason = sc.verify_device("desk-001", "secret-token", _devices(), sc.READ_SCOPE)
    assert ok and reason == "ok"


@pytest.mark.parametrize("device,token,why", [
    ("", "secret-token", "missing credentials"),
    ("desk-001", "", "missing credentials"),
    ("nope", "secret-token", "unknown device"),
    ("desk-001", "wrong", "invalid token"),
])
def test_verify_device_rejects(device, token, why):
    ok, reason = sc.verify_device(device, token, _devices(), sc.READ_SCOPE)
    assert not ok and reason == why


def test_verify_device_disabled_is_revocation():
    ok, reason = sc.verify_device(
        "desk-001", "secret-token", _devices(enabled=False), sc.READ_SCOPE)
    assert not ok and reason == "device disabled"


def test_verify_device_scope_denied():
    ok, reason = sc.verify_device(
        "desk-001", "secret-token", _devices(), "aftersale_records:write")
    assert not ok and reason == "scope denied"


def test_load_devices_missing_file_is_deny_all(tmp_path):
    assert sc.load_devices(str(tmp_path / "nope.json")) == {}


def test_load_devices_bad_json_is_deny_all(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{broken", encoding="utf-8")
    assert sc.load_devices(str(p)) == {}


def test_load_devices_filters_incomplete_records(tmp_path):
    p = tmp_path / "devices.json"
    p.write_text(json.dumps({"devices": [
        {"device_id": "a", "token_hash": "x", "enabled": True},
        {"device_id": "b"},
        {"nope": 1},
    ]}), encoding="utf-8")
    assert set(sc.load_devices(str(p))) == {"a"}


@pytest.mark.parametrize("raw,expected", [
    (0, 1), (1, 1), (499, 499), (500, 500), (501, 500),
    ("abc", sc.DEFAULT_PAGE_SIZE), (None, sc.DEFAULT_PAGE_SIZE), ("120", 120),
])
def test_clamp_page_size(raw, expected):
    assert sc.clamp_page_size(raw) == expected


def test_cursor_roundtrip():
    c = sc.encode_cursor("2026-11-07 12:00:00", 42)
    assert sc.decode_cursor(c) == ("2026-11-07 12:00:00", 42)


@pytest.mark.parametrize("bad", [None, "", "no-delimiter", "stamp|not-int", "|-1"])
def test_decode_cursor_invalid_falls_back_to_full(bad):
    assert sc.decode_cursor(bad) is None


def test_contract_declares_unsupported_write_path():
    c = sc.contract()
    assert c["contract_version"] == sc.CONTRACT_VERSION
    assert "aftersale_records" in c["tables"]
    assert "ledger_records" in c["tables"]
    assert c["capabilities"]["incremental_read"] is True
    assert c["capabilities"]["batch_upsert"] == "gated"
    assert c["semantics"]["upsert_kd_clear_same_day"] == "pending"
    assert c["semantics"]["lww_conflict"] == "supported"
    assert c["page_size"]["max"] == 500


def test_table_whitelist_blocks_injection():
    assert sc.is_supported_table("aftersale_records")
    assert not sc.is_supported_table("aftersale_records; DROP TABLE x")
    assert not sc.is_supported_table("")


def test_lww_decision_matches_merge_back():
    assert sc.lww_decision("2026-11-18 10:00:00", None) == "insert"
    assert sc.lww_decision("2026-11-18 10:00:00", "2026-11-18 09:00:00") == "update"
    assert sc.lww_decision("2026-11-18 09:00:00", "2026-11-18 10:00:00") == "skip"
    # 平手必须跳过，否则双方来回覆盖
    assert sc.lww_decision("2026-11-18 10:00:00", "2026-11-18 10:00:00") == "skip"
    assert sc.lww_decision("bad", "2026-11-18 10:00:00") == "skip"


def test_parse_dt_takes_first_19_chars():
    assert sc.parse_dt("2026-11-18 10:00:00.123") == sc.parse_dt("2026-11-18 10:00:00")
    assert sc.parse_dt(None).year == 1


def test_normalize_upsert_rows_whitelist_and_key_guard():
    rows = [{"created_at": "2026-11-18 09:00:00", "creator": "u",
             "table_no": "1", "problem": "p", "id": 999, "deleted": 1}]
    good, errors = sc.normalize_upsert_rows(rows)
    assert not errors and len(good) == 1
    assert len(good[0]) == len(sc.AFTERSALE_COLS)
    assert 999 not in good[0]  # id/deleted 不在白名单，进不了 SQL


def test_normalize_upsert_rows_rejects_bad_shapes():
    assert sc.normalize_upsert_rows("nope")[0] == []
    good, errors = sc.normalize_upsert_rows([{"creator": "u"}])
    assert len(good) == 1 and errors == []  # 单键非空即能定位
    good, errors = sc.normalize_upsert_rows([{"foo": 1}])
    assert good == [] and errors and "empty business key" in errors[0]
    good, errors = sc.normalize_upsert_rows([{"creator": "u"}] * 501)
    assert good == [] and "too many rows" in errors[0]


def test_upsert_sql_is_parameterized_and_1062_safe():
    sql = sc.build_upsert_sql()
    assert sql["select"].count("%s") == len(sc.RECORD_KEY_COLS)
    assert sql["insert"].count("%s") == len(sc.AFTERSALE_COLS)
    assert sql["update"].count("%s") == len(sc.AFTERSALE_COLS)
    assert "ON DUPLICATE KEY UPDATE" in sql["watermark"]
    assert "INSERT OR REPLACE" not in sql["watermark"]
    for key in ("select", "update", "insert"):
        assert "`aftersale_records`" in sql[key]


# ---- 步骤 5：8 表统一策略框架 ----
ALL_TABLES = list(sc.TABLE_SPECS)


def test_contract_declares_eight_tables_and_four_strategies():
    c = sc.contract()
    assert len(c["tables"]) == 8
    assert set(c["tables"]) == set(ALL_TABLES)
    assert {s["strategy"] for s in c["table_specs"].values()} == {
        "lww", "overwrite", "append", "upsert_kd"}
    assert c["semantics"]["destructive_full_replace"] == "not_exposed"
    # 清同日其他设备记录没有实现，不能对外宣称 supported
    assert c["semantics"]["upsert_kd_clear_same_day"] == "pending"
    assert c["semantics"]["kd_row_upsert"] == "supported_no_delete"
    assert c["capabilities"]["conflict_resolution"] == "per_table_strategy"


def test_effective_cols_keeps_business_id_but_drops_server_id():
    for t in ("billiard_tables", "xqzg_status", "submission_log"):
        assert "id" in sc.effective_cols(t)
    assert "id" not in sc.effective_cols("kd_status")
    assert sc.effective_cols("kd_status") == tuple(
        c for c in sc.KD_COLS if c != "id")
    assert sc.contract()["table_specs"]["kd_status"]["cols"] == len(
        sc.effective_cols("kd_status"))


@pytest.mark.parametrize("table", ALL_TABLES)
def test_query_sql_is_table_specific_and_parameterized(table):
    spec = sc.TABLE_SPECS[table]
    sql, params = sc.build_query_sql(table, None, 7, active_only=True)
    assert f"`{table}`" in sql
    assert f"ORDER BY `{spec['order_col']}` ASC" in sql
    assert params == [8]  # 多取一行判 has_more
    assert sql.count("%s") == 1  # 表名/列名来自白名单，值不进 SQL 文本
    assert ("deleted = 0" in sql) == spec["has_deleted"]


def test_numeric_cursor_tie_is_int_not_string():
    c = sc.decode_cursor_for("billiard_tables", "10|10")
    assert c == ("10", 10)
    sql, params = sc.build_query_sql("billiard_tables", c, 3)
    assert "`id` > %s" in sql
    assert params == [10, 4] and isinstance(params[0], int)


def test_string_cursor_tie_keeps_text():
    assert sc.decode_cursor_for("device_mapping", "2026-11-18 10:00:00|D1") == (
        "2026-11-18 10:00:00", "D1")


@pytest.mark.parametrize("bad", ["a|b|c", "x|not-int", "x|-1", ""])
def test_bad_numeric_cursor_falls_back_to_full(bad):
    assert sc.decode_cursor_for("submission_log", bad) is None


@pytest.mark.parametrize("table,verb,has_update", [
    ("aftersale_records", "INSERT INTO", True),
    ("ledger_records", "INSERT INTO", True),
    ("device_mapping", "INSERT INTO", True),
    ("health_alerts", "INSERT INTO", True),
    ("billiard_tables", "INSERT INTO", True),
    ("xqzg_status", "INSERT INTO", True),
    ("submission_log", "INSERT IGNORE INTO", False),
    ("kd_status", "INSERT INTO", True),
])
def test_upsert_sql_matches_strategy(table, verb, has_update):
    sql = sc.build_upsert_sql(table)
    assert sql["insert"].startswith(verb)
    assert (sql["update"] is not None) == has_update
    assert sql["cols"] == sc.effective_cols(table)
    assert sql["update_cols"] == tuple(c for c in sql["cols"]
                                       if c not in sql["key"])
    assert "DELETE" not in sql["insert"] + (sql["update"] or "")
    if sc.TABLE_SPECS[table]["strategy"] == "lww":
        assert "SELECT updated_at" in sql["select"]
    else:
        assert "SELECT 1" in sql["select"]


def test_kd_upsert_never_touches_server_id():
    sql = sc.build_upsert_sql("kd_status")
    assert "`file_path` = %s" in sql["select"]
    assert "`device_code` = %s" in sql["select"]
    assert "`id`" not in sql["insert"]
    assert "`id` = %s" not in sql["update"]
    assert "DELETE" not in sql["insert"] + sql["update"]


def test_kd_rows_require_both_key_parts():
    good, errors = sc.normalize_upsert_rows(
        [{"file_path": "/p/a.jpg", "device_code": "D1"}], "kd_status")
    assert not errors and len(good[0]) == len(sc.effective_cols("kd_status"))
    bad, errors = sc.normalize_upsert_rows(
        [{"file_path": "/p/a.jpg"}], "kd_status")
    assert bad == [] and "missing kd key" in errors[0]


@pytest.mark.parametrize("table", ALL_TABLES)
def test_normalize_rows_length_matches_effective_cols(table):
    row = {c: "x" for c in sc.TABLE_SPECS[table]["key"]}
    if table == "kd_status":
        row["device_code"] = "D1"
        row["file_path"] = "/p/a.jpg"
    good, errors = sc.normalize_upsert_rows([row], table)
    assert not errors
    assert len(good[0]) == len(sc.effective_cols(table))


# ---- 生产 schema 收窄：契约不能宣称不存在的表 ----

def test_available_tables_defaults_to_full(monkeypatch):
    monkeypatch.delenv("SYNC_AVAILABLE_TABLES", raising=False)
    assert sc.available_tables() == tuple(sc.SUPPORTED_TABLES)
    assert sc.available_tables("") == tuple(sc.SUPPORTED_TABLES)


def test_available_tables_narrows_and_drops_unknown():
    assert sc.available_tables("aftersale_records, billiard_tables") == (
        "aftersale_records", "billiard_tables")
    # 注入串/未知表直接丢弃，绝不进 SQL
    assert sc.available_tables("aftersale_records; DROP TABLE x,ledger_records") == (
        "ledger_records",)
    # 配了却全无效 = 全表关闭（显式故障，不会退化成 query 500）
    assert sc.available_tables(" , , ") == ()
