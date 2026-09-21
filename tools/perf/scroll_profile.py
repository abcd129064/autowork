# -*- coding: utf-8 -*-
"""滚动卡顿根因隔离实验（调研用，非生产代码）

对售后记录页表格做单变量对照，量化各因素对「每步滚动耗时」的贡献：
  baseline        当前线上实现（60 行 + 每行操作列 cellWidget + 行 tooltip + 交替行色）
  no_ops_widget   去掉操作列 cellWidget（改为纯文本占位）
  no_tooltip      去掉每格 setToolTip
  no_alt_rows     关闭交替行色
  plain_delegate  换回原生 QStyledItemDelegate（剥离 qfluentwidgets 抗锯齿圆角绘制）
  rows_15         行数 60 → 15（验证 Qt 是否真的只画可见行）
  long_text       problem/cause/solution 拉长到 400 字符（验证「大量文本」假设）
  per_pixel       垂直滚动模式改 ScrollPerPixel

用法：
    <venv>/Scripts/python.exe tools/perf/scroll_profile.py [--rows 60] [--steps 40]
                                                           [--profile]
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

from PySide6.QtCore import Qt, QEvent, QObject
from PySide6.QtWidgets import (QApplication, QStyledItemDelegate,
                               QTableWidgetItem, QAbstractItemView)

app = QApplication([])

from qfluentwidgets import TableWidget, SmoothMode  # noqa: E402


# ---------------- 真实数据 ----------------

def load_real_rows(limit: int) -> list:
    """从本地 SQLite 取真实售后记录（文本长度即生产真实分布）"""
    path = os.path.join(PROJECT_ROOT, "database", "tables.db")
    if not os.path.exists(path):
        return []
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute("select * from aftersale_records limit ?", (limit,))
        return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        con.close()


def scale_rows(rows, n):
    if not rows:
        rows = [dict(id=i + 1, problem="x", cause="y", solution="z",
                     room_name="球房", region="华东", table_no="T1",
                     creator="甲", resolver="乙", created_at="2026-08-25 10:30:00",
                     occurred_at="2026-08-25 09:00:00", issue_type="球桌问题",
                     resolved="否", is_our_problem="否", is_initiative="否",
                     response_time="") for i in range(5)]
    out = []
    while len(out) < n:
        for r in rows:
            d = dict(r)
            d["id"] = len(out) + 1
            out.append(d)
            if len(out) >= n:
                break
    return out


def long_text_rows(rows):
    out = []
    for r in rows:
        d = dict(r)
        d["problem"] = "击球点位偏移量异常需要重新校准定位器水平仪并复核杆臂阻尼参数" * 12
        d["cause"] = "长期使用磨损导致定位器安装面形变且未及时安排周期性校准维护" * 12
        d["solution"] = "重新校准定位器并紧固全部螺丝后复测三十次验证走位精度恢复" * 12
        out.append(d)
    return out


# ---------------- 分阶段 delegate（拆解 qfw 绘制内部成本） ----------------

def make_staged_delegate(antialias=True, bg=True, text=True):
    """复刻 qfluentwidgets TableItemDelegate.paint，按开关逐段裁剪

    用于把「qfw delegate 相对原生多出的耗时」拆成三段：
      抗锯齿 renderHint / 圆角矩形背景 / super().paint（C++ 文本排版+省略号）
    """
    from qfluentwidgets.components.widgets.table_view import TableItemDelegate
    from qfluentwidgets.common.color import autoFallbackThemeColor
    from qfluentwidgets import isDarkTheme
    from PySide6.QtGui import QColor, QBrush, QPainter
    from PySide6.QtCore import Qt

    class StagedDelegate(TableItemDelegate):
        def paint(self, painter, option, index):
            painter.save()
            painter.setPen(Qt.NoPen)
            if antialias:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setClipping(True)
            painter.setClipRect(option.rect)
            option.rect.adjust(0, self.margin, 0, -self.margin)

            if bg:
                isHover = self.hoverRow == index.row()
                isPressed = self.pressedRow == index.row()
                isAlternate = (index.row() % 2 == 0
                               and self.parent().alternatingRowColors())
                isDark = isDarkTheme()
                c = 255 if isDark else 0
                alpha = 0
                if index.row() not in self.selectedRows:
                    if isPressed:
                        alpha = 9 if isDark else 6
                    elif isHover:
                        alpha = 12
                    elif isAlternate:
                        alpha = 5
                else:
                    alpha = 17 if not (isPressed or isHover) else (
                        15 if isPressed else 25)
                b = index.data(Qt.ItemDataRole.BackgroundRole)
                painter.setBrush(b if b else QColor(c, c, c, alpha))
                self._drawBackground(painter, option, index)

                if (index.row() in self.selectedRows
                        and index.column() == 0
                        and self.parent().horizontalScrollBar().value() == 0):
                    self._drawIndicator(painter, option, index)

            if index.data(Qt.ItemDataRole.CheckStateRole) is not None:
                self._drawCheckBox(painter, option, index)

            painter.restore()
            if text:
                super(StagedDelegate, self).paint(painter, option, index)

    return StagedDelegate


# ---------------- 预渲染（绘制缓存）delegate ----------------

def _CacheDelegate(view):
    """把每个单元格的绘制结果缓存成 QPixmap，滚动时只做 blit

    用于验证「预渲染/缓存」思路的收益上限：绘制一次，之后每帧只 drawPixmap。
    """
    from qfluentwidgets.components.widgets.table_view import TableItemDelegate
    from PySide6.QtGui import QPixmap, QPixmapCache, QPainter

    class CacheDelegate(TableItemDelegate):
        def paint(self, painter, option, index):
            w, h = option.rect.width(), option.rect.height()
            if w <= 0 or h <= 0:
                return
            text = index.data(Qt.DisplayRole)
            key = f"{index.row()}|{index.column()}|{w}|{h}|{text}"
            pm = QPixmapCache.find(key)
            if pm is None or pm.isNull():
                pm = QPixmap(w, h)
                pm.fill(Qt.transparent)
                opt = type(option)(option)
                opt.rect.moveTo(0, 0)
                p = QPainter(pm)
                super(CacheDelegate, self).paint(p, opt, index)
                p.end()
                QPixmapCache.insert(key, pm)
            painter.drawPixmap(option.rect, pm)

    return CacheDelegate(view)


# ---------------- LeanTableDelegate（P0-1 正式实现） ----------------

def _LeanDelegate(view):
    """P0-1 正式实现：core/lean_table_delegate.py（原型已迁入生产代码）"""
    from core.lean_table_delegate import LeanTableDelegate
    return LeanTableDelegate(view)

# ---------------- 页面构建 ----------------

def build_page(rows, variant):
    from windows.aftersale.records import RecordsPage
    page = RecordsPage()
    page.resize(1600, 900)
    page._table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)

    if variant == "no_ops_widget":
        page._make_ops_cell = lambda rec, *a, **k: None
    if variant == "no_tooltip":
        page._row_tooltip = lambda rec: ""
    if variant == "no_alt_rows":
        page._table.setAlternatingRowColors(False)
    if variant == "plain_delegate":
        page._table.setItemDelegate(QStyledItemDelegate(page._table))
    if variant == "aa_off":
        page._table.setItemDelegate(
            make_staged_delegate(antialias=False)(page._table))
    if variant == "bg_off":
        page._table.setItemDelegate(
            make_staged_delegate(bg=False)(page._table))
    if variant == "text_off":
        page._table.setItemDelegate(
            make_staged_delegate(text=False)(page._table))
    if variant == "long_text_noelide":
        page._table.setTextElideMode(Qt.TextElideMode.ElideNone)
    if variant == "lean_delegate":
        page._table.setItemDelegate(_LeanDelegate(page._table))
    if variant == "cache_paint":
        page._table.setItemDelegate(_CacheDelegate(page._table))
    if variant == "fewer_cols":
        import windows.aftersale.records as _rec_mod
        _rec_mod.TABLE_COLUMNS = _rec_mod.TABLE_COLUMNS[:6]
    if variant == "per_pixel":
        page._table.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)

    # 隔离纯渲染：show() 会触发 showEvent → 周期物化重算 + 周期选项 + 数据查询
    # 三条 worker 链直连远程 MySQL（实测滚动 1.2s 内 172 次 pymysql 收包、
    # 0.22s 网络等待），会把 DB 耗时算进滚动耗时里。这里先置位跳过加载链。
    page._cycles_loaded = True
    page._recalc_done = True

    # 必须 show()：隐藏控件的 repaint()/paintEvent 会被 Qt 直接跳过，
    # 测到的耗时恒为 ~0（首版踩坑），且 viewport 高度为 0 导致无法滚动
    page.show()
    flush()
    page._rows = rows
    page._populate(rows)
    flush()
    return page


def flush(times=3):
    for _ in range(times):
        app.processEvents()
        app.processEvents()


# ---------------- 事件计数 ----------------

class Counter(QObject):
    def __init__(self):
        super().__init__()
        self.paints = 0
        self.moves = 0

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Type.Paint:
            self.paints += 1
        elif t in (QEvent.Type.Move, QEvent.Type.Resize):
            self.moves += 1
        return False


# ---------------- 滚动计时 ----------------

def measure_scroll(table, steps: int, rounds: int = 3) -> tuple:
    """逐步滚动并强制同步重绘，返回 (每步 ms, 每步 paint 次数)"""
    sb = table.verticalScrollBar()
    try:
        table.scrollDelagate.verticalSmoothScroll.setSmoothMode(
            SmoothMode.NO_SMOOTH)
    except Exception:
        pass

    # 预热（首次滚动含惰性布局/字体缓存，剔除）
    for _ in range(3):
        sb.setValue(0)
        table.viewport().repaint()
        sb.setValue(2)
        table.viewport().repaint()

    counter = Counter()
    table.viewport().installEventFilter(counter)

    samples = []
    for _ in range(rounds):
        sb.setValue(0)
        table.viewport().repaint()
        t0 = time.perf_counter()
        for i in range(steps):
            sb.setValue(i + 1)
            table.viewport().repaint()
        samples.append((time.perf_counter() - t0) * 1000 / steps)
    table.viewport().removeEventFilter(counter)
    return statistics.median(samples), counter.paints / (steps * rounds)


def count_child_widgets(table) -> int:
    n = 0
    for r in range(table.rowCount()):
        for c in range(table.columnCount()):
            if table.cellWidget(r, c) is not None:
                n += 1
    return n


def visible_rows(table) -> int:
    vp = table.viewport()
    h = table.rowHeight(0) or 40
    return max(1, vp.height() // h)


# ---------------- 主流程 ----------------

VARIANTS = [
    ("baseline", "当前线上实现"),
    ("no_ops_widget", "去掉操作列 cellWidget"),
    ("no_tooltip", "去掉每格 tooltip"),
    ("no_alt_rows", "关闭交替行色"),
    ("plain_delegate", "换原生 delegate"),
    ("rows_15", "行数 60→15"),
    ("long_text", "文本拉长到 400 字"),
    ("per_pixel", "ScrollPerPixel"),
    ("staged_full", "分阶段 delegate 全开（对照组）"),
    ("aa_off", "关抗锯齿"),
    ("bg_off", "关圆角背景绘制"),
    ("text_off", "关文本绘制（只画背景）"),
    ("long_text_noelide", "长文本 + ElideNone"),
    ("lean_delegate", "P0-1 轻量 delegate 原型"),
    ("cache_paint", "预渲染：单元格绘制缓存"),
    ("fewer_cols", "列数 13→7"),
]


def _cleanup(page):
    """释放上一轮页面：deleteLater 需显式派发 DeferredDelete，否则控件堆积
    会让后续变体越测越慢（首版踩坑：顺序靠后的变体系统性偏慢）"""
    page.deleteLater()
    flush()
    from PySide6.QtWidgets import QApplication as _App
    _App.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=60)
    ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--profile", action="store_true")
    args = ap.parse_args()

    real = load_real_rows(200)
    print(f"真实样本: {len(real)} 条；每页 {args.rows} 行\n")

    if args.profile:
        import cProfile
        import pstats
        rows = scale_rows(real, args.rows)
        page = build_page(rows, "baseline")
        pr = cProfile.Profile()
        pr.enable()
        measure_scroll(page._table, args.steps, rounds=1)
        pr.disable()
        pstats.Stats(pr).sort_stats("tottime").print_stats(22)
        return

    print(f"{'变体':18s}{'说明':26s}{'ms/帧':>8s}{'配对基线':>10s}"
          f"{'Δ%':>9s}{'控件数':>8s}")
    print("-" * 79)
    results = []
    for key, desc in VARIANTS:
        n_rows = 15 if key == "rows_15" else args.rows
        rows = scale_rows(real, n_rows)
        if key.startswith("long_text"):
            rows = long_text_rows(rows)
        page = build_page(rows, key)
        ms, paints = measure_scroll(page._table, args.steps, args.rounds)
        n_cw = count_child_widgets(page._table) if key != "no_ops_widget" else 0
        vis = visible_rows(page._table)
        _cleanup(page)

        # 配对基线：每个变体紧邻重测一次基线，抵消控件堆积/内存漂移。
        # 基线恒用正常长度文本（否则长文本变体的对照也变长，Δ 失去意义）
        b_page = build_page(scale_rows(real, n_rows), "baseline")
        base_ms, _ = measure_scroll(b_page._table, args.steps, args.rounds)
        _cleanup(b_page)

        delta = (ms / base_ms - 1) * 100
        print(f"{key:18s}{desc:26s}{ms:8.2f}{base_ms:10.2f}"
              f"{delta:+8.1f}%{n_cw:8d}   可见行={vis}")
        results.append((key, desc, ms, base_ms, delta))

    print("\n结论提示：Δ% 为该变体相对「紧邻重测的配对基线」的变化，"
          "负数=更快。")


if __name__ == "__main__":
    main()
