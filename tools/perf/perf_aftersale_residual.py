# -*- coding: utf-8 -*-
"""售后面板性能残余复测 harness：委托化后填充/排序/弹窗/DB 链路量化。

背景：docs/售后面板性能调查报告2026-09-06.md 的 S1/S2/S3/S4/S5/S6 已落地，
本 harness 复测落地后现状并为 docs/管理面板性能调查报告2026-09-25.md
的售后章节提供量化证据。
用法：C:/Users/shen_zhe/miniconda3/python.exe tools/perf/perf_aftersale_residual.py
产物：tools/_scratch/perf_aftersale_residual.txt（不入库）

隔离措施：table_db.DB_PATH/_conn 重定向临时库、backend 强制 SQLite、
load_cycle_mode 桩、get_app_dir 重定向 scratch（不碰真实 config/database）。
"""
import os
import sys

# ---- Qt 运行时引导（docs/Qt内联引导说明.md 标准模板，必须在 import PySide6 前） ----
import os as _os
import importlib.util as _qt_iu
_qt_handles = []
try:
    _qt_spec = _qt_iu.find_spec('PySide6')
    if _qt_spec is not None:
        _qt_locs = list(getattr(_qt_spec, 'submodule_search_locations', None) or [])
        if _qt_locs:
            _qt_pkg = _qt_locs[0]
            for _d in (_qt_pkg, _os.path.dirname(_qt_pkg),
                       _os.path.join(_os.environ.get('SystemRoot', r'C:\Windows'), 'System32')):
                if _os.path.isdir(_d):
                    try:
                        _qt_handles.append(_os.add_dll_directory(_d))
                    except OSError:
                        pass
            _os.environ['QT_PLUGIN_PATH'] = _os.path.join(_qt_pkg, 'plugins')
            _os.environ.setdefault('QT_QPA_PLATFORM_PLUGIN_PATH',
                                   _os.path.join(_qt_pkg, 'plugins', 'platforms'))
except Exception:
    pass
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# tools/perf/ → 上溯三层 = 仓库根（AGENTS §2.4）
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "tools", "_scratch")
os.makedirs(OUT_DIR, exist_ok=True)

import time
import sqlite3
import statistics
from datetime import datetime, timedelta

import core.acrylic_patch  # noqa: F401
from PySide6.QtWidgets import QApplication

from database import backend, table_db, schema
import database.aftersale_db as adb

backend._mysql_settings_cache = {"enabled": False}

DB_PATH = os.path.join(OUT_DIR, "perf_as_tmp.db")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
table_db.DB_PATH = DB_PATH
table_db._conn = None

APP_DIR = os.path.join(OUT_DIR, "perf_as_appdir")
os.makedirs(APP_DIR, exist_ok=True)


def _stub_get_app_dir(*a, **k):
    return APP_DIR


import core.app_paths
core.app_paths.get_app_dir = _stub_get_app_dir
for _m in list(sys.modules.values()):
    if _m is not None and getattr(_m, "get_app_dir", None) is not None \
            and hasattr(_m, "__name__") and _m.__name__.startswith(("windows.", "core.")):
        _m.get_app_dir = _stub_get_app_dir

# 固定周期模式（tests 同款桩）
adb.load_cycle_mode = lambda: {"type": "mon", "start": "", "span": 7}

# ==================== 造数 ====================

_N = 3000


def _build_db():
    sl = sqlite3.connect(DB_PATH)
    sl.executescript(schema.to_sqlite_ddl("aftersale_records"))
    sl.executescript(schema.to_sqlite_ddl("sync_meta"))
    sl.commit()
    d0 = datetime.now() - timedelta(days=60)
    rows = []
    for i in range(_N):
        day = (d0 + timedelta(days=i % 60)).strftime("%Y/%m/%d")
        rows.append((
            f"{day} 10:00:00", day.replace("/", "-"), f"填写人{i % 8}", adb.ISSUE_TYPES[i % len(adb.ISSUE_TYPES)],
            f"289-{i % 40:02d}", f"球房{i % 25}", f"区域{i % 6}", f"问题{i % 30}",
            "原因", "是" if i % 3 == 0 else "否", "否", "是",
            "已更换配件", f"解决人{i % 5}", "30分钟", "", f"DEV{i % 40}",
            day,
        ))
    sl.executemany(
        "INSERT INTO aftersale_records "
        "(created_at, occurred_at, creator, issue_type, table_no, room_name, "
        "region, problem, cause, resolved, is_initiative, is_our_problem, "
        "solution, resolver, response_time, snk_code, device_code, cycle_start) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    sl.commit()
    sl.close()


_build_db()

# ==================== 测量 ====================

app = QApplication.instance() or QApplication(sys.argv)

_LINES = []


def out(msg=""):
    print(msg)
    _LINES.append(msg)


def _med(fn, repeat=3):
    ts = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts), ts


