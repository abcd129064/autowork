# -*- coding: utf-8 -*-
"""售后面板（独立窗口模块，风格仿运维管理面板）

功能页面（左侧导航切换）：
1. 填写录入 —— 售后问题登记表单（字段参照 售后问题汇总8月.xlsx），
   桌号关联球桌管理库，选中自动带出球房/SNK/设备编码
2. 记录与统计 —— 售后记录筛选/分页/统计/编辑/删除/导出 xlsx/导入 Excel

数据层 database/aftersale_db.py（SQLite/MySQL 双后端，自动跟随 MySQL 测试开关），
所有 DB 读写经后台 QThread（workers/aftersale_worker.py），UI 零阻塞。
周期规则：周二开始、周一结束，按填写时间自动归属。
"""

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
    """

    _DEFAULT = "否"

    def __init__(self, default=_DEFAULT, parent=None):
        super().__init__(parent)
        self.addItem("否", "否")
        self.addItem("是", "是")
        self.setCurrentItem(default)
        self.setFixedHeight(30)
        self.currentItemChanged.connect(lambda _k: self._restyle())
        self._restyle()

    def _restyle(self):
        """选中「是」时整段染成功绿（Fluent 语义色），「否」保持默认灰"""
        val = self.value()
        ok = SEMANTIC["success"]
        if val == "是":
            self.setStyleSheet(
                f"SegmentedWidget {{ background: {_hex_rgba(ok, 16)}; }}"
                f"SegmentedItem {{ background: transparent; color: {ok}; }}"
                f"SegmentedItem[selected=true] {{ background: {ok};"
                " color: #ffffff; border-radius: 4px; }")
        else:
            self.setStyleSheet("")

    def value(self) -> str:
        key = self.currentRouteKey()
        return key if key in ("是", "否") else self._DEFAULT

    def setValue(self, text):
        t = str(text or "").strip()
        self.setCurrentItem(t if t in ("是", "否") else self._DEFAULT)


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


# ==================== 共享表单（录入页与编辑弹窗复用） ====================

class AftersaleForm(QWidget):
    """售后记录表单：字段与 售后问题汇总8月.xlsx 对齐 + 系统附加字段

    桌号输入防抖搜索球桌管理库，候选列表点选后自动带出球房/SNK；
    精确匹配单台球桌时静默带出，无需点选。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._snk_code = ""      # 关联球桌带出（隐藏字段，随记录落库）
        self._search_kw = ""
        self._last_city = ""     # 上次由球桌带出的城市（球房变动时联动清空）
        self._cand_worker = None
        self._init_ui()

        # 球房搜索防抖：停止输入 300ms 后才查库，避免逐字触发查询
        self._search_timer = QTimer(self)
        self._search_timer.setInterval(300)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._do_room_search)

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # ---------- 板块一：基本信息（标签上置，控件固定宽度，右侧留白） ----------
        sec1 = _SectionCard("基本信息", self)
        row1 = QHBoxLayout()
        row1.setSpacing(16)
        col_type = QVBoxLayout()
        col_type.setSpacing(3)
        col_type.addWidget(_field_label("问题类型", True, self))
        # 与「问题描述-问题」同规格：可编辑下拉 + NoInsert（可选预置也可手输）
        self.type_combo = FluentCombo(self)
        self.type_combo.setEditable(True)
        self.type_combo.setInsertPolicy(_QComboBox.InsertPolicy.NoInsert)
        self.type_combo.addItems(aftersale_db.ISSUE_TYPES)
        self.type_combo.setFixedWidth(320)
        col_type.addWidget(self.type_combo)
        self._err_type = _inline_error("必填项未填写", self)
        col_type.addWidget(self._err_type)
        row1.addLayout(col_type)
        col_occ = QVBoxLayout()
        col_occ.setSpacing(3)
        col_occ.addWidget(_field_label("发生时间", True, self))
        occurred_row = QHBoxLayout()
        occurred_row.setSpacing(2)
        self.occurred_picker = ZhDatePicker(self)
        self.occurred_picker.setFixedWidth(150)
        self.occurred_picker.setFixedHeight(33)
        self.occurred_picker.setDate(QDate.currentDate())
        occurred_row.addWidget(self.occurred_picker)
        # 日期步进按钮（实心三角成组）：连续点击逐日前移/后移，
        # 补录历史发生日期（如 8/25 录 8/20 的售后）连续点 ◀ 即可回退
        self._btn_occurred_prev = ToolButton(
            FluentIcon.CARE_LEFT_SOLID, self)
        self._btn_occurred_prev.setFixedSize(28, 33)
        self._btn_occurred_prev.setToolTip("前一天")
        self._btn_occurred_prev.clicked.connect(
            lambda _=False: self._step_occurred(-1))
        occurred_row.addWidget(self._btn_occurred_prev)
        self._btn_occurred_next = ToolButton(
            FluentIcon.CARE_RIGHT_SOLID, self)
        self._btn_occurred_next.setFixedSize(28, 33)
        self._btn_occurred_next.setToolTip("后一天")
        self._btn_occurred_next.clicked.connect(
            lambda _=False: self._step_occurred(1))
        occurred_row.addWidget(self._btn_occurred_next)
        col_occ.addLayout(occurred_row)
        row1.addLayout(col_occ)
        col_creator = QVBoxLayout()
        col_creator.setSpacing(3)
        col_creator.addWidget(_field_label("填写人", False, self))
        self.creator_edit = LineEdit(self)
        self.creator_edit.setText(_default_creator())  # 默认取配置，可改
        self.creator_edit.setFixedWidth(160)
        col_creator.addWidget(self.creator_edit)
        row1.addLayout(col_creator)
        row1.addStretch(1)
        sec1.content_layout.addLayout(row1)
        root.addWidget(sec1)

        # ---------- 板块二：位置关联（控件固定宽度，带出芯片 + 关联确认条） ----------
        sec2 = _SectionCard("位置关联", self)
        row2 = QHBoxLayout()
        row2.setSpacing(16)
        room_col = QVBoxLayout()
        room_col.setSpacing(3)
        room_col.addWidget(_field_label("球房", True, self))
        self.room_edit = SearchLineEdit(self)
        self.room_edit.setPlaceholderText("输入球房名搜索球桌，如 BaoClub")
        self.room_edit.setFixedWidth(320)
        self.room_edit.textChanged.connect(self._on_room_text_changed)
        self.room_edit.clearSignal.connect(self._hide_candidates)
        room_col.addWidget(self.room_edit)
        self._err_room = _inline_error("必填项未填写", self)
        room_col.addWidget(self._err_room)
        row2.addLayout(room_col)
        tbl_col = QVBoxLayout()
        tbl_col.setSpacing(3)
        tbl_col.addWidget(_field_label("桌号", False, self))
        self.table_no_edit = LineEdit(self)
        self.table_no_edit.setPlaceholderText("选桌自动带出")
        self.table_no_edit.setFixedWidth(140)
        tbl_col.addWidget(self.table_no_edit)
        self._err_table = _inline_error("必填项未填写", self)
        tbl_col.addWidget(self._err_table)
        row2.addLayout(tbl_col)
        reg_col = QVBoxLayout()
        reg_col.setSpacing(3)
        reg_col.addWidget(_field_label("地区", True, self))
        self.region_combo = FluentCombo(self)
        self.region_combo.setEditable(True)
        self.region_combo.setInsertPolicy(_QComboBox.InsertPolicy.NoInsert)
        self.region_combo.addItems(aftersale_db.REGIONS_PRESET)
        self.region_combo.setFixedWidth(160)
        reg_col.addWidget(self.region_combo)
        self._err_region = _inline_error("必填项未填写", self)
        reg_col.addWidget(self._err_region)
        row2.addLayout(reg_col)
        row2.addStretch(1)
        sec2.content_layout.addLayout(row2)
        # 球桌候选列表（默认隐藏，搜索命中后展示）
        self._cand_list = QListWidget(self)
        self._cand_list.setFixedHeight(132)
        self._cand_list.setVisible(False)
        self._cand_list.itemClicked.connect(self._on_candidate_clicked)
        _style_cand_list(self._cand_list)
        try:
            qconfig.themeChanged.connect(
                lambda: _style_cand_list(self._cand_list))
        except Exception:
            pass
        sec2.content_layout.addWidget(self._cand_list)
        # 关联确认条：选桌后展示桌号 + SNK，隐藏字段 snk_code 的可视反馈
        self._link_bar = QLabel(self)
        self._link_bar.setObjectName("aftersaleLinkBar")
        self._link_bar.setWordWrap(True)
        self._link_bar.setStyleSheet(
            "QLabel#aftersaleLinkBar { background: %s; color: %s;"
            " border-radius: 6px; padding: 5px 10px; font-size: 12px; }"
            % (_hex_rgba(SEMANTIC["success"], 26), SEMANTIC["success"]))
        self._link_bar.setVisible(False)
        sec2.content_layout.addWidget(self._link_bar)
        root.addWidget(sec2)

        # ---------- 板块三：问题描述 ----------
        sec3 = _SectionCard("问题描述", self)
        v3 = sec3.content_layout
        v3.addWidget(_field_label("问题", True, self))
        self.problem_combo = FluentCombo(self)
        self.problem_combo.setEditable(True)
        self.problem_combo.setInsertPolicy(_QComboBox.InsertPolicy.NoInsert)
        self.problem_combo.setFixedWidth(320)
        v3.addWidget(self.problem_combo)
        self._err_problem = _inline_error("必填项未填写", self)
        v3.addWidget(self._err_problem)
        # 发生原因 / 解决方案：等宽等高多行文本框并排（64px ≈ 3 行）
        txt_row = QHBoxLayout()
        txt_row.setSpacing(14)
        col_cause = QVBoxLayout()
        col_cause.setSpacing(5)
        col_cause.addWidget(_field_label("发生原因", False, self))
        self.cause_edit = PlainTextEdit(self)
        self.cause_edit.setFixedHeight(64)
        self.cause_edit.setPlaceholderText("选填")
        col_cause.addWidget(self.cause_edit)
        txt_row.addLayout(col_cause, 1)
        col_sol = QVBoxLayout()
        col_sol.setSpacing(5)
        col_sol.addWidget(_field_label("解决方案", False, self))
        self.solution_edit = PlainTextEdit(self)
        self.solution_edit.setFixedHeight(64)
        self.solution_edit.setPlaceholderText("选填，多行文本")
        col_sol.addWidget(self.solution_edit)
        txt_row.addLayout(col_sol, 1)
        v3.addLayout(txt_row)
        # 三个是/否判定：分段开关（默认值与旧版一致：是 / 否 / 是）
        seg_row = QHBoxLayout()
        seg_row.setSpacing(18)
        seg_row.addWidget(_field_label("是否解决", True, self))
        self.resolved_combo = YesNoSegment("是", self)
        seg_row.addWidget(self.resolved_combo)
        seg_row.addWidget(_field_label("我们主动发起", False, self))
        self.is_initiative_combo = YesNoSegment("否", self)
        seg_row.addWidget(self.is_initiative_combo)
        seg_row.addWidget(_field_label("是我们的问题", False, self))
        self.is_our_problem_combo = YesNoSegment("是", self)
        seg_row.addWidget(self.is_our_problem_combo)
        seg_row.addStretch(1)
        v3.addLayout(seg_row)
        # 解决人 / 响应时间：左右平分两列
        res_row = QHBoxLayout()
        res_row.setSpacing(16)
        col_res = QVBoxLayout()
        col_res.setSpacing(5)
        col_res.addWidget(_field_label("解决人", False, self))
        self.resolver_combo = FluentCombo(self)
        self.resolver_combo.setEditable(True)
        self.resolver_combo.setInsertPolicy(_QComboBox.InsertPolicy.NoInsert)
        self.resolver_combo.setFixedWidth(220)
        col_res.addWidget(self.resolver_combo)
        res_row.addLayout(col_res)
        col_resp = QVBoxLayout()
        col_resp.setSpacing(5)
        col_resp.addWidget(_field_label("响应时间", False, self))
        self.response_combo = FluentCombo(self)
        self.response_combo.setEditable(True)
        self.response_combo.setInsertPolicy(_QComboBox.InsertPolicy.NoInsert)
        self.response_combo.addItems(aftersale_db.RESPONSE_TIME_PRESET)
        self.response_combo.setFixedWidth(220)
        col_resp.addWidget(self.response_combo)
        res_row.addLayout(col_resp)
        res_row.addStretch(1)
        v3.addLayout(res_row)
        root.addWidget(sec3)

        self.first_error = None  # 最近一次校验的首个错误控件（供滚动聚焦）

    # ---------- 候选值加载 ----------

    def load_candidates(self, cands: dict):
        """填充动态候选（问题/解决人/地区），保留用户已输入文本"""
        cur_problem = self.problem_combo.currentText().strip()
        self.problem_combo.clear()
        self.problem_combo.addItems(cands.get("problems", []))
        if cur_problem:
            self.problem_combo.setEditText(cur_problem)
        cur_resolver = self.resolver_combo.currentText().strip()
        self.resolver_combo.clear()
        self.resolver_combo.addItems(cands.get("resolvers", []))
        if cur_resolver:
            self.resolver_combo.setEditText(cur_resolver)
        # 地区：预置 + 历史新增合并
        cur_region = self.region_combo.currentText().strip()
        regions = list(aftersale_db.REGIONS_PRESET)
        for r in cands.get("regions", []):
            if r not in regions:
                regions.append(r)
        self.region_combo.clear()
        self.region_combo.addItems(regions)
        if cur_region:
            self.region_combo.setEditText(cur_region)

    # ---------- 球房搜索与带出 ----------

    def _on_room_text_changed(self, text):
        """球房输入变动：清空旧桌号/SNK/带出城市，防抖后异步搜索球桌"""
        self._search_kw = str(text or "").strip()
        self._snk_code = ""  # 文本变动后旧的关联失效
        self.table_no_edit.clear()  # 旧桌号失效，防止错带到新球房
        self._set_linked(False)  # 旧关联确认条/带出芯片失效
        # 地区：仅当当前文本是上次带出的城市时联动清空（手填/改过的地区保留）
        if (self._last_city and
                self.region_combo.currentText().strip() == self._last_city):
            self.region_combo.setEditText("")
            self._last_city = ""
        if not self._search_kw:
            self._hide_candidates()
            return
        self._search_timer.start()

    def _do_room_search(self):
        """防抖后按球房名异步搜索球桌（含关键词快照，过期结果丢弃）"""
        kw = self._search_kw
        if not kw:
            return
        self._cand_worker = AftersaleDBWorker(
            table_db.query_tables_by_room, kw, 30)
        self._cand_worker.result_ready.connect(
            lambda result, k=kw: self._on_room_candidates(k, result))
        self._cand_worker.error.connect(lambda _m: self._hide_candidates())
        self._cand_worker.start()

    def _on_room_candidates(self, kw, result):
        if kw != self._search_kw:
            return  # 输入已变化，丢弃过期结果
        rows = result or []
        # 只命中唯一球桌：静默带出，不弹候选
        if len(rows) == 1:
            self._apply_table(rows[0])
            self._hide_candidates()
            return
        self._cand_list.clear()
        if not rows:
            # 无结果提示（不可选中，不干扰后续手填）
            item = QListWidgetItem(f"未找到「{kw}」的球桌，可直接填写")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._cand_list.addItem(item)
            self._cand_list.setVisible(True)
            return
        self._cand_rows = rows
        for r in rows:
            name = str(r.get("name") or "")
            room = str(r.get("roomName") or "")
            item = QListWidgetItem(f"{name} · {room}")
            item.setToolTip(f"桌号: {name}\n球房: {room}\nSNK: {r.get('snk_code') or ''}")
            self._cand_list.addItem(item)
        self._cand_list.setVisible(True)

    def _on_candidate_clicked(self, item):
        row_idx = self._cand_list.row(item)
        rows = getattr(self, "_cand_rows", [])
        if 0 <= row_idx < len(rows):
            self._apply_table(rows[row_idx])
        self._hide_candidates()

    def _apply_table(self, row):
        """选中球桌 → 带出桌号/球房/SNK/城市（球房阻断信号避免重复触发搜索）"""
        self.table_no_edit.setText(str(row.get("name") or ""))
        room = str(row.get("roomName") or "")
        self.room_edit.blockSignals(True)
        self.room_edit.setText(room)
        self.room_edit.blockSignals(False)
        self._snk_code = str(row.get("snk_code") or "")
        self._search_kw = room.strip()
        city = str(row.get("city") or "").strip()
        if city:
            self.region_combo.setEditText(city)
            self._last_city = city
        elif self.region_combo.currentText().strip() == self._last_city:
            # 新球桌城市未知（老库未采集）：清掉上一桌带出的残留城市，
            # 避免换球房后地区仍显示旧城市；手填的地区不受影响
            self.region_combo.setEditText("")
            self._last_city = ""
        self._set_linked(True, row)

    def _set_linked(self, on, row=None):
        """切换关联确认条（隐藏字段 snk_code 的可视反馈）"""
        if not on or not row:
            self._link_bar.setVisible(False)
            return
        city = str(row.get("city") or row.get("region") or "").strip()
        parts = ["已关联球桌：%s · %s 号桌" % (
            str(row.get("roomName") or ""), str(row.get("name") or ""))]
        if str(row.get("snk_code") or ""):
            parts.append("SNK: %s" % str(row.get("snk_code")))
        if city:
            parts.append("城市自动带出")
        self._link_bar.setText("✓　" + "　|　".join(parts))
        self._link_bar.setVisible(True)

    def _hide_candidates(self):
        self._cand_list.setVisible(False)

    def _step_occurred(self, delta_days: int):
        """发生时间步进：负数前移、正数后移；collect 时取 picker.date 新值"""
        self.occurred_picker.setDate(
            self.occurred_picker.date.addDays(delta_days))

    # ---------- 值读写 ----------

    def set_values(self, rec: dict):
        """编辑模式：用已有记录填充表单"""
        self.type_combo.setCurrentText(str(rec.get("issue_type") or ""))
        occurred = str(rec.get("occurred_at") or "").strip()
        occ_d = QDate.fromString(occurred, "yyyy-MM-dd")
        if occ_d.isValid():
            self.occurred_picker.setDate(occ_d)
        self.table_no_edit.setText(str(rec.get("table_no") or ""))
        room = str(rec.get("room_name") or "")
        self.room_edit.blockSignals(True)  # 回填不触发搜索/联动清空
        self.room_edit.setText(room)
        self.room_edit.blockSignals(False)
        self._search_kw = room.strip()
        self.region_combo.setEditText(str(rec.get("region") or ""))
        self.problem_combo.setEditText(str(rec.get("problem") or ""))
        self.cause_edit.setPlainText(str(rec.get("cause") or ""))
        self.resolved_combo.setValue(rec.get("resolved") or "是")
        self.is_initiative_combo.setValue(rec.get("is_initiative") or "否")
        self.is_our_problem_combo.setValue(rec.get("is_our_problem") or "是")
        self.solution_edit.setPlainText(str(rec.get("solution") or ""))
        self.resolver_combo.setEditText(str(rec.get("resolver") or ""))
        self.response_combo.setEditText(str(rec.get("response_time") or ""))
        self.creator_edit.setText(str(rec.get("creator") or ""))
        self._snk_code = str(rec.get("snk_code") or "")
        self._last_city = ""  # 编辑回填不参与城市联动
        self.clear_error()

    def collect(self) -> dict:
        """收集表单值为记录 dict（不含必填校验）"""
        return {
            "issue_type": self.type_combo.currentText().strip(),
            "occurred_at": self.occurred_picker.date.toString("yyyy-MM-dd"),
            "table_no": self.table_no_edit.text().strip(),
            "room_name": self.room_edit.text().strip(),
            "region": self.region_combo.currentText().strip(),
            "problem": self.problem_combo.currentText().strip(),
            "cause": self.cause_edit.toPlainText().strip(),
            "resolved": self.resolved_combo.value(),
            "is_initiative": self.is_initiative_combo.value(),
            "is_our_problem": self.is_our_problem_combo.value(),
            "solution": self.solution_edit.toPlainText().strip(),
            "resolver": self.resolver_combo.currentText().strip(),
            "response_time": self.response_combo.currentText().strip(),
            "creator": self.creator_edit.text().strip(),
            "snk_code": self._snk_code,
        }

    def _required_map(self):
        """必填字段 → (取值, 错误控件, 输入控件)，顺序即校验/聚焦顺序"""
        return [
            ("类型", not self.type_combo.currentText().strip(),
             self._err_type, self.type_combo),
            ("桌号", not self.table_no_edit.text().strip(),
             self._err_table, self.table_no_edit),
            ("球房", not self.room_edit.text().strip(),
             self._err_room, self.room_edit),
            ("地区", not self.region_combo.currentText().strip(),
             self._err_region, self.region_combo),
            ("问题", not self.problem_combo.currentText().strip(),
             self._err_problem, self.problem_combo),
        ]

    def validate(self) -> list:
        """必填校验：返回缺失字段中文名列表，并驱动字段级红框/内联提示

        同时记录 first_error（首个错误输入控件），供调用方滚动聚焦。
        """
        missing = []
        self.first_error = None
        for name, bad, err_lbl, ctl in self._required_map():
            err_lbl.setVisible(bad)
            if hasattr(ctl, "setError"):
                ctl.setError(bad)
            if bad:
                missing.append(name)
                if self.first_error is None:
                    self.first_error = ctl
        return missing

    def clear_error(self):
        """清除全部字段级错误态（输入恢复后由调用方触发）"""
        for _name, _bad, err_lbl, ctl in self._required_map():
            err_lbl.setVisible(False)
            if hasattr(ctl, "setError"):
                ctl.setError(False)
        self.first_error = None

    def clear_form(self):
        """清空表单（保留填写人与下拉候选）"""
        self.room_edit.clear()
        self.table_no_edit.clear()
        self.cause_edit.clear()
        self.solution_edit.clear()
        self.problem_combo.setEditText("")
        self.resolver_combo.setEditText("")
        self.response_combo.setEditText("")
        self.resolved_combo.setValue("是")
        self.is_initiative_combo.setValue("否")
        self.is_our_problem_combo.setValue("是")
        self.type_combo.setCurrentIndex(-1)
        self.occurred_picker.setDate(QDate.currentDate())  # 默认当日
        self.region_combo.setEditText("")
        self._snk_code = ""
        self._last_city = ""
        self._set_linked(False)
        self.clear_error()
        self._hide_candidates()


