# -*- coding: utf-8 -*-
"""SFTP 双栏文件管理窗口：本地/远程面板 + 传输队列。

三块内容各有独立的失败面，结构上刻意保持松散耦合：

- 连接：transport 由 SFTPPanel 独占，各 worker 只拿主机参数自建通道。一条
  SFTP 通道严格一问一答，两个线程共享会让响应错配（见 _HealthCheckWorker）。
- 传输：一任务一 worker 一行表格，并发数受设置项闸门限制；否则一次多选
  几十个文件就等于开几十条 SSH 连接，会被服务端 MaxStartups 拒连。
- 恢复：未完成任务快照写进设置文件，同目标下次连接时询问是否重传。

窗口外壳 SFTPWindow 只是薄壳，可嵌入标签页的实体是 SFTPPanel。
"""

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
    """返回下载落点在 videos 目录下的第一级子目录名；不在 videos 下返回空字符串

    主窗口收到 GLOBAL_SIGNALS.file_downloaded 后靠它把文件挂到对应设备的视频
    列表；返空串表示这次下载与设备目录无关，接收方应忽略。
    """
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
        # 按年龄而非扩展名/白名单清理：_sftp_temp 只存下载与预览的中转文件，
        # 正在写的文件 mtime 是当下，不会被误判成过期
        cutoff = time.time() - 7 * 24 * 3600
        # 逐个删并各自吞异常：某个文件被占用或正被预览进程锁定，不该让整轮清理中止
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
        """比较两个条目：目录恒排在文件前，同类再按当前排序列取原始值比较"""
        tree = self.treeWidget()
        col = tree.sortColumn() if tree is not None else 0
        if tree is not None:
            _hdr = tree.header()
            ascending = (_hdr is None or
                         _hdr.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder)
        else:
            ascending = True
        # 排序键一律取自 UserRole 里的原始 entry：显示文本 "9.5 MB"/"目录"
        # 反推不出数值与类型，按文本比较会让 10 MB 排到 9.5 MB 前面
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
        # 名字列忽略大小写；其余列（类型/权限/时间）直接按文本比即可，
        # 这些列本身是定长格式或字典序，转换反而多一次分配
        if col == 0:
            a = self.text(0).lower()
            b = other.text(0).lower()
            if a != b:
                return a < b
        return self.text(col) < other.text(col)

# ------------------------------------------------------------------ 文件类型图标映射
# 缓存键用 FluentIcon 枚举而非扩展名：几十种扩展名共用同一个图标，
# 按扩展名缓存只会重复构造同一个 engine
_ICON_CACHE: dict = {}

# 扩展名 → FluentIcon 映射。未收录的类型回退 DOCUMENT：宁可图标不精确，
# 也不要空白占位，用户识别文件主要靠文件名
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
    """后台 SFTP 连接健康检测

    定时器每 5s 触发一次，只测"开一个 session 再关掉"的往返耗时；异常统一
    回落成 -1，调用方据此切换指示器颜色，不必再区分异常类型。
    """
    result = Signal(int)  # latency_ms, -1 表示异常

    def __init__(self, transport):
        """只存 transport 引用，会话留到 run() 里开：构造要足够快，不能拖慢面板初始化"""
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
    """传输队列专用表格：纯鼠标事件实现拖拽调序，完全不用 Qt 内置 drag-drop。

    InternalMove 在模型层先删行再插行，进度条 cellWidget 与行号→tid 映射会
    一起丢失；自己搬数据虽然啰嗦，但能精确控制保留什么（见 _move_transfer_row）。
    """

    def __init__(self, parent=None):
        """_panel 先置空、由 SFTPPanel 注入；_drag_row 为 -1 表示当前没有拖拽"""
        super().__init__(parent)
        self._panel = None  # 由 SFTPPanel 设置反向引用
        self._drag_row = -1
        self._press_pos = None

    def mousePressEvent(self, event):
        """记下按下的行与坐标，供 mouseMove 判断是否越过拖拽阈值"""
        if event.button() == Qt.MouseButton.LeftButton:
            row = self.rowAt(event.position().toPoint().y())
            if row >= 0:
                self._drag_row = row
                self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """越过 10px 阈值才算拖拽：小于阈值视为手抖，仍走 Qt 默认的选中逻辑"""
        # 检测到拖拽意图：切换光标提示可拖拽
        if self._press_pos is not None and self._drag_row >= 0:
            if (event.position().toPoint() - self._press_pos).manhattanLength() > 10:
                self.viewport().setCursor(Qt.CursorShape.SizeVerCursor)
                return  # 不调用 super，阻止 Qt 启动内置 drag
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """松手即落位：目标行取自松手坐标，拖到空白处按末行处理"""
        # 拖到行外（列表下方空白）时 rowAt 返回 -1，按「拖到末尾」理解更符合直觉，
        # 否则用户很难精确对齐最后一行
        if event.button() == Qt.MouseButton.LeftButton and self._drag_row >= 0:
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            target_row = self.rowAt(event.position().toPoint().y())
            if target_row < 0:
                target_row = self.rowCount() - 1
            if target_row >= 0 and target_row != self._drag_row and self._panel:
                self._panel._move_transfer_row(self._drag_row, target_row)
            # 无论移动是否发生都要复位：残留的 _drag_row 会让下一次按下带着旧源行
            self._drag_row = -1
            self._press_pos = None
        super().mouseReleaseEvent(event)


