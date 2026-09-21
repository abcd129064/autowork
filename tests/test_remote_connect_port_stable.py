# -*- coding: utf-8 -*-
"""远程面板「连接」差量同步回归（真机 bug 修复：重连端口漂移）

背景：旧 _on_xtcp_connect 每次全清面板注册再从表单重注册，表单行缺端口
时 register_visitor 随机换新端口——热重载世界下等于把同名 visitor 拆了
重建，旧 SSH 窗口全部持死端口（snk_4005 49883→37988→37991 三连漂移）。

验证点：
- 同名 visitor 重连端口恒定（且全程不触发 generate_random_port）
- 表单缺端口时从注册表回填，且回填到表单数据保持两侧口径一致
- 表单里删掉的面板 visitor 被清理，snk 快捷连接来源不受牵连
- 运行中添加 visitor 直接走 apply（热重载生效，不再要求断开重连）
- 同名重复添加被去重（选中已有行，不产生第二条）
- 改 serverName 后端口框残留旧值时自动换空闲端口而非拒绝添加

隔离方式：轻量 harness 提供表单假控件，RemoteMixin 方法按函数绑定挂上；
RemoteSessionManager 用真实注册表，仅打桩 apply（不触碰 QProcess）。
"""
import types
from unittest.mock import MagicMock

import pytest

import core.frp_remote as fr
import main_window.remote_mixin as rm
from core.frp_remote import (RemoteSessionManager, SOURCE_MANUAL,
                             SOURCE_TABLE, SOURCE_SNK)


SETTINGS = {
    "frpc_server": {"serverAddr": "10.0.0.1", "serverPort": 7000,
                    "auth_method": "token", "auth_token": "tok-A"},
    "xtcp_secret_key": "sk-default",
}


