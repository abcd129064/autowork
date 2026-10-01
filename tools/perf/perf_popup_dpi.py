# -*- coding: utf-8 -*-
"""弹窗/页面切换动画与大屏 DPI 光栅成本 harness（专项调查 2026-09-25）。

背景：用户在三类真机反馈卡顿（27 寸 2K/4K 台式机轻微卡、14 寸 2K 笔记本
正常、14 寸笔记本外接 55 寸电视严重卡甚至「未响应」）。本 harness 在
offscreen 下量化四条逐帧渲染路径随「系统缩放 × 窗口逻辑尺寸」的放大：

  1. 整窗重绘（grab）——动画每帧光栅成本的地板代理；
  2. 页面切换 PopUpAniStackedWidget 动画（面板 switchTo，双页滑动 230ms）；
  3. 菜单弹出动画（RoundMenu，pos + windowOpacity 150~250ms）；
  4. 遮罩弹窗卡片动画（MaskDialogBase：整窗 QGraphicsOpacityEffect 200ms +
     卡片常驻 QGraphicsDropShadowEffect(60)）；
  5. 亚克力高斯模糊基准（qfw image_utils 与打包环境 PIL 替代实现对照，
     blurPicSize=None → 全分辨率不降采样）。

场景换算（QT_SCALE_FACTOR × 逻辑窗口 = 物理光栅面积）：
  --scale 1.0 --win 1600x900   窗口化基线（~144 万物理像素）
  --scale 1.0 --win 2560x1440  27 寸 2K@100% 全屏（369 万）
  --scale 1.5 --win 1707x960   14 寸 2K@150% 全屏（369 万，场景 B）
  --scale 1.5 --win 2560x1440  55 寸 4K@150% 全屏（829 万，场景 C）
  --scale 2.0 --win 2560x1440  4K@200% 极端（1230 万）

用法：python tools/perf/perf_popup_dpi.py [--scale 1.0] [--win 1600x900]
产物：tools/_scratch/perf_popup_dpi_<scale>_<win>.txt（不入库）

隔离措施：backend 强制 SQLite、table_db.DB_PATH/get_app_dir 重定向 scratch、
load_cycle_mode 桩（不碰真实 config/database）。不安装 core.perf 的动画补丁，
保持测「库原生动画路径」；优化后口径对照见报告。
"""
import os
import sys
import argparse

# ---- Qt 运行时引导（docs/Qt内联引导说明.md 标准模板，必须在 import PySide6 前） ----
import os as _os
import importlib.util as _qt_iu
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
                        _os.add_dll_directory(_d)
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
_ap.add_argument('--win', default='1600x900', help='面板窗口逻辑尺寸 WxH')
_args = _ap.parse_args()

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["QT_SCALE_FACTOR"] = str(_args.scale)

# tools/perf/ → 上溯三层 = 仓库根（AGENTS §2.4）
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

OUT_DIR = os.path.join(ROOT, "tools", "_scratch")
os.makedirs(OUT_DIR, exist_ok=True)

import time
import sqlite3
import statistics
from datetime import datetime, timedelta

import core.acrylic_patch  # noqa: F401  （打包环境 PIL 亚克力注入；开发环境无副作用）
from PySide6.QtCore import Qt, QPoint, QObject, QEvent, QTimer, QEventLoop
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QApplication, QGraphicsOpacityEffect,
                               QWidget, QTableWidgetItem)

from database import backend, table_db, schema

backend._mysql_settings_cache = {"enabled": False}

DB_PATH = os.path.join(OUT_DIR, "perf_popup_tmp.db")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
table_db.DB_PATH = DB_PATH
table_db._conn = None

APP_DIR = os.path.join(OUT_DIR, "perf_popup_appdir")
os.makedirs(APP_DIR, exist_ok=True)


def _stub_get_app_dir(*a, **k):
    return APP_DIR


