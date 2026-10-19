# -*- coding: utf-8 -*-
"""跑视频表单（录入页与编辑弹窗复用）

字段与在线模板.xlsx 数据 sheet 对齐：分类（sheet 名）→ 类别 → 球房 →
视频名 → 帧数 → 日期 → 描述 → 复现（是/否，需求6）→ 新程序 → 备注 → 署名。
分类切换时类别候选联动（模板解析预置 + 库中历史自由输入）。
「日期」为视频日期（需求7：看昨天的视频可指定昨天，默认当天）。

本类只做录入与回显，不持有数据库连接：校验、收集、预填、回显四段职责分开，
写库由调用方（新增页与编辑弹窗）负责，同一套控件代码才能被两处复用。
"""
from PySide6.QtCore import Qt, QTimer, QDate
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                               QLabel)

from qfluentwidgets import (LineEdit, PlainTextEdit, ScrollArea, CardWidget,
                            PrimaryPushButton, PushButton, BodyLabel,
                            CaptionLabel, FluentIcon, TableWidget,
                            ProgressBar, ZhDatePicker, ComboBox,
                            EditableComboBox, CalendarPicker, ToolButton)

from core.design_tokens import SEMANTIC
from core.utils import show_info_bar
from database import ledger_db
from workers.aftersale_worker import AftersaleDBWorker

# 星号导入带来 _SectionCard / _field_label / _inline_error 等本包私有辅助，
# 表单页与弹窗共用同一套排版件，因此这里不重复定义
from windows.run_video.common import *  # noqa: F401,F403

# 新程序取值（模板「新程序」列宽仅 8.5，是/否 标记）；
# 空串占首位，编辑回显遇到「未填写」的历史记录时不至于被强行落成「否」
NEW_PROGRAM_VALUES = ("", "是", "否")


