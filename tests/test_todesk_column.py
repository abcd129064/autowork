# -*- coding: utf-8 -*-
"""球桌管理 ToDesk号 列（2026-09-20 新增）

数据链路：xqzg status/ API 行 → parse_todesk_id 解析（todesk_id 字段优先，
remark「我的识别码」兜底）→ save_xqzg 落库 xqzg_status.todesk_id →
query_page 按 billiard_tables.code ↔ xqzg_status.device_code 关联富集 →
TablePage TABLE_COLUMNS 渲染「ToDesk号」列。

覆盖：
- parse_todesk_id：字段直传 / remark 兜底 / 两者皆无 / 设备代码不误匹配
- save_xqzg：todesk_id 解析后落库（含 remark 兜底路径）
- query_page：富集取最新分区非空值；xqzg 表缺失时静默空串
"""

import sqlite3

import database.backend as backend
import database.table_db as table_db
from database import schema


# ==================== parse_todesk_id 单元 ====================

def test_parse_todesk_id_field_priority():
    """todesk_id 字段非空时优先（xqzg 接口权威来源，如 "897832375"）"""
    item = {"todesk_id": "897832375",
            "remark": "ToDesk:\n设备代码:111 222 333\n我的识别码:489360899"}
    assert table_db.parse_todesk_id(item) == "897832375"


def test_parse_todesk_id_remark_direct_number():
    """todesk_id 为空：解析「ToDesk:549 211 574」直接跟号（去空格）"""
    item = {"todesk_id": None, "remark": "ToDesk:295 534 145\n我的识别码:489457940"}
    assert table_db.parse_todesk_id(item) == "295534145"


def test_parse_todesk_id_remark_device_code():
    """todesk_id 为空且 ToDesk: 后无号：解析「设备代码:942 598 500（...）」"""
    item = {"todesk_id": None,
            "remark": "ToDesk:\n设备代码:942 598 500（该用户已设置禁止临时密码连接）"
                      "\n我的识别码:370085625\nsnk_35003"}
    assert table_db.parse_todesk_id(item) == "942598500"


def test_parse_todesk_id_never_matches_sunflower():
    """「我的识别码/向日葵」是向日葵的号，绝不误匹配为 ToDesk"""
    item = {"todesk_id": None,
            "remark": "向日葵识别码:448 240 371\n我的识别码:263034244\n向日葵：224 222 122"}
    assert table_db.parse_todesk_id(item) == ""


def test_parse_todesk_id_both_missing():
    """两者皆无（无 ToDesk 的设备）：返回空串"""
    assert table_db.parse_todesk_id({"todesk_id": None, "remark": "普通备注"}) == ""
    assert table_db.parse_todesk_id({}) == ""


def test_parse_todesk_id_todesk_direct_wins_over_device_code():
    """ToDesk: 直接跟号优先于设备代码（同为 ToDesk 口径时取直接号）"""
    item = {"todesk_id": None,
            "remark": "ToDesk:605 347 006\n设备代码:111 222 333\n向日葵识别码:448 240 371"}
    assert table_db.parse_todesk_id(item) == "605347006"


# ==================== parse_sunflower_id 单元（向日葵列） ====================

def test_parse_sunflower_id_from_identification_code():
    """「我的识别码:263034244」默认指向向日葵"""
    assert table_db.parse_sunflower_id(
        {"remark": "ToDesk:295 534 145\n我的识别码:489457940\n"}) == "489457940"


def test_parse_sunflower_id_from_sunflower_markers():
    """「向日葵识别码:xxx」与「向日葵：xxx」两种写法（明确标记优先）"""
    assert table_db.parse_sunflower_id(
        {"remark": "ToDesk:605 347 006\n向日葵识别码:448 240 371\n"}) == "448240371"
    assert table_db.parse_sunflower_id(
        {"remark": "向日葵：224 222 122"}) == "224222122"


def test_parse_sunflower_id_marker_wins_over_generic():
    """明确「向日葵」标记优先于泛化的「我的识别码」"""
    assert table_db.parse_sunflower_id(
        {"remark": "向日葵识别码:111 222 333\n我的识别码:444555666"}) == "111222333"


def test_parse_sunflower_id_empty_and_text_input():
    """无向日葵信息返回空串；支持纯文本入参"""
    assert table_db.parse_sunflower_id({"remark": "ToDesk:111 222 333\n设备代码:444"}) == ""
    assert table_db.parse_sunflower_id("") == ""
    assert table_db.parse_sunflower_id("我的识别码:263034244") == "263034244"


