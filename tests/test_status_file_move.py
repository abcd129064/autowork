# -*- coding: utf-8 -*-
"""设备状态「文件迁移」的本地即时对账（2026-10-04）

背景（用户报障）：运维面板 → 设备状态 → 连 xqzg 接口 → 点「操作」列滑出文件
面板 → 迁移一条到「使用」，InfoBar 已提示迁移成功，但列表不刷新（原来 3 条
还是 3 条），表格计数要 5-10s 后才随静默刷新变化。两个缺陷：

1. ``FileListPanel.refresh_if_visible`` 固定查 ``kd_status``：xqzg 数据源下
   要么查不到该设备（清单永远停在迁移前的条数），要么取回同名设备的 kd 行；
2. 迁移成功后只等接口全量往返（xqzg 千台设备翻页 + 整分区 DELETE/INSERT），
   没有本地即时回显。

覆盖：
- ``move_file_in_row``：内存快照上的清单移动 + 计数增量（含轻量行/空计数/
  负值夹取/同分类无动作）
- ``apply_file_move``：单行落库（只 UPDATE 变更列、不触碰其他设备，未命中/
  分区不符返回 0，非法表名或字段 fail fast）
- ``query_xqzg_by_device``：分源单设备查询（清单反序列化、kd 同名设备不串源）
- 面板链路源码级不变式（不 import Qt：本仓库基线解释器缺 qfluentwidgets，
  GUI 模块无法收集，故按源码文本断言）

隔离方式：内存 SQLite（monkeypatch ``table_db.DB_PATH``），不落盘、不碰真实
``database/tables.db``。不用 pytest 的 ``tmp_path``——基线解释器（3.13 /
Windows）会把它的 mode 0o700 落成连自身都列不出来的目录（WinError 5），
相关用例会整批 error。
"""

import json
import os
import re
import sqlite3

import pytest

import database.backend as backend
import database.table_db as table_db
from database import schema

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEVICE_PAGE = os.path.join(ROOT, "windows", "management", "device_page.py")


# ==================== 隔离夹具（内存库，不碰真实 tables.db） ====================

def _isolate(monkeypatch) -> sqlite3.Connection:
    """把 table_db 指向内存 SQLite 并建好三张表，返回该连接"""
    monkeypatch.setattr(table_db, "DB_PATH", ":memory:")
    monkeypatch.setattr(table_db, "_conn", None)            # 丢弃旧连接
    monkeypatch.setattr(table_db, "_ensure_initialized", lambda c: None)
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)  # 走 SQLite
    conn = table_db._get_conn()
    for table in ("xqzg_status", "kd_status", "sync_meta"):
        conn.executescript(schema.to_sqlite_ddl(table))
    conn.commit()
    return conn


def _row(**kw) -> dict:
    """一台设备的完整行快照（8 类清单 + 计数列，字段口径同 xqzg_status）"""
    row = {f: [] for f in table_db.KD_FILE_FIELDS}
    row.update({c: "0" for c in table_db.FILE_COUNT_FIELDS.values()})
    row.update(kw)
    return row


# ==================== move_file_in_row：内存快照增量 ====================

def test_move_file_in_row_moves_list_and_counts():
    """迁移一条：源清单移除、目标清单追加，两个计数列各 ±1（TEXT 存储）"""
    row = _row(except_files=["a.jpg", "b.jpg", "c.jpg"], except_count="3",
               operation_files=["x.jpg"], operation_count="1")
    changes = table_db.move_file_in_row(
        row, "except_files", "operation_files", "b.jpg")
    assert row["except_files"] == ["a.jpg", "c.jpg"]
    assert row["operation_files"] == ["x.jpg", "b.jpg"]
    assert row["except_count"] == "2"
    assert row["operation_count"] == "2"
    assert changes == {"except_files": ["a.jpg", "c.jpg"],
                       "operation_files": ["x.jpg", "b.jpg"],
                       "except_count": "2", "operation_count": "2"}


