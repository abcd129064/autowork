# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'autowork.ui'
##
## Created by: Qt User Interface Compiler version 6.10.0
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
    QHBoxLayout, QLabel, QLayout, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QPlainTextEdit,
    QPushButton, QRadioButton, QSizePolicy, QSplitter, QVBoxLayout,
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
        self.date.setDisplayFormat("yyyy-MM-dd")
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

        self.pause_btn = QPushButton(self.centralwidget)
        self.pause_btn.setObjectName(u"pause_btn")

        self.horizontalLayout.addWidget(self.pause_btn)


        self.verticalLayout_2.addLayout(self.horizontalLayout)

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

        self.verticalLayout_2.addWidget(self.splitter)

        MainWindow.setCentralWidget(self.centralwidget)

        self.retranslateUi(MainWindow)

    # setupUi

    def retranslateUi(self, MainWindow):
        MainWindow.setWindowTitle(QCoreApplication.translate("MainWindow", u"autowork", None))
        self.flush.setText(QCoreApplication.translate("MainWindow", u"\u5237\u65b0", None))
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
    # retranslateUi

