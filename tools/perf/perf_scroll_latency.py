# -*- coding: utf-8 -*-
"""表格滚动延迟 harness：售后记录页 + 管理面板球桌页，DPI 缩放 × delegate 形态。

背景：用户反馈售后面板「记录与统计」与管理面板「球桌管理」表格滚动延迟
150~300ms（性能选项全关：动画开、平滑滚动关）。既有
tools/perf/scroll_profile.py 的 offscreen 基线仅 ~12ms/帧，量级对不上；
本 harness 补两个真机关键变量：
  1. QT_SCALE_FACTOR（DPI 缩放，CPU 光栅成本 ≈ dpr²，真机普遍 125%~200%）；
  2. delegate 形态（qfw 默认 = 性能优化全关路径 / LeanTableDelegate 轻量
     委托 / 单元格 QPixmap 绘制缓存候选）。
并验证 offscreen 是否取到 CJK 字体（旧报告注明缺字体可能低估文本排版成本）。

用法：C:/Users/shen_zhe/miniconda3/python.exe tools/perf/perf_scroll_latency.py
      [--scale 1.0] [--win 1600x900] [--steps 30] [--rounds 3] [--truth]
产物：tools/_scratch/perf_scroll_latency_<scale>_<win>.txt（不入库）

两套口径（--truth 同时报，默认只报前者）：
  1. 强制口径（历史口径）：每步 setValue + viewport().repaint() —— 等于把
     「整视口全量光栅」当作每步成本，是**上界**；
  2. 自然口径（--truth）：只 setValue，用「事件 → 画面静默」等待，并统计
     每步 paint 面积。Qt 滚动实际走 backing store 位块搬运 + 只重绘新露出
     条带（实测 0.03~0.04 视口当量/步），与强制口径差一个量级。
  真机体感 = 自然口径的 CPU 成本 + DWM 合成（offscreen 测不到），故两档都留。

隔离措施：backend 强制 SQLite、table_db.DB_PATH/get_app_dir 重定向 scratch、
load_cycle_mode 桩、showEvent 加载链短路（不碰真实 config/database）。
"""
import os
import sys
import argparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

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

_ap = argparse.ArgumentParser()
_ap.add_argument('--scale', type=float, default=1.0,
                 help='QT_SCALE_FACTOR（须在 QApplication 创建前设置）')
_ap.add_argument('--steps', type=int, default=30)
_ap.add_argument('--rounds', type=int, default=3)
_ap.add_argument('--page', choices=['all', 'aftersale', 'mgmt'], default='all')
_ap.add_argument('--win', default='1600x900',
                 help='页面逻辑尺寸 WxH（大窗口面积变体，物理像素 = W×H×scale²）')
_ap.add_argument('--cols', default='',
                 help='列数扫描模式：逗号分隔的可见列数列表（如 3,7,13），'
                      '只测 prod/lean 委托随可见列数的延迟曲线；为空则走全变体默认流程')
_ap.add_argument('--truth', action='store_true',
                 help='追加自然路径口径：不强制 repaint，报「事件→画面静默」延迟'
                      '与每步绘制面积（区分整视口重绘 vs 位块搬运+条带重绘）')
_ap.add_argument('--blit', choices=['on', 'off'], default=None,
                 help='表格滚动位块搬移修复（core.perf.patch_table_scroll_blit）'
                      '开/关 A/B。默认不干预：走代码当前的默认实现（开）')
_ap.add_argument('--idle-ms', type=float, default=25.0,
                 help='--truth 下判定「画面已静默」的空闲阈值（毫秒，默认 25）')
_args = _ap.parse_args()

_W, _H = (int(x) for x in _args.win.lower().split("x"))
_COLS = [int(x) for x in _args.cols.split(",") if x.strip()] if _args.cols else None

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["QT_SCALE_FACTOR"] = str(_args.scale)

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

import core.acrylic_patch  # noqa: F401
from PySide6.QtCore import Qt, QPoint, QRect, QObject, QEvent, QEventLoop
from PySide6.QtGui import QFont, QFontInfo, QFontMetrics, QWheelEvent, QPixmap, QPainter
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmapCache

from database import backend, table_db, schema

backend._mysql_settings_cache = {"enabled": False}

DB_PATH = os.path.join(OUT_DIR, "perf_scroll_tmp.db")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
table_db.DB_PATH = DB_PATH
table_db._conn = None

