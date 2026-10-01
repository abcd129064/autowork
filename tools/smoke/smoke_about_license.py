"""冒烟：离屏构造「帮助 → 关于」弹窗，校验 GPLv3 授权文案与开源库署名在位。

背景：AGENTS.md §5.1 记录两个解释器都不完整，只有 miniconda3 带 qfluentwidgets，
   而它的 PySide6 需要内联引导（docs/Qt内联引导说明.md）才 import 得动。
用法：QT_QPA_PLATFORM=offscreen C:\\Users\\shen_zhe\\miniconda3\\python.exe tools/smoke/smoke_about_license.py
产物：stdout，退出码 0=通过
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 内联引导：把 PySide6 自带 Qt 目录加入 DLL 搜索路径
import importlib.util

_handles = []
_spec = importlib.util.find_spec("PySide6")
_locs = list(getattr(_spec, "submodule_search_locations", None) or [])
if _locs:
    _pkg = _locs[0]
    for _d in (_pkg, os.path.dirname(_pkg),
               os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")):
        if os.path.isdir(_d):
            try:
                _handles.append(os.add_dll_directory(_d))
            except OSError:
                pass
    os.environ["QT_PLUGIN_PATH"] = os.path.join(_pkg, "plugins")
    os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH",
                          os.path.join(_pkg, "plugins", "platforms"))

from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402
from main_window.ui_mixin import (AboutDialog,  # noqa: E402
                                  ABOUT_LINKS, ABOUT_OSS_LIBS)

app = QApplication.instance() or QApplication(list(sys.argv))
# MessageBoxBase 构造函数里会读 parent.width()，不能传 None
_host = QWidget()
_host.resize(800, 600)
_host.show()
dlg = AboutDialog(_host)
blob = " ".join(l.text() for l in dlg.findChildren(QLabel))

rc = 0
for key in ("GPL", "licenses/", "石睿轩"):
    if key not in blob:
        print(f"缺失: {key}")
        rc = 1
if "PyQt-Fluent-Widgets" in [t for t, _ in ABOUT_OSS_LIBS]:
    print("残留旧库名 PyQt-Fluent-Widgets")
    rc = 1
if not any("GPL" in t for t, _ in ABOUT_LINKS):
    print("链接区缺许可证条目")
    rc = 1
print("链接区:", [t for t, _ in ABOUT_LINKS])
print("开源库区:", [t for t, _ in ABOUT_OSS_LIBS])
print("弹窗底部文本:", blob[-160:])
print("PASS" if rc == 0 else "FAIL")
raise SystemExit(rc)
