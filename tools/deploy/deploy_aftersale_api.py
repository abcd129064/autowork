# -*- coding: utf-8 -*-
"""上传修复后的 app.py 到服务器并重启验证（密码走 AFT_SSH_PASS 环境变量）"""
import os
import sys
import paramiko

HOST, USER = "49.235.34.253", "root"
pw = os.environ.get("AFT_SSH_PASS")
if not pw:
    sys.exit("AFT_SSH_PASS not set")

CHECKS = [
    ("health", "curl -s http://127.0.0.1:8000/api/health"),
    ("filtered 09/01", "curl -s 'http://127.0.0.1:8000/api/records?page=1&page_size=1&cycle_start=2026/09/01'"),
    ("all", "curl -s 'http://127.0.0.1:8000/api/records?page=1&page_size=1'"),
    ("resolved=no", "curl -s 'http://127.0.0.1:8000/api/records?page=1&page_size=1&resolved=%E5%90%A6'"),
]

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=pw, timeout=20)

def run(cmd, label):
    _, out, err = ssh.exec_command(cmd)
    rc = out.channel.recv_exit_status()
    print(f"--- {label} (rc={rc})")
    print(out.read().decode("utf-8", "replace").strip() or err.read().decode("utf-8", "replace").strip())

run("cp /opt/aftersale-web/app.py /opt/aftersale-web/app.py.bak-stats", "backup")
sftp = ssh.open_sftp()
sftp.put(r"C:\Users\shen_zhe\Desktop\autowork\web\aftersale_api\app.py", "/opt/aftersale-web/app.py")
sftp.close()
run("systemctl restart aftersale-web && sleep 2 && systemctl is-active aftersale-web", "restart")
for label, cmd in CHECKS:
    run(cmd, label)
ssh.close()
print("DONE")
