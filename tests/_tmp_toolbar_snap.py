# -*- coding: utf-8 -*-
"""截图：主窗口顶部（工具栏 + 下方列表顶部），检查垂直贴合/重叠"""
import sys

sys.path.insert(0, r"C:\Users\shen_zhe\Desktop\autowork")
from PySide6.QtCore import QTimer, QRect
from PySide6.QtWidgets import QApplication

from main_window.main_window import MainWindow

app = QApplication(sys.argv)
w = MainWindow()
w.resize(1440, 900)
w.show()


def snap():
    roi = w.rect()
    roi.setHeight(200)  # 截取顶部 200px
    pm = w.grab(roi)
    pm.save(r"C:\Users\shen_zhe\Desktop\autowork\tests\_tmp_toolbar_top.png")
    print("OK saved toolbar top")
    app.quit()


QTimer.singleShot(1200, snap)
QTimer.singleShot(8000, app.quit)
app.exec()
