# -*- coding: utf-8 -*-
"""ToDesk 开关并发语义冒烟：多设备并发 / 同方向拒绝 / 反向打断 / 回调归属路由"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import Qt, Signal, QThread  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
app = QApplication([])

import database.table_db as table_db  # noqa: E402
table_db.get_meta = lambda: (0, "")
table_db.get_submission_stats = lambda days=30: {"by_table": {}}
table_db.query_page = lambda *a, **k: (2, [
    {"name": "49-04", "roomName": "大师", "todesk_id": "111",
     "todesk_status": "1", "code": "DEV_A"},
    {"name": "49-05", "roomName": "大师", "todesk_id": "222",
     "todesk_status": "0", "code": "DEV_B"},
])
table_db.update_todesk_status = lambda dev, st: 1

import windows.management.table_page as tp  # noqa: E402
from windows.management.table_page import TablePage, TABLE_COLUMNS  # noqa: E402
from core.design_tokens import SEMANTIC  # noqa: E402


class FakeWorker(QThread):
    """真信号、不启动线程；属性与 TodeskToggleWorker 对齐便于路由验证"""
    sent_ok = Signal()
    confirmed = Signal(str)
    unconfirmed = Signal(str)
    error = Signal(str)

    def __init__(self, device_code, table_id, turn_on):
        super().__init__()
        self.device_code = device_code
        self.table_id = table_id
        self.turn_on = turn_on

    def isRunning(self):
        return True  # 池中恒视为运行中（覆盖拒绝/打断分支）

    def requestInterruption(self):
        self.interrupted = True

    def disconnect(self, recv):
        self.disconnected = True


p = TablePage()
p._populate(table_db.query_page()[1])
col = [i for i, (k, _, _) in enumerate(TABLE_COLUMNS) if k == "todesk_id"][0]
it_a = p._table.item(0, col)
it_b = p._table.item(1, col)

# ---- 1) 回调按 worker 归属路由：两个 worker 信号互不串扰 ----
wa = FakeWorker("DEV_A", "49-04", True)
wb = FakeWorker("DEV_B", "49-05", False)
p._todesk_ctx_map = {
    "DEV_A": {"row": 0, "old_status": "1", "turn_on": True, "name": "49-04"},
    "DEV_B": {"row": 1, "old_status": "0", "turn_on": False, "name": "49-05"},
}
# 连接方式与产品代码一致（闭包捕获 worker）
wa.sent_ok.connect(lambda ww=wa: p._on_todesk_sent(ww))
wa.confirmed.connect(lambda s, ww=wa: p._on_todesk_confirmed(s, ww))
wb.sent_ok.connect(lambda ww=wb: p._on_todesk_sent(ww))
wa.sent_ok.emit()   # A 开启指令受理 → row0 乐观置绿
assert it_a.foreground().color() == QColor(SEMANTIC["success"])
assert "已下发" in it_a.toolTip()
wb.sent_ok.emit()   # B 关闭指令受理 → row1 乐观置默认色
assert it_b.foreground().color() != QColor(SEMANTIC["success"])
# A confirmed 不影响 B
wa.confirmed.emit("1")
assert str(it_a.data(Qt.ItemDataRole.UserRole)) == "1"
assert str(it_b.data(Qt.ItemDataRole.UserRole)) == "0"
print("1) 回调归属路由 PASS")

# ---- 2) 同方向重复点击拒绝（不弹确认框直接 info bar return） ----
p._todesk_workers = {"DEV_A": wa}
orig_exec = tp.MessageBox.exec
tp.MessageBox.exec = lambda self: False
p._toggle_todesk(0)  # row0 DEV_A 当前 '1'，点关闭=反向 → 走打断分支
assert getattr(wa, "interrupted", False) and getattr(wa, "disconnected", False)
print("2) 反向指令打断旧轮询 PASS")

# ---- 3) 同方向拒绝：把 A 状态设为 '0'，再点（开启=反向？不，'0'→点开启，
# worker.turn_on=False（旧是关闭）→ 若点击反向会打断。构造同方向：worker turn_on=True，
# 单元格 '0' → 点击=开启 → turn_on True == worker.turn_on → 拒绝 ----
wa2 = FakeWorker("DEV_B", "49-05", True)  # 旧 worker 是开启方向
p._todesk_workers = {"DEV_B": wa2}
p._toggle_todesk(1)  # row1 状态 '0' → 点开启 → 同方向 → 拒绝
assert not getattr(wa2, "interrupted", False), "同方向不应打断"
print("3) 同方向重复点击拒绝 PASS")
tp.MessageBox.exec = orig_exec

# ---- 4) finished 清理逻辑（QThread 原生 finished 不能从 Python emit，
#         直调与产品连接处相同的闭包验证身份比较） ----
p._todesk_workers = {"DEV_A": wa}
cleanup = lambda w=wa, d="DEV_A": (
    p._todesk_workers.pop(d, None)
    if p._todesk_workers.get(d) is w else None)
cleanup()
assert "DEV_A" not in p._todesk_workers
# 身份保护：同设备的后继 worker 不被旧 worker 的 finished 误删
wa_old = FakeWorker("DEV_B", "x", True)
wa_new = FakeWorker("DEV_B", "x", False)
p._todesk_workers = {"DEV_B": wa_new}
cleanup_old = lambda w=wa_old, d="DEV_B": (
    p._todesk_workers.pop(d, None)
    if p._todesk_workers.get(d) is w else None)
cleanup_old()
assert p._todesk_workers.get("DEV_B") is wa_new
print("4) finished 清理（含身份保护） PASS")

print("CONCURRENCY SMOKE PASS")
