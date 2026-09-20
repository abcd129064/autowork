# -*- coding: utf-8 -*-
"""core.updater 自动更新纯逻辑层单元测试（无 Qt、无网络、无真机 cmd）

覆盖：
- 版本解析/比较边界（分支后缀不参与比较）
- latest.json 双形态解析 + nginx SPA 回退守卫（200+text/html 必须报错）
- sha256 / 下载（成功、哈希不符、用户取消、HTML 伪装）
- zip 校验解压（坏包、zip-slip 越界）+ onedir 顶级目录归一化
- 增量 manifest diff 与累计进度
- bat 生成（占位符全解析、System32 绝对路径、无裸命令、ping 替代 timeout）
- vbs 模板引号正确性（回归：raw 三引号吃掉 VBScript 转义引号导致静默失败）
- pending 标记 / 回执消费三态（成功、失败、无回执→绝不重试）
- launch_updater 拉起命令与 dev/frozen 判定
"""
import hashlib
import io
import json
import os
import zipfile

import pytest

from core import updater


# ==================== 假 requests ====================

class _FakeResp:
    """最小可用假响应：支持上下文管理器 / raise_for_status / headers / iter_content"""

    def __init__(self, body=b"", status=200, ctype="application/octet-stream",
                 chunks=None):
        self._body = body
        self.status_code = status
        self.headers = {"Content-Type": ctype,
                        "Content-Length": str(len(body))}
        self._chunks = chunks if chunks is not None else [body]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=1):
        for c in self._chunks:
            if c:
                yield c

    @property
    def content(self):
        return self._body

    def json(self):
        return json.loads(self._body.decode("utf-8"))


def _patch_get(monkeypatch, resp=None, exc=None, recorder=None):
    def _get(url, **kw):
        if recorder is not None:
            recorder.append((url, kw))
        if exc is not None:
            raise exc
        return resp
    monkeypatch.setattr("requests.get", _get)


# ==================== 版本比较 ====================

@pytest.mark.parametrize("v,expected", [
    ("3.11.273", (3, 11, 273)),
    ("3.11.273-refactor/fluent-window", (3, 11, 273)),
    ("0.0.0", (0, 0, 0)),
    ("v1.2.3", (1, 2, 3)),
    ("", (0, 0, 0)),
    ("abc", (0, 0, 0)),
    (None, (0, 0, 0)),
])
def test_parse_version(v, expected):
    assert updater.parse_version(v) == expected


def test_parse_version_takes_leftmost_match():
    """分支名里含版本号样式的数字时，必须取最左的那个（真实版本）"""
    assert updater.parse_version("3.11.273-feature/9.9.9") == (3, 11, 273)


def test_parse_version_v_prefix_does_not_silently_disable_updates():
    """回归：'v3.11.280' 若解析成 (0,0,0)，客户端会静默永不更新"""
    assert updater.is_newer("v3.11.280", "3.11.273") is True


@pytest.mark.parametrize("remote,local,expect", [
    ("3.11.274", "3.11.273", True),
    ("3.12.0", "3.11.999", True),
    ("4.0.0", "3.99.99", True),
    ("3.11.273", "3.11.273", False),          # 相等不算新
    ("3.11.272", "3.11.273", False),
    ("0.0.0", "3.11.273", False),             # 占位版本永不触发
    ("3.11.274-feature/x", "3.11.273", True),  # 后缀不挡比较
    ("3.11.273-a", "3.11.273-b", False),      # 后缀不参与比较
])
def test_is_newer(remote, local, expect):
    assert updater.is_newer(remote, local) is expect


# ==================== latest.json 解析 ====================

def _channels_body(version="3.11.280", mode="full", url="packages/a.zip"):
    return json.dumps({"channels": {"autowork": {
        "version": version, "notes": "n", "min_version": "",
        "package": {"mode": mode, "url": url, "sha256": "abc", "size": 123},
        "files": []}}}).encode()


