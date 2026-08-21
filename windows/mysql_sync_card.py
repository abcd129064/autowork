# -*- coding: utf-8 -*-
"""MySQL 远程同步配置卡片（运维面板 / 售后面板复用）

自包含卡片：连接表单 + 启用/自动同步开关 + 测试/同步/保存按钮，
配置统一读写 settings.json 的 mysql_sync 节点（敏感字段 DPAPI 加密）。

设计要点（修复「测试通过但同步报 password NO」的保存顺序问题）：
- 测试连接、立即同步都直接使用表单当前配置（异步 Worker 传入 cfg），
  不依赖 settings.json 是否已保存——测试通过后无需先点保存即可同步；
- 点「立即同步」前先把表单配置落盘（enabled 强制为 True，同步动作本身
  就隐含启用），使数据层（table_db 双后端路由）同步后立即切换到 MySQL；
- 保存配置始终以磁盘最新内容为 base 合并，避免旧缓存整体回写覆盖
  其他模块中途写入的新数据（upload_pass 等）。

信号：
- saved(dict): 「保存配置」成功后发射，携带落盘的 mysql_sync 配置
"""

import json
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QFormLayout,
                               QLabel)
from qfluentwidgets import (CardWidget, BodyLabel, CaptionLabel, LineEdit,
                            PasswordLineEdit, SwitchButton, PushButton,
                            PrimaryPushButton, FluentIcon)

from core.app_paths import get_app_dir
from core.secrets import decrypt_settings, encrypt_settings
from core.utils import show_info_bar
from workers.mysql_sync_worker import MysqlSyncWorker, MysqlTestWorker


def _settings_path() -> str:
    """settings.json 绝对路径（与主程序一致）"""
    return os.path.join(get_app_dir(), "settings.json")


