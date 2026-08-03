# -*- coding: utf-8 -*-
"""运维管理面板（FluentWindow 多页面架构，独立窗口模块）

功能页面（左侧导航切换）：
1. 球桌管理 —— 对接 wechat2-billiard 接口，表格/搜索/分页/列筛选/右键复制
2. 设备状态 —— 对接 kd / xqzg 接口，按日期切换查看设备状态；点击总数/正常/操作单元格
   右侧滑出文件列表，点击文件条目选择目标分类执行图片迁移
3. 管理设置 —— 配置 API 账号密码、选择启用数据源（kd / xqzg）、测试连接

数据层复用 database/table_db.py，Worker 层复用 workers/table_worker.py。
"""

import math
import os
import json
from datetime import datetime

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QLabel, QApplication,
    QTextEdit, QDialog, QPushButton, QCheckBox, QTextBrowser)
from PySide6.QtCore import Qt, QItemSelectionModel, QDate, QPoint, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QShortcut, QKeySequence, QPalette, QCursor
from qfluentwidgets import (TableWidget, SearchLineEdit, PushButton,
    PrimaryPushButton, ToolButton, FluentIcon, ComboBox, RoundMenu, CheckBox,
    Action, TransparentDropDownPushButton, LineEdit, PlainTextEdit,
    FluentWindow, NavigationItemPosition, InfoBar, ProgressBar, TitleLabel,
    BodyLabel, CaptionLabel, CalendarPicker, PasswordLineEdit, ScrollArea,
    CardWidget)
from qfluentwidgets.components.widgets.table_view import TableItemDelegate

from core.app_paths import get_app_dir
from workers.table_worker import (TableFetchWorker, DevicesFetchWorker,
                                  SnookerOmFetchWorker, MigrateImageWorker,
                                  LoginTestWorker, get_active_api_source,
                                  CATEGORY_DIRS)
from database import table_db

# ==================== 常量定义 ====================

# 球桌管理列：(字段key, 表头, 宽度)
TABLE_COLUMNS = [
    ("name", "球桌号", 90),
    ("roomName", "球房名称", 200),
    ("onlineStatusName", "在线状态", 80),
    ("remark", "备注", 470),
    ("cameraPassExt", "相机密码", 220),
]

_STATUS_COLORS = {
    "运行中": QColor(16, 137, 62),
    "空闲": QColor(0, 120, 212),
    "下线": QColor(130, 130, 130),
}

# 设备状态列：(字段key, 表头, 宽度)
DEVICE_COLUMNS = [
    ("table_id", "球桌号码", 90),
    ("club_name", "球房名称", 150),
    ("pic_total", "总数", 70),
    ("normal_count", "正常", 55),
    ("except_count", "操作", 55),
    ("untreated_count", "待处理", 60),
    ("operation_count", "使用", 55),
    ("accuracy_count", "精度", 55),
    ("already_count", "问题", 55),
    ("rubbish_count", "废弃", 55),
    ("operation_rate", "操作率", 70),
    ("error_rate", "错误率", 70),
]

# 文件字段 → 中文分类名
FILE_FIELD_CATEGORIES = [
    ("normal_files", "正常"),
    ("except_files", "操作"),
    ("untreated_files", "待处理"),
    ("operation_files", "使用"),
    ("accuracy_files", "精度"),
    ("already_files", "问题"),
    ("rubbish_files", "废弃"),
    ("version_files", "版本"),
]

# 中文分类 → 文件字段（迁移用，不含版本）
CATEGORY_FILE_FIELDS = {cn: field for field, cn in FILE_FIELD_CATEGORIES if cn != "版本"}

# 迁移可选分类（与 CATEGORY_DIRS 一致）
MIGRATE_CATEGORIES = list(CATEGORY_DIRS.keys())

# 文件字段 → 中文分类名（反查）
FIELD_CATEGORY = dict(FILE_FIELD_CATEGORIES)

# 点击文件条目后的迁移目标选项
MIGRATE_DEST_OPTIONS = ["问题", "精度", "使用", "废弃"]

# 可点击查看文件列表的单元格链接色
_LINK_COLOR = QColor(0, 120, 212)


# ==================== settings.json 读写 ====================

