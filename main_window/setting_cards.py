# -*- coding: utf-8 -*-
"""统一设置页的卡片式设置组件（2026-09-07 重构）

视觉规格（对齐售后运维面板成熟卡片样式 + Watt Toolkit 内联控件）：
  - SettingGroup：组标题（小号加粗）+ 纵向容器，组内**每项设置一张独立
    圆角卡片**（CardWidget），卡片间距 10px（此前为整组一张大卡+行间
    分隔线，用户反馈改为逐项卡片）
  - SettingRow：一张卡片。左=线性图标 + 标题（加粗）+ 副标题说明（灰色
    小字）；右=操作控件（开关 / 下拉 / SpinBox / 按钮 / 组合），控件
    **直接内联**（字号/字体/缩放等不再走「更改…」弹窗）

所有行控件均为松耦合：由统一设置页（hub_pages.SettingsHubPage）负责把
控件信号接到主窗口既有回调（_on_theme_selected 等），业务逻辑零重复。
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFrame,
                               QLabel, QSizePolicy)
from qfluentwidgets import (CardWidget, CaptionLabel, BodyLabel,
                            SwitchButton, ComboBox, PushButton, ToolButton,
                            SpinBox, FluentIconBase, Theme, isDarkTheme)


class SubRow(QWidget):
    """折叠卡（ExpandSettingCard）内部的轻量行：标题 + 右侧控件，
    无卡片背景（避免卡中卡）"""

    def __init__(self, title, control, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 6, 20, 6)
        lay.setSpacing(12)
        lay.addWidget(BodyLabel(title, self))
        lay.addStretch(1)
        if control is not None:
            lay.addWidget(control, 0, Qt.AlignRight | Qt.AlignVCenter)


class _IconLabel(QWidget):
    """设置行左侧线性图标（随主题切换明暗）"""

    def __init__(self, icon: FluentIconBase, parent=None):
        super().__init__(parent)
        self._icon = icon
        self.setFixedSize(30, 30)
        self._label = QLabel(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._label)
        self._repaint_icon()

    def _repaint_icon(self):
        self._label.setPixmap(self._icon.icon(Theme.DARK if isDarkTheme()
                                             else Theme.LIGHT).pixmap(18, 18))

    def showEvent(self, e):  # 主题切换后再次显示时刷新明暗
        super().showEvent(e)
        self._repaint_icon()


class SettingRow(CardWidget):
    """单张设置卡片：图标 + 标题/描述 + 右侧内联控件区

    control 可以是单个 widget 或 widget 列表（横排，如「色块 + 更改 +
    还原默认」）。此前为组内无边框行（整组一卡），2026-09-07 起每项
    一张独立卡片（对齐售后运维面板样式）。
    """

    def __init__(self, icon, title, desc="", control=None, parent=None):
        super().__init__(parent)
        self.setObjectName("settingRow")
        self.setMinimumHeight(56)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(12)

        if icon is not None:
            self._icon_w = _IconLabel(icon, self)
            lay.addWidget(self._icon_w)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        t = BodyLabel(title, self)
        t.setStyleSheet("font-weight: 600;")
        text_col.addWidget(t)
        if desc:
            d = CaptionLabel(desc, self)
            d.setWordWrap(True)
            # 副标题显式次级灰（浅色深灰 / 深色浅灰），themeChanged 自动切换。
            # 2026-09-07 用户反馈：依赖默认色在真机深色模式下显示为黑看不清。
            d.setTextColor(QColor(0, 0, 0, 170), QColor(255, 255, 255, 170))
            text_col.addWidget(d)
        lay.addLayout(text_col, 0)

        lay.addStretch(1)

        if control is not None:
            ctrls = control if isinstance(control, (list, tuple)) else [control]
            for c in ctrls:
                lay.addWidget(c, 0, Qt.AlignRight | Qt.AlignVCenter)

    def add_stretch_hint(self):
        """文本列允许换行拉宽（长描述行用）"""
        pass


def make_switch(checked, on_change, text=True):
    """Watt 式开关：左侧带「开/关」文字的 SwitchButton"""
    sw = SwitchButton("开" if checked else "关")
    sw.setChecked(checked)

    def _on(checked_):
        sw.setText("开" if checked_ else "关")
        on_change(checked_)

    sw.checkedChanged.connect(_on)
    return sw


def make_combo(items, index, on_change, width=170):
    """下拉选择：items=[(文本, data)]，on_change(data)。

    先 setCurrentIndex 再 connect：初始化回显不误触发 on_change。
    """
    cb = ComboBox()
    for label, data in items:
        cb.addItem(label, userData=data)
    if index >= 0:
        cb.setCurrentIndex(index)
    cb.currentIndexChanged.connect(
        lambda _i, c=cb: on_change(c.currentData()))
    cb.setFixedWidth(width)
    return cb


def make_spinbox(value, lo, hi, suffix="", on_change=None, width=110):
    """内联数字调节（字号等）：先 setValue 再 connect，回显不误触发。"""
    sb = SpinBox()
    sb.setRange(lo, hi)
    if suffix:
        sb.setSuffix(suffix)
    sb.setValue(value)
    sb.setFixedWidth(width)
    if on_change is not None:
        sb.valueChanged.connect(on_change)
    return sb


def make_button(text, on_click, icon=None, width=None, primary=False):
    """普通/主按钮"""
    btn = ToolButton(icon) if (icon is not None and not text) else (
        PushButton(icon, text) if icon is not None else PushButton(text))
    if primary:
        from qfluentwidgets import PrimaryPushButton
        new_btn = PrimaryPushButton(icon, text) if icon else PrimaryPushButton(text)
        # 复用外部传入的连接逻辑
        btn = new_btn
    if width:
        btn.setFixedWidth(width)
    btn.clicked.connect(lambda _=False: on_click())
    return btn


class SettingGroup(QWidget):
    """一组设置：组标题 + 逐项独立卡片（2026-09-07 由「整组一卡+分隔线」
    改为每项一卡，卡片间距 10px，对齐售后运维面板成熟样式）"""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("settingGroup")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)
        if title:
            t = CaptionLabel(title, self)
            t.setStyleSheet("font-weight: 600; padding: 2px 4px;")
            # 组标题与卡片副标题同款次级灰双槽（深色模式可读，2026-09-07
            # 用户反馈依赖默认/调色板色真机显示为黑）；color 必须留在
            # setTextColor 双槽，styleSheet 内联 color 会覆盖 custom 槽
            t.setTextColor(QColor(0, 0, 0, 200), QColor(255, 255, 255, 200))
            outer.addWidget(t, 0, Qt.AlignLeft)
            outer.addSpacing(2)

        self._body = outer
        self._rows = []

    def addRow(self, row: SettingRow):
        """追加一张设置卡片"""
        self._body.addWidget(row)
        self._rows.append(row)
        return row

    def addWidget(self, w):
        """嵌入整卡组件（如 AdminSettingsPage / 周期设置卡 / 署名卡）"""
        self._body.addWidget(w)
        return w
