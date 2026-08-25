# -*- coding: utf-8 -*-
"""TableWidget 性能压测脚本（售后 / 跑视频记录页）

测点：
1. bench_fill     —— 表格填充耗时（售后 13 列 / 跑视频 11 列，每页 50 行）
2. bench_scroll   —— 模拟滚轮滚动耗时（平滑动画 ON vs NO_SMOOTH 对比）
3. bench_edit_dlg —— 双击打开卡片（编辑弹窗）构建 + validate + collect 耗时
4. widget_count   —— 表格内 cellWidget 数量（滚动开销的直接指标）

用法（需 venv 环境，offscreen 自动开启）：
    python tools/perf_benchmark.py [--rows N] [--scroll-steps M] [--rounds K]

基线示例：
    <venv>/Scripts/python.exe tools/perf_benchmark.py
"""
import argparse
import os
import statistics
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QPoint, QPointF, QElapsedTimer, QEventLoop
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from qfluentwidgets import TableWidget, SmoothMode

app = QApplication([])


# ---------------- 假数据 ----------------

def _make_aftersale_rows(n: int) -> list:
    rows = []
    for i in range(n):
        rows.append(dict(
            id=i + 1, created_at="2026-08-25 10:30:00", creator="测试员",
            occurred_at="2026-08-25 09:00:00",
            issue_type="球桌问题" if i % 3 else "设备故障",
            room_name=f"球房{i % 5 + 1}", region="华东", table_no=f"T{i % 12 + 1}",
            problem="击球点位偏移，需校准定位器水平仪与杆臂阻尼",
            cause="长期使用磨损" if i % 2 else "安装时未校准",
            solution="重新校准定位器并紧固螺丝",
            resolved="是" if i % 2 else "否",
            is_our_problem="是" if i % 3 == 0 else "否",
            is_initiative="是" if i % 4 == 0 else "否",
            response_time="2026-08-25 11:00:00", resolver="维修员甲",
            is_important=(i % 7 == 0),
        ))
    return rows


def _make_ledger_rows(n: int) -> list:
    cats = ["问题", "未复现", "精度", "使用"]
    kinds = {"问题": ["点位偏移", "不进球", "黑八提前落袋"],
             "未复现": ["未复现"], "精度": ["精度", "走位差"],
             "使用": ["使用"]}
    rows = []
    for i in range(n):
        cat = cats[i % 4]
        rows.append(dict(
            id=i + 1, created_at="2026-08-25 10:30:00", category=cat,
            kind=kinds[cat][i % len(kinds[cat])],
            room_name=f"球房{i % 5 + 1}", video_name=f"video_{i % 20}.mp4",
            frame=str(400 + i), description="击球点位偏移，需要回看录像逐帧核对定位器数据",
            repro="是" if i % 3 == 0 else "否", new_program="是" if i % 4 == 0 else "否",
            signer="张三" if i % 3 else "李四",
        ))
    return rows


# ---------------- 计时工具 ----------------

def timed(fn, rounds=3):
    samples = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.median(samples), min(samples), samples


def _count_cell_widgets(table: TableWidget) -> int:
    n = 0
    for r in range(table.rowCount()):
        for c in range(table.columnCount()):
            if table.cellWidget(r, c) is not None:
                n += 1
    return n


def _flush_events(ms=0):
    """消费排队的布局/重绘事件"""
    loop = QEventLoop()
    from PySide6.QtCore import QTimer
    if ms:
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
    else:
        app.processEvents()
        app.processEvents()


# ---------------- 填充 ----------------

def bench_fill(page, rows, label, populate_has_arg=True):
    def _run():
        page._rows = rows
        if populate_has_arg:
            page._populate(rows)
        else:
            page._populate()
        _flush_events()
    med, _min, _ = timed(_run)
    n_widgets = _count_cell_widgets(page._table)
    print(f"[fill] {label}: median={med:.1f}ms min={_min:.1f}ms "
          f"rows={len(rows)} cellWidgets={n_widgets}")
    return med, n_widgets


# ---------------- 滚动 ----------------

def _wheel_event(x=200, y=200, delta=120):
    return QWheelEvent(
        QPointF(x, y), QPointF(x, y), QPoint(0, 0), QPoint(0, delta),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False)


