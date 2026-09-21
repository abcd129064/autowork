# -*- coding: utf-8 -*-
"""逆向 xqzg 前端 JS：定位网页端 todesk 开关的完整调用链

对比桌面端（我们只调 value/）与网页端的差异——用户实测网页端开关后
服务端状态能正确刷新，怀疑网页端还有额外调用（状态查询/刷新触发）。
"""
import json
import os
import re
import sys

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

with open(os.path.join(ROOT, "config", "credentials.json"), encoding="utf-8") as f:
    creds = json.load(f)
from core.secrets import decrypt_settings  # noqa: E402
api1 = decrypt_settings(creds)["api_credentials"]["api1"]

BASE = "https://xqzg.newbv.cn"
s = requests.Session()
s.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
r = s.post(f"{BASE}/api/rbac/auth/login/",
           json={"username": api1["username"], "password": api1["password"]},
           timeout=15)
print(f"[LOGIN] {r.status_code}")

# 1) 首页 HTML → JS bundle 列表
r = s.get(f"{BASE}/", timeout=20)
html = r.text
scripts = re.findall(r'src="([^"]+\.js[^"]*)"', html)
print(f"[HTML] scripts={len(scripts)}")
for sc in scripts[:20]:
    print("  ", sc)

# 2) 下载全部 bundle，grep todesk / value / status 相关调用上下文
os.makedirs(os.path.join(ROOT, "tools", "_scratch", "xqzg_js"), exist_ok=True)
out_dir = os.path.join(ROOT, "tools", "_scratch", "xqzg_js")
keyword_res = [
    re.compile(r"todesk", re.I),
    re.compile(r"/value"),
    re.compile(r"to_desk"),
    re.compile(r"refresh", re.I),
]
for sc in scripts:
    url = sc if sc.startswith("http") else f"{BASE}/{sc.lstrip('/')}"
    name = re.sub(r"[^\w.-]", "_", sc.split("/")[-1])[:80] or "index.js"
    path = os.path.join(out_dir, name)
    try:
        resp = s.get(url, timeout=30)
        body = resp.text
    except Exception as e:
        print(f"  [SKIP] {name}: {e}")
        continue
    with open(path, "w", encoding="utf-8", errors="replace") as f:
        f.write(body)
    hits = []
    for kw in keyword_res:
        for m in kw.finditer(body):
            start = max(0, m.start() - 120)
            end = min(len(body), m.end() + 180)
            ctx = body[start:end].replace("\n", " ")
            hits.append((kw.pattern, ctx))
    if hits:
        print(f"\n[HIT] {name} ({len(body)//1024}KB) -> {path}")
        seen = set()
        shown = 0
        for pat, ctx in hits:
            key = ctx[:80]
            if key in seen:
                continue
            seen.add(key)
            print(f"  [{pat}] ...{ctx}...")
            shown += 1
            if shown >= 12:
                break

print("\nDONE - bundles saved to tools/_scratch/xqzg_js/")
