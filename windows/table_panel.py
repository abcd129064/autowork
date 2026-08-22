# -*- coding: utf-8 -*-
"""球桌管理面板（独立窗口模块，与原有业务代码解耦）

数据模式：
- 打开面板 → 读取本地 SQLite 缓存秒开（首次无数据时自动触发同步）
- 点「刷新」→ API 拉全量 → 写入 SQLite → 本地查询展示
- 搜索/翻页/列切换 → 纯本地 SQL 查询，零网络延迟
- 搜索为 textChanged 实时过滤（全字段模糊匹配）
"""

import math
from datetime import datetime

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QLabel, QApplication,
    QTextEdit)
from PySide6.QtCore import Qt, QItemSelectionModel, QTimer
from PySide6.QtGui import QColor, QShortcut, QKeySequence, QPalette
from qfluentwidgets import (TableWidget, SearchLineEdit, PushButton,
    PrimaryPushButton, ToolButton, FluentIcon, ComboBox, RoundMenu, CheckBox,
    Action, TransparentDropDownPushButton, LineEdit, PlainTextEdit,
    MenuAnimationType, MessageBox)
from qfluentwidgets.components.widgets.table_view import TableItemDelegate

from core.perf import is_animation_enabled
from core.theme_qss import apply_window_qss
from core.frp_remote import get_session_manager
from workers.table_worker import TableFetchWorker
from database import table_db


def _popup_ani_type():
    """按主界面「性能选项-动画效果」开关决定菜单弹出动画类型（与运维面板一致）"""
    return (MenuAnimationType.DROP_DOWN if is_animation_enabled()
            else MenuAnimationType.NONE)

# 表格列定义：(字段key, 表头文字, 默认宽度)
COLUMNS = [
    ("name", "球桌号", 90),
    ("roomName", "球房名称", 200),
    ("onlineStatusName", "在线状态", 80),
    ("remark", "备注", 470),
    ("cameraPassExt", "相机密码", 220),
]

# 在线状态着色
_STATUS_COLORS = {
    "运行中": QColor(16, 137, 62),   # 绿
    "空闲": QColor(0, 120, 212),     # 蓝
    "下线": QColor(130, 130, 130),   # 灰
}


