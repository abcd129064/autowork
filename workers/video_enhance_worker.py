# -*- coding: utf-8 -*-
"""视频画质提升 Worker（工具页「画质提升」工作区）

五类活儿都必须离开 UI 线程：ffprobe 探一个几百 MB 的录像要几十毫秒、批量增强
是分钟级、minterpolate 补帧在 1080p 上能跑到 0.1x 实时速度。放在主线程里整个
FluentWindow 会冻住，连左侧导航都点不动。

命令拼装、滤镜链、进度解析全在 windows.tools.video_enhance（纯 stdlib，不 import
Qt），本文件只负责三件事：把 stage 分派到对应的批处理循环、把引擎的回调翻成 Qt
信号、以及取消与半成品的收口。

批处理策略是**单个文件失败不中断整批**：一条录像探测不出来或编码器报错，只把
那一行标红（item_failed），后面的继续跑。用户往往是几十条录像一起丢进来，因为
其中一条坏掉就把已经跑完的全丢掉是不可接受的。只有用户取消和 ffmpeg 未定位这类
全局问题才终止整批。

注意不要把业务信号命名成 finished——那是 QThread 原生信号，遮蔽过会崩
（本仓 NewLog/售后/单杆 worker 都踩过）。
"""
import os
import shutil
import tempfile
import time
import traceback
import uuid

from PySide6.QtCore import QThread, Signal

from windows.tools import video_enhance as ve

# stage 取值：
#   probe    批量 ffprobe，填任务表的分辨率/帧率/时长/大小
#   enhance  批量增强（本 Worker 的主业）
#   env      环境检测：ffmpeg 版本 + 编译进来的编码器/滤镜 + 逐个真编一帧试可用性
#   metric   质量对比：VMAF / PSNR / SSIM 客观评分
#   frame    质量对比：抽同一时间点的两帧 PNG 做左右并排
#   preview  滤镜调参：同一时间点出「原帧 / 过完滤镜链的帧」两张 PNG，秒级
STAGES = ("probe", "enhance", "env", "metric", "frame", "preview")


