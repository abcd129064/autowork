# -*- coding: utf-8 -*-
"""工作留痕面板包（design/worklog_hub_v1.html 的 1:1 落地）

模块划分：
- common.py   : 五页签共享词汇（分类/状态/成员配色 + 日期口径 + 页签外壳）
- hub.py      : WorkLogHub（PivotPage 容器，页头 + 五页签）
- dialogs.py  : EditWorklogDialog（新增/编辑，含连续录入）
- records/    : 页签1 记录量化（汇总卡 + 筛选 + 按日分组表）
- timeline/   : 页签2 时间轴（只读回看，点条目跳页签1 定位）
- mindmap/    : 页签3 思维导图（QGraphicsView 自研）
- stats/      : 页签4 统计汇总（QPainter 自绘堆叠柱 + 环图）
- members/    : 页签5 成员与分类（停用 ≠ 删除）

数据访问在 database/worklog_db.py，异步编排在 workers/worklog_worker.py。
"""
from windows.worklog.hub import WorkLogHub  # noqa: F401
from windows.worklog.dialogs import EditWorklogDialog  # noqa: F401

__all__ = ["WorkLogHub", "EditWorklogDialog"]
