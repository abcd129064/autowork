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

    def __init__(self, record: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑售后记录")
        self._record = record
        self.form = AftersaleForm(self)
        self.form.set_values(record)
        self.viewLayout.addWidget(self.form)
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
