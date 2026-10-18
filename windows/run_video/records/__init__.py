# -*- coding: utf-8 -*-
"""跑视频记录与统计页拆分包（原 records.py 1066 行单体）

模块划分（P1 起逐步归位，对外 import 路径保持 `windows.run_video.records` 不变）：
- page.py         : RecordsPage 视图与意图翻译
- controller.py   : 异步编排（worker 生命周期与代际守卫）
- table_adapter.py: 行数据 → 单元格呈现
- query_state.py  : 无 Qt 的纯逻辑（筛选、分页、时间文案）
- dialogs.py      : 编辑/新增弹窗与署名统计弹窗

本文件只做再导出，外部既有引用无需修改。
"""

from windows.run_video.common import TABLE_COLUMNS  # noqa: F401
from windows.run_video.records.dialogs import (  # noqa: F401
    EditLedgerDialog,
    SignerStatsDialog,
)
from windows.run_video.records.page import RecordsPage  # noqa: F401

__all__ = ["RecordsPage", "EditLedgerDialog", "SignerStatsDialog",
           "TABLE_COLUMNS"]