def test_todesk_and_sunflower_do_not_cross_match():
    """ToDesk 与向日葵解析互不串号（口径隔离回归）"""
    remark = "ToDesk:549 211 574\n设备代码:942 598 500\n向日葵识别码:448 240 371\n我的识别码:263034244"
    # ToDesk 取直接号；明确向日葵标记优先于「我的识别码」
    assert table_db.parse_todesk_id({"remark": remark}) == "549211574"
    assert table_db.parse_sunflower_id({"remark": remark}) == "448240371"


# ==================== 集成（临时库，隔离真实 tables.db） ====================

def _prep(db):
    """预建 billiard_tables + xqzg_status + sync_meta（隔离真实库）"""
    sl = sqlite3.connect(db)
    sl.executescript(schema.to_sqlite_ddl("billiard_tables"))
    # xqzg_status 按 save_xqzg 实际写入列预建（幂等起见直接用 schema DDL）
    sl.executescript(schema.to_sqlite_ddl("xqzg_status"))
    sl.execute("CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT)")
    sl.commit()
    sl.close()


def _isolate(monkeypatch, tmp_path):
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(table_db, "DB_PATH", db)
    monkeypatch.setattr(table_db, "_conn", None)               # 强制在 tmp 上重开
    monkeypatch.setattr(table_db, "_ensure_initialized", lambda c: None)  # 跳过建表/FTS
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)    # 走 SQLite 分支
    _prep(db)
    return db


def test_save_xqzg_persists_todesk_id(monkeypatch, tmp_path):
    """save_xqzg：todesk_id 字段直传 + remark 兜底均正确落库"""
    _isolate(monkeypatch, tmp_path)
    table_db.save_xqzg([
        {"table_id": "350-01", "device_code": "D1", "todesk_id": "897832375"},
        {"table_id": "350-03", "device_code": "D2", "todesk_id": None,
         "remark": "ToDesk:\n设备代码:942 598 500\n我的识别码:370085625"},
        {"table_id": "350-05", "device_code": "D3"},  # 无 ToDesk
    ], file_path="2026/09/20")
    conn = table_db._get_conn()
    rows = dict(conn.execute(
        "SELECT device_code, todesk_id FROM xqzg_status").fetchall())
    assert rows == {"D1": "897832375", "D2": "942598500", "D3": ""}


def test_query_page_enriches_todesk_id(monkeypatch, tmp_path):
    """query_page：按 code↔device_code 关联富集，取最新分区非空值"""
    _isolate(monkeypatch, tmp_path)
    # 08/19 旧分区：D1 有上报
    table_db.save_xqzg(
        [{"table_id": "350-01", "device_code": "D1", "todesk_id": "111111111"}],
        file_path="2026/08/19")
    # 08/20 新分区：D1 为空（未上报）、D2 首次上报
    table_db.save_xqzg([
        {"table_id": "350-01", "device_code": "D1", "todesk_id": None},
        {"table_id": "350-03", "device_code": "D2",
         "remark": "ToDesk:222 222 222\n我的识别码:999888777"},
    ], file_path="2026/08/20")
    # 球桌：code 对应 device_code；D3 无 xqzg 记录
    conn = table_db._get_conn()
    conn.execute(
        "INSERT INTO billiard_tables (id, name, code) VALUES "
        "(1, '350-01', 'D1'), (2, '350-03', 'D2'), (3, '350-05', 'D3')")
    conn.commit()

    _total, rows = table_db.query_page(1, 50)
    by_name = {r["name"]: r for r in rows}
    # D1：新分区为空回退旧分区非空值（最近一次上报）
    assert by_name["350-01"]["todesk_id"] == "111111111"
    # D2：remark 兜底解析落库后正常富集
    assert by_name["350-03"]["todesk_id"] == "222222222"
    # D3：无 xqzg 记录 → 空串（不影响行返回）
    assert by_name["350-05"]["todesk_id"] == ""


def test_query_page_tolerates_missing_xqzg_table(monkeypatch, tmp_path):
    """query_page：xqzg_status 表不存在（隔离/异常库）时静默空串不阻断"""
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(table_db, "DB_PATH", db)
    monkeypatch.setattr(table_db, "_conn", None)
    monkeypatch.setattr(table_db, "_ensure_initialized", lambda c: None)
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)
    sl = sqlite3.connect(db)
    sl.executescript(schema.to_sqlite_ddl("billiard_tables"))
    sl.execute("INSERT INTO billiard_tables (id, name, code) VALUES (1, 'T1', 'D1')")
    sl.commit()
    sl.close()

    _total, rows = table_db.query_page(1, 50)
    assert len(rows) == 1
    assert rows[0]["todesk_id"] == ""
