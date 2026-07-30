# -*- coding: utf-8 -*-
"""MainWindow 远程连接 Mixin：P2P 面板、XTCP/TCP 连接、frpc 管理、SFTP/SSH/RDP 窗口启动"""
from __future__ import annotations

import os
import sys
import re
from typing import TYPE_CHECKING

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QFormLayout

if TYPE_CHECKING:
    from autowork_with_table import Ui_MainWindow

try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False

from workers.network_workers import TCPWorker
from windows.sftp_window import SFTPWindow
from windows.ssh_terminal import SSHTerminalWindow
from windows.rdp_window import RDPWindow
from p2p import generate_random_port


class RemoteMixin:
    """远程连接相关方法（Mixin，需与 FluentWindowBase 组合使用）"""

    # 类型声明：由其他 Mixin / 主类提供，仅供 IDE 静态分析
    if TYPE_CHECKING:
        ui: Ui_MainWindow
        _p2p_visitors: list
        _p2p_current_index: int
        _frpc_process: QProcess | None
        _tcp_worker: TCPWorker | None
        _sftp_window: SFTPWindow | None
        _ssh_terminal_window: SSHTerminalWindow | None
        _rdp_window: RDPWindow | None

        def _load_settings(self) -> dict: ...
        def _save_settings(self, data: dict) -> None: ...
        def _get_app_dir(self) -> str: ...
        def _append_log(self, msg: str) -> None: ...
        def _show_info_bar(self, msg: str, level: str, duration: int = 2000) -> None: ...
        def _on_p2p_search_changed(self, text: str) -> None: ...

    # frpc 服务器默认配置
    _FRPC_SERVER_DEFAULTS = {
        "serverAddr": "49.235.34.253",
        "serverPort": 7900,
        "auth_method": "token",
        "auth_token": "123",
    }

    def _init_p2p_panel(self):
        """初始化远程面板状态，从已有的 frpc_xtcp.toml 恢复 visitor 列表"""
        settings = self._load_settings()
        ssh_user = settings.get("ssh_user", "")
        ssh_pass = settings.get("ssh_pass", "")
        if ssh_user:
            self.ui.p2p_ssh_user.setText(ssh_user)
        if ssh_pass:
            self.ui.p2p_ssh_pass.setText(ssh_pass)
        self._load_visitors_from_toml()
        self._refresh_p2p_list()
        self.ui.p2p_form_port.setValue(self._get_new_random_port())
        self._update_p2p_visibility()
        self._update_p2p_buttons()

    def _load_visitors_from_toml(self):
        """从已有的 frpc_xtcp.toml 解析 [[visitors]] 段恢复 visitor 列表"""
        toml_path = os.path.join(self._get_app_dir(), "frpc_xtcp.toml")
        if not os.path.exists(toml_path):
            self._append_log("[远程] 未找到 frpc_xtcp.toml")
            return
        try:
            with open(toml_path, 'r', encoding='utf-8') as f:
                content = f.read()
            blocks = content.split('[[visitors]]')
            for block in blocks[1:]:
                visitor = {}
                m_server = re.search(r'serverName\s*=\s*"([^"]+)"', block)
                m_key = re.search(r'secretKey\s*=\s*"([^"]+)"', block)
                m_port = re.search(r'bindPort\s*=\s*(\d+)', block)
                if m_server and m_port:
                    visitor["serverName"] = m_server.group(1)
                    visitor["secretKey"] = m_key.group(1) if m_key else "abc123"
                    visitor["bindPort"] = int(m_port.group(1))
                    self._p2p_visitors.append(visitor)
            if self._p2p_visitors:
                self._append_log(f"[远程] 从 TOML 恢复了 {len(self._p2p_visitors)} 个 visitor")
        except Exception as e:
            self._append_log(f"[远程] 解析 TOML 失败: {e}")

    def _get_new_random_port(self):
        """生成不冲突的随机端口（排除已添加 visitor 的端口）"""
        used_ports = {v["bindPort"] for v in self._p2p_visitors}
        return generate_random_port(exclude_ports=used_ports)

    def _on_p2p_toggled(self, checked):
        """切换远程面板显示/隐藏"""
        self.ui.p2p_panel.setVisible(checked)

    def _on_p2p_add(self):
        """添加按钮：XTCP 模式添加 visitor，TCP 模式保存当前服务器(ip:port)"""
        if self.ui.p2p_mode_combo.currentText() == "TCP":
            self._on_tcp_server_add()
            return
        server_name = self.ui.p2p_form_server.text().strip()
        if not server_name:
            self._append_log("[远程] 请填写 serverName")
            return
        port = self.ui.p2p_form_port.value()
        for i, v in enumerate(self._p2p_visitors):
            if v["bindPort"] == port and i != self._p2p_current_index:
                self._append_log(f"[远程] 端口 {port} 已被 {v['serverName']} 使用，请更换端口")
                return
        visitor = {
            "serverName": server_name,
            "bindPort": port,
            "secretKey": self.ui.p2p_form_key.text().strip() or "abc123"
        }
        self._p2p_visitors.append(visitor)
        self.ui.p2p_visitor_list.blockSignals(True)
        self._refresh_p2p_list()
        self.ui.p2p_visitor_list.blockSignals(False)
        self._p2p_current_index = len(self._p2p_visitors) - 1
        self.ui.p2p_visitor_list.setCurrentRow(self._p2p_current_index)
        self.ui.p2p_form_port.setValue(self._get_new_random_port())

    def _on_p2p_delete(self):
        """删除按钮：XTCP 模式删除 visitor，TCP 模式删除选中的服务器"""
        if self.ui.p2p_mode_combo.currentText() == "TCP":
            self._on_tcp_server_delete()
            return
        row = self.ui.p2p_visitor_list.currentRow()
        if 0 <= row < len(self._p2p_visitors):
            self._p2p_visitors.pop(row)
            self._p2p_current_index = -1
            self._refresh_p2p_list()

    def _on_p2p_visitor_selected(self, row):
        """列表选择：XTCP 模式加载 visitor 到表单，TCP 模式填充 host/port"""
        if self.ui.p2p_mode_combo.currentText() == "TCP":
            self._on_tcp_server_selected(row)
            return
        self._save_current_form()
        if 0 <= row < len(self._p2p_visitors):
            self._p2p_current_index = row
            v = self._p2p_visitors[row]
            self.ui.p2p_form_server.setText(v.get("serverName", ""))
            self.ui.p2p_form_port.setValue(v.get("bindPort", 10000))
            self.ui.p2p_form_key.setText(v.get("secretKey", "abc123"))
        else:
            self._p2p_current_index = -1

    def _save_current_form(self):
        """将当前表单内容保存回 visitor 数据"""
        if 0 <= self._p2p_current_index < len(self._p2p_visitors):
            v = self._p2p_visitors[self._p2p_current_index]
            v["serverName"] = self.ui.p2p_form_server.text()
            v["bindPort"] = self.ui.p2p_form_port.value()
            v["secretKey"] = self.ui.p2p_form_key.text()
            item = self.ui.p2p_visitor_list.item(self._p2p_current_index)
            if item:
                item.setText(v["serverName"])

    def _refresh_p2p_list(self):
        """刷新 visitor 列表显示"""
        self.ui.p2p_visitor_list.clear()
        for v in self._p2p_visitors:
            self.ui.p2p_visitor_list.addItem(v.get("serverName", ""))
        # 重新应用搜索过滤
        self._on_p2p_search_changed(self.ui.p2p_search.text())

    def _save_p2p_settings(self):
        """visitor 配置仅存于内存，连接时写入 TOML，不持久化到 settings.json"""
        pass

    # ------------------------------------------------------------------ TCP 保存的服务器
    def _load_tcp_servers(self):
        """从 settings.json 读取保存的服务器列表（ip:port 字符串）"""
        settings = self._load_settings()
        servers = settings.get("tcp_servers", [])
        if not isinstance(servers, list):
            return []
        return [s for s in servers if isinstance(s, str) and s.strip()]

    def _save_tcp_servers(self, servers):
        """持久化保存的服务器列表到 settings.json"""
        self._save_settings({"tcp_servers": servers})

    def _refresh_tcp_server_list(self):
        """刷新保存的服务器列表显示（TCP 模式复用 p2p_visitor_list）"""
        self.ui.p2p_visitor_list.clear()
        for s in self._load_tcp_servers():
            self.ui.p2p_visitor_list.addItem(s)
        # 重新应用搜索过滤
        self._on_p2p_search_changed(self.ui.p2p_search.text())

    def _reload_p2p_list_for_mode(self):
        """按当前模式重载列表内容（XTCP=visitors，TCP=保存的服务器）"""
        mode = self.ui.p2p_mode_combo.currentText()
        self.ui.p2p_visitor_list.blockSignals(True)
        if mode == "TCP":
            self._p2p_current_index = -1
            self._refresh_tcp_server_list()
        else:
            saved = self._p2p_current_index
            self._refresh_p2p_list()
            if 0 <= saved < self.ui.p2p_visitor_list.count():
                self.ui.p2p_visitor_list.setCurrentRow(saved)
        self.ui.p2p_visitor_list.blockSignals(False)

    def _on_tcp_server_add(self):
        """TCP 模式：把当前 host:port 保存到服务器列表"""
        host = self.ui.p2p_ssh_host.text().strip()
        if not host:
            self._append_log("[远程] 请先填写主机地址 host")
            return
        entry = f"{host}:{self.ui.p2p_ssh_port.value()}"
        servers = self._load_tcp_servers()
        if entry in servers:
            self._append_log(f"[远程] 服务器 {entry} 已在列表中")
            return
        servers.append(entry)
        self._save_tcp_servers(servers)
        self._refresh_tcp_server_list()
        self.ui.p2p_visitor_list.setCurrentRow(len(servers) - 1)
        self._append_log(f"[远程] 已保存服务器: {entry}")

    def _on_tcp_server_delete(self):
        """TCP 模式：删除选中的服务器"""
        row = self.ui.p2p_visitor_list.currentRow()
        servers = self._load_tcp_servers()
        if 0 <= row < len(servers):
            removed = servers.pop(row)
            self._save_tcp_servers(servers)
            self._refresh_tcp_server_list()
            self._append_log(f"[远程] 已删除服务器: {removed}")

    def _on_tcp_server_selected(self, row):
        """TCP 模式：选中服务器时填充 host/port"""
        servers = self._load_tcp_servers()
        if not (0 <= row < len(servers)):
            return
        entry = servers[row]
        host, _, port_str = entry.rpartition(':')
        if not host:
            host, port = entry, 22
        else:
            try:
                port = int(port_str)
            except ValueError:
                host, port = entry, 22
        self.ui.p2p_ssh_host.setText(host)
        self.ui.p2p_ssh_port.setValue(port)

    def _on_p2p_connect(self):
        """连接按钮 - 根据当前模式分发连接"""
        mode = self.ui.p2p_mode_combo.currentText()
        self._append_log(f"[远程] 连接按钮点击，模式: {mode}")
        if mode == "XTCP":
            self._on_xtcp_connect()
        elif mode == "TCP":
            self._on_tcp_connect()

    def _on_p2p_disconnect(self):
        """断开按钮 - 根据当前模式分发断开"""
        mode = self.ui.p2p_mode_combo.currentText()
        if mode == "XTCP":
            self._on_xtcp_disconnect()
        elif mode == "TCP":
            self._on_tcp_disconnect()

    def _on_p2p_mode_changed(self, _index):
        """连接方式切换时更新 UI 显隐"""
        self._save_current_form()
        self._update_p2p_visibility()

    def _update_p2p_visibility(self):
        """根据当前模式显示/隐藏对应表单"""
        mode = self.ui.p2p_mode_combo.currentText()
        is_xtcp = (mode == "XTCP")
        is_tcp = not is_xtcp
        self.ui.p2p_server_section_label.setText(
            "◎ 服务器 / visitors" if is_xtcp else "◎ 保存的服务器")
        for w in self.ui.p2p_xtcp_widgets:
            w.setVisible(is_xtcp)
        for i in range(self.ui.p2p_xtcp_form.rowCount()):
            lbl = self.ui.p2p_xtcp_form.itemAt(i * 2, QFormLayout.ItemRole.LabelRole)
            if lbl and lbl.widget():
                lbl.widget().setVisible(is_xtcp)
        self.ui.p2p_conn_widget.setVisible(is_xtcp)
        for w in self.ui.p2p_ssh_widgets:
            w.setVisible(is_tcp)
        for row_idx in range(2):
            lbl = self.ui.p2p_ssh_form.itemAt(row_idx, QFormLayout.ItemRole.LabelRole)
            if lbl and lbl.widget():
                lbl.widget().setVisible(is_tcp)
        self._reload_p2p_list_for_mode()
        self._update_p2p_buttons()

    def _on_xtcp_connect(self):
        """生成 TOML 并启动 frpc"""
        self._save_current_form()
        if not self._p2p_visitors:
            self._append_log("[远程] 请先添加 visitor 配置")
            return
        if self._frpc_process is not None:
            self._append_log("[远程] frpc 已在运行中")
            return
        app_dir = self._get_app_dir()
        toml_path = os.path.join(app_dir, "frpc_xtcp.toml")
        try:
            self._write_frpc_config(toml_path)
            self._append_log(f"[远程] 已生成 {toml_path}")
        except Exception as e:
            self._append_log(f"[远程] 生成配置失败: {e}")
            return
        frpc_exe = os.path.join(app_dir, "frpc.exe")
        if not os.path.exists(frpc_exe):
            self._append_log(f"[远程] frpc.exe 不存在: {frpc_exe}")
            return
        self._frpc_process = QProcess()
        self._frpc_process.setWorkingDirectory(app_dir)
        self._frpc_process.readyReadStandardOutput.connect(self._on_frpc_output)
        self._frpc_process.readyReadStandardError.connect(self._on_frpc_error)
        self._frpc_process.finished.connect(self._on_frpc_finished)
        self._frpc_process.start(frpc_exe, ["-c", toml_path])
        self._append_log(f"[远程] 已启动 frpc: {frpc_exe} -c {toml_path}")
        self._update_p2p_buttons()

    def _on_xtcp_disconnect(self):
        """停止 frpc 进程"""
        if self._frpc_process is None:
            self._append_log("[远程] frpc 未在运行")
            return
        self._append_log("[远程] 正在停止 frpc...")
        proc = self._frpc_process
        self._frpc_process = None
        proc.kill()
        proc.waitForFinished(3000)
        proc.deleteLater()
        self.ui.p2p_sftp_btn.setEnabled(False)
        self.ui.p2p_ssh_terminal_btn.setEnabled(False)
        self.ui.p2p_rdp_btn.setEnabled(False)
        self._close_p2p_windows()
        self._update_p2p_buttons()
        self._append_log("[远程] frpc 已停止")

    def _on_tcp_connect(self):
        """启动 TCP 连接"""
        if not PARAMIKO_AVAILABLE:
            self._append_log("[TCP] paramiko 未安装，请执行: pip install paramiko")
            return
        if self._tcp_worker is not None and self._tcp_worker.isRunning():
            self._append_log("[TCP] 已有连接正在运行")
            return
        if self._tcp_worker is not None:
            self._tcp_worker.deleteLater()
            self._tcp_worker = None
        host = self.ui.p2p_ssh_host.text().strip()
        if not host:
            self._append_log("[TCP] 请输入主机地址")
            return
        port = self.ui.p2p_ssh_port.value()
        self._save_ssh_credentials()
        self._tcp_worker = TCPWorker(
            host, port,
            self.ui.p2p_ssh_user.text(), self.ui.p2p_ssh_pass.text()
        )
        self._tcp_worker.result_ready.connect(self._on_tcp_finished)
        self._tcp_worker.error.connect(self._on_tcp_error)
        self._tcp_worker.start()
        self._append_log(f"[TCP] 正在连接 {host}:{port}...")
        self._update_p2p_buttons()

    def _on_tcp_disconnect(self):
        """断开 TCP 连接"""
        if self._tcp_worker is None:
            self._append_log("[TCP] 未连接")
            return
        worker = self._tcp_worker
        self._tcp_worker = None
        if worker.isRunning():
            worker.finished.connect(worker.deleteLater)
        else:
            worker.deleteLater()
        self.ui.p2p_sftp_btn.setEnabled(False)
        self.ui.p2p_ssh_terminal_btn.setEnabled(False)
        self.ui.p2p_rdp_btn.setEnabled(False)
        self._close_p2p_windows()
        self._update_p2p_buttons()
        self._append_log("[TCP] 已断开")

    def _on_tcp_finished(self, result):
        """TCP 连接成功回调"""
        self._append_log(f"[TCP] 连接成功: {result}")
        self._show_info_bar(f"TCP 连接成功: {result}", "success")
        self.ui.p2p_sftp_btn.setEnabled(True)
        self.ui.p2p_ssh_terminal_btn.setEnabled(True)
        self.ui.p2p_rdp_btn.setEnabled(True)
        if self._tcp_worker:
            w = self._tcp_worker
            self._tcp_worker = None
            if w.isRunning():
                w.finished.connect(w.deleteLater)
            else:
                w.deleteLater()

    def _on_tcp_error(self, error):
        """TCP 连接失败回调"""
        self._append_log(f"[TCP] 连接失败: {error}")
        self._show_info_bar(f"网络连接失败: {error}", "error", duration=4000)
        if self._tcp_worker:
            w = self._tcp_worker
            self._tcp_worker = None
            if w.isRunning():
                w.finished.connect(w.deleteLater)
            else:
                w.deleteLater()
        self.ui.p2p_sftp_btn.setEnabled(False)
        self.ui.p2p_ssh_terminal_btn.setEnabled(False)
        self.ui.p2p_rdp_btn.setEnabled(False)
        self._update_p2p_buttons()

    def _write_frpc_config(self, path):
        """生成 frpc_xtcp.toml 文件"""
        settings = self._load_settings()
        frpc_server = settings.get("frpc_server")
        if not frpc_server:
            frpc_server = dict(self._FRPC_SERVER_DEFAULTS)
            self._save_settings({"frpc_server": frpc_server})
            self._append_log("[远程] settings.json 中未找到 frpc_server，已自动生成默认配置")
        server_addr = frpc_server.get("serverAddr", self._FRPC_SERVER_DEFAULTS["serverAddr"])
        server_port = frpc_server.get("serverPort", self._FRPC_SERVER_DEFAULTS["serverPort"])
        auth_method = frpc_server.get("auth_method", self._FRPC_SERVER_DEFAULTS["auth_method"])
        auth_token = frpc_server.get("auth_token", self._FRPC_SERVER_DEFAULTS["auth_token"])
        with open(path, 'w', encoding='utf-8') as f:
            f.write(f'serverAddr = "{server_addr}"\n')
            f.write(f'serverPort = {server_port}\n')
            f.write(f'auth.method = "{auth_method}"\n')
            f.write(f'auth.token = "{auth_token}"\n')
            f.write('\n')
            for v in self._p2p_visitors:
                sn = v["serverName"]
                f.write("[[visitors]]\n")
                f.write(f'name = "{sn}"\n')
                f.write(f'type = "xtcp"\n')
                f.write(f'serverName = "{sn}"\n')
                f.write(f'secretKey = "{v["secretKey"]}"\n')
                f.write(f'bindPort = {v["bindPort"]}\n')
                f.write("\n")

    def _on_frpc_output(self):
        if self._frpc_process:
            output = self._frpc_process.readAllStandardOutput().data().decode('utf-8', errors='ignore')
            if output.strip():
                self._append_log(f"[frpc] {output.strip()}")

    def _on_frpc_error(self):
        if self._frpc_process:
            error = self._frpc_process.readAllStandardError().data().decode('utf-8', errors='ignore')
            if error.strip():
                self._append_log(f"[frpc] {error.strip()}")

    def _on_frpc_finished(self, exit_code, _exit_status):
        self._append_log(f"[远程] frpc 已退出，退出码: {exit_code}")
        self._frpc_process = None
        self.ui.p2p_sftp_btn.setEnabled(False)
        self.ui.p2p_ssh_terminal_btn.setEnabled(False)
        self.ui.p2p_rdp_btn.setEnabled(False)
        self._close_p2p_windows()
        self._update_p2p_buttons()

    def _update_p2p_buttons(self):
        """更新连接/断开按钮状态，以及 SFTP/SSH 终端/远程桌面按钮状态"""
        mode = self.ui.p2p_mode_combo.currentText()
        if mode == "XTCP":
            running = self._frpc_process is not None
            self.ui.p2p_sftp_btn.setEnabled(running)
            self.ui.p2p_ssh_terminal_btn.setEnabled(running)
            self.ui.p2p_rdp_btn.setEnabled(running)
            self.ui.p2p_connect_btn.setEnabled(not running)
            self.ui.p2p_disconnect_btn.setEnabled(running)
        elif mode == "TCP":
            self.ui.p2p_sftp_btn.setEnabled(True)
            self.ui.p2p_ssh_terminal_btn.setEnabled(True)
            self.ui.p2p_rdp_btn.setEnabled(True)
        else:
            self.ui.p2p_sftp_btn.setEnabled(False)
            self.ui.p2p_ssh_terminal_btn.setEnabled(False)
            self.ui.p2p_rdp_btn.setEnabled(False)

    def _close_p2p_windows(self):
        """关闭已打开的 SFTP 和 SSH 终端窗口"""
        if self._sftp_window is not None:
            try:
                self._sftp_window.close()
            except (RuntimeError, OSError):
                pass
            self._sftp_window = None
        if self._ssh_terminal_window is not None:
            try:
                self._ssh_terminal_window.close()
            except (RuntimeError, OSError):
                pass
            self._ssh_terminal_window = None
        if self._rdp_window is not None:
            try:
                self._rdp_window.close()
            except (RuntimeError, OSError):
                pass
            self._rdp_window = None

    def _save_ssh_credentials(self):
        """将当前 SSH 账号/密码保存到 settings.json"""
        username = self.ui.p2p_ssh_user.text().strip()
        password = self.ui.p2p_ssh_pass.text()
        data = {}
        if username:
            data["ssh_user"] = username
        if password:
            data["ssh_pass"] = password
        if data:
            self._save_settings(data)

    def _on_sftp_btn_clicked(self):
        """打开 SFTP 文件管理窗口"""
        if not PARAMIKO_AVAILABLE:
            self._append_log("[SFTP] paramiko 未安装")
            return
        mode = self.ui.p2p_mode_combo.currentText()
        server_name = ''
        if mode == "XTCP":
            if not self._p2p_visitors:
                self._append_log("[SFTP] 请先添加 visitor 配置")
                return
            idx = self._p2p_current_index
            if not (0 <= idx < len(self._p2p_visitors)):
                self._append_log("[SFTP] 请先在列表中选择一个 visitor")
                return
            host = "127.0.0.1"
            port = self._p2p_visitors[idx]["bindPort"]
            server_name = self._p2p_visitors[idx].get("serverName", "")
        elif mode == "TCP":
            host = self.ui.p2p_ssh_host.text().strip()
            port = self.ui.p2p_ssh_port.value()
        else:
            self._append_log("[SFTP] SFTP 仅支持 XTCP/TCP 模式")
            return
        username = self.ui.p2p_ssh_user.text()
        password = self.ui.p2p_ssh_pass.text()
        if not host:
            self._append_log("[SFTP] 主机地址不能为空")
            return
        self._save_ssh_credentials()
        if self._sftp_window is not None:
            try:
                self._sftp_window.close()
            except (RuntimeError, OSError):
                pass
            self._sftp_window = None
        self._append_log(f"[SFTP] 打开文件管理: {server_name or host}:{port}")
        self._sftp_window = SFTPWindow(
            host, port, username, password,
            server_name=server_name,
            log_callback=lambda msg: self._append_log(msg),
            parent=self  # type: ignore[arg-type]
        )
        self._sftp_window.show()

    def _on_ssh_terminal_btn_clicked(self):
        """打开 SSH 终端窗口"""
        if not PARAMIKO_AVAILABLE:
            self._append_log("[SSH] paramiko 未安装")
            return
        mode = self.ui.p2p_mode_combo.currentText()
        if mode == "XTCP":
            if not self._p2p_visitors:
                self._append_log("[SSH] 请先添加 visitor 配置")
                return
            idx = self._p2p_current_index
            if not (0 <= idx < len(self._p2p_visitors)):
                self._append_log("[SSH] 请先在列表中选择一个 visitor")
                return
            host = "127.0.0.1"
            port = self._p2p_visitors[idx]["bindPort"]
        elif mode == "TCP":
            host = self.ui.p2p_ssh_host.text().strip()
            port = self.ui.p2p_ssh_port.value()
        else:
            self._append_log("[SSH] SSH 终端仅支持 XTCP/TCP 模式")
            return
        username = self.ui.p2p_ssh_user.text()
        password = self.ui.p2p_ssh_pass.text()
        if not host:
            self._append_log("[SSH] 主机地址不能为空")
            return
        self._save_ssh_credentials()
        if self._ssh_terminal_window is not None:
            try:
                self._ssh_terminal_window.close()
            except (RuntimeError, OSError):
                pass
            self._ssh_terminal_window = None
        self._append_log(f"[SSH] 打开终端: {host}:{port}")
        self._ssh_terminal_window = SSHTerminalWindow(
            host, port, username, password,
            log_callback=lambda msg: self._append_log(msg),
            parent=self  # type: ignore[arg-type]
        )
        self._ssh_terminal_window.show()

    def _on_rdp_btn_clicked(self):
        """打开远程桌面窗口（嵌入 mstsc.exe）"""
        if sys.platform != 'win32':
            self._append_log("[RDP] 远程桌面仅支持 Windows")
            return
        mode = self.ui.p2p_mode_combo.currentText()
        server_name = ''
        if mode == "XTCP":
            if not self._p2p_visitors:
                self._append_log("[RDP] 请先添加 visitor 配置")
                return
            idx = self._p2p_current_index
            if not (0 <= idx < len(self._p2p_visitors)):
                self._append_log("[RDP] 请先在列表中选择一个 visitor")
                return
            host = "127.0.0.1"
            port = self._p2p_visitors[idx]["bindPort"]
            server_name = self._p2p_visitors[idx].get("serverName", "")
        elif mode == "TCP":
            host = self.ui.p2p_ssh_host.text().strip()
            port = self.ui.p2p_ssh_port.value()
        else:
            self._append_log("[RDP] 远程桌面仅支持 XTCP/TCP 模式")
            return
        username = self.ui.p2p_ssh_user.text()
        password = self.ui.p2p_ssh_pass.text()
        if not host:
            self._append_log("[RDP] 主机地址不能为空")
            return
        self._save_ssh_credentials()
        if self._rdp_window is not None:
            try:
                self._rdp_window.close()
            except (RuntimeError, OSError):
                pass
            self._rdp_window = None
        self._append_log(f"[RDP] 打开远程桌面: {server_name or host}:{port}")
        self._rdp_window = RDPWindow(
            host, port, username, password,
            server_name=server_name,
            log_callback=lambda msg: self._append_log(msg),
            parent=self  # type: ignore[arg-type]
        )
        self._rdp_window.show()
