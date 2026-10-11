# -*- coding: utf-8 -*-
"""行数据 → 单元格呈现（表格适配层）

边界：本模块只做「一条记录该显示成什么样」的决策——文本、字体类别、
前景/底色、操作链接清单、行底色。控件（QTableWidget 自身、按钮、弹窗）
都归 page.py；排序是数据库 ORDER BY 的职责，不在此处维护排序键。

这样「呈现规则」可以单独测试，page.py 只负责把 CellSpec 应用到表格。
"""

from PySide6.QtWidgets import QTableWidgetItem
from PySide6.QtGui import QColor
from qfluentwidgets import isDarkTheme

from core.design_tokens import SEMANTIC, lighten
from windows.aftersale.common import TABLE_COLUMNS, _YES_NO_COLORS
from windows.aftersale.records.query_state import short_dt as _short_dt

# 重要标记行底色（浅色=淡黄 / 深色=暗黄）：勾选「重要」后该条在列表中淡黄显示
_IMPORTANT_BG_LIGHT = QColor(255, 243, 205)
_IMPORTANT_BG_DARK = QColor(64, 57, 28)

# 操作列链接清单（(文案, 色键, 动作键)，由 core.ops_link_delegate 按矩形自绘）：
# 未解决行 3 段、已解决行 2 段，按行取而非按行号。
OPS_LINKS_UNRESOLVED = (("已解决", "primary", "resolve"),
                        ("编辑", "primary", "edit"),
                        ("删除", "danger", "delete"))
OPS_LINKS_RESOLVED = (("编辑", "primary", "edit"),
                      ("删除", "danger", "delete"))

# 字体类别 → page.py 预构建的 QFont 实例（循环外构造一次）
FONT_NORMAL = ""
FONT_SMALL = "small"
FONT_BOLD = "bold"


class CellSpec(object):
    """一个单元格的呈现描述（不持有任何 Qt 控件）"""

    __slots__ = ("text", "font", "fg", "bg", "align_center", "links")

    def __init__(self, text="", font=FONT_NORMAL, fg=None, bg=None,
                 align_center=False, links=None):
        self.text = text
        self.font = font
        self.fg = fg
        self.bg = bg
        self.align_center = align_center
        self.links = links


def row_background(rec: dict):
    """行底色：重要标记行随主题淡黄/暗黄，其余 None"""
    if not bool(rec.get("is_important")):
        return None
    return _IMPORTANT_BG_DARK if isDarkTheme() else _IMPORTANT_BG_LIGHT


def _badge_color(key: str, is_yes: bool) -> str:
    yes_c, _no_c = _YES_NO_COLORS[key]
    color = yes_c if is_yes else SEMANTIC["neutral"]
    if isDarkTheme():
        # SEMANTIC 为浅色底选的低饱和色，深色背景上「否」的石板灰近乎
        # 全黑、「是」的蓝也偏暗 → 统一提亮 35%
        color = lighten(color, 0.35)
    return color


def _badge_cell(key: str, rec: dict, row_bg) -> CellSpec:
    """是/否徽章文本化：只强调「是」（语义色加粗 + 极淡同色底）；
    「否」统一中性灰加粗、无底色，避免三列满屏色块过渡渲染。"""
    is_yes = str(rec.get(key) or "") == "是"
    color = _badge_color(key, is_yes)
    bg = None
    if row_bg is None and is_yes:
        bgc = QColor(color)
        bgc.setAlpha(20)  # ~8% 透明度，更淡的徽章底（仅「是」）
        bg = bgc
    return CellSpec(text="是" if is_yes else "否", font=FONT_BOLD,
                    fg=QColor(color), bg=bg, align_center=True)


def cell_for(key: str, rec: dict, row_bg=None) -> CellSpec:
    """单列呈现规则（外部工具可按列复用）"""
    if key == "created_at":
        # 显示文本截短丢年份，排序交给 SQL 按原始 created_at 列做
        return CellSpec(
            text="%s\n%s" % (_short_dt(rec.get("created_at")),
                             str(rec.get("creator") or "")),
            font=FONT_SMALL)
    if key == "location":
        # 显示文本是换行/点号拼串，排序由 SQL 按球房、地区、桌号逐级做
        return CellSpec(
            text="%s\n%s · %s" % (str(rec.get("room_name") or ""),
                                  str(rec.get("region") or ""),
                                  str(rec.get("table_no") or "")),
            font=FONT_SMALL)
    if key == "response_time":
        # 显示文本混入解决人次行，排序仍按原始 response_time 列
        return CellSpec(
            text="%s\n%s" % (str(rec.get("response_time") or "—"),
                             str(rec.get("resolver") or "")),
            font=FONT_SMALL)
    if key in _YES_NO_COLORS:
        return _badge_cell(key, rec, row_bg)
    if key == "ops":
        # 操作列：空文本 + 链接清单（LINKS_ROLE），由视图级委托按矩形
        # 自绘并反查命中，零子控件。清单按行取（未解决 3 段 / 已解决 2 段）
        links = (OPS_LINKS_RESOLVED
                 if str(rec.get("resolved") or "") == "是"
                 else OPS_LINKS_UNRESOLVED)
        return CellSpec(text="", links=links)
    return CellSpec(text=str(rec.get(key) or ""))


def data_cells(rec: dict):
    """一条记录的整行单元格描述（顺序与 TABLE_COLUMNS 一致，不含勾选列）"""
    row_bg = row_background(rec)
    return [cell_for(key, rec, row_bg) for key, _h, _w in TABLE_COLUMNS]


__all__ = ["CellSpec", "data_cells", "row_background",
           "cell_for", "OPS_LINKS_RESOLVED", "OPS_LINKS_UNRESOLVED",
           "FONT_NORMAL", "FONT_SMALL", "FONT_BOLD"]
