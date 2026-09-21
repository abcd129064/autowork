# -*- coding: utf-8 -*-
"""球桌管理 版本号列 + 退单设备筛选（2026-09-20 新增）

数据链路：wechat listext 行（deviceVersion / status：0=正常启动 2=退单）→
save_all 落库 billiard_tables → query_page(include_tuidan=False) 默认排除
退单设备 → TablePage「筛选」菜单「退单设备」开关控制显隐。

覆盖：
- save_all：deviceVersion / status（int→str）正确落库
- query_page：include_tuidan=False 排除 status='2'；'0'/空串/NULL 保留
"""

import sqlite3

import database.backend as backend
import database.table_db as table_db
from database import schema


def _isolate(monkeypatch, tmp_path):
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(table_db, "DB_PATH", db)
    monkeypatch.setattr(table_db, "_conn", None)
    monkeypatch.setattr(table_db, "_ensure_initialized", lambda c: None)
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)
    sl = sqlite3.connect(db)
    sl.executescript(schema.to_sqlite_ddl("billiard_tables"))
    sl.execute("CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT)")
    sl.commit()
    sl.close()
    return db


def test_save_all_persists_version_and_status(monkeypatch, tmp_path):
    """save_all：deviceVersion 与 status（接口 int，落库转 str）正确写入"""
    _isolate(monkeypatch, tmp_path)
    table_db.save_all([
        {"id": 1, "name": "350-01", "code": "D1",
         "deviceVersion": "200070-20061-100836", "status": 0},
        {"id": 2, "name": "350-02", "code": "D2",
         "deviceVersion": "", "status": 2},
        {"id": 3, "name": "350-03", "code": "D3"},  # 两字段均缺省
    ])
    conn = table_db._get_conn()
    rows = dict(conn.execute(
        "SELECT name, deviceVersion || '|' || status FROM billiard_tables"
    ).fetchall())
    assert rows == {
        "350-01": "200070-20061-100836|0",
        "350-02": "|2",
        "350-03": "|",
    }


def test_query_page_excludes_tuidan_by_default_flag(monkeypatch, tmp_path):
    """query_page(include_tuidan=False)：排除 status='2'，'0'/空/NULL 保留"""
    _isolate(monkeypatch, tmp_path)
    conn = table_db._get_conn()
    conn.execute(
        "INSERT INTO billiard_tables (id, name, status) VALUES "
        "(1, 'T1', '0'), (2, 'T2', '2'), (3, 'T3', ''), "
        "(4, 'T4', NULL)")
    conn.commit()

    total_all, rows_all = table_db.query_page(1, 50)
    assert total_all == 4  # 默认 True：全部返回（向后兼容）

    total, rows = table_db.query_page(1, 50, include_tuidan=False)
    names = {r["name"] for r in rows}
    assert total == 3
    assert names == {"T1", "T3", "T4"}  # T2（退单）被排除，NULL/空串不受影响


def test_query_page_returns_version_column(monkeypatch, tmp_path):
    """query_page：行携带 deviceVersion / status 字段供表格渲染"""
    _isolate(monkeypatch, tmp_path)
    conn = table_db._get_conn()
    conn.execute(
        "INSERT INTO billiard_tables (id, name, deviceVersion, status) "
        "VALUES (1, 'T1', '100005', '0')")
    conn.commit()
    _t, rows = table_db.query_page(1, 50)
    assert rows[0]["deviceVersion"] == "100005"
    assert rows[0]["status"] == "0"
    # todesk 富集照常（无 xqzg 表兜底空串）；向日葵从 remark 解析（无 remark 为空）
    assert rows[0]["todesk_id"] == ""
    assert rows[0]["sunflower_id"] == ""


def test_query_page_excludes_three_test_rooms(monkeypatch, tmp_path):
    """include_test=False：排除 公司测试/办公室测试/外借测试 三个内部球房"""
    _isolate(monkeypatch, tmp_path)
    conn = table_db._get_conn()
    conn.execute(
        "INSERT INTO billiard_tables (id, name, roomName) VALUES "
        "(1, 'T1', '公司测试'), (2, 'T2', '办公室测试'), "
        "(3, 'T3', '外借测试'), (4, 'T4', ' 正常球房 '), (5, 'T5', '珊瑚海台球')")
    conn.commit()
    total, rows = table_db.query_page(1, 50, include_test=False)
    assert total == 2
    assert {r["name"] for r in rows} == {"T4", "T5"}  # TRIM 后的正常球房保留
    # include_test=True：全部返回（向后兼容）
    total_all, _ = table_db.query_page(1, 50, include_test=True)
    assert total_all == 5


def test_query_tables_by_room_excludes_test_rooms(monkeypatch, tmp_path):
    """售后球房带出：同样排除三个内部球房"""
    _isolate(monkeypatch, tmp_path)
    conn = table_db._get_conn()
    conn.execute(
        "INSERT INTO billiard_tables (id, name, roomName) VALUES "
        "(1, 'A-01', '珊瑚海台球'), (2, 'B-01', '公司测试'), "
        "(3, 'C-01', '办公室测试'), (4, 'D-01', '外借测试')")
    conn.commit()
    rows = table_db.query_tables_by_room("台球")
    assert {r["name"] for r in rows} == {"A-01"}
