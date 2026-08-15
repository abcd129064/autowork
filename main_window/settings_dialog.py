# -*- coding: utf-8 -*-
"""SettingsDialog：统一设置面板（从 ui_mixin.py 提取为独立模块）

分页懒加载：Pivot 导航 + 每页首次切入才构建控件，
避免打开时同步创建全部五个分区的控件。
collect() 采用数据驱动：配置项描述表 + 统一收集循环。
"""

import os
import re

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout,
    QHBoxLayout, QStackedWidget, QFileDialog, QDialog, QListWidget,
    QListWidgetItem)
from qfluentwidgets import (MessageBoxBase, SpinBox, ComboBox, LineEdit,
    ToolButton, FluentIcon, BodyLabel, CaptionLabel, Pivot, SwitchButton,
    PushButton, ColorDialog)

from core.ai_providers import AI_PROVIDERS, get_provider


# 日志高亮规则默认值（main_window 从本模块导入，保证设置与渲染同一来源）
# 注意：早期版本的高亮关键词（'返回'/'add'）写死在前端代码中，
# 规则系统化时迁移为默认规则（颜色沿用旧高亮色 [220,80,20]），不可删除，否则旧用户日志高亮会丢失
_DEFAULT_LOG_RULES = [
    {"name": "错误", "pattern": r"ERROR|Exception|Traceback|error",
     "color": "#ff5252", "notify": True},
    {"name": "警告", "pattern": r"WARN|WARNING|超时|失败|timeout",
     "color": "#f0a020", "notify": False},
    {"name": "返回", "pattern": r"返回",
     "color": "#dc5014", "notify": False},
    {"name": "加分", "pattern": r"加分",
     "color": "#dc5014", "notify": False},
    {"name": "add", "pattern": r"add",
     "color": "#dc5014", "notify": False},
]


def _compile_log_rules(raw_rules):
    """把规则配置编译为可匹配对象列表（非法正则可跳过）

    供 main_window（日志渲染）与 ui_mixin（设置保存后即时刷新）共用，
    避免两处重复实现导致行为分叉。
    """
    rules = []
    for r in raw_rules or []:
        try:
            rules.append({
                "name": str(r.get("name", "") or ""),
                "regex": re.compile(r.get("pattern", "") or ""),
                "color": str(r.get("color", "#ff5252") or "#ff5252"),
                "notify": bool(r.get("notify", False)),
            })
        except re.error:
            continue
    return rules


# NewLog 批量整理路径默认值（与 settings_mixin.DEFAULT_PATHS 保持一致）
_DEFAULT_EXCEL_DIR = os.path.expanduser(r"~\Desktop\excel")
_DEFAULT_OUT_DIR = os.path.expanduser(r"~\Desktop")


