# -*- coding: utf-8 -*-
"""验证 _MediaPlayerDialog 端到端渲染（含英文文字修正）"""
import sys

sys.path.insert(0, r"C:\Users\shen_zhe\Desktop\autowork")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from windows.management.widget_page import _MediaPlayerDialog

app = QApplication(sys.argv)
dlg = _MediaPlayerDialog()
dlg.show()


def snap():
    try:
        dlg._next_frame()
        dlg.grab().save(r"C:\Users\shen_zhe\Desktop\autowork\tests\_tmp_media.png")
        print("OK  : media render, frame =", dlg._frame_count)
    except Exception as e:
        print("FAIL:", type(e).__name__, e)
    app.quit()


QTimer.singleShot(1500, snap)
QTimer.singleShot(6000, app.quit)
app.exec()
