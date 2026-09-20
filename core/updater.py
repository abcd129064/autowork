# -*- coding: utf-8 -*-
"""自动更新核心逻辑（纯逻辑层，无 Qt 依赖，可单测）

链路（docs/auto_update_research.md 方案 A）：
  检查更新 → 下载更新包 → 校验 → 解压 staging → 拉起外部 updater → 主程序退出
  → updater 替换（等退出/杀 frpc/rename 备份/robocopy）→ 重启 → 回执消费

latest.json 支持两种形态：
1. channels 形态（推荐）：{"channels": {"autowork": {...}, "aftersale": {...}}}
2. 扁平形态（兼容）：{"version": ..., "url": ..., "sha256": ...}
channel 条目结构：
  {"version": "3.11.280", "released_at": "...", "notes": "...",
   "min_version": "",                      # 低于此版本禁止增量，必须全量
   "package": {"mode": "full",             # full=整包 zip；incremental=增量
               "url": "AutoWork-x.zip", "sha256": "...", "size": 0},
   "files": [{"path": "_internal/a.dll", "sha256": "...", "size": 0}]}

关键文件名（均在 exe 目录）：
  version.json       构建期版本落盘（build_exe.py 写入）
  .update-pending    安装已发起标记（updater 启动前写，重启消费后删）
  update_result.log  updater 回执（JSON）
"""

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

logger = logging.getLogger(__name__)

# 生产默认更新源（nginx 静态目录 /opt/aftersale-web/dist/update/，零 nginx 改动）
DEFAULT_UPDATE_BASE_URL = "http://49.235.34.253/update"
# 渠道名
CHANNEL_AUTOWORK = "autowork"
CHANNEL_AFTERSALE = "aftersale"

PENDING_FLAG = ".update-pending"
RESULT_LOG = "update_result.log"
STAGING_DIRNAME = "_update_staging"
_BACKUP_SUFFIX = "_old"

# 用户数据目录：更新覆盖时排除（config 分域配置/logs/database 本地库）
EXCLUDE_DIRS = ("config", "logs", "database")


# ==================== 版本比较 ====================

_VER_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def parse_version(v: str):
    """解析 '3.11.273-branch' → (3, 11, 273)；分支后缀不参与比较。

    用 search 而非 match：容忍 'v3.11.273' 这类前缀。若用 match，发布时
    latest.json 误写成 'v3.11.273' 会被解析成 (0,0,0)，客户端静默判定
    「远端比自己旧」从而**永不更新**——比抛错更难排查。
    解析失败返回 (0, 0, 0)（永远旧于任何正常版本）。"""
    m = _VER_RE.search(str(v or "").strip())
    if not m:
        return (0, 0, 0)
    return tuple(int(x) for x in m.groups())


def is_newer(remote: str, local: str) -> bool:
    """远端版本是否新于本地"""
    return parse_version(remote) > parse_version(local)


# ==================== latest.json 获取 ====================

def fetch_latest(base_url: str, channel: str = CHANNEL_AUTOWORK,
                 timeout: float = 8.0) -> dict:
    """拉取 latest.json 并抽取指定 channel 条目；网络/格式错误抛异常

    兼容 channels 形态与扁平形态；扁平形态视为 autowork 渠道。

    ⚠️ 必须校验响应确实是 JSON：生产 nginx 的 SPA try_files 回退会把
    index.html 以 **HTTP 200 + text/html** 返回给任何不存在的路径，
    若不拦截，用户只会看到含糊的「Expecting value: line 1 column 1」。
    """
    import requests
    url = base_url.rstrip("/") + "/latest.json"
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    ctype = str(resp.headers.get("Content-Type") or "").lower()
    body = resp.content.lstrip()[:512]
    if "json" not in ctype and not body.startswith(b"{"):
        snippet = body[:80].decode("utf-8", "replace").replace("\n", " ")
        raise ValueError(
            f"更新源返回的不是 JSON（Content-Type={ctype or '未知'}），"
            f"疑似 nginx 回退到首页 HTML：{snippet}…；请检查 {url}")
    try:
        data = resp.json()
    except Exception as e:
        raise ValueError(f"latest.json 解析失败：{e}") from e
    if "channels" in data:
        entry = data["channels"].get(channel)
        if entry is None:
            raise ValueError(f"latest.json 无渠道 {channel}")
    else:
        if channel != CHANNEL_AUTOWORK:
            raise ValueError(f"扁平 latest.json 仅支持 {CHANNEL_AUTOWORK} 渠道")
        entry = data
    entry = dict(entry)
    pkg = entry.get("package") or {}
    # 扁平字段归一化到 package
    if not pkg and entry.get("url"):
        pkg = {"mode": "full", "url": entry["url"],
               "sha256": entry.get("sha256", ""),
               "size": int(entry.get("size") or 0)}
        entry["package"] = pkg
    return entry


