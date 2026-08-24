# -*- coding: utf-8 -*-
"""dialogs 模块（从 windows/aftersale_panel.py 拆出，逻辑未改动）"""

import os
from datetime import datetime, timedelta

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QLabel, QListWidget,
    QListWidgetItem, QFileDialog, QComboBox as _QComboBox, QApplication,
    QDialog, QPushButton as _QPushButton, QGridLayout)
from PySide6.QtCore import Qt, QTimer, QThread, QPointF, QDate, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QFont
from qfluentwidgets import (TableWidget, SearchLineEdit, PushButton,
    PrimaryPushButton, ToolButton, FluentIcon, RoundMenu, Action,
    LineEdit, PlainTextEdit, BodyLabel, CaptionLabel, TitleLabel,
    ScrollArea, CardWidget, MessageBox, MessageBoxBase, CheckBox,
    FluentWindow, NavigationItemPosition, MenuAnimationType,
    setCustomStyleSheet, qconfig, isDarkTheme, ZhDatePicker, RadioButton,
    SpinBox, SegmentedWidget, ProgressBar, FlowLayout)

from core.design_tokens import SEMANTIC, lighten, darken
from core.flow_widgets import FlowToolbarScrollArea
from core.perf import is_acrylic_enabled
from core.theme_qss import current_accent_hex
from core.utils import show_info_bar
from database import aftersale_db, table_db
from workers.aftersale_worker import AftersaleDBWorker
from windows.mysql_sync_card import MysqlSyncCard

from windows.aftersale.common import *  # noqa: F401,F403
from windows.aftersale.form import AftersaleForm

# ==================== 编辑弹窗 ====================

