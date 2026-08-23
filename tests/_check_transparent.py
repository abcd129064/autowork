# -*- coding: utf-8 -*-
"""工具栏滚动容器透明背景断言（消除灰色底色条）"""
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv)
from windows.aftersale_panel import RecordsPage
from core.flow_widgets import FlowToolbarScrollArea

page = RecordsPage()
page.resize(1000, 700)
page.show()
app.processEvents()
target = None
for i in range(page.layout().count()):
    it = page.layout().itemAt(i)
    w = it.widget() if it else None
    if isinstance(w, FlowToolbarScrollArea):
        target = w
assert target is not None
assert "transparent" in target.styleSheet(), target.styleSheet()
assert "transparent" in target.viewport().styleSheet()
assert target.frameShape().value == 0  # NoFrame
print("TRANSPARENT_OK")
