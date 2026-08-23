# -*- coding: utf-8 -*-
"""MainWindow 主类：组合所有 Mixin，包含初始化、信号连接、日志、列表加载、事件处理"""

import os
import re
import glob
import time

from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout,
    QListWidgetItem, QSplitter)
from PySide6.QtCore import Slot, QTimer, Qt, QDate, QDateTime, QProcess, QThread, Signal
from PySide6.QtGui import QColor, QBrush, QShortcut, QKeySequence, QTextCharFormat
from qfluentwidgets import (FluentTitleBar,
    MessageBoxBase, BodyLabel, ComboBox)
from qfluentwidgets.window.fluent_window import FluentWindowBase

from autowork_with_table import Ui_MainWindow
from core.utils import natural_sort_key, show_info_bar
from core.design_tokens import SEMANTIC
from main_window.settings_dialog import _DEFAULT_LOG_RULES, _compile_log_rules

from .settings_mixin import SettingsMixin
from .process_mixin import ProcessMixin
from .remote_mixin import RemoteMixin
from .ui_mixin import UIMixin
from qfluentwidgets import Dialog


def _match_log_rule(line, rules):
    """返回首个命中规则的 (color, name, notify)，未命中返回 (None, None, False)（notify 决定是否弹 InfoBar）"""
    for r in rules:
        if r["regex"].search(line):
            return r["color"], r["name"], r["notify"]
    return None, None, False


class _DbSettingsDialog(MessageBoxBase):
    """数据库设置对话框：内嵌 MysqlSyncCard（启用/连接/测试/保存一体）"""

    def __init__(self, card, parent=None):
        super().__init__(parent)
        self._card = card
        self.viewLayout.addWidget(card)
        self.yesButton.setText("关闭")
        self.yesButton.setVisible(False)
        self.cancelButton.setText("关闭")
        self.widget.setMinimumWidth(460)


class _LogLoadWorker(QThread):
    """后台读取日志文件并匹配高亮"""
    loaded = Signal(list, int, bool, float)  # lines_data(line,color,name,notify), line_count, truncated, size_mb
    error = Signal(str)

    def __init__(self, path, rules, tail_bytes=2 * 1024 * 1024):
        super().__init__()
        self.path = path
        self.rules = rules
        self.tail_bytes = tail_bytes

    def run(self):
        """读日志尾部 → 逐行匹配高亮规则 → loaded 信号回传（异常走 error）"""
        try:
            file_size = os.path.getsize(self.path)
            truncated = False
            with open(self.path, 'rb') as f:
                if file_size > self.tail_bytes:
                    # 大文件只读尾部 tail_bytes(默认 2MB)：整读几十 MB 日志会卡住 UI
                    f.seek(-self.tail_bytes, os.SEEK_END)
                    raw = f.read()
                    truncated = True
                else:
                    raw = f.read()
            # errors='ignore' 容忍日志中的非法 UTF-8 字节（崩溃残留/GBK 混合），
            # 保证任何情况下都能展示已有内容而不是整批解码失败
            content = raw.decode('utf-8', errors='ignore')
            if truncated:
                # 尾部截断起点在任意字节处，首行必然不完整：丢弃到第一个换行为止
                first_nl = content.find('\n')
                if first_nl != -1:
                    content = content[first_nl + 1:]

            lines = content.splitlines()
            # 预计算高亮信息（规则按序匹配，取首个命中）
            lines_data = []
            for line in lines:
                color, name, notify = _match_log_rule(line, self.rules)
                lines_data.append((line, color, name, notify))

            size_mb = file_size / (1024 * 1024)
            self.loaded.emit(lines_data, len(lines_data), truncated, size_mb)
        except Exception as e:
            self.error.emit(str(e))


