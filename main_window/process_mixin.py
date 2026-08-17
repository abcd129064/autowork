# -*- coding: utf-8 -*-
"""MainWindow 进程管理 Mixin：QProcess、三端启动、detect.json/cfg.json、暂停/恢复"""

import os
import sys
import json
import ctypes

from PySide6.QtCore import QProcess, QTimer, Slot
from qfluentwidgets import setCustomStyleSheet, isDarkTheme

from core.app_paths import get_app_dir
from workers.collect_worker import FileCopyWorker

if sys.platform == 'win32':
    from win_api.windows_api import (
        DEVMODE, _EnumDisplaySettingsW, _ChangeDisplaySettingsW,
        ENUM_CURRENT_SETTINGS, CDS_UPDATEREGISTRY, CDS_FULLSCREEN,
        DISP_CHANGE_SUCCESSFUL, DM_BITSPERPEL, DM_PELSWIDTH, DM_PELSHEIGHT,
        DM_DISPLAYFREQUENCY, win_suspend_process, win_resume_process,
    )


class ProcessMixin:
    """进程管理相关方法"""

    # “结束”状态红底样式（播放中）
    _END_BTN_QSS_DARK = (
        "QPushButton#start { background-color: #d13438; color: white; }"
        "QPushButton#start:pressed { background-color: #a1282b; }"
        "QPushButton#start:hover { background-color: #e04b4e; }"
    )
    _END_BTN_QSS_LIGHT = (
        "QPushButton#start { background-color: #c42b1c; color: white; }"
        "QPushButton#start:pressed { background-color: #9a2115; }"
        "QPushButton#start:hover { background-color: #d63c2e; }"
    )

    def _update_play_btn(self, running):
        """更新播放按钮状态：运行中显示红底“结束”，空闲显示默认“播放”"""
        if running:
            self.ui.start.setText("结束")
            qss = self._END_BTN_QSS_DARK if isDarkTheme() else self._END_BTN_QSS_LIGHT
            setCustomStyleSheet(self.ui.start, qss, qss)
        else:
            self.ui.start.setText("播放")
            setCustomStyleSheet(self.ui.start, "", "")

    @Slot()
    def on_start_clicked(self):
        """播放/结束切换按钮 - 类似启动三端的切换逻辑"""
        if self.running_process is not None:
            self.on_end_clicked()
        else:
            self._start_program()

    def _start_program(self):
        """启动 SnookerTracking 程序"""
        exe_name = self.ui.choose_exe.currentText()
        if not exe_name:
            self._show_info_bar("请先选择程序！", "warning")
            return
        exe_dir = self.exe_dir
        exe_path = os.path.join(exe_dir, exe_name)
        if not os.path.exists(exe_path):
            self._show_info_bar(f"程序不存在: {exe_path}", "warning")
            return
        self.running_process = QProcess()
        self.running_process.setWorkingDirectory(exe_dir)
        self.running_process.readyReadStandardOutput.connect(self._on_program_output)
        self.running_process.readyReadStandardError.connect(self._on_program_error)
        self.running_process.finished.connect(self._on_program_finished)
        self._pending_exe_path = exe_path
        self._update_play_btn(True)
        need_decode = self._prepare_detect_json()
        if need_decode == 'decode':
            self._append_log(f"\n[播放] 等待 detect.json 解码完成后启动...")
            self._update_status_running(exe_name)
        elif need_decode == 'copy':
            self._append_log(f"\n[播放] 等待 detect.json 复制完成后启动...")
            self._update_status_running(exe_name)
        else:
            self._launch_program(exe_path, exe_name, exe_dir)

    def _on_program_output(self):
        """识别端 stdout → 日志区（gb2312 解码容错）"""
        if self.running_process:
            output = self.running_process.readAllStandardOutput().data().decode('gb2312', errors='ignore')
            if output.strip():
                self._append_log(output.strip())

    def _on_program_error(self):
        """识别端 stderr → 日志区（带 [程序错误] 前缀）"""
        if self.running_process:
            error = self.running_process.readAllStandardError().data().decode('gb2312', errors='ignore')
            if error.strip():
                self._append_log(f"[程序错误] {error.strip()}")

    def _on_program_finished(self, exit_code, exit_status):
        """程序自然退出：复位暂停态/按钮/状态栏"""
        self._append_log(f"\n[程序结束] 退出码: {exit_code}")
        self.running_process = None
        self._process_suspended = False
        self.ui.pause_btn.setText("暂停")
        self._update_play_btn(False)
        self._update_status_idle()

    @Slot()
    def on_end_clicked(self):
        """结束正在运行的程序"""
        if self.running_process is None:
            self._append_log("\n[提示] 没有正在运行的程序")
            self._show_info_bar("没有正在运行的程序", "warning")
            return
        self._append_log("\n[结束] 正在终止程序...")
        self.running_process.kill()
        self._append_log("[结束] 程序已强制终止")
        self._show_info_bar("程序已终止", "info")
        self.running_process = None
        self._process_suspended = False
        self.ui.pause_btn.setText("暂停")
        self._update_play_btn(False)
        self._update_status_idle()

    # ==================== 三端启动 ====================

    @Slot()
    def on_start_three_clicked(self):
        """启动三端/关闭三端 切换入口"""
        if self._three_running:
            self._stop_three_programs()
        else:
            self._start_three_programs()

    def _start_three_programs(self):
        """依次启动识别端/后端/前端（间隔 3 秒），启动前捕获分辨率供关闭时恢复"""
        exe_name = self.ui.choose_exe.currentText()
        if not exe_name:
            self._show_info_bar('请先在工具栏“程序”下拉框中选择识别端程序！', "warning")
            return
        tracking_path = os.path.join(self.exe_dir, exe_name)
        programs = [
            ("识别端", tracking_path),
            ("后端", self.backend_exe),
            ("前端", self.front_exe),
        ]
        missing = [(name, path) for name, path in programs if not os.path.exists(path)]
        if missing:
            detail = "\n".join(f"  • {name}: {path}" for name, path in missing)
            self._append_log(f"[警告] 以下程序路径不存在：\n{detail}")
            self._show_info_bar("部分程序路径不存在，无法启动", "warning")
            return
        self._three_saved_mode = self._capture_current_resolution()
        if self._three_saved_mode:
            _, (w, h, freq, bits) = self._three_saved_mode
            self._append_log(f"[启动三端] 已捕获当前分辨率: {w}x{h} @ {freq}Hz, {bits}bit（关闭时将自动恢复）")
        self._set_skip_ready_check(False)
        self._three_running = True
        self.ui.start_three_btn.setText("关闭三端")
        interval_ms = 3000
        attr_names = ["_tracking_process", "_backend_process", "_front_process"]
        self._append_log("\n[启动三端] 将依次启动（每个间隔 3 秒）：")
        for i, ((name, path), attr) in enumerate(zip(programs, attr_names)):
            delay = i * interval_ms
            QTimer.singleShot(delay, lambda checked=False, n=name, p=path, a=attr:
                              self._start_one_program(n, p, a))
            self._append_log(f"  {i + 1}. {name}: {path}（{delay // 1000} 秒后启动）")
        self._show_info_bar("三端程序将依次启动（间隔 3 秒）", "success")

    def _stop_three_programs(self):
        """逐个结束三端进程，恢复启动前分辨率并还原 skip_ready_check"""
        self._three_running = False
        self.ui.start_three_btn.setText("启动三端")
        self._append_log("\n[关闭三端] 正在结束三端程序...")
        for attr, name in [("_tracking_process", "识别端"),
                           ("_backend_process", "后端"),
                           ("_front_process", "前端")]:
            process = getattr(self, attr, None)
            if process is not None:
                if process.state() != QProcess.NotRunning:
                    process.kill()
                    process.waitForFinished(1000)
                    self._append_log(f"  已结束 {name}")
                setattr(self, attr, None)
        self._set_skip_ready_check(True)
        saved = self._three_saved_mode
        self._three_saved_mode = None
        if saved:
            _, (w, h, freq, bits) = saved
            QTimer.singleShot(500, lambda checked=False, m=saved: self._restore_resolution(m))
            self._append_log(f"[关闭三端] 0.5 秒后恢复分辨率为 {w}x{h} @ {freq}Hz")
        self._show_info_bar("三端程序已关闭", "success")

    def _start_one_program(self, name, path, attr_name):
        """启动单个三端进程（已取消则跳过），进程引用挂到对应属性"""
        if not self._three_running:
            return
        process = QProcess(self)
        process.setWorkingDirectory(os.path.dirname(path))
        process.finished.connect(
            lambda exit_code, exit_status, n=name, a=attr_name:
            self._on_three_process_finished(n, a, exit_code, exit_status))
        process.start(path)
        setattr(self, attr_name, process)
        self._append_log(f"[启动三端] 已启动 {name}: {path}")

    def _on_three_process_finished(self, name, attr_name, exit_code, exit_status):
        """三端进程退出回调：主动关闭时静默处理，异常退出时记录日志并更新按钮状态"""
        if not self._three_running:
            return  # 用户主动关闭，_stop_three_programs 已处理
        # 仍在“运行中”状态 → 属于意外退出
        status_text = "正常退出" if exit_status == QProcess.NormalExit else "异常崩溃"
        self._append_log(f"[警告] {name} 已退出（{status_text}，退出码: {exit_code}）")
        if getattr(self, attr_name, None) is self.sender():
            setattr(self, attr_name, None)
        self._show_info_bar(f"{name} 已退出（退出码: {exit_code}）", "warning", duration=4000)
        # 三端全部退出 → 复位按钮状态并恢复分辨率
        if all(getattr(self, a, None) is None
               for a in ("_tracking_process", "_backend_process", "_front_process")):
            self._three_running = False
            self.ui.start_three_btn.setText("启动三端")
            self._append_log("[启动三端] 三端程序已全部退出，按钮已复位")
            self._set_skip_ready_check(True)
            saved = self._three_saved_mode
            self._three_saved_mode = None
            if saved:
                QTimer.singleShot(500, lambda checked=False, m=saved: self._restore_resolution(m))

    # ==================== 分辨率捕获/恢复 ====================

    def _capture_current_resolution(self):
        """捕获当前显示器分辨率快照，用于三端关闭后自动恢复。

        返回 (snapshot, (w, h, freq, bits)) 元组；失败返回 None。
        snapshot 是 DEVMODE 的原始内存拷贝，恢复时直接回写以保证字段精确一致。"""
        if sys.platform != 'win32':
            return None
        try:
            dm = DEVMODE()
            dm.dmSize = ctypes.sizeof(DEVMODE)
            if _EnumDisplaySettingsW(None, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)) == 0:
                self._append_log("[分辨率] 读取当前显示模式失败")
                return None
            snapshot = ctypes.string_at(ctypes.addressof(dm), ctypes.sizeof(DEVMODE))
            info = (dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency, dm.dmBitsPerPel)
            return (snapshot, info)
        except Exception as e:
            self._append_log(f"[分辨率] 捕获失败: {e}")
            return None

    def _restore_resolution(self, mode):
        """将显示器分辨率恢复到 _capture_current_resolution 捕获的状态。

        优先使用原始 snapshot 精确恢复；若失败则枚举支持模式寻找最佳匹配；
        最终回退到仅设置宽高 + 系统默认刷新率。"""
        if sys.platform != 'win32' or not mode:
            return
        try:
            snapshot, (width, height, freq, bits) = mode
            dm = DEVMODE()
            ctypes.memmove(ctypes.addressof(dm), snapshot, ctypes.sizeof(DEVMODE))
            dm.dmSize = ctypes.sizeof(DEVMODE)
            ret = _ChangeDisplaySettingsW(ctypes.byref(dm), CDS_UPDATEREGISTRY | CDS_FULLSCREEN)
            if ret == DISP_CHANGE_SUCCESSFUL:
                self._append_log(f"[分辨率] 已恢复为 {width}x{height} @ {freq}Hz")
                return
            self._append_log(f"[分辨率] 完整模式恢复返回 {ret}，尝试枚举支持模式寻找最佳匹配...")
            best = self._find_best_mode(width, height, bits, freq)
            if best is not None:
                best.dmSize = ctypes.sizeof(DEVMODE)
                ret = _ChangeDisplaySettingsW(ctypes.byref(best), CDS_UPDATEREGISTRY | CDS_FULLSCREEN)
                if ret == DISP_CHANGE_SUCCESSFUL:
                    self._append_log(f"[分辨率] 已恢复为 {best.dmPelsWidth}x{best.dmPelsHeight} "
                                     f"@ {best.dmDisplayFrequency}Hz（最佳匹配）")
                    return
                self._append_log(f"[分辨率] 最佳匹配模式恢复返回 {ret}")
            dm2 = DEVMODE()
            dm2.dmSize = ctypes.sizeof(DEVMODE)
            dm2.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_BITSPERPEL
            dm2.dmPelsWidth = width
            dm2.dmPelsHeight = height
            dm2.dmBitsPerPel = bits
            ret = _ChangeDisplaySettingsW(ctypes.byref(dm2), CDS_UPDATEREGISTRY | CDS_FULLSCREEN)
            if ret == DISP_CHANGE_SUCCESSFUL:
                self._append_log(f"[分辨率] 已恢复为 {width}x{height}（刷新率为驱动默认）")
                return
            self._append_log(f"[分辨率] 恢复返回 {ret}，尝试恢复系统默认模式...")
            _ChangeDisplaySettingsW(None, CDS_UPDATEREGISTRY | CDS_FULLSCREEN)
        except Exception as e:
            self._append_log(f"[分辨率] 恢复失败: {e}")

    def _find_best_mode(self, width, height, bits, freq):
        """在显示器支持的模式中，查找与目标宽高率/色深最接近的显示模式。

        按刷新率差值最小、刷新率最高优先排序；返回 DEVMODE 或 None。"""
        try:
            best = None
            best_score = None
            i = 0
            while True:
                dm = DEVMODE()
                dm.dmSize = ctypes.sizeof(DEVMODE)
                if _EnumDisplaySettingsW(None, i, ctypes.byref(dm)) == 0:
                    break
                i += 1
                if dm.dmPelsWidth != width or dm.dmPelsHeight != height:
                    continue
                if bits and dm.dmBitsPerPel != bits:
                    continue
                score = (abs(dm.dmDisplayFrequency - freq), -dm.dmDisplayFrequency)
                if best_score is None or score < best_score:
                    best_score = score
                    best = dm
            return best
        except Exception as e:
            self._append_log(f"[分辨率] 枚举显示模式失败: {e}")
            return None

    def _set_skip_ready_check(self, value):
        """改写识别端 cfg.json 的 sys.skip_ready_check（三端启动前置位）"""
        cfg_path = os.path.join(self.exe_dir, "cfg.json")
        if not os.path.exists(cfg_path):
            self._append_log(f"[警告] cfg.json 不存在: {cfg_path}")
            return
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            cfg.setdefault("sys", {})["skip_ready_check"] = value
            with open(cfg_path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            self._append_log(f"[配置] cfg.json skip_ready_check 已设为 {str(value).lower()}")
        except Exception as e:
            self._append_log(f"[错误] 修改 skip_ready_check 失败: {e}")

    # ==================== detect.json / cfg.json ====================

    def _prepare_detect_json(self):
        """准备 detect.json：根据文件存在情况决定是否从 detect.bin 异步解码。

        解码触发条件：
        - detect.json 不存在且 detect.bin 存在 → 立即解码
        - detect.bin 修改时间晚于 detect.json → 重新解码
        跳过条件：
        - detect.json 已存在且不比 detect.bin 旧 → 直接复制到程序目录
        - 未选中设备 → 跳过
        返回 True 表示正在异步解码（需等待完成后再启动程序），False 表示可立即启动。"""
        if not self.ui.id_list.currentItem():
            self._append_log("[detect] 未选中设备，跳过 detect.json 处理")
            return False
        device_code = self.ui.id_list.currentItem().text()
        device_dir = os.path.join(self.videos_dir, device_code)
        detect_json_path = os.path.join(device_dir, "detect.json")
        detect_bin_path = os.path.join(device_dir, "detect.bin")
        json_exists = os.path.exists(detect_json_path)
        bin_exists = os.path.exists(detect_bin_path)
        need_decode = False
        if not json_exists:
            if not bin_exists:
                self._append_log(f"[detect] 警告: {device_code} 下既没有 detect.json 也没有 detect.bin")
                return False
            self._append_log("[detect] detect.json 不存在，将从 detect.bin 解码")
            need_decode = True
        elif bin_exists:
            bin_mtime = os.path.getmtime(detect_bin_path)
            json_mtime = os.path.getmtime(detect_json_path)
            if bin_mtime > json_mtime:
                self._append_log("[detect] detect.bin 比 detect.json 更新，重新解码")
                need_decode = True
            else:
                self._append_log("[detect] detect.json 已是最新，无需重新解码")
        if need_decode:
            cipher_tool = self.cipher_tool
            if not os.path.exists(cipher_tool):
                self._append_log(f"[detect] 警告: 解码工具不存在: {cipher_tool}")
                return False
            self._pending_detect_json = detect_json_path
            cmd = [cipher_tool, detect_bin_path, detect_json_path]
            self._append_log(f"[detect] 正在异步解码: {' '.join(cmd)}")
            self._decode_process = QProcess()
            self._decode_process.readyReadStandardOutput.connect(self._on_decode_output)
            self._decode_process.readyReadStandardError.connect(self._on_decode_error)
            self._decode_process.finished.connect(self._on_decode_finished)
            self._decode_process.start(cmd[0], cmd[1:])
            return 'decode'
        target_path = os.path.join(self.exe_dir, "detect.json")
        # exe_path 已由调用方 _start_program 写入 self._pending_exe_path
        worker = FileCopyWorker(detect_json_path, target_path)
        worker.copy_finished.connect(self._on_detect_copy_finished)
        worker.error.connect(lambda err: self._on_detect_copy_error(err))
        self._file_copy_worker = worker
        worker.start()
        return 'copy'

    def _on_decode_output(self):
        """解码工具 stdout → 日志区"""
        if self._decode_process:
            output = self._decode_process.readAllStandardOutput().data().decode('gb2312', errors='ignore')
            if output.strip():
                self._append_log(f"[detect] {output.strip()}")

    def _on_decode_error(self):
        """解码工具 stderr → 日志区"""
        if self._decode_process:
            error = self._decode_process.readAllStandardError().data().decode('gb2312', errors='ignore')
            if error.strip():
                self._append_log(f"[detect] {error.strip()}")

    def _on_detect_copy_finished(self):
        """detect.json 异步复制完成回调：启动程序"""
        self._file_copy_worker = None
        target_path = os.path.join(self.exe_dir, "detect.json")
        self._append_log(f"[detect] 已更新 detect.json -> {target_path}")
        self._do_launch_after_detect()

    def _on_detect_copy_error(self, err):
        """detect.json 异步复制失败回调"""
        self._file_copy_worker = None
        self._append_log(f"[detect] 复制失败: {err}")
        self._do_launch_after_detect()

    def _on_decode_finished(self, exit_code, exit_status):
        """解码完成：失败复位待启动状态；成功则把 detect.json 复制到程序目录后启动"""
        self._decode_process = None
        detect_json_path = self._pending_detect_json
        if exit_code != 0:
            self._append_log(f"[detect] 解码失败，退出码: {exit_code}")
            self._pending_exe_path = None
            self._pending_detect_json = None
            self._update_status_idle()
            return
        if not os.path.exists(detect_json_path):
            self._append_log(f"[detect] 警告: 解码后未生成 detect.json")
            self._pending_exe_path = None
            self._pending_detect_json = None
            self._update_status_idle()
            return
        target_path = os.path.join(self.exe_dir, "detect.json")
        worker = FileCopyWorker(detect_json_path, target_path)
        worker.copy_finished.connect(self._on_detect_copy_finished)
        worker.error.connect(lambda err: self._on_detect_copy_error(err))
        self._file_copy_worker = worker
        worker.start()

    def _do_launch_after_detect(self):
        """detect.json 就绪后启动程序（解码/复制路径共用）"""
        exe_path = self._pending_exe_path
        self._pending_exe_path = None
        self._pending_detect_json = None
        if not exe_path:
            return
        exe_name = os.path.basename(exe_path)
        exe_dir = os.path.dirname(exe_path)
        self._launch_program(exe_path, exe_name, exe_dir)

    def _launch_program(self, exe_path, exe_name, exe_dir):
        """启动识别端程序。

        启动前将当前视频路径和起始帧写入 cfg.json，
        然后通过 QProcess.start 启动 exe 并更新 UI 状态。"""
        if self.current_video and self.current_frame is not None:
            video_start_frame = self._compute_video_start_frame(self.current_frame)
            self._update_cfg_json(self.current_video, video_start_frame)
        self.running_process.start(exe_path)
        self._append_log(f"\n[播放] 已启动程序: {exe_name}")
        self._append_log(f"  - 工作目录: {exe_dir}")
        self._show_info_bar(f"已启动程序: {exe_name}", "success")
        self._update_status_running(exe_name)

    def _update_cfg_json(self, video_path, frame):
        """更新程序目录下的 cfg.json，写入当前视频路径和起始帧。

        将 cap.file.path 设为 video_path、cap.file.video_start_frame 设为 frame，
        同时清理顶层残留的 path / video_start_frame 旧字段。"""
        cfg_path = os.path.join(self.exe_dir, "cfg.json")
        if not os.path.exists(cfg_path):
            self._append_log(f"[警告] cfg.json 不存在: {cfg_path}")
            return False
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            if 'cap' in cfg and 'file' in cfg['cap']:
                cfg['cap']['file']['path'] = video_path
                cfg['cap']['file']['video_start_frame'] = frame
            if 'path' in cfg:
                del cfg['path']
            if 'video_start_frame' in cfg:
                del cfg['video_start_frame']
            with open(cfg_path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            self._append_log(f"[配置] 已更新 cfg.json")
            self._append_log(f"  - 视频: {video_path}")
            self._append_log(f"  - 帧数: {frame}")
            return True
        except Exception as e:
            self._append_log(f"[错误] 更新 cfg.json 失败: {str(e)}")
            import traceback
            self._append_log(traceback.format_exc())
            return False

    # ==================== 暂停/恢复 ====================

    @Slot()
    def _on_pause_clicked(self):
        """暂停/恢复按钮：切换识别端进程挂起态"""
        self._toggle_process_suspend()

    def _toggle_process_suspend(self):
        """切换识别端进程的挂起/恢复状态。

        通过 Windows API SuspendThread/ResumeThread 对进程所有线程
        执行挂起或恢复操作，并同步更新暂停按钮文字和状态栏。"""
        if self.running_process is None:
            return
        state = self.running_process.state()
        if state != QProcess.Running:
            return
        pid = int(self.running_process.processId())
        if self._process_suspended:
            if win_resume_process(pid):
                self._process_suspended = False
                self.ui.pause_btn.setText("暂停")
                self._append_log("[播放] 程序已恢复")
                exe_name = self.ui.choose_exe.currentText()
                self._update_status_running(exe_name)
            else:
                self._append_log("[警告] 恢复进程失败")
        else:
            if win_suspend_process(pid):
                self._process_suspended = True
                self.ui.pause_btn.setText("恢复")
                self._append_log("[播放] 程序已暂停")
                self._show_info_bar("程序已暂停")
                exe_name = self.ui.choose_exe.currentText()
                self._update_status_paused(exe_name)
            else:
                self._append_log("[警告] 暂停进程失败")
