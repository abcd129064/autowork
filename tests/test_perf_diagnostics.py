# -*- coding: utf-8 -*-
"""core.perf_diagnostics 纯格式化回归（B1 第二步）。"""
from core import perf_diagnostics as pd


def test_table_blit_disabled():
    s = {"enabled": False, "applied": 0, "total": 3,
         "no_bg": 0, "hidden": 0, "other": 0}
    assert pd.format_table_blit_status(s) == (
        "表格滚动加速：已关闭（perf_table_scroll_blit_v2=false）")


def test_table_blit_counts_and_hint():
    s = {"enabled": True, "applied": 2, "total": 5,
         "no_bg": 1, "hidden": 2, "other": 0}
    txt = pd.format_table_blit_status(s)
    assert "已生效 2/5 张" in txt
    assert "未取到底色 1 张" in txt
    assert "未显示 2 张" in txt
    assert "≥6px" in txt


def test_degrade_disabled_and_static():
    assert pd.format_degrade_diagnostics(0, False, None, 0, 1.0, False) == (
        "自动降级阈值：已关闭（配置 perf_dpi_degrade_pixels=0）")
    assert pd.format_degrade_diagnostics(6000000, False, None, 0, 1.0, False) == (
        "自动降级阈值：600 万像素（静态默认，自适应不可用）")


def test_degrade_explicit_and_auto():
    assert pd.format_degrade_diagnostics(5000000, True, 1, 0, 1.0, False) == (
        "自动降级阈值：500 万像素（配置指定）")
    txt = pd.format_degrade_diagnostics(7000000, False, 7000000, 0.1234, 0.1, False)
    assert "700 万像素（自适应" in txt
    assert "0.123 ms/百万像素" in txt
    assert "基准 0.100" in txt


def test_degrade_bigscreen_suffix():
    txt = pd.format_degrade_diagnostics(6000000, False, None, 0, 1.0, True)
    assert txt.endswith("；大屏性能模式开启（强制降级）")
