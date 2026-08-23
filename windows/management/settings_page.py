# -*- coding: utf-8 -*-
"""settings_page 模块（从 windows/management_panel.py 拆出，逻辑未改动）"""

import csv
import difflib
import json
import logging
import math
import os
import re
import shutil
import subprocess
from datetime import datetime

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QLabel, QApplication,
    QTextEdit, QDialog, QPushButton, QCheckBox, QTextBrowser, QTreeWidgetItem,
    QFileDialog, QToolTip, QFrame, QListWidget, QListWidgetItem, QAbstractScrollArea,
    QTabWidget)
from PySide6.QtCore import (Qt, QItemSelectionModel, QDate, QPoint, QPropertyAnimation,
    QEasingCurve, QTimer, QEvent, QThread, Signal, QRectF, QSize, QDateTime)
from PySide6.QtGui import (QColor, QShortcut, QKeySequence, QPalette, QCursor,
    QPainter, QPen, QFont, QFontMetrics, QBrush)
from qfluentwidgets import (TableWidget, SearchLineEdit, PushButton,
    PrimaryPushButton, ToolButton, FluentIcon, ComboBox, RoundMenu, CheckBox,
    Action, TransparentDropDownPushButton, LineEdit, PlainTextEdit,
    FluentWindow, NavigationItemPosition, ProgressBar, TitleLabel,
    BodyLabel, CaptionLabel, CalendarPicker, PasswordLineEdit, ScrollArea,
    CardWidget, setCustomStyleSheet, qconfig, isDarkTheme, MessageBox, TreeWidget,
    MessageBoxBase, MenuAnimationType, SwitchButton)
from qfluentwidgets.components.widgets.table_view import TableItemDelegate

from core.app_paths import get_app_dir
from core.design_tokens import SEMANTIC
from core.frp_remote import get_session_manager
from core.perf import is_acrylic_enabled, is_animation_enabled
from core.secrets import decrypt_settings, encrypt_settings
from core.utils import launch_sibling_app, show_info_bar
from workers.table_worker import (TableFetchWorker, DevicesFetchWorker,
                                  SnookerOmFetchWorker, MigrateImageWorker,
                                  LoginTestWorker, get_active_api_source,
                                  CATEGORY_DIRS)
from workers.collect_worker import (CollectFilesWorker, ZipUploadWorker,
                                    clip_base_name, date_from_base,
                                    resolve_device_dir,
                                    fuzzy_match_device_dir, norm_device_suffix)
from database import table_db
from windows.mysql_sync_card import MysqlSyncCard
from windows.management.moyu_widgets import (Game2048Widget, SnakeWidget,
                                                  MoyuReaderWidget)
from windows.management.image_viewer import is_image_file

logger = logging.getLogger(__name__)

from windows.management.common import *  # noqa: F401,F403
from windows.management.dialogs import (
    AddRecordDialog, EditSnkDialog, DeviceDirHealDialog,
    DeviceFilesDialog, UploadListDialog,
)

# ==================== 页面3: 管理设置 ====================

