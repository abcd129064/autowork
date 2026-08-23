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
RECORD_FIELDS = (
    "category", "kind", "room_name", "video_name", "frame",
    "description", "repro", "new_program", "remark", "signer",
    "created_at", "updated_at",
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
# 分类（category）由 sheet 名表达，不占列。
_EXPORT_HEADERS = (
    ("kind", "类别"), ("room_name", "球房"), ("video_name", "视频名"),
    ("frame", "帧数"), ("description", "描述"), ("repro", "复现"),
    ("new_program", "新程序"), ("remark", "备注"), ("signer", "署名"),
)


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean(value) -> str:
    return "" if value is None else str(value)


# ==================== 增删改 ====================

def insert_record(record: dict) -> int:
    """新增跑视频记录，返回新记录 id"""
    conn = table_db.get_conn()
    now = _now_str()
    cols = [c for c in RECORD_FIELDS]
    vals = [_clean(record.get(c)) for c in cols]
    # created_at / updated_at 由服务端统一维护，调用方传入值忽略
    vals[cols.index("created_at")] = now
    vals[cols.index("updated_at")] = now
    cur = conn.execute(
        f"INSERT INTO ledger_records ({', '.join(cols)}) "
        f"VALUES ({', '.join(['?'] * len(cols))})", vals)
    conn.commit()
    return int(cur.lastrowid or 0)


def update_record(record_id: int, record: dict) -> bool:
    """更新跑视频记录（仅允许修改业务字段，分类/时间保留服务端值）"""
    conn = table_db.get_conn()
    sets = []
    vals = []
    for c in RECORD_FIELDS:
        if c in ("created_at", "updated_at"):
            continue
        if c not in record:
            continue
        sets.append(f"{c} = ?")
        vals.append(_clean(record.get(c)))
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
    """删除跑视频记录"""
    conn = table_db.get_conn()
    cur = conn.execute("DELETE FROM ledger_records WHERE id = ?",
                       (int(record_id),))
    conn.commit()
    return cur.rowcount > 0


# ==================== 查询 ====================

def _build_where(keyword: str = "", category: str = "",
                 kind: str = "", signer: str = "") -> tuple:
    """构造 WHERE 子句与参数列表（SQLite 占位符 ?，MySQL 侧由 backend 自动转换）"""
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
    kw = str(keyword or "").strip()
    if kw:
        like = " OR ".join(f"{f} LIKE ?" for f in _SEARCH_FIELDS)
        where.append(f"({like})")
        params.extend([f"%{kw}%"] * len(_SEARCH_FIELDS))
    sql = (" WHERE " + " AND ".join(where)) if where else ""
    return sql, params


def query_page(page_no: int, page_size: int, keyword: str = "",
               category: str = "", kind: str = "", signer: str = "") -> tuple:
    """分页查询跑视频记录，返回 (total, rows)；rows 为 dict 列表（含 id）"""
    conn = table_db.get_conn()
    where, params = _build_where(keyword, category, kind, signer)
    total = conn.execute(
        f"SELECT COUNT(*) FROM ledger_records{where}", params).fetchone()[0]
    offset = max(0, int(page_no) - 1) * int(page_size)
    cur = conn.execute(
        f"SELECT id, {', '.join(RECORD_FIELDS)} FROM ledger_records"
        f"{where} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [int(page_size), offset])
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return int(total or 0), rows


def get_kind_candidates(category: str) -> list:
    """类别候选：模板预置 + 库中该分类历史自由输入合并（去重保序）"""
    preset = list(KIND_CANDIDATES.get(category, ()))
    try:
        conn = table_db.get_conn()
        rows = conn.execute(
            "SELECT DISTINCT kind FROM ledger_records WHERE category = ?",
            (category,)).fetchall()
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
    """
    conn = table_db.get_conn()
    rows = conn.execute(
        "SELECT signer, category, COUNT(*) FROM ledger_records "
        "GROUP BY signer, category").fetchall()
    stat: dict = {}
    for signer, category, count in rows:
        signer = str(signer or "").strip() or "未署名"
        bucket = stat.setdefault(signer, {"signer": signer, "total": 0})
        bucket[category] = int(count or 0)
        bucket["total"] += int(count or 0)
    out = []
    for bucket in stat.values():
        for c in CATEGORIES:
            bucket.setdefault(c, 0)
        out.append(bucket)
    out.sort(key=lambda b: (-b["total"], b["signer"]))
    return out


# ==================== 导出（线下汇报用，与在线模板同结构） ====================

def export_xlsx(path: str, category: str = "") -> int:
    """按分类分 sheet 导出为 xlsx（结构与在线模板一致），返回导出行数

    仅作线下汇报/交接用：系统数据源是数据库（MySQL 开启时即服务器侧），
    xlsx 是派生副本而非存储。category 非空时只导出该分类。
    """
    from openpyxl import Workbook

    conn = table_db.get_conn()
    wb = Workbook()
    wb.remove(wb.active)
    targets = [category] if category else list(CATEGORIES)
    exported = 0
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
