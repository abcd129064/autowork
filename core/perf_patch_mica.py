# -*- coding: utf-8 -*-
"""Mica 云母环境兜底补丁（B1 patch 层第一片）。

从 core/perf.py 下沉：只负责在已判定 reason 后对 qfluentwidgets 双层短路，
不探测系统环境、不读配置。core.perf 保留 patch_mica_policy 旧入口，并在
调用前计算 mica_block_reason（便于既有 monkeypatch）。
"""


def apply_mica_policy(reason: str, log=None) -> None:
    """按 reason 应用 Mica 双层短路；reason 为空时保持库原行为。

    qfw 开启 Mica 有两条独立通路，缺一不可：
    1. FluentWidget.setMicaEffectEnabled 开关路径；
    2. FramelessWindow 构造时直调 WindowsWindowEffect.setMicaEffect。
    """
    try:
        from qfluentwidgets.window.fluent_window import FluentWidget
        from qframelesswindow.windows.window_effect import WindowsWindowEffect
    except Exception:
        return
    if getattr(FluentWidget, "_perf_mica_patched", False):
        return
    FluentWidget._perf_mica_patched = True
    if not reason:
        return

    def _setMicaEffectEnabled(self, isEnabled: bool):
        if isEnabled:
            return
        try:
            self.windowEffect.removeBackgroundEffect(self.winId())
        except Exception:
            pass

    def _setMicaEffect(self, hWnd, isDarkMode=False, isAlt=False):
        return

    FluentWidget.setMicaEffectEnabled = _setMicaEffectEnabled
    WindowsWindowEffect.setMicaEffect = _setMicaEffect
    if log is not None:
        try:
            log(f"[perf] Mica 云母已禁用（{reason}），窗口使用纯色背景")
        except Exception:
            pass
