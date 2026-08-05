# -*- coding: utf-8 -*-
"""球桌数据本地 SQLite 存储层（独立模块）

职责：
- save_all(rows): 全量替换球桌数据并记录刷新时间
- query_page(page_no, page_size, keyword): 本地分页 + 全字段模糊搜索
- get_meta(): 返回 (总条数, 最后刷新时间字符串)
- save_xqzg / query_xqzg_page: 接口1 (xqzg.newbv.cn) 数据存取
- save_kd / query_kd_page: 接口2 (kd.newbv.cn) 数据存取
- upsert_kd: 接口2 keyword 搜索拉取结果的增量更新（不覆盖全量）
"""

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta

# 数据库文件路径：database/tables.db（随项目目录）
_DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database")
DB_PATH = os.path.join(_DB_DIR, "tables.db")

# ==================== 原有球桌表（wechat2-billiard 接口） ====================

# 存储的字段（与面板展示列一致）
FIELDS = ("name", "roomName", "onlineStatusName", "remark", "cameraPassExt", "snk_code")

# remark 中 snk 标识的提取规则（如 "... snk_001 ..." → "snk_001"），
# 用于 frp xtcp 远程连接时定位设备（visitor serverName）
_SNK_PATTERN = re.compile(r"snk[\w\-]*", re.IGNORECASE)


