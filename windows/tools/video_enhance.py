# -*- coding: utf-8 -*-
"""ffmpeg 画质提升后端（工具页「画质提升」工作区）

设计稿：design/video_enhance_tool.html

只用 ffmpeg 原生滤镜，不接任何外部 AI 超分可执行文件——仓库根的
video_ai_enhance.py 走的是 Real-ESRGAN 逐帧超分路线，画质上限更高但要 NVIDIA 卡
加一个几 MB 的 exe，而且 18 分钟视频要跑一小时。本模块换一条路：去噪 → 放大 →
锐化 → 色彩 → 补帧 → 编码，全部由一条 -vf 链在一次 ffmpeg 调用里完成，任何有
ffmpeg 的机器都能跑。

滤镜顺序不是随便排的：
  - 去噪在放大之前，否则插值会把噪点一起放大成块状伪影；
  - 锐化在放大之后，锐的是插值产生的软边缘，而不是原始噪点；
  - format=yuv420p 永远收尾，源是 10bit/422 时不转会导致大量播放器黑屏。

本文件不 import Qt，全部是纯函数 + 子进程，可离线单测（tests/test_video_enhance.py）。
"""
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time

# ==================== 异常 ====================


class EnhanceError(Exception):
    """可展示给用户的失败原因（终端与 InfoBar 直接用这条文案）"""


class EnhanceCancelled(Exception):
    """用户取消：调用方据此清理半成品输出，不当作错误上报"""


# ==================== 参数表（UI 下拉项的单一来源） ====================

# 去噪算法。两级串联那一项是给老片用的：atadenoise 先按时序抹掉大面积噪点，
# hqdn3d 再收拾残余，单开任何一个都不够。
DENOISE_ALGOS = [
    ("hqdn3d", "hqdn3d"),
    ("atadenoise", "atadenoise"),
    ("vaguedenoiser", "vaguedenoiser"),
    ("nlmeans", "nlmeans"),
    ("bm3d", "bm3d"),
    ("atadenoise+hqdn3d", "atadenoise + hqdn3d"),
]

# 锐化算法。unsharp+cas 是「强锐化」预设用的组合：USM 提边缘，cas 再补对比度自适应
# 那一层。cas 的 strength 上限是 1，想要更锐只能靠 USM 那一层加量。
SHARPEN_ALGOS = [
    ("cas", "cas"),
    ("unsharp", "unsharp"),
    ("unsharp+cas", "unsharp + cas"),
]

SCALE_ALGOS = [
    ("lanczos", "lanczos"),
    ("spline", "spline"),
    ("bicubic", "bicubic"),
    ("area", "area"),
]

# x264/x265 的速度档；每上一档约慢一倍、同 CRF 体积小 5~10%
X264_PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast",
                "medium", "slow", "slower", "veryslow"]
# nvenc 用的是另一套档位名，混在同一个下拉里会直接报无效参数
NVENC_PRESETS = ["p1", "p2", "p3", "p4", "p5", "p6", "p7"]
SVT_PRESETS = [str(i) for i in range(14)]

# quality_flag: CRF 模式下传质量的参数名——x264/x265/svtav1 认 -crf，nvenc 认 -cq，
#               传错会被当成未知选项直接失败。
# two_pass:     目标体积模式能否走两遍。只有 x264/x265 有 -pass，nvenc 没有，
#               对这些编码器退化成单遍 -b:v（体积不如两遍准，但能跑）。
# hw:           需要额外做一次真实编码测试，编译进来 ≠ 机器上有对应驱动。
ENCODERS = {
    "libx264": {
        "label": "libx264", "quality_flag": "-crf",
        "presets": X264_PRESETS, "two_pass": True, "hw": False,
        "crf_hint": "视觉无损 16~18 · 推荐 18~20 · 网络分发 23",
    },
    "libx265": {
        "label": "libx265", "quality_flag": "-crf",
        "presets": X264_PRESETS, "two_pass": True, "hw": False,
        "crf_hint": "比 x264 大 2~4 才是同等观感",
    },
    "h264_nvenc": {
        "label": "h264_nvenc", "quality_flag": "-cq",
        "presets": NVENC_PRESETS, "two_pass": False, "hw": True,
        "crf_hint": "CQ 18~22 常用",
    },
    "hevc_nvenc": {
        "label": "hevc_nvenc", "quality_flag": "-cq",
        "presets": NVENC_PRESETS, "two_pass": False, "hw": True,
        "crf_hint": "CQ 20~26 常用",
    },
    "av1_nvenc": {
        "label": "av1_nvenc", "quality_flag": "-cq",
        "presets": NVENC_PRESETS, "two_pass": False, "hw": True,
        "crf_hint": "CQ 26~32 常用",
    },
    "libsvtav1": {
        "label": "libsvtav1", "quality_flag": "-crf",
        "presets": SVT_PRESETS, "two_pass": False, "hw": False,
        "crf_hint": "CRF 26~34 常用，preset 0~13 越小越慢",
    },
}

CONTAINERS = [("mp4", "mp4"),
              ("mkv", "mkv")]

AUDIO_MODES = [("copy", "直接复制"),
               ("aac", "AAC 重编码"),
               ("none", "去除音轨")]

