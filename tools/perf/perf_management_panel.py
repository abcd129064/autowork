# -*- coding: utf-8 -*-
"""运维管理面板性能 harness：构造/懒构建/首查/填充/重建各环节量化测量。

背景：docs/远程面板性能调查报告2026-09-24.md 之后对运维管理面板
（windows/management/）的性能专项调查；结论落在
docs/管理面板性能调查报告2026-09-25.md。
用法：C:/Users/shen_zhe/miniconda3/python.exe tools/perf/perf_management_panel.py
产物：tools/_scratch/perf_mgmt_panel.txt（不入库）

隔离措施（不碰真实 config/database）：
- backend._mysql_settings_cache = {"enabled": False} 强制 SQLite 路径；
- table_db.DB_PATH 重定向到 tools/_scratch 临时库（程序结束自删）；
- core.app_paths.get_app_dir 及各模块同名引用重定向到 scratch 临时目录；
- frps client / TableFetchWorker / 防补漏网络链路全部打桩（零网络）。
"""
import os
import sys

# ---- Qt 运行时引导（docs/Qt内联引导说明.md 标准模板，必须在 import PySide6 前） ----
import os as _os
import importlib.util as _qt_iu
_qt_handles = []
try:
    _qt_spec = _qt_iu.find_spec('PySide6')
    if _qt_spec is not None:
        _qt_locs = list(getattr(_qt_spec, 'submodule_search_locations', None) or [])
        if _qt_locs:
            _qt_pkg = _qt_locs[0]
            for _d in (_qt_pkg, _os.path.dirname(_qt_pkg),
                       _os.path.join(_os.environ.get('SystemRoot', r'C:\Windows'), 'System32')):
                if _os.path.isdir(_d):
                    try:
                        _qt_handles.append(_os.add_dll_directory(_d))
                    except OSError:
                        pass
            _os.environ['QT_PLUGIN_PATH'] = _os.path.join(_qt_pkg, 'plugins')
            _os.environ.setdefault('QT_QPA_PLATFORM_PLUGIN_PATH',
                                   _os.path.join(_qt_pkg, 'plugins', 'platforms'))
except Exception:
    pass
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# tools/perf/ → 上溯三层 = 仓库根（AGENTS §2.4）
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "tools", "_scratch")
os.makedirs(OUT_DIR, exist_ok=True)

import time
import sqlite3
import statistics
from datetime import datetime, timedelta

import core.acrylic_patch  # noqa: F401  （引导三阶段：qfluentwidgets 之前）
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QApplication

# ==================== 隔离（必须在 import 项目模块之前生效的部分） ====================
from database import backend, table_db, schema

backend._mysql_settings_cache = {"enabled": False}   # 强制 SQLite，不读 config/database.json

DB_PATH = os.path.join(OUT_DIR, "perf_mgmt_tmp.db")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
table_db.DB_PATH = DB_PATH
table_db._conn = None

APP_DIR = os.path.join(OUT_DIR, "perf_mgmt_appdir")
os.makedirs(APP_DIR, exist_ok=True)


def _stub_get_app_dir(*a, **k):
    return APP_DIR


# ==================== 造数（临时库，与真实库同构） ====================

