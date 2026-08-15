# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_submodules

# 自动收集 qfluentwidgets 全部子模块（含 _rc.resource 图标/QSS 资源）
# 排除 multimedia（需要 PySide6.QtMultimedia，本项目未使用）
qfw_hiddenimports = [
    m for m in collect_submodules('qfluentwidgets')
    if 'multimedia' not in m
]

# ---- conda 环境适配 ----
# conda 安装的 PySide6 不自带 Qt 二进制（无 PySide6/Qt6/bin），
# Qt DLL、shiboken6/pyside6 运行时 DLL 及插件均位于
# {env}/Library/bin 与 {env}/Library/lib/qt6/plugins，
# PyInstaller 无法自动解析，需显式收集；PyPI 版 PySide6 则跳过。
_conda_binaries = []
_conda_datas = []
# 用 Qt 自身探测安装前缀（conda 下为 {env}/Library，PyPI 版为包内 Qt6/）
from PySide6.QtCore import QLibraryInfo as _QLI
_qt_prefix = _QLI.path(_QLI.PrefixPath).replace('/', os.sep)
if _qt_prefix and os.path.basename(os.path.normpath(_qt_prefix)) == 'Library':
    _lib_bin = os.path.join(_qt_prefix, 'bin')
    _qt_plugins = os.path.join(_qt_prefix, 'lib', 'qt6', 'plugins')
    # Qt 核心/模块 DLL + 运行时依赖（OpenSSL、ffi、sqlite 等），按文件名小写匹配
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
    for _f in sorted(os.listdir(_lib_bin)):
        _low = _f.lower()
        if (_low.startswith('qt6') and _low.endswith('.dll')) \
                or _low in _support_dlls:
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
    ] + _conda_datas,
    hiddenimports=[
        'PySide6.QtWidgets',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtSvg',
        'PySide6.QtSvgWidgets',
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
    ] + qfw_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['qfluentwidgets.multimedia', 'scipy'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AutoWork',
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
