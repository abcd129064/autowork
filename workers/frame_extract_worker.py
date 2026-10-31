# -*- coding: utf-8 -*-
"""多视频批量切帧 Worker（训练集数据生产，含自动遮挡过滤）

一键流水线：每个视频 -> 均匀粗抽 2K 帧 -> 遮挡过滤（人/球杆不入画）->
内容去重保留 K 张 -> <输出根>/<视频名>/train_0001.jpg...。全程零参数。

遮挡过滤原理（2026-11-07 两轮真机迭代定稿，numpy 全分辨率单趟）：
  台呢是高饱和绿色；逐帧解码全分辨率 RGB，以绿像元中位色为参考，
  「色差 > 95 且非强绿相（g<=r+25）」的像元为外来。行列绿占比定位台面
  （排除桌外地板），红球掩膜膨胀豁免暗红过渡带，1px 膨胀桥接细杆抗锯齿
  断点。连通域满足实心块（手/臂/衣物）或细长条（球杆）即判遮挡。

ffmpeg 查找：AUTOWORK_FFMPEG / AUTOWORK_FFPROBE 环境变量 > PATH >
D:\ffmpeg-7.0.1-essentials_build\bin 兜底。numpy 为本仓既有依赖
（requirements.txt numpy>=1.24.0），不新增第三方包。
"""
import os
import re
import shutil
import subprocess
from collections import deque

import numpy as np
from PySide6.QtCore import QThread, Signal

# 本地绿色版 ffmpeg 兜底目录；PATH 与环境变量优先
_FALLBACK_DIR = r"D:\ffmpeg-7.0.1-essentials_build\bin"
# 一键模式固定参数：粗抽 2K 再过滤+去重到 K，给筛选留丢弃空间
_COARSE_RATIO = 2
# 遮挡判定阈值（全分辨率像素）。真机实测分布（20261106_205700 整批 100 帧）：
# 干净帧最大连通域长边 <= 62（球+影），漏网的手+杆连通域 >= 83，
# 细长条阈值取 75 居中；实心块 600/0.45 兜底大手/衣物。
# 三颗非红球紧连的极端场景会误杀，属可接受代价
_OCCLUDE_SOLID_AREA = 600
_OCCLUDE_SOLID_FILL = 0.45
_OCCLUDE_ELONG_DIM = 75
# 绿色覆盖率低于此值说明帧里根本没有台呢（非台球视频/黑场），
# 分类器失去前提，此时跳过过滤全保留，宁滥勿缺
_MIN_GREEN_COVERAGE = 0.20
# 行/列绿占比超过此值才算桌面行/列（桌外地板行通常 < 0.2）
_TABLE_PROFILE_FRAC = 0.30
# 外来像元判定：与台呢中位色的欧氏距离阈值。实测本机素材——
# baulk 标线 ~69（排除）、球影 ~94（排除）、阴影杆身 ~106（检出）、白球 ~350
_FOREIGN_DIST = 95
# 内容指纹网格（与去重距离度量配套）；从全分辨率点采样，无需二次解码
_VEC_GRID = 32
_VEC_STEP_SCALE = 3  # 全分辨率通常是 960x542，约 1/3 网格密度采样


def _find_tool(name):
    """定位 ffmpeg/ffprobe；找不到抛 RuntimeError 由 UI 层提示。"""
    env = os.environ.get("AUTOWORK_" + name.upper())
    if env:
        return env
    found = shutil.which(name)
    if found:
        return found
    fallback = os.path.join(_FALLBACK_DIR, name + ".exe")
    if os.path.isfile(fallback):
        return fallback
    raise RuntimeError(f"找不到 {name}，请确认 ffmpeg 已安装或设置 AUTOWORK_{name.upper()}")


def probe_duration(ffprobe, video):
    """视频时长（秒）；读取失败抛 ValueError，由调用方按单个视频失败处理。"""
    import json
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "json", video],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise ValueError(out.stderr.strip()[:200])
    return float(json.loads(out.stdout)["format"]["duration"])


