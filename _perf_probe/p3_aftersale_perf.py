# -*- coding: utf-8 -*-
"""售后面板性能复测探针（S1~S5 改造后）

指标（对照 docs/售后面板性能调查报告2026-09-06.md 第五节验证口径）：
- B4 周期下拉选项 get_cycle_options：基线 78.6ms → 目标 ≤20ms（S1）
- B2 query_with_stats(当前周期)：基线 24.2ms → 目标 ≤6ms（S1+S3）
- B6 get_field_candidates：基线 94.2ms → 目标 缓存命中 ≤1ms（S4）
- 填充 60 行 _populate：基线 36~53ms → 目标 ≤45ms（S5）
- 表头排序 _on_click_header：基线 59.7ms → 目标 ≤10ms（S2）
- 首次加载端到端（周期下拉+列表+统计+渲染）

环境：内存库 100k（tools/stress_test/data_gen），offscreen 渲染。
注意：data_gen 的 cycle_start 为简化口径，S3 物化列过滤前必须先 recalc。
"""
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

from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtCore import QEventLoop, QTimer
app = QApplication([])

from database import aftersale_db as adb
from tools.stress_test.data_gen import build_memory_db

conn = build_memory_db(100000, verbose=False)
adb._conn = lambda: conn
# S3：data_gen 的 cycle_start 是简化口径（yyyy-MM-dd），周期筛选已改为
# 物化列等值过滤，先重算物化列再测（与生产「保存/首载重算后」口径一致）
t0 = time.perf_counter()
n_upd = adb.recalc_cycle_starts()
print(f"[S3] recalc_cycle_starts: 更新 {n_upd} 行，耗时 "
      f"{(time.perf_counter()-t0)*1000:.0f}ms（幂等，二次应为 0）")
t0 = time.perf_counter()
n_upd2 = adb.recalc_cycle_starts()
print(f"[S3] recalc 二次执行: 更新 {n_upd2} 行，耗时 "
      f"{(time.perf_counter()-t0)*1000:.0f}ms")

# 周期筛选索引使用情况（S3 核心：全表扫 → 索引访问）
plan = conn.execute(
    "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM aftersale_records "
    "WHERE cycle_start = '2026/08/04'").fetchall()
print(f"[S3] EXPLAIN(周期等值过滤): {plan[0][3]}")

# ---- B4：周期下拉选项（S1 缓存收益） ----
samples = []
for _ in range(3):
    t0 = time.perf_counter()
    opts = adb.get_cycle_options()
    samples.append((time.perf_counter() - t0) * 1000)
print(f"[B4] get_cycle_options: median={statistics.median(samples):.1f}ms "
      f"min={min(samples):.1f}ms  周期数={len(opts)}  (目标 ≤20ms)")

# ---- B2：query_with_stats 当前周期（S1+S3 收益） ----
cur = adb.current_cycle_start()
if cur not in opts:
    cur = opts[0] if opts else ""
samples = []
for _ in range(5):
    t0 = time.perf_counter()
    total, rows, stats = adb.query_with_stats(1, 60, cycle_start=cur)
    samples.append((time.perf_counter() - t0) * 1000)
print(f"[B2] query_with_stats(周期 {cur}): median={statistics.median(samples):.1f}ms "
      f"min={min(samples):.1f}ms  total={total}  (目标 ≤6ms)")

# ---- B6：动态候选（S4 缓存收益） ----
t0 = time.perf_counter()
adb.get_field_candidates()
t_first = (time.perf_counter() - t0) * 1000
samples = []
for _ in range(5):
    t0 = time.perf_counter()
    adb.get_field_candidates()
    samples.append((time.perf_counter() - t0) * 1000)
print(f"[B6] get_field_candidates: 首次(建缓存)={t_first:.1f}ms "
      f"命中 median={statistics.median(samples):.3f}ms  (目标 命中 ≤1ms)")

# ---- UI 链路：RecordsPage 首载 / 填充 / 表头排序 ----
from windows.aftersale.records import RecordsPage
from windows.aftersale.common import TABLE_COLUMNS, _COL_CHECK

page = RecordsPage()
page.resize(1440, 800)
page.show()
app.processEvents()

def wait_worker(worker, timeout=30000):
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

# 首次加载（含 S3 首载兜底 worker → 周期下拉 → 数据 + 渲染）
page._cycles_loaded = False
t0 = time.perf_counter()
page._load_cycles_then_data()
deadline = time.time() + 30
while time.time() < deadline and getattr(page, "_total", 0) == 0:
    app.processEvents()
    time.sleep(0.005)
print(f"[首载] 周期下拉+列表+统计+渲染: {(time.perf_counter()-t0)*1000:.0f}ms  "
      f"行={page._table.rowCount()} 总数={page._total}")

# 填充 60 行（S5 收益）
def _fill():
    page._populate(page._rows)
    for _ in range(3):
        app.processEvents()
samples = []
for _ in range(5):
    t0 = time.perf_counter()
    _fill()
    samples.append((time.perf_counter() - t0) * 1000)
print(f"[填充] _populate 60 行: median={statistics.median(samples):.1f}ms "
      f"min={min(samples):.1f}ms  (目标 ≤45ms)")

# 表头排序（S2 收益）：问题列（TABLE_COLUMNS 第 5 列）升/降各测
sort_col = _COL_CHECK + 1 + [k for k, _h, _w in TABLE_COLUMNS].index("problem")
def _sort():
    page._on_click_header(sort_col)
    for _ in range(3):
        app.processEvents()
samples = []
for i in range(6):
    t0 = time.perf_counter()
    _sort()
    samples.append((time.perf_counter() - t0) * 1000)
print(f"[排序] 表头点击(升/降交替): median={statistics.median(samples):.1f}ms "
      f"min={min(samples):.1f}ms  (目标 ≤10ms)")

# 填充后连续 7 代的按钮存活数（S5-b 僵尸控件清除）
ops_col = _COL_CHECK + 1 + [k for k, _h, _w in TABLE_COLUMNS].index("ops")
for _ in range(7):
    page._populate(page._rows)
    app.processEvents()
n_btn = sum(
    len(page._table.cellWidget(r, ops_col).findChildren(QPushButton))
    for r in range(page._table.rowCount())
    if page._table.cellWidget(r, ops_col) is not None)
print(f"[S5-b] 连续填充 7 次后按钮存活数: {n_btn} (旧实现堆积至 ~1112，应≈单代)")

sys.stdout.flush()
os._exit(0)
