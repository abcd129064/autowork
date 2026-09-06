# -*- coding: utf-8 -*-
"""云母（Mica）真机对照探针：区分「代码问题」vs「系统问题」

同屏显示两个窗口，肉眼对比：
  A = 纯净 qfw FluentWindow（最小代码，qfw 官方默认路径）
  B = AutoWork 主窗口（完整业务构造）

结论判读（控制台也会打印）：
  A 有云母、B 没有 -> 主窗口代码链路破坏了 Mica（继续查业务构造）
  A、B 都没有     -> 系统级问题：先检查
                     Windows 设置 > 个性化 > 颜色 > 透明效果 是否开启；
                     脚本会自动读注册表给出提示
  A、B 都有       -> 正常，问题出在其他页面遮挡

用法（真机，非 offscreen）：
  <venv>/python.exe tools/probe_mica_real.py
"""
import os
import sys

# 剔除 conda 注入的 Qt DLL 路径（与主程序同款引导）
_parts = [p for p in os.environ.get("PATH", "").split(os.pathsep)
          if "conda" not in p.lower() and "Library\\bin" not in p]
os.environ["PATH"] = os.pathsep.join(_parts)
if "QT_QPA_PLATFORM" in os.environ:
    del os.environ["QT_QPA_PLATFORM"]  # 真机渲染，禁用 offscreen

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- 系统透明效果开关检测（Mica 的前置条件） ---
try:
    import winreg
    k = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize")
    val, _ = winreg.QueryValueEx(k, "EnableTransparency")
    winreg.CloseKey(k)
    if val == 0:
        print("[!] 系统透明效果已关闭：Windows 设置 > 个性化 > 颜色 > "
              "透明效果 打开后，Mica/亚克力才会生效（先开再跑本探针）")
    else:
        print("[OK] 系统透明效果已开启")
except Exception as e:
    print("[..] 无法读取透明效果开关：", e)

print("Win build =", sys.getwindowsversion().build,
      "(Mica 需要 >= 22000 / Win11)")

from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

app = QApplication(sys.argv)

from qfluentwidgets import FluentWindow, FluentIcon, TitleLabel  # noqa: E402
from qfluentwidgets.common.style_sheet import isDarkTheme  # noqa: E402

# --- A: 纯净 FluentWindow ---
class PureWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setObjectName("pureWindow")
        page = QLabel("A · 纯净 FluentWindow\n\n这个窗口有云母吗？", self)
        page.setObjectName("purePage")
        page.setAlignment(Qt.AlignCenter)
        f = QFont()
        f.setPointSize(14)
        page.setFont(f)
        self.addSubInterface(page, FluentIcon.HOME, "对照 A")
        self.setWindowTitle("A: 纯净 FluentWindow")
        self.resize(520, 420)

wa = PureWindow()

# --- B: AutoWork 主窗口（完整构造，等价于正式启动） ---
from main_window import MainWindow  # noqa: E402
wb = MainWindow()
wb.setWindowTitle("B: AutoWork 主窗口")

wa.show()
wb.show()

def _report():
    for tag, w in (("A", wa), ("B", wb)):
        try:
            print(f"[{tag}] micaEnabled={w.isMicaEffectEnabled()} "
                  f"bgAlpha={w.backgroundColor.alpha()} "
                  f"visible={w.isVisible()}")
        except Exception as e:
            print(f"[{tag}] 状态读取失败: {e}")
    print("""
—— 肉眼对比两个窗口 ——
A 有云母、B 没有 -> 主窗口代码破坏 Mica（把本输出发给 AI 继续排查）
A、B 都没有     -> 系统级（看上方透明效果提示）
A、B 都有       -> 主窗口正常，问题在其他页面遮挡（发截图给 AI）
""")

from PySide6.QtCore import QTimer
QTimer.singleShot(800, _report)
sys.exit(app.exec())
