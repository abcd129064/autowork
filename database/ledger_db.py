# -*- coding: utf-8 -*-
"""跑视频记录数据层（SQLite / MySQL 双后端，自动跟随 MySQL 测试开关）

字段来源：在线模板.xlsx 的问题/未复现/精度/使用 四个数据 sheet
（sheet 名即 category 分类；公共列 类别|球房|视频名|帧数|描述|新程序|
备注|署名，精度/使用 多一列「复现」）。

职责：
- insert_record / update_record / delete_record：跑视频记录增删改
- query_page：分类/类别/署名/关键词筛选 + 分页
- stats_by_signer：按署名统计四分类计数（模板「计数」sheet 的电子版）
- get_kind_candidates：类别候选（模板解析预置 + 库中历史自由输入合并）
- export_xlsx：按分类分 sheet 导出（结构与在线模板一致，仅作线下汇报用）

连接复用 table_db 双后端路由（SQLite 单连接 / MySQL thread-local），
MySQL 模式开启后读写直接落在服务器 MySQL，多人提交实时可见；
MySQL 不可用时自动降级本地 SQLite，恢复后由 merge_back 合并回 MySQL。
"""
from datetime import datetime

from core import app_settings
from database import table_db

# ==================== 字段枚举（来源：在线模板.xlsx 解析） ====================

# 分类：模板四个数据 sheet 名（sheet 即分类维度）
CATEGORIES = ("问题", "未复现", "精度", "使用")

# 类别候选：模板各 sheet「类别」列的历史取值（允许自由输入新类别）
KIND_CANDIDATES = {
    "问题": (
        "颜色识别", "遮挡问题", "撞击识别:目标球", "撞击识别:其他",
        "前端问题", "后端问题", "识别端其他问题", "提前切杆:缓慢进袋",
        "提前切杆:其他", "丢杆", "丢球:袋口", "丢球:非袋口", "进袋未识别",
    ),
    "未复现": (
        "颜色识别", "遮挡问题", "撞击识别:目标球", "撞击识别:其他",
        "前端问题", "后端问题", "识别端其他问题", "提前切杆:缓慢进袋",
        "提前切杆:其他", "丢杆", "丢球:袋口", "丢球:非袋口", "进袋未识别",
    ),
    "精度": (
        "轻贴:不动", "轻贴:有动", "薄边:不动", "薄边:有动",
        "同时击中", "白球微动", "白球不动",
    ),
    "使用": (
        "让杆/换人", "让分", "开局", "局末", "手动球", "复位", "击球失误",
        "贴球", "击球过快", "球打飞", "遮挡", "关灯", "袋口球未落下",
        "加速开局前动球", "进袋阻挡未计分", "罚分", "袋口满进球未识别",
        "自由球选错", "误操作", "目标球", "擦球", "其他",
    ),
}

# 记录字段（与建表 DDL 一致）
# 顺序同时是 INSERT 的列顺序与 query_page 的取值顺序，调整要同步建表 DDL
# occurred_at：视频/记录日期（需求7，用户看昨天的视频时指定，默认当天）
RECORD_FIELDS = (
    "category", "kind", "room_name", "video_name", "frame",
    "description", "repro", "new_program", "remark", "signer",
    "occurred_at", "created_at", "updated_at",
)

# 跑视频业务键（多用户共享去重/合并定位用）：SQLite 与 MySQL 两侧 id
# 各自增长会撞车，按 (created_at, signer, category, kind, video_name)
# 判定同一条记录。merge_back 共用此定义（单一来源）。
RECORD_KEY_COLS = ("created_at", "signer", "category", "kind", "video_name")

# 关键词搜索覆盖列（全字段模糊匹配）
_SEARCH_FIELDS = (
    "category", "kind", "room_name", "video_name",
    "description", "repro", "remark", "signer",
)

