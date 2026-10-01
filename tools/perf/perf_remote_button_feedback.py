# -*- coding: utf-8 -*-
"""远程面板按钮反馈链路 GUI 线程阻塞排查（真实组件 + 注入 HTTP 延迟）。

背景：远程面板性能调查（2026-09-24），结论见
docs/远程面板性能调查报告2026-09-24.md §4.2 / §4.3。
2026-09-24 优化落地后更新为「优化后口径」验证：
  A. 四视图 refresh 全量重建（含搜索防抖 300ms 与 SessionWork 合帧门控后）；
     A2 改测委托数据构建（LINKS_ROLE setData，旧 cellWidget 按钮已删）
  B. 断开同步原语成本基线（保留）+ B2 异步口径（disconnect_visitor_async
     调用即回，apply 在后台线程，回调经事件循环回 GUI）
  C. open_session 预检异步口径：缓存过期 → 挂起 + request_refresh，
     GUI 即回（旧同步 refresh 阻塞已消除）；另测 refresh 原语成本
     （现运行在感知后台线程）
  D. InfoBar 创建成本与「立即感知」按钮处理器自身耗时

隔离：get_app_dir 打桩到 tools/_scratch/_h_env_app（TOML 写入不触碰
config/）；frps/manager 均不 start()（无周期任务）；HTTP 打桩注入延迟。
用法：E:/ANACONDA/python.exe tools/perf/perf_remote_button_feedback.py
产物：tools/_scratch/perf_remote_button_feedback_result.json（不入库）
"""
import json
import os
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

from PySide6.QtCore import QObject, QTimer, Signal as QSignal
from PySide6.QtWidgets import QApplication, QTableWidgetItem, QWidget
from qfluentwidgets import InfoBar, InfoBarPosition


class FakeProc(QObject):
    """替代 QProcess 的最小进程桩：满足 _stop_frpc 的信号接口"""
    readyReadStandardOutput = QSignal()
    readyReadStandardError = QSignal()
    finished = QSignal(int)

    def quit(self):
        pass

    def kill(self):
        pass

    def state(self):
        from PySide6.QtCore import QProcess
        return QProcess.ProcessState.NotRunning

# ---- 打桩必须在导入业务模块之前生效 ----
import core.frp_remote as _frm
_frm.get_app_dir = lambda: APP_DIR          # TOML/侧车写入隔离

from core.frp_remote import get_session_manager
from core.frps_admin import get_frps_client
from windows.remote_session.remote_hub import (SessionWork, VisitorWork,
                                               QualityWork, FrpsProxiesWork)

app = QApplication.instance() or QApplication([])

RESULT_NAME = "perf_remote_button_feedback_result.json"


