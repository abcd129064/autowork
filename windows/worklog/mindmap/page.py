# -*- coding: utf-8 -*-
"""页签3 思维导图（稿 459-518）：周期汇总 → 分组维度 → 任务叶子三级导图

稿 508-513 的实现注记逐条落地：QGraphicsView + QGraphicsScene 自绘节点与
贝塞尔连线（不引第三方导图库）；折叠态只存内存；双击叶子开 EditWorklogDialog；
单次渲染节点 > 500 时不画，改用 InfoBar 提示缩小周期（防场景卡顿）。

节点坐标照抄稿 468-505 的 SVG：根 x=24 / 166×48、二级 x=330 / 230×48、
叶子 x=660 / 330×38、叶间距 24、块间距 32。稿里二级节点第二行是个裸数字
（"13"，与同行括号里的"12 项"还对不上），按示意稿笔误处理，实装改成
「已完成 X 项 · 完成率 Y%」；连线同理——稿把李工那条连到 y=180 而节点中心
在 200，实装一律连到节点几何中心。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QDate, Signal
from PySide6.QtGui import (QColor, QFontMetrics, QPainter, QPainterPath, QPen)
from PySide6.QtWidgets import (QFrame, QGraphicsObject, QGraphicsScene,
                               QGraphicsView, QVBoxLayout, QWidget)
from qfluentwidgets import BodyLabel, ComboBox, isDarkTheme, qconfig

from core.design_tokens import (FONT_WEIGHTS, GRAY_DARK, GRAY_LIGHT,
                                MINDMAP_TOKENS, RADIUS, TYPE_SCALE_PX)
from core.utils import show_info_bar
from database import worklog_db
from windows.worklog.common import (FlowToolbar, WorklogPage, category_color,
                                    color_hex, day_label, fmt_hours, iso,
                                    member_color, parse_iso, px_font,
                                    text_color_tier, track_hex, weekday_zh)
from windows.worklog.controller import WorklogController
from windows.worklog.dialogs import EditWorklogDialog

# 稿 462 的分组维度四选一
_AXES = (("member", "按成员分组"), ("project", "按项目分组"),
         ("category", "按分类分组"), ("date", "按日期分组"))

# 稿 463 只画了「本周」一档，另配五档常用的；键名与 period_range 无关，
# 窗口在本页算（上周/上月/近 30 天都不在 worklog_db.PERIOD_RANGES 里）
_PERIODS = (("today", "今天"), ("week", "本周"), ("last_week", "上周"),
            ("month", "本月"), ("last_month", "上月"), ("days30", "近 30 天"))

# 稿 468-505 的节点几何
_ROOT_X, _ROOT_W, _ROOT_H = 24, 166, 48
_GROUP_X, _GROUP_W, _GROUP_H = 330, 230, 48
_LEAF_X, _LEAF_W, _LEAF_H = 660, 330, 38
_TOP = 30            # 首叶 y（稿 483）
_LEAF_GAP = 24       # 同组叶间距（稿 483→495：30+38=68 到 92）
_BLOCK_GAP = 32      # 组块间距（稿 495→502：130 到 162）
_NODE_R = 9          # 稿 rect 的 rx=9
_MARGIN = 24         # 场景右/下留白
_BAR_W, _BAR_H = 24, 6      # 稿 490 的迷你进度条
_EMPTY_H = 400              # 稿 467 svg 的 height：首帧还没数据时铺点阵底用
_MAX_NODES = 500            # 稿 512 的护栏

# 项目 / 日期两条轴的分支配色板：不含 accent（那是根的专座），按名字码点
# 求和取模而非 hash()——后者带 PYTHONHASHSEED 随机化，同一分支每次启动换色
_BRANCH_KEYS = ("accent_dark", "info", "success", "warning", "violet", "lime")


def branch_color(seed) -> str:
    """无既有色映射的分支（项目名 / 日期）→ 稳定轮转色"""
    text = str(seed or "")
    if not text:
        return color_hex("neutral")
    return color_hex(_BRANCH_KEYS[sum(ord(c) for c in text)
                                  % len(_BRANCH_KEYS)])


def period_window(key: str, today: QDate) -> tuple:
    """周期键 → (起, 止)；口径与售后周期设置一致（自然周周一起、自然月）"""
    if key == "today":
        d = iso(today)
        return d, d
    if key == "last_week":
        return worklog_db.week_range(iso(today.addDays(-7)))
    if key == "month":
        return worklog_db.month_range(iso(today))
    if key == "last_month":
        first = QDate(today.year(), today.month(), 1)
        return worklog_db.month_range(iso(first.addDays(-1)))
    if key == "days30":
        return iso(today.addDays(-29)), iso(today)
    return worklog_db.week_range(iso(today))


def _tokens():
    """当前主题的 (灰阶, 导图专属色) 一对"""
    dark = isDarkTheme()
    return (GRAY_DARK if dark else GRAY_LIGHT,
            MINDMAP_TOKENS["dark" if dark else "light"])


# ==================== 场景图元 ====================

class _NodeItem(QGraphicsObject):
    """节点基类：矩形 + 圆角 + 左键单击/双击信号

    用 QGraphicsObject 而不是 QGraphicsItem：折叠与开对话框都要回调页面，
    图元自带 Signal 比挂裸函数属性稳（图元被 scene.clear() 删掉时自动断连）。
    """

    clicked = Signal(object)
    doubleClicked = Signal(object)

    def __init__(self, w, h, parent=None):
        super().__init__(parent)
        self._w = float(w)
        self._h = float(h)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)

    def boundingRect(self):
        # 描边 1.2px 加抗锯齿会溢出几何矩形，各边让 1px 才不被裁掉
        return QRectF(-1, -1, self._w + 2, self._h + 2)

    def shape(self):
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, self._w, self._h),
                            _NODE_R, _NODE_R)
        return path

    def _round_path(self, inset=0.0):
        path = QPainterPath()
        r = QRectF(inset, inset, self._w - 2 * inset, self._h - 2 * inset)
        path.addRoundedRect(r, _NODE_R, _NODE_R)
        return path

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit(self)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class _RootNode(_NodeItem):
    """根节点（稿 483-485）：主色实底 + 「本周工作总览」+「38 项 · 112.5h」"""

    def __init__(self, title, sub):
        super().__init__(_ROOT_W, _ROOT_H)
        self._title = title
        self._sub = sub

    def paint(self, painter, _option, _widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color_hex("accent")))
        painter.drawPath(self._round_path())
        box = QRectF(20, 6, self._w - 40, 18)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(px_font(13, FONT_WEIGHTS["semibold"]))
        painter.drawText(box, Qt.AlignmentFlag.AlignLeft
                         | Qt.AlignmentFlag.AlignVCenter, self._title)
        painter.setPen(QColor(255, 255, 255, 204))      # 稿 #ffffffcc
        painter.setFont(px_font(11))
        painter.drawText(QRectF(20, 26, self._w - 40, 15),
                         Qt.AlignmentFlag.AlignLeft
                         | Qt.AlignmentFlag.AlignVCenter, self._sub)


class _GroupNode(_NodeItem):
    """二级分支（稿 486-490）：分组色实底 + 名称（N 项 · Xh）+ −/+ 折叠钮"""

    def __init__(self, key, label, sub, hex_color, collapsed=False):
        super().__init__(_GROUP_W, _GROUP_H)
        self.key = key
        self.color = hex_color
        self.collapsed = bool(collapsed)
        self._label = label
        self._sub = sub
        self._color = QColor(hex_color)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击折叠 / 展开")

    def setCollapsed(self, value):
        self.collapsed = bool(value)
        self.update()

    def paint(self, painter, _option, _widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawPath(self._round_path())
        # 文本区右边界让出折叠钮：钮中心距右缘 38（稿 490 的 x=522 对 560），
        # 加半个字形与 8px 间隙共 54
        text_w = self._w - 18 - 54
        fm = QFontMetrics(px_font(12.5))
        label = fm.elidedText(self._label, Qt.TextElideMode.ElideRight,
                              int(text_w))
        painter.setPen(QColor("#ffffff"))
        painter.setFont(px_font(12.5, FONT_WEIGHTS["semibold"]))
        painter.drawText(QRectF(18, 6, text_w, 18),
                         Qt.AlignmentFlag.AlignLeft
                         | Qt.AlignmentFlag.AlignVCenter, label)
        painter.setPen(QColor(255, 255, 255, 170))      # 稿 #ffffffaa
        painter.setFont(px_font(11))
        painter.drawText(QRectF(18, 26, text_w, 15),
                         Qt.AlignmentFlag.AlignLeft
                         | Qt.AlignmentFlag.AlignVCenter, self._sub)
        painter.setPen(QColor(255, 255, 255, 204))      # 稿 #ffffffcc
        painter.setFont(px_font(TYPE_SCALE_PX["sm"]))
        painter.drawText(QRectF(self._w - 46, 12, 16, 24),
                         Qt.AlignmentFlag.AlignCenter,
                         "+" if self.collapsed else "−")


class _LeafNode(_NodeItem):
    """任务叶子（稿 495-501）：白底 + 分组色 55% 描边 + 标题/工时进度/迷你条"""

    def __init__(self, rec, hex_color):
        super().__init__(_LEAF_W, _LEAF_H)
        self.rec = rec
        self._stroke = QColor(hex_color)
        self._stroke.setAlphaF(0.55)                    # 稿 stroke="#00BCD455"
        self._fill = QColor(hex_color)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("双击打开编辑对话框")

    def paint(self, painter, _option, _widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gray, mm = _tokens()
        painter.setBrush(QColor(gray["surface"]))
        pen = QPen(self._stroke, 1.2)
        painter.setPen(pen)
        # 内缩半个笔宽：不缩的话 1.2px 描边有一半画在几何矩形外，
        # 相邻叶子的描边会互相压到
        painter.drawPath(self._round_path(0.6))

        text_w = _LEAF_W - 34 - 8 - 14      # 右侧让出迷你条（稿 490 x=956）
        title = self.rec.get("title") or "（无标题）"
        fm = QFontMetrics(px_font(TYPE_SCALE_PX["sm"]))
        painter.setPen(QColor(gray["text_primary"]))
        painter.setFont(px_font(TYPE_SCALE_PX["sm"]))
        painter.drawText(QRectF(14, 3, text_w, 16),
                         Qt.AlignmentFlag.AlignLeft
                         | Qt.AlignmentFlag.AlignVCenter,
                         fm.elidedText(title, Qt.TextElideMode.ElideRight,
                                       int(text_w)))
        progress = int(self.rec.get("progress") or 0)
        sub = "%sh · %d%%" % (fmt_hours(self.rec.get("hours")), progress)
        painter.setPen(QColor(mm["leaf_sub"]))
        painter.setFont(px_font(10.5))
        painter.drawText(QRectF(14, 19, text_w, 14),
                         Qt.AlignmentFlag.AlignLeft
                         | Qt.AlignmentFlag.AlignVCenter, sub)

        bar_x = _LEAF_W - 34
        bar_y = (_LEAF_H - _BAR_H) / 2.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(track_hex()))
        painter.drawRoundedRect(QRectF(bar_x, bar_y, _BAR_W, _BAR_H), 3, 3)
        fill_w = _BAR_W * max(0, min(100, progress)) / 100.0
        if fill_w > 0:
            painter.setBrush(self._fill)
            painter.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, _BAR_H), 3, 3)


class _LinkItem(QGraphicsObject):
    """贝塞尔连线（稿 468-482）：控制点取两端 x 的中点，横向 S 形

    稿的路径 M190,200 C260,200 260,80 330,80 就是这个算式：260 = (190+330)/2。
    """

    def __init__(self, p1, p2, hex_color, width, alpha=1.0):
        super().__init__()
        self._path = QPainterPath()
        mid = (p1.x() + p2.x()) / 2.0
        self._path.moveTo(p1)
        self._path.cubicTo(QPointF(mid, p1.y()), QPointF(mid, p2.y()), p2)
        self._color = QColor(hex_color)
        self._color.setAlphaF(alpha)
        self._width = width
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(-1)              # 压在节点下面，交叠处才不啃掉节点圆角

    def boundingRect(self):
        return self._path.boundingRect().adjusted(-3, -3, 3, 3)

    def paint(self, painter, _option, _widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self._color, self._width))
        painter.drawPath(self._path)


# ==================== 画布 ====================

class _MindView(QGraphicsView):
    """稿 193-195 的 .mm-scroll：surface 底 + 18px 点阵 + 双向滚动

    点阵在 drawBackground 里画而不是给场景铺 QPixmap：场景矩形会随数据
    长到几千像素，平铺一张图要先造出那么大的画布；背景绘制拿到的是可视
    矩形，只画看得见的那些点。
    """

    _DOT_STEP = 18

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # 不跟随鼠标锚定：滚动时视图不该重新居中，稿要的是普通溢出滚动
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.NoAnchor)
        self._scene.setSceneRect(0, 0, _LEAF_X + _LEAF_W + _MARGIN,
                                 _EMPTY_H)
        qconfig.themeChanged.connect(self._repaint_all)
        qconfig.themeColorChanged.connect(self._repaint_all)

    def reset(self, items, bounds):
        """整场景重建：clear() 会 delete 图元，信号随之自动断开"""
        self._scene.clear()
        for item in items:
            self._scene.addItem(item)
        self._scene.setSceneRect(bounds)

    def _repaint_all(self):
        # 节点色取自令牌与主题色，主题一变就得重画，否则留在旧主题上
        self._scene.update()
        self.viewport().update()

    def drawBackground(self, painter, rect):
        gray, mm = _tokens()
        view = QRectF(self.mapToScene(self.viewport().rect()).boundingRect())
        path = QPainterPath()
        # 内圆角 = 外框 6 减去 1px 边框；不裁剪的话方角背景会盖住外框圆角
        inner = float(RADIUS["md"] - 1)
        path.addRoundedRect(view.adjusted(0.5, 0.5, -0.5, -0.5), inner, inner)
        painter.save()
        painter.setClipPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(gray["surface"]))
        painter.drawRect(view)
        painter.setBrush(QColor(mm["grid"]))
        step = self._DOT_STEP
        x0 = int(rect.left() // step) * step
        y0 = int(rect.top() // step) * step
        x = x0
        while x <= rect.right():
            y = y0
            while y <= rect.bottom():
                # 稿的 radial-gradient(circle at 1px 1px, … 1px)：半径 1 的圆点
                painter.drawEllipse(QPointF(x + 1.0, y + 1.0), 1.0, 1.0)
                y += step
            x += step
        painter.restore()


class _CanvasBox(QWidget):
    """稿 466 的 .mm-scroll 外框：高 430、圆角 6、1px --border

    边框画在容器上、视图内缩 1px：QGraphicsView 自己的 frame 取 style 的
    默认边框色，与令牌灰阶对不上，也没有圆角。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(430)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        self.view = _MindView(self)
        lay.addWidget(self.view)
        qconfig.themeChanged.connect(self.update)

    def paintEvent(self, _event):
        gray, _mm = _tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(gray["border"]), 1.0))
        r = float(RADIUS["md"])
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), r, r)


