# -*- coding: utf-8 -*-
"""S2 排序正确性冒烟：表头排序免重填充（sortItems）后三处一致性

断言：
1. 排序后第 r 行行内按钮对应的记录 id == 该行勾选列 UserRole 锚 id
   == _rows[r] 的 id（升序/降序各验一遍，逐行点击「编辑」「已解决」按钮验证）；
2. 勾选两行 → 排序 → 勾选集合（_checked_ids）不变，且勾选状态随行移动；
3. ops cellWidget 实例随行移动（同一记录排序前后按钮容器是同一对象）。

P1-1 返工（2026-09-06）追加：组合列排序键断言——
4. 跨年填写时间（2025-12-31 / 2026-01-02 / 2026-08-01）：显示文本截掉年份
   （"12-31 23:59"），按显示文本升序会错排为 id [2,3,1]（QA 复现），
   _SortKeyItem 原始键应恢复升序 [1,2,3] / 降序 [3,2,1]；
5. 位置组合列：排序键 = 球房+地区+桌号空格拼接（旧 _apply_sort 语义）；
6. 响应组合列：排序键 = 原始 response_time（空串排最前；显示文本 '—'
   会排最后且混入解决人次行，显示序 ≠ 键序）。
"""
import os, sys
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
conn = build_memory_db(2000, verbose=False)
adb._conn = lambda: conn
adb.recalc_cycle_starts()  # S3：物化列与真实口径对齐后再加载

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
import time as _t
deadline = _t.time() + 20
while _t.time() < deadline and getattr(page, "_total", 0) == 0:
    app.processEvents()
    _t.sleep(0.005)
assert page._total > 0, "数据未加载"

ops_col = _COL_CHECK + 1 + [k for k, _h, _w in TABLE_COLUMNS].index("ops")
sort_col = _COL_CHECK + 1 + [k for k, _h, _w in TABLE_COLUMNS].index("problem")

# 记录器：替换 _on_edit_rec / _quick_resolve_rec，点击按钮时不弹窗
edited, resolved = [], []
page._on_edit_rec = lambda rec: edited.append(rec.get("id"))
page._quick_resolve_rec = lambda rec: resolved.append(rec.get("id"))

def anchor(r):
    it = page._table.item(r, _COL_CHECK)
    return it.data(Qt.ItemDataRole.UserRole) if it is not None else None

def click_btn(r, text):
    w = page._table.cellWidget(r, ops_col)
    for b in w.findChildren(QPushButton):
        if b.text() == text:
            b.click()
            return True
    return False

fails = []

def verify(order_label):
    # ① 锚序 == _rows 序；② 按钮点击 → 记录 id == 该行锚
    for r in range(page._table.rowCount()):
        a = anchor(r)
        if a != page._rows[r].get("id"):
            fails.append(f"[{order_label}] 行 {r}: 锚 {a} != _rows[{r}].id "
                         f"{page._rows[r].get('id')}")
            return
    for r in range(page._table.rowCount()):
        edited.clear()
        if not click_btn(r, "编辑"):
            fails.append(f"[{order_label}] 行 {r} 无编辑按钮")
            return
        if edited != [anchor(r)]:
            fails.append(f"[{order_label}] 行 {r}: 编辑按钮命中 {edited} "
                         f"!= 锚 {anchor(r)}")
            return
    # 未解决行「已解决」按钮同样按记录定位
    resolved.clear()
    for r in range(page._table.rowCount()):
        if click_btn(r, "已解决"):
            if resolved != [anchor(r)]:
                fails.append(f"[{order_label}] 行 {r}: 已解决按钮命中 {resolved} "
                             f"!= 锚 {anchor(r)}")
            break

# 勾选两行（记录锚 + ops 容器实例），排序后核对随行移动
chk_rows = [2, min(7, page._table.rowCount() - 1)]
checked_ids_before = set()
widget_by_id = {}
for r in range(page._table.rowCount()):
    widget_by_id[anchor(r)] = page._table.cellWidget(r, ops_col)
for r in chk_rows:
    page._table.item(r, _COL_CHECK).setCheckState(Qt.CheckState.Checked)
    checked_ids_before.add(anchor(r))
app.processEvents()
assert set(page._checked_ids()) == checked_ids_before, "排序前勾选集合异常"

# 升序排序
page._on_click_header(sort_col)   # 首点升序
app.processEvents()
verify("升序")
if set(page._checked_ids()) != checked_ids_before:
    fails.append(f"[升序] 勾选集合变化: {set(page._checked_ids())} != {checked_ids_before}")
for r in range(page._table.rowCount()):
    a = anchor(r)
    if page._table.cellWidget(r, ops_col) is not widget_by_id[a]:
        fails.append(f"[升序] 行 {r}: ops cellWidget 未随行移动")
        break

# 降序排序（再点同列）
page._on_click_header(sort_col)
app.processEvents()
verify("降序")
if set(page._checked_ids()) != checked_ids_before:
    fails.append(f"[降序] 勾选集合变化: {set(page._checked_ids())} != {checked_ids_before}")
