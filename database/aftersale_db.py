# -*- coding: utf-8 -*-
"""售后记录数据层（SQLite / MySQL 双后端，自动跟随 MySQL 测试开关）

职责：
- insert_record / update_record / delete_record：售后记录增删改
- query_page / query_with_stats：筛选 + 分页 + 统计（周期/类型/状态/关键词）
- get_cycle_options：历史周期下拉选项
- get_field_candidates：问题/解决人/地区动态候选
- export_xlsx：按筛选条件导出（表头与 售后问题汇总8月.xlsx 对齐）
- import_excel_rows：一次性导入历史 Excel

连接复用 table_db 双后端路由（SQLite 单连接 / MySQL thread-local），
MySQL 模式下多人各自提交即提交即落库，其他用户刷新/手动同步后可见。
周期规则：周二开始、周一结束，按填写时间自动计算 cycle_start 冗余落库。
"""

import os
from datetime import datetime, timedelta

from database import table_db

# ==================== 字段枚举（来源：售后问题汇总8月.xlsx 解析） ====================

# 类型（11 值，Excel 中为分组首行标记，系统改为每条独立选择）
ISSUE_TYPES = (
    "硬件问题", "程序相关", "识别问题", "直播相关", "操作问题",
    "其他问题", "相机偏移", "新球助手", "安装调试", "不能扫码", "待查",
)

# 地区预置（Excel 历史 9 值，允许自由输入新地区）
REGIONS_PRESET = ("上海", "云南", "四川", "广东", "新疆", "江苏", "江西", "湖南", "西藏")

# 响应时间预置档位（Excel 原数据为自由文本，允许自由输入）
RESPONSE_TIME_PRESET = ("1分钟内", "5分钟内", "30分钟内", "1小时内", "1小时以上")

# 记录字段（与建表 DDL 一致）
RECORD_FIELDS = (
    "created_at", "creator", "issue_type", "table_no", "room_name",
    "region", "problem", "cause", "resolved", "is_initiative",
    "is_our_problem", "solution", "resolver",
    "response_time", "snk_code", "device_code", "cycle_start",
)

# 关键词搜索覆盖列（全字段模糊匹配）
_SEARCH_FIELDS = (
    "table_no", "room_name", "problem", "region",
    "cause", "solution", "resolver", "creator",
)

# 导出表头：与原 Excel 对齐 + 系统附加列
_EXPORT_HEADERS = (
    ("issue_type", "类型"), ("room_name", "球房"), ("table_no", "桌号"),
    ("region", "地区"), ("problem", "问题"), ("cause", "发生原因"),
    ("resolved", "是否解决"), ("solution", "解决方案"), ("resolver", "解决人"),
    ("response_time", "响应时间"), ("created_at", "填写时间"),
    ("creator", "填写人"), ("cycle_start", "周期"),
)


# ==================== 周期计算（周二 ~ 周一） ====================

def cycle_start_of(dt: datetime) -> str:
    """计算给定时间所属周期的起始日（最近的周二，含当天），格式 yyyy/MM/dd

    weekday(): 周一=0 ... 周二=1；(weekday - 1) % 7 即距周二的天数。
    """
    days_since_tue = (dt.weekday() - 1) % 7
    start = dt - timedelta(days=days_since_tue)
    return start.strftime("%Y/%m/%d")


def current_cycle_start() -> str:
    """当前周期起始日"""
    return cycle_start_of(datetime.now())


def cycle_label(cycle_start: str) -> str:
    """周期展示标签：'08/19 - 08/25'（起始周二 + 6 天至周一）"""
    try:
        start = datetime.strptime(str(cycle_start).strip(), "%Y/%m/%d")
    except (ValueError, TypeError):
        return str(cycle_start or "")
    end = start + timedelta(days=6)
    return f"{start:%m/%d} - {end:%m/%d}"


# ==================== 连接与工具 ====================

def _conn():
    """复用 table_db 双后端连接（SQLite 单连接 / MySQL thread-local）"""
    return table_db.get_conn()


def _lookup_table_binding(table_no: str) -> tuple:
    """按桌号精确匹配球桌管理库，返回 (snk_code, device_code)

    桌号自由文本允许非标格式（多桌/手误），匹配不到返回空串不阻断。
    device_code 取该球桌最近一期 kd 状态记录。
    """
    name = str(table_no or "").strip()
    if not name:
        return "", ""
    snk = table_db.get_snk_by_name(name)
    device = ""
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT device_code FROM kd_status WHERE TRIM(table_id) = ? "
            "ORDER BY file_path DESC LIMIT 1", (name,)).fetchone()
        device = str(row[0] or "").strip() if row else ""
    except Exception:
        pass
    return str(snk or ""), device


# ==================== 增删改 ====================

