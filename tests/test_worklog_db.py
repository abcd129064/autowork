# -*- coding: utf-8 -*-
"""工作留痕数据层（database/worklog_db.py）回归测试

覆盖：周期区间换算、成员增改与停用、记录增改与软删除、进度步进联动状态、
六类筛选、四个聚合口径（总量/按天/堆叠/分类/成员汇总）、分类配置读写、
文字报告与 xlsx 导出。

隔离方式同 test_ledger_db：tmp SQLite + monkeypatch，不触碰真实 tables.db；
MySQL 测试开关强制关闭走 SQLite 方言。分类落 config JSON，故配置类用例
另需 app_paths 隔离，否则会写到真实 config/misc.json。
"""
import sqlite3
from datetime import date

import pytest

import database.backend as backend
import database.table_db as table_db
import database.worklog_db as wdb
from database import schema


@pytest.fixture
def db(monkeypatch, tmp_path):
    """临时库：建工作留痕两张表，关闭 MySQL 测试开关"""
    path = str(tmp_path / "t.db")
    monkeypatch.setattr(table_db, "DB_PATH", path)
    monkeypatch.setattr(table_db, "_conn", None)
    monkeypatch.setattr(table_db, "_ensure_initialized", lambda c: None)
    # 真实 config/database.json 里 MySQL enabled=true 指向生产库，
    # 不打这个补丁，下面的写用例会直接落到生产
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)
    sl = sqlite3.connect(path)
    for table in schema.WORKLOG_TABLES:
        sl.executescript(schema.to_sqlite_ddl(table))
    sl.commit()
    sl.close()
    return path


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    """app_settings 隔离（分类清单落 tmp_path/config/misc.json）"""
    import core.app_paths
    import core.app_settings as fas
    monkeypatch.setattr(core.app_paths, "get_app_dir", lambda: str(tmp_path))
    fas.invalidate_cache()
    fas._migrated = False
    yield
    fas.invalidate_cache()
    fas._migrated = False


def _rec(**kw):
    """一条完整记录（数值口径照抄设计稿 11-06 张工那一行）"""
    base = {"work_date": "2026-11-06", "member_id": 1, "title": "接口联调",
            "project": "AutoWork", "category": "开发", "hours": 3.0,
            "progress": 100, "status": "已完成", "note": ""}
    base.update(kw)
    return base


def _seed():
    """三成员 + 跨两天的六条记录，聚合类用例共用这份数据

    11-06：张工 3.0h 已完成 / 李工 1.5h 进行中 / 王工 1.5h 进行中
    11-05：张工 4.0h 已完成 / 赵工(停用) 1.0h 已完成 / 李工 2.0h 已完成
    """
    ids = [wdb.insert_member("张工", "zhang", sort=1),
           wdb.insert_member("李工", "li", sort=2),
           wdb.insert_member("王工", "wang", sort=3),
           wdb.insert_member("赵工", "zhao", sort=4, active=0)]
    wdb.insert_record(_rec(member_id=ids[0]))
    wdb.insert_record(_rec(member_id=ids[1], title="frp 隧道巡检",
                           project="运维", category="运维", hours=1.5,
                           progress=60, status="进行中"))
    wdb.insert_record(_rec(member_id=ids[2], title="整理 SOP 文档",
                           category="文档", hours=1.5, progress=40,
                           status="进行中"))
    wdb.insert_record(_rec(work_date="2026-11-05", member_id=ids[0],
                           title="分页性能优化", hours=4.0))
    wdb.insert_record(_rec(work_date="2026-11-05", member_id=ids[3],
                           title="门店配置答疑", project="售后支持",
                           category="沟通", hours=1.0))
    wdb.insert_record(_rec(work_date="2026-11-05", member_id=ids[1],
                           title="回归用例补齐", hours=2.0))
    return ids


# ==================== 周期区间 ====================

def test_day_range_is_single_day():
    assert wdb.day_range("2026-11-06") == ("2026-11-06", "2026-11-06")