def test_query_page_enriches_sunflower_from_remark(monkeypatch, tmp_path):
    """query_page：向日葵号从 billiard_tables.remark 直接解析"""
    _isolate(monkeypatch, tmp_path)
    conn = table_db._get_conn()
    conn.execute(
        "INSERT INTO billiard_tables (id, name, remark) VALUES "
        "(1, 'T1', 'ToDesk:549 211 574\\n我的识别码:263034244'), "
        "(2, 'T2', '向日葵：224 222 122'), "
        "(3, 'T3', '普通备注')")
    conn.commit()
    _t, rows = table_db.query_page(1, 50)
    by_name = {r["name"]: r for r in rows}
    assert by_name["T1"]["sunflower_id"] == "263034244"
    assert by_name["T2"]["sunflower_id"] == "224222122"
    assert by_name["T3"]["sunflower_id"] == ""
# ==================== todesk_status 落库与富集（2026-09-20 着色需求） ====================

def test_save_xqzg_persists_todesk_status(monkeypatch, tmp_path):
    """save_xqzg：todesk_status 归一化落库 True→'1' / False→'0' / None→''"""
    _isolate(monkeypatch, tmp_path)
    table_db.save_xqzg([
        {"table_id": "T1", "device_code": "D1", "todesk_status": True},
        {"table_id": "T2", "device_code": "D2", "todesk_status": False},
        {"table_id": "T3", "device_code": "D3", "todesk_status": None},
        {"table_id": "T4", "device_code": "D4"},  # 字段缺失
    ], file_path="2026/09/20")
    conn = table_db._get_conn()
    rows = dict(conn.execute(
        "SELECT device_code, todesk_status FROM xqzg_status").fetchall())
    assert rows == {"D1": "1", "D2": "0", "D3": "", "D4": ""}


def test_query_page_enriches_todesk_status(monkeypatch, tmp_path):
    """query_page：todesk_status 随 todesk_id 一并富集（开启设备着色依据）"""
    _isolate(monkeypatch, tmp_path)
    table_db.save_xqzg([
        {"table_id": "T1", "device_code": "D1",
         "todesk_id": "111111111", "todesk_status": True},
        {"table_id": "T2", "device_code": "D2", "todesk_id": None,
         "remark": "ToDesk:222 222 222"},  # 有号无状态
    ], file_path="2026/09/20")
    conn = table_db._get_conn()
    conn.execute(
        "INSERT INTO billiard_tables (id, name, code) VALUES "
        "(1, 'T1', 'D1'), (2, 'T2', 'D2')")
    conn.commit()
    _t, rows = table_db.query_page(1, 50)
    by_name = {r["name"]: r for r in rows}
    assert by_name["T1"]["todesk_id"] == "111111111"
    assert by_name["T1"]["todesk_status"] == "1"
    assert by_name["T2"]["todesk_id"] == "222222222"
    assert by_name["T2"]["todesk_status"] == ""


# ==================== 开关指令回写与 Worker 归一化（2026-09-20 开关需求） ====================

def test_update_todesk_status_writes_latest_partition(monkeypatch, tmp_path):
    """update_todesk_status：回写该设备最新分区行的 todesk_action（20/80 指令口径）"""
    _isolate(monkeypatch, tmp_path)
    table_db.save_xqzg(
        [{"table_id": "T1", "device_code": "D1", "todesk_status": False}],
        file_path="2026/09/19")
    table_db.save_xqzg(
        [{"table_id": "T1", "device_code": "D1", "todesk_status": False}],
        file_path="2026/09/20")

    affected = table_db.update_todesk_status("D1", "1")
    assert affected == 1
    conn = table_db._get_conn()
    rows = dict(conn.execute(
        "SELECT file_path, todesk_action FROM xqzg_status "
        "WHERE device_code = 'D1'").fetchall())
    # 只动最新分区，旧分区快照保持原样（历史不可变）；'1' → action='20'
    assert rows == {"2026/09/19": "", "2026/09/20": "20"}


