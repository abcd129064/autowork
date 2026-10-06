# -*- coding: utf-8 -*-
"""SFTP 文件管理窗口"""

import os
import time
import shutil
import subprocess
import threading

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
    QWidget, QTreeWidgetItem, QFrame, QLabel,
    QHeaderView, QSplitter,
    QTableWidgetItem, QAbstractItemView, QApplication, QPushButton)
from PySide6.QtCore import QObject, QTimer, Qt, QEvent, QThread, Signal
from PySide6.QtGui import QShortcut, QKeySequence, QFont
from qfluentwidgets import (PushButton, PrimaryPushButton, BodyLabel, CaptionLabel,
    LineEdit, SearchLineEdit, setFont, TreeWidget, TableWidget, ProgressBar,
    RoundMenu, Action, FluentIcon, MenuAnimationType,
    MessageBox, MessageBoxBase)

from core.conn_logger import conn_logger
from core.perf import is_animation_enabled
from core.theme_qss import apply_window_qss
from core import transfer_queue as _tq
from core.utils import safe_close_transport
from workers.network_workers import (
    SFTPConnectWorker, SFTPListWorker, SFTPOperationWorker, SFTPDirTransferWorker,
)

# 模块级强引用集合：防止窗口关闭后 Python GC 回收仍在运行的 QThread 导致崩溃
_pending_workers: set = set()


class _GlobalSignals(QObject):
    """跨面板全局信号载体（下载完成联动主窗口视频列表）"""
    # (设备目录名, 下载文件绝对路径, 本批文件数)
    file_downloaded = Signal(str, str, int)


GLOBAL_SIGNALS = _GlobalSignals()


def _videos_top_dir(local_path):
    """返回下载落点在 videos 目录下的第一级子目录名；不在 videos 下返回空字符串"""
    try:
        from core.app_paths import get_app_dir
        videos_dir = os.path.join(get_app_dir(), 'videos')
        norm = os.path.normpath(local_path)
        vbase = os.path.normpath(videos_dir)
        # 用 commonpath 判定包含关系而不是 startswith，因为 "videos2" 这类同级目录前缀也能通过 startswith 造成误判
        if os.path.commonpath([norm, vbase]) != vbase:
            return ''
        rel = os.path.relpath(norm, vbase)
        parts = [p for p in rel.split(os.sep) if p]
        return parts[0] if len(parts) > 1 else ''
    except Exception:
        return ''


def _cleanup_sftp_temp():
    """清理 _sftp_temp 临时目录中 mtime 超过 7 天的文件，异常静默"""
    # 清理是顺带的卫生任务，内外两层 try 都吞异常：单个文件删不掉不能中断后续清理，更不能阻断窗口启动
    try:
        from core.app_paths import get_app_dir
        temp_dir = os.path.join(get_app_dir(), '_sftp_temp')
        if not os.path.isdir(temp_dir):
            return
        cutoff = time.time() - 7 * 24 * 3600
        for name in os.listdir(temp_dir):
            p = os.path.join(temp_dir, name)
            try:
                if os.path.getmtime(p) < cutoff:
                    if os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        os.remove(p)
            except Exception:
                pass
    except Exception:
        pass


def _popup_ani_type():
    """按主界面「性能选项-动画效果」开关决定右键菜单弹出动画类型。

    运行时即时读取 core.perf 全局状态：主窗口切换开关后，已打开的
    远程面板中菜单下一次弹出即同步生效，新打开的会话同样读取当前值。"""
    return (MenuAnimationType.DROP_DOWN if is_animation_enabled()
            else MenuAnimationType.NONE)


# P1-9：快速操作（删除/新建/重命名）入传输队列后的行标签
_QUICK_OP_LABELS = {
    'delete': '删除', 'rmdir': '删除目录', 'mkdir': '新建目录',
    'rename': '重命名', 'create_file': '新建文件',
}


class _SortableTreeItem(QTreeWidgetItem):
    """可排序树节点（表头点击排序）：

    - 目录恒排在文件前（与排序列无关，符合资源管理器习惯）
    - 大小列（第 1 列）按 UserRole 中的数值比较，避免 "9.5 MB" 与 "10 MB" 文本序错乱
    - 文件名列忽略大小写；修改时间列文本即 "YYYY-MM-DD HH:MM"，字典序等同时间序"""
    def __lt__(self, other):
        tree = self.treeWidget()
        col = tree.sortColumn() if tree is not None else 0
        if tree is not None:
            _hdr = tree.header()
            ascending = (_hdr is None or
                         _hdr.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder)
        else:
            ascending = True
        sd = self.data(0, Qt.ItemDataRole.UserRole)
        od = other.data(0, Qt.ItemDataRole.UserRole)
        if sd and od and sd.get('is_dir') != od.get('is_dir'):
            # 目录恒排在文件前，与排序方向无关：Qt 降序是反转参数调用 __lt__，
            # 此处按当前方向显式修正，避免降序时目录掉到列表末尾
            return sd['is_dir'] if ascending else not sd['is_dir']
        if col == 1 and sd and od:
            a = sd.get('size', 0)
            b = od.get('size', 0)
            if a != b:
                return a < b
            # 大小相同回退按文件名（忽略大小写），避免比较格式化文本（恒相等）
            a = self.text(0).lower()
            b = other.text(0).lower()
            if a != b:
                return a < b
        if col == 0:
            a = self.text(0).lower()
            b = other.text(0).lower()
            if a != b:
                return a < b
        return self.text(col) < other.text(col)

# ------------------------------------------------------------------ 文件类型图标映射
_ICON_CACHE: dict = {}

# 扩展名 → FluentIcon 映射
_EXT_ICON_MAP = {
    # 图片
    '.jpg': FluentIcon.PHOTO, '.jpeg': FluentIcon.PHOTO, '.png': FluentIcon.PHOTO,
    '.gif': FluentIcon.PHOTO, '.bmp': FluentIcon.PHOTO, '.svg': FluentIcon.PHOTO,
    '.webp': FluentIcon.PHOTO, '.ico': FluentIcon.PHOTO, '.tiff': FluentIcon.PHOTO,
    # 视频
    '.mp4': FluentIcon.VIDEO, '.avi': FluentIcon.VIDEO, '.mkv': FluentIcon.VIDEO,
    '.mov': FluentIcon.VIDEO, '.wmv': FluentIcon.VIDEO, '.flv': FluentIcon.VIDEO,
    '.webm': FluentIcon.VIDEO, '.m4v': FluentIcon.VIDEO, '.mpg': FluentIcon.VIDEO,
    # 音频
    '.mp3': FluentIcon.MUSIC, '.wav': FluentIcon.MUSIC, '.flac': FluentIcon.MUSIC,
    '.ogg': FluentIcon.MUSIC, '.aac': FluentIcon.MUSIC, '.wma': FluentIcon.MUSIC,
    '.m4a': FluentIcon.MUSIC,
    # 压缩包
    '.zip': FluentIcon.ZIP_FOLDER, '.tar': FluentIcon.ZIP_FOLDER,
    '.gz': FluentIcon.ZIP_FOLDER, '.rar': FluentIcon.ZIP_FOLDER,
    '.7z': FluentIcon.ZIP_FOLDER, '.bz2': FluentIcon.ZIP_FOLDER,
    '.xz': FluentIcon.ZIP_FOLDER, '.tgz': FluentIcon.ZIP_FOLDER,
    # 代码
    '.py': FluentIcon.CODE, '.js': FluentIcon.CODE, '.ts': FluentIcon.CODE,
    '.java': FluentIcon.CODE, '.c': FluentIcon.CODE, '.cpp': FluentIcon.CODE,
    '.h': FluentIcon.CODE, '.go': FluentIcon.CODE, '.rs': FluentIcon.CODE,
    '.sh': FluentIcon.CODE, '.bat': FluentIcon.CODE, '.ps1': FluentIcon.CODE,
    '.rb': FluentIcon.CODE, '.php': FluentIcon.CODE, '.swift': FluentIcon.CODE,
    '.kt': FluentIcon.CODE, '.cs': FluentIcon.CODE, '.lua': FluentIcon.CODE,
    '.html': FluentIcon.CODE, '.css': FluentIcon.CODE, '.vue': FluentIcon.CODE,
    # 文档
    '.doc': FluentIcon.DOCUMENT, '.docx': FluentIcon.DOCUMENT,
    '.txt': FluentIcon.DOCUMENT, '.rtf': FluentIcon.DOCUMENT,
    '.pdf': FluentIcon.DOCUMENT, '.odt': FluentIcon.DOCUMENT,
    '.md': FluentIcon.DOCUMENT, '.log': FluentIcon.DOCUMENT,
    # 表格
    '.xls': FluentIcon.PIE_SINGLE, '.xlsx': FluentIcon.PIE_SINGLE,
    '.csv': FluentIcon.PIE_SINGLE, '.ods': FluentIcon.PIE_SINGLE,
    # 配置
    '.json': FluentIcon.SETTING, '.xml': FluentIcon.SETTING,
    '.yaml': FluentIcon.SETTING, '.yml': FluentIcon.SETTING,
    '.toml': FluentIcon.SETTING, '.ini': FluentIcon.SETTING,
    '.conf': FluentIcon.SETTING, '.cfg': FluentIcon.SETTING,
    '.env': FluentIcon.SETTING,
    # 可执行 / 应用
    '.exe': FluentIcon.APPLICATION, '.msi': FluentIcon.APPLICATION,
    '.app': FluentIcon.APPLICATION, '.bin': FluentIcon.APPLICATION,
    '.run': FluentIcon.APPLICATION, '.deb': FluentIcon.APPLICATION,
    '.rpm': FluentIcon.APPLICATION, '.apk': FluentIcon.APPLICATION,
    # 字体
    '.ttf': FluentIcon.FONT, '.otf': FluentIcon.FONT,
    '.woff': FluentIcon.FONT, '.woff2': FluentIcon.FONT,
    # 数据库
    '.db': FluentIcon.LIBRARY, '.sqlite': FluentIcon.LIBRARY,
    '.sql': FluentIcon.LIBRARY, '.mdb': FluentIcon.LIBRARY,
}


def _file_icon(name: str, is_dir: bool):
    """根据文件名/扩展名返回对应的 FluentIcon（带缓存）

    注意：必须用 qicon()（FluentIconEngine）而非 icon()：
    icon() 会把"构建时主题"的 SVG 固化为静态 QIcon 并被模块级缓存，
    切换到深色模式后仍显示黑色图标看不清；qicon() 每次绘制时按
    qconfig 当前主题动态取黑/白 SVG，缓存也随主题自动适配。
    """
    if is_dir:
        fi = FluentIcon.FOLDER
    else:
        ext = os.path.splitext(name)[1].lower()
        fi = _EXT_ICON_MAP.get(ext, FluentIcon.DOCUMENT)
    # 缓存 QIcon 实例避免重复创建（engine 随主题动态渲染，缓存安全）
    if fi not in _ICON_CACHE:
        _ICON_CACHE[fi] = fi.qicon()
    return _ICON_CACHE[fi]


class _HealthCheckWorker(QThread):
    """后台 SFTP 连接健康检测"""
    result = Signal(int)  # latency_ms, -1 表示异常

    def __init__(self, transport):
        super().__init__()
        self.transport = transport

    def run(self):
        """在共享 transport 上开 session 测延迟，异常发 -1"""
        try:
            import time as _time
            start = _time.time()
            # 绝不能复用业务侧的 SFTP 通道：SFTP 通道是严格单线程一问一答，
            # 两个线程同时发请求会导致响应错配对不上，互相把对方踢乱
            session = self.transport.open_session()
            session.close()
            latency_ms = int((_time.time() - start) * 1000)
            self.result.emit(latency_ms)
        except Exception:
            self.result.emit(-1)


def _safe_release_worker(w):
    """将 worker 放入 pending 集合，线程结束后自动移除并 deleteLater"""
    _pending_workers.add(w)
    # lambda 闭包捕获 w 是为了持强引用：PySide6 连接不保留 Python worker 引用，
    # 否则线程还在跑时 w 被 GC 回收，触发 "Destroyed while thread is still running" qFatal
    w.finished.connect(lambda: (_pending_workers.discard(w), w.deleteLater()))


