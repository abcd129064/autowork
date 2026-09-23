# -*- coding: utf-8 -*-
"""售后排行数据层（query_rank）回归测试

覆盖球房/球桌售后排行接口（与 Web 端 /api/stats/rank 同口径）：
- room 级：GROUP BY TRIM(room_name) 聚合（总量/未解决/我方问题/主动发起/
  最近发生），排除未填球房；share 按 summary.total 整数百分比
- table 级：球房+桌号联合分组，联合名「球房 · 桌号」，排除未填桌号
- 排序白名单：total/unresolved/our_problem/last_occurred + limit TOP N
- 筛选：start~end 日期区间（occurred_at 优先回退 created_at）、账期
  （cycle_start 物化列）、region/room_name（TRIM 精确，下钻）、keyword、
  resolved
- summary 同 WHERE 全量口径（total/unresolved/rooms/tables），
  rooms/tables 排除空值且不受 TOP N 截断影响
- 软删除隔离（deleted=1 不可见）；非法 level 抛 ValueError

隔离方式同 test_aftersale_stats：tmp SQLite + monkeypatch，不触碰真实
tables.db；周期模式固定为周二起保证归属确定性。
"""
import sqlite3

import pytest

import database.backend as backend
import database.table_db as table_db
import database.aftersale_db as adb
from database import schema

TUE = {"type": "tue", "start": "", "span": 7}


@pytest.fixture
def db(monkeypatch, tmp_path):
    """临时库：建 aftersale_records 表 + 固定周期模式，隔离 settings.json"""
    path = str(tmp_path / "t.db")
    monkeypatch.setattr(table_db, "DB_PATH", path)
    monkeypatch.setattr(table_db, "_conn", None)
    monkeypatch.setattr(table_db, "_ensure_initialized", lambda c: None)
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)
    monkeypatch.setattr(adb, "load_cycle_mode", lambda: dict(TUE))
    sl = sqlite3.connect(path)
    sl.executescript(schema.to_sqlite_ddl("aftersale_records"))
    sl.commit()
    sl.close()
    return path


def _insert(db, rows):
    """批量插入售后记录。rows: [{occurred_at, room_name, table_no, region,
    resolved, is_our_problem, is_initiative, deleted, ...}]"""
    sl = sqlite3.connect(db)
    for r in rows:
        sl.execute(
            "INSERT INTO aftersale_records "
            "(created_at, occurred_at, creator, issue_type, table_no, "
            "room_name, region, problem, cause, resolved, is_initiative, "
            "is_our_problem, solution, resolver, response_time, snk_code, "
            "device_code, cycle_start, deleted) "
            "VALUES (?, ?, 'tester', '硬件问题', ?, ?, ?, '问题X', '', ?, "
            "?, ?, '', '', '', '', '', '', ?)",
            (r.get("created_at")
             or (r.get("occurred_at") or "2026-08-20") + " 10:00:00",
             r.get("occurred_at")
             or (r.get("created_at") or "2026-08-20 10:00:00")[:10],
             r.get("table_no", ""),
             r.get("room_name", ""),
             r.get("region", ""),
             r.get("resolved", "否"),
             r.get("is_initiative", "否"),
             r.get("is_our_problem", "否"),
             int(r.get("deleted", 0))))
    sl.commit()
    sl.close()


# ==================== room 级基本聚合 ====================

def test_rank_room_basic(db):
    """room 级：按球房聚合排序 + share + summary 全量口径"""
    _insert(db, [
        {"occurred_at": "2026-08-18", "room_name": "甲球房", "resolved": "是",
         "is_our_problem": "是", "is_initiative": "是"},
        {"occurred_at": "2026-08-19", "room_name": "甲球房", "resolved": "否",
         "is_our_problem": "是"},
        {"occurred_at": "2026-08-20", "room_name": "乙球房", "resolved": "否"},
        {"occurred_at": "2026-08-20", "room_name": "乙球房",
         "resolved": "是"},
    ])
    r = adb.query_rank(level="room")
    assert [x["name"] for x in r["rows"]] == ["乙球房", "甲球房"]
    top = r["rows"][0]
    assert (top["rank"], top["total"], top["share"]) == (1, 2, 50)
    assert top["unresolved"] == 1 and top["our_problem"] == 0
    assert top["last_occurred"] == "2026-08-20"
    jia = r["rows"][1]
    assert jia["unresolved"] == 1 and jia["our_problem"] == 2
    assert jia["initiative"] == 1 and jia["last_occurred"] == "2026-08-19"
    s = r["summary"]
    assert s == {"total": 4, "unresolved": 2, "rooms": 2, "tables": 0}


def test_rank_room_excludes_empty_room(db):
    """未填球房的记录不参与排行（但计入 summary.total）"""
    _insert(db, [
        {"occurred_at": "2026-08-18", "room_name": "甲球房"},
        {"occurred_at": "2026-08-18", "room_name": ""},
        {"occurred_at": "2026-08-18", "room_name": "   "},
    ])
    r = adb.query_rank(level="room")
    assert [x["name"] for x in r["rows"]] == ["甲球房"]
    assert r["rows"][0]["total"] == 1
    assert r["summary"]["total"] == 3      # summary 反映全量口径
    assert r["summary"]["rooms"] == 1