def test_fetch_latest_channels_form(monkeypatch):
    _patch_get(monkeypatch, _FakeResp(_channels_body()))
    e = updater.fetch_latest("http://h/update")
    assert e["version"] == "3.11.280"
    assert e["package"]["mode"] == "full"


def test_fetch_latest_flat_form_normalized(monkeypatch):
    """扁平形态（无 channels）应归一化出 package 结构"""
    body = json.dumps({"version": "1.2.3", "url": "x.zip",
                       "sha256": "s", "size": 9}).encode()
    _patch_get(monkeypatch, _FakeResp(body))
    e = updater.fetch_latest("http://h/u", updater.CHANNEL_AUTOWORK)
    assert e["package"] == {"mode": "full", "url": "x.zip", "sha256": "s",
                            "size": 9}


def test_fetch_latest_flat_form_rejects_other_channel(monkeypatch):
    body = json.dumps({"version": "1.2.3", "url": "x.zip"}).encode()
    _patch_get(monkeypatch, _FakeResp(body))
    with pytest.raises(ValueError, match="仅支持"):
        updater.fetch_latest("http://h/u", updater.CHANNEL_AFTERSALE)


def test_fetch_latest_unknown_channel(monkeypatch):
    _patch_get(monkeypatch, _FakeResp(_channels_body()))
    with pytest.raises(ValueError, match="无渠道"):
        updater.fetch_latest("http://h/u", "nonexist")


def test_fetch_latest_rejects_html_fallback(monkeypatch):
    """生产 nginx SPA try_files 会把缺失路径回退成 200+text/html，
    必须被守卫拦截并给出可诊断信息（而不是含糊的 JSON 解析错）"""
    html = b"<!doctype html><html><body>SPA</body></html>"
    _patch_get(monkeypatch, _FakeResp(html, ctype="text/html"))
    with pytest.raises(ValueError) as ei:
        updater.fetch_latest("http://h/u")
    assert "nginx" in str(ei.value) or "JSON" in str(ei.value)


def test_fetch_latest_accepts_json_ctype_without_brace_check(monkeypatch):
    """Content-Type 带 charset 也应识别为 json"""
    _patch_get(monkeypatch, _FakeResp(_channels_body(),
                                      ctype="application/json; charset=utf-8"))
    assert updater.fetch_latest("http://h/u")["version"] == "3.11.280"


def test_check_update_resolves_relative_url(monkeypatch):
    _patch_get(monkeypatch, _FakeResp(_channels_body()))
    e = updater.check_update("http://h/update", "3.11.273")
    assert e["_remote_version"] == "3.11.280"
    assert e["_resolved_url"] == "http://h/update/packages/a.zip"


def test_check_update_returns_none_when_current(monkeypatch):
    _patch_get(monkeypatch, _FakeResp(_channels_body(version="3.11.273")))
    assert updater.check_update("http://h/u", "3.11.273") is None


def test_check_update_keeps_absolute_url(monkeypatch):
    body = json.dumps({"channels": {"autowork": {
        "version": "9.9.9",
        "package": {"mode": "full", "url": "https://cdn/x.zip",
                    "sha256": "", "size": 1}}}}).encode()
    _patch_get(monkeypatch, _FakeResp(body))
    e = updater.check_update("http://h/u", "1.0.0")
    assert e["_resolved_url"] == "https://cdn/x.zip"


# ==================== sha256 / 下载 ====================

def test_sha256_of_file(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello")
    assert updater.sha256_of_file(str(p)) == hashlib.sha256(b"hello").hexdigest()


def test_download_file_success_and_atomic_rename(tmp_path, monkeypatch):
    data = b"PKGDATA"
    _patch_get(monkeypatch, _FakeResp(data, chunks=[b"PKG", b"DATA"]))
    dest = str(tmp_path / "sub" / "pkg.zip")
    seen = []
    out = updater.download_file("http://h/p.zip", dest,
                               expect_sha256=hashlib.sha256(data).hexdigest(),
                               progress_cb=lambda d, t: seen.append((d, t)))
    assert out == dest
    assert open(dest, "rb").read() == data
    assert not os.path.exists(dest + ".part")
    assert seen and all(t == len(data) for _, t in seen)
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)   # 单调