def parse_snk_code(remark: str) -> str:
    """从球桌 remark 中提取 snk 标识（如 snk_001），无则返回空串"""
    m = _SNK_PATTERN.search(str(remark or ""))
    return m.group(0) if m else ""


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS billiard_tables (
    id      INTEGER PRIMARY KEY,
    name    TEXT DEFAULT '',
    roomName TEXT DEFAULT '',
    onlineStatusName TEXT DEFAULT '',
    remark  TEXT DEFAULT '',
    cameraPassExt TEXT DEFAULT '',
    snk_code TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sync_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# ==================== 新增：接口1 / 接口2 运维数据表 ====================

# 两张表共用 13 个字段
STATUS_FIELDS = (
    "table_id", "club_name", "pic_total", "normal_count",
    "normal_total", "except_count", "operation_rate",
    "untreated_count", "operation_count", "accuracy_count",
    "already_count", "rubbish_count", "error_rate",
)

# kd_status 额外字段（文件列表 + 路径信息 + 设备状态）
KD_EXTRA_FIELDS = (
    "device_code", "target_directory", "status",
    "normal_files", "except_files", "untreated_files",
    "operation_files", "accuracy_files", "already_files",
    "rubbish_files", "version_files",
)
# 文件列表类字段（存储时需 JSON 序列化）
KD_FILE_FIELDS = (
    "normal_files", "except_files", "untreated_files",
    "operation_files", "accuracy_files", "already_files",
    "rubbish_files", "version_files",
)

# kd_status 历史数据保留天数（按日期分区快照，过期自动清理，防止体积无限膨胀）
_KD_KEEP_DAYS = 60

# 数值型字段（以 TEXT 存储，排序时需 CAST 成数值，否则按字典序 "100" < "20"）
_NUMERIC_SORT_FIELDS = frozenset({
    "status", "pic_total", "normal_count", "normal_total", "except_count",
    "untreated_count", "operation_count", "accuracy_count", "already_count",
    "rubbish_count", "operation_rate", "error_rate",
})

# 两张运维数据表允许排序的列白名单（防 SQL 注入，列名只能来自此集合）
_SORTABLE_COLUMNS = frozenset(STATUS_FIELDS) | {"id", "table_id", "club_name", "status"}

# ==================== FTS5 全文搜索 ====================
# trigram 分词器支持任意位置子串匹配（等价 LIKE '%kw%'），但要求关键词
# ≥ 3 个字符；更短的关键词回退多列 LIKE。external content 表 + 触发器
# 增量同步，覆盖全部写入路径（全量替换/日期分区/清理/单行更新）。
_FTS_MIN_LEN = 3

# 主表 → (FTS 表名, 参与搜索的列) 映射
_FTS_MAP = {
    "billiard_tables": ("tables_fts", FIELDS),
    "xqzg_status": ("xqzg_fts", STATUS_FIELDS),
    "kd_status": ("kd_fts", STATUS_FIELDS + ("device_code",)),
}

# 运行时可用标志：SQLite 缺 FTS5/trigram 支持时置 False，查询回退 LIKE
_fts_available = False


def _setup_fts(conn: sqlite3.Connection):
    """创建 FTS5 索引表与同步触发器；存量库首次初始化执行一次 rebuild

    触发器自动维护增量（AFTER INSERT/DELETE/UPDATE），旧库通过
    sync_meta.fts_built 标记只 rebuild 一次，避免每次启动重建。
    任何异常（SQLite 编译选项缺 FTS5 等）置 _fts_available=False 静默回退 LIKE。
    """
    global _fts_available
    if sqlite3.sqlite_version_info < (3, 34):  # trigram 分词器最低版本
        return
    try:
        for table, (fts, fields) in _FTS_MAP.items():
            cols = ", ".join(fields)
            new_vals = ", ".join(f"new.{f}" for f in fields)
            old_vals = ", ".join(f"old.{f}" for f in fields)
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {fts} USING fts5({cols}, "
                f"content='{table}', content_rowid='id', tokenize='trigram')")
            # INSERT OR REPLACE 冲突时先触发 DELETE 再触发 INSERT，两触发器配合保证一致
            conn.executescript(f"""
            CREATE TRIGGER IF NOT EXISTS {fts}_ai AFTER INSERT ON {table} BEGIN
                INSERT INTO {fts}(rowid, {cols}) VALUES (new.id, {new_vals});
            END;
            CREATE TRIGGER IF NOT EXISTS {fts}_ad AFTER DELETE ON {table} BEGIN
                INSERT INTO {fts}({fts}, rowid, {cols}) VALUES ('delete', old.id, {old_vals});
            END;
            CREATE TRIGGER IF NOT EXISTS {fts}_au AFTER UPDATE ON {table} BEGIN
                INSERT INTO {fts}({fts}, rowid, {cols}) VALUES ('delete', old.id, {old_vals});
                INSERT INTO {fts}(rowid, {cols}) VALUES (new.id, {new_vals});
            END;
            """)
        built = conn.execute(
            "SELECT value FROM sync_meta WHERE key='fts_built'").fetchone()
        if not built:
            for _, (fts, _) in _FTS_MAP.items():
                conn.execute(f"INSERT INTO {fts}({fts}) VALUES ('rebuild')")
            conn.execute(
                "INSERT OR REPLACE INTO sync_meta (key, value) VALUES ('fts_built', '1')")
            conn.commit()
        _fts_available = True
    except sqlite3.Error:
        _fts_available = False


def _fts_cond(fts_table: str, kw: str):
    """构造 FTS MATCH 子查询条件；不可用或短关键词返回 None（调用方回退 LIKE）

    关键词用双引号包裹为 FTS 字面量（防特殊字符被当语法解析），
    内部双引号转义为两个双引号。
    """
    if not _fts_available or len(kw) < _FTS_MIN_LEN:
        return None
    escaped = '"' + kw.replace('"', '""') + '"'
    return (f"id IN (SELECT rowid FROM {fts_table} WHERE {fts_table} MATCH ?)",
            [escaped])


def _build_order_clause(order_by: str, desc: bool) -> str:
    """构造 ORDER BY 子句（含白名单校验与数值列 CAST）

    数值字段以 TEXT 存储，直接排序会按字典序（"9" > "100"），
    需 CAST(col AS REAL)；非数字内容 CAST 后为 0.0，自然排在数值最小端。
    非法/未知列名回退为默认 id 排序，杜绝 SQL 注入。
    """
    direction = "DESC" if desc else "ASC"
    if not order_by or order_by not in _SORTABLE_COLUMNS:
        return f" ORDER BY id {direction}"
    if order_by in _NUMERIC_SORT_FIELDS:
        return f" ORDER BY CAST({order_by} AS REAL) {direction}"
    return f" ORDER BY {order_by} {direction}"

_CREATE_STATUS_SQL = """
CREATE TABLE IF NOT EXISTS xqzg_status (
    id              INTEGER PRIMARY KEY,
    table_id        TEXT DEFAULT '',
    club_name       TEXT DEFAULT '',
    pic_total       TEXT DEFAULT '',
    normal_count    TEXT DEFAULT '',
    normal_total    TEXT DEFAULT '',
    except_count    TEXT DEFAULT '',
    operation_rate  TEXT DEFAULT '',
    untreated_count TEXT DEFAULT '',
    operation_count TEXT DEFAULT '',
    accuracy_count  TEXT DEFAULT '',
    already_count   TEXT DEFAULT '',
    rubbish_count   TEXT DEFAULT '',
    error_rate      TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS kd_status (
    id              INTEGER PRIMARY KEY,
    file_path       TEXT DEFAULT '',
    table_id        TEXT DEFAULT '',
    club_name       TEXT DEFAULT '',
    pic_total       TEXT DEFAULT '',
    normal_count    TEXT DEFAULT '',
    normal_total    TEXT DEFAULT '',
    except_count    TEXT DEFAULT '',
    operation_rate  TEXT DEFAULT '',
    untreated_count TEXT DEFAULT '',
    operation_count TEXT DEFAULT '',
    accuracy_count  TEXT DEFAULT '',
    already_count   TEXT DEFAULT '',
    rubbish_count   TEXT DEFAULT '',
    error_rate      TEXT DEFAULT '',
    device_code     TEXT DEFAULT '',
    target_directory TEXT DEFAULT '',
    status          TEXT DEFAULT '',
    normal_files    TEXT DEFAULT '[]',
    except_files    TEXT DEFAULT '[]',
    untreated_files TEXT DEFAULT '[]',
    operation_files TEXT DEFAULT '[]',
    accuracy_files  TEXT DEFAULT '[]',
    already_files   TEXT DEFAULT '[]',
    rubbish_files   TEXT DEFAULT '[]',
    version_files   TEXT DEFAULT '[]'
);
-- 日期分区 + 默认 id 排序的覆盖索引：file_path 筛选的分页查询（列表页主路径）
-- 无需回表扫描全表；CREATE INDEX IF NOT EXISTS 幂等，旧库首次初始化自动补建
CREATE INDEX IF NOT EXISTS idx_kd_status_path_id ON kd_status(file_path, id);
"""

# 列表页轻量字段（不含 8 类文件 JSON）：分页列表只展示状态/计数等，
# 文件清单仅在点开某一行时按 id 懒加载（get_kd_row_full），避免每页
# 反序列化大量 JSON 带来的 CPU/内存开销
_KD_LIGHT_FIELDS = ("file_path",) + STATUS_FIELDS + ("device_code", "target_directory", "status")


# 模块级初始化标志：建表脚本与迁移检查只在首次连接时执行一次，
# 避免高频 query_page（搜索防抖逐字触发）重复执行 DDL/PRAGMA 带来的开销
_initialized = False


def _ensure_initialized(conn: sqlite3.Connection):
    """首次连接时执行建表与迁移检查（整个进程生命周期只跑一次）"""
    global _initialized
    if _initialized:
        return
    conn.executescript(_CREATE_SQL)
    conn.executescript(_CREATE_STATUS_SQL)
    # 迁移修复：若 billiard_tables 被误改为新字段（缺少 name 列），DROP 重建
    cols = [r[1] for r in conn.execute("PRAGMA table_info(billiard_tables)").fetchall()]
    if cols and "name" not in cols:
        conn.execute("DROP TABLE billiard_tables")
        conn.executescript(_CREATE_SQL)
        cols = []
    # 迁移：旧表无 snk_code 列时自动补列，并从已有 remark 回填 snk 标识
    if cols and "snk_code" not in cols:
        conn.execute("ALTER TABLE billiard_tables ADD COLUMN snk_code TEXT DEFAULT ''")
        for rid, remark in conn.execute("SELECT id, remark FROM billiard_tables"):
            snk = parse_snk_code(remark)
            if snk:
                conn.execute(
                    "UPDATE billiard_tables SET snk_code = ? WHERE id = ?", (snk, rid))
        conn.commit()
    # 迁移：kd_status 旧表可能缺少扩展字段，自动 ALTER ADD
    kd_cols = [r[1] for r in conn.execute("PRAGMA table_info(kd_status)").fetchall()]
    if kd_cols and "device_code" not in kd_cols:
        for f in KD_EXTRA_FIELDS:
            default = "'[]'" if f in KD_FILE_FIELDS else "''"
            conn.execute(f"ALTER TABLE kd_status ADD COLUMN {f} TEXT DEFAULT {default}")
        conn.commit()
    if kd_cols and "file_path" not in kd_cols:
        conn.execute("ALTER TABLE kd_status ADD COLUMN file_path TEXT DEFAULT ''")
        conn.commit()
    if kd_cols and "status" not in kd_cols:
        conn.execute("ALTER TABLE kd_status ADD COLUMN status TEXT DEFAULT ''")
        conn.commit()
    # FTS5 全文索引（放在迁移补列之后，确保 kd 全部列就绪）
    _setup_fts(conn)
    _initialized = True


_conn: sqlite3.Connection | None = None
_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    """获取模块级单连接（惰性创建，WAL 模式）"""
    global _conn
    os.makedirs(_DB_DIR, exist_ok=True)
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA busy_timeout=3000")
        _ensure_initialized(_conn)
    return _conn


def close():
    """关闭数据库连接（应用退出时调用）"""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


# ==================== 原有球桌表操作 ====================

def save_all(rows: list) -> int:
    """全量替换数据，返回写入条数（自动从 remark 解析 snk_code 单独存列）

    手动写入的 snk 保护：remark 解析不出 snk 但旧库同球桌号已有非空
    snk（如手动写入）时保留旧值，避免同步把手动值冲掉。
    """
    conn = _get_conn()
    # 先记录存量 snk（TRIM(name) → snk_code），DELETE 后仍可回查
    old_snk = {}
    for name, snk in conn.execute(
            "SELECT name, snk_code FROM billiard_tables"):
        key = str(name or "").strip()
        val = str(snk or "").strip()
        if key and val:
            old_snk[key] = val
    conn.execute("DELETE FROM billiard_tables")
    data = []
    for item in rows:
        remark = str(item.get("remark") or "")
        name = str(item.get("name") or "")
        snk = parse_snk_code(remark) or old_snk.get(name.strip(), "")
        data.append((
            item.get("id") or 0,
            name,
            str(item.get("roomName") or ""),
            str(item.get("onlineStatusName") or ""),
            remark,
            str(item.get("cameraPassExt") or ""),
            snk,
        ))
    conn.executemany(
        "INSERT OR REPLACE INTO billiard_tables "
        "(id, name, roomName, onlineStatusName, remark, cameraPassExt, snk_code) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)", data)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT OR REPLACE INTO sync_meta (key, value) VALUES ('last_sync', ?)", (now,))
    conn.commit()
    return len(data)


def query_page(page_no: int, page_size: int, keyword: str = "") -> tuple:
    """本地分页查询，支持全字段模糊搜索

    Returns:
        (total, rows)  rows 为 list[dict]
    """
    conn = _get_conn()
    where = ""
    params = []
    kw = keyword.strip()
    if kw:
        # 优先 FTS5 trigram 索引（子串匹配），短关键词/不可用时回退多列 LIKE
        fts = _fts_cond("tables_fts", kw)
        if fts:
            where = f" WHERE {fts[0]}"
            params = fts[1]
        else:
            like = f"%{kw}%"
            conds = " OR ".join([f"{f} LIKE ?" for f in FIELDS])
            where = f" WHERE {conds}"
            params = [like] * len(FIELDS)

    total = conn.execute(
        f"SELECT COUNT(*) FROM billiard_tables{where}", params).fetchone()[0]

    offset = (page_no - 1) * page_size
    cursor = conn.execute(
        f"SELECT id, name, roomName, onlineStatusName, remark, cameraPassExt, snk_code "
        f"FROM billiard_tables{where} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [page_size, offset])
    cols = [d[0] for d in cursor.description]
    rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
    return total, rows


def insert_one(record: dict) -> int:
    """手动插入单条记录（API 失效时的兜底入口），返回新记录 id

    record 中显式传入的 snk_code（手动写入）优先；未提供时回退
    从 remark 自动解析。
    """
    conn = _get_conn()
    remark = str(record.get("remark") or "")
    snk = str(record.get("snk_code") or "").strip() or parse_snk_code(remark)
    cur = conn.execute(
        "INSERT INTO billiard_tables "
        "(name, roomName, onlineStatusName, remark, cameraPassExt, snk_code) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            str(record.get("name") or ""),
            str(record.get("roomName") or ""),
            str(record.get("onlineStatusName") or ""),
            remark,
            str(record.get("cameraPassExt") or ""),
            snk,
        ))
    conn.commit()
    return cur.lastrowid


def update_snk_by_name(name: str, snk_code: str) -> int:
    """按球桌号手动写入/修改 snk 标识（TRIM 匹配），返回受影响行数

    snk_code 传空串表示清空。与 save_all 的保护逻辑配合：remark 无 snk
    时手动值在后续同步中会被保留。
    """
    name = str(name or "").strip()
    if not name:
        return 0
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE billiard_tables SET snk_code = ? WHERE TRIM(name) = ?",
        (str(snk_code or "").strip(), name))
    conn.commit()
    return cur.rowcount


