# -*- coding: utf-8 -*-
"""运维管理面板专用 XTCP 远程桥接

从设备状态页右键菜单按 snk 标识（球桌 remark 中的 snk_xxx，即 frp xtcp
visitor 的 serverName）一键建立穿透隧道并打开 SSH/SFTP/RDP 会话。

与主窗口远程面板（RemoteMixin）相互独立：
- 使用专用 TOML（frpc_xtcp_panel.toml），不覆盖主窗口的 frpc_xtcp.toml
- visitor 列表按 snk 动态累积，同一 snk 复用已有隧道与端口
- frpc 进程与会话窗口的生命周期挂在运维面板窗口上，面板关闭即清理

SSH 凭据与 frpc 服务器配置复用 settings.json（ssh_user/ssh_pass/frpc_server），
与主窗口保持一致，无需重复配置。
"""
from __future__ import annotations

import json
import os
import sys

from PySide6.QtCore import QObject, QProcess, QTimer

try:
    import paramiko  # noqa: F401
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False

from core.app_paths import get_app_dir
from p2p import generate_random_port
from qfluentwidgets import InfoBar, InfoBarPosition

# 运维面板专用 TOML（与主窗口 frpc_xtcp.toml 隔离，避免互相覆盖 visitor 配置）
_PANEL_TOML_NAME = "frpc_xtcp_panel.toml"

# frpc 服务器默认配置（auth_token 由 settings.json 提供）
_FRPC_SERVER_DEFAULTS = {
    "serverAddr": "49.235.34.253",
    "serverPort": 7900,
    "auth_method": "token",
    "auth_token": "",
}

# 首次启动 frpc 后等待隧道建立的延时（ms）；复用已运行隧道时仅留少量缓冲
_FRESH_TUNNEL_DELAY_MS = 2500
_REUSE_TUNNEL_DELAY_MS = 300


