# -*- coding: utf-8 -*-
"""部署球桌搜索接口：GRANT billiard_tables SELECT + 上传 app.py + 重启 + 验证"""
import os, io, paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("49.235.34.253", port=22, username="root", password=os.environ["AFT_SSH_PASS"], timeout=25)

def run(cmd):
    _, out, err = ssh.exec_command(cmd, timeout=60)
    o, e = out.read().decode(), err.read().decode()
    print(f"$ {cmd}\n{o}{('[err] ' + e) if e.strip() else ''}")
    return o

# 1. GRANT billiard_tables 只读
run("mysql -uroot -p'Kaidao!2' -e \"GRANT SELECT ON autowork.billiard_tables TO 'aftersale_ro'@'127.0.0.1'; FLUSH PRIVILEGES;\" 2>&1 | grep -v 'Using a password'; echo grant_ok")

# 2. 上传 app.py
sftp = ssh.open_sftp()
with open(r"C:\Users\shen_zhe\Desktop\autowork\web\aftersale_api\app.py", "rb") as f:
    sftp.putfo(io.BytesIO(f.read()), "/opt/aftersale-web/app.py")
sftp.close()

# 3. 重启
run("systemctl restart aftersale-web && sleep 2 && systemctl is-active aftersale-web")

# 4. 验证：登录→搜索球房（真实数据，选个常见词）
run("TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login -H 'Content-Type: application/json' -d '{\"username\":\"admin\",\"password\":\"kaidao12\"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"token\"])'); "
    "curl -s 'http://127.0.0.1:8000/api/tables/search?room=%E7%90%83' -H \"Authorization: Bearer $TOKEN\" | python3 -c 'import sys,json;d=json.load(sys.stdin);rows=d[\"rows\"];print(\"rows=\",len(rows));print(rows[:3])'")
ssh.close()
print("DONE")
