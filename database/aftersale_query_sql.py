# -*- coding: utf-8 -*-
"""售后查询 SQL 构造纯逻辑（A3 queries 第一步）。

从 database/aftersale_db.py 下沉：记录日期表达式、筛选 WHERE、排序白名单。
不连库、不读配置；aftersale_db 直接导入这些名字，既有 adb._build_where /
adb._build_order / adb.LIST_SORT_KEYS 访问路径不变。
"""

# 关键词搜索覆盖列，全字段模糊匹配
_SEARCH_FIELDS = (
    "table_no", "room_name", "problem", "region",
    "cause", "solution", "resolver", "creator",
)

# 记录日期表达式：优先 occurred_at（格式合法时），否则回退 created_at，
# 与 Python _record_cycle 的回退语义一致（LIKE '____-__-__%' 校验
# yyyy-MM-dd 外形；空串/非法格式均回退）。两侧方言语法一致。
_RECORD_DATE_EXPR = (
    "substr(CASE WHEN occurred_at LIKE '____-__-__%' "
    "THEN occurred_at ELSE created_at END, 1, 10)")


def _build_where(keyword: str, issue_type: str, resolved: str,
                 is_initiative: str = "", is_our_problem: str = "") -> tuple:
    """构造筛选 WHERE 子句与参数（类型/状态/关键词/主动发起/我方问题）。

    周期筛选不在本函数处理：由 aftersale_db._append_cycle_where 以
    cycle_start 物化列等值过滤接入，保证分页 COUNT/LIMIT 在数据库侧完成。
    resolved / is_initiative / is_our_problem 为空才参与筛选；统计函数
    单独调用时传空串即得「已解决/未解决」分组基数与全景计数。
    """
    conds, params = [], []
    # 软删除隔离：Web 回收站中的记录（deleted=1）桌面端一律不可见。
    # 该列由 schema.py 声明、_ensure_initialized 自动补列，历史库首次
    # 迁移后即存在；本函数是列表/统计的共同 WHERE 入口。
    conds.append("deleted = 0")
    if issue_type:
        conds.append("issue_type = ?")
        params.append(str(issue_type).strip())
    if resolved:
        conds.append("resolved = ?")
        params.append(str(resolved).strip())
    if is_initiative:
        conds.append("is_initiative = ?")
        params.append(str(is_initiative).strip())
    if is_our_problem:
        conds.append("is_our_problem = ?")
        params.append(str(is_our_problem).strip())
    kw = str(keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        kw_cond = " OR ".join([f"{f} LIKE ?" for f in _SEARCH_FIELDS])
        conds.append(f"({kw_cond})")
        params.extend([like] * len(_SEARCH_FIELDS))
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    return where, params


def _yes_no_rank(col: str) -> str:
    """是/否文字列 → 数值排序表达式，是=1 否=0 空值=-1。"""
    return f"CASE {col} WHEN '是' THEN 1 WHEN '否' THEN 0 ELSE -1 END"


# 列表排序白名单：页面数据键 → ORDER BY 表达式元组
# location 拆成三列逐级排序；三个是否判定列用 CASE 映射成 0/1，避免
# SQLite 按字节、MySQL 按排序规则比较中文导致的跨后端顺序不一致。
_LIST_SORT = {
    "created_at": ("created_at",),
    "occurred_at": ("occurred_at",),
    "issue_type": ("issue_type",),
    "location": ("room_name", "region", "table_no"),
    "problem": ("problem",),
    "cause": ("cause",),
    "solution": ("solution",),
    "resolved": (_yes_no_rank("resolved"),),
    "is_our_problem": (_yes_no_rank("is_our_problem"),),
    "is_initiative": (_yes_no_rank("is_initiative"),),
    "response_time": ("response_time",),
}
LIST_SORT_KEYS = frozenset(_LIST_SORT)


def _build_order(sort_key: str, sort_order: str) -> str:
    """排序键 + 方向 → ORDER BY 片段；未知键回退 id DESC。

    末尾统一补 id DESC：排序值并列时跨页不会来回漂移，同一查询可复现。
    """
    cols = _LIST_SORT.get(str(sort_key or "").strip())
    if not cols:
        return "id DESC"
    direction = "ASC" if str(sort_order or "").lower() == "asc" else "DESC"
    parts = [f"{col} {direction}" for col in cols]
    parts.append("id DESC")
    return ", ".join(parts)
