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
from datetime import datetime
from PySide6.QtWidgets import (QApplication, QMainWindow, QMessageBox, QLabel,
    QWidget, QListWidgetItem, QMenu, QColorDialog, QFontDialog, QInputDialog,
    QDialog, QVBoxLayout, QHBoxLayout, QKeySequenceEdit, QDialogButtonBox,
    QComboBox, QSpinBox, QListView, QAbstractItemView, QFrame, QFormLayout,
    QLineEdit, QTreeWidget, QTreeWidgetItem, QHeaderView, QFileDialog,
    QPushButton, QProgressDialog, QPlainTextEdit, QSplitter, QProgressBar,
    QTableWidget, QTableWidgetItem)
from PySide6.QtCore import Slot, QProcess, Qt, QTimer, QThread, Signal
from PySide6.QtGui import QColor, QBrush, QShortcut, QKeySequence, QFont, QAction, QTextCursor
from autowork_with_table import Ui_MainWindow
from p2p import generate_random_port, is_port_in_use

try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False


class TCPWorker(QThread):
    """TCP 连接工作线程"""
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
        finally:
            # run() 结束后立即关闭 paramiko client，避免资源泄漏
            self.close()

    def close(self):
        if self._client:
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
    """异步 SFTP 操作工作线程（上传/下载/删除/创建目录），支持传输进度"""
    success = Signal(str)
    error = Signal(str)
    progress = Signal(int, int)  # (transferred_bytes, total_bytes)

    def __init__(self, transport, operation, local_path='', remote_path='', file_size=0):
        super().__init__()
        self.transport = transport
        self.operation = operation
        self.local_path = local_path
        self.remote_path = remote_path
        self.file_size = file_size

    def _progress_cb(self, transferred, total):
        self.progress.emit(transferred, total)

    def run(self):
        sftp = None
        try:
            sftp = paramiko.SFTPClient.from_transport(self.transport)
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
        except Exception as e:
            self.error.emit(str(e))
        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass


# 网络就绪类错误关键词，匹配时自动重试，认证失败等错误不重试
_RETRYABLE_KEYWORDS = ('Error reading SSH protocol banner', 'Server connection dropped')
_RETRY_MAX = 5
_RETRY_DELAY = 2  # 秒


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

    def run(self):
        for attempt in range(1, _RETRY_MAX + 1):
            try:
                transport = paramiko.Transport((self.host, self.port))
                transport.connect(username=self.username, password=self.password)
                self.connected.emit(transport)
                return  # 成功，立即退出
            except Exception as e:
                err_msg = str(e)
                # 仅网络就绪类错误才重试，认证失败等直接报错
                if any(kw in err_msg for kw in _RETRYABLE_KEYWORDS) and attempt < _RETRY_MAX:
                    print(f'[SFTP] 连接失败 ({err_msg})，正在重试 ({attempt}/{_RETRY_MAX})...')
                    time.sleep(_RETRY_DELAY)
                    continue
                # 不可重试的错误 或 已达最大重试次数
                if attempt > 1:
                    self.error.emit(f'连接失败（已重试{_RETRY_MAX}次）: {err_msg}')
                else:
                    self.error.emit(err_msg)
                return


