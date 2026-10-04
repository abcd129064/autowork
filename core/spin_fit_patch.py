# -*- coding: utf-8 -*-
"""qfw SpinBox 宽度自适应：端口等多位数字不再被上下按钮遮挡

问题（2026-10-04 用户截图「tcp 端口显示不全，数字被上下箭头遮挡」）：
qfluentwidgets 的 `SpinBox`（`InlineSpinBoxBase`）用 `setButtonSymbols(NoButtons)`
关掉了 Qt 原生按钮，把自绘的上下按钮叠在控件右侧 —— **Qt 因此不为按钮预留文本
空间**，而本项目这些控件普遍 `setFixedWidth(100~150)`。实测（微软雅黑，
`tools/_scratch/_probes/_probe_spinbox_width.py`）：

    宽度 110px：文本右缘 47px > 按钮左缘 39px  → 数字钻到箭头下面
    按钮区恒为 71px（up 31 + down 31 + spacing 5 + 右边距 4），文本左内边距 11px

字号还会在运行时被改（统一设置页 → `QApplication.setFont`），固定宽度不会跟着
变，所以这里按**当前字体**算「文本 + 间隙 + 按钮区」的宽度，并在收到
`QEvent.FontChange` 时重算。

用法：端口/多位数字微调框直接用 `FittedSpinBox(parent, sample="65535")`；
已有点位把 `SpinBox(...)` 换成它并**删掉原来的 `setFixedWidth(...)`**
（否则又会被按钮压住）。需要保留设计稿定宽时用 `refit(min_width=...)` 把它当下限。
"""
from PySide6.QtCore import QEvent
from qfluentwidgets import SpinBox

# 右侧按钮区实测宽度（px）：up 31 + down 31 + spacing 5 + 右边距 4
_BUTTON_ZONE = 71
# 文本左内边距（lineEdit.x() = 11）+ 文本与按钮之间的安全间隙
_TEXT_INSET = 11
_TEXT_GAP = 10


def spin_width_for(spin, sample: str = "65535") -> int:
    """按 `spin` 当前字体算出「文本 + 间隙 + 按钮区」所需宽度（px）"""
    fm = spin.fontMetrics()
    return (fm.horizontalAdvance(str(sample)) + _TEXT_INSET + _TEXT_GAP
            + _BUTTON_ZONE)


class FittedSpinBox(SpinBox):
    """宽度随字体自适应的 SpinBox（端口等多位数字场景）

    Args:
        sample: 该控件可能出现的最长文本（端口默认 "65535"；带后缀时传
            ``f"{最大值}{后缀}"``）。构造后若改了 range/suffix，可再调
            :meth:`refit` 重算。

    注意：类属性 `_fit_sample/_fit_min` 是**兜底值**——qfw `SpinBoxBase.__init__`
    里就调了 `setFont()`，会先触发一次 FontChange（此时实例属性还没赋值），
    没有类属性兜底就会在构造期抛 AttributeError。
    """

    _fit_sample: str = "65535"
    _fit_min: int = 0

    def __init__(self, parent=None, sample: str = "65535"):
        super().__init__(parent)
        self._fit_sample = str(sample)
        self._apply_fit()

    def set_fit_sample(self, sample: str):
        """更新用于计算宽度的样本文本并立即重算"""
        self._fit_sample = str(sample)
        self.refit()

    def refit(self, min_width: int = None) -> int:
        """重算宽度并落定；`min_width` 作为设计稿定宽下限（None 表示沿用上次）"""
        if min_width is not None:
            self._fit_min = int(min_width)
        return self._apply_fit()

    def _apply_fit(self) -> int:
        width = max(spin_width_for(self, self._fit_sample), self._fit_min)
        self.setFixedWidth(width)
        return width

    def changeEvent(self, event):
        # 字号可在运行时被统一设置页改（QApplication.setFont）→ 跟着重算，
        # 否则固定宽度会把数字重新压到按钮下面
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:
            self._apply_fit()
