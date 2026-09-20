# -*- coding: utf-8 -*-
"""S2 生产更新源端到端验证（真实客户端代码打真实生产 URL）

验证 http://49.235.34.253/update/latest.json 通道可用：
  1. fetch_latest 能正确解析生产 latest.json（channels 形态）
  2. 占位版本 0.0.0 → check_update 对任何本地版本都返回 None（不误触发更新）
  3. Content-Type 守卫生效：生产返回 application/json，不应触发 HTML 拦截
  4. 更新源地址配置链路：settings.update_base_url 覆盖 / 默认值回退
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))   # tools/update_sim/ → 项目根
sys.path.insert(0, ROOT)
from core import updater

PROD = "http://49.235.34.253/update"
RESULTS = []


def check(name, cond, extra=""):
    RESULTS.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))


print("===== 1) fetch_latest 解析生产 latest.json =====")
entry = updater.fetch_latest(PROD, updater.CHANNEL_AUTOWORK, timeout=15)
print("  entry:", {k: v for k, v in entry.items() if not k.startswith("_")})
check("生产 latest.json 可解析", isinstance(entry, dict) and bool(entry))
check("channels 形态含 autowork 渠道", "version" in entry, str(entry.get("version")))
check("package 结构已归一化", isinstance(entry.get("package"), dict),
      str(entry.get("package")))

print("\n===== 2) 占位版本不误触发更新 =====")
for local in ("3.11.273", "3.11.280", "0.0.1", "9.9.9"):
    e = updater.check_update(PROD, local)
    check(f"本地 {local} → 不更新（None）", e is None, str(e))

print("\n===== 3) 版本比较边界 =====")
check("0.0.0 不新于 3.11.273", not updater.is_newer("0.0.0", "3.11.273"))
check("3.11.274 新于 3.11.273", updater.is_newer("3.11.274", "3.11.273"))
check("3.12.0 新于 3.11.999", updater.is_newer("3.12.0", "3.11.999"))
check("4.0.0 新于 3.99.99", updater.is_newer("4.0.0", "3.99.99"))
check("相等版本不算新", not updater.is_newer("3.11.273", "3.11.273"))
check("分支后缀不参与比较",
      not updater.is_newer("3.11.273-refactor/fluent-window", "3.11.273"))
check("分支后缀仍可比出更高版本",
      updater.is_newer("3.11.274-feature/x", "3.11.273"))
check("空/垃圾版本解析为 (0,0,0)",
      updater.parse_version("") == (0, 0, 0)
      and updater.parse_version("abc") == (0, 0, 0))

print("\n===== 4) 更新源地址配置链路 =====")
check("默认更新源为生产地址",
      updater.get_update_base_url(lambda k: "") == updater.DEFAULT_UPDATE_BASE_URL,
      updater.get_update_base_url(lambda k: ""))
check("settings 覆盖生效",
      updater.get_update_base_url(lambda k: "http://127.0.0.1:9999/u")
      == "http://127.0.0.1:9999/u")
check("settings_get 抛异常时回退默认",
      updater.get_update_base_url(lambda k: (_ for _ in ()).throw(RuntimeError("boom")))
      == updater.DEFAULT_UPDATE_BASE_URL)
from core.app_settings import domain_of
check("update_base_url 登记在 misc 域", domain_of("update_base_url") == "misc",
      domain_of("update_base_url"))
check("update_auto_check 登记在 misc 域", domain_of("update_auto_check") == "misc",
      domain_of("update_auto_check"))

print("\n===== 5) HTML 回退守卫（生产 SPA try_files 风险）=====")
try:
    # 生产不存在的渠道文件会被 nginx 回退成 index.html（200+text/html）
    # 这里直接请求一个必然回退的路径，验证客户端不会把 HTML 当 JSON
    import requests
    r = requests.get(PROD + "/nonexistent-channel/latest.json", timeout=15)
    ctype = str(r.headers.get("Content-Type") or "").lower()
    print(f"  不存在路径 → http={r.status_code} type={ctype} size={len(r.content)}")
    if "html" in ctype:
        # 守卫应能识别并抛出可诊断的错误
        try:
            updater.fetch_latest(PROD + "/nonexistent-channel", timeout=15)
            check("HTML 回退被守卫拦截", False, "未抛异常")
        except ValueError as ex:
            check("HTML 回退被守卫拦截", "nginx" in str(ex) or "JSON" in str(ex),
                  str(ex)[:100])
    else:
        check("HTML 回退被守卫拦截", True, f"生产直接返回 {ctype}，无需拦截")
except Exception as ex:
    check("HTML 回退被守卫拦截", False, f"{type(ex).__name__}: {ex}")

passed = sum(1 for _, ok in RESULTS if ok)
print(f"\n===== 汇总：{passed}/{len(RESULTS)} PASS =====")
if passed != len(RESULTS):
    print("失败项：")
    for n, ok in RESULTS:
        if not ok:
            print("  -", n)
    sys.exit(1)
print("全部通过")
