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
# 注册表元数据快照（全部 visitor 含 tableId/source/lastUsed），启动恢复用；
# frpc_xtcp.toml 只能存 serverName/端口/密钥，关联球桌等元数据靠该文件补齐
_META_NAME = "frpc_xtcp_meta.json"

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
SOURCE_TABLE = "球桌库"  # 远程面板「从球桌库选择」添加

# 面板 visitor 列表（手工 + 球桌库）：连接时整组重建、写入 frpc_xtcp.toml 供恢复
_PANEL_SOURCES = (SOURCE_MANUAL, SOURCE_TABLE)


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
    visitor_removed = Signal(str)      # visitor 被「删除 snk」彻底移除（主窗口列表同步清理用）

    def __init__(self):
        super().__init__()
        self._frpc_process: QProcess | None = None
        # serverName -> {"serverName", "bindPort", "secretKey",
        #                "tableId", "source", "lastUsed"}
        self._visitors: dict = {}
        self._session_window = None
        self._load_registry()

    # ---------- visitor 注册表 ----------

    def _load_registry(self):
        """启动恢复 visitor 注册表（不自动启动 frpc）

        优先读 frpc_xtcp_meta.json 全量快照（含 snk 快捷隧道及关联球桌/
        来源/最近使用），缺失或损坏时回退解析 frpc_xtcp.toml 仅恢复
        手工 visitor。断开后「当前隧道」面板仍能看到已知隧道即靠此恢复。
        """
        app_dir = get_app_dir()
        try:
            with open(os.path.join(app_dir, _META_NAME), "r",
                      encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = None
        if isinstance(data, list):
            # meta 允许部分损坏：逐条校验，字段不全的条目跳过，
            # 不因为一条坏数据就放弃整个快照的恢复
            for v in data:
                if not isinstance(v, dict):
                    continue
                name = str(v.get("serverName") or "").strip()
                try:
                    port = int(v.get("bindPort") or 0)
                except (TypeError, ValueError):
                    port = 0
                if not name or not port:
                    continue
                self._visitors[name] = {
                    "serverName": name,
                    "bindPort": port,
                    "secretKey": str(v.get("secretKey") or "")
                    or self._default_secret_key(),
                    "tableId": str(v.get("tableId") or ""),
                    "source": str(v.get("source") or SOURCE_MANUAL),
                    "lastUsed": str(v.get("lastUsed") or ""),
                }
            # meta 恢复出内容就到此为止：TOML 回退只在 meta 完全不可用时兜底，
            # 两源混读反而会让旧的 TOML 数据覆盖新的 meta 快照
            if self._visitors:
                return
        # 回退：元数据缺失（旧版本升级）时按 TOML 恢复手工 visitor
        for v in _parse_visitors_toml(os.path.join(app_dir, _MAIN_TOML_NAME)):
            self._visitors[v["serverName"]] = {
                "serverName": v["serverName"],
                "bindPort": v["bindPort"],
                "secretKey": v["secretKey"],
                "tableId": "",
                "source": SOURCE_MANUAL,
                "lastUsed": "",
            }

    def _write_registry_meta(self, path: str):
        """写出注册表元数据快照（apply 时调用，供下次启动完整恢复）"""
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(list(self._visitors.values()), f,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _persist_registry(self):
        """落盘持久化文件（不触碰 frpc 进程）：手工/球桌库 TOML + 全量 meta 快照"""
        app_dir = get_app_dir()
        self._write_manual_toml(os.path.join(app_dir, _MAIN_TOML_NAME))
        self._write_registry_meta(os.path.join(app_dir, _META_NAME))

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
            # 新注册：未指定端口时随机分配一个未被占用的端口
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
            # 已存在：仅更新显式传入且有变化的字段，
            # 未指定端口时保持原端口不变（避免每次调用都换端口）
            if bind_port:
                port = int(bind_port)
                if port != info["bindPort"]:
                    # 新端口若已被其他 visitor 占用则报错，防止隧道端口冲突
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
        # changed 标志：仅注册表有实质变化时才发信号，
        # 让 UI（隧道面板）避免无谓刷新
        if changed:
            self.visitors_changed.emit()
        return self._visitors[server_name]["bindPort"], changed

    def ensure_visitor(self, snk: str, table_id: str = "",
                       source: str = SOURCE_SNK) -> tuple:
        """确保 snk 对应 visitor 已注册且 frpc 正在运行，返回 (bindPort, 是否新启动)

        复用/新建后统一经 mark_used 刷新最近使用时间并发 visitors_changed，
        保证隧道面板能立刻看到「最近使用/关联球桌」数据。
        """
        snk = str(snk or "").strip()
        if not snk:
            raise ValueError("snk 不能为空")
        info = self._visitors.get(snk)
        # 已注册且 frpc 在运行：直接复用现有隧道（不重启），只刷新使用时间
        if info is not None and self.is_running():
            self.mark_used(snk, table_id)
            return info["bindPort"], False
        # 否则注册并重启 frpc 应用新配置（新隧道或 frpc 已退出）
        self.register_visitor(snk, source=source, table_id=table_id)
        self.apply()
        self.mark_used(snk, table_id)
        return self._visitors[snk]["bindPort"], True

    def mark_used(self, server_name: str, table_id: str = ""):
        """刷新 visitor 最近使用时间（可顺带补关联球桌）并通知 UI 刷新"""
        info = self._visitors.get(str(server_name or "").strip())
        if info is None:
            return
        info["lastUsed"] = _now_str()
        if table_id:
            info["tableId"] = str(table_id)
        self.visitors_changed.emit()

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

    def disconnect_visitor(self, server_name: str) -> str:
        """隧道面板「断开连接」：仅在 frpc 运行中生效，绝不自动启动 frpc

        完整断开流程：先优雅关闭该隧道端口上的 SSH/SFTP/RDP 会话
        （panel.shutdown()，避免隧道丢失后窗口假死），再移除 visitor 并
        apply（frpc 按新配置重启释放端口，注册表清空则停止 frpc）。

        Returns:
            "ok" 断开成功；"not_running" frpc 未启动（未做任何改动）；
            "not_found" visitor 不存在；"error" 应用变更失败。
        """
        name = str(server_name or "").strip()
        info = self._visitors.get(name)
        if info is None:
            return "not_found"
        if not self.is_running():
            # frpc 未启动时隧道本未建立，无断开可做；
            # 千万不能在此 apply()，否则会带着剩余 visitor 自动拉起 frpc
            return "not_running"
        self.close_sessions_on_port(info["bindPort"], reason=name)
        self.remove_visitor(name)
        try:
            self.apply()
        except (OSError, RuntimeError) as e:
            self.log_message.emit(f"[远程会话] 应用变更失败: {e}")
            return "error"
        return "ok"

    def delete_visitor(self, server_name: str) -> str:
        """隧道面板「删除 snk」：从注册表与持久化文件中彻底移除 visitor

        与「断开连接」的区别：frpc 未运行时也执行（仅移除并重写持久化
        文件，绝不启动 frpc）；frpc 运行中则先关闭相关会话再移除并 apply。
        移除成功后发 visitor_removed 信号，主窗口远程面板据此同步清理列表。
        Returns 含义同 disconnect_visitor。
        """
        name = str(server_name or "").strip()
        info = self._visitors.get(name)
        if info is None:
            return "not_found"
        if self.is_running():
            self.close_sessions_on_port(info["bindPort"], reason=name)
        self.remove_visitor(name)
        self.visitor_removed.emit(name)
        if self.is_running():
            try:
                self.apply()
            except (OSError, RuntimeError) as e:
                self.log_message.emit(f"[远程会话] 应用变更失败: {e}")
                return "error"
            return "ok"
        # frpc 未启动：仅移除并重写持久化文件（下次启动不再恢复），不启动 frpc
        self._persist_registry()
        return "ok"

    def records(self) -> list:
        """全部 visitor 记录（浅拷贝，供隧道面板展示）"""
        return [dict(v) for v in self._visitors.values()]

    def manual_visitors(self) -> list:
        """手工/球桌库来源的 visitor（供主窗口远程面板表单恢复）"""
        return [{
            "serverName": v["serverName"],
            "bindPort": v["bindPort"],
            "secretKey": v["secretKey"],
            "tableId": v["tableId"],
            "source": v["source"],
        } for v in self._visitors.values() if v["source"] in _PANEL_SOURCES]

    def used_ports(self) -> set:
        return {v["bindPort"] for v in self._visitors.values()}

    def _port_owner(self, port: int):
        # 反查端口归属：冲突报错时能明确告诉用户端口被哪个隧道占了
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
        # 先落盘持久化（frpc_xtcp.toml + 全量 meta），再处理 frpc 进程
        self._persist_registry()
        if not self._visitors:
            # 注册表为空说明没有隧道需要维护，停掉 frpc 避免空转
            self._stop_frpc()
            return
        app_dir = get_app_dir()
        frpc_exe = os.path.join(app_dir, "frpc.exe")
        if not os.path.exists(frpc_exe):
            raise OSError(f"frpc.exe 不存在: {frpc_exe}")
        # 先停旧进程再启新进程：旧进程持有旧配置（端口/visitor 列表），
        # 直接复用会导致新配置不生效或端口冲突
        self._stop_frpc()
        toml_path = os.path.join(app_dir, _PANEL_TOML_NAME)
        self._write_toml(toml_path)
        proc = QProcess(self)
        proc.setWorkingDirectory(app_dir)
        # 信号在 start 之前接好：frpc 可能在极短时间内退出，晚接会错过 finished 事件
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
                # 先摘掉常规回调：kill() 也会触发 finished 信号，
                # 若不清除会误走 _on_frpc_finished 的「意外退出」分支
                proc.readyReadStandardOutput.disconnect(self._on_frpc_output)
                proc.readyReadStandardError.disconnect(self._on_frpc_error)
                proc.finished.disconnect(self._on_frpc_finished)
            except (RuntimeError, TypeError):
                pass  # 信号未连接过时 disconnect 抛异常，忽略即可
            # 换成清理专用回调：进程真正结束后删除 QProcess 对象释放资源
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
        """同步维护 frpc_xtcp.toml（手工 + 球桌库 visitor，启动恢复/配置查看用）"""
        try:
            with open(path, "w", encoding="utf-8") as f:
                self._write_visitor_blocks(f, sources=_PANEL_SOURCES)
        except OSError:
            pass

    def _write_visitor_blocks(self, f, sources=None):
        for sn, v in self._visitors.items():
            if sources is not None and v["source"] not in sources:
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
                from windows.tunnel.ssh_terminal import SSHTerminalPanel
                panel = SSHTerminalPanel(
                    host, port, username, password,
                    log_callback=lambda msg: None,
                    server_name=title_snk,
                )
            elif kind == "sftp":
                from windows.tunnel.sftp_window import SFTPPanel
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
                from windows.tunnel.rdp_window import RDPPanel
                panel = RDPPanel(
                    host, port, username, password,
                    server_name=title_snk,
                    log_callback=lambda msg: None,
                )
        except Exception as e:
            self._notify("打开会话失败", str(e), error=True, notifier=notifier)
            return
        self.ensure_session_window().add_session(panel)

    # ---------- 会话联动（隧道断开时同步处理已打开的 SSH/SFTP/RDP 会话） ----------

    def _live_session_window(self):
        """获取全局会话窗口（未创建或 C++ 对象已销毁时返回 None）"""
        win = self._session_window
        if win is None:
            return None
        # Python 包装对象还在不代表 C++ 侧活着：会话窗口带 WA_DeleteOnClose，
        # 用户关掉后 C++ 对象即销毁，此时任何 Qt 方法调用都抛 RuntimeError，
        # 正好拿 isVisible() 当探针，比拿着悬挂引用继续操作安全
        try:
            win.isVisible()  # 探测 C++ 对象是否已销毁
            return win
        except RuntimeError:
            self._session_window = None
            return None

    def sessions_on_port(self, port) -> list:
        """指定本地端口上已打开的全部会话面板（SSH/SFTP/RDP，无则空列表）"""
        win = self._live_session_window()
        if win is None:
            return []
        try:
            port = int(port)
        except (TypeError, ValueError):
            return []
        panels = []
        for p in list(getattr(win, "_panels", [])):
            try:
                if int(getattr(p, "_port", 0) or 0) == port:
                    panels.append(p)
            except (TypeError, ValueError):
                continue
        return panels

    def is_transferring_on_port(self, port) -> bool:
        """指定端口上的 SFTP 会话是否有文件传输进行中（含暂停未结束的任务）"""
        for p in self.sessions_on_port(port):
            if type(p).__name__ != "SFTPPanel":
                continue
            for info in getattr(p, "_transfer_workers", {}).values():
                worker = info.get("worker") if isinstance(info, dict) else None
                if worker is not None and worker.isRunning():
                    return True
        return False

    def close_sessions_on_port(self, port, reason: str = "") -> int:
        """优雅关闭指定本地端口上的全部会话面板（panel.shutdown() 释放资源），
        返回关闭数量。隧道断开前调用，避免端口失效后会话窗口假死。
        """
        panels = self.sessions_on_port(port)
        if not panels:
            return 0
        win = self._live_session_window()
        closed = 0
        kinds = []
        for p in panels:
            kinds.append(type(p).__name__.replace("Panel", ""))
            try:
                win.remove_session(p)
                closed += 1
            except (RuntimeError, OSError):
                pass
        if closed:
            self.log_message.emit(
                f"[远程会话] 隧道 {reason or port} 已断开，"
                f"同步关闭 {closed} 个相关会话（{' / '.join(kinds)}）")
        return closed

    def close_all_sessions(self, reason: str = "") -> int:
        """关闭全局会话窗口中的全部会话面板（「全部断开」用），返回关闭数量"""
        win = self._live_session_window()
        if win is None:
            return 0
        panels = list(getattr(win, "_panels", []))
        closed = 0
        for p in panels:
            try:
                win.remove_session(p)
                closed += 1
            except (RuntimeError, OSError):
                pass
        if closed:
            self.log_message.emit(
                f"[远程会话] {reason or '全部隧道'}已断开，同步关闭 {closed} 个相关会话")
        return closed

    # ---------- 全局会话窗口 ----------

    def ensure_session_window(self):
        """获取或创建全局远程会话标签容器窗口（单例复用）"""
        from windows.tunnel.remote_session_window import RemoteSessionWindow
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
