# -*- coding: utf-8 -*-
"""拉取 /api/schema/ OpenAPI 全量路由 + 各命名空间 router 根"""
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
r = s.post(f"{BASE}/api/rbac/auth/login/",
           json={"username": api1["username"], "password": api1["password"]},
           timeout=15)
print(f"[LOGIN] {r.status_code}")

# ---------- 1) OpenAPI schema ----------
r = s.get(f"{BASE}/api/schema/", headers={"Accept": "application/json"}, timeout=30)
print(f"\n[SCHEMA] {r.status_code} {len(r.text)} bytes")
schema = r.json()
paths = schema.get("paths", {})
print(f"[SCHEMA] OpenAPI {schema.get('openapi', '?')}，共 {len(paths)} 个路径\n")

print("=" * 90)
print(f"{'PATH':58} {'METHODS'}")
print("=" * 90)
for p in sorted(paths):
    ops = paths[p]
    methods = [m.upper() for m in ops if m in
               ("get", "post", "put", "patch", "delete", "options")]
    # 取 summary 补充说明
    summaries = [ops[m].get("summary") or ops[m].get("operationId", "")
                 for m in ops if m in ("get", "post", "put", "patch", "delete")]
    tag = ""
    for m in ops.values():
        if isinstance(m, dict) and m.get("tags"):
            tag = "[" + ",".join(m["tags"]) + "]"
            break
    print(f"{p:58} {','.join(methods):20} {tag} {'; '.join(filter(None, summaries))[:60]}")

# ---------- 2) 各命名空间 router 根 ----------
print("\n" + "=" * 90)
print("命名空间 router 根探测")
print("=" * 90)
namespaces = sorted({p.split("/")[2] for p in paths if p.startswith("/api/") and len(p.split("/")) > 3})
print("schema 中的命名空间:", namespaces)
# 加上前端发现但可能不在 schema 的
for ns in set(namespaces) | {"snooker_om", "notification", "work_order", "rbac"}:
    r2 = s.get(f"{BASE}/api/{ns}/", headers={"Accept": "application/json"}, timeout=20)
    if r2.status_code == 200:
        try:
            data = r2.json()
            if isinstance(data, dict):
                print(f"\nGET /api/{ns}/ -> 200：")
                for k, v in data.items():
                    print(f"    {k:30} {v}")
        except Exception:
            print(f"\nGET /api/{ns}/ -> 200 (非 JSON)")

# ---------- 3) snooker_om/version/ 深入 ----------
print("\n" + "=" * 90)
print("snooker_om/version/ 深入探测")
print("=" * 90)
for method in ("GET", "OPTIONS"):
    r3 = s.request(method, f"{BASE}/api/snooker_om/version/",
                   headers={"Accept": "application/json"}, timeout=20)
    print(f"{method} /api/snooker_om/version/ -> {r3.status_code} "
          f"Allow={r3.headers.get('Allow', '?')} {r3.text[:200]}")

# schema 里若包含 version 的参数定义，打印出来
for p, ops in paths.items():
    if "version" in p:
        print(f"\nschema 中 {p} 的定义：")
        print(json.dumps(ops, ensure_ascii=False, indent=2)[:2000])
