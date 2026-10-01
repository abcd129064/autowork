# -*- coding: utf-8 -*-
"""open_session 全链路分段计时（真实 RemoteSessionManager + 网络打桩）。

背景：远程面板性能调查（2026-09-24），结论见
docs/远程面板性能调查报告2026-09-24.md §4.4 / §4.5。对 open_session
各阶段独立计时：
  预检（缓存命中 / 缓存过期→挂起 + 后台感知回执续接，P0-1）→
  ensure_visitor_async（复用端口在听即回 / 复用但端口未听→热重载补齐 /
  冷启动）→ wait_ports_ready（就绪即回 / 8s 上限）→ _do_open
  （SSH/SFTP/RDP 面板构造）→ 全链路端到端。

2026-09-24 优化后同步：open_session 预检缓存过期不再同步 refresh，
frps 桩改为 QObject 并提供真 Qt Signal（refresh_finished），
request_refresh 桩仿真生产线程模型（后台延时后刷出 online + 回执）。

隔离：get_app_dir → tools/_scratch/_h_env_app；_restart_frpc 打桩
（不启动真 frpc）；_request_admin_api 注入延迟返回 200；frps 打桩；
真实端口用本机 socket 监听模拟（必须带后台 accept 线程：不 accept 的
监听 socket 在 backlog 填满后 connect_ex 会 1s 超时并误报「未占用」，
属于 harness 伪像，生产 frpc 是真实转发器不受影响）。面板构造后只
deleteLater，绝不处理定时器事件（SSH/SFTP/RDP 面板均在构造时挂 100ms
singleShot 自连，事件循环一旦处理定时器会发起真实网络连接）。

用法：E:/ANACONDA/python.exe tools/perf/perf_remote_opensession.py
产物：tools/_scratch/perf_remote_opensession_result.json（不入库）
"""
import json
import os
import socket
import statistics
import sys
import time

# tools/perf/ → 上溯三层 = 仓库根（AGENTS §2.4：子目录脚本需三层 dirname）
ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 跨解释器借用 qfluentwidgets 源码（纯 Python 包，见 perf_remote_scroll.py 说明）
_MINI_SP = os.environ.get(
    "QFW_SITE_PACKAGES",
    r"C:\Users\shen_zhe\miniconda3\Lib\site-packages")
if os.path.isdir(os.path.join(_MINI_SP, "qfluentwidgets")):
    sys.path.append(_MINI_SP)

APP_DIR = os.path.join(ROOT, "tools", "_scratch", "_h_env_app")
os.makedirs(APP_DIR, exist_ok=True)

from PySide6.QtCore import QEvent, QEventLoop, QObject, QTimer
from PySide6.QtCore import Signal as QSignal
from PySide6.QtWidgets import QApplication

import core.frp_remote as _frm
_frm.get_app_dir = lambda: APP_DIR
# 隔离 settings：避免真实 videos_dir 被面板构造期扫描/清理
_frm._load_settings = lambda: {"videos_dir": "", "ssh_user": "root",
                              "ssh_pass": "x"}

from core.frp_remote import get_session_manager

app = QApplication.instance() or QApplication([])

RESULT_NAME = "perf_remote_opensession_result.json"


