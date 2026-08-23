# -*- coding: utf-8 -*-
"""售后面板（re-export shim）

原 2275 行单体文件已按页面/职责拆分为 ``windows/aftersale/`` 包：
- common.py / form.py / entry.py / dialogs.py / records.py /
  settings.py / window.py

本文件仅做 re-export（``from windows.aftersale import *``），
main_window/ui_mixin 等既有引用路径（``windows.aftersale_panel.X``）不变：
- ``from windows.aftersale_panel import AftersalePanelWindow``
- ``from windows.aftersale_panel import RecordsPage, EntryPage, EditRecordDialog``
"""

from windows.aftersale import *  # noqa: F401,F403
from windows.aftersale import __all__ as _aftersale_all

__all__ = list(_aftersale_all)


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

    # 支持 --table=桌号 参数：主程序/运维面板拉起独立进程时按桌号预筛选
    _table_arg = next((a.split("=", 1)[1] for a in sys.argv[1:]
                       if a.startswith("--table=")), "")

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    theme, color = _debug_theme()
    setTheme(theme)
    setThemeColor(color, lazy=True)
    win = AftersalePanelWindow()
    if _table_arg:
        win.open_records_for_table(_table_arg)
    win.show()
    sys.exit(app.exec())