class _TransferTable(TableWidget):
    """传输队列专用表格：纯鼠标事件实现拖拽调序，
    完全不使用 Qt 内置 drag-drop（避免 InternalMove 在模型层删除行）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._panel = None  # 由 SFTPPanel 设置反向引用
        self._drag_row = -1
        self._press_pos = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            row = self.rowAt(event.position().toPoint().y())
            if row >= 0:
                self._drag_row = row
                self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # 检测到拖拽意图：切换光标提示可拖拽
        if self._press_pos is not None and self._drag_row >= 0:
            if (event.position().toPoint() - self._press_pos).manhattanLength() > 10:
                self.viewport().setCursor(Qt.CursorShape.SizeVerCursor)
                return  # 不调用 super，阻止 Qt 启动内置 drag
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_row >= 0:
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            target_row = self.rowAt(event.position().toPoint().y())
            if target_row < 0:
                target_row = self.rowCount() - 1
            if target_row >= 0 and target_row != self._drag_row and self._panel:
                self._panel._move_transfer_row(self._drag_row, target_row)
            self._drag_row = -1
            self._press_pos = None
        super().mouseReleaseEvent(event)


class _TextInputDialog(MessageBoxBase):
    """Fluent 风格文本输入对话框，替代 QInputDialog.getText"""

    def __init__(self, title, label, default='', parent=None):
        super().__init__(parent)
        # qfluentwidgets 1.11+ 的 MessageBoxBase 不再提供 titleLabel/view，
        # 标题与输入控件需自行创建并加入 viewLayout
        self.titleLabel = BodyLabel(title, self.widget)
        self.fieldLabel = CaptionLabel(label, self.widget)
        self.edit = LineEdit(self.widget)
        self.edit.setText(default)
        if default:
            self.edit.selectAll()
        self.edit.setMinimumWidth(280)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.fieldLabel)
        self.viewLayout.addWidget(self.edit)
        self.widget.setMinimumWidth(380)


class SFTPPanel(QWidget):
    """SFTP 文件管理面板（可嵌入标签页容器，也可独立使用）

    资源清理统一由 shutdown() 方法负责，容器关闭标签时调用。
    """

    # P1-1：请求在当前远程目录打开终端标签（参数=远程目录路径），
    # 由创建方（remote_mixin / frp_remote）连接后用同目标参数建 SSHTerminalPanel
    open_terminal_here = Signal(str)

    def __init__(self, host, port, username, password, server_name='', log_callback=None, default_remote_path=None, default_local_path=None, parent=None):
        super().__init__(parent)
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._server_name = server_name
        self._conn_params = (host, port, username, password)
        self._transport = None
        self._remote_path = default_remote_path or '/home'
        self._remote_entries = []
        _desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        self._local_path = _desktop if os.path.isdir(_desktop) else os.path.expanduser('~')
        # 指定本地初始目录（如 snk 会话按球桌号自动建文件夹）：不存在则尝试创建，失败回退桌面
        if default_local_path:
            try:
                os.makedirs(default_local_path, exist_ok=True)
                if os.path.isdir(default_local_path):
                    self._local_path = default_local_path
            except OSError:
                pass
        self._local_entries = []
        self._log = log_callback or (lambda msg: None)
        self._connect_worker = None
        self._list_worker = None
        self._list_generation = 0
        self._listing = False
        self._pending_remote_path = None
        self._transfer_workers = {}
        self._next_transfer_id = 0
        # P2-1：批量传输并发闸门——运行中（含暂停）任务数达上限后新任务排队
        # （状态列"排队中"），有空位按 FIFO 启动；避免多选 N 个文件 = N 条并发
        # SSH 连接（服务端 MaxStartups 拒连 → 部分文件莫名失败 + auth 日志被刷）
        self._max_concurrent_transfers = self._load_max_concurrent_transfers()
        self._queued_transfers = []  # 排队 tid 的 FIFO
        self._drag_source_row = -1
        self._closing = False
        self._health_worker = None
        # P1-10：传输完成后的按需刷新（400ms 去抖合并，只在任务目标目录
        # == 当前浏览目录时刷新，不再打断用户正在翻的目录/搜索）
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(400)
        self._refresh_timer.timeout.connect(self._flush_pending_refresh)
        self._pending_refresh = set()  # {'remote', 'local'}
        # P2-2 步骤 1：未完成传输队列落盘（800ms 去抖合并，状态变化时才写盘）
        self._pending_save_timer = QTimer(self)
        self._pending_save_timer.setSingleShot(True)
        self._pending_save_timer.setInterval(800)
        self._pending_save_timer.timeout.connect(self._flush_pending_queue)
        self._pending_restore_offered = False
        self._init_ui()
        # P2-2（2026-09-24）：清理挪后台线程——_sftp_temp 积压大量文件时
        # listdir+rmtree 会拖慢窗口构建；清理逻辑只碰文件系统，线程安全。
        # 右键「打开」路径（_ctx_remote_open）仍同步清理：需先清完再落下载文件，
        # 后台跑会与下载写同一目录竞态
        threading.Thread(target=_cleanup_sftp_temp, name="sftp-temp-cleanup",
                         daemon=True).start()
        QTimer.singleShot(100, self._connect_and_list)

    @property
    def tab_title(self) -> str:
        """返回适合标签页显示的标题（P0-4：始终带 host:port，同目标多标签可区分）"""
        if self._server_name:
            return f"SFTP - {self._server_name}（{self._host}:{self._port}）"
        return f"SFTP - {self._host}:{self._port}"

    @property
    def is_connected(self) -> bool:
        """P2-6：只读连通探针（会话恢复后延迟体检用；连接中/断开均为 False）"""
        try:
            return bool(self._transport is not None and self._transport.is_active())
        except Exception:
            return False

    def busy_state(self):
        """(busy, 描述)——P0-1 统一契约：有运行中/暂停中/排队中的传输任务即 busy

        供标签容器（关闭前确认）与 core.frp_remote.is_busy_on_port（断隧道前
        确认）查询；返回元组而非布尔，让确认弹窗能告诉用户具体在忙什么。
        P2-1 起排队任务也计入（关标签同样会丢掉它们）。
        """
        running = 0
        for info in self._transfer_workers.values():
            if not isinstance(info, dict):
                continue
            state = info.get('state')
            worker = info.get('worker')
            if state in ('running', 'paused', 'queued') or (
                    state is None and worker is not None and worker.isRunning()):
                running += 1
        if running:
            return True, f'{running} 个传输任务进行中'
        return False, ''

    @staticmethod
    def _load_max_concurrent_transfers() -> int:
        """读设置 sftp_max_concurrent_transfers（默认 3；非法/越界回退默认）"""
        try:
            from core import app_settings
            val = int(app_settings.get("sftp_max_concurrent_transfers", 3))
        except Exception:
            val = 3
        return val if val >= 1 else 3

    def _active_transfer_count(self) -> int:
        """占用并发槽位的任务数（运行中+暂停中：暂停仍持有线程与连接）"""
        return sum(1 for info in self._transfer_workers.values()
                   if isinstance(info, dict)
                   and info.get('state') in ('running', 'paused'))

    def _launch_or_queue_transfer(self, tid):
        """P2-1：有空位立即启动（状态列"传输中"），否则排队（"排队中"）"""
        info = self._transfer_workers.get(tid)
        if info is None:
            return
        status_item = self._transfer_table.item(info.get('row', -1), 3)
        if self._active_transfer_count() >= self._max_concurrent_transfers:
            info['state'] = 'queued'
            if tid not in self._queued_transfers:
                self._queued_transfers.append(tid)
            if status_item:
                status_item.setText('排队中')
            # P2-2：排队态也要落盘（否则退出即丢）
            self._schedule_pending_save()
            return
        info['state'] = 'running'
        info['start_time'] = time.time()
        info['last_time'] = time.time()
        if status_item:
            status_item.setText('传输中')
        info['worker'].start()
        self._schedule_pending_save()

    def _pump_transfer_queue(self):
        """P2-1：任务结束/删除后排空队列（FIFO 启动直到占满并发位）"""
        while (self._queued_transfers
               and self._active_transfer_count() < self._max_concurrent_transfers):
            tid = self._queued_transfers.pop(0)
            info = self._transfer_workers.get(tid)
            if info is None or info.get('state') != 'queued':
                continue  # 行已被删除或已在别处启动
            self._launch_or_queue_transfer(tid)

    # ------------------------------------------------- 未完成传输队列落盘（P2-2 步骤 1）
    def _pending_records(self):
        """内存中未完成（排队中/传输中/已暂停）的任务 → 可落盘记录列表

        只取 params 快照里的操作类型与两端路径：不落盘密码（连接凭据另走
        core.credentials 解析链），也不落盘进度偏移（步骤 3 真断点续传才做）。
        """
        records = []
        for info in self._transfer_workers.values():
            if not isinstance(info, dict) or info.get('state') not in _tq.PENDING_STATES:
                continue
            params = info.get('params')
            if not params or len(params) < 4:
                continue
            _conn, op, local_path, remote_path = params[0], params[1], params[2], params[3]
            if op not in _tq.VALID_OPS or not local_path or not remote_path:
                continue
            name_item = self._transfer_table.item(info.get('row', -1), 0)
            name = name_item.text() if name_item else ''
            size = 0
            if op == 'upload':
                try:
                    size = os.path.getsize(local_path)
                except OSError:
                    size = 0
            records.append({
                'op': op, 'name': name, 'local_path': local_path,
                'remote_path': remote_path, 'size': size,
            })
        return records

    def _schedule_pending_save(self):
        """状态变化后延迟合并写盘（进度回调频繁，不能每次都写设置文件）"""
        timer = getattr(self, '_pending_save_timer', None)
        if timer is not None and not self._closing:
            timer.start()

    @staticmethod
    def _load_pending_store() -> dict:
        """读设置里的未完成队列整表（损坏/缺失返回 {}）"""
        try:
            from core import app_settings
            store = app_settings.get(_tq.QUEUE_KEY, {})
        except Exception:
            return {}
        return store if isinstance(store, dict) else {}

    @staticmethod
    def _write_pending_store(store: dict) -> bool:
        try:
            from core import app_settings
            app_settings.set(_tq.QUEUE_KEY, store)
            return True
        except Exception:
            return False

    def _flush_pending_queue(self):
        """立即把未完成任务快照写进设置文件（去抖超时 / shutdown 调用）"""
        store, dropped = _tq.prune_store(self._load_pending_store(), max_targets=50)
        if dropped:
            self._log(f'[SFTP] 未完成队列目标过多，已丢弃 {dropped} 个最早的目标')
        new_store = _tq.save_target(store, self._host, self._port, self._pending_records())
        if not self._write_pending_store(new_store):
            self._log('[SFTP] 保存未完成传输队列失败（设置写入异常）')

    def _clear_pending_queue(self):
        """清除本目标的未完成快照（恢复提示只提示一次，避免反复打扰）"""
        store = _tq.clear_target(self._load_pending_store(), self._host, self._port)
        self._write_pending_store(store)

    def _restore_pending_queue(self, records) -> int:
        """把落盘记录重新入队（统一走 _start_transfer_op，仍受并发闸门约束）"""
        started = 0
        for rec in records:
            op = rec.get('op')
            local_path = rec.get('local_path') or ''
            remote_path = rec.get('remote_path') or ''
            name = rec.get('name') or os.path.basename(local_path or remote_path)
            if op in ('upload', 'upload_dir') and not os.path.exists(local_path):
                self._log(f'[SFTP] 跳过已不存在的本地路径: {local_path}')
                continue
            if op in ('upload', 'upload_dir'):
                if op == 'upload_dir':
                    worker = SFTPDirTransferWorker(
                        self._conn_params, op, local_dir=local_path, remote_dir=remote_path,
                        dir_name=os.path.basename(local_path.rstrip('/\\')))
                    size, label = 0, '上传'
                else:
                    size = rec.get('size', 0) or 0
                    worker = SFTPOperationWorker(
                        self._conn_params, op, local_path, remote_path, file_size=size)
                    label = '上传'
            else:
                size = rec.get('size', 0) or 0
                if op == 'download_dir':
                    worker = SFTPDirTransferWorker(
                        self._conn_params, op, local_dir=local_path, remote_dir=remote_path,
                        dir_name=os.path.basename(remote_path.rstrip('/')))
                    size, label = 0, '下载'
                else:
                    worker = SFTPOperationWorker(
                        self._conn_params, op, local_path, remote_path, file_size=size)
                    label = '下载'
            self._start_transfer_op(worker, name, label, size,
                                    op=op, local_path=local_path, remote_path=remote_path)
            started += 1
        return started

    def _offer_pending_restore(self):
        """连接成功后：若本目标有上次未完成的传输，询问是否恢复（P2-2 步骤 1）

        无论选"恢复"还是"忽略"都会消费掉快照：同一个提示不会每次重连都弹。
        """
        if getattr(self, '_pending_restore_offered', False):
            return
        self._pending_restore_offered = True
        records = _tq.load_target(self._load_pending_store(), self._host, self._port)
        if not records:
            return
        self._clear_pending_queue()
        if self._closing:
            return
        summary = _tq.describe_pending(records)
        msg = (f'{summary}。\n\n'
               '"恢复"按原路径重新加入传输队列（已传部分从头重传）；\n'
               '"忽略"则丢弃这份记录。')
        dlg = MessageBox('恢复未完成的传输', msg, self)
        dlg.yesButton.setText('恢复')
        dlg.cancelButton.setText('忽略')
        try:
            accepted = bool(dlg.exec())
        except Exception:
            accepted = False
        if not accepted:
            self._log(f'[SFTP] 已忽略上次未完成的传输（{summary}）')
            return
        started = self._restore_pending_queue(records)
        self._log(f'[SFTP] 已恢复 {started} 个未完成的传输任务（从头传输）')

    # ------------------------------------------------------------------ UI 构建
    def _init_ui(self):
        """搭建双栏文件面板 + 传输队列 + 按钮栏，预构建右键菜单"""
        root = QVBoxLayout(self)
        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        self._build_local_panel()
        self._build_remote_panel()

        self._splitter.addWidget(self._left_panel)
        self._splitter.addWidget(self._right_panel)
        self._splitter.setStretchFactor(0, 1)  # 【左右比例】本地面板拉伸因子
        self._splitter.setStretchFactor(1, 1)  # 【左右比例】远程面板拉伸因子（1:1 等分）

        self._build_transfer_queue()
        # P1-3：断线重连条（置顶，默认隐藏；连接失败/断开时显示）
        self._build_reconnect_bar(root)
        self._build_button_bar(root)
        self._bind_shortcuts()

        # 预构建右键菜单（Action/图标/信号仅创建一次，后续右键零开销弹出）
        self._build_context_menus()

    def _build_reconnect_bar(self, root):
        """断线重连条（与 SSH 终端同构样式，默认隐藏）"""
        self._reconnect_bar = QFrame(self)
        self._reconnect_bar.setStyleSheet(
            "QFrame { background-color: rgba(255, 152, 0, 0.15);"
            " border: 1px solid #ff9800; border-radius: 4px; }"
        )
        bar_layout = QHBoxLayout(self._reconnect_bar)
        bar_layout.setContentsMargins(8, 2, 8, 2)
        bar_layout.setSpacing(6)
        self._reconnect_label = QLabel('连接已断开')
        bar_layout.addWidget(self._reconnect_label)
        bar_layout.addStretch()
        self._reconnect_btn = PrimaryPushButton('重新连接')
        self._reconnect_btn.setFocusPolicy(Qt.NoFocus)
        self._reconnect_btn.setFixedWidth(96)
        self._reconnect_btn.clicked.connect(self._reconnect_clicked)
        bar_layout.addWidget(self._reconnect_btn)
        self._reconnect_bar.hide()
        root.addWidget(self._reconnect_bar)
    
    def _build_local_panel(self):
        """构建本地面板（树控件、路径栏、搜索框）"""
        self._left_panel = QWidget()
        left_lay = QVBoxLayout(self._left_panel)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_bar = QHBoxLayout()
        self._btn_local_up = PushButton('.. 上级')
        self._btn_local_up.clicked.connect(self._local_go_up)
        left_bar.addWidget(self._btn_local_up)
        left_bar.addWidget(CaptionLabel('本地:'))
        self._edit_local_path = LineEdit()
        self._edit_local_path.setText(self._local_path)
        setFont(self._edit_local_path, weight=QFont.Weight.Bold)
        self._edit_local_path.returnPressed.connect(self._on_local_path_entered)
        left_bar.addWidget(self._edit_local_path, 1)
        self._btn_local_refresh = PushButton('刷新')
        self._btn_local_refresh.clicked.connect(self._local_refresh)
        left_bar.addWidget(self._btn_local_refresh)
        left_lay.addLayout(left_bar)
    
        self._local_tree = TreeWidget()
        self._local_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._local_tree.setHeaderLabels(['文件名', '大小', '类型', '修改时间'])
        self._local_tree.setColumnCount(4)
        lh = self._local_tree.header()
        lh.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        for c in [1, 2, 3]:
            lh.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        # 【列宽】本地文件列表各列初始宽度（px）
        lh.resizeSection(0, 220)   # 文件名
        lh.resizeSection(1, 80)    # 大小
        lh.resizeSection(2, 60)    # 类型
        lh.resizeSection(3, 100)   # 修改时间
        # 表头点击排序：默认按文件名升序（目录在前），点击表头可切换列/方向
        lh.setSortIndicatorShown(True)
        lh.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        self._local_tree.setSortingEnabled(True)
        self._local_tree.itemDoubleClicked.connect(self._on_local_item_double_clicked)
        self._local_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._local_tree.customContextMenuRequested.connect(self._on_local_context_menu)
        left_lay.addWidget(self._local_tree)
    
        # 本地底部搜索框（SearchLineEdit 自带搜索图标+清空按钮）
        self._local_search_frame = QWidget()
        local_sf = QHBoxLayout(self._local_search_frame)
        local_sf.setContentsMargins(0, 2, 0, 0)
        self._local_search_edit = SearchLineEdit()
        self._local_search_edit.setPlaceholderText('搜索本地文件...')
        self._local_search_edit.textChanged.connect(self._on_local_search)
        local_sf.addWidget(self._local_search_edit, 1)
        left_lay.addWidget(self._local_search_frame)
        self._local_search_frame.hide()
    
    def _build_remote_panel(self):
        """构建远程面板（树控件、路径栏、搜索框）"""
        self._right_panel = QWidget()
        right_lay = QVBoxLayout(self._right_panel)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_bar = QHBoxLayout()
        self._btn_up = PushButton('.. 上级')
        self._btn_up.clicked.connect(self._go_up)
        right_bar.addWidget(self._btn_up)
        right_bar.addWidget(CaptionLabel('远程:'))
        self._edit_remote_path = LineEdit()
        self._edit_remote_path.setText(self._remote_path)
        setFont(self._edit_remote_path, weight=QFont.Weight.Bold)
        self._edit_remote_path.returnPressed.connect(self._on_remote_path_entered)
        right_bar.addWidget(self._edit_remote_path, 1)
        self._btn_refresh = PushButton('刷新')
        self._btn_refresh.clicked.connect(self._refresh)
        right_bar.addWidget(self._btn_refresh)
        right_lay.addLayout(right_bar)
    
        self._tree = TreeWidget()
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.setHeaderLabels(['文件名', '大小', '类型', '权限', '修改时间'])
        self._tree.setColumnCount(5)
        rh = self._tree.header()
        rh.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        for c in [1, 2, 3, 4]:
            rh.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        # 【列宽】远程文件列表各列初始宽度（px）
        rh.resizeSection(0, 220)   # 文件名
        rh.resizeSection(1, 80)    # 大小
        rh.resizeSection(2, 60)    # 类型
        rh.resizeSection(3, 80)    # 权限
        rh.resizeSection(4, 130)   # 修改时间
        # 表头点击排序：默认按文件名升序（目录在前），点击表头可切换列/方向
        rh.setSortIndicatorShown(True)
        rh.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        self._tree.setSortingEnabled(True)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_remote_context_menu)
        # 拖拽上传：接受从资源管理器拖入的文件/目录，自动上传到当前远程目录
        self._tree.setAcceptDrops(True)
        self._tree.installEventFilter(self)
        right_lay.addWidget(self._tree)
    
        # 远程底部搜索框（SearchLineEdit 自带搜索图标+清空按钮）
        self._remote_search_frame = QWidget()
        remote_sf = QHBoxLayout(self._remote_search_frame)
        remote_sf.setContentsMargins(0, 2, 0, 0)
        self._remote_search_edit = SearchLineEdit()
        self._remote_search_edit.setPlaceholderText('搜索远程文件...')
        self._remote_search_edit.textChanged.connect(self._on_remote_search)
        remote_sf.addWidget(self._remote_search_edit, 1)
        right_lay.addWidget(self._remote_search_frame)
        self._remote_search_frame.hide()
    
    def _build_transfer_queue(self):
        """构建传输队列表格 + 垂直 Splitter"""
        self._transfer_table = _TransferTable()
        self._transfer_table._panel = self
        self._transfer_table.setColumnCount(4)
        self._transfer_table.setRowCount(0)
        self._transfer_table.setHorizontalHeaderLabels(['文件名', '进度', '速度', '状态'])
        hdr = self._transfer_table.horizontalHeader()
        for c in range(3):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        # 【列宽】传输队列各列初始宽度（px）
        hdr.resizeSection(0, 280)   # 文件名
        hdr.resizeSection(1, 180)   # 进度
        hdr.resizeSection(2, 100)   # 速度
        self._transfer_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._transfer_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._transfer_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._transfer_table.verticalHeader().setDefaultSectionSize(24)  # 【行高】每行 24px
        self._transfer_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._transfer_table.setMinimumHeight(80)  # 【面板高度】传输队列最小 80px，可拖拽调节
        self._transfer_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._transfer_table.customContextMenuRequested.connect(self._on_transfer_context_menu)
        # 拖拽调序：纯鼠标事件实现（_TransferTable 子类），禁用 Qt 内置 drag-drop
        self._transfer_table.setDragEnabled(False)
        self._transfer_table.setAcceptDrops(False)
        self._transfer_table.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        # P1-5：拖文件到队列表 = 上传到当前远程目录（顺序在 NoDragDrop 之后，
        # 显式重开 acceptDrops，事件经 eventFilter 拦截处理，不走内置 drop 逻辑）
        self._transfer_table.setAcceptDrops(True)
        self._transfer_table.installEventFilter(self)
        self._transfer_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    
        # 垂直 Splitter：文件区域（上） + 传输队列（下），支持上下拖拽调节
        self._v_splitter = QSplitter(Qt.Orientation.Vertical)
        self._v_splitter.addWidget(self._splitter)
        self._v_splitter.addWidget(self._transfer_table)
        self._v_splitter.setStretchFactor(0, 4)  # 文件区域占大部分
        self._v_splitter.setStretchFactor(1, 1)  # 传输队列占小部分
        self._v_splitter.setSizes([400, 130])    # 初始比例
        # 注意：root.addWidget 由 _init_ui 在 _build_button_bar 之后统一处理
    
    def _build_button_bar(self, root):
        """构建操作按钮栏 + 健康检测定时器，挂到 root 布局"""
        # 先把 v_splitter 挂到 root
        root.addWidget(self._v_splitter, 1)
    
        # ---- 操作按钮栏
        btn_row = QHBoxLayout()
        self._btn_upload = PushButton('上传 ▶')
        self._btn_upload.clicked.connect(self._upload_file)
        btn_row.addWidget(self._btn_upload)
        self._btn_download = PushButton('◀ 下载')
        self._btn_download.clicked.connect(self._download_file)
        btn_row.addWidget(self._btn_download)
        self._btn_delete = PushButton('删除')
        self._btn_delete.clicked.connect(self._delete_selected)
        btn_row.addWidget(self._btn_delete)
        self._btn_mkdir = PushButton('新建目录')
        self._btn_mkdir.clicked.connect(self._create_directory)
        btn_row.addWidget(self._btn_mkdir)
        # P1-1：在当前远程目录打开终端标签（单向跳转，复用同目标连接参数）
        self._btn_terminal = PushButton('终端')
        self._btn_terminal.setToolTip('在当前远程目录打开 SSH 终端标签页')
        self._btn_terminal.clicked.connect(self._open_terminal_here)
        btn_row.addWidget(self._btn_terminal)
        self._btn_xftp = PushButton('Xftp')
        self._btn_xftp.clicked.connect(self._open_in_xftp)
        btn_row.addWidget(self._btn_xftp)
        btn_row.addStretch()
        # P0-2：删除按钮文案随远程侧选中数变化（与右键"传输（上传N 项）"同口径）
        self._tree.itemSelectionChanged.connect(self._update_delete_btn_text)
        # 连接健康指示器（状态圆点 + 延迟）
        self._lbl_health = BodyLabel('●')
        self._lbl_health.setStyleSheet('color: gray; font-size: 14px;')
        self._lbl_health.setToolTip('连接状态: 未连接')
        btn_row.addWidget(self._lbl_health)
        self._lbl_status = BodyLabel('就绪')
        btn_row.addWidget(self._lbl_status)
        root.addLayout(btn_row)
    
        # 连接健康检测定时器（每 5s 检测一次）
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(5000)
        self._health_timer.timeout.connect(self._check_connection_health)
        self._health_timer.start()
    
    def _bind_shortcuts(self):
        """绑定快捷键 + 修复默认按钮 + 预构建右键菜单"""
        # ---- Ctrl+F 快捷键
        sc = QShortcut(QKeySequence('Ctrl+F'), self)
        sc.activated.connect(self._on_search_shortcut)
        esc = QShortcut(QKeySequence('Escape'), self)
        esc.activated.connect(self._hide_search_boxes)
        # ---- P1-4：Ctrl+C = 复制选中项全路径（与右键"复制路径"同语义）
        # 焦点在路径框/搜索框（QLineEdit）时 Qt 会让输入框自身处理复制
        # （QLineEdit accept ShortcutOverride），不会落到这里，无键位冲突
        sc_copy = QShortcut(QKeySequence('Ctrl+C'), self)
        sc_copy.activated.connect(self._copy_selected_paths)

        # 修复误触发"上级"默认按钮，把本地目录推到上一级
        for _btn in self.findChildren(QPushButton):
            _btn.setAutoDefault(False)

    # ------------------------------------------------------------------ 连接
    def _connect_and_list(self):
        """发起 SFTP 连接（异步 worker），成功后列远程目录，同时先刷本地"""
        self._lbl_status.setText('正在连接...')
        self._list_local(self._local_path)
        worker = SFTPConnectWorker(self._host, self._port, self._username, self._password)
        worker.connected.connect(self._on_sftp_connect_success)
        worker.error.connect(self._on_sftp_connect_error)
        self._connect_worker = worker
        worker.start()

    def _on_sftp_connect_success(self, transport):
        """连接成功：保存 transport 并首次列远程目录"""
        self._transport = transport
        self._log(f'[SFTP] 已连接到 {self._host}:{self._port}')
        self._lbl_status.setText('已连接')
        self._reconnect_bar.hide()
        self._list_remote(self._remote_path)
        # P1-3：断线重连成功后自动重试此前失败的任务（批量）
        self._retry_failed_transfers()
        # P2-2 步骤 1：恢复上次未完成的传输（每目标只提示一次）
        self._offer_pending_restore()
        self._cleanup_connect_worker()

    def _on_sftp_connect_error(self, error):
        """连接失败：状态栏展示原因 + 重连条（P1-3：不再需要关标签重开）"""
        self._log(f'[SFTP] 连接失败: {error}')
        self._lbl_status.setText(f'连接失败: {error}')
        self._show_reconnect_bar(f'连接失败: {error}')
        self._cleanup_connect_worker()

    def _show_reconnect_bar(self, text: str = '连接已断开'):
        """显示断线重连条（P1-3，与 SSH 终端同构）"""
        self._reconnect_label.setText(text)
        self._reconnect_btn.setEnabled(True)
        self._reconnect_btn.setText('重新连接')
        self._reconnect_bar.show()

    def _reconnect_clicked(self):
        """一键重连：清理已失效的 transport，复用现有连接路径重建"""
        if self._closing:
            return
        self._reconnect_btn.setEnabled(False)
        self._reconnect_btn.setText('重连中...')
        self._lbl_status.setText('正在重新连接...')
        self._log(f'[SFTP] 正在重新连接 {self._host}:{self._port} ...')
        self._cleanup_dead_transport()
        self._connect_and_list()

    def _cleanup_dead_transport(self):
        """关闭已失效的 transport（重连前/列目录发现失效时清理）"""
        if self._transport is not None:
            try:
                safe_close_transport(self._transport)
            except Exception:
                pass
            self._transport = None

    def _retry_failed_transfers(self):
        """重连成功后批量重试队列表中状态为失败的任务（P1-3）"""
        retried = 0
        for row in range(self._transfer_table.rowCount()):
            status_item = self._transfer_table.item(row, 3)
            if status_item and status_item.text().startswith('失败'):
                self._transfer_retry_row(row)
                retried += 1
        if retried:
            self._log(f'[SFTP] 已自动重新开始 {retried} 个失败任务（均从头传输）')

    def _open_terminal_here(self):
        """P1-1：请求在同一目标的终端标签中打开当前远程目录"""
        path = self._remote_path or '/'
        self.open_terminal_here.emit(path)
        self._log(f'[SFTP] 请求在终端中打开目录: {path}')

    def _cleanup_connect_worker(self):
        """非阻塞释放连接 worker：运行中先 abort 再挂强引用防 GC 崩溃"""
        if self._connect_worker is not None:
            w = self._connect_worker
            self._connect_worker = None
            if hasattr(w, 'abort'):
                w.abort()
            if w.isRunning():
                _safe_release_worker(w)
            else:
                w.deleteLater()

    # ------------------------------------------------------------------ 连接健康检测
    def _check_connection_health(self):
        """定时检测连接状态，更新健康指示器（绿/黄/红圆点）"""
        if self._transport is None or not self._transport.is_active():
            self._set_health_indicator('red', '未连接')
            return

        # 取消前一个检测（如果还在运行）
        if self._health_worker is not None:
            if self._health_worker.isRunning():
                return  # 上一个还没完成，跳过本次

        worker = _HealthCheckWorker(self._transport)
        worker.result.connect(self._on_health_check_result)
        self._health_worker = worker
        worker.start()

    def _on_health_check_result(self, latency_ms):
        """健康检测结果回调"""
        if latency_ms < 0:
            self._set_health_indicator('red', '连接异常')
        elif latency_ms < 200:
            self._set_health_indicator('green', f'已连接 ({latency_ms}ms)')
        elif latency_ms < 1000:
            self._set_health_indicator('orange', f'延迟较高 ({latency_ms}ms)')
        else:
            self._set_health_indicator('red', f'延迟过高 ({latency_ms}ms)')
        self._health_worker = None

    def _set_health_indicator(self, color, tooltip):
        """设置健康指示器颜色和提示"""
        color_map = {'green': '#4CAF50', 'orange': '#FF9800', 'red': '#F44336', 'gray': 'gray'}
        self._lbl_health.setStyleSheet(f'color: {color_map.get(color, "gray")}; font-size: 14px;')
        self._lbl_health.setToolTip(f'连接状态: {tooltip}')

    # ------------------------------------------------------------------ Worker 管理
    def _cleanup_list_worker(self):
        """释放列目录 worker：先摘信号再按运行状态强引用/销毁，防旧响应回写"""
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
                _safe_release_worker(w)
                self._listing = False
            else:
                w.deleteLater()

    def _safe_delete_transfer_worker(self, tid):
        """从传输表移除指定任务：运行中挂强引用等自行结束，否则直接销毁"""
        info = self._transfer_workers.pop(tid, None)
        if info:
            w = info['worker']
            if w.isRunning():
                _safe_release_worker(w)
            else:
                w.deleteLater()

    # ------------------------------------------------------------------ 远程列目录
    def _list_remote(self, path):
        """列远程目录：transport 失效直接置断连态（P1-3：弹重连条）；串行互斥（进行中记待办）"""
        if self._transport is None or not self._transport.is_active():
            if self._transport is not None:
                self._lbl_status.setText('连接已断开')
                self._log('[SFTP] Transport 已失效')
                self._cleanup_dead_transport()
                self._show_reconnect_bar('连接已断开')
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
        # 把代际号挂到 worker 上，回调时靠 sender() 认出是哪一批，
        # 和当前代际对不上就丢弃，防快速连续点目录时旧结果覆盖新目录内容
        worker._list_gen = gen
        self._list_worker = worker
        worker.start()

    def _on_list_worker_finished(self):
        """列目录线程结束后清理 worker 引用（未结束则等 finished 信号）"""
        if self._list_worker is not None and not self._list_worker.isRunning():
            self._list_worker.deleteLater()
            self._list_worker = None

    def _on_list_result(self, path, entries):
        """列目录结果回写：代际号不匹配（已翻走）则丢弃，否则刷表格并处理待办路径"""
        worker = self.sender()
        if worker and hasattr(worker, '_list_gen') and worker._list_gen != self._list_generation:
            return
        self._listing = False
        self._remote_path = path
        self._remote_entries = entries
        self._edit_remote_path.setText(path)
        self._populate_remote(entries)
        # 目录切换后清空远程搜索框
        if self._remote_search_edit.text():
            self._remote_search_edit.blockSignals(True)
            self._remote_search_edit.clear()
            self._remote_search_edit.blockSignals(False)
        dirs = [e for e in entries if e['is_dir']]
        files = [e for e in entries if not e['is_dir']]
        self._lbl_status.setText(f'{len(dirs)} 个目录, {len(files)} 个文件')
        self._log(f'[SFTP] 目录加载完成: {path} ({len(dirs)} 目录, {len(files)} 文件)')
        self._list_fallback_done = False
        self._process_pending_remote_path()

    def _on_list_error(self, error):
        """列目录失败：路径不存在时自动回根目录重试一次（保底不卡死）"""
        worker = self.sender()
        if worker and hasattr(worker, '_list_gen') and worker._list_gen != self._list_generation:
            return
        self._listing = False
        self._lbl_status.setText(f'列表失败: {error}')
        self._log(f'[SFTP] 列表失败: {error}')
        # 保底机制：路径不存在时自动回到根目录（仅重试一次，避免无限循环）
        err_text = str(error)
        if not getattr(self, '_list_fallback_done', False) and (
                '文件或路径不存在' in err_text or 'No such file' in err_text or '[Errno 2]' in err_text):
            self._list_fallback_done = True
            self._pending_remote_path = None
            self._log('[SFTP] 路径不存在，自动回到根目录 /')
            self._edit_remote_path.setText('/')
            self._list_remote('/')
            return
        self._process_pending_remote_path()

    def _process_pending_remote_path(self):
        """上次列目录进行中时被压住的目录请求，此处补跳"""
        pending = self._pending_remote_path
        if pending is not None:
            self._pending_remote_path = None
            self._list_remote(pending)

    def _populate_remote(self, entries):
        """远程条目 → 树控件：批量插入禁重绘禁排序，完成后恢复并按表头重排"""
        self._tree.setUpdatesEnabled(False)  # 批量插入时禁止重绘，避免大列表卡顿
        try:
            self._tree.setSortingEnabled(False)  # 插入期间禁排序，避免逐条重排（O(n²)）
            self._tree.clear()
            dirs = sorted([e for e in entries if e['is_dir']], key=lambda x: x['name'])
            files = sorted([e for e in entries if not e['is_dir']], key=lambda x: x['name'])
            for entry in dirs + files:
                item = _SortableTreeItem()
                item.setIcon(0, _file_icon(entry['name'], entry['is_dir']))
                item.setText(0, entry['name'])
                item.setText(1, self._format_size(entry['size']) if not entry['is_dir'] else '')
                item.setText(2, '目录' if entry['is_dir'] else '文件')
                item.setText(3, entry['perm'])
                item.setText(4, entry['mtime'])
                item.setData(0, Qt.ItemDataRole.UserRole, entry)
                self._tree.addTopLevelItem(item)
        finally:
            # 恢复排序并按当前表头指示器（列/方向）重排，用户点击的表头选择得以保留
            self._tree.setSortingEnabled(True)
            _h = self._tree.header()
            self._tree.sortItems(_h.sortIndicatorSection(), _h.sortIndicatorOrder())
            self._tree.setUpdatesEnabled(True)

    # ------------------------------------------------------------------ 搜索
    def _on_search_shortcut(self):
        """Ctrl+F：按焦点所在侧弹出对应的搜索框（默认远程侧）"""
        focus_right = self._right_panel and self._right_panel.isAncestorOf(self.focusWidget())
        focus_left = self._left_panel and self._left_panel.isAncestorOf(self.focusWidget())
        if focus_right or (not focus_left and self._right_panel is not None):
            self._remote_search_frame.show()
            self._remote_search_edit.setFocus()
        else:
            self._local_search_frame.show()
            self._local_search_edit.setFocus()

    def _hide_search_boxes(self):
        """Esc：收起两侧搜索框并清空关键词恢复完整列表"""
        self._local_search_frame.hide()
        self._remote_search_frame.hide()
        # 清空搜索文本，恢复完整列表
        if self._local_search_edit.text():
            self._local_search_edit.clear()
        if self._remote_search_edit.text():
            self._remote_search_edit.clear()

    def _on_remote_search(self, text=''):
        """远程侧即时过滤：子串包含匹配，只在内存条目上过滤不重新列目录"""
        keyword = text.strip()
        if not keyword:
            # 清空搜索：恢复完整列表
            self._populate_remote(self._remote_entries)
            self._lbl_status.setText('就绪')
            return
        kw = keyword.lower()
        matched = [e for e in self._remote_entries if kw in e['name'].lower()]
        self._populate_remote(matched)
        self._lbl_status.setText(f'找到 {len(matched)} 个匹配项')

    def _on_local_search(self, text=''):
        """本地侧即时过滤（同远程侧，仅内存过滤）"""
        keyword = text.strip()
        if not keyword:
            # 清空搜索：恢复完整列表
            self._populate_local(self._local_entries)
            self._lbl_status.setText('就绪')
            return
        kw = keyword.lower()
        matched = [e for e in self._local_entries if kw in e['name'].lower()]
        self._populate_local(matched)
        self._lbl_status.setText(f'找到 {len(matched)} 个匹配项')

    def _populate_local(self, entries):
        """本地条目 → 树控件（与远程侧同策略：批量插入后恢复排序）"""
        self._local_tree.setUpdatesEnabled(False)  # 批量插入时禁止重绘
        try:
            self._local_tree.setSortingEnabled(False)  # 插入期间禁排序，避免逐条重排（O(n²)）
            self._local_tree.clear()
            dirs = sorted([e for e in entries if e['is_dir']], key=lambda x: x['name'].lower())
            files = sorted([e for e in entries if not e['is_dir']], key=lambda x: x['name'].lower())
            for entry in dirs + files:
                item = _SortableTreeItem()
                item.setIcon(0, _file_icon(entry['name'], entry['is_dir']))
                item.setText(0, entry['name'])
                item.setText(1, self._format_size(entry['size']) if not entry['is_dir'] else '')
                item.setText(2, '目录' if entry['is_dir'] else '文件')
                item.setText(3, entry['mtime'])
                item.setData(0, Qt.ItemDataRole.UserRole, entry)
                self._local_tree.addTopLevelItem(item)
        finally:
            # 恢复排序并按当前表头指示器（列/方向）重排，用户点击的表头选择得以保留
            self._local_tree.setSortingEnabled(True)
            _h = self._local_tree.header()
            self._local_tree.sortItems(_h.sortIndicatorSection(), _h.sortIndicatorOrder())
            self._local_tree.setUpdatesEnabled(True)

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
        """列本地目录：路径无效提示，条目按目录/文件分组排序后填充"""
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
        # 目录切换后清空本地搜索框
        if self._local_search_edit.text():
            self._local_search_edit.blockSignals(True)
            self._local_search_edit.clear()
            self._local_search_edit.blockSignals(False)

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
        """重新列当前远程目录"""
        self._list_remote(self._remote_path)

    def _go_up(self):
        """远程目录上跳一级（到根为止）"""
        parent = '/'.join(self._remote_path.rstrip('/').split('/')[:-1])
        if not parent:
            parent = '/'
        self._list_remote(parent)

    def _on_item_double_clicked(self, item, column):
        """远程条目双击：目录进入，文件直接下载（列目录中不响应）"""
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

    # ------------------------------------------------------------------ 拖拽上传
    def eventFilter(self, obj, event):
        """远程文件列表/传输队列表拖放事件：从资源管理器拖入文件/目录即上传到当前远程目录

        P1-5：队列表同样接收拖入（用户自然会把文件拖向"传输队列"，
        与拖到远程树等价，均上传到当前远程目录）。
        """
        if obj is self._tree or obj is self._transfer_table:
            etype = event.type()
            if etype == QEvent.Type.DragEnter:
                if event.mimeData().hasUrls():
                    event.acceptProposedAction()
                    return True
            elif etype == QEvent.Type.DragMove:
                if event.mimeData().hasUrls():
                    event.acceptProposedAction()
                    return True
            elif etype == QEvent.Type.Drop:
                event.acceptProposedAction()
                self._handle_drop_files(event)
                return True
        return super().eventFilter(obj, event)

    def _move_transfer_row(self, from_row, to_row):
        """将传输队列的 from_row 整行移动到 to_row 位置（item + cellWidget 一起搬）"""
        table = self._transfer_table
        if from_row < 0 or from_row >= table.rowCount():
            return
        if from_row == to_row:
            return

        # Qt 的 moveRow 不会带着 cellWidget（进度条）一起搬，只能存数据→删行→插行重建
        # 1. 保存源行所有数据
        col_count = table.columnCount()
        items_data = []
        for c in range(col_count):
            item = table.item(from_row, c)
            items_data.append(item.text() if item else '')
        # P2-7：tid 存于 UserRole，随行重建搬运（丢了行映射就断了）
        src_item = table.item(from_row, 0)
        row_tid = src_item.data(Qt.ItemDataRole.UserRole) if src_item else None
        # 保存进度条值
        pb_widget = table.cellWidget(from_row, 1)
        pb_value = pb_widget.value() if pb_widget else 0

        # 2. 删除源行
        table.removeRow(from_row)

        # 3. 调整目标行索引（删除后索引可能变化）
        if from_row < to_row:
            to_row -= 1
        # 插入新行
        table.insertRow(to_row)

        # 4. 恢复数据到新行
        name_item = QTableWidgetItem(items_data[0])
        if row_tid is not None:
            name_item.setData(Qt.ItemDataRole.UserRole, row_tid)  # P2-7
        table.setItem(to_row, 0, name_item)
        pb = ProgressBar()
        pb.setRange(0, 100)
        pb.setValue(pb_value)
        table.setCellWidget(to_row, 1, pb)
        table.setItem(to_row, 2, QTableWidgetItem(items_data[2]))
        table.setItem(to_row, 3, QTableWidgetItem(items_data[3]))

        # 5. 重建所有行的 row 映射
        self._rebuild_transfer_row_map()

    def _rebuild_transfer_row_map(self):
        """拖拽调序后重建 _transfer_workers 中的 row 映射

        P2-7：直接读第 0 列 item 的 UserRole 里存的 tid（建行时写入），
        不再用"文件名是否出现在行文本里"的子串匹配启发式——
        同时传 a.txt 与 aa.txt（或名字互相包含的目录）时会串行。
        """
        for row in range(self._transfer_table.rowCount()):
            item = self._transfer_table.item(row, 0)
            if not item:
                continue
            tid = item.data(Qt.ItemDataRole.UserRole)
            if tid is not None and tid in self._transfer_workers:
                self._transfer_workers[tid]['row'] = row

    def _handle_drop_files(self, event):
        """解析拖入的 QUrl 列表，逐个触发上传到当前远程目录"""
        if self._transport is None or not self._transport.is_active():
            self._lbl_status.setText('未连接，无法上传')
            self._log('[SFTP] 拖拽上传失败：未连接')
            return
        paths = []
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if p and os.path.exists(p):
                paths.append(p)
        if not paths:
            self._log('[SFTP] 拖入的内容不含有效的本地文件')
            return
        for p in paths:
            data = {
                'name': os.path.basename(p.rstrip('/\\')),
                'is_dir': os.path.isdir(p),
                'path': p,
            }
            self._upload_file(data)
        self._log(f'[SFTP] 拖拽上传 {len(paths)} 项 -> {self._remote_path}')

    # ------------------------------------------------------------------ 上传 / 下载
    def _upload_file(self, data=None):
        """上传入口：带参传单条，无参按钮点击批量上传选中项；目录转 _upload_dir"""
        if not isinstance(data, dict):
            data = None
        # 无参点击按钮：对所有选中项批量上传（无选中则回退 currentItem 单选流程）
        if data is None:
            items = self._local_tree.selectedItems()
            if len(items) > 1 or (items and items[0] is not self._local_tree.currentItem()):
                for it in items:
                    d = it.data(0, Qt.ItemDataRole.UserRole)
                    if d:
                        self._upload_file(d)
                return
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
        self._start_transfer_op(worker, data['name'], '上传', file_size,
                                op='upload', local_path=local_path, remote_path=remote_path)

    def _upload_dir(self, data):
        """整目录上传（递归 worker）"""
        local_dir = data['path']
        dir_name = data['name']
        remote_dir = self._remote_path.rstrip('/') + '/' + dir_name
        self._log(f'[SFTP] 上传目录: {local_dir} -> {remote_dir}')
        worker = SFTPDirTransferWorker(self._conn_params, 'upload_dir',
                                       local_dir=local_dir, remote_dir=remote_dir, dir_name=dir_name)
        self._start_transfer_op(worker, f'[目录] {dir_name}', '上传', 0,
                                op='upload_dir', local_path=local_dir, remote_path=remote_dir)

    def _download_file(self, entry=None):
        """下载入口：带参传单条，无参按钮点击批量下载选中项；目录转 _download_dir"""
        if not isinstance(entry, dict):
            entry = None
        # 无参点击按钮：对所有选中项批量下载（无选中则回退 currentItem 单选流程）
        if entry is None:
            items = self._tree.selectedItems()
            if len(items) > 1 or (items and items[0] is not self._tree.currentItem()):
                for it in items:
                    e = it.data(0, Qt.ItemDataRole.UserRole)
                    if e:
                        self._download_file(e)
                return
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
        self._start_transfer_op(worker, entry['name'], '下载', file_size,
                                op='download', local_path=local_path, remote_path=remote_path)

    def _download_dir(self, entry):
        """整目录下载（递归 worker）"""
        dir_name = entry['name']
        remote_dir = self._remote_path.rstrip('/') + '/' + dir_name
        local_dir = os.path.join(self._local_path, dir_name)
        self._log(f'[SFTP] 下载目录: {remote_dir} -> {local_dir}')
        worker = SFTPDirTransferWorker(self._conn_params, 'download_dir',
                                       local_dir=local_dir, remote_dir=remote_dir, dir_name=dir_name)
        self._start_transfer_op(worker, f'[目录] {dir_name}', '下载', 0,
                                op='download_dir', local_path=local_dir, remote_path=remote_dir)

    def _start_transfer_op(self, worker, filename, op_label, file_size,
                           op=None, local_path='', remote_path=''):
        """传输任务入队：建表格行/进度条，接 progress/success/error 信号后启动；
        params 快照留存供右键重试/打开文件夹使用"""
        tid = self._next_transfer_id
        self._next_transfer_id += 1
        row = self._transfer_table.rowCount()
        self._transfer_table.insertRow(row)
        name_item = QTableWidgetItem(f'{op_label}: {filename}')
        # P2-7：tid 写入第 0 列 UserRole——拖拽调序后行映射直读，
        # 不再用"文件名是否出现在行文本里"的子串匹配（a.txt/aa.txt 会串行）
        name_item.setData(Qt.ItemDataRole.UserRole, tid)
        self._transfer_table.setItem(row, 0, name_item)
        pb = ProgressBar()
        pb.setRange(0, 100)
        pb.setValue(0)
        self._transfer_table.setCellWidget(row, 1, pb)
        self._transfer_table.setItem(row, 2, QTableWidgetItem('0 B/s'))
        self._transfer_table.setItem(row, 3, QTableWidgetItem('排队中'))
        now = time.time()
        info = {'worker': worker, 'row': row, 'start_time': now,
                'last_bytes': 0, 'last_time': now, 'speed': 0.0, 'state': 'queued',
                # 重试所需参数快照：(conn_params, op, local_path, remote_path)
                'params': (self._conn_params, op, local_path, remote_path)}
        self._transfer_workers[tid] = info
        worker.progress.connect(lambda t, tot, _tid=tid: self._on_transfer_progress(_tid, t, tot))
        worker.success.connect(lambda msg, _tid=tid: self._on_transfer_success(_tid, msg))
        worker.error.connect(lambda err, _tid=tid: self._on_transfer_error(_tid, err))
        # P2-1：经并发闸门启动（满载时排队，有空位再启动）
        self._launch_or_queue_transfer(tid)

    def _on_transfer_progress(self, tid, transferred, total):
        """字节进度 → 进度条百分比 + 限速显示（≥ 0.5s 采样一次）"""
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
        """传输完成：置 100%/完成态；P1-10 按需刷新（仅目标目录==当前浏览目录，
        400ms 去抖合并），不再无条件重列两侧目录打断用户浏览"""
        info = self._transfer_workers.get(tid)
        if info:
            info['state'] = 'done'  # P2-1：释放并发槽位
            self._schedule_pending_save()  # P2-2：任务完成即从落盘队列移除
            row = info['row']
            pb = self._transfer_table.cellWidget(row, 1)
            if pb:
                pb.setValue(100)
            status_item = self._transfer_table.item(row, 3)
            if status_item:
                status_item.setText('完成')
            # A1：下载成功后发射全局信号，联动主窗口
            params = info.get('params')
            if params and params[1] == 'download':
                local_path = params[2]
                if local_path:
                    GLOBAL_SIGNALS.file_downloaded.emit(
                        _videos_top_dir(local_path), local_path, 1)
            # P1-10：按任务类型计算受影响目录，与当前浏览目录一致才列入待刷新
            if params:
                self._schedule_refresh_for(params)
        # 注意：不在此处删除 info，保留 params 供"打开所在文件夹"使用；
        # worker 线程已结束，行被删除/清空/关闭时统一释放
        self._pump_transfer_queue()  # P2-1：空出一个并发位，启动排队任务
        self._lbl_status.setText(msg)
        self._log(f'[SFTP] {msg}')

    def _schedule_refresh_for(self, params):
        """P1-10：根据任务参数决定刷新哪一侧（去抖合并，到期时再校验当前目录）"""
        _conn, op, local_path, remote_path = params
        if op in ('upload', 'upload_dir'):
            # 上传影响远程侧目标目录
            if self._same_remote_dir(self._posix_dirname(remote_path)):
                self._pending_refresh.add('remote')
        elif op in ('download', 'download_dir'):
            target = os.path.dirname(os.path.normpath(local_path or ''))
            if target and self._same_local_dir(target):
                self._pending_refresh.add('local')
        else:
            # 快速操作（delete/mkdir/rename/create_file）都在当前远程目录
            if self._same_remote_dir(self._posix_dirname(remote_path)):
                self._pending_refresh.add('remote')
        if self._pending_refresh and not self._refresh_timer.isActive():
            self._refresh_timer.start()

    @staticmethod
    def _posix_dirname(path):
        """POSIX 风格取父目录（远程路径用，root 的父仍是 root）"""
        p = (path or '').rstrip('/')
        return p.rsplit('/', 1)[0] if '/' in p else '/'

    def _same_remote_dir(self, target) -> bool:
        cur = (self._remote_path or '').rstrip('/') or '/'
        return (target or '').rstrip('/') == cur

    def _same_local_dir(self, target) -> bool:
        try:
            return (os.path.normcase(os.path.normpath(target))
                    == os.path.normcase(os.path.normpath(self._local_path)))
        except Exception:
            return False

    def _flush_pending_refresh(self):
        """去抖到期：执行挂起的目录刷新（此刻再取当前目录，保留代际号机制）"""
        pending = self._pending_refresh
        self._pending_refresh = set()
        if 'remote' in pending:
            self._list_remote(self._remote_path)
        if 'local' in pending:
            self._list_local(self._local_path)

    def _on_transfer_error(self, tid, error):
        """传输失败：行内置失败原因；info 保留供右键"重新开始" """
        info = self._transfer_workers.get(tid)
        if info:
            info['state'] = 'failed'  # P2-1：释放并发槽位
            self._schedule_pending_save()  # P2-2：失败任务不留在待恢复队列里（避免开机就问）
            row = info['row']
            status_item = self._transfer_table.item(row, 3)
            if status_item:
                status_item.setText(f'失败: {error}')
        # 保留 info（含 params）供右键"重新开始"使用，行删除时统一释放 worker
        self._pump_transfer_queue()  # P2-1：空出一个并发位，启动排队任务
        self._lbl_status.setText(f'操作失败: {error}')
        self._log(f'[SFTP] 操作失败: {error}')

    # ------------------------------------------------------------------ 右键菜单预构建
    def _build_context_menus(self):
        """初始化时预构建所有右键菜单（Action/图标/信号仅创建一次，右键时直接弹出）"""
        # ---- 传输队列菜单（完全静态，仅更新 enabled 状态）
        self._ctx_transfer_row = -1
        menu = RoundMenu(parent=self)
        self._act_t_pause = Action(FluentIcon.PAUSE, '暂停', self)
        self._act_t_pause.triggered.connect(lambda: self._transfer_pause_row(self._ctx_transfer_row))
        menu.addAction(self._act_t_pause)
        self._act_t_pause_all = Action(FluentIcon.PAUSE, '全部暂停', self)
        self._act_t_pause_all.triggered.connect(self._transfer_pause_all)
        menu.addAction(self._act_t_pause_all)
        self._act_t_resume = Action(FluentIcon.PLAY, '继续', self)
        self._act_t_resume.triggered.connect(lambda: self._transfer_resume_row(self._ctx_transfer_row))
        menu.addAction(self._act_t_resume)
        self._act_t_resume_all = Action(FluentIcon.PLAY, '全部继续', self)
        self._act_t_resume_all.triggered.connect(self._transfer_resume_all)
        menu.addAction(self._act_t_resume_all)
        menu.addSeparator()
        self._act_t_delete = Action(FluentIcon.DELETE, '删除', self)
        self._act_t_delete.triggered.connect(lambda: self._transfer_delete_row(self._ctx_transfer_row))
        menu.addAction(self._act_t_delete)
        self._act_t_delete_all = Action(FluentIcon.DELETE, '全部删除', self)
        self._act_t_delete_all.triggered.connect(self._transfer_delete_all)
        menu.addAction(self._act_t_delete_all)
        menu.addSeparator()
        # P2-2：文案改"重新开始"——重传是从头开始而非断点续传，
        # "重试"会让用户误以为能续传省流量（2GB 传到 99% 重试又从 0 开始）
        self._act_t_retry = Action(FluentIcon.SYNC, '重新开始', self)
        self._act_t_retry.triggered.connect(lambda: self._transfer_retry_row(self._ctx_transfer_row))
        menu.addAction(self._act_t_retry)
        self._act_t_open_folder = Action(FluentIcon.FOLDER, '打开所在文件夹', self)
        self._act_t_open_folder.triggered.connect(lambda: self._transfer_open_folder(self._ctx_transfer_row))
        menu.addAction(self._act_t_open_folder)
        self._act_t_clear_done = Action(FluentIcon.BROOM, '清除已完成', self)
        self._act_t_clear_done.triggered.connect(self._transfer_clear_completed)
        menu.addAction(self._act_t_clear_done)
        self._ctx_transfer_menu = menu

        # ---- 本地面板菜单（item 相关 + 新建子菜单）
        self._ctx_local_data = None
        self._ctx_local_items = []  # 右键时的传输目标列表（Ctrl 多选时含全部选中项）
        self._ctx_local_menu_full = RoundMenu(parent=self)  # 右键点击文件时
        self._ctx_local_menu_empty = RoundMenu(parent=self)  # 右键点击空白时
        act = Action(FluentIcon.UP, '传输（上传）', self)
        act.triggered.connect(lambda: self._upload_items(self._ctx_local_items))
        self._ctx_local_menu_full.addAction(act)
        self._act_ctx_upload = act  # 右键时按选中数量动态更新文案
        act = Action(FluentIcon.LIBRARY, '打开', self)
        act.triggered.connect(lambda: self._ctx_local_open(self._ctx_local_data))
        self._ctx_local_menu_full.addAction(act)
        act = Action(FluentIcon.COPY, '复制路径', self)
        act.triggered.connect(self._ctx_copy_local_path)
        self._ctx_local_menu_full.addAction(act)
        act = Action(FluentIcon.EDIT, '重命名', self)
        act.triggered.connect(lambda: self._ctx_rename_local(self._ctx_local_data))
        self._ctx_local_menu_full.addAction(act)
        act = Action(FluentIcon.DELETE, '删除', self)
        act.triggered.connect(lambda: self._ctx_delete_local(self._ctx_local_data))
        self._ctx_local_menu_full.addAction(act)
        act = Action(FluentIcon.SYNC, '刷新', self)
        act.triggered.connect(lambda: self._list_local(self._local_path))
        self._ctx_local_menu_full.addAction(act)
        self._ctx_local_menu_full.addSeparator()
        new_local = RoundMenu('新建', self)
        new_local.addAction(Action(FluentIcon.DOCUMENT, '新建文件', triggered=self._ctx_new_file_local))
        new_local.addAction(Action(FluentIcon.FOLDER, '新建文件夹', triggered=self._ctx_new_dir_local))
        self._ctx_local_menu_full.addMenu(new_local)
        # 空白菜单（仅新建）
        new_local2 = RoundMenu('新建', self)
        new_local2.addAction(Action(FluentIcon.DOCUMENT, '新建文件', triggered=self._ctx_new_file_local))
        new_local2.addAction(Action(FluentIcon.FOLDER, '新建文件夹', triggered=self._ctx_new_dir_local))
        self._ctx_local_menu_empty.addMenu(new_local2)

        # ---- 远程面板菜单（item 相关 + 新建子菜单）
        self._ctx_remote_entry = None
        self._ctx_remote_items = []  # 右键时的传输目标列表（Ctrl 多选时含全部选中项）
        self._ctx_remote_menu_full = RoundMenu(parent=self)
        self._ctx_remote_menu_empty = RoundMenu(parent=self)
        act = Action(FluentIcon.DOWN, '传输（下载）', self)
        act.triggered.connect(lambda: self._download_items(self._ctx_remote_items))
        self._ctx_remote_menu_full.addAction(act)
        self._act_ctx_download = act  # 右键时按选中数量动态更新文案
        act = Action(FluentIcon.LIBRARY, '打开', self)
        act.triggered.connect(lambda: self._ctx_remote_open(self._ctx_remote_entry))
        self._ctx_remote_menu_full.addAction(act)
        act = Action(FluentIcon.COPY, '复制路径', self)
        act.triggered.connect(self._ctx_copy_remote_path)
        self._ctx_remote_menu_full.addAction(act)
        act = Action(FluentIcon.EDIT, '重命名', self)
        act.triggered.connect(lambda: self._ctx_rename_remote(self._ctx_remote_entry))
        self._ctx_remote_menu_full.addAction(act)
        act = Action(FluentIcon.DELETE, '删除', self)
        act.triggered.connect(lambda: self._ctx_delete_remote(self._ctx_remote_entry))
        self._ctx_remote_menu_full.addAction(act)
        act = Action(FluentIcon.SYNC, '刷新', self)
        act.triggered.connect(lambda: self._list_remote(self._remote_path))
        self._ctx_remote_menu_full.addAction(act)
        self._ctx_remote_menu_full.addSeparator()
        new_remote = RoundMenu('新建', self)
        new_remote.addAction(Action(FluentIcon.DOCUMENT, '新建文件', triggered=self._ctx_new_file_remote))
        new_remote.addAction(Action(FluentIcon.FOLDER, '新建文件夹', triggered=self._ctx_new_dir_remote))
        self._ctx_remote_menu_full.addMenu(new_remote)
        # 空白菜单（仅新建）
        new_remote2 = RoundMenu('新建', self)
        new_remote2.addAction(Action(FluentIcon.DOCUMENT, '新建文件', triggered=self._ctx_new_file_remote))
        new_remote2.addAction(Action(FluentIcon.FOLDER, '新建文件夹', triggered=self._ctx_new_dir_remote))
        self._ctx_remote_menu_empty.addMenu(new_remote2)

    def _ctx_copy_local_path(self):
        data = self._ctx_local_data
        if data:
            QApplication.clipboard().setText(data['path'])
            self._log(f'[SFTP] 已复制路径: {data["path"]}')

    def _ctx_copy_remote_path(self):
        entry = self._ctx_remote_entry
        if entry:
            remote_full = self._remote_path.rstrip('/') + '/' + entry['name']
            QApplication.clipboard().setText(remote_full)
            self._log(f'[SFTP] 已复制路径: {remote_full}')

    # ------------------------------------------------------------------ 传输队列右键菜单
    def _on_transfer_context_menu(self, pos):
        """传输队列右键：按行状态（传输中/已暂停/失败/完成）启用对应菜单项"""
        row = self._transfer_table.rowAt(pos.y())
        has_selection = row >= 0
        has_tasks = self._transfer_table.rowCount() > 0
        selected_status = ''
        if has_selection:
            status_item = self._transfer_table.item(row, 3)
            if status_item:
                selected_status = status_item.text()
        # 更新缓存菜单的 enabled 状态
        self._ctx_transfer_row = row
        self._act_t_pause.setEnabled(has_selection and selected_status == '传输中')
        self._act_t_pause_all.setEnabled(has_tasks)
        self._act_t_resume.setEnabled(has_selection and selected_status == '已暂停')
        self._act_t_resume_all.setEnabled(has_tasks)
        self._act_t_delete.setEnabled(has_selection)
        self._act_t_delete_all.setEnabled(has_tasks)
        self._act_t_retry.setEnabled(has_selection and selected_status.startswith('失败'))
        self._act_t_open_folder.setEnabled(has_selection and selected_status == '完成')
        self._act_t_clear_done.setEnabled(has_tasks)
        self._ctx_transfer_menu.exec(
            self._transfer_table.viewport().mapToGlobal(pos),
            aniType=_popup_ani_type())

    def _find_tid_by_row(self, row):
        """表格行号反查任务 tid（行号在增删后由调用方维护）"""
        for tid, info in self._transfer_workers.items():
            if info['row'] == row:
                return tid
        return None

    def _transfer_pause_row(self, row):
        """暂停指定行任务（下次进度回调处阻塞生效）；P2-1 起按状态机判定"""
        tid = self._find_tid_by_row(row)
        if tid is None:
            return
        info = self._transfer_workers[tid]
        if info.get('state') != 'running':
            return
        worker = info['worker']
        if hasattr(worker, 'pause'):
            worker.pause()
        info['state'] = 'paused'
        status_item = self._transfer_table.item(row, 3)
        if status_item:
            status_item.setText('已暂停')
        name_item = self._transfer_table.item(row, 0)
        name = name_item.text() if name_item else f'任务{tid}'
        self._log(f'[SFTP] 已暂停传输: {name}')
        self._schedule_pending_save()

    def _transfer_resume_row(self, row):
        """恢复指定行任务并重置速度采样基准；P2-1 起按状态机判定"""
        tid = self._find_tid_by_row(row)
        if tid is None:
            return
        info = self._transfer_workers[tid]
        if info.get('state') != 'paused':
            return
        worker = info['worker']
        if hasattr(worker, 'resume'):
            worker.resume()
        info['state'] = 'running'
        info['last_time'] = time.time()
        info['speed'] = 0.0
        status_item = self._transfer_table.item(row, 3)
        if status_item:
            status_item.setText('传输中')
        name_item = self._transfer_table.item(row, 0)
        name = name_item.text() if name_item else f'任务{tid}'
        self._log(f'[SFTP] 已继续传输: {name}')
        self._schedule_pending_save()

    def _transfer_pause_all(self):
        """暂停全部传输中任务（P2-1 起按状态机判定，排队中任务不受影响）"""
        count = 0
        for tid, info in list(self._transfer_workers.items()):
            if info.get('state') != 'running':
                continue
            row = info['row']
            status_item = self._transfer_table.item(row, 3)
            worker = info['worker']
            if hasattr(worker, 'pause'):
                worker.pause()
            info['state'] = 'paused'
            if status_item:
                status_item.setText('已暂停')
            count += 1
        if count:
            self._log(f'[SFTP] 已暂停全部传输 ({count} 个任务)')
            self._schedule_pending_save()

    def _transfer_resume_all(self):
        """恢复全部已暂停任务"""
        count = 0
        for tid, info in list(self._transfer_workers.items()):
            if info.get('state') != 'paused':
                continue
            row = info['row']
            status_item = self._transfer_table.item(row, 3)
            worker = info['worker']
            if hasattr(worker, 'resume'):
                worker.resume()
            info['state'] = 'running'
            info['last_time'] = time.time()
            info['speed'] = 0.0
            if status_item:
                status_item.setText('传输中')
            count += 1
        if count:
            self._log(f'[SFTP] 已继续全部传输 ({count} 个任务)')
            self._schedule_pending_save()

    def _transfer_delete_row(self, row):
        """删除指定行任务：运行中先 stop，删行后修正后续行的行号映射；
        P2-1：排队任务出队，删的是占位任务时泵队列补位"""
        tid = self._find_tid_by_row(row)
        name_item = self._transfer_table.item(row, 0)
        name = name_item.text() if name_item else f'任务{row}'
        if tid is not None:
            info = self._transfer_workers.get(tid)
            if info:
                was_active = info.get('state') in ('running', 'paused')
                if tid in self._queued_transfers:
                    self._queued_transfers.remove(tid)
                worker = info['worker']
                if hasattr(worker, 'stop'):
                    worker.stop()
                self._safe_delete_transfer_worker(tid)
                if was_active:
                    self._pump_transfer_queue()
        self._transfer_table.removeRow(row)
        for t, inf in self._transfer_workers.items():
            if inf['row'] > row:
                inf['row'] -= 1
        self._log(f'[SFTP] 已删除传输: {name}')
        self._schedule_pending_save()

    def _transfer_delete_all(self):
        """清空传输队列（逐个 stop 后释放 worker；P2-1：排队表一并清空）"""
        self._queued_transfers = []
        for tid in list(self._transfer_workers.keys()):
            info = self._transfer_workers.get(tid)
            if info:
                worker = info['worker']
                if hasattr(worker, 'stop'):
                    worker.stop()
                self._safe_delete_transfer_worker(tid)
        self._transfer_table.setRowCount(0)
        self._log('[SFTP] 已清空传输队列')
        self._schedule_pending_save()

    def _transfer_retry_row(self, row):
        """失败任务重新开始：从 info['params'] 取参数重建 worker 发起传输

        P2-2：重传从头开始（非断点续传），日志/文案明确"从头传输"；
        P2-1：重建任务经并发闸门（满载时排队而非立即起第 N+1 条连接）。
        """
        tid = self._find_tid_by_row(row)
        if tid is None:
            return
        info = self._transfer_workers.get(tid)
        params = info.get('params') if info else None
        if not params or not params[1]:
            self._log('[SFTP] 该任务缺少重试参数，无法重新开始')
            return
        conn_params, op, local_path, remote_path = params
        if op == 'upload_dir':
            worker = SFTPDirTransferWorker(
                conn_params, op, local_dir=local_path, remote_dir=remote_path,
                dir_name=os.path.basename(local_path.rstrip('/\\')))
        elif op == 'download_dir':
            worker = SFTPDirTransferWorker(
                conn_params, op, local_dir=local_path, remote_dir=remote_path,
                dir_name=os.path.basename(remote_path.rstrip('/')))
        else:
            file_size = os.path.getsize(local_path) if (op == 'upload' and local_path and os.path.isfile(local_path)) else 0
            worker = SFTPOperationWorker(conn_params, op, local_path, remote_path, file_size=file_size)
        # 断开旧 worker 信号（防止失败行收到旧信号回写），换新 worker
        old_worker = info['worker']
        try:
            old_worker.progress.disconnect()
            old_worker.success.disconnect()
            old_worker.error.disconnect()
        except Exception:
            pass
        info['worker'] = worker
        info['last_bytes'] = 0
        info['last_time'] = time.time()
        info['speed'] = 0.0
        pb = self._transfer_table.cellWidget(row, 1)
        if pb:
            pb.setValue(0)
        speed_item = self._transfer_table.item(row, 2)
        if speed_item:
            speed_item.setText('0 B/s')
        worker.progress.connect(lambda t, tot, _tid=tid: self._on_transfer_progress(_tid, t, tot))
        worker.success.connect(lambda msg, _tid=tid: self._on_transfer_success(_tid, msg))
        worker.error.connect(lambda err, _tid=tid: self._on_transfer_error(_tid, err))
        # P2-1：经并发闸门（状态列随启动/排队更新）
        self._launch_or_queue_transfer(tid)
        name_item = self._transfer_table.item(row, 0)
        name = name_item.text() if name_item else f'任务{tid}'
        self._log(f'[SFTP] 重新开始传输（从头传输）: {name}')

    def _transfer_open_folder(self, row):
        """完成行：在资源管理器中定位本地文件（下载用目标路径，上传用源路径，均为 local_path）"""
        tid = self._find_tid_by_row(row)
        if tid is None:
            return
        info = self._transfer_workers.get(tid)
        params = info.get('params') if info else None
        if not params:
            return
        local_path = params[2]
        if not local_path or not os.path.exists(local_path):
            self._log('[SFTP] 本地文件不存在，无法定位')
            return
        try:
            subprocess.Popen(['explorer', '/select,', os.path.normpath(local_path)])
        except Exception as e:
            self._log(f'[SFTP] 打开所在文件夹失败: {e}')

    def _transfer_clear_completed(self):
        """清除所有状态为"完成"的行（进行中/暂停/失败的任务不动）"""
        table = self._transfer_table
        done_rows = []
        for row in range(table.rowCount()):
            status_item = table.item(row, 3)
            if status_item and status_item.text() == '完成':
                done_rows.append(row)
        for row in sorted(done_rows, reverse=True):
            tid = self._find_tid_by_row(row)
            if tid is not None:
                self._safe_delete_transfer_worker(tid)
            table.removeRow(row)
            for t, inf in self._transfer_workers.items():
                if inf['row'] > row:
                    inf['row'] -= 1
        if done_rows:
            self._log(f'[SFTP] 已清除 {len(done_rows)} 个完成的传输记录')
            self._schedule_pending_save()

    # ------------------------------------------------------------------ 删除 / 新建目录
    def _run_quick_op(self, op, remote_path, log_msg, local_path=''):
        """快速操作入传输队列（P1-9：产生队列表行与状态，失败可右键重试）

        与上传/下载同一套 _start_transfer_op 通道（params 快照齐全），
        进度条对零字节操作无意义但状态列可见（传输中→完成/失败）。
        """
        self._log(log_msg)
        worker = SFTPOperationWorker(self._conn_params, op, local_path, remote_path)
        label = _QUICK_OP_LABELS.get(op, op)
        self._start_transfer_op(worker, os.path.basename(remote_path.rstrip('/')),
                                label, 0, op=op, local_path=local_path,
                                remote_path=remote_path)

    def _selected_remote_entries(self):
        """右侧远程面板选中项（含 currentItem 兜底；空列表表示无选中）"""
        items = self._tree.selectedItems()
        if not items:
            item = self._tree.currentItem()
            if item is not None:
                items = [item]
        entries = [it.data(0, Qt.ItemDataRole.UserRole) for it in items]
        return [e for e in entries if e]

    def _confirm_delete_remote(self, entries) -> bool:
        """删除远程条目前的二次确认（P0-2：工具栏按钮与右键菜单共用口径）"""
        if not entries:
            return False
        if len(entries) == 1:
            e = entries[0]
            if e['is_dir']:
                msg = f'确定要删除远程目录 "{e["name"]}" 吗？\n注意：仅能删除空目录。'
            else:
                msg = f'确定要删除远程文件 "{e["name"]}" 吗？'
        else:
            msg = (f'确定要删除选中的 {len(entries)} 个远程文件/目录吗？\n'
                   '注意：目录仅能删除空目录。')
        dlg = MessageBox('确认删除', msg, self)
        dlg.yesButton.setText('删除')
        dlg.cancelButton.setText('取消')
        return bool(dlg.exec())

    def _update_delete_btn_text(self):
        """P0-2：删除按钮文案随远程侧选中数变化（与右键"传输（上传N 项）"同口径）"""
        n = len(self._tree.selectedItems())
        self._btn_delete.setText(f'删除（{n} 项）' if n > 1 else '删除')

    def _delete_selected(self):
        """删除远程选中项（P0-2：支持多选批量；二次确认与右键菜单同口径）"""
        entries = self._selected_remote_entries()
        if not entries:
            self._log('[SFTP] 请先在右侧远程面板选择要删除的文件或目录')
            return
        if not self._confirm_delete_remote(entries):
            return
        for entry in entries:
            remote_path = self._remote_path.rstrip('/') + '/' + entry['name']
            op = 'rmdir' if entry['is_dir'] else 'delete'
            self._run_quick_op(op, remote_path, f'[SFTP] 删除: {remote_path}')

    def _create_directory(self):
        """在当前远程目录新建目录（输入名后走 mkdir 任务）"""
        name = self._ask_name('新建目录', '目录名:')
        if not name:
            return
        remote_path = self._remote_path.rstrip('/') + '/' + name
        self._run_quick_op('mkdir', remote_path, f'[SFTP] 创建目录: {remote_path}')

    def _open_in_xftp(self):
        """用系统 Xftp 打开当前连接（凭据写入 URL，未安装时提示）"""
        if not shutil.which('xftp'):
            msg = "[提示] 未找到 Xftp，请确认已安装并加入系统 PATH"
            self._log(msg)
            dlg = MessageBox('未找到 Xftp', msg, self)
            dlg.cancelButton.setText('关闭')
            dlg.exec()
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
            dlg = MessageBox('打开失败', f'无法启动 Xftp：{e}', self)
            dlg.cancelButton.setText('关闭')
            dlg.exec()

    # ------------------------------------------------------------------ 回调
    # （P1-9 后快速操作统一走传输队列回调 _on_transfer_success/_on_transfer_error，
    #  旧 _on_quick_op_success/_on_quick_op_error 已移除）

    # ------------------------------------------------------------------ 工具
    def _ask_name(self, title, label, default=''):
        """弹出 Fluent 文本输入对话框，返回输入文本（取消返回 None）"""
        dlg = _TextInputDialog(title, label, default, self)
        dlg.yesButton.setText('确定')
        dlg.cancelButton.setText('取消')
        if dlg.exec():
            return dlg.edit.text().strip()
        return None

    @staticmethod
    def _format_size(size):
        """字节数 → 人类可读（B/KB/MB/GB/TB）"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f'{size:.1f} {unit}' if unit != 'B' else f'{size} {unit}'
            size /= 1024
        return f'{size:.1f} TB'

    # ------------------------------------------------------------------ 右键菜单（预构建缓存，直接弹出）
    def _on_local_context_menu(self, pos):
        """本地面板右键菜单（预构建缓存，零构建开销）"""
        item = self._local_tree.itemAt(pos)
        if item:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if not data:
                return
            self._ctx_local_data = data
            # Ctrl 多选传输：右键项在选中集内时，目标扩展为整个选中集（资源管理器惯例）
            self._ctx_local_items = self._collect_selected(self._local_tree, item, data)
            n = len(self._ctx_local_items)
            self._act_ctx_upload.setText(f'传输（上传{n} 项）' if n > 1 else '传输（上传）')
            self._ctx_local_menu_full.exec(
                self._local_tree.viewport().mapToGlobal(pos),
                aniType=_popup_ani_type())
        else:
            self._ctx_local_menu_empty.exec(
                self._local_tree.viewport().mapToGlobal(pos),
                aniType=_popup_ani_type())

    def _on_remote_context_menu(self, pos):
        """远程面板右键菜单（预构建缓存，零构建开销）"""
        item = self._tree.itemAt(pos)
        if item:
            entry = item.data(0, Qt.ItemDataRole.UserRole)
            if not entry:
                return
            self._ctx_remote_entry = entry
            # Ctrl 多选传输：右键项在选中集内时，目标扩展为整个选中集（资源管理器惯例）
            self._ctx_remote_items = self._collect_selected(self._tree, item, entry)
            n = len(self._ctx_remote_items)
            self._act_ctx_download.setText(f'传输（下载{n} 项）' if n > 1 else '传输（下载）')
            self._ctx_remote_menu_full.exec(
                self._tree.viewport().mapToGlobal(pos),
                aniType=_popup_ani_type())
        else:
            self._ctx_remote_menu_empty.exec(
                self._tree.viewport().mapToGlobal(pos),
                aniType=_popup_ani_type())

    def _collect_selected(self, tree, clicked_item, fallback):
        """收集右键操作的目标列表：

        右键项已在选中集内 → 返回整个选中集（支持 Ctrl 多选批量传输）；
        否则（右键未选中的项）→ 仅返回右键项本身，符合资源管理器惯例。"""
        items = tree.selectedItems()
        if any(it is clicked_item for it in items):
            return [it.data(0, Qt.ItemDataRole.UserRole) for it in items
                    if it.data(0, Qt.ItemDataRole.UserRole)]
        return [fallback]

    def _copy_selected_paths(self):
        """P1-4：Ctrl+C 复制选中项全路径（优先远程侧，其次本地侧；多选换行分隔）

        焦点在 QLineEdit（路径框/搜索框）时由输入框自身处理复制
        （QLineEdit accept ShortcutOverride），本回调不会被触发。
        """
        focus_widget = self.focusWidget()
        if (focus_widget is not None and self._right_panel is not None
                and self._right_panel.isAncestorOf(focus_widget)):
            entries = self._selected_remote_entries()
            paths = [self._remote_path.rstrip('/') + '/' + e['name']
                     for e in entries]
            if paths:
                QApplication.clipboard().setText('\n'.join(paths))
                self._log(f'[SFTP] 已复制 {len(paths)} 个远程路径')
            return
        if (focus_widget is not None and self._left_panel is not None
                and self._left_panel.isAncestorOf(focus_widget)):
            items = self._local_tree.selectedItems()
            datas = [it.data(0, Qt.ItemDataRole.UserRole) for it in items]
            paths = [d['path'] for d in datas if d]
            if paths:
                QApplication.clipboard().setText('\n'.join(paths))
                self._log(f'[SFTP] 已复制 {len(paths)} 个本地路径')

    def _upload_items(self, items):
        """批量上传（右键多选传输入口）：逐项调用单文件上传逻辑，目录走目录传输"""
        if not items:
            self._log('[SFTP] 请先选择一个文件或目录')
            return
        for d in items:
            self._upload_file(d)

    def _download_items(self, items):
        """批量下载（右键多选传输入口）：逐项调用单文件下载逻辑，目录走目录传输"""
        if not items:
            self._log('[SFTP] 请先选择一个文件或目录')
            return
        for e in items:
            self._download_file(e)

    # ---- 右键菜单操作实现 ----
    def _get_temp_dir(self):
        """打开下载用的 _sftp_temp 临时目录（不存在则建）"""
        from core.app_paths import get_app_dir
        base = get_app_dir()
        temp_dir = os.path.join(base, '_sftp_temp')
        os.makedirs(temp_dir, exist_ok=True)
        return temp_dir

    def _ctx_local_open(self, data):
        """本地文件右键打开（系统默认程序）"""
        path = data['path']
        try:
            os.startfile(path)
            self._log(f'[SFTP] 已打开: {path}')
        except OSError as e:
            self._log(f'[SFTP] 打开失败: {e}')

    def _ctx_remote_open(self, entry):
        """远程文件右键打开：先下载到 _sftp_temp 再启系统程序；
        目录整体下载后打开"""
        _cleanup_sftp_temp()  # 打开前清理超过 7 天的旧临时文件
        temp_dir = self._get_temp_dir()
        remote_path = self._remote_path.rstrip('/') + '/' + entry['name']
        if entry['is_dir']:
            local_dir = os.path.join(temp_dir, entry['name'])
            self._log(f'[SFTP] 下载目录并打开: {remote_path} -> {local_dir}')
            worker = SFTPDirTransferWorker(self._conn_params, 'download_dir',
                                           local_dir=local_dir, remote_dir=remote_path, dir_name=entry['name'])
            worker.success.connect(lambda msg, p=local_dir: self._open_after_download(p))
            self._start_transfer_op(worker, f'[打开] {entry["name"]}', '下载', 0,
                                    op='download_dir', local_path=local_dir, remote_path=remote_path)
        else:
            local_path = os.path.join(temp_dir, entry['name'])
            file_size = entry.get('size', 0)
            self._log(f'[SFTP] 下载并打开: {remote_path} -> {local_path}')
            worker = SFTPOperationWorker(self._conn_params, 'download', local_path, remote_path, file_size=file_size)
            worker.success.connect(lambda msg, p=local_path: self._open_after_download(p))
            self._start_transfer_op(worker, f'[打开] {entry["name"]}', '下载', file_size,
                                    op='download', local_path=local_path, remote_path=remote_path)

    def _open_after_download(self, path):
        """临时目录下载完成后的打开回调"""
        try:
            os.startfile(path)
            self._log(f'[SFTP] 已打开: {path}')
        except OSError as e:
            self._log(f'[SFTP] 打开失败: {e}')

    def _ctx_rename_local(self, data):
        """本地文件重命名（同目录内，权限不足单独提示）"""
        new_name = self._ask_name('重命名', '新名称:', data['name'])
        if not new_name or new_name == data['name']:
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
        """远程文件重命名（走 rename 任务）"""
        new_name = self._ask_name('重命名', '新名称:', entry['name'])
        if not new_name or new_name == entry['name']:
            return
        old_path = self._remote_path.rstrip('/') + '/' + entry['name']
        new_path = self._remote_path.rstrip('/') + '/' + new_name
        self._run_quick_op('rename', new_path,
                           f'[SFTP] 重命名: {old_path} -> {new_path}',
                           local_path=old_path)

    def _ctx_delete_local(self, data):
        """本地文件/目录删除（二次确认，目录整树删除）"""
        if data['is_dir']:
            msg = f'确定要删除本地目录 "{data["name"]}" 及其所有内容吗？'
        else:
            msg = f'确定要删除本地文件 "{data["name"]}" 吗？'
        dlg = MessageBox('确认删除', msg, self)
        dlg.yesButton.setText('删除')
        dlg.cancelButton.setText('取消')
        if not dlg.exec():
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
        """远程文件删除（二次确认；目录仅能删空目录）——P0-2：与工具栏删除共用确认口径"""
        if not self._confirm_delete_remote([entry]):
            return
        remote_path = self._remote_path.rstrip('/') + '/' + entry['name']
        op = 'rmdir' if entry['is_dir'] else 'delete'
        self._run_quick_op(op, remote_path, f'[SFTP] 删除: {remote_path}')

    def _ctx_new_file_local(self):
        """本地新建空文件"""
        name = self._ask_name('新建文件', '文件名:')
        if not name:
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
        """本地新建目录"""
        name = self._ask_name('新建文件夹', '文件夹名:')
        if not name:
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
        """远程新建空文件（走 create_file 任务）"""
        name = self._ask_name('新建文件', '文件名:')
        if not name:
            return
        remote_path = self._remote_path.rstrip('/') + '/' + name
        self._run_quick_op('create_file', remote_path, f'[SFTP] 创建远程文件: {remote_path}')

    def _ctx_new_dir_remote(self):
        """远程新建目录（走 mkdir 任务）"""
        name = self._ask_name('新建文件夹', '文件夹名:')
        if not name:
            return
        remote_path = self._remote_path.rstrip('/') + '/' + name
        self._run_quick_op('mkdir', remote_path, f'[SFTP] 创建远程目录: {remote_path}')

    # ------------------------------------------------------------------ 关闭
    def shutdown(self):
        """安全关闭所有连接和 worker。由容器（标签页关闭）或 QDialog.closeEvent 调用。

        可重复调用，幂等安全。
        """
        if self._closing:
            return
        self._closing = True
        # P2-2 步骤 1：关标签/退应用前先把未完成任务快照写盘（必须在下面清空
        # _queued_transfers 与释放 worker 之前，否则队列已经空了）
        pending_timer = getattr(self, '_pending_save_timer', None)
        if pending_timer is not None:
            pending_timer.stop()
        self._flush_pending_queue()
        # 停止健康检测定时器与待刷新定时器（P1-10）
        if hasattr(self, '_health_timer'):
            self._health_timer.stop()
        if hasattr(self, '_refresh_timer'):
            self._refresh_timer.stop()
        self._pending_refresh = set()
        # P2-1：排队任务不再启动（worker 未 start，仅置停标志后销毁）
        self._queued_transfers = []
        # 等待健康检测 worker 完成
        if self._health_worker and self._health_worker.isRunning():
            self._health_worker.wait(1000)
        transport = self._transport
        self._transport = None
        # 先置空引用掐断后续回调能看到的 transport，防止下面清理 worker 期间又被拿去操作
        self._cleanup_connect_worker()
        for tid in list(self._transfer_workers.keys()):
            info = self._transfer_workers.get(tid)
            if info and hasattr(info['worker'], 'stop'):
                info['worker'].stop()
            self._safe_delete_transfer_worker(tid)
        self._cleanup_list_worker()
        # transport 放最后关：等所有传输 worker 都收到 stop 后再断，
        # 否则在途传输往已关闭的通道写数据会连环抛 paramiko 异常
        safe_close_transport(transport)
        if transport:
            self._log('[SFTP] 已断开连接')


class SFTPWindow(QDialog):
    """SFTP 文件管理独立窗口（向后兼容的薄壳，内部委托 SFTPPanel）"""

    def __init__(self, host, port, username, password, server_name='', log_callback=None, parent=None):
        super().__init__(parent)
        apply_window_qss(self)
        title = f"SFTP 文件管理 - {server_name} ({host}:{port})" if server_name else f"SFTP 文件管理 - {host}:{port}"
        self.setWindowTitle(title)
        self.resize(1200, 800)
        self._panel = SFTPPanel(
            host, port, username, password,
            server_name=server_name,
            log_callback=log_callback, parent=self
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._panel)

    def closeEvent(self, event):
        self._panel.shutdown()
        super().closeEvent(event)

