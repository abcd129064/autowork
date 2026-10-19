# -*- coding: utf-8 -*-
"""core.perf_settings 纯解析回归（B1 第一步）。"""
from core import perf_settings as ps


def test_defaults_are_copies():
    a = ps.default_global_settings()
    a["acrylic"] = False
    assert ps.default_global_settings()["acrylic"] is True


def test_resolve_new_keys():
    cfg = ps.resolve_global_settings({
        "perf_acrylic": False, "perf_animation": False,
        "perf_table_smooth": True, "perf_lean_delegate": False})
    assert cfg == {"acrylic": False, "animation": False,
                   "table_smooth": True, "lean_delegate": False}


def test_legacy_performance_mode_disables_acrylic_and_animation():
    cfg = ps.resolve_global_settings({"performance_mode": True})
    assert cfg["acrylic"] is False
    assert cfg["animation"] is False
    assert cfg["table_smooth"] is False
    assert cfg["lean_delegate"] is True


def test_lean_delegate_string_and_bad_payload():
    assert ps.resolve_global_settings({"perf_lean_delegate": "false"})["lean_delegate"] is False
    assert ps.resolve_global_settings({"perf_lean_delegate": "true"})["lean_delegate"] is True
    assert ps.resolve_global_settings(None) == ps.default_global_settings()
    assert ps.resolve_global_settings([]) == ps.default_global_settings()


def test_panel_overrides_and_effective_value():
    keys = {"aftersale": "k_a", "video": "k_v"}
    ov = ps.panel_overrides({"k_a": 1, "k_v": 0, "other": 1}, keys)
    assert ov == {"aftersale": True, "video": False}
    assert ps.effective_value(True, ov, "aftersale") is True
    assert ps.effective_value(True, ov, "video") is False
    assert ps.effective_value(True, ov, "management") is True
    assert ps.effective_value(False, None, "aftersale") is False
