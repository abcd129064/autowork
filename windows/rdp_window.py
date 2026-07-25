# -*- coding: utf-8 -*-
"""远程桌面窗口（嵌入系统 mstsc.exe 窗口到应用内）"""

import sys
import time
import subprocess

from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget, QLabel
from PySide6.QtCore import QTimer, Qt

from core.conn_logger import conn_logger

if sys.platform == 'win32':
    from win_api.windows_api import (
        _user32, find_window_by_pid,
        GWL_STYLE, WS_CAPTION, WS_THICKFRAME,
    )


class RDPWindow(QDialog):
    """远程桌面窗口（嵌入系统 mstsc.exe 窗口到应用内）

    实现思路：
    1. 通过 cmdkey 静默注册 RDP 凭据（免密码弹窗）
    2. 启动 mstsc.exe /v:host:port
    3. 轮询查找 mstsc 窗口句柄，SetParent 嵌入容器控件
    4. 去除标题栏样式、同步窗口尺寸、监控进程生命周期
    """

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
        self._find_timer = None
        self._watch_timer = None
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
        # 底部状态栏
        self._status_label = QLabel('正在启动远程桌面...')
        self._status_label.setFixedHeight(24)
        self._status_label.setStyleSheet('padding: 2px 8px;')
        layout.addWidget(self._status_label)

    # ------------------------------------------------------------------ 连接流程
    def _start_rdp(self):
        """注册凭据并启动 mstsc.exe"""
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
        # 3. 轮询查找 mstsc 窗口（15 秒超时）
        self._find_deadline = time.time() + 15
        self._find_timer = QTimer(self)
        self._find_timer.timeout.connect(self._try_find_window)
        self._find_timer.start(300)

    def _try_find_window(self):
        """轮询查找 mstsc 窗口句柄，找到后嵌入"""
        if self._mstsc_proc is None or self._mstsc_proc.poll() is not None:
            self._find_timer.stop()
            self._status_label.setText('远程桌面进程已退出（连接失败或被取消）')
            self._log('[RDP] mstsc 进程在嵌入前已退出')
            return
        hwnd = find_window_by_pid(self._mstsc_proc.pid)
        if hwnd:
            self._find_timer.stop()
            self._embed_window(hwnd)
        elif time.time() > self._find_deadline:
            self._find_timer.stop()
            self._status_label.setText('未找到远程桌面窗口（超时）')
            self._log('[RDP] 查找 mstsc 窗口超时')

    def _embed_window(self, hwnd):
        """将 mstsc 窗口嵌入容器"""
        try:
            if not _user32.IsWindow(hwnd):
                self._status_label.setText('远程桌面窗口已失效')
                return
            self._embedded_hwnd = hwnd
            container_hwnd = int(self._container.winId())
            # 去除标题栏和边框，远程桌面画面填满容器
            style = _user32.GetWindowLongW(hwnd, GWL_STYLE)
            style &= ~(WS_CAPTION | WS_THICKFRAME)
            _user32.SetWindowLongW(hwnd, GWL_STYLE, style)
            # 嵌入
            _user32.SetParent(hwnd, container_hwnd)
            self._resize_embedded()
            self._status_label.setText(f'已连接: {self._host}:{self._port}')
            self._log(f"[RDP] 远程桌面已嵌入: {self._host}:{self._port}")
            # 删除临时凭据（安全：不在凭据库中留存密码）
            self._remove_cred()
            # 监控进程生命周期：用户在远程桌面内断开时更新状态
            self._watch_timer = QTimer(self)
            self._watch_timer.timeout.connect(self._check_alive)
            self._watch_timer.start(2000)
        except Exception as e:
            conn_logger.exception('RDP', '嵌入窗口失败', exc=e,
                                  host=self._host, port=self._port)
            self._embedded_hwnd = None
            self._status_label.setText(f'嵌入窗口失败: {e}')

    def _check_alive(self):
        """检测 mstsc 进程/窗口是否存活（会话断开或连接失败时更新状态）"""
        try:
            if self._mstsc_proc is not None and self._mstsc_proc.poll() is not None:
                self._watch_timer.stop()
                self._embedded_hwnd = None
                self._status_label.setText('远程桌面已断开')
                self._log('[RDP] 远程桌面会话已结束')
            elif self._embedded_hwnd and not _user32.IsWindow(self._embedded_hwnd):
                self._embedded_hwnd = None
                self._status_label.setText('远程桌面连接失败（窗口已关闭）')
                self._log('[RDP] mstsc 窗口已销毁（连接失败或弹出错误提示）')
        except Exception:
            pass

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
            if self._find_timer is not None and self._find_timer.isActive():
                self._find_timer.stop()
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
