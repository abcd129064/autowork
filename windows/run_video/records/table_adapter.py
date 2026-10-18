# -*- coding: utf-8 -*-
"""行数据 → 单元格呈现（跑视频记录页的表格适配层）

边界：本模块只回答「一条记录该显示成什么样」——文本、字体类别、前景/底色、
tooltip、操作链接清单，不创建任何 widget。控件（QTableWidget、委托安装、
滚动位置还原）归 page.py；排序由 SQL ORDER BY 负责，这里不维护排序键。

呈现规则逐条复刻拆分前 records.py 的 _populate（原 L870-933）：
分类徽章文本化（语义色前景 + 10% 同色淡底 + 加粗居中）、描述超 60 字 /
复现超 30 字截断并把全文移入 tooltip、操作列空文本挂 _OPS_LINKS 由
core.ops_link_delegate 自绘并反查命中。

这样「呈现规则」可以脱离页面单测：tests/test_run_video_records_table_adapter.py。
"""

from PySide6.QtGui import QColor

from windows.run_video.common import TABLE_COLUMNS, _OPS_LINKS, _category_color
from windows.run_video.records.query_state import short_dt

# 字体类别 → page.py 循环外预构建的 QFont 实例（与售后表格适配层同口径）
FONT_NORMAL = ""
FONT_BOLD = "bold"

# 截断阈值与原表一致：描述 60 字、复现 30 字
_DESC_LIMIT = 60
_REPRO_LIMIT = 30

# 分类徽章底色透明度：约 10%，近似徽章淡底（原 _populate 同值）
_BADGE_BG_ALPHA = 26


class CellSpec(object):
    """一个单元格的呈现描述"""

    __slots__ = ("text", "tooltip", "font", "fg", "bg",
                 "align_center", "links")

    def __init__(self, text="", tooltip="", font=FONT_NORMAL, fg=None,
                 bg=None, align_center=False, links=None):
        self.text = text
        self.tooltip = tooltip
        self.font = font
        self.fg = fg
        self.bg = bg
        self.align_center = align_center
        self.links = links


def _clip(text: str, limit: int):
    """超长截断，返回 (展示文本, tooltip)。

    tooltip 只在真的截断时回传完整文本：未截断的单元格不挂一条与正文
    相同的提示（原 _populate 也是 desc != short_desc 才 setToolTip）。
    """
    if len(text) > limit:
        return text[:limit] + "…", text
    return text, ""


def _plain_cell(rec: dict, key: str) -> CellSpec:
    """普通列：假值（None/空串）统一空文本，避免出现 "None" """
    return CellSpec(text=str(rec.get(key) or ""))


def _category_cell(rec: dict) -> CellSpec:
    """分类徽章：语义色前景 + 同色淡底 + 加粗居中（已无 cellWidget）"""
    text = str(rec.get("category") or "")
    color = _category_color(text)
    # 底色必须是前景色的副本：同一 QColor 上改 alpha 会连带把前景变成半透明
    bg = QColor(color)
    bg.setAlpha(_BADGE_BG_ALPHA)
    return CellSpec(text=text, font=FONT_BOLD, fg=color, bg=bg,
                    align_center=True)


def _clipped_cell(rec: dict, key: str, limit: int) -> CellSpec:
    text, tooltip = _clip(str(rec.get(key) or ""), limit)
    return CellSpec(text=text, tooltip=tooltip)


def cell_for(key: str, rec: dict) -> CellSpec:
    """单列呈现规则；key 取自 TABLE_COLUMNS，未登记的键按普通文本列兜底"""
    if key == "created_at":
        # 08-22 21:14（紧凑展示，丢年份）；排序仍按原始 created_at 列
        return CellSpec(text=short_dt(rec.get("created_at")))
    if key == "category":
        return _category_cell(rec)
    if key == "description":
        return _clipped_cell(rec, key, _DESC_LIMIT)
    if key == "repro":
        return _clipped_cell(rec, key, _REPRO_LIMIT)
    if key == "ops":
        # 空文本 + 链接清单（LINKS_ROLE），由视图级委托按矩形自绘并反查命中
        return CellSpec(text="", links=_OPS_LINKS)
    return _plain_cell(rec, key)


def data_cells(rec: dict) -> list:
    """一条记录的整行单元格描述（顺序严格等于 TABLE_COLUMNS，含 ops 列）"""
    return [cell_for(key, rec) for key, _header, _width in TABLE_COLUMNS]


__all__ = ["CellSpec", "cell_for", "data_cells", "FONT_NORMAL", "FONT_BOLD"]
