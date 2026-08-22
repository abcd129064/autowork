# -*- coding: utf-8 -*-
"""device_page 模块（从 windows/management_panel.py 拆出，逻辑未改动）"""

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

from windows.management.common import *  # noqa: F401,F403
from windows.management.dialogs import (
    AddRecordDialog, EditSnkDialog, DeviceDirHealDialog,
    DeviceFilesDialog, UploadListDialog,
)

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
        self._query_worker.result_ready.connect(self._on_refresh_query)
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
        """从右侧滑入（动画关闭时直接定位，跳过过渡）"""
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
        """滑出并隐藏（动画关闭时直接移出可视区）"""
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
        """按数据源同步日期选择器状态（xqzg 与 kd 同样支持 file_path 日期筛选）"""
        self._date_picker.setEnabled(True)
        for attr in ("_btn_date_prev", "_btn_date_next"):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setEnabled(True)
        self._date_picker.setToolTip("")
        # 同步管理每小时定时拉取：xqzg 与 kd 都按当前日期定时拉取
        timer = getattr(self, "_hourly_timer", None)
        if timer is not None and not timer.isActive():
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
        date = self._current_date()
        if self._active_source() == "xqzg":
            # xqzg 与 kd 同样按 file_path 日期分区筛选
            self._query_worker = _DBQueryWorker(
                _query_xqzg_page_with_stats,
                self._page_no, self._page_size, keyword, date,
                self._sort_key, self._sort_desc, _HF_DAYS)
        else:
            # include_files=False：列表页只查轻量字段，文件 JSON 点开行时按 id 懒加载；
            # 高频问题统计与分页查询同批在 Worker 线程完成，界面零同步查询
            self._query_worker = _DBQueryWorker(
                _query_kd_page_with_stats,
                self._page_no, self._page_size, keyword, date,
                self._sort_key, self._sort_desc, _HF_DAYS)
        # 用默认参数 d/kw 快照本次查询的日期与关键词：lambda 闭包捕获的是变量引用，
        # 不快照的话，等回调触发时用户已切日期/改关键词，会拿新值误判本次查询已过期
        self._query_worker.result_ready.connect(
            lambda result, d=date, kw=keyword: self._on_query_finished(result, d, kw))
        # 查询/排序出错不能静默：xqzg 旧库缺 status 列时列表头排序曾整表静默失败
        self._query_worker.error.connect(
            lambda msg: show_info_bar(str(msg).split(chr(10))[0], "error",
                                      title="查询失败", parent=self, duration=4000))
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
        # 只有 kd 接口支持服务端按关键词过滤，xqzg 只能全量拉再本地筛
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
        rows = data  # 拉取 Worker 翻页后 result_ready 直接发射 rows 列表
        keyword = getattr(self, "_fetch_keyword", "")
        if self._active_source() == "xqzg":
            date = self._current_date()
            self._save_worker = _DBQueryWorker(table_db.save_xqzg, rows, date)
            date_desc = date
        else:
            date = self._current_date()
            # 落库函数按有无关键词二选一，是搜索态数据安全的命门：
            # 带 keyword 拉到的是部分数据，误走 save_kd 会按日期全量替换，
            # 把同日其他设备的本地数据一并删掉，所以只能走 upsert_kd 增量
            save_func = table_db.upsert_kd if keyword else table_db.save_kd
            self._save_worker = _DBQueryWorker(save_func, rows, date)
            date_desc = date
        # 默认参数 dd 快照本次日期描述，理由同 _load_local 里的 lambda 快照
        self._save_worker.result_ready.connect(
            lambda count, dd=date_desc: self._on_save_finished(count, dd))
        # 落库失败必须可见（如远程 MySQL 表缺列）：否则按钮不恢复、数据空白无反馈
        self._save_worker.error.connect(
            lambda msg, dd=date_desc: self._on_save_error(msg, dd))
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
        # 不补的话表里停着上一个关键词的结果，用户看到的和搜的对不上；
        # 补拉同样走 upsert_kd 增量，不会破坏本地全量数据
        current_kw = self._search_edit.text().strip()
        if (current_kw and current_kw != getattr(self, "_fetch_keyword", "")
                and self._active_source() == "kd"):
            self._fetch_api_keyword(current_kw)

    def _on_save_error(self, msg, date_desc=""):
        """落库失败：恢复同步按钮并提示首行错误（静默失败会让用户看到空白/旧数据）"""
        self._sync_btn.setEnabled(True)
        show_info_bar(str(msg).split(chr(10))[0], "error",
                      title="保存失败", parent=self, duration=5000)

    def _on_search_error(self, msg):
        """设备搜索失败：恢复同步按钮并提示"""
        self._sync_btn.setEnabled(True)
        self._lbl_info.setText(f"搜索失败: {msg}")
        show_info_bar(msg, "error", title="搜索失败", parent=self, duration=4000)

    def _populate(self, rows, hf_stats=None):
        """行数据 → 表格：状态码转中文着色、高频设备标红、文件列数量+预览 tooltip"""
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
        """重算页码并同步分页控件/状态文本/数据时间"""
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
        """搜索防抖到期：回第一页重查，kd 数据源额外向服务端拉匹配设备"""
        self._page_no = 1
        self._load_local()
        # 搜索状态（kd 数据源）：同步向服务端发起带 keyword 的请求，
        # 只拉取匹配设备并增量更新本地库（防抖已合并逐字输入）
        keyword = self._search_edit.text().strip()
        if keyword and self._active_source() == "kd":
            self._fetch_api_keyword(keyword)

    def _on_prev_page(self):
        """上一页"""
        if self._page_no > 1:
            self._page_no -= 1
            self._load_local()

    def _on_next_page(self):
        """下一页（末页保护）"""
        if self._page_no < max(1, math.ceil(self._total / self._page_size)):
            self._page_no += 1
            self._load_local()

    def _on_page_size_changed(self, text):
        """每页条数变更：回第一页重查"""
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
        """获取指定行完整数据（含 8 类文件清单）：按数据源懒加载完整行

        列表页缓存为轻量行（不含文件 JSON），仅在点开详情时单点查询；
        kd/xqzg 接口同套字段、落库均含文件清单，按数据源分别懒加载。
        """
        row = self._get_row_at(row_idx)
        if not row:
            return row
        row_id = row.get("id")
        if row_id is None:
            return row
        if self._active_source() == "kd":
            return table_db.get_kd_row_full(row_id) or row
        return table_db.get_xqzg_row_full(row_id) or row

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
        # xqzg 与 kd 同样按 file_path 日期分区，迁移路径拼接方式一致；
        # 此前误以为 xqzg 无日期分区而禁用，现已与 kd 对齐
        can_migrate = (bool(fields)
                       and all(f in _MIGRATABLE_FIELDS for f in fields))
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
            src_category=src_cat, dest_category=dest_cat,
            source=self._active_source())
        self._migrate_worker.success.connect(
            lambda count: self._on_migrate_ok(fname, dest_cat))
        self._migrate_worker.error.connect(self._on_migrate_fail)
        self._migrate_worker.start()
        show_info_bar(f"{fname} → 「{dest_cat}」...", "info",
                      title="迁移中", parent=self, duration=1500)

    def _on_migrate_ok(self, fname, dest_cat):
        """迁移成功：提示并静默刷新；迁到精度/问题时自动写台账并收集文件"""
        show_info_bar(f"{fname} 已移动到「{dest_cat}」", "success",
                      title="迁移成功", parent=self, duration=2500)
        self._silent_refresh()
        # 迁移到精度/问题后自动收集对应视频/日志到 upload 目录
        # （无需再点精度/问题单元格；数据尚未刷回，先把 fname 并入字段列表）
        # 分类→收集字段由这张映射表驱动，与 CATEGORY_DIRS 的字段→服务器目录一脉相承
        field = {"精度": "accuracy_files", "问题": "already_files"}.get(dest_cat)
        if field:
            row = dict(getattr(self._file_panel, "_row", None) or {})
            # 数据库还没刷回新字段，先在本地副本里把 fname 并进去，否则收集会漏掉刚迁的这张
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
        """迁移失败：提示错误首行并静默刷新"""
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
        # 三级匹配（映射/精确/模糊）全失败才走到自愈向导；向导选中会落库 manual 映射，
        # 下次同一设备直接从第①级映射命中，不再弹窗
        if not device_id:
            device_id = self._heal_device_dir(videos_dir, candidates)
            if not device_id:
                show_info_bar("本地设备目录不存在: " + " / ".join(c for c in candidates if c)
                              + "\n可在主界面设备列表找到对应文件夹，右键日志文件→添加到上传目录",
                              "warning", title="无法收集", parent=self, duration=5000)
                return
            fuzzy_note = f"已手动映射 → {device_id}"
        bases = sorted({b for b in (clip_base_name(f) for f in (row.get(field) or [])) if b})
        # 文件列表为空说明没东西可收集，静默返回，避免弹个"收集 0 个"的无意义任务
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
        # 空串候选码先剔掉：相似度算法遇空串直接跳过，留着只会白跑一轮
        scored = []
        try:
            entries = os.listdir(videos_dir)
        except OSError:
            # 列目录失败就给空候选，让向导弹窗兜底提示，别在这里抛异常打断收集
            entries = []
        for name in entries:
            # upload 是收集工作区、videos 是媒体子目录，都不是设备码目录，混进候选会误导选择
            if name in ("upload", "videos"):
                continue
            if not os.path.isdir(os.path.join(videos_dir, name)):
                continue
            score = _dir_similarity(name, cands)
            # 0 分说明完全搭不上边，直接丢弃，避免无关目录稀释候选列表
            if score > 0:
                scored.append((score, name))
        # 双键排序：分数取负实现降序，同分时按目录名升序，保证每次弹出顺序稳定
        scored.sort(key=lambda t: (-t[0], t[1].lower()))
        # 只取前 12：候选再多弹窗也装不下，头部已经是相似度最高的
        dlg = DeviceDirHealDialog(self, videos_dir, cands, scored[:12])
        if not dlg.exec() or not dlg.chosen_dir:
            return ""
        chosen = dlg.chosen_dir
        try:
            # fromkeys 去重：table_id 和 device_code 可能相同，避免同一映射重复落库
            for cand in dict.fromkeys(cands):
                table_db.set_device_mapping(cand, chosen, source="manual")
        except Exception:
            # 落库失败只记日志不阻断本次收集，代价是下次还会再弹一次向导
            logger.warning("自愈向导映射落库失败: %s -> %s", cands, chosen,
                           exc_info=True)
        return chosen

    def _on_collect_done(self, device_id, copied, missing, worker, sub_id=None):
        """收集完成：回填台账收集结果并按缺失情况提示"""
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
        """弹窗预览 upload 目录待上传文件清单（空目录提示先收集）"""
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
        """打包上传成功：回填台账上传结果并提示（本地目录已清空）"""
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
        """打包上传失败：恢复按钮并提示错误首行"""
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
        rows = data  # 拉取 Worker 翻页后 result_ready 直接发射 rows 列表
        date = self._current_date()
        if self._active_source() == "xqzg":
            self._save_worker = _DBQueryWorker(table_db.save_xqzg, rows, date)
        else:
            self._save_worker = _DBQueryWorker(table_db.save_kd, rows, date)
        self._save_worker.result_ready.connect(self._on_refresh_save_finished)
        self._save_worker.error.connect(self._on_save_error)
        self._save_worker.start()

    def _on_refresh_save_finished(self, _count):
        """刷新保存完成：重新加载并刷新文件面板"""
        self._load_local()
        self._file_panel.refresh_if_visible()

    def _on_refresh_error(self, msg):
        """静默刷新失败：仅警告提示，不阻断当前操作"""
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
        rows = data  # 拉取 Worker 翻页后 result_ready 直接发射 rows 列表
        today = QDate.currentDate().toString("yyyy/MM/dd")
        self._save_worker = _DBQueryWorker(table_db.save_kd, rows, today)
        self._save_worker.result_ready.connect(
            lambda count: self._on_hourly_save_finished(count, today))
        self._save_worker.start()

    def _on_hourly_save_finished(self, count, today=""):
        """每小时保存完成：仅当用户正在查看当天时刷新表格"""
        if self._current_date() == today:
            self._load_local()
        self._lbl_time.setText(f"自动更新: {datetime.now().strftime('%H:%M:%S')}（{count} 台）")

    def _on_hourly_error(self, msg):
        """定时拉取失败：不打断用户，仅状态栏静默提示"""
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
        self._export_worker.result_ready.connect(
            lambda result, p=path, s=src: self._on_export_query(result, p, s))
        self._export_worker.error.connect(
            lambda msg: show_info_bar(str(msg).split(chr(10))[0], "error",
                                      title="导出失败", parent=self, duration=4000))
        self._export_worker.start()

    def _on_export_query(self, result, path, src):
        """导出查询完成：按数据源拼表头写 CSV（utf-8-sig）"""
        _total, rows = result
        # 两数据源接口同套字段、落库均含 device_code：统一带设备编码列
        header = ["设备编码"] + [c[1] for c in DEVICE_COLUMNS]
        keys = ["device_code"] + [c[0] for c in DEVICE_COLUMNS]
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
        """生成应有日期序列（最早日期→今天，无数据则近 60 天），对比已存
        分区找出缺失日期静默串行补拉（每次最多 10 天，不弹 UI 仅日志）

        kd 与 xqzg 都按 file_path 日期分区存储，补漏策略一致；
        依据当前数据源选择对应的 worker / 落库函数 / 日期查询函数。
        """
        if getattr(self, "_backfill_running", False):
            return
        src = self._active_source()
        if src == "kd":
            worker_cls = DevicesFetchWorker
            save_func = table_db.save_kd
            get_dates = table_db.get_kd_dates
            get_synced = table_db.get_kd_synced_dates
        elif src == "xqzg":
            worker_cls = SnookerOmFetchWorker
            save_func = table_db.save_xqzg
            get_dates = table_db.get_xqzg_dates
            get_synced = table_db.get_xqzg_synced_dates
        else:
            return
        try:
            # 已覆盖 = 有数据的分区 + 拉过但接口为空的日期（sync_meta）
            covered = set(get_dates()) | set(get_synced())
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
        self._backfill_save_func = save_func
        logger.info("历史补漏[%s]：缺失 %d 天，本次补 %d 天: %s",
                    src, len(missing), len(self._backfill_queue),
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
            # 只延后不跳过：3 秒后重试，否则用户长时间操作会让补漏链永久停摆
            QTimer.singleShot(3000, self._backfill_next)
            return
        date = self._backfill_queue.pop(0)
        worker = self._backfill_save_func  # noqa: F841  占位便于理解
        # 根据数据源选择对应的拉取 Worker（kd 用 DevicesFetchWorker，xqzg 用 SnookerOmFetchWorker）
        src = self._active_source()
        if src == "kd":
            worker = DevicesFetchWorker(file_path=date)
        else:
            worker = SnookerOmFetchWorker(file_path=date)
        # lambda 用默认参数快照当前日期：补漏链逐天换日期，不快照会把结果存错日期
        worker.result_ready.connect(
            lambda data, dt=date: self._on_backfill_done(data, dt))
        worker.error.connect(
            lambda msg, dt=date: self._on_backfill_error(msg, dt))
        self._backfill_worker = worker
        worker.start()

    def _on_backfill_done(self, data, date):
        """补漏拉取完成：异步落库（复用 _DBQueryWorker，不阻塞 GUI）"""
        rows = data  # 拉取 Worker 翻页后 result_ready 直接发射 rows 列表
        self._backfill_save_worker = _DBQueryWorker(
            self._backfill_save_func, rows, date)
        self._backfill_save_worker.result_ready.connect(
            lambda count, dt=date: self._on_backfill_saved(count, dt))
        self._backfill_save_worker.error.connect(
            lambda msg, dt=date: self._on_backfill_save_error(msg, dt))
        self._backfill_save_worker.start()

    def _on_backfill_saved(self, count, date):
        """补漏落库完成：正查看该日期则顺带刷新，间隔后继续下一天"""
        logger.info("历史补漏 %s 完成：%d 台设备", date, count)
        # 只有用户正盯着这个日期才刷新表格，否则后台补漏会不停打断浏览
        if self._current_date() == date:
            self._load_local()  # 用户正查看该日期 → 顺带刷新
        QTimer.singleShot(self._BACKFILL_INTERVAL, self._backfill_next)

    def _on_backfill_save_error(self, msg, date):
        """补漏落库失败：记日志后继续下一天（不阻断补漏链）"""
        # 单点失败不阻断链：这天的缺口还在库里，下次启动补漏会再轮到它
        logger.warning("历史补漏保存失败 %s: %s", date, str(msg).split(chr(10))[0])
        QTimer.singleShot(self._BACKFILL_INTERVAL, self._backfill_next)

    def _on_backfill_error(self, msg, date):
        """补漏拉取失败：记日志后继续下一天"""
        # 同理不因单天拉取失败停链：缺失日期没落库，下次补漏还会重试
        logger.warning("历史补漏拉取失败 %s: %s", date, str(msg).split(chr(10))[0])
        QTimer.singleShot(self._BACKFILL_INTERVAL, self._backfill_next)

    def resizeEvent(self, e):
        """窗口尺寸变化时同步滑出面板的高度与位置（面板贴右缘）"""
        super().resizeEvent(e)
        panel = getattr(self, "_file_panel", None)
        if panel and panel.isVisible():
            panel.setFixedSize(panel.width(), self.height())
            panel.move(self.width() - panel.width(), 0)
