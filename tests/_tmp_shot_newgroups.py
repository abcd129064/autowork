# -*- coding: utf-8 -*-
"""截图：滚动控件墙到底部，确认新增分组（菜单/提示/对话框/翻页布局/多媒体）→ 上半部与下半部截图"""
import sys

sys.path.insert(0, r"C:\Users\shen_zhe\Desktop\autowork")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QScrollArea

from windows.management.widget_page import TestPage

app = QApplication(sys.argv)


def do():
    page = TestPage()
    page.resize(1280, 900)
    page.show()
    page.tabs.setCurrentIndex(1)
    areas = page.widget_tab.findChildren(QScrollArea)

    def sc1():
        for a in areas:
            v = a.verticalScrollBar()
            v.setValue(v.maximum())
        QTimer.singleShot(300, lambda: (
            page.grab().save(r"C:\Users\shen_zhe\Desktop\autowork\tests\_tmp_newgroups_bottom.png"),
            print("OK  : bottom grab"), app.quit()))

    QTimer.singleShot(300, sc1)


QTimer.singleShot(200, do)
QTimer.singleShot(7000, app.quit)
app.exec()