def _build_db(n_tables=1000, n_kd=10000, n_sub=200):
    """按真实 DDL 建全部表，PRAGMA 反射列名后插合成数据"""
    sl = sqlite3.connect(DB_PATH)
    for t in schema.TABLE_NAMES:
        sl.executescript(schema.to_sqlite_ddl(t))
    sl.commit()

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y/%m/%d")
    day_before = (datetime.now() - timedelta(days=2)).strftime("%Y/%m/%d")

    # billiard_tables：球桌表（query_page 数据源）
    cols = [r[1] for r in sl.execute("PRAGMA table_info(billiard_tables)")]
    cols = [c for c in cols if c != "id"]
    known = {"name": "289-{i:02d}", "roomName": "球房{i}", "onlineStatusName": "在线",
             "remark": "备注 snk_{i}", "snk_code": "snk_{i}", "code": "DEV{i}",
             "city": "深圳", "deviceVersion": "1.0.{i}", "status": "1",
             "todesk_id": "1234567{i%10}", "todeskId": "1234567{i%10}",
             "todesk_status": "1", "sunlogin_id": "", "cameraPassExt": ""}
    rows = []
    for i in range(n_tables):
        rows.append(tuple(known.get(c, "x").format(i=i) if c in known
                          else f"v{i}" for c in cols))
    ph = ",".join("?" * len(cols))
    sl.executemany(f"INSERT INTO billiard_tables({','.join(cols)}) VALUES({ph})", rows)

    # kd_status：设备状态（device_page 数据源，按 file_path 日期分区）
    cols = [r[1] for r in sl.execute("PRAGMA table_info(kd_status)")]
    cols = [c for c in cols if c != "id"]
    file_fields = {"normal_files", "except_files", "untreated_files",
                   "operation_files", "accuracy_files", "already_files",
                   "rubbish_files", "version_files"}
    rows = []
    for i in range(n_kd):
        fp = yesterday if i % 5 else day_before
        vals = []
        for c in cols:
            if c == "file_path":
                vals.append(fp)
            elif c == "table_id":
                vals.append(f"289-{i % 500:02d}")
            elif c == "device_code":
                vals.append(f"DEV{i % 500}")
            elif c == "club_name":
                vals.append(f"球房{i % 500}")
            elif c in file_fields:
                vals.append("[]")
            else:
                vals.append(f"{i % 97}")
        rows.append(tuple(vals))
    ph = ",".join("?" * len(cols))
    sl.executemany(f"INSERT INTO kd_status({','.join(cols)}) VALUES({ph})", rows)

    # submission_log：精度/问题提交台账（高频统计 get_submission_stats 数据源）
    try:
        cols = [r[1] for r in sl.execute("PRAGMA table_info(submission_log)")]
        cols = [c for c in cols if c != "id"]
        rows = []
        for i in range(n_sub):
            vals = []
            for c in cols:
                if c in ("created_at", "submitted_at", "report_time"):
                    vals.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                elif c == "table_id":
                    vals.append(f"289-{i % 500:02d}")
                elif c == "device_code":
                    vals.append(f"DEV{i % 500}")
                else:
                    vals.append(f"v{i}")
            rows.append(tuple(vals))
        ph = ",".join("?" * len(cols))
        sl.executemany(f"INSERT INTO submission_log({','.join(cols)}) VALUES({ph})", rows)
    except sqlite3.Error:
        pass  # 该解释器 SQLite 缺特性时跳过（查询侧有 try/except 兜底）
    sl.commit()
    sl.close()


_build_db()

# 预热：触发 _ensure_initialized + FTS 首次 rebuild（一次性成本，不污染测量；
# 真实生产库 FTS 常驻，rebuild 只发生在旧库升级首查）
table_db.query_page(1, 1, "")
table_db.query_kd_page(1, 1, "")

# ==================== 桩 ====================


class _FakeFrpsClient(QObject):
    proxies_changed = Signal(object)

    def __init__(self):
        super().__init__()
        self._n = 0

    def start(self):
        pass

    def online(self, snk):
        self._n += 1
        if not snk:
            return None
        return "online" if (self._n % 3) else "offline"


class _FakeFetchWorker(QObject):
    """health_page TableFetchWorker 替身：start() 不拉网络不落库"""
    result_ready = Signal(object)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

    def start(self):
        pass

    def isRunning(self):
        return False


def _install_stubs():
    import windows.management.table_page as tp
    import windows.management.health_page as hp
    import windows.management.window as mwin
    tp.get_frps_client = lambda: _FAKE_FRPS
    hp.TableFetchWorker = _FakeFetchWorker
    mwin.get_frps_client = lambda: _FAKE_FRPS
    mwin.get_session_manager = lambda: object()  # window 构造仅存引用
    # C2 历史补漏会打网络+写库：桩为 no-op
    import windows.management.device_page as dp
    dp.DevicePage._backfill_missing_dates = lambda self: None
    # settings/日志读写全部落 scratch（覆盖各模块同名引用）
    import core.app_paths
    core.app_paths.get_app_dir = _stub_get_app_dir
    for m in list(sys.modules.values()):
        if m is not None and getattr(m, "get_app_dir", None) is not None \
                and hasattr(m, "__name__") and m.__name__.startswith(("windows.", "core.")):
            m.get_app_dir = _stub_get_app_dir


_FAKE_FRPS = _FakeFrpsClient()

# ==================== 测量辅助 ====================

app = QApplication.instance() or QApplication(sys.argv)

_LINES = []


def out(msg=""):
    print(msg)
    _LINES.append(msg)


def _med(fn, repeat=3):
    """重复执行取中位；返回 (中位 ms, 全部 ms)"""
    ts = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts), ts


def wait_until(cond, timeout=8.0):
    """驱动事件循环直到条件成立；返回是否成功"""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return False


# ==================== 测量段落 ====================


