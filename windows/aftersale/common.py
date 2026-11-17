# -*- coding: utf-8 -*-
"""售后页与弹窗共享的常量、控件工厂和小工具。

本模块从 windows/aftersale_panel.py 拆出，逻辑保持不变。页面与对话框模块
用星号导入引入这里的符号，因此新增公共符号时既要加进 __all__，也要确认
名字不与该模块内的局部变量冲突。
"""

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

from core.design_tokens import (SEMANTIC, lighten, darken,
    UI_V2, ui_color, ui_hover_colors, ui_qss)
from core.flow_widgets import FlowToolbarScrollArea
from core.perf import is_acrylic_enabled
from core.theme_qss import current_accent_hex
from core.utils import show_info_bar
from database import aftersale_db, table_db
from workers.aftersale_worker import AftersaleDBWorker
from windows.mysql_sync_card import MysqlSyncCard

# 包内共享符号：页面/对话框模块用星号导入引入。星号导入默认跳过下划线名，
# 所以 _SectionCard、_row_btn 这类内部辅助必须显式列进 __all__，否则会 NameError
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
    """弹出菜单动画类型：返回本面板覆盖值，缺省继承全局开关。

    低配机器上关动画能明显减少右键菜单掉帧，所以开关按面板粒度生效；
    禁用时返回 NONE，让 Qt 跳过整个动画管线，而不是只把时长设为 0。
    """
    from core.perf import get_animation
    return (MenuAnimationType.DROP_DOWN if get_animation("aftersale")
            else MenuAnimationType.NONE)


def _default_creator() -> str:
    """填写人默认值：从配置门面读 newlog_target_name。

    配置读取包在 try 里，因为表单构建早期就会调用本函数，此时设置模块
    可能尚未完成初始化；失败时退回空串，表单仍能正常显示。
    """
    try:
        from core import app_settings
        return str(app_settings.get("newlog_target_name", "") or "")
    except Exception:
        return ""


# ==================== Fluent 统一下拉框样式 ====================

# FluentCombo 与配套的 _COMBO_QSS_TMPL/_EDIT_LINEEDIT_QSS_TMPL 已删除：
# 下拉框已全部换成 qfluentwidgets ComboBox/EditableComboBox，下方只留候选列表样式

# 桌号候选列表 QSS：与下拉框同风格。用占位符而非固定色值，深浅主题切换时
# 由 _style_cand_list 重新注入，避免维护两套完整样式表
# 圆角 R8/R4、间距 8/16、字号 12、线宽 1px 全部由 V2 token 注入。
# 占位符由 _style_cand_list 按主题填充，模板本身不写死任何色值。
_CAND_LIST_QSS_TMPL = """
QListWidget {{
    background-color: {popup_bg};
    border: {line_w}px solid {border};
    border-radius: {card_radius}px;
    color: {text};
    outline: none;
    padding: {pad}px;
    font-size: {font}px;
}}
QListWidget::item {{ padding: {pad}px {pad2}px; border-radius: {ctl_radius}px; }}
QListWidget::item:hover {{ background-color: {item_hover}; }}
QListWidget::item:selected {{ background-color: {accent}; color: #ffffff; }}
QListWidget::item:disabled {{ color: {disabled}; padding: {pad}px {pad2}px; }}
QScrollBar:vertical {{
    background: transparent;
    width: {scroll_w}px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {border};
    border-radius: {ctl_radius}px;
    min-height: {chip_h}px;
}}
QScrollBar::handle:vertical:hover {{ background: {item_hover}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
"""


def _accent_hex() -> str:
    """读取 qfluentwidgets 当前主题强调色（统一走 core.theme_qss，失败回退默认青）"""
    return current_accent_hex()


def _style_cand_list(widget):
    """给桌号候选列表应用当前主题的 QSS。

    颜色在调用时现取：qfluentwidgets 的强调色可被用户随时修改，缓存
    样式表会让候选列表停留在旧强调色上。
    """
    dark = isDarkTheme()
    hov = ui_hover_colors(dark)
    c = dict(popup_bg=ui_color("control", dark),
             border=ui_color("line", dark),
             text=ui_color("body", dark),
             item_hover=hov["bg"],
             disabled=ui_color("disabled", dark),
             accent=_accent_hex(),
             line_w=UI_V2["line"]["width"],
             card_radius=UI_V2["radius"]["card"],
             ctl_radius=UI_V2["radius"]["control"],
             pad=UI_V2["space"]["xs"],
             pad2=UI_V2["space"]["md"],
             font=UI_V2["type"]["caption"]["size"],
             scroll_w=UI_V2["space"]["xs"],
             chip_h=UI_V2["size"]["chip"])
    widget.setStyleSheet(_CAND_LIST_QSS_TMPL.format(**c))


