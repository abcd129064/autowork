# -*- coding: utf-8 -*-
"""Pivot 二级导航容器页 —— FluentWindow 完整版重构的降层基建

设计稿（design/fluent_window_proposal.html）的二级导航方案：
一级侧边导航 6 项（工作台/运维管理/售后/跑视频/远程会话/统计图表）+ 底部
（设置/关于），其中运维管理/售后/跑视频内含 Pivot 二级导航。

为什么用 Pivot 容器而不是嵌套 FluentWindow：
  实测（tools/verify_fluent_refactor.py B2）嵌套 FluentWindow 会渲染出
  第二个标题栏；降层为 QWidget + Pivot 后页面可用宽度完全恢复。

为什么子页面可以直接复用（零改造）：
  原 ManagementPanelWindow / AftersalePanelWindow / LedgerPanelWindow 的
  子页面本就是纯 QWidget；宿主换成主窗口后 `self.window()` 即主窗口，
  通过主窗口的属性别名（table_page / records_page ...）与
  `switch_to_page()` 路由保持既有调用语义。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QStackedWidget,
                               QHBoxLayout, QScrollArea, QFrame)
from qfluentwidgets import (Pivot, TitleLabel, CaptionLabel, CardWidget,
                            PushButton, setCustomStyleSheet, ToolButton,
                            FluentIcon)


class PivotPage(QWidget):
    """Pivot + QStackedWidget 二级导航容器

    子页面经 :meth:`addPage` 注册后：
      - Pivot 出现对应文本项（routeKey = 页面 objectName，必须非空且容器内唯一）
      - :meth:`switchTo` 与 FluentWindow.switchTo 同名，方便页面代码统一调用
      - 页面会带上 ``_hub_container`` 反向引用，主窗口 :meth:`switch_to_page`
        据此把「FluentWindow.switchTo(子页)」翻译为「切到本容器 + 容器内切换」

    「弹出面板」（2026-09-07 需求）：切换条右端的浮动按钮把本 Hub 复刻为
    独立窗口（即 FluentWindow 重构前主界面点导航弹出子面板的形态）。
    弹出窗口（main_window/hub_popout.HubPopoutWindow）内嵌的 Hub 实例
    由其构造方把 :attr:`btn_popout` 隐藏（窗口里再套一层弹出无意义）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("pivotPage")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        # 二级切换条固定宽度靠左（2026-09-06 需求）：不铺满整页宽度。
        # Pivot 自身 sizePolicy 为 Minimum，若直接 addWidget 会被布局拉伸到
        # 整页宽（各 item 被摊开）；包一层左对齐 HBox 并按内容锁定宽度。
        self.pivot = Pivot(self)
        self.stack = QStackedWidget(self)
        pivot_row = QHBoxLayout()
        pivot_row.setContentsMargins(24, 10, 0, 0)
        pivot_row.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        pivot_row.addWidget(self.pivot, 0, Qt.AlignLeft)
        lay.addLayout(pivot_row)
        lay.addWidget(self.stack, 1)
        self._pages = []

        # ---------- 弹出为独立窗口按钮（叠放在切换条行右端） ----------
        self.btn_popout = ToolButton(FluentIcon.FIT_PAGE, self)
        self.btn_popout.setToolTip("弹出面板：在本窗口外独立打开此面板")
        self.btn_popout.setAccessibleName("弹出面板")
        self.btn_popout.clicked.connect(self._on_popout)
        self.btn_popout.raise_()

    def _on_popout(self):
        win = self.window()
        fn = getattr(win, "open_hub_popout", None)
        if fn is not None:
            fn(self)

    def showEvent(self, event):
        super().showEvent(event)
        if self.btn_popout.isVisible():
            self._reposition_popout_btn()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.btn_popout.isVisible():
            self._reposition_popout_btn()

    def _reposition_popout_btn(self):
        """按钮贴页面右上角（垂直对齐 Pivot 切换条行中部）"""
        self.btn_popout.move(self.width() - self.btn_popout.width() - 16, 10)

    # ---------- 注册 ----------

    def addPage(self, page, text, icon=None):
        """注册子页面并生成 Pivot 项（objectName 同时作为 routeKey）"""
        if not page.objectName():
            raise ValueError(
                f"PivotPage.addPage: {type(page).__name__} 需要非空 objectName")
        self.stack.addWidget(page)
        page._hub_container = self

        def _go(*_args, w=page):
            # qfw PivotItem.itemClicked 以位置参数回传（bool/键名，版本相关），
            # 必须用 *args 吞掉，否则默认值 w=page 被覆盖 → setCurrentWidget(True)
            # → TypeError → 二级页面点击切换无响应（2026-09-06 修复）
            self.switchTo(w)

        self.pivot.addItem(routeKey=page.objectName(), text=text,
                           onClick=_go, icon=icon)
        self._pages.append(page)
        # 记录页面元信息（弹出面板窗口按此重建左侧导航，2026-09-07）
        if not hasattr(self, "_page_meta"):
            self._page_meta = []
        self._page_meta.append((page, text, icon))
        return page

    def switchTo(self, page):
        """容器内切换子页（与 FluentWindow.switchTo 同名兼容）"""
        self.stack.setCurrentWidget(page)
        self.pivot.setCurrentItem(page.objectName())

    def pages(self):
        return list(self._pages)

    def lock_pivot_width(self):
        """按内容锁定切换条宽度（所有页面注册完成后调用一次）"""
        self._shrink_items()
        self.pivot.setFixedWidth(max(self.pivot.sizeHint().width(),
                                     self.pivot.minimumSizeHint().width()))

    def _shrink_items(self):
        """二级切换条整体缩小 30%（2026-09-06 一期反馈）

        PivotItem 默认 18px 字体 + qss 上下 10px padding，视觉过大。
        只 setFixedHeight(24) 压不住：全局字体较大时 qss padding 使
        minimumSizeHint 涨到 40+px 并被布局机制写回 minimumHeight，
        整行仍被撑高（offscreen 实测 height=42）。因此需覆盖 item 的
        padding（仅此一条，其余选中态/hover 规则保留）+ 13px 字体 +
        24px 行高。

        2026-09-07 由 styleSheet 字符串 replace 迁移到 setCustomStyleSheet：
        字符串级修改会被 qfw 主题切换轮询以及主窗口合并源重置
        （ui_mixin._reset_qss_compose_trees）的 setStyleSheet 重设冲掉，
        item 行高回弹 42px；CustomStyleSheet 动态属性则由 qfw 机制自动
        保留并重应用（同 _enforce_toolbar_radio_height 模式）。
        """
        self.pivot.setItemFontSize(13)
        qss = "PivotItem { padding: 1px 12px; }"
        for item in self.pivot.items.values():
            setCustomStyleSheet(item, qss, qss)
            item.setFixedHeight(24)

    # ---------- 关闭清理 ----------

    # 与 ManagementPanelWindow.closeEvent / AftersalePanelWindow.closeEvent
    # 的清理口径一致：所有以 _worker 结尾的属性 + 收集 worker 列表
    _WORKER_ATTR_EXTRA = (
        "_migrate_worker", "_backfill_worker", "_backfill_save_worker",
        "_collect_workers",
    )

    def detach_workers(self):
        """请求停止所有子页后台线程（只 interrupt 不 wait，等待由主窗口统一做）"""
        for page in self._pages:
            for attr in dir(page):
                if attr.endswith("_worker"):
                    self._stop(getattr(page, attr, None))
            for attr in self._WORKER_ATTR_EXTRA:
                w = getattr(page, attr, None)
                if isinstance(w, list):
                    for item in list(w):
                        self._stop(item)
                else:
                    self._stop(w)

    @staticmethod
    def _stop(worker):
        if worker is None:
            return
        try:
            worker.requestInterruption()
        except (RuntimeError, TypeError):
            pass


