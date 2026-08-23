# -*- coding: utf-8 -*-
"""验证 ColorDialog 带真实父窗口时的构造"""
import sys
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QColor
from qfluentwidgets import ColorDialog

app = QApplication(sys.argv)
w = QWidget()
w.resize(400, 300)
w.show()

for label, parent in (("无 parent", None), ("无 parent None", None), ("非None parent", w)):
    try:
        d = ColorDialog(QColor("#009faa"), "选择颜色")
        print("OK  : ColorDialog (no-arg parent) construct", d is not None)
        break
    except Exception as e:
        print("FAIL: no-parent ->", type(e).__name__, e)
        break

try:
    d = ColorDialog(QColor("#009faa"), "选择颜色", parent=w)
    print("OK  : ColorDialog(parent=w) construct")
except Exception as e:
    print("FAIL: parent=w ->", type(e).__name__, e)
