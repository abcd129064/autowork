# -*- coding: utf-8 -*-
"""上传修复后的 app.py + 重启 + 服务端写链路复测"""
import os, io, paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("49.235.34.253", port=22, username="root", password=os.environ["AFT_SSH_PASS"], timeout=25)

def run(cmd, mask=False):
    _, out, err = ssh.exec_command(cmd, timeout=60)
    o, e = out.read().decode(), err.read().decode()
    print(f"$ {cmd if not mask else cmd.split('-p')[0]+'-p***'}\n{o}{('[err] ' + e) if e.strip() else ''}")
    return o

# 0. 备份服务器旧 app.py
run("cp /opt/aftersale-web/app.py /opt/aftersale-web/app.py.bak_autocommit && echo backup_ok")

# 1. 上传
sftp = ssh.open_sftp()
with open(r"C:\Users\shen_zhe\Desktop\autowork\web\aftersale_api\app.py", "rb") as f:
    sftp.putfo(io.BytesIO(f.read()), "/opt/aftersale-web/app.py")
sftp.close()
run("grep -n 'autocommit' /opt/aftersale-web/app.py | head -2")

# 2. 重启
run("systemctl restart aftersale-web && sleep 2 && systemctl is-active aftersale-web")

# 3. 写链路复测（创建→确认落库→更新→删除→确认清空）
o = run(
    "set -e; "
    "TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login -H 'Content-Type: application/json' "
    "-d '{\"username\":\"admin\",\"password\":\"kaidao12\"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"token\"])'); "
    "T1=$(curl -s 'http://127.0.0.1:8000/api/records?page=1&page_size=1' | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"total\"])'); "
    "RID=$(curl -s -X POST http://127.0.0.1:8000/api/records -H 'Content-Type: application/json' -H \"Authorization: Bearer $TOKEN\" "
    "-d '{\"problem\":\"__commit_probe\",\"creator\":\"admin\"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"id\"])'); "
    "T2=$(curl -s 'http://127.0.0.1:8000/api/records?page=1&page_size=1' | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"total\"])'); "
    "UP=$(curl -s -o /dev/null -w '%{http_code}' -X PUT http://127.0.0.1:8000/api/records/$RID -H 'Content-Type: application/json' "
    "-H \"Authorization: Bearer $TOKEN\" -d '{\"problem\":\"__commit_probe_edited\"}'); "
    "DL=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE http://127.0.0.1:8000/api/records/$RID -H \"Authorization: Bearer $TOKEN\"); "
    "T3=$(curl -s 'http://127.0.0.1:8000/api/records?page=1&page_size=1' | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"total\"])'); "
    "echo \"total: $T1 -> $T2 -> $T3; rid=$RID; put=$UP; delete=$DL\""
)
print("RESULT:", o.strip().splitlines()[-1] if o.strip() else "(empty)")
ssh.close()
print("DONE")
