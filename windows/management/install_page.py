# -*- coding: utf-8 -*-
"""install_page 模块：安装清单页（球桌 createTime 视角的列表/筛选/导出）

数据源：wechat listext 同步链路落库的 billiard_tables.createTime（安装
时间）/ roomAddress（球房地址）/ sales（销售）。2026-10-11 实测：接口
2026-09 当月 createTime 台子数 143，与人工样例《球房安装清单202609.xlsx》
143 行逐台日期吻合——安装时间即接口 createTime，本页零独立存储。

- 筛选查询：table_db.query_install_page（球房分组聚合排序，同球房相邻）
- 导出：table_db.export_install_xlsx（复刻人工样例格式：同球房 A/B/C
  纵向合并、列宽、D 列 '@' 文本格式、E 列 mm-dd-yy 日期）
- 同步：复用 TableFetchWorker + table_db.save_all（球桌管理同款链路）
"""

import math
import logging
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QHeaderView, QAbstractItemView, QTableWidgetItem, QFileDialog)
from qfluentwidgets import (TableWidget, SearchLineEdit, PushButton, ToolButton,
    FluentIcon, ComboBox, RoundMenu, CheckBox,
    TransparentDropDownPushButton)

from core.perf import apply_table_smooth_mode
from core.utils import show_info_bar
from workers.table_worker import TableFetchWorker, SnookerOmFetchWorker
from database import table_db

from windows.management.common import *  # noqa: F401,F403

logger = logging.getLogger(__name__)

# 页面列定义：(行数据键, 标题, 列宽)。人工样例 7 列中的「销售-归属/
# 销售-催款」同源 sales（接口仅一个字段，样例两列恒等），页面合并为一列
# 「销售」展示，导出时由 export_install_xlsx 拆回两列；「销售转移」为
# xqzg status/ 接口增量字段（sales_transfer，如 "谢正钱 → 贾高阳"）。
INSTALL_COLUMNS = [
    ("roomName", "球房名字", 200),
    ("roomAddress", "球房地址", 380),
    ("room_count", "本月安装数量", 100),
    ("name", "球桌编号", 90),
    ("createTime", "安装时间", 110),
    ("sales", "销售", 130),
    ("sales_transfer", "销售转移", 150),
]

_MONTH_ALL = "全部月份"
_SALES_ALL = "全部销售"


def _fetch_install_options() -> tuple:
    """月份/销售候选合并取数（Worker 线程执行；两条 DISTINCT 轻查询）"""
    return table_db.install_month_options(), table_db.install_sales_options()