# ==================== 板块一：填写录入页 ====================

class EntryPage(QWidget):
    """填写录入页：页头（周期指示）+ 三段表单 + 随内容操作条"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._save_worker = None
        self._cand_worker = None
        self._init_ui()

    def _init_ui(self):
        # 外边距与记录页一致（左右 20），标题不贴边
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 12)
        root.setSpacing(10)

        # 页头：标题 + 说明左对齐，周期指示芯片右对齐
        head = QHBoxLayout()
        head.setSpacing(12)
        head_l = QVBoxLayout()
        head_l.setSpacing(2)
        head_l.addWidget(TitleLabel("填写录入", self))
        head_l.addWidget(CaptionLabel(
            "提交后写入数据库，多人协作刷新可见", self))
        head.addLayout(head_l)
        head.addStretch(1)
        self._cycle_chip = CaptionLabel(self)
        self._cycle_chip.setStyleSheet(
            "background: %s; color: %s; border-radius: 10px;"
            " padding: 3px 10px;" % (
                _hex_rgba(SEMANTIC["info"], 18), SEMANTIC["info"]))
        head.addWidget(self._cycle_chip)
        root.addLayout(head)

        # 表单滚动区：内容按自身高度排布，不拉伸铺满整页；
        # 操作条随内容放在最后一栏控件下方（与修改前一致）
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }")
        view = QWidget()
        view.setStyleSheet("QWidget { background: transparent; }")
        scroll.setWidget(view)
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(12)
        self.form = AftersaleForm(view)
        layout.addWidget(self.form)

        # 操作条：必填进度 + 清空/提交（表单末栏下方，右对齐）
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self._prog = ProgressBar(view)
        self._prog.setRange(0, 5)
        self._prog.setValue(0)
        self._prog.setTextVisible(False)
        self._prog.setFixedWidth(72)
        bar.addWidget(self._prog)
        self._prog_text = CaptionLabel("必填项 0/5 已填写", view)
        bar.addWidget(self._prog_text)
        bar.addStretch(1)
        self._btn_clear = PushButton(FluentIcon.DELETE, "清空", view)
        self._btn_clear.setToolTip("清空表单全部内容")
        self._btn_clear.setFixedHeight(34)
        self._btn_clear.clicked.connect(self._on_clear)
        bar.addWidget(self._btn_clear)
        self._btn_submit = PrimaryPushButton(FluentIcon.ACCEPT, "提交记录", view)
        self._btn_submit.setToolTip("校验必填项后写入数据库")
        self._btn_submit.setFixedHeight(34)
        self._btn_submit.clicked.connect(self._on_submit)
        bar.addWidget(self._btn_submit)
        layout.addLayout(bar)
        # 剩余高度由 stretch 吸收，表单保持固定排版不随窗口拉伸
        layout.addStretch(1)
        root.addWidget(scroll, 1)
        self._scroll = scroll

        # 必填进度联动：任一必填控件变化即重算
        f = self.form
        for sig in (f.type_combo.currentTextChanged,
                    f.table_no_edit.textChanged,
                    f.room_edit.textChanged,
                    f.region_combo.editTextChanged,
                    f.region_combo.currentIndexChanged,
                    f.problem_combo.editTextChanged,
                    f.problem_combo.currentIndexChanged):
            try:
                sig.connect(self._update_required_progress)
            except Exception:
                pass

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_candidates()
        self._refresh_cycle_chip()

    def _refresh_cycle_chip(self):
        """页头周期指示：本周期 mm/dd – mm/dd（周期设置变化后 showEvent 重算）"""
        try:
            start = datetime.strptime(
                aftersale_db.current_cycle_start(), "%Y/%m/%d")
            end = start + timedelta(
                days=aftersale_db.cycle_span_days() - 1)
            self._cycle_chip.setText("本周期 %s – %s · 按发生时间自动归属" % (
                start.strftime("%m/%d"), end.strftime("%m/%d")))
        except Exception:
            self._cycle_chip.setText("按发生时间自动归属")

    def _update_required_progress(self, *_a):
        """必填进度：n/5 实时刷新；填齐变绿，缺项琥珀色点名"""
        bad = [name for name, is_bad, _l, _c in self.form._required_map()
               if is_bad]
        done = 5 - len(bad)
        self._prog.setValue(done)
        if not bad:
            self._prog_text.setText("必填项 5/5 已填齐")
            self._prog_text.setStyleSheet("color: %s;" % SEMANTIC["success"])
            self._prog.setStyleSheet(
                "QProgressBar { background: %s; border: none;"
                " border-radius: 2px; } QProgressBar::chunk {"
                " background: %s; border-radius: 2px; }" % (
                    _hex_rgba(SEMANTIC["success"], 18),
                    SEMANTIC["success"]))
        else:
            self._prog_text.setText("还差 %d 项：%s" % (
                len(bad), "、".join(bad)))
            self._prog_text.setStyleSheet("color: %s;" % SEMANTIC["warning"])
            self._prog.setStyleSheet(
                "QProgressBar { background: %s; border: none;"
                " border-radius: 2px; } QProgressBar::chunk {"
                " background: %s; border-radius: 2px; }" % (
                    _hex_rgba(SEMANTIC["warning"], 18),
                    SEMANTIC["warning"]))

    def _on_clear(self):
        self.form.clear_form()
        self._update_required_progress()

    def _refresh_candidates(self):
        """进入页面刷新动态候选（问题/解决人/地区）"""
        self._cand_worker = AftersaleDBWorker(aftersale_db.get_field_candidates)
        self._cand_worker.result_ready.connect(self.form.load_candidates)
        self._cand_worker.error.connect(lambda _m: None)
        self._cand_worker.start()

    def _on_submit(self):
        """提交：必填校验（失败则字段级红框+滚动聚焦）→ 后台写库 → 清表单"""
        missing = self.form.validate()
        if missing:
            show_info_bar(f"请先填写必填项: {'、'.join(missing)}", "warning",
                          title="无法提交", parent=self, duration=3000)
            if self.form.first_error is not None:
                self.form.first_error.setFocus()
                self._scroll.ensureWidgetVisible(
                    self.form.first_error, 0, 90)
            return
        if self._save_worker and self._save_worker.isRunning():
            return
        self._btn_submit.setEnabled(False)
        record = self.form.collect()
        self._save_worker = AftersaleDBWorker(aftersale_db.insert_record, record)
        self._save_worker.result_ready.connect(self._on_saved)
        self._save_worker.error.connect(self._on_save_error)
        self._save_worker.start()

    def _on_saved(self, rec_id):
        self._btn_submit.setEnabled(True)
        self.form.clear_form()
        self._update_required_progress()
        show_info_bar(f"售后记录已提交（编号 {rec_id}）", "success",
                      title="提交成功", parent=self, duration=2500)
        # 通知窗口刷新记录页（若已构建）
        win = self.window()
        page = getattr(win, "records_page", None)
        if page is not None:
            page.refresh_async()

    def _on_save_error(self, msg):
        self._btn_submit.setEnabled(True)
        show_info_bar(msg, "error", title="提交失败", parent=self, duration=4000)


# ==================== 编辑弹窗 ====================

class EditRecordDialog(MessageBoxBase):
    """编辑售后记录弹窗：复用共享表单，确认后由调用方异步落库"""

    def __init__(self, record: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑售后记录")
        self._record = record
        self.form = AftersaleForm(self)
        self.form.set_values(record)
        self.viewLayout.addWidget(self.form)
        self.yesButton.setText("保存")
        self.yesButton.clicked.connect(self._on_yes)
        self.cancelButton.setText("取消")
        # 弹窗宽度：表单控件较宽
        self.widget.setMinimumWidth(560)

    def _on_yes(self):
        """保存前校验必填；不通过则阻止关闭"""
        missing = self.form.validate()
        if missing:
            show_info_bar(f"请先填写必填项: {'、'.join(missing)}", "warning",
                          title="无法保存", parent=self, duration=3000)
            # MessageBoxBase 的 yesButton 默认触发 accept，这里用重新校验拦截：
            # 校验失败时把结果标记到属性上，由 exec 返回值区分
            self._validation_ok = False
            return
        self._validation_ok = True
        self.collected = self.form.collect()

    def exec(self):
        self._validation_ok = True
        self.collected = None
        return super().exec()


# ==================== 板块二：记录与统计页 ====================

# 表格展示列（信息整合版：球房+地区+桌号合入「位置」，填写人并入填写时间，
# 解决人并入响应时间；是否解决/是否是我们问题/是否主动发起/发生原因直接成列，
# 完整信息仍保留在行 tooltip）。第 0 列为勾选框（多选/批量操作），不在本定义内。
TABLE_COLUMNS = (
    ("created_at", "填写时间", 150),
    ("occurred_at", "发生时间", 148),
    ("issue_type", "类型", 90),
    ("location", "位置", 200),
    ("problem", "问题", 200),   # Interactive 列，初始宽度 200，可拖拽调宽
    ("cause", "发生原因", 180),
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


class ImportPreviewDialog(QDialog):
    """导入预览：字段要求提示 + 解析效果预览，确认后执行导入"""

    def __init__(self, excel_headers, rows, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导入预览")
        self.resize(1180, 660)
        self.setMinimumSize(900, 480)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        # 字段要求提示
        req = ("类型", "球房", "桌号", "地区", "问题")
        opt = ("发生原因", "是否解决", "解决方案", "解决人", "响应时间")
        missing = [h for h in (*req, *opt) if h not in excel_headers]
        tip = ("表格需要以下列（表头需与列名完全一致）\n"
               f"必填：{'、'.join(req)}\n"
               f"可选：{'、'.join(opt)}")
        if missing:
            tip += f"\n\n⚠ 当前表格缺失列：{'、'.join(missing)}（缺失的可选列将留空，是否解决默认「否」）"
        tip += ("\n自动补充：填写时间=导入时间、填写人=Excel导入、"
                "发生时间=导入当日、周期=按当前周期设置归属")
        lbl = CaptionLabel(tip, self)
        lbl.setWordWrap(True)
        root.addWidget(lbl)

        # 解析效果预览表（前 20 行）
        table = TableWidget(self)
        table.setColumnCount(len(_PREVIEW_COLUMNS))
        table.setHorizontalHeaderLabels([c[1] for c in _PREVIEW_COLUMNS])
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(_FIXED_ROW_HEIGHT)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setWordWrap(False)
        for i, w in enumerate(_PREVIEW_WIDTHS):
            table.setColumnWidth(i, w)
        shown = rows[:20]
        table.setRowCount(len(shown))
        for r, rec in enumerate(shown):
            for c, (key, _h) in enumerate(_PREVIEW_COLUMNS):
                val = str(rec.get(key) or "")
                if key == "cycle_start" and val:
                    val = aftersale_db.cycle_label(val)
                item = QTableWidgetItem(val)
                item.setToolTip(val)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(r, c, item)
        root.addWidget(table, 1)
        self._table = table

        # 底部：统计 + 取消/确认
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        more = "" if len(rows) <= 20 else f"（预览前 20 行）"
        self._lbl_count = CaptionLabel(
            f"共解析 {len(rows)} 条可导入记录{more}", self)
        bottom.addWidget(self._lbl_count)
        bottom.addStretch(1)
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(self.reject)
        bottom.addWidget(btn_cancel)
        btn_ok = PrimaryPushButton(FluentIcon.ACCEPT, "确认导入", self)
        btn_ok.setToolTip("按预览效果批量写入数据库")
        btn_ok.clicked.connect(self.accept)
        bottom.addWidget(btn_ok)
        root.addLayout(bottom)


class RecordsPage(QWidget):
    """记录与统计页：周期概览指标卡 + 筛选/分页/批量操作/一键解决/编辑/删除/导出/导入

    表格列经过信息整合（球房+地区+桌号合入「位置」，填写人并入填写时间，
    解决人并入响应时间）；是否解决/是否是我们问题/是否主动发起/发生原因
    直接成列展示（是/否徽章），完整信息保留在行 tooltip；未解决行提供行内
    一键「标记已解决」。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page_no = 1
        self._page_size = 50
        self._total = 0
        self._rows = []
        self._worker = None
        self._export_worker = None
        self._import_worker = None
        self._cycles_loaded = False
        self._batch_worker = None
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 12)
        root.setSpacing(8)

        # --- 页头：标题 + 说明 + 数据源状态（右对齐） ---
        head = QHBoxLayout()
        head.setSpacing(10)
        head_box = QVBoxLayout()
        head_box.setSpacing(1)
        head_box.addWidget(TitleLabel("记录与统计", self))
        head_box.addWidget(CaptionLabel(
            "售后问题从上报到解决的全流程跟踪", self))
        head.addLayout(head_box)
        head.addStretch(1)
        # 数据源指示：MySQL / 本地 SQLite / 降级兜底
        self._lbl_source = CaptionLabel("", self)
        head.addWidget(self._lbl_source, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(head)

        # --- 周期概览指标卡（四张，随筛选口径实时更新） ---
        ov_head = QHBoxLayout()
        ov_head.setSpacing(8)
        self._lbl_overview = BodyLabel("概览", self)
        ov_head.addWidget(self._lbl_overview)
        ov_head.addStretch(1)
        root.addLayout(ov_head)
        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)
        self._card_total = self._make_stats_card(cards_row)
        self._card_unresolved = self._make_stats_card(cards_row)
        self._card_rate = self._make_stats_card(cards_row)
        self._card_initiative = self._make_stats_card(cards_row)
        root.addLayout(cards_row)

        # --- 工具栏（与主界面同范式：库版 FlowLayout 换行 + 滚动容器锁高，
        #     窄宽自动换行严禁重叠，折行超上限上下滚动） ---
        toolbar_scroll = FlowToolbarScrollArea(self)
        toolbar_scroll.setWidgetResizable(True)
        toolbar_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        toolbar_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        toolbar_scroll.setMaximumHeight(132)  # 约 3 行控件，超出滚动
        toolbar_widget = QWidget(self)
        toolbar_scroll.setWidget(toolbar_widget)
        toolbar = FlowLayout(toolbar_widget)
        toolbar.setHorizontalSpacing(6)
        toolbar.setVerticalSpacing(6)
        toolbar.setContentsMargins(2, 4, 2, 4)

        # 周期筛选（默认当前周期；原生 QComboBox 可靠支持 findData/currentData）
        self._cycle_combo = FluentCombo(self)
        self._cycle_combo.setFixedWidth(210)
        self._cycle_combo.currentIndexChanged.connect(
            lambda _i: self._on_filter_changed())
        toolbar.addWidget(self._cycle_combo)

        # 类型筛选
        self._type_combo = FluentCombo(self)
        self._type_combo.addItem("全部类型")
        self._type_combo.addItems(aftersale_db.ISSUE_TYPES)
        self._type_combo.setFixedWidth(130)
        self._type_combo.currentIndexChanged.connect(
            lambda _i: self._on_filter_changed())
        toolbar.addWidget(self._type_combo)

        # 是否解决筛选
        self._resolved_combo = FluentCombo(self)
        self._resolved_combo.addItem("全部状态", userData="")
        self._resolved_combo.addItem("未解决", userData="否")
        self._resolved_combo.addItem("已解决", userData="是")
        self._resolved_combo.setFixedWidth(130)
        self._resolved_combo.currentIndexChanged.connect(
            lambda _i: self._on_filter_changed())
        toolbar.addWidget(CaptionLabel("是否解决：", self))
        toolbar.addWidget(self._resolved_combo)

        # 是否是我们的问题筛选
        self._our_problem_combo = FluentCombo(self)
        self._our_problem_combo.addItem("全部", userData="")
        self._our_problem_combo.addItem("是", userData="是")
        self._our_problem_combo.addItem("否", userData="否")
        self._our_problem_combo.setToolTip("是否是我们的问题")
        self._our_problem_combo.setFixedWidth(130)
        self._our_problem_combo.currentIndexChanged.connect(
            lambda _i: self._on_filter_changed())
        toolbar.addWidget(CaptionLabel("是否是我们问题：", self))
        toolbar.addWidget(self._our_problem_combo)

        # 是否我们主动发起筛选
        self._initiative_combo = FluentCombo(self)
        self._initiative_combo.addItem("全部", userData="")
        self._initiative_combo.addItem("是", userData="是")
        self._initiative_combo.addItem("否", userData="否")
        self._initiative_combo.setToolTip("是否我们主动发起")
        self._initiative_combo.setFixedWidth(130)
        self._initiative_combo.currentIndexChanged.connect(
            lambda _i: self._on_filter_changed())
        toolbar.addWidget(CaptionLabel("是否主动发起：", self))
        toolbar.addWidget(self._initiative_combo)

        # 关键词搜索（防抖）
        self._search_edit = SearchLineEdit(self)
        self._search_edit.setPlaceholderText("搜索球房 / 问题 / 填写人")
        self._search_edit.setFixedWidth(180)
        self._search_edit.textChanged.connect(self._on_search_input)
        toolbar.addWidget(self._search_edit)

        self._btn_import = PushButton(FluentIcon.ADD, "导入 Excel", self)
        self._btn_import.setToolTip("一次性导入 售后问题汇总 xlsx 历史数据")
        self._btn_import.clicked.connect(self._on_import)
        toolbar.addWidget(self._btn_import)

        self._btn_export = PushButton(FluentIcon.DOWNLOAD, "导出 xlsx", self)
        self._btn_export.setToolTip("按当前筛选条件导出 xlsx（按类型分 Sheet + 统计图表）")
        self._btn_export.clicked.connect(self._on_export)
        toolbar.addWidget(self._btn_export)

        self._btn_refresh = PushButton(FluentIcon.SYNC, "刷新", self)
        self._btn_refresh.setToolTip("重新查询数据库")
        self._btn_refresh.clicked.connect(self._on_refresh)
        toolbar.addWidget(self._btn_refresh)

        root.addWidget(toolbar_scroll)

        # 搜索防抖定时器
        self._search_timer = QTimer(self)
        self._search_timer.setInterval(300)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._on_filter_changed)

        # --- 批量操作条（勾选行后出现：批量标记已解决 / 批量删除） ---
        self._batch_bar = self._make_batch_bar()
        self._batch_bar.setVisible(False)
        root.addWidget(self._batch_bar)

        # --- 表格 ---
        self._table = TableWidget(self)
        # 勾选列 + 数据列（列号：勾选=0，数据列 1..N，注意不要少算勾选列）
        self._table.setColumnCount(_COL_CHECK + 1 + len(TABLE_COLUMNS))
        self._table.setHorizontalHeaderLabels(
            [""] + [c[1] for c in TABLE_COLUMNS])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        # 行高放宽到 40：双行单元格（位置/填写时间/响应）需要两行文字空间
        self._table.verticalHeader().setDefaultSectionSize(40)
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(False)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._table.doubleClicked.connect(lambda _idx: self._on_edit())
        self._table.itemChanged.connect(self._on_item_changed)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        # 其他列保持 Interactive（可拖拽调宽）；最后一列（操作）stretch 拉伸填满
        # 剩余宽度，使表格铺满面板右侧不留白；操作列按钮仍靠左（addStretch 在右）。
        header.setStretchLastSection(True)
        self._table.setColumnWidth(_COL_CHECK, 36)
        for i, (_k, _h, w) in enumerate(TABLE_COLUMNS):
            self._table.setColumnWidth(_COL_CHECK + 1 + i, w)
        root.addWidget(self._table, 1)

        # --- 分页 + 状态栏 ---
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        self._lbl_stats = CaptionLabel("", self)
        bottom.addWidget(self._lbl_stats)
        bottom.addStretch(1)
        self._lbl_cycle = CaptionLabel("", self)
        bottom.addWidget(self._lbl_cycle)
        bottom.addStretch(1)
        self._btn_prev = ToolButton(FluentIcon.LEFT_ARROW, self)
        self._btn_prev.setToolTip("上一页")
        self._btn_prev.clicked.connect(lambda _=False: self._step_page(-1))
        bottom.addWidget(self._btn_prev)
        self._lbl_page = CaptionLabel("1/1", self)
        bottom.addWidget(self._lbl_page)
        self._btn_next = ToolButton(FluentIcon.RIGHT_ARROW, self)
        self._btn_next.setToolTip("下一页")
        self._btn_next.clicked.connect(lambda _=False: self._step_page(1))
        bottom.addWidget(self._btn_next)
        root.addLayout(bottom)

    # ---------- 概览卡与批量条构造 ----------

    def _make_stats_card(self, layout) -> tuple:
        """单张指标卡：标签 + 大数字 + 辅助说明，返回 (card, 数字label, 说明label)"""
        card = CardWidget(self)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        lbl = CaptionLabel("", card)
        num = QLabel("0", card)
        num.setStyleSheet("font-size: 24px; font-weight: 500; background: transparent;")
        sub = CaptionLabel("", card)
        lay.addWidget(lbl)
        lay.addWidget(num)
        lay.addWidget(sub)
        layout.addWidget(card, 1)
        return (card, lbl, num, sub)

    def _make_batch_bar(self) -> QWidget:
        """批量操作条：勾选行后浮现（全选本页 / 批量标记已解决 / 批量删除 / 取消）"""
        bar = QWidget(self)
        bar.setObjectName("batchBar")
        bar.setStyleSheet(
            "QWidget#batchBar { background: "
            f"{_hex_rgba(SEMANTIC['info'], 20)};"
            f" border: 1px solid {_hex_rgba(SEMANTIC['info'], 90)};"
            " border-radius: 6px; }")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(8)
        self._chk_all = CheckBox("全选本页", bar)
        self._chk_all.stateChanged.connect(self._on_toggle_all)
        lay.addWidget(self._chk_all)
        self._lbl_selected = BodyLabel("已选 0 项", bar)
        lay.addWidget(self._lbl_selected)
        btn_resolve = _row_btn(
            "批量标记已解决", "primary", self._on_batch_resolve, bar)
        lay.addWidget(btn_resolve)
        btn_delete = _row_btn("批量删除", "danger", self._on_batch_delete, bar)
        lay.addWidget(btn_delete)
        btn_clear = _row_btn("取消选择", "ghost", self._on_clear_selection, bar)
        lay.addWidget(btn_clear)
        lay.addStretch(1)
        return bar

    @staticmethod
    def _short_dt(val: str) -> str:
        """2026-08-22 21:14:33 → 08-22 21:14（表格内紧凑展示，完整值放 tooltip）"""
        s = str(val or "").strip()
        return s[5:16] if len(s) >= 16 else s

    # ---------- 筛选条件 ----------

    def _current_filters(self) -> dict:
        """汇集当前筛选条件（周期/类型/状态/是否我们主动发起/是否我们的问题/关键词）"""
        cycle = self._cycle_combo.currentData()
        return {
            "cycle_start": str(cycle or ""),
            "issue_type": (self._type_combo.currentText()
                           if self._type_combo.currentIndex() > 0 else ""),
            "resolved": self._resolved_combo.currentData() or "",
            "is_initiative": self._initiative_combo.currentData() or "",
            "is_our_problem": self._our_problem_combo.currentData() or "",
            "keyword": self._search_edit.text().strip(),
        }

    def _on_search_input(self, _text):
        self._search_timer.start()

    def _on_filter_changed(self):
        self._page_no = 1
        self._load()

    def _update_source_label(self):
        """刷新数据源指示标签（读取时调用，反映最新后端状态）

        - MySQL 开启且可用：绿色「数据源: MySQL」
        - MySQL 未开启：灰色「数据源: 本地 SQLite」
        - MySQL 开启但不可用（降级兜底）：橙色「本地 SQLite（MySQL 不可用，降级兜底）」
        """
        from database import backend
        if not backend.is_mysql_test_mode():
            text, color = "数据源: 本地 SQLite", SEMANTIC["neutral"]
        elif backend.get_state() == backend.STATE_ONLINE:
            text, color = "数据源: MySQL", SEMANTIC["success"]
        else:
            text, color = ("数据源: 本地 SQLite（MySQL 不可用，降级兜底）",
                           SEMANTIC["warning"])
        self._lbl_source.setText(text)
        self._lbl_source.setStyleSheet(f"color: {color};")
        self._lbl_source.setToolTip(
            "关闭 MySQL 后自动读写本地 SQLite；"
            "MySQL 恢复可用时会自动切回并合并兜底增量")

    def _on_refresh(self):
        """手动刷新：重建周期选项并重查（多人协作看到他人新数据）"""
        self._cycles_loaded = False
        self._load_cycles_then_data()

    def refresh_async(self):
        """其他页面提交后静默刷新（不重置周期选择）"""
        self._load()

    def set_keyword(self, kw: str):
        """外部入口：按桌号预筛选（球桌管理右键跳转）"""
        self._search_edit.setText(str(kw or ""))
        # 跨球桌查询时周期放宽为全部，避免当前周期过滤掉历史记录
        idx = self._cycle_combo.findData("")
        if idx >= 0:
            self._cycle_combo.setCurrentIndex(idx)

    # ---------- 数据加载 ----------

    def showEvent(self, event):
        super().showEvent(event)
        if not self._cycles_loaded:
            self._load_cycles_then_data()

    def _load_cycles_then_data(self):
        """先异步拉周期选项填充下拉，再加载数据"""
        self._worker = AftersaleDBWorker(aftersale_db.get_cycle_options)
        self._worker.result_ready.connect(self._on_cycles_loaded)
        self._worker.error.connect(lambda _m: self._load())
        self._worker.start()

    def _on_cycles_loaded(self, cycle_starts):
        self._cycles_loaded = True
        prev = self._cycle_combo.currentData()
        self._cycle_combo.blockSignals(True)
        self._cycle_combo.clear()
        current = aftersale_db.current_cycle_start()
        cycles = list(cycle_starts or [])
        # 当前周期仅在库中确实存在该周期数据时才出现（不额外新建库中不存在的周期）
        if current in cycles:
            self._cycle_combo.addItem(
                f"当前周期 {aftersale_db.cycle_label(current)}", userData=current)
            cycles.remove(current)
        self._cycle_combo.addItem("全部周期", userData="")
        for cs in cycles:
            self._cycle_combo.addItem(
                aftersale_db.cycle_label(cs), userData=cs)
        # 恢复之前的选择；默认当前周期（库中有数据）否则全部周期
        idx = self._cycle_combo.findData(prev if prev is not None else current)
        self._cycle_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._cycle_combo.blockSignals(False)
        self._load()

    def _load(self):
        """按当前筛选异步查询（分页数据 + 统计一次返回）"""
        if self._worker and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.disconnect(self)
        f = self._current_filters()
        self._worker = AftersaleDBWorker(
            aftersale_db.query_with_stats,
            self._page_no, self._page_size,
            keyword=f["keyword"], cycle_start=f["cycle_start"],
            issue_type=f["issue_type"], resolved=f["resolved"],
            is_initiative=f["is_initiative"],
            is_our_problem=f["is_our_problem"])
        self._worker.result_ready.connect(self._on_loaded)
        self._worker.error.connect(self._on_load_error)
        self._worker.start()

    def _on_loaded(self, result):
        total, rows, stats = result
        self._total = total
        self._rows = rows
        self._populate(rows)
        self._update_pager()
        self._update_source_label()
        self._update_overview(stats)
        cycle = self._current_filters().get("cycle_start") or ""
        if cycle:
            self._lbl_cycle.setText(f"周期: {aftersale_db.cycle_label(cycle)}")
            self._lbl_overview.setText(
                f"概览 · {aftersale_db.cycle_label(cycle)}")
        else:
            self._lbl_cycle.setText("周期: 全部")
            self._lbl_overview.setText("概览 · 全部周期")

    def _on_load_error(self, msg):
        self._lbl_stats.setText(f"查询失败: {msg}")
        self._update_source_label()

    # ---------- 概览指标卡 ----------

    def _update_overview(self, stats: dict):
        """统计 → 四张指标卡（本周期记录/未解决/已解决率/主动发起）"""
        total = int(stats.get("total") or 0)
        unresolved = int(stats.get("unresolved") or 0)
        rate = int(stats.get("rate") or 0)
        initiative = int(stats.get("initiative") or 0)

        _card, _lbl, num, sub = self._card_total
        num.setText(str(total))
        num.setStyleSheet("font-size: 24px; font-weight: 500; background: transparent;")
        sub.setText(f"已解决 {stats.get('resolved', 0)} 条")

        _card, _lbl, num, sub = self._card_unresolved
        num.setText(str(unresolved))
        # 未解决为 0 时用常规字色，有积压时用危险红提醒
        color = SEMANTIC["danger"] if unresolved > 0 else ""
        base = "font-size: 24px; font-weight: 500; background: transparent;"
        num.setStyleSheet(base + (f" color: {color};" if color else ""))
        sub.setText("待处理" if unresolved else "无积压")

        _card, _lbl, num, sub = self._card_rate
        num.setText(f"{rate}%")
        num.setStyleSheet(base + (f" color: {SEMANTIC['success']};" if rate >= 90 else ""))
        sub.setText("已解决率")

        _card, _lbl, num, sub = self._card_initiative
        init_rate = int(round(initiative * 100 / total)) if total else 0
        num.setText(str(initiative))
        num.setStyleSheet(base)
        sub.setText(f"主动发起 · 占 {init_rate}%")

        self._lbl_stats.setText(f"共 {total} 条")

    # ---------- 表格渲染 ----------

    def _row_tooltip(self, rec: dict) -> str:
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

    def _populate(self, rows):
        """行数据 → 表格：勾选列 + 双行信息整合列 + 是/否徽章三列 + 行内操作按钮"""
        self._table.setUpdatesEnabled(False)
        self._table.blockSignals(True)
        try:
            self._table.setRowCount(len(rows))
            for r, item in enumerate(rows):
                tip = self._row_tooltip(item)

                # 勾选列（供批量操作）
                chk = QTableWidgetItem()
                chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable
                             | Qt.ItemFlag.ItemIsEnabled)
                chk.setCheckState(Qt.CheckState.Unchecked)
                self._table.setItem(r, _COL_CHECK, chk)

                for i, (key, _h, _w) in enumerate(TABLE_COLUMNS):
                    col = _COL_CHECK + 1 + i
                    if key == "created_at":
                        cell = QTableWidgetItem(
                            f"{self._short_dt(item.get('created_at'))}\n"
                            f"{str(item.get('creator') or '')}")
                        f = QFont(cell.font())
                        f.setPointSizeF(10.5)
                        cell.setFont(f)
                    elif key == "location":
                        cell = QTableWidgetItem(
                            f"{str(item.get('room_name') or '')}\n"
                            f"{str(item.get('region') or '')} · "
                            f"{str(item.get('table_no') or '')}")
                        f = QFont(cell.font())
                        f.setPointSizeF(10.5)
                        cell.setFont(f)
                    elif key == "response_time":
                        cell = QTableWidgetItem(
                            f"{str(item.get('response_time') or '—')}\n"
                            f"{str(item.get('resolver') or '')}")
                        f = QFont(cell.font())
                        f.setPointSizeF(10.5)
                        cell.setFont(f)
                    elif key in _YES_NO_COLORS:
                        # 是/否徽章（setCellWidget 承载，该格不设 item）
                        is_yes = str(item.get(key) or "") == "是"
                        yes_c, no_c = _YES_NO_COLORS[key]
                        badge = _badge_label(
                            "是" if is_yes else "否",
                            yes_c if is_yes else no_c,
                            self._table.viewport())
                        self._table.setCellWidget(r, col, badge)
                        continue
                    elif key == "ops":
                        self._table.setCellWidget(
                            r, col, self._make_ops_cell(r, item))
                        continue
                    else:
                        val = str(item.get(key) or "")
                        cell = QTableWidgetItem(val)
                    cell.setToolTip(tip)
                    cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self._table.setItem(r, col, cell)
        finally:
            self._table.setUpdatesEnabled(True)
            self._table.blockSignals(False)
        self._sync_batch_bar()

    def _make_ops_cell(self, row: int, rec: dict) -> QWidget:
        """操作列容器：未解决行 = [已解决 + 编辑 + 删除]，已解决行 = [编辑 + 删除]（删除常驻）"""
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(4)
        is_yes = str(rec.get("resolved") or "") == "是"
        if not is_yes:
            lay.addWidget(_row_btn(
                "已解决", "primary",
                lambda: self._quick_resolve(row), wrap))
        lay.addWidget(_row_btn("编辑", "ghost", lambda: self._on_edit(row), wrap))
        lay.addWidget(_row_btn(
            "删除", "danger", lambda: self._on_delete(row), wrap))
        lay.addStretch(1)
        return wrap

    # ---------- 勾选与批量操作 ----------

    def _checked_ids(self) -> list:
        """当前勾选行的记录 id 列表"""
        ids = []
        for r in range(self._table.rowCount()):
            it = self._table.item(r, _COL_CHECK)
            if it is not None and it.checkState() == Qt.CheckState.Checked:
                if r < len(self._rows):
                    ids.append(self._rows[r].get("id"))
        return ids

    def _on_item_changed(self, item: QTableWidgetItem):
        """勾选列变动 → 刷新批量条计数（程序化改勾选也经此，幂等安全）"""
        if item.column() == _COL_CHECK:
            self._sync_batch_bar()

    def _sync_batch_bar(self):
        """按勾选数显示/隐藏批量操作条，更新计数与全选状态"""
        n = len(self._checked_ids())
        self._batch_bar.setVisible(n > 0)
        self._lbl_selected.setText(f"已选 {n} 项")
        total_rows = self._table.rowCount()
        self._chk_all.blockSignals(True)
        if total_rows and n == total_rows:
            self._chk_all.setCheckState(Qt.CheckState.Checked)
        else:
            self._chk_all.setCheckState(Qt.CheckState.Unchecked)
        self._chk_all.blockSignals(False)

    def _on_toggle_all(self, state):
        """全选/取消全选本页

        根因修复（两处）：
        1. Qt.CheckState 在本环境是普通 enum.Enum（非 IntEnum），stateChanged 信号
           发出的是 int；旧写法 `state == Qt.CheckState.Checked` 恒为 False，
           导致「全选本页」永远把行勾选置为未勾选。这里统一取 .value 比较。
        2. 循环前 blockSignals 避免每行 setCheckState 同步触发 itemChanged →
           _sync_batch_bar 把 _chk_all 视觉状态弹回 Unchecked（blockSignals 只挡
           信号不挡视觉）的竞态；循环结束后统一同步一次。
        """
        want = (getattr(state, "value", state) == Qt.CheckState.Checked.value)
        new_state = (Qt.CheckState.Checked if want
                     else Qt.CheckState.Unchecked)
        self._table.blockSignals(True)
        try:
            for r in range(self._table.rowCount()):
                it = self._table.item(r, _COL_CHECK)
                if it is not None:
                    it.setCheckState(new_state)
        finally:
            self._table.blockSignals(False)
        self._sync_batch_bar()

    def _on_clear_selection(self):
        self._on_toggle_all(Qt.CheckState.Unchecked)

    def _on_batch_resolve(self):
        """批量标记已解决（最小化更新，仅改 resolved）"""
        ids = self._checked_ids()
        if not ids:
            return
        if self._batch_worker and self._batch_worker.isRunning():
            return
        if not MessageBox("批量标记已解决",
                          f"确定将选中的 {len(ids)} 条记录标记为已解决？",
                          self.window()).exec():
            return
        self._batch_worker = AftersaleDBWorker(
            aftersale_db.mark_resolved_batch, ids)
        self._batch_worker.result_ready.connect(
            lambda n: (show_info_bar(f"已标记 {n} 条为已解决", "success",
                                     title="批量操作", parent=self, duration=2500),
                       self._load()))
        self._batch_worker.error.connect(
            lambda m: show_info_bar(m, "error", title="批量操作失败",
                                    parent=self, duration=4000))
        self._batch_worker.start()

    def _on_batch_delete(self):
        """批量删除（确认后走 delete_records）"""
        ids = self._checked_ids()
        if not ids:
            return
        if self._batch_worker and self._batch_worker.isRunning():
            return
        if not MessageBox("批量删除",
                          f"确定删除选中的 {len(ids)} 条售后记录？\n删除后不可恢复",
                          self.window()).exec():
            return
        self._batch_worker = AftersaleDBWorker(
            aftersale_db.delete_records, ids)
        self._batch_worker.result_ready.connect(
            lambda n: (show_info_bar(f"已删除 {n} 条记录", "success",
                                     title="批量操作", parent=self, duration=2500),
                       self._load()))
        self._batch_worker.error.connect(
            lambda m: show_info_bar(m, "error", title="批量操作失败",
                                    parent=self, duration=4000))
        self._batch_worker.start()

    # ---------- 行内快捷操作 ----------

    def _quick_resolve(self, row: int):
        """一键标记已解决：最小化更新（不弹编辑窗），3 步变 1 步"""
        if row >= len(self._rows):
            return
        if self._batch_worker and self._batch_worker.isRunning():
            return
        rec = self._rows[row]
        self._batch_worker = AftersaleDBWorker(
            aftersale_db.mark_resolved_batch, [rec.get("id")])
        self._batch_worker.result_ready.connect(
            lambda _n: (show_info_bar("记录已标记为已解决", "success",
                                      title="操作成功", parent=self, duration=2000),
                        self._load()))
        self._batch_worker.error.connect(
            lambda m: show_info_bar(m, "error", title="操作失败",
                                    parent=self, duration=4000))
        self._batch_worker.start()

    def _update_pager(self):
        pages = max(1, (self._total + self._page_size - 1) // self._page_size)
        self._page_no = min(self._page_no, pages)
        self._lbl_page.setText(f"{self._page_no}/{pages} · 共 {self._total} 条")
        self._btn_prev.setEnabled(self._page_no > 1)
        self._btn_next.setEnabled(self._page_no < pages)

    def _step_page(self, delta):
        self._page_no = max(1, self._page_no + delta)
        self._load()

    # ---------- 右键菜单与编辑 ----------

    def _selected_row(self):
        rows = sorted({i.row() for i in self._table.selectedIndexes()})
        if rows and rows[0] < len(self._rows):
            return rows[0]
        return -1

    def _show_context_menu(self, pos):
        idx = self._table.indexAt(pos)
        if not idx.isValid():
            return
        self._table.selectRow(idx.row())
        menu = RoundMenu(parent=self._table)
        act_edit = Action(FluentIcon.EDIT, "编辑", self._table)
        act_edit.triggered.connect(
            lambda _=False: self._on_edit(idx.row()))
        menu.addAction(act_edit)
        rec = self._rows[idx.row()] if idx.row() < len(self._rows) else {}
        if str(rec.get("resolved") or "") != "是":
            act_done = Action(FluentIcon.ACCEPT, "标记已解决", self._table)
            act_done.triggered.connect(
                lambda _=False, r=idx.row(): self._quick_resolve(r))
            menu.addAction(act_done)
        act_del = Action(FluentIcon.DELETE, "删除", self._table)
        act_del.triggered.connect(
            lambda _=False: self._on_delete(idx.row()))
        menu.addAction(act_del)
        menu.exec_(self._table.viewport().mapToGlobal(pos),
                   aniType=_popup_ani_type())

    def _on_edit(self, row: int = -1):
        if row < 0:
            row = self._selected_row()
        if row < 0 or row >= len(self._rows):
            return
        rec = dict(self._rows[row])
        dlg = EditRecordDialog(rec, self)
        # 编辑弹窗也需动态候选
        cand_worker = AftersaleDBWorker(aftersale_db.get_field_candidates)
        cand_worker.result_ready.connect(dlg.form.load_candidates)
        cand_worker.start()
        self._edit_cand_worker = cand_worker  # 保活引用
        if dlg.exec() and getattr(dlg, "collected", None):
            collected = dlg.collected
            collected["id"] = rec.get("id")
            collected["created_at"] = rec.get("created_at")  # 保留原填写时间
            self._run_update(collected)

    def _run_update(self, record):
        self._worker = AftersaleDBWorker(aftersale_db.update_record, record)
        self._worker.result_ready.connect(
            lambda _n: (show_info_bar("记录已更新", "success",
                                      title="保存成功", parent=self, duration=2000),
                        self._load()))
        self._worker.error.connect(
            lambda m: show_info_bar(m, "error", title="保存失败",
                                    parent=self, duration=4000))
        self._worker.start()

    def _on_delete(self, row: int = -1):
        if row < 0:
            row = self._selected_row()
        if row < 0 or row >= len(self._rows):
            return
        rec = self._rows[row]
        desc = f"{rec.get('table_no') or ''} · {rec.get('problem') or ''}"
        is_yes = str(rec.get("resolved") or "") == "是"
        msg = f"确定删除售后记录「{desc}」吗？\n删除后不可恢复。"
        if is_yes:
            msg += "\n该记录已标记为解决，删除后将无法恢复。"
        dlg = MessageBox("删除售后记录", msg, self)
        dlg.yesButton.setText("删除")
        dlg.cancelButton.setText("取消")
        if not dlg.exec():
            return
        self._worker = AftersaleDBWorker(aftersale_db.delete_record, rec.get("id"))
        self._worker.result_ready.connect(
            lambda _n: (show_info_bar("记录已删除", "success",
                                      title="删除成功", parent=self, duration=2000),
                        self._load()))
        self._worker.error.connect(
            lambda m: show_info_bar(m, "error", title="删除失败",
                                    parent=self, duration=4000))
        self._worker.start()

    # ---------- 导出 / 导入 ----------

    def _on_export(self):
        if self._export_worker and self._export_worker.isRunning():
            return
        f = self._current_filters()
        default_name = "售后记录_" + datetime.now().strftime("%Y%m%d_%H%M") + ".xlsx"
        path, _sel = QFileDialog.getSaveFileName(
            self, "导出 xlsx", default_name, "Excel 文件 (*.xlsx)")
        if not path:
            return
        self._btn_export.setEnabled(False)
        self._export_worker = AftersaleDBWorker(
            aftersale_db.export_xlsx, path,
            keyword=f["keyword"], cycle_start=f["cycle_start"],
            issue_type=f["issue_type"], resolved=f["resolved"],
            is_initiative=f["is_initiative"],
            is_our_problem=f["is_our_problem"])
        self._export_worker.result_ready.connect(self._on_export_done)
        self._export_worker.error.connect(self._on_export_error)
        self._export_worker.start()

    def _on_export_done(self, count):
        self._btn_export.setEnabled(True)
        show_info_bar(f"已导出 {count} 条记录", "success",
                      title="导出成功", parent=self, duration=3000)

    def _on_export_error(self, msg):
        self._btn_export.setEnabled(True)
        show_info_bar(msg, "error", title="导出失败", parent=self, duration=4000)

    def _on_import(self):
        if self._import_worker and self._import_worker.isRunning():
            return
        path, _sel = QFileDialog.getOpenFileName(
            self, "选择售后汇总 Excel", "", "Excel 文件 (*.xlsx)")
        if not path:
            return
        # 第一步：后台解析（不写库），成功后弹预览确认
        self._btn_import.setEnabled(False)
        self._preview_path = path
        self._import_worker = AftersaleDBWorker(aftersale_db.parse_excel_rows, path)
        self._import_worker.result_ready.connect(self._on_import_preview_ready)
        self._import_worker.error.connect(self._on_import_error)
        self._import_worker.start()

    def _on_import_preview_ready(self, result):
        """解析完成：弹预览对话框，确认后真正写库"""
        self._btn_import.setEnabled(True)
        excel_headers, rows = result
        dlg = ImportPreviewDialog(excel_headers, rows, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._btn_import.setEnabled(False)
        self._import_worker = AftersaleDBWorker(
            aftersale_db.import_excel_rows, self._preview_path)
        self._import_worker.result_ready.connect(self._on_import_done)
        self._import_worker.error.connect(self._on_import_error)
        self._import_worker.start()

    def _on_import_done(self, count):
        self._btn_import.setEnabled(True)
        show_info_bar(f"已导入 {count} 条历史记录", "success",
                      title="导入成功", parent=self, duration=3000)
        self._cycles_loaded = False
        self._load_cycles_then_data()

    def _on_import_error(self, msg):
        self._btn_import.setEnabled(True)
        show_info_bar(msg, "error", title="导入失败", parent=self, duration=4000)


# ==================== 周期设置页 ====================

class CycleSettingsPage(QWidget):
    """周期设置卡片：统计周期模式（周二起默认 / 自然周 / 自定义起始日+天数），保存即生效

    saved 信号：保存成功后发出，供设置面板通知记录页刷新周期下拉
    """

    saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def showEvent(self, event):
        """卡片每次显示时回显最新配置（不依赖导航信号，Qt 原生事件更稳）"""
        super().showEvent(event)
        self.load()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)  # 作为设置面板卡片嵌入，外边距由面板控制

        card = CardWidget(self)
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(10)
        vbox.addWidget(BodyLabel("统计周期设置", card))
        vbox.addWidget(CaptionLabel(
            "周期决定售后记录按发生时间归属的统计区间（周二起周一止为原默认）；"
            "保存后立即生效，历史记录的归属按新规则重新计算，无需手工修改", card))

        # 周期模式单选
        self._rb_tue = RadioButton("周二 ~ 周一（原默认，每周二开始）", card)
        self._rb_mon = RadioButton("周一 ~ 周日（自然周）", card)
        self._rb_custom = RadioButton("自定义（指定起始日与周期天数）", card)
        self._rb_tue.setChecked(True)
        for rb in (self._rb_tue, self._rb_mon, self._rb_custom):
            vbox.addWidget(rb)
        self._rb_custom.toggled.connect(self._on_custom_toggled)

        # 自定义参数区（仅自定义模式显示）
        custom_wrap = QWidget(card)
        custom_lay = QHBoxLayout(custom_wrap)
        custom_lay.setContentsMargins(0, 0, 0, 0)
        custom_lay.setSpacing(8)
        custom_lay.addWidget(QLabel("起始日:", custom_wrap))
        self._start_picker = ZhDatePicker(custom_wrap)
        self._start_picker.setFixedWidth(150)
        custom_lay.addWidget(self._start_picker)
        custom_lay.addSpacing(16)
        custom_lay.addWidget(QLabel("周期天数:", custom_wrap))
        self._span_spin = SpinBox(custom_wrap)
        self._span_spin.setRange(1, 365)
        self._span_spin.setValue(7)
        self._span_spin.setFixedWidth(100)
        custom_lay.addWidget(self._span_spin)
        custom_lay.addStretch(1)
        vbox.addWidget(custom_wrap)
        self._custom_wrap = custom_wrap
        self._custom_wrap.setVisible(False)

        # 保存按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_save = PrimaryPushButton(FluentIcon.ACCEPT, "保存", card)
        self._btn_save.setToolTip("保存后立即生效：列表/统计/周期下拉按新规则重新归属")
        self._btn_save.setFixedHeight(36)
        self._btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self._btn_save)
        vbox.addLayout(btn_row)

        root.addWidget(card)
        root.addStretch(1)

    def _on_custom_toggled(self, checked: bool):
        """自定义模式切换：显示/隐藏起始日与周期天数参数区"""
        self._custom_wrap.setVisible(checked)

    def load(self):
        """回显当前周期配置（外部改动后进入页面刷新最新值）"""
        cfg = aftersale_db.load_cycle_mode()
        mode = cfg.get("type", "tue")
        self._rb_tue.setChecked(mode == "tue")
        self._rb_mon.setChecked(mode == "mon")
        self._rb_custom.setChecked(mode == "custom")
        start = str(cfg.get("start") or "")
        d = QDate.fromString(start, "yyyy-MM-dd")
        if d.isValid():
            self._start_picker.setDate(d)
        self._span_spin.setValue(int(cfg.get("span") or 7))
        self._custom_wrap.setVisible(mode == "custom")

    def _on_save(self):
        """保存周期设置：写 settings.json（合并写保留其他字段）并即时生效"""
        if self._rb_mon.isChecked():
            mode = "mon"
        elif self._rb_custom.isChecked():
            mode = "custom"
        else:
            mode = "tue"
        aftersale_db.save_cycle_mode({
            "type": mode,
            "start": self._start_picker.date.toString("yyyy-MM-dd"),
            "span": self._span_spin.value(),
        })
        show_info_bar("周期设置已保存，列表/统计已按新周期重新归属",
                      "success", title="周期设置", parent=self, duration=3000)
        self.saved.emit()  # 通知记录页刷新周期下拉与统计