def _load_settings() -> dict:
    """读取 settings.json，失败时返回空字典"""
    path = os.path.join(get_app_dir(), "settings.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


class FrpRemoteBridge(QObject):
    """按 snk 建立/复用 xtcp 隧道并打开远程会话（挂在运维面板窗口上）"""

    def __init__(self, owner_window):
        super().__init__(owner_window)
        self._owner = owner_window
        self._frpc_process: QProcess | None = None
        # serverName(snk) -> {"bindPort": int, "secretKey": str}
        self._visitors: dict = {}
        self._session_window = None

    # ---------- 对外入口 ----------

    def open_session(self, kind: str, snk: str, table_id: str):
        """打开指定类型的远程会话：kind ∈ {'ssh', 'sftp', 'rdp'}

        自动确保 frpc 运行且该 snk 的 visitor 已建立，延时等待隧道就绪后
        打开对应会话面板。
        """
        snk = str(snk or "").strip()
        if not snk:
            self._notify("无法远程", "该设备没有 snk 标识", error=True)
            return
        if kind in ("ssh", "sftp") and not PARAMIKO_AVAILABLE:
            self._notify("无法远程", "paramiko 未安装，无法建立 SSH/SFTP 会话", error=True)
            return
        if kind == "rdp" and sys.platform != "win32":
            self._notify("无法远程", "远程桌面仅支持 Windows", error=True)
            return

        try:
            port, fresh = self._ensure_visitor(snk)
        except (OSError, RuntimeError) as e:
            self._notify("远程准备失败", str(e), error=True)
            return
        delay = _FRESH_TUNNEL_DELAY_MS if fresh else _REUSE_TUNNEL_DELAY_MS
        self._notify("正在建立远程连接",
                     f"{table_id or snk} → {snk}（本地端口 {port}）")
        QTimer.singleShot(delay, lambda: self._do_open(kind, snk, table_id, port))

    def shutdown(self):
        """停止 frpc 并关闭会话窗口（运维面板关闭时调用）"""
        self._stop_frpc()
        win = self._session_window
        self._session_window = None
        if win is not None:
            try:
                win.close()
            except (RuntimeError, OSError):
                pass

    # ---------- frpc / visitor 管理 ----------

    def _ensure_visitor(self, snk: str) -> tuple:
        """确保 snk 对应 visitor 存在且 frpc 正在运行，返回 (bindPort, 是否新启动)"""
        if snk in self._visitors and self._frpc_process is not None:
            return self._visitors[snk]["bindPort"], False
        if snk not in self._visitors:
            settings = _load_settings()
            used_ports = {v["bindPort"] for v in self._visitors.values()}
            self._visitors[snk] = {
                "bindPort": generate_random_port(exclude_ports=used_ports),
                "secretKey": str(settings.get("xtcp_secret_key") or "abc123"),
            }
        self._restart_frpc()
        return self._visitors[snk]["bindPort"], True

    def _restart_frpc(self):
        """重写 TOML 并（重新）启动 frpc；新增 visitor 需重启才能生效"""
        app_dir = get_app_dir()
        frpc_exe = os.path.join(app_dir, "frpc.exe")
        if not os.path.exists(frpc_exe):
            raise OSError(f"frpc.exe 不存在: {frpc_exe}")
        self._stop_frpc()
        toml_path = os.path.join(app_dir, _PANEL_TOML_NAME)
        self._write_toml(toml_path)
        proc = QProcess(self._owner)
        proc.setWorkingDirectory(app_dir)
        proc.finished.connect(self._on_frpc_finished)
        proc.start(frpc_exe, ["-c", toml_path])
        self._frpc_process = proc

    def _stop_frpc(self):
        proc = self._frpc_process
        self._frpc_process = None
        if proc is not None:
            try:
                proc.finished.disconnect(self._on_frpc_finished)
            except (RuntimeError, TypeError):
                pass
            proc.kill()
            proc.waitForFinished(3000)
            proc.deleteLater()

    def _on_frpc_finished(self, _exit_code, _exit_status):
        """frpc 意外退出：清空进程引用（下次连接会自动重启）"""
        proc = self._frpc_process
        self._frpc_process = None
        if proc is not None:
            proc.deleteLater()

    def _write_toml(self, path: str):
        """生成运维面板专用的 frpc xtcp 配置"""
        settings = _load_settings()
        frpc_server = settings.get("frpc_server") or dict(_FRPC_SERVER_DEFAULTS)
        server_addr = frpc_server.get("serverAddr", _FRPC_SERVER_DEFAULTS["serverAddr"])
        server_port = frpc_server.get("serverPort", _FRPC_SERVER_DEFAULTS["serverPort"])
        auth_method = frpc_server.get("auth_method", _FRPC_SERVER_DEFAULTS["auth_method"])
        auth_token = frpc_server.get("auth_token", "")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f'serverAddr = "{server_addr}"\n')
            f.write(f'serverPort = {server_port}\n')
            f.write(f'auth.method = "{auth_method}"\n')
            f.write(f'auth.token = "{auth_token}"\n')
            f.write('\n')
            for sn, v in self._visitors.items():
                f.write("[[visitors]]\n")
                f.write(f'name = "{sn}"\n')
                f.write('type = "xtcp"\n')
                f.write(f'serverName = "{sn}"\n')
                f.write(f'secretKey = "{v["secretKey"]}"\n')
                f.write(f'bindPort = {v["bindPort"]}\n')
                f.write("\n")

    # ---------- 会话窗口 ----------

    def _do_open(self, kind: str, snk: str, table_id: str, port: int):
        """隧道就绪后实际打开会话面板（隧道在本地 127.0.0.1:port）"""
        # 会话面板依赖 paramiko 等重组件，延迟导入避免模块加载开销
        settings = _load_settings()
        username = settings.get("ssh_user", "")
        password = settings.get("ssh_pass", "")
        host = "127.0.0.1"
        title_snk = f"{table_id}（{snk}）" if table_id else snk
        try:
            if kind == "ssh":
                from windows.ssh_terminal import SSHTerminalPanel
                panel = SSHTerminalPanel(
                    host, port, username, password,
                    log_callback=lambda msg: None,
                    server_name=title_snk,
                )
            elif kind == "sftp":
                from windows.sftp_window import SFTPPanel
                panel = SFTPPanel(
                    host, port, username, password,
                    server_name=title_snk,
                    log_callback=lambda msg: None,
                    default_remote_path=settings.get("sftp_default_remote_path") or None,
                )
            else:  # rdp
                from windows.rdp_window import RDPPanel
                panel = RDPPanel(
                    host, port, username, password,
                    server_name=title_snk,
                    log_callback=lambda msg: None,
                )
        except Exception as e:
            self._notify("打开会话失败", str(e), error=True)
            return
        self._ensure_session_window().add_session(panel)

    def _ensure_session_window(self):
        """获取或创建远程会话标签容器窗口（单例复用，与主窗口同款）"""
        from windows.remote_session_window import RemoteSessionWindow
        win = self._session_window
        if win is not None:
            try:
                win.isVisible()  # 探测 C++ 对象是否已销毁
                win.show()
                win.raise_()
                win.activateWindow()
                return win
            except RuntimeError:
                self._session_window = None
        win = RemoteSessionWindow()
        win.destroyed.connect(lambda: setattr(self, "_session_window", None))
        self._session_window = win
        win.show()
        win.raise_()
        win.activateWindow()
        return win

    # ---------- 提示 ----------

    def _notify(self, title: str, msg: str, error: bool = False):
        """InfoBar 提示（右下角，与项目规范一致）"""
        try:
            if error:
                InfoBar.error(title, msg, parent=self._owner, duration=4000,
                              position=InfoBarPosition.BOTTOM_RIGHT)
            else:
                InfoBar.info(title, msg, parent=self._owner, duration=2000,
                             position=InfoBarPosition.BOTTOM_RIGHT)
        except Exception:
            pass
