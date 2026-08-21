# -*- coding: utf-8 -*-
"""MySQL 同步配置卡片（可复用组件：运维面板 / 售后面板共用）

- 连接表单 + 启用/自动同步开关 + 测试连接/立即同步/保存配置按钮
- sync_scope="ops" 推 5 张业务表；sync_scope="aftersale" 只推售后记录
- 配置读写：DPAPI 加密落盘；以磁盘最新内容为 base 合并，避免双缓存覆盖
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
from workers.mysql_sync_worker import MysqlSyncWorker, MysqlTestWorker


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
    """MySQL 同步配置卡片（运维/售后面板共用）

    Args:
        sync_scope: "ops" 推 5 张运维业务表；"aftersale" 只推售后记录
    """

    def __init__(self, parent=None, sync_scope: str = "ops"):
        super().__init__(parent)
        self.sync_scope = sync_scope
        self._test_worker = None
        self._sync_worker = None
        self._init_ui()

    # ---------- UI ----------

    def _init_ui(self):
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(8)
        scope_name = "售后记录" if self.sync_scope == "aftersale" else "运维业务数据"
        card_title = "数据库设置（MySQL）" if self.sync_scope == "aftersale" else "MySQL 远程同步"
        vbox.addWidget(BodyLabel(card_title, self))
        vbox.addWidget(CaptionLabel(
            f"开启后 MySQL 完全替代本地 SQLite，应用直接读写远程数据库；"
            f"关闭则回到本地 SQLite。同步范围：{scope_name}", self))

        # 开关行
        sw_row = QHBoxLayout()
        sw_row.setSpacing(16)
        sw_row.addWidget(QLabel("启用:", self))
        self._switch_enabled = SwitchButton(self)
        self._switch_enabled.setOnText("开")
        self._switch_enabled.setOffText("关")
        sw_row.addWidget(self._switch_enabled)
        sw_row.addSpacing(20)
        sw_row.addWidget(QLabel("自动同步:", self))
        self._switch_auto = SwitchButton(self)
        self._switch_auto.setOnText("开")
        self._switch_auto.setOffText("关")
        self._switch_auto.setToolTip("每次 API 同步后自动推送到 MySQL")
        sw_row.addWidget(self._switch_auto)
        sw_row.addStretch(1)
        vbox.addLayout(sw_row)

        # 连接表单
        form = QFormLayout()
        form.setSpacing(8)
        self._edit_host = LineEdit(self)
        self._edit_host.setPlaceholderText("MySQL 服务器 IP")
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
        self._edit_db.setPlaceholderText("数据库名（默认 autowork）")
        form.addRow("数据库:", self._edit_db)
        vbox.addLayout(form)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_test = PushButton(FluentIcon.LINK, "测试连接", self)
        self._btn_test.setToolTip("用当前表单配置尝试连接 MySQL")
        self._btn_test.clicked.connect(self._on_test)
        btn_row.addWidget(self._btn_test)
        self._btn_sync = PrimaryPushButton(FluentIcon.SYNC, "立即同步", self)
        self._btn_sync.setToolTip(
            "将本地数据推送到远程 MySQL（自动先保存当前表单配置）")
        self._btn_sync.clicked.connect(self._on_sync)
        btn_row.addWidget(self._btn_sync)
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
        self._switch_auto.setChecked(bool(cfg.get("auto_sync", False)))

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
            "auto_sync": self._switch_auto.isChecked(),
        }

    def _on_save(self):
        """保存配置：合并写 settings.json 并提示"""
        try:
            _save_settings({"mysql_sync": self._collect_cfg()})
            show_info_bar("配置已写入 settings.json，即时生效", "success",
                          title="已保存", parent=self, duration=2500)
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
        self._btn_test.setEnabled(False)
        cfg = self._collect_cfg()
        cfg.pop("auto_sync", None)
        self._test_worker = MysqlTestWorker(cfg, self)
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

    # ---------- 立即同步 ----------

    def _on_sync(self):
        """立即同步：先用表单配置落盘（enabled 隐含 True），再异步推送"""
        if self._sync_worker and self._sync_worker.isRunning():
            show_info_bar("同步进行中，请稍候", "warning",
                          title="提示", parent=self, duration=2000)
            return
        # 保存顺序 bug 防护：同步前必须先落盘，否则远程读取的是旧配置
        try:
            _save_settings({"mysql_sync": self._collect_cfg(enabled=True)})
        except Exception as e:
            show_info_bar(str(e), "error",
                          title="配置保存失败", parent=self, duration=4000)
            return
        self._btn_sync.setEnabled(False)
        cfg = self._collect_cfg(enabled=True)
        cfg.pop("auto_sync", None)
        table_name = ("aftersale_records"
                      if self.sync_scope == "aftersale" else None)
        self._sync_worker = MysqlSyncWorker(table_name, self, cfg=cfg)
        self._sync_worker.progress.connect(
            lambda msg: self._btn_sync.setToolTip(msg))
        self._sync_worker.success.connect(self._on_sync_done)
        self._sync_worker.error.connect(self._on_sync_error)
        self._sync_worker.start()

    def _on_sync_done(self, _count, msg):
        self._btn_sync.setEnabled(True)
        self._btn_sync.setToolTip(
            "将本地数据推送到远程 MySQL（自动先保存当前表单配置）")
        show_info_bar(msg, "success", title="MySQL 同步完成",
                      parent=self, duration=3000)

    def _on_sync_error(self, msg):
        self._btn_sync.setEnabled(True)
        self._btn_sync.setToolTip(
            "将本地数据推送到远程 MySQL（自动先保存当前表单配置）")
        show_info_bar(msg, "error", title="MySQL 同步失败",
                      parent=self, duration=4000)
