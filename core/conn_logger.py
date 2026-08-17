# -*- coding: utf-8 -*-
"""SSH/SFTP 连接统一文件日志 + Qt 消息处理器"""

import os
import threading
import traceback
from datetime import datetime

from PySide6.QtCore import QtMsgType

from .app_paths import get_app_dir


class ConnLogger:
    """SSH/SFTP 连接统一文件日志。
    - 每条日志立即 flush 落盘，即使进程崩溃也不丢失关键信息
    - 格式：时间戳 | 级别 | [操作类型] host:port user=xxx | 错误类型 | 详情
    - 安全：禁止记录密码等敏感信息（仅记录用户名）
    - 日志文件：程序目录/logs/autowork_conn.log
    """
    _MAX_BYTES = 2 * 1024 * 1024  # 单文件超过 2MB 时轮转
    _MAX_ARCHIVES = 3               # 最多保留 3 个归档（.1/.2/.3，.1 最新）

    def __init__(self):
        self._lock = threading.Lock()
        self._file = None
        self._path = ''
        try:
            log_dir = os.path.join(get_app_dir(), 'logs')
            os.makedirs(log_dir, exist_ok=True)
            self._path = os.path.join(log_dir, 'autowork_conn.log')
            self._file = open(self._path, 'a', encoding='utf-8')
        except Exception:
            self._file = None  # 日志不可用时静默降级，绝不影响主功能

    def _rotate(self):
        """归档轮转：当前文件改名为 .1，已有归档依次后移（.1→.2→.3），
        超出 _MAX_ARCHIVES 的最老归档删除。任何一步失败都不抛异常。"""
        try:
            self._file.close()
        except Exception:
            pass
        self._file = None
        try:
            oldest = f'{self._path}.{self._MAX_ARCHIVES}'
            if os.path.exists(oldest):
                os.remove(oldest)
            for i in range(self._MAX_ARCHIVES - 1, 0, -1):
                src = f'{self._path}.{i}'
                if os.path.exists(src):
                    os.replace(src, f'{self._path}.{i + 1}')
            if os.path.exists(self._path):
                os.replace(self._path, f'{self._path}.1')
        except Exception:
            pass
        try:
            self._file = open(self._path, 'a', encoding='utf-8')
        except Exception:
            self._file = None

    def _write(self, level, op, msg, host=None, port=None, user=None,
               error_type=None, detail=None):
        if self._file is None:
            return
        with self._lock:
            try:
                # 归档轮转：文件过大时改名归档（历史保留，不再截断销毁）
                if self._file.tell() > self._MAX_BYTES:
                    self._rotate()
                    if self._file is None:
                        return
                ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                target = ''
                if host:
                    target = f' {host}:{port}'
                    if user:
                        target += f' user={user}'
                etype = f' | {error_type}' if error_type else ''
                line = f'{ts} | {level} | [{op}]{target}{etype} | {msg}'
                if detail:
                    # 多行详情（调用栈）缩进对齐，便于阅读
                    line += '\n' + '\n'.join(f'    {dl}' for dl in str(detail).rstrip().splitlines())
                self._file.write(line + '\n')
                self._file.flush()
            except Exception:
                pass  # 日志写入失败绝不影响主流程

    def info(self, op, msg, **kw):
        """记录 INFO 级日志（op 为操作类型，如 SSH/SFTP）"""
        self._write('INFO', op, msg, **kw)

    def error(self, op, msg, **kw):
        """记录 ERROR 级日志（不落调用栈，需要堆栈时用 exception）"""
        self._write('ERROR', op, msg, **kw)

    def exception(self, op, msg, exc=None, **kw):
        """记录异常详情（含调用栈），用于严重错误落盘"""
        detail = None
        if exc is not None:
            detail = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self._write('ERROR', op, msg,
                    error_type=type(exc).__name__ if exc else 'Exception',
                    detail=detail, **kw)


# 模块级单例
conn_logger = ConnLogger()


def qt_message_handler(msg_type, context, message):
    """Qt 消息处理器：将 Qt 内部 warning/critical/fatal 消息落盘。

    qFatal 在调用 abort()（即 0xC0000409 C 层崩溃）之前会先调用本处理器，
    因此崩溃的真正原因（如 "QThread: Destroyed while thread is still running"）
    能被记录下来，用于事后定位。本处理器自身绝不能抛异常。"""
    try:
        _levels = {
            QtMsgType.QtDebugMsg: 'DEBUG',
            QtMsgType.QtInfoMsg: 'INFO',
            QtMsgType.QtWarningMsg: 'WARN',
            QtMsgType.QtCriticalMsg: 'CRITICAL',
            QtMsgType.QtFatalMsg: 'FATAL',
        }
        level = _levels.get(msg_type, 'UNKNOWN')
        # 仅落盘 warning 及以上级别，避免 debug 信息淹没日志
        if msg_type in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            loc = ''
            try:
                if context is not None and getattr(context, 'file', None):
                    loc = f' ({context.file}:{context.line})'
            except Exception:
                loc = ''
            conn_logger._write('QT-' + level, 'QT', f'{message}{loc}')
    except Exception:
        pass
