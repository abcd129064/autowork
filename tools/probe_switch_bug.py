# -*- coding: utf-8 -*-
"""复现两个界面问题（offscreen）：
  A. 顶级导航/二级 Pivot 切换是否响应（模拟点击）
  B. 二级切换控件（Pivot）实际宽度是否铺满整页
用法：QT_QPA_PLATFORM=offscreen <venv>/python.exe tools/probe_switch_bug.py
"""
import os
import sys

_parts = [p for p in os.environ.get("PATH", "").split(os.pathsep)
          if "conda" not in p.lower() and "Library\\bin" not in p]
os.environ["PATH"] = os.pathsep.join(_parts)
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from main_window import MainWindow  # noqa: E402

w = MainWindow()
w.show()
for _ in range(8):
    app.processEvents()

print("== A. 顶级导航切换 ==")
stack = w.stackedWidget
print("stack count:", stack.count())
for name in ("managementHub", "aftersaleHub", "ledgerHub", "homeInterface",
             "remoteHub", "statsHub", "settingsHub", "aboutPage"):
    pg = w.findChild(type(stack), name)
# findChild 按类型搜不到名字，改用 stackedWidget 遍历
pages = {stack.widget(i).objectName(): stack.widget(i)
         for i in range(stack.count())}
print("pages:", list(pages))

# 模拟导航项点击：navigationInterface 的每个 item 是 widget，直接调其 click
nav = w.navigationInterface
try:
    nav.setCurrentItem("managementHub")
    for _ in range(5):
        app.processEvents()
    print("after setCurrentItem(managementHub): current =",
          stack.currentWidget().objectName())
    nav.setCurrentItem("aftersaleHub")
    for _ in range(5):
        app.processEvents()
    print("after setCurrentItem(aftersaleHub): current =",
          stack.currentWidget().objectName())
except Exception as e:
    print("nav switch EXC:", repr(e))

print("\n== B. 二级 Pivot 切换 ==")
mg = w.management_hub
print("pivot class:", type(mg.pivot).__name__)
print("pivot sizeHint:", mg.pivot.sizeHint().width(),
      "actual:", mg.pivot.width(), " hub width:", mg.width())
for key in ("devicePage", "healthPage", "tablePage"):
    try:
        item = mg.pivot.items.get(key)
        if item is not None:
            item.click()
        else:
            mg.pivot.setCurrentItem(key)
        for _ in range(5):
            app.processEvents()
        print(f"after click {key}: stack current =",
              mg.stack.currentWidget().objectName(),
              " pivot current =", mg.pivot.currentRouteKey())
    except Exception as e:
        print(f"pivot switch {key} EXC:", repr(e))

af = w.aftersale_hub
print("aftersale pivot sizeHint:", af.pivot.sizeHint().width(),
      "actual:", af.pivot.width())

print("\n== C. 菜单栏位置 ==")
print("menubar parent:", w._menubar_widget.parent() if hasattr(w, "_menubar_widget") else "N/A")
print("home layout count:", w.home_vbox.count())

sys.stdout.flush()
os._exit(0)