def save(out):
    """分段落盘：中途崩溃时保留已完成段的结果"""
    path = os.path.join(ROOT, "tools", "_scratch", RESULT_NAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


class DummyWin(QWidget):
    """主窗口桩：只实现 SessionWork 等视图用到的方法"""

    def __init__(self):
        super().__init__()
        self.ib_calls = []
        self.logs = []

    def _show_info_bar(self, title, kind="info", **kw):
        self.ib_calls.append((time.perf_counter(), kind, title))

    def _append_log(self, msg):
        self.logs.append(str(msg))


def stats_of(samples):
    s = sorted(samples)
    med = statistics.median(s)
    p95 = s[max(0, int(len(s) * 0.95) - 1)]
    return {"median_ms": round(med, 2), "p95_ms": round(p95, 2),
            "min_ms": round(s[0], 2), "max_ms": round(s[-1], 2),
            "n": len(s)}


def inject_visitors(mgr, n):
    for i in range(n):
        mgr._visitors[f"snk_bench_{i:03d}"] = {
            "serverName": f"snk_bench_{i:03d}", "bindPort": 42000 + i,
            "secretKey": "k", "tableId": f"T{i % 97:04d}",
            "source": "snk 快捷", "lastUsed": "09-24 10:00",
            "disabled": False,
        }


def make_fake_proxy(i):
    return {"name": f"snk_dev_{i:03d}", "conf": {"remotePort": 30000 + i},
            "curConns": 1, "todayTrafficIn": 1e8, "todayTrafficOut": 1e7,
            "clientID": f"c{i}", "status": "online"}


def main():
    out = {"env": {"python": sys.version.split()[0], "qt": "offscreen",
                   "note": "HTTP 延迟为注入值，非真实网络"}}

    mgr = get_session_manager()
    frps = get_frps_client()
    # 不 start()：无周期任务、无后台线程；HTTP 打桩后按需手动调 refresh
    win = DummyWin()
    hub = type("Hub", (), {})()

    sess = SessionWork(win, hub)
    vis = VisitorWork(win, hub)
    qua = QualityWork(win, hub)
    pro = FrpsProxiesWork(win, hub)
    for w in (sess, vis, qua, pro):
        w.show()
    for _ in range(3):
        app.processEvents()

    # ============ A1. 四视图 refresh 全量重建成本 ============
    res_a = {}
    for n in (10, 50):
        inject_visitors(mgr, n)
        for name, w in (("session", sess), ("visitor", vis),
                        ("quality", qua), ("proxies", pro)):
            if name == "proxies":
                frps.all_proxies = lambda: {"xtcp": [make_fake_proxy(i) for i in range(n)],
                                            "tcp": [make_fake_proxy(i) for i in range(n)]}
                frps.client_version = lambda cid: "0.65.0"
            ts = []
            for _ in range(8):
                t0 = time.perf_counter()
                w.refresh()
                ts.append((time.perf_counter() - t0) * 1000)
            res_a[f"{name}_n{n}"] = stats_of(ts)
    # 搜索框每字符触发一次 refresh（无防抖）：模拟输入 8 字符
    inject_visitors(mgr, 50)
    frps.all_proxies = lambda: {"xtcp": [make_fake_proxy(i) for i in range(50)]}
    frps.client_version = lambda cid: "0.65.0"
    ts = []
    for _ in range(8):
        t0 = time.perf_counter()
        pro.edit_kw.setText("snk_dev_0")   # textChanged → refresh（无防抖）
        ts.append((time.perf_counter() - t0) * 1000)
    res_a["proxies_search_typing"] = stats_of(ts)
    out["A_view_refresh"] = res_a
    save(out)

    # ============ A2. 操作列委托数据构建成本（P0-2 委托化后口径） ============
    # 旧 cellWidget 按钮组 _row_buttons 已删除；现口径 = 每行构建 links
    # 元组 + setData(LINKS_ROLE)，绘制由 OpsLinkDelegate 按需自绘，
    # 无逐行控件创建/销毁成本
    from core.ops_link_delegate import LINKS_ROLE
    table = sess.table
    ts = []
    for _ in range(30):
        t0 = time.perf_counter()
        r = table.rowCount()
        table.insertRow(r)
        cell = QTableWidgetItem("")
        cell.setData(LINKS_ROLE, (
            ("SSH", "primary", "ssh"), ("SFTP", "primary", "sftp"),
            ("RDP", "primary", "rdp"), ("断开", "danger", "disconnect"),
            ("删除", "danger", "delete")))
        table.setItem(r, 8, cell)
        ts.append((time.perf_counter() - t0) * 1000)
    out["A_ops_links_setdata_5links"] = stats_of(ts)
    save(out)

    # ============ B. 断开同步原语成本基线（apply 热重载同步 HTTP；
    # 生产已改走 B2 的异步包装，此段保留作为原语成本基线） ============
    orig_admin = mgr._request_admin_api
    sig = "".join(mgr._common_config_lines(warn=False))
    res_b = {}
    for delay in (0.1, 0.8, 3.0):
        def fake_admin(path, method="GET", _d=delay):
            time.sleep(_d)
            return (200, "ok")
        mgr._request_admin_api = fake_admin
        ts = []
        for _ in range(5):
            mgr._visitors.clear()
            inject_visitors(mgr, 2)           # 两条：断开一条后仍有启用项 →
            # 热重载路径（若仅 1 条会落入「全部断开→_stop_frpc」路径）
            sn = next(iter(mgr._visitors))
            mgr._frpc_process = FakeProc()    # is_running() → True
            mgr._applied_signature = sig      # 走热重载路径
            t0 = time.perf_counter()
            mgr.disconnect_visitor(sn)
            ts.append((time.perf_counter() - t0) * 1000)
            mgr._frpc_process = None
            mgr._visitors.clear()
        res_b[f"disconnect_delay{int(delay*1000)}ms"] = stats_of(ts)
    mgr._request_admin_api = orig_admin
    out["B_disconnect_gui_block"] = res_b
    save(out)

    # GUI 线程冻结的直观口径：断开期间 16ms 心跳 QTimer 丢失的 tick 数
    mgr._visitors.clear()
    inject_visitors(mgr, 2)
    sn = next(iter(mgr._visitors))
    mgr._frpc_process = FakeProc()
    mgr._applied_signature = sig

    def fake_admin_08(path, method="GET"):
        time.sleep(0.8)
        return (200, "ok")
    mgr._request_admin_api = fake_admin_08
    ticks = {"n": 0}
    hb = QTimer()
    hb.setInterval(16)
    hb.timeout.connect(lambda: ticks.__setitem__("n", ticks["n"] + 1))
    hb.start()
    t0 = time.perf_counter()
    mgr.disconnect_visitor(sn)
    wall = (time.perf_counter() - t0) * 1000
    hb.stop()
    expected = int(wall / 16)
    out["B_jank_heartbeat"] = {
        "wall_ms": round(wall, 1), "ticks_fired": ticks["n"],
        "ticks_expected": expected,
        "note": "同步原语基线：期间 UI 完全冻结；生产已改异步（见 B2）"}
    mgr._request_admin_api = orig_admin
    mgr._frpc_process = None
    save(out)

    # ============ B2. 断开异步口径（P0-2 优化后）：调用即回，apply 后台 ============
    mgr._visitors.clear()
    inject_visitors(mgr, 2)
    sn = next(iter(mgr._visitors))
    mgr._frpc_process = FakeProc()
    mgr._applied_signature = sig
    mgr._request_admin_api = fake_admin_08
    done = {"r": None}
    t0 = time.perf_counter()
    mgr.disconnect_visitor_async(sn, on_done=lambda r: done.__setitem__("r", r))
    ret_ms = (time.perf_counter() - t0) * 1000
    ticks = {"n": 0}
    hb = QTimer()
    hb.setInterval(16)
    hb.timeout.connect(lambda: ticks.__setitem__("n", ticks["n"] + 1))
    hb.start()
    deadline = time.perf_counter() + 5
    while done["r"] is None and time.perf_counter() < deadline:
        app.processEvents()
    wall = (time.perf_counter() - t0) * 1000
    hb.stop()
    out["B2_disconnect_async"] = {
        "call_return_ms": round(ret_ms, 2),
        "total_wall_ms": round(wall, 1),
        "result": done["r"],
        "ticks_fired": ticks["n"],
        "ticks_expected": int(wall / 16),
        "note": "GUI 调用即回，后台 apply 期间心跳无丢失（对比 B_jank_heartbeat）"}
    mgr._request_admin_api = orig_admin
    mgr._frpc_process = None
    save(out)

    # ============ C. open_session 预检（P0-1 异步化后）：缓存过期挂起即回 ============
    import core.frps_admin as _fa
    orig_http = _fa._http_get
    orig_loadcfg = _fa._load_config
    _fa._load_config = lambda: {"base_url": "http://127.0.0.1:9",
                                "user": "u", "password": "p"}
    orig_online, orig_configured = frps.online, frps.configured
    orig_req = frps.request_refresh
    res_c = {}
    frps.online = lambda snk: None
    frps.configured = lambda: True
    frps.request_refresh = lambda: None      # 静默：不触发后台感知回执
    def _stop_chain(*a, **kw):
        raise ValueError("stop-chain")
    mgr.ensure_visitor_async = _stop_chain   # 兜底：即使 fail-open 续接也不进真实链路
    mgr._notify = lambda *a, **kw: None
    for delay in (0.5, 2.5):
        def fake_http(url, user, password, timeout, _d=delay):
            time.sleep(_d)
            return (0, "")
        _fa._http_get = fake_http
        ts = []
        for _ in range(5):
            frps._fails = 0
            frps._circuit_until = 0.0
            t0 = time.perf_counter()
            try:
                mgr.open_session("ssh", "snk_x", "T1")
            except ValueError:
                pass
            ts.append((time.perf_counter() - t0) * 1000)
        res_c[f"precheck_async_return_delay{int(delay*1000)}ms"] = stats_of(ts)
    out["C_precheck_gui_block"] = res_c
    save(out)

    # frps.refresh 原语同步成本（P0-1 后运行在感知后台线程，不再占 GUI）
    _fa._http_get = lambda *a, **kw: (time.sleep(0.05), (200, '{"x":[]}'))[1]
    _fa._parse_proxies = lambda body: {"snk_x": {}}
    ts = []
    for _ in range(5):
        frps._fails = 0
        frps._circuit_until = 0.0
        t0 = time.perf_counter()
        frps.refresh()
        ts.append((time.perf_counter() - t0) * 1000)
    out["C_frps_refresh_sync_50ms"] = stats_of(ts)
    _fa._http_get = orig_http
    _fa._load_config = orig_loadcfg
    del frps.online
    del frps.configured
    frps.request_refresh = orig_req
    del mgr.ensure_visitor_async
    save(out)

    # ============ D. 「立即感知」与 InfoBar ============
    orig_req = frps.request_refresh
    frps.request_refresh = lambda: None
    ts = []
    for _ in range(10):
        t0 = time.perf_counter()
        sess._on_probe_now()
        ts.append((time.perf_counter() - t0) * 1000)
    frps.request_refresh = orig_req
    out["D_on_probe_now_handler"] = stats_of(ts)
    save(out)

    ts = []
    for _ in range(20):
        t0 = time.perf_counter()
        ib = InfoBar.info(title="提示", content="已发起 frps 感知请求",
                          parent=win, position=InfoBarPosition.TOP_RIGHT,
                          duration=100)
        ts.append((time.perf_counter() - t0) * 1000)
        ib.close()
    for _ in range(3):
        app.processEvents()
    out["D_infobar_create"] = stats_of(ts)
    save(out)

    # ============ 反馈链路审查结论（2026-09-24 优化落地后） ============
    out["static_review"] = {
        "buttons_with_immediate_feedback": [
            "SessionWork._on_probe_now（InfoBar 立即 + refresh_finished 回执）",
            "QualityWork._on_probe_once（threading.Thread 后台 + InfoBar）",
            "TunnelConfWork._on_channel_test（request_refresh + _test_armed 回执）",
            "open_session：受理 InfoBar 即时出现，预检缓存过期改挂起续接（P0-1）",
            "window.py 断开/删除：disconnect/delete_visitor_async 移后台，"
            "on_done 回 GUI 线程（P0-2）",
            "TunnelConfWork._on_frpc_health：ping_admin×2 移后台线程（P0-3）",
        ],
        "ops_links_delegate": [
            "SessionWork/FrpsProxiesWork/TunnelPanelWindow 操作列改 "
            "ops_link_delegate 自绘文字链接（LINKS_ROLE），无 cellWidget 按钮",
        ],
        "gui_thread_sync_http_points_remaining": [
            "apply 非 hot-reload 分支（停/重启 frpc，QProcess 必须留 GUI 线程）",
            "_stop_frpc POST /api/stop（3s 超时，仅全部断开时触发）",
        ],
    }

    path = os.path.join(ROOT, "tools", "_scratch", RESULT_NAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\n结果已写入 {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
