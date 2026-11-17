# -*- coding: utf-8 -*-
"""步骤 3 客户端接入：/api/sync 增量读路径落地本地 SQLite（默认关闭）。

绞杀者模式：本模块是新增入口，调用方按表逐步接入；未接入的读写仍走
database/backend.py 直连。连接由调用方注入，避免模块自己定位/写真实业务库。
"""
import sqlite3
from datetime import datetime

from database import sync_transport as st

# 与服务端 sync_core.TABLE_SPECS 的键/水位/策略对齐；tests/test_sync_multi_table.py
# 会逐表断言一致，避免双端各维护一份漂移。服务端 spec 还带列清单，客户端
# 只需业务键与水位（列取本地表实际列的交集）。strategy 决定落库冲突语义：
#   lww        —— 服务端 updated_at 严格更新才覆盖
#   overwrite  —— 快照表按主键无条件覆盖
#   append     —— 追加日志，只补新增，永不覆盖
#   upsert_kd  —— 按 (file_path,device_code) 覆盖，绝不删本地其他设备行
TABLE_SPECS = {
    "aftersale_records": {
        "key": ("created_at", "creator", "table_no", "problem"),
        "watermark": "last_sync_api_aftersale_records",
        "has_deleted": True, "strategy": "lww",
    },
    "ledger_records": {
        "key": ("created_at", "signer", "category", "kind", "video_name"),
        "watermark": "last_sync_api_ledger_records",
        "has_deleted": False, "strategy": "lww",
    },
    "device_mapping": {
        "key": ("device_code",),
        "watermark": "last_sync_api_device_mapping",
        "has_deleted": False, "strategy": "lww",
    },
    "health_alerts": {
        "key": ("name",),
        "watermark": "last_sync_api_health_alerts",
        "has_deleted": False, "strategy": "overwrite",
    },
    "billiard_tables": {
        "key": ("id",),
        "watermark": "last_sync_api_billiard_tables",
        "has_deleted": False, "strategy": "overwrite",
    },
    "xqzg_status": {
        "key": ("id",),
        "watermark": "last_sync_api_xqzg_status",
        "has_deleted": False, "strategy": "overwrite",
    },
    "submission_log": {
        "key": ("id",),
        "watermark": "last_sync_api_submission_log",
        "has_deleted": False, "strategy": "append",
    },
    "kd_status": {
        "key": ("file_path", "device_code"),
        "watermark": "last_sync_api_kd_status",
        "has_deleted": False, "strategy": "upsert_kd",
    },
}

# 首表别名：保留给既有调用方与单测，不代表只允许首表。
WATERMARK_KEY = TABLE_SPECS["aftersale_records"]["watermark"]
BUSINESS_KEY = TABLE_SPECS["aftersale_records"]["key"]
TABLE = "aftersale_records"
MAX_BATCHES = 50


