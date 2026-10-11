# -*- coding: utf-8 -*-
"""记录与统计页（视图层）

分层（P1 架构试点，2026-10）：
    query_state.py    纯查询/文案逻辑，无 Qt，可直接 pytest
    table_adapter.py  一条记录 → 单元格呈现（CellSpec），页面只负责套用
    controller.py     全部 QThread 的归属地，只发信号不回摸控件
    page.py           ← 本文件：建控件、翻译用户意图、把结果贴到界面

页面不再 new QThread、不再持有 worker 属性；关窗前由 detach_workers()
统一转交 controller.stop_all()。对外入口（set_keyword/refresh_async/
_apply_smooth_mode/_load_cycles_then_data 等）保持不变。
"""

import os
from datetime import datetime, timedelta

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QLabel, QListWidget,
    QListWidgetItem, QFileDialog, QApplication,
    QDialog, QPushButton as _QPushButton, QGridLayout)
from PySide6.QtCore import (Qt, QTimer, QPointF, QDate, Signal, QEvent)
from PySide6.QtGui import QColor, QPainter, QPen, QFont
from qfluentwidgets import (TableWidget, SearchLineEdit, PushButton,
    PrimaryPushButton, ToolButton, FluentIcon, RoundMenu, Action,
    LineEdit, PlainTextEdit, BodyLabel, CaptionLabel, TitleLabel,
    ScrollArea, CardWidget, MessageBox, MessageBoxBase, CheckBox,
    FluentWindow, NavigationItemPosition, MenuAnimationType,
    setCustomStyleSheet, qconfig, isDarkTheme, ZhDatePicker, RadioButton,
    SpinBox, SegmentedWidget, ProgressBar, FlowLayout, ComboBox,
    EditableComboBox)

import json

from core.app_paths import get_app_dir
from core.design_tokens import SEMANTIC, lighten, darken
from core.flow_widgets import FlowToolbarScrollArea
from core.ops_link_delegate import LINKS_ROLE, install_ops_links
from core.perf import is_acrylic_enabled, apply_table_smooth_mode
from core.theme_qss import current_accent_hex
from core.utils import show_info_bar
from database import aftersale_db, table_db
from windows.mysql_sync_card import MysqlSyncCard

from windows.aftersale.common import *  # noqa: F401,F403
from windows.aftersale.records import query_state as qs
from windows.aftersale.records.controller import RecordsController
from windows.aftersale.records.table_adapter import (
    data_cells, row_background,
    FONT_BOLD, FONT_SMALL, OPS_LINKS_RESOLVED, OPS_LINKS_UNRESOLVED)
from windows.aftersale.dialogs import EditRecordDialog, ImportPreviewDialog
from windows.aftersale.stats_dialog import AfterSaleStatsDialog
from windows.stat_charts import StatsOpener, _aftersale_options

# 重要标记行底色与操作列链接清单下沉到 table_adapter.py，
# 排序键则直接交给 database/aftersale_db.py 的 ORDER BY 白名单。

# S5（2026-09-06）填充微优化：只读 item flags 循环外预计算一次
# （原每格 cell.flags() & ~ItemIsEditable，720 次/页跨边界枚举运算 → 1 次；
# 惰性初始化，避免模块导入期依赖 QApplication 环境）
_RO_FLAGS = None

# 线上售后面板兜底地址（本地 Web 服务未启用时超链接指向此处）
_WEB_FALLBACK_URL = "http://49.235.34.253/"


def _web_entry_url():
    """售后面板网页入口：本地 Web 服务（core.local_web_server）启用时指向
    http://localhost:<port>，否则回退线上站点。读配置门面 local_web 键
    （未登记键归 misc 域），与 local_web_server 的默认值保持一致
    （缺省 enabled=True, port=8787）。"""
    try:
        from core import app_settings
        cfg = (app_settings.get("local_web") or {})
        if bool(cfg.get("enabled", True)):
            return f"http://localhost:{int(cfg.get('port') or 8787)}/"
    except Exception:
        pass
    return _WEB_FALLBACK_URL


def _web_link_html():
    """超链接富文本：显示主机:端口，主题色无下划线，点击由
    QLabel.openExternalLinks 用系统浏览器打开。"""
    url = _web_entry_url().rstrip("/")
    host = url.split("//", 1)[-1]
    return (f'<a href="{url}" style="color: {current_accent_hex()}; '
            f'text-decoration: none;">{host}</a>')

# ==================== 板块二：记录与统计页 ====================

