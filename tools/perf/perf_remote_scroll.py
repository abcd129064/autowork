# -*- coding: utf-8 -*-
"""远程面板表格滚动性能单变量对照测量（复刻 SessionWork 表格结构）。

背景：远程面板性能调查（2026-09-24），结论见
docs/远程面板性能调查报告2026-09-24.md §4.1。量化表格滚动帧耗时中
每行 cellWidget、tooltip、交替行色、qfluentwidgets 委托绘制各因素的
贡献。结构忠实复刻 windows/remote_session/remote_hub.py
SessionWork._add_row（9 列 / 每行 5 按钮 cellWidget / 全格 tooltip /
交替行色 / setWordWrap(False)）。测量范式与 tools/perf/scroll_profile.py
相同：配对基线 + 逐步 setValue + viewport().repaint() 同步重绘 +
Paint 事件计数。

实现说明：所有 fluent 变体复用同一 TableWidget 实例（仅 setRowCount(0)
重灌 + 翻转单变量标志）。逐变体 deleteLater + sendPostedEvents 在
PySide6 6.9.2 × qfluentwidgets 1.11.2 组合下触发 access violation
（offscreen 析构路径），单例复用可规避；配对基线在同表上紧邻重测，
公平性不受影响。

用法：E:/ANACONDA/python.exe tools/perf/perf_remote_scroll.py [--rows 60] [--steps 40]
产物：tools/_scratch/perf_remote_scroll_result.json（不入库）
"""
import argparse
import json
import os
import statistics
import sys
import time

# tools/perf/ → 上溯三层 = 仓库根（AGENTS §2.4：子目录脚本需三层 dirname）
ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 测量环境组合（2026-09-24）：E:\ANACONDA 提供 PySide6（Qt 可用）但缺
# qfluentwidgets；qfluentwidgets 为纯 Python 包，可从另一解释器的
# site-packages 借用源码（不安装、不改动任何环境）。可用环境变量
# QFW_SITE_PACKAGES 覆盖借用路径。
_MINI_SP = os.environ.get(
    "QFW_SITE_PACKAGES",
    r"C:\Users\shen_zhe\miniconda3\Lib\site-packages")
if os.path.isdir(os.path.join(_MINI_SP, "qfluentwidgets")):
    sys.path.append(_MINI_SP)

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHeaderView,
                               QHBoxLayout, QTableWidget, QTableWidgetItem,
                               QWidget)
from qfluentwidgets import PushButton, TableWidget
from qfluentwidgets.common.smooth_scroll import SmoothMode

from core.perf import apply_table_smooth_mode

_C_ACCENT = QColor("#0078d4")
_C_SUCCESS = QColor("#2ecc71")
_C_WARNING = QColor("#f39c12")
_C_INFO = QColor("#3498db")
_C_MUTED = QColor("#8a919b")

_HEADS = ["状态", "frps 在线", "serverName", "关联球桌", "本地端口",
          "RTT / 质量", "今日流量", "来源", "操作"]
_WIDTHS = [96, 92, -1, 96, 80, 170, 108, 90, 330]  # -1 = Stretch

FLAGS_FULL = {"cellwidget": True, "tooltip": True, "altrow": True}
FLAGS_NO_CW = {"cellwidget": False, "tooltip": True, "altrow": True}
FLAGS_NO_TIP = {"cellwidget": True, "tooltip": False, "altrow": True}
FLAGS_NO_ALT = {"cellwidget": True, "tooltip": True, "altrow": False}
FLAGS_MIN = {"cellwidget": False, "tooltip": False, "altrow": False}

VARIANTS = [
    ("baseline", "线上实现全量（cellWidget+tooltip+交替行+fluent 委托）", FLAGS_FULL),
    ("no_cellwidget", "操作列改纯 QTableWidgetItem", FLAGS_NO_CW),
    ("no_tooltip", "去掉全部 tooltip", FLAGS_NO_TIP),
    ("no_alt_rows", "关闭交替行色", FLAGS_NO_ALT),
    ("plain_min", "纯 item 最小形态（无 cellWidget/tooltip/交替行）", FLAGS_MIN),
]


def _row_buttons(parent, sn):
    """复刻 remote_hub._row_buttons：5 按钮，宽度按字体度量自适应"""
    specs = [
        ("SSH", "通过该隧道打开 SSH 终端"),
        ("SFTP", "通过该隧道打开 SFTP 文件传输"),
        ("RDP", "通过该隧道打开远程桌面"),
        ("断开", f"断开隧道 {sn}：关闭相关会话并释放本地端口（保留注册，可重连）"),
        ("删除", f"删除隧道 {sn}：移除注册与持久化配置"),
    ]
    holder = QWidget(parent)
    h = QHBoxLayout(holder)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)
    for text, tip in specs:
        b = PushButton(text, holder)
        b.setFixedHeight(26)
        b.setToolTip(tip)
        fm = b.fontMetrics()
        b.setFixedWidth(fm.horizontalAdvance(text) + 24)
        h.addWidget(b)
    return holder


