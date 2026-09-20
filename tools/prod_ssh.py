# -*- coding: utf-8 -*-
"""生产机 SSH 命令执行器（只读侦察 + 部署共用）

密码不落盘：从环境变量 AFT_SSH_PASS 读取。

用法（bash，注意密码含 `!`，必须单引号）：
  AFT_SSH_PASS='***' python tools/prod_ssh.py "systemctl is-active nginx"
  AFT_SSH_PASS='***' python tools/prod_ssh.py --file tools/_recon.sh

--file 会先上传脚本到 /tmp 再执行，避免多行/引号在 exec_command 里被拆坏。
"""
import os
import sys
import paramiko

HOST, USER, PORT = "49.235.34.253", "root", 22


def main():
    pw = os.environ.get("AFT_SSH_PASS")
    if not pw:
        sys.exit("AFT_SSH_PASS not set")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, port=PORT, username=USER, password=pw, timeout=25)

    try:
        if len(sys.argv) >= 3 and sys.argv[1] == "--file":
            local = sys.argv[2]
            with open(local, "rb") as f:
                data = f.read()
            sftp = ssh.open_sftp()
            sftp.putfo(__import__("io").BytesIO(data), "/tmp/_prod_task.sh")
            sftp.close()
            cmd = "bash /tmp/_prod_task.sh; rm -f /tmp/_prod_task.sh"
        else:
            cmd = sys.argv[1]

        _, out, err = ssh.exec_command(cmd)
        rc = out.channel.recv_exit_status()
        o = out.read().decode("utf-8", "replace")
        e = err.read().decode("utf-8", "replace")
        sys.stdout.write(o)
        if e.strip():
            sys.stdout.write("\n--- STDERR ---\n" + e)
        print(f"\n[exit={rc}]")
    finally:
        ssh.close()


if __name__ == "__main__":
    main()
