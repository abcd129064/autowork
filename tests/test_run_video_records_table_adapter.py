# -*- coding: utf-8 -*-
r"""跑视频记录页表格适配层（windows/run_video/records/table_adapter.py）单测。

锁定拆分前 _populate（原 records.py L870-933）的呈现规则：列顺序、分类徽章
（语义色前景 + alpha=26 同色底 + 加粗居中）、描述 60 字 / 复现 30 字截断与
tooltip、操作列 links、假值兜底。本层只产出 CellSpec，不建控件，所以只需
QColor（无需 QApplication 窗口），跑法：

    $env:QT_QPA_PLATFORM='offscreen'
    $env:PYTHONPATH='tools/_scratch/qfw_env'   # 让 E:\ANACONDA 看到 qfluentwidgets
    E:\ANACONDA\python.exe -m pytest tests/test_run_video_records_table_adapter.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("qfluentwidgets")

from PySide6.QtGui import QColor  # noqa: E402

from windows.run_video.common import (  # noqa: E402
    TABLE_COLUMNS, _OPS_LINKS, _category_color)
from windows.run_video.records.query_state import short_dt  # noqa: E402
from windows.run_video.records.table_adapter import (  # noqa: E402
    CellSpec, FONT_BOLD, FONT_NORMAL, cell_for, data_cells)

# 一行业务上完整的记录：覆盖 11 列里除 ops 外的全部数据键
_REC = {
    "created_at": "2026-08-22 21:14:33",
    "category": "问题",
    "kind": "遮挡问题",
    "room_name": "甲球房",
    "video_name": "20260822.mp4",
    "frame": 120,
    "description": "贴球后识别延迟",
    "repro": "否",
    "new_program": "否",
    "signer": "沈喆",
}

_KEYS = [key for key, _header, _width in TABLE_COLUMNS]


def test_font_constants_match_page_contract():
    assert FONT_NORMAL == ""
    assert FONT_BOLD == "bold"


def test_cellspec_defaults_and_slots():
    spec = CellSpec()
    assert spec.text == ""
    assert spec.tooltip == ""
    assert spec.font == FONT_NORMAL
    assert spec.fg is None
    assert spec.bg is None
    assert spec.align_center is False
    assert spec.links is None
    assert not hasattr(spec, "__dict__"), "CellSpec 必须用 __slots__，不挂动态属性"


def test_cellspec_keeps_keyword_arguments():
    fg = QColor("#2f7fd9")
    spec = CellSpec(text="x", tooltip="全量", font=FONT_BOLD, fg=fg,
                    bg=QColor(fg), align_center=True, links=_OPS_LINKS)
    assert (spec.text, spec.tooltip, spec.font) == ("x", "全量", FONT_BOLD)
    assert spec.fg is fg
    assert spec.align_center is True
    assert spec.links == _OPS_LINKS


def test_column_order_is_exactly_table_columns():
    assert _KEYS == ["created_at", "category", "kind", "room_name",
                     "video_name", "frame", "description", "repro",
                     "new_program", "signer", "ops"]
    cells = data_cells(_REC)
    assert len(cells) == 11 == len(TABLE_COLUMNS)
    assert all(isinstance(c, CellSpec) for c in cells)


def test_full_row_text_follows_original_populate():
    cells = data_cells(_REC)
    assert [c.text for c in cells] == [
        short_dt("2026-08-22 21:14:33"),   # created_at 走 short_dt
        "问题",
        "遮挡问题",
        "甲球房",
        "20260822.mp4",
        "120",                             # frame 数值转文本
        "贴球后识别延迟",
        "否",
        "否",
        "沈喆",
        "",                                # ops 空文本
    ]


def test_only_ops_cell_carries_links():
    cells = data_cells(_REC)
    assert cells[10].links is _OPS_LINKS
    assert [c for c in cells[:10] if c.links is not None] == []


def test_created_at_uses_short_dt_not_raw_timestamp():
    full_ts = {"created_at": "2026-08-22 21:14:33"}
    assert cell_for("created_at", full_ts).text == "08-22 21:14"
    assert cell_for("created_at",
                    {"created_at": "2026-08-22 21:14"}).text == "08-22 21:14"
    assert cell_for("created_at",
                    {"created_at": "2026-08-22"}).text == "2026-08-22"
    assert cell_for("created_at", {"created_at": None}).text == ""
    assert cell_for("created_at", {}).text == ""


@pytest.mark.parametrize("category, hex_rgb", [
    ("问题", "#cf4452"),
    ("未复现", "#d97f2f"),
    ("精度", "#2f7fd9"),
    ("使用", "#2fae63"),
])
def test_category_badge_uses_semantic_color(category, hex_rgb):
    spec = cell_for("category", {"category": category})
    assert spec.text == category
    assert spec.font == FONT_BOLD
    assert spec.align_center is True
    assert isinstance(spec.fg, QColor)
    assert spec.fg.name() == hex_rgb
    assert spec.fg.alpha() == 255, "前景必须不透明"


@pytest.mark.parametrize("category", ["问题", "未复现", "精度", "使用"])
def test_category_badge_background_is_same_color_at_alpha_26(category):
    spec = cell_for("category", {"category": category})
    assert isinstance(spec.bg, QColor)
    assert spec.bg.name() == spec.fg.name(), "底色取同色，只降透明度"
    assert spec.bg.alpha() == 26
    assert spec.bg.alphaF() == pytest.approx(26 / 255.0)


def test_category_color_is_copied_so_foreground_stays_opaque():
    spec = cell_for("category", {"category": "问题"})
    assert spec.fg is not spec.bg, "底色必须是前景副本，否则改 alpha 会连带改前景"
    assert spec.fg.alpha() == 255


def test_unknown_category_falls_back_to_neutral_color():
    spec = cell_for("category", {"category": "临时分类"})
    assert spec.fg.name() == _category_color("临时分类").name()
    assert spec.bg.name() == spec.fg.name()
    assert spec.bg.alpha() == 26


def test_blank_category_renders_empty_text_with_neutral_color():
    spec = cell_for("category", {"category": None})
    assert spec.text == ""
    assert spec.bg.name() == spec.fg.name()


def test_description_over_60_chars_is_clipped_with_tooltip():
    full = "描" * 61
    spec = cell_for("description", {"description": full})
    assert spec.text == "描" * 60 + "…"
    assert len(spec.text) == 61
    assert spec.tooltip == full


def test_description_exactly_60_chars_is_not_clipped():
    full = "描" * 60
    spec = cell_for("description", {"description": full})
    assert spec.text == full
    assert spec.tooltip == ""


def test_description_keeps_59_chars_untouched():
    full = "描" * 59
    spec = cell_for("description", {"description": full})
    assert spec.text == full
    assert spec.tooltip == ""


def test_repro_over_30_chars_is_clipped_with_tooltip():
    full = "复" * 31
    spec = cell_for("repro", {"repro": full})
    assert spec.text == "复" * 30 + "…"
    assert len(spec.text) == 31
    assert spec.tooltip == full


def test_repro_exactly_30_chars_is_not_clipped():
    full = "复" * 30
    spec = cell_for("repro", {"repro": full})
    assert spec.text == full
    assert spec.tooltip == ""


def test_description_and_repro_have_independent_limits():
    rec = {"description": "复" * 31, "repro": "描" * 61}
    desc = cell_for("description", rec)
    repro = cell_for("repro", rec)
    assert desc.text == "复" * 31, "描述 31 字不该截断（阈值 60）"
    assert repro.text == "描" * 30 + "…", "复现 61 字按 30 字阈值截断"


def test_ops_cell_is_empty_text_plus_links():
    spec = cell_for("ops", _REC)
    assert spec.text == ""
    assert spec.tooltip == ""
    assert spec.links == (("编辑", "primary", "edit"),
                          ("删除", "danger", "delete"))
    assert spec.links is _OPS_LINKS
    assert spec.fg is None and spec.bg is None
    assert spec.align_center is False


@pytest.mark.parametrize("key, value, expected", [
    ("kind", "遮挡问题", "遮挡问题"),
    ("room_name", "甲球房", "甲球房"),
    ("video_name", "a.mp4", "a.mp4"),
    ("frame", 120, "120"),
    ("frame", "120", "120"),
    ("new_program", "是", "是"),
    ("signer", "沈喆", "沈喆"),
    ("signer", None, ""),
    ("signer", "", ""),
    ("kind", 0, ""),          # 契约：str(rec.get(key) or "")，假值渲染空串
])
def test_plain_columns_stringify_with_blank_fallback(key, value, expected):
    spec = cell_for(key, {key: value})
    assert spec.text == expected
    assert spec.tooltip == ""
    assert spec.links is None


def test_empty_record_yields_blank_cells_for_every_column():
    cells = data_cells({})
    assert len(cells) == 11
    assert all(isinstance(c, CellSpec) for c in cells)
    assert [c for c in cells if c.text] == []
    for c in cells:
        assert c.tooltip == ""


def test_data_cells_does_not_mutate_record():
    rec = dict(_REC)
    snapshot = dict(rec)
    data_cells(rec)
    assert rec == snapshot


def test_data_cells_builds_fresh_specs_each_call():
    first = data_cells(_REC)
    second = data_cells(_REC)
    assert first is not second
    assert all(a is not b for a, b in zip(first, second))
    # 只有 ops 的 links 是共享的列定义元组，其余单元格互不影响
    assert first[10].links is second[10].links