import core.app_paths
core.app_paths.get_app_dir = _stub_get_app_dir
for _m in list(sys.modules.values()):
    if _m is not None and getattr(_m, "get_app_dir", None) is not None \
            and hasattr(_m, "__name__") and _m.__name__.startswith(("windows.", "core.")):
        _m.get_app_dir = _stub_get_app_dir


def _build_db(n_tables=1000):
    """与 perf_scroll_latency 同口径的最小造数（够 TablePage 首查即可）"""
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


_build_ts = time.perf_counter()
_build_db()
table_db.query_page(1, 1, "")  # 预热 _ensure_initialized + FTS rebuild（一次性）

import database.aftersale_db as adb
adb.load_cycle_mode = lambda: {"type": "mon", "start": "", "span": 7}

app = QApplication.instance() or QApplication(sys.argv)

_LINES = []


def out(msg=""):
    print(msg, flush=True)
    _LINES.append(msg)


def _med(fn, repeat=5):
    ts = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts), ts


class _PaintTimer(QObject):
    """记录 Paint 事件时间戳（挂在目标 widget 的 viewport/window 上）"""

    def __init__(self):
        super().__init__()
        self.t0 = None
        self.stamps = []

    def reset(self):
        self.t0 = time.perf_counter()
        self.stamps = []

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Paint:
            self.stamps.append(time.perf_counter())
        return False


def _run_frames(widget, dur_ms, pump=8):
    """驱动事件循环 dur_ms，统计 Paint 帧序列（含帧间隔中位）"""
    pt = _PaintTimer()
    widget.installEventFilter(pt)
    pt.reset()
    loop = QEventLoop()
    QTimer.singleShot(dur_ms, loop.quit)
    loop.exec()
    widget.removeEventFilter(pt)
    n = len(pt.stamps)
    if n >= 3:
        spans = [(b - a) * 1000 for a, b in zip(pt.stamps, pt.stamps[1:])]
        iv = statistics.median(spans)
        wall = (pt.stamps[-1] - pt.stamps[0]) * 1000
        return n, iv, wall / max(1, n - 1)
    return n, 0.0, 0.0


# ==================== 场景构建 ====================

out("== 〇、环境 ==")
scr = app.primaryScreen()
out(f"scale={_args.scale}  dpr={scr.devicePixelRatio()}  platform={os.environ.get('QT_QPA_PLATFORM')}")
out(f"字体库: {len(QFontDatabase.families())} 个 family（offscreen 为 0 属预期，文本成本被低估）")
_w, _h = (int(x) for x in _args.win.lower().split("x"))
phys_w, phys_h = int(_w * _args.scale), int(_h * _args.scale)
out(f"窗口逻辑 {_w}x{_h} → 物理 {phys_w}x{phys_h}（{phys_w * phys_h / 1e4:.0f} 万像素）")

# 面板窗口（与管理面板真机同构；TablePage 为落地页）
from windows.management.window import ManagementPanelWindow
from qfluentwidgets import RoundMenu, MessageBox, MenuAnimationType, Action, FluentIcon

panel = ManagementPanelWindow()
panel.resize(_w, _h)
panel.show()
app.processEvents()
# 预热首查/填充（N1 后日历缓存延后，不影响本测量）
app.processEvents()
time.sleep(0.2)
app.processEvents()

pages = panel.stackedWidget
cur = pages.currentWidget()
others = [pages.widget(i) for i in range(pages.count()) if pages.widget(i) is not cur]
other = others[0] if others else cur

# ==================== 一、整窗重绘（grab） ====================

out("\n== 一、整窗重绘 grab()（动画每帧光栅成本的地板代理，5 次中位） ==")
ms, _ = _med(lambda: panel.grab())
out(f"面板窗口 grab: {ms:.1f} ms")
ms2, _ = _med(lambda: other.grab())
out(f"非落地页 grab: {ms2:.1f} ms")

# ==================== 二、页面切换动画 ====================

