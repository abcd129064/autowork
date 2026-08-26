# -*- coding: utf-8 -*-
"""售后统计弹窗 offscreen 冒烟：临时库造数 → 构建弹窗 → 等异步加载 → 截图

运行（miniconda3 python 带 PySide6）：
    QT_QPA_PLATFORM=offscreen python tools/smoke_stats_dialog.py
"""
import os
import sqlite3
import sys
import tempfile
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# ---- Qt DLL 引导（同 windows/aftersale_panel.py 顶部，规避 conda Qt 冲突） ----
import importlib.util as _iu
try:
    _spec = _iu.find_spec('PySide6')
    if _spec is not None:
        for _d in (list(_spec.submodule_search_locations or []) +
                   [os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                                 'System32')]):
            if _d and os.path.isdir(_d):
                try:
                    os.add_dll_directory(_d)
                except OSError:
                    pass
except Exception:
    pass

import database.backend as backend
import database.table_db as table_db
from database import schema

# ---- 临时库造数（14 天 × 每日 2~5 条，两地区三类型） ----
tmp = tempfile.mkdtemp(prefix="smoke_stats_")
dbp = os.path.join(tmp, "t.db")
table_db.DB_PATH = dbp
table_db._conn = None
table_db._ensure_initialized = lambda c: None
backend.is_mysql_test_mode = lambda: False

sl = sqlite3.connect(dbp)
sl.executescript(schema.to_sqlite_ddl("aftersale_records"))
rows = []
for i, day in enumerate(range(18, 32)):   # 2026-08-18 ~ 08-31
    d = f"2026-08-{day:02d}"
    for k in range(2 + i % 4):
        rows.append((f"{d} 10:00:00", d,
                     ["硬件问题", "程序相关", "识别问题"][k % 3],
                     "广东" if k % 2 == 0 else "上海",
                     "是" if k % 3 else "否"))
sl.executemany(
    "INSERT INTO aftersale_records (created_at, occurred_at, creator, "
    "issue_type, table_no, room_name, region, problem, cause, resolved, "
    "is_initiative, is_our_problem, solution, resolver, response_time, "
    "snk_code, device_code, cycle_start) "
    "VALUES (?, ?, 'smoke', ?, '', '', ?, '问题X', '', ?, '', '是', "
    "'', '', '', '', '', '')", rows)
sl.commit()
sl.close()

# ---- 构建弹窗 ----
from PySide6.QtWidgets import QApplication
from windows.aftersale.stats_dialog import AfterSaleStatsDialog

app = QApplication([])
dlg = AfterSaleStatsDialog(
    {"cycle_start": "", "issue_type": "", "keyword": ""})
dlg.show()

# 等异步 worker 完成（刷新按钮重新可用且柱图有数据）
for _ in range(120):
    app.processEvents()
    if dlg._btn_refresh.isEnabled() and dlg._chart._data:
        break
    time.sleep(0.05)
app.processEvents()

assert dlg._btn_refresh.isEnabled(), "worker 未完成"
assert dlg._chart._data, "每日趋势无数据"
assert dlg._type_table.rowCount() > 0, "类型表为空"
assert dlg._region_list._rows, "地区列表为空"
assert dlg._kpis["total"][1].text() == str(len(rows)), "KPI 总数不符"

# ---- 点击联动：地区行 / 类型行 发射 apply_filter 并关闭 ----
from PySide6.QtWidgets import QTableWidgetItem
got = []
dlg.apply_filter.connect(lambda d: got.append(d))
dlg._on_region_clicked("广东")
assert got and got[-1] == {"keyword": "广东"}, "地区联动信号不符"
assert not dlg.isVisible(), "联动后应自动关闭"
dlg._on_region_clicked("未填地区")   # 无真实值：不发射
assert len(got) == 1, "未填地区不应发射联动"

dlg.show()
for _ in range(120):
    app.processEvents()
    if dlg._btn_refresh.isEnabled() and dlg._chart._data:
        break
    time.sleep(0.05)
it = dlg._type_table.item(0, 0)
dlg._on_type_cell_clicked(it)
assert got and got[-1]["issue_type"] == it.text(), "类型联动信号不符"
assert not dlg.isVisible(), "类型联动后应自动关闭"

# ---- set_filters 复用：更新范围后重新加载 ----
dlg.set_filters({"cycle_start": "", "issue_type": "", "keyword": ""})
for _ in range(120):
    app.processEvents()
    if dlg._btn_refresh.isEnabled() and dlg._chart._data:
        break
    time.sleep(0.05)
assert dlg._chart._data, "set_filters 后趋势未重新加载"

out = os.path.join(PROJECT_ROOT, "tools", "stats_dialog_smoke.png")
dlg.grab().save(out)
print("SMOKE_OK",
      "total=%s" % dlg._kpis["total"][1].text(),
      "days=%d" % len(dlg._chart._data),
      "regions=%d" % len(dlg._region_list._rows),
      "types=%d" % dlg._type_table.rowCount(),
      "link=region+type",
      "shot=%s" % out)
