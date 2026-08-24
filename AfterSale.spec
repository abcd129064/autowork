# -*- mode: python ; coding: utf-8 -*-
"""售后面板独立打包 spec（单文件 onefile，入口 windows/aftersale_panel.py）

与 AutoWork.spec 相同的沙箱兼容补丁与 conda 适配逻辑；
仅保留售后面板运行所需依赖（不含 SSH/SFTP/frp/取证/AI 等主程序模块）。

单文件模式（onefile）：
- 产物：dist/aftersale.exe（单个 exe，内置全部依赖，可独立分发）
- 首次运行自解压到临时目录，settings.json 由 build_exe.py 复制到 exe 旁，
  无 settings 时默认 SQLite 本地模式
- 数据库持久化：onefile 下 sys._MEIPASS 为临时目录，aftersale_panel.py
  入口会把 table_db 的 DB 路径重定向到 exe 旁 database/tables.db，
  首启从 _MEIPASS 复制种子库（见 aftersale_panel.py 顶部）
构建：python -m PyInstaller --noconfirm AfterSale.spec
"""

import os

# ---- Qt 运行时引导 ----
# 场景：conda base 激活后，PATH 会注入 conda 自带的 Qt DLL（qtbase/qtwebengine 等，位于
# <conda>\Library\bin），它们与 PySide6 自带的 Qt 二进制冲突，导致 Qt 平台插件加载失败
# （qt.qpa.plugin / DLL load failed）。spec 在构建时被 exec，L41 起即 import PySide6，
# 必须在 import PySide6 之前用 os.add_dll_directory 固定 PySide6 自身的 Qt 搜索路径。
import importlib.util as _qt_iu
_qt_handles = []
try:
    _qt_spec = _qt_iu.find_spec('PySide6')
    if _qt_spec is not None:
        _qt_locs = list(getattr(_qt_spec, 'submodule_search_locations', None) or [])
        if _qt_locs:
            _qt_pkg = _qt_locs[0]
            for _d in (_qt_pkg, os.path.dirname(_qt_pkg),
                       os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32')):
                if os.path.isdir(_d):
                    try:
                        _qt_handles.append(os.add_dll_directory(_d))
                    except OSError:
                        pass
            os.environ['QT_PLUGIN_PATH'] = os.path.join(_qt_pkg, 'plugins')
            os.environ.setdefault('QT_QPA_PLATFORM_PLUGIN_PATH',
                                   os.path.join(_qt_pkg, 'plugins', 'platforms'))
except Exception:
    pass

from PyInstaller.utils.hooks import collect_submodules

# ---- 沙箱环境兼容：禁用 PyInstaller 隔离子进程（同 AutoWork.spec） ----
from PyInstaller import isolated as _pyi_isolated
from PyInstaller.isolated import _parent as _pyi_parent


class _InProcIsolatedPython(_pyi_isolated.Python):
    def __init__(self, strict_mode=None):
        self._child = None
        self._already_isolated = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


_pyi_isolated.Python = _InProcIsolatedPython
_pyi_parent.Python = _InProcIsolatedPython

# ---- Qt 应用单例幂等化（配合禁隔离，同 AutoWork.spec） ----
from PySide6.QtCore import QCoreApplication as _QCA
from PySide6.QtGui import QGuiApplication as _QGA
from PySide6.QtWidgets import QApplication as _QWA


def _make_idempotent_app_init(_orig):
    def _patched(self, *args, **kwargs):
        _inst = _QCA.instance()
        if _inst is not None and _inst is not self:
            return
        _orig(self, *args, **kwargs)
    return _patched


_QCA.__init__ = _make_idempotent_app_init(_QCA.__init__)
_QGA.__init__ = _make_idempotent_app_init(_QGA.__init__)
_QWA.__init__ = _make_idempotent_app_init(_QWA.__init__)

# 自动收集 qfluentwidgets 全部子模块（排除未使用的 multimedia）
qfw_hiddenimports = [
    m for m in collect_submodules('qfluentwidgets')
    if 'multimedia' not in m
]

# ---- conda 环境适配（同 AutoWork.spec：两类问题分开处理） ----
# 1) Python C 扩展依赖的运行时原生 DLL 位于 {env}/Library/bin（如 libexpat.dll
#    、ffi.dll、sqlite3.dll），PyInstaller 无法自动定位该目录，必须无条件显式
#    收集——否则打包版运行时 import 这些模块会 DLL load failed（本次即 pyexpat）。
# 2) Qt 配套：conda 版 PySide6 的 Qt DLL/插件在 {env}/Library 下需显式收集；
#    PyPI 版则在包内 Qt6/，由 PyInstaller 的 PySide6 hook 自动处理。
import sys
_conda_binaries = []
_conda_datas = []
_conda_lib_bin = os.path.join(sys.prefix, 'Library', 'bin')
_conda_run_dlls = {
    'libcrypto-3-x64.dll', 'libssl-3-x64.dll', 'ffi.dll',
    'liblzma.dll', 'libexpat.dll', 'libmpdec-4.dll', 'libmpdec.dll',
    'sqlite3.dll', 'zstd.dll',
    'libjpeg.dll', 'libpng16.dll', 'libwebp.dll', 'libwebpmux.dll',
    'libwebpdemux.dll', 'libtiff.dll', 'tiff.dll',
    'libopenjp2.dll', 'openjp2.dll',
    'lcms2.dll', 'freetype.dll', 'harfbuzz.dll',
    'libbz2.dll', 'bz2.dll', 'bzip2.dll',
}
if os.path.isdir(_conda_lib_bin):
    for _f in sorted(os.listdir(_conda_lib_bin)):
        if _f.lower() in _conda_run_dlls:
            _conda_binaries.append((os.path.join(_conda_lib_bin, _f), '.'))
from PySide6.QtCore import QLibraryInfo as _QLI
_qt_prefix = _QLI.path(_QLI.PrefixPath).replace('/', os.sep)
if _qt_prefix and os.path.basename(os.path.normpath(_qt_prefix)) == 'Library':
    _lib_bin = os.path.join(_qt_prefix, 'bin')
    _qt_plugins = os.path.join(_qt_prefix, 'lib', 'qt6', 'plugins')
    _skip_qt_dlls = ('qt6webengine', 'qt6webchannel', 'qt6websockets',
                     'qt6quick', 'qt6qml', 'qt6labs', 'qt6designer',
                     'qt6pdf', 'qt6uitools', 'qt6help', 'qt6test',
                     'qt6shadertools')
    for _f in sorted(os.listdir(_lib_bin)):
        _low = _f.lower()
        if _low.startswith('qt6') and _low.endswith('.dll') \
                and not _low.startswith(_skip_qt_dlls):
            _conda_binaries.append((os.path.join(_lib_bin, _f), '.'))
    _skip_plugins = {'designer', 'qmltooling', 'qmllint', 'networkinformation'}
    for _d in sorted(os.listdir(_qt_plugins)):
        if _d in _skip_plugins:
            continue
        if os.path.isdir(os.path.join(_qt_plugins, _d)):
            _conda_datas.append(
                (os.path.join(_qt_plugins, _d), f'PySide6/plugins/{_d}'))

a = Analysis(
    ['windows/aftersale_panel.py'],
    pathex=[],
    binaries=list(_conda_binaries),
    datas=[
        # 主题资源与图标（get_resource_dir() 读取）
        ('styles', 'styles'),
        ('app_icon.ico', '.'),
        # 球桌库种子：录入页桌号关联搜索依赖（table_db 用 __file__ 定位）
        ('database/tables.db', 'database'),
        # 资源目录（字体/头像/底图，单杆视频等工具共用，随包分发保险）
        ('resource', 'resource'),
    ] + _conda_datas,
    hiddenimports=[
        'PySide6.QtWidgets',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtSvg',
        'PySide6.QtSvgWidgets',
        'darkdetect',
        'darkdetect._windows_detect',
        # 双后端依赖：MySQL 驱动 + Excel 导入导出
        'pymysql',
        'openpyxl',
    ] + qfw_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['qfluentwidgets.multimedia', 'scipy'],
    noarchive=False,
    optimize=0,
)

# babel 语言数据裁剪（trafilatura 链不需要，但 darkdetect/qfw 可能带入少量）
_babel_keep = {'en.dat', 'zh.dat', 'zh_Hans.dat', 'zh_Hant.dat'}
a.datas = [
    _d for _d in a.datas
    if not (_d[0].replace('\\', '/').startswith('babel/locale-data/')
            and os.path.basename(_d[0]) not in _babel_keep)
]

pyz = PYZ(a.pure)

# 单文件模式：EXE 直接内嵌 binaries + datas（onedir 版用 COLLECT 分目录，
# 单文件版必须 exclude_binaries=False 全部打进 exe，运行时自解压到 _MEIPASS）
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='aftersale',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app_icon.ico',
)
# 注意：onefile 模式不再需要 COLLECT，产物直接输出到 dist/aftersale.exe
