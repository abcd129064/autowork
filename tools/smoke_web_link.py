# -*- coding: utf-8 -*-
"""offscreen 冒烟：RecordsPage 页头"网页版"超链接（富文本 QLabel 实现）
- DB 隔离到临时 tmp 库（避免触碰真实 tables.db / 云 MySQL）
- 断言以 exit code 为准；stdout 用 flush（退出走 os._exit 防 offscreen 段错误）
"""
import os
import sys
import tempfile
import json

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TMP = tempfile.mkdtemp(prefix="aftersale_smoke_")

import database.table_db as table_db
table_db.DB_PATH = os.path.join(TMP, "tables.db")

from PySide6.QtWidgets import QApplication, QLabel
app = QApplication([])

from windows.aftersale.records import RecordsPage, _web_entry_url, _web_link_html

page = RecordsPage()
page.resize(1280, 800)
page.show()
app.processEvents()

ok = True
def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("  [" + str(extra) + "]" if extra else ""), flush=True)
    ok = ok and cond

url = _web_entry_url()
check("URL 默认指向本地 8787", url == "http://localhost:8787/", url)

btn = getattr(page, "_btn_web", None)
check("页头存在超链接标签", isinstance(btn, QLabel))
ol = btn.openExternalLinks()
check("点击由系统浏览器打开", bool(ol) if callable(ol) else bool(ol))
txt = btn.text()
check("链接 href 指向 localhost:8787", 'href="http://localhost:8787"' in txt, txt[:90])
check("显示文本=localhost:8787", ">localhost:8787</a>" in txt, txt[:90])

# 几何：链接在数据源标签左侧，且与标签垂直中心对齐（同字体族应≈0）
lbl = page._lbl_source
br = btn.rect().translated(btn.mapTo(page, btn.rect().topLeft()))
lr = lbl.rect().translated(lbl.mapTo(page, lbl.rect().topLeft()))
check("链接位于数据源标签左侧", br.left() < lr.left(), f"btn.x={br.left()} lbl.x={lr.left()}")
diff = abs(br.center().y() - lr.center().y())
check("文字垂直对齐（中心差<3px）", diff < 3, f"btn.cy={br.center().y()} lbl.cy={lr.center().y()} btnH={br.height()} lblH={lr.height()}")
check("与标签同高（无裁切风险）", abs(br.height() - lr.height()) <= 1, f"btnH={br.height()} lblH={lr.height()}")

# settings 覆盖：enabled=false → 回退线上
cfg_path = os.path.join(os.getcwd(), "settings.json")
with open(cfg_path, "r", encoding="utf-8") as f:
    real_cfg = json.load(f)
fake = dict(real_cfg)
fake["local_web"] = {"enabled": False}
import unittest.mock as mock
import io
def patched(file, *a, **kw):
    if str(file).endswith("settings.json"):
        return io.StringIO(json.dumps(fake))
    return open(file, *a, **kw)
with mock.patch("builtins.open", patched):
    url2 = _web_entry_url()
    html2 = _web_link_html()
check("disabled 时回退线上", url2 == "http://49.235.34.253/" and "49.235.34.253" in html2, html2[:90])

print("SMOKE_" + ("OK" if ok else "FAILED"), flush=True)
os._exit(0 if ok else 1)