class AdminSettingsPage(QWidget):
    """管理设置页：数据源选择、API 账号密码、连接测试

    配置写入 settings.json 的 api_credentials 节点，保存后即时生效。
    """

    # 数据源选项：(显示文本, 存储值)
    _SOURCE_OPTIONS = [
        ("kd · 球房运维后台（kd.newbv.cn:30005）", "kd"),
        ("xqzg · 新球房运维后台（xqzg.newbv.cn）", "xqzg"),
    ]
    _API_LABELS = {"api1": "接口1 xqzg", "api2": "接口2 kd"}

    def __init__(self, parent=None):
        super().__init__(parent)
        # 懒加载：首次进入本页才构建 UI 与读取配置（管理面板打开更快）
        self._lazy_built = False

    def _lazy_init(self):
        """首次进入才构建 UI 并读配置（懒加载，加快管理面板打开）"""
        self._test_worker = None
        self._user_edits = {}
        self._pass_edits = {}
        self._test_btns = {}
        self._init_ui()
        self._load_current()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._lazy_built:
            self._lazy_built = True
            self._lazy_init()

    def _init_ui(self):
        """构建滚动区 + 数据源/接口/上传/添加四张卡片 + 保存按钮"""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 滚动区自身不参与焦点：避免点击后焦点转移触发 ensureVisible 自动滚动（画面跳动）
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        view = QWidget()
        view.setStyleSheet("QWidget { background: transparent; }")
        scroll.setWidget(view)
        root.addWidget(scroll)

        layout = QVBoxLayout(view)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        layout.addWidget(self._build_source_card(view))
        layout.addWidget(self._build_api_card(
            # （Session 认证）
            view, "api1", "接口1 · xqzg", "xqzg.newbv.cn"))
        layout.addWidget(self._build_api_card(
            # （JWT 认证）
            view, "api2", "接口2 · kd", "kd.newbv.cn:30005"))
        layout.addWidget(self._build_upload_card(view))
        layout.addWidget(self._build_add_card(view))
        # MySQL 远程同步配置（可复用组件，独立保存 settings.json）
        self._mysql_card = MysqlSyncCard(view, sync_scope="ops")
        self._mysql_card.load()
        layout.addWidget(self._mysql_card)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_save = PrimaryPushButton(FluentIcon.SAVE, "保存设置", view)
        self._btn_save.setToolTip("将以上配置写入 settings.json，保存后即时生效")
        self._btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self._btn_save)
        layout.addLayout(btn_row)
        layout.addStretch(1)

    def _on_add_record(self):
        """从内嵌表单读取 → 写入本地数据库 → 刷新球桌管理页"""
        name = self._add_edit_name.text().strip()
        if not name:
            self._add_edit_name.setPlaceholderText("球桌号不能为空")
            self._add_edit_name.setFocus()
            return
        record = {
            "name": name,
            "roomName": self._add_edit_room.text().strip(),
            "onlineStatusName": "",
            "remark": self._add_edit_remark.toPlainText().strip(),
            "cameraPassExt": self._add_edit_camera.text().strip(),
            "snk_code": self._add_edit_snk.text().strip(),
        }
        table_db.insert_one(record)
        self._add_edit_name.clear()
        self._add_edit_room.clear()
        self._add_edit_camera.clear()
        self._add_edit_snk.clear()
        self._add_edit_remark.clear()
        win = self.window()
        page = getattr(win, "table_page", None)
        if page is not None:
            page._page_no = 1
            page._load_local()
        show_info_bar(f"球桌「{name}」已写入本地数据库", "success",
                      title="添加成功", parent=self, duration=2000)

    def _build_add_card(self, parent):
        """手动添加球桌记录：内嵌表单卡片，无需弹窗"""
        card = CardWidget(parent)
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(8)
        vbox.addWidget(BodyLabel("手动添加球桌记录", card))
        vbox.addWidget(CaptionLabel(
            "直接在下方填写并提交，写入本地数据库（下次同步可能被接口数据覆盖）", card))

        form = QFormLayout()
        form.setSpacing(8)
        self._add_edit_name = LineEdit(card)
        self._add_edit_name.setPlaceholderText("球桌号")
        form.addRow("球桌号:", self._add_edit_name)
        self._add_edit_room = LineEdit(card)
        self._add_edit_room.setPlaceholderText("球房名称")
        form.addRow("球房名称:", self._add_edit_room)
        self._add_edit_camera = LineEdit(card)
        self._add_edit_camera.setPlaceholderText("相机密码")
        form.addRow("相机密码:", self._add_edit_camera)
        self._add_edit_snk = LineEdit(card)
        self._add_edit_snk.setPlaceholderText("如 snk_001（留空则从备注解析）")
        form.addRow("SNK标识:", self._add_edit_snk)
        self._add_edit_remark = PlainTextEdit(card)
        self._add_edit_remark.setFixedHeight(72)
        form.addRow("备注:", self._add_edit_remark)
        vbox.addLayout(form)

        add_row = QHBoxLayout()
        add_row.addStretch(1)
        self._btn_add = PrimaryPushButton(FluentIcon.ADD, "添加记录", card)
        self._btn_add.setToolTip("将表单内容写入本地数据库")
        self._btn_add.clicked.connect(self._on_add_record)
        add_row.addWidget(self._btn_add)
        vbox.addLayout(add_row)
        return card

    def _build_upload_card(self, parent):
        """收集与上传：与主界面设置对话框同款配置（复用 settings.json 同键）"""
        card = CardWidget(parent)
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(8)
        vbox.addWidget(BodyLabel("收集与上传", card))
        vbox.addWidget(CaptionLabel(
            "精度/问题文件收集打包上传；文件收集到 视频/日志目录/upload", card))

        form = QFormLayout()
        form.setSpacing(8)
        self._edit_upload_host = LineEdit(card)
        self._edit_upload_host.setPlaceholderText("上传服务器 IP")
        form.addRow("上传服务器:", self._edit_upload_host)
        self._edit_upload_port = LineEdit(card)
        self._edit_upload_port.setPlaceholderText("端口号（默认 22）")
        form.addRow("上传端口:", self._edit_upload_port)
        self._edit_upload_dir = LineEdit(card)
        self._edit_upload_dir.setPlaceholderText("如 /lhcos-data/videos")
        form.addRow("远程目录:", self._edit_upload_dir)
        self._edit_upload_user = LineEdit(card)
        self._edit_upload_user.setPlaceholderText("上传用户名（默认 root）")
        form.addRow("上传用户名:", self._edit_upload_user)
        self._edit_upload_pass = PasswordLineEdit(card)
        self._edit_upload_pass.setPlaceholderText("上传密码")
        form.addRow("上传密码:", self._edit_upload_pass)
        vbox.addLayout(form)
        return card

    def _build_source_card(self, parent):
        """数据源选择卡片（kd / xqzg 切换）"""
        card = CardWidget(parent)
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(8)
        vbox.addWidget(BodyLabel("设备状态数据源", card))
        vbox.addWidget(CaptionLabel(
            "球房运维管理数据接口", card))
        self._source_combo = ComboBox(card)
        self._source_combo.addItems([text for text, _ in self._SOURCE_OPTIONS])
        self._source_combo.setFixedWidth(340)
        vbox.addWidget(self._source_combo)
        return card

    def _build_api_card(self, parent, api_key, title, desc):
        """单个接口账号卡片：账号/密码表单 + 测试连接按钮"""
        card = CardWidget(parent)
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(8)
        vbox.addWidget(BodyLabel(title, card))
        vbox.addWidget(CaptionLabel(desc, card))

        form = QFormLayout()
        form.setSpacing(8)
        user_edit = LineEdit(card)
        user_edit.setPlaceholderText("账号")
        form.addRow("账号:", user_edit)
        pass_edit = PasswordLineEdit(card)
        pass_edit.setPlaceholderText("密码")
        form.addRow("密码:", pass_edit)
        vbox.addLayout(form)
        self._user_edits[api_key] = user_edit
        self._pass_edits[api_key] = pass_edit

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        test_btn = PushButton(FluentIcon.LINK, "测试连接", card)
        test_btn.setToolTip("使用当前填写的账号密码尝试登录")
        # 点击不获焦：禁用时不会转移焦点到下方控件，避免 ScrollArea 自动滚动导致画面下移
        test_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        test_btn.clicked.connect(lambda _=False, k=api_key: self._on_test(k))
        btn_row.addWidget(test_btn)
        vbox.addLayout(btn_row)
        self._test_btns[api_key] = test_btn
        return card

    # ---------- 读写配置 ----------

    def _load_current(self):
        """从 settings.json 加载当前配置填充到界面"""
        settings = _load_settings()
        creds = settings.get("api_credentials", {})
        for api_key in ("api1", "api2"):
            cfg = creds.get(api_key, {})
            self._user_edits[api_key].setText(cfg.get("username", ""))
            self._pass_edits[api_key].setText(cfg.get("password", ""))
        active = str(creds.get("active_source", "kd")).lower()
        idx = next((i for i, (_, v) in enumerate(self._SOURCE_OPTIONS) if v == active), 0)
        self._source_combo.setCurrentIndex(idx)
        # 收集与上传（与主界面设置同键）
        self._edit_upload_host.setText(str(settings.get("upload_host", "49.235.34.253")))
        self._edit_upload_port.setText(str(settings.get("upload_port", 22)))
        self._edit_upload_dir.setText(
            str(settings.get("upload_remote_dir", "/lhcos-data/videos")))
        self._edit_upload_user.setText(str(settings.get("upload_user", "root")))
        self._edit_upload_pass.setText(str(settings.get("upload_pass", "")))

    def _on_save(self):
        """保存设置：合并接口凭据/数据源/上传配置写 settings.json，即时生效"""
        api_credentials = {
            "api1": {
                "username": self._user_edits["api1"].text().strip(),
                "password": self._pass_edits["api1"].text(),
            },
            "api2": {
                "username": self._user_edits["api2"].text().strip(),
                "password": self._pass_edits["api2"].text(),
            },
            "active_source": self._SOURCE_OPTIONS[self._source_combo.currentIndex()][1],
        }
        try:
            upload_port = int(self._edit_upload_port.text().strip() or 22)
        except ValueError:
            upload_port = 22
        try:
            _save_settings({
                "api_credentials": api_credentials,
                "upload_host": self._edit_upload_host.text().strip(),
                "upload_port": upload_port,
                "upload_remote_dir": self._edit_upload_dir.text().strip(),
                "upload_user": self._edit_upload_user.text().strip() or "root",
                "upload_pass": self._edit_upload_pass.text(),
            })
            show_info_bar("配置已写入 settings.json，即时生效", "success",
                          title="已保存", parent=self, duration=2500)
        except Exception as e:
            show_info_bar(str(e), "error",
                          title="保存失败", parent=self, duration=4000)

    # ---------- 测试连接 ----------

    def _on_test(self, api_key):
        """测试连接：用当前表单凭据异步登录（进行中不重复发起）"""
        if self._test_worker and self._test_worker.isRunning():
            show_info_bar("已有测试进行中，请稍候", "warning",
                          title="提示", parent=self, duration=2000)
            return
        self._test_btns[api_key].setEnabled(False)
        self._test_worker = LoginTestWorker(
            api_key,
            username=self._user_edits[api_key].text().strip(),
            password=self._pass_edits[api_key].text())
        self._test_worker.success.connect(
            lambda msg, k=api_key: self._on_test_done(k, True, msg))
        self._test_worker.error.connect(
            lambda msg, k=api_key: self._on_test_done(k, False, msg))
        self._test_worker.start()

    def _on_test_done(self, api_key, ok, msg):
        """测试完成：恢复按钮并按成败提示"""
        self._test_btns[api_key].setEnabled(True)
        label = self._API_LABELS.get(api_key, api_key)
        if ok:
            show_info_bar(msg, "success", title=f"{label} 连接成功",
                          parent=self, duration=2500)
        else:
            show_info_bar(msg, "error", title=f"{label} 连接失败",
                          parent=self, duration=4000)
