# -*- coding: utf-8 -*-
"""云母（Mica）终极判据：壁纸换色对照法

原理：
  Mica 底色由桌面壁纸实时派生（换壁纸立即刷新，无需重启）。
  把壁纸临时换成纯洋红色 (255,0,255)：
    -> 真正渲染了 Mica 的窗口：标题栏/透明页面区域会明显偏洋红（R-G 差值飙升）
    -> 没渲染 Mica 的窗口：颜色不变
  抓屏两次（换壁纸前/后）对比同区域 RGB，即可自动判定，并保存证据图。
  结束后立即恢复原壁纸。

窗口（同一位置先后显示）：
  A  纯净 FluentWindow（正对照：技术自身必须生效——A 不变粉则本探针无效）
  M  真实主窗口（复刻 main.py 环境：Fusion + 预设主题），分别测
     M-工作台页 与 M-运维管理Hub页（透明页面，关键观察点）

用法（真机）：
  <venv>/python.exe tools/probe_mica_wallpaper.py
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
from PySide6.QtGui import QFont, QPixmap, QImage  # noqa: E402

app = QApplication(sys.argv)


def flush():
    sys.stdout.flush()
    sys.stderr.flush()


def settle(sec=1.6):
    end = time.time() + sec
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


# ==================== 壁纸临时切换（保存→洋红→恢复） ====================
from ctypes import windll, create_unicode_buffer, byref  # noqa: E402

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

MAGENTA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_mica_captures", "_magenta.bmp")
os.makedirs(os.path.dirname(MAGENTA), exist_ok=True)
img = QImage(64, 64, QImage.Format_RGB32)
img.fill(0xFF00FF)  # 纯洋红
img.save(MAGENTA)


def set_wallpaper(path):
    return _user32.SystemParametersInfoW(
        SPI_SETDESKWALLPAPER, 0, path,
        SPIF_UPDATEINIFILE | SPIF_SENDCHANGE)


# ==================== 抓屏工具 ====================
screen = app.primaryScreen()
DPR = screen.devicePixelRatio()
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_mica_captures")


def region_rgb(win, y0f, y1f, xf0, xf1, screen_img, tag):
    """统计窗口逻辑区域平均 RGB；保存证据图；返回 (r,g,b)"""
    fg = win.frameGeometry()
    gx, gy = int(fg.x() * DPR), int(fg.y() * DPR)
    x0 = gx + int(win.width() * xf0 * DPR)
    x1 = gx + int(win.width() * xf1 * DPR)
    Y0 = gy + int(win.height() * y0f * DPR)
    Y1 = gy + int(win.height() * y1f * DPR)
    rs = gs = bs = n = 0
    for yy in range(max(Y0, 0), min(Y1, screen_img.height()), 2):
        for xx in range(max(x0, 0), min(x1, screen_img.width()), 2):
            c = screen_img.pixelColor(xx, yy)
            rs += c.red(); gs += c.green(); bs += c.blue(); n += 1
    if n:
        crop = screen_img.copy(x0, Y0, x1 - x0, Y1 - Y0)
        crop.save(os.path.join(OUT_DIR, f"{tag}.png"))
        return rs // n, gs // n, bs // n
    return None


def probe_window(tag, win, regions, note):
    """对窗口按区域取色（ regions: [(y0f,y1f,xf0,xf1,label)] ）"""
    settle(1.8)
    img = screen.grabWindow(0).toImage()
    out = {}
    print(f"\n[{tag}] {note}")
    for y0f, y1f, xf0, xf1, label in regions:
        rgb = region_rgb(win, y0f, y1f, xf0, xf1, img, f"{tag}_{label}")
        out[label] = rgb
        if rgb:
            print(f"  {label}: rgb={rgb}")
    flush()
    return out


# ==================== 构建窗口 ====================
from qfluentwidgets import FluentWindow, FluentIcon, setTheme, Theme  # noqa: E402

# 复刻 main.py 环境：Fusion + 预设深色主题（配置持久化亦为深色）
app.setStyle("Fusion")
setTheme(Theme.DARK)


class PureWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setObjectName("pureWindow")
        page = QLabel("A 纯净窗\n正对照", self)
        page.setObjectName("purePage")
        page.setAlignment(Qt.AlignCenter)
        f = QFont()
        f.setPointSize(14)
        page.setFont(f)
        self.addSubInterface(page, FluentIcon.HOME, "对照 A")
        self.setWindowTitle("A 纯净窗")
        self.resize(620, 460)


wa = PureWindow()
wa.move(60, 80)
wa.show()
settle(0.8)

REG_STRIP = (0.012, 0.045, 0.30, 0.70, "标题栏条")
REG_BODY = (0.50, 0.80, 0.35, 0.75, "页面体")

print("\n########## 阶段 1：原壁纸 ##########")
flush()
A1 = probe_window("A_原壁纸", wa, (REG_STRIP,), "纯净窗（正对照）")
wa.hide()

from main_window import MainWindow  # noqa: E402

wb = MainWindow()
wb.setWindowTitle("M 主窗口")
wb.move(60, 80)
wb.show()
settle(0.8)
M1_wb = probe_window("M_工作台_原壁纸", wb, (REG_STRIP, REG_BODY),
                     "工作台页（不透明负对照）")
try:
    wb.switchTo(wb.management_hub)
except Exception:
    wb.stackedWidget.setCurrentWidget(wb.management_hub)
settle(0.8)
M1_hub = probe_window("M_Hub_原壁纸", wb, (REG_STRIP, REG_BODY),
                      "运维管理Hub页（透明页面，关键观察点）")
wb.hide()

# ==================== 换洋红壁纸 ====================
print("\n########## 阶段 2：洋红壁纸（屏幕会闪一下洋红） ##########")
ok = set_wallpaper(MAGENTA)
print(f"[壁纸] 临时换为洋红：{'成功' if ok else '失败'}")
settle(1.5)
flush()

wa.show()
settle(0.8)
A2 = probe_window("A_洋红", wa, (REG_STRIP,), "纯净窗（正对照）")
wa.hide()

wb.show()
settle(0.8)
M2_wb = probe_window("M_工作台_洋红", wb, (REG_STRIP, REG_BODY), "工作台页")
try:
    wb.switchTo(wb.management_hub)
except Exception:
    wb.stackedWidget.setCurrentWidget(wb.management_hub)
settle(0.8)
M2_hub = probe_window("M_Hub_洋红", wb, (REG_STRIP, REG_BODY), "运维管理Hub页")
wb.hide()

# ==================== 恢复壁纸 ====================
restored = False
if ORIG_WALLPAPER and os.path.isfile(ORIG_WALLPAPER):
    restored = bool(set_wallpaper(ORIG_WALLPAPER))
print(f"[壁纸] 恢复原壁纸：{'成功' if restored else '失败！请手动重设壁纸'}")
settle(0.6)

# ==================== 判定 ====================
def shift(a, b):
    if not a or not b:
        return None
    return (b[0] - a[0]) - (b[1] - a[1])  # (R-G) 的变化量


print("\n======== 自动判定（洋红偏移量 = (R-G) 变化，>10 即变粉 => Mica 在渲染）========")
for name, p1, p2, label in (
        ("A_标题栏", A1, A2, "标题栏条"),
        ("M_工作台_标题栏", M1_wb, M2_wb, "标题栏条"),
        ("M_工作台_页面体", M1_wb, M2_wb, "页面体"),
        ("M_Hub_标题栏", M1_hub, M2_hub, "标题栏条"),
        ("M_Hub_页面体", M1_hub, M2_hub, "页面体")):
    d = shift(p1.get(label), p2.get(label))
    if d is None:
        print(f"  {name}: 无数据")
        continue
    verdict = "MICA 在渲染（壁纸派生生效）" if d > 10 else (
        "无 Mica（壁纸换了也没反应）" if abs(d) <= 10 else "异常")
    print(f"  {name}: 洋红偏移={d}  -> {verdict}")
flush()
QTimer.singleShot(300, app.quit)
sys.exit(app.exec())
