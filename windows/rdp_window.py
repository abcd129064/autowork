# -*- coding: utf-8 -*-
"""远程桌面窗口（嵌入系统 mstsc.exe 窗口到应用内）

核心设计（v2 - 持续看门狗架构）：
- Windows 11 24H2+ 的 mstsc 可能将会话委托给另一个子进程，
  导致按启动 PID 无法找到真正的会话窗口；
- mstsc 在连接过程中会多次销毁/重建窗口；
- 跨进程 SetParent 需要先 AttachThreadInput 附加线程输入队列。

采用"持续看门狗"模式：每 800ms 检查一次——
1. 已嵌入窗口仍有效 → 无事发生
2. 已嵌入窗口失效 / 尚未嵌入 → 查找新窗口（PID 优先，类名兜底）→ 嵌入
3. 进程退出且连续多轮找不到任何 RDP 窗口 → 判定会话结束
"""

import sys
import time
import ctypes
import subprocess

from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget
from PySide6.QtCore import QTimer, Qt, QProcess
from PySide6.QtGui import QGuiApplication
from qfluentwidgets import CaptionLabel, BodyLabel, PushButton

from core.conn_logger import conn_logger

if sys.platform == 'win32':
    from win_api.windows_api import (
        _user32, _k32, _SetParent_err,
        find_rdp_window_by_pid, find_rdp_session_window, find_mstsc_pids,
        get_window_class_name, GWL_STYLE, WS_CAPTION, WS_THICKFRAME,
        RDP_SESSION_CLASSES, _enum_windows_of_pid,
    )


