# -*- coding: utf-8 -*-
"""T04 直读旁路收敛到双后端测试

旧 table_panel 已删除；management/table_page / forensic_report 不再裸连 SQLite，统一经 table_db 双后端 API。
本文件覆盖新增/复用的读侧函数（纯 SQLite 模式行为），并验证它们都走
_get_conn 双后端路由（主模式即读 MySQL）。UI 文件本身依赖 PySide6，
由 py_compile + 源码断言覆盖。
"""

import sqlite3

import database.table_db as table_db
from database import schema


def _prep_db(db):
    """建 billiard_tables + kd_status（schema DDL），插入测试行"""
    conn = sqlite3.connect(db)
    conn.executescript(schema.to_sqlite_ddl("billiard_tables"))
    conn.executescript(schema.to_sqlite_ddl("kd_status"))
    conn.execute(
        "INSERT INTO billiard_tables (id, name, roomName, snk_code, remark) "
        "VALUES (1, 'T1', 'R1', 'snk_001', 'snk_001 备注'), "
        "(2, 'T2', 'R2', 'snk_002', 'host 192.168.1.10')")
    conn.execute(
        "INSERT INTO kd_status (id, file_path, table_id, club_name, "
        "device_code, status, target_directory, normal_files) "
        "VALUES (1, '2026/08/02', 'T1', 'C1', 'D1', '0', '/dir', '[\"a\"]'), "
        "(2, '2026/08/03', 'T2', 'C2', 'D2', '1', '/dir2', '[]')")
    conn.commit()
    conn.close()


def _use_db(monkeypatch, db):
    """让 table_db 指向临时库并跳过初始化（不触达真实 tables.db）"""
    monkeypatch.setattr(table_db, "DB_PATH", db)
    monkeypatch.setattr(table_db, "_conn", None)
    monkeypatch.setattr(table_db, "_initialized", True)


# ==================== get_latest_kd_status_by_code ====================

def test_get_latest_kd_status_by_code_exact(monkeypatch, tmp_path):
    db = str(tmp_path / "t.db")
    _prep_db(db)
    _use_db(monkeypatch, db)
    info = table_db.get_latest_kd_status_by_code("D1")
    assert info == {"status": "0", "file_path": "2026/08/02"}


def test_get_latest_kd_status_by_code_like_fallback(monkeypatch, tmp_path):
    """device_code 模糊匹配（球桌号当设备码子串）"""
    db = str(tmp_path / "t.db")
    _prep_db(db)
    _use_db(monkeypatch, db)
    info = table_db.get_latest_kd_status_by_code("D")
    assert info is not None and info["status"] == "1"  # 最新分区 D2


def test_get_latest_kd_status_by_code_missing(monkeypatch, tmp_path):
    db = str(tmp_path / "t.db")
    _prep_db(db)
    _use_db(monkeypatch, db)
    assert table_db.get_latest_kd_status_by_code("NO_SUCH") == {}


# ==================== query_latest_kd_full（取证报告用） ====================

def test_query_latest_kd_full_by_table_id(monkeypatch, tmp_path):
    db = str(tmp_path / "t.db")
    _prep_db(db)
    _use_db(monkeypatch, db)
    info = table_db.query_latest_kd_full(table_id="T1")
    assert info["device_code"] == "D1"
    assert info["status"] == "0"
    assert info["file_path"] == "2026/08/02"
    assert info["normal_files"] == ["a"]  # 文件清单已反序列化


def test_query_latest_kd_full_by_code(monkeypatch, tmp_path):
    db = str(tmp_path / "t.db")
    _prep_db(db)
    _use_db(monkeypatch, db)
    info = table_db.query_latest_kd_full(table_id="NO", device_code="D2")
    assert info["device_code"] == "D2"
    assert info["status"] == "1"


def test_query_latest_kd_full_missing(monkeypatch, tmp_path):
    db = str(tmp_path / "t.db")
    _prep_db(db)
    _use_db(monkeypatch, db)
    assert table_db.query_latest_kd_full(table_id="NO", device_code="NOPE") == {}


# ==================== get_table_info_by_snk_or_host（取证报告用） ====================

def test_table_info_by_snk_exact(monkeypatch, tmp_path):
    db = str(tmp_path / "t.db")
    _prep_db(db)
    _use_db(monkeypatch, db)
    info = table_db.get_table_info_by_snk_or_host("snk_001")
    assert info["name"] == "T1"
    assert info["snk_code"] == "snk_001"
    assert info["roomName"] == "R1"


def test_table_info_by_remark_snk(monkeypatch, tmp_path):
    """snk 精确不中 → remark LIKE %snk%（snk_001 出现在 T2 的 remark 中）"""
    db = str(tmp_path / "t.db")
    _prep_db(db)
    _use_db(monkeypatch, db)
    info = table_db.get_table_info_by_snk_or_host("snk_001x")  # 精确不中
    # remark LIKE '%snk_001x%' 不中 → host 兜底也空 → 空 dict
    assert info == {}


def test_table_info_by_host_fallback(monkeypatch, tmp_path):
    """snk 为空/不中 → remark LIKE %host%"""
    db = str(tmp_path / "t.db")
    _prep_db(db)
    _use_db(monkeypatch, db)
    info = table_db.get_table_info_by_snk_or_host("", "192.168.1.10")
    assert info["name"] == "T2"


# ==================== 双后端路由（不裸连 SQLite） ====================

def test_read_functions_route_through_get_conn(monkeypatch, tmp_path):
    """三个读侧函数均经 _get_conn 双后端路由（主模式即读 MySQL）"""
    db = str(tmp_path / "t.db")
    _prep_db(db)
    _use_db(monkeypatch, db)
    calls = []
    real = table_db._get_conn

    def spy():
        calls.append(1)
        return real()

    monkeypatch.setattr(table_db, "_get_conn", spy)
    table_db.get_latest_kd_status_by_code("D1")
    table_db.query_latest_kd_full(table_id="T1")
    table_db.get_table_info_by_snk_or_host("snk_001")
    assert len(calls) == 3


def test_windows_no_bare_sqlite_connect():
    """旧 table_panel 已删除；management/table_page 与 forensic_report 不再裸
    sqlite3.connect（源码断言）"""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("management/table_page.py", "tunnel/forensic_report.py"):
        with open(os.path.join(root, "windows", name),
                  "r", encoding="utf-8") as f:
            src = f.read()
        assert "sqlite3.connect" not in src, f"{name} 残留裸连接"
        assert "import sqlite3" not in src, f"{name} 残留 sqlite3 import"
