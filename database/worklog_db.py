# -*- coding: utf-8 -*-
"""工作留痕数据层（SQLite / MySQL 双后端，全量软删除）

设计稿 design/worklog_hub_v1.html。本模块是五个页签唯一的数据出口：
汇总卡、分组表、时间轴、思维导图、统计图表都读这里的聚合函数，
任何一页都不许自己拼 SQL —— 口径分叉过一次就再也对不回来。

三条硬口径：
1. **软删除**：delete_record 只置 deleted=1，所有列表/统计共用 _build_where
   里的 ``deleted = 0``。成员停用同理（active=0），历史记录归属必须保住。
2. **日期存 'YYYY-MM-DD' 定长串**，字典序即时间序；日/周/月区间在本模块用
   纯 datetime 算好再下发。不用 date()/strftime 这类 SQLite 专有函数，
   否则 MySQL 主库模式下 backend._convert_sql 兜不住（见 schema.py 顶部警告）。
3. **不在 UI 线程查库**：所有函数都是同步阻塞的，调用方必须经
   workers/worklog_worker.py 丢到工作线程。

连接复用 table_db 的双后端路由（SQLite 单连接 / MySQL thread-local）。
"""

from datetime import date, datetime, timedelta

from core import app_settings
from database import table_db

# ==================== 词汇（单一来源，UI 侧 windows/worklog/common.py 从这里取） ====================

# 内置分类（稿 630）：顺序即分类管理页标签流的展示顺序
CATEGORIES = ("开发", "运维", "文档", "测试", "沟通", "学习", "其他")

# 状态（稿 44-49 的 .tag 四态）：完成率只认「已完成」，其余一律算未完成
STATUSES = ("已完成", "进行中", "未开始", "异常")
DONE_STATUS = "已完成"

# 工时上限与进度步进（稿 660 步进 0.5 · 上限 24；稿 408 进度步进 5%）
HOURS_MAX = 24.0
PROGRESS_STEP = 5

# 记录可写字段：既是 INSERT 列序也是 UPDATE 白名单，调整要同步 schema.TABLE_COLUMNS
RECORD_FIELDS = (
    "work_date", "member_id", "title", "project", "category",
    "hours", "progress", "status", "note",
)

# 关键词搜索覆盖列（稿 333 占位符「搜索内容 / 项目 / 备注」逐字对应）
_SEARCH_FIELDS = ("title", "project", "note")

# 导出表头（稿 529「导出 Excel」）：中文名改一个字，线下报表就会错列
_EXPORT_HEADERS = (
    ("work_date", "日期"), ("member_name", "成员"), ("title", "工作内容"),
    ("project", "项目"), ("category", "分类"), ("hours", "工时(h)"),
    ("progress", "进度(%)"), ("status", "状态"), ("note", "备注"),
)


def now_str() -> str:
    """统一时间串 'yyyy-MM-dd HH:mm:ss'：work_date 取前 10 位与它直接比较"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean(value) -> str:
    """None/任意值 → 字符串。空串即「未填」，查询侧不写 IS NULL 分支"""
    return "" if value is None else str(value)


def _num(value, default=0.0) -> float:
    """聚合列 → float：MySQL 的 SUM/AVG 返回 Decimal，SQLite 返回 float/int"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


# ==================== 周期区间（纯 datetime，可离线单测） ====================

def _to_date(anchor) -> date:
    """锚点 → date；空值或格式错都退回今天，不让坏入参把查询条件打成空串"""
    text = _clean(anchor)[:10]
    if text:
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass
    return date.today()


def day_range(anchor) -> tuple:
    d = _to_date(anchor)
    return d.isoformat(), d.isoformat()


def week_range(anchor) -> tuple:
    """自然周（周一起）——与售后周期设置的默认口径一致"""
    d = _to_date(anchor)
    start = d - timedelta(days=d.weekday())
    return start.isoformat(), (start + timedelta(days=6)).isoformat()


def month_range(anchor) -> tuple:
    d = _to_date(anchor)
    start = d.replace(day=1)
    # 下月 1 号减一天：免掉每月天数的分支表
    nxt = start.replace(year=start.year + 1, month=1) if start.month == 12 \
        else start.replace(month=start.month + 1)
    return start.isoformat(), (nxt - timedelta(days=1)).isoformat()


PERIOD_RANGES = {"day": day_range, "week": week_range, "month": month_range}


