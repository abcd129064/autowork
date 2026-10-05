# -*- coding: utf-8 -*-
"""企微售后群归档数据层（database/chat_archive_db.py）回归测试

覆盖：幂等导入（msg_id 指纹去重）、采集断点读写、消息查询/筛选/分页、
按群/按天统计、群×时间段×问题类型三维透视（**按 room_id 分组**）、
人工删除、打标，以及「归档表必须豁免数据保留清理」这条硬约束。

隔离方式：临时 SQLite + monkeypatch table_db.DB_PATH，把 MySQL 开关强制
关掉，绝不触碰 database/tables.db 与生产 MySQL（AGENTS.md §5.2）。

关于临时目录：本文件**不用 pytest 的 tmp_path**，而是自己探测一个可写目录。
原因是受限文件沙箱下 pytest 连自己的 basetemp（%TEMP%\\pytest-of-<user>）
都建不出来（PermissionError [WinError 5]），tmp_path 型测试会整批变成
「环境性 error」而不是跑到断言——归档库这种要落盘 SQLite 的测试必须真跑起来。
探测顺序：系统临时目录 → tools/_scratch/pytest_chat_db（已被 .gitignore 命中）。
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid

import pytest

import database.backend as backend
import database.chat_archive_db as cadb
import database.data_retention as dr
import database.table_db as table_db
from database import schema

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH_FALLBACK = os.path.join(ROOT, "tools", "_scratch", "pytest_chat_db")


def _writable_dir() -> str:
    """选一个真的能写文件的临时目录（探测失败则 skip，不产生假失败）"""
    candidates = []
    try:
        candidates.append(tempfile.mkdtemp(prefix="aw_chat_archive_"))
    except OSError:
        pass
    candidates.append(SCRATCH_FALLBACK)
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, "_probe.tmp")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("x")
            os.remove(probe)
            return d
        except OSError:
            continue
    pytest.skip("当前环境没有可写的临时目录")


@pytest.fixture
def db(monkeypatch):
    """临时归档库：建 3 张 chat_archive_* 表 + sync_meta，关闭 MySQL 开关"""
    tmpdir = _writable_dir()
    path = os.path.join(tmpdir, "chat_{}.db".format(uuid.uuid4().hex[:8]))
    monkeypatch.setattr(table_db, "DB_PATH", path)
    monkeypatch.setattr(table_db, "_ensure_initialized", lambda c: None)
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)
    sl = sqlite3.connect(path)
    for table in schema.CHAT_ARCHIVE_TABLES:
        sl.executescript(schema.to_sqlite_ddl(table))
    sl.executescript(schema.to_sqlite_ddl("sync_meta"))
    sl.commit()
    sl.close()
    yield path
    table_db.close()          # 先释放连接，Windows 下才能删文件
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


def _msg(msg_id, **kw):
    """构造一条入站契约消息（字段与 chat_archive_messages 对齐）"""
    base = {
        "msg_id": msg_id, "source": cadb.SOURCE_CLIP, "room_id": "after_sales",
        "room_name": "售后群", "sender_id": "沈喆", "sender_name": "沈喆",
        "sender_raw": "沈喆", "sender_kind": "internal", "msg_type": "text",
        "content": "内容", "media_count": 0,
        "msg_ts": "2026-09-22 18:14:31", "msg_ts_inferred_year": 0,
        "mentions": [], "pull_batch": "20261006_040903",
    }
    base.update(kw)
    return base


def _seed(db_path):
    """4 条消息：两个群（room_id 不同、room_name 相同）、跨 3 天"""
    cadb.insert_messages([
        _msg("m1", msg_ts="2026-08-10 16:33:33", sender_name="江苏~朱华国",
             sender_raw="江苏~朱华国@微信@微信联系人", sender_kind="external_wechat",
             content="二号台又这样啦"),
        _msg("m2", msg_ts="2026-08-12 12:55:45", msg_type="video",
             content="[视频]", media_count=1),
        _msg("m3", msg_ts="2026-09-22 18:11:53", msg_type="empty", content="",
             sender_name="沈喆"),
        _msg("m4", msg_ts="2026-09-22 18:14:31", room_id="room_b",
             content="更换电池后要按f2进入bios"),
    ])


# ==================== 幂等导入 ====================

def test_insert_messages_counts(db):
    rows = [_msg("a1"), _msg("a2")]
    assert cadb.insert_messages(rows) == {"inserted": 2, "skipped": 0,
                                          "invalid": 0}
    assert cadb.count_messages() == 2
    # 重复导入同一批 → 全部 skipped，不产生重复行
    assert cadb.insert_messages(rows) == {"inserted": 0, "skipped": 2,
                                          "invalid": 0}
    assert cadb.count_messages() == 2


def test_insert_messages_rejects_empty_msg_id(db):
    res = cadb.insert_messages([_msg(""), _msg("ok1")])
    assert res["inserted"] == 1 and res["invalid"] == 1
    assert cadb.count_messages() == 1


def test_insert_messages_intra_batch_duplicate_written_once(db):
    res = cadb.insert_messages([_msg("dup"), _msg("dup")])
    assert res["inserted"] == 1 and res["invalid"] == 1
    assert cadb.count_messages() == 1


def test_insert_messages_empty_input(db):
    assert cadb.insert_messages([]) == {"inserted": 0, "skipped": 0,
                                        "invalid": 0}


def test_insert_messages_applies_defaults(db):
    """room_id/room_name/pull_batch 缺省由调用方兜底（采集器只知道 room_id）"""
    cadb.insert_messages([_msg("d1", room_id="", room_name="", pull_batch="")],
                         default_room_id="after_sales",
                         default_room_name="售后群",
                         default_batch="batch_x")
    row = cadb.query_messages()[0]
    assert row["room_id"] == "after_sales"
    assert row["room_name"] == "售后群"
    assert row["pull_batch"] == "batch_x"


def test_insert_messages_serializes_mentions(db):
    cadb.insert_messages([_msg("mt", mentions=["沈喆", "25984982603779408@openim"])])
    row = cadb.query_messages()[0]
    assert json.loads(row["mentions"]) == ["沈喆", "25984982603779408@openim"]


def test_insert_messages_fills_created_at(db):
    cadb.insert_messages([_msg("c1", created_at="1999-01-01 00:00:00")])
    row = cadb.query_messages()[0]
    # created_at 由数据层统一维护，不接受调用方传入
    assert row["created_at"] != "1999-01-01 00:00:00"
    assert len(row["created_at"]) == 19


def test_insert_messages_many_rows_chunked(db):
    """超过分块阈值（16 列 × 60 行 = 960 占位符）时仍能一次写全"""
    rows = [_msg("bulk_{:03d}".format(i)) for i in range(150)]
    assert cadb.insert_messages(rows)["inserted"] == 150
    assert cadb.count_messages() == 150


def test_existing_msg_ids(db):
    cadb.insert_messages([_msg("e1"), _msg("e2")])
    assert cadb.existing_msg_ids(["e1", "zz"]) == {"e1"}
    assert cadb.existing_msg_ids([]) == set()


# ==================== 采集断点 ====================

def test_cursor_missing_returns_empty_dict(db):
    cur = cadb.get_cursor("after_sales")
    assert cur["scope"] == "after_sales"
    assert cur["next_cursor"] == "" and cur["last_msg_ts"] == ""
    assert cur["updated_at"] == ""


def test_cursor_set_insert_then_update(db):
    cadb.set_cursor("after_sales", next_cursor="2026-08-10 16:33:33",
                    last_msg_ts="2026-08-10 16:33:33")
    cur = cadb.get_cursor("after_sales")
    assert cur["next_cursor"] == "2026-08-10 16:33:33"
    assert cur["last_success_ts"]          # 未指定时自动填当前时间
    first_success = cur["last_success_ts"]

    cadb.set_cursor("after_sales", next_cursor="2026-10-05 21:09:37",
                    last_msg_ts="2026-10-05 21:09:37")
    cur2 = cadb.get_cursor("after_sales")
    assert cur2["next_cursor"] == "2026-10-05 21:09:37"
    assert cur2["last_msg_ts"] == "2026-10-05 21:09:37"
    assert len(cur2["last_success_ts"]) == 19 or cur2["last_success_ts"] == \
        first_success


def test_cursor_scopes_are_independent(db):
    cadb.set_cursor("room_a", last_msg_ts="2026-01-01 00:00:00")
    cadb.set_cursor("room_b", last_msg_ts="2026-02-02 00:00:00")
    assert cadb.get_cursor("room_a")["last_msg_ts"] == "2026-01-01 00:00:00"
    assert cadb.get_cursor("room_b")["last_msg_ts"] == "2026-02-02 00:00:00"


def test_latest_msg_ts(db):
    _seed(db)
    assert cadb.latest_msg_ts() == "2026-09-22 18:14:31"
    assert cadb.latest_msg_ts(room_id="after_sales") == "2026-09-22 18:11:53"
    assert cadb.latest_msg_ts(source="不存在") == ""


# ==================== 查询与筛选 ====================

def test_count_and_query_all(db):
    _seed(db)
    assert cadb.count_messages() == 4
    assert len(cadb.query_messages()) == 4


def test_query_messages_order_is_newest_first(db):
    _seed(db)
    ts = [r["msg_ts"] for r in cadb.query_messages()]
    assert ts == sorted(ts, reverse=True)


def test_query_messages_room_filter(db):
    _seed(db)
    rows = cadb.query_messages(room_id="room_b")
    assert [r["msg_id"] for r in rows] == ["m4"]


def test_query_messages_date_range_inclusive(db):
    _seed(db)
    rows = cadb.query_messages(date_from="2026-09-22", date_to="2026-09-22")
    assert {r["msg_id"] for r in rows} == {"m3", "m4"}
    assert cadb.count_messages(date_from="2026-08-11",
                               date_to="2026-09-21") == 1


def test_query_messages_keyword(db):
    _seed(db)
    rows = cadb.query_messages(keyword="二号台")
    assert [r["msg_id"] for r in rows] == ["m1"]


def test_query_messages_type_and_source_filter(db):
    _seed(db)
    assert [r["msg_id"] for r in cadb.query_messages(msg_type="video")] == ["m2"]
    assert [r["msg_id"] for r in cadb.query_messages(msg_type="empty")] == ["m3"]
    assert len(cadb.query_messages(source=cadb.SOURCE_CLIP)) == 4
    assert cadb.query_messages(source="manual") == []


def test_query_messages_pagination(db):
    _seed(db)
    page1 = cadb.query_messages(limit=2, offset=0)
    page2 = cadb.query_messages(limit=2, offset=2)
    assert len(page1) == 2 and len(page2) == 2
    assert {r["msg_id"] for r in page1} & {r["msg_id"] for r in page2} == set()


def test_query_messages_returns_all_columns(db):
    _seed(db)
    row = cadb.query_messages(keyword="二号台")[0]
    assert set(row) == set(cadb.MESSAGE_FIELDS)


# ==================== 统计 ====================

def test_stats_by_room(db):
    _seed(db)
    stats = {s["room_id"]: s for s in cadb.stats_by_room()}
    assert stats["after_sales"]["msg_count"] == 3
    assert stats["after_sales"]["first_ts"] == "2026-08-10 16:33:33"
    assert stats["after_sales"]["last_ts"] == "2026-09-22 18:11:53"
    assert stats["room_b"]["msg_count"] == 1
    # 排序：消息多的在前
    assert cadb.stats_by_room()[0]["room_id"] == "after_sales"


def test_stats_by_room_date_filter(db):
    _seed(db)
    stats = cadb.stats_by_room(date_from="2026-09-22", date_to="2026-09-22")
    assert {s["room_id"] for s in stats} == {"after_sales", "room_b"}
    assert all(s["msg_count"] == 1 for s in stats)


def test_stats_by_day(db):
    _seed(db)
    days = {d["day"]: d["msg_count"] for d in cadb.stats_by_day()}
    assert days == {"2026-08-10": 1, "2026-08-12": 1, "2026-09-22": 2}
    # 按日期升序
    assert [d["day"] for d in cadb.stats_by_day()] == sorted(days)


def test_stats_by_day_room_filter(db):
    _seed(db)
    days = cadb.stats_by_day(room_id="room_b")
    assert len(days) == 1 and days[0]["msg_count"] == 1


# ==================== 三维透视 ====================

def test_query_pivot_untagged_goes_to_uncategorized(db):
    _seed(db)
    rows = cadb.query_pivot()
    assert len(rows) == 2          # 两个群各一行
    assert {r["category"] for r in rows} == {cadb.UNCATEGORIZED}
    by_room = {r["room_id"]: r for r in rows}
    assert by_room["after_sales"]["msg_count"] == 3


def test_query_pivot_groups_by_room_id_not_room_name(db):
    """两个群同名时必须是两行：分组键是 room_id，不是 room_name

    这正是 database/aftersale_db.py query_rank 用 TRIM(room_name) 分组的老问题
    （群改名撕裂 / 重名合并），归档报告不复用该写法。
    """
    cadb.insert_messages([
        _msg("x1", room_id="room_a", room_name="售后群"),
        _msg("x2", room_id="room_b", room_name="售后群"),
    ])
    rows = cadb.query_pivot()
    assert len(rows) == 2
    assert {r["room_id"] for r in rows} == {"room_a", "room_b"}
    assert {r["room_name"] for r in rows} == {"售后群"}


def test_query_pivot_with_tags(db):
    """打过标的按标签分类，其余归「未分类」——未分类可能每个群一行，需按类求和"""
    _seed(db)
    cadb.add_tag("m1", "设备卡死", matched_rule="卡死|卡住", confidence=0.9)
    rows = cadb.query_pivot()
    cats = {}
    for r in rows:
        cats[r["category"]] = cats.get(r["category"], 0) + r["msg_count"]
    assert cats["设备卡死"] == 1
    assert cats[cadb.UNCATEGORIZED] == 3
    # m2/m3 在 after_sales、m4 在 room_b → 未分类确实分成两行
    uncat_rows = [r for r in rows if r["category"] == cadb.UNCATEGORIZED]
    assert len(uncat_rows) == 2
    assert {r["room_id"] for r in uncat_rows} == {"after_sales", "room_b"}


def test_query_pivot_category_filter(db):
    _seed(db)
    cadb.add_tag("m1", "设备卡死")
    cadb.add_tag("m4", "电池/电源")
    assert {r["category"] for r in cadb.query_pivot(category="设备卡死")} == \
        {"设备卡死"}
    assert {r["category"] for r in cadb.query_pivot(category=cadb.UNCATEGORIZED)} \
        == {cadb.UNCATEGORIZED}


def test_query_pivot_room_and_date_filter(db):
    _seed(db)
    rows = cadb.query_pivot(room_id="room_b")
    assert len(rows) == 1 and rows[0]["room_id"] == "room_b"
    rows2 = cadb.query_pivot(date_from="2026-08-10", date_to="2026-08-10")
    assert len(rows2) == 1 and rows2[0]["msg_count"] == 1


def test_query_pivot_empty_tags_still_uncategorized(db):
    """标签行的 category 为空串时按「未分类」算，不能凭空多出一个空分类"""
    cadb.insert_messages([_msg("t1")])
    cadb.add_tag("t1", "")
    rows = cadb.query_pivot()
    assert len(rows) == 1
    assert rows[0]["category"] == cadb.UNCATEGORIZED
    assert rows[0]["msg_count"] == 1


# ==================== 打标与人工删除 ====================

def test_add_tag_and_tags_for(db):
    _seed(db)
    tid = cadb.add_tag("m1", "设备卡死", matched_rule="卡死", confidence=0.8,
                       tagged_by="rule")
    assert tid > 0
    tags = cadb.tags_for("m1")
    assert len(tags) == 1
    assert tags[0]["category"] == "设备卡死"
    assert tags[0]["matched_rule"] == "卡死"
    assert abs(float(tags[0]["confidence"]) - 0.8) < 1e-9
    assert tags[0]["tagged_by"] == "rule"


def test_add_tag_allows_multiple_per_message(db):
    _seed(db)
    cadb.add_tag("m1", "设备卡死")
    cadb.add_tag("m1", "电源问题")
    assert len(cadb.tags_for("m1")) == 2


def test_delete_room_removes_messages_and_tags(db):
    _seed(db)
    cadb.add_tag("m1", "设备卡死")
    n = cadb.delete_room("after_sales")
    assert n == 3
    assert cadb.count_messages() == 1                 # room_b 保留
    assert cadb.tags_for("m1") == []                  # 标签一并清掉
    assert cadb.count_messages(room_id="room_b") == 1


def test_delete_room_keeps_cursor(db):
    """断点不随数据删除一起丢：重采的起点由人工单独重置"""
    _seed(db)
    cadb.set_cursor("after_sales", last_msg_ts="2026-09-22 18:11:53")
    cadb.delete_room("after_sales")
    assert cadb.get_cursor("after_sales")["last_msg_ts"] == "2026-09-22 18:11:53"


def test_delete_room_unknown_returns_zero(db):
    assert cadb.delete_room("不存在") == 0


# ==================== 数据保留清理豁免 ====================

def test_retention_whitelist_excludes_archive_tables(db):
    """归档表不在数据保留清理的表清单里（表清单是模块内硬编码白名单）

    chat_archive_cursor 一旦被清掉，增量采集就失去断点依据，只能全量重采；
    所以这条是硬约束，新增清理表时必须显式确认，不能顺手加进来。
    """
    for table in schema.CHAT_ARCHIVE_TABLES:
        assert table not in dr._SIZE_TABLES
        assert table not in dr._STATUS_TABLES


def test_retention_cleanup_leaves_archive_untouched(db):
    """即使配置里硬写了归档表名，按大小清理也不会删它们

    _cleanup_by_size 用 _SIZE_TABLES.get(table) 取日期桶表达式，取不到就跳过；
    这里把归档表名塞进 cfg["tables"] 模拟「配置被误改」的最坏情况。
    """
    _seed(db)
    cadb.set_cursor("after_sales", last_msg_ts="2026-08-10 16:33:33")
    cfg = {"enabled": True, "age_days": 1, "check_interval_days": 0,
           "max_size_gb": 0, "min_size_gb": 0, "min_keep_days": 1,
           "tables": list(schema.CHAT_ARCHIVE_TABLES)}
    deleted = dr._cleanup_by_size(table_db.get_conn(), cfg, None)
    assert deleted == 0
    assert cadb.count_messages() == 4
    assert cadb.get_cursor("after_sales")["last_msg_ts"] == "2026-08-10 16:33:33"


# ==================== 与解析器的端到端（导入管线） ====================

CLIP_TEXT = (
    "江苏~朱华国@微信@微信联系人 8/12 12:55:45\r\n"
    "[视频]\r\n"
    "\r\n"
    "\u6c88\u5586 8/12 12:58:20\r\n"
    "我们手势贴球识别要2秒\r\n"
)


def _import_clip(db_path):
    from core import wecom_clip as wc
    return wc.import_clip_text(CLIP_TEXT, room_id="after_sales",
                               room_name="售后群", batch_index=0,
                               pull_batch="unit")


def test_clip_text_end_to_end_import(db):
    res = _import_clip(db)
    assert res["inserted"] == 2
    assert res["messages"] == 2
    rows = cadb.query_messages()
    assert len(rows) == 2
    one = [r for r in rows if r["msg_type"] == "video"][0]
    assert one["content"] == "[视频]"
    assert one["media_count"] == 1
    assert one["sender_kind"] == "external_wechat"
    assert one["sender_name"] == "江苏~朱华国"
    assert one["source"] == cadb.SOURCE_CLIP
    assert one["pull_batch"] == "unit"


def test_clip_import_is_idempotent_and_advances_cursor(db):
    first = _import_clip(db)
    assert first["inserted"] == 2
    cur = cadb.get_cursor("after_sales")
    assert cur["last_msg_ts"] == "2026-08-12 12:58:20"
    second = _import_clip(db)
    assert second["inserted"] == 0 and second["skipped"] == 2
    assert cadb.count_messages() == 2


def test_import_batches_dir_merges_overlap(db, tmp_path_unused=None):
    """多批次目录导入：跨批重叠靠 msg_id 指纹去重，统计口径对得上"""
    from core import wecom_clip as wc
    out = os.path.join(_writable_dir(), "run_{}".format(uuid.uuid4().hex[:8]))
    os.makedirs(out, exist_ok=True)
    try:
        with open(os.path.join(out, "batch_00.txt"), "wb") as fh:
            fh.write(("甲 9/22 10:00:00\r\n第一条\r\n").encode("utf-8"))
        with open(os.path.join(out, "batch_01.txt"), "wb") as fh:
            # 与 batch_00 重叠一条，另加一条新的
            fh.write(("甲 9/22 10:00:00\r\n第一条\r\n\r\n"
                      "乙 9/22 11:00:00\r\n第二条\r\n").encode("utf-8"))
        res = wc.import_batches_dir(out, room_id="after_sales",
                                    room_name="售后群")
        assert res["batches"] == 2
        assert res["raw"] == 3
        assert res["unique"] == 2
        assert res["inserted"] == 2
        assert cadb.count_messages() == 2
        assert cadb.get_cursor("after_sales")["last_msg_ts"] == \
            "2026-09-22 11:00:00"
    finally:
        shutil.rmtree(out, ignore_errors=True)
