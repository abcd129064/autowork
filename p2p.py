import os
import json
import socket
import random
import subprocess
import sys
import time
try:
    import toml
    TOML_AVAILABLE = True
except ImportError:
    TOML_AVAILABLE = False

try:
    import win32gui
    import win32con
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False

def generate_random_port(exclude_ports=None):
    """生成随机端口号，排除常用端口和已使用的端口"""
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