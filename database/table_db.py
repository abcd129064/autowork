# -*- coding: utf-8 -*-
"""球桌数据本地 SQLite 存储层（独立模块）

职责：
- save_all(rows): 全量替换球桌数据并记录刷新时间
- query_page(page_no, page_size, keyword): 本地分页 + 全字段模糊搜索
- get_meta(): 返回 (总条数, 最后刷新时间字符串)
"""

import os
import sqlite3
from datetime import datetime

# 数据库文件路径：database/tables.db（随项目目录）
_DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database")
DB_PATH = os.path.join(_DB_DIR, "tables.db")

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


def _get_conn() -> sqlite3.Connection:
    os.makedirs(_DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_CREATE_SQL)
    return conn


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
