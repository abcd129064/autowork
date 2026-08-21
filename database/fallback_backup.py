# -*- coding: utf-8 -*-
"""MySQL → SQLite 周备份（兜底基线刷新）

每周把 MySQL 全量数据拉到本地 SQLite，使降级时本地有较新基线（≤7 天）。
仅在 ONLINE（MySQL 可用）时执行；降级期间不备份（避免覆盖兜底增量）。

与「恢复后 LWW 合并」（阶段二）互补：
- 周备份：保 ONLINE 期间本地基线新鲜，降级瞬间不至于停在切换前死快照；
- LWW 合并：保降级期间本地写入的增量在恢复后回写 MySQL，不丢数据。

对外 API：
- backup_mysql_to_sqlite(progress_cb) -> (ok, msg, count)：全量拉取替换
- is_backup_due() -> bool：距上次备份是否满 7 天
- maybe_backup(progress_cb) -> (ok, msg, count)：到期才执行，供启动/定时调用
- get_last_backup_time() / set_last_backup_time(t)：上次备份时间读写（sync_meta）
"""

import sqlite3
from datetime import datetime

from database import backend, table_db
from database.table_db import DB_PATH

BACKUP_INTERVAL_DAYS = 7

# 需备份的表（sync_meta 为元信息不备份）
_BACKUP_TABLES = (
    "billiard_tables", "xqzg_status", "kd_status", "submission_log",
    "device_mapping", "aftersale_records", "health_alerts",
)
_LAST_BACKUP_KEY = "mysql_last_backup"


def get_last_backup_time() -> str:
    """上次周备份时间（yyyy-MM-dd HH:mm:ss），从未备份返回空串"""
    try:
        sl = sqlite3.connect(DB_PATH)
        try:
            r = sl.execute(
                "SELECT value FROM sync_meta WHERE key=?", (_LAST_BACKUP_KEY,)
            ).fetchone()
            return r[0] if r else ""
        finally:
            sl.close()
    except sqlite3.Error:
        return ""


def set_last_backup_time(t: str):
    """记录周备份时间到 sync_meta（表不存在则兜底创建）"""
    sl = sqlite3.connect(DB_PATH)
    try:
        sl.execute("CREATE TABLE IF NOT EXISTS sync_meta("
                   "key VARCHAR(128) PRIMARY KEY, value TEXT)")
        sl.execute("INSERT OR REPLACE INTO sync_meta(key,value) VALUES(?,?)",
                   (_LAST_BACKUP_KEY, t))
        sl.commit()
    finally:
        sl.close()


def is_backup_due() -> bool:
    """距上次周备份是否已达 7 天（或从未备份/时间非法）"""
    last = get_last_backup_time()
    if not last:
        return True
    try:
        dt = datetime.strptime(last[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return (datetime.now() - dt).days >= BACKUP_INTERVAL_DAYS


def backup_mysql_to_sqlite(progress_cb=None) -> tuple:
    """MySQL 全量 → 本地 SQLite 全量替换（兜底基线刷新）

    仅在 ONLINE 时执行；连接失败则 mark_degraded 并返回失败。
    逐表 DELETE + INSERT OR REPLACE，按列名取交集防御两侧列差异。
    MySQL 侧连接走 backend（默认 tuple cursor），SQLite 侧直接连 tables.db。

    Returns:
        (ok: bool, message: str, count: int)
    """
    if backend.get_state() != backend.STATE_ONLINE:
        return False, "MySQL 非在线，跳过周备份", 0
    try:
        mysql_conn = backend.create_mysql_connection()
    except Exception as e:
        backend.mark_degraded()
        return False, f"MySQL 连接失败，跳过周备份：{e}", 0

    sl = sqlite3.connect(DB_PATH)
    total = 0
    try:
        table_db._ensure_initialized(sl)  # 确保本地表结构就绪
        for tbl in _BACKUP_TABLES:
            sl_cols = [r[1] for r in sl.execute(
                f"PRAGMA table_info({tbl})").fetchall()]
            if not sl_cols:
                continue  # 本地无此表，跳过
            cur = mysql_conn.execute(f"SELECT * FROM `{tbl}`")
            rows = cur.fetchall()
            if not rows:
                # MySQL 无数据也清空本地表，保持基线一致
                sl.execute(f'DELETE FROM "{tbl}"')
                sl.commit()
                if progress_cb:
                    progress_cb(f"{tbl}: 0 条（已清空）")
                continue
            mysql_cols = [d[0] for d in cur.description]
            common = [c for c in mysql_cols if c in sl_cols]
            if not common:
                continue
            col_list = ",".join(f'"{c}"' for c in common)
            ph = ",".join(["?"] * len(common))
            idx = {c: i for i, c in enumerate(mysql_cols)}
            data = [[r[idx[c]] for c in common] for r in rows]
            sl.execute(f'DELETE FROM "{tbl}"')
            sl.executemany(
                f'INSERT OR REPLACE INTO "{tbl}" ({col_list}) VALUES ({ph})',
                data)
            sl.commit()
            total += len(data)
            if progress_cb:
                progress_cb(f"{tbl}: {len(data)} 条")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        set_last_backup_time(now)
        return True, f"周备份完成，共 {total} 条", total
    finally:
        sl.close()
        try:
            mysql_conn.close()
        except Exception:
            pass


def maybe_backup(progress_cb=None) -> tuple:
    """若距上次备份满 7 天且 MySQL 在线，则执行一次周备份；否则跳过

    供启动 / 定时调用。跳过时返回 (True, "未到期", 0)。
    """
    if not is_backup_due():
        return True, "周备份未到期，跳过", 0
    return backup_mysql_to_sqlite(progress_cb)
