# -*- coding: utf-8 -*-
"""MysqlSyncCard 入口判定逻辑（无 PySide6 依赖，可单测）

抽取的纯函数：根据表单收集的配置决定「立即同步」/「测试连接」是否可执行，
以及不可执行时向用户展示的提示。未启用 MySQL 时统一提示"当前数据库为本地 SQLite"，
避免在 MySQL 已关闭的情况下仍展示 MySQL 相关状态信息。
"""

# 统一提示：未启用时所有路径都展示这条
_LOCAL_SQLITE_HINT = "当前数据库为本地 SQLite，未启用 MySQL"


def should_attempt_sync(cfg: dict) -> tuple:
    """是否允许执行「立即同步」

    Returns:
        (can: bool, hint: str)  can=False 时 hint 为向用户展示的提示
    """
    if not cfg.get("enabled"):
        return False, f"{_LOCAL_SQLITE_HINT}，无需同步"
    return True, ""


def should_attempt_test(cfg: dict) -> tuple:
    """是否允许执行「测试连接」

    Returns:
        (can: bool, hint: str)  can=False 时 hint 为向用户展示的提示
    """
    if not cfg.get("enabled"):
        return False, f"{_LOCAL_SQLITE_HINT}，无需测试连接"
    if not cfg.get("password"):
        return False, "MySQL 密码未配置"
    return True, ""
