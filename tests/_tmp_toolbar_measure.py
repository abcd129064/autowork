# -*- coding: utf-8 -*-
"""测量工具栏与下方列表区域之间的垂直间距/重叠"""
import sys

sys.path.insert(0, r"C:\Users\shen_zhe\Desktop\autowork")
from PySide6.QtCore import QTimer, QPoint
from PySide6.QtWidgets import QApplication

from main_window.main_window import MainWindow

app = QApplication(sys.argv)
w = MainWindow()
w.resize(1440, 900)
w.show()


def measure():
    ui = w.ui
    # 关键控件到窗口的全局 y
    def top_y(o):
        return o.mapTo(w, QPoint(0, 0)).y()

    def bottom_y(o):
        return o.mapTo(w, QPoint(0, 0)).y() + o.height()

    ts = ui.toolbar_scroll
    tw = ui.toolbar_widget
    r1 = ui.row1_container
    r2 = ui.row2_container
    sp = ui.splitter
    idl = ui.id_list
    lvl = ui.local_video_list

    print("=== toolbar_scroll (QScrollArea) ===")
    print("  geo      global:", top_y(ts), "->", bottom_y(ts), " h =", ts.height())
    print("  maxHeight:", ts.maximumHeight())
    print("=== toolbar_widget ===")
    print("  geo      global:", top_y(tw), "->", bottom_y(tw), " h =", tw.height())
    print("=== row1_container ===")
    print("  geo      global:", top_y(r1), "->", bottom_y(r1), " h =", r1.height())
    print("=== row2_container (第二列) ===")
    print("  geo      global:", top_y(r2), "->", bottom_y(r2), " h =", r2.height())
    print("=== 下方列表区域 ===")
    print("  splitter top global:", top_y(sp), " h =", sp.height())
    print("  id_list   top global:", top_y(idl))
    print("  lv_list   top global:", top_y(lvl))

    row2_bottom = bottom_y(r2)
    spl_top = top_y(sp)
    gap = spl_top - row2_bottom
    print()
    print("row2 bottom -> splitter top gap =", gap, "px")
    print("toolbar_scroll bottom -> splitter top gap =", spl_top - bottom_y(ts), "px")
    print("view 背景检查: toolbar_widget bottom -> row2 bottom 差 =", bottom_y(tw) - row2_bottom, "px")

    # 判断是否重叠
    if gap < 0:
        print(f"!!! 重叠 {abs(gap)}px：row2_container 底部压到 splitter 顶部")
    else:
        print(f"OK：无重叠，间距 {gap}px")
    app.quit()


QTimer.singleShot(1200, measure)
QTimer.singleShot(8000, app.quit)
app.exec()
