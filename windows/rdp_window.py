# -*- coding: utf-8 -*-
"""远程桌面窗口（嵌入系统 mstsc.exe 窗口到应用内）

核心设计（v2 - 持续看门狗架构）：
- Windows 11 24H2+ 的 mstsc 可能将会话委托给另一个子进程，
  导致按启动 PID 无法找到真正的会话窗口；
- mstsc 在连接过程中会多次销毁/重建窗口；
- 跨进程 SetParent 需要先 AttachThreadInput 附加线程输入队列。

因此采用"持续看门狗"模式：每 800ms 检查一次——
1. 已嵌入窗口仍有效 → 无事发生
2. 已嵌入窗口失效 / 尚未嵌入 → 查找新窗口（PID 优先，类名兜底）→ 嵌入
3. 进程退出且连续多轮找不到任何 RDP 窗口 → 判定会话结束
"""

import sys
import time
import ctypes
import subprocess

from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget
from PySide6.QtCore import QTimer, Qt
from qfluentwidgets import CaptionLabel

from core.conn_logger import conn_logger

if sys.platform == 'win32':
    from win_api.windows_api import (
        _user32, _k32, _SetParent_err,
        find_rdp_window_by_pid, find_rdp_session_window, find_mstsc_pids,
        get_window_class_name, GWL_STYLE, WS_CAPTION, WS_THICKFRAME,
    )


class RDPWindow(QDialog):
    """远程桌面窗口（嵌入系统 mstsc.exe 窗口到应用内）

    实现思路：
    1. 通过 cmdkey 静默注册 RDP 凭据（免密码弹窗）
    2. 启动 mstsc.exe /v:host:port
    3. 持续看门狗轮询：查找 mstsc 窗口（PID + 类名双通道）
    4. AttachThreadInput + SetParent 嵌入容器控件
    5. 去除标题栏样式、同步窗口尺寸、进程退出检测
    """

    # 进程退出后，连续多少轮找不到窗口才判定会话结束（每轮 800ms）
    _DEAD_ROUNDS = 8

    def __init__(self, host, port, username, password, server_name='', log_callback=None, parent=None):
        super().__init__(parent)
        title = f"远程桌面 - {server_name} ({host}:{port})" if server_name else f"远程桌面 - {host}:{port}"
        self.setWindowTitle(title)
        self.resize(1280, 800)
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._log = log_callback or (lambda msg: None)
        self._mstsc_proc = None
        self._embedded_hwnd = None
        self._watch_timer = None
        self._dead_count = 0          # 连续"无窗口"轮次
        self._cred_removed = False
        self._session_ended = False
        self._init_ui()
        QTimer.singleShot(100, self._start_rdp)

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

    # ------------------------------------------------------------------ 连接流程
    def _start_rdp(self):
        """注册凭据并启动 mstsc.exe，然后启动看门狗"""
        # 1. cmdkey 静默注册凭据（mstsc 自动登录，不弹密码框）
        try:
            subprocess.run(
                ['cmdkey', f'/generic:TERMSRV/{self._host}',
                 f'/user:{self._username}', f'/pass:{self._password}'],
                creationflags=0x08000000, timeout=5,
                capture_output=True
            )
        except Exception as e:
            self._log(f"[RDP] 注册凭据失败: {e}")
        # 2. 启动 mstsc
        try:
            self._mstsc_proc = subprocess.Popen(['mstsc', f'/v:{self._host}:{self._port}'])
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

    # ------------------------------------------------------------------ 看门狗
    def _watchdog(self):
        """持续监控：保持嵌入状态，窗口丢失时自动重新查找并嵌入"""
        if self._session_ended:
            return
        try:
            # --- 情况1：已有嵌入窗口且有效 → 正常，重置计数
            if self._embedded_hwnd:
                if _user32.IsWindow(self._embedded_hwnd):
                    self._dead_count = 0
                    return
                # 嵌入窗口已失效（mstsc 重建了窗口）
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
        """多通道查找 mstsc 会话窗口（评分制，带诊断日志）。
    
        查找策略（逐级兑底）：
        1. 按启动进程 PID 查找（评分制：白名单类名 > 大窗口 > 辅助窗口）
        2. 按所有 mstsc.exe 进程 PID 查找（覆盖 Win11 进程委托场景）
        3. 全局查找（最后兑底）
        每轮查找均输出诊断日志，便于确认实际会话窗口类名。
        """
        # 通道1：按启动进程 PID 查找
        if self._mstsc_proc is not None and self._mstsc_proc.poll() is None:
            hwnd = find_rdp_window_by_pid(self._mstsc_proc.pid, log=self._log)
            if hwnd:
                return hwnd
        # 通道2：查找所有 mstsc.exe 进程（Win11 24H2+ 可能委托给子进程）
        for pid in find_mstsc_pids():
            if self._mstsc_proc is not None and pid == self._mstsc_proc.pid:
                continue  # 已在通道1查过
            hwnd = find_rdp_window_by_pid(pid, log=self._log)
            if hwnd:
                return hwnd
        # 通道3：全局兑底
        return find_rdp_session_window(log=self._log)

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
        """会话结束：停止看门狗，更新状态"""
        self._session_ended = True
        if self._watch_timer is not None:
            self._watch_timer.stop()
        self._embedded_hwnd = None
        self._status_label.setText(message)
        self._log(f'[RDP] {message}')

    # ------------------------------------------------------------------ 尺寸同步
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_embedded()

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
        """删除 cmdkey 注册的临时 RDP 凭据"""
        try:
            subprocess.run(['cmdkey', f'/delete:TERMSRV/{self._host}'],
                           creationflags=0x08000000,
                           timeout=5, capture_output=True)
        except Exception:
            pass

    def closeEvent(self, event):
        try:
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
        super().closeEvent(event)
