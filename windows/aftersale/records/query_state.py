# -*- coding: utf-8 -*-
"""记录页纯逻辑

这里只放「输入 → 输出」的确定性函数：筛选条件汇集、分页文案、
时间截短、行 tooltip 拼串、列号 → 数据键映射。任何控件、线程、
弹窗相关的东西都不属于本模块——这是拆包后的第一道边界。
"""

from datetime import datetime  # noqa: F401  （保留给未来时间辅助函数）

# 类型下拉的「全部」哨兵文案
ALL_TYPES_LABEL = "全部类型"


def short_dt(val) -> str:
    """2026-08-22 21:14:33 → 08-22 21:14（不足 16 字符原样返回）"""
    s = str(val or "").strip()
    return s[5:16] if len(s) >= 16 else s


def build_filters(cycle, type_text, resolved, is_initiative,
                  is_our_problem, keyword) -> dict:
    """汇集当前筛选条件（周期/类型/状态/是否我们主动发起/是否我们的问题/关键词）"""
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
    """总页数（至少 1 页）"""
    size = max(1, int(page_size or 1))
    return max(1, (int(total or 0) + size - 1) // size)


def pager_text(page_no: int, total: int, page_size: int) -> str:
    """页脚：`1/2 · 共 65 条`"""
    return "%d/%d · 共 %d 条" % (page_no, pages_of(total, page_size), total)


def col_key(col: int, table_columns, check_col: int = 0):
    """表格列号 → TABLE_COLUMNS 数据键；勾选列/越界返回 None"""
    i = int(col) - int(check_col) - 1
    if 0 <= i < len(table_columns):
        return table_columns[i][0]
    return None


def row_tooltip(rec: dict) -> str:
    """行 tooltip：整合字段完整信息（含移入 tooltip 的判定字段）"""
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

