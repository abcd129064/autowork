# -*- coding: utf-8 -*-
"""core/dahua_sdk 编码设置相关调用层的离线回归（假 SDK，不连真机）。

覆盖真机（DH-IPC-HAC9488XY-HY @ 4238）探针暴露过的五类缺陷：
1. 一次 UI 任务里背靠背发多个配置请求会撞隧道协议超时——节流必须下沉到 core；
2. "Error occurs when verify returned data"（设备拒收）被当成可重试错误，
   白等一倍时间还可能把误写再写一遍；
3. set_json_config 按字符数报载荷长度，中文配置名会被截成半截 JSON；
4. VideoColor 按 table[0] 取色，撞上"第 0 行是停用时段"就读到没人用的参数；
5. 编码配置把通道写死成 0，多通道设备切通道毫无作用。
用法：pytest tests/test_dahua_sdk_encode.py -q
"""
import json
import time

import pytest

from core import dahua_sdk
from core.dahua_sdk import DahuaClient, DahuaSdkError, classify_error


# ------------------------------------------------------------------ 假 SDK


class _FakeSDK:
    """按大华包装的调用形状模仿一个"能读写 JSON 配置 + 能记住结构体"的设备。

    reject / no_land 两个开关分别对应真机两种失败：SetConfig 直接回失败码、
    以及回成功但读回原值。二者对上层是完全不同的处置依据，必须能分开模拟。
    """

    def __init__(self, configs=None, reject=(), no_land=()):
        self.configs = {k: json.loads(json.dumps(v)) for k, v in
                        (configs or {}).items()}
        self.reject = set(reject)
        self.no_land = set(no_land)
        self.structs = {}
        self.sets = []          # [(name, payload_bytes)]
        self.gets = []          # [(name, channel)]
        self.cfg_gets = []      # [(cfg_id, channel)]
        self.cfg_sets = []      # [(cfg_id, channel, size)]

    # ---- JSON 配置（GetNewDevConfig / SetNewDevConfig）----

    def GetNewDevConfig(self, lid, name, ch, buf, size, err, wait, res=None):
        self.gets.append((name, ch))
        if name not in self.configs:
            return 0
        data = json.dumps({"params": self.configs[name]}).encode()
        if len(data) > size:
            return 0
        buf.value = data
        return 1

    def SetNewDevConfig(self, lid, name, ch, buf, size, err, restart, wait):
        payload = buf.value[:size]
        self.sets.append((name, payload))
        if name in self.reject:
            return 0
        if name not in self.no_land:
            try:
                # 载荷就是 params 那一段：设备侧存成 GET 时回的同形结构
                self.configs[name] = json.loads(payload.decode())
            except ValueError:
                return 0
        return 1

    # ---- 结构体配置（GetConfig / SetConfig）----

    def GetConfig(self, lid, cfg_id, ch, buf, size, wait, res=None):
        self.cfg_gets.append((cfg_id, ch))
        data = self.structs.get(cfg_id)
        if data is None:
            return 0
        buf.raw = bytes(data)
        return 1

    def SetConfig(self, lid, cfg_id, ch, st, size, wait, restart, res=None):
        self.cfg_sets.append((cfg_id, ch, size))
        if cfg_id in self.reject:
            return 0
        if cfg_id not in self.no_land:
            self.structs[cfg_id] = bytes(st)
        return 1

    def GetLastErrorMessage(self):
        return "Error occurs when verify returned data."


def _client(sdk):
    """半只 DahuaClient：绕过登录，只装配调用层需要的状态"""
    c = object.__new__(DahuaClient)
    c._login_id = 1
    c._m = dahua_sdk.module()
    c._sdk = sdk
    c._last_cfg = 0.0
    c._pace_lock = None
    return c


# ------------------------------------------------------------------ 错误翻译


@pytest.mark.parametrize("raw,kind", [
    ("Error occurs when verify returned data.", "rejected"),
    ("The device does not support current operation.", "unsupported"),
    ("The current SDK does not support this funcntion.", "unsupported"),
    ("Protocol error it may result from network timeout", "transport"),
    ("User parameter is illegal", "illegal"),
    ("something nobody has seen", "other"),
])
def test_classify_error_categorises(raw, kind):
    """设备拒收 / 固件不支持 / 链路超时必须分开归类：只有第三类值得重试"""
    hint, got = classify_error(raw)
    assert got == kind
    if kind != "other":
        assert hint != raw, "认得出的错误码要翻成中文处置建议"


def test_protocol_error_is_translated_into_actionable_text():
    """抛给用户的消息要带"该怎么办"，不能只把 SDK 原文贴出来"""
    c = _client(_FakeSDK())
    err = c._fail("写入编码配置")
    assert "设备拒绝" in str(err)
    assert err.kind == "rejected"