def get_snk_by_name(name: str) -> str:
    """按球桌号查 snk 标识（设备状态页 table_id ↔ 球桌管理 name 关联）

    未匹配或该球桌 remark 无 snk 时返回空串。
    """
    name = str(name or "").strip()
    if not name:
        return ""
    conn = _get_conn()
    row = conn.execute(
        "SELECT snk_code FROM billiard_tables WHERE TRIM(name) = ? LIMIT 1",
        (name,)).fetchone()
    return str(row[0] or "") if row else ""


def get_meta() -> tuple:
    """返回 (总条数, 最后同步时间字符串)，无数据时返回 (0, '')"""
    conn = _get_conn()
    total = conn.execute(
        "SELECT COUNT(*) FROM billiard_tables").fetchone()[0]
    row = conn.execute(
        "SELECT value FROM sync_meta WHERE key='last_sync'").fetchone()
    return total, (row[0] if row else "")


# ==================== 接口1 xqzg_status 表操作 ====================

def save_xqzg(rows: list) -> int:
    """全量替换接口1数据，返回写入条数"""
    return _save_status_table("xqzg_status", rows, "last_sync_xqzg")


def query_xqzg_page(page_no: int, page_size: int, keyword: str = "",
                    order_by: str = "", desc: bool = False) -> tuple:
    """接口1数据分页查询

    Args:
        order_by: 排序字段名（白名单校验）；为空按 id 排序
        desc: 是否降序
    """
    return _query_status_page("xqzg_status", page_no, page_size, keyword,
                              order_by, desc)


