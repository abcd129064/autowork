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
    # pymysql 默认 autocommit=False 且 with 连接退出不 commit，
    # 写路径会整事务回滚（INSERT 返回 id 但行丢失）→ 必须开启
    autocommit=True,
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
                 is_initiative: str, is_our_problem: str, cycle_start: str,
                 occurred_at: str = "", region: str = "",
                 room_name: str = "", table_no: str = ""):
    conds, params = [], []
    # 软删除隔离：回收站（deleted=1）不出现在任何常规查询
    conds.append("deleted = 0")
    if issue_type:
        conds.append("issue_type = %s"); params.append(str(issue_type).strip())
    if region:
        conds.append("region = %s"); params.append(str(region).strip())
    if room_name:
        # 球房精确筛选（统计页「球房/球桌排行」点击跳转用）。
        # TRIM 比较：库内旧数据可能有首尾空格，排名聚合口径同为 TRIM
        conds.append("TRIM(room_name) = %s"); params.append(str(room_name).strip())
    if table_no:
        # 桌号仅在球房范围内有意义（跨球房同名桌普遍），单独 table_no
        # 精确筛选也走 TRIM 口径
        conds.append("TRIM(table_no) = %s"); params.append(str(table_no).strip())
    if resolved:
        conds.append("resolved = %s"); params.append(str(resolved).strip())
    if is_initiative:
        conds.append("is_initiative = %s"); params.append(str(is_initiative).strip())
    if is_our_problem:
        conds.append("is_our_problem = %s"); params.append(str(is_our_problem).strip())
    if occurred_at:
        # 按发生日期精确筛（记录归属日期口径与账期一致：occurred_at 缺失回退 created_at）
        conds.append(f"{DATE_EXPR} = %s"); params.append(str(occurred_at).strip()[:10])
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


# 排序白名单（防注入）：列名必须在此集合内才允许进 ORDER BY
_SORTABLE = {"id", "created_at", "occurred_at", "issue_type", "region",
             "room_name", "table_no", "resolved", "response_time",
             "resolver", "creator", "cycle_start"}


@app.get("/api/records")
def records(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    keyword: str = "", cycle_start: str = "", issue_type: str = "",
    resolved: str = "", is_initiative: str = "", is_our_problem: str = "",
    occurred_at: str = "", region: str = "",
    room_name: str = "", table_no: str = "",
    sort_by: str = "", sort_order: str = "",
):
    """分页列表 + 统计一次返回（与桌面端 query_with_stats 同口径）"""
    where, params = _build_where(keyword, issue_type, resolved,
                                 is_initiative, is_our_problem, cycle_start,
                                 occurred_at, region, room_name, table_no)
    # 排序：白名单列 + asc/desc；非法输入回退默认 created_at DESC。
    # 追加 id 作稳定次序键（同 created_at 的行分页不抖动）。
    order = "ORDER BY created_at DESC, id DESC"
    s_by = str(sort_by or "").strip().lower()
    s_ord = "ASC" if str(sort_order or "").strip().lower() == "asc" else "DESC"
    if s_by in _SORTABLE:
        order = f"ORDER BY {s_by} {s_ord}, id DESC"
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

@app.get("/api/tables/search")
def tables_search(room: str = "", limit: int = 30, user=Depends(require_auth)):
    """按球房名模糊搜索球桌（与桌面端 table_db.query_tables_by_room 同源同过滤）。

    过滤：排除「公司测试」与手动设备（@s）；返回 name/roomName/snk_code/city。
    需要 aftersale_ro 对 autowork.billiard_tables 的 SELECT 权限。
    """
    kw = str(room or "").strip()
    if not kw:
        return {"rows": []}
    try:
        limit = max(1, min(int(limit or 30), 50))
    except (TypeError, ValueError):
        limit = 30
    like = f"%{kw}%"
    sql = ("SELECT name, roomName, snk_code, city FROM billiard_tables "
           "WHERE TRIM(roomName) != %s "
           "AND name NOT LIKE %s AND roomName NOT LIKE %s "
           "AND roomName LIKE %s ORDER BY name LIMIT %s")
    with _db() as c, c.cursor() as cur:
        cur.execute(sql, ["公司测试", "%@s%", "%@s%", like, limit])
        rows = cur.fetchall()
    return {"rows": rows}


