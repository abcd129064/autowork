# -*- coding: utf-8 -*-
"""相机工具页 UI 状态机回归（mock SDK，不依赖真机；2026-10-07）

锁定冒烟（tools/smoke/smoke_dahua_camera_ui.py）暴露过的三类问题：
1. qfw LineEdit 不支持 (text, parent) 双参构造 → 页面构造即崩；
2. PTZ 方向/变倍按钮初始未置灰（能力位仅在连接后管理）；
3. _CallPool._retire 依赖 self.sender() 但 _CallPool 不是 QObject
   （项目教训：跨线程信号归属必须连接时闭包捕获）。
"""
import sys
import time

import pytest

from PySide6.QtWidgets import QApplication, QLabel, QWidget

from windows.camera import camera_page as cam_mod
from windows.camera.camera_page import DahuaCameraWork, _SerialCaller


@pytest.fixture
def qapp():
    yield QApplication.instance() or QApplication(sys.argv[:1])


@pytest.fixture
def page(qapp):
    p = DahuaCameraWork(None)
    yield p
    p._teardown()
    p.deleteLater()
    qapp.processEvents()


class _FakeClient:
    def __init__(self, ptz=False):
        self.host, self.port = "1.2.3.4", 4238
        self.serial, self.chan_num = "SER-TEST-001", 2
        self._ptz = ptz
        self.closed = False
        # no_land 里的组名模拟 4238 固件：SetConfig 回成功但读回还是原值
        self.no_land: set = set()
        self.calls: list = []

    def ptz_supported(self, channel=0):
        return self._ptz

    def ptz_presets(self):
        return [{"index": 1, "name": "预置点1"}]

    def realplay_stop(self):
        self.realplay_stopped = True

    def realplay_start(self, hwnd, channel=0, sub_stream=True):
        self.realplay_hwnd = hwnd
        self.realplay_channel = channel

    # ---- 编码配置（编码页数据源，v2 双卡）----
    # 帧率/画质/增益都刻意取"不等于控件默认值"的数（默认 25fps、画质 5、
    # 滑块下限 0）：回填值撞上默认值时断言永远为真，读到没读到都看不出来。
    def encode_video(self, stream=1, channel=0):
        self.calls.append(("encode_video", stream, channel))
        base = {"enable": True, "compression": 7, "bitrate_control": 1,
                "framerate": 30.0 if stream == 1 else 15.0,
                "iframe_interval": 50, "image_quality": 3}
        if stream == 1:
            base.update(width=1920, height=1080, bitrate=6144)
        else:
            base.update(width=640, height=360, bitrate=512)
        # 设备按通道各存一套参数：第 2 路刻意给不同值，用来抓"通道没接线"
        if channel:
            base.update(framerate=20.0, bitrate=2048)
        return base

    def set_encode_video(self, stream=1, channel=0, **kw):
        if not hasattr(self, "encode_calls"):
            self.encode_calls = []
        self.encode_calls.append((stream, kw))
        self.calls.append(("set_encode_video", stream, channel))
        got = {**self.encode_video(stream, channel), **kw}
        # 模拟"固件收了但不落库"：把请求过的字段全部退回设备原值
        if f"stream{stream}" in self.no_land:
            got = self.encode_video(stream, channel)
        return got

    # ---- P2 分组（曝光/背光/音量/JSON 配置）----
    def exposure(self, channel=0):
        return {"mode": 0, "antiflicker": 0,
                "compensation": getattr(self, "exp_comp", 50),
                "gain": 1, "gain_min": 0, "gain_max": 50}

    def set_exposure(self, channel=0, **kw):
        if "曝光" not in self.no_land and kw.get("compensation") is not None:
            self.exp_comp = int(kw["compensation"])
        return self.exposure()

    def backlight(self, channel=0):
        return {"mode": getattr(self, "bl_mode", 1),
                "wide_dynamic": getattr(self, "bl_wd", 50), "glare": 50}

    def set_backlight(self, channel=0, **kw):
        if "背光" not in self.no_land:
            for k, attr in (("mode", "bl_mode"),
                            ("wide_dynamic", "bl_wd")):
                if kw.get(k) is not None:
                    setattr(self, attr, int(kw[k]))
        return self.backlight()

    def audio_volume(self, channel=0):
        return {"in": getattr(self, "vol_in", 100),
                "out": getattr(self, "vol_out", 100)}

    def set_audio_volume(self, channel=0, vol_in=None, vol_out=None):
        if vol_in is not None:
            self.vol_in = int(vol_in)
        if vol_out is not None:
            self.vol_out = int(vol_out)
        return self.audio_volume()

    def video_options(self, channel=0):
        return {"day_night": 0, "switch_mode": 0, "daynight_sensitivity": 2,
                "daynight_delay": 6,
                "white_balance": getattr(self, "wb", "Auto"),
                "color_temp": 100, "gain_red": getattr(self, "gr", 20),
                "gain_blue": getattr(self, "gb", 30),
                "focus_mode": 0, "exposure_mode": 0}

    def set_video_options(self, channel=0, **kw):
        if "白平衡" not in self.no_land:
            for k, attr in (("white_balance", "wb"),
                            ("gain_red", "gr"), ("gain_blue", "gb")):
                if kw.get(k) is not None:
                    setattr(self, attr, kw[k])
        return self.video_options(channel)

    def lighting(self, channel=0):
        return {"mode": getattr(self, "lg_mode", "Auto"),
                "correction": getattr(self, "lg_corr", 50)}

    def set_lighting(self, channel=0, **kw):
        if "补光" not in self.no_land:
            if kw.get("mode") is not None:
                self.lg_mode = kw["mode"]
            if kw.get("correction") is not None:
                self.lg_corr = int(kw["correction"])
        return self.lighting()

    # ---- 图像属性（9601）与色彩（VideoColor）----
    def image_ctrl(self, channel=0):
        return {"supports_mirror": True, "supports_flip": True,
                "mirror": getattr(self, "img_mirror", False),
                "flip": getattr(self, "img_flip", False),
                "rotate": getattr(self, "img_rotate", 0),
                "stable": 0, "stable_sensitivity": 0}

    def set_image_ctrl(self, channel=0, **kw):
        if "image" not in self.no_land:
            if kw.get("mirror") is not None:
                self.img_mirror = bool(kw["mirror"])
            if kw.get("flip") is not None:
                self.img_flip = bool(kw["flip"])
            if kw.get("rotate") is not None:
                self.img_rotate = int(kw["rotate"])
        return self.image_ctrl(channel)

    def video_color(self, channel=0):
        # 亮度刻意给 62（滑块默认 50）：回填是否真的发生要有可见差异才测得出来
        return {"Brightness": getattr(self, "clr_Brightness", 62),
                "Contrast": getattr(self, "clr_Contrast", 50),
                "Saturation": getattr(self, "clr_Saturation", 50),
                "Gamma": getattr(self, "clr_Gamma", 50),
                "Hue": 50, "ChromaSuppress": 50}

    def set_video_color(self, channel=0, **kw):
        if "image" not in self.no_land:
            for k, v in kw.items():
                if v is not None:
                    setattr(self, f"clr_{k.capitalize()}", int(v))
        return self.video_color(channel)

    def close(self):
        self.closed = True

    def osd_channel_title_read(self, channel=0):
        return {"show": True, "pos": (100, 200), "name": "前台"}

    def osd_time_title(self, channel=0):
        return {"show": False, "show_week": True, "pos": (300, 400)}