def test_retry_only_on_transport_failure():
    """rejected 是确定性结论：重试只会白等一倍时间，还把误写风险翻倍"""
    calls = []

    def fn():
        calls.append(1)
        raise DahuaSdkError("写入失败：设备拒绝", "rejected")

    with pytest.raises(DahuaSdkError):
        _client(_FakeSDK())._with_retry(fn)
    assert len(calls) == 1


def test_retry_does_retry_transport_error():
    calls = []

    def fn():
        calls.append(1)
        if len(calls) == 1:
            raise DahuaSdkError("读取失败：链路协议超时", "transport")
        return "ok"

    assert _client(_FakeSDK())._with_retry(fn) == "ok"
    assert len(calls) == 2


# ------------------------------------------------------------------ 节流


def test_pace_enforces_gap_between_config_calls():
    """相邻配置请求至少隔 _CFG_MIN_GAP_S：隧道下背靠背会 Protocol error"""
    c = _client(_FakeSDK())
    c._pace()
    t0 = time.monotonic()
    c._pace()
    assert time.monotonic() - t0 >= dahua_sdk._CFG_MIN_GAP_S * 0.8


def test_json_read_and_write_are_both_paced():
    """一次 RMW 内部连着 读-写-回读 三个请求，节流要覆盖到每一次"""
    sdk = _FakeSDK(configs={"VideoColor": {"table": [
        {"Brightness": 50, "TimeSection": "1 00:00:00-24:00:00"}]}})
    c = _client(sdk)
    t0 = time.monotonic()
    c.set_video_color(brightness=60)
    elapsed = time.monotonic() - t0
    # 读 + 写 + 回读 = 3 次请求 ⇒ 至少补出 2 个间距
    assert elapsed >= 2 * dahua_sdk._CFG_MIN_GAP_S * 0.8
    assert len(sdk.gets) == 2 and len(sdk.sets) == 1


# ------------------------------------------------------------------ JSON 载荷


def test_set_json_config_reports_byte_length_not_char_length():
    """ensure_ascii=False 时中文一字符占 2~3 字节：按 len(str) 传会把载荷
    截成半截，设备收到坏 JSON 仍可能回成功，看起来像"固件不落库" """
    sdk = _FakeSDK(configs={"ChannelTitle": {"table": {"Name": "旧名"}}})
    c = _client(sdk)
    params = {"table": {"Name": "前台大厅"}}
    c.set_json_config("ChannelTitle", params)
    name, payload = sdk.sets[-1]
    raw = json.dumps(params, ensure_ascii=False)
    assert name == "ChannelTitle"
    assert len(payload) == len(raw.encode())
    assert json.loads(payload.decode()) == params
    # 若按字符数报长度，这里必然短一截
    assert len(raw) != len(raw.encode())


def test_json_config_reports_truncation_instead_of_bad_json():
    """回包被缓冲区截断时报"截断"，不要让半截 JSON 变成看不懂解析错误"""
    class _Truncating(_FakeSDK):
        def GetNewDevConfig(self, lid, name, ch, buf, size, err, wait, res=None):
            # 刻意回一段没收尾的 JSON：花括号数对不上就是截断特征
            buf.value = b'{"params": {"table": {"x": "' + b"y" * 4096
            return 1

    c = _client(_Truncating())
    with pytest.raises(DahuaSdkError) as ei:
        c.json_config("VideoInOptions", 0)
    assert "截断" in str(ei.value)


# ------------------------------------------------------------------ 字段映射


def test_video_color_reads_enabled_time_section_row():
    """VideoColor 按 TimeSection 分时段，首列 1/0 才是启用位。
    按 table[0] 取纯靠行序凑巧：停用行排在前面时读到的是没人用的参数，
    点应用又把它写回生效行，画面色彩会被改掉。"""
    sdk = _FakeSDK(configs={"VideoColor": {"table": [
        {"Brightness": 10, "TimeSection": "0 00:00:00-24:00:00"},
        {"Brightness": 80, "TimeSection": "1 00:00:00-24:00:00"},
    ]}})
    assert _client(sdk).video_color()["Brightness"] == 80


def test_set_video_color_touches_only_active_row():
    sdk = _FakeSDK(configs={"VideoColor": {"table": [
        {"Brightness": 10, "TimeSection": "0 00:00:00-24:00:00"},
        {"Brightness": 80, "TimeSection": "1 00:00:00-24:00:00"},
    ]}})
    c = _client(sdk)
    got = c.set_video_color(brightness=66)
    assert got["Brightness"] == 66
    rows = sdk.configs["VideoColor"]["table"]
    assert rows[0]["Brightness"] == 10, "停用时段不该被动到"
    assert rows[1]["Brightness"] == 66


