# -*- coding: utf-8 -*-
"""云母（Mica）离屏取证：A/B 窗口渲染透明度对比 + 主窗口构造链分步二分

原理：
  qfw FluentWidget.paintEvent 用 backgroundColor 填窗口；Mica 启用时该色 alpha=0
  （窗口透出 DWM 云母）。若真机云母消失源于 Qt 侧不透明绘制/遮挡（用户看到的
  #202020/#F0F4F9 恰是 qfw 的 Mica-禁用 fallback 色），则离屏渲染也能取证：
  标题栏等「设计上透明」的条带像素 alpha 应为 0；若 alpha=255 说明 Qt 侧被画死。
  渲染用 render(DrawChildren)（去掉 DrawWindowBackground），只画 paintEvent 内容，
  避免调色板窗口底色污染采样。

判读：
  [P1] B 的 titlebar alpha == A 的 titlebar alpha（都约等于 0）
       -> Qt 侧干净，问题在 DWM 层（转真机探针 probe_mica_bisect.py）
  [P1] B 的 titlebar alpha == 255 而 A 约为 0
       -> Qt 侧有凶手，看 [P2] 分步哪一步 alpha 跳变即锁定

用法：
  <venv>/python.exe tools/probe_mica_offscreen.py
"""
import os
import sys

# --- 剔除 conda 注入的 Qt DLL 路径（与 smoke 同款引导）---
_parts = [p for p in os.environ.get("PATH", "").split(os.pathsep)
          if "conda" not in p.lower() and "Library\\bin" not in p]
os.environ["PATH"] = os.pathsep.join(_parts)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QWidget, QLabel  # noqa: E402
from PySide6.QtCore import Qt, QPoint  # noqa: E402
from PySide6.QtGui import QImage, QPainter, QRegion, QFont  # noqa: E402

app = QApplication(sys.argv)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (xFrac, yFrac, 名称)；yFrac<=1 且三参时按窗口高度比例，二参条目 y 固定 10px 标题栏
DEFAULT_TAGS = ((0.5, 0.01, "titlebar"), (0.03, 0.5, "nav"),
                (0.6, 0.6, "page"))


def flush():
    sys.stdout.flush()
    sys.stderr.flush()


def render_samples(win, tags=None):
    """按 DrawChildren-only 渲染窗口，返回 [(tag, alpha, r, g, b), ...]"""
    if tags is None:
        tags = DEFAULT_TAGS
    img = QImage(win.size(), QImage.Format_ARGB32_Premultiplied)
    img.fill(0)  # 全透明底
    p = QPainter(img)
    win.render(p, QPoint(0, 0), QRegion(),
               QWidget.RenderFlag.DrawChildren)
    p.end()

    w, h = win.width(), win.height()
    out = []
    for xf, yf, name in tags:
        x = min(int(w * xf), w - 1)
        y = min(int(h * yf) if yf <= 1.0 else int(yf), h - 1)
        c = img.pixelColor(x, y)
        out.append((name, c.alpha(), c.red(), c.green(), c.blue()))
    return out


def report(tag, win, samples):
    print(f"[{tag}] size={win.width()}x{win.height()} "
          f"micaEnabled={win.isMicaEffectEnabled()} "
          f"bg={win.backgroundColor.getRgb()} "
          f"winId={int(win.winId())}")
    for name, a, r, g, b in samples:
        print(f"    {name:<10} alpha={a:<3} rgb=({r},{g},{b})")
    flush()


def pump(n=8):
    for _ in range(n):
        app.processEvents()


# ==================== [P1] A/B 对比 ====================
print("=" * 62)
print("[P1] A/B 离屏渲染透明度对比")
print("=" * 62)

from qfluentwidgets import FluentWindow, FluentIcon, FluentTitleBar  # noqa: E402


class PureWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setObjectName("pureWindow")
        page = QLabel("A", self)
        page.setObjectName("purePage")
        page.setAlignment(Qt.AlignCenter)
        self.addSubInterface(page, FluentIcon.HOME, "对照 A")
        self.setWindowTitle("A")
        self.resize(520, 420)