# ==================== 接口2 kd_status 表操作 ====================

def save_kd(rows: list, file_path: str = "") -> int:
    """按日期替换接口2数据（含扩展字段），返回写入条数

    Args:
        rows: API 返回的记录列表
        file_path: 日期路径，如 "2026/08/02"；仅替换该日期的数据
    """
    conn = _get_conn()
    # 只删除该日期的数据，保留其他日期
    conn.execute("DELETE FROM kd_status WHERE file_path = ?", (file_path,))
    all_fields = STATUS_FIELDS + KD_EXTRA_FIELDS
    placeholders = ", ".join(["?"] * (len(all_fields) + 2))  # id + file_path + fields
    col_names = "id, file_path, " + ", ".join(all_fields)
    # 获取当前最大 id，续接编号
    max_id = conn.execute("SELECT MAX(id) FROM kd_status").fetchone()[0] or 0
    data = []
    for idx, item in enumerate(rows, max_id + 1):
        row_vals = [idx, file_path]
        for f in STATUS_FIELDS:
            row_vals.append(str(item.get(f) if item.get(f) is not None else ""))
        for f in KD_EXTRA_FIELDS:
            val = item.get(f)
            if f in KD_FILE_FIELDS:
                row_vals.append(json.dumps(val if isinstance(val, list) else [], ensure_ascii=False))
            else:
                row_vals.append(str(val if val is not None else ""))
        data.append(tuple(row_vals))
    conn.executemany(
        f"INSERT OR REPLACE INTO kd_status ({col_names}) VALUES ({placeholders})", data)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta_key = f"last_sync_kd_{file_path.replace('/', '')}"
    conn.execute(
        "INSERT OR REPLACE INTO sync_meta (key, value) VALUES (?, ?)", (meta_key, now))
    conn.commit()
    inserted = len(data)
    # 保存后顺手清理 60 天前的历史分区，避免数据库随天数无限膨胀
    prune_kd_history(_KD_KEEP_DAYS)
    return inserted