def bench_scroll(table: TableWidget, steps: int, smooth: bool):
    """发送 steps 个滚轮事件，等待动画/重绘结束，返回总耗时（ms）"""
    ss = table.scrollDelagate.verticalSmoothScroll
    hs = getattr(table.scrollDelagate, "horizonSmoothScroll", None) \
        or getattr(table.scrollDelagate, "horizontalSmoothScroll", None)
    if not smooth:
        ss.setSmoothMode(SmoothMode.NO_SMOOTH)
        if hs is not None:
            hs.setSmoothMode(SmoothMode.NO_SMOOTH)
    else:
        ss.setSmoothMode(SmoothMode.LINEAR)
        if hs is not None:
            hs.setSmoothMode(SmoothMode.LINEAR)
    # 预置滚动位置：让 delegate.vScrollBar（未安装的假条，0..99）离开
    # minimum，否则 eventFilter 判定 verticalAtEnd 恒 True、滚轮永远走
    # 原生路径，平滑引擎不参与（这正是生产环境中平滑滚动近乎无效的原因）
    sb = table.verticalScrollBar()
    sb.setValue(sb.maximum() // 2)
    dv = table.scrollDelagate.vScrollBar
    dv.setValue(50, useAni=False)
    _flush_events()
    el = QElapsedTimer()
    el.start()
    for _ in range(steps):
        QApplication.sendEvent(table.viewport(), _wheel_event())
    # 等待平滑动画队列清空 / 原生滚动重绘完成
    guard = 0
    while guard < 200:  # 最多 200 * 25ms = 5s
        app.processEvents()
        engine = getattr(ss, "fixedStepScrollEngine", None)
        queue = getattr(engine, "stepsLeftQueue", None) if engine else None
        timer = getattr(engine, "smoothMoveTimer", None) if engine else None
        busy = (queue is not None and len(queue) > 0) or \
               (timer is not None and timer.isActive())
        if not busy:
            break
        from PySide6.QtCore import QTimer
        loop = QEventLoop()
        QTimer.singleShot(25, loop.quit)
        loop.exec()
        guard += 1
    _flush_events()
    return el.elapsed()


# ---------------- 双击打开卡片 ----------------

def bench_edit_dlg(make_dlg, rows, label, rounds=3):
    rec = dict(rows[0])
    med_init, _, _ = timed(lambda: make_dlg(dict(rec)), rounds)
    # validate / collect 需要实例
    dlg = make_dlg(dict(rec))
    med_val, _, _ = timed(dlg.form.validate, rounds)
    med_col, _, _ = timed(dlg.form.collect, rounds)
    dlg.deleteLater()
    print(f"[dlg ] {label}: init={med_init:.1f}ms validate={med_val:.1f}ms "
          f"collect={med_col:.1f}ms")
    return med_init + med_val + med_col


# ---------------- 主流程 ----------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=50)
    ap.add_argument("--scroll-steps", type=int, default=20)
    ap.add_argument("--rounds", type=int, default=3)
    args = ap.parse_args()
    rows_n, steps, rounds = args.rows, args.scroll_steps, args.rounds

    # 售后记录页
    from windows.aftersale.records import RecordsPage as AftersalePage
    from windows.aftersale.dialogs import EditRecordDialog
    a_page = AftersalePage()
    a_page.resize(1440, 800)
    a_page.show()
    _flush_events()
    a_rows = _make_aftersale_rows(rows_n)
    print("== 售后记录页 ==")
    bench_fill(a_page, a_rows, "aftersale _populate")
    t_smooth = bench_scroll(a_page._table, steps, smooth=True)
    t_nosmooth = bench_scroll(a_page._table, steps, smooth=False)
    print(f"[scroll] aftersale: smooth={t_smooth}ms NO_SMOOTH={t_nosmooth}ms "
          f"({steps} steps)")
    bench_edit_dlg(
        lambda rec: EditRecordDialog(rec, a_page), a_rows,
        "aftersale EditRecordDialog", rounds)

    # 跑视频记录页
    from windows.ledger.records import RecordsPage as LedgerPage
    from windows.ledger.records import EditLedgerDialog
    l_page = LedgerPage()
    l_page.resize(1440, 800)
    l_page.show()
    _flush_events()
    l_rows = _make_ledger_rows(rows_n)
    print("== 跑视频记录页 ==")
    bench_fill(l_page, l_rows, "ledger _populate", populate_has_arg=False)
    t_smooth = bench_scroll(l_page._table, steps, smooth=True)
    t_nosmooth = bench_scroll(l_page._table, steps, smooth=False)
    print(f"[scroll] ledger: smooth={t_smooth}ms NO_SMOOTH={t_nosmooth}ms "
          f"({steps} steps)")
    bench_edit_dlg(
        lambda rec: EditLedgerDialog(rec, l_page), l_rows,
        "ledger EditLedgerDialog", rounds)


if __name__ == "__main__":
    main()
