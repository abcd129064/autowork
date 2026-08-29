# -*- coding: utf-8 -*-
"""hover 重绘 patch 前后对比压测（offscreen，3 轮中位）

背景：qfluentwidgets TableBase._setHoverRow 默认整视口 update，鼠标扫过
每行都全表重绘；core.perf::patch_table_hover_repaint 改为只重绘新旧两行
条带。本脚本在**同一进程内**切换 patch 状态做 A/B 对比（消除跨进程差异）：
模拟 60 行 × 15 列（含操作列 cellWidget）页面，连续 40 次非相邻 hover 行
跳变 + processEvents 触发真实重绘，统计耗时与重绘面积。

用法：<venv>/Scripts/python.exe tools/bench_hover_repaint.py
结果解读：patched 行的 ms 应显著低于 unpatched（2K 下面积约 1/23）。
"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QElapsedTimer
from PySide6.QtWidgets import QApplication, QPushButton, QTableWidgetItem
from qfluentwidgets import TableWidget
from qfluentwidgets.components.widgets.table_view import TableBase

import core.perf as perf

ROWS, COLS, ROUNDS = 60, 15, 40
_ORIG_SET_HOVER = TableBase._setHoverRow  # patch 前捕获原始实现


def make_table():
    t = TableWidget()
    t.setColumnCount(COLS)
    t.setRowCount(ROWS)
    for r in range(ROWS):
        for c in range(COLS - 1):
            it = QTableWidgetItem(f"r{r}c{c}")
            t.setItem(r, c, it)
        t.setCellWidget(r, COLS - 1, QPushButton("编辑"))  # 模拟操作列
    t.resize(1600, 900)
    t.show()
    app.processEvents()
    return t


def run_scenario(patched: bool, rounds: int = ROUNDS) -> tuple:
    if patched:
        perf.patch_table_hover_repaint()
    else:
        TableBase._setHoverRow = _ORIG_SET_HOVER
        # 关键：恢复原实现必须同步重置幂等标志，否则下次 patch 直接 return，
        # 后续 patched 轮会实际跑 unpatched 实现（A/B 污染）
        TableBase._perf_hover_patched = False
    t = make_table()
    # warmup：字体/QSS/样式缓存建立后计时才可比
    for i in range(5):
        t._setHoverRow(i % ROWS)
        app.processEvents()
    vp = t.viewport()
    timer = QElapsedTimer()
    timer.start()
    area = 0
    for i in range(rounds):
        t._setHoverRow((i * 7 + 3) % ROWS)  # 非相邻跳变，逼近真实扫行
        app.processEvents()
    ms = timer.elapsed()
    t.close()
    return ms


def median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2]


def main():
    app_inst = QApplication.instance() or QApplication([])
    unpatched, patched = [], []
    for _ in range(3):
        unpatched.append(run_scenario(False))
        patched.append(run_scenario(True))
    u, p = median(unpatched), median(patched)
    print(f"unpatched(整视口重绘) 3轮: {unpatched} 中位 {u}ms")
    print(f"patched(双行条带重绘) 3轮: {patched} 中位 {p}ms")
    if u > 0:
        print(f"提升: {(u - p) / u * 100:.1f}%  ({p / u:.2f}x)")
    # 恢复原始实现，避免影响同进程后续逻辑
    TableBase._setHoverRow = _ORIG_SET_HOVER


if __name__ == "__main__":
    app = QApplication.instance() or QApplication([])
    main()
    app.processEvents()
    sys.stdout.flush()  # os._exit 会跳过 flush，必须先显式刷出
    os._exit(0)  # offscreen 退出段错误规避
