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
    QTextEdit, QDialog, QPushButton, QCheckBox, QTextBrowser, QTreeWidgetItem)
from PySide6.QtCore import (Qt, QItemSelectionModel, QDate, QPoint, QPropertyAnimation,
    QEasingCurve, QTimer, QEvent)
from PySide6.QtGui import QColor, QShortcut, QKeySequence, QPalette, QCursor
from qfluentwidgets import (TableWidget, SearchLineEdit, PushButton,
    PrimaryPushButton, ToolButton, FluentIcon, ComboBox, RoundMenu, CheckBox,
    Action, TransparentDropDownPushButton, LineEdit, PlainTextEdit,
    FluentWindow, NavigationItemPosition, InfoBar, ProgressBar, TitleLabel,
    BodyLabel, CaptionLabel, CalendarPicker, PasswordLineEdit, ScrollArea,
    CardWidget, setCustomStyleSheet, qconfig, isDarkTheme, MessageBox, TreeWidget,
    MessageBoxBase)
from qfluentwidgets.components.widgets.table_view import TableItemDelegate

from core.app_paths import get_app_dir
from core.frp_remote import FrpRemoteBridge
from workers.table_worker import (TableFetchWorker, DevicesFetchWorker,
                                  SnookerOmFetchWorker, MigrateImageWorker,
                                  LoginTestWorker, get_active_api_source,
                                  CATEGORY_DIRS)
from workers.collect_worker import (CollectFilesWorker, ZipUploadWorker,
                                    clip_base_name)
from database import table_db

# ==================== 常量定义 ====================

# 球桌管理列：(字段key, 表头, 宽度)
TABLE_COLUMNS = [
    ("name", "球桌号", 90),
    ("roomName", "球房名称", 200),
    ("onlineStatusName", "在线状态", 80),
    ("remark", "备注", 360),
    ("cameraPassExt", "相机密码", 220),
    ("snk_code", "SNK标识", 110),
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
    ("status", "状态", 70),
    ("pic_total", "总数", 70),
    ("normal_count", "正常", 55),
    ("except_count", "操作", 55),
    ("operation_count", "使用", 55),
    ("accuracy_count", "精度", 55),
    ("already_count", "问题", 55),
    ("rubbish_count", "废弃", 55),
    ("operation_rate", "操作率", 90),
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

# 迁移目标选项（面板底部四个文字按钮，点击直接迁移）
MIGRATE_DEST_OPTIONS = ["使用", "精度", "问题", "废弃"]

# 迁移按钮固定背景色（setCustomStyleSheet 底色 + hover/pressed 变化，深浅主题通用）
# 低饱和高级配色：使用=翡翠绿、精度=琥珀金、问题=玫瑰红、废弃=石板灰
_MIGRATE_BTN_QSS_TMPL = (
    "QPushButton {{ background-color: {base}; color: #ffffff; border: none;"
    " border-radius: 5px; font-weight: 600; padding: 5px 0; }}"
    "QPushButton:hover {{ background-color: {hover}; }}"
    "QPushButton:pressed {{ background-color: {pressed}; }}"
    "QPushButton:disabled {{ background-color: #8a8f98; color: #d5d7da; }}"
)
_MIGRATE_BTN_QSS = {
    # 翡翠绿：正常/在用类语义
    "使用": _MIGRATE_BTN_QSS_TMPL.format(
        base="#1a9e6c", hover="#22b27b", pressed="#147f56"),
    # 琥珀金：精度/校准类语义
    "精度": _MIGRATE_BTN_QSS_TMPL.format(
        base="#c98a2d", hover="#d99a3d", pressed="#a87123"),
    # 玫瑰红：问题/告警类语义
    "问题": _MIGRATE_BTN_QSS_TMPL.format(
        base="#cf4452", hover="#da5a66", pressed="#ab3641"),
    # 石板灰：废弃/归档类语义
    "废弃": _MIGRATE_BTN_QSS_TMPL.format(
        base="#5c6675", hover="#6b7585", pressed="#4a5361"),
}

# 可迁移的文件分类字段（其余分类点开后仅查看，不显示迁移按钮）
_MIGRATABLE_FIELDS = {
    "except_files", "operation_files", "accuracy_files", "already_files", "rubbish_files",
}

# 可点击查看文件列表的单元格链接色
_LINK_COLOR = QColor(0, 120, 212)

# 设备状态码 → (中文描述, 颜色)
# kd 接口 status 字段：0=下线 1=空闲 2=使用
_DEVICE_STATUS_MAP = {
    "0": ("下线", QColor("#000000")),
    "1": ("空闲", QColor("#fa8c16")),
    "2": ("使用", QColor("#52c41a")),
}


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


def _fmt_size(n: float) -> str:
    """字节数格式化为人类可读大小（B/KB/MB/GB）"""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# ==================== 通用组件 ====================

class _SortableTableWidget(TableWidget):
    """服务端分页排序表格：拦截客户端排序，改由页面携带排序参数重查数据库

    设备状态数据由 SQLite 分页查询（LIMIT/OFFSET），若用 QTableView 默认的
    客户端排序只会重排当前页，跨页数据错乱。重写 sortByColumn 拦截一切
    客户端模型排序（含 Qt 内部 C++ 路径），只更新表头箭头。
    注意：排序入口必须走 _on_header_clicked 直连路径，不能用 sortByColumn
    触发（Qt 内部 C++ 调用不走 Python 重写，会直接排序模型）。
    """

    def sortByColumn(self, column, order):
        # 永远不做客户端排序，仅同步表头箭头
        hh = self.horizontalHeader()
        hh.setSortIndicatorShown(column >= 0)
        if column >= 0:
            hh.setSortIndicator(column, order)

    def setSortIndicator(self, column, order):
        """仅更新表头箭头（不触发任何排序）"""
        self.sortByColumn(column, order)


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
        self.setFixedSize(420, 356)
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
        self._edit_snk = LineEdit(self)
        self._edit_snk.setPlaceholderText("如 snk_001（留空则从备注解析）")
        form.addRow("SNK标识:", self._edit_snk)
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
            "snk_code": self._edit_snk.text().strip(),
        }


