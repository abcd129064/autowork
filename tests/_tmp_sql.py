# -*- coding: utf-8 -*-
"""临时：验证主设置 SQL 同步分区（构建/收集/加密）"""
import sys
from PySide6.QtWidgets import QApplication, QWidget
from main_window.settings_dialog import SettingsDialog

app = QApplication.instance() or QApplication(sys.argv)
host = QWidget()
host.resize(800, 600)
host.show()

from windows.mysql_sync_card import _load_settings
cfg = {"mysql_sync": _load_settings().get("mysql_sync", {}) or {}}
dlg = SettingsDialog(host, cfg)

# 分区存在
keys = [k for k, _ in dlg._SECTIONS]
assert "sql" in keys, keys
print("分区顺序:", keys)

# 切入 sql 分区构建
dlg._switch_section("sql")
assert dlg._sql_built
card = dlg._mysql_card
# load 回显（读磁盘配置）
print("磁盘 mysql_sync:", cfg["mysql_sync"])
assert card._edit_host.text() == str(cfg["mysql_sync"].get("host", ""))
print("卡片回显 OK")

# 修改表单
card._edit_host.setText("192.168.1.10")
card._edit_pass.setText("new_secret")
card._switch_enabled.setChecked(False)

# collect 收集
data = dlg.collect()
ms = data.get("mysql_sync", {})
assert ms["host"] == "192.168.1.10", ms
assert ms["password"] == "new_secret"
assert ms["enabled"] is False
assert ms["port"] == 3306 and ms["user"] == "root"
print("collect OK:", ms)

# 未构建分区时回退原始配置
dlg2 = SettingsDialog(host, cfg)
data2 = dlg2.collect()
assert data2["mysql_sync"] == cfg["mysql_sync"]
print("未构建回退 OK")

# 密码加密落盘验证（encrypt_settings 对 mysql_sync.password 加密）
from core.secrets import encrypt_settings, decrypt_settings
enc = encrypt_settings({"mysql_sync": {"password": "new_secret"}})
assert enc["mysql_sync"]["password"] != "new_secret", enc
dec = decrypt_settings(enc)
assert dec["mysql_sync"]["password"] == "new_secret"
print("密码加密 OK")

print("ALL OK")
