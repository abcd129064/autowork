# -*- coding: utf-8 -*-
"""隧道 / 远程会话窗口合并包

由原 ``windows/remote_session/``（隧道面板）与原 ``windows/session/``（远程会话
窗口）合并而成，承载：
- window.py               : TunnelPanelWindow 当前隧道列表窗口（FramelessWindow）
- remote_session_window.py : RemoteSessionWindow 会话标签容器（可承载
  SFTPPanel / SSHTerminalPanel / RDPPanel 面板）
- sftp_window.py           : SFTPWindow / SFTPPanel / GLOBAL_SIGNALS
- ssh_terminal.py          : SSHTerminalWindow / SSHTerminalPanel / 常用命令条
- ansi_terminal.py         : ANSITerminalWidget（被 ssh_terminal 依赖）
- rdp_window.py            : RDPWindow / RDPPanel
- conn_diag_panel.py       : ConnDiagPanel（SSH/SFTP 连接日志诊断）
- forensic_report.py       : SSH 故障一键取证（ForensicWorker + 报告生成）
- remote_hub.py            : RemoteHub 远程页（Pivot 四视图，2026-09-23 自
  main_window/ 迁入——远程页面文件统一归口本包）
- remote_mixin.py          : RemoteMixin 主窗口远程面板 Mixin（同上迁入）
- tunnel_notice.py         : 隧道失联/恢复会话面板提示条（P2-5，2026-09-23）

外部一律使用直路径 ``from windows.remote_session.xxx import ...``（旧 shim 已删除）。

惰性导出（2026-10-05）
---------------------
本包曾经 eager re-export 全部子模块，于是**任何** ``windows.remote_session.*`` 导入
都会连带拉起 ``window.py`` → ``qfluentwidgets``。后果：缺 qfluentwidgets 的解释器
（如 E:\\ANACONDA 基线）下连 ``ANSITerminalWidget`` 的纯控件单测都收集不了
（实测 8 个测试模块、13 项 collect error），每个新测试只能自己写"直载文件"绕行。
现改为 PEP 562 惰性转发：``from windows.remote_session import SSHTerminalPanel``
仍然可用，但只有真正取用某个名字时才 import 对应子模块。
"""

_EXPORTS = {
    # window
    "TunnelPanelWindow": "windows.remote_session.window",
    # remote_session_window
    "RemoteSessionWindow": "windows.remote_session.remote_session_window",
    # sftp_window
    "GLOBAL_SIGNALS": "windows.remote_session.sftp_window",
    "SFTPPanel": "windows.remote_session.sftp_window",
    "SFTPWindow": "windows.remote_session.sftp_window",
    # ssh_terminal
    "DEFAULT_SSH_COMMANDS": "windows.remote_session.ssh_terminal",
    "get_session_log_dir": "windows.remote_session.ssh_terminal",
    "SSHTerminalPanel": "windows.remote_session.ssh_terminal",
    "SshCommandEditDialog": "windows.remote_session.ssh_terminal",
    "SSHTerminalWindow": "windows.remote_session.ssh_terminal",
    # ansi_terminal
    "ANSITerminalWidget": "windows.remote_session.ansi_terminal",
    # rdp_window
    "RDPPanel": "windows.remote_session.rdp_window",
    "RDPWindow": "windows.remote_session.rdp_window",
    # conn_diag_panel
    "LOG_NAME": "windows.remote_session.conn_diag_panel",
    "parse_log_text": "windows.remote_session.conn_diag_panel",
    "load_all_records": "windows.remote_session.conn_diag_panel",
    "is_success_record": "windows.remote_session.conn_diag_panel",
    "is_conn_fail_record": "windows.remote_session.conn_diag_panel",
    "aggregate_stats": "windows.remote_session.conn_diag_panel",
    "ConnDiagPanel": "windows.remote_session.conn_diag_panel",
    "ConnDiagWidget": "windows.remote_session.conn_diag_panel",
    # forensic_report
    "FORENSIC_COMMANDS": "windows.remote_session.forensic_report",
    "get_forensic_dir": "windows.remote_session.forensic_report",
    "build_forensic_report": "windows.remote_session.forensic_report",
    "ForensicWorker": "windows.remote_session.forensic_report",
}

__all__ = [
    # window
    "TunnelPanelWindow",
    # remote_session_window
    "RemoteSessionWindow",
    # sftp_window
    "GLOBAL_SIGNALS", "SFTPPanel", "SFTPWindow",
    # ssh_terminal
    "DEFAULT_SSH_COMMANDS", "get_session_log_dir",
    "SSHTerminalPanel", "SshCommandEditDialog", "SSHTerminalWindow",
    # ansi_terminal
    "ANSITerminalWidget",
    # rdp_window
    "RDPPanel", "RDPWindow",
    # conn_diag_panel
    "LOG_NAME", "parse_log_text", "load_all_records",
    "is_success_record", "is_conn_fail_record", "aggregate_stats",
    "ConnDiagPanel", "ConnDiagWidget",
    # forensic_report
    "FORENSIC_COMMANDS", "get_forensic_dir", "build_forensic_report",
    "ForensicWorker",
]


def __getattr__(name):
    """PEP 562：按需导入子模块（保持 `from windows.remote_session import X` 兼容）"""
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    value = getattr(importlib.import_module(module_path), name)
    globals()[name] = value          # 缓存，后续取用直接命中
    return value


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS))
