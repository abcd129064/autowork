# -*- coding: utf-8 -*-
"""右键菜单与“添加到上传目录”下沉后的离线行为测试。

覆盖 G2/G3 从 main_window/ui_mixin.py 迁移到 main_window/ui/context_menus.py 后的
缓存菜单、异常提示、日志收集与文本复制分派。全部在 offscreen 下运行，不启动真实网络。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidget, QWidget

from main_window.ui import context_menus

_qapp = None


def _ensure_qapp():
    global _qapp
    _qapp = QApplication.instance() or QApplication(sys.argv[:1])
    return _qapp


@pytest.fixture(scope="module")
def qapp():
    return _ensure_qapp()


class _Ui:
    def __init__(self):
        self.id_list = QListWidget()
        self.log_list = QListWidget()
        self.local_video_list = QListWidget()


class _Host(QWidget):
    """只实现 context_menus 用到的 host 契约，避免构造真实 MainWindow。"""

    def __init__(self):
        super().__init__()
        self.ui = _Ui()
        self.videos_dir = ""
        self._current_log_path = ""
        self.logs = []
        self.bars = []
        self._status_messages = []

    def _append_log(self, msg):
        self.logs.append(msg)

    def _show_info_bar(self, *args, **kwargs):
        self.bars.append((args, kwargs))

    def _show_status_message(self, msg, timeout=0):
        self._status_messages.append((msg, timeout))

    def on_open_dir_clicked(self):
        pass

    def on_open_daily_clicked(self):
        pass

    def _get_selected_date_str(self):
        return ""


@pytest.fixture
def host(qapp):
    w = _Host()
    yield w
    w.deleteLater()


def test_init_context_menus_builds_three_cached_menus(host):
    context_menus.init_context_menus(host)

    assert host.ui.id_list.contextMenuPolicy() == Qt.CustomContextMenu
    assert host.ui.log_list.contextMenuPolicy() == Qt.CustomContextMenu
    assert host.ui.local_video_list.contextMenuPolicy() == Qt.CustomContextMenu

    assert host._ctx_id_menu.actions()[0].text() == "打开目录"
    assert host._ctx_log_menu.actions()[0].text() == "复制此行"
    assert host._ctx_video_menu.actions()[0].text() == "复制视频名"


def test_add_file_to_upload_rejects_missing_file(host):
    context_menus.add_file_to_upload(host, "")

    assert host.bars
    assert host.bars[0][0][0] == "文件不存在，无法添加到上传目录"
    assert host.logs and "文件不存在" in host.logs[0]


def test_add_file_to_upload_dispatches_text_copy(host, tmp_path, monkeypatch):
    host.videos_dir = str(tmp_path)
    host.ui.id_list.addItem("dev01")
    host.ui.id_list.setCurrentRow(0)
    os.makedirs(tmp_path / "dev01", exist_ok=True)
    src = tmp_path / "note.txt"
    src.write_text("hello", encoding="utf-8")

    created = []

    class _Signal:
        def __init__(self):
            self.slots = []

        def connect(self, slot):
            self.slots.append(slot)

    class _FakeCopyWorker:
        def __init__(self, source, dst):
            self.source = source
            self.dst = dst
            self.copy_finished = _Signal()
            self.error = _Signal()
            self.started = False
            created.append(self)

        def start(self):
            self.started = True

    monkeypatch.setattr(context_menus, "FileCopyWorker", _FakeCopyWorker)
    context_menus.add_file_to_upload(host, str(src))

    dst = tmp_path / "upload" / "dev01" / "note.txt"
    assert created and created[0].started
    assert created[0].source == str(src)
    assert created[0].dst == str(dst)
    assert dst.parent.is_dir()
    assert host._file_copy_workers == created


def test_add_file_to_upload_dispatches_log_collect(host, tmp_path, monkeypatch):
    host.videos_dir = str(tmp_path)
    host.ui.id_list.addItem("dev01")
    host.ui.id_list.setCurrentRow(0)
    os.makedirs(tmp_path / "dev01", exist_ok=True)
    log_path = tmp_path / "daily_001.log"
    log_path.write_text("log", encoding="utf-8")

    created = []

    class _Signal:
        def __init__(self):
            self.slots = []

        def connect(self, slot):
            self.slots.append(slot)

    class _FakeCollectWorker:
        def __init__(self, videos_dir, device_code, bases):
            self.args = (videos_dir, device_code, bases)
            self.done = _Signal()
            self.error = _Signal()
            self.started = False
            created.append(self)

        def start(self):
            self.started = True

    monkeypatch.setattr(context_menus, "CollectFilesWorker", _FakeCollectWorker)
    context_menus.add_file_to_upload(host, str(log_path))

    worker = created[0]
    assert worker.started
    assert worker.args == (str(tmp_path), "dev01", ["daily_001"])
    assert len(worker.done.slots) == 1
    assert host._upload_collect_workers == [worker]

    context_menus.on_upload_collect_done(
        host, "dev01", 2, ["missing.mp4"], worker)
    assert host._upload_collect_workers == []
    assert host.bars and "缺失" in host.bars[-1][0][0]