def test_move_file_in_row_dedups_dest_and_tolerates_missing_src():
    """源清单里没有该文件也照样 -1（接口已移走）；目标已存在则不重复追加"""
    row = _row(except_files=[], except_count="3",
               operation_files=["b.jpg"], operation_count="1")
    table_db.move_file_in_row(row, "except_files", "operation_files", "b.jpg")
    assert row["except_files"] == []
    assert row["operation_files"] == ["b.jpg"]
    assert row["except_count"] == "2"
    assert row["operation_count"] == "2"


def test_move_file_in_row_keeps_unknown_counts():
    """计数为空/None（接口未上报）时保持原样，不臆造数值"""
    row = _row(except_files=["a.jpg"], except_count="", operation_count=None)
    changes = table_db.move_file_in_row(
        row, "except_files", "operation_files", "a.jpg")
    assert row["except_files"] == []
    assert row["operation_files"] == ["a.jpg"]
    assert "except_count" not in changes
    assert "operation_count" not in changes


def test_move_file_in_row_clamps_negative_count():
    """服务端计数与清单不一致（0 但清单有条目）时不写负数"""
    row = _row(except_files=["a.jpg"], except_count="0", operation_count="0")
    changes = table_db.move_file_in_row(
        row, "except_files", "operation_files", "a.jpg")
    assert changes["except_count"] == "0"
    assert changes["operation_count"] == "1"


def test_move_file_in_row_light_row_counts_only():
    """列表页轻量行（不含清单）只调计数，不凭空造清单字段"""
    item = {"device_code": "D1", "except_count": "2", "operation_count": "1"}
    changes = table_db.move_file_in_row(
        item, "except_files", "operation_files", "a.jpg")
    assert changes == {"except_count": "1", "operation_count": "2"}
    assert "except_files" not in item and "operation_files" not in item


def test_move_file_in_row_noop_cases():
    """同分类迁移/非法字段/空文件名/空行一律无变更（不产生半截改动）"""
    row = _row(except_files=["a.jpg"], except_count="3")
    assert table_db.move_file_in_row(
        row, "except_files", "except_files", "a.jpg") == {}
    assert row["except_files"] == ["a.jpg"] and row["except_count"] == "3"
    assert table_db.move_file_in_row(
        row, "not_a_field", "operation_files", "a.jpg") == {}
    assert table_db.move_file_in_row(
        row, "except_files", "operation_files", "") == {}
    assert table_db.move_file_in_row(
        {}, "except_files", "operation_files", "a.jpg") == {}


def test_file_count_fields_cover_real_columns():
    """FILE_COUNT_FIELDS 必须指向两张状态表里真实存在的列（防拼错列名）"""
    for table in ("xqzg_status", "kd_status"):
        cols = {c.name for c in schema.TABLE_COLUMNS[table]}
        for field, count_field in table_db.FILE_COUNT_FIELDS.items():
            assert field in cols, f"{table} 缺清单列 {field}"
            assert count_field in cols, f"{table} 缺计数列 {count_field}"


# ==================== apply_file_move：单行落库 ====================

def test_apply_file_move_updates_only_target_row(monkeypatch):
    """按 device_code + 日期分区落库：目标行清单与计数更新，其他设备不动"""
    _isolate(monkeypatch)
    table_db.save_xqzg([
        {"table_id": "383-01", "device_code": "D1",
         "except_files": ["20261003_193651.jpg", "20261003_202202.jpg",
                          "20261003_204609.jpg"],
         "except_count": "3", "operation_files": [], "operation_count": "0"},
        {"table_id": "384-01", "device_code": "D2", "except_count": "5"},
    ], file_path="2026/10/03")

    assert table_db.apply_file_move(
        "xqzg_status", "D1", "2026/10/03", "except_files", "operation_files",
        "20261003_202202.jpg") == 1

    conn = table_db._get_conn()
    src_files, src_count, dest_files, dest_count = conn.execute(
        "SELECT except_files, except_count, operation_files, operation_count "
        "FROM xqzg_status WHERE device_code = 'D1'").fetchone()
    assert json.loads(src_files) == ["20261003_193651.jpg", "20261003_204609.jpg"]
    assert src_count == "2"
    assert json.loads(dest_files) == ["20261003_202202.jpg"]
    assert dest_count == "1"
    # 同分区其他设备完全不受影响
    assert conn.execute("SELECT except_count FROM xqzg_status "
                        "WHERE device_code = 'D2'").fetchone()[0] == "5"


