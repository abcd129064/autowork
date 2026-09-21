# -*- coding: utf-8 -*-
"""售后面板「解决/我们问题/主动发起」三列样式变体实测（调研用，非生产代码）

背景：三列在 2026-08-26 已从 cellWidget(QLabel 胶囊) 优化为文本 item
（填充耗时第一名 42% 已消除）。用户希望「更简洁」并关心性能。本脚本量化
各样式变体的每帧滚动成本，验证「简洁化」与「降开销」是否同向。

变体（仅改 3 个是/否列的样式，其余列完全一致）：
  current   现状：是=语义色前景+8%同色底+加粗；否=中性灰加粗
  no_bg     去底色：是=语义色前景+加粗；否=中性灰加粗（视觉更干净）
  no_bold   去加粗：是=语义色前景+8%底；否=中性灰（保留淡底）
  plain     最简：是=语义色前景；否=默认前景；无底色无加粗
  merged    三列合并为「判定」单列文本（是/是/否），列数 13→11

用法：
    <venv>/Scripts/python.exe tools/perf/badge_style_probe.py [--rows 60]
"""
import argparse
import os
import sqlite3
import statistics
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFont, QColor  # noqa: E402
from PySide6.QtWidgets import (QApplication, QTableWidgetItem,  # noqa: E402
                               QWidget, QHBoxLayout, QPushButton,
                               QAbstractItemView)

app = QApplication.instance() or QApplication([])

from qfluentwidgets import TableWidget, SmoothMode  # noqa: E402
from core.design_tokens import SEMANTIC  # noqa: E402

ROW_H = 32
CHK_W = 36

# 三列语义色（与 common._YES_NO_COLORS 对齐）
_YES_C = {"resolved": SEMANTIC["success"],
          "is_our_problem": SEMANTIC["warning"],
          "is_initiative": SEMANTIC["info"]}
_NEUTRAL = SEMANTIC["neutral"]

# 非徽章列（所有变体共用，保证差异只来自 3 列样式）
_BASE_COLS = [
    ("created_at", "填写时间", 128, True),
    ("occurred_at", "发生时间", 128, True),
    ("issue_type", "类型", 90, False),
    ("location", "位置", 200, True),
    ("problem", "问题", 200, False),
    ("cause", "发生原因", 180, False),
    ("solution", "解决方案", 180, False),
]
_TAIL_COLS = [
    ("response_time", "响应", 110, True),
    ("ops", "操作", 168, False),
]
_YESNO = ["resolved", "is_our_problem", "is_initiative"]


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


def _base_cell_text(key, rec):
    if key == "created_at":
        return f"{short_dt(rec.get('created_at'))}\n{rec.get('creator') or ''}"
    if key == "occurred_at":
        return str(rec.get("occurred_at") or "")
    if key == "location":
        return (f"{rec.get('room_name') or ''}\n"
                f"{rec.get('region') or ''} · {rec.get('table_no') or ''}")
    if key == "response_time":
        return (f"{rec.get('response_time') or '—'}\n"
                f"{rec.get('resolver') or ''}")
    return str(rec.get(key) or "")


def build_table(variant, rows):
    t = TableWidget()
    if variant == "merged":
        cols = (_BASE_COLS + [("judge", "判定", 150, False)] + _TAIL_COLS)
    else:
        cols = (_BASE_COLS + [(k, {"resolved": "解决", "is_our_problem": "我们问题",
                                   "is_initiative": "主动发起"}[k], 70, False)
                              for k in _YESNO] + _TAIL_COLS)
    t.setColumnCount(1 + len(cols))
    t.setHorizontalHeaderLabels([""] + [c[1] for c in cols])
    t.setColumnWidth(0, CHK_W)
    for i, (_k, _h, w, _2) in enumerate(cols):
        t.setColumnWidth(1 + i, w)
    t.setAlternatingRowColors(True)
    t.verticalHeader().setDefaultSectionSize(ROW_H)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

    small = QFont(); small.setPointSizeF(10.5)
    bold = QFont(); bold.setBold(True)

    t.setRowCount(len(rows))
    for r, rec in enumerate(rows):
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        chk.setCheckState(Qt.CheckState.Unchecked)
        t.setItem(r, 0, chk)
        for i, (key, _h, _w, two) in enumerate(cols):
            col = 1 + i
            if key == "ops":
                w = QWidget()
                lay = QHBoxLayout(w); lay.setContentsMargins(4, 0, 4, 0)
                lay.setSpacing(6)
                for name in ("编辑", "详情"):
                    b = QPushButton(name, w); b.setFixedHeight(24)
                    lay.addWidget(b)
                lay.addStretch(1)
                t.setCellWidget(r, col, w)
                continue
            if key == "judge":
                it = QTableWidgetItem(
                    "/".join(str(rec.get(k) or "否") for k in _YESNO))
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                t.setItem(r, col, it)
                continue
            if key in _YESNO:
                is_yes = str(rec.get(key) or "") == "是"
                it = QTableWidgetItem("是" if is_yes else "否")
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if variant == "current":
                    it.setForeground(QColor(_YES_C[key] if is_yes else _NEUTRAL))
                    if is_yes:
                        bg = QColor(_YES_C[key]); bg.setAlpha(20)
                        it.setBackground(bg)
                    it.setFont(bold)
                elif variant == "no_bg":
                    it.setForeground(QColor(_YES_C[key] if is_yes else _NEUTRAL))
                    it.setFont(bold)
                elif variant == "no_bold":
                    it.setForeground(QColor(_YES_C[key] if is_yes else _NEUTRAL))
                    if is_yes:
                        bg = QColor(_YES_C[key]); bg.setAlpha(20)
                        it.setBackground(bg)
                elif variant == "plain":
                    if is_yes:
                        it.setForeground(QColor(_YES_C[key]))
                t.setItem(r, col, it)
                continue
            it = QTableWidgetItem(_base_cell_text(key, rec))
            if two:
                it.setFont(small)
            t.setItem(r, col, it)
    t.resize(1500, 560)
    t.show()
    for _ in range(4):
        app.processEvents()
    return t


def flush(n=3):
    for _ in range(n):
        app.processEvents()


def measure(t, steps=40, rounds=5):
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
    t.hide()
    _KEPT.append(t)
    flush()


VARIANTS = ("current", "no_bg", "no_bold", "plain", "merged")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=60)
    ap.add_argument("--steps", type=int, default=40)
    ap.add_argument("--rounds", type=int, default=5)
    args = ap.parse_args()
    rows = load_rows(args.rows)

    print(f"{'变体':10s}{'列数':>6s}{'ms/帧':>9s}{'配对基线':>10s}{'Δ%':>9s}")
    print("-" * 46)
    for name in VARIANTS:
        t = build_table(name, rows)
        ms = measure(t, args.steps, args.rounds)
        ncol = t.columnCount()
        cleanup(t)
        bt = build_table("current", rows)
        bms = measure(bt, args.steps, args.rounds)
        cleanup(bt)
        print(f"{name:10s}{ncol:6d}{ms:9.2f}{bms:10.2f}"
              f"{(ms / bms - 1) * 100:+8.1f}%")


if __name__ == "__main__":
    main()