def save(out):
    path = os.path.join(ROOT, "tools", "_scratch", RESULT_NAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


def stats_of(samples):
    s = sorted(samples)
    return {"median_ms": round(statistics.median(s), 2),
            "p95_ms": round(s[max(0, int(len(s) * 0.95) - 1)], 2),
            "min_ms": round(s[0], 2), "max_ms": round(s[-1], 2), "n": len(s)}


def bind_port() -> tuple:
    """本机监听 socket + 后台 accept 线程，模拟 frpc 已就绪的 bindPort。

    必须真实 accept：不 accept 的监听 socket 在 backlog 填满后
    connect_ex 会 1s 超时并误报「未占用」（实测伪像，见模块 docstring）；
    生产中 frpc 是真实转发器会 accept，不受此影响。
    返回 (port, (sock, stop_event))。
    """
    import threading
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(64)
    stop = threading.Event()

    def _acceptor():
        s.settimeout(0.2)
        while not stop.is_set():
            try:
                c, _ = s.accept()
                c.close()
            except OSError:
                pass
    threading.Thread(target=_acceptor, daemon=True).start()
    return s.getsockname()[1], (s, stop)


def main():
    out = {"env": {"python": sys.version.split()[0], "qt": "offscreen",
                   "note": "HTTP/进程均打桩，仅 UI 与文件 IO 为真实成本"}}
    mgr = get_session_manager()

    # ---- 打桩 ----
    notifies = []
    mgr._notify = lambda title, msg, error=False, notifier=None: notifies.append(
        (title, error))
    panels = []

    class FakeWin:
        def add_session(self, panel):
            panels.append(panel)
    mgr.ensure_session_window = lambda: FakeWin()

    restart_calls = []
    def fake_restart(signature):
        t0 = time.perf_counter()
        from core.frp_remote import _PANEL_TOML_NAME  # noqa: F401
        # 复刻 _restart_frpc 的真实成本（不含进程启动）：写 TOML 已由
        # _persist_registry 完成，这里仅标记状态
        mgr._frpc_process = object()      # 哨兵：is_running() → True
        mgr._applied_signature = signature
        mgr._frpc_started_at = time.monotonic()
        restart_calls.append((time.perf_counter() - t0) * 1000)
    mgr._restart_frpc = fake_restart

    admin_delays = {"d": 0.0}
    def fake_admin(path, method="GET"):
        time.sleep(admin_delays["d"])
        return (200, "ok")
    mgr._request_admin_api = fake_admin

    # frps 打桩：P0-1 后预检缓存过期走 request_refresh + refresh_finished
    # 回执续接，桩必须是 QObject 并提供真 Qt Signal（回执经事件循环分发）
    frps_states = {"online": "online", "configured": True, "refresh_delay": 0.0}

    class _FakeFrps(QObject):
        refresh_finished = QSignal(str)

        def __init__(self, states):
            super().__init__()
            self._states = states
            self.refresh_calls = 0

        def online(self, snk):
            return self._states["online"]

        def configured(self):
            return self._states["configured"]

        def refresh(self):
            self.refresh_calls += 1
            time.sleep(self._states["refresh_delay"])
            return "ok"

        def request_refresh(self):
            # 仿真生产感知线程模型：后台延时后刷出 online 并发回执
            import threading
            delay = self._states["refresh_delay"]

            def _bg():
                if delay:
                    time.sleep(delay)
                self._states["online"] = "online"
                self.refresh_finished.emit("ok")

            threading.Thread(target=_bg, daemon=True).start()

    frps = _FakeFrps(frps_states)
    import core.frps_admin as _fa
    _fa.get_frps_client = lambda: frps

    # ============ 1. 预检各形态 ============
    res = {}
    ts = []
    for _ in range(200):
        t0 = time.perf_counter()
        frps.online("snk_x")
        ts.append((time.perf_counter() - t0) * 1000)
    res["precheck_cached_lookup"] = stats_of(ts)

    for delay in (0.05, 0.5, 2.5):
        frps_states["online"] = None          # 缓存不可信 → 触发 refresh
        frps_states["refresh_delay"] = delay
        ts = []
        for _ in range(5):
            t0 = time.perf_counter()
            try:
                from core.frps_admin import get_frps_client as _g
                st = _g().online("snk_x")
                if st is None and _g().configured():
                    _g().refresh()
                    st = _g().online("snk_x")
            except Exception:
                st = None
            ts.append((time.perf_counter() - t0) * 1000)
        res[f"precheck_refresh_{int(delay*1000)}ms"] = stats_of(ts)
    frps_states["online"] = "online"
    frps_states["refresh_delay"] = 0.0
    out["1_precheck"] = res
    save(out)

    # ============ 2. ensure_visitor 各路径 ============
    res = {}
    # 2a. 复用且端口在听
    port, sock = bind_port()
    mgr._visitors.clear()
    mgr._visitors["snk_x"] = {"serverName": "snk_x", "bindPort": port,
                              "secretKey": "k", "tableId": "T1",
                              "source": "snk 快捷", "lastUsed": "",
                              "disabled": False}
    mgr._frpc_process = object()
    sig = "".join(mgr._common_config_lines(warn=False))
    mgr._applied_signature = sig
    ts = []
    for _ in range(20):
        t0 = time.perf_counter()
        mgr.ensure_visitor("snk_x", table_id="T1")
        ts.append((time.perf_counter() - t0) * 1000)
    res["reuse_port_listening"] = stats_of(ts)

    # 2b. 复用但端口未监听 → apply 热重载补齐（注入 admin 延迟）
    # 端口探测打桩：本机对未监听端口的 connect_ex 会静默丢弃 SYN、
    # 每次走满 1s 超时（真实成本单列见 is_port_in_use_closed_port），
    # 此处打桩为立即 False 以纯净测量 apply 开销
    sock[0].close()
    sock[1].set()
    _tmp = socket.socket()
    _tmp.bind(("127.0.0.1", 0))
    _free_port = _tmp.getsockname()[1]
    _tmp.close()
    mgr._visitors["snk_x"]["bindPort"] = _free_port
    import p2p as _p2p
    _orig_ipiu = _p2p.is_port_in_use
    _p2p.is_port_in_use = lambda port, host="127.0.0.1": False
    try:
        for delay in (0.05, 0.8):
            admin_delays["d"] = delay
            ts = []
            for _ in range(10):
                t0 = time.perf_counter()
                mgr.ensure_visitor("snk_x", table_id="T1")
                ts.append((time.perf_counter() - t0) * 1000)
            res[f"reuse_port_missing_reload_admin{int(delay*1000)}ms"] = stats_of(ts)
    finally:
        _p2p.is_port_in_use = _orig_ipiu
    admin_delays["d"] = 0.0

    # 真实端口探测成本（未监听端口，本机 SYN 静默丢弃 → 每次走满超时）
    ts = []
    for _ in range(5):
        t0 = time.perf_counter()
        _orig_ipiu(_free_port)
        ts.append((time.perf_counter() - t0) * 1000)
    res["is_port_in_use_closed_port"] = stats_of(ts)

    # 2c. 冷启动（未注册 + frpc 未运行）
    ts = []
    for i in range(10):
        mgr._visitors.clear()
        mgr._frpc_process = None
        mgr._applied_signature = None
        t0 = time.perf_counter()
        mgr.ensure_visitor(f"snk_cold_{i}", table_id="T1")
        ts.append((time.perf_counter() - t0) * 1000)
    res["cold_start_register_apply"] = stats_of(ts)
    mgr._frpc_process = object()
    out["2_ensure_visitor"] = res
    save(out)

    # ============ 3. wait_ports_ready（真实 QTimer 轮询） ============
    res = {}
    port2, sock2 = bind_port()
    ts = []
    for _ in range(20):
        loop = QEventLoop()
        done = {"q": False}

        def _cb():
            done["q"] = True
            loop.quit()
        t0 = time.perf_counter()
        mgr.wait_ports_ready([port2], _cb)
        if not done["q"]:        # 首轮 _poll 同步执行：可能已在 exec 前就绪
            loop.exec()
        ts.append((time.perf_counter() - t0) * 1000)
    res["ready_immediate"] = stats_of(ts)
    sock2[0].close()
    sock2[1].set()
    # 上限路径：端口永不在听 → 8s 截止（只跑一次）
    loop = QEventLoop()
    t0 = time.perf_counter()
    mgr.wait_ports_ready([59999], loop.quit)
    loop.exec()
    res["deadline_8s"] = {"median_ms": round((time.perf_counter() - t0) * 1000, 1),
                          "n": 1}
    out["3_wait_ports_ready"] = res
    save(out)

    # ============ 4. 面板构造（_do_open 的 UI 部分） ============
    res = {}
    from windows.remote_session.ssh_terminal import SSHTerminalPanel
    from windows.remote_session.sftp_window import SFTPPanel
    from windows.remote_session.rdp_window import RDPPanel
    port3, sock3 = bind_port()
    host, user, pwd = "127.0.0.1", "root", "x"
    for kind, cls in (("ssh", SSHTerminalPanel), ("sftp", SFTPPanel),
                      ("rdp", RDPPanel)):
        ts = []
        for _ in range(10):
            panels.clear()
            t0 = time.perf_counter()
            mgr._do_open(kind, "snk_x", "T1", port3)
            dt = (time.perf_counter() - t0) * 1000
            ts.append(dt)
            # 不处理定时器事件：直接丢弃面板（构造时挂的 100ms 自连
            # singleShot 永不触发，不产生真实网络连接）
            for p in panels:
                try:
                    p.deleteLater()
                except RuntimeError:
                    pass
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        res[f"panel_{kind}"] = stats_of(ts)
    sock3[0].close()
    sock3[1].set()
    out["4_panel_construct"] = res
    save(out)

    # ============ 5. 端到端全链路（预检缓存命中 + 复用在听 + 就绪即回） ============
    res = {}
    port5, sock5 = bind_port()
    mgr._visitors.clear()
    mgr._visitors["snk_x"] = {"serverName": "snk_x", "bindPort": port5,
                              "secretKey": "k", "tableId": "T1",
                              "source": "snk 快捷", "lastUsed": "",
                              "disabled": False}
    mgr._frpc_process = object()
    mgr._applied_signature = sig
    ts = []
    for _ in range(10):
        panels.clear()
        loop = QEventLoop()
        done = {"q": False}

        def _finish():
            done["q"] = True
            loop.quit()
        QTimer.singleShot(15000, loop.quit)   # 兜底超时
        orig_do = mgr._do_open

        def wrapped_do(*a, **kw):
            orig_do(*a, **kw)
            QTimer.singleShot(0, _finish)
        mgr._do_open = wrapped_do
        t0 = time.perf_counter()
        mgr.open_session("ssh", "snk_x", "T1")
        if not done["q"]:
            loop.exec()
        mgr._do_open = orig_do
        ts.append((time.perf_counter() - t0) * 1000)
        for p in panels:
            try:
                p.deleteLater()
            except RuntimeError:
                pass
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    res["e2e_happy_ssh"] = stats_of(ts)

    # 5b. 预检需 refresh（注入 0.5s）的端到端（P0-1 异步口径：挂起后由
    # 后台感知回执续接，GUI 不阻塞；总 wall 仍包含感知往返）
    frps_states["refresh_delay"] = 0.5
    ts = []
    for _ in range(5):
        frps_states["online"] = None   # 每轮重新制造缓存过期（回执会刷回 online）
        panels.clear()
        loop = QEventLoop()
        QTimer.singleShot(15000, loop.quit)
        orig_do = mgr._do_open
        mgr._do_open = lambda *a, **kw: (orig_do(*a, **kw),
                                         QTimer.singleShot(0, loop.quit))
        t0 = time.perf_counter()
        mgr.open_session("ssh", "snk_x", "T1")
        loop.exec()
        mgr._do_open = orig_do
        # 注意：不 processEvents——SSH 面板构造时挂的 100ms 自连 singleShot
        # 绝不能被触发，否则会发起真实 paramiko 连接
        ts.append((time.perf_counter() - t0) * 1000)
    frps_states["online"] = "online"
    frps_states["refresh_delay"] = 0.0
    res["e2e_precheck_refresh_500ms_ssh"] = stats_of(ts)
    sock5[0].close()
    sock5[1].set()
    out["5_end_to_end"] = res
    save(out)

    # ============ 6. 理论最坏链路（代码常量推导） ============
    import core.frp_remote as fr
    import core.frps_admin as fa
    out["6_constants"] = {
        "frps_refresh_timeout_sec": fa._DEFAULT_TIMEOUT_SEC,
        "frps_refresh_full_happy_path_requests":
            "1 xtcp + 1 serverinfo + 8 proxies/clients ≈ 10 次 GET"
            "（200 时全部触发；不可达时仅第 1 次）",
        "admin_api_timeout_sec": fr._ADMIN_API_TIMEOUT_SEC,
        "admin_reload_worst": "3s×3 次 + 0.5s×2 sleep ≈ 10s（GUI 线程）",
        "port_ready_interval_ms": fr._PORT_READY_INTERVAL_MS,
        "port_ready_deadline_ms": fr._PORT_READY_DEADLINE_MS,
        "prewarm_timeout_ms": fr._PREWARM_TIMEOUT_MS,
        "stop_grace_kill_ms": 2500,
    }
    save(out)

    print(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\n结果已写入 tools/_scratch/{RESULT_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
