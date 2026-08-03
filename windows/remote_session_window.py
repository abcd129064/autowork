# -*- coding: utf-8 -*-
"""远程会话标签容器窗口

使用 qfluentwidgets TabWidget（Chrome/Edge 风格标签栏 + QStackedWidget）
承载多个 SFTPPanel / SSHTerminalPanel / RDPPanel 面板，实现类似远控软件/浏览器的
标签页切换体验。

用法：
    win = RemoteSessionWindow()   # 独立窗口，不传 parent（避免 owned window 始终置顶）
    win.add_session(panel)   # panel 为 SFTPPanel / SSHTerminalPanel / RDPPanel
    win.show()
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout
from qfluentwidgets import TabWidget
from qfluentwidgets.window.fluent_window import FluentTitleBar
from qframelesswindow import FramelessWindow


class RemoteSessionWindow(FramelessWindow):
    """远程会话标签容器窗口（无边框 + Fluent 自定义标题栏，独立窗口）

    - 每个标签页对应一个 visitor 的会话面板
    - 标签可拖拽排序、可关闭（关闭时自动调用 panel.shutdown() 释放资源）
    - 所有标签关闭后窗口自动关闭
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("远程会话")
        self.resize(1300, 850)
        self.setMinimumSize(800, 500)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        # 替换默认标题栏为 Fluent 风格（含图标/标题/最小化/最大化/关闭，
        # 后续自动同步 windowTitleChanged），取代原生 Qt 标题栏
        self.setTitleBar(FluentTitleBar(self))
        # setTitleBar 之前已设置过标题，信号早于连接发射，此处补一次初始同步
        self.titleBar.setTitle(self.windowTitle())

        self._tab_widget = TabWidget(self)
        self._tab_widget.setTabsClosable(True)
        self._tab_widget.tabCloseRequested.connect(self._on_tab_close_requested)
        # 隐藏标签栏的+号按钮，只显示标签
        self._tab_widget.tabBar.setAddButtonVisible(False)

        # 标题栏为浮动控件（高 48px，resizeEvent 中全宽拉伸），
        # 内容区顶部需预留标题栏高度，避免被遮挡
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 32, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._tab_widget)

        # 跟踪所有面板（用于批量关闭）
        self._panels: list = []

    def showEvent(self, event):
        super().showEvent(event)
        self._reset_titlebar_button_state()

    def _reset_titlebar_button_state(self):
        """重置标题栏按钮状态，修复关闭按钮卡在 PRESSED 导致窗口无法拖动。
        qframelesswindow 的 TitleBarButton 仅在 mousePressEvent 置 PRESSED，
        没有 mouseReleaseEvent 复位（只能靠 leaveEvent 恢复）。窗口关闭（hide）
        复用时关闭按钮可能停在 PRESSED，TitleBar.canDrag() 因此返回 False，
        标题栏无法拖动；鼠标移入按钮触发 enterEvent 后才恢复。每次显示主动复位。
        """
        try:
            from qframelesswindow.titlebar.title_bar_buttons import (
                TitleBarButton, TitleBarButtonState)
            for btn in self.titleBar.findChildren(TitleBarButton):
                if btn.isPressed():
                    btn.setState(TitleBarButtonState.NORMAL)
        except Exception:
            pass

    # ------------------------------------------------------------------ 公开 API

    def add_session(self, panel, title: str = '', icon=None):
        if not title:
            title = getattr(panel, 'tab_title', '会话')
        self._panels.append(panel)
        idx = self._tab_widget.addTab(panel, title, icon)
        self._tab_widget.setCurrentIndex(idx)

    def session_count(self) -> int:
        """当前打开的会话数量"""
        return self._tab_widget.stackedWidget.count()

    def remove_session(self, panel):
        """移除一个会话面板并关闭它"""
        for i in range(self._tab_widget.stackedWidget.count()):
            widget = self._tab_widget.stackedWidget.widget(i)
            if widget is panel:
                self._remove_tab_at(i)
                self._shutdown_panel(panel)
                return

    def shutdown_all(self):
        """关闭所有会话面板并释放资源"""
        for panel in list(self._panels):
            self._shutdown_panel(panel)
        self._panels.clear()

    # ------------------------------------------------------------------ 内部逻辑

    def _on_tab_close_requested(self, index: int):
        """用户点击标签关闭按钮
        qfluentwidgets TabWidget 的 tabCloseRequested 信号直接发出标签索引（int），
        而非 routeKey，因此直接按索引移除即可。
        """
        self._remove_tab_at(index)

    def _remove_tab_at(self, index: int):
        """移除指定索引的标签页并关闭对应面板"""
        widget = self._tab_widget.stackedWidget.widget(index)
        self._tab_widget.removeTab(index)
        if widget is not None:
            self._shutdown_panel(widget)
            if widget in self._panels:
                self._panels.remove(widget)
            widget.deleteLater()
        # 所有标签关闭后自动关闭窗口
        if self._tab_widget.stackedWidget.count() == 0:
            self.close()

    def _shutdown_panel(self, panel):
        """安全调用面板的 shutdown 方法"""
        shutdown = getattr(panel, 'shutdown', None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                pass

    # ------------------------------------------------------------------ 生命周期

    def closeEvent(self, event):
        """窗口关闭时释放所有会话"""
        self.shutdown_all()
        super().closeEvent(event)
