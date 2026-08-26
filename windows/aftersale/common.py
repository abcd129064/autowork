# -*- coding: utf-8 -*-
"""common 模块（从 windows/aftersale_panel.py 拆出，逻辑未改动）"""

import os
from datetime import datetime, timedelta

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QLabel, QListWidget,
    QListWidgetItem, QFileDialog, QApplication,
    QDialog, QPushButton as _QPushButton, QGridLayout)
from PySide6.QtCore import Qt, QTimer, QThread, QDate, Signal
from qfluentwidgets import (TableWidget, SearchLineEdit, PushButton,
    PrimaryPushButton, ToolButton, FluentIcon, RoundMenu, Action,
    LineEdit, PlainTextEdit, BodyLabel, CaptionLabel, TitleLabel,
    ScrollArea, CardWidget, MessageBox, MessageBoxBase, CheckBox,
    FluentWindow, NavigationItemPosition, MenuAnimationType,
    setCustomStyleSheet, qconfig, isDarkTheme, ZhDatePicker, RadioButton,
    SpinBox, SegmentedWidget, ProgressBar, FlowLayout)

from core.design_tokens import SEMANTIC, lighten, darken
from core.flow_widgets import FlowToolbarScrollArea
from core.perf import is_acrylic_enabled
from core.theme_qss import current_accent_hex
from core.utils import show_info_bar
from database import aftersale_db, table_db
from workers.aftersale_worker import AftersaleDBWorker
from windows.mysql_sync_card import MysqlSyncCard

# 包内共享符号：页面/对话框模块经 ``from windows.aftersale.common import *``
# 引入（含下划线辅助名，故显式列出 __all__）
__all__ = [
    "_FIXED_ROW_HEIGHT", "_popup_ani_type", "_default_creator",
    "_CAND_LIST_QSS_TMPL",
    "_accent_hex", "_style_cand_list", "_hex_rgba",
    "_SectionCard", "YesNoSegment", "_field_label", "_inline_error",
    "TABLE_COLUMNS", "_COL_CHECK", "_YES_NO_COLORS", "_badge_label",
    "_ROW_BTN_TMPL", "_row_btn", "_row_btn_css", "_prebuild_btn_css",
    "_PREVIEW_COLUMNS", "_PREVIEW_WIDTHS",
]

# 表格固定行高（与管理面板一致，避免默认行高浪费纵向空间）
_FIXED_ROW_HEIGHT = 32


def _popup_ani_type():
    """弹出菜单动画类型：按售后面板生效值（本面板覆盖→全局开关）"""
    from core.perf import get_animation
    return (MenuAnimationType.DROP_DOWN if get_animation("aftersale")
            else MenuAnimationType.NONE)


def _default_creator() -> str:
    """填写人默认值：settings.json 的 newlog_target_name"""
    import json
    from core.app_paths import get_app_dir
    try:
        with open(os.path.join(get_app_dir(), "settings.json"),
                  "r", encoding="utf-8") as f:
            return str(json.load(f).get("newlog_target_name", "") or "")
    except Exception:
        return ""


# ==================== Fluent 统一下拉框样式 ====================

# 注：FluentCombo 及 _COMBO_QSS_TMPL/_EDIT_LINEEDIT_QSS_TMPL 已于 2026-08-24
# 需求13 删除——全部下拉已换 qfluentwidgets ComboBox/EditableComboBox，
# 原生 QComboBox 样式不再需要。下方保留桌号候选列表样式（仍在用）。

# 桌号候选列表 QSS（与下拉框同风格：圆角边框 + 主题背景 + 强调色选中）
_CAND_LIST_QSS_TMPL = """
QListWidget {{
    background-color: {popup_bg};
    border: 1px solid {border};
    border-radius: 6px;
    color: {text};
    outline: none;
    padding: 2px;
}}
QListWidget::item {{ padding: 4px 8px; border-radius: 4px; }}
QListWidget::item:hover {{ background-color: {item_hover}; }}
QListWidget::item:selected {{ background-color: {accent}; color: #ffffff; }}
QListWidget::item:disabled {{ color: #909090; padding: 10px 8px; }}
QScrollBar:vertical {{
    background: transparent;
    width: 6px;
    margin: 2px 0;
}}
QScrollBar::handle:vertical {{
    background: {border};
    border-radius: 3px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: {item_hover}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
"""


def _accent_hex() -> str:
    """读取 qfluentwidgets 当前主题强调色（统一走 core.theme_qss，失败回退默认青）"""
    return current_accent_hex()


