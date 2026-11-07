# -*- coding: utf-8 -*-
"""用「依赖齐全但 PySide6 需要引导」的解释器跑 GUI 测试与冒烟脚本。

背景：本机两个解释器是分裂的（AGENTS.md §5.1）——E:\\ANACONDA 的 PySide6 能用但
没装 qfluentwidgets，miniconda3 依赖齐全却裸 import PySide6 会 DLL load failed。
按 docs/Qt内联引导说明.md，在 import PySide6 之前先把 PySide6 自带的 Qt 搜索
路径固定下来，miniconda3 就能跑相机/弹窗这类 GUI 测试。本脚本只做引导，
不安装、不改动任何环境。
用法：
    QT_QPA_PLATFORM=offscreen C:\\Users\\shen_zhe\\miniconda3\\python.exe \
        tools/run_with_qt.py pytest tests/test_dahua_camera_ui.py -q
    QT_QPA_PLATFORM=offscreen C:\\Users\\shen_zhe\\miniconda3\\python.exe \
        tools/run_with_qt.py run tools/smoke/smoke_dahua_camera_ui.py
产物：无（测试自身不落盘）
"""
import importlib.util as _qt_iu
import os as _os
import sys

# 引导阶段：固定 PySide6 自带 Qt 的 DLL 搜索路径与插件目录。
# ⚠ _qt_handles 必须赋值，句柄被 GC 掉则刚固定的路径立刻失效。
_qt_handles = []
try:
    _qt_spec = _qt_iu.find_spec('PySide6')
    if _qt_spec is not None:
        _qt_locs = list(getattr(_qt_spec, 'submodule_search_locations', None) or [])
        if _qt_locs:
            _qt_pkg = _qt_locs[0]
            for _d in (_qt_pkg, _os.path.dirname(_qt_pkg),
                       _os.path.join(_os.environ.get('SystemRoot', r'C:\Windows'),
                                     'System32')):
                if _os.path.isdir(_d):
                    try:
                        _qt_handles.append(_os.add_dll_directory(_d))
                    except OSError:
                        pass
            _os.environ['QT_PLUGIN_PATH'] = _os.path.join(_qt_pkg, 'plugins')
            _os.environ.setdefault('QT_QPA_PLATFORM_PLUGIN_PATH',
                                   _os.path.join(_qt_pkg, 'plugins', 'platforms'))
except Exception:  # noqa: BLE001 引导失败也只是退回原生报错，不该盖住真实原因
    pass

# tools/ 根 → 上溯两层 = 仓库根
ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import core.acrylic_patch  # noqa: F401,E402  必须早于 qfluentwidgets 导入

# 控制台默认 GBK，测试输出里的中文与断言消息要先切 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

_mode = sys.argv[1] if len(sys.argv) > 1 else "pytest"
_args = sys.argv[2:]

if not _args:
    print("用法：tools/run_with_qt.py <pytest|run> <参数…>")
    raise SystemExit(2)

if _mode == "pytest":
    import pytest
    raise SystemExit(pytest.main(_args))
if _mode == "run":
    # 目标脚本按 __main__ 跑：它自己的 __file__ 上溯定位仓库根仍然成立。
    # run_path 的返回值是脚本的 globals 字典，不能直接当退出码传给 SystemExit
    import runpy
    runpy.run_path(_args[0], run_name="__main__")
    raise SystemExit(0)
print(f"未知模式 {_mode}（支持 pytest / run）")
raise SystemExit(2)