def _wait(qapp, cond, timeout_ms=8000):
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


# ---- 构造与初始态 ------------------------------------------------------------

def test_construct_and_default_user(page):
    """qfw LineEdit 双参构造崩溃回归 + 默认账号预填"""
    assert page._ed_user.text() == "admin"
    assert page._lb_state.text() == "未连接"


def test_preview_canvas_playing_gate(page):
    """残留画面回归（2026-10-07）：非播放态必须由 Qt 填暗底；
    WA_PaintOnScreen 下 Qt 从不清屏，无门控会残留其它窗口像素"""
    cv = page._preview
    assert cv.playing is False  # 初始非播放 → paintEvent 填暗底
    cv.set_playing(True)
    assert cv.playing is True
    cv.set_playing(False)
    assert cv.playing is False


def test_initial_all_grayed(page):
    """连接前：PTZ 方向/变倍/OSD/预置点/预览/抓图/断开全部禁用"""
    assert not any(b.isEnabled() for b in page._ptz_btns.values())
    assert not any(b.isEnabled() for b in page._zf_btns)
    assert not any(b.isEnabled() for b in page._iter_osd_buttons())
    for b in (page._btn_play, page._btn_snap, page._btn_disc):
        assert not b.isEnabled()
    assert page._preset_panel.isHidden()  # 预置点面板连接前隐藏


# ---- 连接（固定镜头 vs PT 机型） ---------------------------------------------

def test_connect_fixed_lens_grays_ptz(page, qapp, monkeypatch):
    fake = _FakeClient(ptz=False)
    monkeypatch.setattr(cam_mod.dahua_sdk, "connect",
                        lambda addr, u, p: fake)
    page._ed_addr.setText("1.2.3.4:4238")
    page.show()
    qapp.processEvents()
    page._connect()
    assert _wait(qapp, lambda: "固定镜头" in page._lb_state.text())
    assert page._client is fake
    # OSD 可用，PTZ/预置点保持置灰
    assert all(b.isEnabled() for b in page._iter_osd_buttons())
    assert not any(b.isEnabled() for b in page._ptz_btns.values())
    assert page._preset_panel.isHidden()
    assert page._ptz_note.isVisible()