# 画质预设：每一项都是对 default_params() 的覆盖，选预设 = 批量写参数。
# 引擎本身只认参数不认预设名，所以 UI 手动改任何一个滑块都能自然落到「自定义」。
PRESETS = {
    "off": {
        "label": "关闭",
        "desc": "只换编码器或压体积，画质不动，最快",
        "params": {"denoise_on": False, "sharpen_on": False, "color_on": False},
    },
    "light": {
        "label": "轻度修复",
        "desc": "源本身干净，只想去掉压缩块效应",
        "params": {"denoise_on": True, "denoise_algo": "hqdn3d",
                   "denoise_luma": 1.0, "denoise_chroma": 1.0,
                   "denoise_temporal": 4.0,
                   "sharpen_on": True, "sharpen_algo": "cas",
                   "sharpen_amount": 0.4,
                   "color_on": True, "contrast": 1.02, "saturation": 1.03,
                   "brightness": 0.0, "gamma": 1.0},
    },
    "standard": {
        "label": "标准增强",
        "desc": "默认值，台球录像通用",
        "params": {"denoise_on": True, "denoise_algo": "hqdn3d",
                   "denoise_luma": 1.5, "denoise_chroma": 1.5,
                   "denoise_temporal": 6.0,
                   "sharpen_on": True, "sharpen_algo": "cas",
                   "sharpen_amount": 0.8,
                   "color_on": True, "contrast": 1.04, "saturation": 1.06,
                   "brightness": 0.0, "gamma": 1.0},
    },
    "sharp": {
        "label": "强锐化",
        "desc": "糊片、失焦；已清晰的源用了会出白边",
        "params": {"denoise_on": True, "denoise_algo": "hqdn3d",
                   "denoise_luma": 1.0, "denoise_chroma": 1.0,
                   "denoise_temporal": 4.0,
                   "sharpen_on": True, "sharpen_algo": "unsharp+cas",
                   "sharpen_amount": 1.0, "usm_amount": 1.0,
                   "usm_lx": 5, "usm_ly": 5,
                   "color_on": True, "contrast": 1.05, "saturation": 1.0,
                   "brightness": 0.0, "gamma": 1.0},
    },
    "restore": {
        "label": "老片修复",
        "desc": "强噪点、偏灰的老录像；两级去噪串联，慢",
        "params": {"denoise_on": True, "denoise_algo": "atadenoise+hqdn3d",
                   "denoise_luma": 2.0, "denoise_chroma": 2.0,
                   "denoise_temporal": 8.0,
                   "sharpen_on": True, "sharpen_algo": "cas",
                   "sharpen_amount": 0.6,
                   "color_on": True, "contrast": 1.08, "saturation": 1.08,
                   "brightness": 0.02, "gamma": 1.0},
    },
    "anime": {
        "label": "动漫 / 线条",
        "desc": "色块分明、线条锐利的内容",
        "params": {"denoise_on": True, "denoise_algo": "hqdn3d",
                   "denoise_luma": 2.0, "denoise_chroma": 2.0,
                   "denoise_temporal": 6.0,
                   "sharpen_on": True, "sharpen_algo": "cas",
                   "sharpen_amount": 1.0,
                   "color_on": True, "contrast": 1.06, "saturation": 1.15,
                   "brightness": 0.0, "gamma": 1.0},
    },
}

# 命名模板可用占位符，UI 的提示文案与此保持同源
NAME_PLACEHOLDERS = ("{name}", "{ext}", "{wxh}", "{preset}", "{date}", "{suffix}")


def default_params():
    """一份完整的增强参数（= 「标准增强」预设 + CRF18 + libx264）

    UI 与单测都从这里取初值，避免两处各写一份默认值慢慢漂移。
    """
    p = {
        # ---- 滤镜 ----
        "preset": "standard",
        "denoise_on": True,
        "denoise_algo": "hqdn3d",
        "denoise_luma": 1.5,
        "denoise_chroma": 1.5,
        "denoise_temporal": 6.0,
        "sharpen_on": True,
        "sharpen_algo": "cas",
        "sharpen_amount": 0.8,
        "usm_amount": 1.0,
        "usm_lx": 5,
        "usm_ly": 5,
        "scale_on": False,
        "scale_mode": "height",       # ratio / height
        "scale_ratio": 1.5,
        "scale_height": 1080,
        "scale_algo": "lanczos",
        "color_on": True,
        "contrast": 1.04,
        "saturation": 1.06,
        "brightness": 0.0,
        "gamma": 1.0,
        "interp_on": False,
        "interp_fps": 60,
        "interp_mode": "mci",         # mci 运动补偿极慢 / blend 快但有重影 / fruc GPU 光流
        "interp_perf": "",            # GPU 档位，空走滤镜默认；slow / medium / fast
        "interp_gpu_index": 0,        # Vulkan 设备号，多显卡时选到 NVIDIA 那张
        # ---- 编码 ----
        "encoder": "libx264",
        "codec_preset": "slow",
        "rate_mode": "crf",           # crf / size
        "crf": 18,
        "target_mb": 100,
        "audio": "copy",
        "audio_bitrate": 192,
        "container": "mp4",
        "faststart": True,
        "pix_fmt": "yuv420p",
        # ---- 输出 ----
        "out_mode": "source",         # source / videos / custom
        "out_dir": "",
        "name_tpl": "{name}_enhanced{suffix}",
        "on_exist": "skip",           # skip / overwrite / rename
    }
    p.update(PRESETS["standard"]["params"])
    return p


def apply_preset(params, name):
    """把预设覆盖到参数副本上；未知预设名原样返回（不当错误，UI 里就是「自定义」）"""
    out = dict(params or {})
    spec = PRESETS.get(name)
    if spec is None:
        out["preset"] = "custom"
        return out
    out.update(spec["params"])
    out["preset"] = name
    return out


# ==================== 环境定位 ====================

def locate_ffmpeg(settings=None):
    """返回 (ffmpeg, ffprobe) 路径，找不到时为 None

    设置值优先于 PATH：打包分发后目标机器不一定配了 PATH，但也不该强迫用户改
    系统环境变量——所以在设置页留一行手填路径，**留空**才走 shutil.which。

    「填了但文件不存在」和「没填」是两种情况：前者一律返回 None 而不是偷偷用
    PATH 里的那个，否则用户改了设置以为生效了，实际跑的是另一个 ffmpeg，出问
    题时根本查不出来。UI 的「环境检测」页负责把这句话讲给用户听。
    """
    s = settings or {}
    found = []
    for key, exe in (("ffmpeg_path", "ffmpeg"), ("ffprobe_path", "ffprobe")):
        v = (s.get(key) or "").strip()
        if v:
            found.append(v if os.path.isfile(v) else None)
        else:
            found.append(shutil.which(exe))
    return tuple(found)


def _no_window_flags():
    """打包版（无控制台）起子进程不能弹出黑框"""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run_capture(cmd, timeout=120):
    """跑完再取全部输出（ffprobe / -version / -encoders 这类秒级命令用）"""
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=_no_window_flags())
    except (OSError, subprocess.SubprocessError) as e:
        return -1, str(e)
    return p.returncode, (p.stdout or "")


