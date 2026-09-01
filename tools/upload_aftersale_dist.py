# -*- coding: utf-8 -*-
"""上传 web/aftersale_front/dist 到服务器 /opt/aftersale-web/dist/

密码不落盘：从环境变量 AFT_SSH_PASS 读取。
用法（bash）：
  AFT_SSH_PASS='***' python tools/upload_aftersale_dist.py
"""
import os
import sys
import stat
import posixpath

import paramiko

HOST = "49.235.34.253"
USER = "root"
LOCAL_DIST = os.path.join(os.path.dirname(__file__), "..", "web", "aftersale_front", "dist")
REMOTE_DIR = "/opt/aftersale-web/dist"


def run(sftp, ssh, cmd):
    _, out, err = ssh.exec_command(cmd)
    rc = out.channel.recv_exit_status()
    o, e = out.read().decode("utf-8", "replace").strip(), err.read().decode("utf-8", "replace").strip()
    return rc, o, e


def main():
    pw = os.environ.get("AFT_SSH_PASS")
    if not pw:
        print("ERROR: AFT_SSH_PASS not set")
        sys.exit(2)

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=pw, timeout=20)
    sftp = ssh.open_sftp()

    # 1) 远端备份（保留最近 1 份）
    rc, o, e = run(sftp, ssh, (
        f"cd /opt/aftersale-web && rm -f dist_backup_prev.tar.gz && "
        f"[ -f dist_backup_last.tar.gz ] && mv dist_backup_last.tar.gz dist_backup_prev.tar.gz || true; "
        f"tar czf dist_backup_last.tar.gz dist && ls -lh dist_backup_last.tar.gz"
    ))
    print("backup:", rc, o or e)
    if rc != 0:
        sys.exit(3)

    # 2) 清空远端旧产物
    rc, o, e = run(sftp, ssh, f"rm -rf {REMOTE_DIR}/* && mkdir -p {REMOTE_DIR}/assets")
    print("clean:", rc, o or e)

    # 3) 递归上传
    count = 0
    for root, _dirs, files in os.walk(os.path.abspath(LOCAL_DIST)):
        rel = os.path.relpath(root, os.path.abspath(LOCAL_DIST)).replace("\\", "/")
        rdir = REMOTE_DIR if rel == "." else posixpath.join(REMOTE_DIR, rel)
        try:
            sftp.stat(rdir)
        except FileNotFoundError:
            sftp.mkdir(rdir)
        for fn in files:
            lp = os.path.join(root, fn)
            rp = posixpath.join(rdir, fn)
            sftp.put(lp, rp)
            sftp.chmod(rp, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
            count += 1
            print("put", posixpath.relpath(rp, REMOTE_DIR))
    print(f"uploaded {count} files")

    # 4) 校验 index.html 引用的资源都存在
    rc, o, e = run(sftp, ssh, (
        f"cd {REMOTE_DIR} && for f in $(grep -oE 'assets/[^\"]+' index.html); do "
        f"[ -f $f ] || echo MISSING:$f; done; ls -l index.html assets/ | head -20"
    ))
    print("verify:", rc)
    print(o or e)

    sftp.close()
    ssh.close()
    print("DONE")


if __name__ == "__main__":
    main()
