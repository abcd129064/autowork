# -*- coding: utf-8 -*-
"""S4 硬化验证：等待循环真实性 + 路径含空格 + System32 绝对路径

针对两个会毁安装目录的隐患做定向验证：
  1. 等待循环：PATH 被 GNU coreutils 污染（本仿真环境就是）时，bat 必须
     用 System32\\find.exe 真正等到主进程退出后才动手替换；若等待失效，
     替换会在主进程仍运行时发生（生产 = 安装目录被写坏）。
     判定方式：假主进程 sleep 4s，测量 bat 从启动到「新 exe 落位」的耗时，
     必须 >= 3.5s（说明确实在等），且替换结果正确。
  2. 路径含空格：安装目录/staging 路径带空格时全链路仍正确（引号处理）。
  3. bat 内所有外部命令均为 System32 绝对路径（静态扫描）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))   # tools/update_sim/ → 项目根
sys.path.insert(0, ROOT)
from core import updater

RESULTS = []


def check(name, cond, extra=""):
    RESULTS.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))


def write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


print("===== 1) bat 静态扫描：外部命令必须走 System32 绝对路径 =====")
sample = updater.build_bat(r"C:\Fake Install\App", r"C:\stg dir", "AutoWork.exe",
                           12345, "full")
bare_tools = ["robocopy ", "tasklist ", "taskkill ", "timeout ", "find "]
bad = []
for line in sample.splitlines():
    s = line.strip().lower()
    if s.startswith("rem") or s.startswith("::"):
        continue
    for t in bare_tools:
        # 裸命令 = 行首直接是工具名（没有 %SYS32% 前缀）
        if s.startswith(t):
            bad.append(line.strip())
check("无裸外部命令调用（robocopy/tasklist/taskkill/timeout/find）", not bad, str(bad))
check("使用 %SYS32% 前缀", "%SYS32%" in sample)
# 注释里会提到 timeout 这个词（解释为何改用 ping），所以只查实际命令调用
code_lines = [ln.strip() for ln in sample.splitlines()
              if not ln.strip().lower().startswith(("rem", "::"))]
check("未调用 timeout 命令（改 ping 睡眠）",
      not any("timeout" in ln.lower() for ln in code_lines),
      str([ln for ln in code_lines if "timeout" in ln.lower()]))
check("使用 ping 作为睡眠", any("ping.exe" in ln.lower() for ln in code_lines))

print("\n===== 2) 等待循环真实性（PATH 污染环境）=====")
# 本仿真进程 PATH 含 GNU coreutils（find/timeout 非 Windows 版），
# 正好复现「PATH 污染」场景；bat 若用绝对路径就应正确等待。
root = os.path.join(tempfile.gettempdir(), f"aw_wait_{os.getpid()}")
shutil.rmtree(root, ignore_errors=True)
install = os.path.join(root, "AutoWork")
staging = os.path.join(root, "staging")
write(os.path.join(install, "AutoWork.exe"), "old")
write(os.path.join(install, "version.json"), '{"version":"1.0.0"}')
write(os.path.join(staging, "AutoWork.exe"), "new")
write(os.path.join(staging, "version.json"), '{"version":"2.0.0"}')

SLEEP_SEC = 4
proc = subprocess.Popen([sys.executable, "-c", f"import time;time.sleep({SLEEP_SEC})"],
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
bat_path = os.path.join(root, "update.bat")
with open(bat_path, "w", encoding="gbk", errors="replace", newline="\r\n") as f:
    f.write(updater.build_bat(install, staging, "AutoWork.exe", proc.pid, "full"))

t0 = time.time()
p = subprocess.run(["cmd.exe", "/c", bat_path], capture_output=True,
                   encoding="gbk", errors="replace", timeout=120,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
elapsed = time.time() - t0
try:
    proc.kill()
except Exception:
    pass

check("bat rc=0", p.returncode == 0, f"rc={p.returncode} err={p.stderr[:200]}")
check(f"等待循环生效（耗时 {elapsed:.1f}s >= 3.5s）", elapsed >= 3.5,
      f"{elapsed:.2f}s（假主进程 sleep {SLEEP_SEC}s）")
check("替换成功：新 exe 落位", read(os.path.join(install, "AutoWork.exe")) == "new")
check("替换成功：version.json 为新", "2.0.0" in read(os.path.join(install, "version.json")))
check("回执 ok:true", json.loads(read(os.path.join(install, updater.RESULT_LOG)) or "{}").get("ok") is True)
shutil.rmtree(root, ignore_errors=True)

print("\n===== 3) 路径含空格 =====")
root = os.path.join(tempfile.gettempdir(), f"aw_space_{os.getpid()}")
shutil.rmtree(root, ignore_errors=True)
install = os.path.join(root, "Program Files Dir", "My AutoWork")
staging = os.path.join(root, "staging dir with space")
work = os.path.join(root, "work dir")
write(os.path.join(install, "AutoWork.exe"), "old")
write(os.path.join(install, "config", "misc.json"), '{"user":"KEEP"}')
write(os.path.join(install, "_internal", "old.dll"), "olddll")
write(os.path.join(staging, "AutoWork.exe"), "new")
write(os.path.join(staging, "_internal", "new.dll"), "newdll")
updater.mark_update_pending(install, {"version": "3.11.500"})

proc = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(3)"],
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
bat, vbs = updater.write_updater_scripts(work, install, staging,
                                         "AutoWork.exe", proc.pid, "full")
check("含空格路径：脚本已生成（安装目录之外）",
      os.path.isfile(bat) and os.path.isfile(vbs))
check("含空格路径：bat 不在安装目录内",
      not bat.startswith(os.path.normpath(install)))
p = subprocess.run(["cmd.exe", "/c", bat], capture_output=True,
                   encoding="gbk", errors="replace", timeout=120,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
try:
    proc.kill()
except Exception:
    pass
time.sleep(0.5)
check("含空格路径：bat rc=0", p.returncode == 0, f"err={p.stderr[:200]}")
check("含空格路径：主 exe 已换新", read(os.path.join(install, "AutoWork.exe")) == "new")
check("含空格路径：新 dll 落位",
      os.path.isfile(os.path.join(install, "_internal", "new.dll")))
check("含空格路径：旧 dll 已移除（整目录替换）",
      not os.path.isfile(os.path.join(install, "_internal", "old.dll")))
check("含空格路径：config 用户数据回迁",
      read(os.path.join(install, "config", "misc.json")) == '{"user":"KEEP"}')
check("含空格路径：pending 标记保留",
      os.path.isfile(os.path.join(install, updater.PENDING_FLAG)))
check("含空格路径：回执 ok:true",
      json.loads(read(os.path.join(install, updater.RESULT_LOG)) or "{}").get("ok") is True)
check("含空格路径：_old 备份已清理",
      not os.path.isdir(os.path.normpath(install) + updater._BACKUP_SUFFIX))
check("含空格路径：_new 残留已清理",
      not os.path.isdir(os.path.normpath(install) + "_new"))
shutil.rmtree(root, ignore_errors=True)

passed = sum(1 for _, ok in RESULTS if ok)
print(f"\n===== 汇总：{passed}/{len(RESULTS)} PASS =====")
if passed != len(RESULTS):
    print("失败项：")
    for n, ok in RESULTS:
        if not ok:
            print("  -", n)
    sys.exit(1)
print("全部通过")