def detect_capabilities(ffmpeg):
    """探测 ffmpeg 能力，返回 dict

    编码器「编译进来」和「这台机器上能跑」是两件事：*_nvenc 还要 NVIDIA 驱动。
    所以这里只报编译结果，真实可用性由 test_encoder() 单帧试编一次才算数。
    """
    cap = {"ffmpeg": ffmpeg, "version": "", "build": "", "encoders": set(),
           "filters": set(), "has_vmaf": False, "has_fruc": False,
           "ok": False, "reason": ""}
    if not ffmpeg or not os.path.isfile(ffmpeg):
        cap["reason"] = "ffmpeg 未定位到，请在「环境检测」填路径或把它加进 PATH"
        return cap
    rc, out = _run_capture([ffmpeg, "-hide_banner", "-version"])
    if rc != 0:
        cap["reason"] = f"ffmpeg -version 失败(exit={rc}): {out.strip()[:200]}"
        return cap
    lines = [ln for ln in out.splitlines() if ln.strip()]
    # 首行是 "ffmpeg version N-xxx Copyright (c) 2000-2026 the FFmpeg developers"，
    # 只截版本号，否则「环境检测」页会把整句版权声明当成版本显示出来
    cap["version"] = lines[0].replace("ffmpeg version ", "").split(" Copyright")[0].strip() \
        if lines else ""
    for ln in lines:
        if ln.startswith("configuration:"):
            cap["build"] = ln[len("configuration:"):].strip()
            break
    # -encoders / -filters 的行格式都是「标志 名字 描述」，split 后取第二段即可，
    # 比写死列宽的正则稳（不同 build 的描述列对齐宽度不一样）。但表头图例行
    # 长得跟数据行一模一样（"V..... = Video"），只按首列过滤会把 '=' 当成编码器
    # 名收进来，所以名字必须再过一遍合法标识符的正则。
    name_ok = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
    rc, out = _run_capture([ffmpeg, "-hide_banner", "-encoders"])
    if rc == 0:
        for ln in out.splitlines():
            t = ln.split()
            if len(t) >= 2 and t[0][:1] in ("V", "A", "S") and name_ok.match(t[1]):
                cap["encoders"].add(t[1])
    rc, out = _run_capture([ffmpeg, "-hide_banner", "-filters"])
    if rc == 0:
        for ln in out.splitlines():
            t = ln.split()
            if (len(t) >= 2 and re.fullmatch(r"[.TSCN]{2,4}", t[0])
                    and name_ok.match(t[1])):
                cap["filters"].add(t[1])
    cap["has_vmaf"] = ("libvmaf" in cap["filters"]
                       and "--enable-libvmaf" in cap["build"])
    # 编译进来 ≠ 能跑：fruc_vulkan 还要 NVIDIA 光流硬件，真实可用性由
    # test_fruc_vulkan() 真跑一段才算数，这里只报构建里有没有这个滤镜
    cap["has_fruc"] = "fruc_vulkan" in cap["filters"]
    cap["ok"] = True
    return cap


def test_encoder(ffmpeg, encoder):
    """单帧真实编码测试，返回 (ok, reason)

    编一帧开销 <200ms，但能把「驱动缺失 / 显卡不支持该 codec」这类只有真跑才
    暴露的问题挡在开始按钮之前。

    测试分辨率必须是 256x256 而不是更小：NVENC 有最小尺寸限制，64x64 会让它报
    "Frame Dimension less than the minimum supported value"，在明明有独显的机器
    上被误判成「硬件编码器不可用」。
    """
    if not ffmpeg:
        return False, "ffmpeg 未定位"
    cmd = [ffmpeg, "-hide_banner", "-v", "error", "-f", "lavfi",
           "-i", "color=black:s=256x256:d=0.04", "-frames:v", "1",
           "-c:v", encoder, "-f", "null", "-"]
    rc, out = _run_capture(cmd, timeout=60)
    if rc == 0:
        return True, ""
    return False, (out.strip().splitlines() or [f"exit={rc}"])[0][:200]


def test_fruc_vulkan(ffmpeg, gpu_index=0):
    """真跑一段极小片段验证 GPU 光流插帧可用，返回 (ok, reason)

    滤镜编译进来 ≠ 这台机器能跑：fruc_vulkan 还要 NVIDIA RTX 30+ 的光流硬件与
    VK_NV_optical_flow 驱动扩展，且 Vulkan 设备号要选到 N 卡。用 0.2s 的 lavfi
    测试源走一遍完整 hwupload→fruc→hwdownload 链，把「构建太旧没这个滤镜 /
    驱动缺失 / 选错显卡」挡在开始按钮之前，而不是让整批录像逐条失败。
    """
    if not ffmpeg:
        return False, "ffmpeg 未定位"
    idx = int(gpu_index or 0)
    cmd = [ffmpeg, "-hide_banner", "-v", "error",
           "-init_hw_device", f"vulkan=vk:{idx}", "-filter_hw_device", "vk",
           "-f", "lavfi", "-i", "testsrc2=size=256x256:rate=30:duration=0.2",
           "-vf", "format=yuv420p,hwupload,fruc_vulkan=fps=60,hwdownload,"
                  "format=yuv420p",
           "-an", "-f", "null", "-"]
    rc, out = _run_capture(cmd, timeout=60)
    if rc == 0:
        return True, ""
    return False, (out.strip().splitlines() or [f"exit={rc}"])[-1][:200]


# ==================== 源文件探测 ====================

def _parse_rate(raw):
    """'30000/1001' → 29.97；分母为 0 或缺失时按 1 处理，不让零除打断探测"""
    if not raw:
        return 0.0
    num, _, den = str(raw).partition("/")
    try:
        n = float(num)
        d = float(den) if den else 1.0
    except ValueError:
        return 0.0
    return n / d if d else 0.0


def probe(path, ffprobe):
    """探测视频信息，返回 dict（ok=False 时 reason 可直接展示）

    一次 JSON 调用拿全 format + streams，不像根目录 video_ai_enhance.py 那样
    分五次 ffprobe——那个写法在大文件上要重复打开五遍容器。
    """
    info = {"ok": False, "path": path, "reason": "", "width": 0, "height": 0,
            "fps": 0.0, "duration": 0.0, "n_frames": 0, "size": 0,
            "codec_name": "", "pix_fmt": "", "audio_codec": "", "bit_rate": 0}
    if not path or not os.path.isfile(path):
        info["reason"] = f"文件不存在: {path}"
        return info
    if not ffprobe:
        info["reason"] = "ffprobe 未定位到，请在「环境检测」填路径"
        return info
    info["size"] = os.path.getsize(path)
    rc, out = _run_capture([ffprobe, "-v", "error", "-print_format", "json",
                            "-show_format", "-show_streams", path], timeout=180)
    if rc != 0:
        info["reason"] = f"ffprobe 失败(exit={rc}): {out.strip()[:200]}"
        return info
    try:
        data = json.loads(out or "{}")
    except ValueError:
        info["reason"] = "ffprobe 输出不是合法 JSON，文件可能已损坏"
        return info
    vstream = None
    for st in data.get("streams") or []:
        if st.get("codec_type") == "video" and vstream is None:
            # 跳过 attached_pic：有些 mkv 把封面图当视频流塞在第一条
            if st.get("disposition", {}).get("attached_pic"):
                continue
            vstream = st
        elif st.get("codec_type") == "audio" and not info["audio_codec"]:
            info["audio_codec"] = st.get("codec_name") or ""
    if vstream is None:
        info["reason"] = "没有可用的视频流，可能是纯音频或封面图流"
        return info
    fmt = data.get("format") or {}
    info["width"] = int(vstream.get("width") or 0)
    info["height"] = int(vstream.get("height") or 0)
    info["codec_name"] = vstream.get("codec_name") or ""
    info["pix_fmt"] = vstream.get("pix_fmt") or ""
    # r_frame_rate 是容器声明的基准帧率，avg_frame_rate 是实际平均值；
    # 变帧率录像两者会不一致，取 avg 更接近真实播放时长
    info["fps"] = (_parse_rate(vstream.get("avg_frame_rate"))
                   or _parse_rate(vstream.get("r_frame_rate")))
    try:
        info["duration"] = float(fmt.get("duration") or vstream.get("duration") or 0)
    except (TypeError, ValueError):
        info["duration"] = 0.0
    try:
        info["bit_rate"] = int(fmt.get("bit_rate") or 0)
    except (TypeError, ValueError):
        info["bit_rate"] = 0
    nb = vstream.get("nb_frames")
    if nb and str(nb).isdigit():
        info["n_frames"] = int(nb)
    elif info["duration"] and info["fps"]:
        info["n_frames"] = int(round(info["duration"] * info["fps"]))
    if not info["width"] or not info["height"]:
        info["reason"] = "解析不出分辨率"
        return info
    if info["duration"] <= 0:
        # 时长拿不到就没法算目标体积模式的码率，CRF 模式仍然能跑，所以只警告不失败
        info["reason"] = "无法读取时长，「目标体积」模式不可用，CRF 模式仍可跑"
    info["ok"] = True
    return info


