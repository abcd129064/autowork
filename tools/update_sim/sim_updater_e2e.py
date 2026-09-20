# -*- coding: utf-8 -*-
"""S4 updater bat 端到端仿真验证（真机 cmd 执行，非 mock）

在临时目录搭一个「假安装环境」，用 core.updater 生成真实 bat，
拿一个 sleep 子进程冒充「正在运行的主程序」，验证 updater 全链路：

  full 模式：
    1. 等主进程退出（sleep 子进程被 kill 掉）
    2. staging → _new → 三向 move 换位
    3. config/logs/database 用户数据从旧目录回迁到新目录
    4. .update-pending 随换位保留
    5. update_result.log 写 {"ok": true}
    6. start 重启主 exe（仿真用 copy 假 exe，能起进程即可）
  incremental 模式：
    1. staging 直接合并进安装目录
    2. config/logs/database 不被覆盖
    3. update_result.log 写 {"ok": true}
  失败路径：
    - staging 缺主 exe → {"ok": false, "error": "main exe missing..."}

每条断言打印 PASS/FAIL，末尾汇总。不触碰真实项目目录。
"""
import os
import subprocess
import sys
import time
import json
import shutil
import tempfile

# 本文件位于 tools/update_sim/，项目根在上两级
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
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
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def run_bat(bat_path, timeout=90):
    """真机执行 updater bat（cmd /c），返回 returncode

    bat 输出为 GBK（中文 Windows cmd 默认代码页），必须按 GBK 解码，
    否则 capture_output 的读取线程会抛 UnicodeDecodeError。"""
    p = subprocess.run(["cmd.exe", "/c", bat_path],
                       capture_output=True, text=True, timeout=timeout,
                       encoding="gbk", errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return p.returncode, p.stdout, p.stderr


def fake_sleep_pid():
    """起一个 sleep 子进程冒充主程序，返回 pid（bat 会 taskkill 它）

    sleep 5s：真实场景主程序收到退出指令后几百毫秒内消失，这里留 5s
    足够 bat 的 wait_loop 观察到「进程还在→已退出」的完整状态迁移，
    又不至于等满 60s 超时上限。"""
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return p.pid, p


def build_env(tag):
    """搭一套假安装环境，返回 (root, install_dir, staging_dir, main_exe)"""
    root = os.path.join(tempfile.gettempdir(), f"aw_sim_{tag}_{os.getpid()}")
    if os.path.isdir(root):
        shutil.rmtree(root, ignore_errors=True)
    install_dir = os.path.join(root, "AutoWork")
    staging_dir = os.path.join(root, updater.STAGING_DIRNAME)
    main_exe = "AutoWork.exe"
    return root, install_dir, staging_dir, main_exe


def gen_bat(install_dir, staging_dir, main_exe, pid, mode, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    bat_path = os.path.join(work_dir, "update.bat")
    with open(bat_path, "w", encoding="gbk", errors="replace", newline="\r\n") as f:
        f.write(updater.build_bat(install_dir, staging_dir, main_exe, pid, mode))
    return bat_path


# ==================== full 模式 ====================
def test_full():
    print("\n===== FULL 模式 =====")
    root, install_dir, staging_dir, main_exe = build_env("full")
    # 旧版本安装目录：主 exe + version.json + 用户数据 + 一个旧 dll
    write(os.path.join(install_dir, main_exe), "old-exe")
    write(os.path.join(install_dir, "version.json"), '{"version":"3.11.1"}')
    write(os.path.join(install_dir, "_internal", "old.dll"), "olddll")
    write(os.path.join(install_dir, "config", "misc.json"), '{"user":"keep-me"}')
    write(os.path.join(install_dir, "logs", "run.log"), "old-log")
    write(os.path.join(install_dir, "database", "local.db"), "old-db")
    updater.mark_update_pending(install_dir, {"version": "3.11.2", "mode": "full"})

    # staging（新版本）：主 exe + 新 version.json + 新 dll（不含用户数据）
    write(os.path.join(staging_dir, main_exe), "new-exe")
    write(os.path.join(staging_dir, "version.json"), '{"version":"3.11.2"}')
    write(os.path.join(staging_dir, "_internal", "new.dll"), "newdll")

    pid, proc = fake_sleep_pid()
    work_dir = os.path.join(root, "_work")
    bat = gen_bat(install_dir, staging_dir, main_exe, pid, "full", work_dir)
    try:
        run_bat(bat)
    finally:
        try:
            proc.kill()
        except Exception:
            pass

    time.sleep(0.5)
    check("full: 安装目录仍在原位", os.path.isdir(install_dir))
    check("full: 主 exe 已换新", read(os.path.join(install_dir, main_exe)) == "new-exe",
          read(os.path.join(install_dir, main_exe)))
    check("full: version.json 已换新", "3.11.2" in read(os.path.join(install_dir, "version.json")))
    check("full: 新 dll 落位", os.path.isfile(os.path.join(install_dir, "_internal", "new.dll")))
    check("full: 旧 dll 不再存在（整目录替换）",
          not os.path.isfile(os.path.join(install_dir, "_internal", "old.dll")))
    check("full: config 用户数据回迁",
          read(os.path.join(install_dir, "config", "misc.json")) == '{"user":"keep-me"}')
    check("full: logs 用户数据回迁",
          os.path.isfile(os.path.join(install_dir, "logs", "run.log")))
    check("full: database 用户数据回迁",
          os.path.isfile(os.path.join(install_dir, "database", "local.db")))
    check("full: .update-pending 随换位保留",
          os.path.isfile(os.path.join(install_dir, updater.PENDING_FLAG)))
    rj = read(os.path.join(install_dir, updater.RESULT_LOG))
    ok = False
    try:
        ok = json.loads(rj).get("ok") is True
    except Exception:
        ok = False
    check("full: update_result.log = ok:true", ok, rj)
    check("full: 备份目录已清理",
          not os.path.isdir(install_dir + updater._BACKUP_SUFFIX))
    check("full: staging 已清理", not os.path.isdir(staging_dir))
    check("full: bat 自删", not os.path.isfile(bat))

    # 回执消费（模拟重启后 main.py 调用）
    receipt = updater.consume_update_receipt(install_dir)
    check("full: 回执消费 ok=True", bool(receipt) and receipt.get("ok") is True, str(receipt))
    check("full: 回执消费后 pending 删除",
          not os.path.isfile(os.path.join(install_dir, updater.PENDING_FLAG)))
    check("full: 回执消费后 result.log 删除",
          not os.path.isfile(os.path.join(install_dir, updater.RESULT_LOG)))

    shutil.rmtree(root, ignore_errors=True)


# ==================== incremental 模式 ====================
def test_incremental():
    print("\n===== INCREMENTAL 模式 =====")
    root, install_dir, staging_dir, main_exe = build_env("inc")
    write(os.path.join(install_dir, main_exe), "old-exe")
    write(os.path.join(install_dir, "_internal", "keep.dll"), "keepdll")
    write(os.path.join(install_dir, "config", "misc.json"), '{"user":"keep-me"}')
    write(os.path.join(install_dir, "database", "local.db"), "old-db")

    # 增量 staging：只含变更文件（主 exe + 一个新 dll），不含 config/database
    write(os.path.join(staging_dir, main_exe), "new-exe")
    write(os.path.join(staging_dir, "_internal", "patch.dll"), "patchdll")

    pid, proc = fake_sleep_pid()
    work_dir = os.path.join(root, "_work")
    bat = gen_bat(install_dir, staging_dir, main_exe, pid, "incremental", work_dir)
    try:
        run_bat(bat)
    finally:
        try:
            proc.kill()
        except Exception:
            pass

    time.sleep(0.5)
    check("inc: 主 exe 已覆盖", read(os.path.join(install_dir, main_exe)) == "new-exe")
    check("inc: 新 patch.dll 合并进来",
          os.path.isfile(os.path.join(install_dir, "_internal", "patch.dll")))
    check("inc: 原有 keep.dll 保留（合并不删）",
          os.path.isfile(os.path.join(install_dir, "_internal", "keep.dll")))
    check("inc: config 未被覆盖",
          read(os.path.join(install_dir, "config", "misc.json")) == '{"user":"keep-me"}')
    check("inc: database 未被覆盖",
          os.path.isfile(os.path.join(install_dir, "database", "local.db")))
    rj = read(os.path.join(install_dir, updater.RESULT_LOG))
    try:
        ok = json.loads(rj).get("ok") is True
    except Exception:
        ok = False
    check("inc: update_result.log = ok:true", ok, rj)
    check("inc: staging 已清理", not os.path.isdir(staging_dir))

    shutil.rmtree(root, ignore_errors=True)


# ==================== 失败路径：staging 缺主 exe ====================
def test_fail_noexe():
    print("\n===== 失败路径（staging 缺主 exe） =====")
    root, install_dir, staging_dir, main_exe = build_env("fail")
    write(os.path.join(install_dir, main_exe), "old-exe")
    write(os.path.join(install_dir, "config", "misc.json"), '{"user":"keep-me"}')
    # staging 故意不放主 exe
    write(os.path.join(staging_dir, "_internal", "new.dll"), "newdll")
    updater.mark_update_pending(install_dir, {"version": "9.9.9", "mode": "full"})

    pid, proc = fake_sleep_pid()
    work_dir = os.path.join(root, "_work")
    bat = gen_bat(install_dir, staging_dir, main_exe, pid, "full", work_dir)
    try:
        run_bat(bat)
    finally:
        try:
            proc.kill()
        except Exception:
            pass

    time.sleep(0.5)
    check("fail: 安装目录未被破坏（仍是旧 exe）",
          read(os.path.join(install_dir, main_exe)) == "old-exe")
    check("fail: config 完好", os.path.isfile(os.path.join(install_dir, "config", "misc.json")))
    rj = read(os.path.join(install_dir, updater.RESULT_LOG))
    try:
        d = json.loads(rj)
        ok = d.get("ok") is False and "main exe" in str(d.get("error", ""))
    except Exception:
        ok = False
    check("fail: update_result.log = ok:false + 原因", ok, rj)
    check("fail: _new 残留已清理", not os.path.isdir(install_dir + "_new"))

    # 失败回执消费：绝不重试，pending 清掉
    receipt = updater.consume_update_receipt(install_dir)
    check("fail: 回执消费 ok=False", bool(receipt) and receipt.get("ok") is False, str(receipt))
    check("fail: 回执消费后 pending 删除（防死循环）",
          not os.path.isfile(os.path.join(install_dir, updater.PENDING_FLAG)))

    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test_full()
    test_incremental()
    test_fail_noexe()
    passed = sum(1 for _, ok in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n===== 汇总：{passed}/{total} PASS =====")
    if passed != total:
        print("失败项：")
        for n, ok in RESULTS:
            if not ok:
                print("  -", n)
        sys.exit(1)
    print("全部通过")
