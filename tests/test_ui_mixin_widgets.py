# -*- coding: utf-8 -*-
r"""UIMixin 模块级控件的离线基线测试（P0，拆分前先锁行为）。

覆盖：
- AboutDialog 链接富文本拼装（纯静态，不实例化）；
- NewLogDialog 四态状态机（idle/organizing/organized/uploading）与按钮分发；
- _ShortcutKeyEdit 录制/清空/忽略单独修饰键。

运行需 qfluentwidgets + PySide6 与 offscreen 平台（与仓库既有 GUI 单测一致）：
    set QT_QPA_PLATFORM=offscreen
    E:\ANACONDA\python.exe -m pytest tests/test_ui_mixin_widgets.py -q
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import QApplication, QWidget

from main_window.ui_mixin import (
    ABOUT_LINKS,
    ABOUT_OSS_LIBS,
    AboutDialog,
    NewLogDialog,
    UIMixin,
    _ShortcutKeyEdit,
)

_qapp = None


def _ensure_qapp():
    global _qapp
    _qapp = QApplication.instance() or QApplication(sys.argv[:1])
    return _qapp


@pytest.fixture(scope="module")
def qapp():
    return _ensure_qapp()


@pytest.fixture
def host(qapp):
    w = QWidget()
    yield w
    w.deleteLater()


# ==================== AboutDialog ====================

def test_links_html_renders_anchor_per_item():
    html = AboutDialog._links_html([("源码", "https://example.com/a"),
                                    ("反馈", "https://example.com/b")])
    assert '<a href="https://example.com/a">源码</a>' in html
    assert '<a href="https://example.com/b">反馈</a>' in html
    assert "&nbsp;&nbsp;|&nbsp;&nbsp;" in html


def test_about_link_tables_have_license_entries():
    assert any("GPL" in title for title, _ in ABOUT_LINKS)
    assert any("Fluent-Widgets" in title for title, _ in ABOUT_OSS_LIBS)


def test_ui_mixin_theme_helpers_delegate_to_policy(monkeypatch):
    assert UIMixin._parse_theme_color({"theme_color": "#112233"}) == "#112233"
    assert UIMixin._get_theme_mode({"theme_mode": "light"}) == "light"
    monkeypatch.setattr(UIMixin, "_system_is_dark", staticmethod(lambda: True))
    assert UIMixin._effective_is_dark({"theme_mode": "auto"}) is True


# ==================== NewLogDialog 状态机 ====================

def test_newlog_dialog_idle_dispatches_start(host):
    dlg = NewLogDialog(host, "张三")
    calls = []
    dlg.on_start = lambda: calls.append("start")
    # 构造器保持 qfw 默认 "OK"，由调用方 _on_newlog_organize 改成业务文案
    assert dlg._phase == "idle"
    dlg.yesButton.setText("开始整理")
    dlg._on_yes_clicked()
    assert calls == ["start"]
    dlg.deleteLater()


def test_newlog_dialog_organizing_blocks_close(host):
    dlg = NewLogDialog(host, "张三")
    dlg.enter_running()
    assert dlg._phase == "organizing"
    assert dlg.yesButton.text() == "整理中..."
    assert not dlg.yesButton.isEnabled()
    assert not dlg.cancelButton.isEnabled()
    ev = type("_CloseEvent", (), {"ignored": False,
                                  "ignore": lambda self: setattr(self, "ignored", True)})()
    dlg.closeEvent(ev)
    assert ev.ignored is True
    dlg.deleteLater()


def test_newlog_dialog_organized_then_upload(host):
    dlg = NewLogDialog(host, "张三")
    calls = []
    dlg.on_upload = lambda: calls.append("upload")
    dlg.on_cancel_upload = lambda: calls.append("cancel")
    dlg.enter_organized(r"C:\tmp\out")
    assert dlg._phase == "organized"
    assert dlg.yesButton.text() == "打包上传"
    assert dlg._out_path.endswith("out")
    assert not dlg.btn_open_out.isHidden()
    dlg._on_yes_clicked()
    assert calls == ["upload"]

    dlg.enter_uploading()
    assert dlg._phase == "uploading"
    assert dlg.yesButton.text() == "取消上传"
    assert dlg.btn_min.isEnabled()
    assert not dlg.progress_bar.isHidden()
    dlg._on_yes_clicked()
    assert calls == ["upload", "cancel"]
    dlg.deleteLater()


def test_newlog_dialog_failed_returns_idle(host):
    dlg = NewLogDialog(host, "张三")
    dlg.enter_running()
    dlg.enter_failed()
    assert dlg._phase == "idle"
    assert dlg.yesButton.text() == "开始整理"
    assert dlg.target_edit.isEnabled()
    assert dlg.cancelButton.isEnabled()
    dlg.deleteLater()


# ==================== _ShortcutKeyEdit ====================

def test_shortcut_edit_initial_sequence(host):
    edit = _ShortcutKeyEdit("Ctrl+S", host)
    assert edit.keySequence().toString() == QKeySequence("Ctrl+S").toString()
    assert edit.isReadOnly()
    edit.deleteLater()


def test_shortcut_edit_delete_clears(host):
    edit = _ShortcutKeyEdit("Ctrl+S", host)
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier)
    edit.keyPressEvent(ev)
    assert edit.keySequence().isEmpty()
    assert edit.text() == ""
    edit.deleteLater()


def test_shortcut_edit_ignores_modifier_only(host):
    edit = _ShortcutKeyEdit("Ctrl+S", host)
    before = edit.keySequence().toString()
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Control, Qt.ControlModifier)
    edit.keyPressEvent(ev)
    assert edit.keySequence().toString() == before
    edit.deleteLater()


def test_shortcut_edit_records_combination(host):
    edit = _ShortcutKeyEdit("", host)
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_K, Qt.ControlModifier)
    edit.keyPressEvent(ev)
    assert edit.keySequence().toString() == QKeySequence("Ctrl+K").toString()
    assert edit.text() == QKeySequence("Ctrl+K").toString()
    edit.deleteLater()
