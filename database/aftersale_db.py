# -*- coding: utf-8 -*-
"""售后记录数据层（SQLite / MySQL 双后端，自动跟随 MySQL 测试开关）

职责：
- insert_record / update_record / delete_record：售后记录增删改
- query_page / query_with_stats：筛选 + 分页 + 统计（周期/类型/状态/主动发起/我方问题/关键词）
- get_cycle_options：周期下拉选项（库中记录实际归属的周期）
- get_field_candidates：问题/解决人/地区动态候选
- export_xlsx：按筛选条件导出（表头与 售后问题汇总8月.xlsx 对齐）
- import_excel_rows：一次性导入历史 Excel

连接复用 table_db 双后端路由（SQLite 单连接 / MySQL thread-local），
MySQL 模式下多人各自提交即提交即落库，其他用户刷新/手动同步后可见。
周期规则：周二开始、周一结束为默认；可在售后面板「周期设置」切换为自然周
（周一~周日）或自定义（起始日+周期天数）。
周期归属统一按记录发生时间 occurred_at 动态计算（缺失时回退填写时间
created_at）——列表、统计、周期下拉、导出共用 cycle_start_of 同一规则。
性能（2026-09-06 S3）：周期筛选改为走落库的 cycle_start 物化列（等值过滤，
idx_aftersale_cycle 索引可用，旧表达式过滤索引失效全表扫），该列由
insert_record 落库时维护 + 周期配置保存/面板首次加载时 recalc_cycle_starts
幂等重算保证与动态口径一致；存量空值行由 _ensure_cycle_materialized 兜底回填。
"""

import os
from datetime import datetime, timedelta

from core import app_settings
from database import aftersale_cycle, aftersale_settings, table_db
from database import aftersale_queries
from database.aftersale_query_sql import (
    LIST_SORT_KEYS, _LIST_SORT, _RECORD_DATE_EXPR, _SEARCH_FIELDS,
    _build_order, _build_where, _yes_no_rank)
from database import aftersale_excel

# ==================== 字段枚举 ====================

# 类型
ISSUE_TYPES = (
    "硬件问题", "程序相关", "识别问题", "直播相关", "操作问题",
    "其他问题", "相机偏移", "新球助手", "安装调试", "不能扫码", "待查",
)

# 地区预置
REGIONS_PRESET = ("上海", "云南", "四川", "广东", "新疆", "江苏", "江西", "湖南", "西藏")

# 响应时间预置档位
RESPONSE_TIME_PRESET = ("1分钟内", "5分钟内", "30分钟内", "1小时内", "1小时以上")

# 团队默认人员
_CREW_PRESET = ("张峻涛", "沈喆", "孙跃源", "吴斌", "贺勤")

# 记录字段
RECORD_FIELDS = (
    "created_at", "occurred_at", "creator", "issue_type", "table_no",
    "room_name", "region", "problem", "cause", "resolved",
    "is_initiative", "is_our_problem", "solution", "resolver",
    "response_time", "is_important", "snk_code", "device_code", "cycle_start",
    "updated_at",
)

# 售后业务键：SQLite 与 MySQL 两侧 id 各自
# 增长会撞车，按 created_at, creator, table_no, problem 判定同一条记录。
# merge_back / 历史 mysql_sync 推送共用此定义。
RECORD_KEY_COLS = ("created_at", "creator", "table_no", "problem")


# 导出表头
_EXPORT_HEADERS = (
    ("issue_type", "类型"), ("room_name", "球房"), ("table_no", "桌号"),
    ("region", "地区"), ("problem", "问题"), ("cause", "发生原因"),
    ("resolved", "是否解决"), ("solution", "解决方案"), ("resolver", "解决人"),
    ("response_time", "响应时间"), ("created_at", "填写时间"),
    ("occurred_at", "发生时间"), ("creator", "填写人"),
    ("cycle_start", "周期"),
)


# ==================== 周期计算 ====================

# 周期模式：tue=周二起 / mon=自然周 / custom=自定义起始日+周期天数 / month=自然月
CYCLE_MODE_DEFAULT = {"type": "tue", "start": "", "span": 7}
_CYCLE_KEY = "aftersale_cycle"
_VALID_TYPES = ("tue", "mon", "custom", "month")