def test_rank_room_trims_whitespace_grouping(db):
    """球房名首尾空白 TRIM 后合并分组"""
    _insert(db, [
        {"occurred_at": "2026-08-18", "room_name": "甲球房"},
        {"occurred_at": "2026-08-18", "room_name": " 甲球房 "},
    ])
    r = adb.query_rank(level="room")
    assert len(r["rows"]) == 1 and r["rows"][0]["total"] == 2


# ==================== table 级 ====================

def test_rank_table_joint_name_and_exclude_empty(db):
    """table 级：球房+桌号联合分组，联合名「球房 · 桌号」，未填桌号排除"""
    _insert(db, [
        {"occurred_at": "2026-08-18", "room_name": "甲球房", "table_no": "03"},
        {"occurred_at": "2026-08-18", "room_name": "甲球房", "table_no": "03"},
        {"occurred_at": "2026-08-19", "room_name": "甲球房", "table_no": "01"},
        {"occurred_at": "2026-08-19", "room_name": "乙球房", "table_no": "02"},
        {"occurred_at": "2026-08-19", "room_name": "乙球房", "table_no": ""},
    ])
    r = adb.query_rank(level="table")
    assert [x["name"] for x in r["rows"]] == [
        "甲球房 · 03", "乙球房 · 02", "甲球房 · 01"]
    assert r["rows"][0]["room_name"] == "甲球房"
    assert r["rows"][0]["table_no"] == "03"
    assert r["summary"]["tables"] == 3
    assert r["summary"]["rooms"] == 2


def test_rank_table_cross_room_same_table_no(db):
    """跨球房同名桌号分列（联合键分组，不合并）"""
    _insert(db, [
        {"occurred_at": "2026-08-18", "room_name": "甲球房", "table_no": "01"},
        {"occurred_at": "2026-08-18", "room_name": "乙球房", "table_no": "01"},
    ])
    r = adb.query_rank(level="table")
    assert sorted(x["name"] for x in r["rows"]) == [
        "乙球房 · 01", "甲球房 · 01"]


# ==================== 排序与 TOP N ====================

def test_rank_sort_unresolved(db):
    _insert(db, [
        {"occurred_at": "2026-08-18", "room_name": "甲", "resolved": "否"},
        {"occurred_at": "2026-08-18", "room_name": "甲", "resolved": "是"},
        {"occurred_at": "2026-08-18", "room_name": "乙", "resolved": "否"},
        {"occurred_at": "2026-08-18", "room_name": "乙", "resolved": "否"},
        {"occurred_at": "2026-08-18", "room_name": "丙", "resolved": "是"},
    ])
    r = adb.query_rank(level="room", sort="unresolved")
    assert [x["name"] for x in r["rows"]] == ["乙", "甲", "丙"]
    assert [x["unresolved"] for x in r["rows"]] == [2, 1, 0]


def test_rank_sort_our_problem(db):
    _insert(db, [
        {"occurred_at": "2026-08-18", "room_name": "甲",
         "is_our_problem": "是"},
        {"occurred_at": "2026-08-18", "room_name": "乙",
         "is_our_problem": "是"},
        {"occurred_at": "2026-08-18", "room_name": "乙",
         "is_our_problem": "是"},
    ])
    r = adb.query_rank(level="room", sort="our_problem")
    assert [x["name"] for x in r["rows"]] == ["乙", "甲"]


def test_rank_sort_last_occurred(db):
    _insert(db, [
        {"occurred_at": "2026-08-10", "room_name": "甲"},
        {"occurred_at": "2026-08-20", "room_name": "乙"},
        {"occurred_at": "2026-08-15", "room_name": "丙"},
    ])
    r = adb.query_rank(level="room", sort="last_occurred")
    assert [x["name"] for x in r["rows"]] == ["乙", "丙", "甲"]


def test_rank_limit(db):
    for i in range(15):
        _insert(db, [{"occurred_at": "2026-08-18",
                      "room_name": f"球房{i:02d}"}])
    r = adb.query_rank(level="room", limit=10)
    assert len(r["rows"]) == 10
    assert r["summary"]["rooms"] == 15   # summary 不受 TOP N 截断


def test_rank_invalid_level_raises(db):
    with pytest.raises(ValueError):
        adb.query_rank(level="bad")


def test_rank_invalid_sort_falls_back_to_total(db):
    _insert(db, [{"occurred_at": "2026-08-18", "room_name": "甲"}])
    r = adb.query_rank(level="room", sort="not-a-key")
    assert len(r["rows"]) == 1   # 回退 total 排序而非报错


# ==================== 时间筛选 ====================