@pytest.fixture
def mgr(monkeypatch, tmp_path):
    monkeypatch.setattr(fr, "get_app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(fr, "_load_settings", lambda: dict(SETTINGS))
    m = RemoteSessionManager()
    m._logs = []
    m.log_message.connect(m._logs.append)
    m.apply_calls = []

    def fake_apply():
        m.apply_calls.append(m.records())
        return "reloaded"
    m.apply = fake_apply
    return m


class FakeLineEdit:
    def __init__(self, text=""):
        self._text = text

    def text(self):
        return self._text

    def setText(self, t):
        self._text = t


class FakeSpinBox:
    def __init__(self, value=0):
        self._value = value
        self.assigned = []

    def value(self):
        return self._value

    def setValue(self, v):
        self._value = v
        self.assigned.append(v)


class FakeList:
    def blockSignals(self, _b):
        pass

    def clear(self):
        pass

    def addItem(self, _t):
        pass

    def setCurrentRow(self, i):
        self.current = i

    def currentRow(self):
        return -1

    def item(self, _i):
        return None


class FakeCombo:
    def currentText(self):
        return "XTCP"


class FakeSearch:
    def text(self):
        return ""


class Harness:
    """RemoteMixin 所需宿主的最小替身"""

    def __init__(self, session_mgr):
        self._session_mgr = session_mgr
        self._p2p_visitors = []
        self._p2p_current_index = -1
        self.logs = []
        self.bars = []
        self.ui = types.SimpleNamespace(
            p2p_mode_combo=FakeCombo(),
            p2p_form_server=FakeLineEdit(),
            p2p_form_port=FakeSpinBox(),
            p2p_form_key=FakeLineEdit("abc123"),
            p2p_visitor_list=FakeList(),
            p2p_search=FakeSearch(),
        )

    # 表单保存与 DB 反查不在本测试范围内：桩掉，聚焦差量同步逻辑
    def _save_current_form(self):
        pass

    def _lookup_table_name_by_snk(self, snk):
        return ""

    def _append_log(self, msg):
        self.logs.append(msg)

    def _show_info_bar(self, message, message_type="info", title=None,
                       duration=2500):
        self.bars.append((message, message_type))

    def _update_p2p_buttons(self):
        pass

    def _refresh_p2p_list(self):
        pass


@pytest.fixture
def h(mgr):
    harness = Harness(mgr)
    for name in ("_on_xtcp_connect", "_on_p2p_add", "_get_new_random_port",
                 "_register_visitor_to_manager"):
        setattr(harness, name,
                types.MethodType(getattr(rm.RemoteMixin, name), harness))
    return harness


def _make_running(m):
    m._frpc_process = MagicMock()
    m._applied_signature = "sig"


def _visitor(name, port, source=SOURCE_MANUAL):
    m = {
        "serverName": name, "bindPort": port, "secretKey": "sk",
        "tableId": "", "source": source, "lastUsed": "",
    }
    return m


# ==================== 重连端口稳定 ====================

def test_reconnect_keeps_registry_ports(h, mgr, monkeypatch):
    """同名 visitor 重连：端口一律沿用注册表，全程禁止随机分配"""
    def _boom(*a, **k):
        raise AssertionError("重连路径不应分配随机端口")
    monkeypatch.setattr(rm, "generate_random_port", _boom)
    monkeypatch.setattr(fr, "generate_random_port", _boom)
    mgr._visitors = {
        "snk_4005": _visitor("snk_4005", 37991, SOURCE_TABLE),
        "snk_4008": _visitor("snk_4008", 12547, SOURCE_MANUAL),
    }
    _make_running(mgr)
    # 表单：4005 行端口缺失（漂移场景），4008 端口与注册表一致
    h._p2p_visitors = [
        {"serverName": "snk_4005", "bindPort": None, "secretKey": "sk",
         "source": SOURCE_TABLE},
        {"serverName": "snk_4008", "bindPort": 12547, "secretKey": "sk",
         "source": SOURCE_MANUAL},
    ]

    h._on_xtcp_connect()

    assert mgr._visitors["snk_4005"]["bindPort"] == 37991
    assert mgr._visitors["snk_4008"]["bindPort"] == 12547
    # 端口回填到表单数据，列表显示与注册表一致
    assert h._p2p_visitors[0]["bindPort"] == 37991
    assert len(mgr.apply_calls) == 1


def test_reconnect_cleans_deleted_but_keeps_snk_source(h, mgr):
    """表单删掉的面板 visitor 被移除；snk 快捷连接来源不受牵连"""
    mgr._visitors = {
        "snk_4008": _visitor("snk_4008", 12547),
        "snk_gone": _visitor("snk_gone", 23456, SOURCE_TABLE),
        "snk_quick": _visitor("snk_quick", 34567, SOURCE_SNK),
    }
    _make_running(mgr)
    h._p2p_visitors = [
        {"serverName": "snk_4008", "bindPort": 12547, "secretKey": "sk",
         "source": SOURCE_MANUAL},
    ]

    h._on_xtcp_connect()

    assert "snk_gone" not in mgr._visitors
    assert "snk_quick" in mgr._visitors
    assert mgr._visitors["snk_4008"]["bindPort"] == 12547


def test_reconnect_applies_new_secret_without_port_change(h, mgr):
    """重连仅改密钥：端口不动（diff 语义下 frpc 会重建该隧道但端口稳定）"""
    mgr._visitors = {"snk_4008": _visitor("snk_4008", 12547)}
    _make_running(mgr)
    h._p2p_visitors = [
        {"serverName": "snk_4008", "bindPort": None, "secretKey": "new-k",
         "source": SOURCE_MANUAL},
    ]

    h._on_xtcp_connect()

    assert mgr._visitors["snk_4008"]["bindPort"] == 12547
    assert mgr._visitors["snk_4008"]["secretKey"] == "new-k"


# ==================== 添加即热重载 ====================

def test_add_while_running_hot_reloads(h, mgr):
    """frpc 运行中添加 visitor：立即 apply，提示为已生效而非要求重连"""
    mgr._visitors = {"snk_4008": _visitor("snk_4008", 12547)}
    _make_running(mgr)
    h._p2p_visitors = [
        {"serverName": "snk_4008", "bindPort": 12547, "secretKey": "sk",
         "source": SOURCE_MANUAL},
    ]
    h._p2p_current_index = 0
    h.ui.p2p_form_server.setText("snk_new")
    h.ui.p2p_form_port.setValue(45678)

    h._on_p2p_add()

    assert len(mgr.apply_calls) == 1
    assert any("snk_new" in r["serverName"] for r in mgr.apply_calls[-1])
    msg, kind = h.bars[-1]
    assert kind == "success" and "生效" in msg
    assert "断开" not in msg


def test_add_failure_falls_back_to_warning(h, mgr, monkeypatch):
    """apply 抛错：降级为「暂未生效」warning，不崩溃、列表已含新项"""
    mgr._visitors = {"snk_4008": _visitor("snk_4008", 12547)}
    _make_running(mgr)
    h._p2p_visitors = [
        {"serverName": "snk_4008", "bindPort": 12547, "secretKey": "sk",
         "source": SOURCE_MANUAL},
    ]
    h._p2p_current_index = -1

    def boom():
        raise RuntimeError("reload rejected")
    mgr.apply = boom
    h.ui.p2p_form_server.setText("snk_new")
    h.ui.p2p_form_port.setValue(45678)

    h._on_p2p_add()

    msg, kind = h.bars[-1]
    assert kind == "warning" and "暂未生效" in msg
    assert any(v["serverName"] == "snk_new" for v in h._p2p_visitors)


# ==================== 添加防护 ====================

def test_add_duplicate_name_selects_existing(h, mgr):
    """同名重复添加：选中已有行并提示，不产生第二条"""
    mgr._visitors = {"snk_4005": _visitor("snk_4005", 37991)}
    h._p2p_visitors = [
        {"serverName": "snk_4005", "bindPort": 37991, "secretKey": "sk",
         "source": SOURCE_MANUAL},
    ]
    h._p2p_current_index = -1
    h.ui.p2p_form_server.setText("snk_4005")

    h._on_p2p_add()

    assert len(h._p2p_visitors) == 1
    assert h._p2p_current_index == 0
    assert any("已在列表" in m for m, _k in h.bars)
    assert not mgr.apply_calls


def test_add_with_stale_same_port_gets_new_port(h, mgr):
    """改 serverName 未动端口：自动换空闲端口添加，而非端口冲突拒绝"""
    mgr._visitors = {"snk_4005": _visitor("snk_4005", 37991)}
    h._p2p_visitors = [
        {"serverName": "snk_4005", "bindPort": 37991, "secretKey": "sk",
         "source": SOURCE_MANUAL},
    ]
    h._p2p_current_index = 0
    h.ui.p2p_form_server.setText("snk_4001")
    h.ui.p2p_form_port.setValue(37991)  # 选中行残留端口

    h._on_p2p_add()

    added = h._p2p_visitors[-1]
    assert added["serverName"] == "snk_4001"
    assert added["bindPort"] != 37991
    assert not any("已被其他隧道使用" in log for log in h.logs)


def test_add_explicit_conflict_still_rejected(h, mgr):
    """显式改成被占用的其他 visitor 端口：仍然拒绝"""
    mgr._visitors = {"snk_a": _visitor("snk_a", 11111),
                     "snk_b": _visitor("snk_b", 22222)}
    h._p2p_visitors = [
        {"serverName": "snk_a", "bindPort": 11111, "secretKey": "sk",
         "source": SOURCE_MANUAL},
        {"serverName": "snk_b", "bindPort": 22222, "secretKey": "sk",
         "source": SOURCE_MANUAL},
    ]
    h._p2p_current_index = -1
    h.ui.p2p_form_server.setText("snk_c")
    h.ui.p2p_form_port.setValue(22222)  # 与选中行无关的占用端口

    h._on_p2p_add()

    assert any("已被其他隧道使用" in log for log in h.logs)
    assert len(h._p2p_visitors) == 2
