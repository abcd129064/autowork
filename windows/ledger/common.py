# -*- coding: utf-8 -*-
"""跑视频面板包内共享符号（列定义 + 复用的 Fluent UI 工厂）

UI 工厂（FluentCombo/_SectionCard/_row_btn/_badge_label 等）复用
windows.aftersale.common 的现成实现（经 __all__ 导出，口径一致）；
本模块只定义跑视频自身的常量：表格列、分类、类别候选。
"""
from windows.aftersale.common import (  # noqa: F401,F403
    _FIXED_ROW_HEIGHT, _popup_ani_type, _default_creator,
    _hex_rgba, FluentCombo, _SectionCard, YesNoSegment,
    _field_label, _inline_error, _badge_label, _row_btn, _ROW_BTN_TMPL,
)

from database import ledger_db

__all__ = [
    "TABLE_COLUMNS", "_COL_OPS", "CATEGORY_ACCENTS", "_category_badge",
    "_FIXED_ROW_HEIGHT", "_popup_ani_type", "_default_creator",
    "_hex_rgba", "FluentCombo", "_SectionCard", "YesNoSegment",
    "_field_label", "_inline_error", "_badge_label", "_row_btn",
    "_ROW_BTN_TMPL",
]

# 表格展示列（无勾选列；第 0 列即填写时间，ops 为行内操作列）。
# 字段与在线模板数据 sheet 对齐，系统附加 填写时间/操作 两列。
TABLE_COLUMNS = (
    ("created_at", "填写时间", 128),
    ("category", "分类", 64),
    ("kind", "类别", 130),
    ("room_name", "球房", 90),
    ("video_name", "视频名", 150),
    ("frame", "帧数", 52),
    ("description", "描述", 210),
    ("repro", "复现", 130),
    ("new_program", "新程序", 66),
    ("signer", "署名", 64),
    ("ops", "操作", 168),
)
_COL_OPS = len(TABLE_COLUMNS) - 1  # 操作列列号（最后一列）

# 分类徽章语义色：问题=红、未复现=橙、精度=蓝、使用=绿
CATEGORY_ACCENTS = {
    "问题": "#cf4452",
    "未复现": "#d97f2f",
    "精度": "#2f7fd9",
    "使用": "#2fae63",
}


def _category_badge(text: str, parent=None):
    """分类徽章：按分类映射语义色（未知分类用中性灰）"""
    from core.design_tokens import SEMANTIC
    from qfluentwidgets import isDarkTheme
    color = CATEGORY_ACCENTS.get(text, SEMANTIC["neutral"] if isDarkTheme()
                                else "#616161")
    return _badge_label(text, color, parent)
