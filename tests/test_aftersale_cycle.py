# -*- coding: utf-8 -*-
"""database/aftersale_db.py 周期归属逻辑单元测试

周期计算是售后统计的口径基石（列表/统计/周期下拉/导出四处共用同一规则），
且当前 settings.json 配置 type=tue / start=2026-08-18 / span=3 与默认
span=7 不一致，最易隐藏偏差。通过 monkeypatch 注入固定周期模式做确定性
验证。不依赖 PySide6 / 数据库环境，也不会触碰真实 tables.db
（周期函数为纯逻辑，不调用 _get_conn）。

测试日期星期（已校验）：
  2026-08-15 Sat / 08-17 Mon / 08-18 Tue / 08-19 Wed / 08-20 Thu
  08-21 Fri / 08-23 Sun / 08-24 Mon

运行：py -m pytest tests/test_aftersale_cycle.py -v
"""

from datetime import datetime

import pytest

import database.aftersale_db as adb

TUE = {"type": "tue", "start": "", "span": 7}
MON = {"type": "mon", "start": "", "span": 7}
CUSTOM = {"type": "custom", "start": "2026-08-18", "span": 3}


def _d(s):
    return datetime.strptime(s, "%Y-%m-%d")


@pytest.fixture
def set_cycle(monkeypatch):
    """注入固定周期模式，隔离 settings.json 读取"""
    def _apply(mode):
        monkeypatch.setattr(adb, "load_cycle_mode", lambda: dict(mode))
    return _apply


# ==================== cycle_start_of (tue：周二起周一终) ====================

def test_cycle_start_tue(set_cycle):
    set_cycle(TUE)
    assert adb.cycle_start_of(_d("2026-08-18")) == "2026/08/18"  # 周二：起点自身
    assert adb.cycle_start_of(_d("2026-08-19")) == "2026/08/18"  # 周三：同周期
    assert adb.cycle_start_of(_d("2026-08-23")) == "2026/08/18"  # 周日：同周期
    assert adb.cycle_start_of(_d("2026-08-24")) == "2026/08/18"  # 周一：上周期末日
    assert adb.cycle_start_of(_d("2026-08-17")) == "2026/08/11"  # 周一：更早周期


# ==================== cycle_start_of (mon：自然周) ====================

def test_cycle_start_mon(set_cycle):
    set_cycle(MON)
    assert adb.cycle_start_of(_d("2026-08-17")) == "2026/08/17"  # 周一：起点
    assert adb.cycle_start_of(_d("2026-08-23")) == "2026/08/17"  # 周日：同周
    assert adb.cycle_start_of(_d("2026-08-24")) == "2026/08/24"  # 下周一：新周期


# ==================== cycle_start_of (custom：起始日+天数，向过去对齐) ====================

def test_cycle_start_custom(set_cycle):
    set_cycle(CUSTOM)
    assert adb.cycle_start_of(_d("2026-08-18")) == "2026/08/18"  # diff 0 → 18
    assert adb.cycle_start_of(_d("2026-08-20")) == "2026/08/18"  # diff 2 → 同周期
    assert adb.cycle_start_of(_d("2026-08-21")) == "2026/08/21"  # diff 3 → 新周期
    assert adb.cycle_start_of(_d("2026-08-23")) == "2026/08/21"  # diff 5 → 21
    assert adb.cycle_start_of(_d("2026-08-24")) == "2026/08/24"  # diff 6 → 24
    assert adb.cycle_start_of(_d("2026-08-15")) == "2026/08/15"  # diff -3 → 历史对齐


# ==================== cycle_span_days ====================

def test_cycle_span_days_fixed_modes(set_cycle):
    set_cycle(TUE)
    assert adb.cycle_span_days() == 7
    set_cycle(MON)
    assert adb.cycle_span_days() == 7


def test_cycle_span_days_custom(set_cycle):
    set_cycle(CUSTOM)
    assert adb.cycle_span_days() == 3


def test_cycle_span_days_custom_invalid_falls_back(set_cycle):
    set_cycle({"type": "custom", "start": "2026-08-18", "span": "abc"})
    assert adb.cycle_span_days() == 7  # 非法 span 回退 7
    set_cycle({"type": "custom", "start": "2026-08-18", "span": None})
    assert adb.cycle_span_days() == 7  # None 回退 7


# ==================== cycle_label ====================

def test_cycle_label_span7(set_cycle):
    set_cycle(TUE)
    assert adb.cycle_label("2026/08/18") == "08/18 - 08/24"  # 18 + 6 = 24


def test_cycle_label_span3(set_cycle):
    set_cycle(CUSTOM)
    assert adb.cycle_label("2026/08/18") == "08/18 - 08/20"  # 18 + 2 = 20


def test_cycle_label_invalid_passthrough(set_cycle):
    set_cycle(TUE)
    assert adb.cycle_label("garbage") == "garbage"
    assert adb.cycle_label("") == ""


# ==================== _record_cycle ====================

def test_record_cycle_prefers_occurred(set_cycle):
    set_cycle(TUE)
    assert adb._record_cycle("2026-08-19", "2026-08-20") == "2026/08/18"


def test_record_cycle_falls_back_to_created(set_cycle):
    set_cycle(TUE)
    assert adb._record_cycle("", "2026-08-20") == "2026/08/18"


def test_record_cycle_occurred_with_time_truncated(set_cycle):
    set_cycle(TUE)
    assert adb._record_cycle("2026-08-19 10:30:00", "") == "2026/08/18"


def test_record_cycle_both_empty_returns_none(set_cycle):
    set_cycle(TUE)
    assert adb._record_cycle("", "") is None
    assert adb._record_cycle(None, None) is None


# ==================== _parse_occurred ====================

def test_parse_occurred_valid():
    assert adb._parse_occurred("2026-08-19") == datetime(2026, 8, 19)


def test_parse_occurred_with_time_truncated():
    assert adb._parse_occurred("2026-08-19 10:30:00") == datetime(2026, 8, 19)


def test_parse_occurred_invalid_returns_none():
    assert adb._parse_occurred("") is None
    assert adb._parse_occurred("garbage") is None
    assert adb._parse_occurred(None) is None
