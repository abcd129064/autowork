# -*- coding: utf-8 -*-
"""P1 批次回归（2026-09-25）：大屏 DPI 优化的第二梯队

覆盖 core/perf.py + ui_mixin/hub_pages 落地项：
- P1-1 弹窗 windowOpacity 淡入淡出（默认）/ 'opacity' 模式回退库路径 /
  直显路径恢复 opacity=1 / done 延迟到动画结束
- P1-2 dpi_scale 叠加告警：注册表探测（fake winreg）/ 告警写入与读取 /
  apply_dpi_scale 分支
- P1-4 大屏性能模式：开启强制降级（无视阈值与窗口尺寸）

（P1-3 菜单 FADE_IN 映射的用例在 tests/test_perf_dpi_degrade.py。）
"""
import os
import sys

import pytest
from PySide6.QtCore import QAbstractAnimation
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtWidgets import QGraphicsOpacityEffect

import core.perf as perf

_qapp = None


def _ensure_qapp():
    global _qapp
    _qapp = QApplication.instance() or QApplication(sys.argv[:1])
    return _qapp


@pytest.fixture(scope="module")
def qapp():
    return _ensure_qapp()


@pytest.fixture
def ani_on(monkeypatch):
    """强制弹出动画开关为开（隔离真实 config，只测目标分支）"""
    monkeypatch.setattr(perf, "get_animation", lambda panel=None: True)


@pytest.fixture
def fade_on(monkeypatch):
    """默认淡入实现 = 卡片级 effect（P1-1 默认值，隔离真实 config）"""
    monkeypatch.setattr(perf, "get_dialog_fade_mode", lambda: "card")


# ==================== P1-1 弹窗卡片级淡入淡出 ====================

def _make_dialog(parent, size):
    from qfluentwidgets.components.dialog_box.mask_dialog_base import (
        MaskDialogBase)
    parent.resize(*size)
    return MaskDialogBase(parent)


def test_dialog_fade_card_default(qapp, monkeypatch, ani_on, fade_on):
    perf.patch_dialog_animation()
    monkeypatch.setattr(perf, "_dpi_degrade_pixels", 10 ** 12)  # 不触发阈值

    parent = QWidget()
    dlg = _make_dialog(parent, (1600, 900))
    parent.show()  # offscreen 下父窗口可见才同步派发 showEvent
    dlg.show()
    try:
        # 根（含全屏遮罩）不挂 effect——整窗离屏光栅的源头被移除
        assert dlg.graphicsEffect() is None
        # 卡片挂 opacity effect，动画停在起点 0
        eff = dlg.widget.graphicsEffect()
        assert isinstance(eff, QGraphicsOpacityEffect)
        assert eff.opacity() == 0.0
        assert dlg._perf_fade_ani is not None
        assert dlg._perf_fade_ani.state() == QAbstractAnimation.State.Running
        # 动画结束（setCurrentTime(duration) 同步触发 finished——Qt 的
        # stop() 中途打断不发 finished，见 perf._stop_fade 注记）→ 恢复卡片阴影
        dlg._perf_fade_ani.setCurrentTime(dlg._perf_fade_ani.duration())
        from PySide6.QtWidgets import QGraphicsDropShadowEffect
        eff2 = dlg.widget.graphicsEffect()
        assert isinstance(eff2, QGraphicsDropShadowEffect)
        assert eff2.blurRadius() == 60
    finally:
        dlg.hide()
        dlg.deleteLater()


def test_dialog_fade_opacity_mode_falls_back(qapp, monkeypatch, ani_on):
    perf.patch_dialog_animation()
    monkeypatch.setattr(perf, "_dpi_degrade_pixels", 10 ** 12)
    monkeypatch.setattr(perf, "get_dialog_fade_mode", lambda: "opacity")

    parent = QWidget()
    dlg = _make_dialog(parent, (1600, 900))
    parent.show()
    dlg.show()
    try:
        # 逃生门：'opacity' 模式走库原生整窗 QGraphicsOpacityEffect 路径
        assert isinstance(dlg.graphicsEffect(), QGraphicsOpacityEffect)
    finally:
        dlg.hide()
        dlg.deleteLater()


def test_dialog_degrade_direct_show_no_fade(qapp, monkeypatch, ani_on, fade_on):
    perf.patch_dialog_animation()
    monkeypatch.setattr(perf, "_dpi_degrade_pixels", 6_000_000)

    parent = QWidget()
    dlg = _make_dialog(parent, (3000, 2200))  # 660 万 ≥ 阈值
    parent.show()
    dlg.show()
    # 直显路径：无任何动画与 effect，阴影降半径 30
    assert dlg.graphicsEffect() is None
    assert dlg.widget.graphicsEffect().blurRadius() == 30
    assert getattr(dlg, "_perf_fade_ani", None) is None
    dlg.deleteLater()