def test_week_range_starts_monday():
    """2026-11-06 是周五，自然周应为 11-02(周一) ~ 11-08(周日)"""
    assert wdb.week_range("2026-11-06") == ("2026-11-02", "2026-11-08")
    # 周一自身与周日自身都落在同一周
    assert wdb.week_range("2026-11-02") == ("2026-11-02", "2026-11-08")
    assert wdb.week_range("2026-11-08") == ("2026-11-02", "2026-11-08")


def test_month_range_crosses_year():
    assert wdb.month_range("2026-11-06") == ("2026-11-01", "2026-11-30")
    assert wdb.month_range("2026-12-15") == ("2026-12-01", "2026-12-31")
    assert wdb.month_range("2024-02-05") == ("2024-02-01", "2024-02-29")


def test_period_range_unknown_key_falls_back_to_day():
    """未知周期键退回按天：条件为空会打全表，比退回错区间更危险"""
    assert wdb.period_range("year", "2026-11-06") == wdb.day_range("2026-11-06")


def test_bad_anchor_falls_back_to_today():
    today = date.today().isoformat()
    assert wdb.day_range("") == (today, today)
    assert wdb.day_range("不是日期") == (today, today)


def test_recent_days_ascending_includes_anchor():
    days = wdb.recent_days("2026-11-06", 7)
    assert days[-1] == "2026-11-06"
    assert days[0] == "2026-10-31"
    assert days == sorted(days)


# ==================== 建表接线 ====================

def test_table_db_create_script_covers_both_tables():
    """新建本地库时两张表都要建出来：漏一张的话界面报 no such table

    SQLite 侧走 _CREATE_WORKLOG_SQL 常量（已挂进 _ensure_initialized 的
    executescript 序列），MySQL 侧走 backend.MYSQL_DDL 自动派生。
    """
    ddl = table_db._CREATE_WORKLOG_SQL
    for table in schema.WORKLOG_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl
    assert "idx_worklog_date_deleted" in ddl


# ==================== 成员 ====================

def test_list_members_orders_by_sort(db):
    _seed()
    names = [m["name"] for m in wdb.list_members()]
    assert names == ["张工", "李工", "王工", "赵工"]


def test_active_only_excludes_disabled(db):
    _seed()
    names = [m["name"] for m in wdb.list_members(active_only=True)]
    assert names == ["张工", "李工", "王工"]


def test_ensure_member_reuses_and_creates(db):
    first = wdb.ensure_member("张工")
    assert first > 0
    # 同名再来一次必须复用，否则录入下拉会出现两个「张工」
    assert wdb.ensure_member("张工") == first
    assert wdb.ensure_member("新同事") != first
    assert wdb.ensure_member("  ") == 0


def test_update_member_ignores_unknown_keys(db):
    mid = wdb.insert_member("张工", "zhang", sort=1)
    assert wdb.update_member(mid, {"alias": "老张", "id": 999, "hack": 1})
    row = wdb.get_member_by_name("张工")
    assert row["alias"] == "老张"
    assert row["id"] == mid          # id 不在白名单，改不动


def test_set_member_active_keeps_records(db):
    """停用 ≠ 删除：记录仍查得到、成员名仍在"""
    ids = _seed()
    assert wdb.set_member_active(ids[3], False) is True
    rows = wdb.query_records(member_id=ids[3])
    assert len(rows) == 1
    assert rows[0]["member_name"] == "赵工"
    assert wdb.set_member_active(ids[3], True) is True
    assert ids[3] in {m["id"] for m in wdb.list_members(active_only=True)}


def test_member_map_keys_are_ids(db):
    ids = _seed()
    mm = wdb.member_map()
    assert set(mm) == set(ids)
    assert mm[ids[0]]["name"] == "张工"


# ==================== 记录增删改 ====================

def test_insert_requires_title(db):
    assert wdb.insert_record(_rec(title="")) == 0
    assert wdb.insert_record(_rec(title="   ")) == 0


