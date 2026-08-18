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
from datetime import datetime

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QLabel, QListWidget,
    QListWidgetItem, QFileDialog, QComboBox as _QComboBox, QApplication)
from PySide6.QtCore import Qt, QTimer, QThread, QPointF
from PySide6.QtGui import QColor, QBrush, QPainter, QPen
from qfluentwidgets import (TableWidget, SearchLineEdit, PushButton,
    PrimaryPushButton, ToolButton, FluentIcon, RoundMenu, Action,
    LineEdit, PlainTextEdit, BodyLabel, CaptionLabel, TitleLabel,
    ScrollArea, CardWidget, MessageBox, MessageBoxBase, FluentWindow,
    NavigationItemPosition, MenuAnimationType, setCustomStyleSheet,
    qconfig, isDarkTheme)

from core.perf import is_acrylic_enabled
from core.utils import show_info_bar
from database import aftersale_db, table_db
from workers.aftersale_worker import AftersaleDBWorker

# 表格固定行高（与管理面板一致，避免默认行高浪费纵向空间）
_FIXED_ROW_HEIGHT = 32


def _popup_ani_type():
    """弹出菜单动画类型：关闭动画选项时用 NO_ANIMATION（性能开关联动）"""
    from core.perf import is_animation_enabled
    return (MenuAnimationType.DROP_DOWN if is_animation_enabled()
            else MenuAnimationType.NO_ANIMATION)


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
"""


def _accent_hex() -> str:
    """读取 qfluentwidgets 当前主题强调色（失败回退默认青）"""
    try:
        tc = qconfig.themeColor
        if hasattr(tc, "value"):
            tc = tc.value
        if isinstance(tc, QColor) and tc.isValid():
            return tc.name()
    except Exception:
        pass
    return "#009faa"


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
        self._apply_theme()
        try:
            qconfig.themeChanged.connect(self._apply_theme)
        except Exception:
            pass

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
        self.setStyleSheet(_COMBO_QSS_TMPL.format(**c))
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
        self._cand_worker = None
        self._init_ui()

        # 桌号搜索防抖：停止输入 300ms 后才查库，避免逐字触发查询
        self._search_timer = QTimer(self)
        self._search_timer.setInterval(300)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._do_table_search)

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        form = QFormLayout()
        form.setSpacing(8)

        # 类型（必填，11 值枚举）
        self.type_combo = FluentCombo(self)
        self.type_combo.addItems(aftersale_db.ISSUE_TYPES)
        self.type_combo.setFixedWidth(260)
        form.addRow("类型 *:", self.type_combo)

        # 桌号（必填，关联球桌管理）
        self.table_no_edit = SearchLineEdit(self)
        self.table_no_edit.setPlaceholderText("输入桌号搜索球桌，如 226-04；可自由填写")
        self.table_no_edit.setFixedWidth(320)
        self.table_no_edit.textChanged.connect(self._on_table_text_changed)
        self.table_no_edit.clearSignal.connect(self._hide_candidates)
        form.addRow("桌号 *:", self.table_no_edit)

        # 球桌候选列表（默认隐藏，搜索命中后展示）
        self._cand_list = QListWidget(self)
        self._cand_list.setFixedHeight(132)
        self._cand_list.setVisible(False)
        self._cand_list.itemClicked.connect(self._on_candidate_clicked)
        _style_cand_list(self._cand_list)
        try:
            qconfig.themeChanged.connect(lambda: _style_cand_list(self._cand_list))
        except Exception:
            pass
        form.addRow("", self._cand_list)

        # 球房（必填，选桌号自动带出，可手改）
        self.room_edit = LineEdit(self)
        self.room_edit.setPlaceholderText("选择桌号自动带出，或直接填写球房名称")
        self.room_edit.setFixedWidth(320)
        form.addRow("球房 *:", self.room_edit)

        # 地区（必填，预置 + 自由输入；原生 QComboBox 才支持 editable）
        self.region_combo = FluentCombo(self)
        self.region_combo.setEditable(True)
        self.region_combo.setInsertPolicy(_QComboBox.InsertPolicy.NoInsert)
        self.region_combo.addItems(aftersale_db.REGIONS_PRESET)
        self.region_combo.setFixedWidth(260)
        form.addRow("地区 *:", self.region_combo)

        # 问题（必填，历史候选 + 自由输入；原生 QComboBox 才支持 editable）
        self.problem_combo = FluentCombo(self)
        self.problem_combo.setEditable(True)
        self.problem_combo.setInsertPolicy(_QComboBox.InsertPolicy.NoInsert)
        self.problem_combo.setFixedWidth(380)
        form.addRow("问题 *:", self.problem_combo)

        # 发生原因（选填，多行）
        self.cause_edit = PlainTextEdit(self)
        self.cause_edit.setFixedHeight(56)
        self.cause_edit.setPlaceholderText("选填")
        form.addRow("发生原因:", self.cause_edit)

        # 是否解决（默认「否」）
        self.resolved_combo = FluentCombo(self)
        self.resolved_combo.addItems(["否", "是"])
        self.resolved_combo.setFixedWidth(120)
        form.addRow("是否解决 *:", self.resolved_combo)

        # 解决方案（选填，多行）
        self.solution_edit = PlainTextEdit(self)
        self.solution_edit.setFixedHeight(56)
        self.solution_edit.setPlaceholderText("选填")
        form.addRow("解决方案:", self.solution_edit)

        # 解决人 / 响应时间 / 填写人（一行两列紧凑布局）
        row2 = QHBoxLayout()
        self.resolver_combo = FluentCombo(self)
        self.resolver_combo.setEditable(True)
        self.resolver_combo.setInsertPolicy(_QComboBox.InsertPolicy.NoInsert)
        self.resolver_combo.setFixedWidth(180)
        row2.addWidget(QLabel("解决人:", self))
        row2.addWidget(self.resolver_combo)
        row2.addSpacing(16)
        self.response_combo = FluentCombo(self)
        self.response_combo.setEditable(True)
        self.response_combo.setInsertPolicy(_QComboBox.InsertPolicy.NoInsert)
        self.response_combo.addItems(aftersale_db.RESPONSE_TIME_PRESET)
        self.response_combo.setFixedWidth(180)
        row2.addWidget(QLabel("响应时间:", self))
        row2.addWidget(self.response_combo)
        row2.addSpacing(16)
        self.creator_edit = LineEdit(self)
        self.creator_edit.setFixedWidth(140)
        self.creator_edit.setText(_default_creator())
        row2.addWidget(QLabel("填写人:", self))
        row2.addWidget(self.creator_edit)
        row2.addStretch(1)
        form.addRow(row2)

        root.addLayout(form)

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

    # ---------- 桌号搜索与带出 ----------

    def _on_table_text_changed(self, text):
        self._search_kw = str(text or "").strip()
        self._snk_code = ""  # 文本变动后旧的关联失效
        if not self._search_kw:
            self._hide_candidates()
            return
        self._search_timer.start()

    def _do_table_search(self):
        """防抖后异步搜索球桌管理库（含搜索条件快照，过期结果丢弃）"""
        kw = self._search_kw
        if not kw:
            return
        self._cand_worker = AftersaleDBWorker(
            table_db.query_page, 1, 12, kw, False, False)
        self._cand_worker.finished.connect(
            lambda result, k=kw: self._on_candidates(k, result))
        self._cand_worker.error.connect(lambda _m: self._hide_candidates())
        self._cand_worker.start()

    def _on_candidates(self, kw, result):
        if kw != self._search_kw:
            return  # 输入已变化，丢弃过期结果
        _total, rows = result
        # 精确匹配单台球桌：静默带出，不弹候选
        exact = [r for r in rows
                 if str(r.get("name") or "").strip().lower() == kw.lower()]
        if len(exact) == 1:
            self._apply_table(exact[0])
            self._hide_candidates()
            return
        if not rows:
            self._hide_candidates()
            return
        self._cand_rows = rows
        self._cand_list.clear()
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
        """选中球桌 → 带出桌号/球房/SNK（阻断信号避免重复触发搜索）"""
        self.table_no_edit.blockSignals(True)
        self.table_no_edit.setText(str(row.get("name") or ""))
        self.table_no_edit.blockSignals(False)
        self.room_edit.setText(str(row.get("roomName") or ""))
        self._snk_code = str(row.get("snk_code") or "")
        self._search_kw = str(row.get("name") or "").strip()

    def _hide_candidates(self):
        self._cand_list.setVisible(False)

    # ---------- 值读写 ----------

    def set_values(self, rec: dict):
        """编辑模式：用已有记录填充表单"""
        self.type_combo.setCurrentText(str(rec.get("issue_type") or ""))
        self.table_no_edit.setText(str(rec.get("table_no") or ""))
        self._search_kw = str(rec.get("table_no") or "").strip()
        self.room_edit.setText(str(rec.get("room_name") or ""))
        self.region_combo.setEditText(str(rec.get("region") or ""))
        self.problem_combo.setEditText(str(rec.get("problem") or ""))
        self.cause_edit.setPlainText(str(rec.get("cause") or ""))
        self.resolved_combo.setCurrentText(str(rec.get("resolved") or "否"))
        self.solution_edit.setPlainText(str(rec.get("solution") or ""))
        self.resolver_combo.setEditText(str(rec.get("resolver") or ""))
        self.response_combo.setEditText(str(rec.get("response_time") or ""))
        self.creator_edit.setText(str(rec.get("creator") or ""))
        self._snk_code = str(rec.get("snk_code") or "")

    def collect(self) -> dict:
        """收集表单值为记录 dict（不含必填校验）"""
        return {
            "issue_type": self.type_combo.currentText().strip(),
            "table_no": self.table_no_edit.text().strip(),
            "room_name": self.room_edit.text().strip(),
            "region": self.region_combo.currentText().strip(),
            "problem": self.problem_combo.currentText().strip(),
            "cause": self.cause_edit.toPlainText().strip(),
            "resolved": self.resolved_combo.currentText().strip() or "否",
            "solution": self.solution_edit.toPlainText().strip(),
            "resolver": self.resolver_combo.currentText().strip(),
            "response_time": self.response_combo.currentText().strip(),
            "creator": self.creator_edit.text().strip(),
            "snk_code": self._snk_code,
        }

    def validate(self) -> list:
        """必填校验，返回缺失字段中文名列表（空列表为通过）"""
        missing = []
        if not self.type_combo.currentText().strip():
            missing.append("类型")
        if not self.table_no_edit.text().strip():
            missing.append("桌号")
        if not self.room_edit.text().strip():
            missing.append("球房")
        if not self.region_combo.currentText().strip():
            missing.append("地区")
        if not self.problem_combo.currentText().strip():
            missing.append("问题")
        return missing

    def clear_form(self):
        """清空表单（保留填写人与下拉候选）"""
        self.table_no_edit.clear()
        self.room_edit.clear()
        self.cause_edit.clear()
        self.solution_edit.clear()
        self.problem_combo.setEditText("")
        self.resolver_combo.setEditText("")
        self.response_combo.setEditText("")
        self.resolved_combo.setCurrentIndex(0)
        self.type_combo.setCurrentIndex(-1)
        self.region_combo.setEditText("")
        self._snk_code = ""
        self._hide_candidates()


# ==================== 板块一：填写录入页 ====================

class EntryPage(QWidget):
    """填写录入页：表单卡片 + 提交/清空"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._save_worker = None
        self._cand_worker = None
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        view = QWidget()
        view.setStyleSheet("QWidget { background: transparent; }")
        scroll.setWidget(view)
        root.addWidget(scroll)

        layout = QVBoxLayout(view)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        card = CardWidget(view)
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(8)
        vbox.addWidget(BodyLabel("售后问题登记", card))
        vbox.addWidget(CaptionLabel(
            "带 * 为必填项；桌号输入后自动搜索球桌管理库，选中可带出球房/SNK。"
            "提交后写入数据库，多人协作时其他成员刷新可见", card))
        self.form = AftersaleForm(card)
        vbox.addWidget(self.form)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_clear = PushButton("清空", card)
        self._btn_clear.setToolTip("清空表单全部内容")
        self._btn_clear.clicked.connect(self.form.clear_form)
        btn_row.addWidget(self._btn_clear)
        self._btn_submit = PrimaryPushButton(FluentIcon.ACCEPT, "提交记录", card)
        self._btn_submit.setToolTip("校验必填项后写入数据库")
        self._btn_submit.clicked.connect(self._on_submit)
        btn_row.addWidget(self._btn_submit)
        vbox.addLayout(btn_row)

        layout.addWidget(card)
        layout.addStretch(1)

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_candidates()

    def _refresh_candidates(self):
        """进入页面刷新动态候选（问题/解决人/地区）"""
        self._cand_worker = AftersaleDBWorker(aftersale_db.get_field_candidates)
        self._cand_worker.finished.connect(self.form.load_candidates)
        self._cand_worker.error.connect(lambda _m: None)
        self._cand_worker.start()

    def _on_submit(self):
        """提交：必填校验 → 后台线程写库 → 清表单"""
        missing = self.form.validate()
        if missing:
            show_info_bar(f"请先填写必填项: {'、'.join(missing)}", "warning",
                          title="无法提交", parent=self, duration=3000)
            return
        if self._save_worker and self._save_worker.isRunning():
            return
        self._btn_submit.setEnabled(False)
        record = self.form.collect()
        self._save_worker = AftersaleDBWorker(aftersale_db.insert_record, record)
        self._save_worker.finished.connect(self._on_saved)
        self._save_worker.error.connect(self._on_save_error)
        self._save_worker.start()

    def _on_saved(self, rec_id):
        self._btn_submit.setEnabled(True)
        self.form.clear_form()
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

