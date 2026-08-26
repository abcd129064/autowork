# -*- coding: utf-8 -*-
"""跑视频面板（re-export shim）

功能实现在 ``windows/run_video/`` 包：
- common.py / form.py / entry.py / records.py / settings.py / window.py

本文件仅做 re-export（``from windows.run_video import *``），并保留
独立进程入口（``python windows/ledger_panel.py``，供打包分发或调试）：
- ``from windows.ledger_panel import LedgerPanelWindow``
"""

# ---- Qt 运行时引导 ----
# 场景：conda base 激活后，PATH 会注入 conda 自带的 Qt DLL（qtbase/qtwebengine 等，位于
# <conda>\Library\bin），它们与 PySide6 自带的 Qt 二进制冲突，导致 Qt 平台插件加载失败
# （qt.qpa.plugin / DLL load failed）。必须在 import PySide6 之前用 os.add_dll_directory
# 固定 PySide6 自身的 Qt 搜索路径，并让其使用自带的插件目录。
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

from windows.run_video import *  # noqa: F401,F403
from windows.run_video import __all__ as _ledger_all
__all__ = list(_ledger_all)


if __name__ == "__main__":
    import sys
    import json
    import os
    import core.acrylic_patch  # noqa: F401
    from PySide6.QtWidgets import QApplication
    from qfluentwidgets import setTheme, setThemeColor, Theme

    def _debug_theme():
        """读取 settings.json 主题配置（打包后从 exe 旁读取，与主程序入口一致）"""
        try:
            from core.app_paths import get_app_dir
            p = os.path.join(get_app_dir(), "settings.json")
            with open(p, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return (Theme.DARK if cfg.get("dark_theme") else Theme.LIGHT,
                    cfg.get("theme_color", "#00BCD4"))
        except Exception:
            return Theme.LIGHT, "#00BCD4"

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    theme, color = _debug_theme()
    setTheme(theme)
    setThemeColor(color, lazy=True)
    win = LedgerPanelWindow()
    win.show()
    sys.exit(app.exec())