def test_insert_fills_timestamps_and_defaults(db):
    rid = wdb.insert_record({"title": "只填内容"})
    row = wdb.get_record(rid)
    assert row["created_at"] == row["updated_at"]
    assert row["created_at"] != ""
    assert row["work_date"] == date.today().isoformat()
    assert row["status"] == "进行中"
    # deleted 不出现在明细行里：查询侧只认 deleted = 0，不必再回传这一列
    assert "deleted" not in row


def test_normalize_clamps_hours_and_progress(db):
    rid = wdb.insert_record(_rec(hours=99, progress=250))
    row = wdb.get_record(rid)
    assert row["hours"] == wdb.HOURS_MAX
    assert row["progress"] == 100
    rid2 = wdb.insert_record(_rec(title="负值", hours=-3, progress=-20))
    row2 = wdb.get_record(rid2)
    assert row2["hours"] == 0.0
    assert row2["progress"] == 0


def test_progress_full_forces_done_status(db):
    """稿 668：进度拉到 100% 自动置「已完成」，即使传的是别的状态"""
    rid = wdb.insert_record(_rec(progress=60, status="进行中"))
    assert wdb.get_record(rid)["status"] == "进行中"
    wdb.update_record(rid, {"progress": 100})
    assert wdb.get_record(rid)["status"] == "已完成"


def test_partial_update_keeps_other_fields(db):
    """只改进度时不得顺手冲掉日期/状态/工时（normalize 只动传入键）"""
    rid = wdb.insert_record(_rec(work_date="2026-11-05", hours=4.0,
                                 progress=50, status="进行中"))
    assert wdb.update_record(rid, {"progress": 80})
    row = wdb.get_record(rid)
    assert row["work_date"] == "2026-11-05"
    assert row["hours"] == 4.0
    assert row["status"] == "进行中"
    assert row["progress"] == 80


def test_update_ignores_timestamp_fields(db):
    rid = wdb.insert_record(_rec())
    before = wdb.get_record(rid)["created_at"]
    wdb.update_record(rid, {"title": "改了标题",
                            "created_at": "1999-01-01 00:00:00"})
    row = wdb.get_record(rid)
    assert row["title"] == "改了标题"
    assert row["created_at"] == before      # created_at 不在白名单


def test_delete_is_soft(db):
    """软删除：库里还在，列表与统计都看不见"""
    rid = wdb.insert_record(_rec())
    assert wdb.delete_record(rid) is True
    assert wdb.delete_record(rid) is False   # 再删一次已无目标
    assert wdb.get_record(rid) is None
    assert wdb.query_records() == []
    # 行还在库里，只是 deleted 置 1：误删可以从库侧捞回
    row = table_db.get_conn().execute(
        "SELECT deleted FROM worklog_records WHERE id = ?", (rid,)).fetchone()
    assert row[0] == 1


def test_set_progress_returns_row_and_flips_status(db):
    rid = wdb.insert_record(_rec(progress=55, status="进行中"))
    row = wdb.set_progress(rid, 60)
    assert row["progress"] == 60 and row["status"] == "进行中"
    row = wdb.set_progress(rid, 100)
    assert row["progress"] == 100 and row["status"] == "已完成"
    # 越界入参钳到 0~100，不让滚轮连滚出负数
    assert wdb.set_progress(rid, -50)["progress"] == 0
    assert wdb.set_progress(999999, 50) is None


# ==================== 筛选 ====================

def test_query_by_date_range(db):
    _seed()
    assert len(wdb.query_records("2026-11-06", "2026-11-06")) == 3
    assert len(wdb.query_records("2026-11-05", "2026-11-05")) == 3
    assert len(wdb.query_records("2026-11-01", "2026-11-30")) == 6
    # 开区间：只给起点等于「从这天往后全部」
    assert len(wdb.query_records(date_from="2026-11-06")) == 3


def test_query_by_member_category_status(db):
    ids = _seed()
    assert len(wdb.query_records(member_id=ids[0])) == 2
    assert len(wdb.query_records(category="运维")) == 1
    assert len(wdb.query_records(status="进行中")) == 2
    assert len(wdb.query_records(status="已完成")) == 4