class MainWindow(SettingsMixin, ProcessMixin, RemoteMixin, UIMixin, FluentWindowBase):
    """主窗口：组合 SettingsMixin / ProcessMixin / RemoteMixin / UIMixin"""

    def __init__(self):
        super().__init__()
        # Fluent 风格自定义标题栏（无边框 + Mica 云母背景 + 主题自适应按钮）
        self.setTitleBar(FluentTitleBar(self))
        # 压缩标题栏高度：默认 48px → 34px，与菜单栏/工具栏形成紧凑顶部
        self.titleBar.setFixedHeight(34)

        # 主内容垂直布局：菜单栏 + 中心内容 + 状态栏
        # （顶部 34px 留给 Fluent 标题栏）
        self.vBoxLayout = QVBoxLayout()
        self.vBoxLayout.setContentsMargins(0, 34, 0, 0)
        self.vBoxLayout.setSpacing(0)
        self.hBoxLayout.addLayout(self.vBoxLayout)
        self.hBoxLayout.setStretchFactor(self.vBoxLayout, 1)

        # 构建 UI（centralwidget 挂到 vBoxLayout 内）
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        # 台账面板单例（延迟创建，与售后面板同模式）
        self._ledger_panel = None

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

    # ==================== 初始化 ====================

    def init_ui(self):
        """初始化UI组件"""
        # 一次性加载 settings.json 到缓存（后续所有 _load_settings 都读缓存）
        self._reload_settings_cache()
        # 加载路径配置
        self._load_paths()
        # 初始化日志控件智能自动滚动
        self._init_log_auto_scroll()

        # 设置默认日期为昨天（addDays 自动处理跨月/跨年）
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

        # 异步日志加载 worker
        self._log_worker = None

        # 异步解码相关
        self._decode_process = None
        self._pending_exe_path = None
        self._pending_detect_json = None

        # 进程挂起状态
        self._process_suspended = False

        # 三端启动切换状态（按钮在"启动三端"/"关闭三端"间切换）
        self._three_running = False
        self._three_saved_mode = None  # 启动前捕获的原始分辨率，关闭时恢复

        # 初始化状态栏、右键菜单、快捷键、菜单栏
        self._init_statusbar()
        self._init_context_menus()
        self._init_menubar()
        self._init_shortcuts()
        # B5: 帧偏移持久化恢复
        self._restore_frame_offset()
        # B3: 第三列日志内容过滤框（须在 _apply_layout 之前安装好布局切换钩子）
        self._init_log_filter()
        # 从 settings.json 加载并应用用户自定义设置（主题强调色、字号、字体等）
        self._apply_theme_color()
        # 日志高亮规则（设置对话框「日志高亮」分区维护，存 settings.json）
        cfg_rules = self._load_settings().get("log_highlight_rules")
        self._log_rules = _compile_log_rules(cfg_rules or _DEFAULT_LOG_RULES)
        self._rule_last_notify = {}  # 规则名 -> 上次通知时间（防抖）
        self._apply_font_size()
        self._apply_font_family()
        self._apply_theme()
        self._init_system_theme_monitor()
        self._apply_layout()
        # Fluent ComboBox 使用自定义弹出视图，无需 setView
        self.ui.choose_exe.setMinimumWidth(150)  # 保证下拉框不被压成一条线
        # 远程状态
        self._p2p_visitors = []
        self._p2p_current_index = -1
        self._tcp_worker = None
        self._remote_session_window = None
        self._init_p2p_panel()
        # 恢复上次关闭前的远程会话（延迟执行，等待主窗口就绪）
        self._restore_remote_sessions()
        # MySQL 主模式：启动周备份（兜底基线刷新）+ 后端状态监控（降级/恢复提示）
        self._init_fallback_backup()
        self._init_backend_state_monitor()

    # ==================== 兜底备份与后端状态监控 ====================

    def _init_fallback_backup(self):
        """启动周备份：启动后延迟首次检查 + 每 24h 重复（仅 MySQL 主模式生效）"""
        self._backup_worker = None
        self._backup_timer = QTimer(self)
        self._backup_timer.setInterval(24 * 60 * 60 * 1000)  # 24h
        self._backup_timer.timeout.connect(self._start_backup_worker)
        # 启动后延迟 5s 首次检查，避开启动高峰
        QTimer.singleShot(5000, self._start_backup_worker)
        self._backup_timer.start()

    def _start_backup_worker(self):
        """后台执行周备份（单例防并发；非 MySQL 主模式跳过）"""
        from database import backend
        if not backend.is_mysql_test_mode():
            return
        if self._backup_worker and self._backup_worker.isRunning():
            return
        from workers.backup_worker import BackupWorker
        self._backup_worker = BackupWorker(self)
        self._backup_worker.result.connect(self._on_backup_result)
        self._backup_worker.start()

    def _on_backup_result(self, ok, msg, count):
        """周备份结果提示（未到期跳过时不打扰用户）"""
        if not ok:
            self._show_info_bar(f"MySQL 周备份失败：{msg}", "warning", duration=4000)
        elif count > 0:
            self._show_info_bar(f"MySQL 周备份完成：{msg}", "success", duration=3000)

    def _init_backend_state_monitor(self):
        """后端状态轮询：降级/恢复时提示用户，并同步更新右侧数据库状态标签"""
        from database import backend
        self._last_backend_state = backend.get_state()
        self._backend_state_timer = QTimer(self)
        self._backend_state_timer.setInterval(3000)
        self._backend_state_timer.timeout.connect(self._poll_backend_state)
        self._backend_state_timer.start()
        # 系统时间标签：每秒刷新（右侧信息组）
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._refresh_clock)
        self._clock_timer.start()
        self._refresh_clock()
        self._refresh_db_status_text()

    def _refresh_clock(self):
        """工具栏右侧时间标签：按系统时间刷新 HH:mm:ss"""
        try:
            self.ui.time_label.setText(
                QDateTime.currentDateTime().toString("HH:mm:ss"))
        except Exception:
            pass

    def _refresh_db_status_text(self):
        """数据库状态标签：MySQL 在线 / SQLite 兜底（跟随后端状态）"""
        from database import backend
        label = self.ui.db_status_label
        if backend.get_state() == backend.STATE_ONLINE:
            label.setText("数据库: MySQL 在线")
            label.setStyleSheet("color: #1D9E75;")
        else:
            label.setText("数据库: SQLite 兜底")
            label.setStyleSheet("color: #BA7517;")

    def _poll_backend_state(self):
        """轮询后端状态，变化时提示并刷新右侧标签"""
        from database import backend
        cur = backend.get_state()
        if cur != self._last_backend_state:
            prev = self._last_backend_state
            self._last_backend_state = cur
            if cur == backend.STATE_DEGRADED:
                self._show_info_bar("MySQL 不可用，已切换本地 SQLite 兜底",
                                    "warning", duration=5000)
            elif cur == backend.STATE_ONLINE and prev == backend.STATE_DEGRADED:
                self._show_info_bar("MySQL 已恢复在线", "success", duration=3000)
            self._refresh_db_status_text()

    def _db_test_cfg(self) -> dict:
        """从 settings 读取 mysql_sync 连接配置（敏感字段透明解密）"""
        try:
            from core.secrets import decrypt_settings
            raw = self._load_settings()
            if isinstance(raw, dict) and "mysql_sync" in raw:
                dec = decrypt_settings(raw)
                return dict(dec.get("mysql_sync") or {})
            return dict(raw.get("mysql_sync") or {})
        except Exception:
            return {}

    def _on_db_status_clicked(self):
        """左键点击数据库状态：后台测试 MySQL 连通性并提示"""
        if getattr(self, "_db_test_worker", None) and self._db_test_worker.isRunning():
            self._show_info_bar("已有测试进行中，请稍候", "warning", duration=2000)
            return
        cfg = self._db_test_cfg()
        from workers.mysql_sync_worker import MysqlTestWorker
        self._db_test_worker = MysqlTestWorker(cfg, self)
        self._db_test_worker.finished.connect(self._on_db_test_done)
        self._db_test_worker.start()
        self._append_log("[数据库] 正在测试连接...")

    def _on_db_test_done(self, ok, msg):
        self._refresh_db_status_text()
        if ok:
            self._append_log(f"[数据库] 连接成功: {msg}")
            self._show_info_bar(msg, "success", title="MySQL 连接成功",
                                duration=3000)
        else:
            self._append_log(f"[数据库] 连接失败: {msg}")
            self._show_info_bar(msg, "error", title="MySQL 连接失败",
                                duration=4000)

    def _on_open_db_settings(self):
        """右键点击数据库状态：打开数据库设置对话框（复用 MysqlSyncCard）"""
        from windows.mysql_sync_card import MysqlSyncCard
        card = MysqlSyncCard(self, sync_scope="ops")
        card.load()
        dlg = _DbSettingsDialog(card, self)
        dlg.exec()

    def connect_signals(self):
        """连接信号和槽"""
        # 按钮点击事件
        self.ui.flush.clicked.connect(self.on_flush_clicked)
        self.ui.start.clicked.connect(self.on_start_clicked)
        self.ui.open_daily.clicked.connect(self.on_open_daily_clicked)
        self.ui.write_table.clicked.connect(self.on_open_dir_clicked)
        self.ui.pause_btn.clicked.connect(self._on_pause_clicked)
        # 球桌管理面板入口
        self.ui.table_panel_btn.clicked.connect(lambda: QTimer.singleShot(0, self._on_open_table_panel))
        # 售后面板入口（右侧入口组）
        self.ui.btn_aftersale.clicked.connect(
            lambda: QTimer.singleShot(0, self._on_open_aftersale))
        # 数据库状态标签：左键测连通性 / 右键进数据库设置
        self.ui.db_status_label.leftClicked.connect(self._on_db_status_clicked)
        self.ui.db_status_label.rightClicked.connect(
            lambda: QTimer.singleShot(0, self._on_open_db_settings))
        # 列表项选择事件
        self.ui.id_list.currentItemChanged.connect(self._on_id_current_changed)
        self.ui.local_video_list.currentItemChanged.connect(self._on_video_current_changed)
        self.ui.log_list.itemClicked.connect(self.on_log_selected)
        self.ui.log_list.itemDoubleClicked.connect(self.on_log_double_clicked)

        # 日期改变时重新加载第二列
        self.ui.date.dateChanged.connect(self._on_date_changed)

        # 程序下拉框切换时自动保存选择
        self.ui.choose_exe.currentTextChanged.connect(self._on_exe_changed)

        # 右键菜单信号
        self.ui.id_list.customContextMenuRequested.connect(self._id_list_context_menu)
        self.ui.log_list.customContextMenuRequested.connect(self._log_list_context_menu)
        self.ui.local_video_list.customContextMenuRequested.connect(self._local_video_list_context_menu)

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
        self.ui.p2p_rdp_btn.clicked.connect(self._on_rdp_btn_clicked)

        # 启动三端按钮
        self.ui.start_three_btn.clicked.connect(self.on_start_three_clicked)

        # 写入台账按钮（打开台账面板并预填当前球桌会话，表单确认后入库）
        self.ui.btn_write_table.clicked.connect(self._on_open_ledger)

        # 帧数输入框回车确认：恢复焦点到原位，便于空格直接播放
        self.ui.input_frame.returnPressed.connect(self._on_frame_input_confirmed)

        # 设备列表实时搜索（搜索框控件在 autowork_with_table.py 中创建）
        self.ui.id_search.textChanged.connect(self._on_id_search_debounce)
        # 日志文件列表实时搜索
        self.ui.video_search.textChanged.connect(self._on_video_search_debounce)
        # 远程面板列表实时搜索
        self.ui.p2p_search.textChanged.connect(self._on_p2p_search_changed)
        # 搜索防抖定时器（150ms 内的连续输入合并为一次过滤）
        self._search_debounce_timer = QTimer(self)
        self._search_debounce_timer.setInterval(150)
        self._search_debounce_timer.setSingleShot(True)
        self._search_debounce_callback = None
        self._search_debounce_timer.timeout.connect(self._search_debounce_fire)
        # Ctrl+F 切换搜索框显示/隐藏，Esc 隐藏
        self._id_search_sc = QShortcut(QKeySequence('Ctrl+F'), self)
        self._id_search_sc.activated.connect(self._on_search_shortcut)
        self._id_search_esc = QShortcut(QKeySequence('Escape'), self)
        self._id_search_esc.activated.connect(self._hide_all_search)

        # A1: 监听 SFTP 下载完成全局信号（方法内延迟 import，避免循环依赖）
        self._connect_sftp_download_signal()

        # 工具栏改造（刷新图标化 / 配置按钮迁移 / 日期步进键）须在全部信号连接后执行
        self._upgrade_toolbar()

    # ==================== 日志智能自动滚动 ====================

    _LOG_MAX_LINES = 5000  # 日志控件最大行数，超出后截断旧内容

    def _init_log_auto_scroll(self):
        """初始化日志控件的智能自动滚动功能"""
        self._log_at_bottom = True
        self._log_scroll_timer = QTimer(self)
        self._log_scroll_timer.setSingleShot(True)
        self._log_scroll_timer.setInterval(1000)
        self._log_scroll_timer.timeout.connect(self._scroll_log_to_bottom)
        self.ui.show_log.verticalScrollBar().valueChanged.connect(self._on_log_scroll_changed)
        # 批量追加定时器：将 50ms 内的多次 _append_log 合并为一次 UI 更新
        self._log_batch_timer = QTimer(self)
        self._log_batch_timer.setInterval(50)
        self._log_batch_timer.setSingleShot(True)
        self._log_batch_timer.timeout.connect(self._flush_log_batch)
        self._log_batch_buf: list[str] = []

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
        """向日志控件追加文本，带批量合并 + 智能自动滚动 + 行数上限"""
        self._log_batch_buf.append(text)
        if not self._log_batch_timer.isActive():
            self._log_batch_timer.start()

    def _flush_log_batch(self):
        """将缓冲区内所有日志一次性写入控件（富文本着色 + 命中通知）"""
        if not self._log_batch_buf:
            return
        # 拷贝后清空：直接 clear() 会把引用同源的 lines 一并清空
        lines = list(self._log_batch_buf)
        self._log_batch_buf.clear()

        # 逐行写入 + 字符格式着色
        # （不用 insertHtml：HTML fragment 首块会与目标块合并，导致丢样式/与上行串行）
        hit_names = []
        cur = self.ui.show_log.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        # 光标不在块首则先补一个块：否则 insertText 会与末行尾字符粘在同一行
        if not cur.atBlockStart():
            cur.insertBlock()
        for line in lines:
            color, name, notify = _match_log_rule(line, self._log_rules)
            if notify and name not in hit_names:
                hit_names.append(name)
            fmt = QTextCharFormat()
            if color:
                fmt.setForeground(QColor(color))
            cur.setCharFormat(fmt)
            cur.insertText(line)
            cur.insertBlock()
        self.ui.show_log.setTextCursor(cur)

        # 行数上限截断（避免无限增长导致内存/布局开销）
        doc = self.ui.show_log.document()
        if doc.blockCount() > self._LOG_MAX_LINES:
            # 从文档头选中「超出上限的行数」，整体移除最早的历史行
            cursor = self.ui.show_log.textCursor()
            cursor.movePosition(cursor.Start)
            cursor.movePosition(cursor.Down, cursor.KeepAnchor,
                                doc.blockCount() - self._LOG_MAX_LINES)
            cursor.removeSelectedText()
            cursor.deleteChar()  # 删除选区末尾残留的换行符

        # 命中通知规则 → InfoBar（每规则 10s 静默期，避免刷屏）
        now = time.time()
        for name in hit_names:
            if now - self._rule_last_notify.get(name, 0) >= 10:
                self._rule_last_notify[name] = now
                self._show_info_bar(f"日志命中「{name}」规则", "warning",
                                    duration=3000)

        if self._log_at_bottom:
            self._scroll_log_to_bottom()
        else:
            self._log_scroll_timer.start()

    def _show_info_bar(self, message, message_type="info", title=None, duration=2500):
        """弹出 Fluent InfoBar 消息条（右下角），与 _append_log 互不干涉。
        参数:
            message: 消息内容
            message_type: 'success' / 'info' / 'warning' / 'error'
            title: 标题（默认按类型自动生成）
            duration: 显示时长(ms)，<=0 表示常驻不自动关闭
        """
        show_info_bar(message, message_type=message_type, title=title,
                      duration=duration, parent=self)

    # ==================== 列表加载 ====================

    def _update_empty_hint(self, list_widget):
        """更新列表空状态提示：无可见项时显示灰色占位文字，有可见项时隐藏"""
        hint = getattr(list_widget, '_empty_hint', None)
        if hint is None:
            return
        n = list_widget.count()
        if n == 0:
            hint.show()
            return
        for i in range(n):
            if not list_widget.item(i).isHidden():
                hint.hide()
                return
        hint.show()

    def _load_exe_list(self):
        """加载 snooker/bin64 目录下的 SnookerTracking*.exe 到程序下拉框"""
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
        try:
            for item in os.listdir(videos_dir):
                item_path = os.path.join(videos_dir, item)
                if os.path.isdir(item_path):
                    device_codes.append(item)
        except OSError as e:
            self._append_log(f"[警告] 无法读取目录 {videos_dir}: {e}")
            self._show_info_bar(f"无法读取 videos 目录: {e}", "warning", duration=4000)
            return

        if not device_codes:
            self._append_log(f"[警告] videos 目录下没有找到设备文件夹")
            self._update_empty_hint(self.ui.id_list)
            return

        # 清空并添加设备代码列表（自然排序：数字段按数值比较）
        self.ui.id_list.setUpdatesEnabled(False)
        self.ui.id_list.clear()
        for code in sorted(device_codes, key=natural_sort_key):
            self.ui.id_list.addItem(code)
        self.ui.id_list.setUpdatesEnabled(True)

        self._update_empty_hint(self.ui.id_list)
        self._append_log(f"[设备] 找到 {len(device_codes)} 个设备代码")

    def _load_videos_for_device(self, device_code):
        """根据设备代码和选中日期加载日志文件到 local_video_list（第二列）

        查找路径：
        1. videos/{device_code}/{date_str}/ 下的 *.txt, *.log（原有逻辑）
        2. videos/{device_code}/ 根目录下文件名以 YYYYMMDD_ 开头的 *.txt, *.log（新增）
        """
        videos_dir = self.videos_dir
        device_dir = os.path.join(videos_dir, device_code)

        if not os.path.exists(device_dir):
            self._append_log(f"[警告] 设备目录不存在: {device_dir}")
            return

        # 获取选中日期，构建日期子目录路径
        date_str = self._get_selected_date_str()
        date_dir = os.path.join(device_dir, date_str)

        # 清空第二列
        self.ui.local_video_list.clear()

        log_files = []

        # 路径1：日期子目录下的 txt 和 log 文件
        if os.path.exists(date_dir):
            log_files += glob.glob(os.path.join(date_dir, '*.txt'))
            log_files += glob.glob(os.path.join(date_dir, '*.log'))

        # 路径2：设备根目录下文件名以 YYYYMMDD_ 开头的日志文件
        date_prefix = date_str.replace('-', '')  # e.g. "20251128"
        try:
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
        except OSError as e:
            self._append_log(f"[警告] 扫描设备目录失败: {device_code} ({e})")
            self._show_info_bar(f"扫描 {device_code} 设备目录失败: {e}", "warning", duration=4000)

        if not log_files:
            self._update_empty_hint(self.ui.local_video_list)
            self._append_log(f"[提示] {device_code} 下没有 {date_str} 的日志 (查找路径: {date_dir} 及设备根目录)")
            self._show_info_bar(f"{device_code} 下未找到 {date_str} 的日志", "warning")
            return

        self.ui.local_video_list.setUpdatesEnabled(False)
        for log_path in sorted(log_files):
            # 只显示文件名，如 20260705_131009.log
            self.ui.local_video_list.addItem(os.path.basename(log_path))
        self.ui.local_video_list.setUpdatesEnabled(True)

        # 列表重载后重新应用搜索过滤
        self._on_video_search_changed(self.ui.video_search.text())
        self._update_empty_hint(self.ui.local_video_list)

        self._append_log(f"[日志目录] {device_code}/{date_str} 下有 {len(log_files)} 个日志文件")

    def _load_logs_for_device(self, device_code):
        """初始化第三列为空，等待点击日志后展示内容"""
        self.ui.log_list.clear()
        self._update_empty_hint(self.ui.log_list)

    # ==================== 列表搜索 (Ctrl+F) ====================

    def _on_id_search_debounce(self, text):
        """id_search 输入防抖：150ms 内的连续输入合并为一次过滤"""
        self._search_debounce_callback = lambda: self._on_id_search_changed(text)
        self._search_debounce_timer.start()

    def _on_video_search_debounce(self, text):
        """video_search 输入防抖"""
        self._search_debounce_callback = lambda: self._on_video_search_changed(text)
        self._search_debounce_timer.start()

    def _search_debounce_fire(self):
        """防抖到期：执行最近一次注册的搜索回调（只跑一次）"""
        if self._search_debounce_callback:
            self._search_debounce_callback()
            self._search_debounce_callback = None

    def _on_search_shortcut(self):
        """Ctrl+F：根据焦点所在列表切换对应搜索框的显示/隐藏"""
        focused = self.focusWidget()
        # 焦点在日志内容列表（或其过滤框）→ 操作 log_filter（第三态）
        if focused is self.ui.log_list or focused is self.ui.log_filter:
            if self.ui.log_filter.isVisible():
                self._hide_log_filter()
            else:
                self._log_filter_shown = True
                self.ui.log_filter.show()
                self.ui.log_filter.setFocus()
        # 焦点在日志文件列表（或其搜索框）→ 操作 video_search
        elif (self.ui.local_video_list.isAncestorOf(focused)
                or focused is self.ui.local_video_list
                or focused is self.ui.video_search):
            if self.ui.video_search.isVisible():
                self._hide_video_search()
            else:
                self.ui.video_search.show()
                self.ui.video_search.setFocus()
        # 焦点在设备列表（或其搜索框）及其他情况 → 操作 id_search
        else:
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
        for i in range(self.ui.id_list.count()):
            self.ui.id_list.item(i).setHidden(False)
        self._update_empty_hint(self.ui.id_list)

    def _hide_video_search(self):
        """隐藏日志文件搜索框，清空内容并恢复完整列表"""
        self.ui.video_search.blockSignals(True)
        self.ui.video_search.clear()
        self.ui.video_search.blockSignals(False)
        self.ui.video_search.hide()
        for i in range(self.ui.local_video_list.count()):
            self.ui.local_video_list.item(i).setHidden(False)
        self._update_empty_hint(self.ui.local_video_list)

    def _hide_log_filter(self):
        """隐藏日志内容过滤框，清空关键字并恢复全部行显示"""
        self._log_filter_shown = False
        self.ui.log_filter.blockSignals(True)
        self.ui.log_filter.clear()
        self.ui.log_filter.blockSignals(False)
        self.ui.log_filter.hide()
        for i in range(self.ui.log_list.count()):
            self.ui.log_list.item(i).setHidden(False)
        self._update_empty_hint(self.ui.log_list)

    def _hide_all_search(self):
        """隐藏所有搜索框（Esc 触发）"""
        self._hide_id_search()
        self._hide_video_search()
        self._hide_log_filter()

    def _on_id_search_changed(self, text):
        """实时过滤设备列表：不区分大小写子串匹配，用 setHidden 控制显隐（不重建列表）"""
        kw = text.strip().lower()
        for i in range(self.ui.id_list.count()):
            item = self.ui.id_list.item(i)
            item.setHidden(bool(kw) and kw not in item.text().lower())
        self._update_empty_hint(self.ui.id_list)

    def _on_video_search_changed(self, text):
        """实时过滤日志文件列表：不区分大小写子串匹配"""
        kw = text.strip().lower()
        for i in range(self.ui.local_video_list.count()):
            item = self.ui.local_video_list.item(i)
            item.setHidden(bool(kw) and kw not in item.text().lower())
        self._update_empty_hint(self.ui.local_video_list)

    # ==================== 第三列日志内容过滤 (B3) ====================

    def _init_log_filter(self):
        """B3: 在第三列（log_list）顶部安装过滤输入框。

        autowork_with_table 中 log_list 被两种布局直接引用，这里给它包一层
        容器（过滤框在上、列表在下），并钩住 switch_layout：布局切换后
        自动把容器重新放回 log_list 原本的位置，保证过滤框不丢失。
        """
        from autowork_with_table import _create_search_line_edit
        filt = _create_search_line_edit()
        filt.setObjectName(u"log_filter")
        filt.setPlaceholderText("过滤日志内容...")
        filt.setClearButtonEnabled(True)
        filt.setVisible(False)
        self.ui.log_filter = filt

        container = QWidget()
        container.setObjectName(u"log_list_container")
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(filt)
        # 注意：log_list 不在此处装入容器——若先装入，下方
        # _place_log_list_container 会因 parent is container 直接 return，
        # 导致容器永远不被放回布局（初始面板布局时日志列表丢失）
        self.ui.log_list_container = container

        filt.textChanged.connect(self._on_log_filter_debounce)
        self._log_filter_shown = False  # 过滤框显示状态（布局切换后用于恢复）
        self._place_log_list_container()

        # 钩住布局切换：原 switch_layout 会把 log_list 直接搬进新布局，
        # 切换完成后需重新装回容器并放回原位
        orig_switch = self.ui.switch_layout

        def _switch_layout_with_log_container(classic=False):
            orig_switch(classic)
            self._place_log_list_container()
            # 容器曾被旧布局 removeWidget 连带标记为隐藏，
            # 需显式恢复可见（新布局激活时其他控件会被自动刷新，
            # 但动态插入的容器不在此列）
            container = self.ui.log_list_container
            container.show()
            self.ui.log_filter.setVisible(
                getattr(self, '_log_filter_shown', False))

        self.ui.switch_layout = _switch_layout_with_log_container

    def _place_log_list_container(self):
        """把 log_list 装回过滤容器，并将容器放回 log_list 原位置（兼容两种布局）"""
        container = self.ui.log_list_container
        log_list = self.ui.log_list
        parent = log_list.parentWidget()
        if parent is container:
            return
        lay = container.layout()
        if isinstance(parent, QSplitter):
            # 经典布局：log_list 直接挂在 splitter 中，记录索引原位放回
            idx = parent.indexOf(log_list)
            lay.addWidget(log_list)
            parent.insertWidget(idx, container)
        elif parent is not None and parent.layout() is not None:
            # 面板布局：log_list 在带 header 的容器 layout 末尾
            lay.addWidget(log_list)
            parent.layout().addWidget(container, 1)

    def _on_log_filter_debounce(self, text):
        """log_filter 输入防抖：150ms 内的连续输入合并为一次过滤"""
        self._search_debounce_callback = lambda: self._on_log_filter_changed(text)
        self._search_debounce_timer.start()

    def _on_log_filter_changed(self, text):
        """实时过滤日志内容列表：不区分大小写子串匹配（同构第一、二列）"""
        kw = text.strip().lower()
        for i in range(self.ui.log_list.count()):
            item = self.ui.log_list.item(i)
            item.setHidden(bool(kw) and kw not in item.text().lower())
        self._update_empty_hint(self.ui.log_list)

    def _reapply_log_filter(self):
        """日志内容重载后重新应用当前过滤条件"""
        filt = getattr(self.ui, 'log_filter', None)
        if filt is not None:
            self._on_log_filter_changed(filt.text())

    def _on_p2p_search_changed(self, text):
        """实时过滤远程面板 visitor/服务器列表：不区分大小写子串匹配"""
        kw = text.strip().lower()
        for i in range(self.ui.p2p_visitor_list.count()):
            item = self.ui.p2p_visitor_list.item(i)
            item.setHidden(bool(kw) and kw not in item.text().lower())

    # ==================== 日历预热 ====================

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

    # ==================== 日期/帧数工具 ====================

    def _restore_frame_offset(self):
        """B5: 启动时从 settings.json 恢复帧偏移，并挂接持久化（防抖写入，
        复用 settings_mixin 的缓存读写机制）"""
        settings = self._load_settings()
        saved = settings.get("frame_offset")
        try:
            if saved is not None:
                self.ui.input_frame.setText(str(int(saved)))
        except (TypeError, ValueError):
            pass
        # 单次防抖 500ms：输入停止后才落盘一次，因为 _save_settings 每次都要走
        # DPAPI 解密旧文件→合并→再加密的全流程，每敲一键就写盘开销太大
        self._frame_offset_save_timer = QTimer(self)
        self._frame_offset_save_timer.setInterval(500)
        self._frame_offset_save_timer.setSingleShot(True)
        self._frame_offset_save_timer.timeout.connect(self._save_frame_offset)
        self.ui.input_frame.textChanged.connect(self._frame_offset_save_timer.start)

    def _save_frame_offset(self):
        """帧偏移输入框防抖结束：合法整数才写入 settings.json"""
        try:
            val = int(self.ui.input_frame.text().strip())
        except ValueError:
            return
        if val != self._load_settings().get("frame_offset"):
            self._save_settings({"frame_offset": val})
            self._append_log(f"[配置] 已保存帧偏移: {val}")

    def _get_selected_date_str(self):
        """获取日期选择器中的日期，格式如 2026-07-05"""
        qdate = self.ui.date.date
        date_str = qdate.toString("yyyy-MM-dd")
        return date_str

    def _get_frame_input_value(self):
        """获取输入框中的帧数值，默认 400"""
        try:
            return int(self.ui.input_frame.text().strip())
        except (ValueError, AttributeError):
            return 400

    def _compute_video_start_frame(self, log_frame_id):
        """根据单选按钮模式计算 video_start_frame
        - 帧前: log_frame_id - 输入值
        - 帧后: log_frame_id + 输入值
        - 自定义: 输入值
        """
        if self.ui.input_frame_before.isChecked():
            offset = self._get_frame_input_value()
            result = log_frame_id - offset
            # 起始帧不允许为负：负值会直接写进 video_start_frame，
            # 三端程序按负帧定位会失败，钳到 0 保底
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
            # 兜底分支：三个单选全未选中（如选择被程序性清除）时的防御性默认，
            # 按「帧前」语义处理，这是刻意兜底而不是复制粘贴的疏忽
            offset = self._get_frame_input_value()
            result = log_frame_id - offset
            if result < 0:
                self._append_log(
                    f"  [警告] 帧前偏移后起始帧为负值({result})，已修正为 0。"
                    f"log_frame_id={log_frame_id}, offset={offset}")
                result = 0
            return result

    # ==================== 事件处理 ====================

    @Slot()
    def on_flush_clicked(self):
        """刷新按钮点击事件"""
        self._append_log("\n[操作] 刷新数据...")

        # 先记住当前选中的设备代码和程序
        current_device = self.ui.id_list.currentItem()
        saved_device_code = current_device.text() if current_device else None
        saved_exe = self.ui.choose_exe.currentText()

        # 1. 重新扫描可执行程序下拉框（屏蔽信号，避免 clear/addItem 触发 _on_exe_changed 误保存）
        self.ui.choose_exe.blockSignals(True)
        self._load_exe_list()
        # 恢复程序选择
        for i in range(self.ui.choose_exe.count()):
            if self.ui.choose_exe.itemText(i) == saved_exe:
                self.ui.choose_exe.setCurrentIndex(i)
                break
        self.ui.choose_exe.blockSignals(False)

        # 2. 重新扫描设备列表（第一列）
        self._load_device_list()

        # 3. 恢复之前选中的设备，并重新加载其日志目录（第二列）
        if saved_device_code:
            for i in range(self.ui.id_list.count()):
                if self.ui.id_list.item(i).text() == saved_device_code:
                    self.ui.id_list.setCurrentItem(self.ui.id_list.item(i))
                    self._load_videos_for_device(saved_device_code)
                    break
            self.ui.log_list.clear()

        self._append_log("[刷新] 完成")
        self._show_info_bar("数据刷新完成", "success")

    @Slot()
    def on_open_daily_clicked(self):
        """打开 CPP 日志文件"""
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

    # ==================== 写入台账 ====================

    def _current_ledger_context(self) -> dict:
        """收集当前球桌会话上下文（供台账面板填写录入页预填）。

        球房取当前选中设备代码；视频名取第二列当前日志文件名；
        帧数取帧输入框（默认 400）；署名取 settings newlog_target_name。
        分类默认「问题」（主界面入口通常记问题，面板内可修改）。
        """
        device_code = (self.ui.id_list.currentItem().text()
                       if self.ui.id_list.currentItem() else "")
        video_item = self.ui.local_video_list.currentItem()
        video_name = video_item.text() if video_item else ""
        try:
            frame = str(int(self.ui.input_frame.text().strip() or 400))
        except ValueError:
            frame = "400"
        sig = ""
        try:
            from core.app_paths import get_app_dir
            import json
            with open(os.path.join(get_app_dir(), "settings.json"),
                      "r", encoding="utf-8") as f:
                sig = str(json.load(f).get("newlog_target_name", "") or "")
        except Exception:
            pass
        return {
            "category": "问题",
            "room_name": device_code,
            "video_name": video_name,
            "frame": frame,
            "signer": sig,
        }

    @Slot()
    def _on_open_ledger(self):
        """写入台账：打开台账面板并预填当前球桌会话（表单确认后入库）

        替代旧的「保存对话框 + 本地 xlsx 追加」流程：数据经
        ledger_db 双后端路由写入（MySQL 开启时即服务器），面板内
        可编辑字段、筛选/分页/统计，多人协作刷新可见。
        """
        if not self.ui.id_list.currentItem():
            self._append_log("[提示] 请先选择设备代码")
            self._show_info_bar("请先选择设备代码", "warning")
            return
        from windows.ledger_panel import LedgerPanelWindow
        if not hasattr(self, '_ledger_panel') or self._ledger_panel is None:
            # 不传 parent：避免成为主窗口的 owned window 而始终盖在主窗口之上
            self._ledger_panel = LedgerPanelWindow()
            self._ledger_panel.destroyed.connect(
                lambda: setattr(self, '_ledger_panel', None))
        self._ledger_panel.show()
        self._ledger_panel.raise_()
        self._ledger_panel.activateWindow()
        ctx = self._current_ledger_context()
        self._ledger_panel.open_entry_with_context(ctx)
        self._append_log(
            f"[写入台账] 已打开台账面板并预填会话: 球房={ctx['room_name']} "
            f"视频={ctx['video_name']} 帧={ctx['frame']}")

    @Slot()
    def _open_config_file(self, name: str):
        """打开指定配置文件（菜单栏「配置」下拉项 / Ctrl+, 快捷键）"""
        if name == "settings.json":
            path = self._get_settings_path()
        elif name == "cfg.json":
            path = os.path.join(self.exe_dir, "cfg.json")
        else:  # frpc_xtcp.toml
            path = os.path.join(self._get_app_dir(), "frpc_xtcp.toml")

        if not os.path.exists(path):
            self._append_log(f"[配置] 文件不存在: {path}")
            return

        os.startfile(path)
        self._append_log(f"[配置] 已打开: {path}")

    def _step_date(self, delta_days: int):
        """日期步进：负数前移、正数后移；setDate 触发 dateChanged → 现有加载链路"""
        self.ui.date.setDate(self.ui.date.date.addDays(delta_days))

    def _upgrade_toolbar(self):
        """工具栏接线补充（须在 connect_signals 之后执行）：日期步进键点击。
        刷新按钮与日期步进键已在 Ui_MainWindow 中直接创建为图标按钮，
        不在运行时改动布局结构（FlowLayout 的 replaceWidget/takeAt 删除
        会造成索引错位，控件重叠）。
        「配置」按钮已迁移至菜单栏「配置」下拉菜单。
        """
        self.ui.date_prev.clicked.connect(lambda _=False: self._step_date(-1))
        self.ui.date_next.clicked.connect(lambda _=False: self._step_date(1))

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
            alt_path = os.path.join(self.videos_dir, device_code, log_filename)
            if os.path.exists(alt_path):
                full_log_path = alt_path

        # 保存路径供回调使用
        self._current_log_path = full_log_path

        # C6: 用时间戳前缀反查 kd_status，状态栏显示对应 kd 记录分类
        self._update_kd_record_status(device_code, log_filename)

        # 取消前一个正在运行的 worker（用户快速切换场景）
        if self._log_worker is not None and self._log_worker.isRunning():
            self._log_worker.requestInterruption()
            self._log_worker.wait(500)

        # 清空列表并显示加载提示
        self.ui.log_list.clear()
        self.ui.log_list.addItem("加载中...")

        # 启动后台加载
        worker = _LogLoadWorker(full_log_path, self._log_rules)
        worker.loaded.connect(self._on_log_loaded)
        worker.error.connect(self._on_log_load_error)
        self._log_worker = worker  # 防止 GC
        worker.start()

    # ==================== C6: 日志↔kd 记录双向跳转 ====================

    # kd 分类 → 状态栏醒目色（精度/问题需突出提示，其余分类用默认色）
    _KD_STATUS_ACCENTS = {"accuracy_files": SEMANTIC["warning"],
                          "already_files": SEMANTIC["danger"]}

    def _update_kd_record_status(self, device_code, log_filename):
        """C6 正向：选中日志时用时间戳前缀反查 kd_status 文件分类并展示

        kd 照片与本地日志共享时间戳前缀（clip_base_name 机制），单分区
        单设备 LIKE 预筛毫秒级，同步执行即可；任何异常静默降级不影响
        日志加载主流程。
        """
        try:
            from workers.collect_worker import clip_base_name
            from database import table_db
            base = clip_base_name(log_filename)
            if not base:
                return
            date_str = self._get_selected_date_str()
            t0 = time.perf_counter()
            info = table_db.find_kd_file_status(device_code, date_str, base)
            cost_ms = (time.perf_counter() - t0) * 1000
            if cost_ms > 50:
                self._append_log(f"[提示] kd 记录反查耗时 {cost_ms:.0f}ms，建议转后台执行")
            if info:
                cn = info.get("category_cn") or info.get("category") or ""
                accent = self._KD_STATUS_ACCENTS.get(info.get("category"))
                self._show_kd_status_message(f"对应 kd 记录：已标【{cn}】", accent)
                if accent:
                    # 已标精度/问题：InfoBar 醒目提示
                    self._show_info_bar(
                        f"对应 kd 记录：已标【{cn}】（{info.get('file_name')}）",
                        "warning", title="kd 记录", duration=3500)
            else:
                self._show_kd_status_message("未找到对应 kd 记录")
        except Exception as e:
            self._append_log(f"[警告] kd 记录反查失败: {e}")

    def _show_kd_status_message(self, msg, accent=None, timeout=6000):
        """状态栏显示 kd 反查结果；accent 非空时临时醒目着色，超时恢复"""
        self._show_status_message(msg, timeout)
        if accent:
            self._status_message.setStyleSheet(f"color: {accent}; font-weight: 600;")
            QTimer.singleShot(timeout, self._status_message.clearStyleSheet)

    def focus_log_file(self, device_dir, date_str, log_fname):
        """C6 反向跳转入口：切换到指定设备+日期并选中日志文件

        复用现有选中链路：id_list 选中 → on_id_selected 加载第二列；
        日期变化 → dateChanged 重载；最后 _locate_video_item 定位选中
        （setCurrentItem 触发 on_video_selected 加载日志内容）。
        """
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

        # 1. 选中设备（不在列表时刷新一次重试）
        target = None
        for _ in range(2):
            for i in range(self.ui.id_list.count()):
                if self.ui.id_list.item(i).text() == device_dir:
                    target = self.ui.id_list.item(i)
                    break
            if target is not None:
                break
            self._load_device_list()
        if target is None:
            self._show_info_bar(f"设备列表中未找到: {device_dir}", "warning")
            return
        if self.ui.id_list.currentItem() is not target:
            self.ui.id_list.setCurrentItem(target)

        # 2. 对齐日期（dateChanged 会自动重载第二列）
        reload_pending = True
        qdate = QDate.fromString(str(date_str), "yyyy-MM-dd")
        if qdate.isValid() and qdate != self.ui.date.date:
            self.ui.date.setDate(qdate)
            reload_pending = False
        if reload_pending and self.ui.id_list.currentItem() is target:
            # 设备/日期均未变化 → 手动刷新第二列
            self._load_videos_for_device(device_dir)

        # 3. 定位并选中日志文件（复用 SFTP 下载联动的定位逻辑）
        # 先清除第二列搜索过滤，避免目标项被隐藏而无法定位
        self._on_video_search_changed("")
        search = getattr(self.ui, 'video_search', None)
        if search is not None and search.text():
            search.blockSignals(True)
            search.clear()
            search.blockSignals(False)
        self._locate_video_item(str(log_fname))

    def _on_log_loaded(self, lines_data, line_count, truncated, size_mb):
        """日志加载完成，填充 UI"""
        self.ui.log_list.setUpdatesEnabled(False)
        self.ui.log_list.clear()
        for line, color, _name, _notify in lines_data:
            item = QListWidgetItem(line)
            if color:
                item.setForeground(QBrush(QColor(color)))
            self.ui.log_list.addItem(item)
        self.ui.log_list.setUpdatesEnabled(True)
        self._update_empty_hint(self.ui.log_list)
        # 日志文件命中通知规则 → InfoBar（仅 notify=True 的规则，复用 10s 防抖）
        now = time.time()
        for _line, _color, name, notify in lines_data:
            if notify and name and now - self._rule_last_notify.get(name, 0) >= 10:
                self._rule_last_notify[name] = now
                self._show_info_bar(f"日志文件命中「{name}」规则", "warning",
                                    duration=3000)
        # B3: 重载后重新应用过滤条件
        self._reapply_log_filter()

        if truncated:
            self._append_log(f"[日志内容] 文件 {size_mb:.1f}MB 过大，仅显示尾部 {line_count} 行")
            self._show_info_bar(f"大文件仅加载尾部 {line_count} 行", "warning")
        else:
            self._append_log(f"[日志内容] 已加载 {line_count} 行")
            self._show_info_bar(f"日志已加载 {line_count} 行", "success")
        self._update_status_logs(line_count)
        self._log_worker = None

    def _on_log_load_error(self, error_msg):
        """日志加载失败"""
        self._append_log(f"[错误] 无法读取日志文件: {error_msg}")
        self._show_info_bar(f"无法读取日志文件: {error_msg}", "error")
        self.ui.log_list.clear()
        self._update_empty_hint(self.ui.log_list)
        self._current_log_path = None
        self._log_worker = None

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
        if not self.ui.local_video_list.currentItem():
            self._append_log("[警告] 未选择日志文件")
            return

        log_filename = self.ui.local_video_list.currentItem().text()
        video_name = os.path.splitext(log_filename)[0] + '.mp4'
        # 视频查找路径：优先 videos/videos/，其次 videos/{device_code}/
        video_path_primary = os.path.join(self.videos_dir, "videos", video_name)
        device_code = self.ui.id_list.currentItem().text()
        video_path_device = os.path.join(self.videos_dir, device_code, video_name)
        if os.path.exists(video_path_primary):
            video_path = video_path_primary.replace(os.sep, '/')
        elif os.path.exists(video_path_device):
            video_path = video_path_device.replace(os.sep, '/')
        else:
            video_path = video_path_primary.replace(os.sep, '/')
        self.current_video = video_path

        # 根据单选按钮模式计算实际起始帧
        video_start_frame = self._compute_video_start_frame(log_frame_id)

        # 更新 cfg.json
        self._update_cfg_json(video_path, video_start_frame)

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

    # ==================== SFTP 下载联动 (A1 接收端) ====================

    def _connect_sftp_download_signal(self):
        """A1: 连接 SFTP 下载完成全局信号（延迟 import，避免潜在循环依赖）"""
        try:
            from windows.remote_session.sftp_window import GLOBAL_SIGNALS
        except Exception as e:
            self._append_log(f"[警告] SFTP 下载联动信号连接失败: {e}")
            return
        # 信号可能从 SFTP 面板的其他线程上下文发射，强制排队到主线程执行
        GLOBAL_SIGNALS.file_downloaded.connect(
            self._on_sftp_file_downloaded, Qt.QueuedConnection)

    @Slot(str, str, int)
    def _on_sftp_file_downloaded(self, device_code, file_path, batch_count):
        """A1 接收端槽：刷新设备/日志文件列表并定位高亮刚下载的文件（仅 UI 操作）"""
        fname = os.path.basename(file_path) if file_path else ""
        self._append_log(f"[SFTP] 接收到下载文件: {file_path}")
        self._show_info_bar(f"已接收 SFTP 下载文件 {fname}", "success")

        # 1. 设备目录名非空且不在列表 → 刷新设备列表，然后选中该设备
        reload_pending = True  # 是否需要手动刷新第二列
        if device_code:
            names = [self.ui.id_list.item(i).text()
                     for i in range(self.ui.id_list.count())]
            if device_code not in names:
                self._load_device_list()
            for i in range(self.ui.id_list.count()):
                item = self.ui.id_list.item(i)
                if item.text() == device_code:
                    if self.ui.id_list.currentItem() is not item:
                        # 选中变化 → on_id_selected 会自动加载第二列
                        self.ui.id_list.setCurrentItem(item)
                        reload_pending = False
                    break

        # 2. 日期选择器对齐下载文件所属日期（dateChanged 会自动重载第二列）
        qdate = self._parse_download_file_date(file_path, device_code)
        if qdate is not None and qdate.isValid() and qdate != self.ui.date.date:
            self.ui.date.setDate(qdate)
            reload_pending = False

        # 3. 设备/日期均未变化 → 手动复用现有逻辑刷新第二列
        current_device = self.ui.id_list.currentItem()
        if reload_pending and current_device is not None:
            self._load_videos_for_device(current_device.text())

        # 4. 仅日志类型文件（.log/.txt）才定位高亮，其他类型只刷新不定位
        if fname.lower().endswith(('.log', '.txt')):
            self._locate_video_item(fname)

    def _parse_download_file_date(self, file_path, device_code):
        """从下载路径推断文件所属日期：
        videos/{设备}/YYYY-MM-DD/xxx 或 videos/{设备}/YYYYMMDD_xxx，
        解析失败返回 None（保持日期选择器不变）"""
        if not file_path:
            return None
        if device_code:
            # relpath 跨盘符会抛 ValueError，降级成只解析文件名，
            # 避免下载目标落在别的磁盘时丢掉日期对齐能力
            try:
                rel = os.path.relpath(
                    file_path, os.path.join(self.videos_dir, device_code))
            except ValueError:
                rel = os.path.basename(file_path)
        else:
            rel = os.path.basename(file_path)
        parts = rel.replace('\\', '/').split('/')
        if len(parts) >= 2:
            qdate = QDate.fromString(parts[0], "yyyy-MM-dd")
            if qdate.isValid():
                return qdate
        fname = parts[-1]
        if len(fname) >= 9 and fname[8] == '_':
            qdate = QDate.fromString(fname[:8], "yyyyMMdd")
            if qdate.isValid():
                return qdate
        return None

    def _locate_video_item(self, fname):
        """在第二列定位并闪烁高亮匹配 basename 的项"""
        list_w = self.ui.local_video_list
        target = None
        for i in range(list_w.count()):
            item = list_w.item(i)
            if item.text() == fname and not item.isHidden():
                target = item
                break
        if target is None:
            self._append_log(f"[SFTP] 第二列未找到下载文件: {fname}（可能不属于当前日期）")
            return
        list_w.setCurrentItem(target)
        list_w.scrollToItem(target)
        self._flash_list_item(target)

    def _flash_list_item(self, item, times=4, interval=250):
        """列表项背景色闪烁提示（主题色 ↔ 原背景交替，结束后恢复）"""
        accent = QBrush(QColor(self._theme_color))  # 与主题强调色一致
        orig = item.data(Qt.BackgroundRole)
        state = {'n': 0}

        def _tick():
            state['n'] += 1
            if state['n'] > times:
                item.setData(Qt.BackgroundRole, orig)
                return
            item.setData(Qt.BackgroundRole,
                         accent if state['n'] % 2 == 1 else orig)
            QTimer.singleShot(interval, _tick)

        _tick()

    # ==================== 窗口关闭清理 ====================

    def closeEvent(self, event):
        """主窗口关闭时统一释放所有子进程和远程会话资源，防止孤儿进程"""
        # 0. 关闭运维管理面板（独立窗口，不随主窗口自动销毁）
        panel = getattr(self, '_table_panel', None)
        if panel is not None:
            self._table_panel = None
            try:
                panel.close()
            except (RuntimeError, OSError):
                pass

        # 1. 关闭统一远程会话中心（单一 frpc 进程 + 全局隧道/会话窗口）
        try:
            from core.frp_remote import get_session_manager
            get_session_manager().shutdown()
        except (RuntimeError, OSError):
            pass

        # 2. 保存远程会话信息（在关闭窗口之前）
        # 必须先落盘再走第 3 步：_save_remote_sessions 是从 win._panels 提取会话的，
        # 而 win.close() 触发的 shutdown_all 会清空面板列表，顺序反了就存不到任何会话
        self._save_remote_sessions()

        # 3. 关闭远程会话标签容器（触发各面板 shutdown：SFTP/SSH/RDP）
        win = getattr(self, '_remote_session_window', None)
        if win is not None:
            self._remote_session_window = None
            try:
                win.close()  # closeEvent → shutdown_all()
            except (RuntimeError, OSError):
                pass

        # 4. 终止正在运行的 SnookerTracking 程序
        rp = getattr(self, 'running_process', None)
        if rp is not None:
            self.running_process = None
            try:
                rp.kill()
                rp.waitForFinished(1000)
            except (RuntimeError, OSError):
                pass

        # 5. 终止三端进程
        for attr in ('_tracking_process', '_backend_process', '_front_process'):
            p = getattr(self, attr, None)
            if p is not None:
                setattr(self, attr, None)
                try:
                    if p.state() != QProcess.NotRunning:
                        p.kill()
                        p.waitForFinished(1000)
                except (RuntimeError, OSError):
                    pass

        # 6. 清理 TCP worker
        tw = getattr(self, '_tcp_worker', None)
        if tw is not None:
            self._tcp_worker = None
            try:
                if tw.isRunning():
                    # lambda 包装必须：PySide6 C++ 直连不持有 Python 引用
                    tw.finished.connect(lambda w=tw: w.deleteLater())
                else:
                    tw.deleteLater()
            except RuntimeError:
                pass

        # 7. 清理日志加载 worker
        lw = getattr(self, '_log_worker', None)
        if lw is not None:
            self._log_worker = None
            try:
                if lw.isRunning():
                    lw.requestInterruption()
                    lw.wait(1000)
            except RuntimeError:
                pass

        # 7b. 清理批量整理 worker（NewLog 整理 / 打包上传，中断并短等待）
        for attr in ('_newlog_worker', '_newlog_upload_worker', '_single_video_worker'):
            nw = getattr(self, attr, None)
            if nw is not None:
                setattr(self, attr, None)
                try:
                    if nw.isRunning():
                        nw.requestInterruption()
                        nw.wait(1000)
                except (RuntimeError, OSError):
                    pass

        # 8. 终止异步解码进程
        dp = getattr(self, '_decode_process', None)
        if dp is not None:
            self._decode_process = None
            try:
                if dp.state() != QProcess.NotRunning:
                    dp.kill()
                    dp.waitForFinished(1000)
            except (RuntimeError, OSError):
                pass

        # 9. 等待上传收集 worker 结束（文件复制任务，短等待即可）
        for cw in list(getattr(self, '_upload_collect_workers', [])):
            try:
                if cw.isRunning():
                    cw.wait(1500)
            except (RuntimeError, OSError):
                pass
        self._upload_collect_workers = []

        super().closeEvent(event)
