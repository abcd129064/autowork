# -*- coding: utf-8 -*-

import sys
import os
import json
import re
import shutil
import subprocess
import ctypes
import ftplib
import stat
import time
from datetime import datetime
from PySide6.QtWidgets import (QApplication, QMainWindow, QMessageBox, QLabel,
    QListWidgetItem, QMenu, QColorDialog, QFontDialog, QInputDialog,
    QDialog, QVBoxLayout, QHBoxLayout, QKeySequenceEdit, QDialogButtonBox,
    QComboBox, QSpinBox, QListView, QAbstractItemView, QFrame, QFormLayout,
    QLineEdit, QTreeWidget, QTreeWidgetItem, QHeaderView, QFileDialog,
    QPushButton, QProgressDialog)
from PySide6.QtCore import Slot, QProcess, Qt, QTimer, QThread, Signal
from PySide6.QtGui import QColor, QBrush, QShortcut, QKeySequence, QFont, QAction
from autowork_with_table import Ui_MainWindow
from p2p import generate_random_port, is_port_in_use

try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False


class SSHWorker(QThread):
    """SSH 连接工作线程"""
    finished = Signal(str)
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
                                 timeout=10)
            stdin, stdout, stderr = self._client.exec_command("hostname && whoami")
            result = stdout.read().decode('utf-8', errors='ignore').strip()
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

    def close(self):
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None


class FTPWorker(QThread):
    """FTP 连接工作线程"""
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, host, port, username, password):
        super().__init__()
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self._ftp = None

    def run(self):
        try:
            self._ftp = ftplib.FTP()
            self._ftp.connect(self.host, self.port, timeout=10)
            self._ftp.login(self.username, self.password)
            welcome = self._ftp.getwelcome()
            files = self._ftp.nlst()
            self.finished.emit(f"{welcome}\n文件列表: {', '.join(files[:10])}")
        except Exception as e:
            self.error.emit(str(e))

    def close(self):
        if self._ftp:
            try:
                self._ftp.quit()
            except Exception:
                pass
            self._ftp = None


class SFTPListWorker(QThread):
    """异步 SFTP 列目录工作线程"""
    result = Signal(str, list)
    error = Signal(str)

    def __init__(self, transport, remote_path):
        super().__init__()
        self.transport = transport
        self.remote_path = remote_path

    def run(self):
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
            sftp.close()
            self.result.emit(self.remote_path, entries)
        except Exception as e:
            self.error.emit(str(e))


class SFTPOperationWorker(QThread):
    """异步 SFTP 操作工作线程（上传/下载/删除/创建目录）"""
    success = Signal(str)
    error = Signal(str)

    def __init__(self, transport, operation, local_path='', remote_path=''):
        super().__init__()
        self.transport = transport
        self.operation = operation
        self.local_path = local_path
        self.remote_path = remote_path

    def run(self):
        sftp = None
        try:
            sftp = paramiko.SFTPClient.from_transport(self.transport)
            if self.operation == 'upload':
                sftp.put(self.local_path, self.remote_path)
                self.success.emit(f"已上传: {os.path.basename(self.local_path)}")
            elif self.operation == 'download':
                sftp.get(self.remote_path, self.local_path)
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
        except Exception as e:
            self.error.emit(str(e))
        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass


