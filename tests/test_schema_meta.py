# -*- coding: utf-8 -*-
"""schema.py 双方言 DDL 生成等价性测试（GOLDEN 基线快照）

背景：schema.py 将 8 张表的列元数据收敛为单一来源。为防止 T02 把
table_db._CREATE_* / backend.MYSQL_DDL 改为 schema 生成后测试失去独立
基线，本文件在模块内**内联**了每张表 GOLDEN 期望 DDL（不含注释），
以 **git HEAD 旧常量**（T02 改动前的 table_db.py / backend.py 源码）为
基准核验，逐表断言：

- normalize(schema.to_sqlite_ddl(t)) == normalize(GOLDEN_SQLITE_DDL[t])
- normalize(schema.to_mysql_ddl(t)) == normalize(GOLDEN_MYSQL_DDL[t])

normalize = 去首尾空白 + 合并连续空白（含换行），使对齐空格/换行差异
不影响语义等价判定。
"""

import re

from database import schema

# ==================== GOLDEN 基线：SQLite DDL（复制自 table_db.py） ====================

GOLDEN_SQLITE_DDL = {
    "billiard_tables": """
CREATE TABLE IF NOT EXISTS billiard_tables (
    id      INTEGER PRIMARY KEY,
    name    TEXT DEFAULT '',
    roomName TEXT DEFAULT '',
    onlineStatusName TEXT DEFAULT '',
    remark  TEXT DEFAULT '',
    cameraPassExt TEXT DEFAULT '',
    snk_code TEXT DEFAULT '',
    code    TEXT DEFAULT '',
    city    TEXT DEFAULT ''
);
""",
    "sync_meta": """
CREATE TABLE IF NOT EXISTS sync_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
""",
    "xqzg_status": """
CREATE TABLE IF NOT EXISTS xqzg_status (
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
""",
    "kd_status": """
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
CREATE INDEX IF NOT EXISTS idx_kd_status_path_id ON kd_status(file_path, id);
""",
    "submission_log": """
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
""",
    "device_mapping": """
CREATE TABLE IF NOT EXISTS device_mapping (
    device_code TEXT PRIMARY KEY,
    local_dir   TEXT DEFAULT '',
    source      TEXT DEFAULT 'auto',
    created_at  TEXT DEFAULT '',
    updated_at  TEXT DEFAULT ''
);
""",
    "health_alerts": """
CREATE TABLE IF NOT EXISTS health_alerts (
    name            TEXT PRIMARY KEY,
    roomName        TEXT DEFAULT '',
    onlineStatusName TEXT DEFAULT '',
    health          REAL DEFAULT 0,
    resolved_health REAL,
    device_code     TEXT DEFAULT '',
    updated_at      TEXT DEFAULT ''
);
""",
    "aftersale_records": """
CREATE TABLE IF NOT EXISTS aftersale_records (
    id            INTEGER PRIMARY KEY,
    created_at    TEXT DEFAULT '',
    occurred_at   TEXT DEFAULT '',
    creator       TEXT DEFAULT '',
    issue_type    TEXT DEFAULT '',
    table_no      TEXT DEFAULT '',
    room_name     TEXT DEFAULT '',
    region        TEXT DEFAULT '',
    problem       TEXT DEFAULT '',
    cause         TEXT DEFAULT '',
    resolved      TEXT DEFAULT '否',
    is_initiative TEXT DEFAULT '否',
    is_our_problem TEXT DEFAULT '是',
    solution      TEXT DEFAULT '',
    resolver      TEXT DEFAULT '',
    response_time TEXT DEFAULT '',
    snk_code      TEXT DEFAULT '',
    device_code   TEXT DEFAULT '',
    cycle_start   TEXT DEFAULT '',
    updated_at    TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_aftersale_cycle
    ON aftersale_records(cycle_start, id);
CREATE INDEX IF NOT EXISTS idx_aftersale_table_no
    ON aftersale_records(table_no);
""",
    "ledger_records": """
CREATE TABLE IF NOT EXISTS ledger_records (
    id INTEGER PRIMARY KEY,
    category TEXT DEFAULT '',
    kind TEXT DEFAULT '',
    room_name TEXT DEFAULT '',
    video_name TEXT DEFAULT '',
    frame TEXT DEFAULT '',
    description TEXT DEFAULT '',
    repro TEXT DEFAULT '',
    new_program TEXT DEFAULT '',
    remark TEXT DEFAULT '',
    signer TEXT DEFAULT '',
    occurred_at TEXT DEFAULT '',
    created_at TEXT DEFAULT '',
    updated_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ledger_category ON ledger_records(category, id);
CREATE INDEX IF NOT EXISTS idx_ledger_signer ON ledger_records(signer);
""",
}

# ==================== GOLDEN 基线：MySQL DDL（复制自 backend.MYSQL_DDL） ====================

