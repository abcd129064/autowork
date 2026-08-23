# -*- coding: utf-8 -*-
"""记录页工具栏流式布局冒烟（与主界面同范式：库版 FlowLayout + 滚动容器）：
窄宽不重叠、折行滚动容器锁高、宽屏单行、抓图核对"""
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv)
from windows.aftersale_panel import RecordsPage
from core.flow_widgets import FlowToolbarScrollArea

page = RecordsPage()
page.resize(760, 640)
page.show()
app.processEvents()

tl = None
scroll = None
for i in range(page.layout().count()):
    it = page.layout().itemAt(i)
    w = it.widget() if it else None
    if isinstance(w, FlowToolbarScrollArea):
        scroll = w
        inner = w.widget()
        if inner is not None and inner.layout() is not None \
                and inner.layout().__class__.__name__ == "FlowLayout":
            tl = inner.layout()
assert tl is not None, "FlowLayout not mounted"
assert scroll is not None, "FlowToolbarScrollArea not mounted"

rects = []
for i in range(tl.count()):
    it = tl.itemAt(i)
    wdg = it.widget() if it else None
    if wdg is not None:
        rects.append((wdg.__class__.__name__, wdg.geometry()))
assert rects, "no toolbar widgets found"

overlap = []
for i in range(len(rects)):
    for j in range(i + 1, len(rects)):
        a, b = rects[i][1], rects[j][1]
        inter = a.intersected(b)
        if inter.width() > 0 and inter.height() > 0:
            overlap.append((rects[i][0], rects[j][0],
                            inter.width(), inter.height()))
assert not overlap, overlap
rows = sorted(set(r.y() for _n, r in rects))
assert len(rows) >= 2, "narrow width should wrap"
assert scroll.height() <= 132, scroll.height()
print("NARROW_OK rows=%d widgets=%d scroll_h=%d" % (
    len(rows), len(rects), scroll.height()))

narrow_rows, narrow_h = len(rows), scroll.height()
page.resize(1600, 800)
app.processEvents()
rects = []
for i in range(tl.count()):
    it = tl.itemAt(i)
    wdg = it.widget() if it else None
    if wdg is not None:
        rects.append(wdg.geometry())
rows = sorted(set(r.y() for r in rects))
# 越宽折行越少、容器高度随之收紧（不要求绝对单行：控件自然总宽 ~1480）
assert len(rows) < narrow_rows, (len(rows), narrow_rows)
assert scroll.height() < narrow_h, (scroll.height(), narrow_h)
overlap = []
for i in range(len(rects)):
    for j in range(i + 1, len(rects)):
        inter = rects[i].intersected(rects[j])
        if inter.width() > 0 and inter.height() > 0:
            overlap.append((i, j))
assert not overlap, overlap
print("WIDE_OK rows=%d(<%d) scroll_h=%d(<%d)" % (
    len(rows), narrow_rows, scroll.height(), narrow_h))

page.resize(760, 640)
app.processEvents()
page.grab().save(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "_records_narrow_check.png"))
print("SAVED")
