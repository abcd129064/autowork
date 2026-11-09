# -*- coding: utf-8 -*-
"""掉电截断 mp4 的无损修复后端，服务工具页「视频修复」工作区

设计稿：design/video_repair_tool.html

录制端写 mp4 时把 mdat 的 size 字段留成 0，意为一直吃到文件尾，索引盒 moov 留到
最后落盘。中途掉电就永远等不到 moov，播放器一律报 moov atom not found，但 mdat
里的媒体字节本身完好，那就是一条 MPEG-4 Part 2 裸码流，由 00 00 01 B6 帧起始码
与 00 00 01 B3 GOP 起始码分隔。修复思路是补回配置头后重新封装，不做任何转码：

  1. 从同机录制的正常文件里剥出 VOL 头，即 moov→stsd→esds 的 DecoderSpecificInfo。
     MPEG-4 短头码流内部不带 VOL，分辨率与量化参数只存在于这份配置里，没有它
     ffmpeg 连帧尺寸都无从得知。
  2. 按 GOP 边界裁掉尾部残帧：最后一个 VOP 只写了一部分，切在它上面会留下悬空
     GOP 头，解码器改报 header damaged，等于把坏帧换成整段报错。
  3. ffmpeg -c:v copy 重封装成 mp4，媒体字节原样搬运。

约束：只处理「无 moov + mdat 尾扩展 + MPEG-4 Part 2 视频轨」这一类文件。已含 moov
的、H.264/H.265 的、带音轨的一律在体检阶段明确拒绝，不做猜测性修复。
"""
import hashlib
import json
import mmap
import os
import shutil
import subprocess
import tempfile
import time

# MPEG-4 Part 2 起始码：B3 = group_of_vop 即 GOP 头，B6 = visual_object 即一帧
GOP_CODE = b"\x00\x00\x01\xb3"
VOP_CODE = b"\x00\x00\x01\xb6"
# H.264 的 SPS 起始码，用来在体检时认出「不是 mp4v，本工具不修」
H264_SPS_CODE = b"\x00\x00\x01\x67"

_CHUNK = 1 << 20
# 参考文件探测的候选上限：同目录可能有几百个录像，逐个 ffprobe 会拖几十秒
_DISCOVER_LIMIT = 20


class RepairError(Exception):
    """可展示给用户的修复失败原因，终端与 InfoBar 直接用这条文案"""


class RepairCancelled(Exception):
    """用户取消：调用方据此清理半成品，不当作错误上报"""


# ==================== 环境 ====================

def find_ffmpeg():
    """返回 (ffmpeg, ffprobe) 可执行路径，缺失时为 (None, None)

    与仓库根 video_ai_enhance.py 同口径依赖系统 PATH：自带 ffmpeg 二进制会让
    安装包多出上百 MB，而这只是个低频救急工具，不值得为它扩包。
    """
    return shutil.which("ffmpeg"), shutil.which("ffprobe")


def _no_window_flags():
    """打包版没有控制台，起子进程不能弹出黑框"""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run_capture(cmd):
    """跑完再取全部输出，给 ffprobe 这类秒级命令用"""
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       encoding="utf-8", errors="replace",
                       creationflags=_no_window_flags())
    return p.returncode, (p.stdout or "")


# ==================== mp4 盒结构 ====================

