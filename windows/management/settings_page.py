# -*- coding: utf-8 -*-
"""管理设置页：数据源选择、接口凭据、收集上传参数与连接测试。

本模块从 windows/management_panel.py 拆出，拆分的目的是让「运维面板」和
「统一设置页」能复用同一套配置读写逻辑：前者内嵌成独立工具窗，后者只取
其中的数据源 / 双接口 / MySQL 卡片，避免两处各写一份表单校验。
embedded 参数就是这个分歧点：为 True 时只平铺与数据库相关的卡片，
收集上传、手动添加、性能开关都不渲染，由统一设置页的对应分组接管。
"""

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
from core.perf import (is_acrylic_enabled, is_animation_enabled,
                       get_table_smooth, set_table_smooth)
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

class AddTableRecordCard(QWidget):
    """手动添加球桌记录卡：内嵌表单，写入本地数据库后刷新球桌管理页

    2026-09-07 由 AdminSettingsPage._build_add_card 抽出为独立组件：
    统一设置页「面板设置 → 运维」直接复用；AdminSettingsPage（独立
    窗口模式）经 _build_add_card 委托同款组件，行为不变。
    刷新走 self.window() 的 table_page 属性别名（主窗口已提供，
    见 main_window.py table_page property）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # 表单字段直接平铺、不做校验器：球桌号只在提交时判空，其余字段允许
        # 为空——运维补录时常常只记得球桌号，强制填全反而挡住了录入。
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = CardWidget(self)
        outer.addWidget(card)
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(8)
        vbox.addWidget(BodyLabel("手动添加球桌记录", card))
        vbox.addWidget(CaptionLabel(
            "直接在下方填写并提交，写入本地数据库（下次同步可能被接口数据覆盖）", card))

        form = QFormLayout()
        form.setSpacing(8)
        self._edit_name = LineEdit(card)
        self._edit_name.setPlaceholderText("球桌号")
        form.addRow("球桌号:", self._edit_name)
        self._edit_room = LineEdit(card)
        self._edit_room.setPlaceholderText("球房名称")
        form.addRow("球房名称:", self._edit_room)
        self._edit_camera = LineEdit(card)
        self._edit_camera.setPlaceholderText("相机密码")
        form.addRow("相机密码:", self._edit_camera)
        self._edit_snk = LineEdit(card)
        self._edit_snk.setPlaceholderText("如 snk_001（留空则从备注解析）")
        form.addRow("SNK标识:", self._edit_snk)
        self._edit_remark = PlainTextEdit(card)
        self._edit_remark.setFixedHeight(72)
        form.addRow("备注:", self._edit_remark)
        vbox.addLayout(form)

        add_row = QHBoxLayout()
        add_row.addStretch(1)
        self._btn_add = PrimaryPushButton(FluentIcon.ADD, "添加记录", card)
        self._btn_add.setToolTip("将表单内容写入本地数据库")
        self._btn_add.clicked.connect(self._on_add_record)
        add_row.addWidget(self._btn_add)
        vbox.addLayout(add_row)

    def _on_add_record(self):
        """从内嵌表单读取 → 写入本地数据库 → 刷新球桌管理页。

        写入的是本地表，下一次从接口同步时可能被覆盖，所以卡片上明确写了
        「下次同步可能被接口数据覆盖」，避免有人把它当权威数据源。
        """
        name = self._edit_name.text().strip()
        if not name:
            self._edit_name.setPlaceholderText("球桌号不能为空")
            self._edit_name.setFocus()
            return
        # onlineStatusName 置空而不是写「离线」：在线状态只能由接口给出，
        # 手动塞一个假值会让健康度统计把它算成真实设备。
        record = {
            "name": name,
            "roomName": self._edit_room.text().strip(),
            "onlineStatusName": "",
            "remark": self._edit_remark.toPlainText().strip(),
            "cameraPassExt": self._edit_camera.text().strip(),
            "snk_code": self._edit_snk.text().strip(),
        }
        table_db.insert_one(record)
        self._edit_name.clear()
        self._edit_room.clear()
        self._edit_camera.clear()
        self._edit_snk.clear()
        self._edit_remark.clear()
        # 组件同时被独立窗口和统一设置页复用，不能直接持有球桌页引用，
        # 只能通过宿主的 table_page 属性别名反查（主窗口已提供该属性）。
        win = self.window()
        page = getattr(win, "table_page", None)
        if page is not None:
            # 回到第一页：新记录按默认排序通常落在首屏之外，停在当前页会让
            # 用户以为添加失败。
            page._page_no = 1
            page._load_local()
        show_info_bar(f"球桌「{name}」已写入本地数据库", "success",
                      title="添加成功", parent=self, duration=2000)


class AdminSettingsPage(QWidget):
    """管理设置页：数据源选择、API 账号密码、连接测试。

    配置写入 settings.json 的 api_credentials 节点，保存后即时生效。
    接口凭据按 api1/api2 两个槽位存，active_source 决定球桌数据走哪一个；
    切换数据源不需要重启，因为采集 Worker 每次取数都重新读取当前值。
    """

    # 数据源选项：(显示文本, 存储值)
    _SOURCE_OPTIONS = [
        ("kd · 球房运维后台（kd.newbv.cn:30005）", "kd"),
        ("xqzg · 新球房运维后台（xqzg.newbv.cn）", "xqzg"),
    ]
    _API_LABELS = {"api1": "接口1 xqzg", "api2": "接口2 kd"}

    # 表格平滑滚动开关变更（管理窗口据此刷新各子页表格滚动模式）
    table_smooth_changed = Signal(bool)
    # 远程会话表格平滑滚动开关变更（管理窗口据此刷新已打开隧道/诊断窗口）
    remote_smooth_changed = Signal(bool)

    def __init__(self, parent=None, embedded: bool = False):
        super().__init__(parent)
        # embedded=True：嵌入统一设置页（2026-09-06）——去掉内部滚动区与
        # 性能卡（统一页性能组已有同款开关），由外层统一滚动
        self._embedded = embedded
        # 接口控件按 api_key 存字典：新增接口只加一行数据，不用同步改读写两处。
        # 懒加载：首次进入本页才构建 UI 并读配置，管理面板打开更快。
        self._lazy_built = False

    def _lazy_init(self):
        """首次进入才构建 UI 并读配置（懒加载，加快管理面板打开）"""
        # 只保留一个测试 Worker 引用：Qt 里 QThread 被 Python 侧回收时若仍在
        # 运行，会连带销毁底层线程对象，进程直接崩溃。
        self._test_worker = None
        self._user_edits = {}
        self._pass_edits = {}
        self._test_btns = {}
        self._init_ui()
        self._load_current()

    def showEvent(self, event):
        # 首次显示时才建 UI：本页控件数量在管理面板里最多，构造耗时占到面板
        # 打开的可见延迟，而多数用户不会一进来就改设置。
        super().showEvent(event)
        if not self._lazy_built:
            self._lazy_built = True
            self._lazy_init()

    def _init_ui(self):
        """构建数据源/接口/上传/添加卡片 + 保存按钮

        独立页模式：外层包 ScrollArea 由本页自行滚动；
        embedded 模式（嵌入统一设置页）：直接平铺卡片，跳过性能卡
        （统一页性能组已有同款开关），滚动由外层统一处理。
        """
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        # 两种宿主布局不同：独立页自己包滚动区，嵌入模式交给外层统一滚动，
        # 否则会出现嵌套滚动条，滚轮事件归属也会互相抢。
        if self._embedded:
            layout = root
            host = self
        else:
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
            host = view

        layout.addWidget(self._build_source_card(host))
        # 两个接口认证方式不同：xqzg 用 Session Cookie，kd 用 JWT，因此各自
        # 保留账号密码，不能合并成一组共用。
        layout.addWidget(self._build_api_card(
            # （Session 认证）
            host, "api1", "接口1 · xqzg", "xqzg.newbv.cn"))
        layout.addWidget(self._build_api_card(
            # （JWT 认证）
            host, "api2", "接口2 · kd", "kd.newbv.cn:30005"))
        if not self._embedded:
            # embedded（统一设置页「数据库」）仅保留 数据源/双接口/MySQL/保存：
            # 收集上传→统一设置「应用配置」、手动添加球桌→「面板设置→运维」、
            # 性能开关→统一设置「性能」组（2026-09-07 迁移）；
            # 独立窗口模式（windows/management/window.py）保持原样
            layout.addWidget(self._build_upload_card(host))
            layout.addWidget(self._build_add_card(host))
            layout.addWidget(self._build_perf_card(host))
        # MySQL 远程同步卡片自带保存按钮、独立写 settings.json：它管的是库连接
        # 而不是接口凭据，混进下面的统一保存会让「保存设置」牵动生产库配置。
        self._mysql_card = MysqlSyncCard(host, sync_scope="ops")
        self._mysql_card.load()
        layout.addWidget(self._mysql_card)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_save = PrimaryPushButton(FluentIcon.SAVE, "保存设置", host)
        self._btn_save.setToolTip("将以上配置写入 settings.json，保存后即时生效")
        # 保存按钮放在卡片流末尾而不是固定底栏：嵌入模式下本页不掌握滚动
        # 容器高度，固定底栏会盖住最后一张卡片的输入框。
        self._btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self._btn_save)
        # 末尾留伸缩，卡片少时不会被垂直居中拉开间距
        layout.addLayout(btn_row)
        layout.addStretch(1)

    def _build_perf_card(self, parent) -> QWidget:
        """性能卡片：管理面板表格 / 远程会话表格平滑滚动开关（面板级覆盖→全局）

        开关即时生效（set_table_smooth 持久化 + 信号通知管理窗口刷新各表格）；
        未单独拨动时跟随主界面全局开关。
        """
        card = CardWidget(parent)
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(8)
        vbox.addWidget(BodyLabel("性能", card))
        vbox.addWidget(CaptionLabel(
            "管理面板表格与远程会话表格的平滑滚动动画；"
            "未单独拨动时跟随主界面全局开关", card))

        row_mgmt = QHBoxLayout()
        row_mgmt.setSpacing(8)
        lbl_mgmt = BodyLabel("管理面板表格平滑滚动", card)
        lbl_mgmt.setToolTip(
            "关闭后管理面板的球桌/设备/健康度等表格走原生滚动，滚动更跟手")
        row_mgmt.addWidget(lbl_mgmt, 1)
        self.sw_mgmt_smooth = SwitchButton(card)
        # 开关文案显式写「开/关」而不是留空：SwitchButton 默认只有圆点状态，
        # 在深浅色主题下对比度都低，运维同事反馈过看不清当前状态。
        self.sw_mgmt_smooth.setOnText("开")
        self.sw_mgmt_smooth.setOffText("关")
        # 先回显当前生效值，再连接信号，避免初始化 setChecked 误触发持久化
        self.sw_mgmt_smooth.setChecked(get_table_smooth("management"))
        self.sw_mgmt_smooth.checkedChanged.connect(
            self._on_mgmt_smooth_toggled)
        row_mgmt.addWidget(self.sw_mgmt_smooth)
        vbox.addLayout(row_mgmt)

        row_remote = QHBoxLayout()
        row_remote.setSpacing(8)
        lbl_remote = BodyLabel("远程会话表格平滑滚动", card)
        lbl_remote.setToolTip(
            "关闭后当前隧道列表 / 连接诊断表格走原生滚动")
        row_remote.addWidget(lbl_remote, 1)
        self.sw_remote_smooth = SwitchButton(card)
        self.sw_remote_smooth.setOnText("开")
        self.sw_remote_smooth.setOffText("关")
        self.sw_remote_smooth.setChecked(get_table_smooth("remote"))
        self.sw_remote_smooth.checkedChanged.connect(
            self._on_remote_smooth_toggled)
        row_remote.addWidget(self.sw_remote_smooth)
        vbox.addLayout(row_remote)
        return card

    def _on_mgmt_smooth_toggled(self, checked):
        """管理面板表格平滑滚动切换：持久化 + 通知管理窗口刷新各子页表格。

        持久化只保证下次打开生效，已构建的表格要立刻改行为，必须靠信号让
        管理窗口逐个回填滚动模式，否则用户会看到「开关变了但没反应」。
        """
        set_table_smooth("management", checked)
        self.table_smooth_changed.emit(checked)

    def _on_remote_smooth_toggled(self, checked):
        """远程会话表格平滑滚动切换：持久化 + 通知管理窗口刷新已打开窗口"""
        set_table_smooth("remote", checked)
        self.remote_smooth_changed.emit(checked)

    def _build_add_card(self, parent):
        """手动添加球桌记录：内嵌表单卡片。

        表单本体已抽成 AddTableRecordCard，这里只做一层壳：独立窗口模式需要
        它作为卡片挂在设置页里，统一设置页则把同一组件挂到「面板设置 → 运维」，
        两处共用一份提交逻辑。
        """
        return AddTableRecordCard(parent)

    def _build_upload_card(self, parent):
        """收集与上传：与主界面设置对话框同款配置。

        刻意复用 settings.json 的同名字段而不是另起一套键：主界面设置
        对话框和这里改的是同一份上传目标，分键会导致两边显示不一致。
        """
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
        """数据源选择卡片，在 kd 与 xqzg 两套运维接口之间切换。

        切换只写 active_source，不迁移已有数据：两套接口的球桌号口径不同，
        自动合并会造出重复记录，交给下一次全量同步覆盖更安全。
        """
        card = CardWidget(parent)
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(8)
        vbox.addWidget(BodyLabel("设备状态数据源", card))
        vbox.addWidget(CaptionLabel(
            "球房运维管理数据接口", card))
        self._source_combo = ComboBox(card)
        self._source_combo.addItems([text for text, _ in self._SOURCE_OPTIONS])
        # 固定宽度而非自适应：选项文本包含域名，自适应会随字体缩放忽宽忽窄，
        # 让整张卡片在切换数据源时抖动。
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
        # 用默认参数把 api_key 绑进闭包：循环里直接引用外部变量时，回调执行
        # 时读到的会是最后一次迭代的值，两张卡片都会去测 api2。
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
        # 配置里的大小写不受控（手改过 settings.json 的机器上出现过 "KD"），
        # 统一小写后比较；未知值回落到第一项，避免下拉框停在空白态。
        active = str(creds.get("active_source", "kd")).lower()
        idx = next((i for i, (_, v) in enumerate(self._SOURCE_OPTIONS) if v == active), 0)
        self._source_combo.setCurrentIndex(idx)
        # 收集与上传（与主界面设置同键）；embedded 模式已迁出统一设置页，不构建
        if getattr(self, "_edit_upload_host", None) is not None:
            # 默认值直接给生产上传机：新机器上没配过也能开箱可用，猜错时
            # 用户看到的是真实 IP，比空输入框更容易发现需要改。
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
        # 整份 api_credentials 一次写入：_save_settings 是「读旧→合并→整体覆盖」，
        # 分多次调用会让中间态落盘，出现新账号配旧密码这类半更新。
        try:
            data = {"api_credentials": api_credentials}
            # 收集与上传仅独立窗口模式有此表单（embedded 已迁出统一设置页）
            if getattr(self, "_edit_upload_host", None) is not None:
                # 端口非法时静默回落到 22，而不是弹错拦下整次保存：上传配置
                # 和接口凭据在同一次保存里，为端口挡住会让用户改不了密码。
                try:
                    upload_port = int(self._edit_upload_port.text().strip() or 22)
                except ValueError:
                    upload_port = 22
                data.update({
                    "upload_host": self._edit_upload_host.text().strip(),
                    "upload_port": upload_port,
                    "upload_remote_dir": self._edit_upload_dir.text().strip(),
                    "upload_user": self._edit_upload_user.text().strip() or "root",
                    "upload_pass": self._edit_upload_pass.text(),
                })
            _save_settings(data)
            show_info_bar("配置已写入 settings.json，即时生效", "success",
                          title="已保存", parent=self, duration=2500)
        # 写盘失败只提示不抛出：这是 GUI 回调，异常冒到 Qt 事件循环会被吞掉，
        # 用户只会看到「点了保存没反应」。
        except Exception as e:
            show_info_bar(str(e), "error",
                          title="保存失败", parent=self, duration=4000)

    # ---------- 测试连接 ----------

    def _on_test(self, api_key):
        """测试连接：用当前表单凭据异步登录（进行中不重复发起）"""
        # 同一个 Worker 只跑一个登录：并发测试时 success/error 信号会带着
        # 不同 api_key 交错回来，按钮的启用状态会互相覆盖。
        if self._test_worker and self._test_worker.isRunning():
            show_info_bar("已有测试进行中，请稍候", "warning",
                          title="提示", parent=self, duration=2000)
            return
        # 只禁用被点的那一个按钮，另一张卡片仍可测：两个接口互相独立，
        # 全局锁定会让人以为整个页面卡住了。
        self._test_btns[api_key].setEnabled(False)
        self._test_worker = LoginTestWorker(
            api_key,
            username=self._user_edits[api_key].text().strip(),
            password=self._pass_edits[api_key].text())
        # 回调里补上 api_key：信号只带结果文本，不带它属于哪张卡片。
        self._test_worker.success.connect(
            lambda msg, k=api_key: self._on_test_done(k, True, msg))
        self._test_worker.error.connect(
            lambda msg, k=api_key: self._on_test_done(k, False, msg))
        self._test_worker.start()

    def _on_test_done(self, api_key, ok, msg):
        """测试完成：恢复按钮并按成败提示。

        无论成败都恢复按钮：登录失败最常见的原因就是密码写错，此时用户
        需要立刻改完重测，按钮若保持禁用就只能关页面重开。
        """
        self._test_btns[api_key].setEnabled(True)
        label = self._API_LABELS.get(api_key, api_key)
        if ok:
            show_info_bar(msg, "success", title=f"{label} 连接成功",
                          parent=self, duration=2500)
        else:
            show_info_bar(msg, "error", title=f"{label} 连接失败",
                          parent=self, duration=4000)