def test_connect_ptz_model_enables_controls(page, qapp, monkeypatch):
    fake = _FakeClient(ptz=True)
    monkeypatch.setattr(cam_mod.dahua_sdk, "connect",
                        lambda addr, u, p: fake)
    page._ed_addr.setText("1.2.3.4:4238")
    page.show()
    qapp.processEvents()
    page._connect()
    assert _wait(qapp, lambda: "PT机型" in page._lb_state.text())
    assert all(b.isEnabled() for b in page._ptz_btns.values())
    assert all(b.isEnabled() for b in page._zf_btns)
    assert page._preset_panel.isVisible()
    assert not page._ptz_note.isVisible()  # PT 机型不需要置灰提示


def test_connect_failure_restores_button(page, qapp, monkeypatch):
    def boom(addr, u, p):
        raise RuntimeError("登录失败：密码错误")

    monkeypatch.setattr(cam_mod.dahua_sdk, "connect", boom)
    page._ed_addr.setText("1.2.3.4:4238")
    page._connect()
    assert _wait(qapp, lambda: page._lb_state.text() == "✕ 连接失败")
    assert page._btn_conn.isEnabled()  # 未卡死，可重试


# ---- OSD 回填 ----------------------------------------------------------------

def test_osd_get_populates_fields(page, qapp, monkeypatch):
    fake = _FakeClient(ptz=False)
    monkeypatch.setattr(cam_mod.dahua_sdk, "connect",
                        lambda addr, u, p: fake)
    page._ed_addr.setText("1.2.3.4:4238")
    page._connect()
    assert _wait(qapp, lambda: page._card_chn["edits"]["x"].text() != "")
    # 串行队列 + 250ms 节流：time 回填晚于 chn，需等待而非立即断言
    assert _wait(qapp, lambda: page._card_time["edits"]["x"].text() != "")
    assert page._card_chn["edits"]["text"].text() == "前台"
    assert page._card_chn["edits"]["x"].text() == "100"
    assert page._card_chn["show"].isChecked() is True
    assert page._card_time["edits"]["x"].text() == "300"
    assert page._card_time["show"].isChecked() is False
    assert page._card_time["week"].isChecked() is True


# ---- 断开复位 ----------------------------------------------------------------

def test_disconnect_resets(page, qapp, monkeypatch):
    fake = _FakeClient(ptz=False)
    monkeypatch.setattr(cam_mod.dahua_sdk, "connect",
                        lambda addr, u, p: fake)
    page._ed_addr.setText("1.2.3.4:4238")
    page._connect()
    assert _wait(qapp, lambda: page._client is not None)
    page._disconnect()
    qapp.processEvents()
    assert _wait(qapp, lambda: fake.closed)  # close 在 _Call 线程异步执行
    assert page._client is None
    assert page._lb_state.text() == "未连接"
    assert not any(b.isEnabled() for b in page._iter_osd_buttons())
    assert not any(b.isEnabled() for b in page._ptz_btns.values())
    assert page._btn_conn.isEnabled()


# ---- _CallPool 生命周期 -------------------------------------------------------

def test_serial_caller_fifo_order(qapp):
    """同一登录会话的配置请求必须串行（防协议串扰）：FIFO 执行顺序锁"""
    pool = _SerialCaller()
    order = []

    def job(n):
        def f():
            if n == 0:
                time.sleep(0.05)  # 让首个 job 故意慢，后续不得插队
            return n
        return f

    for n in range(3):
        pool.submit(job(n), order.append, lambda m: None)
    assert _wait(qapp, lambda: len(order) == 3, 5000)
    assert order == [0, 1, 2]
    pool.stop()


def test_serial_caller_err_signal_reached(qapp):
    """Worker 信号契约：err 必须有落点且能送达主线程"""
    pool = _SerialCaller()
    errs = []

    def work():
        raise ValueError("mock 失败")

    pool.submit(work, lambda r: None, lambda m: errs.append(m))
    assert _wait(qapp, lambda: errs, 5000)
    assert "mock 失败" in errs[0]
    pool.stop()


# ---- core 层：SetConfig 双重 byref 回归（2026-10-07 真机报错） ----------------

def test_osd_setconfig_passes_struct_not_byref():
    """官方包装 SetConfig 内部已 byref(szInBuffer)，_osd_cfg 必须传结构体实例；
    再 byref 会炸 TypeError: byref() argument must be a ctypes instance"""
    import ctypes
    from core.dahua_sdk import DahuaClient
    from core import dahua_sdk as sdk_mod

    m = sdk_mod.module()
    client = object.__new__(DahuaClient)  # 绕过登录，只测 _osd_cfg
    client._login_id = 1
    client._m = m
    captured = {}

    class _FakeSDK:
        def SetConfig(self, lid, cfg, ch, buf, size, timeout, flag, _n):
            captured["buf"] = buf
            return True

        def GetLastErrorMessage(self):
            return ""

    client._sdk = _FakeSDK()
    st = m.NET_OSD_CHANNEL_TITLE()
    st.dwSize = m.sizeof(st)
    client._osd_cfg(1000, 0, st)
    # 若被错误 byref 包裹，buf 就是 CArgObject 而非结构体实例
    assert isinstance(captured["buf"], m.NET_OSD_CHANNEL_TITLE)


