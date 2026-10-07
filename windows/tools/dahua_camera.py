# -*- coding: utf-8 -*-
"""兼容 shim：相机页已升级为面板级 `windows/camera/`（2026-10-07）。

本文件仅 re-export 保持旧 import 路径可用（tests/ 与
tools/smoke/smoke_dahua_camera_ui.py 仍从 windows.tools.dahua_camera 导入）。
新代码请从 `windows.camera` 导入。
"""
from windows.camera.camera_page import (  # noqa: F401
    DahuaCameraWork,
    _DIR_KEYS,
    _PreviewCanvas,
    _SerialCaller,
)