# S1（2026-09-06）：周期模式进程内缓存——get_cycle_options 会对每个 DISTINCT
# 日期调 cycle_start_of → load_cycle_mode，旧实现每次 open+json.load 读盘，
# 241 个日期即 241 次读盘（占周期下拉耗时 74%）。save_cycle_mode 写盘成功后
# 置空失效（与 backend.py 的 MySQL settings 缓存同策略）；测试均 monkeypatch
# 注入替换函数，天然绕过缓存，不受影响。
_cycle_mode_cache: dict | None = None


def load_cycle_mode() -> dict:
    """读取周期模式设置（config/aftersale.json 经 app_settings 门面），
    缺省/非法回退周二起

    命中进程内缓存直接返回副本（防止调用方污染缓存）。
    """
    global _cycle_mode_cache
    if _cycle_mode_cache is not None:
        return dict(_cycle_mode_cache)

    cfg = dict(CYCLE_MODE_DEFAULT)
    val = app_settings.get(_CYCLE_KEY) or {}
    if isinstance(val, dict):
        if val.get("type") in _VALID_TYPES:
            cfg["type"] = val["type"]
        if val.get("start"):
            cfg["start"] = str(val["start"])
        try:
            raw = val.get("span")
            cfg["span"] = max(1, int(raw if raw not in (None, "") else 7))
        except (TypeError, ValueError):
            cfg["span"] = 7
    _cycle_mode_cache = dict(cfg)
    return cfg


def save_cycle_mode(mode: dict) -> dict:
    """保存周期模式设置（app_settings 门面合并写，域内其余键不动），
    返回规范化配置"""
    global _cycle_mode_cache

    cfg = load_cycle_mode()
    if mode.get("type") in _VALID_TYPES:
        cfg["type"] = mode["type"]
    if mode.get("start"):
        cfg["start"] = str(mode["start"])
    try:
        raw = mode.get("span")
        cfg["span"] = max(1, int(raw if raw not in (None, "") else 7))
    except (TypeError, ValueError):
        cfg["span"] = 7
    # S3 返工：置重算待办标志——周期配置已切换而物化列尚未重算，
    # recalc_cycle_starts 成功后清除（失败残留，由面板首载自愈追平）
    if app_settings.set(_CYCLE_KEY, cfg) and \
            app_settings.set(_CYCLE_RECALC_PENDING_KEY, True):
        # 写盘成功：置空周期模式缓存，下次读取按新配置生效
        _cycle_mode_cache = None
    return cfg


# ==================== 周期物化列重算待办标志 ====================

# S3 返工（2026-09-06）：保存周期成功 → 重算 worker 失败（DB 瞬时锁等）
# 的场景下，周期下拉按新口径（动态计算）而数据按旧口径（物化列）过滤，
# 命中错/空且无自动追平路径。本标志实现自愈：save 置 True → recalc
# 成功清除 → 面板每次打开时检查，有残留即全量重算（无需用户干预）。
_CYCLE_RECALC_PENDING_KEY = "aftersale_cycle_recalc_pending"


def _load_recalc_pending() -> bool:
    """读取重算待办标志（app_settings 门面，缺省回退 False）"""
    return bool(app_settings.get(_CYCLE_RECALC_PENDING_KEY))


def _save_recalc_pending(flag: bool) -> None:
    """写重算待办标志（False 时移除键，稳态 aftersale.json 无冗余键）"""
    if flag:
        app_settings.set(_CYCLE_RECALC_PENDING_KEY, True)
    else:
        app_settings.remove(_CYCLE_RECALC_PENDING_KEY)


# ==================== 常用句（实现见 database.aftersale_settings） ====================


def load_quick_phrases() -> list:
    """读取常用句列表（config/aftersale.json 持久化）。"""
    return aftersale_settings.load_quick_phrases()


def add_quick_phrase(text: str) -> list:
    """新增常用句，写回配置，返回更新后的列表。"""
    return aftersale_settings.add_quick_phrase(text)


def cycle_span_days() -> int:
    """当前模式周期天数：tue/mon 固定 7 天，custom 取配置值（≥1），month 取当月天数"""
    return aftersale_cycle.span_days(load_cycle_mode())