def _cycle_start_of(date_str: str) -> str:
    """记录归属周期起点 yyyy/MM/dd（与桌面端 cycle_start_of 对齐）

    支持 tue（默认，周二起点）/mon（自然周）/month（自然月）；custom 模式
    依赖桌面端配置，Web 端回退 tue 口径。
    """
    try:
        d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        d = datetime.now().date()
    mode = os.getenv("CYCLE_TYPE", "tue")
    if mode == "month":
        return d.replace(day=1).strftime("%Y/%m/%d")
    if mode == "mon":
        start = d - timedelta(days=d.weekday())
    else:  # tue 默认：周二开始周一结束
        start = d - timedelta(days=(d.weekday() - 1) % 7)
    return start.strftime("%Y/%m/%d")


@app.post("/api/records")
def create_record(rec: dict = Body(...), user = Depends(require_auth)):
    if not _write_enabled(): raise HTTPException(503, "write not enabled")
    fields, values = [], []
    for k in _WRITABLE:
        if k in rec and rec[k] is not None:
            fields.append(k); values.append(rec[k])
    if not fields: raise HTTPException(400, "no writable fields")
    # ---- 系统字段（对齐桌面端 insert_record）----
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # created_at=填写时刻：NULL 会导致「填写时间」显示 "-" 且 ORDER BY
    # created_at DESC 时记录沉底（用户报障：新记录排在所有记录最后）
    fields += ["created_at", "updated_at"]; values += [now_str, now_str]
    # occurred_at 缺省当日；cycle_start（账期）按发生时间归属周期
    if "occurred_at" not in fields:
        fields.append("occurred_at"); values.append(now_str[:10])
    occ = str(values[fields.index("occurred_at")])[:10]
    if "cycle_start" not in fields:
        fields.append("cycle_start"); values.append(_cycle_start_of(occ))
    # snk_code/device_code 未提供时按桌号精确匹配球桌管理库带出
    def _val(k):
        return str(values[fields.index(k)] or "").strip() if k in fields else ""
    if not _val("snk_code") and _val("table_no"):
        with _db() as c, c.cursor() as cur:
            cur.execute(
                "SELECT snk_code, code FROM billiard_tables "
                "WHERE TRIM(name)=%s LIMIT 1", [_val("table_no")])
            row = cur.fetchone() or {}
        snk = str(row.get("snk_code") or "")
        if snk:
            fields.append("snk_code"); values.append(snk)
            dev = str(row.get("code") or "")
            if dev and "device_code" not in fields:
                fields.append("device_code"); values.append(dev)
    if "creator" not in fields:  # 强制记录创建人
        fields.append("creator"); values.append(user)
    placeholders = ",".join(["%s"]*len(fields))
    cols = ",".join(fields)
    with _db() as c, c.cursor() as cur:
        cur.execute(f"INSERT INTO aftersale_records ({cols}) VALUES ({placeholders})", values)
        rid = cur.lastrowid
    _audit(user, "create", rid, detail=str(rec.get("issue_type") or ""))
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
    _audit(user, "update", rid,
           detail=",".join(k for k, v in rec.items() if k in _WRITABLE))
    return {"id": rid, "updated": True}

@app.delete("/api/records/{rid}")
def delete_record(rid: int, user = Depends(require_auth)):
    if not _write_enabled(): raise HTTPException(503, "write not enabled")
    with _db() as c, c.cursor() as cur:
        cur.execute("UPDATE aftersale_records SET deleted=1, deleted_at=NOW() WHERE id=%s", [rid])
        n = cur.rowcount
    _audit(user, "delete", rid)
    return {"deleted": rid, "soft": True}

