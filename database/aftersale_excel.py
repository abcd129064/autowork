# -*- coding: utf-8 -*-
"""售后 Excel 导出/导入（A3 excel 切片）。

从 database/aftersale_db.py 下沉：表头样式、按类型分 sheet、统计图表、
导出筛选、历史 Excel 解析与批量导入。连接/周期/字段常量仍由
aftersale_db 单一维护，函数内延迟取用，既有 adb._conn /
adb._record_cycle / adb._EXPORT_HEADERS 等 monkeypatch 点继续生效。
"""

import os
from datetime import datetime

from database.aftersale_queries import _append_cycle_where
from database.aftersale_query_sql import _build_where


def _adb():
    """延迟取 aftersale_db（避免顶层循环 import）。"""
    from database import aftersale_db
    return aftersale_db


# ==================== 导出 / 导入 ====================

def _safe_sheet_title(title: str) -> str:
    r"""Excel sheet 名约束：≤31 字符且不含 : \ / ? * [ ]，非法字符转 -"""
    for ch in ':/\\?*[]':
        title = title.replace(ch, "-")
    return title[:31] or "未分类"


def _write_export_sheet(ws, rows):
    """写导出 sheet 公共逻辑：表头（加粗浅青底）+ 数据行 + 列宽 + 冻结首行

    周期列按发生时间动态归属，与列表筛选口径一致。
    """
    from openpyxl.styles import Font, PatternFill, Alignment

    header_font = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="D9F2F4")
    headers = _adb()._EXPORT_HEADERS
    for c, (_key, header) in enumerate(headers, 1):
        cell = ws.cell(row=1, column=c, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
    for r, rec in enumerate(rows, 2):
        for c, (key, _h) in enumerate(headers, 1):
            val = rec.get(key) or ""
            if key == "cycle_start":
                cs = _adb()._record_cycle(rec.get("occurred_at"),
                                   rec.get("created_at"))
                val = _adb().cycle_label(cs) if cs else ""
            ws.cell(row=r, column=c, value=str(val))
    widths = (12, 24, 10, 8, 28, 30, 10, 30, 10, 12, 18, 12, 10, 16)
    for c, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(row=1, column=c).column_letter].width = w
    ws.freeze_panes = "A2"


