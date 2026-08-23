# -*- coding: utf-8 -*-
"""sync_health_alerts 批量化改造回归测试

背景：旧实现对每条告警逐行 SELECT + INSERT/UPDATE（N+1 次本地往返），
数百台设备时同步开销明显。改造为：
- SQLite：一次取回全部 (name, resolved_health)，Python 侧分支，
  executemany 批量写入；
- MySQL：过滤后参数收集为列表，一次 executemany 提交
  （保留 ON DUPLICATE KEY UPDATE 原子 upsert 语义）。

语义必须不变：过滤规则、已处理标记保留/清除、接口消失设备清理、
返回未处理条数。
"""
import sqlite3

import database.backend as backend
import database.table_db as table_db


_CREATE_TABLE = """
CREATE TABLE health_alerts (
    name            TEXT PRIMARY KEY,
    roomName        TEXT DEFAULT '',
    onlineStatusName TEXT DEFAULT '',
    health          REAL DEFAULT 0,
    resolved_health REAL,
    device_code     TEXT DEFAULT '',
    updated_at      TEXT DEFAULT ''
)
"""


def _setup_sqlite(monkeypatch, tmp_path):
    """临时库 + 跳过真实迁移，只建 health_alerts 表"""
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(table_db, "DB_PATH", db)
    monkeypatch.setattr(table_db, "_conn", None)
    monkeypatch.setattr(table_db, "_ensure_initialized", lambda c: None)
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)
    sl = sqlite3.connect(db)
    sl.execute(_CREATE_TABLE)
    sl.commit()
    sl.close()


def _row(db, name):
    sl = sqlite3.connect(db)
    r = sl.execute(
        "SELECT health, resolved_health FROM health_alerts WHERE name=?",
        (name,)).fetchone()
    sl.close()
    return r


def test_sync_inserts_only_valid_alerts(monkeypatch, tmp_path):
    """过滤规则：<=4000 / >40万 / 公司测试 全部排除，返回未处理条数"""
    _setup_sqlite(monkeypatch, tmp_path)
    rows = [
        {"name": "A", "health": 4500, "roomName": "R1",
         "onlineStatusName": "空闲"},          # 健康度异常 → 写入
        {"name": "B", "health": 6000, "roomName": "R2",
         "onlineStatusName": "忙碌"},          # 严重异常 → 写入
        {"name": "C", "health": 3999},         # 正常 → 排除
        {"name": "D", "health": 4000},         # 接口默认值 → 排除
        {"name": "E", "health": 500000},       # 脏数据 → 排除
        {"name": "F", "health": 4200, "roomName": "公司测试"},  # 测试房 → 排除
        {"name": "", "health": 9999},          # 无名 → 排除
        {"name": "G", "health": "bad"},        # 非数字 → 排除
    ]
    count = table_db.sync_health_alerts(rows)
    assert count == 2
    conn = table_db._get_conn()
    names = [r[0] for r in conn.execute(
        "SELECT name FROM health_alerts").fetchall()]
    assert sorted(names) == ["A", "B"]


def test_sync_keeps_resolved_when_health_unchanged(monkeypatch, tmp_path):
    """已处理且 health 未变：保持已处理，不计入未处理条数"""
    _setup_sqlite(monkeypatch, tmp_path)
    db = table_db.DB_PATH
    rows = [{"name": "A", "health": 4500, "roomName": "R1",
             "onlineStatusName": "空闲"},
            {"name": "B", "health": 6000, "roomName": "R2",
             "onlineStatusName": "忙碌"}]
    table_db.sync_health_alerts(rows)
    table_db.mark_health_alerts_resolved(["A"])   # resolved_health = 4500

    # health 不变重刷：A 保持已处理
    assert table_db.sync_health_alerts(rows) == 1
    assert _row(db, "A")[1] == 4500.0             # 标记仍在
    assert _row(db, "B")[1] is None               # B 未处理


def test_sync_reclears_when_health_changed(monkeypatch, tmp_path):
    """已处理但 health 变化：清除标记重新展示"""
    _setup_sqlite(monkeypatch, tmp_path)
    db = table_db.DB_PATH
    table_db.sync_health_alerts(
        [{"name": "A", "health": 4500, "roomName": "R1",
          "onlineStatusName": "空闲"}])
    table_db.mark_health_alerts_resolved(["A"])

    # health 变了但仍异常 → 重新展示
    assert table_db.sync_health_alerts(
        [{"name": "A", "health": 4800, "roomName": "R1",
          "onlineStatusName": "空闲"}]) == 1
    assert _row(db, "A") == (4800.0, None)


