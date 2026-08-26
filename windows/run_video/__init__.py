# -*- coding: utf-8 -*-
"""跑视频面板包

模块划分（参照售后面板 windows/aftersale/ 拆包结构）：
- common.py   : 表格列定义 + 分类徽章 + 复用售后包的 Fluent UI 工厂
- form.py     : LedgerForm 共享表单（录入页与编辑弹窗复用，字段与
                在线模板.xlsx 数据 sheet 对齐）
- entry.py    : EntryPage 填写录入页（必填进度 + 提交 + 会话预填）
- records.py  : RecordsPage 记录与统计页（指标卡/筛选/分页/编辑/删除/
                署名统计/导出）
- settings.py : SignerSettingsCard 默认署名 + SettingsPage 设置面板
- window.py   : LedgerPanelWindow 主窗口（FluentWindow 组装）

windows/ledger_panel.py 为 re-export shim（独立进程入口），
main_window 经它引用 LedgerPanelWindow。
"""

from windows.run_video.common import (  # noqa: F401
    TABLE_COLUMNS, CATEGORY_ACCENTS,
)
from windows.run_video.form import LedgerForm  # noqa: F401
from windows.run_video.entry import EntryPage  # noqa: F401
from windows.run_video.records import (  # noqa: F401
    RecordsPage, EditLedgerDialog, SignerStatsDialog,
)
from windows.run_video.settings import (  # noqa: F401
    SignerSettingsCard, SettingsPage,
)
from windows.run_video.window import LedgerPanelWindow  # noqa: F401

__all__ = [
    # common
    "TABLE_COLUMNS", "CATEGORY_ACCENTS",
    # form
    "LedgerForm",
    # entry
    "EntryPage",
    # records
    "RecordsPage", "EditLedgerDialog", "SignerStatsDialog",
    # settings
    "SignerSettingsCard", "SettingsPage",
    # 主窗口
    "LedgerPanelWindow",
]