wa = PureWindow()
wa.show()
pump()
report("A", wa, render_samples(wa))

from main_window import MainWindow  # noqa: E402

wb = MainWindow()
wb.setWindowTitle("B")
wb.show()
pump()
report("B", wb, render_samples(wb))

# ==================== [P2] 构造链分步二分 ====================
print()
print("=" * 62)
print("[P2] 纯净窗口逐步叠加主窗口构造步骤（盯 titlebar alpha 跳变）")
print("=" * 62)


class StepWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setObjectName("stepWindow")
        page = QLabel("bisect", self)
        page.setObjectName("stepPage")
        page.setAlignment(Qt.AlignCenter)
        self.addSubInterface(page, FluentIcon.HOME, "base")
        self.resize(1000, 640)


w = StepWindow()
w.show()
pump()
report("S0 纯净基线", w, render_samples(w))

# ---- S1 自定义标题栏（MainWindow.__init__ 第一件事）----
tb = FluentTitleBar(w)
w.setTitleBar(tb)
tb.setFixedHeight(34)
tb.raise_()
pump()
report("S1 +FluentTitleBar(34px)", w, render_samples(w))

# ---- S2 homeInterface / vBoxLayout 兼容层 ----
from PySide6.QtWidgets import QVBoxLayout  # noqa: E402

w.homeInterface = QWidget(w)
w.homeInterface.setObjectName("homeInterface")
w.home_vbox = QVBoxLayout(w.homeInterface)
w.home_vbox.setContentsMargins(0, 0, 0, 0)
w.home_vbox.setSpacing(0)
w.vBoxLayout = w.home_vbox
pump()
report("S2 +homeInterface/vBoxLayout", w, render_samples(w))

# ---- S3 Ui_MainWindow.setupUi（centralwidget 全家桶挂进来）----
try:
    from autowork_with_table import Ui_MainWindow  # noqa: E402
    w.ui = Ui_MainWindow()
    w.ui.setupUi(w)
    pump()
    report("S3 +setupUi", w, render_samples(w))
except Exception as e:
    print(f"[S3] setupUi 失败（跳过继续）: {e!r}")
    flush()

# ---- S4 setTheme(DARK) + ColorScheme（_apply_theme 前半）----
from qfluentwidgets import setTheme, Theme  # noqa: E402

setTheme(Theme.DARK)
app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
pump()
report("S4 +setTheme(DARK)+ColorScheme", w, render_samples(w))

# ---- S5 setStyleSheet(全局主题 qss)（_apply_theme 中半）----
qss_path = os.path.join(ROOT, "styles", "dark.qss")
try:
    with open(qss_path, encoding="utf-8") as f:
        w.setStyleSheet(f.read())
    pump()
    report("S5 +setStyleSheet(dark.qss)", w, render_samples(w))
except Exception as e:
    print(f"[S5] qss 加载失败: {e!r}")
    flush()

# ---- S6 全局字体递归（_apply_theme 尾部 _apply_global_font）----
f = app.font()
f.setPixelSize(16)
app.setFont(f)


def _set_font_recursive(widget, font):
    widget.setFont(font)
    for child in widget.findChildren(QWidget):
        child.setFont(font)
        child.update()


_set_font_recursive(w, f)
pump()
report("S6 +全局字体递归", w, render_samples(w))

# ---- S7 导航亚克力 ----
w.navigationInterface.setAcrylicEnabled(True)
pump()
report("S7 +导航亚克力", w, render_samples(w))

# ---- 汇总 ----
print()
print("判读：P2 各步 titlebar alpha 若从 0 跳到 255，该步即凶手；")
print("      全程保持 0 而 P1 中 B=255，则凶手在 init_ui 其余部分")
print("      （状态栏/菜单栏/快捷键等）或 DWM 层，转真机探针。")
flush()
os._exit(0)
