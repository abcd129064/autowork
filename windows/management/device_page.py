# -*- coding: utf-8 -*-
"""设备状态页与其右侧文件面板：管理面板的第二页。

数据分成两条链路，界面只读本地库：本地库查询走异步 Worker，接口数据必须
由用户点「同步数据」或每小时定时任务显式拉取后落库。这样翻页、搜索、排序
都不会被远程数据库或接口往返拖住，代价是列表可能落后于服务端一个拉取周期。

kd 与 xqzg 两个数据源字段同构，但落库表、查询函数、拉取 Worker 各有一套，
所有分叉集中在 _active_source() 的返回值上；新增数据源按这些分叉逐点对齐。
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
    MessageBoxBase, MenuAnimationType, SwitchButton)
from qfluentwidgets.components.widgets.table_view import TableItemDelegate

from core.app_paths import get_app_dir
from core.design_tokens import SEMANTIC
from core.frp_remote import get_session_manager
from core.perf import (is_acrylic_enabled, is_animation_enabled,
                       apply_table_smooth_mode)
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
from windows.management.moyu_widgets import (Game2048Widget, SnakeWidget,
                                                  MoyuReaderWidget)
from windows.management.image_viewer import is_image_file

logger = logging.getLogger(__name__)

# 共用常量、字段映射与表格混入类集中在 common，由它的 __all__ 显式导出
# （含下划线辅助名）。星号导入沿用管理面板拆分前的命名空间，避免此处再列几十个符号。
from windows.management.common import *  # noqa: F401,F403
from windows.management.dialogs import (
    AddRecordDialog, EditSnkDialog, DeviceDirHealDialog,
    DeviceFilesDialog, UploadListDialog,
)

# ==================== 文件列表面板（右侧滑出） ====================

class _FileNameTable(TextDragSelectTableMixin, TableWidget):
    """文件名单列表格：字符级拖选复制（Mixin），双击打开图片查看

    拖选/复制能力来自 TextDragSelectTableMixin（与健康度页告警表格同一
    实现）；本类只做两件事：
    - row_text 覆盖：Ctrl+C / 右键「复制整行」的回退文本截取 kd 前缀
      （与 FileListPanel._clip_name 口径一致）；
    - 双击钩子：委托面板打开图片查看器（非图片条目由面板提示）。
    """

    # 只有第 0 列参与字符级拖选，Mixin 按这个元组判断命中列
    _TEXT_COLS = (0,)   # 单列文件名

    def __init__(self, panel, parent=None):
        # 反向持有面板而不复制条目：行号与面板 _entries 下标一一对应，
        # 复制一份副本会在面板重载清单后立刻失去同步。
        self._panel = panel
        super().__init__(parent)

    def row_text(self, row):
        # 与 Ctrl+C、右键菜单统一走面板的截断口径：三条复制路径若各截各的，
        # 用户从不同入口拿到的文件名会不一致。
        it = self.item(row, 0)
        return self._panel._clip_name(it.text()) if it is not None else ""

    def _on_text_double_clicked(self, row, col) -> bool:
        # 返回 True 表示事件已消费：Mixin 据此跳过「进入编辑态」的默认分支，
        # 否则双击会先打开查看器、再落进一个编辑框
        self._panel._open_entry_preview(row)
        return True


class FileListPanel(QWidget):
    """设备状态页右侧滑出的文件清单面板。

    只负责展示、选择和把动作转给 DevicePage：迁移成功后还要同步表格计数、
    写入上传工作区与台账，这些状态面板都拿不到，所以动作一律由页面执行。

    面板持有打开那一刻的行快照 _row，清单可能落后于服务端；refresh_if_visible
    是唯一的对齐入口，由页面在静默刷新落库后调用。
    """

    # 固定宽度：面板靠位置动画定位，宽度若跟随窗口变化，动画终点与贴边坐标
    # 会失配，所以只跟随父容器的高度。
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

    def _apply_smooth_mode(self):
        """按当前生效的平滑滚动设置刷新表格滚动模式（管理面板设置页联动）

        面板每次打开都重放一次设置：面板对象常驻但会隐藏，
        用户改完设置再打开时不该还沿用旧模式。
        """
        apply_table_smooth_mode(self._list, panel="management")

    def _init_ui(self):
        self.setFixedWidth(self._PANEL_WIDTH)
        self.setObjectName("fileListPanel")
        # 自定义 QWidget 子类必须开启该属性，stylesheet 背景才会被绘制（否则透明）
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # 背景只能用 setStyleSheet 显式色值并跟随 themeChanged 重应用：palette(...) 只在
        # setStyleSheet 当时解析一次，setCustomStyleSheet 依赖的 CustomStyleSheetWatcher
        # 不装在普通 QWidget 上，两者都会让面板背景停在旧主题或直接透明。
        qconfig.themeChanged.connect(self._apply_theme)
        self._apply_theme()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        # 预览与关闭两个图标按钮放在标题行右侧：面板没有标题栏，
        # 关闭入口只能放这里，否则用户只能靠点页面空白处收起
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

        self._list = _FileNameTable(self, self)
        # 滚动模式跟随管理面板设置页的全局开关，覆盖本表默认值；不接这条联动，
        # 用户在设置里关掉平滑滚动后，这张表仍旧惯性滚动。
        apply_table_smooth_mode(self._list, panel="management")
        self._list.setColumnCount(1)
        self._list.setHorizontalHeaderLabels(["文件名"])
        self._list.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._list.verticalHeader().setVisible(False)
        self._list.setAlternatingRowColors(True)
        self._list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # 字符级拖选由 Mixin 提供，双击不再进编辑态而是打开图片查看器；
        # 保留编辑态会挡住双击入口，且编辑态能做的事情拖选已经覆盖。
        self._list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
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

        # 这行提示是面板唯一的功能发现入口：三个操作都没有常驻按钮
        hint = CaptionLabel("拖选复制 · 双击打开图片 · 右键更多", self)
        layout.addWidget(hint)

    def _apply_theme(self):
        """按当前主题应用面板背景（显式色值，不依赖 palette 引用）

        两个主题各一套固定色，分别对齐 qfluentwidgets 的暗底与亮底；
        用半透明色会把下层页面内容透出来。
        """
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
        """打开面板展示指定分类的文件列表：重建条目、默认选中首条并滑入

        fields 决定清单范围，row 提供各字段的文件数组，两者都来自点击时的
        完整行快照，面板不自行查库，所以同一行反复打开结果一致。
        can_migrate 为 False 时整行按钮隐藏而不是置灰：总数、正常等视图
        本来就不可迁移，留着禁用按钮只会让用户反复点击。
        """
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
        """按当前 _fields 重建文件条目与表格（源分类随条目保留，仅供迁移使用）

        每次整表重建而不做增量 diff：单设备清单最多几百条，重排成本远低于
        维护增量状态的出错成本。重建后行号与 _entries 下标严格一一对应，
        按下标取条目的入口都依赖这条不变式。
        """
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
        """数据刷新后，若面板可见则异步重新加载当前设备的文件列表

        数据源分派：xqzg 与 kd 分表存储，必须按当前数据源取对应表的行。
        此前固定查 kd_status，xqzg 数据源下要么查不到该设备（面板清单一直
        停在迁移前的条数），要么取回同名设备的 kd 行（清单串源）。
        """
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
        query_fn = (table_db.query_xqzg_by_device
                    if self._device_page._active_source() == "xqzg"
                    else table_db.query_kd_by_device)
        self._query_worker = _DBQueryWorker(query_fn, code, date)
        self._query_worker.result_ready.connect(self._on_refresh_query)
        self._query_worker.start()

    def apply_local_move(self, fname: str, src_cat: str, dest_cat: str) -> dict:
        """迁移成功后本地即时生效：内存快照里把 fname 移到目标分类并重绘

        迁移接口已确认成功，但静默刷新要翻页拉全接口再整分区落库（实测
        5-10s），期间面板仍显示迁移前的条数（用户报障：原来 3 条，提示成功
        后还是 3 条）。这里先在本地行快照上做同一动作，列表立刻反映迁移
        结果；静默刷新回来后以服务端口径覆盖（幂等）。

        Returns:
            变更字段 → 新值（供表格行同步计数与本地快照落库）；无变更返回 {}。
        """
        changes = table_db.move_file_in_row(
            self._row, FIELD_CATEGORY.get(src_cat, ""),
            FIELD_CATEGORY.get(dest_cat, ""), fname)
        if not changes:
            return {}
        # 连续迁移手感：重绘后停在原位置的下一条（清单已空则置灰按钮）
        rows = sorted({it.row() for it in self._list.selectedItems()})
        keep = rows[0] if rows else 0
        self._reload_entries()
        if self._entries:
            self._list.selectRow(min(keep, len(self._entries) - 1))
        self._on_selection_changed()
        return changes

    def _on_refresh_query(self, fresh):
        """异步查询完成：用服务端最新行替换快照并重绘

        只在拿到非空结果时替换：查询失败返回空时保留旧快照，
        面板宁可显示可能过期的清单，也好过突然清空。
        """
        if fresh:
            self._row = fresh
            self._reload_entries()

    # ---------- 复制与迁移交互 ----------
    # 复制有三种入口（Ctrl+C、右键、图片查看器内），口径统一交给 _clip_name；
    # 迁移则一律转给 DevicePage，面板只负责给出文件名与源分类。

    def _on_copy_shortcut(self):
        """Ctrl+C：拖选文本优先（字符级选区），否则复制选中行文件名（截取 kd 前缀）

        拖选优先是尊重更精确的意图：用户特意选了半截文件名时，复制整行
        会覆盖掉这个选择，还得手动再删一遍。
        """
        if self._list._has_selection():
            text = self._list.selection_text()
            if text:
                QApplication.clipboard().setText(text)
                return
        rows = sorted({it.row() for it in self._list.selectedItems()})
        if rows:
            item = self._list.item(rows[0], 0)
            if item:
                QApplication.clipboard().setText(self._clip_name(item.text()))

    def _on_selection_changed(self):
        """选中状态变化：有选中条目时启用底部迁移按钮（预览按钮仅限图片）

        预览按钮单独判 is_image_file：非图片条目照样能选中和迁移，
        但打开查看器只会得到一句提示，不如直接禁用。
        """
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
        图片，支持左右翻页与迁移；已打开时复用窗口跳转到新选中图片

        非模态是刻意的：用户常要一边看图一边在面板里继续迁移，
        模态框会把这两步强行串行化。
        """
        rows = sorted({it.row() for it in self._list.selectedItems()})
        if rows:
            self._open_entry_preview(rows[0])

    def _open_entry_preview(self, row):
        """按条目行号打开图片查看器（双击/按钮共用入口）

        非图片条目给出轻提示（双击图片以外的文件不再进入旧编辑态）。
        """
        if not (0 <= row < len(self._entries)):
            return
        if not is_image_file(self._entries[row][0]):
            show_info_bar("该条目不是图片，双击仅支持打开图片文件",
                          "warning", title="无法预览",
                          parent=self, duration=2000,
                          bottom_offset=self._info_raise())
            return
        from windows.management.image_viewer import ImageViewerDialog
        dlg = self._preview_dlg
        if dlg is not None and dlg.isVisible():
            # 复用打开中的查看器：同步最新条目快照并跳转，避免窗口堆叠
            dlg.set_entries(self._entries, row)
            dlg.raise_()
            dlg.activateWindow()
            return
        dlg = ImageViewerDialog(
            self._entries, row,
            file_path=self._device_page._current_date(),
            device_code=self._row.get("device_code", ""),
            device_page=self._device_page,
            can_migrate=self._migrate_wrap.isVisible(),
            dest_options=MIGRATE_DEST_OPTIONS,
            btn_qss=_MIGRATE_BTN_QSS,
            parent=self.window())
        self._preview_dlg = dlg
        # 关闭即销毁（迁移不关窗，仅手动关闭时销毁）：销毁时清引用，避免残留隐藏窗口
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dlg.destroyed.connect(lambda: setattr(self, '_preview_dlg', None))
        dlg.show()

    def _on_migrate_btn(self, dest_cat):
        """底部迁移按钮：将当前选中的文件直接迁移到 dest_cat

        这里只做参数解包，迁移与回显都在 DevicePage：面板拿不到表格行与台账。
        """
        rows = sorted({it.row() for it in self._list.selectedItems()})
        if not rows or not (0 <= rows[0] < len(self._entries)):
            return
        fname, src_cat = self._entries[rows[0]]
        self._device_page.migrate_file(fname, src_cat, dest_cat)

    def _show_context_menu(self, pos):
        """右键条目：复制选中（拖选文本）/ 复制文件名 / 复制全部（迁移已移至底部固定按钮）

        迁移不放进菜单：它是高频动作且目标固定四个，固定在底部比每次
        展开菜单少一次点击，菜单项也不必随条目类型变化。
        """
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

        # 字符级拖选区（MixIn）：优先提供「复制选中」，与文本编辑器习惯一致
        sel_text = self._list.selection_text() \
            if self._list._has_selection() else ""
        if sel_text:
            act_sel = Action(FluentIcon.COPY, "复制选中", self)
            act_sel.triggered.connect(
                lambda: QApplication.clipboard().setText(sel_text))
            menu.addAction(act_sel)

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

        # 菜单动画跟随全局开关：关闭动画时直接弹出，不做缩放过渡
        menu.exec_(self._list.viewport().mapToGlobal(pos), aniType=_popup_ani_type())

    def _clip_name(self, fname: str) -> str:
        """复制时截取文件名 'kd' 之前的字符

        设备照片文件名形如 20260802_125112kd-200055-20046-100835.jpg，
        复制时通常只需要时间戳部分（kd 前）。不含 'kd' 的文件名原样返回。
        """
        # idx > 0 而非 >= 0：文件名以 kd 开头时截出来是空串，
        # 复制结果为空不如原样返回，至少还能拿到完整文件名
        fname = str(fname)
        idx = fname.find("kd")
        return fname[:idx] if idx > 0 else fname

    def _info_raise(self) -> int:
        """面板贴底 InfoBar 抬升量：避开底部迁移按钮行或提示行

        数字对应实际布局高度：有按钮行时约 64px，只剩提示行时约 40px。
        改底部布局必须同步这里，否则提示会被按钮盖掉一半。
        """
        return 64 if self._migrate_wrap.isVisible() else 40

    def _copy_all_names(self):
        """复制面板中全部文件名到剪贴板（每行一个，均截取 kd 前缀）

        截断口径必须与单条复制一致，否则批量粘贴出去会出现两种文件名格式。
        """
        names = [self._clip_name(f) for f, _ in self._entries]
        QApplication.clipboard().setText("\n".join(names))
        show_info_bar(f"{len(names)} 个文件名已复制到剪贴板（已截取 kd 前缀）", "success",
                      title="已复制", parent=self, duration=2000,
                      bottom_offset=self._info_raise())

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
                          title="无法定位", parent=self, duration=3000,
                          bottom_offset=self._info_raise())
            return
        videos_dir = (_load_settings().get("videos_dir") or "").strip()
        if not videos_dir or not os.path.isdir(videos_dir):
            show_info_bar("videos_dir 未配置或目录不存在", "warning",
                          title="无法定位", parent=self, duration=3000,
                          bottom_offset=self._info_raise())
            return
        # 设备目录三级解析（映射表 → 精确同名 → 模糊匹配，同收集链路）
        candidates = [str(self._row.get("table_id") or "").strip(),
                      str(self._row.get("device_code") or "").strip()]
        device_dir, _note, _src = resolve_device_dir(videos_dir, candidates)
        if not device_dir:
            show_info_bar("本地未找到设备目录: " + " / ".join(c for c in candidates if c),
                          "warning", title="无法定位", parent=self, duration=3500,
                          bottom_offset=self._info_raise())
            return
        # 同名日志查找：日期子目录优先，其次设备根目录（与主窗口加载规则一致）
        log_fname = ""
        dev_dir = os.path.join(videos_dir, device_dir)
        # 先日期子目录、再设备根目录：主窗口就是按这个顺序加载的，
        # 这里找错位置会让跳转过去之后主窗口又报找不到文件
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
                          "warning", title="本地无此文件", parent=self, duration=4000,
                          bottom_offset=self._info_raise())
            return
        main_win = self._find_main_window()
        if main_win is None:
            show_info_bar("主窗口未打开", "error",
                          title="无法跳转", parent=self, duration=3000,
                          bottom_offset=self._info_raise())
            return
        # 跳转交给主窗口：日志页的加载、定位、滚动都由它统一处理，
        # 这里只把设备目录/日期/文件名三件事实传过去。
        main_win.focus_log_file(device_dir, date_str, log_fname)

    def _find_main_window(self):
        """从 QApplication 顶层窗口中查找主窗口实例（延迟导入避免循环依赖）

        每次现查而不缓存引用：主窗口可能关闭后重开，缓存会指向已销毁对象，
        跳转时崩在 C++ 侧。延迟导入则避免与 main_window 互相 import。
        """
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
        """从右侧滑入（动画关闭时直接定位，跳过过渡）

        先移到父容器右缘外再 show：不先移出可视区，show 的瞬间会在
        最终位置闪一下，动画看起来像「跳过去再滑进来」。
        """
        parent = self.parent()
        pw, ph = parent.width(), parent.height()
        # 高度取父容器高度而不是固定值：页面本身随窗口缩放，
        # 面板要贴着右缘占满整列
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
        # 必须留住引用：动画对象被 GC 后过渡会中途停止，面板停在半路
        self._anim = anim
        anim.start()

    def slide_out(self):
        """滑出并隐藏（动画关闭时直接移出可视区）

        动画期间面板仍可见，所以不能先 hide，隐藏交给 finished 信号。
        """
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
        # 动画结束才隐藏：提前 hide 会让面板在移出途中瞬间消失
        anim.finished.connect(self.hide)
        self._anim = anim
        anim.start()