# ==================== 滤镜链 ====================

def _fnum(v, digits=4):
    """浮点转滤镜参数字符串：去掉无意义的尾零，1.0 而不是 1.0000"""
    return f"{float(v):.{digits}f}".rstrip("0").rstrip(".") or "0"


def _denoise_filter(p):
    algo = p.get("denoise_algo") or "hqdn3d"
    luma = _fnum(p.get("denoise_luma", 1.5))
    chroma = _fnum(p.get("denoise_chroma", 1.5))
    temporal = _fnum(p.get("denoise_temporal", 6.0))
    if algo == "hqdn3d":
        # 四参格式 luma_spatial:chroma_spatial:luma_tmp:chroma_tmp，后两个共用时序强度
        return f"hqdn3d={luma}:{chroma}:{temporal}:{temporal}"
    if algo == "nlmeans":
        # nlmeans 的 s 是空域强度、p 是时序强度，量纲与 hqdn3d 完全不同：
        # hqdn3d 的 1.5 对应 nlmeans 大约 0.00002 量级，直接搬数值等于没去噪
        return f"nlmeans=s={_fnum(float(luma) * 0.000012, 8)}:p={_fnum(float(temporal) * 0.000012, 8)}"
    if algo == "atadenoise":
        return (f"atadenoise=0a={_fnum(float(luma) * 0.013, 4)}:"
                f"0b={_fnum(float(luma) * 0.027, 4)}:s=80")
    if algo == "vaguedenoiser":
        return f"vaguedenoiser=threshold={_fnum(float(luma) * 0.02, 4)}:method=soft"
    if algo == "bm3d":
        # bm3d 需要两步（estimate + filtering）串联才有意义，单步只是基础去噪
        sigma = _fnum(float(luma) * 3.0, 3)
        return (f"split[a][b];[a]bm3d=sigma={sigma}:block_size=4:ref=None[c];"
                f"[b][c]bm3d=sigma={sigma}:block_size=4:ref_mode=2")
    if algo == "atadenoise+hqdn3d":
        return (f"atadenoise=0a={_fnum(float(luma) * 0.01, 4)}:"
                f"0b={_fnum(float(luma) * 0.02, 4)}:s=80,"
                f"hqdn3d={luma}:{chroma}:{temporal}:{temporal}")
    raise EnhanceError(f"未知去噪算法: {algo}")


def _sharpen_filter(p):
    algo = p.get("sharpen_algo") or "cas"
    # cas 的 strength 只收 0..1，越界时 ffmpeg 拒绝整条链、整批失败。旧版本滑块
    # 量程到 2，用户配置里可能还存着 1.5 这样的值，这里 clamp 兜住 persisted 输入
    amount = _fnum(min(1.0, max(0.0, float(p.get("sharpen_amount", 0.8)))), 3)
    if algo == "cas":
        return f"cas={amount}"
    if algo == "unsharp":
        lx = int(p.get("usm_lx", 5))
        ly = int(p.get("usm_ly", 5))
        return (f"unsharp={lx}:{ly}:{_fnum(p.get('usm_amount', 1.0), 3)}:"
                f"{lx}:{ly}:0.0")
    if algo == "unsharp+cas":
        lx = int(p.get("usm_lx", 5))
        ly = int(p.get("usm_ly", 5))
        return (f"unsharp={lx}:{ly}:{_fnum(p.get('usm_amount', 1.0), 3)}:"
                f"{lx}:{ly}:0.0,cas={amount}")
    raise EnhanceError(f"未知锐化算法: {algo}")


def target_height(p, info):
    """算出放大后的目标高度（偶数），未开启放大时返回源高度

    返回 0 表示源高度未知、无法计算（此时不插 scale，交给 ffmpeg 原样输出）。
    """
    src_h = int((info or {}).get("height") or 0)
    if not p.get("scale_on"):
        return src_h
    if p.get("scale_mode") == "ratio":
        ratio = float(p.get("scale_ratio") or 1.0)
        if src_h <= 0 or ratio <= 0:
            return src_h
        h = int(round(src_h * ratio))
    else:
        h = int(p.get("scale_height") or 0)
        if h <= 0:
            return src_h
    # 编码器要求高度为偶数，奇数会被 libx264 直接拒绝
    return h + (h % 2)


def _scale_filter(p, info):
    h = target_height(p, info)
    if h <= 0:
        return ""
    src_h = int((info or {}).get("height") or 0)
    if src_h and h == src_h:
        # 目标高度就等于源高度时不插 scale：lanczos 原尺寸重采样不是无损的，
        # 白白多花一倍时间还会让画面变软。命名模板的 {suffix} 同样按这个口径
        # 判断（高度没变就不加 _720p），两边必须一致。
        return ""
    algo = p.get("scale_algo") or "lanczos"
    if algo not in dict(SCALE_ALGOS):
        raise EnhanceError(f"未知插值算法: {algo}")
    # -2 = 宽度按高度等比推算并强制取偶，写死宽度会在奇数源上变形
    return f"scale=-2:{h}:flags={algo}"


def _color_filter(p):
    parts = []
    for key, name in (("contrast", "contrast"), ("brightness", "brightness"),
                      ("saturation", "saturation"), ("gamma", "gamma")):
        v = float(p.get(key, 1.0) if key != "brightness" else p.get(key, 0.0))
        default = 0.0 if key == "brightness" else 1.0
        if abs(v - default) > 1e-6:
            parts.append(f"{name}={_fnum(v, 4)}")
    return f"eq={':'.join(parts)}" if parts else ""


def _uses_fruc(p):
    """GPU 光流插帧是否生效：补帧开关打开且模式选了 fruc 才算

    只看 interp_mode 会在补帧关闭时也注入 -init_hw_device，平白初始化一次显卡。
    """
    return bool(p.get("interp_on")) and p.get("interp_mode") == "fruc"


