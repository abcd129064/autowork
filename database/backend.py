# -*- coding: utf-8 -*-
"""数据库后端切换层（测试模式：MySQL 完全替代本地 SQLite）

table_db.py 所有读写均通过此模块路由：
- MySQL 开关关闭 → sqlite3 本地连接（原有行为，零改动）
- MySQL 开关开启 → MysqlConnectionAdapter 包装的 pymysql 连接

适配层自动处理两套 SQL 方言差异，调用方无需感知：
- 占位符：SQLite ? → MySQL %s
- PRAGMA：MySQL 静默忽略
- executescript：MySQL 按分号拆条执行
- INSERT OR REPLACE：需调用方显式改写（见 table_db.py 内各 save 函数）
"""

import json
import os
import re
import threading


# ==================== MySQL 测试模式开关 ====================

def is_mysql_test_mode() -> bool:
    """MySQL 测试模式是否开启（settings.json → mysql_sync.enabled）"""
    return _load_mysql_settings().get("enabled", False)


def _load_mysql_settings() -> dict:
    """读取 settings.json 中 mysql_sync 配置节点（敏感字段透明解密）"""
    # 内联 app_dir 逻辑，避免导入 core 包时触发 PySide6 依赖链
    import sys as _sys
    if getattr(_sys, 'frozen', False):
        app_dir = os.path.dirname(_sys.executable)
    else:
        # backend.py 位于 database/，向上一级即项目根目录
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(app_dir, "settings.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    try:
        from core.secrets import decrypt_settings
        return decrypt_settings(raw).get("mysql_sync", {})
    except Exception:
        # core.secrets 导入失败（无 PySide6 等）时直接返回原始数据
        return raw.get("mysql_sync", {})


# ==================== SQL 语法转换 ====================

def convert_placeholders(sql: str) -> str:
    """SQLite 占位符 ? → MySQL %s（跳过字符串字面量内的 ?）"""
    result = []
    in_str = False
    i = 0
    while i < len(sql):
        c = sql[i]
        if c == "'" and not in_str:
            in_str = True
        elif c == "'" and in_str:
            in_str = False
        elif c == "?" and not in_str:
            result.append("%s")
            i += 1
            continue
        result.append(c)
        i += 1
    return "".join(result)


def convert_on_conflict(sql: str) -> str:
    """SQLite ON CONFLICT(col) DO UPDATE SET ... = excluded.xxx → MySQL ON DUPLICATE KEY UPDATE

    仅处理 table_db.set_device_mapping 使用的一种形态。
    """
    pattern = re.compile(
        r"ON CONFLICT\((\w+)\) DO UPDATE SET (.+)",
        re.IGNORECASE | re.DOTALL)
    m = pattern.search(sql)
    if not m:
        return sql
    assignments = m.group(2)
    mysql_assign = re.sub(
        r"(\w+)\s*=\s*excluded\.(\w+)",
        r"\1 = VALUES(\2)",
        assignments)
    return sql[:m.start()] + "ON DUPLICATE KEY UPDATE " + mysql_assign


def convert_insert_or_replace(sql: str) -> str:
    """INSERT OR REPLACE → INSERT（MySQL 无此语法，调用前已确保无主键冲突）"""
    return re.sub(r"INSERT\s+OR\s+REPLACE", "INSERT", sql, flags=re.IGNORECASE)


# ==================== MySQL 游标 / 连接适配器 ====================

class MysqlCursorAdapter:
    """包装 pymysql 游标，提供 sqlite3.Cursor 兼容接口"""

    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, params=None):
        sql = _convert_sql(sql)
        if params is not None:
            self._cur.execute(sql, params)
        else:
            self._cur.execute(sql)
        return self

    def executemany(self, sql, seq_params):
        sql = _convert_sql(sql)
        total = 0
        for p in seq_params:
            self._cur.execute(sql, p)
            if self._cur.rowcount:
                total += self._cur.rowcount
        self._rowcount = total
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def close(self):
        self._cur.close()

    @property
    def description(self):
        return self._cur.description

    @property
    def rowcount(self):
        rc = getattr(self, "_rowcount", None)
        if rc is not None:
            return rc
        return self._cur.rowcount if self._cur.rowcount is not None else 0

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    def __iter__(self):
        return iter(self._cur.fetchall())