# ==================== 设置面板（周期 + 数据库） ====================

class SettingsPage(QWidget):
    """设置面板：统计周期设置 + 数据库设置（原 MySQL 设置更名）

    周期设置保存后通过 cycle_page.saved 信号通知记录页刷新周期下拉；
    数据库卡片复用 MysqlSyncCard（仅推售后记录）。
    整体包在 ScrollArea 里：小分辨率下设置项可滚动查看，不再被裁剪。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        content = QWidget(self)
        cl = QVBoxLayout(content)
        cl.setContentsMargins(24, 20, 24, 20)
        cl.setSpacing(14)

        # 周期设置卡片
        self.cycle_page = CycleSettingsPage(content)
        cl.addWidget(self.cycle_page)

        # 数据库设置卡片（原 MySQL 设置，仅同步售后记录）
        self.mysql_card = MysqlSyncCard(content, sync_scope="aftersale")
        self.mysql_card.load()
        cl.addWidget(self.mysql_card)
        cl.addStretch(1)

        scroll = ScrollArea(self)
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }")
        root.addWidget(scroll)

    def showEvent(self, event):
        """进入设置面板回显最新配置（导航信号部分版本缺失，用 Qt 原生事件兜底）"""
        super().showEvent(event)
        self.mysql_card.load()


# ==================== 售后面板窗口 ====================

class AftersalePanelWindow(FluentWindow):
    """售后面板：FluentWindow + 左侧导航 + 三个功能页面（填写录入/记录与统计/设置）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("售后面板")
        self.resize(1920, 1080)
        self.setMinimumSize(900, 520)

        self.entry_page = EntryPage(self)
        self.entry_page.setObjectName("aftersaleEntryPage")
        self.records_page = RecordsPage(self)
        self.records_page.setObjectName("aftersaleRecordsPage")

        self.addSubInterface(self.entry_page, FluentIcon.EDIT, "填写录入")
        self.addSubInterface(self.records_page, FluentIcon.LIBRARY, "记录与统计")

        # 设置面板：统计周期设置 + 数据库设置（原 MySQL 设置）
        self.settings_page = SettingsPage(self)
        self.settings_page.setObjectName("aftersaleSettingsPage")
        self.addSubInterface(self.settings_page, FluentIcon.SETTING, "设置")
        # 周期设置保存后，记录页周期下拉与统计立即按新周期刷新
        self.settings_page.cycle_page.saved.connect(self._on_cycle_saved)

        self.navigationInterface.setAcrylicEnabled(is_acrylic_enabled())
        self.navigationInterface.setCurrentItem(self.entry_page.objectName())
        try:
            self.navigationInterface.currentItemChanged.connect(
                self._on_nav_changed)
        except Exception:
            pass

    def _on_cycle_saved(self):
        """周期设置保存成功：记录页重建周期下拉并重查（新周期立即生效）"""
        self.records_page._cycles_loaded = False
        self.records_page._load_cycles_then_data()

    def _on_nav_changed(self, current, _pre=None):
        """导航切换：进入设置面板时刷新数据库配置表单（showEvent 已兜底，此处兼容旧信号）"""
        obj = getattr(current, "routeKey", None)
        if obj == "aftersaleSettingsPage" and getattr(self, "settings_page", None):
            self.settings_page.mysql_card.load()

    def showEvent(self, event):
        super().showEvent(event)
        self._reset_titlebar_button_state()

    def _reset_titlebar_button_state(self):
        """重置标题栏按钮状态（同管理面板：修复关闭按钮卡 PRESSED 导致无法拖动）"""
        try:
            from qframelesswindow.titlebar.title_bar_buttons import (
                TitleBarButton, TitleBarButtonState)
            for btn in self.titleBar.findChildren(TitleBarButton):
                if btn.isPressed():
                    btn.setState(TitleBarButtonState.NORMAL)
        except Exception:
            pass

    def open_records_for_table(self, table_no: str):
        """球桌管理右键入口：跳转记录页并按桌号预筛选"""
        self.records_page.set_keyword(table_no)
        self.switchTo(self.records_page)
        self.records_page.refresh_async()

    def closeEvent(self, event):
        """关闭窗口快速清理 Worker（同管理面板策略：disconnect + 200ms 短等）"""
        def _detach(w):
            if w is None:
                return
            try:
                w.disconnect(self)
            except Exception:
                pass
            try:
                w.requestInterruption()
            except Exception:
                pass
        for page in (self.entry_page, self.records_page):
            for attr in dir(page):
                if attr.endswith("_worker"):
                    _detach(getattr(page, attr, None))
        QThread.msleep(200)
        super().closeEvent(event)


# ==================== 独立运行入口（调试/单独打包用） ====================

if __name__ == "__main__":
    import sys
    import json
    import core.acrylic_patch  # noqa: F401
    from PySide6.QtWidgets import QApplication
    from qfluentwidgets import setTheme, setThemeColor, Theme

    def _debug_theme():
        """读取 settings.json 主题配置（打包后从 exe 旁读取，与主程序入口一致）"""
        try:
            from core.app_paths import get_app_dir
            p = os.path.join(get_app_dir(), "settings.json")
            with open(p, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return (Theme.DARK if cfg.get("dark_theme") else Theme.LIGHT,
                    cfg.get("theme_color", "#00BCD4"))
        except Exception:
            return Theme.LIGHT, "#00BCD4"

    # 支持 --table=桌号 参数：主程序/运维面板拉起独立进程时按桌号预筛选
    _table_arg = next((a.split("=", 1)[1] for a in sys.argv[1:]
                       if a.startswith("--table=")), "")

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    theme, color = _debug_theme()
    setTheme(theme)
    setThemeColor(color, lazy=True)
    win = AftersalePanelWindow()
    if _table_arg:
        win.open_records_for_table(_table_arg)
    win.show()
    sys.exit(app.exec())
