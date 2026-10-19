# -*- coding: utf-8 -*-
"""售后记录表单：字段布局、取值与必填校验。

表单不负责落库，录入页与编辑弹窗共用同一套控件（AftersaleForm），
改字段口径时两条路径会同时生效，不要只验证其中一条。"""

import os
from datetime import datetime, timedelta

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QLabel, QListWidget,
    QListWidgetItem, QFileDialog, QApplication,
    QDialog, QPushButton as _QPushButton, QGridLayout)
from PySide6.QtCore import Qt, QTimer, QThread, QPointF, QDate, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QFont
from qfluentwidgets import (TableWidget, SearchLineEdit, PushButton,
    PrimaryPushButton, ToolButton, FluentIcon, RoundMenu, Action,
    LineEdit, PlainTextEdit, BodyLabel, CaptionLabel, TitleLabel,
    ScrollArea, CardWidget, MessageBox, MessageBoxBase, CheckBox,
    FluentWindow, NavigationItemPosition, MenuAnimationType,
    setCustomStyleSheet, qconfig, isDarkTheme, ZhDatePicker, RadioButton,
    SpinBox, SegmentedWidget, ProgressBar, FlowLayout,
    EditableComboBox)

from core.design_tokens import SEMANTIC, lighten, darken
from core.flow_widgets import FlowToolbarScrollArea
from core.perf import is_acrylic_enabled
from core.theme_qss import current_accent_hex
from core.utils import show_info_bar
from database import aftersale_db, table_db
from workers.aftersale_worker import AftersaleDBWorker
from windows.mysql_sync_card import MysqlSyncCard

from windows.aftersale.common import *  # noqa: F401,F403

# ==================== 共享表单（录入页与编辑弹窗复用） ====================

