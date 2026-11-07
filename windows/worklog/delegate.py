# -*- coding: utf-8 -*-
"""工作留痕表格委托：分组带 / 成员头像 / 语义标签 / 迷你进度条 / 右对齐操作链接

稿 404-408 要求表格走 LeanTableDelegate 且「禁 cellWidget 列」，所有装饰
只能在委托里画。本模块在 ``OpsLeanDelegate`` 上加四类自绘角色，不另起
炉灶：hover 行、选中指示条、勾选框、文本层缓存、操作列链接命中全部沿用
既有实现（core/lean_table_delegate.py 的七条回归点也一并继承）。

两条必须守住的口径
------------------
1. **自绘格绕开整格缓存**。LeanTableDelegate 的缓存键只含 DisplayRole /
   FontRole / ForegroundRole / CheckStateRole / BackgroundRole，不含下面
   这四个自定义角色；同一 (row, col) 换了头像或标签会命中上一份位图，
   表现为「刷新后所有成员都长成第一个」。
2. **整格矩形要在 super().paint() 之前拷出来**。基类会原地把
   ``option.rect`` 上下各收 ``margin``(=2px)，收完再画行分隔线会短一截。

字号说明：稿里有 10.5 / 12.5 / 13.5 这类半点像素，而 Qt 的
``QFont.setPixelSize`` 只收整数，一律按四舍五入取整（→ 11 / 13 / 14）。
"""

from PySide6.QtCore import QRect, Qt, QEvent
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen

from core.design_tokens import (FONT_WEIGHTS, GRAY_DARK, GRAY_LIGHT,
                                TYPE_SCALE_PX)
from core.ops_link_delegate import (OpsLeanDelegate, OpsPlainDelegate,
                                    _INSET_L, _INSET_R, install_ops_links)
from database.worklog_db import PROGRESS_STEP
from windows.worklog.common import (accent_press, accent_soft_q, color_hex,
                                    px_font, tabular, text_color_tier,
                                    track_hex)

# 自定义角色从 UserRole+10 起：LINKS_ROLE 占了 +2，统计弹窗占了 +1，
# 勾选列的 id 锚在 UserRole，撞上去会让链接/勾选静默失灵
ROLE_GROUP = int(Qt.ItemDataRole.UserRole) + 10     # str：分组带文案
ROLE_AVATAR = int(Qt.ItemDataRole.UserRole) + 11    # (首字, 底色 hex, 名字)
ROLE_TAG = int(Qt.ItemDataRole.UserRole) + 12       # (文案, 前景 hex, 软底 hex)
ROLE_BAR = int(Qt.ItemDataRole.UserRole) + 13       # int：进度百分比

# 单元格左右内边距：稿 147 写 12px，但文本层统一走 LeanTableDelegate 的
# _TEXT_INSET_L(15px) —— 那是为对齐原生 SE_ItemViewItemText 定的，改了会让
# 自绘的头像/标签与其余列的文本左边线错开 3px，比与稿差 3px 更显眼
_CELL_PAD_X = 12

# 成员列（稿 171-173）
_AVATAR_SIZE = 20
_AVATAR_GAP = 6
_AVATAR_FONT_PX = 11          # 稿 10.5，Qt 只收整数像素
_MEMBER_FONT_PX = 13          # 稿 12.5

# 语义标签（稿 162-170）
_TAG_FONT_PX = TYPE_SCALE_PX["sm"]
_TAG_DOT = 6
_TAG_DOT_GAP = 5
_TAG_PAD_X_MAX = 9            # 稿 padding:2px 9px
_TAG_PAD_X_MIN = 3            # 状态列只有 76px，收不到 3 就只能截断
_TAG_PAD_Y = 2

# 迷你进度条（稿 157-159）
_BAR_H = 6
_BAR_GAP = 8                  # 稿 .bar-mini 的 margin-right
_BAR_W_MAX = 90
_BAR_W_MIN = 24

# 分组带（稿 149-150）
_GROUP_FONT_PX = 13           # 稿 12.5


