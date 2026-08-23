# -*- coding: utf-8 -*-
"""验证 带父窗口时 Dialog / ColorDialog / MessageBoxBase 子类 构造正常"""
import sys

sys.path.insert(0, r"C:\Users\shen_zhe\Desktop\autowork")

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QColor
from qfluentwidgets import Dialog, ColorDialog, MessageBoxBase
from windows.management.widget_page import _DemoMessageBox

app = QApplication(sys.argv)
w = QWidget()
w.resize(500, 400)
w.show()

for name, fn in (
    ("Dialog(parent)", lambda: Dialog("标题", "内容", w)),
    ("ColorDialog(parent)", lambda: ColorDialog(QColor("#009faa"), "选色", w)),
    ("_DemoMessageBox(parent)", lambda: _DemoMessageBox(w)),
    ("MessageBoxBase(parent)", lambda: MessageBoxBase(w)),
):
    try:
        fn()
        print("OK  :", name)
    except Exception as e:
        print("FAIL:", name, "->", type(e).__name__, e)