class CardPage(QWidget):
    """卡片动作页：标题 + 说明 + 竖排动作按钮卡（远程会话/统计/设置/关于共用骨架）

    callbacks: {key: callable} —— 按钮触发主窗口回调，业务逻辑全部留在主窗口，
    本类只负责排版。
    """

    def __init__(self, title, desc, actions, parent=None, footer=None):
        """actions: [(key, icon, label, sub_text)]；footer: 额外挂到页尾的 widget"""
        super().__init__(parent)
        self.setObjectName("cardPage")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # 设置页卡片较多，整体套滚动（关闭横向滚动与边框，保持 Fluent 观感）
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # viewport 默认白底会盖住窗口底色，置透明以融入 Fluent 窗口背景
        scroll.viewport().setAutoFillBackground(False)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        outer.addWidget(scroll)
        body = QWidget(scroll)
        scroll.setWidget(body)

        lay = QVBoxLayout(body)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)

        lay.addWidget(TitleLabel(title, self))
        d = CaptionLabel(desc, self)
        d.setWordWrap(True)
        lay.addWidget(d)
        lay.addSpacing(6)

        self._buttons = {}
        for key, icon, label, sub in actions:
            card = CardWidget(self)
            v = QVBoxLayout(card)
            v.setContentsMargins(16, 12, 16, 12)
            v.setSpacing(4)
            row = QHBoxLayout()
            row.setSpacing(10)
            btn = PushButton(icon, label, card)
            btn.setFixedWidth(150)
            btn.clicked.connect(lambda _=False, k=key: self._fire(k))
            cap = CaptionLabel(sub or "", card)
            cap.setWordWrap(True)
            row.addWidget(btn)
            row.addWidget(cap, 1)
            v.addLayout(row)
            lay.addWidget(card)
            self._buttons[key] = btn

        lay.addStretch(1)
        if footer is not None:
            lay.addWidget(footer)

        self._callbacks = {}

    def set_callbacks(self, callbacks):
        self._callbacks = dict(callbacks or {})

    def _fire(self, key):
        cb = self._callbacks.get(key)
        if cb is not None:
            try:
                cb()
            except Exception:
                pass
