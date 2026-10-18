# -*- coding: utf-8 -*-
"""跑视频「记录与统计」页（windows/run_video/records/）offscreen 基线测试

目标：在拆分/重构前，用当前实现的真实行为锁住 P0 交互基线（只断言，不改源码）。
覆盖：
- 首屏加载链：默认「今天」日期范围 → 异步 worker → 表格行/指标卡/署名候选/
  页脚文案（共 N 条、x/y 页码、翻页按钮可用态、数据源标签）
- 分页：50 行/页、页脚与按钮态、下一页/上一页按钮的实际翻页效果
- 日期范围筛选：今天 / 近 7 天 / 近 30 天 / 本月 / 全部 / 自定义（含起止倒置
  自动交换、结束日期控件显隐、occurred_at 为空回退 created_at 的口径）
- 分类筛选：分类下拉 + 类别候选联动 + 指标卡点击切换/恢复 + 总记录卡清空筛选
- 自动刷新指纹门：指纹不变不重查、变化才重查（直接注入指纹 + 真实 tick 两条路径）
- 行内操作列：编辑/删除链接的分发、写库、静默重查（弹窗打桩，不弹真窗）

隔离方式对齐 tests/test_aftersale_records_page.py：临时 SQLite + 临时 app_dir
+ monkeypatch，绝不连接生产 MySQL；UI 走 offscreen 平台，不依赖显示器。
"""
import calendar
import gc
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("qfluentwidgets")

import core.app_paths
import core.app_settings as app_settings
import database.backend as backend
import database.ledger_db as ledger_db
import database.table_db as table_db
from core.ops_link_delegate import LINKS_ROLE
from database import schema
from PySide6.QtCore import QDate, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from windows.run_video.common import (TABLE_COLUMNS, _COL_OPS, _OPS_LINKS,
                                      _category_color)
from windows.run_video.records import RecordsPage

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
    """独立临时目录（不用 tmp_path：Windows 上 0o700 会被本项目沙箱/ACL 拒绝访问）"""
    d = os.path.join(tempfile.gettempdir(),
                     "autowork_runvideo_%s" % uuid.uuid4().hex[:8])
    os.makedirs(d)
    try:
        yield d
    finally:
        # worker 线程里的 sqlite 连接要等线程对象被回收才释放句柄；
        # 不先 gc 的话 Windows 上 rmtree 会静默失败，留下 autowork_runvideo_* 空壳。
        gc.collect()
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def db(monkeypatch, workdir):
    """临时 ledger_records 库 + 临时配置目录（settings 隔离，绝不碰生产 MySQL）"""
    path = os.path.join(workdir, "t.db")
    monkeypatch.setattr(table_db, "DB_PATH", path)
    monkeypatch.setattr(table_db, "_conn", None)
    monkeypatch.setattr(table_db, "_ensure_initialized", lambda c: None)
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)
    monkeypatch.setattr(core.app_paths, "get_app_dir", lambda: workdir)
    app_settings.invalidate_cache()
    sl = sqlite3.connect(path)
    sl.executescript(schema.to_sqlite_ddl("ledger_records"))
    sl.commit()
    sl.close()
    try:
        yield path
    finally:
        app_settings.invalidate_cache()
        # 先关连接，否则 Windows 上 workdir 里的 t.db 删不掉
        table_db.close()


# ---------- 通用工具 ----------

def _today_str():
    return QDate.currentDate().toString("yyyy-MM-dd")


def _date_str(offset_days):
    return QDate.currentDate().addDays(offset_days).toString("yyyy-MM-dd")


def _seed(n=1, **overrides):
    """造数：默认今日、问题分类、沈喆署名；overrides 覆盖任意记录字段"""
    ids = []
    for i in range(n):
        rec = {
            "category": "问题",
            "kind": "遮挡问题",
            "room_name": "甲球房",
            "video_name": "v%d.mp4" % i,
            "frame": "120",
            "description": "描述%d" % i,
            "repro": "否",
            "new_program": "否",
            "signer": "沈喆",
            "occurred_at": _today_str(),
        }
        rec.update(overrides)
        ids.append(ledger_db.insert_record(rec))
    return ids


def _clear_occurred_at(db_path, rec_id):
    """把某条记录的 occurred_at 清空（模拟旧库历史记录，验证回退 created_at 口径）"""
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute("UPDATE ledger_records SET occurred_at = '' WHERE id = ?",
                     (int(rec_id),))
        conn.commit()
    finally:
        conn.close()


