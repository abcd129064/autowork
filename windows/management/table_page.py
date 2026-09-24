# -*- coding: utf-8 -*-
"""table_page 模块（从 windows/management_panel.py 拆出，逻辑未改动）"""

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
from core.frps_admin import get_frps_client
from core.perf import (is_acrylic_enabled, is_animation_enabled,
                       apply_table_smooth_mode)
from core.secrets import decrypt_settings, encrypt_settings
from core.utils import show_info_bar
from workers.table_worker import (TableFetchWorker, DevicesFetchWorker,
                                  SnookerOmFetchWorker, MigrateImageWorker,
                                  LoginTestWorker, get_active_api_source,
                                  TodeskToggleWorker, CATEGORY_DIRS)
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

from windows.management.common import *  # noqa: F401,F403
from windows.management.dialogs import (
    AddRecordDialog, EditSnkDialog, DeviceDirHealDialog,
    DeviceFilesDialog, UploadListDialog,
)

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
        # ToDesk 开关 Worker 池：按设备编码并发（轮询窗口最长 2 分钟，
        # 单实例引用会把其他设备的点击全部挡住）；同设备反向指令可打断旧轮询
        self._todesk_workers = {}
        self._todesk_ctx_map = {}  # device_code -> {row, old_status, turn_on}
        self._xqzg_sync_worker = None  # xqzg 实时行同步 Worker（同步数据追加）
        self._hidden_cols = {2, 8}  # 在线状态/设备编码 默认隐藏（ToDesk号/向日葵 插列后设备编码 7→8），「筛选」菜单可勾选显示
        self._show_test = False    # 是否显示「公司测试」数据（默认不显示）
        self._show_manual = False  # 是否显示手动版本设备（name 或 roomName 含 @s，默认不显示）
        self._show_tuidan = False  # 是否显示退单设备（接口 status=2，默认不显示）
        # 搜索防抖：停止输入 300ms 后才查库重建表格，避免逐字触发同步查询
        self._search_timer = QTimer(self)
        self._search_timer.setInterval(300)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._do_search)
        self._init_ui()
        self._load_local()
        # frps 在线感知（二期 P1）：球桌页进入即确保感知启动（幂等单例），
        # 名单刷新只重绘 frps 列，不触发整表重查
        self._frps_client = get_frps_client()
        self._frps_client.start()
        self._frps_client.proxies_changed.connect(
            lambda _d: self.update_frps_column())
        # 异步获取元数据，判断是否需要首次同步
        self._meta_worker = _DBQueryWorker(table_db.get_meta)
        self._meta_worker.result_ready.connect(self._on_meta_finished)
        self._meta_worker.start()

    def _apply_smooth_mode(self):
        """按当前生效的平滑滚动设置刷新本页表格（管理面板设置页联动）"""
        apply_table_smooth_mode(self._table, panel="management")

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
        # 性能（2026-08-26）：管理面板表格接入平滑滚动开关（覆盖→全局）
        apply_table_smooth_mode(self._table, panel="management")
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
        """筛选列下拉菜单：逐列勾选显隐 + 公司测试/手动版本数据开关"""
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
        self._test_cb.setToolTip("显示内部测试球房（公司测试/办公室测试/外借测试）")
        self._test_cb.checkStateChanged.connect(self._toggle_test_data)
        menu.addWidget(self._test_cb, selectable=False)
        # 「手动版本」设备开关：默认不勾选（不显示 name/roomName 含 @s 的设备），勾选后才展示
        self._manual_cb = CheckBox("手动版本", self)
        self._manual_cb.setChecked(self._show_manual)
        self._manual_cb.setFixedSize(max(self._manual_cb.sizeHint().width() + 30, 120), 36)
        self._manual_cb.setToolTip("显示手动版本设备（名称或球房名含 @s）")
        self._manual_cb.checkStateChanged.connect(self._toggle_manual_data)
        menu.addWidget(self._manual_cb, selectable=False)
        # 「退单设备」开关：默认不勾选（不显示接口 status=2 的设备），勾选后才展示
        self._tuidan_cb = CheckBox("退单设备", self)
        self._tuidan_cb.setChecked(self._show_tuidan)
        self._tuidan_cb.setFixedSize(max(self._tuidan_cb.sizeHint().width() + 30, 120), 36)
        self._tuidan_cb.setToolTip("显示退单设备（接口 status=2）")
        self._tuidan_cb.checkStateChanged.connect(self._toggle_tuidan_data)
        menu.addWidget(self._tuidan_cb, selectable=False)
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
            _query_tables_page_with_stats,
            self._page_no, self._page_size, keyword, _HF_DAYS,
            self._show_test, self._show_manual, self._show_tuidan)
        self._query_worker.result_ready.connect(
            lambda result, kw=keyword: self._on_query_finished(result, kw))
        self._query_worker.error.connect(
            lambda msg: show_info_bar(str(msg).split(chr(10))[0], "error",
                                      title="查询失败", parent=self, duration=4000))
        self._query_worker.start()

    def _on_query_finished(self, result, keyword=""):
        """查询完成回调：更新表格与分页（高频统计已随分页同批返回）"""
        total, rows, hf_stats = result
        self._total = total
        self._populate(rows, hf_stats)
        self._update_pager(keyword)
        # 异步获取同步时间
        self._time_worker = _DBQueryWorker(table_db.get_meta)
        self._time_worker.result_ready.connect(self._on_time_meta)
        self._time_worker.start()

    def _on_time_meta(self, result):
        """同步时间元数据到手：状态栏展示数据时间"""
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
        self._save_worker.result_ready.connect(self._on_save_finished)
        self._save_worker.start()

    def _on_save_finished(self, count):
        """保存完成：重置页码并重新加载；追加同步 xqzg 实时行（todesk 源）"""
        self._page_no = 1
        self._load_local()
        self._refresh_btn.setEnabled(True)
        self._lbl_info.setText(f"同步完成，共 {count} 条")
        self._sync_xqzg_live()

    def _sync_xqzg_live(self):
        """追加拉取 xqzg 实时行落当日分区（ToDesk号/状态着色的数据源）

        wechat listext 不含 todesk 字段；不追加这步，ToDesk 列会一直显示
        旧快照状态（设备已关闭仍绿色）。实时行单页 993 条（pagesize 1000
        自动翻页兜底），file_path 传空即实时聚合行。失败静默：账号未配置/
        网络异常只记日志，不影响主同步流程。
        """
        if self._xqzg_sync_worker and self._xqzg_sync_worker.isRunning():
            return
        self._xqzg_sync_worker = SnookerOmFetchWorker(file_path="")
        self._xqzg_sync_worker.result_ready.connect(self._on_xqzg_live_done)
        self._xqzg_sync_worker.error.connect(
            lambda msg: logger.warning("xqzg 实时行同步失败（不影响球桌数据）: %s", msg))
        self._xqzg_sync_worker.start()

    def _on_xqzg_live_done(self, rows):
        """xqzg 实时行拉取完成：落今日分区后重查（着色即最新上报状态）

        落库不能在本回调（GUI 线程）里同步做：批量写远程 MySQL 撞上网络
        闪断会挂到超时才返回，整个界面冻结十秒上下。改走 Worker，
        完成后在回调里重查。
        """
        if not rows:
            return
        today = datetime.now().strftime("%Y/%m/%d")
        self._save_worker = _DBQueryWorker(table_db.save_xqzg, rows, today)
        self._save_worker.result_ready.connect(
            lambda _count: self._load_local())
        self._save_worker.error.connect(
            lambda msg: logger.warning("xqzg 实时行落库失败: %s", msg))
        self._save_worker.start()

    def _on_sync_error(self, msg):
        """API 同步失败：恢复刷新按钮并展示错误"""
        self._refresh_btn.setEnabled(True)
        self._lbl_info.setText(f"同步失败: {msg}")

    def _populate(self, rows, hf_stats=None):
        """行数据 → 表格：高频问题设备状态列标红，填充期关更新防闪烁

        高频统计必须由 Worker 随分页查询一起传入（界面零同步查询）：
        旧版本页在 GUI 线程里同步查 get_submission_stats，MySQL 闪断后
        旧连接半开，每次搜索/翻页都会白挂到 TCP 读超时（~10s）才返回。
        传入为空时降级为不标记（防御，正常链路不会发生）。
        """
        hf_map = (hf_stats or {}).get("by_table") or {}
        # 填充期间关闭界面更新与信号，完成后一次性恢复
        self._table.setUpdatesEnabled(False)
        self._table.blockSignals(True)
        try:
            self._table.setRowCount(len(rows))
            for r, item in enumerate(rows):
                # 球桌号（name）即提交台账的 table_id，用它匹配高频统计
                hf = hf_map.get(str(item.get("name") or "").strip(), 0)
                for c, (key, _, _) in enumerate(TABLE_COLUMNS):
                    if key == "frps_online":
                        # frps 在线感知列（二期 P1）：实时查 frps_admin 缓存，
                        # 不落库；感知未启用/不可达显示「—」，绝不误导为离线
                        cell = self._make_frps_cell(item)
                        self._table.setItem(r, c, cell)
                        continue
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
                    elif key == "todesk_id":
                        # UserRole 存 todesk_status、UserRole+1 存设备编码：
                        # 右键开关动作据此生成指令参数（无需跨列回查行数据）
                        todesk_status = str(item.get("todesk_status") or "")
                        cell.setData(Qt.ItemDataRole.UserRole, todesk_status)
                        cell.setData(Qt.ItemDataRole.UserRole + 1,
                                     str(item.get("code") or "").strip())
                        if val:
                            # ToDesk 号着色：接口上报开启→绿色；关闭/未上报→默认色
                            if todesk_status == "1":
                                cell.setForeground(QColor(SEMANTIC["success"]))
                                cell.setToolTip(f"{tip}\nToDesk 状态：开启")
                            else:
                                state_txt = ("关闭" if todesk_status == "0"
                                             else "未上报")
                                cell.setToolTip(f"{tip}\nToDesk 状态：{state_txt}（右键可开关）")
                    self._table.setItem(r, c, cell)
        finally:
            self._table.blockSignals(False)
            self._table.setUpdatesEnabled(True)
        _fit_table_rows(self._table)

    # ---------- frps 在线感知列（二期 P1，实时不落库） ----------

    _FRPS_CELL = {
        "online": ("● online", "success"),
        "offline": ("● offline", "danger"),
        "unregistered": ("○ 未注册", "warning"),
        None: ("—", "info"),
    }

    def _make_frps_cell(self, item):
        """按 snk_code 生成 frps 在线单元格（四态；感知不可用→灰「—」）"""
        snk = str(item.get("snk_code") or "").strip()
        state = get_frps_client().online(snk) if snk else None
        text, color_key = self._FRPS_CELL.get(state, self._FRPS_CELL[None])
        cell = QTableWidgetItem(text if snk else "—")
        cell.setToolTip(("感知未启用/不可达" if state is None and snk
                         else ("该球桌无 SNK 标识" if not snk else
                               f"frps xtcp proxy 状态：{state}")))
        if snk and state is not None:
            cell.setForeground(QColor(SEMANTIC[color_key]))
        return cell

    def _refresh_frps_column(self):
        """frps 名单刷新后仅重绘该列（不重查库，保住当前页/滚动位置）

        N4（2026-09-25）：snk/frps 列索引循环外枚举一次（原每行重复
        O(rows×cols)）；已有 item 只 setText/setForeground，仅空格才新建
        —— 对已有 item 重复 setItem 会触发 Qt「cannot insert an item that
        is already owned」警告，每次刷新数百条掩盖真实日志。
        """
        col = next((i for i, (k, _, _) in enumerate(TABLE_COLUMNS)
                    if k == "frps_online"), -1)
        if col < 0:
            return
        snk_col = next((i for i, (k, _, _) in enumerate(TABLE_COLUMNS)
                        if k == "snk_code"), col)
        info_fg = QColor(SEMANTIC["info"])
        tbl = self._table
        for r in range(tbl.rowCount()):
            snk_item = tbl.item(r, snk_col)
            snk = snk_item.text().strip() if snk_item else ""
            state = get_frps_client().online(snk) if snk else None
            text, color_key = self._FRPS_CELL.get(state, self._FRPS_CELL[None])
            cell = tbl.item(r, col)
            if cell is None:
                cell = QTableWidgetItem(text if snk else "—")
                tbl.setItem(r, col, cell)
            else:
                cell.setText(text if snk else "—")
            if snk and state is not None:
                cell.setForeground(QColor(SEMANTIC[color_key]))
            else:
                cell.setForeground(info_fg)

    def update_frps_column(self):
        """供 frps proxies_changed 信号驱动的实时刷新入口"""
        if self.isVisible():
            self._refresh_frps_column()

    def _update_pager(self, keyword=""):
        """按总数/页大小重算页码并同步分页控件与状态文本"""
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

    def _toggle_tuidan_data(self, state):
        """「退单设备」显隐切换：回到第一页重新查询（接口 status=2）"""
        self._show_tuidan = (state == Qt.CheckState.Checked)
        self._page_no = 1
        self._load_local()

    def _show_copy_menu(self, pos):
        """右键菜单：复制单元格；SNK 列额外提供修改入口与远程连接入口"""
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
        # 右键 ToDesk 号列：开关指令入口（实时下发 + 轮询确认）
        if idx.isValid() and TABLE_COLUMNS[idx.column()][0] == "todesk_id":
            td_item = self._table.item(idx.row(), idx.column())
            if td_item and td_item.text().strip():
                turn_on = str(td_item.data(Qt.ItemDataRole.UserRole) or "") != "1"
                act_td = Action(
                    FluentIcon.PLAY if turn_on else FluentIcon.PAUSE,
                    "开启 ToDesk" if turn_on else "关闭 ToDesk", self._table)
                if not str(td_item.data(Qt.ItemDataRole.UserRole + 1) or "").strip():
                    # 无设备编码无法下发（datacode 是 value/ 指令的必填定位参数）
                    act_td.setEnabled(False)
                    act_td.setToolTip("该球桌无设备编码，无法下发指令")
                else:
                    act_td.triggered.connect(
                        lambda _=False, r=idx.row(): self._toggle_todesk(r))
                menu.addAction(act_td)
        # 远程连接入口（SSH / SFTP），与设备状态页交互一致
        if idx.isValid():
            self._add_remote_actions(menu, idx.row())
        # 售后记录入口：按桌号反查售后面板（单例窗口缓存于管理面板实例）
        if idx.isValid():
            row = idx.row()
            name_item = self._table.item(row, 0)
            table_name = name_item.text().strip() if name_item else ""
            if table_name:
                act_as = Action(FluentIcon.PEOPLE, "查看售后记录", self._table)
                act_as.triggered.connect(
                    lambda _=False, n=table_name: self._open_aftersale_for_table(n))
                menu.addAction(act_as)
        menu.exec_(self._table.viewport().mapToGlobal(pos), aniType=_popup_ani_type())

    def _open_aftersale_for_table(self, table_name):
        """打开售后记录并按桌号预筛选（球桌 → 售后记录反查）

        优先走主窗口内嵌路由（售后 Hub 记录页，单窗口导航）；
        宿主无内嵌路由时兜底打开独立售后面板（shim 独立进程场景）。
        """
        win = self.window()
        router = getattr(win, "open_aftersale_records_for", None)
        if router is not None:
            router(table_name)
            return
        from windows.aftersale_panel import AftersalePanelWindow
        panel = getattr(win, "_aftersale_panel_ref", None)
        if panel is None:
            panel = AftersalePanelWindow()
            panel.destroyed.connect(
                lambda: setattr(win, "_aftersale_panel_ref", None))
            win._aftersale_panel_ref = panel
        panel.open_records_for_table(table_name)
        panel.show()
        panel.raise_()

    def _add_remote_actions(self, menu, row_idx):
        """右键菜单追加远程连接入口（SSH 终端 / SFTP 文件管理）

        snk 优先取行数据 snk_code 列（存储时已从 remark 解析/手动写入），
        兜底再从 remark 正则解析；两者皆无则该球桌不可远程。
        """
        # 列索引按 TABLE_COLUMNS 动态定位（相机密码/SNK 之间插 ToDesk号 列后，
        # 硬编码索引会随插列漂移）；球桌号恒为第 0 列
        col_of = {k: i for i, (k, _, _) in enumerate(TABLE_COLUMNS)}
        snk_item = self._table.item(row_idx, col_of.get("snk_code", 6))
        remark_item = self._table.item(row_idx, col_of.get("remark", 3))
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
            include_test=self._show_test, include_manual=self._show_manual,
            include_tuidan=self._show_tuidan)
        self._export_worker.result_ready.connect(
            lambda result, p=path: self._on_export_query(result, p))
        self._export_worker.error.connect(
            lambda msg: show_info_bar(str(msg).split(chr(10))[0], "error",
                                      title="导出失败", parent=self, duration=4000))
        self._export_worker.start()

    def _on_export_query(self, result, path):
        """导出查询完成：全量记录写 CSV（utf-8-sig），成功后提示并可定位文件"""
        _total, rows = result
        header = []
        keys = []
        for k, t, _w in TABLE_COLUMNS:
            if k == "frps_online":
                continue  # 派生列（frps 实时感知）无 DB 字段，不参与导出
            header.append(t)
            keys.append(k)
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

    # ---------- ToDesk 开关（右键指令下发 + 轮询确认） ----------

    def _todesk_col(self) -> int:
        """ToDesk 号列索引（按 TABLE_COLUMNS 动态定位，防插列漂移）"""
        for i, (key, _, _) in enumerate(TABLE_COLUMNS):
            if key == "todesk_id":
                return i
        return -1

    def _set_todesk_cell(self, row, status="", busy=False, note=""):
        """原地刷新 ToDesk 号单元格着色与 tooltip（不重查库，保证实时反馈）

        busy=True 表示指令已受理待设备确认（置灰）；status='1' 绿色开启，
        其余恢复默认色并标注 关闭/未上报；note 附加说明（如「已下发」）。
        """
        col = self._todesk_col()
        if col < 0:
            return
        item = self._table.item(row, col)
        if item is None:
            return
        tip = item.text().strip()
        suffix = f"（{note}）" if note else ""
        if busy:
            item.setForeground(QColor(SEMANTIC["neutral"]))
            item.setToolTip(f"{tip}\nToDesk 指令下发中，等待设备确认…")
            return
        item.setData(Qt.ItemDataRole.UserRole, str(status or ""))
        if str(status or "") == "1":
            item.setForeground(QColor(SEMANTIC["success"]))
            item.setToolTip(f"{tip}\nToDesk 状态：开启{suffix}")
        else:
            item.setForeground(
                self._table.palette().color(QPalette.ColorRole.Text))
            state_txt = "关闭" if str(status or "") == "0" else "未上报"
            item.setToolTip(f"{tip}\nToDesk 状态：{state_txt}{suffix}")

    def _toggle_todesk(self, row):
        """右键开关 ToDesk：确认 → 下发指令 → 乐观置灰 → 轮询确认后刷新着色

        Worker 按设备编码并发：不同设备可同时操作；同设备反向指令打断
        旧轮询立即重发（乐观显示本就以目标态为准，无需等旧轮询退出）。
        """
        col = self._todesk_col()
        if col < 0:
            return
        item = self._table.item(row, col)
        if item is None or not item.text().strip():
            return
        device_code = str(item.data(Qt.ItemDataRole.UserRole + 1) or "").strip()
        if not device_code:
            show_info_bar("该球桌无设备编码，无法下发指令", "warning",
                          title="无法操作", parent=self, duration=2500)
            return
        name_item = self._table.item(row, 0)
        table_name = name_item.text().strip() if name_item else ""
        old_status = str(item.data(Qt.ItemDataRole.UserRole) or "")
        turn_on = old_status != "1"
        old_worker = self._todesk_workers.get(device_code)
        if old_worker and old_worker.isRunning():
            if old_worker.turn_on == turn_on:
                # 同方向重复点击：指令已受理，拒绝重复下发
                show_info_bar(f"球桌「{table_name}」的开关指令执行中，请稍候",
                              "warning", title="提示", parent=self, duration=2500)
                return
            # 反向指令：断开旧 worker 回调（防 late 信号干扰）并打断轮询
            try:
                old_worker.disconnect(self)
            except (TypeError, RuntimeError):
                pass
            old_worker.requestInterruption()
        verb = "开启" if turn_on else "关闭"
        box = MessageBox(
            f"确认{verb} ToDesk",
            f"将对球桌「{table_name}」（设备 {device_code}）下发{verb}指令，"
            f"设备确认通常需要数秒。", self)
        box.yesButton.setText(verb)
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        # 上下文按设备编码存取：信号回调经 sender() 归属到具体 worker
        self._todesk_ctx_map[device_code] = {
            "row": row, "old_status": old_status, "turn_on": turn_on,
            "name": table_name}
        self._set_todesk_cell(row, busy=True)
        worker = TodeskToggleWorker(device_code, table_name, turn_on)
        # ⚠️ 不能用 self.sender() 归属回调：PySide6 中 Python 侧 emit 时
        # sender() 恒为 None，连接时闭包捕获 worker 才可靠
        worker.sent_ok.connect(lambda w=worker: self._on_todesk_sent(w))
        worker.confirmed.connect(lambda s, w=worker: self._on_todesk_confirmed(s, w))
        worker.unconfirmed.connect(
            lambda s, w=worker: self._on_todesk_unconfirmed(s, w))
        worker.error.connect(lambda m, w=worker: self._on_todesk_error(m, w))
        # 线程结束后清理池引用（按身份比较，防止误删同设备的后继 worker）
        worker.finished.connect(
            lambda w=worker, d=device_code:
            self._todesk_workers.pop(d, None) if self._todesk_workers.get(d) is w else None)
        self._todesk_workers[device_code] = worker
        worker.start()

    def _todesk_ctx_for(self, worker) -> dict:
        """按信号发送者（worker）取对应设备上下文；无归属直接忽略"""
        dev = str(getattr(worker, "device_code", "") or "").strip()
        return self._todesk_ctx_map.get(dev) or {} if dev else {}

    def _on_todesk_sent(self, worker=None):
        """指令已被服务端受理（errorcode=0，WebSocket 已推送）

        实测指令秒级生效，但设备状态上报有延迟——立即乐观显示目标状态
        （tooltip 标注待上报），后续 confirmed 再补「已确认」。
        """
        ctx = self._todesk_ctx_for(worker)
        if not ctx:
            return
        target = "1" if ctx.get("turn_on") else "0"
        self._set_todesk_cell(ctx["row"], status=target,
                              note="已下发，待设备上报")
        verb = "开启" if ctx.get("turn_on") else "关闭"
        show_info_bar(f"指令已下发（{verb}），设备状态上报可能有延迟",
                      "success", title="ToDesk", parent=self, duration=3000)

    def _on_todesk_confirmed(self, status, worker=None):
        """设备已上报新状态：回写本地最新分区并原地刷新着色"""
        ctx = self._todesk_ctx_for(worker)
        if not ctx:
            return
        try:
            dev = str(getattr(worker, "device_code", "") or "")
            table_db.update_todesk_status(dev, status)
        except Exception:
            logger.exception("回写 todesk_status 失败（不影响界面刷新）")
        self._set_todesk_cell(ctx["row"], status=str(status or ""))
        verb = "开启" if str(status) == "1" else "关闭"
        show_info_bar(f"球桌「{ctx.get('name', '')}」ToDesk 已确认{verb}",
                      "success", title="已确认", parent=self, duration=3000)

    def _on_todesk_unconfirmed(self, last_status, worker=None):
        """轮询期内设备未上报新状态（2026-09-20 真机实测：上报有延迟且
        可能超过轮询窗口）。指令已受理且大概率生效，**不回滚**界面状态，
        仅提示。"""
        ctx = self._todesk_ctx_for(worker)
        if not ctx:
            return
        show_info_bar(
            "指令已生效，但设备状态上报有延迟，稍后「同步数据」后以最新上报为准",
            "warning", title="待上报", parent=self, duration=5000)

    def _on_todesk_error(self, msg, worker=None):
        """下发失败：恢复原状态显示并提示错误"""
        ctx = self._todesk_ctx_for(worker)
        if ctx:
            self._set_todesk_cell(ctx["row"], status=ctx.get("old_status") or "")
        show_info_bar(str(msg).split("\n")[0], "error",
                      title="ToDesk 指令失败", parent=self, duration=4000)
