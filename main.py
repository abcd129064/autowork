# -*- coding: utf-8 -*-

import sys
import os
import json
import re
import shutil
import subprocess
import ctypes
import stat
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from PySide6.QtWidgets import (QApplication, QMainWindow, QMessageBox, QLabel,
    QWidget, QListWidgetItem, QMenu, QColorDialog, QFontDialog, QInputDialog,
    QDialog, QVBoxLayout, QHBoxLayout, QKeySequenceEdit, QDialogButtonBox,
    QComboBox, QSpinBox, QListView, QAbstractItemView, QFrame, QFormLayout,
    QLineEdit, QTreeWidget, QTreeWidgetItem, QHeaderView, QFileDialog,
    QPushButton, QProgressDialog, QPlainTextEdit, QSplitter, QProgressBar,
    QTableWidget, QTableWidgetItem, QMenuBar)
from PySide6.QtCore import Slot, QProcess, Qt, QTimer, QThread, Signal
from PySide6.QtGui import QColor, QBrush, QShortcut, QKeySequence, QFont, QAction, QActionGroup, QTextCursor
from qfluentwidgets import (setTheme, setThemeColor, Theme, InfoBar, InfoBarPosition,
                            RoundMenu, Action, MenuAnimationType, FluentIcon,
                            setFontFamilies, FluentTitleBar)
from qfluentwidgets.window.fluent_window import FluentWindowBase
from autowork_with_table import Ui_MainWindow
from p2p import generate_random_port, is_port_in_use

try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False

# Windows DLL 函数声明
if sys.platform == 'win32':
    _k32 = ctypes.WinDLL('kernel32')
    _OpenProcess = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)(
        ('OpenProcess', _k32))
    _CreateToolhelp32Snapshot = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong)(
        ('CreateToolhelp32Snapshot', _k32))
    _Thread32First = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(
        ('Thread32First', _k32))
    _Thread32Next = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(
        ('Thread32Next', _k32))
    _OpenThread = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)(
        ('OpenThread', _k32))
    _SuspendThread = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(
        ('SuspendThread', _k32))
    _ResumeThread = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(
        ('ResumeThread', _k32))
    _CloseHandle = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p)(
        ('CloseHandle', _k32))
    _dwm = ctypes.WinDLL('dwmapi')
    _DwmSetWindowAttribute = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong)(
        ('DwmSetWindowAttribute', _dwm))

    # ===== 显示设置 API（用于三端启动前捕获分辨率 / 关闭后恢复） =====
    _user32 = ctypes.WinDLL('user32')

    class DEVMODE(ctypes.Structure):
        """Windows DEVMODEW 结构体（220 字节）。
        字段偏移经本机实测校准：用 256 字节缓冲区调用 EnumDisplaySettingsW，
        确认 dmBitsPerPel@168 / dmPelsWidth@172 / dmPelsHeight@176 /
        dmDisplayFlags@180 / dmDisplayFrequency@184。
        关键点：dmFields 与 dmFormName 之间的 union 区域实际占 24 字节（76-99），
        而非文档表面的 16 字节，故需显式填充，否则后续字段整体错位 8 字节。"""
        _fields_ = [
            ("dmDeviceName", ctypes.c_wchar * 32),    # 0-63
            ("dmSpecVersion", ctypes.c_ushort),        # 64
            ("dmDriverVersion", ctypes.c_ushort),      # 66
            ("dmSize", ctypes.c_ushort),               # 68
            ("dmDriverExtra", ctypes.c_ushort),        # 70
            ("dmFields", ctypes.c_ulong),              # 72
            # union 区域（打印字段组 / 显示字段组），实测占 24 字节（76-99）
            ("dmOrientation", ctypes.c_short),         # 76
            ("dmPaperSize", ctypes.c_short),           # 78
            ("dmPaperLength", ctypes.c_short),         # 80
            ("dmPaperWidth", ctypes.c_short),          # 82
            ("dmScale", ctypes.c_short),               # 84
            ("dmCopies", ctypes.c_short),              # 86
            ("dmDefaultSource", ctypes.c_short),       # 88
            ("dmPrintQuality", ctypes.c_short),        # 90
            ("_union_pad", ctypes.c_byte * 8),         # 92-99 填充至 24 字节
            ("dmFormName", ctypes.c_wchar * 32),       # 100-163
            ("dmLogPixels", ctypes.c_ushort),          # 164
            ("_logpixels_pad", ctypes.c_ushort),       # 166 对齐填充
            ("dmBitsPerPel", ctypes.c_ulong),          # 168
            ("dmPelsWidth", ctypes.c_ulong),           # 172
            ("dmPelsHeight", ctypes.c_ulong),          # 176
            ("dmDisplayFlags", ctypes.c_ulong),        # 180
            ("dmDisplayFrequency", ctypes.c_ulong),    # 184
            ("dmICMMethod", ctypes.c_ulong),           # 188
            ("dmICMIntent", ctypes.c_ulong),           # 192
            ("dmMediaType", ctypes.c_ulong),           # 196
            ("dmDitherType", ctypes.c_ulong),          # 200
            ("dmReserved1", ctypes.c_ulong),           # 204
            ("dmReserved2", ctypes.c_ulong),           # 208
            ("dmPanningWidth", ctypes.c_ulong),        # 212
            ("dmPanningHeight", ctypes.c_ulong),       # 216
        ]  # sizeof = 220

    _EnumDisplaySettingsW = ctypes.WINFUNCTYPE(
        ctypes.c_int, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.POINTER(DEVMODE))(
        ('EnumDisplaySettingsW', _user32))
    _ChangeDisplaySettingsW = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.POINTER(DEVMODE), ctypes.c_ulong)(
        ('ChangeDisplaySettingsW', _user32))

    # 显示设置常量
    ENUM_CURRENT_SETTINGS = 0xFFFFFFFF   # (DWORD)-1：当前生效的显示模式
    CDS_UPDATEREGISTRY = 0x1             # 写入注册表（持久化）
    CDS_FULLSCREEN = 0x4                 # 全屏模式切换
    DISP_CHANGE_SUCCESSFUL = 0
    DM_BITSPERPEL = 0x40000
    DM_PELSWIDTH = 0x80000
    DM_PELSHEIGHT = 0x100000
    DM_DISPLAYFREQUENCY = 0x400000


def _natural_sort_key(s):
    """自然排序 key：将字符串中的连续数字段按数值比较，非数字段按字符串比较。
    例如 "23-10" 排在 "193" 前面（23 < 193），而非字典序的 "193" < "23-10"。"""
    return [int(part) if part.isdigit() else part
            for part in re.split(r'(\d+)', s)]


class TCPWorker(QThread):
    """TCP 连接工作线程"""
    # 注意：不能命名为 finished，会遮蔽 QThread 内置 finished 信号导致崩溃
    result_ready = Signal(str)
    error = Signal(str)

    def __init__(self, host, port, username, password):
        super().__init__()
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self._client = None

    def run(self):
        try:
            self._client = paramiko.SSHClient()
            self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self._client.connect(self.host, port=self.port,
                                 username=self.username, password=self.password,
                                 timeout=10, banner_timeout=15, auth_timeout=15)
            stdin, stdout, stderr = self._client.exec_command("hostname && whoami")
            result = stdout.read().decode('utf-8', errors='ignore').strip()
            self.result_ready.emit(result)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            # run() 结束后立即关闭 paramiko client，避免资源泄漏
            self.close()

    def close(self):
        if self._client:
            try:
                transport = self._client.get_transport()
                _safe_close_transport(transport)
            except Exception:
                pass
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None


class SFTPListWorker(QThread):
    """异步 SFTP 列目录工作线程"""
    result = Signal(str, list)
    error = Signal(str)

    def __init__(self, transport, remote_path):
        super().__init__()
        self.transport = transport
        self.remote_path = remote_path

    def run(self):
        sftp = None
        try:
            sftp = paramiko.SFTPClient.from_transport(self.transport)
            entries = []
            for name in sftp.listdir(self.remote_path):
                full_path = self.remote_path.rstrip('/') + '/' + name
                try:
                    st = sftp.stat(full_path)
                    is_dir = stat.S_ISDIR(st.st_mode) if st.st_mode else False
                    size = st.st_size if st.st_size else 0
                    mtime = datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M') if st.st_mtime else ''
                    perm = stat.filemode(st.st_mode) if st.st_mode else ''
                except Exception:
                    is_dir, size, mtime, perm = False, 0, '', ''
                entries.append({
                    'name': name, 'is_dir': is_dir,
                    'size': size, 'mtime': mtime, 'perm': perm
                })
            self.result.emit(self.remote_path, entries)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass


class SFTPOperationWorker(QThread):
    """异步 SFTP 操作工作线程（上传/下载/删除/创建目录），支持传输进度"""
    success = Signal(str)
    error = Signal(str)
    progress = Signal(int, int)  # (transferred_bytes, total_bytes)

    def __init__(self, conn_params, operation, local_path='', remote_path='', file_size=0):
        super().__init__()
        self.conn_params = conn_params  # (host, port, username, password)
        self.operation = operation
        self.local_path = local_path
        self.remote_path = remote_path
        self.file_size = file_size
        # 暂停/停止控制
        self._pause_event = threading.Event()  # set=运行, clear=暂停
        self._pause_event.set()
        self._stop_flag = False

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    def stop(self):
        self._stop_flag = True
        self._pause_event.set()  # 解除暂停阻塞以便线程退出

    def _progress_cb(self, transferred, total):
        # 检查停止
        if self._stop_flag:
            raise InterruptedError('传输已取消')
        # 检查暂停（阻塞等待直到恢复）
        self._pause_event.wait()
        self.progress.emit(transferred, total)

    def run(self):
        transport = None
        sftp = None
        try:
            host, port, username, password = self.conn_params
            transport = paramiko.Transport((host, port))
            transport.banner_timeout = 15
            transport.connect(username=username, password=password)
            sftp = paramiko.SFTPClient.from_transport(transport)
            if self.operation == 'upload':
                sftp.put(self.local_path, self.remote_path, callback=self._progress_cb)
                self.success.emit(f"已上传: {os.path.basename(self.local_path)}")
            elif self.operation == 'download':
                sftp.get(self.remote_path, self.local_path, callback=self._progress_cb)
                self.success.emit(f"已下载: {os.path.basename(self.remote_path)}")
            elif self.operation == 'delete':
                sftp.remove(self.remote_path)
                self.success.emit(f"已删除: {os.path.basename(self.remote_path)}")
            elif self.operation == 'rmdir':
                sftp.rmdir(self.remote_path)
                self.success.emit(f"已删除目录: {os.path.basename(self.remote_path)}")
            elif self.operation == 'mkdir':
                sftp.mkdir(self.remote_path)
                self.success.emit(f"已创建目录: {os.path.basename(self.remote_path)}")
            elif self.operation == 'rename':
                # local_path 复用为 old_path
                try:
                    sftp.posix_rename(self.local_path, self.remote_path)
                except (AttributeError, IOError):
                    sftp.rename(self.local_path, self.remote_path)
                self.success.emit(f"已重命名: {os.path.basename(self.local_path)} -> {os.path.basename(self.remote_path)}")
            elif self.operation == 'create_file':
                with sftp.open(self.remote_path, 'w') as f:
                    pass
                self.success.emit(f"已创建文件: {os.path.basename(self.remote_path)}")
        except InterruptedError:
            pass  # 用户取消，不报错
        except Exception as e:
            self.error.emit(str(e))
        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass
            _safe_close_transport(transport)


class SFTPDirTransferWorker(QThread):
    """异步 SFTP 目录递归传输工作线程（上传整个目录/下载整个目录）"""
    success = Signal(str)
    error = Signal(str)
    progress = Signal(int, int)  # (transferred_bytes, total_bytes)

    def __init__(self, conn_params, operation, local_dir='', remote_dir='', dir_name=''):
        super().__init__()
        self.conn_params = conn_params  # (host, port, username, password)
        self.operation = operation  # 'upload_dir' or 'download_dir'
        self.local_dir = local_dir
        self.remote_dir = remote_dir
        self.dir_name = dir_name
        # 暂停/停止控制
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._stop_flag = False

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    def stop(self):
        self._stop_flag = True
        self._pause_event.set()

    def _check_pause_stop(self):
        """检查停止/暂停状态，停止时抛出InterruptedError，暂停时阻塞等待"""
        if self._stop_flag:
            raise InterruptedError('传输已取消')
        self._pause_event.wait()

    def run(self):
        transport = None
        sftp = None
        try:
            host, port, username, password = self.conn_params
            transport = paramiko.Transport((host, port))
            transport.banner_timeout = 15
            transport.connect(username=username, password=password)
            sftp = paramiko.SFTPClient.from_transport(transport)
            if self.operation == 'upload_dir':
                self._upload_dir(sftp)
            elif self.operation == 'download_dir':
                self._download_dir(sftp)
        except InterruptedError:
            pass  # 用户取消，不报错
        except Exception as e:
            self.error.emit(f"目录传输失败 [{self.dir_name}]: {e}")
        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass
            _safe_close_transport(transport)

    def _upload_dir(self, sftp):
        """递归上传本地目录到远程"""
        # 先计算总大小
        total_size = 0
        file_list = []  # [(local_file_path, relative_path), ...]
        for root, dirs, files in os.walk(self.local_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, self.local_dir)
                try:
                    total_size += os.path.getsize(fpath)
                except OSError:
                    pass
                file_list.append((fpath, rel))

        transferred = 0
        errors = []
        # 创建远程根目录
        self._mkdir_p_remote(sftp, self.remote_dir)
        # 预创建所有子目录
        for root, dirs, files in os.walk(self.local_dir):
            for dname in dirs:
                dpath = os.path.join(root, dname)
                rel_dir = os.path.relpath(dpath, self.local_dir).replace('\\', '/')
                remote_sub = self.remote_dir.rstrip('/') + '/' + rel_dir
                self._mkdir_p_remote(sftp, remote_sub)

        # 逐文件上传
        for fpath, rel in file_list:
            self._check_pause_stop()
            rel_remote = rel.replace('\\', '/')
            remote_file = self.remote_dir.rstrip('/') + '/' + rel_remote
            try:
                file_size = os.path.getsize(fpath)
                sftp.put(fpath, remote_file)
                transferred += file_size
                self.progress.emit(transferred, total_size)
            except PermissionError as e:
                errors.append(f"权限不足: {rel} ({e})")
            except OSError as e:
                errors.append(f"文件占用/不可读: {rel} ({e})")
            except Exception as e:
                errors.append(f"传输失败: {rel} ({e})")

        if errors:
            err_summary = '; '.join(errors[:5])
            if len(errors) > 5:
                err_summary += f' ...等共{len(errors)}个错误'
            self.error.emit(f"目录上传部分失败 [{self.dir_name}]: {err_summary}")
        else:
            self.success.emit(f"已上传目录: {self.dir_name} ({len(file_list)} 个文件)")

    def _download_dir(self, sftp):
        """递归下载远程目录到本地"""
        # 先递归收集远程文件列表及总大小
        file_list = []  # [(remote_file_path, relative_path, size), ...]
        total_size = 0
        self._collect_remote_files(sftp, self.remote_dir, '', file_list)
        for _, _, sz in file_list:
            total_size += sz

        transferred = 0
        errors = []
        # 创建本地根目录
        try:
            os.makedirs(self.local_dir, exist_ok=True)
        except OSError as e:
            self.error.emit(f"无法创建本地目录 [{self.local_dir}]: {e}")
            return

        # 逐文件下载
        for remote_file, rel, sz in file_list:
            self._check_pause_stop()
            local_file = os.path.join(self.local_dir, rel.replace('/', os.sep))
            local_sub_dir = os.path.dirname(local_file)
            try:
                os.makedirs(local_sub_dir, exist_ok=True)
                sftp.get(remote_file, local_file)
                transferred += sz
                self.progress.emit(transferred, total_size)
            except PermissionError as e:
                errors.append(f"权限不足: {rel} ({e})")
            except OSError as e:
                errors.append(f"目标不可写/路径不存在: {rel} ({e})")
            except Exception as e:
                errors.append(f"传输失败: {rel} ({e})")

        if errors:
            err_summary = '; '.join(errors[:5])
            if len(errors) > 5:
                err_summary += f' ...等共{len(errors)}个错误'
            self.error.emit(f"目录下载部分失败 [{self.dir_name}]: {err_summary}")
        else:
            self.success.emit(f"已下载目录: {self.dir_name} ({len(file_list)} 个文件)")

    def _collect_remote_files(self, sftp, remote_base, rel_prefix, file_list):
        """递归收集远程目录下的所有文件"""
        try:
            entries = sftp.listdir_attr(remote_base)
        except Exception:
            return
        for attr in entries:
            name = attr.filename
            full_path = remote_base.rstrip('/') + '/' + name
            rel_path = (rel_prefix + '/' + name) if rel_prefix else name
            if stat.S_ISDIR(attr.st_mode) if attr.st_mode else False:
                self._collect_remote_files(sftp, full_path, rel_path, file_list)
            else:
                size = attr.st_size if attr.st_size else 0
                file_list.append((full_path, rel_path, size))

    def _mkdir_p_remote(self, sftp, remote_path):
        """递归创建远程目录（类似 mkdir -p）"""
        dirs_to_create = []
        path = remote_path
        while path and path != '/':
            try:
                sftp.stat(path)
                break  # 已存在
            except FileNotFoundError:
                dirs_to_create.append(path)
                path = '/'.join(path.rstrip('/').split('/')[:-1])
                if not path:
                    path = '/'
            except IOError:
                dirs_to_create.append(path)
                path = '/'.join(path.rstrip('/').split('/')[:-1])
                if not path:
                    path = '/'
        for d in reversed(dirs_to_create):
            try:
                sftp.mkdir(d)
            except Exception:
                pass  # 可能已被并发创建


# 网络就绪类错误关键词，匹配时自动重试，认证失败等错误不重试
_RETRYABLE_KEYWORDS = ('Error reading SSH protocol banner', 'Server connection dropped')
_RETRY_MAX = 5
_RETRY_DELAY = 2  # 秒


def _safe_close_transport(transport, join_timeout=3):
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


class SFTPConnectWorker(QThread):
    """异步建立 paramiko.Transport 连接的工作线程（含自动重试）"""
    connected = Signal(object)   # 成功时发射 transport 对象
    error = Signal(str)          # 失败时发射错误信息

    def __init__(self, host, port, username, password):
        super().__init__()
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self._abort = False

    def abort(self):
        """请求中止重试循环"""
        self._abort = True

    def run(self):
        for attempt in range(1, _RETRY_MAX + 1):
            if self._abort:
                return
            transport = None
            try:
                transport = paramiko.Transport((self.host, self.port))
                transport.banner_timeout = 15
                transport.connect(username=self.username, password=self.password)
                self.connected.emit(transport)
                return  # 成功，立即退出
            except Exception as e:
                # 安全关闭 transport（close+join 等待后台线程退出，避免 C 层崩溃）
                _safe_close_transport(transport)
                err_msg = str(e)
                # 仅网络就绪类错误才重试，认证失败等直接报错
                if any(kw in err_msg for kw in _RETRYABLE_KEYWORDS) and attempt < _RETRY_MAX:
                    print(f'[SFTP] 连接失败 ({err_msg})，正在重试 ({attempt}/{_RETRY_MAX})...')
                    time.sleep(_RETRY_DELAY)
                    continue
                # 不可重试的错误 或 已达最大重试次数
                if self._abort:
                    return
                if attempt > 1:
                    self.error.emit(f'连接失败（已重试{_RETRY_MAX}次）: {err_msg}')
                else:
                    self.error.emit(err_msg)
                return


