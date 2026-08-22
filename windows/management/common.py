# -*- coding: utf-8 -*-
"""common 模块（从 windows/management_panel.py 拆出，逻辑未改动）"""

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
    MessageBoxBase, MenuAnimationType, SwitchButton)
from qfluentwidgets.components.widgets.table_view import TableItemDelegate

from core.app_paths import get_app_dir
from core.design_tokens import SEMANTIC
from core.frp_remote import get_session_manager
from core.perf import is_acrylic_enabled, is_animation_enabled
from core.secrets import decrypt_settings, encrypt_settings
from core.utils import launch_sibling_app, show_info_bar
from workers.table_worker import (TableFetchWorker, DevicesFetchWorker,
                                  SnookerOmFetchWorker, MigrateImageWorker,
                                  LoginTestWorker, get_active_api_source,
                                  CATEGORY_DIRS)
from workers.collect_worker import (CollectFilesWorker, ZipUploadWorker,
                                    clip_base_name, date_from_base,
                                    resolve_device_dir,
                                    fuzzy_match_device_dir, norm_device_suffix)
from database import table_db
from windows.mysql_sync_card import MysqlSyncCard
from windows.moyu_widgets import Game2048Widget, SnakeWidget, MoyuReaderWidget
from windows.image_viewer import is_image_file

logger = logging.getLogger(__name__)

# 包内共享符号：页面/对话框模块经 ``from windows.management.common import *``
# 引入（含下划线辅助名，故显式列出 __all__）
__all__ = [
    "_dir_similarity", "_popup_ani_type", "_patch_menu_animation",
    "TABLE_COLUMNS", "_STATUS_COLORS", "DEVICE_COLUMNS",
    "FILE_FIELD_CATEGORIES", "CATEGORY_FILE_FIELDS", "MIGRATE_CATEGORIES",
    "FIELD_CATEGORY", "MIGRATE_DEST_OPTIONS", "_MIGRATE_BTN_QSS_TMPL",
    "_migrate_btn_qss", "_MIGRATE_BTN_QSS", "_MIGRATABLE_FIELDS",
    "_LINK_COLOR", "_DEVICE_STATUS_MAP", "_load_settings", "_save_settings",
    "_fmt_size", "_EXPORT_MAX_ROWS", "_HF_DAYS", "_HF_THRESHOLD", "_HF_COLOR",
    "_query_kd_page_with_stats", "_query_xqzg_page_with_stats",
    "_confirm_offline_connect", "_open_in_explorer", "_show_export_bar",
    "_DBQueryWorker", "_SortableTableWidget", "_ReadOnlySelectDelegate",
    "_copy_table_selection", "_FIXED_ROW_HEIGHT", "_fit_table_rows",
]


def _dir_similarity(name: str, candidates: list) -> int:
    """计算候选目录名与目标设备码的相似度分数（0-100）

    结构化规则优先（复用 norm_device_suffix 后缀归一化）：
    精确同名 100；店号前缀相同 + 后缀归一化相等 95；前缀归一化
    相等 + 后缀相同 90；前缀相同 55；字符级相似度（difflib）最高
    60 分兜底。返回对全部候选码的最高分。
    """
    n = str(name or "").strip()
    best = 0
    # 遍历全部候选码取最高分：任一码（table_id 或 device_code）对上目录就算命中
    for cand in candidates:
        c = str(cand or "").strip()
        # 两边都得有内容才谈得上结构化比较，空串直接跳过防误加分
        if not c or not n:
            continue
        # 一字不差直接满分返回，后面不用再比，省得被字符级相似度拉低
        if n == c:
            return 100
        score = 0
        np, _, ns = n.rpartition("-")
        cp, _, cs = c.rpartition("-")
        # 没有"-"时 rpartition 给空串，不进前缀档，防止无分隔符目录硬套结构化规则
        if np and cp:
            nps, cps = norm_device_suffix(np), norm_device_suffix(cp)
            # 前缀完全相同时基础 55 分；后缀归一化相等再 +40（结构化最高档）
            if np == cp:
                score += 55
                if ns and norm_device_suffix(ns) and norm_device_suffix(ns) == norm_device_suffix(cs):
                    score += 40
                # 封顶 95 不给满：前缀同只说明同一家店，店里可能有多台相似命名的机器
            # 仅前缀归一化相同（如 281 与 281-08 的店号部分）：+30，后缀相同再 +60
            elif nps and nps == cps:
                score += 30
                if ns and ns == cs:
                    score += 60
        # 字符级相似度兑底：最高 60 分，与结构化得分取较大值
        # 取 max 而不是累加：两套规则在衡量同一个名字，叠加会虚高误判；
        # 注意字符级上限 60 高于「仅前缀相同」档（55）与归一化前缀档（30），
        # 只有 90/95 两档能稳赢字符级兜底
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
    "运行中": QColor(SEMANTIC["success"]),
    "空闲": QColor(SEMANTIC["info"]),
    "下线": QColor(SEMANTIC["neutral"]),
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
# 数据驱动的单一事实源：收集/展示/迁移全靠这张映射表，新增分类只加一行，
# 下面的反查字典与详情面板自动跟上，不用改任何业务代码；顺序即展示顺序
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
# 版本没有对应的服务器迁移目录，不是迁移目标，这里剔除防止误发起迁移

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