def period_range(period: str, anchor) -> tuple:
    """周期键 → (起, 止)；未知键退回按天，避免条件为空打全表"""
    return PERIOD_RANGES.get(period, day_range)(anchor)


def recent_days(anchor, count: int = 7) -> list:
    """近 count 天（含锚点当天，升序）——统计页柱图的横轴刻度"""
    d = _to_date(anchor)
    n = max(1, int(count))
    return [(d - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


# ==================== 成员 ====================

_MEMBER_FIELDS = ("name", "alias", "active", "sort")


def list_members(active_only: bool = False) -> list:
    """成员清单（含停用项，按 sort 再按 id 升序）

    active_only=True 给录入下拉用；成员管理页要看到停用行，故默认全量。
    """
    conn = table_db.get_conn()
    sql = "SELECT id, name, alias, active, sort FROM worklog_members"
    if active_only:
        # 「参与统计」即 active（稿 604 表头）：关掉的人不再进录入候选
        sql += " WHERE active = 1"
    sql += " ORDER BY sort, id"
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def member_map() -> dict:
    """{member_id: 成员行} —— 界面把记录里的 id 翻成名字/头像首字"""
    return {int(m["id"]): m for m in list_members()}


def get_member_by_name(name: str) -> dict | None:
    """按名字精确取成员（手输兜底：EditableComboBox 记的是历史写法）"""
    name = _clean(name).strip()
    if not name:
        return None
    conn = table_db.get_conn()
    cur = conn.execute(
        "SELECT id, name, alias, active, sort FROM worklog_members "
        "WHERE name = ? ORDER BY id LIMIT 1", (name,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def ensure_member(name: str, alias: str = "") -> int:
    """按名字取成员 id，没有就建（返回 0 表示名字为空、拒绝建档）"""
    name = _clean(name).strip()
    if not name:
        return 0
    hit = get_member_by_name(name)
    if hit:
        return int(hit["id"])
    return insert_member(name, alias=alias)


def insert_member(name: str, alias: str = "", sort: int = 0,
                  active: int = 1) -> int:
    """新增成员，返回 id；重名不拦（由调用方在界面层提示）"""
    conn = table_db.get_conn()
    cur = conn.execute(
        "INSERT INTO worklog_members (name, alias, active, sort) "
        "VALUES (?, ?, ?, ?)",
        (_clean(name).strip(), _clean(alias).strip(),
         1 if int(active) else 0, _int(sort)))
    conn.commit()
    return int(cur.lastrowid or 0)


def update_member(member_id: int, fields: dict) -> bool:
    """改成员字段（白名单 _MEMBER_FIELDS，其余键静默丢弃）"""
    sets, vals = [], []
    for key in _MEMBER_FIELDS:
        if key not in fields:
            continue
        if key in ("active", "sort"):
            sets.append(f"{key} = ?")
            vals.append(_int(fields[key]))
        else:
            sets.append(f"{key} = ?")
            vals.append(_clean(fields[key]).strip())
    if not sets:
        return False
    vals.append(int(member_id))
    conn = table_db.get_conn()
    cur = conn.execute(
        f"UPDATE worklog_members SET {', '.join(sets)} WHERE id = ?", vals)
    conn.commit()
    return cur.rowcount > 0


def set_member_active(member_id: int, active: bool) -> bool:
    """停用 / 启用成员。停用 ≠ 删除：历史记录的 member_id 原样保留"""
    return update_member(member_id, {"active": 1 if active else 0})


# ==================== 分类（落 config JSON，见 app_settings.KEY_DOMAIN 注释） ====================

_CATEGORIES_KEY = "worklog_categories"


def _load_category_cfg() -> dict:
    """配置里的分类覆盖项 {名字: 是否启用}；dict 保序即用户添加顺序"""
    raw = app_settings.get(_CATEGORIES_KEY) or []
    cfg: dict = {}
    # 配置文件可能被手改成非 list（或 list 里混进脏项），逐项校验后丢弃坏的
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = _clean(item.get("name")).strip()
            if name:
                cfg[name] = bool(item.get("active", True))
    return cfg


def _save_category_cfg(cfg: dict) -> None:
    app_settings.set(_CATEGORIES_KEY,
                     [{"name": k, "active": v} for k, v in cfg.items()])


def list_categories(active_only: bool = False) -> list:
    """分类清单：内置 7 项在前、自定义追加在后

    返回 [{"name","builtin","active"}]。builtin 决定界面能否把它彻底移除
    （内置项只允许停用，删掉会让历史记录的分类失去配色）。
    """
    cfg = _load_category_cfg()
    names = list(CATEGORIES) + [n for n in cfg if n not in CATEGORIES]
    out = [{"name": n, "builtin": n in CATEGORIES,
            "active": cfg.get(n, True)} for n in names]
    return [c for c in out if c["active"]] if active_only else out


def category_names(active_only: bool = True) -> tuple:
    """分类名元序（下拉候选用）"""
    return tuple(c["name"] for c in list_categories(active_only))


def add_category(name: str) -> bool:
    """新增自定义分类；已存在（含内置）返回 False"""
    name = _clean(name).strip()
    if not name:
        return False
    cfg = _load_category_cfg()
    if name in CATEGORIES or name in cfg:
        return False
    cfg[name] = True
    _save_category_cfg(cfg)
    return True


def set_category_active(name: str, active: bool) -> bool:
    """停用 / 启用分类。被记录引用的分类只能停用，不能移除（稿 632）"""
    name = _clean(name).strip()
    if not name:
        return False
    cfg = _load_category_cfg()
    cfg[name] = bool(active)
    _save_category_cfg(cfg)
    return True


def rename_category(old: str, new: str) -> bool:
    """自定义分类改名（稿 632 的「可增改」）：配置键与记录列一起换

    内置 7 项不放行：分类配色表与设计稿文案都按名字挂钩，改名即脱钩。
    记录列必须同事务跟着改，否则历史留痕会挂在一个界面上已不存在的分类下。
    """
    old = _clean(old).strip()
    new = _clean(new).strip()
    if not old or not new or old == new:
        return False
    if old in CATEGORIES or new in CATEGORIES:
        return False
    cfg = _load_category_cfg()
    if old not in cfg or new in cfg:
        return False
    _save_category_cfg({new if k == old else k: v for k, v in cfg.items()})
    conn = table_db.get_conn()
    conn.execute("UPDATE worklog_records SET category = ? WHERE category = ?",
                 (new, old))
    conn.commit()
    return True


def category_usage(name: str) -> int:
    """该分类被多少条未删记录引用 —— >0 时界面禁用「删除」只留「停用」"""
    conn = table_db.get_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM worklog_records WHERE category = ? AND deleted = 0",
        (_clean(name).strip(),)).fetchone()
    return _int(row[0] if row else 0)


def project_candidates() -> list:
    """历史项目名（去重保序）—— 项目输入框的下拉候选"""
    conn = table_db.get_conn()
    rows = conn.execute(
        "SELECT DISTINCT project FROM worklog_records "
        "WHERE deleted = 0 AND project <> '' ORDER BY project").fetchall()
    return [_clean(r[0]).strip() for r in rows]


# ==================== 记录增删改 ====================

def normalize_record(record: dict) -> dict:
    """收敛入参：钳位 + 类型 + 100% 自动完成。**只动传进来的键**

    进度到 100% 自动置「已完成」（稿 668）：对话框拖滑块与表格滚轮步进
    两条路都会到 100%，规则只写在这一处，两边行为才不会分叉。

    不在这里补 work_date/status 默认值：部分更新（只改 progress）经本函数
    时若被补上默认值，就会把记录原本的日期与状态一起冲掉。缺省值由
    insert_record 单独补。
    """
    out = dict(record or {})
    if "hours" in out:
        try:
            hours = float(out["hours"])
        except (TypeError, ValueError):
            hours = 0.0
        out["hours"] = round(min(max(hours, 0.0), HOURS_MAX), 2)
    if "progress" in out:
        out["progress"] = min(max(_int(out["progress"]), 0), 100)
        # 已填了别的状态又拉到 100%：按稿口径同样强制归到已完成
        if out["progress"] >= 100:
            out["status"] = DONE_STATUS
    for key in ("work_date", "title", "project", "category", "status", "note"):
        if key in out:
            out[key] = _clean(out[key]).strip()
    if "member_id" in out:
        out["member_id"] = _int(out["member_id"])
    return out


def _with_defaults(data: dict) -> dict:
    """新增路径的缺省值：日期取当天、状态取进行中（稿 664 的默认选中项）"""
    out = dict(data)
    if not out.get("work_date"):
        out["work_date"] = date.today().isoformat()
    if not out.get("status"):
        out["status"] = "进行中"
    return out


def insert_record(record: dict) -> int:
    """新增记录，返回自增 id（0 表示工作内容为空、拒绝落库）"""
    data = _with_defaults(normalize_record(record))
    if not data.get("title"):
        return 0
    now = now_str()
    cols = list(RECORD_FIELDS) + ["created_at", "updated_at", "deleted"]
    vals = [data.get(c) for c in RECORD_FIELDS]
    # 数值列不能落 None：NULL 进 SUM/AVG 会让聚合口径随空值漂移
    vals[cols.index("hours")] = _num(data.get("hours"))
    vals[cols.index("progress")] = _int(data.get("progress"))
    vals[cols.index("member_id")] = _int(data.get("member_id"))
    for c in ("work_date", "title", "project", "category", "status", "note"):
        vals[cols.index(c)] = _clean(data.get(c))
    vals += [now, now, 0]
    conn = table_db.get_conn()
    cur = conn.execute(
        f"INSERT INTO worklog_records ({', '.join(cols)}) "
        f"VALUES ({', '.join(['?'] * len(cols))})", vals)
    conn.commit()
    return int(cur.lastrowid or 0)


def update_record(record_id: int, record: dict) -> bool:
    """改记录（只认 RECORD_FIELDS 白名单；时间戳由本模块维护）"""
    data = normalize_record(record)
    sets, vals = [], []
    for c in RECORD_FIELDS:
        if c not in data:
            continue
        sets.append(f"{c} = ?")
        if c == "hours":
            vals.append(_num(data[c]))
        elif c in ("progress", "member_id"):
            vals.append(_int(data[c]))
        else:
            vals.append(_clean(data[c]))
    if not sets:
        return False
    sets.append("updated_at = ?")
    vals += [now_str(), int(record_id)]
    conn = table_db.get_conn()
    cur = conn.execute(
        f"UPDATE worklog_records SET {', '.join(sets)} "
        f"WHERE id = ? AND deleted = 0", vals)
    conn.commit()
    return cur.rowcount > 0


def get_record(record_id: int) -> dict | None:
    """按 id 取单条（含成员名），编辑对话框的数据源

    where 形参要的是完整 " WHERE ..." 子句（_build_where 就这么返回），
    这里同样带上 deleted = 0，否则软删除的记录还能被编辑对话框读出来改。
    """
    rows = _select_records(" WHERE r.id = ? AND r.deleted = 0",
                           [int(record_id)], limit=1)
    return rows[0] if rows else None


def delete_record(record_id: int) -> bool:
    """软删除：置 deleted=1，统计与列表自动看不见，库里仍可捞回"""
    conn = table_db.get_conn()
    cur = conn.execute(
        "UPDATE worklog_records SET deleted = 1, updated_at = ? "
        "WHERE id = ? AND deleted = 0", (now_str(), int(record_id)))
    conn.commit()
    return cur.rowcount > 0


def set_progress(record_id: int, progress: int) -> dict | None:
    """改进度并回读整行（表格滚轮/双击步进 5% 用）

    返回改后的行而不是 bool：界面要就地刷新该行的进度条与状态标签，
    再查一次库既慢又可能与并发写入错开。改不到（已删/不存在）返回 None。
    """
    value = min(max(_int(progress), 0), 100)
    patch = {"progress": value}
    # 拖到 100% 同步翻状态（与对话框同一规则，都走 normalize_record）
    patch = normalize_record(patch)
    if not update_record(record_id, patch):
        return None
    return get_record(record_id)


# ==================== 查询 ====================

def _build_where(date_from: str = "", date_to: str = "", member_id: int = 0,
                 category: str = "", status: str = "",
                 keyword: str = "") -> tuple:
    """WHERE 子句 + 参数（软删除过滤恒在，五页共用这一份）

    date_from/date_to 为空即不限区间；member_id=0 表示全部成员。
    条件逐个追加、空串不过滤：界面把「全部」换成空串传下来即可。
    """
    where = ["r.deleted = 0"]
    params = []
    if _clean(date_from).strip():
        where.append("r.work_date >= ?")
        params.append(_clean(date_from).strip()[:10])
    if _clean(date_to).strip():
        where.append("r.work_date <= ?")
        params.append(_clean(date_to).strip()[:10])
    if _int(member_id):
        where.append("r.member_id = ?")
        params.append(_int(member_id))
    if _clean(category).strip():
        where.append("r.category = ?")
        params.append(_clean(category).strip())
    if _clean(status).strip():
        where.append("r.status = ?")
        params.append(_clean(status).strip())
    kw = _clean(keyword).strip()
    if kw:
        # 关键词跨列是 OR、与其它条件是 AND；括号必须留着，否则 OR 吃掉前面的过滤
        like = " OR ".join(f"r.{f} LIKE ?" for f in _SEARCH_FIELDS)
        where.append(f"({like})")
        params.extend([f"%{kw}%"] * len(_SEARCH_FIELDS))
    return " WHERE " + " AND ".join(where), params


# LEFT JOIN 取成员名：界面要头像首字与显示名，一次查完免得每行再回表。
# 成员被停用/查不到时 COALESCE 兜空串，历史记录不会因此从列表里消失。
_JOIN_MEMBER = (" FROM worklog_records r "
                "LEFT JOIN worklog_members m ON m.id = r.member_id")
_SELECT_COLS = ("r.id, r.work_date, r.member_id, r.title, r.project, "
                "r.category, r.hours, r.progress, r.status, r.note, "
                "r.created_at, r.updated_at, "
                "COALESCE(m.name, '') AS member_name, "
                "COALESCE(m.alias, '') AS member_alias, "
                "COALESCE(m.sort, 0) AS member_sort, "
                "COALESCE(m.active, 1) AS member_active")


def _select_records(where: str, params: list, limit: int = 0,
                    ascending: bool = False) -> list:
    """按给定 WHERE 取记录行；limit=0 表示不分页整表取（导出必须走这条）"""
    conn = table_db.get_conn()
    sql = f"SELECT {_SELECT_COLS}{_JOIN_MEMBER}{where}"
    # 同一天内按成员排序号再按 id：成员的先后与成员管理页的「排序」列一致。
    # 时间轴要正序回看，记录表要倒序看最新，故方向可切
    direction = "ASC" if ascending else "DESC"
    sql += (f" ORDER BY r.work_date {direction}, member_sort, "
            f"r.member_id, r.id")
    if int(limit) > 0:
        sql += " LIMIT ?"
        params = params + [int(limit)]
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for row in rows:
        row["hours"] = _num(row.get("hours"))
        row["progress"] = _int(row.get("progress"))
        row["member_id"] = _int(row.get("member_id"))
    return rows


def query_records(date_from: str = "", date_to: str = "", member_id: int = 0,
                  category: str = "", status: str = "", keyword: str = "",
                  limit: int = 0, ascending: bool = False) -> list:
    """记录明细（不分页；limit>0 时才截断）

    记录表、时间轴、思维导图、Excel 导出共用这一个出口。导出刻意不接
    分页参数：售后的教训是 query_page 被夹在 1~500，用它导出会静默丢行。
    """
    where, params = _build_where(date_from, date_to, member_id,
                                 category, status, keyword)
    return _select_records(where, params, limit=limit, ascending=ascending)


def query_day(anchor, member_id: int = 0, category: str = "",
              status: str = "", keyword: str = "") -> list:
    """某一天的记录（记录页默认视图的取数口径）"""
    start, end = day_range(anchor)
    return query_records(start, end, member_id, category, status, keyword)


def _agg_select(select: str, group: str, date_from: str, date_to: str,
                member_id: int, category: str, status: str,
                keyword: str) -> list:
    """聚合查询公共出口：五页的汇总数字都从这里出，WHERE 只构造一次"""
    where, params = _build_where(date_from, date_to, member_id,
                                 category, status, keyword)
    conn = table_db.get_conn()
    cur = conn.execute(f"SELECT {select}{_JOIN_MEMBER}{where}{group}", params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


_TOTAL_SELECT = (
    "COUNT(*) AS n, COALESCE(SUM(r.hours), 0) AS hours, "
    "COUNT(DISTINCT r.member_id) AS members, "
    "COUNT(DISTINCT r.work_date) AS days, "
    f"SUM(CASE WHEN r.status = '{DONE_STATUS}' THEN 1 ELSE 0 END) AS done, "
    "COALESCE(AVG(r.progress), 0) AS avg_progress")


def totals(date_from: str = "", date_to: str = "", member_id: int = 0,
           category: str = "", status: str = "", keyword: str = "") -> dict:
    """区间总量（汇总卡四格的数据源）

    done_rate 与 per_capita 在 Python 侧算：SQLite/MySQL 的整数除法与
    ROUND 行为不一致，放 SQL 里两后端会给出不同的小数位。
    """
    rows = _agg_select(_TOTAL_SELECT, "", date_from, date_to,
                       member_id, category, status, keyword)
    row = rows[0] if rows else {}
    n = _int(row.get("n"))
    done = _int(row.get("done"))
    members = _int(row.get("members"))
    days = _int(row.get("days"))
    hours = _num(row.get("hours"))
    return {
        "count": n,
        "hours": round(hours, 2),
        "members": members,
        "days": days,
        "done": done,
        "done_rate": round(done * 100.0 / n, 1) if n else 0.0,
        "avg_progress": round(_num(row.get("avg_progress")), 1),
        # 人均工时口径 = 总工时 / (人数 × 有记录的天数)，单位 h/人·日（稿 324）
        "per_capita": round(hours / (members * days), 1)
        if members and days else 0.0,
    }


def daily_groups(date_from: str = "", date_to: str = "", member_id: int = 0,
                 category: str = "", status: str = "",
                 keyword: str = "") -> list:
    """按天聚合（GROUP BY work_date）——记录页分组行 + 统计页柱图共用

    返回按日期倒序的 [{work_date, count, hours, members, done, done_rate}]。
    """
    rows = _agg_select(
        "r.work_date AS work_date, COUNT(*) AS n, "
        "COALESCE(SUM(r.hours), 0) AS hours, "
        "COUNT(DISTINCT r.member_id) AS members, "
        f"SUM(CASE WHEN r.status = '{DONE_STATUS}' THEN 1 ELSE 0 END) AS done",
        " GROUP BY r.work_date ORDER BY r.work_date DESC",
        date_from, date_to, member_id, category, status, keyword)
    out = []
    for row in rows:
        n = _int(row.get("n"))
        done = _int(row.get("done"))
        out.append({
            "work_date": _clean(row.get("work_date")),
            "count": n,
            "hours": round(_num(row.get("hours")), 2),
            "members": _int(row.get("members")),
            "done": done,
            "done_rate": round(done * 100.0 / n) if n else 0,
        })
    return out


def stacked_daily(date_from: str = "", date_to: str = "",
                  member_id: int = 0) -> list:
    """按 (天, 成员) 聚合工时 —— 统计页堆叠柱的切片数据

    返回 [{work_date, member_id, member_name, hours}] 升序；透视为
    「每天一根柱、柱内按成员切片」由界面完成（DAO 不决定绘图形态）。
    """
    rows = _agg_select(
        "r.work_date AS work_date, r.member_id AS member_id, "
        "COALESCE(m.name, '') AS member_name, "
        "COALESCE(SUM(r.hours), 0) AS hours",
        " GROUP BY r.work_date, r.member_id, m.name "
        "ORDER BY r.work_date, r.member_id",
        date_from, date_to, member_id, "", "", "")
    for row in rows:
        row["hours"] = round(_num(row.get("hours")), 2)
        row["member_id"] = _int(row.get("member_id"))
        row["work_date"] = _clean(row.get("work_date"))
    return rows


def category_hours(date_from: str = "", date_to: str = "",
                   member_id: int = 0) -> list:
    """按分类聚合工时（环图）——工时降序，占比在界面按总工时归一"""
    rows = _agg_select(
        "r.category AS category, COALESCE(SUM(r.hours), 0) AS hours, "
        "COUNT(*) AS n",
        " GROUP BY r.category ORDER BY hours DESC, r.category",
        date_from, date_to, member_id, "", "", "")
    out = []
    for row in rows:
        out.append({"category": _clean(row.get("category")) or "未分类",
                    "hours": round(_num(row.get("hours")), 2),
                    "count": _int(row.get("n"))})
    return out


def member_summary(date_from: str = "", date_to: str = "") -> list:
    """成员汇总表（稿 576）：任务数 / 已完成 / 工时 / 完成率 / 平均进度

    只统计 active=1 的成员（稿 604「参与统计」）。active 过滤放在 Python 侧
    而不是 WHERE：LEFT JOIN 后再加 m.active = 1 会退化成 INNER JOIN，
    成员行已缺失的历史记录会被整条吞掉，汇总数与记录表对不上。
    """
    rows = _agg_select(
        "r.member_id AS member_id, COALESCE(m.name, '') AS member_name, "
        "COALESCE(m.alias, '') AS member_alias, COALESCE(m.sort, 0) AS sort, "
        "COALESCE(m.active, 1) AS member_active, "
        "COUNT(*) AS n, COALESCE(SUM(r.hours), 0) AS hours, "
        f"SUM(CASE WHEN r.status = '{DONE_STATUS}' THEN 1 ELSE 0 END) AS done, "
        "COALESCE(AVG(r.progress), 0) AS avg_progress",
        " GROUP BY r.member_id, m.name, m.alias, m.sort, m.active "
        "ORDER BY sort, r.member_id",
        date_from, date_to, 0, "", "", "")
    out = []
    for row in rows:
        if not _int(row.get("member_active"), 1):
            continue
        n = _int(row.get("n"))
        done = _int(row.get("done"))
        out.append({
            "member_id": _int(row.get("member_id")),
            "member_name": _clean(row.get("member_name")),
            "member_alias": _clean(row.get("member_alias")),
            "count": n,
            "done": done,
            "hours": round(_num(row.get("hours")), 1),
            "done_rate": round(done * 100.0 / n) if n else 0,
            "avg_progress": round(_num(row.get("avg_progress"))),
        })
    return out


# ==================== 文字报告（复制周报 / 日报 / 本期报告） ====================

def build_report_text(date_from: str = "", date_to: str = "",
                      member_id: int = 0, title: str = "") -> str:
    """按成员分段的纯文本报告（复制到剪贴板，不进任何富文本控件）

    在 worker 线程里跑：既要查库又要拼串，放 UI 线程会卡住表格重绘。
    """
    rows = query_records(date_from, date_to, member_id, ascending=True)
    if not rows:
        return f"{title or '工作留痕'}（{date_from} ~ {date_to}）：无记录"

    head = title or "工作留痕"
    span = date_from if date_from == date_to else f"{date_from} ~ {date_to}"
    lines = [f"{head}（{span}）", ""]
    buckets: dict = {}
    for row in rows:
        # 键带 member_sort：段落顺序与成员管理页的「排序」列一致，
        # 汇报时谁先谁后不会随查询结果的偶然顺序变
        key = (_int(row.get("member_sort")), int(row["member_id"]),
               row.get("member_name") or "未指派")
        buckets.setdefault(key, []).append(row)
    summary = totals(date_from, date_to, member_id)
    for (_sort, _mid, name), items in buckets.items():
        hours = round(sum(float(i["hours"]) for i in items), 2)
        lines.append(f"【{name}】{len(items)} 项 · {hours}h")
        for item in items:
            lines.append(
                f"  {item['work_date']}  {item['title']}"
                f"（{item['category'] or '未分类'} / {item['project'] or '无项目'}）"
                f" {item['hours']}h · {item['progress']}% · {item['status']}")
            note = _clean(item.get("note")).strip()
            if note:
                lines.append(f"      备注：{note}")
        lines.append("")
    lines.append(f"合计：{summary['count']} 项 · {summary['hours']}h · "
                 f"完成率 {summary['done_rate']}%")
    return "\n".join(lines)


# ==================== 导出 ====================

def export_xlsx(path: str, date_from: str = "", date_to: str = "",
                member_id: int = 0) -> int:
    """导出 xlsx，返回行数。走 query_records 的整表路径（不分页）"""
    # 延迟 import：不导出就不加载 openpyxl，启动路径不背这个依赖
    from openpyxl import Workbook

    rows = query_records(date_from, date_to, member_id, limit=0)
    wb = Workbook()
    ws = wb.active
    ws.title = "工作留痕"
    ws.append([h for _, h in _EXPORT_HEADERS])
    for row in rows:
        ws.append([row.get(k) for k, _ in _EXPORT_HEADERS])
    # 日期/工时列设文本与数值格式：不设的话 openpyxl 会把 '2026-11-06'
    # 当字符串靠左对齐，人工核对时和相邻数值列的右对齐混在一起很难扫读
    for col, width in zip("ABCDEFGHI", (12, 12, 46, 14, 10, 9, 9, 10, 30)):
        ws.column_dimensions[col].width = width
    wb.save(path)
    return len(rows)
