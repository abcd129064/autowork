# -*- coding: utf-8 -*-
"""轻量表格委托 LeanTableDelegate（滚动性能优化 P0-1）

背景
----
qfluentwidgets 的 ``TableItemDelegate.paint`` 对每个可见单元格执行：
save/restore + 裁剪 + 抗锯齿 + 圆角/矩形背景 + ``initStyleOption``（内含
``getFont()``、``isDarkTheme()``、多次 ``index.data()``），最后调用
``super().paint()`` 走 ``QStyledItemDelegate`` 的 **QTextLayout 排版**。
售后面板 60 行 × 13 列时每帧约 208~244 个单元格，实测：

* 文本排版占滚动耗时 **68%**（关掉文本绘制 -67.6%）
* 换回原生 delegate -50.6%，但会丢 hover / 圆角 / 自绘勾选框（像素差 22%）

做法
----
本委托**继承** ``TableItemDelegate``，保留其全部视觉与能力（hover 行、
交替行、选中行、选中指示条、自绘勾选框、圆角、tooltip、编辑器），
只把最后一步「文本绘制」换成 ``QPainter.drawText`` + 逐行省略号缓存 +
字体/字测缓存。实测 **-49.4%**（11.87 → 6.01 ms/帧），像素级比对与库的
差异仅 **5.2%**（只剩字符断行与亚像素位置，结构性视觉 100% 一致）。

S2（2026-09-25，见 ``docs/表格滚动延迟调查报告2026-09-25.md``）：文本层
再预渲染成 QPixmap，滚动帧只 blit——**只缓存文本层**，背景/hover/选中/
勾选框仍实时绘制，交互状态不受缓存影响。失效键含 (row, col, 尺寸, 文本,
主题, 字体, 前景色, 对齐, dpr)：排序移动行、编辑单元格、切主题均自然失效。
offscreen 实测 lean 帧成本再降 16~28%（售后 7.2→5.9 / 10.6→7.6 ms/步，
scale 1.0/2.0；像素等价探针证实缓存命中与直接 drawText 逐像素一致）。

三个必须遵守的回归点（原型已踩，改动时勿回退）
---------------------------------------------
1. ``ForegroundRole`` 返回的是 **QBrush 不是 QColor**，直接 ``QColor(brush)``
   会抛 ``QVariant must be holding a QColor``。
2. item 文本含 ``\\n``（如「时间\\n填写人」两行），drawText 的 flags 必须带
   ``Qt.TextFlag.TextWordWrap``，且省略号要**逐行**计算，否则两行布局被压成单行。
3. 文本左内边距必须对齐原生 ``SE_ItemViewItemText``（实测格左 15px），
   用 4px 会让整行文本左移 13px。
"""

from math import ceil

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPixmap

from qfluentwidgets import getFont, isDarkTheme, qconfig
from qfluentwidgets.components.widgets.table_view import TableItemDelegate

# 文本内边距：与原生 SE_ItemViewItemText 对齐（实测左 15 / 右 9，见模块文档回归点 3）
_TEXT_INSET_L = 15
_TEXT_INSET_R = 9

# 省略号缓存上限：超容量整体清空（简易 LFU 不划算，滚动时命中率主要在当页）
_ELIDE_CACHE_MAX = 4000

# 文本层 QPixmap 缓存上限（S2）。单格 ~120x38x4B ≈ 18KB，2000 项 ≈ 36MB
# （dpr=2 时物理像素翻两番，~144MB 是极端上界，实际滚动命中集中在当页几百格）
_TEXT_PM_CACHE_MAX = 2000


