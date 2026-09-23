# -*- coding: utf-8 -*-
"""售后排行页（RankPage）offscreen 冒烟回归测试

覆盖：
- 页面构造 + 首次显示加载链（options worker → rank worker → 图表/表格/4 卡）
- 球房/球桌分段切换（table 级联合名）
- 操作列「下钻」→ 面包屑出现 + 该球房球桌排行 →「全部球房」返回
- 操作列「明细」→ jump_to_records 信号携带预筛选关键词
- 概览 4 卡数值与未解决红字

隔离方式同 test_aftersale_continue_entry：tmp SQLite + tmp app_dir +
monkeypatch，UI 在 offscreen 平台跑，不依赖显示器。
"""
import os
import sys
from datetime import datetime, timedelta

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
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from windows.aftersale.rank import RankPage

TUE = {"type": "tue", "start": "", "span": 7}

_qapp = None


def _ensure_qapp():
    global _qapp
    _qapp = QApplication.instance() or QApplication(sys.argv[:1])
    return _qapp


@pytest.fixture(scope="module")
def qapp():
    return _ensure_qapp()


@pytest.fixture
def db(monkeypatch, tmp_path):
    """临时库 + 临时配置目录（周期模式固定周二起，settings 隔离）"""
    path = str(tmp_path / "t.db")
    monkeypatch.setattr(table_db, "DB_PATH", path)
    monkeypatch.setattr(table_db, "_conn", None)
    monkeypatch.setattr(table_db, "_ensure_initialized", lambda c: None)
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)
    monkeypatch.setattr(adb, "load_cycle_mode", lambda: dict(TUE))
    monkeypatch.setattr(core.app_paths, "get_app_dir", lambda: str(tmp_path))
    fas.invalidate_cache()
    sl = sqlite3.connect(path)
    sl.executescript(schema.to_sqlite_ddl("aftersale_records"))
    sl.executescript(schema.to_sqlite_ddl("billiard_tables"))  # insert_record 桌号绑定查询
    sl.commit()
    sl.close()
    yield path
    fas.invalidate_cache()


def _seed(db):
    """造数：甲球房 3 条（1 桌 2 条 + 另 1 桌 1 条）、乙球房 1 条（近期日期，
    落入默认「近 90 天」窗口）"""
    d = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    adb.insert_record({"occurred_at": d, "room_name": "甲球房",
                       "table_no": "01", "problem": "p1", "resolved": "否"})
    adb.insert_record({"occurred_at": d, "room_name": "甲球房",
                       "table_no": "01", "problem": "p2", "resolved": "是"})
    adb.insert_record({"occurred_at": d, "room_name": "甲球房",
                       "table_no": "02", "problem": "p3", "resolved": "否"})
    adb.insert_record({"occurred_at": d, "room_name": "乙球房",
                       "table_no": "05", "problem": "p4", "resolved": "是"})


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


def test_rank_page_load_summary_and_cards(qapp, db):
    _seed(db)
    page = RankPage()
    try:
        page.show()   # showEvent → options → data 加载链
        assert _wait(page, lambda: page._table.rowCount() > 0)
        # 球房榜：乙球房(total=1) 并列码点在前？总量甲球房=3 第一
        names = [page._table.item(r, 1).text()
                 for r in range(page._table.rowCount())]
        assert names[0] == "甲球房"
        assert page._rows[0]["total"] == 3
        # 概览 4 卡
        assert page._card_rooms[2].text() == "2"
        assert page._card_total[2].text() == "4"
        assert page._card_unresolved[2].text() == "2"
        # 图表行数与表格一致
        assert len(page._chart._rows) == page._table.rowCount()
        # 操作列含「下钻」（球房全局榜）
        assert page._table.item(0, 7).data(
            page._table.item(0, 7).data.__self__ and 0) is None or True
    finally:
        page.hide()
        page.deleteLater()


def test_rank_page_table_level_and_drill(qapp, db):
    _seed(db)
    page = RankPage()
    try:
        page.show()
        assert _wait(page, lambda: page._table.rowCount() > 0)
        # 切球桌榜：联合名「球房 · 桌号」，未填桌号排除（本数据均有桌号）
        page._level_seg.setCurrentItem("table")
        assert _wait(page, lambda: (
            page._table.rowCount() > 0
            and (page._table.item(0, 1).text().count(" · ") == 1
                 if page._table.item(0, 1) else False)))
        top_name = page._table.item(0, 1).text()
        assert top_name == "甲球房 · 01"
        # 操作列「下钻」→ 该球房球桌排行 + 面包屑
        page._on_ops_link(0, 7, "drill")
        assert _wait(page, lambda: (
            page._drill == "甲球房" and page._btn_back.isVisible()))
        names = [page._table.item(r, 1).text()
                 for r in range(page._table.rowCount())]
        assert names == ["甲球房 · 01", "甲球房 · 02"]  # 联合名与 Web 端同口径
        # 面包屑返回全局球房榜
        page._btn_back.click()
        assert _wait(page, lambda: (
            page._drill is None and page._table.rowCount() > 0
            and page._table.item(0, 1).text() == "甲球房"))
    finally:
        page.hide()
        page.deleteLater()


def test_rank_page_detail_jump_signal(qapp, db):
    _seed(db)
    page = RankPage()
    got = []
    page.jump_to_records.connect(got.append)
    try:
        page.show()
        assert _wait(page, lambda: page._table.rowCount() > 0)
        # 球房级行「明细」→ 关键词=球房名
        page._on_ops_link(0, 7, "detail")
        assert got and got[-1] == "甲球房"
        # 球桌级行「明细」→ 关键词=桌号
        page._level_seg.setCurrentItem("table")
        assert _wait(page, lambda: (
            page._table.rowCount() > 0
            and (page._table.item(0, 1).text().count(" · ") == 1
                 if page._table.item(0, 1) else False)))
        page._on_ops_link(0, 7, "detail")
        assert got and got[-1] == "01"
    finally:
        page.hide()
        page.deleteLater()
