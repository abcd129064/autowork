# -*- coding: utf-8 -*-
"""状态不同步双 bug 回归（2026-09-25 用户报告）

Bug 1（球桌管理「同步数据」永卡灰色）：
  TableFetchWorker 拉取成功但落库失败（_DBQueryWorker 包装 table_db.save_all，
  MySQL 闪断窗口内半开连接写失败抛错）时 error 信号无人接收 →
  _on_save_finished 永不执行 → 按钮永卡禁用 + 文案永停「正在从服务器同步...」。
  锁：_on_sync_done 源码必须连接 _save_worker.error。

Bug 2（主界面数据库标签恒显「SQLite 兜底」）：
  backend.mark_online 的注释场景「用户显式测试连接成功」从未被任何生产代码
  调用——点标签测试成功后状态机仍 DEGRADED，标签不翻；自然恢复又依赖业务
  查询触发 probe（_MYSQL_PROBE_INTERVAL=15s 节流 × 连续 2 次迟滞），用户
  停在已加载完的页面上没有查询就永不恢复。
  锁：_on_db_test_done ok=True 必须回写 backend.mark_online() 并同步轮询
  基线 _last_backend_state（避免 3s 轮询重复弹「已恢复在线」）。
"""
import inspect

from database import backend


# ---- Bug 1：落库失败必须恢复同步按钮 ---------------------------------------

def test_on_sync_done_connects_save_error():
    """_on_sync_done 未连接 _save_worker.error 即为回归（按钮永卡灰色根因）"""
    from windows.management import table_page as tp
    src = inspect.getsource(tp.TablePage._on_sync_done)
    assert "_save_worker.error.connect" in src, (
        "_on_sync_done 未连接 _save_worker.error——落库失败时"
        "「同步数据」按钮永卡灰色、文案永停「正在从服务器同步...」")


def test_on_sync_error_restores_button():
    """error 落点 _on_sync_error 必须恢复按钮启用状态（承接拉取/落库两阶段失败）"""
    from windows.management import table_page as tp
    src = inspect.getsource(tp.TablePage._on_sync_error)
    assert "setEnabled(True)" in src, "_on_sync_error 未恢复同步按钮"


# ---- Bug 2：显式测试成功必须回写状态机 --------------------------------------

class _LabelSpy:
    """db_status_label 桩：记录最后一次 setText/setStyleSheet"""

    def __init__(self):
        self.last_text = ""
        self.last_css = ""

    def setText(self, t):
        self.last_text = t

    def setStyleSheet(self, c):
        self.last_css = c


def _fake_window():
    """最小 MainWindow 替身：复用真实 _on_db_test_done/_refresh_db_status_text，
    只替换其触及的成员（ui.db_status_label / 日志 / InfoBar），不构造 QWidget"""
    from main_window.main_window import MainWindow

    class _Fake:
        _refresh_db_status_text = MainWindow._refresh_db_status_text
        _on_db_test_done = MainWindow._on_db_test_done

        def __init__(self):
            self._last_backend_state = backend.get_state()
            self.ui = type("UI", (), {
                "db_status_label": _LabelSpy(),
            })()
            self.logs = []
            self.infos = []

        def _append_log(self, msg):
            self.logs.append(msg)

        def _show_info_bar(self, *a, **k):
            self.infos.append(a)

    return _Fake()


def test_db_test_ok_flips_degraded_to_online():
    """测试成功：DEGRADED → ONLINE（此前 mark_online 生产代码零调用即本 bug）"""
    backend.mark_degraded()
    fake = _fake_window()
    fake._on_db_test_done(True, "连接成功")
    assert backend.get_state() == backend.STATE_ONLINE
    # 轮询基线必须同步：否则 3s 后 _poll_backend_state 重复弹「已恢复在线」
    assert fake._last_backend_state == backend.STATE_ONLINE
    assert fake.ui.db_status_label.last_text == "数据库: MySQL 在线"


def test_db_test_ok_when_already_online_is_noop():
    """本就 ONLINE 时测试成功：状态不变、mark_online 幂等返回 False"""
    backend.mark_online()
    fake = _fake_window()
    fake._on_db_test_done(True, "连接成功")
    assert backend.get_state() == backend.STATE_ONLINE


def test_db_test_fail_keeps_degraded_and_label():
    """测试失败：不主动动状态机（等业务查询自然降级），标签保持兜底文案"""
    backend.mark_degraded()
    fake = _fake_window()
    fake._on_db_test_done(False, "连接超时")
    assert backend.get_state() == backend.STATE_DEGRADED
    assert fake.ui.db_status_label.last_text == "数据库: SQLite 兜底"
    assert fake.infos and fake.infos[0][0] == "连接超时"


def teardown_module(module):
    """还原模块级全局状态机到默认 ONLINE，避免污染其他测试"""
    backend.mark_online()
