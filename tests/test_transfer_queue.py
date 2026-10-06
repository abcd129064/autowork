# -*- coding: utf-8 -*-
"""SFTP 传输队列落盘的纯逻辑单测（P2-2 步骤 1）

覆盖：记录构造与容错、按目标隔离、覆盖写与清空、容量上限、垃圾数据兜底、
摘要文案、以及"密码绝不进队列文件"这条安全不变量。
不需要 Qt / paramiko，可在最小依赖解释器（E:\\ANACONDA）下直接跑。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import transfer_queue as tq  # noqa: E402


def test_target_key_normalized():
    assert tq.target_key("203.0.113.9", 22) == "203.0.113.9:22"
    assert tq.target_key(" 10.0.0.7 ", "2222") == "10.0.0.7:2222"
    # 同一目标的不同写法收敛到同一键
    assert tq.target_key("h", 22) == tq.target_key("h", "22")


def test_build_record_ok():
    rec = tq.build_record("upload", r"C:\l\a.bin", "/r/a.bin", size=12)
    assert rec == {"op": "upload", "name": "a.bin", "local_path": r"C:\l\a.bin",
                   "remote_path": "/r/a.bin", "size": 12}
    assert tq.build_record("download_dir", "/l/d", "/r/d", name="d")["name"] == "d"


def test_build_record_rejects_invalid():
    for op in ("", "move", "delete", None):
        try:
            tq.build_record(op, "/l", "/r")
        except ValueError:
            continue
        raise AssertionError(f"非法操作类型未报错: {op!r}")
    # 路径缺失同样拒绝（否则恢复时会拿到空路径任务）
    for local, remote in (("", "/r"), ("/l", ""), (None, None)):
        try:
            tq.build_record("upload", local, remote)
        except ValueError:
            continue
        raise AssertionError("空路径未报错")


def test_normalize_record_tolerates_garbage():
    assert tq.normalize_record(None) is None
    assert tq.normalize_record("upload") is None
    assert tq.normalize_record({}) is None
    assert tq.normalize_record({"op": "spawn", "local_path": "/l", "remote_path": "/r"}) is None
    assert tq.normalize_record({"op": "upload", "local_path": "", "remote_path": "/r"}) is None
    # 大小为负数/字符串/非数字一律收敛为 >=0 的整数
    for raw_size, expect in ((None, 0), (-5, 0), ("12", 12), ("abc", 0), (7.9, 7)):
        rec = tq.normalize_record({"op": "upload", "local_path": "/l/a", "remote_path": "/r",
                                   "size": raw_size})
        assert rec["size"] == expect, (raw_size, rec)
    # 名字缺省取本地文件名
    rec = tq.normalize_record({"op": "download", "local_path": "/l/x/y.bin", "remote_path": "/r"})
    assert rec["name"] == "y.bin"


def test_records_never_carry_credentials():
    """安全不变量：队列文件里只允许出现固定的五个字段"""
    rec = tq.normalize_record({"op": "upload", "local_path": "/l/a", "remote_path": "/r",
                               "password": "s3cret", "username": "root", "size": 1})
    assert set(rec.keys()) == {"op", "name", "local_path", "remote_path", "size"}
    assert "s3cret" not in repr(rec)


def test_save_load_roundtrip_no_mutation():
    store = {}
    records = [tq.build_record("upload", "/l/a", "/r/a"),
               tq.build_record("download", "/l/b", "/r/b")]
    new_store = tq.save_target(store, "h1", 22, records)
    assert store == {}                      # 入参不被修改
    assert tq.load_target(new_store, "h1", 22) == records
    assert tq.load_target(new_store, "h1", "22") == records


def test_targets_are_isolated():
    store = tq.save_target({}, "h1", 22, [tq.build_record("upload", "/l/a", "/r/a")])
    store = tq.save_target(store, "h2", 22, [tq.build_record("download", "/l/b", "/r/b")])
    assert len(tq.load_target(store, "h1", 22)) == 1
    assert len(tq.load_target(store, "h2", 22)) == 1
    assert tq.load_target(store, "h3", 22) == []
    assert tq.total_pending(store) == 2
    # 清掉 h1 不影响 h2
    store = tq.clear_target(store, "h1", 22)
    assert tq.load_target(store, "h1", 22) == []
    assert len(tq.load_target(store, "h2", 22)) == 1


def test_save_empty_removes_key():
    store = tq.save_target({}, "h", 22, [tq.build_record("upload", "/l/a", "/r/a")])
    store = tq.save_target(store, "h", 22, [])
    assert store == {}
    assert tq.total_pending(store) == 0
    # 全是垃圾记录等同空
    store = tq.save_target({}, "h", 22, [None, {"op": "nope"}])
    assert store == {}


def test_save_caps_records_keeping_fifo_order():
    records = [tq.build_record("upload", f"/l/{i}", f"/r/{i}") for i in range(tq.MAX_RECORDS_PER_TARGET + 50)]
    store = tq.save_target({}, "h", 22, records)
    kept = tq.load_target(store, "h", 22)
    assert len(kept) == tq.MAX_RECORDS_PER_TARGET
    assert kept[0]["local_path"] == "/l/0"           # 先入队的先保留
    assert kept[-1]["local_path"] == f"/l/{tq.MAX_RECORDS_PER_TARGET - 1}"


def test_garbage_store_tolerated():
    for bad in (None, "x", 5, []):
        assert tq.load_target(bad, "h", 22) == []
        assert tq.total_pending(bad) == 0
    # 目标值是字符串/内含垃圾项：合法项仍能读出
    assert tq.load_target({"h:22": "oops"}, "h", 22) == []
    mixed = {"h:22": [None, "x", {"op": "upload", "local_path": "/l/a", "remote_path": "/r"}]}
    assert len(tq.load_target(mixed, "h", 22)) == 1
    assert tq.total_pending(mixed) == 1


def test_describe_pending():
    records = [tq.build_record("upload", "/l/a", "/r/a"),
               tq.build_record("upload_dir", "/l/d", "/r/d"),
               tq.build_record("download", "/l/b", "/r/b")]
    text = tq.describe_pending(records)
    assert "3 个未完成的传输任务" in text
    assert "上传 1" in text and "上传目录 1" in text and "下载 1" in text
    assert tq.describe_pending([]) == "没有未完成的传输任务"
    assert tq.describe_pending([None, "x"]) == "没有未完成的传输任务"


def test_prune_store_drops_oldest():
    store = {}
    for i in range(4):
        store = tq.save_target(store, f"h{i}", 22, [tq.build_record("upload", "/l/a", "/r/a")])
    pruned, dropped = tq.prune_store(store, max_targets=2)
    assert dropped == 2
    assert list(pruned.keys()) == ["h2:22", "h3:22"]
    # 未超限时不动
    same, dropped0 = tq.prune_store({"a:1": []}, max_targets=5)
    assert dropped0 == 0 and same == {"a:1": []}


def test_constants_consistent():
    assert set(tq.PENDING_STATES) == {"queued", "running", "paused"}
    for op in tq.VALID_OPS:
        assert op in tq.OP_LABELS, op
    assert tq.QUEUE_KEY == "sftp_pending_queue"
    assert tq.MAX_RECORDS_PER_TARGET > 0
