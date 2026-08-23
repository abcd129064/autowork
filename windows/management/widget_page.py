# -*- coding: utf-8 -*-
"""widget_page 模块：组件测试页（FluentIcon 图标库 + QFluentWidgets 控件墙）

TestPage 内含两个页签：
- 图标库（_IconGalleryTab）：FluentIcon 全量 175 个图标，搜索过滤，点击复制枚举名
功能- 控件测试（_WidgetGalleryTab）：QFluentWidgets 常用控件按类别分组展示，均可直接交互

布局说明：每个控件条目（_ControlItem）采用「控件在上、名称标签在下」的垂直布局，
宽度自适应内容，避免控件与标签横向挤压重叠；名称过长时中间省略 + tooltip 全名。
"""

import logging

from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QApplication, QTabWidget, QTableWidgetItem,
                               QTreeWidgetItem, QListWidgetItem, QDialog)

from qfluentwidgets import (FluentIcon, SearchLineEdit, ScrollArea, TitleLabel,
    SubtitleLabel, LargeTitleLabel, DisplayLabel, StrongBodyLabel, BodyLabel,
    CaptionLabel, CardWidget, SimpleCardWidget, ElevatedCardWidget,
    HeaderCardWidget, GroupHeaderCardWidget,
    ToolButton, PushButton, PrimaryPushButton, ToggleButton, TogglePushButton,
    RadioButton, CheckBox, SwitchButton, HyperlinkButton, SplitPushButton,
    TransparentDropDownPushButton, PillPushButton, LineEdit, PasswordLineEdit,
    PlainTextEdit, ComboBox, EditableComboBox, SpinBox, DoubleSpinBox,
    CompactSpinBox, CompactDoubleSpinBox, CalendarPicker, DateEdit, TimeEdit,
    DateTimeEdit, CompactDateEdit, CompactTimeEdit, CompactDateTimeEdit,
    DatePicker, TimePicker,
    InfoBadge, DotInfoBadge, AvatarWidget, IconWidget, ProgressBar,
    IndeterminateProgressBar, ProgressRing, IndeterminateProgressRing, Slider,
    ClickableSlider, SegmentedWidget, MessageBox, StateToolTip, ToolTip,
    PipsPager, Pivot, TabBar, TabWidget, ListWidget, ListView,
    TreeWidget, TableWidget, RoundMenu, Action,
    PrimaryToolButton, TransparentToolButton, ToggleToolButton,
    TransparentToggleToolButton, TransparentPushButton,
    TransparentTogglePushButton, DropDownPushButton,
    PrimaryDropDownPushButton, SplitToolButton, PrimarySplitPushButton,
    PillToolButton, CommandButton, BreadcrumbBar, NavigationToolButton,
    NavigationPushButton, NavigationSeparator, SmoothScrollArea,
    FluentWindow, SplitFluentWindow, SplashScreen, SettingCardGroup,
    SwitchSettingCard, ComboBoxSettingCard, OptionsSettingCard,
    RangeSettingCard, PushSettingCard, HyperlinkCard, ColorSettingCard,
    ConfigItem, OptionsConfigItem, RangeConfigItem, OptionsValidator,
    RangeValidator, BoolValidator, ColorConfigItem, qconfig)
from qfluentwidgets.components.layout.flow_layout import FlowLayout

from core.utils import show_info_bar

logger = logging.getLogger(__name__)

# 图标卡片尺寸：图标 40×40 + 名称一行（名称过长省略号）
_ICON_W, _ICON_H = 104, 88


def _elided(text: str, avail: int) -> str:
    """按可用宽度对文本做中间省略，避免标签撑破固定宽的控件条目"""
    fm = QFontMetrics(QApplication.font())
    return fm.elidedText(text, Qt.TextElideMode.ElideMiddle, avail)