def make_row_data(i):
    statuses = [("已连接", _C_ACCENT), ("已预热", _C_INFO),
                ("会话中", _C_SUCCESS), ("未启动", _C_MUTED)]
    st, sc = statuses[i % 4]
    return {
        "sn": f"snk_dev_{i:03d}", "table_id": f"T{i % 97:04d}",
        "port": str(40000 + i), "status": st, "status_color": sc,
        "prewarmed": (i % 4 == 1),
        "rtt": f"{20 + i % 80}ms 优", "rtt_color": _C_SUCCESS,
        "traffic": f"↓{1.2 + i % 7:.1f}GB ↑{0.3 + i % 4:.1f}GB",
        "source": "snk 快捷",
    }


def populate(table, n, flags):
    for i in range(n):
        d = make_row_data(i)
        r = table.rowCount()
        table.insertRow(r)
        it0 = QTableWidgetItem(d["status"])
        it0.setForeground(d["status_color"])
        if flags["tooltip"] and d["prewarmed"]:
            it0.setToolTip("预热打洞完成于 09-24 10:00，点击连接免打洞等待")
        table.setItem(r, 0, it0)

        it1 = QTableWidgetItem("● online")
        it1.setForeground(_C_SUCCESS)
        if flags["tooltip"]:
            it1.setToolTip("当前连接 1 · 最近上线 09-24 09:00")
        table.setItem(r, 1, it1)

        for col, text in ((2, d["sn"]), (3, d["table_id"]),
                          (4, d["port"]), (7, d["source"])):
            item = QTableWidgetItem(text)
            if flags["tooltip"]:
                item.setToolTip(text)
            table.setItem(r, col, item)

        it5 = QTableWidgetItem(d["rtt"])
        it5.setForeground(d["rtt_color"])
        if flags["tooltip"]:
            it5.setToolTip("样本 120（成功 118） · P95 88ms")
        table.setItem(r, 5, it5)
        table.setItem(r, 6, QTableWidgetItem(d["traffic"]))

        if flags["cellwidget"]:
            table.setCellWidget(r, 8, _row_buttons(table, d["sn"]))
        else:
            table.setItem(r, 8, QTableWidgetItem("SSH/SFTP/RDP/断开/删除"))


def build_table(cls, n, flags):
    table = cls()
    table.setColumnCount(9)
    table.setHorizontalHeaderLabels(_HEADS)
    table.verticalHeader().setVisible(False)
    table.setWordWrap(False)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    hh = table.horizontalHeader()
    hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    for i, w in enumerate(_WIDTHS):
        if w > 0:
            table.setColumnWidth(i, w)
    table.setMinimumHeight(260)
    table.resize(1240, 520)
    if cls is TableWidget:
        apply_table_smooth_mode(table, panel="remote")
        try:
            table.scrollDelagate.verticalSmoothScroll.setSmoothMode(
                SmoothMode.NO_SMOOTH)
        except Exception:
            pass
    table.show()
    for _ in range(3):
        app.processEvents()
    reset_table(table, n, flags)
    return table


def reset_table(table, n, flags):
    """同一实例上重灌数据并翻转单变量标志（规避析构崩溃，见模块 docstring）"""
    table.setAlternatingRowColors(flags["altrow"])
    table.setRowCount(0)
    populate(table, n, flags)
    table.verticalScrollBar().setValue(0)
    table.viewport().repaint()
    for _ in range(2):
        app.processEvents()


class Counter(QObject):
    def __init__(self):
        super().__init__()
        self.paints = 0

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Paint:
            self.paints += 1
        return False


def measure_scroll(table, steps, rounds=3):
    sb = table.verticalScrollBar()
    for _ in range(3):  # 预热（惰性布局/字体缓存剔除）
        sb.setValue(0)
        table.viewport().repaint()
        sb.setValue(2)
        table.viewport().repaint()
    counter = Counter()
    table.viewport().installEventFilter(counter)
    all_ms = []
    for _ in range(rounds):
        sb.setValue(0)
        table.viewport().repaint()
        t0 = time.perf_counter()
        for i in range(steps):
            sb.setValue(i + 1)
            table.viewport().repaint()
        all_ms.append((time.perf_counter() - t0) * 1000 / steps)
    table.viewport().removeEventFilter(counter)
    return statistics.median(all_ms), counter.paints / (steps * rounds)


