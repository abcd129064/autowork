# -*- coding: utf-8 -*-
"""运维管理面板（FluentWindow 多页面架构，独立窗口模块）

功能页面（左侧导航切换）：
1. 球桌管理 —— 对接 wechat2-billiard 接口，表格/搜索/分页/列筛选/右键复制
2. 设备状态 —— 对接 kd / xqzg 接口，按日期切换查看设备状态；点击总数/正常/操作单元格
   右侧滑出文件列表，点击文件条目选择目标分类执行图片迁移
3. 健康趋势 —— kd_status 60 天历史聚合：错误率突增预警、单设备趋势折线、TOP N 排行
4. 管理设置 —— 配置 API 账号密码、选择启用数据源（kd / xqzg）、测试连接

数据层复用 database/table_db.py，Worker 层复用 workers/table_worker.py。
"""

import csv
import difflib
import json
import logging
import math
import os
import re
import shutil
import subprocess
from datetime import datetime

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QLabel, QApplication,
    QTextEdit, QDialog, QPushButton, QCheckBox, QTextBrowser, QTreeWidgetItem,
    QFileDialog, QToolTip, QFrame, QListWidget, QListWidgetItem, QAbstractScrollArea,
    QTabWidget)
from PySide6.QtCore import (Qt, QItemSelectionModel, QDate, QPoint, QPropertyAnimation,
    QEasingCurve, QTimer, QEvent, QThread, Signal, QRectF, QSize, QDateTime)
from PySide6.QtGui import (QColor, QShortcut, QKeySequence, QPalette, QCursor,
    QPainter, QPen, QFont, QFontMetrics, QBrush)
from qfluentwidgets import (TableWidget, SearchLineEdit, PushButton,
    PrimaryPushButton, ToolButton, FluentIcon, ComboBox, RoundMenu, CheckBox,
    Action, TransparentDropDownPushButton, LineEdit, PlainTextEdit,
    FluentWindow, NavigationItemPosition, ProgressBar, TitleLabel,
    BodyLabel, CaptionLabel, CalendarPicker, PasswordLineEdit, ScrollArea,
    CardWidget, setCustomStyleSheet, qconfig, isDarkTheme, MessageBox, TreeWidget,
    MessageBoxBase, MenuAnimationType)
from qfluentwidgets.components.widgets.table_view import TableItemDelegate

from core.app_paths import get_app_dir
from core.frp_remote import get_session_manager
from core.perf import is_acrylic_enabled, is_animation_enabled
from core.secrets import decrypt_settings, encrypt_settings
from core.utils import show_info_bar
from workers.table_worker import (TableFetchWorker, DevicesFetchWorker,
                                  SnookerOmFetchWorker, MigrateImageWorker,
                                  LoginTestWorker, get_active_api_source,
                                  CATEGORY_DIRS)
from workers.collect_worker import (CollectFilesWorker, ZipUploadWorker,
                                    clip_base_name, date_from_base,
                                    resolve_device_dir,
                                    fuzzy_match_device_dir, norm_device_suffix)
from database import table_db
from windows.moyu_widgets import Game2048Widget, SnakeWidget, MoyuReaderWidget
from windows.image_viewer import is_image_file

logger = logging.getLogger(__name__)


def _dir_similarity(name: str, candidates: list) -> int:
    """计算候选目录名与目标设备码的相似度分数（0-100）

    结构化规则优先（复用 norm_device_suffix 后缀归一化）：
    精确同名 100；店号前缀相同 + 后缀归一化相等 95；前缀归一化
    相等 + 后缀相同 90；前缀相同 55；字符级相似度（difflib）最高
    60 分兜底。返回对全部候选码的最高分。
    """
    n = str(name or "").strip()
    best = 0
    for cand in candidates:
        c = str(cand or "").strip()
        if not c or not n:
            continue
        if n == c:
            return 100
        score = 0
        np, _, ns = n.rpartition("-")
        cp, _, cs = c.rpartition("-")
        if np and cp:
            nps, cps = norm_device_suffix(np), norm_device_suffix(cp)
            # 前缀完全相同时基础 55 分；后缀归一化相等再 +40（结构化最高档）
            if np == cp:
                score += 55
                if ns and norm_device_suffix(ns) and norm_device_suffix(ns) == norm_device_suffix(cs):
                    score += 40
            # 仅前缀归一化相同（如 281 与 281-08 的店号部分）：+30，后缀相同再 +60
            elif nps and nps == cps:
                score += 30
                if ns and ns == cs:
                    score += 60
        # 字符级相似度兑底：最高 60 分，与结构化得分取较大值
        ratio = difflib.SequenceMatcher(None, n.lower(), c.lower()).ratio()
        score = max(score, int(round(ratio * 60)))
        best = max(best, score)
    return best

# ==================== 动画开关辅助 ====================

def _popup_ani_type():
    """按主界面「性能选项-动画效果」开关决定菜单弹出动画类型。

    运行时即时读取 core.perf 全局状态：开关切换后，已打开面板中的
    菜单下一次弹出即同步生效，新打开的面板同样读取当前值。"""
    return (MenuAnimationType.DROP_DOWN if is_animation_enabled()
            else MenuAnimationType.NONE)


def _patch_menu_animation(menu):
    """实例级 exec 补丁：用于由控件代为弹出的菜单（如 ToolButton.setMenu）。

    库内 ToolButton._showMenu 固定以 DROP_DOWN 调用 menu.exec，无法在
    调用点传参；此处绑定实例级 exec，弹出时按动画开关动态决定类型。"""
    def _exec(pos, ani=True, aniType=None):
        if not is_animation_enabled():
            aniType = MenuAnimationType.NONE
        RoundMenu.exec(menu, pos, ani=ani, aniType=aniType)
    menu.exec = _exec


# ==================== 常量定义 ====================

