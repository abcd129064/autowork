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

S5（2026-09-25，同报告 §三）：整格（背景+勾选框+文本）预渲染为 QPixmap，
滚动帧每格只剩 1 次 ``drawPixmap``——针对「列多更卡」：S2 后每格仍有
固定开销（背景填充 + 6 次 ``index.data`` 之后的缓存键构建已被并入单键，
背景填充/勾选框/文本 blit 三合一）。

**状态并入缓存键而非 blit 后叠加填充**（对报告 S5 原案的一处修正）：叠加
方案会把行状态 tint 压在文本/勾选框**之上**，而直绘路径里背景在文本
**之下**——绘制顺序不一致，逐字节等价不成立（彩色前景文本/主题色勾选框
会被 tint 洗色）。状态变体键（每种行状态各渲一份）以极小的缓存条目
开销（滚动帧恒为 base 变体；hover/选中行首次各渲一次）保持**全状态逐
字节等价**，且滚动帧纯 blit、零填充成本。失效键 = S2 键 + 背景角色 +
行状态四元组(选中/按压/悬停/交替) + 勾选状态 + 列总数（首末列圆角形状）。

回退直绘（不缓存）：非 solid 背景刷（渐变/纹理）、内矩形退化、键构建
异常——直绘路径即 S2 时代的完整实现，保证任何场景视觉正确。

三个必须遵守的回归点（原型已踩，改动时勿回退）
---------------------------------------------
1. ``ForegroundRole`` 返回的是 **QBrush 不是 QColor**，直接 ``QColor(brush)``
   会抛 ``QVariant must be holding a QColor``。
2. item 文本含 ``\\n``（如「时间\\n填写人」两行），drawText 的 flags 必须带
   ``Qt.TextFlag.TextWordWrap``，且省略号要**逐行**计算，否则两行布局被压成单行。
3. 文本左内边距必须对齐原生 ``SE_ItemViewItemText``（实测格左 15px），
   用 4px 会让整行文本左移 13px。

S5 附加回归点
------------
4. ``option.rect`` 的 margin 原地调整语义必须两条路径一致——
   ``OpsLeanDelegate.paint`` 在 ``super().paint()`` 之后按**内矩形**画操作
   列链接（core/ops_link_delegate.py），快路径若不改 ``option.rect`` 会让
   链接几何整体错位 2px。
5. ``_drawIndicator`` 依赖 ``pressedRow``（按压时指示条更高）且受
   ``horizontalScrollBar().value()==0`` 门控——恒每帧直画，不进缓存。
6. ``_drawCheckBox`` 只依赖 CheckStateRole + 主题（qfw 源码实证）——可安全
   进缓存，勾选状态在键里；``setCheckedColor`` 运行时换色会先清空缓存。
7. 含勾选框的整格缓存存在**固有的 ±1 通道微差**（已证实为预乘 alpha
   双重舍入，与光栅化坐标无关）：直绘把主题色 1px 笔刷圆角矩形的 AA
   边缘**一次融合**合成到不透明视口（单次舍入）；缓存 = 透明 QPixmap
   预乘量化 + blit 源叠加**两步舍入**。隔离实验（仅 drawRoundedRect 笔刷）
   复现完全相同的 23/4560 像素（0.5%，全部 ±1/255）——SVG 对勾反而
   字节精确。文本 / 背景 tint（RGB∈{0,255}×整数 alpha 的预乘无舍入
   损失）/ 不透明自定义背景（纯拷贝）均逐字节等价；半透明自定义底色
   （alpha∉{0,255}）同族风险 → 强制直绘不缓存。勾选格用容差断言
   （tests/test_lean_cell_cache.py）。整格仍以满格原点 + 直绘同序列
   复刻（clip=满格 → rect 变异 → 背景 → 勾选 → 文本）渲染：与直绘
   同构、跨滚动位置渲染稳定（同一格不随滚动位置闪烁）。