def _style_cand_list(widget):
    """给桌号候选列表应用主题自适应 QSS（与下拉框同风格）"""
    accent = _accent_hex()
    if isDarkTheme():
        c = dict(popup_bg="#2f2f2f", border="#484848", text="#e8eaed",
                 item_hover="#3a3a3a", accent=accent)
    else:
        c = dict(popup_bg="#ffffff", border="#c9c9c9", text="#1f1f1f",
                 item_hover="#f0f0f0", accent=accent)
    widget.setStyleSheet(_CAND_LIST_QSS_TMPL.format(**c))


# 注：FluentCombo 类已于 2026-08-24 需求13 删除——全部下拉已换
# qfluentwidgets ComboBox/EditableComboBox，原生 QComboBox 包装不再需要。


def _hex_rgba(hex_color: str, alpha: int) -> str:
    """#rrggbb → rgba()（QSS 徽章/确认条底色用，半透明底深浅主题均清晰）"""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


# ==================== 表单分组与自定义 Fluent 组件 ====================

class _SectionCard(CardWidget):
    """表单分组卡片：左侧强调色竖条 + 分组标题（设计稿三段式布局）"""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.content_layout = QVBoxLayout(self)
        self.content_layout.setContentsMargins(16, 12, 16, 14)
        self.content_layout.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(7)
        bar = QLabel(self)
        bar.setFixedSize(3, 12)
        bar.setStyleSheet(
            f"background: {_accent_hex()}; border-radius: 1px;")
        try:
            qconfig.themeColorChanged.connect(lambda _c: bar.setStyleSheet(
                f"background: {_accent_hex()}; border-radius: 1px;"))
        except Exception:
            pass
        head.addWidget(bar)
        head.addWidget(BodyLabel(title, self))
        head.addStretch(1)
        self.content_layout.addLayout(head)


class YesNoSegment(SegmentedWidget):
    """是/否 二值分段开关（qfluentwidgets 现成 SegmentedWidget）

    对外暴露 value()/setValue(str)，读写口径与旧 FluentCombo 的
    currentText 一致（"是"/"否"），collect/set_values 无感知。
    include_all=True 时追加「全部」段（value() 返回空串），供筛选
    场景复用同一组件，默认保持二值。
    三态统一库默认外观：旧版选「是」时主动 setStyleSheet 染语义绿底，
    但 SegmentedItem 选中态是库 paintEvent 硬画（QSS 选择器不生效），
    实际只剩整条绿矩形底、观感像 bug，已移除染色逻辑。
    """

    _DEFAULT = "否"

    def __init__(self, default=_DEFAULT, parent=None, include_all=False):
        super().__init__(parent)
        self._include_all = include_all
        if include_all:
            self.addItem("", "全部")
        self.addItem("否", "否")
        self.addItem("是", "是")
        self.setCurrentItem(default)
        self.setFixedHeight(30)

    def value(self) -> str:
        key = self.currentRouteKey()
        if key in ("是", "否"):
            return key
        if self._include_all and key == "":
            return ""
        return self._DEFAULT

    def setValue(self, text):
        t = str(text or "").strip()
        if self._include_all and t == "":
            self.setCurrentItem("")
        elif t in ("是", "否"):
            self.setCurrentItem(t)
        else:
            self.setCurrentItem(self._DEFAULT)


def _field_label(text, required=False, parent=None) -> QLabel:
    """字段标签：12px 次级文本色；必填追加红色 * 富文本

    深色主题下标签用亮灰（#c5c8ce），浅色主题用深灰（#444b55）。
    init 即按当前主题设色，避免深色主题下标签呈黑色与背景同色看不清
    （themeChanged 只在主题「改变」时触发，深色启动时初始样式会失效）。
    """
    lb = QLabel(parent)
    if required:
        lb.setText(text + ' <span style="color:#cf4452;">*</span>')
    else:
        lb.setText(text)

    def _apply():
        lb.setStyleSheet("font-size: 12px; color: %s;"
            " background: transparent;" % (
                "#c5c8ce" if isDarkTheme() else "#444b55"))

    _apply()
    try:
        qconfig.themeChanged.connect(lambda: _apply())
    except Exception:
        pass
    return lb


def _inline_error(text, parent=None) -> QLabel:
    """字段级内联错误提示（默认隐藏，校验失败时显示）"""
    lb = QLabel(text, parent)
    lb.setStyleSheet(
        "font-size: 11px; color: #cf4452; background: transparent;")
    lb.setVisible(False)
    return lb

# 表格展示列（信息整合版：球房+地区+桌号合入「位置」，填写人并入填写时间，
# 解决人并入响应时间；发生原因/解决方案/三个是否判定直接成列，
# 完整信息仍保留在行 tooltip）。第 0 列为勾选框（多选/批量操作），不在本定义内。
TABLE_COLUMNS = (
    ("created_at", "填写时间", 128),
    ("occurred_at", "发生时间", 128),
    ("issue_type", "类型", 90),
    ("location", "位置", 200),
    ("problem", "问题", 200),   # Interactive 列，初始宽度 200，可拖拽调宽
    ("cause", "发生原因", 180),
    ("solution", "解决方案", 180),
    ("resolved", "解决", 70),
    ("is_our_problem", "我们问题", 70),
    ("is_initiative", "主动发起", 70),
    ("response_time", "响应", 110),
    ("ops", "操作", 168),   # stretch 拉伸列：初始 168，自动填满面板剩余宽度（按钮靠左）
)
_COL_CHECK = 0  # 勾选列列号