def measure_static_repaint(table, n=30):
    table.verticalScrollBar().setValue(0)
    table.viewport().repaint()
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        table.viewport().repaint()
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts)


def measure_rebuild(table, n, flags, rounds=5):
    """全量重建（refresh 的表体部分）：setRowCount(0)+逐行重建；
    同时测 setUpdatesEnabled 包裹版（候选优化的上限）。"""
    ts_plain, ts_upd = [], []
    for _ in range(rounds):
        table.setRowCount(0)
        t0 = time.perf_counter()
        populate(table, n, flags)
        ts_plain.append((time.perf_counter() - t0) * 1000)

        table.setRowCount(0)
        t0 = time.perf_counter()
        table.setUpdatesEnabled(False)
        populate(table, n, flags)
        table.setUpdatesEnabled(True)
        table.viewport().repaint()
        ts_upd.append((time.perf_counter() - t0) * 1000)
    reset_table(table, n, flags)
    return statistics.median(ts_plain), statistics.median(ts_upd)


def visible_rows(table):
    h = table.rowHeight(0) or 36
    return max(1, table.viewport().height() // h)


def cellwidget_count(table):
    return sum(1 for r in range(table.rowCount())
               for c in range(table.columnCount())
               if table.cellWidget(r, c) is not None)


def main():
    global app
    app = QApplication.instance() or QApplication([])
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=60)
    ap.add_argument("--steps", type=int, default=40)
    ap.add_argument("--rounds", type=int, default=3)
    args = ap.parse_args()

    out = {"env": {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "qt_platform": os.environ.get("QT_QPA_PLATFORM", ""),
        "rows": args.rows, "steps": args.steps, "rounds": args.rounds,
    }, "variants": []}

    print(f"rows={args.rows} steps={args.steps} rounds={args.rounds} "
          f"(offscreen 1240x520)")
    print(f"{'变体':14s}{'ms/帧':>8s}{'配对基线':>10s}{'Δ%':>9s}"
          f"{'paint/帧':>10s}{'静态repaint':>12s}")
    print("-" * 76)

    t_f = build_table(TableWidget, args.rows, FLAGS_FULL)
    t_q = build_table(QTableWidget, args.rows, FLAGS_FULL)

    for key, desc, flags in VARIANTS:
        reset_table(t_f, args.rows, flags)
        ms, paints = measure_scroll(t_f, args.steps, args.rounds)
        stat = measure_static_repaint(t_f)
        vis = visible_rows(t_f)
        n_cw = cellwidget_count(t_f)
        rp, ru = measure_rebuild(t_f, args.rows, flags)

        reset_table(t_f, args.rows, FLAGS_FULL)   # 配对基线（同表紧邻重测）
        base_ms, _ = measure_scroll(t_f, args.steps, args.rounds)
        delta = (ms / base_ms - 1) * 100
        print(f"{key:14s}{ms:8.2f}{base_ms:10.2f}{delta:+8.1f}%"
              f"{paints:10.1f}{stat:12.2f}   可见行={vis} cellWidget={n_cw}"
              f" 重建={rp:.1f}ms(包裹后{ru:.1f}ms)")
        out["variants"].append({
            "key": key, "desc": desc, "rows": args.rows,
            "ms_per_step": ms, "base_ms_per_step": base_ms,
            "delta_pct": delta, "paints_per_step": paints,
            "static_repaint_ms": stat, "visible_rows": vis,
            "cellwidgets": n_cw, "rebuild_ms": rp, "rebuild_wrapped_ms": ru,
        })

    # 原生 QTableWidget 对照（qfluentwidgets TableWidget 委托/样式的代价）
    reset_table(t_q, args.rows, FLAGS_FULL)
    ms_q, paints_q = measure_scroll(t_q, args.steps, args.rounds)
    reset_table(t_f, args.rows, FLAGS_FULL)
    base_f, _ = measure_scroll(t_f, args.steps, args.rounds)
    print(f"{'qt_plain':14s}{ms_q:8.2f}{base_f:10.2f}"
          f"{(ms_q / base_f - 1) * 100:+8.1f}%{paints_q:10.1f}")
    out["variants"].append({
        "key": "qt_plain", "desc": "原生 QTableWidget（无 fluent 委托/样式）",
        "rows": args.rows, "ms_per_step": ms_q,
        "base_ms_per_step": base_f,
        "delta_pct": (ms_q / base_f - 1) * 100, "paints_per_step": paints_q,
    })

    path = os.path.join(ROOT, "tools", "_scratch",
                        "perf_remote_scroll_result.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n结果已写入 {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
