# -*- coding: utf-8 -*-
"""压力测试入口（CLI）

用法：
    # 单规模快速验证（推荐先跑）
    python tools/stress_test/run_stress.py --scale 10k

    # 全量三档 + 全场景
    python tools/stress_test/run_stress.py --scale 10k,50k,100k \
        --scenarios aftersale,ops,video --repeat 30

    # 只做数据生成与资源预检（不跑场景）
    python tools/stress_test/run_stress.py --scale 100k --dry-run

参数：
    --scale       数据规模（10k/50k/100k），逗号分隔；默认 10k
    --scenarios   场景（aftersale/ops/video），逗号分隔；默认全跑
    --repeat      每项操作重复次数（默认 20；耗时项内部再限流）
    --rss-limit   内存护栏阈值 MB（默认 500，服务器 1GB 约束）
    --out         报告文件名标签

资源安全设计：
- 数据装载/场景之间强制 gc.collect()；
- ResourceSampler 持续采样 RSS，超过 rss-limit 立即中止当前场景并标记；
- 视频场景的临时文件用完即删；数据全在内存库，进程退出即释放全部占用。
"""

import argparse
import gc
import os
import platform
import shutil
import sys
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tools.stress_test import metrics, report  # noqa: E402
from tools.stress_test.data_gen import (  # noqa: E402
    SCALE_PRESETS, build_memory_db, build_ops_dataset, estimate_memory_mb)


def _check_resources(scales, rss_limit_mb):
    """开跑前资源自检（内存/磁盘），超预算给出警告"""
    print("[预检] 资源自检：")
    try:
        import psutil
        total = psutil.virtual_memory().total / (1024 * 1024)
        avail = psutil.virtual_memory().available / (1024 * 1024)
        print(f"  内存：总 {total:.0f}MB / 可用 {avail:.0f}MB　"
              f"护栏 {rss_limit_mb}MB")
        if avail < rss_limit_mb * 1.2:
            print("  [WARN] 可用内存接近护栏，建议降低 --rss-limit 或先跑小档")
    except Exception as e:
        print(f"  （psutil 不可用，跳过内存自检：{e}）")
    try:
        usage = shutil.disk_usage(_PROJECT_ROOT)
        free_gb = usage.free / (1024 ** 3)
        print(f"  磁盘：可用 {free_gb:.1f}GB"
              f"（本套件仅写 results/ 报告，数据全在内存）")
        if free_gb < 1:
            print("  [WARN] 磁盘可用空间不足 1GB，报告写入可能失败")
    except Exception:
        pass
    for s in scales:
        print(f"  规模 {s}：预计数据集常驻 ~ {estimate_memory_mb(s)}MB")


def main():
    ap = argparse.ArgumentParser(description="售后系统规模化压力测试")
    ap.add_argument("--scale", default="10k",
                    help="数据规模：10k/50k/100k，逗号分隔（默认 10k）")
    ap.add_argument("--scenarios", default="aftersale,ops,video",
                    help="场景：aftersale/ops/video，逗号分隔")
    ap.add_argument("--repeat", type=int, default=20, help="每项重复次数")
    ap.add_argument("--rss-limit", type=int, default=500,
                    help="内存护栏阈值 MB（默认 500）")
    ap.add_argument("--out", default="", help="报告文件名标签")
    ap.add_argument("--dry-run", action="store_true",
                    help="只装载数据并预检，不执行场景")
    args = ap.parse_args()

    scales = []
    for s in args.scale.split(","):
        s = s.strip()
        # 注意：dict.get(k, default) 会先求值 default，故不能用 int(s) 兜底
        scales.append(SCALE_PRESETS[s] if s in SCALE_PRESETS else int(s))
    scen_names = [s.strip() for s in args.scenarios.split(",") if s.strip()]

    print("=" * 68)
    print("售后系统规模化压力测试")
    print(f"规模: {scales}　场景: {scen_names}　重复: {args.repeat}　"
          f"护栏: {args.rss_limit}MB")
    print("=" * 68)

    _check_resources(scales, args.rss_limit)
    if args.dry_run:
        for n in scales:
            t0 = time.perf_counter()
            conn = build_memory_db(n)
            cnt = conn.execute(
                "SELECT COUNT(*) FROM aftersale_records").fetchone()[0]
            print(f"  [dry-run] {n} 条装载完成：{cnt} 条，"
                  f"耗时 {(time.perf_counter() - t0) * 1000:.0f}ms，"
                  f"RSS~{metrics.rss_mb()}MB")
            conn.close()
            gc.collect()
        print("预检通过，未执行场景。")
        return 0

    from tools.stress_test.scenarios import aftersale, ops_panel, video
    runners = {"aftersale": aftersale, "ops": ops_panel, "ops_panel": ops_panel,
               "video": video}

    results, meta_scenarios = [], []
    for n in scales:
        print(f"\n--- 规模 {n} 条 ---")
        conn = None
        t_load = time.perf_counter()
        if "aftersale" in scen_names:
            conn = build_memory_db(n)
        ops_conn = build_ops_dataset(n) if ("ops" in scen_names
                                            or "ops_panel" in scen_names) else None
        print(f"    数据装载完成，耗时 "
              f"{(time.perf_counter() - t_load) * 1000:.0f}ms，RSS~{metrics.rss_mb()}MB")

        for name in scen_names:
            runner = runners.get(name)
            if runner is None:
                print(f"    [WARN] 未知场景 {name}，跳过")
                continue
            print(f"    执行场景 {name} ...")
            t0 = time.perf_counter()
            if name == "aftersale":
                res = runner.run(n, conn=conn, repeat=args.repeat,
                                 rss_limit_mb=args.rss_limit)
            elif name in ("ops", "ops_panel"):
                res = runner.run(n, conn=ops_conn, repeat=args.repeat,
                                 rss_limit_mb=args.rss_limit)
            else:
                res = runner.run(n, repeat=args.repeat,
                                 rss_limit_mb=args.rss_limit)
            res["conclusions"] = runner.analyze(res)
            results.append(res)
            meta_scenarios.append(name)
            peak = res.get("resources", {}).get("rss_peak_mb")
            print(f"      [OK] 完成，耗时 {(time.perf_counter() - t0):.1f}s，"
                  f"内存峰值 {peak}MB"
                  f"{'（护栏中止）' if res.get('aborted_by_rss_guard') else ''}")
            metrics.recycle()
            print(f"      回收后 RSS~{metrics.rss_mb()}MB")

        if conn is not None:
            conn.close()
        if ops_conn is not None:
            ops_conn.close()
        metrics.recycle()

    meta = {
        "scales": scales,
        "scenarios": sorted(set(meta_scenarios)),
        "repeat": args.repeat,
        "rss_limit_mb": args.rss_limit,
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "data_mode": "memory(sqlite3 :memory:)",
    }
    jpath, mpath = report.save(results, meta, tag=args.out)
    print("\n" + "=" * 68)
    print(f"报告已生成：\n  JSON: {jpath}\n  Markdown: {mpath}")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
