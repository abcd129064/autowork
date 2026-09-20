# -*- coding: utf-8 -*-
"""自动更新发布工具（S2）：本地产物 → zip + sha256 + manifest → 服务器 update 目录

用法（bash，密码不落盘）：
  # 1) 只打包生成本地产物（不上传），用于自查
  python tools/publish_update.py --pack-only --source dist/AutoWork --version 3.11.280

  # 2) 打包 + 上传 + 原子切换 latest.json（正式发布）
  AFT_SSH_PASS='***' python tools/publish_update.py \
      --source dist/AutoWork --version 3.11.280 --notes "修复xxx；新增yyy"

  # 3) 发增量（只发变更文件；需要先有上一版的 manifest 作为基线）
  AFT_SSH_PASS='***' python tools/publish_update.py \
      --source dist/AutoWork --version 3.11.281 --mode incremental \
      --base-manifest out/update_manifest_3.11.280.json --notes "热修"

发布语义（关键，避免用户下到半个包）：
  - 包体先上传到 <remote>/packages/AutoWork-<version>-<mode>.zip
  - latest.json **最后**上传（一次性覆盖），客户端要么看到旧版要么看到新版，
    绝不会看到「指向还没传完的包」的中间态
  - 旧包保留最近 KEEP_PACKAGES 份，超出的删除（回滚需要）

远端目录（nginx 静态，零 nginx 改动）：
  /opt/aftersale-web/dist/update/latest.json
  /opt/aftersale-web/dist/update/packages/*.zip
  /opt/aftersale-web/dist/update/files/<rel>      增量模式的散文件
"""
import argparse
import hashlib
import json
import os
import posixpath
import stat
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HOST = "49.235.34.253"
USER = "root"
REMOTE_UPDATE_DIR = "/opt/aftersale-web/dist/update"
PUBLIC_BASE_URL = "http://49.235.34.253/update"
KEEP_PACKAGES = 3          # 每渠道保留最近 N 个包（含最新）
CHUNK_LOG_EVERY = 50 * 1024 * 1024

# 发布时排除的本地产物（用户数据/临时物，不该进更新包）
EXCLUDE_DIR_NAMES = {"config", "logs", "database", "_update_staging", "__pycache__"}
EXCLUDE_FILE_NAMES = {".update-pending", "update_result.log", "moyu_state.json"}


def sha256_file(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def walk_files(root):
    """遍历产物文件，返回相对路径列表（posix 分隔符，已排除用户数据目录）"""
    root = os.path.abspath(root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIR_NAMES]
        for fn in filenames:
            if fn in EXCLUDE_FILE_NAMES:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace("\\", "/")
            out.append(rel)
    return sorted(out)


def make_zip(source_dir, zip_path, files=None):
    """打 zip（DEFLATE）；files 为 None 时打全量，否则只打指定相对路径"""
    source_dir = os.path.abspath(source_dir)
    os.makedirs(os.path.dirname(os.path.abspath(zip_path)) or ".", exist_ok=True)
    rels = files if files is not None else walk_files(source_dir)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel in rels:
            zf.write(os.path.join(source_dir, rel.replace("/", os.sep)), rel)
    return len(rels)


def build_manifest(source_dir, files=None):
    """生成文件级 manifest（增量模式用）：[{path, sha256, size}]"""
    source_dir = os.path.abspath(source_dir)
    rels = files if files is not None else walk_files(source_dir)
    out = []
    for rel in rels:
        full = os.path.join(source_dir, rel.replace("/", os.sep))
        out.append({"path": rel, "sha256": sha256_file(full),
                    "size": os.path.getsize(full)})
    return out


def diff_manifest(old_files, new_files):
    """增量文件集：新版中 sha256 与旧版不同（或旧版没有）的路径"""
    old = {f["path"]: f.get("sha256", "") for f in old_files or []}
    changed = []
    for f in new_files:
        if old.get(f["path"]) != f["sha256"]:
            changed.append(f["path"])
    return sorted(changed)


# ==================== 上传 ====================

def _ssh_connect(password):
    import paramiko
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=password, timeout=25)
    return ssh


def _run(ssh, cmd):
    _, out, err = ssh.exec_command(cmd)
    rc = out.channel.recv_exit_status()
    o = out.read().decode("utf-8", "replace").strip()
    e = err.read().decode("utf-8", "replace").strip()
    return rc, o, e


def _mkdirs(sftp, remote_dir):
    parts = [p for p in remote_dir.split("/") if p]
    cur = ""
    for p in parts:
        cur += "/" + p
        try:
            sftp.stat(cur)
        except FileNotFoundError:
            try:
                sftp.mkdir(cur)
            except OSError:
                pass


