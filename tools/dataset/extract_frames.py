#!/usr/bin/env python3
"""视频切帧训练集工具：从视频均匀抽帧 + 内容去重，产出用于球识别训练的图片集。

三个子命令对应实际工作流的三步：
  extract  视频 -> 均匀抽 N 帧（先粗抽，人工翻看删掉有球杆/人/手遮挡的废帧）
  dedup    帧目录 -> 内容去重后保留 K 张最不相似的（相邻近重复帧只留代表）
  auto     一步到位：抽 2K 帧 -> 直接去重到 K（跳过人工筛遮挡，适合快出数据）

依赖：仅标准库 + ffmpeg/ffprobe（不装任何第三方包，系统 Python 无 numpy/PIL 也能跑）。
ffmpeg 查找顺序：环境变量 AUTOWORK_FFMPEG -> PATH -> D:\\ffmpeg-7.0.1-essentials_build\\bin。

用法示例：
  python tools/dataset/extract_frames.py extract D:/video/abc.mp4 -o D:/out/frames -n 50
  # （人工删掉遮挡帧后）
  python tools/dataset/extract_frames.py dedup D:/out/frames -o D:/out/train -k 50 --prefix train_
  python tools/dataset/extract_frames.py auto D:/video/abc.mp4 -o D:/out/train -k 50
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

# 本地绿色版 ffmpeg 的兜底路径；优先级低于 PATH 与环境变量
_FALLBACK_DIR = r"D:\ffmpeg-7.0.1-essentials_build\bin"


def _find_tool(name):
    """定位 ffmpeg/ffprobe：环境变量 > PATH > 已知绿色版目录。"""
    env_key = "AUTOWORK_" + name.upper()
    if os.environ.get(env_key):
        return os.environ[env_key]
    found = shutil.which(name)
    if found:
        return found
    fallback = os.path.join(_FALLBACK_DIR, name + ".exe")
    if os.path.isfile(fallback):
        return fallback
    sys.exit(f"[ERR] 找不到 {name}：请安装或设置环境变量 {env_key} 指向可执行文件")


def probe_duration(video):
    """取视频时长（秒），ffprobe 失败直接报错退出，避免静默产出空集。"""
    out = subprocess.run(
        [_find_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration",
         "-of", "json", video],
        capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"[ERR] ffprobe 失败: {out.stderr.strip()}")
    return float(json.loads(out.stdout)["format"]["duration"])


def extract(video, outdir, n=50, prefix="img_", start_number=1):
    """全程均匀抽 n 帧：fps=n/duration 保证首尾都覆盖，间隔恒定。"""
    os.makedirs(outdir, exist_ok=True)
    dur = probe_duration(video)
    pat = os.path.join(outdir, prefix + "%03d.jpg")
    # q:v=2 为 JPEG 高画质档；训练图压缩损失会直接影响小目标（球）的可标注性
    cmd = [_find_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", video,
           "-vf", f"fps={n}/{dur}", "-q:v", "2",
           "-start_number", str(start_number), pat]
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit("[ERR] ffmpeg 抽帧失败")
    made = len([f for f in os.listdir(outdir) if f.startswith(prefix)])
    print(f"[OK] 抽帧完成: {made} 帧 -> {outdir} (时长 {dur:.1f}s, 间隔 {dur / n:.2f}s)")
    return made


def _gray_vec(ffmpeg, path):
    """解码为 32x32 灰度 rawvideo，返回 1024 字节内容指纹。

    选 32x32 灰度而非感知哈希：距离度量连续、无需 DCT，纯 Python 也算得动。
    """
    r = subprocess.run(
        [ffmpeg, "-v", "error", "-i", path,
         "-vf", "scale=32:32", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True)
    if r.returncode != 0 or len(r.stdout) != 32 * 32:
        sys.exit(f"[ERR] 解码失败: {path}")
    return r.stdout


def dedup(indir, outdir, k=50, prefix="train_", dry_run=False):
    """内容去重：算全量两两距离后做最远点采样（FPS），恰好保留 k 张。

    FPS 每轮挑「与已选集最小距离最大」的帧，天然把近似帧挤掉、保留布局差异大的；
    种子取平均距离最大的帧，避免从时间起点开始偏向片头。
    """
    ffmpeg = _find_tool("ffmpeg")
    files = sorted(f for f in os.listdir(indir) if f.lower().endswith((".jpg", ".jpeg", ".png")))
    if len(files) < k:
        sys.exit(f"[ERR] 候选仅 {len(files)} 帧 < 要保留的 {k} 张")
    print(f"[..] 解码 {len(files)} 帧内容指纹 ...")
    vecs = [_gray_vec(ffmpeg, os.path.join(indir, f)) for f in files]

    n = len(files)
    # 距离矩阵只算上三角；O(n^2) 对几百帧规模（<1024 维内积）秒级完成
    dist = [[0.0] * n for _ in range(n)]
    for i in range(n):
        vi = vecs[i]
        for j in range(i + 1, n):
            vj = vecs[j]
            s = 0
            for a, b in zip(vi, vj):
                d = a - b
                s += d * d
            s = s ** 0.5
            dist[i][j] = dist[j][i] = s

    seed = max(range(n), key=lambda i: sum(dist[i]))
    selected = [seed]
    min_d = dist[seed][:]
    while len(selected) < k:
        nxt = max(range(n), key=lambda i: min_d[i])
        selected.append(nxt)
        for i in range(n):
            if dist[nxt][i] < min_d[i]:
                min_d[i] = dist[nxt][i]

    # 按原文件名顺序重编号输出，保证时间顺序与命名一致
    keep = sorted(selected)
    print(f"[OK] 从 {n} 张选出 {k} 张:")
    if dry_run:
        for rank, idx in enumerate(keep, 1):
            print(f"  {rank:03d} <- {files[idx]}")
        return
    os.makedirs(outdir, exist_ok=True)
    for rank, idx in enumerate(keep, 1):
        shutil.copyfile(os.path.join(indir, files[idx]),
                        os.path.join(outdir, f"{prefix}{rank:04d}.jpg"))
    print(f"  -> {outdir} ({prefix}0001.jpg ~ {prefix}{k:04d}.jpg)")


def main():
    ap = argparse.ArgumentParser(description="视频切帧训练集工具（均匀抽帧 + 内容去重）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("extract", help="视频 -> 均匀抽 N 帧")
    p1.add_argument("video")
    p1.add_argument("-o", "--outdir", required=True)
    p1.add_argument("-n", "--num", type=int, default=50, help="抽帧数量（默认 50）")
    p1.add_argument("--prefix", default="img_", help="输出文件名前缀（默认 img_）")

    p2 = sub.add_parser("dedup", help="帧目录 -> 内容去重保留 K 张")
    p2.add_argument("indir")
    p2.add_argument("-o", "--outdir", required=True)
    p2.add_argument("-k", "--keep", type=int, default=50, help="保留数量（默认 50）")
    p2.add_argument("--prefix", default="train_", help="输出文件名前缀（默认 train_）")
    p2.add_argument("--dry-run", action="store_true", help="只打印选择结果，不落盘")

    p3 = sub.add_parser("auto", help="一步到位：抽 2K 帧 -> 去重到 K（跳过人工筛遮挡）")
    p3.add_argument("video")
    p3.add_argument("-o", "--outdir", required=True, help="最终训练集目录")
    p3.add_argument("-k", "--keep", type=int, default=50, help="最终保留数量（默认 50）")
    p3.add_argument("--tmpdir", help="中间帧目录（默认 outdir 同级 _raw）")

    args = ap.parse_args()
    if args.cmd == "extract":
        extract(args.video, args.outdir, args.num, args.prefix)
    elif args.cmd == "dedup":
        dedup(args.indir, args.outdir, args.keep, args.prefix, args.dry_run)
    elif args.cmd == "auto":
        # 2K 粗抽再压缩到 K：给去重留出丢弃空间，近似重复帧多时仍能凑满 K
        tmp = args.tmpdir or os.path.join(
            os.path.dirname(os.path.abspath(args.outdir.rstrip("\\/"))),
            os.path.basename(os.path.abspath(args.outdir.rstrip("\\/"))) + "_raw")
        extract(args.video, tmp, n=args.keep * 2)
        dedup(tmp, args.outdir, args.keep)
        print(f"[HINT] auto 模式未做遮挡人工筛选；正式训练集建议 extract 后人工删废帧再 dedup")


if __name__ == "__main__":
    main()