class VideoEnhanceWorker(QThread):
    """按 stage 后台执行探测 / 增强 / 环境检测 / 评测 / 抽帧。

    Signals:
        line(str): 终端逐行输出
        probed(int, object): 探测结果，(行号, info dict)
        progress(int, int, float): (行号, 百分比 0~100, 速度倍数)
        item_done(int, str, str): (行号, 源, 输出)；输出为空串表示「跳过」
        item_failed(int, str): (行号, 失败原因)
        env_ready(object): 环境检测结果 dict
        metric_ready(object): 评分结果 dict
        frames_ready(object): 抽帧结果 dict
        finished_ok(object): 本 stage 的汇总 dict，所有 stage 都会发一次，
            UI 只需连这一个信号就能统一解锁按钮
        cancelled(): 用户取消，半成品输出已删除
        error(str): 全局失败原因，文案可直接展示
    """

    line = Signal(str)
    probed = Signal(int, object)
    progress = Signal(int, int, float)
    item_done = Signal(int, str, str)
    item_failed = Signal(int, str)
    env_ready = Signal(object)
    metric_ready = Signal(object)
    frames_ready = Signal(object)
    finished_ok = Signal(object)
    cancelled = Signal()
    error = Signal(str)

    def __init__(self, stage, params, parent=None):
        super().__init__(parent)
        self.stage = stage
        self.params = dict(params or {})

    def run(self):
        if self.stage not in STAGES:
            self.error.emit(f"未知任务类型: {self.stage}")
            return
        try:
            {"probe": self._probe, "enhance": self._enhance, "env": self._env,
             "metric": self._metric, "frame": self._frame,
             "preview": self._preview}[self.stage]()
        except ve.EnhanceCancelled:
            self.cancelled.emit()
        except ve.EnhanceError as e:
            self.error.emit(str(e))
        except Exception as e:
            self.error.emit(f"画质提升异常: {e}")
            self.line.emit(traceback.format_exc())

    # ---------- 公共小工具 ----------

    def _stopped(self):
        """取消检查；已请求中断就地抛出，各 stage 的循环里都会调"""
        if self.isInterruptionRequested():
            raise ve.EnhanceCancelled()

    def _ffmpeg(self):
        ff = self.params.get("ffmpeg") or ""
        if not ff:
            raise ve.EnhanceError("ffmpeg 未定位到，请到「环境检测」页填路径或把它加进 PATH")
        return ff

    @staticmethod
    def _remove_partial(dst):
        """删掉半成品输出

        截断的 mp4 比没有文件更糟：播放器会报 moov atom not found，用户会以为
        源片坏了而不是这次增强失败了。
        「覆盖」模式下原文件在 ffmpeg 启动那一刻就已经被 -y 截断，删掉半成品并
        不会额外损失什么；「跳过/加序号」模式下 dst 本来就不存在，同样安全。
        """
        if dst and os.path.isfile(dst):
            try:
                os.remove(dst)
            except OSError:
                pass

    # ---------- 探测 ----------

    def _probe(self):
        """批量 ffprobe，逐个回传（不攒到最后一起发，否则大目录要干等）"""
        fp = self.params.get("ffprobe") or ""
        items = self.params.get("items") or []
        done = 0
        for i, src in enumerate(items):
            self._stopped()
            info = ve.probe(src, fp)
            self.probed.emit(i, info)
            done += 1
            if not info.get("ok"):
                self.line.emit(f"[探测失败] {os.path.basename(src)}: "
                               f"{info.get('reason') or '未知原因'}")
        self.finished_ok.emit({"total": len(items), "probed": done})

    # ---------- 批量增强 ----------

    def _enhance(self):
        p = dict(self.params.get("params") or {})
        ff = self._ffmpeg()
        fp = self.params.get("ffprobe") or ""
        videos_dir = self.params.get("videos_dir") or ""
        items = self.params.get("items") or []
        infos = self.params.get("infos") or []
        if not items:
            raise ve.EnhanceError("任务列表是空的，先点「添加文件」")

        # passlog 必须唯一：两遍编码时两个任务用同一个前缀会互相覆盖统计文件，
        # 第二遍读到别人的码率分布，成品体积完全失控。放临时目录而不是输出目录，
        # 免得在用户的录像文件夹里留一堆 .log.mbtree。
        passlog = os.path.join(tempfile.gettempdir(),
                               f"autowork_ve_{uuid.uuid4().hex[:12]}")
        total = len(items)
        ok = skipped = failed = 0
        t0 = time.time()
        self.line.emit(f"开始批量增强：{total} 个文件 · {ve.summarize_params(p)}")
        try:
            for i, src in enumerate(items):
                self._stopped()
                # UI 加文件时已经探过一遍，缓存能用就不重复探（几十条录像能省好几秒）
                info = infos[i] if i < len(infos) else None
                if not (isinstance(info, dict) and info.get("ok")):
                    info = ve.probe(src, fp)
                if not info.get("ok"):
                    failed += 1
                    self.item_failed.emit(i, info.get("reason") or "探测失败")
                    continue

                # 输出路径在循环里现算而不是开头批量算：同名不同目录的两条录像
                # 落到同一个输出目录时，后一条要能看到前一条已经写出来了，
                # 「跳过/加序号」策略才判得对。
                dst = ve.resolve_output_path(src, p, info, videos_dir)
                if dst is None:
                    skipped += 1
                    self.line.emit(f"[{i + 1}/{total}] 跳过（输出已存在）: "
                                   f"{os.path.basename(src)}")
                    self.item_done.emit(i, src, "")
                    continue

                self.line.emit(f"[{i + 1}/{total}] {os.path.basename(src)}")
                try:
                    ve.run_enhance(
                        src, dst, p, ff, fp,
                        on_line=self.line.emit,
                        should_stop=self.isInterruptionRequested,
                        on_progress=lambda pct, speed, idx=i: self.progress.emit(
                            idx, int(round(pct)), float(speed)),
                        info=info, passlogfile=passlog)
                except ve.EnhanceCancelled:
                    self._remove_partial(dst)
                    raise
                except ve.EnhanceError as e:
                    failed += 1
                    self._remove_partial(dst)
                    self.item_failed.emit(i, str(e))
                    continue

                # ffmpeg 最后一次 progress 报告停在 99%（它在写完 moov 之前就不
                # 再报了），这里补一发 100，否则进度条永远差一格
                self.progress.emit(i, 100, 0.0)
                ok += 1
                self.item_done.emit(i, src, dst)
        finally:
            ve.cleanup_passlog(passlog)

        elapsed = time.time() - t0
        summary = {"total": total, "ok": ok, "skipped": skipped, "failed": failed,
                   "elapsed": elapsed}
        self.line.emit(f"批量结束：成功 {ok} · 跳过 {skipped} · 失败 {failed} · "
                       f"用时 {int(elapsed // 60)}m{int(elapsed % 60):02d}s")
        self.finished_ok.emit(summary)

    # ---------- 环境检测 ----------

    def _env(self):
        ff = self.params.get("ffmpeg") or ""
        cap = ve.detect_capabilities(ff)
        result = {
            "ffmpeg": ff, "ffprobe": self.params.get("ffprobe") or "",
            "ok": bool(cap.get("ok")), "reason": cap.get("reason", ""),
            "version": cap.get("version", ""), "build": cap.get("build", ""),
            "has_vmaf": bool(cap.get("has_vmaf")),
            "encoders": sorted(cap.get("encoders") or []),
            "filters": sorted(cap.get("filters") or []),
            "tests": {},
        }
        if not result["ok"]:
            self.line.emit(f"[环境] {result['reason']}")
            self.env_ready.emit(result)
            self.finished_ok.emit(result)
            return

        self.line.emit(f"[环境] ffmpeg {result['version']}")
        self.line.emit(f"[环境] 编码器 {len(result['encoders'])} 个 · "
                       f"滤镜 {len(result['filters'])} 个 · "
                       f"libvmaf {'可用' if result['has_vmaf'] else '不可用'}")
        # 「编译进来」不等于「这台机器上能跑」：*_nvenc 还要显卡和驱动，
        # 所以每个编码器都真编一帧黑场才算数（单次 <200ms）
        for name in (self.params.get("encoders") or []):
            self._stopped()
            if name not in result["encoders"]:
                result["tests"][name] = [False, "这个 ffmpeg 构建里没有编译该编码器"]
            else:
                good, why = ve.test_encoder(ff, name)
                result["tests"][name] = [bool(good), why]
            good, why = result["tests"][name]
            self.line.emit(f"  {'✓' if good else '✗'} {name}"
                           + (f" — {why}" if why else ""))
        self.env_ready.emit(result)
        self.finished_ok.emit(result)

    # ---------- 质量评测 ----------

    def _metric(self):
        src = self.params.get("src") or ""
        dst = self.params.get("dst") or ""
        ff = self._ffmpeg()
        fp = self.params.get("ffprobe") or ""
        kinds = self.params.get("kinds") or ["psnr", "ssim"]
        result = {"src": src, "dst": dst, "scores": {}, "reasons": {}}
        # libvmaf 的 JSON 日志要有落点；放临时目录，跑完连目录一起删
        log_dir = tempfile.mkdtemp(prefix="autowork_ve_metric_")
        try:
            for kind in kinds:
                self._stopped()
                log_path = os.path.join(log_dir, f"{kind}.json") if kind == "vmaf" else None
                self.line.emit(f"[评测] {kind} …（放大过的片子会先把参考源缩到同分辨率）")
                try:
                    score = ve.run_metric(src, dst, kind, ff, fp,
                                          on_line=self.line.emit,
                                          should_stop=self.isInterruptionRequested,
                                          log_path=log_path)
                except ve.EnhanceCancelled:
                    raise
                except ve.EnhanceError as e:
                    result["scores"][kind] = None
                    result["reasons"][kind] = str(e)
                    self.line.emit(f"[评测] {kind} 失败: {e}")
                    continue
                result["scores"][kind] = score
                if score is None:
                    result["reasons"][kind] = "ffmpeg 跑完了但解析不出分数"
                else:
                    self.line.emit(f"[评测] {kind} = {score:.4f}")
        finally:
            shutil.rmtree(log_dir, ignore_errors=True)
        self.metric_ready.emit(result)
        self.finished_ok.emit(result)

    # ---------- 抽帧对比 ----------

    def _frame(self):
        """在同一个时间点上各抽一帧，给「质量对比」页做左右并排"""
        ff = self._ffmpeg()
        t = float(self.params.get("t") or 0.0)
        out_dir = self.params.get("out_dir") or ""
        if not out_dir:
            out_dir = tempfile.mkdtemp(prefix="autowork_ve_cmp_")
        os.makedirs(out_dir, exist_ok=True)
        result = {"t": t, "src_png": "", "dst_png": "", "reason": ""}
        for key, video in (("src_png", self.params.get("src") or ""),
                           ("dst_png", self.params.get("dst") or "")):
            self._stopped()
            if not video:
                result["reason"] = result["reason"] or "缺少源或输出文件路径"
                continue
            png = os.path.join(out_dir, f"cmp_{key[:-4]}.png")
            good, why = ve.extract_frame(video, t, png, ff,
                                         self.isInterruptionRequested)
            if good:
                result[key] = png
            else:
                result["reason"] = result["reason"] or why
                self.line.emit(f"[抽帧] {os.path.basename(video)} 失败: {why}")
        self.frames_ready.emit(result)
        self.finished_ok.emit(result)

    # ---------- 单帧滤镜预览 ----------

    def _preview(self):
        """滤镜调参页的「抽一帧预览」：同一时间点出两张 PNG（原帧 / 过完滤镜链）

        与 _frame 的区别是第二张要过一遍当前的 -vf 链，且只跑一帧，所以是秒级
        而不是分钟级——用户拖动滑块后要能立刻看到效果，否则调参只能靠猜。
        """
        ff = self._ffmpeg()
        src = self.params.get("src") or ""
        if not src:
            raise ve.EnhanceError("预览需要一个源文件，先在「增强任务」里选中一行")
        if not os.path.isfile(src):
            raise ve.EnhanceError(f"源文件不存在：{src}")
        t = float(self.params.get("t") or 0.0)
        out_dir = self.params.get("out_dir") or ""
        if not out_dir:
            out_dir = tempfile.mkdtemp(prefix="autowork_ve_prev_")
        os.makedirs(out_dir, exist_ok=True)
        p = dict(self.params.get("params") or {})
        info = self.params.get("info")
        result = {"t": t, "src_png": "", "dst_png": "", "reason": ""}
        raw = os.path.join(out_dir, "prev_src.png")
        good, why = ve.extract_frame(src, t, raw, ff, self.isInterruptionRequested)
        if good:
            result["src_png"] = raw
        else:
            result["reason"] = why
            self.line.emit(f"[预览] 抽原帧失败: {why}")
        self._stopped()
        out = os.path.join(out_dir, "prev_dst.png")
        good, why = ve.extract_preview_frame(src, t, out, p, ff, info)
        if good:
            result["dst_png"] = out
        else:
            result["reason"] = result["reason"] or why
            self.line.emit(f"[预览] 过滤镜链失败: {why}")
        self.frames_ready.emit(result)
        self.finished_ok.emit(result)