def sec_gamepage():
    from windows.management.moyu_page import GamePage
    from windows.management.moyu_widgets import (Game2048Widget, SnakeWidget,
                                                 MinesweeperWidget, MoyuReaderWidget)
    out("\n== A. 构造成本（3 次中位，offscreen，无渲染） ==")
    med, all3 = _med(lambda: GamePage())
    out(f"A1 GamePage 全建（Pivot+4 游戏控件）   {med:8.1f} ms   all={all3}")
    for name, cls in (("2048", Game2048Widget), ("贪吃蛇", SnakeWidget),
                      ("扫雷", MinesweeperWidget), ("小说阅读器", MoyuReaderWidget)):
        med, _ = _med(lambda cls=cls: cls())
        out(f"A1.{name:<8} 单控件构造                 {med:8.1f} ms")


def sec_healthpage():
    import windows.management.health_page as hp
    med, all3 = _med(lambda: hp.HealthPage())
    out(f"A2 HealthPage 构造（fetch 已桩）       {med:8.1f} ms   all={all3}")
    # E. 告警表重建 1000 行（09-23 修复回归验证）
    page = hp.HealthPage()
    n = 1000
    page._rows = [{"name": f"289-{i % 500:02d}", "roomName": f"球房{i % 500}",
                   "onlineStatusName": "在线" if i % 2 else "离线",
                   "health": 5500 if i % 3 else 4500, "stale": bool(i % 7 == 0)}
                  for i in range(n)]
    med, all3 = _med(lambda: page._rebuild_table(), repeat=3)
    out(f"E1 HealthPage._rebuild_table {n} 行     {med:8.1f} ms   all={all3}")


def sec_tablepage_e2e():
    import windows.management.table_page as tp
    ts = []
    for _ in range(3):
        t0 = time.perf_counter()
        page = tp.TablePage()
        ok = wait_until(lambda: page._total > 0, timeout=8)
        if not ok:
            out("!! TablePage 首查 8s 未完成")
        ts.append((time.perf_counter() - t0) * 1000)
    med = statistics.median(ts)
    out(f"A3 TablePage 构造→首查填充完成 (e2e)    {med:8.1f} ms   all={ts}")
    # 构造（不含查询等待）单独计
    med2, all2 = _med(lambda: tp.TablePage(), repeat=3)
    out(f"A3a TablePage __init__ 返回时刻         {med2:8.1f} ms   all={all2}")
    return page


def sec_populate(page):
    from windows.management.common import TABLE_COLUMNS
    n_total = 300
    rows = []
    for i in range(n_total):
        d = {k: f"289-{i:02d}" if k == "name" else f"v{i}"
             for (k, _t, _w) in TABLE_COLUMNS}
        d.update({"code": f"DEV{i}", "snk_code": f"snk_{i}" if i % 2 else "",
                  "todesk_status": "1" if i % 2 else "0"})
        rows.append(d)
    out("\n== B. 填充成本（纯填充，关更新+blockSignals 已内置） ==")
    for size in (50, 100, 300):
        data = rows[:size]
        med, all3 = _med(lambda d=data: page._populate(d), repeat=3)
        out(f"B1 TablePage._populate {size:3d} 行        {med:8.1f} ms   all={all3}")
    # D. frps 列刷新（frps 名单推送驱动）
    page._populate(rows[:50])
    med, all3 = _med(lambda: page._refresh_frps_column(), repeat=3)
    out(f"D1 TablePage._refresh_frps_column 50 行   {med:8.1f} ms   all={all3}")


def sec_devicepage():
    import windows.management.device_page as dp
    # 懒构建 e2e：show() 触发 _lazy_init + 首查填充
    ts = []
    for _ in range(3):
        t0 = time.perf_counter()
        page = dp.DevicePage()
        page.showEvent(QShowEvent())
        ok = wait_until(lambda: page._total > 0, timeout=8)
        if not ok:
            out("!! DevicePage 首查 8s 未完成")
        ts.append((time.perf_counter() - t0) * 1000)
    out(f"A4 DevicePage 懒构建→首查填充 (e2e)     {statistics.median(ts):8.1f} ms   all={ts}")
    # A4b 日历缓存构建（N1 后改由 _on_query_finished 首查完成+800ms 触发，
    # 不再占用首次切页窗口；此处直接调用单独计成本）
    page0 = dp.DevicePage()
    page0.showEvent(QShowEvent())
    wait_until(lambda: page0._total > 0)
    QApplication.processEvents()  # 冲掉之前已排队的 singleShot
    med_cal, all_cal = _med(lambda: page0._apply_calendar_cache(), repeat=3)
    out(f"A4b DevicePage._apply_calendar_cache        {med_cal:8.1f} ms   all={all_cal}")
    # 纯填充
    from windows.management.common import DEVICE_COLUMNS
    rows = []
    for i in range(300):
        d = {k: f"DEV{i}" if k == "device_code" else f"v{i}"
             for (k, _t, _w) in DEVICE_COLUMNS}
        d["table_id"] = f"289-{i % 500:02d}"
        rows.append(d)
    med2, all2 = _med(lambda: dp.DevicePage(), repeat=3)
    out(f"A4a DevicePage __init__（壳，懒）        {med2:8.1f} ms   all={all2}")
    page = dp.DevicePage()
    page.showEvent(QShowEvent())
    wait_until(lambda: page._total > 0)
    for size in (50, 300):
        med, all3 = _med(lambda d=rows[:size]: page._populate(d), repeat=3)
        out(f"B2 DevicePage._populate {size:3d} 行       {med:8.1f} ms   all={all3}")