def test_query_keyword_covers_title_project_note(db):
    _seed()
    wdb.insert_record(_rec(title="无关", project="无关", note="隧道抖动记录"))
    assert len(wdb.query_records(keyword="隧道")) == 2   # 标题 + 备注各一条
    assert len(wdb.query_records(keyword="售后支持")) == 1  # 项目列
    assert wdb.query_records(keyword="查不到") == []


def test_query_day_helper(db):
    _seed()
    assert len(wdb.query_day("2026-11-06 08:00:00")) == 3   # 带时间也能截断


def test_query_limit_and_order(db):
    _seed()
    rows = wdb.query_records()
    assert [r["work_date"] for r in rows[:3]] == ["2026-11-06"] * 3
    # 同一天内按成员排序号：张(1) → 李(2) → 王(3)
    assert [r["member_name"] for r in rows[:3]] == ["张工", "李工", "王工"]
    assert len(wdb.query_records(limit=2)) == 2
    asc = wdb.query_records(ascending=True)
    assert [r["work_date"] for r in asc[:3]] == ["2026-11-05"] * 3


def test_query_survives_missing_member_row(db):
    """成员行缺失（member_id 悬空）时记录仍要查得到，名字兜空串"""
    wdb.insert_record(_rec(member_id=4242))
    rows = wdb.query_records()
    assert len(rows) == 1
    assert rows[0]["member_name"] == ""
    assert rows[0]["member_sort"] == 0


# ==================== 聚合 ====================

def test_totals_single_day(db):
    _seed()
    t = wdb.totals("2026-11-06", "2026-11-06")
    assert t["count"] == 3
    assert t["hours"] == 6.0
    assert t["members"] == 3
    assert t["days"] == 1
    assert t["done"] == 1
    # 完成率 1/3 = 33.3%（稿 321 的 71% 是样例数据，口径按 done/count）
    assert t["done_rate"] == 33.3
    assert t["per_capita"] == 2.0        # 6h / (3 人 × 1 天)


def test_totals_week_and_empty(db):
    _seed()
    t = wdb.totals(*wdb.week_range("2026-11-06"))
    assert t["count"] == 6 and t["hours"] == 13.0
    assert t["days"] == 2 and t["members"] == 4
    assert t["per_capita"] == round(13.0 / 8, 1)
    empty = wdb.totals("2020-01-01", "2020-01-02")
    assert empty["count"] == 0 and empty["hours"] == 0.0
    assert empty["done_rate"] == 0.0 and empty["per_capita"] == 0.0


def test_totals_respects_member_filter(db):
    ids = _seed()
    t = wdb.totals(member_id=ids[0])
    assert t["count"] == 2 and t["hours"] == 7.0 and t["members"] == 1


def test_daily_groups_desc_with_rate(db):
    _seed()
    groups = wdb.daily_groups(*wdb.week_range("2026-11-06"))
    assert [g["work_date"] for g in groups] == ["2026-11-06", "2026-11-05"]
    first = groups[0]
    assert (first["count"], first["hours"], first["members"]) == (3, 6.0, 3)
    assert first["done_rate"] == 33
    second = groups[1]
    assert (second["count"], second["hours"]) == (3, 7.0)
    assert second["done_rate"] == 100


def test_daily_groups_excludes_soft_deleted(db):
    _seed()
    rows = wdb.query_records("2026-11-06", "2026-11-06")
    wdb.delete_record(rows[0]["id"])
    groups = wdb.daily_groups("2026-11-06", "2026-11-06")
    assert groups[0]["count"] == 2


def test_stacked_daily_shape(db):
    _seed()
    rows = wdb.stacked_daily(*wdb.week_range("2026-11-06"))
    assert len(rows) == 6                       # 六条记录各占一格
    assert rows[0]["work_date"] == "2026-11-05"  # 升序，柱图从左往右画
    keys = {(r["work_date"], r["member_name"]) for r in rows}
    assert ("2026-11-06", "张工") in keys
    assert all(isinstance(r["hours"], float) for r in rows)