def check_update(base_url: str, local_version: str,
                 channel: str = CHANNEL_AUTOWORK) -> dict | None:
    """检查更新：有新版本返回 latest 条目，否则 None；网络错误向上抛

    返回条目附加 _resolved_url（package.url 拼 base_url 的绝对地址）。"""
    entry = fetch_latest(base_url, channel)
    remote = str(entry.get("version") or "")
    if not is_newer(remote, local_version):
        return None
    pkg = entry.get("package") or {}
    url = pkg.get("url") or ""
    if url and not url.startswith(("http://", "https://")):
        url = base_url.rstrip("/") + "/" + url.lstrip("/")
    entry["_resolved_url"] = url
    entry["_remote_version"] = remote
    return entry


# ==================== 下载与校验 ====================

def sha256_of_file(path: str, chunk: int = 1024 * 256) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def download_file(url: str, dest: str, expect_sha256: str = "",
                  timeout: float = 20.0, progress_cb=None,
                  should_stop=None) -> str:
    """流式下载到 dest.part，完成后校验 sha256 并原子改名为 dest

    progress_cb(done_bytes, total_bytes)；should_stop() 为 True 时中止
    （删除 .part 并抛 InterruptedError）。校验失败抛 ValueError。

    ⚠️ 生产 nginx 的 SPA try_files 会把不存在的 .zip 以 200+text/html 返回，
    若不拦截，用户只会看到误导性的「sha256 校验失败」而查不出是包没传上去。
    """
    import requests
    part = dest + ".part"
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    try:
        with requests.get(url, stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            ctype = str(resp.headers.get("Content-Type") or "").lower()
            if "html" in ctype:
                raise ValueError(
                    f"更新包下载地址返回 HTML（Content-Type={ctype}），"
                    f"包可能未上传成功或更新源配置有误：{url}")
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(part, "wb") as f:
                for chunk in resp.iter_content(chunk_size=256 * 1024):
                    if should_stop is not None and should_stop():
                        raise InterruptedError("用户取消下载")
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb is not None:
                        progress_cb(done, total)
        if expect_sha256:
            actual = sha256_of_file(part)
            if actual.lower() != expect_sha256.lower():
                raise ValueError(
                    f"sha256 校验失败：期望 {expect_sha256[:12]}…，实际 {actual[:12]}…")
        if os.path.exists(dest):
            os.remove(dest)
        os.replace(part, dest)
        return dest
    finally:
        if os.path.exists(part):
            try:
                os.remove(part)
            except OSError:
                pass


def verify_and_extract_zip(zip_path: str, dest_dir: str) -> str:
    """zip 完整性校验 + 解压到 dest_dir（返回解压根）；坏包抛 ValueError"""
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        if bad is not None:
            raise ValueError(f"zip 内文件损坏：{bad}")
        names = zf.namelist()
        # 防 zip-slip：任何条目路径不得逃出 dest_dir
        root = os.path.abspath(dest_dir)
        for n in names:
            target = os.path.abspath(os.path.join(root, n))
            if not (target == root or target.startswith(root + os.sep)):
                raise ValueError(f"zip 含越界路径：{n}")
        zf.extractall(dest_dir)
    return dest_dir


def flatten_extract_root(dest_dir: str, main_exe: str = "") -> str:
    """返回解压产物的真实安装根目录

    PyInstaller onedir 整包 zip 通常带一层顶级目录（如 AutoWork/），
    updater 需要「直接包含主 exe 的目录」作为 robocopy 源。检测：
    dest_dir 下仅一个子目录且无文件 → 若主 exe 在其中（或未指定
    main_exe），返回该子目录；否则原样返回。"""
    try:
        entries = os.listdir(dest_dir)
    except OSError:
        return dest_dir
    if len(entries) == 1:
        sub = os.path.join(dest_dir, entries[0])
        if os.path.isdir(sub):
            if not main_exe or os.path.isfile(os.path.join(sub, main_exe)):
                return sub
    return dest_dir


# ==================== S5 增量模式 ====================

def manifest_diff(files: list, local_root: str) -> list:
    """对比 manifest files[] 与本地文件 sha256，返回需要下载的条目列表

    本地缺失或哈希不一致 → 需下载。files 条目：{path, sha256, size}。"""
    need = []
    for entry in files or []:
        rel = str(entry.get("path") or "")
        want = str(entry.get("sha256") or "").lower()
        if not rel or not want:
            continue
        local = os.path.join(local_root, rel.replace("/", os.sep))
        if os.path.isfile(local) and sha256_of_file(local) == want:
            continue
        need.append(entry)
    return need


def download_incremental(base_url: str, files: list, staging_dir: str,
                         progress_cb=None, should_stop=None) -> int:
    """把 manifest diff 出的文件逐个下载到 staging（保持相对路径），返回数量

    progress_cb 收到的是全局累计进度 (done_bytes, total_bytes)：
    total 取各条目 size 之和，跨文件累加已完成字节。"""
    base = base_url.rstrip("/") + "/files/"
    total = sum(int(e.get("size") or 0) for e in files or [])
    count = 0
    done_base = 0
    for entry in files:
        rel = str(entry["path"]).replace("/", os.sep)
        url = entry.get("url") or (base + str(entry["path"]))
        if not url.startswith(("http://", "https://")):
            url = base_url.rstrip("/") + "/" + url.lstrip("/")
        dest = os.path.join(staging_dir, rel)

        def _cb(done, _t, _b=done_base):
            if progress_cb is not None:
                progress_cb(_b + done, total)

        download_file(url, dest, expect_sha256=str(entry.get("sha256") or ""),
                      progress_cb=_cb, should_stop=should_stop)
        # size 缺省时按实际落盘字节累计，保证全局进度单调不回退
        done_base += int(entry.get("size") or 0) or (
            os.path.getsize(dest) if os.path.isfile(dest) else 0)
        count += 1
    return count


# ==================== S4 updater 脚本生成与拉起 ====================
#
# 设计要点（全部路径在生成期内联进 bat，运行期零参数、零引号风险）：
#   1. bat 自身放 %TEMP%\autowork_update\，**不放安装目录**——整目录 rename
#      时不能把 updater 自己卷进去（事故模式：更新器替换自身失败）。
#   2. full 模式 = 同级目录原子切换：staging → INSTALL_new → 三向 move 换位，
#      任何一步失败都停在原状，安装目录不会被写坏。
#   3. 用户数据（config/logs/database）在换位后从 _old 回迁到新目录。
#   4. 结果一律写 INSTALL_DIR\update_result.log（JSON），主程序重启后消费。

_BAT_TEMPLATE = r"""@echo off
rem AutoWork updater - generated by main app, do not edit
setlocal EnableExtensions EnableDelayedExpansion
rem CWD must leave INSTALL_DIR, otherwise the dir is locked and cannot be renamed
cd /d "%TEMP%"

rem All external tools are called by ABSOLUTE System32 path: if PATH is polluted
rem (GNU coreutils shipping find.exe/timeout.exe), the wait loop below would
rem silently break and we would rename the install dir while the app still runs.
set "SYS32=%SystemRoot%\System32"

set "INSTALL_DIR=__INSTALL_DIR__"
set "STAGING_DIR=__STAGING_DIR__"
set "NEW_DIR=__NEW_DIR__"
set "BAK_DIR=__BAK_DIR__"
set "MAIN_EXE=__MAIN_EXE__"
set "WAIT_PID=__WAIT_PID__"
set "MODE=__MODE__"
set "RESULT_LOG=__RESULT_LOG__"
set "ERRMSG="

rem ---- 1) wait for main process to exit (max 60s) ----
set /a N=0
:wait_loop
"%SYS32%\tasklist.exe" /FI "PID eq %WAIT_PID%" /NH 2>nul | "%SYS32%\find.exe" "%WAIT_PID%" >nul
if errorlevel 1 goto kill_holders
set /a N+=1
if %N% geq 60 goto kill_holders
rem ping used as a sleep: unlike `timeout` it does not require console stdin
"%SYS32%\ping.exe" -n 2 127.0.0.1 >nul
goto wait_loop

:kill_holders
rem ---- 2) kill child processes that hold files inside INSTALL_DIR ----
"%SYS32%\taskkill.exe" /IM frpc.exe /F >nul 2>&1
"%SYS32%\taskkill.exe" /IM aftersale.exe /F >nul 2>&1
"%SYS32%\taskkill.exe" /IM management.exe /F >nul 2>&1
"%SYS32%\ping.exe" -n 3 127.0.0.1 >nul

rem ---- 3) clear leftovers of a previous aborted attempt ----
if exist "%NEW_DIR%" rd /s /q "%NEW_DIR%" >nul 2>&1
if exist "%BAK_DIR%" rd /s /q "%BAK_DIR%" >nul 2>&1
if exist "%RESULT_LOG%" del /f /q "%RESULT_LOG%" >nul 2>&1

if /I "%MODE%"=="incremental" goto incremental

rem ================= full mode: atomic dir swap =================
"%SYS32%\Robocopy.exe" "%STAGING_DIR%" "%NEW_DIR%" /E /NFL /NDL /NJH /NJS /R:2 /W:2 >nul
if errorlevel 8 goto fail_copy
if not exist "%NEW_DIR%\%MAIN_EXE%" goto fail_noexe

rem user data + pending flag must survive the swap, so seed them into NEW_DIR
for %%D in (__EXCLUDE_DIRS__) do if exist "%INSTALL_DIR%\%%~D" "%SYS32%\Robocopy.exe" "%INSTALL_DIR%\%%~D" "%NEW_DIR%\%%~D" /E /NFL /NDL /NJH /NJS /R:1 /W:1 >nul
if exist "%INSTALL_DIR%\__PENDING_FLAG__" copy /y "%INSTALL_DIR%\__PENDING_FLAG__" "%NEW_DIR%\__PENDING_FLAG__" >nul

move "%INSTALL_DIR%" "%BAK_DIR%" >nul
if errorlevel 1 goto fail_rename
move "%NEW_DIR%" "%INSTALL_DIR%" >nul
if errorlevel 1 goto rollback
goto restart

:rollback
move "%BAK_DIR%" "%INSTALL_DIR%" >nul 2>&1
set "ERRMSG=cannot move new version into place"
goto fail

rem ================= incremental mode: merge in place =================
:incremental
"%SYS32%\Robocopy.exe" "%STAGING_DIR%" "%INSTALL_DIR%" /E /XD config logs database /XF __PENDING_FLAG__ update_result.log /NFL /NDL /NJH /NJS /R:2 /W:2 >nul
if errorlevel 8 goto fail_copy
if not exist "%INSTALL_DIR%\%MAIN_EXE%" goto fail_noexe
goto restart

rem ================= failure paths =================
:fail_copy
set "ERRMSG=copy update package failed"
goto fail
:fail_noexe
set "ERRMSG=main exe missing in update package"
goto fail
:fail_rename
set "ERRMSG=cannot rename install dir (locked by another process)"
goto fail
:fail
if not defined ERRMSG set "ERRMSG=unknown error"
> "%RESULT_LOG%" echo {"ok": false, "error": "%ERRMSG%"}
goto cleanup

:restart
rem ---- 4) verify what landed, then relaunch ----
if not exist "%INSTALL_DIR%\%MAIN_EXE%" goto fail_noexe_after
> "%RESULT_LOG%" echo {"ok": true}
start "" /D "%INSTALL_DIR%" "%INSTALL_DIR%\%MAIN_EXE%"
goto cleanup

:fail_noexe_after
> "%RESULT_LOG%" echo {"ok": false, "error": "main exe missing after update"}

:cleanup
rem ---- 5) cleanup (failures here never change the update result) ----
if exist "%BAK_DIR%" rd /s /q "%BAK_DIR%" >nul 2>&1
if exist "%NEW_DIR%" rd /s /q "%NEW_DIR%" >nul 2>&1
if exist "%STAGING_DIR%" rd /s /q "%STAGING_DIR%" >nul 2>&1
endlocal
(goto) 2>nul & del "%~f0" 2>nul
exit /b
"""

# ⚠️ 不能用 raw 三引号表达 VBScript 的 """" 转义：Python 会把前三个引号
# 当成字符串结束符，生成的 vbs 缺引号 → 「Microsoft VBScript 编译器错误:
# 语法错误」，且 wscript 静默失败（updater 根本没跑）。改用 Chr(34) 拼引号。
_VBS_TEMPLATE = """' AutoWork updater - hidden launcher (running the bat directly flashes a console)
Set ws = CreateObject("Wscript.Shell")
q = Chr(34)
ws.Run q & WScript.Arguments(0) & q, 0, False
"""

# full 模式换位后需要从旧目录回迁的用户数据目录（与 EXCLUDE_DIRS 一致）
_BAT_EXCLUDE_DIRS = ("config", "logs", "database")


def build_bat(install_dir: str, staging_dir: str, main_exe: str,
              wait_pid: int, mode: str, result_log: str = "",
              new_dir: str = "", bak_dir: str = "") -> str:
    """渲染 updater bat 内容（所有路径生成期内联，运行期不接收参数）

    new_dir/bak_dir 缺省按 install_dir 同级派生 `<name>_new` / `<name>_old`。
    返回的字符串由 write_updater_scripts 以 GBK 落盘（cmd 默认代码页非 UTF-8）。
    """
    install_dir = os.path.normpath(install_dir)
    if not result_log:
        result_log = os.path.join(install_dir, RESULT_LOG)
    if not new_dir:
        new_dir = install_dir + "_new"
    if not bak_dir:
        bak_dir = install_dir + _BACKUP_SUFFIX
    out = _BAT_TEMPLATE
    out = out.replace("__INSTALL_DIR__", install_dir)
    out = out.replace("__STAGING_DIR__", os.path.normpath(staging_dir))
    out = out.replace("__NEW_DIR__", new_dir)
    out = out.replace("__BAK_DIR__", bak_dir)
    out = out.replace("__MAIN_EXE__", main_exe)
    out = out.replace("__WAIT_PID__", str(int(wait_pid)))
    out = out.replace("__MODE__", mode)
    out = out.replace("__RESULT_LOG__", result_log)
    out = out.replace("__PENDING_FLAG__", PENDING_FLAG)
    out = out.replace("__EXCLUDE_DIRS__",
                      " ".join(f'"{d}"' for d in _BAT_EXCLUDE_DIRS))
    return out


def write_updater_scripts(work_dir: str, install_dir: str, staging_dir: str,
                          main_exe: str, wait_pid: int, mode: str) -> tuple:
    """把 update.bat + launcher.vbs 写到 work_dir（%TEMP%，安装目录之外）

    返回 (bat_path, vbs_path)。"""
    os.makedirs(work_dir, exist_ok=True)
    bat_path = os.path.join(work_dir, "update.bat")
    # GBK 落盘：cmd.exe 默认代码页非 UTF-8，且 BOM 会让首行 @echo off 解析失败
    with open(bat_path, "w", encoding="gbk", errors="replace",
              newline="\r\n") as f:
        f.write(build_bat(install_dir, staging_dir, main_exe, wait_pid, mode))
    vbs_path = os.path.join(work_dir, "launcher.vbs")
    with open(vbs_path, "w", encoding="gbk", errors="replace",
              newline="\r\n") as f:
        f.write(_VBS_TEMPLATE)
    return bat_path, vbs_path




def mark_update_pending(app_dir: str, info: dict):
    """写安装发起标记（重启后消费；防失败死循环的关键——见 consume）"""
    try:
        with open(os.path.join(app_dir, PENDING_FLAG), "w",
                  encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False)
    except OSError as e:
        logger.warning("写 .update-pending 失败: %s", e)


def consume_update_receipt(app_dir: str) -> dict | None:
    """启动时消费更新回执；返回 {'ok': bool, 'version': str, 'error': str}

    - 无 .update-pending → None（正常启动）
    - 有 pending + result.log → 读回执，删除两者后返回
    - 有 pending 但无 result.log（updater 没跑完/被杀）→ 返回失败回执并
      清理 pending（绝不重试安装，防「每次启动重试失败」死循环——
      dev.to 复盘的头号事故模式）
    """
    pending_path = os.path.join(app_dir, PENDING_FLAG)
    if not os.path.isfile(pending_path):
        return None
    result_path = os.path.join(app_dir, RESULT_LOG)
    receipt = {"ok": False, "version": "", "error": ""}
    try:
        with open(pending_path, "r", encoding="utf-8") as f:
            pending = json.load(f)
        receipt["version"] = str(pending.get("version") or "")
    except Exception:
        pending = {}
    if os.path.isfile(result_path):
        try:
            with open(result_path, "r", encoding="utf-8") as f:
                raw = f.read().strip()
            res = json.loads(raw) if raw else {}
            receipt["ok"] = bool(res.get("ok"))
            receipt["error"] = str(res.get("error") or "")
        except Exception as e:
            receipt["error"] = f"回执解析失败: {e}"
        try:
            os.remove(result_path)
        except OSError:
            pass
    else:
        receipt["error"] = "更新进程未完成（未找到回执）"
    try:
        os.remove(pending_path)
    except OSError:
        pass
    # 清理残留 staging（防占盘）
    staging = os.path.join(app_dir, STAGING_DIRNAME)
    if os.path.isdir(staging):
        shutil.rmtree(staging, ignore_errors=True)
    return receipt


def launch_updater(app_dir: str, staging_dir: str, main_exe: str,
                   mode: str = "full", pending_info: dict | None = None,
                   pid: int | None = None, work_dir: str = "") -> tuple:
    """写 pending 标记 → 生成 updater 脚本 → 拉起（vbs 隐藏窗口）

    返回 (bat_path, vbs_path, cmd)；cmd 为实际执行的命令列表。
    调用方成功后应尽快退出主程序（QApplication.quit / sys.exit）。
    拉起失败抛 OSError（调用方提示用户并保持运行）。"""
    pid = os.getpid() if pid is None else int(pid)
    work_dir = work_dir or os.path.join(tempfile.gettempdir(), "autowork_update")
    bat_path, vbs_path = write_updater_scripts(
        work_dir, app_dir, staging_dir, main_exe, pid, mode)
    mark_update_pending(app_dir, pending_info or {})
    # 路径已全部内联进 bat，运行期只传 bat 自身路径一个参数
    cmd = ["wscript.exe", "//nologo", vbs_path, bat_path]
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | \
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(cmd, creationflags=flags)
    except OSError:
        # vbs 拉起失败回退：直接跑 bat（会闪黑窗但功能可用）
        cmd = ["cmd.exe", "/c", bat_path]
        subprocess.Popen(cmd, creationflags=flags)
    return bat_path, vbs_path, cmd


def launch_updater_and_exit(app_dir: str, staging_dir: str, main_exe: str,
                            mode: str = "full", pending_info: dict | None = None):
    """launch_updater 的兼容封装：拉起后返回 True"""
    launch_updater(app_dir, staging_dir, main_exe, mode, pending_info)
    return True


def resolve_mode(entry: dict, local_version: str) -> str:
    """决定本次更新用 full 还是 incremental

    entry.package.mode 为 incremental 时，若本地版本低于 entry.min_version，
    说明中间跨了多个版本、增量清单不足以保证一致 → 强制降级为 full。
    """
    pkg = entry.get("package") or {}
    mode = str(pkg.get("mode") or "full").lower()
    if mode != "incremental":
        return "full"
    min_ver = str(entry.get("min_version") or "").strip()
    if min_ver and parse_version(local_version) < parse_version(min_ver):
        return "full"
    return "incremental"


def resolve_package_url(base_url: str, entry: dict) -> str:
    """package.url → 绝对下载地址（相对路径按 base_url 拼接）"""
    pkg = entry.get("package") or {}
    url = entry.get("_resolved_url") or pkg.get("url") or ""
    url = str(url)
    if url and not url.startswith(("http://", "https://")):
        url = base_url.rstrip("/") + "/" + url.lstrip("/")
    return url


def prepare_update(base_url: str, entry: dict, app_dir: str,
                   main_exe: str = "", progress_cb=None, should_stop=None,
                   local_version: str = ""):
    """下载 + 校验 + 解压 staging（full/incremental 两种 mode）

    返回 {"mode", "staging_dir", "zip_path", "files_count"}；调用方随后交给
    launch_updater。zip 包下载到 %TEMP%\\autowork_update\\pkg，staging 解压到
    安装目录旁的 `_update_staging`（同卷，updater move 才是瞬时的）。
    异常向上抛（ValueError=校验失败/清单为空，InterruptedError=用户取消）。"""
    pkg = entry.get("package") or {}
    mode = resolve_mode(entry, local_version)
    url = resolve_package_url(base_url, entry)
    if mode == "full" and not url:
        raise ValueError("更新条目缺少下载地址")

    work_dir = os.path.join(tempfile.gettempdir(), "autowork_update")
    os.makedirs(work_dir, exist_ok=True)
    # staging 放安装目录同级：与安装目录同卷，robocopy/move 才是本地瞬时操作
    staging_dir = os.path.join(os.path.dirname(os.path.normpath(app_dir)) or ".",
                               STAGING_DIRNAME)
    if os.path.isdir(staging_dir):
        shutil.rmtree(staging_dir, ignore_errors=True)
    os.makedirs(staging_dir, exist_ok=True)
    extract_root = os.path.join(work_dir, "extract")

    def _cleanup_on_fail():
        for d in (staging_dir, extract_root):
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)

    files_count = 0
    zip_path = ""
    try:
        if mode == "incremental":
            files = entry.get("files") or []
            need = manifest_diff(files, app_dir)
            if not need:
                raise ValueError("增量清单与本地一致，无需更新")
            files_count = download_incremental(base_url, need, staging_dir,
                                              progress_cb=progress_cb,
                                              should_stop=should_stop)
        else:
            zip_path = os.path.join(work_dir, os.path.basename(url.split("?")[0])
                                    or "update.zip")
            download_file(url, zip_path,
                          expect_sha256=str(pkg.get("sha256") or ""),
                          progress_cb=progress_cb, should_stop=should_stop)
            if os.path.isdir(extract_root):
                shutil.rmtree(extract_root, ignore_errors=True)
            verify_and_extract_zip(zip_path, extract_root)
            # onedir zip 常带一层顶级目录，取真正含主 exe 的那层作为安装根
            real_root = flatten_extract_root(extract_root, main_exe)
            shutil.copytree(real_root, staging_dir, dirs_exist_ok=True)
            files_count = sum(len(f) for _, _, f in os.walk(staging_dir))
    except BaseException:
        # 校验失败/坏包/用户取消：绝不留脏 staging（否则 consume 时才发现，
        # 且占盘）。zip 包保留以便重试复用（下次下载会覆盖）。
        _cleanup_on_fail()
        raise
    return {"mode": mode, "staging_dir": staging_dir, "zip_path": zip_path,
            "files_count": files_count}


# ==================== 环境信息 ====================

def get_install_info() -> dict:
    """当前安装环境信息：{app_dir, main_exe, frozen, local_version}"""
    from core.app_paths import get_app_dir
    from core.version import get_app_version
    app_dir = get_app_dir()
    frozen = bool(getattr(sys, "frozen", False))
    main_exe = os.path.basename(sys.executable) if frozen else ""
    return {"app_dir": app_dir, "main_exe": main_exe, "frozen": frozen,
            "local_version": get_app_version()}


def get_update_base_url(settings_get=None) -> str:
    """更新源地址：settings.update_base_url 优先，默认生产地址"""
    url = ""
    try:
        if settings_get is not None:
            url = str(settings_get("update_base_url") or "").strip()
        else:
            from core import app_settings
            url = str(app_settings.get("update_base_url") or "").strip()
    except Exception:
        url = ""
    return url or DEFAULT_UPDATE_BASE_URL