@app.post("/api/records/batch-resolve")
def batch_resolve(payload: dict = Body(...), user = Depends(require_auth)):
    if not _write_enabled(): raise HTTPException(503, "write not enabled")
    ids = payload.get("ids", [])
    if not ids: raise HTTPException(400, "ids required")
    placeholders = ",".join(["%s"]*len(ids))
    with _db() as c, c.cursor() as cur:
        cur.execute(f"UPDATE aftersale_records SET resolved='是', updated_at=NOW() WHERE id IN ({placeholders})", ids)
        n = cur.rowcount
    _audit(user, "batch_resolve", detail=f"ids={ids}")
    return {"updated": n}

@app.post("/api/records/batch-delete")
def batch_delete(payload: dict = Body(...), user = Depends(require_auth)):
    if not _write_enabled(): raise HTTPException(503, "write not enabled")
    ids = payload.get("ids", [])
    if not ids: raise HTTPException(400, "ids required")
    placeholders = ",".join(["%s"]*len(ids))
    with _db() as c, c.cursor() as cur:
        cur.execute(f"UPDATE aftersale_records SET deleted=1, deleted_at=NOW() WHERE id IN ({placeholders})", ids)
        n = cur.rowcount
    _audit(user, "batch_delete", detail=f"ids={ids}")
    return {"deleted": n, "soft": True}


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
    # date 输出完整 yyyy-MM-dd（前端展示自行截取 MM-DD；跨年窗口无歧义，
    # 且总览页点击某天跳列表筛选需要完整日期）
    daily = [{"date": r["d"] or "", "count": r["n"]}
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

    # 未解决时长分布（aging）：只看 resolved='否'，不受用户 resolved 筛选影响；
    # 时长=今天 - 发生日期（occurred_at 缺失回退 created_at）
    aging_where = where + " AND resolved = '否'"
    # 不用 STR_TO_DATE（% 会与 pymysql 参数化格式符冲突）：
    # occurred_at/created_at 均为 'YYYY-MM-DD...' 字符串，MySQL 隐式转日期
    aging_sql = (
        "SELECT CASE "
        "WHEN dd <= 0 THEN '当日' WHEN dd <= 3 THEN '1-3天' "
        "WHEN dd <= 7 THEN '4-7天' WHEN dd <= 15 THEN '8-15天' "
        "ELSE '15天以上' END bucket, COUNT(*) n FROM (SELECT "
        f"DATEDIFF(CURDATE(), {DATE_EXPR}) dd "
        "FROM aftersale_records" + aging_where + ") t GROUP BY 1"
    )
    order_aging = ["当日", "1-3天", "4-7天", "8-15天", "15天以上"]
    with _db() as c, c.cursor() as cur:
        cur.execute(aging_sql, params)
        aging_map = {r["bucket"]: r["n"] for r in cur.fetchall() if r.get("bucket")}
    aging = [{"name": b, "value": aging_map.get(b, 0)} for b in order_aging]

    # ===== 排行 TOP10（2026-09-23 需求：球桌/球房售后数量排序） =====
    # 口径要点：
    # - 桌号跨球房不唯一（"3 号桌"遍地都是）→ 球桌排行必须按
    #   TRIM(room_name)+TRIM(table_no) 联合分组，name 用 "球房·桌号" 展示，
    #   同时带 room_name/table_no 供点击跳列表精确筛选
    # - 空值排除：TRIM 后为空的球房/桌号不参与排行（脏数据不配上榜）
    # - 跟随页面筛选（含周期；无周期时沿用上方 90 天兜底 where）
    with _db() as c, c.cursor() as cur:
        cur.execute(
            "SELECT TRIM(room_name) rn, TRIM(table_no) tn, COUNT(*) n "
            "FROM aftersale_records" + where +
            " AND TRIM(room_name) <> '' AND TRIM(table_no) <> ''"
            " GROUP BY rn, tn ORDER BY n DESC, rn ASC, tn ASC LIMIT 10", params)
        table_top = [{"name": f"{r['rn']}·{r['tn']}", "value": r["n"],
                      "room_name": r["rn"], "table_no": r["tn"]}
                     for r in cur.fetchall()]
        cur.execute(
            "SELECT TRIM(room_name) rn, COUNT(*) n "
            "FROM aftersale_records" + where +
            " AND TRIM(room_name) <> ''"
            " GROUP BY rn ORDER BY n DESC, rn ASC LIMIT 10", params)
        room_top = [{"name": r["rn"], "value": r["n"]} for r in cur.fetchall()]
    return {"region_dist": region, "daily": daily, "our_problem": our,
            "issue_type_dist": issue, "aging": aging, "total": total,
            "table_top": table_top, "room_top": room_top}


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


# ===== 售后排行页（2026-09-23）：球房/球桌两级排行 + 概览 summary =====
# 排序白名单（防注入）：sort 键 → SQL ORDER BY 片段
_RANK_SORT = {
    "total": "n DESC, rn ASC",
    "total_asc": "n ASC, rn ASC",
    "unresolved": "unres DESC, n DESC, rn ASC",
    "our_problem": "ourp DESC, n DESC, rn ASC",
    "last_occurred": "lastd DESC, n DESC, rn ASC",
}


@app.get("/api/stats/rank")
def stats_rank(level: str = "room", limit: int = Query(10, ge=1, le=200),
               cycle_start: str = "", start: str = "", end: str = "",
               resolved: str = "", is_initiative: str = "", is_our_problem: str = "",
               region: str = "", room_name: str = "", keyword: str = "",
               sort: str = "total"):
    """球房/球桌售后排行（独立排行页数据源）

    - level=room  按 TRIM(room_name) 分组
    - level=table 按 TRIM(room_name)+TRIM(table_no) 联合分组（跨球房同名桌不合并）；
      传 room_name 时为该球房内桌号下钻榜
    - start/end（yyyy-MM-dd，含首尾）自定义时间范围，与 cycle_start 互斥使用
      （同传时 cycle_start 优先，对齐看板口径）
    - 无任何时间条件时兜底近 90 天（与 stats/charts 一致）
    - SUM(CASE WHEN…) 一条 SQL 算齐 total/unresolved/our_problem/initiative/last
    """
    if level not in ("room", "table"):
        raise HTTPException(400, f"bad level: {level}")
    if sort not in _RANK_SORT:
        raise HTTPException(400, f"bad sort: {sort}")

    where, params = _build_where(keyword, "", resolved, is_initiative,
                                 is_our_problem, cycle_start,
                                 region=region, room_name=room_name)
    # 自定义时间范围（仅在未传账期时生效；日期格式非法直接 400 防脏参）
    if not cycle_start and (start or end):
        for v in (start, end):
            if v:
                try:
                    datetime.strptime(v.strip()[:10], "%Y-%m-%d")
                except (ValueError, TypeError):
                    raise HTTPException(400, f"bad date: {v}")
        if start:
            where += (" AND " if where else " WHERE ") + f"{DATE_EXPR} >= %s"
            params.append(start.strip()[:10])
        if end:
            where += (" AND " if where else " WHERE ") + f"{DATE_EXPR} <= %s"
            params.append(end.strip()[:10])
    elif not cycle_start and not start and not end:
        where += (" AND " if where else " WHERE ") + \
            f"{DATE_EXPR} >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)"

    # 空值排除口径与 stats/charts 排行一致（脏数据不配上榜）
    nonempty = " AND TRIM(room_name) <> ''"
    if level == "table":
        nonempty += " AND TRIM(table_no) <> ''"

    metrics = (
        "COUNT(*) n, "
        "SUM(CASE WHEN resolved='否' THEN 1 ELSE 0 END) unres, "
        "SUM(CASE WHEN is_our_problem='是' THEN 1 ELSE 0 END) ourp, "
        "SUM(CASE WHEN is_initiative='是' THEN 1 ELSE 0 END) init_, "
        f"MAX({DATE_EXPR}) lastd "
    )
    if level == "room":
        select = "TRIM(room_name) rn, '' tn, "
        group = "GROUP BY rn"
    else:
        select = "TRIM(room_name) rn, TRIM(table_no) tn, "
        group = "GROUP BY rn, tn"
    sql = (f"SELECT {select}{metrics}FROM aftersale_records{where}{nonempty} "
           f"{group} ORDER BY {_RANK_SORT[sort]} LIMIT {int(limit)}")

    # summary：同一 where（不含 nonempty），一次带出覆盖面指标
    sum_sql = (
        "SELECT COUNT(*) total, "
        "COUNT(DISTINCT CASE WHEN TRIM(room_name) <> '' THEN TRIM(room_name) END) rooms, "
        "COUNT(DISTINCT CASE WHEN TRIM(room_name) <> '' AND TRIM(table_no) <> '' "
        "THEN CONCAT(TRIM(room_name), '|', TRIM(table_no)) END) tables_, "
        "SUM(CASE WHEN resolved='否' THEN 1 ELSE 0 END) unres "
        f"FROM aftersale_records{where}"
    )
    with _db() as c, c.cursor() as cur:
        cur.execute(sum_sql, params)
        s = cur.fetchone() or {}
        cur.execute(sql, params)
        raw = cur.fetchall()

    total = int(s.get("total") or 0)
    rows = []
    for i, r in enumerate(raw, 1):
        n = int(r["n"])
        rows.append({
            "rank": i,
            "room_name": r["rn"],
            "table_no": r["tn"],
            "name": f"{r['rn']}·{r['tn']}" if level == "table" and not room_name
                    else (r["tn"] or r["rn"]),
            "total": n,
            "share": round(n * 100 / total, 1) if total else 0.0,
            "unresolved": int(r["unres"] or 0),
            "our_problem": int(r["ourp"] or 0),
            "initiative": int(r["init_"] or 0),
            "last_occurred": r["lastd"] or "",
        })
    return {
        "level": level,
        "sort": sort,
        "limit": int(limit),
        "rows": rows,
        "summary": {
            "rooms": int(s.get("rooms") or 0),
            "tables": int(s.get("tables_") or 0),
            "total": total,
            "unresolved": int(s.get("unres") or 0),
        },
    }


# ===== PHASE-3 APPEND: 审计 / 回收站 / 批量导入 / 用户偏好 =====
# Web 端自有表（桌面端 schema.py 不管理，后端启动时自愈建表）：
# - aftersale_audit_log   写操作审计（谁在何时对哪条记录做了什么）
# - aftersale_user_prefs  用户偏好 KV（常用句库 / 上次填写，多端共享）
# 另：aftersale_records 需 deleted/deleted_at 两列（软删除回收站），
#     由 _ensure_web_tables 启动时检测补列（幂等）。

def _ensure_web_tables():
    """启动时建 Web 自有表 + 补软删除列（幂等；需要账号有 CREATE/ALTER 权限，
    失败不阻断服务——审计/偏好/回收站相应降级）"""
    with _db() as c, c.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS aftersale_audit_log ("
            "id INT AUTO_INCREMENT PRIMARY KEY, "
            "ts DATETIME NOT NULL, "
            "user VARCHAR(64) NOT NULL DEFAULT '', "
            "action VARCHAR(32) NOT NULL DEFAULT '', "
            "record_id INT NULL, "
            "detail VARCHAR(512) NOT NULL DEFAULT '', "
            "KEY idx_ts (ts)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
        cur.execute(
            "CREATE TABLE IF NOT EXISTS aftersale_user_prefs ("
            "username VARCHAR(64) NOT NULL PRIMARY KEY, "
            "prefs LONGTEXT NULL, "
            "updated_at DATETIME NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
        cur.execute(
            "SELECT COUNT(*) n FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='aftersale_records' "
            "AND COLUMN_NAME='deleted'")
        if cur.fetchone()["n"] == 0:
            cur.execute("ALTER TABLE aftersale_records "
                        "ADD COLUMN deleted TINYINT NOT NULL DEFAULT 0")
        cur.execute(
            "SELECT COUNT(*) n FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='aftersale_records' "
            "AND COLUMN_NAME='deleted_at'")
        if cur.fetchone()["n"] == 0:
            cur.execute("ALTER TABLE aftersale_records "
                        "ADD COLUMN deleted_at DATETIME NULL")

try:
    _ensure_web_tables()
except Exception as _e:  # 表已存在/权限不足等：打印后继续，端点内再报错
    print("[aftersale-web] _ensure_web_tables failed:", _e)


def _audit(user: str, action: str, record_id=None, detail: str = ""):
    """写操作审计（尽力而为：审计失败不影响主操作）"""
    try:
        with _db() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO aftersale_audit_log (ts, user, action, record_id, detail) "
                "VALUES (NOW(), %s, %s, %s, %s)",
                [user, action, record_id, str(detail or "")[:500]])
    except Exception as e:
        print("[aftersale-web] audit failed:", e)


@app.get("/api/audit")
def audit_list(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
               action: str = "", user=Depends(require_auth)):
    where, params = "", []
    if action:
        where = " WHERE action=%s"; params.append(action.strip())
    with _db() as c, c.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) n FROM aftersale_audit_log{where}", params)
        total = cur.fetchone()["n"]
        cur.execute(f"SELECT * FROM aftersale_audit_log{where} "
                    "ORDER BY id DESC LIMIT %s OFFSET %s",
                    params + [page_size, (page - 1) * page_size])
        rows = cur.fetchall()
    return {"total": total, "rows": rows, "page": page, "page_size": page_size}


def _deleted_where(deleted: int) -> str:
    return " WHERE deleted = %d" % (1 if deleted else 0)


@app.get("/api/records/recycle")
def recycle_list(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                 keyword: str = "", user=Depends(require_auth)):
    """回收站：deleted=1 的记录（按删除时间倒序）"""
    conds, params = ["deleted = 1"], []
    if keyword:
        k = f"%{keyword.strip()}%"
        conds.append("(table_no LIKE %s OR room_name LIKE %s OR problem LIKE %s OR creator LIKE %s)")
        params += [k] * 4
    where = " WHERE " + " AND ".join(conds)
    with _db() as c, c.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) n FROM aftersale_records{where}", params)
        total = cur.fetchone()["n"]
        cur.execute(f"SELECT * FROM aftersale_records{where} "
                    "ORDER BY deleted_at DESC, id DESC LIMIT %s OFFSET %s",
                    params + [page_size, (page - 1) * page_size])
        rows = cur.fetchall()
    return {"total": total, "rows": rows, "page": page, "page_size": page_size}


