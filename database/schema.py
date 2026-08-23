# -*- coding: utf-8 -*-
"""数据库表结构单一来源（SQLite / MySQL 双方言 DDL 生成）

背景
----
此前 SQLite DDL（database/table_db.py 的 ``_CREATE_*`` 常量）与 MySQL DDL
（database/backend.py 的 ``MYSQL_DDL``、以及已下线的 database/mysql_sync.py
镜像推送 DDL）三处手工重复维护，曾出现 xqzg_status 缺 file_path 列等漂移。
本模块将全部 8 张表的列元数据收敛为单一来源，并提供两方言 DDL 生成器：

- ``TABLE_COLUMNS``：列定义（列名、SQLite 类型、MySQL 类型、两方言默认值、
  两方言附加子句如 PRIMARY KEY / AUTO_INCREMENT）
- ``TABLE_INDEXES``：索引定义（两方言独立描述——名称、列集合可不同）
- ``MIGRATIONS``：迁移注册表（旧库补列的列级元数据，两方言共用同一列集合，
  ``sqlite_alter_sql`` / ``mysql_alter_sql`` 生成 ALTER TABLE ADD COLUMN）
- ``to_sqlite_ddl(table)``：生成 SQLite 方言 DDL（CREATE TABLE ... + 独立
  CREATE INDEX）
- ``to_mysql_ddl(table)``：生成 MySQL 方言 DDL（CREATE TABLE ... 内联
  INDEX ... + ENGINE/CHARSET 表选项）

方言转换已支持清单（与 database/backend.py ``_convert_sql`` 的转换点一致）
--------------------------------------------------------------------------
运行期 SQL（非 DDL）从 SQLite 语法到 MySQL 的转换由 backend 适配层完成，
已支持：
- 占位符 ``?`` → ``%s``（convert_placeholders）
- ``INSERT OR REPLACE`` → ``INSERT``（convert_insert_or_replace；MySQL 无此
  语法，调用方需先确保无主键冲突或改用 ON DUPLICATE KEY UPDATE）
- ``ON CONFLICT(col) DO UPDATE SET ... = excluded.x`` → ``ON DUPLICATE KEY
  UPDATE ... = VALUES(x)``（convert_on_conflict）
- ``date()`` 运算 → ``DATE_FORMAT/DATE_SUB/STR_TO_DATE``
  （_convert_sqlite_date_functions）
- ``COLLATE NOCASE`` 剥离（_strip_collate_nocase；MySQL utf8mb4 默认
  不区分大小写）
- 保留字 ``sync_meta.key/value`` 加反引号（_quote_reserved_words）

⚠️ 新增任何 SQLite 专有 SQL（如新的内置函数、新的冲突子句）前，必须同步
扩展 backend 转换器（database/backend.py ``_convert_sql`` 管线），否则
MySQL 主库模式（enabled=true）下该语句会原样下发导致执行失败。

DDL 生成约定
------------
- 类型映射显式：INTEGER→INT、TEXT→VARCHAR/TEXT/LONGTEXT、
  REAL→DOUBLE、TINYINT；AUTO_INCREMENT 仅 MySQL（submission_log /
  aftersale_records 的 id），SQLite 侧保持 INTEGER PRIMARY KEY rowid 语义。
- 不做类型优化（用户决策 4）：保持与历史 DDL 逐字节语义等价。
- 表选项固定 ``ENGINE=InnoDB DEFAULT CHARSET=utf8mb4``。
"""

from dataclasses import dataclass
from typing import List, Tuple

# 表名清单：与 backend.MYSQL_DDL 历史键序一致（billiard_tables 在前，
# 保证 ``for ddl in backend.MYSQL_DDL.values()`` 的执行顺序不变）
TABLE_NAMES: List[str] = [
    "billiard_tables",
    "sync_meta",
    "xqzg_status",
    "kd_status",
    "submission_log",
    "device_mapping",
    "health_alerts",
    "aftersale_records",
    "ledger_records",
]