# 注：FluentCombo 类已于 2026-08-24 需求13 删除——全部下拉已换
# qfluentwidgets ComboBox/EditableComboBox，原生 QComboBox 包装不再需要。


def _hex_rgba(hex_color: str, alpha: int) -> str:
    """#rrggbb 转 rgba()，供徽章与确认条的半透明底色使用。

    只接受 6 位十六进制，不支持简写：调用方都传 SEMANTIC 令牌，格式稳定，
    多写简写分支只会增加没有测试覆盖的路径。
    """
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


# ==================== 表单分组与自定义 Fluent 组件 ====================

class _SectionCard(CardWidget):
    """表单分组卡片：左侧强调色竖条 + 分组标题，对应设计稿的三段式分组。

    竖条是区分字段分组的唯一视觉线索，强调色变化时必须跟着重绘；监听
    themeColorChanged 而不是在 paintEvent 里现算，避免每次重绘都读全局配置。
    """

    def __init__(self, title, parent=None):
        super().__init__(parent)
        s, r = UI_V2["space"], UI_V2["radius"]
        self.content_layout = QVBoxLayout(self)
        # 四边统一 16px：旧值 (16,12,16,14) 让标题与内容的光学基线对不齐
        self.content_layout.setContentsMargins(s["md"], s["md"], s["md"], s["md"])
        self.content_layout.setSpacing(s["sm"])
        self.setStyleSheet("CardWidget { border-radius: %dpx; }" % r["card"])
        head = QHBoxLayout()
        head.setSpacing(s["xs"])
        bar = QLabel(self)
        # 竖条 4x16 落在网格上；2px 圆角是它的半宽，不做装饰性 1px
        bar.setFixedSize(UI_V2["base"], s["md"])
        bar.setStyleSheet(
            f"background: {_accent_hex()}; border-radius: 2px;")
        try:
            qconfig.themeColorChanged.connect(lambda _c: bar.setStyleSheet(
                f"background: {_accent_hex()}; border-radius: 2px;"))
        except Exception:
            pass
        head.addWidget(bar)
        ttl = BodyLabel(title, self)
        ttl.setStyleSheet(ui_qss("title"))
        head.addWidget(ttl)
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
        # 「全部」段的 routeKey 用空串，与 value() 的空串约定一致；
        # 顺序固定为 全部/否/是，避免同一控件在不同页面里顺序漂移
        self._include_all = include_all
        if include_all:
            self.addItem("", "全部")
        self.addItem("否", "否")
        self.addItem("是", "是")
        self.setCurrentItem(default)
        # Pivot 的 hBoxLayout 带 SetMinimumSize 约束：布局激活时会拿内部
        # SegmentedItem 的最小高度（库默认 26px）反写父控件 minimumHeight，
        # 只在外层 setFixedHeight(32) 会在 show 时被冲回 26。把 item 也锁到
        # 32px，布局算出的最小高度本身就是 32，不必在 showEvent 里追赶。
        for item in self.items.values():
            item.setFixedHeight(UI_V2["size"]["control"])
        self.setFixedHeight(UI_V2["size"]["control"])

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
    """字段标签：统一 12/16/400，必填追加语义红 * 富文本。

    颜色走中性灰阶的 secondary 角色、亮暗反相；构造期即设色，因为
    themeChanged 只在主题「改变」时触发，深色启动时初始样式会失效。
    """
    lb = QLabel(parent)
    if required:
        lb.setText(text + ' <span style="color:%s;">*</span>'
                   % SEMANTIC["danger"])
    else:
        lb.setText(text)

    def _apply():
        lb.setStyleSheet("%s color: %s; background: transparent;" % (
            ui_qss("caption"), ui_color("secondary", isDarkTheme())))

    _apply()
    try:
        qconfig.themeChanged.connect(lambda: _apply())
    except Exception:
        pass
    return lb


def _inline_error(text, parent=None) -> QLabel:
    """字段级内联错误提示：创建即隐藏，由表单在校验失败时显示。

    用独立 QLabel 而不是改字段样式，错误文案不会把输入框撑高，用户修正后
    也无需额外恢复边框。
    """
    lb = QLabel(text, parent)
    lb.setStyleSheet(
        "%s color: %s; background: transparent;"
        % (ui_qss("error"), SEMANTIC["danger"]))
    lb.setVisible(False)
    return lb

# 表格展示列（信息整合版：位置列合并球房+地区+桌号，填写人并入填写时间，
# 解决人并入解决时间；完整信息仍保留在行 tooltip，第 0 列为勾选列）
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
# 已解决 绿/红、我方问题 橙/灰、主动发起 蓝/灰；徽章底色由运行时加透明度生成
_YES_NO_COLORS = {
    "resolved": (SEMANTIC["success"], SEMANTIC["danger"]),
    "is_our_problem": (SEMANTIC["warning"], SEMANTIC["neutral"]),
    "is_initiative": (SEMANTIC["info"], SEMANTIC["neutral"]),
}


