"""PyInstaller 打包包装脚本 - 依次构建 AutoWork / AfterSale / Management 三个 spec

构建完成后自动将运行时需要位于 exe 旁边的文件复制到各产物目录：
- settings.json：用户可编辑配置（应用从 exe 目录读写），三个产物各一份
- frpc.exe：P2P 外部工具（主程序与运维面板的远程会话从 exe 目录启动）
- 旧版 exe 命名（AutoWork.exe / AfterSale.exe）清理，避免与新名混淆
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


def _build(spec_name):
    """构建单个 spec，失败则终止整个流程"""
    print(f'\n[build_exe] ==== 开始构建 {spec_name} ====')
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", spec_name],
        stderr=subprocess.STDOUT
    )
    if result.returncode != 0:
        sys.exit(result.returncode)


_build('AutoWork.spec')
_build('AfterSale.spec')
_build('Management.spec')

# ---- 构建后处理：复制 exe 旁边的运行时文件 ----
# 主程序目录：settings.json + frpc.exe（P2P 远程会话）
# 售后面板目录：settings.json（无需 frpc）
# 运维面板目录：settings.json + frpc.exe（SSH/SFTP 远程会话同样依赖）
dist_dir = os.path.join(ROOT, 'dist', 'AutoWork')
for name in ('settings.json', 'frpc.exe'):
    src = os.path.join(ROOT, name)
    if os.path.isfile(src):
        shutil.copy2(src, os.path.join(dist_dir, name))
        print(f'[build_exe] 已复制 {name} -> dist/AutoWork/')
    else:
        print(f'[build_exe] 跳过 {name}（源文件不存在）')

for rel in ('AfterSale', 'Management'):
    out = os.path.join(ROOT, 'dist', rel)
    src = os.path.join(ROOT, 'settings.json')
    if os.path.isfile(src):
        shutil.copy2(src, os.path.join(out, 'settings.json'))
        print(f'[build_exe] 已复制 settings.json -> dist/{rel}/')
    if rel == 'Management':
        frpc = os.path.join(ROOT, 'frpc.exe')
        if os.path.isfile(frpc):
            shutil.copy2(frpc, os.path.join(out, 'frpc.exe'))
            print(f'[build_exe] 已复制 frpc.exe -> dist/{rel}/')

# ---- 旧版 exe 命名清理（历史产物，避免与新名混淆） ----
# 注意：Windows 文件系统大小写不敏感，os.path.isfile('.../AutoWork.exe')
# 会命中新命名的 autowork.exe；必须用 listdir 的真实文件名精确比较，只删旧名
for rel, old in (('AutoWork', 'AutoWork.exe'), ('AfterSale', 'AfterSale.exe')):
    out_dir = os.path.join(ROOT, 'dist', rel)
    if os.path.isdir(out_dir):
        for f in os.listdir(out_dir):
            if f == old:
                os.remove(os.path.join(out_dir, f))
                print(f'[build_exe] 已清理旧版命名 {rel}/{f}')
                break

# ---- 产物校验 ----
print('\n[build_exe] 产物校验：')
expected = [
    ('dist/AutoWork/autowork.exe', '主程序'),
    ('dist/AutoWork/frpc.exe', 'P2P 工具（与主程序同目录）'),
    ('dist/AutoWork/settings.json', '主程序配置'),
    ('dist/AfterSale/aftersale.exe', '售后面板'),
    ('dist/AfterSale/settings.json', '售后面板配置'),
    ('dist/Management/management.exe', '运维面板'),
    ('dist/Management/frpc.exe', '运维面板 P2P 工具'),
    ('dist/Management/settings.json', '运维面板配置'),
]
all_ok = True
for rel, desc in expected:
    ok = os.path.isfile(os.path.join(ROOT, rel))
    all_ok = all_ok and ok
    print(f'  [{"OK" if ok else "缺失"}] {rel}（{desc}）')

if not all_ok:
    sys.exit(1)
print('[build_exe] 打包完成！')
sys.exit(0)