def upsert_kd(rows: list, file_path: str = "") -> int:
    """按 (file_path, device_code) 增量更新/插入（keyword 搜索拉取专用），返回处理条数

    save_kd 是按日期全量替换，若直接保存带 keyword 拉取的部分数据，
    会删除同日期下未匹配的其他设备，造成数据丢失。
    本函数只处理返回范围内的记录：已存在则更新，不存在则插入，
    不动其他记录，保证本地全量数据完整。
    无 device_code 的记录无法定位唯一性，跳过不写入。
    """
    conn = _get_conn()
    all_fields = STATUS_FIELDS + KD_EXTRA_FIELDS
    set_clause = ", ".join(f"{f} = ?" for f in all_fields)
    max_id = conn.execute("SELECT MAX(id) FROM kd_status").fetchone()[0] or 0
    next_id = max_id + 1
    updated = 0
    inserts = []
    for item in rows:
        device_code = str(item.get("device_code") or "").strip()
        if not device_code:
            continue
        vals = []
        for f in STATUS_FIELDS:
            vals.append(str(item.get(f) if item.get(f) is not None else ""))
        for f in KD_EXTRA_FIELDS:
            val = item.get(f)
            if f in KD_FILE_FIELDS:
                vals.append(json.dumps(val if isinstance(val, list) else [], ensure_ascii=False))
            else:
                vals.append(str(val if val is not None else ""))
        cur = conn.execute(
            f"UPDATE kd_status SET {set_clause} "
            f"WHERE file_path = ? AND device_code = ?",
            vals + [file_path, device_code])
        if cur.rowcount:
            updated += cur.rowcount
            continue
        inserts.append(tuple([next_id, file_path] + vals))
        next_id += 1
    if inserts:
        placeholders = ", ".join(["?"] * (len(all_fields) + 2))
        col_names = "id, file_path, " + ", ".join(all_fields)
        conn.executemany(
            f"INSERT OR REPLACE INTO kd_status ({col_names}) VALUES ({placeholders})",
            inserts)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta_key = f"last_sync_kd_{file_path.replace('/', '')}"
    conn.execute(
        "INSERT OR REPLACE INTO sync_meta (key, value) VALUES (?, ?)", (meta_key, now))
    conn.commit()
    return updated + len(inserts)


