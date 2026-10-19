# -*- coding: utf-8 -*-
"""性能诊断文本纯格式化（B1 第二步）。

从 core/perf.py 下沉：把已采集的表格位块状态 / 降级阈值参数格式化为
中文诊断行。不 import Qt / core.perf；core.perf 负责采集与异常兜底。
"""


def format_table_blit_status(status: dict) -> str:
    """表格滚动加速状态行（status 为 table_blit_status() 的返回结构）。"""
    if not status["enabled"]:
        return "表格滚动加速：已关闭（perf_table_scroll_blit_v2=false）"
    parts = [f"已生效 {status['applied']}/{status['total']} 张"]
    if status["no_bg"]:
        parts.append(f"未取到底色 {status['no_bg']} 张")
    if status["hidden"]:
        parts.append(f"未显示 {status['hidden']} 张")
    if status["other"]:
        parts.append(f"其他 {status['other']} 张")
    txt = "表格滚动加速：" + "，".join(parts)
    if status["no_bg"]:
        txt += ("（取到底色才生效：表格四周需有 ≥6px 纯色间隙，有遮挡/"
                "浮动面板压边则不生效）")
    return txt


def format_degrade_diagnostics(limit: int, explicit: bool,
                               auto_pixels, raster_rate: float,
                               ref_ms_per_mpx: float,
                               bigscreen: bool) -> str:
    """自动降级阈值诊断行（参数由 core.perf 采集）。"""
    if limit <= 0:
        base = "已关闭（配置 perf_dpi_degrade_pixels=0）"
    elif explicit:
        base = f"{limit / 1e4:.0f} 万像素（配置指定）"
    elif auto_pixels is not None:
        rate = raster_rate or 0.0
        base = (f"{limit / 1e4:.0f} 万像素（自适应：本机合成 "
                f"{rate:.3f} ms/百万像素，基准 {ref_ms_per_mpx:.3f}）")
    else:
        base = f"{limit / 1e4:.0f} 万像素（静态默认，自适应不可用）"
    if bigscreen:
        base += "；大屏性能模式开启（强制降级）"
    return "自动降级阈值：" + base
