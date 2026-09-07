# -*- coding: utf-8 -*-
"""P0-2 列合并方案实测（调研用，非生产代码）

列数减少 → 每帧绘制的单元格数减少，但合并后每格文本变长，而文本绘制
成本与字符数正相关（长文本 400 字实测 +44.5%）。两者可能相互抵消，
本脚本用真实数据实测各方案净收益，避免按列数线性外推得出错误结论。

方案：
  current  现状 13 列
  plan_a   三否列合并为「判定」          → 11 列
  plan_b   a + 填写/发生时间合并为「时间」 → 10 列
  plan_c   b + 问题/原因合并              → 9 列
  plan_d   c + 类型并入位置                → 8 列

用法：
    <venv>/Scripts/python.exe tools/column_merge_probe.py [--rows 60]
"""
import argparse
import os
import sqlite3
import statistics
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QEvent  # noqa: E402
from PySide6.QtGui import QFont, QColor  # noqa: E402
from PySide6.QtWidgets import (QApplication, QTableWidgetItem,  # noqa: E402
                               QWidget, QHBoxLayout, QPushButton,
                               QAbstractItemView)

app = QApplication([])

from qfluentwidgets import TableWidget, SmoothMode  # noqa: E402

ROW_H = 32
CHK_W = 36


# ---------------- 真实数据 ----------------

def load_rows(n=60):
    con = sqlite3.connect(os.path.join(PROJECT_ROOT, "database", "tables.db"))
    con.row_factory = sqlite3.Row
    real = [dict(r) for r in con.execute(
        "select * from aftersale_records limit 200")]
    con.close()
    out = []
    while len(out) < n:
        for r in real:
            d = dict(r)
            d["id"] = len(out) + 1
            out.append(d)
            if len(out) >= n:
                break
    return out


def short_dt(v):
    s = str(v or "")
    return s[5:16] if len(s) >= 16 else s


# ---------------- 列方案 ----------------
# (key 或 key 列表, 表头, 宽度, 是否双行, 是否徽章列)

def problem_cause(rec):
    return f"{rec.get('problem') or ''}\n{rec.get('cause') or ''}"


PLANS = {
    "current": [
        (["created_at"], "填写时间", 128, True, False),
        (["occurred_at"], "发生时间", 128, True, False),
        (["issue_type"], "类型", 90, False, False),
        (["location"], "位置", 200, True, False),
        (["problem"], "问题", 200, False, False),
        (["cause"], "发生原因", 180, False, False),
        (["solution"], "解决方案", 180, False, False),
        (["resolved"], "解决", 70, False, True),
        (["is_our_problem"], "我们问题", 70, False, True),
        (["is_initiative"], "主动发起", 70, False, True),
        (["response_time"], "响应", 110, True, False),
        (["ops"], "操作", 168, False, False),
    ],
    "plan_a": [
        (["created_at"], "填写时间", 128, True, False),
        (["occurred_at"], "发生时间", 128, True, False),
        (["issue_type"], "类型", 90, False, False),
        (["location"], "位置", 200, True, False),
        (["problem"], "问题", 200, False, False),
        (["cause"], "发生原因", 180, False, False),
        (["solution"], "解决方案", 180, False, False),
        (["judge"], "判定", 150, False, False),
        (["response_time"], "响应", 110, True, False),
        (["ops"], "操作", 168, False, False),
    ],
    "plan_b": [
        (["time"], "时间", 168, True, False),
        (["issue_type"], "类型", 90, False, False),
        (["location"], "位置", 200, True, False),
        (["problem"], "问题", 200, False, False),
        (["cause"], "发生原因", 180, False, False),
        (["solution"], "解决方案", 180, False, False),
        (["judge"], "判定", 150, False, False),
        (["response_time"], "响应", 110, True, False),
        (["ops"], "操作", 168, False, False),
    ],
    "plan_c": [
        (["time"], "时间", 168, True, False),
        (["issue_type"], "类型", 90, False, False),
        (["location"], "位置", 200, True, False),
        (["problem", "cause"], "问题 / 原因", 260, True, False),
        (["solution"], "解决方案", 180, False, False),
        (["judge"], "判定", 150, False, False),
        (["response_time"], "响应", 110, True, False),
        (["ops"], "操作", 168, False, False),
    ],
    "plan_d": [
        (["time"], "时间", 168, True, False),
        (["issue_type", "location"], "类型 / 位置", 250, True, False),
        (["problem", "cause"], "问题 / 原因", 260, True, False),
        (["solution"], "解决方案", 180, False, False),
        (["judge"], "判定", 150, False, False),
        (["response_time"], "响应", 110, True, False),
        (["ops"], "操作", 168, False, False),
    ],
    # 对照组：只删列不合并（信息移入 tooltip），文本长度不变
    # 用于量化「合并导致文本变长」吃掉了多少收益
    "plan_e_drop3": [
        (["created_at"], "填写时间", 128, True, False),
        (["occurred_at"], "发生时间", 128, True, False),
        (["issue_type"], "类型", 90, False, False),
        (["location"], "位置", 200, True, False),
        (["problem"], "问题", 200, False, False),
        (["cause"], "发生原因", 180, False, False),
        (["solution"], "解决方案", 180, False, False),
        (["response_time"], "响应", 110, True, False),
        (["ops"], "操作", 168, False, False),
    ],
    "plan_f_drop2": [
        (["created_at"], "填写时间", 128, True, False),
        (["issue_type"], "类型", 90, False, False),
        (["location"], "位置", 200, True, False),
        (["problem"], "问题", 200, False, False),
        (["cause"], "发生原因", 180, False, False),
        (["solution"], "解决方案", 180, False, False),
        (["response_time"], "响应", 110, True, False),
        (["ops"], "操作", 168, False, False),
    ],
}

