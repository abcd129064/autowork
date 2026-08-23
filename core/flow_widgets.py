# -*- coding: utf-8 -*-
"""流式工具栏共享组件：与主界面工具栏同一范式（qfluentwidgets FlowLayout 换行
+ 高度自适应滚动容器），供主界面与售后面板等复用，严禁控件重叠。"""

from PySide6.QtWidgets import QScrollArea, QSizePolicy, QFrame


class FlowToolbarScrollArea(QScrollArea):
    """工具栏专用滚动区域：根据自身宽度主动计算内容高度并锁定，
    保证父布局精确按内容高度分配空间（单行=单行高，折行=多行高，超上限滚动）。
    视口透明、无边框，工具栏融入页面背景，不出现灰色底色条。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # 高度上限（约 2~3 行控件），setFixedHeight 会覆盖 maximumHeight，
        # 因此用独立变量保存上限，避免窗口反复缩放时上限被“棘轮”压低
        self._height_cap = self.maximumHeight()
        # 垂直策略设为 Preferred（配合 HFW 标志，作为首次布局的兜底）
        sp = self.sizePolicy()
        sp.setVerticalPolicy(QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        # 透明化处理下沉到组件：主界面与售后面板共用，避免灰色视口底色。
        # 不依赖 objectName（调用方可能覆写）：setStyleSheet 只作用于自身子树，
        # 视口单独设透明。
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.viewport().setStyleSheet("background: transparent;")

    def setMaximumHeight(self, h):
        """同步更新高度上限"""
        self._height_cap = h
        super().setMaximumHeight(h)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        w = self.widget()
        if w is None:
            return super().heightForWidth(width)
        margins = self.contentsMargins()
        sb = self.verticalScrollBar()
        sb_w = sb.width() if sb.isVisible() else 0
        inner_w = max(0, width - margins.left() - margins.right() - sb_w)
        h = w.heightForWidth(inner_w)
        return min(h, self._height_cap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adjust_height()

    def _adjust_height(self):
        """按当前宽度计算流式内容实际高度，锁定自身高度，下方内容紧贴无空白"""
        w = self.widget()
        if w is None or self.width() <= 0:
            return
        margins = self.contentsMargins()
        sb = self.verticalScrollBar()
        sb_w = sb.width() if sb.isVisible() else 0
        inner_w = max(1, self.width() - margins.left() - margins.right() - sb_w)
        h = min(w.heightForWidth(inner_w), self._height_cap)
        if h > 0 and h != self.height():
            self.setFixedHeight(h)
