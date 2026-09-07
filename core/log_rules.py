# -*- coding: utf-8 -*-
"""日志高亮规则：默认值与编译函数。

自 main_window/settings_dialog.py 迁移（2026-09-07 项目清理：设置对话框
UI 已废止，本模块承载仅存的规则常量与编译函数）。
供 main_window（日志渲染）、ui_mixin（设置保存后即时刷新）与
hub_pages（日志高亮规则编辑对话框）共用，避免多处重复实现导致行为分叉。
"""

import re

# 日志高亮规则默认值（设置与渲染同一来源）
# 注意：早期版本的高亮关键词（'返回'/'add'）写死在前端代码中，
# 规则系统化时迁移为默认规则（颜色沿用旧高亮色 [220,80,20]），不可删除，否则旧用户日志高亮会丢失
DEFAULT_LOG_RULES = [
    {"name": "错误", "pattern": r"ERROR|Exception|Traceback|error",
     "color": "#ff5252", "notify": True},
    {"name": "警告", "pattern": r"WARN|WARNING|超时|失败|timeout",
     "color": "#f0a020", "notify": False},
    {"name": "返回", "pattern": r"返回",
     "color": "#dc5014", "notify": False},
    {"name": "加分", "pattern": r"加分",
     "color": "#dc5014", "notify": False},
    {"name": "add", "pattern": r"add",
     "color": "#dc5014", "notify": False},
]


def compile_log_rules(raw_rules):
    """把规则配置编译为可匹配对象列表（非法正则可跳过）"""
    rules = []
    for r in raw_rules or []:
        try:
            rules.append({
                "name": str(r.get("name", "") or ""),
                "regex": re.compile(r.get("pattern", "") or ""),
                "color": str(r.get("color", "#ff5252") or "#ff5252"),
                "notify": bool(r.get("notify", False)),
            })
        except re.error:
            continue
    return rules