def _row(i):
    day = (datetime.now() - timedelta(days=i % 60)).strftime("%Y-%m-%d")
    return {"id": i + 1, "created_at": f"{day} 10:00:00", "occurred_at": day,
            "creator": f"填写人{i % 8}", "issue_type": "硬件问题",
            "table_no": f"289-{i % 40:02d}", "room_name": f"球房{i % 25}",
            "region": f"区域{i % 6}", "problem": f"问题{i % 30}", "cause": "原因",
            "resolved": "是" if i % 3 == 0 else "否", "is_initiative": "否",
            "is_our_problem": "是", "solution": "已更换配件",
            "resolver": f"解决人{i % 5}", "response_time": "30分钟",
            "snk_code": "", "device_code": f"DEV{i % 40}", "cycle_start": day,
            "remark": ""}


def main() -> int:
    from windows.aftersale.records import RecordsPage
    from windows.aftersale.dialogs import EditRecordDialog

    out(f"环境: PySide6 {__import__('PySide6').__version__} / offscreen / "
        f"临时库 {_N} 行售后记录 / {datetime.now():%Y-%m-%d %H:%M:%S}")

    out("\n== A. 页面构造与填充（对照 09-06 报告基线） ==")
    med, all3 = _med(lambda: RecordsPage(), repeat=3)
    out(f"A1 RecordsPage 构造（09-06 面板窗口 196.9ms 的一部分） {med:7.1f} ms   all={all3}")

    page = RecordsPage()
    rows60 = [_row(i) for i in range(60)]
    med, all3 = _med(lambda: page._populate(rows60), repeat=3)
    out(f"A2 _populate 60 行（委托化后；09-06 基线 36~53ms，注释声称 8ms） {med:7.1f} ms   all={all3}")

    # S2 排序验证：Qt sortItems 就地移动，不再重建
    page._populate(rows60)
    page._sort_col = 3  # 任意数据列
    page._sort_asc = True
    med, all3 = _med(lambda: page._sort_table(), repeat=3)
    out(f"A3 表头排序 _sort_table（S2 后；09-06 基线 59.7ms）      {med:7.1f} ms   all={all3}")

    out("\n== B. 弹窗成本 ==")
    rec = _row(0)
    med, all3 = _med(lambda: EditRecordDialog(dict(rec), page), repeat=3)
    out(f"B1 EditRecordDialog init（09-06 基线 30.1ms）           {med:7.1f} ms   all={all3}")
    dlg = EditRecordDialog(dict(rec), page)
    med, all3 = _med(lambda: dlg.form.collect(), repeat=3)
    out(f"B2 AftersaleForm.collect（09-06 基线 5.8ms）           {med:7.1f} ms   all={all3}")

    out("\n== C. DB 链路（S1/S3/S4 落地回归，内存库 3k 行） ==")
    med, all3 = _med(lambda: adb.query_with_stats(1, 60), repeat=3)
    out(f"C1 query_with_stats 全部周期（09-06 基线 11.6ms）       {med:7.1f} ms   all={all3}")
    day = (datetime.now() - timedelta(days=1)).strftime("%Y/%m/%d")
    med, all3 = _med(lambda: adb.query_with_stats(1, 60, cycle_start=day), repeat=3)
    out(f"C2 query_with_stats 周期筛选（S3 物化列；基线 24.2ms）   {med:7.1f} ms   all={all3}")
    med, all3 = _med(lambda: adb.get_cycle_options(), repeat=3)
    out(f"C3 get_cycle_options（S1 缓存后；基线 78.6ms）          {med:7.1f} ms   all={all3}")
    t0 = time.perf_counter()
    adb.get_field_candidates()
    first = (time.perf_counter() - t0) * 1000
    med, all3 = _med(lambda: adb.get_field_candidates(), repeat=3)
    out(f"C4 get_field_candidates 首次 {first:6.1f} ms / S4 缓存命中 {med:7.1f} ms"
        f"（09-06 基线 94.2ms）")

    out("\n说明：offscreen 无 GPU/渲染；周期筛选耗时与数据分布相关，绝对值仅作量级参考。")
    path = os.path.join(OUT_DIR, "perf_aftersale_residual.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(_LINES) + "\n")
    print(f"\n结果已写 {path}")
    return 0


if __name__ == "__main__":
    rc = main()
    os._exit(rc)
