# -*- coding: utf-8 -*-
"""S3 触发点 A 冒烟：周期设置保存 → 后台重算物化列 → 完成后才发 saved 信号

settings.json 重定向到临时目录（monkeypatch core.app_paths.get_app_dir），
不触碰真实配置。断言：saved 信号在重算 worker 完成之后发出、重算已把
物化列按新周期口径重写。
"""
import os, sys, sqlite3, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import importlib.util as _iu
_spec = _iu.find_spec('PySide6')
if _spec is not None:
    _pkg = list(getattr(_spec, 'submodule_search_locations', None) or [])[0]
    for _d in (_pkg, os.path.dirname(_pkg),
               os.path.join(os.environ.get('SystemRoot', r'C:/Windows'), 'System32')):
        if os.path.isdir(_d):
            try: os.add_dll_directory(_d)
            except OSError: pass
    os.environ['QT_PLUGIN_PATH'] = os.path.join(_pkg, 'plugins')

import tempfile
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop, QTimer
app = QApplication([])

import core.app_paths
_tmp = tempfile.mkdtemp()
_orig = core.app_paths.get_app_dir
core.app_paths.get_app_dir = lambda: _tmp

from database import backend, table_db, aftersale_db as adb
from database import schema

# 临时库 + tue 模式（settings.json 落在临时目录）
table_db.DB_PATH = os.path.join(_tmp, "t.db")
table_db._conn = None
backend.is_mysql_test_mode = lambda: False
sl = sqlite3.connect(table_db.DB_PATH)
sl.executescript(schema.to_sqlite_ddl("aftersale_records"))
sl.execute("INSERT INTO aftersale_records (created_at, occurred_at, creator, "
           "issue_type, table_no, room_name, region, problem, cycle_start) "
           "VALUES ('2026-08-20 10:00:00', '2026-08-19', 't', '硬件问题', "
           "'T1', 'r', '', 'p', '')")
sl.commit(); sl.close()
adb.save_cycle_mode({"type": "tue"})
adb.recalc_cycle_starts()  # tue 口径物化：2026/08/18

from windows.aftersale.settings import CycleSettingsPage
page = CycleSettingsPage()
page.show()
app.processEvents()

events = []
page.saved.connect(lambda: events.append("saved"))

# 切到自然月模式并保存
page._rb_month.setChecked(True)
page._on_save()
app.processEvents()
assert events == [], "saved 不应在重算完成前发出"

# 等待重算 worker 完成
deadline = time.time() + 15
while time.time() < deadline and not events:
    app.processEvents()
    time.sleep(0.01)
assert events == ["saved"], f"saved 信号异常: {events}"

# 验证：配置已保存（config/aftersale.json）+ 物化列已按 month 口径重写（2026/08/01）
cfg = json.load(open(os.path.join(_tmp, "config", "aftersale.json"),
                     encoding="utf-8"))
assert cfg["aftersale_cycle"]["type"] == "month"
sl = sqlite3.connect(table_db.DB_PATH)
vals = [r[0] for r in sl.execute("SELECT cycle_start FROM aftersale_records")]
sl.close()
assert vals == ["2026/08/01"], f"物化列未按 month 口径重算: {vals}"

core.app_paths.get_app_dir = _orig
print("PASS: 保存 → 后台重算 → 完成后才发 saved；物化列已按新周期重写")
sys.stdout.flush()
os._exit(0)
