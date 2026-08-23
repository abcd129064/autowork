# -*- coding: utf-8 -*-
"""台账面板（re-export shim）

功能实现在 ``windows/ledger/`` 包：
- common.py / form.py / entry.py / records.py / settings.py / window.py

本文件仅做 re-export（``from windows.ledger import *``），并保留
独立进程入口（``python windows/ledger_panel.py``，供打包分发或调试）：
- ``from windows.ledger_panel import LedgerPanelWindow``
"""

from windows.ledger import *  # noqa: F401,F403
from windows.ledger import __all__ as _ledger_all

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