def _wait(page, cond, timeout_ms=8000):
    """事件循环等待条件成立（异步 worker 链完成后断言；不 sleep 硬等）"""
    loop = QEventLoop()
    timer = QTimer(loop)
    timer.setInterval(30)
    state = {"ok": False}

    def _check():
        try:
            if cond():
                state["ok"] = True
                loop.quit()
        except Exception:
            pass

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
    """数据键 → 表格列号（本面板无勾选列，列序与 TABLE_COLUMNS 一致）"""
    for i, (k, _h, _w) in enumerate(TABLE_COLUMNS):
        if k == key:
            return i
    raise AssertionError("unknown column: %s" % key)


def _stop_workers(page):
    """停掉页面持有的所有 QThread，并等待保活集合里的孤儿 worker 退出"""
    for attr in dir(page):
        if not attr.endswith("_worker"):
            continue
        worker = getattr(page, attr, None)
        if worker is None or not hasattr(worker, "requestInterruption"):
            continue
        try:
            worker.requestInterruption()
            worker.wait(3000)
        except RuntimeError:
            pass
    # _load() 里署名 worker 的槽位会被指标卡 worker 覆盖，孤儿 worker 由
    # workers.aftersale_worker 的模块级保活集合持有；关页前统一等它们退出，
    # 否则进程退出时可能报 QThread 相关告警/崩溃。
    from workers.aftersale_worker import _running
    for worker in list(_running):
        try:
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


def _open_page(qapp):
    page = RecordsPage()
    page.show()  # 首次 showEvent 触发 _load()
    return page


# ---------- 首屏加载 ----------

def test_records_page_first_load(qapp, db):
    """首屏：默认日期「今天」、行数、指标卡、署名候选、页脚文案与按钮态"""
    _seed(3)
    page = _open_page(qapp)
    try:
        assert _wait(page, lambda: (
            page._table.rowCount() == 3
            and page._cards["total"].text() == "3"
            and page._signer_combo.count() == 2)), (
            "首屏加载未完成: rows=%r total=%r signers=%r" % (
                page._table.rowCount(), page._cards["total"].text(),
                page._signer_combo.count()))
        assert page._page_size == 50
        # 默认日期范围：今天（基准日期 = 当前日期）
        assert page._date_mode_combo.currentText() == "今天"
        assert page._date_range() == (_today_str(), _today_str())
        # 页脚：总条数 / 页码 / 翻页按钮（单页时两侧禁用）
        assert page._lbl_stats.text() == "共 3 条"
        assert page._lbl_page.text() == "1/1"
        assert page._btn_prev.isEnabled() is False
        assert page._btn_next.isEnabled() is False
        assert page._lbl_source.text() == "数据源: 本地 SQLite"
        # 指标卡：键序固定，计数与列表同口径
        assert list(page._cards) == ["total", "问题", "未复现", "精度", "使用"]
        assert page._cards["问题"].text() == "3"
        assert page._cards["未复现"].text() == "0"
        assert page._cards["精度"].text() == "0"
        assert page._cards["使用"].text() == "0"
        # 行序 id 倒序（最新在前），单元格内容来自记录字段
        assert [r["video_name"] for r in page._rows] == ["v2.mp4", "v1.mp4", "v0.mp4"]
        assert page._table.item(0, _col("video_name")).text() == "v2.mp4"
        assert page._table.item(0, _col("category")).text() == "问题"
        assert page._table.item(0, _col("kind")).text() == "遮挡问题"
        assert page._table.item(0, _col("room_name")).text() == "甲球房"
        assert page._table.item(0, _col("signer")).text() == "沈喆"
        assert page._table.item(0, _col("created_at")).text() == \
            RecordsPage._short_dt(page._rows[0]["created_at"])
        # 操作列：链接清单挂在 item 的 LINKS_ROLE 上（委托绘制/命中的数据契约）
        links = page._table.item(0, _COL_OPS).data(LINKS_ROLE)
        assert [tuple(x) for x in links] == list(_OPS_LINKS)
    finally:
        _close_page(page)


def test_records_page_adapter_presentation(qapp, db):
    """呈现规则来自 table_adapter：截断/tooltip、分类徽章色、操作链接。"""
    desc, repro = "描" * 70, "复" * 40
    _seed(1, description=desc, repro=repro)
    page = _open_page(qapp)
    try:
        assert _wait(page, lambda: page._table.rowCount() == 1)
        cat = page._table.item(0, _col("category"))
        assert cat.text() == "问题"
        assert cat.font().bold()
        assert cat.foreground().color().name() == _category_color("问题").name()
        assert cat.background().color().alpha() == 26
        d = page._table.item(0, _col("description"))
        assert d.text() == desc[:60] + "…" and d.toolTip() == desc
        rr = page._table.item(0, _col("repro"))
        assert rr.text() == repro[:30] + "…" and rr.toolTip() == repro
        assert page._table.item(0, _col("frame")).text() == "120"
        ops = page._table.item(0, _COL_OPS)
        assert ops.text() == ""
        assert [tuple(x) for x in ops.data(LINKS_ROLE)] == list(_OPS_LINKS)
    finally:
        _close_page(page)


