# -*- coding: utf-8 -*-
"""场景二：运维面板（大表查询 / 实时刷新 / 图表渲染）

运维面板（windows/management）的数据源以 kd_status 这类大表为主
（真实库已有 4.6 万行），压测点：
1. big_query：带 LIMIT 的分页查询 vs 全量聚合（GROUP BY 分区）——
   与售后 query_page（无 LIMIT）形成对照，验证 SQL 分页的有效性；
2. realtime_refresh：模拟面板 QTimer 周期刷新（连续 N 轮聚合），
   观察单轮耗时与**是否随轮次累积劣化**（趋势分析）；
3. chart_render：offscreen 下用 QPainter 渲染 N 点折线，
   模拟运维趋势图（_TrendChart）在数据量增大时的渲染开销。
"""

import gc
import os
import sqlite3
import sys
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tools.stress_test import metrics  # noqa: E402
from tools.stress_test.data_gen import build_ops_dataset  # noqa: E402

SCENARIO = "ops_panel"


def _qt_bootstrap():
    """offscreen 下安全导入 Qt（含 PySide6 DLL 目录引导，避免 conda Qt 冲突）"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import importlib.util as iu
    spec = iu.find_spec("PySide6")
    if spec is not None:
        pkg = list(getattr(spec, "submodule_search_locations", None) or [])[0]
        for d in (pkg, os.path.dirname(pkg),
                  os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")):
            if os.path.isdir(d):
                try:
                    os.add_dll_directory(d)
                except OSError:
                    pass
        os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(pkg, "plugins"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    return app


def run(scale: int, conn: sqlite3.Connection | None = None,
        repeat: int = 100, rss_limit_mb: int = 500,
        refresh_rounds: int = 30, chart_points: int | None = None) -> dict:
    """执行运维面板压测"""
    own_conn = conn is None
    if conn is None:
        conn = build_ops_dataset(scale)
    chart_points = chart_points or min(scale, 5000)

    sampler = metrics.ResourceSampler(rss_limit_mb=rss_limit_mb)
    timers, refresh_series, aborted = {}, [], False
    sampler.start()
    try:
        # ---------- 1) 大表查询 ----------
        t = metrics.Timer("ops_page_limited")     # 带 LIMIT 的分页（正确范式）
        for p in range(min(repeat, 40)):
            offset = (p % 20) * 50
            t.measure(conn.execute,
                      "SELECT id, table_id, device_code, status FROM kd_status "
                      "ORDER BY id DESC LIMIT 50 OFFSET ?", (offset,))
            if sampler.over_limit:
                aborted = True
                break
        timers["ops_page_limited"] = t
        gc.collect()

        if not aborted:
            t = metrics.Timer("ops_group_by")     # 全量聚合（面板统计口径）
            for _ in range(min(repeat, 20)):
                cost, cur = t.measure(
                    conn.execute,
                    "SELECT substr(file_path,1,7) d, COUNT(*) FROM kd_status "
                    "GROUP BY d ORDER BY d")
                cur.fetchall()
                if sampler.over_limit:
                    aborted = True
                    break
            timers["ops_group_by"] = t
            gc.collect()

        # ---------- 2) 实时刷新（模拟 QTimer 周期） ----------
        if not aborted:
            t = metrics.Timer("ops_realtime_refresh")
            for i in range(refresh_rounds):
                cost, cur = t.measure(
                    conn.execute,
                    "SELECT status, COUNT(*) FROM kd_status WHERE file_path >= ? "
                    "GROUP BY status", (f"2026/0{(i % 8) + 1}",))
                cur.fetchall()
                refresh_series.append(round(cost, 2))
                if sampler.over_limit:
                    aborted = True
                    break
                time.sleep(0.02)   # 模拟面板刷新间隔
            timers["ops_realtime_refresh"] = t
            gc.collect()

        # ---------- 3) 图表渲染（offscreen） ----------
        if not aborted:
            try:
                _qt_bootstrap()
                from PySide6.QtGui import QPainter, QPen, QPolygonF, QImage, QColor
                from PySide6.QtCore import QPointF, Qt
                pts = [QPointF(i, (i * 37 % 211) * 1.0) for i in range(chart_points)]
                poly = QPolygonF(pts)
                img = QImage(900, 420, QImage.Format.Format_ARGB32)
                img.fill(QColor(255, 255, 255))

                def _draw():
                    p = QPainter(img)
                    p.setPen(QPen(QColor(0, 120, 215), 2))
                    p.drawPolyline(poly)
                    p.end()

                t = metrics.Timer("ops_chart_render")
                for _ in range(min(repeat, 30)):
                    t.measure(_draw)
                    if sampler.over_limit:
                        aborted = True
                        break
                timers["ops_chart_render"] = t
                del pts, poly, img
            except Exception as e:      # 无 GUI 环境时跳过图表项
                timers["ops_chart_render"] = {
                    "name": "ops_chart_render", "count": 0,
                    "note": f"skipped: {type(e).__name__}: {e}"}
            gc.collect()
    finally:
        sampler.stop()
        if own_conn:
            conn.close()
        gc.collect()

    return {
        "scenario": SCENARIO,
        "scale": scale,
        "repeat": repeat,
        "aborted_by_rss_guard": aborted,
        "timers": {k: v.summary() if hasattr(v, "summary") else v
                   for k, v in timers.items()},
        "resources": sampler.summary(),
        "rss_trend_mb": sampler.trend(),
        "refresh_series_ms": refresh_series,
        "chart_points": chart_points,
    }


def analyze(result: dict) -> list:
    tips = []
    t = result["timers"]
    lim = t.get("ops_page_limited", {})
    grp = t.get("ops_group_by", {})
    ref = t.get("ops_realtime_refresh", {})
    chart = t.get("ops_chart_render", {})
    if lim.get("p95_ms"):
        tips.append(f"带 LIMIT 分页 p95={lim['p95_ms']}ms（QPS~{lim['qps']}）——"
                    "SQL 分页不受数据量影响，是查询侧的正确范式。")
    if grp.get("p95_ms"):
        tips.append(f"全量聚合 GROUP BY p95={grp['p95_ms']}ms——"
                    "面板统计走全表扫描，建议按日期分区建索引或对热点统计做缓存。")
    series = result.get("refresh_series_ms") or []
    if len(series) >= 6:
        head = sum(series[:len(series) // 3]) / (len(series) // 3)
        tail = sum(series[-len(series) // 3:]) / (len(series) // 3)
        drift = (tail - head) / head * 100 if head else 0
        tips.append(f"实时刷新 {len(series)} 轮：前段均值 {head:.2f}ms -> "
                    f"后段均值 {tail:.2f}ms（漂移 {drift:+.1f}%）；"
                    + ("存在累积劣化，需检查每轮是否重复构建大对象/未释放游标。"
                       if drift > 25 else "无明显累积劣化。"))
    if chart.get("p95_ms"):
        tips.append(f"图表渲染 {result.get('chart_points')} 点 p95={chart['p95_ms']}ms"
                    f"（QPS~{chart['qps']}）。")
    elif chart.get("note"):
        tips.append(f"图表渲染跳过：{chart['note']}")
    return tips
