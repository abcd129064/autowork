# -*- coding: utf-8 -*-
"""聚焦验证：新增控件的关键方法（setImage/addAction/addImages/checkable/ColorDialog）"""
import sys
import os
import tempfile

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QPixmap, QColor, QImage
from qfluentwidgets import (
    ImageLabel, IconInfoBadge, TableView, SingleDirectionScrollArea,
    SegmentedToolWidget, SegmentedToggleToolWidget, CommandBar, CommandBarView,
    CheckableMenu, SystemTrayMenu, RoundMenu, Action, FluentIcon,
    FlipView, FlipImageDelegate, Dialog, ColorDialog, TeachingTipView,
    TeachingTip, PopupTeachingTip, Flyout, FlyoutView, ToolTipFilter,
    AdaptiveFlowLayout, NavigationInterface,
)

app = QApplication(sys.argv)

def t(label, fn):
    try:
        fn()
        print("OK  :", label)
    except Exception as e:
        print("FAIL:", label, "->", type(e).__name__, e)

# ImageLabel
il = ImageLabel()
for m in ("setImage", "setFixedWidth", "scaledToWidth"):
    print(f"  ImageLabel.{m}: {'YES' if hasattr(il, m) else 'NO'}")
t("ImageLabel setImage(pixmap)", lambda: il.setImage(QPixmap(100, 60)))
t("ImageLabel setImage(color,size)", lambda: il.setImage(QColor("#009faa"), QPixmap(20,20)))

# IconInfoBadge 构造
t("IconInfoBadge", lambda: IconInfoBadge())
t("TableView", lambda: TableView())
t("SingleDirectionScrollArea", lambda: SingleDirectionScrollArea())
t("SegmentedToolWidget", lambda: SegmentedToolWidget())
t("SegmentedToggleToolWidget", lambda: SegmentedToggleToolWidget())

# CommandBar / CommandBarView
cb = CommandBar()
print("  CommandBar methods:", [m for m in ("addAction","addActions","addWidget","insertAction") if hasattr(cb, m)])
a = Action(FluentIcon.COPY, "复制")
t("CommandBar.addAction", lambda: cb.addAction(a))
cbv = CommandBarView()
print("  CommandBarView methods:", [m for m in ("addAction","insertAction") if hasattr(cbv, m)])
# 优先 addAction；若无则用 append()
t("CommandBarView.addAction", lambda: cbv.addAction(Action(FluentIcon.CUT, "剪切")))

# CheckableMenu / SystemTrayMenu / RoundMenu
cm = CheckableMenu()
a2 = Action(FluentIcon.COPY, "可勾选项")
a2.setCheckable(True)
t("CheckableMenu.addAction(checkable)", lambda: cm.addAction(a2))
t("SystemTrayMenu", lambda: SystemTrayMenu())
t("SystemTrayMenu.addAction", lambda: SystemTrayMenu().addAction(Action(FluentIcon.HOME, "托盘项")))

# FlipView 需要临时图片
tmp_dir = tempfile.mkdtemp(prefix="qf_flip_")
paths = []
for i, c in enumerate(["#e63946", "#457b9d", "#2a9d8f"]):
    p = os.path.join(tmp_dir, f"f{i}.png")
    pm = QPixmap(120, 80)
    pm.fill(QColor(c))
    pm.save(p)
    paths.append(p)
print("  temp images:", paths)
fv = FlipView()
print("  FlipView.addImages sig:", end=" ")
import inspect
try:
    print(inspect.signature(fv.addImages))
except Exception as e:
    print(e)
t("FlipView.addImages(paths)", lambda: fv.addImages(paths))
t("FlipView.setCurrentIndex", lambda: fv.setCurrentIndex(1))
t("FlipImageDelegate construct", lambda: FlipImageDelegate())

# Dialog / ColorDialog
t("Dialog construct", lambda: Dialog("标题", "内容"))
t("ColorDialog construct", lambda: ColorDialog(QColor("#009faa"), "选择颜色"))

# TeachingTip / PopupTeachingTip / Flyout
w = QWidget()
view = TeachingTipView(title="标题", content="内容", icon=FluentIcon.INFO, isClosable=False)
t("TeachingTip construct", lambda: TeachingTip(view, w, duration=1500))
t("PopupTeachingTip construct", lambda: PopupTeachingTip(view, w, duration=1500))
t("Flyout.make", lambda: Flyout.make(FlyoutView(title="标题", content="内容", icon=FluentIcon.INFO), w, parent=w))

# ToolTipFilter / AdaptiveFlowLayout / NavigationInterface
t("ToolTipFilter attach", lambda: ToolTipFilter(w, showDelay=300))
t("AdaptiveFlowLayout", lambda: AdaptiveFlowLayout())
t("NavigationInterface", lambda: NavigationInterface())

print("\nFlush done.")
