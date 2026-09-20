# -*- coding: utf-8 -*-
"""部署 app.py（daily 完整日期 + occurred_at 筛选）+ 重启 + 验证"""
import os, io, paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("49.235.34.253", port=22, username="root", password=os.environ["AFT_SSH_PASS"], timeout=25)

def run(cmd):
    _, out, err = ssh.exec_command(cmd, timeout=60)
    o, e = out.read().decode(), err.read().decode()
    print(f"$ {cmd[:100]}\n{o}{('[err] ' + e) if e.strip() else ''}")
    return o

sftp = ssh.open_sftp()
with open(r"C:\Users\shen_zhe\Desktop\autowork\web\aftersale_api\app.py", "rb") as f:
    sftp.putfo(io.BytesIO(f.read()), "/opt/aftersale-web/app.py")
sftp.close()
run("systemctl restart aftersale-web && sleep 2 && systemctl is-active aftersale-web")
run("curl -s 'http://127.0.0.1:8000/api/stats/charts' | python3 -c 'import sys,json;d=json.load(sys.stdin);print(\"daily[0]:\",d[\"daily\"][0])'")
run("curl -s 'http://127.0.0.1:8000/api/records?page=1&page_size=3&occurred_at=2026-09-18' | python3 -c 'import sys,json;d=json.load(sys.stdin);print(\"occurred_at 筛选: total=\",d[\"total\"],\"| 首行 date:\",d[\"rows\"][0][\"occurred_at\"] if d[\"rows\"] else \"-\")'")
ssh.close()
print("DONE")
