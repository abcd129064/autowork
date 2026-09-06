# -*- coding: utf-8 -*-
"""QA 边界验证 2：排序态下的组合操作链（S2 最脆弱不变量）

链路：加载 → 表头排序 → 翻页 →（排序态保持）→ 关键词筛选 → 排序反转 →
批量勾选 → 勾选集合随排序稳定 → 行号入口（右键/双击）与按钮入口一致 →
一键解决(真实写库) → 刷新后锚序恢复 → 空表填充/空表排序 → 行数收缩。

断言全程：表格行 r 的勾选列 UserRole 锚 == _rows[r]['id']，
且锚序与排序列文本序一致（升/降）。
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

from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtCore import Qt, QEventLoop, QTimer
app = QApplication([])

from database import aftersale_db as adb
from tools.stress_test.data_gen import build_memory_db
conn = build_memory_db(300, verbose=False)
adb._conn = lambda: conn
adb.recalc_cycle_starts()

from windows.aftersale.records import RecordsPage
from windows.aftersale.common import TABLE_COLUMNS, _COL_CHECK

# 单链路触发：不 show()（避免 showEvent 双触发竞态），直接走加载链
page = RecordsPage()
page.resize(1440, 800)
fails = []

def wait_load(timeout=15, wait_total=True):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        time.sleep(0.005)
        if not wait_total:
            if not page._worker or not page._worker.isRunning():
                app.processEvents()
                return True
        elif getattr(page, "_total", 0) > 0:
            # 再消化事件让 _on_loaded 全部落地
            for _ in range(4):
                app.processEvents()
            return True
    return False

def anchor(r):
    it = page._table.item(r, _COL_CHECK)
    return it.data(Qt.ItemDataRole.UserRole) if it is not None else None

def check_invariant(label):
    n = page._table.rowCount()
    if n != len(page._rows):
        fails.append(f"[{label}] 行数 {n} != _rows {len(page._rows)}")
        return
    for r in range(n):
        if anchor(r) != page._rows[r].get("id"):
            fails.append(f"[{label}] 行 {r}: 锚 {anchor(r)} != _rows.id "
                         f"{page._rows[r].get('id')}")
            return

def col_text(r, key):
    col = _COL_CHECK + 1 + [k for k, _h, _w in TABLE_COLUMNS].index(key)
    it = page._table.item(r, col)
    return it.text() if it else ""

sort_col = _COL_CHECK + 1 + [k for k, _h, _w in TABLE_COLUMNS].index("problem")
ops_col = _COL_CHECK + 1 + [k for k, _h, _w in TABLE_COLUMNS].index("problem") - 1 \
    if False else _COL_CHECK + 1 + [k for k, _h, _w in TABLE_COLUMNS].index("ops")

# ---- 1) 初载 ----
page._load_cycles_then_data()
assert wait_load() and page._total > 0, "初载失败"
check_invariant("初载")
first_page_ids = [r.get("id") for r in page._rows]
print(f"[1] 初载: total={page._total} 行={page._table.rowCount()}")

# ---- 2) 排序升序：锚序与文本序一致 ----
page._on_click_header(sort_col)  # 首点升序
app.processEvents()
check_invariant("排序·升序")
def qt_sorted_asc():
    col = sort_col
    return all(not (page._table.item(r + 1, col) < page._table.item(r, col))
               for r in range(page._table.rowCount() - 1))
if not qt_sorted_asc():
    fails.append("[排序·升序] Qt operator< 非单调（升）")
# 排序后行号入口（右键/双击路径）应命中该行锚
row3_anchor = anchor(3)
hit = {}
page._on_edit_rec = lambda rec: hit.setdefault("id", rec.get("id"))
page._on_edit(3)
if hit.get("id") != row3_anchor:
    fails.append(f"[排序·行号入口] _on_edit(3) 命中 {hit.get('id')} != 锚 {row3_anchor}")
print("[2] 升序排序 + 行号入口: OK" if not fails else "[2] 见失败项")

# ---- 3) 排序态翻页 ----
page._step_page(1)
assert wait_load()
check_invariant("翻页后·排序态保持")
if not qt_sorted_asc():
    fails.append("[翻页后·排序态保持] Qt operator< 非单调（_on_loaded 未就地重排）")
cur_ids = [r.get("id") for r in page._rows]
if cur_ids == first_page_ids and page._total > page._page_size:
    fails.append("[翻页后] 第2页数据与第1页相同")
print(f"[3] 翻页(排序态): 页={page._page_no} 行={page._table.rowCount()}")

# ---- 4) 关键词筛选（排序态） ----
page._search_edit.setText("校准")
page._on_filter_changed()
assert wait_load()
check_invariant("筛选后·排序态")
if page._table.rowCount() > 1 and not qt_sorted_asc():
    fails.append("[筛选后·排序态] Qt operator< 非单调（升）")
print(f"[4] 筛选后(排序态): 命中={page._total}")

# ---- 5) 勾选 → 排序反转 → 勾选集合稳定 ----
n_rows = page._table.rowCount()
for r in (0, min(3, n_rows - 1)):
    page._table.item(r, _COL_CHECK).setCheckState(Qt.CheckState.Checked)
app.processEvents()
checked_before = set(page._checked_ids())
page._on_click_header(sort_col)  # 再点 = 降序
app.processEvents()
check_invariant("排序反转")
if set(page._checked_ids()) != checked_before:
    fails.append(f"[排序反转] 勾选集合变化 {set(page._checked_ids())} != {checked_before}")
desc_ok = all(not (page._table.item(r, sort_col) < page._table.item(r + 1, sort_col))
              for r in range(page._table.rowCount() - 1))
if not desc_ok:
    fails.append("[排序反转] Qt operator< 非单调（降）")
print(f"[5] 勾选+排序反转: 勾选 {len(checked_before)} 项保持")

# ---- 6) 按钮入口点击（编辑）与锚一致 ----
edited = []
page._on_edit_rec = lambda rec: edited.append(rec.get("id"))
for r in (0, 1):
    w = page._table.cellWidget(r, ops_col)
    for b in w.findChildren(QPushButton):
        if b.text() == "编辑":
            b.click()
            break
if edited != [anchor(0), anchor(1)]:
    fails.append(f"[按钮入口] 编辑命中 {edited} != [{anchor(0)}, {anchor(1)}]")
print(f"[6] 按钮入口: {edited}")

# ---- 7) 一键解决（真实写库 + 刷新链路）----
resolved_before = conn.execute(
    "SELECT COUNT(*) FROM aftersale_records WHERE resolved='是'").fetchone()[0]
rec0 = next((r for r in page._rows if str(r.get("resolved")) != "是"), None)
assert rec0 is not None, "当前页无未解决记录"
page._on_edit_rec = lambda rec: None  # 还原，防干扰
_loaded_calls = [0]
_orig_on_loaded = page._on_loaded
page._on_loaded = lambda result: (_loaded_calls.__setitem__(0, _loaded_calls[0] + 1),
                                  _orig_on_loaded(result))
page._quick_resolve_rec(rec0)
_t0 = time.time()
while time.time() - _t0 < 15 and _loaded_calls[0] == 0:
    app.processEvents()
    time.sleep(0.005)
app.processEvents()
resolved_after = conn.execute(
    "SELECT COUNT(*) FROM aftersale_records WHERE resolved='是'").fetchone()[0]
if resolved_after != resolved_before + 1:
    fails.append(f"[一键解决] 库内已解决数 {resolved_before}->{resolved_after}")
check_invariant("一键解决刷新后")
print(f"[7] 一键解决: 已解决 {resolved_before}->{resolved_after}")

# ---- 8) 空表填充 / 空表排序 / 行数收缩 ----
page._populate([])
app.processEvents()
if page._table.rowCount() != 0:
    fails.append(f"[空表] rowCount={page._table.rowCount()} != 0")
page._table.sortItems(sort_col, Qt.SortOrder.AscendingOrder)  # 空表 sortItems
app.processEvents()
page._on_click_header(sort_col)  # 空数据下点表头
app.processEvents()
# 行数收缩：60 → 10
shrink = [dict(r) for r in page._rows[:10]] if page._rows else []
page._populate(shrink)
app.processEvents()
if page._table.rowCount() != len(shrink):
    fails.append(f"[行数收缩] rowCount={page._table.rowCount()} != {len(shrink)}")
check_invariant("行数收缩")
print(f"[8] 空表/收缩: rowCount={page._table.rowCount()}")

if fails:
    print(f"FAIL {len(fails)} 项:")
    for f in fails:
        print("  ", f)
    sys.stdout.flush()
    os._exit(1)
print("PASS: 排序态组合操作链（翻页/筛选/勾选/一键解决/空表/收缩）锚序一致")
sys.stdout.flush()
os._exit(0)
