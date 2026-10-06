# -*- coding: utf-8 -*-
"""传输完成后的完整性校验：两侧字节数比对（P2-2 步骤 1 / 2026-10-06）

背景：`paramiko.SFTPClient.put/get` 正常返回就被当成"传输成功"，但下列情况
paramiko 不报错、UI 却显示成功，用户直到打开文件才发现是坏文件：
  * 远端磁盘配额写满（EDQUOT / ENOSPC）导致写入被截断；
  * 传输途中连接抖动、写出提前结束；
  * 传输期间文件被另一端改写，或本地磁盘写满。

本模块只做**字节数**校验：传输完成后一次 stat，成本可忽略，且离线可测。
分块哈希校验更严格，但要重读整个文件（大文件代价高），留待真断点续传
阶段（P2-2 步骤 3）按需评估。

设计约束：零 Qt、零 paramiko、零磁盘访问 —— 便于在最小依赖解释器下单测。
"""

from __future__ import annotations

__all__ = ["TransferSizeMismatch", "check_transfer_size", "describe_size",
           "DIRECTION_UPLOAD", "DIRECTION_DOWNLOAD"]

DIRECTION_UPLOAD = "upload"
DIRECTION_DOWNLOAD = "download"


class TransferSizeMismatch(Exception):
    """传输两侧字节数不一致（判定为失败，不再报"已上传/已下载"）

    name: 文件名或相对路径（只用于提示，不含敏感信息）
    expected: 预期字节数（上传=本地大小；下载=远端 stat 大小）
    actual: 实际字节数（上传=远端 stat 大小；下载=本地大小）；None = 读不到
    direction: 'upload' / 'download'，决定排查提示的措辞
    """

    def __init__(self, name: str, expected: int, actual, *,
                 direction: str = DIRECTION_UPLOAD, side: str = ""):
        self.name = str(name)
        self.expected = int(expected)
        self.actual = None if actual is None else int(actual)
        self.direction = direction or DIRECTION_UPLOAD
        self.side = side or ("远端" if self.direction == DIRECTION_UPLOAD else "本地")
        super().__init__(self.message())

    @classmethod
    def unreadable(cls, name: str, expected, *, direction: str = DIRECTION_UPLOAD,
                   side: str = "") -> "TransferSizeMismatch":
        """传输完成但读不到某一侧的大小（stat 失败）：不算通过校验

        side 指定读不到大小的是哪一侧（上传的远端 / 下载的远端或本地），
        留空时按 direction 推断。
        """
        return cls(name, expected, None, direction=direction, side=side)

    @property
    def delta(self):
        """实际 - 预期；负数表示少了（被截断）；读不到时为 None"""
        if self.actual is None:
            return None
        return self.actual - self.expected

    @property
    def truncated(self) -> bool:
        return self.actual is not None and self.actual < self.expected

    def hint(self) -> str:
        if self.actual is None:
            return f"无法读取{self.side}文件大小，无法确认传输完整性"
        if self.delta < 0:
            return f"{self.side}文件被截断（{self.side}磁盘可能写满或传输中途断开）"
        return f"{self.side}文件比另一端大（传输期间被改写？）"

    def message(self) -> str:
        head = f"大小校验失败 [{self.name}]:"
        if self.expected > 0:
            head += f" 预期 {self.expected} 字节"
        if self.actual is not None:
            head += f"、实际 {self.actual} 字节（相差 {self.delta:+d}）"
        return f"{head} · {self.hint()}"


def describe_size(num) -> str:
    """字节数转人类可读（仅用于提示文案）"""
    try:
        size = float(num)
    except (TypeError, ValueError):
        return str(num)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size) < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"      # pragma: no cover - 循环内已返回


def check_transfer_size(expected, actual, name, *,
                        direction: str = DIRECTION_UPLOAD) -> None:
    """核对两侧字节数：不一致抛 TransferSizeMismatch，一致则静默返回

    direction='upload' 时 expected 应为本地大小、actual 应为远端 stat 大小；
    direction='download' 时相反。调用方负责取得两个数字（本函数只做判定）。
    """
    exp, act = int(expected), int(actual)
    if exp != act:
        raise TransferSizeMismatch(name, exp, act, direction=direction)
