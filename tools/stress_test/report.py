# -*- coding: utf-8 -*-
"""压测结果输出：Markdown 报告 + JSON 机读结果"""

import json
import os
from datetime import datetime

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


def _fmt_timer(name: str, t: dict) -> str:
    if not t.get("count"):
        note = t.get("note") or "无样本（可能因内存护栏中止）"
        return f"| {name} | — | — | — | — | {note} |"
    return (f"| {name} | {t['count']} | {t['p50_ms']} | {t['p95_ms']} | "
            f"{t['p99_ms']} | QPS {t['qps']} |")


def to_markdown(results: list, meta: dict) -> str:
    """生成 Markdown 报告（按规模分节，每节列出各场景指标与结论）"""
    lines = [
        "# 售后系统规模化压力测试报告",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 数据规模：{', '.join(str(s) for s in meta.get('scales', []))} 条（三档）",
        f"- 测试场景：{', '.join(meta.get('scenarios', []))}",
        f"- 数据方式：内存级模拟（sqlite3 :memory:，不新建物理库）",
        f"- 资源护栏：RSS 上限 {meta.get('rss_limit_mb')}MB（服务器总内存 1GB 约束）",
        f"- 运行环境：Python {meta.get('python')}，CPU {meta.get('cpu_count')} 核",
        "",
    ]

    # 汇总对比表（各规模 × 关键指标）
    lines += ["## 一、跨规模对比", ""]
    lines.append("| 规模 | 场景 | 关键操作 | p50(ms) | p95(ms) | QPS | RSS峰值(MB) | CPU均值(%) |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for res in results:
        scen = res.get("scenario", "")
        rss = res.get("resources", {}).get("rss_peak_mb", 0)
        cpu = res.get("resources", {}).get("cpu_avg_pct", 0)
        for name, t in res.get("timers", {}).items():
            if not t.get("count"):
                continue
            lines.append(f"| {res.get('scale')} | {scen} | {name} | {t['p50_ms']} | "
                         f"{t['p95_ms']} | {t['qps']} | {rss} | {cpu} |")
    lines.append("")

    # 分场景明细
    lines += ["## 二、场景明细", ""]
    by_scale = {}
    for res in results:
        by_scale.setdefault(res.get("scale"), []).append(res)

    for scale in sorted(by_scale):
        lines += [f"### 数据规模 {scale} 条", ""]
        for res in by_scale[scale]:
            r = res.get("resources", {})
            lines += [
                f"**场景：{res.get('scenario')}**　"
                f"（内存峰值 {r.get('rss_peak_mb')}MB / 均值 {r.get('rss_avg_mb')}MB，"
                f"CPU 均值 {r.get('cpu_avg_pct')}%，护栏中止={res.get('aborted_by_rss_guard')}）",
                "",
                "| 操作 | 样本数 | p50(ms) | p95(ms) | p99(ms) | 吞吐 |",
                "|---|---|---|---|---|---|",
            ]
            for name, t in res.get("timers", {}).items():
                lines.append(_fmt_timer(name, t))
            trend = res.get("rss_trend_mb") or []
            if trend:
                lines += ["", f"内存趋势（等分 10 段，MB）：{' -> '.join(str(x) for x in trend)}"]
            extra = res.get("extra") or {}
            if extra:
                lines += ["", "补充指标：" + "，".join(f"{k}={v}" for k, v in extra.items())]
            lines.append("")

    # 结论与瓶颈
    lines += ["## 三、瓶颈分析与优化建议", ""]
    for res in results:
        for tip in res.get("conclusions", []):
            lines.append(f"- [{res.get('scale')} / {res.get('scenario')}] {tip}")
    lines.append("")

    return "\n".join(lines)


def save(results: list, meta: dict, tag: str = "") -> tuple:
    """保存 JSON 与 Markdown，返回 (json_path, md_path)"""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"stress_{stamp}" + (f"_{tag}" if tag else "")
    jpath = os.path.join(RESULTS_DIR, base + ".json")
    mpath = os.path.join(RESULTS_DIR, base + ".md")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "results": results}, f,
                  ensure_ascii=False, indent=2)
    with open(mpath, "w", encoding="utf-8") as f:
        f.write(to_markdown(results, meta))
    return jpath, mpath