def _interp_filter(p):
    fps = int(p.get("interp_fps") or 60)
    if fps <= 0:
        return ""
    mode = p.get("interp_mode")
    if mode == "blend":
        # blend 只是把相邻帧混一下，快十倍但快速移动的球会拖成两道影
        return f"minterpolate=fps={fps}:mi_mode=blend"
    if mode == "fruc":
        # GPU 光流插帧：hwupload 进 Vulkan → fruc_vulkan 调 NVIDIA 光流硬件合成
        # 中间帧 → hwdownload 回内存；设备旗标由 build_command 注入，perf 空走默认
        opt = f"fps={fps}"
        perf = str(p.get("interp_perf") or "").strip()
        if perf in ("slow", "medium", "fast"):
            opt += f":perf={perf}"
        return f"format=yuv420p,hwupload,fruc_vulkan={opt},hwdownload"
    return (f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:"
            f"me_mode=bidir:vsbmc=1")


def build_filter_chain(p, info=None):
    """拼出完整的 -vf 值

    顺序固定为 去噪 → 放大 → 锐化 → 色彩 → 补帧 → format，理由见模块 docstring。
    bm3d 那一支带 split/多输入，不是纯线性链，所以它一旦选中整条链就不能再简单
    用逗号拼接——这里把它放在链首并由调用方保证后面不再有需要独立输入的滤镜。
    """
    info = info or {}
    parts = []
    if p.get("denoise_on"):
        parts.append(_denoise_filter(p))
    if p.get("scale_on"):
        f = _scale_filter(p, info)
        if f:
            parts.append(f)
    if p.get("sharpen_on"):
        parts.append(_sharpen_filter(p))
    if p.get("color_on"):
        f = _color_filter(p)
        if f:
            parts.append(f)
    if p.get("interp_on"):
        f = _interp_filter(p)
        if f:
            parts.append(f)
    pix = p.get("pix_fmt") or "yuv420p"
    parts.append(f"format={pix}")
    chain = ",".join(x for x in parts if x)
    # bm3d 的 split[a][b] 写法必须走 -filter_complex 而不是 -vf，否则 ffmpeg 报
    # 「Simple filtergraph cannot have split」。这里显式拦下来，让调用方改道。
    if "split[" in chain:
        raise EnhanceError(
            "bm3d 去噪需要 -filter_complex，当前批处理链路不支持；请改用 hqdn3d 或 nlmeans")
    return chain


# ==================== 输出路径 ====================

def _fill_template(tpl, src, info, p):
    base = os.path.splitext(os.path.basename(src))[0]
    ext = os.path.splitext(src)[1].lstrip(".")
    h = target_height(p, info)
    suffix = f"_{h}p" if (p.get("scale_on") and h and h != info.get("height")) else ""
    values = {
        "{name}": base,
        "{ext}": ext or "mp4",
        "{wxh}": f"{info.get('width', 0)}x{h or info.get('height', 0)}",
        "{preset}": str(p.get("preset") or "custom"),
        "{date}": time.strftime("%Y%m%d"),
        "{suffix}": suffix,
    }
    out = tpl or "{name}_enhanced{suffix}"
    for k, v in values.items():
        out = out.replace(k, v)
    return out, suffix


def output_dir(p, src, videos_dir=""):
    """按 out_mode 决定输出目录；自定义目录没填时回落到源目录，不抛错"""
    mode = p.get("out_mode") or "source"
    if mode == "custom":
        d = (p.get("out_dir") or "").strip()
        if d:
            return d
    elif mode == "videos":
        d = (videos_dir or "").strip()
        if d:
            return d
    return os.path.dirname(os.path.abspath(src))


def _first_free(d, stem, container):
    """从 {stem}_1 起找第一个不存在的文件名"""
    i = 1
    while True:
        cand = os.path.join(d, f"{stem}_{i}.{container}")
        if not os.path.exists(cand):
            return cand
        i += 1


def resolve_output_path(src, p, info=None, videos_dir=""):
    """算出输出文件全路径；on_exist="skip" 且目标已存在时返回 None 表示跳过

    返回 None 是有意为之的第三态：调用方要能区分「跳过」和「失败」，前者在任务
    列表里显示灰色已完成，后者显示红色。
    """
    info = info or {}
    container = p.get("container") or "mp4"
    d = output_dir(p, src, videos_dir)
    stem, _suffix = _fill_template(p.get("name_tpl"), src, info, p)
    path = os.path.join(d, f"{stem}.{container}")
    # 模板只写 {name} 且没放大时，算出来的输出就是源文件本身。这时候哪怕策略是
    # 「覆盖」也必须让开：ffmpeg 会一边读一边截断自己的输入，源片直接毁掉。
    if os.path.abspath(path) == os.path.abspath(src):
        return _first_free(d, stem, container)
    policy = p.get("on_exist") or "skip"
    if not os.path.exists(path):
        return path
    if policy == "overwrite":
        return path
    if policy == "rename":
        return _first_free(d, stem, container)
    return None


# ==================== 命令构建 ====================

def target_bitrate_kbps(target_mb, duration):
    """目标体积 → 码率（kbps），沿用仓库根 video_ai_enhance.py:246 的算式

    1 MB = 8192 kbit；乘 0.98 是给容器头与音轨留余量，否则成品会稳定超出目标
    几个百分点。
    """
    if not target_mb or target_mb <= 0 or not duration or duration <= 0:
        return 0
    return int(float(target_mb) * 8192 * 0.98 / float(duration))


def _encoder_spec(encoder):
    spec = ENCODERS.get(encoder)
    if spec is None:
        raise EnhanceError(f"未知编码器: {encoder}")
    return spec


def _video_encode_args(p, info):
    """编码器 + 质量/码率 + 速度档，两遍模式只出「单遍用」的那部分"""
    encoder = p.get("encoder") or "libx264"
    spec = _encoder_spec(encoder)
    args = ["-c:v", encoder]
    preset = p.get("codec_preset")
    if preset and preset in spec["presets"]:
        # 档位名不在该编码器的合法集合里就不传，传了会被 ffmpeg 当未知参数拒绝
        args += ["-preset", str(preset)]
    if p.get("rate_mode") == "size":
        kbps = target_bitrate_kbps(p.get("target_mb"), (info or {}).get("duration"))
        if kbps <= 0:
            raise EnhanceError(
                "无法算出目标码率：目标体积或视频时长无效，请改用 CRF 模式")
        args += ["-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.45)}k",
                 "-bufsize", f"{int(kbps * 2)}k"]
    else:
        crf = int(p.get("crf", 18))
        args += [spec["quality_flag"], str(crf)]
    return args


