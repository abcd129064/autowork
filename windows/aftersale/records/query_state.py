# -*- coding: utf-8 -*-
"""记录页纯逻辑

这里只放「输入 → 输出」的确定性函数：筛选条件汇集、分页文案、
时间截短、行 tooltip 拼串、列号 → 数据键映射。任何控件、线程、
弹窗相关的东西都不属于本模块——这是拆包后的第一道边界。
不 import Qt 还有个好处：本模块能在没有 PySide6 的解释器里直接单测。
"""

from datetime import datetime  # noqa: F401  （保留给未来时间辅助函数）

# 类型下拉的「全部」哨兵。它与空串是两回事：空串代表不过滤，
# 把「全部类型」原样下传会被当成真实类型值去查，一条都匹配不到。
ALL_TYPES_LABEL = "全部类型"


def short_dt(val) -> str:
    """把 2026-08-22 21:14:33 截成 08-22 21:14，短串原样返回。

    固定切 s[5:16]，前提是库里的时间都存成 19 字符格式；空值和旧数据
    直接返回，切出半截时间比不显示更容易误导。
    """
    s = str(val or "").strip()
    return s[5:16] if len(s) >= 16 else s


def build_filters(cycle, type_text, resolved, is_initiative,
                  is_our_problem, keyword) -> dict:
    """汇集当前筛选条件，并把「全部类型」归一化成空串表示不过滤。

    各字段统一 strip：前后空格会让类型、关键词的等值与模糊匹配悄悄失效。
    """
    type_text = str(type_text or "").strip()
    return {
        "cycle_start": str(cycle or ""),
        "issue_type": ("" if (not type_text or type_text == ALL_TYPES_LABEL)
                       else type_text),
        "resolved": resolved,
        "is_initiative": is_initiative,
        "is_our_problem": is_our_problem,
        "keyword": str(keyword or "").strip(),
    }


def pages_of(total: int, page_size: int) -> int:
    """总页数，至少 1 页；page_size 为 0 时按 1 兜底防止除零。

    空表也必须给 1 页，否则页脚会显示 0/0。
    """
    size = max(1, int(page_size or 1))
    return max(1, (int(total or 0) + size - 1) // size)


def pager_text(page_no: int, total: int, page_size: int) -> str:
    """页脚文案 1/2 · 共 65 条；文案被单测锁定，改格式要同步改测试。

    页数统一走 pages_of，避免这里和翻页按钮的可点范围算出不一致。
    """
    return "%d/%d · 共 %d 条" % (page_no, pages_of(total, page_size), total)


def col_key(col: int, table_columns, check_col: int = 0):
    """表格逻辑列号 → TABLE_COLUMNS 数据键；勾选列与越界列返回 None。

    check_col 是左侧勾选列的列数，不减掉它整行字段都会错一位；返回 None
    让调用方跳过勾选列和填充列，而不是猜一个键。
    """
    i = int(col) - int(check_col) - 1
    if 0 <= i < len(table_columns):
        return table_columns[i][0]
    return None


def row_tooltip(rec: dict) -> str:
    """把一条记录拼成多行 tooltip，缺失字段留空、布尔字段显示「否」。

    用 or "" / or "否" 兜底，记录缺列时不会在 tooltip 里出现字面量 None。
    """
    return (
        f"填写时间: {rec.get('created_at') or ''}\n"
        f"填写人: {rec.get('creator') or ''}\n"
        f"发生时间: {rec.get('occurred_at') or ''}\n"
        f"类型: {rec.get('issue_type') or ''}\n"
        f"位置: {rec.get('room_name') or ''} · "
        f"{rec.get('region') or ''} · {rec.get('table_no') or ''}\n"
        f"问题: {rec.get('problem') or ''}\n"
        f"发生原因: {rec.get('cause') or ''}\n"
        f"是否解决: {rec.get('resolved') or '否'}\n"
        f"是否我们主动发起: {rec.get('is_initiative') or '否'}\n"
        f"是否是我们的问题: {rec.get('is_our_problem') or '否'}\n"
        f"解决方案: {rec.get('solution') or ''}\n"
        f"解决人: {rec.get('resolver') or ''}\n"
        f"响应时间: {rec.get('response_time') or ''}")

