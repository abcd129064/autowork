# -*- coding: utf-8 -*-
"""记录页纯逻辑 query_state 的单测（P2-2）。

query_state 的边界是「无 Qt」：本文件直接按路径加载源文件，不经过
windows.aftersale.records 包的 __init__，所以没有 qfluentwidgets 的
解释器也能跑。若以后有人往 query_state 里加 Qt/qfw import，这里会先红。

覆盖：短时间格式、筛选条件归一化、分页边界、页脚文案、列号→数据键、
行 tooltip 的完整字段与「否」兜底。
"""
import importlib.util
from pathlib import Path

_MODULE_PATH = (Path(__file__).resolve().parents[1]
                / "windows" / "aftersale" / "records" / "query_state.py")
_SPEC = importlib.util.spec_from_file_location(
    "aftersale_query_state_under_test", _MODULE_PATH)
qs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(qs)

import pytest  # noqa: E402


@pytest.mark.parametrize("raw, expected", [
    ("2026-08-22 21:14:33", "08-22 21:14"),
    ("2026-08-22 21:14", "08-22 21:14"),
    ("2026-08-22", "2026-08-22"),
    (" 2026-08-22 21:14:33 ", "08-22 21:14"),
    ("", ""),
    (None, ""),
])
def test_short_dt_keeps_existing_contract(raw, expected):
    assert qs.short_dt(raw) == expected


def test_build_filters_defaults_are_empty_strings_not_none():
    assert qs.build_filters(None, None, None, None, None, None) == {
        "cycle_start": "",
        "issue_type": "",
        "resolved": None,
        "is_initiative": None,
        "is_our_problem": None,
        "keyword": "",
    }


def test_build_filters_normalizes_all_types_and_strips_text():
    got = qs.build_filters(
        "2026-08", " 全部类型 ", True, False, True, "  贴球  ")
    assert got == {
        "cycle_start": "2026-08",
        "issue_type": "",
        "resolved": True,
        "is_initiative": False,
        "is_our_problem": True,
        "keyword": "贴球",
    }


def test_build_filters_keeps_real_type_and_strips_it():
    got = qs.build_filters("", " 问题 ", None, None, None, "")
    assert got["issue_type"] == "问题"
    assert got["cycle_start"] == ""
    assert got["keyword"] == ""


@pytest.mark.parametrize("total, size, expected", [
    (0, 60, 1),
    (1, 60, 1),
    (60, 60, 1),
    (61, 60, 2),
    (120, 60, 2),
    (121, 60, 3),
    (5, 0, 5),     # size 非法时按 1 处理
    (0, 0, 1),
    (None, None, 1),
])
def test_pages_of_edges(total, size, expected):
    assert qs.pages_of(total, size) == expected


@pytest.mark.parametrize("page_no, total, size, expected", [
    (1, 65, 60, "1/2 · 共 65 条"),
    (2, 65, 60, "2/2 · 共 65 条"),
    (1, 0, 60, "1/1 · 共 0 条"),
    (3, 5, 0, "3/5 · 共 5 条"),
])
def test_pager_text_matches_ui_copy(page_no, total, size, expected):
    assert qs.pager_text(page_no, total, size) == expected


_COLS = [("creator", "填写人"), ("issue_type", "类型"), ("problem", "问题")]


@pytest.mark.parametrize("col, expected", [
    (1, "creator"),
    (2, "issue_type"),
    (3, "problem"),
    (0, None),   # 勾选列
    (4, None),   # 越界
    (-1, None),
])
def test_col_key_maps_after_check_column(col, expected):
    assert qs.col_key(col, _COLS, check_col=0) == expected


def test_col_key_honours_custom_check_column():
    assert qs.col_key(2, _COLS, check_col=1) == "creator"
    assert qs.col_key(3, _COLS, check_col=1) == "issue_type"
    assert qs.col_key(4, _COLS, check_col=1) == "problem"
    assert qs.col_key(1, _COLS, check_col=1) is None


def test_row_tooltip_full_record_has_every_field_in_order():
    rec = {
        "created_at": "2026-08-22 21:14:33",
        "creator": "沈喆",
        "occurred_at": "2026-08-20 10:00",
        "issue_type": "问题",
        "room_name": "一号房",
        "region": "A区",
        "table_no": "3号桌",
        "problem": "贴球",
        "cause": "识别延迟",
        "resolved": "是",
        "is_initiative": "是",
        "is_our_problem": "否",
        "solution": "调参",
        "resolver": "朱华国",
        "response_time": "2小时",
    }
    assert qs.row_tooltip(rec) == (
        "填写时间: 2026-08-22 21:14:33\n"
        "填写人: 沈喆\n"
        "发生时间: 2026-08-20 10:00\n"
        "类型: 问题\n"
        "位置: 一号房 · A区 · 3号桌\n"
        "问题: 贴球\n"
        "发生原因: 识别延迟\n"
        "是否解决: 是\n"
        "是否我们主动发起: 是\n"
        "是否是我们的问题: 否\n"
        "解决方案: 调参\n"
        "解决人: 朱华国\n"
        "响应时间: 2小时"
    )


def test_row_tooltip_empty_record_falls_back_to_no():
    assert qs.row_tooltip({}) == (
        "填写时间: \n"
        "填写人: \n"
        "发生时间: \n"
        "类型: \n"
        "位置:  ·  · \n"
        "问题: \n"
        "发生原因: \n"
        "是否解决: 否\n"
        "是否我们主动发起: 否\n"
        "是否是我们的问题: 否\n"
        "解决方案: \n"
        "解决人: \n"
        "响应时间: "
    )


def test_row_tooltip_falsey_flags_use_no_not_python_repr():
    rec = {"resolved": 0, "is_initiative": "", "is_our_problem": None}
    text = qs.row_tooltip(rec)
    assert "是否解决: 否" in text
    assert "是否我们主动发起: 否" in text
    assert "是否是我们的问题: 否" in text
    assert "False" not in text
    assert "None" not in text