def _audio_args(p, info):
    """音轨处理；源本来就没音轨时一律 -an，否则 -c:a copy 会报流不存在"""
    if not (info or {}).get("audio_codec"):
        return ["-an"]
    mode = p.get("audio") or "copy"
    if mode == "none":
        return ["-an"]
    if mode == "aac":
        return ["-c:a", "aac", "-b:a", f"{int(p.get('audio_bitrate') or 192)}k"]
    return ["-c:a", "copy"]


def _mux_args(p):
    container = p.get("container") or "mp4"
    args = []
    # faststart 只对 mp4/mov 有意义：它把 moov 挪到文件头，mkv 没有这个概念
    if p.get("faststart") and container in ("mp4", "mov"):
        args += ["-movflags", "+faststart"]
    return args


def build_command(src, dst, p, info=None, ffmpeg="ffmpeg", passlogfile=None,
                  pass_no=0):
    """构建单遍（或两遍中的某一遍）的 ffmpeg argv

    -progress pipe:1 -nostats 是关键：ffmpeg 会输出机器可读的键值行，比用正则刮
    stderr 里的 time= 稳得多（后者会随终端宽度换行）。
    """
    info = info or {}
    chain = build_filter_chain(p, info)
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-progress", "pipe:1", "-y"]
    if _uses_fruc(p):
        # Vulkan 设备要在 -i 前初始化，filter_hw_device 把它绑给 hwupload/hwdownload。
        # 设备号默认 0，但多显卡机器上 0 号不一定是 N 卡，故做成可调参数。
        idx = int(p.get("interp_gpu_index") or 0)
        cmd += ["-init_hw_device", f"vulkan=vk:{idx}", "-filter_hw_device", "vk"]
    cmd += ["-i", src, "-vf", chain]
    cmd += _video_encode_args(p, info)

    if pass_no:
        # 两遍编码：并发跑多个任务时 passlog 会互相覆盖，必须给唯一前缀
        if passlogfile:
            cmd += ["-passlogfile", passlogfile]
        cmd += ["-pass", str(pass_no)]
        if pass_no == 1:
            # 第一遍只统计不产出，音频可以省掉，但 -vf 必须保留：分辨率变了
            # 第二遍的码率分配才对得上。格式必须是 null 而不是 mp4——mp4 的
            # moov 要回写文件头，而 os.devnull（Windows 上是 NUL）不可 seek，
            # 用 mp4 会直接报 muxer does not support non seekable output。
            cmd += ["-an", "-f", "null", os.devnull]
            return cmd
    cmd += _audio_args(p, info)
    cmd += _mux_args(p)
    cmd += [dst]
    return cmd


def build_two_pass_commands(src, dst, p, info=None, ffmpeg="ffmpeg",
                            passlogfile=None):
    """目标体积模式的两遍命令；编码器不支持两遍时退化成单遍"""
    spec = _encoder_spec(p.get("encoder") or "libx264")
    if p.get("rate_mode") != "size" or not spec["two_pass"]:
        return [build_command(src, dst, p, info, ffmpeg)]
    return [
        build_command(src, dst, p, info, ffmpeg, passlogfile, pass_no=1),
        build_command(src, dst, p, info, ffmpeg, passlogfile, pass_no=2),
    ]


# ==================== 进度解析 ====================

_TIME_US_RE = re.compile(r"^out_time_(?:us|ms)=(\d+)")
_TIME_HMS_RE = re.compile(r"^out_time=(\d+):(\d+):(\d+(?:\.\d+)?)")
_SPEED_RE = re.compile(r"^speed=\s*([\d.]+)x")


def parse_progress(line, duration):
    """从 -progress 输出行解析百分比（0~100），非进度行返回 None

    out_time_ms 这个名字是历史遗留——它和 out_time_us 一样是**微秒**，不是毫秒。
    按毫秒算会让进度条快 1000 倍直接冲到满格，这里统一按微秒处理。
    """
    if not line or not duration or duration <= 0:
        return None
    s = line.strip()
    m = _TIME_US_RE.match(s)
    if m:
        secs = int(m.group(1)) / 1e6
    else:
        m = _TIME_HMS_RE.match(s)
        if not m:
            return None
        secs = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    pct = secs / float(duration) * 100.0
    # 补帧会让输出时长超过源时长（30fps→60fps 时 out_time 仍按输出算），
    # 不夹住就会显示 150% 这种数字
    return max(0.0, min(100.0, pct))


def parse_speed(line):
    """解析 speed=1.36x，用于估算剩余时间；非速度行返回 None"""
    m = _SPEED_RE.match((line or "").strip())
    return float(m.group(1)) if m else None


# -progress 输出的全部键。值为 N/A 时 parse_progress 认不出，靠这张表兜住，
# 否则补帧缓冲期的 out_time=N/A 会当成可读输出刷满终端
_PROGRESS_KEYS = frozenset((
    "frame", "fps", "bitrate", "total_size", "dup_frames", "drop_frames",
    "stream_0_0_q", "speed", "out_time", "out_time_us", "out_time_ms"))


def is_progress_line(line):
    """判断一行是不是 -progress 的键值噪声，含值为 N/A 的行"""
    s = (line or "").strip()
    if s.startswith("progress="):
        return True
    key, sep, _val = s.partition("=")
    return bool(sep) and key in _PROGRESS_KEYS


# ==================== 执行 ====================

def _kill(p):
    """终止子进程；terminate 无效时 kill 兜底

    Windows 上 terminate 对某些编码器不生效，不补 kill 的话用户关掉窗口后
    ffmpeg 还在后台跑满 CPU，风扇响半天也不知道是谁在转。
    """
    if p.poll() is not None:
        return
    p.terminate()
    try:
        p.wait(5)
    except subprocess.TimeoutExpired:
        p.kill()


def _pump_lines(stream, q):
    """把子进程输出逐行塞进队列，EOF 时放一个 None 哨兵"""
    try:
        for raw in stream:
            q.put(raw)
    except Exception:
        pass
    finally:
        q.put(None)


