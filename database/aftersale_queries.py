# -*- coding: utf-8 -*-
"""售后查询执行层（A3 queries 第二步）。

从 database/aftersale_db.py 下沉：周期筛选拼接、分页/统计/排行/周期下拉。
查询 SQL 构造在 database/aftersale_query_sql.py；本模块只负责执行与结果整形。
周期物化/模式读取仍由 aftersale_db 持有，函数内延迟取用，保证既有
monkeypatch 点（adb.load_cycle_mode / adb.recalc_cycle_starts）继续生效，
避免顶层循环 import。
"""

from datetime import datetime

from database.aftersale_query_sql import (
    _RECORD_DATE_EXPR, _build_order, _build_where)


def _adb():
    """延迟取 aftersale_db（避免顶层循环 import）。"""
    from database import aftersale_db
    return aftersale_db


def _conn():
    """连接由 aftersale_db 注入（测试/压测 monkeypatch adb._conn 继续生效）。"""
    return _adb()._conn()


def _record_fields():
    """记录字段元组（由 aftersale_db 单一维护）。"""
    return _adb().RECORD_FIELDS


def _append_cycle_where(where: str, params: list, cycle_start: str) -> tuple:
    """把周期筛选并入 WHERE，返回 (where, params)

    S3（2026-09-06）：由 _RECORD_DATE_EXPR 表达式范围过滤（索引失效，
    EXPLAIN=SCAN，每次加载 3 条全表扫）改为 cycle_start 物化列等值过滤，
    idx_aftersale_cycle 两端索引直接可用。传入非当前模式真实周期起点
    时追加 1=0（与旧实现「非起点返回 0 条」等价，防止周/月口径混用），
    无法解析的串不追加过滤（与旧实现一致）；物化列由 insert_record
    落库维护 + 周期保存/首次加载重算保证一致，存量空值行先经
    _ensure_cycle_materialized 幂等回填，旧数据不漏。
    """
    cyc = str(cycle_start or "").strip()
    if not cyc:
        return where, params
    valid = _adb()._cycle_is_valid_start(cyc)
    if valid is None:
        return where, params
    if not valid:
        # 非本模式真实周期起点 → 无记录命中（与旧实现 total=0 等价）
        where += (" AND " if where else " WHERE ") + " 1 = 0"
        return where, params
    _adb()._ensure_cycle_materialized()
    where += (" AND " if where else " WHERE ") + "cycle_start = ?"
    return where, params + [cyc]