# 球桌管理列：(字段key, 表头, 宽度)
TABLE_COLUMNS = [
    ("name", "球桌号", 90),
    ("roomName", "球房名称", 200),
    ("onlineStatusName", "在线状态", 80),
    ("remark", "备注", 360),
    ("cameraPassExt", "相机密码", 220),
    ("snk_code", "SNK标识", 110),
    ("code", "设备编码", 200),
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


# ==================== settings.json 读写（带内存缓存） ====================

_settings_cache = None
_settings_mtime = 0


def _load_settings() -> dict:
    """读取 settings.json（带内存缓存，文件未变时直接返回缓存；敏感字段透明解密）"""
    global _settings_cache, _settings_mtime
    path = os.path.join(get_app_dir(), "settings.json")
    try:
        mtime = os.path.getmtime(path)
        if _settings_cache is not None and mtime == _settings_mtime:
            return _settings_cache
        with open(path, "r", encoding="utf-8") as f:
            _settings_cache = decrypt_settings(json.load(f))
        _settings_mtime = mtime
        return _settings_cache
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_settings(data: dict):
    """合并写入 settings.json（同步更新缓存；敏感字段加密后落盘）"""
    global _settings_cache, _settings_mtime
    path = os.path.join(get_app_dir(), "settings.json")
    settings = _load_settings()
    settings.update(data)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(encrypt_settings(settings), f, ensure_ascii=False, indent=2)
    _settings_cache = settings
    _settings_mtime = os.path.getmtime(path)


def _fmt_size(n: float) -> str:
    """字节数格式化为人类可读大小（B/KB/MB/GB）"""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# ==================== 远程前置检查 / CSV 导出辅助（A2 / B6） ====================

# CSV 导出单次查询上限（当前搜索/筛选条件下的全部结果）
_EXPORT_MAX_ROWS = 1_000_000

# 高频问题设备：近 N 天提交 ≥ 阈值次标红（C1）
_HF_DAYS = 30
_HF_THRESHOLD = 3
_HF_COLOR = QColor("#e81123")


def _query_kd_page_with_stats(page_no, page_size, keyword, date,
                              sort_key, sort_desc, hf_days):
    """kd 分页查询 + 高频问题统计（Worker 线程内一次完成，界面零同步查询）"""
    total, rows = table_db.query_kd_page(
        page_no, page_size, keyword, date, sort_key, sort_desc)
    try:
        hf = table_db.get_submission_stats(days=hf_days)
    except Exception:
        hf = {"by_device": {}, "by_table": {}}
    return total, rows, hf


def _query_xqzg_page_with_stats(page_no, page_size, keyword,
                                sort_key, sort_desc, hf_days):
    """xqzg 分页查询 + 高频问题统计（Worker 线程内一次完成，界面零同步查询）"""
    total, rows = table_db.query_xqzg_page(
        page_no, page_size, keyword, sort_key, sort_desc)
    try:
        hf = table_db.get_submission_stats(days=hf_days)
    except Exception:
        hf = {"by_device": {}, "by_table": {}}
    return total, rows, hf


def _confirm_offline_connect(parent, last_report: str) -> bool:
    """A2 离线确认：设备下线时弹醒目确认框，返回是否仍要继续连接"""
    box = MessageBox(
        "设备离线", f"设备离线（最后上报时间 {last_report}），仍要尝试连接吗？", parent)
    box.yesButton.setText("仍要连接")
    box.cancelButton.setText("取消")
    return box.exec()


def _open_in_explorer(path: str):
    """在资源管理器中定位文件（explorer /select,）；目录则直接打开"""
    try:
        if os.path.isfile(path):
            subprocess.Popen(["explorer", f"/select,{os.path.normpath(path)}"])
        elif os.path.isdir(path):
            os.startfile(path)
    except Exception:
        pass


def _show_export_bar(parent, path: str, count: int):
    """导出成功提示条，附「打开文件夹」动作（explorer /select, 定位文件）"""
    bar = show_info_bar(f"共 {count} 条 → {os.path.basename(path)}", "success",
                        title="导出成功", parent=parent, duration=6000)
    btn = PushButton(FluentIcon.FOLDER, "打开文件夹")
    btn.clicked.connect(lambda _=False, p=path: _open_in_explorer(p))
    bar.addWidget(btn)


# ==================== 后台数据库查询/保存 Worker ====================

class _DBQueryWorker(QThread):
    """后台数据库查询/保存 Worker（通用封装）

    将 table_db 的同步操作移到工作线程，避免阻塞 GUI。
    通过 finished 信号返回结果，error 信号返回异常信息。
    """
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            result = self.func(*self.args, **self.kwargs)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


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


# 统一固定行高：原 resizeRowsToContents 对每行做内容同步测量，千行大表
# 加载/滚动/窗口缩放都明显卡顿；改为固定行高，长文本省略号 + tooltip
_FIXED_ROW_HEIGHT = 38


def _fit_table_rows(table):
    """统一行高（固定值，不做内容测量）"""
    vh = table.verticalHeader()
    vh.setDefaultSectionSize(_FIXED_ROW_HEIGHT)
    for r in range(table.rowCount()):
        if table.rowHeight(r) != _FIXED_ROW_HEIGHT:
            table.setRowHeight(r, _FIXED_ROW_HEIGHT)


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
        """收集表单内容为球桌记录（onlineStatusName 留空，同步时由接口数据覆盖）"""
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


class DeviceDirHealDialog(MessageBoxBase):
    """收集失败自愈向导（C5）：候选目录按相似度排序点选 + 手动浏览兜底

    确认选择后 chosen_dir 为相对 videos_dir 的设备目录名；
    取消则保持空串，调用方走原失败提示流程。
    """

    def __init__(self, parent, videos_dir: str, candidates: list, scored: list):
        """scored: [(相似度分, 目录名), ...] 已按分数降序"""
        super().__init__(parent)
        self.videos_dir = videos_dir
        self.chosen_dir = ""
        self.titleLabel = BodyLabel("设备目录自愈向导", self)
        self.viewLayout.addWidget(self.titleLabel)
        codes = " / ".join(candidates) or "未知设备"
        self.subLabel = CaptionLabel(
            f"未在视频目录中找到设备「{codes}」对应的文件夹。\n"
            f"请选择实际对应的本地目录，选择后将被记忆，下次自动命中。", self)
        self.subLabel.setWordWrap(True)
        self.viewLayout.addWidget(self.subLabel)

        self.listWidget = QListWidget(self)
        self.listWidget.setMinimumWidth(360)
        self.listWidget.setMinimumHeight(220)
        self.listWidget.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        for score, name in scored:
            it = QListWidgetItem(f"{name}　·　相似度 {score}%", self.listWidget)
            it.setData(Qt.ItemDataRole.UserRole, name)
            it.setToolTip(os.path.join(videos_dir, name))
        self.viewLayout.addWidget(self.listWidget)

        self.browseBtn = PushButton(FluentIcon.FOLDER, "手动浏览...", self)
        self.browseBtn.clicked.connect(self._on_browse)
        self.viewLayout.addWidget(self.browseBtn)

        self.yesButton.setText("确认选择")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(420)

    def validate(self) -> bool:
        """点击确认：必须选中一个候选目录才放行"""
        row = self.listWidget.currentRow()
        if row is None:
            show_info_bar("请先在列表中选择一个目录，或使用「手动浏览...」", "warning",
                          title="提示", parent=self, duration=2500)
            return False
        self.chosen_dir = row.data(Qt.ItemDataRole.UserRole) or ""
        return bool(self.chosen_dir)

    def _on_browse(self):
        """手动浏览兜底：所选目录必须位于 videos_dir 内"""
        picked = QFileDialog.getExistingDirectory(
            self, "选择设备目录", self.videos_dir)
        if not picked:
            return
        picked = os.path.normpath(picked)
        root = os.path.normpath(self.videos_dir)
        try:
            inside = (picked != root and
                      os.path.commonpath([picked, root]) == root)
        except ValueError:
            inside = False
        if not inside:
            show_info_bar("所选目录必须位于视频目录（videos_dir）内", "warning",
                          title="提示", parent=self, duration=2500)
            return
        self.chosen_dir = os.path.relpath(picked, root)
        self.accept()


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
    """上传清单弹窗：树形展示 {videos_dir}/upload 下待上传文件（设备→文件）

    内置打包上传（ZipUploadWorker）；只能通过底部「关闭」按钮关闭，
    右上角 X / ESC 均被拦截，避免上传进行中被意外关闭。
    """

    def __init__(self, upload_root: str, parent=None):
        super().__init__(parent)
        self._upload_root = upload_root
        self._upload_worker = None
        self.setWindowTitle("上传清单")
        self.resize(560, 460)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(8)
        layout.addWidget(BodyLabel(f"收集目录: {upload_root}", self))

        self._tree = TreeWidget(self)
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["文件", "大小", "操作"])
        self._tree.setAlternatingRowColors(True)
        self._tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Fixed)
        self._tree.setColumnWidth(2, 56)
        layout.addWidget(self._tree, 1)

        # 上传字节进度条（打包上传期间显示，平时隐藏）
        self._progress_bar = ProgressBar(self)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedHeight(4)
        self._progress_bar.hide()
        layout.addWidget(self._progress_bar)

        bottom = QHBoxLayout()
        self._lbl_total = CaptionLabel("", self)
        bottom.addWidget(self._lbl_total)
        # 打包上传阶段提示（打包中/连接中/上传中），代替多个 InfoBar 叠加
        self._lbl_progress = CaptionLabel("", self)
        bottom.addWidget(self._lbl_progress)
        bottom.addStretch(1)
        # 最小化：仅上传进行中可用，隐藏对话框后上传后台继续，完成后重新激活
        self._btn_min = PushButton(FluentIcon.MINIMIZE, "最小化", self)
        self._btn_min.setToolTip("上传后台继续进行，完成后重新弹出本窗口")
        self._btn_min.setEnabled(False)
        self._btn_min.clicked.connect(self.hide)
        bottom.addWidget(self._btn_min)
        btn_open = PushButton(FluentIcon.FOLDER, "打开目录", self)
        btn_open.clicked.connect(self._open_dir)
        bottom.addWidget(btn_open)
        self._btn_package = PushButton(FluentIcon.SEND, "打包上传", self)
        self._btn_package.setToolTip("将收集的文件打包 zip 上传服务器，成功后清空本地 upload 目录")
        self._btn_package.clicked.connect(self._on_package_upload)
        bottom.addWidget(self._btn_package)
        btn_close = PushButton("关闭", self)
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        layout.addLayout(bottom)

        # 填充放最后：_populate 需要 _lbl_total 已存在
        self._populate()

    def closeEvent(self, event):
        """右上角 X / ALT+F4 一律拦截，只能通过底部「关闭」按钮关闭
        （accept → done → hide 不走 closeEvent，不受影响）"""
        event.ignore()

    def done(self, r):
        """上传进行中禁止关闭（含「关闭」按钮），防止 zip 传输被截断"""
        if self._upload_worker is not None and self._upload_worker.isRunning():
            show_info_bar("打包上传进行中，请等待完成后再关闭", "warning",
                          title="提示", parent=self, duration=2000)
            return
        super().done(r)

    def keyPressEvent(self, e):
        """屏蔽 ESC 关闭（与 X 一致，只能通过「关闭」按钮退出）"""
        if e.key() == Qt.Key.Key_Escape:
            return
        super().keyPressEvent(e)

    @staticmethod
    def _dir_size(path: str) -> int:
        """递归统计目录总大小（忽略不可访问项）"""
        total = 0
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
        return total

    def _make_delete_btn(self, full_path: str, name: str) -> ToolButton:
        """为行最右侧创建删除按钮（FluentIcon.DELETE）"""
        btn = ToolButton(FluentIcon.DELETE, self)
        btn.setToolTip(f"删除 {name}")
        btn.setIconSize(QSize(16, 16))
        btn.setFixedSize(30, 24)
        btn.clicked.connect(lambda checked=False, p=full_path, n=name:
                            self._on_delete_item(p, n))
        return btn

    def _populate(self):
        """扫描 upload 根目录构建清单树：一级=设备目录，二级=文件/子文件夹，逐行挂删除按钮"""
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
            dev_item = QTreeWidgetItem([dev, "", ""])
            dev_size = 0
            for fname in sorted(os.listdir(dev_dir)):
                full = os.path.join(dev_dir, fname)
                if os.path.isdir(full):
                    # 子文件夹也列入清单，支持整目录删除
                    size = self._dir_size(full)
                    total_files += sum(len(fs) for _r, _ds, fs in os.walk(full))
                    dev_size += size
                    child = QTreeWidgetItem([fname + "  (文件夹)", _fmt_size(size), ""])
                elif os.path.isfile(full):
                    try:
                        size = os.path.getsize(full)
                    except OSError:
                        size = 0
                    total_files += 1
                    dev_size += size
                    child = QTreeWidgetItem([fname, _fmt_size(size), ""])
                else:
                    continue
                dev_item.addChild(child)
                self._tree.setItemWidget(child, 2, self._make_delete_btn(full, fname))
            dev_item.setText(1, _fmt_size(dev_size))
            dev_item.setExpanded(True)
            self._tree.addTopLevelItem(dev_item)
            self._tree.setItemWidget(dev_item, 2, self._make_delete_btn(dev_dir, dev))
            total_size += dev_size
        self._lbl_total.setText(f"共 {total_files} 个文件，总大小 {_fmt_size(total_size)}")

    # ---------- 逐行删除（二次确认 + 路径安全校验） ----------

    def _safe_upload_path(self, full_path: str):
        """路径安全校验：必须位于 upload 根目录内部且不是根目录本身"""
        root = os.path.normcase(os.path.normpath(os.path.abspath(self._upload_root)))
        path = os.path.normcase(os.path.normpath(os.path.abspath(full_path)))
        if path == root:
            return None
        try:
            if os.path.commonpath([root, path]) != root:
                return None
        except ValueError:
            return None
        return path

    def _on_delete_item(self, full_path: str, name: str):
        """行删除按钮：二次确认后删除磁盘文件/文件夹并刷新清单"""
        if self._upload_worker is not None and self._upload_worker.isRunning():
            show_info_bar("打包上传进行中，请等待完成后再操作", "warning",
                          title="提示", parent=self, duration=2000)
            return
        safe_path = self._safe_upload_path(full_path)
        if safe_path is None:
            show_info_bar("路径不在 upload 目录内，已拒绝删除", "error",
                          title="删除失败", parent=self, duration=4000)
            return
        box = MessageBox("删除确认", f"确定删除 {name} 吗？", self)
        box.yesButton.setText("删除")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        try:
            if os.path.isdir(safe_path):
                shutil.rmtree(safe_path)
            else:
                os.remove(safe_path)
        except FileNotFoundError:
            show_info_bar(f"{name} 已不存在，正在刷新清单", "warning",
                          title="删除失败", parent=self, duration=3000)
        except PermissionError:
            show_info_bar(f"没有权限删除 {name}（文件可能被占用）", "error",
                          title="删除失败", parent=self, duration=4000)
            return
        except OSError as e:
            show_info_bar(f"{name}: {e.strerror or e}", "error",
                          title="删除失败", parent=self, duration=4000)
            return
        show_info_bar(name, "success", title="已删除", parent=self, duration=2000)
        self._tree.clear()
        self._populate()

    def _open_dir(self):
        """在资源管理器中打开 upload 根目录"""
        if os.path.isdir(self._upload_root):
            os.startfile(self._upload_root)

    # ---------- 打包上传 ----------

    def _on_package_upload(self):
        """打包 upload 目录为 zip 并 SFTP 上传，成功后清空本地目录并刷新清单

        凭据用上传专用字段 upload_user/upload_pass（不复用 SSH 凭据）；
        目标由 upload_host/upload_port/upload_remote_dir 配置。
        密码不在代码中内置默认值，未配置时提示用户在设置中填写。
        上传进行中此按钮语义切换为「取消上传」（Task #57）。
        """
        if self._upload_worker is not None and self._upload_worker.isRunning():
            self._on_cancel_upload()
            return
        if not os.path.isdir(self._upload_root) or not os.listdir(self._upload_root):
            show_info_bar("upload 目录为空，无文件可上传", "info",
                          title="提示", parent=self, duration=3000)
            return
        settings = _load_settings()
        host = str(settings.get("upload_host") or "49.235.34.253").strip()
        try:
            port = int(settings.get("upload_port") or 22)
        except (TypeError, ValueError):
            port = 22
        remote_dir = str(settings.get("upload_remote_dir") or "/lhcos-data/videos").strip()
        username = str(settings.get("upload_user") or "root").strip()
        password = str(settings.get("upload_pass") or "")
        if not password:
            show_info_bar("未配置上传密码，请先在设置中填写后重试", "warning",
                          title="提示", parent=self, duration=3000)
            return

        count = self.file_count(self._upload_root)
        box = MessageBox(
            "打包上传",
            f"将把 upload 目录中的 {count} 个文件打包为 zip，上传到\n"
            f"{host}:{remote_dir}\n\n上传成功后将清空本地 upload 目录，确定继续？",
            self)
        box.yesButton.setText("上传")
        box.cancelButton.setText("取消")
        if not box.exec():
            return

        # 上传期间按钮语义切换：打包上传 → 取消上传（二次确认后中断 worker）
        self._btn_package.setText("取消上传")
        self._btn_package.setEnabled(True)
        self._btn_min.setEnabled(True)
        self._progress_bar.setValue(0)
        self._progress_bar.show()
        self._upload_worker = ZipUploadWorker(
            self._upload_root, host, port, username, password, remote_dir)
        # 阶段提示显示在底部进度标签，避免多个 InfoBar 叠加
        self._upload_worker.progress.connect(self._lbl_progress.setText)
        # 字节进度驱动进度条（SFTP put 回调）
        self._upload_worker.percent.connect(self._on_upload_percent)
        self._upload_worker.done.connect(self._on_upload_done)
        self._upload_worker.error.connect(self._on_upload_fail)
        self._upload_worker.cancelled.connect(self._on_upload_cancelled)
        self._upload_worker.start()

    def _on_cancel_upload(self):
        """取消上传：二次确认后请求中断（压缩中/上传中均可取消）

        实际的临时 zip 清理与 SFTP 连接关闭由 ZipUploadWorker 完成，
        中断完成后 cancelled 信号恢复按钮状态。
        """
        box = MessageBox("取消上传", "上传进行中，确定取消？", self)
        box.yesButton.setText("确定取消")
        box.cancelButton.setText("继续上传")
        if not box.exec():
            return
        if self._upload_worker is not None and self._upload_worker.isRunning():
            self._btn_package.setEnabled(False)
            self._lbl_progress.setText("正在取消...")
            self._upload_worker.requestInterruption()

    def _restore_upload_ui(self):
        """上传结束（成功/失败/取消）后恢复按钮与进度条状态

        done/error/cancelled 信号在 worker.run() 内部发出，此时线程可能仍在
        关闭 SFTP/SSH 连接；若直接释放引用，GC 会销毁仍在运行的 QThread 触发
        "Destroyed while thread is still running" 崩溃，因此先安全释放。
        """
        w = self._upload_worker
        self._upload_worker = None
        try:
            if w is not None:
                if w.isRunning():
                    # lambda 包装必须：PySide6 对 finished.connect(w.deleteLater)
                    # 走 C++ 直连不持有 Python 引用，worker 会被 GC 在运行中销毁
                    w.finished.connect(lambda w=w: w.deleteLater())
                else:
                    w.deleteLater()
        except RuntimeError:
            pass
        self._btn_package.setText("打包上传")
        self._btn_package.setEnabled(True)
        self._btn_min.setEnabled(False)
        self._lbl_progress.setText("")
        self._progress_bar.hide()
        self._progress_bar.setValue(0)

    def _reactivate_if_hidden(self):
        """对话框被最小化时重新弹出并激活（上传后台完成的提示入口）"""
        if not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()

    def _on_upload_percent(self, p):
        """上传字节进度：进度条 + 百分比文字（含显示保护）"""
        if self._progress_bar.isHidden():
            self._progress_bar.show()
        self._progress_bar.setValue(p)
        self._lbl_progress.setText(f"上传中 {p}%")

    def _on_upload_done(self, info):
        self._restore_upload_ui()
        self._reactivate_if_hidden()
        self._tree.clear()
        self._populate()  # upload 目录已被 worker 清空，刷新为空清单
        # C1 台账：回填上传结果（匹配近期已收集未上传记录，失败静默）
        try:
            table_db.update_submission_upload(str(info or ""), True)
        except Exception:
            pass
        show_info_bar(f"{info} · 本地 upload 目录已清空", "success",
                      title="上传成功", parent=self, duration=5000)

    def _on_upload_fail(self, msg):
        self._restore_upload_ui()
        self._reactivate_if_hidden()
        show_info_bar(msg.split(chr(10))[0], "error",
                      title="上传失败", parent=self, duration=5000)

    def _on_upload_cancelled(self):
        """取消完成：恢复按钮状态、清理 worker 引用（临时 zip 已由 worker 删除）"""
        self._restore_upload_ui()
        self._reactivate_if_hidden()
        show_info_bar("临时 zip 已清理，可重新发起打包上传", "info",
                      title="已取消上传", parent=self, duration=4000)

    @staticmethod
    def file_count(upload_root: str) -> int:
        """清单中的文件总数（递归统计，与打包遍历 os.walk 一致，供外部确认弹窗展示）"""
        count = 0
        try:
            entries = os.listdir(upload_root)
        except OSError:
            return 0
        for dev in entries:
            dev_dir = os.path.join(upload_root, dev)
            if os.path.isdir(dev_dir):
                count += sum(len(files) for _root, _dirs, files in os.walk(dev_dir))
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
        self._query_worker = None
        self._save_worker = None
        self._export_worker = None
        self._hidden_cols = {2, 6}  # 在线状态/设备编码 默认隐藏，可在「筛选」菜单勾选显示
        self._show_test = False    # 是否显示「公司测试」数据（默认不显示）
        self._show_manual = False  # 是否显示手动版本设备（name 或 roomName 含 @s，默认不显示）
        # 搜索防抖：停止输入 300ms 后才查库重建表格，避免逐字触发同步查询
        self._search_timer = QTimer(self)
        self._search_timer.setInterval(300)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._do_search)
        self._init_ui()
        self._load_local()
        # 异步获取元数据，判断是否需要首次同步
        self._meta_worker = _DBQueryWorker(table_db.get_meta)
        self._meta_worker.finished.connect(self._on_meta_finished)
        self._meta_worker.start()

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
        self._search_edit.textChanged.connect(self._on_search_input)
        toolbar.addWidget(self._search_edit)
        toolbar.addStretch(1)

        self._btn_export_csv = PushButton(FluentIcon.DOWNLOAD, "导出 CSV", self)
        self._btn_export_csv.setToolTip("导出当前搜索结果（含搜索/筛选条件）为 CSV")
        self._btn_export_csv.clicked.connect(self._export_csv)
        toolbar.addWidget(self._btn_export_csv)

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
        self._table.verticalHeader().setDefaultSectionSize(_FIXED_ROW_HEIGHT)
        self._table.setAlternatingRowColors(True)
        # 关闭自动换行：长文本由默认省略号截断 + tooltip 展示全文，
        # 换行会显著增加布局成本
        self._table.setWordWrap(False)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_copy_menu)
        QShortcut(QKeySequence.StandardKey.Copy, self._table).activated.connect(
            lambda: _copy_table_selection(self._table))

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        # 列宽拖动不再触发全表行高重算（固定行高，无需测量）
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
        # 每页选项 20/50/100/300（已移除 800/1000：大页同步填充/测量开销过大）
        self._size_combo.blockSignals(True)
        self._size_combo.addItems(["20", "50", "100", "300"])
        self._size_combo.setCurrentText(str(self._page_size))
        self._size_combo.blockSignals(False)
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
        # 「公司测试」数据开关：默认不勾选（不显示），勾选后才展示
        menu.addSeparator()
        self._test_cb = CheckBox("公司测试", self)
        self._test_cb.setChecked(self._show_test)
        self._test_cb.setFixedSize(max(self._test_cb.sizeHint().width() + 30, 120), 36)
        self._test_cb.setToolTip("显示内部测试球房数据")
        self._test_cb.checkStateChanged.connect(self._toggle_test_data)
        menu.addWidget(self._test_cb, selectable=False)
        # 「手动版本」设备开关：默认不勾选（不显示 name/roomName 含 @s 的设备），勾选后才展示
        self._manual_cb = CheckBox("手动版本", self)
        self._manual_cb.setChecked(self._show_manual)
        self._manual_cb.setFixedSize(max(self._manual_cb.sizeHint().width() + 30, 120), 36)
        self._manual_cb.setToolTip("显示手动版本设备（名称或球房名含 @s）")
        self._manual_cb.checkStateChanged.connect(self._toggle_manual_data)
        menu.addWidget(self._manual_cb, selectable=False)
        # 菜单由 ToolButton 代为弹出（库内固定 DROP_DOWN），打实例补丁以跟随动画开关
        _patch_menu_animation(menu)
        self._col_btn.setMenu(menu)

    def _on_meta_finished(self, result):
        """元数据查询完成：若数据库为空则自动触发首次同步"""
        db_total, _ = result
        if db_total == 0:
            self._sync_from_api()

    # ---------- 数据加载 ----------

    def _load_local(self):
        """异步分页查询本地数据库，快速切换时取消前一个 Worker"""
        if self._query_worker and self._query_worker.isRunning():
            self._query_worker.requestInterruption()
            # PySide6 不支持无参 disconnect()：指定接收者断开全部信号
            self._query_worker.disconnect(self)
        keyword = self._search_edit.text().strip()
        self._query_worker = _DBQueryWorker(
            table_db.query_page, self._page_no, self._page_size, keyword,
            include_test=self._show_test, include_manual=self._show_manual)
        self._query_worker.finished.connect(
            lambda result, kw=keyword: self._on_query_finished(result, kw))
        self._query_worker.start()

    def _on_query_finished(self, result, keyword=""):
        """查询完成回调：更新表格与分页"""
        total, rows = result
        self._total = total
        self._populate(rows)
        self._update_pager(keyword)
        # 异步获取同步时间
        self._time_worker = _DBQueryWorker(table_db.get_meta)
        self._time_worker.finished.connect(self._on_time_meta)
        self._time_worker.start()

    def _on_time_meta(self, result):
        _, sync_time = result
        self._lbl_time.setText(f"数据时间: {sync_time}" if sync_time else "未同步")

    def _sync_from_api(self):
        """从服务器拉取全量球桌数据并落库（任务运行中忽略重复点击）"""
        if self._worker and self._worker.isRunning():
            return
        self._refresh_btn.setEnabled(False)
        self._lbl_info.setText("正在从服务器同步...")
        self._worker = TableFetchWorker()
        self._worker.result_ready.connect(self._on_sync_done)
        self._worker.error.connect(self._on_sync_error)
        self._worker.start()

    def _on_sync_done(self, rows):
        """API 同步完成：异步保存数据到本地数据库"""
        self._save_worker = _DBQueryWorker(table_db.save_all, rows)
        self._save_worker.finished.connect(self._on_save_finished)
        self._save_worker.start()

    def _on_save_finished(self, count):
        """保存完成：重置页码并重新加载"""
        self._page_no = 1
        self._load_local()
        self._refresh_btn.setEnabled(True)
        self._lbl_info.setText(f"同步完成，共 {count} 条")

    def _on_sync_error(self, msg):
        self._refresh_btn.setEnabled(True)
        self._lbl_info.setText(f"同步失败: {msg}")

    def _populate(self, rows):
        # 高频问题标记：一次聚合查询（无 N+1），失败静默不标记
        try:
            hf_map = table_db.get_submission_stats(days=_HF_DAYS)["by_table"]
        except Exception:
            hf_map = {}
        # 填充期间关闭界面更新与信号，完成后一次性恢复
        self._table.setUpdatesEnabled(False)
        self._table.blockSignals(True)
        try:
            self._table.setRowCount(len(rows))
            for r, item in enumerate(rows):
                # 球桌号（name）即提交台账的 table_id，用它匹配高频统计
                hf = hf_map.get(str(item.get("name") or "").strip(), 0)
                for c, (key, _, _) in enumerate(TABLE_COLUMNS):
                    val = item.get(key) or ""
                    val = str(val).replace("\n", " ").strip()
                    cell = QTableWidgetItem(val)
                    tip = str(item.get(key) or "")
                    cell.setToolTip(tip)
                    if key == "onlineStatusName":
                        color = _STATUS_COLORS.get(val)
                        if color:
                            cell.setForeground(color)
                        if hf >= _HF_THRESHOLD:
                            # 高频问题设备：状态列标红 + tooltip 显示提交次数
                            cell.setText(f"{val} · 高频问题")
                            cell.setForeground(_HF_COLOR)
                            cell.setToolTip(
                                f"{tip}\n近 {_HF_DAYS} 天提交 {hf} 次（精度/问题）")
                    self._table.setItem(r, c, cell)
        finally:
            self._table.blockSignals(False)
            self._table.setUpdatesEnabled(True)
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

    def _on_search_input(self, _=""):
        """搜索输入防抖入口：重启 300ms 定时器，连续输入合并为一次查询"""
        self._search_timer.start()

    def _do_search(self):
        """搜索防抖到期：回到第一页按当前关键词重查"""
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
        """列显隐切换：维护隐藏集合并同步表头"""
        if visible:
            self._hidden_cols.discard(col_idx)
        else:
            self._hidden_cols.add(col_idx)
        self._table.setColumnHidden(col_idx, not visible)

    def _toggle_test_data(self, state):
        """「公司测试」数据显隐切换：回到第一页重新查询"""
        self._show_test = (state == Qt.CheckState.Checked)
        self._page_no = 1
        self._load_local()

    def _toggle_manual_data(self, state):
        """「手动版本」设备显隐切换：回到第一页重新查询"""
        self._show_manual = (state == Qt.CheckState.Checked)
        self._page_no = 1
        self._load_local()

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
        # 远程连接入口（SSH / SFTP），与设备状态页交互一致
        if idx.isValid():
            self._add_remote_actions(menu, idx.row())
        menu.exec_(self._table.viewport().mapToGlobal(pos), aniType=_popup_ani_type())

    def _add_remote_actions(self, menu, row_idx):
        """右键菜单追加远程连接入口（SSH 终端 / SFTP 文件管理）

        snk 优先取行数据 snk_code 列（存储时已从 remark 解析/手动写入），
        兜底再从 remark 正则解析；两者皆无则该球桌不可远程。
        """
        # 列索引：5=SNK标识列（存储时已从 remark 解析/手动写入），3=备注列兑底正则解析
        snk_item = self._table.item(row_idx, 5)
        remark_item = self._table.item(row_idx, 3)
        table_item = self._table.item(row_idx, 0)
        table_id = table_item.text().strip() if table_item else ""
        snk = (snk_item.text().strip() if snk_item else "") or \
            table_db.parse_snk_code(remark_item.text() if remark_item else "")
        menu.addSeparator()
        remote_items = [
            ("ssh", FluentIcon.COMMAND_PROMPT, "SSH 终端"),
            ("sftp", FluentIcon.FOLDER, "SFTP 文件管理"),
        ]
        if not snk:
            # 置灰但可见：提示功能存在，仅该球桌缺 snk 配置不可用
            tip = Action(FluentIcon.INFO, "该球桌无 snk 标识，无法远程", self._table)
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
        """委托统一远程会话中心建立 xtcp 隧道并打开会话

        与全应用共享同一 frpc 进程/TOML（RemoteSessionManager 单例），
        同一 snk 已建隧道时直接复用；面板关闭不再 shutdown，
        frpc 由主窗口 closeEvent 统一关闭。
        """
        # A2 远程前置检查：查 kd 最新分区该设备状态，离线时先确认；
        # 单条轻量 SQL，查询失败静默跳过检查照常连接
        try:
            latest = table_db.get_latest_kd_status(table_id)
            if latest and latest.get("status") == "0":
                if not _confirm_offline_connect(
                        self, latest.get("file_path") or "未知"):
                    return
        except Exception:
            pass
        bridge = getattr(self.window(), "_remote_bridge", None)
        if bridge is None:
            show_info_bar("远程桥接未初始化", "error",
                          title="无法远程", parent=self, duration=3000)
            return
        bridge.open_session(kind, snk, table_id, notifier=self, source="球桌管理")

    def _export_csv(self):
        """B6 导出当前搜索结果（含搜索条件）为 CSV（utf-8-sig，Excel 中文兼容）"""
        keyword = self._search_edit.text().strip()
        default = f"球桌数据_{datetime.now().strftime('%Y-%m-%d')}.csv"
        path, _sel = QFileDialog.getSaveFileName(
            self, "导出 CSV", default, "CSV 文件 (*.csv)")
        if not path:
            return
        if self._export_worker and self._export_worker.isRunning():
            show_info_bar("已有导出进行中，请稍候", "warning",
                          title="提示", parent=self, duration=2000)
            return
        # 复用异步查询机制：一次拉取当前条件下的全部记录后写文件
        # （遵循当前「公司测试」与「手动版本」筛选状态）
        self._export_worker = _DBQueryWorker(
            table_db.query_page, 1, _EXPORT_MAX_ROWS, keyword,
            include_test=self._show_test, include_manual=self._show_manual)
        self._export_worker.finished.connect(
            lambda result, p=path: self._on_export_query(result, p))
        self._export_worker.error.connect(
            lambda msg: show_info_bar(str(msg).split(chr(10))[0], "error",
                                      title="导出失败", parent=self, duration=4000))
        self._export_worker.start()

    def _on_export_query(self, result, path):
        _total, rows = result
        header = [c[1] for c in TABLE_COLUMNS]
        keys = [c[0] for c in TABLE_COLUMNS]
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(header)
                for item in rows:
                    writer.writerow([str(item.get(k) or "") for k in keys])
        except OSError as e:
            show_info_bar(str(e), "error",
                          title="导出失败", parent=self, duration=4000)
            return
        _show_export_bar(self, path, len(rows))

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
            show_info_bar(f"球桌「{table_name}」SNK 标识已更新为「{new_snk or '空'}」", "success",
                          title="已保存", parent=self, duration=2500)
        else:
            show_info_bar("未找到匹配的球桌记录", "warning",
                          title="未修改", parent=self, duration=2500)


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
        self._query_worker = None  # 异步刷新 Worker
        self._preview_dlg = None   # 图片查看器（非模态，保持引用防 GC）
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
        # 图片预览入口：标题行右侧，选中图片条目后可用
        self._btn_preview = ToolButton(FluentIcon.PHOTO, self)
        self._btn_preview.setToolTip("预览选中图片（卡片查看 + 左右翻页）")
        self._btn_preview.setEnabled(False)
        self._btn_preview.clicked.connect(self._open_image_preview)
        header.addWidget(self._btn_preview)
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

        # 底部四个迁移目标按钮（选中条目后可用，点击直接迁移）
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
        """打开面板展示指定分类的文件列表：重建条目、默认选中首条并滑入"""
        self._row = row
        self._title = title
        self._fields = fields
        self._migrate_wrap.setVisible(can_migrate)
        if not can_migrate:
            for btn in self._migrate_btns.values():
                btn.setEnabled(False)
        self._reload_entries()
        # 默认选中首条：打开即可直接使用预览/迁移按钮（触发选中信号启用按钮）
        if self._entries:
            self._list.selectRow(0)
        self.slide_in()

    def _reload_entries(self):
        """按当前 _fields 重建文件条目与表格（源分类随条目保留，仅供迁移使用）"""
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
        """数据刷新后，若面板可见则异步重新加载当前设备的文件列表"""
        if not self.isVisible():
            return
        code = self._row.get("device_code", "")
        if not code:
            return
        date = self._device_page._current_date()
        # 取消前一个刷新 Worker，避免堆积
        if self._query_worker and self._query_worker.isRunning():
            self._query_worker.requestInterruption()
            # PySide6 不支持无参 disconnect()：指定接收者断开全部信号
            self._query_worker.disconnect(self)
        self._query_worker = _DBQueryWorker(
            table_db.query_kd_by_device, code, date)
        self._query_worker.finished.connect(self._on_refresh_query)
        self._query_worker.start()

    def _on_refresh_query(self, fresh):
        """异步查询完成：更新文件面板"""
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
        """选中状态变化：有选中条目时启用底部迁移按钮（预览按钮仅限图片）"""
        rows = sorted({it.row() for it in self._list.selectedItems()})
        enabled = bool(rows)
        for btn in self._migrate_btns.values():
            btn.setEnabled(enabled)
        preview_ok = False
        if enabled and 0 <= rows[0] < len(self._entries):
            preview_ok = is_image_file(self._entries[rows[0]][0])
        self._btn_preview.setEnabled(preview_ok)

    def _open_image_preview(self):
        """打开图片查看对话框（非模态，不阻塞面板操作）：卡片式展示当前选中
        图片，支持左右翻页与迁移；已打开时复用窗口跳转到新选中图片"""
        rows = sorted({it.row() for it in self._list.selectedItems()})
        if not rows or not (0 <= rows[0] < len(self._entries)):
            return
        from windows.image_viewer import ImageViewerDialog
        dlg = self._preview_dlg
        if dlg is not None and dlg.isVisible():
            # 复用打开中的查看器：同步最新条目快照并跳转，避免窗口堆叠
            dlg.set_entries(self._entries, rows[0])
            dlg.raise_()
            dlg.activateWindow()
            return
        dlg = ImageViewerDialog(
            self._entries, rows[0],
            file_path=self._device_page._current_date(),
            device_code=self._row.get("device_code", ""),
            device_page=self._device_page,
            can_migrate=self._migrate_wrap.isVisible(),
            dest_options=MIGRATE_DEST_OPTIONS,
            btn_qss=_MIGRATE_BTN_QSS,
            parent=self.window())
        self._preview_dlg = dlg
        # 关闭即销毁（含迁移成功 accept）：销毁时清引用，避免残留隐藏窗口
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dlg.destroyed.connect(lambda: setattr(self, '_preview_dlg', None))
        dlg.show()

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

        # 图片条目：通过图片查看器打开（卡片展示 + 左右翻页 + 迁移）
        if is_image_file(fname):
            act_view = Action(FluentIcon.PHOTO, "通过图片打开", self)
            act_view.triggered.connect(self._open_image_preview)
            menu.addAction(act_view)
            menu.addSeparator()

        act_copy = Action(FluentIcon.COPY, "复制文件名", self)
        act_copy.triggered.connect(
            lambda: QApplication.clipboard().setText(self._clip_name(fname)))
        menu.addAction(act_copy)

        act_copy_all = Action(FluentIcon.COPY, "复制全部文件名", self)
        act_copy_all.triggered.connect(self._copy_all_names)
        menu.addAction(act_copy_all)

        # C6 反向跳转：kd 照片与本地日志共享时间戳前缀，可关联到对应日志
        # 时才显示（.log/.txt 直接支持；照片名需含可解析的时间戳前缀）
        base = clip_base_name(fname)
        if (fname.lower().endswith((".log", ".txt"))
                or re.match(r"^\d{8}_\d{6}", base)):
            menu.addSeparator()
            act_analyze = Action(FluentIcon.DOCUMENT, "在主窗口显示", self)
            act_analyze.setToolTip("在主窗口显示并加载该文件对应的日志")
            act_analyze.triggered.connect(lambda _=False, f=fname: self._analyze_in_main_window(f))
            menu.addAction(act_analyze)

        menu.exec_(self._list.viewport().mapToGlobal(pos), aniType=_popup_ani_type())

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
        show_info_bar(f"{len(names)} 个文件名已复制到剪贴板（已截取 kd 前缀）", "success",
                      title="已复制", parent=self, duration=2000)

    # ---------- C6 反向跳转：在主窗口分析 ----------

    def _analyze_in_main_window(self, fname):
        """C6 反向：由 kd 文件清单条目跳转到主窗口分析对应日志

        kd 照片名（20260724_225031kd-xxx.jpg）与本地日志共享时间戳前缀，
        据此在 {videos_dir}/{设备目录}/{日期}/ 下定位同名 .log/.txt，
        找到后激活主窗口并复用其选中链路定位该日志；本地无文件时提示
        先通过 SFTP 下载。
        """
        base = clip_base_name(fname)
        date_str = date_from_base(base)
        if not date_str:
            show_info_bar("文件名缺少可解析的时间戳前缀，无法关联日志", "warning",
                          title="无法定位", parent=self, duration=3000)
            return
        videos_dir = (_load_settings().get("videos_dir") or "").strip()
        if not videos_dir or not os.path.isdir(videos_dir):
            show_info_bar("videos_dir 未配置或目录不存在", "warning",
                          title="无法定位", parent=self, duration=3000)
            return
        # 设备目录三级解析（映射表 → 精确同名 → 模糊匹配，同收集链路）
        candidates = [str(self._row.get("table_id") or "").strip(),
                      str(self._row.get("device_code") or "").strip()]
        device_dir, _note, _src = resolve_device_dir(videos_dir, candidates)
        if not device_dir:
            show_info_bar("本地未找到设备目录: " + " / ".join(c for c in candidates if c),
                          "warning", title="无法定位", parent=self, duration=3500)
            return
        # 同名日志查找：日期子目录优先，其次设备根目录（与主窗口加载规则一致）
        log_fname = ""
        dev_dir = os.path.join(videos_dir, device_dir)
        for ext in (".log", ".txt"):
            for cand in (os.path.join(dev_dir, date_str, base + ext),
                         os.path.join(dev_dir, base + ext)):
                if os.path.isfile(cand):
                    log_fname = base + ext
                    break
            if log_fname:
                break
        if not log_fname:
            show_info_bar(f"{device_dir}/{date_str} 下未找到 {base}.log/.txt，请先通过 SFTP 下载",
                          "warning", title="本地无此文件", parent=self, duration=4000)
            return
        main_win = self._find_main_window()
        if main_win is None:
            show_info_bar("主窗口未打开", "error",
                          title="无法跳转", parent=self, duration=3000)
            return
        main_win.focus_log_file(device_dir, date_str, log_fname)

    def _find_main_window(self):
        """从 QApplication 顶层窗口中查找主窗口实例（延迟导入避免循环依赖）"""
        try:
            from main_window.main_window import MainWindow
        except Exception:
            return None
        for w in QApplication.topLevelWidgets():
            if isinstance(w, MainWindow):
                return w
        return None

    # ---------- 滑入 / 滑出动画 ----------

    def slide_in(self):
        parent = self.parent()
        pw, ph = parent.width(), parent.height()
        self.setFixedSize(self._PANEL_WIDTH, ph)
        self.move(pw, 0)
        self.show()
        self.raise_()
        # 动画开关关闭：直接定位到最终位置，跳过过渡动画
        if not is_animation_enabled():
            self.move(pw - self._PANEL_WIDTH, 0)
            return
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
        # 动画开关关闭：直接移出可视区并隐藏，跳过过渡动画
        if not is_animation_enabled():
            self.move(pw, 0)
            self.hide()
            return
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
        # 懒加载：首次进入本页才构建 UI 与查询数据（管理面板打开时不再
        # 一次性构造全部三页）
        self._lazy_built = False
        # 外部跳转预设（趋势页预警点击）：未构建时暂存，_lazy_init 应用
        self._pending_search = ""
        self._pending_date = ""

    def focus_search(self, keyword: str, file_path: str = ""):
        """外部设置搜索关键词并触发查询（如趋势页预警跳转）

        页面未懒构建时存入 pending 字段，_lazy_init 完成后自动应用；
        file_path 非空时同步把日期选择器切到该分区日期。
        """
        self._pending_search = str(keyword or "")
        self._pending_date = str(file_path or "")
        if not self._lazy_built:
            return
        if self._pending_date:
            d = QDate.fromString(self._pending_date.replace("/", "-"), "yyyy-MM-dd")
            self._pending_date = ""
            if d.isValid() and d != self._date_picker.date:
                self._date_picker.setDate(d)  # 触发 dateChanged → 重查该日期
        if self._pending_search:
            # setText 触发 textChanged → 防抖 → _do_search（本地重查 + 服务端拉取）
            self._search_edit.setText(self._pending_search)
            self._pending_search = ""

    def _lazy_init(self):
        """首次显示时执行：原初始化逻辑（UI + 日期 + 定时 + 首页加载）"""
        self._page_no = 1
        self._page_size = 50
        self._total = 0
        self._worker = None
        self._migrate_worker = None
        self._refresh_worker = None
        self._hourly_worker = None  # 每小时定时拉取专用，与手动搜索 _worker 隔离
        self._collect_workers = []   # 收集 Worker 列表（不同设备可并行）
        self._upload_worker = None   # 打包上传 Worker（全局唯一）
        self._query_worker = None    # 异步查询 Worker
        self._save_worker = None     # 异步保存 Worker
        self._export_worker = None   # CSV 导出异步查询 Worker（B6）
        self._last_submission_id = None  # 最近一条提交台账 id，供收集完成后回填（C1）
        # C2 历史日期自动补漏状态
        self._backfill_queue = []
        self._backfill_running = False
        self._backfill_worker = None
        self._backfill_save_worker = None
        self._fetch_keyword = ""     # 当前 API 拉取携带的 keyword（空=全量）
        self._last_fetch_silent = False  # 静默拉取（搜索触发）完成不弹 InfoBar
        # 搜索防抖：停止输入 300ms 后才查库重建表格，避免逐字触发同步查询
        self._search_timer = QTimer(self)
        self._search_timer.setInterval(300)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._do_search)
        self._init_ui()
        # 默认日期为昨天（与主窗口一致）；预警跳转预设了分区日期时优先用它
        init_date = QDate.currentDate().addDays(-1)
        if self._pending_date:
            d = QDate.fromString(self._pending_date.replace("/", "-"), "yyyy-MM-dd")
            if d.isValid():
                init_date = d
        self._date_picker.blockSignals(True)
        self._date_picker.setDate(init_date)
        self._date_picker.blockSignals(False)
        # 根据数据源调整日期选择器可用状态（xqzg 不按日期区分）
        self._apply_source_date_state()
        # 复用缓存日历：替换 DatePicker._showCalendarView，避免每次点击重建（0.5s+ 延迟）。
        # 缓存构建较重（日视图生成数万日期项），延后到事件循环空闲执行，
        # 避免首次切页卡顿；构建完成前点击日历走默认路径（功能不受影响）
        QTimer.singleShot(0, self._apply_calendar_cache)
        # 每小时定时拉取当天设备状态（仅 kd 数据源），保持状态字段时效性
        self._hourly_timer = QTimer(self)
        self._hourly_timer.setInterval(3600 * 1000)
        self._hourly_timer.timeout.connect(self._hourly_refresh)
        if self._active_source() == "kd":
            self._hourly_timer.start()
        self._load_local()
        # 趋势页预警跳转：预置搜索词在构建完成后应用（防抖自动触发查询）
        if self._pending_search:
            pending_kw = self._pending_search
            self._pending_search = ""
            self._pending_date = ""
            self._search_edit.setText(pending_kw)
        # C2 历史日期自动补漏：延后启动，避开首次加载与用户操作；静默仅日志
        QTimer.singleShot(2000, self._backfill_missing_dates)

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
        # 日期步进按钮：紧贴日期选择器组成一组，连续点击逐日前移/后移
        self._btn_date_prev = ToolButton(FluentIcon.LEFT_ARROW, self)
        self._btn_date_prev.setFixedWidth(26)
        self._btn_date_prev.setToolTip("前一天")
        self._btn_date_prev.clicked.connect(lambda _=False: self._step_date(-1))
        self._btn_date_next = ToolButton(FluentIcon.RIGHT_ARROW, self)
        self._btn_date_next.setFixedWidth(26)
        self._btn_date_next.setToolTip("后一天")
        self._btn_date_next.clicked.connect(lambda _=False: self._step_date(1))
        date_group = QHBoxLayout()
        date_group.setSpacing(2)
        date_group.addWidget(self._date_picker)
        date_group.addWidget(self._btn_date_prev)
        date_group.addWidget(self._btn_date_next)
        toolbar.addLayout(date_group)

        self._search_edit = SearchLineEdit(self)
        self._search_edit.setPlaceholderText("搜索")
        self._search_edit.setFixedWidth(160)
        self._search_edit.textChanged.connect(self._on_search_input)
        toolbar.addWidget(self._search_edit)
        toolbar.addStretch(1)

        self._btn_export_csv = PushButton(FluentIcon.DOWNLOAD, "导出 CSV", self)
        self._btn_export_csv.setToolTip("导出当前查询结果（含日期/搜索/排序条件）为 CSV")
        self._btn_export_csv.clicked.connect(self._export_csv)
        toolbar.addWidget(self._btn_export_csv)

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
        self._table.verticalHeader().setDefaultSectionSize(_FIXED_ROW_HEIGHT)
        self._table.setAlternatingRowColors(True)
        # 固定行高 + 省略号：关闭换行，长文本由默认 ElideRight 截断，tooltip 展示全文
        self._table.setWordWrap(False)
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
        # 每页选项 20/50/100/300（已移除 800/1000：大页同步填充/测量开销过大）
        self._size_combo.blockSignals(True)
        self._size_combo.addItems(["20", "50", "100", "300"])
        self._size_combo.setCurrentText(str(self._page_size))
        self._size_combo.blockSignals(False)
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
        """复用缓存 CalendarView：替换 DatePicker._showCalendarView 为快速显示，避免每次点击重建

        日视图默认生成 ±100 年约 7.3 万个日期项（约 0.5s），构建缓存期间临时把范围
        缩到今年 ±10 年（约 40ms），构建后立即还原；缓存实例仅用于本页日期筛选，
        数据窗口近十年足够，且不影响其他日历组件。
        """
        try:
            from qfluentwidgets.components.date_time import calendar_view as _cv_mod
            from qfluentwidgets.components.date_time.calendar_view import CalendarView
            picker = self._date_picker
            _span = 10
            _now_year = QDate.currentDate().year()
            _orig_init_items = _cv_mod.DayScrollView._initItems

            def _narrow_init_items(self):
                # 缩小年份范围后再走原逻辑：_initItems 按 self.minYear/maxYear 逐日生成
                self.minYear = _now_year - _span
                self.maxYear = _now_year + _span
                _orig_init_items(self)

            _cv_mod.DayScrollView._initItems = _narrow_init_items
            try:
                cached_view = CalendarView(self.window())
            finally:
                _cv_mod.DayScrollView._initItems = _orig_init_items
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
                # ani 跟随动画开关：关闭时日历直接弹出，无过渡动画
                cached_view.exec(picker.mapToGlobal(QPoint(x, y)),
                                 ani=is_animation_enabled())

            picker._showCalendarView = _fast_show_calendar_view
            picker._cached_calendar_view = cached_view
        except Exception:
            pass

    # ---------- 日期管理 ----------

    def _current_date(self) -> str:
        """返回日期选择器中的日期，格式如 2026/08/02（kd 接口 file_path 格式）"""
        return self._date_picker.date.toString("yyyy/MM/dd")

    def _on_date_changed(self, _=None):
        """日期切换：回到第一页、收起文件面板并按新日期重新查询"""
        self._page_no = 1
        panel = getattr(self, "_file_panel", None)
        if panel:
            panel.slide_out()
        self._load_local()

    def _step_date(self, delta_days: int):
        """日期步进：负数前移、正数后移；setDate 触发 dateChanged → 现有加载链路"""
        self._date_picker.setDate(self._date_picker.date.addDays(delta_days))

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
        # 步进按钮与日期选择器同组，同步启用/禁用
        for attr in ("_btn_date_prev", "_btn_date_next"):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setEnabled(not is_xqzg)
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
        """页面显示时刷新数据源状态（数据源可能在管理设置页被切换）

        首次显示先完成懒构建（UI + 首页加载），避免管理面板打开时同步构造
        """
        super().showEvent(event)
        if not self._lazy_built:
            self._lazy_built = True
            self._lazy_init()
            return
        self._apply_source_date_state()
        self._load_local()

    def _load_local(self):
        """异步分页查询本地数据库，快速切换时取消前一个 Worker"""
        if self._query_worker and self._query_worker.isRunning():
            self._query_worker.requestInterruption()
            # PySide6 不支持无参 disconnect()：指定接收者断开全部信号
            self._query_worker.disconnect(self)
        keyword = self._search_edit.text().strip()
        if self._active_source() == "xqzg":
            self._query_worker = _DBQueryWorker(
                _query_xqzg_page_with_stats,
                self._page_no, self._page_size, keyword,
                self._sort_key, self._sort_desc, _HF_DAYS)
            date = ""  # xqzg 不按日期筛选
        else:
            date = self._current_date()
            # include_files=False：列表页只查轻量字段，文件 JSON 点开行时按 id 懒加载；
            # 高频问题统计与分页查询同批在 Worker 线程完成，界面零同步查询
            self._query_worker = _DBQueryWorker(
                _query_kd_page_with_stats,
                self._page_no, self._page_size, keyword, date,
                self._sort_key, self._sort_desc, _HF_DAYS)
        self._query_worker.finished.connect(
            lambda result, d=date, kw=keyword: self._on_query_finished(result, d, kw))
        self._query_worker.start()

    def _on_query_finished(self, result, date="", keyword=""):
        """查询完成回调：更新表格与分页（统计已随分页同批返回）"""
        total, rows, hf_stats = result
        self._total = total
        self._populate(rows, hf_stats)
        self._update_pager(date, keyword)

    def _search_from_api(self):
        """按当前日期从服务器拉取设备数据（kd 数据源携带搜索词减少传输量）"""
        date = self._current_date()
        if not date:
            return
        if self._worker and self._worker.isRunning():
            return
        self._sync_btn.setEnabled(False)
        src = self._active_source()
        # 搜索状态（kd 数据源）：携带 keyword 只拉取匹配设备，减少数据传输
        keyword = self._search_edit.text().strip() if src == "kd" else ""
        self._fetch_keyword = keyword
        self._last_fetch_silent = False
        self._lbl_info.setText(f"正在从 {src} 搜索 {date} 的设备数据")
        if src == "xqzg":
            self._worker = SnookerOmFetchWorker(file_path=date)
        else:
            self._worker = DevicesFetchWorker(file_path=date, keyword=keyword)
        self._worker.result_ready.connect(self._on_search_done)
        self._worker.error.connect(self._on_search_error)
        self._worker.start()

    def _fetch_api_keyword(self, keyword):
        """搜索状态 API 拉取：只请求匹配 keyword 的设备（仅 kd 数据源）

        返回结果为部分数据，落库走 upsert_kd 增量更新，不覆盖本地全量。
        防堆积：Worker 运行中不发新请求；若期间关键词变化，
        由 _on_save_finished 在完成后用最新关键词补拉一次。
        """
        if self._worker and self._worker.isRunning():
            return
        date = self._current_date()
        if not date:
            return
        self._fetch_keyword = keyword
        self._last_fetch_silent = True
        self._worker = DevicesFetchWorker(file_path=date, keyword=keyword)
        self._worker.result_ready.connect(self._on_search_done)
        self._worker.error.connect(self._on_search_error)
        self._worker.start()

    def _on_search_done(self, data):
        """API 搜索完成：异步保存数据到本地数据库

        带 keyword 拉取的结果是部分数据，走 upsert_kd 增量更新；
        save_kd 是按日期全量替换，直接保存会删除同日期下其他设备。
        """
        rows = data.get("lists") or data.get("results") or []
        keyword = getattr(self, "_fetch_keyword", "")
        if self._active_source() == "xqzg":
            self._save_worker = _DBQueryWorker(table_db.save_xqzg, rows)
            date_desc = "全部日期"
        else:
            date = self._current_date()
            save_func = table_db.upsert_kd if keyword else table_db.save_kd
            self._save_worker = _DBQueryWorker(save_func, rows, date)
            date_desc = date
        self._save_worker.finished.connect(
            lambda count, dd=date_desc: self._on_save_finished(count, dd))
        self._save_worker.start()

    def _on_save_finished(self, count, date_desc=""):
        """保存完成：重置页码、刷新表格并提示（静默拉取不弹窗）"""
        self._sync_btn.setEnabled(True)
        self._page_no = 1
        self._load_local()
        if not getattr(self, "_last_fetch_silent", False):
            show_info_bar(f"{date_desc} 共 {count} 台设备", "success",
                          title="搜索完成", parent=self, duration=2500)
        # 请求在途期间关键词已变化：用最新关键词补拉一次（防抖合并后只补最新值）
        current_kw = self._search_edit.text().strip()
        if (current_kw and current_kw != getattr(self, "_fetch_keyword", "")
                and self._active_source() == "kd"):
            self._fetch_api_keyword(current_kw)

    def _on_search_error(self, msg):
        self._sync_btn.setEnabled(True)
        self._lbl_info.setText(f"搜索失败: {msg}")
        show_info_bar(msg, "error", title="搜索失败", parent=self, duration=4000)

    def _populate(self, rows, hf_stats=None):
        # 缓存当前页数据，供 _get_row_at 直接按行号取用，避免每次点击都重查数据库
        self._current_rows = rows
        # 高频问题标记：统计随分页查询在 Worker 线程完成（界面零同步查询），
        # 传入为空时降级为不标记（防御，正常链路不会发生）
        hf_stats = hf_stats or {"by_device": {}, "by_table": {}}
        # 填充期间关闭界面更新与信号，完成后一次性恢复
        self._table.setUpdatesEnabled(False)
        self._table.blockSignals(True)
        try:
            self._table.setRowCount(len(rows))
            for r, item in enumerate(rows):
                # 优先按 device_code 匹配，无则回退 table_id（两者指向同一批记录）
                _dev = str(item.get("device_code") or "").strip()
                hf = (hf_stats["by_device"].get(_dev, 0) if _dev else
                      hf_stats["by_table"].get(
                          str(item.get("table_id") or "").strip(), 0))
                for c, (key, _, _) in enumerate(DEVICE_COLUMNS):
                    val = item.get(key)
                    if key == "status":
                        # 设备状态码 → 中文 + 颜色标识
                        text, color = _DEVICE_STATUS_MAP.get(str(val).strip(), ("未知", None))
                        tip = f"设备状态: {text}"
                        if hf >= _HF_THRESHOLD:
                            # 高频问题设备：状态列标红，tooltip 显示提交次数
                            text = f"{text} · 高频问题"
                            color = _HF_COLOR
                            tip += f"\n近 {_HF_DAYS} 天提交 {hf} 次（精度/问题）"
                        cell = QTableWidgetItem(text)
                        cell.setToolTip(tip)
                        if color is not None:
                            cell.setForeground(color)
                        self._table.setItem(r, c, cell)
                        continue
                    if isinstance(val, list):
                        # 文件分类列：单元格显示数量，tooltip 预览前 30 个文件名
                        display = str(len(val))
                        tip = "\n".join(val[:30]) + ("..." if len(val) > 30 else "")
                    else:
                        display = str(val if val is not None else "")
                        tip = display
                    cell = QTableWidgetItem(display)
                    cell.setToolTip(tip if tip else "(空)")
                    if key in self._FILE_VIEW_FIELDS:
                        # 可点击列：链接色提示，点击滑出右侧文件面板
                        cell.setForeground(_LINK_COLOR)
                        cell.setToolTip("点击查看文件列表")
                    self._table.setItem(r, c, cell)
        finally:
            self._table.blockSignals(False)
            self._table.setUpdatesEnabled(True)
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

    def _on_search_input(self, _=""):
        """搜索输入防抖入口：重启 300ms 定时器，连续输入合并为一次查询"""
        self._search_timer.start()

    def _do_search(self):
        self._page_no = 1
        self._load_local()
        # 搜索状态（kd 数据源）：同步向服务端发起带 keyword 的请求，
        # 只拉取匹配设备并增量更新本地库（防抖已合并逐字输入）
        keyword = self._search_edit.text().strip()
        if keyword and self._active_source() == "kd":
            self._fetch_api_keyword(keyword)

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

    def _get_full_row_at(self, row_idx) -> dict:
        """获取指定行完整数据（含 8 类文件清单）：kd 数据按 id 懒加载

        列表页缓存为轻量行（不含文件 JSON），仅在点开详情时单点查询；
        xqzg 数据源无文件字段直接返回缓存行。
        """
        row = self._get_row_at(row_idx)
        if not row or self._active_source() != "kd":
            return row
        row_id = row.get("id")
        if row_id is None:
            return row
        return table_db.get_kd_row_full(row_id) or row

    def _show_context_menu(self, pos):
        """右键菜单：查看文件列表 / 复制文件列表 / 复制单元格 / 远程连接 / 清除映射"""
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
        # C4：清除错误设备映射（auto 映射导致收集错误时人工删除，下次收集重新匹配）
        self._add_mapping_actions(menu, idx.row())
        menu.exec_(self._table.viewport().mapToGlobal(pos), aniType=_popup_ani_type())

    def _add_mapping_actions(self, menu, row_idx):
        """右键菜单追加「清除设备映射」项（仅该设备存在映射记录时显示）"""
        row = self._get_row_at(row_idx)
        codes = [str(row.get("table_id") or "").strip(),
                 str(row.get("device_code") or "").strip()]
        infos = []
        for code in dict.fromkeys(c for c in codes if c):
            try:
                info = table_db.get_device_mapping(code)
            except Exception:
                info = {}
            if info:
                infos.append(info)
        if not infos:
            return
        menu.addSeparator()
        for info in infos:
            code = str(info.get("device_code") or "")
            local_dir = str(info.get("local_dir") or "")
            src = "自动" if str(info.get("source") or "auto") == "auto" else "手动"
            act = Action(FluentIcon.DELETE,
                         f"清除设备映射 {code} → {local_dir}（{src}）",
                         self._table)
            act.triggered.connect(
                lambda _=False, cd=code, ld=local_dir:
                self._clear_device_mapping(cd, ld))
            menu.addAction(act)

    def _clear_device_mapping(self, device_code, local_dir):
        """确认后删除设备映射（删除后下次收集重新走模糊匹配）"""
        box = MessageBox(
            "清除设备映射",
            f"确定删除 {device_code} → {local_dir} 的映射？\n"
            f"删除后下次收集将重新模糊匹配（若映射错误导致收集到\n"
            f"错误目录，建议先在 videos_dir 中清理误收集的文件）。",
            self)
        box.yesButton.setText("删除映射")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        try:
            n = table_db.delete_device_mapping(device_code)
        except Exception as e:
            show_info_bar(str(e), "error",
                          title="删除失败", parent=self, duration=3000)
            return
        if n:
            show_info_bar(f"设备映射 {device_code} 已删除", "success",
                          title="已清除", parent=self, duration=2500)
        else:
            show_info_bar("该设备映射不存在或已被删除", "info",
                          title="提示", parent=self, duration=2500)

    def _add_remote_actions(self, menu, row_idx):
        """右键菜单追加远程连接入口（SSH 终端 / SFTP 文件 / 远程桌面）"""
        row = self._get_row_at(row_idx)
        table_id = str(row.get("table_id") or "").strip()
        snk = table_db.get_snk_by_name(table_id)
        # A2 前置检查所需：行内设备状态与最后上报日期（xqzg 无 status 则跳过检查）
        status = str(row.get("status") or "").strip()
        report = str(row.get("file_path") or "") or self._current_date()
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
                    lambda _=False, k=kind:
                    self._open_remote_session(k, snk, table_id, status, report))
            menu.addAction(act)

    def _open_remote_session(self, kind, snk, table_id, status="", report=""):
        """委托统一远程会话中心建立 xtcp 隧道并打开会话"""
        # A2 远程前置检查：行内 status=0（下线）时先确认，不阻止但醒目提示
        if str(status).strip() == "0":
            if not _confirm_offline_connect(self, report or "未知"):
                return
        bridge = getattr(self.window(), "_remote_bridge", None)
        if bridge is None:
            show_info_bar("远程桥接未初始化", "error",
                          title="无法远程", parent=self, duration=3000)
            return
        bridge.open_session(kind, snk, table_id, notifier=self, source="设备状态")

    def _show_files_dialog(self, row_idx):
        """弹出该行全部文件分类的详情弹窗（kd 数据按 id 懒加载完整行）"""
        row = self._get_full_row_at(row_idx)
        if row:
            DeviceFilesDialog(row, self).exec()

    def _copy_file_field(self, row_idx, field):
        """复制指定文件字段的全部文件名到剪贴板（每行一个）"""
        row = self._get_full_row_at(row_idx)
        files = row.get(field) or []
        QApplication.clipboard().setText("\n".join(files))
        show_info_bar(f"{len(files)} 个文件名已复制到剪贴板", "success",
                      title="已复制", parent=self, duration=2000)

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
        """点击文件统计列：滑出右侧文件面板展示对应分类（仅可点击列响应）"""
        key = DEVICE_COLUMNS[col][0]
        cfg = self._FILE_VIEW_FIELDS.get(key)
        if not cfg:
            return
        data = self._get_full_row_at(row)
        if not data:
            return
        title, fields = cfg
        can_migrate = bool(fields) and all(f in _MIGRATABLE_FIELDS for f in fields)
        self._file_panel.show_files(data, title, fields, can_migrate=can_migrate)
        # 注意：点击精度/问题单元格只展示文件列表，不触发收集；
        # 收集统一由迁移按钮（精度/问题提交）成功后在 _on_migrate_ok 中触发

    def migrate_file(self, fname, src_cat, dest_cat):
        """迁移单个文件到目标分类（调用 migrate_image API）"""
        if self._migrate_worker and self._migrate_worker.isRunning():
            show_info_bar("已有迁移任务进行中，请稍候", "warning",
                          title="提示", parent=self, duration=2000)
            return
        date = self._current_date()
        device_code = self._file_panel._row.get("device_code", "")
        if not date or not device_code:
            show_info_bar("缺少日期或设备编码，无法迁移", "warning",
                          title="提示", parent=self, duration=2500)
            return
        self._migrate_worker = MigrateImageWorker(
            file_path=date, device_code=device_code, file_names=[fname],
            src_category=src_cat, dest_category=dest_cat)
        self._migrate_worker.success.connect(
            lambda count: self._on_migrate_ok(fname, dest_cat))
        self._migrate_worker.error.connect(self._on_migrate_fail)
        self._migrate_worker.start()
        show_info_bar(f"{fname} → 「{dest_cat}」...", "info",
                      title="迁移中", parent=self, duration=1500)

    def _on_migrate_ok(self, fname, dest_cat):
        show_info_bar(f"{fname} 已移动到「{dest_cat}」", "success",
                      title="迁移成功", parent=self, duration=2500)
        self._silent_refresh()
        # 迁移到精度/问题后自动收集对应视频/日志到 upload 目录
        # （无需再点精度/问题单元格；数据尚未刷回，先把 fname 并入字段列表）
        field = {"精度": "accuracy_files", "问题": "already_files"}.get(dest_cat)
        if field:
            row = dict(getattr(self._file_panel, "_row", None) or {})
            row[field] = list(row.get(field) or []) + [fname]
            # C1 台账：精度/问题迁移成功写一条（collect_ok 待收集结果回填）
            self._log_submission(row, dest_cat, fname)
            self._auto_collect(row, field)

    def _log_submission(self, row: dict, category: str, fname: str):
        """C1 台账写入点：精度/问题迁移成功记录一条提交（失败静默不阻断主流程）"""
        try:
            self._last_submission_id = table_db.log_submission(
                device_code=str(row.get("device_code") or ""),
                table_id=str(row.get("table_id") or ""),
                club_name=str(row.get("club_name") or ""),
                category=category,
                file_name=fname,
                file_path_date=self._current_date())
        except Exception:
            self._last_submission_id = None

    def _on_migrate_fail(self, msg):
        show_info_bar(msg.split("\n")[0], "error",
                      title="迁移失败", parent=self, duration=4000)
        self._silent_refresh()

    # ---------- 收集与上传 ----------

    def _upload_root(self) -> str:
        """返回上传收集目录 {videos_dir}/upload；未配置 videos_dir 时提示并返回空串"""
        videos_dir = (_load_settings().get("videos_dir") or "").strip()
        if not videos_dir:
            show_info_bar("未配置 videos_dir，请先在设置中配置视频/日志目录", "warning",
                          title="提示", parent=self, duration=3000)
            return ""
        return os.path.join(videos_dir, "upload")

    def _auto_collect(self, row: dict, field: str):
        """迁移到精度/问题后自动收集设备文件到 upload 工作区

        视频/日志按文件列表的基础名收集；detect.bin 与 CPP 日志（daily_*.txt）
        只收集一次（目标已存在即跳过）。

        设备目录三级匹配（C4）：① device_mapping 持久化映射命中；
        ② table_id / device_code 精确同名目录；③ 模糊匹配（店号前缀
        相同 + 后缀归一化数字相等，如 281-S8 ↔ 281-08，仅唯一命中才
        采用，命中后自动落库 source='auto'）；均失败时弹出收集失败
        自愈向导（C5）：候选目录按相似度排序供用户点选或手动浏览，
        选中后落库 source='manual' 并立即继续收集；取消则走原失败提示。
        """
        videos_dir = (_load_settings().get("videos_dir") or "").strip()
        if not videos_dir or not os.path.isdir(videos_dir):
            show_info_bar("videos_dir 未配置或目录不存在", "warning",
                          title="无法收集", parent=self, duration=3000)
            return
        candidates = [str(row.get("table_id") or "").strip(),
                      str(row.get("device_code") or "").strip()]
        device_id, fuzzy_note, _src = resolve_device_dir(videos_dir, candidates)
        if not device_id:
            device_id = self._heal_device_dir(videos_dir, candidates)
            if not device_id:
                show_info_bar("本地设备目录不存在: " + " / ".join(c for c in candidates if c)
                              + "\n可在主界面设备列表找到对应文件夹，右键日志文件→添加到上传目录",
                              "warning", title="无法收集", parent=self, duration=5000)
                return
            fuzzy_note = f"已手动映射 → {device_id}"
        bases = sorted({b for b in (clip_base_name(f) for f in (row.get(field) or [])) if b})
        if not bases:
            return
        worker = CollectFilesWorker(videos_dir, device_id, bases)
        # 捕获当前台账 id：收集完成后回填 collect_ok（C1）
        sub_id = getattr(self, "_last_submission_id", None)
        worker.done.connect(
            lambda dev, n, miss, w=worker, sid=sub_id:
            self._on_collect_done(dev, n, miss, w, sid))
        worker.error.connect(
            lambda msg: show_info_bar(msg.split(chr(10))[0], "error",
                                      title="收集失败", parent=self, duration=4000))
        self._collect_workers.append(worker)
        worker.start()
        show_info_bar(f"{device_id}{'（' + fuzzy_note + '）' if fuzzy_note else ''} · "
                      f"{len(bases)} 个视频/日志 → upload 目录", "info",
                      title="收集中", parent=self,
                      duration=1500 if not fuzzy_note else 3500)

    @staticmethod
    def _norm_suffix(name: str) -> str:
        """后缀归一化：只留数字并去前导零（S8/08/TV2 → 8/8/2）"""
        return norm_device_suffix(name)

    def _fuzzy_match_device_dir(self, videos_dir: str, candidates: list) -> tuple:
        """模糊搜索本地设备目录（委托 collect_worker，保留供自愈向导复用）"""
        return fuzzy_match_device_dir(videos_dir, candidates)

    def _heal_device_dir(self, videos_dir: str, candidates: list) -> str:
        """自愈向导（C5）：匹配失败时弹候选目录列表供用户点选

        扫描 videos_dir 下全部子目录，按与目标设备码的相似度降序取
        TOP 12 弹 Fluent 候选对话框；用户选中后对所有候选码落库
        source='manual'（下次 resolve_device_dir 直接命中映射不再弹窗），
        返回所选目录名；取消返回空串走原失败提示。
        """
        cands = [c for c in candidates if c]
        scored = []
        try:
            entries = os.listdir(videos_dir)
        except OSError:
            entries = []
        for name in entries:
            if name in ("upload", "videos"):
                continue
            if not os.path.isdir(os.path.join(videos_dir, name)):
                continue
            score = _dir_similarity(name, cands)
            if score > 0:
                scored.append((score, name))
        scored.sort(key=lambda t: (-t[0], t[1].lower()))
        dlg = DeviceDirHealDialog(self, videos_dir, cands, scored[:12])
        if not dlg.exec() or not dlg.chosen_dir:
            return ""
        chosen = dlg.chosen_dir
        try:
            for cand in dict.fromkeys(cands):
                table_db.set_device_mapping(cand, chosen, source="manual")
        except Exception:
            logger.warning("自愈向导映射落库失败: %s -> %s", cands, chosen,
                           exc_info=True)
        return chosen

    def _on_collect_done(self, device_id, copied, missing, worker, sub_id=None):
        if worker in self._collect_workers:
            self._collect_workers.remove(worker)
        # C1 台账：回填收集结果（全部就位才算成功，失败静默）
        if sub_id:
            try:
                table_db.update_submission_collect(sub_id, not missing)
            except Exception:
                pass
        if missing:
            shown = ", ".join(missing[:3]) + (" ..." if len(missing) > 3 else "")
            show_info_bar(f"{device_id}: 复制 {copied} 个（已存在跳过），缺失 {len(missing)} 个: {shown}",
                          "warning", title="收集完成", parent=self, duration=4000)
        else:
            show_info_bar(f"{device_id}: 复制 {copied} 个文件到 upload 目录（已存在跳过）",
                          "success", title="收集完成", parent=self, duration=3000)

    def _show_upload_list(self):
        root = self._upload_root()
        if not root:
            return
        if not os.path.isdir(root) or not os.listdir(root):
            show_info_bar("暂无待上传文件，请先点击精度/问题收集文件", "info",
                          title="上传清单", parent=self, duration=3000)
            return
        UploadListDialog(root, self).exec()

    def _on_package_upload(self):
        """打包 upload 目录为 zip 并 SFTP 上传，成功后清空本地目录"""
        root = self._upload_root()
        if not root:
            return
        if not os.path.isdir(root) or not os.listdir(root):
            show_info_bar("upload 目录为空，请先点击精度/问题收集文件", "info",
                          title="提示", parent=self, duration=3000)
            return
        if self._upload_worker is not None and self._upload_worker.isRunning():
            show_info_bar("已有上传进行中，请稍候", "warning",
                          title="提示", parent=self, duration=2000)
            return
        settings = _load_settings()
        host = str(settings.get("upload_host") or "49.235.34.253").strip()
        try:
            port = int(settings.get("upload_port") or 22)
        except (TypeError, ValueError):
            port = 22
        remote_dir = str(settings.get("upload_remote_dir") or "/lhcos-data/videos").strip()
        # 上传专用凭据（不复用 SSH 凭据），密码不内置默认值，未配置时拦截提示
        username = str(settings.get("upload_user") or "root").strip()
        password = str(settings.get("upload_pass") or "")
        if not password:
            show_info_bar("未配置上传密码，请先在设置中填写后重试", "warning",
                          title="提示", parent=self, duration=3000)
            return

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
        # 字节进度：上传阶段在状态栏显示百分比
        self._upload_worker.percent.connect(
            lambda p: self._lbl_time.setText(f"上传中 {p}%"))
        self._upload_worker.done.connect(self._on_upload_done)
        self._upload_worker.error.connect(self._on_upload_fail)
        self._upload_worker.start()

    def _on_upload_done(self, info):
        self._btn_package.setEnabled(True)
        self._lbl_time.setText("")
        # C1 台账：回填上传结果（匹配近期已收集未上传记录，失败静默）
        try:
            table_db.update_submission_upload(str(info or ""), True)
        except Exception:
            pass
        show_info_bar(f"{info} · 本地 upload 目录已清空", "success",
                      title="上传成功", parent=self, duration=5000)

    def _on_upload_fail(self, msg):
        self._btn_package.setEnabled(True)
        self._lbl_time.setText("")
        show_info_bar(msg.split(chr(10))[0], "error",
                      title="上传失败", parent=self, duration=5000)

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
        """迁移后静默刷新完成：异步保存数据"""
        rows = data.get("lists") or data.get("results") or []
        date = self._current_date()
        if self._active_source() == "xqzg":
            self._save_worker = _DBQueryWorker(table_db.save_xqzg, rows)
        else:
            self._save_worker = _DBQueryWorker(table_db.save_kd, rows, date)
        self._save_worker.finished.connect(self._on_refresh_save_finished)
        self._save_worker.start()

    def _on_refresh_save_finished(self, _count):
        """刷新保存完成：重新加载并刷新文件面板"""
        self._load_local()
        self._file_panel.refresh_if_visible()

    def _on_refresh_error(self, msg):
        show_info_bar(msg, "warning", title="刷新失败", parent=self, duration=3000)

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
        """每小时定时拉取完成：异步保存数据"""
        rows = data.get("lists") or data.get("results") or []
        today = QDate.currentDate().toString("yyyy/MM/dd")
        self._save_worker = _DBQueryWorker(table_db.save_kd, rows, today)
        self._save_worker.finished.connect(
            lambda count: self._on_hourly_save_finished(count, today))
        self._save_worker.start()

    def _on_hourly_save_finished(self, count, today=""):
        """每小时保存完成：仅当用户正在查看当天时刷新表格"""
        if self._current_date() == today:
            self._load_local()
        self._lbl_time.setText(f"自动更新: {datetime.now().strftime('%H:%M:%S')}（{count} 台）")

    def _on_hourly_error(self, msg):
        # 定时拉取失败不打断用户，仅在状态栏静默提示
        self._lbl_time.setText(f"自动更新失败: {msg.split(chr(10))[0]}")

    # ---------- CSV 导出（B6） ----------

    def _export_csv(self):
        """导出当前查询结果（含日期/搜索/排序条件）为 CSV（utf-8-sig）"""
        src = self._active_source()
        date = self._current_date()
        default_name = (f"设备状态_{date.replace('/', '-')}" if src == "kd"
                        else "设备状态")
        default = f"{default_name}_{datetime.now().strftime('%Y-%m-%d')}.csv"
        path, _sel = QFileDialog.getSaveFileName(
            self, "导出 CSV", default, "CSV 文件 (*.csv)")
        if not path:
            return
        if self._export_worker and self._export_worker.isRunning():
            show_info_bar("已有导出进行中，请稍候", "warning",
                          title="提示", parent=self, duration=2000)
            return
        keyword = self._search_edit.text().strip()
        # 复用异步查询机制：按当前条件一次拉取全部记录后写文件
        if src == "xqzg":
            self._export_worker = _DBQueryWorker(
                table_db.query_xqzg_page, 1, _EXPORT_MAX_ROWS, keyword,
                self._sort_key, self._sort_desc)
        else:
            self._export_worker = _DBQueryWorker(
                table_db.query_kd_page, 1, _EXPORT_MAX_ROWS, keyword, date,
                self._sort_key, self._sort_desc)
        self._export_worker.finished.connect(
            lambda result, p=path, s=src: self._on_export_query(result, p, s))
        self._export_worker.error.connect(
            lambda msg: show_info_bar(str(msg).split(chr(10))[0], "error",
                                      title="导出失败", parent=self, duration=4000))
        self._export_worker.start()

    def _on_export_query(self, result, path, src):
        _total, rows = result
        if src == "kd":
            header = ["设备编码"] + [c[1] for c in DEVICE_COLUMNS]
            keys = ["device_code"] + [c[0] for c in DEVICE_COLUMNS]
        else:
            header = [c[1] for c in DEVICE_COLUMNS]
            keys = [c[0] for c in DEVICE_COLUMNS]
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(header)
                for item in rows:
                    writer.writerow([
                        str(item.get(k) if item.get(k) is not None else "")
                        for k in keys])
        except OSError as e:
            show_info_bar(str(e), "error",
                          title="导出失败", parent=self, duration=4000)
            return
        _show_export_bar(self, path, len(rows))

    # ---------- 历史日期自动补漏（C2） ----------

    _BACKFILL_MAX = 10        # 单次启动最多补漏天数，其余下次再补
    _BACKFILL_INTERVAL = 1500  # 串行拉取间隔（毫秒），避免并发打爆 API

    def _backfill_missing_dates(self):
        """生成应有日期序列（最早 kd 日期→今天，无数据则近 60 天），对比已存
        分区找出缺失日期静默串行补拉（每次最多 10 天，不弹 UI 仅日志）
        """
        if self._active_source() != "kd" or getattr(self, "_backfill_running", False):
            return
        try:
            # 已覆盖 = 有数据的分区 + 拉过但接口为空的日期（sync_meta）
            covered = set(table_db.get_kd_dates()) | set(table_db.get_kd_synced_dates())
        except Exception:
            return
        today = QDate.currentDate()
        start = None
        if covered:
            start = QDate.fromString(min(covered), "yyyy/MM/dd")
        if start is None or not start.isValid():
            start = today.addDays(-59)
        missing = []
        d = start
        while d <= today:
            s = d.toString("yyyy/MM/dd")
            if s not in covered:
                missing.append(s)
            d = d.addDays(1)
        if not missing:
            return
        self._backfill_running = True
        self._backfill_queue = missing[:self._BACKFILL_MAX]
        logger.info("历史补漏：缺失 %d 天，本次补 %d 天: %s",
                    len(missing), len(self._backfill_queue),
                    ", ".join(self._backfill_queue))
        self._backfill_next()

    def _backfill_next(self):
        """串行消费补漏队列：逐个日期拉取，间隔短延时；用户操作优先"""
        if not getattr(self, "_backfill_queue", []):
            if getattr(self, "_backfill_running", False):
                logger.info("历史补漏完成")
            self._backfill_running = False
            return
        # 用户手动拉取/静默刷新/定时拉取进行中 → 延后，避免并发冲突
        busy = any(getattr(self, a) is not None and getattr(self, a).isRunning()
                   for a in ("_worker", "_refresh_worker", "_hourly_worker"))
        if busy:
            QTimer.singleShot(3000, self._backfill_next)
            return
        date = self._backfill_queue.pop(0)
        worker = DevicesFetchWorker(file_path=date)
        worker.result_ready.connect(
            lambda data, dt=date: self._on_backfill_done(data, dt))
        worker.error.connect(
            lambda msg, dt=date: self._on_backfill_error(msg, dt))
        self._backfill_worker = worker
        worker.start()

    def _on_backfill_done(self, data, date):
        """补漏拉取完成：异步落库（复用 _DBQueryWorker，不阻塞 GUI）"""
        rows = data.get("lists") or data.get("results") or []
        self._backfill_save_worker = _DBQueryWorker(table_db.save_kd, rows, date)
        self._backfill_save_worker.finished.connect(
            lambda count, dt=date: self._on_backfill_saved(count, dt))
        self._backfill_save_worker.error.connect(
            lambda msg, dt=date: self._on_backfill_save_error(msg, dt))
        self._backfill_save_worker.start()

    def _on_backfill_saved(self, count, date):
        logger.info("历史补漏 %s 完成：%d 台设备", date, count)
        if self._current_date() == date:
            self._load_local()  # 用户正查看该日期 → 顺带刷新
        QTimer.singleShot(self._BACKFILL_INTERVAL, self._backfill_next)

    def _on_backfill_save_error(self, msg, date):
        logger.warning("历史补漏保存失败 %s: %s", date, str(msg).split(chr(10))[0])
        QTimer.singleShot(self._BACKFILL_INTERVAL, self._backfill_next)

    def _on_backfill_error(self, msg, date):
        logger.warning("历史补漏拉取失败 %s: %s", date, str(msg).split(chr(10))[0])
        QTimer.singleShot(self._BACKFILL_INTERVAL, self._backfill_next)

    def resizeEvent(self, e):
        """窗口尺寸变化时同步滑出面板的高度与位置（面板贴右缘）"""
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
        # 懒加载：首次进入本页才构建 UI 与读取配置（管理面板打开更快）
        self._lazy_built = False

    def _lazy_init(self):
        self._test_worker = None
        self._user_edits = {}
        self._pass_edits = {}
        self._test_btns = {}
        self._init_ui()
        self._load_current()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._lazy_built:
            self._lazy_built = True
            self._lazy_init()

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

        layout.addWidget(self._build_source_card(view))
        layout.addWidget(self._build_api_card(
            # （Session 认证）
            view, "api1", "接口1 · xqzg", "xqzg.newbv.cn"))
        layout.addWidget(self._build_api_card(
            # （JWT 认证）
            view, "api2", "接口2 · kd", "kd.newbv.cn:30005"))
        layout.addWidget(self._build_upload_card(view))
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
        show_info_bar(f"球桌「{name}」已写入本地数据库", "success",
                      title="添加成功", parent=self, duration=2000)

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

    def _build_upload_card(self, parent):
        """收集与上传：与主界面设置对话框同款配置（复用 settings.json 同键）"""
        card = CardWidget(parent)
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(8)
        vbox.addWidget(BodyLabel("收集与上传", card))
        vbox.addWidget(CaptionLabel(
            "精度/问题文件收集打包上传；文件收集到 视频/日志目录/upload", card))

        form = QFormLayout()
        form.setSpacing(8)
        self._edit_upload_host = LineEdit(card)
        self._edit_upload_host.setPlaceholderText("上传服务器 IP")
        form.addRow("上传服务器:", self._edit_upload_host)
        self._edit_upload_port = LineEdit(card)
        self._edit_upload_port.setPlaceholderText("端口号（默认 22）")
        form.addRow("上传端口:", self._edit_upload_port)
        self._edit_upload_dir = LineEdit(card)
        self._edit_upload_dir.setPlaceholderText("如 /lhcos-data/videos")
        form.addRow("远程目录:", self._edit_upload_dir)
        self._edit_upload_user = LineEdit(card)
        self._edit_upload_user.setPlaceholderText("上传用户名（默认 root）")
        form.addRow("上传用户名:", self._edit_upload_user)
        self._edit_upload_pass = PasswordLineEdit(card)
        self._edit_upload_pass.setPlaceholderText("上传密码")
        form.addRow("上传密码:", self._edit_upload_pass)
        vbox.addLayout(form)
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
        settings = _load_settings()
        creds = settings.get("api_credentials", {})
        for api_key in ("api1", "api2"):
            cfg = creds.get(api_key, {})
            self._user_edits[api_key].setText(cfg.get("username", ""))
            self._pass_edits[api_key].setText(cfg.get("password", ""))
        active = str(creds.get("active_source", "kd")).lower()
        idx = next((i for i, (_, v) in enumerate(self._SOURCE_OPTIONS) if v == active), 0)
        self._source_combo.setCurrentIndex(idx)
        # 收集与上传（与主界面设置同键）
        self._edit_upload_host.setText(str(settings.get("upload_host", "49.235.34.253")))
        self._edit_upload_port.setText(str(settings.get("upload_port", 22)))
        self._edit_upload_dir.setText(
            str(settings.get("upload_remote_dir", "/lhcos-data/videos")))
        self._edit_upload_user.setText(str(settings.get("upload_user", "root")))
        self._edit_upload_pass.setText(str(settings.get("upload_pass", "")))

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
            upload_port = int(self._edit_upload_port.text().strip() or 22)
        except ValueError:
            upload_port = 22
        try:
            _save_settings({
                "api_credentials": api_credentials,
                "upload_host": self._edit_upload_host.text().strip(),
                "upload_port": upload_port,
                "upload_remote_dir": self._edit_upload_dir.text().strip(),
                "upload_user": self._edit_upload_user.text().strip() or "root",
                "upload_pass": self._edit_upload_pass.text(),
            })
            show_info_bar("配置已写入 settings.json，即时生效", "success",
                          title="已保存", parent=self, duration=2500)
        except Exception as e:
            show_info_bar(str(e), "error",
                          title="保存失败", parent=self, duration=4000)

    # ---------- 测试连接 ----------

    def _on_test(self, api_key):
        if self._test_worker and self._test_worker.isRunning():
            show_info_bar("已有测试进行中，请稍候", "warning",
                          title="提示", parent=self, duration=2000)
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
            show_info_bar(msg, "success", title=f"{label} 连接成功",
                          parent=self, duration=2500)
        else:
            show_info_bar(msg, "error", title=f"{label} 连接失败",
                          parent=self, duration=4000)