def _write_stats_sheet(wb, rows):
    """统计图表 sheet：类型×状态数据表 + 类型数量柱状图 + 解决状态饼图

    布局：A1:D{last} 数据表（含合计行），F2 起柱状图；
    数据表下方 A 列放 已解决/未解决 两行供饼图引用，饼图锚定 D 列。
    """
    if not rows:
        return
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.chart import BarChart, PieChart, Reference

    ws = wb.create_sheet("统计图表")
    # 统计各类型 总数/已解决/未解决
    counts = {}
    for rec in rows:
        t = str(rec.get("issue_type") or "未分类")
        item = counts.setdefault(t, [0, 0, 0])
        item[0] += 1
        if rec.get("resolved") == "是":
            item[1] += 1
        else:
            item[2] += 1
    ordered = sorted(counts.items(), key=lambda kv: kv[1][0], reverse=True)

    header_font = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="D9F2F4")
    for c, h in enumerate(("类型", "数量", "已解决", "未解决"), 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
    total = total_yes = total_no = 0
    for r, (t, (n, yes, no)) in enumerate(ordered, 2):
        ws.cell(row=r, column=1, value=t)
        ws.cell(row=r, column=2, value=n)
        ws.cell(row=r, column=3, value=yes)
        ws.cell(row=r, column=4, value=no)
        total += n
        total_yes += yes
        total_no += no
    last = len(ordered) + 1  # 合计行号
    ws.cell(row=last, column=1, value="合计").font = header_font
    ws.cell(row=last, column=2, value=total)
    ws.cell(row=last, column=3, value=total_yes)
    ws.cell(row=last, column=4, value=total_no)

    # 柱状图：各问题类型数量（不含合计行）
    bar = BarChart()
    bar.type = "col"
    bar.style = 10
    bar.title = "各问题类型数量"
    bar.y_axis.title = "数量"
    bar.add_data(Reference(ws, min_col=2, max_col=2,
                           min_row=1, max_row=last - 1),
                 titles_from_data=True)
    bar.set_categories(Reference(ws, min_col=1, min_row=2,
                                 max_row=last - 1))
    bar.width = 16
    bar.height = 9
    ws.add_chart(bar, "F2")

    # 饼图：已解决/未解决占比（数据表下方单独两行，避免混入类型行）
    pr = last + 2  # 饼图数据起始行
    ws.cell(row=pr - 1, column=1, value="状态").font = header_font
    ws.cell(row=pr - 1, column=2, value="数量").font = header_font
    ws.cell(row=pr, column=1, value="已解决")
    ws.cell(row=pr, column=2, value=total_yes)
    ws.cell(row=pr + 1, column=1, value="未解决")
    ws.cell(row=pr + 1, column=2, value=total_no)
    pie = PieChart()
    pie.title = "解决状态占比"
    pie.add_data(Reference(ws, min_col=2, min_row=pr - 1, max_row=pr + 1),
                 titles_from_data=True)
    pie.set_categories(Reference(ws, min_col=1, min_row=pr, max_row=pr + 1))
    pie.width = 12
    pie.height = 9
    ws.add_chart(pie, f"D{pr}")


def export_xlsx(path: str, keyword: str = "", cycle_start: str = "",
                issue_type: str = "", resolved: str = "",
                is_initiative: str = "", is_our_problem: str = "") -> int:
    """按筛选条件导出全部记录（不分页）为 xlsx，返回导出条数

    表头与 售后问题汇总8月.xlsx 对齐，附加 填写时间/填写人/周期 三列。
    导出结构：
    - Sheet「售后记录」：全部记录（原样）
    - 每个有数据的问题类型一个 Sheet（如「硬件问题」），便于按类型分发
    - Sheet「统计图表」：类型数量柱状图 + 解决状态饼图
    """
    from openpyxl import Workbook

    conn = _adb()._conn()
    where, params = _build_where(keyword, issue_type, resolved,
                                 is_initiative, is_our_problem)
    # P2（2026-08-28）：周期筛选 SQL 化（与列表/统计同口径），
    # 导出前不再全量取回后 Python 过滤
    where, params = _append_cycle_where(where, params, cycle_start)
    cur = conn.execute(
        f"SELECT {', '.join(_adb().RECORD_FIELDS)} FROM aftersale_records"
        f"{where} ORDER BY id DESC", params)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    wb = Workbook()
    # 主 sheet：全部记录
    ws = wb.active
    ws.title = "售后记录"
    _write_export_sheet(ws, rows)

    # 按问题类型分类：预置枚举顺序在前，库中额外类型排后
    by_type = {}
    for rec in rows:
        by_type.setdefault(str(rec.get("issue_type") or "未分类"),
                           []).append(rec)
    for t in _adb().ISSUE_TYPES:
        if t in by_type:
            ws_t = wb.create_sheet(_safe_sheet_title(t))
            _write_export_sheet(ws_t, by_type.pop(t))
    for t, group in by_type.items():
        ws_t = wb.create_sheet(_safe_sheet_title(t))
        _write_export_sheet(ws_t, group)

    # 统计图表 sheet
    _write_stats_sheet(wb, rows)

    out_dir = os.path.dirname(os.path.abspath(path))
    os.makedirs(out_dir, exist_ok=True)
    wb.save(path)
    return len(rows)


def parse_excel_rows(xlsx_path: str) -> tuple:
    """解析售后汇总 Excel，返回 (Excel 表头列表, 记录字典列表)；不写库

    与 import_excel_rows 共用同一解析逻辑，供导入预览与正式导入使用。
    规则：
    - 表头按中文名定位（类型/球房/桌号/地区/问题/发生原因/是否解决/解决方案/解决人/响应时间）
    - 类型列在 Excel 中为分组首行标记，逐行向下填充
    - 跳过全空行；是否解决空值默认「否」
    - created_at 填解析时间，creator 标记「Excel导入」，周期按当前配置归属
    """
    from openpyxl import load_workbook

    wb = load_workbook(xlsx_path, data_only=True)
    ws = wb.worksheets[0]  # 取第一个工作表（Sheet1 正式表）

    # 表头定位：首行按中文名匹配列号
    header_map = {}
    excel_headers = []
    for c in range(1, ws.max_column + 1):
        h = str(ws.cell(row=1, column=c).value or "").strip()
        if h:
            header_map[h] = c
            excel_headers.append(h)
    need = ("类型", "球房", "桌号", "地区", "问题")
    missing = [h for h in need if h not in header_map]
    if missing:
        raise ValueError(f"表头缺少必需列: {'、'.join(missing)}")

    def _val(row_idx, header):
        c = header_map.get(header)
        if not c:
            return ""
        v = ws.cell(row=row_idx, column=c).value
        return str(v or "").strip()

    now = datetime.now()
    created_at = now.strftime("%Y-%m-%d %H:%M:%S")
    occurred = created_at[:10]  # 历史导入无发生时间，默认取导入当日
    cycle = _adb().cycle_start_of(now)
    last_type = ""
    data = []
    for r in range(2, ws.max_row + 1):
        issue_type = _val(r, "类型") or last_type
        if _val(r, "类型"):
            last_type = issue_type
        room = _val(r, "球房")
        table_no = _val(r, "桌号")
        problem = _val(r, "问题")
        if not room and not table_no and not problem:
            continue  # 全空行跳过
        resolved = _val(r, "是否解决") or "否"
        if resolved not in ("是", "否"):
            resolved = "否"
        data.append({
            "created_at": created_at, "occurred_at": occurred,
            "creator": "Excel导入", "issue_type": issue_type,
            "table_no": table_no, "room_name": room,
            "region": _val(r, "地区"), "problem": problem,
            "cause": _val(r, "发生原因"), "resolved": resolved,
            "solution": _val(r, "解决方案"), "resolver": _val(r, "解决人"),
            "response_time": _val(r, "响应时间"), "cycle_start": cycle,
        })
    return excel_headers, data


def import_excel_rows(xlsx_path: str) -> int:
    """一次性导入历史 Excel（售后问题汇总 格式），返回导入条数

    解析规则见 parse_excel_rows；解析结果批量写入数据库。
    """
    _headers, data = parse_excel_rows(xlsx_path)
    if not data:
        return 0
    order = ("created_at", "occurred_at", "creator", "issue_type", "table_no",
             "room_name", "region", "problem", "cause", "resolved",
             "solution", "resolver", "response_time", "cycle_start")
    conn = _conn()
    conn.executemany(
        "INSERT INTO aftersale_records "
        "(created_at, occurred_at, creator, issue_type, table_no, room_name, "
        "region, problem, cause, resolved, solution, resolver, response_time, "
        "snk_code, device_code, cycle_start) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [tuple(rec[k] for k in order) + ("", "") for rec in data])
    conn.commit()
    _adb()._invalidate_field_cands_cache()
    return len(data)
