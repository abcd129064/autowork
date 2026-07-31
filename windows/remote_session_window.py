# -*- coding: utf-8 -*-
"""远程会话标签容器窗口

使用 qfluentwidgets TabWidget（Chrome/Edge 风格标签栏 + QStackedWidget）
承载多个 SFTPPanel / SSHTerminalPanel / RDPPanel 面板，实现类似远控软件/浏览器的
标签页切换体验。

用法：
    win = RemoteSessionWindow(parent=main_window)
    win.add_session(panel)   # panel 为 SFTPPanel / SSHTerminalPanel / RDPPanel
    win.show()
"""

from PySide6.QtWidgets import QDialog, QVBoxLayout
from PySide6.QtCore import Qt
from qfluentwidgets import TabWidget, FluentIcon


class RemoteSessionWindow(QDialog):
    """远程会话标签容器窗口

    - 每个标签页对应一个 visitor 的会话面板
    - 标签可拖拽排序、可关闭（关闭时自动调用 panel.shutdown() 释放资源）
    - 所有标签关闭后窗口自动关闭
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("远程会话")
        self.resize(1300, 850)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self._tab_widget = TabWidget(self)
        self._tab_widget.setTabsClosable(True)
        self._tab_widget.tabCloseRequested.connect(self._on_tab_close_requested)
        self._tab_widget.tabBar.tabAddRequested.connect(self._on_tab_add_requested)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._tab_widget)

        # 跟踪所有面板（用于批量关闭）
        self._panels: list = []

    # ------------------------------------------------------------------ 公开 API

    def add_session(self, panel, title: str = '', icon=None):
        """添加一个会话面板为新标签页并切换到它

        Args:
            panel: SFTPPanel / SSHTerminalPanel / RDPPanel 实例
            title: 标签标题（默认使用 panel.tab_title）
            icon: 标签图标（可选）
        """
        if not title:
            title = getattr(panel, 'tab_title', '会话')
        self._panels.append(panel)
        idx = self._tab_widget.addTab(panel, title, icon)
        self._tab_widget.setCurrentIndex(idx)

    def session_count(self) -> int:
        """当前打开的会话数量"""
        return self._tab_widget.stackedWidget.count()

    def shutdown_all(self):
        """关闭所有会话面板并释放资源"""
        for panel in list(self._panels):
            self._shutdown_panel(panel)
        self._panels.clear()

    # ------------------------------------------------------------------ 内部逻辑

    def _on_tab_close_requested(self, route_key: str):
        """用户点击标签关闭按钮"""
        # 通过 routeKey 找到对应的 widget index
        stacked = self._tab_widget.stackedWidget
        for i in range(stacked.count()):
            widget = stacked.widget(i)
            if widget.property('routeKey') == route_key:
                self._remove_tab_at(i)
                return

    def _on_tab_add_requested(self):
        """用户点击 '+' 按钮（暂不实现新建会话，仅忽略）"""
        pass

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