out("\n== 二、页面切换动画（PopUpAniStackedWidget，230ms，原生未打补丁） ==")
# 预热目标页（懒构建等一次性成本）
pages.setCurrentWidget(other, popOut=False)
app.processEvents()
time.sleep(0.1)
app.processEvents()
pages.setCurrentWidget(cur, popOut=False)
app.processEvents()


def _switch():
    pages.setCurrentWidget(other, popOut=True)


pt = _PaintTimer()
pages.installEventFilter(pt)
t0 = time.perf_counter()
_switch()
pt.reset()
loop = QEventLoop()
QTimer.singleShot(450, loop.quit)
loop.exec()
pages.removeEventFilter(pt)
n = len(pt.stamps)
wall = (pt.stamps[-1] - pt.stamps[0]) * 1000 if n >= 2 else 0
out(f"切换动画 Paint 帧数={n}  帧间隔中位={statistics.median([(b - a) * 1000 for a, b in zip(pt.stamps, pt.stamps[1:])]) if n >= 3 else 0:.1f} ms  动画期均帧成本={wall / max(1, n - 1):.1f} ms")
out(f"（60fps 预算 16.7 ms/帧；每帧即「两页整窗」光栅）")

# ==================== 三、菜单弹出动画 ====================

out("\n== 三、菜单弹出动画（RoundMenu 10 项，DROP_DOWN + windowOpacity） ==")
menu = RoundMenu(parent=panel)
for i in range(10):
    menu.addAction(Action(FluentIcon.SETTING, f"菜单项 {i}"))
menu.adjustSize()


def _menu_frames(ani_type):
    pt = _PaintTimer()
    menu.installEventFilter(pt)
    pt.reset()
    menu.exec(panel.rect().center(), ani=True, aniType=ani_type)
    loop = QEventLoop()
    QTimer.singleShot(500, loop.quit)
    loop.exec()
    menu.close()
    app.processEvents()
    menu.removeEventFilter(pt)
    s = pt.stamps
    if len(s) >= 3:
        wall = (s[-1] - s[0]) * 1000
        return len(s), wall / max(1, len(s) - 1)
    return len(s), 0.0


n_on, ms_on = _menu_frames(MenuAnimationType.DROP_DOWN)
n_off, ms_off = _menu_frames(MenuAnimationType.NONE)
out(f"动画开: 帧数={n_on}  均帧={ms_on:.1f} ms")
out(f"动画关: 帧数={n_off}  均帧={ms_off:.1f} ms")
ms, _ = _med(lambda: menu.repaint())
out(f"菜单静置单帧重绘: {ms:.1f} ms")

# ==================== 四、遮罩弹窗卡片动画 ====================

out("\n== 四、遮罩弹窗（MessageBox=MaskDialogBase：整窗 opacity 动画+卡片常驻阴影） ==")


def _dialog_frames():
    dlg = MessageBox("新增记录", "内容字段" * 30, panel)
    pt = _PaintTimer()
    dlg.installEventFilter(pt)
    pt.reset()
    dlg.show()
    loop = QEventLoop()
    QTimer.singleShot(400, loop.quit)
    loop.exec()
    dlg.setGraphicsEffect(None)
    dlg.done(0)
    app.processEvents()
    dlg.removeEventFilter(pt)
    s = pt.stamps
    if len(s) >= 3:
        wall = (s[-1] - s[0]) * 1000
        return len(s), wall / max(1, len(s) - 1)
    return len(s), 0.0


n_d, ms_d = _dialog_frames()
out(f"动画开（opacity 200ms）: 帧数={n_d}  均帧={ms_d:.1f} ms")

