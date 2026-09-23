# -*- coding: utf-8 -*-
"""统一远程会话中心（单一 frpc 进程 + 单一 TOML）

整合此前两套相互隔离的 frpc/visitor 管理：
- 主窗口远程面板的手工 visitor（原 frpc_xtcp.toml + 自管 frpc 进程）
- 各面板按 snk 一键直连（原 FrpRemoteBridge / frpc_xtcp_panel.toml + 自管 frpc 进程）

RemoteSessionManager 为模块级单例（get_session_manager()）：
- 统一 visitor 注册表：手工 visitor 与 snk 快捷连接共享注册表，
  同一 serverName 已注册则复用现有隧道与本地端口，不重复建隧道
- 单一 frpc_xtcp_panel.toml + 单一 frpc 进程；visitor 变化优先走 frpc admin API
  （GET /api/reload）热重载：frpc 按 name+配置 diff，只启停变化的 visitor，
  既有隧道（含正在传输的 XTCP 会话）不打断；server/auth 配置变化或热重载
  失败时才回退「停旧起新」重启路径
- 持久化同样收敛到 frpc_xtcp_panel.toml：关联球桌/来源/最近使用等
  元数据以 # meta 注释内联在 visitor 块中；旧版 frpc_xtcp.toml /
  frpc_xtcp_meta.json 仅升级时回读，不再写入
- open_session 保持与 FrpRemoteBridge 相同语义，便于调用方平滑迁移
- FrpRemoteBridge 保留为薄包装（委托 manager），仅为导入兼容

frpc 生命周期由主窗口 closeEvent 调用 manager.shutdown() 统一关闭；
各面板关闭时不再各自 shutdown，避免误杀其他入口仍在使用的隧道。

SSH 凭据与 frpc 服务器配置复用 settings.json（ssh_user/ssh_pass/frpc_server），
密码经 core/secrets.py DPAPI 解密层读取，生成 TOML 时写入解密后的值。
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
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

# 统一 TOML（唯一持久化文件：server 配置 + 全部 visitor + 元数据注释），
# frpc 实际加载该文件，启动恢复也只回读该文件
_PANEL_TOML_NAME = "frpc_xtcp_panel.toml"
# disabled（已断开）visitor 侧车文件：断开保留注册的 visitor 不进 TOML
# （TOML 即 frpc 运行配置），完整记录落此处，启动时合并回注册表
_DISABLED_NAME = "frpc_xtcp_disabled.json"
# 旧版持久化文件（已停止写入，仅旧版本升级时回读迁移一次）
_MAIN_TOML_NAME = "frpc_xtcp.toml"
_META_NAME = "frpc_xtcp_meta.json"

# frpc 服务器默认配置（auth_token 由 settings.json 提供）
_FRPC_SERVER_DEFAULTS = {
    "serverAddr": "49.235.34.253",
    "serverPort": 7900,
    "auth_method": "token",
    "auth_token": "",
}

# 预热打洞 connect 超时：首次 XTCP 打洞普遍 1~3s，RTT 探测口径（800ms）
# 会把它误记为超时；预热只图打通，用长超时
_PREWARM_TIMEOUT_MS = 3000

# 隧道本地端口就绪轮询（2026-09-23 P2-4）：frpc 冷启动/热重载后 bindPort
# 何时开始监听取决于登录 frps + visitor 注册速度（实测 0.5~5s+），固定
# 2.5s 延时慢网赌输、快网白等。改为 200ms 间隔轮询，全部就绪立即回调，
# 超过上限（8s）也回调（调用方照常尝试，行为不劣于旧固定延时）。
_PORT_READY_INTERVAL_MS = 200
_PORT_READY_DEADLINE_MS = 8000

# frpc 意外退出自愈（2026-09-23 P1-1）：崩溃/被系统回收后注册表仍有启用
# 隧道时按退避自动重启（5s → 30s → 2min，最多 3 档）；到顶才放弃，回到
# 「用户点 SSH 再拉起」的一期行为。上次进程健康运行 ≥60s 即清零失败计数，
# 长跑一晚崩一次仍能从第一档秒级恢复。
_RECOVER_DELAYS_MS = (5000, 30000, 120000)
_RECOVER_RESET_UPTIME_SEC = 60

# 静默预连瞬态失败有界重试（2026-09-23 P1-2）：开机 6s 恰逢网络未就绪/
# frps 重启属瞬态，60s/120s 各重试一次；仍失败放弃到手动路径（点 SSH 会
# 再拉）。只重试 autostart 一条链路，总开关/手动 apply 失败不自动重试。
_AUTOSTART_RETRY_DELAYS_MS = (60000, 120000)

# frpc admin API（热重载）：仅监听回环地址，端口/口令每次进程生成随机值。
# 0.66.0 起 GET /api/reload 重读 TOML 并按 name+DeepEqual diff visitors，
# 未变化的 visitor 完全不动（XTCP 会话不中断）；reload 不重读 common 配置
# （serverAddr/auth.*），这些变化仍需重启 frpc。
_ADMIN_PORT_RANGE = (17500, 24999)   # 避开 generate_random_port 的常用端口集
_ADMIN_API_TIMEOUT_SEC = 3

# visitor 注册来源标识（展示用，可透传自定义文案）
SOURCE_MANUAL = "手工添加"
SOURCE_SNK = "snk 快捷"
SOURCE_TABLE = "球桌库"  # 远程面板「从球桌库选择」添加

# 面板 visitor 列表（手工 + 球桌库）：连接时整组重建、写入 frpc_xtcp.toml 供恢复
_PANEL_SOURCES = (SOURCE_MANUAL, SOURCE_TABLE)


def _load_settings() -> dict:
    """读取配置门面合并视图（敏感字段透明解密），失败时返回空字典"""
    try:
        from core import app_settings
        return app_settings.get_merged()
    except Exception:
        return {}


def _now_str() -> str:
    return datetime.now().strftime("%m-%d %H:%M")


def _parse_visitors_toml(toml_path: str) -> list:
    """解析 frpc TOML 中的 [[visitors]] 段，返回 visitor 记录列表

    每块末尾的 ``# meta = {...}`` 注释行携带注册表元数据（tableId/source/
    lastUsed，TOML 不便表达注册表扩展字段故以内联注释承载）；缺失时
    （旧版文件）按未处理的手工 visitor 恢复。
    """
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
        if not (m_server and m_port):
            continue
        v = {
            "serverName": m_server.group(1),
            "secretKey": m_key.group(1) if m_key else "abc123",
            "bindPort": int(m_port.group(1)),
            "tableId": "",
            "source": SOURCE_MANUAL,
            "lastUsed": "",
        }
        m_meta = re.search(r"^#\s*meta\s*=\s*(\{.*\})\s*$", block, re.M)
        if m_meta:
            try:
                meta = json.loads(m_meta.group(1))
                v["tableId"] = str(meta.get("tableId") or "")
                v["source"] = str(meta.get("source") or SOURCE_MANUAL)
                v["lastUsed"] = str(meta.get("lastUsed") or "")
            except ValueError:
                pass  # meta 注释损坏：按缺省恢复，基础字段不受影响
        visitors.append(v)
    return visitors


class RemoteSessionManager(QObject):
    """统一远程会话中心：单一 visitor 注册表 + 单一 frpc 进程/TOML（进程级单例）"""

    visitors_changed = Signal()       # visitor 注册表变化（增删改）
    frpc_state_changed = Signal(bool)  # frpc 运行状态变化（True=运行中）
    log_message = Signal(str)          # 运行日志（主窗口接入日志区）
    visitor_removed = Signal(str)      # visitor 被「删除 snk」彻底移除（主窗口列表同步清理用）
    tunnel_issue_changed = Signal(str, str)  # (snk, "lost"/"ok") 隧道失联/恢复
    prewarmed = Signal(list)           # 预热成功的 snk 列表（预热线程 → 主线程标记）

    def __init__(self):
        super().__init__()
        self._frpc_process: QProcess | None = None
        # serverName -> {"serverName", "bindPort", "secretKey",
        #                "tableId", "source", "lastUsed"}
        self._visitors: dict = {}
        self._session_window = None
        # frpc admin API（热重载通道）：随机端口 + 随机 BasicAuth 口令，
        # 仅监听 127.0.0.1，随进程存活；口令不落任何持久化文件
        self._admin_port = self._pick_admin_port()
        self._admin_user = "autowork"
        self._admin_password = secrets.token_urlsafe(24)
        # 上次成功应用（启动或热重载）时的 server/auth 配置快照：
        # reload 不重读 common 配置，快照变化必须走重启路径
        self._applied_signature: str | None = None
        # 自愈状态（P1-1）：意外退出退避计数 + 本次进程启动时刻（健康运行
        # 够久即清零计数，防长跑一晚崩一次也走到「放弃」档）
        self._recover_fails = 0
        self._frpc_started_at = 0.0
        # 隧道失联告警中的 snk 集合（tunnel_issue_changed "ok" 时移除）
        self._tunnel_issues: set = set()
        # 静默预连有界重试（P1-2）：autostart 瞬态失败的已重试次数
        self._autostart_retries = 0
        # P2-6/P2-5 信号回接：预热标记 / 失联告警联动会话面板
        self.prewarmed.connect(self._mark_prewarmed)
        self.tunnel_issue_changed.connect(self.notify_tunnel_issue)
        self._load_registry()

    # ---------- visitor 注册表 ----------

    def _load_registry(self):
        """启动恢复 visitor 注册表（不自动启动 frpc）

        唯一持久化文件为 frpc_xtcp_panel.toml（visitor 块内 # meta 注释
        携带关联球桌/来源/最近使用）；文件存在即视为权威——含空注册表
        （上次全部断开后仅剩 server 配置，不回读旧文件复活残留隧道）。
        文件缺失（旧版本升级）时按 frpc_xtcp_meta.json 快照 →
        frpc_xtcp.toml 顺序回读迁移一次。
        """
        app_dir = get_app_dir()
        panel_path = os.path.join(app_dir, _PANEL_TOML_NAME)
        if os.path.exists(panel_path):
            for v in _parse_visitors_toml(panel_path):
                self._visitors[v["serverName"]] = v
            # 合并「已断开（保留注册）」侧车：disabled visitor 不在 TOML 里
            # （TOML 即 frpc 运行配置），断开口径重启后不复活隧道但保注册
            try:
                with open(os.path.join(app_dir, _DISABLED_NAME), "r",
                          encoding="utf-8") as f:
                    data = json.load(f)
                for v in (data or []):
                    if isinstance(v, dict) and v.get("serverName"):
                        self._visitors[v["serverName"]] = v
            except (OSError, ValueError):
                pass  # 侧车损坏：丢断开态注册可接受，绝不阻塞启动恢复
            return
        # ---- 旧版本升级迁移（以下文件已停止写入） ----
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
        # 回退：元数据缺失时按旧手工 TOML 恢复（无 meta 注释 → 手工来源）
        for v in _parse_visitors_toml(os.path.join(app_dir, _MAIN_TOML_NAME)):
            self._visitors[v["serverName"]] = v

    def _persist_registry(self):
        """落盘持久化文件（不触碰 frpc 进程）

        拆两份：启用中的 visitor 写 frpc_xtcp_panel.toml（frpc 实际加载的
        运行配置）；「已断开（保留注册）」的 disabled visitor 不写 TOML
        （写进去会被 frpc 直接拉起），落到 _DISABLED_NAME 侧车 JSON，
        启动时由 _load_registry 合并回注册表。
        """
        app_dir = get_app_dir()
        self._write_toml(os.path.join(app_dir, _PANEL_TOML_NAME))
        disabled = [v for v in self._visitors.values() if v.get("disabled")]
        try:
            if disabled:
                with open(os.path.join(app_dir, _DISABLED_NAME), "w",
                          encoding="utf-8") as f:
                    json.dump(disabled, f, ensure_ascii=False, indent=1)
            elif os.path.exists(os.path.join(app_dir, _DISABLED_NAME)):
                os.remove(os.path.join(app_dir, _DISABLED_NAME))
        except OSError:
            pass  # 侧车写失败不影响主配置：重连后该隧道会自然回到 TOML

    def persist(self):
        """落盘持久化文件（不触碰 frpc 进程），供面板添加/删除 visitor 后
        调用：注册表与 frpc_xtcp_panel.toml 即时对齐，重启后不丢不复活"""
        self._persist_registry()

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
            if bind_port:
                try:
                    port = int(bind_port)
                except (TypeError, ValueError):
                    # 非法端口不崩溃：回退随机端口（与未指定等价）
                    port = generate_random_port(exclude_ports=self.used_ports())
            else:
                port = generate_random_port(exclude_ports=self.used_ports())
            self._visitors[server_name] = {
                "serverName": server_name,
                "bindPort": port,
                "secretKey": str(secret_key or self._default_secret_key()),
                "tableId": str(table_id or ""),
                "source": str(source or SOURCE_MANUAL),
                "lastUsed": "",
                "disabled": False,
            }
            changed = True
        else:
            # 已存在：仅更新显式传入且有变化的字段，
            # 未指定端口时保持原端口不变（避免每次调用都换端口）
            if bind_port:
                try:
                    port = int(bind_port)
                except (TypeError, ValueError):
                    # 非法端口不崩溃：保持原端口不变（port == info["bindPort"]
                    # 恒真，跳过下方更新分支）
                    port = info["bindPort"]
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
            # 重新注册已断开的隧道视为「重新启用」：调用方（ensure_visitor
            # 重连、主面板差量同步）语义都是要让这条隧道活起来
            if info.get("disabled"):
                info["disabled"] = False
                changed = True
        # changed 标志：仅注册表有实质变化时才发信号，
        # 让 UI（隧道面板）避免无谓刷新
        if changed:
            self.visitors_changed.emit()
        return self._visitors[server_name]["bindPort"], changed

    def ensure_visitor(self, snk: str, table_id: str = "",
                       source: str = SOURCE_SNK) -> tuple:
        """确保 snk 对应 visitor 已注册且 frpc 正在运行，返回 (bindPort, 是否冷启动)

        复用/新建后统一经 mark_used 刷新最近使用时间并发 visitors_changed，
        保证隧道面板能立刻看到「最近使用/关联球桌」数据。

        「冷启动」（second 返回值 True）指 frpc 进程新建/重启，需等登录与
        端口就绪；热重载成功时 /api/reload 返回即代表新 visitor 的 bindPort
        监听器已绑定（XTCP 打洞在首个本地连接时才触发），按复用短延时即可。
        """
        snk = str(snk or "").strip()
        if not snk:
            raise ValueError("snk 不能为空")
        info = self._visitors.get(snk)
        # 已注册且 frpc 在运行：直接复用现有隧道（不重启），只刷新使用时间。
        # 自愈（2026-09-22 真机）：注册表有但本地端口没监听 = 该 visitor 从未
        # 进过进程（旧版「添加并注册」不 apply 的遗留），复用必被拒——补一次
        # apply（运行中即热重载）把端口真正拉起来再返回。
        # disabled（已断开保留注册）不走复用：落下方注册+apply 路径，
        # register_visitor 会把 disabled 清掉，等效「重新启用并连接」。
        if info is not None and not info.get("disabled") and self.is_running():
            from p2p import is_port_in_use
            if not is_port_in_use(info["bindPort"]):
                self.log_message.emit(
                    f"[远程会话] {snk} 端口 {info['bindPort']} 未监听，"
                    "热重载补齐")
                self.apply()
            self.mark_used(snk, table_id)
            return self._visitors[snk]["bindPort"], False
        # 否则注册并应用新配置：frpc 运行中走热重载（其余隧道不中断），
        # 未运行则冷启动 frpc（新隧道或 frpc 已退出）
        self.register_visitor(snk, source=source, table_id=table_id)
        result = self.apply()
        self.mark_used(snk, table_id)
        return self._visitors[snk]["bindPort"], result in ("started", "restarted")

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

        与「删除」的本质区别（2026-09-22 修复：此前两按钮行为等价）：
        断开只把 visitor 置为 disabled 态——先优雅关闭该隧道端口上的
        SSH/SFTP/RDP 会话（避免隧道丢失后窗口假死），再 apply 让 frpc 热
        重载摘除该隧道、释放本地端口；注册与持久化配置**保留**，下次
        启动仍恢复，重新连接（一键直连/SSH 入口）自动回到启用态。
        全部隧道断开（仅剩 disabled）时 apply 会停掉 frpc，注册表照旧保留。

        Returns:
            "ok" 断开成功；"not_running" frpc 未启动（未做任何改动）；
            "not_found" visitor 不存在；"error" 应用变更失败。
        """
        name = str(server_name or "").strip()
        info = self._visitors.get(name)
        if info is None:
            return "not_found"
        if info.get("disabled"):
            # 已是断开态：幂等，不重复关会话/apply（即便 frpc 因全部断开
            # 已停止，"已断开"仍是本次操作的正确答案）
            return "ok"
        if not self.is_running():
            # frpc 未启动时隧道本未建立，无断开可做；
            # 千万不能在此 apply()，否则会带着剩余 visitor 自动拉起 frpc
            return "not_running"
        self.close_sessions_on_port(info["bindPort"], reason=name)
        info["disabled"] = True
        self.visitors_changed.emit()
        try:
            self.apply()
        except (OSError, RuntimeError) as e:
            # 回滚断开标记，避免「UI 显示已断开但 frpc 仍在跑该隧道」的裂脑；
            # apply 失败前已按 disabled 落过盘，回滚后立即重写对齐
            info["disabled"] = False
            self._persist_registry()
            self.visitors_changed.emit()
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
        # frpc 未启动：仅移除并重写持久化文件，不启动 frpc
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

    @property
    def admin_port(self) -> int:
        """本机 frpc admin API 端口（webServer.port，启动时随机分配）"""
        return self._admin_port

    def ping_admin(self, path: str = "/healthz") -> tuple:
        """管理通道健康检查（UI 展示用）：返回 (状态码或0=不可达, 响应体)"""
        return self._request_admin_api(path)

    def stop_frpc(self):
        """停止 frpc 进程（注册表保留，TOML 不删）——二期 P1 优雅停止入口

        内部走 _stop_frpc 两段式：POST /api/stop 让 frpc 自行收尾，
        2.5s 未退出才兜底强杀；区别于旧「清空注册表→apply→还原」绕行。
        """
        self._stop_frpc()

    def active_count(self) -> int:
        """启用中（非 disabled）的隧道数——静默预连的必要性判定"""
        return sum(1 for v in self._visitors.values() if not v.get("disabled"))

    def frps_server_addr(self) -> str:
        """frps 服务器地址（frpc_server.serverAddr）——frps 代理视图 tcp
        页签「直连」的目标主机（remotePort 监听在 frps 机器上）"""
        frpc_server = _load_settings().get("frpc_server") or {}
        return str(frpc_server.get("serverAddr")
                   or _FRPC_SERVER_DEFAULTS["serverAddr"])

    def autostart(self) -> str:
        """开机静默预连：后台自动拉起 frpc 并恢复启用中的全部隧道，
        让「点 SSH/SFTP 秒连」（ensure_visitor 走复用路径，省 2.5s 冷启动）

        静默纪律：绝不弹窗、绝不抛错——无启用隧道/已在运行直接跳过，
        失败只写日志（frpc.exe 缺失、auth_token 未配、进程起不来等）。
        启动成功不代表隧道已就绪：XTCP 打洞在首个本地连接才触发，
        预热由质量探测轮（tcp connect 各 bindPort）顺带完成。

        Returns:
            "started"/"reloaded" 已应用；"skipped_running" frpc 已在跑；
            "skipped_no_tunnel" 无启用隧道；"failed" 应用异常（详见日志）。
        """
        if self.is_running():
            self._autostart_retries = 0
            return "skipped_running"
        if self.active_count() == 0:
            return "skipped_no_tunnel"
        try:
            result = self.apply()
        except (OSError, RuntimeError, ValueError) as e:
            # 现场无网/frps 不可达等瞬态失败静默：不打扰开机流程，
            # 有界重试（P1-2，60s/120s 各一次）后仍失败则放弃——
            # 用户手动点 SSH 时仍会走原冷启动路径重试
            self.log_message.emit(f"[远程会话] 静默预连失败（将自动重试）: {e}")
            self._schedule_autostart_retry()
            return "failed"
        self._autostart_retries = 0
        self.log_message.emit(
            f"[远程会话] 静默预连：frpc 已按注册表启动"
            f"（{self.active_count()} 条启用隧道）")
        return result

    def _schedule_autostart_retry(self):
        """autostart 瞬态失败的有界重试（P1-2）：60s/120s 各补一枪"""
        if self._autostart_retries >= len(_AUTOSTART_RETRY_DELAYS_MS):
            self.log_message.emit(
                "[远程会话] 静默预连重试用尽，放弃（手动连接会重试）")
            return
        delay = _AUTOSTART_RETRY_DELAYS_MS[self._autostart_retries]
        self._autostart_retries += 1
        self.log_message.emit(
            f"[远程会话] {delay // 1000}s 后自动重试静默预连"
            f"（第 {self._autostart_retries}/{len(_AUTOSTART_RETRY_DELAYS_MS)} 次）")
        QTimer.singleShot(delay, self._autostart_retry)

    def _autostart_retry(self):
        """延迟重试回调：仍走 autostart 全套前置判定（运行中/无隧道自动跳过）"""
        result = self.autostart()
        if result in ("started", "reloaded", "restarted"):
            self.prewarm_async()  # 重试成功也补预热打洞

    def prewarm_async(self):
        """预热打洞：后台线程对全部启用隧道的本地 bindPort 串行发起一次
        TCP connect。XTCP 语义下 connect = NAT 打洞 + 端到端握手全部完成，
        打洞路由成果由 frpc 保留复用——首条真实 SSH 不再承担打洞延时。

        为什么不走质量探测器（VisitorProber.run_once）：探测器超时是 RTT
        测量口径（默认 800ms），**首次打洞普遍 1~3s**，会被整轮误记为超时
        样本污染质量页统计（连 3 败即判"异常"）；预热用 3s 长超时只图打通，
        不发 sample 信号、不留统计。串行探（绝不并发）：并发打洞互抢 UDP
        通道反而拖慢。daemon 线程 + 全异常吞，绝不阻塞主线程。
        """
        import threading

        def _run():
            from core.visitor_probe import tcp_connect_rtt_ms
            recs = [r for r in self.records()
                    if not r.get("disabled") and r.get("bindPort")]
            if not recs:
                return
            ok = 0
            ok_snks = []
            for r in recs:
                try:
                    if tcp_connect_rtt_ms("127.0.0.1", int(r["bindPort"]),
                                          _PREWARM_TIMEOUT_MS) is not None:
                        ok += 1
                        ok_snks.append(r.get("serverName", ""))
                except Exception:
                    pass  # 单条失败不影响其余隧道预热
            self.log_message.emit(
                f"[远程会话] 预热打洞完成：{ok}/{len(recs)} 条隧道已可秒连")
            if ok_snks:
                self.prewarmed.emit(ok_snks)  # queued 回主线程标记
        if self.active_count() == 0:
            return
        threading.Thread(target=_run, daemon=True,
                         name="frp-prewarm").start()

    def _mark_prewarmed(self, snks: list):
        """P2-6：预热成功标记进注册表（仅内存，不持久化——洞随 frpc
        重启失效，落盘会在重启后展示假「已预热」）"""
        changed = False
        now = time.strftime("%H:%M")
        for snk in snks:
            v = self._visitors.get(snk)
            if v is not None and v.get("prewarmedAt") != now:
                v["prewarmedAt"] = now
                changed = True
        if changed:
            self.visitors_changed.emit()

    def report_tunnel_issue(self, snk: str, lost: bool):
        """P2-5：感知端（remote_hub）上报隧道失联/恢复，翻转时发信号"""
        if lost:
            if snk in self._tunnel_issues:
                return
            self._tunnel_issues.add(snk)
            self.tunnel_issue_changed.emit(snk, "lost")
        else:
            if snk not in self._tunnel_issues:
                return
            self._tunnel_issues.remove(snk)
            self.tunnel_issue_changed.emit(snk, "ok")

    def notify_tunnel_issue(self, snk: str, state: str):
        """P2-5：隧道失联/恢复时，对该端口上已打开的会话面板展示/
        撤除提示条（SSH/SFTP/RDP 面板顶部）"""
        port = None
        for v in self._visitors.values():
            if v.get("serverName") == snk:
                port = v.get("bindPort")
                break
        if port is None:
            return
        from windows.remote_session.tunnel_notice import clear, show
        for p in self.sessions_on_port(port):
            try:
                (show if state == "lost" else clear)(p, snk)
            except RuntimeError:
                pass  # 面板已销毁（C++ 对象不在），跳过

    def wait_ports_ready(self, ports, on_ready, on_deadline=None):
        """轮询本地隧道端口监听就绪（P2-4）：全部就绪立即回调，不空等

        frpc 冷启动/热重载后 bindPort 何时开始监听取决于登录 frps +
        visitor 注册速度（实测 0.5~5s+），固定 2.5s 延时慢网赌输、快网
        白等。200ms 间隔轮询，上限 8s——超时也回调（on_deadline 缺省
        落到 on_ready），调用方照常尝试，行为不劣于旧固定延时。
        """
        from p2p import is_port_in_use
        ports = [int(p) for p in ports if p]
        if not ports:
            on_ready()
            return
        deadline = time.monotonic() + _PORT_READY_DEADLINE_MS / 1000.0

        def _poll():
            try:
                ready = all(is_port_in_use(p) for p in ports)
            except Exception:
                ready = False
            if ready:
                on_ready()
                return
            if time.monotonic() >= deadline:
                (on_deadline or on_ready)()
                return
            QTimer.singleShot(_PORT_READY_INTERVAL_MS, _poll)

        _poll()

    def prewarm_when_ready(self):
        """frpc 启动后用：等全部启用隧道 bindPort 监听就绪再预热打洞
        （替代固定 3s 等待——快网早预热、慢网不赌输），超时照常预热"""
        ports = [r.get("bindPort") for r in self.records()
                 if not r.get("disabled") and r.get("bindPort")]
        self.wait_ports_ready(ports, self.prewarm_async)

    def _pick_admin_port(self) -> int:
        """随机选一个本机空闲端口作为 frpc admin API 端口（仅回环监听）"""
        from p2p import is_port_in_use
        candidates = secrets.token_bytes(16)
        span = _ADMIN_PORT_RANGE[1] - _ADMIN_PORT_RANGE[0] + 1
        for i in range(len(candidates)):
            port = _ADMIN_PORT_RANGE[0] + candidates[i] % span
            if not is_port_in_use(port):
                return port
        return secrets.choice(range(_ADMIN_PORT_RANGE[0], _ADMIN_PORT_RANGE[1] + 1))

    def _common_config_lines(self, warn: bool = True) -> list:
        """server/auth 公共配置行（_write_toml 与热重载签名共用，口径唯一）

        warn=False 用于纯签名计算，避免同一次 apply 重复弹 token 缺失警告。
        """
        settings = _load_settings()
        frpc_server = settings.get("frpc_server") or dict(_FRPC_SERVER_DEFAULTS)
        server_addr = frpc_server.get("serverAddr", _FRPC_SERVER_DEFAULTS["serverAddr"])
        server_port = frpc_server.get("serverPort", _FRPC_SERVER_DEFAULTS["serverPort"])
        auth_method = frpc_server.get("auth_method", _FRPC_SERVER_DEFAULTS["auth_method"])
        auth_token = frpc_server.get("auth_token", "")
        if not auth_token and warn:
            self.log_message.emit("[远程会话] 警告: frpc auth_token 未配置，"
                                  "请在 设置 → 认证 Token 中填写")
        return [
            f'serverAddr = "{server_addr}"\n',
            f'serverPort = {server_port}\n',
            f'auth.method = "{auth_method}"\n',
            f'auth.token = "{auth_token}"\n',
        ]

    def apply(self):
        """按当前注册表应用配置；frpc 已在运行且公共配置未变时走热重载，
        不打断既有隧道（XTCP 会话/已开 SSH 窗口不中断）

        路径选择：
        - 注册表为空 → 停止 frpc
        - frpc 运行中 + server/auth 签名未变 → 重写 TOML + GET /api/reload
          （frpc 内部按 name+配置 diff，只启停变化的 visitor）
        - 热重载不可用（admin 端口连不上/5xx）或签名变化（serverAddr、
          auth.token 等 reload 不重读的配置）→ 回退「停旧起新」重启
        - 热重载返回 4xx（新配置本身非法）→ 保持原进程运行并抛错，
          绝不重启（重启只会用同一份坏配置杀死现有隧道）

        Returns:
            "reloaded" 热重载成功（现有隧道零中断，新 visitor 端口已监听）
            / "restarted" 停旧起新 / "started" 首次启动 / "stopped" 注册表为空
        """
        # 先落盘持久化文件（frpc_xtcp_panel.toml + disabled 侧车，含 server
        # 配置），再处理 frpc 进程
        self._persist_registry()
        was_running = self.is_running()
        if not any(not v.get("disabled") for v in self._visitors.values()):
            # 没有启用中的隧道（注册表为空，或全部处于「已断开」态）：
            # 停掉 frpc 避免空转；disabled 注册保留，重连时再拉起
            self._stop_frpc()
            return "stopped"
        # 与 _persist_registry 写入 TOML 的公共段同源；warn=False 避免重复弹警告
        signature = "".join(self._common_config_lines(warn=False))
        if was_running and signature == self._applied_signature:
            status, body = self._reload_with_retry()
            if status == 200:
                self.log_message.emit("[远程会话] 已热重载 visitor 配置"
                                      "（现有隧道不中断）")
                return "reloaded"
            if 400 <= status < 500:
                # 配置非法：现有 frpc 与隧道保持原状，向调用方报错
                raise RuntimeError(f"frpc 热重载拒绝新配置: {body or status}")
            # 连不上/5xx：admin 通道不可用，回退重启路径
            self.log_message.emit("[远程会话] 热重载不可用"
                                  f"（HTTP {status or 'unreachable'}），重启 frpc 应用配置")
        self._restart_frpc(signature)
        return "restarted" if was_running else "started"

    def _reload_with_retry(self) -> tuple:
        """GET /api/reload，不可达时做有界重试

        冷启动刚完成的短窗口内 webServer 可能还没监听；连接被拒是瞬态，
        重试 2 次（间隔 0.5s）仍失败才判定为 admin 通道不可用。
        4xx 为确定性结果（配置非法），不重试。
        """
        status, body = self._request_admin_api("/api/reload")
        for _ in range(2):
            if status != 0:
                break
            time.sleep(0.5)
            status, body = self._request_admin_api("/api/reload")
        return status, body

    def _restart_frpc(self, signature: str):
        """停旧起新（原有路径）：进程级配置变化或热重载不可用时的兜底"""
        app_dir = get_app_dir()
        frpc_exe = os.path.join(app_dir, "frpc.exe")
        if not os.path.exists(frpc_exe):
            raise OSError(f"frpc.exe 不存在: {frpc_exe}")
        # 先停旧进程再启新进程：旧进程持有旧配置（端口/visitor 列表），
        # 直接复用会导致新配置不生效或端口冲突
        self._stop_frpc()
        # admin 端口若已被其他进程占用（或被上次启动的自己占着未释放），
        # 重新随机一个空闲端口——否则 frpc 会因 webServer 绑定失败直接退出
        from p2p import is_port_in_use
        if is_port_in_use(self._admin_port):
            self._admin_port = self._pick_admin_port()
            self._persist_registry()  # 重写 TOML 用新端口
        # _persist_registry 已按当前注册表写出最新 TOML，直接复用该文件
        toml_path = os.path.join(app_dir, _PANEL_TOML_NAME)
        proc = QProcess(self)
        proc.setWorkingDirectory(app_dir)
        # 信号在 start 之前接好：frpc 可能在极短时间内退出，晚接会错过 finished 事件
        proc.readyReadStandardOutput.connect(self._on_frpc_output)
        proc.readyReadStandardError.connect(self._on_frpc_error)
        proc.finished.connect(self._on_frpc_finished)
        proc.start(frpc_exe, ["-c", toml_path])
        self._frpc_process = proc
        self._applied_signature = signature
        self._frpc_started_at = time.monotonic()  # P1-1 健康运行计时起点
        self.frpc_state_changed.emit(True)

    def _request_admin_api(self, path: str,
                           method: str = "GET") -> tuple:
        """请求本机 frpc admin API，返回 (HTTP状态码或0=不可达, 响应体前200字)

        不可达（未启动/端口未监听/超时）返回 (0, "")，调用方据此决定回退路径。
        强制直连：机器上设了 HTTP(S)_PROXY 时 urllib 默认走代理，对本机
        回环管理接口必然失败（代理回 502），热重载/优雅停止会静默降级——
        ProxyHandler({}) 绕过一切代理。
        """
        url = f"http://127.0.0.1:{self._admin_port}{path}"
        req = urllib.request.Request(url, method=method)
        token = base64.b64encode(
            f"{self._admin_user}:{self._admin_password}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}))
        try:
            with opener.open(req,
                             timeout=_ADMIN_API_TIMEOUT_SEC) as resp:
                body = resp.read(200).decode("utf-8", errors="replace")
                return resp.status, body
        except urllib.error.HTTPError as e:
            try:
                body = e.read(200).decode("utf-8", errors="replace")
            except Exception:
                body = ""
            return e.code, body
        except (urllib.error.URLError, OSError, ValueError):
            return 0, ""

    def _stop_frpc(self):
        """停止 frpc：两段式优雅停止（二期 P1）

        先 POST /api/stop（frpc 0.65+ admin 原生端点）让 frpc 自行关闭
        控制连接与监听端口——修复旧 kill 强杀的三类残留：本地隧道端口
        TIME_WAIT 拖累下次启动、TOML 半写、窗口期竞态。2.5s 内未退出
        （QTimer 回检，不阻塞调用线程）才兜底 kill()。admin 通道不可达
        （status=0）时直接 kill，行为同日一期。
        """
        proc = self._frpc_process
        self._frpc_process = None
        self._applied_signature = None
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
            status, _body = self._request_admin_api("/api/stop", method="POST")
            if status == 0:
                # admin 通道不可达：无从优雅停止，直接强杀（同日一期行为）
                proc.kill()
            else:
                proc.quit()  # 尽力而为的退出信号（frpc 通常已由 /api/stop 收尾）
                # 兜底：2.5s 后仍在运行才强杀（异步回检，绝不在主线程 waitFor）
                def _force_kill(p=proc):
                    try:
                        if p.state() != QProcess.ProcessState.NotRunning:
                            p.kill()
                            self.log_message.emit(
                                "[远程会话] frpc 优雅退出超时，已强制结束")
                    except RuntimeError:
                        pass  # 进程对象已被 Qt 销毁（正常退出清理完成）
                QTimer.singleShot(2500, _force_kill)
            self.frpc_state_changed.emit(False)

    def _on_stop_cleanup_done(self, *_args):
        """frpc 进程停止后的清理回调（替代 waitForFinished 阻塞等待）"""
        proc = self.sender()
        if proc is not None:
            proc.deleteLater()

    def _on_frpc_finished(self, exit_code, _exit_status):
        """frpc 意外退出：清空进程引用；注册表仍有启用隧道时按退避自愈重启
        （P1-1：5s → 30s → 2min 三档，健康运行 ≥60s 清零计数）"""
        proc = self._frpc_process
        self._frpc_process = None
        self._applied_signature = None  # 进程已亡，热重载对比基线随之失效
        if proc is not None:
            proc.deleteLater()
            self.frpc_state_changed.emit(False)
            self.log_message.emit(f"[远程会话] frpc 已退出，退出码: {exit_code}")
        # ---- P1-1 自愈判定 ----
        if self.active_count() == 0:
            return  # 无启用隧道：本就是正常停机路径，无需恢复
        # 健康运行够久（非刚拉起就崩）→ 计数清零，下次仍从第一档秒级恢复
        if (self._frpc_started_at > 0
                and time.monotonic() - self._frpc_started_at
                >= _RECOVER_RESET_UPTIME_SEC):
            self._recover_fails = 0
        self._schedule_recover()

    def _schedule_recover(self):
        """按退避档位调度一次自愈重启（计数用尽则放弃并写日志）"""
        if self._recover_fails >= len(_RECOVER_DELAYS_MS):
            self.log_message.emit(
                "[远程会话] frpc 连续异常退出且自愈退避已用尽，放弃自动恢复"
                "（手动连接时会重新拉起）")
            return
        delay = _RECOVER_DELAYS_MS[self._recover_fails]
        self._recover_fails += 1
        self.log_message.emit(
            f"[远程会话] frpc 意外退出，{delay // 1000}s 后自动恢复"
            f"（第 {self._recover_fails}/{len(_RECOVER_DELAYS_MS)} 次重试）")
        QTimer.singleShot(delay, self._recover_frpc)

    def _recover_frpc(self):
        """P1-1 延迟自愈回调：拉起 frpc 恢复全部启用隧道"""
        if self.is_running():
            return  # 期间用户已手动拉起，无需恢复
        if self.active_count() == 0:
            return  # 期间隧道已全部断开/删除
        try:
            result = self.apply()
        except (OSError, RuntimeError, ValueError) as e:
            # 网络未恢复等瞬态原因：继续用剩余退避档重试（不经过退出回调，
            # 避免崩溃前进程的健康时长误清零计数）
            self.log_message.emit(f"[远程会话] 自动恢复失败: {e}")
            self._schedule_recover()
            return
        self._recover_fails = 0
        self._frpc_started_at = time.monotonic()
        self.log_message.emit(f"[远程会话] 自动恢复完成（{result}）")
        self.prewarm_async()  # 恢复后补预热打洞

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
        """生成统一 frpc xtcp 配置（所有 visitor）

        webServer 段开启本机 admin API（GET /api/reload 热重载通道）：
        仅监听 127.0.0.1 + 随机 BasicAuth，供 autowork 进程内使用。
        reload 只重读 proxies/visitors，webServer 自身变更不生效（需重启，
        与 serverAddr/auth 同口径，由 _applied_signature 统一管理）。
        """
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(self._common_config_lines())
            f.write('\n')
            f.write('webServer.addr = "127.0.0.1"\n')
            f.write(f'webServer.port = {self._admin_port}\n')
            f.write(f'webServer.user = "{self._admin_user}"\n')
            f.write(f'webServer.password = "{self._admin_password}"\n')
            f.write('\n')
            self._write_visitor_blocks(f)

    def _write_visitor_blocks(self, f):
        """写全部**启用** visitor 块；块尾 # meta 注释内联注册表元数据
        （回读见 _parse_visitors_toml）。disabled（已断开保留注册）跳过——
        TOML 即 frpc 运行配置，写进去会被拉起；其记录由 _persist_registry
        落 _DISABLED_NAME 侧车"""
        for sn, v in self._visitors.items():
            if v.get("disabled"):
                continue
            f.write("[[visitors]]\n")
            f.write(f'name = "{sn}"\n')
            f.write('type = "xtcp"\n')
            f.write(f'serverName = "{sn}"\n')
            f.write(f'secretKey = "{v["secretKey"]}"\n')
            f.write(f'bindPort = {v["bindPort"]}\n')
            meta = {"tableId": v.get("tableId", ""),
                    "source": v.get("source", ""),
                    "lastUsed": v.get("lastUsed", "")}
            f.write(f"# meta = {json.dumps(meta, ensure_ascii=False)}\n")
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

        # ---- 二期 P0 连接预检：frps 权威离线 → 秒提示，不盲等打洞超时 ----
        # 感知不可用（未配置/不可达/缓存过期）一律放行回退一期路径；
        # unregistered 也放行——visitor 注册先于设备上线是正常时序，
        # 只有确认 offline（设备注册过又掉线）才拦截，避免误伤。
        try:
            from core.frps_admin import get_frps_client
            state = get_frps_client().online(snk)
            if state is None and get_frps_client().configured():
                # 缓存不可信时当场刷一次（2.5s 超时上限），仍失败则放行
                get_frps_client().refresh()
                state = get_frps_client().online(snk)
        except Exception:
            state = None
        if state == "offline":
            self._notify("设备未在线",
                         f"{snk} 在 frps 上状态为 offline（设备断电/断网/现场 "
                         f"frpc 掉线），已跳过打洞等待。可稍后重试，或到"
                         f"球桌管理页核对现场状态。", error=True, notifier=notifier)
            self.log_message.emit(f"[远程会话] 预检拦截: {snk} frps offline，未发起打洞")
            return

        try:
            port, _fresh = self.ensure_visitor(snk, table_id=table_id, source=source)
        except (OSError, RuntimeError, ValueError) as e:
            self._notify("远程准备失败", str(e), error=True, notifier=notifier)
            return
        msg = f"{table_id or snk} → {snk}（本地端口 {port}）"
        # SFTP 会话按球桌号在 videos_dir 下自动建本地目录，下载直接落位
        if kind == "sftp" and table_id:
            videos_dir = str(_load_settings().get("videos_dir") or "").strip()
            if videos_dir and os.path.isdir(videos_dir):
                msg += f"，本地目录 videos{os.sep}{table_id}"
        self._notify("正在建立远程连接", msg, notifier=notifier)
        # P2-4：轮询隧道端口监听就绪（200ms 间隔、8s 上限）替代固定延时——
        # 复用路径端口已在听首轮即中，冷启动快网早开、慢网不再赌输
        self.wait_ports_ready(
            [port],
            lambda: self._do_open(kind, snk, table_id, port, notifier))

    def open_direct_session(self, kind: str, host: str, port: int,
                            name: str = "", notifier=None):
        """TCP 直连会话（不经 frpc）：对任意 host:port 打开 SSH/SFTP 面板

        供远程页「连接」TCP 模式与 frps 代理视图「直连」动作使用——与
        主面板 TCP 模式同语义：凭据取 settings ssh_user/ssh_pass（主面板
        连接时写回的同一组），会话进全局会话窗口（会话总览可见）。
        """
        if kind in ("ssh", "sftp") and not PARAMIKO_AVAILABLE:
            self._notify("无法远程", "paramiko 未安装，无法建立 SSH/SFTP 会话",
                         error=True, notifier=notifier)
            return
        host = str(host or "").strip()
        if not host:
            self._notify("无法连接", "主机地址不能为空", error=True,
                         notifier=notifier)
            return
        try:
            port = int(port)
        except (TypeError, ValueError):
            self._notify("无法连接", f"端口非法: {port}", error=True,
                         notifier=notifier)
            return
        title = str(name or "").strip() or f"{host}:{port}"
        self._notify("正在建立连接", f"{title} → {host}:{port}",
                     notifier=notifier)
        self.log_message.emit(f"[远程会话] TCP 直连: {title} ({host}:{port}, {kind})")
        self._do_open(kind, title, "", port, notifier=notifier, host=host)

    def _do_open(self, kind: str, snk: str, table_id: str, port: int,
                 notifier=None, host: str = "127.0.0.1"):
        """隧道就绪后实际打开会话面板（默认隧道在本地 127.0.0.1:port；
        host 可变——TCP 直连路径经 open_direct_session 传目标地址）"""
        # 会话面板依赖 paramiko 等重组件，延迟导入避免模块加载开销
        settings = _load_settings()
        username = settings.get("ssh_user", "")
        password = settings.get("ssh_pass", "")
        # TCP 直连路径传入目标主机（如 frps remotePort 所在机器）；
        # 隧道路径未传 host 时保持默认本机
        host = str(host or "").strip() or "127.0.0.1"
        title_snk = f"{table_id}（{snk}）" if table_id else snk
        try:
            if kind == "ssh":
                from windows.remote_session.ssh_terminal import SSHTerminalPanel
                panel = SSHTerminalPanel(
                    host, port, username, password,
                    log_callback=lambda msg: None,
                    server_name=title_snk,
                )
            elif kind == "sftp":
                from windows.remote_session.sftp_window import SFTPPanel
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
                from windows.remote_session.rdp_window import RDPPanel
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
        from windows.remote_session.remote_session_window import RemoteSessionWindow
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
