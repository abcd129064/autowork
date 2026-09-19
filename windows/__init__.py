# -*- coding: utf-8 -*-
"""windows 包：独立对话框窗口（SFTP/SSH终端/远程桌面）

窗口类走 PEP 562 懒加载：`from windows import SFTPWindow` 照旧可用，
但 `import windows` 本身不再拉起 PySide6/paramiko——否则子包里的纯逻辑
模块（如 windows.tools.single_shot_video 渲染、windows.tools.newlog 整理）
也会被连带拖进 GUI 依赖，脱离界面无法单独导入/测试。
"""

# 属性名 → 所属模块（首次访问时才 import 对应模块）
_LAZY_EXPORTS = {
    "SFTPWindow": ".remote_session.sftp_window",
    "SSHTerminalWindow": ".remote_session.ssh_terminal",
    "RDPWindow": ".remote_session.rdp_window",
}


def __getattr__(name):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module
    value = getattr(import_module(target, __name__), name)
    globals()[name] = value          # 缓存，后续访问走普通属性查找
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