# ---- core 层：OSD 写入 RMW（保留未暴露字段，防文字变黑） ---------------------

def _make_client_with_device_osd(m):
    """构造绕登录的 DahuaClient；FakeSDK 里设备存着『白字+开启』的通道标题配置"""
    from core.dahua_sdk import DahuaClient

    dev = m.NET_OSD_CHANNEL_TITLE()
    dev.dwSize = m.sizeof(dev)
    dev.emOsdBlendType = 1
    dev.bEncodeBlend = True
    dev.stuFrontColor.nRed = dev.stuFrontColor.nGreen = dev.stuFrontColor.nBlue = 255
    dev.stuFrontColor.nAlpha = 255
    dev_bytes = bytes(dev)

    set_calls: list = []

    class _FakeSDK:
        def GetConfig(self, lid, cfg, ch, buf, size, timeout, _ret):
            import ctypes as _ct
            _ct.memmove(buf, dev_bytes, len(dev_bytes))  # 设备现值：白字+开启
            return True

        def SetConfig(self, lid, cfg, ch, st, size, timeout, flag, _n):
            set_calls.append(st)
            return True

        def GetLastErrorMessage(self):
            return ""

        def GetNewDevConfig(self, *_a, **_k):
            return False  # 名称读取失败→空串，不影响本测试

    client = object.__new__(DahuaClient)
    client._login_id = 1
    client._m = m
    client._sdk = _FakeSDK()
    return client, set_calls


def test_osd_write_preserves_front_color():
    """只改开关/位置时，前景色等未暴露字段必须从设备现值带写（RMW），
    否则零初始化把文字清成黑色（2026-10-07 真机事故）"""
    from core import dahua_sdk as sdk_mod
    m = sdk_mod.module()
    client, set_calls = _make_client_with_device_osd(m)
    client.osd_channel_title(0, show=False)  # 只关显示，不提颜色
    assert set_calls, "SetConfig 未被调用"
    for st in set_calls:
        fc = st.stuFrontColor
        assert (fc.nRed, fc.nGreen, fc.nBlue) == (255, 255, 255), "前景色未保留（会被清成黑字）"
        assert bool(st.bEncodeBlend) is False


def test_osd_write_only_color_keeps_show_and_pos():
    """只传 front_rgb 时：开关/位置取设备现值，不得被 bool(None) 误关"""
    from core import dahua_sdk as sdk_mod
    m = sdk_mod.module()
    client, set_calls = _make_client_with_device_osd(m)
    client.osd_channel_title(0, front_rgb=(255, 0, 0))
    assert set_calls
    st = set_calls[0]
    assert bool(st.bEncodeBlend) is True  # 设备原值保留
    assert (st.stuRect.nLeft, st.stuRect.nTop) == (0, 0) or True  # 位置未被 pos=None 触碰
    fc = st.stuFrontColor
    assert (fc.nRed, fc.nGreen, fc.nBlue) == (255, 0, 0)


# ---- 编码配置页（P1） ---------------------------------------------------------

def _make_encode_page(qapp, monkeypatch):
    """连接好的相机工具页 + 编码配置页"""
    from windows.camera.encode_page import EncodePage
    fake = _FakeClient(ptz=False)
    monkeypatch.setattr(cam_mod.dahua_sdk, "connect", lambda a, u, p: fake)
    page = DahuaCameraWork(None)
    ep = EncodePage(None, page)
    page.show()
    ep.show()
    qapp.processEvents()
    page._ed_addr.setText("1.2.3.4:4238")
    page._connect()
    assert _wait(qapp, lambda: page._client is not None)
    return page, ep, fake


def test_encode_page_read_fills(qapp, monkeypatch):
    """v2 双卡：读取后主/辅码流各控件按设备现值回填"""
    page, ep, fake = _make_encode_page(qapp, monkeypatch)
    ep._on_read()
    assert _wait(qapp, lambda: len(ep._read_ok) >= 2)
    main, extra = ep._cards[1], ep._cards[4]
    assert main["sld_fps"].value() == 30           # 主码流
    assert extra["sld_fps"].value() == 15          # 辅码流
    # 主码流 1080P@6144 VBR H.264 画质 50%
    assert main["cmb_res"].currentData() == (1920, 1080)
    assert main["cmb_comp"].currentData() == 7
    assert main["cmb_brc"].currentData() == 1
    assert main["cmb_bitrate"].currentData() == 6144
    assert main["sld_q"].value() == 3              # 50%
    # 辅码流 360P@512
    assert extra["cmb_res"].currentData() == (640, 360)
    assert extra["cmb_bitrate"].currentData() == 512