def test_video_options_exposes_gain_red_blue():
    """真机回包里有 GainRed / GainBlue：设计稿标的"未导出"其实是没读出来"""
    sdk = _FakeSDK(configs={"VideoInOptions": {"table": {
        "WhiteBalance": "Manual", "GainRed": 33, "GainBlue": 44,
        "DayNightColor": 1, "DayNightSensitivity": 2, "DayNightSwitchDelay": 6}}})
    got = _client(sdk).video_options()
    assert (got["gain_red"], got["gain_blue"]) == (33, 44)
    assert got["white_balance"] == "Manual"


def test_set_video_options_writes_named_fields_only():
    sdk = _FakeSDK(configs={"VideoInOptions": {"table": {
        "WhiteBalance": "Auto", "GainRed": 1, "GainBlue": 2,
        "DayNightColor": 1, "ColorTemperatureLevel": 100}}})
    c = _client(sdk)
    got = c.set_video_options(white_balance="Manual", gain_red=9)
    table = sdk.configs["VideoInOptions"]["table"]
    assert table["WhiteBalance"] == "Manual"
    assert table["GainRed"] == 9
    assert table["GainBlue"] == 2, "未点名的字段必须原样保留（RMW）"
    assert table["ColorTemperatureLevel"] == 100
    assert got["white_balance"] == "Manual"


def test_image_ctrl_returns_stable_capability_mask():
    """nStable 是防抖能力掩码：core 要把它回给页面，页面才知道该不该置灰"""
    m = dahua_sdk.module()
    st = m.NET_EM_CFG_VIDEO_IMAGE_CTRL_INFO()
    st.dwSize = m.sizeof(st)
    st.bMirror = st.bFlip = 1
    st.bHorizontalFlip = 1
    st.nStable = 2          # 光学防抖
    sdk = _FakeSDK()
    sdk.structs[dahua_sdk.DahuaClient._CFG_IMAGE_CTRL] = bytes(st)
    got = _client(sdk).image_ctrl()
    assert got["supports_mirror"] is True
    assert got["mirror"] is True
    assert got["stable"] == 2


# ------------------------------------------------------------------ 通道接线


def test_encode_video_passes_channel_to_getconfig():
    """通道以前写死 0：多通道设备上切通道下拉毫无作用，读回来永远是第 1 路"""
    m = dahua_sdk.module()
    st = m.NET_ENCODE_VIDEO_INFO()
    st.dwSize = m.sizeof(st)
    st.emFormatType = 1
    st.nWidth, st.nHeight, st.nFrameRate = 2560, 1440, 20.0
    sdk = _FakeSDK()
    sdk.structs[1100] = bytes(st)
    c = _client(sdk)
    got = c.encode_video(1, channel=3)
    assert sdk.cfg_gets[-1] == (1100, 3)
    assert (got["width"], got["framerate"]) == (2560, 20.0)


def test_set_encode_video_writes_and_reads_back():
    m = dahua_sdk.module()
    st = m.NET_ENCODE_VIDEO_INFO()
    st.dwSize = m.sizeof(st)
    st.emFormatType = 4
    st.nIFrameInterval = 50
    sdk = _FakeSDK()
    sdk.structs[1100] = bytes(st)
    c = _client(sdk)
    got = c.set_encode_video(4, channel=2, iframe_interval=25)
    assert got["iframe_interval"] == 25
    assert sdk.cfg_sets[-1][:2] == (1100, 2)


def test_set_encode_video_rejected_surfaces_kind():
    """辅码流改分辨率在 4238 上被固件直接拒掉（SDK err 21）：
    错误要带 rejected 归类，页面才知道要回读纠偏而不是重试"""
    m = dahua_sdk.module()
    st = m.NET_ENCODE_VIDEO_INFO()
    st.dwSize = m.sizeof(st)
    sdk = _FakeSDK(reject={1100})
    sdk.structs[1100] = bytes(st)
    c = _client(sdk)
    with pytest.raises(DahuaSdkError) as ei:
        c.set_encode_video(4, width=1280, height=720)
    assert ei.value.kind == "rejected"
    assert len(sdk.cfg_sets) == 1, "被拒后不该再重试一次"


def test_set_image_ctrl_no_land_returns_original_state():
    """固件"收了但不落库"：set_* 的返回值必须是读回来的原值，
    回显入参就等于把没生效的修改谎报成已生效"""
    m = dahua_sdk.module()
    st = m.NET_EM_CFG_VIDEO_IMAGE_CTRL_INFO()
    st.dwSize = m.sizeof(st)
    st.bHorizontalFlip = 0
    sdk = _FakeSDK(no_land={dahua_sdk.DahuaClient._CFG_IMAGE_CTRL})
    sdk.structs[dahua_sdk.DahuaClient._CFG_IMAGE_CTRL] = bytes(st)
    c = _client(sdk)
    got = c.set_image_ctrl(mirror=True)
    assert got["mirror"] is False
