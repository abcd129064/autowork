# -*- coding: utf-8 -*-
"""云母（Mica）真凶二分（渲染级判据）

前置结论：
  - 壁纸换洋红对照法已证：纯净窗 Mica 在渲染（洋红偏移 R-G=22），
    完整主窗口不渲染（偏移=0），且 DWM backdrop 属性回读值相同(=2)
    => 凶手藏在读不回的 DWM 状态（accent policy / 扩展边距）。
  - 属性级二分已失效，本轮改用【渲染级判据】：洋红壁纸常驻，
    每个实验窗显示后抓标题栏条带 RGB，R-G>8 即「Mica 在渲染」。

窗口（同一位置先后显示，洋红壁纸全程不变）：
  W0  纯净对照（必须为粉，否则技术失效）
  S1  + FluentTitleBar(34px)
  S3  + setStyleSheet(dark.qss)
  S4  + 全局字体递归
  S5  + 导航亚克力
  S6  + homeInterface别名 + Ui_MainWindow.setupUi
  S7  + resize(1500, 900)
  S8  + addSubInterface×4 + 导航展开/折叠设置
  S2  + setTheme(DARK)+ColorScheme（全局）
  FULL 完整 MainWindow（必须不为粉——负对照）

首个「不变粉」的窗口即真凶步骤。结束后立即恢复原壁纸。

用法（真机）：
  <venv>/python.exe tools/probe_mica_steps_pink.py
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

from PySide6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout  # noqa: E402
from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QFont, QImage  # noqa: E402

app = QApplication(sys.argv)


def flush():
    sys.stdout.flush()
    sys.stderr.flush()


def settle(sec=1.4):
    end = time.time() + sec
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


# ==================== 壁纸临时切换 ====================
from ctypes import windll, create_unicode_buffer  # noqa: E402

_user32 = windll.user32
SPI_GETDESKWALLPAPER = 0x0073
SPI_SETDESKWALLPAPER = 0x0014
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02

_buf = create_unicode_buffer(500)
_ok = _user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, 500, _buf, 0)
ORIG_WALLPAPER = _buf.value if _ok else ""
print(f"[壁纸] 原壁纸 = {ORIG_WALLPAPER or '(空——可能是纯色壁纸)'}")
flush()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAGENTA = os.path.join(ROOT, "tools", "_mica_captures", "_magenta.bmp")
os.makedirs(os.path.dirname(MAGENTA), exist_ok=True)
QImage(64, 64, QImage.Format_RGB32).fill(0xFF00FF)
QImage(64, 64, QImage.Format_RGB32).save(MAGENTA) if False else None
_mg = QImage(64, 64, QImage.Format_RGB32)
_mg.fill(0xFF00FF)
_mg.save(MAGENTA)

assert _user32.SystemParametersInfoW(
    SPI_SETDESKWALLPAPER, 0, MAGENTA, SPIF_UPDATEINIFILE | SPIF_SENDCHANGE), \
    "换洋红壁纸失败"
print("[壁纸] 已切为洋红（全程保持，结束时恢复）")
settle(1.2)

# ==================== 抓屏判据 ====================
screen = app.primaryScreen()
DPR = screen.devicePixelRatio()
OUT_DIR = os.path.join(ROOT, "tools", "_mica_captures")


def strip_rg(win):
    """标题栏条带平均 RGB 与 R-G 偏移"""
    settle(1.4)
    img = screen.grabWindow(0).toImage()
    fg = win.frameGeometry()
    gx, gy = int(fg.x() * DPR), int(fg.y() * DPR)
    x0 = gx + int(win.width() * 0.30 * DPR)
    x1 = gx + int(win.width() * 0.70 * DPR)
    Y0 = gy + int(win.height() * 0.012 * DPR)
    Y1 = gy + int(win.height() * 0.045 * DPR)
    rs = gs = bs = n = 0
    for yy in range(Y0, Y1, 2):
        for xx in range(x0, x1, 2):
            c = img.pixelColor(xx, yy)
            rs += c.red(); gs += c.green(); bs += c.blue(); n += 1
    if not n:
        return None
    r, g, b = rs // n, gs // n, bs // n
    return r, g, b, (r - g)


def check(tag, win):
    r = strip_rg(win)
    if r is None:
        print(f"  {tag:<22} 无数据")
        return False
    rgb, d = r[:3], r[3]
    alive = d > 8
    print(f"  {tag:<22} rgb={rgb} R-G={d:<4} -> "
          f"{'MICA 在渲染 ✓' if alive else 'MICA 已死 ✗'}")
    flush()
    return alive


# ==================== 实验窗口 ====================
from qfluentwidgets import (FluentWindow, FluentIcon, FluentTitleBar,  # noqa: E402
                            setTheme, Theme)
from qfluentwidgets.common.style_sheet import isDarkTheme  # noqa: E402

POS = (60, 60)


class StepWindow(FluentWindow):
    """最简 FluentWindow + 透明页；由外部按步骤打补丁"""

    def __init__(self, tag):
        super().__init__()
        self.setObjectName(f"w_{tag}")
        page = QLabel("probe", self)
        page.setObjectName(f"p_{tag}")
        page.setAlignment(Qt.AlignCenter)
        self.addSubInterface(page, FluentIcon.HOME, tag)
        self.setWindowTitle(tag)
        self.resize(700, 480)


def _set_font_recursive(widget, font):
    widget.setFont(font)
    for child in widget.findChildren(QWidget):
        child.setFont(font)
        child.update()


results = []


def run(tag, patch=None):
    w = StepWindow(tag)
    if patch is not None:
        try:
            patch(w)
        except Exception as e:
            print(f"  [!] {tag} 步骤异常: {e!r}")
            flush()
    w.move(*POS)
    w.show()
    alive = check(tag, w)
    results.append((tag, alive))
    w.hide()
    w.deleteLater()
    return alive


print("\n======== 渲染级二分（洋红壁纸下盯 R-G） ========")
flush()

run("W0纯净对照")
run("S1标题栏", lambda w: (w.setTitleBar(FluentTitleBar(w)),
                           w.titleBar.setFixedHeight(34),
                           w.titleBar.raise_()))


def _s3(w):
    with open(os.path.join(ROOT, "styles", "dark.qss"), encoding="utf-8") as f:
        w.setStyleSheet(f.read())


run("S3全局QSS", _s3)


def _s4(w):
    f = QFont(app.font())
    f.setPixelSize(16)
    _set_font_recursive(w, f)


run("S4字体递归", _s4)
run("S5导航亚克力", lambda w: w.navigationInterface.setAcrylicEnabled(True))


def _s6(w):
    w.homeInterface = QWidget(w)
    w.homeInterface.setObjectName("homeInterface")
    w.home_vbox = QVBoxLayout(w.homeInterface)
    w.home_vbox.setContentsMargins(0, 0, 0, 0)
    w.home_vbox.setSpacing(0)
    w.vBoxLayout = w.home_vbox
    from autowork_with_table import Ui_MainWindow
    Ui_MainWindow().setupUi(w)


run("S6setupUi", _s6)
run("S7尺寸1500", lambda w: w.resize(1500, 900))


def _s8(w):
    for i in range(3):
        pg = QLabel(f"page{i}", w)
        pg.setObjectName(f"pg{i}_{w.objectName()}")
        w.addSubInterface(pg, FluentIcon.HOME, f"P{i}")
    w.navigationInterface.setExpandWidth(200)
    w.navigationInterface.setCollapsible(True)


run("S8导航多页", _s8)


def _s2(w):
    setTheme(Theme.DARK)
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark)


run("S2主题DARK", _s2)

print("构建 FULL（完整 MainWindow，稍慢）...")
flush()
from main_window import MainWindow  # noqa: E402

full = MainWindow()
full.setWindowTitle("FULL主窗口")
full.move(*POS)
full.show()
alive = check("FULL完整主窗口", full)
results.append(("FULL完整主窗口", alive))

# ---- FULL 修复实验：重设 Mica ----
print("\n---- FULL 修复实验 ----")
h = int(full.winId())
full.windowEffect.removeBackgroundEffect(h)
full.windowEffect.setMicaEffect(h, isDarkTheme())
alive1 = check("FULL+R1重设Mica", full)
results.append(("FULL+R1重设Mica", alive1))
full.windowEffect.setMicaEffect(h, isDarkTheme(), isAlt=True)
alive2 = check("FULL+R2改MicaAlt", full)
results.append(("FULL+R2改MicaAlt", alive2))

# ---- 恢复壁纸 ----
restored = False
if ORIG_WALLPAPER and os.path.isfile(ORIG_WALLPAPER):
    restored = bool(_user32.SystemParametersInfoW(
        SPI_SETDESKWALLPAPER, 0, ORIG_WALLPAPER,
        SPIF_UPDATEINIFILE | SPIF_SENDCHANGE))
print(f"\n[壁纸] 恢复原壁纸：{'成功' if restored else '失败！请手动重设壁纸'}")

print("\n======== 结果汇总 ========")
for tag, a in results:
    print(f"  {'✓' if a else '✗'} {tag}")
flush()
full.hide()
QTimer.singleShot(300, app.quit)
sys.exit(app.exec())
