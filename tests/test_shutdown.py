# -*- coding: utf-8 -*-
"""B4：主窗口关闭清理与 kd 反查 worker 的离线回归。"""
import sys

import pytest

pytest.importorskip("PySide6")

import core.frp_remote as frp_remote
from PySide6.QtCore import QProcess
from main_window import shutdown
from workers.kd_status_worker import _KdStatusQueryWorker


class _Window:
    def __init__(self, calls, name):
        self.calls = calls
        self.name = name
        self.closed = 0

    def close(self):
        self.closed += 1
        self.calls.append(self.name)


class _Worker:
    def __init__(self, calls, name, running=True):
        self.calls = calls
        self.name = name
        self.running = running
        self.waited = []
        self.interrupted = 0
        self.deleted = 0
        self.finished = type("_Sig", (), {"connect": lambda self, fn: setattr(self, "fn", fn)})()

    def isRunning(self):
        return self.running

    def requestInterruption(self):
        self.interrupted += 1

    def wait(self, ms):
        self.waited.append(ms)

    def deleteLater(self):
        self.deleted += 1


class _Proc:
    def __init__(self, calls, name):
        self.calls = calls
        self.name = name
        self.killed = 0

    def state(self):
        return 1  # != QProcess.NotRunning

    def kill(self):
        self.killed += 1
        self.calls.append(self.name)

    def waitForFinished(self, ms):
        self.calls.append((self.name, "wait", ms))


class _Hub:
    def __init__(self, calls, name):
        self.calls = calls
        self.name = name
        self.detached = 0

    def detach_workers(self):
        self.detached += 1
        self.calls.append(self.name)


class _SessionManager:
    def __init__(self, calls):
        self.calls = calls

    def shutdown(self):
        self.calls.append("session_shutdown")


def test_shutdown_main_window_order_and_cleanup(monkeypatch):
    calls = []
    monkeypatch.setattr(shutdown.QThread, "msleep", staticmethod(lambda ms: calls.append(("sleep", ms))))
    monkeypatch.setattr(frp_remote, "get_session_manager", lambda: _SessionManager(calls))

    host = type("Host", (), {})()
    host.management_hub = _Hub(calls, "detach_management")
    host.aftersale_hub = _Hub(calls, "detach_aftersale")
    host.ledger_hub = _Hub(calls, "detach_ledger")
    host._hub_popouts = {"a": _Window(calls, "popout")}
    host._table_panel = _Window(calls, "table_panel")
    host._save_remote_sessions = lambda: calls.append("save_sessions")
    host._remote_session_window = _Window(calls, "remote_close")
    host.running_process = _Proc(calls, "tracking_kill")
    host._tracking_process = _Proc(calls, "p_tracking")
    host._backend_process = _Proc(calls, "p_backend")
    host._front_process = _Proc(calls, "p_front")
    host._tcp_worker = _Worker(calls, "tcp")
    host._log_worker = _Worker(calls, "log")
    host._newlog_worker = _Worker(calls, "newlog")
    host._newlog_upload_worker = _Worker(calls, "upload")
    host._single_video_worker = _Worker(calls, "single")
    host._decode_process = _Proc(calls, "decode")
    host._upload_collect_workers = [_Worker(calls, "collect")]

    shutdown.shutdown_main_window(host)

    assert host.management_hub.detached == 1
    assert host.aftersale_hub.detached == 1
    assert host.ledger_hub.detached == 1
    assert calls.index("save_sessions") < calls.index("remote_close")
    assert "session_shutdown" in calls
    assert "tracking_kill" in calls
    assert all(getattr(host, name) is None for name in (
        "_table_panel", "_remote_session_window", "running_process",
        "_tracking_process", "_backend_process", "_front_process",
        "_tcp_worker", "_log_worker", "_newlog_worker",
        "_newlog_upload_worker", "_single_video_worker", "_decode_process"))
    assert host._upload_collect_workers == []
    assert host._tcp_worker is None
    assert host._log_worker is None
    assert ("sleep", 200) in calls


def test_kd_status_worker_emits_info_or_none(monkeypatch):
    from database import table_db

    got = []
    w = _KdStatusQueryWorker("dev", "2026-08-19", "base")
    w.done.connect(got.append)
    monkeypatch.setattr(table_db, "find_kd_file_status",
                        lambda d, day, base: {"d": d, "day": day, "base": base})
    w.run()
    assert got == [{"d": "dev", "day": "2026-08-19", "base": "base"}]

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(table_db, "find_kd_file_status", boom)
    got.clear()
    w.run()
    assert got == [None]