def test_records_page_detach_workers(qapp, db):
    """切页/关窗：detach_workers 停轮询并请求中断 controller 的查询线程。"""
    _seed(1)
    page = _open_page(qapp)
    try:
        assert _wait(page, lambda: page._table.rowCount() == 1)
        page._auto_timer.start()
        assert page._auto_timer.isActive()
        page.detach_workers()
        assert not page._auto_timer.isActive()
        assert page._worker is None or not page._worker.isRunning()
    finally:
        _close_page(page)


# ---------- 分页 ----------

def test_records_page_pagination(qapp, db):
    """分页：50 行/页、页脚页码、下一页/上一页按钮的实际行为与页间不重叠"""
    ids = _seed(65)
    page = _open_page(qapp)
    try:
        assert _wait(page, lambda: (
            page._table.rowCount() == 50
            and page._lbl_stats.text() == "共 65 条"
            and page._lbl_page.text() == "1/2"))
        assert page._btn_prev.isEnabled() is False
        assert page._btn_next.isEnabled() is True
        page1_ids = [r["id"] for r in page._rows]
        assert page1_ids == sorted(ids, reverse=True)[:50]
        # 点「下一页」按钮：异步重查 → 第二页 15 行，按钮态翻转
        page._btn_next.click()
        assert _wait(page, lambda: (
            page._lbl_page.text() == "2/2"
            and page._table.rowCount() == 15))
        page2_ids = [r["id"] for r in page._rows]
        assert page2_ids == sorted(ids, reverse=True)[50:]
        assert set(page1_ids).isdisjoint(page2_ids)
        assert page._btn_prev.isEnabled() is True
        assert page._btn_next.isEnabled() is False
        # 末页再点「下一页」：_step_page 守卫挡住，页码与行数不变
        page._btn_next.click()
        assert _wait(page, lambda: (
            page._lbl_page.text() == "2/2"
            and page._table.rowCount() == 15))
        # 点「上一页」回到第一页，行内容与首次一致
        page._btn_prev.click()
        assert _wait(page, lambda: (
            page._lbl_page.text() == "1/2"
            and [r["id"] for r in page._rows] == page1_ids))
        assert page._btn_prev.isEnabled() is False
        assert page._btn_next.isEnabled() is True
    finally:
        _close_page(page)

# ---------- 日期范围筛选 ----------

def test_records_page_date_range_filters(qapp, db):
    """日期范围：今天/近7天/近30天/本月/全部/自定义 + occurred_at 空回退 created_at"""
    legacy_id = _seed(1, video_name="legacy.mp4")[0]
    _seed(2, video_name="today.mp4")
    _seed(1, video_name="yesterday.mp4", occurred_at=_date_str(-1))
    _seed(2, video_name="five.mp4", occurred_at=_date_str(-5))
    _seed(1, video_name="twenty.mp4", occurred_at=_date_str(-20))
    # 旧库历史记录：occurred_at 为空，日期口径回退 created_at（今天）
    _clear_occurred_at(db, legacy_id)
    page = _open_page(qapp)
    try:
        # 默认「今天」：两条今日 + occurred_at 为空的旧记录（回退 created_at）
        assert _wait(page, lambda: (
            page._table.rowCount() == 3
            and page._lbl_stats.text() == "共 3 条"
            and page._rows[-1]["video_name"] == "legacy.mp4")), (
            "默认今天范围过滤异常: rows=%r stats=%r" % (
                page._table.rowCount(), page._lbl_stats.text()))
        assert page._date_range() == (_today_str(), _today_str())
        # 近7天：今天 3 + 昨天 1 + 前 5 天 2 = 6
        page._date_mode_combo.setCurrentIndex(2)
        assert page._date_to.isHidden() is True
        assert _wait(page, lambda: (
            page._table.rowCount() == 6
            and page._lbl_stats.text() == "共 6 条"))
        assert page._date_range() == (_date_str(-6), _today_str())
        # 近30天：再加前 20 天 1 条 = 7
        page._date_mode_combo.setCurrentIndex(3)
        assert _wait(page, lambda: page._table.rowCount() == 7)
        assert page._date_range() == (_date_str(-29), _today_str())
        # 本月：范围 = 基准日期所在自然月（1 号 ~ 月末）
        page._date_mode_combo.setCurrentIndex(4)
        d = QDate.currentDate().toPython()
        last_day = calendar.monthrange(d.year, d.month)[1]
        assert page._date_range() == (
            d.replace(day=1).isoformat(),
            d.replace(day=last_day).isoformat())
        # 全部：不过滤，7 条全在
        page._date_mode_combo.setCurrentIndex(0)
        assert _wait(page, lambda: (
            page._table.rowCount() == 7
            and page._date_range() == ("", "")))
        # 自定义：结束日期控件出现；昨天~今天共 4 条（今日 3 + 昨天 1）
        page._date_mode_combo.setCurrentIndex(5)
        assert _wait(page, lambda: page._date_to.isHidden() is False)
        page._date_from.setDate(QDate.currentDate().addDays(-1))
        page._date_to.setDate(QDate.currentDate())
        assert _wait(page, lambda: page._table.rowCount() == 4)
        assert page._date_range() == (_date_str(-1), _today_str())
        # 自定义倒置（起 > 止）：自动交换，筛选结果不变
        page._date_from.setDate(QDate.currentDate())
        page._date_to.setDate(QDate.currentDate().addDays(-1))
        assert _wait(page, lambda: page._table.rowCount() == 4)
        assert page._date_range() == (_date_str(-1), _today_str())
        # 切回「今天」：结束日期隐藏，基准日期仍是今天 → 3 条
        page._date_mode_combo.setCurrentIndex(1)
        assert _wait(page, lambda: (
            page._date_to.isHidden() is True
            and page._table.rowCount() == 3))
        assert page._date_range() == (_today_str(), _today_str())
    finally:
        _close_page(page)


