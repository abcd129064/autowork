# -*- coding: utf-8 -*-
"""会话 UI 工厂注入回归（B2 收尾：core/frp_remote.py 彻底不 import windows）

守住四条不变量：
1. core/frp_remote.py 的 AST 里不存在任何 module 以 windows 开头的
   Import/ImportFrom（用 AST 而非字符串匹配：文件里还有注释提到 windows）；
2. 未注入 UI 时 _do_open 不抛异常，只把原因转成 InfoBar 错误提示；
3. 注入后 _do_open / _open_terminal_from_sftp / ensure_session_window 都只经
   create_* 工厂拿控件与窗口，参数（含 SFTP 目录、终端起始目录）原样透传；
4. install 幂等，且不覆盖已存在的工厂（测试 fake 或子类实现必须保得住）。

面板模块在本文件里全部用替身，不依赖 qfluentwidgets——这正是 B2 想要的
效果：core 与 UI 解耦后，缺重组件的解释器也能覆盖打开会话的编排逻辑。
"""
import ast
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

import core.frp_remote as fr
from windows.remote_session.session_ui import SessionUi, install

# tests/ 的上一级才是仓库根（AGENTS §2.4 的 __file__ 上溯写法）
ROOT = Path(__file__).resolve().parents[1]
CORE_FILE = ROOT / "core" / "frp_remote.py"
UI_FILE = ROOT / "windows" / "remote_session" / "session_ui.py"


@pytest.fixture(scope="session", autouse=True)
def qapp():
    """_notify 在 notifier=None 时会取 QApplication.activeWindow()，先建好 app。"""
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


@pytest.fixture
def make_manager(monkeypatch, tmp_path):
    """构造真实 RemoteSessionManager：配置目录指向 tmp_path，不碰真实 config。"""
    monkeypatch.setattr(fr, "get_app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(fr, "_load_settings",
                        lambda: {"ssh_user": "u", "ssh_pass": "p",
                                 "videos_dir": ""})

    def _make():
        return fr.RemoteSessionManager()
    return _make


@pytest.fixture
def manager(make_manager):
    return make_manager()


class _SignalStub:
    """open_terminal_here 替身：_do_open 只需要 connect，测试里手动 emit。"""

    def __init__(self):
        self._handlers = []

    def connect(self, handler):
        self._handlers.append(handler)

    def emit(self, path):
        for handler in list(self._handlers):
            handler(path)


class _FakeSftpPanel:
    def __init__(self):
        self.open_terminal_here = _SignalStub()


class _FakeWindow:
    """RemoteSessionWindow 替身：ensure_session_window 会连 destroyed 再调显示 API。"""

    def __init__(self):
        self.added = []
        # 真实窗口 destroyed 是 Qt 信号；这里只需要 connect 不炸
        self.destroyed = SimpleNamespace(connect=lambda _cb: None)
        self.shown = 0

    def add_session(self, panel, icon=None):
        self.added.append((panel, icon))

    def isVisible(self):  # noqa: N802 —— 与 Qt API 命名保持一致
        return False

    def show(self):
        self.shown += 1

    def raise_(self):  # noqa: N802
        pass

    def activateWindow(self):  # noqa: N802
        pass