def test_category_hours_desc(db):
    _seed()
    rows = wdb.category_hours(*wdb.week_range("2026-11-06"))
    assert [(r["category"], r["hours"]) for r in rows] == [
        ("开发", 9.0), ("文档", 1.5), ("运维", 1.5), ("沟通", 1.0)]
    assert sum(r["hours"] for r in rows) == 13.0
    assert rows[0]["count"] == 3
    # 工时相同的分类按名字兜底排序：环图两次刷新的切片顺序不会互换
    hours = [r["hours"] for r in rows]
    assert hours == sorted(hours, reverse=True)


def test_category_hours_blank_falls_back(db):
    wdb.insert_record(_rec(category=""))
    rows = wdb.category_hours()
    assert rows[0]["category"] == "未分类"


def test_member_summary_skips_inactive(db):
    """赵工「参与统计」=否，不进成员汇总表（稿 576-587 只有三人）"""
    _seed()
    rows = wdb.member_summary(*wdb.week_range("2026-11-06"))
    assert [r["member_name"] for r in rows] == ["张工", "李工", "王工"]
    zhang = rows[0]
    assert zhang["count"] == 2 and zhang["done"] == 2
    assert zhang["hours"] == 7.0 and zhang["done_rate"] == 100
    li = rows[1]
    assert li["count"] == 2 and li["done"] == 1 and li["done_rate"] == 50
    assert li["avg_progress"] == 80          # (60 + 100) / 2


def test_member_summary_keeps_orphan_records(db):
    """成员行缺失的记录不能整条从汇总里消失（LEFT JOIN + Python 侧过滤）"""
    wdb.insert_record(_rec(member_id=4242))
    rows = wdb.member_summary()
    assert len(rows) == 1
    assert rows[0]["member_name"] == "" and rows[0]["count"] == 1


# ==================== 分类（config JSON） ====================

def test_categories_default_seven(cfg):
    names = [c["name"] for c in wdb.list_categories()]
    assert names == list(wdb.CATEGORIES)
    assert all(c["builtin"] and c["active"] for c in wdb.list_categories())


def test_add_category_appends_after_builtins(cfg):
    assert wdb.add_category("培训") is True
    names = [c["name"] for c in wdb.list_categories()]
    assert names == list(wdb.CATEGORIES) + ["培训"]
    assert wdb.list_categories()[-1]["builtin"] is False
    # 重名（含内置名）拒绝，避免下拉里出现两个同名分类
    assert wdb.add_category("培训") is False
    assert wdb.add_category("开发") is False
    assert wdb.add_category("   ") is False


def test_set_category_active_hides_from_candidates(cfg):
    wdb.add_category("培训")
    assert wdb.set_category_active("培训", False) is True
    assert "培训" not in wdb.category_names(active_only=True)
    assert "培训" in [c["name"] for c in wdb.list_categories()]
    # 内置分类同样只能停用不能移除
    assert wdb.set_category_active("学习", False) is True
    assert "学习" not in wdb.category_names()


def test_category_config_survives_reload(cfg):
    """写盘再读回：配置落 misc.json，重启后自定义分类不丢"""
    import core.app_settings as fas
    wdb.add_category("培训")
    wdb.set_category_active("学习", False)
    fas.invalidate_cache()
    names = [c["name"] for c in wdb.list_categories()]
    assert "培训" in names
    assert any(c["name"] == "学习" and not c["active"]
               for c in wdb.list_categories())


def test_category_config_tolerates_dirty_file(cfg):
    """配置被手改成脏数据时按内置七项兜底，不抛"""
    import core.app_settings as fas
    fas.set("worklog_categories", "不是列表")
    assert [c["name"] for c in wdb.list_categories()] == list(wdb.CATEGORIES)
    fas.set("worklog_categories", [{"nope": 1}, {"name": "培训"}])
    names = [c["name"] for c in wdb.list_categories()]
    assert names == list(wdb.CATEGORIES) + ["培训"]


