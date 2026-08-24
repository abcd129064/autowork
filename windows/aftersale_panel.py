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

# ---- Qt 运行时引导 ----
# 场景：conda base 激活后，PATH 会注入 conda 自带的 Qt DLL（qtbase/qtwebengine 等，位于
# <conda>\Library\bin），它们与 PySide6 自带的 Qt 二进制冲突，导致 Qt 平台插件加载失败
# （qt.qpa.plugin / DLL load failed）。必须在 import PySide6 之前用 os.add_dll_directory
# 固定 PySide6 自身的 Qt 搜索路径，并让其使用自带的插件目录。
import os as _os
import importlib.util as _qt_iu
import sys as _sys
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

# ---- 单文件打包模式：数据库持久化重定向 ----
# PyInstaller onefile 下 sys._MEIPASS 为临时解压目录（每次启动重建），
# table_db 用 __file__ 定位 database/tables.db 会指向临时目录，数据重启即丢。
# 此处把 DB 重定向到 exe 旁 database/（持久），首启从 _MEIPASS 复制种子库。
# 必须在 `from windows.aftersale import *` 之前执行——aftersale 包导入会加载
# table_db 并锁定 DB_PATH（判断特征：onefile 的 _MEIPASS 为 _MEIxxxx 临时目录，
# onedir 为 _internal，autowork 主程序不受影响）。
if (getattr(_sys, 'frozen', False)
        and getattr(_sys, '_MEIPASS', None)
        and os.path.basename(_sys._MEIPASS).startswith('_MEI')):
    try:
        import shutil as _shutil
        from core.app_paths import get_app_dir as _get_app_dir
        from database import table_db as _tdb
        _data_dir = os.path.join(_get_app_dir(), 'database')
        _db_file = os.path.join(_data_dir, 'tables.db')
        if not os.path.isfile(_db_file):
            os.makedirs(_data_dir, exist_ok=True)
            _seed = os.path.join(_sys._MEIPASS, 'database', 'tables.db')
            if os.path.isfile(_seed):
                _shutil.copy2(_seed, _db_file)
        _tdb._DB_DIR = _data_dir
        _tdb.DB_PATH = _db_file
        _tdb._conn = None          # 连接尚未创建，置空保险
        _tdb._initialized = False  # 确保建表/迁移在 exe 旁新库上执行
    except Exception:
        pass  # 重定向失败回退临时库（数据不持久，但应用可启动）

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

    # 支持 --table=桌号 参数：单文件独立分发时可命令行指定桌号预筛选
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
