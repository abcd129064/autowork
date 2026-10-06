# -*- coding: utf-8 -*-
"""SFTP 未完成传输队列落盘 offscreen/无网冒烟（P2-2 步骤 1）

验证 SFTPPanel 与 core.transfer_queue 的接线：
  1. 内存里未完成的任务（排队中/传输中/已暂停）→ 落盘记录，且完成/失败的任务不进快照；
  2. 落盘记录不含密码等敏感字段；
  3. _flush_pending_queue 写入的是"按目标隔离"的结构，别的目标不受影响；
  4. shutdown() 必须先落盘再清空内存队列（关标签不丢任务）；
  5. 连接成功后 _offer_pending_restore：忽略 → 消费快照；恢复 → 重新入队（仍受并发闸门约束）；
  6. 记录指向的本地路径已不存在 → 跳过并记日志，不建空任务。

不联网、不起真实传输线程：注入的假 worker 不 start()，恢复路径把并发闸门设为 0
使任务只排队；临时文件只落在 tools/_scratch/ 下。

已知环境现象（与断言无关）：offscreen 平台下**构造本面板**后进程收尾会以
0xC0000005 结束（rc=-1073741819），所以判定以最后一行 SMOKE_OK / SMOKE_FAILED
为准。已用 tools/_scratch/_probes/remote_session/probe_sftp_exit_crash.py 排除两个嫌疑：
只建 QApplication 不建面板 → rc=0；建面板但不调 shutdown（也不起清理线程）→ 仍崩。

运行（需要 qfluentwidgets + PySide6 的解释器）：
    set QT_QPA_PLATFORM=offscreen
    C:\\Users\\shen_zhe\\miniconda3\\python.exe tools/smoke/smoke_sftp_queue_persist.py
"""
import os
import sys
import gc

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# ---- Qt DLL 引导（同 windows/aftersale_panel.py 顶部，规避 conda Qt 冲突） ----
import importlib.util as _iu                                        # noqa: E402
try:
    _spec = _iu.find_spec('PySide6')
    if _spec is not None:
        for _d in (list(_spec.submodule_search_locations or []) +
                   [os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                                 'System32')]):
            if _d and os.path.isdir(_d):
                try:
                    os.add_dll_directory(_d)
                except OSError:
                    pass
except Exception:
    pass

from PySide6.QtWidgets import QApplication                     # noqa: E402

from core import app_settings                                  # noqa: E402
from core import transfer_queue as tq                          # noqa: E402
from windows.remote_session import sftp_window as sw           # noqa: E402

_SCRATCH = os.path.join(PROJECT_ROOT, "tools", "_scratch", "_smoke_sftp_queue")
os.makedirs(_SCRATCH, exist_ok=True)
_LOCAL = os.path.join(_SCRATCH, "payload.bin")
with open(_LOCAL, "wb") as f:
    f.write(b"x" * 1024)
_MISSING = os.path.join(_SCRATCH, "gone.bin")
if os.path.exists(_MISSING):
    os.remove(_MISSING)

_HOST, _PORT = "203.0.113.9", 22
_OTHER = ("198.51.100.7", 2222)
_LOGS = []
_STORE = {}          # 打桩后的设置存储（含其他键，验证只动队列键）

_PASS = 0
_FAIL = 0


