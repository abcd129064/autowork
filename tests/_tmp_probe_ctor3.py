# -*- coding: utf-8 -*-
"""补充确认：FlipView 方法 / TeachingTip & Flyout 视图类 / ColorDialog 方法"""
import sys
import inspect

from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import (
    FlipView, FlipImageDelegate, TeachingTip, PopupTeachingTip, Flyout,
    FlyoutViewBase, ColorDialog, TeachingTipView, FlyoutView,
    FluentIcon, qconfig,
)

app = QApplication(sys.argv)

print("=== 视图类存在性 ===")
for n in ("TeachingTipView", "FlyoutView"):
    print(f"  {n}: exists in qfluentwidgets top?", n in globals())

for cls in (TeachingTipView, FlyoutView):
    try:
        print(f"\n  {cls.__name__} sig:", inspect.signature(cls.__init__))
    except Exception as e:
        print(f"  {cls.__name__} sig FAIL:", e)

print("\n=== FlipView 方法 ===")
fv = FlipView()
for m in ("addImages", "addImage", "setImages", "addWidget", "setPageNumber",
          "setCurrentIndex", "setMaximumHeight"):
    print(f"  {m}: {'YES' if hasattr(fv, m) else 'NO'}")
print("  FlipView sig:", inspect.signature(FlipView.__init__))

print("\n=== ColorDialog 方法 ===")
for m in ("getColor", "exec", "color", "setColor", "qconfig"):
    print(f"  {m}: {'YES' if hasattr(ColorDialog, m) else 'NO'}")
print("  getColor sig:", inspect.signature(ColorDialog.getColor) if hasattr(ColorDialog, "getColor") else "n/a")

print("\n=== Flyout 静态/类方法 ===")
for m in ("make", "show"):
    print(f"  {m}: {'YES' if hasattr(Flyout, m) else 'NO'}")
if hasattr(Flyout, "make"):
    print("  make sig:", inspect.signature(Flyout.make))

print("\n=== FlyoutViewBase 子类构造尝试 ===")
try:
    v = TeachingTipView(icon=FluentIcon.INFO, title="标题", content="内容", isClosable=False)
    print("  TeachingTipView(...) OK")
except Exception as e:
    print("  TeachingTipView FAIL:", type(e).__name__, e)
try:
    v = FlyoutView(icon=FluentIcon.INFO, title="标题", content="内容", isClosable=False)
    print("  FlyoutView(...) OK")
except Exception as e:
    print("  FlyoutView FAIL:", type(e).__name__, e)