# ==================== 健康度趋势看板（C3） ====================

# 折线序列配色：错误率=警示红、操作率=信息蓝、精度=琥珀金（虚线）
_TREND_SERIES = (
    ("error_rate", "错误率", QColor("#e81123"), False),
    ("operation_rate", "操作率", QColor("#0078d4"), False),
    ("accuracy_count", "精度", QColor("#c98a2d"), True),
)


class _TrendChart(QWidget):
    """零依赖自绘折线图（QPainter）

    绘制 error_rate/operation_rate/accuracy_count 三条折线（数据已由
    query_kd_trend CAST 为数值），带网格线、Y 刻度、X 日期标签、图例与
    悬停提示。缺失日期（补漏未完成）自然按稀疏点绘制。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        self._hover = -1
        self._pts_x = []
        self.setMinimumHeight(210)
        self.setMouseTracking(True)

    def set_data(self, rows):
        """设置趋势数据并重绘（同时清空悬停高亮）"""
        self._rows = list(rows or [])
        self._hover = -1
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = isDarkTheme()
        text_c = QColor("#e8ebef") if dark else QColor("#3b4046")
        faint_c = QColor("#8a919b")
        grid_c = QColor("#3a414b") if dark else QColor("#e2e6ea")
        if not self._rows:
            p.setPen(QPen(faint_c))
            p.setFont(QFont("Microsoft YaHei", 10))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "暂无趋势数据 — 请在上方搜索并选择设备")
            return
        pad_l, pad_r, pad_t, pad_b = 48, 14, 32, 24
        plot = QRectF(pad_l, pad_t, max(10, self.width() - pad_l - pad_r),
                      max(10, self.height() - pad_t - pad_b))
        n = len(self._rows)
        vmax = max([float(r.get(k) or 0) for r in self._rows
                    for k, _, _, _ in _TREND_SERIES] + [1.0]) * 1.15

        def x_at(i):
            return plot.left() + (plot.width() * i / (n - 1) if n > 1
                                  else plot.width() / 2)

        def y_at(v):
            return plot.bottom() - plot.height() * max(0.0, float(v)) / vmax

        self._pts_x = [x_at(i) for i in range(n)]
        # 网格线 + Y 刻度（5 档）
        p.setFont(QFont("Microsoft YaHei", 7))
        for g in range(5):
            v = vmax * g / 4
            y = int(y_at(v))
            p.setPen(QPen(grid_c, 1, Qt.PenStyle.DotLine if g
                          else Qt.PenStyle.SolidLine))
            p.drawLine(int(plot.left()), y, int(plot.right()), y)
            p.setPen(QPen(faint_c))
            label = f"{v:.1f}" if v < 10 else f"{v:.0f}"
            p.drawText(QRectF(0, y - 7, pad_l - 6, 14),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       label)
        # X 日期标签（最多 8 个，避免重叠）
        step = max(1, math.ceil(n / 8))
        for i in range(0, n, step):
            date = str(self._rows[i].get("file_path") or "")
            p.setPen(QPen(faint_c))
            p.drawText(QRectF(self._pts_x[i] - 24, plot.bottom() + 4, 48, 16),
                       Qt.AlignmentFlag.AlignCenter, date[-5:])
        # 图例（顶部横排）
        p.setFont(QFont("Microsoft YaHei", 8))
        lx = plot.left()
        for _, label, color, dashed in _TREND_SERIES:
            pen = QPen(color, 2)
            if dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(int(lx), 14, int(lx) + 18, 14)
            p.setPen(QPen(text_c))
            p.drawText(int(lx) + 24, 18, label)
            lx += 24 + QFontMetrics(p.font()).horizontalAdvance(label) + 18
        # 悬停竖向参考线
        if 0 <= self._hover < n:
            p.setPen(QPen(faint_c, 1, Qt.PenStyle.DashLine))
            p.drawLine(int(self._pts_x[self._hover]), int(plot.top()),
                       int(self._pts_x[self._hover]), int(plot.bottom()))
        # 折线 + 数据点（None 值断点，各段独立绘制）
        for key, _, color, dashed in _TREND_SERIES:
            pen = QPen(color, 2,
                       Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine)
            p.setPen(pen)
            prev = None
            for i, r in enumerate(self._rows):
                v = r.get(key)
                pt = (self._pts_x[i], y_at(v)) if v is not None else None
                if pt and prev:
                    p.drawLine(int(prev[0]), int(prev[1]), int(pt[0]), int(pt[1]))
                prev = pt
            p.setBrush(color)
            p.setPen(Qt.PenStyle.NoPen)
            for i, r in enumerate(self._rows):
                v = r.get(key)
                if v is None:
                    continue
                rad = 4.5 if i == self._hover else 2.5
                p.drawEllipse(QRectF(self._pts_x[i] - rad, y_at(v) - rad,
                                     rad * 2, rad * 2))

    def mouseMoveEvent(self, event):
        """悬停取最近的 X 数据点：更新高亮并显示该日期的三指标 tooltip"""
        if not self._rows or not self._pts_x:
            return
        x = event.position().x()
        # 按 x 坐标绝对差取最近的数据点索引
        idx = min(range(len(self._pts_x)),
                  key=lambda i: abs(self._pts_x[i] - x))
        if idx != self._hover:
            self._hover = idx
            self.update()
        r = self._rows[idx]
        tip = (f"{r.get('file_path', '')}   错误率 {float(r.get('error_rate') or 0):.1f}%   "
               f"操作率 {float(r.get('operation_rate') or 0):.1f}%   "
               f"精度 {int(float(r.get('accuracy_count') or 0))}")
        QToolTip.showText(event.globalPosition().toPoint(), tip, self)

    def leaveEvent(self, event):
        self._hover = -1
        self.update()
        QToolTip.hideText()
        super().leaveEvent(event)


class _AlertRow(CardWidget):
    """单条错误率突增预警条目（整行可点击，跳转设备页搜索该设备）"""

    def __init__(self, info: dict, on_jump, parent=None):
        super().__init__(parent)
        self._info = info
        self._on_jump = on_jump
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"数据日期 {info.get('file_path', '')}，点击查看该设备")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(10)
        icon = QLabel("⚠", self)
        icon.setStyleSheet("color: #e81123; font-size: 15px;")
        lay.addWidget(icon)
        avg = max(float(info.get("avg_rate") or 0), 0.001)
        times = float(info.get("today_rate") or 0) / avg
        txt = QLabel(
            f"{info.get('device_code', '')} · {info.get('club_name', '')} · "
            f"球桌 {info.get('table_id', '')} —— 今日错误率 "
            f"{float(info.get('today_rate') or 0):.1f}%，近 "
            f"{info.get('hist_days', 0)} 日均值 "
            f"{float(info.get('avg_rate') or 0):.1f}%（{times:.1f}×）", self)
        txt.setStyleSheet("color: #e81123;")
        lay.addWidget(txt, 1)
        jump = QLabel("查看设备 →", self)
        jump.setStyleSheet("color: #e81123; font-weight: 600;")
        lay.addWidget(jump)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_jump(self._info)
        super().mouseReleaseEvent(event)


class TrendPage(QWidget):
    """健康度趋势看板页（C3）：突增预警 + 单设备趋势折线 + TOP N 排行

    全部查询走 _DBQueryWorker 异步模式；页面懒构建（首次进入才初始化）。
    仅 kd 数据源可用（kd_status 才有日期分区历史）。
    """

    # 排行表列：(key, 表头, 宽度)
    _RANK_COLUMNS = [
        ("rank", "排名", 46), ("club_name", "球房", 150), ("table_id", "球桌", 70),
        ("status", "状态", 60), ("error_rate", "错误率", 80),
        ("operation_rate", "操作率", 80), ("accuracy_count", "精度", 60),
        ("already_count", "问题", 60),
    ]
    # 排序下拉文案 → query_kd_ranking 字段
    _RANK_FIELD_MAP = {
        "错误率": "error_rate", "操作率": "operation_rate",
        "精度": "accuracy_count", "问题数": "already_count",
        "操作数": "except_count", "废弃数": "rubbish_count",
    }
    # 页面切回时预警/排行重查的最小间隔（秒），避免频繁切换重复扫表
    _REFRESH_COOLDOWN = 60

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lazy_built = False

    def _lazy_init(self):
        self._alerts_worker = None   # 预警查询
        self._cand_worker = None     # 设备候选搜索
        self._trend_worker = None    # 趋势序列查询
        self._rank_worker = None     # 排行查询
        self._candidates = []        # 当前匹配的候选设备行
        self._last_refresh = None    # 上次预警/排行刷新时间
        # 搜索防抖（与球桌/设备页同款 300ms 模式）
        self._search_timer = QTimer(self)
        self._search_timer.setInterval(300)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._do_search_candidates)
        self._init_ui()
        self._load_alerts()
        self._load_ranking()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 12)
        root.setSpacing(10)
        title = TitleLabel("健康度趋势", self)
        root.addWidget(title)

        # ---------- 预警区（置顶红色提示，点击跳转设备页） ----------
        alert_card = CardWidget(self)
        a_lay = QVBoxLayout(alert_card)
        a_lay.setContentsMargins(14, 10, 14, 12)
        a_lay.setSpacing(6)
        head = QHBoxLayout()
        lbl_head = QLabel("⚠ 异常突增预警（今日错误率 > 近 7 日均值×2）", alert_card)
        lbl_head.setStyleSheet("font-weight: 600; color: #e81123;")
        head.addWidget(lbl_head)
        head.addStretch(1)
        self._btn_alert_refresh = ToolButton(FluentIcon.SYNC, alert_card)
        self._btn_alert_refresh.setToolTip("刷新预警与排行")
        self._btn_alert_refresh.clicked.connect(self._refresh_all)
        head.addWidget(self._btn_alert_refresh)
        a_lay.addLayout(head)
        self._lbl_alert_empty = QLabel("暂无突增预警", alert_card)
        self._lbl_alert_empty.setStyleSheet("color: #8a919b;")
        a_lay.addWidget(self._lbl_alert_empty)
        alert_scroll = ScrollArea(alert_card)
        alert_scroll.setWidgetResizable(True)
        alert_scroll.setFixedHeight(110)
        alert_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._alert_body = QWidget()
        body_lay = QVBoxLayout(self._alert_body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(4)
        body_lay.addStretch(1)
        alert_scroll.setWidget(self._alert_body)
        a_lay.addWidget(alert_scroll)
        root.addWidget(alert_card)

        # ---------- 设备趋势折线 ----------
        trend_card = CardWidget(self)
        t_lay = QVBoxLayout(trend_card)
        t_lay.setContentsMargins(14, 10, 14, 12)
        t_lay.setSpacing(8)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self._search_edit = SearchLineEdit(trend_card)
        self._search_edit.setPlaceholderText("搜索设备（编码 / 球房 / 球桌）")
        self._search_edit.setFixedWidth(230)
        self._search_edit.textChanged.connect(self._on_search_input)
        bar.addWidget(self._search_edit)
        self._device_combo = ComboBox(trend_card)
        self._device_combo.setToolTip("选择设备查看趋势")
        self._device_combo.currentIndexChanged.connect(self._on_device_chosen)
        bar.addWidget(self._device_combo, 1)
        t_lay.addLayout(bar)
        self._lbl_trend_title = CaptionLabel("近 30 天趋势", trend_card)
        t_lay.addWidget(self._lbl_trend_title)
        self._chart = _TrendChart(trend_card)
        t_lay.addWidget(self._chart)
        root.addWidget(trend_card)

        # ---------- TOP N 排行 ----------
        rank_card = CardWidget(self)
        r_lay = QVBoxLayout(rank_card)
        r_lay.setContentsMargins(14, 10, 14, 12)
        r_lay.setSpacing(8)
        r_head = QHBoxLayout()
        r_head.setSpacing(6)
        r_head.addWidget(QLabel("TOP", rank_card))
        self._top_combo = ComboBox(rank_card)
        self._top_combo.addItems(["10", "20", "50"])
        self._top_combo.setFixedWidth(70)
        self._top_combo.currentTextChanged.connect(lambda _: self._load_ranking())
        r_head.addWidget(self._top_combo)
        r_head.addWidget(QLabel("排行 · 排序:", rank_card))
        self._sort_combo = ComboBox(rank_card)
        self._sort_combo.addItems(list(self._RANK_FIELD_MAP.keys()))
        self._sort_combo.currentTextChanged.connect(lambda _: self._load_ranking())
        r_head.addWidget(self._sort_combo)
        r_head.addStretch(1)
        self._lbl_rank_date = CaptionLabel("", rank_card)
        r_head.addWidget(self._lbl_rank_date)
        r_lay.addLayout(r_head)
        self._rank_table = TableWidget(rank_card)
        self._rank_table.setColumnCount(len(self._RANK_COLUMNS))
        self._rank_table.setHorizontalHeaderLabels([c[1] for c in self._RANK_COLUMNS])
        self._rank_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._rank_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._rank_table.verticalHeader().setVisible(False)
        self._rank_table.verticalHeader().setDefaultSectionSize(_FIXED_ROW_HEIGHT)
        self._rank_table.setAlternatingRowColors(True)
        self._rank_table.setWordWrap(False)
        self._rank_table.setMinimumHeight(180)
        self._rank_table.cellDoubleClicked.connect(self._on_rank_double_clicked)
        header = self._rank_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for i, (_, _, w) in enumerate(self._RANK_COLUMNS):
            self._rank_table.setColumnWidth(i, w)
        r_lay.addWidget(self._rank_table)
        r_tip = CaptionLabel("双击排行行可跳转设备页查看", rank_card)
        r_tip.setStyleSheet("color: #8a919b;")
        r_lay.addWidget(r_tip)
        root.addWidget(rank_card)
        root.addStretch(1)

    def showEvent(self, event):
        """首次显示懒构建；之后按冷却间隔刷新预警/排行（数据可能已同步更新）"""
        super().showEvent(event)
        if not self._lazy_built:
            self._lazy_built = True
            self._lazy_init()
            return
        if self._active_source() != "kd":
            return
        now = datetime.now()
        if (self._last_refresh is None
                or (now - self._last_refresh).total_seconds() > self._REFRESH_COOLDOWN):
            self._load_alerts()
            self._load_ranking()

    # ---------- 通用 ----------

    def _active_source(self) -> str:
        return get_active_api_source()

    def _run_query(self, attr, func, args, on_ok):
        """异步查询通用入口：同名旧任务断开信号，worker 引用挂到 self 供
        面板 closeEvent 统一清理"""
        old = getattr(self, attr, None)
        if old is not None and old.isRunning():
            try:
                # PySide6 不支持无参 QObject.disconnect()：指定接收者断开全部信号
                old.disconnect(self)
            except (RuntimeError, TypeError):
                pass
        worker = _DBQueryWorker(func, *args)
        setattr(self, attr, worker)
        worker.finished.connect(on_ok)
        worker.error.connect(
            lambda msg: logger.warning("趋势页查询失败: %s", msg))
        worker.start()

    def _refresh_all(self):
        self._load_alerts()
        self._load_ranking()

    # ---------- 预警区 ----------

    def _load_alerts(self):
        if self._active_source() != "kd":
            self._lbl_alert_empty.setText("趋势看板仅支持 kd 数据源（当前为 xqzg）")
            self._lbl_alert_empty.show()
            self._clear_alert_rows()
            return
        self._run_query("_alerts_worker", table_db.query_kd_alerts, (7,),
                        self._on_alerts)

    def _on_alerts(self, alerts):
        self._last_refresh = datetime.now()
        self._clear_alert_rows()
        if not alerts:
            self._lbl_alert_empty.setText("暂无突增预警 — 各设备错误率平稳")
            self._lbl_alert_empty.show()
            return
        self._lbl_alert_empty.hide()
        lay = self._alert_body.layout()
        for info in alerts:
            lay.insertWidget(lay.count() - 1,
                             _AlertRow(info, self._jump_to_device, self._alert_body))

    def _clear_alert_rows(self):
        lay = self._alert_body.layout()
        while lay.count() > 1:  # 末尾 stretch 保留
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _jump_to_device(self, info):
        """预警条目点击 → 切到设备状态页并搜索该设备（日期定位到预警分区）"""
        win = self.window()
        device_page = getattr(win, "device_page", None)
        if device_page is None:
            return
        win.switchTo(device_page)
        device_page.focus_search(str(info.get("device_code") or ""),
                                 str(info.get("file_path") or ""))

    # ---------- 设备趋势 ----------

    def _on_search_input(self, _=""):
        """搜索防抖入口：300ms 无输入后才查候选设备"""
        self._search_timer.start()

    def _do_search_candidates(self):
        """防抖后按关键词查候选设备并填充下拉（空关键词直接清空）"""
        kw = self._search_edit.text().strip()
        if not kw:
            self._candidates = []
            self._device_combo.clear()
            return
        self._run_query("_cand_worker", self._query_candidates, (kw,),
                        self._on_candidates)

    @staticmethod
    def _query_candidates(kw):
        """最新分区内按关键词匹配的候选设备（复用分页查询的 FTS/LIKE 路径）"""
        dates = table_db.get_kd_dates()
        latest = dates[0] if dates else ""
        if not latest:
            return []
        _, rows = table_db.query_kd_page(1, 30, kw, latest)
        return [r for r in rows if str(r.get("device_code") or "")]

    def _on_candidates(self, rows):
        self._candidates = rows
        combo = self._device_combo
        combo.blockSignals(True)
        combo.clear()
        for r in rows:
            combo.addItem(
                f"{r.get('device_code', '')} · {r.get('club_name', '')} · "
                f"{r.get('table_id', '')}", r.get("device_code", ""))
        combo.blockSignals(False)
        if rows:
            combo.setCurrentIndex(0)
            self._load_trend(str(rows[0].get("device_code") or ""))
        else:
            self._chart.set_data([])
            self._lbl_trend_title.setText("未匹配到设备")

    def _on_device_chosen(self, _=None):
        """下拉选择设备 → 加载该设备近 30 天趋势"""
        code = self._device_combo.currentData()
        if code:
            self._load_trend(str(code))

    def _load_trend(self, device_code):
        if not device_code:
            return
        self._lbl_trend_title.setText(f"{device_code} · 近 30 天趋势")
        self._run_query("_trend_worker", table_db.query_kd_trend,
                        (device_code, 30), self._chart.set_data)

    # ---------- TOP N 排行 ----------

    def _load_ranking(self):
        if self._active_source() != "kd":
            return
        by = self._RANK_FIELD_MAP.get(self._sort_combo.currentText(), "error_rate")
        try:
            top = int(self._top_combo.currentText())
        except ValueError:
            top = 10
        self._run_query("_rank_worker", table_db.query_kd_ranking,
                        ("", top, by), self._on_ranking)

    def _on_ranking(self, result):
        rows = result.get("rows", [])
        self._lbl_rank_date.setText(f"数据日期 {result.get('date') or '无'}")
        t = self._rank_table
        t.setRowCount(len(rows))
        for i, r in enumerate(rows):
            status_text = _DEVICE_STATUS_MAP.get(
                str(r.get("status") or "").strip(), ("未知", None))[0]
            vals = [
                str(i + 1),
                str(r.get("club_name") or ""),
                str(r.get("table_id") or ""),
                status_text,
                f"{float(r.get('error_rate') or 0):.1f}%",
                f"{float(r.get('operation_rate') or 0):.1f}%",
                str(int(float(r.get("accuracy_count") or 0))),
                str(int(float(r.get("already_count") or 0))),
            ]
            for col, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if col == 4 and float(r.get("error_rate") or 0) > 0:
                    item.setForeground(_HF_COLOR)  # 非零错误率标红
                if col == 3:
                    color = _DEVICE_STATUS_MAP.get(
                        str(r.get("status") or "").strip(), (None, None))[1]
                    if color is not None:
                        item.setForeground(color)
                t.setItem(i, col, item)
            # 设备码存 itemData 供双击跳转
            first = t.item(i, 0)
            if first is not None:
                first.setData(Qt.ItemDataRole.UserRole,
                              str(r.get("device_code") or ""))

    def _on_rank_double_clicked(self, row, _col):
        item = self._rank_table.item(row, 0)
        code = item.data(Qt.ItemDataRole.UserRole) if item else ""
        if code:
            self._jump_to_device({"device_code": code})

class HealthPage(QWidget):
    """设备健康度管理页：健康度异常告警（wechat2-billiard 接口 health 字段）

    数据获取：每 30 分钟全量拉取最新 health（TableFetchWorker → sync_health_alerts 落库）；
    展示刷新：每 1 小时从库重载重建表格（页面载入时立即拉取+展示一次）。
    阈值判定（基准 4000）：4000 为接口默认值视为空值不算异常；
    4000~5000 健康度异常；>5000 严重异常需立即处理；>40 万为脏数据排除。
    排序：① 空闲且严重异常；② 健康度异常（4000~5000）；③ 其余严重异常。
    勾选+「已处理」：处理时记录当时 health；后续刷新 health 未变化不再展示，
    变化且仍异常则重新展示（变化后 <4000 自动消失）。
    """

    _FETCH_INTERVAL_MS = 30 * 60 * 1000    # 数据获取：每 30 分钟
    _DISPLAY_INTERVAL_MS = 60 * 60 * 1000  # 展示刷新：每 1 小时

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fetch_worker = None
        self._init_ui()

        self._fetch_timer = QTimer(self)
        self._fetch_timer.setInterval(self._FETCH_INTERVAL_MS)
        self._fetch_timer.timeout.connect(self._fetch_health_data)
        self._fetch_timer.start()

        self._display_timer = QTimer(self)
        self._display_timer.setInterval(self._DISPLAY_INTERVAL_MS)
        self._display_timer.timeout.connect(self._refresh_display)
        self._display_timer.start()

        # 页面载入立即拉取一次（完成后自动刷新展示）
        self._fetch_health_data()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = TitleLabel("健康度异常告警", self)
        header.addWidget(title)
        header.addStretch(1)
        self._lbl_sync = CaptionLabel("正在获取数据", self)
        header.addWidget(self._lbl_sync)
        layout.addLayout(header)

        hint = CaptionLabel(
            "基准 4000；>4000~5000 为健康度异常；"
            ">5000 为严重异常。"
            "勾选条目后点「已处理」，health 未变化则不再展示", self)
        layout.addWidget(hint)

        self._table = TableWidget(self)
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(
            ["", "球桌号", "球房名称", "在线状态", "健康度"])
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 40)
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_resolved = PrimaryPushButton("已处理", self)
        self._btn_resolved.setEnabled(False)
        self._btn_resolved.setToolTip("将勾选的告警标记为已处理（记录当前 health 值）")
        self._btn_resolved.clicked.connect(self._on_resolved_clicked)
        btn_row.addWidget(self._btn_resolved)
        layout.addLayout(btn_row)

    # ---------- 展示 ----------

    def _refresh_display(self):
        """重载告警条目重建表格（展示刷新定时器 / 拉取完成后调用）"""
        try:
            rows = table_db.query_health_alerts()
        except Exception as e:
            self._lbl_sync.setText(f"查询失败: {e}")
            return
        self._table.setRowCount(0)
        for r in rows:
            h = float(r.get("health") or 0)
            # 阈值分级：>5000 严重异常（红），4000~5000 健康度异常（橙）
            severe = h > table_db.HEALTH_SEVERE
            color = QColor("#ff5252") if severe else QColor("#f0a020")
            level = "严重异常" if severe else "健康度异常"
            row = self._table.rowCount()
            self._table.insertRow(row)
            cb = CheckBox(self)
            cb.setToolTip("勾选后可标记已处理")
            cb.setProperty("alert_name", r.get("name") or "")
            cb.toggled.connect(lambda _c: self._update_resolved_enabled())
            self._table.setCellWidget(row, 0, cb)
            for col, key in ((1, "name"), (2, "roomName"), (3, "onlineStatusName")):
                it = QTableWidgetItem(str(r.get(key) or ""))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, col, it)
            it_h = QTableWidgetItem(f"{h:.0f} · {level}")
            it_h.setFlags(it_h.flags() & ~Qt.ItemFlag.ItemIsEditable)
            it_h.setForeground(QBrush(color))
            self._table.setItem(row, 4, it_h)
        self._update_resolved_enabled()
        now = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        self._lbl_sync.setText(f"{len(rows)} 条异常 · 展示刷新于 {now}")

    def _iter_checkboxes(self):
        for row in range(self._table.rowCount()):
            cb = self._table.cellWidget(row, 0)
            if isinstance(cb, CheckBox):
                yield cb

    def _update_resolved_enabled(self):
        """有勾选条目时启用「已处理」按钮"""
        self._btn_resolved.setEnabled(
            any(cb.isChecked() for cb in self._iter_checkboxes()))

    # ---------- 数据获取 ----------

    def _fetch_health_data(self):
        """全量拉取球桌数据（含 health 字段）并同步告警表"""
        if self._fetch_worker is not None and self._fetch_worker.isRunning():
            return
        self._fetch_worker = TableFetchWorker(self)
        self._fetch_worker.result_ready.connect(self._on_fetch_ok)
        self._fetch_worker.error.connect(self._on_fetch_fail)
        self._fetch_worker.start()

    def _on_fetch_ok(self, rows):
        try:
            count = table_db.sync_health_alerts(rows)
        except Exception as e:
            self._lbl_sync.setText(f"同步失败: {e}")
            return
        now = QDateTime.currentDateTime().toString("HH:mm:ss")
        self._lbl_sync.setText(f"数据获取于 {now} · {count} 条异常")
        self._refresh_display()

    def _on_fetch_fail(self, msg):
        self._lbl_sync.setText(f"获取失败: {str(msg).split(chr(10))[0]}")

    # ---------- 处理交互 ----------

    def _on_resolved_clicked(self):
        """「已处理」：记录勾选条目当时的 health 值，后续未变化不再展示"""
        names = [cb.property("alert_name")
                 for cb in self._iter_checkboxes() if cb.isChecked()]
        if not names:
            return
        n = table_db.mark_health_alerts_resolved(names)
        show_info_bar(f"已标记 {n} 条告警为已处理", "success",
                      title="已处理", parent=self, duration=2500)
        self._refresh_display()


class GamePage(QWidget):
    """摸鱼中心"""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        area = ScrollArea(self)
        area.setWidgetResizable(True)
        area.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        box = QVBoxLayout(container)
        box.setContentsMargins(10, 6, 10, 10)

        self.tabs = QTabWidget(container)
        self.tabs.setDocumentMode(True)
        self.game_2048 = Game2048Widget(self.tabs)
        # 隐藏「贪吃蛇」入口：实例和页签一并注释，恢复时取消下方两行即可
        # self.game_snake = SnakeWidget(self.tabs)
        # self.tabs.addTab(self.game_snake, "贪吃蛇")
        self.reader = MoyuReaderWidget(self.tabs)
        self.tabs.addTab(self.reader, "小说阅读")
        self.tabs.addTab(self.game_2048, "2048")
        box.addWidget(self.tabs)

        area.setWidget(container)
        layout.addWidget(area)
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, index):
        """页签切换：游戏控件取键盘焦点；贪吃蛇切走暂停、切回恢复"""
        current = self.tabs.widget(index)
        snake = getattr(self, "game_snake", None)
        if snake is not None:
            if current is snake:
                snake.auto_resume()
            else:
                snake.auto_pause()
        if current is not self.reader:
            current.setFocus()

# ==================== 主窗口 ====================

class ManagementPanelWindow(FluentWindow):
    """运维管理面板：FluentWindow + 左侧导航 + 六个功能页面（球桌/设备/健康度/趋势/设置/小游戏）"""

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
        self.trend_page = TrendPage(self)
        self.trend_page.setObjectName("trendPage")
        self.health_page = HealthPage(self)
        self.health_page.setObjectName("healthPage")
        self.game_page = GamePage(self)
        self.game_page.setObjectName("gamePage")
        self.settings_page = AdminSettingsPage(self)
        self.settings_page.setObjectName("adminSettingsPage")

        # 注册导航
        self.addSubInterface(self.table_page, FluentIcon.LIBRARY, "球桌管理")
        self.addSubInterface(self.device_page, FluentIcon.IOT, "设备状态")
        # 隐藏「健康趋势」页导航入口，恢复时取消下行注释即可
        # （TrendPage 实例仍会构建但无导航入口，不会被展示；closeEvent 清理不受影响）
        # self.addSubInterface(self.trend_page, FluentIcon.PIE_SINGLE, "健康趋势")
        self.addSubInterface(self.health_page, FluentIcon.PIE_SINGLE, "设备健康度管理")
        self.addSubInterface(self.settings_page, FluentIcon.SETTING, "管理设置")
        self.addSubInterface(self.game_page, FluentIcon.GAME, "小游戏")

        # 导航亚克力与「性能选项」联动：关闭 perf_acrylic 后不再强制开启，
        # 避免关闭菜单亚克力后导航栏仍有额外核显消耗
        self.navigationInterface.setAcrylicEnabled(is_acrylic_enabled())
        self.navigationInterface.setCurrentItem(self.table_page.objectName())

        # 远程会话中心：设备状态页右键菜单按 snk 建立 frp xtcp 隧道并打开会话
        # （全局单例，与主窗口远程面板/球桌面板共享同一 frpc 进程）
        self._remote_bridge = get_session_manager()

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
        """关闭窗口时快速清理所有 Worker（目标 <300ms）

        注意：远程会话中心为全局单例，frpc/隧道可能被其他入口使用中，
        面板关闭不 shutdown，统一由主窗口 closeEvent 关闭。

        各 Worker 均为「run() 直接跑同步函数」的 QThread，无事件循环，
        quit() 是 no-op，wait(N) 只能干等函数自然跑完（最长 N 毫秒/
        每个运行中 worker）——之前对多个 worker 各 wait(2000) 导致关闭
        卡顿约 2 秒的回归。现改为：先 disconnect 防止关闭后回调，再
        requestInterruption + 一次性短等待（200ms），未退出的直接放弃；
        worker 持有引用由 GC 回收，落库操作幂等（历史补漏下次打开续补）。
        table_db 是模块级单连接，面板关闭不关。
        """
        # 停止补漏队列（防止关闭后继续拉取）
        dev = self.device_page
        getattr(dev, "_backfill_queue", []).clear()
        dev._backfill_running = False

        def _detach(worker):
            """断开信号并请求中断；未运行的 worker 直接跳过不等待。

            worker 均挂在页面属性上，引用随面板关闭一并回收；
            若被 GC 在 run() 执行中途销毁，Qt 层 C++ QThread 对象仍存活
            至线程结束，不会崩溃，落库操作幂等可重试。
            """
            if not (worker and worker.isRunning()):
                return
            try:
                # PySide6 不支持无参 QObject.disconnect()：指定接收者断开全部信号
                worker.disconnect(self)
            except (RuntimeError, TypeError):
                pass
            try:
                worker.requestInterruption()
            except RuntimeError:
                pass

        for page in (self.table_page, dev, self.trend_page,
                     self.settings_page):
            for attr in ("_worker", "_migrate_worker", "_refresh_worker",
                         "_test_worker", "_upload_worker", "_query_worker",
                         "_save_worker", "_meta_worker", "_export_worker",
                         "_time_worker", "_backfill_worker", "_backfill_save_worker",
                         "_alerts_worker", "_cand_worker", "_trend_worker",
                         "_rank_worker", "_hourly_worker"):
                _detach(getattr(page, attr, None))
        # 收集 Worker（不同设备可并行，列表管理）
        for worker in list(getattr(dev, "_collect_workers", [])):
            _detach(worker)
        # 一次性短等待：所有 worker 同时给 200ms 自行收尾；未退出的直接放弃，
        # 绝不在关闭路径上串行 wait（旧实现对每个运行中 worker 各 wait(2000)，
        # 是无事件循环线程的干等，累积出 ~2s 关闭卡顿的根因）
        QThread.msleep(200)
        super().closeEvent(event)


# ==================== 独立运行入口（调试用） ====================

if __name__ == "__main__":
    import sys
    import json
    import os
    import core.acrylic_patch  # noqa: F401
    from PySide6.QtWidgets import QApplication
    from qfluentwidgets import setTheme, setThemeColor, Theme

    def _debug_theme_color():
        """调试入口读取 settings.json 主题强调色（与主程序入口一致）"""
        try:
            p = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "settings.json")
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f).get("theme_color", "#00BCD4")
        except Exception:
            return "#00BCD4"

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    setTheme(Theme.DARK)
    setThemeColor(_debug_theme_color(), lazy=True)
    win = ManagementPanelWindow()
    win.show()
    sys.exit(app.exec())