def cycle_start_of(dt: datetime) -> str:
    """计算给定时间所属周期的起始日，格式 yyyy/MM/dd

    模式：
    - tue（默认）：周二开始周一结束，days_since_tue=(weekday-1)%7
    - mon（自然周）：周一开始周日结束，days_since_mon=weekday
    - custom：从配置起始日起每 span 天一个周期，历史周期向过去对齐
    - month：自然月，当月 1 号为起始日
    """
    return aftersale_cycle.start_of(dt, load_cycle_mode())


def current_cycle_start() -> str:
    """当前周期起始日"""
    return aftersale_cycle.current_start(load_cycle_mode())


def _parse_occurred(occurred: str):
    """解析日期串 YYYY-MM-DD，非法返回 None 由调用方回退"""
    return aftersale_cycle.parse_occurred(occurred)


def cycle_label(cycle_start: str) -> str:
    """周期展示标签：周模式 '08/19 - 08/25'（起始日 + 周期天数-1，custom 按配置 span）；month 模式 '2026-08'（自然月）"""
    return aftersale_cycle.label(cycle_start, load_cycle_mode())


# ==================== 周期归属 ====================

def cycle_date_range(cycle_start: str) -> tuple:
    """周期起止日期（date, date）：month 模式为整月，其余为起始日 + span-1 天

    供统计弹窗「当前周期」趋势范围使用；month 模式按周期自身所在月份计算
    （不依赖当前时刻，历史周期同样正确）。非法输入返回 (None, None)。
    """
    return aftersale_cycle.date_range(cycle_start, load_cycle_mode())


def _record_cycle(occurred_at: str, created_at: str):
    """记录归属周期：优先按 occurred_at 计算，缺失/非法时回退 created_at，

    两者均无返回 None。列表筛选、统计、下拉选项、导出全部经此函数归属，
    保证四处口径一致；不依赖冗余落库的 cycle_start 字段。
    归属按当前周期配置（含 month 自然月）。
    """
    return aftersale_cycle.record_cycle(
        occurred_at, created_at, load_cycle_mode())


def _match_cycle(record: dict, cycle_start: str) -> bool:
    """记录是否属于所选周期：按记录时间动态计算后匹配，空周期不过滤"""
    return aftersale_cycle.match_cycle(record, cycle_start, load_cycle_mode())


# ==================== 连接与工具 ====================

def _conn():
    """复用 table_db 双后端连接，SQLite 单连接 和 MySQL thread-local"""
    return table_db.get_conn()


def _lookup_table_binding(table_no: str) -> tuple:
    """按桌号精确匹配球桌管理库，返回 snk_code, device_code

    桌号自由文本允许非标格式，匹配不到返回空串不阻断。device_code 取该球桌最近一期 kd 状态记录。
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

    created_at 自动取填写时刻；occurred_at（发生时间）缺省取当日；
    cycle_start 按发生时间归属周期，发生时间缺失时回退填写时间；
    snk_code/device_code 未提供时按桌号精确匹配球桌管理库自动带出。
    """
    now = datetime.now()
    created_at = now.strftime("%Y-%m-%d %H:%M:%S")
    occurred = str(record.get("occurred_at") or "").strip() or created_at[:10]
    occ_dt = _parse_occurred(occurred)
    cycle = cycle_start_of(occ_dt) if occ_dt else cycle_start_of(now)
    snk = str(record.get("snk_code") or "").strip()
    device = str(record.get("device_code") or "").strip()
    if not snk:
        snk, device = _lookup_table_binding(record.get("table_no"))
    conn = _conn()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        "INSERT INTO aftersale_records "
        "(created_at, occurred_at, creator, issue_type, table_no, room_name, "
        "region, problem, cause, resolved, is_initiative, is_our_problem, "
        "solution, resolver, response_time, snk_code, device_code, "
        "cycle_start, updated_at, is_important) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (created_at, occurred,
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
         snk, device, cycle, now_str,
         int(bool(record.get("is_important"))) ))
    conn.commit()
    _invalidate_field_cands_cache()
    return cur.lastrowid