APP_DIR = os.path.join(OUT_DIR, "perf_scroll_appdir")
os.makedirs(APP_DIR, exist_ok=True)


def _stub_get_app_dir(*a, **k):
    return APP_DIR


import core.app_paths
core.app_paths.get_app_dir = _stub_get_app_dir
for _m in list(sys.modules.values()):
    if _m is not None and getattr(_m, "get_app_dir", None) is not None \
            and hasattr(_m, "__name__") and _m.__name__.startswith(("windows.", "core.")):
        _m.get_app_dir = _stub_get_app_dir


# ==================== 造数 ====================

def _build_mgmt_db(n_tables=1000):
    sl = sqlite3.connect(DB_PATH)
    for t in schema.TABLE_NAMES:
        sl.executescript(schema.to_sqlite_ddl(t))
    sl.commit()
    cols = [r[1] for r in sl.execute("PRAGMA table_info(billiard_tables)")]
    cols = [c for c in cols if c != "id"]
    known = {"name": "289-{i:02d}", "roomName": "球房{i}", "onlineStatusName": "在线",
             "remark": "备注 snk_{i}", "snk_code": "snk_{i}", "code": "DEV{i}",
             "city": "深圳", "deviceVersion": "1.0.{i}", "status": "1",
             "todesk_id": "1234567{i%10}", "todeskId": "1234567{i%10}",
             "todesk_status": "1", "sunlogin_id": "", "cameraPassExt": ""}
    rows = [tuple(known.get(c, "x").format(i=i) if c in known else f"v{i}"
                  for c in cols) for i in range(n_tables)]
    ph = ",".join("?" * len(cols))
    sl.executemany(f"INSERT INTO billiard_tables({','.join(cols)}) VALUES({ph})", rows)

    # 售后记录表（RecordsPage 数据源；中文文本长度贴近生产分布）
    d0 = datetime.now() - timedelta(days=60)
    rows = []
    for i in range(3000):
        day = (d0 + timedelta(days=i % 60)).strftime("%Y/%m/%d")
        rows.append((
            f"{day} 10:00:00", day.replace("/", "-"), f"填写人{i % 8}",
            ["硬件问题", "软件问题", "球桌问题", "网络问题"][i % 4],
            f"289-{i % 40:02d}", f"球房{i % 25}", f"区域{i % 6}",
            "击球点位偏移需要重新校准定位器水平仪" if i % 3 else "扫码灯常亮无法连接",
            "长期使用磨损导致安装面形变", "是" if i % 3 == 0 else "否", "否", "是",
            "重新校准并紧固全部螺丝后复测验证走位精度恢复正常" if i % 3 else "已远程指导重启恢复",
            f"解决人{i % 5}", "30分钟", "", f"DEV{i % 40}", day,
        ))
    sl.executemany(
        "INSERT INTO aftersale_records "
        "(created_at, occurred_at, creator, issue_type, table_no, room_name, "
        "region, problem, cause, resolved, is_initiative, is_our_problem, "
        "solution, resolver, response_time, snk_code, device_code, cycle_start) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    sl.commit()
    sl.close()


_build_db_ts = time.perf_counter()
_build_mgmt_db()
table_db.query_page(1, 1, "")  # 预热 _ensure_initialized + FTS rebuild（一次性）

import database.aftersale_db as adb
adb.load_cycle_mode = lambda: {"type": "mon", "start": "", "span": 7}

app = QApplication.instance() or QApplication(sys.argv)

_LINES = []


def out(msg=""):
    print(msg, flush=True)
    _LINES.append(msg)


def _med(fn, repeat=3):
    ts = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts), ts


class _PaintCounter(QObject):
    def __init__(self):
        super().__init__()
        self.paints = 0

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Paint:
            self.paints += 1
        return False


# ==================== delegate 变体 ====================