class SFTPWindow(QDialog):
    """SFTP 文件管理窗口（左右双面板，类似 Xftp）"""

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
        root.addWidget(self._transfer_table)

        # ---- 操作按钮栏
        btn_row = QHBoxLayout()
        self._btn_upload = QPushButton('上传文件 ▶')
        self._btn_upload.setAutoDefault(False)
        self._btn_upload.clicked.connect(self._upload_file)
        btn_row.addWidget(self._btn_upload)
        self._btn_download = QPushButton('◀ 下载文件')
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
        if self._transport is None:
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
        if data is None:
            item = self._local_tree.currentItem()
            if not item:
                self._log('[SFTP] 请先在左侧本地面板选择一个文件')
                return
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if not data or data['is_dir']:
                self._log('[SFTP] 请选择一个文件（非目录）')
                return
        local_path = data['path']
        remote_path = self._remote_path.rstrip('/') + '/' + data['name']
        file_size = os.path.getsize(local_path) if os.path.isfile(local_path) else 0
        self._log(f'[SFTP] 上传: {local_path} -> {remote_path}')
        worker = SFTPOperationWorker(self._transport, 'upload', local_path, remote_path, file_size=file_size)
        self._start_transfer_op(worker, data['name'], '上传', file_size)

    def _download_file(self, entry=None):
        if entry is None:
            item = self._tree.currentItem()
            if not item:
                self._log('[SFTP] 请先在右侧远程面板选择一个文件')
                return
            entry = item.data(0, Qt.ItemDataRole.UserRole)
            if not entry or entry['is_dir']:
                self._log('[SFTP] 请选择一个文件（非目录）')
                return
        remote_path = self._remote_path.rstrip('/') + '/' + entry['name']
        local_path = os.path.join(self._local_path, entry['name'])
        file_size = entry.get('size', 0)
        self._log(f'[SFTP] 下载: {remote_path} -> {local_path}')
        worker = SFTPOperationWorker(self._transport, 'download', local_path, remote_path, file_size=file_size)
        self._start_transfer_op(worker, entry['name'], '下载', file_size)

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
        # 记录 worker 信息
        info = {'worker': worker, 'row': row, 'start_time': time.time()}
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
        elapsed = time.time() - info['start_time']
        speed = transferred / elapsed if elapsed > 0.5 else 0
        speed_item = self._transfer_table.item(row, 2)
        if speed_item:
            speed_item.setText(f'{self._format_size(speed)}/s')

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
        worker = SFTPOperationWorker(self._transport, op, '', remote_path)
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
        worker = SFTPOperationWorker(self._transport, 'mkdir', '', remote_path)
        worker.success.connect(self._on_quick_op_success)
        worker.error.connect(self._on_quick_op_error)
        tid = self._next_transfer_id
        self._next_transfer_id += 1
        self._transfer_workers[tid] = {'worker': worker, 'row': -1, 'start_time': time.time()}
        worker.success.connect(lambda msg, _tid=tid: self._safe_delete_transfer_worker(_tid))
        worker.error.connect(lambda err, _tid=tid: self._safe_delete_transfer_worker(_tid))
        worker.start()

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

    # ------------------------------------------------------------------ 关闭
    def closeEvent(self, event):
        # 清理异步连接 worker
        self._cleanup_connect_worker()
        # 清理列目录 worker
        self._cleanup_list_worker()
        # 清理所有传输 worker
        for tid in list(self._transfer_workers.keys()):
            self._safe_delete_transfer_worker(tid)
        if self._transport:
            try:
                self._transport.close()
            except Exception:
                pass
            self._transport = None
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

    def run(self):
        for attempt in range(1, _RETRY_MAX + 1):
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(
                    self.host, port=self.port,
                    username=self.username, password=self.password,
                    timeout=10
                )
                self.connected.emit(client)
                return  # 成功，立即退出
            except Exception as e:
                err_msg = str(e)
                if any(kw in err_msg for kw in _RETRYABLE_KEYWORDS) and attempt < _RETRY_MAX:
                    print(f'[SSH] 连接失败 ({err_msg})，正在重试 ({attempt}/{_RETRY_MAX})...')
                    time.sleep(_RETRY_DELAY)
                    continue
                if attempt > 1:
                    self.error.emit(f'连接失败（已重试{_RETRY_MAX}次）: {err_msg}')
                else:
                    self.error.emit(err_msg)
                return

class SSHExecWorker(QThread):
    """异步执行 SSH 命令的工作线程（使用 exec_command，无持久 shell）"""
    output = Signal(str)
    error = Signal(str)
    finished = Signal()

    def __init__(self, client, command):
        super().__init__()
        self._client = client
        self._command = command

    def run(self):
        try:
            stdin, stdout, stderr = self._client.exec_command(self._command)
            out = stdout.read().decode('utf-8', errors='ignore')
            err = stderr.read().decode('utf-8', errors='ignore')
            if out:
                self.output.emit(out)
            if err:
                self.error.emit(err)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()


