# -*- coding: utf-8 -*-
"""SpinBox 数字被上下按钮遮挡回归（2026-10-04 用户截图报告）

现象：远程页「TCP 直连」卡的「端口」固定宽 110px，数字被右侧上下箭头压住
（用户截图：只看见一半数字）。

根因：qfluentwidgets 的 `SpinBox`（`InlineSpinBoxBase`）用
`setButtonSymbols(NoButtons)` 关掉 Qt 原生按钮、把自绘按钮叠在控件右侧 ——
**Qt 因此不给文本预留按钮空间**。实测（微软雅黑，见
`tools/_scratch/_probes/_probe_spinbox_width.py`）：按钮区恒 71px
（up 31 + down 31 + spacing 5 + 右边距 4），文本左内边距 11px ⇒
宽度必须 ≥ 文本宽 + 92，而项目里普遍 `setFixedWidth(100~150)`，
字号一大（统一设置页可运行时 `QApplication.setFont`）就遮挡。

修复：`core/spin_fit_patch.FittedSpinBox` 按当前字体算宽度并在 FontChange 时重算。

本文件守住：
1. `spin_width_for` 随字号增长、随样本文本变长；
2. 各字号下文本右缘严格早于按钮左缘（含最长文本 65535）；
3. 运行时改字号（FontChange）后宽度自动重算；
4. `refit(min_width=...)` 保留设计稿定宽下限；
5. 报告点（远程页 TCP 端口 / 本地端口）确实换成自适应控件且未被 setFixedWidth 覆盖。
"""
import ast
import sys
from pathlib import Path

import pytest
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QApplication

from core.spin_fit_patch import FittedSpinBox, spin_width_for

_ROOT = Path(__file__).resolve().parents[1]
_SIZES = (9, 11, 14, 20)


@pytest.fixture
def qapp():
    yield QApplication.instance() or QApplication(sys.argv[:1])


def _lay_out(sb, qapp):
    sb.show()
    qapp.processEvents()
    return sb


def test_spin_width_grows_with_font(qapp):
    sb = FittedSpinBox()
    widths = []
    for pt in _SIZES:
        sb.setFont(QFont("Microsoft YaHei", pt))
        widths.append(sb.width())
    assert widths == sorted(widths), widths
    assert widths[-1] > widths[0], widths


def test_spin_width_grows_with_sample(qapp):
    narrow = FittedSpinBox(sample="365")
    wide = FittedSpinBox(sample="65535")
    assert wide.width() > narrow.width()
    assert spin_width_for(wide, "65535") > spin_width_for(wide, "365")


@pytest.mark.parametrize("pt", _SIZES)
def test_text_never_under_buttons(qapp, pt):
    """核心不变量：文本右缘（按最长文本算）+ 安全间隙 ≤ 按钮左缘"""
    sb = FittedSpinBox(sample="65535")
    sb.setRange(1, 65535)
    sb.setValue(9897)
    sb.setFont(QFont("Microsoft YaHei", pt))
    try:
        _lay_out(sb, qapp)
        fm = QFontMetrics(sb.font())
        text_right = sb.lineEdit().x() + fm.horizontalAdvance("65535")
        button_left = sb.upButton.x()
        assert text_right + 4 <= button_left, (
            f"{pt}pt: 文本右缘 {text_right} 侵入按钮区（按钮左缘 {button_left}，"
            f"控件宽 {sb.width()}）")
    finally:
        sb.close()
        sb.deleteLater()


def test_font_change_refits(qapp):
    """运行时改字号（统一设置页 → QApplication.setFont）必须跟着重算"""
    sb = FittedSpinBox()
    sb.setFont(QFont("Microsoft YaHei", 9))
    small = sb.width()
    sb.setFont(QFont("Microsoft YaHei", 20))
    assert sb.width() > small, (small, sb.width())
    sb.setFont(QFont("Microsoft YaHei", 9))
    assert sb.width() == small


def test_refit_respects_design_floor(qapp):
    sb = FittedSpinBox(sample="20 pt")
    sb.setFont(QFont("Microsoft YaHei", 9))
    fitted = sb.refit()
    assert sb.width() == fitted
    assert sb.refit(min_width=fitted + 40) == fitted + 40
    assert sb.refit() == fitted + 40          # 下限被记住
    assert sb.refit(min_width=10) == fitted   # 低于需要值时不缩回


def test_remote_port_fields_use_fitted_spinbox():
    """报告点守护：TCP 端口 / 本地端口必须是 FittedSpinBox 且无 setFixedWidth 覆盖"""
    src = (_ROOT / "windows" / "remote_session" / "remote_hub.py").read_text(
        encoding="utf-8")
    assigned = {}
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if (isinstance(target, ast.Attribute)
                and target.attr in ("tcp_port", "spin_port")
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)):
            assigned[target.attr] = node.value.func.id
    assert assigned.get("tcp_port") == "FittedSpinBox", assigned
    assert assigned.get("spin_port") == "FittedSpinBox", assigned
    assert "tcp_port.setFixedWidth(" not in src
