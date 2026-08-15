# -*- coding: utf-8 -*-
"""SFTP 文件管理窗口"""

import os
import time
import shutil
import subprocess

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
    QWidget, QTreeWidgetItem,
    QHeaderView, QSplitter,
    QTableWidgetItem, QAbstractItemView, QApplication, QPushButton)
from PySide6.QtCore import QObject, QTimer, Qt, QEvent, QThread, Signal
from PySide6.QtGui import QShortcut, QKeySequence, QFont
from qfluentwidgets import (PushButton, BodyLabel, CaptionLabel, LineEdit,
    SearchLineEdit, setFont, TreeWidget, TableWidget, ProgressBar,
    RoundMenu, Action, FluentIcon, MenuAnimationType,
    MessageBox, MessageBoxBase)

from core.conn_logger import conn_logger
from core.perf import is_animation_enabled
from core.utils import safe_close_transport
from workers.network_workers import (
    SFTPConnectWorker, SFTPListWorker, SFTPOperationWorker, SFTPDirTransferWorker,
)

# 模块级强引用集合：防止窗口关闭后 Python GC 回收仍在运行的 QThread 导致崩溃
_pending_workers: set = set()


class _GlobalSignals(QObject):
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
        if os.path.commonpath([norm, vbase]) != vbase:
            return ''
        rel = os.path.relpath(norm, vbase)
        parts = [p for p in rel.split(os.sep) if p]
        return parts[0] if len(parts) > 1 else ''
    except Exception:
        return ''


def _cleanup_sftp_temp():
    """清理 _sftp_temp 临时目录中 mtime 超过 7 天的文件，异常静默"""
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
        try:
            import time as _time
            start = _time.time()
            session = self.transport.open_session()
            session.close()
            latency_ms = int((_time.time() - start) * 1000)
            self.result.emit(latency_ms)
        except Exception:
            self.result.emit(-1)