for r in range(page._table.rowCount()):
    a = anchor(r)
    if page._table.cellWidget(r, ops_col) is not widget_by_id[a]:
        fails.append(f"[降序] 行 {r}: ops cellWidget 未随行移动")
        break

# ---- P1-1 返工：跨年数据 + 组合列（填写时间/位置/响应）排序键断言 ----
import copy as _copy
_base_rec = _copy.deepcopy(page._rows[0])

def mk_rec(rid, **over):
    rec = dict(_base_rec)
    rec["id"] = rid
    rec.update(over)
    return rec

def col_of(key):
    return _COL_CHECK + 1 + [k for k, _h, _w in TABLE_COLUMNS].index(key)

def anchor_ids():
    return [anchor(r) for r in range(page._table.rowCount())]

# ① 跨年填写时间：显示文本截掉年份，按显示文本升序会得到 id [2,3,1]
#    （QA 复现的错序）；_SortKeyItem 原始键应恢复 [1,2,3] / 降序 [3,2,1]
xs = [
    mk_rec(101, created_at="2025-12-31 23:59:59"),
    mk_rec(102, created_at="2026-01-02 00:00:01"),
    mk_rec(103, created_at="2026-08-01 12:00:00"),
]
page._populate(xs)
app.processEvents()
created_col = col_of("created_at")
# 前置自检：显示文本确实不含年份（保证本断言真实覆盖年份丢失路径）
disp = [page._table.item(r, created_col).text().split("\n")[0]
        for r in range(3)]
assert disp == ["12-31 23:59", "01-02 00:00", "08-01 12:00"], \
    f"填写时间显示文本异常: {disp}"
page._on_click_header(created_col)   # 首点升序
app.processEvents()
if anchor_ids() != [101, 102, 103]:
    fails.append(f"[跨年·升序] 填写时间排序 {anchor_ids()} != [101,102,103]")
page._on_click_header(created_col)   # 再点降序
app.processEvents()
if anchor_ids() != [103, 102, 101]:
    fails.append(f"[跨年·降序] 填写时间排序 {anchor_ids()} != [103,102,101]")

# ② 位置组合列：排序键 = 球房+地区+桌号空格拼接（旧 _apply_sort 语义）
loc_rows = [
    mk_rec(201, room_name="乙房", region="", table_no="5"),
    mk_rec(202, room_name="甲房", region="华东", table_no="12"),
    mk_rec(203, room_name="甲房", region="华东", table_no="3"),
]
def loc_key(r):
    return " ".join(str(r.get(k) or "") for k in
                    ("room_name", "region", "table_no"))
expected_loc = [r["id"] for r in sorted(loc_rows, key=loc_key)]
page._populate(loc_rows)
app.processEvents()
loc_col = col_of("location")
page._on_click_header(loc_col)
app.processEvents()
if anchor_ids() != expected_loc:
    fails.append(f"[位置·升序] {anchor_ids()} != 旧键序 {expected_loc}")
page._on_click_header(loc_col)
app.processEvents()
if anchor_ids() != expected_loc[::-1]:
    fails.append(f"[位置·降序] {anchor_ids()} != 旧键序逆 {expected_loc[::-1]}")

# ③ 响应组合列：排序键 = 原始 response_time；空串排最前，而显示 '—'
#    （U+2014）按显示文本反而排最后——显示序 ≠ 键序，锁键语义
resp_rows = [
    mk_rec(301, response_time="", resolver="张三"),
    mk_rec(302, response_time="2026-01-01 09:00:00", resolver="李四"),
    mk_rec(303, response_time="2026-08-01 09:00:00", resolver="王五"),
]
expected_resp = [r["id"] for r in sorted(
    resp_rows, key=lambda r: str(r.get("response_time") or ""))]
assert expected_resp == [301, 302, 303], \
    f"响应键序预期异常: {expected_resp}"
page._populate(resp_rows)
app.processEvents()
resp_col = col_of("response_time")
page._on_click_header(resp_col)
app.processEvents()
if anchor_ids() != expected_resp:
    fails.append(f"[响应·升序] {anchor_ids()} != 键序 {expected_resp}")
page._on_click_header(resp_col)
app.processEvents()
if anchor_ids() != expected_resp[::-1]:
    fails.append(f"[响应·降序] {anchor_ids()} != 键序逆 {expected_resp[::-1]}")

if fails:
    print("FAIL:")
    for f in fails[:10]:
        print("  ", f)
    sys.stdout.flush()
    os._exit(1)
print("PASS: S2 排序后 按钮记录/勾选锚/_rows 三处一致，勾选集合与 cellWidget 均随行移动；"
      "P1-1 组合列（填写时间跨年/位置/响应）排序键恢复旧 _apply_sort 语义")
sys.stdout.flush()
os._exit(0)