class EditSnkDialog(MessageBoxBase):
    """SNK 标识手动写入/修改对话框（留空保存即清空）"""

    def __init__(self, parent, table_name: str, current: str):
        super().__init__(parent)
        self.titleLabel = BodyLabel(f"修改 SNK 标识 · 球桌 {table_name}", self)
        self.viewLayout.addWidget(self.titleLabel)
        self.edit = LineEdit(self)
        self.edit.setText(current)
        self.edit.setPlaceholderText("如 snk_001（留空则清空）")
        self.edit.setMinimumWidth(280)
        self.viewLayout.addWidget(self.edit)


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


class UploadListDialog(QDialog):
    """上传清单弹窗：树形展示 {videos_dir}/upload 下待上传文件（设备→文件）"""

    def __init__(self, upload_root: str, parent=None):
        super().__init__(parent)
        self._upload_root = upload_root
        self.setWindowTitle("上传清单")
        self.resize(560, 460)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(8)
        layout.addWidget(BodyLabel(f"收集目录: {upload_root}", self))

        self._tree = TreeWidget(self)
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(["文件", "大小"])
        self._tree.setAlternatingRowColors(True)
        self._tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._tree, 1)

        bottom = QHBoxLayout()
        self._lbl_total = CaptionLabel("", self)
        bottom.addWidget(self._lbl_total)
        bottom.addStretch(1)
        btn_open = PushButton(FluentIcon.FOLDER, "打开目录", self)
        btn_open.clicked.connect(self._open_dir)
        bottom.addWidget(btn_open)
        btn_close = PushButton("关闭", self)
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        layout.addLayout(bottom)

        # 填充放最后：_populate 需要 _lbl_total 已存在
        self._populate()

    def _populate(self):
        total_files = 0
        total_size = 0
        try:
            entries = sorted(os.listdir(self._upload_root))
        except OSError:
            entries = []
        for dev in entries:
            dev_dir = os.path.join(self._upload_root, dev)
            if not os.path.isdir(dev_dir):
                continue
            dev_item = QTreeWidgetItem([dev, ""])
            dev_size = 0
            for fname in sorted(os.listdir(dev_dir)):
                full = os.path.join(dev_dir, fname)
                if not os.path.isfile(full):
                    continue
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                dev_size += size
                total_files += 1
                dev_item.addChild(QTreeWidgetItem([fname, _fmt_size(size)]))
            dev_item.setText(1, _fmt_size(dev_size))
            dev_item.setExpanded(True)
            self._tree.addTopLevelItem(dev_item)
            total_size += dev_size
        self._lbl_total.setText(f"共 {total_files} 个文件，总大小 {_fmt_size(total_size)}")

    def _open_dir(self):
        if os.path.isdir(self._upload_root):
            os.startfile(self._upload_root)

    @staticmethod
    def file_count(upload_root: str) -> int:
        """清单中的文件总数（供外部确认弹窗展示）"""
        count = 0
        try:
            entries = os.listdir(upload_root)
        except OSError:
            return 0
        for dev in entries:
            dev_dir = os.path.join(upload_root, dev)
            if os.path.isdir(dev_dir):
                count += sum(1 for f in os.listdir(dev_dir)
                             if os.path.isfile(os.path.join(dev_dir, f)))
        return count


# ==================== 页面1: 球桌管理 ====================

