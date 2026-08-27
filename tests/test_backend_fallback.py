# -*- coding: utf-8 -*-
"""MySQL 主 + SQLite 兜底：降级回退状态机测试（阶段一）

验证 _get_conn 在 MySQL 不可用时自动降级到 SQLite、恢复时自动切回 MySQL
并触发合并 hook。通过 monkeypatch 隔离连接创建与 SQLite 落地，不触达真实
MySQL / tables.db。
"""

import pytest

import database.backend as backend
import database.table_db as table_db


@pytest.fixture
def reset_state(monkeypatch):
    """每测试重置后端状态为 ONLINE、清空 thread-local MySQL 连接、
    重置 DEGRADED 恢复试探节流时间戳（避免跨测试被节流拦截）"""
    monkeypatch.setattr(backend, "_state", backend.STATE_ONLINE)
    monkeypatch.setattr(table_db, "_mysql_local", threading_local())
    monkeypatch.setattr(table_db, "_last_mysql_probe_ts", 0.0)


def threading_local():
    import threading
    return threading.local()


def test_online_mysql_failure_falls_back_to_sqlite(monkeypatch, reset_state):
    """ONLINE + MySQL 连接抛异常 → 降级 + 返回 SQLite 连接"""
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: True)

    def boom():
        raise RuntimeError("mysql down")
    monkeypatch.setattr(backend, "create_mysql_connection", boom)

    sqlite_sentinel = object()
    monkeypatch.setattr(table_db, "_get_sqlite_conn", lambda: sqlite_sentinel)

    got = table_db._get_conn()
    assert got is sqlite_sentinel
    assert backend.get_state() == backend.STATE_DEGRADED


def test_degraded_mysql_recovered_returns_mysql(monkeypatch, reset_state):
    """DEGRADED + MySQL 恢复 → mark_online + 触发合并 + 返回 MySQL 连接"""
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: True)
    monkeypatch.setattr(backend, "_state", backend.STATE_DEGRADED)

    mysql_sentinel = object()
    monkeypatch.setattr(backend, "create_mysql_connection",
                        lambda: mysql_sentinel)
    monkeypatch.setattr(table_db, "_ensure_mysql_tables", lambda c: None)
    monkeypatch.setattr(table_db, "_mysql_tables_ready", True)

    merged = {"called": False}
    monkeypatch.setattr(table_db, "_trigger_merge_back",
                        lambda: merged.__setitem__("called", True))

    got = table_db._get_conn()
    assert got is mysql_sentinel
    assert backend.get_state() == backend.STATE_ONLINE
    assert merged["called"] is True


def test_degraded_still_down_stays_on_sqlite(monkeypatch, reset_state):
    """DEGRADED + MySQL 仍不可用 → 继续返回 SQLite，状态不变"""
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: True)
    monkeypatch.setattr(backend, "_state", backend.STATE_DEGRADED)

    def boom():
        raise RuntimeError("still down")
    monkeypatch.setattr(backend, "create_mysql_connection", boom)

    sqlite_sentinel = object()
    monkeypatch.setattr(table_db, "_get_sqlite_conn", lambda: sqlite_sentinel)

    got = table_db._get_conn()
    assert got is sqlite_sentinel
    assert backend.get_state() == backend.STATE_DEGRADED


def test_non_mysql_mode_uses_sqlite(monkeypatch, reset_state):
    """enabled=false → 直接走 SQLite，不碰 MySQL"""
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)
    sqlite_sentinel = object()
    monkeypatch.setattr(table_db, "_get_sqlite_conn", lambda: sqlite_sentinel)

    got = table_db._get_conn()
    assert got is sqlite_sentinel