def _stream_ffmpeg(cmd, on_line=None, should_stop=None, on_progress=None,
                   duration=0.0):
    """跑 ffmpeg 并逐行转发，支持中途取消

    stderr 必须并进 stdout 用单管道读：两个管道分别读需要额外线程，而 stderr
    写满 64KB 缓冲区时 ffmpeg 会阻塞在那里，表现为「进度条卡住但进程还活着」。

    读取放到后台线程 + 队列，主循环带超时轮询而不是直接 for 迭代管道：
    minterpolate / atadenoise 这类滤镜要先缓冲好几秒才吐出第一行输出，直接
    阻塞在管道上会让这段时间里的「取消」点了没反应，用户只能去杀进程。

    on_progress 的契约是 (百分比 float, 速度 float)——速度沿用最近一次 speed= 行
    的值而不是传 None，因为调用方是 Qt 信号，Signal(float) 收到 None 会直接抛
    TypeError 把 worker 打死。
    """
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         encoding="utf-8", errors="replace", bufsize=1,
                         creationflags=_no_window_flags())
    q = queue.Queue()
    threading.Thread(target=_pump_lines, args=(p.stdout, q), daemon=True).start()
    tail = []
    last_speed = 0.0

    def _stop_check():
        if should_stop and should_stop():
            _kill(p)
            raise EnhanceCancelled()

    try:
        while True:
            try:
                raw = q.get(timeout=0.25)
            except queue.Empty:
                _stop_check()
                continue
            if raw is None:
                break
            _stop_check()
            line = raw.rstrip("\r\n")
            if not line:
                continue
            tail.append(line)
            if len(tail) > 12:
                tail.pop(0)
            sp = parse_speed(line)
            if sp:
                last_speed = sp
            pct = parse_progress(line, duration)
            if pct is not None:
                if on_progress:
                    on_progress(pct, last_speed)
                # 进度键值行每秒好几条，全转发会把终端刷成噪声，只留可读的那部分
                continue
            if is_progress_line(line):
                continue
            if on_line:
                on_line(line)
    finally:
        try:
            p.stdout.close()
        except Exception:
            pass
    rc = p.wait()
    return rc, tail


def run_enhance(src, dst, p, ffmpeg, ffprobe, on_line=None, should_stop=None,
                on_progress=None, info=None, passlogfile=None):
    """增强单个文件，返回报告 dict

    info 可由调用方预先探测好传进来（任务列表加文件时已经探过一遍，不必重复）。
    """
    def _say(msg):
        if on_line:
            on_line(msg)

    if not ffmpeg:
        raise EnhanceError("ffmpeg 未定位到，请到「环境检测」填路径")
    if info is None:
        info = probe(src, ffprobe)
    if not info.get("ok"):
        raise EnhanceError(info.get("reason") or f"探测失败: {src}")
    duration = float(info.get("duration") or 0)

    out_dir = os.path.dirname(os.path.abspath(dst))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    cmds = build_two_pass_commands(src, dst, p, info, ffmpeg, passlogfile)
    _say(f"源: {info['width']}x{info['height']} @ {info['fps']:.3f}fps, "
         f"{duration:.1f}s, {info['n_frames']} 帧, "
         f"音频: {info['audio_codec'] or '无'}")
    _say(f"滤镜链: {build_filter_chain(p, info)}")
    _say(f"输出: {dst}")

    t0 = time.time()
    # 两遍模式下第一遍只是统计，进度算到 50% 为止，第二遍走剩下的 50%，
    # 否则用户会看到进度条冲到 100% 又掉回 0
    total = len(cmds)
    for idx, cmd in enumerate(cmds):
        if should_stop and should_stop():
            raise EnhanceCancelled()
        if total > 1:
            _say(f"[两遍编码 {idx + 1}/{total}]")

        def _prog(pct, speed, base=idx):
            if on_progress:
                on_progress((base + pct / 100.0) / total * 100.0, speed)

        rc, tail = _stream_ffmpeg(cmd, on_line=_say, should_stop=should_stop,
                                  on_progress=_prog, duration=duration)
        if rc != 0:
            detail = "\n".join(tail[-6:])
            # 取消时子进程被 terminate，退出码非 0 属于预期，不当失败报
            if should_stop and should_stop():
                raise EnhanceCancelled()
            raise EnhanceError(f"ffmpeg 失败(exit={rc}):\n{detail}")
    elapsed = time.time() - t0
    if not os.path.isfile(dst) or os.path.getsize(dst) == 0:
        raise EnhanceError(f"输出文件未生成或为空: {dst}")
    report = {
        "src": src, "dst": dst, "elapsed": elapsed,
        "size_before": info.get("size", 0),
        "size_after": os.path.getsize(dst),
        "width": info.get("width", 0), "height": info.get("height", 0),
        "out_height": target_height(p, info),
        "duration": duration, "fps": info.get("fps", 0.0),
        "filter_chain": build_filter_chain(p, info),
        "encoder": p.get("encoder"), "rate_mode": p.get("rate_mode"),
        "crf": p.get("crf"), "target_mb": p.get("target_mb"),
        "preset": p.get("preset"), "passes": total,
    }
    _say(f"完成: {os.path.basename(dst)} "
         f"({report['size_after'] / 1048576.0:.1f} MB) "
         f"用时 {int(elapsed // 60)}m{int(elapsed % 60):02d}s")
    # CRF 模式下锐化会增加高频细节，成品比源大是正常现象，不提示用户会以为出错了
    if (p.get("rate_mode") == "crf" and report["size_before"]
            and report["size_after"] > report["size_before"]):
        _say("提示: 输出比源大 —— CRF 模式下锐化增加了高频细节，"
             "需要控体积请切「目标体积」模式")
    return report


def cleanup_passlog(passlogfile):
    """删掉两遍编码留下的 passlog（.log 与 .log.mbtree 两个文件）"""
    if not passlogfile:
        return
    for suffix in ("-0.log", "-0.log.mbtree", ".log", ".log.mbtree"):
        try:
            f = passlogfile + suffix
            if os.path.isfile(f):
                os.remove(f)
        except OSError:
            pass


# ==================== 质量评测与抽帧 ====================

METRICS = [("vmaf", "VMAF"),
           ("psnr", "PSNR dB"),
           ("ssim", "SSIM")]


def build_metric_command(src, dst, kind, ffmpeg="ffmpeg", log_path=None):
    """构建客观指标评测命令

    libvmaf/psnr/ssim 都要求两侧分辨率一致，所以输出被放大过时必须先把源
    lanczos 放大到同尺寸，否则滤镜直接报 dimension mismatch。
    输入顺序：0 = dst（失真侧），1 = src（参考侧）。
    """
    if kind not in dict(METRICS):
        raise EnhanceError(f"未知指标: {kind}")
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-y", "-i", dst, "-i", src]
    if kind == "vmaf":
        opt = "libvmaf"
        if log_path:
            opt += f"=log_fmt=json:log_path={_quote_filter_path(log_path)}"
    elif kind == "psnr":
        opt = "psnr"
    else:
        opt = "ssim"
    cmd += ["-lavfi", f"[0:v][1:v]{opt}", "-an", "-f", "null", "-"]
    return cmd