def _convert_sql(sql: str) -> str:
    """统一应用所有 SQL 方言转换"""
    sql = convert_placeholders(sql)
    sql = convert_insert_or_replace(sql)
    sql = convert_on_conflict(sql)
    sql = _quote_reserved_words(sql)
    sql = _convert_sqlite_date_functions(sql)
    sql = _strip_collate_nocase(sql)
    return sql


def _convert_sqlite_date_functions(sql: str) -> str:
    """SQLite date() 函数 → MySQL DATE_SUB/DATE_FORMAT

    处理 query_kd_alerts 中的模式：
    replace(date(replace(col, '/', '-'), '-N days'), '-', '/')
    → DATE_FORMAT(DATE_SUB(STR_TO_DATE(REPLACE(col, '/', '-'), '%Y-%m-%d'), INTERVAL N DAY), '%Y/%m/%d')
    """
    # 匹配：replace(date(replace(X, '/', '-'), '-N days'), '-', '/')
    pattern = re.compile(
        r"replace\(date\(replace\((\w+),\s*'/',\s*'-'\),\s*'-(\d+)\s+days?'\),\s*'-',\s*'/'\)",
        re.IGNORECASE)

    def replacer(m):
        col = m.group(1)
        days = m.group(2)
        return (f"DATE_FORMAT(DATE_SUB(STR_TO_DATE(REPLACE({col}, '/', '-'), '%Y-%m-%d'), "
                f"INTERVAL {days} DAY), '%Y/%m/%d')")

    return pattern.sub(replacer, sql)


def _strip_collate_nocase(sql: str) -> str:
    """移除 SQLite 专有的 COLLATE NOCASE（MySQL utf8mb4 默认不区分大小写）"""
    return re.sub(r"\s+COLLATE\s+NOCASE", "", sql, flags=re.IGNORECASE)


def _quote_reserved_words(sql: str) -> str:
    """处理 MySQL 保留字：sync_meta.key / sync_meta.value 在列名位置时加反引号

    只处理 sync_meta 表的 key/value 列，避免误改其他上下文。
    """
    # WHERE key= → WHERE `key`=
    sql = re.sub(r"\bWHERE key\s*=", "WHERE `key` =", sql, flags=re.IGNORECASE)
    # WHERE key LIKE → WHERE `key` LIKE
    sql = re.sub(r"\bWHERE key\s+LIKE", "WHERE `key` LIKE", sql, flags=re.IGNORECASE)
    # SELECT key FROM → SELECT `key` FROM
    sql = re.sub(r"\bSELECT key\s+FROM", "SELECT `key` FROM", sql, flags=re.IGNORECASE)
    # (key, value) VALUES → (`key`, `value`) VALUES
    sql = re.sub(r"\(key,\s*value\)", "(`key`, `value`)", sql, flags=re.IGNORECASE)
    # DELETE FROM sync_meta WHERE key=  已涵盖在上面
    return sql


class MysqlConnectionAdapter:
    """模拟 sqlite3.Connection 接口的 MySQL 连接适配器"""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None) -> MysqlCursorAdapter:
        # SQLite 专有指令静默跳过
        stripped = sql.strip().upper()
        if stripped.startswith("PRAGMA"):
            return MysqlCursorAdapter(self._conn.cursor())
        sql = _convert_sql(sql)
        cur = self._conn.cursor()
        if params is not None:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        return MysqlCursorAdapter(cur)

    def executemany(self, sql, seq_params) -> MysqlCursorAdapter:
        sql = _convert_sql(sql)
        cur = self._conn.cursor()
        total = 0
        for p in seq_params:
            cur.execute(sql, p)
            if cur.rowcount:
                total += cur.rowcount
        adapter = MysqlCursorAdapter(cur)
        adapter._rowcount = total
        return adapter

    def executescript(self, script: str):
        """按分号拆条逐条执行（对应 SQLite executescript）"""
        for stmt in script.split(";"):
            stmt = stmt.strip()
            if stmt and not stmt.upper().startswith("PRAGMA"):
                self.execute(stmt)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def column_exists(self, table: str, column: str) -> bool:
        """检查列是否存在（替代 SQLite PRAGMA table_info）"""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
            "AND COLUMN_NAME = %s", (table, column))
        row = cur.fetchone()
        return bool(row and row[0] > 0)

    def table_exists(self, table: str) -> bool:
        """检查表是否存在"""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (table,))
        row = cur.fetchone()
        return bool(row and row[0] > 0)