@app.post("/api/records/restore")
def restore_records(payload: dict = Body(...), user = Depends(require_auth)):
    """从回收站恢复（deleted 置回 0）"""
    if not _write_enabled(): raise HTTPException(503, "write not enabled")
    ids = payload.get("ids", [])
    if not ids: raise HTTPException(400, "ids required")
    placeholders = ",".join(["%s"]*len(ids))
    with _db() as c, c.cursor() as cur:
        cur.execute(f"UPDATE aftersale_records SET deleted=0, deleted_at=NULL "
                    f"WHERE id IN ({placeholders})", ids)
        n = cur.rowcount
    _audit(user, "restore", detail=f"ids={ids}")
    return {"restored": n}


@app.post("/api/records/purge")
def purge_records(payload: dict = Body(...), user = Depends(require_auth)):
    """彻底删除（硬删，回收站不可恢复）"""
    if not _write_enabled(): raise HTTPException(503, "write not enabled")
    ids = payload.get("ids", [])
    if not ids: raise HTTPException(400, "ids required")
    placeholders = ",".join(["%s"]*len(ids))
    with _db() as c, c.cursor() as cur:
        cur.execute(f"DELETE FROM aftersale_records WHERE id IN ({placeholders})", ids)
        n = cur.rowcount
    _audit(user, "purge", detail=f"ids={ids}")
    return {"purged": n}


