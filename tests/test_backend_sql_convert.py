# -*- coding: utf-8 -*-
"""database/backend.py SQL 方言转换层单元测试

这些纯字符串转换函数是 SQLite→MySQL 双后端切换的核心，也是任何数据库
迁移/重构最易踩坑的地方（占位符、ON CONFLICT、保留字、date() 函数、
COLLATE NOCASE）。本文件不依赖 PySide6 / 数据库环境即可运行。

运行：在项目根目录执行
  py -m pytest tests/test_backend_sql_convert.py -v
"""

from database import backend


# ==================== convert_placeholders ====================

def test_placeholders_basic():
    assert backend.convert_placeholders(
        "SELECT * FROM t WHERE a=? AND b=?"
    ) == "SELECT * FROM t WHERE a=%s AND b=%s"


def test_placeholders_inside_string_literal_preserved():
    # 字符串字面量内的 ? 不应被转换
    assert backend.convert_placeholders(
        "INSERT INTO t VALUES ('a?b', ?)"
    ) == "INSERT INTO t VALUES ('a?b', %s)"


def test_placeholders_single_quoted_literal():
    assert backend.convert_placeholders(
        "SELECT '?' WHERE x=?"
    ) == "SELECT '?' WHERE x=%s"


def test_placeholders_no_change_when_absent():
    assert backend.convert_placeholders("SELECT 1") == "SELECT 1"


# ==================== convert_on_conflict ====================

def test_on_conflict_to_on_duplicate_key():
    sql = ("INSERT INTO device_mapping(device_code, local_dir) VALUES(?, ?) "
           "ON CONFLICT(device_code) DO UPDATE SET local_dir=excluded.local_dir")
    # 替换为 "col = VALUES(col)"（= 两侧带空格，见 regex 替换串）
    expected = ("INSERT INTO device_mapping(device_code, local_dir) VALUES(?, ?) "
                "ON DUPLICATE KEY UPDATE local_dir = VALUES(local_dir)")
    assert backend.convert_on_conflict(sql) == expected


def test_on_conflict_no_match_unchanged():
    sql = "SELECT * FROM t"
    assert backend.convert_on_conflict(sql) == sql


# ==================== convert_insert_or_replace ====================

def test_insert_or_replace_uppercase():
    assert backend.convert_insert_or_replace(
        "INSERT OR REPLACE INTO t(a) VALUES(?)"
    ) == "INSERT INTO t(a) VALUES(?)"


def test_insert_or_replace_lowercase():
    # re.sub 替换串为字面量 "INSERT"（大写），仅匹配部分被替换，其余保留原大小写
    assert backend.convert_insert_or_replace(
        "insert or replace into t(a) values(?)"
    ) == "INSERT into t(a) values(?)"


# ==================== _strip_collate_nocase ====================

def test_strip_collate_nocase_uppercase():
    assert backend._strip_collate_nocase(
        "WHERE a COLLATE NOCASE = ?"
    ) == "WHERE a = ?"


def test_strip_collate_nocase_mixed_case():
    assert backend._strip_collate_nocase(
        "WHERE a collate nocase = ?"
    ) == "WHERE a = ?"


# ==================== _quote_reserved_words ====================

def test_quote_reserved_where_key_eq():
    assert backend._quote_reserved_words("WHERE key = ?") == "WHERE `key` = ?"


def test_quote_reserved_where_key_like():
    assert backend._quote_reserved_words(
        "WHERE key LIKE ?") == "WHERE `key` LIKE ?"


def test_quote_reserved_select_key_from():
    assert backend._quote_reserved_words(
        "SELECT key FROM sync_meta") == "SELECT `key` FROM sync_meta"


def test_quote_reserved_insert_tuple():
    assert backend._quote_reserved_words(
        "(key, value) VALUES (?, ?)") == "(`key`, `value`) VALUES (?, ?)"


# ==================== _convert_sqlite_date_functions ====================