def test_download_file_sha_mismatch(tmp_path, monkeypatch):
    _patch_get(monkeypatch, _FakeResp(b"real"))
    dest = str(tmp_path / "p.zip")
    with pytest.raises(ValueError, match="sha256"):
        updater.download_file("http://h/p.zip", dest, expect_sha256="0" * 64)
    assert not os.path.exists(dest)
    assert not os.path.exists(dest + ".part")     # 脏 .part 必须清掉


def test_download_file_rejects_html(tmp_path, monkeypatch):
    """包未上传时 nginx 回退 HTML，必须报「下载地址返回 HTML」而非哈希不符"""
    _patch_get(monkeypatch, _FakeResp(b"<html></html>", ctype="text/html"))
    with pytest.raises(ValueError, match="HTML"):
        updater.download_file("http://h/p.zip", str(tmp_path / "p.zip"))


def test_download_file_cancel(tmp_path, monkeypatch):
    _patch_get(monkeypatch, _FakeResp(b"abcdef", chunks=[b"ab", b"cd", b"ef"]))
    dest = str(tmp_path / "p.zip")
    with pytest.raises(InterruptedError):
        updater.download_file("http://h/p.zip", dest, should_stop=lambda: True)
    assert not os.path.exists(dest)
    assert not os.path.exists(dest + ".part")


def test_download_file_http_error(tmp_path, monkeypatch):
    _patch_get(monkeypatch, _FakeResp(b"", status=404))
    with pytest.raises(RuntimeError):
        updater.download_file("http://h/p.zip", str(tmp_path / "p.zip"))


# ==================== zip 校验 / 解压 ====================

def _make_zip(path, entries):
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)


def test_verify_and_extract_ok(tmp_path):
    z = tmp_path / "a.zip"
    _make_zip(str(z), {"AutoWork.exe": "exe", "_internal/a.dll": "dll"})
    out = tmp_path / "ex"
    updater.verify_and_extract_zip(str(z), str(out))
    assert (out / "AutoWork.exe").read_text() == "exe"
    assert (out / "_internal" / "a.dll").read_text() == "dll"


def test_verify_and_extract_rejects_zip_slip(tmp_path):
    z = tmp_path / "evil.zip"
    _make_zip(str(z), {"../escape.txt": "pwn"})
    with pytest.raises(ValueError, match="越界"):
        updater.verify_and_extract_zip(str(z), str(tmp_path / "ex"))
    assert not (tmp_path / "escape.txt").exists()


def test_verify_and_extract_rejects_corrupt(tmp_path):
    z = tmp_path / "bad.zip"
    _make_zip(str(z), {"a.txt": "x"})
    raw = bytearray(z.read_bytes())
    # 破坏压缩数据区（保留文件头，让 testzip 而非 open 失败）
    for i in range(len(raw) - 1, max(0, len(raw) - 60), -1):
        raw[i] ^= 0xFF
    z.write_bytes(bytes(raw))
    with pytest.raises(Exception):
        updater.verify_and_extract_zip(str(z), str(tmp_path / "ex"))


def test_flatten_extract_root_strips_single_wrapper(tmp_path):
    root = tmp_path / "ex"
    (root / "AutoWork").mkdir(parents=True)
    (root / "AutoWork" / "AutoWork.exe").write_text("exe")
    assert updater.flatten_extract_root(str(root), "AutoWork.exe") == \
        str(root / "AutoWork")


def test_flatten_extract_root_keeps_flat_layout(tmp_path):
    root = tmp_path / "ex"
    root.mkdir()
    (root / "AutoWork.exe").write_text("exe")
    assert updater.flatten_extract_root(str(root), "AutoWork.exe") == str(root)


