# -*- coding: utf-8 -*-
"""售后面板拆分包（原 windows/aftersale_panel.py 2275 行单体拆出）

模块划分（按页面/职责，逻辑未改动）：
- common.py   : 常量/QSS 模板/工具函数/通用小组件（FluentCombo/YesNoSegment/
                表格行内控件工厂）+ 表格列定义
- form.py     : AftersaleForm 共享表单（录入页与编辑弹窗复用）
- entry.py    : EntryPage 填写录入页
- dialogs.py  : EditRecordDialog / ImportPreviewDialog
- records.py  : RecordsPage 记录与统计页
- settings.py : CycleSettingsPage 周期设置 + SettingsPage 设置面板
- window.py   : AftersalePanelWindow 主窗口（FluentWindow 组装）

windows/aftersale_panel.py 保留为 re-export shim（``from
windows.aftersale import *``），main_window/ui_mixin 等既有引用路径不变。
"""

from windows.aftersale.common import (  # noqa: F401
    FluentCombo, YesNoSegment, TABLE_COLUMNS,
)
from windows.aftersale.form import AftersaleForm  # noqa: F401
from windows.aftersale.entry import EntryPage  # noqa: F401
from windows.aftersale.dialogs import (  # noqa: F401
    EditRecordDialog, ImportPreviewDialog,
)
from windows.aftersale.records import RecordsPage  # noqa: F401
from windows.aftersale.settings import (  # noqa: F401
    CycleSettingsPage, SettingsPage,
)
from windows.aftersale.window import AftersalePanelWindow  # noqa: F401

__all__ = [
    # common（公开常量/组件）
    "TABLE_COLUMNS", "FluentCombo", "YesNoSegment",
    # form
    "AftersaleForm",
    # entry
    "EntryPage",
    # dialogs
    "EditRecordDialog", "ImportPreviewDialog",
    # records
    "RecordsPage",
    # settings
    "CycleSettingsPage", "SettingsPage",
    # 主窗口
    "AftersalePanelWindow",
]