def _safe_release_worker(w):
    """将 worker 放入 pending 集合，线程结束后自动移除并 deleteLater"""
    _pending_workers.add(w)
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
        self._drag_source_row = -1
        self._closing = False
        self._health_worker = None
        self._init_ui()
        _cleanup_sftp_temp()  # 清理 _sftp_temp 中超过 7 天的旧临时文件
        QTimer.singleShot(100, self._connect_and_list)

    @property
    def tab_title(self) -> str:
        """返回适合标签页显示的标题"""
        if self._server_name:
            return f"SFTP - {self._server_name}"
        return f"SFTP - {self._host}:{self._port}"

    # ------------------------------------------------------------------ UI 构建
    def _init_ui(self):
        root = QVBoxLayout(self)
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
    
        self._build_local_panel()
        self._build_remote_panel()
    
        self._splitter.addWidget(self._left_panel)
        self._splitter.addWidget(self._right_panel)
        self._splitter.setStretchFactor(0, 1)  # 【左右比例】本地面板拉伸因子
        self._splitter.setStretchFactor(1, 1)  # 【左右比例】远程面板拉伸因子（1:1 等分）
    
        self._build_transfer_queue()
        self._build_button_bar(root)
        self._bind_shortcuts()
    
        # 预构建右键菜单（Action/图标/信号仅创建一次，后续右键零开销弹出）
        self._build_context_menus()
    
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
        self._btn_xftp = PushButton('Xftp')
        self._btn_xftp.clicked.connect(self._open_in_xftp)
        btn_row.addWidget(self._btn_xftp)
        btn_row.addStretch()
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
    
        # 修复误触发"上级"默认按钮，把本地目录推到上一级
        for _btn in self.findChildren(QPushButton):
            _btn.setAutoDefault(False)

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
        info = self._transfer_workers.pop(tid, None)
        if info:
            w = info['worker']
            if w.isRunning():
                _safe_release_worker(w)
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
        pending = self._pending_remote_path
        if pending is not None:
            self._pending_remote_path = None
            self._list_remote(pending)

    def _populate_remote(self, entries):
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
        # 清空搜索文本，恢复完整列表
        if self._local_search_edit.text():
            self._local_search_edit.clear()
        if self._remote_search_edit.text():
            self._remote_search_edit.clear()

    def _on_remote_search(self, text=''):
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

    # ------------------------------------------------------------------ 拖拽上传
    def eventFilter(self, obj, event):
        """远程文件列表拖放事件：从资源管理器拖入文件/目录即上传到当前远程目录"""
        if obj is self._tree:
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

        # 1. 保存源行所有数据
        col_count = table.columnCount()
        items_data = []
        for c in range(col_count):
            item = table.item(from_row, c)
            items_data.append(item.text() if item else '')
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
        table.setItem(to_row, 0, QTableWidgetItem(items_data[0]))
        pb = ProgressBar()
        pb.setRange(0, 100)
        pb.setValue(pb_value)
        table.setCellWidget(to_row, 1, pb)
        table.setItem(to_row, 2, QTableWidgetItem(items_data[2]))
        table.setItem(to_row, 3, QTableWidgetItem(items_data[3]))

        # 5. 重建所有行的 row 映射
        self._rebuild_transfer_row_map()

    def _rebuild_transfer_row_map(self):
        """根据文件名重建 _transfer_workers 中的 row 映射"""
        # 构建“文件名 -> tid”的映射
        name_to_tid = {}
        for tid, info in self._transfer_workers.items():
            worker = info.get('worker')
            if worker:
                fname = getattr(worker, '_filename', '') or getattr(worker, '_dir_name', '')
                if fname:
                    name_to_tid[fname] = tid
        # 遍历表格行，更新 row 映射
        for row in range(self._transfer_table.rowCount()):
            item = self._transfer_table.item(row, 0)
            if not item:
                continue
            text = item.text()
            for fname, tid in name_to_tid.items():
                if fname in text:
                    self._transfer_workers[tid]['row'] = row
                    break

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
        local_dir = data['path']
        dir_name = data['name']
        remote_dir = self._remote_path.rstrip('/') + '/' + dir_name
        self._log(f'[SFTP] 上传目录: {local_dir} -> {remote_dir}')
        worker = SFTPDirTransferWorker(self._conn_params, 'upload_dir',
                                       local_dir=local_dir, remote_dir=remote_dir, dir_name=dir_name)
        self._start_transfer_op(worker, f'[目录] {dir_name}', '上传', 0,
                                op='upload_dir', local_path=local_dir, remote_path=remote_dir)

    def _download_file(self, entry=None):
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
        tid = self._next_transfer_id
        self._next_transfer_id += 1
        row = self._transfer_table.rowCount()
        self._transfer_table.insertRow(row)
        self._transfer_table.setItem(row, 0, QTableWidgetItem(f'{op_label}: {filename}'))
        pb = ProgressBar()
        pb.setRange(0, 100)
        pb.setValue(0)
        self._transfer_table.setCellWidget(row, 1, pb)
        self._transfer_table.setItem(row, 2, QTableWidgetItem('0 B/s'))
        self._transfer_table.setItem(row, 3, QTableWidgetItem('传输中'))
        now = time.time()
        info = {'worker': worker, 'row': row, 'start_time': now,
                'last_bytes': 0, 'last_time': now, 'speed': 0.0,
                # 重试所需参数快照：(conn_params, op, local_path, remote_path)
                'params': (self._conn_params, op, local_path, remote_path)}
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
            # A1：下载成功后发射全局信号，联动主窗口
            params = info.get('params')
            if params and params[1] == 'download':
                local_path = params[2]
                if local_path:
                    GLOBAL_SIGNALS.file_downloaded.emit(
                        _videos_top_dir(local_path), local_path, 1)
        # 注意：不在此处删除 info，保留 params 供"打开所在文件夹"使用；
        # worker 线程已结束，行被删除/清空/关闭时统一释放
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
        # 保留 info（含 params）供右键"重试"使用，行删除时统一释放 worker
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
        self._act_t_retry = Action(FluentIcon.SYNC, '重试', self)
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

    def _transfer_retry_row(self, row):
        """失败任务一键重试：从 info['params'] 取参数重建 worker 发起传输"""
        tid = self._find_tid_by_row(row)
        if tid is None:
            return
        info = self._transfer_workers.get(tid)
        params = info.get('params') if info else None
        if not params or not params[1]:
            self._log('[SFTP] 该任务缺少重试参数，无法重试')
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
        status_item = self._transfer_table.item(row, 3)
        if status_item:
            status_item.setText('传输中')
        worker.progress.connect(lambda t, tot, _tid=tid: self._on_transfer_progress(_tid, t, tot))
        worker.success.connect(lambda msg, _tid=tid: self._on_transfer_success(_tid, msg))
        worker.error.connect(lambda err, _tid=tid: self._on_transfer_error(_tid, err))
        worker.start()
        name_item = self._transfer_table.item(row, 0)
        name = name_item.text() if name_item else f'任务{tid}'
        self._log(f'[SFTP] 重试传输: {name}')

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

    # ------------------------------------------------------------------ 删除 / 新建目录
    def _run_quick_op(self, op, remote_path, log_msg, local_path=''):
        """执行快速操作的通用模板（创建 worker → 注册 → 启动）"""
        self._log(log_msg)
        worker = SFTPOperationWorker(self._conn_params, op, local_path, remote_path)
        worker.success.connect(self._on_quick_op_success)
        worker.error.connect(self._on_quick_op_error)
        tid = self._next_transfer_id
        self._next_transfer_id += 1
        self._transfer_workers[tid] = {'worker': worker, 'row': -1, 'start_time': time.time()}
        worker.success.connect(lambda msg, _tid=tid: self._safe_delete_transfer_worker(_tid))
        worker.error.connect(lambda err, _tid=tid: self._safe_delete_transfer_worker(_tid))
        worker.start()

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
        self._run_quick_op(op, remote_path, f'[SFTP] 删除: {remote_path}')

    def _create_directory(self):
        name = self._ask_name('新建目录', '目录名:')
        if not name:
            return
        remote_path = self._remote_path.rstrip('/') + '/' + name
        self._run_quick_op('mkdir', remote_path, f'[SFTP] 创建目录: {remote_path}')

    def _open_in_xftp(self):
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
    def _on_quick_op_success(self, msg):
        self._lbl_status.setText(msg)
        self._log(f'[SFTP] {msg}')
        self._list_remote(self._remote_path)

    def _on_quick_op_error(self, error):
        self._lbl_status.setText(f'操作失败: {error}')
        self._log(f'[SFTP] 操作失败: {error}')

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
        try:
            os.startfile(path)
            self._log(f'[SFTP] 已打开: {path}')
        except OSError as e:
            self._log(f'[SFTP] 打开失败: {e}')

    def _ctx_rename_local(self, data):
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
        new_name = self._ask_name('重命名', '新名称:', entry['name'])
        if not new_name or new_name == entry['name']:
            return
        old_path = self._remote_path.rstrip('/') + '/' + entry['name']
        new_path = self._remote_path.rstrip('/') + '/' + new_name
        self._run_quick_op('rename', new_path,
                           f'[SFTP] 重命名: {old_path} -> {new_path}',
                           local_path=old_path)

    def _ctx_delete_local(self, data):
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
        if entry['is_dir']:
            msg = f'确定要删除远程目录 "{entry["name"]}" 吗？\n注意：仅能删除空目录。'
        else:
            msg = f'确定要删除远程文件 "{entry["name"]}" 吗？'
        dlg = MessageBox('确认删除', msg, self)
        dlg.yesButton.setText('删除')
        dlg.cancelButton.setText('取消')
        if not dlg.exec():
            return
        remote_path = self._remote_path.rstrip('/') + '/' + entry['name']
        op = 'rmdir' if entry['is_dir'] else 'delete'
        self._run_quick_op(op, remote_path, f'[SFTP] 删除: {remote_path}')

    def _ctx_new_file_local(self):
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
        name = self._ask_name('新建文件', '文件名:')
        if not name:
            return
        remote_path = self._remote_path.rstrip('/') + '/' + name
        self._run_quick_op('create_file', remote_path, f'[SFTP] 创建远程文件: {remote_path}')

    def _ctx_new_dir_remote(self):
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
        # 停止健康检测定时器
        if hasattr(self, '_health_timer'):
            self._health_timer.stop()
        # 等待健康检测 worker 完成
        if self._health_worker and self._health_worker.isRunning():
            self._health_worker.wait(1000)
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


class SFTPWindow(QDialog):
    """SFTP 文件管理独立窗口（向后兼容的薄壳，内部委托 SFTPPanel）"""

    def __init__(self, host, port, username, password, server_name='', log_callback=None, parent=None):
        super().__init__(parent)
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

