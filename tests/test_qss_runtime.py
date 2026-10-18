# -*- coding: utf-8 -*-
"""main_window.ui.qss_runtime 的回归：挂载幂等、工具栏高度强制、QSS 加载容错。"""
import types

import pytest

from main_window.ui import qss_runtime


class _FakeWidget:
    def __init__(self):
        self.heights = []

    def setFixedHeight(self, value):
        self.heights.append(value)


def test_apply_business_qss_idempotent(monkeypatch):
    calls = []
    win = types.SimpleNamespace(stackedWidget=object())
    monkeypatch.setattr(qss_runtime, "load_qss", lambda name: name + "-qss")
    monkeypatch.setattr(qss_runtime, "setCustomStyleSheet",
                        lambda widget, light, dark: calls.append((widget, light, dark)))

    assert qss_runtime.apply_business_qss(win) is True
    assert win._business_qss_applied is True
    assert calls == [(win.stackedWidget, "light-qss", "dark-qss")]
    assert qss_runtime.apply_business_qss(win) is False
    assert len(calls) == 1


def test_enforce_toolbar_radio_height_registers_three_frames(monkeypatch):
    calls = []
    ui = types.SimpleNamespace(input_frame_before=object(),
                               input_frame_set=object(),
                               input_frame_custom=object())
    monkeypatch.setattr(qss_runtime, "setCustomStyleSheet",
                        lambda widget, light, dark: calls.append((widget, light, dark)))
    qss_runtime.enforce_toolbar_radio_height(ui)
    assert len(calls) == 3
    assert all("min-height: 32px" in light for _, light, _ in calls)


def test_enforce_toolbar_button_height_ignores_missing_and_errors():
    good = _FakeWidget()

    class Bad:
        def setFixedHeight(self, value):
            raise RuntimeError("boom")

    ui = types.SimpleNamespace(write_table=good, start=Bad(), unknown=object())
    qss_runtime.enforce_toolbar_button_height(ui)
    assert good.heights == [32]


def test_load_qss_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(qss_runtime, "get_resource_dir", lambda: str(tmp_path))
    assert qss_runtime.load_qss("not-exist") == ""


def test_load_qss_reads_file(tmp_path, monkeypatch):
    styles = tmp_path / "styles"
    styles.mkdir()
    (styles / "light.qss").write_text("QWidget { color: red; }", encoding="utf-8")
    monkeypatch.setattr(qss_runtime, "get_resource_dir", lambda: str(tmp_path))
    assert "color: red" in qss_runtime.load_qss("light")
