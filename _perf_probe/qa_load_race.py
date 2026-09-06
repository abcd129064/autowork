# -*- coding: utf-8 -*-
"""QA 边界验证 3：加载链竞态复现（_fill_sort_decompose 首跑 rows=0 的根因）

场景：showEvent 触发链 A（recalc worker → cycles → data）与脚本显式再调
_load_cycles_then_data 触发链 B 并发交叠 —— 复现 1/7 概率的「最终无数据」。
用 LoggingWorker 捕获每个 worker 的 error 信号与 result 丢弃情况。
"""
import os, sys, time
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
app = QApplication([])

from database import aftersale_db as adb
from tools.stress_test.data_gen import build_memory_db
import windows.aftersale.records as records_mod
from windows.aftersale.records import RecordsPage

_orig_worker_cls = records_mod.AftersaleDBWorker


class LoggingWorker(_orig_worker_cls):
    """记录 error 信号 payload 与被中断丢弃的 result"""
    def __init__(self, func, *a, **k):
        super().__init__(func, *a, **k)
        self._fname = getattr(func, "__name__", str(func))
        self.error.connect(lambda m: print(f"    WORKER_ERROR[{self._fname}]: {m}"))
        self.result_ready.connect(
            lambda _r: print(f"    result_ready[{self._fname}] "
                             f"interrupted={self.isInterruptionRequested()}"))


records_mod.AftersaleDBWorker = LoggingWorker

N = int(sys.argv[1]) if len(sys.argv) > 1 else 8
bad = 0
for it in range(N):
    conn = build_memory_db(2000, verbose=False)
    adb._conn = lambda: conn
    adb.recalc_cycle_starts()
    page = RecordsPage()
    page.resize(1440, 800)
    print(f"[iter {it}] show()...")
    page.show()          # showEvent → 链 A
    app.processEvents()
    page._load_cycles_then_data()   # 显式 → 链 B
    t0 = time.time()
    while time.time() - t0 < 10 and getattr(page, "_total", 0) == 0:
        app.processEvents()
        time.sleep(0.005)
    ok = page._total > 0 and page._table.rowCount() > 0
    if not ok:
        bad += 1
        print(f"  [iter {it}] FAIL: total={page._total} rows={page._table.rowCount()} "
              f"stats_label={page._lbl_stats.text()!r}")
    else:
        print(f"  [iter {it}] ok: total={page._total} rows={page._table.rowCount()}")
    page.hide()
    page.deleteLater()
    app.processEvents()
    conn.close()

print(f"汇总: {N} 轮中失败 {bad} 轮")
sys.stdout.flush()
os._exit(0 if bad == 0 else 1)
