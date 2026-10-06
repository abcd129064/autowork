# -*- coding: utf-8 -*-
"""SFTP 传输完整性校验 offscreen/无网冒烟（P2-2 步骤 1）

只验证 workers/network_workers.py 里"成功判定"这条链路：用假 sftp 对象
（stat 返回受控字节数）驱动 `_verify_upload` / `_verify_download` /
`_verify_dir_file`，覆盖
  1. 两侧一致 → 静默通过；
  2. 远端少字节（截断）→ 抛 TransferSizeMismatch 且文案指向远端；
  3. 本地多字节 → 文案指向本地；
  4. stat 失败（读不到大小）→ 判定失败而不是静默成功；
  5. verify_size=False → 关掉校验（排障开关）；
  6. 目录传输逐文件校验：不一致会被记进 errors 汇总。
不连网、不起线程、不碰真实 SFTP；临时文件只落在 tools/_scratch/ 下。

运行（需要 paramiko 的解释器）：
    C:\\Users\\shen_zhe\\miniconda3\\python.exe tools/smoke/smoke_sftp_size_verify.py
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# ---- Qt DLL 引导（同 windows/aftersale_panel.py 顶部，规避 conda Qt 冲突） ----
import importlib.util as _iu                                        # noqa: E402
try:
    _spec = _iu.find_spec('PySide6')
    if _spec is not None:
        for _d in (list(_spec.submodule_search_locations or []) +
                   [os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                                 'System32')]):
            if _d and os.path.isdir(_d):
                try:
                    os.add_dll_directory(_d)
                except OSError:
                    pass
except Exception:
    pass

from core.transfer_verify import (                                  # noqa: E402
    DIRECTION_DOWNLOAD, DIRECTION_UPLOAD, TransferSizeMismatch,
)
from workers.network_workers import (                               # noqa: E402
    SFTPDirTransferWorker, SFTPOperationWorker,
)

_SCRATCH = os.path.join(PROJECT_ROOT, "tools", "_scratch", "_smoke_sftp_size")
os.makedirs(_SCRATCH, exist_ok=True)
_CONN = ("203.0.113.9", 22, "root", "pw")     # 假凭据：本冒烟不联网
_LOCAL = os.path.join(_SCRATCH, "payload.bin")
_SIZE = 4096
with open(_LOCAL, "wb") as f:
    f.write(b"x" * _SIZE)


class _Attr:
    def __init__(self, size):
        self.st_size = size


class _FakeSFTP:
    """只会 stat 的假 SFTP：size=None 表示 stat 抛错（读不到大小）"""

    def __init__(self, size):
        self.size = size
        self.stat_calls = []

    def stat(self, path):
        self.stat_calls.append(path)
        if self.size is None:
            raise IOError("stat failed")
        return _Attr(self.size)


_PASS = 0


def _expect_raise(label, fn, *, direction, needle):
    global _PASS
    try:
        fn()
    except TransferSizeMismatch as e:
        assert e.direction == direction, f"{label}: direction={e.direction}"
        assert needle in str(e), f"{label}: 文案缺少 {needle!r} → {e}"
        _PASS += 1
        print(f"  [通过] {label} → {e}")
        return
    raise AssertionError(f"{label}: 预期抛 TransferSizeMismatch 但没有")


def _expect_ok(label, fn):
    global _PASS
    fn()
    _PASS += 1
    print(f"  [通过] {label}")


print("## 单文件上传")
w = SFTPOperationWorker(_CONN, "upload", _LOCAL, "/tmp/payload.bin")
_expect_ok("远端大小一致 → 通过", lambda: w._verify_upload(_FakeSFTP(_SIZE), _SIZE))
_expect_raise("远端少了 100 字节 → 失败", lambda: w._verify_upload(_FakeSFTP(_SIZE - 100), _SIZE),
              direction=DIRECTION_UPLOAD, needle="远端文件被截断")
_expect_raise("远端多了 7 字节 → 失败", lambda: w._verify_upload(_FakeSFTP(_SIZE + 7), _SIZE),
              direction=DIRECTION_UPLOAD, needle="比另一端大")
_expect_raise("stat 失败 → 判定失败（不静默放过）",
              lambda: w._verify_upload(_FakeSFTP(None), _SIZE),
              direction=DIRECTION_UPLOAD, needle="无法读取远端文件大小")
_expect_ok("本地大小未知（-1）→ 跳过校验",
           lambda: w._verify_upload(_FakeSFTP(1), -1))
_expect_ok("verify_size=False → 关掉校验",
           lambda: SFTPOperationWorker(_CONN, "upload", _LOCAL, "/tmp/payload.bin",
                                       verify_size=False
                                       )._verify_upload(_FakeSFTP(1), _SIZE))

print("## 单文件下载")
d = SFTPOperationWorker(_CONN, "download", _LOCAL, "/tmp/payload.bin")
_expect_ok("本地大小 == 远端 → 通过", lambda: d._verify_download(_FakeSFTP(_SIZE)))
with open(_LOCAL, "r+b") as f:                # 模拟本地被截断
    f.truncate(_SIZE - 512)
_expect_raise("本地少了 512 字节 → 失败", lambda: d._verify_download(_FakeSFTP(_SIZE)),
              direction=DIRECTION_DOWNLOAD, needle="本地文件被截断")
with open(_LOCAL, "ab") as f:
    f.write(b"y" * 1024)                      # 恢复成比远端大
_expect_raise("本地多了 1024 字节 → 失败", lambda: d._verify_download(_FakeSFTP(_SIZE)),
              direction=DIRECTION_DOWNLOAD, needle="比另一端大")
_expect_raise("远端 stat 失败 → 判定失败",
              lambda: d._verify_download(_FakeSFTP(None)),
              direction=DIRECTION_DOWNLOAD, needle="无法读取远端文件大小")

print("## 目录传输逐文件校验")
dw = SFTPDirTransferWorker(_CONN, "upload_dir", _SCRATCH, "/tmp/dir", dir_name="dir")
_expect_ok("目录上传：一致 → 通过",
           lambda: dw._verify_dir_file(_FakeSFTP(_SIZE), "/tmp/dir/a.bin", _SIZE, "a.bin",
                                       direction=DIRECTION_UPLOAD))
_expect_raise("目录上传：截断 → 失败（名字带相对路径）",
              lambda: dw._verify_dir_file(_FakeSFTP(10), "/tmp/dir/sub/a.bin", _SIZE,
                                          "sub/a.bin", direction=DIRECTION_UPLOAD),
              direction=DIRECTION_UPLOAD, needle="[sub/a.bin]")
_expect_raise("目录下载：本地偏小 → 失败",
              lambda: dw._verify_dir_file(_FakeSFTP(_SIZE), "/tmp/dir/a.bin",
                                          _SIZE - 1, "a.bin", direction=DIRECTION_DOWNLOAD),
              direction=DIRECTION_DOWNLOAD, needle="本地文件被截断")
_expect_ok("目录传输：verify_size=False → 关掉校验",
           lambda: SFTPDirTransferWorker(_CONN, "upload_dir", _SCRATCH, "/tmp/dir",
                                         dir_name="dir", verify_size=False
                                         )._verify_dir_file(_FakeSFTP(1), "/tmp/dir/a.bin",
                                                            _SIZE, "a.bin",
                                                            direction=DIRECTION_UPLOAD))

print(f"SMOKE_OK sftp_size_verify passed={_PASS}")
