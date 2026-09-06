# -*- coding: utf-8 -*-
"""QA R2 独立验证（P2-2）：merge_aftersale 合并成功后候选缓存确实失效

用假 mysql_conn（duck-typed cursor：sel_sql 恒 fetchone=None → 全部走
insert 分支）+ monkeypatch DB_PATH 指向临时库，验证：
1. 合并前 get_field_candidates 建缓存；
2. merge_aftersale 后 _field_cands_cache 被置空；
3. 合并结果返回值不受缓存失效异常影响（失效路径 try/except 不吞合并数）。
"""
import os, sys, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database.merge_back as mb
import database.aftersale_db as adb
from database import schema

tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, "t.db")
sl = sqlite3.connect(db_path)
sl.executescript(schema.to_sqlite_ddl("aftersale_records"))
sl.execute(
    "INSERT INTO aftersale_records (created_at, occurred_at, creator, "
    "issue_type, table_no, room_name, region, problem, cycle_start, updated_at) "
    "VALUES ('2026-08-20 10:00:00', '2026-08-19', 't', '硬件问题', 'T1', "
    "'r', '', '合并前问题P', '', '2026-08-20 10:00:00')")
sl.commit()
sl.close()

mb.DB_PATH = db_path
# 重定向 aftersale_db 连接（防 get_field_candidates 触真实 tables.db）
_conn_holder = sqlite3.connect(db_path)
_orig_conn = adb._conn
adb._conn = lambda: _conn_holder


class _FakeCursor:
    def __init__(self):
        self.executed = []
    def execute(self, sql, params=None):
        self.executed.append(sql)
    def fetchone(self):
        return None  # 远端无记录 → 全走 insert
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class _FakeMysqlConn:
    def __init__(self):
        self.committed = 0
        self._cur = _FakeCursor()
    def cursor(self):
        return self._cur
    def commit(self):
        self.committed += 1


# 1) 建缓存（走真实临时库，候选来自库中 1 条记录 + 预置/crew 合并）
c1 = adb.get_field_candidates()
assert adb._field_cands_cache is not None, "缓存未建立"

# 2) 合并
fake = _FakeMysqlConn()
n = mb.merge_aftersale(fake)
assert n == 1, f"合并条数异常: {n}"
assert fake.committed == 1, "commit 未调用"

# 3) 缓存已失效
if adb._field_cands_cache is not None:
    print(f"FAIL: merge_aftersale 后缓存未失效 "
          f"(cache={adb._field_cands_cache})")
    sys.exit(1)

# 4) 失效路径异常不吞合并结果：把 _invalidate_field_cands_cache 换成抛异常
adb._field_cands_cache = {"x": []}  # 重新放一个缓存
orig_inv = adb._invalidate_field_cands_cache
def _boom():
    raise RuntimeError("模拟失效钩子异常")
adb._invalidate_field_cands_cache = _boom
try:
    n2 = mb.merge_aftersale(_FakeMysqlConn())
finally:
    adb._invalidate_field_cands_cache = orig_inv
    adb._field_cands_cache = None
if n2 != 1:
    print(f"FAIL: 失效钩子异常影响了合并结果: {n2}")
    sys.exit(1)

adb._conn = _orig_conn
_conn_holder.close()
print("PASS: merge_aftersale 后候选缓存已失效；失效钩子异常不影响合并结果")
