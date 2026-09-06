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
        """调试入口读取配置门面主题强调色（与主程序入口一致）"""
        try:
            from core import app_settings
            return app_settings.get("theme_color", "#00BCD4")
        except Exception:
            return "#00BCD4"

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    setTheme(Theme.DARK)
    setThemeColor(_debug_theme_color(), lazy=True)
    win = ManagementPanelWindow()
    win.show()
    sys.exit(app.exec())
