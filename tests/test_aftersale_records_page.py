# -*- coding: utf-8 -*-
"""记录与统计页（RecordsPage）offscreen 回归测试（P0 基线）

覆盖（改造前先锁行为，再拆包重构，保证逐条不变）：
- 首屏加载链：周期选项 → 数据 worker → 表格/分页/统计/概览卡/数据源标签
- 关键词筛选（查询代际守卫下的重查）
- 分页：60 行/页、页脚文案、翻页按钮
- 操作列「已解决」链接：写库 + 静默重查 + 行状态翻转
- 勾选批量条：计数文案、全选、批量标记已解决（确认框打桩）
- 表头排序：排序键下推 SQL，翻页后仍是全量顺序；行号入口按 id 锚反查
- 自动刷新指纹门：指纹不变不重查、变化才重查

隔离方式同 tests/test_aftersale_rank_page.py：tmp SQLite + tmp app_dir +
monkeypatch，UI 在 offscreen 平台跑，不依赖显示器与真实网络。
"""
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("qfluentwidgets")

import core.app_paths
import core.app_settings as fas
import database.backend as backend
import database.table_db as table_db
import database.aftersale_db as adb
from database import schema
from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtWidgets import QApplication

from windows.aftersale.common import TABLE_COLUMNS, _COL_CHECK
from windows.aftersale.records import RecordsPage

_qapp = None


def _ensure_qapp():
    global _qapp
    _qapp = QApplication.instance() or QApplication(sys.argv[:1])
    return _qapp


@pytest.fixture(scope="module")
def qapp():
    return _ensure_qapp()


@pytest.fixture
def workdir():
    """独立临时目录。

    不用 pytest 内置 tmp_path：它在 Windows 上以 0o700 建目录，本项目的
    沙箱/ACL 环境会因此拒绝后续访问（pytest 自身清理也会崩）。这里改用
    默认权限的 os.makedirs，隔离语义等价（每个测试一个全新目录）。
    """
    d = os.path.join(tempfile.gettempdir(), "autowork_rec_%s" % uuid.uuid4().hex[:8])
    os.makedirs(d)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def db(monkeypatch, workdir):
    """临时库 + 临时配置目录（settings 隔离，绝不碰生产 MySQL）"""
    path = os.path.join(workdir, "t.db")
    monkeypatch.setattr(table_db, "DB_PATH", path)
    monkeypatch.setattr(table_db, "_conn", None)
    monkeypatch.setattr(table_db, "_ensure_initialized", lambda c: None)
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)
    monkeypatch.setattr(core.app_paths, "get_app_dir", lambda: workdir)
    fas.invalidate_cache()
    sl = sqlite3.connect(path)
    sl.executescript(schema.to_sqlite_ddl("aftersale_records"))
    sl.executescript(schema.to_sqlite_ddl("billiard_tables"))
    sl.commit()
    sl.close()
    try:
        yield path
    finally:
        fas.invalidate_cache()
        # 先关连接，否则 Windows 上 workdir 里的 t.db 删不掉
        table_db.close()


def _seed(n=3, **overrides):
    """造数：n 条今日记录，问题字段 p0..p(n-1)；resolved 默认否"""
    today = datetime.now().strftime("%Y-%m-%d")
    ids = []
    for i in range(n):
        rec = {"occurred_at": today, "room_name": "甲球房",
               "table_no": "01", "problem": "p%d" % i, "resolved": "否"}
        rec.update(overrides)
        ids.append(adb.insert_record(rec))
    return ids


def _seed_problems(n):
    """造数：问题字段定宽 p00..p(n-1)，避免字典序把 p10 排到 p2 前"""
    today = datetime.now().strftime("%Y-%m-%d")
    ids = []
    for i in range(n):
        ids.append(adb.insert_record({
            "occurred_at": today, "room_name": "甲球房",
            "table_no": "01", "problem": "p%02d" % i, "resolved": "否"}))
    return ids


def _wait(page, cond, timeout_ms=6000):
    """事件循环等待条件成立（异步 worker 链完成后断言）"""
    loop = QEventLoop()
    timer = QTimer(loop)
    timer.setInterval(30)
    state = {"ok": False}

    def _check():
        if cond():
            state["ok"] = True
            loop.quit()

    timer.timeout.connect(_check)
    guard = QTimer(loop)
    guard.setSingleShot(True)
    guard.timeout.connect(loop.quit)
    timer.start()
    guard.start(timeout_ms)
    _check()
    if not state["ok"]:
        loop.exec()
    timer.stop()
    return state["ok"]


