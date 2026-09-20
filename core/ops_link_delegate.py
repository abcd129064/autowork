# -*- coding: utf-8 -*-
"""表格「操作列」文字链接委托：单元格内自绘多段可点击文本（零子控件）

背景
----
售后面板与跑视频面板的操作列原本是 cellWidget（1 容器 + 2~3 个 QPushButton）：
60 行一页就是 210 个 QWidget，每次刷新重建实测 31ms，换成单元格内文字链接后
8ms（同机对照，纯文本 item 基线 8.0ms）。cellWidget 不走委托绘制路径，
P0-1 的 LeanTableDelegate 文本优化对它们完全无效，还带来「行内按钮整列下沉
8px」这类定位补丁和「排序必须移动 cellWidget」的额外复杂度。

做法
----
链接清单写在 item 的 ``LINKS_ROLE`` 上（``((文案, 色键, 动作键), ...)``），
委托按矩形自绘彩色文字，点击在 ``editorEvent`` 里按矩形反查命中后回调页面。
**绘制与命中共用 ``_link_geom`` 唯一算式**，避免两套坐标随字号/主题漂移。

两个必须遵守的点
----------------
1. 只能作为**视图级委托**装上（``table.setItemDelegate``），不要用
   ``setItemDelegateForColumn``：qfluentwidgets 的 hover / pressed / selected
   行状态是视图推给视图级委托实例的（``TableBase._setHoverRow`` →
   ``self.delegate``），列级委托收不到推送，操作列会掉整行高亮。
2. 基类跟着 P0-1 开关走：开启用 ``LeanTableDelegate``，关闭用库的
   ``TableItemDelegate``。设置页切换开关时 ``core.perf`` 会全局重建委托，
   必须经 ``rebuild_ops_delegate`` 换回本模块的形态，否则一开一关就把链接
   绘制顶没了（表现为操作列变空白）。
"""

from PySide6.QtCore import Qt, QEvent, QRect
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QStyleOptionViewItem

from qfluentwidgets import isDarkTheme, qconfig
from qfluentwidgets.components.widgets.table_view import TableItemDelegate

from core.design_tokens import SEMANTIC
from core.theme_qss import current_accent_hex

try:  # P0-1 轻量委托（正常都在；导入异常时退化为库委托，仅少一层文本加速）
    from core.lean_table_delegate import LeanTableDelegate
except Exception:  # pragma: no cover - 仅在 PySide6/qfluentwidgets 缺失时
    LeanTableDelegate = TableItemDelegate

# 操作列链接数据角色（+2 起避开勾选列 id 锚 UserRole 与统计弹窗 UserRole+1）
LINKS_ROLE = int(Qt.ItemDataRole.UserRole) + 2

# 文本内边距与 LeanTableDelegate 一致，保证操作列与其余列左边线对齐
_INSET_L = 15
_INSET_R = 9
_GAP = 14           # 链接间距（原按钮间距 4px 太挤，文字化后需拉开防误触）
_HIT_PAD = 5        # 命中矩形左右放宽
_FONT_SCALE = 0.92  # 链接字号 = 表格字号 × 0.92（对齐原 12px 按钮字观感）

# 色键 → 颜色（沿用原按钮 QSS 的三套语义色，深浅主题各一份）
_GHOST_FG = ("#c8d0dc", "#333333")


def _link_color(key: str, dark: bool) -> QColor:
    if key == "primary":
        return QColor(current_accent_hex())
    if key == "danger":
        return QColor(SEMANTIC["danger"])
    return QColor(_GHOST_FG[1 if dark else 0])


