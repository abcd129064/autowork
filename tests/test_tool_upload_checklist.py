# -*- coding: utf-8 -*-
"""待上传清单勾选列改造回归（P0 2026-09-25，main_window/tool_hub.UploadListWork）

勾选列从「CheckBox+QWidget 容器 cellWidget」改为可勾选 QTableWidgetItem：
- 零子控件（cellWidget 数恒 0），重建只付 setItem 成本
- 单击翻转经 itemChanged → 表头三态回显（全选/部分/未选）
- 表头 clicked 接管语义保留：未全选→全选；已全选→全不选
- refresh() 重建期 blockSignals，防止逐行勾选放大成 O(n²)
- _checked_files / _update_sel_count 口径不变

隔离：tmp_path 伪造 videos_dir/upload；不起真实上传 worker，不弹窗。
"""
import sys
import time

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("qfluentwidgets")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from main_window.tool_hub import UploadListWork


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication(sys.argv[:1])


@pytest.fixture
def card(tmp_path, qapp):            # qapp 必须先于控件构造（无 app 段错误）
    root = tmp_path / "upload"
    root.mkdir()
    for name, size in (("a.mp4", 1024), ("b.log", 2048), ("c.json", 10)):
        (root / name).write_bytes(b"x" * size)

    class _Win:                      # 最小 win 桩：UploadListWork 只读 videos_dir
        videos_dir = str(tmp_path)

    c = UploadListWork(_Win())
    c.refresh()
    yield c, root
    c.deleteLater()


def test_rows_use_checkable_items_zero_cellwidgets(card):
    """勾选列必须是可勾选 item，且不再有 cellWidget 子控件"""
    c, _root = card
    assert c.table.rowCount() == 3
    for r in range(c.table.rowCount()):
        assert c.table.cellWidget(r, 0) is None
        it = c.table.item(r, 0)
        assert it is not None
        assert it.checkState() == Qt.CheckState.Unchecked
        assert it.flags() & Qt.ItemFlag.ItemIsUserCheckable
        assert not it.flags() & Qt.ItemFlag.ItemIsEditable
    assert c.chk_all.checkState() == Qt.CheckState.Unchecked


def test_click_toggles_and_header_tristate(card):
    """单击翻转 → itemChanged → 表头三态：部分勾选=半选，全选=勾满"""
    c, _root = card
    c.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    assert c.chk_all.checkState() == Qt.CheckState.PartiallyChecked
    c.table.item(1, 0).setCheckState(Qt.CheckState.Checked)
    c.table.item(2, 0).setCheckState(Qt.CheckState.Checked)
    assert c.chk_all.checkState() == Qt.CheckState.Checked
    assert "已选 3 个" in c.lbl_progress.text()


def test_header_clicked_select_all_then_clear(card):
    """表头单击接管语义：未全选→全选；已全选→全不选（半选仅回显）"""
    c, _root = card
    c.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    c._on_header_clicked()
    assert all(c._row_checked(r) for r in range(c.table.rowCount()))
    c._on_header_clicked()
    assert not any(c._row_checked(r) for r in range(c.table.rowCount()))
    assert c.chk_all.checkState() == Qt.CheckState.Unchecked


def test_checked_files_and_sel_count(card):
    """勾选收集与已选统计口径不变"""
    c, root = card
    c.table.item(1, 0).setCheckState(Qt.CheckState.Checked)
    assert c._checked_files() == [str(root / "b.log")]
    assert "已选 1 个" in c.lbl_progress.text()


def test_refresh_rebuilds_and_keeps_signals_suppressed(card):
    """重建清空勾选；重建期 blockSignals 后表头三态仍正确收敛"""
    c, root = card
    c.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    (root / "d.txt").write_bytes(b"y" * 5)
    c.refresh()
    assert c.table.rowCount() == 4
    assert not any(c._row_checked(r) for r in range(4))
    assert c.chk_all.checkState() == Qt.CheckState.Unchecked


def test_large_refresh_stays_fast(tmp_path, qapp):
    """P0 收益锁进回归：1000 行重建必须远快于 cellWidget 方案（原形态秒级）"""
    root = tmp_path / "upload"
    root.mkdir()
    for i in range(1000):
        (root / f"f{i:04}.log").write_bytes(b"x")

    class _Win:
        videos_dir = str(tmp_path)

    c = UploadListWork(_Win())
    t0 = time.perf_counter()
    c.refresh()
    dt_ms = (time.perf_counter() - t0) * 1000
    assert c.table.rowCount() == 1000
    assert dt_ms < 800, f"1000 行重建耗时 {dt_ms:.0f}ms，疑回退到 cellWidget 形态"
    c.deleteLater()
