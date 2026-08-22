# -*- coding: utf-8 -*-
"""运维管理面板拆分包（原 windows/management_panel.py 4483 行单体拆出）

模块划分（按页面/职责，逻辑未改动）：
- common.py        : 常量/设置读写/后台 Worker/通用表格组件/调色板工具
- dialogs.py       : AddRecordDialog / EditSnkDialog / DeviceDirHealDialog /
                     DeviceFilesDialog / UploadListDialog
- table_page.py    : TablePage 球桌管理
- device_page.py   : FileListPanel + DevicePage 设备状态/上传/迁移
- settings_page.py : AdminSettingsPage 管理设置
- health_page.py   : TrendPage 健康趋势 + HealthPage 设备健康度
- moyu_page.py     : GamePage 小游戏
- window.py        : ManagementPanelWindow 主窗口（FluentWindow 组装）

windows/management_panel.py 保留为 re-export shim（``from
windows.management import *``），main_window/ui_mixin 等既有引用路径不变。
"""

from windows.management.common import *  # noqa: F401,F403
from windows.management.dialogs import (  # noqa: F401
    AddRecordDialog, EditSnkDialog, DeviceDirHealDialog,
    DeviceFilesDialog, UploadListDialog,
)
from windows.management.table_page import TablePage  # noqa: F401
from windows.management.device_page import FileListPanel, DevicePage  # noqa: F401
from windows.management.settings_page import AdminSettingsPage  # noqa: F401
from windows.management.health_page import TrendPage, HealthPage  # noqa: F401
from windows.management.moyu_page import GamePage  # noqa: F401
from windows.management.window import ManagementPanelWindow  # noqa: F401

__all__ = [
    # common（公开常量）
    "TABLE_COLUMNS", "DEVICE_COLUMNS", "FILE_FIELD_CATEGORIES",
    "CATEGORY_FILE_FIELDS", "MIGRATE_CATEGORIES", "FIELD_CATEGORY",
    "MIGRATE_DEST_OPTIONS",
    # dialogs
    "AddRecordDialog", "EditSnkDialog", "DeviceDirHealDialog",
    "DeviceFilesDialog", "UploadListDialog",
    # 页面
    "TablePage", "FileListPanel", "DevicePage", "AdminSettingsPage",
    "TrendPage", "HealthPage", "GamePage",
    # 主窗口
    "ManagementPanelWindow",
]