# ==================== MySQL 连接工厂 ====================

def create_mysql_connection():
    """创建 MySQL 连接适配器；pymysql 未安装时抛 RuntimeError"""
    try:
        import pymysql
    except ImportError:
        raise RuntimeError("pymysql 未安装，请执行 pip install pymysql")
    cfg = _load_mysql_settings()
    if not cfg:
        raise RuntimeError("MySQL 配置缺失，请先在管理设置中配置")
    conn = pymysql.connect(
        host=cfg.get("host", "127.0.0.1"),
        port=int(cfg.get("port", 3306)),
        user=cfg.get("user", "root"),
        password=cfg.get("password", ""),
        database=cfg.get("database", "autowork"),
        charset="utf8mb4",
        connect_timeout=10,
        # autocommit=True 是关键：QThread 结束后 thread-local 连接被丢弃，
        # 若 autocommit=False 会留下未提交事务，持续持有表元数据锁，
        # 导致后续 DDL（CREATE/TRUNCATE）与查询全部排队卡死。
        # 自动提交后丢弃的连接不再持锁，彻底消除级联阻塞。
        autocommit=True,
        # 读写超时保护：网络抖动时快速失败而非无限挂起
        read_timeout=60,
        write_timeout=60,
    )
    return MysqlConnectionAdapter(conn)


# ==================== MySQL 建表 DDL ====================

