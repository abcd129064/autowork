# -*- coding: utf-8 -*-
"""大屏/超高 DPI 自动降级回归（2026-09-25，P0-1/P0-2/P0-3）

覆盖 core/perf.py 大屏降级能力（依据 docs/大屏与超高DPI渲染性能调查报告2026-09-25.md）：
- 阈值与物理像素换算：get_dpi_degrade_pixels（默认 600 万 / perf 域覆盖 / 0=关闭）、
  window_physical_pixels / screen_physical_pixels
- P0-1 patch_dialog_animation：超阈值弹窗直显（无 opacity effect）+ 阴影半径 60→30；
  未超阈值走 P1-1 卡片级淡入（根不挂 effect，卡片 opacity 200ms）
- P0-2 patch_acrylic_downsample：AcrylicBrush.blurPicSize=None 时强制 (450,450)
- P0-3 patch_switch_animation：超阈值 setCurrentIndex 直切（不启动动画）；
  patch_menu_animation 超阈值降级 NONE

注：patch 类测试会永久替换 qfw 类方法（与 test_mica_policy 同模式），
但默认阈值 600 万下小窗口行为与库原生完全一致，不影响后续用例。
"""
import sys

import pytest
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QGraphicsOpacityEffect

import core.perf as perf

_qapp = None


def _ensure_qapp():
    global _qapp
    _qapp = QApplication.instance() or QApplication(sys.argv[:1])
    return _qapp


@pytest.fixture(scope="module")
def qapp():
    return _ensure_qapp()


# ==================== 阈值与换算 ====================

def test_degrade_threshold_default(qapp, monkeypatch):
    monkeypatch.setattr(perf, "_dpi_degrade_pixels", None)
    assert perf.get_dpi_degrade_pixels() == 6_000_000


def test_degrade_threshold_from_config(qapp, monkeypatch):
    monkeypatch.setattr(perf, "_dpi_degrade_pixels", None)
    monkeypatch.setattr(perf.app_settings, "get_domain",
                        lambda domain: {"perf_dpi_degrade_pixels": 123})
    assert perf.get_dpi_degrade_pixels() == 123


def test_degrade_threshold_zero_disables(qapp, monkeypatch):
    monkeypatch.setattr(perf, "_dpi_degrade_pixels", None)
    monkeypatch.setattr(perf.app_settings, "get_domain",
                        lambda domain: {"perf_dpi_degrade_pixels": 0})
    assert perf.get_dpi_degrade_pixels() == 0

    w = QWidget()
    w.resize(3840, 2160)
    assert perf._over_dpi_degrade(w) is False


def test_window_physical_pixels_matches_geometry(qapp):
    w = QWidget()
    w.resize(3000, 2200)
    # offscreen 默认 DPR=1：物理像素 = 逻辑尺寸
    assert perf.window_physical_pixels(w) == 3000 * 2200
    assert perf.screen_physical_pixels(w) > 0


def test_over_degrade_boundary(qapp, monkeypatch):
    w = QWidget()
    w.resize(3000, 2200)  # 660 万
    monkeypatch.setattr(perf, "_dpi_degrade_pixels", 3000 * 2200)
    assert perf._over_dpi_degrade(w) is True
    monkeypatch.setattr(perf, "_dpi_degrade_pixels", 3000 * 2200 + 1)
    assert perf._over_dpi_degrade(w) is False


@pytest.fixture
def ani_on(monkeypatch):
    """强制弹出动画开关为开（隔离真实 config，只测阈值分支）"""
    monkeypatch.setattr(perf, "get_animation", lambda panel=None: True)


# ==================== P0-1 弹窗降级 ====================

def _make_mask_dialog(parent, size):
    """构造 MaskDialogBase 实例（qfw 要求 parent 非 None；parent 须由
    调用方持有引用——QDialog 的 C++ 所有权归父窗口，Python 侧被 GC
    会连带删除对话框）"""
    from qfluentwidgets.components.dialog_box.mask_dialog_base import (
        MaskDialogBase)
    parent.resize(*size)
    return MaskDialogBase(parent)