def query_page(page_no: int, page_size: int, keyword: str = "",
               cycle_start: str = "", issue_type: str = "",
               resolved: str = "", is_initiative: str = "",
               is_our_problem: str = "", sort_key: str = "",
               sort_order: str = "desc") -> tuple:
    """分页查询售后记录，返回 (total, rows)

    周期筛选走 cycle_start 物化列等值过滤，COUNT + LIMIT/OFFSET 在
    数据库侧完成；排序也下沉到 SQL，跨页排序才与全量数据一致。
    sort_key/sort_order 见 _LIST_SORT，未知键按 id DESC 返回。
    """
    conn = _conn()
    where, params = _build_where(keyword, issue_type, resolved,
                                 is_initiative, is_our_problem)
    where, params = _append_cycle_where(where, params, cycle_start)
    total = conn.execute(
        f"SELECT COUNT(*) FROM aftersale_records{where}", params).fetchone()[0]
    offset = (max(1, page_no) - 1) * page_size
    order = _build_order(sort_key, sort_order)
    cur = conn.execute(
        f"SELECT id, {', '.join(_record_fields())} FROM aftersale_records"
        f"{where} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [int(page_size), offset])
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return int(total or 0), rows


def query_with_stats(page_no: int, page_size: int, keyword: str = "",
                     cycle_start: str = "", issue_type: str = "",
                     resolved: str = "", is_initiative: str = "",
                     is_our_problem: str = "", sort_key: str = "",
                     sort_order: str = "desc") -> tuple:
    """分页查询 + 同口径统计，返回 (total, rows, stats)

    列表与统计共用筛选口径；排序参数只影响列表，统计恒为全量聚合。
    统计不带 resolved/is_* 筛选，否则已解决/未解决计数会退化。
    """
    total, rows = query_page(page_no, page_size, keyword, cycle_start,
                             issue_type, resolved, is_initiative,
                             is_our_problem, sort_key, sort_order)
    conn = _conn()
    # 统计与列表同口径（keyword/issue_type/周期；不带 resolved/initiative/
    # our_problem 筛选）。单次 SQL 聚合替代旧实现的「二次全表取回 + Python
    # 逐条 sum」——压测 100k 下该路径 439.5ms → 毫秒级。
    where, params = _build_where(keyword, issue_type, "")
    where, params = _append_cycle_where(where, params, cycle_start)
    row = conn.execute(
        "SELECT COUNT(*), SUM(resolved = '是'), SUM(is_initiative = '是'), "
        "SUM(is_our_problem = '是') FROM aftersale_records" + where,
        params).fetchone()
    n_all = int(row[0] or 0)
    n_resolved = int(row[1] or 0)
    n_init = int(row[2] or 0)
    n_our = int(row[3] or 0)
    rate = int(round(n_resolved * 100 / n_all)) if n_all else 0
    stats = {"total": n_all, "resolved": n_resolved,
             "unresolved": n_all - n_resolved,
             "initiative": n_init, "our_problem": n_our, "rate": rate}
    return total, rows, stats


def query_stats_detail(keyword: str = "", cycle_start: str = "",
                       issue_type: str = "", trend_start: str = "",
                       trend_end: str = "") -> dict:
    """售后统计弹窗详细统计（与四卡片/列表完全同口径）

    与 query_with_stats 一致：统计不带 resolved/is_initiative/
    is_our_problem 筛选（否则已解决/未解决计数退化），仅按
    keyword + issue_type + cycle_start（按记录时间动态归属）过滤。
    返回：
    - summary: {total, resolved, unresolved, rate, initiative, our_problem}
    - daily  : [{"date": "2026-08-18", "count": n, "resolved": n}, ...]
               按日升序；trend_start/trend_end（"YYYY-MM-DD"）仅过滤该序列
    - regions: [{"region": "广东", "count": n}, ...] 按数量降序
    - types  : [{"issue_type": "硬件问题", "count": n, "resolved": n,
                 "unresolved": n}, ...] 按数量降序

    性能（P2，2026-08-28）：summary/daily/regions/types 全部 SQL 聚合
    （GROUP BY + SUM(CASE)），周期筛选 SQL 化（_append_cycle_where）——
    旧实现全表取回 7 字段 + Python 逐条聚合，100k 下每次弹窗打开数百毫秒。
    """
    conn = _conn()
    where, params = _build_where(keyword, issue_type, "")
    where, params = _append_cycle_where(where, params, cycle_start)

    # summary：单条 SQL 聚合
    row = conn.execute(
        "SELECT COUNT(*), SUM(resolved = '是'), SUM(is_initiative = '是'), "
        "SUM(is_our_problem = '是') FROM aftersale_records" + where,
        params).fetchone()
    n_all = int(row[0] or 0)
    n_resolved = int(row[1] or 0)
    n_init = int(row[2] or 0)
    n_our = int(row[3] or 0)
    rate = int(round(n_resolved * 100 / n_all)) if n_all else 0
    summary = {"total": n_all, "resolved": n_resolved,
               "unresolved": n_all - n_resolved, "rate": rate,
               "initiative": n_init, "our_problem": n_our}

    date_col = _RECORD_DATE_EXPR
    # 每日趋势：按记录日期聚合（occurred_at 优先回退 created_at），
    # trend_start/trend_end 仅作用于 daily 序列
    daily_where, daily_params = where, list(params)
    if trend_start:
        daily_where += (" AND " if daily_where else " WHERE ") + \
                       f"{date_col} >= ?"
        daily_params.append(str(trend_start))
    if trend_end:
        daily_where += (" AND " if daily_where else " WHERE ") + \
                       f"{date_col} <= ?"
        daily_params.append(str(trend_end))
    rows = conn.execute(
        f"SELECT {date_col} AS d, COUNT(*), SUM(resolved = '是') "
        f"FROM aftersale_records{daily_where} GROUP BY {date_col} "
        f"ORDER BY d ASC", daily_params).fetchall()
    daily = [{"date": str(d or ""), "count": int(c), "resolved": int(r or 0)}
             for d, c, r in rows if d]

    # 按地区聚合（数量降序；count 并列时按地区名稳定排序）
    rows = conn.execute(
        "SELECT region, COUNT(*) FROM aftersale_records" + where +
        " GROUP BY region ORDER BY COUNT(*) DESC, region ASC", params).fetchall()
    regions = [{"region": str(r or "").strip() or "未填地区", "count": int(c)}
               for r, c in rows]

    # 按问题类型聚合（数量降序；count 并列时按类型名稳定排序）
    rows = conn.execute(
        "SELECT issue_type, COUNT(*), SUM(resolved = '是') "
        "FROM aftersale_records" + where +
        " GROUP BY issue_type ORDER BY COUNT(*) DESC, issue_type ASC",
        params).fetchall()
    types = [{"issue_type": str(t or "").strip() or "未分类",
              "count": int(c), "resolved": int(rd or 0),
              "unresolved": int(c) - int(rd or 0)}
             for t, c, rd in rows]
    return {"summary": summary, "daily": daily,
            "regions": regions, "types": types}


# ==================== 球房/球桌售后排行 ====================

# 排行排序白名单：sort 键 → ORDER BY 片段（SELECT 别名排序，SQLite/MySQL
# 均支持；与 Web 端 /api/stats/rank 的 _RANK_SORT 同口径，并列时按名称稳定排序）
_RANK_SORT = {
    "total": "total DESC, rn ASC",
    "total_asc": "total ASC, rn ASC",
    "unresolved": "unres DESC, total DESC, rn ASC",
    "our_problem": "ourp DESC, total DESC, rn ASC",
    "last_occurred": "lastd DESC, total DESC, rn ASC",
}


def query_rank(level: str = "room", limit: int = 10, sort: str = "total",
               start: str = "", end: str = "", cycle_start: str = "",
               resolved: str = "", is_initiative: str = "",
               is_our_problem: str = "", region: str = "",
               room_name: str = "", keyword: str = "") -> dict:
    """球房/球桌售后排行（与 Web 端 /api/stats/rank 同口径）

    口径：
    - WHERE 复用 _build_where（keyword/resolved/is_initiative/is_our_problem
      + deleted=0 软删除隔离）+ _append_cycle_where（账期物化列），追加
      region / room_name（下钻，TRIM 精确）/ start~end（记录日期区间，
      _RECORD_DATE_EXPR 字符串比较，跨 SQLite/MySQL 一致）
    - 排行排除未填球房（球桌级同时排除未填桌号）的记录；球桌级联合名
      「球房 · 桌号」在 Python 侧拼装（避开 CONCAT/|| 双后端方言差异）
    - 每组一次 SQL 算齐 总量/未解决/我方问题/主动发起/最近发生
      （SUM 布尔表达式 + MAX 日期，与 query_stats_detail 同范式）
    - summary 反映同 WHERE 全量口径（不受 TOP N 截断影响）：
      total/unresolved/覆盖球房（DISTINCT 非空球房）/覆盖球桌
      （DISTINCT 非空球房+桌号对，经去重子查询——COUNT(DISTINCT) 多表达式
      与 CONCAT 均为 MySQL 专属，子查询形式两端通用）
    - share = 组总量 × 100 ÷ summary.total（整数百分比，四舍五入）

    返回 {"rows": [{rank, room_name, table_no, name, total, share,
    unresolved, our_problem, initiative, last_occurred}, ...],
    "summary": {total, unresolved, rooms, tables}}。
    """
    if level not in ("room", "table"):
        raise ValueError("level 仅支持 room/table")
    order = _RANK_SORT.get(sort) or _RANK_SORT["total"]
    conn = _conn()
    where, params = _build_where(keyword, "", resolved, is_initiative,
                                 is_our_problem)
    where, params = _append_cycle_where(where, params, cycle_start)
    region_v = str(region or "").strip()
    if region_v:
        where += " AND TRIM(region) = ?"
        params.append(region_v)
    drill = str(room_name or "").strip()
    if drill:
        where += " AND TRIM(room_name) = ?"
        params.append(drill)
    start_v = str(start or "").strip()
    end_v = str(end or "").strip()
    if start_v:
        where += f" AND {_RECORD_DATE_EXPR} >= ?"
        params.append(start_v)
    if end_v:
        where += f" AND {_RECORD_DATE_EXPR} <= ?"
        params.append(end_v)

    # 排行：排除未填球房（球桌级再排除未填桌号）
    where_rank = where + " AND TRIM(room_name) != ''"
    if level == "table":
        where_rank += " AND TRIM(table_no) != ''"
    if level == "table":
        sel = "TRIM(room_name) AS rn, TRIM(table_no) AS tn"
        group = "TRIM(room_name), TRIM(table_no)"
    else:
        sel = "TRIM(room_name) AS rn, '' AS tn"
        group = "TRIM(room_name)"
    cur = conn.execute(
        f"SELECT {sel}, COUNT(*) AS total, "
        f"SUM(resolved = '否') AS unres, "
        f"SUM(is_our_problem = '是') AS ourp, "
        f"SUM(is_initiative = '是') AS init_, "
        f"MAX({_RECORD_DATE_EXPR}) AS lastd "
        f"FROM aftersale_records{where_rank} "
        f"GROUP BY {group} ORDER BY {order} LIMIT ?",
        list(params) + [max(1, int(limit))])

    # summary：同 WHERE 全量口径（三条轻量聚合，均不受 TOP N 截断影响）
    row = conn.execute(
        "SELECT COUNT(*), SUM(resolved = '否'), "
        "COUNT(DISTINCT CASE WHEN TRIM(room_name) != '' "
        f"THEN TRIM(room_name) END) FROM aftersale_records{where}",
        params).fetchone()
    total_n = int(row[0] or 0)
    unresolved_n = int(row[1] or 0)
    rooms_n = int(row[2] or 0)
    row = conn.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT TRIM(room_name), "
        f"TRIM(table_no) FROM aftersale_records{where} "
        "AND TRIM(room_name) != '' AND TRIM(table_no) != '') t",
        params).fetchone()
    tables_n = int(row[0] or 0)

    rows = []
    for i, r in enumerate(cur.fetchall(), 1):
        rn, tn = str(r[0] or ""), str(r[1] or "")
        n = int(r[2] or 0)
        rows.append({
            "rank": i, "room_name": rn, "table_no": tn,
            "name": f"{rn} · {tn}" if level == "table" else rn,
            "total": n,
            "share": int(round(n * 100 / total_n)) if total_n else 0,
            "unresolved": int(r[3] or 0),
            "our_problem": int(r[4] or 0),
            "initiative": int(r[5] or 0),
            "last_occurred": str(r[6] or ""),
        })
    return {"rows": rows,
            "summary": {"total": total_n, "unresolved": unresolved_n,
                        "rooms": rooms_n, "tables": tables_n}}


def get_cycle_options() -> list:
    """周期下拉选项：库中记录实际归属的周期（按 occurred_at 动态计算，

    缺失回退 created_at），按起始日降序。仅返回库中确实存在数据的周期，
    不额外插入库中不存在的当前周期（当前周期无数据则不出现）。

    性能（P2，2026-08-28）：周期是记录日期的函数，故只需取 DISTINCT 日期
    （最多几百个唯一值）再逐个映射周期——旧实现全表取回 (occurred_at,
    created_at) 两列并在 Python 逐行算周期，100k 下每次面板加载数百毫秒。
    """
    from datetime import datetime
    conn = _conn()
    cur = conn.execute(
        f"SELECT DISTINCT {_RECORD_DATE_EXPR} AS d FROM aftersale_records")
    cycles = set()
    for (d,) in cur.fetchall():
        if not d:
            continue
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            continue
        c = _adb().cycle_start_of(dt)
        if c:
            cycles.add(c)
    cycles.discard(None)
    return sorted(cycles, reverse=True)