class SFTPWindow(QDialog):
    """SFTP 文件管理窗口"""

    def __init__(self, host, port, username, password, server_name='', log_callback=None, parent=None):
        super().__init__(parent)
        title = f"SFTP 文件管理 - {server_name} ({host}:{port})" if server_name else f"SFTP 文件管理 - {host}:{port}"
        self.setWindowTitle(title)
        self.resize(1000, 620)
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._server_name = server_name
        self._conn_params = (host, port, username, password)  # 用于创建独立传输连接
        self._transport = None
        self._remote_path = '/home'
        self._remote_entries = []
        # 本地默认进入桌面目录，不存在时回退到用户主目录
        _desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        self._local_path = _desktop if os.path.isdir(_desktop) else os.path.expanduser('~')
        self._local_entries = []
        self._log = log_callback or (lambda msg: None)
        # 异步连接用 worker
        self._connect_worker = None
        # 列目录用单一 worker
        self._list_worker = None
        # 列目录防重入与过期结果过滤
        self._list_generation = 0
        self._listing = False
        # 待处理的远程路径（当前正在列目录时用户发起新导航时暂存）
        self._pending_remote_path = None
        # 传输用多 worker 并行管理：{id: {'worker': ..., 'row': ..., 'start_time': ...}}
        self._transfer_workers = {}
        self._next_transfer_id = 0
        self._init_ui()
        QTimer.singleShot(100, self._connect_and_list)

    # ------------------------------------------------------------------ UI 构建
    def _init_ui(self):
        root = QVBoxLayout(self)

        # ---- 左右双面板
        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧 - 本地文件
        self._left_panel = QWidget()
        left_lay = QVBoxLayout(self._left_panel)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_bar = QHBoxLayout()
        self._btn_local_up = QPushButton('.. 上级')
        self._btn_local_up.setAutoDefault(False)  # 防止 QDialog 中 Enter 键劫持
        self._btn_local_up.clicked.connect(self._local_go_up)
        left_bar.addWidget(self._btn_local_up)
        left_bar.addWidget(QLabel('本地:'))
        self._edit_local_path = QLineEdit(self._local_path)
        self._edit_local_path.setStyleSheet('font-weight:bold;')
        self._edit_local_path.returnPressed.connect(self._on_local_path_entered)
        left_bar.addWidget(self._edit_local_path, 1)
        self._btn_local_refresh = QPushButton('刷新')
        self._btn_local_refresh.setAutoDefault(False)
        self._btn_local_refresh.clicked.connect(self._local_refresh)
        left_bar.addWidget(self._btn_local_refresh)
        left_lay.addLayout(left_bar)

        self._local_tree = QTreeWidget()
        self._local_tree.setHeaderLabels(['文件名', '大小', '类型', '修改时间'])
        self._local_tree.setColumnCount(4)
        lh = self._local_tree.header()
        lh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in [1, 2, 3]:
            lh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self._local_tree.itemDoubleClicked.connect(self._on_local_item_double_clicked)
        self._local_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._local_tree.customContextMenuRequested.connect(self._on_local_context_menu)
        left_lay.addWidget(self._local_tree)

        # 本地底部搜索框（默认隐藏）
        self._local_search_frame = QWidget()
        local_sf = QHBoxLayout(self._local_search_frame)
        local_sf.setContentsMargins(0, 2, 0, 0)
        self._local_search_edit = QLineEdit()
        self._local_search_edit.setPlaceholderText('搜索本地文件...')
        self._local_search_edit.returnPressed.connect(self._on_local_search)
        local_sf.addWidget(self._local_search_edit, 1)
        btn_ls = QPushButton('搜索')
        btn_ls.setAutoDefault(False)
        btn_ls.clicked.connect(self._on_local_search)
        local_sf.addWidget(btn_ls)
        btn_lc = QPushButton('✕')
        btn_lc.setAutoDefault(False)
        btn_lc.clicked.connect(lambda: self._local_search_frame.hide())
        local_sf.addWidget(btn_lc)
        left_lay.addWidget(self._local_search_frame)
        self._local_search_frame.hide()

        # 右侧 - 远程 SFTP
        self._right_panel = QWidget()
        right_lay = QVBoxLayout(self._right_panel)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_bar = QHBoxLayout()
        self._btn_up = QPushButton('.. 上级目录')
        self._btn_up.setAutoDefault(False)  # 防止 QDialog 中 Enter 键劫持
        self._btn_up.clicked.connect(self._go_up)
        right_bar.addWidget(self._btn_up)
        right_bar.addWidget(QLabel('远程:'))
        self._edit_remote_path = QLineEdit(self._remote_path)
        self._edit_remote_path.setStyleSheet('font-weight:bold;')
        self._edit_remote_path.returnPressed.connect(self._on_remote_path_entered)
        right_bar.addWidget(self._edit_remote_path, 1)
        self._btn_refresh = QPushButton('刷新')
        self._btn_refresh.setAutoDefault(False)
        self._btn_refresh.clicked.connect(self._refresh)
        right_bar.addWidget(self._btn_refresh)
        right_lay.addLayout(right_bar)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(['文件名', '大小', '类型', '权限', '修改时间'])
        self._tree.setColumnCount(5)
        rh = self._tree.header()
        rh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in [1, 2, 3, 4]:
            rh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_remote_context_menu)
        right_lay.addWidget(self._tree)

        # 远程底部搜索框（默认隐藏）
        self._remote_search_frame = QWidget()
        remote_sf = QHBoxLayout(self._remote_search_frame)
        remote_sf.setContentsMargins(0, 2, 0, 0)
        self._remote_search_edit = QLineEdit()
        self._remote_search_edit.setPlaceholderText('搜索远程文件...')
        self._remote_search_edit.returnPressed.connect(self._on_remote_search)
        remote_sf.addWidget(self._remote_search_edit, 1)
        btn_rs = QPushButton('搜索')
        btn_rs.setAutoDefault(False)
        btn_rs.clicked.connect(self._on_remote_search)
        remote_sf.addWidget(btn_rs)
        btn_rc = QPushButton('✕')
        btn_rc.setAutoDefault(False)
        btn_rc.clicked.connect(lambda: self._remote_search_frame.hide())
        remote_sf.addWidget(btn_rc)
        right_lay.addWidget(self._remote_search_frame)
        self._remote_search_frame.hide()

        self._splitter.addWidget(self._left_panel)
        self._splitter.addWidget(self._right_panel)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 1)
        root.addWidget(self._splitter, 1)

        # ---- 传输队列面板
        self._transfer_table = QTableWidget(0, 4)
        self._transfer_table.setHorizontalHeaderLabels(['文件名', '进度', '速度', '状态'])
        hdr = self._transfer_table.horizontalHeader()
        # 前3列可拖拽调整，最后一列自动拉伸填满剩余空间（解决右侧空白和不自适应问题）
        for c in range(3):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)  # 状态列自动贴合右边界
        # 设置合理的默认列宽（仅对 Interactive 列生效）
        hdr.resizeSection(0, 280)   # 文件名（最长内容）
        hdr.resizeSection(1, 180)   # 进度（QProgressBar）
        hdr.resizeSection(2, 100)   # 速度
        # 禁止水平滚动条，所有列始终在可视区域内显示
        self._transfer_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._transfer_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._transfer_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._transfer_table.verticalHeader().setDefaultSectionSize(24)
        # 垂直滚动条仅在有内容超出时才显示，任务少时不会出现多余滚动条
        self._transfer_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._transfer_table.setFixedHeight(130)
        self._transfer_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._transfer_table.customContextMenuRequested.connect(self._on_transfer_context_menu)
        root.addWidget(self._transfer_table)

        # ---- 操作按钮栏
        btn_row = QHBoxLayout()
        self._btn_upload = QPushButton('上传 ▶')
        self._btn_upload.setAutoDefault(False)
        self._btn_upload.clicked.connect(self._upload_file)
        btn_row.addWidget(self._btn_upload)
        self._btn_download = QPushButton('◀ 下载')
        self._btn_download.setAutoDefault(False)
        self._btn_download.clicked.connect(self._download_file)
        btn_row.addWidget(self._btn_download)
        self._btn_delete = QPushButton('删除')
        self._btn_delete.setAutoDefault(False)
        self._btn_delete.clicked.connect(self._delete_selected)
        btn_row.addWidget(self._btn_delete)
        self._btn_mkdir = QPushButton('新建目录')
        self._btn_mkdir.setAutoDefault(False)
        self._btn_mkdir.clicked.connect(self._create_directory)
        btn_row.addWidget(self._btn_mkdir)
        self._btn_xftp = QPushButton('Xftp')
        self._btn_xftp.setAutoDefault(False)
        self._btn_xftp.clicked.connect(self._open_in_xftp)
        btn_row.addWidget(self._btn_xftp)
        btn_row.addStretch()
        self._lbl_status = QLabel('就绪')
        btn_row.addWidget(self._lbl_status)
        root.addLayout(btn_row)

        # ---- Ctrl+F 快捷键
        sc = QShortcut(QKeySequence('Ctrl+F'), self)
        sc.activated.connect(self._on_search_shortcut)
        esc = QShortcut(QKeySequence('Escape'), self)
        esc.activated.connect(self._hide_search_boxes)

    # ------------------------------------------------------------------ 连接
    def _connect_and_list(self):
        """异步建立 SFTP 连接，不阻塞主线程"""
        self._lbl_status.setText('正在连接...')
        # 加载本地目录不依赖网络，可立即执行
        self._list_local(self._local_path)
        worker = SFTPConnectWorker(self._host, self._port, self._username, self._password)
        worker.connected.connect(self._on_sftp_connect_success)
        worker.error.connect(self._on_sftp_connect_error)
        self._connect_worker = worker
        worker.start()

    def _on_sftp_connect_success(self, transport):
        """异步 SFTP 连接成功回调"""
        self._transport = transport
        self._log(f'[SFTP] 已连接到 {self._host}:{self._port}')
        self._lbl_status.setText('已连接')
        self._list_remote(self._remote_path)
        self._cleanup_connect_worker()

    def _on_sftp_connect_error(self, error):
        """异步 SFTP 连接失败回调"""
        self._log(f'[SFTP] 连接失败: {error}')
        self._lbl_status.setText(f'连接失败: {error}')
        self._cleanup_connect_worker()

    def _cleanup_connect_worker(self):
        """非阻塞安全清理连接 worker"""
        if self._connect_worker is not None:
            w = self._connect_worker
            self._connect_worker = None
            if hasattr(w, 'abort'):
                w.abort()
            if w.isRunning():
                w.finished.connect(w.deleteLater)
            else:
                w.deleteLater()

    # ------------------------------------------------------------------ Worker 管理
    def _cleanup_list_worker(self):
        """非阻塞清理列目录 worker：断开信号后交由 deleteLater"""
        if self._list_worker is not None:
            w = self._list_worker
            self._list_worker = None
            # 断开所有信号，防止旧 worker 结果回调干扰
            try:
                w.result.disconnect()
            except Exception:
                pass
            try:
                w.error.disconnect()
            except Exception:
                pass
            try:
                w.finished.disconnect()
            except Exception:
                pass
            if w.isRunning():
                # 不阻塞主线程，等 finished 信号后再 deleteLater
                w.finished.connect(w.deleteLater)
                # 信号已断开，旧 worker 的 result/error 不会再到达，重置 _listing
                self._listing = False
            else:
                w.deleteLater()

    def _safe_delete_transfer_worker(self, tid):
        """非阻塞安全清理单个传输 worker"""
        info = self._transfer_workers.pop(tid, None)
        if info:
            w = info['worker']
            if w.isRunning():
                w.finished.connect(w.deleteLater)
            else:
                w.deleteLater()

    # ------------------------------------------------------------------ 远程列目录
    def _list_remote(self, path):
        if self._transport is None or not self._transport.is_active():
            if self._transport is not None:
                self._lbl_status.setText('连接已断开')
                self._log('[SFTP] Transport 已失效，请重新打开窗口')
                self._transport = None
            return
        # 防重入：如果正在列目录，暂存目标路径，等当前操作完成后自动执行
        if self._listing:
            self._pending_remote_path = path
            self._lbl_status.setText(f'等待加载: {path}')
            return
        # 非阻塞清理旧 worker（断开信号，不 wait）
        self._cleanup_list_worker()
        self._list_generation += 1
        gen = self._list_generation
        self._listing = True
        self._lbl_status.setText(f'加载中: {path}')
        worker = SFTPListWorker(self._transport, path)
        worker.result.connect(self._on_list_result)
        worker.error.connect(self._on_list_error)
        worker.finished.connect(self._on_list_worker_finished)
        # 在 worker 上记录 generation，用于回调中校验
        worker._list_gen = gen
        self._list_worker = worker
        worker.start()

    def _on_list_worker_finished(self):
        """列目录 worker 线程结束后的异步清理回调"""
        if self._list_worker is not None and not self._list_worker.isRunning():
            self._list_worker.deleteLater()
            self._list_worker = None

    def _on_list_result(self, path, entries):
        # 校验 generation，忽略过期 worker 的结果
        worker = self.sender()
        if worker and hasattr(worker, '_list_gen') and worker._list_gen != self._list_generation:
            return
        self._listing = False
        self._remote_path = path
        self._remote_entries = entries
        self._edit_remote_path.setText(path)
        self._populate_remote(entries)
        dirs = [e for e in entries if e['is_dir']]
        files = [e for e in entries if not e['is_dir']]
        self._lbl_status.setText(f'{len(dirs)} 个目录, {len(files)} 个文件')
        self._log(f'[SFTP] 目录加载完成: {path} ({len(dirs)} 目录, {len(files)} 文件)')
        # 处理挂起的导航请求
        self._process_pending_remote_path()

    def _on_list_error(self, error):
        worker = self.sender()
        if worker and hasattr(worker, '_list_gen') and worker._list_gen != self._list_generation:
            return
        self._listing = False
        self._lbl_status.setText(f'列表失败: {error}')
        self._log(f'[SFTP] 列表失败: {error}')
        # 处理挂起的导航请求（即使当前失败也要执行用户的新请求）
        self._process_pending_remote_path()

    def _process_pending_remote_path(self):
        """当前 listing 结束后，执行用户挂起的远程导航请求"""
        pending = self._pending_remote_path
        if pending is not None:
            self._pending_remote_path = None
            self._list_remote(pending)

    def _populate_remote(self, entries):
        self._tree.clear()
        dirs = sorted([e for e in entries if e['is_dir']], key=lambda x: x['name'])
        files = sorted([e for e in entries if not e['is_dir']], key=lambda x: x['name'])
        for entry in dirs + files:
            item = QTreeWidgetItem()
            prefix = '/ ' if entry['is_dir'] else ''
            item.setText(0, prefix + entry['name'])
            item.setText(1, self._format_size(entry['size']) if not entry['is_dir'] else '')
            item.setText(2, '目录' if entry['is_dir'] else '文件')
            item.setText(3, entry['perm'])
            item.setText(4, entry['mtime'])
            item.setData(0, Qt.ItemDataRole.UserRole, entry)
            self._tree.addTopLevelItem(item)

    # ------------------------------------------------------------------ 搜索
    def _on_search_shortcut(self):
        focus_right = self._right_panel and self._right_panel.isAncestorOf(self.focusWidget())
        focus_left = self._left_panel and self._left_panel.isAncestorOf(self.focusWidget())
        if focus_right or (not focus_left and self._right_panel is not None):
            self._remote_search_frame.show()
            self._remote_search_edit.setFocus()
        else:
            self._local_search_frame.show()
            self._local_search_edit.setFocus()

    def _hide_search_boxes(self):
        self._local_search_frame.hide()
        self._remote_search_frame.hide()

    def _on_remote_search(self):
        keyword = self._remote_search_edit.text().strip()
        if not keyword:
            return
        kw = keyword.lower()
        matched = [e for e in self._remote_entries if kw in e['name'].lower()]
        self._populate_remote(matched)
        self._lbl_status.setText(f'搜索完成，找到 {len(matched)} 个匹配项')

    def _on_local_search(self):
        keyword = self._local_search_edit.text().strip()
        if not keyword:
            return
        kw = keyword.lower()
        matched = [e for e in self._local_entries if kw in e['name'].lower()]
        self._populate_local(matched)
        self._lbl_status.setText(f'搜索完成，找到 {len(matched)} 个匹配项')

    def _populate_local(self, entries):
        self._local_tree.clear()
        dirs = sorted([e for e in entries if e['is_dir']], key=lambda x: x['name'].lower())
        files = sorted([e for e in entries if not e['is_dir']], key=lambda x: x['name'].lower())
        for entry in dirs + files:
            item = QTreeWidgetItem()
            prefix = '/ ' if entry['is_dir'] else ''
            item.setText(0, prefix + entry['name'])
            item.setText(1, self._format_size(entry['size']) if not entry['is_dir'] else '')
            item.setText(2, '目录' if entry['is_dir'] else '文件')
            item.setText(3, entry['mtime'])
            item.setData(0, Qt.ItemDataRole.UserRole, entry)
            self._local_tree.addTopLevelItem(item)

    # ------------------------------------------------------------------ 路径输入跳转
    def _on_local_path_entered(self):
        path = self._edit_local_path.text().strip()
        # 守卫：仅在路径实际发生变化时才导航，防止误触发
        if os.path.normcase(os.path.normpath(path)) == os.path.normcase(os.path.normpath(self._local_path)):
            return
        print(f'[SFTP] 本地路径导航: {self._local_path} -> {path}')
        if os.path.isdir(path):
            self._list_local(path)
        else:
            self._lbl_status.setText(f'本地路径不存在: {path}')

    def _on_remote_path_entered(self):
        path = self._edit_remote_path.text().strip()
        if path:
            print(f'[SFTP] 远程路径导航: {self._remote_path} -> {path}')
            self._list_remote(path)

    # ------------------------------------------------------------------ 本地列目录
    def _list_local(self, path):
        if not os.path.isdir(path):
            self._lbl_status.setText(f'本地路径无效: {path}')
            return
        self._local_path = path
        self._edit_local_path.setText(path)
        self._local_tree.clear()
        try:
            with os.scandir(path) as it:
                entries = list(it)
        except Exception as e:
            self._lbl_status.setText(f'读取本地目录失败: {e}')
            return
        self._local_entries = []
        dirs = sorted([e for e in entries if e.is_dir()], key=lambda x: x.name.lower())
        files = sorted([e for e in entries if e.is_file()], key=lambda x: x.name.lower())
        for entry in dirs + files:
            try:
                st = entry.stat()
            except Exception:
                continue
            is_dir = entry.is_dir()
            mtime = datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M') if st.st_mtime else ''
            edata = {
                'name': entry.name, 'is_dir': is_dir,
                'size': st.st_size if not is_dir else 0,
                'mtime': mtime, 'path': entry.path,
            }
            self._local_entries.append(edata)
        self._populate_local(self._local_entries)

    def _local_refresh(self):
        self._list_local(self._local_path)

    def _local_go_up(self):
        parent = os.path.dirname(self._local_path)
        if parent != self._local_path:
            self._list_local(parent)

    def _on_local_item_double_clicked(self, item, column):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        if data['is_dir']:
            self._list_local(data['path'])
        else:
            self._upload_file(data)

    # ------------------------------------------------------------------ 远程导航
    def _refresh(self):
        self._list_remote(self._remote_path)

    def _go_up(self):
        parent = '/'.join(self._remote_path.rstrip('/').split('/')[:-1])
        if not parent:
            parent = '/'
        self._list_remote(parent)

    def _on_item_double_clicked(self, item, column):
        if self._listing:
            return  # 正在加载目录，忽略双击
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if not entry:
            return
        if entry['is_dir']:
            new_path = self._remote_path.rstrip('/') + '/' + entry['name']
            self._list_remote(new_path)
        else:
            self._download_file(entry)

    # ------------------------------------------------------------------ 上传 / 下载
    def _upload_file(self, data=None):
        if not isinstance(data, dict):
            data = None
        if data is None:
            item = self._local_tree.currentItem()
            if not item:
                self._log('[SFTP] 请先在左侧本地面板选择一个文件或目录')
                return
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if not data:
                return
        if data['is_dir']:
            self._upload_dir(data)
            return
        local_path = data['path']
        remote_path = self._remote_path.rstrip('/') + '/' + data['name']
        file_size = os.path.getsize(local_path) if os.path.isfile(local_path) else 0
        self._log(f'[SFTP] 上传: {local_path} -> {remote_path}')
        worker = SFTPOperationWorker(self._conn_params, 'upload', local_path, remote_path, file_size=file_size)
        self._start_transfer_op(worker, data['name'], '上传', file_size)

    def _upload_dir(self, data):
        """上传整个本地目录到远程"""
        local_dir = data['path']
        dir_name = data['name']
        remote_dir = self._remote_path.rstrip('/') + '/' + dir_name
        self._log(f'[SFTP] 上传目录: {local_dir} -> {remote_dir}')
        worker = SFTPDirTransferWorker(self._conn_params, 'upload_dir',
                                       local_dir=local_dir, remote_dir=remote_dir, dir_name=dir_name)
        self._start_transfer_op(worker, f'[目录] {dir_name}', '上传', 0)

    def _download_file(self, entry=None):
        if not isinstance(entry, dict):
            entry = None
        if entry is None:
            item = self._tree.currentItem()
            if not item:
                self._log('[SFTP] 请先在右侧远程面板选择一个文件或目录')
                return
            entry = item.data(0, Qt.ItemDataRole.UserRole)
            if not entry:
                return
        if entry['is_dir']:
            self._download_dir(entry)
            return
        remote_path = self._remote_path.rstrip('/') + '/' + entry['name']
        local_path = os.path.join(self._local_path, entry['name'])
        file_size = entry.get('size', 0)
        self._log(f'[SFTP] 下载: {remote_path} -> {local_path}')
        worker = SFTPOperationWorker(self._conn_params, 'download', local_path, remote_path, file_size=file_size)
        self._start_transfer_op(worker, entry['name'], '下载', file_size)

    def _download_dir(self, entry):
        """下载整个远程目录到本地"""
        dir_name = entry['name']
        remote_dir = self._remote_path.rstrip('/') + '/' + dir_name
        local_dir = os.path.join(self._local_path, dir_name)
        self._log(f'[SFTP] 下载目录: {remote_dir} -> {local_dir}')
        worker = SFTPDirTransferWorker(self._conn_params, 'download_dir',
                                       local_dir=local_dir, remote_dir=remote_dir, dir_name=dir_name)
        self._start_transfer_op(worker, f'[目录] {dir_name}', '下载', 0)

    def _start_transfer_op(self, worker, filename, op_label, file_size):
        """启动传输任务，在传输队列表格中添加一行"""
        tid = self._next_transfer_id
        self._next_transfer_id += 1
        # 在表格中新增一行
        row = self._transfer_table.rowCount()
        self._transfer_table.insertRow(row)
        self._transfer_table.setItem(row, 0, QTableWidgetItem(f'{op_label}: {filename}'))
        # 进度条
        pb = QProgressBar()
        pb.setRange(0, 100)
        pb.setValue(0)
        self._transfer_table.setCellWidget(row, 1, pb)
        self._transfer_table.setItem(row, 2, QTableWidgetItem('0 B/s'))
        self._transfer_table.setItem(row, 3, QTableWidgetItem('传输中'))
        # 记录 worker 信息（含速度计算用的滑动窗口字段）
        now = time.time()
        info = {'worker': worker, 'row': row, 'start_time': now,
                'last_bytes': 0, 'last_time': now, 'speed': 0.0}
        self._transfer_workers[tid] = info
        # 连接信号
        worker.progress.connect(lambda t, tot, _tid=tid: self._on_transfer_progress(_tid, t, tot))
        worker.success.connect(lambda msg, _tid=tid: self._on_transfer_success(_tid, msg))
        worker.error.connect(lambda err, _tid=tid: self._on_transfer_error(_tid, err))
        worker.start()

    def _on_transfer_progress(self, tid, transferred, total):
        info = self._transfer_workers.get(tid)
        if not info:
            return
        row = info['row']
        pct = int(transferred * 100 / total) if total > 0 else 0
        pb = self._transfer_table.cellWidget(row, 1)
        if pb:
            pb.setValue(pct)
        # 滑动窗口计算实时速度（排除暂停期间）
        now = time.time()
        dt = now - info['last_time']
        if dt >= 0.5:
            db = transferred - info['last_bytes']
            info['speed'] = db / dt if db > 0 else 0.0
            info['last_bytes'] = transferred
            info['last_time'] = now
        speed_item = self._transfer_table.item(row, 2)
        if speed_item:
            speed_item.setText(f'{self._format_size(info["speed"])}/s')

    def _on_transfer_success(self, tid, msg):
        info = self._transfer_workers.get(tid)
        if info:
            row = info['row']
            pb = self._transfer_table.cellWidget(row, 1)
            if pb:
                pb.setValue(100)
            status_item = self._transfer_table.item(row, 3)
            if status_item:
                status_item.setText('完成')
        self._safe_delete_transfer_worker(tid)
        self._lbl_status.setText(msg)
        self._log(f'[SFTP] {msg}')
        self._list_remote(self._remote_path)
        self._list_local(self._local_path)

    def _on_transfer_error(self, tid, error):
        info = self._transfer_workers.get(tid)
        if info:
            row = info['row']
            status_item = self._transfer_table.item(row, 3)
            if status_item:
                status_item.setText(f'失败: {error}')
        self._safe_delete_transfer_worker(tid)
        self._lbl_status.setText(f'操作失败: {error}')
        self._log(f'[SFTP] 操作失败: {error}')

    # ------------------------------------------------------------------ 传输队列右键菜单
    def _on_transfer_context_menu(self, pos):
        """传输队列面板右键菜单"""
        menu = QMenu(self)
        row = self._transfer_table.rowAt(pos.y())
        has_selection = row >= 0
        has_tasks = self._transfer_table.rowCount() > 0

        # 判断选中行状态
        selected_status = ''
        if has_selection:
            status_item = self._transfer_table.item(row, 3)
            if status_item:
                selected_status = status_item.text()

        act_pause = menu.addAction('暂停')
        act_pause_all = menu.addAction('全部暂停')
        act_resume = menu.addAction('继续')
        act_resume_all = menu.addAction('全部继续')
        menu.addSeparator()
        act_delete = menu.addAction('删除')
        act_delete_all = menu.addAction('全部删除')

        # 启用/禁用逻辑
        act_pause.setEnabled(has_selection and selected_status == '传输中')
        act_pause_all.setEnabled(has_tasks)
        act_resume.setEnabled(has_selection and selected_status == '已暂停')
        act_resume_all.setEnabled(has_tasks)
        act_delete.setEnabled(has_selection)
        act_delete_all.setEnabled(has_tasks)

        action = menu.exec(self._transfer_table.viewport().mapToGlobal(pos))
        if action is None:
            return
        if action == act_pause:
            self._transfer_pause_row(row)
        elif action == act_pause_all:
            self._transfer_pause_all()
        elif action == act_resume:
            self._transfer_resume_row(row)
        elif action == act_resume_all:
            self._transfer_resume_all()
        elif action == act_delete:
            self._transfer_delete_row(row)
        elif action == act_delete_all:
            self._transfer_delete_all()

    def _find_tid_by_row(self, row):
        """根据表格行号查找对应的 transfer id"""
        for tid, info in self._transfer_workers.items():
            if info['row'] == row:
                return tid
        return None

    def _transfer_pause_row(self, row):
        """暂停指定行的传输任务"""
        tid = self._find_tid_by_row(row)
        if tid is None:
            return
        info = self._transfer_workers[tid]
        worker = info['worker']
        if hasattr(worker, 'pause'):
            worker.pause()
        status_item = self._transfer_table.item(row, 3)
        if status_item:
            status_item.setText('已暂停')
        name_item = self._transfer_table.item(row, 0)
        name = name_item.text() if name_item else f'任务{tid}'
        self._log(f'[SFTP] 已暂停传输: {name}')

    def _transfer_resume_row(self, row):
        """恢复指定行的传输任务"""
        tid = self._find_tid_by_row(row)
        if tid is None:
            return
        info = self._transfer_workers[tid]
        worker = info['worker']
        if hasattr(worker, 'resume'):
            worker.resume()
        # 重置速度计算基准，排除暂停期间的时间
        info['last_time'] = time.time()
        info['speed'] = 0.0
        status_item = self._transfer_table.item(row, 3)
        if status_item:
            status_item.setText('传输中')
        name_item = self._transfer_table.item(row, 0)
        name = name_item.text() if name_item else f'任务{tid}'
        self._log(f'[SFTP] 已继续传输: {name}')

    def _transfer_pause_all(self):
        """暂停所有传输中的任务"""
        count = 0
        for tid, info in list(self._transfer_workers.items()):
            row = info['row']
            if row < 0:
                continue
            status_item = self._transfer_table.item(row, 3)
            if status_item and status_item.text() == '传输中':
                worker = info['worker']
                if hasattr(worker, 'pause'):
                    worker.pause()
                status_item.setText('已暂停')
                count += 1
        if count:
            self._log(f'[SFTP] 已暂停全部传输 ({count} 个任务)')

    def _transfer_resume_all(self):
        """恢复所有已暂停的任务"""
        count = 0
        for tid, info in list(self._transfer_workers.items()):
            row = info['row']
            if row < 0:
                continue
            status_item = self._transfer_table.item(row, 3)
            if status_item and status_item.text() == '已暂停':
                worker = info['worker']
                if hasattr(worker, 'resume'):
                    worker.resume()
                # 重置速度计算基准
                info['last_time'] = time.time()
                info['speed'] = 0.0
                status_item.setText('传输中')
                count += 1
        if count:
            self._log(f'[SFTP] 已继续全部传输 ({count} 个任务)')

    def _transfer_delete_row(self, row):
        """删除指定行的传输任务"""
        tid = self._find_tid_by_row(row)
        name_item = self._transfer_table.item(row, 0)
        name = name_item.text() if name_item else f'任务{row}'
        if tid is not None:
            info = self._transfer_workers.get(tid)
            if info:
                worker = info['worker']
                if hasattr(worker, 'stop'):
                    worker.stop()
                self._safe_delete_transfer_worker(tid)
        self._transfer_table.removeRow(row)
        # 删除行后更新其他 worker 的 row 索引
        for t, inf in self._transfer_workers.items():
            if inf['row'] > row:
                inf['row'] -= 1
        self._log(f'[SFTP] 已删除传输: {name}')

    def _transfer_delete_all(self):
        """删除所有传输任务"""
        for tid in list(self._transfer_workers.keys()):
            info = self._transfer_workers.get(tid)
            if info:
                worker = info['worker']
                if hasattr(worker, 'stop'):
                    worker.stop()
                self._safe_delete_transfer_worker(tid)
        self._transfer_table.setRowCount(0)
        self._log('[SFTP] 已清空传输队列')

    # ------------------------------------------------------------------ 删除 / 新建目录
    def _delete_selected(self):
        item = self._tree.currentItem()
        if not item:
            self._log('[SFTP] 请先在右侧远程面板选择要删除的文件或目录')
            return
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if not entry:
            return
        remote_path = self._remote_path.rstrip('/') + '/' + entry['name']
        op = 'rmdir' if entry['is_dir'] else 'delete'
        self._log(f'[SFTP] 删除: {remote_path}')
        worker = SFTPOperationWorker(self._conn_params, op, '', remote_path)
        worker.success.connect(self._on_quick_op_success)
        worker.error.connect(self._on_quick_op_error)
        tid = self._next_transfer_id
        self._next_transfer_id += 1
        self._transfer_workers[tid] = {'worker': worker, 'row': -1, 'start_time': time.time()}
        worker.success.connect(lambda msg, _tid=tid: self._safe_delete_transfer_worker(_tid))
        worker.error.connect(lambda err, _tid=tid: self._safe_delete_transfer_worker(_tid))
        worker.start()

    def _create_directory(self):
        name, ok = QInputDialog.getText(self, '新建目录', '目录名:')
        if not ok or not name:
            return
        remote_path = self._remote_path.rstrip('/') + '/' + name
        self._log(f'[SFTP] 创建目录: {remote_path}')
        worker = SFTPOperationWorker(self._conn_params, 'mkdir', '', remote_path)
        worker.success.connect(self._on_quick_op_success)
        worker.error.connect(self._on_quick_op_error)
        tid = self._next_transfer_id
        self._next_transfer_id += 1
        self._transfer_workers[tid] = {'worker': worker, 'row': -1, 'start_time': time.time()}
        worker.success.connect(lambda msg, _tid=tid: self._safe_delete_transfer_worker(_tid))
        worker.error.connect(lambda err, _tid=tid: self._safe_delete_transfer_worker(_tid))
        worker.start()

    def _open_in_xftp(self):
        """使用 Xftp 打开当前 SFTP 连接"""
        if not shutil.which('xftp'):
            msg = "[提示] 未找到 Xftp，请确认已安装并加入系统 PATH"
            self._log(msg)
            QMessageBox.warning(self, "未找到 Xftp", msg)
            return
        xftp_url = f'sftp://{self._username}:{self._password}@{self._host}:{self._port}'
        try:
            subprocess.Popen(
                f'xftp -url "{xftp_url}"',
                shell=True,
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        except Exception as e:
            self._log(f"[提示] 启动 Xftp 失败: {e}")
            QMessageBox.warning(self, "打开失败", f"无法启动 Xftp：{e}")

    # ------------------------------------------------------------------ 回调
    def _on_quick_op_success(self, msg):
        self._lbl_status.setText(msg)
        self._log(f'[SFTP] {msg}')
        self._list_remote(self._remote_path)

    def _on_quick_op_error(self, error):
        self._lbl_status.setText(f'操作失败: {error}')
        self._log(f'[SFTP] 操作失败: {error}')

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def _format_size(size):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f'{size:.1f} {unit}' if unit != 'B' else f'{size} {unit}'
            size /= 1024
        return f'{size:.1f} TB'

    # ------------------------------------------------------------------ 右键菜单
    def _on_local_context_menu(self, pos):
        """本地面板右键菜单"""
        item = self._local_tree.itemAt(pos)
        menu = QMenu(self)
        if item:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if not data:
                return
            act_transfer = menu.addAction('传输（上传）')
            act_open = menu.addAction('打开')
            act_copy = menu.addAction('复制路径')
            act_rename = menu.addAction('重命名')
            act_delete = menu.addAction('删除')
            menu.addSeparator()
        # 新建子菜单（空白区域也可用）
        new_menu = menu.addMenu('新建')
        act_new_file = new_menu.addAction('新建文件')
        act_new_dir = new_menu.addAction('新建文件夹')

        action = menu.exec(self._local_tree.viewport().mapToGlobal(pos))
        if action is None:
            return
        if item and action == act_transfer:
            self._upload_file(data)
        elif item and action == act_open:
            self._ctx_local_open(data)
        elif item and action == act_copy:
            QApplication.clipboard().setText(data['path'])
            self._log(f'[SFTP] 已复制路径: {data["path"]}')
        elif item and action == act_rename:
            self._ctx_rename_local(data)
        elif item and action == act_delete:
            self._ctx_delete_local(data)
        elif action == act_new_file:
            self._ctx_new_file_local()
        elif action == act_new_dir:
            self._ctx_new_dir_local()

    def _on_remote_context_menu(self, pos):
        """远程面板右键菜单"""
        item = self._tree.itemAt(pos)
        menu = QMenu(self)
        if item:
            entry = item.data(0, Qt.ItemDataRole.UserRole)
            if not entry:
                return
            act_transfer = menu.addAction('传输（下载）')
            act_open = menu.addAction('打开')
            act_copy = menu.addAction('复制路径')
            act_rename = menu.addAction('重命名')
            act_delete = menu.addAction('删除')
            menu.addSeparator()
        # 新建子菜单
        new_menu = menu.addMenu('新建')
        act_new_file = new_menu.addAction('新建文件')
        act_new_dir = new_menu.addAction('新建文件夹')

        action = menu.exec(self._tree.viewport().mapToGlobal(pos))
        if action is None:
            return
        if item and action == act_transfer:
            self._download_file(entry)
        elif item and action == act_open:
            self._ctx_remote_open(entry)
        elif item and action == act_copy:
            remote_full = self._remote_path.rstrip('/') + '/' + entry['name']
            QApplication.clipboard().setText(remote_full)
            self._log(f'[SFTP] 已复制路径: {remote_full}')
        elif item and action == act_rename:
            self._ctx_rename_remote(entry)
        elif item and action == act_delete:
            self._ctx_delete_remote(entry)
        elif action == act_new_file:
            self._ctx_new_file_remote()
        elif action == act_new_dir:
            self._ctx_new_dir_remote()

    # ---- 右键菜单操作实现 ----
    def _get_temp_dir(self):
        """获取程序所在目录下的临时子目录，不存在则创建"""
        base = os.path.dirname(os.path.abspath(__file__))
        temp_dir = os.path.join(base, '_sftp_temp')
        os.makedirs(temp_dir, exist_ok=True)
        return temp_dir

    def _ctx_local_open(self, data):
        """用系统默认程序打开本地文件/文件夹"""
        path = data['path']
        try:
            os.startfile(path)
            self._log(f'[SFTP] 已打开: {path}')
        except OSError as e:
            self._log(f'[SFTP] 打开失败: {e}')

    def _ctx_remote_open(self, entry):
        """下载远程文件到临时目录后自动打开"""
        temp_dir = self._get_temp_dir()
        remote_path = self._remote_path.rstrip('/') + '/' + entry['name']
        if entry['is_dir']:
            local_dir = os.path.join(temp_dir, entry['name'])
            self._log(f'[SFTP] 下载目录并打开: {remote_path} -> {local_dir}')
            worker = SFTPDirTransferWorker(self._conn_params, 'download_dir',
                                           local_dir=local_dir, remote_dir=remote_path, dir_name=entry['name'])
            worker.success.connect(lambda msg, p=local_dir: self._open_after_download(p))
            self._start_transfer_op(worker, f'[打开] {entry["name"]}', '下载', 0)
        else:
            local_path = os.path.join(temp_dir, entry['name'])
            file_size = entry.get('size', 0)
            self._log(f'[SFTP] 下载并打开: {remote_path} -> {local_path}')
            worker = SFTPOperationWorker(self._conn_params, 'download', local_path, remote_path, file_size=file_size)
            worker.success.connect(lambda msg, p=local_path: self._open_after_download(p))
            self._start_transfer_op(worker, f'[打开] {entry["name"]}', '下载', file_size)

    def _open_after_download(self, path):
        """下载完成后用系统默认程序打开文件/文件夹"""
        try:
            os.startfile(path)
            self._log(f'[SFTP] 已打开: {path}')
        except OSError as e:
            self._log(f'[SFTP] 打开失败: {e}')

    def _ctx_rename_local(self, data):
        """重命名本地文件/文件夹"""
        new_name, ok = QInputDialog.getText(self, '重命名', '新名称:', text=data['name'])
        if not ok or not new_name or new_name == data['name']:
            return
        old_path = data['path']
        new_path = os.path.join(os.path.dirname(old_path), new_name)
        try:
            os.rename(old_path, new_path)
            self._log(f'[SFTP] 已重命名: {data["name"]} -> {new_name}')
            self._list_local(self._local_path)
        except PermissionError as e:
            self._log(f'[SFTP] 重命名失败（权限不足）: {e}')
        except OSError as e:
            self._log(f'[SFTP] 重命名失败: {e}')

    def _ctx_rename_remote(self, entry):
        """重命名远程文件/文件夹（通过独立 Transport 的 Worker 执行，避免与列目录并发）"""
        new_name, ok = QInputDialog.getText(self, '重命名', '新名称:', text=entry['name'])
        if not ok or not new_name or new_name == entry['name']:
            return
        old_path = self._remote_path.rstrip('/') + '/' + entry['name']
        new_path = self._remote_path.rstrip('/') + '/' + new_name
        self._log(f'[SFTP] 重命名: {old_path} -> {new_path}')
        # local_path 复用为 old_path
        worker = SFTPOperationWorker(self._conn_params, 'rename', old_path, new_path)
        worker.success.connect(self._on_quick_op_success)
        worker.error.connect(self._on_quick_op_error)
        tid = self._next_transfer_id
        self._next_transfer_id += 1
        self._transfer_workers[tid] = {'worker': worker, 'row': -1, 'start_time': time.time()}
        worker.success.connect(lambda msg, _tid=tid: self._safe_delete_transfer_worker(_tid))
        worker.error.connect(lambda err, _tid=tid: self._safe_delete_transfer_worker(_tid))
        worker.start()

    def _ctx_delete_local(self, data):
        """删除本地文件/文件夹（带确认）"""
        msg = f'确定要删除本地{"\u76ee\u5f55" if data["is_dir"] else "\u6587\u4ef6"} "{data["name"]}" 吗？'
        if data['is_dir']:
            msg = f'确定要删除本地目录 "{data["name"]}" 及其所有内容吗？'
        reply = QMessageBox.question(self, '确认删除', msg,
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        path = data['path']
        try:
            if data['is_dir']:
                shutil.rmtree(path)
            else:
                os.remove(path)
            self._log(f'[SFTP] 已删除本地: {path}')
            self._list_local(self._local_path)
        except PermissionError as e:
            self._log(f'[SFTP] 删除失败（权限不足）: {e}')
        except OSError as e:
            self._log(f'[SFTP] 删除失败: {e}')

    def _ctx_delete_remote(self, entry):
        """删除远程文件/文件夹（带确认）"""
        if entry['is_dir']:
            msg = f'确定要删除远程目录 "{entry["name"]}" 吗？\n注意：仅能删除空目录。'
        else:
            msg = f'确定要删除远程文件 "{entry["name"]}" 吗？'
        reply = QMessageBox.question(self, '确认删除', msg,
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        remote_path = self._remote_path.rstrip('/') + '/' + entry['name']
        op = 'rmdir' if entry['is_dir'] else 'delete'
        self._log(f'[SFTP] 删除: {remote_path}')
        worker = SFTPOperationWorker(self._conn_params, op, '', remote_path)
        worker.success.connect(self._on_quick_op_success)
        worker.error.connect(self._on_quick_op_error)
        tid = self._next_transfer_id
        self._next_transfer_id += 1
        self._transfer_workers[tid] = {'worker': worker, 'row': -1, 'start_time': time.time()}
        worker.success.connect(lambda msg, _tid=tid: self._safe_delete_transfer_worker(_tid))
        worker.error.connect(lambda err, _tid=tid: self._safe_delete_transfer_worker(_tid))
        worker.start()

    def _ctx_new_file_local(self):
        """在本地当前目录新建空文件"""
        name, ok = QInputDialog.getText(self, '新建文件', '文件名:')
        if not ok or not name:
            return
        path = os.path.join(self._local_path, name)
        try:
            open(path, 'w').close()
            self._log(f'[SFTP] 已创建本地文件: {path}')
            self._list_local(self._local_path)
        except PermissionError as e:
            self._log(f'[SFTP] 创建文件失败（权限不足）: {e}')
        except OSError as e:
            self._log(f'[SFTP] 创建文件失败: {e}')

    def _ctx_new_dir_local(self):
        """在本地当前目录新建文件夹"""
        name, ok = QInputDialog.getText(self, '新建文件夹', '文件夹名:')
        if not ok or not name:
            return
        path = os.path.join(self._local_path, name)
        try:
            os.makedirs(path, exist_ok=True)
            self._log(f'[SFTP] 已创建本地目录: {path}')
            self._list_local(self._local_path)
        except PermissionError as e:
            self._log(f'[SFTP] 创建目录失败（权限不足）: {e}')
        except OSError as e:
            self._log(f'[SFTP] 创建目录失败: {e}')

    def _ctx_new_file_remote(self):
        """在远程当前目录新建空文件（通过独立 Transport 的 Worker 执行）"""
        name, ok = QInputDialog.getText(self, '新建文件', '文件名:')
        if not ok or not name:
            return
        remote_path = self._remote_path.rstrip('/') + '/' + name
        self._log(f'[SFTP] 创建远程文件: {remote_path}')
        worker = SFTPOperationWorker(self._conn_params, 'create_file', '', remote_path)
        worker.success.connect(self._on_quick_op_success)
        worker.error.connect(self._on_quick_op_error)
        tid = self._next_transfer_id
        self._next_transfer_id += 1
        self._transfer_workers[tid] = {'worker': worker, 'row': -1, 'start_time': time.time()}
        worker.success.connect(lambda msg, _tid=tid: self._safe_delete_transfer_worker(_tid))
        worker.error.connect(lambda err, _tid=tid: self._safe_delete_transfer_worker(_tid))
        worker.start()

    def _ctx_new_dir_remote(self):
        """在远程当前目录新建文件夹"""
        name, ok = QInputDialog.getText(self, '新建文件夹', '文件夹名:')
        if not ok or not name:
            return
        remote_path = self._remote_path.rstrip('/') + '/' + name
        self._log(f'[SFTP] 创建远程目录: {remote_path}')
        worker = SFTPOperationWorker(self._conn_params, 'mkdir', '', remote_path)
        worker.success.connect(self._on_quick_op_success)
        worker.error.connect(self._on_quick_op_error)
        tid = self._next_transfer_id
        self._next_transfer_id += 1
        self._transfer_workers[tid] = {'worker': worker, 'row': -1, 'start_time': time.time()}
        worker.success.connect(lambda msg, _tid=tid: self._safe_delete_transfer_worker(_tid))
        worker.error.connect(lambda err, _tid=tid: self._safe_delete_transfer_worker(_tid))
        worker.start()

    # ------------------------------------------------------------------ 关闭
    def closeEvent(self, event):
        # 1. 先标记 transport 无效，阻止新操作
        transport = self._transport
        self._transport = None
        # 2. 清理异步连接 worker（中止重试）
        self._cleanup_connect_worker()
        # 3. 停止所有传输 worker
        for tid in list(self._transfer_workers.keys()):
            info = self._transfer_workers.get(tid)
            if info and hasattr(info['worker'], 'stop'):
                info['worker'].stop()
            self._safe_delete_transfer_worker(tid)
        # 4. 等待列目录 worker 结束（短超时，避免主线程永久阻塞）
        if self._list_worker is not None:
            w = self._list_worker
            self._list_worker = None
            try:
                w.result.disconnect()
                w.error.disconnect()
            except Exception:
                pass
            if w.isRunning():
                w.wait(2000)
            w.deleteLater()
        # 5. 最后关闭 transport（close+join 等待后台线程退出）
        _safe_close_transport(transport)
        if transport:
            self._log('[SFTP] 已断开连接')
        super().closeEvent(event)


class SSHConnectWorker(QThread):
    """异步建立 SSH 连接的工作线程（保持 client 存活，含自动重试）"""
    connected = Signal(object)   # 成功时发射 SSHClient 对象
    error = Signal(str)          # 失败时发射错误信息

    def __init__(self, host, port, username, password):
        super().__init__()
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self._abort = False

    def abort(self):
        """请求中止重试循环"""
        self._abort = True

    def run(self):
        for attempt in range(1, _RETRY_MAX + 1):
            if self._abort:
                return
            client = None
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(
                    self.host, port=self.port,
                    username=self.username, password=self.password,
                    timeout=10, banner_timeout=15, auth_timeout=15
                )
                self.connected.emit(client)
                return  # 成功，立即退出
            except Exception as e:
                # 安全关闭 transport 后台线程（close+join），避免线程残留导致 C 层崩溃
                if client:
                    try:
                        transport = client.get_transport()
                        _safe_close_transport(transport)
                    except Exception:
                        pass
                    try:
                        client.close()
                    except Exception:
                        pass
                err_msg = str(e)
                if any(kw in err_msg for kw in _RETRYABLE_KEYWORDS) and attempt < _RETRY_MAX:
                    print(f'[SSH] 连接失败 ({err_msg})，正在重试 ({attempt}/{_RETRY_MAX})...')
                    time.sleep(_RETRY_DELAY)
                    continue
                if self._abort:
                    return
                if attempt > 1:
                    self.error.emit(f'连接失败（已重试{_RETRY_MAX}次）: {err_msg}')
                else:
                    self.error.emit(err_msg)
                return

class SSHExecWorker(QThread):
    """异步执行 SSH 命令的工作线程（使用 exec_command，无持久 shell）"""
    output = Signal(str)
    error = Signal(str)
    # 注意：不能命名为 finished，会遮蔽 QThread 内置 finished 信号导致崩溃
    done = Signal()

    def __init__(self, client, command):
        super().__init__()
        self._client = client
        self._command = command

    def run(self):
        try:
            stdin, stdout, stderr = self._client.exec_command(self._command, timeout=30)
            out = stdout.read().decode('utf-8', errors='ignore')
            err = stderr.read().decode('utf-8', errors='ignore')
            if out:
                self.output.emit(out)
            if err:
                self.error.emit(err)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.done.emit()


class SSHTerminalWindow(QDialog):
    """SSH 终端窗口（exec_command 模式，底部输入框）"""

    def __init__(self, host, port, username, password, log_callback=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"SSH 终端 - {host}:{port}")
        self.resize(800, 500)
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._log = log_callback or (lambda msg: None)
        self._client = None
        self._connect_worker = None
        self._exec_worker = None
        self._init_ui()
        QTimer.singleShot(100, self._connect_ssh)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        # 输出区域（字体通过 setFont 设置，不在 QSS 中指定，以便全局字体变更能生效）
        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        self._output.setFont(QFont("Consolas", 10))
        self._output.setStyleSheet(
            "QPlainTextEdit { background-color: #1e1e1e; color: #00ff00; }"
        )
        layout.addWidget(self._output)

        # 输入区域
        input_layout = QHBoxLayout()
        self._prompt_label = QLabel("$")
        self._prompt_label.setFont(QFont("Consolas", 10))
        self._prompt_label.setStyleSheet("color: #00ff00;")
        input_layout.addWidget(self._prompt_label)

        self._input = QLineEdit()
        self._input.setFont(QFont("Consolas", 10))
        self._input.setStyleSheet(
            "QLineEdit { background-color: #2d2d2d; color: #00ff00; }"
        )
        self._input.setPlaceholderText("输入命令，回车执行...")
        self._input.returnPressed.connect(self._execute_command)
        self._input.setEnabled(False)  # 连接成功前禁用
        input_layout.addWidget(self._input)

        self._send_btn = QPushButton("执行")
        self._send_btn.clicked.connect(self._execute_command)
        self._send_btn.setEnabled(False)
        input_layout.addWidget(self._send_btn)

        self._cmd_btn = QPushButton("CMD")
        self._cmd_btn.clicked.connect(self._open_in_cmd)
        input_layout.addWidget(self._cmd_btn)

        self._xshell_btn = QPushButton("Xshell")
        self._xshell_btn.clicked.connect(self._open_in_xshell)
        input_layout.addWidget(self._xshell_btn)

        layout.addLayout(input_layout)

    def _connect_ssh(self):
        """异步建立 SSH 连接"""
        self._append_output(f"正在连接 {self._host}:{self._port} ...\n")
        worker = SSHConnectWorker(self._host, self._port, self._username, self._password)
        worker.connected.connect(self._on_connected)
        worker.error.connect(self._on_connect_error)
        self._connect_worker = worker
        worker.start()

    def _on_connected(self, client):
        """SSH 连接成功"""
        self._client = client
        self._cleanup_connect_worker()
        self._input.setEnabled(True)
        self._send_btn.setEnabled(True)
        self._input.setFocus()
        self._append_output("[连接成功] 请输入命令\n")

    def _on_connect_error(self, error):
        """SSH 连接失败"""
        self._cleanup_connect_worker()
        self._append_output(f"[连接失败] {error}\n")

    def _cleanup_connect_worker(self):
        """非阻塞清理连接 worker"""
        if self._connect_worker is not None:
            w = self._connect_worker
            self._connect_worker = None
            if hasattr(w, 'abort'):
                w.abort()
            if w.isRunning():
                w.finished.connect(w.deleteLater)
            else:
                w.deleteLater()

    def _execute_command(self):
        """执行输入的命令"""
        cmd = self._input.text().strip()
        if not cmd or self._client is None:
            return
        self._input.clear()
        self._append_output(f"$ {cmd}\n")

        # 清理上一个 exec worker
        if self._exec_worker is not None:
            if self._exec_worker.isRunning():
                return  # 上一个命令还在执行
            self._exec_worker = None

        worker = SSHExecWorker(self._client, cmd)
        worker.output.connect(self._on_output)
        worker.error.connect(self._on_error)
        worker.done.connect(self._on_exec_finished)
        self._exec_worker = worker
        worker.start()

    def _on_output(self, text):
        """命令标准输出"""
        self._append_output(text)

    def _on_error(self, text):
        """命令标准错误"""
        self._append_output(f"[错误] {text}")

    def _on_exec_finished(self):
        """命令执行完成"""
        w = self._exec_worker
        self._exec_worker = None
        if w is not None:
            w.deleteLater()
        self._append_output("---\n")
        self._input.setFocus()

    def _append_output(self, text):
        """追加文本到输出区域"""
        self._output.moveCursor(QTextCursor.End)
        self._output.insertPlainText(text)
        self._output.moveCursor(QTextCursor.End)

    def _open_in_cmd(self):
        """在系统 CMD 中打开 SSH 连接（交互式终端）"""
        if not shutil.which('ssh'):
            QMessageBox.warning(
                self, "未找到 SSH 客户端",
                "系统中未安装 OpenSSH 客户端。\n"
                "请在 Windows 设置 > 应用 > 可选功能 中安装 OpenSSH 客户端。"
            )
            return
        cmd = f'ssh -p {self._port} {self._username}@{self._host}'
        subprocess.Popen(['cmd', '/k', cmd], creationflags=subprocess.CREATE_NEW_CONSOLE)

    def _open_in_xshell(self):
        """使用 Xshell 打开 SSH 连接"""
        xshell_path = shutil.which('xshell') or shutil.which('Xshell')
        if not xshell_path:
            msg = "[提示] 未找到 Xshell，请确认已安装并加入系统 PATH"
            self._log(msg)
            QMessageBox.warning(self, "未找到 Xshell", msg)
            return
        xshell_url = f'ssh://{self._username}:{self._password}@{self._host}:{self._port}'
        try:
            # Xshell 是 GUI 程序，无需 shell=True 和 CREATE_NEW_CONSOLE
            subprocess.Popen([xshell_path, '-url', xshell_url])
        except Exception as e:
            self._log(f"[提示] 启动 Xshell 失败: {e}")
            QMessageBox.warning(self, "打开失败", f"无法启动 Xshell：{e}")

    def closeEvent(self, event):
        # 清理 exec worker
        if self._exec_worker is not None:
            w = self._exec_worker
            self._exec_worker = None
            if w.isRunning():
                w.finished.connect(w.deleteLater)
            else:
                w.deleteLater()
        # 清理 connect worker
        self._cleanup_connect_worker()
        # 关闭 SSH client（close+join 等待 transport 后台线程退出，避免 C 层崩溃）
        if self._client:
            try:
                transport = self._client.get_transport()
                _safe_close_transport(transport)
            except Exception:
                pass
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        super().closeEvent(event)



class MainWindow(FluentWindowBase):
    def __init__(self):
        super().__init__()
        # Fluent 风格自定义标题栏（无边框 + Mica 云母背景 + 主题自适应按钮）
        self.setTitleBar(FluentTitleBar(self))
        # 压缩标题栏高度：默认 48px → 32px，与菜单栏/工具栏形成紧凑顶部
        self.titleBar.setFixedHeight(32)

        # 主内容垂直布局：菜单栏 + 中心内容 + 状态栏
        # （顶部 32px 留给 Fluent 标题栏）
        self.vBoxLayout = QVBoxLayout()
        self.vBoxLayout.setContentsMargins(0, 32, 0, 0)
        self.vBoxLayout.setSpacing(0)
        self.hBoxLayout.addLayout(self.vBoxLayout)
        self.hBoxLayout.setStretchFactor(self.vBoxLayout, 1)

        # 构建 UI（centralwidget 挂到 vBoxLayout 内）
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        # 设置窗口标题（显示在 Fluent 标题栏上）
        self.setWindowTitle("AutoWork - 自动化工作工具")

        # 初始化UI（内部创建菜单栏/状态栏控件）
        self.init_ui()

        # 菜单栏插到内容最上方，状态栏追加到最下方
        self.vBoxLayout.insertWidget(0, self._menubar_widget)
        self.vBoxLayout.addWidget(self._statusbar_widget)

        # 连接信号和槽
        self.connect_signals()

        self.titleBar.raise_()
    
    # 默认路径配置，首次运行时自动写入 settings.json
    DEFAULT_PATHS = {
        "exe_dir": r"C:\Users\shen_zhe\Desktop\snooker\bin64",
        "videos_dir": r"C:\Users\shen_zhe\Desktop\videos",
        "cipher_tool": r"C:\Users\shen_zhe\Desktop\videos\AESBase64CipherTool.exe",
        "front_exe": r"C:\Users\shen_zhe\Desktop\snooker\win-unpacked\SnookerNewbvMaster.exe",
        "backend_exe": r"C:\Users\shen_zhe\Desktop\snooker\backend\SnookerServer.exe",
    }
    # 默认快捷键配置
    DEFAULT_SHORTCUTS = {
        "shortcut_flush": "F5",
        "shortcut_start": "Space",
        "shortcut_open_dir": "Ctrl+O",
    }
    # 默认高亮颜色（橙色）
    DEFAULT_HIGHLIGHT_COLOR = [220, 80, 20]

    @staticmethod
    def _get_app_dir():
        """获取应用程序所在目录（兼容 PyInstaller 打包后的路径）"""
        if getattr(sys, 'frozen', False):
            # PyInstaller 打包后，sys.executable 指向 .exe 文件
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.abspath(__file__))

    def _get_settings_path(self):
        """获取配置文件路径，与 main.py / .exe 同目录"""
        return os.path.join(self._get_app_dir(), "settings.json")

    def _reload_settings_cache(self):
        """从 settings.json 一次性加载到内存缓存"""
        path = self._get_settings_path()
        self._settings_cache = dict(self.DEFAULT_PATHS)  # 默认值作为基础
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    self._settings_cache.update(json.load(f))
            except Exception:
                pass

    def _load_settings(self):
        """返回缓存的配置（不再读磁盘，如需刷新请调用 _reload_settings_cache）"""
        if not hasattr(self, '_settings_cache'):
            self._reload_settings_cache()
        return self._settings_cache

    def _save_settings(self, data):
        """将配置写入 settings.json，同时更新内存缓存"""
        path = self._get_settings_path()
        try:
            self._load_settings()  # 确保缓存已初始化
            self._settings_cache.update(data)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self._settings_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._append_log(f"[警告] 保存配置失败: {e}")

    def _load_paths(self):
        """从配置加载路径，并设置实例属性"""
        settings = self._load_settings()
        self.exe_dir = settings.get("exe_dir", self.DEFAULT_PATHS["exe_dir"])
        self.videos_dir = settings.get("videos_dir", self.DEFAULT_PATHS["videos_dir"])
        self.cipher_tool = settings.get("cipher_tool", self.DEFAULT_PATHS["cipher_tool"])
        self.front_exe = settings.get("front_exe", self.DEFAULT_PATHS["front_exe"])
        self.backend_exe = settings.get("backend_exe", self.DEFAULT_PATHS["backend_exe"])
        # 确保首次运行时将默认路径写入 settings.json
        if not os.path.exists(self._get_settings_path()):
            self._save_settings(self.DEFAULT_PATHS)

    def _restore_exe_selection(self):
        """从配置文件恢复上次选择的程序"""
        settings = self._load_settings()
        saved_exe = settings.get("last_exe", "")
        if saved_exe:
            for i in range(self.ui.choose_exe.count()):
                if self.ui.choose_exe.itemText(i) == saved_exe:
                    self.ui.choose_exe.setCurrentIndex(i)
                    self._append_log(f"[配置] 已恢复上次程序: {saved_exe}")
                    return

    def init_ui(self):
        """初始化UI组件"""
        # 一次性加载 settings.json 到缓存（后续所有 _load_settings 都读缓存）
        self._reload_settings_cache()
        # 加载路径配置
        self._load_paths()
        # 初始化日志控件智能自动滚动
        self._init_log_auto_scroll()
        
        # 设置默认日期为昨天（addDays 自动处理跨月/跨年）
        from PySide6.QtCore import QDate
        yesterday = QDate.currentDate().addDays(-1)
        self.ui.date.blockSignals(True)
        self.ui.date.setDate(yesterday)
        self.ui.date.blockSignals(False)
        
        # 预热日历面板，避免首次点击弹出延迟
        self._warmup_calendar_view()
        
        # 初始化程序下拉框 - 扫描 snooker/bin64 目录下的 SnookerTracking*.exe
        self._load_exe_list()
        # 恢复上次选择的程序
        self._restore_exe_selection()
        
        # 初始化设备代码列表 - 扫描 videos 目录下的设备文件夹
        self._load_device_list()
        
        # 在日志区域显示欢迎信息
        self._append_log("欢迎使用 AutoWork 工具！")
        self._append_log(f"程序目录: {self.exe_dir}")
        self._append_log(f"视频目录: {self.videos_dir}")
        self._append_log("请选择程序并开始工作...")
        
        # 存储当前选中的视频和帧数
        self.current_video = None
        self.current_frame = None
        
        # 存储运行的程序进程
        self.running_process = None
        
        # 存储当前日志文件路径（用于右键菜单定位）
        self._current_log_path = None
        
        # 异步解码相关
        self._decode_process = None
        self._pending_exe_path = None
        self._pending_detect_json = None
        
        # 进程挂起状态
        self._process_suspended = False

        # 三端启动切换状态（按钮在“启动三端”/“关闭三端”间切换）
        self._three_running = False
        self._three_saved_mode = None  # 启动前捕获的原始分辨率，关闭时恢复
        
        # 初始化状态栏、右键菜单、快捷键、菜单栏
        self._init_statusbar()
        self._init_context_menus()
        self._init_menubar()
        self._init_shortcuts()
        # 从 settings.json 加载并应用用户自定义设置（高亮颜色、字号、字体等）
        self._apply_highlight_color()
        self._apply_font_size()
        self._apply_font_family()
        self._apply_theme()
        self._init_system_theme_monitor()
        self._apply_layout()
        # Fluent ComboBox 使用自定义弹出视图，无需 setView
        self.ui.choose_exe.setFixedWidth(190)  # 略宽于 SnookerTracking824.exe
        # 远程状态
        self._frpc_process = None
        self._p2p_visitors = []
        self._p2p_current_index = -1
        self._tcp_worker = None
        self._sftp_window = None
        self._ssh_terminal_window = None
        self._init_p2p_panel()

    # ==================== 日志智能自动滚动 ====================

    def _init_log_auto_scroll(self):
        """初始化日志控件的智能自动滚动功能"""
        self._log_at_bottom = True
        self._log_scroll_timer = QTimer(self)
        self._log_scroll_timer.setSingleShot(True)
        self._log_scroll_timer.setInterval(1000)
        self._log_scroll_timer.timeout.connect(self._scroll_log_to_bottom)
        self.ui.show_log.verticalScrollBar().valueChanged.connect(self._on_log_scroll_changed)

    def _is_log_at_bottom(self):
        """判断日志滚动条是否在底部（允许 2px 误差）"""
        sb = self.ui.show_log.verticalScrollBar()
        return sb.value() >= sb.maximum() - 2

    def _on_log_scroll_changed(self, value):
        """滚动条值变化时更新底部状态标志"""
        sb = self.ui.show_log.verticalScrollBar()
        self._log_at_bottom = (value >= sb.maximum() - 2)
        if self._log_at_bottom and self._log_scroll_timer.isActive():
            self._log_scroll_timer.stop()

    def _scroll_log_to_bottom(self):
        """将日志控件滚动到底部"""
        sb = self.ui.show_log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _append_log(self, text):
        """向日志控件追加文本，带智能自动滚动"""
        self.ui.show_log.appendPlainText(text)
        if self._log_at_bottom:
            # 用户在底部，立即滚动
            self._scroll_log_to_bottom()
        else:
            # 用户不在底部，启动/重置延迟滚动定时器（debounce）
            self._log_scroll_timer.start()

    def _show_info_bar(self, message, message_type="info", title=None, duration=2500):
        """弹出 Fluent InfoBar 消息条（右上角），与 _append_log 互不干涉。

        参数:
            message: 消息内容
            message_type: 'success' / 'info' / 'warning' / 'error'
            title: 标题（默认按类型自动生成）
            duration: 显示时长(ms)，<=0 表示常驻不自动关闭
        """
        if title is None:
            title = {'success': '成功', 'info': '提示',
                     'warning': '警告', 'error': '错误'}.get(message_type, '提示')
        kwargs = dict(
            title=title,
            content=message,
            parent=self,
            position=InfoBarPosition.BOTTOM_RIGHT,
            duration=duration,
        )
        factory = {
            'success': InfoBar.success,
            'info': InfoBar.info,
            'warning': InfoBar.warning,
            'error': InfoBar.error,
        }.get(message_type, InfoBar.info)
        factory(**kwargs)

    def _load_exe_list(self):
        """加载 snooker/bin64 目录下的 SnookerTracking*.exe 到程序下拉框"""
        import glob
        
        exe_dir = self.exe_dir
        if not os.path.exists(exe_dir):
            self._append_log(f"[警告] 目录不存在: {exe_dir}")
            return
        
        # 查找所有匹配的 exe 文件
        pattern = os.path.join(exe_dir, "*SnookerTracking*.exe")
        exe_files = glob.glob(pattern)
        
        if not exe_files:
            self._append_log(f"[警告] 未找到 SnookerTracking*.exe 文件")
            return
        
        # 清空并添加文件列表
        self.ui.choose_exe.clear()
        for exe_path in sorted(exe_files):
            exe_name = os.path.basename(exe_path)
            self.ui.choose_exe.addItem(exe_name)
        # 限制下拉列表最多显示 8 项，超出自动滚动
        self.ui.choose_exe.setMaxVisibleItems(8)
        
        self._append_log(f"[程序] 找到 {len(exe_files)} 个可执行文件")

    def _load_device_list(self):
        """加载 videos 目录下的设备代码文件夹到 id_list"""
        videos_dir = self.videos_dir
        if not os.path.exists(videos_dir):
            self._append_log(f"[警告] 目录不存在: {videos_dir}")
            return
        
        # 获取所有子目录（设备代码）
        device_codes = []
        for item in os.listdir(videos_dir):
            item_path = os.path.join(videos_dir, item)
            if os.path.isdir(item_path):
                device_codes.append(item)
        
        if not device_codes:
            self._append_log(f"[警告] videos 目录下没有找到设备文件夹")
            return
        
        # 清空并添加设备代码列表（自然排序：数字段按数值比较）
        self.ui.id_list.clear()
        for code in sorted(device_codes, key=_natural_sort_key):
            self.ui.id_list.addItem(code)
        
        self._append_log(f"[设备] 找到 {len(device_codes)} 个设备代码")

    # ==================== 设备列表搜索 (Ctrl+F) ====================

    def _on_id_search_shortcut(self):
        """Ctrl+F：切换设备搜索框的显示/隐藏"""
        if self.ui.id_search.isVisible():
            self._hide_id_search()
        else:
            self.ui.id_search.show()
            self.ui.id_search.setFocus()

    def _hide_id_search(self):
        """隐藏设备搜索框，清空内容并恢复完整设备列表"""
        self.ui.id_search.blockSignals(True)
        self.ui.id_search.clear()
        self.ui.id_search.blockSignals(False)
        self.ui.id_search.hide()
        # 恢复所有项可见
        for i in range(self.ui.id_list.count()):
            self.ui.id_list.item(i).setHidden(False)

    def _on_id_search_changed(self, text):
        """实时过滤设备列表：不区分大小写子串匹配，用 setHidden 控制显隐（不重建列表）"""
        kw = text.strip().lower()
        for i in range(self.ui.id_list.count()):
            item = self.ui.id_list.item(i)
            item.setHidden(bool(kw) and kw not in item.text().lower())

    def _warmup_calendar_view(self):
        """预热日历面板：缓存 CalendarView 实例并复用，
        避免每次点击都重新创建（原实现每次 new 一个导致 0.5s+ 延迟）"""
        try:
            from qfluentwidgets.components.date_time.calendar_view import CalendarView
            from PySide6.QtCore import QPoint

            picker = self.ui.date
            # 创建缓存实例（关闭时不销毁，以便复用）
            cached_view = CalendarView(self.window())
            cached_view.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
            cached_view.hide()

            def _fast_show_calendar_view():
                import warnings
                cached_view.setResetEnabled(picker.isRestEnabled())
                # 重新连接信号（先断开旧连接防止重复）
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    cached_view.resetted.disconnect()
                    cached_view.dateChanged.disconnect()
                cached_view.resetted.connect(picker.reset)
                cached_view.dateChanged.connect(picker._onDateChanged)

                if picker.date.isValid():
                    cached_view.setDate(picker.date)

                x = int(picker.width() / 2 - cached_view.sizeHint().width() / 2)
                y = picker.height()
                cached_view.exec(picker.mapToGlobal(QPoint(x, y)))

            # 替换原始方法
            picker._showCalendarView = _fast_show_calendar_view
            # 保存引用防止 GC
            picker._cached_calendar_view = cached_view
        except Exception:
            pass

    def _get_selected_date_str(self):
        """获取日期选择器中的日期，格式如 2026-07-05"""
        qdate = self.ui.date.date
        date_str = qdate.toString("yyyy-MM-dd")
        return date_str

    def _load_videos_for_device(self, device_code):
        """根据设备代码和选中日期加载日志文件到 loacl_video_list（第二列）
        
        查找路径：
        1. videos/{device_code}/{date_str}/ 下的 *.txt, *.log（原有逻辑）
        2. videos/{device_code}/ 根目录下文件名以 YYYYMMDD_ 开头的 *.txt, *.log（新增）
        """
        import glob
        
        videos_dir = self.videos_dir
        device_dir = os.path.join(videos_dir, device_code)
        
        if not os.path.exists(device_dir):
            self._append_log(f"[警告] 设备目录不存在: {device_dir}")
            return
        
        # 获取选中日期，构建日期子目录路径
        date_str = self._get_selected_date_str()
        date_dir = os.path.join(device_dir, date_str)
        
        # 清空第二列
        self.ui.loacl_video_list.clear()
        
        log_files = []
        
        # 路径1：日期子目录下的 txt 和 log 文件
        if os.path.exists(date_dir):
            log_files += glob.glob(os.path.join(date_dir, '*.txt'))
            log_files += glob.glob(os.path.join(date_dir, '*.log'))
        
        # 路径2：设备根目录下文件名以 YYYYMMDD_ 开头的日志文件
        # 将 2025-11-28 转换为 20251128 前缀进行匹配
        date_prefix = date_str.replace('-', '')  # e.g. "20251128"
        for fname in os.listdir(device_dir):
            fpath = os.path.join(device_dir, fname)
            if not os.path.isfile(fpath):
                continue
            if not (fname.endswith('.txt') or fname.endswith('.log')):
                continue
            # 匹配 YYYYMMDD_ 前缀
            if fname.startswith(date_prefix + '_'):
                # 避免与日期子目录中已找到的文件重复（按文件名去重）
                if not any(os.path.basename(p) == fname for p in log_files):
                    log_files.append(fpath)
        
        if not log_files:
            self._append_log(f"[提示] {device_code} 下没有 {date_str} 的日志 (查找路径: {date_dir} 及设备根目录)")
            self._show_info_bar(f"{device_code} 下未找到 {date_str} 的日志", "warning")
            return
        
        for log_path in sorted(log_files):
            # 只显示文件名，如 20260705_131009.log
            self.ui.loacl_video_list.addItem(os.path.basename(log_path))
        
        self._append_log(f"[日志目录] {device_code}/{date_str} 下有 {len(log_files)} 个日志文件")

    def _load_logs_for_device(self, device_code):
        """初始化第三列为空，等待点击日志后展示内容"""
        # 第三列初始化为空，点击第二列的日志项后才填充内容
        self.ui.log_list.clear()

    def connect_signals(self):
        """连接信号和槽"""
        # 按钮点击事件
        self.ui.flush.clicked.connect(self.on_flush_clicked)
        self.ui.start.clicked.connect(self.on_start_clicked)
        self.ui.end.clicked.connect(self.on_end_clicked)
        self.ui.open_daily.clicked.connect(self.on_open_daily_clicked)
        self.ui.write_table.clicked.connect(self.on_open_dir_clicked)
        self.ui.open_config.clicked.connect(lambda: QTimer.singleShot(0, self.on_open_config_clicked))
        self.ui.pause_btn.clicked.connect(self._on_pause_clicked)
        # 列表项选择事件
        self.ui.id_list.currentItemChanged.connect(self._on_id_current_changed)
        self.ui.loacl_video_list.currentItemChanged.connect(self._on_video_current_changed)
        self.ui.log_list.itemClicked.connect(self.on_log_selected)
        self.ui.log_list.itemDoubleClicked.connect(self.on_log_double_clicked)
        
        # 日期改变时重新加载第二列
        self.ui.date.dateChanged.connect(self._on_date_changed)
        
        # 程序下拉框切换时自动保存选择
        self.ui.choose_exe.currentTextChanged.connect(self._on_exe_changed)
        
        # 右键菜单信号
        self.ui.id_list.customContextMenuRequested.connect(self._id_list_context_menu)
        self.ui.log_list.customContextMenuRequested.connect(self._log_list_context_menu)
        self.ui.loacl_video_list.customContextMenuRequested.connect(self._loacl_video_list_context_menu)
        
        # 远程面板信号
        self.ui.p2p_btn.toggled.connect(self._on_p2p_toggled)
        self.ui.p2p_add_btn.clicked.connect(self._on_p2p_add)
        self.ui.p2p_delete_btn.clicked.connect(self._on_p2p_delete)
        self.ui.p2p_connect_btn.clicked.connect(self._on_p2p_connect)
        self.ui.p2p_disconnect_btn.clicked.connect(self._on_p2p_disconnect)
        self.ui.p2p_visitor_list.currentRowChanged.connect(self._on_p2p_visitor_selected)
        self.ui.p2p_mode_combo.currentIndexChanged.connect(self._on_p2p_mode_changed)
        self.ui.p2p_sftp_btn.clicked.connect(self._on_sftp_btn_clicked)
        self.ui.p2p_ssh_terminal_btn.clicked.connect(self._on_ssh_terminal_btn_clicked)

        # 启动三端按钮
        self.ui.start_three_btn.clicked.connect(self.on_start_three_clicked)

        # 设备列表实时搜索（搜索框控件在 autowork_with_table.py 中创建）
        self.ui.id_search.textChanged.connect(self._on_id_search_changed)
        # Ctrl+F 切换设备搜索框显示/隐藏，Esc 隐藏
        self._id_search_sc = QShortcut(QKeySequence('Ctrl+F'), self)
        self._id_search_sc.activated.connect(self._on_id_search_shortcut)
        self._id_search_esc = QShortcut(QKeySequence('Escape'), self)
        self._id_search_esc.activated.connect(self._hide_id_search)

    @Slot()
    def on_flush_clicked(self):
        """刷新按钮点击事件"""
        self._append_log("\n[操作] 刷新数据...")
        
        # 先记住当前选中的设备代码和程序
        current_device = self.ui.id_list.currentItem()
        saved_device_code = current_device.text() if current_device else None
        saved_exe = self.ui.choose_exe.currentText()
        
        # 1. 重新扫描可执行程序下拉框
        self._load_exe_list()
        # 恢复程序选择
        for i in range(self.ui.choose_exe.count()):
            if self.ui.choose_exe.itemText(i) == saved_exe:
                self.ui.choose_exe.setCurrentIndex(i)
                break
        
        # 2. 重新扫描设备列表（第一列）
        self._load_device_list()
        
        # 3. 恢复之前选中的设备，并重新加载其日志目录（第二列）
        if saved_device_code:
            # 在刷新后的列表中找回该设备
            for i in range(self.ui.id_list.count()):
                if self.ui.id_list.item(i).text() == saved_device_code:
                    self.ui.id_list.setCurrentItem(self.ui.id_list.item(i))
                    self._load_videos_for_device(saved_device_code)
                    break
            self.ui.log_list.clear()
        
        self._append_log("[刷新] 完成")
        self._show_info_bar("数据刷新完成", "success")
        
    @Slot()
    def on_start_clicked(self):
        """播放按钮点击事件 - 启动 SnookerTracking 程序"""
        # 如果已经有程序在运行，先结束它
        if self.running_process is not None:
            self._append_log("\n[警告] 已有程序正在运行，请先点击'结束'")
            self._show_info_bar("已有程序正在运行，请先点击'结束'", "warning")
            return
        
        # 获取选中的程序
        exe_name = self.ui.choose_exe.currentText()
        if not exe_name:
            QMessageBox.warning(self, "警告", "请先选择程序！")
            return
        
        exe_dir = self.exe_dir
        exe_path = os.path.join(exe_dir, exe_name)
        if not os.path.exists(exe_path):
            QMessageBox.warning(self, "警告", f"程序不存在: {exe_path}")
            return
        
        # 使用 QProcess 启动程序
        self.running_process = QProcess()
        self.running_process.setWorkingDirectory(exe_dir)
        
        # 连接信号以捕获输出
        self.running_process.readyReadStandardOutput.connect(self._on_program_output)
        self.running_process.readyReadStandardError.connect(self._on_program_error)
        self.running_process.finished.connect(self._on_program_finished)
        
        # 启动前准备 detect.json
        self._pending_exe_path = exe_path
        need_decode = self._prepare_detect_json()
        
        if need_decode:
            # 解码进行中，启动将在 _on_decode_finished 中继续
            self._append_log(f"\n[播放] 等待 detect.json 解码完成后启动...")
            self._update_status_running(exe_name)
        else:
            # 无需解码，直接启动
            self._launch_program(exe_path, exe_name, exe_dir)
    
    def _on_program_output(self):
        """处理程序的标准输出"""
        if self.running_process:
            output = self.running_process.readAllStandardOutput().data().decode('gb2312', errors='ignore')
            if output.strip():
                self._append_log(output.strip())
    
    def _on_program_error(self):
        """处理程序的错误输出"""
        if self.running_process:
            error = self.running_process.readAllStandardError().data().decode('gb2312', errors='ignore')
            if error.strip():
                self._append_log(f"[程序错误] {error.strip()}")
    
    def _on_program_finished(self, exit_code, exit_status):
        """程序结束时回调"""
        self._append_log(f"\n[程序结束] 退出码: {exit_code}")
        self.running_process = None
        self._process_suspended = False
        self.ui.pause_btn.setText("暂停")
        self._update_status_idle()
        
    @Slot()
    def on_end_clicked(self):
        """结束按钮点击事件 - 停止运行的程序"""
        if self.running_process is None:
            self._append_log("\n[提示] 没有正在运行的程序")
            self._show_info_bar("没有正在运行的程序", "warning")
            return
        
        # 直接强制终止进程
        self._append_log("\n[结束] 正在终止程序...")
        self.running_process.kill()  # 强制终止
        self._append_log("[结束] 程序已强制终止")
        self._show_info_bar("程序已终止", "info")
        self.running_process = None
        self._process_suspended = False
        self.ui.pause_btn.setText("暂停")
        self._update_status_idle()
        
    @Slot()
    def on_start_three_clicked(self):
        """启动三端按钮点击事件 - 在启动三端 / 关闭三端之间切换"""
        if self._three_running:
            self._stop_three_programs()
        else:
            self._start_three_programs()

    def _start_three_programs(self):
        """启动三端：捕获分辨率 → 修改 cfg.json → 错峰启动 → 按钮变为“关闭三端”"""
        # 识别端路径：复用当前 exe_dir + choose_exe 下拉框选中项
        exe_name = self.ui.choose_exe.currentText()
        if not exe_name:
            QMessageBox.warning(self, "警告", "请先在工具栏“程序”下拉框中选择识别端程序！")
            return
        tracking_path = os.path.join(self.exe_dir, exe_name)

        # 三端路径列表（启动顺序：识别端 → 后端 → 前端）
        programs = [
            ("识别端", tracking_path),
            ("后端", self.backend_exe),
            ("前端", self.front_exe),
        ]

        # 启动前逐一检查路径是否存在
        missing = [(name, path) for name, path in programs if not os.path.exists(path)]
        if missing:
            detail = "\n".join(f"  • {name}: {path}" for name, path in missing)
            QMessageBox.warning(self, "程序缺失", f"以下程序路径不存在，无法启动：\n{detail}")
            return

        # 1. 捕获当前主屏幕分辨率（后端启动后会强制 1080p，关闭时恢复此分辨率）
        self._three_saved_mode = self._capture_current_resolution()
        if self._three_saved_mode:
            _, (w, h, freq, bits) = self._three_saved_mode
            self._append_log(f"[启动三端] 已捕获当前分辨率: {w}x{h} @ {freq}Hz, {bits}bit（关闭时将自动恢复）")

        # 2. 修改 cfg.json：skip_ready_check true → false
        self._set_skip_ready_check(False)

        # 3. 标记运行中，按钮切换为“关闭三端”
        self._three_running = True
        self.ui.start_three_btn.setText("关闭三端")

        # 4. 依次错峰启动三个进程（每个间隔 3 秒），避免同时启动造成资源竞争。
        #    使用 QTimer.singleShot 而非 time.sleep，防止阻塞 UI 主线程。
        interval_ms = 3000
        attr_names = ["_tracking_process", "_backend_process", "_front_process"]
        self._append_log("\n[启动三端] 将依次启动（每个间隔 3 秒）：")
        for i, ((name, path), attr) in enumerate(zip(programs, attr_names)):
            delay = i * interval_ms
            # lambda 用默认参数固化循环变量，避免闭包延迟绑定问题
            QTimer.singleShot(delay, lambda checked=False, n=name, p=path, a=attr:
                              self._start_one_program(n, p, a))
            self._append_log(f"  {i + 1}. {name}: {path}（{delay // 1000} 秒后启动）")
        self._show_info_bar("三端程序将依次启动（间隔 3 秒）", "success")

    def _stop_three_programs(self):
        """关闭三端：结束进程 → 恢复 cfg.json → 恢复分辨率 → 按钮变为“启动三端”"""
        # 1. 标记为非运行（同时阻止尚未触发的错峰启动定时器），按钮还原
        self._three_running = False
        self.ui.start_three_btn.setText("启动三端")

        # 2. 结束三个进程
        self._append_log("\n[关闭三端] 正在结束三端程序...")
        for attr, name in [("_tracking_process", "识别端"),
                           ("_backend_process", "后端"),
                           ("_front_process", "前端")]:
            process = getattr(self, attr, None)
            if process is not None:
                if process.state() != QProcess.NotRunning:
                    process.kill()
                    process.waitForFinished(1000)
                    self._append_log(f"  已结束 {name}")
                setattr(self, attr, None)

        # 3. 恢复 cfg.json：skip_ready_check false → true
        self._set_skip_ready_check(True)

        # 4. 恢复启动前捕获的分辨率（延迟 500ms，等待后端完全释放显示模式）
        saved = self._three_saved_mode
        self._three_saved_mode = None
        if saved:
            _, (w, h, freq, bits) = saved
            QTimer.singleShot(500, lambda checked=False, m=saved: self._restore_resolution(m))
            self._append_log(f"[关闭三端] 0.5 秒后恢复分辨率为 {w}x{h} @ {freq}Hz")

        self._show_info_bar("三端程序已关闭", "success")

    def _start_one_program(self, name, path, attr_name):
        """启动单个外部程序并保存 QProcess 引用防止 GC 回收"""
        # 若用户在错峰等待期间已点击“关闭三端”，则不再启动
        if not self._three_running:
            return
        process = QProcess(self)
        process.setWorkingDirectory(os.path.dirname(path))
        process.start(path)
        setattr(self, attr_name, process)
        self._append_log(f"[启动三端] 已启动 {name}: {path}")

    def _capture_current_resolution(self):
        """捕获当前主屏幕显示模式。
        返回 (DEVMODE 原始字节快照, (宽, 高, 刷新率, 色深)) 或 None。
        保存完整 DEVMODE 快照而非仅 4 个标量：恢复时可原样回传驱动报告的
        全部字段（dmFields/dmDisplayFlags 等），避免非整数刷新率（如 239.99Hz
        被读作 239）重建模式时与驱动实际支持的模式不匹配。"""
        if sys.platform != 'win32':
            return None
        try:
            dm = DEVMODE()
            dm.dmSize = ctypes.sizeof(DEVMODE)
            if _EnumDisplaySettingsW(None, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)) == 0:
                self._append_log("[分辨率] 读取当前显示模式失败")
                return None
            # 完整拷贝 DEVMODE 原始字节，防止后续被复用篡改
            snapshot = ctypes.string_at(ctypes.addressof(dm), ctypes.sizeof(DEVMODE))
            info = (dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency, dm.dmBitsPerPel)
            return (snapshot, info)
        except Exception as e:
            self._append_log(f"[分辨率] 捕获失败: {e}")
            return None

    def _restore_resolution(self, mode):
        """恢复启动前捕获的显示模式。mode = (DEVMODE 字节快照, (宽, 高, 刷新率, 色深))。
        按优先级逐级尝试：①完整快照原样恢复 → ②枚举支持模式找最接近刷新率 →
        ③仅恢复分辨率+色深（刷新率交驱动默认）→ ④传 NULL 恢复注册表默认。"""
        if sys.platform != 'win32' or not mode:
            return
        try:
            snapshot, (width, height, freq, bits) = mode

            # ① 优先：用启动前捕获的完整 DEVMODE 快照原样恢复（保留全部原始字段）
            dm = DEVMODE()
            ctypes.memmove(ctypes.addressof(dm), snapshot, ctypes.sizeof(DEVMODE))
            dm.dmSize = ctypes.sizeof(DEVMODE)  # 确保结构体尺寸字段正确
            ret = _ChangeDisplaySettingsW(ctypes.byref(dm), CDS_UPDATEREGISTRY | CDS_FULLSCREEN)
            if ret == DISP_CHANGE_SUCCESSFUL:
                self._append_log(f"[分辨率] 已恢复为 {width}x{height} @ {freq}Hz")
                return
            self._append_log(f"[分辨率] 完整模式恢复返回 {ret}，尝试枚举支持模式寻找最佳匹配...")

            # ② 枚举全部支持模式，找分辨率/色深一致且刷新率最接近的（处理 239↔240 取整差异）
            best = self._find_best_mode(width, height, bits, freq)
            if best is not None:
                best.dmSize = ctypes.sizeof(DEVMODE)
                ret = _ChangeDisplaySettingsW(ctypes.byref(best), CDS_UPDATEREGISTRY | CDS_FULLSCREEN)
                if ret == DISP_CHANGE_SUCCESSFUL:
                    self._append_log(f"[分辨率] 已恢复为 {best.dmPelsWidth}x{best.dmPelsHeight} "
                                     f"@ {best.dmDisplayFrequency}Hz（最佳匹配）")
                    return
                self._append_log(f"[分辨率] 最佳匹配模式恢复返回 {ret}")

            # ③ 仅恢复分辨率 + 色深，刷新率交给驱动默认
            dm2 = DEVMODE()
            dm2.dmSize = ctypes.sizeof(DEVMODE)
            dm2.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_BITSPERPEL
            dm2.dmPelsWidth = width
            dm2.dmPelsHeight = height
            dm2.dmBitsPerPel = bits
            ret = _ChangeDisplaySettingsW(ctypes.byref(dm2), CDS_UPDATEREGISTRY | CDS_FULLSCREEN)
            if ret == DISP_CHANGE_SUCCESSFUL:
                self._append_log(f"[分辨率] 已恢复为 {width}x{height}（刷新率为驱动默认）")
                return

            # ④ 兜底：传 NULL 恢复注册表中的默认模式
            self._append_log(f"[分辨率] 恢复返回 {ret}，尝试恢复系统默认模式...")
            _ChangeDisplaySettingsW(None, CDS_UPDATEREGISTRY | CDS_FULLSCREEN)
        except Exception as e:
            self._append_log(f"[分辨率] 恢复失败: {e}")

    def _find_best_mode(self, width, height, bits, freq):
        """枚举主屏幕全部支持的显示模式，返回分辨率/色深匹配且刷新率与目标最接近的 DEVMODE；
        找不到返回 None。用于处理非整数刷新率（239.99Hz 读作 239）与驱动实际支持值（240）的取整差异。"""
        try:
            best = None
            best_score = None
            i = 0
            while True:
                dm = DEVMODE()
                dm.dmSize = ctypes.sizeof(DEVMODE)
                if _EnumDisplaySettingsW(None, i, ctypes.byref(dm)) == 0:
                    break
                i += 1
                if dm.dmPelsWidth != width or dm.dmPelsHeight != height:
                    continue
                if bits and dm.dmBitsPerPel != bits:
                    continue
                # 刷新率差值越小越好；差值相同优先更高刷新率
                score = (abs(dm.dmDisplayFrequency - freq), -dm.dmDisplayFrequency)
                if best_score is None or score < best_score:
                    best_score = score
                    best = dm
            return best
        except Exception as e:
            self._append_log(f"[分辨率] 枚举显示模式失败: {e}")
            return None

    def _set_skip_ready_check(self, value):
        """修改 cfg.json 中的 skip_ready_check 开关（true=跳过就绪检查，false=执行就绪检查）"""
        cfg_path = os.path.join(self.exe_dir, "cfg.json")
        if not os.path.exists(cfg_path):
            self._append_log(f"[警告] cfg.json 不存在: {cfg_path}")
            return
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            cfg.setdefault("sys", {})["skip_ready_check"] = value
            with open(cfg_path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            self._append_log(f"[配置] cfg.json skip_ready_check 已设为 {str(value).lower()}")
        except Exception as e:
            self._append_log(f"[错误] 修改 skip_ready_check 失败: {e}")

    @Slot()
    def on_open_daily_clicked(self):
        """打开 CPP 日志文件"""
        # 检查是否选中了设备
        if not self.ui.id_list.currentItem():
            self._append_log("[提示] 请先选择设备代码")
            self._show_info_bar("请先选择设备代码", "warning")
            return
        
        device_code = self.ui.id_list.currentItem().text()
        date_str = self._get_selected_date_str()
        daily_path = os.path.join(
            self.videos_dir, device_code, f"daily_{date_str}.txt"
        )
        
        if not os.path.exists(daily_path):
            self._append_log(f"[提示] CPP 日志文件不存在: {daily_path}")
            self._show_info_bar(f"CPP 日志文件不存在: {daily_path}", "warning")
            return
        
        # 用系统默认程序打开文件
        os.startfile(daily_path)
        self._append_log(f"[CPP日志] 已打开: {daily_path}")
        
    @Slot()
    def on_open_dir_clicked(self):
        """打开目录按钮点击事件 - 打开当前选中设备的目录"""
        if not self.ui.id_list.currentItem():
            self._append_log("[提示] 请先选择设备代码")
            self._show_info_bar("请先选择设备代码", "warning")
            return
        
        device_code = self.ui.id_list.currentItem().text()
        device_dir = os.path.join(self.videos_dir, device_code)
        
        if not os.path.exists(device_dir):
            self._append_log(f"[提示] 目录不存在: {device_dir}")
            self._show_info_bar("目录不存在", "warning")
            return
        
        os.startfile(device_dir)
        self._append_log(f"[打开目录] {device_dir}")
    
    @Slot()
    def on_open_config_clicked(self):
        """配置按钮点击事件 - 选择打开 settings.json / cfg.json / frpc_xtcp.toml"""
        msg = QMessageBox(self)
        msg.setWindowTitle("打开配置文件")
        msg.setText("选择要打开的配置文件：")
        settings_btn = msg.addButton("settings.json", QMessageBox.ActionRole)
        cfg_btn = msg.addButton("cfg.json", QMessageBox.ActionRole)
        frpc_btn = msg.addButton("frpc_xtcp.toml", QMessageBox.ActionRole)
        msg.addButton(QMessageBox.Cancel)
        msg.exec()
        
        clicked = msg.clickedButton()
        if clicked == settings_btn:
            path = self._get_settings_path()
        elif clicked == cfg_btn:
            path = os.path.join(self.exe_dir, "cfg.json")
        elif clicked == frpc_btn:
            path = os.path.join(self._get_app_dir(), "frpc_xtcp.toml")
        else:
            return
        
        if not os.path.exists(path):
            self._append_log(f"[配置] 文件不存在: {path}")
            return
        
        os.startfile(path)
        self._append_log(f"[配置] 已打开: {path}")
        
    @Slot()
    def _on_id_current_changed(self, current, previous):
        """第一列当前项改变（鼠标点击/键盘导航均触发）"""
        if current is not None:
            self.on_id_selected(current)

    def on_id_selected(self, item):
        """ID列表项选中事件 - 加载对应设备的日志目录"""
        device_code = item.text()
        self._append_log(f"\n[设备选中] {device_code}")
        self._show_info_bar(f"设备选中：{device_code}", "success")
        self._update_status_device(device_code)
        
        # 加载该设备下的日志目录到第二列
        self._load_videos_for_device(device_code)
        # 清空第三列
        self._load_logs_for_device(device_code)
        
    def _on_video_current_changed(self, current, previous):
        """第二列当前项改变（鼠标点击/键盘导航均触发）"""
        if current is not None:
            self.on_video_selected(current)

    @Slot()
    def on_video_selected(self, item):
        """日志目录项选中事件 - 在第三列展示日志内容"""
        log_filename = item.text()
        self._append_log(f"\n[日志选中] {log_filename}")
        
        # 获取当前选中的设备代码
        if not self.ui.id_list.currentItem():
            return
        device_code = self.ui.id_list.currentItem().text()
        
        # 拼接完整路径：优先从日期子目录查找，找不到则尝试设备根目录
        date_str = self._get_selected_date_str()
        full_log_path = os.path.join(self.videos_dir, device_code, date_str, log_filename)
        if not os.path.exists(full_log_path):
            # 日志文件可能直接放在设备根目录下（YYYYMMDD_HHMMSS 命名）
            alt_path = os.path.join(self.videos_dir, device_code, log_filename)
            if os.path.exists(alt_path):
                full_log_path = alt_path
        
        # 读取日志文件内容并显示在第三列
        try:
            with open(full_log_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            self._current_log_path = full_log_path
            self.ui.log_list.clear()
            highlight_patterns = [r'返回', r'add']
            for line in content.splitlines():
                item = QListWidgetItem(line)
                if any(re.search(p, line) for p in highlight_patterns):
                    item.setForeground(QBrush(self.highlight_color))
                self.ui.log_list.addItem(item)
            
            line_count = len(content.splitlines())
            self._append_log(f"[日志内容] 已加载 {line_count} 行")
            self._show_info_bar(f"日志已加载 {line_count} 行", "success")
            self._update_status_logs(line_count)
        except Exception as e:
            self._append_log(f"[错误] 无法读取日志文件: {str(e)}")
            self._show_info_bar(f"无法读取日志文件: {e}", "error")
            self.ui.log_list.clear()
            self._current_log_path = None
        
    def _get_frame_input_value(self):
        """获取输入框中的帧数值，默认 400"""
        try:
            return int(self.ui.input_frame.text().strip())
        except (ValueError, AttributeError):
            return 400

    def _compute_video_start_frame(self, log_frame_id):
        """根据单选按钮模式计算 video_start_frame
        - 帧前: log_frame_id - 输入值
        - 帧数: log_frame_id
        - 自定义: 输入值
        """
        if self.ui.input_frame_before.isChecked():
            offset = self._get_frame_input_value()
            result = log_frame_id - offset
            if result < 0:
                self._append_log(
                    f"  [警告] 帧前偏移后起始帧为负值({result})，已修正为 0。"
                    f"log_frame_id={log_frame_id}, offset={offset}")
                result = 0
            self._append_log(f"  [模式] 帧前: {log_frame_id} - {offset} = {result}")
            return result
        elif self.ui.input_frame_set.isChecked():
            offset = self._get_frame_input_value()
            result = log_frame_id + offset
            self._append_log(f"  [模式] 帧后: {log_frame_id} + {offset} = {result}")
            return result
        elif self.ui.input_frame_custom.isChecked():
            custom = self._get_frame_input_value()
            self._append_log(f"  [模式] 自定义: {custom}")
            return custom
        else:
            offset = self._get_frame_input_value()
            result = log_frame_id - offset
            if result < 0:
                self._append_log(
                    f"  [警告] 帧前偏移后起始帧为负值({result})，已修正为 0。"
                    f"log_frame_id={log_frame_id}, offset={offset}")
                result = 0
            return result

    def _launch_program(self, exe_path, exe_name, exe_dir):
        """实际启动主程序（在 detect.json 准备好之后调用）"""
        # 刷新 cfg.json（应用当前单选按钮模式）
        if self.current_video and self.current_frame is not None:
            video_start_frame = self._compute_video_start_frame(self.current_frame)
            self._update_cfg_json(self.current_video, video_start_frame)
        
        # 启动程序
        self.running_process.start(exe_path)
        self._append_log(f"\n[播放] 已启动程序: {exe_name}")
        self._append_log(f"  - 工作目录: {exe_dir}")
        self._show_info_bar(f"已启动程序: {exe_name}", "success")
        self._update_status_running(exe_name)

    def _on_decode_output(self):
        """处理解码程序的标准输出"""
        if self._decode_process:
            output = self._decode_process.readAllStandardOutput().data().decode('gb2312', errors='ignore')
            if output.strip():
                self._append_log(f"[detect] {output.strip()}")

    def _on_decode_error(self):
        """处理解码程序的错误输出"""
        if self._decode_process:
            error = self._decode_process.readAllStandardError().data().decode('gb2312', errors='ignore')
            if error.strip():
                self._append_log(f"[detect] {error.strip()}")

    def _on_decode_finished(self, exit_code, exit_status):
        """解码完成后回调：复制 detect.json 并启动主程序"""
        self._decode_process = None
        
        detect_json_path = self._pending_detect_json
        exe_path = self._pending_exe_path
        exe_name = os.path.basename(exe_path)
        exe_dir = os.path.dirname(exe_path)
        
        if exit_code != 0:
            self._append_log(f"[detect] 解码失败，退出码: {exit_code}")
            self._pending_exe_path = None
            self._pending_detect_json = None
            self._update_status_idle()
            return
        
        # 验证解码结果
        if not os.path.exists(detect_json_path):
            self._append_log(f"[detect] 警告: 解码后未生成 detect.json")
            self._pending_exe_path = None
            self._pending_detect_json = None
            self._update_status_idle()
            return
        
        # 复制 detect.json 到程序目录
        target_path = os.path.join(self.exe_dir, "detect.json")
        try:
            shutil.copy2(detect_json_path, target_path)
            self._append_log(f"[detect] 已更新 detect.json -> {target_path}")
        except Exception as e:
            self._append_log(f"[detect] 复制失败: {e}")
        
        self._pending_exe_path = None
        self._pending_detect_json = None
        
        # 继续启动主程序
        self._launch_program(exe_path, exe_name, exe_dir)

    def _prepare_detect_json(self):
        """准备 detect.json：解密并复制到程序目录。返回 True 表示正在异步解码，返回 False 表示已同步完成或跳过。"""
        # 检查是否选中了设备
        if not self.ui.id_list.currentItem():
            self._append_log("[detect] 未选中设备，跳过 detect.json 处理")
            return False
        
        device_code = self.ui.id_list.currentItem().text()
        device_dir = os.path.join(self.videos_dir, device_code)
        detect_json_path = os.path.join(device_dir, "detect.json")
        detect_bin_path = os.path.join(device_dir, "detect.bin")
        
        json_exists = os.path.exists(detect_json_path)
        bin_exists = os.path.exists(detect_bin_path)
        
        # 判断是否需要解码
        need_decode = False
        if not json_exists:
            if not bin_exists:
                self._append_log(f"[detect] 警告: {device_code} 下既没有 detect.json 也没有 detect.bin")
                return False
            self._append_log("[detect] detect.json 不存在，将从 detect.bin 解码")
            need_decode = True
        elif bin_exists:
            # 两者都存在，比较修改时间
            bin_mtime = os.path.getmtime(detect_bin_path)
            json_mtime = os.path.getmtime(detect_json_path)
            if bin_mtime > json_mtime:
                self._append_log("[detect] detect.bin 比 detect.json 更新，重新解码")
                need_decode = True
            else:
                self._append_log("[detect] detect.json 已是最新，无需重新解码")
        
        if need_decode:
            # 使用 QProcess 异步调用 AESBase64CipherTool.exe 解码
            cipher_tool = self.cipher_tool
            if not os.path.exists(cipher_tool):
                self._append_log(f"[detect] 警告: 解码工具不存在: {cipher_tool}")
                return False
            
            self._pending_detect_json = detect_json_path
            cmd = [cipher_tool, detect_bin_path, detect_json_path]
            self._append_log(f"[detect] 正在异步解码: {' '.join(cmd)}")
            
            self._decode_process = QProcess()
            self._decode_process.readyReadStandardOutput.connect(self._on_decode_output)
            self._decode_process.readyReadStandardError.connect(self._on_decode_error)
            self._decode_process.finished.connect(self._on_decode_finished)
            self._decode_process.start(cmd[0], cmd[1:])
            return True
        
        # 不需要解码，直接同步复制 detect.json 到程序目录
        target_path = os.path.join(self.exe_dir, "detect.json")
        try:
            shutil.copy2(detect_json_path, target_path)
            self._append_log(f"[detect] 已更新 detect.json -> {target_path}")
        except Exception as e:
            self._append_log(f"[detect] 复制失败: {e}")
        return False

    def _update_cfg_json(self, video_path, frame):
        """更新 cfg.json 配置文件"""
        cfg_path = os.path.join(self.exe_dir, "cfg.json")
        if not os.path.exists(cfg_path):
            self._append_log(f"[警告] cfg.json 不存在: {cfg_path}")
            return False
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            if 'cap' in cfg and 'file' in cfg['cap']:
                cfg['cap']['file']['path'] = video_path
                cfg['cap']['file']['video_start_frame'] = frame
            if 'path' in cfg:
                del cfg['path']
            if 'video_start_frame' in cfg:
                del cfg['video_start_frame']
            with open(cfg_path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            self._append_log(f"[配置] 已更新 cfg.json")
            self._append_log(f"  - 视频: {video_path}")
            self._append_log(f"  - 帧数: {frame}")
            return True
        except Exception as e:
            self._append_log(f"[错误] 更新 cfg.json 失败: {str(e)}")
            import traceback
            self._append_log(traceback.format_exc())
            return False

    @Slot()
    def on_log_selected(self, item):
        """日志列表项选中事件 - 解析日志并更新cfg.json"""
        log_line = item.text()
        self._append_log(f"\n[日志选中] {log_line}")
        
        # 解析日志：提取帧数
        frame_match = re.search(r'frame_id:(\d+)', log_line)
        if not frame_match:
            self._append_log("[警告] 日志中未找到 frame_id")
            return
        
        log_frame_id = int(frame_match.group(1))
        self.current_frame = log_frame_id
        
        # 获取当前选中的设备代码
        if not self.ui.id_list.currentItem():
            self._append_log("[警告] 未选择设备代码")
            return
        
        # 从第二列获取当前选中的日志文件名，推断视频文件名
        if not self.ui.loacl_video_list.currentItem():
            self._append_log("[警告] 未选择日志文件")
            return
        
        log_filename = self.ui.loacl_video_list.currentItem().text()
        video_name = os.path.splitext(log_filename)[0] + '.mp4'
        # 视频查找路径：优先 videos/videos/，其次 videos/{device_code}/
        video_path_primary = os.path.join(self.videos_dir, "videos", video_name)
        device_code = self.ui.id_list.currentItem().text()
        video_path_device = os.path.join(self.videos_dir, device_code, video_name)
        # 并行探测两个候选路径（exists 为 I/O 型 syscall，线程可真正重叠执行）
        with ThreadPoolExecutor(max_workers=2) as pool:
            primary_exists = pool.submit(os.path.exists, video_path_primary)
            device_exists = pool.submit(os.path.exists, video_path_device)
            if primary_exists.result():
                video_path = video_path_primary.replace(os.sep, '/')
            elif device_exists.result():
                video_path = video_path_device.replace(os.sep, '/')
            else:
                # 都不存在时保持原有默认路径（videos/videos/）
                video_path = video_path_primary.replace(os.sep, '/')
        self.current_video = video_path
        
        # 根据单选按钮模式计算实际起始帧
        video_start_frame = self._compute_video_start_frame(log_frame_id)
        
        # 更新 cfg.json
        self._update_cfg_json(video_path, video_start_frame)

    def _on_date_changed(self, date):
        """日期改变时重新加载第二列日志列表"""
        current_device = self.ui.id_list.currentItem()
        if current_device:
            device_code = current_device.text()
            self._load_videos_for_device(device_code)
            self.ui.log_list.clear()

    def _on_exe_changed(self, exe_name):
        """程序下拉框改变时保存选择到配置文件"""
        if exe_name:
            self._save_settings({"last_exe": exe_name})
            self._append_log(f"[配置] 已保存程序选择: {exe_name}")
            self._show_info_bar(f"已保存程序选择: {exe_name}", "success")

    @Slot()
    def on_log_double_clicked(self, item):
        """日志列表项双击事件 - 解析日志、更新cfg.json并启动程序"""
        # 如果已有程序在运行，先自动结束旧程序
        if self.running_process is not None:
            self._append_log("\n[双击] 检测到已有程序运行，自动结束旧程序...")
            self.on_end_clicked()
        
        # 先触发选中逻辑（更新cfg.json）
        self.on_log_selected(item)
        
        # 然后启动播放
        self.on_start_clicked()

    # ==================== 状态栏 ====================

    def _set_dark_titlebar(self):
        """Windows 深色标题栏适配（DWM API）"""
        if sys.platform != 'win32':
            return
        try:
            import ctypes
            hwnd = int(self.winId())
            # Windows 10/11 DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            value = ctypes.c_int(1)
            _DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(value), ctypes.sizeof(value)
            )
            # Windows 11 DWMWA_WINDOW_CORNER_PREFERENCE = 33 (圆角)
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            preference = ctypes.c_int(2)  # DWMWCP_ROUND = 2
            _DwmSetWindowAttribute(
                hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(preference), ctypes.sizeof(preference)
            )
        except Exception:
            pass

    def _init_statusbar(self):
        """初始化底部状态栏（自定义控件，替代 QMainWindow.statusBar）"""
        self._statusbar_widget = QWidget()
        self._statusbar_widget.setObjectName(u"statusbar_widget")
        self._statusbar_widget.setFixedHeight(24)
        _sb_layout = QHBoxLayout(self._statusbar_widget)
        _sb_layout.setContentsMargins(8, 0, 8, 0)
        _sb_layout.setSpacing(0)
        self._status_message = QLabel("")
        _sb_layout.addWidget(self._status_message, 1)
        self.status_device = QLabel("设备: 未选择")
        self.status_state = QLabel("状态: 空闲")
        self.status_logs = QLabel("日志: 0 行")
        _sb_layout.addWidget(self.status_device)
        _sb_layout.addWidget(QLabel(" | "))
        _sb_layout.addWidget(self.status_state)
        _sb_layout.addWidget(QLabel(" | "))
        _sb_layout.addWidget(self.status_logs)
        self._show_status_message("就绪", 3000)

    def _show_status_message(self, msg, timeout=0):
        """在状态栏左侧显示临时消息（timeout>0 时自动清除）"""
        self._status_message.setText(msg)
        if timeout > 0:
            QTimer.singleShot(timeout, lambda: (
                self._status_message.setText("")
                if self._status_message.text() == msg else None))

    def _update_status_device(self, device_code):
        self.status_device.setText(f"设备: {device_code}")
        self.ui.log_status_device.setText(f"设备: {device_code}")

    def _update_status_running(self, exe_name):
        self.status_state.setText(f"状态: 运行中 - {exe_name}")

    def _update_status_idle(self):
        self.status_state.setText("状态: 空闲")

    def _update_status_paused(self, exe_name):
        self.status_state.setText(f"状态: 已暂停 - {exe_name}")

    def _update_status_logs(self, count):
        self.status_logs.setText(f"日志: {count} 行")
        self.ui.log_status_count.setText(f"日志: {count} 条")

    # ==================== 暂停/恢复 ====================

    @Slot()
    def _on_pause_clicked(self):
        """暂停按钮点击事件 - 挂起/恢复外部进程"""
        self._toggle_process_suspend()

    def _toggle_process_suspend(self):
        """切换外部进程的挂起/恢复状态"""
        if self.running_process is None:
            return
        state = self.running_process.state()
        if state != QProcess.Running:
            return

        pid = int(self.running_process.processId())
        if self._process_suspended:
            # 恢复进程
            if self._win_resume_process(pid):
                self._process_suspended = False
                self.ui.pause_btn.setText("暂停")
                self._append_log("[播放] 程序已恢复")
                exe_name = self.ui.choose_exe.currentText()
                self._update_status_running(exe_name)
            else:
                self._append_log("[警告] 恢复进程失败")
        else:
            # 挂起进程
            if self._win_suspend_process(pid):
                self._process_suspended = True
                self.ui.pause_btn.setText("恢复")
                self._append_log("[播放] 程序已暂停")
                self._show_info_bar("程序已暂停")
                exe_name = self.ui.choose_exe.currentText()
                self._update_status_paused(exe_name)
            else:
                self._append_log("[警告] 暂停进程失败")

    @staticmethod
    def _win_set_process_threads(pid, thread_action):
        """Windows API: 对指定进程的所有线程执行操作（挂起/恢复）

        参数:
            pid: 目标进程 ID
            thread_action: 对每个线程句柄调用的函数（_SuspendThread 或 _ResumeThread）
        返回:
            bool: 操作是否成功
        """
        PROCESS_SUSPEND_RESUME = 0x0800
        THREAD_SUSPEND_RESUME = 0x0002
        TH32CS_SNAPTHREAD = 0x00000004

        class THREADENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.c_ulong),
                ("cntUsage", ctypes.c_ulong),
                ("th32ThreadID", ctypes.c_ulong),
                ("th32OwnerProcessID", ctypes.c_ulong),
                ("tpBasePri", ctypes.c_long),
                ("tpDeltaPri", ctypes.c_long),
                ("dwFlags", ctypes.c_ulong),
            ]

        h_process = _OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
        if not h_process:
            return False
        try:
            snap = _CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
            if snap == -1:
                return False
            te = THREADENTRY32()
            te.dwSize = ctypes.sizeof(THREADENTRY32)
            if _Thread32First(snap, ctypes.byref(te)):
                while True:
                    if te.th32OwnerProcessID == pid:
                        h_thread = _OpenThread(THREAD_SUSPEND_RESUME, False, te.th32ThreadID)
                        if h_thread:
                            thread_action(h_thread)
                            _CloseHandle(h_thread)
                    if not _Thread32Next(snap, ctypes.byref(te)):
                        break
            _CloseHandle(snap)
            return True
        finally:
            _CloseHandle(h_process)

    @staticmethod
    def _win_suspend_process(pid):
        """Windows API: 挂起指定进程的所有线程"""
        return MainWindow._win_set_process_threads(pid, _SuspendThread)

    @staticmethod
    def _win_resume_process(pid):
        """Windows API: 恢复指定进程的所有线程"""
        return MainWindow._win_set_process_threads(pid, _ResumeThread)

    # ==================== 右键菜单 ====================

    def _init_context_menus(self):
        """为列表控件设置自定义右键菜单策略"""
        self.ui.id_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.ui.log_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.ui.loacl_video_list.setContextMenuPolicy(Qt.CustomContextMenu)

    def _id_list_context_menu(self, pos):
        """设备列表右键菜单（Fluent RoundMenu）"""
        menu = RoundMenu(parent=self)
        action_open_dir = Action(FluentIcon.FOLDER, "打开目录")
        action_cpp_log = Action(FluentIcon.DOCUMENT, "查看 CPP 日志")
        action_open_dir.triggered.connect(self.on_open_dir_clicked)
        action_cpp_log.triggered.connect(self.on_open_daily_clicked)
        menu.addAction(action_open_dir)
        menu.addAction(action_cpp_log)
        menu.exec(self.ui.id_list.mapToGlobal(pos), aniType=MenuAnimationType.DROP_DOWN)

    def _log_list_context_menu(self, pos):
        """日志内容列表右键菜单（Fluent RoundMenu）"""
        item = self.ui.log_list.currentItem()
        if item is None:
            return
        menu = RoundMenu(parent=self)
        action_copy = Action(FluentIcon.COPY, "复制此行")
        action_copy_frame = Action(FluentIcon.LIBRARY, "复制帧数")
        action_locate = Action(FluentIcon.PEOPLE, "在文件管理器中定位")

        def _do_copy():
            QApplication.clipboard().setText(item.text())
            self._show_status_message("已复制到剪贴板", 2000)
            self._append_log("[复制] 已复制当前行文本到剪贴板")

        def _do_copy_frame():
            frame_match = re.search(r'frame_id:(\d+)', item.text())
            if frame_match:
                frame_id = frame_match.group(1)
                QApplication.clipboard().setText(frame_id)
                self._show_status_message(f"帧数 {frame_id} 已复制到剪贴板", 2000)
                self._append_log(f"[复制] 帧数 {frame_id} 已复制到剪贴板")
            else:
                self._append_log("[提示] 当前行未找到 frame_id")

        def _do_locate():
            if self._current_log_path and os.path.exists(self._current_log_path):
                subprocess.run(['explorer', '/select,', self._current_log_path])
            else:
                self._append_log("[提示] 无法定位日志文件")

        action_copy.triggered.connect(_do_copy)
        action_copy_frame.triggered.connect(_do_copy_frame)
        action_locate.triggered.connect(_do_locate)
        menu.addAction(action_copy)
        menu.addAction(action_copy_frame)
        menu.addSeparator()
        menu.addAction(action_locate)
        menu.exec(self.ui.log_list.mapToGlobal(pos), aniType=MenuAnimationType.DROP_DOWN)

    def _loacl_video_list_context_menu(self, pos):
        """日志文件列表右键菜单（Fluent RoundMenu）"""
        item = self.ui.loacl_video_list.currentItem()
        if item is None:
            return
        menu = RoundMenu(parent=self)
        action_copy_name = Action(FluentIcon.COPY, "复制视频名")

        def _do_copy_name():
            pure_name = os.path.splitext(item.text())[0]
            QApplication.clipboard().setText(pure_name)
            self._show_status_message(f"文件名 {pure_name} 已复制到剪贴板", 2000)
            self._append_log(f"[复制] 文件名 {pure_name} 已复制到剪贴板")

        action_copy_name.triggered.connect(_do_copy_name)
        menu.addAction(action_copy_name)
        menu.exec(self.ui.loacl_video_list.mapToGlobal(pos), aniType=MenuAnimationType.DROP_DOWN)

    # ==================== 远程连接 ====================

    def _init_p2p_panel(self):
        """初始化远程面板状态，从已有的 frpc_xtcp.toml 恢复 visitor 列表"""
        # 从 settings.json 恢复 SSH 凭据（不再硬编码在 UI 文件中）
        settings = self._load_settings()
        ssh_user = settings.get("ssh_user", "")
        ssh_pass = settings.get("ssh_pass", "")
        if ssh_user:
            self.ui.p2p_ssh_user.setText(ssh_user)
        if ssh_pass:
            self.ui.p2p_ssh_pass.setText(ssh_pass)
        self._load_visitors_from_toml()
        self._refresh_p2p_list()
        # 初始化表单 bindPort 为随机值
        self.ui.p2p_form_port.setValue(self._get_new_random_port())
        self._update_p2p_visibility()
        self._update_p2p_buttons()

    def _load_visitors_from_toml(self):
        """从已有的 frpc_xtcp.toml 解析 [[visitors]] 段恢复 visitor 列表"""
        import re
        toml_path = os.path.join(self._get_app_dir(), "frpc_xtcp.toml")
        if not os.path.exists(toml_path):
            return
        try:
            with open(toml_path, 'r', encoding='utf-8') as f:
                content = f.read()
            # 按 [[visitors]] 分割，跳过全局头部
            blocks = content.split('[[visitors]]')
            for block in blocks[1:]:  # 第一段是全局头部，跳过
                visitor = {}
                m_server = re.search(r'serverName\s*=\s*"([^"]+)"', block)
                m_key = re.search(r'secretKey\s*=\s*"([^"]+)"', block)
                m_port = re.search(r'bindPort\s*=\s*(\d+)', block)
                if m_server and m_port:
                    visitor["serverName"] = m_server.group(1)
                    visitor["secretKey"] = m_key.group(1) if m_key else "abc123"
                    visitor["bindPort"] = int(m_port.group(1))
                    self._p2p_visitors.append(visitor)
            if self._p2p_visitors:
                self._append_log(f"[远程] 从 TOML 恢复了 {len(self._p2p_visitors)} 个 visitor")
        except Exception as e:
            self._append_log(f"[远程] 解析 TOML 失败: {e}")

    def _get_new_random_port(self):
        """生成不冲突的随机端口（排除已添加 visitor 的端口）"""
        used_ports = {v["bindPort"] for v in self._p2p_visitors}
        return generate_random_port(exclude_ports=used_ports)

    def _on_p2p_toggled(self, checked):
        """切换远程面板显示/隐藏"""
        self.ui.p2p_panel.setVisible(checked)

    def _on_p2p_add(self):
        """添加新的 visitor 配置"""
        server_name = self.ui.p2p_form_server.text().strip()
        if not server_name:
            self._append_log("[远程] 请填写 serverName")
            return
        port = self.ui.p2p_form_port.value()
        # 检查端口是否与已有 visitor 冲突
        for i, v in enumerate(self._p2p_visitors):
            if v["bindPort"] == port and i != self._p2p_current_index:
                self._append_log(f"[远程] 端口 {port} 已被 {v['serverName']} 使用，请更换端口")
                return
        visitor = {
            "serverName": server_name,
            "bindPort": port,
            "secretKey": self.ui.p2p_form_key.text().strip() or "abc123"
        }
        self._p2p_visitors.append(visitor)
        # 刷新列表时阻塞信号，防止 currentRowChanged 触发 _save_current_form 覆盖数据
        self.ui.p2p_visitor_list.blockSignals(True)
        self._refresh_p2p_list()
        self.ui.p2p_visitor_list.blockSignals(False)
        # 更新当前索引为新项
        self._p2p_current_index = len(self._p2p_visitors) - 1
        self.ui.p2p_visitor_list.setCurrentRow(self._p2p_current_index)
        # 添加后更新表单端口为下一个随机值
        self.ui.p2p_form_port.setValue(self._get_new_random_port())

    def _on_p2p_delete(self):
        """删除当前选中的 visitor"""
        row = self.ui.p2p_visitor_list.currentRow()
        if 0 <= row < len(self._p2p_visitors):
            self._p2p_visitors.pop(row)
            self._p2p_current_index = -1
            self._refresh_p2p_list()

    def _on_p2p_visitor_selected(self, row):
        """选择 visitor 列表项时加载到表单"""
        # 先保存当前表单
        self._save_current_form()
        if 0 <= row < len(self._p2p_visitors):
            self._p2p_current_index = row
            v = self._p2p_visitors[row]
            self.ui.p2p_form_server.setText(v.get("serverName", ""))
            self.ui.p2p_form_port.setValue(v.get("bindPort", 10000))
            self.ui.p2p_form_key.setText(v.get("secretKey", "abc123"))
        else:
            self._p2p_current_index = -1

    def _save_current_form(self):
        """将当前表单内容保存回 visitor 数据"""
        if 0 <= self._p2p_current_index < len(self._p2p_visitors):
            v = self._p2p_visitors[self._p2p_current_index]
            v["serverName"] = self.ui.p2p_form_server.text()
            v["bindPort"] = self.ui.p2p_form_port.value()
            v["secretKey"] = self.ui.p2p_form_key.text()
            # 更新列表显示
            item = self.ui.p2p_visitor_list.item(self._p2p_current_index)
            if item:
                item.setText(v["serverName"])

    def _refresh_p2p_list(self):
        """刷新 visitor 列表显示"""
        self.ui.p2p_visitor_list.clear()
        for v in self._p2p_visitors:
            self.ui.p2p_visitor_list.addItem(v.get("serverName", ""))

    def _save_p2p_settings(self):
        """visitor 配置仅存于内存，连接时写入 TOML，不持久化到 settings.json"""
        pass

    def _on_p2p_connect(self):
        """连接按钮 - 根据当前模式分发连接"""
        mode = self.ui.p2p_mode_combo.currentText()
        self._append_log(f"[远程] 连接按钮点击，模式: {mode}")
        if mode == "XTCP":
            self._on_xtcp_connect()
        elif mode == "TCP":
            self._on_tcp_connect()

    def _on_p2p_disconnect(self):
        """断开按钮 - 根据当前模式分发断开"""
        mode = self.ui.p2p_mode_combo.currentText()
        if mode == "XTCP":
            self._on_xtcp_disconnect()
        elif mode == "TCP":
            self._on_tcp_disconnect()

    def _on_p2p_mode_changed(self, index):
        """连接方式切换时更新 UI 显隐"""
        self._save_current_form()
        self._update_p2p_visibility()

    def _update_p2p_visibility(self):
        """根据当前模式显示/隐藏对应表单"""
        mode = self.ui.p2p_mode_combo.currentText()
        is_xtcp = (mode == "XTCP")
        # XTCP 控件显隐
        for w in self.ui.p2p_xtcp_widgets:
            w.setVisible(is_xtcp)
        for i in range(self.ui.p2p_xtcp_form.rowCount()):
            lbl = self.ui.p2p_xtcp_form.itemAt(i * 2, QFormLayout.ItemRole.LabelRole)
            if lbl and lbl.widget():
                lbl.widget().setVisible(is_xtcp)
        # host/port 字段随模式切换显隐（仅第0/1行），账号/密码始终可见
        is_tcp = not is_xtcp
        for w in self.ui.p2p_ssh_widgets:
            w.setVisible(is_tcp)
        for row_idx in range(2):  # host(行0) 和 port(行1)
            lbl = self.ui.p2p_ssh_form.itemAt(row_idx, QFormLayout.ItemRole.LabelRole)
            if lbl and lbl.widget():
                lbl.widget().setVisible(is_tcp)
        self._update_p2p_buttons()

    def _on_xtcp_connect(self):
        """生成 TOML 并启动 frpc"""
        self._save_current_form()
        if not self._p2p_visitors:
            self._append_log("[远程] 请先添加 visitor 配置")
            return
        if self._frpc_process is not None:
            self._append_log("[远程] frpc 已在运行中")
            return
        app_dir = self._get_app_dir()
        toml_path = os.path.join(app_dir, "frpc_xtcp.toml")
        try:
            self._write_frpc_config(toml_path)
            self._append_log(f"[远程] 已生成 {toml_path}")
        except Exception as e:
            self._append_log(f"[远程] 生成配置失败: {e}")
            return
        frpc_exe = os.path.join(app_dir, "frpc.exe")
        if not os.path.exists(frpc_exe):
            self._append_log(f"[远程] frpc.exe 不存在: {frpc_exe}")
            return
        self._frpc_process = QProcess()
        self._frpc_process.setWorkingDirectory(app_dir)
        self._frpc_process.readyReadStandardOutput.connect(self._on_frpc_output)
        self._frpc_process.readyReadStandardError.connect(self._on_frpc_error)
        self._frpc_process.finished.connect(self._on_frpc_finished)
        self._frpc_process.start(frpc_exe, ["-c", toml_path])
        self._append_log(f"[远程] 已启动 frpc: {frpc_exe} -c {toml_path}")
        self._update_p2p_buttons()

    def _on_xtcp_disconnect(self):
        """停止 frpc 进程"""
        if self._frpc_process is None:
            self._append_log("[远程] frpc 未在运行")
            return
        self._append_log("[远程] 正在停止 frpc...")
        proc = self._frpc_process
        self._frpc_process = None  # 先置空，防止 _on_frpc_finished 重复处理
        proc.kill()
        proc.waitForFinished(3000)
        proc.deleteLater()
        self.ui.p2p_sftp_btn.setEnabled(False)
        self.ui.p2p_ssh_terminal_btn.setEnabled(False)
        # 断开时关闭已打开的 SFTP/SSH 终端窗口
        self._close_p2p_windows()
        self._update_p2p_buttons()
        self._append_log("[远程] frpc 已停止")

    def _on_tcp_connect(self):
        """启动 TCP 连接"""
        if not PARAMIKO_AVAILABLE:
            self._append_log("[TCP] paramiko 未安装，请执行: pip install paramiko")
            return
        # 增加对 isRunning 的检查，防止残留引用误判
        if self._tcp_worker is not None and self._tcp_worker.isRunning():
            self._append_log("[TCP] 已有连接正在运行")
            return
        # 清理残留的旧 worker 引用
        if self._tcp_worker is not None:
            self._tcp_worker.deleteLater()
            self._tcp_worker = None
        host = self.ui.p2p_ssh_host.text().strip()
        if not host:
            self._append_log("[TCP] 请输入主机地址")
            return
        port = self.ui.p2p_ssh_port.value()
        self._save_ssh_credentials()
        self._tcp_worker = TCPWorker(
            host, port,
            self.ui.p2p_ssh_user.text(), self.ui.p2p_ssh_pass.text()
        )
        self._tcp_worker.result_ready.connect(self._on_tcp_finished)
        self._tcp_worker.error.connect(self._on_tcp_error)
        self._tcp_worker.start()
        self._append_log(f"[TCP] 正在连接 {host}:{port}...")
        self._update_p2p_buttons()

    def _on_tcp_disconnect(self):
        """断开 TCP 连接"""
        if self._tcp_worker is None:
            self._append_log("[TCP] 未连接")
            return
        worker = self._tcp_worker
        self._tcp_worker = None
        # 非阻塞清理：不跨线程调用 close()，等线程自然结束后 deleteLater
        if worker.isRunning():
            worker.finished.connect(worker.deleteLater)
        else:
            worker.deleteLater()
        self.ui.p2p_sftp_btn.setEnabled(False)
        self.ui.p2p_ssh_terminal_btn.setEnabled(False)
        # 断开时关闭已打开的 SFTP/SSH 终端窗口
        self._close_p2p_windows()
        self._update_p2p_buttons()
        self._append_log("[TCP] 已断开")

    def _on_tcp_finished(self, result):
        """TCP 连接成功回调"""
        self._append_log(f"[TCP] 连接成功: {result}")
        self._show_info_bar(f"TCP 连接成功: {result}", "success")
        # 仅在 TCP 真正成功后才启用按钮
        self.ui.p2p_sftp_btn.setEnabled(True)
        self.ui.p2p_ssh_terminal_btn.setEnabled(True)
        # 线程结束后安全销毁并清空引用
        if self._tcp_worker:
            self._tcp_worker.deleteLater()
            self._tcp_worker = None

    def _on_tcp_error(self, error):
        """TCP 连接失败回调"""
        self._append_log(f"[TCP] 连接失败: {error}")
        self._show_info_bar(f"网络连接失败: {error}", "error", duration=4000)
        if self._tcp_worker:
            self._tcp_worker.deleteLater()
        self._tcp_worker = None
        self.ui.p2p_sftp_btn.setEnabled(False)
        self.ui.p2p_ssh_terminal_btn.setEnabled(False)
        self._update_p2p_buttons()

    # frpc 服务器默认配置（settings.json 缺失时自动生成）
    _FRPC_SERVER_DEFAULTS = {
        "serverAddr": "49.235.34.253",
        "serverPort": 7900,
        "auth_method": "token",
        "auth_token": "123",
    }

    def _write_frpc_config(self, path):
        """生成 frpc_xtcp.toml 文件（全局头部从 settings.json 的 frpc_server 读取，遍历所有 visitor）"""
        settings = self._load_settings()
        frpc_server = settings.get("frpc_server")
        if not frpc_server:
            # 缺失时自动生成默认配置并写入 settings.json
            frpc_server = dict(self._FRPC_SERVER_DEFAULTS)
            self._save_settings({"frpc_server": frpc_server})
            self._append_log("[远程] settings.json 中未找到 frpc_server，已自动生成默认配置")
        server_addr = frpc_server.get("serverAddr", self._FRPC_SERVER_DEFAULTS["serverAddr"])
        server_port = frpc_server.get("serverPort", self._FRPC_SERVER_DEFAULTS["serverPort"])
        auth_method = frpc_server.get("auth_method", self._FRPC_SERVER_DEFAULTS["auth_method"])
        auth_token = frpc_server.get("auth_token", self._FRPC_SERVER_DEFAULTS["auth_token"])
        with open(path, 'w', encoding='utf-8') as f:
            # 全局头部配置（从 settings.json 的 frpc_server 字段读取）
            f.write(f'serverAddr = "{server_addr}"\n')
            f.write(f'serverPort = {server_port}\n')
            f.write(f'auth.method = "{auth_method}"\n')
            f.write(f'auth.token = "{auth_token}"\n')
            f.write('\n')
            for v in self._p2p_visitors:
                sn = v["serverName"]
                f.write("[[visitors]]\n")
                f.write(f'name = "{sn}"\n')
                f.write(f'type = "xtcp"\n')
                f.write(f'serverName = "{sn}"\n')
                f.write(f'secretKey = "{v["secretKey"]}"\n')
                f.write(f'bindPort = {v["bindPort"]}\n')
                f.write("\n")

    def _on_frpc_output(self):
        """处理 frpc 标准输出"""
        if self._frpc_process:
            output = self._frpc_process.readAllStandardOutput().data().decode('utf-8', errors='ignore')
            if output.strip():
                self._append_log(f"[frpc] {output.strip()}")

    def _on_frpc_error(self):
        """处理 frpc 错误输出"""
        if self._frpc_process:
            error = self._frpc_process.readAllStandardError().data().decode('utf-8', errors='ignore')
            if error.strip():
                self._append_log(f"[frpc] {error.strip()}")

    def _on_frpc_finished(self, exit_code, exit_status):
        """frpc 进程结束回调"""
        self._append_log(f"[远程] frpc 已退出，退出码: {exit_code}")
        self._frpc_process = None
        # frpc 意外退出时禁用 SFTP/SSH 终端按钮，防止误触
        self.ui.p2p_sftp_btn.setEnabled(False)
        self.ui.p2p_ssh_terminal_btn.setEnabled(False)
        # 关闭已打开的 SFTP/SSH 终端窗口
        self._close_p2p_windows()
        self._update_p2p_buttons()

    def _update_p2p_buttons(self):
        """更新连接/断开按钮状态，以及 SFTP/SSH 终端按钮状态"""
        mode = self.ui.p2p_mode_combo.currentText()
        if mode == "XTCP":
            running = self._frpc_process is not None
            # XTCP 模式下，frpc 运行时即可使用 SFTP/SSH 终端（具体连接时再按选中 visitor 的 bindPort 发起）
            self.ui.p2p_sftp_btn.setEnabled(running)
            self.ui.p2p_ssh_terminal_btn.setEnabled(running)
        elif mode == "TCP":
            running = self._tcp_worker is not None
        else:
            running = False
        self.ui.p2p_connect_btn.setEnabled(not running)
        self.ui.p2p_disconnect_btn.setEnabled(running)

    def _close_p2p_windows(self):
        """关闭已打开的 SFTP 和 SSH 终端窗口，避免连接失效后误操作"""
        if self._sftp_window is not None:
            try:
                self._sftp_window.close()
            except Exception:
                pass
            self._sftp_window = None
        if self._ssh_terminal_window is not None:
            try:
                self._ssh_terminal_window.close()
            except Exception:
                pass
            self._ssh_terminal_window = None

    def _save_ssh_credentials(self):
        """将当前 SSH 账号/密码保存到 settings.json，下次启动时自动恢复"""
        username = self.ui.p2p_ssh_user.text().strip()
        password = self.ui.p2p_ssh_pass.text()
        data = {}
        if username:
            data["ssh_user"] = username
        if password:
            data["ssh_pass"] = password
        if data:
            self._save_settings(data)

    def _on_sftp_btn_clicked(self):
        """打开 SFTP 文件管理窗口"""
        if not PARAMIKO_AVAILABLE:
            self._append_log("[SFTP] paramiko 未安装")
            return
        mode = self.ui.p2p_mode_combo.currentText()
        server_name = ''
        if mode == "XTCP":
            # XTCP 模式：连接到 127.0.0.1:当前选中 visitor 的 bindPort
            if not self._p2p_visitors:
                self._append_log("[SFTP] 请先添加 visitor 配置")
                return
            idx = self._p2p_current_index
            if not (0 <= idx < len(self._p2p_visitors)):
                self._append_log("[SFTP] 请先在列表中选择一个 visitor")
                return
            host = "127.0.0.1"
            port = self._p2p_visitors[idx]["bindPort"]
            server_name = self._p2p_visitors[idx].get("serverName", "")
        elif mode == "TCP":
            host = self.ui.p2p_ssh_host.text().strip()
            port = self.ui.p2p_ssh_port.value()
        else:
            self._append_log("[SFTP] SFTP 仅支持 XTCP/TCP 模式")
            return
        username = self.ui.p2p_ssh_user.text()
        password = self.ui.p2p_ssh_pass.text()
        if not host:
            self._append_log("[SFTP] 主机地址不能为空")
            return
        self._save_ssh_credentials()
        self._append_log(f"[SFTP] 打开文件管理: {server_name or host}:{port}")
        self._sftp_window = SFTPWindow(
            host, port, username, password,
            server_name=server_name,
            log_callback=lambda msg: self._append_log(msg),
            parent=self
        )
        self._sftp_window.show()

    def _on_ssh_terminal_btn_clicked(self):
        """打开 SSH 终端窗口"""
        if not PARAMIKO_AVAILABLE:
            self._append_log("[SSH] paramiko 未安装")
            return
        mode = self.ui.p2p_mode_combo.currentText()
        if mode == "XTCP":
            if not self._p2p_visitors:
                self._append_log("[SSH] 请先添加 visitor 配置")
                return
            idx = self._p2p_current_index
            if not (0 <= idx < len(self._p2p_visitors)):
                self._append_log("[SSH] 请先在列表中选择一个 visitor")
                return
            host = "127.0.0.1"
            port = self._p2p_visitors[idx]["bindPort"]
        elif mode == "TCP":
            host = self.ui.p2p_ssh_host.text().strip()
            port = self.ui.p2p_ssh_port.value()
        else:
            self._append_log("[SSH] SSH 终端仅支持 XTCP/TCP 模式")
            return
        username = self.ui.p2p_ssh_user.text()
        password = self.ui.p2p_ssh_pass.text()
        if not host:
            self._append_log("[SSH] 主机地址不能为空")
            return
        self._save_ssh_credentials()
        self._append_log(f"[SSH] 打开终端: {host}:{port}")
        self._ssh_terminal_window = SSHTerminalWindow(
            host, port, username, password,
            log_callback=lambda msg: self._append_log(msg),
            parent=self
        )
        self._ssh_terminal_window.show()

    # ==================== 快捷键 ====================

    def _get_shortcut_settings(self):
        """从 settings.json 获取快捷键配置，缺失字段用默认值"""
        settings = self._load_settings()
        return {
            "shortcut_flush": settings.get("shortcut_flush", self.DEFAULT_SHORTCUTS["shortcut_flush"]),
            "shortcut_start": settings.get("shortcut_start", self.DEFAULT_SHORTCUTS["shortcut_start"]),
            "shortcut_open_dir": settings.get("shortcut_open_dir", self.DEFAULT_SHORTCUTS["shortcut_open_dir"]),
        }

    def _init_shortcuts(self):
        """绑定全局快捷键（从 settings.json 读取配置）"""
        # 清除旧快捷键引用
        self._shortcuts = []
        sc = self._get_shortcut_settings()
        # 刷新
        s1 = QShortcut(QKeySequence(sc["shortcut_flush"]), self)
        s1.activated.connect(self.on_flush_clicked)
        self._shortcuts.append(s1)
        # 打开目录
        s2 = QShortcut(QKeySequence(sc["shortcut_open_dir"]), self)
        s2.activated.connect(self.on_open_dir_clicked)
        self._shortcuts.append(s2)
        # 空格切换播放/结束
        self._space_shortcut = QShortcut(QKeySequence(sc["shortcut_start"]), self)
        self._space_shortcut.activated.connect(self._on_space_pressed)
        self._shortcuts.append(self._space_shortcut)

    def _on_space_pressed(self):
        """空格键切换播放/结束，焦点在输入框时不触发"""
        if self.focusWidget() is self.ui.input_frame:
            return
        if self.running_process is not None:
            self.on_end_clicked()
        else:
            self.on_start_clicked()

    # ==================== 菜单栏 ====================

    def _init_menubar(self):
        """初始化顶部菜单栏（独立 QMenuBar 控件，替代 QMainWindow.menuBar）"""
        self._menubar_widget = QMenuBar()
        self._menubar_widget.setObjectName(u"menubar_widget")
        # 固定高度 24px：独立 QMenuBar 在 Fusion 风格下默认垂直度量偏大，
        # 会导致菜单栏与工具栏之间出现多余空隙，固定高度确保顶部紧凑
        self._menubar_widget.setFixedHeight(24)
        menubar = self._menubar_widget

        # 「功能」菜单
        func_menu = menubar.addMenu("功能")
        act_sc = func_menu.addAction("修改快捷键")
        act_sc.triggered.connect(lambda: QTimer.singleShot(0, self._on_modify_shortcuts))
        act_hc = func_menu.addAction("高亮颜色设置")
        act_hc.triggered.connect(lambda: QTimer.singleShot(0, self._on_highlight_color))

        # 「视图」菜单（布局/主题为二级子菜单，字号/缩放/字体同级）
        view_menu = menubar.addMenu("视图")
        settings = self._load_settings()

        # -- 二级子菜单: 布局（互斥单选） --
        layout_menu = view_menu.addMenu("布局")
        self._layout_group = QActionGroup(self)
        self._layout_group.setExclusive(True)
        self._act_layout_panel = QAction("面板布局", self)
        self._act_layout_panel.setCheckable(True)
        self._act_layout_panel.setChecked(not settings.get("classic_layout", True))
        self._layout_group.addAction(self._act_layout_panel)
        layout_menu.addAction(self._act_layout_panel)
        self._act_layout_classic = QAction("经典布局", self)
        self._act_layout_classic.setCheckable(True)
        self._act_layout_classic.setChecked(settings.get("classic_layout", True))
        self._layout_group.addAction(self._act_layout_classic)
        layout_menu.addAction(self._act_layout_classic)
        self._layout_group.triggered.connect(self._on_layout_selected)

        # -- 二级子菜单: 主题（互斥单选） --
        theme_menu = view_menu.addMenu("主题")
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        _theme_mode = self._get_theme_mode(settings)
        self._act_theme_auto = QAction("跟随系统", self)
        self._act_theme_auto.setCheckable(True)
        self._act_theme_auto.setChecked(_theme_mode == "auto")
        self._theme_group.addAction(self._act_theme_auto)
        theme_menu.addAction(self._act_theme_auto)
        self._act_theme_light = QAction("浅色主题", self)
        self._act_theme_light.setCheckable(True)
        self._act_theme_light.setChecked(_theme_mode == "light")
        self._theme_group.addAction(self._act_theme_light)
        theme_menu.addAction(self._act_theme_light)
        self._act_theme_dark = QAction("深色主题", self)
        self._act_theme_dark.setCheckable(True)
        self._act_theme_dark.setChecked(_theme_mode == "dark")
        self._theme_group.addAction(self._act_theme_dark)
        theme_menu.addAction(self._act_theme_dark)
        self._theme_group.triggered.connect(self._on_theme_selected)

        # -- 字号/缩放/字体（与布局、主题同级） --
        view_menu.addSeparator()
        act_fs = view_menu.addAction("字号大小")
        act_fs.triggered.connect(lambda: QTimer.singleShot(0, self._on_font_size))
        act_scale = view_menu.addAction("界面缩放")
        act_scale.triggered.connect(lambda: QTimer.singleShot(0, self._on_dpi_scale))
        act_ff = view_menu.addAction("字体设置")
        act_ff.triggered.connect(lambda: QTimer.singleShot(0, self._on_font_family))

        # 「帮助」菜单
        help_menu = menubar.addMenu("帮助")
        act_about = help_menu.addAction("关于")
        act_about.triggered.connect(lambda: QTimer.singleShot(0, self._on_about))

    def _on_modify_shortcuts(self):
        """弹出快捷键修改对话框"""
        dlg = QDialog(self)
        dlg.setWindowTitle("修改快捷键")
        layout = QVBoxLayout(dlg)
        sc = self._get_shortcut_settings()
        fields = [
            ("刷新", "shortcut_flush", sc["shortcut_flush"]),
            ("播放/结束", "shortcut_start", sc["shortcut_start"]),
            ("打开目录", "shortcut_open_dir", sc["shortcut_open_dir"]),
        ]
        editors = {}
        for label, key, default in fields:
            row = QHBoxLayout()
            row.addWidget(QLabel(label + ":"))
            edit = QKeySequenceEdit(QKeySequence(default))
            row.addWidget(edit)
            editors[key] = edit
            layout.addLayout(row)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)
        if dlg.exec() == QDialog.Accepted:
            new_sc = {k: v.keySequence().toString() for k, v in editors.items()}
            self._save_settings(new_sc)
            self._init_shortcuts()
            self._append_log(f"[配置] 已更新快捷键: {new_sc}")

    def _on_highlight_color(self):
        """弹出颜色选择对话框修改日志高亮颜色"""
        current = self.highlight_color
        color = QColorDialog.getColor(current, self, "选择高亮颜色")
        if color.isValid():
            self.highlight_color = color
            self._save_settings({
                "highlight_color": [color.red(), color.green(), color.blue()]
            })
            self._append_log(
                f"[配置] 已更新高亮颜色: RGB({color.red()},{color.green()},{color.blue()})")
            self._show_info_bar(f"[配置] 已更新高亮颜色: RGB({color.red()},{color.green()},{color.blue()})")

    def _on_font_size(self):
        """弹出字号选择对话框"""
        settings = self._load_settings()
        current = settings.get("font_size", 10)
        val, ok = QInputDialog.getInt(self, "字号大小", "请输入字号 (10~20):", current, 10, 20, 1)
        if ok:
            self._save_settings({"font_size": val})
            self._apply_font_size()
            self._append_log(f"[配置] 已更新字号: {val}pt")
            self._show_info_bar(f"[配置] 已更新字号: {val}pt")

    def _on_dpi_scale(self):
        """弹出 DPI 缩放比例选择对话框"""
        settings = self._load_settings()
        current = settings.get("dpi_scale", 100)
        options = [100, 125, 150, 175, 200]
        idx, ok = QInputDialog.getItem(
            self, "界面缩放", "选择缩放比例:",
            [f"{o}%" for o in options],
            options.index(current) if current in options else 0,
            editable=False)
        if ok:
            val = int(idx.replace("%", ""))
            self._save_settings({"dpi_scale": val})
            QMessageBox.information(self, "界面缩放", "缩放设置已保存，重启应用后生效。")
            self._append_log(f"[配置] 已设置缩放: {val}%（重启后生效）")
            self._show_info_bar(f"[配置] 已设置缩放: {val}%,需重启")

    def _on_font_family(self):
        """弹出字体选择对话框"""
        settings = self._load_settings()
        current_family = settings.get("font_family", "")
        current_font = QFont(current_family) if current_family else QFont()
        result = QFontDialog.getFont(current_font, self, "选择字体")
        # PySide6 不同版本返回顺序可能为 (QFont, bool) 或 (bool, QFont)
        if isinstance(result[0], QFont):
            font, ok = result[0], result[1]
        else:
            ok, font = result[0], result[1]
        if ok:
            self._save_settings({"font_family": font.family()})
            self._apply_font_family()
            self._show_info_bar(f"[配置] 已更新字体: {font.family()}")
            self._append_log(f"[配置] 已更新字体: {font.family()}")

    def _on_about(self) :
        """显示关于对话框"""
        QMessageBox.about(
            self,
            "关于",
            "AutoWork - 自动化工作工具\n"
            "版本: 1.4.0\n\n"
            "用于视频播放、日志管理与数据记录的桌面自动化工具。"
        )

    # ==================== 设置应用 ====================

    def _apply_highlight_color(self):
        """从 settings.json 加载高亮颜色"""
        settings = self._load_settings()
        rgb = settings.get("highlight_color", self.DEFAULT_HIGHLIGHT_COLOR)
        self.highlight_color = QColor(rgb[0], rgb[1], rgb[2])

    def _apply_font_size(self):
        """从 settings.json 加载并应用全局字号"""
        self._apply_global_font()

    def _apply_font_family(self):
        """从 settings.json 加载并应用全局字体"""
        self._apply_global_font()

    def _apply_global_font(self):
        """统一应用全局字体（族 + 字号），确保所有控件生效。

        修复要点：
        1. qfluentwidgets 控件在构造时通过 setFont(getFont(14)) 显式设置字体
           （使用 setPixelSize + setFamilies），QApplication.setFont() 对其无效，
           必须逐个控件重新设置；
        2. 必须使用 setPixelSize 而非 setPointSize —— Qt 中两者互斥，
           Fluent 内部统一使用 pixelSize，若用 pointSize 会导致字体解析冲突；
        3. 必须使用 setFamilies()（复数）设置字体族列表，与 Fluent getFont() 一致；
        4. 先更新 qconfig.fontFamilies 再遍历控件，确保后续新建控件也使用新字体；
        5. 不使用 unpolish/polish —— 它会触发 Fluent 样式引擎重新应用自身字体，
           覆盖刚设好的用户字体；仅用 update() 触发重绘即可。
        """
        settings = self._load_settings()
        family = settings.get("font_family", None)
        size = settings.get("font_size", None)
        if not family and not size:
            return

        # 1. 先同步 qfluentwidgets 字体族配置（影响后续新建的 Fluent 控件）
        if family:
            setFontFamilies([family], save=False)

        # 2. 构造目标字体 —— 使用 pixelSize + families，与 Fluent getFont() 保持一致
        #    用户字号以 point 为单位（10~20），转换为 pixel：px = pt * 4 / 3
        app_font = QFont()
        if family:
            app_font.setFamilies([family])
        else:
            # 未设置字体族时沿用当前应用字体的族
            app_font.setFamilies(QApplication.font().families())
        if size:
            pixel_size = max(12, int(int(size) * 4 / 3))
        else:
            # 未设置字号时沿用当前应用字体的 pixelSize（默认 14px ≈ 10.5pt）
            cur_px = QApplication.font().pixelSize()
            pixel_size = cur_px if cur_px > 0 else 14
        app_font.setPixelSize(pixel_size)

        # 3. 设置为应用程序默认字体（影响后续新建的标准 Qt 控件）
        QApplication.setFont(app_font)

        # 4. 遍历所有顶级窗口及子控件，逐一设置字体
        #    （覆盖 Fluent 控件构造时的显式字体）
        for window in QApplication.topLevelWidgets():
            if not isinstance(window, QWidget):
                continue
            self._set_widget_font_recursive(window, app_font)
            window.update()

    def _set_widget_font_recursive(self, widget, app_font):
        """为窗口内所有控件统一设置用户字体（含日志区/终端区）。

        注意：不调用 unpolish/polish，避免触发 Fluent 样式引擎重置字体。
        仅通过 setFont() + update() 即可完成字体切换。
        """
        widget.setFont(app_font)
        for child in widget.findChildren(QWidget):
            child.setFont(app_font)
            child.update()

    @staticmethod
    def _get_theme_mode(settings):
        """获取主题模式：'auto'(跟随系统) / 'light'(浅色) / 'dark'(深色)
        兼容旧版布尔型 dark_theme 字段。"""
        mode = settings.get("theme_mode", "")
        if mode in ("auto", "light", "dark"):
            return mode
        # 旧版兼容：dark_theme=True → dark，False → auto（跟随系统）
        return "dark" if settings.get("dark_theme", False) else "auto"
    
    @staticmethod
    def _system_is_dark():
        """检测 Windows 系统当前是否为深色主题"""
        try:
            import darkdetect
            return bool(darkdetect.isDark())
        except Exception:
            return False
    
    @staticmethod
    def _effective_is_dark(settings):
        """解析实际生效的深色状态：
        auto=跟随 Windows 系统主题，light=强制浅色，dark=强制深色。"""
        mode = MainWindow._get_theme_mode(settings)
        if mode == "dark":
            return True
        if mode == "light":
            return False
        return MainWindow._system_is_dark()

    def _apply_theme(self):
        """根据 settings.json 中的 theme_mode 字段应用 Fluent 主题 + 补充 QSS"""
        settings = self._load_settings()
        is_dark = self._effective_is_dark(settings)

        # Fluent 全局主题引擎（自动处理所有 Fluent 控件）
        setTheme(Theme.DARK if is_dark else Theme.LIGHT)
        setThemeColor("#00BCD4", lazy=True)  # lazy=True 避免立即刷新导致样式递归崩溃
        # 锁定 Qt 调色板与应用主题一致：否则 Windows 深色模式会把调色板染成深色，
        # 导致 Fluent 浅色控件取到白色 windowText → 白字白底看不清
        QApplication.styleHints().setColorScheme(
            Qt.ColorScheme.Dark if is_dark else Qt.ColorScheme.Light)

        if not is_dark:
            # 浅色主题：Fluent 引擎接管大部分控件样式，
            # 但 QSplitter handle 和分割线在默认浅色下几乎不可见，需补充最小 QSS
            light_stylesheet = """
            /* ===== 分割器（浅色主题） ===== */
            QSplitter::handle {
                background-color: #D6D6D6;
            }
            QSplitter::handle:horizontal {
                width: 2px;
            }
            QSplitter::handle:vertical {
                height: 2px;
            }
            QSplitter::handle:hover {
                background-color: #00BCD4;
            }

            /* ===== 工具栏分割线（浅色主题） ===== */
            QFrame#toolbar_separator {
                color: #C8C8C8;
                margin: 4px 2px;
            }

            /* ===== 菜单栏紧凑样式（浅色主题） ===== */
            QMenuBar {
                background: transparent;
                padding: 1px;
            }
            QMenuBar::item {
                background: transparent;
                padding: 2px 8px;
                border-radius: 4px;
            }
            QMenuBar::item:selected {
                background-color: rgba(0, 0, 0, 0.06);
            }
            """
            self.setStyleSheet(light_stylesheet)
            # 强制刷新所有控件样式，防止从深色切回时 Fluent 控件文字颜色残留
            self.style().unpolish(self)
            self.style().polish(self)
            for w in self.findChildren(QWidget):
                w.update()
            # 主题切换后重新应用用户字体（Fluent 引擎可能覆盖）
            self._apply_global_font()
            return

        # 补充 QSS：仅覆盖非 Fluent 的标准 Qt 控件
        # 深色统一色板：基底 #202020（对齐 Fluent 深色窗口色）/ 悬浮面 #2C2C2C / 边框 #383838
        stylesheet = """
        /* ===== 窗口自身底色（标题栏透明区域下方） ===== */
        MainWindow {
            background-color: #202020;
        }

        /* ===== 主窗口背景 ===== */
        QWidget#centralwidget {
            background-color: #202020;
        }

        /* ===== 工具栏 ===== */
        QWidget#toolbar_widget {
            background-color: #202020;
            border-bottom: 1px solid #383838;
        }
        QFrame#toolbar_separator {
            color: #383838;
            margin: 4px 2px;
        }

        /* ===== 按钮（对话框/SFTP/SSH 窗口中仍使用原生 QPushButton） ===== */
        QPushButton {
            background-color: #00BCD4;
            color: #FFFFFF;
            border: none;
            border-radius: 6px;
            padding: 5px 14px;
            min-height: 22px;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: #00ACC1;
        }
        QPushButton:pressed {
            background-color: #00838F;
        }
        QPushButton:disabled {
            background-color: #383838;
            color: #555960;
        }

        /* ===== 输入框 ===== */
        QLineEdit {
            background-color: #2C2C2C;
            color: #C8D0DC;
            border: 1px solid #383838;
            border-radius: 6px;
            padding: 4px 8px;
            selection-background-color: #00BCD4;
            selection-color: #FFFFFF;
        }
        QLineEdit:focus {
            border: 1px solid #00BCD4;
        }
        QLineEdit:disabled {
            color: #555960;
            background-color: #202020;
        }

        /* ===== 数字微调框 ===== */
        QSpinBox {
            background-color: #2C2C2C;
            color: #C8D0DC;
            border: 1px solid #383838;
            border-radius: 6px;
            padding: 4px 6px;
        }
        QSpinBox:hover, QSpinBox:focus {
            border: 1px solid #00BCD4;
        }
        QSpinBox::up-button, QSpinBox::down-button {
            background-color: #2C2C2C;
            border: none;
            width: 16px;
        }
        QSpinBox::up-button:hover, QSpinBox::down-button:hover {
            background-color: #00BCD4;
        }

        /* ===== 日志输出区域（终端风格） ===== */
        /* 注意：不在此处指定 font-family/font-size，
           字体由 _apply_global_font() 统一通过 widget.setFont() 控制，
           避免 QSS 覆盖用户自选字体 */
        QPlainTextEdit#show_log {
            background-color: #202020;
            color: #B0BEC5;
            border: none;
            selection-background-color: #00BCD4;
            selection-color: #FFFFFF;
        }
        /* 覆盖 Fluent 内置 hover/focus 变色，保持终端背景恒定 */
        QPlainTextEdit#show_log:hover,
        QPlainTextEdit#show_log:focus {
            background-color: #202020;
            border: none;
        }

        /* ===== 日志顶部状态条 ===== */
        QWidget#log_status_bar {
            background-color: #2C2C2C;
            border-bottom: 1px solid #383838;
        }
        QLabel#log_status_device, QLabel#log_status_count {
            color: #8892a2;
            font-size: 8pt;
            background: transparent;
        }

        /* ===== 左侧面板标题 ===== */
        QLabel#left_panel_header {
            background-color: #202020;
            color: #00BCD4;
            font-weight: bold;
            font-size: 9pt;
            border-bottom: 1px solid #383838;
            padding-left: 10px;
        }

        /* ===== 面板容器 ===== */
        QWidget#left_panel, QWidget#center_panel {
            background-color: #202020;
        }

        /* ===== 列表/树/日期控件（统一基底色，禁止回退调色板产生杂色） ===== */
        QListWidget, QTreeWidget {
            background-color: #202020;
            color: #C8D0DC;
            border: none;
            outline: none;
        }
        QListWidget::item:hover, QTreeWidget::item:hover {
            background-color: rgba(255, 255, 255, 0.05);
        }
        QListWidget::item:selected, QTreeWidget::item:selected {
            background-color: rgba(0, 188, 212, 0.20);
            color: #00BCD4;
        }
        QDateEdit {
            background-color: #2C2C2C;
            color: #C8D0DC;
            border: 1px solid #383838;
            border-radius: 6px;
            padding: 3px 8px;
        }
        QDateEdit::drop-down {
            border: none;
            width: 20px;
        }
        QDateEdit:focus {
            border: 1px solid #00BCD4;
        }

        /* ===== 远程面板 ===== */
        QFrame#p2p_panel {
            background-color: #2C2C2C;
            border-left: 1px solid #383838;
            border-radius: 6px;
            margin: 4px 4px 4px 0;
        }
        QLabel#p2p_panel_header {
            background-color: #202020;
            color: #00BCD4;
            font-weight: bold;
            font-size: 10pt;
            border-bottom: 1px solid #383838;
            border-radius: 6px 6px 0 0;
        }
        QLabel#section_label {
            color: #00BCD4;
            font-weight: bold;
            font-size: 8pt;
            background: transparent;
            padding-left: 2px;
        }

        /* ===== 分割器 ===== */
        QSplitter::handle {
            background-color: #383838;
        }
        QSplitter::handle:horizontal {
            width: 2px;
        }
        QSplitter::handle:vertical {
            height: 2px;
        }
        QSplitter::handle:hover {
            background-color: #00BCD4;
        }

        /* ===== 标签 ===== */
        QLabel {
            color: #8892a2;
            background-color: transparent;
        }

        /* ===== 菜单栏 ===== */
        QMenuBar {
            background-color: #202020;
            color: #C8D0DC;
            border-bottom: 1px solid #383838;
            padding: 1px;
        }
        QMenuBar::item {
            background: transparent;
            padding: 2px 8px;
            border-radius: 4px;
        }
        QMenuBar::item:selected {
            background-color: rgba(0, 188, 212, 0.15);
            color: #00BCD4;
        }
        QMenu {
            background-color: #2C2C2C;
            color: #C8D0DC;
            border: 1px solid #383838;
            border-radius: 6px;
            padding: 4px;
        }
        QMenu::item {
            padding: 5px 28px 5px 12px;
            border-radius: 4px;
        }
        QMenu::item:selected {
            background-color: rgba(0, 188, 212, 0.15);
            color: #00BCD4;
        }
        QMenu::separator {
            height: 1px;
            background-color: #383838;
            margin: 4px 8px;
        }

        /* ===== 滚动条 ===== */
        QScrollBar:vertical {
            background-color: #202020;
            width: 8px;
            border: none;
            margin: 0;
        }
        QScrollBar::handle:vertical {
            background-color: #383838;
            border-radius: 4px;
            min-height: 24px;
            margin: 2px;
        }
        QScrollBar::handle:vertical:hover {
            background-color: #00BCD4;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: none;
        }
        QScrollBar:horizontal {
            background-color: #202020;
            height: 8px;
            border: none;
            margin: 0;
        }
        QScrollBar::handle:horizontal {
            background-color: #383838;
            border-radius: 4px;
            min-width: 24px;
            margin: 2px;
        }
        QScrollBar::handle:horizontal:hover {
            background-color: #00BCD4;
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0px;
        }
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
            background: none;
        }

        /* ===== 工具提示 ===== */
        QToolTip {
            background-color: #2C2C2C;
            color: #00BCD4;
            border: 1px solid #383838;
            border-radius: 6px;
            padding: 4px 8px;
        }

        /* ===== 进度条 ===== */
        QProgressBar {
            background-color: #2C2C2C;
            border: 1px solid #383838;
            border-radius: 6px;
            text-align: center;
            color: #C8D0DC;
            height: 18px;
        }
        QProgressBar::chunk {
            background-color: #00BCD4;
            border-radius: 5px;
        }

        /* ===== 表单标签 ===== */
        QFormLayout QLabel {
            color: #8892a2;
            font-size: 8pt;
        }
        """
        self.setStyleSheet(stylesheet)
        # 主题切换后重新应用用户字体（Fluent 引擎可能覆盖）
        self._apply_global_font()

    # ==================== 系统主题监听（跟随系统模式） ====================

    def _init_system_theme_monitor(self):
        """初始化系统主题变化轮询（仅“跟随系统”模式下生效）"""
        self._last_applied_dark = None
        self._theme_poll_timer = QTimer(self)
        self._theme_poll_timer.setInterval(2000)  # 每 2 秒检测一次 Windows 主题
        self._theme_poll_timer.timeout.connect(self._poll_system_theme)
        self._sync_theme_polling()

    def _sync_theme_polling(self):
        """根据当前主题模式启停轮询定时器"""
        settings = self._load_settings()
        if self._get_theme_mode(settings) == "auto":
            self._last_applied_dark = self._effective_is_dark(settings)
            if not self._theme_poll_timer.isActive():
                self._theme_poll_timer.start()
        else:
            self._theme_poll_timer.stop()

    def _poll_system_theme(self):
        """轮询检测 Windows 主题变化，变化时自动重新应用主题"""
        try:
            sys_dark = self._system_is_dark()
            if sys_dark != self._last_applied_dark:
                self._last_applied_dark = sys_dark
                self._apply_theme()
                actual = "深色" if sys_dark else "浅色"
                self._append_log(f"[主题] 检测到系统主题变化，已自动切换为{actual}")
        except KeyboardInterrupt:
            pass

    def _on_theme_selected(self, action):
        """主题子菜单互斥选择"""
        if action == self._act_theme_dark:
            mode = "dark"
        elif action == self._act_theme_light:
            mode = "light"
        else:
            mode = "auto"
        self._save_settings({"theme_mode": mode})
        self._apply_theme()
        self._sync_theme_polling()  # 跟随系统模式时启动轮询，其他模式停止
        if mode == "dark":
            self._append_log("[主题] 已切换为深色主题")
        elif mode == "light":
            self._append_log("[主题] 已切换为浅色主题")
        else:
            actual = "深色" if self._system_is_dark() else "浅色"
            self._append_log(f"[主题] 已切换为跟随系统（当前系统为{actual}）")

    def _on_layout_selected(self, action):
        """布局子菜单互斥选择"""
        is_classic = (action == self._act_layout_classic)
        self._save_settings({"classic_layout": is_classic})
        self.ui.switch_layout(classic=is_classic)
        # 重新应用主题样式（新控件需要样式覆盖）
        self._apply_theme()
        layout_name = "经典布局" if is_classic else "面板布局"
        self._append_log(f"[布局] 已切换为{layout_name}")

    def _apply_layout(self):
        """从 settings.json 加载并应用布局偏好"""
        settings = self._load_settings()
        classic = settings.get("classic_layout", True)
        if classic:
            self.ui.switch_layout(classic=True)

    @staticmethod
    def apply_dpi_scale(settings_path):
        """在 QApplication 创建后应用 DPI 缩放"""
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                settings = json.load(f)
            scale = settings.get("dpi_scale", 100)
            if scale != 100:
                os.environ["QT_SCALE_FACTOR"] = str(scale / 100.0)
        except Exception:
            pass


