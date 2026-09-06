# -*- coding: utf-8 -*-
"""QSS 杀 Mica：机制验证 + 修复方案验证（单窗口活体实验）

实验序列（洋红壁纸判据）：
  1 基线                     期望 ✓
  2 setStyleSheet(dark.qss)  期望 ✗（复现杀手）
     记录 HWND / hasAlpha 是否变化（表面重建?）
  3 复活A：重设 setMicaEffect 期望 ✗（DWM 属性无效——表面问题）
  4 复活B：hide+show          （showEvent 会 reapply Mica）
  5 复活C：setWindowFlags 重建原生窗 + show
  6 修复方案验证：新窗口把 qss 挂到 stackedWidget（合并 qfw 自身 qss）
     —— 子控件样式不受影响，窗口表面保持 alpha => 期望 ✓

用法（真机）：
  <venv>/python.exe tools/probe_mica_qss_fix.py
"""
import os
import sys
import time

import importlib.util as _qt_iu  # noqa: E402
_qt_handles = []
try:
    _qt_spec = _qt_iu.find_spec("PySide6")
    if _qt_spec is not None:
        _qt_locs = list(getattr(_qt_spec, "submodule_search_locations", None) or [])
        if _qt_locs:
            _qt_pkg = _qt_locs[0]
            for _d in (_qt_pkg, os.path.dirname(_qt_pkg),
                       os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                    "System32")):
                if os.path.isdir(_d):
                    try:
                        _qt_handles.append(os.add_dll_directory(_d))
                    except OSError:
                        pass
            os.environ["QT_PLUGIN_PATH"] = os.path.join(_qt_pkg, "plugins")
            os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH",
                                  os.path.join(_qt_pkg, "plugins", "platforms"))
except Exception:
    pass

if "QT_QPA_PLATFORM" in os.environ:
    del os.environ["QT_QPA_PLATFORM"]

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QWidget, QLabel  # noqa: E402
from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402

app = QApplication(sys.argv)


def flush():
    sys.stdout.flush()
    sys.stderr.flush()


def settle(sec=1.2):
    end = time.time() + sec
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


from ctypes import windll, create_unicode_buffer  # noqa: E402

_user32 = windll.user32
SPI_GETDESKWALLPAPER = 0x0073
SPI_SETDESKWALLPAPER = 0x0014
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02

_buf = create_unicode_buffer(500)
_ok = _user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, 500, _buf, 0)
ORIG_WALLPAPER = _buf.value if _ok else ""
print(f"[壁纸] 原壁纸 = {ORIG_WALLPAPER or '(空)'}")
flush()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "tools", "_mica_captures")
os.makedirs(OUT_DIR, exist_ok=True)
MAGENTA = os.path.join(OUT_DIR, "_magenta.bmp")
_mg = QImage(64, 64, QImage.Format_RGB32)
_mg.fill(0xFF00FF)
_mg.save(MAGENTA)

assert _user32.SystemParametersInfoW(
    SPI_SETDESKWALLPAPER, 0, MAGENTA, SPIF_UPDATEINIFILE | SPIF_SENDCHANGE)
print("[壁纸] 已切为洋红")
settle(1.0)

screen = app.primaryScreen()
DPR = screen.devicePixelRatio()


def strip_state(win):
    settle(1.2)
    img = screen.grabWindow(0).toImage()
    fg = win.frameGeometry()
    x0 = int(fg.x() * DPR) + int(win.width() * 0.30 * DPR)
    x1 = int(fg.x() * DPR) + int(win.width() * 0.70 * DPR)
    Y0 = int(fg.y() * DPR) + int(win.height() * 0.012 * DPR)
    Y1 = int(fg.y() * DPR) + int(win.height() * 0.045 * DPR)
    rs = gs = n = 0
    for yy in range(Y0, Y1, 2):
        for xx in range(x0, x1, 2):
            c = img.pixelColor(xx, yy)
            rs += c.red(); gs += c.green(); n += 1
    rg = (rs - gs) // n if n else 0
    hWnd = int(win.winId())
    try:
        ha = win.windowHandle().format().hasAlpha()
    except Exception:
        ha = "?"
    alive = rg > 8
    print(f"    R-G={rg:<4} hWnd=0x{hWnd:X} hasAlpha={ha} "
          f"-> {'MICA✓' if alive else 'MICA✗'}")
    flush()
    return alive, hWnd


