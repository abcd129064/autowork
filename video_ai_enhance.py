#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
video_ai_enhance.py — 视频 AI 画质增强一键脚本

流程：抽帧 -> Real-ESRGAN 多 GPU 并行超分 -> (可选)AI/原片混合 -> 降采样
      -> x264 两遍编码压到目标体积（或 CRF 模式）-> 清理中间产物

依赖：
  - ffmpeg / ffprobe 在 PATH 中
  - realesrgan-ncnn-vulkan.exe（默认路径见 CONFIG['esrgan_exe']，可用 --esrgan 覆盖）

用法示例：
  # 最常用：压到 100MB，70% AI 混合，双卡并行
  python tools/video_ai_enhance.py "D:/video/input.mp4" --target-size-mb 100 --blend 0.7

  # 质量优先：不控体积，CRF 16，全 AI 无混合
  python tools/video_ai_enhance.py "D:/video/input.mp4" --crf 16 --blend 1.0

  # 只看执行计划不实际跑
  python tools/video_ai_enhance.py "D:/video/input.mp4" --target-size-mb 100 --dry-run

  # 指定 GPU（Vulkan 设备号）与速度权重、保留中间文件
  python tools/video_ai_enhance.py "D:/video/input.mp4" --gpus 0,2 --gpu-weights 7.4,1 --keep-temp

  # 输出降采样到指定高度（默认 = 超分后原始高度，如 4x 后的 2168）
  python tools/video_ai_enhance.py "D:/video/input.mp4" --crf 16 --out-height 1084
