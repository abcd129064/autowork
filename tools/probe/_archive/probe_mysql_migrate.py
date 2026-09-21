# -*- coding: utf-8 -*-
"""todesk_action 口径真实库验证（一次性）：

1. 迁移前 SHOW COLUMNS xqzg_status（确认缺 todesk_action）
2. table_db._get_conn() 首连 → _ensure_mysql_tables 自动迁移
3. 迁移后 SHOW COLUMNS（确认 todesk_action 出现且类型/默认值正确）
4. 登录 xqzg 拉实时行（file_path='' 单页）落今日分区
5. query_page 富集核对 49 号球房：04/06 应 '1'，其余应 '0'
"""
import io
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

out = io.StringIO()
_P = lambda *a: print(*a, file=out)

import requests  # noqa: E402
import database.table_db as table_db  # noqa: E402
import database.backend as backend  # noqa: E402
from database.schema import TABLE_COLUMNS  # noqa: E402

_P("backend state:", backend.get_state(),
   "| mysql_test_mode:", backend.is_mysql_test_mode())


def show_cols():
    conn = backend.create_mysql_connection()
    cur = conn.execute("SHOW COLUMNS FROM xqzg_status")
    cols = [(r[0], r[1], r[4]) for r in cur.fetchall()]
    conn.close()
    return cols


def _flush():
    with open(os.path.join(ROOT, "tools", "_probe_out.txt"), "w", encoding="utf-8") as f:
        f.write(out.getvalue())


before = show_cols()
names_before = [c[0] for c in before]
_P("\n== 迁移前 xqzg_status 列数:", len(names_before),
   "| todesk_action 存在:", "todesk_action" in names_before)

# 首连触发自动迁移（_mysql_tables_ready 初始 False）。
# 注意：_get_conn 返回线程本地连接，不能 close（后续 save_xqzg 复用），
# 这里只触发建表+迁移逻辑。
table_db._get_conn()

after = show_cols()
names_after = [c[0] for c in after]
_P("== 迁移后 xqzg_status 列数:", len(names_after),
   "| todesk_action 存在:", "todesk_action" in names_after)
act = [c for c in after if c[0] == "todesk_action"]
_P("   todesk_action 定义:", act)
exp = [c for c in TABLE_COLUMNS["xqzg_status"] if c.name == "todesk_action"]
_P("   schema 期望:", (exp[0].name, exp[0].mysql_type, exp[0].mysql_default) if exp else None)

# ---- 登录 xqzg 拉实时行 ----
from core.secrets import decrypt_settings  # noqa: E402
with open(os.path.join(ROOT, "config", "credentials.json"), encoding="utf-8") as f:
    creds = decrypt_settings(json.load(f))
api1 = creds["api_credentials"]["api1"]

BASE = "https://xqzg.newbv.cn"
s = requests.Session()
s.headers["User-Agent"] = "AutoWork-probe/1.0"
r = s.post(f"{BASE}/api/rbac/auth/login/",
           json={"username": api1["username"], "password": api1["password"]},
           timeout=15)
_P("\n== xqzg login:", r.status_code)
tok = s.cookies.get("csrftoken")
if not tok:
    s.get(BASE + "/", timeout=15)
    tok = s.cookies.get("csrftoken")
hdr = {"Accept": "application/json", "Referer": f"{BASE}/"}
if tok:
    hdr["X-CSRFToken"] = tok

r = s.get(f"{BASE}/api/snooker_om/status/",
          params={"page": 1, "pagesize": 1200},
          headers=hdr, timeout=30)
data = r.json()
rows = data.get("results") or []
_P("== 实时行 total=%s 拉到=%s" % (data.get("total"), len(rows)))
has_action = sum(1 for x in rows if x.get("todesk_action"))
_P("   含 todesk_action 的行:", has_action)

today = datetime.now().strftime("%Y/%m/%d")
n = table_db.save_xqzg(rows, today)
_P("== save_xqzg 今日分区(%s) 落库行数: %s" % (today, n))

# ---- 49 号球房富集核对 ----
total, page_rows = table_db.query_page(1, 50, keyword="49-")
_P("\n== query_page(keyword='49-') 共 %s 条" % total)
bad = []
for rec in page_rows:
    tid = rec.get("table_id", "")
    if not tid.startswith("49-"):
        continue
    disp = rec.get("todesk_status", "")
    act_v = rec.get("todesk_action", "")
    mark = ""
    if tid in ("49-04", "49-06"):
        ok = disp == "1"
    else:
        ok = disp == "0"
    if not ok:
        bad.append((tid, act_v, disp))
        mark = "  <-- 异常"
    _P("   %-8s action=%-4r 显示=%-4r%s" % (tid, act_v, disp, mark))
_P("异常设备:", bad if bad else "无（04/06='1'，其余='0'）")

with open(os.path.join(ROOT, "tools", "_probe_out.txt"), "w", encoding="utf-8") as f:
    f.write(out.getvalue())
