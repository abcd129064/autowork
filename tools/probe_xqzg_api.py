# -*- coding: utf-8 -*-
"""xqzg.newbv.cn API 端点探测（只读：GET/OPTIONS；仅 login 为 POST）

用法：python tools/probe_xqzg_api.py
输出：登录状态 → DRF 根路由/schema → 已知端点 OPTIONS → 前端 JS 提取的
全部 /api/ 路径 → 逐个验证 → snooker_om 字典兜底探测。
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
LOGIN = f"{BASE}/api/rbac/auth/login/"

# ---------- 1) 解密凭据并登录 ----------
with open(os.path.join(ROOT, "config", "credentials.json"), encoding="utf-8") as f:
    creds = json.load(f)
from core.secrets import decrypt_settings  # noqa: E402
creds = decrypt_settings(creds)
api1 = creds["api_credentials"]["api1"]

s = requests.Session()
s.headers["User-Agent"] = "AutoWork-probe/1.0"
r = s.post(LOGIN, json={"username": api1["username"],
                        "password": api1["password"]}, timeout=15)
print(f"[LOGIN] {r.status_code} {r.text[:200]}")
if r.status_code != 200:
    print("!! 登录失败，后续匿名探测仍继续")
print()


def probe(method, url, **kw):
    try:
        return s.request(method, url, timeout=25, **kw)
    except Exception as e:
        print(f"[ERR ] {method} {url} -> {type(e).__name__}: {e}")
        return None


# ---------- 2) DRF 根路由 / schema / 文档端点 ----------
print("=== 根路由 / schema / 文档 ===")
for u in ("/api/", "/api/snooker_om/", "/api/schema/", "/api/docs/",
          "/swagger/", "/swagger.json", "/redoc/", "/api/rbac/"):
    r = probe("GET", BASE + u, headers={"Accept": "application/json"})
    if r is None:
        continue
    one_line = r.text.replace("\n", " ")[:260]
    print(f"GET {u} -> {r.status_code} ({r.headers.get('Content-Type','')[:40]})")
    if r.status_code == 200 and len(r.text) < 4000:
        print(f"     {one_line}")
print()

# ---------- 3) 已知端点 OPTIONS ----------
print("=== 已知端点 OPTIONS ===")
for u in ("/api/snooker_om/status/", "/api/snooker_om/migrate_image/",
          "/api/snooker_om/update_health/", "/api/rbac/auth/login/"):
    r = probe("OPTIONS", BASE + u)
    if r is None:
        continue
    print(f"OPTIONS {u} -> {r.status_code} Allow={r.headers.get('Allow', '?')}")
    ct = r.headers.get("Content-Type", "")
    if "json" in ct and r.text and len(r.text) < 3000:
        print(f"     {r.text[:600]}")
print()

# ---------- 4) 抓前端 JS，正则提取全部 /api/ 路径 ----------
print("=== 前端资源抓取 ===")
js_paths = set()
r = probe("GET", BASE + "/")
if r is not None and r.status_code == 200:
    html = r.text
    srcs = re.findall(r'src="([^"]+\.js[^"]*)"', html)
    srcs += re.findall(r"src='([^']+\.js[^']*)'", html)
    srcs += re.findall(r'href="([^"]+\.js[^"]*)"', html)
    print(f"index.html: {len(html)}B, {len(srcs)} 个 JS: {srcs[:15]}")
    # 内联脚本里的路径也算
    js_paths.update(re.findall(r'["\'`](/api/[A-Za-z0-9_/.{}$-]+)', html))
    js_paths.update(re.findall(r'snooker_om/([A-Za-z0-9_-]+)', html))
    for src in srcs:
        url = src if src.startswith("http") else (
            BASE + (src if src.startswith("/") else "/" + src))
        rr = probe("GET", url)
        if rr is None or rr.status_code != 200:
            print(f"  !! {url} -> {getattr(rr, 'status_code', 'ERR')}")
            continue
        found = re.findall(r'["\'`](/api/[A-Za-z0-9_/.{}$-]+)', rr.text)
        js_paths.update(found)
        found2 = re.findall(r'snooker_om/([A-Za-z0-9_-]+)', rr.text)
        js_paths.update("snooker_om/" + x for x in found2)
        print(f"  {src} ({len(rr.text)//1024}KB) -> {len(found)} 个 /api/ 路径")
else:
    print(f"!! 首页获取失败: {getattr(r, 'status_code', 'ERR')}")

print(f"\n=== 前端 JS 中出现的全部 API 路径（{len(js_paths)}） ===")
for p in sorted(js_paths):
    print(" ", p)
print()

# ---------- 5) 逐个验证发现的路径 ----------
print("=== 端点验证（非 404 即存在） ===")
verified = {}
for p in sorted(x for x in js_paths if x.startswith("/api/")):
    r = probe("OPTIONS", BASE + p)
    if r is not None and r.status_code != 404:
        verified[p] = f"OPTIONS {r.status_code} Allow={r.headers.get('Allow', '?')}"
        continue
    r2 = probe("GET", BASE + p, headers={"Accept": "application/json"})
    if r2 is not None and r2.status_code != 404:
        verified[p] = f"GET {r2.status_code}"
for p in sorted(verified):
    print(f"  {p}  [{verified[p]}]")
print()

# ---------- 6) snooker_om 字典兜底探测 ----------
print("=== snooker_om 字典探测（仅列非 404） ===")
guess = [
    "status", "migrate_image", "update_health",           # 已知
    "devices", "device", "tables", "table", "clubs", "club", "rooms", "room",
    "health", "healths", "alert", "alerts", "report", "reports",
    "file", "files", "file_list", "image", "images", "image_list",
    "media", "log", "logs", "record", "records",
    "config", "settings", "sync", "stats", "statistics", "summary",
    "dashboard", "export", "import", "upload", "download", "backup",
    "version", "heartbeat", "online", "offline", "snapshot", "history",
    "trend", "rank", "ranking", "submission", "ledger", "cycle",
    "maintain", "maintenance", "fault", "exception", "task", "tasks",
    "user", "users", "group", "groups", "menu", "menus", "permission",
    "reset", "batch", "batch_migrate", "preview", "thumbnail",
    "disk", "storage", "clean", "cleanup", "archive",
]
for name in guess:
    u = f"{BASE}/api/snooker_om/{name}/"
    r = probe("GET", u, headers={"Accept": "application/json"})
    if r is not None and r.status_code != 404:
        print(f"  {name}/ -> {r.status_code} {r.text[:160]}")

print("\nDONE")