def test_flatten_extract_root_keeps_when_multiple_entries(tmp_path):
    root = tmp_path / "ex"
    (root / "AutoWork").mkdir(parents=True)
    (root / "AutoWork" / "AutoWork.exe").write_text("exe")
    (root / "readme.txt").write_text("r")
    assert updater.flatten_extract_root(str(root), "AutoWork.exe") == str(root)


def test_flatten_extract_root_keeps_when_exe_absent(tmp_path):
    """只有一个子目录但主 exe 不在其中 → 不盲目下钻"""
    root = tmp_path / "ex"
    (root / "Other").mkdir(parents=True)
    (root / "Other" / "x.txt").write_text("x")
    assert updater.flatten_extract_root(str(root), "AutoWork.exe") == str(root)


# ==================== 增量 manifest ====================

def _manifest(tmp_path, rel, content):
    p = tmp_path / rel.replace("/", os.sep)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return {"path": rel, "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content)}


def test_manifest_diff(tmp_path):
    same = _manifest(tmp_path, "_internal/b.pyd", b"same")
    changed = _manifest(tmp_path, "_internal/a.dll", b"v1")
    files = [same, changed, {"path": "new.exe", "sha256": "f" * 64, "size": 3}]
    # 改本地 a.dll 内容 → 应变为需下载
    (tmp_path / "_internal" / "a.dll").write_bytes(b"v2-DIFFERENT")
    need = updater.manifest_diff(files, str(tmp_path))
    paths = sorted(f["path"] for f in need)
    assert paths == ["_internal/a.dll", "new.exe"]


def test_manifest_diff_skips_invalid_entries(tmp_path):
    files = [{"path": "", "sha256": "a" * 64}, {"path": "x", "sha256": ""},
             {"path": "y.txt", "sha256": "b" * 64}]
    need = updater.manifest_diff(files, str(tmp_path))
    assert [f["path"] for f in need] == ["y.txt"]


def test_manifest_diff_all_same_returns_empty(tmp_path):
    m = _manifest(tmp_path, "a.txt", b"hello")
    assert updater.manifest_diff([m], str(tmp_path)) == []


def test_download_incremental_accumulates_progress(monkeypatch, tmp_path):
    calls = []
    seen = []

    def _fake_dl(url, dest, expect_sha256="", timeout=20.0,
                 progress_cb=None, should_stop=None):
        calls.append((url, dest))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, "wb").write(b"12345")
        if progress_cb:
            progress_cb(5, 5)
        return dest

    monkeypatch.setattr(updater, "download_file", _fake_dl)
    files = [{"path": "a.exe", "sha256": "x", "size": 5},
             {"path": "_internal/b.dll", "sha256": "y", "size": 5}]
    n = updater.download_incremental(
        "http://h/u", files, str(tmp_path / "stg"),
        progress_cb=lambda d, t: seen.append((d, t)))
    assert n == 2
    assert all(t == 10 for _, t in seen), seen       # total = 两文件之和
    dones = [d for d, _ in seen]
    assert dones == sorted(dones) and dones[-1] == 10   # 跨文件累计不回退
    assert calls[0][0] == "http://h/u/files/a.exe"
    assert os.path.isfile(tmp_path / "stg" / "_internal" / "b.dll")


# ==================== bat / vbs 生成 ====================

def test_build_bat_all_placeholders_resolved(tmp_path):
    install = str(tmp_path / "AutoWork")
    bat = updater.build_bat(install, str(tmp_path / "stg"), "AutoWork.exe",
                            4321, "full")
    assert "__" not in bat.replace("__pycache__", "")   # 占位符全解析
    for token in ("__INSTALL_DIR__", "__STAGING_DIR__", "__NEW_DIR__",
                  "__BAK_DIR__", "__MAIN_EXE__", "__WAIT_PID__", "__MODE__",
                  "__RESULT_LOG__", "__EXCLUDE_DIRS__", "__PENDING_FLAG__"):
        assert token not in bat, token
    assert install in bat
    assert "4321" in bat