def _make_cache_delegate(view):
    """单元格 QPixmap 绘制缓存（P2-1 候选思路）：绘制一次，滚动帧只 blit"""
    from qfluentwidgets.components.widgets.table_view import TableItemDelegate
    from PySide6.QtWidgets import QStyleOptionViewItem

    class CacheDelegate(TableItemDelegate):
        _cache = {}

        def paint(self, painter, option, index):
            w, h = option.rect.width(), option.rect.height()
            if w <= 0 or h <= 0:
                return
            text = index.data(Qt.ItemDataRole.DisplayRole) or ""
            key = (index.row(), index.column(), w, h, str(text))
            cls = type(self)
            pm = cls._cache.get(key)
            if pm is None:
                pm = QPixmap(w, h)
                pm.fill(Qt.transparent)
                opt = QStyleOptionViewItem(option)
                opt.rect = QRect(0, 0, w, h)
                p = QPainter(pm)
                try:
                    super(CacheDelegate, self).paint(p, opt, index)
                finally:
                    p.end()
                if len(cls._cache) > 4000:
                    cls._cache.clear()
                cls._cache[key] = pm
            painter.drawPixmap(option.rect, pm)

    return CacheDelegate(view)


def _swap_delegate(table, variant):
    if variant == "default":
        from qfluentwidgets.components.widgets.table_view import TableItemDelegate
        table.setItemDelegate(TableItemDelegate(table))
    elif variant == "lean":
        from core.lean_table_delegate import LeanTableDelegate
        table.setItemDelegate(LeanTableDelegate(table))
    elif variant == "cache":
        table.setItemDelegate(_make_cache_delegate(table))
    # "prod"：保持构造后已装的委托不动（售后=OpsLeanDelegate；球桌页=库默认）
    QPixmapCache.clear()
    table.viewport().update()


# ==================== 测量 ====================

def _prep_scroll(table):
    from core.perf import apply_table_smooth_mode
    apply_table_smooth_mode(table)  # 默认配置 → NO_SMOOTH（用户口径：平滑滚动关）
    sb = table.verticalScrollBar()
    sb.setValue(0)
    table.viewport().repaint()
    return sb


def _measure(table, steps, rounds):
    """返回 (setValue 滚动 ms/步, 滚轮事件延迟 ms, paint 次/步)"""
    sb = _prep_scroll(table)
    # 预热：剔除惰性布局/字体/缓存首次成本
    for v in (0, 2, 0, 2):
        sb.setValue(v)
        table.viewport().repaint()

    counter = _PaintCounter()
    table.viewport().installEventFilter(counter)
    max_v = sb.maximum()

    samples = []
    for _ in range(rounds):
        sb.setValue(0)
        table.viewport().repaint()
        t0 = time.perf_counter()
        for i in range(steps):
            sb.setValue(min(i + 1, max_v))
            table.viewport().repaint()
        samples.append((time.perf_counter() - t0) * 1000 / steps)
    ms_step = statistics.median(samples)

    # 滚轮单档延迟：发送一档 QWheelEvent（120 = 一格）→ 同步等 repaint 完成
    vp = table.viewport()
    pos = vp.rect().center()
    gpos = vp.mapToGlobal(pos)
    wheel_ms = []
    for _ in range(rounds * 3):
        sb.setValue(0)
        vp.repaint()
        ev = QWheelEvent(pos, gpos, QPoint(0, 0), QPoint(0, 120),
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                         Qt.ScrollPhase.ScrollUpdate, False)
        t0 = time.perf_counter()
        QApplication.sendEvent(vp, ev)
        vp.repaint()
        wheel_ms.append((time.perf_counter() - t0) * 1000)
    wheel = statistics.median(wheel_ms)

    # paint 计数（每步）
    counter.paints = 0
    sb.setValue(0)
    vp.repaint()
    n0 = counter.paints
    for i in range(steps):
        sb.setValue(min(i + 1, max_v))
        vp.repaint()
    paints = (counter.paints - n0) / steps
    table.viewport().removeEventFilter(counter)
    return ms_step, wheel, paints


class _PaintAreaCounter(QObject):
    """viewport paint 次数 / 绘制面积 / 最后一次 paint 时间戳

    与 _PaintCounter 的差别在**面积**：整视口重绘与「位块搬移 + 只绘新露出
    条带」的 paint 次数都是 1，只有面积能把两者分开——这正是真机滚动成本
    与历史口径差异的全部来源。
    """

    def __init__(self):
        super().__init__()
        self.reset()

    def reset(self):
        self.paints = 0
        self.area = 0
        self.first_ts = 0.0
        self.last_ts = 0.0

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Paint:
            r = ev.rect()
            now = time.perf_counter()
            if not self.paints:
                self.first_ts = now
            self.paints += 1
            self.area += r.width() * r.height()
            self.last_ts = now
        return False