class TablePage(QWidget):
    """球桌管理页（复用原 TablePanelWindow 全部功能逻辑）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page_no = 1
        self._page_size = 50
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
        self._search_edit.setPlaceholderText("搜索")
        self._search_edit.setFixedWidth(280)
        self._search_edit.textChanged.connect(self._on_search)
        toolbar.addWidget(self._search_edit)
        toolbar.addStretch(1)

        self._col_btn = TransparentDropDownPushButton("筛选", self)
        self._col_btn.setIcon(FluentIcon.FILTER.qicon())
        self._build_col_menu()
        toolbar.addWidget(self._col_btn)

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
        self._size_combo.addItems(["50", "100", "300", "800", "1000"])
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
        pager.addStretch(1)
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
        self._lbl_info.setText(f"共 {self._total} 条{kw_tip}")

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

    def _show_copy_menu(self, pos):
        idx = self._table.indexAt(pos)
        if not self._table.selectedItems():
            if not idx.isValid():
                return
            self._table.selectionModel().select(
                idx, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        menu = RoundMenu(parent=self._table)
        act = Action(FluentIcon.COPY, "复制", self._table)
        act.triggered.connect(lambda: _copy_table_selection(self._table))
        menu.addAction(act)
        # 右键 SNK 标识列：支持手动写入/修改（无 snk 设备的远程入口依赖此值）
        if idx.isValid() and TABLE_COLUMNS[idx.column()][0] == "snk_code":
            row = idx.row()
            name_item = self._table.item(row, 0)
            table_name = name_item.text().strip() if name_item else ""
            if table_name:
                snk_item = self._table.item(row, idx.column())
                current = snk_item.text().strip() if snk_item else ""
                act_snk = Action(FluentIcon.EDIT, "修改 SNK 标识", self._table)
                act_snk.triggered.connect(
                    lambda _=False, n=table_name, c=current: self._edit_snk(n, c))
                menu.addAction(act_snk)
        menu.exec_(self._table.viewport().mapToGlobal(pos))

    def _edit_snk(self, table_name, current):
        """弹窗手动写入/修改指定球桌的 snk 标识，保存后刷新当前页"""
        dlg = EditSnkDialog(self, table_name, current)
        dlg.yesButton.setText("保存")
        dlg.cancelButton.setText("取消")
        if not dlg.exec():
            return
        new_snk = dlg.edit.text().strip()
        affected = table_db.update_snk_by_name(table_name, new_snk)
        if affected:
            self._load_local()
            InfoBar.success("已保存",
                            f"球桌「{table_name}」SNK 标识已更新为「{new_snk or '空'}」",
                            parent=self, duration=2500)
        else:
            InfoBar.warning("未修改", "未找到匹配的球桌记录", parent=self, duration=2500)


# ==================== 文件列表面板（右侧滑出） ====================

class FileListPanel(QWidget):
    """设备状态页右侧滑出面板：文件名单列展示，双击编辑复制，Ctrl+C/右键复制，底部四按钮迁移"""

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
        # 背景不能用 palette(...)：Qt 只在 setStyleSheet 当时解析一次，主题切换后
        # 不会重新解析（面板会残留旧主题背景）。
        # 也不能用 setCustomStyleSheet：它只写动态属性，实际应用依赖
        # CustomStyleSheetWatcher，而该 watcher 仅在控件注册过 styleSheetManager
        # （qfluentwidgets 组件自带 QSS）时才会安装，普通 QWidget 上永远不生效
        # （导致背景透明）。
        # 正确做法：直接 setStyleSheet 显式色值 + 监听 qconfig.themeChanged 重应用。
        qconfig.themeChanged.connect(self._apply_theme)
        self._apply_theme()

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
        self._list.setColumnCount(1)
        self._list.setHorizontalHeaderLabels(["文件名"])
        self._list.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._list.verticalHeader().setVisible(False)
        self._list.setAlternatingRowColors(True)
        self._list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # 双击进入只读编辑态：可拖选部分文件名复制（与球桌管理一致）
        self._list.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self._list.setItemDelegate(_ReadOnlySelectDelegate(self._list))
        # 单击选中行 → 启用底部迁移按钮
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_context_menu)
        # Ctrl+C：编辑态复制选中文本；选中行复制文件名（截取 kd 前缀）
        QShortcut(QKeySequence.StandardKey.Copy, self._list).activated.connect(
            self._on_copy_shortcut)
        layout.addWidget(self._list, 1)

        # 底部四个迁移目标图标按钮（选中条目后可用，点击直接迁移）
        # 容器可整体隐藏：总数/正常等不可迁移的视图不显示迁移按钮
        self._migrate_wrap = QWidget(self)
        self._migrate_wrap.hide()
        btn_row = QHBoxLayout(self._migrate_wrap)
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(6)
        self._migrate_btns = {}
        for dest in MIGRATE_DEST_OPTIONS:
            btn = PushButton(dest, self)
            qss = _MIGRATE_BTN_QSS.get(dest, "")
            if qss:
                setCustomStyleSheet(btn, qss, qss)
            btn.setToolTip(f"将选中的文件迁移到「{dest}」")
            btn.setEnabled(False)
            btn.clicked.connect(lambda _=False, d=dest: self._on_migrate_btn(d))
            self._migrate_btns[dest] = btn
            btn_row.addWidget(btn, 1)
        layout.addWidget(self._migrate_wrap)

        hint = CaptionLabel("双击或右键进行复制", self)
        layout.addWidget(hint)

    def _apply_theme(self):
        """按当前主题应用面板背景（显式色值，不依赖 palette 引用）"""
        if isDarkTheme():
            self.setStyleSheet(
                "QWidget#fileListPanel { background: #2b2b2b;"
                " border-left: 1px solid #3f3f3f; }")
        else:
            self.setStyleSheet(
                "QWidget#fileListPanel { background: #ffffff;"
                " border-left: 1px solid #e0e0e0; }")

    # ---------- 展示 ----------

    def show_files(self, row: dict, title: str, fields: list, can_migrate: bool = True):
        self._row = row
        self._title = title
        self._fields = fields
        self._migrate_wrap.setVisible(can_migrate)
        if not can_migrate:
            for btn in self._migrate_btns.values():
                btn.setEnabled(False)
        self._reload_entries()
        self.slide_in()

    def _reload_entries(self):
        self._entries = []
        for field in self._fields:
            src_cat = FIELD_CATEGORY.get(field, "")
            for fname in (self._row.get(field) or []):
                # 保留 (文件名, 源分类) 二元组：src_cat 仅迁移时使用，不在界面展示
                self._entries.append((str(fname), src_cat))
        self._list.setRowCount(len(self._entries))
        for r, (fname, _src_cat) in enumerate(self._entries):
            name_item = QTableWidgetItem(fname)
            name_item.setToolTip(fname)
            self._list.setItem(r, 0, name_item)
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

    # ---------- 复制与迁移交互 ----------

    def _on_copy_shortcut(self):
        """Ctrl+C：编辑态优先复制光标选中部分，否则复制选中行文件名（截取 kd 前缀）"""
        focus = QApplication.focusWidget()
        if focus is not None and self._list.isAncestorOf(focus):
            tc = getattr(focus, "textCursor", None)
            if tc is not None and tc.hasSelection():
                QApplication.clipboard().setText(
                    tc.selectedText().replace("\u2029", "\n"))
                return
        rows = sorted({it.row() for it in self._list.selectedItems()})
        if rows:
            item = self._list.item(rows[0], 0)
            if item:
                QApplication.clipboard().setText(self._clip_name(item.text()))

    def _on_selection_changed(self):
        """选中状态变化：有选中条目时启用底部迁移按钮"""
        rows = sorted({it.row() for it in self._list.selectedItems()})
        enabled = bool(rows)
        for btn in self._migrate_btns.values():
            btn.setEnabled(enabled)

    def _on_migrate_btn(self, dest_cat):
        """底部迁移按钮：将当前选中的文件直接迁移到 dest_cat"""
        rows = sorted({it.row() for it in self._list.selectedItems()})
        if not rows or not (0 <= rows[0] < len(self._entries)):
            return
        fname, src_cat = self._entries[rows[0]]
        self._device_page.migrate_file(fname, src_cat, dest_cat)

    def _show_context_menu(self, pos):
        """右键条目：复制文件名 / 复制全部（迁移已移至底部固定按钮）"""
        row = self._list.rowAt(pos.y())
        if not (0 <= row < len(self._entries)):
            return
        self._list.selectRow(row)
        fname, _src_cat = self._entries[row]
        menu = RoundMenu(parent=self)

        act_copy = Action(FluentIcon.COPY, "复制文件名", self)
        act_copy.triggered.connect(
            lambda: QApplication.clipboard().setText(self._clip_name(fname)))
        menu.addAction(act_copy)

        act_copy_all = Action(FluentIcon.COPY, "复制全部文件名", self)
        act_copy_all.triggered.connect(self._copy_all_names)
        menu.addAction(act_copy_all)

        menu.exec_(self._list.viewport().mapToGlobal(pos))

    def _clip_name(self, fname: str) -> str:
        """复制时截取文件名 'kd' 之前的字符

        设备照片文件名形如 20260802_125112kd-200055-20046-100835.jpg，
        复制时通常只需要时间戳部分（kd 前）。不含 'kd' 的文件名原样返回。
        """
        fname = str(fname)
        idx = fname.find("kd")
        return fname[:idx] if idx > 0 else fname

    def _copy_all_names(self):
        """复制面板中全部文件名到剪贴板（每行一个，均截取 kd 前缀）"""
        names = [self._clip_name(f) for f, _ in self._entries]
        QApplication.clipboard().setText("\n".join(names))
        InfoBar.success("已复制", f"{len(names)} 个文件名已复制到剪贴板（已截取 kd 前缀）",
                        parent=self, duration=2000)

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
    # pic_total 为全部文件总数，列出所有分类
    _FILE_VIEW_FIELDS = {
        "pic_total": ("全部文件", [f for f, _ in FILE_FIELD_CATEGORIES]),
        "normal_count": ("正常文件", ["normal_files"]),
        "except_count": ("操作文件", ["except_files"]),
        "operation_count": ("使用文件", ["operation_files"]),
        "accuracy_count": ("精度文件", ["accuracy_files"]),
        "already_count": ("问题文件", ["already_files"]),
        "rubbish_count": ("废弃文件", ["rubbish_files"]),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page_no = 1
        self._page_size = 50
        self._total = 0
        self._worker = None
        self._migrate_worker = None
        self._refresh_worker = None
        self._hourly_worker = None  # 每小时定时拉取专用，与手动搜索 _worker 隔离
        self._collect_workers = []   # 收集 Worker 列表（不同设备可并行）
        self._upload_worker = None   # 打包上传 Worker（全局唯一）
        self._init_ui()
        # 默认日期为昨天（与主窗口一致）
        yesterday = QDate.currentDate().addDays(-1)
        self._date_picker.blockSignals(True)
        self._date_picker.setDate(yesterday)
        self._date_picker.blockSignals(False)
        # 根据数据源调整日期选择器可用状态（xqzg 不按日期区分）
        self._apply_source_date_state()
        # 复用缓存日历：替换 DatePicker._showCalendarView，避免每次点击重建（0.5s+ 延迟）
        self._apply_calendar_cache()
        # 每小时定时拉取当天设备状态（仅 kd 数据源），保持状态字段时效性
        self._hourly_timer = QTimer(self)
        self._hourly_timer.setInterval(3600 * 1000)
        self._hourly_timer.timeout.connect(self._hourly_refresh)
        if self._active_source() == "kd":
            self._hourly_timer.start()
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
        self._search_edit.setPlaceholderText("搜索")
        self._search_edit.setFixedWidth(160)
        self._search_edit.textChanged.connect(self._on_search)
        toolbar.addWidget(self._search_edit)
        toolbar.addStretch(1)

        self._btn_upload_list = PushButton(FluentIcon.LIBRARY, "上传清单", self)
        self._btn_upload_list.setToolTip("查看已收集待上传的文件（videos_dir/upload）")
        self._btn_upload_list.clicked.connect(self._show_upload_list)
        toolbar.addWidget(self._btn_upload_list)

        self._btn_package = PushButton(FluentIcon.SEND, "打包上传", self)
        self._btn_package.setToolTip("将收集的文件打包 zip 上传服务器，成功后清空本地 upload 目录")
        self._btn_package.clicked.connect(self._on_package_upload)
        toolbar.addWidget(self._btn_package)

        self._sync_btn = PushButton(FluentIcon.SYNC, "同步数据", self)
        self._sync_btn.setToolTip("从服务器拉取所选日期的设备数据")
        self._sync_btn.clicked.connect(self._search_from_api)
        toolbar.addWidget(self._sync_btn)
        root.addLayout(toolbar)

        # --- 表格 ---
        self._table = _SortableTableWidget(self)
        self._sort_key = ""   # 当前排序字段（DEVICE_COLUMNS key），空=默认 id 顺序
        self._sort_desc = False
        self._table.setColumnCount(len(DEVICE_COLUMNS))
        self._table.setHorizontalHeaderLabels([c[1] for c in DEVICE_COLUMNS])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self._table.setItemDelegate(_ReadOnlySelectDelegate(self._table))
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        # 点击表头排序：直连 sectionClicked 手动处理（更新箭头 + SQL 重查），
        # 绝不走 QTableView 内置排序链路（客户端排序只重排当前页，且 Qt 内部
        # C++ 路径会绕过 Python 重写直接排序模型）
        self._table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._table.cellClicked.connect(self._on_cell_clicked)
        QShortcut(QKeySequence.StandardKey.Copy, self._table).activated.connect(
            lambda: _copy_table_selection(self._table))
        # 悬停可点击的文件统计列时切换为手型光标，提示该单元格可点击
        viewport = self._table.viewport()
        viewport.setMouseTracking(True)
        viewport.installEventFilter(self)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for i, (_, _, w) in enumerate(DEVICE_COLUMNS):
            self._table.setColumnWidth(i, w)
        root.addWidget(self._table, 1)

        # --- 状态栏（统一格式：共X条 | 每页:下拉 | 页码 | 数据时间） ---
        pager = QHBoxLayout()
        pager.setSpacing(6)
        self._lbl_info = QLabel("", self)
        pager.addWidget(self._lbl_info)
        pager.addStretch(1)
        pager.addWidget(QLabel("每页:", self))
        self._size_combo = ComboBox(self)
        self._size_combo.addItems(["50", "100", "300", "800", "1000"])
        self._size_combo.setCurrentText(str(self._page_size))
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
        pager.addStretch(1)
        self._lbl_time = QLabel("", self)
        pager.addWidget(self._lbl_time)
        root.addLayout(pager)

        # --- 右侧滑出文件面板 ---
        self._file_panel = FileListPanel(self)

    def _apply_calendar_cache(self):
        """复用缓存 CalendarView：替换 DatePicker._showCalendarView 为快速显示，避免每次点击重建"""
        try:
            from qfluentwidgets.components.date_time.calendar_view import CalendarView
            picker = self._date_picker
            cached_view = CalendarView(self.window())
            cached_view.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
            cached_view.hide()

            def _fast_show_calendar_view():
                import warnings
                cached_view.setResetEnabled(picker.isRestEnabled())
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    cached_view.resetted.disconnect()
                    cached_view.dateChanged.disconnect()
                cached_view.resetted.connect(picker.reset)
                cached_view.dateChanged.connect(picker._onDateChanged)
                if picker.date.isValid():
                    cached_view.setDate(picker.date)
                x = int(picker.width() / 2 - cached_view.sizeHint().width() / 2)
                y = picker.height()
                cached_view.exec(picker.mapToGlobal(QPoint(x, y)))

            picker._showCalendarView = _fast_show_calendar_view
            picker._cached_calendar_view = cached_view
        except Exception:
            pass

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

    def _apply_source_date_state(self):
        """按数据源设置日期选择器可用状态

        xqzg 接口数据不按日期存储/筛选（xqzg_status 表无 file_path 列），
        禁用日期选择器避免“切日期却看到同一份数据”的误解。
        """
        is_xqzg = (self._active_source() == "xqzg")
        self._date_picker.setEnabled(not is_xqzg)
        if is_xqzg:
            self._date_picker.setToolTip("xqzg 数据源不按日期区分，日期选择不可用")
        else:
            self._date_picker.setToolTip("")
        # 同步管理每小时定时拉取：仅 kd 数据源启用（xqzg 无 status 时效需求）
        timer = getattr(self, "_hourly_timer", None)
        if timer is not None:
            if is_xqzg:
                timer.stop()
            elif not timer.isActive():
                timer.start()

    def showEvent(self, event):
        """页面显示时刷新数据源状态（数据源可能在管理设置页被切换）"""
        super().showEvent(event)
        self._apply_source_date_state()
        self._load_local()

    def _load_local(self):
        keyword = self._search_edit.text().strip()
        if self._active_source() == "xqzg":
            total, rows = table_db.query_xqzg_page(
                self._page_no, self._page_size, keyword,
                self._sort_key, self._sort_desc)
            date = ""  # xqzg 不按日期筛选
        else:
            date = self._current_date()
            total, rows = table_db.query_kd_page(
                self._page_no, self._page_size, keyword, date,
                self._sort_key, self._sort_desc)
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
        self._lbl_info.setText(f"正在从 {src} 搜索 {date} 的设备数据")
        if src == "xqzg":
            self._worker = SnookerOmFetchWorker(file_path=date)
        else:
            self._worker = DevicesFetchWorker(file_path=date)
        self._worker.result_ready.connect(self._on_search_done)
        self._worker.error.connect(self._on_search_error)
        self._worker.start()

    def _on_search_done(self, data):
        rows = data.get("lists") or data.get("results") or []
        if self._active_source() == "xqzg":
            count = table_db.save_xqzg(rows)
            date_desc = "全部日期"
        else:
            date = self._current_date()
            count = table_db.save_kd(rows, date)
            date_desc = date
        self._sync_btn.setEnabled(True)
        self._page_no = 1
        self._load_local()
        InfoBar.success("搜索完成", f"{date_desc} 共 {count} 台设备", parent=self, duration=2500)

    def _on_search_error(self, msg):
        self._sync_btn.setEnabled(True)
        self._lbl_info.setText(f"搜索失败: {msg}")
        InfoBar.error("搜索失败", msg, parent=self, duration=4000)

    def _populate(self, rows):
        # 缓存当前页数据，供 _get_row_at 直接按行号取用，避免每次点击都重查数据库
        self._current_rows = rows
        self._table.setRowCount(len(rows))
        for r, item in enumerate(rows):
            for c, (key, _, _) in enumerate(DEVICE_COLUMNS):
                val = item.get(key)
                if key == "status":
                    # 设备状态码 → 中文 + 颜色标识
                    text, color = _DEVICE_STATUS_MAP.get(str(val).strip(), ("未知", None))
                    cell = QTableWidgetItem(text)
                    cell.setToolTip(f"设备状态: {text}")
                    if color is not None:
                        cell.setForeground(color)
                    self._table.setItem(r, c, cell)
                    continue
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

    def _on_header_clicked(self, column):
        """表头点击：同列切换升/降序，新列默认升序；更新箭头后重查数据库"""
        if not (0 <= column < len(DEVICE_COLUMNS)):
            return
        key = DEVICE_COLUMNS[column][0]
        if key == self._sort_key:
            desc = not self._sort_desc
        else:
            desc = False
        order = (Qt.SortOrder.DescendingOrder if desc
                 else Qt.SortOrder.AscendingOrder)
        self._table.setSortIndicator(column, order)
        if key == self._sort_key and desc == self._sort_desc:
            return
        self._sort_key = key
        self._sort_desc = desc
        self._page_no = 1
        self._load_local()

    def _update_pager(self, date="", keyword=""):
        total_pages = max(1, math.ceil(self._total / self._page_size))
        self._page_no = min(self._page_no, total_pages)
        self._lbl_page.setText(f"{self._page_no}/{total_pages}")
        self._btn_prev.setEnabled(self._page_no > 1)
        self._btn_next.setEnabled(self._page_no < total_pages)
        kw = f"（搜索: {keyword}）" if keyword else ""
        self._lbl_info.setText(f"共 {self._total} 台设备{kw}")
        self._lbl_time.setText(f"数据时间: {date or '全部'}")

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

    def _get_row_at(self, row_idx) -> dict:
        """获取表格指定行的完整数据（直接取当前页缓存，不再重查数据库）"""
        rows = getattr(self, '_current_rows', None) or []
        if 0 <= row_idx < len(rows):
            return rows[row_idx]
        return {}

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

        # 远程连接：按球桌号关联球桌管理 remark 中的 snk 标识（frp xtcp
        # visitor serverName），无 snk 的设备菜单项保留可见但置灰并说明原因
        self._add_remote_actions(menu, idx.row())
        menu.exec_(self._table.viewport().mapToGlobal(pos))

    def _add_remote_actions(self, menu, row_idx):
        """右键菜单追加远程连接入口（SSH 终端 / SFTP 文件 / 远程桌面）"""
        row = self._get_row_at(row_idx)
        table_id = str(row.get("table_id") or "").strip()
        snk = table_db.get_snk_by_name(table_id)
        menu.addSeparator()
        remote_items = [
            ("ssh", FluentIcon.COMMAND_PROMPT, "SSH 终端"),
            ("sftp", FluentIcon.FOLDER, "SFTP 文件管理"),
            ("rdp", FluentIcon.VIDEO, "远程桌面"),
        ]
        if not snk:
            # 置灰但可见：提示功能存在，仅该设备缺 snk 配置不可用
            tip = Action(FluentIcon.INFO, "该设备无 snk 标识，无法远程", self._table)
            tip.setEnabled(False)
            menu.addAction(tip)
        for kind, icon, label in remote_items:
            act = Action(icon, label if snk else f"{label}（无 snk）", self._table)
            act.setEnabled(bool(snk))
            if snk:
                act.triggered.connect(
                    lambda _=False, k=kind: self._open_remote_session(k, snk, table_id))
            menu.addAction(act)

    def _open_remote_session(self, kind, snk, table_id):
        """委托运维面板窗口的 FrpRemoteBridge 建立 xtcp 隧道并打开会话"""
        bridge = getattr(self.window(), "_remote_bridge", None)
        if bridge is None:
            InfoBar.error("无法远程", "远程桥接未初始化", parent=self, duration=3000)
            return
        bridge.open_session(kind, snk, table_id)

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

    def eventFilter(self, obj, event):
        """悬停在可点击的文件统计列上时显示手型光标，移出恢复默认箭头"""
        if obj is self._table.viewport() and event.type() == QEvent.Type.MouseMove:
            idx = self._table.indexAt(event.position().toPoint())
            if idx.isValid() and DEVICE_COLUMNS[idx.column()][0] in self._FILE_VIEW_FIELDS:
                self._table.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self._table.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        return super().eventFilter(obj, event)

    def _on_cell_clicked(self, row, col):
        key = DEVICE_COLUMNS[col][0]
        cfg = self._FILE_VIEW_FIELDS.get(key)
        if not cfg:
            return
        data = self._get_row_at(row)
        if not data:
            return
        title, fields = cfg
        can_migrate = bool(fields) and all(f in _MIGRATABLE_FIELDS for f in fields)
        self._file_panel.show_files(data, title, fields, can_migrate=can_migrate)
        # 点击精度/问题后自动收集该设备的视频/日志/CPP日志/detect.bin 到 upload 工作区
        if key in ("accuracy_count", "already_count"):
            field = "accuracy_files" if key == "accuracy_count" else "already_files"
            self._auto_collect(data, field)

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

    # ---------- 收集与上传 ----------

    def _upload_root(self) -> str:
        """返回上传收集目录 {videos_dir}/upload；未配置 videos_dir 时提示并返回空串"""
        videos_dir = (_load_settings().get("videos_dir") or "").strip()
        if not videos_dir:
            InfoBar.warning("提示", "未配置 videos_dir，请先在设置中配置视频/日志目录",
                            parent=self, duration=3000)
            return ""
        return os.path.join(videos_dir, "upload")

    def _auto_collect(self, row: dict, field: str):
        """点击精度/问题后自动收集设备文件到 upload 工作区

        视频/日志按文件列表的基础名收集；detect.bin 与 CPP 日志（daily_*.txt）
        只收集一次（目标已存在即跳过）。设备目录优先按 table_id 匹配，
        失败回退 device_code。
        """
        videos_dir = (_load_settings().get("videos_dir") or "").strip()
        if not videos_dir or not os.path.isdir(videos_dir):
            InfoBar.warning("无法收集", "videos_dir 未配置或目录不存在",
                            parent=self, duration=3000)
            return
        candidates = [str(row.get("table_id") or "").strip(),
                      str(row.get("device_code") or "").strip()]
        device_id = next((n for n in dict.fromkeys(candidates)
                          if n and os.path.isdir(os.path.join(videos_dir, n))), "")
        if not device_id:
            InfoBar.warning("无法收集",
                            "本地设备目录不存在: " + " / ".join(c for c in candidates if c),
                            parent=self, duration=3000)
            return
        bases = sorted({b for b in (clip_base_name(f) for f in (row.get(field) or [])) if b})
        if not bases:
            return
        worker = CollectFilesWorker(videos_dir, device_id, bases)
        worker.done.connect(
            lambda dev, n, miss, w=worker: self._on_collect_done(dev, n, miss, w))
        worker.error.connect(
            lambda msg: InfoBar.error("收集失败", msg.split(chr(10))[0],
                                      parent=self, duration=4000))
        self._collect_workers.append(worker)
        worker.start()
        InfoBar.info("收集中",
                     f"{device_id} · {len(bases)} 个视频/日志 → upload 目录",
                     parent=self, duration=1500)

    def _on_collect_done(self, device_id, copied, missing, worker):
        if worker in self._collect_workers:
            self._collect_workers.remove(worker)
        if missing:
            shown = ", ".join(missing[:3]) + (" ..." if len(missing) > 3 else "")
            InfoBar.warning("收集完成",
                            f"{device_id}: 复制 {copied} 个（已存在跳过），缺失 {len(missing)} 个: {shown}",
                            parent=self, duration=4000)
        else:
            InfoBar.success("收集完成",
                            f"{device_id}: 复制 {copied} 个文件到 upload 目录（已存在跳过）",
                            parent=self, duration=3000)

    def _show_upload_list(self):
        root = self._upload_root()
        if not root:
            return
        if not os.path.isdir(root) or not os.listdir(root):
            InfoBar.info("上传清单", "暂无待上传文件，请先点击精度/问题收集文件",
                         parent=self, duration=3000)
            return
        UploadListDialog(root, self).exec()

    def _on_package_upload(self):
        """打包 upload 目录为 zip 并 SFTP 上传，成功后清空本地目录"""
        root = self._upload_root()
        if not root:
            return
        if not os.path.isdir(root) or not os.listdir(root):
            InfoBar.info("提示", "upload 目录为空，请先点击精度/问题收集文件",
                         parent=self, duration=3000)
            return
        if self._upload_worker is not None and self._upload_worker.isRunning():
            InfoBar.warning("提示", "已有上传进行中，请稍候", parent=self, duration=2000)
            return
        settings = _load_settings()
        host = str(settings.get("upload_host") or "49.235.34.253").strip()
        try:
            port = int(settings.get("upload_port") or 22)
        except (TypeError, ValueError):
            port = 22
        remote_dir = str(settings.get("upload_remote_dir") or "/lhcos-data/videos").strip()
        username = settings.get("ssh_user", "")
        password = settings.get("ssh_pass", "")

        count = UploadListDialog.file_count(root)
        box = MessageBox(
            "打包上传",
            f"将把 upload 目录中的 {count} 个文件打包为 zip，上传到\n"
            f"{host}:{remote_dir}\n\n上传成功后将清空本地 upload 目录，确定继续？",
            self)
        box.yesButton.setText("上传")
        box.cancelButton.setText("取消")
        if not box.exec():
            return

        self._btn_package.setEnabled(False)
        self._upload_worker = ZipUploadWorker(
            root, host, port, username, password, remote_dir)
        # 阶段提示显示在底部状态栏，避免多个 InfoBar 叠加
        self._upload_worker.progress.connect(self._lbl_time.setText)
        self._upload_worker.done.connect(self._on_upload_done)
        self._upload_worker.error.connect(self._on_upload_fail)
        self._upload_worker.start()

    def _on_upload_done(self, info):
        self._btn_package.setEnabled(True)
        self._lbl_time.setText("")
        InfoBar.success("上传成功", f"{info} · 本地 upload 目录已清空",
                        parent=self, duration=5000)

    def _on_upload_fail(self, msg):
        self._btn_package.setEnabled(True)
        self._lbl_time.setText("")
        InfoBar.error("上传失败", msg.split(chr(10))[0], parent=self, duration=5000)

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

    # ---------- 每小时定时拉取 ----------

    def _hourly_refresh(self):
        """每小时自动拉取当天 kd 设备状态（静默，不打扰用户）

        设备 status（空闲/下线/使用）时效性要求高，定时拉取当天分区保持新鲜。
        仅拉取并保存；若当前日期选择器正是当天，则顺带刷新表格显示。
        """
        if self._active_source() != "kd":
            return
        if self._hourly_worker and self._hourly_worker.isRunning():
            return
        today = QDate.currentDate().toString("yyyy/MM/dd")
        self._hourly_worker = DevicesFetchWorker(file_path=today)
        self._hourly_worker.result_ready.connect(self._on_hourly_done)
        self._hourly_worker.error.connect(self._on_hourly_error)
        self._hourly_worker.start()

    def _on_hourly_done(self, data):
        rows = data.get("lists") or data.get("results") or []
        today = QDate.currentDate().toString("yyyy/MM/dd")
        count = table_db.save_kd(rows, today)
        # 仅当用户正在查看当天时刷新表格，避免打断其在历史日期上的浏览
        if self._current_date() == today:
            self._load_local()
        self._lbl_time.setText(f"自动更新: {datetime.now().strftime('%H:%M:%S')}（{count} 台）")

    def _on_hourly_error(self, msg):
        # 定时拉取失败不打断用户，仅在状态栏静默提示
        self._lbl_time.setText(f"自动更新失败: {msg.split(chr(10))[0]}")

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
        # 滚动区自身不参与焦点：避免点击后焦点转移触发 ensureVisible 自动滚动（画面跳动）
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
            # （Session 认证）
            view, "api1", "接口1 · xqzg", "xqzg.newbv.cn"))
        layout.addWidget(self._build_api_card(
            # （JWT 认证）
            view, "api2", "接口2 · kd", "kd.newbv.cn:30005"))
        layout.addWidget(self._build_add_card(view))

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_save = PrimaryPushButton(FluentIcon.SAVE, "保存设置", view)
        self._btn_save.setToolTip("将以上配置写入 settings.json，保存后即时生效")
        self._btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self._btn_save)
        layout.addLayout(btn_row)
        layout.addStretch(1)

    def _on_add_record(self):
        """从内嵌表单读取 → 写入本地数据库 → 刷新球桌管理页"""
        name = self._add_edit_name.text().strip()
        if not name:
            self._add_edit_name.setPlaceholderText("球桌号不能为空")
            self._add_edit_name.setFocus()
            return
        record = {
            "name": name,
            "roomName": self._add_edit_room.text().strip(),
            "onlineStatusName": "",
            "remark": self._add_edit_remark.toPlainText().strip(),
            "cameraPassExt": self._add_edit_camera.text().strip(),
            "snk_code": self._add_edit_snk.text().strip(),
        }
        table_db.insert_one(record)
        self._add_edit_name.clear()
        self._add_edit_room.clear()
        self._add_edit_camera.clear()
        self._add_edit_snk.clear()
        self._add_edit_remark.clear()
        win = self.window()
        page = getattr(win, "table_page", None)
        if page is not None:
            page._page_no = 1
            page._load_local()
        InfoBar.success("添加成功", f"球桌「{name}」已写入本地数据库", parent=self, duration=2000)

    def _build_add_card(self, parent):
        """手动添加球桌记录：内嵌表单卡片，无需弹窗"""
        card = CardWidget(parent)
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(8)
        vbox.addWidget(BodyLabel("手动添加球桌记录", card))
        vbox.addWidget(CaptionLabel(
            "直接在下方填写并提交，写入本地数据库（下次同步可能被接口数据覆盖）", card))

        form = QFormLayout()
        form.setSpacing(8)
        self._add_edit_name = LineEdit(card)
        self._add_edit_name.setPlaceholderText("球桌号")
        form.addRow("球桌号:", self._add_edit_name)
        self._add_edit_room = LineEdit(card)
        self._add_edit_room.setPlaceholderText("球房名称")
        form.addRow("球房名称:", self._add_edit_room)
        self._add_edit_camera = LineEdit(card)
        self._add_edit_camera.setPlaceholderText("相机密码")
        form.addRow("相机密码:", self._add_edit_camera)
        self._add_edit_snk = LineEdit(card)
        self._add_edit_snk.setPlaceholderText("如 snk_001（留空则从备注解析）")
        form.addRow("SNK标识:", self._add_edit_snk)
        self._add_edit_remark = PlainTextEdit(card)
        self._add_edit_remark.setFixedHeight(72)
        form.addRow("备注:", self._add_edit_remark)
        vbox.addLayout(form)

        add_row = QHBoxLayout()
        add_row.addStretch(1)
        self._btn_add = PrimaryPushButton(FluentIcon.ADD, "添加记录", card)
        self._btn_add.setToolTip("将表单内容写入本地数据库")
        self._btn_add.clicked.connect(self._on_add_record)
        add_row.addWidget(self._btn_add)
        vbox.addLayout(add_row)
        return card

    def _build_source_card(self, parent):
        card = CardWidget(parent)
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(8)
        vbox.addWidget(BodyLabel("设备状态数据源", card))
        vbox.addWidget(CaptionLabel(
            "球房运维管理数据接口", card))
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
        # 点击不获焦：禁用时不会转移焦点到下方控件，避免 ScrollArea 自动滚动导致画面下移
        test_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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

        # 远程桥接：设备状态页右键菜单按 snk 建立 frp xtcp 隧道并打开会话
        self._remote_bridge = FrpRemoteBridge(self)

    def showEvent(self, event):
        super().showEvent(event)
        self._reset_titlebar_button_state()

    def _reset_titlebar_button_state(self):
        """重置标题栏按钮状态，修复关闭按钮卡在 PRESSED 导致窗口无法拖动。

        qframelesswindow 的 TitleBarButton 仅在 mousePressEvent 置 PRESSED，
        没有 mouseReleaseEvent 复位（只能靠 leaveEvent 恢复）。面板关闭（hide）
        复用时关闭按钮可能停在 PRESSED，TitleBar.canDrag() 因此返回 False，
        标题栏无法拖动；鼠标移入按钮触发 enterEvent 后才恢复。每次显示主动复位。
        """
        try:
            from qframelesswindow.titlebar.title_bar_buttons import (
                TitleBarButton, TitleBarButtonState)
            for btn in self.titleBar.findChildren(TitleBarButton):
                if btn.isPressed():
                    btn.setState(TitleBarButtonState.NORMAL)
        except Exception:
            pass

    def closeEvent(self, event):
        """关闭窗口时清理所有 Worker 与远程桥接（frpc 进程/会话窗口）"""
        bridge = getattr(self, "_remote_bridge", None)
        if bridge is not None:
            bridge.shutdown()
        for page in (self.table_page, self.device_page, self.settings_page):
            for attr in ("_worker", "_migrate_worker", "_refresh_worker", "_test_worker",
                         "_upload_worker"):
                worker = getattr(page, attr, None)
                if worker and worker.isRunning():
                    try:
                        worker.disconnect()
                    except (RuntimeError, TypeError):
                        pass
                    worker.quit()
                    worker.wait(2000)
        # 收集 Worker（不同设备可并行，列表管理）
        for worker in list(getattr(self.device_page, "_collect_workers", [])):
            if worker.isRunning():
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
