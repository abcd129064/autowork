# -*- coding: utf-8 -*-
"""探测 value/ 的请求体字段（空 POST 触发校验错误回显，Django 惯例安全：
缺字段 → 400 带字段名，不会派发任何指令）。
to_desk/status / frp/status 为签名端点，空 POST 预期 403，仅验证认证形态。
"""
import json
import os
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
print("[LOGIN] ok")

# 取 csrftoken（Django 登录后一般已种 cookie；否则 GET 首页补种）
tok = s.cookies.get("csrftoken")
if not tok:
    s.get(BASE + "/", timeout=15)
    tok = s.cookies.get("csrftoken")
print(f"[CSRF] csrftoken={'有' if tok else '无'}")

hdr = {"Accept": "application/json", "X-CSRFToken": tok or "",
       "Referer": f"{BASE}/"} if tok else {}
if not hdr:
    hdr = {"Accept": "application/json", "Referer": f"{BASE}/"}

print("\n--- POST /api/snooker_om/value/  body={} ---")
r = s.post(f"{BASE}/api/snooker_om/value/", json={}, headers=hdr, timeout=20)
print(f"{r.status_code}  {r.text[:600]}")

print("\n--- POST /api/snooker_om/value/  data=(空form) ---")
r = s.post(f"{BASE}/api/snooker_om/value/", data={}, headers=hdr, timeout=20)
print(f"{r.status_code}  {r.text[:600]}")

print("\n--- POST /api/snooker_om/to_desk/status/  body={} （无签名） ---")
r = s.post(f"{BASE}/api/snooker_om/to_desk/status/", json={}, timeout=20)
print(f"{r.status_code}  {r.text[:400]}")

print("\n--- POST /api/snooker_om/frp/status/  body={} （无签名） ---")
r = s.post(f"{BASE}/api/snooker_om/frp/status/", json={}, timeout=20)
print(f"{r.status_code}  {r.text[:400]}")
