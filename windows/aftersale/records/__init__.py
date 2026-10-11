# -*- coding: utf-8 -*-
"""记录与统计页拆分包（原 windows/aftersale/records.py 1465 行单体）

P1 试点：先包化，再按职责分层。当前阶段只做「包 + 模块归位」，行为零变化。

模块划分：
- page.py        : RecordsPage 视图与意图翻译（控件、布局、信号连线、表格渲染）
- controller.py  : 异步编排（查询/周期/指纹/写操作的 worker 生命周期与代际守卫）
- table_adapter.py: 行数据 → 单元格呈现（文本/排序键/徽章色/操作链接/提示）
- query_state.py : 无 Qt 的纯逻辑（筛选条件、分页文案、时间与提示串格式化）

对外入口保持 `from windows.aftersale.records import RecordsPage` 不变；
模块级工具函数 `_web_entry_url` / `_web_link_html` 也从这里再导出，
tools/smoke 与 tools/perf 的既有引用路径无需改动。
"""

from windows.aftersale.common import TABLE_COLUMNS  # noqa: F401
from windows.aftersale.records.page import (  # noqa: F401
    RecordsPage,
    _web_entry_url,
    _web_link_html,
)

__all__ = ["RecordsPage", "_web_entry_url", "_web_link_html", "TABLE_COLUMNS"]