def test_sync_updates_base_fields_and_removes_gone(monkeypatch, tmp_path):
    """基础字段随接口刷新；接口中消失的设备被清理"""
    _setup_sqlite(monkeypatch, tmp_path)
    db = table_db.DB_PATH
    table_db.sync_health_alerts(
        [{"name": "A", "health": 4500, "roomName": "R1",
          "onlineStatusName": "空闲"},
         {"name": "B", "health": 6000, "roomName": "R2",
          "onlineStatusName": "忙碌"}])

    # A 在线状态变化；B 从接口消失
    assert table_db.sync_health_alerts(
        [{"name": "A", "health": 4500, "roomName": "R1",
          "onlineStatusName": "忙碌"}]) == 1
    conn = table_db._get_conn()
    assert conn.execute(
        "SELECT onlineStatusName FROM health_alerts WHERE name='A'"
    ).fetchone()[0] == "忙碌"
    assert conn.execute(
        "SELECT COUNT(*) FROM health_alerts WHERE name='B'"
    ).fetchone()[0] == 0


def test_sync_empty_round_keeps_history(monkeypatch, tmp_path):
    """本轮无有效告警（全被过滤）：不清空历史，返回值不变"""
    _setup_sqlite(monkeypatch, tmp_path)
    table_db.sync_health_alerts(
        [{"name": "A", "health": 4500, "roomName": "R1",
          "onlineStatusName": "空闲"}])
    # 只来一条正常值 → seen 为空 → 不 DELETE
    assert table_db.sync_health_alerts(
        [{"name": "X", "health": 100}]) == 1


# ==================== MySQL 路径：executemany 批量 upsert ====================

class _FakeCursor:
    def __init__(self, value):
        self._value = value

    def fetchone(self):
        return (self._value,)


class _FakeMysqlConn:
    """记录 executemany / execute 调用，模拟最终 COUNT 查询"""

    def __init__(self, unresolved=0):
        self.unresolved = unresolved
        self.batch = None          # (sql, params_list)
        self.executes = []         # [(sql, params), ...]

    def executemany(self, sql, seq):
        self.batch = (sql, list(seq))

    def execute(self, sql, params=None):
        self.executes.append((sql, params))
        return _FakeCursor(self.unresolved)

    def commit(self):
        pass


def test_sync_stores_and_queries_device_code(monkeypatch, tmp_path):
    """device_code（球桌 code 字段）随同步落库，查询链路透出供「已处理」用"""
    _setup_sqlite(monkeypatch, tmp_path)
    table_db.sync_health_alerts(
        [{"name": "A", "health": 4500, "roomName": "R1",
          "onlineStatusName": "空闲", "code": "CODE-A"},
         {"name": "B", "health": 6000, "roomName": "R2",
          "onlineStatusName": "忙碌"}])          # 无 code → 空串
    rows = {r["name"]: r for r in table_db.query_health_alerts()}
    assert rows["A"]["device_code"] == "CODE-A"
    assert rows["B"]["device_code"] == ""


def test_mysql_sync_batches_upsert():
    """MySQL 路径：过滤后一次 executemany，upsert 语义保留"""
    conn = _FakeMysqlConn(unresolved=2)
    rows = [
        {"name": "A", "health": 4500, "roomName": "R1",
         "onlineStatusName": "空闲", "code": "CODE-A"},
        {"name": "B", "health": 6000, "roomName": "R2",
         "onlineStatusName": "忙碌", "code": "CODE-B"},
        {"name": "C", "health": 100},              # 正常 → 过滤
        {"name": "D", "health": 4200, "roomName": "公司测试"},  # 过滤
    ]
    ret = table_db._sync_health_alerts_mysql(
        conn, rows, "2026-08-23 02:00:00")
    assert ret == 2

    # 批量 upsert 只调用一次，参数只含有效设备
    assert conn.batch is not None
    sql, params = conn.batch
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "resolved_health = IF(" in sql
    assert "device_code = VALUES(device_code)" in sql
    assert [p[0] for p in params] == ["A", "B"]
    assert [p[4] for p in params] == ["CODE-A", "CODE-B"]
    assert all(len(p) == 6 and p[5] == "2026-08-23 02:00:00" for p in params)

    # DELETE 清理与最终 COUNT 各一次
    assert len(conn.executes) == 2
    assert "NOT IN (?,?)" in conn.executes[0][0]
    assert sorted(conn.executes[0][1]) == ["A", "B"]


def test_mysql_sync_empty_batch_skips_upsert():
    """全部被过滤：不调 executemany，也不执行非法 DELETE（NOT IN ()）"""
    conn = _FakeMysqlConn(unresolved=0)
    ret = table_db._sync_health_alerts_mysql(
        conn, [{"name": "X", "health": 100}], "2026-08-23 02:00:00")
    assert ret == 0
    assert conn.batch is None
    # 无 DELETE（空批次不执行），只剩最终 COUNT 查询
    assert len(conn.executes) == 1
    assert "resolved_health IS NULL" in conn.executes[0][0]


# ==================== 过滤函数单元用例 ====================

def test_filter_dedupes_same_name_last_wins():
    """同名设备多条记录：以最后一条为准（旧逐行实现的最终结果一致）"""
    items = table_db._filter_alert_items([
        {"name": "A", "health": 4500, "roomName": "R1",
         "onlineStatusName": "空闲"},
        {"name": "A", "health": 6000, "roomName": "R1",
         "onlineStatusName": "忙碌", "code": "CODE-A"},
    ])
    assert len(items) == 1
    assert items[0] == ("A", "R1", "忙碌", 6000.0, "CODE-A")