def prune_kd_history(keep_days: int = 60) -> int:
    """清理 kd_status 中超过保留天数的旧日期分区数据，返回删除条数

    kd 数据按日期分区（file_path 如 "2026/08/02"）每日快照，体积随天数线性增长。
    设备状态属“快照”性质，过期数据业务价值低，定期清理可将数据库体积封顶在
    keep_days 天内，避免无限膨胀。

    Args:
        keep_days: 保留最近多少天的数据（含今天），默认 60 天
    """
    conn = _get_conn()
    cutoff = (datetime.now() - timedelta(days=keep_days - 1)).strftime("%Y/%m/%d")
    cur = conn.execute(
        "DELETE FROM kd_status WHERE file_path != '' AND file_path < ?", (cutoff,))
    deleted = cur.rowcount
    if deleted:
        conn.commit()
    return deleted


def query_kd_page(page_no: int, page_size: int, keyword: str = "", file_path: str = "",
                  order_by: str = "", desc: bool = False, include_files: bool = False) -> tuple:
    """接口2数据分页查询

    Args:
        file_path: 日期路径筛选，如 "2026/08/02"；为空则查全部日期
        order_by: 排序字段名（白名单校验）；为空按 id 排序
        desc: 是否降序
        include_files: 是否携带 8 类文件清单 JSON（列表页用默认轻量模式，
            详情按需走 get_kd_row_full 按 id 懒加载）
    """
    conn = _get_conn()
    all_fields = (("file_path",) + STATUS_FIELDS + KD_EXTRA_FIELDS if include_files
                  else _KD_LIGHT_FIELDS)
    conds = []
    params = []
    # 日期筛选
    if file_path:
        conds.append("file_path = ?")
        params.append(file_path)
    # 关键词搜索（优先 FTS5，短关键词/不可用回退多列 LIKE）
    kw = keyword.strip()
    if kw:
        fts = _fts_cond("kd_fts", kw)
        if fts:
            conds.append(fts[0])
            params.extend(fts[1])
        else:
            like = f"%{kw}%"
            search_fields = STATUS_FIELDS + ("device_code",)
            kw_cond = " OR ".join([f"{f} LIKE ?" for f in search_fields])
            conds.append(f"({kw_cond})")
            params.extend([like] * len(search_fields))

    where = (" WHERE " + " AND ".join(conds)) if conds else ""

    total = conn.execute(
        f"SELECT COUNT(*) FROM kd_status{where}", params).fetchone()[0]

    offset = (page_no - 1) * page_size
    select_cols = "id, " + ", ".join(all_fields)
    order_sql = _build_order_clause(order_by, desc)
    cursor = conn.execute(
        f"SELECT {select_cols} "
        f"FROM kd_status{where}{order_sql} LIMIT ? OFFSET ?",
        params + [page_size, offset])
    cols = [d[0] for d in cursor.description]
    rows = []
    for r in cursor.fetchall():
        row_dict = dict(zip(cols, r))
        if include_files:
            # 反序列化文件列表字段（仅详情查询需要）
            for f in KD_FILE_FIELDS:
                try:
                    row_dict[f] = json.loads(row_dict.get(f) or "[]")
                except (json.JSONDecodeError, TypeError):
                    row_dict[f] = []
        rows.append(row_dict)
    return total, rows


