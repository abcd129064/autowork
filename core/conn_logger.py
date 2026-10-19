# -*- coding: utf-8 -*-
"""SSH/SFTP 连接统一文件日志 + Qt 消息处理器

不复用标准 logging：连接问题要能单独打开一个文件看完整时间线，混进业务日志里就没法快速定责。"""

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
    文件句柄长期持有，写失败一律静默：日志目录只读时退化为不记录，不能反过来拖垮连接。
    """
    _MAX_BYTES = 2 * 1024 * 1024  # 单文件超过 2MB 时轮转
    # 三个归档加当前文件约 8MB 封顶：再多就是在用户目录里悄悄堆垃圾。
    _MAX_ARCHIVES = 3               # 最多保留 3 个归档（.1/.2/.3，.1 最新）

    def __init__(self):
        # 多个 worker 线程共用一个句柄：写入与轮转必须互斥，否则会写出半行、半截时间戳。
        self._lock = threading.Lock()
        self._file = None
        self._path = ''
        # 目录平时可写，但装在 Program Files 下权限可能不足：失败就整体降级为不记录。
        try:
            log_dir = os.path.join(get_app_dir(), 'logs')
            # 首次运行目录还不存在；exist_ok 让两个进程同时首启时不会互相报错。
            os.makedirs(log_dir, exist_ok=True)
            # 路径存成成员：轮转要反复拼 .1/.2/.3，每处重拼容易拼错目录。
            self._path = os.path.join(log_dir, 'autowork_conn.log')
            # 追加模式：进程重启接着上次写；utf-8 是硬要求，中文错误信息不能乱码。
            self._file = open(self._path, 'a', encoding='utf-8')
        except Exception:
            self._file = None  # 日志不可用时静默降级，绝不影响主功能

    def _rotate(self):
        """归档轮转：当前文件改名为 .1，已有归档依次后移（.1→.2→.3），
        超出 _MAX_ARCHIVES 的最老归档删除。任何一步失败都不抛异常。"""
        try:
            # 先放掉旧句柄：Windows 下文件仍被占用时 os.replace 会直接失败。
            self._file.close()
        except Exception:
            pass
        # 置空后再改名：即使后面整段失败，_write 也会短路，而不是写已关闭的句柄。
        self._file = None
        try:
            oldest = f'{self._path}.{self._MAX_ARCHIVES}'
            if os.path.exists(oldest):
                # 必须先删最老的归档，否则后面的 os.replace 会撞上已存在的 .3。
                os.remove(oldest)
            # 从最老往最新倒着改名，正着改会让 .1 先覆盖 .2 再被 .2 覆盖。
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
        # 日志不可用时直接短路：调用点遍布连接流程，不能因为日志问题抛错。
        if self._file is None:
            return
        with self._lock:
            try:
                # 归档轮转：文件过大时改名归档（历史保留，不再截断销毁）
                if self._file.tell() > self._MAX_BYTES:
                    self._rotate()
                    if self._file is None:
                        return
                # 截到毫秒：微秒位对定位连接时序没有意义，只会让每行变长。
                ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                target = ''
                # host 为空就整段跳过：不少 Qt 消息根本没有连接上下文。
                if host:
                    target = f' {host}:{port}'
                    # 只拼用户名：密码一类的凭据绝不能进日志。
                    if user:
                        target += f' user={user}'
                etype = f' | {error_type}' if error_type else ''
                # 一条记录压成一行：事后靠 grep 按 | 切字段，正文里不能再出现换行。
                line = f'{ts} | {level} | [{op}]{target}{etype} | {msg}'
                if detail:
                    # 多行详情（调用栈）缩进对齐，便于阅读
                    line += '\n' + '\n'.join(f'    {dl}' for dl in str(detail).rstrip().splitlines())
                self._file.write(line + '\n')
                # 每条都 flush：崩溃或断电时，缓冲区里最后几行恰恰是最关键的现场。
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
        # exc 允许为空：调用方常常只有消息没有异常对象，此时仍要落一条 ERROR。
        detail = None
        if exc is not None:
            detail = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self._write('ERROR', op, msg,
                    error_type=type(exc).__name__ if exc else 'Exception',
                    detail=detail, **kw)


# 模块级单例
# 导入即创建：各模块直接 from ... import conn_logger，不必自己管生命周期。
conn_logger = ConnLogger()


def qt_message_handler(msg_type, context, message):
    """Qt 消息处理器：将 Qt 内部 warning/critical/fatal 消息落盘。

    qFatal 在调用 abort()（即 0xC0000409 C 层崩溃）之前会先调用本处理器，
    因此崩溃的真正原因（如 "QThread: Destroyed while thread is still running"）
    能被记录下来，用于事后定位。本处理器自身绝不能抛异常。"""
    try:
        # Qt 把枚举按 C++ 侧的值传进来，直接与成员比较最稳；顺便映射出可读级别名。
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
            # context 在个别 Qt 版本里可能是 None 或不是完整对象，取属性必须防护。
            loc = ''
            try:
                if context is not None and getattr(context, 'file', None):
                    loc = f' ({context.file}:{context.line})'
            except Exception:
                loc = ''
            # 走底层 _write 而不调 info/error：本处理器运行在崩溃路径上，少一层调用少一个风险点。
            conn_logger._write('QT-' + level, 'QT', f'{message}{loc}')
    # 整体包在 try 里：qFatal 之后进程马上就要 abort，此时再抛异常只会掩盖真正的错误。
    except Exception:
        pass