def test_dialog_over_threshold_direct_show(qapp, monkeypatch, ani_on):
    perf.patch_dialog_animation()
    monkeypatch.setattr(perf, "_dpi_degrade_pixels", 6_000_000)

    parent = QWidget()
    dlg = _make_mask_dialog(parent, (3000, 2200))  # 660 万 ≥ 阈值
    parent.show()  # offscreen 下父窗口可见才同步派发 showEvent
    dlg.show()
    try:
        # 直显：整窗不挂 opacity effect
        assert dlg.graphicsEffect() is None
        # 阴影降半径：60 → 30
        shadow = dlg.widget.graphicsEffect()
        assert isinstance(shadow, QGraphicsDropShadowEffect)
        assert shadow.blurRadius() == 30
    finally:
        dlg.hide()
        dlg.deleteLater()


def test_dialog_under_threshold_uses_card_fade(qapp, monkeypatch, ani_on):
    perf.patch_dialog_animation()
    monkeypatch.setattr(perf, "_dpi_degrade_pixels", 6_000_000)
    monkeypatch.setattr(perf, "get_dialog_fade_mode", lambda: "card")

    parent = QWidget()
    dlg = _make_mask_dialog(parent, (1600, 900))  # 144 万 < 阈值
    parent.show()  # offscreen 下父窗口可见才同步派发 showEvent
    dlg.show()
    try:
        # P1-1 默认卡片级淡入：根（含全屏遮罩）不挂 effect，
        # 卡片挂 opacity effect，动画停在起点 0
        assert dlg.graphicsEffect() is None
        eff = dlg.widget.graphicsEffect()
        assert isinstance(eff, QGraphicsOpacityEffect)
        assert eff.opacity() == 0.0
        assert dlg._perf_fade_ani is not None
        # 动画结束（setCurrentTime(duration) 同步触发 finished——Qt 的
        # stop() 中途打断不发 finished）→ 恢复卡片阴影 effect
        dlg._perf_fade_ani.setCurrentTime(dlg._perf_fade_ani.duration())
        shadow = dlg.widget.graphicsEffect()
        assert isinstance(shadow, QGraphicsDropShadowEffect)
        assert shadow.blurRadius() == 60
    finally:
        dlg.hide()
        dlg.deleteLater()


def test_dialog_degrade_done_skips_fade(qapp, monkeypatch, ani_on):
    perf.patch_dialog_animation()
    monkeypatch.setattr(perf, "_dpi_degrade_pixels", 6_000_000)

    parent = QWidget()
    dlg = _make_mask_dialog(parent, (3000, 2200))
    parent.show()  # offscreen 下父窗口可见才同步派发 showEvent
    dlg.show()
    dlg.done(1)  # 超阈值：应立即走 QDialog.done，不再挂 fade-out effect
    assert dlg.graphicsEffect() is None
    dlg.deleteLater()


# ==================== P0-2 亚克力降采样 ====================

def test_acrylic_downsample_clamps_none(qapp, monkeypatch):
    from qfluentwidgets.components.widgets import acrylic_label as al
    perf.patch_acrylic_downsample()

    dev = QWidget()
    brush = al.AcrylicBrush(dev, blurRadius=18)
    assert brush.blurPicSize is None  # 前置：库默认全分辨率

    captured = {}

    def _fake_blur(image, blurRadius=18, brightFactor=1, blurPicSize=None):
        captured["blurPicSize"] = blurPicSize
        return QPixmap(8, 8)

    monkeypatch.setattr(al, "gaussianBlur", _fake_blur)
    brush.setImage(QPixmap(600, 2160))
    # 强制降采样：blurPicSize 被 clamp，且实际传给模糊函数
    assert brush.blurPicSize == (450, 450)
    assert captured["blurPicSize"] == (450, 450)


def test_acrylic_downsample_keeps_explicit_size(qapp, monkeypatch):
    from qfluentwidgets.components.widgets import acrylic_label as al
    perf.patch_acrylic_downsample()

    dev = QWidget()
    brush = al.AcrylicBrush(dev, blurRadius=18)
    brush.setBlurPicSize(QSize(200, 100))  # 显式设置的更小值不被覆盖
    monkeypatch.setattr(al, "gaussianBlur",
                        lambda image, blurRadius=18, brightFactor=1,
                        blurPicSize=None: QPixmap(8, 8))
    brush.setImage(QPixmap(600, 2160))
    assert brush.blurPicSize == (200, 100)