def _drain_until_idle(counter, idle_s, deadline_s=2.0):
    """处理事件直到「最后一次 paint 之后静默 idle_s」

    返回 (是否静默退出, 承载最后一次 paint 的 processEvents 切片耗时 ms)。

    为什么返回值里要带「切片耗时」：paint 是在 processEvents 内部同步执行的，
    事件过滤器只能拿到「事件送达」的时刻（paint 开始前），拿不到结束时刻。
    若只用「送达时刻 + 强制空闲」当步耗时，会把整段光栅成本漏掉（曾实测
    出现「整视口重绘却 0.36ms/步」的假象）。paint 所在的这次 processEvents
    调用返回时，绘制必然已完成，其耗时即该帧的光栅成本下界。
    """
    t_end = time.perf_counter() + deadline_s
    paint_ms = 0.0
    while True:
        t_a = time.perf_counter()
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 5)
        t_b = time.perf_counter()
        if counter.first_ts >= t_a:      # 本次切片内发生了（最后一次）paint
            paint_ms = (t_b - t_a) * 1000
        now = t_b
        if counter.paints > 0 and now - counter.last_ts >= idle_s:
            return True, paint_ms
        if now >= t_end:
            return False, paint_ms


def _measure_natural(table, steps, rounds, idle_ms):
    """自然路径口径：只 setValue，不强制 repaint

    返回 dict(ms_per_step, paints_per_step, area_per_step, viewport_equiv,
    wheel_ms)。每一步的延迟 = setValue → 最后一次 paint 之后静默 idle_ms，
    再减去这段强制空闲（否则每步都被加上 idle_ms 的常数）。
    """
    sb = _prep_scroll(table)
    idle_s = max(idle_ms, 1.0) / 1000.0
    counter = _PaintAreaCounter()
    vp = table.viewport()
    vp.installEventFilter(counter)
    try:
        for v in (0, 2, 0, 2):          # 预热：惰性布局 / 字体 / 委托缓存
            sb.setValue(v)
            _drain_until_idle(counter, idle_s)  # noqa: 仅取静默
        vp_area = max(1, vp.width() * vp.height())
        max_v = sb.maximum()
        lat, first, raster, paints, areas = [], [], [], [], []
        for _ in range(rounds):
            sb.setValue(0)
            _drain_until_idle(counter, idle_s)
            for v in range(1, min(steps, max_v) + 1):  # noqa: C901
                counter.reset()
                t0 = time.perf_counter()
                sb.setValue(v)
                _ok, paint_ms = _drain_until_idle(counter, idle_s)
                lat.append((counter.first_ts - t0) * 1000 + paint_ms
                           if counter.first_ts else float("nan"))
                if counter.first_ts:
                    first.append((counter.first_ts - t0) * 1000)
                raster.append(paint_ms)
                paints.append(counter.paints)
                areas.append(counter.area)
        wheel = []
        for _ in range(max(3, rounds * 3)):
            sb.setValue(0)
            _drain_until_idle(counter, idle_s)  # noqa: 复位
            pos = vp.rect().center()
            gpos = vp.mapToGlobal(pos)
            ev = QWheelEvent(pos, gpos, QPoint(0, 0), QPoint(0, 120),
                             Qt.MouseButton.NoButton,
                             Qt.KeyboardModifier.NoModifier,
                             Qt.ScrollPhase.ScrollUpdate, False)
            counter.reset()
            t0 = time.perf_counter()
            QApplication.sendEvent(vp, ev)
            _ok, paint_ms = _drain_until_idle(counter, idle_s)
            wheel.append((counter.first_ts - t0) * 1000 + paint_ms
                         if counter.first_ts else float("nan"))
        return {
            "ms_per_step": statistics.median(lat),
            "first_paint_ms": statistics.median(first) if first else float("nan"),
            "raster_ms": statistics.median(raster),
            "paints_per_step": statistics.median(paints),
            "area_per_step": statistics.median(areas),
            "viewport_equiv": statistics.median(areas) / vp_area,
            "wheel_ms": statistics.median(wheel),
        }
    finally:
        vp.removeEventFilter(counter)