class AftersaleForm(QWidget):
    """售后记录表单：字段与 售后问题汇总8月.xlsx 对齐 + 系统附加字段

    桌号输入防抖搜索球桌管理库，候选列表点选后自动带出球房/SNK；
    精确匹配单台球桌时静默带出，无需点选。
    """

    def __init__(self, parent=None):
        """构造表单并挂上球房搜索的防抖计时器。

        构造期会读「上次填写人/解决人」等偏好，面板常驻时改设置不会即时
        生效，需要重开面板；搜索计时器同理只创建一次。
        """
        super().__init__(parent)
        self._snk_code = ""      # 关联球桌带出（隐藏字段，随记录落库）
        self._search_kw = ""
        self._last_city = ""     # 上次由球桌带出的城市（球房变动时联动清空）
        self._cand_worker = None
        self._init_ui()

        # 球房搜索防抖：停止输入 300ms 后才查库，避免逐字触发查询
        self._search_timer = QTimer(self)
        self._search_timer.setInterval(300)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._do_room_search)

    def _init_ui(self):
        """按「基本信息/位置关联/问题描述」三块线性搭界面。

        各字段形态差异大（日期步进、搜索带出、分段开关），抽通用构建器
        反而更难读；要调布局按板块定位即可。
        """
        root = QVBoxLayout(self)
        # 外边距交给宿主：录入页在滚动区、编辑弹窗在对话框，两处留白不同，
        # 表单再加一层会双重缩进
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # ---------- 板块一：基本信息（标签上置，控件固定宽度，右侧留白） ----------
        sec1 = _SectionCard("基本信息", self)
        row1 = QHBoxLayout()
        row1.setSpacing(16)
        # 每个字段列固定「标签-控件-内联错误」三段；错误行常驻占位，
        # 只切显隐，避免报错瞬间整行高度跳动
        col_type = QVBoxLayout()
        col_type.setSpacing(3)
        col_type.addWidget(_field_label("问题类型", True, self))
        # 与「问题描述-问题」同规格：EditableComboBox（可选预置也可手输，
        # 需求13 替换 FluentCombo 原生 QComboBox 为 qfluentwidgets 组件）
        self.type_combo = EditableComboBox(self)
        self.type_combo.addItems(aftersale_db.ISSUE_TYPES)
        # 与「问题」下拉同为 320：两列字段的左右边缘才对得齐
        self.type_combo.setFixedWidth(320)
        # 与「问题」控件交互一致：挂手输补全，支持预置可选 + 可手输
        self._set_completer(self.type_combo, list(aftersale_db.ISSUE_TYPES))
        col_type.addWidget(self.type_combo)
        # 内联错误只控制显隐与文案，不参与取值；validate 是唯一写入方
        self._err_type = _inline_error("必填项未填写", self)
        col_type.addWidget(self._err_type)
        row1.addLayout(col_type)
        col_occ = QVBoxLayout()
        col_occ.setSpacing(3)
        col_occ.addWidget(_field_label("发生时间", True, self))
        occurred_row = QHBoxLayout()
        occurred_row.setSpacing(2)
        self.occurred_picker = ZhDatePicker(self)
        # 高度写死 33 与步进按钮对齐：ZhDatePicker 默认比 ToolButton 高 1px，
        # 不显式指定时整行看起来是歪的
        self.occurred_picker.setFixedWidth(150)
        self.occurred_picker.setFixedHeight(33)
        # 默认日期走 _default_occurred_date()：开「记住上次发生日期」时
        # 沿用上一条新增的日期（连续补录同一天免重复拨日期），否则当日
        self.occurred_picker.setDate(self._default_occurred_date())
        occurred_row.addWidget(self.occurred_picker)
        # 日期步进按钮（实心三角成组）：连续点击逐日前移/后移，
        # 补录历史发生日期（如 8/25 录 8/20 的售后）连续点 ◀ 即可回退
        self._btn_occurred_prev = ToolButton(
            FluentIcon.CARE_LEFT_SOLID, self)
        self._btn_occurred_prev.setFixedSize(28, 33)
        self._btn_occurred_prev.setToolTip("前一天")
        self._btn_occurred_prev.clicked.connect(
            lambda _=False: self._step_occurred(-1))
        occurred_row.addWidget(self._btn_occurred_prev)
        self._btn_occurred_next = ToolButton(
            FluentIcon.CARE_RIGHT_SOLID, self)
        self._btn_occurred_next.setFixedSize(28, 33)
        self._btn_occurred_next.setToolTip("后一天")
        self._btn_occurred_next.clicked.connect(
            lambda _=False: self._step_occurred(1))
        occurred_row.addWidget(self._btn_occurred_next)
        col_occ.addLayout(occurred_row)
        row1.addLayout(col_occ)
        col_creator = QVBoxLayout()
        col_creator.setSpacing(3)
        col_creator.addWidget(_field_label("填写人", False, self))
        # 默认值三级回退：上次填写 → 配置默认 → 空；读取发生在构造期
        # 填写人：可编辑下拉（默认取配置，可手输；候选由 load_candidates 从历史填充）
        self.creator_edit = EditableComboBox(self)
        # 默认优先取「上次填写」，无则取配置，可改
        self.creator_edit.setText(aftersale_db.load_last_creator() or _default_creator())
        self.creator_edit.setFixedWidth(160)
        self._set_completer(self.creator_edit, [])  # 候选动态填充，初始空
        col_creator.addWidget(self.creator_edit)
        row1.addLayout(col_creator)
        # 末尾 stretch：窗口变宽时字段留在左侧，不被均分拉长
        row1.addStretch(1)
        sec1.content_layout.addLayout(row1)
        root.addWidget(sec1)

        # ---------- 板块二：位置关联（控件固定宽度，带出芯片 + 关联确认条） ----------
        sec2 = _SectionCard("位置关联", self)
        row2 = QHBoxLayout()
        row2.setSpacing(16)
        room_col = QVBoxLayout()
        room_col.setSpacing(3)
        room_col.addWidget(_field_label("球房", True, self))
        # 用 SearchLineEdit 而非 LineEdit：自带清除按钮，清空即收起候选
        self.room_edit = SearchLineEdit(self)
        self.room_edit.setPlaceholderText("输入球房名搜索球桌")
        self.room_edit.setFixedWidth(320)
        self.room_edit.textChanged.connect(self._on_room_text_changed)
        self.room_edit.clearSignal.connect(self._hide_candidates)
        room_col.addWidget(self.room_edit)
        self._err_room = _inline_error("必填项未填写", self)
        room_col.addWidget(self._err_room)
        row2.addLayout(room_col)
        tbl_col = QVBoxLayout()
        tbl_col.setSpacing(3)
        tbl_col.addWidget(_field_label("桌号", False, self))
        # 桌号允许手填但不参与必填：历史工单里有没桌号的记录，
        # 强制必填会让老数据无法补录
        self.table_no_edit = LineEdit(self)
        self.table_no_edit.setPlaceholderText("选桌自动带出")
        self.table_no_edit.setFixedWidth(140)
        tbl_col.addWidget(self.table_no_edit)
        self._err_table = _inline_error("必填项未填写", self)
        tbl_col.addWidget(self._err_table)
        row2.addLayout(tbl_col)
        reg_col = QVBoxLayout()
        reg_col.setSpacing(3)
        reg_col.addWidget(_field_label("地区", True, self))
        self.region_combo = EditableComboBox(self)  # 需求13：QFluentWidgets 组件
        self.region_combo.addItems(aftersale_db.REGIONS_PRESET)
        self.region_combo.setFixedWidth(160)
        reg_col.addWidget(self.region_combo)
        self._err_region = _inline_error("必填项未填写", self)
        reg_col.addWidget(self._err_region)
        row2.addLayout(reg_col)
        row2.addStretch(1)
        sec2.content_layout.addLayout(row2)
        # 球桌候选列表（默认隐藏，搜索命中后展示）
        # 候选用原生 QListWidget：单列点选即可，TableWidget 还要定义表头
        # 与列宽，反而多写一堆无用配置
        self._cand_list = QListWidget(self)
        self._cand_list.setFixedHeight(132)
        self._cand_list.setVisible(False)
        self._cand_list.itemClicked.connect(self._on_candidate_clicked)
        _style_cand_list(self._cand_list)
        # 主题切换后重刷候选样式；极早期启动路径下 qconfig 可能未就绪，
        # 失败只是颜色不跟随，不应阻断表单构建
        try:
            qconfig.themeChanged.connect(
                lambda: _style_cand_list(self._cand_list))
        except Exception:
            pass
        sec2.content_layout.addWidget(self._cand_list)
        # 关联确认条：选桌后展示桌号 + SNK，隐藏字段 snk_code 的可视反馈
        # 样式用 objectName 限定：同进程还有多个 QLabel，不限定选择器
        # 会把绿色底刷到别的控件上
        self._link_bar = QLabel(self)
        self._link_bar.setObjectName("aftersaleLinkBar")
        self._link_bar.setWordWrap(True)
        self._link_bar.setStyleSheet(
            "QLabel#aftersaleLinkBar { background: %s; color: %s;"
            " border-radius: 6px; padding: 5px 10px; font-size: 12px; }"
            % (_hex_rgba(SEMANTIC["success"], 26), SEMANTIC["success"]))
        self._link_bar.setVisible(False)
        sec2.content_layout.addWidget(self._link_bar)
        root.addWidget(sec2)

        # ---------- 板块三：问题描述 ----------
        sec3 = _SectionCard("问题描述", self)
        v3 = sec3.content_layout
        v3.addWidget(_field_label("问题", True, self))
        # 问题行：下拉 + 「常用句」按钮（点击弹窗内条目复制到剪贴板，不自动填入）
        prob_row = QHBoxLayout()
        prob_row.setSpacing(8)
        self.problem_combo = EditableComboBox(self)  # 需求13：QFluentWidgets 组件
        # 锁 320：再宽会把右侧「常用句」按钮挤出可视区
        self.problem_combo.setFixedWidth(320)
        prob_row.addWidget(self.problem_combo)
        # 常用句只复制不写入：直接填入会覆盖用户敲了一半的描述
        self._btn_phrases = PushButton(FluentIcon.COPY, "常用句", self)
        self._btn_phrases.setToolTip("常用句（点击条目复制到剪贴板，可添加新句子）")
        self._btn_phrases.clicked.connect(self._open_quick_phrases)
        prob_row.addWidget(self._btn_phrases)
        prob_row.addStretch(1)
        v3.addLayout(prob_row)
        self._err_problem = _inline_error("必填项未填写", self)
        v3.addWidget(self._err_problem)
        # 发生原因 / 解决方案：等宽等高多行文本框并排（64px ≈ 3 行）
        txt_row = QHBoxLayout()
        txt_row.setSpacing(14)
        col_cause = QVBoxLayout()
        col_cause.setSpacing(5)
        col_cause.addWidget(_field_label("发生原因", False, self))
        # 64px 约三行，与右侧解决方案等高：并排两块文本高度不等会显得错位
        self.cause_edit = PlainTextEdit(self)
        self.cause_edit.setFixedHeight(64)
        self.cause_edit.setPlaceholderText("选填")
        col_cause.addWidget(self.cause_edit)
        txt_row.addLayout(col_cause, 1)
        col_sol = QVBoxLayout()
        col_sol.setSpacing(5)
        col_sol.addWidget(_field_label("解决方案", False, self))
        # 与发生原因同高、1:1 拉伸；超长内容各自滚动，不撑高整块卡片
        self.solution_edit = PlainTextEdit(self)
        self.solution_edit.setFixedHeight(64)
        self.solution_edit.setPlaceholderText("选填，多行文本")
        col_sol.addWidget(self.solution_edit)
        txt_row.addLayout(col_sol, 1)
        v3.addLayout(txt_row)
        # 三个是/否判定：分段开关（默认值与旧版一致：是 / 否 / 是）
        seg_row = QHBoxLayout()
        seg_row.setSpacing(18)
        # 用分段开关而非复选框：复选框的「未勾选」既可能是「否」也可能是
        # 「没填」，分段开关没有这种歧义
        seg_row.addWidget(_field_label("是否解决", True, self))
        self.resolved_combo = YesNoSegment("是", self)
        seg_row.addWidget(self.resolved_combo)
        seg_row.addWidget(_field_label("我们主动发起", False, self))
        self.is_initiative_combo = YesNoSegment("否", self)
        seg_row.addWidget(self.is_initiative_combo)
        seg_row.addWidget(_field_label("是我们的问题", False, self))
        self.is_our_problem_combo = YesNoSegment("是", self)
        seg_row.addWidget(self.is_our_problem_combo)
        # 重要标记：勾选后「记录与统计」列表中该条显示淡黄色底色，代表比较重要
        # 重要标记是唯一真正的布尔字段，其余三个判定都是二选一
        self.is_important_check = CheckBox("重要", self)
        self.is_important_check.setToolTip("标记为重要售后（记录列表中该条显示淡黄色底色）")
        seg_row.addWidget(self.is_important_check)
        seg_row.addStretch(1)
        v3.addLayout(seg_row)
        # 解决人 / 响应时间：左右平分两列
        # 解决人与响应时间并排：都是选填且同为 220 宽，对称排布更省纵向空间
        res_row = QHBoxLayout()
        res_row.setSpacing(16)
        col_res = QVBoxLayout()
        col_res.setSpacing(5)
        col_res.addWidget(_field_label("解决人", False, self))
        self.resolver_combo = EditableComboBox(self)  # 需求13：QFluentWidgets 组件
        # 默认取「上次填写」，重新打开面板时解决人保持不变
        if aftersale_db.load_last_resolver():
            self.resolver_combo.setText(aftersale_db.load_last_resolver())
        self.resolver_combo.setFixedWidth(220)
        col_res.addWidget(self.resolver_combo)
        res_row.addLayout(col_res)
        col_resp = QVBoxLayout()
        col_resp.setSpacing(5)
        col_resp.addWidget(_field_label("响应时间", False, self))
        self.response_combo = EditableComboBox(self)  # 需求13：QFluentWidgets 组件
        self.response_combo.addItems(aftersale_db.RESPONSE_TIME_PRESET)
        self.response_combo.setFixedWidth(220)
        col_resp.addWidget(self.response_combo)
        res_row.addLayout(col_resp)
        res_row.addStretch(1)
        v3.addLayout(res_row)
        root.addWidget(sec3)

        # 首个错误控件由 validate 写入，调用方据此滚动定位；其余路径不碰
        self.first_error = None

    # ---------- 候选值加载 ----------

    def load_candidates(self, cands: dict):
        """用历史候选刷新问题/解决人/地区/填写人四个下拉。

        保留用户已输入文本：候选来自数据库查询，异步返回时用户可能已经
        开始打字，直接 clear 会把输入吞掉。EditableComboBox 是 LineEdit
        子类，改完候选必须重建补全器，否则手输提示还是旧列表。
        """
        cur_problem = self.problem_combo.currentText().strip()
        problems = list(cands.get("problems", []))
        self.problem_combo.clear()
        self.problem_combo.addItems(problems)
        if cur_problem:
            self.problem_combo.setText(cur_problem)
        self._set_completer(self.problem_combo, problems)
        cur_resolver = self.resolver_combo.currentText().strip()
        resolvers = list(cands.get("resolvers", []))
        self.resolver_combo.clear()
        self.resolver_combo.addItems(resolvers)
        if cur_resolver:
            self.resolver_combo.setText(cur_resolver)
        self._set_completer(self.resolver_combo, resolvers)
        # 地区：预置 + 历史新增合并
        cur_region = self.region_combo.currentText().strip()
        regions = list(aftersale_db.REGIONS_PRESET)
        for r in cands.get("regions", []):
            if r not in regions:
                regions.append(r)
        self.region_combo.clear()
        self.region_combo.addItems(regions)
        if cur_region:
            self.region_combo.setText(cur_region)
        self._set_completer(self.region_combo, regions)
        # 填写人：历史使用频次候选（可手输新增）
        cur_creator = self.creator_edit.currentText().strip()
        creators = list(cands.get("creators", []))
        self.creator_edit.clear()
        self.creator_edit.addItems(creators)
        if cur_creator:
            self.creator_edit.setText(cur_creator)
        self._set_completer(self.creator_edit, creators)

    @staticmethod
    def _set_completer(combo, items: list):
        """给 EditableComboBox 挂手输补全器（LineEdit 子类无内置补全）。

        使用包含匹配（MatchContains + 忽略大小写）：输入任意子串也能力命中
        候选，例如候选「识别反应慢」输入「反应慢」（子串在中间）同样带出，
        而非仅前缀命中（输入「识别」才行）。
        """
        try:
            from PySide6.QtWidgets import QCompleter
            # 补全器挂到 combo 名下：控件销毁时 Qt 连带回收，否则每次
            # load_candidates 都漏一个 QCompleter
            c = QCompleter(items, combo)
            c.setFilterMode(Qt.MatchFlag.MatchContains)
            c.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            c.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            combo.setCompleter(c)
            # 限制下拉高度不超过宿主面板：候选随使用量增长（100+ 条）时，
            # 避免弹出列表撑破当前面板；窗口未 show 时取合理兜底值
            try:
                popup = c.popup()
                avail = int(getattr(combo.window(), "height", lambda: 0)()) or 0
                # 夹在 220~440 之间；窗口尚未 show 时 height() 为 0，
                # 用 320 兜底，保证候选列表不会顶出屏幕
                max_h = (
                    max(220, min(440, int(avail * 0.8)))
                    if avail > 0 else 320)
                popup.setMaximumHeight(max_h)
            except Exception:
                pass
        except Exception:
            pass

    # ---------- 常用句 ----------

    def _open_quick_phrases(self):
        """打开常用句浮窗，点条目复制到剪贴板而不是填进表单。

        用非模态窗口：录售后时常要边看常用句边改描述，模态会锁住整个
        面板。模块级 import 会与 dialogs.py 形成循环依赖，故延迟导入。
        """
        # 延迟导入避免 dialogs.py → form.py 的循环依赖
        from windows.aftersale.dialogs import QuickPhraseDialog
        # 关闭上一实例（若仍打开），再新建非模态窗口；保存引用防止被垃圾回收
        # 保留引用：非模态窗口只存在局部变量里会被立刻回收，表现为闪一下
        old = getattr(self, "_quick_phrase_dlg", None)
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
        dlg = QuickPhraseDialog(self)
        self._quick_phrase_dlg = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    # ---------- 球房搜索与带出 ----------

    def _on_room_text_changed(self, text):
        """球房文本变动时作废旧关联，并重启 300ms 防抖计时。

        逐字查库会让输入卡顿（球桌表按球房名模糊匹配），所以只在停顿后
        发起一次异步查询；作废旧值是为了避免把上一家球房的关联写错。
        """
        self._search_kw = str(text or "").strip()
        # 球房一变，桌号与 SNK 立即作废：留着会把上一家的关联写到新球房
        self._snk_code = ""  # 文本变动后旧的关联失效
        self.table_no_edit.clear()  # 旧桌号失效，防止错带到新球房
        self._set_linked(False)  # 旧关联确认条/带出芯片失效
        # 地区：仅当当前文本是上次带出的城市时联动清空（手填/改过的地区保留）
        if (self._last_city and
                self.region_combo.currentText().strip() == self._last_city):
            self.region_combo.setText("")
            self._last_city = ""
        if not self._search_kw:
            self._hide_candidates()
            return
        self._search_timer.start()

    def _do_room_search(self):
        """防抖到期后发起一次异步球桌查询。

        查询跑在 QThread 里，回调自带关键词快照；主线程不等结果，避免
        数据库慢查询冻结界面。
        """
        kw = self._search_kw
        if not kw:
            return
        # 上限 30 条：同一球房正常不会更多，候选太长反而难选；worker 存
        # 实例属性，线程被提前回收时回调会静默丢失
        self._cand_worker = AftersaleDBWorker(
            table_db.query_tables_by_room, kw, 30)
        self._cand_worker.result_ready.connect(
            lambda result, k=kw: self._on_room_candidates(k, result))
        self._cand_worker.error.connect(lambda _m: self._hide_candidates())
        self._cand_worker.start()

    def _on_room_candidates(self, kw, result):
        """渲染球房候选；只有一个命中时直接带出，不弹列表。

        回调带回发起搜索时的关键词快照，与当前输入不一致就整批丢弃，
        否则连续输入时先返回的慢查询会覆盖后一次结果。
        """
        if kw != self._search_kw:
            return  # 输入已变化，丢弃过期结果
        rows = result or []
        # 只命中唯一球桌：静默带出，不弹候选
        if len(rows) == 1:
            self._apply_table(rows[0])
            self._hide_candidates()
            return
        self._cand_list.clear()
        if not rows:
            # 无结果提示（不可选中，不干扰后续手填）
            item = QListWidgetItem(f"未找到「{kw}」的球桌，可直接填写")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._cand_list.addItem(item)
            self._cand_list.setVisible(True)
            return
        # 行号即候选下标：点击时按行号回查原始行，不必把展示文案解析回字段
        self._cand_rows = rows
        for r in rows:
            name = str(r.get("name") or "")
            room = str(r.get("roomName") or "")
            item = QListWidgetItem(f"{name} · {room}")
            item.setToolTip(f"桌号: {name}\n球房: {room}\nSNK: {r.get('snk_code') or ''}")
            self._cand_list.addItem(item)
        self._cand_list.setVisible(True)

    def _on_candidate_clicked(self, item):
        """点选候选：按行号取回原始行带出字段，并收起列表。"""
        row_idx = self._cand_list.row(item)
        rows = getattr(self, "_cand_rows", [])
        if 0 <= row_idx < len(rows):
            self._apply_table(rows[row_idx])
        self._hide_candidates()

    def _apply_table(self, row):
        """把一行球桌数据回填到桌号/球房/地区，并记住隐藏的 SNK。

        球房写入时阻断信号，否则 textChanged 会反过来清掉刚带出的字段；
        城市为空说明老库没采集，此时只清「上一桌带出的」地区，人工填的保留。
        """
        self.table_no_edit.setText(str(row.get("name") or ""))
        room = str(row.get("roomName") or "")
        # 必须阻断信号：setText 会走 _on_room_text_changed，把刚带出的桌号
        # 和 SNK 当成用户改动清掉
        self.room_edit.blockSignals(True)
        self.room_edit.setText(room)
        self.room_edit.blockSignals(False)
        self._snk_code = str(row.get("snk_code") or "")
        self._search_kw = room.strip()
        city = str(row.get("city") or "").strip()
        if city:
            self.region_combo.setText(city)
            self._last_city = city
        elif self.region_combo.currentText().strip() == self._last_city:
            # 新球桌城市未知（老库未采集）：清掉上一桌带出的残留城市，
            # 避免换球房后地区仍显示旧城市；手填的地区不受影响
            self.region_combo.setText("")
            self._last_city = ""
        self._set_linked(True, row)

    def _set_linked(self, on, row=None):
        """显示/隐藏关联确认条，让隐藏字段 snk_code 可见。

        用户看不到 SNK 就无从判断关联对不对，所以带出球桌后必须给出
        可视反馈；取消关联时同样要收起，避免旧提示残留。
        """
        if not on or not row:
            self._link_bar.setVisible(False)
            return
        # 城市两种来源：新库叫 city，老库只有 region，按序兜底
        city = str(row.get("city") or row.get("region") or "").strip()
        parts = ["已关联球桌：%s · %s 号桌" % (
            str(row.get("roomName") or ""), str(row.get("name") or ""))]
        if str(row.get("snk_code") or ""):
            parts.append("SNK: %s" % str(row.get("snk_code")))
        if city:
            parts.append("城市自动带出")
        self._link_bar.setText("✓　" + "　|　".join(parts))
        self._link_bar.setVisible(True)

    def _hide_candidates(self):
        """收起候选列表；清空球房、选中候选、查询失败三条路径共用。"""
        self._cand_list.setVisible(False)

    def _step_occurred(self, delta_days: int):
        """把发生时间按天前后挪动，负数为前移。

        补录历史工单时常见「今天录前几天的售后」，连点步进比反复拨日历
        快；取值统一走 picker.date，不需要额外维护内部状态。
        """
        self.occurred_picker.setDate(
            self.occurred_picker.date.addDays(delta_days))

    @staticmethod
    def _default_occurred_date() -> QDate:
        """新增/清空表单时「发生时间」的默认值。

        开「记住上次发生日期」（统一设置-面板设置-售后，默认开）且已记住
        日期时用记住值；否则回落到当日。记住值非法（格式损坏）同样回落当日。
        """
        last = QDate.fromString(aftersale_db.load_last_occurred(),
                                "yyyy-MM-dd")
        return last if last.isValid() else QDate.currentDate()

    # ---------- 值读写 ----------

    def set_values(self, rec: dict):
        """编辑模式回填：把一条已有记录写进各控件。

        逐个控件 setText/setValue 而不走统一映射：日期、分段开关、多行
        文本的写入口径各不相同，再包一层只会增加理解成本。
        """
        self.type_combo.setText(str(rec.get("issue_type") or ""))  # setText 回显
        occurred = str(rec.get("occurred_at") or "").strip()
        occ_d = QDate.fromString(occurred, "yyyy-MM-dd")
        if occ_d.isValid():
            self.occurred_picker.setDate(occ_d)
        self.table_no_edit.setText(str(rec.get("table_no") or ""))
        room = str(rec.get("room_name") or "")
        self.room_edit.blockSignals(True)  # 回填不触发搜索/联动清空
        self.room_edit.setText(room)
        self.room_edit.blockSignals(False)
        self._search_kw = room.strip()
        self.region_combo.setText(str(rec.get("region") or ""))
        self.problem_combo.setText(str(rec.get("problem") or ""))
        self.cause_edit.setPlainText(str(rec.get("cause") or ""))
        self.resolved_combo.setValue(rec.get("resolved") or "是")
        self.is_initiative_combo.setValue(rec.get("is_initiative") or "否")
        self.is_our_problem_combo.setValue(rec.get("is_our_problem") or "是")
        self.is_important_check.setChecked(bool(rec.get("is_important")))
        self.solution_edit.setPlainText(str(rec.get("solution") or ""))
        self.resolver_combo.setText(str(rec.get("resolver") or ""))
        self.response_combo.setText(str(rec.get("response_time") or ""))
        self.creator_edit.setText(str(rec.get("creator") or ""))
        self._snk_code = str(rec.get("snk_code") or "")
        # 清空 _last_city：否则改球房时会误判「当前地区是自动带出的」，
        # 把原记录里人工填写的地区抹掉
        self._last_city = ""  # 编辑回填不参与城市联动
        self.clear_error()

    def collect(self) -> dict:
        """把表单读成一条记录 dict，不做必填校验。

        顺带记住本次填写人/解决人，下次打开面板默认带出。校验与取值分开，
        是为了编辑场景能只取不校验。
        """
        aftersale_db.save_last_people(
            self.creator_edit.text().strip(),
            self.resolver_combo.currentText().strip())
        return {
            "issue_type": self.type_combo.currentText().strip(),
            "occurred_at": self.occurred_picker.date.toString("yyyy-MM-dd"),
            "table_no": self.table_no_edit.text().strip(),
            "room_name": self.room_edit.text().strip(),
            "region": self.region_combo.currentText().strip(),
            "problem": self.problem_combo.currentText().strip(),
            "cause": self.cause_edit.toPlainText().strip(),
            "resolved": self.resolved_combo.value(),
            "is_initiative": self.is_initiative_combo.value(),
            "is_our_problem": self.is_our_problem_combo.value(),
            "is_important": 1 if self.is_important_check.isChecked() else 0,
            "solution": self.solution_edit.toPlainText().strip(),
            "resolver": self.resolver_combo.currentText().strip(),
            "response_time": self.response_combo.currentText().strip(),
            "creator": self.creator_edit.text().strip(),
            "snk_code": self._snk_code,
        }

    def _required_map(self):
        """必填字段 → (取值, 错误控件, 输入控件)，顺序即校验/聚焦顺序

        桌号为选填（2026-08 需求：允许不填写桌号），不参与必填进度与校验。
        """
        # 顺序即校验顺序：validate 按此顺序报缺项，first_error 取第一个
        return [
            ("类型", not self.type_combo.currentText().strip(),
             self._err_type, self.type_combo),
            ("球房", not self.room_edit.text().strip(),
             self._err_room, self.room_edit),
            ("地区", not self.region_combo.currentText().strip(),
             self._err_region, self.region_combo),
            ("问题", not self.problem_combo.currentText().strip(),
             self._err_problem, self.problem_combo),
        ]

    def validate(self) -> list:
        """必填校验：返回缺失字段中文名列表，并驱动字段级红框/内联提示

        同时记录 first_error（首个错误输入控件），供调用方滚动聚焦。
        """
        missing = []
        self.first_error = None
        for name, bad, err_lbl, ctl in self._required_map():
            err_lbl.setVisible(bad)
            if hasattr(ctl, "setError"):
                ctl.setError(bad)
            if bad:
                missing.append(name)
                if self.first_error is None:
                    self.first_error = ctl
        return missing

    def clear_error(self):
        """清除全部字段级错误态（输入恢复后由调用方触发）"""
        for _name, _bad, err_lbl, ctl in self._required_map():
            err_lbl.setVisible(False)
            if hasattr(ctl, "setError"):
                ctl.setError(False)
        self.first_error = None

    def clear_form(self):
        """清空表单，保留填写人与解决人的「上次填写」值。

        连续补录同一批工单时这两个人通常不变，重置掉反而要重敲；其余
        字段回到默认，发生时间由 _default_occurred_date 决定。
        """
        self.room_edit.clear()
        self.table_no_edit.clear()
        self.cause_edit.clear()
        self.solution_edit.clear()
        self.problem_combo.setText("")
        # 解决人/填写人保持「上次填写」：清空后与重新打开面板均恢复
        self.resolver_combo.setText(aftersale_db.load_last_resolver())
        self.creator_edit.setText(aftersale_db.load_last_creator() or _default_creator())
        self.response_combo.setText("")
        self.resolved_combo.setValue("是")
        self.is_initiative_combo.setValue("否")
        self.is_our_problem_combo.setValue("是")
        self.is_important_check.setChecked(False)
        # 类型回到未选中：clear() 会连候选项一起删掉，之后就没得选了
        self.type_combo.setCurrentIndex(-1)
        self.occurred_picker.setDate(self._default_occurred_date())  # 记忆或当日
        self.region_combo.setText("")
        self._snk_code = ""
        self._last_city = ""
        self._set_linked(False)
        self.clear_error()
        self._hide_candidates()
