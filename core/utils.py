# -*- coding: utf-8 -*-
"""通用工具函数：网络异常分类、自然排序、Transport 安全关闭"""

import re
import socket

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