# ==================== 页面2: 设备状态 ====================

class DevicePage(QWidget):
    """设备状态页：按日期查看 kd / xqzg 设备数据，支持文件列表与迁移。

    懒构建：管理面板打开时只创建本对象，UI 与首次查询推迟到 showEvent，
    避免三个页面一次性构造拖慢面板打开。

    kd 与 xqzg 的分叉全部落在 _active_source() 上（查询函数、落库函数、
    拉取 Worker 各一套），其余交互共用同一条链路。
    """

    # 可点击查看文件列表的列：字段 key → (面板标题, 文件字段列表)。统计列展开
    # 对应的 *_files 字段；pic_total 是全部文件总数，所以列出所有分类。
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
        # 外部跳转预设（趋势页预警点击）：未构建时暂存，_lazy_init 应用；
        # 所以 focus_search 必须先把值写进字段再看 _lazy_built，顺序不能换。
        self._pending_search = ""
        self._pending_date = ""

    def _apply_smooth_mode(self):
        """按当前生效的平滑滚动设置刷新本页表格（管理面板设置页联动）

        用 getattr 而不是直接取 _table：本页懒构建前还没有表格，而设置页
        可能在面板刚打开时就广播刷新，直接属性访问会 AttributeError。
        """
        if getattr(self, "_table", None) is not None:
            apply_table_smooth_mode(self._table, panel="management")

    def focus_search(self, keyword: str, file_path: str = ""):
        """外部设置搜索关键词并触发查询（如趋势页预警跳转）

        页面未懒构建时存入 pending 字段，_lazy_init 完成后自动应用；
        file_path 非空时同步把日期选择器切到该分区日期。

        两个桥接字段用完即清：不清的话下次进入本页会重复跳转到同一次
        预警，用户会以为自己又点了一次。
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
        # Worker 引用按用途分开持有：手动拉取、静默刷新、每小时拉取与补漏可能
        # 同时存在，共用一个槽会让后一轮覆盖前一轮，既判不了重也判不了过期。
        self._worker = None
        self._migrate_worker = None
        self._refresh_worker = None
        self._hourly_worker = None  # 每小时定时拉取专用，与手动搜索 _worker 隔离
        self._collect_workers = []   # 收集 Worker 列表（不同设备可并行）
        self._upload_worker = None   # 打包上传 Worker（全局唯一）
        self._query_worker = None    # 异步查询 Worker
        self._full_row_seq = 0       # 完整行懒加载序号（只采纳最后一次点击的结果）
        self._save_worker = None     # 异步保存 Worker
        # 迁移后本地快照写回 Worker（apply_file_move，仅落一行增量 UPDATE）
        self._snapshot_worker = None
        # 静默刷新在途标记 + 「补一次」排队标记（在途期间又有迁移时置位，
        # 避免整分区重拉把刚迁移的条目落回原分类）
        self._refresh_pending = False
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
        # 默认看昨天而不是今天：当天数据要等设备陆续上报，早上打开时往往是空的
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
        # 缓存构建较重（实测 ~77ms，qfluentwidgets 1.11.2），不再用 singleShot(0) 排队尾
        # （仍在首切「到可操作」的感知窗口内串行执行），改为**首查填充完成后**再空闲
        # 延迟触发（见 _on_query_finished）；构建完成前点击日历走默认路径（功能不受影响）
        self._calendar_cache_pending = True
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
        # 历史日期自动补漏延后 2 秒启动：避开首次加载，也避免和首查抢同一批
        # 接口配额；全程静默，只写日志
        QTimer.singleShot(2000, self._backfill_missing_dates)

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 12)
        root.setSpacing(10)

        # --- 工具栏 ---
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        toolbar.addWidget(QLabel("日期:", self))
        # 日历用缓存视图（见 _apply_calendar_cache）而不是默认构造：默认每次
        # 弹出都要现生成七万多个日期项，首次打开要等半秒
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
        # 步进按钮与选择器合成一组：查历史分区时连点「前一天」比开日历快，
        # 间距压到 2px 让三者看起来像一个控件
        date_group = QHBoxLayout()
        date_group.setSpacing(2)
        date_group.addWidget(self._date_picker)
        date_group.addWidget(self._btn_date_prev)
        date_group.addWidget(self._btn_date_next)
        toolbar.addLayout(date_group)

        self._search_edit = SearchLineEdit(self)
        self._search_edit.setPlaceholderText("搜索")
        # 搜索框固定 160px：它只过滤设备码/球桌号，拉长也显示不了更多字
        self._search_edit.setFixedWidth(160)
        self._search_edit.textChanged.connect(self._on_search_input)
        toolbar.addWidget(self._search_edit)
        toolbar.addStretch(1)

        # 右侧三个与文件相关的动作：导出只读、上传清单只预览、打包上传会同时
        # 改动服务器与本地目录，所以放在最右并按风险逐级加重提示。
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

        # 同步是唯一会写库的工具栏入口，放在最右；每次拉取都置灰，
        # 避免用户在拉取途中重复点击触发并发写入
        self._sync_btn = PushButton(FluentIcon.SYNC, "同步数据", self)
        self._sync_btn.setToolTip("从服务器拉取所选日期的设备数据")
        self._sync_btn.clicked.connect(self._search_from_api)
        toolbar.addWidget(self._sync_btn)
        root.addLayout(toolbar)

        # --- 表格 ---
        self._table = _SortableTableWidget(self)
        # 性能（2026-08-26）：管理面板表格接入平滑滚动开关（覆盖→全局）
        apply_table_smooth_mode(self._table, panel="management")
        self._sort_key = ""   # 当前排序字段（DEVICE_COLUMNS key），空=默认 id 顺序
        self._sort_desc = False
        self._table.setColumnCount(len(DEVICE_COLUMNS))
        self._table.setHorizontalHeaderLabels([c[1] for c in DEVICE_COLUMNS])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        # 只读委托：单元格内容全部来自数据库，允许编辑只会改到界面，
        # 下次查询又被覆盖，用户会以为改了但没保存
        self._table.setItemDelegate(_ReadOnlySelectDelegate(self._table))
        self._table.verticalHeader().setVisible(False)
        # 固定行高：行高随内容变化会让翻页时整表高度跳动，滚动位置也跟着漂移
        self._table.verticalHeader().setDefaultSectionSize(_FIXED_ROW_HEIGHT)
        self._table.setAlternatingRowColors(True)
        # 关闭换行：长文本换行会撑高行、破坏固定行高；截断交给 ElideRight，
        # 全文交给 tooltip
        self._table.setWordWrap(False)
        # 排序直连 sectionClicked 手写（更新箭头 + SQL 重查），不走 QTableView 内置
        # 排序：后者只重排当前页，且 Qt 内部 C++ 路径会绕过 Python 重写直接动模型
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
        # 列宽可拖但保留设计稿初值，最后一列吃满剩余宽度；不这么设会在右侧
        # 留一条空白，看起来像少了一列
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for i, (_, _, w) in enumerate(DEVICE_COLUMNS):
            self._table.setColumnWidth(i, w)
        root.addWidget(self._table, 1)

        # --- 状态栏（统一格式：共X条 | 每页:下拉 | 页码 | 数据时间） ---
        # 状态栏固定格式：左侧总数、右侧数据日期，中段页码与每页条数靠伸缩
        # 撑开；窗口变窄时先压缩文字，避免控件互相重叠。
        pager = QHBoxLayout()
        pager.setSpacing(6)
        self._lbl_info = QLabel("", self)
        pager.addWidget(self._lbl_info)
        pager.addStretch(1)
        pager.addWidget(QLabel("每页:", self))
        self._size_combo = ComboBox(self)
        # 每页选项 20/50/100/300（已移除 800/1000：大页同步填充/测量开销过大）
        # 先屏蔽信号再设初值：addItems 会触发 currentTextChanged，
        # 初始化阶段白查一次数据库
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
        # 页面构造时就建好并隐藏，不做懒加载：首次点击文件列要求即时滑出，
        # 临时构造会把控件创建时间叠在 200ms 动画前面。
        self._file_panel = FileListPanel(self)

    def _apply_calendar_cache(self):
        """复用缓存 CalendarView：替换 DatePicker._showCalendarView 为快速显示，避免每次点击重建

        日视图默认生成 ±100 年约 7.3 万个日期项（约 0.5s），构建缓存期间临时把范围
        缩到今年 ±10 年（约 40ms），构建后立即还原；缓存实例仅用于本页日期筛选，
        数据窗口近十年足够，且不影响其他日历组件。

        类属性替换必须用 finally 还原：日历类是全局共享的，漏还原会让其他
        页面的日历也只生成 ±10 年，且不会报错，很难发现。
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

            # 改类属性而不是实例属性：DatePicker 每次弹出都会新建 DayScrollView，
            # 只有类级别才能覆盖到那一次构建
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
                # 先断开再重连：picker 每次弹出都走这里，不断开会让同一个缓存
                # 视图累积多份信号连接，选一次日期触发多次查询。
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
        """返回日期选择器中的日期，格式如 2026/08/02（kd 接口 file_path 格式）

        拼本地路径、查库分区、传接口参数三处都依赖这个格式，所以只在
        这里转换一次，别处直接复用返回值，避免两种分隔符混用。
        """
        return self._date_picker.date.toString("yyyy/MM/dd")

    def _on_date_changed(self, _=None):
        """日期切换：回到第一页、收起文件面板并按新日期重新查询

        面板必须先收起：它展示的是旧日期的文件清单，留着会误导用户
        以为新日期也有这些文件。
        """
        self._page_no = 1
        panel = getattr(self, "_file_panel", None)
        if panel:
            panel.slide_out()
        self._load_local()

    def _step_date(self, delta_days: int):
        """日期步进：负数前移、正数后移；setDate 触发 dateChanged → 现有加载链路

        统一走信号而不直接调 _load_local：日期文本、分页复位、面板收起
        都由同一条链路处理，绕过去就会出现日期变了而列表还是旧的。
        """
        self._date_picker.setDate(self._date_picker.date.addDays(delta_days))

    # ---------- 数据加载 ----------
    # 翻页、搜索、换日期、排序、刷新最终都落到 _load_local：新增入口也必须
    # 走这条链路，否则页码与 _total 会各自漂移。

    def _active_source(self) -> str:
        """当前启用的设备数据源：'kd' / 'xqzg'

        每次现取而不缓存：设置页切换数据源后无需通知本页，下一次查询
        自然走新源；缓存反而会在切换后继续读旧值。
        """
        return get_active_api_source()

    def _apply_source_date_state(self):
        """按数据源同步日期选择器状态（xqzg 与 kd 同样支持 file_path 日期筛选）

        早期 xqzg 不按日期分区，这里曾把日期控件禁掉；现在两个源都按
        file_path 存，恒可用。保留函数是为了切换数据源时有统一复位点。
        """
        # 两个数据源都按 file_path 日期分区，日期筛选恒可用；保留这个函数是给
        # 将来「不按日期分区」的数据源留一个单点开关，而不是散落到各调用处。
        self._date_picker.setEnabled(True)
        for attr in ("_btn_date_prev", "_btn_date_next"):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setEnabled(True)
        # 清掉可能残留的禁用说明：此刻日期控件确定可用
        self._date_picker.setToolTip("")
        # 同步管理每小时定时拉取：xqzg 与 kd 都按当前日期定时拉取
        timer = getattr(self, "_hourly_timer", None)
        if timer is not None and not timer.isActive():
                timer.start()

    def showEvent(self, event):
        """页面显示时刷新数据源状态（数据源可能在管理设置页被切换）

        首次显示先完成懒构建（UI + 首页加载），避免管理面板打开时同步构造；
        之后每次显示都重查一次，因为数据源可能在设置页被切换，缓存行属于旧源。
        """
        super().showEvent(event)
        if not self._lazy_built:
            self._lazy_built = True
            self._lazy_init()
            return
        self._apply_source_date_state()
        self._load_local()

    def _load_local(self):
        """异步分页查询本地数据库，快速切换时取消前一个 Worker

        取消用 requestInterruption + disconnect：只请求中断的话，上一轮
        的 result_ready 仍会投递过来，把旧结果画到新条件上。
        """
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
                                      title="查询失败", parent=self, duration=4000, bottom_offset=self._info_raise()))
        self._query_worker.start()

    def _on_query_finished(self, result, date="", keyword=""):
        """查询完成回调：更新表格与分页（统计已随分页同批返回）

        日历缓存故意延后到首查之后 800ms：构建要几十毫秒，放在首查前
        会直接拖慢用户看到的第一屏数据。
        """
        total, rows, hf_stats = result
        self._total = total
        self._populate(rows, hf_stats)
        # 用回调带回的日期/关键词而不是读控件：回调可能晚于用户的下一次操作，
        # 读控件会把状态栏标成新条件、数据却还是旧查询的。
        self._update_pager(date, keyword)
        # 首查填充完成后，用户感知窗口已过，再空闲 800ms 构建日历缓存（N1）
        if getattr(self, "_calendar_cache_pending", False):
            self._calendar_cache_pending = False
            QTimer.singleShot(800, self._apply_calendar_cache)

    def _search_from_api(self):
        """按当前日期从服务器拉取设备数据（kd 数据源携带搜索词减少传输量）

        这是唯一会改动本地库的显式入口：拉完后按日期整分区替换，
        其余的查询与翻页都只读。
        """
        date = self._current_date()
        if not date:
            return
        if self._worker and self._worker.isRunning():
            return
        # 单飞：并发拉同一日期时，两个 save_kd 会按到达顺序互相整分区覆盖，
        # 先到的旧快照可能盖掉后到的新快照，所以正在拉取就放弃本次。
        self._sync_btn.setEnabled(False)
        src = self._active_source()
        # 搜索状态（kd 数据源）：携带 keyword 只拉取匹配设备，减少数据传输
        # 只有 kd 接口支持服务端按关键词过滤，xqzg 只能全量拉再本地筛
        keyword = self._search_edit.text().strip() if src == "kd" else ""
        self._fetch_keyword = keyword
        self._last_fetch_silent = False
        # 先写状态栏再启动 Worker：拉取可能几秒，这行文字是唯一的进行中反馈
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
        # 标记静默：搜索触发的拉取不弹「共 N 台设备」，逐字搜索会刷屏
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

        落库同样放到 Worker：save_kd 一次要写整天的设备行，同步执行会让
        界面在「搜索完成」提示出现前先卡住几秒。
        """
        rows = data  # 拉取 Worker 翻页后 result_ready 直接发射 rows 列表
        keyword = getattr(self, "_fetch_keyword", "")
        if self._active_source() == "xqzg":
            date = self._current_date()
            # xqzg 接口只能全量拉取，按日期整分区替换正是期望行为
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
        """保存完成：重置页码、刷新表格并提示（静默拉取不弹窗）

        回第一页而不是留在当前页：新数据的条数与排序都可能变了，
        停在第 5 页看到的是另一批设备，用户会以为数据错乱。
        """
        self._sync_btn.setEnabled(True)
        self._page_no = 1
        self._load_local()
        if not getattr(self, "_last_fetch_silent", False):
            show_info_bar(f"{date_desc} 共 {count} 台设备", "success",
                          title="搜索完成", parent=self, duration=2500, bottom_offset=self._info_raise())
        # 请求在途期间关键词已变化：用最新关键词补拉一次（防抖合并后只补最新值）
        # 不补的话表里停着上一个关键词的结果，用户看到的和搜的对不上；
        # 补拉同样走 upsert_kd 增量，不会破坏本地全量数据
        current_kw = self._search_edit.text().strip()
        if (current_kw and current_kw != getattr(self, "_fetch_keyword", "")
                and self._active_source() == "kd"):
            self._fetch_api_keyword(current_kw)

    def _on_save_error(self, msg, date_desc=""):
        """落库失败：恢复同步按钮并提示首行错误（静默失败会让用户看到空白/旧数据）

        只取首行：MySQL 驱动的报错常带整段 SQL 与调用栈，全塞进 InfoBar
        会撑爆宽度，完整信息在日志里。
        """
        self._sync_btn.setEnabled(True)
        show_info_bar(str(msg).split(chr(10))[0], "error",
                      title="保存失败", parent=self, duration=5000, bottom_offset=self._info_raise())

    def _on_search_error(self, msg):
        """设备搜索失败：恢复同步按钮并提示

        按钮必须恢复：否则「同步数据」会永久置灰，用户只能重启页面。
        """
        self._sync_btn.setEnabled(True)
        self._lbl_info.setText(f"搜索失败: {msg}")
        show_info_bar(msg, "error", title="搜索失败", parent=self, duration=4000, bottom_offset=self._info_raise())

    def _populate(self, rows, hf_stats=None):
        """行数据 → 表格：状态码转中文着色、高频设备标红、文件列数量+预览 tooltip

        填充期间先关信号与界面更新，最后一次性绘制：几百行逐条 setItem
        会触发逐行重排，大页（300 行）时肉眼可见闪烁。
        """
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
                # 优先按 device_code 匹配，无则回退 table_id：历史数据里两种键都
                # 被写过，只认一种会让另一批设备永远标不出高频问题。
                _dev = str(item.get("device_code") or "").strip()
                hf = (hf_stats["by_device"].get(_dev, 0) if _dev else
                      hf_stats["by_table"].get(
                          str(item.get("table_id") or "").strip(), 0))
                for c, (key, _, _) in enumerate(DEVICE_COLUMNS):
                    val = item.get(key)
                    if key == "status":
                        # 设备状态码 → 中文 + 颜色标识
                        # 未收录的状态码统一显示「未知」而不是留空：
                        # 空单元格会让人以为查询丢了字段
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
                        # 只预览前 30 个：行高固定，tooltip 过长会顶出屏幕可视区
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
        # 行高统一在这里定：单元格内容长度不同，逐行自适应会让整表高度抖动
        _fit_table_rows(self._table)

    def _on_header_clicked(self, column):
        """表头点击：同列切换升/降序，新列默认升序；更新箭头后重查数据库"""
        if not (0 <= column < len(DEVICE_COLUMNS)):
            return
        key = DEVICE_COLUMNS[column][0]
        # 同列再点才翻转方向，换列一律从升序开始：否则新列会继承上一列的
        # 降序状态，出现「点了升序箭头却按降序排」。
        if key == self._sort_key:
            desc = not self._sort_desc
        else:
            desc = False
        order = (Qt.SortOrder.DescendingOrder if desc
                 else Qt.SortOrder.AscendingOrder)
        self._table.setSortIndicator(column, order)
        # 箭头先更新；排序条件没变就不必再查库（重复点同方向是空操作）
        if key == self._sort_key and desc == self._sort_desc:
            return
        self._sort_key = key
        self._sort_desc = desc
        self._page_no = 1
        self._load_local()

    def _update_pager(self, date="", keyword=""):
        """重算页码并同步分页控件/状态文本/数据时间

        控件状态集中在这里更新：翻页、搜索、刷新都调用它，散落到各调用
        处就会出现「下一页按钮可点、页码却说没有下一页」这类不一致。
        """
        # 总数为 0 也算 1 页：页码显示 1/1 比 0/0 更符合直觉
        total_pages = max(1, math.ceil(self._total / self._page_size))
        # 总页数变小后当前页可能越界（换页大小、搜索收窄结果集），先夹到末页，
        # 否则页码显示成 3/2，这一页查询还返回空。
        self._page_no = min(self._page_no, total_pages)
        self._lbl_page.setText(f"{self._page_no}/{total_pages}")
        self._btn_prev.setEnabled(self._page_no > 1)
        self._btn_next.setEnabled(self._page_no < total_pages)
        kw = f"（搜索: {keyword}）" if keyword else ""
        self._lbl_info.setText(f"共 {self._total} 台设备{kw}")
        self._lbl_time.setText(f"数据时间: {date or '全部'}")

    # ---------- 交互 ----------
    # 搜索、翻页、排序都只改查询条件再走 _load_local，不直接改表格行；
    # 这样任何入口的结果都经过同一套统计与分页更新。

    def _on_search_input(self, _=""):
        """搜索输入防抖入口：重启 300ms 定时器，连续输入合并为一次查询

        每次输入都重启，只有停手 300ms 才真正查库；不合并的话逐字输入
        会打出十几次远程查询，输入框直接卡住。
        """
        self._search_timer.start()

    def _do_search(self):
        """搜索防抖到期：回第一页重查，kd 数据源额外向服务端拉匹配设备"""
        self._page_no = 1
        self._load_local()
        # 搜索状态（kd 数据源）：同步向服务端发起带 keyword 的请求，
        # 只拉取匹配设备并增量更新本地库（防抖已合并逐字输入）
        # 本地结果先出，服务端拉取在后台补全：等接口回来再刷会让人以为搜索没反应
        keyword = self._search_edit.text().strip()
        if keyword and self._active_source() == "kd":
            self._fetch_api_keyword(keyword)

    def _on_prev_page(self):
        """上一页（首页时静默忽略，按钮此时已置灰，连点也不会越界）"""
        if self._page_no > 1:
            self._page_no -= 1
            self._load_local()

    def _on_next_page(self):
        """下一页（末页保护：总页数按 _total 现算，不读按钮的 enabled 状态）"""
        if self._page_no < max(1, math.ceil(self._total / self._page_size)):
            self._page_no += 1
            self._load_local()

    def _on_page_size_changed(self, text):
        """每页条数变更：回第一页重查（下拉项固定，解析失败直接忽略）"""
        try:
            size = int(text)
        except ValueError:
            return
        if size != self._page_size:
            self._page_size = size
            self._page_no = 1
            self._load_local()

    def _get_row_at(self, row_idx) -> dict:
        """获取表格指定行的完整数据（直接取当前页缓存，不再重查数据库）

        行号越界返回空 dict 而不是抛异常：点击与分页刷新存在竞态，
        点到的行可能已被新结果替换，调用方按空处理即可。
        """
        rows = getattr(self, '_current_rows', None) or []
        if 0 <= row_idx < len(rows):
            return rows[row_idx]
        return {}

    def _request_full_row(self, row_idx, on_ready):
        """按 id 懒加载完整行（含 8 类文件清单）：查询放到后台线程

        列表页缓存为轻量行（不含文件 JSON），仅在点开详情时按 id 单点查询；
        kd/xqzg 接口同套字段、落库均含文件清单，按数据源分别懒加载。

        2026-09-20 修复卡死：此前在 GUI 线程直连 MySQL，一旦连接被服务端
        断开（远程主机 RST / wait_timeout），点击文件统计列要一直等到 TCP
        超时（实测约 15s 假死）。改走 _DBQueryWorker，界面不再被网络裹住，
        失败时弹 InfoBar 而不是静默无反应。

        ``on_ready(row_or_None)``：None 表示查询失败（已弹提示）。
        """
        row = self._get_row_at(row_idx)
        row_id = row.get("id") if row else None
        if not row or row_id is None:
            on_ready(row)
            return
        # 按数据源选完整行查询函数：两张表字段同名但表不同，
        # 用错函数会查出同 id 的另一条记录
        fn = (table_db.get_kd_row_full if self._active_source() == "kd"
              else table_db.get_xqzg_row_full)
        # 序号只增不减：连点两行时多个查询并发在跑，先发的慢查询可能后到，
        # 回调用它比对后丢弃过期结果，避免张冠李戴。
        self._full_row_seq += 1
        seq = self._full_row_seq
        worker = _DBQueryWorker(fn, int(row_id))
        worker.result_ready.connect(
            lambda full, s=seq, fb=row: self._on_full_row(s, on_ready,
                                                          full or fb))
        worker.error.connect(lambda msg, s=seq: self._on_full_row_error(s, msg))
        worker.start()

    def _on_full_row(self, seq, on_ready, row):
        """只在序号仍是最新时回调，过期结果静默丢弃。

        后发先至和先发后至都可能发生，所以用递增序号判断，而不是查
        「worker 是否还在跑」——后者在回调时已经跑完了。
        """
        if seq != self._full_row_seq:
            return  # 已有更新的点击，丢弃过期结果
        on_ready(row)

    def _on_full_row_error(self, seq, msg):
        """读取完整行失败：只对最新一次点击提示，过期失败不再打扰用户"""
        if seq != self._full_row_seq:
            return
        show_info_bar(str(msg).split(chr(10))[0], "error",
                      title="读取文件清单失败", parent=self, duration=4000, bottom_offset=self._info_raise())

    def _show_context_menu(self, pos):
        """右键菜单：查看文件列表 / 复制文件列表 / 复制单元格 / 远程连接 / 清除映射

        菜单项按当前列动态拼：只有统计列才有「复制文件列表」，只有配了
        snk 的设备才有可用的远程项，避免一屏点不动的入口。
        """
        idx = self._table.indexAt(pos)
        if not idx.isValid():
            return
        self._table.selectRow(idx.row())
        menu = RoundMenu(parent=self._table)

        act_files = Action(FluentIcon.LIBRARY, "查看文件列表", self._table)
        act_files.triggered.connect(lambda: self._show_files_dialog(idx.row()))
        menu.addAction(act_files)

        # 统计列命名约定 *_count ↔ *_files：右键复制按约定推出清单字段，
        # 新增统计列若不走这个约定，菜单项会静默不出现。
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
        # 菜单按「看 → 复制 → 远程 → 清除映射」排列：最后一项会改本地映射，
        # 放末尾减少误点
        self._add_remote_actions(menu, idx.row())
        # C4：清除错误设备映射（auto 映射导致收集错误时人工删除，下次收集重新匹配）
        self._add_mapping_actions(menu, idx.row())
        menu.exec_(self._table.viewport().mapToGlobal(pos), aniType=_popup_ani_type())

    def _add_mapping_actions(self, menu, row_idx):
        """右键菜单追加「清除设备映射」项（仅该设备存在映射记录时显示）

        没有映射时整段不出现而不是置灰：这条菜单只对「收集到了错误目录」
        的用户有意义，平时留着只会拉长菜单。
        """
        row = self._get_row_at(row_idx)
        # 两个码都查：历史映射表里 table_id 与 device_code 都当过键，只查一种
        # 会让另一半设备的「清除映射」入口消失。
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
        """确认后删除设备映射（删除后下次收集重新走模糊匹配）

        删除前先弹确认：映射一旦清掉，下次收集要重新猜目录，
        猜错就会把文件收进别人的文件夹。
        """
        # 文案点名「误收集的文件要自行清理」：这里只删映射不动磁盘，下次收集
        # 一旦重新匹配到同一目录，用户会以为映射根本没删掉。
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
                          title="删除失败", parent=self, duration=3000, bottom_offset=self._info_raise())
            return
        if n:
            show_info_bar(f"设备映射 {device_code} 已删除", "success",
                          title="已清除", parent=self, duration=2500, bottom_offset=self._info_raise())
        else:
            # 删除返回 0 说明记录已被别处删掉，按提示而非报错处理：
            # 用户的意图（这条映射不该存在）其实已经达成。
            show_info_bar("该设备映射不存在或已被删除", "info",
                          title="提示", parent=self, duration=2500, bottom_offset=self._info_raise())

    def _add_remote_actions(self, menu, row_idx):
        """右键菜单追加远程连接入口（SSH 终端 / SFTP 文件 / 远程桌面）

        三个入口的共同前提是球桌管理里配了 snk（xtcp 隧道名）；缺 snk 时
        菜单项保留可见但置灰，直接隐藏会让人以为功能没做。
        """
        row = self._get_row_at(row_idx)
        table_id = str(row.get("table_id") or "").strip()
        snk = table_db.get_snk_by_name(table_id)
        # A2 前置检查所需：行内设备状态与最后上报日期（xqzg 无 status 则跳过检查）
        status = str(row.get("status") or "").strip()
        # report 取行内 file_path（该设备最后上报的分区日期），缺失时退回当前
        # 选择日期，用于下线确认文案里说明「最后一次上报」是什么时候。
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
        """委托统一远程会话中心建立 xtcp 隧道并打开会话

        本页只提供设备行里的 snk 与状态，隧道建立、窗口管理、失败通知
        都由桥接负责，避免三个页面各写一套。
        """
        # A2 远程前置检查：行内 status=0（下线）时先确认，不阻止但醒目提示
        # 下线设备仍允许连接，只是先要用户确认：现场排障往往正要连一台「下线」的机器
        if str(status).strip() == "0":
            if not _confirm_offline_connect(self, report or "未知"):
                return
        # 远程桥接挂在管理面板窗口上而不是本页：会话窗口与通知中心由面板统一
        # 持有，本页只当调用方，避免每个页面各建一套隧道管理器。
        bridge = getattr(self.window(), "_remote_bridge", None)
        if bridge is None:
            show_info_bar("远程桥接未初始化", "error",
                          title="无法远程", parent=self, duration=3000, bottom_offset=self._info_raise())
            return
        bridge.open_session(kind, snk, table_id, notifier=self, source="设备状态")

    def _show_files_dialog(self, row_idx):
        """弹出该行全部文件分类的详情弹窗（完整行按 id 异步懒加载）

        弹窗用 exec 模态：详情里的迁移/复制都针对同一行，
        允许并行打开多个反而容易对同一文件重复操作。
        """
        def _open(row):
            if row:
                DeviceFilesDialog(row, self).exec()

        self._request_full_row(row_idx, _open)

    def _copy_file_field(self, row_idx, field):
        """复制指定文件字段的全部文件名到剪贴板（每行一个）

        名单取自按 id 懒加载的完整行：列表页缓存的行不含文件 JSON，
        直接读缓存只会复制出空内容。
        """
        def _do(row):
            files = (row or {}).get(field) or []
            QApplication.clipboard().setText("\n".join(files))
            show_info_bar(f"{len(files)} 个文件名已复制到剪贴板", "success",
                          title="已复制", parent=self, duration=2000, bottom_offset=self._info_raise())

        self._request_full_row(row_idx, _do)

    # ---------- 文件面板与迁移 ----------
    # 迁移改的是服务端分类，本地只做即时回显；最终事实以 _silent_refresh
    # 拉回来的服务端数据为准，本地改动不参与对账。

    def eventFilter(self, obj, event):
        """悬停在可点击的文件统计列上时显示手型光标，移出恢复默认箭头

        光标是唯一的可点击提示：这些格子看起来和普通文本没区别，
        不换光标用户不会想到能点开。
        """
        # Qt 自带的手型光标只在整列可点击时才生效，本表只有部分统计列可点，
        # 所以在 viewport 上自己判命中；命中用 indexAt，不手算列边界。
        if obj is self._table.viewport() and event.type() == QEvent.Type.MouseMove:
            idx = self._table.indexAt(event.position().toPoint())
            if idx.isValid() and DEVICE_COLUMNS[idx.column()][0] in self._FILE_VIEW_FIELDS:
                self._table.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self._table.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        return super().eventFilter(obj, event)

    def _on_cell_clicked(self, row, col):
        """点击文件统计列：滑出右侧文件面板展示对应分类（仅可点击列响应）

        完整行（文件清单）在后台线程按 id 取，取到再开面板：避免死连接/慢
        网络把界面卡住（见 _request_full_row）。

        只有 _FILE_VIEW_FIELDS 里的统计列会响应：其他列是选中与编辑语义，
        在这里统一拦掉，不给每列单独连信号。
        """
        key = DEVICE_COLUMNS[col][0]
        cfg = self._FILE_VIEW_FIELDS.get(key)
        if not cfg:
            return
        title, fields = cfg
        # xqzg 与 kd 同样按 file_path 日期分区，迁移路径拼接方式一致；
        # 此前误以为 xqzg 无日期分区而禁用，现已与 kd 对齐
        # 必须全部字段都可迁移才给按钮：总数视图混着可迁移与只读分类，
        # 给按钮会让用户以为整批文件都能迁走。
        can_migrate = (bool(fields)
                       and all(f in _MIGRATABLE_FIELDS for f in fields))

        def _show(data):
            if not data:
                return
            self._file_panel.show_files(
                data, title, fields, can_migrate=can_migrate)

        # 行数据到齐才开面板：面板构造依赖文件数组，先开再补会让它闪一次空清单
        self._request_full_row(row, _show)
        # 注意：点击精度/问题单元格只展示文件列表，不触发收集；
        # 收集统一由迁移按钮（精度/问题提交）成功后在 _on_migrate_ok 中触发

    def _info_raise(self) -> int:
        """贴底 InfoBar 需抬升的像素数：文件面板打开时避开其底部按钮/提示行

        面板关闭（如工具栏上传按钮触发的提示）返回 0，照常贴底不凭空悬空。
        按钮行隐藏（总数/正常等不可迁移视图）只需越过「双击或右键进行复制」
        提示行，抬升量减半。
        """
        fp = getattr(self, "_file_panel", None)
        if fp is not None and fp.isVisible():
            return 64 if fp._migrate_wrap.isVisible() else 40
        return 0

    def migrate_file(self, fname, src_cat, dest_cat):
        """迁移单个文件到目标分类（调用 migrate_image API）

        单文件粒度：接口一次只接受一个文件名，批量迁移由面板多次调用，
        所以这里必须自己拦并发。
        """
        # 单飞：并发提交同一设备的不同文件会让服务端分类计数互相覆盖，
        # 直接串行化，代价是连点时要等一下。
        if self._migrate_worker and self._migrate_worker.isRunning():
            show_info_bar("已有迁移任务进行中，请稍候", "warning",
                          title="提示", parent=self, duration=2000,
                          bottom_offset=self._info_raise())
            return
        # 日期与设备码都取自当前上下文：接口按「日期分区 + 设备码」定位文件，
        # 缺一个就会被服务端拒绝
        date = self._current_date()
        device_code = self._file_panel._row.get("device_code", "")
        if not date or not device_code:
            show_info_bar("缺少日期或设备编码，无法迁移", "warning",
                          title="提示", parent=self, duration=2500,
                          bottom_offset=self._info_raise())
            return
        # source 随请求下发：服务端要按数据源选表，传错会去 kd 表里找 xqzg 的文件
        self._migrate_worker = MigrateImageWorker(
            file_path=date, device_code=device_code, file_names=[fname],
            src_category=src_cat, dest_category=dest_cat,
            source=self._active_source())
        # 成功回调带上源分类与设备码：本地即时回显要按同一对字段/同一台设备
        # 做动作（迁移在途时用户可能已点开别的设备，见 _apply_local_move）
        self._migrate_worker.success.connect(
            lambda count, s=src_cat, d=dest_cat, dev=device_code:
            self._on_migrate_ok(fname, s, d, dev))
        self._migrate_worker.error.connect(self._on_migrate_fail)
        self._migrate_worker.start()
        show_info_bar(f"{fname} → 「{dest_cat}」...", "info",
                      title="迁移中", parent=self, duration=1500,
                      bottom_offset=self._info_raise())

    def _on_migrate_ok(self, fname, src_cat, dest_cat, device_code=""):
        """迁移成功：本地即时生效 + 提示，随后静默刷新以服务端口径对账

        静默刷新要翻页拉全接口再整分区落库（实测 5-10s），期间文件面板清单与
        表格计数都停在迁移前的值（用户报障 2026-10-04：提示迁移成功，原来 3 条
        还是 3 条，表格要 5-10s 后才变）。现在先在本地快照上完成同一动作立即
        回显，_silent_refresh 回来后覆盖为服务端口径（幂等）。
        """
        show_info_bar(f"{fname} 已移动到「{dest_cat}」", "success",
                      title="迁移成功", parent=self, duration=2500,
                      bottom_offset=self._info_raise())
        row = self._apply_local_move(fname, src_cat, dest_cat, device_code)
        # 回显之后再对账：静默刷新以服务端为准覆盖，两边一致时是幂等的
        self._silent_refresh()
        # 迁移到精度/问题后自动收集对应视频/日志到 upload 目录
        # （无需再点精度/问题单元格；本地快照已并入 fname，这里只做去重兜底）
        # 分类→收集字段由这张映射表驱动，与 CATEGORY_DIRS 的字段→服务器目录一脉相承
        field = {"精度": "accuracy_files", "问题": "already_files"}.get(dest_cat)
        if not field:
            return
        if not row:
            # 面板已切到别的设备且当前页查不到该设备：拿不到本设备清单，
            # 不猜着收集（否则会把别的设备的文件收进 upload）
            show_info_bar("文件面板已切到其他设备，本次未自动收集文件", "warning",
                          title="未收集", parent=self, duration=4000,
                          bottom_offset=self._info_raise())
            return
        files = list(row.get(field) or [])
        if fname not in files:
            files.append(fname)
        row[field] = files
        # C1 台账：精度/问题迁移成功写一条（collect_ok 待收集结果回填）
        self._log_submission(row, dest_cat, fname)
        self._auto_collect(row, field)

    def _apply_local_move(self, fname, src_cat, dest_cat, device_code=""):
        """迁移成功即时回显：文件面板清单 + 表格行计数就地更新并异步落库

        面板持有的是「点开时那台设备」的行快照：迁移在途时用户可能已点开别的
        设备，此时不动面板（否则会把这次迁移记到别人头上），改用当前页轻量行
        只同步计数（move_file_in_row 对无清单的行只调计数）。

        Returns:
            迁移设备本地的行快照（供精度/问题自动收集）；面板已切设备且当前页
            查不到该设备、或本次无实际变更时返回空 dict。
        """
        panel = getattr(self, "_file_panel", None)
        panel_row = getattr(panel, "_row", None) or {}
        # device_code 为空（老调用路径）时不设防，按面板当前行处理
        on_panel = panel is not None and (
            not device_code
            or str(panel_row.get("device_code") or "") == str(device_code))
        src_field = FIELD_CATEGORY.get(src_cat, "")
        dest_field = FIELD_CATEGORY.get(dest_cat, "")
        if on_panel:
            changes = panel.apply_local_move(fname, src_cat, dest_cat)
            row = panel_row
        else:
            changes = {}
            row = {}
        # 面板没命中也要更新表格行：用户可能正看着这台设备，
        # 计数不跟着变会让人以为迁移没生效
        row_idx = self._find_row_idx(device_code or panel_row.get("device_code"))
        if row_idx >= 0:
            item = self._current_rows[row_idx]
            if not on_panel:
                # 面板未持有该行：在列表页轻量行上补算同一动作（仅计数列生效）
                changes = table_db.move_file_in_row(
                    item, src_field, dest_field, fname)
                row = item
            elif changes:
                # 面板行与表格行是同一设备：把新计数同步进轻量行缓存，避免随后
                # 整表重绘（翻页/排序）又退回旧计数
                for key in (table_db.FILE_COUNT_FIELDS.get(src_field),
                            table_db.FILE_COUNT_FIELDS.get(dest_field)):
                    if key and key in changes:
                        item[key] = changes[key]
            self._update_row_cells(row_idx, changes)
        # 写回本地库是为了「下次点开就有新清单」，失败也不影响本次回显
        self._persist_local_move(device_code or panel_row.get("device_code"),
                                 src_field, dest_field, fname)
        return row if changes else {}

    def _find_row_idx(self, device_code: str) -> int:
        """按设备码在当前页行缓存里定位行号（迁移即时回显定位用，未命中 -1）

        只认 device_code：迁移成功回调带的就是设备编码。命中不了不算
        错误，调用方会退回在轻量行上补算。
        """
        code = str(device_code or "").strip()
        if not code:
            return -1
        # 线性扫当前页（最多 300 行）：比按 id 再查一次库便宜，也不用维护行索引
        for idx, item in enumerate(getattr(self, "_current_rows", None) or []):
            if str(item.get("device_code") or "").strip() == code:
                return idx
        return -1

    def _update_row_cells(self, row_idx: int, changes: dict):
        """就地刷新表格某行的统计单元格（迁移即时回显，不整表重绘）

        changes 里的文件清单字段不在 DEVICE_COLUMNS 内，循环里自然被跳过。

        只改文本与 tooltip：链接色由 _populate 统一设置，这里重设会让
        迁移后的计数列丢掉颜色。
        """
        if not changes or not (0 <= row_idx < self._table.rowCount()):
            return
        for col, (key, _, _) in enumerate(DEVICE_COLUMNS):
            if key not in changes:
                continue
            cell = self._table.item(row_idx, col)
            if cell is None:
                continue
            text = str(changes[key] if changes[key] is not None else "")
            cell.setText(text)
            cell.setToolTip(text or "(空)")

    def _persist_local_move(self, device_code, src_field, dest_field, fname):
        """异步把本次迁移写回本地快照（失败仅记日志，静默刷新兜底对账）

        写回后文件面板刷新（按 device_code + 日期分区查库）立即取到新清单，
        不必等接口全量往返；只在同一行做增量 UPDATE，不触碰其他设备。
        """
        if (not device_code or not src_field or not dest_field
                or src_field == dest_field):
            return
        # 表名与本次迁移用同一数据源：迁移已经打到对应源，写回必须同表
        table = "xqzg_status" if self._active_source() == "xqzg" else "kd_status"
        # 数据库写入必须在工作线程（GUI 线程同步写远程 MySQL 会冻结界面）
        worker = _DBQueryWorker(
            table_db.apply_file_move, table, device_code,
            self._current_date(), src_field, dest_field, fname)
        worker.error.connect(
            lambda msg: logger.warning("迁移本地快照写回失败: %s", msg))
        self._snapshot_worker = worker
        worker.start()

    def _log_submission(self, row: dict, category: str, fname: str):
        """台账写入点：精度/问题迁移成功记录一条提交（失败静默不阻断主流程）

        记的是「文件已提交」这个事实，收集与上传结果随后按 id 回填；
        没有这条记录，收集完成时就找不到对象可以回填。
        """
        # 台账失败不能阻断主流程：它只影响后续收集/上传结果的回填对账，
        # 迁移本身已经成功，弹错会让用户误以为迁移失败。
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
        """迁移失败：提示错误首行并静默刷新

        失败也刷新：接口可能部分成功（批量迁移时前几个已生效），
        以服务端口径重拉一次才能把界面对齐到真实状态。
        """
        show_info_bar(msg.split("\n")[0], "error",
                      title="迁移失败", parent=self, duration=4000,
                      bottom_offset=self._info_raise())
        self._silent_refresh()

    # ---------- 收集与上传 ----------
    # 收集把 videos_dir 里的媒体复制到 upload 待上传区，上传再把 upload 打成
    # 一个 zip 走 SFTP；upload 是两者唯一的交接点，失败可直接重试。

    def _upload_root(self) -> str:
        """返回上传收集目录 {videos_dir}/upload；未配置 videos_dir 时提示并返回空串

        返回空串而不是抛异常：调用方都是按钮槽，异常会冒到 Qt 事件循环
        里只留下控制台堆栈，用户看不到任何反馈。
        """
        videos_dir = (_load_settings().get("videos_dir") or "").strip()
        if not videos_dir:
            show_info_bar("未配置 videos_dir，请先在设置中配置视频/日志目录", "warning",
                          title="提示", parent=self, duration=3000,
                          bottom_offset=self._info_raise())
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
                          title="无法收集", parent=self, duration=3000,
                          bottom_offset=self._info_raise())
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
                              "warning", title="无法收集", parent=self, duration=5000,
                              bottom_offset=self._info_raise())
                return
            fuzzy_note = f"已手动映射 → {device_id}"
        # 按基础名收集并去重：同一文件可能同时挂在多个分类字段下，
        # 重复收集只会让 Worker 反复判断目标已存在
        bases = sorted({b for b in (clip_base_name(f) for f in (row.get(field) or [])) if b})
        # 文件列表为空说明没东西可收集，静默返回，避免弹个"收集 0 个"的无意义任务
        if not bases:
            return
        # Worker 存进列表持有引用：QThread 被 GC 会连同信号一起消失，
        # 这个列表就是收集任务的生命周期
        worker = CollectFilesWorker(videos_dir, device_id, bases)
        # 捕获当前台账 id：收集完成后回填 collect_ok（C1）
        sub_id = getattr(self, "_last_submission_id", None)
        worker.done.connect(
            lambda dev, n, miss, w=worker, sid=sub_id:
            self._on_collect_done(dev, n, miss, w, sid))
        worker.error.connect(
            lambda msg: show_info_bar(msg.split(chr(10))[0], "error",
                                      title="收集失败", parent=self, duration=4000,
                                      bottom_offset=self._info_raise()))
        self._collect_workers.append(worker)
        worker.start()
        show_info_bar(f"{device_id}{'（' + fuzzy_note + '）' if fuzzy_note else ''} · "
                      f"{len(bases)} 个视频/日志 → upload 目录", "info",
                      title="收集中", parent=self,
                      duration=1500 if not fuzzy_note else 3500,
                      bottom_offset=self._info_raise())

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
        # 传打分结果而不是原始目录列表：排序与截断留在页面侧，
        # 对话框只负责渲染候选
        dlg = DeviceDirHealDialog(self, videos_dir, cands, scored[:12])
        if not dlg.exec() or not dlg.chosen_dir:
            return ""
        # 用对话框回传的目录而不是自己取第一名：用户可能手动选了别的
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
        """收集完成：回填台账收集结果并按缺失情况提示

        「全部就位才算成功」：缺一个文件时服务端那份清单就是不完整的，
        标记成功会让后续上传对账以为这单已经齐了。
        """
        # 从在跑列表摘掉：列表只负责持有引用防 GC，收集结束由信号自然释放，
        # 留着会让列表随收集次数无限增长。
        if worker in self._collect_workers:
            self._collect_workers.remove(worker)
        # C1 台账：回填收集结果（全部就位才算成功，失败静默）
        if sub_id:
            try:
                table_db.update_submission_collect(sub_id, not missing)
            except Exception:
                pass
        # 缺失清单只列前 3 个：InfoBar 一行放不下整份清单，
        # 完整数量写在文案里，用户据此判断要不要重收
        if missing:
            shown = ", ".join(missing[:3]) + (" ..." if len(missing) > 3 else "")
            show_info_bar(f"{device_id}: 复制 {copied} 个（已存在跳过），缺失 {len(missing)} 个: {shown}",
                          "warning", title="收集完成", parent=self, duration=4000,
                          bottom_offset=self._info_raise())
        else:
            show_info_bar(f"{device_id}: 复制 {copied} 个文件到 upload 目录（已存在跳过）",
                          "success", title="收集完成", parent=self, duration=3000,
                          bottom_offset=self._info_raise())

    def _show_upload_list(self):
        """弹窗预览 upload 目录待上传文件清单（空目录提示先收集）

        先判空再开窗：列表对话框对空目录没有有意义的展示，
        直接提示「先收集」比给一个空窗口更清楚。
        """
        root = self._upload_root()
        if not root:
            return
        if not os.path.isdir(root) or not os.listdir(root):
            show_info_bar("暂无待上传文件，请先点击精度/问题收集文件", "info",
                          title="上传清单", parent=self, duration=3000,
                          bottom_offset=self._info_raise())
            return
        UploadListDialog(root, self).exec()

    def _on_package_upload(self):
        """打包 upload 目录为 zip 并 SFTP 上传，成功后清空本地目录

        流程：读上传专用配置 → 确认文件数 → 交给 ZipUploadWorker。
        清空放在 Worker 内部、上传成功之后，打包或上传失败时待传文件还在。

        凭据缺失直接拦截：没有内置默认密码，宁可让用户去设置页填，
        也不要拿空密码反复重试到被服务器拒绝。
        """
        root = self._upload_root()
        if not root:
            return
        if not os.path.isdir(root) or not os.listdir(root):
            show_info_bar("upload 目录为空，请先点击精度/问题收集文件", "info",
                          title="提示", parent=self, duration=3000,
                          bottom_offset=self._info_raise())
            return
        if self._upload_worker is not None and self._upload_worker.isRunning():
            show_info_bar("已有上传进行中，请稍候", "warning",
                          title="提示", parent=self, duration=2000,
                          bottom_offset=self._info_raise())
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
                          title="提示", parent=self, duration=3000,
                          bottom_offset=self._info_raise())
            return

        # 确认框给出确切文件数：用户容易以为只传当前设备，先让他看到总量
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

        # 置灰而不是丢弃点击：上传要几十秒，用户需要看到「正在进行」
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
        """打包上传成功：回填台账上传结果并提示（本地目录已清空）

        清空由 Worker 在上传成功后完成，这里只负责恢复按钮与反馈；
        台账回填失败不回滚，文件已经传走，报错只会让用户困惑。
        """
        self._btn_package.setEnabled(True)
        self._lbl_time.setText("")
        # C1 台账：回填上传结果（匹配近期已收集未上传记录，失败静默）
        try:
            table_db.update_submission_upload(str(info or ""), True)
        except Exception:
            pass
        show_info_bar(f"{info} · 本地 upload 目录已清空", "success",
                      title="上传成功", parent=self, duration=5000, bottom_offset=self._info_raise())

    def _on_upload_fail(self, msg):
        """打包上传失败：恢复按钮并提示错误首行

        本地 upload 目录保持原样，用户修好网络或凭据后可直接重试。
        """
        self._btn_package.setEnabled(True)
        self._lbl_time.setText("")
        show_info_bar(msg.split(chr(10))[0], "error",
                      title="上传失败", parent=self, duration=5000, bottom_offset=self._info_raise())

    def _silent_refresh(self):
        """迁移后静默重新拉取当前日期数据，刷新表格与文件面板

        在途刷新不丢弃而是记一个「补一次」标记（_refresh_pending）：整分区
        重拉有 5-10s 往返，拿到的是**发起时刻**的服务端快照；期间又迁移的
        条目不在其中，若直接丢弃这次刷新，落库结果会把本地已即时生效的
        迁移又落回原分类（表现为迁过去的条目自己跑回来）。
        在途判定用自己的标记属性，不用 isRunning（QThread 状态翻转与信号
        投递存在微秒级竞态，可能把已结束的刷新误判为在途而永久搁置）。
        """
        # 日期为空说明控件还没初始化，此时没有分区可拉，直接放弃
        date = self._current_date()
        if not date:
            return
        if self._refresh_worker is not None:
            self._refresh_pending = True
            return
        if self._active_source() == "xqzg":
            self._refresh_worker = SnookerOmFetchWorker(file_path=date)
        else:
            self._refresh_worker = DevicesFetchWorker(file_path=date)
        self._refresh_worker.result_ready.connect(self._on_refresh_done)
        self._refresh_worker.error.connect(self._on_refresh_error)
        self._refresh_worker.start()

    def _on_refresh_done(self, data):
        """迁移后静默刷新完成：异步保存数据

        这里走 save_* 整分区替换而不是 upsert：刷新拉的是当天全量，
        用增量更新会让服务端已删除的文件永远留在本地。
        """
        rows = data  # 拉取 Worker 翻页后 result_ready 直接发射 rows 列表
        date = self._current_date()
        if self._active_source() == "xqzg":
            self._save_worker = _DBQueryWorker(table_db.save_xqzg, rows, date)
        else:
            self._save_worker = _DBQueryWorker(table_db.save_kd, rows, date)
        self._save_worker.result_ready.connect(self._on_refresh_save_finished)
        # 落库失败要复位在途标记（走通用「保存失败」提示），否则后续刷新
        # 一直被误判为「已有刷新在途」而只记 pending 不执行
        self._save_worker.error.connect(self._on_refresh_save_error)
        self._save_worker.start()

    def _on_refresh_save_finished(self, _count):
        """刷新保存完成：重新加载并刷新文件面板；有排队则补一轮"""
        self._load_local()
        self._file_panel.refresh_if_visible()
        self._refresh_worker = None          # 本轮结束，标记复位（先复位再补轮）
        if self._refresh_pending:
            self._refresh_pending = False
            self._silent_refresh()

    def _on_refresh_error(self, msg):
        """静默刷新失败：复位在途标记，仅警告提示，不阻断当前操作

        标记必须复位：否则后续每次迁移都会走进「已有刷新在途」分支，
        只记 pending 永不执行，面板计数从此停在旧值。
        """
        self._refresh_worker = None
        self._refresh_pending = False
        show_info_bar(msg, "warning", title="刷新失败", parent=self, duration=3000, bottom_offset=self._info_raise())

    def _on_refresh_save_error(self, msg):
        """静默刷新落库失败：复位在途标记后走通用保存失败提示

        落库失败不影响本地即时回显，但服务端数据没进来，
        必须让用户知道这次对账没完成。
        """
        self._refresh_worker = None
        self._refresh_pending = False
        self._on_save_error(msg)

    # ---------- 每小时定时拉取 ----------
    # 只拉当天分区：设备在线状态变化快，历史日期不会变，重复拉是浪费。

    def _hourly_refresh(self):
        """每小时自动拉取当天 kd 设备状态（静默，不打扰用户）

        设备 status（空闲/下线/使用）时效性要求高，定时拉取当天分区保持新鲜。
        仅拉取并保存；若当前日期选择器正是当天，则顺带刷新表格显示。

        静默是刻意的：每小时弹一次提示会把用户正在做的事情打断，
        结果只反映在状态栏的自动更新时间上。
        """
        # 定时器两个数据源都会启动（见 _apply_source_date_state），真正拦截在
        # 这里：xqzg 只能全量拉取且数据变更不频繁，每小时拉一次代价过高。
        if self._active_source() != "kd":
            return
        if self._hourly_worker and self._hourly_worker.isRunning():
            return
        # 单飞：上一次还没回来就跳过本轮，避免慢接口把 Worker 越堆越多
        today = QDate.currentDate().toString("yyyy/MM/dd")
        self._hourly_worker = DevicesFetchWorker(file_path=today)
        self._hourly_worker.result_ready.connect(self._on_hourly_done)
        self._hourly_worker.error.connect(self._on_hourly_error)
        self._hourly_worker.start()

    def _on_hourly_done(self, data):
        """每小时定时拉取完成：异步保存数据

        不弹任何提示：这是后台保活，每小时弹一次会把用户正在做的
        事情打断；结果只反映在状态栏的时间上。
        """
        rows = data  # 拉取 Worker 翻页后 result_ready 直接发射 rows 列表
        today = QDate.currentDate().toString("yyyy/MM/dd")
        # 定时拉取不带关键词，拿的是当天全量快照，按日期整分区替换；
        # 若改成 upsert，当天已从服务端消失的设备会一直留在本地。
        self._save_worker = _DBQueryWorker(table_db.save_kd, rows, today)
        self._save_worker.result_ready.connect(
            lambda count: self._on_hourly_save_finished(count, today))
        self._save_worker.start()

    def _on_hourly_save_finished(self, count, today=""):
        """每小时保存完成：仅当用户正在查看当天时刷新表格

        别的日期刷新没有意义，还会打断用户正在翻的页；状态栏那行时间
        则无论看哪天都要更新，它用来证明定时链路还活着。
        """
        if self._current_date() == today:
            self._load_local()
        self._lbl_time.setText(f"自动更新: {datetime.now().strftime('%H:%M:%S')}（{count} 台）")

    def _on_hourly_error(self, msg):
        """定时拉取失败：不打断用户，仅状态栏静默提示

        定时任务失败是常态（网络抖动、接口超时），弹 InfoBar 会打断操作；
        状态栏留一行痕迹，用户想知道数据为何没更新时有处可查。
        """
        self._lbl_time.setText(f"自动更新失败: {msg.split(chr(10))[0]}")

    # ---------- CSV 导出（B6） ----------
    # 导出复用列表的查询函数，日期/搜索/排序条件必须与界面一致，
    # 否则用户会拿到一份和屏幕上不一样的数据。

    def _export_csv(self):
        """导出当前查询结果（含日期/搜索/排序条件）为 CSV（utf-8-sig）

        默认文件名带日期：导出物常被并排比对，重名会让用户分不清是哪天。
        """
        src = self._active_source()
        date = self._current_date()
        # xqzg 的导出不带日期条件（接口本身也不按日期筛），默认名刻意不带
        # 日期，避免用户以为导出的只是那一天的设备
        default_name = (f"设备状态_{date.replace('/', '-')}" if src == "kd"
                        else "设备状态")
        default = f"{default_name}_{datetime.now().strftime('%Y-%m-%d')}.csv"
        path, _sel = QFileDialog.getSaveFileName(
            self, "导出 CSV", default, "CSV 文件 (*.csv)")
        if not path:
            return
        # 导出同样单飞：两个导出同时跑会互相覆盖 Worker 引用，
        # 前一个的错误回调也没有对象可归
        if self._export_worker and self._export_worker.isRunning():
            show_info_bar("已有导出进行中，请稍候", "warning",
                          title="提示", parent=self, duration=2000, bottom_offset=self._info_raise())
            return
        keyword = self._search_edit.text().strip()
        # 复用异步查询机制：按当前条件一次拉取全部记录后写文件
        # 借分页接口一次拉全量（上限 _EXPORT_MAX_ROWS）再写文件：导出要的是
        # 完整结果集，这里只是复用它拼好的搜索与排序条件。
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
                                      title="导出失败", parent=self, duration=4000, bottom_offset=self._info_raise()))
        self._export_worker.start()

    def _on_export_query(self, result, path, src):
        """导出查询完成：按数据源拼表头写 CSV（utf-8-sig）

        设备编码单独加在最前：DEVICE_COLUMNS 是给界面看的列，
        导出物要能按编码回到系统里查。
        """
        _total, rows = result
        # 两数据源接口同套字段、落库均含 device_code：统一带设备编码列
        header = ["设备编码"] + [c[1] for c in DEVICE_COLUMNS]
        # 键序必须与表头严格对应，否则导出的列会整体错位
        keys = ["device_code"] + [c[0] for c in DEVICE_COLUMNS]
        try:
            # utf-8-sig 带 BOM：Excel 双击打开时才认得出中文，去掉 BOM 会变
            # 乱码，用户第一反应会当成导出坏了。
            # 写失败（目标被 Excel 占用等）只提示不抛：导出是只读动作，
            # 异常冒到事件循环里用户看不到原因
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(header)
                for item in rows:
                    writer.writerow([
                        str(item.get(k) if item.get(k) is not None else "")
                        for k in keys])
        except OSError as e:
            show_info_bar(str(e), "error",
                          title="导出失败", parent=self, duration=4000, bottom_offset=self._info_raise())
            return
        _show_export_bar(self, path, len(rows))

    # ---------- 历史日期自动补漏（C2） ----------
    # 补漏是一条串行状态机：任意一步失败都只记日志并继续下一天，
    # 缺口留到下次启动重试，避免单点故障让整条链停摆。

    # 两个值一起决定补漏对后端的压力：10 天 × 1.5s 约等于一分钟内 10 次翻页
    # 拉取，再密会和用户手动同步抢带宽，也容易触发接口限流。
    _BACKFILL_MAX = 10        # 单次启动最多补漏天数，其余下次再补
    _BACKFILL_INTERVAL = 1500  # 串行拉取间隔（毫秒），避免并发打爆 API

    def _backfill_missing_dates(self):
        """生成应有日期序列（最早日期→今天，无数据则近 60 天），对比已存
        分区找出缺失日期静默串行补拉（每次最多 10 天，不弹 UI 仅日志）

        kd 与 xqzg 都按 file_path 日期分区存储，补漏策略一致；
        依据当前数据源选择对应的 worker / 落库函数 / 日期查询函数。

        缺失日期按「最早有数据的一天 → 今天」逐日推进，只补真缺的日子；
        已有数据与拉过但接口为空的分区都算已覆盖，避免反复拉空日期。
        """
        # 防重入：本方法由启动定时器触发，上一次还没跑完就再次进入时，两条链
        # 会同时消费队列，同一天被拉两次还可能互相覆盖落库结果。
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
        # 库里一天数据都没有时按近 60 天补：更早的数据接口多半也已清理，
        # 一次铺太长只会让首次启动的补漏跑上几个小时。
        if start is None or not start.isValid():
            start = today.addDays(-59)
        missing = []
        d = start
        # 逐日推进而不是按周步进：分区键是 yyyy/MM/dd 字符串，逐日比较最直接，
        # 补漏量本来就以「天」为单位
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
        """串行消费补漏队列：逐个日期拉取，间隔短延时；用户操作优先

        串行是刻意的：补漏本身是后台补偿，并发只会和用户的手动同步抢
        带宽与接口配额，慢一点没有代价。
        """
        if not getattr(self, "_backfill_queue", []):
            if getattr(self, "_backfill_running", False):
                logger.info("历史补漏完成")
            self._backfill_running = False
            return
        # 用户手动拉取/静默刷新/定时拉取进行中 → 延后，避免并发冲突
        # 只看这三条链：补漏自己的 worker 不在检查范围，否则补漏会自我阻塞
        busy = any(getattr(self, a) is not None and getattr(self, a).isRunning()
                   for a in ("_worker", "_refresh_worker", "_hourly_worker"))
        if busy:
            # 只延后不跳过：3 秒后重试，否则用户长时间操作会让补漏链永久停摆
            QTimer.singleShot(3000, self._backfill_next)
            return
        # 落库函数启动时已快照（_backfill_save_func）：补漏途中用户切数据源时，
        # 链上仍按发起时的源落库，不会把 kd 数据写进 xqzg 表。
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
        # 补漏 Worker 单独持有：与手动拉取的 _worker 分开，
        # 两条链各自的错误回调才知道该往哪条链继续
        self._backfill_worker = worker
        worker.start()

    def _on_backfill_done(self, data, date):
        """补漏拉取完成：异步落库（复用 _DBQueryWorker，不阻塞 GUI）

        落库仍走 Worker：远程 MySQL 单次写入可能几十毫秒到秒级，
        在 GUI 线程做会把补漏期间的界面卡成幻灯片。
        """
        rows = data  # 拉取 Worker 翻页后 result_ready 直接发射 rows 列表
        # 单独持有补漏落库 Worker：与手动保存共用 _save_worker 会互相覆盖引用，
        # 前一次失败的回调就分不清是哪个链路的
        self._backfill_save_worker = _DBQueryWorker(
            self._backfill_save_func, rows, date)
        self._backfill_save_worker.result_ready.connect(
            lambda count, dt=date: self._on_backfill_saved(count, dt))
        self._backfill_save_worker.error.connect(
            lambda msg, dt=date: self._on_backfill_save_error(msg, dt))
        self._backfill_save_worker.start()

    def _on_backfill_saved(self, count, date):
        """补漏落库完成：正查看该日期则顺带刷新，间隔后继续下一天

        只有用户正盯着这个日期才刷表：后台补漏每天刷一次表格，会不停
        把用户的滚动位置和选中行重置掉。
        """
        logger.info("历史补漏 %s 完成：%d 台设备", date, count)
        # 只有用户正盯着这个日期才刷新表格，否则后台补漏会不停打断浏览
        if self._current_date() == date:
            self._load_local()  # 用户正查看该日期 → 顺带刷新
        QTimer.singleShot(self._BACKFILL_INTERVAL, self._backfill_next)

    def _on_backfill_save_error(self, msg, date):
        """补漏落库失败：记日志后继续下一天（不阻断补漏链）

        不重试同一天：接口或数据库的问题不会在 1.5 秒后自愈，
        立即重试只会让后面所有日期都堵在这一天。
        """
        # 单点失败不阻断链：这天的缺口还在库里，下次启动补漏会再轮到它
        logger.warning("历史补漏保存失败 %s: %s", date, str(msg).split(chr(10))[0])
        QTimer.singleShot(self._BACKFILL_INTERVAL, self._backfill_next)

    def _on_backfill_error(self, msg, date):
        """补漏拉取失败：记日志后继续下一天

        缺的日期没写进 sync_meta，下次启动仍会被算成缺失，天然带重试。
        """
        # 同理不因单天拉取失败停链：缺失日期没落库，下次补漏还会重试
        logger.warning("历史补漏拉取失败 %s: %s", date, str(msg).split(chr(10))[0])
        QTimer.singleShot(self._BACKFILL_INTERVAL, self._backfill_next)

    def resizeEvent(self, e):
        """窗口尺寸变化时同步滑出面板的高度与位置（面板贴右缘）

        面板是页面的子控件而不是独立窗口，不跟随就会在放大窗口后
        露出右边缘的空白，或者被压在内容下方。
        """
        super().resizeEvent(e)
        panel = getattr(self, "_file_panel", None)
        if panel and panel.isVisible():
            panel.setFixedSize(panel.width(), self.height())
            panel.move(self.width() - panel.width(), 0)
