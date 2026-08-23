# -*- coding: utf-8 -*-
"""不同窗口宽度下：工具栏折行/滚动条场景的间距测量"""
import sys

sys.path.insert(0, r"C:\Users\shen_zhe\Desktop\autowork")
from PySide6.QtCore import QTimer, QPoint
from PySide6.QtWidgets import QApplication

from main_window.main_window import MainWindow

app = QApplication(sys.argv)
w = MainWindow()
w.resize(1440, 900)
w.show()

results = []

def top_y(o):
    return o.mapTo(w, QPoint(0, 0)).y()
def bot_y(o):
    return o.mapTo(w, QPoint(0, 0)).y() + o.height()

def measure(width):
    w.resize(width, 900)
    ui = w.ui

def run_next_change(widths, idx):
    if idx >= len(widths):
        app.quit()
        return
    width = widths[idx]
    w.resize(width, 900)
    ui = w.ui
    # 等布局稳定（FlowLayout 折行 + _adjust_height 更新）
    def after():
        ts, tw, r2, sp = ui.toolbar_scroll, ui.toolbar_widget, ui.row2_container, ui.splitter
        sb = ts.verticalScrollBar()
        r2b = bot_y(r2)
        spt = top_y(sp)
        gap = spt - r2b
        sgap = spt - bot_y(ts)
        results.append((width, tw.height(), sb.isVisible(), sb.maximum(), gap, sgap))
        print(f"width={width:5d}  toolbar_widget_h={tw.height():3d}  scrollbarVisible={sb.isVisible()}  vbarMax={sb.maximum()}  row2->splitter gap={gap:+3d}  scroll->splitter gap={sgap:+3d}")
        run_next_change(widths, idx + 1)
    QTimer.singleShot(400, after)


widths = [1440, 1280, 1100, 1000, 920, 850, 800, 760, 720]
QTimer.singleShot(1000, lambda: run_next_change(widths, 0))
QTimer.singleShot(15000, app.quit)
app.exec()

print("\n=== 汇总（含真实重叠判断） ===")
for row in results:
    width, th, sv, vmax, gap, sgap = row
    flag = ""
    if gap < 0:
        flag = "   <-- 重叠!"
    print(f"width={width:5d}  th={th:3d}  vbar={sv}  vbarMax={vmax:3d}  gap={gap:+3d}  sgap={sgap:+3d}{flag}")