class FakeUi:
    """SessionUi 替身：记录每次调用的实参，返回可断言的假面板/假窗口。"""

    def __init__(self):
        self.ssh_calls = []
        self.sftp_calls = []
        self.rdp_calls = []
        self.window_calls = 0
        self.panels = []
        self.window = _FakeWindow()

    def create_ssh_panel(self, host, port, username, password, *,
                         server_name="", log_callback=None, start_dir=""):
        self.ssh_calls.append({
            "host": host, "port": port, "username": username,
            "password": password, "server_name": server_name,
            "log_callback": log_callback, "start_dir": start_dir})
        panel = SimpleNamespace(kind="ssh")
        self.panels.append(panel)
        return panel

    def create_sftp_panel(self, host, port, username, password, *,
                          server_name="", log_callback=None,
                          default_remote_path=None, default_local_path=None):
        self.sftp_calls.append({
            "host": host, "port": port, "username": username,
            "password": password, "server_name": server_name,
            "log_callback": log_callback,
            "default_remote_path": default_remote_path,
            "default_local_path": default_local_path})
        panel = _FakeSftpPanel()
        self.panels.append(panel)
        return panel

    def create_rdp_panel(self, host, port, username, password, *,
                         server_name="", log_callback=None):
        self.rdp_calls.append({
            "host": host, "port": port, "username": username,
            "password": password, "server_name": server_name,
            "log_callback": log_callback})
        panel = SimpleNamespace(kind="rdp")
        self.panels.append(panel)
        return panel

    def create_session_window(self):
        self.window_calls += 1
        return self.window


# ---------------- (a) 未安装 UI：只提示，不抛异常 ----------------


def test_do_open_without_ui_only_reports_infobar(manager, monkeypatch):
    bars = []
    monkeypatch.setattr(fr, "show_info_bar",
                        lambda msg, kind, **kw: bars.append((kw.get("title"),
                                                             msg, kind)))
    manager._do_open("ssh", "snk_01", "T1", 2222)   # 不抛异常即核心断言

    assert len(bars) == 1
    title, msg, kind = bars[0]
    assert title == "打开会话失败" and kind == "error"
    assert "会话 UI 未安装" in msg


def test_open_terminal_from_sftp_without_ui_only_reports_infobar(manager,
                                                                monkeypatch):
    bars = []
    monkeypatch.setattr(fr, "show_info_bar",
                        lambda msg, kind, **kw: bars.append((kw.get("title"),
                                                             msg)))
    manager._open_terminal_from_sftp(("10.0.0.9", 22, "u", "p", "srv"), "/tmp")

    assert bars == [("打开终端失败", "会话 UI 未安装（应由 UI 层启动时注入）")]


# ---------------- (b) 注入 fake UI：三个打开入口都走工厂 ----------------


def test_do_open_routes_each_kind_through_factory(manager):
    ui = FakeUi()
    manager.set_session_ui(ui)

    manager._do_open("ssh", "snk_01", "T1", 2222, username="root",
                     password="pw")
    manager._do_open("sftp", "snk_01", "T1", 2223)
    manager._do_open("rdp", "snk_01", "T1", 2224)

    assert len(ui.ssh_calls) == len(ui.sftp_calls) == len(ui.rdp_calls) == 1
    # 三个面板都进同一个工厂窗口，且 add_session 的 icon 关键字签名不变
    assert [p for p, _ in ui.window.added] == ui.panels
    assert ui.window_calls == 1   # 窗口只建一次，后续分支复用

    ssh = ui.ssh_calls[0]
    assert (ssh["host"], ssh["port"], ssh["username"],
            ssh["password"]) == ("127.0.0.1", 2222, "root", "pw")
    assert ssh["server_name"] == "T1（snk_01）"
    assert callable(ssh["log_callback"])
    # 未显式传凭据时回退 settings（_do_open 原有语义不能丢）
    assert (ui.sftp_calls[0]["username"], ui.sftp_calls[0]["password"]) == (
        "u", "p")
    assert ui.rdp_calls[0]["server_name"] == "T1（snk_01）"


def test_do_open_sftp_keeps_video_dir_and_terminal_hook(manager, monkeypatch,
                                                        tmp_path):
    videos = tmp_path / "videos"
    videos.mkdir()
    monkeypatch.setattr(fr, "_load_settings",
                        lambda: {"ssh_user": "u", "ssh_pass": "p",
                                 "videos_dir": str(videos),
                                 "sftp_default_remote_path": "/data"})
    ui = FakeUi()
    manager.set_session_ui(ui)
    manager._do_open("sftp", "snk_01", "116-01", 2223)

    sftp = ui.sftp_calls[0]
    assert sftp["default_local_path"] == str(videos / "116-01")
    assert sftp["default_remote_path"] == "/data"
    assert sftp["server_name"] == "116-01（snk_01）"

    # SFTP「终端」按钮仍然接到 _open_terminal_from_sftp，并补开 SSH 标签
    ui.panels[0].open_terminal_here.emit("/var/log")
    assert len(ui.ssh_calls) == 1
    ssh = ui.ssh_calls[0]
    assert (ssh["host"], ssh["port"], ssh["start_dir"]) == (
        "127.0.0.1", 2223, "/var/log")
    assert ssh["server_name"] == "116-01（snk_01）"


