# -*- coding: utf-8 -*-
"""P0 真机回归：售后面板 RecordsPage 在 10 万行数据下的端到端操作耗时
走真实异步链路（_load_cycles_then_data → get_cycle_options → query_with_stats
→ _populate / 翻页 / 关键词筛选 / 统计弹窗数据），offscreen 渲染。
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
from PySide6.QtCore import QEventLoop, QTimer
app = QApplication([])

from database import aftersale_db as adb
from tools.stress_test.data_gen import build_memory_db
conn = build_memory_db(100000, verbose=False)
adb._conn = lambda: conn
# S3（2026-09-06）：data_gen 的 cycle_start 是简化口径（occurred 前 10 天
# 的 yyyy-MM-dd 串），与真实 cycle_start_of（yyyy/MM/dd 周期起点）不一致；
# 周期筛选已改为 cycle_start 物化列等值过滤，必须先重算物化列再查询
adb.recalc_cycle_starts()

from windows.aftersale.records import RecordsPage
page = RecordsPage()
page.resize(1440, 800)
page.show()
app.processEvents()

def wait_worker(worker, timeout=20000):
    """等待 worker 完成 + 消费 result_ready 槽（populate 等 UI 更新）"""
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

# 1) 完整首次加载：周期下拉 + 列表 + 指标卡统计（链式 worker：
#    周期 worker 完成 → _on_cycles_loaded → 再启动数据 worker，轮询数据就绪）
t0 = time.perf_counter()
page._load_cycles_then_data()
deadline = time.time() + 20
while time.time() < deadline and getattr(page, "_total", 0) == 0:
    app.processEvents()
    time.sleep(0.005)
t_load = (time.perf_counter() - t0) * 1000
print(f"[1] 首次加载(周期下拉+列表+统计+渲染): {t_load:.0f}ms  "
      f"表格行={page._table.rowCount()} 总记录={page._total} "
      f"页签={getattr(page, '_lbl_page', None) and page._lbl_page.text()}")

# 2) 翻页（第 3 页）
t0 = time.perf_counter()
while page._page_no < 3:
    page._step_page(1)
    wait_worker(page._worker)
t_next = (time.perf_counter() - t0) * 1000
print(f"[2] 连续翻页到第3页: {t_next:.0f}ms  当前页={page._page_no} "
      f"表格行={page._table.rowCount()}")

# 3) 关键词筛选（命中行数应显著少于总量）
page._search_edit.setText("校准")
t0 = time.perf_counter()
page._on_filter_changed()
wait_worker(page._worker)
t_filter = (time.perf_counter() - t0) * 1000
print(f"[3] 关键词筛选'校准': {t_filter:.0f}ms  命中={page._total} "
      f"表格行={page._table.rowCount()}")

# 4) 筛选后翻页（验证分页在筛选集内正确）
t0 = time.perf_counter()
page._step_page(1)
wait_worker(page._worker)
print(f"[4] 筛选后翻页: {(time.perf_counter()-t0)*1000:.0f}ms  "
      f"页={page._page_no} 行={page._table.rowCount()}")

# 5) 统计弹窗数据（query_stats_detail 全量聚合）
page._search_edit.setText("")
t0 = time.perf_counter()
detail = adb.query_stats_detail()
t_detail = (time.perf_counter() - t0) * 1000
print(f"[5] 统计弹窗数据(100k 全量聚合): {t_detail:.0f}ms  "
      f"total={detail['summary']['total']} daily天数={len(detail['daily'])} "
      f"regions={len(detail['regions'])} types={len(detail['types'])}")

# 6) 周期下拉选项
t0 = time.perf_counter()
opts = adb.get_cycle_options()
print(f"[6] 周期下拉选项: {(time.perf_counter()-t0)*1000:.0f}ms  {len(opts)} 个周期 "
      f"(最新 {opts[0] if opts else '-'})")

# 断言：默认带当前周期筛选（10万条中当前周期 ~1.1万），分页/渲染正常即可
# 筛选会重置页号，断言数据加载与表格渲染正确即可（页大小现为 60）
ok = page._total > 0 and page._table.rowCount() == 60
print("PASS" if ok else "FAIL: 数据未正确加载")
