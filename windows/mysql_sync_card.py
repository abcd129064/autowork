# -*- coding: utf-8 -*-
"""MySQL 配置卡片（可复用组件：运维面板 / 售后面板共用）

- 连接表单 + 启用开关 + 测试连接/保存配置按钮
- sync_scope="ops" 运维业务数据；sync_scope="aftersale" 售后记录
- 配置读写：DPAPI 加密落盘；以磁盘最新内容为 base 合并，避免双缓存覆盖
- 镜像推送（自动同步/立即同步）已随机制 B 下线：仅保留连接测试与配置保存
"""

import json
import os

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
                               QLabel)
from qfluentwidgets import (CardWidget, BodyLabel, CaptionLabel, LineEdit,
                            PasswordLineEdit, SwitchButton, PushButton,
                            PrimaryPushButton, FluentIcon)

from core.app_paths import get_app_dir
from core.secrets import decrypt_settings, encrypt_settings
from core.utils import show_info_bar
from database import backend
from database.mysql_sync_card_logic import should_attempt_test
from workers.mysql_sync_worker import MysqlTestWorker


def _settings_path() -> str:
    """settings.json 绝对路径"""
    return os.path.join(get_app_dir(), "settings.json")


def _load_settings() -> dict:
    """读取 settings.json（敏感字段透明解密）；缺失/损坏返回 {}"""
    try:
        with open(_settings_path(), "r", encoding="utf-8") as f:
            return decrypt_settings(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_settings(data: dict):
    """合并写 settings.json：以磁盘最新内容为 base（防双缓存覆盖 bug）

    先重新读盘再合并，避免内存缓存过期把其他页面的新配置覆盖掉。
    """
    settings = _load_settings()
    settings.update(data)
    with open(_settings_path(), "w", encoding="utf-8") as f:
        json.dump(encrypt_settings(settings), f, ensure_ascii=False, indent=2)


class MysqlSyncCard(CardWidget):
    """MySQL 同步配置卡片（运维/售后/跑视频面板共用）

    Args:
        sync_scope: "ops" 推 5 张运维业务表；"aftersale" 只推售后记录；
            "ledger" 只推跑视频记录（仅影响说明文案，开关/连接配置共用）
    """

    def __init__(self, parent=None, sync_scope: str = "ops"):
        super().__init__(parent)
        self.sync_scope = sync_scope
        self._test_worker = None
        self._init_ui()

    # ---------- UI ----------

    def _init_ui(self):
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(8)
        # 同步范围文案：ops 运维业务数据；aftersale 售后记录；ledger 跑视频记录
        _SCOPE_NAMES = {
            "ops": "运维业务数据",
            "aftersale": "售后记录",
            "ledger": "跑视频记录",
        }
        scope_name = _SCOPE_NAMES.get(self.sync_scope, "运维业务数据")
        card_title = "数据库设置" if self.sync_scope != "ops" else "服务器SQL 同步"
        vbox.addWidget(BodyLabel(card_title, self))
        vbox.addWidget(CaptionLabel(
            f"开启后 服务器SQL 完全替代本地 SQLite，应用实时读写远程数据库；"
            f"关闭则回到本地 SQLite。同步范围：{scope_name}", self))

        # 开关行
        sw_row = QHBoxLayout()
        sw_row.setSpacing(16)
        sw_row.addWidget(QLabel("启用:", self))
        self._switch_enabled = SwitchButton(self)
        self._switch_enabled.setOnText("开")
        self._switch_enabled.setOffText("关")
        sw_row.addWidget(self._switch_enabled)
        sw_row.addStretch(1)
        vbox.addLayout(sw_row)

        # 连接表单
        form = QFormLayout()
        form.setSpacing(8)
        self._edit_host = LineEdit(self)
        self._edit_host.setPlaceholderText("远程SQL 服务器 IP")
        form.addRow("服务器地址:", self._edit_host)
        self._edit_port = LineEdit(self)
        self._edit_port.setPlaceholderText("端口号（默认 3306）")
        form.addRow("端口:", self._edit_port)
        self._edit_user = LineEdit(self)
        self._edit_user.setPlaceholderText("用户名（默认 root）")
        form.addRow("用户名:", self._edit_user)
        self._edit_pass = PasswordLineEdit(self)
        self._edit_pass.setPlaceholderText("密码")
        form.addRow("密码:", self._edit_pass)
        self._edit_db = LineEdit(self)
        self._edit_db.setPlaceholderText("数据库名")
        form.addRow("数据库:", self._edit_db)
        vbox.addLayout(form)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_test = PushButton(FluentIcon.LINK, "测试连接", self)
        self._btn_test.setToolTip("用当前表单配置尝试连接远程SQL")
        self._btn_test.clicked.connect(self._on_test)
        btn_row.addWidget(self._btn_test)
        self._btn_save = PushButton(FluentIcon.SAVE, "保存配置", self)
        self._btn_save.setToolTip("写入 settings.json 即时生效")
        self._btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self._btn_save)
        vbox.addLayout(btn_row)

    # ---------- 配置读写 ----------

    def load(self):
        """从 settings.json 加载当前配置填充表单（进入页面/保存后调用）"""
        cfg = _load_settings().get("mysql_sync", {})
        self._edit_host.setText(str(cfg.get("host", "")))
        self._edit_port.setText(str(cfg.get("port", 3306)))
        self._edit_user.setText(str(cfg.get("user", "root")))
        self._edit_pass.setText(str(cfg.get("password", "")))
        self._edit_db.setText(str(cfg.get("database", "autowork")))
        self._switch_enabled.setChecked(bool(cfg.get("enabled", False)))

    def _collect_cfg(self, enabled: bool = None) -> dict:
        """收集表单配置为 dict（端口非法时兜底 3306）"""
        try:
            port = int(self._edit_port.text().strip() or 3306)
        except ValueError:
            port = 3306
        return {
            "enabled": self._switch_enabled.isChecked()
            if enabled is None else enabled,
            "host": self._edit_host.text().strip(),
            "port": port,
            "user": self._edit_user.text().strip() or "root",
            "password": self._edit_pass.text(),
            "database": self._edit_db.text().strip() or "autowork",
        }

    def _on_save(self):
        """保存配置：合并写 settings.json 并提示

        实时主库模式下写入直接进 MySQL，本地无新增，「立即同步」/自动
        推送已随镜像推送机制 B 下线；保存只负责写配置并让 backend 各线程
        按新配置重建连接。
        """
        try:
            cfg = self._collect_cfg()
            _save_settings({"mysql_sync": cfg})
            # backend 的开关/凭据走进程缓存；保存成功后让各线程在下一次
            # 数据库访问时按新配置重建连接，避免继续使用旧 host/账号。
            backend.invalidate_mysql_settings_cache()
            if cfg["enabled"]:
                hint = "已启用 远程SQL，将直接读写服务器SQL"
            else:
                hint = "已关闭 远程SQL，将使用本地SQLite"
            show_info_bar(f"配置已写入 settings.json，即时生效；{hint}",
                          "success",
                          title="已保存", parent=self, duration=3000)
        except Exception as e:
            show_info_bar(str(e), "error",
                          title="保存失败", parent=self, duration=4000)

    # ---------- 测试连接 ----------

    def _on_test(self):
        """用当前表单配置异步测试连接（进行中不重复发起）"""
        if self._test_worker and self._test_worker.isRunning():
            show_info_bar("已有测试进行中，请稍候", "warning",
                          title="提示", parent=self, duration=2000)
            return
        # 入口防御：未启用 MySQL 或密码为空时直接提示当前为本地 SQLite，
        # 避免调用 MysqlTestWorker 产生误导性"MySQL 连接成功/失败"提示
        form_cfg = self._collect_cfg()
        can, hint = should_attempt_test(form_cfg)
        if not can:
            msg_type = "info" if "本地 SQLite" in hint else "warning"
            show_info_bar(hint, msg_type, title="提示",
                          parent=self, duration=2500)
            return
        self._btn_test.setEnabled(False)
        self._test_worker = MysqlTestWorker(form_cfg, self)
        self._test_worker.finished.connect(self._on_test_done)
        self._test_worker.start()

    def _on_test_done(self, ok, msg):
        self._btn_test.setEnabled(True)
        if ok:
            show_info_bar(msg, "success", title="MySQL 连接成功",
                          parent=self, duration=2500)
        else:
            show_info_bar(msg, "error", title="MySQL 连接失败",
                          parent=self, duration=4000)
