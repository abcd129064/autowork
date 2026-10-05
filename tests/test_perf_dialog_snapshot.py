# -*- coding: utf-8 -*-
"""快照淡入回归（2026-10-07，P0-1 follow-up）

覆盖 core/perf.py 的 'snapshot' 实现（超阈值机器上保留弹窗淡入）：
- 模式解析：auto + 超阈值 → 'snapshot'；大屏模式压过一切；
  显式 'card'/'opacity' 在超阈值下收敛到 'direct'（既有契约，见
  tests/test_perf_dpi_p1.py 的 fade_on 用例）
- 快照构建：卡片区域真实像素（中心不透明、dpr/尺寸正确）、
  区域过小 / 卡片不可见 → (None, None)（失败即退回直显）
- 覆盖层：不挂任何 QGraphicsEffect（挂 effect 的对话框 render/grab
  会丢全部子控件）、鼠标穿透、透明度夹取 + 自绘
- 显示路径：整窗无 effect、卡片让位、淡入结束后卡片归位 + 阴影复原
- 收尾：done() 与构建失败都必须把卡片放回来（绝不留空白弹窗）
- 像素等价：快照与真实渲染的卡片内部逐点比对（防止抓成空图/黑图）

注：patch 类测试会永久替换 qfw 类方法（与 test_perf_dpi_degrade.py 同模式）。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("qfluentwidgets")

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPalette, QPixmap
from PySide6.QtWidgets import (QApplication, QGraphicsDropShadowEffect,
                               QWidget)

import core.perf as perf

_qapp = None


def _ensure_qapp():
    global _qapp
    _qapp = QApplication.instance() or QApplication([])
    return _qapp


@pytest.fixture(scope="module")
def qapp():
    return _ensure_qapp()


@pytest.fixture(autouse=True)
def _patched():
    """showEvent/done 替换是幂等的（模块级守卫），本文件全程需要它"""
    perf.patch_dialog_animation()
    yield


@pytest.fixture
def ani_on(monkeypatch):
    monkeypatch.setattr(perf, "get_animation", lambda panel=None: True)


def _make_dialog(parent, size=(1600, 900), opaque=True):
    """构造 MaskDialogBase（qfw 要求 parent 非 None；parent 须由调用方
    持有引用——QDialog 的 C++ 所有权归父窗口，Python 侧被 GC 会连带删除）

    opaque=True 时给中心卡片铺一层不透明底色：裸 MaskDialogBase 的卡片
    本身是透明的（qfw 的对话框子类才给它上色），而 _build_dialog_snapshot
    按设计拒绝透明卡片（中心 alpha < 250 → 宁可退回直显）。
    """
    from qfluentwidgets.components.dialog_box.mask_dialog_base import (
        MaskDialogBase)
    parent.resize(*size)
    dlg = MaskDialogBase(parent)
    if opaque:
        w = dlg.widget
        w.setAutoFillBackground(True)
        pal = w.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor("#ffffff"))
        w.setPalette(pal)
    return dlg


def _shown_dialog(qapp, size=(1600, 900), opaque=True):
    """建一个已显示的对话框（offscreen 下父窗口可见才同步派发 showEvent）"""
    parent = QWidget()
    dlg = _make_dialog(parent, size, opaque)
    parent.show()
    dlg.show()
    qapp.processEvents()
    return parent, dlg


def _settle(qapp, rounds=3):
    """把 QTimer.singleShot(0) 排的活干完"""
    for _ in range(rounds):
        qapp.processEvents()


@pytest.fixture
def direct_on(monkeypatch, ani_on):
    """直显路径：卡片可见、无任何 opacity effect（快照构建的前置状态）"""
    monkeypatch.setattr(perf, "is_bigscreen_mode", lambda: False)
    monkeypatch.setattr(perf, "_over_dpi_degrade", lambda *a, **k: False)
    monkeypatch.setattr(perf, "get_dialog_fade_mode", lambda: "direct")
    return None


@pytest.fixture
def snapshot_on(monkeypatch, ani_on):
    """把「超阈值 + auto」钉成快照淡入（不依赖跑分机器与真实阈值）"""
    monkeypatch.setattr(perf, "is_bigscreen_mode", lambda: False)
    monkeypatch.setattr(perf, "_over_dpi_degrade", lambda *a, **k: True)
    monkeypatch.setattr(perf, "get_dialog_fade_mode", lambda: "auto")
    return None


def _mode(monkeypatch, fade, bigscreen=False, over=True):
    monkeypatch.setattr(perf, "is_bigscreen_mode", lambda: bigscreen)
    monkeypatch.setattr(perf, "_over_dpi_degrade", lambda *a, **k: over)
    monkeypatch.setattr(perf, "get_dialog_fade_mode", lambda: fade)
    return perf.resolve_dialog_fade_mode(QWidget())


# ==================== 模式解析 ====================

def test_mode_auto_over_threshold_is_snapshot(qapp, monkeypatch):
    """auto 在超阈值机器上改用快照淡入（P0-1 的降级不再等于「没动画」）"""
    assert _mode(monkeypatch, "auto") == "snapshot"


def test_mode_explicit_snapshot_survives_degrade(qapp, monkeypatch):
    assert _mode(monkeypatch, "snapshot") == "snapshot"


def test_mode_explicit_values_converge_to_direct(qapp, monkeypatch):
    """超阈值下 'card'/'opacity' 被降级接管（既有回归用例的契约）"""
    assert _mode(monkeypatch, "card") == "direct"
    assert _mode(monkeypatch, "opacity") == "direct"
    assert _mode(monkeypatch, "direct") == "direct"


def test_mode_bigscreen_wins_over_everything(qapp, monkeypatch):
    """大屏性能模式是一键全量降级：连快照也不留"""
    assert _mode(monkeypatch, "snapshot", bigscreen=True) == "direct"
    assert _mode(monkeypatch, "card", bigscreen=True) == "direct"


def test_mode_under_threshold_unchanged(qapp, monkeypatch):
    """未超阈值：auto 仍是卡片级淡入，显式值照还（零回归）"""
    assert _mode(monkeypatch, "auto", over=False) == "card"
    assert _mode(monkeypatch, "snapshot", over=False) == "snapshot"
    assert _mode(monkeypatch, "opacity", over=False) == "opacity"


def test_mode_dirty_value_falls_back_to_auto(qapp, monkeypatch):
    """配置里的脏值按 auto 处理（'card' 是 auto 在未超阈值下的落点）"""
    monkeypatch.setattr(perf, "is_bigscreen_mode", lambda: False)
    monkeypatch.setattr(perf, "_over_dpi_degrade", lambda *a, **k: False)
    monkeypatch.setattr(perf, "_dialog_fade_mode", "whatever")
    monkeypatch.setattr(perf.app_settings, "get_domain", lambda _d: {})
    assert perf.get_dialog_fade_mode() == "auto"
    assert perf.resolve_dialog_fade_mode(QWidget()) == "card"


# ==================== 快照构建 ====================

def test_snapshot_matches_card_and_dpr(qapp, direct_on):
    """快照尺寸/dpr/内容基线（QRegion 必须从 QtGui 导入——导错会让
    每次构建都静默回退直显，这条用例就是那次事故的回归）"""
    parent, dlg = _shown_dialog(qapp)
    try:
        pm, rect = perf._build_dialog_snapshot(dlg, dlg.widget)
        assert pm is not None and not pm.isNull()
        dpr = dlg.devicePixelRatioF()
        assert pm.devicePixelRatio() == pytest.approx(dpr)
        assert pm.width() == pytest.approx(rect.width() * dpr, abs=2)
        assert pm.height() == pytest.approx(rect.height() * dpr, abs=2)
        img = pm.toImage()
        # 卡片中心：不透明（抓成空图时 alpha 会是 0）
        center = img.pixelColor(img.width() // 2, img.height() // 2)
        assert center.alpha() >= perf._SNAPSHOT_MIN_ALPHA, center.name()
        # 卡片外（区域左边缘）不抓遮罩：遮罩是 60% 白，抓进来会 ≥150
        if rect.left() + 8 < dlg.widget.geometry().left():
            assert img.pixelColor(1, img.height() // 2).alpha() < 60
    finally:
        dlg.hide()
        dlg.deleteLater()


def test_snapshot_rejects_tiny_region(qapp, direct_on):
    """区域太小 → (None, None)：宁可退回直显，也不做一张糊图"""
    parent, dlg = _shown_dialog(qapp)
    try:
        dlg.resize(10, 10)
        pm, rect = perf._build_dialog_snapshot(dlg, dlg.widget)
        assert pm is None and rect is None
    finally:
        dlg.hide()
        dlg.deleteLater()


def test_snapshot_has_no_graphics_effect_on_dialog(qapp, snapshot_on):
    """构建快照时对话框本体不能挂 effect（挂了就抓不到子控件）"""
    parent, dlg = _shown_dialog(qapp, (3000, 2200))
    try:
        _settle(qapp)
        assert dlg.graphicsEffect() is None
        assert dlg._perf_snapshot_overlay is not None
        assert dlg._perf_snapshot_overlay.graphicsEffect() is None
    finally:
        dlg.hide()
        dlg.deleteLater()


# ==================== 覆盖层 ====================

def _render_overlay(ov, size=(40, 40)):
    img = QImage(size[0], size[1], QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    ov.render(img)
    return img


def test_overlay_paints_own_opacity(qapp):
    """淡入靠覆盖层自绘透明度：不挂 effect，t=0.5 → alpha 减半"""
    parent, dlg = _shown_dialog(qapp, (600, 400))
    try:
        src = QPixmap(40, 40)
        src.fill(QColor(255, 0, 0, 255))
        ov = perf._SnapshotOverlay(dlg, src, QRect(0, 0, 40, 40))
        assert ov.graphicsEffect() is None
        assert ov.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        ov.set_snapshot_opacity(1.0)
        full = _render_overlay(ov).pixelColor(20, 20)
        assert (full.red(), full.alpha()) == (255, 255)
        ov.set_snapshot_opacity(0.5)
        half = _render_overlay(ov).pixelColor(20, 20)
        assert abs(half.alpha() - 128) <= 3, half.alpha()
        ov.set_snapshot_opacity(-5)      # 夹到 0
        assert _render_overlay(ov).pixelColor(20, 20).alpha() == 0
        ov.set_snapshot_opacity(9)       # 夹到 1
        assert _render_overlay(ov).pixelColor(20, 20).alpha() == 255
        ov.deleteLater()
    finally:
        dlg.hide()
        dlg.deleteLater()


# ==================== 显示路径 ====================

def test_show_starts_snapshot_fade(qapp, snapshot_on):
    parent, dlg = _shown_dialog(qapp, (3000, 2200))
    try:
        _settle(qapp)
        assert dlg.graphicsEffect() is None                 # 整窗无 effect
        assert dlg.widget.isHidden() is True                # 卡片让位给快照
        ov = dlg._perf_snapshot_overlay
        assert ov is not None and ov.isVisible()
        assert ov.width() > 100 and ov.height() > 100
        assert dlg._perf_fade_ani is not None
        # 降级口径的阴影半径（30）已经就位
        sh = dlg.widget.graphicsEffect()
        assert isinstance(sh, QGraphicsDropShadowEffect)
        assert sh.blurRadius() == 30
    finally:
        dlg.hide()
        dlg.deleteLater()


def test_snapshot_fade_finishes_and_restores_card(qapp, snapshot_on):
    parent, dlg = _shown_dialog(qapp, (3000, 2200))
    try:
        _settle(qapp)
        ani = dlg._perf_fade_ani
        assert ani is not None
        ani.setCurrentTime(ani.duration())      # 同步触发 finished
        assert getattr(dlg, "_perf_snapshot_overlay", None) is None
        assert dlg._perf_fade_ani is None
        assert dlg.widget.isHidden() is False   # 卡片归位
        assert dlg.graphicsEffect() is None
        assert dlg.widget.graphicsEffect().blurRadius() == 30
    finally:
        dlg.hide()
        dlg.deleteLater()


def test_done_cleans_snapshot(qapp, snapshot_on):
    """半途 done()：快照必须撤掉，卡片状态复原"""
    parent, dlg = _shown_dialog(qapp, (3000, 2200))
    try:
        _settle(qapp)
        assert dlg._perf_snapshot_overlay is not None
        dlg.done(0)
        assert getattr(dlg, "_perf_snapshot_overlay", None) is None
        assert dlg.graphicsEffect() is None
        assert dlg.widget.isHidden() is False
    finally:
        dlg.hide()
        dlg.deleteLater()


def test_build_failure_restores_card(qapp, snapshot_on, monkeypatch):
    """快照构建失败 → 退回直显（卡片回来，不是空白弹窗）"""
    monkeypatch.setattr(perf, "_build_dialog_snapshot",
                        lambda *a, **k: (None, None))
    parent, dlg = _shown_dialog(qapp, (3000, 2200))
    try:
        _settle(qapp)
        assert getattr(dlg, "_perf_snapshot_overlay", None) is None
        assert dlg.widget.isHidden() is False
        assert dlg._perf_fade_ani is None
        assert dlg.graphicsEffect() is None
    finally:
        dlg.hide()
        dlg.deleteLater()


def test_start_returns_false_without_card(qapp):
    """坏输入（没有卡片）不抛异常，直接返回 False 让调用方走直显"""
    parent = QWidget()
    dlg = _make_dialog(parent)
    dlg.widget = None
    assert perf._start_snapshot_fade(dlg, 30) is False


def test_cleanup_is_idempotent(qapp, snapshot_on):
    parent, dlg = _shown_dialog(qapp, (3000, 2200))
    try:
        _settle(qapp)
        perf._cleanup_snapshot_fade(dlg)
        perf._cleanup_snapshot_fade(dlg)        # 再调一次不得抛
        assert getattr(dlg, "_perf_snapshot_overlay", None) is None
        assert dlg.widget.isHidden() is False
    finally:
        dlg.hide()
        dlg.deleteLater()


# ==================== 像素等价 ====================

def _mean_diff(ref_img, pm_img, rect, dpr, inset=40, samples=40):
    """卡片内部逐点比对（避开阴影/圆角 AA 边缘）"""
    x0 = int((rect.left() + inset) * dpr)
    y0 = int((rect.top() + inset) * dpr)
    x1 = int((rect.left() + rect.width() - inset) * dpr)
    y1 = int((rect.top() + rect.height() - inset) * dpr)
    x1 = min(x1, pm_img.width() - 1)
    y1 = min(y1, pm_img.height() - 1)
    step_x = max(1, (x1 - x0) // samples)
    step_y = max(1, (y1 - y0) // samples)
    total = 0
    n = 0
    opaque_ref = 0
    opaque_snap = 0
    for y in range(y0, y1, step_y):
        for x in range(x0, x1, step_x):
            a = ref_img.pixelColor(x, y)
            b = pm_img.pixelColor(int(x - rect.left() * dpr),
                                  int(y - rect.top() * dpr))
            total += (abs(a.red() - b.red()) + abs(a.green() - b.green())
                      + abs(a.blue() - b.blue()))
            n += 3
            opaque_ref += 1 if a.alpha() == 255 else 0
            opaque_snap += 1 if b.alpha() == 255 else 0
    return total / max(1, n), n, opaque_ref, opaque_snap


def test_snapshot_pixels_match_rendered_card(qapp, direct_on):
    """快照 = 卡片区域的真实像素（抓成空图/黑图时这条会炸）"""
    parent, dlg = _shown_dialog(qapp, (1200, 800))
    try:
        ref = dlg.grab()
        pm, rect = perf._build_dialog_snapshot(dlg, dlg.widget)
        assert pm is not None
        diff, n, opaque_ref, opaque_snap = _mean_diff(
            ref.toImage(), pm.toImage(), rect, pm.devicePixelRatio())
        assert n > 0
        # 参考图与快照都应是卡片实体像素（不是透明空区）
        assert opaque_ref > n / 9 * 0.9 and opaque_snap > n / 9 * 0.9
        assert diff <= 6.0, diff
    finally:
        dlg.hide()
        dlg.deleteLater()
