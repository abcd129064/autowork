# -*- mode: python ; coding: utf-8 -*-

import os

# ---- Qt 运行时引导 ----
# 场景：conda base 激活后，PATH 会注入 conda 自带的 Qt DLL（qtbase/qtwebengine 等，位于
# <conda>\Library\bin），它们与 PySide6 自带的 Qt 二进制冲突，导致 Qt 平台插件加载失败
# （qt.qpa.plugin / DLL load failed）。spec 在构建时被 exec，L37 起即 import PySide6，
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

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# ---- 沙箱环境兼容：禁用 PyInstaller 隔离子进程 ----
# 当前终端沙箱禁止 CreatePipe（WinError 5），isolated 子进程无法启动；
# 将 isolated.Python 替换为进程内直接执行版本（call() 已有 _already_isolated
# 分支支持进程内调用，语义等价，仅失去子进程隔离性，不影响收集结果）
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


# 必须同时覆盖两个命名空间：isolated.Python 供 with 语句解析；
# _parent.Python 是模块级 call()/isolated_call 函数体内部的静态引用
_pyi_isolated.Python = _InProcIsolatedPython
_pyi_parent.Python = _InProcIsolatedPython

# ---- Qt 应用单例幂等化（配合禁隔离） ----
# isolated 子进程被禁用后，hook 的探测代码与打包主进程共享运行环境；
# Qt 应用类单例（QCoreApplication 等）创建后 shiboken 禁止再次创建，
# 导致后续 hook（如 QtNetwork 的 OpenSSL 探测）构造 QCoreApplication
# 抛 RuntimeError。将构造 patch 为幂等：已有实例则跳过初始化（此类
# 构造仅用于抑制警告，实例闲置，见 PyInstaller hook 的 noqa: F841）
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

# 自动收集 qfluentwidgets 全部子模块（含 _rc.resource 图标/QSS 资源）
# 排除 multimedia（需要 PySide6.QtMultimedia，本项目未使用）
qfw_hiddenimports = [
    m for m in collect_submodules('qfluentwidgets')
    if 'multimedia' not in m
]

# ---- pygwalker 统计图表（windows/stat_charts.py 延迟 import） ----
# pyg.to_html 需要包内 HTML 模板与 graphic-walker 前端资源，
# collect_data_files 收集；collect_submodules 防延迟 import 漏检。
# 体积影响：pygwalker + pandas + graphic-walker 前端 ~+60MB（体积换交互）
pygwalker_datas = collect_data_files('pygwalker')
pygwalker_hiddenimports = collect_submodules('pygwalker')