def _check(label, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [通过] {label}")
    else:
        _FAIL += 1
        print(f"  [失败] {label} {detail}")


def _logs_contain(needle):
    return any(needle in (m or "") for m in _LOGS)


# ---- 打桩：设置读写只碰内存；MessageBox 结果可控 ----
app_settings.get = lambda k, d=None: _STORE.get(k, d)
app_settings.set = lambda k, v: _STORE.__setitem__(k, v)


class _FakeWorker:
    """只提供 _safe_delete_transfer_worker / shutdown 需要的接口，绝不 start"""

    def __init__(self):
        self.stopped = False

    def isRunning(self):
        return False

    def deleteLater(self):
        pass

    def stop(self):
        self.stopped = True

    def pause(self):
        pass

    def resume(self):
        pass


class _FakeBox:
    """替换 qfluentwidgets.MessageBox：记录弹窗次数，返回预设结果"""

    answer = True
    calls = []

    def __init__(self, title, text, parent=None):
        self.title, self.text = title, text
        _FakeBox.calls.append((title, text))
        from PySide6.QtWidgets import QPushButton
        self.yesButton, self.cancelButton = QPushButton(), QPushButton()

    def exec(self):
        return 1 if _FakeBox.answer else 0


sw.MessageBox = _FakeBox

app = QApplication.instance() or QApplication(sys.argv)
panel = sw.SFTPPanel(_HOST, _PORT, "root", "pw", log_callback=_LOGS.append)
panel._pending_save_timer.stop()          # 冒烟里手动 flush，不依赖事件循环


def _inject(tid, op, state, local, remote, name="", row=None):
    """注入一条"内存任务"（等价于 _start_transfer_op 建的行 + info 快照）"""
    if row is None:
        row = panel._transfer_table.rowCount()
        panel._transfer_table.insertRow(row)
        from PySide6.QtWidgets import QTableWidgetItem
        item = QTableWidgetItem(name or f"上传: {os.path.basename(local)}")
        item.setData(0x0100, tid)          # Qt.ItemDataRole.UserRole
        panel._transfer_table.setItem(row, 0, item)
        panel._transfer_table.setItem(row, 3, QTableWidgetItem(state))
    panel._transfer_workers[tid] = {
        'worker': _FakeWorker(), 'row': row, 'state': state,
        'last_bytes': 0, 'last_time': 0.0, 'speed': 0.0, 'start_time': 0.0,
        'params': ((_HOST, _PORT, "root", "pw"), op, local, remote),
    }


print("## 1. 未完成任务落盘")
_inject(1, "upload", "queued", _LOCAL, "/srv/payload.bin", name="上传: payload.bin")
_inject(2, "download", "running", _LOCAL, "/srv/other.bin", name="下载: other.bin")
_inject(3, "upload", "paused", _LOCAL, "/srv/paused.bin", name="上传: paused.bin")
_inject(4, "upload", "done", _LOCAL, "/srv/done.bin", name="上传: done.bin")
_inject(5, "upload", "failed", _LOCAL, "/srv/failed.bin", name="上传: failed.bin")
records = panel._pending_records()
_check("只有 queued/running/paused 进快照", len(records) == 3 and
       [r['op'] for r in records] == ['upload', 'download', 'upload'],
       f"→ {[r['op'] for r in records]}")
_check("完成/失败任务不进快照", all('/srv/done.bin' != r['remote_path']
                                and '/srv/failed.bin' != r['remote_path'] for r in records))
_check("记录字段齐全且无敏感字段",
       set(records[0].keys()) == {'op', 'name', 'local_path', 'remote_path', 'size'}
       and 'pw' not in repr(records), f"→ {records[0]}")
panel._flush_pending_queue()
key = tq.target_key(_HOST, _PORT)
_check("落盘到本目标键下", key in _STORE.get(tq.QUEUE_KEY, {}),
       f"→ {list(_STORE.get(tq.QUEUE_KEY, {}).keys())}")
_check("落盘顺序 = 入队顺序", [r['remote_path'] for r in _STORE[tq.QUEUE_KEY][key]] ==
       ['/srv/payload.bin', '/srv/other.bin', '/srv/paused.bin'])
_check("队列文件不含密码明文", 'pw' not in repr(_STORE[tq.QUEUE_KEY]))

print("## 2. 按目标隔离（别的目标不被覆盖）")
other_store = tq.save_target(_STORE[tq.QUEUE_KEY], _OTHER[0], _OTHER[1],
                             [tq.build_record('upload', _LOCAL, '/x/a.bin')])
_STORE[tq.QUEUE_KEY] = other_store
panel._flush_pending_queue()
_check("本目标重写后仍保留 3 条", len(tq.load_target(_STORE[tq.QUEUE_KEY], _HOST, _PORT)) == 3)
_check("别的目标记录不受影响", len(tq.load_target(_STORE[tq.QUEUE_KEY], *_OTHER)) == 1)

print("## 3. shutdown 先落盘再清空")
before = len(tq.load_target(_STORE[tq.QUEUE_KEY], _HOST, _PORT))
panel._pending_records()          # 触发一次快照构建（内容应与 shutdown 落盘一致）
panel.shutdown()
after = tq.load_target(_STORE[tq.QUEUE_KEY], _HOST, _PORT)
_check("shutdown 后队列仍在（关标签不丢）", after and before == 3, f"→ {len(after)} 条")
_check("shutdown 后内存队列已清空", not panel._transfer_workers)
try:
    panel.shutdown()
    _check("shutdown 幂等（重复调用不抛）", True)
except Exception as exc:                     # noqa: BLE001 - 冒烟脚本要看到具体异常
    _check("shutdown 幂等（重复调用不抛）", False, f"→ {exc!r}")

print("## 4. 恢复提示：忽略")
panel2 = sw.SFTPPanel(_HOST, _PORT, "root", "pw", log_callback=_LOGS.append)
panel2._pending_save_timer.stop()
tq_store = tq.save_target(_STORE.get(tq.QUEUE_KEY, {}), _HOST, _PORT, records)
_STORE[tq.QUEUE_KEY] = tq_store
_FakeBox.answer = False
_FakeBox.calls.clear()
panel2._offer_pending_restore()
_check("弹出一次恢复询问", len(_FakeBox.calls) == 1 and _FakeBox.calls[0][0] == '恢复未完成的传输',
       f"→ {_FakeBox.calls}")
_check("询问文案含任务摘要", '3 个未完成的传输任务' in _FakeBox.calls[0][1])
_check("选择忽略 → 快照被消费", tq.load_target(_STORE[tq.QUEUE_KEY], _HOST, _PORT) == [])
_check("选择忽略 → 不建新任务", not panel2._transfer_workers)
_check("选择忽略 → 有日志", _logs_contain('已忽略上次未完成的传输'))
panel2._offer_pending_restore()
_check("同一面板不再重复询问", len(_FakeBox.calls) == 1)

print("## 5. 恢复提示：恢复（经并发闸门只排队，不起线程）")
panel3 = sw.SFTPPanel(_HOST, _PORT, "root", "pw", log_callback=_LOGS.append)
panel3._pending_save_timer.stop()
panel3._max_concurrent_transfers = 0        # 闸门占满 → 只排队，绝不 start
records_gone = list(records) + [tq.build_record('upload', _MISSING, '/srv/gone.bin')]
_STORE[tq.QUEUE_KEY] = tq.save_target(_STORE.get(tq.QUEUE_KEY, {}), _HOST, _PORT, records_gone)
_FakeBox.answer = True
_FakeBox.calls.clear()
panel3._offer_pending_restore()
_check("弹出恢复询问", len(_FakeBox.calls) == 1)
_check("3 个任务重新入队（第 4 条本地缺失被跳过）", len(panel3._transfer_workers) == 3,
       f"→ {len(panel3._transfer_workers)}")
_check("恢复的任务处于排队态", all(i['state'] == 'queued'
                              for i in panel3._transfer_workers.values()))
_check("恢复的任务在原表格里可见", panel3._transfer_table.rowCount() == 3)
_check("缺失本地路径 → 记日志跳过", _logs_contain('跳过已不存在的本地路径'))
_check("恢复后有日志汇总", _logs_contain('已恢复 3 个未完成的传输任务'))
panel3._flush_pending_queue()
_check("恢复的任务重新落盘（下次还能接着问）",
       len(tq.load_target(_STORE[tq.QUEUE_KEY], _HOST, _PORT)) == 3)

panel3.shutdown()
panel.shutdown()
# 关掉控件树再退出：offscreen 平台下未 close 的 QWidget 在进程收尾时偶发
# 0xC0000005（同 smoke_remote_ssh_creds.py 的处理），close+processEvents 后消失
for _w in (panel3, panel2, panel):
    try:
        _w.close()
        _w.deleteLater()
    except Exception:
        pass
app.processEvents()
gc.collect()

print(f"SMOKE_RESULT queue_persist passed={_PASS} failed={_FAIL}")
if _FAIL:
    print("SMOKE_FAILED")
    os._exit(1)
print("SMOKE_OK")
sys.stdout.flush()
os._exit(0)          # 规避 offscreen 退出时 0xC0000005（同其他冒烟脚本）
