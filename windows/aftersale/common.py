# -*- coding: utf-8 -*-
"""common 模块（从 windows/aftersale_panel.py 拆出，逻辑未改动）"""

import os
from datetime import datetime, timedelta

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QLabel, QListWidget,
    QListWidgetItem, QFileDialog, QComboBox as _QComboBox, QApplication,
    QDialog, QPushButton as _QPushButton, QGridLayout)
from PySide6.QtCore import Qt, QTimer, QThread, QPointF, QDate, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QFont
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
    "_COMBO_QSS_TMPL", "_EDIT_LINEEDIT_QSS_TMPL", "_CAND_LIST_QSS_TMPL",
    "_accent_hex", "_style_cand_list", "FluentCombo", "_hex_rgba",
    "_SectionCard", "YesNoSegment", "_field_label", "_inline_error",
    "TABLE_COLUMNS", "_COL_CHECK", "_YES_NO_COLORS", "_badge_label",
    "_ROW_BTN_TMPL", "_row_btn", "_PREVIEW_COLUMNS", "_PREVIEW_WIDTHS",
]

# 表格固定行高（与管理面板一致，避免默认行高浪费纵向空间）
_FIXED_ROW_HEIGHT = 32


def _popup_ani_type():
    """弹出菜单动画类型：关闭动画选项时用 NONE（性能开关联动）"""
    from core.perf import is_animation_enabled
    return (MenuAnimationType.DROP_DOWN if is_animation_enabled()
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

# 下拉框主体 QSS 模板（深浅主题各填一套显式色值）
_COMBO_QSS_TMPL = """
QComboBox {{
    background-color: {bg};
    border: 1px solid {border};
    border-radius: 4px;
    padding: 3px 30px 3px 11px;
    color: {text};
    min-height: 26px;
}}
QComboBox:hover {{ border-color: {border_hover}; }}
QComboBox:focus, QComboBox:on {{ border-color: {accent}; }}
QComboBox:editable {{ padding-right: 30px; }}
QComboBox:disabled {{ color: {text_disabled}; background-color: {bg_disabled}; }}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 26px;
    border: none;
}}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBox QAbstractItemView {{
    background-color: {popup_bg};
    border: 1px solid {border};
    border-radius: 4px;
    color: {text};
    selection-background-color: {accent};
    selection-color: #ffffff;
    outline: none;
    padding: 2px;
}}
"""

# 可编辑下拉框内部 QLineEdit：去掉自带边框，与外框融为一体
_EDIT_LINEEDIT_QSS_TMPL = (
    "QLineEdit {{ border: none; background: transparent; padding: 0;"
    " color: {text}; selection-background-color: {accent};"
    " selection-color: #ffffff; }}"
)

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


class FluentCombo(_QComboBox):
    """原生 QComboBox + Fluent 统一样式（主题自适应 + 自绘下拉箭头）

    用原生控件是因为 qfluentwidgets 的 ComboBox 是按钮式下拉，不支持
    setEditable/findData。此类补齐 Fluent 外观，保证与面板内其它
    qfluentwidgets 输入控件（LineEdit/SearchLineEdit）视觉一致。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(33)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._arrow_color = QColor("#616161")
        self._err = False  # 字段级校验错误态（红框）
        self._apply_theme()
        try:
            qconfig.themeChanged.connect(self._apply_theme)
        except Exception:
            pass

    def setError(self, on: bool):
        """字段校验错误态：红框提示；主题切换重应用样式时保留"""
        self._err = bool(on)
        self._apply_theme()

    def _apply_theme(self):
        """按当前主题应用显式色值（不依赖 palette，主题切换后重应用）"""
        dark = isDarkTheme()
        accent = _accent_hex()
        if dark:
            c = dict(bg="#2b2b2b", border="#484848", border_hover="#5c5c5c",
                     text="#e8eaed", text_disabled="#6f6f6f",
                     bg_disabled="#262626", popup_bg="#2f2f2f", accent=accent)
            self._arrow_color = QColor("#a8adb4")
        else:
            c = dict(bg="#fdfdfd", border="#c9c9c9", border_hover="#a6a6a6",
                     text="#1f1f1f", text_disabled="#a0a0a0",
                     bg_disabled="#f2f2f2", popup_bg="#ffffff", accent=accent)
            self._arrow_color = QColor("#616161")
        self._colors = c  # 缓存当前主题色，供 setEditable 后补 lineEdit 样式
        qss = _COMBO_QSS_TMPL.format(**c)
        if self._err:  # 错误态追加红框（同选择器后置规则胜出）
            qss += "QComboBox { border-color: %s; }" % SEMANTIC["danger"]
        self.setStyleSheet(qss)
        self._style_lineedit()
        self.update()

    def _style_lineedit(self):
        """给可编辑态的内部 QLineEdit 去边框样式（非编辑态无 lineEdit 则跳过）"""
        le = self.lineEdit()
        if le is not None:
            le.setStyleSheet(_EDIT_LINEEDIT_QSS_TMPL.format(**self._colors))

    def setEditable(self, editable):
        """启用编辑后内部 QLineEdit 才创建，需补应用 Fluent 样式"""
        super().setEditable(editable)
        if editable:
            self._style_lineedit()

    def paintEvent(self, event):
        super().paintEvent(event)
        # 自绘 Fluent 下拉箭头（右侧居中 chevron，QSS 已隐藏默认箭头）
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = self.width() - 17
        cy = self.height() / 2
        pen = QPen(self._arrow_color, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPolyline([QPointF(cx - 4.5, cy - 2.2), QPointF(cx, cy + 2.4),
                        QPointF(cx + 4.5, cy - 2.2)])
        p.end()


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
    """字段标签：12px 次级文本色；必填追加红色 * 富文本"""
    lb = QLabel(parent)
    if required:
        lb.setText(text + ' <span style="color:#cf4452;">*</span>')
    else:
        lb.setText(text)
    lb.setStyleSheet(
        "font-size: 12px; color: #444b55; background: transparent;")
    try:
        qconfig.themeChanged.connect(
            lambda: lb.setStyleSheet("font-size: 12px; color: %s;"
                " background: transparent;" % (
                    "#c5c8ce" if isDarkTheme() else "#444b55")))
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


def _row_btn(text: str, style: str, on_click, parent=None) -> _QPushButton:
    """行内操作按钮：primary=强调色实心 / danger=红色描边 / ghost=中性描边

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
    btn = _QPushButton(text, parent)
    btn.setFixedHeight(24)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(_ROW_BTN_TMPL.format(**kw))
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
