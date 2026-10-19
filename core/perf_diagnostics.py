# -*- coding: utf-8 -*-
"""性能诊断文本纯格式化（B1 第二步）。

从 core/perf.py 下沉：把已采集的表格位块状态 / 降级阈值参数格式化为
中文诊断行。不 import Qt / core.perf；core.perf 负责采集与异常兜底。

刻意保持零依赖：这些函数要在没有 PySide6 的解释器里直接跑单测，
一旦沾上 Qt，格式化逻辑就只能靠真机验证，改一个字都要开窗口。
"""


def format_table_blit_status(status: dict) -> str:
    """表格滚动加速状态行（status 为 table_blit_status() 的返回结构）。

    这里只拼装不判定：哪些表算「未生效」由采集侧归类，本函数拿到的
    已经是分好桶的计数，改了归类规则不需要跟着改格式化。
    """
    if not status["enabled"]:
        # 关闭时不再输出各项计数：一堆 0 只会让人误以为加速坏了
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
    """自动降级阈值诊断行（参数由 core.perf 采集）。

    分支顺序就是优先级：显式配置 > 本机自适应 > 静态默认。自适应值来自
    上次渲染实测，换机器或换驱动都会漂移，所以必须把基准一起打出来，
    否则用户只会发现阈值莫名其妙变了，却找不到是谁改的。
    """
    # limit<=0 是「关闭」的唯一约定；其余分支都假定它已是有效的像素上限
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
    # 大屏模式单独追加而不是并进阈值来源：用户需要分清阈值是配置定的
    # 还是环境逼的，混在一句里就没法判断该改哪个开关
    if bigscreen:
        base += "；大屏性能模式开启（强制降级）"
    return "自动降级阈值：" + base
