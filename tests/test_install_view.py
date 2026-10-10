# -*- coding: utf-8 -*-
"""安装清单视图（billiard_tables createTime 视角）—— 2026-10-11 新增

数据源：wechat listext createTime/roomAddress/sales 随球桌同步链路落库。
2026-10-11 实测：接口 2026-09 当月 createTime 台子数 143，与人工样例
《球房安装清单202609.xlsx》143 行逐台日期吻合——安装时间即接口
createTime，故不建独立台账表（「本月安装数量」由 room+month 分组动态算）。

覆盖：
- save_all 三列（createTime/roomAddress/sales）落库
- query_install_page：月份/销售/关键词筛选、公司测试默认排除、
  room_count 动态富集（TRIM 分组）、球房分组相邻排序
- install_month_options / install_sales_options
- export_install_xlsx round-trip：表头/合并单元格/列宽/D '@'/E mm-dd-yy
- 旧库迁移：缺三列的老库由 MIGRATIONS 注册表自动补列
"""

import os
import sqlite3

import database.backend as backend
import database.table_db as table_db
from database import schema

# 造数样本：两个球房（其一两台不同日期）+ 公司测试 + 退单 + @s 设备
_ROWS = (
    {"id": 1, "name": "389-13", "roomName": "胖胖台球馆", "code": "C1",
     "sales": "刘绪德", "roomAddress": "上海市嘉定区柳湖路751号",
     "createTime": "2026-09-30 21:34:08", "status": 0},
    {"id": 2, "name": "389-14", "roomName": "胖胖台球馆 ", "code": "C2",
     "sales": "刘绪德", "roomAddress": "上海市嘉定区柳湖路751号",
     "createTime": "2026-09-29 10:00:00", "status": 0},
    {"id": 3, "name": "290-00", "roomName": "APZ台球俱乐部", "code": "C3",
     "sales": "新疆代理", "roomAddress": "新疆库尔勒市382号",
     "createTime": "2026-09-30 00:49:48", "status": 0},
    # 8 月旧装（同球房跨月 → 独立分组，room_count 不串月）
    {"id": 4, "name": "389-00", "roomName": "胖胖台球馆", "code": "C0",
     "sales": "刘绪德", "roomAddress": "上海市嘉定区柳湖路751号",
     "createTime": "2026-08-15 08:00:00", "status": 0},
    # 公司测试（默认排除）
    {"id": 5, "name": "100-01", "roomName": "公司测试", "code": "C4",
     "sales": "内部", "roomAddress": "x", "createTime": "2026-09-01 00:00:00",
     "status": 0},
    # 退单设备（默认包含：安装台账语义）
    {"id": 6, "name": "77-01", "roomName": "退单球房", "code": "C5",
     "sales": "张三", "roomAddress": "y", "createTime": "2026-09-05 00:00:00",
     "status": 2},
    # 手动版本 @s（默认包含：人工样例含 @s 设备）
    {"id": 7, "name": "200-01", "roomName": "KingTV@s", "code": "C6",
     "sales": "李四", "roomAddress": "z", "createTime": "2026-09-03 00:00:00",
     "status": 0},
)


def _isolate(monkeypatch, tmp_path):
    """隔离到临时库（LIKE 路径范式：禁用 FTS 与真实初始化）"""
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(table_db, "DB_PATH", db)
    monkeypatch.setattr(table_db, "_conn", None)
    monkeypatch.setattr(table_db, "_ensure_initialized", lambda c: None)
    monkeypatch.setattr(table_db, "_fts_available", False)
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)
    sl = sqlite3.connect(db)
    sl.executescript(schema.to_sqlite_ddl("billiard_tables"))
    sl.execute("CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT)")
    sl.commit()
    sl.close()
    return db