def _col(key):
    """数据键 → 表格列号"""
    for i, (k, _h, _w) in enumerate(TABLE_COLUMNS):
        if k == key:
            return _COL_CHECK + 1 + i
    raise AssertionError("unknown column: %s" % key)


def _patch_messagebox(monkeypatch, page, accepted=True):
    """把页面模块里的 MessageBox 打桩（翻页/批量确认不弹真窗）"""
    mod = sys.modules[type(page).__module__]

    class _Box(object):
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return accepted

    monkeypatch.setattr(mod, "MessageBox", _Box, raising=False)


def _stop_workers(page):
    """停掉页面持有的所有 QThread（等价 pivot_page.detach_workers 的收尾）"""
    holders = [page]
    ctrl = getattr(page, "_controller", None)
    if ctrl is not None:
        holders.append(ctrl)
    for holder in holders:
        for attr in dir(holder):
            if not attr.endswith("_worker"):
                continue
            worker = getattr(holder, attr, None)
            if worker is None or not hasattr(worker, "requestInterruption"):
                continue
            try:
                worker.requestInterruption()
                worker.wait(3000)
            except RuntimeError:
                pass


def _close_page(page):
    """关页并回收线程，避免 QThread destroyed while running 崩溃"""
    page.hide()
    _stop_workers(page)
    qapp = QApplication.instance()
    if qapp is not None:
        qapp.processEvents()
    page.deleteLater()
    if qapp is not None:
        qapp.processEvents()


def _open_page(qapp, db):
    page = RecordsPage()
    page.show()
    return page


def test_records_page_first_load(qapp, db):
    _seed(3)
    page = _open_page(qapp, db)
    try:
        assert _wait(page, lambda: page._table.rowCount() == 3)
        # 默认 ORDER BY id DESC
        assert [r["problem"] for r in page._rows] == ["p2", "p1", "p0"]
        # 分页 / 统计 / 数据源 / 周期标题
        assert page._lbl_page.text() == "1/1 · 共 3 条"
        assert page._lbl_stats.text() == "共 3 条"
        assert page._lbl_source.text() == "数据源: 本地 SQLite"
        assert page._lbl_cycle.text().startswith("周期:")
        assert page._lbl_overview.text().startswith("概览 ·")
        # 概览四卡
        assert page._card_total[2].text() == "3"
        assert page._card_unresolved[2].text() == "3"
        assert page._card_initiative[2].text() == "0%"
        assert page._card_our_problem[2].text() == "100%"
        # 表格单元格与数据同序
        assert page._table.item(0, _col("problem")).text() == "p2"
        # 勾选列锚 = 记录 id（排序/批量依赖的隐式契约）
        assert page._table.item(0, _COL_CHECK).data(
            Qt.ItemDataRole.UserRole) == page._rows[0]["id"]
    finally:
        _close_page(page)


def test_records_page_keyword_filter(qapp, db):
    _seed(3)
    page = _open_page(qapp, db)
    try:
        assert _wait(page, lambda: page._table.rowCount() == 3)
        page._search_edit.setText("p1")
        page._on_filter_changed()
        assert _wait(page, lambda: page._table.rowCount() == 1)
        assert page._rows[0]["problem"] == "p1"
        assert page._lbl_stats.text() == "共 1 条"
        # 清空关键词恢复全量
        page._search_edit.setText("")
        page._on_filter_changed()
        assert _wait(page, lambda: page._table.rowCount() == 3)
    finally:
        _close_page(page)


def test_records_page_pagination(qapp, db):
    _seed(65)
    page = _open_page(qapp, db)
    try:
        assert _wait(page, lambda: page._table.rowCount() == 60)
        assert page._lbl_page.text() == "1/2 · 共 65 条"
        assert page._btn_prev.isEnabled() is False
        assert page._btn_next.isEnabled() is True
        page._step_page(1)
        assert _wait(page, lambda: page._table.rowCount() == 5)
        assert page._lbl_page.text() == "2/2 · 共 65 条"
        assert page._btn_prev.isEnabled() is True
        assert page._btn_next.isEnabled() is False
    finally:
        _close_page(page)


def test_records_page_ops_link_resolve(qapp, db):
    _seed(1)
    page = _open_page(qapp, db)
    try:
        assert _wait(page, lambda: page._table.rowCount() == 1)
        assert page._rows[0]["resolved"] == "否"
        page._on_ops_link(0, _col("ops"), "resolve")
        # 写库 + 静默重查后行状态翻转
        assert _wait(page, lambda: page._rows and page._rows[0]["resolved"] == "是")
        _total, rows = adb.query_page(1, 10)
        assert rows[0]["resolved"] == "是"
    finally:
        _close_page(page)


