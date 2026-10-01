# -*- coding: utf-8 -*-
"""球桌管理搜索支持「版本号」offscreen 冒烟（2026-10-04）

临时库造数 → 真实 _ensure_initialized（含 FTS5 建索引）→ 构建 TablePage →
在搜索框输入 200070 / 20058 / 200（短词回退 LIKE）→ 断言表格行与版本号列 →
截图到 tools/_scratch/。

运行（唯一全依赖解释器）：
    QT_QPA_PLATFORM=offscreen python tools/smoke/smoke_table_search.py
"""
import os
import sqlite3
import sys
import tempfile
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# ---- Qt DLL 引导（同 windows/aftersale_panel.py 顶部，规避 conda Qt 冲突） ----
import importlib.util as _iu
try:
    _spec = _iu.find_spec('PySide6')
    if _spec is not None:
        for _d in (list(_spec.submodule_search_locations or []) +
                   [os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                                 'System32')]):
            if _d and os.path.isdir(_d):
                try:
                    os.add_dll_directory(_d)
                except OSError:
                    pass
except Exception:
    pass

import database.backend as backend
import database.table_db as table_db

# ---- 临时库造数（含版本号：用户实报搜索词 200070 / 20058） ----
tmp = tempfile.mkdtemp(prefix="smoke_tsearch_")
dbp = os.path.join(tmp, "t.db")
table_db.DB_PATH = dbp
table_db._initialized = False
table_db._fts_available = False
backend.is_mysql_test_mode = lambda: False

_ROWS = (
    ("A01", "球房甲", "200070-20061-100836", "0"),
    ("B02", "球房乙", "20058-10001-100002", "0"),
    ("C03", "球房丙", "100000-10000-100000", "0"),
    ("D04", "球房丁", "200070-77777-000003", "0"),   # 含 200070 的第三台
    ("E05", "球房戊", "200070-88888-000004", "2"),   # 退单：默认不显示
    ("F06", "公司测试", "200070-99999-000005", "0"),  # 测试球房：默认不显示
)

conn = table_db._get_conn()          # 触发真实初始化（建表 + FTS5 + 触发器）
assert table_db._fts_available is True, "本机 SQLite 应支持 fts5 trigram"
conn.executemany(
    "INSERT INTO billiard_tables (name, roomName, deviceVersion, status) "
    "VALUES (?, ?, ?, ?)", _ROWS)
conn.commit()

# ---- 构建页面（stub frps 感知，避免冒烟触碰生产 frps 管理接口） ----
from unittest.mock import MagicMock                       # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase            # noqa: E402
from PySide6.QtWidgets import QApplication                # noqa: E402
from windows.management import table_page as tp           # noqa: E402
from windows.management.common import TABLE_COLUMNS       # noqa: E402

tp.get_frps_client = lambda: MagicMock()

app = QApplication([])

# venv 的 PySide6 不带 fonts 目录（QFontDatabase 报 cannot find font directory），
# 不注册系统中文字体时截图里中文全是方框，无法作为验收证据。
for _f in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyh.ttf",
           r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc"):
    if os.path.exists(_f):
        QFontDatabase.addApplicationFont(_f)
app.setFont(QFont("Microsoft YaHei", 9))

page = tp.TablePage()
page.resize(1680, 900)
page.show()

_VER_COL = [i for i, c in enumerate(TABLE_COLUMNS) if c[0] == "deviceVersion"][0]


def _pump(cond, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def _names():
    return sorted(page._table.item(r, 0).text()
                  for r in range(page._table.rowCount()))


def _search(kw, expect_names):
    """输入关键词，等表格内容真正变为期望集合（不能只等行数：行数相同会误判旧结果）"""
    page._search_edit.setText(kw)
    ok = _pump(lambda: _names() == sorted(expect_names)
               and page._total == len(expect_names))
    names = _names()
    versions = sorted(page._table.item(r, _VER_COL).text()
                      for r in range(page._table.rowCount()))
    assert ok, (f"搜索 {kw!r} 期望 {sorted(expect_names)}，实得 "
                f"{names} rowCount={page._table.rowCount()} total={page._total}")
    print(f"  搜索 {kw!r} → {page._table.rowCount()} 行 {names} 版本号={versions}")
    return names, versions


# 初始全量（默认隐藏测试球房/退单设备 → 4 台）
assert _pump(lambda: page._total == 4), (
    f"初始加载异常：total={page._total}")
print(f"初始列表: {page._table.rowCount()} 行（共 {page._total} 条，"
      f"已排除测试球房/退单/手动设备）")

print("搜索断言：")
names, versions = _search("200070", ["A01", "D04"])
assert versions == ["200070-20061-100836", "200070-77777-000003"], versions

names, _ = _search("20058", ["B02"])

names, _ = _search("200", ["A01", "B02", "D04"])   # < 3 字符 → LIKE 回退路径

names, _ = _search("100000", ["C03"])

names, _ = _search("球房甲", ["A01"])              # 原有字段搜索未回归

names, _ = _search("不存在版本号X", [])

out = os.path.join(PROJECT_ROOT, "tools", "_scratch", "smoke_table_search.png")
_search("200070", ["A01", "D04"])
page.grab().save(out)
print("SMOKE_OK",
      f"ver_col={_VER_COL}",
      "cases=200070/20058/200/100000/球房甲/无命中",
      f"fts={table_db._fts_available}",
      f"shot={out}")
