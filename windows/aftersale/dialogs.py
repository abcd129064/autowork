# -*- coding: utf-8 -*-
"""售后页的三个弹窗：编辑记录、常用句、导入预览。

本模块从 windows/aftersale_panel.py 拆出，逻辑保持不变；拆分让面板只关心
列表与筛选，弹窗各自独立、便于单独验证。共享常量与控件工厂仍由
windows.aftersale.common 提供，所以这里用 import * 引入而不是逐个列举。
"""

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
    """编辑售后记录弹窗：复用共享表单，确认后异步落库。

    continuous=True 是连续录入模式，行为与 Web 端对齐：保存成功后不关窗，
    只清空问题相关字段等待下一条，点「完成」才关闭；每条成功都通过 on_saved
    通知调用方刷新列表。发生日期由本弹窗记入配置，下一条表单可直接沿用，
    连续录入时不必反复选日期。编辑模式保持原语义：保存即关窗。
    """

    def __init__(self, record: dict, parent=None, title: str = "编辑售后记录",
                 continuous: bool = False, on_saved=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        # record 同时服务「编辑」与「新增预填」；带 id/created_at 的视为已存在记录
        self._record = record
        # 连续录入只适用于新增。即使调用方传了 continuous=True，编辑已有记录
        # 仍必须保存即关窗，否则用户会以为没保存成功而重复录入
        self._continuous = bool(continuous) and not (
            record.get("id") or record.get("created_at"))
        self._busy = False          # 异步落库在途，防重复提交
        self._validated_ok = False  # 最近一次 validate() 结果（供 _on_yes 判定）
        self._on_saved_cb = on_saved
        self._save_worker = None
        self.form = AftersaleForm(self)
        self.form.set_values(record)
        self.viewLayout.addWidget(self.form)
        # MessageBoxBase 无系统标题栏，顶部自建 TitleLabel 标明用途（编辑/新增）
        ttl = TitleLabel(title, self)
        self.viewLayout.insertWidget(0, ttl)
        self.yesButton.setText("保存并继续" if self._continuous else "保存")
        # 连续模式下「完成」是唯一的关窗出口，比「取消」更贴合语义
        self.cancelButton.setText("完成" if self._continuous else "取消")
        self.yesButton.clicked.connect(self._on_yes)
        # 弹窗宽度：表单控件较宽
        self.widget.setMinimumWidth(560)

    def _on_yes(self):
        """收集表单值；连续录入模式下改走异步落库。

        必填拦截放在 validate() 而不是这里：基类在 accept 之前调用 validate，
        未通过时弹窗不会关闭，用户输入也就不会丢。走到本槽时必填一定已填齐，
        因此这里只判 _validated_ok 就能区分「校验通过」与「基类直接放行」。

        连续录入模式让 validate() 恒返回 False，基类便永不 accept；校验通过后
        由本槽异步落库，成功后清空表单等待下一条。
        """
        if not self._validated_ok:
            return
        if self._continuous:
            self._submit_continue()
            return
        self.collected = self.form.collect()

    def _submit_continue(self):
        """连续录入：把当前表单交给后台线程落库。

        在途期间禁用主按钮并置 _busy，避免连点产生重复记录；lambda 用默认
        参数绑定本条记录的发生日期，回调晚到时不会取到下一条的值。
        """
        if self._busy:
            return
        self._busy = True
        self.yesButton.setEnabled(False)
        record = self.form.collect()
        self._save_worker = AftersaleDBWorker(aftersale_db.insert_record, record)
        self._save_worker.result_ready.connect(
            lambda rid, occ=record.get("occurred_at"): self._on_saved(rid, occ))
        self._save_worker.error.connect(self._on_save_error)
        self._save_worker.start()

    def _on_saved(self, rec_id, occurred_at=None):
        """单条落库成功：记住日期、清空表单、提示并通知调用方。

        先保存发生日期再清表单：clear_form 会把日期恢复成「上次填写」，
        这一步让连续录入默认沿用同一天，跨天时用户仍可手动改。
        弹窗保持打开，直到用户点「完成」。
        """
        self._busy = False
        self.yesButton.setEnabled(True)
        if occurred_at:
            aftersale_db.save_last_occurred(str(occurred_at))
        self.form.clear_form()
        show_info_bar(f"已新增售后记录（编号 {rec_id}），可继续录入", "success",
                      title="连续录入", parent=self, duration=2500)
        if self._on_saved_cb is not None:
            try:
                self._on_saved_cb()
            except Exception:
                pass

    def _on_save_error(self, msg):
        # 失败也必须复位 _busy 和按钮，否则用户会被永久锁在无法保存的状态
        self._busy = False
        self.yesButton.setEnabled(True)
        show_info_bar(msg, "error", title="保存失败", parent=self, duration=4000)

    def validate(self) -> bool:
        """必填校验；返回 False 时基类不会 accept，弹窗保持打开。

        校验曾经写在 _on_yes 里，但基类默认 validate() 恒 True，会先 accept
        关窗才轮到 _on_yes，必填校验因此形同虚设，用户输入也随关窗丢失。
        挪到这里后，校验失败只提示并聚焦，不改变窗口状态。

        连续录入模式即使校验通过也返回 False，让弹窗保持打开；落库改由
        _on_yes 异步执行。编辑模式保持原行为：通过即 accept 关窗。
        """
        missing = self.form.validate()
        if missing:
            self._validated_ok = False
            show_info_bar(f"请先填写必填项: {'、'.join(missing)}", "warning",
                          title="无法保存", parent=self, duration=3000)
            # 聚焦首个缺失字段，与录入页「滚动聚焦」体验一致
            first_error = getattr(self.form, "first_error", None)
            if first_error is not None:
                first_error.setFocus()
            return False
        self._validated_ok = True
        return not self._continuous

    def exec(self):
        """每次模态执行前重置结果，避免复用实例时读到上一次的 collected。"""
        self.collected = None
        self._validated_ok = False
        return super().exec()


class QuickPhraseDialog(QDialog):
    """常用句框：独立顶层非模态窗口，点击条目即复制到剪贴板。

    非模态是刻意选择：录入售后记录时经常要一边看历史一边粘常用句，模态框
    会挡住面板，反而多一步开关。窗口带系统标题栏，可拖到任意角落，不遮住
    正在编辑的表单。

    - 单击 / 双击条目：将该句复制到系统剪贴板，不绑定任何表单字段
    - 底部输入框 + 「添加」：新增常用句并持久化到 settings.json（去重、置顶）
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("常用句")
        # 非模态：不进入 exec 事件循环，售后面板仍可继续操作
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setMinimumWidth(460)
        # 独立顶层窗口：套用全局 QSS，保持深/浅主题一致
        # 主题套用失败不能影响弹窗可用性：退回系统默认样式时仍能复制常用句
        try:
            from core.theme_qss import apply_window_qss
            apply_window_qss(self)
        except Exception:
            pass

        # 常用句存 settings.json 而不是数据库：它是个人快捷语，体量小、
        # 无需参与业务查询与同步，放进库表反而增加迁移负担
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
        # tooltip 放全文：窗口较窄时长句会被截断，悬停仍能看到完整内容
        for p in self.phrases:
            item = QListWidgetItem(p)
            item.setToolTip(p)
            self._list.addItem(item)

    def _copy_text(self, text: str):
        # 空文本直接忽略：粘贴空串会悄悄清掉用户剪贴板里原有的内容
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
        # 去重与持久化都在 db 层完成；这里只刷新列表并把焦点留在输入框，
        # 方便连续添加多条
        t = self._input.text().strip()
        if not t:
            return
        self.phrases = aftersale_db.add_quick_phrase(t)
        self._reload_list()
        self._input.clear()
        self._input.setFocus()


class ImportPreviewDialog(QDialog):
    """导入预览：先讲清字段要求，再展示解析结果，确认后才真正写库。

    批量导入难回滚，所以把「会写成什么样」提前给用户看；表格只读、不可选中，
    避免用户误以为能在这里直接改数据。
    """

    def __init__(self, excel_headers, rows, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导入预览")
        self.resize(1180, 660)
        self.setMinimumSize(900, 480)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        # 字段要求提示。表头必须与列名完全一致：导入按中文表头映射字段，
        # 模糊匹配会让错列在不知不觉中写进库
        req = ("类型", "球房", "桌号", "地区", "问题")
        opt = ("发生原因", "是否解决", "解决方案", "解决人", "响应时间")
        missing = [h for h in (*req, *opt) if h not in excel_headers]
        tip = ("表格需要以下列（表头需与列名完全一致）\n"
               f"必填：{'、'.join(req)}\n"
               f"可选：{'、'.join(opt)}")
        # 缺列不阻断导入：可选列留空即可；必填列缺失会体现为大量空字段，
        # 用户可在确认前从预览里发现
        if missing:
            tip += f"\n\n⚠ 当前表格缺失列：{'、'.join(missing)}（缺失的可选列将留空，是否解决默认「否」）"
        tip += ("\n自动补充：填写时间=导入时间、填写人=Excel导入、"
                "发生时间=导入当日、周期=按当前周期设置归属")
        lbl = CaptionLabel(tip, self)
        lbl.setWordWrap(True)
        root.addWidget(lbl)

        # 解析效果预览表（前 20 行）。只渲染前 20 行：预览是给人看的，
        # 几千行全量渲染会让弹窗明显卡住
        table = TableWidget(self)
        table.setColumnCount(len(_PREVIEW_COLUMNS))
        table.setHorizontalHeaderLabels([c[1] for c in _PREVIEW_COLUMNS])
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(_FIXED_ROW_HEIGHT)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setWordWrap(False)
        # 列宽固定：预览表不参与布局协商，固定宽度才能保证表头与内容对齐
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
        # 明确写出「预览前 20 行」，否则用户会以为只解析出 20 条记录
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