class _IconCard(CardWidget):
    """单个图标卡片：图标 + 枚举名，点击复制 ``FluentIcon.XXX`` 到剪贴板"""

    def __init__(self, icon, name: str, parent=None):
        super().__init__(parent)
        self._name = name
        self.setFixedSize(_ICON_W, _ICON_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"FluentIcon.{name}（点击复制）")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 8, 6, 6)
        lay.setSpacing(2)
        self._icon_btn = ToolButton(icon, self)
        self._icon_btn.setFixedSize(40, 40)
        self._icon_btn.setIconSize(QSize(22, 22))
        self._icon_btn.setToolTip(self.toolTip())
        self._icon_btn.clicked.connect(self._copy)
        lay.addWidget(self._icon_btn, 0, Qt.AlignmentFlag.AlignHCenter)

        self._name_lbl = QLabel(_elided(name, _ICON_W - 16), self)
        self._name_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._name_lbl.setToolTip(name)
        lay.addWidget(self._name_lbl, 0, Qt.AlignmentFlag.AlignHCenter)

    def _copy(self):
        QApplication.clipboard().setText(f"FluentIcon.{self._name}")
        show_info_bar(f"FluentIcon.{self._name} 已复制", "success",
                      title="复制成功", parent=self.window(), duration=1500)

    def mouseReleaseEvent(self, event):
        """整卡点击复制（ToolButton 上的点击由按钮自己处理）"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._copy()
        super().mouseReleaseEvent(event)


class _ControlItem(QWidget):
    """控件测试条目：控件居中在上 + 名称标签居中在下（垂直布局，宽度自适应防重叠）"""

    def __init__(self, widget, name: str, parent=None, min_w=128):
        super().__init__(parent)
        # 宽度 = max(最小宽, 控件首选宽) + 边距，保证再宽的控件也不挤压标签
        hint = widget.sizeHint().width()
        w = max(min_w, hint if hint > 0 else min_w) + 24
        self.setFixedWidth(w)
        self.setObjectName("ctrlItem")

        box = QVBoxLayout(self)
        box.setContentsMargins(10, 8, 10, 8)
        box.setSpacing(6)
        # 控件水平居中
        center = QHBoxLayout()
        center.addStretch(1)
        center.addWidget(widget)
        center.addStretch(1)
        box.addLayout(center)
        # 名称标签：单行居中，超出省略，tooltip 全名
        self._label = CaptionLabel(_elided(name, w - 24), self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._label.setToolTip(name)
        box.addWidget(self._label)


class _IconGalleryTab(QWidget):
    """页签1：FluentIcon 全量图标墙（搜索过滤 + 点击复制枚举名）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cards = []  # [(卡片, 枚举名)]

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(24, 16, 24, 10)
        header.setSpacing(12)
        self._count_lbl = CaptionLabel("", self)
        header.addWidget(self._count_lbl)
        header.addStretch(1)
        self._search = SearchLineEdit(self)
        self._search.setPlaceholderText("按名称过滤图标（如 COPY / CHEVRON）")
        self._search.setFixedWidth(240)
        self._search.textChanged.connect(self._filter)
        header.addWidget(self._search)
        root.addLayout(header)

        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        container = QWidget()
        container.setStyleSheet("QWidget { background: transparent; }")
        scroll.setWidget(container)
        root.addWidget(scroll, 1)

        self._flow = FlowLayout(container, needAni=False, isTight=True)
        self._flow.setContentsMargins(24, 4, 24, 16)
        self._flow.setSpacing(8)
        for name, member in FluentIcon.__members__.items():
            card = _IconCard(member, name, container)
            self._flow.addWidget(card)
            self._cards.append((card, name))
        self._update_count(None)

    def _update_count(self, matched):
        total = len(self._cards)
        if matched is None:
            self._count_lbl.setText(f"共 {total} 个图标，点击卡片复制枚举名")
        else:
            self._count_lbl.setText(f"匹配 {matched} / {total} 个图标，点击卡片复制枚举名")

    def _filter(self, text: str):
        kw = text.strip().lower()
        matched = 0
        for card, name in self._cards:
            show = not kw or kw in name.lower()
            card.setVisible(show)
            matched += show
        self._flow.invalidate()
        self._update_count(matched)


# 顶层示例窗口的强引用列表，避免弹出后被垃圾回收
_DEMO_WINDOWS = []


def _safe_remove(lst, obj):
    try:
        if obj in lst:
            lst.remove(obj)
    except (RuntimeError, ValueError):
        pass


