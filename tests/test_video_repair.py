# -*- coding: utf-8 -*-
"""视频修复后端（windows/tools/video_repair.py）离线单测。

全部用合成字节，不碰 ffmpeg、不碰真实录像：盒结构与起始码是纯字节逻辑，
能在 tmp_path 上完整复现。真文件端到端在 tools/smoke/smoke_video_repair_tab.py。
"""
import os

import pytest

from windows.tools import video_repair as vr

GOP = vr.GOP_CODE
VOP = vr.VOP_CODE


def _box(btype, body, size=None):
    """拼一个 8 字节头的普通盒；size 传 0 用来复现「吃到 EOF」的占位写法"""
    total = 8 + len(body) if size is None else size
    return total.to_bytes(4, "big") + btype + body


def _large_box(btype, body):
    """size=1 + 64 位 largesize 的盒头（>4GB 的 mdat 走这支）"""
    total = 16 + len(body)
    return (b"\x00\x00\x00\x01" + btype + total.to_bytes(8, "big") + body)


def _write(path, data):
    with open(path, "wb") as f:
        f.write(data)
    return str(path)


def _truncated(payload):
    """掉电截断文件的典型形态：ftyp + mdat(size=0)，没有 moov"""
    return _box(b"ftyp", b"isom\x00\x00\x02\x00isom") + _box(b"mdat", payload, 0)


def _stream(*frames):
    """按 GOP/VOP 起始码拼一段裸流，每帧后面填指定长度的字节"""
    out = bytearray()
    for kind, length in frames:
        out += GOP if kind == "gop" else VOP
        out += b"\x20" * length
    return bytes(out)


# ==================== scan_boxes ====================

def test_scan_boxes_tail_extended_mdat(tmp_path):
    """size=0 必须解释成「延伸到文件尾」，否则负载长度算错、裁剪点也全错"""
    payload = _stream(("gop", 10), ("vop", 5))
    path = _write(tmp_path / "broken.mp4", _truncated(payload))
    boxes = vr.scan_boxes(path)
    assert boxes["ok"] is True
    assert boxes["has_moov"] is False
    assert boxes["mdat_last"] is True
    # 负载起点 = ftyp 总长 + mdat 自己的 8 字节盒头，不是 mdat 的起始位置
    head = len(_box(b"ftyp", b"isom\x00\x00\x02\x00isom")) + 8
    assert boxes["payload_off"] == head
    assert boxes["payload_size"] == os.path.getsize(path) - head
    assert [t for t, _p, _s in boxes["boxes"]] == ["ftyp", "mdat"]


def test_scan_boxes_detects_moov(tmp_path):
    """有 moov 就不是掉电截断，后面所有修复逻辑都不该放行"""
    payload = _stream(("gop", 10), ("vop", 5))
    data = (_box(b"ftyp", b"isom") + _box(b"mdat", payload)
            + _box(b"moov", b"\x00" * 16))
    boxes = vr.scan_boxes(_write(tmp_path / "ok.mp4", data))
    assert boxes["has_moov"] is True
    assert boxes["mdat_last"] is False
    assert boxes["ok"] is False


def test_scan_boxes_largesize(tmp_path):
    """size=1 时真实长度在后 8 字节，按 8 字节头处理会把负载起点算错 8 字节"""
    payload = _stream(("gop", 10), ("vop", 5))
    data = _box(b"ftyp", b"isom") + _large_box(b"mdat", payload)
    path = _write(tmp_path / "large.mp4", data)
    boxes = vr.scan_boxes(path)
    assert boxes["payload_off"] == 12 + 16
    assert boxes["boxes"][1][2] == 16 + len(payload)


def test_scan_boxes_rejects_trailing_bytes(tmp_path):
    """盒链没吃到 EOF 说明中间被截过，切点推不出来，必须拒修而不是产出坏文件"""
    data = (_box(b"ftyp", b"isom")
            + _box(b"mdat", _stream(("gop", 10), ("vop", 5)), 8 + 15)
            + b"\xff\xff\xff\xff")
    boxes = vr.scan_boxes(_write(tmp_path / "stray.mp4", data))
    assert boxes["ok"] is False
    assert "游离" in boxes["reason"]