@app.post("/api/records/batch-import")
def batch_import(payload: dict = Body(...), user = Depends(require_auth)):
    """批量导入（Excel 历史数据；行字段已由前端按桌面端 parse_excel_rows 语义映射）

    每行至少要有 room_name/table_no/problem 之一，否则跳过；
    系统字段（created_at/occurred_at/cycle_start）由后端统一补齐。
    单次 ≤ 500 行，防止误传大包。
    """
    if not _write_enabled(): raise HTTPException(503, "write not enabled")
    rows = payload.get("rows") or []
    if not rows: raise HTTPException(400, "rows required")
    if len(rows) > 500: raise HTTPException(400, "too many rows (max 500 per request)")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    imported, skipped = 0, 0
    with _db() as c, c.cursor() as cur:
        for r in rows:
            if not isinstance(r, dict): skipped += 1; continue
            fields, values = [], []
            for k in _WRITABLE:
                v = r.get(k)
                if v is not None and str(v).strip() != "":
                    fields.append(k); values.append(str(v).strip())
            if not any(k in fields for k in ("room_name", "table_no", "problem")):
                skipped += 1; continue
            fields += ["created_at", "updated_at"]; values += [now_str, now_str]
            if "occurred_at" not in fields:
                fields.append("occurred_at"); values.append(now_str[:10])
            occ = str(values[fields.index("occurred_at")])[:10]
            if "cycle_start" not in fields:
                fields.append("cycle_start"); values.append(_cycle_start_of(occ))
            if "creator" not in fields:
                fields.append("creator"); values.append(user)
            placeholders = ",".join(["%s"]*len(fields))
            cur.execute(f"INSERT INTO aftersale_records ({','.join(fields)}) "
                        f"VALUES ({placeholders})", values)
            imported += 1
    _audit(user, "import", detail=f"imported={imported},skipped={skipped}")
    return {"imported": imported, "skipped": skipped}