class RecordsPage(QWidget):
    """记录与统计页：周期概览指标卡 + 筛选/分页/批量操作/一键解决/编辑/删除/导出/导入

    表格列经过信息整合（球房+地区+桌号合入「位置」，填写人并入填写时间，
    解决人并入响应时间）；发生原因/解决方案/三个是否判定直接成列展示
    （是/否徽章），完整信息保留在行 tooltip；未解决行提供行内
    一键「标记已解决」。
    """

    # 面板标识：供 core.perf 面板级覆盖取值与全局刷新发现使用
    _PERF_PANEL_KEY = "aftersale"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page_no = 1
        self._page_size = 60
        self._total = 0
        self._rows = []
        # 行 id → 记录：表格行被排序/重建后，仍按锚定位原记录
        self._rec_by_id = {}
        # 后台任务全部交给 controller（页面不再持有任何 QThread）。
        # worker 属性、重入保护、seq 闭包都在 controller 内部；页面只连信号。
        self._controller = RecordsController(self)
        self._connect_controller()
        self._cycles_loaded = False
        self._manual_refresh = False  # 手动刷新标志：完成/失败时弹 infobar 反馈
        self._stats_dialog = None  # 售后统计弹窗（非模态，复用实例）
        # S3（2026-09-06）：周期物化列兜底回填只跑一次（首次加载前）
        self._recalc_done = False
        # 自动刷新（2026-09-16 需求：他人填写免手动同步）：轮询轻量指纹，
        # 仅在数据真的变化时静默重查，并保持阅读位置。
        self._auto_timer = QTimer(self)
        self._auto_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._auto_timer.timeout.connect(self._auto_refresh_tick)
        self._last_fingerprint = None   # 上次全表指纹快照（None=需重建基线）
        self._preserve_view = None      # 静默重查待恢复的 (滚动偏移, 勾选id集)
        self._init_ui()
        self._sync_auto_timer()
        # pygwalker 统计图表（独立浏览器窗口，工具栏「统计图表」按钮触发）
        self._stats_opener = StatsOpener(_aftersale_options, "aftersale", self)
        self._stats_opener.finished.connect(self._on_stats_finished)

    # ---------- 信号接线（controller → 视图） ----------

    def _connect_controller(self):
        """把后台任务的结果统一接到视图槽（页面侧唯一的线程入口）"""
        c = self._controller
        c.rows_ready.connect(self._on_loaded)
        c.load_failed.connect(self._on_load_error)
        c.cycles_ready.connect(self._on_cycles_loaded)
        c.cycles_failed.connect(lambda: self._load())
        c.recalc_finished.connect(self._after_recalc)
        c.fingerprint_ready.connect(self._on_fingerprint)
        c.mutation_done.connect(self._on_mutation_done)
        c.mutation_failed.connect(self._on_mutation_failed)
        c.export_done.connect(self._on_export_done)
        c.export_failed.connect(self._on_export_error)
        c.import_preview_ready.connect(self._on_import_preview_ready)
        c.import_done.connect(self._on_import_done)
        c.import_failed.connect(self._on_import_error)

    def detach_workers(self):
        """关窗/切页：请求中断本页所有后台线程（pivot_page 统一调用）"""
        self._controller.stop_all()

    # 写操作完成后的 InfoBar 文案（tag → (标题, 文案模板)）与失败标题
    _MUTATION_DONE = {
        "quick_resolve": ("操作成功", "记录已标记为已解决"),
        "batch_resolve": ("批量操作", "已标记 {0} 条为已解决"),
        "batch_delete": ("批量操作", "已删除 {0} 条记录"),
        "duplicate": ("复制成功", "记录已复制并新增"),
        "update": ("保存成功", "记录已更新"),
        "delete": ("删除成功", "记录已删除"),
    }
    _MUTATION_ERROR_TITLE = {
        "quick_resolve": "操作失败",
        "batch_resolve": "批量操作失败",
        "batch_delete": "批量操作失败",
        "duplicate": "复制失败",
        "update": "保存失败",
        "delete": "删除失败",
    }

    def _on_mutation_done(self, tag, payload):
        """写操作成功：按 tag 弹 InfoBar 并重查当前页"""
        if tag == "duplicate":
            # 复制新增成功后同样记住该发生日期（保持「下一条=这条的日期」）
            aftersale_db.save_last_occurred(getattr(self, "_dup_occurred", ""))
        title, text = self._MUTATION_DONE.get(tag, ("操作成功", "操作已完成"))
        if "{0}" in text:
            text = text.format(payload)
        show_info_bar(text, "success", title=title, parent=self,
                      duration=2500 if tag.startswith("batch_") else 2000)
        self._load()

    def _on_mutation_failed(self, tag, msg):
        show_info_bar(msg, "error",
                      title=self._MUTATION_ERROR_TITLE.get(tag, "操作失败"),
                      parent=self, duration=4000)

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 12)
        root.setSpacing(8)

        # --- 周期概览指标卡（四张，随筛选口径实时更新） ---
        # 概览标题行：概览（左）+ 网页入口/数据源指示（右）。
        # 网页链接与数据源 2026-09-07 从独立页头行下移合并到本行、与概览
        # 齐平（页头左侧标题早已删除，原行只剩右侧两个小标签、单独占一行
        # 浪费垂直空间）。
        ov_head = QHBoxLayout()
        ov_head.setSpacing(8)
        self._lbl_overview = BodyLabel("概览", self)
        ov_head.addWidget(self._lbl_overview)
        ov_head.addStretch(1)
        # 网页版入口：浏览器打开售后面板网页（本地 8787 / 线上兜底）。
        # 用富文本 QLabel 而非 HyperlinkButton：与数据源标签同字体族渲染，
        # 天然基线对齐，无按钮内边距导致的文字顶部裁切
        self._btn_web = CaptionLabel("", self)
        self._btn_web.setTextFormat(Qt.TextFormat.RichText)
        self._btn_web.setOpenExternalLinks(True)
        self._btn_web.setText(_web_link_html())
        self._btn_web.setToolTip("在浏览器中打开售后面板网页")
        ov_head.addWidget(self._btn_web)
        # 数据源指示：MySQL / 本地 SQLite / 降级兜底
        self._lbl_source = CaptionLabel("", self)
        ov_head.addWidget(self._lbl_source)
        root.addLayout(ov_head)
        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)
        self._card_total = self._make_stats_card(cards_row)
        self._card_unresolved = self._make_stats_card(cards_row)
        self._card_initiative = self._make_stats_card(cards_row)
        self._card_our_problem = self._make_stats_card(cards_row)
        root.addLayout(cards_row)
        # 「未解决」卡可点击：一键联动「是否解决=否」筛选，展示未解决条目
        # 再次点击恢复全部
        unr_card = self._card_unresolved[0]
        unr_card.setCursor(Qt.CursorShape.PointingHandCursor)
        unr_card.setToolTip("点击查看未解决条目（再次点击恢复全部）")
        unr_card.clicked.connect(self._on_unresolved_card_clicked)
        # 「售后总数」卡可点击：打开详细统计弹窗（每日趋势/地区/问题类型，
        # 独立可缩放非模态窗口，统计范围跟随当前筛选）
        total_card = self._card_total[0]
        total_card.setCursor(Qt.CursorShape.PointingHandCursor)
        total_card.setToolTip(
            "点击查看详细统计（每日趋势 · 地区分布 · 问题类型）")
        total_card.clicked.connect(self._on_total_card_clicked)

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

        # 周期筛选（默认当前周期；qfluentwidgets ComboBox 支持 findData/currentData，
        # 需求9 统一为 QFluentWidgets 组件；userData 周期值经 addItem 传入）
        # 录入页周期为只读芯片（按发生时间自动归属、不可选），筛选需可选项
        # 列表（全部/当前/各历史周期），故保持下拉形态不复用录入页组件
        self._cycle_combo = ComboBox(self)
        self._cycle_combo.setFixedWidth(210)
        self._cycle_combo.currentIndexChanged.connect(
            lambda _i: self._on_filter_changed())
        toolbar.addWidget(self._cycle_combo)

        # 类型筛选（与填写录入页同款 EditableComboBox：预置可选、可手输；
        # 需求13 替换 FluentCombo 原生 QComboBox 为 qfluentwidgets 组件）
        self._type_combo = EditableComboBox(self)
        self._type_combo.addItem("全部类型")
        self._type_combo.addItems(aftersale_db.ISSUE_TYPES)
        self._type_combo.setFixedWidth(140)
        self._type_combo.currentIndexChanged.connect(
            lambda _i: self._on_filter_changed())
        # 手输时无 index 变化，走防抖搜索（与关键词同一 300ms 防抖）
        self._type_combo.currentTextChanged.connect(
            lambda _t: self._search_timer.start())
        toolbar.addWidget(self._type_combo)

        # 是否解决筛选（与填写录入页同款分段开关，三态：全部/否/是）
        self._resolved_seg = YesNoSegment("", self, include_all=True)
        self._resolved_seg.currentItemChanged.connect(
            lambda _k: self._on_filter_changed())
        toolbar.addWidget(CaptionLabel("是否解决：", self))
        toolbar.addWidget(self._resolved_seg)

        # 是否是我们的问题筛选
        self._our_problem_seg = YesNoSegment("", self, include_all=True)
        self._our_problem_seg.setToolTip("是否是我们的问题")
        self._our_problem_seg.currentItemChanged.connect(
            lambda _k: self._on_filter_changed())
        toolbar.addWidget(CaptionLabel("是否是我们问题：", self))
        toolbar.addWidget(self._our_problem_seg)

        # 是否我们主动发起筛选
        self._initiative_seg = YesNoSegment("", self, include_all=True)
        self._initiative_seg.setToolTip("是否我们主动发起")
        self._initiative_seg.currentItemChanged.connect(
            lambda _k: self._on_filter_changed())
        toolbar.addWidget(CaptionLabel("是否主动发起：", self))
        toolbar.addWidget(self._initiative_seg)

        # 关键词搜索（防抖）
        self._search_edit = SearchLineEdit(self)
        self._search_edit.setPlaceholderText("搜索")
        self._search_edit.setFixedWidth(180)
        self._search_edit.textChanged.connect(self._on_search_input)
        toolbar.addWidget(self._search_edit)

        self._btn_add = PrimaryPushButton(FluentIcon.ADD, "新增", self)
        self._btn_add.setToolTip("新增一条售后记录")
        self._btn_add.clicked.connect(self._on_add)
        toolbar.addWidget(self._btn_add)

        self._btn_import = PushButton(FluentIcon.ADD, "导入 Excel", self)
        self._btn_import.setToolTip("一次性导入 售后问题汇总 xlsx 历史数据")
        self._btn_import.clicked.connect(self._on_import)
        toolbar.addWidget(self._btn_import)

        self._btn_export = PushButton(FluentIcon.DOWNLOAD, "导出 xlsx", self)
        self._btn_export.setToolTip("按当前筛选条件导出 xlsx")
        self._btn_export.clicked.connect(self._on_export)
        toolbar.addWidget(self._btn_export)

        self._btn_refresh = PushButton(FluentIcon.SYNC, "刷新", self)
        self._btn_refresh.setToolTip("重新查询数据库")
        self._btn_refresh.clicked.connect(self._on_refresh)
        toolbar.addWidget(self._btn_refresh)

        # pygwalker 自助分析入口
        self._btn_stats_chart = PushButton(
            FluentIcon.HISTORY, "统计图表", self)
        self._btn_stats_chart.setToolTip(
            "打开 pygwalker 自助分析窗口")
        self._btn_stats_chart.clicked.connect(self._on_open_stats_chart)
        toolbar.addWidget(self._btn_stats_chart)

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
        # 操作列文字链接委托（2026-09-19）：单元格内自绘「已解决/编辑/删除」+ 矩形
        # 命中，取代原 cellWidget（60 行一页 = 210 个 QWidget，刷新填充实测
        # 31ms → 8ms）。必须装为视图级委托：qfluentwidgets 的 hover/selected 行
        # 状态是视图推给视图级委托实例的，用 setItemDelegateForColumn 会丢整行高亮。
        install_ops_links(self._table, self._on_ops_link)
        # 性能（2026-08-26）：默认关闭 qfluentwidgets 平滑滚动动画——滚轮触发
        # 动画引擎逐帧 moveScrollBar，50 行 + 行内控件逐帧重绘导致滚动卡顿；
        # NO_SMOOTH 走原生滚动。按 本面板覆盖→全局 生效，
        # 设置页开关可即时切换（见 _apply_smooth_mode）。
        self._apply_smooth_mode()
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
        # 列排序：点击表头升/降序切换，排序键下推到 SQL 才能跨页一致
        self._sort_col = -1        # 当前排序逻辑列号，-1 表示按 id 倒序
        self._sort_asc = True
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self._on_click_header)
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

    def _apply_smooth_mode(self):
        """按当前生效的平滑滚动设置（本面板覆盖→全局）刷新表格滚动模式（即时）"""
        apply_table_smooth_mode(self._table, panel=self._PERF_PANEL_KEY)

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
        """2026-08-22 21:14:33 → 08-22 21:14"""
        return qs.short_dt(val)

    # ---------- 筛选条件 ----------

    def _current_filters(self) -> dict:
        """汇集当前筛选条件（周期/类型/状态/是否我们主动发起/是否我们的问题/关键词）"""
        return qs.build_filters(
            self._cycle_combo.currentData(),
            self._type_combo.currentText(),
            self._resolved_seg.value(),
            self._initiative_seg.value(),
            self._our_problem_seg.value(),
            self._search_edit.text())

    def _on_search_input(self, _text):
        self._search_timer.start()

    def _on_filter_changed(self):
        self._page_no = 1
        self._load()

    def _update_source_label(self):
        """刷新数据源指示标签（读取时调用，反映最新后端状态）

        - MySQL 开启且可用：绿色「数据源: MySQL」
        - MySQL 未开启：灰色「数据源: 本地 SQLite」
        - MySQL 开启但不可用：橙色「本地 SQLite」
        """
        from database import backend
        if not backend.is_mysql_test_mode():
            text, color = "数据源: 本地 SQLite", SEMANTIC["neutral"]
        elif backend.get_state() == backend.STATE_ONLINE:
            text, color = "数据源: MySQL", SEMANTIC["success"]
        else:
            text, color = ("数据源: 本地 SQLite 原因：服务器 MySQL 连接不可达",
                           SEMANTIC["warning"])
        self._lbl_source.setText(text)
        self._lbl_source.setStyleSheet(f"color: {color};")
        self._lbl_source.setToolTip(
            "关闭 MySQL 后自动读写本地 SQLite；"
            "MySQL 恢复可用时会自动切回并合并兜底增量")
        # 同步刷新网页入口（跟随主题色与 settings 中 local_web 最新配置）
        self._btn_web.setText(_web_link_html())

    def _on_unresolved_card_clicked(self):
        """点击概览「未解决」卡：切到未解决列表（再次点击恢复全部）"""
        if self._resolved_seg.value() == "否":
            self._resolved_seg.setValue("")   # 恢复「全部」
        else:
            self._resolved_seg.setValue("否")  # 只看未解决
        self._on_filter_changed()

    def _on_total_card_clicked(self):
        """点击概览「售后总数」卡：打开详细统计弹窗（非模态，实例复用）

        统计范围跟随当前筛选；弹窗内点击地区/类型行会联动筛选列表。
        """
        if self._stats_dialog is None:
            self._stats_dialog = AfterSaleStatsDialog(
                self._current_filters(), self.window())
            self._stats_dialog.apply_filter.connect(self._on_stats_filter)
        else:
            # 复用实例：更新统计范围并刷新（避免每次打开新建父级弹窗堆积）
            self._stats_dialog.set_filters(self._current_filters())
        self._stats_dialog.show()
        self._stats_dialog.raise_()
        self._stats_dialog.activateWindow()

    def _on_stats_filter(self, overrides: dict):
        """统计弹窗点击联动：按条件筛选记录列表并切回本页

        overrides 支持 {"issue_type": str} 与 {"keyword": str}（地区走关键词，
        关键词搜索覆盖 region 字段）。
        """
        f = overrides or {}
        self._type_combo.blockSignals(True)
        self._search_edit.blockSignals(True)
        try:
            if "issue_type" in f:
                t = str(f.get("issue_type") or "").strip()
                idx = self._type_combo.findText(t)
                if idx >= 0:
                    self._type_combo.setCurrentIndex(idx)
                elif t:
                    try:
                        self._type_combo.setEditText(t)
                    except Exception:
                        pass  # 非常规可编辑实现，忽略
            if "keyword" in f:
                self._search_edit.setText(str(f.get("keyword") or ""))
        finally:
            self._type_combo.blockSignals(False)
            self._search_edit.blockSignals(False)
        self._on_filter_changed()
        # 确保记录页可见（弹窗为非模态，期间可能已切到其他页）。
        # 宿主兼容：主窗口单窗口导航走 switch_to_page 路由（本页在
        # AftersaleHub 的 Pivot 内）；独立面板走 FluentWindow.switchTo
        win = self.window()
        router = getattr(win, "switch_to_page", None) if win is not None else None
        if router is not None:
            try:
                router(self)
            except Exception:
                pass
        elif win is not None and hasattr(win, "switchTo"):
            try:
                win.switchTo(self)
            except Exception:
                pass
        show_info_bar("已按统计条件筛选记录列表", "success",
                      title="统计联动", parent=self, duration=2000)

    def _on_open_stats_chart(self):
        """打开 pygwalker 自助分析窗口。"""
        self._btn_stats_chart.setEnabled(False)
        self._stats_opener.open_analysis(self._current_filters())

    def _on_stats_finished(self, ok, msg):
        self._btn_stats_chart.setEnabled(True)
        show_info_bar(msg, message_type="success" if ok else "error",
                      parent=self)

    def _on_refresh(self):
        """手动刷新：重建周期选项并重查（多人协作看到他人新数据）

        完成后弹 infobar 提示刷新结果（成功显示条数，失败显示原因）。
        """
        self._cycles_loaded = False
        self._manual_refresh = True
        self._load_cycles_then_data()

    def refresh_async(self):
        """其他页面提交后静默刷新（不重置周期选择）"""
        self._load()

    # ---------- 自动刷新（2026-09-16：他人填写免手动同步） ----------

    def _sync_auto_timer(self):
        """按当前配置启停轮询定时器（开关/间隔变更后调用，也用于构造时初始化）"""
        try:
            enabled = aftersale_db.auto_refresh_enabled()
            interval = aftersale_db.auto_refresh_interval()
        except Exception:
            enabled, interval = False, 30
        self._auto_timer.setInterval(interval * 1000)
        # 启停同时受页面可见性约束（show/hideEvent 联动）：隐藏的页面
        # （弹窗口关闭仅隐藏/切走的其他 Pivot 页）不轮询，省无谓查询
        should_run = enabled and self.isVisible()
        if should_run:
            if not self._auto_timer.isActive():
                self._auto_timer.start()
        elif self._auto_timer.isActive():
            self._auto_timer.stop()

    def _auto_refresh_tick(self):
        """定时器回调：前置守卫后起一条轻量指纹查询。

        定时器仅在「开关开 + 页面可见」时运行（_sync_auto_timer 与
        show/hideEvent 联动）。跳过条件：有模态弹窗在前台（编辑/确认/
        设置）、或本表正在查询/写库——不打扰用户操作，等下个周期。
        """
        from PySide6.QtWidgets import QApplication
        if QApplication.activeModalWidget() is not None:
            return
        if self._controller.is_running(
                "_auto_worker", "_query_worker", "_mutate_worker"):
            return
        self._controller.check_fingerprint()

    def _on_fingerprint(self, fp):
        """指纹回来：与上次快照比对，一致则完全不动表格，变化才静默重查。"""
        if fp is None:
            return
        fp = tuple(fp)
        if self._last_fingerprint is None:
            self._last_fingerprint = fp  # 建立基线（首帧/重开后）不刷新
            return
        if fp == self._last_fingerprint:
            return  # 无远端变化：保留滚动/勾选/展开，零重绘
        self._last_fingerprint = fp
        # 先捕获阅读位置再重查；_load 自增 seq 后记录本轮期望值，
        # _on_loaded 只有拿到本次结果（seq 匹配）才还原，避免误消费
        cap = self._capture_view()
        self._load()
        self._preserve_view = cap + (self._query_seq,)

    def _capture_view(self):
        """捕获当前阅读位置：垂直滚动偏移 + 已勾选记录 id 集合"""
        sb = self._table.verticalScrollBar()
        offset = sb.value() if sb is not None else 0
        return (offset, set(self._checked_ids()))

    def _restore_view(self, captured):
        """静默重查后还原滚动位置与勾选（数据变了也尽量让用户停在原地）

        captured = (滚动偏移, 勾选id集, 期望seq)——仅当本轮结果 seq 匹配
        （即这是触发还原的那次查询的返回值）时才由 _on_loaded 调用。
        """
        offset, checked = captured[0], captured[1]
        try:
            if checked:
                self._table.blockSignals(True)
                try:
                    for r in range(self._table.rowCount()):
                        it = self._table.item(r, _COL_CHECK)
                        if it is None:
                            continue
                        rid = it.data(Qt.ItemDataRole.UserRole)
                        it.setCheckState(
                            Qt.CheckState.Checked if rid in checked
                            else Qt.CheckState.Unchecked)
                finally:
                    self._table.blockSignals(False)
                self._sync_batch_bar()
        except Exception:
            pass
        # 滚动还原延后一个事件循环：setRowCount 后 Qt 尚未重算 scrollbar
        # maximum，同步 setValue 会被陈旧上限夹小（实测停在 0 附近）
        def _apply_scroll():
            try:
                sb = self._table.verticalScrollBar()
                if sb is not None:
                    sb.setValue(min(offset, sb.maximum()))
            except Exception:
                pass
        QTimer.singleShot(0, _apply_scroll)

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
        # 切回本页：若开了自动刷新，重启定时器并立刻补一次比对——
        # 隐藏期间（在填写页/其他面板）他人的新记录即刻呈现
        if aftersale_db.auto_refresh_enabled():
            self._sync_auto_timer()
            self._auto_refresh_tick()

    def hideEvent(self, event):
        super().hideEvent(event)
        # 页面隐藏（切页/弹窗口关闭）：停轮询省查询；开关与基线保留，
        # 下次 showEvent 恢复
        self._auto_timer.stop()

    def _load_cycles_then_data(self):
        """先异步拉周期选项填充下拉，再加载数据

        S3（2026-09-06）：首次执行前先起一次周期物化列兜底 worker
        （recalc_cycle_starts_on_load：待办标志或空值行探测，干净库上
        毫秒级无感知；上次重算失败残留待办标志或存在空 cycle_start
        存量行时全量重算），完成后再拉周期与数据，保证周期筛选
        （物化列等值过滤）口径就绪。_recalc_done 标志只跑一次（每次
        打开面板检查一次待办，幂等）。
        """
        if not self._recalc_done:
            self._recalc_done = True
            # 成功/失败都由 recalc_finished 信号回到 _after_recalc
            self._controller.recalc_cycles()
            return
        self._fetch_cycles()

    def _after_recalc(self):
        """兜底重算完成：若期间已有周期链在跑/已完成则不再重复起链

        S2/S3 返工（2026-09-06）：showEvent 触发的链 A（recalc → cycles →
        data）与显式再调 _load_cycles_then_data 的链 B 并发交叠时，本回调
        会在任意时刻重入造成双链交错（QA 实测偶发最终无数据）——已有周期
        worker 在跑或周期已加载即跳过，链 B 会带着最新筛选取代本链。
        """
        if self._controller.is_running("_cycles_worker") or self._cycles_loaded:
            return
        self._fetch_cycles()

    def _fetch_cycles(self):
        """异步拉周期选项填充下拉，完成后加载数据

        重入保护：已有周期拉取在跑时不再重复起链（showEvent 与显式调用/
        连续刷新交叠场景）——在跑的链完成后会带着最新筛选项加载数据。
        """
        # 重入保护在 controller 内部；失败由 cycles_failed 信号接到 _load
        self._controller.fetch_cycles()

    def _on_cycles_loaded(self, cycle_starts):
        self._cycles_loaded = True
        prev = self._cycle_combo.currentData()
        self._cycle_combo.blockSignals(True)
        self._cycle_combo.clear()
        current = aftersale_db.current_cycle_start()
        cycles = list(cycle_starts or [])
        # 当前周期仅在库中确实存在该周期数据时才出现（不额外新建库中不存在的周期）
        # 注意：项文本不可带前导空格——qfluentwidgets ComboBox 菜单点击用
        # findText(i.text().lstrip()) 匹配 index，带空格项会失配返回 -1，
        # 触发 setCurrentIndex(-1) 清空文字且 currentData() 变 None（查全部数据）。
        if current in cycles:
            self._cycle_combo.addItem(
                aftersale_db.cycle_label(current), userData=current)
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
        """按当前筛选异步查询（分页数据 + 统计一次返回）

        查询代数守卫保留在页面侧：即便旧查询结果已排队，回调时按 seq
        丢弃过期帧（2026-09-08 搜索中新增竞态修复）。
        """
        self._query_seq = getattr(self, "_query_seq", 0) + 1
        # 排序状态下推到 SQL：翻页/筛选后仍是全量数据的顺序，而非当前 60 行
        self._controller.query(
            self._current_filters(), self._page_no, self._page_size,
            self._query_seq, self._sort_key(), self._sort_order())

    def _on_loaded(self, result, seq=None):
        if seq is not None and seq != getattr(self, "_query_seq", None):
            return  # 过期查询结果（已被更新的筛选/翻页取代），丢弃
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
        if self._manual_refresh:
            # 手动点击刷新后的完成反馈（筛选/翻页/静默刷新不弹）
            self._manual_refresh = False
            show_info_bar(f"已刷新 · 共 {total} 条记录", "success",
                          title="刷新", parent=self, duration=2500)
        # 自动刷新的静默重查：还原触发前的滚动位置与勾选（用户无感）
        pv = self._preserve_view
        if pv is not None:
            self._preserve_view = None
            if seq is None or seq == pv[2]:
                self._restore_view(pv)

    def _on_load_error(self, msg, seq=None):
        if seq is not None and seq != getattr(self, "_query_seq", None):
            return  # 过期查询的错误，丢弃
        self._lbl_stats.setText(f"查询失败: {msg}")
        self._update_source_label()
        if self._manual_refresh:
            self._manual_refresh = False
            show_info_bar(f"刷新失败: {msg}", "error",
                          title="刷新", parent=self, duration=4000)

    # ---------- 概览指标卡 ----------

    def _update_overview(self, stats: dict):
        """统计 → 四张指标卡（售后总数/未解决/主动发起占比/我方问题占比）"""
        total = int(stats.get("total") or 0)
        resolved = int(stats.get("resolved") or 0)
        unresolved = int(stats.get("unresolved") or 0)
        initiative = int(stats.get("initiative") or 0)
        our_problem = int(stats.get("our_problem") or 0)
        base = "font-size: 24px; font-weight: 500; background: transparent;"

        _card, _lbl, num, sub = self._card_total
        _lbl.setText("售后总数")
        num.setText(str(total))
        num.setStyleSheet(base)
        sub.setText(f"已解决 {resolved} · 未解决 {unresolved}")

        _card, _lbl, num, sub = self._card_unresolved
        _lbl.setText("未解决")
        num.setText(str(unresolved))
        # 未解决为 0 时用常规字色，有积压时用危险红提醒
        color = SEMANTIC["danger"] if unresolved > 0 else ""
        num.setStyleSheet(base + (f" color: {color};" if color else ""))
        sub.setText(f"待处理有 {unresolved} 条" if unresolved else "全部已处理")

        _card, _lbl, num, sub = self._card_initiative
        _lbl.setText("主动发起")
        init_rate = int(round(initiative * 100 / total)) if total else 0
        num.setText(f"{init_rate}%")
        num.setStyleSheet(base)
        sub.setText(f"我们发起的售后有 {initiative} 条")

        _card, _lbl, num, sub = self._card_our_problem
        _lbl.setText("我方问题")
        our_rate = int(round(our_problem * 100 / total)) if total else 0
        num.setText(f"{our_rate}%")
        num.setStyleSheet(base)
        sub.setText(f"我们的问题有 {our_problem} 条")

        self._lbl_stats.setText(f"共 {total} 条")

    # ---------- 表格渲染 ----------

    def _row_tooltip(self, rec: dict) -> str:
        """行 tooltip：整合字段完整信息（含移入 tooltip 的判定字段）

        实现下沉到 query_state.row_tooltip（无 Qt，可单测）；本方法保留
        是历史兼容点（tools/perf/scroll_profile.py:198 monkeypatch 它）。
        """
        return qs.row_tooltip(rec)

    def _populate(self, rows):
        """行数据 → 表格：勾选列 + 双行信息整合列 + 是/否徽章三列 + 行内操作链接

        呈现规则（文本/字体/前景底色/链接清单）已下沉到
        table_adapter.data_cells；本方法只负责「套用」与性能开关：

        - 派发上一代 deleteLater 僵尸控件（processEvents 不消化 DeferredDelete，
          须显式派发；2026-09-19 操作列文字化后已无 cellWidget，此行保留作为
          弹窗/其他控件延迟销毁的卫生兵，实测零开销）
        - setUpdatesEnabled(False) + blockSignals 包住整轮填充
        - 只读 item flags 循环外预计算（720 次/页跨边界枚举 → 1 次）
        - 字体实例循环外构造一次（避免每格 QFont 拷贝）
        - 勾选列 item 写入记录 id 锚（UserRole），行序变化后仍能按锚定位
        """
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self._rec_by_id = {rec.get("id"): rec for rec in rows}
        self._table.setUpdatesEnabled(False)
        self._table.blockSignals(True)
        try:
            self._table.setRowCount(len(rows))
            global _RO_FLAGS
            if _RO_FLAGS is None:
                _RO_FLAGS = QTableWidgetItem().flags() & \
                    ~Qt.ItemFlag.ItemIsEditable
            fonts = {FONT_SMALL: QFont(), FONT_BOLD: QFont()}
            fonts[FONT_SMALL].setPointSizeF(10.5)
            fonts[FONT_BOLD].setBold(True)
            for r, rec in enumerate(rows):
                tip = self._row_tooltip(rec)
                # 重要标记：勾选「重要」后该行整体淡黄底色（浅色亮黄 / 深色暗黄随主题）
                row_bg = row_background(rec)

                chk = QTableWidgetItem()
                chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable
                             | Qt.ItemFlag.ItemIsEnabled)
                chk.setCheckState(Qt.CheckState.Unchecked)
                chk.setData(Qt.ItemDataRole.UserRole, rec.get("id"))
                if row_bg is not None:
                    chk.setBackground(row_bg)
                self._table.setItem(r, _COL_CHECK, chk)

                for i, spec in enumerate(data_cells(rec)):
                    col = _COL_CHECK + 1 + i
                    cell = QTableWidgetItem(spec.text)
                    font = fonts.get(spec.font)
                    if font is not None:
                        cell.setFont(font)
                    if spec.fg is not None:
                        cell.setForeground(spec.fg)
                    if spec.bg is not None:
                        cell.setBackground(spec.bg)
                    if spec.align_center:
                        cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    if spec.links is not None:
                        cell.setData(LINKS_ROLE, spec.links)
                    cell.setToolTip(tip)
                    cell.setFlags(_RO_FLAGS)
                    if row_bg is not None:
                        cell.setBackground(row_bg)
                    self._table.setItem(r, col, cell)
        finally:
            self._table.setUpdatesEnabled(True)
            self._table.blockSignals(False)
        self._sync_batch_bar()

    def _on_ops_link(self, row: int, _col: int, action: str):
        """操作列文字链接点击分发（core.ops_link_delegate 的 editorEvent 回调）

        行号是表格可视行号，可能与 _rows 下标错位；统一经勾选列 id 锚
        反查记录，排序、筛选、静默刷新都不会把操作落到邻行。
        """
        rec = self._rec_at_row(row)
        if rec is None:
            return
        if action == "resolve":
            self._quick_resolve_rec(rec)
        elif action == "edit":
            self._on_edit_rec(rec)
        elif action == "delete":
            self._on_delete_rec(rec)

    # ---------- 勾选与批量操作 ----------

    def _checked_ids(self) -> list:
        """当前勾选行的记录 id 列表（按勾选列 UserRole 记录锚读取，
        排序移动整行后勾选状态与 id 随行移动，集合天然保持对应）"""
        ids = []
        for r in range(self._table.rowCount()):
            it = self._table.item(r, _COL_CHECK)
            if it is not None and it.checkState() == Qt.CheckState.Checked:
                rid = it.data(Qt.ItemDataRole.UserRole)
                if rid is not None:
                    ids.append(rid)
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
        if self._controller.is_running("_batch_worker"):
            return
        if not MessageBox("批量标记已解决",
                          f"确定将选中的 {len(ids)} 条记录标记为已解决？",
                          self.window()).exec():
            return
        self._controller.batch_mutate(
            "batch_resolve", aftersale_db.mark_resolved_batch, ids)

    def _on_batch_delete(self):
        """批量删除（确认后走 delete_records）"""
        ids = self._checked_ids()
        if not ids:
            return
        if self._controller.is_running("_batch_worker"):
            return
        if not MessageBox("批量删除",
                          f"确定删除选中的 {len(ids)} 条售后记录？\n删除后不可恢复",
                          self.window()).exec():
            return
        self._controller.batch_mutate(
            "batch_delete", aftersale_db.delete_records, ids)

    # ---------- 列排序 ----------

    def _col_key(self, col: int):
        """逻辑列号 → TABLE_COLUMNS 的数据键；勾选列/越界返回 None"""
        return qs.col_key(col, TABLE_COLUMNS, _COL_CHECK)

    def _rec_at_row(self, row: int):
        """可视行号 → 记录：以勾选列 id 锚反查，不依赖 _rows 下标"""
        if row < 0 or row >= self._table.rowCount():
            return None
        it = self._table.item(row, _COL_CHECK)
        rid = it.data(Qt.ItemDataRole.UserRole) if it is not None else None
        return self._rec_by_id.get(rid) if rid is not None else None

    def _sort_key(self) -> str:
        """当前排序对外的数据键；未排序为空串，由 DB 回退 id DESC"""
        return self._col_key(self._sort_col) if self._sort_col >= 0 else ""

    def _sort_order(self) -> str:
        """当前排序方向，与 _build_order 的 asc/desc 约定一致"""
        return "asc" if self._sort_asc else "desc"

    def _on_click_header(self, col: int):
        """表头点击：首点升序、再点降序；排序交给服务端跨页生效

        操作列与勾选列没有可排序的字段，点击直接忽略，避免指示器
        显示已排序而数据实际按 id 回退。
        """
        key = self._col_key(col)
        if col == _COL_CHECK or key not in aftersale_db.LIST_SORT_KEYS:
            return
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True
        header = self._table.horizontalHeader()
        header.setSortIndicator(
            col, Qt.SortOrder.AscendingOrder if self._sort_asc
            else Qt.SortOrder.DescendingOrder)
        # 排序变化后旧页号没有意义，从第一页重新取
        self._page_no = 1
        self._load()

    # ---------- 行内快捷操作 ----------

    def _quick_resolve(self, row: int):
        """一键标记已解决（按行号入口：右键菜单/双击等）"""
        rec = self._rec_at_row(row)
        if rec is not None:
            self._quick_resolve_rec(rec)

    def _quick_resolve_rec(self, rec: dict):
        """一键标记已解决：最小化更新（不弹编辑窗），3 步变 1 步

        按记录引用定位（行内按钮回调入口）——S2 排序移动整行后行号会失效，
        按引用/id 不受影响。"""
        if not rec or not rec.get("id"):
            return
        if self._controller.is_running("_batch_worker"):
            return
        self._controller.batch_mutate(
            "quick_resolve", aftersale_db.mark_resolved_batch,
            [rec.get("id")])

    def _update_pager(self):
        pages = qs.pages_of(self._total, self._page_size)
        self._page_no = min(self._page_no, pages)
        self._lbl_page.setText(
            qs.pager_text(self._page_no, self._total, self._page_size))
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
        # 复制：按原记录内容新增一条相同记录
        act_copy = Action(FluentIcon.COPY, "复制", self._table)
        act_copy.triggered.connect(
            lambda _=False: self._on_duplicate(idx.row()))
        menu.addAction(act_copy)
        rec = self._rec_at_row(idx.row()) or {}
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

    def _on_add(self):
        """工具栏「+」：唤起填写面板（连续录入模式，对齐 Web 端 2026-09-22）"""
        # 空表单 = 新增模式：复用 EditRecordDialog 的 AftersaleForm（含必填校验/收集）。
        # continuous=True：保存成功不关窗、清空问题字段等待下一条，点「完成」才关窗；
        # 每条成功经 on_saved 回调实时刷新列表（记住发生日期由弹窗内部处理）
        dlg = EditRecordDialog({}, self, title="新增售后记录",
                               continuous=True, on_saved=self._load)
        # 新增弹窗也需动态候选
        self._controller.candidates_ready.connect(dlg.form.load_candidates)
        self._controller.fetch_candidates()
        dlg.exec()
        # 关窗后兜底刷新一次（连续录入期间每条已实时刷新）
        self._load()

    def _on_duplicate(self, row: int = -1):
        """复制当前行：按原记录内容新增一条相同记录（填写时间/周期重算）"""
        if row < 0:
            row = self._selected_row()
        rec = self._rec_at_row(row)
        if rec is None:
            return
        rec = dict(rec)
        rec.pop("id", None)
        rec.pop("created_at", None)    # insert_record 自动取当前填写时间
        rec.pop("cycle_start", None)   # 周期按发生时间重新归属
        # 复制新增成功 → 同样记住该发生日期（保持「下一条=这条的日期」语义一致）
        # 由 _on_mutation_done 的 duplicate 分支落地
        self._dup_occurred = str(rec.get("occurred_at") or "")
        self._controller.mutate("duplicate", aftersale_db.insert_record, rec)

    def _on_edit(self, row: int = -1):
        """编辑（按行号入口：双击/右键菜单/选中行）"""
        if row < 0:
            row = self._selected_row()
        rec = self._rec_at_row(row)
        if rec is not None:
            self._on_edit_rec(rec)

    def _on_edit_rec(self, rec: dict):
        """编辑（按记录引用入口：行内按钮回调，排序后行号失效不影响定位）"""
        if not rec:
            return
        rec = dict(rec)
        dlg = EditRecordDialog(rec, self)
        # 编辑弹窗也需动态候选
        self._controller.candidates_ready.connect(dlg.form.load_candidates)
        self._controller.fetch_candidates()
        if dlg.exec() and getattr(dlg, "collected", None):
            collected = dlg.collected
            collected["id"] = rec.get("id")
            collected["created_at"] = rec.get("created_at")  # 保留原填写时间
            self._run_update(collected)

    def _run_update(self, record):
        self._controller.mutate("update", aftersale_db.update_record, record)

    def _on_delete(self, row: int = -1):
        """删除（按行号入口：右键菜单/选中行）"""
        if row < 0:
            row = self._selected_row()
        rec = self._rec_at_row(row)
        if rec is not None:
            self._on_delete_rec(rec)

    def _on_delete_rec(self, rec: dict):
        """删除（按记录引用入口：行内按钮回调，排序后行号失效不影响定位）"""
        if not rec:
            return
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
        self._controller.mutate("delete", aftersale_db.delete_record,
                                rec.get("id"))

    # ---------- 导出 / 导入 ----------

    def _on_export(self):
        if self._controller.is_running("_export_worker"):
            return
        f = self._current_filters()
        default_name = "售后记录_" + datetime.now().strftime("%Y%m%d_%H%M") + ".xlsx"
        path, _sel = QFileDialog.getSaveFileName(
            self, "导出 xlsx", default_name, "Excel 文件 (*.xlsx)")
        if not path:
            return
        self._btn_export.setEnabled(False)
        self._controller.export_xlsx(path, f)

    def _on_export_done(self, count):
        self._btn_export.setEnabled(True)
        show_info_bar(f"已导出 {count} 条记录", "success",
                      title="导出成功", parent=self, duration=3000)

    def _on_export_error(self, msg):
        self._btn_export.setEnabled(True)
        show_info_bar(msg, "error", title="导出失败", parent=self, duration=4000)

    def _on_import(self):
        if self._controller.is_running("_import_worker"):
            return
        path, _sel = QFileDialog.getOpenFileName(
            self, "选择售后汇总 Excel", "", "Excel 文件 (*.xlsx)")
        if not path:
            return
        # 第一步：后台解析（不写库），成功后弹预览确认
        self._btn_import.setEnabled(False)
        self._preview_path = path
        self._controller.parse_excel(path)

    def _on_import_preview_ready(self, result):
        """解析完成：弹预览对话框，确认后真正写库"""
        self._btn_import.setEnabled(True)
        excel_headers, rows = result
        dlg = ImportPreviewDialog(excel_headers, rows, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._btn_import.setEnabled(False)
        self._controller.commit_import(self._preview_path)

    def _on_import_done(self, count):
        self._btn_import.setEnabled(True)
        show_info_bar(f"已导入 {count} 条历史记录", "success",
                      title="导入成功", parent=self, duration=3000)
        self._cycles_loaded = False
        self._load_cycles_then_data()

    def _on_import_error(self, msg):
        self._btn_import.setEnabled(True)
        show_info_bar(msg, "error", title="导入失败", parent=self, duration=4000)
