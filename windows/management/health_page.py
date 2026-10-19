# -*- coding: utf-8 -*-
"""健康度页面：数据源健康度总览、趋势看板与设备排障入口

页面数据全部来自本地 SQLite 的 health 快照表，一次刷新要扫全表，因此读写在
后台 worker 里进行，UI 只负责展示与筛选。趋势与排行依赖 kd 数据源的日期分区，
其它数据源没有历史分区，进入趋势页只会看到空图——这是数据本身的限制，不是缺陷。
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
import time
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
                                  LoginTestWorker, HealthUpdateWorker,
                                  get_active_api_source, CATEGORY_DIRS)
from workers.aftersale_worker import AftersaleDBWorker
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

# stale 行底色：浅色模式淡黄、深色模式暗黄。两个值写死而不是走 SEMANTIC，
# 是因为「警示底色」需要低饱和才不刺眼，语义色板里没有这一档。
# 配色与售后面板「重要」行保持一致：同一个用户在两个页面看到同一种黄，
# 才会把它读成同一类信号。
_STALE_BG_LIGHT = QColor(255, 243, 205)
_STALE_BG_DARK = QColor(64, 57, 28)

from windows.management.common import *  # noqa: F401,F403

# ==================== 健康度趋势看板（C3） ====================

# 折线序列配色：错误率=警示红、操作率=信息蓝、精度=琥珀金（虚线）
_TREND_SERIES = (
    ("error_rate", "错误率", QColor(SEMANTIC["danger"]), False),
    ("operation_rate", "操作率", QColor(SEMANTIC["info"]), False),
    ("accuracy_count", "精度", QColor(SEMANTIC["warning"]), True),
)


class _TrendChart(QWidget):
    """零依赖自绘折线图：三条指标共用一套坐标轴

    不用 matplotlib 是为了免掉一个几十 MB 的依赖，只为画三条线不值得。
    三个指标量纲不同（两个百分比、一个计数），共用 vmax 归一化后只比较
    走势不比较绝对值，所以 Y 轴刻度只做参考。
    缺失日期留 None 而不是补 0：补 0 会把「没采集」画成「指标归零」，
    两类问题在图上长得一模一样，排障时就分不清了。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        # _hover 是「当前悬停的数据点下标」，-1 表示没有悬停。位置信息
        # 只在重绘时由 _pts_x 提供，所以两份状态必须同步更新。
        self._hover = -1
        self._pts_x = []
        self.setMinimumHeight(210)
        self.setMouseTracking(True)

    def set_data(self, rows):
        """换成新数据并立刻重绘，同时清掉悬停高亮

        高亮必须清掉：数据换了以后 _hover 指的仍是旧数据的下标，
        不清就会残留一条指向无关日期的参考线。这里先拷贝一份 rows，
        调用方的列表随后被复用或清空都不会影响已绘制的数据。
        """
        self._rows = list(rows or [])
        self._hover = -1
        self.update()

    def paintEvent(self, event):
        """按「底图 → 折线 → 高亮」的层次绘制整个图表

        Y 轴上限取所有值的 1.15 倍，给最高点留出头顶空间；正好取最大值时
        峰值会贴着上边框画，看起来像被裁掉了。
        单点数据没有横向跨度，x_at 退化成居中画一条，否则会除以 (n-1)=0。
        """
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
        # 1.0 兜底：全是 0 的数据会让 vmax=0，随后每处除法都会炸。
        # 乘 1.15 是给峰值留头顶空间，贴边绘制会被误读成数据被截断。
        vmax = max([float(r.get(k) or 0) for r in self._rows
                    for k, _, _, _ in _TREND_SERIES] + [1.0]) * 1.15

        def x_at(i):
            return plot.left() + (plot.width() * i / (n - 1) if n > 1
                                  else plot.width() / 2)

        # clamp 到 0：历史数据里存在负的错误率（分母修复前写入的快照），
        # 不夹紧就会画到绘图区外面去，压到 X 轴标签上。
        def y_at(v):
            return plot.bottom() - plot.height() * max(0.0, float(v)) / vmax

        self._pts_x = [x_at(i) for i in range(n)]
        # 网格线 + Y 刻度（5 档）。最后一档用实线并落在顶边，
        # 是给眼睛一个「图被画满到这里」的边界，其余用点线避免抢折线。
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
        # X 轴标签最多 8 个：日期标签宽约 48px，画多了在窄窗口里会糊成一片。
        # 取 step 而不是固定 8 个，是为了让标签均匀分布在整条时间轴上。
        step = max(1, math.ceil(n / 8))
        for i in range(0, n, step):
            date = str(self._rows[i].get("file_path") or "")
            p.setPen(QPen(faint_c))
            p.drawText(QRectF(self._pts_x[i] - 24, plot.bottom() + 4, 48, 16),
                       Qt.AlignmentFlag.AlignCenter, date[-5:])
        # 图例（顶部横排）。x 位置按已画文字的实际宽度累加，
        # 写死间距会在中英文混排时重叠——「精度」比「error」窄很多。
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
        # 折线遇到 None 就断开：把缺失日期连成直线会让用户以为那几天
        # 数据正常。断点分段画，缺口在图上直接可见。
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
        """按横向距离取最近的数据点并弹出该日期的三指标提示

        x 轴是时间轴而不是等距分类轴，只能按像素距离取最近点；
        只有真正换了点才 update，否则每一次鼠标移动都会触发整图重绘。
        """
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
    """一条错误率突增预警，整行可点，点击后跳到设备页并带上搜索词

    做成可点行而不是只在末尾放按钮：预警的价值在于「看到就能立刻去查」，
    多一次精准点击就多一次放弃的可能。
    """

    def __init__(self, info: dict, on_jump, parent=None):
        super().__init__(parent)
        self._info = info
        self._on_jump = on_jump
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"数据日期 {info.get('file_path', '')}，点击查看该设备")
        # 数字部分（错误率、均值、倍数）全部在这一行里拼成一段文本：
        # 只有一个 QLabel 才能让整行随窗口宽度自然省略，多控件拼排会互相挤压。
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(10)
        icon = QLabel("⚠", self)
        icon.setStyleSheet(f"color: {SEMANTIC['danger']}; font-size: 15px;")
        lay.addWidget(icon)
        # 分母夹到 0.001 而不是 0：历史均值可以是 0（那几天一次错都没出），
        # 直接除会抛 ZeroDivisionError，让整页预警整块渲染失败。
        # 夹紧后倍数会显得很大，但这正是「从零突增」应有的读法。
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
        """整行左键点击即跳转，不区分点在哪一段文字上

        只认左键：右键会走 CardWidget 的上下文菜单路径，拦截掉会破坏
        框架自带的菜单行为。
        """
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_jump(self._info)
        super().mouseReleaseEvent(event)