# 与 table_db.py 中 SQLite DDL 一一对应（IF NOT EXISTS 幂等，无 ALTER 需求）
MYSQL_DDL = {
    "billiard_tables": """
        CREATE TABLE IF NOT EXISTS billiard_tables (
            id               INT PRIMARY KEY,
            name             VARCHAR(255) DEFAULT '',
            roomName         VARCHAR(255) DEFAULT '',
            onlineStatusName VARCHAR(255) DEFAULT '',
            remark           TEXT,
            cameraPassExt    VARCHAR(512) DEFAULT '',
            snk_code         VARCHAR(128) DEFAULT '',
            code             VARCHAR(255) DEFAULT '',
            city             VARCHAR(255) DEFAULT ''
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    "sync_meta": """
        CREATE TABLE IF NOT EXISTS sync_meta (
            `key`  VARCHAR(128) PRIMARY KEY,
            value  TEXT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    "xqzg_status": """
        CREATE TABLE IF NOT EXISTS xqzg_status (
            id               INT PRIMARY KEY,
            file_path        VARCHAR(64) DEFAULT '',
            table_id         VARCHAR(255) DEFAULT '',
            club_name        VARCHAR(255) DEFAULT '',
            pic_total        VARCHAR(64) DEFAULT '',
            normal_count     VARCHAR(64) DEFAULT '',
            normal_total     VARCHAR(64) DEFAULT '',
            except_count     VARCHAR(64) DEFAULT '',
            operation_rate   VARCHAR(64) DEFAULT '',
            untreated_count  VARCHAR(64) DEFAULT '',
            operation_count  VARCHAR(64) DEFAULT '',
            accuracy_count   VARCHAR(64) DEFAULT '',
            already_count    VARCHAR(64) DEFAULT '',
            rubbish_count    VARCHAR(64) DEFAULT '',
            error_rate       VARCHAR(64) DEFAULT '',
            device_code      VARCHAR(255) DEFAULT '',
            target_directory VARCHAR(512) DEFAULT '',
            status           VARCHAR(32) DEFAULT '',
            normal_files     LONGTEXT,
            except_files     LONGTEXT,
            untreated_files  LONGTEXT,
            operation_files  LONGTEXT,
            accuracy_files   LONGTEXT,
            already_files    LONGTEXT,
            rubbish_files    LONGTEXT,
            version_files    LONGTEXT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    "kd_status": """
        CREATE TABLE IF NOT EXISTS kd_status (
            id               INT PRIMARY KEY,
            file_path        VARCHAR(64) DEFAULT '',
            table_id         VARCHAR(255) DEFAULT '',
            club_name        VARCHAR(255) DEFAULT '',
            pic_total        VARCHAR(64) DEFAULT '',
            normal_count     VARCHAR(64) DEFAULT '',
            normal_total     VARCHAR(64) DEFAULT '',
            except_count     VARCHAR(64) DEFAULT '',
            operation_rate   VARCHAR(64) DEFAULT '',
            untreated_count  VARCHAR(64) DEFAULT '',
            operation_count  VARCHAR(64) DEFAULT '',
            accuracy_count   VARCHAR(64) DEFAULT '',
            already_count    VARCHAR(64) DEFAULT '',
            rubbish_count    VARCHAR(64) DEFAULT '',
            error_rate       VARCHAR(64) DEFAULT '',
            device_code      VARCHAR(255) DEFAULT '',
            target_directory VARCHAR(512) DEFAULT '',
            status           VARCHAR(32) DEFAULT '',
            normal_files     LONGTEXT,
            except_files     LONGTEXT,
            untreated_files  LONGTEXT,
            operation_files  LONGTEXT,
            accuracy_files   LONGTEXT,
            already_files    LONGTEXT,
            rubbish_files    LONGTEXT,
            version_files    LONGTEXT,
            INDEX idx_kd_file_path (file_path),
            INDEX idx_kd_device_code (device_code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    "submission_log": """
        CREATE TABLE IF NOT EXISTS submission_log (
            id             INT AUTO_INCREMENT PRIMARY KEY,
            created_at     VARCHAR(32) DEFAULT '',
            device_code    VARCHAR(255) DEFAULT '',
            table_id       VARCHAR(255) DEFAULT '',
            club_name      VARCHAR(255) DEFAULT '',
            category       VARCHAR(64) DEFAULT '',
            file_name      VARCHAR(512) DEFAULT '',
            file_path_date VARCHAR(64) DEFAULT '',
            collect_ok     TINYINT DEFAULT 0,
            upload_zip     VARCHAR(512),
            upload_ok      TINYINT,
            INDEX idx_sub_device_time (device_code, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    "device_mapping": """
        CREATE TABLE IF NOT EXISTS device_mapping (
            device_code VARCHAR(255) PRIMARY KEY,
            local_dir   VARCHAR(512) DEFAULT '',
            source      VARCHAR(32) DEFAULT 'auto',
            created_at  VARCHAR(32) DEFAULT '',
            updated_at  VARCHAR(32) DEFAULT ''
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    "health_alerts": """
        CREATE TABLE IF NOT EXISTS health_alerts (
            name             VARCHAR(255) PRIMARY KEY,
            roomName         VARCHAR(255) DEFAULT '',
            onlineStatusName VARCHAR(255) DEFAULT '',
            health           DOUBLE DEFAULT 0,
            resolved_health  DOUBLE,
            updated_at       VARCHAR(32) DEFAULT ''
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    "aftersale_records": """
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
    """,
}


# ==================== 后端状态机（MySQL 主 + SQLite 兜底）====================

STATE_ONLINE = "ONLINE"      # MySQL 可用，读写走 MySQL
STATE_DEGRADED = "DEGRADED"  # MySQL 不可用，回退本地 SQLite 兜底

_state = STATE_ONLINE
_state_lock = threading.Lock()


def get_state() -> str:
    """当前后端状态：ONLINE=MySQL 主库，DEGRADED=SQLite 兜底"""
    with _state_lock:
        return _state


def mark_degraded() -> bool:
    """标记降级（MySQL 不可用）。返回是否发生状态切换"""
    global _state
    with _state_lock:
        if _state == STATE_DEGRADED:
            return False
        _state = STATE_DEGRADED
    _log_state_change(STATE_DEGRADED)
    return True


def mark_online() -> bool:
    """标记在线（MySQL 恢复）。返回是否发生状态切换"""
    global _state
    with _state_lock:
        if _state == STATE_ONLINE:
            return False
        _state = STATE_ONLINE
    _log_state_change(STATE_ONLINE)
    return True


def _log_state_change(new_state: str):
    """状态切换记连接日志（best-effort，绝不阻塞业务）"""
    try:
        from core.conn_logger import conn_logger
        if new_state == STATE_DEGRADED:
            conn_logger.error("backend_state",
                              "MySQL 不可用，已回退本地 SQLite 兜底")
        else:
            conn_logger.info("backend_state", "MySQL 已恢复在线")
    except Exception:
        pass
