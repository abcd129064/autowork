# -*- coding: utf-8 -*-
"""实测：row2 按钮底部与下方列表顶部交界，放大截图 + 逐行像素分析"""
import sys

sys.path.insert(0, r"C:\Users\shen_zhe\Desktop\autowork")
from PySide6.QtCore import QTimer, QPoint
from PySide6.QtWidgets import QApplication

from main_window.main_window import MainWindow

app = QApplication(sys.argv)
w = MainWindow()
w.resize(1440, 900)
w.show()

def top_y(o):
    return o.mapTo(w, QPoint(0, 0)).y()
def bot_y(o):
    return o.mapTo(w, QPoint(0, 0)).y() + o.height()

def snap():
    ui = w.ui
    ts, tw, r2, sp = ui.toolbar_scroll, ui.toolbar_widget, ui.row2_container, ui.splitter
    idx, lvl, log = ui.id_list, ui.local_video_list, ui.log_list
    print("toolbar_scroll", top_y(ts), "->", bot_y(ts), "h", ts.height())
    print("toolbar_widget", top_y(tw), "->", bot_y(tw), "h", tw.height())
    print("row2_container", top_y(r2), "->", bot_y(r2), "h", r2.height())
    print("splitter", top_y(sp))
    print("id_list", top_y(idx))
    print("lv_list", top_y(lvl))
    print("log_list", top_y(log))
    print("row2_bottom -> splitter_top gap =", top_y(sp) - bot_y(r2))
    print("row2_bottom -> id_list_top  gap =", top_y(idx) - bot_y(r2))
    # 截图：从 row2 顶部(逻辑101)到下方列表(逻辑 + 40) 的完整横带，放大
    roi = w.rect()
    roi.setY(int(90 * (w.width() / 1440 if w.width() else 1)))  # 不精确，直接用物理
    # 直接截取窗口上部，再用 cv2 放大研究
    full = w.grab().toImage().save(r"C:\Users\shen_zhe\Desktop\autowork\tests\_ft_full.png")
    app.quit()

QTimer.singleShot(1200, snap)
QTimer.singleShot(8000, app.quit)
app.exec()
print("saved _ft_full.png")
