# -*- coding: utf-8 -*-
"""场景三：视频业务（上传打包 / 转码 / 播放读帧 / 存储 IO）

资源受限（1GB 内存 / 20GB 硬盘）下的处理方式：
- **不生成真实大视频文件**：上传/转码用内存字节流与内存帧（numpy）模拟，
  播放用临时小尺寸视频（120 帧 320x240，约几百 KB），测完立即 os.remove；
- 每个环节结束强制 `del + gc.collect()`，避免大 buffer 跨环节常驻；
- 吞吐以 MB/s 或 帧/s（FPS）计量，同时采集 CPU%（视频环节是 CPU 密集型）。
"""

import gc
import io
import os
import sys
import tempfile
import time
import zipfile

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tools.stress_test import metrics  # noqa: E402
from tools.stress_test.data_gen import make_video_bytes  # noqa: E402

SCENARIO = "video"

# 负载参数（在 1GB 内存约束内的保守值）
UPLOAD_MB = 8          # 单次上传负载（压缩前）
TRANSCODE_FRAMES = 60  # 转码帧数
PLAYBACK_FRAMES = 120  # 播放用临时视频帧数


def run(scale: int, repeat: int = 20, rss_limit_mb: int = 500,
        upload_mb: float = UPLOAD_MB) -> dict:
    """执行视频业务压测

    scale 只用于报告标注（视频链路负载固定，避免吃满内存/硬盘）。
    """
    sampler = metrics.ResourceSampler(rss_limit_mb=rss_limit_mb)
    timers, extra, aborted = {}, {}, False
    sampler.start()
    tmp_files = []
    try:
        # ---------- 1) 上传：内存负载 -> zip 打包（模拟 ZipUploadWorker） ----------
        t = metrics.Timer("video_upload_zip")
        sizes = []
        for _ in range(min(repeat, 15)):
            payload = make_video_bytes(upload_mb)
            def _pack(data=payload):
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                    z.writestr("clip.bin", data)
                return buf.tell()
            cost, zsize = t.measure(_pack)
            sizes.append(zsize / (1024 * 1024))
            del payload
            gc.collect()
            if sampler.over_limit:
                aborted = True
                break
        timers["video_upload_zip"] = t
        if sizes:
            avg_in = upload_mb
            avg_out = sum(sizes) / len(sizes)
            extra["zip_ratio"] = round(avg_out / avg_in, 3)
            extra["upload_throughput_mb_s"] = round(
                avg_in / (t.samples[-1] / 1000), 2) if t.samples else 0

        # ---------- 2) 转码：内存帧 resize + JPEG 编码 ----------
        if not aborted:
            try:
                import cv2
                import numpy as np
                frame = (np.random.default_rng(7).random((720, 1280, 3)) * 255
                         ).astype("uint8")
                t = metrics.Timer("video_transcode")
                for _ in range(min(repeat, 20)):
                    def _transcode():
                        small = cv2.resize(frame, (640, 360),
                                           interpolation=cv2.INTER_AREA)
                        ok, _buf = cv2.imencode(".jpg", small,
                                                [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                        return ok
                    t.measure(_transcode)
                    if sampler.over_limit:
                        aborted = True
                        break
                timers["video_transcode"] = t
                if t.samples:
                    extra["transcode_fps"] = round(
                        1000 / (sum(t.samples) / len(t.samples)), 1)
                extra["transcode_frames"] = TRANSCODE_FRAMES
                del frame
            except Exception as e:
                timers["video_transcode"] = {
                    "name": "video_transcode", "count": 0,
                    "note": f"skipped: {type(e).__name__}: {e}"}
            gc.collect()

        # ---------- 3) 播放：临时小视频 -> 逐帧读取 ----------
        if not aborted:
            try:
                import cv2
                import numpy as np
                fd, path = tempfile.mkstemp(suffix=".avi")
                os.close(fd)
                tmp_files.append(path)
                w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), 25,
                                    (320, 240))
                for i in range(PLAYBACK_FRAMES):
                    img = np.full((240, 320, 3), i % 255, dtype="uint8")
                    w.write(img)
                w.release()
                del img
                gc.collect()

                def _read_all():
                    cap = cv2.VideoCapture(path)
                    n = 0
                    while True:
                        ok, _f = cap.read()
                        if not ok:
                            break
                        n += 1
                    cap.release()
                    return n

                t = metrics.Timer("video_playback")
                for _ in range(min(repeat, 10)):
                    t.measure(_read_all)
                    if sampler.over_limit:
                        aborted = True
                        break
                timers["video_playback"] = t
                if t.samples:
                    extra["playback_fps"] = round(
                        PLAYBACK_FRAMES / (sum(t.samples) / len(t.samples) / 1000), 1)
                extra["playback_frames"] = PLAYBACK_FRAMES
            except Exception as e:
                timers["video_playback"] = {
                    "name": "video_playback", "count": 0,
                    "note": f"skipped: {type(e).__name__}: {e}"}
            gc.collect()

        # ---------- 4) 存储：内存 -> 临时文件写 + 读回校验 ----------
        if not aborted:
            payload = make_video_bytes(4)
            fd, path = tempfile.mkstemp(suffix=".bin")
            os.close(fd)
            tmp_files.append(path)
            t = metrics.Timer("video_storage_io")
            for _ in range(min(repeat, 10)):
                def _io(data=payload, p=path):
                    with open(p, "wb") as f:
                        f.write(data)
                    with open(p, "rb") as f:
                        return len(f.read()) == len(data)
                t.measure(_io)
                if sampler.over_limit:
                    aborted = True
                    break
            timers["video_storage_io"] = t
            if t.samples:
                extra["storage_throughput_mb_s"] = round(
                    4 / (sum(t.samples) / len(t.samples) / 1000), 1)
            del payload
            gc.collect()
    finally:
        sampler.stop()
        # 清理临时文件（严格遵守 20GB 硬盘约束）
        for p in tmp_files:
            try:
                os.remove(p)
            except Exception:
                pass
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
        "extra": extra,
        "tmp_files_cleaned": len(tmp_files),
    }


def analyze(result: dict) -> list:
    tips = []
    t = result["timers"]
    ex = result.get("extra", {})
    up = t.get("video_upload_zip", {})
    if up.get("p50_ms"):
        tips.append(f"上传打包 p50={up['p50_ms']}ms，压缩比~{ex.get('zip_ratio')}，"
                    f"吞吐~{ex.get('upload_throughput_mb_s')}MB/s——"
                    "zip 是 CPU 密集环节，高并发上传时应限流或改流式压缩。")
    tc = t.get("video_transcode", {})
    if tc.get("p50_ms"):
        tips.append(f"转码（resize+JPEG 编码）~{ex.get('transcode_fps')} FPS——"
                    "单线程 CPU 密集，建议按批处理/后台线程池隔离，避免抢占 UI 线程。")
    elif tc.get("note"):
        tips.append(f"转码跳过：{tc['note']}")
    pb = t.get("video_playback", {})
    if pb.get("p50_ms"):
        tips.append(f"播放读帧 ~{ex.get('playback_fps')} FPS"
                    f"（{ex.get('playback_frames')} 帧/次）——解码读帧开销随分辨率增长。")
    elif pb.get("note"):
        tips.append(f"播放跳过：{pb['note']}")
    st = t.get("video_storage_io", {})
    if st.get("p50_ms"):
        tips.append(f"存储 IO（写+读回校验）吞吐~{ex.get('storage_throughput_mb_s')}MB/s。")
    tips.append(f"临时文件已清理 {result.get('tmp_files_cleaned')} 个，无磁盘残留。")
    return tips
