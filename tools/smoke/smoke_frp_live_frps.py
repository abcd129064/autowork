# -*- coding: utf-8 -*-
"""真机回归：连生产 frps（49.235.34.253:7900）验证 visitor 热重载不中断

覆盖离线冒烟做不到的关键断言——有真实控制连接（ctl != nil）时，
/api/reload 才会真正走 UpdateAllConfigurer → VisitorManager.UpdateAll 的
diff 逻辑并打印 "visitor added"/"visitor removed"。

步骤：
1. 用生产 token 启动真实 frpc.exe（1 个 visitor）→ 等 "login to server success"
   （证明 frps 接受认证、控制连接建立）
2. 记录 PID；admin healthz 就绪
3. 追加第 2 个 visitor → GET /api/reload 200
4. 断言：PID 不变（进程未重启）+ 日志出现 "visitor added" 含新 visitor 名 +
   无 "visitor removed"（既有 visitor 不被打断）→ 动态注册新 visitor 成功
5. 再 reload 一次（配置未变）→ 日志无新增 visitor removed/added（幂等）
6. 收尾 kill frpc

用法：<venv>python tools/smoke/smoke_frp_live_frps.py
token 经 core.app_settings 透明解密读取，脚本不外泄明文。
退出码 0 = 全部通过。
"""
import base64
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, APP_DIR)

FRPC_EXE = os.path.join(APP_DIR, "frpc.exe")
TMP_TOML = os.path.join(APP_DIR, "logs", "_live_frps.toml")
TMP_LOG = TMP_TOML + ".log"
ADMIN_PORT = 17655
ADMIN_USER = "autowork"
ADMIN_PASS = "livepass123"

# 测试专用 visitor 名（serverName 指向不存在的目标代理无妨：XTCP 只在首个
# 本地连接才向 frps 打洞，本脚本不建立本地连接，故 frps 侧无残留、无冲突）
V1 = "aw_reload_t1"
V2 = "aw_reload_t2"

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


def load_server_cfg():
    from core import app_settings
    s = app_settings.get_merged()
    fr = s.get("frpc_server") or {}
    addr = fr.get("serverAddr") or "49.235.34.253"
    port = int(fr.get("serverPort") or 7900)
    method = fr.get("auth_method") or "token"
    token = fr.get("auth_token") or ""
    return addr, port, method, token


def write_toml(visitors, addr, port, method, token):
    with open(TMP_TOML, "w", encoding="utf-8") as f:
        f.write(f'serverAddr = "{addr}"\n')
        f.write(f'serverPort = {port}\n')
        f.write(f'auth.method = "{method}"\n')
        f.write(f'auth.token = "{token}"\n')
        f.write('loginFailExit = false\n\n')  # 登录失败也不退出，便于诊断
        f.write('webServer.addr = "127.0.0.1"\n')
        f.write(f'webServer.port = {ADMIN_PORT}\n')
        f.write(f'webServer.user = "{ADMIN_USER}"\n')
        f.write(f'webServer.password = "{ADMIN_PASS}"\n\n')
        for name, bp in visitors:
            f.write('[[visitors]]\n')
            f.write(f'name = "{name}"\n')
            f.write('type = "xtcp"\n')
            f.write(f'serverName = "{name}"\n')
            f.write('secretKey = "reloadtest"\n')
            f.write(f'bindPort = {bp}\n\n')


