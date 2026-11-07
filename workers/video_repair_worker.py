# -*- coding: utf-8 -*-
"""视频修复 Worker（工具页「视频修复」工作区）

体检与修复都必须离开 UI 线程：mmap 扫起始码在几百 MB 的录像上是秒级操作，放在
主线程会把整个 FluentWindow 冻住，连左侧导航都点不动。

后端逻辑全在 windows.tools.video_repair（纯文件与子进程操作，不 import Qt），
本文件只做「参数进 → 信号出」以及取消与异常的收口。
"""
import os
import traceback

from PySide6.QtCore import QThread, Signal

# 体检结果里要带出去的帧统计字段（几千个偏移量本身不回传，界面用不上）
_FRAME_KEYS = ("frame_count", "gop_count", "median_frame", "last_vop_tail",
               "last_frame_incomplete", "is_h264")


class VideoRepairWorker(QThread):
    """后台执行体检或修复的 Worker。

    stage:
        ``"probe"``  体检损坏文件，并探测或自动发现参考文件，结果经 probed 回传
        ``"repair"`` 拼裸流 → ffmpeg 流拷贝重封装 → 校验，结果经 finished_ok 回传

    Signals:
        line(str): 终端逐行输出
        probed(object): 体检结果 dict（仅 stage="probe"）
        finished_ok(object): 修复报告 dict（仅 stage="repair"）
        cancelled(): 用户取消，半成品输出已删除
        error(str): 失败原因，文案可直接展示

    注意不要把业务信号命名成 finished——那是 QThread 原生信号，遮蔽过会崩
    （本仓 NewLog/售后 worker 都踩过）。
    """

    line = Signal(str)
    probed = Signal(object)
    finished_ok = Signal(object)
    cancelled = Signal()
    error = Signal(str)

    def __init__(self, stage, params, parent=None):
        super().__init__(parent)
        self.stage = stage
        self.params = dict(params or {})

    def run(self):
        try:
            from windows.tools import video_repair as vr
        except ImportError as e:
            self.error.emit(f"无法加载视频修复模块: {e}")
            return
        try:
            if self.stage == "probe":
                self._probe(vr)
            else:
                self._repair(vr)
        except vr.RepairCancelled:
            self.cancelled.emit()
        except vr.RepairError as e:
            self.error.emit(str(e))
        except Exception as e:
            self.error.emit(f"视频修复异常: {e}")
            self.line.emit(traceback.format_exc())

    # ---------- 体检 ----------

    def _probe(self, vr):
        """体检损坏文件，必要时自动发现参考文件并校验其可用性"""
        path = self.params.get("path") or ""
        broken = vr.probe_broken(path)
        boxes = broken.get("boxes") or {}
        frames = broken.get("frames") or {}
        result = {
            "path": path,
            "ok": bool(broken.get("ok")),
            "reason": broken.get("reason", ""),
            "file_size": boxes.get("file_size", 0),
            "has_moov": boxes.get("has_moov", False),
            "mdat_last": boxes.get("mdat_last", False),
            # 盒结构只回传可读摘要：完整列表在几百 MB 文件上可能有几十项
            "box_list": [f"{t} @0x{p:X} ({s:,} B)"
                         for t, p, s in (boxes.get("boxes") or [])[:12]],
            "payload_off": boxes.get("payload_off", 0),
            "payload_size": boxes.get("payload_size", 0),
            "frames": {k: frames.get(k) for k in _FRAME_KEYS},
            "reference": "", "ref_info": None, "vol_bytes": 0, "discovered": False,
        }
        if self.isInterruptionRequested():
            return

        ref = self.params.get("reference") or ""
        # 用户没指定参考文件时才自动发现：手挑过的选择优先级高于同目录猜测
        if not ref and result["ok"]:
            ref = vr.discover_reference(path) or ""
            result["discovered"] = bool(ref)
        if ref:
            result["reference"] = ref
            try:
                info = vr.probe_reference(ref)
                vol = vr.read_vol(ref)
                result["ref_info"] = info
                result["vol_bytes"] = len(vol or b"")
                if info["codec"] != "mpeg4":
                    result["ok"] = False
                    result["reason"] = (f"参考文件编码是 {info['codec'] or '未知'}，"
                                        f"需要 mpeg4（MPEG-4 Part 2）")
                elif info["has_audio"]:
                    result["ok"] = False
                    result["reason"] = "参考文件含音轨：流拷贝会丢音轨，本工具不处理"
                elif not vol:
                    result["ok"] = False
                    result["reason"] = "参考文件里读不出 VOL 配置头，请换同机录制的正常文件"
            except vr.RepairError as e:
                result["ok"] = False
                result["reason"] = str(e)
        self.probed.emit(result)

    # ---------- 修复 ----------

    def _repair(self, vr):
        """执行修复；取消时删掉半成品输出，不给用户留一个打不开的残缺文件"""
        p = self.params
        out = p.get("out") or ""
        try:
            report = vr.repair(
                p.get("broken") or "", p.get("reference") or "", out,
                fps=float(p.get("fps") or 25),
                cut_mode=p.get("cut_mode") or "auto",
                verify_decode=bool(p.get("verify_decode", True)),
                on_line=self.line.emit,
                should_stop=self.isInterruptionRequested)
        except vr.RepairCancelled:
            if out and os.path.isfile(out):
                try:
                    os.remove(out)
                except OSError:
                    pass
            raise
        self.finished_ok.emit(report)
