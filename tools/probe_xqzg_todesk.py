# -*- coding: utf-8 -*-
"""深挖 todesk/frp 控制链路相关端点：
1. value/ to_desk/status/ frp/status/ 的 schema 全文
2. 前端 JS 中 to_desk/frp/value/todesk 的调用上下文（payload 字段名）
3. status/ use/ 完整首行字段（找 todesk 号 / frp 状态字段）
"""
import json
import os
import re
import sys

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

BASE = "https://xqzg.newbv.cn"

with open(os.path.join(ROOT, "config", "credentials.json"), encoding="utf-8") as f:
    creds = json.load(f)
from core.secrets import decrypt_settings  # noqa: E402
creds = decrypt_settings(creds)
api1 = creds["api_credentials"]["api1"]

s = requests.Session()
s.headers["User-Agent"] = "AutoWork-probe/1.0"
assert s.post(f"{BASE}/api/rbac/auth/login/",
              json={"username": api1["username"],
                    "password": api1["password"]}, timeout=15).status_code == 200
print("[LOGIN] ok\n")

# ---------- 1) 三个端点的 schema 全文 ----------
schema = s.get(f"{BASE}/api/schema/", headers={"Accept": "application/json"},
               timeout=30).json()
print("=" * 80)
print("1) schema 全文（value / to_desk/status / frp/status）")
print("=" * 80)
for p in ("/api/snooker_om/value/", "/api/snooker_om/to_desk/status/",
          "/api/snooker_om/frp/status/"):
    print(f"\n--- {p} ---")
    print(json.dumps(schema["paths"].get(p, {}), ensure_ascii=False, indent=1)[:3000])

# ---------- 2) 前端 JS 调用上下文 ----------
print("\n" + "=" * 80)
print("2) 前端 JS 中 to_desk / frp / value / todesk 上下文")
print("=" * 80)
js = s.get(f"{BASE}/assets/index-ygueT98f.js", timeout=60).text
print(f"JS 大小: {len(js)//1024}KB")

for kw in ("to_desk", "frp", "/value", "todesk", "Todesk", "ToDesk"):
    positions = [m.start() for m in re.finditer(re.escape(kw), js)]
    print(f"\n### '{kw}' 出现 {len(positions)} 次")
    shown = 0
    last_end = -1
    for pos in positions:
        if pos < last_end:
            continue
        ctx = js[max(0, pos - 220):pos + 260].replace("\n", " ")
        print(f"  @{pos}: ...{ctx}...")
        last_end = pos + 260
        shown += 1
        if shown >= 12:
            print(f"  ...（其余 {len(positions) - shown} 处省略）")
            break

# ---------- 3) status/ use/ 完整首行字段 ----------
print("\n" + "=" * 80)
print("3) status/ 与 use/ 首行完整字段（找 todesk 号 / frp 状态）")
print("=" * 80)
for path in ("/api/snooker_om/status/", "/api/snooker_om/use/"):
    r = s.get(BASE + path, params={"page": 1, "pagesize": 1},
              headers={"Accept": "application/json"}, timeout=30)
    row = (r.json().get("results") or [{}])[0]
    print(f"\n--- {path} 首行全部 {len(row)} 个字段 ---")
    for k, v in row.items():
        vs = json.dumps(v, ensure_ascii=False)
        print(f"  {k:24} = {vs[:80]}")
