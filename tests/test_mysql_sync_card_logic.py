# -*- coding: utf-8 -*-
"""MysqlSyncCard 入口判定逻辑单测

验证 should_attempt_sync / should_attempt_test 在不同 enabled/password
组合下正确返回 (can, hint)，确保 MySQL 未启用时统一显示本地 SQLite 提示。
"""

import database.mysql_sync_card_logic as logic


# ==================== should_attempt_sync ====================

def test_sync_disabled_shows_local_sqlite():
    can, hint = logic.should_attempt_sync({"enabled": False})
    assert can is False
    assert "本地 SQLite" in hint
    assert "未启用" in hint


def test_sync_enabled_allows():
    can, hint = logic.should_attempt_sync(
        {"enabled": True, "host": "x", "password": "p"})
    assert can is True and hint == ""


def test_sync_missing_enabled_key_treated_as_disabled():
    # cfg.get("enabled") 默认 False 行为
    can, hint = logic.should_attempt_sync({})
    assert can is False
    assert "本地 SQLite" in hint


# ==================== should_attempt_test ====================

def test_test_disabled_shows_local_sqlite():
    can, hint = logic.should_attempt_test({"enabled": False, "password": "p"})
    assert can is False
    assert "本地 SQLite" in hint
    assert "未启用" in hint


def test_test_enabled_no_password_warns():
    can, hint = logic.should_attempt_test(
        {"enabled": True, "host": "x", "password": ""})
    assert can is False
    assert "密码" in hint


def test_test_enabled_with_password_allows():
    can, hint = logic.should_attempt_test(
        {"enabled": True, "host": "x", "password": "secret"})
    assert can is True and hint == ""


def test_disabled_takes_priority_over_password_check():
    # 未启用优先于密码检查：用户关掉启用就不该提示密码问题
    can, hint = logic.should_attempt_test({"enabled": False, "password": ""})
    assert can is False
    assert "本地 SQLite" in hint
