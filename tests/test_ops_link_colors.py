# -*- coding: utf-8 -*-
"""ops_link_delegate 链接色语义回归（2026-09-25 用户截图报告）

ghost 色键曾把深浅主题索引取反：深色画 #333（深底隐身）、浅色画
#c8d0dc（白底看不清）。本测试锁死「深色=浅灰、浅色=深灰」语义，
与原按钮时代 windows/aftersale/common.py _row_btn_css 的 ghost 分支对齐。

纯函数测试，不构造控件，无需 QApplication。
"""
import pytest

pytest.importorskip("PySide6")

from core.ops_link_delegate import _link_color


def test_ghost_is_light_gray_on_dark_theme():
    """深色主题：ghost 链接必须用浅灰字（#c8d0dc）"""
    assert _link_color("ghost", dark=True).name().lower() == "#c8d0dc"


def test_ghost_is_dark_gray_on_light_theme():
    """浅色主题：ghost 链接必须用深灰字（#333333）"""
    assert _link_color("ghost", dark=False).name().lower() == "#333333"


def test_ghost_luminance_direction_guard():
    """亮度方向守卫：dark=True 的字色必须比 dark=False 亮得多——
    索引再取反时此断言立即失败（不依赖具体色值）"""
    def _lum(dark):
        c = _link_color("ghost", dark=dark)
        return 0.2126 * c.red() + 0.7152 * c.green() + 0.0722 * c.blue()

    assert _lum(True) > 140        # 深底需要亮字
    assert _lum(False) < 120       # 浅底需要暗字