class _TextInputDialog(MessageBoxBase):
    """Fluent 风格文本输入对话框，替代 QInputDialog.getText

    QInputDialog 是系统原生控件，不跟随 qfluentwidgets 的字体与深色主题，
    在亚克力背景下画风割裂；代价是 1.11+ 的 MessageBoxBase 不再代建标题，
    控件得自己拼进 viewLayout。
    """

    def __init__(self, title, label, default='', parent=None):
        """title 是对话框标题，label 是输入框上方的说明，default 会被全选。

        有默认值时 selectAll：重命名场景下用户几乎总是整体替换而非插入，
        光标停在末尾再让用户手动全选是多一步。"""
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

    资源清理统一由 shutdown() 负责，容器关闭标签时调用；任何绕过它的销毁
    路径都会让后台 worker 在控件析构后继续回调，PySide6 直接 qFatal 退出。

    transport 由本面板独占，worker 只拿主机参数另开通道，不共享连接对象。
    """

    # P1-1：请求在当前远程目录打开终端标签（参数=远程目录路径），
    # 由创建方（remote_mixin / frp_remote）连接后用同目标参数建 SSHTerminalPanel
    open_terminal_here = Signal(str)

    def __init__(self, host, port, username, password, server_name='', log_callback=None, default_remote_path=None, default_local_path=None, parent=None):
        """建面板但不立即连接：真正的连接延到 QTimer.singleShot(100) 之后。

        default_remote_path 缺省进 /home 而非 /，是因为普通账号对 / 大多没有读权限，
        首屏必然报错；default_local_path 供 snk 会话按球桌号预建落点目录，
        log_callback 由容器注入，不传则丢弃日志（独立使用时无日志面板可写）。"""
        super().__init__(parent)
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._server_name = server_name
        self._conn_params = (host, port, username, password)
        # transport 归面板独占，只用于列目录与健康检测；传输类 worker 一律
        # 拿 _conn_params 自建通道，一条 SFTP 通道不能并发共享
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
        # 列目录是短任务且可能被快速连点：代际号用来丢弃迟到的旧结果，
        # 互斥只能保证不并发，不能保证结果对应当前路径
        self._list_generation = 0
        self._listing = False
        # 列目录进行中用户又点了别的目录，先记这里，等当前一轮回调后再补跳
        self._pending_remote_path = None
        # tid → info 字典（worker/row/state/params/速度采样）。表格行与 tid 是
        # 双向映射，增删行和拖拽调序都必须同步维护，故有 _rebuild_transfer_row_map
        self._transfer_workers = {}
        self._next_transfer_id = 0
        # P2-1：批量传输并发闸门——运行中（含暂停）任务数达上限后新任务排队
        # （状态列"排队中"），有空位按 FIFO 启动；避免多选 N 个文件 = N 条并发
        # SSH 连接（服务端 MaxStartups 拒连 → 部分文件莫名失败 + auth 日志被刷）
        self._max_concurrent_transfers = self._load_max_concurrent_transfers()
        # FIFO 而非优先级队列：用户多选上传时先点先传更符合直觉，
        # 按文件大小排序反而会让小文件不断插队、大文件饿死
        self._queued_transfers = []  # 排队 tid 的 FIFO
        self._drag_source_row = -1
        # 标签容器与窗口都可能各调一次关闭流程，_closing 兼作幂等标记
        self._closing = False
        # 健康检测 worker 每次新建、用完即弃：复用会让上一轮的异常结果
        # 污染下一轮判定，也会让"线程已结束但对象还在"的状态难以分辨
        self._health_worker = None
        # P1-10：传输完成后的按需刷新（400ms 去抖合并，只在任务目标目录
        # == 当前浏览目录时刷新，不再打断用户正在翻的目录/搜索）
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(400)
        self._refresh_timer.timeout.connect(self._flush_pending_refresh)
        # 攒的是侧名（remote/local）而不是具体目录：去抖到期时才取当时的当前
        # 目录，避免这 400ms 里用户又翻走导致刷新错目录
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
        # 延迟 100ms 再连：等容器把标签页布局做完，否则连接失败的重连条会按
        # 未定尺寸的父窗口布局，位置跑到界面角落
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
        # 上界不钳：并发数由用户自担；下界必须 ≥1，否则闸门永远不满足，
        # 所有任务停在"排队中"却没有任何代码去启动它们
        return val if val >= 1 else 3

    def _active_transfer_count(self) -> int:
        """占用并发槽位的任务数（运行中+暂停中：暂停仍持有线程与连接）"""
        # 只数 running/paused：排队任务还没占线程，若也算进来队列永远排不空；
        # 但暂停的必须算，它的 worker 线程与 SSH 连接都还挂着
        return sum(1 for info in self._transfer_workers.values()
                   if isinstance(info, dict)
                   and info.get('state') in ('running', 'paused'))

    def _launch_or_queue_transfer(self, tid):
        """P2-1：有空位立即启动（状态列"传输中"），否则排队（"排队中"）"""
        info = self._transfer_workers.get(tid)
        if info is None:
            return
        # 状态列固定第 3 列；行号会因拖拽调序而变，故每次现取而不是缓存 item
        status_item = self._transfer_table.item(info.get('row', -1), 3)
        if self._active_transfer_count() >= self._max_concurrent_transfers:
            info['state'] = 'queued'
            # 去重保护：同一 tid 可能被重复唤醒，例如恢复队列后又点了"重新开始"
            if tid not in self._queued_transfers:
                self._queued_transfers.append(tid)
            if status_item:
                status_item.setText('排队中')
            # P2-2：排队态也要落盘（否则退出即丢）
            self._schedule_pending_save()
            return
        info['state'] = 'running'
        # 速度采样起点：worker 只汇报累计字节，速率靠相邻两次时间戳之差算，
        # 起点缺失会让首个速度值大得离谱
        info['start_time'] = time.time()
        info['last_time'] = time.time()
        if status_item:
            status_item.setText('传输中')
        # 状态与计数先落到 running 再 start()：worker 的首条进度信号会回读并发统计，
        # 顺序反了会把占用的并发位算少，泵队列随即多放一个任务进来
        info['worker'].start()
        self._schedule_pending_save()

    def _pump_transfer_queue(self):
        """P2-1：任务结束/删除后排空队列（FIFO 启动直到占满并发位）"""
        while (self._queued_transfers
               and self._active_transfer_count() < self._max_concurrent_transfers):
            tid = self._queued_transfers.pop(0)
            info = self._transfer_workers.get(tid)
            if info is None or info.get('state') != 'queued':
                # 行已被删除或已在别处启动：队列里留着的是过期引用，直接丢弃
                continue
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
            # 老版本写下的记录可能带已废弃的 op，恢复时无从重建 worker，
            # 这里先滤掉，免得恢复阶段再逐条判断
            if op not in _tq.VALID_OPS or not local_path or not remote_path:
                continue
            name_item = self._transfer_table.item(info.get('row', -1), 0)
            name = name_item.text() if name_item else ''
            # 只有上传能从本地盘现取字节数；下载时远端大小未知，留 0 让进度条
            # 按"总量未知"渲染，而不是显示一个假的 0%
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
        # 关闭流程里不再起定时器：shutdown 已同步刷过一次盘，再排队写只会
        # 在控件析构后触发回调
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
        """把整表写回设置文件；失败只返回 False，不抛异常打断传输。

        设置文件是普通 JSON，磁盘满或权限被改都会失败；丢一份恢复快照
        不影响当前会话，没必要为它中断用户正在进行的传输。
        """
        try:
            from core import app_settings
            app_settings.set(_tq.QUEUE_KEY, store)
            return True
        except Exception:
            return False

    def _flush_pending_queue(self):
        """立即把未完成任务快照写进设置文件（去抖超时 / shutdown 调用）"""
        # 每台连过的服务器都会留一条目标记录，不设上限设置文件会无限膨胀；
        # 50 台之外按最久未用淘汰，被淘汰的目标最多丢一次恢复提示
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
            # 记录里有展示名就沿用，老记录缺 name 时再从路径反推，保证行标题不留空
            name = rec.get('name') or os.path.basename(local_path or remote_path)
            # 源文件可能在断线期间被移走或删除：直接跳过并留一条日志，
            # 比让 worker 一启动就失败更容易排查
            if op in ('upload', 'upload_dir') and not os.path.exists(local_path):
                self._log(f'[SFTP] 跳过已不存在的本地路径: {local_path}')
                continue
            # 目录类走 SFTPDirTransferWorker（local_dir/remote_dir 关键字参数），
            # 文件类走 SFTPOperationWorker（位置参数 local/remote），两者签名不通用，
            # 所以这里必须按 op 分四种情况重建，不能合并
            if op in ('upload', 'upload_dir'):
                if op == 'upload_dir':
                    worker = SFTPDirTransferWorker(
                        self._conn_params, op, local_dir=local_path, remote_dir=remote_path,
                        dir_name=os.path.basename(local_path.rstrip('/\\')))
                    # 目录总量要遍历完才知道，进度条改按已传文件数与当前文件进度推进
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
        # 返回实际入队数（跳过的不算）：调用方据此决定恢复提示里写不写「部分任务已跳过」
        return started

    def _offer_pending_restore(self):
        """连接成功后：若本目标有上次未完成的传输，询问是否恢复（P2-2 步骤 1）

        无论选"恢复"还是"忽略"都会消费掉快照：同一个提示不会每次重连都弹。
        """
        # 重连风暴里 _on_sftp_connect_success 可能连续触发，用一次性标记
        # 压住重复弹窗，否则用户会看到叠起来的恢复询问
        if getattr(self, '_pending_restore_offered', False):
            return
        # 标记先于读快照置位：读取抛异常时也不该在下次重连再弹一次同样的框
        self._pending_restore_offered = True
        records = _tq.load_target(self._load_pending_store(), self._host, self._port)
        if not records:
            return
        self._clear_pending_queue()
        # 快照已被消费，即使窗口正在关闭也不再回写：半关闭状态下弹模态框
        # 会挂在没有父窗口的事件循环里，进程退不干净
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
        """搭建双栏文件面板 + 传输队列 + 按钮栏，预构建右键菜单

        顺序有依赖：按钮栏内部会把垂直 Splitter 挂到 root，而 Splitter 需要先
        拿到左右两个面板，所以 _build_transfer_queue 必须排在它前面。
        """
        root = QVBoxLayout(self)
        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        self._build_local_panel()
        self._build_remote_panel()

        # 先 addWidget 再设 stretch：QSplitter 只对已加入的索引生效，
        # 反过来设会被静默忽略，表现为两侧宽度不随窗口变化
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
        # 橙色半透明底 + 实心描边：断线是「还能救」的异常态，用错误红会让人以为
        # 会话已经废了；与 SSH 终端页保持同一视觉语言，用户不必重新学一遍
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
        # stretch 把按钮顶到最右：提示文字长度随错误原因变化，不用 stretch 时
        # 按钮会紧贴文字左右横跳
        bar_layout.addStretch()
        self._reconnect_btn = PrimaryPushButton('重新连接')
        # 不抢焦点：断线条只是提示，抢走焦点会打断用户正在路径栏里的输入
        self._reconnect_btn.setFocusPolicy(Qt.NoFocus)
        self._reconnect_btn.setFixedWidth(96)
        self._reconnect_btn.clicked.connect(self._reconnect_clicked)
        bar_layout.addWidget(self._reconnect_btn)
        # 默认隐藏而不是禁用：断线条占一整行高度，常驻会在正常使用时白占空间
        self._reconnect_bar.hide()
        root.addWidget(self._reconnect_bar)
    
    def _build_local_panel(self):
        """构建本地面板（树控件、路径栏、搜索框）

        控件一律挂到 self 上而不是留局部变量：刷新、搜索、右键菜单都要回头
        访问它们，按属性名取也比层层 findChild 稳定。
        """
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
        # ExtendedSelection 是批量上传的前提：单选模式下用户每传一个文件
        # 都得重新点一次，多选几十个时体验无法接受
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
        # 指示器必须在填充数据前设定：_populate 会先读当前排序再恢复，
        # 没有初始方向时首次进目录可能按插入顺序而非文件名排序
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
        # 搜索框默认隐藏：常驻会永久压掉一行文件列表高度，只在 Ctrl+F 时展开
        left_lay.addWidget(self._local_search_frame)
        self._local_search_frame.hide()
    
    def _build_remote_panel(self):
        """构建远程面板（树控件、路径栏、搜索框）

        结构与本地面板刻意保持同构，两边按钮位置一致，用户不必重新找；
        差异只有权限列与拖放上传，都是远端才需要的操作。
        """
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
        # 路径加粗：两栏布局里路径是唯一能区分「我在看哪台机器哪个目录」的信息，
        # 与下面的文件名同字号不加粗时很容易被当成可编辑的普通输入框略过
        setFont(self._edit_remote_path, weight=QFont.Weight.Bold)
        self._edit_remote_path.returnPressed.connect(self._on_remote_path_entered)
        # stretch=1 让输入框吃掉整行剩余宽度：否则路径一长，刷新按钮会被挤出可视区
        right_bar.addWidget(self._edit_remote_path, 1)
        self._btn_refresh = PushButton('刷新')
        self._btn_refresh.clicked.connect(self._refresh)
        right_bar.addWidget(self._btn_refresh)
        right_lay.addLayout(right_bar)
    
        self._tree = TreeWidget()
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # 比本地面板多一列"权限"：远程排障第一眼就是看 644/755；本地权限
        # 在资源管理器里更直观，放进列表只会挤占文件名宽度
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
        # 过滤器只装到树控件，不装整个面板：路径栏与搜索框也要接收拖入的文本，
        # 面板级过滤会把它们的原生拖放行为一起吞掉
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
        """构建传输队列表格 + 垂直 Splitter

        队列表与文件列表用 Splitter 而非固定高度：两者都要看，谁多谁少
        取决于当前任务，交给用户自己拖。

        root.addWidget 不在这里做——按钮栏要排在队列下方，由 _init_ui 统一挂载。
        """
        # 反向引用要先于任何行操作设好：_TransferTable 的鼠标处理器随时可能
        # 被触发，拿到 None 会在拖拽调序时直接抛 AttributeError
        self._transfer_table = _TransferTable()
        self._transfer_table._panel = self
        self._transfer_table.setColumnCount(4)
        self._transfer_table.setRowCount(0)
        self._transfer_table.setHorizontalHeaderLabels(['文件名', '进度', '速度', '状态'])
        hdr = self._transfer_table.horizontalHeader()
        for c in range(3):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        # 状态列吃掉剩余宽度：它承载"排队中/失败原因"这类变长文本，
        # 定宽会把关键信息截断成省略号
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        # 【列宽】传输队列各列初始宽度（px）
        hdr.resizeSection(0, 280)   # 文件名
        hdr.resizeSection(1, 180)   # 进度
        hdr.resizeSection(2, 100)   # 速度
        # 关横向滚动条：前三列内容宽度固定，出现滚动条只说明窗口过窄，
        # 状态列拉伸后不需要横向拖动
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
        # 4:1 而非等分：拖文件时视线在表格上，队列露出三四行就够看进度
        self._v_splitter.setStretchFactor(0, 4)  # 文件区域占大部分
        self._v_splitter.setStretchFactor(1, 1)  # 传输队列占小部分
        self._v_splitter.setSizes([400, 130])    # 初始比例
        # 注意：root.addWidget 由 _init_ui 在 _build_button_bar 之后统一处理
    
    def _build_button_bar(self, root):
        """构建操作按钮栏 + 健康检测定时器，挂到 root 布局

        先把垂直 Splitter 挂进 root 再挂按钮行：QVBoxLayout 按加入顺序自上
        而下排列，顺序反了按钮就会跑到文件列表上方。
        """
        # 先把 v_splitter 挂到 root
        root.addWidget(self._v_splitter, 1)
    
        # ---- 操作按钮栏
        btn_row = QHBoxLayout()
        # 箭头方向表示数据流向（本地→远程），比只写"上传"少一次思考；
        # 下载按钮用反方向箭头，两个按钮并排时一眼能分清
        # 直接连槽、不包 lambda：clicked 把 checked(False) 传给 _upload_file，入口里
        # isinstance(dict) 判定后落到「批量上传选中项」分支，正是按钮要的行为
        self._btn_upload = PushButton('上传 ▶')
        self._btn_upload.clicked.connect(self._upload_file)
        btn_row.addWidget(self._btn_upload)
        # 同上：无参点击 = 下载右侧所有选中项，单条下载走右键菜单传 entry
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
        # 用文本圆点而非三张状态图标：切颜色只改 stylesheet，不必为红/橙/绿各备一份资源
        self._lbl_health = BodyLabel('●')
        self._lbl_health.setStyleSheet('color: gray; font-size: 14px;')
        self._lbl_health.setToolTip('连接状态: 未连接')
        btn_row.addWidget(self._lbl_health)
        self._lbl_status = BodyLabel('就绪')
        btn_row.addWidget(self._lbl_status)
        root.addLayout(btn_row)
    
        # 连接健康检测定时器（每 5s 检测一次）
        self._health_timer = QTimer(self)
        # 5s 一次：更密会让每次检测都新开 SSH 会话，在慢链路上本身就成了负担；
        # 更疏则断线后用户要等很久才看到红点
        self._health_timer.setInterval(5000)
        self._health_timer.timeout.connect(self._check_connection_health)
        self._health_timer.start()
    
    def _bind_shortcuts(self):
        """绑定快捷键 + 修复默认按钮 + 预构建右键菜单"""
        # ---- Ctrl+F 快捷键
        # QShortcut 的父对象传 self（面板）而非窗口：面板嵌在标签页里时，
        # 快捷键只在当前标签激活时生效，不会抢别的标签的 Ctrl+F
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
        # 先刷本地再发起网络连接：本地列目录不依赖网络，能立刻填满左栏，
        # 用户不必对着两个空白面板等握手
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
        # 释放放最后：恢复流程会复用同一个 _log 与状态栏，提前销毁 worker 虽不
        # 影响正确性，但会把"已连接"日志挤在清理动作之后，读起来错位
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
        # 每次都复位按钮状态：上一次重连可能失败并留下了"重连中..."的禁用态，
        # 不复位就再也没有入口可点
        self._reconnect_btn.setEnabled(True)
        self._reconnect_btn.setText('重新连接')
        self._reconnect_bar.show()

    def _reconnect_clicked(self):
        """一键重连：清理已失效的 transport，复用现有连接路径重建"""
        if self._closing:
            return
        # 先禁用再重连：连接失败会立刻回调 _show_reconnect_bar 复位，
        # 中间这段时间按钮必须挡住重复点击，否则会并发起多条连接
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
            # 关闭失败也照样置空：残留的半死 transport 比 None 更危险，
            # 后续 is_active() 可能仍返回 True，让健康检测误判在线
            self._transport = None

    def _retry_failed_transfers(self):
        """重连成功后批量重试队列表中状态为失败的任务（P1-3）"""
        retried = 0
        for row in range(self._transfer_table.rowCount()):
            status_item = self._transfer_table.item(row, 3)
            # 按状态列文本前缀判定而非 info['state']：失败原因可能来自 worker
            # 内部异常，未必走到 state 更新，文本是唯一必定落地的痕迹
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
            # abort 只对实现了它的 worker 有效，用 hasattr 兜底：
            # 没有中止能力的 worker 只能等它自己跑完再释放
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
                # 上一个还没完成就跳过本轮：健康检测是采样，不是排队任务，
                # 堆起来只会让弱网下的会话数越滚越多
                return  # 上一个还没完成，跳过本次

        worker = _HealthCheckWorker(self._transport)
        worker.result.connect(self._on_health_check_result)
        self._health_worker = worker
        worker.start()

    def _on_health_check_result(self, latency_ms):
        """健康检测结果回调"""
        # 负数是 worker 约定的失败哨兵（探测超时或通道已断），不是「极快」，
        # 必须排在下面的区间判断之前处理
        if latency_ms < 0:
            self._set_health_indicator('red', '连接异常')
        # 200ms 内算健康、1s 内算勉强可用：跨运营商链路常在 100-300ms 之间，
        # 阈值定太严会让正常连接一直显示黄色，指示器失去意义
        elif latency_ms < 200:
            self._set_health_indicator('green', f'已连接 ({latency_ms}ms)')
        elif latency_ms < 1000:
            self._set_health_indicator('orange', f'延迟较高 ({latency_ms}ms)')
        else:
            self._set_health_indicator('red', f'延迟过高 ({latency_ms}ms)')
        self._health_worker = None

    def _set_health_indicator(self, color, tooltip):
        """设置健康指示器颜色和提示"""
        # 写内联样式而不是 setObjectName+QSS：圆点颜色随延迟秒级变化，
        # 每变一次都触发样式重算，比直接改一条属性贵得多
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
                # 先断信号再释放：worker 可能正在发结果，留着连接会让回调打到
                # 即将销毁的表上，表现为随机空白或崩溃
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
            # 还在跑就交给全局强引用集合等它收尾（直接 deleteLater 会 qFatal），
            # 同时立刻解锁 _listing：旧 worker 已放弃，不解锁后续列目录会一直被压成待办
            if w.isRunning():
                _safe_release_worker(w)
                self._listing = False
            else:
                w.deleteLater()

    def _safe_delete_transfer_worker(self, tid):
        """从传输表移除指定任务：运行中挂强引用等自行结束，否则直接销毁"""
        # 先从映射里摘掉再销毁：销毁过程会触发 finished 回调，
        # 那些回调若还能查到 tid，就会去操作已不存在的表格行
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
        # 列目录必须串行：同一 transport 上并发两次 listdir 会让响应错配，
        # 拿到的可能是另一个目录的内容；进行中只记最后一个目标，回调后再补跳
        if self._listing:
            self._pending_remote_path = path
            # 状态栏写明「等待加载」而不是静默忽略：用户双击目录后界面毫无反应会以为点空了，
            # 事实上请求已排队，回调结束后会立刻补跳
            self._lbl_status.setText(f'等待加载: {path}')
            return
        # 先释放上一个 worker 再起新的：两者共用同一 transport，
        # 上一轮没读完的请求会和新请求抢同一个通道
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
        # 目录以回调带回的 path 为准而不是发起请求时的入参：服务端可能把 '/home/' 规范化
        # 成 '/home'，用入参会让路径栏显示的值与实际列出的目录逐渐漂移
        self._remote_path = path
        self._remote_entries = entries
        self._edit_remote_path.setText(path)
        self._populate_remote(entries)
        # 目录切换后清空远程搜索框
        # 清空搜索框必须屏蔽 textChanged：否则 clear() 会回调 _on_remote_search，
        # 拿旧条目再填一次表，把刚刷好的新目录覆盖掉
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
        # 失败也要放开门闩：_listing 残留为 True 的话，后续每次列目录都会被当成重入而丢弃，
        # 界面永久停在「等待加载」
        self._listing = False
        self._lbl_status.setText(f'列表失败: {error}')
        self._log(f'[SFTP] 列表失败: {error}')
        # 保底机制：路径不存在时自动回到根目录（仅重试一次，避免无限循环）
        err_text = str(error)
        # 三种文案都要匹配：paramiko 直译的英文、服务端返回的中文、以及 OSError
        # 的 strerror，命中哪一种取决于服务器实现与版本，只认一种就会漏
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
            # 目录在前靠插入顺序，此时排序是关的、__lt__ 不参与；恢复排序后才由
            # _SortableTreeItem.__lt__ 继续保证目录优先，两处都不能省
            for entry in dirs + files:
                item = _SortableTreeItem()
                item.setIcon(0, _file_icon(entry['name'], entry['is_dir']))
                item.setText(0, entry['name'])
                item.setText(1, self._format_size(entry['size']) if not entry['is_dir'] else '')
                item.setText(2, '目录' if entry['is_dir'] else '文件')
                item.setText(3, entry['perm'])
                item.setText(4, entry['mtime'])
                # 原始 entry 挂到 UserRole：排序要按 size 数值比较，右键菜单也要
                # 靠它取 is_dir/perm，从显示文本反推既慢又不可靠
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
        # 焦点可能不在任何一侧（例如刚点过按钮栏），此时默认给远程侧：
        # 搜远程文件是更高频的操作，猜错的代价也更小
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
        # 收起时必须连关键词一起清掉：不清的话下次 Ctrl+F 打开仍是过滤后的
        # 残表，用户会以为文件丢了
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
        # 只过滤已缓存的条目：每次按键都重列远端目录会把 SSH 通道打满，
        # 远端目录内容在两次刷新之间不会因本地搜索而变化
        kw = keyword.lower()
        # 两侧都转小写：远端文件名大小写敏感，但用户敲搜索词时不会在意大小写
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
        # 与远程侧同一策略：清空输入即从 _local_entries 全量重建，
        # 所以过滤结果不需要合并回缓存，缓存始终保留未过滤的目录快照
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
            # 本地排序统一转小写，贴合 Windows 资源管理器的大小写不敏感习惯；
            # 远程侧保留原样按字节序，因为 Linux 文件名区分大小写
            dirs = sorted([e for e in entries if e['is_dir']], key=lambda x: x['name'].lower())
            files = sorted([e for e in entries if not e['is_dir']], key=lambda x: x['name'].lower())
            for entry in dirs + files:
                item = _SortableTreeItem()
                item.setIcon(0, _file_icon(entry['name'], entry['is_dir']))
                item.setText(0, entry['name'])
                # 目录大小留空而不是显示 0：目录的真实体积要递归统计，写 0 会被
                # 当成空目录，留空至少不误导
                item.setText(1, self._format_size(entry['size']) if not entry['is_dir'] else '')
                item.setText(2, '目录' if entry['is_dir'] else '文件')
                item.setText(3, entry['mtime'])
                # 整条 entry 挂在 UserRole 上：双击/右键都要 path 与 is_dir，
                # 只存名字的话每处调用都得重新拼路径并再 stat 一次
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
        """回车跳转本地目录；目标与当前目录相同则直接返回。

        用 normcase 比较而非字符串相等：Windows 路径大小写不敏感，
        C:\\Users 与 c:\\users 指向同一处，重列只会让列表闪一下并丢选中。
        """
        path = self._edit_local_path.text().strip()
        if os.path.normcase(os.path.normpath(path)) == os.path.normcase(os.path.normpath(self._local_path)):
            return
        if os.path.isdir(path):
            self._list_local(path)
        else:
            self._lbl_status.setText(f'本地路径不存在: {path}')

    def _on_remote_path_entered(self):
        """回车跳转远程目录；空串直接忽略，避免把用户带到当前目录的父级。"""
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
        # 先清树再扫盘：扫描失败时列表不能还留着上一个目录的条目，否则用户会对着
        # 已不属于当前路径的文件做删除/重命名
        self._local_tree.clear()
        # 目录可能在扫描前被删或改了权限（网络盘尤其常见），异常收在这里
        # 只提示不抛，否则一次失败的跳转会打断整个面板
        try:
            with os.scandir(path) as it:
                entries = list(it)
        except Exception as e:
            self._lbl_status.setText(f'读取本地目录失败: {e}')
            return
        # _local_entries 保留全量条目：搜索是在这份内存副本上过滤的，
        # 只存过滤后的结果会让用户清空关键词后再也回不到完整列表
        # 先清空再填：上一次目录的条目若残留，搜索会捞到已不在当前目录的文件
        self._local_entries = []
        from datetime import datetime
        # 目录在前、文件在后，组内按小写名排序；与远程侧策略一致（本地大小写不敏感）
        dirs = sorted([e for e in entries if e.is_dir()], key=lambda x: x.name.lower())
        files = sorted([e for e in entries if e.is_file()], key=lambda x: x.name.lower())
        for entry in dirs + files:
            # 单个条目 stat 失败（断裂的符号链接、刚被删除的文件）只跳过它，
            # 不能让一个坏条目毁掉整个目录的展示
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
        """重列当前本地目录；路径不变，用于外部程序改动目录后的手动同步。"""
        self._list_local(self._local_path)

    def _local_go_up(self):
        """本地上跳一级；已在盘符根时什么也不做。"""
        parent = os.path.dirname(self._local_path)
        # 根目录的 dirname 返回它自己，少了这层判断会变成反复重列同一目录
        if parent != self._local_path:
            self._list_local(parent)

    def _on_local_item_double_clicked(self, item, column):
        """本地双击：目录进入，文件直接上传。

        与资源管理器双击即打开的习惯对齐，这里的「打开」等价于把文件送到
        远端当前目录，省掉先选中再点上传的两步。
        """
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        if data['is_dir']:
            self._list_local(data['path'])
        else:
            self._upload_file(data)

    # ------------------------------------------------------------------ 远程导航
    def _refresh(self):
        """重新列当前远程目录；远端文件被其他程序改动后靠它手动同步。"""
        self._list_remote(self._remote_path)

    def _go_up(self):
        """远程目录上跳一级（到根为止）"""
        # 手动按 '/' 切分而不用 os.path.dirname：远程路径永远是 POSIX 风格，
        # 在 Windows 上 dirname 不认 '/'，会把整条路径当成文件名
        parent = '/'.join(self._remote_path.rstrip('/').split('/')[:-1])
        if not parent:
            parent = '/'
        self._list_remote(parent)

    def _on_item_double_clicked(self, item, column):
        """远程条目双击：目录进入，文件直接下载（列目录中不响应）"""
        # 列目录途中禁止再跳：此时树里还是旧目录的条目，双击会把新路径与
        # 旧条目配错，容易进到不存在的目录
        if self._listing:
            return
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if not entry:
            return
        if entry['is_dir']:
            new_path = self._remote_path.rstrip('/') + '/' + entry['name']
            self._list_remote(new_path)
        else:
            # 不在这里拼 worker：双击与右键「下载」共用 _download_file 这条入口，
            # 并发闸门、去重与落盘逻辑只需在入口处维护一份
            self._download_file(entry)

    # ------------------------------------------------------------------ 拖拽上传
    def eventFilter(self, obj, event):
        """远程文件列表/传输队列表拖放事件：从资源管理器拖入文件/目录即上传到当前远程目录

        P1-5：队列表同样接收拖入（用户自然会把文件拖向"传输队列"，
        与拖到远程树等价，均上传到当前远程目录）。
        """
        if obj is self._tree or obj is self._transfer_table:
            etype = event.type()
            # DragEnter/DragMove 都必须显式 accept：只处理 Drop 的话 Qt 不会把
            # 光标变成「可放置」，Drop 事件也永远不会送达
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

        # 2. 删除源行（进度条随行销毁，所以第 1 步必须先把 value 抄出来）
        table.removeRow(from_row)

        # 3. 调整目标行索引（删除后索引可能变化）
        # 源行在上方时，删除会让目标行号整体前移一位；不修正会插到目标下一行
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
        # 只做表格 → 字典的单向重建：表格是行的唯一权威，字典里 row 只是缓存。
        # 读不到 tid 的行（异常插入或已置空的占位行）跳过，但不动字典项，
        # 否则 worker 还在跑的任务会因为一次调序从 _transfer_workers 里消失
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
        # 只认本地文件：浏览器拖来的 http 链接 toLocalFile() 返回空串，
        # 已不存在的路径也滤掉，免得后面计算大小时直接抛异常
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if p and os.path.exists(p):
                paths.append(p)
        if not paths:
            self._log('[SFTP] 拖入的内容不含有效的本地文件')
            return
        # 逐个交给 _upload_file 而不是在这里拼 worker：入口函数已按 is_dir 分流，
        # 拖入目录时自然落到递归上传分支，也统一享受并发闸门
        for p in paths:
            # 末尾分隔符要先剥掉：C:\\dir\\ 的 basename 是空串，会让远端路径变成 "//"
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
            # 多选时逐个走递归分支；只有「单选且恰好是当前项」才落到下面的
            # currentItem 流程，否则当前项会被重复上传一次
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
        # 目录与文件用两个 worker 类（递归 vs 单文件），分流必须在这里做完：
        # 下面按文件拼远端路径、算大小的写法对目录都不成立
        if data['is_dir']:
            self._upload_dir(data)
            return
        local_path = data['path']
        # 根目录 '/' 经 rstrip 变空串，拼出来仍是 '/name'，不要改成 os.path.join：
        # join('/','a') 在 Windows 侧会掺入反斜杠，远端路径必须只用正斜杠
        remote_path = self._remote_path.rstrip('/') + '/' + data['name']
        # 大小只作进度分母的预估：文件可能正在被别的程序写入，进度到 100% 时
        # 以 worker 实际传输字节为准，这里算小了不会卡住进度条
        file_size = os.path.getsize(local_path) if os.path.isfile(local_path) else 0
        self._log(f'[SFTP] 上传: {local_path} -> {remote_path}')
        worker = SFTPOperationWorker(self._conn_params, 'upload', local_path, remote_path, file_size=file_size)
        self._start_transfer_op(worker, data['name'], '上传', file_size,
                                op='upload', local_path=local_path, remote_path=remote_path)

    def _upload_dir(self, data):
        """整目录上传（递归 worker）"""
        # 目录名单独取出：既用来拼远端路径，也做进度行的前缀（[目录] xxx）；
        # 从路径反推 basename 在末尾带分隔符时会得到空串
        local_dir = data['path']
        dir_name = data['name']
        remote_dir = self._remote_path.rstrip('/') + '/' + dir_name
        self._log(f'[SFTP] 上传目录: {local_dir} -> {remote_dir}')
        worker = SFTPDirTransferWorker(self._conn_params, 'upload_dir',
                                       local_dir=local_dir, remote_dir=remote_dir, dir_name=dir_name)
        # 目录总字节要等 worker 遍历完才知道，先按 0 建行，进度由 worker 边算边报
        self._start_transfer_op(worker, f'[目录] {dir_name}', '上传', 0,
                                op='upload_dir', local_path=local_dir, remote_path=remote_dir)

    def _download_file(self, entry=None):
        """下载入口：带参传单条，无参按钮点击批量下载选中项；目录转 _download_dir"""
        if not isinstance(entry, dict):
            entry = None
        # 无参点击按钮：对所有选中项批量下载（无选中则回退 currentItem 单选流程）
        if entry is None:
            items = self._tree.selectedItems()
            # 与上传侧同一套判定：多选批量处理，单选且为当前项才走单文件流程
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
        # 同上：目录走递归 worker，且下面按文件取的 file_size 对目录恒为 0、没有意义
        if entry['is_dir']:
            self._download_dir(entry)
            return
        # 远端用字符串拼（只用正斜杠），本地用 os.path.join（跟随平台分隔符）
        remote_path = self._remote_path.rstrip('/') + '/' + entry['name']
        # entry['name'] 是 listdir 的单段名，不含分隔符，落地只会落在当前本地目录内；
        # 同名文件直接覆盖，不弹确认——传输队列本身就是用户显式发起的动作
        local_path = os.path.join(self._local_path, entry['name'])
        # 目录条目 size 恒为 0，文件用列目录时缓存的字节数，省一次远端 stat 往返
        file_size = entry.get('size', 0)
        self._log(f'[SFTP] 下载: {remote_path} -> {local_path}')
        worker = SFTPOperationWorker(self._conn_params, 'download', local_path, remote_path, file_size=file_size)
        self._start_transfer_op(worker, entry['name'], '下载', file_size,
                                op='download', local_path=local_path, remote_path=remote_path)

    def _download_dir(self, entry):
        """整目录下载（递归 worker）"""
        # 递归下载保持目录名不变，落点由本地面板决定；同名目录会与已有内容合并
        dir_name = entry['name']
        remote_dir = self._remote_path.rstrip('/') + '/' + dir_name
        local_dir = os.path.join(self._local_path, dir_name)
        # 目录名原样沿用，不追加时间戳后缀：同名目录按文件逐个覆盖合并（worker 不预删），
        # 这是用户重复下载同一目录时想要的增量效果
        self._log(f'[SFTP] 下载目录: {remote_dir} -> {local_dir}')
        worker = SFTPDirTransferWorker(self._conn_params, 'download_dir',
                                       local_dir=local_dir, remote_dir=remote_dir, dir_name=dir_name)
        # 总字节传 0：目录大小要递归统计，交 worker 边传边报，进度条按已传文件数推进
        self._start_transfer_op(worker, f'[目录] {dir_name}', '下载', 0,
                                op='download_dir', local_path=local_dir, remote_path=remote_dir)

    def _start_transfer_op(self, worker, filename, op_label, file_size,
                           op=None, local_path='', remote_path=''):
        """传输任务入表并启动：建行、接信号、交给并发闸门调度。

        身份用自增 tid 而不是行号：行会被拖拽调序、删除、清空，行号随时失效，
        而 params 快照要活到用户点「重新开始」那一刻。
        """
        # tid 单调递增且永不复用：它同时是字典键、行 UserRole 值和落盘记录的身份，
        # 复用会让「删除后重试」的旧回调认错任务
        tid = self._next_transfer_id
        self._next_transfer_id += 1
        # 新行一律追加在末尾：队列顺序即用户发起顺序，拖拽调序时再靠 _move_transfer_row 改
        row = self._transfer_table.rowCount()
        self._transfer_table.insertRow(row)
        name_item = QTableWidgetItem(f'{op_label}: {filename}')
        # P2-7：tid 写入第 0 列 UserRole——拖拽调序后行映射直读，
        # 不再用"文件名是否出现在行文本里"的子串匹配（a.txt/aa.txt 会串行）
        name_item.setData(Qt.ItemDataRole.UserRole, tid)
        self._transfer_table.setItem(row, 0, name_item)
        # 进度条用 cellWidget 而非 item：QTableWidget 不管控 widget 的数据与绘制，
        # 换列/删行时必须手动搬（这也是 _move_transfer_row 要抄 value 的原因）
        pb = ProgressBar()
        pb.setRange(0, 100)
        pb.setValue(0)
        self._transfer_table.setCellWidget(row, 1, pb)
        self._transfer_table.setItem(row, 2, QTableWidgetItem('0 B/s'))
        # 初始状态写「排队中」而不是「传输中」：能不能立刻启动由并发闸门决定，
        # 先写传输中会在满载时出现「传输中」却一动不动的一行
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
        # 行被删或队列被清空后，worker 还可能送最后一帧进度，查不到就直接丢弃
        info = self._transfer_workers.get(tid)
        if not info:
            return
        row = info['row']
        # 目录传输的总量边遍历边累加，中途 total 可能为 0，此时不更新百分比
        # （除零会抛 OverflowError，中断整条 Qt 信号分发）
        pct = int(transferred * 100 / total) if total > 0 else 0
        pb = self._transfer_table.cellWidget(row, 1)
        if pb:
            pb.setValue(pct)
        now = time.time()
        # 0.5s 才更新一次速度：worker 按块回调，每帧都改表格文本会把 UI 拖死
        dt = now - info['last_time']
        if dt >= 0.5:
            db = transferred - info['last_bytes']
            # db <= 0 时不更新速度：暂停恢复或重传会让累计字节回退，直接相除会得到
            # 负速度；保留上一次的读数比显示 -3 MB/s 更不容易让人误会
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
            # 只有下载才通知主窗口：上传不改动本机文件，发出去只会让设备列表白刷
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
        # 只刷受影响的一侧：上传后远端多出条目而本地没变，两边都重列会白付一次
        # 远端 listdir 的往返延迟（慢速链路上肉眼可见）
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
        # 只在定时器空闲时启动：isActive 判断让同一窗口内的多个完成任务合并成一次刷新，
        # 否则批量传 20 个文件会触发 20 次目录重列
        if self._pending_refresh and not self._refresh_timer.isActive():
            self._refresh_timer.start()

    @staticmethod
    def _posix_dirname(path):
        """POSIX 风格取父目录（远程路径用，root 的父仍是 root）"""
        p = (path or '').rstrip('/')
        return p.rsplit('/', 1)[0] if '/' in p else '/'

    def _same_remote_dir(self, target) -> bool:
        """判断目标是否就是当前浏览的远程目录；'/' 与空串视为同一处。"""
        cur = (self._remote_path or '').rstrip('/') or '/'
        return (target or '').rstrip('/') == cur

    def _same_local_dir(self, target) -> bool:
        """同上，本地侧；用 normcase 折叠盘符大小写与分隔符差异。"""
        # 非法路径当作「不是同一目录」：宁可少刷一次，也不要抛异常打断传输回调
        try:
            return (os.path.normcase(os.path.normpath(target))
                    == os.path.normcase(os.path.normpath(self._local_path)))
        except Exception:
            return False

    def _flush_pending_refresh(self):
        """去抖到期：执行挂起的目录刷新（此刻再取当前目录，保留代际号机制）"""
        # 先取走再清空：刷新的同时若有任务完成，新标记会落在空集合上，不被本轮吞掉
        pending = self._pending_refresh
        self._pending_refresh = set()
        if 'remote' in pending:
            self._list_remote(self._remote_path)
        if 'local' in pending:
            self._list_local(self._local_path)

    def _on_transfer_error(self, tid, error):
        """传输失败：行内置失败原因；info 保留供右键"重新开始" """
        info = self._transfer_workers.get(tid)
        # 失败同样要置 failed 并回落队列：否则一个失败任务会永久占住一个并发槽
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
        # 只能用「记住最后一次右键的 row」：Action.triggered 只带 checked，
        # 无法携带行号，预构建的槽只能读这个缓存
        # 初值 -1 表示「还没右键过任何行」，_find_tid_by_row 会直接判空；
        # 因此 _on_transfer_context_menu 每次弹菜单前都要重算它，上次的 row 可能已被调序改指
        self._ctx_transfer_row = -1
        # Action 与菜单都挂在 self 上只建一次：每次右键现建会往 QObject 树里不断
        # 堆积子对象（RoundMenu 不自动回收），而且 _on_transfer_context_menu 需要
        # 稳定的引用去改 enabled，所以每个 Action 都留了 self._act_* 句柄
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
        # 右键瞬间写快照：菜单弹出后用户仍可能改变选择，槽执行时再读会取错目标
        self._ctx_local_data = None
        self._ctx_local_items = []  # 右键时的传输目标列表（Ctrl 多选时含全部选中项）
        self._ctx_local_menu_full = RoundMenu(parent=self)  # 右键点击文件时
        self._ctx_local_menu_empty = RoundMenu(parent=self)  # 右键点击空白时
        act = Action(FluentIcon.UP, '传输（上传）', self)
        act.triggered.connect(lambda: self._upload_items(self._ctx_local_items))
        self._ctx_local_menu_full.addAction(act)
        # 留句柄是为了右键时把文案改成「上传 3 项」：Action 文案不能由槽在触发时补，
        # 用户看到菜单的那一刻就必须是对的
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
        # 「新建」子菜单必须建第二份：一个 QAction 同时 addAction 到两个菜单时，
        # 后挂的会把它从第一个菜单里摘走，表现为右键文件时子菜单是空的
        new_local2 = RoundMenu('新建', self)
        new_local2.addAction(Action(FluentIcon.DOCUMENT, '新建文件', triggered=self._ctx_new_file_local))
        new_local2.addAction(Action(FluentIcon.FOLDER, '新建文件夹', triggered=self._ctx_new_dir_local))
        self._ctx_local_menu_empty.addMenu(new_local2)

        # ---- 远程面板菜单（item 相关 + 新建子菜单）
        self._ctx_remote_entry = None
        self._ctx_remote_items = []  # 右键时的传输目标列表（Ctrl 多选时含全部选中项）
        # full / empty 两份菜单共用同一批槽：点条目与点空白要弹出的项不同，
        # 但槽读的都是上面那份快照
        self._ctx_remote_menu_full = RoundMenu(parent=self)
        self._ctx_remote_menu_empty = RoundMenu(parent=self)
        act = Action(FluentIcon.DOWN, '传输（下载）', self)
        act.triggered.connect(lambda: self._download_items(self._ctx_remote_items))
        self._ctx_remote_menu_full.addAction(act)
        # 同 _act_ctx_upload：多选时文案要变成「下载 3 项」，故保留引用
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
        # 刷新走 lambda 取当前目录而不是绑死右键时的路径：菜单是单例，
        # 绑死会让用户切换目录后点刷新却回到旧目录
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
        # 所有菜单都以 self 为 parent，随面板析构统一回收，shutdown 里不逐个 delete，
        # 少一处必须跟着 Action 增删维护的清理代码

    def _ctx_copy_local_path(self):
        """复制本地绝对路径；空白处右键也可能点到，没有快照就静默返回。"""
        data = self._ctx_local_data
        if data:
            QApplication.clipboard().setText(data['path'])
            self._log(f'[SFTP] 已复制路径: {data["path"]}')

    def _ctx_copy_remote_path(self):
        """复制远程绝对路径；entry 里只有名字，必须拼上当前浏览目录。"""
        entry = self._ctx_remote_entry
        if entry:
            # entry 里只有单段名字，绝对路径必须现场拼；rstrip('/') 同时兜住「当前在根目录」
            # 的情况，否则拼出来是 '//name'，粘到终端里还能用，粘到别的工具里就未必
            remote_full = self._remote_path.rstrip('/') + '/' + entry['name']
            QApplication.clipboard().setText(remote_full)
            self._log(f'[SFTP] 已复制路径: {remote_full}')

    # ------------------------------------------------------------------ 传输队列右键菜单
    def _on_transfer_context_menu(self, pos):
        """传输队列右键：按行状态（传输中/已暂停/失败/完成）启用对应菜单项"""
        # 按状态列文字决定哪些项可点：这是用户眼前看到的东西，和 info['state'] 等价，
        # 又不必从 row 反查 tid 再读字典
        # 目标行取自鼠标坐标而不是 selectedItems：右键不改选择，点在哪一行就该操作哪一行，
        # 读选中项会把「上一次选中的行」当成目标，在空白处右键时直接错指
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
        # 暂停只对「传输中」开放：排队中的任务还没线程可暂停，置成 paused 会让它
        # 永远占着队列位置却不消耗并发位，队列直接卡死
        self._act_t_pause.setEnabled(has_selection and selected_status == '传输中')
        self._act_t_pause_all.setEnabled(has_tasks)
        self._act_t_resume.setEnabled(has_selection and selected_status == '已暂停')
        self._act_t_resume_all.setEnabled(has_tasks)
        self._act_t_delete.setEnabled(has_selection)
        self._act_t_delete_all.setEnabled(has_tasks)
        # 用 startswith 而不是相等：失败文案是「失败: 具体原因」，长度随错误信息变化
        self._act_t_retry.setEnabled(has_selection and selected_status.startswith('失败'))
        # 只有已完成的下载才有本地文件可定位；上传的「所在文件夹」是本地面板无关目录
        self._act_t_open_folder.setEnabled(has_selection and selected_status == '完成')
        self._act_t_clear_done.setEnabled(has_tasks)
        self._ctx_transfer_menu.exec(
            self._transfer_table.viewport().mapToGlobal(pos),
            aniType=_popup_ani_type())

    def _find_tid_by_row(self, row):
        """表格行号反查任务 tid（行号在增删后由调用方维护）"""
        # 线性扫描即可：队列行数通常个位数，维护反向索引的复杂度不划算
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
        # 状态不是 running 就什么都不做：已完成的任务没有线程可暂停，排队中的还没启动，
        # 硬置成 paused 会让它永久占着队列位置（详见 _on_transfer_context_menu 的说明）
        if info.get('state') != 'running':
            return
        # pause() 只置标志位，真正阻塞点在下一次进度回调里，所以这里改状态就够了
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
        # 只认状态机不认按钮文案：这个入口还被「全部继续」复用，靠菜单 enabled 把关的话，
        # 连点两次恢复会把速度采样基准再刷一遍
        if info.get('state') != 'paused':
            return
        worker = info['worker']
        if hasattr(worker, 'resume'):
            worker.resume()
        info['state'] = 'running'
        # 重置采样基准：暂停的时长不能算进平均速度，否则恢复瞬间会显示接近 0 的速度
        info['last_time'] = time.time()
        info['speed'] = 0.0
        # 第 3 列文字同时是右键菜单 enabled 的判定依据，状态改了必须同步改文字，
        # 否则恢复后菜单里的「继续」仍然可点
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
        # 遍历字典副本：pause()/状态写入会间接触发 worker 回调改动 _transfer_workers，
        # 直接迭代原字典会抛 RuntimeError: dictionary changed size during iteration
        for tid, info in list(self._transfer_workers.items()):
            if info.get('state') != 'running':
                continue
            row = info['row']
            status_item = self._transfer_table.item(row, 3)
            worker = info['worker']
            # 防御快速操作类 worker：未实现 pause 时只改状态，不强求阻塞
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
        # 同样遍历副本；恢复后立刻重置速度基准，暂停时长不能计入平均速度
        for tid, info in list(self._transfer_workers.items()):
            if info.get('state') != 'paused':
                continue
            row = info['row']
            status_item = self._transfer_table.item(row, 3)
            worker = info['worker']
            # hasattr 而非直接调用：历史行或占位行里的 worker 未必实现了暂停协议，
            # 缺了它仍要把状态改回 running，否则这一行永远停在「已暂停」且不再收进度
            if hasattr(worker, 'resume'):
                worker.resume()
            info['state'] = 'running'
            info['last_time'] = time.time()
            info['speed'] = 0.0
            if status_item:
                status_item.setText('传输中')
            count += 1
        # 一个都没恢复时不写盘也不记日志：右键菜单的「全部继续」在无暂停任务时仍可点，
        # 每次点击都留一条日志会把有用的记录冲掉
        if count:
            self._log(f'[SFTP] 已继续全部传输 ({count} 个任务)')
            self._schedule_pending_save()

    def _transfer_delete_row(self, row):
        """删除指定行任务：运行中先 stop，删行后修正后续行的行号映射；
        P2-1：排队任务出队，删的是占位任务时泵队列补位"""
        tid = self._find_tid_by_row(row)
        # 名字要在 removeRow 之前取：删掉后 item(row, 0) 已经指向下一行，日志会张冠李戴
        name_item = self._transfer_table.item(row, 0)
        name = name_item.text() if name_item else f'任务{row}'
        if tid is not None:
            info = self._transfer_workers.get(tid)
            if info:
                was_active = info.get('state') in ('running', 'paused')
                # 先出队再删字典：否则泵队列会把已删除的 tid 当成待启动任务捞出来
                if tid in self._queued_transfers:
                    self._queued_transfers.remove(tid)
                worker = info['worker']
                # 用 stop() 而不是 terminate()：线程要在下一次读写循环里自己退出，
                # 强杀会在 SFTP 通道上留下半包，后续任务全部报协议错误
                if hasattr(worker, 'stop'):
                    worker.stop()
                self._safe_delete_transfer_worker(tid)
                # 只有真正占用并发位的任务才需要补位；删排队中的行时队列长度自动减一
                if was_active:
                    self._pump_transfer_queue()
        self._transfer_table.removeRow(row)
        # 下方各行 row 全部前移一位；不修正会让右键操作作用到隔壁任务上
        for t, inf in self._transfer_workers.items():
            if inf['row'] > row:
                inf['row'] -= 1
        self._log(f'[SFTP] 已删除传输: {name}')
        self._schedule_pending_save()

    def _transfer_delete_all(self):
        """清空传输队列（逐个 stop 后释放 worker；P2-1：排队表一并清空）"""
        # 先清排队表：stop() 可能触发失败的 finished，泵队列不能再去启动已删任务
        self._queued_transfers = []
        for tid in list(self._transfer_workers.keys()):
            info = self._transfer_workers.get(tid)
            if info:
                worker = info['worker']
                if hasattr(worker, 'stop'):
                    worker.stop()
                self._safe_delete_transfer_worker(tid)
        # 不逐行 removeRow：setRowCount(0) 一次清空，避免每删一行触发一次布局；
        # 行 item 随表格一起回收，worker 已在上面 stop 并从字典解绑
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
        # 缺 op 就重建不出 worker（快速操作行与历史行可能如此），只能让用户重做一次
        if not params or not params[1]:
            self._log('[SFTP] 该任务缺少重试参数，无法重新开始')
            return
        conn_params, op, local_path, remote_path = params
        # 按 op 分派三种 worker：目录传输要显式给 dir_name（进度按文件计），
        # 单文件传输要 file_size 才能算百分比，两者构造签名不同不能合并
        if op == 'upload_dir':
            worker = SFTPDirTransferWorker(
                conn_params, op, local_dir=local_path, remote_dir=remote_path,
                dir_name=os.path.basename(local_path.rstrip('/\\')))
        elif op == 'download_dir':
            # 下载侧目录名只能从 remote_path 反推：params 里没单独存 dir_name
            worker = SFTPDirTransferWorker(
                conn_params, op, local_dir=local_path, remote_dir=remote_path,
                dir_name=os.path.basename(remote_path.rstrip('/')))
        else:
            # 上传前重新取一次大小：上次失败可能发生在文件被改动之后，沿用旧字节数
            # 会让进度条在中途冲过 100% 或卡在 80% 不动；源文件已被删除时退回 0
            file_size = os.path.getsize(local_path) if (op == 'upload' and local_path and os.path.isfile(local_path)) else 0
            worker = SFTPOperationWorker(conn_params, op, local_path, remote_path, file_size=file_size)
        # 断开旧 worker 信号：旧线程可能还活着并在退出前补发 error/success，
        # 不摘掉的话刚重置的进度会被失败文案覆盖回去
        old_worker = info['worker']
        try:
            old_worker.progress.disconnect()
            old_worker.success.disconnect()
            old_worker.error.disconnect()
        except Exception:
            pass
        # 重置采样基准并清零进度：不重置 last_bytes 的话，新 worker 报的第一个
        # 累计字节会被拿去和旧任务的总量相减，速度瞬间变成负值或天文数字
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
        # params 固定为 (conn, op, local, remote) 四元组，第 3 位恒是本地路径：
        # 下载时它是落点，上传时它是源文件，两种都能在资源管理器里定位
        local_path = params[2]
        if not local_path or not os.path.exists(local_path):
            self._log('[SFTP] 本地文件不存在，无法定位')
            return
        try:
            # 用 explorer /select 而不是 os.startfile：前者能在资源管理器里高亮选中该文件
            subprocess.Popen(['explorer', '/select,', os.path.normpath(local_path)])
        except Exception as e:
            self._log(f'[SFTP] 打开所在文件夹失败: {e}')

    def _transfer_clear_completed(self):
        """清除所有状态为"完成"的行（进行中/暂停/失败的任务不动）"""
        table = self._transfer_table
        # 先收集再倒序删：正序删会让后面的行号整体前移，第二行起就删错目标
        done_rows = []
        for row in range(table.rowCount()):
            status_item = table.item(row, 3)
            if status_item and status_item.text() == '完成':
                done_rows.append(row)
        # 从大行号往小删：先删小行号会让后面每行的 row 都变，得反复重算映射
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
        # 不传 file_size：mkdir/rmdir 没有字节概念，给了反而让进度条瞬间跳满
        worker = SFTPOperationWorker(self._conn_params, op, local_path, remote_path)
        # 行名用 remote_path 的 basename：快速操作没有独立的文件名参数
        label = _QUICK_OP_LABELS.get(op, op)
        self._start_transfer_op(worker, os.path.basename(remote_path.rstrip('/')),
                                label, 0, op=op, local_path=local_path,
                                remote_path=remote_path)

    def _selected_remote_entries(self):
        """右侧远程面板选中项（含 currentItem 兜底；空列表表示无选中）"""
        items = self._tree.selectedItems()
        # 失去焦点或程序化设置 current 后 selectedItems 可能为空，用 currentItem 兜底
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
        # 文案必须点明「仅能删除空目录」：rmdir 是非递归的，用户以为会连内容
        # 一起删时，报错就成了唯一的失败信号
        if len(entries) == 1:
            e = entries[0]
            if e['is_dir']:
                msg = f'确定要删除远程目录 "{e["name"]}" 吗？\n注意：仅能删除空目录。'
            else:
                msg = f'确定要删除远程文件 "{e["name"]}" 吗？'
        else:
            msg = (f'确定要删除选中的 {len(entries)} 个远程文件/目录吗？\n'
                   '注意：目录仅能删除空目录。')
        # 确认按钮写动词「删除」而不是默认的「确定」：破坏性操作要让用户按下那一刻
        # 就知道会发生什么，而不是靠标题去回想点的是哪个框
        dlg = MessageBox('确认删除', msg, self)
        dlg.yesButton.setText('删除')
        dlg.cancelButton.setText('取消')
        return bool(dlg.exec())

    def _update_delete_btn_text(self):
        """P0-2：删除按钮文案随远程侧选中数变化（与右键"传输（上传N 项）"同口径）"""
        n = len(self._tree.selectedItems())
        # 只有多选才显示计数：单项保持原文案，避免按钮宽度随选择抖动
        self._btn_delete.setText(f'删除（{n} 项）' if n > 1 else '删除')

    def _delete_selected(self):
        """删除远程选中项（P0-2：支持多选批量；二次确认与右键菜单同口径）"""
        entries = self._selected_remote_entries()
        if not entries:
            self._log('[SFTP] 请先在右侧远程面板选择要删除的文件或目录')
            return
        if not self._confirm_delete_remote(entries):
            return
        # 逐条入队而不是合成一个批量删除任务：远端删除没有事务语义，合并后某一项失败
        # 会让整批显示失败，用户还得自己分辨哪些已删掉
        for entry in entries:
            remote_path = self._remote_path.rstrip('/') + '/' + entry['name']
            # 目录走 rmdir、文件走 remove；rmdir 遇非空目录会直接报错，
            # 这正是想要的保护，绝不做递归删除
            op = 'rmdir' if entry['is_dir'] else 'delete'
            self._run_quick_op(op, remote_path, f'[SFTP] 删除: {remote_path}')

    def _create_directory(self):
        """在当前远程目录新建目录（输入名后走 mkdir 任务）"""
        name = self._ask_name('新建目录', '目录名:')
        # 取消返回 None，直接回车得到空串，两者都算放弃
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
        # 凭据拼进 URL 是因为 Xftp 只认这种命令行格式；代价是密码会出现在进程
        # 列表里，所以只适合本机可信环境
        xftp_url = f'sftp://{self._username}:{self._password}@{self._host}:{self._port}'
        try:
            # shell=True 才能让 xftp 经由 PATH 解析；新开控制台可避免 xftp 的
            # 输出挂到本进程的 stdout 上
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
            # strip 首尾空白：误敲的空格会变成合法但看不见的文件名
            return dlg.edit.text().strip()
        return None

    @staticmethod
    def _format_size(size):
        """字节数 → 人类可读（B/KB/MB/GB/TB）"""
        # B 不带小数（"1.0 B" 像测量值），其余保留一位；超出 GB 一律按 TB 收尾
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f'{size:.1f} {unit}' if unit != 'B' else f'{size} {unit}'
            size /= 1024
        return f'{size:.1f} TB'

    # ------------------------------------------------------------------ 右键菜单（预构建缓存，直接弹出）
    def _on_local_context_menu(self, pos):
        """本地面板右键菜单（预构建缓存，零构建开销）"""
        # itemAt 收的就是 viewport 坐标，pos 直接来自 CustomContextMenu 信号，无需换算
        item = self._local_tree.itemAt(pos)
        if item:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if not data:
                return
            # 与远程侧同构：单条操作读 _ctx_local_data，批量上传读 _ctx_local_items
            self._ctx_local_data = data
            # Ctrl 多选传输：右键项在选中集内时，目标扩展为整个选中集（资源管理器惯例）
            self._ctx_local_items = self._collect_selected(self._local_tree, item, data)
            n = len(self._ctx_local_items)
            self._act_ctx_upload.setText(f'传输（上传{n} 项）' if n > 1 else '传输（上传）')
            self._ctx_local_menu_full.exec(
                self._local_tree.viewport().mapToGlobal(pos),
                aniType=_popup_ani_type())
        else:
            # 空白处只给「新建」：没有目标条目时上传/重命名/删除都无从下手
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
            # 单条操作（打开/重命名/删除）读 _ctx_remote_entry，批量下载读 _ctx_remote_items；
            # 两个快照每次右键都覆盖写，不会残留上一目录的 entry 引用
            self._ctx_remote_entry = entry
            # Ctrl 多选传输：右键项在选中集内时，目标扩展为整个选中集（资源管理器惯例）
            self._ctx_remote_items = self._collect_selected(self._tree, item, entry)
            n = len(self._ctx_remote_items)
            self._act_ctx_download.setText(f'传输（下载{n} 项）' if n > 1 else '传输（下载）')
            self._ctx_remote_menu_full.exec(
                self._tree.viewport().mapToGlobal(pos),
                aniType=_popup_ani_type())
        else:
            # 空白处只给「新建」，与本地侧口径保持一致
            self._ctx_remote_menu_empty.exec(
                self._tree.viewport().mapToGlobal(pos),
                aniType=_popup_ani_type())

    def _collect_selected(self, tree, clicked_item, fallback):
        """收集右键操作的目标列表：

        右键项已在选中集内 → 返回整个选中集（支持 Ctrl 多选批量传输）；
        否则（右键未选中的项）→ 仅返回右键项本身，符合资源管理器惯例。"""
        items = tree.selectedItems()
        # 右键未选中项时只作用于它自己，但不动用户已有选择：
        # 清空选择会让误点后的 Ctrl 多选全部丢失
        if any(it is clicked_item for it in items):
            return [it.data(0, Qt.ItemDataRole.UserRole) for it in items
                    if it.data(0, Qt.ItemDataRole.UserRole)]
        return [fallback]

    def _copy_selected_paths(self):
        """P1-4：Ctrl+C 复制选中项全路径（优先远程侧，其次本地侧；多选换行分隔）

        焦点在 QLineEdit（路径框/搜索框）时由输入框自身处理复制
        （QLineEdit accept ShortcutOverride），本回调不会被触发。
        """
        # 靠焦点判断要复制哪一侧：没有焦点就无从知道用户指的是远程还是本地
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
        # 两侧都没命中说明焦点在面板容器或分栏分隔条上，此时静默不复制：
        # 顺手清空剪贴板会毁掉用户原本复制好的内容

    def _upload_items(self, items):
        """批量上传（右键多选传输入口）：逐项调用单文件上传逻辑，目录走目录传输"""
        if not items:
            self._log('[SFTP] 请先选择一个文件或目录')
            return
        # 逐项而不是合成一个批量 worker：每项各自入队成行，单项失败只影响它自己，
        # 用户能单独重试；合成一条的话任何一项报错都会让整批显示为失败
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
        # 延迟导入：本模块被纯逻辑测试导入时，不必连带触发应用目录的路径探测
        from core.app_paths import get_app_dir
        base = get_app_dir()
        temp_dir = os.path.join(base, '_sftp_temp')
        # exist_ok=True 兜住目录被用户手动删掉或被清理线程清空的情况；
        # 固定目录名也让「再打开同一个远程文件」直接复用上次的本地副本
        os.makedirs(temp_dir, exist_ok=True)
        return temp_dir

    def _ctx_local_open(self, data):
        """本地文件右键打开（系统默认程序）"""
        path = data['path']
        try:
            # os.startfile 是 Windows 专有 API；本仓库只发 Windows 版，不必分支
            os.startfile(path)
            self._log(f'[SFTP] 已打开: {path}')
        except OSError as e:
            self._log(f'[SFTP] 打开失败: {e}')

    def _ctx_remote_open(self, entry):
        """远程文件右键打开：先下载到 _sftp_temp 再启系统程序；
        目录整体下载后打开"""
        # 每次打开顺手清一次：临时目录只在「打开远程文件」时增长，这里是唯一回收时机
        _cleanup_sftp_temp()  # 打开前清理超过 7 天的旧临时文件
        temp_dir = self._get_temp_dir()
        remote_path = self._remote_path.rstrip('/') + '/' + entry['name']
        # 临时副本按原文件名落地，不建唯一子目录：同名远程文件再次打开时直接覆盖旧副本，
        # 否则 _sftp_temp 会按打开次数无限膨胀
        if entry['is_dir']:
            # 目录下载完打开的是文件夹本身，不遍历里面逐个打开文件
            local_dir = os.path.join(temp_dir, entry['name'])
            self._log(f'[SFTP] 下载目录并打开: {remote_path} -> {local_dir}')
            worker = SFTPDirTransferWorker(self._conn_params, 'download_dir',
                                           local_dir=local_dir, remote_dir=remote_path, dir_name=entry['name'])
            # p=local_dir 早绑定：connect 时就把路径固定下来，回调触发时不再读局部变量
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
        """临时目录下载完成后的打开回调；用户只是想看一眼，无需先选保存位置。"""
        try:
            os.startfile(path)
            self._log(f'[SFTP] 已打开: {path}')
        except OSError as e:
            self._log(f'[SFTP] 打开失败: {e}')

    def _ctx_rename_local(self, data):
        """本地文件重命名（同目录内，权限不足单独提示）"""
        new_name = self._ask_name('重命名', '新名称:', data['name'])
        # 取消、空名、同名都直接返回：同名 rename 在 Windows 上会直接抛 FileExistsError
        if not new_name or new_name == data['name']:
            return
        old_path = data['path']
        new_path = os.path.join(os.path.dirname(old_path), new_name)
        # 不预先校验 Windows 非法字符（\ / : * ? " < > |）：os.rename 会以 OSError 拒绝，
        # 与其复制一份不完整的黑名单，不如让系统给出准确的失败原因
        try:
            os.rename(old_path, new_path)
            self._log(f'[SFTP] 已重命名: {data["name"]} -> {new_name}')
            self._list_local(self._local_path)
        except PermissionError as e:
            # 单独捕获权限错误：这类失败要明确提示是权限问题，笼统的「失败」会误导排查
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
        # 源路径放在 local_path 位：_run_quick_op 的 params 是
        # (conn, op, local_path, remote_path)，rename 需要旧路径当源
        self._run_quick_op('rename', new_path,
                           f'[SFTP] 重命名: {old_path} -> {new_path}',
                           local_path=old_path)

    def _ctx_delete_local(self, data):
        """本地文件/目录删除（二次确认，目录整树删除）"""
        # 本地删除是真删、不进回收站，所以文案必须写明「及其所有内容」
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
            # 目录必须 rmtree：os.remove 对非空目录直接抛 IsADirectoryError，
            # 而右键删除目录时用户的预期就是把里面的东西一起清掉（确认文案已写明）
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
            # 'w' 会截断同名文件；新建流程依赖用户不输入已存在的名字，未额外校验
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
            # exist_ok=True：重复点「新建」不报错，与资源管理器行为一致
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
        # 走队列而不是直接调 SFTP：失败行会留在表里，可以右键「重新开始」
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
        # _closing 兼作重入闸门：标签页关闭与 closeEvent 会各调一次 shutdown
        if self._closing:
            return
        self._closing = True
        # P2-2 步骤 1：关标签/退应用前先把未完成任务快照写盘（必须在下面清空
        # _queued_transfers 与释放 worker 之前，否则队列已经空了）
        # 先停定时器再刷盘：否则它可能在 flush 之后又写一次，把干净的状态覆盖回去
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
        # 最多等 1s：健康检测只是 open_session/close 的短操作，等太久会拖慢关窗
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
    """SFTP 文件管理独立窗口（向后兼容的薄壳，内部委托 SFTPPanel）

    保留这个类是为了让既有的「独立窗口」调用方不改代码；新代码应直接用
    SFTPPanel 嵌入标签页，否则关窗与关标签会走两条不同的清理路径。
    """

    def __init__(self, host, port, username, password, server_name='', log_callback=None, parent=None):
        """建好 panel 并铺满窗口；1200x800 只是默认尺寸，用户可自由缩放"""
        super().__init__(parent)
        apply_window_qss(self)
        title = f"SFTP 文件管理 - {server_name} ({host}:{port})" if server_name else f"SFTP 文件管理 - {host}:{port}"
        self.setWindowTitle(title)
        self.resize(1200, 800)
        # 薄壳只做窗口外观与布局，状态全在 panel：关标签页时能单独 shutdown panel，
        # 不必销毁整个窗口
        self._panel = SFTPPanel(
            host, port, username, password,
            server_name=server_name,
            log_callback=log_callback, parent=self
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._panel)

    def closeEvent(self, event):
        """关窗前先关闭 panel 内的线程与 transport，顺序反了会留下野指针回调"""
        # 先收尾 panel 再走默认实现：它持有 transport 与多个线程，必须赶在窗口销毁前停掉；
        # 反过来调 super() 会先销毁子控件，之后 worker 回调拿到的都是野指针
        self._panel.shutdown()
        super().closeEvent(event)

