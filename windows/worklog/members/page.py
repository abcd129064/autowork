# -*- coding: utf-8 -*-
"""页签5 成员与分类（稿 597-635）：左右等宽两卡，成员表 + 分类标签流

稿 620/632 的两条实现注记是本页的红线：
* 停用 ≠ 删除 —— 成员与分类都只做 active 翻转，历史记录的归属原样保留；
* 有记录引用的分类禁删，界面上干脆不给「删除」入口，只给停用/启用。

分类的增改改的是 config JSON（core.app_settings 的 misc 域），不走数据库：
分类只有名字+启用位两个字段，且内置 7 项要能在没建库的环境里也显示。
成员/分类改动后发 ``changed`` 信号，由 Hub 广播给其余页签重查候选。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QHeaderView, QAbstractItemView, QLabel,
                               QTableWidgetItem, QDialog)

from qfluentwidgets import (TableWidget, LineEdit, PushButton,
                            PrimaryPushButton, FlowLayout, RoundMenu, Action)

from core.design_tokens import SPACE, TYPE_SCALE_PX, FONT_WEIGHTS
from core.ops_link_delegate import LINKS_ROLE
from core.spin_fit_patch import FittedSpinBox
from core.utils import show_info_bar
from database import worklog_db
from windows.worklog.common import (CardBox, TagPill, WorklogPage, px_font,
                                    tag_colors, category_tag, member_color,
                                    text_color_tier)
from windows.worklog.controller import WorklogController
from windows.worklog.delegate import (ROLE_AVATAR, ROLE_TAG,
                                      install_worklog_delegate)

_COLUMNS = (("成员", 130), ("别名", 0), ("参与统计", 90), ("排序", 60),
            ("操作", 96))
_ROW_H = 38


class _NameDialog(QDialog):
    """添加 / 重命名分类的单词输入框（稿 624 按钮的落点）"""

    def __init__(self, title, initial="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedWidth(360)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACE[5], SPACE[4], SPACE[5], SPACE[4])
        lay.setSpacing(SPACE[3])
        self.edt = LineEdit(self)
        self.edt.setPlaceholderText("分类名，如「调研」")
        self.edt.setText(initial)
        lay.addWidget(self.edt)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(SPACE[2])
        foot.addStretch(1)
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(lambda _=False: self.reject())
        foot.addWidget(btn_cancel)
        btn_ok = PrimaryPushButton("保存", self)
        btn_ok.clicked.connect(lambda _=False: self.accept())
        foot.addWidget(btn_ok)
        lay.addLayout(foot)
        self.edt.selectAll()

    def text(self) -> str:
        return self.edt.text().strip()


class _MemberDialog(QDialog):
    """成员新增 / 编辑（稿 602 按钮的落点）：名字手输，别名与排序号可选"""

    def __init__(self, member=None, parent=None):
        super().__init__(parent)
        self._member = member or {}
        self.setWindowTitle("编辑成员" if member else "添加成员")
        self.setModal(True)
        self.setFixedWidth(400)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACE[5], SPACE[4], SPACE[5], SPACE[4])
        lay.setSpacing(SPACE[3])

        self.edt_name = self._row(lay, "成员名 *")
        self.edt_name.setPlaceholderText("如「张工」，录入下拉靠它记忆写法")
        self.edt_alias = self._row(lay, "别名")
        self.edt_alias.setPlaceholderText("可选，表格与时间轴优先显示别名")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE[2])
        lbl = QLabel("排序", self)
        lbl.setFont(px_font(TYPE_SCALE_PX["base"]))
        lbl.setFixedWidth(76)
        row.addWidget(lbl)
        self.spin_sort = FittedSpinBox(self, sample="999")
        self.spin_sort.setRange(0, 999)
        row.addWidget(self.spin_sort)
        row.addStretch(1)
        lay.addLayout(row)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(SPACE[2])
        foot.addStretch(1)
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(lambda _=False: self.reject())
        foot.addWidget(btn_cancel)
        btn_ok = PrimaryPushButton("保存", self)
        btn_ok.clicked.connect(lambda _=False: self._try_accept())
        foot.addWidget(btn_ok)
        lay.addLayout(foot)

        if member:
            self.edt_name.setText(member.get("name") or "")
            self.edt_alias.setText(member.get("alias") or "")
            self.spin_sort.setValue(int(member.get("sort") or 0))

    def _row(self, lay, label_text):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE[2])
        lbl = QLabel(label_text, self)
        lbl.setFont(px_font(TYPE_SCALE_PX["base"]))
        lbl.setFixedWidth(76)                     # 稿 219 的 76px 标签列
        row.addWidget(lbl)
        edt = LineEdit(self)
        row.addWidget(edt, 1)
        lay.addLayout(row)
        return edt

    def _try_accept(self):
        if not self.edt_name.text().strip():
            show_info_bar("成员名不能为空", "warning", parent=self,
                          duration=2500)
            self.edt_name.setFocus()
            return
        self.accept()

    def values(self) -> dict:
        return {
            "name": self.edt_name.text().strip(),
            "alias": self.edt_alias.text().strip(),
            "sort": self.spin_sort.value(),
        }


class MembersPage(WorklogPage):
    """成员与分类维护页：所有写操作走 controller.mutate 的后台线程"""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ctl = WorklogController(self)
        self._members = []
        self._categories = []
        self._loaded_once = False

        wrap = QWidget(self)
        grid = QGridLayout(wrap)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(SPACE[3])                   # 稿 234 gap:14
        grid.addWidget(self._build_members_card(wrap), 0, 0)
        grid.addWidget(self._build_categories_card(wrap), 0, 1)
        self.lay.addWidget(wrap, 1)

        self._ctl.options_ready.connect(self._on_options)
        self._ctl.options_failed.connect(
            lambda msg: show_info_bar(msg, "error", title="加载失败",
                                      parent=self, duration=4000))
        self._ctl.mutation_done.connect(self._on_mutation_done)
        self._ctl.mutation_failed.connect(
            lambda tag, msg: show_info_bar(msg, "error", title="操作失败",
                                           parent=self, duration=4000))

    # ---------------- 布局 ----------------

    def _card_head(self, card, title, btn_text, on_click):
        """稿 98-100 的 .card-h：15px/600 标题 + 右端 sm 主色按钮"""
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(SPACE[2])
        lbl = QLabel(title, card)
        lbl.setFont(px_font(TYPE_SCALE_PX["lg"], FONT_WEIGHTS["semibold"]))
        head.addWidget(lbl)
        head.addStretch(1)
        btn = PrimaryPushButton(btn_text, card)
        btn.setFixedHeight(28)                      # 稿 141 .btn.sm
        btn.setFont(px_font(TYPE_SCALE_PX["sm"]))
        fm = btn.fontMetrics()
        btn.setFixedWidth(fm.horizontalAdvance(btn_text) + SPACE[2] + 14)
        btn.clicked.connect(lambda _=False: on_click())
        head.addWidget(btn)
        card.lay.addLayout(head)
        return head

    def _build_members_card(self, parent):
        card = CardBox(parent=parent)
        self._card_members = card
        self._card_head(card, "成员管理", "＋ 添加成员", self._on_add_member)

        self._table = TableWidget(card)
        self._table.setColumnCount(len(_COLUMNS))
        self._table.setHorizontalHeaderLabels([c[0] for c in _COLUMNS])
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setWordWrap(False)
        self._table.setShowGrid(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for i, (_name, w) in enumerate(_COLUMNS):
            if w:
                self._table.setColumnWidth(i, w)
            else:
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        install_worklog_delegate(self._table, self._on_ops, None)
        card.lay.addWidget(self._table, 1)
        return card

    def _build_categories_card(self, parent):
        card = CardBox(parent=parent)
        self._card_categories = card
        self._card_head(card, "分类管理", "＋ 添加分类", self._on_add_category)
        self._cat_host = self._new_cat_host(card)
        card.lay.addWidget(self._cat_host, 1)
        return card

    def _new_cat_host(self, parent):
        """标签流宿主：整块重建而不是运行时增删 FlowLayout 的项

        qfw FlowLayout 的 takeAt/replaceWidget 会把它内部索引与实际位置
        弄错位（主窗口工具栏重叠过），整块换宿主 Widget 就没有这个问题。
        """
        host = QWidget(parent)
        flow = FlowLayout(host, needAni=False)
        flow.setContentsMargins(0, 0, 0, 0)
        flow.setHorizontalSpacing(SPACE[2])   # 稿 625 gap:8
        flow.setVerticalSpacing(SPACE[2])     # 稿 625 line-height:2.4
        return host

    # ---------------- 取数 ----------------

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded_once:
            self._loaded_once = True
            self._ctl.fetch_options()

    def detach_workers(self):
        self._ctl.stop_all()

    def _on_options(self, data):
        self._members = data.get("members") or []
        self._categories = data.get("categories") or []
        self._fill_members()
        self._fill_categories()

    def _fill_members(self):
        table = self._table
        table.setRowCount(0)
        for m in self._members:
            row = table.rowCount()
            table.insertRow(row)
            table.setRowHeight(row, _ROW_H)
            name = m.get("name") or ""
            values = (name, m.get("alias") or "", "",
                      str(int(m.get("sort") or 0)), "")
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled
                              | Qt.ItemFlag.ItemIsSelectable)
                table.setItem(row, col, item)
            table.item(row, 0).setData(
                ROLE_AVATAR, (name[:1] or "？", member_color(m.get("id")),
                              name))
            active = bool(m.get("active"))
            table.item(row, 2).setData(
                ROLE_TAG, ("是" if active else "否",)
                + tag_colors("success" if active else "neutral"))
            table.item(row, 4).setData(
                LINKS_ROLE,
                [("编辑", "primary", "edit"),
                 (("停用", "danger", "off") if active
                  else ("启用", "primary", "on"))])

    def _fill_categories(self):
        host = self._new_cat_host(self._card_categories)
        flow = host.layout()
        for cat in self._categories:
            name = cat.get("name") or ""
            active = bool(cat.get("active"))
            text = name if active else "%s · 已停用" % name
            pill = TagPill(text, host)
            fg, bg = (category_tag(name) if active
                      else tag_colors("neutral"))
            pill.set_tag(text, fg, bg)
            pill.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            pill.customContextMenuRequested.connect(
                lambda _pos, c=cat: self._on_pill_menu(c))
            flow.addWidget(pill)
        old, self._cat_host = self._cat_host, host
        self._card_categories.lay.replaceWidget(old, host)
        old.deleteLater()

    # ---------------- 交互 ----------------

    def _member_at(self, row):
        """行序与 self._members 一致（_fill_members 按序灌入，中间不插行）"""
        if 0 <= row < len(self._members):
            return self._members[row]
        return None

    def _on_ops(self, row, _col, action):
        m = self._member_at(row)
        if m is None:
            return
        if action == "edit":
            self._on_edit_member(m)
        elif action in ("off", "on"):
            self._ctl.mutate("member_active", worklog_db.set_member_active,
                             m.get("id"), action == "on")

    def _on_add_member(self):
        dlg = _MemberDialog(parent=self.window())
        if not dlg.exec():
            return
        v = dlg.values()
        self._ctl.mutate("member_add", worklog_db.insert_member,
                         v["name"], v["alias"], v["sort"])

    def _on_edit_member(self, m):
        dlg = _MemberDialog(member=m, parent=self.window())
        if not dlg.exec():
            return
        self._ctl.mutate("member_edit", worklog_db.update_member,
                         m.get("id"), dlg.values())

    def _on_add_category(self):
        dlg = _NameDialog("添加分类", parent=self.window())
        if not dlg.exec() or not dlg.text():
            return
        self._ctl.mutate("cat_add", worklog_db.add_category, dlg.text())

    def _on_pill_menu(self, cat):
        name = cat.get("name") or ""
        menu = RoundMenu(parent=self)
        if not cat.get("builtin"):
            act_rename = Action("重命名", self)
            act_rename.triggered.connect(lambda _=False:
                                           self._on_rename_category(name))
            menu.addAction(act_rename)
        active = bool(cat.get("active"))
        act_toggle = Action("启用" if active else "停用", self)
        act_toggle.triggered.connect(
            lambda _=False: self._ctl.mutate(
                "cat_active", worklog_db.set_category_active, name,
                not active))
        menu.addAction(act_toggle)
        menu.exec(QCursor.pos())

    def _on_rename_category(self, old):
        dlg = _NameDialog("重命名分类", initial=old, parent=self.window())
        if not dlg.exec() or not dlg.text() or dlg.text() == old:
            return
        self._ctl.mutate("cat_rename", worklog_db.rename_category, old,
                         dlg.text())

    def _on_mutation_done(self, tag, payload):
        texts = {
            "member_add": ("成员已添加", "成员名为空，未保存"),
            "member_edit": ("成员已保存", "保存失败，成员可能已不存在"),
            "member_active": ("成员状态已更新", "操作失败"),
            "cat_add": ("分类已添加", "分类已存在，未重复添加"),
            "cat_rename": ("分类已重命名", "重命名失败：内置分类或名字已存在"),
            "cat_active": ("分类状态已更新", "操作失败"),
        }
        ok, bad = texts.get(tag, ("已完成", "操作失败"))
        show_info_bar(ok if payload else bad,
                      "success" if payload else "warning",
                      parent=self, duration=2000)
        # 成员/分类是其余页签的下拉候选与配色来源：自己重拉一遍，
        # 再广播让 Hub 通知其它页签（本页不接 on_options_changed，不会回环）
        self._ctl.fetch_options()
        self.changed.emit()


__all__ = ["MembersPage"]
