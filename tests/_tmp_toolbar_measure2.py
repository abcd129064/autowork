# -*- coding: utf-8 -*-
"""精确测量：toolbar_scroll 视口几何 + row2 底部到下方控件顶部的像素距离"""
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
    def top_y(o):
        return o.mapTo(w, QPoint(0, 0)).y()
    def bot_y(o):
        return o.mapTo(w, QPoint(0, 0)).y() + o.height()

    ts = ui.toolbar_scroll
    tw = ui.toolbar_widget
    r1 = ui.row1_container
    r2 = ui.row2_container
    sp = ui.splitter
    idx = ui.id_list
    lvl = ui.local_video_list
    log = ui.log_list

    # viewport 几何
    vp = ts.viewport()
    print("== toolbar_scroll ==")
    print("  scroll     geo:", ts.geometry())
    print("  viewport   geo:", vp.geometry())
    print("  scroll top/bot global:", top_y(ts), bot_y(ts))
    print("  viewport top/bot global:", vp.mapTo(w, QPoint(0,0)).y(),
          vp.mapTo(w, QPoint(0,0)).y() + vp.height())
    print("  widget in viewport pos:", tw.pos(), " size:", tw.size())
    print("  vScrollBar visible:", ts.verticalScrollBar().isVisible(),
          " max:", ts.verticalScrollBar().maximum())

    print("== row2 / 下方 ==")
    print("  row2 top/bot global:", top_y(r2), bot_y(r2))
    print("  splitter top global:", top_y(sp))
    print("  id_list top/bot global:", top_y(idx), bot_y(idx))
    print("  lv_list top/bot global:", top_y(lvl), bot_y(lvl))
    print("  log_list top global:", top_y(log))

    # 关键：row2 底部到 id_list 顶部
    print()
    print("  row2_bottom -> id_list_top  gap =", top_y(idx) - bot_y(r2))
    print("  row2_bottom -> lv_list_top  gap =", top_y(lvl) - bot_y(r2))
    print("  row2_bottom -> splitter_top gap =", top_y(sp) - bot_y(r2))
    print("  scroll_bottom->splitter_top gap =", top_y(sp) - bot_y(ts))
    app.quit()


QTimer.singleShot(1200, measure)
QTimer.singleShot(8000, app.quit)
app.exec()