class _WorklogCustomMixin:
    """四类自绘角色的绘制与进度步进命中（mixin 必须写在基类之前）"""

    _step_handler = None

    # ---------------- 角色识别 ----------------

    @staticmethod
    def _group_text(index):
        """分组行文案；只有第 0 列携带，其余列靠它判断「本行是分组行」"""
        return index.data(ROLE_GROUP)

    @staticmethod
    def _custom_payload(index):
        """取自绘角色，返回 (种类, 值)；普通格返回 None"""
        for kind, role in (("avatar", ROLE_AVATAR), ("tag", ROLE_TAG),
                           ("bar", ROLE_BAR)):
            value = index.data(role)
            if value is not None:
                return kind, value
        return None

    def _is_group_row(self, index) -> bool:
        if index.column() == 0:
            return bool(self._group_text(index))
        first = index.model().index(index.row(), 0, index.parent())
        return bool(first.data(ROLE_GROUP))

    # ---------------- 绘制入口 ----------------

    def paint(self, painter, option, index):
        if not index.isValid():
            return super().paint(painter, option, index)
        # 整格矩形先拷一份：基类会原地把它上下各收 margin，之后再画行分隔线
        # 与自绘内容就会短 2px（core/lean_table_delegate.py 回归点 4）
        full = QRect(option.rect)
        if self._is_group_row(index):
            self._paint_group_band(painter, full, index)
            return
        payload = self._custom_payload(index)
        if payload is not None:
            self._paint_base_layer(painter, option, index)
            self._paint_custom(painter, full, payload, self._theme_dark())
            self._paint_row_line(painter, full)
            return
        super().paint(painter, option, index)
        self._paint_row_line(painter, full)

    @staticmethod
    def _theme_dark() -> bool:
        from qfluentwidgets import isDarkTheme
        return bool(isDarkTheme())

    def _paint_base_layer(self, painter, option, index):
        """底层（背景/hover/选中/勾选/文本）由各形态自己指定，见两个子类"""
        raise NotImplementedError

    def _paint_row_line(self, painter, full):
        """行底 1px 分隔线（稿 147 的 border-bottom:#f0f1f3）

        #f0f1f3 不是令牌色，取最接近的 ``GRAY_*["bg"]``：令牌优先于稿的字面
        hex，否则深色主题下这条线要么消失要么刺眼。
        """
        tokens = GRAY_DARK if self._theme_dark() else GRAY_LIGHT
        painter.save()
        painter.setPen(QPen(QColor(tokens["bg"]), 1.0))
        painter.drawLine(full.left(), full.bottom(), full.right(), full.bottom())
        painter.restore()

    # ---------------- 分组带 ----------------

    def _paint_group_band(self, painter, full, index):
        """整行分组带（稿 348-350）：--accent-soft 底 + --accent-press 文字

        稿说分组行「不占模型列」，但 QTableWidget 没有 colspan（setSpan 在
        本仓零先例），所以做成真实行：每格各填自己那块底色，文字由携带文案
        的第 0 列按「行首绝对坐标」画出整句，各格再用自己的矩形裁出自己那
        一段。视图不保证第 0 列先画，只让它画满整行会被后画的列盖掉。
        """
        painter.save()
        painter.setClipRect(full)
        painter.fillRect(full, accent_soft_q())

        text = str(self._group_text(index) or "")
        if text:
            view = self.parent()
            band_left = view.columnViewportPosition(0)
            if band_left < 0:      # 第 0 列被隐藏：按列宽累加推算行首
                band_left = full.left() - sum(
                    view.columnWidth(c) for c in range(index.column()))
            cols = index.model().columnCount()
            total = sum(view.columnWidth(c) for c in range(cols))
            band = QRect(band_left, full.top(), max(total, full.width()),
                         full.height())
            painter.setPen(QColor(accent_press()))
            painter.setFont(px_font(_GROUP_FONT_PX, FONT_WEIGHTS["semibold"]))
            painter.drawText(band.adjusted(_CELL_PAD_X, 0, -_CELL_PAD_X, 0),
                             Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()

    # ---------------- 三类自绘格 ----------------

    def _paint_custom(self, painter, full, payload, dark):
        kind, value = payload
        if kind == "avatar":
            self._paint_avatar(painter, full, value, dark)
        elif kind == "tag":
            self._paint_tag(painter, full, value)
        elif kind == "bar":
            self._paint_bar(painter, full, value, dark)

    def _paint_avatar(self, painter, rect, payload, dark):
        """成员列（稿 171-173）：20px 圆形头像（姓氏首字）+ 12.5px 名字"""
        initial, hexv, name = payload
        x = rect.left() + _INSET_L
        d = _AVATAR_SIZE
        top = rect.center().y() - d // 2
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(hexv))
        painter.drawEllipse(QRect(x, top, d, d))
        painter.setPen(QColor("#ffffff"))
        painter.setFont(px_font(_AVATAR_FONT_PX, FONT_WEIGHTS["semibold"]))
        painter.drawText(QRect(x, top, d, d), Qt.AlignmentFlag.AlignCenter,
                         str(initial or "")[:1])

        name_x = x + d + _AVATAR_GAP
        name_w = rect.right() - _INSET_R - name_x
        if name_w > 0:
            font = px_font(_MEMBER_FONT_PX)
            fm = QFontMetrics(font)
            painter.setFont(font)
            painter.setPen(text_color_tier(1)[1 if dark else 0])
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.drawText(QRect(name_x, rect.top(), name_w, rect.height()),
                             Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter,
                             fm.elidedText(str(name or ""),
                                           Qt.TextElideMode.ElideRight, name_w))
        painter.restore()

    def _paint_tag(self, painter, rect, payload):
        """语义标签胶囊（稿 162-170）：软底 + 6px 前导圆点 + 12px/600 文字

        内边距自适应：状态列只有 76px，稿的 padding 9px 放不下「已完成」
        （要 ~65px，可用只有 58px），所以从 9 逐像素收到 3，仍不够才截断。
        分类列 84px 能保持稿的原值。
        """
        text, fg, bg = payload
        font = px_font(_TAG_FONT_PX, FONT_WEIGHTS["semibold"])
        fm = QFontMetrics(font)
        content = _TAG_DOT + _TAG_DOT_GAP + fm.horizontalAdvance(str(text))
        avail = rect.right() - _INSET_R - (rect.left() + _CELL_PAD_X)
        pad = _TAG_PAD_X_MAX
        while pad * 2 + content > avail and pad > _TAG_PAD_X_MIN:
            pad -= 1
        width = min(pad * 2 + content, max(0, avail))
        height = fm.height() + _TAG_PAD_Y * 2
        x = rect.left() + _CELL_PAD_X
        y = rect.center().y() - height // 2

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(QRect(x, y, width, height),
                                height / 2.0, height / 2.0)
        dot_y = y + (height - _TAG_DOT) // 2
        painter.setBrush(QColor(fg))
        painter.drawEllipse(QRect(x + pad, dot_y, _TAG_DOT, _TAG_DOT))

        text_x = x + pad + _TAG_DOT + _TAG_DOT_GAP
        text_w = x + width - pad - text_x
        if text_w > 0:
            painter.setPen(QColor(fg))
            painter.setFont(font)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.drawText(QRect(text_x, y, text_w, height),
                             Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter,
                             fm.elidedText(str(text),
                                           Qt.TextElideMode.ElideRight, text_w))
        painter.restore()

    def _paint_bar(self, painter, rect, pct, dark):
        """进度列（稿 355）：迷你条 + 右侧百分比数字

        条宽按可用空间收缩：稿写死 90px，可 130px 的列减去左右内边距、
        8px 间距与「100%」四个字符后只剩 ~68px，照抄会把数字挤出格子。
        """
        pct = max(0, min(100, int(pct or 0)))
        font = tabular(px_font(TYPE_SCALE_PX["base"]))
        fm = QFontMetrics(font)
        text = "%d%%" % pct
        text_w = fm.horizontalAdvance(text)
        x = rect.left() + _CELL_PAD_X
        bar_w = min(_BAR_W_MAX, rect.right() - _INSET_R - x - _BAR_GAP - text_w)
        bar_w = max(_BAR_W_MIN, bar_w)
        top = rect.center().y() - _BAR_H // 2

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(track_hex()))
        painter.drawRoundedRect(QRect(x, top, bar_w, _BAR_H),
                                _BAR_H / 2.0, _BAR_H / 2.0)
        fill_w = int(round(bar_w * pct / 100.0))
        if fill_w > 0:
            painter.setBrush(QColor(color_hex("accent")))
            painter.drawRoundedRect(QRect(x, top, fill_w, _BAR_H),
                                    _BAR_H / 2.0, _BAR_H / 2.0)
        painter.setPen(text_color_tier(1)[1 if dark else 0])
        painter.setFont(font)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.drawText(QRect(x + bar_w + _BAR_GAP, rect.top(),
                               rect.right() - _INSET_R - x - bar_w - _BAR_GAP,
                               rect.height()),
                         Qt.AlignmentFlag.AlignLeft
                         | Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()

    # ---------------- 操作列右对齐 ----------------

    def _link_geom(self, option, links):
        """操作链接整体右移贴齐右内边距（稿 154 justify-content:flex-end）

        基类算式是左起排布；这里只做平移，命中矩形跟着走（``_hit`` 复用同一
        算式）。剩余空间不足时保持左起 + 截断，不硬挤到格子外面去。
        间距沿用 core 的 ``_GAP``(14px) 而非稿的 10px：那是为防误触调过的
        共享常量，全仓操作列同一手感。
        """
        geom = super()._link_geom(option, links)
        if not geom:
            return geom
        delta = (option.rect.right() - _INSET_R) - geom[-1][3].right()
        if delta <= 0:
            return geom
        return [(t, c, a, QRect(r).translated(delta, 0)) for t, c, a, r in geom]

    # ---------------- 进度步进 ----------------

    def set_step_handler(self, handler):
        """进度步进回调 ``handler(row, new_percent)``

        不复用操作列的 handler：那个签名是 (row, col, action_key)，把「步进
        到多少」塞进 action_key 会让页面侧的分派多一层解包。
        """
        self._step_handler = handler

    def _step_at(self, pos, up: bool) -> bool:
        """进度格上的滚轮/双击步进；非进度格返回 False 让事件照常放行

        滚轮必须由视口过滤器接管：QAbstractItemView.viewportEvent 不把
        Wheel 转给 editorEvent，委托在那里等不到这个事件。
        """
        view = self.parent()
        if view is None:
            return False
        try:
            index = view.indexAt(pos)
        except Exception:
            return False
        if not index.isValid() or index.data(ROLE_BAR) is None:
            return False
        cur = max(0, min(100, int(index.data(ROLE_BAR) or 0)))
        new = max(0, min(100, cur + (PROGRESS_STEP if up else -PROGRESS_STEP)))
        # 已在边界也要吃掉滚轮，否则表格会跟着翻页，用户以为没生效又多滚几下
        if new != cur and self._step_handler is not None:
            self._step_handler(index.row(), new)
        return True

    def eventFilter(self, obj, event):
        if obj is self._ops_watch:
            et = event.type()
            if et == QEvent.Type.Wheel:
                if self._step_at(self._event_pos(event),
                                 event.angleDelta().y() > 0):
                    event.accept()
                    return True
            elif et == QEvent.Type.MouseButtonDblClick:
                # 进度格上的双击是步进而不是编辑：吞掉它，视图就发不出
                # doubleClicked，页面的「双击整行=编辑」不会误触
                if self._step_at(self._event_pos(event), True):
                    event.accept()
                    return True
        return super().eventFilter(obj, event)


class WorklogLeanDelegate(_WorklogCustomMixin, OpsLeanDelegate):
    """P0-1 轻量文本绘制 + 工作留痕自绘列（默认形态）"""

    def _paint_base_layer(self, painter, option, index):
        # 直绘而不走整格缓存：缓存键不含四类自定义角色，
        # 同一 (row, col) 换了头像/标签会命中上一份位图
        self._paint_direct(painter, option, index)


class WorklogPlainDelegate(_WorklogCustomMixin, OpsPlainDelegate):
    """库委托绘制 + 工作留痕自绘列（设置页关掉轻量委托时的回退形态）"""

    def _paint_base_layer(self, painter, option, index):
        # 走 _OpsLinkMixin.paint：自绘格没有链接数据，它画完底层就返回
        super().paint(painter, option, index)


def install_worklog_delegate(table, handler, step_handler=None):
    """换上工作留痕自绘委托，并把两种形态登记到表上

    ``_OPS_DELEGATE_CLASSES`` 是给 core.ops_link_delegate 的形态覆盖：
    设置页关掉「轻量委托」时要换成 WorklogPlainDelegate 而不是库基类，
    否则分组带 / 头像 / 标签 / 进度条会当场退化成纯文本。
    """
    table._OPS_DELEGATE_CLASSES = (WorklogLeanDelegate, WorklogPlainDelegate)
    delegate = install_ops_links(table, handler)
    if hasattr(delegate, "set_step_handler"):
        delegate.set_step_handler(step_handler)
    return delegate


__all__ = ["ROLE_GROUP", "ROLE_AVATAR", "ROLE_TAG", "ROLE_BAR",
           "WorklogLeanDelegate", "WorklogPlainDelegate",
           "install_worklog_delegate"]
