# -*- coding: utf-8 -*-
"""核对今日分区中 49 号球房行的富集结果（正确键名 name/roomName/code）"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

out = io.StringIO()
_P = lambda *a: print(*a, file=out)

import database.table_db as table_db  # noqa: E402

total, page_rows = table_db.query_page(1, 200, keyword="49-",
                                       include_test=True)
_P("query_page(keyword='49-') total=%s 返回=%s" % (total, len(page_rows)))
bad = []
seen = 0
for rec in page_rows:
    name = rec.get("name") or ""
    if not name.startswith("49-"):
        continue
    seen += 1
    disp = rec.get("todesk_status", "")
    if name in ("49-04", "49-06"):
        ok = disp == "1"
    else:
        ok = disp == "0"
    if not ok:
        bad.append((name, disp))
    _P("  %-8s action=%-4r 显示=%-4r%s" % (
        name, rec.get("todesk_action"), disp, "" if ok else "  <-- 异常"))
_P("49- 命中:", seen, "| 异常:", bad if bad else "无（04/06='1'，其余='0'）")

with open("tools/_probe_out.txt", "w", encoding="utf-8") as f:
    f.write(out.getvalue())