class TrendPage(QWidget):
    """健康度趋势看板：突增预警 + 单设备折线 + TOP N 排行

    三块内容按「先看有没有事、再看是谁、最后看趋势」的排障顺序自上而下排：
    预警回答「今天要不要管」，排行回答「先管谁」，折线回答「这台机器怎么了」。
    所有查询走 _DBQueryWorker，主线程只贴数据；页面懒构建，因为多数用户
    从不进入这一页，启动时白建一张图与四个 worker 是纯浪费。
    只有 kd 数据源有按日分区，其它源进入后预警与折线都是空的。
    """

    # 排行表列定义：(字段名, 表头, 列宽)。宽度的取舍是「球房名最长、
    # 数值列刚好放得下三位小数」，全部自适应反而会让球房名被压成省略号。
    _RANK_COLUMNS = [
        ("rank", "排名", 46), ("club_name", "球房", 150), ("table_id", "球桌", 70),
        ("status", "状态", 60), ("error_rate", "错误率", 80),
        ("operation_rate", "操作率", 80), ("accuracy_count", "精度", 60),
        ("already_count", "问题", 60),
    ]
    # 排序下拉的中文文案到查询字段的映射。文案与字段名分开维护，
    # 是为了让 SQL 层只认字段名，界面文案以后再改也不牵动查询。
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
        """首次进入时补齐全部状态、构建 UI 并发起首轮查询

        所有成员都在这里一次建好，而不是散在 __init__ 与各处 if 判断里：
        少了任何一个，后续槽函数就会撞上 AttributeError。
        首轮查询放在最后，此时控件已经能接收结果，早一步发起只会丢数据。
        """
        self._alerts_worker = None   # 预警查询
        self._cand_worker = None     # 设备候选搜索
        self._trend_worker = None    # 趋势序列查询
        self._rank_worker = None     # 排行查询
        self._candidates = []        # 当前匹配的候选设备行
        self._last_refresh = None    # 上次预警/排行刷新时间
        # 搜索防抖 300ms，与球桌页、设备页保持同一节奏：用户对这个延迟
        # 已经有肌肉记忆，某一页突然变成即时响应反而会觉得卡顿。
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
        # 三张卡片按排障顺序自上而下，末尾的 stretch 负责把内容顶到上方：
        # 窗口被拉高时空白留在底部，而不是把三块均匀撑开。
        title = TitleLabel("健康度趋势", self)
        root.addWidget(title)

        # ---------- 预警区（置顶红色提示，点击跳转设备页） ----------
        alert_card = CardWidget(self)
        a_lay = QVBoxLayout(alert_card)
        a_lay.setContentsMargins(14, 10, 14, 12)
        a_lay.setSpacing(6)
        head = QHBoxLayout()
        # 判据直接写在标题里：预警阈值是个容易被质疑的数字，
        # 摆在用户眼前才能省掉「多少算突增」的来回解释。
        lbl_head = QLabel("⚠ 异常突增预警（今日错误率 > 近 7 日均值×2）", alert_card)
        lbl_head.setStyleSheet(f"font-weight: 600; color: {SEMANTIC['danger']};")
        head.addWidget(lbl_head)
        head.addStretch(1)
        self._btn_alert_refresh = ToolButton(FluentIcon.SYNC, alert_card)
        self._btn_alert_refresh.setToolTip("刷新预警与排行")
        self._btn_alert_refresh.clicked.connect(self._refresh_all)
        head.addWidget(self._btn_alert_refresh)
        a_lay.addLayout(head)
        # 空状态标签与条目区共存：有预警时隐藏，没预警时给一句明确的话，
        # 这块区域永远不留纯空白。
        self._lbl_alert_empty = QLabel("暂无突增预警", alert_card)
        self._lbl_alert_empty.setStyleSheet("color: #8a919b;")
        a_lay.addWidget(self._lbl_alert_empty)
        # 预警区固定 110px 高：条目数不定，留一块固定高度让下方折线图不会被
        # 预警挤走。真有多条时在这个区域内滚动，而不是让整页跟着变长。
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
        # 搜索框只负责筛候选设备、不负责画图：真正的曲线来自按设备码的
        # 跨日期查询，搜索词本身不是查询条件。
        self._search_edit.textChanged.connect(self._on_search_input)
        bar.addWidget(self._search_edit)
        # 下拉框里存的是设备码（addItem 的第二参数），显示文本带球房与球桌，
        # 是为了让同型号多台设备能靠位置区分开。
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
        # TOP 只给 10/20/50：再大就要扫更多行，而超出屏幕范围的部分
        # 用户也不会真的往下翻，只是白白增加查询时间。
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
        # 双击而不是单击跳转：单击要用来选中行、配合复制，跳转留给双击，
        # 避免用户只想看一眼数据就被带走。
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
        """首次显示时构建 UI；之后按冷却间隔决定要不要重查

        重查的意义在于数据可能刚被同步任务更新过，用户切回来应当看到新数字；
        但每次切换都扫全表太贵，所以用一个 60 秒的冷却挡住频繁来回切。
        非 kd 数据源直接返回：这一页对它们本来就是空的，重查纯属白跑。
        """
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
        """读当前启用的设备数据源（kd 或 xqzg）

        每次现查而不缓存：数据源可以在设置页被切换，缓存会让本页拿着旧值
        去查一条已经没有分区的表。
        """
        return get_active_api_source()

    def _run_query(self, attr, func, args, on_ok):
        """所有查询共用的异步入口：先掐掉同类旧任务，再起新 worker

        掐旧任务是为了防止过期的慢查询在用户改完条件后回来覆盖新结果——
        连点两次排序下拉时，先发的那次可能后到。
        断开信号而不是 abort 线程：SQLite 查询无法安全中断，只能让它跑完
        再把结果丢掉。worker 挂在 self 上，面板关闭时统一清理。
        """
        old = getattr(self, attr, None)
        if old is not None and old.isRunning():
            try:
                # PySide6 不支持无参 disconnect()，只能指定接收者断开全部信号；
                # 旧任务可能已被销毁，这里捕获运行时异常而不是先判断存在。
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
        """手动刷新按钮：预警与排行一起重查

        折线不跟着重查：图上的设备是用户特意选的，刷新按钮的本意是
        看「今天有没有新情况」，把用户正在看的设备换掉是帮倒忙。
        """
        self._load_alerts()
        self._load_ranking()

    # ---------- 预警区 ----------

    def _load_alerts(self):
        """异步查近 7 日的错误率突增预警，只对 kd 数据源有效

        非 kd 源在这里直接给出说明文字，而不是发一个注定为空的查询：
        空图配空表格，用户会以为功能坏了。
        """
        if self._active_source() != "kd":
            self._lbl_alert_empty.setText("趋势看板仅支持 kd 数据源（当前为 xqzg）")
            self._lbl_alert_empty.show()
            self._clear_alert_rows()
            return
        self._run_query("_alerts_worker", table_db.query_kd_alerts, (7,),
                        self._on_alerts)

    def _on_alerts(self, alerts):
        """回写预警结果：空结果也要给一句「平稳」，而不是留白

        留白会被读成「还没加载完」，用户会反复点刷新。插入位置是 count()-1，
        因为布局末尾有一个 stretch 负责把条目顶到上方，插在它后面就沉底了。
        """
        self._last_refresh = datetime.now()
        self._clear_alert_rows()
        if not alerts:
            self._lbl_alert_empty.setText("暂无突增预警 — 各设备错误率平稳")
            self._lbl_alert_empty.show()
            return
        self._lbl_alert_empty.hide()
        lay = self._alert_body.layout()
        # 每条预警占用一个可点 CardWidget，数量由查询结果决定，
        # 所以这里不预留位置，来几条插几条。
        for info in alerts:
            lay.insertWidget(lay.count() - 1,
                             _AlertRow(info, self._jump_to_device, self._alert_body))

    def _clear_alert_rows(self):
        """删掉全部预警条目，只保留末尾的 stretch

        stretch 一旦被一起删掉，下一次插入的卡片会被垂直居中撑开，
        整块区域看起来忽高忽低。deleteLater 而不是 removeWidget：
        条目在信号槽里被触发删除，立刻 delete 会踩到正在执行的调用栈。
        """
        lay = self._alert_body.layout()
        while lay.count() > 1:  # 末尾 stretch 保留
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _jump_to_device(self, info):
        """点预警跳到设备页，并把设备码与日期一起带过去

        两种宿主两种路由：独立面板用 FluentWindow.switchTo，主窗口的单窗口
        导航用 switch_to_page。不写成 try/except 试探，是因为走错分支会
        把整页重建一遍，代价比多一次 getattr 大得多。
        日期也要传：设备页默认停在最新分区，而预警说的可能是前几天的事。
        """
        win = self.window()
        device_page = getattr(win, "device_page", None)
        if device_page is None:
            return
        # 宿主兼容：独立面板走 FluentWindow.switchTo；主窗口单窗口导航走
        # switch_to_page 路由（设备页在 ManagementHub 的 Pivot 内）
        router = getattr(win, "switch_to_page", None)
        if router is not None:
            router(device_page)
        else:
            win.switchTo(device_page)
        device_page.focus_search(str(info.get("device_code") or ""),
                                 str(info.get("file_path") or ""))

    # ---------- 设备趋势 ----------

    def _on_search_input(self, _=""):
        """搜索防抖入口：重启 300ms 计时器

        每敲一个字都查库的话，输入「ABC」会连续起三次设备查询，
        而前两次的结果注定被丢弃。单次触发由 setSingleShot 保证。
        """
        self._search_timer.start()

    def _do_search_candidates(self):
        """防抖到期后按关键词查候选设备

        空关键词不清库而直接清空下拉：候选列表的本意是缩小范围，
        没有任何过滤条件时把前 30 台设备倒进下拉，等于逼用户自己翻。
        """
        kw = self._search_edit.text().strip()
        if not kw:
            self._candidates = []
            self._device_combo.clear()
            return
        self._run_query("_cand_worker", self._query_candidates, (kw,),
                        self._on_candidates)

    @staticmethod
    def _query_candidates(kw):
        """在最新分区里按关键词找候选设备

        只搜最新分区：趋势图按设备码跨日期查询，候选列表混入历史分区里
        已经下线的设备，用户选中后只会看到一张空图。
        取第 1 页且只要 30 条——这是给下拉框用的，再多也翻不过来。
        """
        dates = table_db.get_kd_dates()
        latest = dates[0] if dates else ""
        if not latest:
            return []
        _, rows = table_db.query_kd_page(1, 30, kw, latest)
        return [r for r in rows if str(r.get("device_code") or "")]

    def _on_candidates(self, rows):
        """把候选设备填进下拉框，并顺手加载第一个设备的趋势

        填充期间 blockSignals：每次 addItem 都会触发 currentIndexChanged，
        不挡的话会连发几十次 _load_trend，把查询线程和数据库一起打满。
        默认选第一个是为了「搜完就能看到图」，少一次点击。
        """
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
        """下拉选中设备后加载该设备近 30 天趋势

        取 currentData 而不是解析显示文本：文本里混着球房与球桌，
        按分隔符切分会随文案改版就失效。
        """
        code = self._device_combo.currentData()
        if code:
            self._load_trend(str(code))

    def _load_trend(self, device_code):
        """异步查指定设备的近 30 天趋势并交给折线图

        30 天是写死的窗口：更长的区间在 8 个 X 轴标签里会挤在一起，
        而趋势页要回答的是「最近有没有变坏」，不需要季度级跨度。
        标题先更新再查询，这样即使查询失败，用户也知道图上应是哪台设备。
        """
        if not device_code:
            return
        self._lbl_trend_title.setText(f"{device_code} · 近 30 天趋势")
        self._run_query("_trend_worker", table_db.query_kd_trend,
                        (device_code, 30), self._chart.set_data)

    # ---------- TOP N 排行 ----------

    def _load_ranking(self):
        """按 TOP 数与排序字段异步查排行

        非 kd 源直接返回：排行榜依赖 kd 的按日分区表，别的源连表都没有。
        排序字段从下拉文案经映射表取，取不到时退回错误率，
        保证后端永远拿到一个合法列名，而不是界面上的一句中文。
        """
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
        """把排行结果铺进表格，非零错误率与异常状态分别上色

        错误率为 0 的行不上色：全表标红等于没标，只有真正有错的行才该跳出来。
        设备码塞进第 0 列的 UserRole，双击时不靠解析文本反查设备，文本随时
        可能被改格式，UserRole 不会。
        """
        rows = result.get("rows", [])
        self._lbl_rank_date.setText(f"数据日期 {result.get('date') or '无'}")
        t = self._rank_table
        t.setRowCount(len(rows))
        for i, r in enumerate(rows):
            # 状态码是接口原样存的，可能带空白或脏字符，先 strip 再查表；
            # 查不到就显示「未知」而不是把原始码抛给用户。
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
                # 状态列按语义色上色（在线绿、离线灰），颜色查不到就保持默认，
                # 未知状态不强行染成红色：那是「没数据」而不是「出故障」。
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
        """排行双击后带着设备码跳到设备页

        设备码为空时什么都不做：跳过去也是一张空页，
        不如留在原地让用户自己判断。
        """
        item = self._rank_table.item(row, 0)
        code = item.data(Qt.ItemDataRole.UserRole) if item else ""
        if code:
            self._jump_to_device({"device_code": code})

