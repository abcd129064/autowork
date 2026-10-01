# -*- coding: utf-8 -*-
"""S5 整格绘制缓存回归（2026-09-26，core/lean_table_delegate.py）

覆盖（docs/表格滚动延迟调查报告2026-09-25.md §三 S5，用户确认实施）：
- **逐字节等价**：无状态帧 + 全状态矩阵（hover/按压/选中/选中+hover/
  选中+按压 × plain/alternate 行），缓存命中（第二次绘制）vs 直绘路径
  （_paint_from_cache 强制 False 回退）像素逐字节一致；
- **勾选格容差**（唯一例外）：主题色勾选框笔刷 AA 边缘经透明 pm 预乘 +
  blit 两步舍入，与直绘单次融合存在 ±1/255 通道微差（隔离实验证实
  23/4560 像素，见 core/lean_table_delegate.py 回归点 7）——差异必须限于
  勾选框包围盒、逐通道 |Δ|≤1、总量 ≤2%；
- **缓存命中证明**：第二次绘制不新增条目、QPixmap 对象身份不变；
- **失效矩阵**：文本编辑 / 主题 / dpr / 列宽 / 行状态 / 背景角色 /
  勾选状态变化 → 新键；_invalidate 与 setCheckedColor 清空；
- **回退路径**：非 solid 背景刷（渐变）/ 半透明自定义底色不缓存不崩；
- **option.rect 变异语义**：两条路径 paint 后 rect 均为 margin 内矩形
  （OpsLeanDelegate 操作列链接几何依赖，回归点 4）。

状态并入缓存键而非 blit 后叠加填充（对 S5 原案的一处修正，见模块
docstring）：叠加会把 tint 压在文本/勾选框之上（绘制顺序与直绘不一致，
逐字节等价不成立）；状态变体键保持全状态等价 + 滚动帧纯 blit。
"""
import sys

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("qfluentwidgets")

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import (QBrush, QColor, QImage, QLinearGradient, QPainter,
                           QPixmap)
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem, QTableWidgetItem

import core.lean_table_delegate as ltd
from core.lean_table_delegate import LeanTableDelegate

_qapp = None

CELL_W, CELL_H = 120, 38
MARGIN = 2


@pytest.fixture(scope="module")
def qapp():
    global _qapp
    _qapp = QApplication.instance() or QApplication(sys.argv[:1])
    return _qapp


# ==================== 被测对象 ====================