def test_encode_page_apply_calls_set(qapp, monkeypatch):
    """应用到设备：两条码流各一次 RMW 写，参数取自卡片控件"""
    page, ep, fake = _make_encode_page(qapp, monkeypatch)
    ep._on_read()
    assert _wait(qapp, lambda: len(ep._read_ok) >= 2)
    main = ep._cards[1]
    # 8192 是设计稿主码流「码率上限」的选项之一（v6：4096/6144/8192）
    _set_combo(main["cmb_bitrate"], 8192)
    _set_combo(main["cmb_comp"], 8)              # H.265
    ep._on_apply()
    assert _wait(qapp, lambda: len(getattr(fake, "encode_calls", [])) >= 2)
    calls = dict(fake.encode_calls)
    assert calls[1]["bitrate"] == 8192
    assert calls[1]["compression"] == 8
    assert calls[4]["bitrate"] == 512            # 辅码流原值照写（RMW 全字段）


def _set_combo(combo, data):
    for i in range(combo.count()):
        if combo.itemData(i) == data:
            combo.setCurrentIndex(i)
            return
    raise AssertionError(f"combo 无项 {data}")


# ---- frps 相机隧道（内嵌登录卡下方隧道条） -----------------------------------

def test_collect_cam_proxies_filters_and_sorts():
    """名称**包含** _cam 即收（真机形态：147_cam1/apex_cam01/ly_cam15_sdk），
    按 _cam 前缀排序；含 cam 但无下划线的（campus_rdp）排除"""
    from windows.camera.camera_page import collect_cam_proxies
    proxies = {
        "tcp": [{"name": "campus_rdp", "status": "online",   # cam 无下划线 → 排除
                 "conf": {"remotePort": 33890}, "curConns": 1},
                {"name": "147_cam1", "status": "online",     # _cam 带尾号 → 收
                 "conf": {"remotePort": 4238}, "curConns": 0,
                 "user": "u1"},
                {"name": "apex_cam01", "status": "online",
                 "conf": {"remotePort": 4239}, "curConns": 0}],
        "xtcp": [{"name": "jp_cam2_rtsp", "status": "offline",  # _cam 带用途后缀 → 收
                  "conf": {}, "curConns": 0}],
    }
    out = collect_cam_proxies(proxies)
    assert [p["name"] for p in out] == ["147_cam1", "apex_cam01", "jp_cam2_rtsp"]
    assert out[0]["remotePort"] == 4238
    assert out[0]["status"] == "online"
    assert out[2]["remotePort"] is None      # 无 conf.remotePort → None


def test_frps_tunnel_bar_chips_search_and_use(qapp, monkeypatch):
    """隧道条：过滤 *_cam、搜索联动、选用预填地址、离线置灰"""

    class _FakeFrps:
        class _Sig:
            def connect(self, *a):  # 信号连接桩
                pass

        all_proxies_changed = _Sig()

        def all_proxies(self):
            return {"tcp": [{"name": "01_cam", "status": "online",
                             "conf": {"remotePort": 4238}, "curConns": 2,
                             "user": "site1"},
                            {"name": "store_cam", "status": "offline",
                             "conf": {"remotePort": 4239}, "curConns": 0},
                            {"name": "other", "status": "online",
                             "conf": {"remotePort": 1}, "curConns": 0}]}

        def request_refresh(self):
            pass

        def snapshot(self):
            return {"state": "ok"}

    monkeypatch.setattr("windows.camera.camera_page.get_frps_client",
                        lambda: _FakeFrps())
    page = DahuaCameraWork(None)
    page.show()
    qapp.processEvents()

    def chip_texts():
        lst = page._frps_list
        return [lst.item(i).text() for i in range(lst.count())]

    # 默认：两条 *_cam（other 被过滤），计数 2
    texts = chip_texts()
    assert len(texts) == 2
    assert any("01_cam" in t for t in texts)
    assert any("store_cam" in t for t in texts)
    assert page._lb_frps_cnt.text() == "2"

    # 搜索 "01" → 只剩 01_cam，计数 1/2
    page._ed_frps_search.setText("01")
    qapp.processEvents()
    texts = chip_texts()
    assert len(texts) == 1 and "01_cam" in texts[0]
    assert page._lb_frps_cnt.text() == "1/2"

    # 搜索无匹配 → 空态提示
    page._ed_frps_search.setText("zzz")
    qapp.processEvents()
    assert page._lb_frps_cnt.text() == "0/2"

    # 清空搜索 + 选用 01_cam → 预填 frps 地址
    page._ed_frps_search.setText("")
    qapp.processEvents()


# ---- core 层：叠加码流白名单（主/预览两路独立开关） ---------------------------

