# -*- coding: utf-8 -*-
"""填充/排序耗时分解：区分纯填充、重绘冲刷、DeferredDelete 清理、纯 sortItems"""
import os, sys, time, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import importlib.util as _iu
_spec = _iu.find_spec('PySide6')
if _spec is not None:
    _pkg = list(getattr(_spec, 'submodule_search_locations', None) or [])[0]
    for _d in (_pkg, os.path.dirname(_pkg),
               os.path.join(os.environ.get('SystemRoot', r'C:/Windows'), 'System32')):
        if os.path.isdir(_d):
            try: os.add_dll_directory(_d)
            except OSError: pass
    os.environ['QT_PLUGIN_PATH'] = os.path.join(_pkg, 'plugins')

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop, QTimer, QEvent, Qt
app = QApplication([])

from database import aftersale_db as adb
from tools.stress_test.data_gen import build_memory_db
conn = build_memory_db(2000, verbose=False)
adb._conn = lambda: conn
adb.recalc_cycle_starts()

from windows.aftersale.records import RecordsPage
from windows.aftersale.common import TABLE_COLUMNS, _COL_CHECK

page = RecordsPage()
page.resize(1440, 800)
page.show()
app.processEvents()

def wait_worker(worker, timeout=20000):
    if worker is None:
        app.processEvents()
        return
    if worker.isRunning():
        loop = QEventLoop()
        QTimer.singleShot(timeout, loop.quit)
        worker.finished.connect(loop.quit)
        loop.exec()
    for _ in range(6):
        app.processEvents()

page._load_cycles_then_data()
deadline = __import__("time").time() + 20
while __import__("time").time() < deadline and getattr(page, "_total", 0) == 0:
    app.processEvents()
rows = list(page._rows)
print(f"rows={len(rows)}")

def med(fn, n=7):
    s = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        s.append((time.perf_counter() - t0) * 1000)
    return statistics.median(s), min(s)

# 1) 纯填充（不含重绘冲刷；_populate 内部含 DeferredDelete 清理）
m1a, m1b = med(lambda: page._populate(rows))
print(f"纯 _populate(含清僵尸): median={m1a:.1f}ms min={m1b:.1f}ms")

# 2) 填充 + 重绘冲刷
def fill_flush():
    page._populate(rows)
    for _ in range(3):
        app.processEvents()
m2a, m2b = med(fill_flush)
print(f"_populate+冲刷: median={m2a:.1f}ms min={m2b:.1f}ms")

# 3) 对照：手工清一次 DeferredDelete 的单价
def dd_only():
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
m3a, m3b = med(dd_only)
print(f"单独 sendPostedEvents(DeferredDelete): median={m3a:.1f}ms min={m3b:.1f}ms")

# 4) 纯 sortItems（不 _on_click_header，无重绘冲刷）
sort_col = _COL_CHECK + 1 + [k for k, _h, _w in TABLE_COLUMNS].index("problem")
page._populate(rows)
app.processEvents()
asc = True
def pure_sort():
    global asc
    page._table.sortItems(sort_col, Qt.SortOrder.AscendingOrder if asc
                          else Qt.SortOrder.DescendingOrder)
    asc = not asc
m4a, m4b = med(pure_sort)
print(f"纯 table.sortItems(problem列,普通item): median={m4a:.1f}ms min={m4b:.1f}ms")

# 4b) 纯 sortItems 组合列（created_at 列，_SortKeyItem.__lt__ 键比较路径）
combo_col = _COL_CHECK + 1 + [k for k, _h, _w in TABLE_COLUMNS].index("created_at")
asc2 = True
def pure_sort_combo():
    global asc2
    page._table.sortItems(combo_col, Qt.SortOrder.AscendingOrder if asc2
                          else Qt.SortOrder.DescendingOrder)
    asc2 = not asc2
m4ca, m4cb = med(pure_sort_combo)
print(f"纯 table.sortItems(created_at列,_SortKeyItem): median={m4ca:.2f}ms min={m4cb:.2f}ms")

# 5) _on_click_header（setSortIndicator + _sort_table + 锚序回读）不含冲刷
def hdr_only():
    page._on_click_header(sort_col)
m5a, m5b = med(hdr_only)
print(f"_on_click_header(不含冲刷): median={m5a:.1f}ms min={m5b:.1f}ms")

# 6) _on_click_header + 冲刷
def hdr_flush():
    page._on_click_header(sort_col)
    for _ in range(3):
        app.processEvents()
m6a, m6b = med(hdr_flush)
print(f"_on_click_header+冲刷: median={m6a:.1f}ms min={m6b:.1f}ms")

# 7) 冲刷单价（空跑）
def flush_only():
    for _ in range(3):
        app.processEvents()
m7a, m7b = med(flush_only)
print(f"单独 processEvents×3: median={m7a:.1f}ms min={m7b:.1f}ms")

sys.stdout.flush()
os._exit(0)