def _load_settings() -> dict:
    """读取 settings.json，失败时返回空字典"""
    path = os.path.join(get_app_dir(), "settings.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_settings(data: dict):
    """合并写入 settings.json（保留未涉及的其他字段）"""
    path = os.path.join(get_app_dir(), "settings.json")
    settings = _load_settings()
    settings.update(data)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


# ==================== 通用组件 ====================

class _ReadOnlySelectDelegate(TableItemDelegate):
    """双击单元格进入只读编辑态：支持光标拖选文本并复制，禁止修改"""

    def createEditor(self, parent, option, index):
        editor = QTextEdit(parent)
        editor.setReadOnly(True)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        pal = editor.palette()
        pal.setColor(QPalette.ColorRole.Text,
                     self.parent().palette().color(QPalette.ColorRole.Text))
        editor.setPalette(pal)
        editor.setStyleSheet(
            "QTextEdit { background: palette(base); border: 1px solid palette(highlight);"
            " border-radius: 4px; padding: 2px;"
            " selection-background-color: palette(highlight);"
            " selection-color: palette(highlighted-text); }")
        return editor

    def setEditorData(self, editor, index):
        editor.setPlainText(index.data(Qt.ItemDataRole.DisplayRole) or "")

    def setModelData(self, editor, model, index):
        pass

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


def _copy_table_selection(table):
    """复制表格选中内容（编辑态优先复制光标选中部分，否则复制选中单元格）"""
    focus = QApplication.focusWidget()
    if focus is not None and table.isAncestorOf(focus):
        tc_getter = getattr(focus, "textCursor", None)
        if tc_getter is not None:
            tc = focus.textCursor()
            if tc.hasSelection():
                text = tc.selectedText().replace("\u2029", "\n")
                QApplication.clipboard().setText(text)
                return
    items = table.selectedItems()
    if not items:
        return
    cells = sorted((it.row(), it.column(), it.text()) for it in items)
    lines, cur_row, parts = [], None, []
    for row, col, text in cells:
        if row != cur_row:
            if parts:
                lines.append("\t".join(parts))
            cur_row, parts = row, []
        parts.append(text)
    if parts:
        lines.append("\t".join(parts))
    QApplication.clipboard().setText("\n".join(lines))


def _fit_table_rows(table):
    """行高自适应 + 最小行高"""
    table.resizeRowsToContents()
    for r in range(table.rowCount()):
        table.setRowHeight(r, max(table.rowHeight(r) + 10, 38))


class AddRecordDialog(QDialog):
    """手动添加记录弹窗"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("手动添加记录")
        self.setFixedSize(420, 320)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(10)
        self._edit_name = LineEdit(self)
        form.addRow("球桌号:", self._edit_name)
        self._edit_room = LineEdit(self)
        form.addRow("球房名称:", self._edit_room)
        self._edit_camera = LineEdit(self)
        form.addRow("相机密码:", self._edit_camera)
        self._edit_remark = PlainTextEdit(self)
        self._edit_remark.setFixedHeight(80)
        form.addRow("备注:", self._edit_remark)
        layout.addLayout(form)
        layout.addStretch(1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        btn_ok = PrimaryPushButton("添加", self)
        btn_ok.clicked.connect(self._on_ok)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

    def _on_ok(self):
        if not self._edit_name.text().strip():
            self._edit_name.setPlaceholderText("球桌号不能为空")
            self._edit_name.setFocus()
            return
        self.accept()

    def get_record(self) -> dict:
        return {
            "name": self._edit_name.text().strip(),
            "roomName": self._edit_room.text().strip(),
            "onlineStatusName": "",
            "remark": self._edit_remark.toPlainText().strip(),
            "cameraPassExt": self._edit_camera.text().strip(),
        }


class DeviceFilesDialog(QDialog):
    """设备文件列表详情弹窗（按分类展示全部文件，支持一键复制）"""

    def __init__(self, row: dict, parent=None):
        super().__init__(parent)
        code = row.get("device_code", "")
        self.setWindowTitle(f"文件详情 - {code}")
        self.resize(620, 500)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.addWidget(BodyLabel(f"设备: {code}    球房: {row.get('club_name', '')}", self))
        header.addStretch(1)
        btn_copy = PushButton(FluentIcon.COPY, "复制全部", self)
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(self._plain))
        header.addWidget(btn_copy)
        layout.addLayout(header)

        self._plain = self._build_text(row)
        browser = QTextBrowser(self)
        browser.setPlainText(self._plain)
        layout.addWidget(browser, 1)

        btn_close = PushButton("关闭", self)
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close, 0, Qt.AlignmentFlag.AlignRight)

    @staticmethod
    def _build_text(row) -> str:
        parts = []
        for field, cn in FILE_FIELD_CATEGORIES:
            files = row.get(field) or []
            parts.append(f"【{cn}】({len(files)} 个)")
            for f in files:
                parts.append(f"  {f}")
            parts.append("")
        return "\n".join(parts)


# ==================== 页面1: 球桌管理 ====================

class TablePage(QWidget):
    """球桌管理页（复用原 TablePanelWindow 全部功能逻辑）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page_no = 1
        self._page_size = 30
        self._total = 0
        self._worker = None
        self._hidden_cols = {2}
        self._init_ui()
        self._load_local()
        db_total, _ = table_db.get_meta()
        if db_total == 0:
            self._sync_from_api()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 12)
        root.setSpacing(10)

        # --- 工具栏 ---
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self._search_edit = SearchLineEdit(self)
        self._search_edit.setPlaceholderText("搜索球桌号 / 球房 / 备注...")
        self._search_edit.setFixedWidth(280)
        self._search_edit.textChanged.connect(self._on_search)
        toolbar.addWidget(self._search_edit)
        toolbar.addStretch(1)

        self._col_btn = TransparentDropDownPushButton("筛选", self)
        self._col_btn.setIcon(FluentIcon.FILTER.icon())
        self._build_col_menu()
        toolbar.addWidget(self._col_btn)

        self._add_btn = PushButton(FluentIcon.ADD, "手动添加", self)
        self._add_btn.setToolTip("手动添加一条记录到本地数据库")
        self._add_btn.clicked.connect(self._on_add_record)
        toolbar.addWidget(self._add_btn)

        self._refresh_btn = PushButton(FluentIcon.SYNC, "同步数据", self)
        self._refresh_btn.setToolTip("从服务器拉取全量数据")
        self._refresh_btn.clicked.connect(self._sync_from_api)
        toolbar.addWidget(self._refresh_btn)
        root.addLayout(toolbar)

        # --- 表格 ---
        self._table = TableWidget(self)
        self._table.setColumnCount(len(TABLE_COLUMNS))
        self._table.setHorizontalHeaderLabels([c[1] for c in TABLE_COLUMNS])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self._table.setItemDelegate(_ReadOnlySelectDelegate(self._table))
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_copy_menu)
        QShortcut(QKeySequence.StandardKey.Copy, self._table).activated.connect(
            lambda: _copy_table_selection(self._table))

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        header.sectionResized.connect(lambda: _fit_table_rows(self._table))
        for i, (_, _, w) in enumerate(TABLE_COLUMNS):
            self._table.setColumnWidth(i, w)
        for i in self._hidden_cols:
            self._table.setColumnHidden(i, True)
        root.addWidget(self._table, 1)

        # --- 分页栏 ---
        pager = QHBoxLayout()
        pager.setSpacing(6)
        self._lbl_info = QLabel("正在加载...", self)
        pager.addWidget(self._lbl_info)
        pager.addStretch(1)
        pager.addWidget(QLabel("每页:", self))
        self._size_combo = ComboBox(self)
        self._size_combo.addItems(["30", "50", "100", "200"])
        self._size_combo.setFixedWidth(70)
        self._size_combo.currentTextChanged.connect(self._on_page_size_changed)
        pager.addWidget(self._size_combo)
        self._btn_prev = ToolButton(FluentIcon.LEFT_ARROW, self)
        self._btn_prev.setToolTip("上一页")
        self._btn_prev.clicked.connect(self._on_prev_page)
        pager.addWidget(self._btn_prev)
        self._lbl_page = QLabel("1/1", self)
        self._lbl_page.setMinimumWidth(50)
        self._lbl_page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pager.addWidget(self._lbl_page)
        self._btn_next = ToolButton(FluentIcon.RIGHT_ARROW, self)
        self._btn_next.setToolTip("下一页")
        self._btn_next.clicked.connect(self._on_next_page)
        pager.addWidget(self._btn_next)
        self._lbl_time = QLabel("", self)
        pager.addWidget(self._lbl_time)
        root.addLayout(pager)

    def _build_col_menu(self):
        menu = RoundMenu("筛选列", self)
        for i, (_, title, _) in enumerate(TABLE_COLUMNS):
            cb = CheckBox(title, self)
            cb.setChecked(i not in self._hidden_cols)
            cb.setFixedSize(max(cb.sizeHint().width() + 30, 120), 36)
            cb.checkStateChanged.connect(
                lambda state, idx=i: self._toggle_col(idx, state == Qt.CheckState.Checked))
            menu.addWidget(cb, selectable=False)
        self._col_btn.setMenu(menu)

    # ---------- 数据加载 ----------

    def _load_local(self):
        keyword = self._search_edit.text().strip()
        total, rows = table_db.query_page(self._page_no, self._page_size, keyword)
        self._total = total
        self._populate(rows)
        self._update_pager(keyword)
        _, sync_time = table_db.get_meta()
        self._lbl_time.setText(f"数据时间: {sync_time}" if sync_time else "未同步")

    def _sync_from_api(self):
        if self._worker and self._worker.isRunning():
            return
        self._refresh_btn.setEnabled(False)
        self._lbl_info.setText("正在从服务器同步...")
        self._worker = TableFetchWorker()
        self._worker.result_ready.connect(self._on_sync_done)
        self._worker.error.connect(self._on_sync_error)
        self._worker.start()

    def _on_sync_done(self, rows):
        count = table_db.save_all(rows)
        self._page_no = 1
        self._load_local()
        self._refresh_btn.setEnabled(True)
        self._lbl_info.setText(f"同步完成，共 {count} 条")

    def _on_sync_error(self, msg):
        self._refresh_btn.setEnabled(True)
        self._lbl_info.setText(f"同步失败: {msg}")

    def _populate(self, rows):
        self._table.setRowCount(len(rows))
        for r, item in enumerate(rows):
            for c, (key, _, _) in enumerate(TABLE_COLUMNS):
                val = item.get(key) or ""
                val = str(val).replace("\n", " ").strip()
                cell = QTableWidgetItem(val)
                cell.setToolTip(str(item.get(key) or ""))
                if key == "onlineStatusName":
                    color = _STATUS_COLORS.get(val)
                    if color:
                        cell.setForeground(color)
                self._table.setItem(r, c, cell)
        _fit_table_rows(self._table)

    def _update_pager(self, keyword=""):
        total_pages = max(1, math.ceil(self._total / self._page_size))
        self._page_no = min(self._page_no, total_pages)
        self._lbl_page.setText(f"{self._page_no}/{total_pages}")
        self._btn_prev.setEnabled(self._page_no > 1)
        self._btn_next.setEnabled(self._page_no < total_pages)
        kw_tip = f"（搜索: {keyword}）" if keyword else ""
        self._lbl_info.setText(f"共 {self._total} 条{kw_tip}，第 {self._page_no}/{total_pages} 页")

    # ---------- 交互 ----------

    def _on_search(self, _=""):
        self._page_no = 1
        self._load_local()

    def _on_prev_page(self):
        if self._page_no > 1:
            self._page_no -= 1
            self._load_local()

    def _on_next_page(self):
        if self._page_no < max(1, math.ceil(self._total / self._page_size)):
            self._page_no += 1
            self._load_local()

    def _on_page_size_changed(self, text):
        try:
            size = int(text)
        except ValueError:
            return
        if size != self._page_size:
            self._page_size = size
            self._page_no = 1
            self._load_local()

    def _toggle_col(self, col_idx, visible):
        if visible:
            self._hidden_cols.discard(col_idx)
        else:
            self._hidden_cols.add(col_idx)
        self._table.setColumnHidden(col_idx, not visible)

    def _on_add_record(self):
        dlg = AddRecordDialog(self)
        if not dlg.exec():
            return
        table_db.insert_one(dlg.get_record())
        self._page_no = 1
        self._load_local()
        InfoBar.success("添加成功", "记录已写入本地数据库", parent=self, duration=2000)

    def _show_copy_menu(self, pos):
        if not self._table.selectedItems():
            idx = self._table.indexAt(pos)
            if not idx.isValid():
                return
            self._table.selectionModel().select(
                idx, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        menu = RoundMenu(parent=self._table)
        act = Action(FluentIcon.COPY, "复制", self._table)
        act.triggered.connect(lambda: _copy_table_selection(self._table))
        menu.addAction(act)
        menu.exec_(self._table.viewport().mapToGlobal(pos))


# ==================== 文件列表面板（右侧滑出） ====================

class FileListPanel(QWidget):
    """设备状态页右侧滑出面板：展示文件列表，点击条目弹出迁移目标选项"""

    _PANEL_WIDTH = 360

    def __init__(self, device_page):
        super().__init__(device_page)
        self._device_page = device_page
        self._row = {}          # 当前设备完整数据
        self._title = ""        # 面板标题
        self._fields = []       # 展示的文件字段列表
        self._entries = []      # [(文件名, 源分类), ...]
        self._anim = None
        self._init_ui()
        self.hide()

    def _init_ui(self):
        self.setFixedWidth(self._PANEL_WIDTH)
        self.setObjectName("fileListPanel")
        # 自定义 QWidget 子类必须开启该属性，stylesheet 背景才会被绘制（否则透明）
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "QWidget#fileListPanel { background: palette(window);"
            " border-left: 1px solid palette(mid); }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        self._lbl_title = BodyLabel("文件列表", self)
        header.addWidget(self._lbl_title, 1)
        self._btn_close = ToolButton(FluentIcon.CLOSE, self)
        self._btn_close.setToolTip("关闭")
        self._btn_close.clicked.connect(self.slide_out)
        header.addWidget(self._btn_close)
        layout.addLayout(header)

        self._list = TableWidget(self)
        self._list.setColumnCount(2)
        self._list.setHorizontalHeaderLabels(["分类", "文件名"])
        self._list.setColumnWidth(0, 52)
        self._list.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._list.verticalHeader().setVisible(False)
        self._list.setAlternatingRowColors(True)
        self._list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._list.cellClicked.connect(self._on_file_clicked)
        layout.addWidget(self._list, 1)

        hint = CaptionLabel("点击文件条目，选择要迁移到的目标分类", self)
        layout.addWidget(hint)

    # ---------- 展示 ----------

    def show_files(self, row: dict, title: str, fields: list):
        self._row = row
        self._title = title
        self._fields = fields
        self._reload_entries()
        self.slide_in()

    def _reload_entries(self):
        self._entries = []
        for field in self._fields:
            src_cat = FIELD_CATEGORY.get(field, "")
            for fname in (self._row.get(field) or []):
                self._entries.append((str(fname), src_cat))
        self._list.setRowCount(len(self._entries))
        for r, (fname, src_cat) in enumerate(self._entries):
            cat_item = QTableWidgetItem(src_cat)
            cat_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._list.setItem(r, 0, cat_item)
            name_item = QTableWidgetItem(fname)
            name_item.setToolTip(fname)
            self._list.setItem(r, 1, name_item)
        code = self._row.get("table_id") or self._row.get("device_code", "")
        self._lbl_title.setText(f"{self._title} · {code} · {len(self._entries)} 个")

    def refresh_if_visible(self):
        """数据刷新后，若面板可见则重新加载当前设备的文件列表"""
        if not self.isVisible():
            return
        code = self._row.get("device_code", "")
        if not code:
            return
        date = self._device_page._current_date()
        _, rows = table_db.query_kd_page(1, 99999, code, date)
        fresh = next((r for r in rows if r.get("device_code") == code), None)
        if fresh:
            self._row = fresh
            self._reload_entries()

    # ---------- 迁移交互 ----------

    def _on_file_clicked(self, row, _col):
        if not (0 <= row < len(self._entries)):
            return
        fname, src_cat = self._entries[row]
        menu = RoundMenu(parent=self)
        for dest in MIGRATE_DEST_OPTIONS:
            act = Action(FluentIcon.SEND, f"迁移到「{dest}」", self)
            act.triggered.connect(
                lambda _=False, f=fname, s=src_cat, d=dest:
                    self._device_page.migrate_file(f, s, d))
            menu.addAction(act)
        menu.exec_(QCursor.pos())

    # ---------- 滑入 / 滑出动画 ----------

    def slide_in(self):
        parent = self.parent()
        pw, ph = parent.width(), parent.height()
        self.setFixedSize(self._PANEL_WIDTH, ph)
        self.move(pw, 0)
        self.show()
        self.raise_()
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(200)
        anim.setStartValue(QPoint(pw, 0))
        anim.setEndValue(QPoint(pw - self._PANEL_WIDTH, 0))
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim = anim
        anim.start()

    def slide_out(self):
        parent = self.parent()
        pw = parent.width()
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(180)
        anim.setStartValue(self.pos())
        anim.setEndValue(QPoint(pw, 0))
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.finished.connect(self.hide)
        self._anim = anim
        anim.start()


# ==================== 页面2: 设备状态 ====================

class DevicePage(QWidget):
    """设备状态页：按日期查看 kd 接口设备数据，支持文件列表详情"""

    # 可点击查看文件列表的列：字段key → (面板标题, 文件字段列表)
    _FILE_VIEW_FIELDS = {
        "pic_total": ("全部文件", ["except_files", "normal_files"]),
        "normal_count": ("正常文件", ["normal_files"]),
        "except_count": ("操作文件", ["except_files"]),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page_no = 1
        self._page_size = 50
        self._total = 0
        self._worker = None
        self._migrate_worker = None
        self._refresh_worker = None
        self._init_ui()
        # 默认日期为昨天（与主窗口一致）
        yesterday = QDate.currentDate().addDays(-1)
        self._date_picker.blockSignals(True)
        self._date_picker.setDate(yesterday)
        self._date_picker.blockSignals(False)
        self._load_local()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 12)
        root.setSpacing(10)

        # --- 工具栏 ---
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        toolbar.addWidget(QLabel("日期:", self))
        self._date_picker = CalendarPicker(self)
        self._date_picker.setFixedWidth(160)
        self._date_picker.dateChanged.connect(self._on_date_changed)
        toolbar.addWidget(self._date_picker)

        self._search_edit = SearchLineEdit(self)
        self._search_edit.setPlaceholderText("搜索球桌号 / 球房名 / 设备编码...")
        self._search_edit.setFixedWidth(240)
        self._search_edit.textChanged.connect(self._on_search)
        toolbar.addWidget(self._search_edit)
        toolbar.addStretch(1)

        self._sync_btn = PrimaryPushButton(FluentIcon.SEARCH, "搜索", self)
        self._sync_btn.setToolTip("从 kd 接口拉取所选日期的设备数据")
        self._sync_btn.clicked.connect(self._search_from_api)
        toolbar.addWidget(self._sync_btn)
        root.addLayout(toolbar)

        # --- 表格 ---
        self._table = TableWidget(self)
        self._table.setColumnCount(len(DEVICE_COLUMNS))
        self._table.setHorizontalHeaderLabels([c[1] for c in DEVICE_COLUMNS])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self._table.setItemDelegate(_ReadOnlySelectDelegate(self._table))
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._table.cellClicked.connect(self._on_cell_clicked)
        QShortcut(QKeySequence.StandardKey.Copy, self._table).activated.connect(
            lambda: _copy_table_selection(self._table))

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for i, (_, _, w) in enumerate(DEVICE_COLUMNS):
            self._table.setColumnWidth(i, w)
        root.addWidget(self._table, 1)

        # --- 状态栏 ---
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self._lbl_info = QLabel("", self)
        bar.addWidget(self._lbl_info)
        bar.addStretch(1)
        pager = QHBoxLayout()
        self._btn_prev = ToolButton(FluentIcon.LEFT_ARROW, self)
        self._btn_prev.clicked.connect(self._on_prev_page)
        pager.addWidget(self._btn_prev)
        self._lbl_page = QLabel("1/1", self)
        self._lbl_page.setMinimumWidth(50)
        self._lbl_page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pager.addWidget(self._lbl_page)
        self._btn_next = ToolButton(FluentIcon.RIGHT_ARROW, self)
        self._btn_next.clicked.connect(self._on_next_page)
        pager.addWidget(self._btn_next)
        bar.addLayout(pager)
        bar.addStretch(1)
        self._lbl_time = QLabel("", self)
        bar.addWidget(self._lbl_time)
        root.addLayout(bar)

        # --- 右侧滑出文件面板 ---
        self._file_panel = FileListPanel(self)

    # ---------- 日期管理 ----------

    def _current_date(self) -> str:
        """返回日期选择器中的日期，格式如 2026/08/02（kd 接口 file_path 格式）"""
        return self._date_picker.date.toString("yyyy/MM/dd")

    def _on_date_changed(self, _=None):
        self._page_no = 1
        panel = getattr(self, "_file_panel", None)
        if panel:
            panel.slide_out()
        self._load_local()

    # ---------- 数据加载 ----------

    def _active_source(self) -> str:
        """当前启用的设备数据源：'kd' / 'xqzg'"""
        return get_active_api_source()

    def _load_local(self):
        date = self._current_date()
        keyword = self._search_edit.text().strip()
        if self._active_source() == "xqzg":
            total, rows = table_db.query_xqzg_page(self._page_no, self._page_size, keyword)
        else:
            total, rows = table_db.query_kd_page(self._page_no, self._page_size, keyword, date)
        self._total = total
        self._populate(rows)
        self._update_pager(date, keyword)

    def _search_from_api(self):
        date = self._current_date()
        if not date:
            return
        if self._worker and self._worker.isRunning():
            return
        self._sync_btn.setEnabled(False)
        src = self._active_source()
        self._lbl_info.setText(f"正在从 {src} 搜索 {date} 的设备数据...")
        if src == "xqzg":
            self._worker = SnookerOmFetchWorker(file_path=date)
        else:
            self._worker = DevicesFetchWorker(file_path=date)
        self._worker.result_ready.connect(self._on_search_done)
        self._worker.error.connect(self._on_search_error)
        self._worker.start()

    def _on_search_done(self, data):
        rows = data.get("lists") or data.get("results") or []
        date = self._current_date()
        if self._active_source() == "xqzg":
            count = table_db.save_xqzg(rows)
        else:
            count = table_db.save_kd(rows, date)
        self._sync_btn.setEnabled(True)
        self._page_no = 1
        self._load_local()
        self._lbl_time.setText(f"查询时间: {datetime.now().strftime('%H:%M:%S')}")
        InfoBar.success("搜索完成", f"{date} 共 {count} 台设备", parent=self, duration=2500)

    def _on_search_error(self, msg):
        self._sync_btn.setEnabled(True)
        self._lbl_info.setText(f"搜索失败: {msg}")
        InfoBar.error("搜索失败", msg, parent=self, duration=4000)

    def _populate(self, rows):
        self._table.setRowCount(len(rows))
        for r, item in enumerate(rows):
            for c, (key, _, _) in enumerate(DEVICE_COLUMNS):
                val = item.get(key)
                if isinstance(val, list):
                    display = str(len(val))
                    tip = "\n".join(val[:30]) + ("..." if len(val) > 30 else "")
                else:
                    display = str(val if val is not None else "")
                    tip = display
                cell = QTableWidgetItem(display)
                cell.setToolTip(tip if tip else "(空)")
                if key in self._FILE_VIEW_FIELDS:
                    cell.setForeground(_LINK_COLOR)
                    cell.setToolTip("点击查看文件列表")
                self._table.setItem(r, c, cell)
        _fit_table_rows(self._table)

    def _update_pager(self, date="", keyword=""):
        total_pages = max(1, math.ceil(self._total / self._page_size))
        self._page_no = min(self._page_no, total_pages)
        self._lbl_page.setText(f"{self._page_no}/{total_pages}")
        self._btn_prev.setEnabled(self._page_no > 1)
        self._btn_next.setEnabled(self._page_no < total_pages)
        kw = f"（搜索: {keyword}）" if keyword else ""
        self._lbl_info.setText(f"共 {self._total} 台设备{kw} · 日期 {date or '全部'}")

    # ---------- 交互 ----------

    def _on_search(self, _=""):
        self._page_no = 1
        self._load_local()

    def _on_prev_page(self):
        if self._page_no > 1:
            self._page_no -= 1
            self._load_local()

    def _on_next_page(self):
        if self._page_no < max(1, math.ceil(self._total / self._page_size)):
            self._page_no += 1
            self._load_local()

    def _get_row_at(self, row_idx) -> dict:
        """获取表格指定行的完整数据"""
        offset = (self._page_no - 1) * self._page_size + row_idx
        keyword = self._search_edit.text().strip()
        if self._active_source() == "xqzg":
            _, rows = table_db.query_xqzg_page(offset + 1, 1, keyword)
        else:
            date = self._current_date()
            _, rows = table_db.query_kd_page(offset + 1, 1, keyword, date)
        return rows[0] if rows else {}

    def _show_context_menu(self, pos):
        idx = self._table.indexAt(pos)
        if not idx.isValid():
            return
        self._table.selectRow(idx.row())
        menu = RoundMenu(parent=self._table)

        act_files = Action(FluentIcon.LIBRARY, "查看文件列表", self._table)
        act_files.triggered.connect(lambda: self._show_files_dialog(idx.row()))
        menu.addAction(act_files)

        col_key = DEVICE_COLUMNS[idx.column()][0]
        cell_item = self._table.item(idx.row(), idx.column())
        if col_key.endswith("_count") and cell_item:
            act_copy_files = Action(FluentIcon.COPY, f"复制文件列表", self._table)
            field = col_key.replace("_count", "_files")
            act_copy_files.triggered.connect(
                lambda: self._copy_file_field(idx.row(), field))
            menu.addAction(act_copy_files)

        menu.addSeparator()
        act_copy = Action(FluentIcon.COPY, "复制单元格", self._table)
        act_copy.triggered.connect(lambda: _copy_table_selection(self._table))
        menu.addAction(act_copy)
        menu.exec_(self._table.viewport().mapToGlobal(pos))

    def _show_files_dialog(self, row_idx):
        row = self._get_row_at(row_idx)
        if row:
            DeviceFilesDialog(row, self).exec()

    def _copy_file_field(self, row_idx, field):
        row = self._get_row_at(row_idx)
        files = row.get(field) or []
        QApplication.clipboard().setText("\n".join(files))
        InfoBar.success("已复制", f"{len(files)} 个文件名已复制到剪贴板", parent=self, duration=2000)

    # ---------- 文件面板与迁移 ----------

    def _on_cell_clicked(self, row, col):
        key = DEVICE_COLUMNS[col][0]
        cfg = self._FILE_VIEW_FIELDS.get(key)
        if not cfg:
            return
        data = self._get_row_at(row)
        if not data:
            return
        title, fields = cfg
        self._file_panel.show_files(data, title, fields)

    def migrate_file(self, fname, src_cat, dest_cat):
        """迁移单个文件到目标分类（调用 migrate_image API）"""
        if self._migrate_worker and self._migrate_worker.isRunning():
            InfoBar.warning("提示", "已有迁移任务进行中，请稍候", parent=self, duration=2000)
            return
        date = self._current_date()
        device_code = self._file_panel._row.get("device_code", "")
        if not date or not device_code:
            InfoBar.warning("提示", "缺少日期或设备编码，无法迁移", parent=self, duration=2500)
            return
        self._migrate_worker = MigrateImageWorker(
            file_path=date, device_code=device_code, file_names=[fname],
            src_category=src_cat, dest_category=dest_cat)
        self._migrate_worker.success.connect(
            lambda count: self._on_migrate_ok(fname, dest_cat))
        self._migrate_worker.error.connect(self._on_migrate_fail)
        self._migrate_worker.start()
        InfoBar.info("迁移中", f"{fname} → 「{dest_cat}」...", parent=self, duration=1500)

    def _on_migrate_ok(self, fname, dest_cat):
        InfoBar.success("迁移成功", f"{fname} 已移动到「{dest_cat}」", parent=self, duration=2500)
        self._silent_refresh()

    def _on_migrate_fail(self, msg):
        InfoBar.error("迁移失败", msg.split("\n")[0], parent=self, duration=4000)
        self._silent_refresh()

    def _silent_refresh(self):
        """迁移后静默重新拉取当前日期数据，刷新表格与文件面板"""
        date = self._current_date()
        if not date:
            return
        if self._refresh_worker and self._refresh_worker.isRunning():
            return
        if self._active_source() == "xqzg":
            self._refresh_worker = SnookerOmFetchWorker(file_path=date)
        else:
            self._refresh_worker = DevicesFetchWorker(file_path=date)
        self._refresh_worker.result_ready.connect(self._on_refresh_done)
        self._refresh_worker.error.connect(self._on_refresh_error)
        self._refresh_worker.start()

    def _on_refresh_done(self, data):
        rows = data.get("lists") or data.get("results") or []
        date = self._current_date()
        if self._active_source() == "xqzg":
            table_db.save_xqzg(rows)
        else:
            table_db.save_kd(rows, date)
        self._load_local()
        self._file_panel.refresh_if_visible()

    def _on_refresh_error(self, msg):
        InfoBar.warning("刷新失败", msg, parent=self, duration=3000)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        panel = getattr(self, "_file_panel", None)
        if panel and panel.isVisible():
            panel.setFixedSize(panel.width(), self.height())
            panel.move(self.width() - panel.width(), 0)


# ==================== 页面3: 管理设置 ====================

class AdminSettingsPage(QWidget):
    """管理设置页：数据源选择、API 账号密码、连接测试

    配置写入 settings.json 的 api_credentials 节点，保存后即时生效。
    """

    # 数据源选项：(显示文本, 存储值)
    _SOURCE_OPTIONS = [
        ("kd · 球房运维后台（kd.newbv.cn:30005）", "kd"),
        ("xqzg · 新球房运维后台（xqzg.newbv.cn）", "xqzg"),
    ]
    _API_LABELS = {"api1": "接口1 xqzg", "api2": "接口2 kd"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._test_worker = None
        self._user_edits = {}
        self._pass_edits = {}
        self._test_btns = {}
        self._init_ui()
        self._load_current()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        view = QWidget()
        view.setStyleSheet("QWidget { background: transparent; }")
        scroll.setWidget(view)
        root.addWidget(scroll)

        layout = QVBoxLayout(view)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        layout.addWidget(TitleLabel("管理设置", view))
        layout.addWidget(self._build_source_card(view))
        layout.addWidget(self._build_api_card(
            view, "api1", "接口1 · xqzg", "xqzg.newbv.cn（Session 认证）"))
        layout.addWidget(self._build_api_card(
            view, "api2", "接口2 · kd", "kd.newbv.cn:30005（JWT 认证）"))

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_save = PrimaryPushButton(FluentIcon.SAVE, "保存设置", view)
        self._btn_save.setToolTip("将以上配置写入 settings.json，保存后即时生效")
        self._btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self._btn_save)
        layout.addLayout(btn_row)
        layout.addStretch(1)

    def _build_source_card(self, parent):
        card = CardWidget(parent)
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(8)
        vbox.addWidget(BodyLabel("设备状态数据源", card))
        vbox.addWidget(CaptionLabel(
            "选择「设备状态」页拉取与展示数据所使用的接口；图片迁移仅 kd 支持", card))
        self._source_combo = ComboBox(card)
        self._source_combo.addItems([text for text, _ in self._SOURCE_OPTIONS])
        self._source_combo.setFixedWidth(340)
        vbox.addWidget(self._source_combo)
        return card

    def _build_api_card(self, parent, api_key, title, desc):
        card = CardWidget(parent)
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(8)
        vbox.addWidget(BodyLabel(title, card))
        vbox.addWidget(CaptionLabel(desc, card))

        form = QFormLayout()
        form.setSpacing(8)
        user_edit = LineEdit(card)
        user_edit.setPlaceholderText("账号")
        form.addRow("账号:", user_edit)
        pass_edit = PasswordLineEdit(card)
        pass_edit.setPlaceholderText("密码")
        form.addRow("密码:", pass_edit)
        vbox.addLayout(form)
        self._user_edits[api_key] = user_edit
        self._pass_edits[api_key] = pass_edit

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        test_btn = PushButton(FluentIcon.LINK, "测试连接", card)
        test_btn.setToolTip("使用当前填写的账号密码尝试登录")
        test_btn.clicked.connect(lambda _=False, k=api_key: self._on_test(k))
        btn_row.addWidget(test_btn)
        vbox.addLayout(btn_row)
        self._test_btns[api_key] = test_btn
        return card

    # ---------- 读写配置 ----------

    def _load_current(self):
        """从 settings.json 加载当前配置填充到界面"""
        creds = _load_settings().get("api_credentials", {})
        for api_key in ("api1", "api2"):
            cfg = creds.get(api_key, {})
            self._user_edits[api_key].setText(cfg.get("username", ""))
            self._pass_edits[api_key].setText(cfg.get("password", ""))
        active = str(creds.get("active_source", "kd")).lower()
        idx = next((i for i, (_, v) in enumerate(self._SOURCE_OPTIONS) if v == active), 0)
        self._source_combo.setCurrentIndex(idx)

    def _on_save(self):
        api_credentials = {
            "api1": {
                "username": self._user_edits["api1"].text().strip(),
                "password": self._pass_edits["api1"].text(),
            },
            "api2": {
                "username": self._user_edits["api2"].text().strip(),
                "password": self._pass_edits["api2"].text(),
            },
            "active_source": self._SOURCE_OPTIONS[self._source_combo.currentIndex()][1],
        }
        try:
            _save_settings({"api_credentials": api_credentials})
            InfoBar.success("已保存", "API 配置已写入 settings.json，即时生效",
                            parent=self, duration=2500)
        except Exception as e:
            InfoBar.error("保存失败", str(e), parent=self, duration=4000)

    # ---------- 测试连接 ----------

    def _on_test(self, api_key):
        if self._test_worker and self._test_worker.isRunning():
            InfoBar.warning("提示", "已有测试进行中，请稍候", parent=self, duration=2000)
            return
        self._test_btns[api_key].setEnabled(False)
        self._test_worker = LoginTestWorker(
            api_key,
            username=self._user_edits[api_key].text().strip(),
            password=self._pass_edits[api_key].text())
        self._test_worker.success.connect(
            lambda msg, k=api_key: self._on_test_done(k, True, msg))
        self._test_worker.error.connect(
            lambda msg, k=api_key: self._on_test_done(k, False, msg))
        self._test_worker.start()

    def _on_test_done(self, api_key, ok, msg):
        self._test_btns[api_key].setEnabled(True)
        label = self._API_LABELS.get(api_key, api_key)
        if ok:
            InfoBar.success(f"{label} 连接成功", msg, parent=self, duration=2500)
        else:
            InfoBar.error(f"{label} 连接失败", msg, parent=self, duration=4000)


# ==================== 主窗口 ====================

class ManagementPanelWindow(FluentWindow):
    """运维管理面板：FluentWindow + 左侧导航 + 三个功能页面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("运维管理面板")
        self.resize(1150, 680)
        self.setMinimumSize(900, 520)

        # 创建子页面
        self.table_page = TablePage(self)
        self.table_page.setObjectName("tablePage")
        self.device_page = DevicePage(self)
        self.device_page.setObjectName("devicePage")
        self.settings_page = AdminSettingsPage(self)
        self.settings_page.setObjectName("adminSettingsPage")

        # 注册导航
        self.addSubInterface(self.table_page, FluentIcon.LIBRARY, "球桌管理")
        self.addSubInterface(self.device_page, FluentIcon.IOT, "设备状态")
        self.addSubInterface(self.settings_page, FluentIcon.SETTING, "管理设置")

        self.navigationInterface.setAcrylicEnabled(True)
        self.navigationInterface.setCurrentItem(self.table_page.objectName())

    def closeEvent(self, event):
        """关闭窗口时清理所有 Worker"""
        for page in (self.table_page, self.device_page, self.settings_page):
            for attr in ("_worker", "_migrate_worker", "_refresh_worker", "_test_worker"):
                worker = getattr(page, attr, None)
                if worker and worker.isRunning():
                    try:
                        worker.disconnect()
                    except (RuntimeError, TypeError):
                        pass
                    worker.quit()
                    worker.wait(2000)
        super().closeEvent(event)


# ==================== 独立运行入口（调试用） ====================

if __name__ == "__main__":
    import sys
    import core.acrylic_patch  # noqa: F401
    from PySide6.QtWidgets import QApplication
    from qfluentwidgets import setTheme, setThemeColor, Theme

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    setTheme(Theme.DARK)
    setThemeColor("#00BCD4", lazy=True)
    win = ManagementPanelWindow()
    win.show()
    sys.exit(app.exec())