def update_record(record: dict) -> int:
    """按 id 更新记录（created_at/cycle_start 保留原值），返回受影响行数

    多人场景：MySQL autocommit 提交后其他用户刷新即可见。
    """
    rec_id = record.get("id")
    if not rec_id:
        return 0
    # 编辑不改动填写时间；cycle_start 若缺失优先按发生时间重算，
    # 发生时间也缺失时回退填写时间，保证改发生时间后周期跟随
    occurred = str(record.get("occurred_at") or "").strip()
    created_at = str(record.get("created_at") or "")
    cycle = str(record.get("cycle_start") or "")
    if not cycle:
        occ_dt = _parse_occurred(occurred)
        if occ_dt:
            cycle = cycle_start_of(occ_dt)
        elif created_at:
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
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        "UPDATE aftersale_records SET occurred_at=?, creator=?, issue_type=?, "
        "table_no=?, room_name=?, region=?, problem=?, cause=?, resolved=?, "
        "is_initiative=?, is_our_problem=?, solution=?, "
        "resolver=?, response_time=?, snk_code=?, device_code=?, cycle_start=?, "
        "updated_at=?, is_important=? "
        "WHERE id=?",
        (occurred,
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
         snk, device, cycle, now_str,
         int(bool(record.get("is_important"))), rec_id))
    conn.commit()
    _invalidate_field_cands_cache()
    return cur.rowcount


def delete_record(rec_id) -> int:
    """按 id 删除记录，返回受影响行数"""
    if not rec_id:
        return 0
    conn = _conn()
    cur = conn.execute("DELETE FROM aftersale_records WHERE id = ?", (rec_id,))
    conn.commit()
    _invalidate_field_cands_cache()
    return cur.rowcount


def mark_resolved_batch(rec_ids) -> int:
    """批量标记已解决，返回受影响行数

    供列表「一键标记已解决 / 批量标记」使用：区别于 update_record 的全字段
    回写，不依赖 UI 提供其余字段原值，不会误改其他字段。
    """
    ids = [i for i in rec_ids if i]
    if not ids:
        return 0
    conn = _conn()
    qs = ", ".join(["?"] * len(ids))
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        f"UPDATE aftersale_records SET resolved = '是', updated_at = ? "
        f"WHERE id IN ({qs})", [now_str] + list(ids))
    conn.commit()
    _invalidate_field_cands_cache()
    return cur.rowcount


def delete_records(rec_ids) -> int:
    """按 id 批量删除记录，返回受影响行数"""
    ids = [i for i in rec_ids if i]
    if not ids:
        return 0
    conn = _conn()
    qs = ", ".join(["?"] * len(ids))
    cur = conn.execute(f"DELETE FROM aftersale_records WHERE id IN ({qs})", ids)
    conn.commit()
    _invalidate_field_cands_cache()
    return cur.rowcount


# ==================== 查询 ====================



# 非法周期起点判定：传入的 cycle_start 不是当前模式下的真实周期起点
# （如 tue 模式传入周三日期），语义上无任何记录命中 → 追加 WHERE 1=0。
def _cycle_is_valid_start(cycle_start: str):
    """周期起点合法性判定，返回 True / False / None

    - True：当前模式下的真实周期起点（yyyy/MM/dd，cycle_start_of 校验
      起点回到自身），正常走物化列过滤；
    - False：格式合法但非本模式真实周期起点（周/月口径混用）→ 0 命中；
    - None：无法解析（与旧实现一致，调用方不追加过滤）。
    """
    return aftersale_cycle.is_valid_start(cycle_start, load_cycle_mode())


# 物化列兜底回填的连续失效计数（日期形似但不可解析/回填异常时防循环）
_cycle_recalc_futile = 0


def _legacy_cycle_rows_pending() -> bool:
    """是否存在 cycle_start 为空但记录日期可解析的存量行（物化列待回填）

    走 idx_aftersale_cycle 索引探测（cycle_start = '' 等值），干净库上
    微秒级。LIKE '____-__-__%' 与 _parse_occurred 的 yyyy-MM-dd 外形校验
    一致，仅命中可能被回填的行。
    """
    conn = _conn()
    row = conn.execute(
        "SELECT 1 FROM aftersale_records WHERE cycle_start = '' AND "
        "(occurred_at LIKE '____-__-__%' OR created_at LIKE '____-__-__%') "
        "LIMIT 1").fetchone()
    return row is not None


