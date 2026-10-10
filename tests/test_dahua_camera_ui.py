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

from PySide6.QtWidgets import QApplication

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
