# -*- coding: utf-8 -*-
"""页签2 时间轴（稿 412-455）：按天 / 按成员两轴的只读回看

稿 452-454 的实现注记逐条落地：滚动区与条目卡全是自绘 QWidget（外壳复用
common.WorklogPage 的 QScrollArea）、不引第三方时间轴库、与页签1 共用同一
查询 Worker（controller.query_records）。

分组头的三个数字（工时/项数/完成率）直接对 worker 回来的明细做聚合，不为
第二轴再加一族 SQL：明细行已带全部字段，两轴口径天然一致，也省掉「按成员
聚合」在 SQLite / MySQL 双后端各写一份的维护面。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel

from qfluentwidgets import BodyLabel, CaptionLabel, ComboBox, StrongBodyLabel

from core.design_tokens import (GRAY_DARK, GRAY_LIGHT, SPACE, TYPE_SCALE_PX,
                                FONT_WEIGHTS)
from core.utils import show_info_bar
from database import worklog_db
from windows.worklog.common import (AvatarDot, CardBox, ColorLabel, DateBox,
                                    FlowToolbar, TagPill, WorklogPage,
                                    category_color, color_hex, day_label,
                                    fmt_hours, iso, parse_iso, px_font,
                                    status_tag, tag_colors, text_color_tier,
                                    week_scope_zh, weekday_zh)
from windows.worklog.controller import WorklogController

# 稿 415 的下拉只有一处，anno 说是「按天/按成员 两轴切换」，文案照抄稿面
_MODES = ("按天 · 全部成员", "按成员 · 全部成员")


class _EntryCard(CardBox):
    """稿 184-190 的条目卡：标题行（内容 + 成员头像）+ meta 行

    整卡可点（稿 419 点条目跳页签1 定位），所以给 PointingHand 光标；
    圆点不画在这里 —— CSS 上它在卡片矩形外 left:-21px，Qt 子控件画不出
    父矩形，交给 _TimelineBody 统一画。
    """

    clicked = Signal(dict)

    def __init__(self, rec, show_date=False, parent=None):
        super().__init__(padding=(10, 14), gap=4, parent=parent)
        self._rec = rec
        self.dot_color = (color_hex("success")
                          if rec.get("status") == worklog_db.DONE_STATUS
                          else color_hex("accent"))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(SPACE[2])                     # 稿 189 gap:8px
        title = StrongBodyLabel(rec.get("title") or "", self)
        title.setFont(px_font(13.5, FONT_WEIGHTS["semibold"]))
        title.setWordWrap(False)
        head.addWidget(title)
        name = rec.get("member_alias") or rec.get("member_name") or ""
        if name:
            dot = AvatarDot(self)
            dot.set_member(name[:1], rec.get("member_id"))
            head.addWidget(dot)
            who = BodyLabel(name, self)
            who.setFont(px_font(12.5))
            head.addWidget(who)
        head.addStretch(1)
        self.lay.addLayout(head)

        meta = QHBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(SPACE[3])                     # 稿 190 gap:12px
        if show_date:
            d = parse_iso(rec.get("work_date"))
            meta.addWidget(_meta_label(
                "%s %s" % (day_label(d), weekday_zh(d)) if d.isValid()
                else rec.get("work_date") or ""))
        cat = rec.get("category") or ""
        if cat:
            meta.addWidget(ColorLabel("● " + cat,
                                      lambda c=cat: category_color(c)))
        if rec.get("project"):
            meta.addWidget(_meta_label("📁 " + rec.get("project")))
        meta.addWidget(_meta_label("⏱ %sh" % fmt_hours(rec.get("hours"))))
        meta.addWidget(_meta_label("进度 %d%%" % (rec.get("progress") or 0)))
        pill = TagPill(rec.get("status") or "", self)
        pill.set_tag(rec.get("status") or "", *status_tag(rec.get("status")))
        meta.addWidget(pill)
        meta.addStretch(1)
        self.lay.addLayout(meta)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._rec)
        super().mousePressEvent(ev)


class _TimelineBody(QWidget):
    """稿 181-188：左缩进 22 的条目容器，竖线与圆点在本层自绘"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(22, 0, 0, 0)      # 稿 181 padding-left:22
        self.lay.setSpacing(SPACE[2])                 # 稿 185 margin-bottom:8
        from qfluentwidgets import qconfig
        qconfig.themeChanged.connect(self.update)
        qconfig.themeColorChanged.connect(self.update)

    def paintEvent(self, _event):
        from qfluentwidgets import isDarkTheme
        tokens = GRAY_DARK if isDarkTheme() else GRAY_LIGHT
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens["border"]))
        # 稿 182-183：left:6 width:2 上下各留 6
        painter.drawRoundedRect(QRectF(6, 6, 2, max(0, self.height() - 12)),
                                1.0, 1.0)
        for i in range(self.lay.count()):
            item = self.lay.itemAt(i)
            card = item.widget() if item else None
            color = getattr(card, "dot_color", None)
            if not color:
                continue
            # 稿 186-187：top:16 的 8px 圆点 + 3px 描边，描边色即状态色
            cy = card.y() + 16
            painter.setBrush(QColor(tokens["surface"]))
            painter.setPen(QPen(QColor(color), 3.0))
            painter.drawEllipse(QRectF(1.5, cy - 5.5, 11, 11))
            painter.setPen(Qt.PenStyle.NoPen)