def main():
    """主函数"""
    # 应用 DPI 缩放（必须在 QApplication 创建前设置环境变量）
    settings_path = os.path.join(MainWindow._get_app_dir(), "settings.json")
    MainWindow.apply_dpi_scale(settings_path)

    app = QApplication(sys.argv)
    
    # 设置应用程序样式
    app.setStyle("Fusion")

    # 【关键】在创建任何 Fluent 控件之前设定主题，避免中途变更导致控件文字刷新遗漏
    # theme_mode: auto=跟随 Windows 系统主题，light=强制浅色，dark=强制深色
    try:
        with open(settings_path, 'r', encoding='utf-8') as f:
            _settings = json.load(f)
    except Exception:
        _settings = {}
    _is_dark = MainWindow._effective_is_dark(_settings)
    setTheme(Theme.DARK if _is_dark else Theme.LIGHT)
    setThemeColor("#00BCD4", lazy=True)
    # 锁定 Qt 调色板，禁止 Windows 深色模式向应用注入深色调色板
    # （否则 Fluent 浅色控件会从深色调色板取白色文字 → 白字白底看不清）
    QApplication.styleHints().setColorScheme(
        Qt.ColorScheme.Dark if _is_dark else Qt.ColorScheme.Light)
    
    # 【关键】在创建窗口/控件之前应用用户自定义字体，
    # 使 Fluent 控件构造时 getFont() 即可取到正确的字体族
    try:
        _fam = _settings.get("font_family")
        _sz = _settings.get("font_size")
        if _fam or _sz:
            # 先更新 qconfig，确保 Fluent getFont() 构造时读到新字体族
            if _fam:
                setFontFamilies([_fam], save=False)
            # 构造字体：使用 pixelSize + families，与 Fluent getFont() 保持一致
            _app_font = QFont()
            if _fam:
                _app_font.setFamilies([_fam])
            else:
                _app_font.setFamilies(app.font().families())
            if _sz:
                _px = max(12, int(int(_sz) * 4 / 3))
            else:
                _cur_px = app.font().pixelSize()
                _px = _cur_px if _cur_px > 0 else 14
            _app_font.setPixelSize(_px)
            app.setFont(_app_font)
    except Exception:
        pass

    # 创建并显示主窗口
    window = MainWindow()
    window.show()
    
    # 运行应用程序
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
