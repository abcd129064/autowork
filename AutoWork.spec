# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules

# 自动收集 qfluentwidgets 全部子模块（含 _rc.resource 图标/QSS 资源）
# 排除 multimedia（需要 PySide6.QtMultimedia，本项目未使用）
qfw_hiddenimports = [
    m for m in collect_submodules('qfluentwidgets')
    if 'multimedia' not in m
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        # styles/ 为只读主题资源，打包后位于 _internal/styles/，
        # 运行时通过 get_resource_dir()（sys._MEIPASS）读取
        ('styles', 'styles'),
        # 应用图标（窗口/任务栏图标，运行时经 get_resource_dir() 读取）
        ('app_icon.ico', '.'),
        # 注意：settings.json / frpc.exe / frpc_xtcp.toml 由 build_exe.py
        # 构建后复制到 dist 根目录（exe 旁边），不放入 datas；
        # autowork_with_table.py / p2p.py 已被 import 追踪编译进 PYZ，无需重复打包
    ],
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
        # 亚克力补丁依赖（打包环境无 numpy/scipy 时 PIL 替代实现需要）
        'PIL.ImageQt',
        'PIL.ImageFilter',
        'PIL.ImageEnhance',
    ] + qfw_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['qfluentwidgets.multimedia', 'scipy', 'numpy'],
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