"""

from math import ceil

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QPainter, QPixmap
from PySide6.QtWidgets import QStyleOptionViewItem

from qfluentwidgets import getFont, isDarkTheme, qconfig
from qfluentwidgets.components.widgets.table_view import TableItemDelegate

# 文本内边距：与原生 SE_ItemViewItemText 对齐（实测左 15 / 右 9，见模块文档回归点 3）
_TEXT_INSET_L = 15
_TEXT_INSET_R = 9

# 省略号缓存上限：超容量整体清空（简易 LFU 不划算，滚动时命中率主要在当页）
_ELIDE_CACHE_MAX = 4000

# 文本层 QPixmap 缓存上限（S2，仅回退直绘路径使用）。单格 ~120x38x4B ≈ 18KB，
# 2000 项 ≈ 36MB（dpr=2 时物理像素翻两番，~144MB 是极端上界，实际滚动命中集中在当页几百格）
_TEXT_PM_CACHE_MAX = 2000

# 整格 QPixmap 缓存上限（S5）。整格 ≈ 120x34x4B ≈ 16KB；滚动帧恒为 base
# 变体，hover/选中/按压变体只对状态行各渲一次，活跃条目 ≈ 可见格数 + 少量
# 状态行；2000 项 ≈ 32MB（dpr=2 极端上界 ~128MB，超限整体清空自愈）
_CELL_PM_CACHE_MAX = 2000


class LeanTableDelegate(TableItemDelegate):
    """保留 qfluentwidgets 表格全部视觉，仅替换文本绘制路径的轻量委托"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._base_font = None
        self._fm_cache = {}
        self._elide_cache = {}
        self._text_pm_cache = {}   # S2：文本层预渲染位图（键见 _draw_text，仅回退路径）
        self._cell_pm_cache = {}    # S5：整格预渲染位图（键见 _paint_from_cache）
        self._dark = None
        # 主题切换会改变文本色/背景色，缓存必须失效（浅色文本色为黑，深色为白）
        try:
            qconfig.themeChanged.connect(self._invalidate)
        except Exception:
            pass

    def setCheckedColor(self, light, dark):
        """勾选框配色变化 → 缓存里的勾选框会过期，先清空再换色"""
        self._invalidate()
        super().setCheckedColor(light, dark)

    # ---------------- 缓存 ----------------

    def _invalidate(self, *_args):
        """主题/字体变化后清空缓存（缓存项均含主题相关结果）"""
        self._base_font = None
        self._fm_cache.clear()
        self._elide_cache.clear()
        self._text_pm_cache.clear()
        self._cell_pm_cache.clear()
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

    @staticmethod
    def _bg_alpha(selected, pressed, hover, alternate, dark) -> int:
        """行背景 alpha（直绘与整格缓存共用唯一算式，防两路口径漂移）"""
        if not selected:
            if pressed:
                return 9 if dark else 6
            if hover:
                return 12
            return 5 if alternate else 0
        if pressed:
            return 15
        if hover:
            return 25
        return 17

    # ---------------- 绘制 ----------------

    def paint(self, painter, option, index):
        # S5：整格缓存命中 → blit + 每帧指示条；未命中/不适合缓存（非
        # solid 背景刷、退化矩形、键构建异常）→ S2 时代的完整直绘路径
        if self._paint_from_cache(painter, option, index):
            return
        self._paint_direct(painter, option, index)

    def _paint_from_cache(self, painter, option, index) -> bool:
        """S5：整格（背景+勾选框+文本）预渲染缓存路径

        滚动帧恒命中 base 变体（纯 blit）；行状态（选中/按压/悬停）作为
        键的一部分各渲一份变体——与直绘共享 ``_bg_alpha`` 算式，全状态
        逐字节等价。返回 False 表示本格走直绘（调用方负责）。
        """
        try:
            row, col = index.row(), index.column()
            full = QRect(option.rect)
            w, h = full.width(), full.height()
            if w <= 0 or h <= 0:
                return False

            text = index.data(Qt.ItemDataRole.DisplayRole)
            text = str(text) if text else ""
            font = index.data(Qt.ItemDataRole.FontRole) or self._default_font()
            fg = index.data(Qt.ItemDataRole.ForegroundRole)
            dark = self._is_dark()
            if fg is not None:
                # ForegroundRole 返回 QBrush，不能直接 QColor(brush)（回归点 1）
                color = QColor(fg.color() if hasattr(fg, "color") else fg)
            else:
                color = QColor(255, 255, 255) if dark else QColor(0, 0, 0)
            align = index.data(Qt.ItemDataRole.TextAlignmentRole)
            flags = (int(align) if align is not None
                     else int(Qt.AlignmentFlag.AlignLeft
                              | Qt.AlignmentFlag.AlignVCenter))
            flags |= int(Qt.TextFlag.TextWordWrap)  # 回归点 2

            raw_check = index.data(Qt.ItemDataRole.CheckStateRole)
            if raw_check is None:
                check_key = None
            elif isinstance(raw_check, Qt.CheckState):
                check_key = raw_check.value
            else:
                check_key = int(raw_check)

            selected = row in self.selectedRows
            is_pressed = self.pressedRow == row
            is_hover = self.hoverRow == row
            is_alternate = (row % 2 == 0
                            and self.parent().alternatingRowColors())

            bg = index.data(Qt.ItemDataRole.BackgroundRole)
            if bg is None:
                # 无自定义背景刷：行状态进键（状态 tint 由 _bg_alpha 算出）
                bg_key = None
                state_key = (selected, is_pressed, is_hover, is_alternate)
                alpha = self._bg_alpha(selected, is_pressed, is_hover,
                                       is_alternate, dark)
            else:
                qbg = bg if isinstance(bg, QBrush) else QBrush(bg)
                if qbg.style() != Qt.BrushStyle.SolidPattern:
                    return False  # 渐变/纹理刷：直绘保证视觉正确
                if qbg.color().alpha() not in (0, 255):
                    # 半透明自定义底色：预乘两步舍入会引入 ±1（回归点 7
                    # 同族），直绘保逐字节等价
                    return False
                # 自定义背景刷不受行状态影响（与直绘一致），状态不进键防冗余变体
                bg_key = qbg.color().rgba()
                state_key = None
                alpha = 0

            dev = painter.device()
            dpr = dev.devicePixelRatioF() if dev is not None else 1.0
            key = (row, col, w, h, text, dark, font.family(),
                   font.pointSizeF(), font.weight(), font.bold(),
                   color.rgba(), flags, dpr, bg_key, check_key, state_key,
                   index.model().columnCount(index.parent()))
            pm = self._cell_pm_cache.get(key)
            if pm is None:
                pm = self._render_cell_pixmap(w, h, index, bg, dark,
                                               alpha, dpr)
                if len(self._cell_pm_cache) > _CELL_PM_CACHE_MAX:
                    self._cell_pm_cache.clear()
                self._cell_pm_cache[key] = pm
        except Exception:
            return False

        # ---- 命中：blit + 每帧选中指示条 ----
        # option.rect 的 margin 原地调整必须与直绘同语义（回归点 4：操作列
        # 链接几何在 super().paint() 之后按内矩形计算）
        option.rect.adjust(0, self.margin, 0, -self.margin)
        painter.save()
        painter.setClipping(True)
        painter.setClipRect(full)
        painter.drawPixmap(full.topLeft(), pm)
        if (selected and col == 0
                and self.parent().horizontalScrollBar().value() == 0):
            # 指示条依赖 pressedRow（高度）与横向滚动位置（回归点 5）：恒每帧
            painter.setPen(Qt.NoPen)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._drawIndicator(painter, option, index)
        painter.restore()
        return True

    def _render_cell_pixmap(self, w, h, index, bg, dark, alpha, dpr) -> QPixmap:
        """整格渲染进 QPixmap（满格尺寸，本地原点 = 格左上角）

        逐算子复刻直绘路径：clip=满格 → option.rect 变异为 margin 内矩形 →
        背景（行状态 alpha）→ 勾选框 → 文本（复用 ``_draw_text``，含 S2 文本
        层缓存）。满格原点 + 同序列复刻保证与直绘同构、跨滚动位置渲染稳定。
        已知偏差（回归点 7）：主题色勾选框笔刷的 AA 边缘经透明 pm 预乘 +
        blit 两步舍入，与直绘的单次融合存在 ±1/255 通道微差（隔离实验复现
        23/4560 像素，不可感知）；文本 / tint / 不透明背景的预乘数学精确、
        逐字节等价。margin 上下带保持透明（源叠加不覆盖底色）。
        """
        pm = QPixmap(max(1, ceil(w * dpr)), max(1, ceil(h * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        try:
            opt = QStyleOptionViewItem()
            opt.rect = QRect(0, 0, w, h)
            p.save()
            p.setPen(Qt.NoPen)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setClipping(True)
            p.setClipRect(QRect(0, 0, w, h))
            opt.rect.adjust(0, self.margin, 0, -self.margin)

            if bg is not None:
                p.setBrush(bg if isinstance(bg, QBrush) else QBrush(bg))
            else:
                c = 255 if dark else 0
                p.setBrush(QColor(c, c, c, alpha))
            self._drawBackground(p, opt, index)

            state = index.data(Qt.ItemDataRole.CheckStateRole)
            if state is not None:
                self._drawCheckBox(p, opt, index)
            p.restore()

            self._draw_text(p, opt, index, dark, state is not None)
        finally:
            p.end()
        return pm

    def _paint_direct(self, painter, option, index):
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

        alpha = self._bg_alpha(selected, is_pressed, is_hover,
                               is_alternate, dark)

        brush = index.data(Qt.ItemDataRole.BackgroundRole)
        painter.setBrush(brush if brush is not None
                         else QColor(255 if dark else 0,
                                     255 if dark else 0,
                                     255 if dark else 0, alpha))
        self._drawBackground(painter, option, index)

        if (selected and index.column() == 0
                and self.parent().horizontalScrollBar().value() == 0):
            self._drawIndicator(painter, option, index)

        state = index.data(Qt.ItemDataRole.CheckStateRole)
        if state is not None:
            self._drawCheckBox(painter, option, index)
        painter.restore()

        self._draw_text(painter, option, index, dark, state is not None)

    # ---------------- 文本（回退直绘路径的唯一文本出口） ----------------

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