def _put(sftp, local, remote, label=""):
    _mkdirs(sftp, posixpath.dirname(remote))
    size = os.path.getsize(local)
    done = [0]
    last_log = [0.0]

    def cb(xfer, total):
        done[0] = xfer
        if xfer - last_log[0] >= CHUNK_LOG_EVERY or xfer == total:
            last_log[0] = xfer
            pct = (xfer * 100 // total) if total else 0
            print(f"  {label} {xfer / 1048576:.1f}/{total / 1048576:.1f} MB ({pct}%)",
                  flush=True)

    sftp.put(local, remote, callback=cb)
    sftp.chmod(remote, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
    return size


def _remote_sha256(ssh, remote_path):
    rc, o, e = _run(ssh, f"sha256sum {remote_path!r} 2>/dev/null | cut -d' ' -f1")
    return o.strip() if rc == 0 else ""


def upload(ssh, sftp, payload, channel, version, mode, notes, dry_run=False):
    """上传包体/散文件，最后原子覆盖 latest.json；返回 latest 内容"""
    pkg_remote = payload["pkg_remote"]
    local_sha = payload["sha256"]

    if dry_run:
        print(f"[dry-run] 将上传 {pkg_remote}（{payload['size'] / 1048576:.1f} MB，"
              f"sha256={local_sha[:16]}…）")
    else:
        print(f"上传包体 → {pkg_remote}")
        _put(sftp, payload["pkg_local"], pkg_remote, label="pkg")
        remote_sha = _remote_sha256(ssh, pkg_remote)
        if remote_sha and remote_sha.lower() != local_sha.lower():
            raise RuntimeError(
                f"上传后 sha256 不一致：本地 {local_sha[:16]}… 远端 {remote_sha[:16]}…")
        print(f"  远端校验通过 sha256={remote_sha[:16]}…")

    # 增量模式：散文件也要上传（客户端按 manifest 逐个下载）
    if mode == "incremental" and payload.get("files_local"):
        base_remote = posixpath.join(REMOTE_UPDATE_DIR, "files")
        n = 0
        for rel, local_path in payload["files_local"].items():
            remote = posixpath.join(base_remote, rel)
            if dry_run:
                n += 1
                continue
            _put(sftp, local_path, remote, label=rel)
            n += 1
        print(f"  增量散文件 {n} 个已上传")

    entry = {
        "version": version,
        "released_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "notes": notes or "",
        "min_version": payload.get("min_version", ""),
        "package": {
            "mode": mode,
            "url": posixpath.relpath(pkg_remote, REMOTE_UPDATE_DIR),
            "sha256": local_sha,
            "size": payload["size"],
        },
        "files": payload.get("manifest", []),
    }

    # latest.json 原子切换：先传 .tmp 再 mv（同目录 rename，nginx 不会读到半截）
    latest_remote = posixpath.join(REMOTE_UPDATE_DIR, "latest.json")
    existing = {}
    if not dry_run:
        try:
            with sftp.open(latest_remote, "r") as f:
                existing = json.loads(f.read().decode("utf-8", "replace") or "{}")
        except Exception:
            existing = {}
    channels = dict(existing.get("channels") or {})
    channels[channel] = entry
    latest = {"updated_at": entry["released_at"], "channels": channels}

    local_latest = payload["latest_local"]
    with open(local_latest, "w", encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False, indent=2)
    if dry_run:
        print(f"[dry-run] 将覆盖 {latest_remote}（channel={channel}）")
    else:
        tmp_remote = latest_remote + ".tmp"
        _put(sftp, local_latest, tmp_remote, label="latest.json")
        rc, o, e = _run(ssh, f"mv -f {tmp_remote!r} {latest_remote!r}")
        if rc != 0:
            raise RuntimeError(f"latest.json 切换失败: {e}")
        print(f"latest.json 已切换（channel={channel} → {version}）")
    return latest


def prune_old_packages(ssh, channel, version, mode, dry_run=False):
    """保留最近 KEEP_PACKAGES 个同渠道包，删除更早的（按 mtime）"""
    pattern = posixpath.join(REMOTE_UPDATE_DIR, "packages",
                             f"{channel}-*.zip")
    rc, o, e = _run(ssh, f"ls -1t {pattern} 2>/dev/null")
    if rc != 0 or not o.strip():
        return
    lines = [x for x in o.splitlines() if x.strip()]
    keep = set()
    for ln in lines:
        name = posixpath.basename(ln)
        if f"-{version}-" in name:
            keep.add(ln)
        elif len(keep) < KEEP_PACKAGES:
            keep.add(ln)
    drop = [ln for ln in lines if ln not in keep]
    for d in drop:
        if dry_run:
            print(f"[dry-run] 将删除旧包 {d}")
        else:
            _run(ssh, f"rm -f {d!r}")
            print(f"  删除旧包 {posixpath.basename(d)}")


# ==================== main ====================

def main():
    ap = argparse.ArgumentParser(
        description="AutoWork 自动更新发布工具（打包 → 上传 → 原子切换 latest.json）")
    ap.add_argument("--source", required=True,
                    help="产物目录（如 dist/AutoWork）")
    ap.add_argument("--version", required=True, help="本次发布版本号，如 3.11.280")
    ap.add_argument("--channel", default="autowork",
                    help="渠道名（autowork / aftersale），默认 autowork")
    ap.add_argument("--mode", default="full", choices=["full", "incremental"],
                    help="full=整包 zip（默认）；incremental=只发变更文件")
    ap.add_argument("--base-manifest", default="",
                    help="增量模式必填：上一版 manifest json 路径（算差异基线）")
    ap.add_argument("--min-version", default="",
                    help="低于此版本的客户端强制走全量（增量模式建议填上一版）")
    ap.add_argument("--notes", default="", help="更新说明（客户端弹窗展示）")
    ap.add_argument("--out-dir", default="", help="本地打包输出目录，默认 out/")
    ap.add_argument("--pack-only", action="store_true",
                    help="只在本地生成 zip/manifest/latest.json，不上传")
    ap.add_argument("--dry-run", action="store_true",
                    help="打包并打印将要上传的内容，但不实际上传")
    args = ap.parse_args()

    source = os.path.abspath(args.source)
    if not os.path.isdir(source):
        print(f"ERROR: 产物目录不存在: {source}")
        return 2
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.abspath(args.out_dir) if args.out_dir else \
        os.path.join(root, "out")
    os.makedirs(out_dir, exist_ok=True)

    all_files = walk_files(source)
    if not all_files:
        print(f"ERROR: 产物目录为空（或全被排除）: {source}")
        return 2
    manifest = build_manifest(source, all_files)
    manifest_path = os.path.join(out_dir, f"update_manifest_{args.version}.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"产物文件 {len(all_files)} 个，manifest → {manifest_path}")

    # 增量：与基线 manifest 求差
    mode = args.mode
    target_files = all_files
    if mode == "incremental":
        if not args.base_manifest or not os.path.isfile(args.base_manifest):
            print("ERROR: --mode incremental 需要 --base-manifest 指向上一版 manifest")
            return 2
        with open(args.base_manifest, encoding="utf-8") as f:
            base = json.load(f)
        changed = diff_manifest(base, manifest)
        if not changed:
            print("无文件变更，无需发布增量包")
            return 0
        target_files = changed
        print(f"增量变更文件 {len(changed)} 个（基线 {os.path.basename(args.base_manifest)}）")
        if not args.min_version:
            print("提示：增量发布建议同时给 --min-version（上一版本号），"
                  "让跨多版的旧客户端自动降级为全量")

    zip_name = f"{args.channel}-{args.version}-{mode}.zip"
    zip_local = os.path.join(out_dir, zip_name)
    n = make_zip(source, zip_local, target_files)
    size = os.path.getsize(zip_local)
    sha = sha256_file(zip_local)
    print(f"打包完成 → {zip_local}（{n} 个文件，{size / 1048576:.1f} MB，"
          f"sha256={sha[:16]}…）")

    payload = {
        "pkg_local": zip_local,
        "pkg_remote": posixpath.join(REMOTE_UPDATE_DIR, "packages", zip_name),
        "size": size,
        "sha256": sha,
        "manifest": manifest,
        "min_version": args.min_version,
        "latest_local": os.path.join(out_dir, f"latest_{args.channel}.json"),
        "files_local": {},
    }
    if mode == "incremental":
        payload["files_local"] = {
            rel: os.path.join(source, rel.replace("/", os.sep))
            for rel in target_files
        }

    if args.pack_only:
        # 本地也生成一份 latest.json 供联调（本地 http 服务可直接用）
        latest = {"updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                  "channels": {args.channel: {
                      "version": args.version,
                      "released_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                      "notes": args.notes,
                      "min_version": args.min_version,
                      "package": {"mode": mode, "url": zip_name,
                                  "sha256": sha, "size": size},
                      "files": manifest}}}
        with open(payload["latest_local"], "w", encoding="utf-8") as f:
            json.dump(latest, f, ensure_ascii=False, indent=2)
        print(f"--pack-only：本地 latest.json → {payload['latest_local']}")
        return 0

    pw = os.environ.get("AFT_SSH_PASS")
    if not pw:
        print("ERROR: 未设置 AFT_SSH_PASS（上传需要 SSH 密码）")
        return 2

    ssh = _ssh_connect(pw)
    sftp = ssh.open_sftp()
    try:
        rc, o, e = _run(ssh, f"mkdir -p {REMOTE_UPDATE_DIR}/packages "
                             f"{REMOTE_UPDATE_DIR}/files && ls -ld {REMOTE_UPDATE_DIR}")
        print("远端目录:", o or e)
        upload(ssh, sftp, payload, args.channel, args.version, mode,
               args.notes, dry_run=args.dry_run)
        if not args.dry_run:
            prune_old_packages(ssh, args.channel, args.version, mode)
            rc, o, e = _run(ssh, f"cat {REMOTE_UPDATE_DIR}/latest.json")
            if rc == 0:
                try:
                    d = json.loads(o)
                    ch = (d.get("channels") or {}).get(args.channel) or {}
                    print("线上校验:", ch.get("version"),
                          (ch.get("package") or {}).get("mode"),
                          f"{(ch.get('package') or {}).get('size', 0) / 1048576:.1f} MB")
                except Exception:
                    print("线上 latest.json 解析失败:", o[:200])
            rc, o, e = _run(ssh, f"ls -lh {REMOTE_UPDATE_DIR}/packages/ | tail -8")
            print(o)
    finally:
        sftp.close()
        ssh.close()
    print("DONE  客户端更新源:", f"{PUBLIC_BASE_URL}/latest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