def test_category_usage_counts_live_records(db):
    _seed()
    # _seed 里三条「开发」：张工 11-06、张工 11-05、李工 11-05（沿用默认分类）
    assert wdb.category_usage("开发") == 3
    assert wdb.category_usage("运维") == 1
    assert wdb.category_usage("不存在") == 0
    rows = wdb.query_records(category="开发")
    wdb.delete_record(rows[0]["id"])
    # 软删除的记录不再算引用，分类可以放开停用
    assert wdb.category_usage("开发") == 2


def test_rename_category_moves_records(db, cfg):
    """改名要连记录列一起换，否则历史记录会挂在一个不存在的分类上"""
    wdb.add_category("调研")
    rid = wdb.insert_record(_rec(category="调研"))
    assert wdb.rename_category("调研", "预研") is True
    assert wdb.get_record(rid)["category"] == "预研"
    names = [c["name"] for c in wdb.list_categories()]
    assert "调研" not in names and "预研" in names


def test_rename_category_guards(db, cfg):
    """内置分类锁名（颜色映射与稿内文案都按名字取），重名与空名拒绝"""
    wdb.add_category("调研")
    assert wdb.rename_category("开发", "研发") is False
    assert wdb.rename_category("调研", "开发") is False
    assert wdb.rename_category("调研", "调研") is False
    assert wdb.rename_category("没有", "预研") is False
    assert wdb.rename_category("调研", "  ") is False
    # 被拒后原分类与记录都不动
    assert [c["name"] for c in wdb.list_categories()][-1] == "调研"


def test_project_candidates(db):
    _seed()
    wdb.insert_record(_rec(title="x", project=""))
    wdb.insert_record(_rec(title="y", project="AutoWork"))
    assert wdb.project_candidates() == ["AutoWork", "售后支持", "运维"]


# ==================== 文字报告 / 导出 ====================

def test_build_report_text_segments_by_member(db):
    _seed()
    text = wdb.build_report_text(*wdb.week_range("2026-11-06"),
                                 title="本周工作")
    assert text.startswith("本周工作（2026-11-02 ~ 2026-11-08）")
    assert "【张工】2 项 · 7.0h" in text
    assert "分页性能优化" in text and "接口联调" in text
    assert text.rstrip().endswith("合计：6 项 · 13.0h · 完成率 66.7%")


def test_build_report_text_single_day_span(db):
    _seed()
    text = wdb.build_report_text("2026-11-06", "2026-11-06")
    assert "（2026-11-06）" in text          # 同一天不写区间


def test_build_report_text_with_note_and_empty(db):
    wdb.insert_record(_rec(note="遗留：等对方联调"))
    assert "备注：遗留：等对方联调" in wdb.build_report_text(
        "2026-11-06", "2026-11-06")
    assert "无记录" in wdb.build_report_text("2020-01-01", "2020-01-02")


def test_export_xlsx_writes_all_rows(db, tmp_path):
    _seed()
    out = str(tmp_path / "worklog.xlsx")
    count = wdb.export_xlsx(out, *wdb.week_range("2026-11-06"))
    assert count == 6
    from openpyxl import load_workbook
    ws = load_workbook(out).worksheets[0]
    assert ws.title == "工作留痕"
    assert [c.value for c in ws[1]] == [h for _, h in wdb._EXPORT_HEADERS]
    # 表头之外恰好 6 行数据：导出走整表路径，不受任何分页夹取影响
    assert ws.max_row == 7
    assert ws.cell(row=2, column=2).value == "张工"


def test_export_xlsx_empty_range(db, tmp_path):
    out = str(tmp_path / "empty.xlsx")
    assert wdb.export_xlsx(out, "2020-01-01", "2020-01-02") == 0
    from openpyxl import load_workbook
    assert load_workbook(out).worksheets[0].max_row == 1   # 只剩表头