# ==================== 页面 ====================

class MindmapPage(WorklogPage):
    """思维导图页：只读回看 + 双击叶子编辑"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ctl = WorklogController(self)
        self._seq = 0
        self._rows = None
        self._collapsed = {}          # 分组键 → True；稿 512 明确只存内存
        self._loaded_once = False
        self._members = []
        self._categories = []
        self._projects = []

        self._build_toolbar()
        self.canvas = _CanvasBox(self)
        self.lay.addWidget(self.canvas)

        self._lbl_state = BodyLabel("", self.body)
        self._lbl_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_state.setFont(px_font(TYPE_SCALE_PX["base"]))
        self._lbl_state.setTextColor(*text_color_tier(3))
        self._lbl_state.hide()
        self.lay.addWidget(self._lbl_state)
        self.lay.addStretch(1)

        self._ctl.rows_ready.connect(self._on_rows)
        self._ctl.load_failed.connect(self._on_load_failed)
        self._ctl.options_ready.connect(self._on_options)
        self._ctl.options_failed.connect(
            lambda msg: show_info_bar(msg, "error", title="候选加载失败",
                                      parent=self, duration=4000))

    # ---------------- 布局 ----------------

    def _build_toolbar(self):
        """稿 460-465：分组维度 + 周期，右端只读说明"""
        bar = FlowToolbar(self)
        self.cbo_axis = ComboBox(bar)
        self.cbo_axis.setMinimumWidth(150)
        for key, text in _AXES:
            self.cbo_axis.addItem(text, userData=key)
        self.cbo_axis.currentIndexChanged.connect(lambda _i: self._populate())
        bar.addWidget(self.cbo_axis)

        self.cbo_period = ComboBox(bar)
        self.cbo_period.setMinimumWidth(110)
        for key, text in _PERIODS:
            self.cbo_period.addItem(text, userData=key)
        self.cbo_period.setCurrentIndex(1)      # 默认「本周」（稿 463）
        self.cbo_period.currentIndexChanged.connect(
            lambda _i: self.refresh())
        bar.addWidget(self.cbo_period)

        bar.addRight()
        self._note = BodyLabel(
            "点击分支节点折叠 / 展开 · 双击任务节点打开编辑对话框", bar)
        self._note.setFont(px_font(TYPE_SCALE_PX["sm"]))
        self._note.setTextColor(*text_color_tier(3))
        bar.addWidget(self._note)
        self.lay.addWidget(bar)

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
        """成员/分类被页签5 改动后由 Hub 广播：候选与导图都要重拉"""
        self._ctl.fetch_options()
        self.refresh()

    def _axis(self) -> str:
        return self.cbo_axis.currentData() or "member"

    def _period(self) -> tuple:
        key = self.cbo_period.currentData() or "week"
        text = dict(_PERIODS)[key]
        start, end = period_window(key, QDate.currentDate())
        return text, start, end

    def refresh(self):
        self._seq += 1
        seq = self._seq
        self._rows = None
        _text, start, end = self._period()
        self._set_state("加载中…")
        self._ctl.query_records(seq, date_from=start, date_to=end,
                                ascending=True)

    def _on_rows(self, rows, seq):
        if seq != self._seq:
            return
        self._rows = rows or []
        self._populate()
        # 换周期/换维度后回到左上角；折叠触发的重排不走这里，视图位置保持
        for bar in (self.canvas.view.verticalScrollBar(),
                    self.canvas.view.horizontalScrollBar()):
            bar.setValue(bar.minimum())

    def _on_load_failed(self, msg, seq):
        if seq != self._seq:
            return
        self._set_state("加载失败，请稍后重试")
        self.canvas.view.reset([], QRectF())
        show_info_bar(msg, "error", title="思维导图加载失败", parent=self,
                      duration=4000)

    def _on_options(self, data):
        data = data or {}
        self._members = data.get("members") or []
        self._categories = data.get("categories") or []
        self._projects = data.get("projects") or []

    def _set_state(self, text):
        self._lbl_state.setText(text)
        self._lbl_state.setVisible(bool(text))
        self.canvas.setVisible(not text)

    # ---------------- 组装 ----------------

    def _populate(self):
        rows = self._rows
        if rows is None:
            return
        period_text, _start, _end = self._period()
        if not rows:
            self.canvas.view.reset([], QRectF())
            self._set_state("%s还没有留痕记录" % period_text)
            return
        groups = self._group(rows)
        leaves = sum(len(g[3]) for g in groups)
        total = 1 + len(groups) + leaves
        if total > _MAX_NODES:
            # 稿 512 的护栏：先提示再空场景，硬画会让视图卡到点不动
            self.canvas.view.reset([], QRectF())
            self._set_state("节点过多（%d 个），请缩小周期范围后再看" % total)
            show_info_bar("本期节点 %d 个，超过 %d 的渲染上限，"
                          "请缩小周期范围" % (total, _MAX_NODES),
                          "warning", title="思维导图", parent=self,
                          duration=4000)
            return
        self._set_state("")
        items, bounds = self._build_scene(groups, rows, period_text)
        self.canvas.view.reset(items, bounds)

    def _group(self, rows):
        """[(键, 一级标签, 分支色, 记录列表)]，顺序按维度各自的口径"""
        axis = self._axis()
        buckets, order = {}, []
        for rec in rows:
            key = self._key_of(axis, rec)
            if key not in buckets:
                buckets[key] = []
                order.append(key)
            buckets[key].append(rec)
        out = []
        for key in self._sort_keys(axis, order, buckets):
            recs = buckets[key]
            out.append((key, self._label_of(axis, key, recs),
                        self._color_of(axis, key, recs), recs))
        return out

    @staticmethod
    def _key_of(axis, rec):
        if axis == "member":
            return int(rec.get("member_id") or 0)
        if axis == "project":
            return (rec.get("project") or "").strip() or "无项目"
        if axis == "category":
            return (rec.get("category") or "").strip() or "未分类"
        return rec.get("work_date") or ""

    @staticmethod
    def _label_of(axis, key, recs):
        if axis == "member":
            first = recs[0]
            return (first.get("member_alias") or first.get("member_name")
                    or "未指派")
        if axis == "date":
            d = parse_iso(key)
            return ("%s %s" % (day_label(d), weekday_zh(d))
                    if d.isValid() else str(key))
        return str(key)

    @staticmethod
    def _color_of(axis, key, recs):
        if axis == "member":
            return member_color(key)
        if axis == "category":
            return category_color(key)
        return branch_color(key)

    def _sort_keys(self, axis, order, buckets):
        """分支顺序：日期按时间升序、成员按排序号、分类按内置序、项目按工时降序"""
        if axis == "date":
            return sorted(order)
        if axis == "member":
            sort_of = {}
            for recs in buckets.values():
                for rec in recs:
                    mid = int(rec.get("member_id") or 0)
                    sort_of.setdefault(mid, int(rec.get("member_sort") or 0))
            return sorted(order, key=lambda k: (sort_of.get(k, 0), k))
        if axis == "category":
            builtin = {name: i for i, name in enumerate(worklog_db.CATEGORIES)}
            return sorted(order,
                          key=lambda k: (builtin.get(k, 99), str(k)))
        hours = {k: sum(float(r.get("hours") or 0) for r in v)
                 for k, v in buckets.items()}
        return sorted(order, key=lambda k: (-hours[k], str(k)))

    def _build_scene(self, groups, rows, period_text):
        """按稿的三列坐标摆节点，返回 (图元列表, 场景矩形)"""
        items = []
        blocks = []          # [(组节点, [叶节点])]
        cursor = float(_TOP)
        for key, label, color, recs in groups:
            collapsed = bool(self._collapsed.get(key))
            hours = sum(float(r.get("hours") or 0) for r in recs)
            node = _GroupNode(key,
                              "%s（%d 项 · %sh）"
                              % (label, len(recs), fmt_hours(hours)),
                              self._group_sub(recs), color, collapsed)
            node.clicked.connect(self._on_group_clicked)
            leaves = []
            if collapsed:
                node.setPos(_GROUP_X, cursor)
                height = float(_GROUP_H)
            else:
                y = cursor
                for rec in recs:
                    leaf = _LeafNode(rec, color)
                    leaf.setPos(_LEAF_X, y)
                    leaf.doubleClicked.connect(self._on_leaf_double)
                    leaves.append(leaf)
                    y += _LEAF_H + _LEAF_GAP
                span = y - _LEAF_GAP - cursor
                height = max(span, float(_GROUP_H))
                # 分支节点几何中心对齐叶子跨度中心（稿 486 的 y=56 对 30..130）
                node.setPos(_GROUP_X, cursor + (span - _GROUP_H) / 2.0)
            blocks.append((node, leaves))
            items.append(node)
            items.extend(leaves)
            cursor += height + _BLOCK_GAP

        bottom = cursor - _BLOCK_GAP
        root_top = self._center_of(blocks) - _ROOT_H / 2.0
        root = _RootNode("%s工作总览" % period_text,
                         "%d 项 · %sh"
                         % (len(rows),
                            fmt_hours(sum(float(r.get("hours") or 0)
                                          for r in rows))))
        root.setPos(_ROOT_X, root_top)
        items.insert(0, root)

        _gray, mm = _tokens()
        root_anchor = QPointF(_ROOT_X + _ROOT_W, root_top + _ROOT_H / 2.0)
        for node, leaves in blocks:
            gy = node.pos().y() + _GROUP_H / 2.0
            items.append(_LinkItem(root_anchor, QPointF(_GROUP_X, gy),
                                   mm["link"], 1.4))
            if not leaves:
                continue
            g_anchor = QPointF(_GROUP_X + _GROUP_W, gy)
            for leaf in leaves:
                items.append(_LinkItem(
                    g_anchor,
                    QPointF(_LEAF_X, leaf.pos().y() + _LEAF_H / 2.0),
                    node.color, 1.8, 0.55))

        bounds = QRectF(0, 0, _LEAF_X + _LEAF_W + _MARGIN,
                        max(bottom, root_top + _ROOT_H) + _MARGIN)
        return items, bounds

    @staticmethod
    def _center_of(blocks):
        """首末分支中心的中点——根节点垂直居中在分支群里（稿 483）

        取中点而不是各中心的均值：某一条分支特别长时，均值会被它拽过去，
        根节点就不在扇形连线的中间了。稿的三分支等长，两种算法同值。
        """
        centers = [node.pos().y() + _GROUP_H / 2.0 for node, _leaves in blocks]
        if not centers:
            return float(_TOP)
        return (centers[0] + centers[-1]) / 2.0

    @staticmethod
    def _group_sub(recs):
        done = sum(1 for r in recs
                   if r.get("status") == worklog_db.DONE_STATUS)
        rate = int(round(done * 100.0 / len(recs))) if recs else 0
        return "已完成 %d 项 · 完成率 %d%%" % (done, rate)

    # ---------------- 交互 ----------------

    def _on_group_clicked(self, node):
        """折叠/展开：只改内存态并就地重排，不重查库（稿 512）"""
        self._collapsed[node.key] = not node.collapsed
        self._populate()

    def _on_leaf_double(self, leaf):
        rec = leaf.rec or {}
        dlg = EditWorklogDialog(record=rec, parent=self.window(),
                                on_saved=self.refresh,
                                members=self._members,
                                categories=self._categories,
                                projects=self._projects)
        dlg.exec()


__all__ = ["MindmapPage"]