def insert_record(record: dict) -> int:
    """新增售后记录，返回新记录 id

    created_at/cycle_start 自动计算；snk_code/device_code 未提供时
    按桌号精确匹配球桌管理库自动带出。
    """
    now = datetime.now()
    created_at = now.strftime("%Y-%m-%d %H:%M:%S")
    cycle = cycle_start_of(now)
    snk = str(record.get("snk_code") or "").strip()
    device = str(record.get("device_code") or "").strip()
    if not snk:
        snk, device = _lookup_table_binding(record.get("table_no"))
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO aftersale_records "
        "(created_at, creator, issue_type, table_no, room_name, region, "
        "problem, cause, resolved, is_initiative, is_our_problem, solution, "
        "resolver, response_time, snk_code, device_code, cycle_start) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (created_at,
         str(record.get("creator") or ""),
         str(record.get("issue_type") or ""),
         str(record.get("table_no") or ""),
         str(record.get("room_name") or ""),
         str(record.get("region") or ""),
         str(record.get("problem") or ""),
         str(record.get("cause") or ""),
         str(record.get("resolved") or "否"),
         str(record.get("is_initiative") or "否"),
         str(record.get("is_our_problem") or "是"),
         str(record.get("solution") or ""),
         str(record.get("resolver") or ""),
         str(record.get("response_time") or ""),
         snk, device, cycle))
    conn.commit()
    return cur.lastrowid


