# -*- coding: utf-8 -*-
"""S4 vbs 隐藏窗口启动器校验：bat 生成 + GBK 落盘 + vbs→bat 拉起链路

验证点：
  1. write_updater_scripts 把 bat/vbs 正确落盘到带空格的 work_dir
  2. bat 可按 GBK 读回（cmd 默认代码页非 UTF-8）
  3. vbs 模板语法有效：wscript 拉起最小 bat，隐藏窗口，marker 文件生成
"""
import os
import sys
import time
import shutil
import subprocess
import tempfile

# 本文件位于 tools/update_sim/，项目根在上两级
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
from core import updater

root = os.path.join(tempfile.gettempdir(), "aw_vbs_test")
shutil.rmtree(root, ignore_errors=True)
os.makedirs(root, exist_ok=True)
work = os.path.join(root, "work with space")   # 带空格，验证引号处理
marker = os.path.join(root, "marker.txt")

# 1) 落盘
install = os.path.join(root, "FakeApp")
staging = os.path.join(root, "stg")
os.makedirs(install, exist_ok=True)
os.makedirs(staging, exist_ok=True)
bat, vbs = updater.write_updater_scripts(
    work, install, staging, "AutoWork.exe", 999999, "full")
print("bat exists:", os.path.isfile(bat))
print("vbs exists:", os.path.isfile(vbs))
with open(bat, encoding="gbk") as f:
    content = f.read()
print("bat gbk-readable:", bool(content), "len:", len(content))
print("bat has no unresolved placeholder:", "__INSTALL_DIR__" not in content)
print("bat INSTALL_DIR inlined:", install in content)

# 2) vbs 语法：最小 bat + vbs 模板拉起
mini_bat = os.path.join(work, "mini.bat")
with open(mini_bat, "w", encoding="gbk", newline="\r\n") as f:
    f.write('@echo off\r\n> "%s" echo vbs-launched-ok\r\n' % marker)
mini_vbs = os.path.join(work, "mini.vbs")
with open(mini_vbs, "w", encoding="gbk", newline="\r\n") as f:
    f.write(updater._VBS_TEMPLATE)

subprocess.Popen(
    ["wscript.exe", "//nologo", mini_vbs, mini_bat],
    creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
    | getattr(subprocess, "CREATE_NO_WINDOW", 0))
for _ in range(40):
    time.sleep(0.3)
    if os.path.isfile(marker):
        break
if os.path.isfile(marker):
    with open(marker) as f:
        print("vbs->bat marker:", f.read().strip())
else:
    print("vbs->bat marker: NOT-CREATED")

shutil.rmtree(root, ignore_errors=True)
