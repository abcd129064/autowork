# -*- coding: utf-8 -*-
"""部署 create_record 系统字段修复 + 修正 1117 + 验证"""
import os, io, paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("49.235.34.253", port=22, username="root", password=os.environ["AFT_SSH_PASS"], timeout=25)

def run(cmd):
    _, out, err = ssh.exec_command(cmd, timeout=60)
    o, e = out.read().decode(), err.read().decode()
    print(f"$ {cmd[:120]}\n{o}{('[err] ' + e) if e.strip() else ''}")
    return o

# 1. 上传 app.py + 重启
sftp = ssh.open_sftp()
with open(r"C:\Users\shen_zhe\Desktop\autowork\web\aftersale_api\app.py", "rb") as f:
    sftp.putfo(io.BytesIO(f.read()), "/opt/aftersale-web/app.py")
sftp.close()
run("systemctl restart aftersale-web && sleep 2 && systemctl is-active aftersale-web")

# 2. 修正 1117（用户真实操作记录）：补 created_at/updated_at/cycle_start（occurred 9-18 → 2026/09/15）
run("mysql -uroot -p'Kaidao!2' -e \"UPDATE autowork.aftersale_records SET created_at=COALESCE(created_at,NOW()), updated_at=NOW(), cycle_start='2026/09/15' WHERE id=1117 AND occurred_at='2026-09-18'; SELECT id, created_at, occurred_at, cycle_start, snk_code FROM autowork.aftersale_records WHERE id=1117;\" 2>&1 | grep -v 'Using a password'")

# 3. 端到端验证：创建（带桌号测 SNK 带出）→ 查回系统字段 → 删除清理
run(
    "TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login -H 'Content-Type: application/json' "
    "-d '{\"username\":\"admin\",\"password\":\"kaidao12\"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"token\"])'); "
    "RID=$(curl -s -X POST http://127.0.0.1:8000/api/records -H 'Content-Type: application/json' -H \"Authorization: Bearer $TOKEN\" "
    "-d '{\"problem\":\"__sysfield_probe\",\"occurred_at\":\"2026-09-18\",\"table_no\":\"125-01\",\"room_name\":\"翡翠轩竞技台球俱乐部\",\"region\":\"四川\",\"issue_type\":\"程序相关\"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"id\"])'); "
    "curl -s \"http://127.0.0.1:8000/api/records?page=1&page_size=1\" | python3 -c 'import sys,json;r=json.load(sys.stdin)[\"rows\"][0];print(\"top row:\",r[\"id\"],\"| created_at=\",r[\"created_at\"],\"| cycle_start=\",r[\"cycle_start\"],\"| snk=\",r[\"snk_code\"])'; "
    "curl -s -o /dev/null -w 'cleanup_delete=%{http_code}\\n' -X DELETE http://127.0.0.1:8000/api/records/$RID -H \"Authorization: Bearer $TOKEN\""
)
ssh.close()
print("DONE")
