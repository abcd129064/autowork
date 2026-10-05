# -*- coding: utf-8 -*-
"""自适应降级阈值回归（2026-10-07，P0-1 follow-up：静态 600 万 → 按本机标定）

覆盖 core/perf.py 自适应层（依据 docs/大屏与超高DPI渲染性能调查报告2026-09-25.md
§3.1 的基准机口径）：
- 倍率缩放：阈值 = 600 万 × (基准速率 / 本机速率) —— 慢机器更早降级、快机器少打扰
- 倍率与阈值两端夹取（0.25~4.0 倍、120 万~2400 万像素）
- 三级优先：显式 perf_dpi_degrade_pixels > 自适应标定 > 静态默认（0 = 关闭）
- 开关 perf_dpi_degrade_adaptive 关闭 → 完全退回静态 600 万
- 标定失败（不可用 / 已置失败标记）→ 静态 600 万，且不重试
- invalidate_cache 清掉标定缓存与显式标记
- 真实标定冒烟：返回值是正的有限数（基准机应接近 _AUTO_REF_MS_PER_MPX）

注：本文件把 measure_raster_rate_ms_per_mpx 换成固定值来测「倍率口径」，
真实标定只做冒烟，避免用例结果依赖跑分机器。
"""
import math
import sys

import pytest
from PySide6.QtWidgets import QApplication

import core.perf as perf

_qapp = None


def _ensure_qapp():
    global _qapp
    _qapp = QApplication.instance() or QApplication(sys.argv[:1])
    return _qapp


@pytest.fixture(scope="module")
def qapp():
    return _ensure_qapp()


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """每条用例前清掉缓存/显式标记（避免跨用例串味）"""
    for name, val in (("_auto_limit", None), ("_raster_rate", None),
                      ("_raster_rate_failed", False),
                      ("_dpi_degrade_pixels", None),
                      ("_dpi_degrade_pixels_explicit", None),
                      ("_bigscreen_mode", False)):
        monkeypatch.setattr(perf, name, val)
    monkeypatch.setattr(perf.app_settings, "get_domain", lambda _d: {})
    yield


def _rate(monkeypatch, ms_per_mpx):
    """把本机标定速率钉成给定值（倍率 = 基准速率 / 该值）"""
    monkeypatch.setattr(perf, "measure_raster_rate_ms_per_mpx",
                        lambda: ms_per_mpx)


# ==================== 倍率口径 ====================

def test_baseline_rate_keeps_default(qapp, monkeypatch):
    """基准机速率 → 阈值恒为 600 万（零回归）"""
    _rate(monkeypatch, perf._AUTO_REF_MS_PER_MPX)
    assert perf.get_auto_degrade_pixels() == 6_000_000
    assert perf.get_effective_degrade_pixels() == 6_000_000


def test_slow_machine_lowers_threshold(qapp, monkeypatch):
    """慢一倍 → 阈值减半 300 万（更早降级）"""
    _rate(monkeypatch, perf._AUTO_REF_MS_PER_MPX * 2)
    assert perf.get_auto_degrade_pixels() == 3_000_000


def test_fast_machine_raises_threshold(qapp, monkeypatch):
    """快一倍 → 阈值翻倍 1200 万（少打扰）"""
    _rate(monkeypatch, perf._AUTO_REF_MS_PER_MPX / 2)
    assert perf.get_auto_degrade_pixels() == 12_000_000


def test_ratio_clamped_both_ends(qapp, monkeypatch):
    """极端快慢机器的倍率夹取：不允许把阈值推到荒唐区间"""
    _rate(monkeypatch, perf._AUTO_REF_MS_PER_MPX * 1000)   # 极慢
    assert perf.get_auto_degrade_pixels() == int(
        6_000_000 * perf._AUTO_RATIO_MIN)

    monkeypatch.setattr(perf, "_auto_limit", None)
    _rate(monkeypatch, perf._AUTO_REF_MS_PER_MPX / 1000)   # 极快
    assert perf.get_auto_degrade_pixels() == int(
        6_000_000 * perf._AUTO_RATIO_MAX)


def test_limit_bounds_respected(qapp, monkeypatch):
    """无论倍率多离谱，阈值都落在 [120 万, 2400 万]"""
    for mult in (1e-6, 0.01, 0.5, 1.0, 2.0, 100.0, 1e6):
        monkeypatch.setattr(perf, "_auto_limit", None)
        _rate(monkeypatch, perf._AUTO_REF_MS_PER_MPX * mult)
        lim = perf.get_auto_degrade_pixels()
        assert perf._AUTO_LIMIT_MIN <= lim <= perf._AUTO_LIMIT_MAX, (mult, lim)