def _migrate_btn_qss(base_hex):
    """迁移按钮 QSS：hover/pressed 由令牌基色派生，不再手写六组明度值"""
    from core.design_tokens import lighten, darken
    return _MIGRATE_BTN_QSS_TMPL.format(
        base=base_hex, hover=lighten(base_hex, 0.12),
        pressed=darken(base_hex, 0.18))


_MIGRATE_BTN_QSS = {
    # 翡翠绿：正常/在用类语义
    "使用": _migrate_btn_qss(SEMANTIC["success"]),
    # 琥珀金：精度/校准类语义
    "精度": _migrate_btn_qss(SEMANTIC["warning"]),
    # 玫瑰红：问题/告警类语义
    "问题": _migrate_btn_qss(SEMANTIC["danger"]),
    # 石板灰：废弃/归档类语义
    "废弃": _migrate_btn_qss(SEMANTIC["neutral"]),
}

# 可迁移的文件分类字段（其余分类点开后仅查看，不显示迁移按钮）
_MIGRATABLE_FIELDS = {
    "except_files", "operation_files", "accuracy_files", "already_files", "rubbish_files",
}

# 可点击查看文件列表的单元格链接色
_LINK_COLOR = QColor(SEMANTIC["info"])

# 设备状态码 → (中文描述, 颜色)
# kd 接口 status 字段：0=下线 1=空闲 2=使用
_DEVICE_STATUS_MAP = {
    "0": ("下线", QColor(SEMANTIC["neutral"])),
    "1": ("空闲", QColor(SEMANTIC["warning"])),
    "2": ("使用", QColor(SEMANTIC["success"])),
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


# ==================== MySQL 自动推送已下线 ====================
# 镜像推送机制 B（API 同步后静默推送 SQLite → MySQL）已随架构评审整体移除：
# 当前为 MySQL 主库 + SQLite 兜底双后端模式，读写直连 MySQL，无镜像推送
# 路径。原 _trigger_auto_mysql_sync / _mysql_auto_worker 已删除。


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
_HF_COLOR = QColor(SEMANTIC["danger"])


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


def _query_xqzg_page_with_stats(page_no, page_size, keyword, date,
                                sort_key, sort_desc, hf_days):
    """xqzg 分页查询 + 高频问题统计（Worker 线程内一次完成，界面零同步查询）"""
    total, rows = table_db.query_xqzg_page(
        page_no, page_size, keyword, date, sort_key, sort_desc,
        include_files=False)  # 列表页轻量行，文件 JSON 点开行时按 id 懒加载
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

# 运行中的查询 worker 保活集合：QThread 对象在 run() 结束前保持强引用，
# 防止频繁刷新/切换时旧 worker 被 GC 销毁导致
# `QThread: Destroyed while thread is still running` 崩溃
_running = set()


class _DBQueryWorker(QThread):
    """后台数据库查询/保存 Worker（通用封装）

    将 table_db 的同步操作移到工作线程，避免阻塞 GUI。
    通过 result_ready 信号返回结果，error 信号返回异常信息。
    """
    result_ready = Signal(object)  # 查询/保存结果（不能命名为 finished，会遮蔽 Qt 原生 finished）
    error = Signal(str)

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        _running.add(self)  # 保活：线程退出前不被 GC
        # 用 Qt 原生 finished 做清理：它由 Qt 在 run() 返回后（线程已标记结束）发射，
        # Queued 到主线程执行 _release 时销毁是安全的。
        # 注意：PySide6 中 super().finished 会被子类同名信号遮蔽，不能用于此目的
        self.finished.connect(self._release)

    def _release(self):
        """线程已退出，可安全释放保活引用（Queued 到主线程执行）"""
        _running.discard(self)

    def run(self):
        try:
            result = self.func(*self.args, **self.kwargs)
            self.result_ready.emit(result)
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
