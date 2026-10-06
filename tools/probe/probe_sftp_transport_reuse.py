# -*- coding: utf-8 -*-
"""P0-5 测量：SFTP 每个传输任务是否各建一条 Transport（决定"共享 transport"该不该做）

背景
----
sftp_window 的**浏览**路径已经共享面板的 transport（SFTPListWorker(transport, path)），
但**每个传输任务**（上传/下载/删除/改名/建目录）都新起一个 SFTPOperationWorker，
它在 run() 里 `paramiko.Transport((host, port))` + `connect(username, password)`
——即"多选 30 个文件 = 30 次 TCP+SSH 握手 + 30 次认证"。共享 transport 的改造
（1 条 Transport + 每任务独立 channel）属于 M 级改动，先测量再决定。

两种模式
--------
默认（离线，无需网络/凭据）：把 paramiko.Transport / SFTPClient.from_transport 换成
计数假件，按 `--concurrency` 分组真实执行 `SFTPOperationWorker.run()`，量出
"N 个任务 = 多少次握手/认证"，再用 `--connect-ms`（默认 25ms，可用真机实测值替换）
估算共享 transport 能省多少。

`--real host:port user`（需要真机凭据，默认只打印计划，加 --yes 才执行）：
在远端临时目录里真实上传 N 个小文件，分别测"每任务一条 Transport"与
"一条 Transport + N 条 channel"的墙钟时间，打印对比表并清理远端临时文件。
密码只从环境变量读（默认 AFT_SSH_PASS），不写日志、不进命令行。

用法
----
    python tools/probe/probe_sftp_transport_reuse.py                    # 离线结构测量
    python tools/probe/probe_sftp_transport_reuse.py --tasks 30 --connect-ms 40
    python tools/probe/probe_sftp_transport_reuse.py --real 49.235.34.253:22 root
    set AFT_SSH_PASS=xxx && python tools/probe/probe_sftp_transport_reuse.py --real ... --yes

本脚本只读代码 + 打桩，不改任何配置；真机模式只在自建临时目录里写小文件。
"""
import argparse
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ---- Qt DLL 引导（workers/network_workers.py 依赖 PySide6；同项目其它冒烟脚本） ----
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

_SCRATCH = os.path.join(PROJECT_ROOT, "tools", "_scratch", "_probe_transport")


# ==================== 模式 A：离线结构测量 ====================

class _CountingTransport:
    """计数用的 Transport 替身：只统计握手/认证次数与耗时"""

    created = 0
    connected = 0
    closed = 0
    connect_ms = 25.0

    def __init__(self, addr, *args, **kwargs):
        _CountingTransport.created += 1
        self.addr = addr
        self.sock = None
        self.banner_timeout = None
        self.auth_timeout = None
        self._active = True

    def connect(self, username=None, password=None, **kwargs):
        _CountingTransport.connected += 1
        time.sleep(_CountingTransport.connect_ms / 1000.0)   # 模拟 TCP+SSH 握手 + 认证

    def open_session(self, *args, **kwargs):
        time.sleep(0.003)                                    # 模拟开一条 channel 的往返
        return None

    def is_active(self):
        return self._active

    def close(self):
        _CountingTransport.closed += 1
        self._active = False


class _CountingSFTP:
    """SFTPClient 替身：put/get 只搬运字节，stat 返回与本地一致的大小（大小校验要过）"""

    opened = 0
    puts = 0
    stats = 0

    def __init__(self, transport):
        _CountingSFTP.opened += 1
        self.transport = transport
        self._sizes = {}

    # -- 传输 --
    def put(self, local, remote, callback=None):
        _CountingSFTP.puts += 1
        size = os.path.getsize(local) if os.path.exists(local) else 0
        chunk = 4096
        sent = 0
        while sent < size:
            step = min(chunk, size - sent)
            sent += step
            if callback:
                callback(sent, size)
        self._sizes[remote] = size

    def get(self, remote, local, callback=None):
        size = self._sizes.get(remote, 0)
        if callback:
            callback(size, size)

    # -- 校验/其它 --
    def stat(self, path):
        _CountingSFTP.stats += 1

        class _Attr:
            st_size = 0
        attr = _Attr()
        attr.st_size = self._sizes.get(path, 0)
        return attr

    def remove(self, path):
        pass

    def close(self):
        pass