def test_scan_boxes_rejects_tiny_file(tmp_path):
    """连一个盒头都装不下，不能走 mmap 循环（会返回空 boxes 后被当成可修）"""
    boxes = vr.scan_boxes(_write(tmp_path / "tiny.mp4", b"\x00" * 10))
    assert boxes["ok"] is False
    assert "文件过小" in boxes["reason"]


def test_scan_boxes_rejects_file_without_mdat(tmp_path):
    path = _write(tmp_path / "no_mdat.mp4", _box(b"ftyp", b"isom") + _box(b"free", b"\x00" * 8))
    boxes = vr.scan_boxes(path)
    assert boxes["ok"] is False
    assert "mdat" in boxes["reason"]


# ==================== scan_frames ====================

def test_scan_frames_offsets_and_tail(tmp_path):
    """偏移必须相对负载起点：repair() 拿它直接当拷贝长度用"""
    payload = _stream(("gop", 100), ("vop", 80), ("vop", 80), ("vop", 20))
    path = _write(tmp_path / "broken.mp4", _truncated(payload))
    boxes = vr.scan_boxes(path)
    frames = vr.scan_frames(path, boxes["payload_off"], boxes["payload_size"])
    assert frames["gops"] == [0]
    assert frames["vops"] == [104, 188, 272]
    assert frames["frame_count"] == 3
    assert frames["gop_count"] == 1
    assert frames["median_frame"] == 84
    assert frames["last_vop_tail"] == 24
    # 末帧只剩 24 B，远低于中位 84/2 → 判定为断电时写了一半的残帧
    assert frames["last_frame_incomplete"] is True
    assert frames["is_h264"] is False


def test_scan_frames_flags_h264(tmp_path):
    """认错码流会产出满屏马赛克的「成功」文件，H.264 必须在体检阶段就挡掉"""
    payload = vr.H264_SPS_CODE + b"\x00" * 40 + _stream(("gop", 60), ("vop", 60))
    path = _write(tmp_path / "h264.mp4", _truncated(payload))
    boxes = vr.scan_boxes(path)
    frames = vr.scan_frames(path, boxes["payload_off"], boxes["payload_size"])
    assert frames["is_h264"] is True


def test_scan_frames_complete_last_frame(tmp_path):
    """恰好断在帧边界时末帧长度等于中位数，此时不该裁掉任何一帧"""
    payload = _stream(("gop", 80), ("vop", 80), ("vop", 80))
    path = _write(tmp_path / "edge.mp4", _truncated(payload))
    boxes = vr.scan_boxes(path)
    frames = vr.scan_frames(path, boxes["payload_off"], boxes["payload_size"])
    assert frames["last_frame_incomplete"] is False


# ==================== esds / VOL ====================

def _esds_chain(vol, flags=0x00):
    """按 0x03 ES → 0x04 DecCfg → 0x05 DecSpecificInfo 拼描述符链"""
    d5 = bytes([0x05, len(vol)]) + vol
    d4_body = bytes(13) + d5
    d4 = bytes([0x04, len(d4_body)]) + d4_body
    d3_body = b"\x00\x02" + bytes([flags]) + (b"\x00\x01" if flags & 0x80 else b"") + d4
    return bytes([0x03, len(d3_body)]) + d3_body


def _esds_file(tmp_path, chain, name="ref.mp4"):
    """esds 命中位置在盒类型字段上，后面还要留 4 字节 version/flags 才是描述符"""
    body = b"\x00\x00\x00\x00" + chain
    data = _box(b"ftyp", b"isom") + _box(b"moov", _box(b"esds", body))
    return _write(tmp_path / name, data)


def test_read_vol_extracts_descriptor_bytes(tmp_path):
    vol = b"\x00\x00\x01\xb0\x01\x00\x12\x00"
    assert vr.read_vol(_esds_file(tmp_path, _esds_chain(vol))) == vol


