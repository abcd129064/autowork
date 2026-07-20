# -*- coding: utf-8 -*-

################################################################################
## AutoWork - SCADA 工业监控 Dashboard UI
## 三区域布局: 左侧设备树 + 中间日志控制台 + 右侧远程控制面板
## WARNING! 重新编译 .ui 文件会覆盖此文件
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QApplication, QComboBox, QDateEdit,
    QFormLayout, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QPlainTextEdit,
    QPushButton, QRadioButton, QSizePolicy, QSpinBox, QSplitter, QVBoxLayout,
    QWidget, QStatusBar)


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


class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        if not MainWindow.objectName():
            MainWindow.setObjectName(u"MainWindow")
        MainWindow.resize(1280, 720)
        MainWindow.setMinimumSize(QSize(960, 540))

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
        # 顶部工具栏 — 三组逻辑分区
        # ============================================================
        self.toolbar_widget = QWidget(self.centralwidget)
        self.toolbar_widget.setObjectName(u"toolbar_widget")
        self.toolbar_widget.setFixedHeight(44)
        self.horizontalLayout = QHBoxLayout(self.toolbar_widget)
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.horizontalLayout.setSpacing(6)
        self.horizontalLayout.setContentsMargins(10, 6, 10, 6)

        # --- 组1: 文件操作 ---
        self.flush = QPushButton(self.toolbar_widget)
        self.flush.setObjectName(u"flush")
        self.horizontalLayout.addWidget(self.flush)

        self.date = QDateEdit(self.toolbar_widget)
        self.date.setObjectName(u"date")
        self.date.setMaximumSize(QSize(110, 16777215))
        self.date.setCalendarPopup(True)
        self.date.setDateTime(QDateTime(QDate(2000, 10, 7), QTime(0, 0, 0)))
        self.horizontalLayout.addWidget(self.date)

        self.write_table = QPushButton(self.toolbar_widget)
        self.write_table.setObjectName(u"write_table")
        self.horizontalLayout.addWidget(self.write_table)

        self.open_config = QPushButton(self.toolbar_widget)
        self.open_config.setObjectName(u"open_config")
        self.horizontalLayout.addWidget(self.open_config)

        # 分割线
        self.horizontalLayout.addWidget(_make_separator(vertical=True))

        # --- 组2: 程序配置 ---
        self.label_2 = QLabel(self.toolbar_widget)
        self.label_2.setObjectName(u"label_2")
        self.horizontalLayout.addWidget(self.label_2)

        self.choose_exe = QComboBox(self.toolbar_widget)
        self.choose_exe.setObjectName(u"choose_exe")
        self.choose_exe.setMinimumWidth(140)
        self.horizontalLayout.addWidget(self.choose_exe)

        # 帧控制
        self.input_frame_before = QRadioButton(self.toolbar_widget)
        self.input_frame_before.setObjectName(u"input_frame_before")
        self.input_frame_before.setChecked(True)
        self.horizontalLayout.addWidget(self.input_frame_before)

        self.input_frame_set = QRadioButton(self.toolbar_widget)
        self.input_frame_set.setObjectName(u"input_frame_set")
        self.horizontalLayout.addWidget(self.input_frame_set)

        self.input_frame_custom = QRadioButton(self.toolbar_widget)
        self.input_frame_custom.setObjectName(u"input_frame_custom")
        self.horizontalLayout.addWidget(self.input_frame_custom)

        self.input_frame = QLineEdit(self.toolbar_widget)
        self.input_frame.setObjectName(u"input_frame")
        self.input_frame.setFixedWidth(55)
        self.horizontalLayout.addWidget(self.input_frame)

        # 分割线
        self.horizontalLayout.addWidget(_make_separator(vertical=True))

        # --- 组3: 播放控制 ---
        self.open_daily = QPushButton(self.toolbar_widget)
        self.open_daily.setObjectName(u"open_daily")
        self.horizontalLayout.addWidget(self.open_daily)

        self.start = QPushButton(self.toolbar_widget)
        self.start.setObjectName(u"start")
        self.horizontalLayout.addWidget(self.start)

        self.end = QPushButton(self.toolbar_widget)
        self.end.setObjectName(u"end")
        self.horizontalLayout.addWidget(self.end)

        self.pause_btn = QPushButton(self.toolbar_widget)
        self.pause_btn.setObjectName(u"pause_btn")
        self.pause_btn.setText("\u6682\u505c")
        self.horizontalLayout.addWidget(self.pause_btn)

        self.p2p_btn = QPushButton(self.toolbar_widget)
        self.p2p_btn.setObjectName(u"p2p_btn")
        self.p2p_btn.setCheckable(True)
        self.p2p_btn.setMaximumWidth(50)
        self.horizontalLayout.addWidget(self.p2p_btn)

        # 尾部弹性空间，防止全屏时控件被均分拉开
        self.horizontalLayout.addStretch()

        self.verticalLayout_2.addWidget(self.toolbar_widget)

        # ============================================================
        # 三区域 Splitter: 左侧设备树 | 中间日志控制台 | 右侧远程面板
        # ============================================================
        self.horizontalLayout_main = QHBoxLayout()
        self.horizontalLayout_main.setObjectName(u"horizontalLayout_main")
        self.horizontalLayout_main.setContentsMargins(0, 0, 0, 0)
        self.horizontalLayout_main.setSpacing(0)

        # 创建核心控件（两套布局共享，切换时复用实例）
        self.id_list = QListWidget()
        self.id_list.setObjectName(u"id_list")

        self.loacl_video_list = QListWidget()
        self.loacl_video_list.setObjectName(u"loacl_video_list")

        self.log_list = QListWidget()
        self.log_list.setObjectName(u"log_list")

        self.show_log = QPlainTextEdit()
        self.show_log.setObjectName(u"show_log")
        self.show_log.setReadOnly(True)
        self.show_log.setFont(QFont("Consolas", 10))

        # 日志顶部状态条（仅新版布局显示）
        self.log_status_bar = QWidget()
        self.log_status_bar.setObjectName(u"log_status_bar")
        self.log_status_bar.setFixedHeight(26)
        log_status_layout = QHBoxLayout(self.log_status_bar)
        log_status_layout.setContentsMargins(8, 2, 8, 2)
        log_status_layout.setSpacing(12)
        self.log_status_device = QLabel("\u8bbe\u5907: --")
        self.log_status_device.setObjectName(u"log_status_device")
        log_status_layout.addWidget(self.log_status_device)
        self.log_status_count = QLabel("\u65e5\u5fd7: 0 \u6761")
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
        p2p_header = QLabel("\u8fdc\u7a0b\u63a7\u5236\u9762\u677f")
        p2p_header.setObjectName(u"p2p_panel_header")
        p2p_header.setFixedHeight(32)
        p2p_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        p2p_main_layout.addWidget(p2p_header)

        # --- 模块1: 服务器/网络 ---
        p2p_main_layout.addWidget(_make_section_label("\u25ce \u670d\u52a1\u5668 / \u7f51\u7edc"))

        self.p2p_visitor_list = QListWidget(self.p2p_panel)
        self.p2p_visitor_list.setObjectName(u"p2p_visitor_list")
        self.p2p_visitor_list.setMaximumHeight(100)
        p2p_main_layout.addWidget(self.p2p_visitor_list)

        p2p_list_btn_layout = QHBoxLayout()
        self.p2p_add_btn = QPushButton("\u6dfb\u52a0")
        self.p2p_add_btn.setObjectName(u"p2p_add_btn")
        self.p2p_delete_btn = QPushButton("\u5220\u9664")
        self.p2p_delete_btn.setObjectName(u"p2p_delete_btn")
        p2p_list_btn_layout.addWidget(self.p2p_add_btn)
        p2p_list_btn_layout.addWidget(self.p2p_delete_btn)
        p2p_main_layout.addLayout(p2p_list_btn_layout)

        # XTCP 表单
        self.p2p_form_server = QLineEdit(self.p2p_panel)
        self.p2p_form_server.setObjectName(u"p2p_form_server")
        self.p2p_form_server.setPlaceholderText("snk_xxxx")
        self.p2p_form_port = QSpinBox(self.p2p_panel)
        self.p2p_form_port.setObjectName(u"p2p_form_port")
        self.p2p_form_port.setRange(1024, 65535)
        self.p2p_form_key = QLineEdit(self.p2p_panel)
        self.p2p_form_key.setObjectName(u"p2p_form_key")
        self.p2p_form_key.setText("abc123")

        self.p2p_xtcp_form = QFormLayout()
        self.p2p_xtcp_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.p2p_xtcp_form.addRow("serverName:", self.p2p_form_server)
        self.p2p_xtcp_form.addRow("bindPort:", self.p2p_form_port)
        self.p2p_xtcp_form.addRow("secretKey:", self.p2p_form_key)
        p2p_main_layout.addLayout(self.p2p_xtcp_form)

        # XTCP 专属控件列表
        self.p2p_xtcp_widgets = [
            self.p2p_visitor_list, self.p2p_add_btn, self.p2p_delete_btn,
            self.p2p_form_server, self.p2p_form_port, self.p2p_form_key
        ]

        # 连接/断开按钮
        p2p_conn_layout = QHBoxLayout()
        self.p2p_connect_btn = QPushButton("\u8fde\u63a5")
        self.p2p_connect_btn.setObjectName(u"p2p_connect_btn")
        self.p2p_disconnect_btn = QPushButton("\u65ad\u5f00")
        self.p2p_disconnect_btn.setObjectName(u"p2p_disconnect_btn")
        p2p_conn_layout.addWidget(self.p2p_connect_btn)
        p2p_conn_layout.addWidget(self.p2p_disconnect_btn)
        p2p_main_layout.addLayout(p2p_conn_layout)

        # 分割线
        p2p_main_layout.addWidget(_make_separator())

        # --- 模块2: 权限与配置 ---
        p2p_main_layout.addWidget(_make_section_label("\u25ce \u6743\u9650\u4e0e\u914d\u7f6e"))

        mode_layout = QHBoxLayout()
        mode_label = QLabel("\u8fde\u63a5\u65b9\u5f0f:")
        mode_label.setObjectName(u"p2p_mode_label")
        mode_layout.addWidget(mode_label)
        self.p2p_mode_combo = QComboBox(self.p2p_panel)
        self.p2p_mode_combo.setObjectName(u"p2p_mode_combo")
        self.p2p_mode_combo.addItems(["XTCP", "TCP"])
        mode_layout.addWidget(self.p2p_mode_combo)
        p2p_main_layout.addLayout(mode_layout)

        # TCP 表单
        self.p2p_ssh_host = QLineEdit(self.p2p_panel)
        self.p2p_ssh_host.setObjectName(u"p2p_ssh_host")
        self.p2p_ssh_host.setPlaceholderText("127.0.0.1")
        self.p2p_ssh_port = QSpinBox(self.p2p_panel)
        self.p2p_ssh_port.setObjectName(u"p2p_ssh_port")
        self.p2p_ssh_port.setRange(1, 65535)
        self.p2p_ssh_port.setValue(22)
        self.p2p_ssh_user = QLineEdit(self.p2p_panel)
        self.p2p_ssh_user.setObjectName(u"p2p_ssh_user")
        self.p2p_ssh_user.setText("newbv")
        self.p2p_ssh_pass = QLineEdit(self.p2p_panel)
        self.p2p_ssh_pass.setObjectName(u"p2p_ssh_pass")
        self.p2p_ssh_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.p2p_ssh_pass.setText("Xqsjnbv155")

        self.p2p_ssh_form = QFormLayout()
        self.p2p_ssh_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.p2p_ssh_form.addRow("host:", self.p2p_ssh_host)
        self.p2p_ssh_form.addRow("port:", self.p2p_ssh_port)
        self.p2p_ssh_form.addRow("\u8d26\u53f7:", self.p2p_ssh_user)
        self.p2p_ssh_form.addRow("\u5bc6\u7801:", self.p2p_ssh_pass)
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
        p2p_main_layout.addWidget(_make_section_label("\u25ce \u9ad8\u7ea7\u529f\u80fd"))

        self.p2p_sftp_btn = QPushButton("\u6587\u4ef6\u7ba1\u7406")
        self.p2p_sftp_btn.setObjectName(u"p2p_sftp_btn")
        self.p2p_sftp_btn.setEnabled(False)
        p2p_main_layout.addWidget(self.p2p_sftp_btn)

        self.p2p_ssh_terminal_btn = QPushButton("SSH \u7ec8\u7aef")
        self.p2p_ssh_terminal_btn.setObjectName(u"p2p_ssh_terminal_btn")
        self.p2p_ssh_terminal_btn.setEnabled(False)
        p2p_main_layout.addWidget(self.p2p_ssh_terminal_btn)

        p2p_main_layout.addStretch()

        self.p2p_panel.setFixedWidth(290)
        self.p2p_panel.setVisible(False)

        self.horizontalLayout_main.addWidget(self.p2p_panel)

        self.verticalLayout_2.addLayout(self.horizontalLayout_main)

        MainWindow.setCentralWidget(self.centralwidget)

        self.retranslateUi(MainWindow)

        QMetaObject.connectSlotsByName(MainWindow)
    # setupUi

    def retranslateUi(self, MainWindow):
        MainWindow.setWindowTitle(QCoreApplication.translate("MainWindow", u"AutoWork", None))
        self.flush.setText(QCoreApplication.translate("MainWindow", u"\u5237\u65b0", None))
        self.date.setDisplayFormat(QCoreApplication.translate("MainWindow", u"yyyy-MM-dd", None))
        self.write_table.setText(QCoreApplication.translate("MainWindow", u"\u6253\u5f00\u76ee\u5f55", None))
        self.open_config.setText(QCoreApplication.translate("MainWindow", u"\u914d\u7f6e", None))
        self.label_2.setText(QCoreApplication.translate("MainWindow", u"\u7a0b\u5e8f:", None))
        self.input_frame_before.setText(QCoreApplication.translate("MainWindow", u"\u5e27\u524d", None))
        self.input_frame_set.setText(QCoreApplication.translate("MainWindow", u"\u5e27\u540e", None))
        self.input_frame_custom.setText(QCoreApplication.translate("MainWindow", u"\u81ea\u5b9a\u4e49", None))
        self.input_frame.setText(QCoreApplication.translate("MainWindow", u"400", None))
        self.open_daily.setText(QCoreApplication.translate("MainWindow", u"CPP\u65e5\u5fd7", None))
        self.start.setText(QCoreApplication.translate("MainWindow", u"\u64ad\u653e", None))
        self.end.setText(QCoreApplication.translate("MainWindow", u"\u7ed3\u675f", None))
        self.pause_btn.setText(QCoreApplication.translate("MainWindow", u"\u6682\u505c", None))
        self.p2p_btn.setText(QCoreApplication.translate("MainWindow", u"\u8fdc\u7a0b", None))
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
        id_header = QLabel("  \u8bbe\u5907")
        id_header.setObjectName(u"left_panel_header")
        id_header.setFixedHeight(26)
        id_header.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        id_layout.addWidget(id_header)
        id_layout.addWidget(self.id_list, 1)
        self.left_top_splitter.addWidget(id_widget)

        file_widget = QWidget()
        file_layout = QVBoxLayout(file_widget)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(0)
        file_header = QLabel("  \u6587\u4ef6 / \u65e5\u5fd7")
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
        log_header = QLabel("  \u65e5\u5fd7\u5185\u5bb9")
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

        self.splitter.addWidget(self.id_list)
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
        self.id_list.setParent(None)
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
