# -*- coding: utf-8 -*-
"""通用工具函数：网络异常分类、自然排序、Transport 安全关闭、日志目录闭环清理"""

import os
import re
import socket
import subprocess
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


def show_info_bar(message, message_type="info", title=None, duration=2500,
                  parent=None, bottom_offset=0):
    """统一 InfoBar 提示：位置固定 BOTTOM_RIGHT，标题按类型自动映射。

    参数与主窗口 _show_info_bar 一致（message/message_type/title/duration），
    额外提供 parent（默认取当前活动窗口兜底）；返回 InfoBar 实例，
    便于调用方追加 Action/Widget（如「打开文件夹」按钮）。

    bottom_offset：在默认贴底位置基础上再向上抬升的像素数（2026-09-23 需求：
    设备状态页文件面板底部有迁移按钮行，贴底提示会遮挡按钮）。做法是给该条
    InfoBar 打 bottomOffset 动态属性 + 对 BottomRightInfoBarManager._pos 装一次
    识别该属性的补丁——manager 的滑入/重排/resize 定位全部经 _pos 计算，因此
    提示从屏幕右缘直接滑到抬升后的坐标，全程不经过贴底位置，无遮挡闪现；
    未设属性的其他 InfoBar 不受影响（manager 为子类级单例，补丁只改算法）。
    """
    # 延迟导入：core 层不硬依赖 UI 库，worker 等非 GUI 上下文也可安全引用
    from PySide6.QtCore import Qt
    from qfluentwidgets import InfoBar, InfoBarPosition
    from qfluentwidgets.components.widgets.info_bar import InfoBarIcon
    if parent is None:
        from PySide6.QtWidgets import QApplication
        parent = QApplication.activeWindow()
    if title is None:
        title = {'success': '成功', 'info': '提示',
                 'warning': '警告', 'error': '错误'}.get(message_type, '提示')
    icon = {'success': InfoBarIcon.SUCCESS, 'info': InfoBarIcon.INFORMATION,
            'warning': InfoBarIcon.WARNING,
            'error': InfoBarIcon.ERROR}.get(message_type,
                                            InfoBarIcon.INFORMATION)
    if bottom_offset:
        _patch_bottom_right_offset()
    # 不能用 InfoBar.success 等 classmethod：其内部创建后立即 show()，滑入动画
    # 终值已按贴底坐标定死——必须先建对象、打上 bottomOffset，再 show()
    bar = InfoBar(icon, title, message, orient=Qt.Horizontal,
                  isClosable=True, duration=duration,
                  position=InfoBarPosition.BOTTOM_RIGHT, parent=parent)
    if bottom_offset:
        bar.setProperty("bottomOffset", int(bottom_offset))
    bar.show()
    return bar


def _patch_bottom_right_offset():
    """让 BottomRightInfoBarManager._pos 识别 InfoBar 的 bottomOffset 属性（幂等）

    manager 的滑入动画（_slideStartPos/_createSlideAni 终值）、多条重排
    （_updateDropAni）、父容器 resize 复位（eventFilter）都以 _pos 为唯一
    坐标来源，补丁一处即全链路生效；仅对带属性的条生效，零副作用。
    """
    from qfluentwidgets.components.widgets.info_bar import \
        BottomRightInfoBarManager as BRM

    if getattr(BRM._pos, '_bottom_offset_aware', False):
        return
    orig_pos = BRM._pos

    def _pos(self, infoBar, parentSize=None):
        pt = orig_pos(self, infoBar, parentSize)
        offset = infoBar.property('bottomOffset') or 0
        if offset:
            pt.setY(pt.y() - int(offset))
        return pt

    _pos._bottom_offset_aware = True
    BRM._pos = _pos


# ==================== 打包版兄弟程序拉起 ====================

# 各独立打包应用的产物目录名（与 spec 的 COLLECT name 对应），
# 主程序/管理面板拉起兄弟 exe 时按此查找同级发布目录
_SIBLING_DIRS = {"aftersale.exe": "AfterSale", "management.exe": "Management"}


def launch_sibling_app(exe_name: str, args: list = None) -> bool:
    """拉起同包分发的独立 exe（如 aftersale.exe / management.exe）

    查找顺序（仅打包环境）：
    1. 当前 exe 同目录（用户把兄弟程序放在一起时）
    2. 同级发布目录（dist/AutoWork 旁 dist/AfterSale、dist/Management）
    3. 当前 exe 所在目录的父目录直接放同名 exe

    找到则异步启动并返回 True；开发环境或未找到返回 False，
    调用方应回退为内嵌打开，保证两种形态（独立进程/内嵌）都可用。
    """
    import sys
    if not getattr(sys, "frozen", False):
        return False
    app_dir = os.path.dirname(os.path.abspath(sys.executable))
    parent = os.path.dirname(app_dir)
    cands = [os.path.join(app_dir, exe_name)]
    _dir = _SIBLING_DIRS.get(exe_name)
    if _dir:
        cands.append(os.path.join(parent, _dir, exe_name))
    cands.append(os.path.join(parent, exe_name))
    for cand in cands:
        if os.path.isfile(cand):
            try:
                subprocess.Popen([cand] + list(args or []))
                return True
            except Exception:
                return False
    return False
