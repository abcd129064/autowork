# -*- coding: utf-8 -*-
"""跑视频面板主窗口：FluentWindow + 左侧导航 + 三个功能页面

结构参照售后面板（windows/aftersale/window.py）：
- 填写录入（EntryPage）：表单 + 必填进度 + 提交，支持主界面会话预填
- 记录与统计（RecordsPage）：指标卡 + 筛选/分页 + 编辑/删除/署名统计/导出
- 设置（SettingsPage）：默认署名 + 数据库设置（MysqlSyncCard，run_video scope）

主界面「跑视频面板」按钮经 open_entry_with_context(ctx) 打开本窗口并
预填当前球桌会话上下文（未选设备时球房为空，面板内手填）。
"""
from PySide6.QtCore import QThread

from qfluentwidgets import FluentWindow, FluentIcon

from core.perf import is_acrylic_enabled

from windows.run_video.entry import EntryPage
from windows.run_video.records import RecordsPage
from windows.run_video.settings import SettingsPage


class LedgerPanelWindow(FluentWindow):
    """跑视频面板：表单录入 + 记录列表/筛选/分页 + 设置"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("跑视频面板")
        self.resize(1680, 900)
        self.setMinimumSize(900, 520)

        self.entry_page = EntryPage(self)
        self.entry_page.setObjectName("ledgerEntryPage")
        self.records_page = RecordsPage(self)
        self.records_page.setObjectName("ledgerRecordsPage")

        self.addSubInterface(self.entry_page, FluentIcon.EDIT, "填写录入")
        self.addSubInterface(self.records_page, FluentIcon.LIBRARY,
                             "记录与统计")

        # 提交成功后记录页静默刷新（不弹 infobar）
        self.entry_page.saved.connect(self.records_page.refresh_async)

        self.settings_page = SettingsPage(self)
        self.settings_page.setObjectName("ledgerSettingsPage")
        self.addSubInterface(self.settings_page, FluentIcon.SETTING, "设置")
        # 表格平滑滚动开关变更：实时刷新记录页表格滚动模式（仅本面板）
        self.settings_page.table_smooth_changed.connect(
            lambda _v: self.records_page._apply_smooth_mode())

        self.navigationInterface.setAcrylicEnabled(is_acrylic_enabled())
        self.navigationInterface.setCurrentItem(self.entry_page.objectName())

    def open_entry_with_context(self, ctx: dict):
        """主界面「跑视频面板」入口：切到填写录入页并预填会话上下文

        ctx 由主界面 _current_ledger_context 提供：
        {category, room_name, video_name, frame, signer}（均可为空）。
        """
        self.switchTo(self.entry_page)
        self.entry_page.prefill(ctx or {})

    def showEvent(self, event):
        super().showEvent(event)
        self._reset_titlebar_button_state()

    def _reset_titlebar_button_state(self):
        """重置标题栏按钮状态（同售后面板：修复关闭按钮卡 PRESSED 无法拖动）"""
        try:
            from qframelesswindow.titlebar.title_bar_buttons import (
                TitleBarButton, TitleBarButtonState)
            for btn in self.titleBar.findChildren(TitleBarButton):
                if btn.isPressed():
                    btn.setState(TitleBarButtonState.NORMAL)
        except Exception:
            pass

    def closeEvent(self, event):
        """关闭窗口快速清理 Worker（同售后面板策略：disconnect + 200ms 短等）"""
        def _detach(w):
            if w is None:
                return
            try:
                w.disconnect(self)
            except Exception:
                pass
            try:
                w.requestInterruption()
            except Exception:
                pass
        for page in (self.entry_page, self.records_page):
            for attr in dir(page):
                if attr.endswith("_worker"):
                    _detach(getattr(page, attr, None))
        QThread.msleep(200)
        super().closeEvent(event)
