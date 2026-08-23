# -*- coding: utf-8 -*-
"""entry 模块（从 windows/aftersale_panel.py 拆出，逻辑未改动）"""

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

# ==================== 板块一：填写录入页 ====================

class EntryPage(QWidget):
    """填写录入页：页头（周期指示）+ 三段表单 + 随内容操作条"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._save_worker = None
        self._cand_worker = None
        self._init_ui()

    def _init_ui(self):
        # 外边距与记录页一致（左右 20），标题不贴边
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 12)
        root.setSpacing(10)

        # 页头：标题 + 说明左对齐，周期指示芯片右对齐
        head = QHBoxLayout()
        head.setSpacing(12)
        head_l = QVBoxLayout()
        head_l.setSpacing(2)
        head_l.addWidget(TitleLabel("填写录入", self))
        head_l.addWidget(CaptionLabel(
            "提交后写入数据库，多人协作刷新可见", self))
        head.addLayout(head_l)
        head.addStretch(1)
        self._cycle_chip = CaptionLabel(self)
        self._cycle_chip.setStyleSheet(
            "background: %s; color: %s; border-radius: 10px;"
            " padding: 3px 10px;" % (
                _hex_rgba(SEMANTIC["info"], 18), SEMANTIC["info"]))
        head.addWidget(self._cycle_chip)
        root.addLayout(head)

        # 表单滚动区：内容按自身高度排布，不拉伸铺满整页；
        # 操作条随内容放在最后一栏控件下方（与修改前一致）
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }")
        view = QWidget()
        view.setStyleSheet("QWidget { background: transparent; }")
        scroll.setWidget(view)
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(12)
        self.form = AftersaleForm(view)
        layout.addWidget(self.form)

        # 操作条：必填进度 + 清空/提交（表单末栏下方，右对齐）
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self._prog = ProgressBar(view)
        self._prog.setRange(0, 5)
        self._prog.setValue(0)
        self._prog.setTextVisible(False)
        self._prog.setFixedWidth(72)
        bar.addWidget(self._prog)
        self._prog_text = CaptionLabel("必填项 0/5 已填写", view)
        bar.addWidget(self._prog_text)
        bar.addStretch(1)
        self._btn_clear = PushButton(FluentIcon.DELETE, "清空", view)
        self._btn_clear.setToolTip("清空表单全部内容")
        self._btn_clear.setFixedHeight(34)
        self._btn_clear.clicked.connect(self._on_clear)
        bar.addWidget(self._btn_clear)
        self._btn_submit = PrimaryPushButton(FluentIcon.ACCEPT, "提交记录", view)
        self._btn_submit.setToolTip("校验必填项后写入数据库")
        self._btn_submit.setFixedHeight(34)
        self._btn_submit.clicked.connect(self._on_submit)
        bar.addWidget(self._btn_submit)
        layout.addLayout(bar)
        # 剩余高度由 stretch 吸收，表单保持固定排版不随窗口拉伸
        layout.addStretch(1)
        root.addWidget(scroll, 1)
        self._scroll = scroll

        # 必填进度联动：任一必填控件变化即重算
        # （需求13：region/problem 已为 EditableComboBox（LineEdit 子类），
        # 手输触发 textChanged；currentIndexChanged 保留）
        f = self.form
        for sig in (f.type_combo.currentTextChanged,
                    f.table_no_edit.textChanged,
                    f.room_edit.textChanged,
                    f.region_combo.textChanged,
                    f.region_combo.currentIndexChanged,
                    f.problem_combo.textChanged,
                    f.problem_combo.currentIndexChanged):
            try:
                sig.connect(self._update_required_progress)
            except Exception:
                pass

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_candidates()
        self._refresh_cycle_chip()

    def _refresh_cycle_chip(self):
        """页头周期指示：本周期 mm/dd – mm/dd（周期设置变化后 showEvent 重算）"""
        try:
            start = datetime.strptime(
                aftersale_db.current_cycle_start(), "%Y/%m/%d")
            end = start + timedelta(
                days=aftersale_db.cycle_span_days() - 1)
            self._cycle_chip.setText("本周期 %s – %s · 按发生时间自动归属" % (
                start.strftime("%m/%d"), end.strftime("%m/%d")))
        except Exception:
            self._cycle_chip.setText("按发生时间自动归属")

    def _update_required_progress(self, *_a):
        """必填进度：n/5 实时刷新；填齐变绿，缺项琥珀色点名"""
        bad = [name for name, is_bad, _l, _c in self.form._required_map()
               if is_bad]
        done = 5 - len(bad)
        self._prog.setValue(done)
        if not bad:
            self._prog_text.setText("必填项 5/5 已填齐")
            self._prog_text.setStyleSheet("color: %s;" % SEMANTIC["success"])
            self._prog.setStyleSheet(
                "QProgressBar { background: %s; border: none;"
                " border-radius: 2px; } QProgressBar::chunk {"
                " background: %s; border-radius: 2px; }" % (
                    _hex_rgba(SEMANTIC["success"], 18),
                    SEMANTIC["success"]))
        else:
            self._prog_text.setText("还差 %d 项：%s" % (
                len(bad), "、".join(bad)))
            self._prog_text.setStyleSheet("color: %s;" % SEMANTIC["warning"])
            self._prog.setStyleSheet(
                "QProgressBar { background: %s; border: none;"
                " border-radius: 2px; } QProgressBar::chunk {"
                " background: %s; border-radius: 2px; }" % (
                    _hex_rgba(SEMANTIC["warning"], 18),
                    SEMANTIC["warning"]))

    def _on_clear(self):
        self.form.clear_form()
        self._update_required_progress()

    def _refresh_candidates(self):
        """进入页面刷新动态候选（问题/解决人/地区）"""
        self._cand_worker = AftersaleDBWorker(aftersale_db.get_field_candidates)
        self._cand_worker.result_ready.connect(self.form.load_candidates)
        self._cand_worker.error.connect(lambda _m: None)
        self._cand_worker.start()

    def _on_submit(self):
        """提交：必填校验（失败则字段级红框+滚动聚焦）→ 后台写库 → 清表单"""
        missing = self.form.validate()
        if missing:
            show_info_bar(f"请先填写必填项: {'、'.join(missing)}", "warning",
                          title="无法提交", parent=self, duration=3000)
            if self.form.first_error is not None:
                self.form.first_error.setFocus()
                self._scroll.ensureWidgetVisible(
                    self.form.first_error, 0, 90)
            return
        if self._save_worker and self._save_worker.isRunning():
            return
        self._btn_submit.setEnabled(False)
        record = self.form.collect()
        self._save_worker = AftersaleDBWorker(aftersale_db.insert_record, record)
        self._save_worker.result_ready.connect(self._on_saved)
        self._save_worker.error.connect(self._on_save_error)
        self._save_worker.start()

    def _on_saved(self, rec_id):
        self._btn_submit.setEnabled(True)
        self.form.clear_form()
        self._update_required_progress()
        show_info_bar(f"售后记录已提交（编号 {rec_id}）", "success",
                      title="提交成功", parent=self, duration=2500)
        # 通知窗口刷新记录页（若已构建）
        win = self.window()
        page = getattr(win, "records_page", None)
        if page is not None:
            page.refresh_async()

    def _on_save_error(self, msg):
        self._btn_submit.setEnabled(True)
        show_info_bar(msg, "error", title="提交失败", parent=self, duration=4000)