def _parse_dt(value) -> datetime:
    """与服务端 sync_core.parse_dt / database/merge_back.py 同口径。"""
    try:
        return datetime.strptime(str(value or "").strip()[:19],
                                 "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return datetime.min


class SyncService:
    """把服务端增量行按 TABLE_SPECS.strategy 合并进本地表，并记录水位。"""

    def __init__(self, transport, conn: sqlite3.Connection,
                 table: str = TABLE, max_batches: int = MAX_BATCHES):
        if table not in TABLE_SPECS:
            raise ValueError(f"unsupported sync table: {table}")
        self.transport = transport
        self.conn = conn
        self.table = table
        self.spec = TABLE_SPECS[table]
        self.max_batches = max_batches

    @classmethod
    def from_settings(cls, conn, table: str = TABLE):
        """配置域 sync_api 关闭时返回 None，绝不静默启用新链路。"""
        transport = st.SyncTransport.from_settings()
        return cls(transport, conn, table=table) if transport is not None else None

    @classmethod
    def run_if_enabled(cls, conn, table: str = TABLE, **kwargs):
        """显式触发入口（不自动挂载后台定时任务）。

        决策理由：双写窗口尚未做 API vs 直连对账前，自动定时同步会把服务端
        数据悄悄写进本地库；灰度期只应由用户的显式动作触发，失败以返回值
        告知调用方显示，不抛异常打断 UI。
        """
        svc = cls.from_settings(conn, table=table)
        if svc is None:
            return None
        try:
            return dict(svc.pull_incremental(**kwargs), ok=True, table=table)
        except Exception as e:
            return {"ok": False, "table": table,
                    "error": f"{type(e).__name__}: {e}"}

    def _columns(self) -> set:
        return {r[1] for r in self.conn.execute(
            f"PRAGMA table_info({self.table})")}

    def _read_watermark(self) -> str:
        row = self.conn.execute(
            "SELECT value FROM sync_meta WHERE key = ?",
            (self.spec["watermark"],)).fetchone()
        return (row[0] if row else "") or ""

    def _write_watermark(self, cursor: str):
        # UPSERT 而非 INSERT：重复同步同一水位不能撞主键；SQLite/MySQL 双方言通用
        self.conn.execute(
            "INSERT INTO sync_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (self.spec["watermark"], cursor))

    def pull_incremental(self, page_size: int = 200,
                         active_only: bool = False) -> dict:
        """分页拉取并合并；返回统计与提示，count=0 必须让 UI 可见。

        只写服务端返回行与本地表实际列的交集：双端 schema 允许有差异
        （如 MySQL 专有 deleted/deleted_at），未知列直接忽略而不是报错。
        """
        contract = self.transport.check_contract() or {}
        tables = contract.get("tables")
        if tables and self.table not in tables:
            raise RuntimeError(f"server contract lacks table {self.table}")
        cols = self._columns()
        key_cols = self.spec["key"]
        if not cols or not set(key_cols) <= cols:
            raise RuntimeError(f"local {self.table} schema lacks business key")
        strategy = self.spec["strategy"]
        local_non_key = sorted(cols - set(key_cols) - {"id"})
        key_where = " AND ".join(f"{c} = ?" for c in key_cols)
        # 只有 lww 表才读本地 updated_at；运维快照表根本没有该列，拼进去会
        # 直接报 "no such column: updated_at"。
        lww = strategy == "lww"
        select_sql = (f"SELECT {'updated_at' if lww else '1'} FROM {self.table} "
                      f"WHERE {key_where} LIMIT 1")

        cursor = self._read_watermark() or None
        pages = rows_seen = inserted = updated = skipped = 0
        notice = ""
        for _ in range(self.max_batches):
            page = self.transport.query(
                self.table, cursor=cursor, page_size=page_size,
                # 无软删除列的表不请求过滤，避免服务端拼出 deleted=0
                active_only=active_only and self.spec["has_deleted"])
            rows = page.get("rows") or []
            pages += 1
            rows_seen += len(rows)
            if not rows:
                # 契约要求：同步 0 条不能静默，交给调用方显示提示
                notice = "本次同步 0 条记录"
                break
            # 用 savepoint 而非 BEGIN：调用方连接可能已有未提交事务，
            # 显式 BEGIN 会报 "cannot start a transaction within a transaction"；
            # savepoint 可嵌套，失败只回滚本次拉取，不提交别人的事务。
            self.conn.execute("SAVEPOINT sync_pull")
            try:
                for raw in rows:
                    keys = [raw.get(c, "") for c in key_cols]
                    exist = self.conn.execute(select_sql, keys).fetchone()
                    if lww:
                        # 本地更新或平手：服务端不能覆盖（与 merge_back 同口径）
                        if exist and _parse_dt(raw.get("updated_at")) <= _parse_dt(exist[0]):
                            skipped += 1
                            continue
                    elif strategy == "append" and exist:
                        skipped += 1  # 追加语义：已存在的日志永不覆盖
                        continue
                    # overwrite / upsert_kd：存在即覆盖；两者都只动命中的行，
                    # 绝不 DELETE 本地其他设备/其他日期的记录。
                    shared = [c for c in local_non_key if c in raw]
                    if exist and not shared:
                        skipped += 1  # 没有可更新的交集列，避免拼出空 SET
                        continue
                    if exist:
                        set_str = ", ".join(f"{c} = ?" for c in shared)
                        self.conn.execute(
                            f"UPDATE {self.table} SET {set_str} WHERE {key_where}",
                            [raw[c] for c in shared] + keys)
                        updated += 1
                    else:
                        all_cols = list(key_cols) + shared
                        ph = ", ".join(["?"] * len(all_cols))
                        self.conn.execute(
                            f"INSERT INTO {self.table} ({', '.join(all_cols)}) "
                            f"VALUES ({ph})",
                            [raw.get(c, "") for c in all_cols])
                        inserted += 1
                if page.get("next_cursor"):
                    self._write_watermark(page["next_cursor"])
                self.conn.execute("RELEASE sync_pull")
            except Exception:
                self.conn.execute("ROLLBACK TO sync_pull")
                self.conn.execute("RELEASE sync_pull")
                raise
            cursor = page.get("next_cursor") or cursor
            if not page.get("has_more"):
                break
        else:
            notice = f"达到分批上限 {self.max_batches}，同步未完成"
        return {"pages": pages, "rows": rows_seen, "inserted": inserted,
                "updated": updated, "skipped": skipped,
                "cursor": cursor, "notice": notice}