def test_update_todesk_status_normalizes_and_missing_device(monkeypatch, tmp_path):
    """update_todesk_status：True/False/'0' 归一化；未知设备返回 0 不抛错"""
    _isolate(monkeypatch, tmp_path)
    table_db.save_xqzg(
        [{"table_id": "T1", "device_code": "D1", "todesk_status": None}],
        file_path="2026/09/20")
    assert table_db.update_todesk_status("D1", True) == 1
    conn = table_db._get_conn()
    val = conn.execute(
        "SELECT todesk_action FROM xqzg_status WHERE device_code = 'D1'"
    ).fetchone()[0]
    # True → '20'（指令口径，非 status 口径的 '1'）
    assert val == "20"
    # 未知设备 / 空设备码：0 且不抛
    assert table_db.update_todesk_status("NOPE", "1") == 0
    assert table_db.update_todesk_status("", "1") == 0


def test_todesk_toggle_worker_norm_status():
    """TodeskToggleWorker._norm_status 与 table_db 同口径（bool/字符串/未知值）"""
    from workers.table_worker import TodeskToggleWorker
    norm = TodeskToggleWorker._norm_status
    assert norm(True) == "1"
    assert norm(False) == "0"
    assert norm("1") == "1"
    assert norm("true") == "1"
    assert norm("True") == "1"
    assert norm("0") == "0"
    assert norm("false") == "0"
    assert norm(None) == ""
    assert norm("") == ""
    # 未知值原样透传（轮询时与期望值比较自然不匹配，不会误判确认）
    assert norm("2") == "2"


def test_todesk_toggle_worker_defaults(monkeypatch):
    """TodeskToggleWorker：轮询参数默认 10s × 12 次，配置可覆盖且异常值兜底"""
    import workers.table_worker as tw

    monkeypatch.setattr(tw, "_load_api_credentials", lambda: {"api1": {}})
    monkeypatch.setattr(tw, "_load_toggle_config",
                        lambda: {"poll_interval": 5, "max_polls": 3})
    w = tw.TodeskToggleWorker("D1", "T1", True)
    assert w.poll_interval == 5.0
    assert w.max_polls == 3
    assert w.device_code == "D1"
    assert w.turn_on is True

    # 配置非法值 → 回落默认
    monkeypatch.setattr(tw, "_load_toggle_config",
                        lambda: {"poll_interval": "x", "max_polls": None})
    w2 = tw.TodeskToggleWorker("D1", "T1", False)
    assert w2.poll_interval == 10.0
    assert w2.max_polls == 12

    # interval 下限 0.5s 防误配死循环打接口
    monkeypatch.setattr(tw, "_load_toggle_config",
                        lambda: {"poll_interval": 0, "max_polls": -1})
    w3 = tw.TodeskToggleWorker("D1", "T1", True)
    assert w3.poll_interval == 0.5
    assert w3.max_polls == 1


# ==================== value/ 响应解析与实时行匹配（2026-09-20 真机修正） ====================

def test_parse_value_response_nested_errorcode():
    """成功响应 errorcode 嵌在 data 里（真机实测：顶层无该键）"""
    from workers.table_worker import parse_value_response
    ok, err = parse_value_response({
        "code": 200, "msg": "提交成功",
        "data": {"data_type": "action", "deviceID": "DEV1",
                 "datavalue": "80", "pushStatus": "SENT",
                 "pushMessage": "WebSocket 指令已发送", "errorcode": 0}})
    assert ok is True and err == ""
    # errorcode 字符串 "0" 同样判成功
    ok, _ = parse_value_response({"data": {"errorcode": "0"}})
    assert ok is True


def test_parse_value_response_failure_and_fallback():
    """失败响应 data.errorcode=-1 + errortext；顶层兼容与非 dict 兜底"""
    from workers.table_worker import parse_value_response
    # 真机实测：datacode 传 todesk_id 报「该设备号没找到」（HTTP 仍 200）
    ok, err = parse_value_response({
        "code": 200, "msg": "提交成功",
        "data": {"datacode": "897832375", "datavalue": "80",
                 "errorcode": -1, "errortext": "该设备号没找到",
                 "pushMessage": "该设备号没找到"}})
    assert ok is False
    assert "该设备号没找到" in err
    # 顶层 errorcode 兼容（防服务端改版回退）
    ok, _ = parse_value_response({"errorcode": 0})
    assert ok is True
    ok, err = parse_value_response({"errorcode": -2, "errortext": "其他错"})
    assert ok is False and "其他错" in err
    # 非 dict 兜底
    ok, _ = parse_value_response("oops")
    assert ok is False


