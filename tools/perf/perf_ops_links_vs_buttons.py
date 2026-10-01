# -*- coding: utf-8 -*-
"""操作列性能对比：cellWidget 按钮 vs ops_link_delegate 自绘文字链接

对照方式（各 500 行 × 6 列，最后一列是操作列）：
  buttons = 每个单元格嵌 2 个 qfluentwidgets.PushButton（旧实现形态）
  links   = core.ops_link_delegate 自绘 + 矩形命中（2026-09-19 起现行实现）

测量三项：
  1. 构建耗时（populate）：含 widget 构造 + QSS 装配 + 布局
  2. 内存增量：优先 psutil RSS（含 C++ 侧），缺失则退 tracemalloc（仅 Python 层）
  3. 渲染耗时：离屏 render 到 QPixmap，反映 paint 开销

另附 cProfile top（单独一轮，避免污染计时）。

运行：QT_QPA_PLATFORM=offscreen <py> tools/perf/perf_ops_links_vs_buttons.py
"""
import cProfile
import io
import os
import pstats
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import importlib.util as _iu
_spec = _iu.find_spec('PySide6')
if _spec is not None:
    _pkg = list(getattr(_spec, 'submodule_search_locations', None) or [])[0]
    for _d in (_pkg, os.path.dirname(_pkg),
               os.path.join(os.environ.get('SystemRoot', r'C:/Windows'), 'System32')):
        if os.path.isdir(_d):
            try:
                os.add_dll_directory(_d)
            except OSError:
                pass
    os.environ['QT_PLUGIN_PATH'] = os.path.join(_pkg, 'plugins')

import gc
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QTableWidget,
                               QTableWidgetItem, QWidget)

app = QApplication([])

from qfluentwidgets import PushButton  # noqa: E402  项目里旧实现用的就是它

from core.ops_link_delegate import LINKS_ROLE, install_ops_links  # noqa: E402

ROWS = 500
COLS = 6
ROUNDS = 3
LINKS = (("编辑", "primary", "edit"), ("删除", "danger", "delete"))

try:
    import psutil
    _proc = psutil.Process()
except Exception:
    psutil = None
    _proc = None


def _rss_mb():
    if _proc is None:
        return None
    return _proc.memory_info().rss / 1024 / 1024


def _mk_table():
    t = QTableWidget(ROWS, COLS)
    t.resize(1200, 800)
    return t


def _count_cell_widgets(t):
    """统计业务显式塞进操作列的控件数（cellWidget 方案的实体成本）

    只数 setCellWidget 装进去的那一棵，排除 QTableWidget 自身内部子控件
    （viewport/滚动条/header 等），否则把框架常数算进来会得出反直觉结果。
    """
    n = 0
    col = t.columnCount() - 1
    for r in range(t.rowCount()):
        cw = t.cellWidget(r, col)
        if cw is None:
            continue
        stack = [cw]
        while stack:
            w = stack.pop()
            n += 1
            for k in w.children():
                if isinstance(k, QWidget):
                    stack.append(k)
    return n


def populate_buttons(t):
    for r in range(ROWS):
        for c in range(COLS - 1):
            t.setItem(r, c, QTableWidgetItem(f"cell-{r}-{c}"))
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addWidget(PushButton("编辑", w))
        lay.addWidget(PushButton("删除", w))
        t.setCellWidget(r, COLS - 1, w)


def populate_links(t):
    install_ops_links(t, lambda *a: None)
    for r in range(ROWS):
        for c in range(COLS - 1):
            t.setItem(r, c, QTableWidgetItem(f"cell-{r}-{c}"))
        it = QTableWidgetItem("")
        it.setData(LINKS_ROLE, LINKS)
        t.setItem(r, COLS - 1, it)


def measure(populate, label):
    gc.collect()
    app.processEvents()
    base = _rss_mb()

    times = []
    for _ in range(ROUNDS):
        t = _mk_table()
        t0 = time.perf_counter()
        populate(t)
        app.processEvents()      # 让布局/样式真正结算
        times.append((time.perf_counter() - t0) * 1000)
        peek = t
        del t
        gc.collect()

    # 保最后一张做渲染与控件计数
    t = _mk_table()
    populate(t)
    app.processEvents()
    peak = _rss_mb()
    widgets = _count_cell_widgets(t)

    pm = QPixmap(t.size())
    t0 = time.perf_counter()
    t.render(pm)
    app.processEvents()
    render_ms = (time.perf_counter() - t0) * 1000

    del t, pm
    gc.collect()
    app.processEvents()

    return {
        "label": label,
        "build_ms": min(times),          # 取最快一轮，排除 GC 抖动
        "build_ms_avg": sum(times) / len(times),
        "render_ms": render_ms,
        "widgets": widgets,
        "rss_delta_mb": (peak - base) if (peak is not None and base is not None) else None,
    }


print(f"PySide6 offscreen｜{ROWS} 行 × {COLS} 列（操作列 {LINKS}）\n")

# 先跑一轮 links 预热 import/qfw 样式缓存，避免把首次开销算给 buttons
warm = _mk_table()
populate_links(warm)
del warm
gc.collect()

res_b = measure(populate_buttons, "cellWidget 按钮")
res_l = measure(populate_links, "自绘文字链接")

print(f"{'指标':<22}{'cellWidget 按钮':>18}{'自绘链接':>16}{'倍数':>10}")
print("-" * 66)
print(f"{'构建耗时(ms,最快轮)':<20}{res_b['build_ms']:>18.1f}{res_l['build_ms']:>16.1f}"
      f"{res_b['build_ms'] / max(res_l['build_ms'], 1e-6):>9.1f}x")
print(f"{'构建耗时(ms,均值)':<20}{res_b['build_ms_avg']:>18.1f}{res_l['build_ms_avg']:>16.1f}"
      f"{res_b['build_ms_avg'] / max(res_l['build_ms_avg'], 1e-6):>9.1f}x")
print(f"{'离屏渲染(ms)':<22}{res_b['render_ms']:>18.1f}{res_l['render_ms']:>16.1f}"
      f"{res_b['render_ms'] / max(res_l['render_ms'], 1e-6):>9.1f}x")
print(f"{'实体子控件数(个)':<20}{res_b['widgets']:>18}{res_l['widgets']:>16}"
      f"{'-' if not res_l['widgets'] else res_b['widgets'] / res_l['widgets']:>10}")
if res_b['rss_delta_mb'] is not None and res_l['rss_delta_mb'] is not None:
    print(f"{'内存增量 RSS(MB)':<20}{res_b['rss_delta_mb']:>18.1f}"
          f"{res_l['rss_delta_mb']:>16.1f}"
          f"{max(res_b['rss_delta_mb'], 0) / max(res_l['rss_delta_mb'], 1e-6):>9.1f}x")

print("\n===== cProfile：按钮方案构建耗时去哪了（top 12）=====")
t = _mk_table()
pr = cProfile.Profile()
pr.enable()
populate_buttons(t)
pr.disable()
s = io.StringIO()
pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(12)
print(s.getvalue()[:2600])
del t
gc.collect()

print("===== cProfile：自绘链接方案（top 8）=====")
t2 = _mk_table()
pr2 = cProfile.Profile()
pr2.enable()
populate_links(t2)
pr2.disable()
s2 = io.StringIO()
pstats.Stats(pr2, stream=s2).sort_stats("cumulative").print_stats(8)
print(s2.getvalue()[:1400])