def _seed(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert table_db.save_all(list(_ROWS)) == len(_ROWS)
    return table_db._get_conn()


# ==================== schema 注册 ====================

def test_schema_registers_install_columns():
    """三列+销售转移必须同时登记 TABLE_COLUMNS 与 MIGRATIONS（双方言单一来源）"""
    cols = {c.name for c in schema.TABLE_COLUMNS["billiard_tables"]}
    assert {"createTime", "roomAddress", "sales", "sales_transfer"} <= cols
    mig = {m.col for m in schema.MIGRATIONS["billiard_tables"]}
    assert {"createTime", "roomAddress", "sales", "sales_transfer"} <= mig
    # DDL 生成两侧均含新列
    assert "createTime" in schema.to_sqlite_ddl("billiard_tables")
    assert "roomAddress" in schema.to_mysql_ddl("billiard_tables")
    assert "sales_transfer" in schema.to_mysql_ddl("billiard_tables")


# ==================== 落库 ====================

def test_save_all_persists_install_fields(monkeypatch, tmp_path):
    """save_all 必须落 createTime/roomAddress/sales 三列"""
    conn = _seed(monkeypatch, tmp_path)
    row = conn.execute(
        "SELECT createTime, roomAddress, sales FROM billiard_tables "
        "WHERE name = '389-13'").fetchone()
    assert row == ("2026-09-30 21:34:08", "上海市嘉定区柳湖路751号", "刘绪德")


# ==================== sales_transfer 回填（xqzg 增量字段） ====================

def test_sales_transfer_backfill_and_save_all_protection(monkeypatch, tmp_path):
    """xqzg 行回填 sales_transfer；save_all（listext 无该字段）不清空回填值"""
    _seed(monkeypatch, tmp_path)
    # 1. 独立回填入口：table_id ↔ name 匹配，仅回填非空值
    n = table_db.update_sales_transfer_from_xqzg([
        {"table_id": "389-13", "sales_transfer": "谢正钱 → 贾高阳"},
        {"table_id": " 389-14 ", "sales_transfer": ""},        # 空值不回填
        {"table_id": "不存在的台子", "sales_transfer": "张三 → 李四"},  # 无匹配
        {"table_id": "", "sales_transfer": "无桌号"},           # 无桌号跳过
    ])
    assert n == 2  # 处理 2 行（空值/无桌号跳过；实际命中 1 行）
    conn = table_db._get_conn()
    val = conn.execute(
        "SELECT sales_transfer FROM billiard_tables WHERE name = '389-13'"
    ).fetchone()[0]
    assert val == "谢正钱 → 贾高阳"

    # 2. save_all 全量替换（listext 行不含 sales_transfer）保护旧值
    rows_no_transfer = [dict(r) for r in _ROWS]
    for r in rows_no_transfer:
        r.pop("sales_transfer", None)
    table_db.save_all(rows_no_transfer)
    val = conn.execute(
        "SELECT sales_transfer FROM billiard_tables WHERE name = '389-13'"
    ).fetchone()[0]
    assert val == "谢正钱 → 贾高阳", "save_all 不得清空回填值"


def test_sales_transfer_in_query_and_search(monkeypatch, tmp_path):
    """查询返回 sales_transfer；关键词可按转移内容命中"""
    _seed(monkeypatch, tmp_path)
    table_db.update_sales_transfer_from_xqzg(
        [{"table_id": "389-13", "sales_transfer": "谢正钱 → 贾高阳"}])
    total, rows = table_db.query_install_page(1, 50)
    by_name = {r["name"]: r for r in rows}
    assert by_name["389-13"]["sales_transfer"] == "谢正钱 → 贾高阳"
    # 关键词模糊命中销售转移列
    total, rows = table_db.query_install_page(1, 50, keyword="贾高阳")
    assert total == 1 and rows[0]["name"] == "389-13"


# ==================== 查询 ====================

def test_query_install_page_filters(monkeypatch, tmp_path):
    """月份/销售/关键词筛选 + 公司测试默认排除 + 退单/@s 默认包含"""
    _seed(monkeypatch, tmp_path)
    # 默认：排除公司测试，包含退单与 @s → 7-1=6 行
    total, rows = table_db.query_install_page(1, 50)
    assert total == 6, total
    names = {r["name"] for r in rows}
    assert "100-01" not in names
    assert {"77-01", "200-01"} <= names

    # 月份筛选：2026-08 只有一台
    total, rows = table_db.query_install_page(1, 50, ym="2026-08")
    assert total == 1 and rows[0]["name"] == "389-00"

    # 销售筛选
    total, rows = table_db.query_install_page(1, 50, sales="新疆代理")
    assert total == 1 and rows[0]["name"] == "290-00"

    # 关键词（覆盖 roomAddress）
    total, rows = table_db.query_install_page(1, 50, keyword="柳湖路")
    assert total == 3, total  # 389-13/389-14/389-00

    # 公司测试显式包含
    total, _rows = table_db.query_install_page(1, 50, include_test=True)
    assert total == 7, total


def test_query_install_page_room_count_and_order(monkeypatch, tmp_path):
    """room_count 按球房+月动态算（TRIM 分组，跨月不串）；分组相邻排序"""
    _seed(monkeypatch, tmp_path)
    total, rows = table_db.query_install_page(1, 50, ym="2026-09")
    assert total == 5, total
    by_name = {r["name"]: r for r in rows}
    # 胖胖 9 月两台（roomName 带尾随空格也要 TRIM 归组）→ room_count=2
    assert by_name["389-13"]["room_count"] == 2
    assert by_name["389-14"]["room_count"] == 2
    # 胖胖 8 月单独分组 → room_count=1（ym 筛选下不可见，全量口径验证）
    total_all, rows_all = table_db.query_install_page(1, 50)
    by_name_all = {r["name"]: r for r in rows_all}
    assert by_name_all["389-00"]["room_count"] == 1

    # 分组相邻：胖胖 9 月两台必须连续出现
    seq = [r["roomName"].strip() for r in rows_all]
    sep_groups = [seq[0]] + [g for i, g in enumerate(seq[1:], 1)
                             if g != seq[i - 1]]
    assert sep_groups.count("胖胖台球馆") == 2, sep_groups  # 8月/9月各一组
    # 组序：胖胖 9 月组（max 09-30 21:34）最先，APZ（09-30 00:49）随后
    first_two = [r["roomName"].strip() for r in rows_all[:2]]
    assert first_two == ["胖胖台球馆", "胖胖台球馆"], first_two
    assert rows_all[2]["roomName"] == "APZ台球俱乐部"


def test_install_options(monkeypatch, tmp_path):
    """月份候选倒序去重；销售候选非空升序（SQLite 按码点序：内<刘<张<新<李）"""
    _seed(monkeypatch, tmp_path)
    assert table_db.install_month_options() == ["2026-09", "2026-08"]
    assert table_db.install_sales_options() == [
        "内部", "刘绪德", "张三", "新疆代理", "李四"]


# ==================== 导出 round-trip ====================

def test_export_xlsx_round_trip(monkeypatch, tmp_path):
    """导出格式与人工样例一致：表头/合并/列宽/D '@'/E mm-dd-yy"""
    _seed(monkeypatch, tmp_path)
    path = str(tmp_path / "out.xlsx")
    count = table_db.export_install_xlsx(path, ym="2026-09")
    assert count == 5, count

    from openpyxl import load_workbook
    ws = load_workbook(path).worksheets[0]
    # sheet 名与样例命名一致（2026-09 → 9月）
    assert ws.title == "球房安装清单9月", ws.title
    # 表头 8 列（前 7 列与样例一致 + 销售转移增量列）
    assert [c.value for c in ws[1]] == [
        "球房名字", "球房地址", "本月安装数量", "球桌编号",
        "安装时间", "销售-归属", "销售-催款", "销售转移"]
    # 同球房（同月）连续行 A/B/C 纵向合并；跨行数=2 的组才合并
    merged = sorted(str(m) for m in ws.merged_cells.ranges)
    assert "A2:A3" in merged and "B2:B3" in merged and "C2:C3" in merged
    # 列宽与样例一致（H 为销售转移增量列）
    for letter, width in (("A", 30), ("B", 67), ("C", 14.4),
                          ("D", 18.2), ("E", 32), ("F", 14), ("G", 14),
                          ("H", 20)):
        assert abs(ws.column_dimensions[letter].width - width) < 0.01, letter
    # 格式：D 文本 '@'，E 日期 mm-dd-yy；E 值为 date
    assert ws["D2"].number_format == "@"
    assert ws["E2"].number_format == "mm-dd-yy"
    assert ws["E2"].value is not None and ws["E2"].value.year == 2026
    # 值：首行 = 组最晚安装的台子；销售两列同源
    assert ws["A2"].value == "胖胖台球馆"
    assert ws["B2"].value == "上海市嘉定区柳湖路751号"
    assert ws["C2"].value == 2
    assert ws["D2"].value == "389-13"
    assert ws["F2"].value == "刘绪德" and ws["G2"].value == "刘绪德"


def test_export_xlsx_single_row_group_no_merge(monkeypatch, tmp_path):
    """单行分组不产生合并单元格；全部月份导出 sheet 名无月份后缀"""
    _seed(monkeypatch, tmp_path)
    path = str(tmp_path / "out_all.xlsx")
    count = table_db.export_install_xlsx(path, sales="新疆代理")
    assert count == 1, count
    from openpyxl import load_workbook
    ws = load_workbook(path).worksheets[0]
    assert ws.title == "球房安装清单", ws.title
    assert list(ws.merged_cells.ranges) == []
    # createTime 解析失败时回退原始字符串（不抛异常）；bad-date 码点序在
    # '2026-…' 之前（DESC 排最前），不能假设在最后一行，全表扫值断言
    conn = table_db._get_conn()
    conn.execute(
        "INSERT INTO billiard_tables (name, roomName, createTime, sales) "
        "VALUES ('X-1', '异常球房', 'bad-date', '谁')")
    conn.commit()
    table_db.export_install_xlsx(path)
    from openpyxl import load_workbook as _lw
    ws2 = _lw(path).worksheets[0]
    flat = [str(c.value) for row in ws2.iter_rows(min_row=2)
            for c in row if c.value is not None]
    assert "bad-date" in flat


# ==================== 旧库迁移 ====================

def test_legacy_db_migration_adds_columns(monkeypatch, tmp_path):
    """缺三列的老库由 MIGRATIONS 注册表自动补列（SQLite 侧）"""
    db = str(tmp_path / "legacy.db")
    sl = sqlite3.connect(db)
    # 老库结构：2026-10-11 之前的列集合（无 createTime/roomAddress/sales）
    sl.execute(
        "CREATE TABLE billiard_tables ("
        "id INTEGER PRIMARY KEY, name TEXT DEFAULT '', "
        "roomName TEXT DEFAULT '', onlineStatusName TEXT DEFAULT '', "
        "remark TEXT DEFAULT '', cameraPassExt TEXT DEFAULT '', "
        "snk_code TEXT DEFAULT '', code TEXT DEFAULT '', "
        "city TEXT DEFAULT '', deviceVersion TEXT DEFAULT '', "
        "status TEXT DEFAULT '')")
    sl.execute("INSERT INTO billiard_tables (id, name) VALUES (1, '旧台')")
    sl.commit()
    changed = table_db._migrate_sqlite_add_columns(sl, "billiard_tables")
    sl.close()
    assert changed is True
    sl = sqlite3.connect(db)
    cols = {r[1] for r in sl.execute(
        "PRAGMA table_info(billiard_tables)").fetchall()}
    sl.close()
    assert {"createTime", "roomAddress", "sales", "sales_transfer"} <= cols