class _DemoFluentWindow(FluentWindow):
    """FluentWindow 示例：三个子接口页面的普通窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("FluentWindow 示例")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        for objname, icon, text in (("homePage", FluentIcon.HOME, "首页"),
                                    ("musicPage", FluentIcon.MUSIC, "音乐"),
                                    ("settingPage", FluentIcon.SETTING, "设置")):
            page = QWidget()
            page.setObjectName(objname)
            lay = QVBoxLayout(page)
            lay.addWidget(SubtitleLabel(f"这是 {text} 页面", page))
            self.addSubInterface(page, icon, text)


class _DemoSplitWindow(SplitFluentWindow):
    """SplitFluentWindow 示例：左右两个导航栏的分栏窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SplitFluentWindow 示例")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        left = QWidget()
        left.setObjectName("leftPage")
        self.addSubInterface(left, FluentIcon.HOME, "左侧")
        right = QWidget()
        right.setObjectName("rightPage")
        self.addSubInterface(right, FluentIcon.SETTING, "右侧")


class _SettingsDemoDialog(QDialog):
    """SettingCard 示例弹窗：一组设置卡片，演示常用设置项的交互"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SettingCard 示例")
        self.resize(520, 580)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.addWidget(TitleLabel("设置卡片示例", self))
        root.addWidget(CaptionLabel("以下卡片绑定临时配置项，可直接交互", self))

        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        root.addWidget(scroll)

        group = SettingCardGroup("通用", None)
        bool_item = ConfigItem("Demo", "AutoPlay", True, BoolValidator())
        opt_item = OptionsConfigItem("Demo", "PlayMode", "YYDS",
                                     OptionsValidator(["YYDS", "Yes"]))
        range_item = RangeConfigItem("Demo", "Volume", 50, RangeValidator(0, 100))
        color_item = ColorConfigItem("Demo", "ThemeColor", "#009faa")
        group.addSettingCard(SwitchSettingCard(
            FluentIcon.SETTING, "自动播放", configItem=bool_item))
        group.addSettingCard(ComboBoxSettingCard(
            opt_item, FluentIcon.MUSIC, "播放模式", texts=["YYDS", "Yes"]))
        group.addSettingCard(OptionsSettingCard(
            opt_item, FluentIcon.MUSIC, "音质选项", texts=["YYDS", "Yes"]))
        group.addSettingCard(RangeSettingCard(
            range_item, FluentIcon.VOLUME, "音量"))
        group.addSettingCard(PushSettingCard("配置", FluentIcon.SETTING, "推送设置"))
        group.addSettingCard(HyperlinkCard(
            "https://github.com", "去官网", FluentIcon.LINK, "链接"))
        group.addSettingCard(ColorSettingCard(
            color_item, FluentIcon.BRUSH, "主题色"))
        scroll.setWidget(group)


class _WidgetGalleryTab(QWidget):
    """页签2：QFluentWidgets 常用控件分组展示（可直接交互）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("SmoothScrollArea { background: transparent; border: none; }")
        container = QWidget()
        container.setStyleSheet("QWidget { background: transparent; }")
        scroll.setWidget(container)
        root.addWidget(scroll)

        v = QVBoxLayout(container)
        v.setContentsMargins(24, 16, 24, 20)
        v.setSpacing(14)
        v.addWidget(self._group_card("按钮 Buttons", self._fill_buttons, container))
        v.addWidget(self._group_card("输入 Inputs", self._fill_inputs, container))
        v.addWidget(self._group_card("显示 Display", self._fill_display, container))
        v.addWidget(self._group_card("反馈 Feedback", self._fill_feedback, container))
        v.addWidget(self._group_card("容器 Containers", self._fill_containers, container))
        v.addWidget(self._group_card("导航 Navigation", self._fill_navigation, container))
        v.addWidget(self._group_card("日期 Date & Time", self._fill_datetime, container))
        v.addWidget(self._group_card("窗口 & 设置 Windows", self._fill_windows, container))
        v.addStretch(1)

    def _group_card(self, title, fill_fn, parent):
        """分组卡片：SubtitleLabel 标题 + FlowLayout 条目流；用 fill_fn 填充"""
        card = CardWidget(parent)
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 12, 16, 12)
        box.setSpacing(8)
        box.addWidget(SubtitleLabel(title, card))
        flow = FlowLayout()
        flow.setSpacing(10)
        box.addLayout(flow)
        fill_fn(flow, card)
        return card

    # ------------------------------ 按钮组 ------------------------------
    @staticmethod
    def _demo_menu(parent):
        menu = RoundMenu(parent=parent)
        menu.addAction(Action(FluentIcon.COPY, "复制"))
        menu.addAction(Action(FluentIcon.CUT, "剪切"))
        return menu

    def _fill_buttons(self, flow, card):
        """标准/工具/透明/切换/下拉/拆分/胶囊/链接/命令按钮"""
        add = flow.addWidget
        add(_ControlItem(PushButton("普通按钮", card), "PushButton", card))
        add(_ControlItem(PrimaryPushButton("主要按钮", card), "PrimaryPushButton", card))
        add(_ControlItem(TransparentPushButton("透明按钮", card), "TransparentPushButton", card))
        cmd = CommandButton(FluentIcon.COPY, card)
        cmd.setText("命令按钮")
        cmd.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        add(_ControlItem(cmd, "CommandButton", card))

        add(_ControlItem(self._icon_btn(ToolButton, FluentIcon.ADD, card), "ToolButton", card))
        add(_ControlItem(self._icon_btn(PrimaryToolButton, FluentIcon.ADD, card), "PrimaryToolButton", card))
        add(_ControlItem(self._icon_btn(TransparentToolButton, FluentIcon.MAIL, card), "TransparentToolButton", card))

        toggle = ToggleButton("切换按钮", card)
        toggle.setChecked(True)
        add(_ControlItem(toggle, "ToggleButton", card))
        tp = TogglePushButton("切换推送", card)
        tp.setChecked(True)
        add(_ControlItem(tp, "TogglePushButton", card))
        add(_ControlItem(self._icon_btn(ToggleToolButton, FluentIcon.PLAY, card, checked=True), "ToggleToolButton", card))
        add(_ControlItem(self._icon_btn(TransparentToggleToolButton, FluentIcon.BOOK_SHELF, card, checked=True), "TransparentToggleToolButton", card))
        tt = TransparentTogglePushButton("透明切换", card)
        tt.setChecked(True)
        add(_ControlItem(tt, "TransparentTogglePushButton", card))

        d1 = DropDownPushButton(FluentIcon.MENU, "下拉按钮")
        d1.setMenu(self._demo_menu(d1))
        add(_ControlItem(d1, "DropDownPushButton", card, 168))
        d2 = PrimaryDropDownPushButton(FluentIcon.MENU, "主下拉")
        d2.setMenu(self._demo_menu(d2))
        add(_ControlItem(d2, "PrimaryDropDownPushButton", card, 168))
        d3 = TransparentDropDownPushButton(FluentIcon.MENU, "透明下拉")
        d3.setMenu(self._demo_menu(d3))
        add(_ControlItem(d3, "TransparentDropDownPushButton", card, 168))

        split = SplitPushButton("拆分按钮", card, FluentIcon.SAVE)
        split.clicked.connect(lambda: show_info_bar(
            "点击了主按钮区域", "info", title="SplitPushButton", parent=self.window()))
        add(_ControlItem(split, "SplitPushButton", card, 168))
        add(_ControlItem(self._icon_btn(SplitToolButton, FluentIcon.SAVE, card, 48), "SplitToolButton", card))
        add(_ControlItem(PrimarySplitPushButton("主拆分", card), "PrimarySplitPushButton", card, 168))

        add(_ControlItem(PillPushButton("胶囊按钮", card), "PillPushButton", card))
        add(_ControlItem(self._icon_btn(PillToolButton, FluentIcon.HEART, card), "PillToolButton", card))
        link = HyperlinkButton(card)
        link.setText("超链接")
        link.setUrl("https://github.com")
        add(_ControlItem(link, "HyperlinkButton", card))

        # 单选（互斥）/复选/开关
        radio = QWidget(card)
        rb = QHBoxLayout(radio)
        rb.setContentsMargins(0, 0, 0, 0)
        rb.setSpacing(4)
        rb.addWidget(RadioButton("A", radio))
        rb.addWidget(RadioButton("B", radio))
        radio.setFixedWidth(84)
        add(_ControlItem(radio, "RadioButton", card, 84))
        cb = CheckBox("复选框", card)
        cb.setChecked(True)
        add(_ControlItem(cb, "CheckBox", card))
        sw = SwitchButton(card)
        sw.setChecked(True)
        add(_ControlItem(sw, "SwitchButton", card))

    @staticmethod
    def _icon_btn(cls, icon, parent, w=36, checked=False):
        """构造等尺寸图标按钮（工具/切换类共用）"""
        b = cls(icon, parent)
        b.setFixedSize(w, w)
        if checked:
            b.setChecked(True)
        return b

    # ------------------------------ 输入组 ------------------------------
    def _fill_inputs(self, flow, card):
        add = flow.addWidget
        edit = LineEdit(card)
        edit.setPlaceholderText("输入文本")
        edit.setFixedWidth(132)
        add(_ControlItem(edit, "LineEdit", card, 132))
        search = SearchLineEdit(card)
        search.setPlaceholderText("搜索…")
        search.setFixedWidth(132)
        add(_ControlItem(search, "SearchLineEdit", card, 132))
        pwd = PasswordLineEdit(card)
        pwd.setPlaceholderText("密码")
        pwd.setFixedWidth(132)
        add(_ControlItem(pwd, "PasswordLineEdit", card, 132))
        te = PlainTextEdit(card)
        te.setPlaceholderText("多行文本")
        te.setFixedSize(168, 72)
        add(_ControlItem(te, "PlainTextEdit", card, 168))

        combo = ComboBox(card)
        combo.addItems(["选项 1", "选项 2", "选项 3"])
        combo.setFixedWidth(128)
        add(_ControlItem(combo, "ComboBox", card, 128))
        ecombo = EditableComboBox(card)
        ecombo.addItems(["可编辑 1", "可编辑 2"])
        ecombo.setFixedWidth(128)
        add(_ControlItem(ecombo, "EditableComboBox", card, 128))

        spin = SpinBox(card)
        spin.setRange(0, 100)
        spin.setValue(42)
        add(_ControlItem(spin, "SpinBox", card))
        dspin = DoubleSpinBox(card)
        dspin.setDecimals(1)
        dspin.setRange(0, 1)
        dspin.setSingleStep(0.1)
        dspin.setValue(0.5)
        add(_ControlItem(dspin, "DoubleSpinBox", card))
        cspin = CompactSpinBox(card)
        cspin.setValue(42)
        add(_ControlItem(cspin, "CompactSpinBox", card))
        cdspin = CompactDoubleSpinBox(card)
        cdspin.setValue(0.5)
        add(_ControlItem(cdspin, "CompactDoubleSpinBox", card))

        # 日期/时间输入框（紧凑型）
        add(_ControlItem(CompactDateEdit(card), "CompactDateEdit", card, 148))
        add(_ControlItem(CompactTimeEdit(card), "CompactTimeEdit", card, 148))
        add(_ControlItem(CompactDateTimeEdit(card), "CompactDateTimeEdit", card, 190))

    # ------------------------------ 显示组 ------------------------------
    def _fill_display(self, flow, card):
        add = flow.addWidget
        # 文本标签系列
        add(_ControlItem(LargeTitleLabel("大标题", card), "LargeTitleLabel", card, 120))
        add(_ControlItem(TitleLabel("标题", card), "TitleLabel", card, 120))
        add(_ControlItem(SubtitleLabel("副标题", card), "SubtitleLabel", card, 120))
        add(_ControlItem(DisplayLabel("展示文本", card), "DisplayLabel", card, 120))
        add(_ControlItem(StrongBodyLabel("强调正文", card), "StrongBodyLabel", card))
        add(_ControlItem(BodyLabel("正文", card), "BodyLabel", card))
        add(_ControlItem(CaptionLabel("说明文字", card), "CaptionLabel", card))

        # 徽标/头像/图标
        badges = QWidget(card)
        bh = QHBoxLayout(badges)
        bh.setContentsMargins(0, 0, 0, 0)
        bh.setSpacing(4)
        for b in (InfoBadge.success("成功"), InfoBadge.info("信息"),
                  InfoBadge.warning("警告"), InfoBadge.error("错误")):
            bh.addWidget(b)
        add(_ControlItem(badges, "InfoBadge", card, 240))
        add(_ControlItem(DotInfoBadge(card), "DotInfoBadge", card, 50))
        add(_ControlItem(AvatarWidget("S", card), "AvatarWidget", card))
        add(_ControlItem(IconWidget(FluentIcon.BRUSH, card), "IconWidget", card))

        # 进度类
        bar = ProgressBar(card)
        bar.setValue(60)
        bar.setFixedWidth(140)
        add(_ControlItem(bar, "ProgressBar", card, 140))
        add(_ControlItem(IndeterminateProgressBar(card), "IndeterminateProgressBar", card, 160))
        ring = ProgressRing(card)
        ring.setFixedSize(52, 52)
        ring.setValue(65)
        add(_ControlItem(ring, "ProgressRing", card, 60))
        iring = IndeterminateProgressRing(card)
        iring.setFixedSize(52, 52)
        add(_ControlItem(iring, "IndeterminateProgressRing", card, 60))

        # 滑杆
        slider = Slider(card)
        slider.setRange(0, 100)
        slider.setValue(60)
        slider.setFixedWidth(120)
        add(_ControlItem(slider, "Slider", card, 120))
        cslider = ClickableSlider(card)
        cslider.setRange(0, 100)
        cslider.setValue(60)
        cslider.setFixedWidth(120)
        add(_ControlItem(cslider, "ClickableSlider", card, 120))

    # ------------------------------ 反馈组 ------------------------------
    def _fill_feedback(self, flow, card):
        add = flow.addWidget
        for text, mtype in (("成功 InfoBar", "success"), ("信息 InfoBar", "info"),
                            ("警告 InfoBar", "warning"), ("错误 InfoBar", "error")):
            btn = PushButton(text, card)
            btn.clicked.connect(lambda _=False, t=mtype: show_info_bar(
                f"这是一条 {t} 类型的 InfoBar 提示", t,
                title="InfoBar 测试", parent=self.window()))
            add(_ControlItem(btn, f"InfoBar.{mtype}", card, 168))
        mb = PushButton("弹出 MessageBox", card)
        mb.clicked.connect(self._show_message_box)
        add(_ControlItem(mb, "MessageBox", card, 168))
        md = PushButton("MessageDialog", card)
        md.clicked.connect(self._show_message_dialog)
        add(_ControlItem(md, "MessageDialog", card, 168))
        st = PushButton("显示 StateToolTip", card)
        st.clicked.connect(self._show_state_tooltip)
        add(_ControlItem(st, "StateToolTip", card, 168))
        tt = PushButton("悬停 ToolTip", card)
        tt.setToolTip("这是 ToolTip 提示文本")
        add(_ControlItem(tt, "ToolTip", card))

        seg = SegmentedWidget(card)
        for i in range(3):
            seg.addItem(f"seg{i + 1}", f"分段 {i + 1}")
        seg.setFixedWidth(220)
        add(_ControlItem(seg, "SegmentedWidget", card, 250))

        pips = PipsPager(card)
        pips.setPageNumber(5)
        add(_ControlItem(pips, "PipsPager", card, 160))

    # ------------------------------ 容器组 ------------------------------
    def _fill_containers(self, flow, card):
        add = flow.addWidget
        # 卡片体系
        # Header/GroupHeader 卡片自带内部布局，用 setTitle 填充而非重建 layout
        add(_ControlItem(self._mini_card(CardWidget, "CardWidget", card), "CardWidget", card, 150))
        add(_ControlItem(self._mini_card(SimpleCardWidget, "SimpleCardWidget", card), "SimpleCardWidget", card, 150))
        add(_ControlItem(self._mini_card(ElevatedCardWidget, "ElevatedCardWidget", card), "ElevatedCardWidget", card, 150))
        hcw = HeaderCardWidget(card)
        hcw.setTitle("HeaderCardWidget")
        hcw.setFixedWidth(160)
        add(_ControlItem(hcw, "HeaderCardWidget", card, 168))
        gcw = GroupHeaderCardWidget(card)
        gcw.setTitle("GroupHeaderCardWidget")
        gcw.setFixedWidth(160)
        add(_ControlItem(gcw, "GroupHeaderCardWidget", card, 168))

        # 列表/树/表格
        lst = ListWidget(card)
        lst.addItems(["列表条目 1", "列表条目 2", "列表条目 3"])
        lst.setFixedSize(168, 88)
        add(_ControlItem(lst, "ListWidget", card, 168))
        lview = ListView(card)
        add(_ControlItem(lview, "ListView", card, 128))

        tree = TreeWidget(card)
        for p, kids in (("父节点 1", ["子节点 1-1", "子节点 1-2"]),
                        ("父节点 2", ["子节点 2-1", "子节点 2-2"])):
            pnode = QTreeWidgetItem([p])
            for k in kids:
                pnode.addChild(QTreeWidgetItem([k]))
            tree.addTopLevelItem(pnode)
        tree.expandAll()
        tree.setFixedSize(168, 88)
        add(_ControlItem(tree, "TreeWidget", card, 168))

        table = TableWidget(card)
        table.setColumnCount(3)
        table.setRowCount(3)
        for r in range(3):
            for c in range(3):
                table.setItem(r, c, QTableWidgetItem(f"{r + 1},{c + 1}"))
        table.setFixedSize(168, 88)
        add(_ControlItem(table, "TableWidget", card, 168))

        # 标签栏 / 标签页
        tab = TabBar(card)
        tab.addTab("tab1", "标签 1", FluentIcon.FOLDER.qicon())
        tab.addTab("tab2", "标签 2", FluentIcon.DOCUMENT.qicon())
        tab.addTab("tab3", "标签 3", FluentIcon.VIEW.qicon())
        add(_ControlItem(tab, "TabBar", card, 240))

        tw = TabWidget(card)
        for i in range(2):
            page = QWidget()
            pl = QVBoxLayout(page)
            pl.addWidget(BodyLabel(f"标签页内容 {i + 1}", page))
            tw.addTab(page, f"页 {i + 1}")
        tw.setFixedSize(180, 76)
        add(_ControlItem(tw, "TabWidget", card, 180))

        # 滚动/平滑滚动（静态展示）
        add(_ControlItem(self._mini_scroll(card), "SmoothScrollArea", card, 168))

    @staticmethod
    def _mini_card(cls, text, parent):
        w = cls(parent)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.addWidget(BodyLabel(text, w))
        w.setFixedWidth(150)
        return w

    @staticmethod
    def _mini_scroll(parent):
        from qfluentwidgets import SmoothScrollArea
        sc = SmoothScrollArea(parent)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        for i in range(1, 4):
            lay.addWidget(BodyLabel(f"滚动行 {i}", inner))
        sc.setWidget(inner)
        sc.setFixedSize(150, 64)
        return sc

    # ------------------------------ 导航组 ------------------------------
    def _fill_navigation(self, flow, card):
        add = flow.addWidget
        bread = BreadcrumbBar(card)
        bread.addItem(FluentIcon.HOME, "首页")
        bread.addItem(FluentIcon.FOLDER, "分类")
        bread.addItem(FluentIcon.DOCUMENT, "详情")
        bread.setFixedWidth(260)
        add(_ControlItem(bread, "BreadcrumbBar", card, 280))

        add(_ControlItem(NavigationToolButton(FluentIcon.HOME, card), "NavigationToolButton", card))
        add(_ControlItem(NavigationPushButton(FluentIcon.HOME, "首页", True), "NavigationPushButton", card))
        add(_ControlItem(NavigationSeparator(card), "NavigationSeparator", card))

        pivot = Pivot(card)
        for i, t in enumerate(("Tab 1", "Tab 2", "Tab 3")):
            pivot.addItem(f"route{i}", t)
        pivot.setCurrentItem("route1")
        pivot.setFixedWidth(240)
        add(_ControlItem(pivot, "Pivot", card, 260))

    # ------------------------------ 日期组 ------------------------------
    def _fill_datetime(self, flow, card):
        add = flow.addWidget
        add(_ControlItem(CalendarPicker(card), "CalendarPicker", card, 220))
        add(_ControlItem(DatePicker(card), "DatePicker", card, 200))
        add(_ControlItem(TimePicker(card), "TimePicker", card, 140))
        add(_ControlItem(DateEdit(card), "DateEdit", card, 148))
        add(_ControlItem(TimeEdit(card), "TimeEdit", card, 148))
        add(_ControlItem(DateTimeEdit(card), "DateTimeEdit", card, 190))

    # ------------------------------ 交互演示 ------------------------------
    def _show_message_box(self):
        box = MessageBox("组件测试", "这是 MessageBox 对话框，用于确认类交互",
                         self.window())
        box.yesButton.setText("确定")
        box.cancelButton.setText("取消")
        box.exec()

    def _show_message_dialog(self):
        from qfluentwidgets import MessageDialog
        dlg = MessageDialog("提示", "这是 MessageDialog 对话框", self.window())
        dlg.yesButton.setText("确定")
        dlg.cancelButton.setText("取消")
        dlg.exec()

    def _show_state_tooltip(self):
        tip = StateToolTip("执行中", "正在演示 StateToolTip…", self.window())
        tip.show()
        QTimer.singleShot(2500, tip.hide)

    # ------------------------------ 窗口 & 设置组 ------------------------------
    def _fill_windows(self, flow, card):
        """窗口级/重组件：嵌入控件墙会撑坏布局，改为点击按钮弹出真实示例"""
        add = flow.addWidget
        add(_ControlItem(self._demo_btn("SettingCard 设置", FluentIcon.SETTING,
                                        self._show_settings_demo, card),
                         "SettingCard 示例", card))
        add(_ControlItem(self._demo_btn("ComboBoxSettingCard", FluentIcon.MUSIC,
                                        self._show_settings_demo, card),
                         "ComboBoxSettingCard", card))
        add(_ControlItem(self._demo_btn("SplashScreen 启动屏", FluentIcon.PALETTE,
                                        self._show_splash_demo, card),
                         "SplashScreen", card))
        add(_ControlItem(self._demo_btn("FluentWindow 窗口", FluentIcon.HOME,
                                        self._show_fluent_window, card),
                         "FluentWindow", card))
        add(_ControlItem(self._demo_btn("SplitFluentWindow", FluentIcon.CALENDAR,
                                        self._show_split_window, card),
                         "SplitFluentWindow", card))

    @staticmethod
    def _demo_btn(text, icon, handler, parent):
        btn = PushButton(text, parent)
        btn.setIcon(icon.qicon())
        btn.clicked.connect(handler)
        return btn

    def _show_settings_demo(self):
        _SettingsDemoDialog(self.window()).exec()

    def _show_splash_demo(self):
        win = _DemoFluentWindow()
        win.destroyed.connect(lambda: _safe_remove(_DEMO_WINDOWS, win))
        _DEMO_WINDOWS.append(win)
        splash = SplashScreen(FluentIcon.DOCUMENT.qicon(), win)
        splash.show()

        def _finish():
            try:
                splash.finish()
                win.showMaximized()
            except RuntimeError:
                pass

        QTimer.singleShot(1500, _finish)

    def _show_fluent_window(self):
        win = _DemoFluentWindow()
        win.destroyed.connect(lambda: _safe_remove(_DEMO_WINDOWS, win))
        _DEMO_WINDOWS.append(win)
        win.show()

    def _show_split_window(self):
        win = _DemoSplitWindow()
        win.destroyed.connect(lambda: _safe_remove(_DEMO_WINDOWS, win))
        _DEMO_WINDOWS.append(win)
        win.show()


class TestPage(QWidget):
    """组件测试页：FluentIcon 图标库 + QFluentWidgets 控件测试（两个页签）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lazy_built = False

    def showEvent(self, event):
        super().showEvent(event)
        if not self._lazy_built:
            self._lazy_built = True
            self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget(self)
        self.tabs.setDocumentMode(True)
        self.icon_tab = _IconGalleryTab(self.tabs)
        self.widget_tab = _WidgetGalleryTab(self.tabs)
        self.tabs.addTab(self.icon_tab, "图标库")
        self.tabs.addTab(self.widget_tab, "控件测试")
        root.addWidget(self.tabs)