def read_settings() -> dict:
    """读取 settings.json（敏感字段透明解密），失败返回空 dict"""
    try:
        with open(_settings_path(), "r", encoding="utf-8") as f:
            return decrypt_settings(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_mysql_cfg(cfg: dict):
    """把 mysql_sync 配置合并写入 settings.json（敏感字段加密落盘）

    以磁盘最新内容为 base 再合并本次节点，避免用启动时旧缓存整体
    回写覆盖其他模块中途保存的数据；写入后 mtime 变化，各模块的
    mtime 缓存自动失效重新读取。
    """
    settings = read_settings()
    settings["mysql_sync"] = cfg
    with open(_settings_path(), "w", encoding="utf-8") as f:
        json.dump(encrypt_settings(settings), f, ensure_ascii=False, indent=2)


class MysqlSyncCard(CardWidget):
    """MySQL 远程同步配置卡片：连接信息 + 开关 + 测试/同步/保存按钮

    继承 CardWidget 保持与运维面板其他配置卡片一致的圆角卡片外观。

    Args:
        sync_scope: 同步范围 —— "ops"（默认）推 5 张运维业务表；
            "aftersale" 推售后记录（业务键去重 upsert，多用户共享库安全）
    """

    saved = Signal(dict)

    def __init__(self, parent=None, sync_scope: str = "ops"):
        super().__init__(parent)
        self._scope = sync_scope if sync_scope == "aftersale" else "ops"
        self._test_worker = None
        self._sync_worker = None
        self._init_ui()

    def _init_ui(self):
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(8)
        vbox.addWidget(BodyLabel("MySQL 远程同步（测试）", self))
        vbox.addWidget(CaptionLabel(
            "开启后 MySQL 完全替代本地 SQLite，应用直接读写远程数据库；关闭则回到本地 SQLite", self))

        # 开关行
        sw_row = QHBoxLayout()
        sw_row.setSpacing(16)
        sw_row.addWidget(QLabel("启用（测试）:", self))
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
        self._btn_sync.setToolTip(self._sync_tooltip())
        self._btn_sync.clicked.connect(self._on_sync)
        btn_row.addWidget(self._btn_sync)
        self._btn_save = PushButton(FluentIcon.SAVE, "保存配置", self)
        self._btn_save.setToolTip("将当前表单写入 settings.json（密码加密存储），保存后即时生效")
        self._btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self._btn_save)
        vbox.addLayout(btn_row)
        # 点击不获焦：禁用/运行中不会转移焦点到下方控件，避免 ScrollArea 自动滚动画面下移
        for btn in (self._btn_test, self._btn_sync, self._btn_save):
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def _sync_tooltip(self) -> str:
        """「立即同步」按钮提示：按同步范围说明推送内容与去重策略"""
        if self._scope == "aftersale":
            return ("将本地 SQLite 的售后记录推送到远程 MySQL"
                    "（按填写时间/填写人/桌号/问题去重，本地优先），并自动保存配置生效")
        return "将本地 SQLite 的 5 张运维数据表推送到远程 MySQL，并自动保存配置生效"

    # ---------- 配置读写 ----------

    def load(self, cfg: dict):
        """用配置 dict 填充表单（cfg 缺省时从 settings.json 读取）"""
        if cfg is None:
            cfg = read_settings().get("mysql_sync", {})
        self._edit_host.setText(str(cfg.get("host", "")))
        self._edit_port.setText(str(cfg.get("port", 3306)))
        self._edit_user.setText(str(cfg.get("user", "root")))
        self._edit_pass.setText(str(cfg.get("password", "")))
        self._edit_db.setText(str(cfg.get("database", "autowork")))
        self._switch_enabled.setChecked(bool(cfg.get("enabled", False)))
        self._switch_auto.setChecked(bool(cfg.get("auto_sync", False)))

    def load_settings(self):
        """从 settings.json 读取 mysql_sync 配置填充表单"""
        self.load(read_settings().get("mysql_sync", {}))

    def collect_cfg(self, enabled: bool = None) -> dict:
        """收集表单为配置 dict；enabled 传值可覆盖开关（同步动作隐含启用）"""
        try:
            port = int(self._edit_port.text().strip() or 3306)
        except ValueError:
            port = 3306
        return {
            "enabled": self._switch_enabled.isChecked() if enabled is None else bool(enabled),
            "host": self._edit_host.text().strip(),
            "port": port,
            "user": self._edit_user.text().strip() or "root",
            "password": self._edit_pass.text(),
            "database": self._edit_db.text().strip() or "autowork",
            "auto_sync": self._switch_auto.isChecked(),
        }

    # ---------- 测试连接 ----------

    def _on_test(self):
        """测试 MySQL 连接：用当前表单配置异步测试（不落盘）"""
        if self._test_worker and self._test_worker.isRunning():
            show_info_bar("已有测试进行中，请稍候", "warning",
                          title="提示", parent=self, duration=2000)
            return
        self._btn_test.setEnabled(False)
        cfg = self.collect_cfg(enabled=True)
        self._test_worker = MysqlTestWorker(cfg, self)
        self._test_worker.finished.connect(self._on_test_done)
        self._test_worker.start()

    def _on_test_done(self, ok, msg):
        """测试完成：恢复按钮并按成败提示"""
        self._btn_test.setEnabled(True)
        if ok:
            show_info_bar(msg, "success", title="MySQL 连接成功",
                          parent=self, duration=2500)
        else:
            show_info_bar(msg, "error", title="MySQL 连接失败",
                          parent=self, duration=4000)

    # ---------- 立即同步 ----------

    def _on_sync(self):
        """手动触发全量推送：先落盘表单配置（enabled 隐含启用），再异步推送

        落盘目的：同步动作本身要求 MySQL 已启用（_load_mysql_config 校验），
        且让数据层双后端路由同步后立即切换；这样「测试通过 → 直接同步」
        不再依赖用户先点保存设置。
        """
        if self._sync_worker and self._sync_worker.isRunning():
            show_info_bar("同步进行中，请稍候", "warning",
                          title="提示", parent=self, duration=2000)
            return
        cfg = self.collect_cfg(enabled=True)
        try:
            save_mysql_cfg(cfg)
        except Exception as e:
            show_info_bar(str(e), "error", title="保存配置失败",
                          parent=self, duration=4000)
            return
        self._btn_sync.setEnabled(False)
        # 售后面板只推售后记录（业务键去重），运维面板推全部 5 张业务表
        table = "aftersale_records" if self._scope == "aftersale" else None
        self._sync_worker = MysqlSyncWorker(table_name=table, cfg=cfg, parent=self)
        self._sync_worker.progress.connect(
            lambda msg: self._btn_sync.setToolTip(msg))
        self._sync_worker.success.connect(self._on_sync_done_ok)
        self._sync_worker.error.connect(self._on_sync_done_err)
        self._sync_worker.start()

    def _on_sync_done_ok(self, count, msg):
        """同步成功：恢复按钮并提示"""
        self._btn_sync.setEnabled(True)
        self._btn_sync.setToolTip(self._sync_tooltip())
        show_info_bar(msg, "success", title="MySQL 同步完成",
                      parent=self, duration=3000)

    def _on_sync_done_err(self, msg):
        """同步失败：恢复按钮并提示"""
        self._btn_sync.setEnabled(True)
        self._btn_sync.setToolTip(self._sync_tooltip())
        show_info_bar(msg, "error", title="MySQL 同步失败",
                      parent=self, duration=4000)

    # ---------- 保存配置 ----------

    def _on_save(self):
        """把当前表单写入 settings.json，即时生效（密码加密落盘）"""
        cfg = self.collect_cfg()
        try:
            save_mysql_cfg(cfg)
        except Exception as e:
            show_info_bar(str(e), "error", title="保存失败",
                          parent=self, duration=4000)
            return
        show_info_bar("MySQL 配置已写入 settings.json，即时生效", "success",
                      title="已保存", parent=self, duration=2500)
        self.saved.emit(cfg)
