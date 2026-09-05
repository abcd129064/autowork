# -*- coding: utf-8 -*-
"""offscreen 冒烟：RecordsPage 页头 HyperlinkButton（网页版入口）
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

from PySide6.QtWidgets import QApplication
app = QApplication([])

from windows.aftersale.records import RecordsPage, _web_entry_url

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
check("页头存在 HyperlinkButton", btn is not None)
check("按钮文本=localhost:8787", btn is not None and btn.text() == "localhost:8787",
      btn.text() if btn is not None else "")
u = (btn.url.toString() if isinstance(getattr(btn, "url", None), object) and not callable(btn.url) else btn.url().toString())
check("按钮 URL 正确", u == url, u)

# 几何：按钮在数据源标签左侧，且文字垂直对齐（中心差 < 5px）
lbl = page._lbl_source
br, lr = btn.rect().translated(btn.mapTo(page, btn.rect().topLeft())), lbl.rect().translated(lbl.mapTo(page, lbl.rect().topLeft()))
bx = br.left()
lx = lr.left()
check("按钮位于数据源标签左侧", bx < lx, f"btn.x={bx} lbl.x={lx}")
bcy, lcy = br.center().y(), lr.center().y()
check("文字垂直对齐（中心差<5px）", abs(bcy - lcy) < 5, f"btn.cy={bcy} lbl.cy={lcy} btnH={br.height()} lblH={lr.height()}")

# settings 覆盖：enabled=false → 回退线上
cfg_path = os.path.join(os.getcwd(), "settings.json")
with open(cfg_path, "r", encoding="utf-8") as f:
    real_cfg = json.load(f)
fake = dict(real_cfg)
fake["local_web"] = {"enabled": False}
import unittest.mock as mock
with mock.patch("builtins.open", mock.mock_open(read_data=json.dumps(fake))):
    # _web_entry_url 用 get_app_dir()+settings.json，mock open 只在路径匹配时生效
    import builtins
    real_open = builtins.open
    def patched(file, *a, **kw):
        if str(file).endswith("settings.json"):
            import io
            return io.StringIO(json.dumps(fake))
        return real_open(file, *a, **kw)
    with mock.patch("builtins.open", patched):
        url2 = _web_entry_url()
check("disabled 时回退线上", url2 == "http://49.235.34.253/", url2)

print("SMOKE_" + ("OK" if ok else "FAILED"), flush=True)
os._exit(0 if ok else 1)