# MySQL 保留字列：生成 MySQL DDL 时列名需加反引号（与 backend 转换器
# _quote_reserved_words 只处理 sync_meta key/value 的口径一致）
_MYSQL_RESERVED_COLUMNS = frozenset({"key"})

# MySQL 表选项（所有表统一）
_MYSQL_TABLE_OPTIONS = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"


@dataclass(frozen=True)
class ColumnDef:
    """单列定义（两方言独立描述）

    Attributes:
        name: 列名
        sqlite_type: SQLite 类型（INTEGER / TEXT / REAL）
        mysql_type: MySQL 类型（INT / VARCHAR(n) / TEXT / LONGTEXT /
            TINYINT / DOUBLE）
        sqlite_default: SQLite DEFAULT 子句的 SQL 字面量（'' / '[]' /
            '否' / 0 等）；None 表示不写 DEFAULT
        mysql_default: MySQL DEFAULT 子句的 SQL 字面量；None 表示不写
            DEFAULT（MySQL 下 TEXT/LONGTEXT 列不允许字面量默认值）
        sqlite_extra: SQLite 附加子句（如 PRIMARY KEY）
        mysql_extra: MySQL 附加子句（如 PRIMARY KEY / AUTO_INCREMENT
            PRIMARY KEY）
    """

    name: str
    sqlite_type: str
    mysql_type: str
    sqlite_default: str | None = None
    mysql_default: str | None = None
    sqlite_extra: str = ""
    mysql_extra: str = ""


@dataclass(frozen=True)
class IndexDef:
    """索引定义（两方言独立描述；某方言无此索引时对应字段留空）

    Attributes:
        sqlite_name: SQLite 索引名；空串表示 SQLite 侧无此索引
        sqlite_cols: SQLite 索引列（顺序敏感）
        mysql_name: MySQL 索引名；空串表示 MySQL 侧无此索引
        mysql_cols: MySQL 索引列（顺序敏感）
    """

    sqlite_name: str
    sqlite_cols: Tuple[str, ...] = ()
    mysql_name: str = ""
    mysql_cols: Tuple[str, ...] = ()


# ==================== 列元数据（单一来源） ====================