YES_NO = ("resolved", "is_our_problem", "is_initiative")


def cell_text(keys, rec, two_line):
    if keys == ["judge"] or keys[0] == "judge":
        return "/".join(str(rec.get(k) or "否") for k in YES_NO)
    if keys == ["time"] or keys[0] == "time":
        return (f"{short_dt(rec.get('created_at'))} 填 "
                f"{rec.get('creator') or ''}\n"
                f"{short_dt(rec.get('occurred_at'))} 发生")
    vals = [str(rec.get(k) or "") for k in keys]
    vals = [v for v in vals if v]
    if two_line or len(keys) > 1:
        return "\n".join(vals)
    return vals[0] if vals else ""


# ---------------- 建表与填充 ----------------

def build_table(plan, rows):
    t = TableWidget()
    t.setColumnCount(1 + len(plan))
    t.setHorizontalHeaderLabels([""] + [p[1] for p in plan])
    t.setColumnWidth(0, CHK_W)
    for i, (_k, _h, w, _2, _b) in enumerate(plan):
        t.setColumnWidth(1 + i, w)
    t.setAlternatingRowColors(True)
    t.verticalHeader().setDefaultSectionSize(ROW_H)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

    small = QFont()
    small.setPointSizeF(10.5)
    bold = QFont()
    bold.setBold(True)

    t.setRowCount(len(rows))
    for r, rec in enumerate(rows):
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        chk.setCheckState(Qt.CheckState.Unchecked)
        t.setItem(r, 0, chk)
        for i, (keys, _h, _w, two, badge) in enumerate(plan):
            col = 1 + i
            if keys == ["ops"]:
                w = QWidget()
                lay = QHBoxLayout(w)
                lay.setContentsMargins(4, 0, 4, 0)
                lay.setSpacing(6)
                for name in ("编辑", "详情"):
                    b = QPushButton(name, w)
                    b.setFixedHeight(24)
                    lay.addWidget(b)
                lay.addStretch(1)
                t.setCellWidget(r, col, w)
                continue
            it = QTableWidgetItem(cell_text(keys, rec, two))
            if two or len(keys) > 1:
                it.setFont(small)
            if badge:
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                it.setFont(bold)
                if str(rec.get(keys[0]) or "") == "是":
                    it.setForeground(QColor(15, 123, 15))
            t.setItem(r, col, it)
    t.resize(1500, 560)
    t.show()
    for _ in range(4):
        app.processEvents()
    return t


def flush(n=3):
    for _ in range(n):
        app.processEvents()


def measure(t, steps=40, rounds=3):
    sb = t.verticalScrollBar()
    try:
        t.scrollDelagate.verticalSmoothScroll.setSmoothMode(SmoothMode.NO_SMOOTH)
    except Exception:
        pass
    for _ in range(3):
        sb.setValue(0); t.viewport().repaint()
        sb.setValue(2); t.viewport().repaint()
    samples = []
    for _ in range(rounds):
        sb.setValue(0)
        t.viewport().repaint()
        t0 = time.perf_counter()
        for i in range(steps):
            sb.setValue(i + 1)
            t.viewport().repaint()
        samples.append((time.perf_counter() - t0) * 1000 / steps)
    return statistics.median(samples)


_KEPT = []


def cleanup(t):
    """顶层 TableWidget 直接 deleteLater + 派发 DeferredDelete 会段错误
    （实测 exit 139），改为保留实例并隐藏；控件堆积带来的漂移由配对基线抵消"""
    t.hide()
    _KEPT.append(t)
    flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=60)
    ap.add_argument("--steps", type=int, default=40)
    ap.add_argument("--rounds", type=int, default=3)
    args = ap.parse_args()
    rows = load_rows(args.rows)

    print(f"{'方案':10s}{'列数':>6s}{'ms/帧':>9s}{'配对基线':>10s}"
          f"{'Δ%':>9s}")
    print("-" * 46)
    base_ms = None
    for name in ("current", "plan_a", "plan_f_drop2", "plan_b",
                 "plan_c", "plan_d", "plan_e_drop3"):
        t = build_table(PLANS[name], rows)
        ms = measure(t, args.steps, args.rounds)
        cleanup(t)
        # 配对基线：紧邻重测 current，抵消控件堆积
        bt = build_table(PLANS["current"], rows)
        bms = measure(bt, args.steps, args.rounds)
        cleanup(bt)
        if base_ms is None:
            base_ms = ms
        print(f"{name:10s}{len(PLANS[name]) + 1:6d}{ms:9.2f}{bms:10.2f}"
              f"{(ms / bms - 1) * 100:+8.1f}%")


if __name__ == "__main__":
    main()
