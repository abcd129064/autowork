# -*- coding: utf-8 -*-
"""售后面板「连续录入」回归测试（2026-09-22 对齐 Web 端）

覆盖 EditRecordDialog 连续录入模式：
- 新增模式：保存成功不关窗、清空问题字段、保留填写人/解决人/发生日期记忆
- 连续多条：同一弹窗连续落库多条记录
- 必填缺失：弹窗不关、不落库
- 编辑模式回归：保存即关窗、collected 正常收集、不落库
- 按钮文案：连续=保存并继续/完成，编辑=保存/取消

隔离方式同 test_aftersale_batch_ops（tmp SQLite + monkeypatch）+
app_settings 走 tmp app_dir（save_last_people/save_last_occurred 隔离）。
UI 在 offscreen 平台跑，不依赖显示器。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("qfluentwidgets")

import core
import core.app_settings as fas
import database.backend as backend
import database.table_db as table_db
import database.aftersale_db as adb
from database import schema
from PySide6.QtCore import QDate, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QWidget

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
    sl.commit()
    sl.close()
    yield path
    fas.invalidate_cache()


def _count(db):
    sl = sqlite3.connect(db)
    n = sl.execute("SELECT COUNT(*) FROM aftersale_records").fetchone()[0]
    sl.close()
    return n


def _make_record(**kw):
    """一条完整可提交的记录（必填齐全）"""
    rec = {
        "issue_type": "硬件问题",
        "occurred_at": "2026-08-20",
        "table_no": "T1",
        "room_name": "阳光球房",
        "region": "四川",
        "problem": "识别反应慢",
        "cause": "摄像头遮挡",
        "resolved": "否",
        "is_initiative": "是",
        "is_our_problem": "是",
        "is_important": 0,
        "solution": "",
        "resolver": "李四",
        "response_time": "30分钟",
        "creator": "张三",
        "snk_code": "SNK-001",
    }
    rec.update(kw)
    return rec


def _wait_worker(qapp, worker, timeout=10000):
    """等待 AftersaleDBWorker 线程退出 + 主线程队列消费（result_ready 槽）"""
    if worker is not None and worker.isRunning():
        loop = QEventLoop()
        QTimer.singleShot(timeout, loop.quit)
        worker.finished.connect(loop.quit)
        loop.exec()
    for _ in range(10):
        qapp.processEvents()


def _wait_dialog_done(qapp, dlg, timeout_ms=3000):
    """等待弹窗关闭完成（qfw MaskDialogBase.done 经 ~100ms 淡出动画才置 result）。

    ⚠️ 不能用 dlg.result() 判断是否已关闭：QDialog.Rejected == 0，而未关闭
    弹窗的初始 result() 也是 0，两者无法区分——必须以 accepted/rejected 信号
    实际发射为准（调用前先挂好 spy），这里只负责轮询事件循环推进动画。
    """
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        qapp.processEvents()
        if dlg.result() != 0:  # 已置 Accepted(1)/Rejected(2)（初始恒 0）
            break
        time.sleep(0.02
                   )
    for _ in range(6):
        qapp.processEvents()


# ==================== 按钮文案 ====================

def test_button_labels(db, qapp):
    from windows.aftersale.dialogs import EditRecordDialog
    host = QWidget()
    host.resize(800, 600)
    dlg_add = EditRecordDialog({}, host, title="新增售后记录", continuous=True)
    dlg_edit = EditRecordDialog({"id": 1}, host)
    assert dlg_add.yesButton.text() == "保存并继续"
    assert dlg_add.cancelButton.text() == "完成"
    assert dlg_edit.yesButton.text() == "保存"
    assert dlg_edit.cancelButton.text() == "取消"
    # 编辑弹窗不受 continuous 参数误开影响（带 id/created_at 强制单条语义）
    dlg_edit2 = EditRecordDialog({"id": 2}, host, continuous=True)
    assert dlg_edit2.yesButton.text() == "保存"


# ==================== 连续录入主流程 ====================

def test_continue_entry_saves_and_keeps_dialog_open(db, qapp):
    from windows.aftersale.dialogs import EditRecordDialog
    host = QWidget()
    host.resize(800, 600)
    saved_cb = []
    dlg = EditRecordDialog({}, host, title="新增售后记录",
                           continuous=True, on_saved=lambda: saved_cb.append(1))
    accepted = []
    dlg.accepted.connect(lambda: accepted.append(True))

    dlg.form.set_values(_make_record())
    dlg.yesButton.click()          # validate 恒 False → 基类不 accept
    _wait_worker(qapp, dlg._save_worker)

    assert not accepted            # 弹窗未被 accept（保持打开）
    assert _count(db) == 1         # 记录已落库
    assert saved_cb == [1]         # on_saved 回调已通知调用方
    # 表单已清空（问题相关字段），等待下一条
    f = dlg.form
    assert f.room_edit.text() == ""
    assert f.table_no_edit.text() == ""
    assert f.region_combo.text() == ""
    assert f.problem_combo.text() == ""
    assert f.cause_edit.toPlainText() == ""
    assert f.solution_edit.toPlainText() == ""
    assert f.response_combo.text() == ""
    assert f._snk_code == ""
    assert not f.is_important_check.isChecked()
    # 判定恢复默认值 是/否/是
    assert f.resolved_combo.value() == "是"
    assert f.is_initiative_combo.value() == "否"
    assert f.is_our_problem_combo.value() == "是"
    # 填写人/解决人恢复「上次填写」（collect 时已记住）
    assert f.creator_edit.text() == "张三"
    assert f.resolver_combo.text() == "李四"
    # 发生日期沿用「记住上次发生日期」记忆（默认开）
    assert f.occurred_picker.date == QDate.fromString("2026-08-20", "yyyy-MM-dd")
    # 校验错误态已清
    assert dlg.form.validate() == ["类型", "球房", "地区", "问题"]
    dlg.form.clear_error()
    # 保存按钮已恢复可用（可继续提交）
    assert dlg.yesButton.isEnabled()


def test_continue_entry_multiple_records(db, qapp):
    from windows.aftersale.dialogs import EditRecordDialog
    host = QWidget()
    host.resize(800, 600)
    dlg = EditRecordDialog({}, host, title="新增售后记录", continuous=True)

    for i in range(3):
        dlg.form.set_values(_make_record(room_name=f"球房{i}", table_no=f"T{i}",
                                         problem=f"问题{i}"))
        dlg.yesButton.click()
        _wait_worker(qapp, dlg._save_worker)
        assert not dlg._busy

    assert _count(db) == 3
    sl = sqlite3.connect(db)
    rooms = [r[0] for r in sl.execute(
        "SELECT room_name FROM aftersale_records ORDER BY id")]
    sl.close()
    assert rooms == ["球房0", "球房1", "球房2"]
    # 弹窗仍在（未 accept）、表单为空可继续
    assert dlg.result() != QDialog.Accepted
    assert dlg.form.room_edit.text() == ""


# ==================== 必填缺失：不落库、不关窗 ====================

def test_continue_entry_missing_required(db, qapp):
    from windows.aftersale.dialogs import EditRecordDialog
    host = QWidget()
    host.resize(800, 600)
    dlg = EditRecordDialog({}, host, title="新增售后记录", continuous=True)
    accepted = []
    dlg.accepted.connect(lambda: accepted.append(True))

    # 只填类型，缺 球房/地区/问题
    dlg.form.type_combo.setText("硬件问题")
    dlg.yesButton.click()
    for _ in range(6):
        qapp.processEvents()

    assert not accepted            # 弹窗不关
    assert dlg._validated_ok is False
    assert dlg._save_worker is None  # 未触发落库
    assert _count(db) == 0
    dlg.form.clear_error()


# ==================== 编辑模式回归：保存即关窗 ====================

def test_edit_mode_unchanged(db, qapp):
    from windows.aftersale.dialogs import EditRecordDialog
    host = QWidget()
    host.resize(800, 600)
    rec = _make_record(id=1, created_at="2026-08-20 10:00:00")
    dlg = EditRecordDialog(rec, host)
    assert dlg.validate() is True   # 编辑模式：校验通过 → 基类 accept 关窗
    dlg.yesButton.click()
    _wait_dialog_done(qapp, dlg)    # 等淡出动画结束、result 置位
    assert dlg.result() == QDialog.Accepted
    # collected 正常收集（调用方据此走 update 链路，id 由调用方在 exec 后补）
    assert dlg.collected["issue_type"] == "硬件问题"
    assert dlg.collected["room_name"] == "阳光球房"
    assert "id" not in dlg.collected
    # 编辑模式不触发 insert
    assert _count(db) == 0