class SFTPWindow(QDialog):
    """SFTP 文件管理窗口"""

    def __init__(self, host, port, username, password, log_callback=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"SFTP 文件管理 - {host}")
        self.resize(750, 500)
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._transport = None
        self._current_path = '/home'
        self._log = log_callback or (lambda msg: None)
        self._worker = None
        self._init_ui()
        QTimer.singleShot(100, self._connect_and_list)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        # 顶部路径栏
        path_layout = QHBoxLayout()
        self._btn_up = QPushButton(".. 上级目录")
        self._btn_up.clicked.connect(self._go_up)
        path_layout.addWidget(self._btn_up)
        path_layout.addWidget(QLabel("当前路径:"))
        self._lbl_path = QLabel(self._current_path)
        self._lbl_path.setStyleSheet("font-weight: bold;")
        path_layout.addWidget(self._lbl_path, 1)
        self._btn_refresh = QPushButton("刷新")
        self._btn_refresh.clicked.connect(self._refresh)
        path_layout.addWidget(self._btn_refresh)
        layout.addLayout(path_layout)
        # 文件列表
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["文件名", "大小", "类型", "权限", "修改时间"])
        self._tree.setColumnCount(5)
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in [1, 2, 3, 4]:
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self._tree)
        # 底部按钮栏
        btn_layout = QHBoxLayout()
        self._btn_upload = QPushButton("上传文件")
        self._btn_upload.clicked.connect(self._upload_file)
        btn_layout.addWidget(self._btn_upload)
        self._btn_download = QPushButton("下载文件")
        self._btn_download.clicked.connect(self._download_file)
        btn_layout.addWidget(self._btn_download)
        self._btn_delete = QPushButton("删除")
        self._btn_delete.clicked.connect(self._delete_selected)
        btn_layout.addWidget(self._btn_delete)
        self._btn_mkdir = QPushButton("新建目录")
        self._btn_mkdir.clicked.connect(self._create_directory)
        btn_layout.addWidget(self._btn_mkdir)
        btn_layout.addStretch()
        self._lbl_status = QLabel("就绪")
        btn_layout.addWidget(self._lbl_status)
        layout.addLayout(btn_layout)

    def _connect_and_list(self):
        """建立 SFTP 连接并列出根目录"""
        self._lbl_status.setText("正在连接...")
        try:
            self._transport = paramiko.Transport((self._host, self._port))
            self._transport.connect(username=self._username, password=self._password)
            self._log(f"[SFTP] 已连接到 {self._host}:{self._port}")
            self._lbl_status.setText("已连接")
            self._list_directory(self._current_path)
        except Exception as e:
            self._log(f"[SFTP] 连接失败: {e}")
            self._lbl_status.setText(f"连接失败: {e}")

    def _cleanup_worker(self):
        """安全清理当前 worker，防止 QThread C++ 对象被 GC 提前回收"""
        if self._worker is not None:
            if self._worker.isRunning():
                self._worker.wait(3000)
            self._worker.deleteLater()
            self._worker = None

    def _list_directory(self, path):
        """异步列出目录内容"""
        if self._transport is None:
            return
        self._cleanup_worker()
        self._lbl_status.setText(f"加载中: {path}")
        worker = SFTPListWorker(self._transport, path)
        worker.result.connect(self._on_list_result)
        worker.error.connect(self._on_list_error)
        self._worker = worker
        worker.start()

    def _on_list_result(self, path, entries):
        """目录列表加载完成"""
        self._current_path = path
        self._lbl_path.setText(path)
        self._tree.clear()
        # 目录排前，文件排后
        dirs = sorted([e for e in entries if e['is_dir']], key=lambda x: x['name'])
        files = sorted([e for e in entries if not e['is_dir']], key=lambda x: x['name'])
        for entry in dirs + files:
            item = QTreeWidgetItem()
            prefix = "/ " if entry['is_dir'] else ""
            item.setText(0, prefix + entry['name'])
            size_str = self._format_size(entry['size']) if not entry['is_dir'] else ''
            item.setText(1, size_str)
            item.setText(2, "目录" if entry['is_dir'] else "文件")
            item.setText(3, entry['perm'])
            item.setText(4, entry['mtime'])
            item.setData(0, Qt.ItemDataRole.UserRole, entry)
            self._tree.addTopLevelItem(item)
        self._lbl_status.setText(f"{len(dirs)} 个目录, {len(files)} 个文件")
        self._log(f"[SFTP] 目录加载完成: {path} ({len(dirs)} 目录, {len(files)} 文件)")

    def _on_list_error(self, error):
        self._lbl_status.setText(f"列表失败: {error}")
        self._log(f"[SFTP] 列表失败: {error}")

    def _refresh(self):
        self._list_directory(self._current_path)

    def _go_up(self):
        parent = '/'.join(self._current_path.rstrip('/').split('/')[:-1])
        if not parent:
            parent = '/'
        self._list_directory(parent)

    def _on_item_double_clicked(self, item, column):
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if entry and entry['is_dir']:
            new_path = self._current_path.rstrip('/') + '/' + entry['name']
            self._list_directory(new_path)

    def _upload_file(self):
        local_path, _ = QFileDialog.getOpenFileName(self, "选择要上传的文件")
        if not local_path:
            return
        remote_path = self._current_path.rstrip('/') + '/' + os.path.basename(local_path)
        self._cleanup_worker()
        self._lbl_status.setText(f"上传中: {os.path.basename(local_path)}...")
        self._log(f"[SFTP] 上传: {local_path} -> {remote_path}")
        worker = SFTPOperationWorker(self._transport, 'upload', local_path, remote_path)
        worker.success.connect(self._on_op_success)
        worker.error.connect(self._on_op_error)
        self._worker = worker
        worker.start()

    def _download_file(self):
        item = self._tree.currentItem()
        if not item:
            self._log("[SFTP] 请先选择要下载的文件")
            return
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if not entry or entry['is_dir']:
            self._log("[SFTP] 请选择一个文件（非目录）")
            return
        local_path, _ = QFileDialog.getSaveFileName(self, "保存到", entry['name'])
        if not local_path:
            return
        remote_path = self._current_path.rstrip('/') + '/' + entry['name']
        self._cleanup_worker()
        self._lbl_status.setText(f"下载中: {entry['name']}...")
        self._log(f"[SFTP] 下载: {remote_path} -> {local_path}")
        worker = SFTPOperationWorker(self._transport, 'download', local_path, remote_path)
        worker.success.connect(self._on_op_success)
        worker.error.connect(self._on_op_error)
        self._worker = worker
        worker.start()

    def _delete_selected(self):
        item = self._tree.currentItem()
        if not item:
            self._log("[SFTP] 请先选择要删除的文件或目录")
            return
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if not entry:
            return
        remote_path = self._current_path.rstrip('/') + '/' + entry['name']
        op = 'rmdir' if entry['is_dir'] else 'delete'
        self._cleanup_worker()
        self._log(f"[SFTP] 删除: {remote_path}")
        worker = SFTPOperationWorker(self._transport, op, '', remote_path)
        worker.success.connect(self._on_op_success)
        worker.error.connect(self._on_op_error)
        self._worker = worker
        worker.start()

    def _create_directory(self):
        name, ok = QInputDialog.getText(self, "新建目录", "目录名:")
        if not ok or not name:
            return
        remote_path = self._current_path.rstrip('/') + '/' + name
        self._cleanup_worker()
        self._log(f"[SFTP] 创建目录: {remote_path}")
        worker = SFTPOperationWorker(self._transport, 'mkdir', '', remote_path)
        worker.success.connect(self._on_op_success)
        worker.error.connect(self._on_op_error)
        self._worker = worker
        worker.start()

    def _on_op_success(self, msg):
        self._lbl_status.setText(msg)
        self._log(f"[SFTP] {msg}")
        self._list_directory(self._current_path)

    def _on_op_error(self, error):
        self._lbl_status.setText(f"操作失败: {error}")
        self._log(f"[SFTP] 操作失败: {error}")

    @staticmethod
    def _format_size(size):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}" if unit != 'B' else f"{size} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def closeEvent(self, event):
        # 先安全等待 worker 结束，再关闭 transport
        if self._worker is not None:
            if self._worker.isRunning():
                self._worker.wait(3000)
            self._worker.deleteLater()
            self._worker = None
        if self._transport:
            try:
                self._transport.close()
            except Exception:
                pass
            self._transport = None
            self._log("[SFTP] 已断开连接")
        super().closeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        
        # 设置窗口标题
        self.setWindowTitle("AutoWork - 自动化工作工具")
        
        # 初始化UI
        self.init_ui()
        
        # 连接信号和槽
        self.connect_signals()
    
    # 默认路径配置，首次运行时自动写入 settings.json
    DEFAULT_PATHS = {
        "exe_dir": r"C:\Users\shen_zhe\Desktop\snooker\bin64",
        "videos_dir": r"C:\Users\shen_zhe\Desktop\videos",
        "cipher_tool": r"C:\Users\shen_zhe\Desktop\videos\AESBase64CipherTool.exe",
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
            self.ui.show_log.appendPlainText(f"[警告] 保存配置失败: {e}")

    def _load_paths(self):
        """从配置加载路径，并设置实例属性"""
        settings = self._load_settings()
        self.exe_dir = settings.get("exe_dir", self.DEFAULT_PATHS["exe_dir"])
        self.videos_dir = settings.get("videos_dir", self.DEFAULT_PATHS["videos_dir"])
        self.cipher_tool = settings.get("cipher_tool", self.DEFAULT_PATHS["cipher_tool"])
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
                    self.ui.show_log.appendPlainText(f"[配置] 已恢复上次程序: {saved_exe}")
                    return

    def init_ui(self):
        """初始化UI组件"""
        # 一次性加载 settings.json 到缓存（后续所有 _load_settings 都读缓存）
        self._reload_settings_cache()
        # 加载路径配置
        self._load_paths()
        
        # 设置默认日期为当前日期，日减一 （使用 Python datetime 避免 QDate 年份异常）
        from datetime import date as py_date
        from PySide6.QtCore import QDate
        today = py_date.today()
        self.ui.date.blockSignals(True)
        self.ui.date.setDate(QDate(today.year, today.month, today.day-1))
        self.ui.date.blockSignals(False)
        
        # 初始化程序下拉框 - 扫描 snooker/bin64 目录下的 SnookerTracking*.exe
        self._load_exe_list()
        # 恢复上次选择的程序
        self._restore_exe_selection()
        
        # 初始化设备代码列表 - 扫描 videos 目录下的设备文件夹
        self._load_device_list()
        
        # 在日志区域显示欢迎信息
        self.ui.show_log.appendPlainText("欢迎使用 AutoWork 工具！")
        self.ui.show_log.appendPlainText(f"程序目录: {self.exe_dir}")
        self.ui.show_log.appendPlainText(f"视频目录: {self.videos_dir}")
        self.ui.show_log.appendPlainText("请选择程序并开始工作...")
        
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
        
        # 初始化状态栏、右键菜单、快捷键、菜单栏
        self._init_statusbar()
        self._init_context_menus()
        self._init_menubar()
        self._init_shortcuts()
        # 从 settings.json 加载并应用用户自定义设置（高亮颜色、字号、字体等）
        self._apply_highlight_color()
        self._apply_font_size()
        self._apply_font_family()
        # 替换 choose_exe 的弹出视图为自定义 QListView，配合 setMaxVisibleItems 生效
        _popup_view = QListView(self.ui.choose_exe)
        _popup_view.setUniformItemSizes(True)
        _popup_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.ui.choose_exe.setView(_popup_view)
        # P2P 状态
        self._frpc_process = None
        self._p2p_visitors = []
        self._p2p_current_index = -1
        self._ssh_worker = None
        self._ftp_worker = None
        self._auto_ssh_worker = None
        self._sftp_window = None
        self._init_p2p_panel()
    
    def _load_exe_list(self):
        """加载 snooker/bin64 目录下的 SnookerTracking*.exe 到程序下拉框"""
        import glob
        
        exe_dir = self.exe_dir
        if not os.path.exists(exe_dir):
            self.ui.show_log.appendPlainText(f"[警告] 目录不存在: {exe_dir}")
            return
        
        # 查找所有匹配的 exe 文件
        pattern = os.path.join(exe_dir, "*SnookerTracking*.exe")
        exe_files = glob.glob(pattern)
        
        if not exe_files:
            self.ui.show_log.appendPlainText(f"[警告] 未找到 SnookerTracking*.exe 文件")
            return
        
        # 清空并添加文件列表
        self.ui.choose_exe.clear()
        for exe_path in sorted(exe_files):
            exe_name = os.path.basename(exe_path)
            self.ui.choose_exe.addItem(exe_name)
        # 限制下拉列表最多显示 8 项，超出自动滚动
        self.ui.choose_exe.setMaxVisibleItems(8)
        
        self.ui.show_log.appendPlainText(f"[程序] 找到 {len(exe_files)} 个可执行文件")

    def _load_device_list(self):
        """加载 videos 目录下的设备代码文件夹到 id_list"""
        videos_dir = self.videos_dir
        if not os.path.exists(videos_dir):
            self.ui.show_log.appendPlainText(f"[警告] 目录不存在: {videos_dir}")
            return
        
        # 获取所有子目录（设备代码）
        device_codes = []
        for item in os.listdir(videos_dir):
            item_path = os.path.join(videos_dir, item)
            if os.path.isdir(item_path):
                device_codes.append(item)
        
        if not device_codes:
            self.ui.show_log.appendPlainText(f"[警告] videos 目录下没有找到设备文件夹")
            return
        
        # 清空并添加设备代码列表
        self.ui.id_list.clear()
        for code in sorted(device_codes):
            self.ui.id_list.addItem(code)
        
        self.ui.show_log.appendPlainText(f"[设备] 找到 {len(device_codes)} 个设备代码")

    def _get_selected_date_str(self):
        """获取日期选择器中的日期，格式如 2026-07-05"""
        qdate = self.ui.date.date()
        date_str = qdate.toString("yyyy-MM-dd")
        return date_str

    def _load_videos_for_device(self, device_code):
        """根据设备代码和选中日期加载日志文件到 loacl_video_list（第二列）"""
        import glob
        
        videos_dir = self.videos_dir
        device_dir = os.path.join(videos_dir, device_code)
        
        if not os.path.exists(device_dir):
            self.ui.show_log.appendPlainText(f"[警告] 设备目录不存在: {device_dir}")
            return
        
        # 获取选中日期，构建日期子目录路径
        date_str = self._get_selected_date_str()
        date_dir = os.path.join(device_dir, date_str)
        
        # 清空第二列
        self.ui.loacl_video_list.clear()
        
        if not os.path.exists(date_dir):
            self.ui.show_log.appendPlainText(f"[提示] {device_code} 下没有 {date_str} 的日志 (查找路径: {date_dir})")
            return
        
        # 查找日期目录下的 txt 和 log 文件
        log_files = glob.glob(os.path.join(date_dir, '*.txt'))
        log_files += glob.glob(os.path.join(date_dir, '*.log'))
        
        for log_path in sorted(log_files):
            # 只显示文件名，如 20260705_131009.log
            self.ui.loacl_video_list.addItem(os.path.basename(log_path))
        
        self.ui.show_log.appendPlainText(f"[日志目录] {device_code}/{date_str} 下有 {len(log_files)} 个日志文件")

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
        
        # P2P 面板信号
        self.ui.p2p_btn.toggled.connect(self._on_p2p_toggled)
        self.ui.p2p_add_btn.clicked.connect(self._on_p2p_add)
        self.ui.p2p_delete_btn.clicked.connect(self._on_p2p_delete)
        self.ui.p2p_connect_btn.clicked.connect(self._on_p2p_connect)
        self.ui.p2p_disconnect_btn.clicked.connect(self._on_p2p_disconnect)
        self.ui.p2p_visitor_list.currentRowChanged.connect(self._on_p2p_visitor_selected)
        self.ui.p2p_mode_combo.currentIndexChanged.connect(self._on_p2p_mode_changed)
        self.ui.p2p_sftp_btn.clicked.connect(self._on_sftp_btn_clicked)

    @Slot()
    def on_flush_clicked(self):
        """刷新按钮点击事件"""
        self.ui.show_log.appendPlainText("\n[操作] 刷新数据...")
        
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
        
        self.ui.show_log.appendPlainText("[刷新] 完成")
        
    @Slot()
    def on_start_clicked(self):
        """播放按钮点击事件 - 启动 SnookerTracking 程序"""
        # 如果已经有程序在运行，先结束它
        if self.running_process is not None:
            self.ui.show_log.appendPlainText("\n[警告] 已有程序正在运行，请先点击'结束'")
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
            self.ui.show_log.appendPlainText(f"\n[播放] 等待 detect.json 解码完成后启动...")
            self._update_status_running(exe_name)
        else:
            # 无需解码，直接启动
            self._launch_program(exe_path, exe_name, exe_dir)
    
    def _on_program_output(self):
        """处理程序的标准输出"""
        if self.running_process:
            output = self.running_process.readAllStandardOutput().data().decode('gb2312', errors='ignore')
            if output.strip():
                self.ui.show_log.appendPlainText(output.strip())
    
    def _on_program_error(self):
        """处理程序的错误输出"""
        if self.running_process:
            error = self.running_process.readAllStandardError().data().decode('gb2312', errors='ignore')
            if error.strip():
                self.ui.show_log.appendPlainText(f"[程序错误] {error.strip()}")
    
    def _on_program_finished(self, exit_code, exit_status):
        """程序结束时回调"""
        self.ui.show_log.appendPlainText(f"\n[程序结束] 退出码: {exit_code}")
        self.running_process = None
        self._process_suspended = False
        self.ui.pause_btn.setText("暂停")
        self._update_status_idle()
        
    @Slot()
    def on_end_clicked(self):
        """结束按钮点击事件 - 停止运行的程序"""
        if self.running_process is None:
            self.ui.show_log.appendPlainText("\n[提示] 没有正在运行的程序")
            return
        
        # 直接强制终止进程
        self.ui.show_log.appendPlainText("\n[结束] 正在终止程序...")
        self.running_process.kill()  # 强制终止
        self.ui.show_log.appendPlainText("[结束] 程序已强制终止")
        self.running_process = None
        self._process_suspended = False
        self.ui.pause_btn.setText("暂停")
        self._update_status_idle()
        
    @Slot()
    def on_open_daily_clicked(self):
        """打开 CPP 日志文件"""
        # 检查是否选中了设备
        if not self.ui.id_list.currentItem():
            self.ui.show_log.appendPlainText("[提示] 请先选择设备代码")
            return
        
        device_code = self.ui.id_list.currentItem().text()
        date_str = self._get_selected_date_str()
        daily_path = os.path.join(
            self.videos_dir, device_code, f"daily_{date_str}.txt"
        )
        
        if not os.path.exists(daily_path):
            self.ui.show_log.appendPlainText(f"[提示] CPP 日志文件不存在: {daily_path}")
            return
        
        # 用系统默认程序打开文件
        os.startfile(daily_path)
        self.ui.show_log.appendPlainText(f"[CPP日志] 已打开: {daily_path}")
        
    @Slot()
    def on_open_dir_clicked(self):
        """打开目录按钮点击事件 - 打开当前选中设备的目录"""
        if not self.ui.id_list.currentItem():
            self.ui.show_log.appendPlainText("[提示] 请先选择设备代码")
            return
        
        device_code = self.ui.id_list.currentItem().text()
        device_dir = os.path.join(self.videos_dir, device_code)
        
        if not os.path.exists(device_dir):
            self.ui.show_log.appendPlainText(f"[提示] 目录不存在: {device_dir}")
            return
        
        os.startfile(device_dir)
        self.ui.show_log.appendPlainText(f"[打开目录] {device_dir}")
    
    @Slot()
    def on_open_config_clicked(self):
        """配置按钮点击事件 - 选择打开 settings.json 或 cfg.json"""
        msg = QMessageBox(self)
        msg.setWindowTitle("打开配置文件")
        msg.setText("选择要打开的配置文件：")
        settings_btn = msg.addButton("settings.json", QMessageBox.ActionRole)
        cfg_btn = msg.addButton("cfg.json", QMessageBox.ActionRole)
        msg.addButton(QMessageBox.Cancel)
        msg.exec()
        
        clicked = msg.clickedButton()
        if clicked == settings_btn:
            path = self._get_settings_path()
        elif clicked == cfg_btn:
            path = os.path.join(self.exe_dir, "cfg.json")
        else:
            return
        
        if not os.path.exists(path):
            self.ui.show_log.appendPlainText(f"[配置] 文件不存在: {path}")
            return
        
        os.startfile(path)
        self.ui.show_log.appendPlainText(f"[配置] 已打开: {path}")
        
    @Slot()
    def _on_id_current_changed(self, current, previous):
        """第一列当前项改变（鼠标点击/键盘导航均触发）"""
        if current is not None:
            self.on_id_selected(current)

    def on_id_selected(self, item):
        """ID列表项选中事件 - 加载对应设备的日志目录"""
        device_code = item.text()
        self.ui.show_log.appendPlainText(f"\n[设备选中] {device_code}")
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
        self.ui.show_log.appendPlainText(f"\n[日志选中] {log_filename}")
        
        # 获取当前选中的设备代码
        if not self.ui.id_list.currentItem():
            return
        device_code = self.ui.id_list.currentItem().text()
        
        # 拼接完整路径：设备目录/日期/文件名
        date_str = self._get_selected_date_str()
        full_log_path = os.path.join(self.videos_dir, device_code, date_str, log_filename)
        
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
            self.ui.show_log.appendPlainText(f"[日志内容] 已加载 {line_count} 行")
            self._update_status_logs(line_count)
        except Exception as e:
            self.ui.show_log.appendPlainText(f"[错误] 无法读取日志文件: {str(e)}")
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
                self.ui.show_log.appendPlainText(
                    f"  [警告] 帧前偏移后起始帧为负值({result})，已修正为 0。"
                    f"log_frame_id={log_frame_id}, offset={offset}")
                result = 0
            self.ui.show_log.appendPlainText(f"  [模式] 帧前: {log_frame_id} - {offset} = {result}")
            return result
        elif self.ui.input_frame_set.isChecked():
            offset = self._get_frame_input_value()
            result = log_frame_id + offset
            self.ui.show_log.appendPlainText(f"  [模式] 帧后: {log_frame_id} + {offset} = {result}")
            return result
        elif self.ui.input_frame_custom.isChecked():
            custom = self._get_frame_input_value()
            self.ui.show_log.appendPlainText(f"  [模式] 自定义: {custom}")
            return custom
        else:
            offset = self._get_frame_input_value()
            result = log_frame_id - offset
            if result < 0:
                self.ui.show_log.appendPlainText(
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
        self.ui.show_log.appendPlainText(f"\n[播放] 已启动程序: {exe_name}")
        self.ui.show_log.appendPlainText(f"  - 工作目录: {exe_dir}")
        self._update_status_running(exe_name)

    def _on_decode_output(self):
        """处理解码程序的标准输出"""
        if self._decode_process:
            output = self._decode_process.readAllStandardOutput().data().decode('gb2312', errors='ignore')
            if output.strip():
                self.ui.show_log.appendPlainText(f"[detect] {output.strip()}")

    def _on_decode_error(self):
        """处理解码程序的错误输出"""
        if self._decode_process:
            error = self._decode_process.readAllStandardError().data().decode('gb2312', errors='ignore')
            if error.strip():
                self.ui.show_log.appendPlainText(f"[detect] {error.strip()}")

    def _on_decode_finished(self, exit_code, exit_status):
        """解码完成后回调：复制 detect.json 并启动主程序"""
        self._decode_process = None
        
        detect_json_path = self._pending_detect_json
        exe_path = self._pending_exe_path
        exe_name = os.path.basename(exe_path)
        exe_dir = os.path.dirname(exe_path)
        
        if exit_code != 0:
            self.ui.show_log.appendPlainText(f"[detect] 解码失败，退出码: {exit_code}")
            self._pending_exe_path = None
            self._pending_detect_json = None
            self._update_status_idle()
            return
        
        # 验证解码结果
        if not os.path.exists(detect_json_path):
            self.ui.show_log.appendPlainText(f"[detect] 警告: 解码后未生成 detect.json")
            self._pending_exe_path = None
            self._pending_detect_json = None
            self._update_status_idle()
            return
        
        # 复制 detect.json 到程序目录
        target_path = os.path.join(self.exe_dir, "detect.json")
        try:
            shutil.copy2(detect_json_path, target_path)
            self.ui.show_log.appendPlainText(f"[detect] 已更新 detect.json -> {target_path}")
        except Exception as e:
            self.ui.show_log.appendPlainText(f"[detect] 复制失败: {e}")
        
        self._pending_exe_path = None
        self._pending_detect_json = None
        
        # 继续启动主程序
        self._launch_program(exe_path, exe_name, exe_dir)

    def _prepare_detect_json(self):
        """准备 detect.json：解密并复制到程序目录。返回 True 表示正在异步解码，返回 False 表示已同步完成或跳过。"""
        # 检查是否选中了设备
        if not self.ui.id_list.currentItem():
            self.ui.show_log.appendPlainText("[detect] 未选中设备，跳过 detect.json 处理")
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
                self.ui.show_log.appendPlainText(f"[detect] 警告: {device_code} 下既没有 detect.json 也没有 detect.bin")
                return False
            self.ui.show_log.appendPlainText("[detect] detect.json 不存在，将从 detect.bin 解码")
            need_decode = True
        elif bin_exists:
            # 两者都存在，比较修改时间
            bin_mtime = os.path.getmtime(detect_bin_path)
            json_mtime = os.path.getmtime(detect_json_path)
            if bin_mtime > json_mtime:
                self.ui.show_log.appendPlainText("[detect] detect.bin 比 detect.json 更新，重新解码")
                need_decode = True
            else:
                self.ui.show_log.appendPlainText("[detect] detect.json 已是最新，无需重新解码")
        
        if need_decode:
            # 使用 QProcess 异步调用 AESBase64CipherTool.exe 解码
            cipher_tool = self.cipher_tool
            if not os.path.exists(cipher_tool):
                self.ui.show_log.appendPlainText(f"[detect] 警告: 解码工具不存在: {cipher_tool}")
                return False
            
            self._pending_detect_json = detect_json_path
            cmd = [cipher_tool, detect_bin_path, detect_json_path]
            self.ui.show_log.appendPlainText(f"[detect] 正在异步解码: {' '.join(cmd)}")
            
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
            self.ui.show_log.appendPlainText(f"[detect] 已更新 detect.json -> {target_path}")
        except Exception as e:
            self.ui.show_log.appendPlainText(f"[detect] 复制失败: {e}")
        return False

    def _update_cfg_json(self, video_path, frame):
        """更新 cfg.json 配置文件"""
        cfg_path = os.path.join(self.exe_dir, "cfg.json")
        if not os.path.exists(cfg_path):
            self.ui.show_log.appendPlainText(f"[警告] cfg.json 不存在: {cfg_path}")
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
            self.ui.show_log.appendPlainText(f"[配置] 已更新 cfg.json")
            self.ui.show_log.appendPlainText(f"  - 视频: {video_path}")
            self.ui.show_log.appendPlainText(f"  - 帧数: {frame}")
            return True
        except Exception as e:
            self.ui.show_log.appendPlainText(f"[错误] 更新 cfg.json 失败: {str(e)}")
            import traceback
            self.ui.show_log.appendPlainText(traceback.format_exc())
            return False

    @Slot()
    def on_log_selected(self, item):
        """日志列表项选中事件 - 解析日志并更新cfg.json"""
        log_line = item.text()
        self.ui.show_log.appendPlainText(f"\n[日志选中] {log_line}")
        
        # 解析日志：提取帧数
        frame_match = re.search(r'frame_id:(\d+)', log_line)
        if not frame_match:
            self.ui.show_log.appendPlainText("[警告] 日志中未找到 frame_id")
            return
        
        log_frame_id = int(frame_match.group(1))
        self.current_frame = log_frame_id
        
        # 获取当前选中的设备代码
        if not self.ui.id_list.currentItem():
            self.ui.show_log.appendPlainText("[警告] 未选择设备代码")
            return
        
        # 从第二列获取当前选中的日志文件名，推断视频文件名
        if not self.ui.loacl_video_list.currentItem():
            self.ui.show_log.appendPlainText("[警告] 未选择日志文件")
            return
        
        log_filename = self.ui.loacl_video_list.currentItem().text()
        video_name = os.path.splitext(log_filename)[0] + '.mp4'
        video_path = os.path.join(self.videos_dir, "videos", video_name).replace(os.sep, '/')
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
            self.ui.show_log.appendPlainText(f"[配置] 已保存程序选择: {exe_name}")

    @Slot()
    def on_log_double_clicked(self, item):
        """日志列表项双击事件 - 解析日志、更新cfg.json并启动程序"""
        # 如果已有程序在运行，先自动结束旧程序
        if self.running_process is not None:
            self.ui.show_log.appendPlainText("\n[双击] 检测到已有程序运行，自动结束旧程序...")
            self.on_end_clicked()
        
        # 先触发选中逻辑（更新cfg.json）
        self.on_log_selected(item)
        
        # 然后启动播放
        self.on_start_clicked()

    # ==================== 状态栏 ====================

    def _init_statusbar(self):
        """初始化底部状态栏，添加永久性标签"""
        self.status_device = QLabel("设备: 未选择")
        self.status_state = QLabel("状态: 空闲")
        self.status_logs = QLabel("日志: 0 行")
        sb = self.statusBar()
        sb.addPermanentWidget(self.status_device)
        sb.addPermanentWidget(QLabel(" | "))
        sb.addPermanentWidget(self.status_state)
        sb.addPermanentWidget(QLabel(" | "))
        sb.addPermanentWidget(self.status_logs)
        sb.showMessage("就绪", 3000)

    def _update_status_device(self, device_code):
        self.status_device.setText(f"设备: {device_code}")

    def _update_status_running(self, exe_name):
        self.status_state.setText(f"状态: 运行中 - {exe_name}")

    def _update_status_idle(self):
        self.status_state.setText("状态: 空闲")

    def _update_status_paused(self, exe_name):
        self.status_state.setText(f"状态: 已暂停 - {exe_name}")

    def _update_status_logs(self, count):
        self.status_logs.setText(f"日志: {count} 行")

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
                self.ui.show_log.appendPlainText("[播放] 程序已恢复")
                exe_name = self.ui.choose_exe.currentText()
                self._update_status_running(exe_name)
            else:
                self.ui.show_log.appendPlainText("[警告] 恢复进程失败")
        else:
            # 挂起进程
            if self._win_suspend_process(pid):
                self._process_suspended = True
                self.ui.pause_btn.setText("恢复")
                self.ui.show_log.appendPlainText("[播放] 程序已暂停")
                exe_name = self.ui.choose_exe.currentText()
                self._update_status_paused(exe_name)
            else:
                self.ui.show_log.appendPlainText("[警告] 暂停进程失败")

    @staticmethod
    def _win_suspend_process(pid):
        """Windows API: 挂起指定进程的所有线程"""
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

        h_process = ctypes.windll.kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
        if not h_process:
            return False
        try:
            snap = ctypes.windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
            if snap == -1:
                return False
            te = THREADENTRY32()
            te.dwSize = ctypes.sizeof(THREADENTRY32)
            kernel32 = ctypes.windll.kernel32
            if kernel32.Thread32First(snap, ctypes.byref(te)):
                while True:
                    if te.th32OwnerProcessID == pid:
                        h_thread = kernel32.OpenThread(THREAD_SUSPEND_RESUME, False, te.th32ThreadID)
                        if h_thread:
                            kernel32.SuspendThread(h_thread)
                            kernel32.CloseHandle(h_thread)
                    if not kernel32.Thread32Next(snap, ctypes.byref(te)):
                        break
            kernel32.CloseHandle(snap)
            return True
        finally:
            ctypes.windll.kernel32.CloseHandle(h_process)

    @staticmethod
    def _win_resume_process(pid):
        """Windows API: 恢复指定进程的所有线程"""
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

        h_process = ctypes.windll.kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
        if not h_process:
            return False
        try:
            snap = ctypes.windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
            if snap == -1:
                return False
            te = THREADENTRY32()
            te.dwSize = ctypes.sizeof(THREADENTRY32)
            kernel32 = ctypes.windll.kernel32
            if kernel32.Thread32First(snap, ctypes.byref(te)):
                while True:
                    if te.th32OwnerProcessID == pid:
                        h_thread = kernel32.OpenThread(THREAD_SUSPEND_RESUME, False, te.th32ThreadID)
                        if h_thread:
                            kernel32.ResumeThread(h_thread)
                            kernel32.CloseHandle(h_thread)
                    if not kernel32.Thread32Next(snap, ctypes.byref(te)):
                        break
            kernel32.CloseHandle(snap)
            return True
        finally:
            ctypes.windll.kernel32.CloseHandle(h_process)

    # ==================== 右键菜单 ====================

    def _init_context_menus(self):
        """为列表控件设置自定义右键菜单策略"""
        self.ui.id_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.ui.log_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.ui.loacl_video_list.setContextMenuPolicy(Qt.CustomContextMenu)

    def _id_list_context_menu(self, pos):
        """设备列表右键菜单"""
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        action_open_dir = menu.addAction("打开目录")
        action_cpp_log = menu.addAction("查看 CPP 日志")
        action = menu.exec(self.ui.id_list.mapToGlobal(pos))
        if action == action_open_dir:
            self.on_open_dir_clicked()
        elif action == action_cpp_log:
            self.on_open_daily_clicked()

    def _log_list_context_menu(self, pos):
        """日志内容列表右键菜单"""
        from PySide6.QtWidgets import QMenu
        item = self.ui.log_list.currentItem()
        if item is None:
            return
        menu = QMenu(self)
        action_copy = menu.addAction("复制此行")
        action_copy_frame = menu.addAction("复制帧数")
        action_locate = menu.addAction("在文件管理器中定位")
        action = menu.exec(self.ui.log_list.mapToGlobal(pos))
        if action == action_copy:
            if item:
                QApplication.clipboard().setText(item.text())
                self.statusBar().showMessage("已复制到剪贴板", 2000)
                self.ui.show_log.appendPlainText("[复制] 已复制当前行文本到剪贴板")
        elif action == action_copy_frame:
            frame_match = re.search(r'frame_id:(\d+)', item.text())
            if frame_match:
                frame_id = frame_match.group(1)
                QApplication.clipboard().setText(frame_id)
                self.statusBar().showMessage(f"帧数 {frame_id} 已复制到剪贴板", 2000)
                self.ui.show_log.appendPlainText(f"[复制] 帧数 {frame_id} 已复制到剪贴板")
            else:
                self.ui.show_log.appendPlainText("[提示] 当前行未找到 frame_id")
        elif action == action_locate:
            if self._current_log_path and os.path.exists(self._current_log_path):
                subprocess.run(['explorer', '/select,', self._current_log_path])
            else:
                self.ui.show_log.appendPlainText("[提示] 无法定位日志文件")

    def _loacl_video_list_context_menu(self, pos):
        """日志文件列表右键菜单"""
        from PySide6.QtWidgets import QMenu
        item = self.ui.loacl_video_list.currentItem()
        if item is None:
            return
        menu = QMenu(self)
        action_copy_name = menu.addAction("复制视频名")
        action = menu.exec(self.ui.loacl_video_list.mapToGlobal(pos))
        if action == action_copy_name:
            pure_name = os.path.splitext(item.text())[0]
            QApplication.clipboard().setText(pure_name)
            self.statusBar().showMessage(f"文件名 {pure_name} 已复制到剪贴板", 2000)
            self.ui.show_log.appendPlainText(f"[复制] 文件名 {pure_name} 已复制到剪贴板")

    # ==================== P2P 连接 ====================

    def _init_p2p_panel(self):
        """初始化 P2P 面板状态和加载已保存配置"""
        # 从 settings.json 加载已保存的 visitor 配置
        settings = self._load_settings()
        saved = settings.get("p2p_visitors", [])
        for v in saved:
            self._p2p_visitors.append(dict(v))
        self._refresh_p2p_list()
        # 初始化表单 bindPort 为随机值
        self.ui.p2p_form_port.setValue(self._get_new_random_port())
        self._update_p2p_visibility()
        self._update_p2p_buttons()

    def _get_new_random_port(self):
        """生成不冲突的随机端口（排除已添加 visitor 的端口）"""
        used_ports = {v["bindPort"] for v in self._p2p_visitors}
        return generate_random_port(exclude_ports=used_ports)

    def _on_p2p_toggled(self, checked):
        """切换 P2P 面板显示/隐藏"""
        self.ui.p2p_panel.setVisible(checked)

    def _on_p2p_add(self):
        """添加新的 visitor 配置"""
        server_name = self.ui.p2p_form_server.text().strip()
        if not server_name:
            self.ui.show_log.appendPlainText("[P2P] 请填写 serverName")
            return
        port = self.ui.p2p_form_port.value()
        # 检查端口是否与已有 visitor 冲突
        for i, v in enumerate(self._p2p_visitors):
            if v["bindPort"] == port and i != self._p2p_current_index:
                self.ui.show_log.appendPlainText(f"[P2P] 端口 {port} 已被 {v['serverName']} 使用，请更换端口")
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
        self._save_p2p_settings()
        # 添加后更新表单端口为下一个随机值
        self.ui.p2p_form_port.setValue(self._get_new_random_port())

    def _on_p2p_delete(self):
        """删除当前选中的 visitor"""
        row = self.ui.p2p_visitor_list.currentRow()
        if 0 <= row < len(self._p2p_visitors):
            self._p2p_visitors.pop(row)
            self._p2p_current_index = -1
            self._refresh_p2p_list()
            self._save_p2p_settings()

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
        """保存 visitor 配置到 settings.json"""
        self._save_settings({"p2p_visitors": self._p2p_visitors})

    def _on_p2p_connect(self):
        """连接按钮 - 根据当前模式分发连接"""
        mode = self.ui.p2p_mode_combo.currentText()
        self.ui.show_log.appendPlainText(f"[P2P] 连接按钮点击，模式: {mode}")
        if mode == "XTCP":
            self._on_xtcp_connect()
        elif mode == "SSH":
            self._on_ssh_connect()
        elif mode == "FTP":
            self._on_ftp_connect()

    def _on_p2p_disconnect(self):
        """断开按钮 - 根据当前模式分发断开"""
        mode = self.ui.p2p_mode_combo.currentText()
        if mode == "XTCP":
            self._on_xtcp_disconnect()
        elif mode == "SSH":
            self._on_ssh_disconnect()
        elif mode == "FTP":
            self._on_ftp_disconnect()

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
        # host 字段随模式切换显隐（仅第0行），账号/密码始终可见
        is_ssh_ftp = not is_xtcp
        for w in self.ui.p2p_ssh_widgets:
            w.setVisible(is_ssh_ftp)
        host_lbl = self.ui.p2p_ssh_form.itemAt(0, QFormLayout.ItemRole.LabelRole)
        if host_lbl and host_lbl.widget():
            host_lbl.widget().setVisible(is_ssh_ftp)
        self._update_p2p_buttons()

    def _on_xtcp_connect(self):
        """生成 TOML 并启动 frpc"""
        self._save_current_form()
        if not self._p2p_visitors:
            self.ui.show_log.appendPlainText("[P2P] 请先添加 visitor 配置")
            return
        if self._frpc_process is not None:
            self.ui.show_log.appendPlainText("[P2P] frpc 已在运行中")
            return
        app_dir = self._get_app_dir()
        toml_path = os.path.join(app_dir, "frpc_xtcp.toml")
        try:
            self._write_frpc_toml(toml_path)
            self.ui.show_log.appendPlainText(f"[P2P] 已生成 {toml_path}")
        except Exception as e:
            self.ui.show_log.appendPlainText(f"[P2P] 生成 TOML 失败: {e}")
            return
        frpc_exe = os.path.join(app_dir, "frpc.exe")
        if not os.path.exists(frpc_exe):
            self.ui.show_log.appendPlainText(f"[P2P] frpc.exe 不存在: {frpc_exe}")
            return
        self._frpc_process = QProcess()
        self._frpc_process.setWorkingDirectory(app_dir)
        self._frpc_process.readyReadStandardOutput.connect(self._on_frpc_output)
        self._frpc_process.readyReadStandardError.connect(self._on_frpc_error)
        self._frpc_process.finished.connect(self._on_frpc_finished)
        self._frpc_process.start(frpc_exe, ["-c", toml_path])
        self.ui.show_log.appendPlainText(f"[P2P] 已启动 frpc: {frpc_exe} -c {toml_path}")
        self._update_p2p_buttons()
        # frpc 启动后延迟尝试 SSH 自动登录
        if PARAMIKO_AVAILABLE and self._p2p_visitors:
            first_visitor = self._p2p_visitors[0]
            QTimer.singleShot(3000, lambda: self._try_auto_ssh_login(
                first_visitor["bindPort"],
                self.ui.p2p_ssh_user.text(),
                self.ui.p2p_ssh_pass.text()
            ))
            self.ui.show_log.appendPlainText(
                f"[P2P] 3秒后尝试 SSH 登录 127.0.0.1:{first_visitor['bindPort']}...")

    def _try_auto_ssh_login(self, port, username, password):
        """frpc 启动后自动尝试 SSH 登录"""
        if self._frpc_process is None:
            return
        worker = SSHWorker("127.0.0.1", port, username, password)
        # 先保存引用，再启动，防止信号先于赋值到达
        self._auto_ssh_worker = worker
        worker.finished.connect(self._on_auto_ssh_login_success)
        worker.error.connect(self._on_auto_ssh_login_failed)
        worker.start()

    def _on_auto_ssh_login_success(self, result):
        """自动 SSH 登录成功"""
        self.ui.show_log.appendPlainText(f"[P2P] SSH 自动登录成功: {result}")
        self.ui.p2p_sftp_btn.setEnabled(True)
        if self._auto_ssh_worker:
            self._auto_ssh_worker.wait(3000)
            self._auto_ssh_worker.deleteLater()
        self._auto_ssh_worker = None

    def _on_auto_ssh_login_failed(self, error):
        """自动 SSH 登录失败"""
        self.ui.show_log.appendPlainText(f"[P2P] SSH 自动登录失败: {error}")
        if self._auto_ssh_worker:
            self._auto_ssh_worker.wait(3000)
            self._auto_ssh_worker.deleteLater()
        self._auto_ssh_worker = None

    def _on_xtcp_disconnect(self):
        """停止 frpc 进程"""
        if self._frpc_process is None:
            self.ui.show_log.appendPlainText("[P2P] frpc 未在运行")
            return
        self.ui.show_log.appendPlainText("[P2P] 正在停止 frpc...")
        proc = self._frpc_process
        self._frpc_process = None  # 先置空，防止 _on_frpc_finished 重复处理
        proc.kill()
        proc.waitForFinished(3000)
        proc.deleteLater()
        # 同时清理 auto_ssh_worker（frpc 停了，SSH 也没意义了）
        if self._auto_ssh_worker:
            self._auto_ssh_worker.wait(3000)
            self._auto_ssh_worker.deleteLater()
            self._auto_ssh_worker = None
        self.ui.p2p_sftp_btn.setEnabled(False)
        self._update_p2p_buttons()
        self.ui.show_log.appendPlainText("[P2P] frpc 已停止")

    def _on_ssh_connect(self):
        """启动 SSH 连接"""
        if not PARAMIKO_AVAILABLE:
            self.ui.show_log.appendPlainText("[SSH] paramiko 未安装，请执行: pip install paramiko")
            return
        if self._ssh_worker is not None:
            self.ui.show_log.appendPlainText("[SSH] 已有连接正在运行")
            return
        host = self.ui.p2p_ssh_host.text().strip()
        if not host:
            self.ui.show_log.appendPlainText("[SSH] 请输入主机地址")
            return
        port = 22
        self._ssh_worker = SSHWorker(
            host, port,
            self.ui.p2p_ssh_user.text(), self.ui.p2p_ssh_pass.text()
        )
        self._ssh_worker.finished.connect(self._on_ssh_finished)
        self._ssh_worker.error.connect(self._on_ssh_error)
        self._ssh_worker.start()
        self.ui.show_log.appendPlainText(f"[SSH] 正在连接 {host}:{port}...")
        self._update_p2p_buttons()

    def _on_ssh_disconnect(self):
        """断开 SSH 连接"""
        if self._ssh_worker is None:
            self.ui.show_log.appendPlainText("[SSH] 未连接")
            return
        worker = self._ssh_worker
        self._ssh_worker = None
        worker.close()
        worker.quit()
        worker.wait(3000)
        worker.deleteLater()
        self.ui.p2p_sftp_btn.setEnabled(False)
        self._update_p2p_buttons()
        self.ui.show_log.appendPlainText("[SSH] 已断开")

    def _on_ssh_finished(self, result):
        """SSH 连接成功回调"""
        self.ui.show_log.appendPlainText(f"[SSH] 连接成功: {result}")
        self.ui.p2p_sftp_btn.setEnabled(True)
        # 线程已结束，用 deleteLater 安全销毁 C++ 对象
        # _ssh_worker 引用保留，断开按钮需通过它关闭 paramiko 客户端
        if self._ssh_worker:
            self._ssh_worker.deleteLater()

    def _on_ssh_error(self, error):
        """SSH 连接失败回调"""
        self.ui.show_log.appendPlainText(f"[SSH] 连接失败: {error}")
        if self._ssh_worker:
            self._ssh_worker.wait(3000)
            self._ssh_worker.deleteLater()
        self._ssh_worker = None
        self._update_p2p_buttons()

    def _on_ftp_connect(self):
        """启动 FTP 连接"""
        if self._ftp_worker is not None:
            self.ui.show_log.appendPlainText("[FTP] 已有连接正在运行")
            return
        host = self.ui.p2p_ssh_host.text().strip()
        if not host:
            self.ui.show_log.appendPlainText("[FTP] 请输入主机地址")
            return
        port = 21
        self._ftp_worker = FTPWorker(
            host, port,
            self.ui.p2p_ssh_user.text(), self.ui.p2p_ssh_pass.text()
        )
        self._ftp_worker.finished.connect(self._on_ftp_finished)
        self._ftp_worker.error.connect(self._on_ftp_error)
        self._ftp_worker.start()
        self.ui.show_log.appendPlainText(f"[FTP] 正在连接 {host}:{port}...")
        self._update_p2p_buttons()

    def _on_ftp_disconnect(self):
        """断开 FTP 连接"""
        if self._ftp_worker is None:
            self.ui.show_log.appendPlainText("[FTP] 未连接")
            return
        worker = self._ftp_worker
        self._ftp_worker = None
        worker.close()
        worker.quit()
        worker.wait(3000)
        worker.deleteLater()
        self._update_p2p_buttons()
        self.ui.show_log.appendPlainText("[FTP] 已断开")

    def _on_ftp_finished(self, result):
        """FTP 连接成功回调"""
        self.ui.show_log.appendPlainText(f"[FTP] 连接成功:\n{result}")

    def _on_ftp_error(self, error):
        """FTP 连接失败回调"""
        self.ui.show_log.appendPlainText(f"[FTP] 连接失败: {error}")
        if self._ftp_worker:
            self._ftp_worker.wait(3000)
            self._ftp_worker.deleteLater()
        self._ftp_worker = None
        self._update_p2p_buttons()

    def _write_frpc_toml(self, path):
        """手动写入 frpc_xtcp.toml 文件（含全局头部配置，遍历所有 visitor）"""
        with open(path, 'w', encoding='utf-8') as f:
            # 全局头部配置
            f.write('serverAddr = "49.235.34.253"\n')
            f.write('serverPort = 7900\n')
            f.write('auth.method = "token"\n')
            f.write('auth.token = "123"\n')
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
                self.ui.show_log.appendPlainText(f"[frpc] {output.strip()}")

    def _on_frpc_error(self):
        """处理 frpc 错误输出"""
        if self._frpc_process:
            error = self._frpc_process.readAllStandardError().data().decode('utf-8', errors='ignore')
            if error.strip():
                self.ui.show_log.appendPlainText(f"[frpc] {error.strip()}")

    def _on_frpc_finished(self, exit_code, exit_status):
        """frpc 进程结束回调"""
        self.ui.show_log.appendPlainText(f"[P2P] frpc 已退出，退出码: {exit_code}")
        self._frpc_process = None
        self._update_p2p_buttons()

    def _update_p2p_buttons(self):
        """更新连接/断开按钮状态"""
        mode = self.ui.p2p_mode_combo.currentText()
        if mode == "XTCP":
            running = self._frpc_process is not None
        elif mode == "SSH":
            running = self._ssh_worker is not None
        elif mode == "FTP":
            running = self._ftp_worker is not None
        else:
            running = False
        self.ui.p2p_connect_btn.setEnabled(not running)
        self.ui.p2p_disconnect_btn.setEnabled(running)

    def _on_sftp_btn_clicked(self):
        """打开 SFTP 文件管理窗口"""
        if not PARAMIKO_AVAILABLE:
            self.ui.show_log.appendPlainText("[SFTP] paramiko 未安装")
            return
        mode = self.ui.p2p_mode_combo.currentText()
        if mode == "XTCP":
            # XTCP 模式：连接到 127.0.0.1:bindPort
            if not self._p2p_visitors:
                self.ui.show_log.appendPlainText("[SFTP] 请先添加 visitor 配置")
                return
            host = "127.0.0.1"
            port = self._p2p_visitors[0]["bindPort"]
        elif mode == "SSH":
            host = self.ui.p2p_ssh_host.text().strip()
            port = 22
        else:
            self.ui.show_log.appendPlainText("[SFTP] SFTP 仅支持 XTCP/SSH 模式")
            return
        username = self.ui.p2p_ssh_user.text()
        password = self.ui.p2p_ssh_pass.text()
        if not host:
            self.ui.show_log.appendPlainText("[SFTP] 主机地址不能为空")
            return
        self.ui.show_log.appendPlainText(f"[SFTP] 打开文件管理: {host}:{port}")
        self._sftp_window = SFTPWindow(
            host, port, username, password,
            log_callback=lambda msg: self.ui.show_log.appendPlainText(msg),
            parent=self
        )
        self._sftp_window.show()

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
        """初始化顶部菜单栏"""
        menubar = self.menuBar()

        # 「功能」菜单
        func_menu = menubar.addMenu("功能")
        act_sc = func_menu.addAction("修改快捷键")
        act_sc.triggered.connect(lambda: QTimer.singleShot(0, self._on_modify_shortcuts))
        act_hc = func_menu.addAction("高亮颜色设置")
        act_hc.triggered.connect(lambda: QTimer.singleShot(0, self._on_highlight_color))

        # 「主题」菜单
        theme_menu = menubar.addMenu("主题")
        act_fs = theme_menu.addAction("字号大小")
        act_fs.triggered.connect(lambda: QTimer.singleShot(0, self._on_font_size))
        act_scale = theme_menu.addAction("界面缩放")
        act_scale.triggered.connect(lambda: QTimer.singleShot(0, self._on_dpi_scale))
        act_ff = theme_menu.addAction("字体设置")
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
            self.ui.show_log.appendPlainText(f"[配置] 已更新快捷键: {new_sc}")

    def _on_highlight_color(self):
        """弹出颜色选择对话框修改日志高亮颜色"""
        current = self.highlight_color
        color = QColorDialog.getColor(current, self, "选择高亮颜色")
        if color.isValid():
            self.highlight_color = color
            self._save_settings({
                "highlight_color": [color.red(), color.green(), color.blue()]
            })
            self.ui.show_log.appendPlainText(
                f"[配置] 已更新高亮颜色: RGB({color.red()},{color.green()},{color.blue()})")

    def _on_font_size(self):
        """弹出字号选择对话框"""
        settings = self._load_settings()
        current = settings.get("font_size", 10)
        val, ok = QInputDialog.getInt(self, "字号大小", "请输入字号 (10~20):", current, 10, 20, 1)
        if ok:
            self._save_settings({"font_size": val})
            self._apply_font_size()
            self.ui.show_log.appendPlainText(f"[配置] 已更新字号: {val}pt")

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
            self.ui.show_log.appendPlainText(f"[配置] 已设置缩放: {val}%（重启后生效）")

    def _on_font_family(self):
        """弹出字体选择对话框"""
        settings = self._load_settings()
        current_family = settings.get("font_family", "")
        current_font = QFont(current_family) if current_family else QFont()
        font, ok = QFontDialog.getFont(current_font, self, "选择字体")
        if ok:
            self._save_settings({"font_family": font.family()})
            self._apply_font_family()
            self.ui.show_log.appendPlainText(f"[配置] 已更新字体: {font.family()}")

    def _on_about(self) :
        """显示关于对话框"""
        QMessageBox.about(
            self,
            "关于",
            "AutoWork - 自动化工作工具\n"
            "版本: 1.1\n\n"
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
        settings = self._load_settings()
        size = settings.get("font_size", None)
        if size:
            font = QApplication.font()
            font.setPointSize(size)
            QApplication.setFont(font)

    def _apply_font_family(self):
        """从 settings.json 加载并应用全局字体"""
        settings = self._load_settings()
        family = settings.get("font_family", None)
        if family:
            font = QApplication.font()
            font.setFamily(family)
            QApplication.setFont(font)

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
    
    # 创建并显示主窗口
    window = MainWindow()
    window.show()
    
    # 运行应用程序
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
