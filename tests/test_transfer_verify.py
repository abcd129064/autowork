# -*- coding: utf-8 -*-
"""传输完整性校验（core/transfer_verify.py）单测

不依赖 paramiko / Qt：本文件在最小依赖解释器（E:\\ANACONDA）下也能收集运行。
覆盖：两侧一致 → 静默；少字节（截断）与多字节 → 抛 TransferSizeMismatch，
且异常属性/文案方向正确、不泄露无关信息。
"""

import pytest

from core.transfer_verify import (
    DIRECTION_DOWNLOAD, DIRECTION_UPLOAD, TransferSizeMismatch,
    check_transfer_size, describe_size,
)


# ---------------------------------------------------------------- 一致场景

@pytest.mark.parametrize("size", [0, 1, 1024, 5 * 1024 * 1024])
def test_equal_sizes_pass_for_both_directions(size):
    assert check_transfer_size(size, size, "a.txt") is None
    assert check_transfer_size(size, size, "a.txt",
                               direction=DIRECTION_DOWNLOAD) is None


def test_string_numbers_accepted():
    """stat/getsize 回来的可能是 str（测试替身/其他后端），按数字处理"""
    assert check_transfer_size("2048", "2048", "a.bin") is None
    with pytest.raises(TransferSizeMismatch):
        check_transfer_size("2048", "1024", "a.bin")


# ---------------------------------------------------------------- 不一致场景

def test_upload_truncated_raises_with_remote_hint():
    with pytest.raises(TransferSizeMismatch) as ei:
        check_transfer_size(4096, 1024, "big.bin", direction=DIRECTION_UPLOAD)
    err = ei.value
    assert (err.expected, err.actual, err.delta) == (4096, 1024, -3072)
    assert err.truncated is True
    assert err.name == "big.bin"
    assert err.direction == DIRECTION_UPLOAD
    assert "远端文件被截断" in str(err)
    assert "4096" in str(err) and "1024" in str(err) and "-3072" in str(err)


def test_download_truncated_raises_with_local_hint():
    with pytest.raises(TransferSizeMismatch) as ei:
        check_transfer_size(4096, 100, "x.dat", direction=DIRECTION_DOWNLOAD)
    err = ei.value
    assert err.truncated is True
    assert "本地文件被截断" in str(err)
    assert "远端文件被截断" not in str(err)


def test_larger_actual_uses_rewrite_hint():
    """实际比预期大：提示"被改写"而不是"被截断"（两个方向都覆盖）"""
    for direction in (DIRECTION_UPLOAD, DIRECTION_DOWNLOAD):
        with pytest.raises(TransferSizeMismatch) as ei:
            check_transfer_size(100, 200, "grow.log", direction=direction)
        err = ei.value
        assert err.truncated is False
        assert "比另一端大" in str(err)
        assert "截断" not in str(err)


def test_direction_defaults_to_upload():
    with pytest.raises(TransferSizeMismatch) as ei:
        check_transfer_size(10, 9, "a")
    assert ei.value.direction == DIRECTION_UPLOAD
    with pytest.raises(TransferSizeMismatch) as ei2:
        check_transfer_size(10, 9, "a", direction="")
    assert ei2.value.direction == DIRECTION_UPLOAD, "空方向应回退到 upload"


def test_message_carries_no_extra_payload():
    """文案只含文件名与两个数字：不夹带主机/账号/密码等（避免日志泄露）"""
    err = TransferSizeMismatch("a.txt", 10, 5)
    text = str(err)
    assert "a.txt" in text
    for leaked in ("password", "passwd", "root@", "192.168"):
        assert leaked not in text


def test_unreadable_actual_is_failure_not_silent_pass():
    """传输完成但读不到实际大小：算校验失败，且不谎报"相差 N 字节"""
    err = TransferSizeMismatch.unreadable("a.bin", 2048)
    assert err.actual is None and err.delta is None and err.truncated is False
    assert "无法读取远端文件大小" in str(err)
    assert "2048" in str(err)
    assert "相差" not in str(err)
    err_local = TransferSizeMismatch.unreadable("a.bin", 2048,
                                               direction=DIRECTION_DOWNLOAD)
    assert "无法读取本地文件大小" in str(err_local)


def test_exception_is_plain_exception_subclass():
    """classify_conn_error 对未知异常走 `return msg`，故 str(e) 必须自解释"""
    err = TransferSizeMismatch("a.txt", 10, 5)
    assert isinstance(err, Exception)
    assert str(err).startswith("大小校验失败 [a.txt]")


# ---------------------------------------------------------------- 文案工具

@pytest.mark.parametrize("num,expect", [
    (0, "0 B"), (1, "1 B"), (512, "512 B"),
    (1024, "1.0 KB"), (1536, "1.5 KB"),
    (1048576, "1.0 MB"), (3 * 1048576, "3.0 MB"),
    (1073741824, "1.0 GB"),
])
def test_describe_size(num, expect):
    assert describe_size(num) == expect


def test_describe_size_tolerates_garbage():
    assert describe_size("n/a") == "n/a"
    assert describe_size(None) == "None"