def test_cached_after_first_measure(qapp, monkeypatch):
    """标定只做一次：第二次读取不得再触发实测"""
    calls = []
    monkeypatch.setattr(perf, "measure_raster_rate_ms_per_mpx",
                        lambda: (calls.append(1), perf._AUTO_REF_MS_PER_MPX)[1])
    assert perf.get_auto_degrade_pixels() == 6_000_000
    assert perf.get_auto_degrade_pixels() == 6_000_000
    assert len(calls) == 1


# ==================== 优先级：显式 > 自适应 > 静态 ====================

def test_explicit_config_wins_over_adaptive(qapp, monkeypatch):
    """配置里显式写了 perf_dpi_degrade_pixels → 自适应不参与"""
    _rate(monkeypatch, perf._AUTO_REF_MS_PER_MPX / 4)   # 快机器本可放宽
    monkeypatch.setattr(perf.app_settings, "get_domain",
                        lambda _d: {"perf_dpi_degrade_pixels": 999_999})
    assert perf.get_effective_degrade_pixels() == 999_999
    assert perf._dpi_degrade_pixels_explicit is True
    assert "配置指定" in perf.degrade_diagnostics_text()


def test_explicit_zero_disables_degrade(qapp, monkeypatch):
    """0 = 关闭降级（显式关阈值是「强制某实现」的正规逃生门）"""
    monkeypatch.setattr(perf.app_settings, "get_domain",
                        lambda _d: {"perf_dpi_degrade_pixels": 0})
    assert perf.get_effective_degrade_pixels() == 0
    from PySide6.QtWidgets import QWidget
    w = QWidget()
    w.resize(3840, 2160)
    assert perf._over_dpi_degrade(w) is False
    assert "已关闭" in perf.degrade_diagnostics_text()


def test_adaptive_disabled_falls_back_to_static(qapp, monkeypatch):
    """perf_dpi_degrade_adaptive=false → 退回静态 600 万"""
    monkeypatch.setattr(perf.app_settings, "get_domain",
                        lambda _d: {"perf_dpi_degrade_adaptive": False})
    _rate(monkeypatch, perf._AUTO_REF_MS_PER_MPX * 4)
    assert perf.get_auto_degrade_pixels() is None
    assert perf.get_effective_degrade_pixels() == 6_000_000


def test_calibration_failure_uses_static(qapp, monkeypatch):
    """标定不可用 → 静态 600 万（失败安全，绝不因为量不出速率就乱降级）"""
    monkeypatch.setattr(perf, "_raster_rate_failed", True)
    assert perf.get_auto_degrade_pixels() is None
    assert perf.get_effective_degrade_pixels() == 6_000_000
    assert "静态默认" in perf.degrade_diagnostics_text()


def test_measure_returns_none_rnguard(qapp, monkeypatch):
    """实测函数返回 None（无 Qt/异常）时不得写坏缓存"""
    monkeypatch.setattr(perf, "measure_raster_rate_ms_per_mpx", lambda: None)
    assert perf.get_auto_degrade_pixels() is None
    assert perf._auto_limit is None


# ==================== 诊断文案与缓存重置 ====================

def test_diagnostics_text_reports_adaptive_source(qapp, monkeypatch):
    """诊断行要能看出「600 万是怎么来的」：自适应口径带倍率与速率"""
    _rate(monkeypatch, perf._AUTO_REF_MS_PER_MPX * 2)
    txt = perf.degrade_diagnostics_text()
    assert "300 万像素" in txt, txt
    assert "自适应" in txt and "ms/百万像素" in txt

    monkeypatch.setattr(perf, "_bigscreen_mode", True)
    assert "大屏性能模式" in perf.degrade_diagnostics_text()


def test_invalidate_cache_resets_calibration(qapp, monkeypatch):
    """invalidate_cache（设置变更后）必须清掉标定缓存与显式标记"""
    perf._raster_rate = 0.5
    perf._auto_limit = 123
    perf._dpi_degrade_pixels_explicit = True
    perf._dpi_degrade_pixels = 777
    perf.invalidate_cache()
    assert perf._raster_rate is None
    assert perf._auto_limit is None
    assert perf._dpi_degrade_pixels_explicit is None
    assert perf._dpi_degrade_pixels is None


def test_real_calibration_smoke(qapp):
    """真实标定冒烟：正的有限数（本机应接近基准 0.298 ms/百万像素）"""
    perf._raster_rate = None
    perf._raster_rate_failed = False
    rate = perf.measure_raster_rate_ms_per_mpx()
    if rate is None:      # 无 GUI 环境的兜底：跳过而非失败
        pytest.skip("当前环境无法标定（无 QApplication 光栅能力）")
    assert math.isfinite(rate) and rate > 0
    # 同一进程内第二次调用直接命中缓存
    assert perf.measure_raster_rate_ms_per_mpx() == rate
