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
from PySide6.QtGui import QFontMetrics, QImage, QPixmap, QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QApplication, QStackedWidget, QTableWidgetItem,
                               QTreeWidgetItem, QListWidgetItem, QDialog,
                               QFileDialog)

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
    RangeValidator, BoolValidator, ColorConfigItem, qconfig,
    ImageLabel, IconInfoBadge, TableView, SingleDirectionScrollArea,
    SegmentedToolWidget, SegmentedToggleToolWidget, CommandBar, CommandBarView,
    CheckableMenu, SystemTrayMenu, TeachingTip, PopupTeachingTip, Flyout,
    TeachingTipView, FlyoutView, ToolTipFilter, Dialog, ColorDialog,
    MessageDialog, MessageBoxBase, FlipView, AdaptiveFlowLayout, NavigationInterface)
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


class _MediaPlayerDialog(QDialog):
    """多媒体播放器示例：基于 OpenCV 逐帧渲染 + QTimer。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("多媒体播放器示例")
        self.resize(680, 500)

        # 延迟导入，避免拖慢控件测试页加载
        import cv2
        import numpy as np
        self._cv = cv2
        self._np = np

        self._cap = None            # VideoCapture；None 表示演示动画模式
        self._playing = False
        self._fps = 25.0
        self._frame_count = 0
        self._syncing = False

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._next_frame)

        self._build_ui()
        self._use_demo()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        root.addWidget(TitleLabel("多媒体播放", self))
        root.addWidget(CaptionLabel(
            "qfluentwidgets 已移除 VideoWidget/MediaPlayBar；", self))

        self._screen = QLabel(self)
        self._screen.setMinimumSize(560, 315)
        self._screen.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._screen.setStyleSheet(
            "background: #101418; border-radius: 8px; color: #8a8a8a; font-size: 13px;")
        self._screen.setText("未加载媒体")
        root.addWidget(self._screen)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._play_btn = ToolButton(FluentIcon.PLAY, self)
        self._play_btn.setFixedSize(36, 36)
        self._play_btn.setToolTip("播放 / 暂停")
        self._play_btn.clicked.connect(self._toggle_play)
        bar.addWidget(self._play_btn)

        self._stop_btn = ToolButton(self._stop_icon(), self)
        self._stop_btn.setFixedSize(36, 36)
        self._stop_btn.setToolTip("停止并回到起点")
        self._stop_btn.clicked.connect(self._stop)
        bar.addWidget(self._stop_btn)

        self._open_btn = PushButton("打开视频", self)
        self._open_btn.clicked.connect(self._open_file)
        bar.addWidget(self._open_btn)

        self._time_lbl = CaptionLabel("00:00 / 00:00", self)
        bar.addWidget(self._time_lbl)
        bar.addStretch(1)

        self._slider = Slider(Qt.Orientation.Horizontal, self)
        self._slider.setRange(0, 0)
        self._slider.setFixedHeight(20)
        self._slider.sliderPressed.connect(self._on_slider_press)
        self._slider.sliderReleased.connect(self._on_slider_release)
        bar.addWidget(self._slider, 1)
        root.addLayout(bar)

    @staticmethod
    def _stop_icon():
        for name in ("CANCEL", "STOP", "DELETE", "REMOVE"):
            if hasattr(FluentIcon, name):
                return getattr(FluentIcon, name).qicon()
        return FluentIcon.PLAY.qicon()

    # ------------------------------------------------------------------ 源
    def _use_demo(self):
        """切换到内置演示动画（无需媒体文件）"""
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._fps = 20.0
        self._frame_count = 0
        self._playing = True
        self._slider.blockSignals(True)
        self._slider.setRange(0, 0)
        self._slider.setValue(0)
        self._slider.blockSignals(False)
        self._timer.start(int(1000 / self._fps))
        self._update_play_icon()

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", "",
            "视频 (*.mp4 *.mkv *.avi *.mov *.wmv *.flv *.webm *.ts)")
        if not path:
            return
        cap = self._cv.VideoCapture(path)
        if not cap.isOpened():
            show_info_bar("无法打开该视频文件（可能缺少解码器）", "error",
                          title="多媒体播放", parent=self)
            return
        if self._cap is not None:
            self._cap.release()
        self._cap = cap
        total = int(cap.get(self._cv.CAP_PROP_FRAME_COUNT))
        fps = cap.get(self._cv.CAP_PROP_FPS)
        self._fps = fps if fps and fps > 0 else 25.0
        self._frame_count = 0
        self._slider.blockSignals(True)
        self._slider.setRange(0, max(total - 1, 0))
        self._slider.setValue(0)
        self._slider.blockSignals(False)
        self._playing = True
        if not self._timer.isActive():
            self._timer.start(int(1000 / self._fps))
        self._update_play_icon()

    # ------------------------------------------------------------------ 播放
    def _toggle_play(self):
        self._playing = not self._playing
        if self._playing and not self._timer.isActive():
            self._timer.start(int(1000 / self._fps))
        self._update_play_icon()

    def _stop(self):
        self._playing = False
        if self._cap is not None:
            self._cap.set(self._cv.CAP_PROP_POS_FRAMES, 0)
        self._frame_count = 0
        self._slider.blockSignals(True)
        self._slider.setValue(0)
        self._slider.blockSignals(False)
        self._update_play_icon()

    def _next_frame(self):
        if not self._playing:
            return
        cv = self._cv
        if self._cap is not None:
            ret, frame = self._cap.read()
            if not ret:
                # 循环播放
                self._cap.set(cv.CAP_PROP_POS_FRAMES, 0)
                ret, frame = self._cap.read()
                if not ret:
                    self._stop()
                    return
            self._frame_count += 1
            if not self._syncing:
                self._slider.blockSignals(True)
                self._slider.setValue(self._frame_count)
                self._slider.blockSignals(False)
        else:
            frame = self._gen_demo_frame()
            self._frame_count += 1
        self._show_frame(frame)
        self._update_time()

    def _gen_demo_frame(self):
        cv, np = self._cv, self._np
        W, H = 640, 360
        t = self._frame_count
        frame = np.zeros((H, W, 3), dtype=np.uint8)
        frame[:] = (18, 26, 34)
        x = int((t * 6) % (W + 140)) - 70
        cv.rectangle(frame, (x, H // 2 - 40), (x + 80, H // 2 + 40), (66, 159, 0), -1)
        cv.rectangle(frame, (10, 10), (W - 10, 46), (40, 50, 60), -1)
        # cv2 的 Hershey 字体不支持中文，故用英文叠加文字
        cv.putText(frame, f"QWidgets media demo - frame {t}", (20, 36),
                   cv.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv.LINE_AA)
        return frame

    def _show_frame(self, frame):
        rgb = self._cv.cvtColor(frame, self._cv.COLOR_BGR2RGB)
        h, w, _ = rgb.shape
        img = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        self._screen.setPixmap(QPixmap.fromImage(img))

    # ------------------------------------------------------------------ 控制
    def _update_play_icon(self):
        try:
            icon = FluentIcon.PAUSE.qicon() if self._playing else FluentIcon.PLAY.qicon()
        except AttributeError:
            icon = FluentIcon.PLAY.qicon()
        self._play_btn.setIcon(icon)

    def _update_time(self):
        fps = self._fps if self._fps > 0 else 25.0
        cur = int(self._frame_count / fps)
        if self._cap is None:
            total = cur  # 演示模式下总时长随帧推进
        else:
            total = int(self._slider.maximum() / fps) if self._slider.maximum() > 0 else cur
        self._time_lbl.setText(f"{cur // 60:02d}:{cur % 60:02d} / {total // 60:02d}:{total % 60:02d}")

    def _on_slider_press(self):
        self._syncing = True

    def _on_slider_release(self):
        self._syncing = False
        pos = self._slider.value()
        if self._cap is not None:
            self._cap.set(self._cv.CAP_PROP_POS_FRAMES, pos)
        self._frame_count = pos

    def closeEvent(self, event):
        if self._cap is not None:
            self._cap.release()
        self._timer.stop()
        super().closeEvent(event)


class _DemoMessageBox(MessageBoxBase):
    """MessageBoxBase 基类示例：自定义内容的消息框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel("MessageBoxBase 示例", self)
        self.contentLabel = BodyLabel(
            "这是基于 MessageBoxBase 自定义的消息框，可自由定制内容。", self)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.contentLabel)
        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(360)


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
        v.addWidget(self._group_card("菜单 & 命令 Menus & Commands", self._fill_menus, container))
        v.addWidget(self._group_card("提示 & 弹层 Tips & Flyout", self._fill_tips, container))
        v.addWidget(self._group_card("对话框 Dialogs", self._fill_dialogs, container))
        v.addWidget(self._group_card("翻页 & 布局 Flip & Layout", self._fill_flip_layout, container))
        v.addWidget(self._group_card("多媒体 Multimedia", self._fill_media, container))
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
        il = ImageLabel(card)
        pm = QPixmap(88, 56)
        pm.fill(QColor("#009faa"))
        il.setImage(pm)
        add(_ControlItem(il, "ImageLabel", card, 96))
        add(_ControlItem(IconInfoBadge(card), "IconInfoBadge", card, 60))

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

        tv = TableView(card)
        tv.setFixedSize(168, 88)
        add(_ControlItem(tv, "TableView", card, 168))

        scd = SingleDirectionScrollArea(card)
        scd.setFixedSize(160, 64)
        inner = QWidget()
        ilay = QVBoxLayout(inner)
        ilay.setContentsMargins(8, 8, 8, 8)
        for i in range(3):
            ilay.addWidget(BodyLabel(f"单向滚动 {i}", inner))
        scd.setWidget(inner)
        add(_ControlItem(scd, "SingleDirectionScrollArea", card, 168))

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

        seg_tools = SegmentedToolWidget(card)
        for i in range(3):
            seg_tools.addItem(f"tool{i}", f"工具 {i + 1}")
        seg_tools.setFixedWidth(200)
        add(_ControlItem(seg_tools, "SegmentedToolWidget", card, 220))

        seg_tog = SegmentedToggleToolWidget(card)
        for i in range(3):
            seg_tog.addItem(f"tog{i}", f"切换 {i + 1}")
        seg_tog.setFixedWidth(200)
        add(_ControlItem(seg_tog, "SegmentedToggleToolWidget", card, 220))

    # ------------------------------ 日期组 ------------------------------
    def _fill_datetime(self, flow, card):
        add = flow.addWidget
        add(_ControlItem(CalendarPicker(card), "CalendarPicker", card, 220))
        add(_ControlItem(DatePicker(card), "DatePicker", card, 200))
        add(_ControlItem(TimePicker(card), "TimePicker", card, 140))
        add(_ControlItem(DateEdit(card), "DateEdit", card, 148))
        add(_ControlItem(TimeEdit(card), "TimeEdit", card, 148))
        add(_ControlItem(DateTimeEdit(card), "DateTimeEdit", card, 190))

    # ------------------------------ 菜单 & 命令组 ------------------------------
    def _fill_menus(self, flow, card):
        add = flow.addWidget
        cb = CommandBar(card)
        cb.addAction(Action(FluentIcon.COPY, "复制"))
        cb.addAction(Action(FluentIcon.CUT, "剪切"))
        cb.addAction(Action(FluentIcon.SAVE, "保存"))
        cb.setFixedWidth(180)
        add(_ControlItem(cb, "CommandBar", card, 200))

        cbv = CommandBarView(card)
        cbv.addAction(Action(FluentIcon.FOLDER, "文件夹"))
        cbv.addAction(Action(FluentIcon.DOCUMENT, "文档"))
        cbv.setFixedWidth(180)
        add(_ControlItem(cbv, "CommandBarView", card, 200))

        rm_btn = PushButton("RoundMenu", card)
        rm = RoundMenu("菜单", rm_btn.parent())
        rm.addAction(Action(FluentIcon.COPY, "复制"))
        rm.addAction(Action(FluentIcon.CUT, "剪切"))
        rm_btn.clicked.connect(
            lambda: rm.exec(rm_btn.mapToGlobal(rm_btn.rect().bottomLeft())))
        add(_ControlItem(rm_btn, "RoundMenu", card, 136))

        cm_btn = PushButton("CheckableMenu", card)
        cm = CheckableMenu("可勾选菜单", cm_btn.parent())
        for txt, ic in (("复制", FluentIcon.COPY), ("剪切", FluentIcon.CUT),
                        ("保存", FluentIcon.SAVE)):
            a = Action(ic, txt)
            a.setCheckable(True)
            cm.addAction(a)
        cm_btn.clicked.connect(
            lambda: cm.exec(cm_btn.mapToGlobal(cm_btn.rect().bottomLeft())))
        add(_ControlItem(cm_btn, "CheckableMenu", card, 136))

        stm_btn = PushButton("SystemTrayMenu", card)
        stm = SystemTrayMenu("托盘菜单", stm_btn.parent())
        stm.addAction(Action(FluentIcon.HOME, "托盘项"))
        stm_btn.clicked.connect(
            lambda: stm.exec(stm_btn.mapToGlobal(stm_btn.rect().bottomLeft())))
        add(_ControlItem(stm_btn, "SystemTrayMenu", card, 136))

    # ------------------------------ 提示 & 弹层组 ------------------------------
    def _fill_tips(self, flow, card):
        add = flow.addWidget
        tt = PushButton("TeachingTip", card)
        tt.clicked.connect(lambda: self._show_teaching_tip(tt))
        add(_ControlItem(tt, "TeachingTip", card, 136))

        pt = PushButton("PopupTeachingTip", card)
        pt.clicked.connect(lambda: self._show_popup_tip(pt))
        add(_ControlItem(pt, "PopupTeachingTip", card, 160))

        fy = PushButton("Flyout", card)
        fy.clicked.connect(lambda: self._show_flyout(fy))
        add(_ControlItem(fy, "Flyout", card, 136))

        tf = PushButton("ToolTipFilter", card)
        tf.setToolTip("悬停查看 ToolTipFilter 显示的提示")
        ToolTipFilter(tf, showDelay=100)
        add(_ControlItem(tf, "ToolTipFilter", card, 136))

    # ------------------------------ 对话框组 ------------------------------
    def _fill_dialogs(self, flow, card):
        add = flow.addWidget
        db = PushButton("Dialog", card)
        db.clicked.connect(self._show_dialog)
        add(_ControlItem(db, "Dialog", card, 128))

        cb = PushButton("ColorDialog", card)
        cb.clicked.connect(self._show_color_dialog)
        add(_ControlItem(cb, "ColorDialog", card, 128))

        mb = PushButton("MessageBoxBase", card)
        mb.clicked.connect(self._show_message_box_base)
        add(_ControlItem(mb, "MessageBoxBase", card, 140))

    # ------------------------------ 翻页 & 布局组 ------------------------------
    def _fill_flip_layout(self, flow, card):
        add = flow.addWidget
        fv = FlipView(card)
        imgs = []
        for c in ("#e63946", "#457b9d", "#2a9d8f", "#e9c46a"):
            pm = QPixmap(120, 72)
            pm.fill(QColor(c))
            imgs.append(pm)
        fv.addImages(imgs)
        fv.setFixedSize(140, 88)
        add(_ControlItem(fv, "FlipView", card, 150))

        add(_ControlItem(self._hint("FlipImageDelegate：FlipView 的图标委托", card),
                         "FlipImageDelegate", card, 220))
        add(_ControlItem(self._hint("FlowLayout 流式布局", card),
                         "FlowLayout", card, 220))
        add(_ControlItem(self._hint("AdaptiveFlowLayout 自适应流式布局", card),
                         "AdaptiveFlowLayout", card, 220))
        add(_ControlItem(self._hint("NavigationInterface 导航接口", card),
                         "NavigationInterface", card, 220))

    # ------------------------------ 多媒体组 ------------------------------
    def _fill_media(self, flow, card):
        add = flow.addWidget
        mp = PushButton("打开媒体播放器", card)
        mp.clicked.connect(self._open_media_player)
        add(_ControlItem(mp, "MediaPlayer", card, 180))
        add(_ControlItem(
            self._hint("VideoWidget / MediaPlayBar", card),
            "Multimedia", card, 220))

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

    # ------------------------------ 新增组件交互演示 ------------------------------
    @staticmethod
    def _hint(text, parent, w=220):
        lbl = QLabel(text, parent)
        lbl.setWordWrap(True)
        lbl.setFixedWidth(w)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return lbl

    def _show_teaching_tip(self, anchor):
        view = TeachingTipView("教学提示", "这是一条 TeachingTip 提示",
                               FluentIcon.INFO, isClosable=False)
        tip = TeachingTip(view, anchor, duration=2200, isDeleteOnClose=True)
        tip.show()

    def _show_popup_tip(self, anchor):
        view = TeachingTipView("弹出教学", "PopupTeachingTip 弹出提示",
                               FluentIcon.INFO, isClosable=False)
        tip = PopupTeachingTip(view, anchor, duration=2200, isDeleteOnClose=True)
        tip.show()

    def _show_flyout(self, anchor):
        view = FlyoutView("飞出面", "这是一条 Flyout 内容", FluentIcon.INFO, isClosable=False)
        Flyout.make(view, anchor, parent=anchor)

    def _show_dialog(self):
        dlg = Dialog("示例对话框", "这是 qfluentwidgets 的 Dialog。", self.window())
        dlg.yesButton.setText("确定")
        dlg.cancelButton.setText("取消")
        dlg.exec()

    def _show_color_dialog(self):
        ColorDialog(QColor("#009faa"), "选择颜色", self.window()).exec()

    def _show_message_box_base(self):
        _DemoMessageBox(self.window()).exec()

    def _open_media_player(self):
        _MediaPlayerDialog(self.window()).exec()


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
        root.setSpacing(0)
        # 页面切换用 Pivot（页签导航）+ QStackedWidget（内容堆栈）替换原生 QTabWidget，
        # 与设置对话框（settings_dialog）的 Pivot 分页模式一致，观感贴合 Fluent 风格
        self.pivot = Pivot(self)
        self.pivot.addItem(routeKey="icon", text="图标库",
                           onClick=lambda *_: self._switch_page("icon"))
        self.pivot.addItem(routeKey="widget", text="控件测试",
                           onClick=lambda *_: self._switch_page("widget"))
        self.stack = QStackedWidget(self)
        self.icon_tab = _IconGalleryTab(self.stack)
        self.widget_tab = _WidgetGalleryTab(self.stack)
        self.stack.addWidget(self.icon_tab)
        self.stack.addWidget(self.widget_tab)
        root.addWidget(self.pivot, 0, Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self.stack, 1)
        self.pivot.setCurrentItem("icon")

    def _switch_page(self, key):
        """Pivot 页签切换：同步高亮与内容堆栈"""
        self.pivot.setCurrentItem(key)
        self.stack.setCurrentWidget(
            self.icon_tab if key == "icon" else self.widget_tab)
