# -*- mode: python ; coding: utf-8 -*-
"""售后面板独立打包 spec（入口 windows/aftersale_panel.py）

与 AutoWork.spec 相同的沙箱兼容补丁与 conda 适配逻辑；
仅保留售后面板运行所需依赖（不含 SSH/SFTP/frp/取证/AI 等主程序模块）。
构建：python -m PyInstaller --noconfirm AfterSale.spec
产物：dist/AfterSale/AfterSale.exe（settings.json 需自行放 exe 旁，
无 settings 时默认 SQLite 本地模式，首次运行自建 database/tables.db）
"""

import os
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

# ---- conda 环境适配（同 AutoWork.spec：收集 Library/bin 的 Qt DLL 与插件） ----
_conda_binaries = []
_conda_datas = []
from PySide6.QtCore import QLibraryInfo as _QLI
_qt_prefix = _QLI.path(_QLI.PrefixPath).replace('/', os.sep)
if _qt_prefix and os.path.basename(os.path.normpath(_qt_prefix)) == 'Library':
    _lib_bin = os.path.join(_qt_prefix, 'bin')
    _qt_plugins = os.path.join(_qt_prefix, 'lib', 'qt6', 'plugins')
    _support_dlls = {
        'libcrypto-3-x64.dll', 'libssl-3-x64.dll', 'ffi.dll',
        'liblzma.dll', 'libexpat.dll', 'libmpdec-4.dll', 'libmpdec.dll',
        'sqlite3.dll', 'zstd.dll',
        'libjpeg.dll', 'libpng16.dll', 'libwebp.dll', 'libwebpmux.dll',
        'libwebpdemux.dll', 'libtiff.dll', 'tiff.dll',
        'libopenjp2.dll', 'openjp2.dll',
        'lcms2.dll', 'freetype.dll', 'harfbuzz.dll',
        'libbz2.dll', 'bz2.dll', 'bzip2.dll',
        'shiboken6.cp313-win_amd64.dll', 'pyside6.cp313-win_amd64.dll',
    }
    _skip_qt_dlls = ('qt6webengine', 'qt6webchannel', 'qt6websockets',
                     'qt6quick', 'qt6qml', 'qt6labs', 'qt6designer',
                     'qt6pdf', 'qt6uitools', 'qt6help', 'qt6test',
                     'qt6shadertools')
    for _f in sorted(os.listdir(_lib_bin)):
        _low = _f.lower()
        if _low in _support_dlls:
            _conda_binaries.append((os.path.join(_lib_bin, _f), '.'))
        elif _low.startswith('qt6') and _low.endswith('.dll') \
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

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AfterSale',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app_icon.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AfterSale',
)
