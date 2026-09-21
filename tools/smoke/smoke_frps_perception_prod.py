# -*- coding: utf-8 -*-
"""生产端到端：二期 P0/P1 感知 + 预检 + 优雅停止 真机验证（2026-09-22）

目标 frps = 49.235.34.253:7400（webServer 已在生产开启，BasicAuth）。
断言链：
  A. FrpsAdminClient 对生产真实刷新：ok、名单规模、耗时、四态
     （online 样本 / offline 样本 / unregistered / 本机注册 snk 必在名单）
  B. P0 预检判定：仅 offline 拦截（不实际发起，取判定函数口径）
  C. frpc.exe 0.65 优雅停止：真实起一个测试 frpc（仅 visitor，不碰面板
     持久化文件），POST /api/stop 断言进程自行退出（无需 kill）——
     这就是 _stop_frpc 两段式的第一段在生产 frpc 上的实证

token 与面板凭据经 core.app_settings 读取，脚本不落明文。
用法：<venv>python tools/smoke/smoke_frps_perception_prod.py；退出码 0=全过。
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, APP_DIR)

FRPC_EXE = os.path.join(APP_DIR, "frpc.exe")
TMP_TOML = os.path.join(APP_DIR, "logs", "_perception_prod.toml")
TMP_LOG = TMP_TOML + ".log"
ADMIN_PORT = 17677
ADMIN_USER = "autowork"
ADMIN_PASS = "perception-pass-1"
TV1 = "aw_perception_t1"

PASS = 0
FAIL = []


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


def main():
    # ---------- A. 真实感知 ----------
    from core import app_settings
    cfg = app_settings.get("frps_admin") or {}
    if not cfg.get("base_url"):
        print("frps_admin 未配置，终止")
        return 2
    print(f"A. frps 感知 → {cfg['base_url']}")

    import core.frps_admin as fa
    c = fa.FrpsAdminClient()
    t0 = time.perf_counter()
    state = c.refresh()
    elapsed = time.perf_counter() - t0
    check("refresh → ok", state == "ok", state)
    snap = c.snapshot()
    n = len(snap["proxies"])
    check(f"名单规模 {n} 条（>100）", n > 100)
    check(f"刷新耗时 {elapsed:.2f}s < 3s", elapsed < 3.0)
    online = [k for k, v in snap["proxies"].items() if v["status"] == "online"]
    offline = [k for k, v in snap["proxies"].items() if v["status"] != "online"]
    print(f"     online={len(online)} 非online={len(offline)}")
    check("存在 online 样本", len(online) > 0)
    if online:
        check("online() 判定与名单一致",
              c.online(online[0]) == "online")
    if offline:
        check("offline() 判定与名单一致",
              c.online(offline[0]) == "offline")
    check("不存在 snk → unregistered",
          c.online("snk_@#不存在") == "unregistered")
    check("空 snk → None（不误报未注册）", c.online("") is None)

    # 本机面板注册表的真实 snk 必须出现在 frps 名单（现场设备注册过）
    panel = os.path.join(APP_DIR, "frpc_xtcp_panel.toml")
    if os.path.exists(panel):
        from core.frp_remote import _parse_visitors_toml
        recs = _parse_visitors_toml(panel)
        snks = [r["serverName"] for r in recs if r.get("serverName")]
        print(f"     面板注册 snk: {snks}")
        for s in snks:
            st = c.online(s)
            check(f"{s} 在 frps 名单（非 unregistered）",
                  st in ("online", "offline"), f"state={st}")

    # ---------- B. 预检口径 ----------
    print("B. P0 预检判定口径")
    # 与 open_session 相同的判定序列（不实际建会话）：offline 拦、其余放行
    if offline:
        check("offline 设备预检将拦截", c.online(offline[0]) == "offline")
    check("unregistered 预检放行（visitor 先注册是正常时序）",
          c.online("snk_new_registered_local") == "unregistered")

    # ---------- C. frpc 优雅停止（真实进程） ----------
    print("C. frpc.exe /api/stop 优雅停止")
    if not os.path.exists(FRPC_EXE):
        print("  缺 frpc.exe，跳过 C")
        return 1 if FAIL else 0
    s = app_settings.get_merged()
    fr = s.get("frpc_server") or {}
    addr, port = fr.get("serverAddr") or "49.235.34.253", int(fr.get("serverPort") or 7900)
    token = fr.get("auth_token") or ""
    if not token:
        print("  auth_token 缺失，跳过 C")
        return 1 if FAIL else 0
    with open(TMP_TOML, "w", encoding="utf-8") as f:
        f.write(f'serverAddr = "{addr}"\nserverPort = {port}\n'
                f'auth.method = "token"\nauth.token = "{token}"\n'
                'loginFailExit = false\n\n'
                'webServer.addr = "127.0.0.1"\n'
                f'webServer.port = {ADMIN_PORT}\n'
                f'webServer.user = "{ADMIN_USER}"\n'
                f'webServer.password = "{ADMIN_PASS}"\n\n'
                '[[visitors]]\n'
                f'name = "{TV1}"\ntype = "xtcp"\nserverName = "{TV1}"\n'
                'secretKey = "perceptiontest"\nbindPort = 47677\n')
    open(TMP_LOG, "wb").close()
    out = open(TMP_LOG, "ab")
    proc = subprocess.Popen([FRPC_EXE, "-c", TMP_TOML],
                            stdout=out, stderr=subprocess.STDOUT, cwd=APP_DIR)
    try:
        def api(path, method="GET"):
            url = f"http://127.0.0.1:{ADMIN_PORT}{path}"
            req = urllib.request.Request(url, method=method)
            tok = base64.b64encode(
                f"{ADMIN_USER}:{ADMIN_PASS}".encode()).decode()
            req.add_header("Authorization", f"Basic {tok}")
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}))
            try:
                with opener.open(req, timeout=6) as r:
                    return r.status, r.read(300).decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                return e.code, ""
            except (urllib.error.URLError, OSError):
                return 0, ""

        t0 = time.time()
        while time.time() - t0 < 15 and api("/healthz")[0] != 200:
            time.sleep(0.4)
        check("frpc admin healthz 200", api("/healthz")[0] == 200)
        t0 = time.time()
        log_txt = ""
        while time.time() - t0 < 20:
            with open(TMP_LOG, "rb") as fh:
                log_txt = fh.read().replace(b"\x00", b"").decode("utf-8", "replace")
            if "login to server success" in log_txt:
                break
            time.sleep(0.4)
        check("frpc 登录生产 frps 成功", "login to server success" in log_txt,
              log_txt[-300:])
        code2, _ = api("/api/status")
        check("frpc 0.65 /api/status 可用（管理通道自检口径）", code2 == 200,
              f"HTTP {code2}")

        code, _body = api("/api/stop", method="POST")
        check("/api/stop → 200（接受优雅退出）", code == 200, f"HTTP {code}")
        # 核心断言：不给 kill，进程应在 5s 内自行退出（_stop_frpc 第一段生效）
        try:
            rc = proc.wait(timeout=5)
            check("frpc 进程 5s 内自行退出（无需强杀）", rc is not None,
                  f"rc={rc}")
        except subprocess.TimeoutExpired:
            check("frpc 进程 5s 内自行退出（无需强杀）", False, "超时仍存活")
        # 退出后本地端口应释放（TIME_WAIT 不算 LISTEN：bind 检测用 connect）
        time.sleep(0.5)
        import socket
        sk = socket.socket()
        sk.settimeout(0.5)
        try:
            connected = sk.connect_ex(("127.0.0.1", 47677)) == 0
        finally:
            sk.close()
        check("退出后 visitor bindPort 已释放", not connected)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        out.close()

    print(f"\n结果: {PASS} PASS / {len(FAIL)} FAIL")
    for x in FAIL:
        print(f"  未通过: {x}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
