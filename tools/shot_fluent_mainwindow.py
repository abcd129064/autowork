# -*- coding: utf-8 -*-
"""FluentWindow 重构视觉验证（offscreen 截图）

生成两张 PNG 到 design/ 目录：
  - shot_home.png   工作台页
  - shot_mgmt.png   球桌管理页（迁入的运维子页）

用法：
  QT_QPA_PLATFORM=offscreen <venv>/python.exe tools/shot_fluent_mainwindow.py
"""
import os
import sys

_parts = [p for p in os.environ.get("PATH", "").split(os.pathsep)
          if "conda" not in p.lower() and "Library\\bin" not in p]
os.environ["PATH"] = os.pathsep.join(_parts)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from qfluentwidgets import setTheme, Theme, setThemeColor  # noqa: E402

app = QApplication(sys.argv)
setTheme(Theme.LIGHT)
setThemeColor("#00BCD4", lazy=True)

from main_window import MainWindow  # noqa: E402

w = MainWindow()
w.resize(1500, 900)
w.show()

# offscreen 下导航默认停在折叠态（48px），强制展开以便观察导航项
w.navigationInterface.setFixedWidth(200)

for _ in range(10):
    app.processEvents()

out_dir = os.path.join(ROOT, "design")
os.makedirs(out_dir, exist_ok=True)


def shot(name):
    path = os.path.join(out_dir, name)
    pix = w.grab()
    pix.save(path)
    print(f"  已保存 {path}  ({pix.width()}x{pix.height()})")


shot("shot_home.png")

# 依次截：运维管理（球桌管理子页）、售后（记录与统计子页）、跑视频、设置页
w.switchTo(w.management_hub)
for _ in range(10):
    app.processEvents()
shot("shot_mgmt.png")

w.switchTo(w.aftersale_hub)
w.aftersale_hub.switchTo(w.aftersale_hub.records_page)
for _ in range(10):
    app.processEvents()
shot("shot_aftersale.png")

w.switchTo(w.ledger_hub)
for _ in range(10):
    app.processEvents()
shot("shot_ledger.png")

w.switchTo(w.settings_hub)
for _ in range(10):
    app.processEvents()
shot("shot_settings.png")

print("截图完成")
os._exit(0)