def test_apply_file_move_keeps_other_columns_intact(monkeypatch):
    """只 UPDATE 变更列：同行的状态/正常清单等其他字段不被清空"""
    _isolate(monkeypatch)
    table_db.save_xqzg([
        {"table_id": "383-01", "device_code": "D1", "status": "2",
         "normal_files": ["n1.jpg"], "normal_count": "1",
         "except_files": ["a.jpg"], "except_count": "1",
         "operation_files": [], "operation_count": "0"},
    ], file_path="2026/10/03")

    table_db.apply_file_move("xqzg_status", "D1", "2026/10/03",
                             "except_files", "operation_files", "a.jpg")

    row = table_db.query_xqzg_by_device("D1", "2026/10/03")
    assert row["status"] == "2"
    assert row["normal_files"] == ["n1.jpg"] and row["normal_count"] == "1"
    assert row["except_files"] == [] and row["operation_files"] == ["a.jpg"]


def test_apply_file_move_returns_zero_when_not_matched(monkeypatch):
    """未知设备码 / 分区不符 / 同分类迁移 → 0，不误改其他分区的同名设备"""
    _isolate(monkeypatch)
    table_db.save_xqzg([
        {"device_code": "D1", "except_files": ["a.jpg"], "except_count": "1",
         "operation_count": "0"},
    ], file_path="2026/10/03")

    assert table_db.apply_file_move("xqzg_status", "NOPE", "2026/10/03",
                                    "except_files", "operation_files",
                                    "a.jpg") == 0
    assert table_db.apply_file_move("xqzg_status", "D1", "2026/10/04",
                                    "except_files", "operation_files",
                                    "a.jpg") == 0
    assert table_db.apply_file_move("xqzg_status", "D1", "2026/10/03",
                                    "except_files", "except_files",
                                    "a.jpg") == 0
    # 均为「未命中/无变更」，原行保持不变
    row = table_db.query_xqzg_by_device("D1", "2026/10/03")
    assert row["except_files"] == ["a.jpg"] and row["except_count"] == "1"


def test_apply_file_move_rejects_bad_args(monkeypatch):
    """非法表名/清单字段是编程错误：fail fast 而不是静默改错表"""
    _isolate(monkeypatch)
    with pytest.raises(ValueError):
        table_db.apply_file_move("billiard_tables", "D1", "2026/10/03",
                                 "except_files", "operation_files", "a.jpg")
    with pytest.raises(ValueError):
        table_db.apply_file_move("xqzg_status", "D1", "2026/10/03",
                                 "except_files", "not_a_field", "a.jpg")
    # 空设备码属于「没得可改」，返回 0 而不是抛错（老调用路径兼容）
    assert table_db.apply_file_move("xqzg_status", "", "2026/10/03",
                                    "except_files", "operation_files",
                                    "a.jpg") == 0

# ==================== query_xqzg_by_device：分源单设备查询 ====================

def test_query_xqzg_by_device_deserializes_files(monkeypatch):
    """按 device_code + 分区取单台 xqzg 设备完整行（清单反序列化）"""
    _isolate(monkeypatch)
    table_db.save_xqzg([
        {"table_id": "383-01", "device_code": "D1",
         "except_files": ["a.jpg", "b.jpg"], "except_count": "2"},
    ], file_path="2026/10/03")

    row = table_db.query_xqzg_by_device("D1", "2026/10/03")
    assert row["table_id"] == "383-01"
    assert row["device_code"] == "D1"
    assert row["except_files"] == ["a.jpg", "b.jpg"]
    assert row["file_path"] == "2026/10/03"
    assert table_db.query_xqzg_by_device("D1", "2026/10/04") == {}
    assert table_db.query_xqzg_by_device("NOPE", "2026/10/03") == {}


