# -*- coding: utf-8 -*-
"""SFTP 文件管理窗口"""

import os
import time
import shutil
import subprocess

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QWidget, QMenu, QInputDialog, QLineEdit, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QPushButton, QPlainTextEdit, QSplitter, QProgressBar,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QMessageBox, QApplication)
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QShortcut, QKeySequence

from core.conn_logger import conn_logger
from core.utils import safe_close_transport
from workers.network_workers import (
    SFTPConnectWorker, SFTPListWorker, SFTPOperationWorker, SFTPDirTransferWorker,
)


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
        self._conn_params = (host, port, username, password)
        self._transport = None
        self._remote_path = '/home'
        self._remote_entries = []
        _desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        self._local_path = _desktop if os.path.isdir(_desktop) else os.path.expanduser('~')
        self._local_entries = []
        self._log = log_callback or (lambda msg: None)
        self._connect_worker = None
        self._list_worker = None
        self._list_generation = 0
        self._listing = False
        self._pending_remote_path = None
        self._transfer_workers = {}
        self._next_transfer_id = 0
        self._init_ui()
        QTimer.singleShot(100, self._connect_and_list)

    # ------------------------------------------------------------------ UI 构建
    def _init_ui(self):
        root = QVBoxLayout(self)
        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧 - 本地文件
        self._left_panel = QWidget()
        left_lay = QVBoxLayout(self._left_panel)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_bar = QHBoxLayout()
        self._btn_local_up = QPushButton('.. 上级')
        self._btn_local_up.setAutoDefault(False)
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

        # 本地底部搜索框
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
        self._btn_up.setAutoDefault(False)
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

        # 远程底部搜索框
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
        for c in range(3):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hdr.resizeSection(0, 280)
        hdr.resizeSection(1, 180)
        hdr.resizeSection(2, 100)
        self._transfer_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._transfer_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._transfer_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._transfer_table.verticalHeader().setDefaultSectionSize(24)
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
        self._lbl_status.setText('正在连接...')
        self._list_local(self._local_path)
        worker = SFTPConnectWorker(self._host, self._port, self._username, self._password)
        worker.connected.connect(self._on_sftp_connect_success)
        worker.error.connect(self._on_sftp_connect_error)
        self._connect_worker = worker
        worker.start()

    def _on_sftp_connect_success(self, transport):
        self._transport = transport
        self._log(f'[SFTP] 已连接到 {self._host}:{self._port}')
        self._lbl_status.setText('已连接')
        self._list_remote(self._remote_path)
        self._cleanup_connect_worker()

    def _on_sftp_connect_error(self, error):
        self._log(f'[SFTP] 连接失败: {error}')
        self._lbl_status.setText(f'连接失败: {error}')
        self._cleanup_connect_worker()

    def _cleanup_connect_worker(self):
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
        if self._list_worker is not None:
            w = self._list_worker
            self._list_worker = None
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
                w.finished.connect(w.deleteLater)
                self._listing = False
            else:
                w.deleteLater()

    def _safe_delete_transfer_worker(self, tid):
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
        if self._listing:
            self._pending_remote_path = path
            self._lbl_status.setText(f'等待加载: {path}')
            return
        self._cleanup_list_worker()
        self._list_generation += 1
        gen = self._list_generation
        self._listing = True
        self._lbl_status.setText(f'加载中: {path}')
        worker = SFTPListWorker(self._transport, path)
        worker.result.connect(self._on_list_result)
        worker.error.connect(self._on_list_error)
        worker.finished.connect(self._on_list_worker_finished)
        worker._list_gen = gen
        self._list_worker = worker
        worker.start()

    def _on_list_worker_finished(self):
        if self._list_worker is not None and not self._list_worker.isRunning():
            self._list_worker.deleteLater()
            self._list_worker = None

    def _on_list_result(self, path, entries):
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
        self._process_pending_remote_path()

    def _on_list_error(self, error):
        worker = self.sender()
        if worker and hasattr(worker, '_list_gen') and worker._list_gen != self._list_generation:
            return
        self._listing = False
        self._lbl_status.setText(f'列表失败: {error}')
        self._log(f'[SFTP] 列表失败: {error}')
        self._process_pending_remote_path()

    def _process_pending_remote_path(self):
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
        if os.path.normcase(os.path.normpath(path)) == os.path.normcase(os.path.normpath(self._local_path)):
            return
        if os.path.isdir(path):
            self._list_local(path)
        else:
            self._lbl_status.setText(f'本地路径不存在: {path}')

    def _on_remote_path_entered(self):
        path = self._edit_remote_path.text().strip()
        if path:
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
        from datetime import datetime
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
            return
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
        dir_name = entry['name']
        remote_dir = self._remote_path.rstrip('/') + '/' + dir_name
        local_dir = os.path.join(self._local_path, dir_name)
        self._log(f'[SFTP] 下载目录: {remote_dir} -> {local_dir}')
        worker = SFTPDirTransferWorker(self._conn_params, 'download_dir',
                                       local_dir=local_dir, remote_dir=remote_dir, dir_name=dir_name)
        self._start_transfer_op(worker, f'[目录] {dir_name}', '下载', 0)

    def _start_transfer_op(self, worker, filename, op_label, file_size):
        tid = self._next_transfer_id
        self._next_transfer_id += 1
        row = self._transfer_table.rowCount()
        self._transfer_table.insertRow(row)
        self._transfer_table.setItem(row, 0, QTableWidgetItem(f'{op_label}: {filename}'))
        pb = QProgressBar()
        pb.setRange(0, 100)
        pb.setValue(0)
        self._transfer_table.setCellWidget(row, 1, pb)
        self._transfer_table.setItem(row, 2, QTableWidgetItem('0 B/s'))
        self._transfer_table.setItem(row, 3, QTableWidgetItem('传输中'))
        now = time.time()
        info = {'worker': worker, 'row': row, 'start_time': now,
                'last_bytes': 0, 'last_time': now, 'speed': 0.0}
        self._transfer_workers[tid] = info
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
        menu = QMenu(self)
        row = self._transfer_table.rowAt(pos.y())
        has_selection = row >= 0
        has_tasks = self._transfer_table.rowCount() > 0
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
        for tid, info in self._transfer_workers.items():
            if info['row'] == row:
                return tid
        return None

    def _transfer_pause_row(self, row):
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
        tid = self._find_tid_by_row(row)
        if tid is None:
            return
        info = self._transfer_workers[tid]
        worker = info['worker']
        if hasattr(worker, 'resume'):
            worker.resume()
        info['last_time'] = time.time()
        info['speed'] = 0.0
        status_item = self._transfer_table.item(row, 3)
        if status_item:
            status_item.setText('传输中')
        name_item = self._transfer_table.item(row, 0)
        name = name_item.text() if name_item else f'任务{tid}'
        self._log(f'[SFTP] 已继续传输: {name}')

    def _transfer_pause_all(self):
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
                info['last_time'] = time.time()
                info['speed'] = 0.0
                status_item.setText('传输中')
                count += 1
        if count:
            self._log(f'[SFTP] 已继续全部传输 ({count} 个任务)')

    def _transfer_delete_row(self, row):
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
        for t, inf in self._transfer_workers.items():
            if inf['row'] > row:
                inf['row'] -= 1
        self._log(f'[SFTP] 已删除传输: {name}')

    def _transfer_delete_all(self):
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
        from core.app_paths import get_app_dir
        base = get_app_dir()
        temp_dir = os.path.join(base, '_sftp_temp')
        os.makedirs(temp_dir, exist_ok=True)
        return temp_dir

    def _ctx_local_open(self, data):
        path = data['path']
        try:
            os.startfile(path)
            self._log(f'[SFTP] 已打开: {path}')
        except OSError as e:
            self._log(f'[SFTP] 打开失败: {e}')

    def _ctx_remote_open(self, entry):
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
        try:
            os.startfile(path)
            self._log(f'[SFTP] 已打开: {path}')
        except OSError as e:
            self._log(f'[SFTP] 打开失败: {e}')

    def _ctx_rename_local(self, data):
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
        new_name, ok = QInputDialog.getText(self, '重命名', '新名称:', text=entry['name'])
        if not ok or not new_name or new_name == entry['name']:
            return
        old_path = self._remote_path.rstrip('/') + '/' + entry['name']
        new_path = self._remote_path.rstrip('/') + '/' + new_name
        self._log(f'[SFTP] 重命名: {old_path} -> {new_path}')
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
        if data['is_dir']:
            msg = f'确定要删除本地目录 "{data["name"]}" 及其所有内容吗？'
        else:
            msg = f'确定要删除本地文件 "{data["name"]}" 吗？'
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
        transport = self._transport
        self._transport = None
        self._cleanup_connect_worker()
        for tid in list(self._transfer_workers.keys()):
            info = self._transfer_workers.get(tid)
            if info and hasattr(info['worker'], 'stop'):
                info['worker'].stop()
            self._safe_delete_transfer_worker(tid)
        self._cleanup_list_worker()
        safe_close_transport(transport)
        if transport:
            self._log('[SFTP] 已断开连接')
        super().closeEvent(event)

