# -*- coding: utf-8 -*-
"""真机冒烟：真实 frpc.exe 0.66.0 验证 admin API 热重载通道

用仓库内置的生产同款 frpc.exe 跑本机回环，无需真实 frps：
1. 生成含 webServer 段的 TOML（与 RemoteSessionManager 同口径）启动 frpc
   （loginFailExit=false：登录不上的服务器不影响 admin API 可用性验证）
2. GET /healthz（免认证）→ 200：admin 端口已监听
3. GET /api/reload 无 BasicAuth → 401；带正确 BasicAuth → 200（1 visitor）
4. 重写 TOML 追加第 2 个 visitor → reload 200 且 frpc 进程存活
   （stdout 日志应出现 "visitor added: [snk_2]" diff 语义）
5. 写坏 TOML（type 非法）→ reload 400 且进程存活
6. 修复回 2 visitors → reload 200；kill 收尾

用法：<venv>python tools/smoke/smoke_frp_admin_reload.py
退出码 0 = 全部断言通过。
"""
import base64
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FRPC_EXE = os.path.join(APP_DIR, "frpc.exe")
TMP_TOML = os.path.join(APP_DIR, "logs", "_smoke_admin.toml")
ADMIN_PORT = 17654
ADMIN_USER = "autowork"
ADMIN_PASS = "smoke-pass-123"

PASS = 0
FAIL = []


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name} {detail}")


def write_toml(visitors):
    with open(TMP_TOML, "w", encoding="utf-8") as f:
        f.write('serverAddr = "127.0.0.1"\n')
        f.write('serverPort = 17999\n')          # 无人监听的假 frps
        f.write('loginFailExit = false\n')
        f.write('auth.method = "token"\n')
        f.write('auth.token = "smoke"\n\n')
        f.write('webServer.addr = "127.0.0.1"\n')
        f.write(f'webServer.port = {ADMIN_PORT}\n')
        f.write(f'webServer.user = "{ADMIN_USER}"\n')
        f.write(f'webServer.password = "{ADMIN_PASS}"\n\n')
        for name, port in visitors:
            f.write('[[visitors]]\n')
            f.write(f'name = "{name}"\n')
            f.write('type = "xtcp"\n')
            f.write(f'serverName = "{name}"\n')
            f.write('secretKey = "abc123"\n')
            f.write(f'bindPort = {port}\n\n')


def api(path, auth=True, timeout=5):
    url = f"http://127.0.0.1:{ADMIN_PORT}{path}"
    req = urllib.request.Request(url, method="GET")
    if auth:
        token = base64.b64encode(
            f"{ADMIN_USER}:{ADMIN_PASS}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(200).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(200).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError):
        return 0, ""


def wait_healthz(deadline=15):
    t0 = time.time()
    while time.time() - t0 < deadline:
        s, _ = api("/healthz", auth=False)
        if s == 200:
            return True
        time.sleep(0.3)
    return False


def main():
    if not os.path.exists(FRPC_EXE):
        print(f"缺少 {FRPC_EXE}")
        return 2
    write_toml([("snk_1", 47511)])
    out = open(TMP_TOML + ".log", "wb")
    proc = subprocess.Popen(
        [FRPC_EXE, "-c", TMP_TOML], stdout=out, stderr=subprocess.STDOUT,
        cwd=APP_DIR)
    try:
        print("[1] admin 端口就绪")
        check("healthz 200（webServer 段被 0.66.0 接受并监听）", wait_healthz())

        print("[2] reload 认证与响应")
        s, _ = api("/api/reload", auth=False)
        check("无 BasicAuth → 401", s == 401, f"got {s}")
        s, b = api("/api/reload")
        check("带 BasicAuth → 200", s == 200, f"got {s} {b}")

        print("[3] 动态追加 visitor：重写 TOML + reload")
        write_toml([("snk_1", 47511), ("snk_2", 47512)])
        s, b = api("/api/reload")
        check("2 visitor 配置 reload → 200", s == 200, f"got {s} {b}")
        check("reload 后进程存活（无重启）", proc.poll() is None)
        time.sleep(1.0)
        log = open(TMP_TOML + ".log", encoding="utf-8",
                   errors="replace").read() if os.path.getsize(TMP_TOML + ".log") else ""
        # Windows console 编码下日志可能含 \x00（UTF-16 混写），按宽松匹配
        log_norm = log.replace("\x00", "")
        # 无真实 frps → 控制会话未建立（svr.ctl=nil），UpdateAllConfigurer
        # 按设计跳过 visitor 启停，故此处只能断言 reload 被接受（success reload
        # conf）；"visitor added [snk_2] 且 snk_1 不被重启" 的 diff 语义需对
        # 真实 frps 做真机回归（见文件头步骤 + 报告「已知限制」）。
        check("frpc 日志确认 reload 配置被接受（success reload conf）",
              "success reload conf" in log_norm,
              "tail: " + log_norm[-300:])

        print("[4] 非法配置：reload 400 且旧进程不退出")
        write_toml([("snk_1", 47511)])
        with open(TMP_TOML, "a", encoding="utf-8") as f:
            f.write('[[visitors]]\nname = "broken"\ntype = "nope"\n'
                    'serverName = "x"\nsecretKey = "y"\nbindPort = 47599\n')
        s, b = api("/api/reload")
        check("坏配置 reload → 400", s == 400, f"got {s} {b}")
        check("坏配置后进程仍存活", proc.poll() is None)
        log = open(TMP_TOML + ".log", encoding="utf-8",
                   errors="replace").read().replace("\x00", "")
        check("坏配置被校验拦截（reload frpc proxy config error）",
              "reload frpc proxy config error" in log
              or "unknown visitor type" in log,
              "tail: " + log[-300:])

        print("[5] 恢复配置再 reload")
        write_toml([("snk_1", 47511), ("snk_2", 47512)])
        s, b = api("/api/reload")
        check("恢复后 reload → 200", s == 200, f"got {s} {b}")
        check("全程进程未重启", proc.poll() is None)
    finally:
        proc.kill()
        proc.wait(timeout=10)
        out.close()

    print(f"\n结果: {PASS} PASS / {len(FAIL)} FAIL")
    for f in FAIL:
        print(f"  未通过: {f}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
