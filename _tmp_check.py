# -*- coding: utf-8 -*-
"""临时验证脚本：确认 forensic_report 移动后导入正常（验证后删除）"""
import windows.tunnel as tunnel
import windows.tunnel.ssh_terminal as st
from windows.tunnel.forensic_report import (ForensicWorker,
                                            build_forensic_report,
                                            get_forensic_dir,
                                            FORENSIC_COMMANDS)
print("tunnel exports OK:", all(hasattr(tunnel, n) for n in (
    "ForensicWorker", "FORENSIC_COMMANDS", "build_forensic_report",
    "get_forensic_dir", "SSHTerminalPanel", "ConnDiagPanel")))
print("ssh_terminal imports forensic OK")