@pytest.fixture
def env(qapp):
    """3×4 表格 + 内容矩阵（覆盖首/中/末列、交替行、勾选、彩色前景、
    右对齐、多行、长文本 elide、自定义背景、空文本）+ LeanTableDelegate"""
    from qfluentwidgets import TableWidget

    t = TableWidget()
    t.setAlternatingRowColors(True)   # TableBase 默认开，显式声明测试意图
    t.setColumnCount(3)
    t.setRowCount(4)

    def _item(r, c, text):
        it = QTableWidgetItem(text)
        t.setItem(r, c, it)
        return it

    _item(0, 0, "289-01")                     # 首列圆角 + 偶行 alternate
    _item(0, 1, "球房名称A")                    # 偶行中间列
    _item(0, 2, "在线")                         # 偶行末列圆角
    _item(1, 0, "289-02")                     # 奇行首列
    _item(1, 1, "时间\n填写人")                 # 多行（回归点 2）
    _item(2, 0, "击球点位偏移需要重新校准定位器水平仪")   # 长文本 elide（奇行）
    _item(2, 1, "右对齐文本")
    t.item(2, 1).setTextAlignment(
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    _item(2, 2, "彩色前景")
    t.item(2, 2).setData(Qt.ItemDataRole.ForegroundRole,
                        QBrush(QColor(200, 30, 30)))
    _item(3, 0, "自定义背景")                   # 偶行 + solid 背景刷
    t.item(3, 0).setData(Qt.ItemDataRole.BackgroundRole,
                         QBrush(QColor(0, 120, 215)))
    _item(3, 1, "")                            # 空文本
    _item(3, 2, "勾选格")
    t.item(3, 2).setFlags(t.item(3, 2).flags()
                          | Qt.ItemFlag.ItemIsUserCheckable)
    t.item(3, 2).setCheckState(Qt.CheckState.Checked)

    d = LeanTableDelegate(t)
    t.setItemDelegate(d)
    yield t, d
    t.deleteLater()


def _paint_cell(delegate, table, row, col, dpr=1.0):
    """离屏渲染一格：已知底色 backdrop + delegate.paint（option.rect=满格矩形）"""
    pm = QPixmap(int(CELL_W * dpr), int(CELL_H * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(QColor(64, 64, 64))
    p = QPainter(pm)
    opt = QStyleOptionViewItem()
    opt.rect = QRect(0, 0, CELL_W, CELL_H)
    delegate.paint(p, opt, table.model().index(row, col))
    p.end()
    return pm


class _DirectPatch:
    """临时禁用 S5 缓存路径（_paint_from_cache 恒回退直绘）

    实例属性遮蔽类方法；退出时删除实例属性回落到类实现。必须即进即出：
    若在禁用状态下调用 _cached，会比较出 direct vs direct 的假等价。
    """

    def __init__(self, delegate):
        self._d = delegate

    def __enter__(self):
        self._d._paint_from_cache = lambda *a, **k: False
        return self._d

    def __exit__(self, *exc):
        del self._d._paint_from_cache
        return False


def _paint_direct(delegate, table, row, col, dpr=1.0):
    """强制回退直绘路径（S2 时代实现），用于逐字节对比"""
    with _DirectPatch(delegate):
        return _paint_cell(delegate, table, row, col, dpr)


def _img_bytes(pm):
    img = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    return (img.width(), img.height(), bytes(img.constBits()))


def _cached(delegate, table, row, col, dpr=1.0, twice=True):
    """正常缓存路径绘制（默认画两次，返回第二次 = 命中帧）"""
    pm = _paint_cell(delegate, table, row, col, dpr)
    if twice:
        pm = _paint_cell(delegate, table, row, col, dpr)
    return pm


# ==================== 逐字节等价（无状态帧） ====================

# (行, 列, 说明) —— 覆盖首/中/末列、偶/奇行（alternate）、多行、elide、
# 右对齐、彩色前景、自定义背景、空文本。勾选格 (3,2) 不在此列：预乘
# alpha 双重舍入导致 ±1 通道微差，走专门容差断言（见下）。
_EQ_CELLS = [
    (0, 0, "first-col-rounded-alternate"),
    (0, 1, "middle-alternate"),
    (0, 2, "last-col-rounded-alternate"),
    (1, 0, "first-col-plain"),
    (1, 1, "multiline"),
    (2, 0, "elided-long-text"),
    (2, 1, "right-aligned"),
    (2, 2, "colored-foreground"),
    (3, 0, "custom-solid-background"),
    (3, 1, "empty-text"),
]


@pytest.mark.parametrize("row,col,name", _EQ_CELLS)
def test_cached_equals_direct_nonstate(env, monkeypatch, row, col, name):
    """无状态帧：缓存命中 vs 直绘，像素逐字节一致（S5 验收口径）"""
    t, d = env
    a = _img_bytes(_paint_direct(d, t, row, col))
    b = _img_bytes(_cached(d, t, row, col))
    assert a == b, f"cell ({row},{col}) {name}: cached != direct"


def test_checkbox_cell_premultiply_tolerance(env, monkeypatch):
    """勾选格：±1 通道容差比较（唯一含 CheckStateRole 的格子）

    机制（隔离实验证实，见 core/lean_table_delegate.py 回归点 7）：直绘把
    主题色 1px 笔刷圆角矩形的 AA 边缘一次融合合成到不透明视口（单次舍
    入）；缓存 = 透明 QPixmap 预乘量化 + blit 源叠加两步舍入 → 23/4560
    像素（0.5%）的 AA 边缘 ±1/255 通道微差。约束：差异必须限于勾选框
    包围盒（文本/背景/tint 区字节不变）、逐通道 |Δ|≤1、总量 ≤2%。
    含 hover 状态变体（tint 预乘数学精确，微差不随状态叠加放大）。
    """
    t, d = env
    row, col = 3, 2

    def _assert_premultiply_tolerance():
        a = _img_bytes(_paint_direct(d, t, row, col))
        b = _img_bytes(_cached(d, t, row, col))
        assert a[:2] == b[:2]
        da, db = a[2], b[2]
        w, h, bpp = a[0], a[1], 4
        diff_px = set()
        for i, (va, vb) in enumerate(zip(da, db)):
            if va != vb:
                assert abs(va - vb) <= 1, f"byte#{i} 通道差超过 1"
                px = i // bpp
                diff_px.add((px % w, px // w))
        for x, y in diff_px:
            # 勾选框包围盒（矩形 (15,8.5,19,19) + 1px 笔刷 + AA ≈ 1px 余量）
            assert 13 <= x <= 36 and 6 <= y <= 30, (
                f"差异越界 ({x},{y})：应限于勾选框包围盒")
        assert len(diff_px) <= 0.02 * w * h

    _assert_premultiply_tolerance()
    d.hoverRow = row
    try:
        _assert_premultiply_tolerance()
    finally:
        d.hoverRow = -1


# ==================== 状态矩阵：逐字节等价 + tint 生效 ====================

_STATES = [
    # (selected, hover, pressed)
    (False, True, False),
    (False, False, True),
    (True, False, False),
    (True, True, False),
    (True, False, True),
]


def _set_state(d, row, selected, hover, pressed):
    d.selectedRows = {row} if selected else set()
    d.hoverRow = row if hover else -1
    d.pressedRow = row if pressed else -1


@pytest.mark.parametrize("selected,hover,pressed", _STATES)
@pytest.mark.parametrize("row", [0, 1])   # 0=alternate 偶行, 1=plain 奇行
def test_cached_equals_direct_states(env, monkeypatch, selected, hover,
                                     pressed, row):
    """全状态矩阵：状态变体键渲染 vs 直绘，逐字节一致；且 tint 可见"""
    t, d = env
    col = 1  # 中间列（避开指示条/勾选，单一变量）
    base = _img_bytes(_cached(d, t, row, col))  # 无状态基线
    try:
        _set_state(d, row, selected, hover, pressed)
        a = _img_bytes(_paint_direct(d, t, row, col))
        b = _img_bytes(_cached(d, t, row, col))
        assert a == b, "状态帧：cached != direct"
        assert b != base, "状态 tint 未生效（与无状态帧无差异）"
    finally:
        _set_state(d, row, False, False, False)


def test_selected_indicator_per_frame(env, monkeypatch):
    """选中行首列指示条：依赖 pressedRow/hscroll，每帧直画，两路径一致"""
    t, d = env
    row, col = 1, 0
    d.selectedRows = {row}
    try:
        a = _img_bytes(_paint_direct(d, t, row, col))
        b = _img_bytes(_cached(d, t, row, col))
        assert a == b
        # 与未选中帧不同（指示条 + tint 生效）
        d.selectedRows = set()
        c = _img_bytes(_cached(d, t, row, col))
        assert b != c
    finally:
        d.selectedRows = set()


def test_custom_background_ignores_state(env, monkeypatch):
    """自定义背景刷不受行状态影响（直绘口径一致），状态不进键防冗余变体"""
    t, d = env
    row, col = 3, 0
    no_state = _img_bytes(_cached(d, t, row, col))
    n0 = len(d._cell_pm_cache)
    d.hoverRow = row
    try:
        hovered = _img_bytes(_cached(d, t, row, col))
        a = _img_bytes(_paint_direct(d, t, row, col))
        assert hovered == no_state      # 视觉不变
        assert hovered == a              # 且与直绘一致
        assert len(d._cell_pm_cache) == n0   # 状态未进键：无新变体
    finally:
        d.hoverRow = -1


# ==================== 缓存命中证明 ====================

def test_second_paint_is_cache_hit(env, monkeypatch):
    """第二次绘制不新增条目、QPixmap 对象身份不变（真命中而非重渲染）"""
    t, d = env
    row, col = 0, 1
    _paint_cell(d, t, row, col)
    before = list(d._cell_pm_cache.items())
    pm2 = _paint_cell(d, t, row, col)
    after = list(d._cell_pm_cache.items())
    assert len(before) == len(after) == 1
    assert before[0][1] is after[0][1]   # 同一 QPixmap 对象
    # 命中帧与首帧（渲染帧）也逐字节一致
    assert _img_bytes(pm2) == _img_bytes(_paint_direct(d, t,
                                                        row, col))


# ==================== 失效矩阵 ====================

def test_text_edit_invalidates(env, monkeypatch):
    t, d = env
    _cached(d, t, 0, 1)
    n0 = len(d._cell_pm_cache)
    t.item(0, 1).setText("新文本内容")
    _paint_cell(d, t, 0, 1)
    assert len(d._cell_pm_cache) == n0 + 1   # 新键


def test_theme_flag_in_key(env, monkeypatch):
    t, d = env
    _cached(d, t, 0, 1)
    n0 = len(d._cell_pm_cache)
    d._dark = True   # 模拟主题翻转（正式链路 qconfig.themeChanged→_invalidate）
    try:
        _paint_cell(d, t, 0, 1)
        assert len(d._cell_pm_cache) == n0 + 1
    finally:
        d._dark = None


def test_dpr_in_key(env, monkeypatch):
    t, d = env
    _cached(d, t, 0, 1, dpr=1.0)
    n0 = len(d._cell_pm_cache)
    _cached(d, t, 0, 1, dpr=2.0)
    assert len(d._cell_pm_cache) == n0 + 1   # 跨屏拖动 → dpr 变化 → 新键


def test_column_width_in_key(env, monkeypatch):
    t, d = env
    _cached(d, t, 0, 1)
    n0 = len(d._cell_pm_cache)
    t.setColumnWidth(1, 240)
    try:
        # 手动构造更宽的 option.rect 模拟列宽变化（h 在键里）
        pm = QPixmap(240, CELL_H)
        pm.fill(QColor(64, 64, 64))
        p = QPainter(pm)
        opt = QStyleOptionViewItem()
        opt.rect = QRect(0, 0, 240, CELL_H)
        d.paint(p, opt, t.model().index(0, 1))
        p.end()
        assert len(d._cell_pm_cache) == n0 + 1
    finally:
        t.setColumnWidth(1, 100)


def test_check_state_in_key(env, monkeypatch):
    t, d = env
    _cached(d, t, 3, 2)
    n0 = len(d._cell_pm_cache)
    t.item(3, 2).setCheckState(Qt.CheckState.Unchecked)
    try:
        _paint_cell(d, t, 3, 2)
        assert len(d._cell_pm_cache) == n0 + 1
    finally:
        t.item(3, 2).setCheckState(Qt.CheckState.Checked)


def test_background_role_in_key(env, monkeypatch):
    t, d = env
    _cached(d, t, 0, 1)
    n0 = len(d._cell_pm_cache)
    t.item(0, 1).setData(Qt.ItemDataRole.BackgroundRole,
                        QBrush(QColor(120, 0, 0)))
    try:
        _paint_cell(d, t, 0, 1)
        assert len(d._cell_pm_cache) == n0 + 1
    finally:
        t.item(0, 1).setData(Qt.ItemDataRole.BackgroundRole, None)


def test_invalidate_and_checked_color_clear(env, monkeypatch):
    t, d = env
    _cached(d, t, 0, 1)
    assert len(d._cell_pm_cache) > 0
    d._invalidate()
    assert len(d._cell_pm_cache) == 0
    _cached(d, t, 3, 2)   # 勾选格
    assert len(d._cell_pm_cache) > 0
    d.setCheckedColor("#ff0000", "#ff0000")
    assert len(d._cell_pm_cache) == 0


def test_capacity_overflow_clears(env, monkeypatch):
    t, d = env
    monkeypatch.setattr(ltd, "_CELL_PM_CACHE_MAX", 2)
    for row in range(4):
        _paint_cell(d, t, row, 1)
    # 上限 2：第 4 次插入前触发整体清空再插入 → 只剩最后 1 条
    assert len(d._cell_pm_cache) == 1


# ==================== 回退路径 ====================

def test_gradient_brush_falls_back_direct(env, monkeypatch):
    """非 solid 背景刷（渐变）：不缓存、不崩、视觉走直绘"""
    t, d = env
    grad = QLinearGradient(0, 0, CELL_W, 0)
    grad.setColorAt(0, QColor(255, 0, 0))
    grad.setColorAt(1, QColor(0, 0, 255))
    t.item(0, 1).setData(Qt.ItemDataRole.BackgroundRole, QBrush(grad))
    try:
        pm = _paint_cell(d, t, 0, 1)   # 不抛异常
        assert len(d._cell_pm_cache) == 0
        a = _img_bytes(_paint_direct(d, t, 0, 1))
        assert _img_bytes(pm) == a     # 与直绘一致（同一条代码路径）
    finally:
        t.item(0, 1).setData(Qt.ItemDataRole.BackgroundRole, None)


def test_semitransparent_background_falls_back_direct(env, monkeypatch):
    """半透明自定义底色（alpha∉{0,255}）：预乘两步舍入会引入 ±1（勾选
    框微差的同族机制），强制走直绘不缓存——保证所有缓存格字节等价"""
    t, d = env
    t.item(0, 1).setData(Qt.ItemDataRole.BackgroundRole,
                        QBrush(QColor(0, 120, 215, 128)))
    try:
        pm = _paint_cell(d, t, 0, 1)
        assert len(d._cell_pm_cache) == 0
        a = _img_bytes(_paint_direct(d, t, 0, 1))
        assert _img_bytes(pm) == a     # 与直绘一致（同一条代码路径）
    finally:
        t.item(0, 1).setData(Qt.ItemDataRole.BackgroundRole, None)


# ==================== option.rect 变异语义（回归点 4） ====================

def test_paint_mutates_option_rect_both_paths(env):
    """paint 后 option.rect 必须是 margin 内矩形——OpsLeanDelegate 在
    super().paint() 之后按内矩形画操作列链接，两条路径均不得破坏该语义"""
    t, d = env

    def _rect_after(delegate, force_direct):
        pm = QPixmap(CELL_W, CELL_H)
        p = QPainter(pm)
        opt = QStyleOptionViewItem()
        opt.rect = QRect(0, 0, CELL_W, CELL_H)
        if force_direct:
            with _DirectPatch(delegate):
                delegate.paint(p, opt, t.model().index(0, 1))
        else:
            delegate.paint(p, opt, t.model().index(0, 1))
        p.end()
        # opt.rect 是 C++ 侧内部 QRect 的引用，option 析构后悬空，必须拷贝
        return QRect(opt.rect)

    # 缓存命中帧
    _paint_cell(d, t, 0, 1)
    r1 = _rect_after(d, force_direct=False)
    # 直绘回退帧
    r2 = _rect_after(d, force_direct=True)
    expected = QRect(0, MARGIN, CELL_W, CELL_H - 2 * MARGIN)
    assert r1 == expected
    assert r2 == expected
