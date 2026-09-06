# -*- coding: utf-8 -*-
"""统一设置页的 Watt Toolkit 式分组卡片组件

视觉规格（对照 Watt Toolkit 设置界面 + design/fluent_window_proposal.html 的
.set-group/.set-card 规格）：
  - SettingGroup：组标题（小号加粗）+ 一张圆角大卡片，卡片内是多行设置行，
    行间细分隔线（与 Watt 一致：整组一张卡，行内不另起卡片）
  - SettingRow：左=线性图标 + 标题（加粗）+ 副标题说明（灰色小字）；
    右=操作控件（开关 / 下拉 / 按钮 / 组合），行高约 52~56px

所有行控件均为松耦合：由统一设置页（hub_pages.SettingsHubPage）负责把
控件信号接到主窗口既有回调（_on_theme_selected 等），业务逻辑零重复。
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFrame,
                               QLabel, QSizePolicy)
from qfluentwidgets import (CardWidget, CaptionLabel, BodyLabel,
                            SwitchButton, ComboBox, PushButton, ToolButton,
                            FluentIconBase, Theme, isDarkTheme)


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


class SettingRow(QFrame):
    """单行设置项：图标 + 标题/描述 + 右侧控件区

    control 可以是单个 widget 或 widget 列表（横排，如「更改 + 还原默认」）。
    """

    def __init__(self, icon, title, desc="", control=None, parent=None):
        super().__init__(parent)
        self.setObjectName("settingRow")
        self.setMinimumHeight(52)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 8, 16, 8)
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
    """下拉选择：items=[(文本, data)]，on_change(data)"""
    cb = ComboBox()
    for label, data in items:
        cb.addItem(label, userData=data)
    if index >= 0:
        cb.setCurrentIndex(index)
    cb.currentIndexChanged.connect(
        lambda _i, c=cb: on_change(c.currentData()))
    cb.setFixedWidth(width)
    return cb


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


class SettingGroup(CardWidget):
    """一组设置行：组标题 + 单张大卡片 + 行间分隔线（Watt Toolkit 样式）"""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("settingGroup")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        if title:
            t = CaptionLabel(title, self)
            t.setStyleSheet(
                "font-weight: 600; color: palette(mid); padding: 2px 4px;")
            outer.addWidget(t, 0, Qt.AlignLeft)
            outer.addSpacing(4)

        self._body = QVBoxLayout()
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(0)
        outer.addLayout(self._body)
        self._rows = []

    def addRow(self, row: SettingRow):
        """追加一行（行间自动插分隔线）"""
        if self._rows:
            line = QFrame(self)
            line.setFixedHeight(1)
            line.setStyleSheet("background: rgba(128, 128, 128, 0.45); "
                               "border: none;")
            self._body.addWidget(line)
        self._body.addWidget(row)
        self._rows.append(row)
        return row

    def addWidget(self, w):
        """嵌入整卡组件（如 MysqlSyncCard / 周期设置卡），上下加留白"""
        wrap = QVBoxLayout()
        wrap.setContentsMargins(8, 8, 8, 8)
        wrap.addWidget(w)
        self._body.addLayout(wrap)
        return w