def update_record(record: dict) -> int:
    """按 id 更新记录（created_at/cycle_start 保留原值），返回受影响行数

    多人场景：MySQL autocommit 提交后其他用户刷新即可见。
    """
    rec_id = record.get("id")
    if not rec_id:
        return 0
    # 编辑不改动填写时间；cycle_start 若缺失按 created_at 重算保证一致
    created_at = str(record.get("created_at") or "")
    cycle = str(record.get("cycle_start") or "")
    if not cycle and created_at:
        try:
            cycle = cycle_start_of(
                datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            cycle = ""
    snk = str(record.get("snk_code") or "").strip()
    device = str(record.get("device_code") or "").strip()
    if not snk:
        snk, device = _lookup_table_binding(record.get("table_no"))
    conn = _conn()
    cur = conn.execute(
        "UPDATE aftersale_records SET creator=?, issue_type=?, table_no=?, "
        "room_name=?, region=?, problem=?, cause=?, resolved=?, "
        "is_initiative=?, is_our_problem=?, solution=?, "
        "resolver=?, response_time=?, snk_code=?, device_code=?, cycle_start=? "
        "WHERE id=?",
        (str(record.get("creator") or ""),
         str(record.get("issue_type") or ""),
         str(record.get("table_no") or ""),
         str(record.get("room_name") or ""),
         str(record.get("region") or ""),
         str(record.get("problem") or ""),
         str(record.get("cause") or ""),
         str(record.get("resolved") or "否"),
         str(record.get("is_initiative") or "否"),
         str(record.get("is_our_problem") or "是"),
         str(record.get("solution") or ""),
         str(record.get("resolver") or ""),
         str(record.get("response_time") or ""),
         snk, device, cycle, rec_id))
    conn.commit()
    return cur.rowcount


def delete_record(rec_id) -> int:
    """按 id 删除记录，返回受影响行数"""
    if not rec_id:
        return 0
    conn = _conn()
    cur = conn.execute("DELETE FROM aftersale_records WHERE id = ?", (rec_id,))
    conn.commit()
    return cur.rowcount


# ==================== 查询 ====================

def _build_where(keyword: str, cycle_start: str, issue_type: str,
                 resolved: str) -> tuple:
    """构造筛选 WHERE 子句与参数（周期/类型/状态/关键词）

    统计口径说明：resolved 为空才参与筛选；统计函数单独调用时
    传空串即得「已解决/未解决」分组基数。
    """
    conds, params = [], []
    if cycle_start:
        conds.append("cycle_start = ?")
        params.append(str(cycle_start).strip())
    if issue_type:
        conds.append("issue_type = ?")
        params.append(str(issue_type).strip())
    if resolved:
        conds.append("resolved = ?")
        params.append(str(resolved).strip())
    kw = str(keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        kw_cond = " OR ".join([f"{f} LIKE ?" for f in _SEARCH_FIELDS])
        conds.append(f"({kw_cond})")
        params.extend([like] * len(_SEARCH_FIELDS))
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    return where, params


def query_page(page_no: int, page_size: int, keyword: str = "",
               cycle_start: str = "", issue_type: str = "",
               resolved: str = "") -> tuple:
    """分页查询售后记录，返回 (total, rows)"""
    conn = _conn()
    where, params = _build_where(keyword, cycle_start, issue_type, resolved)
    total = conn.execute(
        f"SELECT COUNT(*) FROM aftersale_records{where}", params).fetchone()[0]
    offset = (max(1, page_no) - 1) * page_size
    cur = conn.execute(
        f"SELECT id, {', '.join(RECORD_FIELDS)} FROM aftersale_records"
        f"{where} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [page_size, offset])
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return total, rows


def query_with_stats(page_no: int, page_size: int, keyword: str = "",
                     cycle_start: str = "", issue_type: str = "",
                     resolved: str = "") -> tuple:
    """分页查询 + 同口径统计，返回 (total, rows, stats)

    stats 统计不带 resolved 筛选（否则已解决/未解决计数退化），
    展示「共 X · 已解决 Y · 未解决 Z」。
    """
    total, rows = query_page(page_no, page_size, keyword, cycle_start,
                             issue_type, resolved)
    conn = _conn()
    where, params = _build_where(keyword, cycle_start, issue_type, "")
    base = conn.execute(
        f"SELECT COUNT(*), SUM(CASE WHEN resolved = '是' THEN 1 ELSE 0 END) "
        f"FROM aftersale_records{where}", params).fetchone()
    n_all = int(base[0] or 0)
    n_resolved = int(base[1] or 0)
    stats = {"total": n_all, "resolved": n_resolved,
             "unresolved": n_all - n_resolved}
    return total, rows, stats


def get_cycle_options() -> list:
    """历史周期选项（distinct cycle_start 降序，含当前周期）"""
    conn = _conn()
    cur = conn.execute(
        "SELECT DISTINCT cycle_start FROM aftersale_records "
        "WHERE cycle_start != '' ORDER BY cycle_start DESC")
    cycles = [r[0] for r in cur.fetchall()]
    current = current_cycle_start()
    if current not in cycles:
        cycles.insert(0, current)
    return cycles


def get_field_candidates() -> dict:
    """动态候选：问题/解决人/地区（按使用频次降序，各取前 60）"""
    conn = _conn()
    out = {}
    for key, field in (("problems", "problem"),
                       ("resolvers", "resolver"),
                       ("regions", "region")):
        cur = conn.execute(
            f"SELECT {field}, COUNT(*) FROM aftersale_records "
            f"WHERE {field} != '' GROUP BY {field} "
            f"ORDER BY COUNT(*) DESC LIMIT 60")
        out[key] = [r[0] for r in cur.fetchall()]
    # 问题候选合并预置常见项（新库无历史数据时下拉不为空）
    if not out["problems"]:
        out["problems"] = ["主机没有开机", "遥控器没反应", "程序没了",
                           "不能扫码", "识别不了", "记分牌显示不出来"]
    return out


# ==================== 导出 / 导入 ====================

def export_xlsx(path: str, keyword: str = "", cycle_start: str = "",
                issue_type: str = "", resolved: str = "") -> int:
    """按筛选条件导出全部记录（不分页）为 xlsx，返回导出条数

    表头与 售后问题汇总8月.xlsx 对齐，附加 填写时间/填写人/周期 三列。
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    conn = _conn()
    where, params = _build_where(keyword, cycle_start, issue_type, resolved)
    cur = conn.execute(
        f"SELECT {', '.join(RECORD_FIELDS)} FROM aftersale_records"
        f"{where} ORDER BY id DESC", params)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    wb = Workbook()
    ws = wb.active
    ws.title = "售后记录"
    # 表头样式：加粗 + 浅青底
    header_font = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="D9F2F4")
    for c, (_key, header) in enumerate(_EXPORT_HEADERS, 1):
        cell = ws.cell(row=1, column=c, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
    # 数据行（周期列展示范围标签）
    for r, rec in enumerate(rows, 2):
        for c, (key, _h) in enumerate(_EXPORT_HEADERS, 1):
            val = rec.get(key) or ""
            if key == "cycle_start" and val:
                val = cycle_label(val)
            ws.cell(row=r, column=c, value=str(val))
    # 列宽与冻结首行
    widths = (12, 24, 10, 8, 28, 30, 10, 30, 10, 12, 18, 10, 16)
    for c, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(row=1, column=c).column_letter].width = w
    ws.freeze_panes = "A2"

    out_dir = os.path.dirname(os.path.abspath(path))
    os.makedirs(out_dir, exist_ok=True)
    wb.save(path)
    return len(rows)


def import_excel_rows(xlsx_path: str) -> int:
    """一次性导入历史 Excel（售后问题汇总 格式），返回导入条数

    规则：
    - 表头按中文名定位（类型/球房/桌号/地区/问题/发生原因/是否解决/解决方案/解决人/响应时间）
    - 类型列在 Excel 中为分组首行标记，逐行向下填充
    - 跳过全空行；是否解决空值默认「否」
    - created_at 填导入时间，creator 标记「Excel导入」
    """
    from openpyxl import load_workbook

    wb = load_workbook(xlsx_path, data_only=True)
    ws = wb.worksheets[0]  # 取第一个工作表（Sheet1 正式表）

    # 表头定位：首行按中文名匹配列号
    header_map = {}
    for c in range(1, ws.max_column + 1):
        h = str(ws.cell(row=1, column=c).value or "").strip()
        header_map[h] = c
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
    cycle = cycle_start_of(now)
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
        data.append((
            created_at, "Excel导入", issue_type, table_no, room,
            _val(r, "地区"), problem, _val(r, "发生原因"), resolved,
            _val(r, "解决方案"), _val(r, "解决人"), _val(r, "响应时间"),
            "", "", cycle))
    if not data:
        return 0
    conn = _conn()
    conn.executemany(
        "INSERT INTO aftersale_records "
        "(created_at, creator, issue_type, table_no, room_name, region, "
        "problem, cause, resolved, solution, resolver, response_time, "
        "snk_code, device_code, cycle_start) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", data)
    conn.commit()
    return len(data)