def test_convert_date_function_three_days():
    sql = "replace(date(replace(col, '/', '-'), '-3 days'), '-', '/')"
    expected = ("DATE_FORMAT(DATE_SUB(STR_TO_DATE(REPLACE(col, '/', '-'), "
                "'%%Y-%%m-%%d'), INTERVAL 3 DAY), '%%Y/%%m/%%d')")
    assert backend._convert_sqlite_date_functions(sql) == expected


def test_convert_date_function_no_match_unchanged():
    sql = "SELECT date('now')"
    assert backend._convert_sqlite_date_functions(sql) == sql


# ==================== %% 日期格式符 + pymysql 参数化还原 ====================

def test_convert_date_format_percent_escaped_then_restored_by_format():
    """_convert_sql 输出含 %%Y，经 pymysql 的 `query % ()` 格式化后还原为 %Y。"""
    converted = backend._convert_sql(
        "replace(date(replace(fp, '/', '-'), '-7 days'), '-', '/')")
    assert "%%Y-%%m-%%d" in converted
    assert "%%Y/%%m/%%d" in converted
    # 模拟 pymysql 无参数路径：execute 检测到 %% 时传空元组触发格式化
    restored = converted % ()
    assert "%Y-%m-%d" in restored
    assert "%Y/%m/%d" in restored
    assert "%%" not in restored


def test_convert_sql_mixed_placeholders_date_and_reserved():
    """组合场景：? 占位符（→%s）、日期函数、WHERE key= 保留字同一条 SQL。"""
    sql = ("SELECT key, replace(date(replace(fp, '/', '-'), '-7 days'), '-', '/') "
           "FROM kd_status WHERE key = ?")
    converted = backend._convert_sql(sql)
    assert "`key`" in converted
    assert "%s" in converted
    assert "%%Y-%%m-%%d" in converted
    # 带参数路径：sql % (值,) 后 %% 还原为 %、%s 替换为值（纯 % 格式化不加引号，
    # 引号由 pymysql escape 负责，这里只验证值替换本身）
    rendered = converted % ("abc",)
    assert "%Y-%m-%d" in rendered
    assert "%%" not in rendered
    assert "%s" not in rendered
    assert "abc" in rendered


def test_convert_sql_query_kd_alerts_like_sqlite_no_param_no_double_percent():
    """query_kd_alerts 的 SQLite 原文（days=7）转换后，无参数路径无 %% 残留。"""
    sql = """
    WITH latest(fp) AS (
        SELECT MAX(file_path) FROM kd_status WHERE file_path != ''
    ),
    cutoff(c) AS (
        SELECT replace(date(replace(fp, '/', '-'), '-7 days'), '-', '/')
        FROM latest
    )
    SELECT device_code FROM kd_status
    WHERE file_path >= (SELECT c FROM cutoff)
    """
    converted = backend._convert_sql(sql)
    assert "DATE_FORMAT" in converted
    assert "%%Y" in converted
    restored = converted % ()
    assert "%%" not in restored
    assert "%Y-%m-%d" in restored


# ==================== _convert_sql (端到端管线) ====================

def test_convert_sql_chain_placeholders_and_reserved():
    sql = "SELECT key FROM sync_meta WHERE key = ?"
    expected = "SELECT `key` FROM sync_meta WHERE `key` = %s"
    assert backend._convert_sql(sql) == expected


def test_convert_sql_chain_insert_replace_and_conflict():
    sql = ("INSERT OR REPLACE INTO device_mapping(device_code, local_dir) "
           "VALUES(?, ?) ON CONFLICT(device_code) DO UPDATE SET "
           "local_dir=excluded.local_dir")
    expected = ("INSERT INTO device_mapping(device_code, local_dir) "
                "VALUES(%s, %s) ON DUPLICATE KEY UPDATE "
                "local_dir = VALUES(local_dir)")
    assert backend._convert_sql(sql) == expected


def test_convert_sql_chain_collate_strip_with_placeholder():
    sql = "SELECT a FROM t WHERE a COLLATE NOCASE = ?"
    expected = "SELECT a FROM t WHERE a = %s"
    assert backend._convert_sql(sql) == expected