def _ensure_cycle_materialized() -> None:
    """周期物化列兜底：检测到存量空值行时幂等回填一次（S3）

    周期筛选走 cycle_start 物化列（索引化），直写库的历史数据（早期
    Excel 导入/外部写入）该列可能为空，物化过滤会漏掉它们——检测到
    即同步重算（_record_cycle 同口径，幂等）。回填后仍有残留（日期
    形似但不可解析，_record_cycle 返回 None 写 ''）或回填异常（只读
    库等）时递增失效计数，进程内最多重试 2 次，避免每次查询全表重算。
    """
    global _cycle_recalc_futile
    if _cycle_recalc_futile >= 2:
        return
    try:
        if _legacy_cycle_rows_pending():
            recalc_cycle_starts()
            if _legacy_cycle_rows_pending():
                _cycle_recalc_futile += 1
            else:
                _cycle_recalc_futile = 0
        else:
            _cycle_recalc_futile = 0
    except Exception:
        _cycle_recalc_futile += 1


def recalc_cycle_starts(batch: int = 2000) -> int:
    """按当前周期配置全表重算 cycle_start 物化列，返回更新行数（幂等）

    分批（默认 2000 行/批）SELECT id, occurred_at, created_at,
    cycle_start → _record_cycle 计算新周期起点 → 与现值不同的收集后
    executemany UPDATE；_record_cycle 返回 None（两日期均无法解析）的
    记录写 ''。周期配置保存后、面板首次加载兜底、存量数据一次性回填
    共用本函数；二次执行无差异时返回 0（无感知）。成功路径清除
    重算待办标志（先读后写，稳态无标志时不产生 settings.json 写盘）。
    """
    conn = _conn()
    updated = 0
    last_id = 0
    while True:
        rows = conn.execute(
            "SELECT id, occurred_at, created_at, cycle_start "
            "FROM aftersale_records WHERE id > ? ORDER BY id LIMIT ?",
            (last_id, int(batch))).fetchall()
        if not rows:
            break
        last_id = rows[-1][0]
        updates = []
        for rec_id, occ, cre, cur in rows:
            new = _record_cycle(occ, cre) or ""
            if str(cur or "") != new:
                updates.append((new, rec_id))
        if updates:
            conn.executemany(
                "UPDATE aftersale_records SET cycle_start = ? WHERE id = ?",
                updates)
            updated += len(updates)
    conn.commit()
    # 重算成功 → 清除待办标志（任何触发路径统一在此闭环；失败/异常不清除，
    # 由面板下次打开经 recalc_cycle_starts_on_load 自愈）
    try:
        if _load_recalc_pending():
            _save_recalc_pending(False)
    except Exception:
        pass
    return updated


def recalc_cycle_starts_on_load() -> int:
    """面板首载兜底：有待办标志或存量空值行 → 全量重算，否则仅索引探测

    S3 返工（2026-09-06）：上次保存周期后的重算失败会残留
    aftersale_cycle_recalc_pending 标志——周期下拉按新口径（动态计算）、
    数据按旧口径（物化列）过滤，命中错/空。本函数在面板每次打开时检查：
    有待办标志（无论空值探测结果）或探测到空值存量行 → 全量
    recalc_cycle_starts（成功即清标志），否则走微秒级空值探测返回 0，
    正常路径零额外成本。幂等，可放心重复调用。
    """
    if _load_recalc_pending() or _legacy_cycle_rows_pending():
        return recalc_cycle_starts()
    return 0




# ==================== 查询实现（database.aftersale_queries） ====================


def _append_cycle_where(where: str, params: list, cycle_start: str) -> tuple:
    """兼容入口：周期筛选拼接（实现见 database.aftersale_queries）。"""
    return aftersale_queries._append_cycle_where(where, params, cycle_start)


def query_page(page_no: int, page_size: int, keyword: str = "",
               cycle_start: str = "", issue_type: str = "",
               resolved: str = "", is_initiative: str = "",
               is_our_problem: str = "", sort_key: str = "",
               sort_order: str = "desc") -> tuple:
    """分页查询（实现见 database.aftersale_queries）。"""
    return aftersale_queries.query_page(
        page_no, page_size, keyword, cycle_start, issue_type, resolved,
        is_initiative, is_our_problem, sort_key, sort_order)