# 表格列定义：(字段key, 表头, 列宽)
RECORD_COLUMNS = (
    ("created_at", "填写时间", 132),
    ("issue_type", "类型", 84),
    ("table_no", "桌号", 84),
    ("room_name", "球房", 170),
    ("region", "地区", 60),
    ("problem", "问题", 190),
    ("resolved", "是否解决", 72),
    ("resolver", "解决人", 76),
    ("response_time", "响应时间", 84),
    ("creator", "填写人", 76),
)


class RecordsPage(QWidget):
    """记录与统计页：筛选/分页/统计/编辑/删除/导出/导入/刷新"""

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
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 12)
        root.setSpacing(10)

        # --- 工具栏 ---
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

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
        self._type_combo.setFixedWidth(120)
        self._type_combo.currentIndexChanged.connect(
            lambda _i: self._on_filter_changed())
        toolbar.addWidget(self._type_combo)

        # 是否解决筛选
        self._resolved_combo = FluentCombo(self)
        self._resolved_combo.addItem("全部状态", userData="")
        self._resolved_combo.addItem("未解决", userData="否")
        self._resolved_combo.addItem("已解决", userData="是")
        self._resolved_combo.setFixedWidth(100)
        self._resolved_combo.currentIndexChanged.connect(
            lambda _i: self._on_filter_changed())
        toolbar.addWidget(self._resolved_combo)

        # 关键词搜索（防抖）
        self._search_edit = SearchLineEdit(self)
        self._search_edit.setPlaceholderText("搜索 桌号/球房/问题/地区/解决人")
        self._search_edit.setFixedWidth(220)
        self._search_edit.textChanged.connect(self._on_search_input)
        toolbar.addWidget(self._search_edit)

        toolbar.addStretch(1)

        self._btn_import = PushButton(FluentIcon.ADD, "导入 Excel", self)
        self._btn_import.setToolTip("一次性导入 售后问题汇总 xlsx 历史数据")
        self._btn_import.clicked.connect(self._on_import)
        toolbar.addWidget(self._btn_import)

        self._btn_export = PushButton(FluentIcon.DOWNLOAD, "导出 xlsx", self)
        self._btn_export.setToolTip("按当前筛选条件导出为 xlsx")
        self._btn_export.clicked.connect(self._on_export)
        toolbar.addWidget(self._btn_export)

        self._btn_refresh = PushButton(FluentIcon.SYNC, "刷新", self)
        self._btn_refresh.setToolTip("重新查询数据库（多人协作时同步他人提交）")
        self._btn_refresh.clicked.connect(self._on_refresh)
        toolbar.addWidget(self._btn_refresh)

        root.addLayout(toolbar)

        # 搜索防抖定时器
        self._search_timer = QTimer(self)
        self._search_timer.setInterval(300)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._on_filter_changed)

        # --- 表格 ---
        self._table = TableWidget(self)
        self._table.setColumnCount(len(RECORD_COLUMNS))
        self._table.setHorizontalHeaderLabels([c[1] for c in RECORD_COLUMNS])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_FIXED_ROW_HEIGHT)
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(False)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._table.doubleClicked.connect(lambda _idx: self._on_edit())
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for i, (_k, _h, w) in enumerate(RECORD_COLUMNS):
            self._table.setColumnWidth(i, w)
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

    # ---------- 筛选条件 ----------

    def _current_filters(self) -> dict:
        """汇集当前筛选条件（周期/类型/状态/关键词）"""
        cycle = self._cycle_combo.currentData()
        return {
            "cycle_start": str(cycle or ""),
            "issue_type": (self._type_combo.currentText()
                           if self._type_combo.currentIndex() > 0 else ""),
            "resolved": self._resolved_combo.currentData() or "",
            "keyword": self._search_edit.text().strip(),
        }

    def _on_search_input(self, _text):
        self._search_timer.start()

    def _on_filter_changed(self):
        self._page_no = 1
        self._load()

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
        self._worker.finished.connect(self._on_cycles_loaded)
        self._worker.error.connect(lambda _m: self._load())
        self._worker.start()

    def _on_cycles_loaded(self, cycle_starts):
        self._cycles_loaded = True
        prev = self._cycle_combo.currentData()
        self._cycle_combo.blockSignals(True)
        self._cycle_combo.clear()
        current = aftersale_db.current_cycle_start()
        self._cycle_combo.addItem(
            f"当前周期 {aftersale_db.cycle_label(current)}", userData=current)
        self._cycle_combo.addItem("全部周期", userData="")
        for cs in cycle_starts or []:
            if cs != current:
                self._cycle_combo.addItem(
                    aftersale_db.cycle_label(cs), userData=cs)
        # 恢复之前的选择（默认当前周期）
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
            f["keyword"], f["cycle_start"], f["issue_type"], f["resolved"])
        self._worker.finished.connect(self._on_loaded)
        self._worker.error.connect(self._on_load_error)
        self._worker.start()

    def _on_loaded(self, result):
        total, rows, stats = result
        self._total = total
        self._rows = rows
        self._populate(rows)
        self._update_pager()
        self._lbl_stats.setText(
            f"共 {stats.get('total', 0)} 条 · "
            f"已解决 {stats.get('resolved', 0)} · "
            f"未解决 {stats.get('unresolved', 0)}")
        cycle = self._current_filters().get("cycle_start") or ""
        if cycle:
            self._lbl_cycle.setText(f"周期: {aftersale_db.cycle_label(cycle)}")
        else:
            self._lbl_cycle.setText("周期: 全部")

    def _on_load_error(self, msg):
        self._lbl_stats.setText(f"查询失败: {msg}")

    def _populate(self, rows):
        """行数据 → 表格：已解决/未解决着色"""
        self._table.setUpdatesEnabled(False)
        self._table.blockSignals(True)
        try:
            self._table.setRowCount(len(rows))
            for r, item in enumerate(rows):
                for c, (key, _h, _w) in enumerate(RECORD_COLUMNS):
                    val = str(item.get(key) or "")
                    cell = QTableWidgetItem(val)
                    cell.setToolTip(val)
                    cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if key == "resolved":
                        color = (QColor("#52c41a") if val == "是"
                                 else QColor("#ff5252"))
                        cell.setForeground(QBrush(color))
                    self._table.setItem(r, c, cell)
        finally:
            self._table.setUpdatesEnabled(True)
            self._table.blockSignals(False)

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
        act_edit.triggered.connect(lambda _=False: self._on_edit())
        menu.addAction(act_edit)
        rec = self._rows[idx.row()] if idx.row() < len(self._rows) else {}
        if str(rec.get("resolved") or "") != "是":
            act_done = Action(FluentIcon.ACCEPT, "标记已解决", self._table)
            act_done.triggered.connect(
                lambda _=False, r=rec: self._on_mark_resolved(r))
            menu.addAction(act_done)
        act_del = Action(FluentIcon.DELETE, "删除", self._table)
        act_del.triggered.connect(lambda _=False: self._on_delete())
        menu.addAction(act_del)
        menu.exec_(self._table.viewport().mapToGlobal(pos),
                   aniType=_popup_ani_type())

    def _on_edit(self):
        r = self._selected_row()
        if r < 0:
            return
        rec = dict(self._rows[r])
        dlg = EditRecordDialog(rec, self)
        # 编辑弹窗也需动态候选
        cand_worker = AftersaleDBWorker(aftersale_db.get_field_candidates)
        cand_worker.finished.connect(dlg.form.load_candidates)
        cand_worker.start()
        self._edit_cand_worker = cand_worker  # 保活引用
        if dlg.exec() and getattr(dlg, "collected", None):
            collected = dlg.collected
            collected["id"] = rec.get("id")
            collected["created_at"] = rec.get("created_at")  # 保留原填写时间
            self._run_update(collected)

    def _on_mark_resolved(self, rec):
        """快捷标记已解决：弹编辑窗并预置「是」"""
        rec = dict(rec)
        rec["resolved"] = "是"
        dlg = EditRecordDialog(rec, self)
        if dlg.exec() and getattr(dlg, "collected", None):
            collected = dlg.collected
            collected["id"] = rec.get("id")
            collected["created_at"] = rec.get("created_at")
            self._run_update(collected)

    def _run_update(self, record):
        self._worker = AftersaleDBWorker(aftersale_db.update_record, record)
        self._worker.finished.connect(
            lambda _n: (show_info_bar("记录已更新", "success",
                                      title="保存成功", parent=self, duration=2000),
                        self._load()))
        self._worker.error.connect(
            lambda m: show_info_bar(m, "error", title="保存失败",
                                    parent=self, duration=4000))
        self._worker.start()

    def _on_delete(self):
        r = self._selected_row()
        if r < 0:
            return
        rec = self._rows[r]
        desc = f"{rec.get('table_no') or ''} · {rec.get('problem') or ''}"
        if not MessageBox("确认删除", f"确定删除该售后记录？\n{desc}", self.window()).exec():
            return
        self._worker = AftersaleDBWorker(aftersale_db.delete_record, rec.get("id"))
        self._worker.finished.connect(
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
            f["keyword"], f["cycle_start"], f["issue_type"], f["resolved"])
        self._export_worker.finished.connect(self._on_export_done)
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
        if not MessageBox("导入确认",
                          "将解析该 Excel 并批量写入数据库（重复桌号+问题的记录可能被追加，请注意核对）。继续？",
                          self.window()).exec():
            return
        self._btn_import.setEnabled(False)
        self._import_worker = AftersaleDBWorker(aftersale_db.import_excel_rows, path)
        self._import_worker.finished.connect(self._on_import_done)
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


