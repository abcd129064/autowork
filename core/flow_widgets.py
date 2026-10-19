# -*- coding: utf-8 -*-
"""流式工具栏共享组件：与主界面工具栏同一范式（qfluentwidgets FlowLayout 换行
+ 高度自适应滚动容器），供主界面与售后面板等复用，严禁控件重叠。

滚动区域只能按内容 sizeHint 给高度，而流式布局折行后 sizeHint 不变，
不重写 heightForWidth 把高度接进布局，第二行控件必被裁掉。
"""

from PySide6.QtWidgets import QScrollArea, QSizePolicy, QFrame


class FlowToolbarScrollArea(QScrollArea):
    """工具栏专用滚动区域：根据自身宽度主动计算内容高度并锁定，
    保证父布局精确按内容高度分配空间（单行=单行高，折行=多行高，超上限滚动）。
    视口透明、无边框，工具栏融入页面背景，不出现灰色底色条。

    不用裸 QScrollArea 的原因：它的高度取自内容 sizeHint，不随宽度重算，
    窗口一窄就只留一行高度，折行内容直接被裁掉。
    """

    def __init__(self, parent=None):
        """建立透明无边框外观，并接管高度上限的维护权。"""
        super().__init__(parent)
        # 高度上限单独存一份：setFixedHeight 会覆盖 maximumHeight 且用过后无法回调，
        # 不自己维护，窗口反复缩放时上限会被上一次的高度「棘轮」压死
        self._height_cap = self.maximumHeight()
        # 垂直策略设为 Preferred 并打开 HFW 标志，让首次布局也有合理兜底，
        # 不会在 _adjust_height 之前出现瞬间撑满或塌成 0 高
        sp = self.sizePolicy()
        sp.setVerticalPolicy(QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        # 透明化下沉到组件：主界面与售后面板共用。不挂 objectName，调用方可能覆写它；
        # 样式表只作用于自身子树，视口是独立子窗口，必须单独再设一次透明
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.viewport().setStyleSheet("background: transparent;")

    def setMaximumHeight(self, h):
        """重写以同步高度上限：父布局设置的 maximumHeight 也要参与钳制"""
        self._height_cap = h
        super().setMaximumHeight(h)

    def hasHeightForWidth(self):
        # 声明支持「给定宽度反算高度」，父布局才能按内容而不是按拉伸分配空间
        return True

    def heightForWidth(self, width):
        # 反算高度供布局系统查询：减去边距与可见滚动条宽度后交给内容控件，
        # 再用 _height_cap 钳制，超过上限时交由滚动条处理
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
        # 宽度一变，FlowLayout 的折行数就可能变，必须重算高度
        self._adjust_height()

    def _adjust_height(self):
        """按当前宽度计算流式内容实际高度，锁定自身高度，下方内容紧贴无空白"""
        w = self.widget()
        if w is None or self.width() <= 0:
            # 尚未 setWidget 或窗口最小化到 0 宽，跳过以免算出负高度
            return
        margins = self.contentsMargins()
        sb = self.verticalScrollBar()
        sb_w = sb.width() if sb.isVisible() else 0
        # 内宽下限取 1：窗口极窄时 宽度-边距-滚动条 可能小于等于 0，
        # 传给 FlowLayout 会算成「每行放不下任何控件」，排成 N 行把高度算爆
        inner_w = max(1, self.width() - margins.left() - margins.right() - sb_w)
        h = min(w.heightForWidth(inner_w), self._height_cap)
        # 只在高度真的变化时 setFixedHeight：无变化也调用会触发一次多余重排
        if h > 0 and h != self.height():
            self.setFixedHeight(h)