results = []

with open(os.path.join(ROOT, "styles", "dark.qss"), encoding="utf-8") as f:
    QSS_ALL = f.read()

from qfluentwidgets import FluentWindow, FluentIcon  # noqa: E402
from qfluentwidgets.common.style_sheet import (FluentStyleSheet,  # noqa: E402
                                               isDarkTheme, Theme, qconfig)
from qframelesswindow.windows.window_effect import WindowsWindowEffect  # noqa: E402

w = FluentWindow()
w.setObjectName("mechWin")
page = QLabel("probe", w)
page.setObjectName("mechPage")
page.setAlignment(Qt.AlignCenter)
w.addSubInterface(page, FluentIcon.HOME, "mech")
w.resize(700, 480)
w.move(60, 60)
w.show()
settle(0.8)

print("1 基线")
results.append(("1 基线", strip_state(w)[0]))

print("2 setStyleSheet(dark.qss)")
w.setStyleSheet(QSS_ALL)
alive2, h2 = strip_state(w)
results.append(("2 全量qss", alive2))

print("3 复活A：重设 setMicaEffect（当前HWND）")
w.windowEffect.removeBackgroundEffect(h2)
w.windowEffect.setMicaEffect(h2, isDarkTheme())
results.append(("3 复活A重设Mica", strip_state(w)[0]))

print("4 复活B：hide+show（showEvent reapply）")
w.hide()
settle(0.3)
w.show()
results.append(("4 复活B hide+show", strip_state(w)[0]))

print("5 复活C：setWindowFlags 重建原生窗")
w.setWindowFlags(w.windowFlags())
w.show()
alive5, _ = strip_state(w)
results.append(("5 复活C flags重建", alive5))
if not alive5:
    h = int(w.winId())
    w.windowEffect.setMicaEffect(h, isDarkTheme())
    print("    flags重建后补一次 setMicaEffect")
    results.append(("5b 重建+重设Mica", strip_state(w)[0]))

# ==================== 修复方案验证 ====================
print("\n6 修复方案：qss 挂到 stackedWidget（合并 qfw FLUENT_WINDOW qss）")
w2 = FluentWindow()
w2.setObjectName("fixWin")
page2 = QLabel("fix-carrier", w2)
page2.setObjectName("fixPage")
page2.setAlignment(Qt.AlignCenter)
w2.addSubInterface(page2, FluentIcon.HOME, "fix")
w2.resize(700, 480)
w2.move(60, 60)
w2.show()
settle(0.8)

qfw_qss = FluentStyleSheet.FLUENT_WINDOW.content(qconfig.get(qconfig.themeMode))
# 合并：qfw 窗口级 qss（保住 StackedWidget 半透明规则） + 业务 qss
w2.stackedWidget.setStyleSheet(qfw_qss + "\n" + QSS_ALL)
settle(0.5)
alive6 = strip_state(w2)[0]
results.append(("6 qss挂stackedWidget", alive6))

# 中央区域也采一点（页面体上方），确认子控件仍被样式（QLabel 透明、无怪色）
print("    （另验证：标题栏按钮区/子控件样式是否正常——肉眼后续在真机复核）")

# ==================== 恢复壁纸 ====================
restored = False
if ORIG_WALLPAPER and os.path.isfile(ORIG_WALLPAPER):
    restored = bool(_user32.SystemParametersInfoW(
        SPI_SETDESKWALLPAPER, 0, ORIG_WALLPAPER,
        SPIF_UPDATEINIFILE | SPIF_SENDCHANGE))
print(f"\n[壁纸] 恢复原壁纸：{'成功' if restored else '失败！请手动重设壁纸'}")

print("\n======== 汇总 ========")
for tag, a in results:
    print(f"  {'✓' if a else '✗'} {tag}")
flush()
w.hide()
w2.hide()
QTimer.singleShot(300, app.quit)
sys.exit(app.exec())