# 静态对照：opacity effect 挂上 vs 摘掉，各 grab 5 次
dlg2 = MessageBox("新增记录", "内容字段" * 30, panel)
dlg2.show()
app.processEvents()
eff = QGraphicsOpacityEffect(dlg2)
dlg2.setGraphicsEffect(eff)
eff.setOpacity(0.99)
app.processEvents()
ms_eff, _ = _med(lambda: dlg2.grab())
dlg2.setGraphicsEffect(None)
app.processEvents()
ms_no, _ = _med(lambda: dlg2.grab())
dlg2.done(0)
app.processEvents()
out(f"弹窗静态渲染: 挂 opacity effect {ms_eff:.1f} ms/帧 → 摘掉 {ms_no:.1f} ms/帧"
    f"（effect 离屏放大 {ms_eff / max(0.01, ms_no):.1f}×）")

# ==================== 五、亚克力模糊基准 ====================

out("\n== 五、亚克力高斯模糊（radius 18，blurPicSize=None=全分辨率） ==")
from qfluentwidgets.common.image_utils import gaussianBlur as blur_qfw
from PySide6.QtGui import QPixmap


# 打包环境路径：core.acrylic_patch 的 PIL 实现（numpy/scipy 被排除时注入）。
# 开发环境 numpy/scipy 可用 → acrylic_patch 不注入，这里内联同语义实现以保证口径。
def _blur_pil(image, blurRadius=18, brightFactor=1, blurPicSize=None):
    from PIL import Image, ImageFilter, ImageEnhance
    from PIL.ImageQt import fromqpixmap
    if isinstance(image, str) and not image.startswith(':'):
        pil_img = Image.open(image)
    else:
        pil_img = fromqpixmap(image)
    if blurPicSize:
        w, h = pil_img.size
        ratio = min(blurPicSize[0] / w, blurPicSize[1] / h)
        if ratio < 1:
            pil_img = pil_img.resize((int(w * ratio), int(h * ratio)))
    pil_img = pil_img.convert('RGB')
    pil_img = pil_img.filter(ImageFilter.GaussianBlur(radius=blurRadius))
    if brightFactor != 1:
        pil_img = ImageEnhance.Brightness(pil_img).enhance(brightFactor)
    data = pil_img.tobytes('raw', 'RGB')
    w, h = pil_img.size
    from PySide6.QtGui import QImage
    qimg = QImage(data, w, h, 3 * w, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg)


for tag, (bw, bh) in [("导航栏(400x1440逻辑@1.5)", (600, 2160)),
                      ("4K 整屏(3840x2160)", (3840, 2160))]:
    pm = QPixmap(bw, bh)
    pm.fill(Qt.GlobalColor.darkGray)
    t0 = time.perf_counter()
    blur_qfw(pm, blurRadius=18)
    ms_qfw = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    _blur_pil(pm, blurRadius=18)
    ms_pil = (time.perf_counter() - t0) * 1000
    out(f"{tag} ({bw}x{bh}): qfw实现(scipy) {ms_qfw:.0f} ms  PIL替代(打包环境) {ms_pil:.0f} ms"
        + ("  ← 单次主线程阻塞" if max(ms_qfw, ms_pil) > 500 else ""))

# ==================== 六、汇总 ====================

out("\n== 六、判定参考 ==")
out("物理像素数 = 逻辑窗口 × scale²；上表 grab/均帧数值随物理像素近线性放大。")
out("单帧 >50ms 即明显掉帧；弹窗 opacity 动画期每帧都走整窗离屏渲染。")
out("亚克力模糊在主线程同步执行（导航展开 grabImage → gaussianBlur），")
out("打包环境为 PIL 实现；4K 全分辨率单次数百 ms~秒级即「转一下就未响应」的实体。")

# ---- 落盘 ----
snap = os.path.join(OUT_DIR, f"perf_popup_dpi_{_args.scale}_{_w}x{_h}.txt")
with open(snap, "w", encoding="utf-8") as f:
    f.write("\n".join(_LINES) + "\n")
out(f"\n快照已写: tools/_scratch/{os.path.basename(snap)}")

# 清理本次临时库
try:
    os.remove(DB_PATH)
except OSError:
    pass

raise SystemExit(0)
