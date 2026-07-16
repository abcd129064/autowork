# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'autowork_with_table.ui'
##
## Created by: Qt User Interface Compiler version 6.11.1
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
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
    QWidget)

class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        if not MainWindow.objectName():
            MainWindow.setObjectName(u"MainWindow")
        MainWindow.resize(1136, 659)
        self.centralwidget = QWidget(MainWindow)
        self.centralwidget.setObjectName(u"centralwidget")
        self.centralwidget.setEnabled(True)
        sizePolicy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.centralwidget.sizePolicy().hasHeightForWidth())
        self.centralwidget.setSizePolicy(sizePolicy)
        self.centralwidget.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        self.verticalLayout_2 = QVBoxLayout(self.centralwidget)
        self.verticalLayout_2.setSpacing(0)
        self.verticalLayout_2.setObjectName(u"verticalLayout_2")
        self.verticalLayout_2.setContentsMargins(3, 3, 3, 4)
        self.horizontalLayout = QHBoxLayout()
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.horizontalLayout.setSizeConstraint(QLayout.SizeConstraint.SetDefaultConstraint)
        self.flush = QPushButton(self.centralwidget)
        self.flush.setObjectName(u"flush")

        self.horizontalLayout.addWidget(self.flush)

        self.date = QDateEdit(self.centralwidget)
        self.date.setObjectName(u"date")
        self.date.setMaximumSize(QSize(128, 16777215))
        self.date.setCalendarPopup(True)
        self.date.setDateTime(QDateTime(QDate(2000, 10, 7), QTime(0, 0, 0)))

        self.horizontalLayout.addWidget(self.date)

        self.write_table = QPushButton(self.centralwidget)
        self.write_table.setObjectName(u"write_table")

        self.horizontalLayout.addWidget(self.write_table)

        self.open_config = QPushButton(self.centralwidget)
        self.open_config.setObjectName(u"open_config")

        self.horizontalLayout.addWidget(self.open_config)

        self.label_2 = QLabel(self.centralwidget)
        self.label_2.setObjectName(u"label_2")

        self.horizontalLayout.addWidget(self.label_2)

        self.choose_exe = QComboBox(self.centralwidget)
        self.choose_exe.setObjectName(u"choose_exe")

        self.horizontalLayout.addWidget(self.choose_exe)

        self.input_frame_before = QRadioButton(self.centralwidget)
        self.input_frame_before.setObjectName(u"input_frame_before")
        self.input_frame_before.setChecked(True)

        self.horizontalLayout.addWidget(self.input_frame_before)

        self.input_frame_set = QRadioButton(self.centralwidget)
        self.input_frame_set.setObjectName(u"input_frame_set")

        self.horizontalLayout.addWidget(self.input_frame_set)

        self.input_frame_custom = QRadioButton(self.centralwidget)
        self.input_frame_custom.setObjectName(u"input_frame_custom")

        self.horizontalLayout.addWidget(self.input_frame_custom)

        self.input_frame = QLineEdit(self.centralwidget)
        self.input_frame.setObjectName(u"input_frame")

        self.horizontalLayout.addWidget(self.input_frame)

        self.open_daily = QPushButton(self.centralwidget)
        self.open_daily.setObjectName(u"open_daily")

        self.horizontalLayout.addWidget(self.open_daily)

        self.start = QPushButton(self.centralwidget)
        self.start.setObjectName(u"start")

        self.horizontalLayout.addWidget(self.start)

        self.end = QPushButton(self.centralwidget)
        self.end.setObjectName(u"end")

        self.horizontalLayout.addWidget(self.end)

        # ===== pause_btn（暂停/恢复按钮）— 与 start/end 同行 =====
        self.pause_btn = QPushButton(self.centralwidget)
        self.pause_btn.setObjectName(u"pause_btn")
        self.pause_btn.setText("暂停")

        self.horizontalLayout.addWidget(self.pause_btn)

        self.p2p_btn = QPushButton(self.centralwidget)
        self.p2p_btn.setObjectName(u"p2p_btn")
        self.p2p_btn.setCheckable(True)
        self.p2p_btn.setMaximumWidth(50)

        self.horizontalLayout.addWidget(self.p2p_btn)


        self.verticalLayout_2.addLayout(self.horizontalLayout)

        self.horizontalLayout_main = QHBoxLayout()
        self.horizontalLayout_main.setObjectName(u"horizontalLayout_main")
        self.horizontalLayout_main.setContentsMargins(0, 0, 0, 0)
        self.horizontalLayout_main.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self.centralwidget)
        self.splitter.setObjectName(u"splitter")
        self.id_list = QListWidget(self.splitter)
        self.id_list.setObjectName(u"id_list")
        self.loacl_video_list = QListWidget(self.splitter)
        self.loacl_video_list.setObjectName(u"loacl_video_list")
        self.log_list = QListWidget(self.splitter)
        self.log_list.setObjectName(u"log_list")
        self.show_log = QPlainTextEdit(self.splitter)
        self.show_log.setObjectName(u"show_log")
        self.show_log.setReadOnly(True)
        self.splitter.setSizes([80, 150, 300, 500])

        self.horizontalLayout_main.addWidget(self.splitter)

        # --- 远程面板 ---
        self.p2p_panel = QFrame(self.centralwidget)
        self.p2p_panel.setObjectName(u"p2p_panel")
        self.p2p_panel.setFrameShape(QFrame.Shape.StyledPanel)
        p2p_main_layout = QVBoxLayout(self.p2p_panel)
        p2p_main_layout.setContentsMargins(6, 6, 6, 6)
        p2p_main_layout.setSpacing(4)

        self.p2p_visitor_list = QListWidget(self.p2p_panel)
        self.p2p_visitor_list.setObjectName(u"p2p_visitor_list")
        self.p2p_visitor_list.setMaximumHeight(120)
        p2p_main_layout.addWidget(self.p2p_visitor_list)

        p2p_list_btn_layout = QHBoxLayout()
        self.p2p_add_btn = QPushButton("添加")
        self.p2p_add_btn.setObjectName(u"p2p_add_btn")
        self.p2p_delete_btn = QPushButton("删除")
        self.p2p_delete_btn.setObjectName(u"p2p_delete_btn")
        p2p_list_btn_layout.addWidget(self.p2p_add_btn)
        p2p_list_btn_layout.addWidget(self.p2p_delete_btn)
        p2p_main_layout.addLayout(p2p_list_btn_layout)

        self.p2p_form_server = QLineEdit(self.p2p_panel)
        self.p2p_form_server.setObjectName(u"p2p_form_server")
        self.p2p_form_port = QSpinBox(self.p2p_panel)
        self.p2p_form_port.setObjectName(u"p2p_form_port")
        self.p2p_form_port.setRange(1024, 65535)
        self.p2p_form_key = QLineEdit(self.p2p_panel)
        self.p2p_form_key.setObjectName(u"p2p_form_key")
        self.p2p_form_key.setText("abc123")

        self.p2p_xtcp_form = QFormLayout()
        self.p2p_xtcp_form.addRow("serverName:", self.p2p_form_server)
        self.p2p_xtcp_form.addRow("bindPort:", self.p2p_form_port)
        self.p2p_xtcp_form.addRow("secretKey:", self.p2p_form_key)
        p2p_main_layout.addLayout(self.p2p_xtcp_form)

        # XTCP 专属控件列表（用于模式切换时显隐）
        self.p2p_xtcp_widgets = [
            self.p2p_visitor_list, self.p2p_add_btn, self.p2p_delete_btn,
            self.p2p_form_server, self.p2p_form_port, self.p2p_form_key
        ]

        self.p2p_connect_btn = QPushButton("连接", self.p2p_panel)
        self.p2p_connect_btn.setObjectName(u"p2p_connect_btn")
        self.p2p_disconnect_btn = QPushButton("断开", self.p2p_panel)
        self.p2p_disconnect_btn.setObjectName(u"p2p_disconnect_btn")
        p2p_main_layout.addWidget(self.p2p_connect_btn)
        p2p_main_layout.addWidget(self.p2p_disconnect_btn)

        self.p2p_sftp_btn = QPushButton("文件管理", self.p2p_panel)
        self.p2p_sftp_btn.setObjectName(u"p2p_sftp_btn")
        self.p2p_sftp_btn.setEnabled(False)
        p2p_main_layout.addWidget(self.p2p_sftp_btn)

        self.p2p_ssh_terminal_btn = QPushButton("SSH 终端", self.p2p_panel)
        self.p2p_ssh_terminal_btn.setObjectName(u"p2p_ssh_terminal_btn")
        self.p2p_ssh_terminal_btn.setEnabled(False)
        p2p_main_layout.addWidget(self.p2p_ssh_terminal_btn)

        # --- 分隔线 ---
        p2p_separator = QFrame(self.p2p_panel)
        p2p_separator.setFrameShape(QFrame.Shape.HLine)
        p2p_separator.setFrameShadow(QFrame.Shadow.Sunken)
        p2p_main_layout.addWidget(p2p_separator)

        # --- 连接方式选择 ---
        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("连接方式:"))
        self.p2p_mode_combo = QComboBox(self.p2p_panel)
        self.p2p_mode_combo.setObjectName(u"p2p_mode_combo")
        self.p2p_mode_combo.addItems(["XTCP", "TCP"])
        mode_layout.addWidget(self.p2p_mode_combo)
        p2p_main_layout.addLayout(mode_layout)

        # --- TCP 表单 ---
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
        self.p2p_ssh_pass.setText("Xqjjnbv155")

        self.p2p_ssh_form = QFormLayout()
        self.p2p_ssh_form.addRow("host:", self.p2p_ssh_host)
        self.p2p_ssh_form.addRow("port:", self.p2p_ssh_port)
        self.p2p_ssh_form.addRow("\u8d26\u53f7:", self.p2p_ssh_user)
        self.p2p_ssh_form.addRow("\u5bc6\u7801:", self.p2p_ssh_pass)
        p2p_main_layout.addLayout(self.p2p_ssh_form)

        # host/port 随模式切换显隐（账号/密码始终可见）
        self.p2p_ssh_widgets = [
            self.p2p_ssh_host, self.p2p_ssh_port,
        ]
        # 默认 XTCP 模式，隐藏 host/port 字段及其标签
        for w in self.p2p_ssh_widgets:
            w.setVisible(False)
        for row_idx in range(2):  # host(行0) 和 port(行1)
            lbl_item = self.p2p_ssh_form.itemAt(row_idx, QFormLayout.ItemRole.LabelRole)
            if lbl_item and lbl_item.widget():
                lbl_item.widget().setVisible(False)

        p2p_main_layout.addStretch()

        self.p2p_panel.setFixedWidth(260)
        self.p2p_panel.setVisible(False)

        self.horizontalLayout_main.addWidget(self.p2p_panel)

        self.verticalLayout_2.addLayout(self.horizontalLayout_main)

        MainWindow.setCentralWidget(self.centralwidget)

        self.retranslateUi(MainWindow)

        QMetaObject.connectSlotsByName(MainWindow)
    # setupUi

    def retranslateUi(self, MainWindow):
        MainWindow.setWindowTitle(QCoreApplication.translate("MainWindow", u"autowork", None))
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