class RDPPanel(QWidget):
    """远程桌面面板（嵌入系统 mstsc.exe 窗口到应用内，可嵌入标签页容器）

    实现思路：
    1. 通过 cmdkey 静默注册 RDP 凭据（免密码弹窗）
    2. 启动 mstsc.exe /v:host:port
    3. 持续看门狗轮询：查找 mstsc 窗口（PID + 类名双通道）
    4. AttachThreadInput + SetParent 嵌入容器控件
    5. 去除标题栏样式、同步窗口尺寸、进程退出检测

    资源清理统一由 shutdown() 方法负责。
    """

    # 进程退出后，连续多少轮找不到窗口才判定会话结束（每轮 800ms）
    _DEAD_ROUNDS = 8

    def __init__(self, host, port, username, password, server_name='', log_callback=None, parent=None):
        super().__init__(parent)
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._server_name = server_name
        self._log = log_callback or (lambda msg: None)
        self._mstsc_proc = None
        self._embedded_hwnd = None
        self._watch_timer = None
        self._dead_count = 0          # 连续“无窗口”轮次
        self._cred_removed = False
        self._session_ended = False
        self._closing = False
        # 看门狗窗口查找缓存：PID 未变且句柄仍有效时复用，避免每轮 EnumWindows
        self._cached_hwnd = None
        self._cached_pid = None
        # mstsc 启动控制：必须等容器获得有效尺寸后才启动，
        # 才能把容器尺寸作为远程分辨率传给 mstsc（否则远程会话按
        # 默认分辨率——如 1080p——连接，嵌入后画面被裁剪并出现滚动条）
        self._rdp_started = False
        self._reconnecting = False    # 重连进行中标志（防重复点击/看门狗误报）
        self._init_ui()
        QTimer.singleShot(100, self._try_start_rdp)
        # 兑底：若容器尺寸一直未就绪（如面板未布局），延迟后用
        # 屏幕工作区的 80% 作为默认分辨率启动，避免永远不启动
        QTimer.singleShot(3000, self._fallback_start_rdp)
    
    @property
    def tab_title(self) -> str:
        """返回适合标签页显示的标题"""
        if hasattr(self, '_server_name') and self._server_name:
            return f"RDP - {self._server_name}"
        return f"RDP - {self._host}:{self._port}"

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # 嵌入容器（必须是原生窗口才能接受 SetParent）
        self._container = QWidget()
        self._container.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        layout.addWidget(self._container, 1)
        # 底部状态栏（Fluent CaptionLabel）
        self._status_label = CaptionLabel('正在启动远程桌面...', self)
        self._status_label.setFixedHeight(24)
        self._status_label.setStyleSheet('padding: 2px 8px;')
        layout.addWidget(self._status_label)
        # 会话结束覆盖层（提示文字 + 重新连接按钮），默认隐藏
        self._reconnect_overlay = QWidget(self)
        self._reconnect_overlay.hide()
        ol = QVBoxLayout(self._reconnect_overlay)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(10)
        ol.addStretch(1)
        self._overlay_hint = BodyLabel('远程会话已结束', self._reconnect_overlay)
        self._overlay_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ol.addWidget(self._overlay_hint, 0, Qt.AlignmentFlag.AlignCenter)
        self._reconnect_btn = PushButton('重新连接', self._reconnect_overlay)
        self._reconnect_btn.setFixedSize(140, 36)
        self._reconnect_btn.clicked.connect(self._reconnect)
        ol.addWidget(self._reconnect_btn, 0, Qt.AlignmentFlag.AlignCenter)
        ol.addStretch(1)

    # ------------------------------------------------------------------ 连接流程
    def showEvent(self, event):
        super().showEvent(event)
        self._try_start_rdp()

    def _try_start_rdp(self):
        """容器获得有效尺寸后启动 mstsc（仅执行一次）。

        启动时机过晚/过早都不行：必须在面板已布局、容器宽高已知时启动，
        才能把容器尺寸作为远程分辨率传给 mstsc。
        """
        if self._rdp_started or self._session_ended or self._closing:
            return
        w, h = self._container.width(), self._container.height()
        if w < 200 or h < 200:
            return  # 尺寸无效，等下一次 resizeEvent/showEvent 再试
        self._rdp_started = True
        self._start_rdp(w, h)

    def _fallback_start_rdp(self):
        """兑底启动：容器尺寸迟迟未就绪时，用屏幕工作区 80% 作为分辨率"""
        if self._rdp_started or self._session_ended or self._closing:
            return
        w, h = 1280, 800
        try:
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                w, h = int(area.width() * 0.8), int(area.height() * 0.8)
        except Exception:
            pass
        self._rdp_started = True
        self._log(f'[RDP] 容器尺寸未就绪，使用默认分辨率 {w}x{h} 启动')
        self._start_rdp(w, h)

    def _start_rdp(self, width, height):
        """注册凭据并启动 mstsc.exe，然后启动看门狗

        分辨率通过命令行 /w /h 参数传递（不用 .rdp 文件：
        .rdp 文件启动会被 mstsc 视为不可信发布者，触发
        “远程桌面连接安全警告”弹窗，且绕过 cmdkey 凭据）。
        必须等 cmdkey 注册完成后再启动 mstsc，否则会弹密码框。
        """
        self._pending_size = (width, height)
        # 1. cmdkey 异步注册凭据（mstsc 自动登录，不弹密码框）
        self._run_cmdkey_async(
            [f'/generic:TERMSRV/{self._host}',
             f'/user:{self._username}', f'/pass:{self._password}'],
            callback=self._on_cred_registered
        )

    def _on_cred_registered(self, exit_code, _stdout, stderr):
        """cmdkey 注册完成回调：启动 mstsc 并开启看门狗"""
        if exit_code != 0:
            self._log(f'[RDP] 注册凭据失败 (exit={exit_code}): {stderr.strip()}')
        # 2. 凭据已注册，命令行直连 + /w /h 指定远程分辨率
        w, h = getattr(self, '_pending_size', (1280, 800))
        cmd = ['mstsc', f'/v:{self._host}:{self._port}', f'/w:{w}', f'/h:{h}']
        try:
            self._mstsc_proc = subprocess.Popen(cmd)
        except Exception as e:
            conn_logger.exception('RDP', '启动 mstsc.exe 失败', exc=e,
                                  host=self._host, port=self._port)
            self._status_label.setText(f'启动远程桌面失败: {e}')
            return
        conn_logger.info('RDP', f'已启动 mstsc: {self._host}:{self._port}',
                         host=self._host, port=self._port, user=self._username)
        self._status_label.setText(f'正在连接 {self._host}:{self._port} ...')
        # 3. 启动持续看门狗（永不主动停止，直到会话结束或窗口关闭）
        self._watch_timer = QTimer(self)
        self._watch_timer.timeout.connect(self._watchdog)
        self._watch_timer.start(800)

    def _run_cmdkey_async(self, args, callback=None):
        """异步执行 cmdkey 命令（避免阻塞 GUI 线程）"""
        proc = QProcess(self)
        if callback:
            proc.finished.connect(
                lambda code, status: callback(
                    code,
                    proc.readAllStandardOutput().data().decode(errors='replace'),
                    proc.readAllStandardError().data().decode(errors='replace')))
        proc.start('cmdkey', args)

    # ------------------------------------------------------------------ 看门狗
    def _watchdog(self):
        """持续监控：保持嵌入状态，窗口丢失时自动重新查找并嵌入"""
        if self._session_ended:
            return
        try:
            # --- 情况1：已有嵌入窗口且有效
            if self._embedded_hwnd:
                if _user32.IsWindow(self._embedded_hwnd):
                    # 若嵌入的不是真正的会话容器（如误嵌的过渡窗口），
                    # 尝试查找真正的会话窗口并替换嵌入
                    cls = get_window_class_name(self._embedded_hwnd)
                    if cls not in RDP_SESSION_CLASSES:
                        better = self._find_session_window_strict()
                        if better and better != self._embedded_hwnd:
                            self._log(f'[RDP] 找到真正的会话窗口，'
                                      f'替换误嵌入的 {cls!r} 窗口')
                            self._embedded_hwnd = None
                            self._embed_window(better)
                            return
                    self._dead_count = 0
                    return
                # 嵌入窗口已经失效（mstsc 重建了窗口）
                self._log('[RDP] 嵌入窗口失效，重新查找...')
                self._embedded_hwnd = None
    
            # --- 情况2：无有效嵌入窗口 → 查找并嵌入
            hwnd = self._find_mstsc_window()
            if hwnd:
                self._dead_count = 0
                self._embed_window(hwnd)
            else:
                # 找不到窗口：若进程已退出则累计“死亡轮次”
                proc_dead = (self._mstsc_proc is None or
                             self._mstsc_proc.poll() is not None)
                if proc_dead:
                    self._dead_count += 1
                    if self._dead_count >= self._DEAD_ROUNDS:
                        self._end_session('远程桌面已断开')
                else:
                    # 进程活着但找不到可嵌入窗口 → 输出诊断信息
                    # （帮助定位 Win11 25H2 上的真实会话窗口类名）
                    self._log(f'[RDP] 未找到可嵌入窗口，'
                              f'mstsc 进程: {find_mstsc_pids()}')
        except Exception:
            pass

    def _find_mstsc_window(self):
        """多通道查找 mstsc 会话窗口（带 PID 级缓存 + 评分制）。

        缓存策略：若上次找到的句柄仍有效且对应 PID 未变，直接复用，
        跳过昂贵的 EnumWindows 遍历。缓存失效条件：句柄无效或 PID 变化。

        查找策略（逐级兑底）：
        1. 按启动进程 PID 查找（评分制：白名单类名 > 大窗口 > 辅助窗口）
        2. 按所有 mstsc.exe 进程 PID 查找（覆盖 Win11 进程委托场景）
        3. 全局查找（最后兑底）
        """
        # 缓存检查：句柄有效 + PID 未变 → 直接复用
        if (self._cached_hwnd and self._cached_pid is not None
                and _user32.IsWindow(self._cached_hwnd)):
            current_pid = ctypes.wintypes.DWORD()
            _user32.GetWindowThreadProcessId(self._cached_hwnd, ctypes.byref(current_pid))
            if current_pid.value == self._cached_pid:
                return self._cached_hwnd
            # PID 变了，缓存失效
            self._cached_hwnd = None
            self._cached_pid = None

        # 通道1：按启动进程 PID 查找
        if self._mstsc_proc is not None and self._mstsc_proc.poll() is None:
            hwnd = find_rdp_window_by_pid(self._mstsc_proc.pid, log=self._log)
            if hwnd:
                self._update_cache(hwnd)
                return hwnd
        # 通道2：查找所有 mstsc.exe 进程（Win11 24H2+ 可能委托给子进程）
        for pid in find_mstsc_pids():
            if self._mstsc_proc is not None and pid == self._mstsc_proc.pid:
                continue  # 已在通道1查过
            hwnd = find_rdp_window_by_pid(pid, log=self._log)
            if hwnd:
                self._update_cache(hwnd)
                return hwnd
        # 通道3：全局兑底
        hwnd = find_rdp_session_window(log=self._log)
        if hwnd:
            self._update_cache(hwnd)
        return hwnd

    def _find_session_window_strict(self):
        """严格查找真正的会话窗口（绕过缓存，仅接受白名单类名）。

        用于替换误嵌入的非会话窗口（如对话框）：查找前失效缓存，
        只返回 TscShellContainerClass 等已知会话容器。
        """
        self._cached_hwnd = None
        self._cached_pid = None
        pids = []
        if self._mstsc_proc is not None and self._mstsc_proc.poll() is None:
            pids.append(self._mstsc_proc.pid)
        pids.extend(p for p in find_mstsc_pids() if p not in pids)
        for pid in pids:
            for w in _enum_windows_of_pid(pid):
                if w['class'] in RDP_SESSION_CLASSES:
                    self._update_cache(w['hwnd'])
                    return w['hwnd']
        return None

    def _update_cache(self, hwnd):
        """更新看门狗窗口查找缓存"""
        pid = ctypes.wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        self._cached_hwnd = hwnd
        self._cached_pid = pid.value

    # ------------------------------------------------------------------ 嵌入
    def _embed_window(self, hwnd):
        """将 mstsc 窗口嵌入容器（AttachThreadInput + SetParent）"""
        try:
            if not _user32.IsWindow(hwnd):
                return
            container_hwnd = int(self._container.winId())

            # 去除标题栏和边框，远程桌面画面填满容器
            style = _user32.GetWindowLongW(hwnd, GWL_STYLE)
            style &= ~(WS_CAPTION | WS_THICKFRAME)
            _user32.SetWindowLongW(hwnd, GWL_STYLE, style)

            # 跨进程嵌入关键步骤：附加目标窗口线程的输入队列到当前线程，
            # 否则 SetParent 可能被 Windows 拒绝或嵌入后不渲染
            remote_tid = _user32.GetWindowThreadProcessId(hwnd, None)
            local_tid = _k32.GetCurrentThreadId()
            attached = False
            if remote_tid and remote_tid != local_tid:
                attached = bool(_user32.AttachThreadInput(local_tid, remote_tid, True))

            # SetParent 返回旧父窗口句柄，失败返回 NULL
            old_parent = _SetParent_err(hwnd, container_hwnd)
            err = ctypes.get_last_error()

            if attached:
                # 嵌入完成后解除线程附加（保持窗口独立性）
                _user32.AttachThreadInput(local_tid, remote_tid, False)

            if not old_parent and err:
                self._log(f'[RDP] SetParent 失败 (error={err})，下轮重试')
                return

            self._embedded_hwnd = hwnd
            # 嵌入成功：若处于重连流程，移除“会话结束”覆盖层
            if self._reconnecting:
                self._reconnecting = False
                self._reconnect_overlay.hide()
            self._resize_embedded()
            self._status_label.setText(f'已连接: {self._host}:{self._port}')
            self._log(f"[RDP] 远程桌面已嵌入: {self._host}:{self._port} "
                      f"(class={get_window_class_name(hwnd)})")
            # 删除临时凭据（安全：不在凭据库中留存密码）
            if not self._cred_removed:
                self._remove_cred()
                self._cred_removed = True
        except Exception as e:
            conn_logger.exception('RDP', '嵌入窗口失败', exc=e,
                                  host=self._host, port=self._port)
            self._embedded_hwnd = None

    def _end_session(self, message):
        """会话结束：停止看门狗，更新状态，显示重连覆盖层

        看门狗已停止，不会在重连期间误报；重连时 _start_rdp 会重启看门狗。
        """
        self._session_ended = True
        if self._watch_timer is not None:
            self._watch_timer.stop()
        self._embedded_hwnd = None
        self._status_label.setText(message)
        self._log(f'[RDP] {message}')
        # 关闭窗口路径（shutdown）不显示重连按钮
        if not self._closing:
            self._overlay_hint.setText(message)
            # 覆盖容器区域（若尚未布局过，同步一次几何）
            if self._reconnect_overlay.geometry() != self._container.geometry():
                self._reconnect_overlay.setGeometry(self._container.geometry())
            self._reconnect_overlay.show()
            self._reconnect_overlay.raise_()

    # ------------------------------------------------------------------ 重连
    def _reconnect(self):
        """点击重连：清理旧状态，复用 _start_rdp 完整路径重新建连"""
        if self._closing or self._reconnecting:
            return
        self._reconnecting = True
        self._reconnect_overlay.hide()
        self._session_ended = False
        self._dead_count = 0
        self._embedded_hwnd = None
        # 清理看门狗窗口查找缓存（旧 hwnd / 旧 PID）
        self._cached_hwnd = None
        self._cached_pid = None
        # 清理旧 mstsc 进程引用（正常应已退出，此处兜底终止）
        if self._mstsc_proc is not None:
            try:
                if self._mstsc_proc.poll() is None:
                    self._mstsc_proc.terminate()
                    self._mstsc_proc.wait(timeout=2)
            except Exception:
                pass
            self._mstsc_proc = None
        self._cred_removed = False
        self._status_label.setText('正在重新连接...')
        self._log('[RDP] 用户请求重新连接')
        # 优先复用容器当前尺寸（含原延迟启动逻辑的兜底分辨率）
        w, h = self._container.width(), self._container.height()
        if w < 200 or h < 200:
            w, h = getattr(self, '_pending_size', (0, 0))
        if w < 200 or h < 200:
            w, h = 1280, 800
            try:
                screen = QGuiApplication.primaryScreen()
                if screen is not None:
                    area = screen.availableGeometry()
                    w, h = int(area.width() * 0.8), int(area.height() * 0.8)
            except Exception:
                pass
        self._start_rdp(w, h)

    # ------------------------------------------------------------------ 尺寸同步
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._reconnect_overlay is not None:
            self._reconnect_overlay.setGeometry(self._container.geometry())
        self._resize_embedded()
        # 若因容器尺寸未就绪而尚未启动 mstsc，此处补触发
        self._try_start_rdp()

    def _resize_embedded(self):
        if not self._embedded_hwnd:
            return
        try:
            if not _user32.IsWindow(self._embedded_hwnd):
                self._embedded_hwnd = None
                return
            if self._container.width() > 0 and self._container.height() > 0:
                _user32.MoveWindow(self._embedded_hwnd, 0, 0,
                                   self._container.width(), self._container.height(), True)
        except Exception:
            self._embedded_hwnd = None

    # ------------------------------------------------------------------ 清理
    def _remove_cred(self):
        """异步删除 cmdkey 注册的临时 RDP 凭据"""
        self._run_cmdkey_async([f'/delete:TERMSRV/{self._host}'])

    def shutdown(self):
        """安全关闭：停止看门狗、摘除嵌入窗口、终止 mstsc 进程。

        由容器（标签页关闭）或 QDialog.closeEvent 调用。可重复调用，幂等安全。
        """
        if self._closing:
            return
        self._closing = True
        try:
            self._reconnect_overlay.hide()
            if self._watch_timer is not None and self._watch_timer.isActive():
                self._watch_timer.stop()
            hwnd = self._embedded_hwnd
            self._embedded_hwnd = None
            # 1. 先把嵌入窗口从容器摘除（还原为桌面顶层窗口）
            if hwnd:
                try:
                    if _user32.IsWindow(hwnd):
                        _user32.SetParent(hwnd, None)
                except Exception:
                    pass
            # 2. 终止 mstsc 进程
            if self._mstsc_proc is not None and self._mstsc_proc.poll() is None:
                try:
                    self._mstsc_proc.terminate()
                except Exception:
                    pass
                try:
                    self._mstsc_proc.wait(timeout=3)
                except Exception:
                    try:
                        self._mstsc_proc.kill()
                        self._mstsc_proc.wait(timeout=2)
                    except Exception:
                        pass
            self._mstsc_proc = None
            self._remove_cred()
        except Exception:
            pass


class RDPWindow(QDialog):
    """远程桌面独立窗口（向后兼容的薄壳，内部委托 RDPPanel）"""

    def __init__(self, host, port, username, password, server_name='', log_callback=None, parent=None):
        super().__init__(parent)
        title = f"远程桌面 - {server_name} ({host}:{port})" if server_name else f"远程桌面 - {host}:{port}"
        self.setWindowTitle(title)
        self.resize(1280, 800)
        self._panel = RDPPanel(
            host, port, username, password,
            server_name=server_name,
            log_callback=log_callback, parent=self
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._panel)

    def closeEvent(self, event):
        self._panel.shutdown()
        super().closeEvent(event)
