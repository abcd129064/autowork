# -*- coding: utf-8 -*-
"""探测：待新增 qfluentwidgets 组件的 __init__ 签名（避免写入后编译/构造失败）"""
import sys
import inspect
from PySide6.QtWidgets import QApplication
from qfluentwidgets import (
    ImageLabel, IconInfoBadge, TableView, SingleDirectionScrollArea,
    SegmentedToolWidget, SegmentedToggleToolWidget, CommandBar, CommandBarView,
    CheckableMenu, SystemTrayMenu, RoundMenu, TeachingTip, PopupTeachingTip,
    Flyout, ToolTipFilter, Dialog, ColorDialog, MessageBoxBase, FlipView,
    FlipImageDelegate, AdaptiveFlowLayout, FlowLayout, NavigationInterface,
    Action, FluentIcon, isDarkTheme, qconfig,
)

app = QApplication(sys.argv)

CLASSES = [
    "ImageLabel", "IconInfoBadge", "TableView", "SingleDirectionScrollArea",
    "SegmentedToolWidget", "SegmentedToggleToolWidget", "CommandBar",
    "CommandBarView", "CheckableMenu", "SystemTrayMenu", "RoundMenu",
    "TeachingTip", "PopupTeachingTip", "Flyout", "ToolTipFilter", "Dialog",
    "ColorDialog", "MessageBoxBase", "FlipView", "FlipImageDelegate",
    "AdaptiveFlowLayout", "FlowLayout", "NavigationInterface",
]

for name in CLASSES:
    cls = globals()[name]
    try:
        sig = inspect.signature(cls.__init__)
    except Exception as e:
        sig = f"<no sig: {e}>"
    print(f"\n### {name}")
    print("  sig:", sig)