def test_build_bat_uses_system32_absolute_paths():
    """回归：PATH 被 GNU coreutils 污染时，裸 find/timeout 会让等待循环
    瞬间失效 → 主程序还在跑就 rename 安装目录（毁目录）"""
    bat = updater.build_bat(r"C:\App", r"C:\stg", "AutoWork.exe", 1, "full")
    code = [ln.strip() for ln in bat.splitlines()
            if not ln.strip().lower().startswith(("rem", "::"))]
    joined = "\n".join(code).lower()
    for tool in ("tasklist", "taskkill", "robocopy", "find.exe", "ping.exe"):
        assert f'"%sys32%\\{tool}' in joined or f"%sys32%\\{tool}" in joined, tool
    for bare in ("robocopy ", "tasklist ", "taskkill "):
        assert not any(ln.lower().startswith(bare) for ln in code), bare
    assert "timeout" not in joined          # 改用 ping（不依赖 console stdin）


def test_build_bat_modes_and_excludes(tmp_path):
    full = updater.build_bat(str(tmp_path / "A"), str(tmp_path / "s"),
                             "AutoWork.exe", 1, "full")
    assert ":incremental" in full and "goto incremental" in full
    # 用户数据排除必须同时出现在 full 回迁循环与 incremental /XD
    for d in updater.EXCLUDE_DIRS:
        assert f'"{d}"' in full
        assert d in full.split(":incremental", 1)[1]
    assert updater.PENDING_FLAG in full      # pending 需随换位保留
    assert "update_result.log" in full


def test_build_bat_derives_sibling_dirs(tmp_path):
    install = str(tmp_path / "AutoWork")
    bat = updater.build_bat(install, str(tmp_path / "s"), "AutoWork.exe", 1,
                            "full")
    assert install + "_new" in bat
    assert install + updater._BACKUP_SUFFIX in bat


def test_vbs_template_quoting_regression():
    # 回归：raw 三引号会把 VBScript 的四连引号转义吃掉 → 编译器语法错误，
    # 且 wscript 静默失败（updater 根本没跑）。改用 Chr(34) 拼引号。
    vbs = updater._VBS_TEMPLATE
    assert "Chr(34)" in vbs
    assert '"' * 4 not in vbs
    assert "ws.Run q & WScript.Arguments(0) & q, 0, False" in vbs
    assert 'CreateObject("Wscript.Shell")' in vbs


def test_write_updater_scripts(tmp_path):
    install = tmp_path / "AutoWork"
    install.mkdir()
    work = tmp_path / "work dir"          # 带空格
    bat, vbs = updater.write_updater_scripts(
        str(work), str(install), str(tmp_path / "stg"), "AutoWork.exe", 7,
        "full")
    assert os.path.isfile(bat) and os.path.isfile(vbs)
    # bat 必须在安装目录之外（整目录 rename 不能把 updater 自己卷进去）
    assert not os.path.normpath(bat).startswith(os.path.normpath(str(install)))
    assert os.path.normpath(work) in os.path.normpath(bat)
    # GBK 可读且无 BOM（cmd 首行解析）
    raw = open(bat, "rb").read()
    assert not raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("gbk")
    assert text.startswith("@echo off")
    assert text.count("\r\n") > 10         # CRLF 换行
    assert "7" in text


# ==================== pending / 回执 ====================

def test_consume_receipt_none_when_no_pending(tmp_path):
    assert updater.consume_update_receipt(str(tmp_path)) is None


def test_consume_receipt_success(tmp_path):
    d = str(tmp_path)
    updater.mark_update_pending(d, {"version": "3.11.280", "mode": "full"})
    with open(os.path.join(d, updater.RESULT_LOG), "w", encoding="utf-8") as f:
        f.write('{"ok": true}')
    r = updater.consume_update_receipt(d)
    assert r == {"ok": True, "version": "3.11.280", "error": ""}
    # 两文件都必须删掉，避免下次启动重复提示
    assert not os.path.isfile(os.path.join(d, updater.PENDING_FLAG))
    assert not os.path.isfile(os.path.join(d, updater.RESULT_LOG))


