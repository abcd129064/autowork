# -*- coding: utf-8 -*-
"""运维管理面板（re-export shim）

原 4483 行单体文件已按页面/职责拆分为 ``windows/management/`` 包：
- common.py / dialogs.py / table_page.py / device_page.py /
  settings_page.py / health_page.py / moyu_page.py / window.py

本文件仅做 re-export（``from windows.management import *``），
main_window/ui_mixin 等既有引用路径（``windows.management_panel.X``）不变：
- ``from windows.management_panel import ManagementPanelWindow``
- ``from windows.management_panel import UploadListDialog``

镜像推送机制 B（_trigger_auto_mysql_sync）已在 T02 下线，拆包后不复活。
"""

from windows.management import *  # noqa: F401,F403
from windows.management import __all__ as _management_all

__all__ = list(_management_all)


if __name__ == "__main__":
    import sys
    import json
    import os
    import core.acrylic_patch  # noqa: F401
    from PySide6.QtWidgets import QApplication
    from qfluentwidgets import setTheme, setThemeColor, Theme

    def _debug_theme_color():
        """调试入口读取 settings.json 主题强调色（打包后从 exe 旁读取）"""
        try:
            from core.app_paths import get_app_dir
            p = os.path.join(get_app_dir(), "settings.json")
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f).get("theme_color", "#00BCD4")
        except Exception:
            return "#00BCD4"

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    setTheme(Theme.DARK)
    setThemeColor(_debug_theme_color(), lazy=True)
    win = ManagementPanelWindow()
    win.show()
    sys.exit(app.exec())
