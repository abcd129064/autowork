"""PyInstaller 打包包装脚本 - 使用 AutoWork.spec 构建，将 stderr 重定向到 stdout 以避免 PowerShell NativeCommandError

构建完成后自动将运行时需要位于 exe 旁边的文件复制到 dist 根目录：
- settings.json：用户可编辑配置（应用从 exe 目录读写）
- frpc.exe：P2P 外部工具（应用从 exe 目录启动）
"""
import subprocess
import sys
import os
import shutil
import stat

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

def _rmtree_readonly(path):
    """递归删除目录；Windows 下遇到只读文件先清只读属性再删，
    避免 PyInstaller 清理旧 dist 时因只读 frpc.exe 抛 PermissionError。"""
    if not os.path.exists(path):
        return
    def _onerror(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            func(p)
        except OSError:
            pass
    shutil.rmtree(path, onerror=_onerror)


# ---- 打包前：清理上一次构建产物（只读容错）----
# 历史版本会把只读的 frpc.exe 复制进 dist/AutoWork，PyInstaller 的
# COLLECT 清理步骤在 Windows 上删不掉只读文件会直接报错退出，
# 因此这里先自行清理，确保后续 PyInstaller 无需处理被锁的只读文件。
_old_dist = os.path.join(ROOT, 'dist', 'AutoWork')
if os.path.isdir(_old_dist):
    _rmtree_readonly(_old_dist)
    print('[build_exe] 已清理旧 dist/AutoWork')

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
        dst = os.path.join(dist_dir, name)
        shutil.copy2(src, dst)
        # 清除目标只读位，保证下次打包清理时能正常删除
        try:
            os.chmod(dst, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass
        print(f'[build_exe] 已复制 {name} -> dist/AutoWork/')
    else:
        print(f'[build_exe] 跳过 {name}（源文件不存在）')

print('[build_exe] 打包完成！')
sys.exit(0)
