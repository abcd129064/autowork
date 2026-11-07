# -*- coding: utf-8 -*-
"""页签4 统计汇总（稿 522-595）：周期切换 + 四卡 + 自绘堆叠柱/环图 + 成员汇总

周期口径：SegmentedWidget 的按日/按周/按月决定「本期」的窗口与柱的粒度
（按日=近 7 天逐日、按周=近 4 周逐周、按月=近 6 月逐月）。稿里只给了按日
态的一张截图（近 7 天柱 + 112.5h），另两档的窗口宽度没有标注，取「4 周 /
6 月」是让柱数与按日档的 7 根量级相当、又不至于密到读不出标签。

堆叠柱与环图全部 QPainter 自绘（稿 591 明令不引图表库）；柱的切片色 = 成员
语义色，与页签3 分支、页签1 头像同一套 member_color，跨页签才对得上号。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QDate, QRectF
from PySide6.QtGui import QClipboard, QColor, QPainter, QPen
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QHeaderView, QAbstractItemView, QTableWidgetItem,
                               QFileDialog, QApplication, QSizePolicy)
from qfluentwidgets import (BodyLabel, CaptionLabel, ComboBox, FluentIcon,
                            PrimaryPushButton, PushButton, SegmentedWidget,
                            StrongBodyLabel, TableWidget)

from core.design_tokens import SPACE, DEFAULT_GAP, TYPE_SCALE_PX, FONT_WEIGHTS
from core.ops_link_delegate import LINKS_ROLE
from core.utils import show_info_bar
from database import worklog_db
from windows.worklog.common import (CardBox, FlowToolbar, IconBlock,
                                    SummaryCard, category_color, fmt_hours,
                                    iso, member_color, px_font, tabular,
                                    text_color_tier, tier_color, track_hex,
                                    WorklogPage, accent_press, color_hex)
from windows.worklog.controller import WorklogController
from windows.worklog.delegate import (ROLE_AVATAR, install_worklog_delegate)

# 稿 524 的三档；routeKey 与 worklog_db 的周期词汇对齐，便于以后接 period_range
_PERIODS = (("day", "按日"), ("week", "按周"), ("month", "按月"))

# 稿 545-551 画了 7 根柱；周/月档取 4/6 根，柱宽量级与按日档相当
_BUCKET_COUNT = {"day": 7, "week": 4, "month": 6}

_COLUMNS = (
    ("成员", 150), ("任务数", 70), ("已完成", 70), ("工时(h)", 80),
    ("完成率", 70), ("平均进度", 80), ("个人报告", 120),
)
_ROW_H = 38


def period_buckets(key: str, today: QDate) -> list:
    """本期桶清单（升序）：[(标签, 起, 止)]，本期窗口 = 首桶起 ~ 末桶止

    桶边界在 UI 侧算而不是 SQL 侧：日历换算是纯逻辑，放数据层会逼着
    SQLite/MySQL 各写一套 strftime/DATE_FORMAT，两边还容易差一天。
    """
    out = []
    if key == "week":
        monday = today.addDays(-(today.dayOfWeek() - 1))
        for i in range(_BUCKET_COUNT[key] - 1, -1, -1):
            m = monday.addDays(-7 * i)
            out.append((m.toString("MM-dd"), iso(m), iso(m.addDays(6))))
    elif key == "month":
        first = QDate(today.year(), today.month(), 1)
        for i in range(_BUCKET_COUNT[key] - 1, -1, -1):
            m = first.addMonths(-i)
            out.append(("%d月" % m.month(), iso(m),
                        iso(m.addDays(m.daysInMonth() - 1))))
    else:
        for i in range(_BUCKET_COUNT[key] - 1, -1, -1):
            d = today.addDays(-i)
            out.append((d.toString("MM-dd"), iso(d), iso(d)))
    return out


class _StackedBars(QWidget):
    """按成员堆叠的工时柱（稿 544-552）：柱顶数值 + 底部标签 + 本期高亮

    稿的 CSS 只画了单色柱（``.bar-wrap i`` 一层），堆叠是 impl-note 592 的
    要求（「堆叠柱按成员语义色切片」），两者以 impl-note 为准。非高亮桶的
    切片压到 55% 不透明度：稿用 #93c7d1（主色冲淡）区分普通日与今日，
    堆叠态下冲淡整根柱会丢成员色，改成冲淡「非本期」这一维度。
    """

    _VALUE_H = 16      # 柱顶数值行（稿 .bv 11px + gap 5）
    _LABEL_H = 16      # 底部日期行（稿 .bl 11px）
    _BAR_W_RATIO = 0.7
    _BAR_W_MAX = 38    # 稿 .bar-wrap i 的 max-width:38px

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bars = []        # [(标签, 总量, [(QColor, 工时)])]
        self._hot = -1
        self.setFixedHeight(150)          # 稿 .bar-wrap 的 height:150px
        # 高度锁死、宽度吃满卡片：柱宽是按可用宽度均分的，不锁高会让卡片
        # 每次重排都跟着 sizeHint 抖一下
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)

    def setData(self, bars, hot=-1):
        self._bars = bars or []
        self._hot = hot
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._bars:
            p.setPen(tier_color(3))
            p.setFont(px_font(TYPE_SCALE_PX["base"]))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "本期还没有工时数据")
            return
        w, h = self.width(), self.height()
        top, bottom = self._VALUE_H, h - self._LABEL_H
        plot_h = bottom - top
        peak = max(b[1] for b in self._bars) or 1
        slot = w / len(self._bars)
        bar_w = min(self._BAR_W_MAX, slot * self._BAR_W_RATIO)
        value_pen = QPen(tier_color(2))
        label_pen = QPen(tier_color(3))
        for i, (label, total, slices) in enumerate(self._bars):
            cx = slot * i + slot / 2
            x = cx - bar_w / 2
            alpha = 1.0 if i == self._hot else 0.55
            y = bottom
            first_top = bottom - total / peak * plot_h
            for color, hours in slices:
                if hours <= 0:
                    continue
                seg_h = hours / peak * plot_h
                y -= seg_h
                c = QColor(color)
                c.setAlphaF(alpha)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(c)
                if abs(y - first_top) < 0.5:
                    # 只有最上面一段带圆角（稿 border-radius:5px 5px 0 0）：
                    # 先画四角都圆的，再用一块方角矩形把底部 5px 盖回来。
                    # 不走 QPainterPath 的并集 —— 默认奇偶填充会把重叠区掏空
                    p.drawRoundedRect(QRectF(x, y, bar_w, seg_h), 5, 5)
                    p.drawRect(QRectF(x, y + max(0.0, seg_h - 5), bar_w,
                                      min(5.0, seg_h)))
                else:
                    p.drawRect(QRectF(x, y, bar_w, seg_h))
            p.setPen(value_pen)
            p.setFont(tabular(px_font(TYPE_SCALE_PX["xs"])))
            # 稿 .bw 是 justify-content:flex-end 的列：数值贴在柱顶上方，
            # 不是钉在绘图区顶行（柱矮的时候顶行数字会离柱很远）
            vy = max(0.0, first_top - self._VALUE_H)
            p.drawText(QRectF(cx - slot / 2, vy, slot, self._VALUE_H),
                       Qt.AlignmentFlag.AlignCenter, fmt_hours(total))
            p.setPen(label_pen)
            p.drawText(QRectF(cx - slot / 2, bottom, slot, self._LABEL_H),
                       Qt.AlignmentFlag.AlignCenter, label)


class _Donut(QWidget):
    """分类占比环（稿 555-565）：130×130、环宽 20、中心总工时"""

    _SIZE = 130
    _RING = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self._slices = []      # [(分类名, 工时, QColor)]
        self._total = 0.0
        self.setFixedSize(self._SIZE, self._SIZE)

    def setData(self, slices, total):
        self._slices = slices or []
        self._total = total or 0.0
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # drawArc 的笔画以路径为中心线，内缩半个环宽后外径才正好占满 130
        half = self._RING / 2.0
        rect = QRectF(half, half, self._SIZE - self._RING,
                      self._SIZE - self._RING)
        pen = QPen(QColor(track_hex()), self._RING)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(rect, 0, 360 * 16)
        if self._total > 0:
            angle = 90 * 16          # 12 点起画，顺时针
            for _name, hours, color in self._slices:
                span = -int(round(hours / self._total * 360 * 16))
                pen.setColor(color)
                p.setPen(pen)
                p.drawArc(rect, angle, span)
                angle += span
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(tier_color(1))
        p.setFont(tabular(px_font(17, FONT_WEIGHTS["bold"])))
        p.drawText(QRectF(0, 47, self._SIZE, 22),
                   Qt.AlignmentFlag.AlignCenter, fmt_hours(self._total))
        p.setPen(tier_color(3))
        p.setFont(px_font(10))
        p.drawText(QRectF(0, 69, self._SIZE, 14),
                   Qt.AlignmentFlag.AlignCenter, "总工时 h")


class StatsPage(WorklogPage):
    """统计汇总页：本期窗口内的分布、占比与成员产出"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ctl = WorklogController(self)
        self._seq = 0
        self._totals = None
        self._stacked = None
        self._cats = None
        self._summary = None
        self._members = []
        self._loaded_once = False
        self._report_label = "本期文字报告"

        self._build_toolbar()
        self._build_cards()
        self._build_charts()
        self._build_table()

        self._lbl_state = BodyLabel("", self.body)
        self._lbl_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_state.setFont(px_font(TYPE_SCALE_PX["base"]))
        self._lbl_state.setTextColor(*text_color_tier(3))
        self._lbl_state.hide()
        self.lay.addWidget(self._lbl_state)
        self.lay.addStretch(1)
        self._connect()

    # ---------------- 布局 ----------------

    def _build_toolbar(self):
        """稿 523-529：周期分段 + 成员筛选，右端复制报告/导出"""
        bar = FlowToolbar(self)
        self.seg_period = SegmentedWidget(bar)
        for key, text in _PERIODS:
            self.seg_period.addItem(key, text)
        self.seg_period.setCurrentItem("day")
        self.seg_period.currentItemChanged.connect(lambda _k: self.refresh())
        bar.addWidget(self.seg_period)

        self.cbo_member = ComboBox(bar)
        self.cbo_member.setMinimumWidth(120)
        self.cbo_member.currentIndexChanged.connect(lambda _i: self.refresh())
        bar.addWidget(self.cbo_member)

        bar.addRight()
        self.btn_report = PrimaryPushButton(FluentIcon.COPY,
                                            "复制本期文字报告", bar)
        self.btn_report.clicked.connect(lambda _=False: self._on_copy_report())
        bar.addWidget(self.btn_report)
        self.btn_export = PushButton("导出 Excel", bar)
        self.btn_export.clicked.connect(lambda _=False: self._on_export())
        bar.addWidget(self.btn_export)
        self.lay.addWidget(bar)

    def _build_cards(self):
        """稿 531-540：四等分汇总卡，口径换成「本期」"""
        wrap = QWidget(self)
        grid = QGridLayout(wrap)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(SPACE[3])
        self.card_hours = SummaryCard(
            "本期总工时", lead=IconBlock(FluentIcon.STOP_WATCH, accent_press))
        self.card_tasks = SummaryCard(
            "本期任务数", lead=IconBlock(FluentIcon.QUICK_NOTE,
                                         color_hex("info")))
        self.card_rate = SummaryCard(
            "完成率", lead=IconBlock(FluentIcon.ACCEPT_MEDIUM,
                                     color_hex("success")))
        self.card_percap = SummaryCard(
            "人均工时", lead=IconBlock(FluentIcon.PEOPLE,
                                       color_hex("warning")))
        for i, card in enumerate((self.card_hours, self.card_tasks,
                                  self.card_rate, self.card_percap)):
            grid.addWidget(card, 0, i)
        self.lay.addWidget(wrap)

    def _build_charts(self):
        """稿 541-572：1.5fr:1fr 两卡，左堆叠柱右环图+图例"""
        wrap = QWidget(self)
        grid = QGridLayout(wrap)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(DEFAULT_GAP + 2)      # 稿 .charts 的 gap:14px
        grid.setColumnStretch(0, 3)           # 1.5fr : 1fr
        grid.setColumnStretch(1, 2)

        left = CardBox(parent=wrap)
        head = QHBoxLayout()
        head.setSpacing(SPACE[2])
        title = StrongBodyLabel("工时分布（按成员堆叠）", left)
        title.setFont(px_font(TYPE_SCALE_PX["md"], FONT_WEIGHTS["semibold"]))
        head.addWidget(title)
        head.addStretch(1)
        self._legend_host = QWidget(left)
        self._legend_lay = QHBoxLayout(self._legend_host)
        self._legend_lay.setContentsMargins(0, 0, 0, 0)
        self._legend_lay.setSpacing(SPACE[2])
        head.addWidget(self._legend_host)
        left.lay.addLayout(head)
        self.bars = _StackedBars(left)
        left.lay.addWidget(self.bars)
        grid.addWidget(left, 0, 0)

        right = CardBox(parent=wrap)
        rtitle = StrongBodyLabel("分类占比（按工时）", right)
        rtitle.setFont(px_font(TYPE_SCALE_PX["md"], FONT_WEIGHTS["semibold"]))
        right.lay.addWidget(rtitle)
        row = QHBoxLayout()
        row.setSpacing(SPACE[4])              # 稿 554 的 gap:16px
        self.donut = _Donut(right)
        row.addWidget(self.donut, 0, Qt.AlignmentFlag.AlignVCenter)
        self._donut_legend = QWidget(right)
        self._donut_legend_lay = QVBoxLayout(self._donut_legend)
        self._donut_legend_lay.setContentsMargins(0, 0, 0, 0)
        self._donut_legend_lay.setSpacing(SPACE[1])
        row.addWidget(self._donut_legend, 1)
        right.lay.addLayout(row)
        grid.addWidget(right, 0, 1)
        self.lay.addWidget(wrap)
        self._charts_wrap = wrap

    def _build_table(self):
        """稿 574-589：贴边成员汇总表，末列两条复制链接"""
        card = CardBox(padding=0, parent=self)
        self._table = TableWidget(card)
        self._table.setColumnCount(len(_COLUMNS))
        self._table.setHorizontalHeaderLabels([c[0] for c in _COLUMNS])
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setWordWrap(False)
        self._table.setShowGrid(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for i, (_name, w) in enumerate(_COLUMNS):
            self._table.setColumnWidth(i, w)
        # 成员列吃掉剩余宽度：稿 574 的表是通栏的，全定宽会留一大块空白
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        # 稿 576：只有「个人报告」表头右对齐（其余 th 一律 text-align:left）
        last = self._table.horizontalHeaderItem(len(_COLUMNS) - 1)
        last.setTextAlignment(Qt.AlignmentFlag.AlignRight
                              | Qt.AlignmentFlag.AlignVCenter)
        install_worklog_delegate(self._table, self._on_ops_link)
        card.lay.addWidget(self._table)
        self.lay.addWidget(card)
        self._table_card = card

    def _connect(self):
        ctl = self._ctl
        ctl.totals_ready.connect(self._on_totals)
        ctl.stacked_ready.connect(self._on_stacked)
        ctl.cats_ready.connect(self._on_cats)
        ctl.summary_ready.connect(self._on_summary)
        ctl.load_failed.connect(self._on_load_failed)
        ctl.options_ready.connect(self._on_options)
        ctl.options_failed.connect(
            lambda msg: show_info_bar(msg, "error", title="候选加载失败",
                                      parent=self, duration=4000))
        ctl.report_ready.connect(self._on_report_ready)
        ctl.report_failed.connect(
            lambda msg: show_info_bar(msg, "error", title="报告生成失败",
                                      parent=self, duration=4000))
        ctl.export_done.connect(self._on_export_done)
        ctl.export_failed.connect(
            lambda msg: show_info_bar(msg, "error", title="导出失败",
                                      parent=self, duration=4000))

    # ---------------- 取数 ----------------

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded_once:
            self._loaded_once = True
            self.initial_load()

    def initial_load(self):
        self._ctl.fetch_options()
        self.refresh()

    def detach_workers(self):
        self._ctl.stop_all()

    def on_options_changed(self):
        """成员被页签5 改动后由 Hub 广播：候选与统计都要重拉"""
        self._ctl.fetch_options()
        self.refresh()

    def _period_key(self) -> str:
        # currentRouteKey 而不是 currentItem：后者返回 SegmentedItem 控件对象
        return self.seg_period.currentRouteKey() or "day"

    def _window(self) -> tuple:
        buckets = period_buckets(self._period_key(), QDate.currentDate())
        return buckets, buckets[0][1], buckets[-1][2]

    def _member_id(self) -> int:
        return self.cbo_member.currentData() or 0

    def refresh(self):
        """稿 590 要求切换即重查：四族查询并发，seq 守卫丢弃过期帧"""
        self._seq += 1
        seq = self._seq
        self._totals = self._stacked = self._cats = self._summary = None
        _buckets, start, end = self._window()
        mid = self._member_id()
        self._set_state("加载中…")
        self._ctl.query_totals(seq, date_from=start, date_to=end,
                               member_id=mid)
        self._ctl.query_stacked(seq, start, end, mid)
        self._ctl.query_categories(seq, start, end, mid)
        self._ctl.query_member_summary(seq, start, end)

    def _set_state(self, text):
        self._lbl_state.setText(text)
        self._lbl_state.setVisible(bool(text))
        for w in (self._charts_wrap, self._table_card):
            w.setVisible(not text)

    def _on_totals(self, totals, seq):
        if seq != self._seq:
            return
        self._totals = totals or {}
        self._maybe_populate()

    def _on_stacked(self, rows, seq):
        if seq != self._seq:
            return
        self._stacked = rows or []
        self._maybe_populate()

    def _on_cats(self, rows, seq):
        if seq != self._seq:
            return
        self._cats = rows or []
        self._maybe_populate()

    def _on_summary(self, rows, seq):
        if seq != self._seq:
            return
        rows = rows or []
        # 汇总表在 SQL 侧没有成员维度（member_summary 只收日期区间），
        # 成员筛选在这里过滤行：每行的数字本就是按成员算好的，砍行不砍列
        mid = self._member_id()
        if mid:
            rows = [r for r in rows if int(r.get("member_id") or 0) == mid]
        self._summary = rows
        self._maybe_populate()

    def _on_load_failed(self, msg, seq):
        if seq != self._seq:
            return
        self._set_state("加载失败：%s" % msg)
        show_info_bar(msg, "error", title="加载失败", parent=self,
                      duration=4000)

    def _on_options(self, data):
        self._members = (data or {}).get("members") or []
        keep = self._member_id()
        self.cbo_member.blockSignals(True)
        self.cbo_member.clear()
        self.cbo_member.addItem("全部成员", userData=0)
        for m in self._members:
            if m.get("active"):
                self.cbo_member.addItem(m.get("name") or "",
                                        userData=int(m.get("id") or 0))
        idx = self.cbo_member.findData(keep)
        self.cbo_member.setCurrentIndex(idx if idx >= 0 else 0)
        self.cbo_member.blockSignals(False)

    def _maybe_populate(self):
        if None in (self._totals, self._stacked, self._cats, self._summary):
            return
        if not self._stacked and not self._cats:
            self._set_state("本期还没有留痕记录")
            self._fill_cards()
            return
        self._set_state("")
        self._fill_cards()
        self._fill_bars()
        self._fill_donut()
        self._fill_table()

    # ---------------- 填卡 / 填图 / 填表 ----------------

    def _fill_cards(self):
        t = self._totals or {}
        self.card_hours.setValue(fmt_hours(t.get("hours")), "h")
        self.card_tasks.setValue(t.get("count"), "项")
        self.card_rate.setValue(int(round(t.get("done_rate") or 0)), "%",
                                value_color=color_hex("success"))
        self.card_percap.setValue(fmt_hours(t.get("per_capita")), "h/人·日")

    def _fill_bars(self):
        buckets, _s, _e = self._window()
        today = iso(QDate.currentDate())
        bars, hot = [], -1
        for i, (label, start, end) in enumerate(buckets):
            per = {}
            for row in self._stacked:
                d = row.get("work_date")
                if not (start <= d <= end):
                    continue
                mid = int(row.get("member_id") or 0)
                per[mid] = per.get(mid, 0.0) + float(row.get("hours") or 0)
            slices = [(QColor(member_color(mid)), hours)
                      for mid, hours in sorted(per.items())]
            bars.append((label, round(sum(per.values()), 1), slices))
            if start <= today <= end:
                hot = i
        self.bars.setData(bars, hot)
        self._fill_bar_legend()

    def _fill_bar_legend(self):
        """柱图右上角的成员色签（稿 543）：只列本期真有工时的成员"""
        while self._legend_lay.count():
            item = self._legend_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        seen = set()
        for row in self._stacked:
            mid = int(row.get("member_id") or 0)
            if mid in seen or not row.get("member_name"):
                continue
            seen.add(mid)
            tag = _LegendTag(row.get("member_name"), member_color(mid),
                             self._legend_host)
            self._legend_lay.addWidget(tag)
        self._legend_lay.addStretch(1)

    def _fill_donut(self):
        total = sum(float(r.get("hours") or 0) for r in self._cats)
        slices = [(r.get("category"), float(r.get("hours") or 0),
                   QColor(category_color(r.get("category"))))
                  for r in self._cats]
        self.donut.setData(slices, total)
        while self._donut_legend_lay.count():
            item = self._donut_legend_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for name, hours, color in slices:
            row = QWidget(self._donut_legend)
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(SPACE[1])
            dot = BodyLabel("●", row)
            dot.setFont(px_font(TYPE_SCALE_PX["sm"]))
            dot.setStyleSheet("color: %s;" % color.name())
            lay.addWidget(dot)
            lay.addWidget(BodyLabel(name or "未分类", row))
            pct = StrongBodyLabel(
                "%d%%" % round(hours * 100.0 / total) if total else "0%", row)
            lay.addWidget(pct)
            lay.addWidget(CaptionLabel("· %sh" % fmt_hours(hours), row))
            lay.addStretch(1)
            self._donut_legend_lay.addWidget(row)
        self._donut_legend_lay.addStretch(1)

    def _fill_table(self):
        self._table.setRowCount(len(self._summary))
        for r, row in enumerate(self._summary):
            mid = int(row.get("member_id") or 0)
            name = row.get("member_name") or "未指派"
            item = QTableWidgetItem(name)
            item.setData(ROLE_AVATAR, (name[:1], member_color(mid), name))
            self._table.setItem(r, 0, item)
            nums = (row.get("count"), row.get("done"),
                    fmt_hours(row.get("hours")),
                    "%d%%" % (row.get("done_rate") or 0),
                    "%d%%" % (row.get("avg_progress") or 0))
            for c, value in enumerate(nums, start=1):
                # 稿 153 的 td.num 只给等宽数字、不右对齐（th 才分左/右）
                cell = QTableWidgetItem(str(value))
                cell.setFont(tabular(px_font(TYPE_SCALE_PX["base"])))
                self._table.setItem(r, c, cell)
            ops = QTableWidgetItem("")
            ops.setData(LINKS_ROLE, [("复制日报", "primary", "day"),
                                     ("复制周报", "primary", "week")])
            self._table.setItem(r, len(_COLUMNS) - 1, ops)

    def _on_ops_link(self, row, _col, action):
        rec = self._summary[row] if 0 <= row < len(self._summary) else None
        if rec is None:
            return
        mid = int(rec.get("member_id") or 0)
        today = QDate.currentDate()
        if action == "day":
            d = iso(today)
            self._report_label = "%s 的日报" % (rec.get("member_name") or "")
            self._ctl.build_report(d, d, mid, title="日报")
        else:
            start, end = worklog_db.week_range(iso(today))
            self._report_label = "%s 的周报" % (rec.get("member_name") or "")
            self._ctl.build_report(start, end, mid, title="周报")

    # ---------------- 报告 / 导出 ----------------

    def _on_copy_report(self):
        _buckets, start, end = self._window()
        self._report_label = "本期文字报告"
        self._ctl.build_report(start, end, self._member_id(),
                               title=dict(_PERIODS)[self._period_key()] + "统计")

    def _on_report_ready(self, text):
        if not text:
            show_info_bar("本期没有可复制的记录", "warning", parent=self,
                          duration=3000)
            return
        QApplication.clipboard().setText(text, QClipboard.Mode.Clipboard)
        show_info_bar("%s已复制到剪贴板" % self._report_label, "success",
                      parent=self, duration=2500)

    def _on_export(self):
        _buckets, start, end = self._window()
        path, _ok = QFileDialog.getSaveFileName(
            self.window(), "导出 Excel", "工作留痕_%s_%s.xlsx" % (start, end),
            "Excel 文件 (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        if not self._ctl.export_xlsx(path, start, end, self._member_id()):
            show_info_bar("已有导出任务在跑，请稍候再试", "warning",
                          parent=self, duration=2500)

    def _on_export_done(self, count):
        show_info_bar("已导出 %d 行记录" % int(count or 0), "success",
                      parent=self, duration=2500)


class _LegendTag(QWidget):
    """柱图图例的小色签（稿 543 的 .tag acc/info/on）"""

    def __init__(self, text, hex_color, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.setSpacing(4)
        dot = BodyLabel("●", self)
        dot.setFont(px_font(8))
        dot.setStyleSheet("color: %s;" % hex_color)
        lay.addWidget(dot)
        label = BodyLabel(text, self)
        label.setFont(px_font(TYPE_SCALE_PX["sm"]))
        label.setStyleSheet("color: %s;" % hex_color)
        lay.addWidget(label)


__all__ = ["StatsPage"]
