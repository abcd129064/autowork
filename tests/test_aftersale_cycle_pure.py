# -*- coding: utf-8 -*-
"""database.aftersale_cycle 纯逻辑回归（A3 第一步）。

不读 config、不连库；周期模式显式传入。aftersale_db 的同名包装函数由
tests/test_aftersale_cycle.py 覆盖，本文件锁纯模块接口。
"""
from datetime import datetime

from database import aftersale_cycle as ac

TUE = {"type": "tue", "start": "", "span": 7}
MON = {"type": "mon", "start": "", "span": 7}
MONTH = {"type": "month", "start": "", "span": 7}
CUSTOM = {"type": "custom", "start": "2026-08-18", "span": 3}


def _d(text):
    return datetime.strptime(text, "%Y-%m-%d")


def test_parse_occurred_and_cycle_start():
    assert ac.parse_occurred("2026-08-19 10:30:00") == _d("2026-08-19")
    assert ac.parse_occurred("bad") is None
    assert ac.parse_occurred(None) is None
    assert ac.parse_cycle_start("2026/08/18") == _d("2026-08-18")
    assert ac.parse_cycle_start("2026-08-18") == _d("2026-08-18")
    assert ac.parse_cycle_start("") is None


def test_start_of_all_modes():
    assert ac.start_of(_d("2026-08-19"), TUE) == "2026/08/18"
    assert ac.start_of(_d("2026-08-23"), TUE) == "2026/08/18"
    assert ac.start_of(_d("2026-08-23"), MON) == "2026/08/17"
    assert ac.start_of(_d("2026-08-21"), CUSTOM) == "2026/08/21"
    assert ac.start_of(_d("2026-08-15"), CUSTOM) == "2026/08/15"
    assert ac.start_of(_d("2026-08-15"), MONTH) == "2026/08/01"


def test_span_days():
    assert ac.span_days(TUE) == 7
    assert ac.span_days(CUSTOM) == 3
    assert ac.span_days({"type": "custom", "span": -1}) == 1
    assert ac.span_days({"type": "custom", "span": 0}) == 7
    assert ac.span_days({"type": "custom", "span": "bad"}) == 7
    assert ac.span_days(MONTH, _d("2026-02-10")) == 28
    assert ac.span_days(MONTH, _d("2026-08-10")) == 31


def test_label_and_date_range():
    assert ac.label("2026/08/18", TUE) == "08/18 - 08/24"
    assert ac.label("2026/08/18", CUSTOM) == "08/18 - 08/20"
    assert ac.label("2026/08/01", MONTH) == "2026-08"
    assert ac.label("garbage", TUE) == "garbage"
    assert ac.date_range("2026/08/18", TUE) == (
        datetime(2026, 8, 18).date(), datetime(2026, 8, 24).date())
    assert ac.date_range("2026/02/01", MONTH) == (
        datetime(2026, 2, 1).date(), datetime(2026, 2, 28).date())
    assert ac.date_range("bad", TUE) == (None, None)


def test_record_cycle_and_match():
    assert ac.record_cycle("2026-08-19", "2026-08-20", TUE) == "2026/08/18"
    assert ac.record_cycle("", "2026-08-20", TUE) == "2026/08/18"
    assert ac.record_cycle("", "", TUE) is None
    rec = {"occurred_at": "2026-08-19", "created_at": ""}
    assert ac.match_cycle(rec, "2026/08/18", TUE) is True
    assert ac.match_cycle(rec, "2026/08/25", TUE) is False
    assert ac.match_cycle(rec, "", TUE) is True


def test_is_valid_start():
    assert ac.is_valid_start("2026/08/18", TUE) is True
    assert ac.is_valid_start("2026/08/19", TUE) is False
    assert ac.is_valid_start("2026/08/01", MONTH) is True
    assert ac.is_valid_start("bad", TUE) is None
    assert ac.is_valid_start("", TUE) is None