# 是/否徽章语义色（元组：是 → 前色，否 → 后色）：
# 是否解决 绿/红、是否是我们问题 橙/灰、是否主动发起 蓝/灰；徽章底色由运行时加透明度生成
_YES_NO_COLORS = {
    "resolved": (SEMANTIC["success"], SEMANTIC["danger"]),
    "is_our_problem": (SEMANTIC["warning"], SEMANTIC["neutral"]),
    "is_initiative": (SEMANTIC["info"], SEMANTIC["neutral"]),
}


# ---------- 表格行内控件工厂（徽章 / 小按钮） ----------

def _badge_label(text: str, color: str, parent=None) -> QLabel:
    """状态徽章：圆角胶囊 + 语义色文字 + 同色半透明底"""
    lb = QLabel(text, parent)
    lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lb.setFixedHeight(20)
    lb.setStyleSheet(
        f"QLabel {{ background: {_hex_rgba(color, 30)}; color: {color};"
        " border-radius: 9px; padding: 0 9px; font-size: 12px; }")
    return lb


_ROW_BTN_TMPL = (
    "QPushButton {{ background: {bg}; color: {fg}; border: 1px solid {bd};"
    " border-radius: 4px; padding: 2px 8px; font-size: 12px; }}"
    "QPushButton:hover {{ background: {hov}; border-color: {hov_bd}; }}"
    "QPushButton:pressed {{ background: {prs}; }}"
)


def _row_btn_css(style: str) -> str:
    """行内按钮 QSS（primary=强调色实心 / danger=红色描边 / ghost=中性描边）

    强调色实时读取当前主题（与迁移按钮同套路），hover/pressed 由令牌派生。
    """
    from core.theme_qss import current_accent_hex
    accent = current_accent_hex()
    if style == "primary":
        kw = dict(bg=accent, fg="#ffffff", bd=accent,
                  hov=lighten(accent, 0.12), hov_bd=lighten(accent, 0.12),
                  prs=darken(accent, 0.18))
    elif style == "danger":
        d = SEMANTIC["danger"]
        kw = dict(bg="transparent", fg=d, bd=_hex_rgba(d, 140),
                  hov=_hex_rgba(d, 26), hov_bd=d, prs=_hex_rgba(d, 46))
    else:  # ghost：中性描边（深浅主题各一套灰，跟随主题切换）
        if isDarkTheme():
            kw = dict(bg="transparent", fg="#c8d0dc", bd="#484848",
                      hov="#333333", hov_bd="#5c5c5c", prs="#3a3a3a")
        else:
            kw = dict(bg="transparent", fg="#333333", bd="#c9c9c9",
                      hov="#f0f0f0", hov_bd="#a6a6a6", prs="#e4e4e4")
    return _ROW_BTN_TMPL.format(**kw)


def _prebuild_btn_css() -> dict:
    """一次预构建 primary/danger/ghost 三种按钮 QSS（表格批量填充用）

    表格每行 2-3 个按钮，若每按钮现算样式会重复读主题色 + 拼 QSS 字符串；
    同一页填充期间主题不变，循环外构建一次即可省去 ~150 次重复计算。
    """
    return {s: _row_btn_css(s) for s in ("primary", "danger", "ghost")}


def _row_btn(text: str, style: str, on_click, parent=None,
             css: str | None = None) -> _QPushButton:
    """行内操作按钮（css 可传入预构建样式，批量填充时避免重复计算）"""
    btn = _QPushButton(text, parent)
    btn.setFixedHeight(24)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(css if css is not None else _row_btn_css(style))
    btn.clicked.connect(lambda _=False: on_click())
    return btn


# 导入预览列定义（与导出表头对齐 + 系统附加列）
_PREVIEW_COLUMNS = (
    ("issue_type", "类型"), ("room_name", "球房"), ("table_no", "桌号"),
    ("region", "地区"), ("problem", "问题"), ("cause", "发生原因"),
    ("resolved", "是否解决"), ("solution", "解决方案"),
    ("resolver", "解决人"), ("response_time", "响应时间"),
    ("created_at", "填写时间"), ("occurred_at", "发生时间"),
    ("creator", "填写人"), ("cycle_start", "周期"),
)
_PREVIEW_WIDTHS = (76, 208, 64, 50, 248, 220, 76, 160, 64, 76, 152, 90, 64, 110)