class _ReadOnlySelectDelegate(TableItemDelegate):
    """双击单元格进入只读编辑态：支持光标拖选文本并复制，但禁止修改数据

    必须继承 TableItemDelegate（而非 QStyledItemDelegate），
    因为 qfluentwidgets TableWidget 内部会调用 setHoverRow/setSelectedRows。
    """

    def createEditor(self, parent, option, index):
        editor = QTextEdit(parent)
        editor.setReadOnly(True)  # 只读：可选中文本、Ctrl+C 复制，不能改内容
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 显式设置前景色，避免继承表格选中态白色文字
        pal = editor.palette()
        pal.setColor(QPalette.ColorRole.Text, self.parent().palette().color(QPalette.ColorRole.Text))
        editor.setPalette(pal)
        editor.setStyleSheet(
            "QTextEdit { background: palette(base); border: 1px solid palette(highlight);"
            " border-radius: 4px; padding: 2px;"
            " selection-background-color: palette(highlight);"
            " selection-color: palette(highlighted-text); }")
        return editor

    def setEditorData(self, editor, index):
        editor.setPlainText(index.data(Qt.ItemDataRole.DisplayRole) or "")

    def setModelData(self, editor, model, index):
        pass  # 只读，永不回写数据

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class AddRecordDialog(QDialog):
    """手动添加记录弹窗（API 失效时的兜底录入入口）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("手动添加记录")
        self.setFixedSize(420, 356)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(10)

        # 球桌号（必填）
        self._edit_name = LineEdit(self)
        form.addRow("球桌号:", self._edit_name)

        # 球房名称
        self._edit_room = LineEdit(self)
        form.addRow("球房名称:", self._edit_room)

        # 相机密码
        self._edit_camera = LineEdit(self)
        form.addRow("相机密码:", self._edit_camera)

        # SNK 标识（远程连接用，可留空自动从备注解析）
        self._edit_snk = LineEdit(self)
        self._edit_snk.setPlaceholderText("如 snk_001（留空则从备注解析）")
        form.addRow("SNK标识:", self._edit_snk)

        # 备注（多行）
        self._edit_remark = PlainTextEdit(self)
        self._edit_remark.setFixedHeight(80)
        form.addRow("备注:", self._edit_remark)

        layout.addLayout(form)
        layout.addStretch(1)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_cancel = PushButton("取消", self)
        self._btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_cancel)
        self._btn_ok = PrimaryPushButton("添加", self)
        self._btn_ok.clicked.connect(self._on_ok)
        btn_row.addWidget(self._btn_ok)
        layout.addLayout(btn_row)

    def _on_ok(self):
        """校验球桌号必填后接受对话框"""
        if not self._edit_name.text().strip():
            self._edit_name.setPlaceholderText("球桌号不能为空")
            self._edit_name.setFocus()
            return
        self.accept()

    def get_record(self) -> dict:
        """返回表单数据（与 COLUMNS 字段 key 对应）"""
        return {
            "name": self._edit_name.text().strip(),
            "roomName": self._edit_room.text().strip(),
            "onlineStatusName": "",
            "remark": self._edit_remark.toPlainText().strip(),
            "cameraPassExt": self._edit_camera.text().strip(),
            "snk_code": self._edit_snk.text().strip(),
        }


class TablePanelWindow(QDialog):
    """球桌管理面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        apply_window_qss(self)
        self.setWindowTitle("球桌管理")
        self.resize(1050, 560)
        self.setMinimumSize(700, 400)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)

        self._page_no: int = 1         # 当前页
        self._page_size: int = 30      # 每页条数
        self._total: int = 0           # 当前查询总条数
        self._worker: TableFetchWorker = None
        self._hidden_cols: set = {2}   # 默认隐藏「在线状态」列
        self._rows: list = []          # 当前页行数据（右键远程连接需读取 snk_code）
        self._show_test: bool = False    # 是否显示「公司测试」数据（默认不显示）
        self._show_manual: bool = False  # 是否显示手动版本设备（name 或 roomName 含 @s，默认不显示）

        self._init_ui()
        self._load_local()

        # 本地无数据时自动触发首次同步
        db_total, _ = table_db.get_meta()
        if db_total == 0:
            self._sync_from_api()

    # ==================== UI 构建 ====================

    def _init_ui(self):
        self._search_timer = None
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(8)

        # --- 工具栏 ---
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        self._search_edit = SearchLineEdit(self)
        self._search_edit.setPlaceholderText("搜索")
        self._search_edit.setFixedWidth(280)
        self._search_edit.textChanged.connect(self._on_search)
        toolbar.addWidget(self._search_edit)

        toolbar.addStretch(1)

        # 筛选按钮（列显隐，CheckBox 子菜单）
        self._col_btn = TransparentDropDownPushButton("筛选", self)
        self._col_btn.setIcon(FluentIcon.FILTER.qicon())
        self._build_col_menu()
        toolbar.addWidget(self._col_btn)

        # 刷新按钮（从API同步）
        self._refresh_btn = PushButton(FluentIcon.SYNC, "同步数据", self)
        self._refresh_btn.setToolTip("从服务器拉取全量数据")
        self._refresh_btn.clicked.connect(self._sync_from_api)
        toolbar.addWidget(self._refresh_btn)

        root.addLayout(toolbar)

        # --- 数据表格 ---
        self._table = TableWidget(self)
        self._table.setColumnCount(len(COLUMNS))
        self._table.setHorizontalHeaderLabels([c[1] for c in COLUMNS])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # 双击进入只读编辑态：光标可选择单元格内部分文本复制
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self._table.setItemDelegate(_ReadOnlySelectDelegate(self._table))
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(True)  # 备注列超宽自动换行
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_copy_menu)

        # Ctrl+C 复制选中内容（鼠标左键拖选后直接复制）
        QShortcut(QKeySequence.StandardKey.Copy, self._table).activated.connect(self._copy_selected)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)  # 末列自动填满剩余宽度：拉宽无缝隙、缩窄不溢出
        header.sectionResized.connect(self._fit_row_heights)
        for i, (_, _, w) in enumerate(COLUMNS):
            self._table.setColumnWidth(i, w)
        for i in self._hidden_cols:
            self._table.setColumnHidden(i, True)

        root.addWidget(self._table, 1)

        # --- 底部分页栏 ---
        pager = QHBoxLayout()
        pager.setSpacing(6)

        self._lbl_info = QLabel("正在加载...", self)
        pager.addWidget(self._lbl_info)

        pager.addStretch(1)

        # 每页条数
        pager.addWidget(QLabel("每页:", self))
        self._size_combo = ComboBox(self)
        self._size_combo.addItems(["30", "50", "100", "200"])
        self._size_combo.setFixedWidth(70)
        self._size_combo.currentTextChanged.connect(self._on_page_size_changed)
        pager.addWidget(self._size_combo)

        self._btn_prev = ToolButton(FluentIcon.LEFT_ARROW, self)
        self._btn_prev.setToolTip("上一页")
        self._btn_prev.clicked.connect(self._on_prev_page)
        pager.addWidget(self._btn_prev)

        self._lbl_page = QLabel("1/1", self)
        self._lbl_page.setMinimumWidth(50)
        self._lbl_page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pager.addWidget(self._lbl_page)

        self._btn_next = ToolButton(FluentIcon.RIGHT_ARROW, self)
        self._btn_next.setToolTip("下一页")
        self._btn_next.clicked.connect(self._on_next_page)
        pager.addWidget(self._btn_next)

        self._lbl_time = QLabel("", self)
        pager.addWidget(self._lbl_time)

        root.addLayout(pager)

    def _build_col_menu(self):
        """构建列显隐筛选菜单（CheckBox 复选框）"""
        menu = RoundMenu("筛选列", self)
        for i, (_, title, _) in enumerate(COLUMNS):
            cb = CheckBox(title, self)
            cb.setChecked(i not in self._hidden_cols)
            # 新建未布局的 CheckBox 默认 640x480 会导致菜单溢出截断，
            cb.setFixedSize(max(cb.sizeHint().width() + 30, 120), 36)
            cb.checkStateChanged.connect(lambda state, idx=i: self._toggle_col(idx, state == Qt.CheckState.Checked))
            menu.addWidget(cb, selectable=False)
        # 「公司测试」数据开关：默认不勾选（不显示），勾选后才展示
        menu.addSeparator()
        self._test_cb = CheckBox("公司测试", self)
        self._test_cb.setChecked(self._show_test)
        self._test_cb.setFixedSize(max(self._test_cb.sizeHint().width() + 30, 120), 36)
        self._test_cb.setToolTip("显示内部测试球房数据")
        self._test_cb.checkStateChanged.connect(self._toggle_test_data)
        menu.addWidget(self._test_cb, selectable=False)
        # 「手动版本」设备开关：默认不勾选（不显示 name/roomName 含 @s 的设备），勾选后才展示
        self._manual_cb = CheckBox("手动版本", self)
        self._manual_cb.setChecked(self._show_manual)
        self._manual_cb.setFixedSize(max(self._manual_cb.sizeHint().width() + 30, 120), 36)
        self._manual_cb.setToolTip("显示手动版本设备（名称或球房名含 @s）")
        self._manual_cb.checkStateChanged.connect(self._toggle_manual_data)
        menu.addWidget(self._manual_cb, selectable=False)
        self._col_btn.setMenu(menu)

    # ==================== 本地查询 ====================

    def _load_local(self):
        """从本地 SQLite 查询当前页并展示"""
        keyword = self._search_edit.text().strip()
        total, rows = table_db.query_page(
            self._page_no, self._page_size, keyword,
            include_test=self._show_test, include_manual=self._show_manual)
        self._total = total
        self._populate(rows)
        self._update_pager(keyword)
        # 显示数据同步时间
        _, sync_time = table_db.get_meta()
        self._lbl_time.setText(f"数据时间: {sync_time}" if sync_time else "未同步")

    # ==================== API 同步 ====================

    def _sync_from_api(self):
        """启动 Worker 拉取全量数据写入本地库"""
        if self._worker and self._worker.isRunning():
            return
        self._refresh_btn.setEnabled(False)
        self._lbl_info.setText("正在从服务器同步...")
        self._worker = TableFetchWorker()
        self._worker.result_ready.connect(self._on_sync_done)
        self._worker.error.connect(self._on_sync_error)
        self._worker.start()

    def _on_sync_done(self, rows):
        """API 数据到手：写入本地库 → 刷新展示"""
        count = table_db.save_all(rows)
        self._page_no = 1
        self._load_local()
        self._refresh_btn.setEnabled(True)
        self._lbl_info.setText(f"同步完成，共 {count} 条")

    def _on_sync_error(self, msg):
        """API 同步失败：恢复刷新按钮并展示错误"""
        self._refresh_btn.setEnabled(True)
        self._lbl_info.setText(f"同步失败: {msg}")

    # ==================== 表格填充 ====================

    def _populate(self, rows):
        """行数据 → 表格（换行压平、tooltip 存原文、在线状态着色）"""
        self._rows = list(rows)
        self._table.setRowCount(len(rows))
        for r, item in enumerate(rows):
            for c, (key, _, _) in enumerate(COLUMNS):
                val = item.get(key) or ""
                # 单元格内不支持换行显示：换行符压平为空格避免行高错乱
                val = str(val).replace("\n", " ").strip()
                cell = QTableWidgetItem(val)
                # tooltip 保留未压平的原始值：窄列显示不全时可悬停查看全文
                cell.setToolTip(str(item.get(key) or ""))
                # 在线状态着色
                if key == "onlineStatusName":
                    color = _STATUS_COLORS.get(val)
                    if color:
                        cell.setForeground(color)
                self._table.setItem(r, c, cell)
        self._fit_row_heights()

    def _update_pager(self, keyword=""):
        """按总数/页大小重算页码并同步分页控件与状态文本"""
        total_pages = max(1, math.ceil(self._total / self._page_size))
        self._page_no = min(self._page_no, total_pages)
        self._lbl_page.setText(f"{self._page_no}/{total_pages}")
        self._btn_prev.setEnabled(self._page_no > 1)
        self._btn_next.setEnabled(self._page_no < total_pages)
        kw_tip = f"（搜索: {keyword}）" if keyword else ""
        self._lbl_info.setText(
            f"共 {self._total} 条{kw_tip}，当前第 {self._page_no}/{total_pages} 页")

    # ==================== 交互事件 ====================

    def _on_search(self, text=""):
        """实时搜索：防抖 300ms 后执行"""
        if self._search_timer:
            self._search_timer.stop()
        self._search_timer = QTimer.singleShot(300, self._do_search)

    def _do_search(self):
        """实际执行搜索"""
        self._page_no = 1
        self._load_local()

    def _on_prev_page(self):
        if self._page_no > 1:
            self._page_no -= 1
            self._load_local()

    def _on_next_page(self):
        total_pages = max(1, math.ceil(self._total / self._page_size))
        if self._page_no < total_pages:
            self._page_no += 1
            self._load_local()

    def _on_page_size_changed(self, text):
        """每页条数变更：回第一页重查"""
        try:
            size = int(text)
        except ValueError:
            return
        if size != self._page_size:
            self._page_size = size
            self._page_no = 1
            self._load_local()

    def _toggle_col(self, col_idx, visible):
        """列显隐切换"""
        if visible:
            self._hidden_cols.discard(col_idx)
        else:
            self._hidden_cols.add(col_idx)
        self._table.setColumnHidden(col_idx, not visible)

    def _toggle_test_data(self, state):
        """「公司测试」数据显隐切换：回到第一页重新查询"""
        self._show_test = (state == Qt.CheckState.Checked)
        self._page_no = 1
        self._load_local()

    def _toggle_manual_data(self, state):
        """「手动版本」设备显隐切换：回到第一页重新查询"""
        self._show_manual = (state == Qt.CheckState.Checked)
        self._page_no = 1
        self._load_local()

    # ==================== 复制功能 ====================

    def _fit_row_heights(self):
        """行高自适应换行内容，并追加上下留白避免文字紧凑挤压"""
        self._table.resizeRowsToContents()
        for r in range(self._table.rowCount()):
            self._table.setRowHeight(r, max(self._table.rowHeight(r) + 14, 40))

    def _show_copy_menu(self, pos):
        """右键菜单：Fluent 风格复制 + 远程连接入口（无选中时先选中右键点击的单元格）"""
        idx = self._table.indexAt(pos)
        if not idx.isValid():
            return
        if not self._table.selectedItems():
            self._table.selectionModel().select(
                idx, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        menu = RoundMenu(parent=self._table)
        act = Action(FluentIcon.COPY, "复制", self._table)
        act.triggered.connect(self._copy_selected)
        menu.addAction(act)
        # 远程连接：按行数据的 snk 标识（frp xtcp visitor serverName），
        # 无 snk 的球桌菜单项保留可见但置灰并说明原因（与运维面板交互一致）
        self._add_remote_actions(menu, idx.row())
        menu.exec_(self._table.viewport().mapToGlobal(pos), aniType=_popup_ani_type())

    def _add_remote_actions(self, menu, row_idx):
        """右键菜单追加远程连接入口（SSH 终端 / SFTP 文件管理）

        snk 优先取行数据 snk_code 列（存储时已从 remark 解析/手动写入），
        兜底再从 remark 正则解析；两者皆无则该球桌不可远程。
        """
        row = self._rows[row_idx] if 0 <= row_idx < len(self._rows) else {}
        table_id = str(row.get("name") or "").strip()
        snk = (str(row.get("snk_code") or "").strip()
               or table_db.parse_snk_code(row.get("remark")))
        menu.addSeparator()
        remote_items = [
            ("ssh", FluentIcon.COMMAND_PROMPT, "SSH 终端"),
            ("sftp", FluentIcon.FOLDER, "SFTP 文件管理"),
        ]
        if not snk:
            # 置灰但可见：提示功能存在，仅该球桌缺 snk 配置不可用
            tip = Action(FluentIcon.INFO, "该球桌无 snk 标识，无法远程", self._table)
            tip.setEnabled(False)
            menu.addAction(tip)
        for kind, icon, label in remote_items:
            act = Action(icon, label if snk else f"{label}（无 snk）", self._table)
            act.setEnabled(bool(snk))
            if snk:
                act.triggered.connect(
                    lambda _=False, k=kind: self._open_remote_session(k, snk, table_id))
            menu.addAction(act)

    def _check_device_offline(self, table_name):
        """发起远程连接前的设备状态前置检查

        关联方式：kd_status.table_id ↔ 球桌管理 name（与设备状态页一致）；
        精确匹配不到时降级按球桌号模糊匹配 device_code；仍匹配不到则
        视为无法判断，跳过检查照常连接。单条 SQL 取最新日期分区的一条
        记录，任何异常静默跳过不阻塞连接。

        Returns:
            True 允许继续连接；False 用户确认取消（仅设备离线时弹框询问）
        """
        name = str(table_name or "").strip()
        if not name:
            return True
        try:
            # 经 table_db 双后端 API（主模式读 MySQL / 本地读 SQLite），
            # 不再裸连 SQLite 旁路。
            info = table_db.get_latest_kd_status(name)
            if not info:  # 降级：按球桌号模糊匹配 device_code
                info = table_db.get_latest_kd_status_by_code(name)
            if not info or str(info.get("status") or "").strip() != "0":
                return True  # 未匹配到记录或非离线状态，直接放行
            last_report = str(info.get("file_path") or "").strip() or "未知"
            dlg = MessageBox(
                "设备离线",
                f"球桌「{name}」对应设备已下线（最后上报：{last_report}）。\n仍要尝试连接吗？",
                self)
            dlg.yesButton.setText("仍要连接")
            dlg.cancelButton.setText("取消")
            return dlg.exec()
        except Exception:
            return True  # 查询异常静默跳过，照常连接

    def _open_remote_session(self, kind, snk, table_id):
        """委托统一远程会话中心建立 xtcp 隧道并打开会话

        会话中心为全局单例（共享单一 frpc 进程/TOML），同一 snk 已建隧道
        时直接复用；面板关闭不再 shutdown，frpc 由主窗口 closeEvent 统一关闭。
        发起连接前先做设备离线前置检查，用户取消则不建立隧道。
        """
        if not self._check_device_offline(table_id):
            return
        get_session_manager().open_session(kind, snk, table_id,
                                           notifier=self, source="球桌面板")

    def _copy_selected(self):
        """复制选中文本：编辑态优先复制光标选中部分，否则复制选中单元格"""
        # 若当前焦点在表格内的编辑器上且光标选中了部分文本，优先复制该部分
        focus = QApplication.focusWidget()
        if focus is not None and self._table.isAncestorOf(focus):
            tc_getter = getattr(focus, "textCursor", None)
            if tc_getter is not None:
                tc = focus.textCursor()
                if tc.hasSelection():
                    text = tc.selectedText().replace("\u2029", "\n")
                    QApplication.clipboard().setText(text)
                    return
        items = self._table.selectedItems()
        if not items:
            return
        cells = sorted((it.row(), it.column(), it.text()) for it in items)
        lines, cur_row, parts = [], None, []
        for row, col, text in cells:
            if row != cur_row:
                if parts:
                    lines.append("\t".join(parts))
                cur_row, parts = row, []
            parts.append(text)
        if parts:
            lines.append("\t".join(parts))
        QApplication.clipboard().setText("\n".join(lines))

    # ==================== 资源清理 ====================

    def closeEvent(self, event):
        # 远程会话中心为全局单例，面板关闭不 shutdown（避免误杀其他入口的隧道）
        if self._worker and self._worker.isRunning():
            self._worker.result_ready.disconnect()
            self._worker.error.disconnect()
            self._worker.quit()
            self._worker.wait(2000)
        super().closeEvent(event)