class SSHTerminalWindow(QDialog):
    """SSH 终端窗口（exec_command 模式，底部输入框）"""

    def __init__(self, host, port, username, password, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"SSH 终端 - {host}:{port}")
        self.resize(800, 500)
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._client = None
        self._connect_worker = None
        self._exec_worker = None
        self._init_ui()
        QTimer.singleShot(100, self._connect_ssh)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        # 输出区域
        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        self._output.setStyleSheet(
            "QPlainTextEdit { background-color: #1e1e1e; color: #00ff00; "
            "font-family: Consolas, 'Courier New', monospace; font-size: 11pt; }"
        )
        layout.addWidget(self._output)

        # 输入区域
        input_layout = QHBoxLayout()
        self._prompt_label = QLabel("$")
        self._prompt_label.setStyleSheet("color: #00ff00; font-family: Consolas; font-size: 11pt;")
        input_layout.addWidget(self._prompt_label)

        self._input = QLineEdit()
        self._input.setStyleSheet(
            "QLineEdit { background-color: #2d2d2d; color: #00ff00; "
            "font-family: Consolas, 'Courier New', monospace; font-size: 11pt; }"
        )
        self._input.setPlaceholderText("输入命令，回车执行...")
        self._input.returnPressed.connect(self._execute_command)
        self._input.setEnabled(False)  # 连接成功前禁用
        input_layout.addWidget(self._input)

        self._send_btn = QPushButton("执行")
        self._send_btn.clicked.connect(self._execute_command)
        self._send_btn.setEnabled(False)
        input_layout.addWidget(self._send_btn)

        self._cmd_btn = QPushButton("在 CMD 中打开")
        self._cmd_btn.clicked.connect(self._open_in_cmd)
        input_layout.addWidget(self._cmd_btn)

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
        worker.finished.connect(self._on_exec_finished)
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
        # 关闭 SSH client
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
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
        # 远程状态
        self._frpc_process = None
        self._p2p_visitors = []
        self._p2p_current_index = -1
        self._tcp_worker = None
        self._sftp_window = None
        self._ssh_terminal_window = None
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

    # ==================== 远程连接 ====================

    def _init_p2p_panel(self):
        """初始化远程面板状态，从已有的 frpc_xtcp.toml 恢复 visitor 列表"""
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
                self.ui.show_log.appendPlainText(f"[远程] 从 TOML 恢复了 {len(self._p2p_visitors)} 个 visitor")
        except Exception as e:
            self.ui.show_log.appendPlainText(f"[远程] 解析 TOML 失败: {e}")

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
            self.ui.show_log.appendPlainText("[远程] 请填写 serverName")
            return
        port = self.ui.p2p_form_port.value()
        # 检查端口是否与已有 visitor 冲突
        for i, v in enumerate(self._p2p_visitors):
            if v["bindPort"] == port and i != self._p2p_current_index:
                self.ui.show_log.appendPlainText(f"[远程] 端口 {port} 已被 {v['serverName']} 使用，请更换端口")
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
        self.ui.show_log.appendPlainText(f"[远程] 连接按钮点击，模式: {mode}")
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
            self.ui.show_log.appendPlainText("[远程] 请先添加 visitor 配置")
            return
        if self._frpc_process is not None:
            self.ui.show_log.appendPlainText("[远程] frpc 已在运行中")
            return
        app_dir = self._get_app_dir()
        toml_path = os.path.join(app_dir, "frpc_xtcp.toml")
        try:
            self._write_frpc_config(toml_path)
            self.ui.show_log.appendPlainText(f"[远程] 已生成 {toml_path}")
        except Exception as e:
            self.ui.show_log.appendPlainText(f"[远程] 生成配置失败: {e}")
            return
        frpc_exe = os.path.join(app_dir, "frpc.exe")
        if not os.path.exists(frpc_exe):
            self.ui.show_log.appendPlainText(f"[远程] frpc.exe 不存在: {frpc_exe}")
            return
        self._frpc_process = QProcess()
        self._frpc_process.setWorkingDirectory(app_dir)
        self._frpc_process.readyReadStandardOutput.connect(self._on_frpc_output)
        self._frpc_process.readyReadStandardError.connect(self._on_frpc_error)
        self._frpc_process.finished.connect(self._on_frpc_finished)
        self._frpc_process.start(frpc_exe, ["-c", toml_path])
        self.ui.show_log.appendPlainText(f"[远程] 已启动 frpc: {frpc_exe} -c {toml_path}")
        self._update_p2p_buttons()

    def _on_xtcp_disconnect(self):
        """停止 frpc 进程"""
        if self._frpc_process is None:
            self.ui.show_log.appendPlainText("[远程] frpc 未在运行")
            return
        self.ui.show_log.appendPlainText("[远程] 正在停止 frpc...")
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
        self.ui.show_log.appendPlainText("[远程] frpc 已停止")

    def _on_tcp_connect(self):
        """启动 TCP 连接"""
        if not PARAMIKO_AVAILABLE:
            self.ui.show_log.appendPlainText("[TCP] paramiko 未安装，请执行: pip install paramiko")
            return
        # 增加对 isRunning 的检查，防止残留引用误判
        if self._tcp_worker is not None and self._tcp_worker.isRunning():
            self.ui.show_log.appendPlainText("[TCP] 已有连接正在运行")
            return
        # 清理残留的旧 worker 引用
        if self._tcp_worker is not None:
            self._tcp_worker.deleteLater()
            self._tcp_worker = None
        host = self.ui.p2p_ssh_host.text().strip()
        if not host:
            self.ui.show_log.appendPlainText("[TCP] 请输入主机地址")
            return
        port = self.ui.p2p_ssh_port.value()
        self._tcp_worker = TCPWorker(
            host, port,
            self.ui.p2p_ssh_user.text(), self.ui.p2p_ssh_pass.text()
        )
        self._tcp_worker.finished.connect(self._on_tcp_finished)
        self._tcp_worker.error.connect(self._on_tcp_error)
        self._tcp_worker.start()
        self.ui.show_log.appendPlainText(f"[TCP] 正在连接 {host}:{port}...")
        self._update_p2p_buttons()

    def _on_tcp_disconnect(self):
        """断开 TCP 连接"""
        if self._tcp_worker is None:
            self.ui.show_log.appendPlainText("[TCP] 未连接")
            return
        worker = self._tcp_worker
        self._tcp_worker = None
        # 非阻塞清理：运行中的 worker 等 finished 后再 deleteLater
        if worker.isRunning():
            worker.close()
            worker.quit()
            worker.finished.connect(worker.deleteLater)
        else:
            worker.deleteLater()
        self.ui.p2p_sftp_btn.setEnabled(False)
        self.ui.p2p_ssh_terminal_btn.setEnabled(False)
        # 断开时关闭已打开的 SFTP/SSH 终端窗口
        self._close_p2p_windows()
        self._update_p2p_buttons()
        self.ui.show_log.appendPlainText("[TCP] 已断开")

    def _on_tcp_finished(self, result):
        """TCP 连接成功回调"""
        self.ui.show_log.appendPlainText(f"[TCP] 连接成功: {result}")
        # 仅在 TCP 真正成功后才启用按钮
        self.ui.p2p_sftp_btn.setEnabled(True)
        self.ui.p2p_ssh_terminal_btn.setEnabled(True)
        # 线程结束后安全销毁并清空引用
        if self._tcp_worker:
            self._tcp_worker.deleteLater()
            self._tcp_worker = None

    def _on_tcp_error(self, error):
        """TCP 连接失败回调"""
        self.ui.show_log.appendPlainText(f"[TCP] 连接失败: {error}")
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
            self.ui.show_log.appendPlainText("[远程] settings.json 中未找到 frpc_server，已自动生成默认配置")
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
                self.ui.show_log.appendPlainText(f"[frpc] {output.strip()}")

    def _on_frpc_error(self):
        """处理 frpc 错误输出"""
        if self._frpc_process:
            error = self._frpc_process.readAllStandardError().data().decode('utf-8', errors='ignore')
            if error.strip():
                self.ui.show_log.appendPlainText(f"[frpc] {error.strip()}")

    def _on_frpc_finished(self, exit_code, exit_status):
        """frpc 进程结束回调"""
        self.ui.show_log.appendPlainText(f"[远程] frpc 已退出，退出码: {exit_code}")
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

    def _on_sftp_btn_clicked(self):
        """打开 SFTP 文件管理窗口"""
        if not PARAMIKO_AVAILABLE:
            self.ui.show_log.appendPlainText("[SFTP] paramiko 未安装")
            return
        mode = self.ui.p2p_mode_combo.currentText()
        server_name = ''
        if mode == "XTCP":
            # XTCP 模式：连接到 127.0.0.1:当前选中 visitor 的 bindPort
            if not self._p2p_visitors:
                self.ui.show_log.appendPlainText("[SFTP] 请先添加 visitor 配置")
                return
            idx = self._p2p_current_index
            if not (0 <= idx < len(self._p2p_visitors)):
                self.ui.show_log.appendPlainText("[SFTP] 请先在列表中选择一个 visitor")
                return
            host = "127.0.0.1"
            port = self._p2p_visitors[idx]["bindPort"]
            server_name = self._p2p_visitors[idx].get("serverName", "")
        elif mode == "TCP":
            host = self.ui.p2p_ssh_host.text().strip()
            port = self.ui.p2p_ssh_port.value()
        else:
            self.ui.show_log.appendPlainText("[SFTP] SFTP 仅支持 XTCP/TCP 模式")
            return
        username = self.ui.p2p_ssh_user.text()
        password = self.ui.p2p_ssh_pass.text()
        if not host:
            self.ui.show_log.appendPlainText("[SFTP] 主机地址不能为空")
            return
        self.ui.show_log.appendPlainText(f"[SFTP] 打开文件管理: {server_name or host}:{port}")
        self._sftp_window = SFTPWindow(
            host, port, username, password,
            server_name=server_name,
            log_callback=lambda msg: self.ui.show_log.appendPlainText(msg),
            parent=self
        )
        self._sftp_window.show()

    def _on_ssh_terminal_btn_clicked(self):
        """打开 SSH 终端窗口"""
        if not PARAMIKO_AVAILABLE:
            self.ui.show_log.appendPlainText("[SSH] paramiko 未安装")
            return
        mode = self.ui.p2p_mode_combo.currentText()
        if mode == "XTCP":
            if not self._p2p_visitors:
                self.ui.show_log.appendPlainText("[SSH] 请先添加 visitor 配置")
                return
            idx = self._p2p_current_index
            if not (0 <= idx < len(self._p2p_visitors)):
                self.ui.show_log.appendPlainText("[SSH] 请先在列表中选择一个 visitor")
                return
            host = "127.0.0.1"
            port = self._p2p_visitors[idx]["bindPort"]
        elif mode == "TCP":
            host = self.ui.p2p_ssh_host.text().strip()
            port = self.ui.p2p_ssh_port.value()
        else:
            self.ui.show_log.appendPlainText("[SSH] SSH 终端仅支持 XTCP/TCP 模式")
            return
        username = self.ui.p2p_ssh_user.text()
        password = self.ui.p2p_ssh_pass.text()
        if not host:
            self.ui.show_log.appendPlainText("[SSH] 主机地址不能为空")
            return
        self.ui.show_log.appendPlainText(f"[SSH] 打开终端: {host}:{port}")
        self._ssh_terminal_window = SSHTerminalWindow(
            host, port, username, password, parent=self
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