def test_consume_receipt_failure_keeps_no_retry(tmp_path):
    """有 pending 无回执 → 判失败且清理 pending（绝不重试，防死循环）"""
    d = str(tmp_path)
    updater.mark_update_pending(d, {"version": "9.9.9"})
    r = updater.consume_update_receipt(d)
    assert r["ok"] is False
    assert r["version"] == "9.9.9"
    assert "未完成" in r["error"]
    assert not os.path.isfile(os.path.join(d, updater.PENDING_FLAG))
    # 再消费一次必须为 None（不会反复失败提示）
    assert updater.consume_update_receipt(d) is None


def test_consume_receipt_bad_json(tmp_path):
    d = str(tmp_path)
    updater.mark_update_pending(d, {"version": "1.0.0"})
    with open(os.path.join(d, updater.RESULT_LOG), "w", encoding="utf-8") as f:
        f.write("not-json")
    r = updater.consume_update_receipt(d)
    assert r["ok"] is False and "回执解析失败" in r["error"]


def test_consume_receipt_error_from_updater(tmp_path):
    d = str(tmp_path)
    updater.mark_update_pending(d, {"version": "1.0.0"})
    with open(os.path.join(d, updater.RESULT_LOG), "w", encoding="utf-8") as f:
        f.write('{"ok": false, "error": "cannot rename install dir"}')
    r = updater.consume_update_receipt(d)
    assert r["ok"] is False and r["error"] == "cannot rename install dir"


def test_consume_receipt_cleans_staging(tmp_path):
    d = str(tmp_path)
    updater.mark_update_pending(d, {"version": "1.0.0"})
    st = tmp_path / updater.STAGING_DIRNAME
    (st / "x").mkdir(parents=True)
    (st / "x" / "f.txt").write_text("leftover")
    updater.consume_update_receipt(d)
    assert not st.exists()


def test_mark_update_pending_survives_bad_dir(tmp_path):
    """写标记失败不能抛（更新流程要继续）"""
    updater.mark_update_pending(str(tmp_path / "nope" / "deep"), {"v": 1})


# ==================== resolve_mode / url ====================

def test_resolve_mode():
    inc = {"package": {"mode": "incremental"}, "min_version": "3.11.280"}
    assert updater.resolve_mode(inc, "3.11.280") == "incremental"
    assert updater.resolve_mode(inc, "3.11.999") == "incremental"
    assert updater.resolve_mode(inc, "3.11.100") == "full"   # 低于 min → 降级
    assert updater.resolve_mode({"package": {"mode": "full"}}, "1.0") == "full"
    assert updater.resolve_mode({}, "1.0") == "full"         # 缺 package → full
    assert updater.resolve_mode({"package": {"mode": "WEIRD"}}, "1.0") == "full"


def test_resolve_package_url():
    e = {"package": {"url": "packages/a.zip"}}
    assert updater.resolve_package_url("http://h/u/", e) == \
        "http://h/u/packages/a.zip"
    assert updater.resolve_package_url("http://h/u", {
        "_resolved_url": "https://cdn/x.zip"}) == "https://cdn/x.zip"
    assert updater.resolve_package_url("http://h/u", {"package": {}}) == ""


# ==================== launch_updater ====================

def test_launch_updater_writes_scripts_and_spawns(tmp_path, monkeypatch):
    install = tmp_path / "AutoWork"
    install.mkdir()
    staging = tmp_path / "stg"
    staging.mkdir()
    spawned = []
    monkeypatch.setattr(updater.subprocess, "Popen",
                        lambda cmd, **kw: spawned.append((cmd, kw)) or "P")
    work = str(tmp_path / "w")
    bat, vbs, cmd = updater.launch_updater(
        str(install), str(staging), "AutoWork.exe", mode="full",
        pending_info={"version": "3.11.280"}, pid=1234, work_dir=work)
    assert spawned and len(spawned) == 1
    argv = spawned[0][0]
    assert argv[0] == "wscript.exe" and vbs in argv and bat in argv
    # 路径已内联进 bat，运行期只传 bat 一个参数
    assert len(argv) == 4
    assert os.path.isfile(os.path.join(str(install), updater.PENDING_FLAG))
    pend = json.load(open(os.path.join(str(install), updater.PENDING_FLAG),
                          encoding="utf-8"))
    assert pend["version"] == "3.11.280"
    assert "1234" in open(bat, encoding="gbk").read()