def query_kd_by_device(device_code: str, file_path: str = "") -> dict:
    """按 device_code 精确查询单台设备完整信息（含文件清单）

    供 FileListPanel 刷新使用，避免全量分页查询后 Python 侧过滤。
    返回空 dict 表示未找到匹配记录。
    """
    conn = _get_conn()
    conds = ["device_code = ?"]
    params = [device_code]
    if file_path:
        conds.append("file_path = ?")
        params.append(file_path)
    where = " WHERE " + " AND ".join(conds)
    all_fields = ("file_path",) + STATUS_FIELDS + KD_EXTRA_FIELDS
    select_cols = "id, " + ", ".join(all_fields)
    cur = conn.execute(
        f"SELECT {select_cols} FROM kd_status{where} LIMIT 1", params)
    r = cur.fetchone()
    if r is None:
        return {}
    row_dict = dict(zip(["id"] + list(all_fields), r))
    for f in KD_FILE_FIELDS:
        try:
            row_dict[f] = json.loads(row_dict.get(f) or "[]")
        except (json.JSONDecodeError, TypeError):
            row_dict[f] = []
    return row_dict


def get_kd_row_full(row_id: int) -> dict:
    """按 id 查询 kd_status 完整行（含 8 类文件清单反序列化）

    配合 query_kd_page 的轻量模式：列表页不读文件 JSON，点开某行详情时
    才按 id 单点查询；记录不存在时返回空 dict。
    """
    conn = _get_conn()
    cols = ("id", "file_path") + STATUS_FIELDS + KD_EXTRA_FIELDS
    cur = conn.execute(
        f"SELECT {', '.join(cols)} FROM kd_status WHERE id = ?", (row_id,))
    r = cur.fetchone()
    if r is None:
        return {}
    row_dict = dict(zip(cols, r))
    for f in KD_FILE_FIELDS:
        try:
            row_dict[f] = json.loads(row_dict.get(f) or "[]")
        except (json.JSONDecodeError, TypeError):
            row_dict[f] = []
    return row_dict