def _out_natural(nat, indent="          "):
    """打印自然口径一行（与强制口径同表并列）"""
    out(f"{indent}↳ 自然路径（不强制 repaint）：{nat['ms_per_step']:7.2f} ms/步"
        f"（响应 {nat['first_paint_ms']:5.2f} + 光栅 {nat['raster_ms']:6.2f}）   "
        f"滚轮单档 {nat['wheel_ms']:7.2f} ms   "
        f"paint {nat['paints_per_step']:.2f} 次/步   "
        f"绘制面积 {nat['area_per_step']:,.0f} px²/步"
        f"（{nat['viewport_equiv']:.3f} 视口当量/步）")


def _probe_font():
    out("\n== 〇、环境与 CJK 字体验证 ==")
    from PySide6.QtGui import QGuiApplication
    scr = QGuiApplication.primaryScreen()
    out(f"scale={_args.scale}  devicePixelRatio={scr.devicePixelRatio()}  "
        f"platform={os.environ.get('QT_QPA_PLATFORM')}")
    f_def = QFont()
    info_def = QFontInfo(f_def)
    out(f"默认字体: family={info_def.family()} pointSize={info_def.pointSize()}")
    f_cn = QFont("Microsoft YaHei")
    info_cn = QFontInfo(f_cn)
    adv_cn = QFontMetrics(f_cn).horizontalAdvance("球房名称击球点位偏移校准")
    adv_lat = QFontMetrics(f_cn).horizontalAdvance("ABCDEFGHIJKLMN")
    out(f"雅黑解析: family={info_cn.family()}  中文串宽={adv_cn}px  "
        f"等长拉丁串宽={adv_lat}px  （相等≈CJK 未命中，字形是方框）")


def _measure_page(title, build_fn, populate_fn, variants, steps, rounds):
    out(f"\n== {title}（{steps} 步/轮 × {rounds} 轮中位） ==")
    for variant, desc in variants:
        page = build_fn()
        table = page._table
        _swap_delegate(table, variant)
        populate_fn(page)
        nat = _measure_natural(table, steps, rounds, _args.idle_ms) \
            if _args.truth else None
        ms_step, wheel, paints = _measure(table, steps, rounds)
        page.deleteLater()
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        out(f"{variant:8s} {desc:30s} {ms_step:8.2f} ms/步   滚轮单档 {wheel:8.2f} ms"
            f"   paint {paints:.1f} 次/步")
        if nat is not None:
            _out_natural(nat)


def _set_visible_cols(table, n):
    """隐藏第 n 列之后的全部列（模拟用户在筛选里只勾 N 列）"""
    hh = table.horizontalHeader()
    total = hh.count()
    for i in range(total):
        table.setColumnHidden(i, i >= n)


def _scan_cols(title, build_fn, populate_fn, cols_list, steps, rounds):
    """列数扫描：同一页面在不同可见列数下的滚动延迟（prod/lean 两变体）"""
    out(f"\n== {title}——可见列数扫描（{steps} 步/轮 × {rounds} 轮中位） ==")
    out(f"{'列数':>4s} {'变体':8s} {'ms/步':>10s} {'滚轮单档':>10s}   paint 次/步")
    for n in cols_list:
        for variant in ("prod", "lean"):
            page = build_fn()
            table = page._table
            populate_fn(page)
            _set_visible_cols(table, n)
            _swap_delegate(table, variant)
            nat = _measure_natural(table, steps, rounds, _args.idle_ms) \
                if _args.truth else None
            ms_step, wheel, paints = _measure(table, steps, rounds)
            page.deleteLater()
            app.processEvents()
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            out(f"{n:4d} {variant:8s} {ms_step:10.2f} {wheel:10.2f}   {paints:.1f}")
            if nat is not None:
                _out_natural(nat, indent="     ")


