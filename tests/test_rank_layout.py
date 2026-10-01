# -*- coding: utf-8 -*-
"""售后排行页左图表/右表格错位回归（2026-10-01 用户截图报告）

现象：排行卡内左侧条形图被下推，「球房售后排行」标题与图表/表格之间
出现大块空白；TOP 50→10 切换后错位更夸张（图表被推到卡片中部）。

根因（offscreen 几何探针实证）：
1. qfw TableWidget 的 sizeHint 虚高（10 行数据 hint≈400px），把 body
   单元格撑高；QHBoxLayout 对受固定高度限制的子项（_chart_scroll
   setFixedHeight）默认垂直居中（QLayoutPrivate::alignedRect 兜底
   AlignCenter）→ 图表下移 (hint-chart_h)/2。
2. 表格自身高度不跟内容收缩：TOP 50 时撑到 1600，切回 TOP 10 后残留
   1600 → 单元格超高，图表居中到 y≈676。

修复：body 两个子项显式 AlignTop + 新增 _sync_table_height()（表头 +
行数×行高，1600 封顶，与 _sync_chart_height 对称），三处调用点接线。
"""
import inspect
import sys

import pytest

from PySide6.QtWidgets import QApplication

from windows.aftersale.rank import RankPage, _RankHBarChart
from windows.aftersale.common import _FIXED_ROW_HEIGHT


@pytest.fixture
def qapp():
    yield QApplication.instance() or QApplication(sys.argv[:1])


def _make_rows(n):
    return [
        {"rank": i + 1, "name": f"球房{i+1}", "table_no": "",
         "room_name": f"球房{i+1}", "total": 100 - i, "share": 3,
         "unresolved": i % 3, "our_problem": i % 2, "initiative": 1,
         "last_occurred": "2026-09-30"}
        for i in range(n)
    ]


@pytest.fixture
def page(qapp):
    """排行页（offscreen；qapp 必须先于控件构造，否则 C++ 段错误）"""
    p = RankPage()
    p.resize(1000, 650)
    p.show()
    qapp.processEvents()
    yield p
    p.hide()
    p.deleteLater()


# ---- 行为锁：顶对齐 + 高度自适应 -------------------------------------------

def test_chart_and_table_top_aligned(page, qapp):
    """10 行数据：图表与表格必须同一 y（此前图表被垂直居中下推 31px+）"""
    page._on_loaded({"rows": _make_rows(10), "summary": {}})
    qapp.processEvents()
    assert page._chart_scroll.y() == page._table.y(), (
        "左图表与右表格 y 不齐——body 布局回退为垂直居中（AlignTop 丢失）")


def test_table_height_follows_rows(page, qapp):
    """表格高度必须跟行数走（此前 sizeHint 虚高 10 行≈400px 撑出底部空白）"""
    page._on_loaded({"rows": _make_rows(10), "summary": {}})
    qapp.processEvents()
    hdr = page._table.horizontalHeader()
    expect = hdr.height() + 10 * _FIXED_ROW_HEIGHT + 4
    assert abs(page._table.height() - expect) <= 2, (
        f"表格高度 {page._table.height()} ≠ 内容高 {expect}（sizeHint 虚高回归）")


def test_no_stale_height_after_top50_to_top10(page, qapp):
    """TOP 50→10 切换：表格不得残留 1600 高（此前图表被居中推到 y≈676）"""
    page._on_loaded({"rows": _make_rows(50), "summary": {}})
    qapp.processEvents()
    page._on_loaded({"rows": _make_rows(10), "summary": {}})
    qapp.processEvents()
    assert page._table.height() < 600, (
        f"TOP50→10 后表格高度残留 {page._table.height()}")
    assert page._chart_scroll.y() == page._table.y()


def test_empty_state_heights(page, qapp):
    """空态：图表与表格同为最小高度 280"""
    page._on_loaded({"rows": [], "summary": {}})
    qapp.processEvents()
    assert page._chart_scroll.height() == 280
    assert page._table.height() == 280


# ---- 源码锁：防回退 ---------------------------------------------------------

def test_body_widgets_have_aligntop():
    """body 两个子项必须显式 AlignTop（缺失即回退垂直居中错位）"""
    src = inspect.getsource(RankPage._init_ui)
    assert "body.addWidget(self._chart_scroll, 2, Qt.AlignmentFlag.AlignTop)" in src
    assert "body.addWidget(self._table, 3, Qt.AlignmentFlag.AlignTop)" in src


def test_on_loaded_calls_sync_table_height():
    """_on_loaded 必须调用 _sync_table_height（表格高度跟行数收缩的挂点）"""
    src = inspect.getsource(RankPage._on_loaded)
    assert "_sync_table_height()" in src
