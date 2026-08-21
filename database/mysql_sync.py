# -*- coding: utf-8 -*-
"""MySQL 远程镜像同步层（SQLite → MySQL 单向推送）

职责：
- 从 settings.json 读取 MySQL 连接配置
- 首次连接自动建库建表（幂等）
- push_all(): 全量推送 5 张业务表到 MySQL
- push_table(): 按表名单独推送
- test_connection(): 测试连接是否可达

设计原则：
- SQLite 始终为主读写库，本模块只做单向推送，绝不反向写入 SQLite
- pymysql 不可用或连接失败时静默降级，不影响主流程
- 占位符从 SQLite 的 ? 转换为 MySQL 的 %s
- INSERT OR REPLACE 转换为 INSERT ... ON DUPLICATE KEY UPDATE
"""

import json
import os
import sqlite3
import time
from datetime import datetime

from database import backend

# ==================== 配置读取 ====================

def _load_mysql_config() -> dict:
    """从 settings.json 读取 mysql_sync 配置（敏感字段透明解密）"""
    from core.app_paths import get_app_dir
    from core.secrets import decrypt_settings
    path = os.path.join(get_app_dir(), "settings.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            settings = decrypt_settings(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    cfg = settings.get("mysql_sync", {})
    if not cfg.get("enabled", False):
        return {}
    return cfg


def _get_pymysql():
    """延迟导入 pymysql，未安装时返回 None"""
    try:
        import pymysql
        return pymysql
    except ImportError:
        return None


# ==================== MySQL 建表 DDL ====================

_CREATE_DATABASE = (
    "CREATE DATABASE IF NOT EXISTS `{db}` "
    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
)

# billiard_tables：球桌主数据
_DDL_BILLIARD_TABLES = """
CREATE TABLE IF NOT EXISTS billiard_tables (
    id              INT PRIMARY KEY,
    name            VARCHAR(255) DEFAULT '',
    roomName        VARCHAR(255) DEFAULT '',
    onlineStatusName VARCHAR(255) DEFAULT '',
    remark          TEXT,
    cameraPassExt   VARCHAR(512) DEFAULT '',
    snk_code        VARCHAR(128) DEFAULT '',
    code            VARCHAR(255) DEFAULT '',
    city            VARCHAR(255) DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# xqzg_status：接口1 运维数据
_DDL_XQZG_STATUS = """
CREATE TABLE IF NOT EXISTS xqzg_status (
    id              INT PRIMARY KEY,
    table_id        VARCHAR(255) DEFAULT '',
    club_name       VARCHAR(255) DEFAULT '',
    pic_total       VARCHAR(64) DEFAULT '',
    normal_count    VARCHAR(64) DEFAULT '',
    normal_total    VARCHAR(64) DEFAULT '',
    except_count    VARCHAR(64) DEFAULT '',
    operation_rate  VARCHAR(64) DEFAULT '',
    untreated_count VARCHAR(64) DEFAULT '',
    operation_count VARCHAR(64) DEFAULT '',
    accuracy_count  VARCHAR(64) DEFAULT '',
    already_count   VARCHAR(64) DEFAULT '',
    rubbish_count   VARCHAR(64) DEFAULT '',
    error_rate      VARCHAR(64) DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# kd_status：接口2 运维数据（含文件 JSON）
_DDL_KD_STATUS = """
CREATE TABLE IF NOT EXISTS kd_status (
    id              INT PRIMARY KEY,
    file_path       VARCHAR(64) DEFAULT '',
    table_id        VARCHAR(255) DEFAULT '',
    club_name       VARCHAR(255) DEFAULT '',
    pic_total       VARCHAR(64) DEFAULT '',
    normal_count    VARCHAR(64) DEFAULT '',
    normal_total    VARCHAR(64) DEFAULT '',
    except_count    VARCHAR(64) DEFAULT '',
    operation_rate  VARCHAR(64) DEFAULT '',
    untreated_count VARCHAR(64) DEFAULT '',
    operation_count VARCHAR(64) DEFAULT '',
    accuracy_count  VARCHAR(64) DEFAULT '',
    already_count   VARCHAR(64) DEFAULT '',
    rubbish_count   VARCHAR(64) DEFAULT '',
    error_rate      VARCHAR(64) DEFAULT '',
    device_code     VARCHAR(255) DEFAULT '',
    target_directory VARCHAR(512) DEFAULT '',
    status          VARCHAR(32) DEFAULT '',
    normal_files    LONGTEXT,
    except_files    LONGTEXT,
    untreated_files LONGTEXT,
    operation_files LONGTEXT,
    accuracy_files  LONGTEXT,
    already_files   LONGTEXT,
    rubbish_files   LONGTEXT,
    version_files   LONGTEXT,
    INDEX idx_kd_file_path (file_path),
    INDEX idx_kd_device_code (device_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# submission_log：精度/问题提交台账
_DDL_SUBMISSION_LOG = """
CREATE TABLE IF NOT EXISTS submission_log (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    created_at      VARCHAR(32) DEFAULT '',
    device_code     VARCHAR(255) DEFAULT '',
    table_id        VARCHAR(255) DEFAULT '',
    club_name       VARCHAR(255) DEFAULT '',
    category        VARCHAR(64) DEFAULT '',
    file_name       VARCHAR(512) DEFAULT '',
    file_path_date  VARCHAR(64) DEFAULT '',
    collect_ok      TINYINT DEFAULT 0,
    upload_zip      VARCHAR(512),
    upload_ok       TINYINT,
    INDEX idx_sub_device_time (device_code, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# device_mapping：设备码映射
_DDL_DEVICE_MAPPING = """
CREATE TABLE IF NOT EXISTS device_mapping (
    device_code     VARCHAR(255) PRIMARY KEY,
    local_dir       VARCHAR(512) DEFAULT '',
    source          VARCHAR(32) DEFAULT 'auto',
    created_at      VARCHAR(32) DEFAULT '',
    updated_at      VARCHAR(32) DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# sync_meta：同步元信息（MySQL 侧记录每次推送时间）
_DDL_SYNC_META = """
CREATE TABLE IF NOT EXISTS sync_meta (
    `key`   VARCHAR(128) PRIMARY KEY,
    value   TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# aftersale_records：售后记录（多用户共享，推送走业务键 upsert 不覆盖）
_DDL_AFTERSALE_RECORDS = """
CREATE TABLE IF NOT EXISTS aftersale_records (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    created_at    VARCHAR(32) DEFAULT '',
    occurred_at   VARCHAR(32) DEFAULT '',
    creator       VARCHAR(255) DEFAULT '',
    issue_type    VARCHAR(255) DEFAULT '',
    table_no      VARCHAR(255) DEFAULT '',
    room_name     VARCHAR(255) DEFAULT '',
    region        VARCHAR(255) DEFAULT '',
    problem       TEXT,
    cause         TEXT,
    resolved      VARCHAR(255) DEFAULT '否',
    is_initiative VARCHAR(255) DEFAULT '否',
    is_our_problem VARCHAR(255) DEFAULT '是',
    solution      TEXT,
    resolver      VARCHAR(255) DEFAULT '',
    response_time VARCHAR(255) DEFAULT '',
    snk_code      VARCHAR(255) DEFAULT '',
    device_code   VARCHAR(255) DEFAULT '',
    cycle_start   VARCHAR(32) DEFAULT '',
    updated_at    VARCHAR(32) DEFAULT '',
    INDEX idx_aftersale_cycle (cycle_start),
    INDEX idx_aftersale_table_no (table_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_ALL_DDL = [
    _DDL_BILLIARD_TABLES,
    _DDL_XQZG_STATUS,
    _DDL_KD_STATUS,
    _DDL_SUBMISSION_LOG,
    _DDL_DEVICE_MAPPING,
    _DDL_SYNC_META,
    _DDL_AFTERSALE_RECORDS,
]


# ==================== 连接管理 ====================

def _connect(cfg: dict = None, use_database: bool = True):
    """创建 MySQL 连接；cfg 缺省时从 settings.json 读取

    use_database=False 时不指定 database（用于首次建库）
    """
    pymysql = _get_pymysql()
    if pymysql is None:
        raise RuntimeError("pymysql 未安装，请执行 pip install pymysql")
    if cfg is None:
        cfg = _load_mysql_config()
    if not cfg:
        raise RuntimeError("MySQL 同步未启用或配置缺失")
    kwargs = {
        "host": cfg.get("host", "127.0.0.1"),
        "port": int(cfg.get("port", 3306)),
        "user": cfg.get("user", "root"),
        "password": cfg.get("password", ""),
        "charset": "utf8mb4",
        "connect_timeout": 10,
        "cursorclass": pymysql.cursors.DictCursor,
    }
    if use_database:
        kwargs["database"] = cfg.get("database", "autowork")
    return pymysql.connect(**kwargs)


def _ensure_schema(cfg: dict):
    """确保目标数据库和全部表存在（幂等，首次连接时执行）

    先建库（无 database 连接），再连入目标库建表。
    """
    db_name = cfg.get("database", "autowork")
    # 先建库（连接时未指定 database）
    conn_tmp = _connect(cfg, use_database=False)
    try:
        with conn_tmp.cursor() as cur:
            cur.execute(_CREATE_DATABASE.format(db=db_name))
        conn_tmp.commit()
    finally:
        conn_tmp.close()
    # 建表
    conn = _connect(cfg, use_database=True)
    try:
        with conn.cursor() as cur:
            for ddl in _ALL_DDL:
                cur.execute(ddl)
        conn.commit()
        # 迁移：旧 billiard_tables 库缺 city 列（接口 roomCity）时补列
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM billiard_tables")
            exist = {r["Field"] for r in cur.fetchall()}
            if "city" not in exist:
                cur.execute(
                    "ALTER TABLE billiard_tables "
                    "ADD COLUMN city VARCHAR(255) DEFAULT ''")
                conn.commit()
        # 迁移：旧 aftersale_records 库缺 occurred_at 列时补列
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM aftersale_records")
            exist = {r["Field"] for r in cur.fetchall()}
            if "occurred_at" not in exist:
                cur.execute(
                    "ALTER TABLE aftersale_records "
                    "ADD COLUMN occurred_at VARCHAR(32) DEFAULT ''")
                conn.commit()
    finally:
        conn.close()


# ==================== 数据推送 ====================

def _read_sqlite(table_name: str, columns: tuple) -> list:
    """从本地 SQLite 读取指定表的全部数据，返回 list[tuple]

    按实际列与目标元组取交集读取，本地老库缺失的列补空串占位，
    避免 MySQL 模式下跳过 SQLite 迁移导致 no such column 推送失败。
    """
    from database.table_db import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    try:
        exist = [r[1] for r in conn.execute(
            f"PRAGMA table_info({table_name})").fetchall()]
        if not exist:
            return []  # 表不存在：无数据可推
        cols = [c for c in columns if c in exist]
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {table_name}").fetchall()
    finally:
        conn.close()
    if len(cols) == len(columns):
        return rows
    # 缺列补空串，按列名映射保持与 columns 顺序对齐
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        out.append(tuple(d.get(c, "") for c in columns))
    return out


# billiard_tables 字段
_BT_COLS = ("id", "name", "roomName", "onlineStatusName",
            "remark", "cameraPassExt", "snk_code", "code", "city")
_BT_MYSQL_COLS = _BT_COLS  # 同名

# xqzg_status 字段（与 kd_status 同套字段，无 file_path 日期分区）
_XQZG_COLS = (
    "id", "table_id", "club_name", "pic_total", "normal_count",
    "normal_total", "except_count", "operation_rate", "untreated_count",
    "operation_count", "accuracy_count", "already_count", "rubbish_count",
    "error_rate", "device_code", "target_directory", "status",
    "normal_files", "except_files", "untreated_files",
    "operation_files", "accuracy_files", "already_files", "rubbish_files",
    "version_files",
)

# kd_status 字段
_KD_COLS = (
    "id", "file_path", "table_id", "club_name", "pic_total",
    "normal_count", "normal_total", "except_count", "operation_rate",
    "untreated_count", "operation_count", "accuracy_count", "already_count",
    "rubbish_count", "error_rate", "device_code", "target_directory",
    "status", "normal_files", "except_files", "untreated_files",
    "operation_files", "accuracy_files", "already_files", "rubbish_files",
    "version_files",
)

# submission_log 字段（不含 id，MySQL 侧 AUTO_INCREMENT）
_SUB_COLS = (
    "id", "created_at", "device_code", "table_id", "club_name",
    "category", "file_name", "file_path_date", "collect_ok",
    "upload_zip", "upload_ok",
)

# device_mapping 字段
_DM_COLS = ("device_code", "local_dir", "source", "created_at", "updated_at")


def _push_replace(conn, table_name: str, columns: tuple, rows: list,
                  progress_cb=None) -> int:
    """全量覆盖推送：TRUNCATE + INSERT（适合数据量不大的表）"""
    if not rows:
        return 0
    cols_str = ", ".join(f"`{c}`" for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    sql = f"INSERT INTO `{table_name}` ({cols_str}) VALUES ({placeholders})"
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE `{table_name}`")
        cur.executemany(sql, rows)
    conn.commit()
    if progress_cb:
        progress_cb(f"{table_name}: {len(rows)} 条已推送")
    return len(rows)


def _push_upsert(conn, table_name: str, pk_col: str, columns: tuple,
                 rows: list, progress_cb=None) -> int:
    """增量推送：INSERT ... ON DUPLICATE KEY UPDATE（按主键去重）"""
    if not rows:
        return 0
    cols_str = ", ".join(f"`{c}`" for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    update_cols = [c for c in columns if c != pk_col]
    update_str = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in update_cols)
    sql = (f"INSERT INTO `{table_name}` ({cols_str}) VALUES ({placeholders}) "
           f"ON DUPLICATE KEY UPDATE {update_str}")
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()
    if progress_cb:
        progress_cb(f"{table_name}: {len(rows)} 条已推送")
    return len(rows)


def _push_insert_ignore(conn, table_name: str, columns: tuple,
                        rows: list, progress_cb=None) -> int:
    """追加推送：INSERT IGNORE（仅插入不存在的记录，适合 submission_log）"""
    if not rows:
        return 0
    cols_str = ", ".join(f"`{c}`" for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    sql = (f"INSERT IGNORE INTO `{table_name}` ({cols_str}) "
           f"VALUES ({placeholders})")
    with conn.cursor() as cur:
        affected = cur.executemany(sql, rows)
    conn.commit()
    if progress_cb:
        progress_cb(f"{table_name}: {affected} 条新增")
    return affected


# ==================== 对外 API ====================

def test_connection(cfg: dict = None) -> tuple:
    """测试 MySQL 连接是否可达

    Returns:
        (ok: bool, message: str)
    """
    pymysql = _get_pymysql()
    if pymysql is None:
        return False, "pymysql 未安装，请执行 pip install pymysql"
    try:
        # 先无 database 连接，测试基础连通性
        conn = _connect(cfg, use_database=False)
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            ver = cur.fetchone()
        conn.close()
        return True, f"连接成功，MySQL {ver.get('VERSION()', '')}"
    except Exception as e:
        return False, f"连接失败: {type(e).__name__}: {e}"


def ensure_schema(cfg: dict = None) -> tuple:
    """确保远程数据库和表结构就绪（幂等）

    Returns:
        (ok: bool, message: str)
    """
    try:
        if cfg is None:
            cfg = _load_mysql_config()
        if not cfg:
            return False, "MySQL 同步未启用或配置缺失"
        _ensure_schema(cfg)
        return True, "数据库与表结构已就绪"
    except Exception as e:
        return False, f"建库建表失败: {type(e).__name__}: {e}"


def push_all(progress_cb=None) -> tuple:
    """全量推送 5 张业务表到 MySQL

    Args:
        progress_cb: 可选回调 progress_cb(message: str)，每个阶段推送后通知

    Returns:
        (ok: bool, message: str, total_count: int)
    """
    cfg = _load_mysql_config()
    if not cfg:
        return False, "MySQL 同步未启用或配置缺失", 0
    # MySQL 主模式（enabled=true）下数据已直写远程库，本地 SQLite 停留在
    # 切换前陈旧快照；此时镜像推送只会 TRUNCATE 掉新鲜数据，必须跳过。
    if backend.is_mysql_test_mode():
        return True, "MySQL 主模式：数据已直写远程库，无需镜像推送", 0

    t0 = time.time()
    total = 0
    try:
        _ensure_schema(cfg)
        conn = _connect(cfg)

        # 1. billiard_tables — 全量覆盖
        rows = _read_sqlite("billiard_tables", _BT_COLS)
        total += _push_replace(conn, "billiard_tables", _BT_MYSQL_COLS,
                               rows, progress_cb)

        # 2. xqzg_status — 全量覆盖
        rows = _read_sqlite("xqzg_status", _XQZG_COLS)
        total += _push_replace(conn, "xqzg_status", _XQZG_COLS,
                               rows, progress_cb)

        # 3. kd_status — 全量覆盖
        rows = _read_sqlite("kd_status", _KD_COLS)
        total += _push_replace(conn, "kd_status", _KD_COLS,
                               rows, progress_cb)

        # 4. submission_log — 增量追加（按 id 去重）
        rows = _read_sqlite("submission_log", _SUB_COLS)
        total += _push_insert_ignore(conn, "submission_log", _SUB_COLS,
                                     rows, progress_cb)

        # 5. device_mapping — 全量覆盖
        rows = _read_sqlite("device_mapping", _DM_COLS)
        total += _push_replace(conn, "device_mapping", _DM_COLS,
                               rows, progress_cb)

        # 记录推送时间
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO `sync_meta` (`key`, value) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE value = VALUES(value)",
                ("last_push_time", now))
        conn.commit()

        elapsed = time.time() - t0
        conn.close()
        return True, f"推送完成，共 {total} 条，耗时 {elapsed:.1f}s", total

    except Exception as e:
        return False, f"推送失败: {type(e).__name__}: {e}", total


def push_table(table_name: str, progress_cb=None) -> tuple:
    """按表名单独推送

    Args:
        table_name: 表名（billiard_tables / xqzg_status / kd_status /
                    submission_log / device_mapping）
        progress_cb: 可选回调

    Returns:
        (ok: bool, message: str, count: int)
    """
    cfg = _load_mysql_config()
    if not cfg:
        return False, "MySQL 同步未启用或配置缺失", 0
    if backend.is_mysql_test_mode():
        return True, "MySQL 主模式：数据已直写远程库，无需镜像推送", 0

    _TABLE_MAP = {
        "billiard_tables": (_BT_COLS, _BT_MYSQL_COLS, "replace"),
        "xqzg_status": (_XQZG_COLS, _XQZG_COLS, "replace"),
        "kd_status": (_KD_COLS, _KD_COLS, "replace"),
        "submission_log": (_SUB_COLS, _SUB_COLS, "insert_ignore"),
        "device_mapping": (_DM_COLS, _DM_COLS, "replace"),
    }
    if table_name not in _TABLE_MAP:
        return False, f"不支持的表名: {table_name}", 0

    read_cols, mysql_cols, strategy = _TABLE_MAP[table_name]
    try:
        _ensure_schema(cfg)
        conn = _connect(cfg)
        rows = _read_sqlite(table_name, read_cols)

        if strategy == "replace":
            count = _push_replace(conn, table_name, mysql_cols, rows, progress_cb)
        else:
            count = _push_insert_ignore(conn, table_name, mysql_cols, rows, progress_cb)

        conn.close()
        return True, f"{table_name}: {count} 条已推送", count
    except Exception as e:
        return False, f"推送失败: {type(e).__name__}: {e}", 0


# 售后记录业务键：SQLite 与 MySQL 两侧 id 各自增长会撞车，
# 按 (created_at, creator, table_no, problem) 判定同一条记录
_AFTERSALE_KEY_COLS = ("created_at", "creator", "table_no", "problem")


def push_aftersale(cfg: dict = None, progress_cb=None) -> tuple:
    """售后记录安全推送：业务键去重 upsert（不 TRUNCATE 不按 id 去重）

    售后库是多用户共享，全量 replace 会清掉他人数据；SQLite 与 MySQL
    两侧 id 各自增长会撞车。按业务键判断：
    - 不存在 → INSERT（MySQL 侧 AUTO_INCREMENT 生成新 id）
    - 存在 → UPDATE 本地最新值（保留 MySQL 侧 id）
    本地删除不反向删除远程（单向推送语义）。

    Returns:
        (ok: bool, message: str, count: int)
    """
    try:
        if cfg is None:
            cfg = _load_mysql_config()
        if not cfg:
            return False, "MySQL 同步未启用或配置缺失", 0
        if backend.is_mysql_test_mode():
            return True, "MySQL 主模式：售后已直写远程库，无需推送", 0
        from database import aftersale_db
        _ensure_schema(cfg)
        conn = _connect(cfg)
        columns = aftersale_db.RECORD_FIELDS  # 不含 id（两侧 id 不通用）
        rows = _read_sqlite("aftersale_records", columns)
        cols_str = ", ".join(f"`{c}`" for c in columns)
        ph = ", ".join(["%s"] * len(columns))
        insert_sql = (f"INSERT INTO `aftersale_records` ({cols_str}) "
                      f"VALUES ({ph})")
        set_str = ", ".join(
            f"`{c}` = %s" for c in columns if c not in _AFTERSALE_KEY_COLS)
        update_sql = (f"UPDATE `aftersale_records` SET {set_str} WHERE "
                      + " AND ".join(f"`{c}` = %s" for c in _AFTERSALE_KEY_COLS))
        key_ph = " AND ".join(f"`{c}` = %s" for c in _AFTERSALE_KEY_COLS)
        sel_sql = (f"SELECT id FROM `aftersale_records` WHERE {key_ph} "
                   f"LIMIT 1")
        inserted = updated = 0
        with conn.cursor() as cur:
            for r in rows:
                d = dict(zip(columns, r))
                keys = [d[c] for c in _AFTERSALE_KEY_COLS]
                cur.execute(sel_sql, keys)
                exist = cur.fetchone()
                if exist:
                    vals = [d[c] for c in columns
                            if c not in _AFTERSALE_KEY_COLS] + keys
                    cur.execute(update_sql, vals)
                    updated += 1
                else:
                    cur.execute(insert_sql, [d[c] for c in columns])
                    inserted += 1
        conn.commit()
        conn.close()
        if progress_cb:
            progress_cb(f"aftersale_records: 新增 {inserted}，更新 {updated}")
        return True, f"售后记录已推送：新增 {inserted}，更新 {updated}", \
            inserted + updated
    except Exception as e:
        return False, f"推送失败: {type(e).__name__}: {e}", 0


def is_enabled() -> bool:
    """MySQL 同步是否已启用"""
    cfg = _load_mysql_config()
    return bool(cfg)


def get_last_push_time() -> str:
    """从 MySQL 读取上次推送时间；未推送过或连接失败返回空串"""
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT value FROM `sync_meta` WHERE `key` = %s",
                ("last_push_time",))
            row = cur.fetchone()
        conn.close()
        return row.get("value", "") if row else ""
    except Exception:
        return ""
