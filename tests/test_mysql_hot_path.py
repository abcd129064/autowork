# -*- coding: utf-8 -*-
"""MySQL 热路径回归：配置缓存、连接复用、原生批量写与事务边界。"""

import threading

from database import backend, table_db


class _FakeCursor:
    def __init__(self):
        self.execute_calls = []
        self.executemany_calls = []
        self.rowcount = 0
        self.description = None
        self.lastrowid = None

    def execute(self, sql, params=None):
        self.execute_calls.append((sql, params))

    def executemany(self, sql, params):
        self.executemany_calls.append((sql, list(params)))
        self.rowcount = len(self.executemany_calls[-1][1])

    def close(self):
        pass


class _FakeRawConnection:
    def __init__(self):
        self.cursor_obj = _FakeCursor()
        self.begin_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    def cursor(self):
        return self.cursor_obj

    def begin(self):
        self.begin_calls += 1

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1

    def close(self):
        self.close_calls += 1


def test_mysql_settings_are_cached_until_explicit_invalidation(monkeypatch):
    """热路径重复读取开关时不得重复读取/解密 settings.json。"""
    calls = []

    def read():
        calls.append(1)
        return {"enabled": True}

    monkeypatch.setattr(backend, "_read_mysql_settings", read)
    backend.invalidate_mysql_settings_cache()
    try:
        assert backend.is_mysql_test_mode() is True
        assert backend.is_mysql_test_mode() is True
        assert calls == [1]
        backend.invalidate_mysql_settings_cache()
        assert backend.is_mysql_test_mode() is True
        assert calls == [1, 1]
    finally:
        backend.invalidate_mysql_settings_cache()


def test_adapter_executemany_delegates_to_driver_bulk_api():
    """适配器不能把批量 INSERT 退化成 Python 层逐行 execute。"""
    raw = _FakeRawConnection()
    conn = backend.MysqlConnectionAdapter(raw)

    conn.executemany("INSERT INTO t(a) VALUES (?)", [(1,), (2,), (3,)])

    assert raw.cursor_obj.execute_calls == []
    assert raw.cursor_obj.executemany_calls == [
        ("INSERT INTO t(a) VALUES (%s)", [(1,), (2,), (3,)])]


def test_batch_transaction_uses_one_explicit_mysql_transaction():
    """DELETE + 批量 INSERT 等替换操作必须显式包在同一个事务中。"""
    raw = _FakeRawConnection()
    conn = backend.MysqlConnectionAdapter(raw)

    with table_db._batch_transaction(conn):
        conn.execute("DELETE FROM t")
        conn.executemany("INSERT INTO t(a) VALUES (?)", [(1,), (2,)])

    assert raw.begin_calls == 1
    assert raw.commit_calls == 1
    assert raw.rollback_calls == 0


def test_batch_transaction_rolls_back_on_error():
    raw = _FakeRawConnection()
    conn = backend.MysqlConnectionAdapter(raw)

    try:
        with table_db._batch_transaction(conn):
            raise RuntimeError("write failed")
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected transaction error")

    assert raw.begin_calls == 1
    assert raw.commit_calls == 0
    assert raw.rollback_calls == 1


def test_get_conn_reuses_healthy_connection_without_ping(monkeypatch):
    """同线程健康连接直接复用；热路径不应 ping 或重建。"""
    raw = _FakeRawConnection()
    conn = backend.MysqlConnectionAdapter(raw)
    created = []

    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: True)
    monkeypatch.setattr(backend, "mysql_settings_generation", lambda: 7)
    monkeypatch.setattr(backend, "get_state", lambda: backend.STATE_ONLINE)
    monkeypatch.setattr(backend, "create_mysql_connection",
                        lambda: created.append(conn) or conn)
    monkeypatch.setattr(table_db, "_mysql_tables_ready", True)
    monkeypatch.setattr(table_db, "_mysql_local", threading.local())

    assert table_db._get_conn() is conn
    assert table_db._get_conn() is conn
    assert created == [conn]


def test_save_xqzg_invalid_file_path_raises_value_error():
    """save_xqzg 入口校验：非 yyyy/MM/dd 格式的 file_path 必须 fail fast。

    校验发生在 _get_conn() 之前，因此无需数据库连接即可断言；
    脏 file_path 会写坏 sync_meta 键与数据分区，必须入口拒绝。
    """
    import pytest

    with pytest.raises(ValueError):
        table_db.save_xqzg([], file_path="2026/8/2")
    with pytest.raises(ValueError):
        table_db.save_xqzg([], file_path="abc")
    with pytest.raises(ValueError):
        table_db.save_xqzg([], file_path="2026-08-02")
    # 空串合法（无分区）：校验条件为 `if file_path and ...`，空串不触发。
    # 不实际调用 save_xqzg([]) 以免进入 _get_conn 依赖数据库环境。
    assert table_db.save_xqzg.__defaults__ == ("",)

