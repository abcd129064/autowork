"""PyInstaller 打包包装脚本 - 构建 AutoWork（完整版）+ AfterSale（单文件）

产物（两者相互独立，互不关联）：
1. dist/AutoWork/   完整应用（onedir）：主程序 autowork.exe，售后面板/运维
   面板均内置（打开不调用任何外部 exe）；需整目录分发
2. dist/aftersale.exe  售后面板（单文件 onefile）：内置全部依赖，独立分发；
   数据落在 exe 旁 database/tables.db（aftersale_panel 入口重定向），
   与 AutoWork 的数据相互独立

构建完成后自动将运行时需要位于 exe 旁边的文件复制到各产物目录：
- settings.json：用户可编辑配置（应用从 exe 目录读写），两份产物各一份
- frpc.exe：P2P 外部工具（完整版主程序的远程会话从 exe 目录启动）
- 旧版 exe 命名（AutoWork.exe）清理，避免与新名混淆
"""
import subprocess
import sys
import os
import shutil

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

# ---- Qt 运行时引导（conda base 下 import core.version 经 conn_logger 拉起
# PySide6.QtCore，PATH 注入的 conda 自带 Qt DLL 与 PySide6 冲突 → DLL load
# failed；见 docs/Qt内联引导说明.md）。必须在任何项目模块 import 之前执行。----
import importlib.util as _qt_iu
_qt_handles = []
try:
    _qt_spec = _qt_iu.find_spec('PySide6')
    if _qt_spec is not None:
        _qt_locs = list(getattr(_qt_spec, 'submodule_search_locations', None) or [])
        if _qt_locs:
            _qt_pkg = _qt_locs[0]
            for _d in (_qt_pkg, os.path.dirname(_qt_pkg),
                       os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32')):
                if os.path.isdir(_d):
                    try:
                        _qt_handles.append(os.add_dll_directory(_d))
                    except OSError:
                        pass
            os.environ['QT_PLUGIN_PATH'] = os.path.join(_qt_pkg, 'plugins')
            os.environ.setdefault('QT_QPA_PLATFORM_PLUGIN_PATH',
                                  os.path.join(_qt_pkg, 'plugins', 'platforms'))
except Exception:
    pass

# ---- --qt-check：解释器自检短路（release.py 预检调用，见 docs/Qt内联引导说明.md）----
# 与真实构建相同的导入链（core/__init__ → conn_logger → PySide6.QtCore），
# 秒级验证「当前解释器 + 上方引导」能加载 Qt——2026-09-21 事故：裸 conda python
# 跑 release.py，引导缺失下在构建收尾 import 处崩，白烧 6 分钟且 dist 已被挪走。
if "--qt-check" in sys.argv[1:]:
    sys.path.insert(0, ROOT)
    from core.version import get_app_version  # noqa: E402
    print("qt-ok", get_app_version())
    sys.exit(0)

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


_build('AutoWork.spec')   # 完整应用（onedir，aftersale/管理面板内置）
_build('AfterSale.spec')  # 售后面板（onefile 单文件，独立分发）

# ---- 构建期版本落盘：version.json 写到 exe 旁 ----
# 打包环境无 .git，core/version.py 会回退 {BASE}.0；构建机（有 .git）
# 在此算好完整版本号落盘，客户端「检查更新」的版本比较依赖它
import json
from datetime import datetime
sys.path.insert(0, ROOT)
from core.version import get_app_version, get_branch_name  # noqa: E402

_version_info = {
    "version": get_app_version(),
    "branch": get_branch_name(),
    "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
}
for _vdest in (os.path.join(ROOT, 'dist', 'AutoWork'), os.path.join(ROOT, 'dist')):
    if os.path.isdir(_vdest):
        with open(os.path.join(_vdest, 'version.json'), 'w', encoding='utf-8') as _vf:
            json.dump(_version_info, _vf, ensure_ascii=False, indent=2)
        print(f'[build_exe] 已写 version.json -> {os.path.relpath(_vdest, ROOT)}/ '
              f'({_version_info["version"]})')

# ---- 构建后处理：复制 exe 旁边的运行时文件 ----
# 完整版目录：config/（分域配置）+ frpc.exe（P2P 远程会话）
# 单文件售后面板：config/ 复制到 dist/（aftersale.exe 旁）
# settings.json 已迁移为 settings.json.bak；若仓库根仍存在旧版 settings.json
#（如旧版升级包构建场景），一并复制作为首启自动迁移的迁移源
dist_dir = os.path.join(ROOT, 'dist', 'AutoWork')
root_config = os.path.join(ROOT, 'config')
if os.path.isdir(root_config):
    for dest in (dist_dir, os.path.join(ROOT, 'dist')):
        dst_config = os.path.join(dest, 'config')
        if os.path.isdir(dst_config):
            shutil.rmtree(dst_config)
        shutil.copytree(root_config, dst_config)
        print(f'[build_exe] 已复制 config/ -> {os.path.relpath(dest, ROOT)}/')
else:
    print('[build_exe] 跳过 config/（目录不存在，首次构建后产物将走首启迁移）')

root_settings = os.path.join(ROOT, 'settings.json')
if os.path.isfile(root_settings):
    # 旧版 settings.json 保留场景：复制为迁移源（应用首启自动分拣到 config/）
    for dest in (dist_dir, os.path.join(ROOT, 'dist')):
        shutil.copy2(root_settings, os.path.join(dest, 'settings.json'))
    print('[build_exe] 已复制 settings.json（迁移源）-> dist/AutoWork/ 与 dist/')

frpc_src = os.path.join(ROOT, 'frpc.exe')
if os.path.isfile(frpc_src):
    shutil.copy2(frpc_src, os.path.join(dist_dir, 'frpc.exe'))
    print('[build_exe] 已复制 frpc.exe -> dist/AutoWork/')
else:
    print('[build_exe] 跳过 frpc.exe（源文件不存在）')

# ---- 旧产物清理 ----
# 旧版 exe 命名清理（历史产物，避免与新名混淆）。注意：Windows 文件系统
# 大小写不敏感，os.path.isfile('.../AutoWork.exe') 会命中新命名的
# autowork.exe；必须用 listdir 的真实文件名精确比较，只删旧名
out_dir = os.path.join(ROOT, 'dist', 'AutoWork')
if os.path.isdir(out_dir):
    for f in os.listdir(out_dir):
        if f == 'AutoWork.exe':
            os.remove(os.path.join(out_dir, f))
            print(f'[build_exe] 已清理旧版命名 AutoWork/{f}')
            break
# 旧 onedir 售后面板目录与运维面板目录（已改单文件 / 不再独立打包）
for stale in ('AfterSale', 'Management'):
    stale_dir = os.path.join(ROOT, 'dist', stale)
    if os.path.isdir(stale_dir):
        shutil.rmtree(stale_dir, ignore_errors=True)
        print(f'[build_exe] 已清理旧产物目录 dist/{stale}/')

# ---- 产物校验 ----
print('\n[build_exe] 产物校验：')
expected = [
    ('dist/AutoWork/autowork.exe', '主程序（完整版，aftersale/管理面板内置）'),
    ('dist/AutoWork/frpc.exe', 'P2P 工具（与主程序同目录）'),
    ('dist/AutoWork/config', '主程序分域配置目录'),
    ('dist/AutoWork/version.json', '构建期版本落盘（自动更新版本比较依赖）'),
    ('dist/aftersale.exe', '售后面板（单文件独立版，与主程序互不关联）'),
    ('dist/config', '售后面板分域配置目录（exe 旁）'),
    ('dist/version.json', '售后面板版本落盘（自动更新版本比较依赖）'),
]
all_ok = True
for rel, desc in expected:
    p = os.path.join(ROOT, rel)
    ok = os.path.isdir(p) if os.path.isdir(p) else os.path.isfile(p)
    all_ok = all_ok and ok
    print(f'  [{"OK" if ok else "缺失"}] {rel}（{desc}）')

if not all_ok:
    sys.exit(1)
print('[build_exe] 打包完成！')
sys.exit(0)
