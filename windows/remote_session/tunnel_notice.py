# -*- coding: utf-8 -*-
"""隧道失联/恢复提示条（2026-09-23 P2-5）

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

_BG = "rgba(250, 173, 20, 0.14)"
_BORDER = "rgba(250, 173, 20, 0.55)"
_FG = "#d48806"

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
    """在会话面板顶部展示/更新失联提示条（供 mgr.notify_tunnel_issue 调）"""
    lay = panel.layout()
    if lay is None:
        return
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
        lay.insertWidget(0, bar)
        panel._tunnel_notice = bar
    text = (f"⚠ 隧道可能已失联（{snk}）：frps 显示设备离线或连接探测"
            f"连续超时，本会话可能已卡死；可回远程页重新连接。")
    bar._lbl.setText(text)
    bar.setToolTip(f"snk: {snk}")
    bar.show()


def clear(panel, snk: str):
    """撤除会话面板上的失联提示条（恢复或面板关闭时）"""
    bar = getattr(panel, "_tunnel_notice", None)
    if bar is None:
        return
    panel._tunnel_notice = None
    try:
        bar.hide()
        bar.deleteLater()
    except RuntimeError:
        pass  # C++ 对象已销毁
