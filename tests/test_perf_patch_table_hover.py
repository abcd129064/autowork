# -*- coding: utf-8 -*-
"""core.perf_patch_table_hover 回归（B1 patch 第三片）。"""
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("qfluentwidgets")

from core import perf_patch_table_hover as pth


def test_hover_patch_repaints_old_and_new_rows(monkeypatch):
    from qfluentwidgets.components.widgets.table_view import TableBase

    class FakeTableBase:
        _perf_hover_patched = False

        def _setHoverRow(self, row):
            raise AssertionError("original hover repaint should be replaced")

    monkeypatch.setattr(TableBase, "_perf_hover_patched", False, raising=False)
    monkeypatch.setattr("qfluentwidgets.components.widgets.table_view.TableBase",
                        FakeTableBase)
    pth.apply_table_hover_patch()
    assert FakeTableBase._perf_hover_patched is True

    class VP:
        def __init__(self):
            self.updates = []

        def width(self):
            return 100

        def update(self, *args):
            self.updates.append(args)

    class Delegate:
        hoverRow = 1

        def setHoverRow(self, row):
            self.hoverRow = row

    class Model:
        def rowCount(self):
            return 5

    obj = type("Obj", (), {})()
    obj.delegate = Delegate()
    obj.viewport = lambda: obj.vp
    obj.model = lambda: Model()
    obj.rowHeight = lambda r: 40
    obj.rowViewportPosition = lambda r: r * 40
    obj.vp = VP()

    FakeTableBase._setHoverRow(obj, 2)
    assert obj.delegate.hoverRow == 2
    assert obj.vp.updates == [(0, 40, 100, 40), (0, 80, 100, 40)]

    obj.vp.updates.clear()
    FakeTableBase._setHoverRow(obj, 2)
    assert obj.vp.updates == []


def test_hover_patch_is_idempotent(monkeypatch):
    from qfluentwidgets.components.widgets.table_view import TableBase

    class FakeTableBase:
        _perf_hover_patched = False

        def _setHoverRow(self, row):
            pass

    monkeypatch.setattr("qfluentwidgets.components.widgets.table_view.TableBase",
                        FakeTableBase)
    pth.apply_table_hover_patch()
    first = FakeTableBase._setHoverRow
    pth.apply_table_hover_patch()
    assert FakeTableBase._setHoverRow is first