"""

import argparse
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from os import path

# ---------------------------------------------------------------------------
# 默认配置（CLI 参数未指定时生效；CLI 优先级高于这里）
# ---------------------------------------------------------------------------
CONFIG = {
    # Real-ESRGAN 可执行文件路径
    "esrgan_exe": r"C:/Users/shen_zhe/WorkBuddy/2026-10-01-16-57-14/video_enhance/realesrgan/realesrgan-ncnn-vulkan.exe",
    # 超分模型：realesrgan-x4plus(真实场景最优质) / realesr-animevideov3-x2(动画/快速)
    "model": "realesrgan-x4plus",
    # 超分倍率（x4plus 只支持 4；animevideov3 支持 2/3/4）
    "scale": 4,
    # AI 画面占比：1.0=纯AI(锐利/可能塑料感)，0.7=推荐，0.5=更自然
    "blend": 0.7,
    # 输出高度：None=保持超分原始分辨率；整数=降采样到该高度（宽度按比例）
    "out_height": None,
    # 目标体积 MB（设置后走两遍编码精确控体积）；与 crf 二选一，同时给时 target-size-mb 优先
    "target_size_mb": 100,
    # CRF 质量模式（仅当 target_size_mb 为 None 时生效）
    "crf": 16,
    # Vulkan GPU 设备号列表；None=自动探测全部 NVIDIA 设备
    "gpus": None,
    # 各 GPU 速度权重（与 gpus 一一对应）；None=平均分配
    "gpu_weights": None,
    # ncnn tile 尺寸：0=自动；小显存 GPU 建议 128
    "tile": 0,
    # 中间编码 CRF（拼接前的分段文件质量）
    "inter_crf": 14,
    # x264 preset（最终编码）
    "preset": "medium",
    # 保留中间产物（调试用）
    "keep_temp": False,
    # 临时目录；None=系统临时目录下自动建
    "tmpdir": None,
}


def log(msg):
    print("[{0}] {1}".format(time.strftime("%H:%M:%S"), msg), flush=True)


def die(msg, code=1):
    print("[错误] {0}".format(msg), file=sys.stderr, flush=True)
    sys.exit(code)


def run(cmd, **kw):
    """执行外部命令，出错时抛异常。"""
    log("$ " + " ".join(str(c) for c in cmd))
    p = subprocess.run([str(c) for c in cmd], **kw)
    if p.returncode != 0:
        raise RuntimeError("命令失败(exit={0}): {1}".format(p.returncode, cmd[0]))
    return p


def probe_video(src):
    """返回 (width, height, fps, duration, n_frames, has_audio)"""
    def _probe(args):
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0"]
            + args + ["-of", "default=noprint_wrappers=1:nokey=1", src],
            capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError("ffprobe 失败: " + p.stderr.strip()[:300])
        return p.stdout.strip()

    w, h = _probe(["-show_entries", "stream=width,height"]).splitlines()[:2]
    fps_raw = _probe(["-show_entries", "stream=r_frame_rate"])[0:32]
    num, _, den = fps_raw.partition("/")
    fps = float(num) / float(den or 1)
    dur = float(_probe(["-show_entries", "format=duration"]))
    n = _probe(["-count_packets", "-show_entries", "stream=nb_read_packets"])
    n_frames = int(float(n))
    a = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_name", "-of",
         "default=noprint_wrappers=1:nokey=1", src],
        capture_output=True, text=True)
    has_audio = bool(a.stdout.strip())
    return int(w), int(h), fps, dur, n_frames, has_audio


def detect_gpus(esrgan_exe):
    """跑单帧探测，解析 realesrgan 输出的 Vulkan 设备列表，返回 NVIDIA 设备号列表。"""
    with tempfile.TemporaryDirectory() as td:
        gray = path.join(td, "in.jpg")
        run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", "color=black:s=64x64:d=0.04", "-frames:v", "1", gray])
        p = subprocess.run(
            [esrgan_exe, "-i", gray, "-o", path.join(td, "out.png"),
             "-n", "realesrgan-x4plus", "-s", "4", "-f", "png"],
            capture_output=True, text=True, timeout=300)
        text = p.stdout + p.stderr
    devices = []
    seen = set()
    for m in re.finditer(r"\[(\d+) ([^\]]+)\]", text):
        idx, name = int(m.group(1)), m.group(2).strip()
        if idx not in seen:
            seen.add(idx)
            devices.append((idx, name))
    nvidia = [(idx, name) for idx, name in devices if "NVIDIA" in name]
    if not nvidia:
        die("未探测到 NVIDIA Vulkan 设备。完整设备列表:\n" + text[:800])
    return nvidia


def benchmark_gpus(esrgan_exe, model, scale, tile, src, gpus, work_dir, n_frames=10):
    """每卡实测处理 n_frames 帧的速率，返回与 gpus 对应的权重列表。"""
    bench_dir = path.join(work_dir, "bench_in")
    bench_out = path.join(work_dir, "bench_out")
    os.makedirs(bench_dir, exist_ok=True)
    run(["ffmpeg", "-y", "-v", "error", "-i", src,
         "-vf", "select='not(mod(n,20))'",
         "-frames:v", str(n_frames), "-vsync", "0", "-qscale:v", "1",
         path.join(bench_dir, "%06d.jpg")])
    weights = []
    for gpu in gpus:
        gpu_out = path.join(bench_out, "g{0}".format(gpu))
        os.makedirs(gpu_out, exist_ok=True)  # 必须预创建，否则exe把路径当文件校验扩展名
        t0 = time.time()
        upscale_segment(esrgan_exe, model, scale, tile, gpu, bench_dir, gpu_out)
        dt = time.time() - t0
        fps = n_frames / max(dt, 0.001)
        weights.append(fps)
        log("GPU{0} 基准: {1} 帧 / {2:.1f}s = {3:.2f} fps".format(gpu, n_frames, dt, fps))
    shutil.rmtree(bench_dir, ignore_errors=True)
    shutil.rmtree(bench_out, ignore_errors=True)
    return weights


def split_ranges(n_frames, gpus, weights):
    """按速度权重把 [0, n_frames-1] 切成连续区间，返回 [(gpu, start, count), ...]"""
    if len(gpus) != len(weights):
        die("gpus 与 gpu_weights 数量不一致: {0} vs {1}".format(gpus, weights))
    total_w = float(sum(weights))
    ranges, cursor = [], 0
    for i, gpu in enumerate(gpus):
        if i == len(gpus) - 1:
            count = n_frames - cursor
        else:
            count = int(round(n_frames * weights[i] / total_w))
        if count > 0:
            ranges.append((gpu, cursor, count))
            cursor += count
    if cursor != n_frames:
        die("帧数分配错误: {0} != {1}".format(cursor, n_frames))
    return ranges


def extract_segment(src, start, count, out_dir):
    """从视频抽取 [start, start+count) 帧（0基），文件名全局编号从 start+1 开始。"""
    run(["ffmpeg", "-y", "-v", "error", "-i", src,
         "-vf", "select='between(n,{0},{1})'".format(start, start + count - 1),
         "-vsync", "0", "-qscale:v", "1",
         "-start_number", start + 1, path.join(out_dir, "%06d.jpg")])


def upscale_segment(esrgan_exe, model, scale, tile, gpu, in_dir, out_dir):
    cmd = [esrgan_exe, "-i", in_dir, "-o", out_dir,
           "-n", model, "-s", scale, "-f", "png", "-g", gpu]
    if tile:
        cmd += ["-t", tile]
    run(cmd)


def encode_segment(in_png_dir, in_jpg_dir, start, fps, blend, out_h, scale,
                   crf, out_file):
    """单段：混合(可关) + 降采样(可选) + 高质量中间编码。"""
    if blend < 0.999:
        # AI 帧为主输入，原片帧按超分倍率放大后混合
        inputs = (["-start_number", str(start + 1), "-framerate", str(fps),
                   "-i", path.join(in_png_dir, "%06d.png")]
                  + ["-start_number", str(start + 1), "-framerate", str(fps),
                     "-i", path.join(in_jpg_dir, "%06d.jpg")])
        chain = ("[1:v]scale=iw*{m:d}:ih*{m:d}:flags=lanczos,format=rgb24[b];"
                 "[0:v]format=rgb24[a];"
                 "[a][b]blend=all_expr='A*{ai:.4f}+B*{orig:.4f}'").format(
                     m=scale, ai=blend, orig=1.0 - blend)
    else:
        inputs = ["-start_number", str(start + 1), "-framerate", str(fps),
                  "-i", path.join(in_png_dir, "%06d.png")]
        chain = "[0:v]format=rgb24"
    if out_h:
        chain += ",scale=-2:{0}:flags=lanczos".format(out_h)
    chain += ",format=yuv420p"
    run(["ffmpeg", "-y", "-v", "warning"] + inputs +
        ["-filter_complex", chain, "-c:v", "libx264", "-crf", crf,
         "-preset", "fast", out_file])


def concat_and_final(segments, fps, target_mb, crf, preset, src, has_audio, out_file):
    list_file = path.join(path.dirname(segments[0]), "concat.txt")
    with open(list_file, "w") as f:
        for s in segments:
            f.write("file '" + path.abspath(s).replace("\\", "/") + "'\n")
    base = ["ffmpeg", "-y", "-v", "warning", "-f", "concat", "-safe", "0",
            "-i", list_file]
    enc = ["-c:v", "libx264", "-preset", preset]
    if target_mb:
        # 用源视频时长计算码率（与成片等长）
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", src],
            capture_output=True, text=True).stdout.strip() or 0)
        if dur <= 0:
            die("无法获取视频时长，无法计算目标码率")
        kbps = int(target_mb * 8192 * 0.98 / dur)
        log("两遍编码目标码率 {0} kbps (时长 {1:.1f}s)".format(kbps, dur))
        run(base + enc + ["-b:v", "{0}k".format(kbps), "-pass", "1", "-an",
                          "-f", "mp4", path.join(path.dirname(list_file), "null.mp4")])
        audio = []
        if has_audio:
            audio = ["-i", src, "-map", "0:v", "-map", "1:a", "-c:a", "copy"]
        run(base + enc + ["-b:v", "{0}k".format(kbps), "-pass", "2"] + audio +
            ["-movflags", "+faststart", out_file])
    else:
        audio = []
        if has_audio:
            audio = ["-i", src, "-map", "0:v", "-map", "1:a", "-c:a", "copy"]
        run(base + enc + ["-crf", crf] + audio +
            ["-movflags", "+faststart", out_file])


def main():
    ap = argparse.ArgumentParser(
        description="视频 AI 画质增强（Real-ESRGAN 多 GPU 并行 + x264 压制）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("input", help="输入视频路径")
    ap.add_argument("-o", "--output", default=None,
                    help="输出视频路径（默认: 输入名_ai_enhanced.mp4）")
    ap.add_argument("--esrgan", default=CONFIG["esrgan_exe"], help="realesrgan-ncnn-vulkan 可执行文件")
    ap.add_argument("--model", default=CONFIG["model"],
                    help="模型: realesrgan-x4plus / realesr-animevideov3-x2 等")
    ap.add_argument("--scale", type=int, default=CONFIG["scale"], help="超分倍率")
    ap.add_argument("--blend", type=float, default=CONFIG["blend"],
                    help="AI 占比 0.0~1.0，0.7=推荐，1.0=纯AI")
    ap.add_argument("--out-height", type=int, default=CONFIG["out_height"],
                    help="输出高度，0=保持超分原始分辨率")
    ap.add_argument("--target-size-mb", type=float, default=CONFIG["target_size_mb"],
                    help="目标体积MB(两遍编码)；0=不用体积模式")
    ap.add_argument("--crf", type=int, default=CONFIG["crf"],
                    help="CRF 模式（仅 target-size-mb=0 时生效）")
    ap.add_argument("--gpus", default=CONFIG["gpus"],
                    help="Vulkan 设备号，逗号分隔，如 0,2；缺省=自动探测")
    ap.add_argument("--gpu-weights", default=CONFIG["gpu_weights"],
                    help="各GPU速度权重，逗号分隔，如 7.4,1；缺省=平均")
    ap.add_argument("--tile", type=int, default=CONFIG["tile"], help="ncnn tile，0=自动")
    ap.add_argument("--inter-crf", type=int, default=CONFIG["inter_crf"], help="分段中间编码CRF")
    ap.add_argument("--preset", default=CONFIG["preset"], help="最终x264 preset")
    ap.add_argument("--keep-temp", action="store_true", help="保留中间产物")
    ap.add_argument("--tmpdir", default=CONFIG["tmpdir"], help="临时目录根")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不执行")
    args = ap.parse_args()

    src = path.abspath(args.input)
    if not path.isfile(src):
        die("输入文件不存在: " + src)
    if not path.isfile(args.esrgan):
        die("realesrgan-ncnn-vulkan.exe 不存在: " + args.esrgan +
            "\n（从 https://github.com/xinntao/Real-ESRGAN/releases 下载，"
            "直连失败可用 https://gh-proxy.com/ 前缀加速）")
    out = path.abspath(args.output) if args.output else path.join(
        path.dirname(src), path.splitext(path.basename(src))[0] + "_ai_enhanced.mp4")

    # ---- 参数规范化 ----
    blend = min(1.0, max(0.0, args.blend))
    out_h = args.out_height if args.out_height else None
    target_mb = args.target_size_mb if args.target_size_mb else None
    if target_mb and target_mb <= 0:
        target_mb = None

    log("探测视频信息: " + src)
    w, h, fps, dur, n_frames, has_audio = probe_video(src)
    fps_str = "{0:.6f}".format(fps)
    log("分辨率 {0}x{1} @ {2:.3f}fps, 时长 {3:.1f}s, 共 {4} 帧, 音频: {5}".format(
        w, h, fps, dur, n_frames, "有" if has_audio else "无"))
    final_h = out_h if out_h else h * args.scale
    log("计划: 模型={0} x{1} | blend={2} | 输出高度={3} | {4}".format(
        args.model, args.scale, blend, final_h,
        ("目标 {0}MB 两遍编码".format(target_mb) if target_mb
         else "CRF {0}".format(args.crf))))

    # ---- GPU 探测与分段 ----
    if args.gpus:
        gpus = [int(g) for g in str(args.gpus).split(",") if g.strip() != ""]
        names = {g: "GPU{0}".format(g) for g in gpus}
    else:
        detected = detect_gpus(args.esrgan)
        gpus = [g for g, _ in detected]
        names = dict(detected)
        log("自动探测到 NVIDIA 设备: " + ", ".join("[{0}] {1}".format(g, n) for g, n in detected))
    if args.gpu_weights:
        weights = [float(x) for x in str(args.gpu_weights).split(",") if x.strip() != ""]
    elif args.dry_run:
        weights = [1.0] * len(gpus)
        log("[dry-run] 未指定权重，按平均分配示意（实际运行会先基准测试）")
    else:
        bench_root = path.join(tempfile.gettempdir(), "video_ai_enhance_bench")
        os.makedirs(bench_root, exist_ok=True)
        log("基准测试中（每卡 10 帧）...")
        weights = benchmark_gpus(args.esrgan, args.model, args.scale,
                                 args.tile, src, gpus, bench_root)
        shutil.rmtree(bench_root, ignore_errors=True)
    ranges = split_ranges(n_frames, gpus, weights)
    for gpu, start, count in ranges:
        log("分段: GPU{0}({1}) 处理帧 {2}~{3} 共 {4} 帧".format(
            gpu, names.get(gpu, "?"), start + 1, start + count, count))
    if args.dry_run:
        log("[dry-run] 计划如上，未执行。")
        return

    # ---- 执行 ----
    tmp_root = args.tmpdir or path.join(tempfile.gettempdir(), "video_ai_enhance")
    work = path.join(tmp_root, time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(work, exist_ok=True)
    log("工作目录: " + work)

    try:
        threads, errors = [], {}
        segs_info = []
        for gpu, start, count in ranges:
            in_dir = path.join(work, "in_g{0}".format(gpu))
            out_dir = path.join(work, "up_g{0}".format(gpu))
            os.makedirs(in_dir, exist_ok=True)
            os.makedirs(out_dir, exist_ok=True)
            segs_info.append((gpu, start, count, in_dir, out_dir))
            extract_segment(src, start, count, in_dir)

        t0 = time.time()
        for gpu, start, count, in_dir, out_dir in segs_info:
            def _worker(g=gpu, s=start, i=in_dir, o=out_dir):
                try:
                    log("GPU{0} 开始超分...".format(g))
                    upscale_segment(args.esrgan, args.model, args.scale,
                                    args.tile, g, i, o)
                    log("GPU{0} 超分完成".format(g))
                except Exception as exc:  # noqa
                    errors[g] = str(exc)
            th = threading.Thread(target=_worker)
            th.start()
            threads.append(th)
        for th in threads:
            th.join()
        if errors:
            die("超分失败: " + "; ".join("{0}: {1}".format(k, v[:200]) for k, v in errors.items()))
        log("全部超分完成，耗时 {0:.1f} 分钟".format((time.time() - t0) / 60))

        # ---- 分段编码 ----
        seg_files = []
        for idx, (gpu, start, count, in_dir, out_dir) in enumerate(segs_info):
            seg_file = path.join(work, "seg{0}.mkv".format(idx))
            log("编码分段 {0}/{1}（帧 {2}~{3}）...".format(idx + 1, len(segs_info), start + 1, start + count))
            encode_segment(out_dir, in_dir, start, fps_str, blend, out_h,
                           args.scale, args.inter_crf, seg_file)
            seg_files.append(seg_file)

        # ---- 拼接 + 最终编码 ----
        log("拼接并最终编码...")
        concat_and_final(seg_files, fps_str, target_mb, args.crf,
                         args.preset, src, has_audio, out)
        if path.isfile(out):
            mb = path.getsize(out) / 1048576.0
            log("完成: {0} ({1:.1f} MB)".format(out, mb))
        else:
            die("输出文件未生成: " + out)
    finally:
        if args.keep_temp:
            log("保留中间产物: " + work)
        else:
            log("清理中间产物: " + work)
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        die("用户中断", 130)
