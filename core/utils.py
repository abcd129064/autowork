# -*- coding: utf-8 -*-
"""通用工具函数：网络异常分类、自然排序、Transport 安全关闭、日志目录闭环清理"""

import os
import re
import socket
import time

try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False


# 网络就绪类错误关键词，匹配时自动重试，认证失败等错误不重试
RETRYABLE_KEYWORDS = ('Error reading SSH protocol banner', 'Server connection dropped')
RETRY_MAX = 5
RETRY_DELAY = 2  # 秒


def classify_conn_error(e):
    """将网络异常转换为用户可读的中文提示（不含敏感信息）"""
    msg = str(e)
    if isinstance(e, socket.timeout) or 'timed out' in msg or 'timeout' in msg.lower():
        return '连接超时，请检查目标地址/端口是否可达'
    if isinstance(e, ConnectionRefusedError) or 'refused' in msg.lower():
        return '连接被拒绝，目标端口未开放或服务未启动'
    if isinstance(e, OSError) and getattr(e, 'winerror', None) == 10065:
        return '主机不可达，请检查网络连通性'
    if isinstance(e, OSError) and getattr(e, 'winerror', None) == 10060:
        return '连接超时（主机无响应），请检查防火墙或网络'
    if PARAMIKO_AVAILABLE and isinstance(e, paramiko.AuthenticationException):
        return '认证失败，请检查用户名和密码'
    if PARAMIKO_AVAILABLE and isinstance(e, paramiko.BadHostKeyException):
        return '主机密钥不匹配，可能遭受中间人攻击或服务器已重装'
    if PARAMIKO_AVAILABLE and isinstance(e, paramiko.SSHException):
        return f'SSH 协议错误: {msg}'
    if isinstance(e, EOFError):
        return '远端意外关闭连接（SSH 服务可能未就绪）'
    if isinstance(e, InterruptedError):
        return '操作已取消'
    if isinstance(e, PermissionError):
        return f'权限不足: {msg}'
    if isinstance(e, FileNotFoundError):
        return f'文件或路径不存在: {msg}'
    return msg


def natural_sort_key(s):
    """自然排序 key：将字符串中的连续数字段按数值比较，非数字段按字符串比较。
    例如 "23-10" 排在 "193" 前面（23 < 193），而非字典序的 "193" < "23-10"。"""
    return [int(part) if part.isdigit() else part
            for part in re.split(r'(\d+)', s)]


# 端口占用：持有绑定后的 socket，防止被垃圾回收导致端口释放
_WEB_PORT_HOLDER = None


def occupy_web_port(preferred=0):
    """强制占用一个端口并返回端口号：
    - preferred > 0 时优先尝试该端口，被占用则向后递增探测；
    - preferred <= 0 时从 8080 开始自动探测；
    绑定成功后持有 socket，程序运行期间该端口被本程序独占。
    """
    global _WEB_PORT_HOLDER
    start = preferred if 0 < preferred < 65536 else 8080
    for port in range(start, 65536):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            sock.close()
            continue
        _WEB_PORT_HOLDER = sock
        return port
    return 0  # 理论不可达：探测范围覆盖全部可用端口


def safe_close_transport(transport, join_timeout=3):
    """安全关闭 paramiko Transport：先 close 再 join 等待后台线程退出。
    避免线程仍在读 socket 时 socket 被销毁导致 C 层崩溃 (0xC0000409)。
    """
    if transport is None:
        return
    try:
        transport.close()
    except Exception:
        pass
    try:
        transport.join(join_timeout)
    except Exception:
        pass


def cleanup_log_dir(dir_path, max_files=500, max_age_days=30, suffix='.log'):
    """日志目录闭环清理，防止无限增长占满磁盘：
    1) 删除修改时间超过 max_age_days 天的文件（<=0 时不按龄清理）
    2) 剩余文件数仍超过 max_files 时，从最旧开始删除直到不超限（<=0 时不限数量）

    仅处理指定后缀的普通文件；任何失败静默降级，绝不影响主流程。
    返回实际删除的文件数。
    """
    if not dir_path or not os.path.isdir(dir_path):
        return 0
    removed = 0
    try:
        suffix = (suffix or '').lower()
        entries = []
        for name in os.listdir(dir_path):
            if suffix and not name.lower().endswith(suffix):
                continue
            path = os.path.join(dir_path, name)
            if not os.path.isfile(path):
                continue
            try:
                entries.append((os.path.getmtime(path), path))
            except OSError:
                continue
        # 1) 超龄清理
        if max_age_days and max_age_days > 0:
            cutoff = time.time() - max_age_days * 86400
            kept = []
            for mtime, path in entries:
                if mtime < cutoff:
                    try:
                        os.remove(path)
                        removed += 1
                        continue
                    except OSError:
                        pass
                kept.append((mtime, path))
            entries = kept
        # 2) 超量清理（按修改时间新→旧排序，保留最新的 max_files 个）
        if max_files and max_files > 0 and len(entries) > max_files:
            entries.sort(reverse=True)
            for _, path in entries[max_files:]:
                try:
                    os.remove(path)
                    removed += 1
                except OSError:
                    pass
    except OSError:
        pass
    return removed


def show_info_bar(message, message_type="info", title=None, duration=2500, parent=None):
    """统一 InfoBar 提示：位置固定 BOTTOM_RIGHT，标题按类型自动映射。

    参数与主窗口 _show_info_bar 一致（message/message_type/title/duration），
    额外提供 parent（默认取当前活动窗口兜底）；返回 InfoBar 实例，
    便于调用方追加 Action/Widget（如「打开文件夹」按钮）。
    """
    # 延迟导入：core 层不硬依赖 UI 库，worker 等非 GUI 上下文也可安全引用
    from qfluentwidgets import InfoBar, InfoBarPosition
    if parent is None:
        from PySide6.QtWidgets import QApplication
        parent = QApplication.activeWindow()
    if title is None:
        title = {'success': '成功', 'info': '提示',
                 'warning': '警告', 'error': '错误'}.get(message_type, '提示')
    factory = {'success': InfoBar.success, 'info': InfoBar.info,
               'warning': InfoBar.warning, 'error': InfoBar.error}
    return factory.get(message_type, InfoBar.info)(
        title=title, content=message, parent=parent,
        position=InfoBarPosition.BOTTOM_RIGHT, duration=duration)