# ---- conda 环境适配 ----
# 运行环境是 conda base，但 PySide6 为 PyPI 版（历史修复误装）。两类问题分开处理：
# 1) Python C 扩展（_ctypes/_sqlite3/pyexpat/PIL/zstandard/cryptography 等）依赖的
#    运行时原生 DLL 位于 {env}/Library/bin（如 libexpat.dll、ffi.dll、sqlite3.dll），
#    PyInstaller 无法自动定位该目录，必须无条件显式收集——否则打包版运行时
#    import 这些模块会 DLL load failed（本次正是 pyexpat 缺 libexpat.dll）。
# 2) Qt 配套：conda 版 PySide6 的 Qt DLL/插件在 {env}/Library 下需显式收集；
#    PyPI 版则在包内 Qt6/，由 PyInstaller 的 PySide6 hook 自动处理。
import sys
_conda_binaries = []
_conda_datas = []
# 运行时原生库：无论 PySide6 为何种版本，只要 {env}/Library/bin 存在即收集
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
# Qt 二进制 + 插件：仅 conda 版 PySide6（Qt 前缀为 Library）才需从该目录收集
from PySide6.QtCore import QLibraryInfo as _QLI
_qt_prefix = _QLI.path(_QLI.PrefixPath).replace('/', os.sep)
if _qt_prefix and os.path.basename(os.path.normpath(_qt_prefix)) == 'Library':
    _lib_bin = os.path.join(_qt_prefix, 'bin')
    _qt_plugins = os.path.join(_qt_prefix, 'lib', 'qt6', 'plugins')
    # Qt6 DLL 黑名单：项目零引用 WebEngine/QML/Quick/Designer/Pdf 等模块，
    # conda 的 Library/bin 含全套 Qt DLL，无差别收集会引入 ~250MB 无用二进制，
    # 只收集运行时必需的 Core/Gui/Widgets/Svg/Network/OpenGL 等
    _skip_qt_dlls = ('qt6webengine', 'qt6webchannel', 'qt6websockets',
                     'qt6quick', 'qt6qml', 'qt6labs', 'qt6designer',
                     'qt6pdf', 'qt6uitools', 'qt6help', 'qt6test',
                     'qt6shadertools')
    for _f in sorted(os.listdir(_lib_bin)):
        _low = _f.lower()
        if _low.startswith('qt6') and _low.endswith('.dll') \
                and not _low.startswith(_skip_qt_dlls):
            _conda_binaries.append((os.path.join(_lib_bin, _f), '.'))
    # Qt 插件：放入 PySide6/plugins/<子目录>，PyInstaller 的
    # PySide6 hook 会据此自动设置 QT_PLUGIN_PATH
    # networkinformation 依赖 glib 系列 DLL（conda 环境未安装），本项目不使用，排除
    _skip_plugins = {'designer', 'qmltooling', 'qmllint', 'networkinformation'}
    for _d in sorted(os.listdir(_qt_plugins)):
        if _d in _skip_plugins:
            continue
        if os.path.isdir(os.path.join(_qt_plugins, _d)):
            _conda_datas.append(
                (os.path.join(_qt_plugins, _d), f'PySide6/plugins/{_d}'))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=list(_conda_binaries),
    datas=[
        # styles/ 为只读主题资源，打包后位于 _internal/styles/，
        # 运行时通过 get_resource_dir()（sys._MEIPASS）读取
        ('styles', 'styles'),
        # 应用图标（窗口/任务栏图标，运行时经 get_resource_dir() 读取）
        ('app_icon.ico', '.'),
        # 球桌数据库种子：table_db.py 用 __file__ 定位 database/tables.db，
        # 打包后 __file__ 位于 _internal/database/，必须随包分发，
        # 否则打包版首次运行自建空库，球桌库搜索永远无候选。
        # 打包前由 build_exe.py 执行 WAL checkpoint，保证主库文件含全部数据
        ('database/tables.db', 'database'),
        # 单杆视频资源（字体/头像/底图/logo，工具菜单「单杆视频」）
        ('resource', 'resource'),
        # 注意：settings.json / frpc.exe / frpc_xtcp.toml 由 build_exe.py
        # 构建后复制到 dist 根目录（exe 旁边），不放入 datas；
        # autowork_with_table.py / p2p.py 已被 import 追踪编译进 PYZ，无需重复打包
    ] + pygwalker_datas + _conda_datas,
    hiddenimports=[
        'PySide6.QtWidgets',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtSvg',
        'PySide6.QtSvgWidgets',
        'pandas',
        'paramiko',
        'cryptography',
        'bcrypt',
        'darkdetect',
        'darkdetect._windows_detect',
        # AI 厂商分析（取证报告，forensic_report 内延迟 import，显式声明保险）
        'openai',
        # 亚克力补丁依赖（打包环境无 numpy/scipy 时 PIL 替代实现需要）
        'PIL.ImageQt',
        'PIL.ImageFilter',
        'PIL.ImageEnhance',
        # 单杆视频（tools/single_shot_video.py 延迟导入，显式声明保险）
        'cv2',
        'numpy',
    ] + pygwalker_hiddenimports + qfw_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['qfluentwidgets.multimedia', 'scipy'],
    noarchive=False,
    optimize=0,
)

# ---- 体积精简（Analysis 后过滤，减小 _internal 体积） ----
# babel 语言数据裁剪：trafilatura 链（摸鱼中心小说阅读器）仅需中英及常用语言，
# 全量 1084 个 locale 数据约 28MB，白名单外全部剔除
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
# 注意：.dll 经 reclassification 后同时存在于 a.datas 与 a.binaries，须双重过滤
_skip_plugins_dll = ('imageformats/qpdf.dll', 'sqldrivers/qsqlpsql.dll')
a.datas = [
    _d for _d in a.datas
    if not _d[0].replace('\\', '/').endswith(_skip_plugins_dll)
]
a.binaries = [
    _b for _b in a.binaries
    if not _b[0].replace('\\', '/').endswith(_skip_plugins_dll)
]
# MKL/LLVM 全量剔除（方案 D 核心）：环境中的 numpy 实为 PyPI openblas 版
# （numpy.libs 内 libscipy_openblas64 dll），但 hook-numpy 因 conda-meta 残留
# numpy 记录误判为 conda 版，collect_dynamic_libs 收进整套 MKL + omptarget
# 依赖（~420MB）。已实测：28 个 mkl/omptarget/sycl dll 全部排除后 numpy
# 全功能与 cv2 FFmpeg 视频读写均正常；mkl_fft/mkl_random 未被收集，无运行时依赖
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
    name='autowork',
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
    name='AutoWork',
)
