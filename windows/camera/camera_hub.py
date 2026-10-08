# -*- coding: utf-8 -*-
"""相机面板 Hub：面板级导航（主窗口左侧「相机」）。

单子页起步，P2 预留扩展位（多相机管理 / OSD 模板批量推送）。
弹出独立窗口复用全局 btn_popout 机制（objectName=cameraHub）。
"""
from __future__ import annotations

from qfluentwidgets import FluentIcon

from main_window.pivot_page import PivotPage
from windows.camera.camera_page import DahuaCameraWork
from windows.camera.encode_page import EncodePage


class CameraHub(PivotPage):
    """相机面板：子页签 = 相机工具 | 编码配置"""

    def __init__(self, win=None, parent=None):
        super().__init__(parent)
        self.setObjectName("cameraHub")
        self._win = win

        self.camera_page = DahuaCameraWork(self._win, self)
        self.encode_page = EncodePage(self._win, self.camera_page, self)
        self.addPage(self.camera_page, "相机工具", FluentIcon.CAMERA)
        self.addPage(self.encode_page, "编码配置", FluentIcon.SETTING)
        self.lock_pivot_width()