def test_open_terminal_from_sftp_uses_factory(manager):
    ui = FakeUi()
    manager.set_session_ui(ui)
    logs = []
    manager.log_message.connect(logs.append)

    manager._open_terminal_from_sftp(("10.0.0.9", 22, "root", "pw", "srv"),
                                     "/var/log")

    assert len(ui.ssh_calls) == 1
    call = ui.ssh_calls[0]
    assert (call["host"], call["port"], call["username"],
            call["password"]) == ("10.0.0.9", 22, "root", "pw")
    assert call["server_name"] == "srv" and call["start_dir"] == "/var/log"
    assert [p for p, _ in ui.window.added] == ui.panels
    assert any("在终端中打开目录 /var/log" in m for m in logs)


def test_ensure_session_window_uses_factory_and_reuses(manager):
    ui = FakeUi()
    manager.set_session_ui(ui)

    win = manager.ensure_session_window()
    assert win is ui.window
    assert manager._session_window is ui.window
    assert ui.window_calls == 1

    # 第二次取用命中已存活窗口，不再调用工厂（单例语义不变）
    assert manager.ensure_session_window() is ui.window
    assert ui.window_calls == 1


# ---------------- (c) install：幂等且不覆盖已有工厂 ----------------


def test_install_is_idempotent_and_keeps_existing_factory(manager,
                                                          make_manager):
    install(manager)
    first = manager._session_ui
    assert isinstance(first, SessionUi)

    install(manager)
    assert manager._session_ui is first   # 重复调用不重建、不替换

    other = make_manager()
    sentinel = object()
    other._session_ui = sentinel
    install(other)
    assert other._session_ui is sentinel  # 已有工厂（fake/子类）原样保留


def test_set_session_ui_stores_factory(manager):
    ui = FakeUi()
    manager.set_session_ui(ui)
    assert manager._session_ui is ui
    assert manager._require_session_ui() is ui


def test_require_session_ui_raises_when_absent(manager):
    # 未注入时 _require_session_ui 必须抛错（_do_open 再接住转成 InfoBar）
    with pytest.raises(RuntimeError, match="会话 UI 未安装"):
        manager._require_session_ui()


# ---------------- (d) 分层静态检查：core 不再依赖 windows ----------------


def _windows_imports(tree):
    """返回 AST 中所有 module 以 windows 开头的 import 位置。"""
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "windows":
                    offenders.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[0] == "windows":
                offenders.append((node.lineno, module))
    return offenders


def test_core_frp_remote_has_no_windows_imports():
    # 用 AST 而不是 "from windows" 字符串匹配：文件里有注释/文档字符串提到
    # windows，字符串匹配会永远失败或逼迫删掉有价值的分层注释。
    tree = ast.parse(CORE_FILE.read_text(encoding="utf-8"))
    assert _windows_imports(tree) == []


def test_session_ui_module_top_level_is_light():
    """工厂模块顶层不得拉起重依赖：否则注入本身就会破坏启动开销/基线解释器。"""
    tree = ast.parse(UI_FILE.read_text(encoding="utf-8"))
    heavy = ("windows", "qfluentwidgets", "PySide6", "paramiko")
    for node in tree.body:          # 只看模块顶层；方法内的延迟 import 不算
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        for name in names:
            assert name.split(".")[0] not in heavy, (
                f"session_ui.py 顶层不得 import {name}")
