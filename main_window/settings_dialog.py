# -*- coding: utf-8 -*-
"""SettingsDialog：统一设置面板（从 ui_mixin.py 提取为独立模块）

分页懒加载：Pivot 导航 + 每页首次切入才构建控件，
避免打开时同步创建全部五个分区的控件。
collect() 采用数据驱动：配置项描述表 + 统一收集循环。
"""

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout,
    QHBoxLayout, QStackedWidget, QFileDialog)
from qfluentwidgets import (MessageBoxBase, SpinBox, ComboBox, LineEdit,
    ToolButton, FluentIcon, BodyLabel, CaptionLabel, Pivot)


class SettingsDialog(MessageBoxBase):
    """设置对话框（分页懒加载）：Pivot 导航 + 每页首次切入才构建控件"""

    # 分区：(key, 标题)，顺序即页顺序
    _SECTIONS = [
        ("paths", "路径配置"),
        ("remote", "远程连接"),
        ("upload", "收集与上传"),
        ("frpc", "FRPC 服务器"),
        ("appearance", "外观"),
    ]

    # ---------- 配置项描述表（数据驱动 collect） ----------
    # 每项: (配置key, 所属分区, 控件获取lambda, 读取函数, 回退函数)
    #   控件获取: lambda self -> widget or None（未构建时返回 None）
    #   读取函数: lambda widget -> 从控件读值
    #   回退函数: lambda cfg -> 未构建时从原始配置取值

    def _build_config_items():
        """构建配置项描述表（静态方法，初始化时调用一次）"""
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

        # 远程连接（4项）
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

        # FRPC 服务器（1项，嵌套字典）
        items.append(("frpc_server", "frpc",
                       lambda s: getattr(s, '_frpc_built', False) and s or None,
                       lambda s: {
                           "serverAddr": s._edit_frpc_addr.text().strip(),
                           "serverPort": _safe_int(s._edit_frpc_port.text().strip(), 7000),
                           "auth_method": "token",
                           "auth_token": s._edit_frpc_token.text().strip(),
                       },
                       lambda cfg: _fallback_frpc(cfg)))

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
        self._add_section_header("📂 路径配置")
        path_items = [
            ("exe_dir", "程序目录", cfg.get("exe_dir", ""), "dir"),
            ("videos_dir", "视频/日志目录", cfg.get("videos_dir", ""), "dir"),
            ("cipher_tool", "加密工具", cfg.get("cipher_tool", ""), "file"),
            ("front_exe", "前端程序", cfg.get("front_exe", ""), "file"),
            ("backend_exe", "后端程序", cfg.get("backend_exe", ""), "file"),
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

    # ---------- 远程连接 ----------
    def _build_remote_section(self, cfg):
        self._add_section_header("🌐 远程连接")
        form = QFormLayout()
        form.setSpacing(8)

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

        self._edit_tcp_servers = LineEdit(self)
        servers = cfg.get("tcp_servers", [])
        self._edit_tcp_servers.setText(", ".join(servers))
        self._edit_tcp_servers.setPlaceholderText("多个用逗号分隔，如 ip:port, ip:port")
        form.addRow("TCP服务器:", self._edit_tcp_servers)

        self.main_layout.addLayout(form)

    # ---------- 收集与上传 ----------
    def _build_upload_section(self, cfg):
        self._add_section_header("📦 收集与上传")
        form = QFormLayout()
        form.setSpacing(8)

        self._edit_upload_host = LineEdit(self)
        self._edit_upload_host.setText(cfg.get("upload_host", "49.235.34.253"))
        self._edit_upload_host.setPlaceholderText("上传服务器 IP")
        form.addRow("上传服务器:", self._edit_upload_host)

        self._edit_upload_port = LineEdit(self)
        self._edit_upload_port.setText(str(cfg.get("upload_port", 22)))
        self._edit_upload_port.setPlaceholderText("端口号（默认 22）")
        form.addRow("上传端口:", self._edit_upload_port)

        self._edit_upload_dir = LineEdit(self)
        self._edit_upload_dir.setText(
            cfg.get("upload_remote_dir", "/lhcos-data/videos"))
        self._edit_upload_dir.setPlaceholderText("如 /lhcos-data/videos")
        form.addRow("远程目录:", self._edit_upload_dir)

        self._edit_upload_user = LineEdit(self)
        self._edit_upload_user.setText(cfg.get("upload_user", "root"))
        self._edit_upload_user.setPlaceholderText("上传用户名（默认 root）")
        form.addRow("上传用户名:", self._edit_upload_user)

        self._edit_upload_pass = LineEdit(self)
        self._edit_upload_pass.setText(cfg.get("upload_pass", ""))
        self._edit_upload_pass.setEchoMode(LineEdit.EchoMode.Password)
        self._edit_upload_pass.setPlaceholderText("上传密码")
        form.addRow("上传密码:", self._edit_upload_pass)

        self.main_layout.addLayout(form)

    # ---------- FRPC 服务器 ----------
    def _build_frpc_section(self, cfg):
        self._add_section_header("🔗 FRPC 穿透服务器")
        frpc = cfg.get("frpc_server", {})
        form = QFormLayout()
        form.setSpacing(8)

        self._edit_frpc_addr = LineEdit(self)
        self._edit_frpc_addr.setText(frpc.get("serverAddr", ""))
        self._edit_frpc_addr.setPlaceholderText("服务器 IP")
        form.addRow("服务器地址:", self._edit_frpc_addr)

        self._edit_frpc_port = LineEdit(self)
        self._edit_frpc_port.setText(str(frpc.get("serverPort", 7000)))
        self._edit_frpc_port.setPlaceholderText("端口号")
        form.addRow("服务器端口:", self._edit_frpc_port)

        self._edit_frpc_token = LineEdit(self)
        self._edit_frpc_token.setText(frpc.get("auth_token", ""))
        self._edit_frpc_token.setPlaceholderText("认证 Token")
        form.addRow("认证 Token:", self._edit_frpc_token)

        self.main_layout.addLayout(form)
        self._frpc_built = True

    # ---------- 外观 ----------
    def _build_appearance_section(self, cfg):
        self._add_section_header("🎨 外观")
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
        lbl = CaptionLabel(text, self)
        lbl.setStyleSheet("font-weight: bold; font-size: 13px; margin-top: 4px;")
        self.main_layout.addWidget(lbl)

    def _browse(self, edit, mode):
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
    """FRPC 配置回退值（未构建分区时使用）"""
    frpc = dict(cfg.get("frpc_server", {}) or {})
    return {
        "serverAddr": str(frpc.get("serverAddr", "") or ""),
        "serverPort": frpc.get("serverPort", 7000),
        "auth_method": frpc.get("auth_method", "token"),
        "auth_token": str(frpc.get("auth_token", "") or ""),
    }

