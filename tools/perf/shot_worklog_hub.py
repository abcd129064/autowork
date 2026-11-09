# -*- coding: utf-8 -*-
"""工作留痕面板 offscreen 截图（五页签视觉留档）

用法：
  C:\\Users\\shen_zhe\\miniconda3\\python.exe tools/perf/shot_worklog_hub.py
产物：tools/_scratch/shots/shot_worklog_<tab>.png（验证产物，已 gitignore）

为什么不走 `shot_fluent_mainwindow.py` 那条真库链路：worklog 两张表是懒建的
（`table_db._CREATE_WORKLOG_SQL` 在首次取连接时执行），真库截屏会把 DDL 写进
`database/tables.db`——那是 AGENTS.md §3.1 的禁改文件，也会破掉 §7(5)
「跑完 config/ 与 database/ 零改动」这道闸。这里跟单测/冒烟同一套隔离：
临时目录 + 独立 SQLite + 关掉 MySQL 主库模式，种子数据让五个页签都有得填。
"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# tools/perf/ → 上溯三层 = 仓库根
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ---- Qt 内联引导（必须在 import PySide6 之前，见 docs/Qt内联引导说明.md）----
import importlib.util

_spec = importlib.util.find_spec("PySide6")
_locs = list(getattr(_spec, "submodule_search_locations", None) or [])
if _locs:
    _pkg = _locs[0]
    for _d in (_pkg, os.path.dirname(_pkg),
               os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")):
        if os.path.isdir(_d):
            try:
                os.add_dll_directory(_d)
            except OSError:
                pass
    os.environ["QT_PLUGIN_PATH"] = os.path.join(_pkg, "plugins")
    os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH",
                          os.path.join(_pkg, "plugins", "platforms"))

import sqlite3  # noqa: E402
import tempfile  # noqa: E402

TMP = tempfile.mkdtemp(prefix="wl_shot_")
import core.app_paths
core.app_paths.get_app_dir = lambda: TMP
import database.backend as backend
backend.is_mysql_test_mode = lambda: False
import database.table_db as table_db
from database import schema

DB_PATH = os.path.join(TMP, "t.db")
table_db.DB_PATH = DB_PATH
table_db._conn = None
_local = sqlite3.connect(DB_PATH)
for _t in schema.WORKLOG_TABLES:
    _local.executescript(schema.to_sqlite_ddl(_t))
_local.commit()
_local.close()

from PySide6.QtCore import QDate, QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from qfluentwidgets import Theme, setTheme, setThemeColor  # noqa: E402

app = QApplication(list(sys.argv))
setTheme(Theme.LIGHT)
setThemeColor("#00BCD4", lazy=True)

import core.acrylic_patch  # noqa: E402,F401
from database import worklog_db  # noqa: E402


def pump(ms):
    """真等事件循环：页签取数在 QThread 里，processEvents 收不到跨线程信号"""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def seed():
    """三成员 × 三周的记录：够填满分组带、时间轴、导图三级与统计两图"""
    today = QDate.currentDate()
    titles = ("售后排名接口联调", "query_page 分页性能优化",
              "frp 隧道巡检与自愈验证", "自动更新 SOP 文档整理",
              "设备状态图片预览接入", "日志高亮规则系统化",
              "MySQL 兜底降级评审", "球桌在线状态筛选")
    mids = [worklog_db.insert_member(name)
            for name in ("张工", "李工", "王工")]
    n = 0
    for day in range(21):
        date = today.addDays(-day).toString("yyyy-MM-dd")
        for _ in range(2 if day % 3 else 1):
            n += 1
            worklog_db.insert_record(dict(
                work_date=date, member_id=mids[n % 3],
                title="%s（%s）" % (titles[n % len(titles)], date[5:]),
                project=("AutoWork", "球房运维", "")[n % 3],
                category=("开发", "运维", "文档", "测试")[n % 4],
                hours=round(0.5 + (n % 6) * 0.5, 1),
                progress=(n * 15) % 101,
                status="已完成" if n % 3 == 0 else "进行中"))
    return n


print("种子记录：%d 条" % seed())

from windows.worklog.hub import WorkLogHub  # noqa: E402

OUT = os.path.join(ROOT, "tools", "_scratch", "shots")
os.makedirs(OUT, exist_ok=True)

hub = WorkLogHub()
hub.resize(1500, 900)
hub.show()
pump(1200)

TABS = (("records", hub.records_page),
        ("timeline", hub.timeline_page),
        ("mindmap", hub.mindmap_page),
        ("stats", hub.stats_page),
        ("members", hub.members_page))

for name, page in TABS:
    hub.switchTo(page)
    # 取数一律显式触发：切页只跑一次 showEvent 首载，重跑才拿到当前种子数据
    refresh = getattr(page, "refresh", None)
    if refresh:
        refresh()
    pump(1800)
    path = os.path.join(OUT, "shot_worklog_%s.png" % name)
    pix = hub.grab()
    pix.save(path)
    print("  已保存 %s  (%dx%d)" % (path, pix.width(), pix.height()))

print("截图完成")
# os._exit 不走正常析构（避免 MainWindow 那套关窗副作用），缓冲区必须先冲掉，
# 否则上面几行 print 全丢
sys.stdout.flush()
os._exit(0)