@app.get("/api/user/prefs")
def get_prefs(user=Depends(require_auth)):
    """当前用户偏好（常用句库 / 上次填写等），多端共享"""
    with _db() as c, c.cursor() as cur:
        cur.execute("SELECT prefs, updated_at FROM aftersale_user_prefs WHERE username=%s", [user])
        row = cur.fetchone()
    if not row or not row.get("prefs"):
        return {"prefs": {}, "updated_at": None}
    try:
        return {"prefs": _json.loads(row["prefs"]), "updated_at": str(row.get("updated_at") or "")}
    except Exception:
        return {"prefs": {}, "updated_at": None}


@app.put("/api/user/prefs")
def put_prefs(payload: dict = Body(...), user = Depends(require_auth)):
    """合并写偏好：客户端传增量 dict，服务端与已有值浅合并后存储（≤128KB）"""
    patch = payload.get("prefs") or {}
    if not isinstance(patch, dict): raise HTTPException(400, "prefs must be object")
    with _db() as c, c.cursor() as cur:
        cur.execute("SELECT prefs FROM aftersale_user_prefs WHERE username=%s", [user])
        row = cur.fetchone()
        cur_prefs = {}
        if row and row.get("prefs"):
            try: cur_prefs = _json.loads(row["prefs"])
            except Exception: cur_prefs = {}
        cur_prefs.update(patch)
        raw = _json.dumps(cur_prefs, ensure_ascii=False)
        if len(raw.encode("utf-8")) > 128 * 1024:
            raise HTTPException(400, "prefs too large")
        cur.execute(
            "INSERT INTO aftersale_user_prefs (username, prefs, updated_at) "
            "VALUES (%s, %s, NOW()) ON DUPLICATE KEY UPDATE prefs=%s, updated_at=NOW()",
            [user, raw, raw])
    return {"ok": True}