def build_scaled_metric_command(src, dst, kind, ffmpeg="ffmpeg", log_path=None,
                                width=0, height=0):
    """输出与源分辨率不同时的评测命令：先把参考源放大到输出尺寸再比

    分数口径要在 UI 上标注清楚——这时衡量的已经不是「同分辨率下的编码损失」，
    而是「放大 + 编码整体相对源的偏离」。
    """
    if kind not in dict(METRICS):
        raise EnhanceError(f"未知指标: {kind}")
    if not width or not height:
        raise EnhanceError("缺少输出分辨率，无法预缩放参考源")
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-y", "-i", dst, "-i", src]
    if kind == "vmaf":
        opt = "libvmaf"
        if log_path:
            opt += f"=log_fmt=json:log_path={_quote_filter_path(log_path)}"
    else:
        opt = kind
    cmd += ["-lavfi",
            f"[1:v]scale={int(width)}:{int(height)}:flags=lanczos[ref];"
            f"[0:v][ref]{opt}",
            "-an", "-f", "null", "-"]
    return cmd


def _quote_filter_path(path):
    """滤镜参数里的 Windows 路径要转义冒号和反斜杠，否则 C: 会被当成选项分隔符"""
    return ("'" + str(path).replace("\\", "/").replace(":", "\\:") + "'")


_VMAF_SCORE_RE = re.compile(r"VMAF score:\s*([\d.]+)")
_PSNR_RE = re.compile(r"PSNR\b.*?average:([\d.]+)")
_SSIM_RE = re.compile(r"SSIM\b.*?All:([\d.]+)")


def parse_metric_output(kind, text, log_path=None):
    """从评测输出里取分数，取不到返回 None

    psnr/ssim 的结果打在 stderr（已并进 stdout）末尾；vmaf 指定了 log_path 时
    优先读 JSON 的 pooled_metrics，比刮文本可靠。
    """
    text = text or ""
    if kind == "vmaf" and log_path and os.path.isfile(log_path):
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            score = (data.get("pooled_metrics") or {}).get("vmaf", {}).get("score")
            if score is not None:
                return float(score)
        except (OSError, ValueError, AttributeError):
            pass
        m = _VMAF_SCORE_RE.search(text)
        return float(m.group(1)) if m else None
    if kind == "psnr":
        m = _PSNR_RE.search(text)
        return float(m.group(1)) if m else None
    if kind == "ssim":
        m = _SSIM_RE.search(text)
        return float(m.group(1)) if m else None
    m = _VMAF_SCORE_RE.search(text)
    return float(m.group(1)) if m else None


def run_metric(src, dst, kind, ffmpeg, ffprobe, on_line=None, should_stop=None,
               log_path=None):
    """跑一次指标评测，返回分数或 None"""
    a = probe(dst, ffprobe)
    b = probe(src, ffprobe)
    same = (a.get("width") == b.get("width") and a.get("height") == b.get("height")
            and a.get("width"))
    if same:
        cmd = build_metric_command(src, dst, kind, ffmpeg, log_path)
    else:
        cmd = build_scaled_metric_command(src, dst, kind, ffmpeg, log_path,
                                          a.get("width"), a.get("height"))
    if on_line:
        on_line("$ " + " ".join(str(c) for c in cmd))
    rc, tail = _stream_ffmpeg(cmd, on_line=on_line, should_stop=should_stop)
    if rc != 0:
        raise EnhanceError(f"{kind} 评测失败(exit={rc}):\n" + "\n".join(tail[-6:]))
    return parse_metric_output(kind, "\n".join(tail), log_path)


def build_frame_command(video, t_sec, out_png, ffmpeg="ffmpeg"):
    """抽单帧命令；-ss 放在 -i 之前是关键帧快定位，放到后面会逐帧解码到该点"""
    return [ffmpeg, "-hide_banner", "-v", "error", "-y",
            "-ss", _fnum(max(0.0, float(t_sec or 0)), 3), "-i", video,
            "-frames:v", "1", out_png]


def extract_frame(video, t_sec, out_png, ffmpeg, should_stop=None):
    """抽一帧存成 PNG，返回 (ok, reason)"""
    d = os.path.dirname(os.path.abspath(out_png))
    if d:
        os.makedirs(d, exist_ok=True)
    cmd = build_frame_command(video, t_sec, out_png, ffmpeg)
    rc, tail = _run_capture(cmd, timeout=180)
    if should_stop and should_stop():
        return False, "已取消"
    if rc != 0 or not os.path.isfile(out_png):
        return False, (tail.strip().splitlines() or [f"exit={rc}"])[-1][:200]
    return True, ""


def build_preview_command(src, t_sec, out_png, p, info=None, ffmpeg="ffmpeg"):
    """单帧滤镜预览命令：把当前 -vf 链跑在一帧上，秒级出图

    补帧（minterpolate）刻意排除：单帧看不出插帧效果，而它要先缓冲若干帧才吐出
    第一帧，留着会把「秒级预览」拖成十几秒，用户还以为界面卡死了。
    """
    q = dict(p or {})
    q["interp_on"] = False
    chain = build_filter_chain(q, info)
    cmd = [ffmpeg, "-hide_banner", "-v", "error", "-y",
           "-ss", _fnum(max(0.0, float(t_sec or 0)), 3), "-i", src]
    if chain:
        cmd += ["-vf", chain]
    cmd += ["-frames:v", "1", out_png]
    return cmd


def extract_preview_frame(src, t_sec, out_png, p, ffmpeg, info=None):
    """抽一帧并过一遍滤镜链，返回 (ok, reason)；与 extract_frame 的原帧配对使用"""
    d = os.path.dirname(os.path.abspath(out_png))
    if d:
        os.makedirs(d, exist_ok=True)
    cmd = build_preview_command(src, t_sec, out_png, p, info, ffmpeg)
    rc, tail = _run_capture(cmd, timeout=180)
    if rc != 0 or not os.path.isfile(out_png):
        return False, (tail.strip().splitlines() or [f"exit={rc}"])[-1][:200]
    return True, ""


# ==================== 历史留档 ====================

HISTORY_LIMIT = 50


def append_history(history, entry):
    """把一条记录追加进历史并做环形裁剪，返回新列表（不改传入的 list）

    不原地 append 是刻意的：调用方传进来的往往是 settings 缓存里的同一个 list
    对象，原地改会让「保存」和「当前值」指向同一份数据，回滚时无从比对。
    """
    out = list(history or [])
    out.insert(0, dict(entry or {}))
    return out[:HISTORY_LIMIT]


def summarize_params(p):
    """参数摘要，用于历史表格与终端一行回显"""
    if not p:
        return ""
    bits = [PRESETS.get(p.get("preset"), {}).get("label") or "自定义"]
    if p.get("scale_on"):
        bits.append(f"放大{p.get('scale_ratio')}x"
                    if p.get("scale_mode") == "ratio"
                    else f"至{p.get('scale_height')}p")
    enc = p.get("encoder") or "libx264"
    if p.get("rate_mode") == "size":
        bits.append(f"{enc} {p.get('target_mb')}MB 两遍")
    else:
        bits.append(f"{enc} crf{p.get('crf')} {p.get('codec_preset')}")
    return " · ".join(bits)