def run_offline(tasks, concurrency, connect_ms, size_kb):
    import paramiko
    from workers import network_workers as nw

    os.makedirs(_SCRATCH, exist_ok=True)
    payload = os.path.join(_SCRATCH, "payload.bin")
    with open(payload, "wb") as f:
        f.write(b"x" * (size_kb * 1024))

    _CountingTransport.connect_ms = connect_ms
    paramiko.Transport = _CountingTransport                        # 打桩：只计数
    paramiko.SFTPClient.from_transport = staticmethod(_CountingSFTP)

    def _one(i):
        w = nw.SFTPOperationWorker(("203.0.113.9", 22, "newbv", "pw"),
                                   "upload", payload, f"/tmp/probe/task_{i}.bin",
                                   file_size=size_kb * 1024)
        t0 = time.time()
        w.run()                                                    # 直接跑，不起线程
        return time.time() - t0

    print("== 模式 A：离线结构测量（paramiko 已打桩，无网络） ==")
    print(f"参数：任务数 {tasks} · 并发 {concurrency} · 单文件 {size_kb} KiB"
          f" · 模拟握手+认证 {connect_ms} ms")
    print(f"打桩前：Transport={_CountingTransport.created} 次认证={_CountingTransport.connected}")

    t_start = time.time()
    durations = []
    batch = max(1, concurrency)
    for begin in range(0, tasks, batch):
        for i in range(begin, min(begin + batch, tasks)):
            durations.append(_one(i))
    baseline = time.time() - t_start
    transports = _CountingTransport.created
    auths = _CountingTransport.connected
    channels = _CountingSFTP.opened

    print()
    print(f"{'指标':<26}{'每任务一条 Transport':>24}")
    print("-" * 50)
    print(f"{'Transport 创建次数':<26}{transports:>24}")
    print(f"{'SSH 握手 + 认证次数':<26}{auths:>24}")
    print(f"{'SFTP session/channel 数':<26}{channels:>24}")
    print(f"{'墙钟耗时(含模拟握手)':<26}{baseline:>23.2f}s")

    # 反事实：1 条 Transport + 每任务一条 channel（握手/认证只做一次）
    handshake = connect_ms / 1000.0
    shared = baseline - auths * handshake + handshake
    saved = baseline - shared
    pct = (saved / baseline * 100.0) if baseline > 0 else 0.0
    transfer_only = sum(durations) / max(1, len(durations)) * tasks

    print()
    print(f"{'指标':<26}{'共享 Transport 模型':>24}")
    print("-" * 50)
    print(f"{'Transport 创建次数':<26}{1:>24}")
    print(f"{'SSH 握手 + 认证次数':<26}{1:>24}")
    print(f"{'SFTP session/channel 数':<26}{channels:>24}")
    print(f"{'墙钟耗时(估算)':<26}{shared:>23.2f}s")
    print(f"{'可省':<26}{saved:>22.2f}s（{pct:.0f}%）")
    print()
    print(f"注：握手/认证耗时是 --connect-ms 给的模型值（本机无网络，无法实测）；"
          f"纯传输部分 {transfer_only:.2f}s 与节省无关。")
    print(f"结构结论（与模拟值无关，直接来自代码）：{tasks} 个传输任务 → "
          f"{transports} 条 Transport / {auths} 次认证；"
          f"浏览目录路径（SFTPListWorker）本身复用面板 transport，不在此列。")
    print(f"真机实测：python tools/probe/probe_sftp_transport_reuse.py "
          f"--real <host:port> <user> --yes")
    if saved < 0.2:
        print("判定：按当前 connect-ms，共享 transport 的收益很小 → 不值得冒 M 级改动的风险。")
    else:
        print("判定：收益可观（本地网络下每次握手+认证就是实打实的串行等待）→ "
              "值得按'共享 Transport + 每任务独立 channel'改造，但仍需先做并发闸门（已有）。")


# ==================== 模式 B：真机测量 ====================

