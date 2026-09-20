# -*- coding: utf-8 -*-
"""S2+S3+S4+S5 集成仿真：本地发布 → http 源 → 客户端检查/下载/解压 → bat 替换

完整复刻生产链路（唯一差别：服务器换成本地 http.server，上传换成文件拷贝）：

  1. tools/publish_update.py --pack-only  打包 v1 全量（验证排除 config/logs/database）
  2. 改产物 → 打包 v2 增量（验证 diff 只挑变更文件）
  3. 组装本地更新源目录（latest.json + packages/ + files/）并起 http.server
  4. 客户端 core.updater.check_update：版本比较、URL 解析
  5. 客户端 prepare_update：下载 + sha256 校验 + 解压 staging
     - full 包带顶级目录（onedir zip 常态）→ flatten_extract_root 归一化
     - incremental 包 → manifest_diff 只下变更文件
  6. min_version 强制降级：旧客户端遇到 incremental 应自动转 full
  7. staging 交给真机 bat 完成替换（复用 sim_updater_e2e 的验证方式）
  8. 篡改包体 → sha256 校验必须失败（安全断言）

每条断言打印 PASS/FAIL，末尾汇总。全程只在 %TEMP% 与 out/_sim 下操作。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from http.server import HTTPServer, SimpleHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))   # tools/update_sim/ → 项目根
sys.path.insert(0, ROOT)
from core import updater
from tools import publish_update as pub

RESULTS = []


def check(name, cond, extra=""):
    RESULTS.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))


def write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def make_source(base, version, dll_content):
    """造假 onedir 产物（含必须被排除的用户数据目录）"""
    src = os.path.join(base, "AutoWork")
    shutil.rmtree(src, ignore_errors=True)
    write(os.path.join(src, "AutoWork.exe"), f"exe-{version}")
    write(os.path.join(src, "version.json"), json.dumps({"version": version}))
    write(os.path.join(src, "_internal", "a.dll"), dll_content)
    write(os.path.join(src, "_internal", "b.pyd"), "pyd-unchanged")
    write(os.path.join(src, "config", "misc.json"), '{"user":"KEEP"}')
    write(os.path.join(src, "logs", "run.log"), "SHOULD-NOT-PACK")
    write(os.path.join(src, "database", "x.db"), "SHOULD-NOT-PACK")
    write(os.path.join(src, updater.PENDING_FLAG), "SHOULD-NOT-PACK")
    return src


def run_publish(argv):
    """跑 publish_update.main()，返回 returncode"""
    old = sys.argv
    sys.argv = ["publish_update.py"] + argv
    try:
        return pub.main()
    finally:
        sys.argv = old


def start_server(serve_dir):
    """在 serve_dir 上起 http.server（后台线程），返回 (base_url, server)"""
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *a, **kw):
            pass    # 抑制逐请求日志，只留断言输出

    handler = lambda *a, **kw: QuietHandler(*a, directory=serve_dir, **kw)
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return f"http://127.0.0.1:{port}/update", httpd


def main():
    work = os.path.join(tempfile.gettempdir(), f"aw_int_{os.getpid()}")
    shutil.rmtree(work, ignore_errors=True)
    out_dir = os.path.join(work, "out")
    serve_dir = os.path.join(work, "serve", "update")
    os.makedirs(serve_dir, exist_ok=True)
    os.makedirs(os.path.join(serve_dir, "packages"), exist_ok=True)
    os.makedirs(os.path.join(serve_dir, "files"), exist_ok=True)

    # ========== 1) v1 全量发布 ==========
    print("\n===== 1) v1 全量打包 =====")
    src = make_source(os.path.join(work, "v1"), "3.11.280", "dll-a-v1")
    rc = run_publish(["--source", src, "--version", "3.11.280",
                      "--out-dir", out_dir, "--pack-only",
                      "--notes", "第一版：修复启动闪屏"])
    check("v1 发布 rc=0", rc == 0, f"rc={rc}")
    v1_zip = os.path.join(out_dir, "autowork-3.11.280-full.zip")
    check("v1 zip 生成", os.path.isfile(v1_zip))
    with zipfile.ZipFile(v1_zip) as zf:
        names = set(zf.namelist())
    check("v1 zip 含主 exe", "AutoWork.exe" in names)
    check("v1 zip 排除 config", not any(n.startswith("config/") for n in names),
          str([n for n in names if n.startswith("config/")]))
    check("v1 zip 排除 logs", not any(n.startswith("logs/") for n in names))
    check("v1 zip 排除 database", not any(n.startswith("database/") for n in names))
    check("v1 zip 排除 .update-pending", updater.PENDING_FLAG not in names)
    v1_manifest = os.path.join(out_dir, "update_manifest_3.11.280.json")
    check("v1 manifest 生成", os.path.isfile(v1_manifest))

    # ========== 2) v2 增量发布 ==========
    print("\n===== 2) v2 增量打包 =====")
    src2 = make_source(os.path.join(work, "v2"), "3.11.281", "dll-a-v2-CHANGED")
    rc = run_publish(["--source", src2, "--version", "3.11.281",
                      "--out-dir", out_dir, "--pack-only", "--mode", "incremental",
                      "--base-manifest", v1_manifest,
                      "--min-version", "3.11.280",
                      "--notes", "热修：dll 崩溃"])
    check("v2 增量发布 rc=0", rc == 0, f"rc={rc}")
    v2_zip = os.path.join(out_dir, "autowork-3.11.281-incremental.zip")
    check("v2 增量 zip 生成", os.path.isfile(v2_zip))
    with zipfile.ZipFile(v2_zip) as zf:
        inc_names = set(zf.namelist())
    check("v2 增量只含变更文件（exe+version.json+a.dll）",
          inc_names == {"AutoWork.exe", "version.json", "_internal/a.dll"},
          str(sorted(inc_names)))
    check("v2 增量不含未变更的 b.pyd", "_internal/b.pyd" not in inc_names)

    # 增量模式缺 base-manifest 必须报错（防误发全量清单为增量）
    rc = run_publish(["--source", src2, "--version", "3.11.999",
                      "--out-dir", out_dir, "--pack-only", "--mode", "incremental"])
    check("增量缺 --base-manifest 被拒", rc == 2, f"rc={rc}")

    # ========== 3) 组装本地更新源 + 起服务 ==========
    print("\n===== 3) 组装更新源并起 http 服务 =====")
    # latest.json 指向 v2 增量（生产语义：latest 永远是最新版）
    latest = json.load(open(os.path.join(out_dir, "latest_autowork.json"),
                            encoding="utf-8"))
    entry2 = latest["channels"]["autowork"]
    # 为 full 场景准备一份指向 v1 全量包的 latest（带顶级目录，模拟 onedir zip）
    shutil.copy(v1_zip, os.path.join(serve_dir, "packages",
                                     os.path.basename(v1_zip)))
    shutil.copy(v2_zip, os.path.join(serve_dir, "packages",
                                     os.path.basename(v2_zip)))
    for rel, meta in [(f["path"], f) for f in entry2["files"]]:
        srcf = os.path.join(src2, rel.replace("/", os.sep))
        if os.path.isfile(srcf):
            dst = os.path.join(serve_dir, "files", rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(srcf, dst)
    # 再造一个「带顶级目录」的全量包（PyInstaller onedir zip 的常见形态）
    nested_zip = os.path.join(serve_dir, "packages", "autowork-3.11.282-full.zip")
    with zipfile.ZipFile(nested_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(os.path.join(src2, "AutoWork.exe"), "AutoWork/AutoWork.exe")
        zf.write(os.path.join(src2, "version.json"), "AutoWork/version.json")
        zf.write(os.path.join(src2, "_internal", "a.dll"), "AutoWork/_internal/a.dll")
        zf.write(os.path.join(src2, "_internal", "b.pyd"), "AutoWork/_internal/b.pyd")
    nested_sha = pub.sha256_file(nested_zip)
    latest_full_nested = {"updated_at": "2026-09-21T00:00:00", "channels": {
        "autowork": {
            "version": "3.11.282", "released_at": "2026-09-21T00:00:00",
            "notes": "整包更新（顶级目录形态）", "min_version": "",
            "package": {"mode": "full",
                        "url": "packages/autowork-3.11.282-full.zip",
                        "sha256": nested_sha,
                        "size": os.path.getsize(nested_zip)},
            "files": []}}}
    with open(os.path.join(serve_dir, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(latest_full_nested, f, ensure_ascii=False, indent=2)

    base_url, httpd = start_server(os.path.join(work, "serve"))
    print("  更新源:", base_url)

    try:
        # ========== 4) 客户端检查更新 ==========
        print("\n===== 4) 客户端 check_update =====")
        e = updater.check_update(base_url, "3.11.280", updater.CHANNEL_AUTOWORK)
        check("有新版本时返回条目", e is not None)
        check("远端版本正确", (e or {}).get("_remote_version") == "3.11.282",
              str((e or {}).get("_remote_version")))
        check("相对 url 解析为绝对地址",
              str((e or {}).get("_resolved_url", "")).startswith(base_url),
              str((e or {}).get("_resolved_url")))
        e_none = updater.check_update(base_url, "3.11.999")
        check("本地已是最新时返回 None", e_none is None)
        # 渠道不存在必须报错而不是静默当 autowork
        try:
            updater.fetch_latest(base_url, "nonexist")
            check("未知渠道报错", False)
        except ValueError:
            check("未知渠道报错", True)
        except Exception as ex:
            check("未知渠道报错", False, f"{type(ex).__name__}: {ex}")

        # ========== 5) full 下载解压（带顶级目录归一化） ==========
        print("\n===== 5) full 下载 + 解压 staging =====")
        fake_install = os.path.join(work, "install", "AutoWork")
        os.makedirs(fake_install, exist_ok=True)
        write(os.path.join(fake_install, "AutoWork.exe"), "old-exe")
        prog = []
        info = updater.prepare_update(base_url, e, fake_install,
                                      main_exe="AutoWork.exe",
                                      progress_cb=lambda d, t: prog.append((d, t)),
                                      local_version="3.11.280")
        check("prepare_update mode=full", info["mode"] == "full", info["mode"])
        st = info["staging_dir"]
        check("staging 目录已生成", os.path.isdir(st), st)
        check("staging 与安装目录同级（同卷，move 瞬时）",
              os.path.dirname(st) == os.path.dirname(os.path.normpath(fake_install)),
              st)
        check("顶级目录已归一化（主 exe 直接在 staging 根）",
              os.path.isfile(os.path.join(st, "AutoWork.exe")))
        check("staging 主 exe 内容为新版",
              open(os.path.join(st, "AutoWork.exe")).read() == "exe-3.11.281")
        check("staging 不含顶级 AutoWork/ 残留",
              not os.path.isdir(os.path.join(st, "AutoWork")))
        check("下载进度回调有触发", len(prog) > 0, f"{len(prog)} 次")
        check("进度回调 total>0 且单调不减",
              all(t > 0 for _, t in prog) and
              all(prog[i][0] <= prog[i + 1][0] for i in range(len(prog) - 1)))
        check("zip 已下载到 TEMP", os.path.isfile(info["zip_path"]), info["zip_path"])

        # ========== 6) 真机 bat 替换（用刚下载的 staging） ==========
        print("\n===== 6) staging → 真机 bat 替换 =====")
        updater.mark_update_pending(fake_install, {"version": "3.11.282"})
        # 旧目录用户数据，验证换位后回迁
        write(os.path.join(fake_install, "config", "misc.json"), '{"user":"KEEP"}')
        proc = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(5)"],
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        work_dir = os.path.join(work, "updater_work")
        bat, vbs = updater.write_updater_scripts(
            work_dir, fake_install, st, "AutoWork.exe", proc.pid, info["mode"])
        p = subprocess.run(["cmd.exe", "/c", bat], capture_output=True,
                           encoding="gbk", errors="replace", timeout=90,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        proc.kill()
        time.sleep(0.5)
        check("bat 执行 rc=0", p.returncode == 0, f"rc={p.returncode} err={p.stderr[:200]}")
        check("替换后主 exe 为新版本",
              open(os.path.join(fake_install, "AutoWork.exe")).read() == "exe-3.11.281")
        check("替换后 version.json 为新版本",
              "3.11.281" in open(os.path.join(fake_install, "version.json")).read())
        check("替换后用户数据 config 回迁",
              open(os.path.join(fake_install, "config", "misc.json")).read()
              == '{"user":"KEEP"}')
        rj = open(os.path.join(fake_install, updater.RESULT_LOG)).read().strip()
        check("回执 ok:true", json.loads(rj).get("ok") is True, rj)
        receipt = updater.consume_update_receipt(fake_install)
        check("重启后回执消费成功", bool(receipt) and receipt["ok"], str(receipt))

        # ========== 7) incremental：manifest_diff 只下变更文件 ==========
        print("\n===== 7) 增量模式（manifest_diff）=====")
        inc_entry = dict(entry2)
        inc_entry["_resolved_url"] = base_url + "/" + entry2["package"]["url"]
        # 本地装一个 v1（b.pyd 与 v2 相同，a.dll/exe/version.json 不同）
        inc_install = os.path.join(work, "install2", "AutoWork")
        os.makedirs(inc_install, exist_ok=True)
        write(os.path.join(inc_install, "AutoWork.exe"), "exe-3.11.280")
        write(os.path.join(inc_install, "version.json"),
              json.dumps({"version": "3.11.280"}))
        write(os.path.join(inc_install, "_internal", "a.dll"), "dll-a-v1")
        write(os.path.join(inc_install, "_internal", "b.pyd"), "pyd-unchanged")
        need = updater.manifest_diff(entry2["files"], inc_install)
        need_paths = sorted(f["path"] for f in need)
        check("manifest_diff 挑出 3 个变更文件",
              need_paths == ["AutoWork.exe", "_internal/a.dll", "version.json"],
              str(need_paths))
        check("manifest_diff 跳过未变更的 b.pyd",
              "_internal/b.pyd" not in need_paths)
        info2 = updater.prepare_update(base_url, inc_entry, inc_install,
                                       main_exe="AutoWork.exe",
                                       local_version="3.11.280")
        check("增量 prepare_update mode=incremental",
              info2["mode"] == "incremental", info2["mode"])
        check("增量 staging 只含变更文件", info2["files_count"] == 3,
              f"{info2['files_count']}")
        st2 = info2["staging_dir"]
        check("增量 staging 主 exe 就位",
              os.path.isfile(os.path.join(st2, "AutoWork.exe")))
        check("增量 staging 不含 b.pyd",
              not os.path.isfile(os.path.join(st2, "_internal", "b.pyd")))
        # 增量合并：b.pyd 必须保留
        proc2 = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(5)"],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        bat2, _ = updater.write_updater_scripts(
            os.path.join(work, "updater_work2"), inc_install, st2,
            "AutoWork.exe", proc2.pid, "incremental")
        subprocess.run(["cmd.exe", "/c", bat2], capture_output=True,
                       encoding="gbk", errors="replace", timeout=90,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        proc2.kill()
        time.sleep(0.5)
        check("增量后主 exe 已更新",
              open(os.path.join(inc_install, "AutoWork.exe")).read() == "exe-3.11.281")
        check("增量后 b.pyd 保留",
              open(os.path.join(inc_install, "_internal", "b.pyd")).read()
              == "pyd-unchanged")
        check("增量后 a.dll 已更新",
              open(os.path.join(inc_install, "_internal", "a.dll")).read()
              == "dll-a-v2-CHANGED")

        # ========== 8) min_version 强制降级 full ==========
        print("\n===== 8) min_version 降级 =====")
        check("resolve_mode: 满足 min_version → incremental",
              updater.resolve_mode(inc_entry, "3.11.280") == "incremental")
        check("resolve_mode: 低于 min_version → full",
              updater.resolve_mode(inc_entry, "3.11.100") == "full")
        check("resolve_mode: 无 min_version → incremental",
              updater.resolve_mode({"package": {"mode": "incremental"}}, "1.0.0")
              == "incremental")
        check("resolve_mode: package.mode=full → full",
              updater.resolve_mode({"package": {"mode": "full"}}, "9.9.9") == "full")

        # ========== 9) sha256 篡改必须被拒 ==========
        print("\n===== 9) 完整性校验（安全断言）=====")
        bad_entry = json.loads(json.dumps(latest_full_nested["channels"]["autowork"]))
        bad_entry["_resolved_url"] = base_url + "/" + bad_entry["package"]["url"]
        bad_entry["package"]["sha256"] = "0" * 64   # 伪造哈希
        bad_install = os.path.join(work, "install3", "AutoWork")
        os.makedirs(bad_install, exist_ok=True)
        try:
            updater.prepare_update(base_url, bad_entry, bad_install,
                                   main_exe="AutoWork.exe",
                                   local_version="3.11.280")
            check("sha256 不符时抛 ValueError", False)
        except ValueError as ex:
            check("sha256 不符时抛 ValueError", "sha256" in str(ex), str(ex)[:120])
        except Exception as ex:
            check("sha256 不符时抛 ValueError", False,
                  f"{type(ex).__name__}: {ex}")
        check("校验失败后不留脏 staging",
              not os.path.isdir(os.path.join(os.path.dirname(
                  os.path.normpath(bad_install)), updater.STAGING_DIRNAME)))

        # ========== 10) 用户取消 ==========
        print("\n===== 10) 取消下载 =====")
        try:
            updater.prepare_update(base_url, e, bad_install,
                                   main_exe="AutoWork.exe",
                                   should_stop=lambda: True,
                                   local_version="3.11.280")
            check("should_stop 触发 InterruptedError", False)
        except InterruptedError:
            check("should_stop 触发 InterruptedError", True)
        except Exception as ex:
            check("should_stop 触发 InterruptedError", False,
                  f"{type(ex).__name__}: {ex}")
        check("取消后不留 .part 脏文件",
              not any(f.endswith(".part") for _, _, fs in os.walk(work) for f in fs))

    finally:
        httpd.shutdown()
        shutil.rmtree(work, ignore_errors=True)

    passed = sum(1 for _, ok in RESULTS if ok)
    print(f"\n===== 汇总：{passed}/{len(RESULTS)} PASS =====")
    if passed != len(RESULTS):
        print("失败项：")
        for n, ok in RESULTS:
            if not ok:
                print("  -", n)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