def get_kd_dates() -> list:
    """获取 kd_status 中已存储的所有日期列表（降序）"""
    conn = _get_conn()
    cursor = conn.execute(
        "SELECT DISTINCT file_path FROM kd_status WHERE file_path != '' ORDER BY file_path DESC")
    return [r[0] for r in cursor.fetchall()]


# ==================== 通用内部函数 ====================

def _save_status_table(table_name: str, rows: list, meta_key: str) -> int:
    """全量替换指定运维数据表"""
    conn = _get_conn()
    conn.execute(f"DELETE FROM {table_name}")
    placeholders = ", ".join(["?"] * (len(STATUS_FIELDS) + 1))
    col_names = "id, " + ", ".join(STATUS_FIELDS)
    data = []
    for idx, item in enumerate(rows, 1):
        data.append(tuple(
            [idx] + [str(item.get(f) if item.get(f) is not None else "") for f in STATUS_FIELDS]
        ))
    conn.executemany(
        f"INSERT OR REPLACE INTO {table_name} ({col_names}) VALUES ({placeholders})", data)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT OR REPLACE INTO sync_meta (key, value) VALUES (?, ?)", (meta_key, now))
    conn.commit()
    return len(data)


def _query_status_page(table_name: str, page_no: int, page_size: int, keyword: str = "",
                       order_by: str = "", desc: bool = False) -> tuple:
    """运维数据表通用分页查询（order_by/desc 见 _build_order_clause）"""
    conn = _get_conn()
    where = ""
    params = []
    kw = keyword.strip()
    if kw:
        # 优先 FTS5 trigram 索引，短关键词/不可用回退多列 LIKE
        fts_table = _FTS_MAP.get(table_name, (None,))[0]
        fts = _fts_cond(fts_table, kw) if fts_table else None
        if fts:
            where = f" WHERE {fts[0]}"
            params = fts[1]
        else:
            like = f"%{kw}%"
            conds = " OR ".join([f"{f} LIKE ?" for f in STATUS_FIELDS])
            where = f" WHERE {conds}"
            params = [like] * len(STATUS_FIELDS)

    total = conn.execute(
        f"SELECT COUNT(*) FROM {table_name}{where}", params).fetchone()[0]

    offset = (page_no - 1) * page_size
    select_cols = "id, " + ", ".join(STATUS_FIELDS)
    order_sql = _build_order_clause(order_by, desc)
    cursor = conn.execute(
        f"SELECT {select_cols} "
        f"FROM {table_name}{where}{order_sql} LIMIT ? OFFSET ?",
        params + [page_size, offset])
    cols = [d[0] for d in cursor.description]
    rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
    return total, rows