class EditRecordDialog(MessageBoxBase):
    """编辑售后记录弹窗：复用共享表单，确认后由调用方异步落库"""

    def __init__(self, record: dict, parent=None, title: str = "编辑售后记录"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._record = record
        self.form = AftersaleForm(self)
        self.form.set_values(record)
        self.viewLayout.addWidget(self.form)
        # MessageBoxBase 无系统标题栏，顶部自建 TitleLabel 标明用途（编辑/新增）
        ttl = TitleLabel(title, self)
        self.viewLayout.insertWidget(0, ttl)
        self.yesButton.setText("保存")
        self.yesButton.clicked.connect(self._on_yes)
        self.cancelButton.setText("取消")
        # 弹窗宽度：表单控件较宽
        self.widget.setMinimumWidth(560)

    def _on_yes(self):
        """保存前校验必填；不通过则阻止关闭"""
        missing = self.form.validate()
        if missing:
            show_info_bar(f"请先填写必填项: {'、'.join(missing)}", "warning",
                          title="无法保存", parent=self, duration=3000)
            # MessageBoxBase 的 yesButton 默认触发 accept，这里用重新校验拦截：
            # 校验失败时把结果标记到属性上，由 exec 返回值区分
            self._validation_ok = False
            return
        self._validation_ok = True
        self.collected = self.form.collect()

    def exec(self):
        self._validation_ok = True
        self.collected = None
        return super().exec()


class QuickPhraseDialog(QDialog):
    """常用句框：独立顶层非模态窗口，点击条目即复制到剪贴板

    - 带系统标题栏，可在屏幕上自由移动，不遮挡、不阻塞售后面板交互
    - 单击 / 双击条目：将该句复制到系统剪贴板（不绑定任何表单字段）
    - 底部输入框 + 「添加」：新增常用句并持久化到 settings.json（去重、置顶）
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("常用句")
        # 非模态：不进入 exec 事件循环，售后面板仍可继续操作
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setMinimumWidth(460)
        # 独立顶层窗口：套用全局 QSS，保持深/浅主题一致
        try:
            from core.theme_qss import apply_window_qss
            apply_window_qss(self)
        except Exception:
            pass

        self.phrases = aftersale_db.load_quick_phrases()

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        tip = CaptionLabel(
            "点击任意常用句即可复制到剪贴板（窗口可拖动，不影响售后面板）", self)
        tip.setWordWrap(True)
        root.addWidget(tip)

        self._list = QListWidget(self)
        self._list.setFixedHeight(220)
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._list.itemClicked.connect(self._on_copy)
        self._list.itemDoubleClicked.connect(self._on_copy)
        self._reload_list()
        root.addWidget(self._list, 1)

        # 手动添加行：输入框 + 添加按钮（回车 / 点按钮均触发）
        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self._input = LineEdit(self)
        self._input.setPlaceholderText("输入新的常用句，回车或点「添加」…")
        self._input.returnPressed.connect(self._on_add)
        add_row.addWidget(self._input, 1)
        self._btn_add = PushButton(FluentIcon.ADD, "添加", self)
        self._btn_add.clicked.connect(self._on_add)
        add_row.addWidget(self._btn_add)
        root.addLayout(add_row)

        # 底部：复制选中（不关闭，可连续复制）+ 关闭
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch(1)
        self._btn_copy = PushButton(FluentIcon.COPY, "复制选中", self)
        self._btn_copy.clicked.connect(self._on_copy_selected)
        btn_row.addWidget(self._btn_copy)
        self._btn_close = PushButton("关闭", self)
        self._btn_close.clicked.connect(self.close)
        btn_row.addWidget(self._btn_close)
        root.addLayout(btn_row)

    def _reload_list(self):
        self._list.clear()
        for p in self.phrases:
            item = QListWidgetItem(p)
            item.setToolTip(p)
            self._list.addItem(item)

    def _copy_text(self, text: str):
        t = str(text or "").strip()
        if not t:
            return
        QApplication.clipboard().setText(t)
        show_info_bar("已复制：" + t, "success", title="常用句",
                      parent=self, duration=1500)

    def _on_copy(self, item):
        self._copy_text(item.text())

    def _on_copy_selected(self):
        cur = self._list.currentItem()
        if cur is not None:
            self._copy_text(cur.text())

    def _on_add(self):
        t = self._input.text().strip()
        if not t:
            return
        self.phrases = aftersale_db.add_quick_phrase(t)
        self._reload_list()
        self._input.clear()
        self._input.setFocus()


class ImportPreviewDialog(QDialog):
    """导入预览：字段要求提示 + 解析效果预览，确认后执行导入"""

    def __init__(self, excel_headers, rows, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导入预览")
        self.resize(1180, 660)
        self.setMinimumSize(900, 480)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        # 字段要求提示
        req = ("类型", "球房", "桌号", "地区", "问题")
        opt = ("发生原因", "是否解决", "解决方案", "解决人", "响应时间")
        missing = [h for h in (*req, *opt) if h not in excel_headers]
        tip = ("表格需要以下列（表头需与列名完全一致）\n"
               f"必填：{'、'.join(req)}\n"
               f"可选：{'、'.join(opt)}")
        if missing:
            tip += f"\n\n⚠ 当前表格缺失列：{'、'.join(missing)}（缺失的可选列将留空，是否解决默认「否」）"
        tip += ("\n自动补充：填写时间=导入时间、填写人=Excel导入、"
                "发生时间=导入当日、周期=按当前周期设置归属")
        lbl = CaptionLabel(tip, self)
        lbl.setWordWrap(True)
        root.addWidget(lbl)

        # 解析效果预览表（前 20 行）
        table = TableWidget(self)
        table.setColumnCount(len(_PREVIEW_COLUMNS))
        table.setHorizontalHeaderLabels([c[1] for c in _PREVIEW_COLUMNS])
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(_FIXED_ROW_HEIGHT)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setWordWrap(False)
        for i, w in enumerate(_PREVIEW_WIDTHS):
            table.setColumnWidth(i, w)
        shown = rows[:20]
        table.setRowCount(len(shown))
        for r, rec in enumerate(shown):
            for c, (key, _h) in enumerate(_PREVIEW_COLUMNS):
                val = str(rec.get(key) or "")
                if key == "cycle_start" and val:
                    val = aftersale_db.cycle_label(val)
                item = QTableWidgetItem(val)
                item.setToolTip(val)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(r, c, item)
        root.addWidget(table, 1)
        self._table = table

        # 底部：统计 + 取消/确认
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        more = "" if len(rows) <= 20 else f"（预览前 20 行）"
        self._lbl_count = CaptionLabel(
            f"共解析 {len(rows)} 条可导入记录{more}", self)
        bottom.addWidget(self._lbl_count)
        bottom.addStretch(1)
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(self.reject)
        bottom.addWidget(btn_cancel)
        btn_ok = PrimaryPushButton(FluentIcon.ACCEPT, "确认导入", self)
        btn_ok.setToolTip("按预览效果批量写入数据库")
        btn_ok.clicked.connect(self.accept)
        bottom.addWidget(btn_ok)
        root.addLayout(bottom)