class _OpsLinkMixin:
    """为表格委托追加「单元格内文字链接」的绘制与命中（不含任何子控件）"""

    # 类级默认值：即便未走 init_ops_links 也不会 AttributeError
    _ops_handler = None
    _ops_hooked = False
    _ops_watch = None       # 已装事件过滤器的视口
    _hover_link = None      # (row, col, 链接序)
    _hover_rect = None      # 上一次高亮所在矩形，用于精准局部重绘
    _pressed_link = None    # 左键按下时命中的链接（与抬起配对才触发动作）
    _link_font_key = None
    _link_font = None
    _link_fm = None
    _color_cache = None

    # ---------------- 装配 ----------------

    def init_ops_links(self, handler=None):
        """绑定点击回调 ``handler(row, col, action_key)``，挂主题失效与悬停过滤

        两个信号都要接：``themeChanged`` 只跟深/浅模式，强调色（primary 链接色）
        更换走 ``themeColorChanged``（口径同 core.theme_qss.apply_window_qss）。
        """
        self._ops_handler = handler
        self._watch_viewport(True)
        if not self._ops_hooked:
            self._ops_hooked = True
            for sig in (getattr(qconfig, "themeChanged", None),
                        getattr(qconfig, "themeColorChanged", None)):
                try:
                    if sig is not None:
                        sig.connect(self._ops_invalidate)
                except Exception:
                    pass

    def _ops_invalidate(self, *_args):
        """主题切换：颜色缓存作废并整表重绘（链接色随之更新）"""
        self._color_cache = None
        vp = self._viewport()
        if vp is not None:
            vp.update()

    def _viewport(self):
        view = self.parent()
        try:
            return view.viewport() if view is not None else None
        except Exception:
            return None

    # ---------------- 字体与字测（按基础字体缓存一份） ----------------

    def _font_for(self, option):
        base = option.font
        key = (base.family(), base.pointSizeF(), base.bold())
        if self._link_font_key != key:
            f = QFont(base)
            f.setPointSizeF(base.pointSizeF() * _FONT_SCALE)
            self._link_font_key = key
            self._link_font = f
            self._link_fm = QFontMetrics(f)
        return self._link_font, self._link_fm

    def _color(self, key, dark):
        if self._color_cache is None:
            self._color_cache = {}
        ck = (key, dark)
        c = self._color_cache.get(ck)
        if c is None:
            c = _link_color(key, dark)
            self._color_cache[ck] = c
        return c

    # ---------------- 几何：绘制与命中共用唯一算式 ----------------

    def _link_geom(self, option, links):
        """单元格矩形 → [(文案, 色键, 动作键, 绘制矩形)]

        窄列先收缩间距，仍放不下则省略号截断末段（命中矩形同步收缩）。
        """
        font, fm = self._font_for(option)
        rect = option.rect
        right = rect.right() - _INSET_R
        x = rect.left() + _INSET_L
        # 取链接三元组的文案段（直接 str(link) 会把整个 tuple 画出来）
        texts = [str(link[0]) for link in links]
        widths = [fm.horizontalAdvance(t) for t in texts]
        n = len(links)
        gap = _GAP
        avail = right - x
        if sum(widths) + gap * (n - 1) > avail:
            gap = max(4, int((avail - sum(widths)) / max(1, n - 1)))
        y = rect.center().y() - fm.height() // 2
        out = []
        for i, link in enumerate(links):
            w = widths[i]
            text = texts[i]
            if x + w > right:  # 末段放不下：截断并收尾
                w = max(0, right - x)
                text = fm.elidedText(text, Qt.TextElideMode.ElideRight, w)
                out.append((text, link[1], link[2] if len(link) > 2 else "",
                            QRect(x, y, w, fm.height())))
                break
            out.append((text, link[1], link[2] if len(link) > 2 else "",
                        QRect(x, y, w, fm.height())))
            x += w + gap
        return out

    @staticmethod
    def _links_of(index):
        links = index.data(LINKS_ROLE)
        if isinstance(links, (list, tuple)) and links:
            return links
        return None

    # ---------------- 绘制 ----------------

    def paint(self, painter, option, index):
        # 基类负责背景/整行 hover/选中指示条/勾选框/普通文本（链接格文本为空）
        super().paint(painter, option, index)
        links = self._links_of(index)
        if links is None:
            return
        dark = bool(isDarkTheme())
        hover = self._hover_link
        hovered = (hover[2] if hover is not None
                   and (hover[0], hover[1]) == (index.row(), index.column())
                   else -1)
        font, _fm = self._font_for(option)
        geom = self._link_geom(option, links)
        painter.save()
        painter.setClipRect(option.rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setFont(font)
        for i, (text, ckey, _act, r) in enumerate(geom):
            painter.setPen(QPen(self._color(ckey, dark)))
            painter.drawText(
                r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                text)
            if i == hovered:  # 悬停段加下划线，替代原按钮的底色反馈
                painter.drawLine(r.left(), r.bottom(), r.right(), r.bottom())
        painter.restore()

    # ---------------- 命中与事件 ----------------

    def _hit(self, option, links, pos):
        """鼠标点 → 链接序（-1=未命中）；纵向放开到整格高，比按钮更好点"""
        pad = min(_HIT_PAD, max(2, _GAP // 2))
        rect = option.rect
        for i, (_t, _c, _a, r) in enumerate(self._link_geom(option, links)):
            hit = QRect(r.left() - pad, rect.top(),
                        r.width() + pad * 2, rect.height())
            if hit.contains(pos):
                return i
        return -1

    @staticmethod
    def _event_pos(event):
        try:
            return event.position().toPoint()
        except AttributeError:
            return event.pos()

    def _set_hover(self, want, rect=None):
        """切换悬停段：局部重绘旧/新矩形 + 手型光标，避免整视口刷新

        光标只在「命中/离开」切换时改一次（原按钮的手型来自 QPushButton 自身，
        文字化后需委托自行维护），不进逐帧路径。

        rect 必须拷贝后再存：``option`` 由 C++ 侧按事件临时构造，事件处理返回
        即析构，而 PySide6 的 ``option.rect`` 返回的是其内部 QRect 的引用，
        直接挂到 self 上会在下次使用时报
        ``Internal C++ object (PySide6.QtCore.QRect) already deleted``。
        外扩 2px 是为了盖住下划线（重绘矩形由本模块独享，宁大勿小）。
        """
        if self._hover_link == want:
            return
        old = self._hover_rect
        new = (QRect(rect).adjusted(-2, -2, 2, 2)
               if (rect is not None and want is not None) else None)
        self._hover_link = want
        self._hover_rect = new
        vp = self._viewport()
        if vp is None:
            return
        vp.setCursor(Qt.CursorShape.PointingHandCursor if want is not None
                     else Qt.CursorShape.ArrowCursor)
        if old is not None:
            vp.update(old)
        if new is not None:
            vp.update(new)

    def _apply_hover(self, index, option, pos):
        """按坐标更新悬停段，返回是否命中（过滤器与 editorEvent 共用）"""
        links = self._links_of(index)
        if links is None:
            self._set_hover(None)
            return False
        i = self._hit(option, links, pos)
        self._set_hover(None if i < 0 else (index.row(), index.column(), i),
                        option.rect)
        return i >= 0

    # ---------------- 悬停通道：直接过滤视口鼠标事件 ----------------

    def _watch_viewport(self, on):
        """在视口上装/卸事件过滤器（同一个视口不重复装）"""
        vp = self._viewport()
        if vp is None:
            return
        if on:
            if self._ops_watch is not vp:
                self._unwatch_viewport()
                vp.installEventFilter(self)
                self._ops_watch = vp
        else:
            self._unwatch_viewport()

    def _unwatch_viewport(self):
        vp = self._ops_watch
        self._ops_watch = None
        if vp is not None:
            try:
                vp.removeEventFilter(self)
            except Exception:
                pass

    def teardown_ops_links(self):
        """委托被新实例取代时调用：摘掉过滤器，避免残留实例继续响应事件"""
        self._unwatch_viewport()
        self._ops_handler = None

    def eventFilter(self, obj, event):
        """无按键悬停反馈 + 吞掉落在链接上的双击

        为何要自己装过滤器而不依赖委托回调：
        * ``hoverEvent`` 在 PySide6 的委托上根本不存在，且需视口 WA_Hover（本
          仓库 TableWidget 为 False）；
        * ``editorEvent`` 是否收到 MouseMove 各 Qt 版本口径不一（多数只在按下/
          双击或拖选时转发），不能当作悬停的唯一通道。
        视口本身开了鼠标跟踪（实测 True），直接吃它的 MouseMove/Leave 与
        Qt 版本解耦；editorEvent 里的同名处理保留作补集（两路幂等，不重复重绘）。

        双击必须在过滤器层面吞：视图是先 ``emit doubleClicked(index)`` 再过问
        委托，靠 editorEvent 返回 True 只挡得住编辑器弹出，挡不住「整行双击=
        编辑」（原 cellWidget 按钮在控件层面就吃掉了事件，此处对齐）。
        """
        if obj is not self._ops_watch:
            return super().eventFilter(obj, event)
        et = event.type()
        if et == QEvent.Type.MouseMove:
            spot = self._link_cell(obj, self._event_pos(event))
            if spot is None:
                self._set_hover(None)
            else:
                self._apply_hover(spot[0], spot[1], self._event_pos(event))
        elif et == QEvent.Type.Leave:
            self._set_hover(None)
        elif et == QEvent.Type.MouseButtonDblClick:
            pos = self._event_pos(event)
            spot = self._link_cell(obj, pos)
            if spot is not None and self._hit(spot[1],
                                              self._links_of(spot[0]), pos) >= 0:
                event.accept()
                return True  # 视图看不到这次双击：不触发行编辑，也不重复动作
        return super().eventFilter(obj, event)

    def _link_cell(self, vp, pos):
        """视口坐标 → (index, option)；不在链接格上返回 None

        自建 option 只填 rect（视口坐标，与 pos 同系）与字体，成本极低；
        字体取视口的（与 ``viewOptions().font`` 一致，保证命中矩形与绘制对齐）。
        """
        view = self.parent()
        if view is None:
            return None
        try:
            index = view.indexAt(pos)
        except Exception:
            return None
        if not index.isValid() or self._links_of(index) is None:
            return None
        option = QStyleOptionViewItem()
        option.rect = view.visualRect(index)
        option.font = vp.font()
        return index, option

    def editorEvent(self, event, model, option, index):
        links = self._links_of(index) if index.isValid() else None
        if links is None:
            self._set_hover(None)
            self._pressed_link = None
            return super().editorEvent(event, model, option, index)
        et = event.type()
        pos = self._event_pos(event)
        spot = (index.row(), index.column())
        if et == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                i = self._hit(option, links, pos)
                # 只记账，事件照常放行（整行选中/滚动不受影响）
                self._pressed_link = (spot[0], spot[1], i) if i >= 0 else None
            else:
                self._pressed_link = None
        elif et == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton:
                i = self._hit(option, links, pos)
                pressed = self._pressed_link
                self._pressed_link = None
                if i >= 0 and pressed == (spot[0], spot[1], i):
                    # 与 QPushButton 同语义：按下+抬起落在同一段才触发，
                    # 避免跨行拖选、或在别处按下本格抬起时误发操作
                    cb = self._ops_handler
                    if cb is not None:
                        cb(spot[0], spot[1], links[i][2])
                    return True
        elif et == QEvent.Type.MouseButtonDblClick:
            if self._hit(option, links, pos) >= 0:
                # 兼容补集：正常情况下落在链接上的双击已在 eventFilter 被吞，
                # 这里只防过滤器未装上的极端场景（不挡 doubleClicked 信号）
                return True
        elif et == QEvent.Type.MouseMove:
            # 补集：部分 Qt 版本会把无按键移动也转给委托，逻辑幂等
            self._apply_hover(index, option, pos)
        elif et == QEvent.Type.Leave:
            self._set_hover(None)
        return super().editorEvent(event, model, option, index)


class OpsLeanDelegate(_OpsLinkMixin, LeanTableDelegate):
    """P0-1 轻量文本绘制 + 操作列文字链接（默认形态）"""


class OpsPlainDelegate(_OpsLinkMixin, TableItemDelegate):
    """库委托绘制 + 操作列文字链接（轻量委托关闭时的回退形态）"""


_KEEP = object()  # 「沿用现有回调」哨兵


def _lean_enabled() -> bool:
    try:
        from core.perf import is_lean_delegate_enabled
        return bool(is_lean_delegate_enabled())
    except Exception:
        return True


def install_ops_links(table, handler=_KEEP):
    """把表格当前委托换成带操作链接的同族委托，返回新委托

    ``handler(row, col, action_key)`` 为链接点击回调；不传则沿用现有委托的
    回调（用于性能开关切换后的重建）。经 ``table.setItemDelegate`` 安装：
    qfluentwidgets 的 ``TableBase`` 重写了该方法，会同步替换 ``self.delegate``，
    hover/selected 状态推送随之指向新实例。
    """
    lean = _lean_enabled()
    cls = OpsLeanDelegate if lean else OpsPlainDelegate
    cur = getattr(table, "delegate", None)
    if isinstance(cur, cls):
        if handler is not _KEEP:
            cur.init_ops_links(handler)
        return cur
    new = cls(table)
    new.init_ops_links(None if handler is _KEEP else handler)
    if handler is _KEEP and isinstance(cur, _OpsLinkMixin):
        new._ops_handler = cur._ops_handler
    # 迁移视图推给委托的行状态，避免切换瞬间高亮丢失
    if cur is not None:
        try:
            new.hoverRow = cur.hoverRow
            new.pressedRow = cur.pressedRow
            new.selectedRows = set(cur.selectedRows)
            new.lightCheckedColor = cur.lightCheckedColor
            new.darkCheckedColor = cur.darkCheckedColor
        except Exception:
            pass
    table.setItemDelegate(new)
    if isinstance(cur, _OpsLinkMixin):
        cur.teardown_ops_links()   # 旧实例不再收视口事件（委托是 table 的子对象）
    return new


def rebuild_ops_delegate(table):
    """core.perf 全局刷新委托时调用

    返回 True 表示该表的委托归本模块管（已按新开关就位，调用方请跳过），
    False 表示该表没装操作链接委托，交给通用逻辑处理。
    """
    cur = getattr(table, "delegate", None)
    if not isinstance(cur, _OpsLinkMixin):
        return False
    lean = _lean_enabled()
    if lean is isinstance(cur, OpsLeanDelegate):
        return True  # 形态已与开关一致，不必重建
    install_ops_links(table)
    vp = table.viewport()
    if vp is not None:
        vp.update()
    return True
