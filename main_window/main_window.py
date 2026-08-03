# -*- coding: utf-8 -*-
"""MainWindow 主类：组合所有 Mixin，包含初始化、信号连接、日志、列表加载、事件处理"""

import os
import re
import glob

from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout,
    QListWidgetItem)
from PySide6.QtCore import Slot, QTimer, Qt, QDate, QProcess
from PySide6.QtGui import QColor, QBrush, QShortcut, QKeySequence
from qfluentwidgets import (InfoBar, InfoBarPosition, FluentTitleBar,
    MessageBoxBase, BodyLabel, ComboBox)
from qfluentwidgets.window.fluent_window import FluentWindowBase

from autowork_with_table import Ui_MainWindow
from core.utils import natural_sort_key

from .settings_mixin import SettingsMixin
from .process_mixin import ProcessMixin
from .remote_mixin import RemoteMixin
from .ui_mixin import UIMixin
from qfluentwidgets import Dialog


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
        # 从 settings.json 加载并应用用户自定义设置（高亮颜色、字号、字体等）
        self._apply_highlight_color()
        self._apply_font_size()
        self._apply_font_family()
        self._apply_theme()
        self._init_system_theme_monitor()
        self._apply_layout()
        # Fluent ComboBox 使用自定义弹出视图，无需 setView
        self.ui.choose_exe.setMinimumWidth(150)  # 保证下拉框不被压成一条线
        # 远程状态
        self._frpc_process = None
        self._p2p_visitors = []
        self._p2p_current_index = -1
        self._tcp_worker = None
        self._remote_session_window = None
        self._init_p2p_panel()
        # 恢复上次关闭前的远程会话（延迟执行，等待主窗口就绪）
        self._restore_remote_sessions()

    def connect_signals(self):
        """连接信号和槽"""
        # 按钮点击事件
        self.ui.flush.clicked.connect(self.on_flush_clicked)
        self.ui.start.clicked.connect(self.on_start_clicked)
        self.ui.open_daily.clicked.connect(self.on_open_daily_clicked)
        self.ui.write_table.clicked.connect(self.on_open_dir_clicked)
        self.ui.open_config.clicked.connect(lambda: QTimer.singleShot(0, self.on_open_config_clicked))
        self.ui.pause_btn.clicked.connect(self._on_pause_clicked)
        # 球桌管理面板入口
        self.ui.table_panel_btn.clicked.connect(lambda: QTimer.singleShot(0, self._on_open_table_panel))
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
        self.ui.p2p_rdp_btn.clicked.connect(self._on_rdp_btn_clicked)

        # 启动三端按钮
        self.ui.start_three_btn.clicked.connect(self.on_start_three_clicked)

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
        """将缓冲区内所有日志一次性写入控件（减少重绘次数）"""
        if not self._log_batch_buf:
            return
        combined = '\n'.join(self._log_batch_buf)
        self._log_batch_buf.clear()

        self.ui.show_log.appendPlainText(combined)

        # 行数上限截断（避免无限增长导致内存/布局开销）
        doc = self.ui.show_log.document()
        if doc.blockCount() > self._LOG_MAX_LINES:
            cursor = self.ui.show_log.textCursor()
            cursor.movePosition(cursor.Start)
            cursor.movePosition(cursor.Down, cursor.KeepAnchor,
                                doc.blockCount() - self._LOG_MAX_LINES)
            cursor.removeSelectedText()
            cursor.deleteChar()  # 删除多余换行

        if self._log_at_bottom:
            self._scroll_log_to_bottom()
        else:
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
        """根据设备代码和选中日期加载日志文件到 loacl_video_list（第二列）

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
        self.ui.loacl_video_list.clear()

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
            self._update_empty_hint(self.ui.loacl_video_list)
            self._append_log(f"[提示] {device_code} 下没有 {date_str} 的日志 (查找路径: {date_dir} 及设备根目录)")
            self._show_info_bar(f"{device_code} 下未找到 {date_str} 的日志", "warning")
            return

        self.ui.loacl_video_list.setUpdatesEnabled(False)
        for log_path in sorted(log_files):
            # 只显示文件名，如 20260705_131009.log
            self.ui.loacl_video_list.addItem(os.path.basename(log_path))
        self.ui.loacl_video_list.setUpdatesEnabled(True)

        # 列表重载后重新应用搜索过滤
        self._on_video_search_changed(self.ui.video_search.text())
        self._update_empty_hint(self.ui.loacl_video_list)

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
        if self._search_debounce_callback:
            self._search_debounce_callback()
            self._search_debounce_callback = None

    def _on_search_shortcut(self):
        """Ctrl+F：根据焦点所在列表切换对应搜索框的显示/隐藏"""
        focused = self.focusWidget()
        # 焦点在日志文件列表（或其搜索框）→ 操作 video_search
        if (self.ui.loacl_video_list.isAncestorOf(focused)
                or focused is self.ui.loacl_video_list
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
        for i in range(self.ui.loacl_video_list.count()):
            self.ui.loacl_video_list.item(i).setHidden(False)
        self._update_empty_hint(self.ui.loacl_video_list)

    def _hide_all_search(self):
        """隐藏所有搜索框（Esc 触发）"""
        self._hide_id_search()
        self._hide_video_search()

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
        for i in range(self.ui.loacl_video_list.count()):
            item = self.ui.loacl_video_list.item(i)
            item.setHidden(bool(kw) and kw not in item.text().lower())
        self._update_empty_hint(self.ui.loacl_video_list)

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

    @Slot()
    def on_open_config_clicked(self):
        """配置按钮点击事件 - 选择打开 settings.json / cfg.json / frpc_xtcp.toml（Fluent 对话框）"""

        class ConfigFileDialog(MessageBoxBase):
            def __init__(self, parent, options):
                super().__init__(parent)
                self.titleLabel = BodyLabel("选择要打开的配置文件：", self)
                self.viewLayout.addWidget(self.titleLabel)
                self.comboBox = ComboBox(self)
                self.comboBox.addItems(options)
                self.comboBox.setMinimumWidth(260)
                self.viewLayout.addWidget(self.comboBox)

        options = ["settings.json", "cfg.json", "frpc_xtcp.toml"]
        dlg = ConfigFileDialog(self, options)
        dlg.yesButton.setText("打开")
        dlg.cancelButton.setText("取消")
        dlg.widget.setMinimumWidth(320)
        if not dlg.exec():
            return

        choice = dlg.comboBox.currentText()
        if choice == "settings.json":
            path = self._get_settings_path()
        elif choice == "cfg.json":
            path = os.path.join(self.exe_dir, "cfg.json")
        else:  # frpc_xtcp.toml
            path = os.path.join(self._get_app_dir(), "frpc_xtcp.toml")

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
            alt_path = os.path.join(self.videos_dir, device_code, log_filename)
            if os.path.exists(alt_path):
                full_log_path = alt_path

        # 读取日志文件内容并显示在第三列
        # 大文件只读尾部 _LOG_TAIL_BYTES，避免一次性加载整个文件导致 UI 卡顿/内存暴涨
        _LOG_TAIL_BYTES = 2 * 1024 * 1024  # 2MB
        try:
            file_size = os.path.getsize(full_log_path)
            truncated = False
            with open(full_log_path, 'rb') as f:
                if file_size > _LOG_TAIL_BYTES:
                    f.seek(-_LOG_TAIL_BYTES, os.SEEK_END)
                    raw = f.read()
                    truncated = True
                else:
                    raw = f.read()
            content = raw.decode('utf-8', errors='ignore')
            if truncated:
                # 丢弃可能不完整的首行（从中间截断处开始）
                first_nl = content.find('\n')
                if first_nl != -1:
                    content = content[first_nl + 1:]

            self._current_log_path = full_log_path
            self.ui.log_list.setUpdatesEnabled(False)
            self.ui.log_list.clear()
            highlight_patterns = [r'返回', r'add']
            for line in content.splitlines():
                log_item = QListWidgetItem(line)
                if any(re.search(p, line) for p in highlight_patterns):
                    log_item.setForeground(QBrush(self.highlight_color))
                self.ui.log_list.addItem(log_item)
            self.ui.log_list.setUpdatesEnabled(True)
            self._update_empty_hint(self.ui.log_list)

            line_count = len(content.splitlines())
            if truncated:
                size_mb = file_size / (1024 * 1024)
                self._append_log(f"[日志内容] 文件 {size_mb:.1f}MB 过大，仅显示尾部 {line_count} 行")
                self._show_info_bar(f"大文件仅加载尾部 {line_count} 行", "warning")
            else:
                self._append_log(f"[日志内容] 已加载 {line_count} 行")
                self._show_info_bar(f"日志已加载 {line_count} 行", "success")
            self._update_status_logs(line_count)
        except Exception as e:
            self._append_log(f"[错误] 无法读取日志文件: {str(e)}")
            self._show_info_bar(f"无法读取日志文件: {e}", "error")
            self.ui.log_list.clear()
            self._update_empty_hint(self.ui.log_list)
            self._current_log_path = None

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

        # 1. 终止 frpc 进程
        proc = getattr(self, '_frpc_process', None)
        if proc is not None:
            self._frpc_process = None
            try:
                proc.kill()
                proc.waitForFinished(2000)
            except (RuntimeError, OSError):
                pass
            try:
                proc.deleteLater()
            except RuntimeError:
                pass

        # 2. 保存远程会话信息（在关闭窗口之前）
        self._save_remote_sessions()

        # 3. 关闭远程会话标签容器（触发各面板 shutdown：SFTP/SSH/RDP）
        win = getattr(self, '_remote_session_window', None)
        if win is not None:
            self._remote_session_window = None
            try:
                win.close()  # closeEvent → shutdown_all()
            except (RuntimeError, OSError):
                pass

        # 3. 终止正在运行的 SnookerTracking 程序
        rp = getattr(self, 'running_process', None)
        if rp is not None:
            self.running_process = None
            try:
                rp.kill()
                rp.waitForFinished(1000)
            except (RuntimeError, OSError):
                pass

        # 4. 终止三端进程
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

        # 5. 清理 TCP worker
        tw = getattr(self, '_tcp_worker', None)
        if tw is not None:
            self._tcp_worker = None
            try:
                if tw.isRunning():
                    tw.finished.connect(tw.deleteLater)
                else:
                    tw.deleteLater()
            except RuntimeError:
                pass

        # 6. 终止异步解码进程
        dp = getattr(self, '_decode_process', None)
        if dp is not None:
            self._decode_process = None
            try:
                if dp.state() != QProcess.NotRunning:
                    dp.kill()
                    dp.waitForFinished(1000)
            except (RuntimeError, OSError):
                pass

        # 7. 等待上传收集 worker 结束（文件复制任务，短等待即可）
        for cw in list(getattr(self, '_upload_collect_workers', [])):
            try:
                if cw.isRunning():
                    cw.wait(1500)
            except (RuntimeError, OSError):
                pass
        self._upload_collect_workers = []

        super().closeEvent(event)
