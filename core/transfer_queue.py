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
队列按目标隔离、不跨目标排序：谁先连上谁先恢复，符合用户对「刚才那台机器」的直觉。
"""

from __future__ import annotations

import os

__all__ = [
    "QUEUE_KEY", "MAX_RECORDS_PER_TARGET", "PENDING_STATES", "VALID_OPS",
    "OP_LABELS", "target_key", "build_record", "normalize_record",
    "load_target", "save_target", "clear_target", "total_pending",
    "describe_pending", "prune_store",
]

# 键名一旦改动，老用户 settings.json 里的旧队列就地失联，只能当已丢失处理。
QUEUE_KEY = "sftp_pending_queue"
# 单目标最多留 200 条：settings.json 每次整份重写，再大只会拖慢启动读盘。
MAX_RECORDS_PER_TARGET = 200
# 与 sftp_window 内 info['state'] 的取值保持一致
PENDING_STATES = ("queued", "running", "paused")
# 操作类型同时是落盘白名单：不在这四项里的记录一律当脏数据丢弃。
VALID_OPS = ("upload", "download", "upload_dir", "download_dir")
# 中文标签只给提示框看，落盘永远存 op 原值，改文案不影响已有数据。
OP_LABELS = {
    "upload": "上传", "download": "下载",
    "upload_dir": "上传目录", "download_dir": "下载目录",
}
# 落盘字段白名单：外部塞进来的密码、token 之类不会被写进设置文件。
_RECORD_KEYS = ("op", "name", "local_path", "remote_path", "size")


def target_key(host, port) -> str:
    """目标标识：同一 host:port 视为同一队列空间"""
    # port 先 int 再拼："22" 与 22 必须落到同一个键，否则重连后读不到自己的队列。
    return f"{str(host).strip()}:{int(port)}"


def build_record(op, local_path, remote_path, name="", size=0):
    """构造一条队列记录；非法操作类型抛 ValueError（调用方 bug，不该静默）

    name/size 可省略：它们只影响列表展示，调用方不必为展示字段操心。"""
    # 走同一套 normalize：构造阶段就挡住脏参数，而不是等重放时才发现传不了。
    record = normalize_record({
        "op": op, "name": name, "local_path": local_path,
        "remote_path": remote_path, "size": size,
    })
    if record is None:
        # 调用方 bug 不该静默：返回 None 会让任务凭空消失，抛错能在测试里当场暴露。
        raise ValueError(f"非法传输记录: op={op!r} local={local_path!r} remote={remote_path!r}")
    return record


def normalize_record(raw):
    """磁盘/内存里的一条记录 → 规范化 dict；不可用时返回 None（容忍手改配置）

    只保留 _RECORD_KEYS 六个字段：即使外部塞了密码之类的字段也不会被落盘。
    """
    # 容忍手改配置与旧版本残留：解析失败一律退化成 None，不让一条脏数据拖垮整个队列。
    if not isinstance(raw, dict):
        return None
    # 统一转 str：配置字段可能被手改成数字或 None，后面的比较才安全。
    op = str(raw.get("op", "") or "")
    if op not in VALID_OPS:
        return None
    local_path = str(raw.get("local_path", "") or "")
    remote_path = str(raw.get("remote_path", "") or "")
    # 路径缺一即弃：没有源或目标的记录重放不了，留着只会在提示框里报假数。
    if not local_path or not remote_path:
        return None
    # size 只用于展示，解析失败按 0 处理，不值得为它作废整条记录。
    try:
        size = max(0, int(raw.get("size", 0) or 0))
    except (TypeError, ValueError):
        size = 0
    name = str(raw.get("name", "") or "")
    # name 缺省退回 basename：列表里总得有个能读的显示名。
    if not name:
        name = os.path.basename(local_path or remote_path) or remote_path
    return {"op": op, "name": name, "local_path": local_path,
            "remote_path": remote_path, "size": size}


def _as_store(store) -> dict:
    """非 dict 一律当空表：调用方传 None 或旧格式时不必各自判空。"""
    return store if isinstance(store, dict) else {}


def load_target(store, host, port):
    """取某目标下待恢复的记录（顺序 = 入队顺序）；损坏项被丢弃"""
    records = _as_store(store).get(target_key(host, port))
    # 值不是 list 说明配置被手改成别的类型，按空队列处理而不是抛错。
    if not isinstance(records, list):
        return []
    # 顺序遍历而非转 set：恢复后按入队顺序重新排队，用户看到的次序才不会变。
    out = []
    for raw in records:
        rec = normalize_record(raw)
        if rec is not None:
            out.append(rec)
    return out


def save_target(store, host, port, records):
    """写入某目标的记录，返回新的 store（不修改入参）；空列表则删除该目标键

    本函数不碰 settings.json：落盘由调用方统一做，便于批量合并一次写入。"""
    key = target_key(host, port)
    # 复制一份再改：返回的 store 与入参不是同一对象，调用方整体写盘才不会半更新。
    new_store = dict(_as_store(store))
    cleaned = []
    # (records or []) 容忍 None：调用方常把 UI 收集结果原样传进来，未必先判空。
    for raw in (records or []):
        rec = normalize_record(raw)
        if rec is not None:
            cleaned.append(rec)
    # 清空后不留空键：settings.json 里堆满 [] 会让 prune_store 的目标计数失真。
    if not cleaned:
        new_store.pop(key, None)
        return new_store
    # 超限丢列表尾部的（最后入队的），保留最早排的那批——先来的更该被恢复。
    new_store[key] = cleaned[:MAX_RECORDS_PER_TARGET]
    return new_store


def clear_target(store, host, port):
    """删除某目标的记录，返回新的 store"""
    new_store = dict(_as_store(store))
    # 键不存在也算成功：调用方不必先查再删。
    new_store.pop(target_key(host, port), None)
    return new_store


def total_pending(store) -> int:
    """全部目标下待恢复记录总数

    启动时用它决定要不要弹恢复提示，所以要便宜：纯遍历，不碰磁盘。"""
    total = 0
    for records in _as_store(store).values():
        if isinstance(records, list):
            # 逐个 normalize 再计数：损坏项不算待恢复，否则提示「有 3 条」点进去只剩 1 条。
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
        # dict 保序：按首次出现顺序排，提示框里上传/下载的次序才稳定。
        counts[label] = counts.get(label, 0) + 1
    # 空队列也要给一句话：提示框的正文不能是空标题。
    if not valid:
        return "没有未完成的传输任务"
    # 只列实际出现的类型：没有下载任务就不写「下载 0」。
    detail = " · ".join(f"{label} {n}" for label, n in counts.items())
    return f"{valid} 个未完成的传输任务（{detail}）"


def prune_store(store, max_targets=50):
    """store 里目标数超限时丢弃超额目标（防设置文件无限膨胀）

    只在写入前调用；返回 (新 store, 丢弃目标数)。
    """
    new_store = dict(_as_store(store))
    # 先复制键列表：边遍历边 pop 会直接 RuntimeError。
    keys = [k for k in new_store.keys()]
    dropped = 0
    # 上限由调用方给：默认 50 覆盖日常几台机器，又不至于让设置文件失控。
    while len(keys) > max_targets:
        key = keys.pop(0)      # 字典保序：丢最早写入的目标
        new_store.pop(key, None)
        dropped += 1
    return new_store, dropped