def test_query_by_device_is_source_isolated(monkeypatch):
    """xqzg 与 kd 分表：同名设备的 kd 行不得串进 xqzg 面板（回归：固定查 kd）"""
    _isolate(monkeypatch)
    table_db.save_xqzg([{"device_code": "D1", "except_count": "3"}],
                       file_path="2026/10/03")
    table_db.save_kd([{"device_code": "D1", "except_count": "9"}],
                     file_path="2026/10/03")

    assert table_db.query_xqzg_by_device("D1", "2026/10/03")["except_count"] == "3"
    assert table_db.query_kd_by_device("D1", "2026/10/03")["except_count"] == "9"


# ==================== 面板链路源码级不变式（不 import Qt） ====================

def _method_src(name: str) -> str:
    """从 device_page.py 文本里截取某个方法的源码

    基线解释器缺 qfluentwidgets，import 该模块会收集失败，故按文本断言。
    """
    text = open(DEVICE_PAGE, encoding="utf-8").read()
    m = re.search(rf"\n    def {name}\(", text)
    assert m, f"device_page.py 未找到方法 {name}"
    rest = text[m.end():]
    nxt = re.search(r"\n    def ", rest)
    return text[m.start():m.end() + (nxt.start() if nxt else len(rest))]


def test_panel_refresh_dispatches_by_source():
    """面板刷新必须按当前数据源选表（此前固定查 kd_status → xqzg 列表不刷新）"""
    body = _method_src("refresh_if_visible")
    assert "table_db.query_xqzg_by_device" in body, \
        "xqzg 数据源刷新未查 xqzg_status：迁移后文件面板取不到新清单"
    assert "table_db.query_kd_by_device" in body, "kd 数据源刷新链路丢失"


def test_migrate_ok_echoes_locally_before_silent_refresh():
    """迁移成功先本地即时回显，再静默刷新对账（否则要等接口全量往返 5-10s）"""
    body = _method_src("_on_migrate_ok")
    assert "_apply_local_move(" in body, "迁移成功未做本地即时回显"
    assert "_silent_refresh(" in body, "迁移成功后静默刷新对账链路丢失"
    assert body.index("_apply_local_move(") < body.index("_silent_refresh("), \
        "本地回显必须在静默刷新之前（否则界面仍要等接口往返）"


def test_panel_local_move_uses_row_helper():
    """面板即时回显必须复用数据层纯函数 move_file_in_row（禁止各写一份）"""
    body = _method_src("apply_local_move")
    assert "table_db.move_file_in_row" in body
    assert "_reload_entries()" in body, "本地移动后未重绘清单"


def test_local_snapshot_write_goes_through_worker():
    """本地快照写回必须走后台 Worker（GUI 线程同步写远程 MySQL 会冻结界面）"""
    body = _method_src("_persist_local_move")
    assert "_DBQueryWorker" in body and "table_db.apply_file_move" in body
    assert "_active_source()" in body, "写回未按数据源选表（kd/xqzg 分表存储）"


def test_silent_refresh_queues_instead_of_dropping():
    """在途刷新期间又有迁移时必须补一轮，不能直接丢弃

    整分区重拉拿到的是发起时刻的快照：期间迁移的条目不在其中，丢弃补刷会把
    本地已即时生效（且已落库）的迁移又落回原分类。
    """
    body = _method_src("_silent_refresh")
    assert "_refresh_pending = True" in body, \
        "已有刷新在途时未记「补一次」标记：迁移结果可能被旧快照覆盖"
    # 断言代码体（split 掉 docstring）：isRunning 判定有竞态，必须用自有标记
    assert "isRunning" not in body.split('"""', 2)[-1], \
        "在途判定用 isRunning 有竞态，应使用自有标记属性"
    done = _method_src("_on_refresh_save_finished")
    assert "_refresh_worker = None" in done and "_refresh_pending" in done, \
        "刷新结束后未复位标记/未补轮"
    # 失败路径也要复位，否则后续刷新被永久判为「在途」
    for name in ("_on_refresh_error", "_on_refresh_save_error"):
        assert "_refresh_worker = None" in _method_src(name), \
            f"{name} 未复位在途标记"
