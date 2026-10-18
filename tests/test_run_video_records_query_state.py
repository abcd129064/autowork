# -*- coding: utf-8 -*-
"""跑视频记录页 query_state（纯逻辑）单测：筛选归一 / 日期区间 / 分页文案

为什么按文件路径加载而不是 import 包：windows/run_video/records/__init__.py
会再导出 page.py，连带 import PySide6 + qfluentwidgets；基线解释器
E:\\ANACONDA 缺 qfluentwidgets，走包导入会让「零依赖模块」的测试因为无关
依赖收集失败。按路径加载既能测到同一个源文件，也顺带证明本模块自足。
"""
import ast
import importlib.util
import os
from datetime import date

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_PATH = os.path.join(ROOT, "windows", "run_video", "records",
                           "query_state.py")


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "run_video_query_state_under_test", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_qs = _load_module()
short_dt = _qs.short_dt
date_range = _qs.date_range
current_filters = _qs.current_filters
pages_of = _qs.pages_of
clamp_page = _qs.clamp_page
page_text = _qs.page_text
total_text = _qs.total_text

EMPTY_FILTERS = {
    "category": "", "kind": "", "signer": "", "keyword": "",
    "date_from": "", "date_to": "", "repro": "",
}


# ---------- 时间文案 ----------

def test_short_dt_cuts_full_timestamp():
    assert short_dt("2026-08-22 21:14:33") == "08-22 21:14"
    assert short_dt("2026-01-02 03:04:05") == "01-02 03:04"


def test_short_dt_keeps_short_values():
    # 恰好 16 位是截断阈值：不短于它就走 [5:16]
    assert short_dt("2026-08-22 21:14") == "08-22 21:14"
    # 只有日期 / 空值 / None / 带空白：原样（去空白）返回，不能切成半个
    assert short_dt("2026-08-22") == "2026-08-22"
    assert short_dt("") == ""
    assert short_dt(None) == ""
    assert short_dt("  2026-08-22 21:14:33  ") == "08-22 21:14"


# ---------- 日期区间 ----------

@pytest.mark.parametrize("mode,expected", [
    ("全部", ("", "")),
    ("今天", ("2026-03-15", "2026-03-15")),
    ("近7天", ("2026-03-09", "2026-03-15")),
    ("近30天", ("2026-02-14", "2026-03-15")),
])
def test_date_range_quick_modes(mode, expected):
    assert date_range(mode, date(2026, 3, 15)) == expected


def test_date_range_month_handles_february_and_leap_year():
    # 2026 非闰年：2 月 28 天；2024 闰年：2 月 29 天（月末计算不能越界）
    assert date_range("本月", date(2026, 2, 14)) == ("2026-02-01", "2026-02-28")
    assert date_range("本月", date(2024, 2, 14)) == ("2024-02-01", "2024-02-29")
    # 大月最后一天作基准：replace(day=31) 合法，不依赖 base.day
    assert date_range("本月", date(2026, 12, 31)) == ("2026-12-01", "2026-12-31")


def test_date_range_custom_swaps_inverted_range():
    # 起止倒置必须交换，否则 SQL 恒空、用户只看到「没有记录」
    assert date_range("自定义", date(2026, 3, 10), date(2026, 3, 1)) == (
        "2026-03-01", "2026-03-10")
    # 同一天倒置与否都是单日
    assert date_range("自定义", date(2026, 3, 5), date(2026, 3, 5)) == (
        "2026-03-05", "2026-03-05")


def test_date_range_custom_defaults_end_to_base():
    assert date_range("自定义", date(2026, 3, 10)) == ("2026-03-10",
                                                     "2026-03-10")


# ---------- 筛选归一 ----------

def test_current_filters_normalizes_all_labels():
    # 占位词含首尾空格也要归零：先 strip 再比，否则「全部分类 」会当真实值
    assert current_filters("全部分类", "全部类别", "全部署名",
                           "", "", "", "") == EMPTY_FILTERS
    assert current_filters(" 全部分类 ", " 全部类别 ", " 全部署名 ",
                           "  ", " ", " ", " ") == EMPTY_FILTERS


def test_current_filters_strips_and_keeps_real_values():
    f = current_filters(" 问题 ", " 精度 ", " 张三 ", " 卡顿 ",
                        "2026-03-01", " 2026-03-15 ", " 是 ")
    assert f == {
        "category": "问题", "kind": "精度", "signer": "张三",
        "keyword": "卡顿", "date_from": "2026-03-01",
        "date_to": "2026-03-15", "repro": "是",
    }
    assert sorted(f) == sorted(EMPTY_FILTERS)


# ---------- 分页 ----------

@pytest.mark.parametrize("total,page_size,expected", [
    (0, 50, 1),     # 空结果也要有一页
    (1, 50, 1),
    (50, 50, 1),    # 整除不多算一页
    (51, 50, 2),    # 余数要进位
    (65, 50, 2),
    (100, 50, 2),
    (0, 0, 1),      # page_size 非法时不抛 ZeroDivisionError
])
def test_pages_of_at_least_one(total, page_size, expected):
    assert pages_of(total, page_size) == expected


@pytest.mark.parametrize("page_no,expected", [
    (1, 1), (2, 2), (3, 2), (0, 1), (-5, 1),
])
def test_clamp_page_within_bounds(page_no, expected):
    # total=65 / 50 → 共 2 页，越界页码夹到 [1, 2]
    assert clamp_page(page_no, 65, 50) == expected
    # 空结果仍停在第 1 页（页码 7 不能显示成 7/1）
    assert clamp_page(page_no, 0, 50) == 1


def test_page_footer_texts():
    assert page_text(1, 65, 50) == "1/2"
    assert page_text(9, 65, 50) == "2/2"    # 越界页码先夹取，不显示 9/2
    assert page_text(1, 0, 50) == "1/1"
    assert total_text(65) == "共 65 条"
    assert total_text(0) == "共 0 条"


# ---------- 依赖边界 ----------

def test_module_stays_qt_database_worker_free():
    """拆分纪律：本模块只允许标准库，页面对它单向依赖。"""
    with open(MODULE_PATH, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"PySide6", "qfluentwidgets", "database", "workers",
                 "core", "windows"}
    assert imported & forbidden == set(), imported & forbidden
    assert imported <= {"calendar", "datetime"}, imported
