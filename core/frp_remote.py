# -*- coding: utf-8 -*-
"""统一远程会话中心（单一 frpc 进程 + 单一 TOML）

整合此前两套相互隔离的 frpc/visitor 管理：
- 主窗口远程面板的手工 visitor（原 frpc_xtcp.toml + 自管 frpc 进程）
- 各面板按 snk 一键直连（原 FrpRemoteBridge / frpc_xtcp_panel.toml + 自管 frpc 进程）

RemoteSessionManager 为模块级单例（get_session_manager()）：
- 统一 visitor 注册表：手工 visitor 与 snk 快捷连接共享注册表，
  同一 serverName 已注册则复用现有隧道与本地端口，不重复建隧道
- 单一 frpc_xtcp_panel.toml + 单一 frpc 进程；visitor 变化时增量重写并重启 frpc
- frpc_xtcp.toml 仍同步维护（仅手工 visitor），用于下次启动恢复与"打开配置"查看
- open_session 保持与 FrpRemoteBridge 相同语义，便于调用方平滑迁移
- FrpRemoteBridge 保留为薄包装（委托 manager），仅为导入兼容

frpc 生命周期由主窗口 closeEvent 调用 manager.shutdown() 统一关闭；
各面板关闭时不再各自 shutdown，避免误杀其他入口仍在使用的隧道。

SSH 凭据与 frpc 服务器配置复用 settings.json（ssh_user/ssh_pass/frpc_server），
密码经 core/secrets.py DPAPI 解密层读取，生成 TOML 时写入解密后的值。
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

try:
    import paramiko  # noqa: F401
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False

from core.app_paths import get_app_dir
from core.secrets import decrypt_settings
from core.utils import show_info_bar
from p2p import generate_random_port

# 统一 TOML（所有 visitor：手工 + snk 快捷），frpc 实际加载该文件
_PANEL_TOML_NAME = "frpc_xtcp_panel.toml"
# 手工 visitor 持久化 TOML（启动恢复 / 配置查看用），由 manager 同步维护
_MAIN_TOML_NAME = "frpc_xtcp.toml"

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

# visitor 注册来源标识（展示用，可透传自定义文案）
SOURCE_MANUAL = "手工添加"
SOURCE_SNK = "snk 快捷"


def _load_settings() -> dict:
    """读取 settings.json（敏感字段透明解密），失败时返回空字典"""
    path = os.path.join(get_app_dir(), "settings.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return decrypt_settings(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _now_str() -> str:
    return datetime.now().strftime("%m-%d %H:%M")


def _parse_visitors_toml(toml_path: str) -> list:
    """解析 frpc TOML 中的 [[visitors]] 段，返回 visitor 字典列表"""
    visitors = []
    if not os.path.exists(toml_path):
        return visitors
    try:
        with open(toml_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return visitors
    for block in content.split("[[visitors]]")[1:]:
        m_server = re.search(r'serverName\s*=\s*"([^"]+)"', block)
        m_key = re.search(r'secretKey\s*=\s*"([^"]+)"', block)
        m_port = re.search(r'bindPort\s*=\s*(\d+)', block)
        if m_server and m_port:
            visitors.append({
                "serverName": m_server.group(1),
                "secretKey": m_key.group(1) if m_key else "abc123",
                "bindPort": int(m_port.group(1)),
            })
    return visitors


class RemoteSessionManager(QObject):
    """统一远程会话中心：单一 visitor 注册表 + 单一 frpc 进程/TOML（进程级单例）"""

    visitors_changed = Signal()       # visitor 注册表变化（增删改）
    frpc_state_changed = Signal(bool)  # frpc 运行状态变化（True=运行中）
    log_message = Signal(str)          # 运行日志（主窗口接入日志区）

    def __init__(self):
        super().__init__()
        self._frpc_process: QProcess | None = None
        # serverName -> {"serverName", "bindPort", "secretKey",
        #                "tableId", "source", "lastUsed"}
        self._visitors: dict = {}
        self._session_window = None
        self._load_manual_toml()

    # ---------- visitor 注册表 ----------

    def _load_manual_toml(self):
        """启动时从 frpc_xtcp.toml 恢复手工 visitor（不自动启动 frpc）"""
        app_dir = get_app_dir()
        for v in _parse_visitors_toml(os.path.join(app_dir, _MAIN_TOML_NAME)):
            self._visitors[v["serverName"]] = {
                "serverName": v["serverName"],
                "bindPort": v["bindPort"],
                "secretKey": v["secretKey"],
                "tableId": "",
                "source": SOURCE_MANUAL,
                "lastUsed": "",
            }

    def register_visitor(self, server_name: str, bind_port: int | None = None,
                         secret_key: str | None = None, source: str = SOURCE_MANUAL,
                         table_id: str = "") -> tuple:
        """注册/更新 visitor，返回 (bindPort, 注册表是否变化)

        同一 serverName 已注册则复用（更新端口/密钥/关联球桌），不重复建隧道。
        显式指定的 bindPort 与其他 visitor 冲突时抛 RuntimeError。
        """
        server_name = str(server_name or "").strip()
        if not server_name:
            raise ValueError("serverName 不能为空")
        info = self._visitors.get(server_name)
        changed = False
        if info is None:
            port = int(bind_port) if bind_port else \
                generate_random_port(exclude_ports=self.used_ports())
            self._visitors[server_name] = {
                "serverName": server_name,
                "bindPort": port,
                "secretKey": str(secret_key or self._default_secret_key()),
                "tableId": str(table_id or ""),
                "source": str(source or SOURCE_MANUAL),
                "lastUsed": "",
            }
            changed = True
        else:
            if bind_port:
                port = int(bind_port)
                if port != info["bindPort"]:
                    owner = self._port_owner(port)
                    if owner is not None:
                        raise RuntimeError(f"端口 {port} 已被 {owner} 使用")
                    info["bindPort"] = port
                    changed = True
            if secret_key and str(secret_key) != info["secretKey"]:
                info["secretKey"] = str(secret_key)
                changed = True
            if table_id:
                info["tableId"] = str(table_id)
            info["source"] = str(source or info["source"])
        if changed:
            self.visitors_changed.emit()
        return self._visitors[server_name]["bindPort"], changed

    def ensure_visitor(self, snk: str, table_id: str = "",
                       source: str = SOURCE_SNK) -> tuple:
        """确保 snk 对应 visitor 已注册且 frpc 正在运行，返回 (bindPort, 是否新启动)"""
        snk = str(snk or "").strip()
        if not snk:
            raise ValueError("snk 不能为空")
        info = self._visitors.get(snk)
        if info is not None and self.is_running():
            info["lastUsed"] = _now_str()
            if table_id:
                info["tableId"] = str(table_id)
            return info["bindPort"], False
        self.register_visitor(snk, source=source, table_id=table_id)
        self.apply()
        info = self._visitors[snk]
        info["lastUsed"] = _now_str()
        return info["bindPort"], True

    def remove_visitor(self, server_name: str) -> bool:
        """移除指定 visitor（不触发 frpc 重启，需调用方 apply）"""
        info = self._visitors.pop(str(server_name or "").strip(), None)
        if info is not None:
            self.visitors_changed.emit()
            return True
        return False

    def remove_visitors_by_source(self, source: str) -> int:
        """按来源批量移除 visitor（如主面板断开时移除全部手工 visitor）"""
        names = [sn for sn, v in self._visitors.items() if v["source"] == source]
        for sn in names:
            self._visitors.pop(sn, None)
        if names:
            self.visitors_changed.emit()
        return len(names)

    def disconnect_visitor(self, server_name: str) -> bool:
        """移除 visitor 并立即应用（隧道面板"断开"按钮用）"""
        if not self.remove_visitor(server_name):
            return False
        try:
            self.apply()
        except (OSError, RuntimeError) as e:
            self.log_message.emit(f"[远程会话] 应用变更失败: {e}")
        return True

    def records(self) -> list:
        """全部 visitor 记录（浅拷贝，供隧道面板展示）"""
        return [dict(v) for v in self._visitors.values()]

    def manual_visitors(self) -> list:
        """手工来源的 visitor（供主窗口远程面板表单恢复）"""
        return [{
            "serverName": v["serverName"],
            "bindPort": v["bindPort"],
            "secretKey": v["secretKey"],
        } for v in self._visitors.values() if v["source"] == SOURCE_MANUAL]

    def used_ports(self) -> set:
        return {v["bindPort"] for v in self._visitors.values()}

    def _port_owner(self, port: int):
        for sn, v in self._visitors.items():
            if v["bindPort"] == int(port):
                return sn
        return None

    @staticmethod
    def _default_secret_key() -> str:
        return str(_load_settings().get("xtcp_secret_key") or "abc123")

    # ---------- frpc 进程 / TOML ----------

    def is_running(self) -> bool:
        return self._frpc_process is not None

    def apply(self):
        """按当前注册表重写 TOML 并（重新）启动 frpc；注册表为空时停止 frpc"""
        app_dir = get_app_dir()
        self._write_manual_toml(os.path.join(app_dir, _MAIN_TOML_NAME))
        if not self._visitors:
            self._stop_frpc()
            return
        frpc_exe = os.path.join(app_dir, "frpc.exe")
        if not os.path.exists(frpc_exe):
            raise OSError(f"frpc.exe 不存在: {frpc_exe}")
        self._stop_frpc()
        toml_path = os.path.join(app_dir, _PANEL_TOML_NAME)
        self._write_toml(toml_path)
        proc = QProcess(self)
        proc.setWorkingDirectory(app_dir)
        proc.readyReadStandardOutput.connect(self._on_frpc_output)
        proc.readyReadStandardError.connect(self._on_frpc_error)
        proc.finished.connect(self._on_frpc_finished)
        proc.start(frpc_exe, ["-c", toml_path])
        self._frpc_process = proc
        self.frpc_state_changed.emit(True)

    def _stop_frpc(self):
        proc = self._frpc_process
        self._frpc_process = None
        if proc is not None:
            try:
                proc.readyReadStandardOutput.disconnect(self._on_frpc_output)
                proc.readyReadStandardError.disconnect(self._on_frpc_error)
                proc.finished.disconnect(self._on_frpc_finished)
            except (RuntimeError, TypeError):
                pass
            proc.finished.connect(self._on_stop_cleanup_done)
            proc.kill()
            self.frpc_state_changed.emit(False)

    def _on_stop_cleanup_done(self, *_args):
        """frpc 进程停止后的清理回调（替代 waitForFinished 阻塞等待）"""
        proc = self.sender()
        if proc is not None:
            proc.deleteLater()

    def _on_frpc_finished(self, exit_code, _exit_status):
        """frpc 意外退出：清空进程引用（下次连接会自动重启）"""
        proc = self._frpc_process
        self._frpc_process = None
        if proc is not None:
            proc.deleteLater()
            self.frpc_state_changed.emit(False)
            self.log_message.emit(f"[远程会话] frpc 已退出，退出码: {exit_code}")

    def _on_frpc_output(self):
        proc = self._frpc_process
        if proc is not None:
            output = proc.readAllStandardOutput().data().decode("utf-8", errors="ignore")
            if output.strip():
                self.log_message.emit(f"[frpc] {output.strip()}")

    def _on_frpc_error(self):
        proc = self._frpc_process
        if proc is not None:
            error = proc.readAllStandardError().data().decode("utf-8", errors="ignore")
            if error.strip():
                self.log_message.emit(f"[frpc] {error.strip()}")

    def _write_toml(self, path: str):
        """生成统一 frpc xtcp 配置（所有 visitor）"""
        settings = _load_settings()
        frpc_server = settings.get("frpc_server") or dict(_FRPC_SERVER_DEFAULTS)
        server_addr = frpc_server.get("serverAddr", _FRPC_SERVER_DEFAULTS["serverAddr"])
        server_port = frpc_server.get("serverPort", _FRPC_SERVER_DEFAULTS["serverPort"])
        auth_method = frpc_server.get("auth_method", _FRPC_SERVER_DEFAULTS["auth_method"])
        auth_token = frpc_server.get("auth_token", "")
        if not auth_token:
            self.log_message.emit("[远程会话] 警告: frpc auth_token 未配置，"
                                  "请在 设置 → 认证 Token 中填写")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f'serverAddr = "{server_addr}"\n')
            f.write(f'serverPort = {server_port}\n')
            f.write(f'auth.method = "{auth_method}"\n')
            f.write(f'auth.token = "{auth_token}"\n')
            f.write('\n')
            self._write_visitor_blocks(f)

    def _write_manual_toml(self, path: str):
        """同步维护 frpc_xtcp.toml（仅手工 visitor，启动恢复/配置查看用）"""
        try:
            with open(path, "w", encoding="utf-8") as f:
                self._write_visitor_blocks(f, source=SOURCE_MANUAL)
        except OSError:
            pass

    def _write_visitor_blocks(self, f, source: str | None = None):
        for sn, v in self._visitors.items():
            if source is not None and v["source"] != source:
                continue
            f.write("[[visitors]]\n")
            f.write(f'name = "{sn}"\n')
            f.write('type = "xtcp"\n')
            f.write(f'serverName = "{sn}"\n')
            f.write(f'secretKey = "{v["secretKey"]}"\n')
            f.write(f'bindPort = {v["bindPort"]}\n')
            f.write("\n")

    # ---------- 对外入口（与 FrpRemoteBridge.open_session 同语义） ----------

    def open_session(self, kind: str, snk: str, table_id: str,
                     notifier=None, source: str = SOURCE_SNK):
        """打开指定类型的远程会话：kind ∈ {'ssh', 'sftp', 'rdp'}

        自动确保 frpc 运行且该 snk 的 visitor 已建立，延时等待隧道就绪后
        打开对应会话面板。notifier 为 InfoBar 提示的宿主控件（可选）。
        """
        snk = str(snk or "").strip()
        if not snk:
            self._notify("无法远程", "该设备没有 snk 标识", error=True, notifier=notifier)
            return
        if kind in ("ssh", "sftp") and not PARAMIKO_AVAILABLE:
            self._notify("无法远程", "paramiko 未安装，无法建立 SSH/SFTP 会话",
                         error=True, notifier=notifier)
            return
        if kind == "rdp" and sys.platform != "win32":
            self._notify("无法远程", "远程桌面仅支持 Windows", error=True, notifier=notifier)
            return

        try:
            port, fresh = self.ensure_visitor(snk, table_id=table_id, source=source)
        except (OSError, RuntimeError, ValueError) as e:
            self._notify("远程准备失败", str(e), error=True, notifier=notifier)
            return
        delay = _FRESH_TUNNEL_DELAY_MS if fresh else _REUSE_TUNNEL_DELAY_MS
        msg = f"{table_id or snk} → {snk}（本地端口 {port}）"
        # SFTP 会话按球桌号在 videos_dir 下自动建本地目录，下载直接落位
        if kind == "sftp" and table_id:
            videos_dir = str(_load_settings().get("videos_dir") or "").strip()
            if videos_dir and os.path.isdir(videos_dir):
                msg += f"，本地目录 videos{os.sep}{table_id}"
        self._notify("正在建立远程连接", msg, notifier=notifier)
        QTimer.singleShot(delay, lambda: self._do_open(kind, snk, table_id, port, notifier))

    def _do_open(self, kind: str, snk: str, table_id: str, port: int, notifier=None):
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
                # snk 会话：本地初始目录 = videos_dir/{球桌号}（不存在自动创建）
                local_dir = None
                videos_dir = str(settings.get("videos_dir") or "").strip()
                if table_id and videos_dir and os.path.isdir(videos_dir):
                    local_dir = os.path.join(videos_dir, str(table_id).strip())
                panel = SFTPPanel(
                    host, port, username, password,
                    server_name=title_snk,
                    log_callback=lambda msg: None,
                    default_remote_path=settings.get("sftp_default_remote_path") or None,
                    default_local_path=local_dir,
                )
            else:  # rdp
                from windows.rdp_window import RDPPanel
                panel = RDPPanel(
                    host, port, username, password,
                    server_name=title_snk,
                    log_callback=lambda msg: None,
                )
        except Exception as e:
            self._notify("打开会话失败", str(e), error=True, notifier=notifier)
            return
        self.ensure_session_window().add_session(panel)

    # ---------- 全局会话窗口 ----------

    def ensure_session_window(self):
        """获取或创建全局远程会话标签容器窗口（单例复用）"""
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

    # ---------- 生命周期 ----------

    def shutdown(self):
        """停止 frpc 并关闭全局会话窗口（仅主窗口 closeEvent 调用）"""
        self._stop_frpc()
        win = self._session_window
        self._session_window = None
        if win is not None:
            try:
                win.close()
            except (RuntimeError, OSError):
                pass

    # ---------- 提示 ----------

    def _notify(self, title: str, msg: str, error: bool = False, notifier=None):
        """InfoBar 提示（右下角，与项目规范一致）"""
        try:
            parent = notifier
            if parent is None:
                from PySide6.QtWidgets import QApplication
                parent = QApplication.activeWindow()
            if error:
                show_info_bar(msg, "error", title=title, parent=parent, duration=4000)
            else:
                show_info_bar(msg, "info", title=title, parent=parent, duration=2000)
        except Exception:
            pass


# ---------------------------------------------------------------------- 单例

_session_manager: RemoteSessionManager | None = None


def get_session_manager() -> RemoteSessionManager:
    """获取统一远程会话中心单例（需在 QApplication 创建后调用）"""
    global _session_manager
    if _session_manager is None:
        _session_manager = RemoteSessionManager()
    return _session_manager


# ---------------------------------------------------------------------- 兼容层

class FrpRemoteBridge(QObject):
    """兼容薄包装：全部委托 RemoteSessionManager（新代码请直接使用 get_session_manager）

    历史上每个面板各自持有 FrpRemoteBridge 并自管 frpc 进程，导致同一设备
    从不同入口连接会各建隧道、重复占用本地端口；现统一由 manager 管理。
    """

    def __init__(self, owner_window):
        super().__init__(owner_window)
        self._owner = owner_window

    def open_session(self, kind: str, snk: str, table_id: str, notifier=None):
        get_session_manager().open_session(kind, snk, table_id,
                                           notifier=notifier or self._owner)

    def shutdown(self):
        """兼容保留：frpc 生命周期现由主窗口 closeEvent 统一关闭 manager，
        单个面板关闭不再 shutdown，避免误杀其他入口仍在使用的隧道。"""
        pass