def api(path, auth=True, timeout=6):
    url = f"http://127.0.0.1:{ADMIN_PORT}{path}"
    req = urllib.request.Request(url, method="GET")
    if auth:
        tok = base64.b64encode(f"{ADMIN_USER}:{ADMIN_PASS}".encode()).decode()
        req.add_header("Authorization", f"Basic {tok}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(512).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(512).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError):
        return 0, ""


def read_log():
    if not os.path.exists(TMP_LOG):
        return ""
    with open(TMP_LOG, "rb") as f:
        return f.read().replace(b"\x00", b"").decode("utf-8", "replace")


def wait_until(pred, deadline):
    t0 = time.time()
    while time.time() - t0 < deadline:
        if pred():
            return True
        time.sleep(0.4)
    return False


def main():
    if not os.path.exists(FRPC_EXE):
        print(f"缺少 {FRPC_EXE}")
        return 2
    addr, port, method, token = load_server_cfg()
    if not token:
        print("frps auth_token 未配置，无法连生产，终止")
        return 2
    print(f"目标 frps: {addr}:{port}（token 已读取，长度 {len(token)}）")

    open(TMP_LOG, "wb").close()
    write_toml([(V1, 47611)], addr, port, method, token)
    out = open(TMP_LOG, "ab")
    proc = subprocess.Popen([FRPC_EXE, "-c", TMP_TOML],
                            stdout=out, stderr=subprocess.STDOUT, cwd=APP_DIR)
    pid = proc.pid
    try:
        print("[1] admin 端口 + 登录成功")
        check("healthz 200", wait_until(lambda: api("/healthz", auth=False)[0] == 200, 15))
        logged_in = wait_until(
            lambda: "login to server success" in read_log(), 20)
        check("frps 登录成功（控制连接已建立，ctl!=nil）", logged_in,
              "tail: " + read_log()[-400:])
        if not logged_in:
            print("  未登录成功，跳过 diff 断言（可能 token 不符/frps 拒绝）")
            return 1

        print("[2] 基线：仅 1 个 visitor，reload 记录起点日志长度")
        log_before = len(read_log())

        print("[3] 追加第 2 个 visitor → reload")
        write_toml([(V1, 47611), (V2, 47612)], addr, port, method, token)
        status, body = api("/api/reload")
        check("reload → 200", status == 200, f"got {status} {body}")
        check("frpc 进程未重启（PID 不变）", proc.poll() is None and pid == proc.pid,
              f"start {pid} now {proc.pid} rc {proc.poll()}")

        ok_add = wait_until(
            lambda: "visitor added" in read_log()[log_before:], 8)
        newlog = read_log()[log_before:]
        check("diff 日志出现 'visitor added'（动态注册新 visitor）", ok_add,
              "new: " + newlog[-500:])
        check("新 visitor 名出现在日志中", V2 in newlog, "new: " + newlog[-500:])
        check("既有 visitor 未被移除（无 'visitor removed'）",
              "visitor removed" not in newlog, "new: " + newlog[-500:])

        print("[4] 再 reload（配置未变）→ 幂等，不应动任何 visitor")
        log_before2 = len(read_log())
        status, body = api("/api/reload")
        check("二次 reload → 200", status == 200, f"got {status} {body}")
        time.sleep(1.0)
        delta = read_log()[log_before2:]
        check("幂等 reload 无 added/removed diff",
              "visitor added" not in delta and "visitor removed" not in delta,
              "delta: " + delta[-400:])
        check("二次 reload 后进程仍存活", proc.poll() is None)

        print("[5] 删除 visitor 回归：移除 V2 → reload 应 removed 且 V1 保留")
        log_before3 = len(read_log())
        write_toml([(V1, 47611)], addr, port, method, token)
        status, body = api("/api/reload")
        check("移除后 reload → 200", status == 200, f"got {status} {body}")
        ok_rm = wait_until(lambda: "visitor removed" in read_log()[log_before3:], 8)
        d3 = read_log()[log_before3:]
        check("'visitor removed' 出现且含 V2", ok_rm and V2 in d3, "d: " + d3[-400:])
        # removed 行形如 visitor removed: [aw_reload_t2]，确认 V1 不在该行
        m = re.search(r"visitor removed[^\n]*", d3)
        check("V1 未被牵连移除（removed 行不含 V1）",
              bool(m) and V1 not in m.group(0), "line: " + (m.group(0) if m else "N/A"))
        check("全程 PID 不变（隧道从未中断）", proc.poll() is None and pid == proc.pid)
    finally:
        proc.kill()
        proc.wait(timeout=10)
        out.close()

    print(f"\n结果: {PASS} PASS / {len(FAIL)} FAIL")
    for x in FAIL:
        print(f"  未通过: {x}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