def test_rank_date_range(db):
    _insert(db, [
        {"occurred_at": "2026-08-01", "room_name": "甲"},
        {"occurred_at": "2026-08-15", "room_name": "乙"},
        {"occurred_at": "2026-08-31", "room_name": "丙"},
    ])
    r = adb.query_rank(level="room", start="2026-08-10", end="2026-08-20")
    assert [x["name"] for x in r["rows"]] == ["乙"]
    assert r["summary"]["total"] == 1


def test_rank_date_range_falls_back_to_created_at(db):
    """occurred_at 缺失时日期区间按 created_at 前 10 位判定"""
    _insert(db, [
        {"created_at": "2026-08-15 09:30:00", "room_name": "甲"},
        {"created_at": "2026-09-01 09:30:00", "room_name": "乙"},
    ])
    r = adb.query_rank(level="room", start="2026-08-01", end="2026-08-31")
    assert [x["name"] for x in r["rows"]] == ["甲"]


def test_rank_cycle_filter(db):
    """tue 模式（2026-08-18 周二起）：08-18~08-24 属周期 2026/08/18"""
    _insert(db, [
        {"occurred_at": "2026-08-20", "room_name": "甲"},
        {"occurred_at": "2026-08-25", "room_name": "乙"},
    ])
    r = adb.query_rank(level="room", cycle_start="2026/08/18")
    assert [x["name"] for x in r["rows"]] == ["甲"]


# ==================== 其他筛选（region/下钻/keyword/resolved） ====================

def test_rank_region_filter(db):
    _insert(db, [
        {"occurred_at": "2026-08-18", "room_name": "甲", "region": "广东"},
        {"occurred_at": "2026-08-18", "room_name": "乙", "region": "上海"},
    ])
    r = adb.query_rank(level="room", region="广东")
    assert [x["name"] for x in r["rows"]] == ["甲"]


def test_rank_drill_room_name(db):
    """下钻：room_name 精确过滤（球房级=该球房单行；table 级=该球房各桌）"""
    _insert(db, [
        {"occurred_at": "2026-08-18", "room_name": "甲球房", "table_no": "01"},
        {"occurred_at": "2026-08-18", "room_name": "甲球房", "table_no": "02"},
        {"occurred_at": "2026-08-18", "room_name": "乙球房", "table_no": "01"},
    ])
    r = adb.query_rank(level="room", room_name="甲球房")
    assert [x["name"] for x in r["rows"]] == ["甲球房"]
    assert r["summary"]["total"] == 2
    t = adb.query_rank(level="table", room_name="甲球房")
    assert sorted(x["name"] for x in t["rows"]) == [
        "甲球房 · 01", "甲球房 · 02"]


def test_rank_keyword(db):
    _insert(db, [
        {"occurred_at": "2026-08-18", "room_name": "丁俊晖宜兴店",
         "table_no": "03"},
        {"occurred_at": "2026-08-18", "room_name": "乙球房",
         "table_no": "05"},
    ])
    r = adb.query_rank(level="room", keyword="宜兴")
    assert [x["name"] for x in r["rows"]] == ["丁俊晖宜兴店"]


def test_rank_resolved_filter(db):
    _insert(db, [
        {"occurred_at": "2026-08-18", "room_name": "甲", "resolved": "是"},
        {"occurred_at": "2026-08-18", "room_name": "甲", "resolved": "否"},
        {"occurred_at": "2026-08-18", "room_name": "乙", "resolved": "否"},
    ])
    r = adb.query_rank(level="room", resolved="否")
    assert [x["name"] for x in r["rows"]] == ["乙", "甲"]
    assert r["rows"][0]["total"] == 1 and r["rows"][1]["total"] == 1


# ==================== 边界 ====================

def test_rank_empty_table(db):
    r = adb.query_rank()
    assert r["rows"] == []
    assert r["summary"] == {"total": 0, "unresolved": 0,
                            "rooms": 0, "tables": 0}


def test_rank_deleted_isolated(db):
    """软删除（deleted=1，Web 回收站）记录对排行不可见"""
    _insert(db, [
        {"occurred_at": "2026-08-18", "room_name": "甲"},
        {"occurred_at": "2026-08-18", "room_name": "甲", "deleted": 1},
    ])
    r = adb.query_rank(level="room")
    assert r["rows"][0]["total"] == 1
    assert r["summary"]["total"] == 1


def test_rank_share_percentage_rounding(db):
    """share = 组总量 ×100 ÷ summary.total 四舍五入整数"""
    _insert(db, [
        {"occurred_at": "2026-08-18", "room_name": "甲"},
        {"occurred_at": "2026-08-18", "room_name": "甲"},
        {"occurred_at": "2026-08-18", "room_name": "甲"},
        {"occurred_at": "2026-08-18", "room_name": "乙"},
    ])
    r = adb.query_rank(level="room")
    by_name = {x["name"]: x for x in r["rows"]}
    assert by_name["甲"]["share"] == 75
    assert by_name["乙"]["share"] == 25