def test_launch_updater_falls_back_to_cmd(tmp_path, monkeypatch):
    install = tmp_path / "AutoWork"
    install.mkdir()
    calls = []

    def _popen(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "wscript.exe":
            raise OSError("no wscript")
        return "P"

    monkeypatch.setattr(updater.subprocess, "Popen", _popen)
    updater.launch_updater(str(install), str(tmp_path), "AutoWork.exe",
                           work_dir=str(tmp_path / "w"), pid=1)
    assert len(calls) == 2
    assert calls[1][0] == "cmd.exe" and calls[1][1] == "/c"


def test_get_install_info_dev(monkeypatch):
    monkeypatch.setattr(updater.sys, "frozen", False, raising=False)
    info = updater.get_install_info()
    assert info["frozen"] is False
    assert info["main_exe"] == ""
    assert info["app_dir"]
    assert info["local_version"]


def test_get_update_base_url(monkeypatch):
    assert updater.get_update_base_url(lambda k: "") == \
        updater.DEFAULT_UPDATE_BASE_URL
    assert updater.get_update_base_url(lambda k: None) == \
        updater.DEFAULT_UPDATE_BASE_URL
    assert updater.get_update_base_url(
        lambda k: "http://127.0.0.1:8000/u") == "http://127.0.0.1:8000/u"

    def _boom(k):
        raise RuntimeError("settings broken")

    assert updater.get_update_base_url(_boom) == updater.DEFAULT_UPDATE_BASE_URL


def test_update_base_url_registered_in_misc_domain():
    from core.app_settings import domain_of
    assert domain_of("update_base_url") == "misc"
    assert domain_of("update_auto_check") == "misc"


# ==================== prepare_update（无网络，patch 下载） ====================

def _entry(mode="full", url="packages/a.zip", sha="", files=None,
           version="9.9.9", min_version=""):
    return {"version": version, "_remote_version": version,
            "min_version": min_version,
            "_resolved_url": "http://h/u/" + url,
            "package": {"mode": mode, "url": url, "sha256": sha, "size": 1},
            "files": files or []}


def test_prepare_update_full_flattens_wrapper(tmp_path, monkeypatch):
    """onedir zip 带顶级目录 → staging 根必须直接含主 exe"""
    install = tmp_path / "site" / "AutoWork"
    install.mkdir(parents=True)
    zip_path = tmp_path / "pkg.zip"
    _make_zip(str(zip_path), {"AutoWork/AutoWork.exe": "new",
                              "AutoWork/_internal/a.dll": "dll"})

    def _fake_dl(url, dest, **kw):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, "wb").write(zip_path.read_bytes())
        return dest

    monkeypatch.setattr(updater, "download_file", _fake_dl)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    info = updater.prepare_update("http://h/u", _entry(), str(install),
                                  main_exe="AutoWork.exe")
    assert info["mode"] == "full"
    st = info["staging_dir"]
    assert os.path.isfile(os.path.join(st, "AutoWork.exe"))
    assert os.path.isfile(os.path.join(st, "_internal", "a.dll"))
    assert not os.path.isdir(os.path.join(st, "AutoWork"))
    assert info["files_count"] == 2
    # staging 与安装目录同级（同卷，move 才是瞬时的）
    assert os.path.dirname(st) == os.path.dirname(str(install))
    assert os.path.basename(st) == updater.STAGING_DIRNAME