# ---------- 分类筛选 ----------

def test_records_page_category_filter(qapp, db):
    """分类筛选：下拉 → 类别候选联动 → 重查；指标卡点击切换/恢复分类"""
    _seed(2, category="问题", kind="遮挡问题", video_name="p.mp4")
    _seed(1, category="精度", kind="轻贴:不动", video_name="a.mp4")
    _seed(1, category="使用", kind="让杆/换人", video_name="u.mp4")
    page = _open_page(qapp)
    try:
        assert _wait(page, lambda: (
            page._table.rowCount() == 4
            and page._lbl_stats.text() == "共 4 条"))
        assert page._cards["问题"].text() == "2"
        assert page._cards["精度"].text() == "1"
        assert page._cards["使用"].text() == "1"
        # 分类下拉选「精度」：异步联动类别候选，随后重查
        idx = page._cat_combo.findText("精度")
        assert idx > 0
        page._cat_combo.setCurrentIndex(idx)
        assert _wait(page, lambda: (
            page._table.rowCount() == 1
            and page._rows[0]["category"] == "精度"
            and page._lbl_stats.text() == "共 1 条"))
        assert page._kind_combo.itemText(0) == "全部类别"
        assert _wait(page, lambda: page._kind_combo.findText("轻贴:不动") >= 0)
        # 指标卡点击「问题」→ 切到问题分类；再点同一张 → 恢复全部分类
        page._card_widgets["问题"].clicked.emit()
        assert _wait(page, lambda: (
            page._table.rowCount() == 2
            and page._cat_combo.currentText() == "问题"))
        page._card_widgets["问题"].clicked.emit()
        assert _wait(page, lambda: (
            page._table.rowCount() == 4
            and page._cat_combo.currentText() == "全部分类"))
        # 「总记录」卡：一键清空筛选（日期档回今天、分类回全部）
        page._card_widgets["total"].clicked.emit()
        assert _wait(page, lambda: (
            page._table.rowCount() == 4
            and page._cat_combo.currentText() == "全部分类"
            and page._date_mode_combo.currentText() == "今天"))
    finally:
        _close_page(page)


# ---------- 自动刷新指纹门 ----------

