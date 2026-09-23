# -*- coding: utf-8 -*-
"""MySQL 主 + SQLite 兜底：降级回退状态机测试（阶段一）

验证 _get_conn 在 MySQL 不可用时自动降级到 SQLite、恢复时经迟滞确认
后自动切回 MySQL 并触发合并 hook。通过 monkeypatch 隔离连接创建与
SQLite 落地，不触达真实 MySQL / tables.db。
"""

import pytest

import database.backend as backend
import database.table_db as table_db


@pytest.fixture
def reset_state(monkeypatch):
    """每测试重置后端状态为 ONLINE、清空 thread-local MySQL 连接、
    重置 DEGRADED 恢复试探节流时间戳与迟滞计数（避免跨测试被节流拦截）"""
    monkeypatch.setattr(backend, "_state", backend.STATE_ONLINE)
    monkeypatch.setattr(backend, "_confirm_streak", 0)
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
    """DEGRADED + 首次试连成功 → 本次沿用 MySQL 连接 + 触发合并，
    但状态保持 DEGRADED（迟滞未达阈值）"""
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
    assert backend.get_state() == backend.STATE_DEGRADED
    assert merged["called"] is True


def test_degraded_recovery_hysteresis_second_success_flips_online(
        monkeypatch, reset_state):
    """迟滞：DEGRADED 下连续两次试连成功（间隔≥节流窗口）才翻回 ONLINE"""
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: True)
    monkeypatch.setattr(backend, "_state", backend.STATE_DEGRADED)

    mysql_sentinel = object()
    monkeypatch.setattr(backend, "create_mysql_connection",
                        lambda: mysql_sentinel)
    monkeypatch.setattr(table_db, "_ensure_mysql_tables", lambda c: None)
    monkeypatch.setattr(table_db, "_mysql_tables_ready", True)
    monkeypatch.setattr(table_db, "_trigger_merge_back", lambda: None)

    # 第一次成功：仍 DEGRADED
    assert table_db._get_conn() is mysql_sentinel
    assert backend.get_state() == backend.STATE_DEGRADED

    # 拨过节流窗口，第二次成功 → ONLINE
    table_db._last_mysql_probe_ts -= table_db._MYSQL_PROBE_INTERVAL + 1
    assert table_db._get_conn() is mysql_sentinel
    assert backend.get_state() == backend.STATE_ONLINE


def test_degraded_hysteresis_failure_resets_streak(monkeypatch, reset_state):
    """恢复路上任何一次失败都打断"连续成功"：成功1次→失败→再成功1次
    仍不翻 ONLINE，需再连续成功两次"""
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: True)
    monkeypatch.setattr(backend, "_state", backend.STATE_DEGRADED)
    monkeypatch.setattr(table_db, "_ensure_mysql_tables", lambda c: None)
    monkeypatch.setattr(table_db, "_mysql_tables_ready", True)
    monkeypatch.setattr(table_db, "_trigger_merge_back", lambda: None)

    ok_conn = object()
    monkeypatch.setattr(backend, "create_mysql_connection", lambda: ok_conn)
    assert table_db._get_conn() is ok_conn          # 成功 #1
    assert backend.get_state() == backend.STATE_DEGRADED

    def boom():
        raise RuntimeError("still down")
    monkeypatch.setattr(backend, "create_mysql_connection", boom)
    table_db._last_mysql_probe_ts -= table_db._MYSQL_PROBE_INTERVAL + 1
    sqlite_sentinel = object()
    monkeypatch.setattr(table_db, "_get_sqlite_conn", lambda: sqlite_sentinel)
    assert table_db._get_conn() is sqlite_sentinel  # 失败 → streak 清零
    assert backend.get_state() == backend.STATE_DEGRADED

    monkeypatch.setattr(backend, "create_mysql_connection", lambda: ok_conn)
    table_db._last_mysql_probe_ts -= table_db._MYSQL_PROBE_INTERVAL + 1
    assert table_db._get_conn() is ok_conn          # 成功 #1'（重新计数）
    assert backend.get_state() == backend.STATE_DEGRADED

    table_db._last_mysql_probe_ts -= table_db._MYSQL_PROBE_INTERVAL + 1
    assert table_db._get_conn() is ok_conn          # 成功 #2' → ONLINE
    assert backend.get_state() == backend.STATE_ONLINE


def test_mark_degraded_resets_probe_streak(reset_state):
    """mark_degraded 幂等且清零迟滞计数；ONLINE 态上报成功不翻转"""
    backend.mark_degraded()
    assert backend.note_probe_success() is False    # streak=1 < 2
    backend.mark_degraded()                         # 幂等 + 清零
    assert backend._confirm_streak == 0
    assert backend.get_state() == backend.STATE_DEGRADED

    assert backend.mark_online() is True            # 显式恢复仍即时生效
    assert backend.note_probe_success() is False    # ONLINE 态上报不翻转
    assert backend.get_state() == backend.STATE_ONLINE


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