class _HealthCopyTable(TextDragSelectTableMixin, TableWidget):
    """支持鼠标拖选文本复制的告警表格（能力见 common.TextDragSelectTableMixin）

    QTableWidget 默认只能整格/整行选择，无法像文本编辑器那样拖动鼠标
    选中部分文字。本类经 TextDragSelectTableMixin 在单元格文本上实现
    字符级拖选：按住左键拖动即出蓝色选区高亮，松开后 Ctrl+C 或右键
    「复制选中」写入系统剪贴板；未拖选时 Ctrl+C / 右键复制整行
    （制表符分列，便于贴到表格）。
    """

    # 文本列（0 列是可勾选 item，不参与文本选择）
    _TEXT_COLS = (1, 2, 3, 4)


class HealthPage(QWidget):
    """健康度异常告警页：把远端接口的 health 字段变成一张待处理清单

    数据获取与展示刷新是两条独立节奏，故意不合并：拉取要打远端接口，成本高，
    30 分钟一次就够；重建表格只读本地库，但整表重建要几十毫秒，1 小时一次
    才不会在用户滚动时打断。页面载入时两条链路各强制跑一次，否则新开的
    程序要空等半小时才有数据。
    阈值判定以 4000 为基准：4000 是接口的默认值，等价于「没报过异常」，
    必须当空值处理，否则整张表会把所有正常设备都列进来。
    4000 到 5000 是健康度异常；超过 5000 是严重异常，需要立刻处理；
    超过 40 万的是接口脏数据，直接排除——那个量级只可能是字段错位。
    排序：① 空闲且严重异常；② 健康度异常（4000~5000）；③ 其余严重异常。
    勾选后点「已处理」会调 xqzg 的 update_health 把服务端健康度重置为 4000，
    并在本地记下处理当时的 health 值。之后刷新时若 health 没变，这条不再展示：
    网站侧接口每小时才刷新一次，48 小时宽限期内都在等数据源更新。
    health 变了但仍然异常就重新展示，降到 4000 以下则自动消失。
    超过 48 小时还是处理时的值，说明数据源根本没刷新（而不是没修好），
    此时重新展示、加 * 号并整行标黄，提示用户这条可能已经失效。
    黄色与售后面板「重要」行同款，两处的「需要人工关注」是同一个语义。
    """

    # 拉远端接口每 30 分钟一次：再密就是给接口添无谓压力，
    # 再疏则设备刚出问题到用户看到之间会拖过一个工单周期。
    _FETCH_INTERVAL_MS = 30 * 60 * 1000
    # 本地重建每 1 小时：只读本地库，但整表重建有成本，没必要跟拉取同频。
    _DISPLAY_INTERVAL_MS = 60 * 60 * 1000

    def __init__(self, parent=None):
        super().__init__(parent)
        # 四个 worker 各管一条链路：拉取、重置接口、本地标记、落库。
        # 分开持有是因为它们可以合法并发（拉取的同时可以标记上一批），
        # 但同类任务必须互斥，所以每处用之前都要判 isRunning。
        self._fetch_worker = None
        self._health_worker = None
        self._mark_worker = None
        self._sync_worker = None
        self._rows = []
        # 上次拉取/重建时刻，用 monotonic 秒而不是墙上时间：系统改时钟或
        # 跨夏令时后，wall clock 差值可能是负的，冷却判断会永久失效。
        # 0 表示从未执行，首次进入必然触发一次补拉。
        self._last_fetch_ts = 0.0
        self._last_display_ts = 0.0
        self._init_ui()

        # 两个定时器都挂在 self 上，随页面一起销毁；用 QTimer 而不是
        # QThread 轮询，是因为 Qt 的定时器本身就回到 GUI 线程，不需要跨线程同步。
        self._fetch_timer = QTimer(self)
        self._fetch_timer.setInterval(self._FETCH_INTERVAL_MS)
        self._fetch_timer.timeout.connect(self._on_fetch_timer_tick)
        self._fetch_timer.start()

        self._display_timer = QTimer(self)
        self._display_timer.setInterval(self._DISPLAY_INTERVAL_MS)
        self._display_timer.timeout.connect(self._on_display_timer_tick)
        self._display_timer.start()

        # 页面载入立即拉取一次（完成后自动刷新展示）
        self._fetch_health_data()

    def showEvent(self, event):
        """回到本页时按冷却间隔补拉与补刷

        隐藏期间定时器照跑但会主动跳过（见两个 timer_tick），这段时间欠下的
        更新由这里一次性补齐，否则用户切回去看到的是几小时前的数字。
        补拉条件与定时器用同一组间隔常量，两处口径必须一致，
        否则会出现「定时器已该跑、显示却没变」的错觉。
        """
        super().showEvent(event)
        now = time.monotonic()
        if (now - self._last_fetch_ts) * 1000 >= self._FETCH_INTERVAL_MS:
            self._fetch_health_data()
        if (now - self._last_display_ts) * 1000 >= self._DISPLAY_INTERVAL_MS:
            self._refresh_display()

    def _on_fetch_timer_tick(self):
        """定时拉取；页面不可见时直接跳过

        这是全程序唯一的周期性全量接口拉取加全表落库，不做可见性门控时，
        面板最小化一晚也会照跑几十轮，白白消耗接口额度与数据库写入。
        跳过是安全的：showEvent 会在用户切回时按需补齐。
        """
        if self.isVisible():
            self._fetch_health_data()

    def _on_display_timer_tick(self):
        """定时重建表格；页面不可见时跳过

        重建要操作全部行控件，对隐藏页面做这件事没有任何收益，
        只会在用户切回时表现为一次莫名的卡顿。
        """
        if self.isVisible():
            self._refresh_display()

    def _apply_smooth_mode(self):
        """按当前生效的平滑滚动设置刷新本页表格（管理面板设置页联动）"""
        apply_table_smooth_mode(self._table, panel="management")

    def _init_ui(self):
        """搭建「搜索同步行 + 告警表 + 已处理按钮」的页面骨架

        表格首列是勾选框而不是文本：批量处理只能靠勾选表达，
        用整行选中代替勾选的话，用户没法只处理其中几条。
        """
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        # 标题行右侧放搜索与手动同步：这两个动作都针对「当前这张告警表」，
        # 放在别处就得先切页再操作。
        header = QHBoxLayout()
        title = TitleLabel("健康度异常告警", self)
        header.addWidget(title)
        # 搜索框：本地过滤已加载告警（球桌号/球房/在线状态/健康度），即时生效
        self._search_edit = SearchLineEdit(self)
        self._search_edit.setPlaceholderText("搜索（球桌 / 球房 / 状态 / 健康度）")
        self._search_edit.setFixedWidth(240)
        self._search_edit.setToolTip(
            "在当前告警列表内即时过滤；清空恢复全部。"
            "表格文本可用鼠标拖选后 Ctrl+C 或右键复制")
        self._search_edit.textChanged.connect(self._apply_filter)
        header.addWidget(self._search_edit)
        header.addStretch(1)
        self._btn_sync = PushButton(FluentIcon.SYNC, "手动同步", self)
        self._btn_sync.setToolTip(
            "立即拉取最新健康度数据；MySQL 模式下可见其他用户标记的「已处理」状态")
        self._btn_sync.clicked.connect(self._on_manual_sync)
        header.addWidget(self._btn_sync)
        # 状态标签常驻而不是按需创建：拉取结果、搜索命中数、失败原因
        # 都复用它，避免多个提示控件抢同一行位置。
        self._lbl_sync = CaptionLabel("正在获取数据", self)
        header.addWidget(self._lbl_sync)
        layout.addLayout(header)

        # hint = CaptionLabel(
        #     "基准 4000；>4000~5000 为健康度异常；"
        #     ">5000 为严重异常。"
        #     "勾选条目后点「已处理」，调用 xqzg 接口将健康度重置为 4000；"
        #     "使用服务器 MySQL 时，他人标记的已处理在同步后自动对齐", self)
        # layout.addWidget(hint)

        self._table = _HealthCopyTable(self)
        # 平滑滚动是全局开关，这里按管理面板的口径接入：同一个开关
        # 在不同页面表现不一致，用户会直接报「滚动时好时坏」。
        apply_table_smooth_mode(self._table, panel="management")
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(
            ["", "球桌号", "球房名称", "在线状态", "健康度"])
        # 只有第 0 列固定宽度：勾选框是固定尺寸，给弹性宽度会左右晃；
        # 球房名用 Stretch 吃掉剩余空间，其余列按内容自适应。
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
        # 整行选中：用户是对「一台设备」做处理，不是对某个格子，
        # 按单元格选择会让勾选与选中两套状态互相干扰。
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        # 勾选列可勾选 item 点击翻转 → 联动「已处理」按钮可用性
        self._table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._table, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_resolved = PrimaryPushButton("已处理", self)
        self._btn_resolved.setEnabled(False)
        self._btn_resolved.setToolTip(
            "通过 xqzg 接口将勾选设备健康度重置为 4000，并标记已处理")
        self._btn_resolved.clicked.connect(self._on_resolved_clicked)
        btn_row.addWidget(self._btn_resolved)
        layout.addLayout(btn_row)

    # ---------- 展示 ----------

    def _refresh_display(self):
        """重读告警表并重建界面，供定时器与拉取完成两条路径共用

        查询必须在 worker 线程里做。MySQL 连接闪断时旧连接处于半开状态，
        同步查询会一直挂到 TCP 超时，在 GUI 线程里就是一次十秒级的整窗冻结。
        同名旧任务在途就先断开信号：告警以最后一次查询为准，
        让过期结果先回来渲染一遍再被覆盖，只会让用户看到表格闪一下。
        """
        self._last_display_ts = time.monotonic()
        old = getattr(self, "_alerts_worker", None)
        if old is not None and old.isRunning():
            try:
                old.disconnect(self)
            except (RuntimeError, TypeError):
                pass
        worker = _DBQueryWorker(table_db.query_health_alerts)
        self._alerts_worker = worker
        worker.result_ready.connect(self._on_alerts_loaded)
        worker.error.connect(
            lambda msg: self._lbl_sync.setText(f"查询失败: {msg}"))
        worker.start()

    def _on_alerts_loaded(self, rows):
        """告警查询完成：回写行数据并重建表格（Worker 线程零界面操作）"""
        self._rows = rows
        now = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        self._sync_base_text = f"{len(rows)} 条异常 · 展示刷新于 {now}"
        self._rebuild_table()

    @staticmethod
    def _match_row(r, kw) -> bool:
        """判断单条告警是否命中关键词，多字段子串匹配

        匹配面覆盖球桌号、球房、在线状态、健康度数值和分级文案：
        用户找一条告警时可能记得其中任意一个，只匹配球桌号会让「搜 严重异常」
        这类用法完全失效。关键词已由调用方转小写，这里逐字段 lower 后再比。
        """
        if not kw:
            return True
        h = float(r.get("health") or 0)
        severe = h > table_db.HEALTH_SEVERE
        fields = (str(r.get("name") or ""), str(r.get("roomName") or ""),
                  str(r.get("onlineStatusName") or ""), f"{h:.0f}",
                  "严重异常" if severe else "健康度异常")
        return any(kw in f.lower() for f in fields)

    def _apply_filter(self, _text=""):
        """按关键词切换行的显隐，不重建任何控件

        早期实现是每次输入都清空重填表格，于是每敲一个字都会：滚动条弹回顶部、
        已勾选的条目丢失、整表闪一下。改成只调 setRowHidden 之后，这几件事
        天然不会发生——控件没被销毁，滚动位置与勾选状态都还在控件上。
        只有状态真的变化才调用设置，是因为 setRowHidden 会触发重绘。
        """
        kw = self._search_edit.text().strip().lower()
        t = self._table
        shown = 0
        for row in range(t.rowCount()):
            r = self._rows[row] if row < len(self._rows) else None
            ok = r is not None and self._match_row(r, kw)
            if t.isRowHidden(row) == ok:  # 状态未变不重复设置，减少重绘
                t.setRowHidden(row, not ok)
            if ok:
                shown += 1
        # 状态栏文案由「基础计数」和「搜索命中数」拼成，基础部分存在实例上，
        # 这样清空搜索时能原样还原，不必再查一次库。
        base = getattr(self, "_sync_base_text", "")
        raw = self._search_edit.text().strip()
        if raw:
            self._lbl_sync.setText(f"{base} · 搜索「{raw}」命中 {shown} 条")
        else:
            self._lbl_sync.setText(base)

    def _rebuild_table(self):
        """从行数据整体重建表格，只在数据刷新时调用

        勾选列用可勾选的 QTableWidgetItem 加 delegate 原生绘制，而不是每行塞一个
        CheckBox 控件。差别在数量级：cellWidget 每行都要走一次 QSS 装配与布局，
        一千行实测主线程冻结约 293ms，真机叠加绘制与销毁更久，这正是点完
        「已处理」后整个程序卡一下的根因；纯 item 重建同样数据量约 10ms。
        blockSignals 也是性能需要：逐行 setItem 会触发 itemChanged，
        每次都去重算按钮可用性，行数一多就退化成平方级。
        重建末尾必须重套过滤，否则刷新一次当前搜索结果就被悄悄丢掉。
        """
        t = self._table
        t.blockSignals(True)
        try:
            t.setRowCount(0)
            for r in self._rows:
                h = float(r.get("health") or 0)
                # 阈值分级：>5000 严重异常（红），4000~5000 健康度异常（橙）
                severe = h > table_db.HEALTH_SEVERE
                color = QColor(SEMANTIC["danger"]) if severe else QColor(SEMANTIC["warning"])
                level = "严重异常" if severe else "健康度异常"
                # stale 行：已处理超过 48 小时、数据源仍未刷新。加 * 号并整行标黄，
                # 让人一眼分辨「还没修好」和「可能根本没人动过」。
                stale = bool(r.get("stale"))
                row_bg = (_STALE_BG_DARK if isDarkTheme() else _STALE_BG_LIGHT) \
                    if stale else None
                row = t.rowCount()
                t.insertRow(row)
                it0 = QTableWidgetItem()
                it0.setFlags(Qt.ItemFlag.ItemIsEnabled
                             | Qt.ItemFlag.ItemIsUserCheckable)
                it0.setData(Qt.ItemDataRole.UserRole, r.get("name") or "")
                it0.setCheckState(Qt.CheckState.Unchecked)
                it0.setToolTip("点击勾选后可标记已处理")
                        # 勾选框同样要上底色，否则整行标黄会在首列断开一截。
                if row_bg is not None:
                    it0.setBackground(row_bg)
                t.setItem(row, 0, it0)
                for col, key in ((1, "name"), (2, "roomName"),
                                 (3, "onlineStatusName")):
                    it = QTableWidgetItem(str(r.get(key) or ""))
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if row_bg is not None:
                        it.setBackground(row_bg)
                    t.setItem(row, col, it)
                it_h = QTableWidgetItem(f"{h:.0f}{' *' if stale else ''} · {level}")
                it_h.setFlags(it_h.flags() & ~Qt.ItemFlag.ItemIsEditable)
                it_h.setForeground(QBrush(color))
                if stale:
                    it_h.setBackground(row_bg)
                    it_h.setToolTip(
                        "已处理超过 48 小时，服务器数据仍未变化——"
                        "可能是数据源未刷新或重置未生效，可重新勾选处理")
                t.setItem(row, 4, it_h)
        finally:
            t.blockSignals(False)
        self._update_resolved_enabled()
        self._apply_filter()  # 重建后按当前搜索词套用显隐（含状态栏计数）

    def _iter_alert_items(self):
        """逐行产出首列的可勾选条目，跳过缺 item 的行

        生成器而不是返回列表：调用方只关心「有没有一条被勾上」，
        找到就能提前结束，没必要每次把全部行都收集一遍。
        """
        for row in range(self._table.rowCount()):
            it = self._table.item(row, 0)
            if it is not None and it.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                yield it

    def _on_item_changed(self, item):
        """勾选状态变化时刷新「已处理」按钮的可用性

        只在首列响应：重建表格时其他列也会触发 itemChanged，
        每列都重算一遍纯属浪费，而勾选状态只存在于首列。
        """
        if item.column() == 0:
            self._update_resolved_enabled()

    def _update_resolved_enabled(self):
        """按当前勾选情况开关「已处理」按钮

        没有勾选时按钮禁用而不是允许点击后弹提示：这个按钮会真的调用远端接口
        改设备状态，禁用比事后报错更能表达「先选几台」。
        """
        self._btn_resolved.setEnabled(
            any(it.checkState() == Qt.CheckState.Checked
                for it in self._iter_alert_items()))

    # ---------- 数据获取 ----------

    def _on_manual_sync(self):
        """手动同步：立刻拉一次远端数据

        正在跑时不再发起，只把状态文字换掉：重复发起会起两个 worker
        同时写同一张表，落库顺序无法保证。
        """
        if self._fetch_worker is not None and self._fetch_worker.isRunning():
            self._lbl_sync.setText("同步进行中…")
            return
        self._btn_sync.setEnabled(False)
        self._lbl_sync.setText("正在手动同步…")
        self._fetch_health_data()

    def _fetch_health_data(self):
        """全量拉取球桌数据并交给后台写库

        health 字段附在球桌列表接口里，所以健康度只能跟着全量拉取走，
        没有单独的精简接口可用；这也是它必须做可见性门控的原因。
        拉取时刻在这里就记下，失败也算跑过一轮，否则冷却判断会让失败后
        每次进入页面都重试，故障时反而放大请求量。
        """
        if self._fetch_worker is not None and self._fetch_worker.isRunning():
            return
        self._last_fetch_ts = time.monotonic()
        self._fetch_worker = TableFetchWorker(self)
        self._fetch_worker.result_ready.connect(self._on_fetch_ok)
        self._fetch_worker.error.connect(self._on_fetch_fail)
        self._fetch_worker.start()

    def _on_fetch_ok(self, rows):
        """拉取成功：把整批数据交给后台线程同步进告警表

        同步不留在 GUI 线程，是因为 MySQL 断连时写库会阻塞到超时。
        上一轮同步还在跑就跳过本轮：落库顺序无法保证，宁可丢一轮，
        下一轮拉取会带来完整快照。
        """
        self._btn_sync.setEnabled(True)
        if self._sync_worker is not None and self._sync_worker.isRunning():
            return  # 上一轮同步未完成，跳过（下一轮拉取再补）
        self._sync_worker = AftersaleDBWorker(
            table_db.sync_health_alerts, rows)
        self._sync_worker.result_ready.connect(self._on_sync_done)
        self._sync_worker.error.connect(self._on_sync_fail)
        self._sync_worker.start()

    def _on_sync_done(self, count):
        """落库完成：状态栏写明时刻与条数，再重建一次展示

        条数取自落库结果而不是界面行数，这样「0 条异常」也能明确显示出来，
        否则用户分不清是没异常还是没拉到。
        """
        # 只显示时分秒：日期在「今天」的语境下没有信息量，
        # 而状态栏宽度有限，多几位日期会把条数挤出去。
        now = QDateTime.currentDateTime().toString("HH:mm:ss")
        self._lbl_sync.setText(f"数据获取于 {now} · {count} 条异常")
        self._refresh_display()

    def _on_sync_fail(self, msg):
        """落库失败：仍先刷新展示，再报错

        写库失败不代表读不出旧数据，先把库里现有的告警刷出来，
        比让界面停在上一轮内容更像「还在工作」。
        错误只显示首行：pymysql 的多行堆栈塞进标签会把标题栏撑变形。
        """
        self._refresh_display()
        self._lbl_sync.setText(f"同步失败: {str(msg).split(chr(10))[0]}")

    def _on_fetch_fail(self, msg):
        """拉取失败：恢复按钮并只显示错误首行

        按钮必须恢复，否则一次网络抖动就把同步入口锁死到下次重启。
        """
        self._btn_sync.setEnabled(True)
        self._lbl_sync.setText(f"获取失败: {str(msg).split(chr(10))[0]}")

    # ---------- 处理交互 ----------

    def _on_resolved_clicked(self):
        """把勾选设备的服务端健康度重置为 4000（接口默认值）

        界面按球桌号勾选，而接口要的是设备码，所以要用告警行里的 device_code
        做一次映射；映射缺失时仍把空码发出去，让接口明确报错，
        好过在本地悄悄跳过、用户以为已经处理。
        确认框不能省：这是改动线上设备状态的不可逆操作，误点一次
        就得等网站侧下一个刷新周期才会恢复展示。
        两个 worker 互斥检查放在确认框之前，避免用户确认完才发现
        上一轮还没结束，白点一次。
        """
        # 勾选行里取球桌号；空名过滤掉，避免把一条空设备发给接口。
        names = [str(it.data(Qt.ItemDataRole.UserRole) or "")
                 for it in self._iter_alert_items()
                 if it.checkState() == Qt.CheckState.Checked]
        names = [n for n in names if n]
        if not names:
            return
        if self._health_worker is not None and self._health_worker.isRunning():
            show_info_bar("正在处理中，请稍候", "warning",
                          title="处理中", parent=self, duration=2000)
            return
        if self._mark_worker is not None and self._mark_worker.isRunning():
            show_info_bar("正在标记已处理，请稍候", "warning",
                          title="处理中", parent=self, duration=2000)
            return
        code_map = {str(r.get("name") or ""): str(r.get("device_code") or "")
                    for r in self._rows}
        pairs = [(n, code_map.get(n, "")) for n in names]
        if not MessageBox("重置健康度",
                          f"将对勾选的 {len(pairs)} 台设备调用 xqzg 接口，"
                          f"将健康度重置为 4000（默认值），确认处理？",
                          self.window()).exec():
            return
        self._btn_resolved.setEnabled(False)
        self._btn_resolved.setText("处理中…")
        self._health_worker = HealthUpdateWorker(pairs, self)
        self._health_worker.result_ready.connect(self._on_health_reset_ok)
        self._health_worker.error.connect(self._on_health_reset_fail)
        self._health_worker.start()

    def _on_health_reset_ok(self, ok_names, fails):
        """接口重置返回后，把成功项在本地标记为已处理

        标记走后台线程：MySQL 断连时 pymysql 抛出的 OperationalError 不会
        穿透到 Qt 主线程槽，留在主线程里就是按钮卡在「处理中…」且界面僵住。
        标记失败也必须走收尾流程，否则按钮再也回不到可用状态；
        后续 DB 操作会自动降级到本地库，业务不必中断。
        这里一律把 _health_worker 置空，成功与失败都不能留下一个"仍在跑"的
        假引用，否则下次点击会被自己的互斥检查挡回去。
        """
        self._health_worker = None
        self._btn_resolved.setText("已处理")
        if ok_names:
            self._mark_worker = AftersaleDBWorker(
                table_db.mark_health_alerts_resolved, ok_names)
            self._mark_worker.result_ready.connect(
                lambda _n: self._finish_health_reset(ok_names, fails))
            self._mark_worker.error.connect(
                lambda m: self._finish_health_reset(ok_names, fails, db_err=m))
            self._mark_worker.start()
        else:
            self._finish_health_reset([], fails)

    def _finish_health_reset(self, ok_names, fails, db_err=None):
        """标记结束后的统一收尾：刷新展示并按结果提示

        三种结果分开提示，因为用户接下来要做的事不同：只标记失败要去修数据库，
        部分失败要重试那几台，全部成功则可以离开本页。
        失败明细只列前五台，其余折成计数——弹窗太长会顶出屏幕。
        """
        # 先刷新再提示：刷新会把已处理的条目摘掉，用户看到的是处理后的状态，
        # 提示语只是补充说明失败细节。
        self._refresh_display()
        if db_err:
            show_info_bar(
                f"接口已重置 {len(ok_names)} 台，但本地标记失败（{db_err}）",
                "warning", title="已处理（标记失败）",
                parent=self, duration=6000)
        elif fails:
            detail = "；".join(f"{n}: {e}" for n, e in fails[:5])
            if len(fails) > 5:
                detail += f"；…等共 {len(fails)} 台失败"
            show_info_bar(f"成功 {len(ok_names)} 台；失败 {len(fails)} 台：{detail}",
                          "warning", title="部分重置失败",
                          parent=self, duration=6000)
        else:
            show_info_bar(f"已重置 {len(ok_names)} 台设备健康度为 4000",
                          "success", title="处理完成", parent=self, duration=2500)

    def _on_health_reset_fail(self, msg):
        """整体失败：恢复按钮、按勾选状态重算可用性并提示原因

        不重新勾选任何行：用户刚勾的那几台还得再点一次，
        但保留勾选状态，至少不用重新找。
        """
        self._health_worker = None
        self._btn_resolved.setText("已处理")
        self._update_resolved_enabled()
        show_info_bar(str(msg), "error", title="重置失败",
                      parent=self, duration=4000)