def test_records_page_fingerprint_gate(qapp, db, monkeypatch):
    """指纹门（直接注入）：首次只建基线；相同不重查；增/删导致变化才重查"""
    ids = _seed(2)
    page = _open_page(qapp)
    try:
        assert _wait(page, lambda: page._table.rowCount() == 2)
        calls = {"load": 0}
        real_load = page._load

        def _counting_load():
            calls["load"] += 1
            real_load()

        monkeypatch.setattr(page, "_load", _counting_load)
        fp = ledger_db.change_fingerprint()
        page._last_fingerprint = None
        page._on_fingerprint(fp)  # 首次：只建立基线，不刷新
        assert calls["load"] == 0
        page._on_fingerprint(fp)  # 指纹不变：零重查
        page._on_fingerprint(fp)
        assert calls["load"] == 0
        page._on_fingerprint(None)  # 查询异常返回 None：直接忽略
        assert calls["load"] == 0
        # 新增一条 → 指纹变化（COUNT / MAX(id) 入指纹）→ 静默重查
        _seed(1, video_name="new.mp4")
        fp_add = ledger_db.change_fingerprint()
        assert fp_add != fp
        page._on_fingerprint(fp_add)
        assert calls["load"] == 1
        assert _wait(page, lambda: page._table.rowCount() == 3)
        # 删除一条 → COUNT 变化 → 静默重查
        ledger_db.delete_record(ids[0])
        fp_del = ledger_db.change_fingerprint()
        assert fp_del != fp_add
        page._on_fingerprint(fp_del)
        assert calls["load"] == 2
        assert _wait(page, lambda: page._table.rowCount() == 2)
    finally:
        _close_page(page)


def test_records_page_auto_refresh_tick(qapp, db, monkeypatch):
    """真实 tick 路径：异步指纹 worker → 门回调；不变不重查，变化才重查"""
    _seed(2)
    page = _open_page(qapp)
    try:
        assert _wait(page, lambda: (
            page._table.rowCount() == 2
            and page._worker is not None
            and not page._worker.isRunning()))
        page._last_fingerprint = ledger_db.change_fingerprint()
        fp_calls = {"n": 0}
        real_handler = page._on_fingerprint

        def _counting_handler(fp):
            fp_calls["n"] += 1
            real_handler(fp)

        monkeypatch.setattr(page, "_on_fingerprint", _counting_handler)
        load_calls = {"n": 0}
        real_load = page._load

        def _counting_load():
            load_calls["n"] += 1
            real_load()

        monkeypatch.setattr(page, "_load", _counting_load)
        # tick：指纹查询异步回来，指纹未变 → 不重查
        page._auto_refresh_tick()
        assert _wait(page, lambda: fp_calls["n"] == 1)
        assert load_calls["n"] == 0
        # 等指纹 worker 真正退出，避免下次 tick 被「查询中」前置守卫挡掉
        assert _wait(page, lambda: (
            page._auto_worker is not None
            and not page._auto_worker.isRunning()))
        # 数据变化后再 tick：指纹变 → 静默重查
        _seed(1, video_name="extra.mp4")
        page._auto_refresh_tick()
        assert _wait(page, lambda: (
            fp_calls["n"] == 2
            and load_calls["n"] == 1
            and page._table.rowCount() == 3))
    finally:
        _close_page(page)


# ---------- 行内编辑 / 删除入口 ----------

def test_records_page_ops_links_edit_delete(qapp, db, monkeypatch):
    """操作列「编辑/删除」：链接分发 → 打桩弹窗 → 异步写库 → 静默重查"""
    _seed(2)
    page = _open_page(qapp)
    try:
        assert _wait(page, lambda: page._table.rowCount() == 2)
        mod = sys.modules[type(page).__module__]
        seen = {}

        class _FakeEditDialog(object):
            def __init__(self, record, parent=None):
                seen["edit"] = dict(record)
                self.collected = {"kind": "丢球:袋口", "remark": "测试改备注"}

            def exec(self):
                return True

        monkeypatch.setattr(mod, "EditLedgerDialog", _FakeEditDialog)
        # 行 0 是 id 最大的 v1.mp4；编辑回调应拿到该条记录
        page._on_ops_link(0, _COL_OPS, "edit")
        assert seen["edit"]["video_name"] == "v1.mp4"
        assert _wait(page, lambda: (
            page._rows and page._rows[0]["kind"] == "丢球:袋口"))
        total, rows = ledger_db.query_page(1, 50)
        assert total == 2
        assert rows[0]["kind"] == "丢球:袋口"
        assert rows[0]["remark"] == "测试改备注"
        # 删除：确认框打桩为「确定」，删除后静默重查

        class _FakeMessageBox(object):
            def __init__(self, *args, **kwargs):
                seen["box"] = args

            def exec(self):
                return True

        monkeypatch.setattr(mod, "MessageBox", _FakeMessageBox)
        page._on_ops_link(0, _COL_OPS, "delete")
        assert seen["box"][0] == "删除确认"
        assert "跑视频" in seen["box"][1]
        assert _wait(page, lambda: page._table.rowCount() == 1)
        total, rows = ledger_db.query_page(1, 50)
        assert total == 1
        assert rows[0]["video_name"] == "v0.mp4"
        assert page._lbl_stats.text() == "共 1 条"
    finally:
        _close_page(page)