GOLDEN_MYSQL_DDL = {
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
            device_code      VARCHAR(255) DEFAULT '',
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
    "ledger_records": """
        CREATE TABLE IF NOT EXISTS ledger_records (
            id INT AUTO_INCREMENT PRIMARY KEY,
            category VARCHAR(32) DEFAULT '',
            kind VARCHAR(255) DEFAULT '',
            room_name VARCHAR(255) DEFAULT '',
            video_name VARCHAR(512) DEFAULT '',
            frame VARCHAR(32) DEFAULT '',
            description TEXT,
            repro TEXT,
            new_program VARCHAR(32) DEFAULT '',
            remark TEXT,
            signer VARCHAR(64) DEFAULT '',
            occurred_at VARCHAR(32) DEFAULT '',
            created_at VARCHAR(32) DEFAULT '',
            updated_at VARCHAR(32) DEFAULT '',
            INDEX idx_ledger_category (category),
            INDEX idx_ledger_signer (signer)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
}


def normalize(sql: str) -> str:
    """去首尾空白 + 合并连续空白（对齐/换行差异不影响语义等价）"""
    return re.sub(r"\s+", " ", sql.strip())


def test_table_names_cover_all_nine_tables():
    """TABLE_NAMES 覆盖全部 9 张双方言表"""
    assert schema.TABLE_NAMES == [
        "billiard_tables", "sync_meta", "xqzg_status", "kd_status",
        "submission_log", "device_mapping", "health_alerts",
        "aftersale_records", "ledger_records",
    ]
    assert set(schema.TABLE_NAMES) == set(GOLDEN_SQLITE_DDL) == set(GOLDEN_MYSQL_DDL)


# ==================== SQLite 方言逐表等价 ====================

def test_sqlite_ddl_billiard_tables():
    assert normalize(schema.to_sqlite_ddl("billiard_tables")) == normalize(
        GOLDEN_SQLITE_DDL["billiard_tables"])


def test_sqlite_ddl_sync_meta():
    assert normalize(schema.to_sqlite_ddl("sync_meta")) == normalize(
        GOLDEN_SQLITE_DDL["sync_meta"])


def test_sqlite_ddl_xqzg_status():
    assert normalize(schema.to_sqlite_ddl("xqzg_status")) == normalize(
        GOLDEN_SQLITE_DDL["xqzg_status"])


def test_sqlite_ddl_kd_status():
    assert normalize(schema.to_sqlite_ddl("kd_status")) == normalize(
        GOLDEN_SQLITE_DDL["kd_status"])


def test_sqlite_ddl_submission_log():
    assert normalize(schema.to_sqlite_ddl("submission_log")) == normalize(
        GOLDEN_SQLITE_DDL["submission_log"])


def test_sqlite_ddl_device_mapping():
    assert normalize(schema.to_sqlite_ddl("device_mapping")) == normalize(
        GOLDEN_SQLITE_DDL["device_mapping"])


def test_sqlite_ddl_health_alerts():
    assert normalize(schema.to_sqlite_ddl("health_alerts")) == normalize(
        GOLDEN_SQLITE_DDL["health_alerts"])


def test_sqlite_ddl_aftersale_records():
    assert normalize(schema.to_sqlite_ddl("aftersale_records")) == normalize(
        GOLDEN_SQLITE_DDL["aftersale_records"])


def test_sqlite_ddl_ledger_records():
    assert normalize(schema.to_sqlite_ddl("ledger_records")) == normalize(
        GOLDEN_SQLITE_DDL["ledger_records"])


# ==================== MySQL 方言逐表等价 ====================

def test_mysql_ddl_billiard_tables():
    assert normalize(schema.to_mysql_ddl("billiard_tables")) == normalize(
        GOLDEN_MYSQL_DDL["billiard_tables"])


def test_mysql_ddl_sync_meta():
    assert normalize(schema.to_mysql_ddl("sync_meta")) == normalize(
        GOLDEN_MYSQL_DDL["sync_meta"])


def test_mysql_ddl_xqzg_status():
    assert normalize(schema.to_mysql_ddl("xqzg_status")) == normalize(
        GOLDEN_MYSQL_DDL["xqzg_status"])


def test_mysql_ddl_kd_status():
    assert normalize(schema.to_mysql_ddl("kd_status")) == normalize(
        GOLDEN_MYSQL_DDL["kd_status"])


def test_mysql_ddl_submission_log():
    assert normalize(schema.to_mysql_ddl("submission_log")) == normalize(
        GOLDEN_MYSQL_DDL["submission_log"])


def test_mysql_ddl_device_mapping():
    assert normalize(schema.to_mysql_ddl("device_mapping")) == normalize(
        GOLDEN_MYSQL_DDL["device_mapping"])


def test_mysql_ddl_health_alerts():
    assert normalize(schema.to_mysql_ddl("health_alerts")) == normalize(
        GOLDEN_MYSQL_DDL["health_alerts"])


def test_mysql_ddl_aftersale_records():
    assert normalize(schema.to_mysql_ddl("aftersale_records")) == normalize(
        GOLDEN_MYSQL_DDL["aftersale_records"])


def test_mysql_ddl_ledger_records():
    assert normalize(schema.to_mysql_ddl("ledger_records")) == normalize(
        GOLDEN_MYSQL_DDL["ledger_records"])


# ==================== 漂移修复回归 ====================

def test_mysql_xqzg_status_contains_file_path():
    """回归：xqzg_status MySQL DDL 必须含 file_path（此前镜像 DDL 缺列漂移）"""
    assert "file_path" in schema.to_mysql_ddl("xqzg_status")


def test_mysql_xqzg_status_file_path_matches_sqlite():
    """双方言 file_path 列语义一致：SQLite TEXT / MySQL VARCHAR(64)"""
    assert "file_path TEXT DEFAULT ''" in schema.to_sqlite_ddl("xqzg_status")
    assert "file_path VARCHAR(64) DEFAULT ''" in schema.to_mysql_ddl("xqzg_status")


def test_sqlite_ddl_unknown_table_raises():
    import pytest
    with pytest.raises(KeyError):
        schema.to_sqlite_ddl("no_such_table")


def test_mysql_ddl_unknown_table_raises():
    import pytest
    with pytest.raises(KeyError):
        schema.to_mysql_ddl("no_such_table")