def scan_boxes(path):
    """顺序走顶层盒，返回结构体检结果；走 mmap，不把整个文件读进内存

    录像动辄几百 MB，read() 全量载入会直接吃掉同量级内存并把 UI 进程顶到换页。
    """
    size = os.path.getsize(path)
    boxes = []
    mdat = None
    reason = ""
    if size < 16:
        return {"ok": False, "reason": "文件过小，不是有效 mp4", "file_size": size,
                "boxes": [], "has_moov": False, "mdat": None, "mdat_last": False,
                "payload_off": 0, "payload_size": 0}
    with open(path, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            pos = 0
            while pos + 8 <= size:
                bsize = int.from_bytes(mm[pos:pos + 4], "big")
                btype = bytes(mm[pos + 4:pos + 8])
                head = 8
                if bsize == 1:
                    # largesize：真实长度在随后 8 字节里，>4GB 的 mdat 会走这支
                    if pos + 16 > size:
                        reason = "盒头声明 largesize 但文件已截断"
                        break
                    bsize = int.from_bytes(mm[pos + 8:pos + 16], "big")
                    head = 16
                elif bsize == 0:
                    # size=0 是「本盒延伸到文件尾」的合法写法，录制端就是这么占位的
                    bsize = size - pos
                if bsize < head:
                    reason = f"盒 {btype!r} 长度 {bsize} 小于盒头，结构已损坏"
                    break
                boxes.append((btype.decode("latin1"), pos, bsize))
                if btype == b"mdat" and mdat is None:
                    mdat = (pos, bsize, head)
                pos += bsize
            else:
                if pos != size:
                    reason = f"盒链在 {pos} 处结束，文件尾还有 {size - pos} B 游离数据"
        finally:
            mm.close()

    has_moov = any(t == "moov" for t, _p, _s in boxes)
    if mdat is None:
        return {"ok": False, "reason": reason or "未找到 mdat 盒", "file_size": size,
                "boxes": boxes, "has_moov": has_moov, "mdat": None,
                "mdat_last": False, "payload_off": 0, "payload_size": 0}
    mpos, msize, mhead = mdat
    # mdat 必须是最后一个盒：否则「吃到文件尾」的假设不成立，裁剪点无从推算
    mdat_last = (mpos + msize) >= size
    if not mdat_last and not reason:
        reason = f"mdat 之后还有 {size - mpos - msize} B，媒体切点不可信"
    return {
        "ok": mdat_last and not reason,
        "reason": reason,
        "file_size": size,
        "boxes": boxes,
        "has_moov": has_moov,
        "mdat": (mpos, msize),
        "mdat_last": mdat_last,
        "payload_off": mpos + mhead,
        "payload_size": size - mpos - mhead,
    }


def scan_frames(path, off, size, head_scan=1 << 20):
    """在 mdat 负载上扫起始码，返回相对负载起点的帧/GOP 偏移与末帧残量

    顺带看一眼前 head_scan 字节里有没有 H.264 的 SPS：短头 MPEG-4 与 H.264 的
    修复路径完全不同，认错了会产出一个满屏马赛克的「成功」文件。
    """
    vops, gops = [], []
    is_h264 = False
    end = off + size
    with open(path, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            for code, out in ((VOP_CODE, vops), (GOP_CODE, gops)):
                i = mm.find(code, off, end)
                while i >= 0:
                    out.append(i - off)
                    # 步进 4 而非 1：起始码不会自我重叠，跳着找能省一半以上扫描量
                    i = mm.find(code, i + 4, end)
            is_h264 = mm.find(H264_SPS_CODE, off, min(end, off + head_scan)) >= 0
        finally:
            mm.close()

    gaps = [b - a for a, b in zip(vops, vops[1:])]
    gaps.sort()
    median = gaps[len(gaps) // 2] if gaps else 0
    tail = size - vops[-1] if vops else 0
    return {
        "vops": vops,
        "gops": gops,
        "frame_count": len(vops),
        "gop_count": len(gops),
        "median_frame": median,
        "last_vop_tail": tail,
        # 启发式：末帧明显短于中位帧大概率是断电时写了一半。反过来「不短」不等于
        # 写完——末帧后面不再有起始码，流本身无法自证，auto 档照样会把它裁掉
        "last_frame_incomplete": bool(vops) and tail < max(median // 2, 1),
        "is_h264": is_h264,
    }


# ==================== 参考文件 ====================

def parse_esds(buf):
    """从 esds 盒负载里剥出 DecoderSpecificInfo，即 VOL 配置头

    描述符链 0x03 ES_Descriptor → 0x04 DecoderConfigDescriptor → 0x05
    DecoderSpecificInfo，每层长度是可变字节数的 7bit 编码。
    """
    def read_len(j):
        n = 0
        for _ in range(4):
            if j >= len(buf):
                return None, j
            b = buf[j]
            n = (n << 7) | (b & 0x7F)
            j += 1
            if not (b & 0x80):
                break
        return n, j

    i = 0
    ln = 0
    for tag, skip in ((0x03, 3), (0x04, 13), (0x05, 0)):
        if i >= len(buf) or buf[i] != tag:
            return None
        ln, i = read_len(i + 1)
        if ln is None:
            return None
        # 0x03 层的 flags 最高位置位表示带 dependsOn_ES_ID，要多跳 2 字节
        if tag == 0x03 and i + 2 < len(buf) and (buf[i + 2] & 0x80):
            skip += 2
        i += skip
    if i + ln > len(buf):
        return None
    vol = bytes(buf[i:i + ln])
    # VOL 必须以起始码开头，否则说明描述符层级对不上，宁可报错也不要写坏文件
    return vol if vol[:3] == b"\x00\x00\x01" else None


def read_vol(path):
    """从正常 mp4 的 esds 里读出 VOL 头原始字节，读不到返回 None"""
    size = os.path.getsize(path)
    if size == 0:
        # mmap 不接受空文件，这里先挡掉，否则抛的是 ValueError 而不是 None
        return None
    with open(path, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            at = mm.find(b"esds")
            if at < 0:
                return None
            # find 命中的是盒的类型字段，描述符从 +8 处开始，前 4B 是版本与标志
            # 只取 64KB：esds 之后紧跟的就是 stsd 里的其他内容，够用且不必全量拷贝
            chunk = bytes(mm[at + 8: min(size, at + 8 + 65536)])
        finally:
            mm.close()
    return parse_esds(chunk)


def probe_reference(path, ffprobe=None):
    """ffprobe 读参考文件的编码参数，供界面核对与体检判定"""
    if ffprobe is None:
        _ff, ffprobe = find_ffmpeg()
    if not ffprobe:
        raise RepairError("未找到 ffprobe，请安装 ffmpeg 并加入 PATH")
    rc, out = _run_capture([
        ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=codec_name,profile,width,height,r_frame_rate:format_tags=encoder",
        "-of", "json", path])
    if rc != 0:
        raise RepairError(f"参考文件读取失败: {out.strip()[:200] or 'ffprobe 退出码 ' + str(rc)}")
    try:
        info = json.loads(out or "{}")
    except ValueError:
        raise RepairError(f"参考文件参数解析失败: {out.strip()[:200]}")
    streams = info.get("streams") or [{}]
    st = streams[0]
    rate = str(st.get("r_frame_rate") or "0/1")
    num, _, den = rate.partition("/")
    try:
        fps = float(num) / float(den) if float(den) else 0.0
    except ValueError:
        fps = 0.0
    rc2, out2 = _run_capture([ffprobe, "-v", "error", "-select_streams", "a",
                              "-show_entries", "stream=codec_name", "-of", "csv=p=0",
                              path])
    audio = bool((out2 or "").strip())
    boxes = scan_boxes(path)
    return {
        "path": path,
        "codec": st.get("codec_name") or "",
        "profile": st.get("profile") or "",
        "width": st.get("width") or 0,
        "height": st.get("height") or 0,
        "fps": round(fps, 3),
        "encoder": (info.get("format", {}).get("tags", {}) or {}).get("encoder", ""),
        "has_audio": audio,
        "has_moov": boxes["has_moov"],
    }


def discover_reference(broken_path, ffprobe=None, limit=_DISCOVER_LIMIT):
    """在损坏文件同目录里自动找一个可当参考的正常 mp4，找不到返回 None

    规则：按修改时间倒序取前 limit 个 mp4，排除损坏文件自身与已生成的
    *_repaired.mp4，要求含 moov 且编码为 mpeg4。同一台录制机的参数是固定的，
    同目录邻居比用户手挑更可靠，也省掉每次重选。
    """
    folder = os.path.dirname(os.path.abspath(broken_path))
    broken_abs = os.path.abspath(broken_path)
    try:
        names = [n for n in os.listdir(folder) if n.lower().endswith(".mp4")]
    except OSError:
        return None
    cands = []
    for n in names:
        p = os.path.join(folder, n)
        if os.path.abspath(p) == broken_abs or n.lower().endswith("_repaired.mp4"):
            continue
        try:
            cands.append((os.path.getmtime(p), p))
        except OSError:
            continue
    cands.sort(reverse=True)
    for _mtime, p in cands[:limit]:
        try:
            if not scan_boxes(p)["has_moov"]:
                continue
            if probe_reference(p, ffprobe)["codec"] != "mpeg4":
                continue
        except (RepairError, OSError, ValueError):
            continue
        return p
    return None


# ==================== 体检 ====================

def probe_broken(path, ffprobe=None):
    """对损坏文件做完整体检，返回 {ok, reason, ...统计}

    ok 为真才允许修复；为假时 reason 就是给用户看的那句话，必须说清是哪一条
    前提不满足，不能只回一个「失败」。
    """
    if not os.path.isfile(path):
        return {"ok": False, "reason": "文件不存在", "path": path}
    boxes = scan_boxes(path)
    result = {
        "ok": False, "reason": "", "path": path, "boxes": boxes,
        "frames": None, "file_size": boxes["file_size"],
    }
    if not boxes["boxes"]:
        result["reason"] = boxes["reason"] or "无法解析 mp4 盒结构"
        return result
    if boxes["has_moov"]:
        result["reason"] = ("已找到 moov 索引，不属于掉电截断。若仍打不开多半是 "
                            "mdat 内部损坏，本工具不处理")
        return result
    if not boxes["mdat_last"]:
        result["reason"] = boxes["reason"] or "mdat 不是最后一个盒，媒体切点不可信"
        return result
    frames = scan_frames(path, boxes["payload_off"], boxes["payload_size"])
    result["frames"] = frames
    if frames["is_h264"]:
        result["reason"] = "码流是 H.264，当前只支持 MPEG-4 Part 2"
        return result
    if not frames["frame_count"]:
        result["reason"] = "mdat 里找不到 MPEG-4 帧起始码 00 00 01 B6，不是本工具能修的码流"
        return result
    if not frames["gop_count"]:
        result["reason"] = "找不到 GOP 起始码 00 00 01 B3，无法确定安全裁剪点"
        return result
    result["ok"] = True
    result["reason"] = "符合掉电截断特征，可修复"
    return result


def pick_cut(vops, gops, mode="auto"):
    """决定裸流裁到哪里

    auto 是默认档，取「小于最后一个 VOP 偏移的最大 GOP 偏移」，把残帧连同它所属的
    GOP 头一起丢掉。vop 档直接切在最后一个 VOP 起点上，会把 GOP 头留在流尾变成
    悬空半头——实测解码器因此从「末帧损坏」升级成「header damaged」整段报错。
    """
    if not vops:
        raise RepairError("没有可用的帧起始码，无法确定裁剪点")
    if mode == "vop":
        return vops[-1]
    last = vops[-1]
    for g in reversed(gops):
        if g < last:
            return g
    # 整个文件只有一个 GOP 时没有更早的 GOP 头可退，只能退到第一帧
    return vops[0]


# ==================== 校验辅助 ====================

def sha_range(path, off, size):
    """分块计算文件 [off, off+size) 区间的 SHA-256"""
    h = hashlib.sha256()
    left = size
    with open(path, "rb") as f:
        f.seek(off)
        while left > 0:
            buf = f.read(min(_CHUNK, left))
            if not buf:
                break
            h.update(buf)
            left -= len(buf)
    return h.hexdigest()


def probe_output(path, ffprobe):
    """ffprobe 数输出文件的真实帧数：-count_frames 逐帧解，nb_read_frames 才可信"""
    rc, out = _run_capture([
        ffprobe, "-v", "error", "-count_frames", "-select_streams", "v:0",
        "-show_entries",
        "stream=codec_name,profile,width,height,r_frame_rate,nb_read_frames,duration",
        "-of", "json", path])
    if rc != 0:
        raise RepairError(f"输出校验失败: {out.strip()[:200] or 'ffprobe 退出码 ' + str(rc)}")
    info = json.loads(out or "{}")
    st = (info.get("streams") or [{}])[0]
    rate = str(st.get("r_frame_rate") or "0/1")
    num, _, den = rate.partition("/")
    try:
        fps = round(float(num) / float(den), 3) if float(den) else 0.0
    except ValueError:
        fps = 0.0
    try:
        frames = int(st.get("nb_read_frames") or 0)
    except (TypeError, ValueError):
        frames = 0
    try:
        duration = float(st.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "codec": st.get("codec_name") or "",
        "profile": st.get("profile") or "",
        "width": st.get("width") or 0,
        "height": st.get("height") or 0,
        "fps": fps,
        "frames": frames,
        "duration": round(duration, 3),
    }


def count_decode_errors(path, ffmpeg):
    """完整解码一遍数错误行数，返回 (错误数, 前几条错误)

    帧数对得上不代表能播：参考文件分辨率不匹配时 ffmpeg 照样封装成功，只有真的
    解一遍才看得出满屏花屏。这一步是本工具「敢说无损」的底气。
    """
    cmd = [ffmpeg, "-v", "error", "-i", path, "-f", "null", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         encoding="utf-8", errors="replace", bufsize=1,
                         creationflags=_no_window_flags())
    lines = []
    try:
        for raw in p.stdout:
            s = raw.strip()
            if s:
                lines.append(s)
    finally:
        p.stdout.close()
    p.wait()
    return len(lines), lines[:5]


# ==================== 修复主流程 ====================

def _run_ffmpeg(cmd, on_line=None, should_stop=None):
    """跑 ffmpeg 并逐行转发输出，支持中途取消

    用 -progress pipe:1 拿机器可读进度，并把 stderr 并进 stdout：两个管道分别读
    需要额外线程，而 stderr 写满 64KB 缓冲区时 ffmpeg 会阻塞，单管道读才不会卡死。
    """
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         encoding="utf-8", errors="replace", bufsize=1,
                         creationflags=_no_window_flags())
    errors = []
    last_report = 0.0
    try:
        for raw in p.stdout:
            if should_stop and should_stop():
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
                raise RepairCancelled()
            line = raw.rstrip("\r\n")
            if not line:
                continue
            key, sep, val = line.partition("=")
            # progress 记录都是 key=value 且键名无空格，据此与 ffmpeg 的文本输出区分
            if sep and " " not in key:
                if key in ("out_time_us", "out_time_ms"):
                    now = time.monotonic()
                    if now - last_report >= 0.5:
                        last_report = now
                        try:
                            sec = int(val) / 1_000_000
                        except ValueError:
                            continue
                        if on_line:
                            on_line(f"[ffmpeg] 已写入 {sec:.1f} s")
                continue
            errors.append(line)
            if on_line:
                on_line(line)
    finally:
        p.stdout.close()
    rc = p.wait()
    return rc, errors


def repair(broken, reference, out, fps=25, cut_mode="auto", verify_decode=True,
           on_line=None, should_stop=None, ffmpeg=None, ffprobe=None):
    """修复一个掉电截断的 mp4，返回校验报告 dict

    失败抛 RepairError，文案可直接展示；用户取消抛 RepairCancelled。
    原文件全程只读，输出路径与输入相同时直接拒绝。
    """
    def say(msg):
        if on_line:
            on_line(msg)

    def stopped():
        return bool(should_stop and should_stop())

    if ffmpeg is None or ffprobe is None:
        found_ff, found_fp = find_ffmpeg()
        ffmpeg = ffmpeg or found_ff
        ffprobe = ffprobe or found_fp
    if not ffmpeg or not ffprobe:
        raise RepairError("未找到 ffmpeg / ffprobe，请先安装 ffmpeg 并加入 PATH")
    if not os.path.isfile(broken):
        raise RepairError(f"损坏文件不存在: {broken}")
    if not os.path.isfile(reference):
        raise RepairError(f"参考文件不存在: {reference}")
    if os.path.abspath(broken) == os.path.abspath(out):
        raise RepairError("输出路径不能与损坏文件相同，本工具不改写原件")

    say("[1/4] 体检损坏文件…")
    boxes = scan_boxes(broken)
    if boxes["has_moov"]:
        raise RepairError("损坏文件已含 moov 索引，不属于掉电截断，本工具不处理")
    if not boxes["mdat_last"]:
        raise RepairError(boxes["reason"] or "mdat 不是最后一个盒，媒体切点不可信")
    frames = scan_frames(broken, boxes["payload_off"], boxes["payload_size"])
    if not frames["frame_count"] or not frames["gop_count"]:
        raise RepairError("mdat 里找不到 MPEG-4 起始码，不是本工具能修的码流")
    cut = pick_cut(frames["vops"], frames["gops"], cut_mode)
    payload_size = boxes["payload_size"]
    say(f"      盒结构 ftyp + mdat 尾扩展，负载 {payload_size:,} B，起点 0x{boxes['payload_off']:X}")
    say(f"      帧起始码 {frames['frame_count']} 个，GOP {frames['gop_count']} 个，"
        f"中位帧长 {frames['median_frame']:,} B")
    say(f"      末帧残留 {frames['last_vop_tail']:,} B · "
        f"{'明显偏短' if frames['last_frame_incomplete'] else '长度正常'}，随 GOP 丢弃")
    say(f"      裁剪点 0x{cut:X} · {cut_mode} 档，丢弃 {payload_size - cut:,} B")

    say("[2/4] 提取参考文件 VOL 配置头…")
    ref = probe_reference(reference, ffprobe)
    if ref["codec"] != "mpeg4":
        raise RepairError(f"参考文件编码是 {ref['codec'] or '未知'}，需要 mpeg4")
    if ref["has_audio"]:
        raise RepairError("参考文件含音轨，流拷贝会丢音轨，本工具不处理")
    vol = read_vol(reference)
    if not vol:
        raise RepairError("无法从参考文件的 esds 中提取 VOL 头，请换同机录制的正常文件")
    say(f"      {ref['width']}×{ref['height']} · {ref['fps']} fps · {ref['profile']} · "
        f"VOL {len(vol)} B · {vol[:6].hex(' ')}")
    if not fps or fps <= 0:
        fps = ref["fps"] or 25
    say(f"      封装帧率取 {fps} fps")

    if stopped():
        raise RepairCancelled()

    tmp_dir = tempfile.mkdtemp(prefix="autowork_repair_")
    mpg = os.path.join(tmp_dir, "payload.mpg")
    try:
        # VOL + 裁剪后的裸流拼成 .mpg：ffmpeg 需要从流里读到配置头才知道帧尺寸
        with open(broken, "rb") as src, open(mpg, "wb") as dst:
            dst.write(vol)
            src.seek(boxes["payload_off"])
            left = cut
            while left > 0:
                buf = src.read(min(_CHUNK, left))
                if not buf:
                    break
                dst.write(buf)
                left -= len(buf)
        say(f"      裸流已拼接 → {mpg} · {os.path.getsize(mpg):,} B")

        say("[3/4] ffmpeg 流拷贝重封装，不转码…")
        # -c:v mpeg4 必须显式指定：裸流里没有 mpeg1 序列头，自动探测会误判成
        # mpeg1video 并报 "aspect ratio has forbidden 0 value"
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
               "-progress", "pipe:1", "-f", "mpegvideo", "-framerate", str(fps),
               "-c:v", "mpeg4", "-i", mpg, "-c:v", "copy",
               "-avoid_negative_ts", "make_zero", out]
        rc, errors = _run_ffmpeg(cmd, say, should_stop)
        if rc != 0:
            hint = ("；常见原因是参考文件分辨率不一致，请换同机录制的参考文件"
                    if errors else "")
            raise RepairError(f"ffmpeg 重封装失败，退出码 {rc}: "
                              f"{'; '.join(errors)[:300]}{hint}")
        size = os.path.getsize(out)
        say(f"      已写出 {out} · {size:,} B")

        say("[4/4] 校验…")
        info = probe_output(out, ffprobe)
        say(f"      编码 {info['codec']} · {info['profile']} · {info['width']}×{info['height']} · "
            f"{info['fps']} fps · 帧数 {info['frames']} · 时长 {info['duration']} s")

        # 无损证明：输出 mdat 去掉借来的 VOL 后，必须与原文件负载前缀逐字节相同
        out_boxes = scan_boxes(out)
        sha_src = sha_range(broken, boxes["payload_off"], cut)
        sha_out = sha_range(out, out_boxes["payload_off"] + len(vol), cut)
        sha_ok = sha_src == sha_out
        say(f"      媒体字节 SHA-256 {sha_src[:16]}… · "
            f"{'与原件负载前缀一致，无损' if sha_ok else '与原件不一致，请人工核对'}")

        decode_errors, decode_lines = (0, [])
        # 重封装已成功，此时取消不该扔掉成品：Worker 的取消路径会删 out，
        # 所以这里只跳过校验、照常返回报告
        if stopped():
            say("      已请求取消，跳过校验，输出文件保留")
            verify_decode = False
        if verify_decode:
            decode_errors, decode_lines = count_decode_errors(out, ffmpeg)
            say(f"      完整解码 {decode_errors} 处错误"
                + (f"：{'; '.join(decode_lines)[:200]}" if decode_errors else ""))

        return {
            "out": out, "size": size, "cancelled": False,
            "frames": info["frames"], "duration": info["duration"],
            "width": info["width"], "height": info["height"], "fps": info["fps"],
            "codec": info["codec"], "profile": info["profile"],
            "total_vops": frames["frame_count"],
            "dropped_bytes": payload_size - cut,
            "cut_mode": cut_mode, "cut_offset": cut,
            "vol_bytes": len(vol),
            "sha_ok": sha_ok, "sha256": sha_src,
            "verify_decode": bool(verify_decode),
            "decode_errors": decode_errors, "decode_lines": decode_lines,
            "reference": reference,
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