def run_real(host, port, user, files, remote_dir, connect_ms_hint, confirm):
    import paramiko

    password = os.environ.get("AFT_SSH_PASS", "")
    if not password:
        print("缺少密码：请设置环境变量 AFT_SSH_PASS（不写命令行，避免进历史记录）")
        return 2

    os.makedirs(_SCRATCH, exist_ok=True)
    payload = os.path.join(_SCRATCH, "tiny.bin")
    with open(payload, "wb") as f:
        f.write(b"x" * 1024)

    print("== 模式 B：真机测量 ==")
    print(f"目标 {user}@{host}:{port} · 文件数 {files} · 远端目录 {remote_dir}")
    print("将执行：在远端建临时目录 → 上传 N 个小文件（两种方式）→ 删除临时目录")
    if not confirm:
        print("未加 --yes，仅打印计划，未连接。")
        return 0

    def _connect():
        t0 = time.time()
        transport = paramiko.Transport((host, port))
        transport.banner_timeout = 15
        transport.auth_timeout = 15
        transport.connect(username=user, password=password)
        return transport, time.time() - t0

    # 准备远端目录
    transport, first_cost = _connect()
    sftp = paramiko.SFTPClient.from_transport(transport)
    try:
        sftp.mkdir(remote_dir)
    except IOError:
        pass
    sftp.close()
    transport.close()

    # 方式一：每任务一条 Transport
    per_task = []
    t0 = time.time()
    for i in range(files):
        transport, cost = _connect()
        per_task.append(cost)
        sftp = paramiko.SFTPClient.from_transport(transport)
        try:
            sftp.put(payload, f"{remote_dir}/per_task_{i}.bin")
        finally:
            sftp.close()
            transport.close()
    t_per_task = time.time() - t0

    # 方式二：一条 Transport + N 条 channel
    t0 = time.time()
    transport, cost = _connect()
    connect_cost = cost
    channels = []
    try:
        for i in range(files):
            sftp = paramiko.SFTPClient.from_transport(transport)
            channels.append(sftp)
            sftp.put(payload, f"{remote_dir}/shared_{i}.bin")
    finally:
        for sftp in channels:
            try:
                sftp.close()
            except Exception:
                pass
        transport.close()
    t_shared = time.time() - t0

    # 清理
    transport, _ = _connect()
    sftp = paramiko.SFTPClient.from_transport(transport)
    try:
        for name in sftp.listdir(remote_dir):
            sftp.remove(f"{remote_dir}/{name}")
        sftp.rmdir(remote_dir)
    finally:
        sftp.close()
        transport.close()

    avg = sum(per_task) / max(1, len(per_task))
    print()
    print(f"{'指标':<30}{'每任务一条 Transport':>22}{'共享 Transport':>18}")
    print("-" * 70)
    print(f"{'总耗时':<30}{t_per_task:>21.2f}s{t_shared:>17.2f}s")
    print(f"{'Transport 创建次数':<30}{files:>22}{1:>18}")
    print(f"{'握手+认证次数':<30}{files:>22}{1:>18}")
    print(f"{'单次握手+认证(均值)':<30}{avg * 1000:>20.0f}ms{connect_cost * 1000:>16.0f}ms")
    print(f"{'可省':<30}{(t_per_task - t_shared):>21.2f}s"
          f"（{(t_per_task - t_shared) / t_per_task * 100:.0f}%）")
    print()
    print(f"外推：{files} 个文件的节省 ≈ {(avg) * (files - 1):.2f}s（线性，"
          f"与文件大小无关，纯握手/认证串行等待）")
    print("注：以上为真实墙钟，已删除远端临时目录；--connect-ms 只是离线模式用的模型值"
          f"（本次实测均值 {avg * 1000:.0f}ms，可回填到离线模式）。")
    return 0


def main():
    ap = argparse.ArgumentParser(description="SFTP 每任务 Transport 复用测量（P0-5）")
    ap.add_argument("--tasks", type=int, default=30, help="离线模式任务数（默认 30）")
    ap.add_argument("--concurrency", type=int, default=3, help="模拟并发闸门（默认 3）")
    ap.add_argument("--connect-ms", type=float, default=25.0,
                    help="离线模式每次握手+认证的模型耗时（默认 25ms）")
    ap.add_argument("--size-kb", type=int, default=64, help="每个任务的文件大小（默认 64KiB）")
    ap.add_argument("--real", metavar="HOST:PORT", help="真机模式：目标 host:port")
    ap.add_argument("--user", default="root", help="真机模式用户名（默认 root）")
    ap.add_argument("--files", type=int, default=8, help="真机模式文件数（默认 8）")
    ap.add_argument("--dir", default=None, help="远端临时目录（默认 /tmp/aft_probe_<pid>）")
    ap.add_argument("--yes", action="store_true", help="真机模式确认执行（否则只打印计划）")
    args = ap.parse_args()

    if args.real:
        host, _, port = args.real.partition(":")
        if not host:
            print("--real 需要 host:port（端口可省，默认 22）")
            return 2
        remote_dir = args.dir or f"/tmp/aft_probe_{os.getpid()}"
        return run_real(host, int(port or 22), args.user, max(1, args.files),
                        remote_dir, args.connect_ms, args.yes)
    run_offline(max(1, args.tasks), max(1, args.concurrency), args.connect_ms,
                max(1, args.size_kb))
    return 0


if __name__ == "__main__":
    sys.exit(main())
