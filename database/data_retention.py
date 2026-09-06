# -*- coding: utf-8 -*-
"""数据保留自动清理（双后端兼容，纯逻辑层不依赖 PySide6）

背景
----
MySQL 主库模式下业务数据持续增长，本模块提供两类自动清理，防止数据量
无限膨胀：

A. 按时间过期清理（xqzg_status / kd_status）
   两表无独立时间列，日期信息编码在 file_path（``yyyy/MM/dd`` 分区，
   如 ``2026/08/21``；字符串字典序即时间序）。直接按
   ``file_path < (today - age_days)`` 删除过期分区，每日执行幂等。

B. 按大小清理（其余业务流水表）
   范围：aftersale_records / ledger_records / submission_log /
   health_alerts（device_mapping 为设备→目录映射表，删除会导致重新识别，
   不纳入；billiard_tables / sync_meta 为元数据，永不清理）。
   每 check_interval_days 天检查一次（sync_meta 记录 ``last_size_check``），
   表大小超过 max_size_gb 时按日期桶从最早开始逐日删除，直到低于
   min_size_gb 或最早日期进入 min_keep_days 保护期（默认最近 30 天不删）。

配置（settings.json → data_retention）
--------------------------------------
.. code-block:: json

    {
      "enabled": true,             // 总开关
      "age_days": 60,              // A：xqzg/kd 保留天数
      "check_interval_days": 60,   // B：大小检查间隔（天）
      "max_size_gb": 3,            // B：触发清理的大小阈值
      "min_size_gb": 2,            // B：清理目标（低于此值停止）
      "min_keep_days": 30,         // B：最低保留天数（保护，防误删光）
      "tables": [...]              // B：白名单子集（默认全部 4 张）
    }

双后端说明
----------
- 表大小统计：MySQL 用 information_schema.tables 的
  data_length + index_length；SQLite 用 dbstat 虚表（不可用时跳过该表）。
- 删除 SQL 全部使用 ``?`` 占位符与双方言共有函数（substr/COALESCE/
  NULLIF），经 database.backend._convert_sql 自动转方言，无需额外适配。
- 日期桶格式统一为 ``YYYY-MM-DD``（substr 前缀提取），与既有跑视频面板
  ``substr(COALESCE(NULLIF(occurred_at,''), created_at),1,10)`` 口径一致。
"""

import json
import os
from datetime import date, datetime, timedelta

# ==================== 配置 ====================

DEFAULT_CONFIG = {
    "enabled": True,
    "age_days": 60,
    "check_interval_days": 60,
    "max_size_gb": 3,
    "min_size_gb": 2,
    "min_keep_days": 30,
}

# 按大小清理的表白名单：表名 → 日期桶提取 SQL 表达式（模块内硬编码，
# 白名单校验后以 f-string 拼入 SQL，无注入风险）。
# - aftersale_records / ledger_records：与面板筛选同口径，
#   occurred_at 优先（兼容纯日期/完整时间两格式），空则回退 created_at
# - submission_log：created_at 必有值，前缀取日期
# - health_alerts：仅 updated_at；空值兜底 1970-01-01（迁移前旧记录，
#   健康状态会重新上报，删旧无害）
_SIZE_TABLES = {
    "aftersale_records":
        "substr(COALESCE(NULLIF(occurred_at,''), created_at),1,10)",
    "ledger_records":
        "substr(COALESCE(NULLIF(occurred_at,''), created_at),1,10)",
    "submission_log":
        "substr(COALESCE(NULLIF(created_at,''),'1970-01-01'),1,10)",
    "health_alerts":
        "substr(COALESCE(NULLIF(updated_at,''),'1970-01-01'),1,10)",
}

# 按时间过期清理的表（结构完全相同：file_path 为 yyyy/MM/dd 日期分区）
_STATUS_TABLES = ("xqzg_status", "kd_status")

_SYNC_META_KEY = "last_size_check"


# ==================== config/database.json 读取（不依赖 core，避免 PySide6 链） ====================