def test_acrylic_downsample_idempotent(qapp):
    from qfluentwidgets.components.widgets import acrylic_label as al
    perf.patch_acrylic_downsample()
    first = al.AcrylicBrush.setImage
    perf.patch_acrylic_downsample()
    assert al.AcrylicBrush.setImage is first  # 二次调用不重复包装


# ==================== P0-3 页面切换直切 / 菜单 NONE ====================

def test_switch_over_threshold_direct_cut(qapp, monkeypatch):
    from qfluentwidgets.components.widgets.stacked_widget import (
        PopUpAniStackedWidget)
    perf.patch_switch_animation()
    monkeypatch.setattr(perf, "_dpi_degrade_pixels", 6_000_000)

    win = QWidget()
    win.resize(3000, 2200)
    stack = PopUpAniStackedWidget(win)
    page_a, page_b = QWidget(), QWidget()
    stack.addWidget(page_a)
    stack.addWidget(page_b)

    stack.setCurrentIndex(1)
    # 直切：索引立即切换，未启动任何动画
    assert stack.currentIndex() == 1
    assert stack._ani is None


def test_switch_under_threshold_keeps_animation(qapp, monkeypatch):
    from PySide6.QtCore import QAbstractAnimation
    from qfluentwidgets.components.widgets.stacked_widget import (
        PopUpAniStackedWidget)
    perf.patch_switch_animation()
    monkeypatch.setattr(perf, "_dpi_degrade_pixels", 10 ** 12)  # 永不触发

    win = QWidget()
    win.resize(1600, 900)
    stack = PopUpAniStackedWidget(win)
    page_a, page_b = QWidget(), QWidget()
    stack.addWidget(page_a)
    stack.addWidget(page_b)

    stack.setCurrentIndex(1)
    # 库原生路径：动画启动（无事件循环推进也处于 Running 态）
    assert stack._ani is not None
    assert stack._ani.state() == QAbstractAnimation.State.Running


def test_switch_patch_idempotent():
    from qfluentwidgets.components.widgets.stacked_widget import (
        PopUpAniStackedWidget)
    perf.patch_switch_animation()
    first = PopUpAniStackedWidget.setCurrentIndex
    perf.patch_switch_animation()
    assert PopUpAniStackedWidget.setCurrentIndex is first


def test_menu_over_threshold_degrades_to_none(qapp, monkeypatch, ani_on):
    from qfluentwidgets.components.widgets.menu import (
        MenuAnimationManager, MenuAnimationType, RoundMenu)
    perf.patch_menu_animation()
    # 屏幕口径：阈值设 1 → 任何屏幕都超
    monkeypatch.setattr(perf, "_dpi_degrade_pixels", 1)

    host = QWidget()
    menu = RoundMenu("t", parent=host)
    manager = MenuAnimationManager.make(menu, MenuAnimationType.DROP_DOWN)
    assert type(manager).__name__ == "DummyMenuAnimationManager"  # NONE


def test_menu_under_threshold_maps_to_fade_in(qapp, monkeypatch, ani_on):
    from qfluentwidgets.components.widgets.menu import (
        MenuAnimationManager, MenuAnimationType, RoundMenu)
    perf.patch_menu_animation()
    monkeypatch.setattr(perf, "_dpi_degrade_pixels", 0)  # 自动降级关闭

    host = QWidget()
    menu = RoundMenu("t", parent=host)
    # P1-3（2026-09-25）：未超阈值时 DROP_DOWN/PULL_UP 映射为库自带
    # FADE_IN_*（windowOpacity 淡入，无逐帧 setMask）
    mgr = MenuAnimationManager.make(menu, MenuAnimationType.DROP_DOWN)
    assert type(mgr).__name__ == "FadeInDropDownMenuAnimationManager"
    mgr2 = MenuAnimationManager.make(menu, MenuAnimationType.PULL_UP)
    assert type(mgr2).__name__ == "FadeInPullUpMenuAnimationManager"
