# -*- coding: utf-8 -*-
"""把 v2 部署到生产，与 v1 并行运行（根选择页 + /v1/ + /v2/）

目标布局（全部位于 /opt/aftersale-web/dist）：
    index.html        ← 新的入口选择页（本次新增）
    v1/index.html     ← v1 原 index.html 的副本（**不改动** v1 的 assets/）
    v2/**             ← v2 全套产物（base=/v2/，自带 static/）
    assets/**         ← v1 的资产，原地保留
                        （v1 的 index.html 用的是 `/assets/...` 根绝对路径，
                          所以 assets 必须继续留在 dist 根，不能跟着挪进 v1/）

不改 nginx 配置：现有 `location / { try_files $uri $uri/ /index.html; }`
本身就能服务上述结构（`$uri/` 让 /v1/ 与 /v2/ 命中各自目录下的 index.html）。

用法：
    AFT_SSH_PASS='***' python tools/deploy_parallel_v1v2.py --dry-run
    AFT_SSH_PASS='***' python tools/deploy_parallel_v1v2.py

回滚（服务器上执行）：
    cd /opt/aftersale-web && tar xzf dist_backup_pre-v2_<时间戳>.tar.gz
"""
import io
import os
import posixpath
import stat
import sys
import tarfile
import time

import paramiko

HOST, USER, PORT = "49.235.34.253", "root", 22
BASE = "/opt/aftersale-web"
DIST = posixpath.join(BASE, "dist")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_V2 = os.path.join(ROOT, "web", "vue-pure-admin", "dist-v2")
LOCAL_CHOOSER = os.path.join(ROOT, "web", "aftersale_chooser", "index.html")

DRY = "--dry-run" in sys.argv


def sh(ssh, cmd, label="", check=True):
    _, out, err = ssh.exec_command(cmd)
    rc = out.channel.recv_exit_status()
    o = out.read().decode("utf-8", "replace").strip()
    e = err.read().decode("utf-8", "replace").strip()
    if label:
        print(f"--- {label} (rc={rc})")
        if o:
            print(o)
        if e:
            print("STDERR:", e)
    if check and rc != 0:
        raise SystemExit(f"命令失败 rc={rc}: {cmd}")
    return rc, o, e


def build_tarball():
    """本地把 dist-v2 打成 tar.gz（比逐个 sftp put 快得多，也避免半途失败留下残缺）"""
    buf = io.BytesIO()
    count = 0
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for root, _dirs, files in os.walk(LOCAL_V2):
            for fn in files:
                fp = os.path.join(root, fn)
                arc = os.path.relpath(fp, LOCAL_V2).replace("\\", "/")
                tf.add(fp, arcname=arc)
                count += 1
    data = buf.getvalue()
    print(f"本地打包 {count} 个文件 -> {len(data) / 1024 / 1024:.2f} MB")
    return data, count


