# -*- coding: utf-8 -*-
"""售后面板 Web 后端（只读版）—— 对齐桌面端 database/aftersale_db 查询口径

只提供只读接口，写操作仍走桌面端。MySQL 凭据经 .env（chmod 600）注入。
周期口径与桌面端一致：
- 周期模式 tue/mon/custom/month（默认 tue=自然周，周二为起点，span=7）
- 记录归属周期用 _RECORD_DATE_EXPR = substr(COALESCE(NULLIF(occurred_at,''),created_at),1,10)
- 非当前模式合法周期起点 → 该筛选命中 0 条（与桌面端 WHERE 1=0 等价）
"""
import os
from datetime import datetime, timedelta

import pymysql
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="aftersale-web", docs_url=None, redoc_url=None, openapi_url=None)
# 生产同源（nginx 反代），开发可放开 origin；GET 只读
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])

DB = dict(
    host=os.getenv("MYSQL_HOST", "127.0.0.1"),
    port=int(os.getenv("MYSQL_PORT", "3306")),
    user=os.getenv("MYSQL_USER", "aftersale_ro"),
    password=os.getenv("MYSQL_PASS", ""),
    database=os.getenv("MYSQL_DB", "autowork"),
    charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
)

# 记录日期表达式（与桌面端 _RECORD_DATE_EXPR 一致，两方言通用）
DATE_EXPR = "substr(COALESCE(NULLIF(occurred_at,''),created_at),1,10)"


def _db():
    return pymysql.connect(**DB)


def _cycle_range(cycle_start: str) -> tuple | None:
    """周期起点 yyyy/MM/dd → [start_iso, end_iso)；None=非法起点（0 条）。"""
    try:
        start = datetime.strptime(str(cycle_start).strip(), "%Y/%m/%d")
    except (ValueError, TypeError):
        return None
    mode = os.getenv("CYCLE_TYPE", "tue")
    if mode == "month":
        import calendar
        span = calendar.monthrange(start.year, start.month)[1]
    elif mode == "custom":
        try:
            span = max(1, int(os.getenv("CYCLE_SPAN", "7")))
        except (TypeError, ValueError):
            span = 7
    else:  # tue / mon 自然周
        span = 7
    # 起点合法性：tue=周二(weekday1) mon=周一(weekday0)
    wd = start.weekday()
    if (mode == "tue" and wd != 1) or (mode == "mon" and wd != 0):
        return None
    return start.strftime("%Y-%m-%d"), (start + timedelta(days=span)).strftime("%Y-%m-%d")


def _build_where(keyword: str, issue_type: str, resolved: str,
                 is_initiative: str, is_our_problem: str, cycle_start: str):
    conds, params = [], []
    if issue_type:
        conds.append("issue_type = %s"); params.append(str(issue_type).strip())
    if resolved:
        conds.append("resolved = %s"); params.append(str(resolved).strip())
    if is_initiative:
        conds.append("is_initiative = %s"); params.append(str(is_initiative).strip())
    if is_our_problem:
        conds.append("is_our_problem = %s"); params.append(str(is_our_problem).strip())
    if keyword:
        k = f"%{str(keyword).strip()}%"
        conds.append("(table_no LIKE %s OR room_name LIKE %s OR problem LIKE %s OR "
                     "cause LIKE %s OR solution LIKE %s OR creator LIKE %s)")
        params += [k] * 6
    cyc = _cycle_range(cycle_start) if cycle_start else None
    if cycle_start and cyc is None:
        conds.append("1 = 0")  # 非当前模式合法起点 → 0 条（对齐桌面端）
    elif cyc:
        conds.append(f"{DATE_EXPR} >= %s AND {DATE_EXPR} < %s")
        params += list(cyc)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    return where, params


@app.get("/api/health")
def health():
    return {"ok": True, "db": os.getenv("MYSQL_DB", "autowork")}


@app.get("/api/cycle-options")
def cycle_options():
    """最近 12 个周期起点（含当前）供下拉；仅支持当前模式合法起点。"""
    mode = os.getenv("CYCLE_TYPE", "tue")
    now = datetime.now()
    wd = now.weekday()
    anchor = wd if (mode == "tue" and wd >= 1) or (mode == "mon" and wd >= 0) else None
    # 简化：tue 模式取本周二，mon 取本周一，向前推 12 个
    if mode == "tue":
        delta = (wd - 1) % 7
        cur = now - timedelta(days=delta)
    elif mode == "mon":
        delta = wd % 7
        cur = now - timedelta(days=delta)
    elif mode == "month":
        cur = now.replace(day=1)
    else:  # custom 按天 span
        return {"options": [now.strftime("%Y/%m/%d")], "type": mode}
    opts = []
    for i in range(12):
        opts.append(cur.strftime("%Y/%m/%d"))
        cur -= timedelta(days=7 if mode in ("tue", "mon") else (cur.day))
        if mode == "month":
            cur = cur.replace(day=1)
    return {"options": opts, "type": mode, "current": opts[0]}


@app.get("/api/records")
def records(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    keyword: str = "", cycle_start: str = "", issue_type: str = "",
    resolved: str = "", is_initiative: str = "", is_our_problem: str = "",
):
    """分页列表 + 统计一次返回（与桌面端 query_with_stats 同口径）"""
    where, params = _build_where(keyword, issue_type, resolved,
                                 is_initiative, is_our_problem, cycle_start)
    order = "ORDER BY created_at DESC"
    with _db() as c:
        with c.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) n FROM aftersale_records{where}", params)
            total = cur.fetchone()["n"]
            cur.execute(
                f"SELECT * FROM aftersale_records{where} {order} LIMIT %s OFFSET %s",
                params + [page_size, (page - 1) * page_size])
            rows = cur.fetchall()
            # 统计（全局口径，与桌面端 stats 一致：不按周期过滤）
            def cnt(extra: str):
                cur.execute(f"SELECT COUNT(*) n FROM aftersale_records WHERE {extra}")
                return cur.fetchone()["n"]
            stats = {
                "total": cnt("1=1"),
                "unresolved": cnt("resolved = '否'"),
                "initiative": cnt("is_initiative = '是'"),
                "our_problem": cnt("is_our_problem = '是'"),
            }
    return {"total": total, "rows": rows, "stats": stats,
            "page": page, "page_size": page_size}


@app.get("/api/table-columns")
def table_columns():
    """表格列定义（与桌面端 TABLE_COLUMNS 同构，前端据此渲染）"""
    return {"columns": [
        {"key": "created_at", "label": "填写时间", "width": 128},
        {"key": "occurred_at", "label": "发生时间", "width": 128},
        {"key": "issue_type", "label": "类型", "width": 90},
        {"key": "location", "label": "位置", "width": 200},
        {"key": "problem", "label": "问题", "width": 200},
        {"key": "cause", "label": "发生原因", "width": 180},
        {"key": "solution", "label": "解决方案", "width": 180},
        {"key": "resolved", "label": "解决", "width": 70},
        {"key": "is_our_problem", "label": "我们问题", "width": 70},
        {"key": "is_initiative", "label": "主动发起", "width": 70},
        {"key": "response_time", "label": "响应", "width": 110},
    ]}
