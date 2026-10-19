# -*- coding: utf-8 -*-
"""远程会话面板 UI 工厂：core 只描述「要开什么」，控件构造留在 windows 层。

背景（B2，docs/架构重构清单2026-10-18.md）：core/frp_remote.py 过去把
SSHTerminalPanel / SFTPPanel / RDPPanel / RemoteSessionWindow 的 import 写在
方法内部，虽避开了模块加载开销，却让 core 反向依赖 windows，分层检查一直
命中这 5 行；依赖方向也反了——core 是更底层的统一会话中心。

现改为主窗口启动时调用 install() 把本工厂注入 RemoteSessionManager，core 只
通过 manager 上四个 create_* 方法拿面板/窗口，不再 import 任何 windows 模块。
工厂方法内部的 import 仍是延迟的：既保留原有「点开会话才付出重组件加载代价」
的行为，也让测试可以整体替换 sys.modules 里的面板模块命中假实现。

本模块顶层因此刻意保持轻量（只用标准库）：注入动作发生在主窗口初始化阶段，
不能因为 import 本模块就把 qfluentwidgets 与四个重面板一起拉起来。
"""
from __future__ import annotations


class SessionUi:
    """RemoteSessionManager 的 UI 工厂生产实现（每个进程一个，由主窗口注入）。

    方法签名与 core 侧调用点逐参数对应，工厂只做转发、不改默认值——默认值
    若两边各写一份，早晚会漂移成「面板默认」与「core 期望」不一致。
    """

    def create_ssh_panel(self, host, port, username, password, *,
                         server_name="", log_callback=None, start_dir=""):
        """SSH 终端面板（SFTP「终端」按钮与 SSH 会话共用）。"""
        # 延迟 import：qfluentwidgets/paramiko 链路只在真正开面板时才加载
        from windows.remote_session.ssh_terminal import SSHTerminalPanel
        return SSHTerminalPanel(
            host, port, username, password,
            log_callback=log_callback,
            server_name=server_name,
            start_dir=start_dir,
        )

    def create_sftp_panel(self, host, port, username, password, *,
                          server_name="", log_callback=None,
                          default_remote_path=None, default_local_path=None):
        """SFTP 面板；本地/远端初始目录由调用方算好传入，工厂不碰 settings。"""
        from windows.remote_session.sftp_window import SFTPPanel
        return SFTPPanel(
            host, port, username, password,
            server_name=server_name,
            log_callback=log_callback,
            default_remote_path=default_remote_path,
            default_local_path=default_local_path,
        )

    def create_rdp_panel(self, host, port, username, password, *,
                         server_name="", log_callback=None):
        """RDP 面板。"""
        from windows.remote_session.rdp_window import RDPPanel
        return RDPPanel(
            host, port, username, password,
            server_name=server_name,
            log_callback=log_callback,
        )

    def create_session_window(self):
        """全局会话标签容器窗口（单例复用由 core 的 ensure_session_window 管）。"""
        from windows.remote_session.remote_session_window import RemoteSessionWindow
        return RemoteSessionWindow()


def install(manager) -> None:
    """把本工厂注入 RemoteSessionManager（幂等）。

    与 tunnel_notice.install 同一约定：已有工厂就原样保留，重复调用不会把
    测试注入的 fake 或子类实现覆盖掉——主窗口重建、多次调用都应安全。
    """
    if getattr(manager, "_session_ui", None) is not None:
        return
    manager.set_session_ui(SessionUi())
