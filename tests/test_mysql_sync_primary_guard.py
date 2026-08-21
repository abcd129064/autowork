# -*- coding: utf-8 -*-
"""mysql_sync 主模式守卫测试（P0 修复回归）

验证 MySQL 主模式（enabled=true）下 push_all / push_table / push_aftersale
直接 no-op 返回，绝不触达 _ensure_schema / _connect / _read_sqlite，
从而不会用陈旧 SQLite TRUNCATE 覆盖新鲜 MySQL 数据。

通过 monkeypatch 隔离配置读取与主模式判定，无需真实 MySQL / SQLite。
"""

import database.mysql_sync as mysql_sync


def _patch_primary(monkeypatch):
    """主模式 + 配置存在的隔离环境"""
    monkeypatch.setattr(mysql_sync, "_load_mysql_config",
                        lambda: {"host": "x", "enabled": True})
    monkeypatch.setattr(mysql_sync.backend, "is_mysql_test_mode", lambda: True)


def _patch_no_schema(monkeypatch):
    """断言守卫在 _ensure_schema 之前拦截（建表不应被调用）"""
    called = {"schema": False}
    monkeypatch.setattr(mysql_sync, "_ensure_schema",
                        lambda *a, **k: called.__setitem__("schema", True))
    return called


# ==================== push_all ====================

def test_push_all_noop_in_primary(monkeypatch):
    _patch_primary(monkeypatch)
    called = _patch_no_schema(monkeypatch)
    ok, msg, n = mysql_sync.push_all()
    assert ok is True and n == 0
    assert "主模式" in msg
    assert called["schema"] is False  # 守卫在建表前拦截


def test_push_all_disabled_returns_false(monkeypatch):
    # enabled=false → _load_mysql_config 返回 {} → 未启用
    monkeypatch.setattr(mysql_sync, "_load_mysql_config", lambda: {})
    ok, msg, n = mysql_sync.push_all()
    assert ok is False and n == 0


# ==================== push_table ====================

def test_push_table_noop_in_primary(monkeypatch):
    _patch_primary(monkeypatch)
    called = _patch_no_schema(monkeypatch)
    ok, msg, n = mysql_sync.push_table("billiard_tables")
    assert ok is True and n == 0
    assert "主模式" in msg
    assert called["schema"] is False


def test_push_table_non_primary_reaches_table_check(monkeypatch):
    # 非主模式 + 配置存在 + 非法表名 → 应走到表名校验（守卫未误拦正常路径）
    monkeypatch.setattr(mysql_sync, "_load_mysql_config",
                        lambda: {"host": "x"})
    monkeypatch.setattr(mysql_sync.backend, "is_mysql_test_mode", lambda: False)
    ok, msg, n = mysql_sync.push_table("nope_table")
    assert ok is False  # 不支持的表名


# ==================== push_aftersale ====================

def test_push_aftersale_noop_in_primary(monkeypatch):
    _patch_primary(monkeypatch)
    called = _patch_no_schema(monkeypatch)
    ok, msg, n = mysql_sync.push_aftersale()
    assert ok is True and n == 0
    assert "主模式" in msg
    assert called["schema"] is False


def test_push_aftersale_disabled_returns_false(monkeypatch):
    monkeypatch.setattr(mysql_sync, "_load_mysql_config", lambda: {})
    ok, msg, n = mysql_sync.push_aftersale()
    assert ok is False and n == 0
