# -*- coding: utf-8 -*-
"""球桌数据本地 SQLite 存储层（独立模块）

职责：
- save_all(rows): 全量替换球桌数据并记录刷新时间
- query_page(page_no, page_size, keyword): 本地分页 + 全字段模糊搜索
- get_meta(): 返回 (总条数, 最后刷新时间字符串)
- save_xqzg / query_xqzg_page: 接口1 (xqzg.newbv.cn) 数据存取
- save_kd / query_kd_page: 接口2 (kd.newbv.cn) 数据存取
"""

import json
import os
import sqlite3
from datetime import datetime

# 数据库文件路径：database/tables.db（随项目目录）
_DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database")
DB_PATH = os.path.join(_DB_DIR, "tables.db")

# ==================== 原有球桌表（wechat2-billiard 接口） ====================

# 存储的字段（与面板展示列一致）
FIELDS = ("name", "roomName", "onlineStatusName", "remark", "cameraPassExt")

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS billiard_tables (
    id      INTEGER PRIMARY KEY,
    name    TEXT DEFAULT '',
    roomName TEXT DEFAULT '',
    onlineStatusName TEXT DEFAULT '',
    remark  TEXT DEFAULT '',
    cameraPassExt TEXT DEFAULT ''
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

# kd_status 额外字段（文件列表 + 路径信息）
KD_EXTRA_FIELDS = (
    "device_code", "target_directory",
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
    normal_files    TEXT DEFAULT '[]',
    except_files    TEXT DEFAULT '[]',
    untreated_files TEXT DEFAULT '[]',
    operation_files TEXT DEFAULT '[]',
    accuracy_files  TEXT DEFAULT '[]',
    already_files   TEXT DEFAULT '[]',
    rubbish_files   TEXT DEFAULT '[]',
    version_files   TEXT DEFAULT '[]'
);
"""


def _get_conn() -> sqlite3.Connection:
    os.makedirs(_DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_CREATE_SQL)
    conn.executescript(_CREATE_STATUS_SQL)
    # 迁移修复：若 billiard_tables 被误改为新字段（缺少 name 列），DROP 重建
    cols = [r[1] for r in conn.execute("PRAGMA table_info(billiard_tables)").fetchall()]
    if cols and "name" not in cols:
        conn.execute("DROP TABLE billiard_tables")
        conn.executescript(_CREATE_SQL)
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
    return conn


# ==================== 原有球桌表操作 ====================

def save_all(rows: list) -> int:
    """全量替换数据，返回写入条数"""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM billiard_tables")
        data = []
        for item in rows:
            data.append((
                item.get("id") or 0,
                str(item.get("name") or ""),
                str(item.get("roomName") or ""),
                str(item.get("onlineStatusName") or ""),
                str(item.get("remark") or ""),
                str(item.get("cameraPassExt") or ""),
            ))
        conn.executemany(
            "INSERT OR REPLACE INTO billiard_tables (id, name, roomName, onlineStatusName, remark, cameraPassExt) "
            "VALUES (?, ?, ?, ?, ?, ?)", data)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT OR REPLACE INTO sync_meta (key, value) VALUES ('last_sync', ?)", (now,))
        conn.commit()
        return len(data)
    finally:
        conn.close()


def query_page(page_no: int, page_size: int, keyword: str = "") -> tuple:
    """本地分页查询，支持全字段模糊搜索

    Returns:
        (total, rows)  rows 为 list[dict]
    """
    conn = _get_conn()
    try:
        where = ""
        params = []
        kw = keyword.strip()
        if kw:
            like = f"%{kw}%"
            conds = " OR ".join([f"{f} LIKE ?" for f in FIELDS])
            where = f" WHERE {conds}"
            params = [like] * len(FIELDS)

        total = conn.execute(
            f"SELECT COUNT(*) FROM billiard_tables{where}", params).fetchone()[0]

        offset = (page_no - 1) * page_size
        cursor = conn.execute(
            f"SELECT id, name, roomName, onlineStatusName, remark, cameraPassExt "
            f"FROM billiard_tables{where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [page_size, offset])
        cols = [d[0] for d in cursor.description]
        rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
        return total, rows
    finally:
        conn.close()


def insert_one(record: dict) -> int:
    """手动插入单条记录（API 失效时的兜底入口），返回新记录 id"""
    conn = _get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO billiard_tables (name, roomName, onlineStatusName, remark, cameraPassExt) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                str(record.get("name") or ""),
                str(record.get("roomName") or ""),
                str(record.get("onlineStatusName") or ""),
                str(record.get("remark") or ""),
                str(record.get("cameraPassExt") or ""),
            ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_meta() -> tuple:
    """返回 (总条数, 最后同步时间字符串)，无数据时返回 (0, '')"""
    conn = _get_conn()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM billiard_tables").fetchone()[0]
        row = conn.execute(
            "SELECT value FROM sync_meta WHERE key='last_sync'").fetchone()
        return total, (row[0] if row else "")
    finally:
        conn.close()


# ==================== 接口1 xqzg_status 表操作 ====================

def save_xqzg(rows: list) -> int:
    """全量替换接口1数据，返回写入条数"""
    return _save_status_table("xqzg_status", rows, "last_sync_xqzg")


def query_xqzg_page(page_no: int, page_size: int, keyword: str = "") -> tuple:
    """接口1数据分页查询"""
    return _query_status_page("xqzg_status", page_no, page_size, keyword)


# ==================== 接口2 kd_status 表操作 ====================

def save_kd(rows: list, file_path: str = "") -> int:
    """按日期替换接口2数据（含扩展字段），返回写入条数

    Args:
        rows: API 返回的记录列表
        file_path: 日期路径，如 "2026/08/02"；仅替换该日期的数据
    """
    conn = _get_conn()
    try:
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
        return len(data)
    finally:
        conn.close()


def query_kd_page(page_no: int, page_size: int, keyword: str = "", file_path: str = "") -> tuple:
    """接口2数据分页查询（含扩展字段）

    Args:
        file_path: 日期路径筛选，如 "2026/08/02"；为空则查全部日期
    """
    conn = _get_conn()
    try:
        all_fields = ("file_path",) + STATUS_FIELDS + KD_EXTRA_FIELDS
        conds = []
        params = []
        # 日期筛选
        if file_path:
            conds.append("file_path = ?")
            params.append(file_path)
        # 关键词搜索
        kw = keyword.strip()
        if kw:
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
        cursor = conn.execute(
            f"SELECT {select_cols} "
            f"FROM kd_status{where} ORDER BY id LIMIT ? OFFSET ?",
            params + [page_size, offset])
        cols = [d[0] for d in cursor.description]
        rows = []
        for r in cursor.fetchall():
            row_dict = dict(zip(cols, r))
            # 反序列化文件列表字段
            for f in KD_FILE_FIELDS:
                try:
                    row_dict[f] = json.loads(row_dict.get(f) or "[]")
                except (json.JSONDecodeError, TypeError):
                    row_dict[f] = []
            rows.append(row_dict)
        return total, rows
    finally:
        conn.close()


def get_kd_dates() -> list:
    """获取 kd_status 中已存储的所有日期列表（降序）"""
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "SELECT DISTINCT file_path FROM kd_status WHERE file_path != '' ORDER BY file_path DESC")
        return [r[0] for r in cursor.fetchall()]
    finally:
        conn.close()


# ==================== 通用内部函数 ====================

def _save_status_table(table_name: str, rows: list, meta_key: str) -> int:
    """全量替换指定运维数据表"""
    conn = _get_conn()
    try:
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
    finally:
        conn.close()


def _query_status_page(table_name: str, page_no: int, page_size: int, keyword: str = "") -> tuple:
    """运维数据表通用分页查询"""
    conn = _get_conn()
    try:
        where = ""
        params = []
        kw = keyword.strip()
        if kw:
            like = f"%{kw}%"
            conds = " OR ".join([f"{f} LIKE ?" for f in STATUS_FIELDS])
            where = f" WHERE {conds}"
            params = [like] * len(STATUS_FIELDS)

        total = conn.execute(
            f"SELECT COUNT(*) FROM {table_name}{where}", params).fetchone()[0]

        offset = (page_no - 1) * page_size
        select_cols = "id, " + ", ".join(STATUS_FIELDS)
        cursor = conn.execute(
            f"SELECT {select_cols} "
            f"FROM {table_name}{where} ORDER BY id LIMIT ? OFFSET ?",
            params + [page_size, offset])
        cols = [d[0] for d in cursor.description]
        rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
        return total, rows
    finally:
        conn.close()
