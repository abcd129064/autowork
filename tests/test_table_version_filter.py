# -*- coding: utf-8 -*-
"""球桌版本号筛选的纯函数回归 —— 2026-11-18 补落盘

背景：`docs/API.md` 与 `docs/CHANGELOG.md` 记录版本号筛选时都引用了本文件，
但文件从未提交，`tools/check_refs.py --all` 因此长期报两处断链。这里按
`database/table_db.py` 的真实实现补回归，不改动查询层行为。

只测 `parse_version_filter` / `build_version_cond` 两个纯函数：不连库、
不建表、不碰 GUI，符合 `tests/` 的离线准入条件。SQL 片段按字符串断言，
锁定的是 MySQL / SQLite 双方言通用的那部分口径，不是实现细节的字面量。
"""

import pytest

import database.table_db as table_db


# ---------- parse_version_filter ----------

def test_empty_input_returns_none():
    """空串与纯空白都表示不筛选，调用方据此跳过整个条件分支。"""
    assert table_db.parse_version_filter("") is None
    assert table_db.parse_version_filter("   ") is None


def test_contains_mode_keeps_raw_text():
    """contains 保留原始串做 LIKE，不能转成整数，否则分段版本号搜不到。"""
    assert table_db.parse_version_filter("200072") == ("contains", ["200072"])
    assert table_db.parse_version_filter("200072-20063") == (
        "contains", ["200072-20063"])


def test_inline_operators_override_mode():
    """文本里的运算符优先于下拉，熟练用户不必先切口径；长运算符先匹配。"""
    cases = {
        "<200072": ("lt", [200072]),
        "<=200072": ("le", [200072]),
        ">200070": ("gt", [200070]),
        ">=200078": ("ge", [200078]),
        "!=200072": ("ne", [200072]),
        "<>200072": ("ne", [200072]),
        "=200072": ("eq", [200072]),
    }
    for text, expected in cases.items():
        assert table_db.parse_version_filter(text, "contains") == expected


def test_fullwidth_input_is_normalized():
    """中文输入法的全角符号与数字必须归一，否则整条条件会被判成非法。"""
    assert table_db.parse_version_filter("＜２０００７２") == ("lt", [200072])
    assert table_db.parse_version_filter("≤２０００７２", "contains") == (
        "le", [200072])


def test_between_accepts_three_separators():
    """区间分隔符不含 '-'：版本号自身用 '-' 分段，用它会产生歧义。"""
    for text in ("200070~200078", "200070..200078", "200070,200078",
                 "２０００７０～２０００７８"):
        assert table_db.parse_version_filter(text, "between") == (
            "between", [200070, 200078])


def test_between_sorts_bounds():
    """用户写反上下界时按升序归一，否则 BETWEEN 恒为空且没有任何提示。"""
    assert table_db.parse_version_filter("200078~200070", "between") == (
        "between", [200070, 200078])


def test_non_numeric_numeric_condition_raises():
    """数值口径只认纯数字；静默取前导数字会让人以为按完整版本比过了。"""
    with pytest.raises(ValueError):
        table_db.parse_version_filter("abc", "lt")
    with pytest.raises(ValueError):
        table_db.parse_version_filter("200072-20063", "gt")


def test_overflow_raises_value_error_not_overflow():
    """超 8 字节整数在 SQLite 绑定时是 OverflowError，会绕过非法输入兜底。"""
    with pytest.raises(ValueError):
        table_db.parse_version_filter("9" * 30, "lt")


def test_between_missing_bound_raises():
    """区间只给一个值不能退化成单值比较，那会静默改变用户意图。"""
    with pytest.raises(ValueError):
        table_db.parse_version_filter("200070", "between")
    with pytest.raises(ValueError):
        table_db.parse_version_filter("200070~200072~200078", "between")


def test_unknown_mode_falls_back_to_contains():
    """未知口径按子串处理而不是抛错，避免旧配置里的值把查询打挂。"""
    assert table_db.parse_version_filter("200072", "bogus") == (
        "contains", ["200072"])


# ---------- build_version_cond ----------

def test_build_empty_returns_none():
    assert table_db.build_version_cond("") is None
    assert table_db.build_version_cond("", "lt", True) is None


def test_build_contains_uses_like_with_ifnull():
    """NULL 版本号参与 LIKE 得 NULL，不加 IFNULL 时反选会连 NULL 行一起丢。"""
    cond, params = table_db.build_version_cond("200072")
    assert cond == "IFNULL(deviceVersion, '') LIKE ?"
    assert params == ["%200072%"]


def test_build_numeric_uses_cast_and_firmware_guard():
    """CAST DECIMAL 取首段主版本；>0 与 '%-%' 前提挡住人工 placeholder。"""
    cond, params = table_db.build_version_cond("200072", "lt")
    assert "CAST(IFNULL(deviceVersion, '') AS DECIMAL) < ?" in cond
    assert "IFNULL(deviceVersion, '') LIKE ?" in cond
    assert "> 0" in cond
    assert params == ["%-%", 200072]


def test_build_invert_wraps_whole_condition():
    """反选取反整体（含有固件版本前提），保证正反向不重不漏。"""
    cond, params = table_db.build_version_cond("200072", "lt", invert=True)
    assert cond.startswith("NOT (") and cond.endswith(")")
    assert params == ["%-%", 200072]
    plain, _ = table_db.build_version_cond("200072", "lt")
    assert cond == "NOT (%s)" % plain


def test_build_ne_is_eq_with_inverted_flag():
    """'!=' 等价「等于 + 反选」；再叠工具栏反选时按异或抵消回正向。"""
    cond_ne, params_ne = table_db.build_version_cond("!=200072")
    cond_eq, params_eq = table_db.build_version_cond("200072", "eq",
                                                     invert=True)
    assert cond_ne == cond_eq and params_ne == params_eq
    cond_xor, _ = table_db.build_version_cond("!=200072", invert=True)
    assert not cond_xor.startswith("NOT (")
    assert "= ?" in cond_xor
