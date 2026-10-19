# -*- coding: utf-8 -*-
"""UIMixin 死代码删除后的边界锁定（G7/G9/G10/G12）。"""
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("qfluentwidgets")

from main_window.ui_mixin import UIMixin

_DEAD = (
    "_on_font_size", "_on_dpi_scale", "_on_font_family",
    "_on_open_port_fake", "_on_open_single_video",
    "_on_single_video_finished", "_on_single_video_failed",
    "_on_newlog_organize", "_on_newlog_dlg_destroyed",
    "_on_newlog_finished", "_on_newlog_failed",
    "_newlog_upload_target", "_start_newlog_upload",
    "_on_newlog_cancel_upload", "_reactivate_newlog_dlg",
    "_on_newlog_upload_progress", "_on_newlog_upload_percent",
    "_on_newlog_upload_done", "_on_newlog_upload_fail",
    "_on_newlog_upload_cancelled", "_on_perf_options",
    "_set_dark_titlebar", "_on_show_upload_list", "_on_add_table_record",
    "_on_open_settings", "_on_about",
)


def test_dead_methods_removed():
    remaining = [name for name in _DEAD if hasattr(UIMixin, name)]
    assert remaining == []


def test_live_helpers_kept():
    for name in ("_release_worker_safe", "_set_font_size_inline",
                 "_set_font_family_inline", "_set_dpi_scale_inline",
                 "_on_open_conn_diag", "_on_open_tool_hub"):
        assert hasattr(UIMixin, name), name
