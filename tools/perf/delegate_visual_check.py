# -*- coding: utf-8 -*-
"""P0-1 视觉回归验证：抓图对比 baseline / lean / plain 三种 delegate

P0-1 的核心风险是「换 delegate 会不会改变 UI 呈现」。本脚本对同一份真实数据、
同一滚动位置分别抓取表格视口位图，做像素级差异统计，并各自落盘 PNG 供肉眼比对。

用法：
    <venv>/Scripts/python.exe tools/perf/delegate_visual_check.py
"""
import os
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QStyledItemDelegate  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402

# 先导入 scroll_profile（其模块级即创建 QApplication），再复用该实例；
# 本脚本自行 QApplication([]) 会触发 "singleton 已存在" 的 RuntimeError
import tools.scroll_profile as _sp  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance()
from core.lean_table_delegate import LeanTableDelegate as _LeanDelegate  # noqa: E402

OUT_DIR = os.path.join(PROJECT_ROOT, "tools", "_scratch")
os.makedirs(OUT_DIR, exist_ok=True)


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


def flush(n=4):
    for _ in range(n):
        app.processEvents()


def build(variant, rows):
    from windows.aftersale.records import RecordsPage
    page = RecordsPage()
    page._cycles_loaded = True
    page._recalc_done = True
    page.resize(1600, 900)
    page.show()
    flush()
    if variant == "lean":
        page._table.setItemDelegate(_LeanDelegate(page._table))
    elif variant == "plain":
        page._table.setItemDelegate(QStyledItemDelegate(page._table))
    page._rows = rows
    page._populate(rows)
    flush()

    # 三种视觉状态：第 5 行勾选、第 4 行选中、第 2 行 hover。
    # 注意顺序：勾选走 item 状态（与 delegate 无关），选中/hover 依赖 delegate
    # 的 setSelectedRows/hoverRow 属性——原生 QStyledItemDelegate 没有这两个
    # 成员，直接 selectRow() 会抛 AttributeError，故统一走 selectionModel
    # 并对具备该成员的 delegate 手动同步。
    from PySide6.QtCore import Qt as _Qt, QItemSelectionModel as _SM
    it = page._table.item(5, 0)
    if it is not None:
        it.setCheckState(_Qt.CheckState.Checked)
    sel = page._table.selectionModel()
    sel.select(page._table.model().index(4, 0),
               _SM.SelectionFlag.Select | _SM.SelectionFlag.Rows)
    if hasattr(page._table.delegate, "setSelectedRows"):
        page._table.delegate.setSelectedRows(sel.selectedIndexes())
    if hasattr(page._table.delegate, "hoverRow"):
        page._table.delegate.hoverRow = 2

    sb = page._table.verticalScrollBar()
    sb.setValue(3)
    page._table.viewport().repaint()
    flush()
    return page


def grab(page):
    img = page._table.viewport().grab().toImage().convertToFormat(
        QImage.Format.Format_ARGB32)
    # 视口抓图未绘制区域是透明的（预览时显示为纯黑，会把浅色主题的
    # 黑色文本一起"吞"掉），合成到白底才是肉眼所见的效果
    canvas = QImage(img.size(), QImage.Format.Format_ARGB32)
    canvas.fill(0xFFFFFFFF)
    from PySide6.QtGui import QPainter
    p = QPainter(canvas)
    p.drawImage(0, 0, img)
    p.end()
    return canvas


def diff(a: QImage, b: QImage):
    if a.size() != b.size():
        return None
    w, h = a.width(), a.height()
    total = w * h
    nz = 0
    maxd = 0
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            pa, pb = a.pixelColor(x, y), b.pixelColor(x, y)
            d = max(abs(pa.red() - pb.red()), abs(pa.green() - pb.green()),
                    abs(pa.blue() - pb.blue()))
            if d > 8:
                nz += 1
            if d > maxd:
                maxd = d
    sampled = (h // 2 + (1 if h % 2 else 0)) * (w // 2 + (1 if w % 2 else 0))
    return nz / sampled * 100, maxd


def main():
    rows = load_rows(60)
    imgs = {}
    pages = {}
    for v in ("baseline", "lean", "plain"):
        pages[v] = build(v, rows)
        imgs[v] = grab(pages[v])
        p = os.path.join(OUT_DIR, f"perf_delegate_{v}.png")
        imgs[v].save(p)
        print(f"[{v:9s}] 已抓图 {imgs[v].width()}x{imgs[v].height()} -> {p}")

    print()
    for v in ("lean", "plain"):
        r = diff(imgs["baseline"], imgs[v])
        if r is None:
            print(f"baseline vs {v}: 尺寸不一致，无法比较")
            continue
        pct, maxd = r
        print(f"baseline vs {v:6s}: 显著差异像素占比 {pct:6.2f}%  "
              f"最大通道差 {maxd:3d}")


if __name__ == "__main__":
    main()