def main() -> int:
    from qfluentwidgets.components.widgets.table_view import TableItemDelegate
    if _args.blit is not None:
        # 直接改内存开关，绝不写 config（A/B 用）
        import core.perf as _perf
        _perf._table_blit_enabled = (_args.blit == 'on')
        _perf.patch_table_scroll_blit()
        _perf.apply_table_blit_globally()
    _probe_font()

    # ---- 售后记录页 ----
    from windows.aftersale.records import RecordsPage

    def build_as():
        page = RecordsPage()
        page._cycles_loaded = True   # 短路 showEvent 加载链（与 scroll_profile 同口径）
        page._recalc_done = True
        page.resize(_W, _H)
        page.show()
        app.processEvents()
        return page

    def pop_as(page):
        rows = []
        for i in range(60):
            day = "2026/09/24"
            rows.append({"id": i + 1, "created_at": f"{day} 10:00:00",
                         "occurred_at": day.replace("/", "-"),
                         "creator": f"填写人{i % 8}", "issue_type": "硬件问题",
                         "table_no": f"289-{i % 40:02d}", "room_name": f"球房{i % 25}",
                         "region": f"区域{i % 6}",
                         "problem": "击球点位偏移需要重新校准定位器水平仪",
                         "cause": "长期使用磨损导致安装面形变",
                         "resolved": "是" if i % 3 == 0 else "否", "is_initiative": "否",
                         "is_our_problem": "是",
                         "solution": "重新校准并紧固全部螺丝后复测验证走位精度恢复正常",
                         "resolver": f"解决人{i % 5}", "response_time": "30分钟",
                         "snk_code": "", "device_code": f"DEV{i % 40}",
                         "cycle_start": day, "remark": ""})
        page._populate(rows)
        app.processEvents()

    if _args.page in ("all", "aftersale"):
        _measure_page("一、售后记录页 RecordsPage（60 行 × 13 列）",
                      build_as, pop_as,
                      [("prod", "生产现状（OpsLeanDelegate）"),
                       ("default", "qfw 默认（性能优化全关）"),
                       ("lean", "LeanTableDelegate 轻量委托"),
                       ("cache", "单元格绘制缓存（候选）")],
                      _args.steps, _args.rounds)
    else:
        build_as  # noqa: B018  （ aftersale 段被 --page 跳过）

    # ---- 管理面板球桌页 ----
    import windows.management.table_page as tp

    def build_mgmt():
        page = tp.TablePage()
        page.resize(_W, _H)
        page.show()
        return page

    def pop_mgmt(page):
        from windows.management.common import TABLE_COLUMNS
        rows = []
        for i in range(60):
            d = {k: (f"289-{i:02d}" if k == "name" else f"球房{i % 25}" if k == "roomName"
                     else f"v{i}") for (k, _t, _w) in TABLE_COLUMNS}
            d.update({"code": f"DEV{i}", "snk_code": f"snk_{i}" if i % 2 else "",
                      "todesk_status": "1" if i % 2 else "0"})
            rows.append(d)
        page._populate(rows)
        app.processEvents()

    if _COLS:
        if _args.page in ("all", "aftersale"):
            _scan_cols("一、售后记录页 RecordsPage", build_as, pop_as,
                       _COLS, _args.steps, _args.rounds)
        if _args.page in ("all", "mgmt"):
            _scan_cols("二、管理面板球桌页 TablePage", build_mgmt, pop_mgmt,
                       _COLS, _args.steps, _args.rounds)
        out("\n说明：列数扫描通过 setColumnHidden 模拟用户筛选勾选 N 列；"
            "可见列数 × 可见行数 = 每帧绘制的单元格数，lean 每格仍有固定成本。")
        path = os.path.join(OUT_DIR, f"perf_scroll_latency_cols_{_args.scale}_{_W}x{_H}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(_LINES) + "\n")
        print(f"\n结果已写 {path}")
        return 0

    if _args.page in ("all", "mgmt"):
        _measure_page("二、管理面板球桌页 TablePage（60 行）",
                      build_mgmt, pop_mgmt,
                      [("prod", "生产现状（构造后委托）"),
                       ("default", "qfw 默认（性能优化全关）"),
                       ("lean", "LeanTableDelegate 轻量委托"),
                       ("cache", "单元格绘制缓存（候选）")],
                      _args.steps, _args.rounds)

    out("\n说明：offscreen 软件光栅为 CPU 侧成本；QT_SCALE_FACTOR 模拟真机 DPI 缩放"
        "（光栅面积 ≈ dpr²）。滚轮单档延迟为「wheel 事件处理 + 全视口重绘」实测。")
    path = os.path.join(OUT_DIR, f"perf_scroll_latency_{_args.scale}_{_W}x{_H}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(_LINES) + "\n")
    print(f"\n结果已写 {path}")
    return 0


if __name__ == "__main__":
    rc = main()
    os._exit(rc)
