# -*- coding: utf-8 -*-

################################################################################
## AutoWork - SCADA 工业监控 Dashboard UI
## 三区域布局: 左侧设备树 + 中间日志控制台 + 右侧远程控制面板
## WARNING! 重新编译 .ui 文件会覆盖此文件
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QTimer, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QApplication,
    QFormLayout, QFrame, QHBoxLayout, QLabel, QLayout,
    QMainWindow, QScrollArea,
    QSizePolicy, QSplitter, QVBoxLayout,
    QWidget, QStatusBar)
from qfluentwidgets import (
    PushButton as FluentPushButton,
    PrimaryPushButton,
    ToggleButton,
    ComboBox as FluentComboBox,
    CalendarPicker,
    RadioButton,
    ListWidget,
    PlainTextEdit as FluentPlainTextEdit,
    SearchLineEdit,
    LineEdit as FluentLineEdit,
    PasswordLineEdit,
    SpinBox as FluentSpinBox,
    FlowLayout,
    setFont,
    setCustomStyleSheet,
)


def _make_separator(vertical=False):
    """创建分割线"""
    sep = QFrame()
    sep.setObjectName(u"toolbar_separator")
    sep.setFrameShape(QFrame.Shape.VLine if vertical else QFrame.Shape.HLine)
    sep.setFrameShadow(QFrame.Shadow.Plain)
    if vertical:
        sep.setFixedWidth(1)
    else:
        sep.setFixedHeight(1)
    return sep


def _make_section_label(text, parent=None):
    """创建卡片模块标题标签"""
    lbl = QLabel(text, parent)
    lbl.setObjectName(u"section_label")
    lbl.setFixedHeight(24)
    lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
    return lbl


class _ToolbarRadioButton(RadioButton):
    """工具栏专用单选按钮：强制 32px 行高，与 Fluent 按钮中线对齐。
    通过 setCustomStyleSheet 将高度 QSS 注册到 qfluentwidgets 主题管理器，
    setTheme() 切换主题时会自动重新追加，不会被内部 QSS 覆盖。"""

    _HEIGHT_QSS = "QRadioButton { min-height: 32px; max-height: 32px; }"

    def __init__(self, parent=None):
        super().__init__(parent)
        setCustomStyleSheet(self, self._HEIGHT_QSS, self._HEIGHT_QSS)


