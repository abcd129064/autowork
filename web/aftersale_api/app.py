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
            # 统计：与列表同一筛选口径（KPI 跟随筛选变化）
            def cnt(extra: str):
                cond = f"({extra})"
                w = (where + " AND " + cond) if where else f" WHERE {cond}"
                cur.execute(f"SELECT COUNT(*) n FROM aftersale_records{w}", params)
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

from fastapi import Depends

# ===== PHASE-2 APPEND: auth + write APIs (gated) =====
import json as _json, time as _time
from datetime import datetime as _dt
try:
    import bcrypt as _bcrypt
except ImportError: _bcrypt = None
try:
    import jwt as _jwt
except ImportError: _jwt = None
from fastapi import Header, HTTPException, Body

# 可写字段白名单（与 DESC 表对齐；id/created_at/updated_at 由系统控制）
_WRITABLE = {"creator","issue_type","table_no","room_name","region","problem",
             "cause","resolved","solution","resolver","response_time","snk_code",
             "device_code","cycle_start","is_initiative","is_our_problem",
             "occurred_at","is_important"}

def _write_enabled() -> bool: return os.getenv("WRITE_ENABLED", "false").lower() == "true"
def _auth_enabled() -> bool: return os.getenv("AUTH_ENABLED", "false").lower() == "true"
def _auth_required(): 
    if not _auth_enabled(): raise HTTPException(503, "auth not enabled")
    if not _jwt: raise HTTPException(500, "pyjwt missing")
    if not _bcrypt: raise HTTPException(500, "bcrypt missing")

def _load_users():
    p = "/opt/aftersale-web/users.json"
    if not os.path.exists(p): return {}
    try:
        with open(p, encoding="utf-8") as f: return _json.load(f)
    except Exception: return {}

@app.post("/api/auth/login")
def auth_login(payload: dict = Body(...)):
    _auth_required()
    u, pw = payload.get("username",""), payload.get("password","")
    users = _load_users()
    rec = users.get(u)
    if not rec or not _bcrypt.checkpw(pw.encode(), rec["pw_hash"].encode()):
        raise HTTPException(401, "invalid credentials")
    token = _jwt.encode({"u": u, "exp": _time.time() + 12*3600}, os.environ["AUTH_SECRET"], algorithm="HS256")
    return {"token": token, "user": u}

def require_auth(authorization: str = Header(default="")):
    if not _auth_enabled(): return  # 关闭时不要求
    if not authorization.startswith("Bearer "): raise HTTPException(401, "missing token")
    try:
        data = _jwt.decode(authorization[7:], os.environ["AUTH_SECRET"], algorithms=["HS256"])
        return data["u"]
    except Exception:
        raise HTTPException(401, "invalid token")

@app.get("/api/auth/me")
def auth_me(user = Depends(require_auth)):
    return {"user": user}

@app.post("/api/records")
def create_record(rec: dict = Body(...), user = Depends(require_auth)):
    if not _write_enabled(): raise HTTPException(503, "write not enabled")
    fields, values = [], []
    for k in _WRITABLE:
        if k in rec and rec[k] is not None:
            fields.append(k); values.append(rec[k])
    if not fields: raise HTTPException(400, "no writable fields")
    if "creator" not in fields:  # 强制记录创建人
        fields.append("creator"); values.append(user)
    placeholders = ",".join(["%s"]*len(fields))
    cols = ",".join(fields)
    with _db() as c, c.cursor() as cur:
        cur.execute(f"INSERT INTO aftersale_records ({cols}) VALUES ({placeholders})", values)
        rid = cur.lastrowid
    return {"id": rid, "creator": user}

@app.put("/api/records/{rid}")
def update_record(rid: int, rec: dict = Body(...), user = Depends(require_auth)):
    if not _write_enabled(): raise HTTPException(503, "write not enabled")
    sets, values = [], []
    for k in _WRITABLE:
        if k in rec and rec[k] is not None:
            sets.append(f"{k}=%s"); values.append(rec[k])
    if not sets: raise HTTPException(400, "no fields")
    values += [rid]
    client_updated_at = rec.get("updated_at")  # 乐观锁：客户端传读取时的时间戳
    if client_updated_at:
        values.append(client_updated_at)
        sql = f"UPDATE aftersale_records SET {','.join(sets)}, updated_at=NOW() WHERE id=%s AND updated_at=%s"
    else:
        sql = f"UPDATE aftersale_records SET {','.join(sets)}, updated_at=NOW() WHERE id=%s"
    with _db() as c, c.cursor() as cur:
        cur.execute(sql, values)
        if cur.rowcount == 0:
            raise HTTPException(409, "conflict: record changed by another client")
    return {"id": rid, "updated": True}

@app.delete("/api/records/{rid}")
def delete_record(rid: int, user = Depends(require_auth)):
    if not _write_enabled(): raise HTTPException(503, "write not enabled")
    with _db() as c, c.cursor() as cur:
        cur.execute("DELETE FROM aftersale_records WHERE id=%s", [rid])
    return {"deleted": rid}

@app.post("/api/records/batch-resolve")
def batch_resolve(payload: dict = Body(...), user = Depends(require_auth)):
    if not _write_enabled(): raise HTTPException(503, "write not enabled")
    ids = payload.get("ids", [])
    if not ids: raise HTTPException(400, "ids required")
    placeholders = ",".join(["%s"]*len(ids))
    with _db() as c, c.cursor() as cur:
        cur.execute(f"UPDATE aftersale_records SET resolved='是', updated_at=NOW() WHERE id IN ({placeholders})", ids)
        n = cur.rowcount
    return {"updated": n}

