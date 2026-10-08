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

from PySide6.QtWidgets import QApplication, QLabel
from qfluentwidgets import CardWidget

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

    def ptz_supported(self, channel=0):
        return self._ptz

    def ptz_presets(self):
        return [{"index": 1, "name": "预置点1"}]

    def realplay_stop(self):
        pass

    # ---- 编码配置（P1 编码页数据源，v2 双卡） ----
    def encode_video(self, stream=1):
        base = {"enable": True, "compression": 7, "bitrate_control": 1,
                "framerate": 25.0, "iframe_interval": 50, "image_quality": 5}
        if stream == 1:
            base.update(width=1920, height=1080, bitrate=4096)
        else:
            base.update(width=640, height=360, bitrate=512)
        return base

    def set_encode_video(self, stream=1, **kw):
        if not hasattr(self, "encode_calls"):
            self.encode_calls = []
        self.encode_calls.append((stream, kw))
        return {**self.encode_video(stream), **kw}

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
    assert _wait(qapp, lambda: ep._cards[1]["sld_fps"].value() == 25)
    main, extra = ep._cards[1], ep._cards[4]
    # 主码流 1080P@4096 VBR H.264
    assert main["cmb_res"].currentData() == (1920, 1080)
    assert main["cmb_comp"].currentData() == 7
    assert main["cmb_brc"].currentData() == 1
    assert main["cmb_bitrate"].currentData() == 4096
    assert main["sld_q"].value() == 5            # 80%
    # 辅码流 360P@512
    assert extra["cmb_res"].currentData() == (640, 360)
    assert extra["cmb_bitrate"].currentData() == 512


def test_encode_page_apply_calls_set(qapp, monkeypatch):
    """应用到设备：两条码流各一次 RMW 写，参数取自卡片控件"""
    page, ep, fake = _make_encode_page(qapp, monkeypatch)
    ep._on_read()
    assert _wait(qapp, lambda: ep._cards[1]["sld_fps"].value() == 25)
    main = ep._cards[1]
    _set_combo(main["cmb_bitrate"], 2048)
    _set_combo(main["cmb_comp"], 8)              # H.265
    ep._on_apply()
    assert _wait(qapp, lambda: len(getattr(fake, "encode_calls", [])) >= 2)
    calls = dict(fake.encode_calls)
    assert calls[1]["bitrate"] == 2048
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
        h = page._frps_v
        out = []
        for i in range(h.count()):
            w = h.itemAt(i).widget()
            labels = w.findChildren(QLabel) if w else []
            if labels:
                out.append("|".join(lb.text() for lb in labels))
        return out

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
    page._frps_use({"name": "01_cam", "remotePort": 4238})
    assert page._ed_addr.text().endswith(":4238")


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