class LeanTableDelegate(TableItemDelegate):
    """保留 qfluentwidgets 表格全部视觉，仅替换文本绘制路径的轻量委托"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._base_font = None
        self._fm_cache = {}
        self._elide_cache = {}
        self._text_pm_cache = {}   # S2：文本层预渲染位图（键见 _draw_text）
        self._dark = None
        # 主题切换会改变文本色/背景色，缓存必须失效（浅色文本色为黑，深色为白）
        try:
            qconfig.themeChanged.connect(self._invalidate)
        except Exception:
            pass

    # ---------------- 缓存 ----------------

    def _invalidate(self, *_args):
        """主题/字体变化后清空缓存（缓存项均含主题相关结果）"""
        self._base_font = None
        self._fm_cache.clear()
        self._elide_cache.clear()
        self._text_pm_cache.clear()
        self._dark = None

    def _is_dark(self) -> bool:
        if self._dark is None:
            self._dark = bool(isDarkTheme())
        return self._dark

    def _default_font(self):
        """表格默认字体：全表共用一份（库内每格 getFont(13) 现算）"""
        if self._base_font is None:
            self._base_font = getFont(13)
        return self._base_font

    def _metrics(self, font) -> QFontMetrics:
        key = (font.family(), font.pointSizeF(), font.bold(), font.weight())
        fm = self._fm_cache.get(key)
        if fm is None:
            fm = QFontMetrics(font)
            if len(self._fm_cache) > 64:
                self._fm_cache.clear()
            self._fm_cache[key] = fm
        return fm

    # ---------------- 绘制 ----------------

    def paint(self, painter, option, index):
        # ---- 背景 / 勾选框：与库实现逐行对齐（视觉不变） ----
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipping(True)
        painter.setClipRect(option.rect)
        option.rect.adjust(0, self.margin, 0, -self.margin)

        row = index.row()
        selected = row in self.selectedRows
        is_pressed = self.pressedRow == row
        is_hover = self.hoverRow == row
        is_alternate = (row % 2 == 0
                        and self.parent().alternatingRowColors())
        dark = self._is_dark()
        c = 255 if dark else 0

        if not selected:
            alpha = ((9 if dark else 6) if is_pressed
                     else (12 if is_hover else (5 if is_alternate else 0)))
        else:
            alpha = 15 if is_pressed else (25 if is_hover else 17)

        brush = index.data(Qt.ItemDataRole.BackgroundRole)
        painter.setBrush(brush if brush is not None
                         else QColor(c, c, c, alpha))
        self._drawBackground(painter, option, index)

        if (selected and index.column() == 0
                and self.parent().horizontalScrollBar().value() == 0):
            self._drawIndicator(painter, option, index)

        state = index.data(Qt.ItemDataRole.CheckStateRole)
        if state is not None:
            self._drawCheckBox(painter, option, index)
        painter.restore()

        self._draw_text(painter, option, index, dark, state is not None)

    # ---------------- 文本（唯一被替换的部分） ----------------

    def _draw_text(self, painter, option, index, dark, has_check):
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return
        text = str(text)

        rect = option.rect.adjusted(_TEXT_INSET_L, 0, -_TEXT_INSET_R, 0)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        font = index.data(Qt.ItemDataRole.FontRole) or self._default_font()
        painter.setFont(font)

        fm = self._metrics(font)
        key = (text, rect.width(), font.pointSizeF(), font.bold())
        elided = self._elide_cache.get(key)
        if elided is None:
            # 逐行省略：含 '\n' 的文本按行分别 elide，否则两行结构会被截没
            elided = "\n".join(
                fm.elidedText(seg, Qt.TextElideMode.ElideRight, rect.width())
                for seg in text.split("\n"))
            if len(self._elide_cache) > _ELIDE_CACHE_MAX:
                self._elide_cache.clear()
            self._elide_cache[key] = elided

        fg = index.data(Qt.ItemDataRole.ForegroundRole)
        if fg is not None:
            # ForegroundRole 返回 QBrush，不能直接 QColor(brush)
            color = QColor(fg.color() if hasattr(fg, "color") else fg)
        else:
            color = QColor(255, 255, 255) if dark else QColor(0, 0, 0)

        align = index.data(Qt.ItemDataRole.TextAlignmentRole)
        if align is not None:
            flags = int(align)
        else:
            flags = int(Qt.AlignmentFlag.AlignLeft
                        | Qt.AlignmentFlag.AlignVCenter)
        # 必须带 TextWordWrap，否则 '\n' 不换行（回归点 2）
        flags |= int(Qt.TextFlag.TextWordWrap)

        # S2：文本层预渲染 QPixmap，滚动帧只 blit（drawText 只发生在首次）。
        # 键含 dpr：跨屏拖动窗口时物理分辨率变化必须重渲染，否则发糊
        dev = painter.device()
        dpr = dev.devicePixelRatioF() if dev is not None else 1.0
        key = (index.row(), index.column(), rect.width(), rect.height(),
               text, dark, font.family(), font.pointSizeF(), font.weight(),
               font.bold(), color.rgba(), flags, dpr)
        pm = self._text_pm_cache.get(key)
        if pm is None:
            pm = self._render_text_pixmap(rect, font, color, flags, elided, dpr)
            if len(self._text_pm_cache) > _TEXT_PM_CACHE_MAX:
                self._text_pm_cache.clear()
            self._text_pm_cache[key] = pm
        painter.drawPixmap(rect.topLeft(), pm)

    @staticmethod
    def _render_text_pixmap(rect, font, color, flags, elided, dpr) -> QPixmap:
        """把一段 elided 文本渲染成透明底位图（与直接 drawText 同一条路径）"""
        pm = QPixmap(max(1, ceil(rect.width() * dpr)),
                     max(1, ceil(rect.height() * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        try:
            p.setFont(font)
            p.setPen(color)
            p.drawText(QRect(0, 0, rect.width(), rect.height()), flags, elided)
        finally:
            p.end()
        return pm