def query_with_stats(page_no: int, page_size: int, keyword: str = "",
                     cycle_start: str = "", issue_type: str = "",
                     resolved: str = "", is_initiative: str = "",
                     is_our_problem: str = "", sort_key: str = "",
                     sort_order: str = "desc") -> tuple:
    """分页 + 同口径统计（实现见 database.aftersale_queries）。"""
    return aftersale_queries.query_with_stats(
        page_no, page_size, keyword, cycle_start, issue_type, resolved,
        is_initiative, is_our_problem, sort_key, sort_order)


def query_stats_detail(keyword: str = "", cycle_start: str = "",
                       issue_type: str = "", trend_start: str = "",
                       trend_end: str = "") -> dict:
    """统计弹窗详细统计（实现见 database.aftersale_queries）。"""
    return aftersale_queries.query_stats_detail(
        keyword, cycle_start, issue_type, trend_start, trend_end)


def query_rank(level: str = "room", limit: int = 10, sort: str = "total",
               start: str = "", end: str = "", cycle_start: str = "",
               resolved: str = "", is_initiative: str = "",
               is_our_problem: str = "", region: str = "",
               room_name: str = "", keyword: str = "") -> dict:
    """球房/球桌排行（实现见 database.aftersale_queries）。"""
    return aftersale_queries.query_rank(
        level, limit, sort, start, end, cycle_start, resolved, is_initiative,
        is_our_problem, region, room_name, keyword)


def get_cycle_options() -> list:
    """周期下拉选项（实现见 database.aftersale_queries）。"""
    return aftersale_queries.get_cycle_options()



# ---------------- 填写记忆 / 自动刷新（实现见 database.aftersale_settings） ----------------


def _load_settings_str(key: str) -> str:
    """读取配置中指定字符串键（兼容旧私有入口）。"""
    return aftersale_settings.load_settings_str(key)


def load_last_creator() -> str:
    """上次填写的填写人（config/aftersale.json，无则空串）。"""
    return aftersale_settings.load_last_creator()


def load_last_resolver() -> str:
    """上次填写的解决人（config/aftersale.json，无则空串）。"""
    return aftersale_settings.load_last_resolver()


def save_last_people(creator: str, resolver: str) -> None:
    """记住本次填写的填写人/解决人，供下次打开面板恢复（非空才写）。"""
    return aftersale_settings.save_last_people(creator, resolver)


def remember_occurred_enabled() -> bool:
    """「记住上次发生日期」开关（config/aftersale.json，缺省开启）。"""
    return aftersale_settings.remember_occurred_enabled()


def load_last_occurred() -> str:
    """上次新增记录的发生日期（yyyy-MM-dd）；开关关闭或无记录返回空串。"""
    return aftersale_settings.load_last_occurred()


def save_last_occurred(occurred: str) -> None:
    """记住本次新增记录的发生日期（开关关闭 / 空值时不写）。"""
    return aftersale_settings.save_last_occurred(occurred)


def set_remember_occurred(enabled: bool) -> None:
    """写「记住上次发生日期」开关；关闭时一并清除已记住日期。"""
    return aftersale_settings.set_remember_occurred(enabled)


def auto_refresh_enabled() -> bool:
    """「自动刷新记录」开关（config/aftersale.json，缺省关闭）。"""
    return aftersale_settings.auto_refresh_enabled()


def auto_refresh_interval() -> int:
    """自动刷新间隔秒数（缺省 30；仅接受 ≥5 的整数）。"""
    return aftersale_settings.auto_refresh_interval()


def set_auto_refresh(enabled: bool) -> None:
    """写「自动刷新记录」开关（统一设置-面板设置-售后 联动入口）。"""
    return aftersale_settings.set_auto_refresh(enabled)


def set_auto_refresh_interval(seconds: int) -> None:
    """写自动刷新间隔秒数（<5 秒的入参按 5 秒下限钳制）。"""
    return aftersale_settings.set_auto_refresh_interval(seconds)