def _meta_label(text) -> BodyLabel:
    """meta 行的 12px 灰字（稿 190 .tl-meta）"""
    lbl = BodyLabel(text)
    lbl.setFont(px_font(TYPE_SCALE_PX["sm"]))
    lbl.setTextColor(*text_color_tier(2))
    return lbl


class TimelinePage(WorklogPage):
    """时间轴页：只读回看，点条目经 Hub 转发到页签1 定位"""

    locate_requested = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ctl = WorklogController(self)
        self._seq = 0
        self._rows = None
        self._loaded_once = False

        self._build_toolbar()
        self._host = QWidget(self)
        self._host_lay = QVBoxLayout(self._host)
        self._host_lay.setContentsMargins(0, 0, 0, 0)
        self._host_lay.setSpacing(SPACE[2])           # 稿 176 .tl-day mb:6
        self.lay.addWidget(self._host, 1)

        self._lbl_state = BodyLabel("", self)
        self._lbl_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_state.setFont(px_font(TYPE_SCALE_PX["base"]))
        self._lbl_state.setTextColor(*text_color_tier(3))
        self._lbl_state.hide()
        self.lay.addWidget(self._lbl_state)

        self._ctl.rows_ready.connect(self._on_rows)
        self._ctl.load_failed.connect(self._on_load_failed)

    # ---------------- 布局 ----------------

    def _build_toolbar(self):
        """稿 414-419：轴向下拉 + 日期组，右端只读说明"""
        bar = FlowToolbar(self)
        self.cbo_mode = ComboBox(bar)
        self.cbo_mode.setMinimumWidth(150)
        self.cbo_mode.addItems(list(_MODES))
        self.cbo_mode.currentIndexChanged.connect(lambda _i: self._populate())
        bar.addWidget(self.cbo_mode)

        self.date_box = DateBox(bar, caption=week_scope_zh)
        self.date_box.dateChanged.connect(lambda _d: self.refresh())
        bar.addWidget(self.date_box)

        bar.addRight()
        self._note = CaptionLabel(
            "时间轴为只读回看视图 · 点击条目跳转页签1 并定位记录", bar)
        self._note.setFont(px_font(TYPE_SCALE_PX["sm"]))
        self._note.setTextColor(*text_color_tier(3))
        bar.addWidget(self._note)
        self.lay.addWidget(bar)

    # ---------------- 取数 ----------------

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded_once:
            self._loaded_once = True
            self.refresh()

    def detach_workers(self):
        self._ctl.stop_all()

    def refresh(self):
        self._seq += 1
        seq = self._seq
        self._rows = None
        self._set_state("加载中…")
        start, end = worklog_db.week_range(iso(self.date_box.date()))
        # 天序倒排（稿 440 的 11月6日 在 11月5日 之前），天内按成员排序号
        self._ctl.query_records(seq, date_from=start, date_to=end)

    def _on_rows(self, rows, seq):
        if seq != self._seq:
            return
        self._rows = rows or []
        self._populate()

    def _on_load_failed(self, msg, seq):
        if seq != self._seq:
            return
        self._set_state("加载失败，请稍后重试")
        show_info_bar(msg, "error", title="时间轴加载失败", parent=self,
                      duration=4000)

    def _set_state(self, text):
        self._lbl_state.setText(text)
        self._lbl_state.setVisible(bool(text))

    # ---------------- 组装 ----------------

    def _populate(self):
        """按当前轴重建小节；旧卡片带信号，必须 deleteLater 解绑"""
        while self._host_lay.count():
            item = self._host_lay.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        rows = self._rows
        if rows is None:
            return
        if not rows:
            self._set_state("本周还没有留痕记录")
            return
        self._set_state("")
        for key, recs in self._sections(rows):
            self._host_lay.addWidget(self._build_section(key, recs))
        self._host_lay.addStretch(1)

    def _sections(self, rows):
        """[(小节键, 记录列表)]：按天保倒序首现顺序，按成员按排序号"""
        if self.cbo_mode.currentIndex() == 0:
            out = []
            index = {}
            for rec in rows:
                key = rec.get("work_date") or ""
                if key not in index:
                    index[key] = len(out)
                    out.append((key, []))
                out[index[key]][1].append(rec)
            return out
        order, out = {}, []
        for rec in rows:
            key = int(rec.get("member_id") or 0)
            if key not in order:
                order[key] = (len(out), rec.get("member_sort") or 0, key)
                out.append((key, []))
            out[order[key][0]][1].append(rec)
        out.sort(key=lambda pair: order[pair[0]])
        return out

    def _build_section(self, key, recs):
        section = QWidget(self)
        lay = QVBoxLayout(section)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE[3])                      # 稿 177 head mb:10

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(SPACE[2])                     # 稿 177 gap:10
        by_member = self.cbo_mode.currentIndex() == 1
        if by_member:
            name = recs[0].get("member_alias") or recs[0].get("member_name")
            dot = AvatarDot(section)
            dot.set_member((name or "？")[:1], key)
            head.addWidget(dot)
            title_text, sub_text = name or "未命名成员", ""
        else:
            d = parse_iso(key)
            title_text = ("%s %s" % (day_label(d), weekday_zh(d))
                          if d.isValid() else str(key))
            sub_text = str(key)
        title = QLabel(title_text, section)
        title.setFont(px_font(TYPE_SCALE_PX["md"],
                                     FONT_WEIGHTS["bold"]))
        head.addWidget(title)
        if sub_text:
            sub = CaptionLabel(sub_text, section)
            sub.setFont(px_font(TYPE_SCALE_PX["sm"]))
            sub.setTextColor(*text_color_tier(3))
            head.addWidget(sub)
        head.addStretch(1)
        for pill in self._sum_pills(recs, section):
            head.addWidget(pill)
        lay.addLayout(head)

        body = _TimelineBody(section)
        for rec in recs:
            card = _EntryCard(rec, show_date=by_member, parent=body)
            card.clicked.connect(self.locate_requested)
            body.lay.addWidget(card)
        lay.addWidget(body)
        return section

    def _sum_pills(self, recs, parent):
        """稿 424 日头右侧三枚：⏱ 工时 / 📋 项数 / ✅ 完成率"""
        hours = sum(float(r.get("hours") or 0) for r in recs)
        done = sum(1 for r in recs
                   if r.get("status") == worklog_db.DONE_STATUS)
        rate = int(round(done * 100.0 / len(recs))) if recs else 0
        specs = (("⏱ %sh" % fmt_hours(hours), tag_colors("accent")),
                 ("📋 %d 项" % len(recs), tag_colors("info")),
                 ("✅ %d%%" % rate, tag_colors("success")))
        out = []
        for text, (fg, bg) in specs:
            pill = TagPill(text, parent)
            pill.set_tag(text, fg, bg)
            out.append(pill)
        return out


__all__ = ["TimelinePage"]