# 导出表头（与在线模板数据 sheet 对齐；精度/使用 多「复现」列）。
# 中文名与模板逐字对齐：改一个字，线下报表和系统导出就会错列。
# 分类（category）由 sheet 名表达，不占列；「日期」为系统附加列（occurred_at）。
_EXPORT_HEADERS = (
    ("occurred_at", "日期"), ("kind", "类别"), ("room_name", "球房"),
    ("video_name", "视频名"), ("frame", "帧数"), ("description", "描述"),
    ("repro", "复现"), ("new_program", "新程序"), ("remark", "备注"),
    ("signer", "署名"),
)


def _now_str() -> str:
    """统一的时间串格式：yyyy-MM-dd HH:mm:ss，写库与比较都按它。

    不用 ISO 的 T 分隔：occurred_at 要取前 10 位与纯日期串直接比较，
    带 T 的格式会让「YYYY-MM-DD」截断比较错位。
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean(value) -> str:
    """把 None 与任意值收敛成字符串，写库前统一走它。

    NULL 和字符串 "None" 混进表里，查询侧就得多写一堆 IS NULL 分支，
    空串即「未填」，所有过滤与统计只认这一种表示。
    """
    return "" if value is None else str(value)


# ==================== 增删改 ====================

def insert_record(record: dict) -> int:
    """新增跑视频记录，返回新记录 id。

    id 由数据库自增分配，调用方拿返回值刷新表格；SQLite 与 MySQL 两侧
    id 各自增长会撞车，跨端同步只认 RECORD_KEY_COLS 业务键。
    """
    conn = table_db.get_conn()
    now = _now_str()
    # 列顺序取 RECORD_FIELDS，与建表 DDL 一致；不要按 dict 顺序拼 INSERT
    cols = [c for c in RECORD_FIELDS]
    # 缺字段按空串落库而不是 NULL：查询侧不做 IS NULL 分支，空串即「未填」
    vals = [_clean(record.get(c)) for c in cols]
    # created_at / updated_at 由服务端统一维护，调用方传入值忽略
    vals[cols.index("created_at")] = now
    vals[cols.index("updated_at")] = now
    # 视频日期 occurred_at：调用方可指定（看昨天的视频填昨天），
    # 缺省取当天（需求7，按天统计据此过滤）
    if not vals[cols.index("occurred_at")].strip():
        vals[cols.index("occurred_at")] = now[:10]
    # 占位符按列数生成，列名只来自 RECORD_FIELDS 白名单，没有注入面
    cur = conn.execute(
        f"INSERT INTO ledger_records ({', '.join(cols)}) "
        f"VALUES ({', '.join(['?'] * len(cols))})", vals)
    conn.commit()
    return int(cur.lastrowid or 0)


def update_record(record_id: int, record: dict) -> bool:
    """更新跑视频记录（仅允许修改业务字段，分类/时间保留服务端值）。

    按 RECORD_FIELDS 白名单过滤：调用方即使传 created_at 也写不进去，
    时间戳只由本模块维护，否则每条记录的时间线都能被前端改掉。
    """
    conn = table_db.get_conn()
    sets = []
    vals = []
    for c in RECORD_FIELDS:
        # 时间戳不在可写集合：created_at 是审计依据，updated_at 由下面统一刷新
        if c in ("created_at", "updated_at"):
            continue
        if c not in record:
            continue
        sets.append(f"{c} = ?")
        vals.append(_clean(record.get(c)))
    # 没有任何可更新字段就直接返回，不去发一条只为刷 updated_at 的空 UPDATE
    if not sets:
        return False
    sets.append("updated_at = ?")
    vals.append(_now_str())
    vals.append(int(record_id))
    cur = conn.execute(
        f"UPDATE ledger_records SET {', '.join(sets)} WHERE id = ?", vals)
    conn.commit()
    return cur.rowcount > 0


def delete_record(record_id: int) -> bool:
    """删除跑视频记录（物理删除，rowcount 为 0 表示记录已不存在）。

    本层不做二次确认：确认属于界面职责，数据层只保证按主键精确定位，
    误删会直接丢数据，没有回收站可退。
    """
    conn = table_db.get_conn()
    # 按主键删除，rowcount 为 0 说明记录已被别人删掉，按失败回报
    cur = conn.execute("DELETE FROM ledger_records WHERE id = ?",
                       (int(record_id),))
    conn.commit()
    return cur.rowcount > 0


# ==================== 查询 ====================

def _build_where(keyword: str = "", category: str = "",
                 kind: str = "", signer: str = "",
                 date_from: str = "", date_to: str = "",
                 repro: str = "") -> tuple:
    """构造 WHERE 子句与参数列表（SQLite 占位符 ?，MySQL 侧由 backend 自动转换）

    date_from/date_to：按视频日期 occurred_at 过滤（需求7 按天统计；
    COALESCE 兼容旧库 occurred_at 为空的历史记录回退 created_at）。
    取前 10 位（YYYY-MM-DD）做日期串比较：occurred_at 存纯日期、
    created_at 存完整时间两种格式均兼容；date_from/date_to 传纯日期。
    repro：是否复现精确过滤（"是"/"否"；空串不过滤，需求6）。
    """
    # 条件逐个追加、空串即不过滤：前端把「全部」换成空串传下来，后端不需要单独枚举
    where, params = [], []
    if category:
        where.append("category = ?")
        params.append(category)
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if signer:
        where.append("signer = ?")
        params.append(signer)
    if repro:
        where.append("repro = ?")
        params.append(str(repro).strip())
    if date_from:
        where.append("substr(COALESCE(NULLIF(occurred_at, ''), created_at),"
                     " 1, 10) >= ?")
        params.append(str(date_from).strip())
    if date_to:
        where.append("substr(COALESCE(NULLIF(occurred_at, ''), created_at),"
                     " 1, 10) <= ?")
        params.append(str(date_to).strip())
    kw = str(keyword or "").strip()
    if kw:
        # 关键词跨列是 OR、与其它条件是 AND；括号必须留着，否则 OR 会吃掉前面的过滤
        like = " OR ".join(f"{f} LIKE ?" for f in _SEARCH_FIELDS)
        where.append(f"({like})")
        params.extend([f"%{kw}%"] * len(_SEARCH_FIELDS))
    # 无条件时不加 WHERE：COUNT 与取页共用这一条子句，避免两处各拼一份拼错
    sql = (" WHERE " + " AND ".join(where)) if where else ""
    return sql, params


def query_page(page_no: int, page_size: int, keyword: str = "",
               category: str = "", kind: str = "", signer: str = "",
               date_from: str = "", date_to: str = "",
               repro: str = "") -> tuple:
    """分页查询跑视频记录，返回 (total, rows)；rows 为 dict 列表（含 id）

    date_from/date_to 按视频日期 occurred_at 过滤（需求7，"" 表示不过滤）；
    repro 是否复现精确过滤（需求6，"" 表示不过滤）。

    先 COUNT 再 LIMIT/OFFSET：分页器要总数，两次查询共用同一个 where；
    中间若有并发写入，总数与当前页可能略有偏差，对本场景可接受。
    """
    conn = table_db.get_conn()
    where, params = _build_where(keyword, category, kind, signer,
                                 date_from, date_to, repro)
    # COUNT 与下面的取页共用同一份 where/params，筛选条件只构造一次
    total = conn.execute(
        f"SELECT COUNT(*) FROM ledger_records{where}", params).fetchone()[0]
    # page_no 从 1 开始；异常入参（0 或负数）钳到 0，不把异常抛给界面
    offset = max(0, int(page_no) - 1) * int(page_size)
    cur = conn.execute(
        f"SELECT id, {', '.join(RECORD_FIELDS)} FROM ledger_records"
        f"{where} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [int(page_size), offset])
    # 用游标描述符取列名：SELECT 里额外拼了 id，也能自动对上，不必手写列名表
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return int(total or 0), rows


def get_kind_candidates(category: str) -> list:
    """类别候选：模板预置 + 库中该分类历史自由输入合并（去重保序）。

    保序是为了让预置项稳定在前、新值追加在后：下拉顺序随数据变化，
    用户习惯的选项位置就会跳动。
    """
    preset = list(KIND_CANDIDATES.get(category, ()))
    try:
        conn = table_db.get_conn()
        rows = conn.execute(
            "SELECT DISTINCT kind FROM ledger_records WHERE category = ?",
            (category,)).fetchall()
    # 读库失败按「只有预置项」处理：类别下拉拿不到历史值也必须能继续录入
    except Exception:
        rows = []
    for r in rows:
        v = str(r[0] or "").strip()
        if v and v not in preset:
            preset.append(v)
    return preset


def stats_by_signer() -> list:
    """按署名统计四分类计数（模板「计数」sheet 的电子版）

    返回 [{"signer": 署名, "问题": n, "未复现": n, "精度": n,
    "使用": n, "total": n}, ...]，按 total 降序。

    空署名归到「未署名」：模板允许不填署名，统计时不能把这些记录漏掉。
    """
    conn = table_db.get_conn()
    rows = conn.execute(
        "SELECT signer, category, COUNT(*) FROM ledger_records "
        "GROUP BY signer, category").fetchall()
    stat: dict = {}
    for signer, category, count in rows:
        signer = str(signer or "").strip() or "未署名"
        # 每个署名一个 bucket，分类按 category 动态落键，四分类之外的值也不会丢
        bucket = stat.setdefault(signer, {"signer": signer, "total": 0})
        bucket[category] = int(count or 0)
        bucket["total"] += int(count or 0)
    out = []
    for bucket in stat.values():
        for c in CATEGORIES:
            bucket.setdefault(c, 0)
        out.append(bucket)
    # 总数降序、署名升序：同量级按名字排，界面两次刷新的顺序不会乱跳
    out.sort(key=lambda b: (-b["total"], b["signer"]))
    return out


# ==================== 导出（线下汇报用，与在线模板同结构） ====================

def export_xlsx(path: str, category: str = "") -> int:
    """按分类分 sheet 导出为 xlsx（结构与在线模板一致），返回导出行数

    仅作线下汇报/交接用：系统数据源是数据库（MySQL 开启时即服务器侧），
    xlsx 是派生副本而非存储。category 非空时只导出该分类。

    导出才加载 openpyxl：启动路径不背这个依赖，没装也不影响日常录数据。
    """
    # 延迟 import 放函数内：不导出就完全不加载 openpyxl
    from openpyxl import Workbook

    conn = table_db.get_conn()
    wb = Workbook()
    # 删掉默认 sheet：Workbook() 自带一张空表，留着会导出多余的空 sheet
    wb.remove(wb.active)
    targets = [category] if category else list(CATEGORIES)
    exported = 0
    # 一个分类一个 sheet，sheet 名即分类名，与在线模板的读法保持一致
    for cat in targets:
        ws = wb.create_sheet(cat)
        ws.append([h for _, h in _EXPORT_HEADERS])
        cur = conn.execute(
            "SELECT " + ", ".join(c for c, _ in _EXPORT_HEADERS) +
            " FROM ledger_records WHERE category = ? ORDER BY id",
            (cat,))
        for r in cur.fetchall():
            ws.append([_clean(v) for v in r])
            exported += 1
    wb.save(path)
    return exported


# ==================== 记住上次视频日期（连续录入体验，仿售后范式） ====================

_REMEMBER_OCCURRED_KEY = "ledger_remember_occurred"
_LAST_OCCURRED_KEY = "ledger_last_occurred"


def remember_occurred_enabled() -> bool:
    """「记住上次视频日期」开关（misc 域，缺省开启）。

    缺省开启：现场连续补录同一天的多条记录是常态，记不住就得反复拨日期。
    """
    return bool(app_settings.get(_REMEMBER_OCCURRED_KEY, True))


def load_last_occurred() -> str:
    """上次新增记录的视频日期（yyyy-MM-dd）；开关关闭或无记录返回空串。

    供 LedgerForm 计算日期默认值——连续补录同一天的多条记录时免重复拨日期。
    """
    # 开关关闭时直接返回空串，让表单退回当天；默认值计算不放在这一层
    if not remember_occurred_enabled():
        return ""
    v = app_settings.get(_LAST_OCCURRED_KEY)
    if isinstance(v, str) and v.strip():
        return v.strip()
    return ""


def save_last_occurred(occurred: str) -> None:
    """记住本次新增记录的视频日期（开关关闭 / 空值时不写）。

    写失败不抛：这个键只影响下次的默认值，丢一次记忆不该阻断保存流程。
    """
    occurred = (occurred or "").strip()
    if not occurred or not remember_occurred_enabled():
        return
    app_settings.set(_LAST_OCCURRED_KEY, occurred)


def set_remember_occurred(enabled: bool) -> None:
    """写「记住上次视频日期」开关（统一设置-面板设置-跑视频 联动入口）。

    关闭时一并清除已记住的日期：重新打开后从「当日」重新开始。
    """
    app_settings.set(_REMEMBER_OCCURRED_KEY, bool(enabled))
    if not enabled:
        app_settings.remove(_LAST_OCCURRED_KEY)


# ==================== 记录自动刷新（他人填写免手动同步，仿售后范式） ====================

_AUTO_REFRESH_KEY = "ledger_auto_refresh"
_AUTO_REFRESH_INTERVAL_KEY = "ledger_auto_refresh_interval"


def auto_refresh_enabled() -> bool:
    """「自动刷新记录」开关（misc 域，缺省关闭）"""
    return bool(app_settings.get(_AUTO_REFRESH_KEY, False))


def auto_refresh_interval() -> int:
    """自动刷新间隔秒数（缺省 30；仅接受 ≥5 的整数，防误配高频轮询）"""
    try:
        v = int(app_settings.get(_AUTO_REFRESH_INTERVAL_KEY, 30))
    except (TypeError, ValueError):
        return 30
    # 非法值与小于 5 的入参都退回 30：设置文件可能被手改成小数或字符串
    return v if v >= 5 else 30


def set_auto_refresh(enabled: bool) -> None:
    """写「自动刷新记录」开关（统一设置-面板设置-跑视频 联动入口）"""
    app_settings.set(_AUTO_REFRESH_KEY, bool(enabled))


def set_auto_refresh_interval(seconds: int) -> None:
    """写自动刷新间隔秒数（<5 秒的入参按 5 秒下限钳制）"""
    try:
        v = int(seconds)
    except (TypeError, ValueError):
        return
    # 写入口再钳一次下限：绕过界面直接调用也不会把轮询设成 1 秒
    app_settings.set(_AUTO_REFRESH_INTERVAL_KEY, max(5, v))


def change_fingerprint() -> tuple:
    """全表轻量指纹 (条数, 最大 updated_at, 最大 id)——自动刷新判据。

    轮询只跑这一条聚合 SQL（COUNT/MAX 毫秒级），与记录页上次快照比对：
    一致则完全不动表格（保滚动位置），不一致才触发一次静默重查。
    删除不改 MAX(updated_at)，故 COUNT 也入指纹；id 单调增兜底同秒批量写入。

    只跑聚合不取行：轮询可能到秒级，拉全表会把界面拖慢。
    """
    conn = table_db.get_conn()
    row = conn.execute(
        "SELECT COUNT(*), MAX(updated_at), MAX(id) "
        "FROM ledger_records").fetchone()
    return (int(row[0] or 0), str(row[1] or ""), int(row[2] or 0))