def uniform_extract(ffmpeg, ffprobe, video, outdir, n, prefix="img_"):
    """全程均匀抽 n 帧；fps=n/时长保证首尾都覆盖且间隔恒定。"""
    os.makedirs(outdir, exist_ok=True)
    dur = probe_duration(ffprobe, video)
    r = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", video,
         "-vf", f"fps={n}/{dur}", "-q:v", "2",
         "-start_number", "1", os.path.join(outdir, prefix + "%03d.jpg")],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise ValueError(r.stderr.strip()[:200])
    return sorted(f for f in os.listdir(outdir) if f.startswith(prefix))


def decode_rgb(ffmpeg, path):
    """解码原始分辨率 RGB24 rawvideo，返回 (H, W, 3) uint8 数组。"""
    import json
    r = subprocess.run(
        [ffmpeg, "-v", "error", "-i", path,
         "-vf", "scale=iw:ih", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
        capture_output=True)
    buf = r.stdout
    # 尺寸不能靠猜：从 JPEG 头解析宽高，校验缓冲区长度
    w, h = _jpeg_size(path)
    if r.returncode != 0 or len(buf) != w * h * 3:
        raise ValueError(f"解码失败: {path}")
    arr = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    return arr


def _jpeg_size(path):
    """从 JPEG SOF 段读宽高（解码前需要尺寸来校验 rawvideo 长度）"""
    import struct
    with open(path, "rb") as f:
        data = f.read(64 * 1024)
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        if marker == 0xD8 or 0xD0 <= marker <= 0xD9 or marker == 0x01:
            i += 2
            continue
        seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
        i += 2 + seg_len
    raise ValueError(f"无法解析 JPEG 尺寸: {path}")


def analyze_frame(arr):
    """全分辨率遮挡分析。返回 (is_clean, content_vec)。

    外来像元判定用「与台呢中位色的距离 > 95」加上「强绿相豁免（g > r+25）」：
    阴影中的橄榄色杆身（g-r≈5）和深色衣物（g-r≈6）与台呢色差都超阈值，若按
    宽松绿相豁免会整根/整片漏检（2026-11-07 真机审计教训）；真正的球影是
    强绿相（g-r≈60），豁免线取 25 居中。baulk 线/D 弧色差只有 ~69，距离阈值
    天然排除。另有红球团簇豁免（高饱和红 + 掩膜膨胀 2 圈盖住暗红过渡带）。
    台面定位：绿像元行/列占比 > 0.30 的行/列区间，桌外地板天然出局。
    外来掩膜先膨胀 1 圈再算连通域：桥接细杆上的抗锯齿断点，否则斜杆
    碎成对角小分量（2026-11-07 真机踩坑）。
    遮挡判定（框内外来像元连通域）：
      实心块 area>=400 且 fill>=0.35（手/臂/衣物，斜杆尾会把填充率拉到 ~0.4）；
      细长条长边>=70（球杆）。
    """
    h, w = arr.shape[:2]
    px = arr.astype(np.int16)
    r = px[..., 0]
    g = px[..., 1]
    b = px[..., 2]
    green = (g > r + 15) & (g > b + 10)
    red = (r > g + 90) & (r > b + 60)
    vec = _sample_vec(arr)
    coverage = float(green.mean())
    if coverage < _MIN_GREEN_COVERAGE:
        return True, vec

    row_idx = np.where(green.mean(axis=1) > _TABLE_PROFILE_FRAC)[0]
    col_idx = np.where(green.mean(axis=0) > _TABLE_PROFILE_FRAC)[0]
    if len(row_idx) == 0 or len(col_idx) == 0:
        return True, vec

    # 台呢参考色：绿像元中位数（对光照渐变稳健）；采样 2 万点足够
    gy, gx = np.nonzero(green)
    if len(gy) > 20000:
        sel = np.linspace(0, len(gy) - 1, 20000).astype(int)
        gy, gx = gy[sel], gx[sel]
    cloth = np.median(px[gy, gx], axis=0)
    dist = np.sqrt(((px - cloth) ** 2).sum(axis=2))

    foreign = (dist > _FOREIGN_DIST) & ~red & ~(g > r + 25)

    # 红球掩膜膨胀 2 圈（3x3），暗红过渡带随饱和红一并豁免
    dil = red.copy()
    for _ in range(2):
        d = dil.copy()
        d[1:, :] |= dil[:-1, :]
        d[:-1, :] |= dil[1:, :]
        d[:, 1:] |= dil[:, :-1]
        d[:, :-1] |= dil[:, 1:]
        dil = d
    foreign &= ~dil

    foreign[:row_idx[0], :] = False
    foreign[row_idx[-1] + 1:, :] = False
    foreign[:, :col_idx[0]] = False
    foreign[:, col_idx[-1] + 1:] = False

    # 膨胀 1 圈桥接细杆抗锯齿断点，再稀疏 BFS
    f = foreign.copy()
    f[1:, :] |= foreign[:-1, :]
    f[:-1, :] |= foreign[1:, :]
    f[:, 1:] |= foreign[:, :-1]
    f[:, :-1] |= foreign[:, 1:]

    if _foreign_block_exists(f, (col_idx[0], col_idx[-1], row_idx[0], row_idx[-1])):
        return False, vec
    return True, vec


def _foreign_block_exists(foreign, box):
    """稀疏连通域判定：实心块或细长条即遮挡。外来像元通常 <1 万，纯 BFS 足够。

    box 为台面外接框；四个袋口角是暗色中性块（g-r≈20，介于衣物与影子之间，
    颜色上无法区分），但它们必然紧贴框角且尺寸有限——贴角小分量按袋口豁免。
    """
    minx, maxx, miny, maxy = box
    ys, xs = np.nonzero(foreign)
    if len(ys) == 0:
        return False
    pts = set(zip(ys.tolist(), xs.tolist()))
    seen = set()
    for start in pts:
        if start in seen:
            continue
        area = 0
        min_y = max_y = start[0]
        min_x = max_x = start[1]
        queue = deque((start,))
        seen.add(start)
        while queue:
            y, x = queue.popleft()
            area += 1
            if y < min_y:
                min_y = y
            if y > max_y:
                max_y = y
            if x < min_x:
                min_x = x
            if x > max_x:
                max_x = x
            for ny in (y - 1, y, y + 1):
                for nx in (x - 1, x, x + 1):
                    if (nx != x or ny != y) and (ny, nx) in pts and (ny, nx) not in seen:
                        seen.add((ny, nx))
                        queue.append((ny, nx))
        bw = max_x - min_x + 1
        bh = max_y - min_y + 1
        max_dim = max(bw, bh)
        # 贴框角的小分量 = 袋口（角袋挖洞+网兜），豁免
        near_left = min_x <= minx + 45
        near_right = max_x >= maxx - 45
        near_top = min_y <= miny + 45
        near_bottom = max_y >= maxy - 45
        if (near_left or near_right) and (near_top or near_bottom) and max_dim <= 70:
            continue
        fill = area / (bw * bh)
        # 细长条（球杆）：长边超大即拦，不看填充率（斜杆 bbox 填充低）
        if max_dim >= _OCCLUDE_ELONG_DIM:
            return True
        # 实心块（手/臂/衣物）：大面积且紧凑；相邻两三颗球 fill 低不会命中
        if area >= _OCCLUDE_SOLID_AREA and fill >= _OCCLUDE_SOLID_FILL:
            return True
    return False


def _sample_vec(arr):
    """全分辨率点采样成 32x32 灰度指纹（供 FPS 去重距离计算）。"""
    h, w = arr.shape[:2]
    step_y = max(1, h // (_VEC_GRID * _VEC_STEP_SCALE))
    step_x = max(1, w // (_VEC_GRID * _VEC_STEP_SCALE))
    small = arr[::step_y, ::step_x]
    gray = (small[..., 0].astype(np.uint16) * 299
            + small[..., 1].astype(np.uint16) * 587
            + small[..., 2].astype(np.uint16) * 114) // 1000
    ys = np.linspace(0, gray.shape[0] - 1, _VEC_GRID).astype(int)
    xs = np.linspace(0, gray.shape[1] - 1, _VEC_GRID).astype(int)
    return gray[np.ix_(ys, xs)].astype(np.uint8).tobytes()


def fps_select(vecs, k):
    """最远点采样：返回 k 个下标（升序），帧间内容差异最大化。

    种子取与其他帧平均距离最大者，避免从时间起点偏向片头。
    """
    n = len(vecs)
    if n <= k:
        return list(range(n))
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
    return sorted(selected)


def safe_stem(video_path):
    """视频文件名（不含扩展名）净化为安全目录名：中文保留，非法字符换 _。"""
    stem = os.path.splitext(os.path.basename(video_path))[0]
    return re.sub(r'[\\/:*?"<>|]+', "_", stem).strip() or "video"


class FrameExtractWorker(QThread):
    """逐个视频跑「粗抽 2K -> 遮挡过滤 -> 去重 K」流水线，按视频名分文件夹。

    Signals:
        line(str): 进度日志（逐行）
        video_done(str, str, int): 单个视频完成（视频名, 输出目录, 保留张数）
        finished_ok(str): 全部完成（输出根目录）
        error(str): 致命错误（ffmpeg 缺失等，任务中止）
    """

    line = Signal(str)
    video_done = Signal(str, str, int)
    finished_ok = Signal(str)
    error = Signal(str)

    def __init__(self, videos, out_root, keep=50, parent=None):
        super().__init__(parent)
        self._videos = list(videos)
        self._out_root = out_root
        self._keep = keep
        # 只在视频之间检查：单个视频内部处理只要几秒，不值得做细粒度取消
        self._cancelled = False

    def cancel(self):
        """请求在当前视频跑完后停止（幂等）。"""
        self._cancelled = True

    def run(self):
        try:
            ffmpeg = _find_tool("ffmpeg")
            ffprobe = _find_tool("ffprobe")
        except RuntimeError as e:
            self.error.emit(str(e))
            return
        os.makedirs(self._out_root, exist_ok=True)
        ok = fail = 0
        for idx, video in enumerate(self._videos, 1):
            if self._cancelled:
                self.line.emit("■ 已取消：剩余视频不再处理")
                break
            stem = safe_stem(video)
            final_dir = os.path.join(self._out_root, stem)
            # 粗抽目录放临时子目录，筛选完成后整体删除，最终目录只留成品
            tmp_dir = os.path.join(final_dir, "_raw")
            self.line.emit(f"[{idx}/{len(self._videos)}] {os.path.basename(video)} 切帧中 ...")
            try:
                names = uniform_extract(ffmpeg, ffprobe, video, tmp_dir,
                                        self._keep * _COARSE_RATIO)
                clean = []  # (name, vec)
                blocked = 0
                for n_ in names:
                    is_clean, vec = analyze_frame(decode_rgb(ffmpeg, os.path.join(tmp_dir, n_)))
                    if is_clean:
                        clean.append((n_, vec))
                    else:
                        blocked += 1
                self.line.emit(f"  遮挡过滤: {len(names)} -> {len(clean)} 张干净"
                               f"（剔除 {blocked} 张人/球杆入画）")
                if not clean:
                    raise ValueError("没有一帧画面干净（无台呢或全程遮挡）")
                vecs = [v for _, v in clean]
                keep_idx = fps_select(vecs, self._keep)
                os.makedirs(final_dir, exist_ok=True)
                # 先清掉旧产物再落新：输出目录复用时残留的旧 train_*.jpg
                # 会混进新数据集（2026-11-07 真机踩坑）
                for old in os.listdir(final_dir):
                    if old.startswith("train_") and old.endswith(".jpg"):
                        os.remove(os.path.join(final_dir, old))
                for rank, i in enumerate(keep_idx, 1):
                    shutil.copyfile(os.path.join(tmp_dir, clean[i][0]),
                                    os.path.join(final_dir, f"train_{rank:04d}.jpg"))
                kept = len(keep_idx)
                if kept < self._keep:
                    self.line.emit(f"  ⚠ 干净帧不足 {self._keep} 张，实际保留 {kept} 张")
                self.video_done.emit(os.path.basename(video), final_dir, kept)
                self.line.emit(f"  ✔ {stem}: 保留 {kept} 张")
                ok += 1
            except Exception as e:
                # 单个视频失败不拖垮整批：记录后继续下一个
                fail += 1
                self.line.emit(f"  ✘ {stem}: {e}")
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
        summary = f"完成: 成功 {ok} 个视频, 失败 {fail} 个 -> {self._out_root}"
        self.line.emit(f"✔ {summary}")
        if ok == 0:
            self.error.emit(summary)
        else:
            self.finished_ok.emit(self._out_root)