# ==================== 售后面板窗口 ====================

class AftersalePanelWindow(FluentWindow):
    """售后面板：FluentWindow + 左侧导航 + 两个功能页面（填写录入/记录与统计）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("售后面板")
        self.resize(1150, 680)
        self.setMinimumSize(900, 520)

        self.entry_page = EntryPage(self)
        self.entry_page.setObjectName("aftersaleEntryPage")
        self.records_page = RecordsPage(self)
        self.records_page.setObjectName("aftersaleRecordsPage")

        self.addSubInterface(self.entry_page, FluentIcon.EDIT, "填写录入")
        self.addSubInterface(self.records_page, FluentIcon.LIBRARY, "记录与统计")

        self.navigationInterface.setAcrylicEnabled(is_acrylic_enabled())
        self.navigationInterface.setCurrentItem(self.entry_page.objectName())

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
        """读取 settings.json 主题配置（与主程序入口一致）"""
        try:
            p = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "settings.json")
            with open(p, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return (Theme.DARK if cfg.get("dark_theme") else Theme.LIGHT,
                    cfg.get("theme_color", "#00BCD4"))
        except Exception:
            return Theme.LIGHT, "#00BCD4"

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    theme, color = _debug_theme()
    setTheme(theme)
    setThemeColor(color, lazy=True)
    win = AftersalePanelWindow()
    win.show()
    sys.exit(app.exec())
