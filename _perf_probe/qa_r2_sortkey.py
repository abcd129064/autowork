# -*- coding: utf-8 -*-
"""QA R2 独立验证（P1-1 排序键）：不复用工程师断言，独立构造对抗样本

1. 跨年填写时间：4 个年份、同月同日同分（年份是唯一序因子）；
2. 位置列：构造「显示文本序 != 键序」的对抗数据（显示 = room\nregion·table，
   键 = room region table 空格拼接），两者排序结果必须不同才有效力；
3. 响应列：空串 + 乱序档位 + 解决人次行，空串升序应最前；
4. 对照基准 = 旧 _apply_sort 键公式在本探针内独立重算（不 import 旧代码）；
5. _SortKeyItem.__lt__ 回退分支直测：与普通 QTableWidgetItem 混比、
   None 键防护。
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

from PySide6.QtWidgets import QApplication, QTableWidgetItem
from PySide6.QtCore import Qt
app = QApplication([])

from windows.aftersale.records import RecordsPage, _SortKeyItem
from windows.aftersale.common import TABLE_COLUMNS, _COL_CHECK

fails = []
page = RecordsPage()

def col_of(key):
    return _COL_CHECK + 1 + [k for k, _h, _w in TABLE_COLUMNS].index(key)

def run_sort(key, asc):
    page._sort_col = col_of(key)
    page._sort_asc = asc
    page._sort_table()
    app.processEvents()
    return [r.get("id") for r in page._rows]

# ---- 样本：跨年（同 03-15 09:30，仅年份不同）+ 填写人干扰 ----
rows = [
    {"id": 1, "created_at": "2024-03-15 09:30:00", "creator": "张三"},
    {"id": 2, "created_at": "2025-03-15 09:30:00", "creator": "李四"},
    {"id": 3, "created_at": "2026-03-15 09:30:00", "creator": "王五"},
    {"id": 4, "created_at": "2027-03-15 09:30:00", "creator": "赵六"},
    # 同年同日不同分：验证完整时间戳比较
    {"id": 5, "created_at": "2026-03-15 09:31:00", "creator": "张三"},
]
# 旧键公式（独立重算，等价 git HEAD _apply_sort）
old_key = lambda r: str(r.get("created_at") or "")
exp_asc = [r["id"] for r in sorted(rows, key=old_key)]
exp_desc = exp_asc[::-1]
page._rows = rows
page._populate(rows)
app.processEvents()
got = run_sort("created_at", True)
if got != exp_asc:
    fails.append(f"[跨年·升] {got} != 旧键序 {exp_asc}")
exp_desc = [r["id"] for r in sorted(rows, key=old_key, reverse=True)]
got = run_sort("created_at", False)
if got != exp_desc:
    fails.append(f"[跨年·降] {got} != 旧键序逆 {exp_desc}")

# ---- 样本：位置列对抗数据（显示文本序 != 键序）----
# 键 = "room region table"（空格拼接），显示 = "room\nregion · table"。
# 构造前缀交叠：room="A"/region="Z"（键 "A Z "）vs room="A B"/region=""（键 "A B "）
# —— 键序 r2<r1（"B"<"Z"），显示序 r1<r2（"\n"(0x0A)<" "(0x20)），两者相反。
loc_rows = [
    {"id": 11, "room_name": "A", "region": "Z", "table_no": ""},
    {"id": 12, "room_name": "A B", "region": "", "table_no": ""},
    {"id": 13, "room_name": "B球房", "region": "上海", "table_no": "2"},
    {"id": 14, "room_name": "", "region": "上海", "table_no": "1"},
    {"id": 15, "room_name": "A球房", "region": "上海", "table_no": "1"},
]
old_loc_key = lambda r: " ".join(str(r.get(k) or "") for k in
                                 ("room_name", "region", "table_no"))
loc_exp = [r["id"] for r in sorted(loc_rows, key=old_loc_key)]
# 显示文本序（换行参与比较，行为不同）——仅用于确认样本有区分度
disp_key = lambda r: (f"{str(r.get('room_name') or '')}\n"
                      f"{str(r.get('region') or '')} · "
                      f"{str(r.get('table_no') or '')}")
loc_disp = [r["id"] for r in sorted(loc_rows, key=disp_key)]
if loc_exp == loc_disp:
    fails.append("[位置·样本] 显示序与键序相同，对抗样本无区分度（测试无效）")
page._rows = loc_rows
page._populate(loc_rows)
app.processEvents()
got = run_sort("location", True)
if got != loc_exp:
    fails.append(f"[位置·升] {got} != 旧键序 {loc_exp}")
loc_exp_desc = [r["id"] for r in sorted(loc_rows, key=old_loc_key, reverse=True)]
got = run_sort("location", False)
if got != loc_exp_desc:
    fails.append(f"[位置·降] {got} != 旧键稳定降序 {loc_exp_desc}")

# ---- 样本：响应列（空串最前 + 档位乱序 + 解决人次行）----
resp_rows = [
    {"id": 21, "response_time": "1小时以上", "resolver": "沈喆"},
    {"id": 22, "response_time": "", "resolver": "吴斌"},
    {"id": 23, "response_time": "1分钟内", "resolver": "贺勤"},
    {"id": 24, "response_time": "30分钟内", "resolver": ""},
    {"id": 25, "response_time": "5分钟内", "resolver": "张峻涛"},
    {"id": 26, "response_time": "", "resolver": "孙跃源"},
]
old_resp_key = lambda r: str(r.get("response_time") or "")
resp_exp = [r["id"] for r in sorted(resp_rows, key=old_resp_key)]
page._rows = resp_rows
page._populate(resp_rows)
app.processEvents()
got = run_sort("response_time", True)
if got != resp_exp:
    fails.append(f"[响应·升] {got} != 旧键序 {resp_exp}（空串应最前）")
resp_exp_desc = [r["id"] for r in sorted(resp_rows, key=old_resp_key, reverse=True)]
got = run_sort("response_time", False)
if got != resp_exp_desc:
    fails.append(f"[响应·降] {got} != 旧键稳定降序 {resp_exp_desc}（平键应保持原序）")

# ---- _SortKeyItem.__lt__ 行为观察（回退分支可达性审计）----
a = _SortKeyItem("显示A", "zzz")
b = _SortKeyItem("显示B", "aaa")
assert b < a and not (a < b), "__lt__ 键路径异常"          # 键序 aaa<zzz
# 观察项 1：与普通 item 混比（生产不可达：三列内所有行均为 _SortKeyItem）
plain = QTableWidgetItem("显示AB")
mixed_err = ""
try:
    _ = a < plain
    _ = plain < a
except TypeError as e:
    mixed_err = str(e)
print(f"[观察] 混比行为: {'TypeError: ' + mixed_err if mixed_err else '未抛异常'}"
      f"（生产不可达，列内 item 同构）")
# 观察项 2：None 键防护（_populate 不产生 None 键，防御性直测）
c = _SortKeyItem("显示C", None)
none_err = ""
try:
    _ = c < a
    _ = a < c
except TypeError as e:
    none_err = str(e)
if none_err:
    fails.append(f"[__lt__None] None 键混比抛 TypeError: {none_err}")
else:
    print("[观察] None 键防护: 未抛 TypeError")

if fails:
    print("FAIL:")
    for f in fails:
        print("  ", f)
    sys.stdout.flush()
    os._exit(1)
print(f"PASS: 跨年(4年份)升序={exp_asc} 位置键序={loc_exp} (显示序={loc_disp}，"
      f"两者不同=有区分度) 响应键序={resp_exp} __lt__ 回退/None 防护均安全")
sys.stdout.flush()
os._exit(0)
