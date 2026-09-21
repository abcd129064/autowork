# -*- coding: utf-8 -*-
"""从 OpenAPI schema 提取 snooker_om 全部端点详情（参数/请求体/描述），
并对 GET 端点做安全采样（只读、小分页），输出 JSON 供报告生成。"""
import json
import os
import sys

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
              json={"username": api1["username"], "password": api1["password"]},
              timeout=15).status_code == 200
print("[LOGIN] ok")

r = s.get(f"{BASE}/api/schema/", headers={"Accept": "application/json"}, timeout=30)
schema = r.json()
paths = schema.get("paths", {})
components = schema.get("components", {})


def deref(node, depth=0):
    """递归解析 $ref（限深，防循环）"""
    if depth > 4 or not isinstance(node, dict):
        return node
    if "$ref" in node:
        ref = node["$ref"]  # #/components/requestBodies/xxx
        parts = ref.lstrip("#/").split("/")
        target = schema
        for p in parts:
            target = target.get(p, {})
        return deref(target, depth + 1)
    out = {}
    for k, v in node.items():
        if k in ("properties", "items", "schema", "additionalProperties"):
            out[k] = deref(v, depth + 1)
        else:
            out[k] = v
    return out


def brief_schema(sc):
    """压缩 schema 表示：类型 + 字段名列表"""
    sc = deref(sc)
    if not isinstance(sc, dict):
        return sc
    t = sc.get("type", "?")
    if t == "object" and "properties" in sc:
        props = sc["properties"]
        req = set(sc.get("required") or [])
        fields = ", ".join(
            f"{k}{'*' if k in req else ''}({deref(v).get('type', '?')})"
            for k, v in props.items())
        return f"object{{{fields}}}"
    if t == "array" and "items" in sc:
        return f"array<{brief_schema(sc['items'])}>"
    if "enum" in sc:
        return f"enum{sc['enum']}"
    return t


result = {}
for p, ops in sorted(paths.items()):
    if not p.startswith("/api/snooker_om/"):
        continue
    entry = {}
    for method, op in ops.items():
        if method not in ("get", "post", "put", "patch", "delete"):
            continue
        info = {
            "summary": op.get("summary", ""),
            "description": op.get("description", ""),
            "params": [],
        }
        for prm in op.get("parameters", []):
            info["params"].append({
                "name": prm.get("name"),
                "in": prm.get("in"),
                "required": prm.get("required", False),
                "type": brief_schema(prm.get("schema", {})),
                "desc": prm.get("description", ""),
            })
        rb = op.get("requestBody")
        if rb:
            rb = deref(rb)
            content = rb.get("content", {})
            for ct, spec in content.items():
                info["request_body"] = {
                    "content_type": ct,
                    "schema": brief_schema(spec.get("schema", {})),
                }
                break
        entry[method] = info
    result[p] = entry

# ---------- GET 端点安全采样 ----------
SAMPLES = {
    "/api/snooker_om/status/": {"file_path": "", "page": 1, "pagesize": 1},
    "/api/snooker_om/use/": {"page": 1, "pagesize": 1},
    "/api/snooker_om/ext/": {"page": 1, "pagesize": 1},
    "/api/snooker_om/daily_data/": {},
    "/api/snooker_om/device_health/": {"page": 1, "pagesize": 1},
    "/api/snooker_om/map_view/": {},
    "/api/snooker_om/wechat_mini/": {"page": 1, "pagesize": 1},
    "/api/snooker_om/week_total/": {},
}
samples = {}
for path, params in SAMPLES.items():
    try:
        rr = s.get(BASE + path, params=params,
                   headers={"Accept": "application/json"}, timeout=30)
        body = rr.text[:600]
        samples[path] = {"status": rr.status_code, "body": body}
        print(f"[SAMPLE] {path} -> {rr.status_code} {body[:160]}")
    except Exception as e:
        samples[path] = {"status": "ERR", "body": str(e)}
        print(f"[SAMPLE] {path} -> ERR {e}")

out = {"endpoints": result, "samples": samples,
       "total_paths": len(paths)}
dst = os.path.join(ROOT, "logs", "xqzg_api_probe_result.json")
with open(dst, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"\n[OUT] {dst}  snooker_om 端点数: {len(result)}")