# ---------- 表格行内控件工厂（徽章 / 小按钮） ----------

def _badge_label(text: str, color: str, parent=None) -> QLabel:
    """状态徽章：圆角胶囊、语义色文字、同色半透明底。

    底色用文字色的低透明度版本，深浅主题下都能保持对比度；写死浅色底
    会在深色主题里发白，反而盖住文字。
    """
    lb = QLabel(text, parent)
    lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lb.setFixedHeight(UI_V2["size"]["chip"])
    lb.setStyleSheet(
        "QLabel { background: %s; color: %s; border-radius: %dpx;"
        " padding: 0 %dpx; %s }" % (
            _hex_rgba(color, 30), color, UI_V2["radius"]["pill"],
            UI_V2["space"]["xs"], ui_qss("caption")))
    return lb


# 圆角 R4、内边距 8px、字号 12、线宽 1px 全由 token 注入；hover 只切换
# 统一的边框与底色状态，不做逐帧动画。
_ROW_BTN_TMPL = (
    "QPushButton {{ background: {bg}; color: {fg}; border: {line_w}px solid {bd};"
    " border-radius: {radius}px; padding: {pad_v}px {pad_h}px; font-size: {font}px; }}"
    "QPushButton:hover {{ background: {hov}; border-color: {hov_bd}; }}"
    "QPushButton:pressed {{ background: {prs}; }}"
)


def _row_btn_css(style: str) -> str:
    """行内按钮 QSS（primary=强调色实心 / danger=红色描边 / ghost=中性描边）

    强调色实时读取当前主题（与迁移按钮同套路），hover/pressed 由令牌派生。
    """
    from core.theme_qss import current_accent_hex
    dark = isDarkTheme()
    accent = current_accent_hex()
    hov = ui_hover_colors(dark)
    if style == "primary":
        kw = dict(bg=accent, fg="#ffffff", bd=accent,
                  hov=lighten(accent, 0.12), hov_bd=lighten(accent, 0.12),
                  prs=darken(accent, 0.18))
    elif style == "danger":
        d = SEMANTIC["danger"]
        kw = dict(bg="transparent", fg=d, bd=_hex_rgba(d, 140),
                  hov=_hex_rgba(d, 26), hov_bd=d, prs=_hex_rgba(d, 46))
    else:
        kw = dict(bg="transparent", fg=ui_color("body", dark),
                  bd=ui_color("line", dark), hov=hov["bg"],
                  hov_bd=hov["line"], prs=ui_color("hover", dark))
    kw.update(line_w=UI_V2["line"]["width"],
              radius=UI_V2["radius"]["control"],
              pad_v=UI_V2["space"]["xs"],
              pad_h=UI_V2["space"]["xs"],
              font=UI_V2["type"]["caption"]["size"])
    return _ROW_BTN_TMPL.format(**kw)


def _prebuild_btn_css() -> dict:
    """一次预构建 primary/danger/ghost 三种按钮 QSS（表格批量填充用）

    表格每行 2-3 个按钮，若每按钮现算样式会重复读主题色 + 拼 QSS 字符串；
    同一页填充期间主题不变，循环外构建一次即可省去 ~150 次重复计算。
    """
    return {s: _row_btn_css(s) for s in ("primary", "danger", "ghost")}


def _row_btn(text: str, style: str, on_click, parent=None,
             css: str | None = None) -> _QPushButton:
    """行内操作按钮；css 可传入预构建样式，批量填充时免去重复计算。

    clicked 会带一个 checked 布尔值，lambda 用下划线参数接住丢弃，确保
    回调始终按无参签名调用，不用为每个按钮写适配函数。
    """
    btn = _QPushButton(text, parent)
    btn.setFixedHeight(UI_V2["size"]["chip"])
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(css if css is not None else _row_btn_css(style))
    btn.clicked.connect(lambda _=False: on_click())
    return btn


# 导入预览列定义：前几列与导出的 Excel 表头一致，末尾附加系统字段。
# 必须与导出表头保持同一顺序，否则用户拿导出文件回导时列会错位
_PREVIEW_COLUMNS = (
    ("issue_type", "类型"), ("room_name", "球房"), ("table_no", "桌号"),
    ("region", "地区"), ("problem", "问题"), ("cause", "发生原因"),
    ("resolved", "是否解决"), ("solution", "解决方案"),
    ("resolver", "解决人"), ("response_time", "响应时间"),
    ("created_at", "填写时间"), ("occurred_at", "发生时间"),
    ("creator", "填写人"), ("cycle_start", "周期"),
)
_PREVIEW_WIDTHS = (76, 208, 64, 50, 248, 220, 76, 160, 64, 76, 152, 90, 64, 110)
