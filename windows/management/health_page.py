# -*- coding: utf-8 -*-
"""health_page 模块（从 windows/management_panel.py 拆出，逻辑未改动）"""

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
from windows.management.moyu_widgets import (Game2048Widget, SnakeWidget,
                                                  MoyuReaderWidget)
from windows.management.image_viewer import is_image_file

logger = logging.getLogger(__name__)

from windows.management.common import *  # noqa: F401,F403

# ==================== 健康度趋势看板（C3） ====================

# 折线序列配色：错误率=警示红、操作率=信息蓝、精度=琥珀金（虚线）
_TREND_SERIES = (
    ("error_rate", "错误率", QColor(SEMANTIC["danger"]), False),
    ("operation_rate", "操作率", QColor(SEMANTIC["info"]), False),
    ("accuracy_count", "精度", QColor(SEMANTIC["warning"]), True),
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
        """绘制网格/Y 刻度/X 日期/图例/三条折线（None 断点分段）与悬停参考线"""
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
        """离开画布：清悬停高亮并隐藏 tooltip"""
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
        icon.setStyleSheet(f"color: {SEMANTIC['danger']}; font-size: 15px;")
        lay.addWidget(icon)
        avg = max(float(info.get("avg_rate") or 0), 0.001)
        times = float(info.get("today_rate") or 0) / avg
        txt = QLabel(
            f"{info.get('device_code', '')} · {info.get('club_name', '')} · "
            f"球桌 {info.get('table_id', '')} —— 今日错误率 "
            f"{float(info.get('today_rate') or 0):.1f}%，近 "
            f"{info.get('hist_days', 0)} 日均值 "
            f"{float(info.get('avg_rate') or 0):.1f}%（{times:.1f}×）", self)
        txt.setStyleSheet(f"color: {SEMANTIC['danger']};")
        lay.addWidget(txt, 1)
        jump = QLabel("查看设备 →", self)
        jump.setStyleSheet(f"color: {SEMANTIC['danger']}; font-weight: 600;")
        lay.addWidget(jump)

    def mouseReleaseEvent(self, event):
        """整行左键点击 → 跳转设备页搜索该设备"""
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
        """首次进入才构建 UI（懒加载）并初始化各查询 worker 引用"""
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
        """搭建预警区 + 趋势折线 + 排行三张卡片"""
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
        lbl_head.setStyleSheet(f"font-weight: 600; color: {SEMANTIC['danger']};")
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
        """当前启用的设备数据源（kd/xqzg）"""
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
        worker.result_ready.connect(on_ok)
        worker.error.connect(
            lambda msg: logger.warning("趋势页查询失败: %s", msg))
        worker.start()

    def _refresh_all(self):
        """手动刷新：预警 + 排行一起重查"""
        self._load_alerts()
        self._load_ranking()

    # ---------- 预警区 ----------

    def _load_alerts(self):
        """异步查错误率突增预警（仅 kd 数据源）"""
        if self._active_source() != "kd":
            self._lbl_alert_empty.setText("趋势看板仅支持 kd 数据源（当前为 xqzg）")
            self._lbl_alert_empty.show()
            self._clear_alert_rows()
            return
        self._run_query("_alerts_worker", table_db.query_kd_alerts, (7,),
                        self._on_alerts)

    def _on_alerts(self, alerts):
        """预警结果回写：无预警显示平稳提示，有则逐条插入可点击条目"""
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
        """清空预警条目（末尾 stretch 保留）"""
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
        """候选设备回写：填充下拉并默认加载首个设备的趋势"""
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
        """异步查指定设备近 30 天趋势并绘到折线图"""
        if not device_code:
            return
        self._lbl_trend_title.setText(f"{device_code} · 近 30 天趋势")
        self._run_query("_trend_worker", table_db.query_kd_trend,
                        (device_code, 30), self._chart.set_data)

    # ---------- TOP N 排行 ----------

    def _load_ranking(self):
        """按 TOP 数与排序字段异步查排行（仅 kd）"""
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
        """排行结果 → 表格：状态/错误率着色，设备码存 itemData 供双击跳转"""
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
        """排行双击 → 跳转设备页查看该设备"""
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
        """搭建标题/阈值提示 + 告警表格（首列勾选框）+ 已处理按钮"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = TitleLabel("健康度异常告警", self)
        header.addWidget(title)
        header.addStretch(1)
        self._btn_sync = PushButton(FluentIcon.SYNC, "手动同步", self)
        self._btn_sync.setToolTip(
            "立即拉取最新健康度数据；MySQL 模式下可见其他用户标记的「已处理」状态")
        self._btn_sync.clicked.connect(self._on_manual_sync)
        header.addWidget(self._btn_sync)
        self._lbl_sync = CaptionLabel("正在获取数据", self)
        header.addWidget(self._lbl_sync)
        layout.addLayout(header)

        hint = CaptionLabel(
            "基准 4000；>4000~5000 为健康度异常；"
            ">5000 为严重异常。"
            "勾选条目后点「已处理」，health 未变化则不再展示；"
            "使用服务器 MySQL 时，他人标记的已处理在同步后自动对齐", self)
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
            color = QColor(SEMANTIC["danger"]) if severe else QColor(SEMANTIC["warning"])
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
        """逐行产出首列勾选框（跳过被其他控件占用的行）"""
        for row in range(self._table.rowCount()):
            cb = self._table.cellWidget(row, 0)
            if isinstance(cb, CheckBox):
                yield cb

    def _update_resolved_enabled(self):
        """有勾选条目时启用「已处理」按钮"""
        self._btn_resolved.setEnabled(
            any(cb.isChecked() for cb in self._iter_checkboxes()))

    # ---------- 数据获取 ----------

    def _on_manual_sync(self):
        """手动同步：立即拉取最新数据（进行中不重复发起）"""
        if self._fetch_worker is not None and self._fetch_worker.isRunning():
            self._lbl_sync.setText("同步进行中…")
            return
        self._btn_sync.setEnabled(False)
        self._lbl_sync.setText("正在手动同步…")
        self._fetch_health_data()

    def _fetch_health_data(self):
        """全量拉取球桌数据（含 health 字段）并同步告警表"""
        if self._fetch_worker is not None and self._fetch_worker.isRunning():
            return
        self._fetch_worker = TableFetchWorker(self)
        self._fetch_worker.result_ready.connect(self._on_fetch_ok)
        self._fetch_worker.error.connect(self._on_fetch_fail)
        self._fetch_worker.start()

    def _on_fetch_ok(self, rows):
        """拉取完成：同步告警表后刷新展示（失败仅提示不弹窗）"""
        self._btn_sync.setEnabled(True)
        try:
            count = table_db.sync_health_alerts(rows)
        except Exception as e:
            self._lbl_sync.setText(f"同步失败: {e}")
            return
        now = QDateTime.currentDateTime().toString("HH:mm:ss")
        self._lbl_sync.setText(f"数据获取于 {now} · {count} 条异常")
        self._refresh_display()

    def _on_fetch_fail(self, msg):
        """拉取失败：状态栏提示错误首行"""
        self._btn_sync.setEnabled(True)
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
