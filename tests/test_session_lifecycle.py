# -*- coding: utf-8 -*-
"""异步链路生命周期回归（2026-11-10 0xC0000409 崩溃修复）。

覆盖：QThread 保活函数幂等性、InfoBar 按 key 复用、frps 忙时回执、
预检挂起超时兜底、ToDesk 回调代次守卫、按设备编码定位行、Transport 超时保活。
这些点都是「线程对象被提前释放」或「一次操作弹多条提示/永久静默」的根因，
纯离线隔离，不碰真机与网络。
"""

import os
import time
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402


@pytest.fixture
def qapp():
    """本仓无全局 conftest qapp，需要构造控件/处理 queued 信号的用例自行依赖。"""
    app = QApplication.instance() or QApplication([])
    yield app


# ---------------- QThread 保活 ----------------

def test_safe_release_finished_worker_not_leaked():
    """已结束的 worker 直接 deleteLater，不能挂进集合等永不触发的 finished。"""
    from windows.remote_session import sftp_window

    w = MagicMock()
    w.isFinished.return_value = True
    before = len(sftp_window._pending_workers)
    sftp_window._safe_release_worker(w)
    w.deleteLater.assert_called_once()
    assert len(sftp_window._pending_workers) == before


def test_safe_release_started_worker_cleans_on_finish(qapp):
    """start 前挂保活，finished 回执后集合自动摘除，全程持有强引用。"""
    from windows.remote_session import sftp_window

    class _Quick(QThread):
        def run(self):
            pass

    w = _Quick()
    sftp_window._safe_release_worker(w)
    assert w in sftp_window._pending_workers
    w.start()
    w.wait(2000)
    for _ in range(20):
        QApplication.processEvents()
        time.sleep(0.01)
    assert w not in sftp_window._pending_workers


def test_safe_release_none_is_noop():
    from windows.remote_session import ssh_terminal

    before = len(ssh_terminal._pending_workers)
    ssh_terminal._safe_release_worker(None)
    assert len(ssh_terminal._pending_workers) == before


# ---------------- InfoBar 按 key 复用 ----------------

def test_show_info_bar_key_updates_in_place(qapp):
    """同一次操作的多个阶段只保留一条提示，文案就地更新。"""
    from core import utils

    parent = QWidget()
    key = "ut:lifecycle:infobar"
    utils._keyed_bars.pop(key, None)
    try:
        first = utils.show_info_bar("受理中", "info", title="阶段一",
                                    parent=parent, duration=5000, key=key)
        second = utils.show_info_bar("已确认", "success", title="阶段二",
                                     parent=parent, key=key)
        assert second is first
        assert second.contentLabel.text() == "已确认"
        assert second.titleLabel.text() == "阶段二"
        assert utils._keyed_bars[key] is first
    finally:
        utils._keyed_bars.pop(key, None)
        parent.deleteLater()


def test_show_info_bar_without_key_keeps_original_behavior(qapp):
    """不传 key 时每次都新建，保证既有调用点行为不变。"""
    from core import utils

    parent = QWidget()
    a = utils.show_info_bar("甲", "info", parent=parent, duration=-1)
    b = utils.show_info_bar("乙", "info", parent=parent, duration=-1)
    assert a is not b
    parent.deleteLater()


# ---------------- frps 回执 / 预检兜底 ----------------

def test_request_refresh_busy_still_emits_receipt(qapp):
    """后台忙时不能静默 return，等待 refresh_finished 的打开请求会永久挂起。"""
    from core.frps_admin import get_frps_client

    c = get_frps_client()
    hits = []
    slot = hits.append
    c.refresh_finished.connect(slot)
    old_busy = c._bg_busy
    c._bg_busy = True
    try:
        c.request_refresh()
    finally:
        c._bg_busy = old_busy
        try:
            c.refresh_finished.disconnect(slot)
        except (TypeError, RuntimeError):
            pass
    assert len(hits) == 1


def test_preflight_timeout_resume_is_generation_guarded(monkeypatch):
    """旧代次定时器不得重复续接；当前代次且仍挂起才 fail-open。"""
    import core.frps_admin
    from core.frp_remote import RemoteSessionManager

    # 让断连走 mock，避免 PySide6 对 MagicMock 槽报 disconnect 警告
    monkeypatch.setattr(core.frps_admin, "get_frps_client", lambda: MagicMock())
    m = MagicMock()
    m._pending_seq = 7
    m._pending_open = ("ssh", "SNK", "T1", "src", None)
    RemoteSessionManager._resume_pending_on_timeout(m, 6)
    assert m._open_after_preflight.call_count == 0
    assert m._pending_open is not None

    RemoteSessionManager._resume_pending_on_timeout(m, 7)
    assert m._open_after_preflight.call_count == 1
    assert m._pending_open is None


# ---------------- ToDesk 回调守卫 ----------------

def test_todesk_ctx_for_rejects_stale_worker():
    """被新指令取代的旧 worker 返回 None，late 信号不再弹提示/写反状态。"""
    from windows.management.table_page import TablePage

    p = MagicMock()
    p._todesk_ctx_map = {"D1": {"device_code": "D1", "seq": 2}}
    w = MagicMock()
    w.device_code = "D1"
    w._todesk_seq = 1
    assert TablePage._todesk_ctx_for(p, w) is None
    w._todesk_seq = 2
    assert TablePage._todesk_ctx_for(p, w)["seq"] == 2
    w.device_code = "DX"
    assert TablePage._todesk_ctx_for(p, w) == {}


def test_row_of_todesk_resolves_by_device_code():
    """翻页/同步后行号失效，回调必须按设备编码重新定位。"""
    from windows.management.table_page import TablePage

    p = MagicMock()
    p._table.rowCount.return_value = 3
    target = MagicMock()
    target.data.return_value = "D2"
    p._table.item.side_effect = lambda r, c: target if r == 2 else None
    assert TablePage._row_of_todesk(p, "D2", 5) == 2
    assert TablePage._row_of_todesk(p, "MISS", 5) == -1
    assert TablePage._row_of_todesk(p, 1, 5) == 1


def test_cleanup_todesk_worker_removes_ctx_by_generation():
    """只清同代次上下文，旧 worker 的 finished 不能删掉新操作的 ctx。"""
    from windows.management.table_page import TablePage

    p = MagicMock()
    w = MagicMock()
    p._todesk_workers = {"D1": w}
    p._todesk_ctx_map = {"D1": {"device_code": "D1", "seq": 3}}
    TablePage._cleanup_todesk_worker(p, w, "D1", 2)
    assert "D1" not in p._todesk_workers
    assert "D1" in p._todesk_ctx_map

    p._todesk_workers = {"D1": w}
    TablePage._cleanup_todesk_worker(p, w, "D1", 3)
    assert "D1" not in p._todesk_ctx_map


# ---------------- Transport 超时保活 ----------------

def test_safe_close_transport_keeps_active_until_inactive():
    """join 超时且线程仍活着时保留强引用，inactive 后惰性回收。"""
    from core import utils

    t = MagicMock()
    t.is_active.return_value = True
    utils._retired_transports.discard(t)
    utils.safe_close_transport(t, join_timeout=0)
    t.close.assert_called_once()
    assert t in utils._retired_transports

    t.is_active.return_value = False
    utils._reap_retired_transports()
    assert t not in utils._retired_transports
