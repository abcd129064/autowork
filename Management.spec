# -*- mode: python ; coding: utf-8 -*-
"""运维管理面板独立打包 spec（入口 windows/management_panel.py）

与 AutoWork.spec 相同的沙箱兼容补丁与 conda 适配逻辑；
仅保留运维面板运行所需依赖（球桌管理/设备状态/健康趋势/设置/小游戏，
含 SSH/SFTP 远程会话与售后面板内嵌兜底）。
构建：python -m PyInstaller --noconfirm Management.spec
产物：dist/Management/management.exe（settings.json 由 build_exe.py 复制到 exe 旁）
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
    ['windows/management_panel.py'],
    pathex=[],
    binaries=list(_conda_binaries),
    datas=[
        # 主题资源与图标（get_resource_dir() 读取）
        ('styles', 'styles'),
        ('app_icon.ico', '.'),
        # 球桌库种子：球桌/设备数据依赖（table_db 用 __file__ 定位）
        ('database/tables.db', 'database'),
        # 资源目录（字体/头像/底图，小游戏与工具共用，随包分发保险）
        ('resource', 'resource'),
    ] + _conda_datas,
    hiddenimports=[
        'PySide6.QtWidgets',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtSvg',
        'PySide6.QtSvgWidgets',
        'PySide6.QtNetwork',
        'paramiko',
        'cryptography',
        'bcrypt',
        'darkdetect',
        'darkdetect._windows_detect',
        # 双后端依赖：MySQL 驱动 + Excel 导出
        'pymysql',
        'openpyxl',
        # 图片预览/迁移（image_viewer、collect_worker）
        'PIL.ImageQt',
        'PIL.ImageFilter',
        'PIL.ImageEnhance',
    ] + qfw_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # simplejson：jedi 自带的 typeshed 存根目录无 __init__.py，会被误收为
    # 命名空间包，导致 requests 回退导入时命中空壳报 ImportError（排除后
    # requests 自动回退标准库 json，零功能损失）
    excludes=['qfluentwidgets.multimedia', 'scipy', 'simplejson'],
    noarchive=False,
    optimize=0,
)

# babel 语言数据裁剪（trafilatura 链：摸鱼阅读器仅需中英及常用语言）
_babel_keep = {
    'en.dat', 'zh.dat', 'zh_Hans.dat', 'zh_Hant.dat',
    'ja.dat', 'ko.dat', 'fr.dat', 'de.dat', 'es.dat', 'it.dat',
    'pt.dat', 'pt_BR.dat', 'ru.dat', 'ar.dat', 'hi.dat',
    'vi.dat', 'th.dat', 'id.dat', 'ms.dat', 'tr.dat',
}
a.datas = [
    _d for _d in a.datas
    if not (_d[0].replace('\\', '/').startswith('babel/locale-data/')
            and os.path.basename(_d[0]) not in _babel_keep)
]
# qpdf.dll 插件依赖已排除的 Qt6Pdf.dll，剔除避免运行时插件加载警告；
# qsqlpsql.dll 依赖未收集的 libpq.dll（项目用 Python sqlite3，不用 QtSql 驱动）
_skip_plugins_dll = ('imageformats/qpdf.dll', 'sqldrivers/qsqlpsql.dll')
a.datas = [
    _d for _d in a.datas
    if not _d[0].replace('\\', '/').endswith(_skip_plugins_dll)
]
a.binaries = [
    _b for _b in a.binaries
    if not _b[0].replace('\\', '/').endswith(_skip_plugins_dll)
]
# MKL/LLVM 全量剔除（同 AutoWork.spec：numpy 实为 PyPI openblas 版，
# hook 误判收集的 MKL 依赖全部排除，不影响功能）
_mkl_drop = ('mkl', 'omptarget', 'sycl')
a.binaries = [
    _b for _b in a.binaries
    if not os.path.basename(_b[0]).lower().startswith(_mkl_drop)
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='management',
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
    name='Management',
)