def test_pick_todesk_status_by_table_id_and_code():
    """实时行匹配：keyword 缩量后按 table_id 精确匹配，兜底设备编码"""
    from workers.table_worker import pick_todesk_status
    rows = [
        {"table_id": "49-40", "device_code": "AAA", "todesk_status": True},
        {"table_id": "49-04", "device_code": "BBB", "todesk_status": False},
    ]
    assert pick_todesk_status(rows, "49-04") is False
    assert pick_todesk_status(rows, "49-40") is True
    # table_id 未命中（如球桌号在 xqzg 侧不同名）→ 设备编码兜底
    assert pick_todesk_status(rows, "nope", "aaa") is True
    # 都未命中 → None（轮询继续等，不误判）
    assert pick_todesk_status(rows, "nope", "zzz") is None
    assert pick_todesk_status([], "49-04", "BBB") is None
    assert pick_todesk_status(None, "49-04") is None


# ==================== 显示口径 todesk_action（2026-09-21 网页 JS 逆向对齐） ====================
# 网页端开关状态读 todesk_action（服务端记录的最后一次指令值，20=开 80=关，
# 下发即记录不依赖设备上报）；todesk_status 依赖上报且关闭不上报（恒 true）。

def test_save_xqzg_persists_todesk_action(monkeypatch, tmp_path):
    """save_xqzg：todesk_action 原样落库（'20'/'80'/None→''）"""
    _isolate(monkeypatch, tmp_path)
    table_db.save_xqzg([
        {"table_id": "T1", "device_code": "D1", "todesk_action": 20},
        {"table_id": "T2", "device_code": "D2", "todesk_action": "80"},
        {"table_id": "T3", "device_code": "D3", "todesk_action": None},
    ], file_path="2026/09/21")
    conn = table_db._get_conn()
    rows = dict(conn.execute(
        "SELECT device_code, todesk_action FROM xqzg_status").fetchall())
    assert rows == {"D1": "20", "D2": "80", "D3": ""}


def test_query_page_display_uses_todesk_action(monkeypatch, tmp_path):
    """query_page：显示口径 todesk_action 优先（'20'→'1'，'80'→'0'），
    无 action 时回退 todesk_status"""
    _isolate(monkeypatch, tmp_path)
    table_db.save_xqzg([
        # 实际关闭但服务端 status 恒 true 的设备：action=80 → 显示 '0'
        {"table_id": "T1", "device_code": "D1", "todesk_id": "111",
         "todesk_status": True, "todesk_action": 80},
        # 开启设备：action=20 → 显示 '1'
        {"table_id": "T2", "device_code": "D2", "todesk_id": "222",
         "todesk_status": True, "todesk_action": "20"},
        # 无 action（从未下发过指令）：回退 todesk_status
        {"table_id": "T3", "device_code": "D3", "todesk_id": "333",
         "todesk_status": True},
        # 全空：空串
        {"table_id": "T4", "device_code": "D4", "todesk_id": "444"},
    ], file_path="2026/09/21")
    conn = table_db._get_conn()
    conn.execute(
        "INSERT INTO billiard_tables (id, name, code) VALUES "
        "(1, 'T1', 'D1'), (2, 'T2', 'D2'), (3, 'T3', 'D3'), (4, 'T4', 'D4')")
    conn.commit()
    _t, rows = table_db.query_page(1, 50)
    by_name = {r["name"]: r for r in rows}
    assert by_name["T1"]["todesk_status"] == "0"   # action=80 压过 status=true
    assert by_name["T2"]["todesk_status"] == "1"
    assert by_name["T3"]["todesk_status"] == "1"   # 无 action 回退 status
    assert by_name["T4"]["todesk_status"] == ""


def test_update_todesk_status_writes_action(monkeypatch, tmp_path):
    """update_todesk_status：确认状态映射回指令值写 todesk_action 列"""
    _isolate(monkeypatch, tmp_path)
    table_db.save_xqzg(
        [{"table_id": "T1", "device_code": "D1"}], file_path="2026/09/21")
    assert table_db.update_todesk_status("D1", "0") == 1
    conn = table_db._get_conn()
    val = conn.execute(
        "SELECT todesk_action FROM xqzg_status WHERE device_code='D1'"
    ).fetchone()[0]
    assert val == "80"
    assert table_db.update_todesk_status("D1", "1") == 1
    val = conn.execute(
        "SELECT todesk_action FROM xqzg_status WHERE device_code='D1'"
    ).fetchone()[0]
    assert val == "20"
    # 未知设备 / 非法状态：0 且不抛
    assert table_db.update_todesk_status("NOPE", "1") == 0
    assert table_db.update_todesk_status("", "1") == 0