def test_records_page_batch_bar_and_resolve(qapp, db, monkeypatch):
    _seed(3)
    page = _open_page(qapp, db)
    _patch_messagebox(monkeypatch, page)
    try:
        assert _wait(page, lambda: page._table.rowCount() == 3)
        assert page._batch_bar.isVisible() is False
        # 勾选 2 行 → 批量条出现并计数
        for r in (0, 1):
            page._table.item(r, _COL_CHECK).setCheckState(
                Qt.CheckState.Checked)
        assert page._batch_bar.isVisible() is True
        assert page._lbl_selected.text() == "已选 2 项"
        assert page._chk_all.checkState() == Qt.CheckState.Unchecked
        # 全选本页 → 3 项
        page._on_toggle_all(Qt.CheckState.Checked)
        assert page._lbl_selected.text() == "已选 3 项"
        assert page._chk_all.checkState() == Qt.CheckState.Checked
        # 批量标记已解决
        page._on_batch_resolve()
        assert _wait(page, lambda: (
            page._rows and all(r["resolved"] == "是" for r in page._rows)))
        assert len(page._checked_ids()) == 0
        assert page._batch_bar.isVisible() is False
    finally:
        _close_page(page)


def test_records_page_header_sort_cross_page(qapp, db):
    """表头排序走 SQL：第 2 页仍是全量顺序，且行 id 锚与 _rows 对齐"""
    _seed_problems(65)
    page = _open_page(qapp, db)
    try:
        assert _wait(page, lambda: page._table.rowCount() == 60)
        col = _col("problem")
        page._on_click_header(col)
        assert _wait(page, lambda: (
            page._table.rowCount() == 60 and len(page._rows) == 60
            and page._rows[0]["problem"] == "p00"
            and page._rows[-1]["problem"] == "p59"))
        assert page._table.item(0, col).text() == "p00"
        assert page._table.item(0, _COL_CHECK).data(
            Qt.ItemDataRole.UserRole) == page._rows[0]["id"]
        # 翻到第 2 页：证明排序发生在 SQL，而不是对当前 60 行本地重排
        page._step_page(1)
        assert _wait(page, lambda: (
            page._table.rowCount() == 5 and len(page._rows) == 5
            and page._rows[0]["problem"] == "p60"))
        assert page._lbl_page.text() == "2/2 · 共 65 条"
        # 回第 1 页再点同一列 → 降序，首尾随全量顺序翻转
        page._step_page(-1)
        assert _wait(page, lambda: page._table.rowCount() == 60)
        page._on_click_header(col)
        assert _wait(page, lambda: (
            len(page._rows) == 60 and page._rows[0]["problem"] == "p64"
            and page._rows[-1]["problem"] == "p05"))
        assert page._table.item(0, col).text() == "p64"
        assert page._table.item(0, _COL_CHECK).data(
            Qt.ItemDataRole.UserRole) == page._rows[0]["id"]
    finally:
        _close_page(page)


def test_records_page_fingerprint_gate(qapp, db, monkeypatch):
    _seed(2)
    page = _open_page(qapp, db)
    try:
        assert _wait(page, lambda: page._table.rowCount() == 2)
        calls = {"n": 0}
        real_load = page._load

        def _counting_load(*a, **k):
            calls["n"] += 1
            return real_load(*a, **k)

        monkeypatch.setattr(page, "_load", _counting_load)
        page._last_fingerprint = None
        fp = adb.change_fingerprint()
        page._on_fingerprint(fp)          # 建立基线，不重查
        assert calls["n"] == 0
        page._on_fingerprint(fp)          # 指纹不变，零重绘
        assert calls["n"] == 0
        adb.insert_record({"occurred_at": datetime.now().strftime("%Y-%m-%d"),
                           "problem": "远端新增"})
        page._on_fingerprint(adb.change_fingerprint())  # 指纹变化 → 重查
        assert calls["n"] == 1
        assert _wait(page, lambda: page._table.rowCount() == 3)
    finally:
        _close_page(page)


def test_records_page_compat_entrypoints(qapp, db):
    """外部调用面：set_keyword / refresh_async / _apply_smooth_mode / _sync_auto_timer"""
    _seed(1)
    page = _open_page(qapp, db)
    try:
        assert _wait(page, lambda: page._table.rowCount() == 1)
        page.set_keyword("01")
        assert page._search_edit.text() == "01"
        assert _wait(page, lambda: page._table.rowCount() == 1)
        page.refresh_async()
        assert _wait(page, lambda: page._table.rowCount() == 1)
        page._apply_smooth_mode()
        page._sync_auto_timer()
    finally:
        _close_page(page)
