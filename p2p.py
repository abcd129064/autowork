# -*- coding: utf-8 -*-
"""端口/外部客户端小工具：随机端口生成、端口占用检测、Xshell/Xftp 双开"""
import socket
import random
import subprocess

def generate_random_port(exclude_ports=None):
    """随机生成 10000-65535 内不与 exclude_ports（及常用端口）冲突的端口"""
    if exclude_ports is None:
        exclude_ports = set()

    # 常用端口
    common_ports = {
            0, 1, 7, 9, 13, 17, 19, 20, 21, 22, 23, 25, 37, 42, 43, 53, 67, 68, 69,
        80, 88, 110, 111, 113, 115, 119, 123, 135, 137, 138, 139, 143, 161, 162,
        179, 389, 443, 445, 465, 514, 515, 520, 521, 587, 631, 636, 873, 993, 995,
        1080, 1433, 1434, 1521, 1723, 3306, 3389, 5432, 5900, 5901, 6379, 8080, 8443,
        7400  # frpc admin端口
    }
    exclude_ports = exclude_ports | common_ports

    while True:
        port = random.randint(10000, 65535)
        if port not in exclude_ports:
            return port


def is_port_in_use(port, host='127.0.0.1'):
    """检测端口是否被占用"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except (socket.error, OSError):
        return False

def open_xshell_and_xftp(host, port, username, password, log=None):
    """凭据写进 URL 直接双开 Xshell/Xftp（免交互式 input），返回是否启动成功"""
    _log = log or (lambda msg: None)
    _log(f'[Xshell/Xftp] 正在连接到: {username}@{host}:{port}')

    try:
        create_new_console = 0x00000010

        xshell_url = f'ssh://{username}:{password}@{host}:{port}'
        subprocess.Popen(
            f'xshell -url "{xshell_url}"',
            shell=True,
            creationflags=create_new_console
        )

        xftp_url = f'sftp://{username}:{password}@{host}:{port}'
        subprocess.Popen(
            f'xftp -url "{xftp_url}"',
            shell=True,
            creationflags=create_new_console
        )

        _log('[Xshell/Xftp] Xshell 和 Xftp 已打开')
        return True

    except FileNotFoundError:
        _log('[Xshell/Xftp] 未找到 xshell 或 xftp 命令，请确保已安装并加入 PATH')
        return False
    except OSError as e:
        _log(f'[Xshell/Xftp] 启动失败: {e}')
        return False