class SettingsDialog(MessageBoxBase):
    """设置对话框（分页懒加载）：Pivot 导航 + 每页首次切入才构建控件"""

    # 分区：(key, 标题)，顺序即页顺序
    _SECTIONS = [
        ("paths", "路径配置"),
        ("remote", "远程连接"),
        ("upload", "收集与上传"),
        ("ai", "API Key"),
        ("log_rules", "日志高亮"),
        ("appearance", "外观"),
    ]

    # ---------- 配置项描述表（数据驱动 collect） ----------
    # 每项: (配置key, 所属分区, 控件获取lambda, 读取函数, 回退函数)
    #   控件获取: lambda self -> widget or None（未构建时返回 None）
    #   读取函数: lambda widget -> 从控件读值
    #   回退函数: lambda cfg -> 未构建时从原始配置取值

    @staticmethod
    def _build_config_items():
        items = []

        # 路径配置（5项）—— 控件存在 _path_edits 字典中
        for key in ("exe_dir", "videos_dir", "cipher_tool",
                    "front_exe", "backend_exe"):
            items.append((
                key, "paths",
                lambda s, k=key: s._path_edits.get(k),
                lambda w: w.text().strip(),
                lambda cfg, k=key: str(cfg.get(k, "") or ""),
            ))

        # NewLog 批量整理路径（2项）—— 默认值 = 当前用户桌面
        for key, default in (("newlog_excel_dir", _DEFAULT_EXCEL_DIR),
                             ("newlog_out_dir", _DEFAULT_OUT_DIR)):
            items.append((
                key, "paths",
                lambda s, k=key: s._path_edits.get(k),
                lambda w: w.text().strip(),
                lambda cfg, k=key, d=default: str(cfg.get(k, "") or d),
            ))

        # 远程连接（5项）
        items.append(("ssh_user", "remote",
                       lambda s: getattr(s, '_edit_ssh_user', None),
                       lambda w: w.text().strip(),
                       lambda cfg: str(cfg.get("ssh_user", "") or "")))
        items.append(("ssh_pass", "remote",
                       lambda s: getattr(s, '_edit_ssh_pass', None),
                       lambda w: w.text().strip(),
                       lambda cfg: str(cfg.get("ssh_pass", "") or "")))
        items.append(("sftp_default_remote_path", "remote",
                       lambda s: getattr(s, '_edit_sftp_path', None),
                       lambda w: w.text().strip(),
                       lambda cfg: str(cfg.get("sftp_default_remote_path", "") or "")))
        items.append(("tcp_servers", "remote",
                       lambda s: getattr(s, '_edit_tcp_servers', None),
                       lambda w: [x.strip() for x in w.text().strip().split(",") if x.strip()],
                       lambda cfg: list(cfg.get("tcp_servers", []) or [])))
        # 启动时恢复远程会话（默认开启；关闭后重启不再自动恢复未退出的会话）
        items.append(("restore_remote_sessions", "remote",
                       lambda s: getattr(s, '_switch_restore_sessions', None),
                       lambda w: bool(w.isChecked()),
                       lambda cfg: bool(cfg.get("restore_remote_sessions", True))))

        # 收集与上传（5项）
        items.append(("upload_host", "upload",
                       lambda s: getattr(s, '_edit_upload_host', None),
                       lambda w: w.text().strip(),
                       lambda cfg: str(cfg.get("upload_host", "49.235.34.253") or "")))
        items.append(("upload_port", "upload",
                       lambda s: getattr(s, '_edit_upload_port', None),
                       lambda w: _safe_int(w.text().strip(), 22),
                       lambda cfg: _safe_int(cfg.get("upload_port", 22), 22)))
        items.append(("upload_remote_dir", "upload",
                       lambda s: getattr(s, '_edit_upload_dir', None),
                       lambda w: w.text().strip(),
                       lambda cfg: str(cfg.get("upload_remote_dir", "/lhcos-data/videos") or "")))
        items.append(("upload_user", "upload",
                       lambda s: getattr(s, '_edit_upload_user', None),
                       lambda w: w.text().strip() or "root",
                       lambda cfg: str(cfg.get("upload_user", "root") or "root")))
        items.append(("upload_pass", "upload",
                       lambda s: getattr(s, '_edit_upload_pass', None),
                       lambda w: w.text(),
                       lambda cfg: str(cfg.get("upload_pass", "") or "")))

        # FRPC 服务器（1项，嵌套字典）——已合并到「远程连接」分区
        items.append(("frpc_server", "remote",
                       lambda s: getattr(s, '_frpc_built', False) and s or None,
                       lambda s: {
                           "serverAddr": s._edit_frpc_addr.text().strip(),
                           "serverPort": _safe_int(s._edit_frpc_port.text().strip(), 7000),
                           "auth_method": "token",
                           "auth_token": s._edit_frpc_token.text().strip(),
                       },
                       lambda cfg: _fallback_frpc(cfg)))

        # ai（4项：厂商/模型/开关 + 各厂商 API Key 字典）
        items.append(("ai_vendor", "ai",
                       lambda s: getattr(s, '_combo_ai_vendor', None),
                       lambda w: w.currentData() or "deepseek",
                       lambda cfg: str(cfg.get("ai_vendor", "deepseek") or "deepseek")))
        items.append(("ai_model", "ai",
                       lambda s: getattr(s, '_edit_ai_model', None),
                       lambda w: w.text().strip(),
                       lambda cfg: str(cfg.get("ai_model", "") or "")))
        items.append(("forensic_ai_analysis", "ai",
                       lambda s: getattr(s, '_switch_ai_enabled', None),
                       lambda w: bool(w.isChecked()),
                       lambda cfg: bool(cfg.get("forensic_ai_analysis", True))))
        # ai_api_keys 与 frpc 同为特殊项：widget_fn 返回 self，
        # reader 将当前厂商的 Key 合并回已有字典（未编辑的其他厂商 Key 不丢失）
        items.append(("ai_api_keys", "ai",
                       lambda s: getattr(s, '_ai_built', False) and s or None,
                       lambda s: _collect_ai_keys(s),
                       lambda cfg: dict(cfg.get("ai_api_keys", {}) or {})))

        # 日志高亮规则（特殊项：widget_fn 返回 self，reader 收集 UI 状态）
        items.append(("log_highlight_rules", "log_rules",
                       lambda s: getattr(s, '_log_rules_built', False) and s or None,
                       lambda s: s._collect_log_rules(),
                       lambda cfg: list(cfg.get("log_highlight_rules",
                                                _DEFAULT_LOG_RULES))))

        # 外观（3项）
        items.append(("font_size", "appearance",
                       lambda s: getattr(s, '_spin_font_size', None),
                       lambda w: w.value(),
                       lambda cfg: cfg.get("font_size", 11)))
        items.append(("dpi_scale", "appearance",
                       lambda s: getattr(s, '_combo_dpi', None),
                       lambda w: int(w.currentText().replace("%", "")),
                       lambda cfg: cfg.get("dpi_scale", 100)))
        items.append(("font_family", "appearance",
                       lambda s: getattr(s, '_edit_font_family', None),
                       lambda w: w.text().strip(),
                       lambda cfg: cfg.get("font_family", "Microsoft YaHei UI")))

        return items

    # frpc 的 reader 比较特殊：reader 拿到的是 self（整个对话框），从中读取多个控件
    # 需要在 collect 循环中特殊处理：当 reader 的第一个参数是 self 而非 widget 时
    # 实际上 frpc 的 widget_fn 返回的是 self 或 None，reader_fn 接收 self
    # 为保持一致性，frpc 的 reader_fn 签名仍为 lambda widget -> value，
    # 只是这里 widget 实际上是 self（对话框实例）

    _CONFIG_ITEMS = _build_config_items()

    def __init__(self, parent, cfg):
        super().__init__(parent)
        self.titleLabel = BodyLabel("设置", self)
        self.viewLayout.addWidget(self.titleLabel)

        self._cfg = cfg
        self._path_edits = {}
        self._built = set()    # 已构建的分区 key
        self._pages = {}       # key -> 占位页 widget
        self._frpc_built = False  # FRPC 分区是否已构建
        self._ai_built = False    # AI 分析分区是否已构建

        # Pivot 分页导航 + 页面堆栈（页面控件首次切入时才创建）
        self.pivot = Pivot(self)
        self.stack = QStackedWidget(self)
        self.stack.setMinimumHeight(240)
        for key, title in self._SECTIONS:
            self.pivot.addItem(
                routeKey=key, text=title,
                onClick=lambda checked=False, k=key: self._switch_section(k))
            self._pages[key] = QWidget(self.stack)
            self.stack.addWidget(self._pages[key])
        self.viewLayout.addWidget(self.pivot)
        self.viewLayout.addWidget(self.stack)

        # 默认展示第一页（路径配置）
        self._switch_section("paths")

    def _switch_section(self, key):
        """切换到指定分区：首次切入时构建该页控件"""
        self.pivot.setCurrentItem(key)
        idx = [k for k, _ in self._SECTIONS].index(key)
        self.stack.setCurrentIndex(idx)
        if key in self._built:
            return
        self._built.add(key)
        layout = QVBoxLayout(self._pages[key])
        layout.setContentsMargins(4, 10, 4, 4)
        layout.setSpacing(10)
        self.main_layout = layout
        getattr(self, f"_build_{key}_section")(self._cfg)
        layout.addStretch(1)

    # ---------- 路径配置 ----------
    def _build_paths_section(self, cfg):
      #  self._add_section_header("📂 路径配置")
        path_items = [
            ("exe_dir", "程序目录", cfg.get("exe_dir", ""), "dir"),
            ("videos_dir", "视频/日志目录", cfg.get("videos_dir", ""), "dir"),
            ("cipher_tool", "加密工具", cfg.get("cipher_tool", ""), "file"),
            ("front_exe", "前端程序", cfg.get("front_exe", ""), "file"),
            ("backend_exe", "后端程序", cfg.get("backend_exe", ""), "file"),
            ("newlog_excel_dir", "整理Excel目录",
             cfg.get("newlog_excel_dir", _DEFAULT_EXCEL_DIR), "dir"),
            ("newlog_out_dir", "整理输出目录",
             cfg.get("newlog_out_dir", _DEFAULT_OUT_DIR), "dir"),
        ]
        for key, label, value, mode in path_items:
            row = QHBoxLayout()
            lbl = BodyLabel(label, self)
            lbl.setFixedWidth(90)
            row.addWidget(lbl)
            edit = LineEdit(self)
            edit.setText(value)
            edit.setPlaceholderText(f"请选择{label}...")
            row.addWidget(edit, 1)
            btn = ToolButton(FluentIcon.FOLDER, self)
            btn.setFixedSize(32, 32)
            btn.setToolTip("浏览...")
            btn.clicked.connect(lambda checked, e=edit, m=mode: self._browse(e, m))
            row.addWidget(btn)
            self._path_edits[key] = edit
            self.main_layout.addLayout(row)

    # ---------- 远程连接（含 FRPC 服务器，原独立分区已合并至此） ----------
    def _build_remote_section(self, cfg):
       # self._add_section_header("🌐 远程连接")
        form = QFormLayout()
        form.setSpacing(8)

        # 会话恢复开关：关闭后重启程序不再自动恢复上次未退出的远程会话
        self._switch_restore_sessions = SwitchButton(self)
        self._switch_restore_sessions.setChecked(
            bool(cfg.get("restore_remote_sessions", True)))
        self._switch_restore_sessions.setOnText("开")
        self._switch_restore_sessions.setOffText("关")
        self._switch_restore_sessions.setToolTip(
            "关闭后重启程序不会自动恢复上次未退出的 SSH/SFTP/远程桌面会话")
        form.addRow("启动时恢复远程会话:", self._switch_restore_sessions)

        self._edit_ssh_user = LineEdit(self)
        self._edit_ssh_user.setText(cfg.get("ssh_user", ""))
        self._edit_ssh_user.setPlaceholderText("SSH 用户名")
        form.addRow("SSH 用户名:", self._edit_ssh_user)

        self._edit_ssh_pass = LineEdit(self)
        self._edit_ssh_pass.setText(cfg.get("ssh_pass", ""))
        self._edit_ssh_pass.setEchoMode(LineEdit.EchoMode.Password)
        self._edit_ssh_pass.setPlaceholderText("SSH 密码")
        form.addRow("SSH 密码:", self._edit_ssh_pass)

        self._edit_sftp_path = LineEdit(self)
        self._edit_sftp_path.setText(cfg.get("sftp_default_remote_path", ""))
        self._edit_sftp_path.setPlaceholderText("如 /home/user/project")
        form.addRow("SFTP默认路径:", self._edit_sftp_path)

        # self._edit_tcp_servers = LineEdit(self)
        # servers = cfg.get("tcp_servers", [])
        # self._edit_tcp_servers.setText(", ".join(servers))
        # self._edit_tcp_servers.setPlaceholderText("多个用逗号分隔，如 ip:port, ip:port")
        # form.addRow("TCP服务器:", self._edit_tcp_servers)

        self.main_layout.addLayout(form)

        # ---- FRPC 服务器（穿透配置与远程连接同属一条链路，合并展示） ----
        self._add_section_header("FRPC 服务器")
        frpc_form = QFormLayout()
        frpc_form.setSpacing(8)
        frpc = cfg.get("frpc_server", {})

        self._edit_frpc_addr = LineEdit(self)
        self._edit_frpc_addr.setText(frpc.get("serverAddr", ""))
        self._edit_frpc_addr.setPlaceholderText("服务器 IP")
        frpc_form.addRow("服务器地址:", self._edit_frpc_addr)

        self._edit_frpc_port = LineEdit(self)
        self._edit_frpc_port.setText(str(frpc.get("serverPort", 7000)))
        self._edit_frpc_port.setPlaceholderText("端口号")
        frpc_form.addRow("服务器端口:", self._edit_frpc_port)

        self._edit_frpc_token = LineEdit(self)
        self._edit_frpc_token.setText(frpc.get("auth_token", ""))
        self._edit_frpc_token.setEchoMode(LineEdit.EchoMode.Password)
        self._edit_frpc_token.setPlaceholderText("认证 Token")
        frpc_form.addRow("认证 Token:", self._edit_frpc_token)

        self.main_layout.addLayout(frpc_form)
        self._frpc_built = True

    # ---------- 收集与上传 ----------
    def _build_upload_section(self, cfg):
       # self._add_section_header("📦 收集与上传")
        form = QFormLayout()
        form.setSpacing(8)

        self._edit_upload_host = LineEdit(self)
        self._edit_upload_host.setText(cfg.get("upload_host", "49.235.34.253"))
        self._edit_upload_host.setPlaceholderText("上传服务器 IP")
        form.addRow("上传服务器:", self._edit_upload_host)

        self._edit_upload_port = LineEdit(self)
        self._edit_upload_port.setText(str(cfg.get("upload_port", 22)))
        self._edit_upload_port.setPlaceholderText("端口号")
        form.addRow("上传端口:", self._edit_upload_port)

        self._edit_upload_dir = LineEdit(self)
        self._edit_upload_dir.setText(
            cfg.get("upload_remote_dir", "/lhcos-data/videos"))
        self._edit_upload_dir.setPlaceholderText("如 /lhcos-data/videos")
        form.addRow("远程目录:", self._edit_upload_dir)

        self._edit_upload_user = LineEdit(self)
        self._edit_upload_user.setText(cfg.get("upload_user", "root"))
        self._edit_upload_user.setPlaceholderText("上传用户名")
        form.addRow("上传用户名:", self._edit_upload_user)

        self._edit_upload_pass = LineEdit(self)
        self._edit_upload_pass.setText(cfg.get("upload_pass", ""))
        self._edit_upload_pass.setEchoMode(LineEdit.EchoMode.Password)
        self._edit_upload_pass.setPlaceholderText("上传密码")
        form.addRow("上传密码:", self._edit_upload_pass)

        self.main_layout.addLayout(form)

    # ---------- AI 分析 ----------
    def _build_ai_section(self, cfg):
       # self._add_section_header("🤖 AI SSH日志分析")
        form = QFormLayout()
        form.setSpacing(8)

        self._switch_ai_enabled = SwitchButton(self)
        self._switch_ai_enabled.setChecked(
            bool(cfg.get("forensic_ai_analysis", True)))
        self._switch_ai_enabled.setOnText("开")
        self._switch_ai_enabled.setOffText("关")
        form.addRow("启用 AI SSH日志分析:", self._switch_ai_enabled)

        self._combo_ai_vendor = ComboBox(self)
        for p in AI_PROVIDERS:
            # 注意：qfluentwidgets addItem 第二参是 icon，userData 必须关键字传参
            self._combo_ai_vendor.addItem(p["label"], userData=p["id"])
        cur_vendor = str(cfg.get("ai_vendor", "deepseek") or "deepseek")
        for i in range(self._combo_ai_vendor.count()):
            if self._combo_ai_vendor.itemData(i) == cur_vendor:
                self._combo_ai_vendor.setCurrentIndex(i)
                break
        form.addRow("模型厂商:", self._combo_ai_vendor)

        self._edit_ai_key = LineEdit(self)
        self._edit_ai_key.setEchoMode(LineEdit.EchoMode.Password)
        self._edit_ai_key.setPlaceholderText(
            "各厂商开放平台创建的 API Key")
        form.addRow("API Key:", self._edit_ai_key)

        self._edit_ai_model = LineEdit(self)
        self._edit_ai_model.setPlaceholderText("模型名")
        form.addRow("模型:", self._edit_ai_model)

        # 接口地址提示（随厂商切换更新）
        self._lbl_ai_base_url = CaptionLabel("", self)
        self._lbl_ai_base_url.setWordWrap(True)
        form.addRow("接口地址:", self._lbl_ai_base_url)

        # 先填充当前厂商的 Key/模型/地址，再接信号（避免填充过程误触发）
        self._apply_ai_vendor(cur_vendor, initial=True)
        self._combo_ai_vendor.currentIndexChanged.connect(
            lambda _idx: self._on_ai_vendor_changed())

        self.main_layout.addLayout(form)
        self._ai_built = True

    def _get_saved_ai_keys(self) -> dict:
        """已保存的各厂商 API Key（明文，来自打开对话框时的配置）"""
        keys = self._cfg.get("ai_api_keys", {})
        return keys if isinstance(keys, dict) else {}

    def _apply_ai_vendor(self, vendor_id: str, initial: bool = False):
        """按厂商刷新 Key 输入框（各家 Key 分别保存）、模型与接口地址

        initial=True 时保留已保存的自定义模型；后续手动切换厂商则
        重置为该厂商官方默认模型（避免拿错模型名，用户可再改）。
        """
        provider = get_provider(vendor_id)
        self._edit_ai_key.setText(
            str(self._get_saved_ai_keys().get(provider["id"]) or ""))
        saved_model = str(self._cfg.get("ai_model", "") or "").strip()
        if initial and saved_model:
            self._edit_ai_model.setText(saved_model)
        else:
            self._edit_ai_model.setText(provider["default_model"])
        self._lbl_ai_base_url.setText(
            f"{provider['base_url']}（环境变量：{provider['env_key']}）")

    def _on_ai_vendor_changed(self):
        vendor_id = self._combo_ai_vendor.currentData() or "deepseek"
        self._apply_ai_vendor(vendor_id)

    # ---------- 日志高亮规则 ----------
    def _build_log_rules_section(self, cfg):
        """日志高亮规则管理：列表 + 添加/编辑/删除，规则存 log_highlight_rules"""
       # self._add_section_header("🎨 日志高亮规则")
        tip = CaptionLabel(
            "规则按序匹配，命中行整行着色；开启「命中通知」后弹 InfoBar"
            "（每规则 10 秒去重）。正则写法与 Python re 一致，如 ERROR|Exception。", self)
        tip.setWordWrap(True)
        self.main_layout.addWidget(tip)

        self._log_rules_state = list(
            cfg.get("log_highlight_rules") or _DEFAULT_LOG_RULES)
        self._log_rules_list = QListWidget(self)
        self._log_rules_list.setFixedHeight(150)
        self.main_layout.addWidget(self._log_rules_list)

        row = QHBoxLayout()
        btn_add = PushButton(FluentIcon.ADD, "添加", self)
        btn_add.clicked.connect(self._add_log_rule)
        btn_edit = PushButton(FluentIcon.EDIT, "编辑", self)
        btn_edit.clicked.connect(self._edit_log_rule)
        btn_del = PushButton(FluentIcon.DELETE, "删除", self)
        btn_del.clicked.connect(self._del_log_rule)
        row.addWidget(btn_add)
        row.addWidget(btn_edit)
        row.addWidget(btn_del)
        row.addStretch(1)
        self.main_layout.addLayout(row)

        self._refresh_log_rules_list()
        self._log_rules_built = True

    def _refresh_log_rules_list(self):
        """按当前规则状态重建列表（规则名 + 正则 + 颜色 + 通知开关）"""
        self._log_rules_list.clear()
        for r in self._log_rules_state:
            name = r.get("name", "") or ""
            pat = r.get("pattern", "") or ""
            notify = "通知" if r.get("notify") else "静默"
            item = QListWidgetItem(f"{name}  ·  {pat}  ·  {notify}")
            color = r.get("color", "#ff5252") or "#ff5252"
            try:
                item.setForeground(QColor(color))
            except Exception:
                pass
            item.setData(1, r)
            self._log_rules_list.addItem(item)

    def _current_log_rule(self):
        """当前选中规则 dict，未选中返回 None"""
        item = self._log_rules_list.currentItem()
        return item.data(1) if item is not None else None

    def _add_log_rule(self):
        rule = self._edit_rule_dialog({"name": "", "pattern": "",
                                        "color": "#ff5252", "notify": True})
        if rule:
            self._log_rules_state.append(rule)
            self._refresh_log_rules_list()

    def _edit_log_rule(self):
        rule = self._current_log_rule()
        if rule is None:
            return
        new_rule = self._edit_rule_dialog(dict(rule))
        if new_rule:
            self._log_rules_state[self._log_rules_state.index(rule)] = new_rule
            self._refresh_log_rules_list()

    def _del_log_rule(self):
        rule = self._current_log_rule()
        if rule is None:
            return
        self._log_rules_state.remove(rule)
        self._refresh_log_rules_list()

    def _edit_rule_dialog(self, rule):
        """规则编辑弹窗：确定返回新规则 dict，取消/非法正则返回 None"""
        dlg = QDialog(self)
        dlg.setWindowTitle("编辑规则" if rule.get("name") else "添加规则")
        dlg.resize(460, 250)
        v = QVBoxLayout(dlg)
        form = QFormLayout()
        form.setSpacing(8)

        name_edit = LineEdit(dlg)
        name_edit.setText(rule.get("name", "") or "")
        name_edit.setPlaceholderText("规则名，如：错误")
        form.addRow("规则名:", name_edit)

        pat_edit = LineEdit(dlg)
        pat_edit.setText(rule.get("pattern", "") or "")
        pat_edit.setPlaceholderText("正则，如 ERROR|Exception")
        form.addRow("匹配正则:", pat_edit)

        color = QColor(rule.get("color", "#ff5252") or "#ff5252")
        color_lbl = BodyLabel(color.name(), dlg)
        color_lbl.setStyleSheet(f"color:{color.name()}; font-weight:bold;")
        btn_color = PushButton("选择颜色…", dlg)

        def _pick():
            nonlocal color
            cd = ColorDialog(color, "选择高亮颜色", dlg)
            if cd.exec():
                color = cd.color
                color_lbl.setText(color.name())
                color_lbl.setStyleSheet(
                    f"color:{color.name()}; font-weight:bold;")

        btn_color.clicked.connect(_pick)
        h = QHBoxLayout()
        h.addWidget(btn_color)
        h.addWidget(color_lbl)
        h.addStretch(1)
        form.addRow("颜色:", h)

        notify_sw = SwitchButton("命中时弹通知", dlg)
        notify_sw.setChecked(bool(rule.get("notify")))
        form.addRow("命中通知:", notify_sw)
        v.addLayout(form)

        btns = QHBoxLayout()
        btns.addStretch(1)
        btn_ok = PushButton("确定", dlg)
        btn_ok.clicked.connect(dlg.accept)
        btn_cancel = PushButton("取消", dlg)
        btn_cancel.clicked.connect(dlg.reject)
        btns.addWidget(btn_ok)
        btns.addWidget(btn_cancel)
        v.addLayout(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        pat = pat_edit.text().strip()
        if not pat:
            return None
        try:
            re.compile(pat)
        except re.error:
            return None
        return {"name": name_edit.text().strip() or pat,
                "pattern": pat,
                "color": color.name(),
                "notify": notify_sw.isChecked()}

    def _collect_log_rules(self):
        """collect 用：返回当前规则编辑状态（未构建分区时返回空，回退走 cfg）"""
        return list(getattr(self, "_log_rules_state", []) or [])

    # ---------- 外观 ----------
    def _build_appearance_section(self, cfg):
       # self._add_section_header("🎨 外观")
        form = QFormLayout()
        form.setSpacing(8)

        self._spin_font_size = SpinBox(self)
        self._spin_font_size.setRange(10, 20)
        self._spin_font_size.setValue(cfg.get("font_size", 11))
        self._spin_font_size.setSuffix(" pt")
        form.addRow("字号大小:", self._spin_font_size)

        self._combo_dpi = ComboBox(self)
        dpi_options = [100, 125, 150, 175, 200]
        self._combo_dpi.addItems([f"{d}%" for d in dpi_options])
        cur_dpi = cfg.get("dpi_scale", 100)
        if cur_dpi in dpi_options:
            self._combo_dpi.setCurrentIndex(dpi_options.index(cur_dpi))
        form.addRow("界面缩放:", self._combo_dpi)

        self._edit_font_family = LineEdit(self)
        self._edit_font_family.setText(cfg.get("font_family", "Microsoft YaHei UI"))
        self._edit_font_family.setPlaceholderText("字体名称")
        form.addRow("字体:", self._edit_font_family)

        self.main_layout.addLayout(form)

    # ---------- 工具 ----------
    def _add_section_header(self, text):
        '''添加节标题'''
        lbl = CaptionLabel(text, self)
        lbl.setStyleSheet("font-weight: bold; font-size: 13px; margin-top: 4px;")
        self.main_layout.addWidget(lbl)

    def _browse(self, edit, mode):
        '''浏览目录或文件'''
        if mode == "dir":
            path = QFileDialog.getExistingDirectory(self, "选择目录", edit.text())
        else:
            path, _ = QFileDialog.getOpenFileName(self, "选择文件", edit.text())
        if path:
            edit.setText(path)

    def collect(self):
        """数据驱动收集所有编辑结果：遍历配置项描述表，统一处理已构建/未构建分支"""
        cfg = self._cfg
        data = {}
        for key, section, widget_fn, reader_fn, fallback_fn in self._CONFIG_ITEMS:
            widget = widget_fn(self)
            if widget is not None:
                data[key] = reader_fn(widget)
            else:
                data[key] = fallback_fn(cfg)
        return data


# ---------- 模块级辅助函数 ----------

def _safe_int(value, default):
    """安全整数转换，失败时返回默认值"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _fallback_frpc(cfg):
    """FRPC 配置回退值"""
    frpc = dict(cfg.get("frpc_server", {}) or {})
    return {
        "serverAddr": str(frpc.get("serverAddr", "") or ""),
        "serverPort": frpc.get("serverPort", 7000),
        "auth_method": frpc.get("auth_method", "token"),
        "auth_token": str(frpc.get("auth_token", "") or ""),
    }


def _collect_ai_keys(dialog):
    """收集各厂商 API Key：已有字典为基础，用当前编辑的厂商 Key 覆盖；
    清空 Key 时删除对应条目（保持 settings.json 整洁）"""
    keys = dict(dialog._get_saved_ai_keys())
    vendor_id = dialog._combo_ai_vendor.currentData() or "deepseek"
    value = dialog._edit_ai_key.text().strip()
    if value:
        keys[vendor_id] = value
    else:
        keys.pop(vendor_id, None)
    return keys

def _web_port():
    if os.environ.get("WEB_PORT") is not None:
        return int(os.environ.get("WEB_PORT"))
    else:
        return 8080