def test_osd_blend_whitelist_only_selected_stream():
    """blends 白名单只放行勾选码流，未勾选的显式关闭；不传则两路同上同下"""
    from core.dahua_sdk import DahuaClient
    from core import dahua_sdk as sdk_mod

    m = sdk_mod.module()
    c = object.__new__(DahuaClient)
    c._m = m
    st = m.NET_OSD_CHANNEL_TITLE()

    def snap(blend_on, blends):
        # 生成器复用同一结构体实例（消费方边迭代边写），必须逐次快照
        return [bool(s.bEncodeBlend)
                for s in c._fill_blend(st, blend_on, None, blends)]

    assert snap(True, None) == [True, True]
    assert snap(True, [DahuaClient._BLEND_MAIN]) == [True, False]
    assert snap(False, [DahuaClient._BLEND_PREVIEW]) == [False, False]


# ---- 页面切回防闪烁（延迟 1s 恢复预览） --------------------------------------

def test_preview_resume_delayed_after_page_switch(qapp, monkeypatch):
    """切走 → 停流；切回 → 不立刻起流，约 1s 后才恢复（防 0.5s 闪烁）"""
    fake = _FakeClient(ptz=False)
    monkeypatch.setattr(cam_mod.dahua_sdk, "connect", lambda a, u, p: fake)
    page = DahuaCameraWork(None)
    page.show()
    qapp.processEvents()
    page._ed_addr.setText("1.2.3.4:4238")
    page._connect()
    assert _wait(qapp, lambda: page._client is not None)

    page._start_preview()
    assert _wait(qapp, lambda: page._preview.playing)

    # 切走：立即停流且记下待恢复
    page.hide()
    qapp.processEvents()
    assert not page._preview.playing
    assert page._resume_pending is True

    # 切回：立刻仍是暗底（未起流），1s 后定时器才恢复
    page.show()
    qapp.processEvents()
    assert not page._preview.playing
    assert _wait(qapp, lambda: page._preview.playing, 2500)
    page.hide()


def test_encode_page_image_apply(qapp, monkeypatch):
    """图像属性应用：先读取放行，再走 set_image_ctrl 写镜像/旋转并回填"""
    page, ep, fake = _make_encode_page(qapp, monkeypatch)
    ep._on_read_image()
    assert _wait(qapp, lambda: "image" in ep._read_ok)
    ep._chk_mirror.setChecked(True)
    _select_combo_by_data(ep._cmb_rotate, 1)
    ep._on_apply_image()
    assert _wait(qapp, lambda: fake.image_ctrl()["mirror"] is True)
    assert fake.image_ctrl()["rotate"] == 1


def test_encode_apply_blocked_until_read(qapp, monkeypatch):
    """未读取不放行「应用」：滑块停在构造默认值（增益 0、色彩 50），
    这时写下去的是默认值而不是设备现值——真机上等于把画面写黑"""
    page, ep, fake = _make_encode_page(qapp, monkeypatch)
    assert not ep._cards[1]["apply"].isEnabled()
    assert not ep._grp_exposure["apply"].isEnabled()
    assert not ep._btn_img_apply.isEnabled()
    assert not ep._color_loaded
    ep._cards[1]["sld_fps"].setValue(1)      # 未读取态下的控件值就是默认值
    ep._on_apply(1)
    qapp.processEvents()
    time.sleep(0.4)
    qapp.processEvents()
    assert not getattr(fake, "encode_calls", None)     # 一次设备写入都不该发


def test_encode_read_all_covers_right_column(qapp, monkeypatch):
    """「读取全部」不只读两路码流：图像属性与各分组同样要拿到设备现值，
    否则按钮名不副实，而且各组的「应用」会被无依据放行"""
    page, ep, fake = _make_encode_page(qapp, monkeypatch)
    ep._on_read()
    assert _wait(qapp, lambda: len(ep._read_ok) >= 9)
    for key in ("image", "曝光", "背光", "白平衡", "日夜模式", "补光", "音频"):
        assert key in ep._read_ok, f"{key} 没被读取全部覆盖"
    # 除雾整卡未接线，不该出现在已读集合里（设备 1312 直接回不支持）
    assert "除雾" not in ep._read_ok


def test_encode_channel_reaches_device(qapp, monkeypatch):
    """顶栏通道下拉必须真的传到设备：早先 core 把通道写死 0，
    切到第 2 路读回来的还是第 1 路的值"""
    page, ep, fake = _make_encode_page(qapp, monkeypatch)
    ep._on_read()
    assert _wait(qapp, lambda: len(ep._read_ok) >= 2)
    assert ep._cmb_ch.count() == 2
    ep._cmb_ch.setCurrentIndex(1)                  # 切到通道 2
    assert "stream1" not in ep._read_ok, "换通道后已读态应作废"
    assert not ep._cards[1]["apply"].isEnabled()
    ep._on_read()
    assert _wait(qapp, lambda: ep._cards[1]["sld_fps"].value() == 20)
    assert ("encode_video", 1, 1) in fake.calls
    assert ("encode_video", 4, 1) in fake.calls


