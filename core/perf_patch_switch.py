# -*- coding: utf-8 -*-
"""大屏页面切换直切补丁（B1 patch 层第四片）。

从 core/perf.py 下沉：PopUpAniStackedWidget.setCurrentIndex 在超阈值时
直接调用 QStackedWidget 基类实现，跳过 250ms 滑入动画。阈值判定由
core.perf 注入（保留 _over_dpi_degrade 的 monkeypatch 点）。
"""


def apply_switch_animation_patch(over_degrade) -> None:
    """安装页面切换直切补丁（幂等）；over_degrade(host) 返回 True 时直切。"""
    try:
        from PySide6.QtWidgets import QStackedWidget
        from qfluentwidgets.components.widgets.stacked_widget import (
            PopUpAniStackedWidget)
    except Exception:
        return
    if getattr(PopUpAniStackedWidget, "_perf_switch_patched", False):
        return
    orig_set_index = PopUpAniStackedWidget.setCurrentIndex

    def _setCurrentIndex(self, index, *args, **kwargs):
        try:
            if over_degrade(self):
                return QStackedWidget.setCurrentIndex(self, index)
        except Exception:
            pass
        return orig_set_index(self, index, *args, **kwargs)

    PopUpAniStackedWidget.setCurrentIndex = _setCurrentIndex
    PopUpAniStackedWidget._perf_switch_patched = True
