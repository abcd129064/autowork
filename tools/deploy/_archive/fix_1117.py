# -*- coding: utf-8 -*-
import os, paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("49.235.34.253", port=22, username="root", password=os.environ["AFT_SSH_PASS"], timeout=25)

def run(cmd):
    _, out, err = ssh.exec_command(cmd, timeout=60)
    print(f"$ {cmd[:100]}\n" + out.read().decode() + err.read().decode())

# 空串或 NULL 的 created_at 统一补当前时刻（仅 1117 这类异常行；顺带全表排查）
run("mysql -uroot -p'Kaidao!2' -e \"UPDATE autowork.aftersale_records SET created_at=NOW(), updated_at=NOW() WHERE id=1117 AND (created_at IS NULL OR created_at=''); SELECT id, created_at, occurred_at, cycle_start FROM autowork.aftersale_records WHERE id=1117; SELECT COUNT(*) AS bad_created FROM autowork.aftersale_records WHERE created_at IS NULL OR created_at='';\" 2>&1 | grep -v 'Using a password'")
ssh.close()
print("DONE")