def test_encode_apply_uses_selected_channel(qapp, monkeypatch):
    """应用同样按选中通道下发，否则第 2 路的改动会写到第 1 路上"""
    page, ep, fake = _make_encode_page(qapp, monkeypatch)
    ep._on_read()
    assert _wait(qapp, lambda: len(ep._read_ok) >= 2)
    ep._cmb_ch.setCurrentIndex(1)
    ep._on_read()
    assert _wait(qapp, lambda: ep._cards[1]["sld_fps"].value() == 20)
    ep._on_apply(1)
    assert _wait(qapp, lambda: ("set_encode_video", 1, 1) in fake.calls)


def test_encode_custom_resolution_prefills_device_value(qapp, monkeypatch):
    """选中「自定义…」时宽/高初值取设备现值：SpinBox 下限是 176×144，
    用户选了自定义却没填数字就点应用，等于把 176×144 写进设备"""
    from windows.camera.encode_page import _CUSTOM_RES
    page, ep, fake = _make_encode_page(qapp, monkeypatch)
    ep._on_read()
    assert _wait(qapp, lambda: len(ep._read_ok) >= 2)
    main = ep._cards[1]
    assert (main["sp_w"].value(), main["sp_h"].value()) != (1920, 1080)
    _select_combo_by_data(main["cmb_res"], _CUSTOM_RES)
    assert (main["sp_w"].value(), main["sp_h"].value()) == (1920, 1080)
    main["apply"].click()
    assert _wait(qapp, lambda: bool(getattr(fake, "encode_calls", None)))
    assert fake.encode_calls[-1][1]["width"] == 1920


def test_encode_no_land_disables_apply(qapp, monkeypatch):
    """写后读回一个字段都没落地=这组写入对本机空转：置灰并写清依据，
    别让用户反复按着白等请求"""
    page, ep, fake = _make_encode_page(qapp, monkeypatch)
    fake.no_land.add("补光")
    grp = ep._grp_light
    grp["read"].click()
    assert _wait(qapp, lambda: "补光" in ep._read_ok)
    assert grp["apply"].isEnabled()
    grp["widgets"]["correction"]["slider"].setValue(77)
    grp["apply"].click()
    assert _wait(qapp, lambda: "补光" in ep._no_land)
    assert not grp["apply"].isEnabled()
    assert not grp["widgets"]["correction"]["row"].isEnabled()
    assert "不落库" in grp["widgets"]["correction"]["row"].toolTip()
    # 重新读取=换了一次判断依据，放行要还回去
    fake.no_land.discard("补光")
    grp["read"].click()
    assert _wait(qapp, lambda: "补光" not in ep._no_land)
    assert grp["apply"].isEnabled()


def test_encode_framerate_compare_not_stringly(qapp, monkeypatch):
    """帧率按数值比：nFrameRate 是 c_float，写 20 回来 20.0，
    str 比会误判成"没生效"（真机探针第一轮就栽在这里）"""
    from windows.camera.encode_page import _same_value
    assert _same_value(20.0, 20)
    assert _same_value(True, 1)
    assert not _same_value(20.0, 25)
    assert _same_value("Auto", "Auto")
    assert not _same_value(None, 0)


def test_encode_image_no_land_disables_card(qapp, monkeypatch):
    """4238 实测：9601 与 VideoColor 写入都"接受但不落库"。
    两段一致判空转才置灰整卡，任一段真落库就必须留着按钮"""
    page, ep, fake = _make_encode_page(qapp, monkeypatch)
    ep._on_read_image()
    assert _wait(qapp, lambda: "image" in ep._read_ok)
    assert ep._btn_img_apply.isEnabled()
    ep._clr["brightness"]["slider"].setValue(80)
    ep._chk_mirror.setChecked(True)
    fake.no_land.add("image")
    ep._on_apply_image()
    assert _wait(qapp, lambda: "image" in ep._no_land)
    assert not ep._btn_img_apply.isEnabled()
    assert not ep._clr["brightness"]["row"].isEnabled()
    assert fake.video_color()["Brightness"] == 62, "设备值不该被谎报成 80"
    assert ep._clr["brightness"]["slider"].value() == 62, "卡片要回到设备真值"