def sec_other_pages():
    import windows.management.health_page as hp
    import windows.management.settings_page as sp
    import windows.management.widget_page as wp
    import windows.management.moyu_page as mp
    med, all3 = _med(lambda: hp.TrendPage(), repeat=3)
    out(f"A5 TrendPage 懒构建（show 前壳）        {med:8.1f} ms   all={all3}")
    med, all3 = _med(lambda: sp.AdminSettingsPage(), repeat=3)
    out(f"A6 AdminSettingsPage 构造               {med:8.1f} ms   all={all3}")
    med, all3 = _med(lambda: wp.TestPage(), repeat=3)
    out(f"A7 TestPage 构造（控件墙）              {med:8.1f} ms   all={all3}")
    # N2（2026-09-25）：GamePage 已改懒构建，构造只剩壳；
    # 首次装配成本 = showEvent 触发 _build_content
    med, all3 = _med(lambda: mp.GamePage(), repeat=3)
    out(f"A8 GamePage 构造（懒壳，N2 后）         {med:8.1f} ms   all={all3}")

    def _game_full():
        g = mp.GamePage()
        g.showEvent(QShowEvent())
        return g

    med, all3 = _med(_game_full, repeat=3)
    out(f"A8a GamePage 壳+首次装配（showEvent）   {med:8.1f} ms   all={all3}")


def sec_window_e2e():
    import windows.management.window as mwin
    ts = []
    for _ in range(3):
        t0 = time.perf_counter()
        w = mwin.ManagementPanelWindow()
        ts.append((time.perf_counter() - t0) * 1000)
        w.close()
        w.deleteLater()
    out(f"A9 ManagementPanelWindow 构造（7 页全建）{statistics.median(ts):8.1f} ms   all={ts}")


def sec_db():
    from windows.management import common as mgmt
    out("\n== C. 纯 DB 查询（Worker 内同款调用，10k 行 kd / 1k 行球桌） ==")
    med, all3 = _med(lambda: mgmt._query_tables_page_with_stats(1, 50, "", 30, True, True, True))
    out(f"C1 球桌页分页+高频统计 (50 行)          {med:8.1f} ms   all={all3}")
    yday = (datetime.now() - timedelta(days=1)).strftime("%Y/%m/%d")
    med, all3 = _med(lambda: mgmt._query_kd_page_with_stats(1, 50, "", yday, "", False, 30))
    out(f"C2 设备页分页+高频统计 (50 行, 昨日分区) {med:8.1f} ms   all={all3}")
    med, all3 = _med(lambda: table_db.query_kd_page(1, 50, "DEV1", yday, "", False))
    out(f"C2a 设备页关键词搜索 (50 行, LIKE/FTS)  {med:8.1f} ms   all={all3}")


def main() -> int:
    _install_stubs()
    out(f"环境: PySide6 {__import__('PySide6').__version__} / offscreen / "
        f"临时库 10k kd + 1k 球桌 / {datetime.now():%Y-%m-%d %H:%M:%S}")
    sec_gamepage()
    sec_healthpage()
    page = sec_tablepage_e2e()
    sec_populate(page)
    sec_devicepage()
    sec_other_pages()
    sec_window_e2e()
    sec_db()
    out("\n说明：offscreen 无 GPU/绘制；真机数值通常更高（叠加渲染与销毁成本）。")
    path = os.path.join(OUT_DIR, "perf_mgmt_panel.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(_LINES) + "\n")
    print(f"\n结果已写 {path}")
    return 0


if __name__ == "__main__":
    rc = main()
    # 后台 worker/QThread 未完全收尾，走强制退出（docs/Qt内联引导说明.md 常见坑）
    os._exit(rc)