def _app_dir() -> str:
    import sys as _sys
    if getattr(_sys, "frozen", False):
        return os.path.dirname(_sys.executable)
    # data_retention.py 位于 database/，向上两级即项目根目录
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_settings() -> dict:
    """读取 config/database.json（域文件，data_retention 节点所在）；
    缺失/损坏返回 {}（不抛异常）"""
    path = os.path.join(_app_dir(), "config", "database.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _load_config() -> dict:
    """读取 data_retention 配置节点（缺省回落 DEFAULT_CONFIG）"""
    raw = _read_settings().get("data_retention") or {}
    cfg = dict(DEFAULT_CONFIG)
    for k in ("enabled",):
        if k in raw:
            cfg[k] = bool(raw[k])
    for k in ("age_days", "check_interval_days", "max_size_gb",
              "min_size_gb", "min_keep_days"):
        if k in raw:
            try:
                cfg[k] = int(raw[k])
            except (TypeError, ValueError):
                pass
    # tables 白名单过滤（未知表名忽略，防止配置误写）
    if isinstance(raw.get("tables"), list):
        cfg["tables"] = [t for t in raw["tables"] if t in _SIZE_TABLES]
    else:
        cfg["tables"] = list(_SIZE_TABLES.keys())
    return cfg


def is_enabled() -> bool:
    """总开关（worker 挂载时判断，避免频繁读文件）"""
    try:
        return bool(_load_config().get("enabled", True))
    except Exception:
        return True


# ==================== 后端识别与表大小 ====================

def _is_mysql_conn(conn) -> bool:
    """当前连接是否为 MySQL 适配器（比 backend 状态判断更直接：
    DEGRADED 兜底时拿到的是 SQLite 连接，同样走 SQLite 统计分支）"""
    return type(conn).__name__ == "MysqlConnectionAdapter"


def _table_size_bytes(conn, table):
    """表占用字节（MySQL information_schema / SQLite dbstat）

    返回 int；统计失败返回 None（调用方跳过该表，不阻塞其他表）。
    """
    if _is_mysql_conn(conn):
        try:
            cur = conn.execute(
                "SELECT data_length + index_length "
                "FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = %s",
                (table,))
            row = cur.fetchone()
            return int(row[0]) if row and row[0] else 0
        except Exception:
            return None
    try:
        cur = conn.execute(
            "SELECT SUM(pgsize) FROM dbstat WHERE name = ?", (table,))
        row = cur.fetchone()
        return int(row[0]) if row and row[0] else 0
    except Exception:
        # dbstat 虚表不可用（部分编译选项关闭）：无法按表统计，跳过
        return None


# ==================== A：按时间过期清理 ====================

def _cleanup_status_by_age(conn, age_days: int) -> int:
    """删除 xqzg_status / kd_status 中 file_path 早于 age_days 天的分区

    返回删除总行数。file_path 空串恒被排除（'' 字典序最早，误删整表）。
    """
    threshold = (date.today() - timedelta(days=age_days)).strftime("%Y/%m/%d")
    total = 0
    for table in _STATUS_TABLES:
        cur = conn.execute(
            f"DELETE FROM {table} WHERE file_path != '' AND file_path < ?",
            (threshold,))
        conn.commit()
        total += int(getattr(cur, "rowcount", 0) or 0)
    return total


# ==================== B：按大小清理 ====================

def _size_check_due(conn, interval_days: int) -> bool:
    """距上次大小检查是否已满 interval_days（sync_meta 记录）

    无记录或记录损坏视为到期。记录日期格式 YYYY-MM-DD。
    """
    try:
        cur = conn.execute(
            "SELECT value FROM sync_meta WHERE key = ?", (_SYNC_META_KEY,))
        row = cur.fetchone()
    except Exception:
        return True
    if not row or not row[0]:
        return True
    try:
        last = datetime.strptime(str(row[0]), "%Y-%m-%d").date()
    except ValueError:
        return True
    return (date.today() - last).days >= interval_days


def _cleanup_table_by_size(conn, table: str, expr: str, cfg: dict,
                           progress_cb) -> int:
    """单表按大小清理：从最早日期桶逐日删除，直到低于 min_size_gb

    规则：
    - 每次循环先查当前表大小，<= max_size_gb 即停止（未超阈值不删）
    - 取最早日期桶（排除空值桶），若该桶 >= 保护期阈值（最近
      min_keep_days 天）说明无可删数据，停止
    - 逐桶删除并逐桶提交（单日影响可控，失败只丢一桶）
    """
    max_size = int(cfg["max_size_gb"]) * 1024 ** 3
    min_size = int(cfg["min_size_gb"]) * 1024 ** 3
    keep_threshold = (date.today() - timedelta(
        days=int(cfg["min_keep_days"]))).strftime("%Y-%m-%d")
    total = 0
    while True:
        size = _table_size_bytes(conn, table)
        if size is None or size <= max_size:
            break
        # 取最早非空日期桶
        cur = conn.execute(
            f"SELECT {expr} AS d, COUNT(*) FROM {table} "
            f"WHERE {expr} != '' GROUP BY d ORDER BY d ASC LIMIT 1")
        row = cur.fetchone()
        if not row or not row[0]:
            break  # 无可删数据
        bucket = str(row[0])
        if bucket >= keep_threshold:
            # 最早日期已进入保护期，停止（防极端情况删光最近数据）
            if progress_cb:
                progress_cb(f"{table}: 最早日期 {bucket} 已进入 {cfg['min_keep_days']} 天保护期，停止")
            break
        cur = conn.execute(
            f"DELETE FROM {table} WHERE {expr} = ?", (bucket,))
        conn.commit()
        n = int(getattr(cur, "rowcount", 0) or 0)
        total += n
        if progress_cb:
            progress_cb(f"{table}: 删除 {bucket} 共 {n} 行")
    return total


def _cleanup_by_size(conn, cfg: dict, progress_cb) -> int:
    """按大小清理：检查间隔到期才执行；返回删除总行数"""
    interval = int(cfg.get("check_interval_days", 60))
    if not _size_check_due(conn, interval):
        return 0
    total = 0
    for table in cfg.get("tables", []):
        expr = _SIZE_TABLES.get(table)
        if not expr:
            continue
        try:
            total += _cleanup_table_by_size(conn, table, expr, cfg,
                                            progress_cb)
        except Exception as e:
            if progress_cb:
                progress_cb(f"{table}: 清理失败 {type(e).__name__}: {e}")
            # 单表失败不阻断其他表，继续
    # 无论是否实际删除，本次检查视为完成（控制检查频率）
    _mark_size_checked(conn)
    return total


def _mark_size_checked(conn):
    """记录本次大小检查时间（双后端兼容 upsert）"""
    from database import table_db
    try:
        table_db._upsert_sync_meta(conn, _SYNC_META_KEY,
                                   date.today().strftime("%Y-%m-%d"))
        conn.commit()
    except Exception:
        pass  # 记录失败不影响清理结果


# ==================== 主入口 ====================

def run_cleanup(progress_cb=None):
    """执行一轮数据保留清理（A + B）

    Returns:
        (ok: bool, msg: str, deleted: int) —— deleted 为两类清理删除总行数
    """
    cfg = _load_config()
    if not cfg.get("enabled", True):
        return True, "数据保留清理已禁用，跳过", 0
    try:
        from database import table_db
        conn = table_db._get_conn()
    except Exception as e:
        return False, f"获取数据库连接失败: {type(e).__name__}: {e}", 0

    try:
        n_age = _cleanup_status_by_age(conn, int(cfg.get("age_days", 60)))
    except Exception as e:
        return False, f"状态表过期清理失败: {type(e).__name__}: {e}", 0

    try:
        n_size = _cleanup_by_size(conn, cfg, progress_cb)
    except Exception as e:
        return False, f"按大小清理失败: {type(e).__name__}: {e}", 0

    total = n_age + n_size
    if total > 0:
        msg = (f"状态表过期清理 {n_age} 行，按大小清理 {n_size} 行，"
               f"共 {total} 行")
    else:
        msg = "无过期数据，无需清理"
    return True, msg, total
