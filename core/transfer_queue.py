# -*- coding: utf-8 -*-
"""SFTP 传输队列落盘（P2-2 步骤 1 / 2026-10-06）

背景：P2-1 给传输加了并发闸门（默认 3 条并行），超额任务进入"排队中"等待。
旧版队列只活在内存里 —— 应用退出、标签关闭、断线重连失败时，排队中的任务和
正在传输的任务一起悄悄消失，用户以为还在传，回头发现文件没动。

这里把**未完成**任务（排队中 / 传输中 / 已暂停）按目标 `host:port` 写进设置
文件；下次连上同一目标时由面板提示"是否重新加入队列"。

只存操作类型与路径：
  * 不存密码 —— 连接凭据仍走 core.credentials 的解析链（表单 > 本次会话 > 已保存）；
  * 不存进度偏移 —— 那是步骤 3 真断点续传才需要的（.part + offset）。
所有函数都是纯字典运算，便于在最小依赖解释器下单测。
"""

from __future__ import annotations

import os

__all__ = [
    "QUEUE_KEY", "MAX_RECORDS_PER_TARGET", "PENDING_STATES", "VALID_OPS",
    "OP_LABELS", "target_key", "build_record", "normalize_record",
    "load_target", "save_target", "clear_target", "total_pending",
    "describe_pending", "prune_store",
]

QUEUE_KEY = "sftp_pending_queue"
MAX_RECORDS_PER_TARGET = 200
# 与 sftp_window 内 info['state'] 的取值保持一致
PENDING_STATES = ("queued", "running", "paused")
VALID_OPS = ("upload", "download", "upload_dir", "download_dir")
OP_LABELS = {
    "upload": "上传", "download": "下载",
    "upload_dir": "上传目录", "download_dir": "下载目录",
}
_RECORD_KEYS = ("op", "name", "local_path", "remote_path", "size")


def target_key(host, port) -> str:
    """目标标识：同一 host:port 视为同一队列空间"""
    return f"{str(host).strip()}:{int(port)}"


def build_record(op, local_path, remote_path, name="", size=0):
    """构造一条队列记录；非法操作类型抛 ValueError（调用方 bug，不该静默）"""
    record = normalize_record({
        "op": op, "name": name, "local_path": local_path,
        "remote_path": remote_path, "size": size,
    })
    if record is None:
        raise ValueError(f"非法传输记录: op={op!r} local={local_path!r} remote={remote_path!r}")
    return record


def normalize_record(raw):
    """磁盘/内存里的一条记录 → 规范化 dict；不可用时返回 None（容忍手改配置）

    只保留 _RECORD_KEYS 六个字段：即使外部塞了密码之类的字段也不会被落盘。
    """
    if not isinstance(raw, dict):
        return None
    op = str(raw.get("op", "") or "")
    if op not in VALID_OPS:
        return None
    local_path = str(raw.get("local_path", "") or "")
    remote_path = str(raw.get("remote_path", "") or "")
    if not local_path or not remote_path:
        return None
    try:
        size = max(0, int(raw.get("size", 0) or 0))
    except (TypeError, ValueError):
        size = 0
    name = str(raw.get("name", "") or "")
    if not name:
        name = os.path.basename(local_path or remote_path) or remote_path
    return {"op": op, "name": name, "local_path": local_path,
            "remote_path": remote_path, "size": size}


def _as_store(store) -> dict:
    return store if isinstance(store, dict) else {}


def load_target(store, host, port):
    """取某目标下待恢复的记录（顺序 = 入队顺序）；损坏项被丢弃"""
    records = _as_store(store).get(target_key(host, port))
    if not isinstance(records, list):
        return []
    out = []
    for raw in records:
        rec = normalize_record(raw)
        if rec is not None:
            out.append(rec)
    return out


def save_target(store, host, port, records):
    """写入某目标的记录，返回新的 store（不修改入参）；空列表则删除该目标键"""
    key = target_key(host, port)
    new_store = dict(_as_store(store))
    cleaned = []
    for raw in (records or []):
        rec = normalize_record(raw)
        if rec is not None:
            cleaned.append(rec)
    if not cleaned:
        new_store.pop(key, None)
        return new_store
    new_store[key] = cleaned[:MAX_RECORDS_PER_TARGET]
    return new_store


def clear_target(store, host, port):
    """删除某目标的记录，返回新的 store"""
    new_store = dict(_as_store(store))
    new_store.pop(target_key(host, port), None)
    return new_store


def total_pending(store) -> int:
    """全部目标下待恢复记录总数"""
    total = 0
    for records in _as_store(store).values():
        if isinstance(records, list):
            total += sum(1 for raw in records if normalize_record(raw) is not None)
    return total


def describe_pending(records) -> str:
    """给提示框用的一句话摘要，如 "3 个未完成的传输任务（上传 2 · 下载 1）" """
    counts = {}
    valid = 0
    for raw in (records or []):
        rec = normalize_record(raw)
        if rec is None:
            continue
        valid += 1
        label = OP_LABELS.get(rec["op"], rec["op"])
        counts[label] = counts.get(label, 0) + 1
    if not valid:
        return "没有未完成的传输任务"
    detail = " · ".join(f"{label} {n}" for label, n in counts.items())
    return f"{valid} 个未完成的传输任务（{detail}）"


def prune_store(store, max_targets=50):
    """store 里目标数超限时丢弃超额目标（防设置文件无限膨胀）

    只在写入前调用；返回 (新 store, 丢弃目标数)。
    """
    new_store = dict(_as_store(store))
    keys = [k for k in new_store.keys()]
    dropped = 0
    while len(keys) > max_targets:
        key = keys.pop(0)      # 字典保序：丢最早写入的目标
        new_store.pop(key, None)
        dropped += 1
    return new_store, dropped
