# -*- coding: utf-8 -*-
"""隧道 / 远程会话窗口合并包

由原 ``windows/tunnel/``（隧道面板）与原 ``windows/session/``（远程会话
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

外部一律使用直路径 ``from windows.tunnel.xxx import ...``（旧 shim 已删除）。
"""

from windows.tunnel.window import TunnelPanelWindow  # noqa: F401
from windows.tunnel.remote_session_window import RemoteSessionWindow  # noqa: F401
from windows.tunnel.sftp_window import GLOBAL_SIGNALS, SFTPPanel, SFTPWindow  # noqa: F401
from windows.tunnel.ssh_terminal import (  # noqa: F401
    DEFAULT_SSH_COMMANDS, get_session_log_dir,
    SSHTerminalPanel, SshCommandEditDialog, SSHTerminalWindow,
)
from windows.tunnel.ansi_terminal import ANSITerminalWidget  # noqa: F401
from windows.tunnel.rdp_window import RDPPanel, RDPWindow  # noqa: F401
from windows.tunnel.conn_diag_panel import (  # noqa: F401
    LOG_NAME, parse_log_text, load_all_records,
    is_success_record, is_conn_fail_record, aggregate_stats,
    ConnDiagPanel,
)
from windows.tunnel.forensic_report import (  # noqa: F401
    FORENSIC_COMMANDS, get_forensic_dir, build_forensic_report,
    ForensicWorker,
)

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
    "ConnDiagPanel",
    # forensic_report
    "FORENSIC_COMMANDS", "get_forensic_dir", "build_forensic_report",
    "ForensicWorker",
]