@app.post("/api/records/batch-delete")
def batch_delete(payload: dict = Body(...), user = Depends(require_auth)):
    if not _write_enabled(): raise HTTPException(503, "write not enabled")
    ids = payload.get("ids", [])
    if not ids: raise HTTPException(400, "ids required")
    placeholders = ",".join(["%s"]*len(ids))
    with _db() as c, c.cursor() as cur:
        cur.execute(f"DELETE FROM aftersale_records WHERE id IN ({placeholders})", ids)
        n = cur.rowcount
    return {"deleted": n}


# ===== PHASE-1.5 APPEND: charts stats API =====
@app.get("/api/stats/charts")
def stats_charts(cycle_start: str = "", issue_type: str = "", resolved: str = "",
                 is_initiative: str = "", is_our_problem: str = ""):
    """默认图表四件套：地区分布 / 每日售后量 / 我方问题占比 / 问题类型分布
    统计口径与 /api/records 筛选一致（可传同样筛选参数）。"""
    where, params = _build_where("", issue_type, resolved,
                                 is_initiative, is_our_problem, cycle_start)
    cyc = _cycle_range(cycle_start) if cycle_start else None
    # 无周期时：最近 90 天（避免全表聚合过慢）
    if not cyc:
        where += (" AND " if where else " WHERE ") + f"{DATE_EXPR} >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)"

    def group(q: str, extra=(), limit=200):
        with _db() as c, c.cursor() as cur:
            cur.execute(q + where + f" GROUP BY 1 ORDER BY 2 DESC LIMIT {limit}", params + list(extra))
            return cur.fetchall()

    region = [{"name": r.get("region") or "未知", "value": r["n"]}
              for r in group("SELECT region, COUNT(*) n FROM aftersale_records")
              if r.get("region")]
    daily = [{"date": r["d"][5:] if r["d"] else "", "count": r["n"]}
             for r in group("SELECT " + DATE_EXPR + " d, COUNT(*) n FROM aftersale_records")]
    # 我方问题占比（NULL 视为否）
    with _db() as c, c.cursor() as cur:
        cur.execute("SELECT COUNT(*) n FROM aftersale_records" + where, params)
        total = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) n FROM aftersale_records" + where + " AND is_our_problem='是'", params)
        yes = cur.fetchone()["n"]
    our = {"yes": yes, "no": max(0, total - yes)}
    issue = [{"name": r.get("issue_type") or "未填", "value": r["n"]}
             for r in group("SELECT issue_type, COUNT(*) n FROM aftersale_records")]
    return {"region_dist": region, "daily": daily, "our_problem": our,
            "issue_type_dist": issue, "total": total}


# ===== PHASE-2A APPEND: generic aggregation (custom charts) =====
_DIM = {
    "region": "region", "issue_type": "issue_type", "resolved": "resolved",
    "is_initiative": "is_initiative", "is_our_problem": "is_our_problem",
    "table_no": "table_no", "creator": "creator", "resolver": "resolver",
    "day": DATE_EXPR,
    "week": "DATE_FORMAT(" + DATE_EXPR + ", '%x-W%u')",
}

@app.post("/api/stats/query")
def stats_query(payload: dict = Body(...)):
    """通用聚合（自定义图表）：单维度 + 度量 + 图表类型，维度白名单防注入"""
    dim = payload.get("dimension", "")
    measure = payload.get("measure", "count")
    chart = payload.get("chart", "bar")
    sort = payload.get("sort", "value_desc")
    try:
        limit = min(max(int(payload.get("limit") or 20), 1), 50)
    except (TypeError, ValueError):
        limit = 20
    if dim not in _DIM:
        raise HTTPException(400, f"bad dimension: {dim}")
    if measure not in ("count", "percent"):
        raise HTTPException(400, "bad measure")
    if chart not in ("bar", "line", "pie", "ring", "hbar"):
        raise HTTPException(400, "bad chart")
    f = payload.get("filter") or {}
    where, params = _build_where(str(f.get("keyword") or ""), str(f.get("issue_type") or ""),
                                 str(f.get("resolved") or ""), str(f.get("is_initiative") or ""),
                                 str(f.get("is_our_problem") or ""), str(f.get("cycle_start") or ""))
    col = _DIM[dim]
    order = "n DESC" if sort == "value_desc" else ("n ASC" if sort == "value_asc" else "name ASC")
    sql = (f"SELECT {col} name, COUNT(*) n FROM aftersale_records{where} "
           f"GROUP BY {col} ORDER BY {order} LIMIT {limit}")
    with _db() as c, c.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        if measure == "percent":
            cur.execute("SELECT COUNT(*) n FROM aftersale_records" + where, params)
            total = cur.fetchone()["n"] or 1
        else:
            total = None
    out = [{"name": r["name"] if r["name"] not in (None, "") else "未填", "value": r["n"]} for r in rows]
    if total:
        for x in out:
            x["percent"] = round(x["value"] * 100 / total, 1)
    return {"columns": out, "dimension": dim, "measure": measure, "chart": chart,
            "total": total, "limit": limit}