def test_prepare_update_incremental_only_changed(tmp_path, monkeypatch):
    install = tmp_path / "site" / "AutoWork"
    install.mkdir(parents=True)
    same = _manifest(install.parent, "_internal/b.pyd", b"same")
    changed = _manifest(install.parent, "AutoWork.exe", b"v1")
    # 让本地 exe 与 manifest 不一致
    (install / "AutoWork.exe").write_bytes(b"OLD")
    (install / "_internal").mkdir(exist_ok=True)
    (install / "_internal" / "b.pyd").write_bytes(b"same")

    def _fake_dl(url, dest, **kw):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, "wb").write(b"NEW")
        return dest

    monkeypatch.setattr(updater, "download_file", _fake_dl)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    e = _entry(mode="incremental", files=[same, changed])
    info = updater.prepare_update("http://h/u", e, str(install),
                                  main_exe="AutoWork.exe",
                                  local_version="9.9.9")
    assert info["mode"] == "incremental"
    assert info["files_count"] == 1          # 只下变更的那个
    assert os.path.isfile(os.path.join(info["staging_dir"], "AutoWork.exe"))
    assert not os.path.exists(os.path.join(info["staging_dir"], "_internal"))


def test_prepare_update_min_version_downgrades_to_full(tmp_path, monkeypatch):
    """增量条目 + 本地版本低于 min_version → 必须自动转全量"""
    install = tmp_path / "site" / "AutoWork"
    install.mkdir(parents=True)
    zip_path = tmp_path / "pkg.zip"
    _make_zip(str(zip_path), {"AutoWork.exe": "new"})
    monkeypatch.setattr(updater, "download_file",
                        lambda url, dest, **kw: (
                            os.makedirs(os.path.dirname(dest), exist_ok=True),
                            open(dest, "wb").write(zip_path.read_bytes()),
                            dest)[-1])
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    e = _entry(mode="incremental", min_version="3.11.280",
               files=[{"path": "x", "sha256": "a" * 64, "size": 1}])
    info = updater.prepare_update("http://h/u", e, str(install),
                                  main_exe="AutoWork.exe",
                                  local_version="3.11.100")
    assert info["mode"] == "full"


def test_prepare_update_no_url_raises(tmp_path):
    e = _entry(url="")
    e["_resolved_url"] = ""
    with pytest.raises(ValueError, match="下载地址"):
        updater.prepare_update("http://h/u", e, str(tmp_path))


def test_prepare_update_incremental_nothing_to_do(tmp_path):
    """增量清单与本地完全一致 → 报错且不建 staging"""
    install = tmp_path / "site" / "AutoWork"
    install.mkdir(parents=True)
    m = _manifest(install, "a.txt", b"same")
    e = _entry(mode="incremental", files=[m])
    with pytest.raises(ValueError, match="无需更新"):
        updater.prepare_update("http://h/u", e, str(install),
                               local_version="9.9.9")
    assert not os.path.isdir(os.path.join(str(tmp_path / "site"),
                                          updater.STAGING_DIRNAME))


def test_prepare_update_cleans_staging_on_failure(tmp_path, monkeypatch):
    """下载/校验失败必须清掉 staging（否则占盘且 consume 时才发现）"""
    install = tmp_path / "site" / "AutoWork"
    install.mkdir(parents=True)

    def _boom(url, dest, **kw):
        raise ValueError("sha256 校验失败")

    monkeypatch.setattr(updater, "download_file", _boom)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    with pytest.raises(ValueError):
        updater.prepare_update("http://h/u", _entry(), str(install))
    assert not os.path.isdir(os.path.join(str(tmp_path / "site"),
                                          updater.STAGING_DIRNAME))


def test_prepare_update_cancel_cleans_staging(tmp_path, monkeypatch):
    install = tmp_path / "site" / "AutoWork"
    install.mkdir(parents=True)

    def _cancel(url, dest, **kw):
        raise InterruptedError("用户取消")

    monkeypatch.setattr(updater, "download_file", _cancel)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    with pytest.raises(InterruptedError):
        updater.prepare_update("http://h/u", _entry(), str(install))
    assert not os.path.isdir(os.path.join(str(tmp_path / "site"),
                                          updater.STAGING_DIRNAME))
