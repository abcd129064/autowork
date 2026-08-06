"""PyInstaller 打包包装脚本 - 使用 AutoWork.spec 构建，将 stderr 重定向到 stdout 以避免 PowerShell NativeCommandError

构建完成后自动将运行时需要位于 exe 旁边的文件复制到 dist 根目录：
- settings.json：用户可编辑配置（应用从 exe 目录读写）
- frpc.exe：P2P 外部工具（应用从 exe 目录启动）
"""
import subprocess
import sys
import os
import shutil

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

# ---- 打包前：球桌库 WAL checkpoint ----
# database/tables.db 以 WAL 模式运行，未合并的增量数据在 tables.db-wal 中；
# spec 只分发主库文件，打包前必须 checkpoint 将 WAL 全部合入主库，
# 否则打包版种子库可能缺少最新同步数据（球桌库搜索候选不全）
db_path = os.path.join(ROOT, 'database', 'tables.db')
if os.path.isfile(db_path):
    import sqlite3
    _c = sqlite3.connect(db_path)
    try:
        _c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        _c.commit()
        print('[build_exe] 已 checkpoint database/tables.db（WAL 合入主库）')
    finally:
        _c.close()

result = subprocess.run(
    [sys.executable, "-m", "PyInstaller",
     "--noconfirm", "AutoWork.spec"],
    stderr=subprocess.STDOUT
)
if result.returncode != 0:
    sys.exit(result.returncode)

# ---- 构建后处理：复制 exe 旁边的运行时文件 ----
dist_dir = os.path.join(ROOT, 'dist', 'AutoWork')
for name in ('settings.json', 'frpc.exe'):
    src = os.path.join(ROOT, name)
    if os.path.isfile(src):
        shutil.copy2(src, os.path.join(dist_dir, name))
        print(f'[build_exe] 已复制 {name} -> dist/AutoWork/')
    else:
        print(f'[build_exe] 跳过 {name}（源文件不存在）')

print('[build_exe] 打包完成！')
sys.exit(0)
