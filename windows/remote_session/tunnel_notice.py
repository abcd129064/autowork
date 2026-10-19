# -*- coding: utf-8 -*-
"""会话面板顶部的隧道失联提示条：只告知风险，不强制断开会话。

隧道失联（frps 权威 offline / RTT 探测连败判异常）时，对该端口上**已
打开**的 SSH/SFTP/RDP 会话面板顶部插入一条非阻断提示条——会话不强制
关闭（SFTP 传输中不能杀），只告知「可能卡死、可回远程页重连」；恢复
（探测转 ok / frps 重新上线 / frpc 自愈成功）时自动撤除。

设计要点：
- 懒创建：提示条挂在 panel 自身属性（_tunnel_notice），首次告警才建，
  恢复即隐藏销毁，不给正常会话增加任何常驻开销；
- 插入 layout index 0（面板顶部），不改面板原有布局与信号；
- 面板布局无 layout 或已销毁（RuntimeError）时静默跳过——提示条是
  增益信息，绝不能因为它把会话面板搞挂。
"""
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

# 固定的琥珀色而不是主题语义色：深浅主题下都要读成「警告但非错误」，
# 借用主题的 info 色会被当成普通提示，danger 色又像已经断连。
_BG = "rgba(250, 173, 20, 0.14)"
_BORDER = "rgba(250, 173, 20, 0.55)"
_FG = "#d48806"

# 样式全部挂在 objectName 选择器下，避免顺着父子关系染到面板里其他控件。
_STYLE = f"""
QFrame#tunnelNoticeBar {{
    background: {_BG};
    border: 1px solid {_BORDER};
    border-radius: 6px;
}}
QFrame#tunnelNoticeBar QLabel {{
    background: transparent;
    color: {_FG};
}}
"""


def show(panel, snk: str):
    """在会话面板顶部展示或更新失联提示条，同一面板只保留一条。"""
    lay = panel.layout()
    if lay is None:
        return
    # 探测会周期性重发 lost，复用已有提示条，否则面板顶部会叠成一串同样的提示。
    bar = getattr(panel, "_tunnel_notice", None)
    if bar is None:
        bar = QFrame(panel)
        bar.setObjectName("tunnelNoticeBar")
        bar.setStyleSheet(_STYLE)
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 3, 10, 3)
        lbl = QLabel("", bar)
        lbl.setWordWrap(True)
        h.addWidget(lbl)
        bar._lbl = lbl
        # index 0 即面板顶部：不改面板原有布局与信号，恢复时整条移除即可。
        lay.insertWidget(0, bar)
        panel._tunnel_notice = bar
    text = (f"⚠ 隧道可能已失联（{snk}）：frps 显示设备离线或连接探测"
            f"连续超时，本会话可能已卡死；可回远程页重新连接。")
    bar._lbl.setText(text)
    bar.setToolTip(f"snk: {snk}")
    # 复用的提示条可能正处于隐藏态，不 show 时新文案看不见。
    bar.show()


def clear(panel, snk: str):
    """撤除会话面板上的失联提示条，恢复或面板关闭时调用。

    先摘掉 panel 上的引用再 deleteLater：同一轮里若再收到 lost，
    show() 会新建提示条，而不是拿到一个正在销毁的 QFrame。
    """
    bar = getattr(panel, "_tunnel_notice", None)
    if bar is None:
        return
    panel._tunnel_notice = None
    try:
        bar.hide()
        bar.deleteLater()
    except RuntimeError:
        pass  # C++ 对象已销毁


def install(manager) -> None:
    """把 RemoteSessionManager.tunnel_notice_requested 接到本模块（幂等）。

    core 层只发信号，UI 层在这里查绑定端口并给已打开面板加/撤提示条。
    """
    if getattr(manager, "_tunnel_notice_ui_installed", False):
        return
    # 先置位再 connect：重复调用不会挂上多个槽，on_show 只应生效一次。
    manager._tunnel_notice_ui_installed = True

    def _apply(snk: str, state: str):
        # 信号只带 serverName，端口得反查 _visitors；查不到说明隧道已移除，
        # 此时没有可提示的面板，直接返回。
        port = None
        for v in getattr(manager, "_visitors", {}).values():
            if v.get("serverName") == snk:
                port = v.get("bindPort")
                break
        if port is None:
            return
        for panel in manager.sessions_on_port(port):
            try:
                (show if state == "lost" else clear)(panel, snk)
            except RuntimeError:
                pass

    manager.tunnel_notice_requested.connect(_apply)