def test_read_vol_skips_depends_on_es_id(tmp_path):
    """flags 最高位置位时 ES_Descriptor 里多两字节 dependsOn_ES_ID，漏跳会取到错位字节"""
    vol = b"\x00\x00\x01\xb2\x00\x00\x08\x00"
    path = _esds_file(tmp_path, _esds_chain(vol, flags=0x80))
    assert vr.read_vol(path) == vol


def test_parse_esds_rejects_non_startcode(tmp_path):
    """解出来的内容不以 00 00 01 开头说明层级对不上，写进文件只会做出打不开的产物"""
    assert vr.parse_esds(_esds_chain(b"nope-nope")) is None
    assert vr.parse_esds(b"\x99\x02\xab\xcd") is None


def test_read_vol_on_empty_and_esds_less(tmp_path):
    """mmap 不接受 0 字节文件；esds 之后被截断也不能抛异常"""
    assert vr.read_vol(_write(tmp_path / "empty.mp4", b"")) is None
    assert vr.read_vol(_write(tmp_path / "nonesds.mp4", b"\x00" * 32)) is None
    path = _write(tmp_path / "short.mp4", _box(b"ftyp", b"isom")
                  + _box(b"moov", _box(b"esds", b"\x00\x00\x00\x00\x03\x40\x05")))
    assert vr.read_vol(path) is None


# ==================== pick_cut ====================

def test_pick_cut_auto_drops_dangling_gop_header():
    """切在最后一个 VOP 上会把所属 GOP 头留在流尾，实测解码报 header damaged"""
    vops, gops = [10, 100, 400, 700], [0, 380]
    assert vr.pick_cut(vops, gops, "auto") == 380
    assert vr.pick_cut(vops, gops, "vop") == 700


def test_pick_cut_auto_falls_back_to_first_frame():
    """整个文件只有一个 GOP 头且它就在残帧后面时，没有更早的 GOP 可退"""
    assert vr.pick_cut([100, 400], [410], "auto") == 100


def test_pick_cut_without_frames_raises():
    with pytest.raises(vr.RepairError):
        vr.pick_cut([], [])


# ==================== probe_broken ====================

def _broken_payload():
    return _stream(("gop", 90), ("vop", 90), ("vop", 90), ("vop", 9))


def test_probe_broken_accepts_power_loss_shape(tmp_path):
    path = _write(tmp_path / "broken.mp4", _truncated(_broken_payload()))
    result = vr.probe_broken(path)
    assert result["ok"] is True
    assert result["frames"]["frame_count"] == 3


@pytest.mark.parametrize("data,keyword", [
    (_box(b"ftyp", b"isom") + _box(b"mdat", _broken_payload())
     + _box(b"moov", b"\x00" * 16), "moov"),
    (_truncated(b"\x00" * 60), "帧起始码"),
    (_truncated(vr.H264_SPS_CODE + b"\x00" * 40 + _stream(("gop", 60), ("vop", 60))), "H.264"),
    (_truncated(_stream(("gop", 60), ("gop", 60))), "帧起始码"),
])
def test_probe_broken_rejects_each_unsupported_shape(tmp_path, data, keyword):
    """每条拒绝都要给出可执行的原因，否则用户只知道「失败」而不知道该换哪个文件"""
    result = vr.probe_broken(_write(tmp_path / "x.mp4", data))
    assert result["ok"] is False
    assert keyword in result["reason"]


def test_probe_broken_missing_file(tmp_path):
    result = vr.probe_broken(str(tmp_path / "nope.mp4"))
    assert result["ok"] is False
    assert "不存在" in result["reason"]


# ==================== discover_reference ====================

def test_discover_reference_prefers_newest_valid_sibling(tmp_path, monkeypatch):
    """同目录邻居比用户手挑可靠，但要按 mtime 倒序、并排掉自身与已生成的产物"""
    broken = _write(tmp_path / "20261105_195736.mp4", _truncated(_broken_payload()))
    old = _write(tmp_path / "older.mp4", _truncated(_broken_payload()))
    good = _write(tmp_path / "newer.mp4", _box(b"ftyp", b"isom")
                  + _box(b"mdat", b"\x00" * 16) + _box(b"moov", b"\x00" * 16))
    _write(tmp_path / "already_repaired.mp4", _truncated(_broken_payload()))
    os.utime(old, (1_600_000_000, 1_600_000_000))
    os.utime(good, (1_700_000_000, 1_700_000_000))
    monkeypatch.setattr(vr, "probe_reference", lambda p, ffprobe=None: {"codec": "mpeg4"})
    assert vr.discover_reference(broken) == good