def test_dialog_done_defers_until_fade_end(qapp, monkeypatch, ani_on, fade_on):
    perf.patch_dialog_animation()
    monkeypatch.setattr(perf, "_dpi_degrade_pixels", 10 ** 12)

    parent = QWidget()
    dlg = _make_dialog(parent, (1600, 900))
    parent.show()
    dlg.show()
    dlg._perf_fade_ani.setCurrentTime(dlg._perf_fade_ani.duration())  # 先结束淡入（恢复阴影），再测淡出
    dlg.done(1)
    # 淡出动画在途：卡片 opacity effect 存在、对话框尚未真正关闭
    assert dlg.isVisible()
    assert isinstance(dlg.widget.graphicsEffect(), QGraphicsOpacityEffect)
    # 动画结束（setCurrentTime(duration) 同步触发 finished）→ 移除 effect 并真正关闭
    dlg._perf_fade_ani.setCurrentTime(dlg._perf_fade_ani.duration())
    assert not dlg.isVisible()
    assert dlg.widget.graphicsEffect() is None
    assert dlg.graphicsEffect() is None
    dlg.deleteLater()


# ==================== P1-2 dpi_scale 叠加告警 ====================

class _FakeKey:
    def __init__(self, value):
        self._v = value

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_detect_system_scale_from_registry(qapp, monkeypatch):
    import winreg

    monkeypatch.setattr(winreg, "OpenKey",
                        lambda root, path: _FakeKey(None))
    monkeypatch.setattr(winreg, "QueryValueEx",
                        lambda k, name: (144, winreg.REG_SZ))
    # 144 DPI → 150%
    assert perf.detect_system_scale_percent() == 150

    monkeypatch.setattr(winreg, "QueryValueEx",
                        lambda k, name: (96, winreg.REG_SZ))
    assert perf.detect_system_scale_percent() == 100


def test_detect_system_scale_failure_returns_none(qapp, monkeypatch):
    import winreg

    def boom(*args, **kwargs):
        raise OSError("no key")

    monkeypatch.setattr(winreg, "OpenKey", boom)
    assert perf.detect_system_scale_percent() is None


def test_dpi_stack_warning_roundtrip(qapp):
    old = perf._dpi_stack_warning
    try:
        perf.set_dpi_stack_warning(150, 150)
        msg = perf.get_dpi_stack_warning()
        assert msg and "150" in msg and "225" in msg
    finally:
        perf._dpi_stack_warning = old


def test_apply_dpi_scale_stack_warning_branch(qapp, monkeypatch):
    from main_window import MainWindow
    old = perf._dpi_stack_warning
    try:
        # 系统缩放 150% + 应用内 150% → 告警写入
        monkeypatch.setattr(perf, "detect_system_scale_percent",
                            lambda: 150)
        perf._dpi_stack_warning = None
        MainWindow.apply_dpi_scale({"dpi_scale": 150})
        assert os.environ.get("QT_SCALE_FACTOR") == "1.5"
        assert perf.get_dpi_stack_warning() is not None

        # 系统缩放 100% + 应用内 150% → 不告警
        monkeypatch.setattr(perf, "detect_system_scale_percent",
                            lambda: 100)
        perf._dpi_stack_warning = None
        MainWindow.apply_dpi_scale({"dpi_scale": 150})
        assert perf.get_dpi_stack_warning() is None

        # 应用内 100% → 连环境变量都不设置
        os.environ.pop("QT_SCALE_FACTOR", None)
        MainWindow.apply_dpi_scale({"dpi_scale": 100})
        assert "QT_SCALE_FACTOR" not in os.environ
    finally:
        os.environ.pop("QT_SCALE_FACTOR", None)
        perf._dpi_stack_warning = old


# ==================== P1-4 大屏性能模式 ====================

def test_bigscreen_mode_forces_degrade(qapp, monkeypatch):
    w = QWidget()
    w.resize(400, 300)  # 小窗：像素阈值口径永不触发

    monkeypatch.setattr(perf, "_bigscreen_mode", True)
    monkeypatch.setattr(perf, "_dpi_degrade_pixels", 10 ** 12)
    assert perf._over_dpi_degrade(w) is True  # 模式优先，无视阈值

    monkeypatch.setattr(perf, "_bigscreen_mode", False)
    assert perf._over_dpi_degrade(w) is False  # 关闭 → 回到阈值判定


def test_bigscreen_mode_dialog_direct_show(qapp, monkeypatch, ani_on, fade_on):
    perf.patch_dialog_animation()
    # 阈值设到极大（自动判定永不触发），仅靠大屏模式强制降级
    monkeypatch.setattr(perf, "_dpi_degrade_pixels", 10 ** 12)
    monkeypatch.setattr(perf, "_bigscreen_mode", True)

    parent = QWidget()
    dlg = _make_dialog(parent, (800, 600))
    parent.show()
    dlg.show()
    # 强制降级：无任何动画/effect，直显 + 阴影降半径
    assert dlg.graphicsEffect() is None
    assert dlg.widget.graphicsEffect().blurRadius() == 30
    assert getattr(dlg, "_perf_fade_ani", None) is None
    dlg.deleteLater()