class _FlowScrollArea(QScrollArea):
    """工具栏专用滚动区域：根据自身宽度主动计算内容高度并锁定，
    保证父布局精确按内容高度分配空间（单行=单行高，折行=多行高，超上限滚动）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # 高度上限（约 3 行控件），setFixedHeight 会覆盖 maximumHeight，
        # 因此用独立变量保存上限，避免窗口反复缩放时上限被“棘轮”压低
        self._height_cap = self.maximumHeight()
        # 垂直策略设为 Preferred（配合 HFW 标志，作为首次布局的兜底）
        sp = self.sizePolicy()
        sp.setVerticalPolicy(QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)

    def setMaximumHeight(self, h):
        """同步更新高度上限"""
        self._height_cap = h
        super().setMaximumHeight(h)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        w = self.widget()
        if w is None:
            return super().heightForWidth(width)
        margins = self.contentsMargins()
        sb = self.verticalScrollBar()
        sb_w = sb.width() if sb.isVisible() else 0
        inner_w = max(0, width - margins.left() - margins.right() - sb_w)
        h = w.heightForWidth(inner_w)
        return min(h, self._height_cap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adjust_height()

    def _adjust_height(self):
        """按当前宽度计算流式内容实际高度，锁定自身高度，下方内容紧贴无空白"""
        w = self.widget()
        if w is None or self.width() <= 0:
            return
        margins = self.contentsMargins()
        sb = self.verticalScrollBar()
        sb_w = sb.width() if sb.isVisible() else 0
        inner_w = max(1, self.width() - margins.left() - margins.right() - sb_w)
        h = min(w.heightForWidth(inner_w), self._height_cap)
        if h > 0 and h != self.height():
            self.setFixedHeight(h)


class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        if not MainWindow.objectName():
            MainWindow.setObjectName(u"MainWindow")
        MainWindow.resize(1440, 900)
        MainWindow.setMinimumSize(QSize(640, 400))

        # ===== Central Widget =====
        self.centralwidget = QWidget(MainWindow)
        self.centralwidget.setObjectName(u"centralwidget")
        self.centralwidget.setEnabled(True)
        sizePolicy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.centralwidget.sizePolicy().hasHeightForWidth())
        self.centralwidget.setSizePolicy(sizePolicy)
        self.centralwidget.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)

        # 主垂直布局: 工具栏 + 三区域内容
        self.verticalLayout_2 = QVBoxLayout(self.centralwidget)
        self.verticalLayout_2.setSpacing(0)
        self.verticalLayout_2.setObjectName(u"verticalLayout_2")
        self.verticalLayout_2.setContentsMargins(0, 0, 0, 0)

        # ============================================================
        # 顶部工具栏 — FlowLayout 流式布局（窗口缩窄时控件自动换行，严禁重叠）
        # 包裹在 QScrollArea 中：折行过多时可上下滚动，不挤压下方日志区域
        # ============================================================
        self.toolbar_scroll = _FlowScrollArea(self.centralwidget)
        self.toolbar_scroll.setObjectName(u"toolbar_scroll")
        self.toolbar_scroll.setWidgetResizable(True)
        self.toolbar_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.toolbar_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.toolbar_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # 最多展示约 3 行控件（单行约 44px），超出部分滚动查看
        self.toolbar_scroll.setMaximumHeight(132)

        self.toolbar_widget = QWidget()
        self.toolbar_widget.setObjectName(u"toolbar_widget")
        self.horizontalLayout = FlowLayout(self.toolbar_widget)
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.horizontalLayout.setHorizontalSpacing(6)
        self.horizontalLayout.setVerticalSpacing(6)
        self.horizontalLayout.setContentsMargins(10, 6, 10, 6)

        # --- 组1: 文件操作 ---
        self.flush = FluentPushButton(self.toolbar_widget)
        self.flush.setObjectName(u"flush")
        self.horizontalLayout.addWidget(self.flush)

        self.date = CalendarPicker(self.toolbar_widget)
        self.date.setObjectName(u"date")
        self.date.setMinimumWidth(150)
        self.date.setDate(QDate(2000, 10, 7))
        self.horizontalLayout.addWidget(self.date)

        self.write_table = FluentPushButton(self.toolbar_widget)
        self.write_table.setObjectName(u"write_table")
        self.horizontalLayout.addWidget(self.write_table)

        self.open_config = FluentPushButton(self.toolbar_widget)
        self.open_config.setObjectName(u"open_config")
        self.horizontalLayout.addWidget(self.open_config)

        # --- 组2: 程序配置 ---
        self.label_2 = QLabel(self.toolbar_widget)
        self.label_2.setObjectName(u"label_2")
        self.label_2.setFixedHeight(32)
        self.label_2.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        setFont(self.label_2, 11)
        self.horizontalLayout.addWidget(self.label_2)

        self.choose_exe = FluentComboBox(self.toolbar_widget)
        self.choose_exe.setObjectName(u"choose_exe")
        self.choose_exe.setMinimumWidth(145)
        self.horizontalLayout.addWidget(self.choose_exe)

        # 帧控制（_ToolbarRadioButton 强制 32px 行高与按钮中线对齐）
        self.input_frame_before = _ToolbarRadioButton(self.toolbar_widget)
        self.input_frame_before.setObjectName(u"input_frame_before")
        self.input_frame_before.setChecked(True)
        self.horizontalLayout.addWidget(self.input_frame_before)

        self.input_frame_set = _ToolbarRadioButton(self.toolbar_widget)
        self.input_frame_set.setObjectName(u"input_frame_set")
        self.horizontalLayout.addWidget(self.input_frame_set)

        self.input_frame_custom = _ToolbarRadioButton(self.toolbar_widget)
        self.input_frame_custom.setObjectName(u"input_frame_custom")
        self.horizontalLayout.addWidget(self.input_frame_custom)

        self.input_frame = FluentLineEdit(self.toolbar_widget)
        self.input_frame.setObjectName(u"input_frame")
        self.input_frame.setFixedWidth(75)
        self.horizontalLayout.addWidget(self.input_frame)

        # --- 组3: 播放控制 ---
        self.open_daily = FluentPushButton(self.toolbar_widget)
        self.open_daily.setObjectName(u"open_daily")
        self.horizontalLayout.addWidget(self.open_daily)

        self.start = PrimaryPushButton(self.toolbar_widget)
        self.start.setObjectName(u"start")
        self.horizontalLayout.addWidget(self.start)

        self.end = FluentPushButton(self.toolbar_widget)
        self.end.setObjectName(u"end")
        self.horizontalLayout.addWidget(self.end)

        self.pause_btn = FluentPushButton(self.toolbar_widget)
        self.pause_btn.setObjectName(u"pause_btn")
        self.pause_btn.setText("暂停")
        self.horizontalLayout.addWidget(self.pause_btn)

        self.start_three_btn = FluentPushButton(self.toolbar_widget)
        self.start_three_btn.setObjectName(u"start_three_btn")
        self.horizontalLayout.addWidget(self.start_three_btn)

        self.p2p_btn = ToggleButton(self.toolbar_widget)
        self.p2p_btn.setObjectName(u"p2p_btn")
        self.p2p_btn.setMaximumWidth(72)
        self.horizontalLayout.addWidget(self.p2p_btn)

        self.toolbar_scroll.setWidget(self.toolbar_widget)
        self.verticalLayout_2.addWidget(self.toolbar_scroll)

        # ============================================================
        # 三区域 Splitter: 左侧设备树 | 中间日志控制台 | 右侧远程面板
        # ============================================================
        self.horizontalLayout_main = QHBoxLayout()
        self.horizontalLayout_main.setObjectName(u"horizontalLayout_main")
        self.horizontalLayout_main.setContentsMargins(0, 0, 0, 0)
        self.horizontalLayout_main.setSpacing(0)

        # 创建核心控件（两套布局共享，切换时复用实例）
        self.id_list = ListWidget()
        self.id_list.setObjectName(u"id_list")

        # 设备搜索框（位于 id_list 正下方，默认隐藏，Ctrl+F 显示）
        self.id_search = SearchLineEdit()
        self.id_search.setObjectName(u"id_search")
        self.id_search.setPlaceholderText("搜索设备代码...")
        self.id_search.setClearButtonEnabled(True)
        self.id_search.setVisible(False)

        # 容器：id_list + 搜索框，布局切换时随 id_list 一起迁移
        self.id_list_container = QWidget()
        self.id_list_container.setObjectName(u"id_list_container")
        _id_container_layout = QVBoxLayout(self.id_list_container)
        _id_container_layout.setContentsMargins(0, 0, 0, 0)
        _id_container_layout.setSpacing(0)
        _id_container_layout.addWidget(self.id_list, 1)
        _id_container_layout.addWidget(self.id_search)

        self.loacl_video_list = ListWidget()
        self.loacl_video_list.setObjectName(u"loacl_video_list")

        self.log_list = ListWidget()
        self.log_list.setObjectName(u"log_list")
        self.log_list.setObjectName(u"log_list")

        self.show_log = FluentPlainTextEdit()
        self.show_log.setObjectName(u"show_log")
        self.show_log.setReadOnly(True)
        self.show_log.setFont(QFont("Consolas", 10))
        # 隐藏 Fluent EditLayer 覆盖层，避免聚焦时绘制主题色底边
        self.show_log.layer.hide()

        # 日志顶部状态条（仅新版布局显示）
        self.log_status_bar = QWidget()
        self.log_status_bar.setObjectName(u"log_status_bar")
        self.log_status_bar.setFixedHeight(26)
        log_status_layout = QHBoxLayout(self.log_status_bar)
        log_status_layout.setContentsMargins(8, 2, 8, 2)
        log_status_layout.setSpacing(12)
        self.log_status_device = QLabel("设备: --")
        self.log_status_device.setObjectName(u"log_status_device")
        log_status_layout.addWidget(self.log_status_device)
        self.log_status_count = QLabel("日志: 0 条")
        self.log_status_count.setObjectName(u"log_status_count")
        log_status_layout.addWidget(self.log_status_count)
        log_status_layout.addStretch()

        # 构建默认布局（新版）
        self._is_classic_layout = False
        self._build_modern_content()

        self.horizontalLayout_main.addWidget(self.splitter)

        # ===== 右侧: 远程控制面板 (卡片模块化, 默认隐藏) =====
        self.p2p_panel = QFrame(self.centralwidget)
        self.p2p_panel.setObjectName(u"p2p_panel")
        self.p2p_panel.setFrameShape(QFrame.Shape.StyledPanel)
        p2p_main_layout = QVBoxLayout(self.p2p_panel)
        p2p_main_layout.setContentsMargins(10, 10, 10, 10)
        p2p_main_layout.setSpacing(8)

        # 面板标题
        p2p_header = QLabel("远程控制面板")
        p2p_header.setObjectName(u"p2p_panel_header")
        p2p_header.setFixedHeight(18)
        p2p_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        p2p_main_layout.addWidget(p2p_header)

        # --- 模块1: 服务器/网络 ---
        # 分区标题随连接模式切换文本（XTCP=visitors，TCP=保存的服务器），见 main.py _update_p2p_visibility
        self.p2p_server_section_label = _make_section_label("◎ 服务器 / visitors")
        p2p_main_layout.addWidget(self.p2p_server_section_label)

        self.p2p_visitor_list = ListWidget(self.p2p_panel)
        self.p2p_visitor_list.setObjectName(u"p2p_visitor_list")
        # 只保底下限（约3行），不设上限：列表随窗口高度自由伸缩，
        # 并通过 setStretchFactor 优先吸收剩余垂直空间
        self.p2p_visitor_list.setMinimumHeight(90)
        p2p_main_layout.addWidget(self.p2p_visitor_list)
        p2p_main_layout.setStretchFactor(self.p2p_visitor_list, 1)

        p2p_list_btn_layout = QHBoxLayout()
        self.p2p_add_btn = FluentPushButton("添加")
        self.p2p_add_btn.setObjectName(u"p2p_add_btn")
        self.p2p_delete_btn = FluentPushButton("删除")
        self.p2p_delete_btn.setObjectName(u"p2p_delete_btn")
        p2p_list_btn_layout.addWidget(self.p2p_add_btn)
        p2p_list_btn_layout.addWidget(self.p2p_delete_btn)
        p2p_main_layout.addLayout(p2p_list_btn_layout)

        # XTCP 表单
        self.p2p_form_server = FluentLineEdit(self.p2p_panel)
        self.p2p_form_server.setObjectName(u"p2p_form_server")
        self.p2p_form_server.setPlaceholderText("snk_xxxx")
        self.p2p_form_port = FluentSpinBox(self.p2p_panel)
        self.p2p_form_port.setObjectName(u"p2p_form_port")
        self.p2p_form_port.setRange(1024, 65535)
        self.p2p_form_key = FluentLineEdit(self.p2p_panel)
        self.p2p_form_key.setObjectName(u"p2p_form_key")
        self.p2p_form_key.setText("abc123")

        self.p2p_xtcp_form = QFormLayout()
        self.p2p_xtcp_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.p2p_xtcp_form.addRow("serverName:", self.p2p_form_server)
        self.p2p_xtcp_form.addRow("bindPort:", self.p2p_form_port)
        self.p2p_xtcp_form.addRow("secretKey:", self.p2p_form_key)
        p2p_main_layout.addLayout(self.p2p_xtcp_form)

        # XTCP 专属控件列表（visitor 列表与添加/删除按钮为两种模式共用，不在此列：
        # TCP 模式下该列表复用为“保存的服务器”，见 main.py _update_p2p_visibility）
        self.p2p_xtcp_widgets = [
            self.p2p_form_server, self.p2p_form_port, self.p2p_form_key
        ]

        # 连接/断开按钮（XTCP 专属：TCP 模式由各功能按钮点击时直连，无需统一连接入口）
        # 用容器包裹以便 TCP 模式下整体隐藏，避免留下空白布局间隙
        self.p2p_conn_widget = QWidget(self.p2p_panel)
        self.p2p_conn_widget.setObjectName(u"p2p_conn_widget")
        p2p_conn_layout = QHBoxLayout(self.p2p_conn_widget)
        p2p_conn_layout.setContentsMargins(0, 0, 0, 0)
        self.p2p_connect_btn = PrimaryPushButton("连接")
        self.p2p_connect_btn.setObjectName(u"p2p_connect_btn")
        self.p2p_disconnect_btn = FluentPushButton("断开")
        self.p2p_disconnect_btn.setObjectName(u"p2p_disconnect_btn")
        p2p_conn_layout.addWidget(self.p2p_connect_btn)
        p2p_conn_layout.addWidget(self.p2p_disconnect_btn)
        p2p_main_layout.addWidget(self.p2p_conn_widget)

        # 分割线
        p2p_main_layout.addWidget(_make_separator())

        # --- 模块2: 权限与配置 ---
        p2p_main_layout.addWidget(_make_section_label("◎ 权限与配置"))

        mode_layout = QHBoxLayout()
        mode_label = QLabel("连接方式:")
        mode_label.setObjectName(u"p2p_mode_label")
        mode_layout.addWidget(mode_label)
        self.p2p_mode_combo = FluentComboBox(self.p2p_panel)
        self.p2p_mode_combo.setObjectName(u"p2p_mode_combo")
        self.p2p_mode_combo.addItems(["XTCP", "TCP"])
        mode_layout.addWidget(self.p2p_mode_combo)
        p2p_main_layout.addLayout(mode_layout)

        # TCP 表单
        self.p2p_ssh_host = FluentLineEdit(self.p2p_panel)
        self.p2p_ssh_host.setObjectName(u"p2p_ssh_host")
        self.p2p_ssh_host.setPlaceholderText("127.0.0.1")
        self.p2p_ssh_port = FluentSpinBox(self.p2p_panel)
        self.p2p_ssh_port.setObjectName(u"p2p_ssh_port")
        self.p2p_ssh_port.setRange(1, 65535)
        self.p2p_ssh_port.setValue(22)
        self.p2p_ssh_user = FluentLineEdit(self.p2p_panel)
        self.p2p_ssh_user.setObjectName(u"p2p_ssh_user")
        self.p2p_ssh_user.setText("newbv")
        self.p2p_ssh_pass = PasswordLineEdit(self.p2p_panel)
        self.p2p_ssh_pass.setObjectName(u"p2p_ssh_pass")
        self.p2p_ssh_pass.setText("Xqsjnbv155")
        self.p2p_ssh_pass.setPlaceholderText("请输入密码")

        self.p2p_ssh_form = QFormLayout()
        self.p2p_ssh_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.p2p_ssh_form.addRow("host:", self.p2p_ssh_host)
        self.p2p_ssh_form.addRow("port:", self.p2p_ssh_port)
        self.p2p_ssh_form.addRow("账号:", self.p2p_ssh_user)
        self.p2p_ssh_form.addRow("密码:", self.p2p_ssh_pass)
        p2p_main_layout.addLayout(self.p2p_ssh_form)

        # host/port 随模式切换显隐（账号/密码始终可见）
        self.p2p_ssh_widgets = [
            self.p2p_ssh_host, self.p2p_ssh_port,
        ]
        for w in self.p2p_ssh_widgets:
            w.setVisible(False)
        for row_idx in range(2):
            lbl_item = self.p2p_ssh_form.itemAt(row_idx, QFormLayout.ItemRole.LabelRole)
            if lbl_item and lbl_item.widget():
                lbl_item.widget().setVisible(False)

        # 分割线
        p2p_main_layout.addWidget(_make_separator())

        # --- 模块3: 高级功能 ---
        p2p_main_layout.addWidget(_make_section_label("◎ 功能"))

        self.p2p_sftp_btn = FluentPushButton("文件管理")
        self.p2p_sftp_btn.setObjectName(u"p2p_sftp_btn")
        self.p2p_sftp_btn.setEnabled(False)
        p2p_main_layout.addWidget(self.p2p_sftp_btn)

        self.p2p_ssh_terminal_btn = FluentPushButton("SSH 终端")
        self.p2p_ssh_terminal_btn.setObjectName(u"p2p_ssh_terminal_btn")
        self.p2p_ssh_terminal_btn.setEnabled(False)
        p2p_main_layout.addWidget(self.p2p_ssh_terminal_btn)

        self.p2p_rdp_btn = FluentPushButton("远程桌面")
        self.p2p_rdp_btn.setObjectName(u"p2p_rdp_btn")
        self.p2p_rdp_btn.setEnabled(False)
        p2p_main_layout.addWidget(self.p2p_rdp_btn)

        # 注意：尾部不再 addStretch()——剩余垂直空间全部由 p2p_visitor_list 吸收
        # （见上方 setStretchFactor），表单/按钮固定贴底，列表随窗口高度自由伸缩
        # 面板宽度 340px：保证表单字段（serverName/密码等）有足够呼吸空间
        self.p2p_panel.setFixedWidth(290)
        self.p2p_panel.setVisible(False)

        self.horizontalLayout_main.addWidget(self.p2p_panel)

        self.verticalLayout_2.addLayout(self.horizontalLayout_main)

        # 将中心控件挂到容器布局（兼容 QMainWindow 和 FluentWindowBase 容器模式）
        if hasattr(MainWindow, 'setCentralWidget'):
            MainWindow.setCentralWidget(self.centralwidget)
        else:
            # FluentWindowBase 模式：必须挂到 vBoxLayout（带 48px 标题栏预留的垂直布局），
            # 而非 MainWindow.layout()（hBoxLayout）——后者会导致内容绕过标题栏预留区，
            # 与菜单栏水平并排，造成顶部三行（标题栏/菜单栏/工具栏）挤压重叠
            MainWindow.vBoxLayout.addWidget(self.centralwidget)

        self.retranslateUi(MainWindow)

        # 注意：不能调用 QMetaObject.connectSlotsByName(MainWindow)
        # 它会按 on_<objectName>_<signal> 规则自动连接 on_end_clicked / on_flush_clicked 等槽，
        # 与 connect_signals() 中的手动连接重复，导致按钮点击触发两次。
    # setupUi

    def retranslateUi(self, MainWindow):
        if hasattr(MainWindow, 'setCentralWidget'):  # 仅对真正的窗口设置标题
            MainWindow.setWindowTitle(QCoreApplication.translate("MainWindow", u"AutoWork", None))
        self.flush.setText(QCoreApplication.translate("MainWindow", u"刷新", None))
        self.date.setDateFormat(QCoreApplication.translate("MainWindow", u"yyyy-MM-dd", None))
        self.write_table.setText(QCoreApplication.translate("MainWindow", u"打开目录", None))
        self.open_config.setText(QCoreApplication.translate("MainWindow", u"配置", None))
        self.label_2.setText(QCoreApplication.translate("MainWindow", u"程序:", None))
        self.input_frame_before.setText(QCoreApplication.translate("MainWindow", u"帧前", None))
        self.input_frame_set.setText(QCoreApplication.translate("MainWindow", u"帧后", None))
        self.input_frame_custom.setText(QCoreApplication.translate("MainWindow", u"自定义", None))
        self.input_frame.setText(QCoreApplication.translate("MainWindow", u"400", None))
        self.open_daily.setText(QCoreApplication.translate("MainWindow", u"CPP日志", None))
        self.start.setText(QCoreApplication.translate("MainWindow", u"播放", None))
        self.end.setText(QCoreApplication.translate("MainWindow", u"结束", None))
        self.pause_btn.setText(QCoreApplication.translate("MainWindow", u"暂停", None))
        self.start_three_btn.setText(QCoreApplication.translate("MainWindow", u"启动三端", None))
        self.p2p_btn.setText(QCoreApplication.translate("MainWindow", u"远程", None))
    # retranslateUi

    # ================================================================
    # 布局构建与切换
    # ================================================================

    def _build_modern_content(self):
        """新版布局: 左侧嵌套Splitter(设备|文件 + 日志) | 中间日志控制台"""
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self.centralwidget)
        self.splitter.setObjectName(u"splitter")

        # ===== 左侧面板 =====
        self.left_panel = QWidget()
        self.left_panel.setObjectName(u"left_panel")
        left_outer = QVBoxLayout(self.left_panel)
        left_outer.setContentsMargins(0, 0, 0, 0)
        left_outer.setSpacing(0)

        self.left_splitter = QSplitter(Qt.Orientation.Vertical)
        self.left_splitter.setObjectName(u"left_splitter")

        # 上方: 水平 Splitter，设备列表 | 文件列表
        self.left_top_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.left_top_splitter.setObjectName(u"left_top_splitter")

        id_widget = QWidget()
        id_layout = QVBoxLayout(id_widget)
        id_layout.setContentsMargins(0, 0, 0, 0)
        id_layout.setSpacing(0)
        id_header = QLabel("  设备")
        id_header.setObjectName(u"left_panel_header")
        id_header.setFixedHeight(26)
        id_header.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        id_layout.addWidget(id_header)
        id_layout.addWidget(self.id_list_container, 1)
        self.left_top_splitter.addWidget(id_widget)

        file_widget = QWidget()
        file_layout = QVBoxLayout(file_widget)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(0)
        file_header = QLabel("  文件 / 日志")
        file_header.setObjectName(u"left_panel_header")
        file_header.setFixedHeight(26)
        file_header.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        file_layout.addWidget(file_header)
        file_layout.addWidget(self.loacl_video_list, 1)
        self.left_top_splitter.addWidget(file_widget)

        self.left_top_splitter.setSizes([100, 120])
        self.left_splitter.addWidget(self.left_top_splitter)

        # 下方: 日志内容列表
        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(0)
        log_header = QLabel("  日志内容")
        log_header.setObjectName(u"left_panel_header")
        log_header.setFixedHeight(26)
        log_header.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        log_layout.addWidget(log_header)
        log_layout.addWidget(self.log_list, 1)
        self.left_splitter.addWidget(log_widget)

        self.left_splitter.setSizes([350, 650])
        left_outer.addWidget(self.left_splitter, 1)
        self.splitter.addWidget(self.left_panel)

        # ===== 中间: 日志控制台 =====
        self.center_panel = QWidget()
        self.center_panel.setObjectName(u"center_panel")
        center_layout = QVBoxLayout(self.center_panel)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        center_layout.addWidget(self.log_status_bar)
        center_layout.addWidget(self.show_log, 1)
        self.splitter.addWidget(self.center_panel)

        self.splitter.setSizes([400, 600])
        self._is_classic_layout = False

    def _build_classic_content(self):
        """经典布局: 四列水平Splitter（设备|文件|日志内容|日志控制台）"""
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self.centralwidget)
        self.splitter.setObjectName(u"splitter")

        self.splitter.addWidget(self.id_list_container)
        self.splitter.addWidget(self.loacl_video_list)
        self.splitter.addWidget(self.log_list)
        self.splitter.addWidget(self.show_log)

        self.splitter.setSizes([80, 150, 300, 500])
        self._is_classic_layout = True

    def switch_layout(self, classic=False):
        """切换布局模式，复用核心控件实例（保留数据和信号连接）"""
        if classic == self._is_classic_layout:
            return

        # 1. 将核心控件从旧布局中脱离（设置 parent=None 防止被旧 splitter 删除）
        # id_list 与其搜索框同在 id_list_container 内，迁移容器即可一并带走
        self.id_list_container.setParent(None)
        self.loacl_video_list.setParent(None)
        self.log_list.setParent(None)
        self.show_log.setParent(None)
        self.log_status_bar.setParent(None)

        # 2. 从 horizontalLayout_main 中移除旧 splitter 并删除
        self.horizontalLayout_main.removeWidget(self.splitter)
        self.splitter.deleteLater()

        # 3. 构建新布局
        if classic:
            self._build_classic_content()
        else:
            self._build_modern_content()

        # 4. 将新 splitter 插入到 horizontalLayout_main 的最前面（p2p_panel 之前）
        self.horizontalLayout_main.insertWidget(0, self.splitter)
