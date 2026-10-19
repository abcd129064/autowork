# -*- coding: utf-8 -*-
"""售后周期计算纯逻辑（无数据库 / 无 Qt / 无 app_settings）。

从 database/aftersale_db.py 下沉的第一步（A3）：周期模式以显式 dict 传入，
不读配置、不连库，便于直接单测。aftersale_db 保留同名包装函数并注入
load_cycle_mode()，外部调用与既有 monkeypatch 点不变。

模式 dict 形如：
    {"type": "tue"|"mon"|"custom"|"month", "start": "yyyy-MM-dd", "span": 7}
"""

import calendar
from datetime import datetime, timedelta


def parse_occurred(value):
    """解析记录日期串 YYYY-MM-DD（允许带时间，取前 10 位），非法返回 None。"""
    try:
        return datetime.strptime(str(value or "").strip()[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def parse_cycle_start(value):
    """解析周期起点：兼容 yyyy-MM-dd 与 yyyy/MM/dd，非法返回 None。"""
    text = str(value or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except (ValueError, TypeError):
            pass
    return None


def span_days(mode, today=None):
    """周期天数：tue/mon 固定 7，custom 取配置（≥1），month 取当月天数。"""
    if mode.get("type") == "month":
        now = today or datetime.now()
        return calendar.monthrange(now.year, now.month)[1]
    if mode.get("type") == "custom":
        try:
            return max(1, int(mode.get("span") or 7))
        except (TypeError, ValueError):
            return 7
    return 7


def start_of(dt, mode):
    """计算给定时间所属周期的起始日，格式 yyyy/MM/dd。"""
    if mode.get("type") == "month":
        return dt.replace(day=1).strftime("%Y/%m/%d")
    if mode.get("type") == "mon":
        start = dt - timedelta(days=dt.weekday())
    elif mode.get("type") == "custom":
        start0 = parse_occurred(mode.get("start")) or dt
        span = max(1, int(mode.get("span") or 7))
        diff = (dt.date() - start0.date()).days
        start = start0 + timedelta(days=(diff // span) * span)
    else:  # tue 默认
        start = dt - timedelta(days=(dt.weekday() - 1) % 7)
    return start.strftime("%Y/%m/%d")


def current_start(mode, now=None):
    """当前周期起始日。"""
    return start_of(now or datetime.now(), mode)


def label(cycle_start, mode):
    """周期展示标签：周模式 '08/19 - 08/25'，month 模式 '2026-08'。"""
    start = parse_cycle_start(cycle_start)
    if start is None:
        return str(cycle_start or "")
    if mode.get("type") == "month":
        return f"{start:%Y}-{start:%m}"
    end = start + timedelta(days=span_days(mode) - 1)
    return f"{start:%m/%d} - {end:%m/%d}"


def date_range(cycle_start, mode):
    """周期起止日期 (date, date)：month 为整月，其余为起始日 + span-1 天。"""
    start = parse_cycle_start(cycle_start)
    if start is None:
        return None, None
    s = start.date()
    if mode.get("type") == "month":
        e = s.replace(day=calendar.monthrange(s.year, s.month)[1])
    else:
        e = s + timedelta(days=span_days(mode) - 1)
    return s, e


def record_cycle(occurred_at, created_at, mode):
    """记录归属周期：优先 occurred_at，缺失/非法回退 created_at；均无返回 None。"""
    for raw in (occurred_at, created_at):
        dt = parse_occurred(raw)
        if dt:
            return start_of(dt, mode)
    return None


def match_cycle(record, cycle_start, mode):
    """记录是否属于所选周期：空周期不过滤。"""
    if not cycle_start:
        return True
    return record_cycle(record.get("occurred_at"), record.get("created_at"),
                        mode) == str(cycle_start).strip()


def is_valid_start(cycle_start, mode):
    """周期起点合法性：True=真实起点，False=格式合法但口径不符，None=不可解析。"""
    cyc = str(cycle_start or "").strip()
    try:
        start = datetime.strptime(cyc, "%Y/%m/%d")
    except (ValueError, TypeError):
        return None
    return start_of(start, mode) == cyc