class InstallPage(QWidget):
    """安装清单页：每个台子/球桌的安装时间列表（筛选 + 分页 + 导出）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page_no = 1
        self._page_size = 50
        self._total = 0
        self._worker = None           # API 同步拉取（TableFetchWorker）
        self._save_worker = None      # 同步结果落库
        self._query_worker = None     # 分页查询
        self._export_worker = None    # 导出
        self._meta_worker = None      # 数据时间元信息
        self._options_worker = None   # 月份/销售候选
        self._xqzg_sync_worker = None  # xqzg 实时行（sales_transfer 回填源）
        # 过滤口径与球桌管理对齐；差异：手动版本/退单设备默认包含
        # （安装台账记录安装事实，人工样例含 @s 设备），公司测试默认排除
        self._show_test = False
        self._show_manual = True
        self._show_tuidan = True
        # 搜索防抖：停止输入 300ms 后才查库（同球桌管理）
        self._search_timer = QTimer(self)
        self._search_timer.setInterval(300)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._do_search)
        self._init_ui()
        self._load_local()
        self._refresh_options_async()

    def _apply_smooth_mode(self):
        """按当前生效的平滑滚动设置刷新本页表格（管理面板设置页联动）"""
        apply_table_smooth_mode(self._table, panel="management")

    # ---------- UI ----------

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 12)
        root.setSpacing(10)

        # --- 工具栏 ---
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self._search_edit = SearchLineEdit(self)
        self._search_edit.setPlaceholderText(
            "搜索")
        self._search_edit.setFixedWidth(300)
        self._search_edit.textChanged.connect(self._on_search_input)
        toolbar.addWidget(self._search_edit)

        self._month_combo = ComboBox(self)
        self._month_combo.addItem(_MONTH_ALL)
        self._month_combo.currentTextChanged.connect(self._on_month_changed)
        self._month_combo.setToolTip("按安装月份筛选")
        toolbar.addWidget(self._month_combo)

        self._sales_combo = ComboBox(self)
        self._sales_combo.addItem(_SALES_ALL)
        self._sales_combo.currentTextChanged.connect(self._on_sales_changed)
        self._sales_combo.setToolTip("按销售筛选")
        toolbar.addWidget(self._sales_combo)
        toolbar.addStretch(1)

        self._btn_export = PushButton(FluentIcon.DOWNLOAD, "导出 Excel", self)
        self._btn_export.setToolTip("按当前筛选导出 xlsx")
        self._btn_export.clicked.connect(self._export_xlsx)
        toolbar.addWidget(self._btn_export)

        self._filter_btn = TransparentDropDownPushButton("筛选", self)
        self._filter_btn.setIcon(FluentIcon.FILTER.qicon())
        self._build_filter_menu()
        toolbar.addWidget(self._filter_btn)

        self._refresh_btn = PushButton(FluentIcon.SYNC, "同步数据", self)
        self._refresh_btn.setToolTip(
            "从服务器拉取全量球桌数据")
        self._refresh_btn.clicked.connect(self._sync_from_api)
        toolbar.addWidget(self._refresh_btn)
        root.addLayout(toolbar)

        # --- 表格 ---
        self._table = TableWidget(self)
        apply_table_smooth_mode(self._table, panel="management")
        self._table.setColumnCount(len(INSTALL_COLUMNS))
        self._table.setHorizontalHeaderLabels([c[1] for c in INSTALL_COLUMNS])
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectItems)
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self._table.setItemDelegate(_ReadOnlySelectDelegate(self._table))
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_FIXED_ROW_HEIGHT)
        self._table.setAlternatingRowColors(True)
        # 关闭自动换行：长地址省略号截断 + tooltip 展示全文
        self._table.setWordWrap(False)
        QShortcut(QKeySequence.StandardKey.Copy, self._table).activated.connect(
            lambda: _copy_table_selection(self._table))

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for i, (_, _, w) in enumerate(INSTALL_COLUMNS):
            self._table.setColumnWidth(i, w)
        root.addWidget(self._table, 1)

        # --- 分页栏 ---
        pager = QHBoxLayout()
        pager.setSpacing(6)
        self._lbl_info = QLabel("正在加载中，请稍等", self)
        pager.addWidget(self._lbl_info)
        pager.addStretch(1)
        pager.addWidget(QLabel("每页:", self))
        self._size_combo = ComboBox(self)
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

    def _build_filter_menu(self):
        """筛选下拉菜单：公司测试/手动版本/退单设备三类数据显隐开关"""
        menu = RoundMenu("筛选", self)
        self._test_cb = CheckBox("公司测试", self)
        self._test_cb.setChecked(self._show_test)
        self._test_cb.setFixedSize(
            max(self._test_cb.sizeHint().width() + 30, 120), 36)
        self._test_cb.setToolTip("显示内部测试球房")
        self._test_cb.checkStateChanged.connect(self._toggle_test_data)
        menu.addWidget(self._test_cb, selectable=False)
        self._manual_cb = CheckBox("手动版本", self)
        self._manual_cb.setChecked(self._show_manual)
        self._manual_cb.setFixedSize(
            max(self._manual_cb.sizeHint().width() + 30, 120), 36)
        self._manual_cb.setToolTip("显示手动版本设备；"
                                   "安装台账默认包含")
        self._manual_cb.checkStateChanged.connect(self._toggle_manual_data)
        menu.addWidget(self._manual_cb, selectable=False)
        self._tuidan_cb = CheckBox("退单设备", self)
        self._tuidan_cb.setChecked(self._show_tuidan)
        self._tuidan_cb.setFixedSize(
            max(self._tuidan_cb.sizeHint().width() + 30, 120), 36)
        self._tuidan_cb.setToolTip("显示退单设备 接口 status=2；"
                                   "安装台账默认包含")
        self._tuidan_cb.checkStateChanged.connect(self._toggle_tuidan_data)
        menu.addWidget(self._tuidan_cb, selectable=False)
        _patch_menu_animation(menu)
        self._filter_btn.setMenu(menu)

    # ---------- 数据加载 ----------

    def _current_ym(self) -> str:
        text = self._month_combo.currentText()
        return "" if text == _MONTH_ALL else text

    def _current_sales(self) -> str:
        text = self._sales_combo.currentText()
        return "" if text == _SALES_ALL else text

    def _load_local(self):
        """异步分页查询本地数据库，快速切换时取消前一个 Worker"""
        if self._query_worker and self._query_worker.isRunning():
            self._query_worker.requestInterruption()
            self._query_worker.disconnect(self)
        self._query_worker = _DBQueryWorker(
            table_db.query_install_page,
            self._page_no, self._page_size,
            self._search_edit.text().strip(),
            self._current_ym(), self._current_sales(),
            self._show_test, self._show_manual, self._show_tuidan)
        self._query_worker.result_ready.connect(self._on_query_finished)
        self._query_worker.error.connect(
            lambda msg: show_info_bar(str(msg).split(chr(10))[0], "error",
                                      title="查询失败", parent=self,
                                      duration=4000))
        self._query_worker.start()

    def _on_query_finished(self, result):
        """查询完成回调：更新表格与分页，顺带取数据时间"""
        total, rows = result
        self._total = total
        self._populate(rows)
        self._update_pager()
        self._meta_worker = _DBQueryWorker(table_db.get_meta)
        self._meta_worker.result_ready.connect(self._on_time_meta)
        self._meta_worker.error.connect(lambda _m: None)
        self._meta_worker.start()

    def _on_time_meta(self, result):
        """同步时间元数据到手：状态栏展示数据时间"""
        _db_total, sync_time = result
        self._lbl_time.setText(f"数据时间: {sync_time}" if sync_time else "未同步")

    def _populate(self, rows):
        """填充表格（安装时间只展示日期部分，全量值看 tooltip）"""
        self._table.setRowCount(0)
        self._table.setRowCount(len(rows))
        for r, item in enumerate(rows):
            for c, (key, _title, _w) in enumerate(INSTALL_COLUMNS):
                v = item.get(key)
                if key == "createTime":
                    text = str(v or "")[:10]
                elif key == "room_count":
                    text = str(int(v or 0))
                else:
                    text = str(v or "")
                cell = QTableWidgetItem(text)
                if key in ("room_count", "name", "createTime"):
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                cell.setToolTip(str(v or ""))
                self._table.setItem(r, c, cell)

    def _update_pager(self):
        """刷新分页栏（末页保护：当前页超出总页数时钳制）"""
        total_pages = max(1, math.ceil(self._total / self._page_size))
        self._page_no = min(self._page_no, total_pages)
        self._lbl_page.setText(f"{self._page_no}/{total_pages}")
        self._btn_prev.setEnabled(self._page_no > 1)
        self._btn_next.setEnabled(self._page_no < total_pages)
        self._lbl_info.setText(f"共 {self._total} 台")

    # ---------- 月份/销售候选 ----------

    def _refresh_options_async(self):
        """异步刷新月份/销售下拉候选（重建时保持当前选择）"""
        if self._options_worker and self._options_worker.isRunning():
            self._options_worker.requestInterruption()
            self._options_worker.disconnect(self)
        self._options_worker = _DBQueryWorker(_fetch_install_options)
        self._options_worker.result_ready.connect(self._on_options_ready)
        # 候选加载失败静默（下拉仅剩「全部」，不影响主查询）
        self._options_worker.error.connect(lambda _m: None)
        self._options_worker.start()

    def _on_options_ready(self, result):
        months, sales_opts = result
        self._rebuild_combo(self._month_combo, _MONTH_ALL, months)
        self._rebuild_combo(self._sales_combo, _SALES_ALL, sales_opts)

    @staticmethod
    def _rebuild_combo(combo, all_text, items):
        """重建下拉项并保持当前选择（blockSignals 防止触发重查）"""
        current = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(all_text)
        combo.addItems(items)
        if current and combo.findText(current) >= 0:
            combo.setCurrentText(current)
        combo.blockSignals(False)

    # ---------- 交互 ----------

    def _on_search_input(self, _text):
        """搜索输入防抖：300ms 静默后才触发查询"""
        self._search_timer.start()

    def _do_search(self):
        self._page_no = 1
        self._load_local()

    def _on_month_changed(self, _text):
        self._page_no = 1
        self._load_local()

    def _on_sales_changed(self, _text):
        self._page_no = 1
        self._load_local()

    def _on_page_size_changed(self, text):
        try:
            self._page_size = int(text)
        except ValueError:
            return
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
        """「退单设备」显隐切换：回到第一页重新查询"""
        self._show_tuidan = (state == Qt.CheckState.Checked)
        self._page_no = 1
        self._load_local()

    # ---------- 导出 ----------

    def _export_xlsx(self):
        """按当前筛选导出 xlsx（格式与人工样例一致）"""
        ym = self._current_ym() or datetime.now().strftime("%Y-%m")
        default = f"球房安装清单{ym.replace('-', '')}.xlsx"
        path, _sel = QFileDialog.getSaveFileName(
            self, "导出安装清单", default, "Excel 文件 (*.xlsx)")
        if not path:
            return
        if self._export_worker and self._export_worker.isRunning():
            show_info_bar("已有导出进行中，请稍候", "warning",
                          title="提示", parent=self, duration=2000)
            return
        self._btn_export.setEnabled(False)
        self._export_worker = _DBQueryWorker(
            table_db.export_install_xlsx, path,
            self._search_edit.text().strip(),
            self._current_ym(), self._current_sales(),
            self._show_test, self._show_manual, self._show_tuidan)
        self._export_worker.result_ready.connect(
            lambda count, p=path: self._on_export_done(count, p))
        self._export_worker.error.connect(self._on_export_error)
        self._export_worker.start()

    def _on_export_done(self, count, path):
        self._btn_export.setEnabled(True)
        _show_export_bar(self, path, count)

    def _on_export_error(self, msg):
        self._btn_export.setEnabled(True)
        show_info_bar(str(msg).split(chr(10))[0], "error",
                      title="导出失败", parent=self, duration=4000)

    # ---------- 同步（球桌管理同款链路，安装数据随球桌同步落地） ----------

    def _sync_from_api(self):
        """从服务器拉取全量球桌数据（含安装时间三列）并落库"""
        if self._worker and self._worker.isRunning():
            return
        self._refresh_btn.setEnabled(False)
        self._lbl_info.setText("正在从服务器同步...")
        self._worker = TableFetchWorker()
        self._worker.result_ready.connect(self._on_sync_done)
        self._worker.error.connect(self._on_sync_error)
        self._worker.start()

    def _on_sync_done(self, rows):
        """API 同步完成：异步保存到本地数据库（error 必须有落点）"""
        self._save_worker = _DBQueryWorker(table_db.save_all, rows)
        self._save_worker.result_ready.connect(self._on_save_finished)
        self._save_worker.error.connect(self._on_sync_error)
        self._save_worker.start()

    def _on_save_finished(self, count):
        """保存完成：重置页码重查 + 刷新月份/销售候选 + 追加 xqzg 实时行"""
        self._page_no = 1
        self._load_local()
        self._refresh_options_async()
        self._refresh_btn.setEnabled(True)
        self._lbl_info.setText(f"同步完成，共 {count} 条")
        self._sync_xqzg_live()

    def _sync_xqzg_live(self):
        """追加拉 xqzg 实时行：sales_transfer（销售转移）回填数据源

        wechat listext 不含 sales_transfer；不追加这步，销售转移列一直是
        旧快照值。落库（save_xqzg）内部按 table_id ↔ 球桌号 合并回
        billiard_tables。失败静默：账号未配置/网络异常只记日志，不影响
        安装清单主数据（球桌管理页同款策略）。
        """
        if self._xqzg_sync_worker and self._xqzg_sync_worker.isRunning():
            return
        self._xqzg_sync_worker = SnookerOmFetchWorker(file_path="")
        self._xqzg_sync_worker.result_ready.connect(self._on_xqzg_live_done)
        self._xqzg_sync_worker.error.connect(
            lambda msg: logger.warning(
                "xqzg 实时行同步失败: %s", msg))
        self._xqzg_sync_worker.start()

    def _on_xqzg_live_done(self, rows):
        """xqzg 实时行拉取完成：异步落库（save_xqzg 内含 sales_transfer
        回填），完成后重查列表展示最新转移信息"""
        if not rows:
            return
        today = datetime.now().strftime("%Y/%m/%d")
        self._save_worker = _DBQueryWorker(table_db.save_xqzg, rows, today)
        self._save_worker.result_ready.connect(lambda _c: self._load_local())
        self._save_worker.error.connect(
            lambda msg: logger.warning("xqzg 实时行落库失败: %s", msg))
        self._save_worker.start()

    def _on_sync_error(self, msg):
        self._refresh_btn.setEnabled(True)
        self._lbl_info.setText("同步失败")
        show_info_bar(str(msg).split(chr(10))[0], "error",
                      title="同步失败", parent=self, duration=4000)