TABLE_COLUMNS = {
    "billiard_tables": [
        ColumnDef("id", "INTEGER", "INT", sqlite_extra="PRIMARY KEY",
                  mysql_extra="PRIMARY KEY"),
        ColumnDef("name", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("roomName", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("onlineStatusName", "TEXT", "VARCHAR(255)", "''", "''"),
        # MySQL remark 为 TEXT 不允许 DEFAULT 子句，与历史 DDL 一致
        ColumnDef("remark", "TEXT", "TEXT", "''", None),
        ColumnDef("cameraPassExt", "TEXT", "VARCHAR(512)", "''", "''"),
        ColumnDef("snk_code", "TEXT", "VARCHAR(128)", "''", "''"),
        ColumnDef("code", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("city", "TEXT", "VARCHAR(255)", "''", "''"),
    ],
    "sync_meta": [
        ColumnDef("key", "TEXT", "VARCHAR(128)", sqlite_extra="PRIMARY KEY",
                  mysql_extra="PRIMARY KEY"),
        ColumnDef("value", "TEXT", "TEXT"),
    ],
    "xqzg_status": [
        ColumnDef("id", "INTEGER", "INT", sqlite_extra="PRIMARY KEY",
                  mysql_extra="PRIMARY KEY"),
        ColumnDef("file_path", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("table_id", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("club_name", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("pic_total", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("normal_count", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("normal_total", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("except_count", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("operation_rate", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("untreated_count", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("operation_count", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("accuracy_count", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("already_count", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("rubbish_count", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("error_rate", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("device_code", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("target_directory", "TEXT", "VARCHAR(512)", "''", "''"),
        ColumnDef("status", "TEXT", "VARCHAR(32)", "''", "''"),
        # 8 类文件清单存 JSON：SQLite TEXT 带默认 '[]'；MySQL LONGTEXT
        # 不允许 DEFAULT 子句（读取端 json.loads(None) 兼容）
        ColumnDef("normal_files", "TEXT", "LONGTEXT", "'[]'", None),
        ColumnDef("except_files", "TEXT", "LONGTEXT", "'[]'", None),
        ColumnDef("untreated_files", "TEXT", "LONGTEXT", "'[]'", None),
        ColumnDef("operation_files", "TEXT", "LONGTEXT", "'[]'", None),
        ColumnDef("accuracy_files", "TEXT", "LONGTEXT", "'[]'", None),
        ColumnDef("already_files", "TEXT", "LONGTEXT", "'[]'", None),
        ColumnDef("rubbish_files", "TEXT", "LONGTEXT", "'[]'", None),
        ColumnDef("version_files", "TEXT", "LONGTEXT", "'[]'", None),
    ],
    "kd_status": [
        ColumnDef("id", "INTEGER", "INT", sqlite_extra="PRIMARY KEY",
                  mysql_extra="PRIMARY KEY"),
        ColumnDef("file_path", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("table_id", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("club_name", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("pic_total", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("normal_count", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("normal_total", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("except_count", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("operation_rate", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("untreated_count", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("operation_count", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("accuracy_count", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("already_count", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("rubbish_count", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("error_rate", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("device_code", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("target_directory", "TEXT", "VARCHAR(512)", "''", "''"),
        ColumnDef("status", "TEXT", "VARCHAR(32)", "''", "''"),
        ColumnDef("normal_files", "TEXT", "LONGTEXT", "'[]'", None),
        ColumnDef("except_files", "TEXT", "LONGTEXT", "'[]'", None),
        ColumnDef("untreated_files", "TEXT", "LONGTEXT", "'[]'", None),
        ColumnDef("operation_files", "TEXT", "LONGTEXT", "'[]'", None),
        ColumnDef("accuracy_files", "TEXT", "LONGTEXT", "'[]'", None),
        ColumnDef("already_files", "TEXT", "LONGTEXT", "'[]'", None),
        ColumnDef("rubbish_files", "TEXT", "LONGTEXT", "'[]'", None),
        ColumnDef("version_files", "TEXT", "LONGTEXT", "'[]'", None),
    ],
    "submission_log": [
        ColumnDef("id", "INTEGER", "INT", sqlite_extra="PRIMARY KEY",
                  mysql_extra="AUTO_INCREMENT PRIMARY KEY"),
        ColumnDef("created_at", "TEXT", "VARCHAR(32)", "''", "''"),
        ColumnDef("device_code", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("table_id", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("club_name", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("category", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("file_name", "TEXT", "VARCHAR(512)", "''", "''"),
        ColumnDef("file_path_date", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("collect_ok", "INTEGER", "TINYINT", "0", "0"),
        ColumnDef("upload_zip", "TEXT", "VARCHAR(512)"),
        ColumnDef("upload_ok", "INTEGER", "TINYINT"),
    ],
    "device_mapping": [
        ColumnDef("device_code", "TEXT", "VARCHAR(255)",
                  sqlite_extra="PRIMARY KEY", mysql_extra="PRIMARY KEY"),
        ColumnDef("local_dir", "TEXT", "VARCHAR(512)", "''", "''"),
        ColumnDef("source", "TEXT", "VARCHAR(32)", "'auto'", "'auto'"),
        ColumnDef("created_at", "TEXT", "VARCHAR(32)", "''", "''"),
        ColumnDef("updated_at", "TEXT", "VARCHAR(32)", "''", "''"),
    ],
    "health_alerts": [
        ColumnDef("name", "TEXT", "VARCHAR(255)",
                  sqlite_extra="PRIMARY KEY", mysql_extra="PRIMARY KEY"),
        ColumnDef("roomName", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("onlineStatusName", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("health", "REAL", "DOUBLE", "0", "0"),
        ColumnDef("resolved_health", "REAL", "DOUBLE"),
        # device_code：xqzg update_health 接口入参（billiard_tables.code），
        # 点击「已处理」时按此码调接口将服务端健康度重置为 4000
        ColumnDef("device_code", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("updated_at", "TEXT", "VARCHAR(32)", "''", "''"),
    ],
    "aftersale_records": [
        ColumnDef("id", "INTEGER", "INT", sqlite_extra="PRIMARY KEY",
                  mysql_extra="AUTO_INCREMENT PRIMARY KEY"),
        ColumnDef("created_at", "TEXT", "VARCHAR(32)", "''", "''"),
        ColumnDef("occurred_at", "TEXT", "VARCHAR(32)", "''", "''"),
        ColumnDef("creator", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("issue_type", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("table_no", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("room_name", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("region", "TEXT", "VARCHAR(255)", "''", "''"),
        # problem/cause/solution：SQLite TEXT 带 DEFAULT ''；MySQL TEXT
        # 不允许 DEFAULT 子句，与历史 DDL 一致
        ColumnDef("problem", "TEXT", "TEXT", "''", None),
        ColumnDef("cause", "TEXT", "TEXT", "''", None),
        ColumnDef("resolved", "TEXT", "VARCHAR(255)", "'否'", "'否'"),
        ColumnDef("is_initiative", "TEXT", "VARCHAR(255)", "'否'", "'否'"),
        ColumnDef("is_our_problem", "TEXT", "VARCHAR(255)", "'是'", "'是'"),
        ColumnDef("solution", "TEXT", "TEXT", "''", None),
        ColumnDef("resolver", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("response_time", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("snk_code", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("device_code", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("cycle_start", "TEXT", "VARCHAR(32)", "''", "''"),
        ColumnDef("updated_at", "TEXT", "VARCHAR(32)", "''", "''"),
    ],
    # 跑视频记录（跑视频面板，双后端）：字段来源 在线模板.xlsx 的
    # 问题/未复现/精度/使用 四个数据 sheet（sheet 名即 category 分类；
    # 精度/使用 多一列「复现」）。description/repro/remark 为长文本，
    # MySQL TEXT 不允许 DEFAULT 子句（与 aftersale_records 口径一致）。
    "ledger_records": [
        ColumnDef("id", "INTEGER", "INT", sqlite_extra="PRIMARY KEY",
                  mysql_extra="AUTO_INCREMENT PRIMARY KEY"),
        ColumnDef("category", "TEXT", "VARCHAR(32)", "''", "''"),
        ColumnDef("kind", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("room_name", "TEXT", "VARCHAR(255)", "''", "''"),
        ColumnDef("video_name", "TEXT", "VARCHAR(512)", "''", "''"),
        ColumnDef("frame", "TEXT", "VARCHAR(32)", "''", "''"),
        ColumnDef("description", "TEXT", "TEXT", "''", None),
        ColumnDef("repro", "TEXT", "TEXT", "''", None),
        ColumnDef("new_program", "TEXT", "VARCHAR(32)", "''", "''"),
        ColumnDef("remark", "TEXT", "TEXT", "''", None),
        ColumnDef("signer", "TEXT", "VARCHAR(64)", "''", "''"),
        ColumnDef("created_at", "TEXT", "VARCHAR(32)", "''", "''"),
        ColumnDef("updated_at", "TEXT", "VARCHAR(32)", "''", "''"),
    ],
}

# ==================== 索引元数据（单一来源） ====================

TABLE_INDEXES = {
    "kd_status": [
        # SQLite 覆盖索引 (file_path, id)：日期分区 + 默认 id 排序的分页
        # 查询无需回表；MySQL 侧为单列索引（历史 DDL 保持一致）
        IndexDef("idx_kd_status_path_id", ("file_path", "id"),
                 "idx_kd_file_path", ("file_path",)),
        # MySQL 侧额外的 device_code 索引；SQLite 无此索引
        IndexDef("", (), "idx_kd_device_code", ("device_code",)),
    ],
    "submission_log": [
        IndexDef("idx_submission_device_time", ("device_code", "created_at"),
                 "idx_sub_device_time", ("device_code", "created_at")),
    ],
    "aftersale_records": [
        # SQLite 覆盖索引含 id；MySQL 侧仅为 cycle_start（历史 DDL 保持一致）
        IndexDef("idx_aftersale_cycle", ("cycle_start", "id"),
                 "idx_aftersale_cycle", ("cycle_start",)),
        IndexDef("idx_aftersale_table_no", ("table_no",),
                 "idx_aftersale_table_no", ("table_no",)),
    ],
    "ledger_records": [
        # 分类筛选与署名统计（模板「计数」sheet 按署名汇总四分类）
        IndexDef("idx_ledger_category", ("category", "id"),
                 "idx_ledger_category", ("category",)),
        IndexDef("idx_ledger_signer", ("signer",),
                 "idx_ledger_signer", ("signer",)),
    ],
}

# ==================== 迁移注册表（列级元数据单一来源） ====================

# 历史迁移（table_db._ensure_initialized SQLite 侧 / _ensure_mysql_tables
# MySQL 侧）把「旧库补列」的列级元数据散落在两处 ALTER/DROP 块里。此处按
# 表按列统一登记：SQLite 侧与 MySQL 侧共用同一列集合，各自方言的
# ALTER TABLE ADD COLUMN 由 sqlite_alter_sql/mysql_alter_sql 生成。
#
# 部署约定不变（红线）：SQLite 模式由 _ensure_initialized 自动迁移；
# MySQL 模式不自动 DDL（表结构上线时人工 ALTER），_ensure_mysql_tables
# 的补列逻辑仅作幂等兜底，不影响「上线手动 ALTER」的既有流程。
#
# 注意：非「简单补列」的迁移不在此登记——billiard_tables 缺 name 的
# DROP 重建、xqzg_fts 缺列时删除由 _setup_fts 重建等结构性修复仍由
# table_db 迁移函数内联处理。
@dataclass(frozen=True)
class ColumnMigration:
    """列级迁移注册条目（两方言共用同一列，各带方言类型/默认值）

    Attributes:
        table: 表名
        col: 列名
        sqlite_type: SQLite ADD COLUMN 类型（TEXT / INTEGER / REAL）
        sqlite_default: SQLite DEFAULT 字面量；None 表示不写 DEFAULT
        mysql_type: MySQL ADD COLUMN 类型（VARCHAR(n) / LONGTEXT /
            TINYINT / DOUBLE）
        mysql_default: MySQL DEFAULT 字面量；None 表示不写 DEFAULT
            （MySQL TEXT/LONGTEXT 列不允许 DEFAULT 子句）
    """

    table: str
    col: str
    sqlite_type: str
    sqlite_default: str | None
    mysql_type: str
    mysql_default: str | None


# 按表登记：列顺序与历史迁移执行顺序一致（KD_EXTRA_FIELDS 在前、
# file_path 在后，保证旧库补列后的最终列集合与历史一致）。
MIGRATIONS: dict = {
    "billiard_tables": [
        ColumnMigration("billiard_tables", "snk_code", "TEXT", "''",
                        "VARCHAR(128)", "''"),
        ColumnMigration("billiard_tables", "code", "TEXT", "''",
                        "VARCHAR(255)", "''"),
        ColumnMigration("billiard_tables", "city", "TEXT", "''",
                        "VARCHAR(255)", "''"),
    ],
    "health_alerts": [
        # 旧库补 device_code（xqzg update_health 接口入参，见 TABLE_COLUMNS）
        ColumnMigration("health_alerts", "device_code", "TEXT", "''",
                        "VARCHAR(255)", "''"),
    ],
    "aftersale_records": [
        ColumnMigration("aftersale_records", "is_initiative", "TEXT", "'否'",
                        "VARCHAR(255)", "'否'"),
        ColumnMigration("aftersale_records", "is_our_problem", "TEXT", "'是'",
                        "VARCHAR(255)", "'是'"),
        ColumnMigration("aftersale_records", "occurred_at", "TEXT", "''",
                        "VARCHAR(32)", "''"),
        ColumnMigration("aftersale_records", "updated_at", "TEXT", "''",
                        "VARCHAR(32)", "''"),
    ],
    "xqzg_status": [
        ColumnMigration("xqzg_status", "device_code", "TEXT", "''",
                        "VARCHAR(255)", "''"),
        ColumnMigration("xqzg_status", "target_directory", "TEXT", "''",
                        "VARCHAR(512)", "''"),
        ColumnMigration("xqzg_status", "status", "TEXT", "''",
                        "VARCHAR(32)", "''"),
        # 8 类文件清单 JSON：SQLite TEXT 默认 '[]'；MySQL LONGTEXT 无默认
        ColumnMigration("xqzg_status", "normal_files", "TEXT", "'[]'",
                        "LONGTEXT", None),
        ColumnMigration("xqzg_status", "except_files", "TEXT", "'[]'",
                        "LONGTEXT", None),
        ColumnMigration("xqzg_status", "untreated_files", "TEXT", "'[]'",
                        "LONGTEXT", None),
        ColumnMigration("xqzg_status", "operation_files", "TEXT", "'[]'",
                        "LONGTEXT", None),
        ColumnMigration("xqzg_status", "accuracy_files", "TEXT", "'[]'",
                        "LONGTEXT", None),
        ColumnMigration("xqzg_status", "already_files", "TEXT", "'[]'",
                        "LONGTEXT", None),
        ColumnMigration("xqzg_status", "rubbish_files", "TEXT", "'[]'",
                        "LONGTEXT", None),
        ColumnMigration("xqzg_status", "version_files", "TEXT", "'[]'",
                        "LONGTEXT", None),
        ColumnMigration("xqzg_status", "file_path", "TEXT", "''",
                        "VARCHAR(64)", "''"),
    ],
    "kd_status": [
        ColumnMigration("kd_status", "device_code", "TEXT", "''",
                        "VARCHAR(255)", "''"),
        ColumnMigration("kd_status", "target_directory", "TEXT", "''",
                        "VARCHAR(512)", "''"),
        ColumnMigration("kd_status", "status", "TEXT", "''",
                        "VARCHAR(32)", "''"),
        ColumnMigration("kd_status", "normal_files", "TEXT", "'[]'",
                        "LONGTEXT", None),
        ColumnMigration("kd_status", "except_files", "TEXT", "'[]'",
                        "LONGTEXT", None),
        ColumnMigration("kd_status", "untreated_files", "TEXT", "'[]'",
                        "LONGTEXT", None),
        ColumnMigration("kd_status", "operation_files", "TEXT", "'[]'",
                        "LONGTEXT", None),
        ColumnMigration("kd_status", "accuracy_files", "TEXT", "'[]'",
                        "LONGTEXT", None),
        ColumnMigration("kd_status", "already_files", "TEXT", "'[]'",
                        "LONGTEXT", None),
        ColumnMigration("kd_status", "rubbish_files", "TEXT", "'[]'",
                        "LONGTEXT", None),
        ColumnMigration("kd_status", "version_files", "TEXT", "'[]'",
                        "LONGTEXT", None),
        ColumnMigration("kd_status", "file_path", "TEXT", "''",
                        "VARCHAR(64)", "''"),
    ],
}


def sqlite_alter_sql(m: ColumnMigration) -> str:
    """生成 SQLite 方言 ALTER TABLE ADD COLUMN"""
    ddl = f"ALTER TABLE {m.table} ADD COLUMN {m.col} {m.sqlite_type}"
    if m.sqlite_default is not None:
        ddl += f" DEFAULT {m.sqlite_default}"
    return ddl


def mysql_alter_sql(m: ColumnMigration) -> str:
    """生成 MySQL 方言 ALTER TABLE ADD COLUMN"""
    ddl = f"ALTER TABLE {m.table} ADD COLUMN {_quote_mysql_name(m.col)} {m.mysql_type}"
    if m.mysql_default is not None:
        ddl += f" DEFAULT {m.mysql_default}"
    return ddl


def sqlite_alter_for(table: str, col: str) -> str:
    """按表+列名取 SQLite ALTER SQL（迁移函数内特殊补列场景用）"""
    for m in MIGRATIONS.get(table, []):
        if m.col == col:
            return sqlite_alter_sql(m)
    raise KeyError(f"迁移注册表未登记列: {table}.{col}")


def mysql_alter_for(table: str, col: str) -> str:
    """按表+列名取 MySQL ALTER SQL（迁移函数内特殊补列场景用）"""
    for m in MIGRATIONS.get(table, []):
        if m.col == col:
            return mysql_alter_sql(m)
    raise KeyError(f"迁移注册表未登记列: {table}.{col}")


# ==================== DDL 生成 ====================

def _quote_mysql_name(name: str) -> str:
    """MySQL 列名加反引号（仅保留字列，如 sync_meta.key）"""
    return f"`{name}`" if name in _MYSQL_RESERVED_COLUMNS else name


def _render_sqlite_column(col: ColumnDef) -> str:
    """渲染 SQLite 单列定义"""
    parts = [col.name, col.sqlite_type]
    if col.sqlite_default is not None:
        parts.append(f"DEFAULT {col.sqlite_default}")
    if col.sqlite_extra:
        parts.append(col.sqlite_extra)
    return " ".join(parts)


def _render_mysql_column(col: ColumnDef) -> str:
    """渲染 MySQL 单列定义"""
    parts = [_quote_mysql_name(col.name), col.mysql_type]
    if col.mysql_default is not None:
        parts.append(f"DEFAULT {col.mysql_default}")
    if col.mysql_extra:
        parts.append(col.mysql_extra)
    return " ".join(parts)


def to_sqlite_ddl(table: str) -> str:
    """生成指定表的 SQLite 方言 DDL

    CREATE TABLE IF NOT EXISTS ...（幂等）；索引以独立
    CREATE INDEX IF NOT EXISTS 语句追加（与 table_db 历史常量一致）。
    """
    if table not in TABLE_COLUMNS:
        raise KeyError(f"未知表名: {table}")
    cols = [_render_sqlite_column(c) for c in TABLE_COLUMNS[table]]
    col_block = ",\n".join(f"    {c}" for c in cols)
    ddl = f"CREATE TABLE IF NOT EXISTS {table} (\n{col_block}\n);"
    for idx in TABLE_INDEXES.get(table, []):
        if idx.sqlite_name and idx.sqlite_cols:
            ddl += (f"\nCREATE INDEX IF NOT EXISTS {idx.sqlite_name} "
                    f"ON {table}({', '.join(idx.sqlite_cols)});")
    return ddl


def to_mysql_ddl(table: str) -> str:
    """生成指定表的 MySQL 方言 DDL

    CREATE TABLE IF NOT EXISTS ...（幂等）；索引内联为 INDEX 行；
    表尾追加 ENGINE/CHARSET 表选项。MySQL 表不允许 DEFAULT 子句的
    TEXT/LONGTEXT 列保持无 DEFAULT（由 ColumnDef.mysql_default=None 表达）。
    """
    if table not in TABLE_COLUMNS:
        raise KeyError(f"未知表名: {table}")
    lines = [f"    {_render_mysql_column(c)}" for c in TABLE_COLUMNS[table]]
    # 索引内联为表内 INDEX 行（与历史 MYSQL_DDL 布局一致：列尾 + 索引行
    # 统一用 ",\\n" 分隔，无需手动补列尾逗号，避免双逗号）
    for idx in TABLE_INDEXES.get(table, []):
        if idx.mysql_name and idx.mysql_cols:
            lines.append(f"    INDEX {idx.mysql_name} "
                         f"({', '.join(idx.mysql_cols)})")
    col_block = ",\n".join(lines)
    return (f"CREATE TABLE IF NOT EXISTS {table} (\n{col_block}\n) "
            f"{_MYSQL_TABLE_OPTIONS}")
