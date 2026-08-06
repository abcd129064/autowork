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

# ==================== 精度/问题提交本地台账（C1） ====================

_CREATE_SUBMISSION_SQL = """
CREATE TABLE IF NOT EXISTS submission_log (
    id             INTEGER PRIMARY KEY,
    created_at     TEXT DEFAULT '',
    device_code    TEXT DEFAULT '',
    table_id       TEXT DEFAULT '',
    club_name      TEXT DEFAULT '',
    category       TEXT DEFAULT '',
    file_name      TEXT DEFAULT '',
    file_path_date TEXT DEFAULT '',
    collect_ok     INTEGER DEFAULT 0,
    upload_zip     TEXT,
    upload_ok      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_submission_device_time
    ON submission_log(device_code, created_at);
"""

# ==================== 设备映射表（C4：设备码 → 本地 videos 目录） ====================

_CREATE_MAPPING_SQL = """
CREATE TABLE IF NOT EXISTS device_mapping (
    device_code TEXT PRIMARY KEY,
    local_dir   TEXT DEFAULT '',
    source      TEXT DEFAULT 'auto',
    created_at  TEXT DEFAULT '',
    updated_at  TEXT DEFAULT ''
);
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
    conn.executescript(_CREATE_SUBMISSION_SQL)
    conn.executescript(_CREATE_MAPPING_SQL)
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


def get_tables_online_map() -> dict:
    """批量取全部球桌 name → onlineStatusName 映射（C4 在线状态交叉校验用）

    键为 TRIM 后的球桌号，与 kd_status.table_id 同款关联方式（参见
    get_snk_by_name）；一次全量查询，调用方按页内行匹配，无逐行 N+1。
    """
    conn = _get_conn()
    result = {}
    for name, status in conn.execute(
            "SELECT name, onlineStatusName FROM billiard_tables"):
        key = str(name or "").strip()
        if key:
            result[key] = str(status or "").strip()
    return result


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


# 文件字段 → 中文分类名（C6 日志↔kd 双向跳转反查展示用，
# 与 management_panel.FILE_FIELD_CATEGORIES 保持一致）
_KD_FILE_CATEGORY_CN = {
    "normal_files": "正常",
    "except_files": "操作",
    "untreated_files": "待处理",
    "operation_files": "使用",
    "accuracy_files": "精度",
    "already_files": "问题",
    "rubbish_files": "废弃",
    "version_files": "版本",
}


def _clip_base(fname: str) -> str:
    """截取文件名 'kd' 之前的部分作为基础名

    与 collect_worker.clip_base_name 同规则，独立实现避免数据层反向
    依赖 workers 包。
    """
    fname = str(fname or "").strip()
    idx = fname.find("kd")
    if idx > 0:
        return fname[:idx]
    return os.path.splitext(fname)[0]


def find_kd_file_status(device_code: str, date: str, clip_base: str) -> dict:
    """按 设备码 + 日期分区 反查文件基础名在 kd_status 中的所属分类（C6）

    kd 照片与本地日志共享时间戳前缀（如 20260724_225031），利用该同源
    关系由日志文件名反查 kd 记录状态。单分区单设备 + 8 类文件清单
    JSON 字段 LIKE 预筛（命中分区索引，毫秒级），再在 Python 侧按
    基础名精确比对，避免子串误匹配。

    Args:
        device_code: 设备码（kd_status.device_code 精确匹配）
        date: 日期，兼容 "2026-07-24" / "2026/07/24" / "20260724"
        clip_base: 文件基础名（时间戳前缀，如 "20260724_225031"）

    Returns:
        {"category": 字段key, "category_cn": 中文分类, "file_name": 命中的
        清单文件名, "kd_id": 记录 id}；未找到返回空 dict。
    """
    code = str(device_code or "").strip()
    base = str(clip_base or "").strip()
    if not code or not base:
        return {}
    # 日期归一化为 file_path 格式 yyyy/MM/dd
    d = str(date or "").strip().replace("/", "-").replace(".", "-")
    if len(d) == 8 and d.isdigit():
        d = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    parts = d.split("-")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return {}
    file_path = "/".join(parts)

    conn = _get_conn()
    like = f"%{base}%"
    like_conds = " OR ".join(f"{f} LIKE ?" for f in KD_FILE_FIELDS)
    cur = conn.execute(
        "SELECT id, " + ", ".join(KD_FILE_FIELDS) +
        " FROM kd_status WHERE device_code = ? AND file_path = ?"
        f" AND ({like_conds}) LIMIT 1",
        [code, file_path] + [like] * len(KD_FILE_FIELDS))
    row = cur.fetchone()
    if row is None:
        return {}
    kd_id = row[0]
    for idx, field in enumerate(KD_FILE_FIELDS):
        try:
            names = json.loads(row[1 + idx] or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        for fname in names:
            fname = str(fname or "")
            # 基础名精确相等，或清单文件名以基础名开头（容忍后缀差异）
            if _clip_base(fname) == base or fname.startswith(base):
                return {"category": field,
                        "category_cn": _KD_FILE_CATEGORY_CN.get(field, field),
                        "file_name": fname, "kd_id": kd_id}
    return {}


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


def get_kd_synced_dates() -> list:
    """从 sync_meta 提取曾同步过的 kd 日期（含接口返回空数据的日期）

    save_kd/upsert_kd 落库时都会写 last_sync_kd_YYYYMMDD 元数据，即使该日
    无设备数据；历史补漏（C2）用它区分「从未拉取」与「拉过但为空」，
    避免对接口确实无数据的日期反复重试。
    """
    conn = _get_conn()
    rows = conn.execute(
        "SELECT key FROM sync_meta WHERE key LIKE 'last_sync_kd_%'").fetchall()
    dates = []
    for (key,) in rows:
        s = key.replace("last_sync_kd_", "")
        if len(s) == 8 and s.isdigit():
            dates.append(f"{s[:4]}/{s[4:6]}/{s[6:]}")
    return dates


def get_latest_kd_status(table_id: str) -> dict:
    """查指定球桌最近一次上报的设备状态（轻量单条 SQL，远程前置检查用）

    按 file_path 倒序取该球桌最新分区记录（而非全局最新分区——该设备可能
    不在当天分区中）。status: 0=下线 1=空闲 2=使用。

    Returns:
        {"status": "0/1/2", "file_path": "yyyy/MM/dd"}；未找到返回空 dict
    """
    tid = str(table_id or "").strip()
    if not tid:
        return {}
    conn = _get_conn()
    row = conn.execute(
        "SELECT status, file_path FROM kd_status WHERE TRIM(table_id) = ? "
        "ORDER BY file_path DESC LIMIT 1", (tid,)).fetchone()
    return {"status": str(row[0] or "").strip(), "file_path": row[1]} if row else {}


# ==================== kd 健康度聚合查询（C3 趋势看板） ====================

# 排行榜排序字段白名单（防 SQL 注入；均为数值列，排序统一 CAST AS REAL）
_KD_RANK_FIELDS = frozenset({
    "error_rate", "operation_rate", "accuracy_count",
    "already_count", "except_count", "rubbish_count",
})


def query_kd_trend(device_code: str, days: int = 30) -> list:
    """单设备近 N 天按日期的指标序列（单条 SQL，趋势折线图数据源）

    Returns:
        list[dict]: {"file_path", "error_rate", "operation_rate", "accuracy_count"}
        数值字段已 CAST 为 REAL（"12.5%" → 12.5），按日期升序。
        数据空洞（未拉取日期）自然缺行，前端按断点绘制即可。
    """
    code = str(device_code or "").strip()
    if not code:
        return []
    conn = _get_conn()
    cutoff = (datetime.now() - timedelta(days=max(1, days) - 1)).strftime("%Y/%m/%d")
    cur = conn.execute(
        "SELECT file_path, CAST(error_rate AS REAL) AS error_rate, "
        "CAST(operation_rate AS REAL) AS operation_rate, "
        "CAST(accuracy_count AS REAL) AS accuracy_count "
        "FROM kd_status WHERE device_code = ? AND file_path != '' AND file_path >= ? "
        "ORDER BY file_path ASC", (code, cutoff))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def query_kd_ranking(date: str = "", top: int = 10, by: str = "error_rate") -> dict:
    """指定日期设备指标 TOP N 排行（单条 SQL，白名单校验排序字段）

    Args:
        date: file_path 日期如 "2026/08/06"；空串取最近一个已存分区
            （当天未拉取时自动回退，避免空榜）
        top: 排行条数
        by: 排序字段，限 _KD_RANK_FIELDS，非法值回退 error_rate，降序

    Returns:
        {"date": 实际分区日期, "rows": [{club_name, device_code, table_id,
        status, error_rate, operation_rate, accuracy_count, already_count}]}
        无数据时 rows 为空列表。
    """
    conn = _get_conn()
    fp = str(date or "").strip()
    if not fp:
        row = conn.execute(
            "SELECT MAX(file_path) FROM kd_status WHERE file_path != ''").fetchone()
        fp = row[0] if row and row[0] else ""
    if not fp:
        return {"date": "", "rows": []}
    field = by if by in _KD_RANK_FIELDS else "error_rate"
    cur = conn.execute(
        "SELECT club_name, device_code, table_id, status, "
        "CAST(error_rate AS REAL) AS error_rate, "
        "CAST(operation_rate AS REAL) AS operation_rate, "
        "CAST(accuracy_count AS REAL) AS accuracy_count, "
        "CAST(already_count AS REAL) AS already_count "
        "FROM kd_status WHERE file_path = ? AND device_code != '' "
        f"ORDER BY CAST({field} AS REAL) DESC LIMIT ?",
        (fp, max(1, int(top))))
    cols = [d[0] for d in cur.description]
    return {"date": fp, "rows": [dict(zip(cols, r)) for r in cur.fetchall()]}


def query_kd_alerts(days: int = 7) -> list:
    """突增预警：最新分区 error_rate > 前 N 日均值×2 的设备（单条 CTE SQL）

    「今日」取最新已存分区（当天未拉取时回退最近一天，不会漏报前一天突增）；
    历史均值为该分区之前 N 天窗口（不含当日）。历史均值为 0 或无历史
    记录的设备不报（避免除零噪声与首次出现即误报）。

    Returns:
        list[dict]: {device_code, club_name, table_id, today_rate, avg_rate,
        hist_days, file_path}，按突增幅度降序。
    """
    conn = _get_conn()
    days = max(1, int(days))
    # date() 不认 yyyy/MM/dd，先换连字符运算再换回；days 已 int 化可安全拼接
    cur = conn.execute(f"""
    WITH latest(fp) AS (
        SELECT MAX(file_path) FROM kd_status WHERE file_path != ''
    ),
    cutoff(c) AS (
        SELECT replace(date(replace(fp, '/', '-'), '-{days} days'), '-', '/')
        FROM latest
    ),
    today AS (
        SELECT device_code, MAX(club_name) AS club_name,
               MAX(table_id) AS table_id,
               MAX(CAST(error_rate AS REAL)) AS today_rate
        FROM kd_status
        WHERE file_path = (SELECT fp FROM latest) AND device_code != ''
        GROUP BY device_code
    ),
    hist AS (
        SELECT device_code, AVG(CAST(error_rate AS REAL)) AS avg_rate,
               COUNT(*) AS hist_days
        FROM kd_status
        WHERE file_path >= (SELECT c FROM cutoff)
          AND file_path < (SELECT fp FROM latest)
          AND device_code != ''
        GROUP BY device_code
    )
    SELECT t.device_code, t.club_name, t.table_id, t.today_rate,
           ROUND(h.avg_rate, 2) AS avg_rate, h.hist_days,
           (SELECT fp FROM latest) AS file_path
    FROM today t JOIN hist h ON t.device_code = h.device_code
    WHERE h.avg_rate > 0 AND t.today_rate > h.avg_rate * 2.0
    ORDER BY t.today_rate - h.avg_rate DESC
    """)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ==================== 精度/问题提交台账操作（C1） ====================

def log_submission(device_code: str = "", table_id: str = "", club_name: str = "",
                   category: str = "", file_name: str = "", file_path_date: str = "",
                   collect_ok: bool = False) -> int:
    """写入一条精度/问题提交台账，返回新记录 id

    Args:
        category: '精度' / '问题'
        collect_ok: 收集结果；迁移成功写入时通常未知，先 False 待收集完成后回填
    """
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO submission_log "
        "(created_at, device_code, table_id, club_name, category, file_name, "
        "file_path_date, collect_ok) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         str(device_code or ""), str(table_id or ""), str(club_name or ""),
         str(category or ""), str(file_name or ""), str(file_path_date or ""),
         1 if collect_ok else 0))
    conn.commit()
    return cur.lastrowid


def update_submission_collect(log_id: int, ok: bool) -> int:
    """回填收集结果（collect_ok），返回受影响行数；log_id 无效时返回 0"""
    if not log_id:
        return 0
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE submission_log SET collect_ok = ? WHERE id = ?",
        (1 if ok else 0, log_id))
    conn.commit()
    return cur.rowcount


def update_submission_upload(upload_zip: str, ok: bool, within_hours: int = 24) -> int:
    """回填上传结果：打包上传是整目录 zip（多设备合并），无法定位单条记录，
    故批量更新 within_hours 内「已收集但未上传」的全部记录（upload_ok 为空者）

    无匹配记录时补写一条仅含上传结果的台账（直接打包上传、未经迁移台账的
    场景），保证上传动作留痕。返回更新条数。
    """
    conn = _get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cutoff = (datetime.now() - timedelta(hours=within_hours)).strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        "UPDATE submission_log SET upload_zip = ?, upload_ok = ? "
        "WHERE collect_ok = 1 AND upload_ok IS NULL AND created_at >= ?",
        (str(upload_zip or ""), 1 if ok else 0, cutoff))
    if cur.rowcount == 0:
        conn.execute(
            "INSERT INTO submission_log (created_at, upload_zip, upload_ok) "
            "VALUES (?, ?, ?)",
            (now, str(upload_zip or ""), 1 if ok else 0))
    conn.commit()
    return cur.rowcount


def get_submission_stats(device_code: str = None, days: int = 30) -> dict:
    """近 N 天提交次数聚合（单条 GROUP BY SQL，列表页批量匹配无 N+1）

    Args:
        device_code: 仅统计该设备；None 统计全部设备
        days: 统计窗口天数（常用 7/30）

    Returns:
        {"by_device": {device_code: 次数}, "by_table": {table_id: 次数}}
        两个映射由同一条聚合结果合并而来：设备页按 device_code 匹配，
        球桌页按 table_id（球桌号）匹配。
    """
    conn = _get_conn()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    sql = ("SELECT device_code, table_id, COUNT(*) FROM submission_log "
           "WHERE created_at >= ?")
    params = [cutoff]
    if device_code:
        sql += " AND device_code = ?"
        params.append(str(device_code).strip())
    sql += " GROUP BY device_code, table_id"
    by_device, by_table = {}, {}
    for dev, tid, n in conn.execute(sql, params):
        dev = str(dev or "").strip()
        tid = str(tid or "").strip()
        if dev:
            by_device[dev] = by_device.get(dev, 0) + n
        if tid:
            by_table[tid] = by_table.get(tid, 0) + n
    return {"by_device": by_device, "by_table": by_table}


# ==================== 设备映射表操作（C4） ====================

def get_device_mapping(device_code: str) -> dict:
    """按设备码查映射记录，返回 dict（无记录返回空 dict）

    Returns:
        {"device_code", "local_dir", "source", "created_at", "updated_at"}
        source: 'auto'=模糊匹配自动落库 / 'manual'=人工指定（自愈向导预留）
    """
    code = str(device_code or "").strip()
    if not code:
        return {}
    conn = _get_conn()
    row = conn.execute(
        "SELECT device_code, local_dir, source, created_at, updated_at "
        "FROM device_mapping WHERE device_code = ?", (code,)).fetchone()
    if not row:
        return {}
    return dict(zip(("device_code", "local_dir", "source",
                     "created_at", "updated_at"), row))


def set_device_mapping(device_code: str, local_dir: str, source: str = "auto") -> bool:
    """写入/更新设备码 → 本地目录映射，返回是否写入成功

    首次插入记录 created_at；后续更新只刷新 local_dir/source/updated_at，
    created_at 保留首次建立时间。source 取值 'auto' / 'manual'（manual
    由自愈向导人工选择落库时使用，manual 优先级语义上高于 auto）。
    """
    code = str(device_code or "").strip()
    local_dir = str(local_dir or "").strip()
    if not code or not local_dir:
        return False
    if source not in ("auto", "manual"):
        source = "auto"
    conn = _get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO device_mapping (device_code, local_dir, source, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(device_code) DO UPDATE SET "
        "local_dir = excluded.local_dir, source = excluded.source, "
        "updated_at = excluded.updated_at",
        (code, local_dir, source, now, now))
    conn.commit()
    return True


def get_all_device_mappings() -> dict:
    """全部设备映射 {device_code: local_dir}（收集入口批量预取可用）"""
    conn = _get_conn()
    return {code: d for code, d in conn.execute(
        "SELECT device_code, local_dir FROM device_mapping")}


def delete_device_mapping(device_code: str) -> int:
    """删除指定设备映射（清除错误映射入口），返回受影响行数"""
    code = str(device_code or "").strip()
    if not code:
        return 0
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM device_mapping WHERE device_code = ?", (code,))
    conn.commit()
    return cur.rowcount


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
