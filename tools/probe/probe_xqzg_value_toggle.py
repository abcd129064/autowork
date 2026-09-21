# -*- coding: utf-8 -*-
"""ToDesk 开关全链路真机探测（测试设备：公司球房测试/0-100、公司测试/huzh）

诊断两个线上问题：
1. 无论开/关 UI 都报「发送失败」但指令实际生效 → 打印 value/ 原始响应，
   核对 errorcode 类型（可能为字符串 "0" 而非 int 0）与字段结构；
2. 本地快照绿色但实际已关闭、刷新不同步 → 对比本地 xqzg_status 快照与
   实时 status/?table_id= 返回的 todesk/todesk_status 字段语义。

只对 0-100 做一轮 开→读→关→读 回环，huzh 仅读不写。
"""
import json
import os
import sys
import time

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
r = s.post(f"{BASE}/api/rbac/auth/login/",
           json={"username": api1["username"], "password": api1["password"]},
           timeout=15)
print(f"[LOGIN] {r.status_code}")
tok = s.cookies.get("csrftoken")
if not tok:
    s.get(BASE + "/", timeout=15)
    tok = s.cookies.get("csrftoken")
print(f"[CSRF] {'有' if tok else '无'}")

hdr = {"Accept": "application/json", "Referer": f"{BASE}/"}
if tok:
    hdr["X-CSRFToken"] = tok


def read_live(table_id):
    """GET status/?table_id=（无 file_path 实时行）打印原始 JSON"""
    r = s.get(f"{BASE}/api/snooker_om/status/",
              params={"page": 1, "pagesize": 20, "table_id": table_id},
              headers=hdr, timeout=20)
    print(f"\n[READ] table_id={table_id} -> HTTP {r.status_code}")
    try:
        data = r.json()
        rows = data.get("results") or []
        print(f"  total={data.get('total')} rows={len(rows)}")
        for row in rows:
            keep = {k: row.get(k) for k in
                    ("table_id", "device_code", "file_path", "club_name",
                     "todesk_id", "todesk_status", "frp_status", "status")}
            print(f"  {json.dumps(keep, ensure_ascii=False)}")
        return rows
    except ValueError:
        print("  非 JSON:", r.text[:300])
        return []


def send_value(device_code, datavalue, label):
    """POST value/ 打印原始响应（诊断 errorcode 判定为什么误判失败）"""
    r = s.post(f"{BASE}/api/snooker_om/value/",
               data={"datacode": device_code, "datatype": "action",
                     "datavalue": datavalue},
               headers=hdr, timeout=20)
    print(f"\n[SEND] {label} datacode={device_code} datavalue={datavalue}"
          f" -> HTTP {r.status_code}")
    print(f"  Content-Type: {r.headers.get('Content-Type')}")
    print(f"  RAW: {r.text[:500]}")
    try:
        data = r.json()
        print(f"  parsed: errorcode={data.get('errorcode')!r}"
              f" ({type(data.get('errorcode')).__name__})"
              f" pushStatus={data.get('pushStatus')!r}")
        return data
    except ValueError:
        print("  非 JSON 响应")
        return None


# 目标设备（本地库已核实）
DEV = "B05D9V2CNFCW008CM00C9"   # 0-100（公司球房测试）
TID = "0-100"
DEV2 = "49VJRQ3CNCMK002990174"  # huzh（公司测试）
TID2 = "huzh"

print("=" * 60)
print("第一步：只读——两台设备的实时状态")
rows1 = read_live(TID)
rows2 = read_live(TID2)

print("=" * 60)
print("第二步：0-100 开→读→关→读 回环（真机指令）")
send_value(DEV, "20", "开启 todesk")
time.sleep(5)
cur = read_live(TID)
send_value(DEV, "80", "关闭 todesk")
time.sleep(5)
cur = read_live(TID)

print("=" * 60)
print("完成")