class LedgerForm(QWidget):
    """跑视频表单：分类/类别/球房必填，其余选填；支持会话上下文预填

    校验与收集拆成两个方法，是因为录入页还要在用户输入过程中静默算「必填完成度」，
    那段逻辑不需要标红，也不需要滚动定位。
    """

    def __init__(self, parent=None):
        """只建控件，构造期不查库；类别候选在本方法末尾按当前分类异步拉取。"""
        super().__init__(parent)
        # 必须挂在实例上：候选查询是 QThread，局部变量出作用域即被回收，
        # 线程还在跑时销毁会导致信号回调永远不触发，下拉一直是空的
        self._cand_worker = None
        self._init_ui()

    def _init_ui(self):
        """按「基础信息 / 描述与备注」两段卡片排版，字段宽度写死。

        宽度对齐在线模板的列宽而不是随窗口拉伸：短字段被拉成一条长线后，
        录入时的视觉重心会散，也看不出哪些是必填。
        """
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # ---------- 板块一：分类与基础信息 ----------
        sec1 = _SectionCard("分类与基础信息", self)
        row1 = QHBoxLayout()
        row1.setSpacing(16)
        col_cat = QVBoxLayout()
        col_cat.setSpacing(3)
        col_cat.addWidget(_field_label("分类", True, self))
        # 分类候选取自 ledger_db.CATEGORIES（模板 sheet 名），故意不做可编辑：
        # 手输一个不在表里的分类，写库时找不到对应工作表，只能整条丢给后台人工处理
        self.category_combo = ComboBox(self)  # QFluentWidgets 原生（需求9）
        self.category_combo.addItems(ledger_db.CATEGORIES)
        self.category_combo.setFixedWidth(130)
        self.category_combo.currentTextChanged.connect(
            self._on_category_changed)
        col_cat.addWidget(self.category_combo)
        # 必填提示做成字段下方的常驻小字而非弹窗：三处必填同时缺失时，
        # 弹窗要连点三次，行内提示能一次看全
        self._err_category = _inline_error("必填项未填写", self)
        col_cat.addWidget(self._err_category)
        row1.addLayout(col_cat)
        col_kind = QVBoxLayout()
        col_kind.setSpacing(3)
        col_kind.addWidget(_field_label("类别", True, self))
        # 需求12：类别改用 qfluentwidgets EditableComboBox（可编辑 + 下拉候选，
        # 支持手输新类别，替代 FluentCombo 的原生 QComboBox 可编辑实现）
        self.kind_combo = EditableComboBox(self)
        self.kind_combo.setFixedWidth(320)  # 按最长历史类别名留余量，类别是自由文本，不能按枚举估宽
        col_kind.addWidget(self.kind_combo)
        self._err_kind = _inline_error("必填项未填写", self)
        col_kind.addWidget(self._err_kind)
        row1.addLayout(col_kind)
        row1.addStretch(1)  # 右侧留白：窗口拉宽时控件保持左对齐，不会被匀开
        sec1.content_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(16)
        col_room = QVBoxLayout()
        col_room.setSpacing(3)
        col_room.addWidget(_field_label("球房", True, self))
        self.room_edit = LineEdit(self)
        self.room_edit.setPlaceholderText("设备代码/球房名")
        self.room_edit.setFixedWidth(160)
        col_room.addWidget(self.room_edit)
        self._err_room = _inline_error("必填项未填写", self)
        col_room.addWidget(self._err_room)
        row2.addLayout(col_room)
        col_video = QVBoxLayout()
        col_video.setSpacing(3)
        col_video.addWidget(_field_label("视频名", False, self))
        self.video_edit = LineEdit(self)
        self.video_edit.setPlaceholderText("当前视频/日志文件名")
        self.video_edit.setFixedWidth(300)
        col_video.addWidget(self.video_edit)
        row2.addLayout(col_video)
        col_frame = QVBoxLayout()
        col_frame.setSpacing(3)
        col_frame.addWidget(_field_label("帧数", False, self))
        self.frame_edit = LineEdit(self)
        self.frame_edit.setPlaceholderText("400")  # 帧数只做文本透传，录入时转 int 会把非数字输入直接吞掉
        self.frame_edit.setFixedWidth(80)
        col_frame.addWidget(self.frame_edit)
        row2.addLayout(col_frame)
        # 日期：视频日期（需求7）——默认「记住上次日期」或当天；
        # 需求1：控件改为与主面板相同方式（CalendarPicker + ◀▶ 步进按钮）
        col_date = QVBoxLayout()
        col_date.setSpacing(3)
        col_date.addWidget(_field_label("日期", False, self))
        date_row = QHBoxLayout()
        date_row.setSpacing(2)
        self.date_edit = CalendarPicker(self)
        self.date_edit.setMinimumWidth(150)
        self.date_edit.setFixedHeight(33)
        self.date_edit.setDate(self._default_date())
        date_row.addWidget(self.date_edit)
        # 日期步进按钮（与主面板 date_prev/date_next 同款 26×32）：
        # 连续点击逐日前移/后移，补录历史视频日期免开日历面板
        self._btn_date_prev = ToolButton(FluentIcon.LEFT_ARROW, self)
        self._btn_date_prev.setFixedSize(26, 32)
        self._btn_date_prev.setToolTip("前一天")
        self._btn_date_prev.clicked.connect(
            lambda _=False: self._step_date(-1))
        date_row.addWidget(self._btn_date_prev)
        self._btn_date_next = ToolButton(FluentIcon.RIGHT_ARROW, self)
        self._btn_date_next.setFixedSize(26, 32)
        self._btn_date_next.setToolTip("后一天")
        self._btn_date_next.clicked.connect(
            lambda _=False: self._step_date(1))
        date_row.addWidget(self._btn_date_next)
        col_date.addLayout(date_row)
        row2.addLayout(col_date)
        row2.addStretch(1)
        sec1.content_layout.addLayout(row2)
        root.addWidget(sec1)

        # ---------- 板块二：描述与备注 ----------
        sec2 = _SectionCard("描述与备注", self)
        v2 = sec2.content_layout
        # 描述 / 备注：并排（70% / 30%，需求8；参照售后面板「发生原因/解决方案」
        # 等宽等高多行文本框并排范式，本处以 7:3 stretch 分配宽度）
        txt_desc = QHBoxLayout()
        txt_desc.setSpacing(14)
        col_desc = QVBoxLayout()
        col_desc.setSpacing(5)
        col_desc.addWidget(_field_label("描述", False, self))
        self.desc_edit = PlainTextEdit(self)
        self.desc_edit.setFixedHeight(64)
        self.desc_edit.setPlaceholderText("问题现象/使用场景描述")
        col_desc.addWidget(self.desc_edit)
        txt_desc.addLayout(col_desc, 7)
        col_remark = QVBoxLayout()
        col_remark.setSpacing(5)
        col_remark.addWidget(_field_label("备注", False, self))
        # 需求8：备注与描述并排等高（多行文本框，参照售后面板并排范式）
        self.remark_edit = PlainTextEdit(self)
        self.remark_edit.setFixedHeight(64)
        self.remark_edit.setPlaceholderText("备注")
        col_remark.addWidget(self.remark_edit)
        txt_desc.addLayout(col_remark, 3)
        v2.addLayout(txt_desc)
        self._repro_label = _field_label("复现", False, self)
        # 需求6：复现为「是/否」选择（售后面板同款 SegmentedWidget，默认「否」）；
        # 需求7：恒显（不再按分类隐藏，用户反馈录入界面需始终可见）
        self.repro_seg = YesNoSegment("否", self)
        self.repro_seg.setFixedWidth(110)
        self._new_program_label = _field_label("新程序", False, self)
        # 需求10：新程序与复现同款 SegmentedWidget（默认「否」）
        self.new_program_seg = YesNoSegment("否", self)
        self.new_program_seg.setFixedWidth(110)
        # 需求11：新程序与复现同列并排（两个是/否开关一行，紧凑展示）
        repro_row = QHBoxLayout()
        repro_row.setSpacing(10)
        repro_row.addWidget(self._repro_label)
        repro_row.addWidget(self.repro_seg)
        repro_row.addWidget(self._new_program_label)
        repro_row.addWidget(self.new_program_seg)
        repro_row.addStretch(1)
        v2.addLayout(repro_row)
        # 行：署名（新程序已上移与复现同列，需求11）
        txt_row = QHBoxLayout()
        txt_row.setSpacing(16)
        col_signer = QVBoxLayout()
        col_signer.setSpacing(3)
        col_signer.addWidget(_field_label("署名", False, self))
        self.signer_edit = LineEdit(self)
        self.signer_edit.setText(_default_creator())  # 默认取配置，可改
        self.signer_edit.setFixedWidth(120)
        col_signer.addWidget(self.signer_edit)
        txt_row.addLayout(col_signer)
        txt_row.addStretch(1)
        v2.addLayout(txt_row)
        root.addWidget(sec2)

        self.first_error = None  # 最近一次校验的首个错误控件（供滚动聚焦）
        # 末尾主动跑一次联动：初始化阶段的 currentTextChanged 未必发出，
        # 不补这一下，类别下拉会在首次打开时是空的
        self._on_category_changed(self.category_combo.currentText())

    # ---------- 分类联动 ----------

    def _on_category_changed(self, category: str):
        """分类切换：类别候选联动（复现选择恒显，见需求7）

        空分类直接返回：清空/初始化阶段会发一次空值信号，此时发起候选查询
        既没有意义，回调回来还会把下拉刷成空。
        """
        if not category:
            return
        self._load_kind_candidates(category)

    def _load_kind_candidates(self, category: str):
        """异步拉取类别候选（模板预置 + 历史自由输入），保留用户已输入文本

        候选要合并库里的历史自由输入，只能查库；同步查会在打开表单时卡住首屏，
        所以走 AftersaleDBWorker 的线程。
        """
        cur = self.kind_combo.currentText().strip()
        # 每次切换分类都新建 worker：旧 worker 的回调会被 _on_kind_candidates 的
        # 分类校验挡掉，复用同一个实例反而会把新旧结果搅在一起
        self._cand_worker = AftersaleDBWorker(
            ledger_db.get_kind_candidates, category)
        # lambda 用默认参数把 category / cur 提前绑死：回调触发时控件上的文本
        # 已被用户改过，闭包读实时值会填到错误的分类上
        self._cand_worker.result_ready.connect(
            lambda cands, k=category, c=cur: self._on_kind_candidates(
                k, cands, c))
        # 查询失败回退内置候选表：下拉空着会让用户误以为类别可以不填
        self._cand_worker.error.connect(
            lambda _m: self._on_kind_candidates(
                category, ledger_db.KIND_CANDIDATES.get(category, ()), cur))
        self._cand_worker.start()

    def _on_kind_candidates(self, category, cands, keep_text):
        """类别候选填充：过期分类结果丢弃；保留用户已输入文本（EditableComboBox
        用 setText 替代 QComboBox 的 setEditText，并重建补全器）"""
        # 结果回来时分类可能已被用户改走，这类过期回包必须丢弃，
        # 否则会把上一个分类的候选盖到当前下拉里
        if self.category_combo.currentText() != category:
            return
        self.kind_combo.clear()
        cands = list(cands or ())
        self.kind_combo.addItems(cands)
        # addItems 会把 currentText 重置成第一项，所以填充后必须把用户已输入的
        # 文本写回去，否则切一次分类就吃掉正在输入的内容
        if keep_text:
            self.kind_combo.setText(keep_text)
        # 手输补全提示（EditableComboBox 为 LineEdit 子类，需显式挂补全器）
        try:
            from PySide6.QtWidgets import QCompleter
            self.kind_combo.setCompleter(QCompleter(cands, self.kind_combo))
        except Exception:
            # 补全器只影响输入联想，缺了照样能提交；qfluentwidgets 版本差异会让
            # setCompleter 直接抛 TypeError，不能因为它把整块表单构造打断
            pass

    # ---------- 日期步进 / 默认值 ----------

    def _step_date(self, delta_days: int):
        """视频日期步进：负数前移、正数后移（需求1，与主面板同交互）

        只改控件上的日期，不触发校验或候选刷新：连续点箭头补录历史日期时，
        每一步都去查库没有意义。
        """
        self.date_edit.setDate(self.date_edit.date.addDays(delta_days))

    @staticmethod
    def _default_date() -> QDate:
        """新增/清空表单时「日期」的默认值（需求3：与售后连续录入同逻辑）。

        开「记住上次视频日期」（统一设置-面板设置-跑视频，默认开）且已记住
        日期时沿用上一条新增的日期（连续补录同一天免重复拨日期），否则回落当日；
        记住值非法（格式损坏）同样回落当日。
        """
        # QDate.fromString 遇到坏串只返回 invalid，不会抛异常；
        # try 兜的是 ledger_db.load_last_occurred 读配置失败（首次运行尚无配置）
        try:
            last = QDate.fromString(ledger_db.load_last_occurred(),
                                    "yyyy-MM-dd")
        except Exception:
            last = QDate()
        return last if last.isValid() else QDate.currentDate()

    # ---------- 校验与收集 ----------

    def validate(self, show_errors: bool = True) -> list:
        """必填校验：分类/类别/球房；返回缺失项名称列表

        show_errors=True（默认，提交校验）触发红框 + 设置 first_error 供滚动聚焦；
        show_errors=False（必填进度静默校验）仅返回 missing，不修改红框状态。

        返回值用中文名而不是字段 key，调用方可以直接拼「请填写：分类、球房」。
        """
        missing = []
        if show_errors:
            self._err_category.setVisible(False)
            self._err_kind.setVisible(False)
            self._err_room.setVisible(False)
            self.first_error = None
        # 三段几乎重复的判空是刻意保留的：三个控件类型各不相同
        # （ComboBox / EditableComboBox / LineEdit），抽成表反而更难读
        if not self.category_combo.currentText().strip():
            missing.append("分类")
            if show_errors:
                self._err_category.setVisible(True)
                if self.first_error is None:
                    self.first_error = self.category_combo
        if not self.kind_combo.currentText().strip():
            missing.append("类别")
            if show_errors:
                self._err_kind.setVisible(True)
                if self.first_error is None:
                    self.first_error = self.kind_combo
        if not self.room_edit.text().strip():
            missing.append("球房")
            if show_errors:
                self._err_room.setVisible(True)
                if self.first_error is None:
                    self.first_error = self.room_edit
        # first_error 只在首次命中时赋值，保证拿到的是版面上最靠上的缺失项，
        # 调用方滚动过去后用户能顺着往下改
        return missing

    def collect(self) -> dict:
        """收集表单为记录 dict（与 ledger_db.insert_record 入参一致）

        键名必须与库表列名逐字一致，否则写库时会静默丢字段；frame 保持字符串，
        后台导出阶段再解析，录入端不做数值校验。
        """
        frame = self.frame_edit.text().strip()
        return {
            "category": self.category_combo.currentText().strip(),
            "kind": self.kind_combo.currentText().strip(),
            "room_name": self.room_edit.text().strip(),
            "video_name": self.video_edit.text().strip(),
            "frame": frame,
            # 日期：视频日期（需求7），ZhDatePicker.date 为 QDate 属性
            "occurred_at": self.date_edit.date.toString("yyyy-MM-dd"),
            "description": self.desc_edit.toPlainText().strip(),
            "repro": self.repro_seg.value().strip(),
            "new_program": self.new_program_seg.value().strip(),
            "remark": self.remark_edit.toPlainText().strip(),
            "signer": self.signer_edit.text().strip(),
        }

    def clear_form(self):
        """清空表单（保留分类与署名默认值）

        分类与署名故意不清：连续录入通常发生在同一分类、同一署名下，
        每次重选反而增加误选风险。
        """
        self.kind_combo.setText("")  # EditableComboBox 用 setText 清空
        self.room_edit.clear()
        self.video_edit.clear()
        self.frame_edit.clear()
        self.date_edit.setDate(self._default_date())  # 记住的日期或当天
        self.desc_edit.clear()
        self.repro_seg.setValue("否")  # 复现默认「否」（seg 无 clear，回默认）
        self.new_program_seg.setValue("否")  # 新程序默认「否」（seg 无 clear，回默认）
        self.remark_edit.clear()

    # ---------- 预填 / 回显 ----------

    def prefill(self, ctx: dict):
        """按主界面会话上下文预填（只填空值，不覆盖已输入内容）

        主界面每次切页、切会话都会推一次上下文，直接覆盖会把用户正在敲的
        内容冲掉，因此逐字段判空。
        """
        if not self.room_edit.text().strip():
            self.room_edit.setText(str(ctx.get("room_name", "") or ""))
        if not self.video_edit.text().strip():
            self.video_edit.setText(str(ctx.get("video_name", "") or ""))
        if not self.frame_edit.text().strip():
            self.frame_edit.setText(str(ctx.get("frame", "") or ""))
        if not self.signer_edit.text().strip():
            self.signer_edit.setText(str(ctx.get("signer", "") or ""))
        # 只有确实存在于枚举里的分类才接收，避免把「全部」这类筛选值写进表单
        category = str(ctx.get("category", "") or "")
        if category in ledger_db.CATEGORIES:
            self.category_combo.setCurrentText(category)

    def set_values(self, record: dict):
        """编辑回显：按记录 dict 填充全部字段

        回显一律走 setText 而不是 addItems：记录里的类别可能已不在当前候选里，
        仍要原样显示，否则用户会以为编辑把类别改掉了。
        """
        category = str(record.get("category") or "问题")
        # 分类不在枚举里就保持当前值：历史记录可能来自旧模板，
        # 硬塞进下拉只会显示成空白
        if category in ledger_db.CATEGORIES:
            self.category_combo.setCurrentText(category)
        self.kind_combo.setText(str(record.get("kind") or ""))  # setText 回显
        self.room_edit.setText(str(record.get("room_name") or ""))
        self.video_edit.setText(str(record.get("video_name") or ""))
        self.frame_edit.setText(str(record.get("frame") or ""))
        # 日期回显：occurred_at（YYYY-MM-DD 或带时间），非法/空回退当天
        # 历史记录可能带时分秒，只取前 10 位日期部分再解析
        occ = str(record.get("occurred_at") or "")[:10]
        qd = QDate.fromString(occ, "yyyy-MM-dd")
        if qd.isValid():
            self.date_edit.setDate(qd)
        else:
            self.date_edit.setDate(QDate.currentDate())
        self.desc_edit.setPlainText(str(record.get("description") or ""))
        # 复现：历史数据为描述文本（旧版文本框遗留）时 setValue 回退「否」
        self.repro_seg.setValue(str(record.get("repro") or ""))
        # 新程序：seg 回显（setValue 兼容「是/否」，其余回退「否」）
        self.new_program_seg.setValue(str(record.get("new_program") or ""))
        self.remark_edit.setPlainText(str(record.get("remark") or ""))
        self.signer_edit.setText(str(record.get("signer") or ""))