def main():
    pw = os.environ.get("AFT_SSH_PASS")
    if not pw:
        sys.exit("AFT_SSH_PASS not set")

    for p, label in [(LOCAL_V2, "v2 产物"), (LOCAL_CHOOSER, "选择页")]:
        if not os.path.exists(p):
            sys.exit(f"{label}不存在: {p}")

    ts = time.strftime("%Y%m%d_%H%M%S")
    backup = f"dist_backup_pre-v2_{ts}.tar.gz"

    plan = [
        f"备份 dist -> {BASE}/{backup}",
        "建 v1/index.html（已存在则跳过，防二次执行把选择页覆盖进去）",
        f"清空并重建 {DIST}/v2/",
        "上传并解包 v2 产物 -> dist/v2/",
        "上传选择页 -> dist/index.html",
        "校验资源引用 + 接口",
    ]
    print("=== 部署计划 ===")
    for i, s in enumerate(plan, 1):
        print(f"  {i}. {s}")
    if DRY:
        print("\n[--dry-run] 未做任何改动")
        return

    tar_bytes, nfiles = build_tarball()

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, port=PORT, username=USER, password=pw, timeout=25)

    try:
        # 1) 备份
        sh(ssh,
           f"cd {BASE} && tar czf {backup} dist && ls -lh {backup}",
           "1) 备份")

        # 2) v1 入口（幂等：已存在就不覆盖，否则会把选择页当成 v1 页）
        sh(ssh,
           f"cd {DIST} && mkdir -p v1 && "
           f"if [ -f v1/index.html ]; then echo 'v1/index.html 已存在，跳过'; "
           f"else cp index.html v1/index.html && echo 'v1/index.html 已创建'; fi && "
           f"ls -l v1/",
           "2) v1 入口")

        # 3) 清空 v2 目录（只动我们自己的输出目录）
        sh(ssh, f"rm -rf {DIST}/v2 && mkdir -p {DIST}/v2", "3) 重建 v2 目录")

        # 4) 上传 tarball 并解包
        sftp = ssh.open_sftp()
        remote_tar = "/tmp/_v2dist.tar.gz"
        sftp.putfo(io.BytesIO(tar_bytes), remote_tar)
        print(f"--- 4) 上传 tarball ({len(tar_bytes)/1024/1024:.2f} MB) 完成")
        sftp.close()

        sh(ssh, f"tar xzf {remote_tar} -C {DIST}/v2 && rm -f {remote_tar} && "
                f"find {DIST}/v2 -type f | wc -l",
           "4) 解包 v2")
        sh(ssh, f"chmod -R a+rX {DIST}", "4b) 权限")

        # 5) 选择页
        sftp = ssh.open_sftp()
        with open(LOCAL_CHOOSER, "rb") as f:
            sftp.putfo(io.BytesIO(f.read()), f"{DIST}/index.html")
        sftp.close()
        print("--- 5) 选择页已上传 -> dist/index.html")

        # 6) 校验
        print("\n=== 6) 校验 ===")
        sh(ssh, (
            f"cd {DIST} && "
            "echo '--- 目录 ---' && ls -l index.html v1/ | head -5 && "
            "echo '--- v2 顶层 ---' && ls v2/ && "
            "echo '--- v2/index.html 资源引用是否都存在 ---' && "
            "for f in $(grep -oE '/v2/static/[^\"]+' v2/index.html | sort -u); do "
            "  [ -f \".$f\" ] || echo MISSING:$f; done; echo '(以上无 MISSING 即通过)' && "
            "echo '--- v1/index.html 里的资源引用 ---' && "
            "grep -oE '/assets/[^\"]+' v1/index.html | sort -u | while read a; do "
            "  [ -f \".$a\" ] || echo MISSING:$a; done; echo '(无 MISSING 即通过)'"
        ), "6a) 静态资源")

        sh(ssh, (
            "echo '--- / ---'; curl -s -o /dev/null -w '%{http_code}\\n' http://127.0.0.1/ ; "
            "echo '--- /v1/ ---'; curl -s -o /dev/null -w '%{http_code}\\n' http://127.0.0.1/v1/ ; "
            "echo '--- /v2/ ---'; curl -s -o /dev/null -w '%{http_code}\\n' http://127.0.0.1/v2/ ; "
            "echo '--- /v2/platform-config.json ---'; curl -s http://127.0.0.1/v2/platform-config.json | head -4 ; "
            "echo '--- /api/records ---'; curl -s 'http://127.0.0.1/api/records?page=1&page_size=1' | head -c 200; echo"
        ), "6b) 端点连通")

        sh(ssh, f"nginx -t", "6c) nginx 配置仍然有效（未改动，仅供确认）")

        print(f"\n✅ 部署完成")
        print(f"   入口   http://{HOST}/")
        print(f"   新版   http://{HOST}/v2/")
        print(f"   老版   http://{HOST}/v1/")
        print(f"   回滚   cd {BASE} && tar xzf {backup}")
        print(f"\n   v2 文件数 {nfiles}")
    finally:
        ssh.close()


if __name__ == "__main__":
    main()