def change_fingerprint() -> tuple:
    """全表轻量指纹 (条数, 最大 updated_at, 最大 id)——自动刷新判据。

    轮询只跑这一条聚合 SQL（COUNT/MAX 全走不了索引也仅毫秒级），
    与 RecordsPage 上次快照比对：一致则完全不动表格（保滚动位置、
    勾选与展开状态），不一致才触发一次静默重查。
    删除不改 MAX(updated_at)，故 COUNT 也入指纹；id 单调增兜底
    同秒批量写入。MySQL autocommit 下他人提交即对本查询可见。
    """
    conn = _conn()
    row = conn.execute(
        "SELECT COUNT(*), MAX(updated_at), MAX(id) "
        "FROM aftersale_records").fetchone()
    return (int(row[0] or 0), str(row[1] or ""), int(row[2] or 0))


# S4（2026-09-06）：动态候选进程内缓存（写后失效）——get_field_candidates
# 是 4 条 GROUP BY 全表聚合（100k 内存库单次 94ms），录入页 showEvent 与
# 新增/编辑弹窗每次打开都触发，数据未变时纯属重复劳动。写操作成功后
# 置空失效（insert/update/delete/mark_resolved_batch/delete_records/import）。
_field_cands_cache: dict | None = None


def _invalidate_field_cands_cache() -> None:
    """写操作成功后置空候选缓存（下次 get_field_candidates 重建）"""
    global _field_cands_cache
    _field_cands_cache = None


def get_field_candidates() -> dict:
    """动态候选：问题/解决人/地区/填写人（按使用频次降序，各取前 60）

    命中进程内缓存返回深副本（列表可变，防调用方污染缓存）。
    """
    global _field_cands_cache
    if _field_cands_cache is not None:
        return {k: list(v) for k, v in _field_cands_cache.items()}
    conn = _conn()
    out = {}
    for key, field in (("problems", "problem"),
                       ("resolvers", "resolver"),
                       ("regions", "region"),
                       ("creators", "creator")):
        cur = conn.execute(
            f"SELECT {field}, COUNT(*) FROM aftersale_records "
            f"WHERE {field} != '' GROUP BY {field} "
            f"ORDER BY COUNT(*) DESC LIMIT 60")
        out[key] = [r[0] for r in cur.fetchall()]
    # 问题候选合并预置常见项（新库无历史数据时下拉不为空）
    if not out["problems"]:
        out["problems"] = ["主机没有开机", "遥控器没反应", "程序没了",
                           "不能扫码", "识别不了", "记分牌显示不出来"]
    # 填写人/解决人候选合并团队默认人员（恒置顶、去重、保持前 60）
    for key in ("resolvers", "creators"):
        seen = set()
        merged = []
        for name in list(_CREW_PRESET) + list(out[key]):
            if name and name not in seen:
                seen.add(name)
                merged.append(name)
        out[key] = merged[:60]
    _field_cands_cache = {k: list(v) for k, v in out.items()}
    return out


# ==================== Excel 导出 / 导入（database.aftersale_excel） ====================


def _safe_sheet_title(title: str) -> str:
    """Excel sheet 名约束（实现见 database.aftersale_excel）。"""
    return aftersale_excel._safe_sheet_title(title)


def _write_export_sheet(ws, rows):
    """写导出 sheet 公共逻辑（实现见 database.aftersale_excel）。"""
    return aftersale_excel._write_export_sheet(ws, rows)


def _write_stats_sheet(wb, rows):
    """写统计图表 sheet（实现见 database.aftersale_excel）。"""
    return aftersale_excel._write_stats_sheet(wb, rows)


def export_xlsx(path: str, keyword: str = "", cycle_start: str = "",
                issue_type: str = "", resolved: str = "",
                is_initiative: str = "", is_our_problem: str = "") -> int:
    """按筛选条件导出 xlsx（实现见 database.aftersale_excel）。"""
    return aftersale_excel.export_xlsx(
        path, keyword, cycle_start, issue_type, resolved,
        is_initiative, is_our_problem)


def parse_excel_rows(xlsx_path: str) -> tuple:
    """解析售后汇总 Excel（实现见 database.aftersale_excel）。"""
    return aftersale_excel.parse_excel_rows(xlsx_path)


def import_excel_rows(xlsx_path: str) -> int:
    """一次性导入历史 Excel（实现见 database.aftersale_excel）。"""
    return aftersale_excel.import_excel_rows(xlsx_path)
