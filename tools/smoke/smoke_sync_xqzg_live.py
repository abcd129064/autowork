# -*- coding: utf-8 -*-
"""_sync_xqzg_live 挂接冒烟：确认以 file_path='' 构造 SnookerOmFetchWorker"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication([])

import database.table_db as table_db  # noqa: E402
table_db.get_meta = lambda: (0, "")
table_db.get_submission_stats = lambda days=30: {"by_table": {}}

import windows.management.table_page as tp  # noqa: E402
from PySide6.QtCore import QThread, Signal  # noqa: E402


class FakeW(QThread):
    called = None
    result_ready = Signal()
    error = Signal(str)

    def __init__(self, file_path="", **kw):
        super().__init__()
        FakeW.called = file_path

    def isRunning(self):
        return False

    def start(self):
        pass


orig = tp.SnookerOmFetchWorker
tp.SnookerOmFetchWorker = FakeW
p = tp.TablePage()
p._sync_xqzg_live()
assert FakeW.called == "", FakeW.called
assert p._xqzg_sync_worker is not None
tp.SnookerOmFetchWorker = orig
print("SYNC HOOK PASS")