def test_discover_reference_skips_wrong_codec(tmp_path, monkeypatch):
    broken = _write(tmp_path / "broken.mp4", _truncated(_broken_payload()))
    h264 = _write(tmp_path / "other.mp4", _box(b"ftyp", b"isom")
                  + _box(b"mdat", b"\x00" * 16) + _box(b"moov", b"\x00" * 16))
    monkeypatch.setattr(vr, "probe_reference",
                        lambda p, ffprobe=None: {"codec": "h264"})
    assert vr.discover_reference(broken) is None


def test_discover_reference_survives_probe_failure(tmp_path, monkeypatch):
    """同目录里常有半截文件，探测抛错只能跳过，不能让整个自动发现失败"""
    broken = _write(tmp_path / "broken.mp4", _truncated(_broken_payload()))
    # 候选必须带 moov 才会走到 ffprobe 这一步，否则在盒结构阶段就被跳过了
    bad = _write(tmp_path / "aaa_bad.mp4", _box(b"ftyp", b"isom")
                 + _box(b"mdat", b"\x00" * 16) + _box(b"moov", b"\x00" * 16))
    good = _write(tmp_path / "good.mp4", _box(b"ftyp", b"isom")
                  + _box(b"mdat", b"\x00" * 16) + _box(b"moov", b"\x00" * 16))
    os.utime(bad, (1_900_000_000, 1_900_000_000))
    os.utime(good, (1_800_000_000, 1_800_000_000))

    def _flaky(p, ffprobe=None):
        if p == good:
            return {"codec": "mpeg4"}
        raise vr.RepairError("ffprobe 挂了")

    monkeypatch.setattr(vr, "probe_reference", _flaky)
    assert vr.discover_reference(broken) == good


# ==================== repair 的前置拒绝 ====================

def test_repair_refuses_output_equal_to_input(tmp_path):
    """输出撞原件会把唯一的数据源覆盖掉，这条必须最先硬拦"""
    broken = _write(tmp_path / "broken.mp4", _truncated(_broken_payload()))
    ref = _write(tmp_path / "ref.mp4", _truncated(_broken_payload()))
    with pytest.raises(vr.RepairError, match="不能与损坏文件相同"):
        vr.repair(broken, ref, broken, ffmpeg="ffmpeg", ffprobe="ffprobe")


def test_repair_requires_ffmpeg(tmp_path, monkeypatch):
    """PATH 里没有 ffmpeg 时给的是安装指引，而不是 subprocess 的 FileNotFound"""
    broken = _write(tmp_path / "broken.mp4", _truncated(_broken_payload()))
    ref = _write(tmp_path / "ref.mp4", _truncated(_broken_payload()))
    monkeypatch.setattr(vr, "find_ffmpeg", lambda: (None, None))
    with pytest.raises(vr.RepairError, match="ffmpeg"):
        vr.repair(broken, ref, str(tmp_path / "out.mp4"))


def test_sha_range_matches_whole_file_digest(tmp_path):
    """无损证明依赖区间哈希，区间取满时必须等于整文件哈希"""
    import hashlib
    data = _truncated(_broken_payload())
    path = _write(tmp_path / "s.mp4", data)
    assert vr.sha_range(path, 0, len(data)) == hashlib.sha256(data).hexdigest()
    assert vr.sha_range(path, 8, 4) != vr.sha_range(path, 8, 5)


def test_backend_stays_qt_free():
    """后端要能被离线单测和命令行直接引用，一旦牵进 Qt 就绑死 GUI 环境"""
    with open(vr.__file__, encoding="utf-8") as f:
        src = f.read()
    assert "PySide6" not in src
    assert "qfluentwidgets" not in src