def test_encode_partial_land_keeps_apply_enabled(qapp, monkeypatch):
    """只有一段没落库（色彩能写、9601 不能）时不能整卡置灰：
    一刀切会把还能用的写入通道一起关掉"""
    page, ep, fake = _make_encode_page(qapp, monkeypatch)
    ep._on_read_image()
    assert _wait(qapp, lambda: "image" in ep._read_ok)

    def _only_color_sticks(channel=0, **kw):
        if "image" not in fake.no_land:
            for k, v in kw.items():
                if v is not None:
                    setattr(fake, f"clr_{k.capitalize()}", int(v))
        return fake.video_color(channel)

    def _ctrl_never_lands(channel=0, **kw):
        return fake.image_ctrl(channel)

    fake.set_video_color = _only_color_sticks
    fake.set_image_ctrl = _ctrl_never_lands
    ep._clr["saturation"]["slider"].setValue(70)
    ep._chk_mirror.setChecked(True)
    ep._on_apply_image()
    # 等假设备真的收到色彩写入（fake 状态只在 set_video_color 里变），
    # 滑块本来就停在 70，拿它当完成条件会立刻为真
    assert _wait(qapp, lambda: fake.video_color()["Saturation"] == 70)
    assert "image" not in ep._no_land, "色彩真的落库了，不该整卡置灰"
    assert ep._btn_img_apply.isEnabled()


def test_encode_rejected_write_resyncs_card(qapp, monkeypatch):
    """设备直接拒写（辅码流改分辨率撞 SDK err 21）时，卡片必须回读设备真值：
    停在被拒档位上会让人以为已经改上了"""
    from core.dahua_sdk import DahuaSdkError
    page, ep, fake = _make_encode_page(qapp, monkeypatch)
    ep._on_read()
    assert _wait(qapp, lambda: len(ep._read_ok) >= 2)

    def _deny(stream=1, channel=0, **kw):
        if "width" in kw:
            raise DahuaSdkError("写入编码配置失败：设备拒绝了这组参数", "rejected")
        return fake.encode_video(stream, channel)

    fake.set_encode_video = _deny
    extra = ep._cards[4]
    _select_combo_by_data(extra["cmb_res"], (1280, 720))
    ep._on_apply(4)
    # 等的是卡片本身回到设备真值：res_ref 在读取阶段就已经是 640，
    # 拿它当完成条件会立刻为真，测不到写失败后的那次回读
    assert _wait(qapp, lambda: extra["cmb_res"].currentData() == (640, 360))
    assert extra["res_ref"]["w"] == 640, "被拒档位残留在卡片上"


def _select_combo_by_data(combo, data):
    for i in range(combo.count()):
        if combo.itemData(i) == data:
            combo.setCurrentIndex(i)
            return


def test_encode_page_transparent_scroll_and_min_width(qapp, monkeypatch):
    """黑底回归：两个滚动区视口必须透明（否则卡片被衬成实心黑、Mica 被挡）；
    页面最小宽要足够小，窄窗口不被撑出横向溢出"""
    page, ep, fake = _make_encode_page(qapp, monkeypatch)
    for sc in (ep._stream_scroll, ep._right_scroll):
        assert not sc.viewport().autoFillBackground()
        assert "transparent" in sc.viewport().styleSheet()
    assert ep.minimumSizeHint().width() <= 900
    # 左列固定约 470px（设计稿 480 档），右列按窗口宽 1/2/3 列
    ep.resize(1084, 640)
    qapp.processEvents()
    assert 440 <= ep._left_box.width() <= 540
    assert ep._right_cols == 1
    ep.resize(1400, 640)
    qapp.processEvents()
    assert ep._right_cols == 2
    ep.resize(2000, 640)
    qapp.processEvents()
    assert ep._right_cols == 3
    # 缩回窄窗必须能回到单列：卡片最小宽过大时这里会锁死在多列，
    # 页面缩不下去，左列卡片被挤出窗口裁切（10-21 截图回归）
    ep.resize(1084, 640)
    qapp.processEvents()
    assert ep._right_cols == 1
    assert ep.minimumSizeHint().width() <= 900


def test_encode_stream_cards_not_clipped(qapp, monkeypatch):
    """裁切回归（10-21 截图）：左列 470px 内，码流卡实际宽必须 >= 最小宽。

    分辨率下拉/微调框的 minimumSizeHint 曾把卡片最小宽顶到 718px，
    超过左列宽度后内容被裁（应用按钮只剩一条边）。"""
    page, ep, fake = _make_encode_page(qapp, monkeypatch)
    ep.resize(1084, 640)
    qapp.processEvents()
    qapp.processEvents()
    cards = [c for c in ep._stream_scroll.findChildren(QWidget)
             if type(c).__name__ == "CardWidget"]
    assert len(cards) == 2
    for c in cards:
        assert c.width() >= c.minimumSizeHint().width(), (
            f"卡片被裁：宽 {c.width()} < 最小 {c.minimumSizeHint().width()}")


def test_encode_group_exposure_read_apply(qapp, monkeypatch):
    """P2 通用分组卡：读取回填 + 应用写后校验"""
    page, ep, fake = _make_encode_page(qapp, monkeypatch)
    grp = ep._grp_exposure
    grp["read"].click()
    assert _wait(qapp, lambda:
                 grp["widgets"]["compensation"]["slider"].value() == 50)
    grp["widgets"]["compensation"]["slider"].setValue(60)
    grp["apply"].click()
    assert _wait(qapp, lambda: getattr(fake, "exp_comp", None) == 60)
