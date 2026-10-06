# -*- coding: utf-8 -*-
"""工具页 ·「相机工具」子页：大华相机登录 / 预览 / 云台 / OSD 叠加

设计稿：design/dahua_camera_tool.html v1；可行性证据：
docs/大华相机工具集成调研2026-10-06.md §5.0。

架构约定：
- SDK 交互全部经 core/dahua_sdk.py（Qt-free 单例封装）；
- 所有阻塞调用（登录/云台/OSD 读写/抓图）走 _Call 线程，ok/err 双落点
  （项目 Worker 信号契约：result 与 error 必须都有连接，09-30 教训）；
- SDK 回调不直触控件：预览由 SDK 内部渲染到 HWND，本页零数据回调。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QApplication, QGridLayout, QHBoxLayout,
                               QVBoxLayout, QWidget)
from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, CheckBox,
                            FluentIcon, LineEdit, PasswordLineEdit,
                            PrimaryPushButton, PushButton, SpinBox)

from core import dahua_sdk
from core.dahua_sdk import DahuaClient
from core.utils import show_info_bar

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _toast(widget: QWidget, msg: str, error: bool = False, duration: int = 2500):
    show_info_bar(msg, message_type="error" if error else "success",
                  duration=duration, parent=widget)


# ---------------------------------------------------------------- 线程助手


class _Call(QThread):
    """阻塞 SDK 调用线程：ok/err 双信号（都需连接，契约要求）"""
    ok = Signal(object)
    err = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):  # noqa: D102
        try:
            self.ok.emit(self._fn())
        except Exception as e:  # noqa: BLE001 统一转 UI 错误提示
            logger.warning("相机调用失败: %s", e, exc_info=True)
            self.err.emit(str(e))


class _CallPool:
    """_Call 生命周期管理：持引用防 GC，页面销毁时等待收尾"""

    def __init__(self, parent_widget):
        self._parent = parent_widget
        self._alive: list[_Call] = []

    def run(self, fn, on_ok, on_err):
        call = _Call(fn, self._parent)
        self._alive.append(call)
        call.ok.connect(on_ok)
        call.err.connect(on_err)
        call.finished.connect(lambda c=call: self._retire(c))
        call.start()

    def _retire(self, call=None):
        if call is None:  # 兼容：非闭包连接时兜底扫描
            for c in [c for c in self._alive if c.isFinished()]:
                self._alive.remove(c)
            return
        if call in self._alive:
            self._alive.remove(call)

    def wait_all(self, ms: int = 3000) -> None:
        for call in list(self._alive):
            call.wait(ms)


# ---------------------------------------------------------------- 页面


class _PreviewCanvas(QWidget):
    """SDK 直渲 HWND 的画布：Qt 侧零绘制，防「重绘盖帧」闪烁。

    根因：若预览区是普通 QWidget/QLabel（哪怕带 WA_NativeWindow），
    任何 Qt 重绘（InfoBar、状态标签、resize、主题刷新）都会先用背景色
    填掉 SDK 画好的帧，下一帧视频再盖回来 → 时不时闪一下。
    - WA_PaintOnScreen：绕过 Qt backing store，不再往该 HWND 写任何像素；
    - paintEvent 置空：重绘事件什么都不画；
    - WA_NoSystemBackground / WA_OpaquePaintEvent：不发擦除、不做透明合成。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_PaintOnScreen, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    def paintEvent(self, event):  # noqa: N802 故意空实现，绘制权全在 SDK
        pass



_DIR_KEYS = [  # 九宫格 → SDK_PTZ_ControlType 命令名
    ("LEFTTOP", "↖"), ("UP_CONTROL", "↑"), ("RIGHTTOP", "↗"),
    ("LEFT_CONTROL", "←"), ("", "·"), ("RIGHT_CONTROL", "→"),
    ("LEFTDOWN", "↙"), ("DOWN_CONTROL", "↓"), ("RIGHTDOWN", "↘"),
]


class DahuaCameraWork(QWidget):
    """相机工具：登录 → 预览 → 云台（能力位自适应） → OSD 叠加"""

    def __init__(self, win=None, parent=None):
        super().__init__(parent)
        self._win = win
        self._client: DahuaClient | None = None
        self._pool = _CallPool(self)
        self.setObjectName("dahuaCameraWork")

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(10)

        # ---- 登录条 ----
        login_card = CardWidget(self)
        lv = QHBoxLayout(login_card)
        lv.setContentsMargins(14, 10, 14, 10)
        self._ed_addr = LineEdit(login_card)
        self._ed_addr.setPlaceholderText("设备地址，支持隧道 host:port（如 49.235.34.253:4238）")
        self._ed_addr.setFixedWidth(280)
        self._ed_user = LineEdit(login_card)
        self._ed_user.setText("admin")
        self._ed_user.setFixedWidth(90)
        self._ed_pwd = PasswordLineEdit(login_card)
        self._ed_pwd.setFixedWidth(120)
        self._btn_conn = PrimaryPushButton(FluentIcon.LINK, "连接", login_card)
        self._btn_disc = PushButton(FluentIcon.CLOSE, "断开", login_card)
        self._btn_disc.setEnabled(False)
        self._lb_state = BodyLabel("未连接", login_card)
        for w in (self._ed_addr, self._ed_user, self._ed_pwd,
                  self._btn_conn, self._btn_disc, self._lb_state):
            lv.addWidget(w)
        lv.addStretch(1)
        root.addWidget(login_card)

        # ---- 中区：预览 + 云台 ----
        mid = QHBoxLayout()
        mid.setSpacing(10)

        preview_card = CardWidget(self)
        pv = QVBoxLayout(preview_card)
        pv.setContentsMargins(12, 10, 12, 12)
        self._preview = _PreviewCanvas(preview_card)
        self._preview.setMinimumSize(560, 315)
        pv.addWidget(self._preview, 1)
        self._lb_pv_hint = CaptionLabel(
            "连接后点击「开始预览」（SDK 直接渲染辅码流到上方画布）", preview_card)
        pv.addWidget(self._lb_pv_hint)
        pbar = QHBoxLayout()
        self._btn_play = PrimaryPushButton(FluentIcon.VIDEO, "开始预览", preview_card)
        self._btn_play.setEnabled(False)
        self._btn_stop_play = PushButton(FluentIcon.PAUSE, "停止", preview_card)
        self._btn_stop_play.setEnabled(False)
        self._btn_snap = PushButton(FluentIcon.CAMERA, "抓图存档", preview_card)
        self._btn_snap.setEnabled(False)
        self._lb_snap = CaptionLabel("", preview_card)
        for w in (self._btn_play, self._btn_stop_play, self._btn_snap):
            pbar.addWidget(w)
        pbar.addStretch(1)
        pbar.addWidget(self._lb_snap)
        pv.addLayout(pbar)
        mid.addWidget(preview_card, 5)

        ptz_card = CardWidget(self)
        ptz = QVBoxLayout(ptz_card)
        ptz.setContentsMargins(12, 10, 12, 12)
        ptz_h = QHBoxLayout()
        ptz_h.addWidget(BodyLabel("速度", ptz_card))
        self._sp_speed = SpinBox(ptz_card)
        self._sp_speed.setRange(1, 8)
        self._sp_speed.setValue(4)
        ptz_h.addWidget(self._sp_speed)
        ptz_h.addStretch(1)
        ptz.addLayout(ptz_h)

        grid_w = QWidget(ptz_card)
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        self._ptz_btns: dict[str, PushButton] = {}
        for i, (cmd, label) in enumerate(_DIR_KEYS):
            btn = PushButton(label, grid_w)
            btn.setFixedSize(52, 34)
            if cmd:
                btn.pressed.connect(lambda c=cmd: self._ptz_cmd(c, stop=False))
                btn.released.connect(lambda c=cmd: self._ptz_cmd(c, stop=True))
                self._ptz_btns[cmd] = btn
            else:
                btn.setEnabled(False)
            grid.addWidget(btn, i // 3, i % 3)
        ptz.addWidget(grid_w, 0, Qt.AlignCenter)

        zf = QGridLayout()
        zf.setSpacing(6)
        self._zf_btns: list[PushButton] = []
        for col, (title, add, dec) in enumerate((
                ("变倍", "ZOOM_ADD_CONTROL", "ZOOM_DEC_CONTROL"),
                ("聚焦", "FOCUS_ADD_CONTROL", "FOCUS_DEC_CONTROL"),
                ("光圈", "APERTURE_ADD_CONTROL", "APERTURE_DEC_CONTROL"))):
            zf.addWidget(BodyLabel(title, grid_w), 0, col, Qt.AlignCenter)
            b_add = PushButton("＋", grid_w)
            b_dec = PushButton("－", grid_w)
            for b, c in ((b_add, add), (b_dec, dec)):
                b.setFixedWidth(44)
                b.pressed.connect(lambda c=c: self._ptz_cmd(c, stop=False))
                b.released.connect(lambda c=c: self._ptz_cmd(c, stop=True))
                self._zf_btns.append(b)
            zf.addWidget(b_add, 1, col)
            zf.addWidget(b_dec, 2, col)
        ptz.addLayout(zf)

        self._ptz_note = CaptionLabel(
            "⚠ 方向/预置点仅 PT 机型有效；固定镜头机型此区自动置灰。", ptz_card)
        self._ptz_note.setWordWrap(True)
        ptz.addWidget(self._ptz_note)

        preset_row = QHBoxLayout()
        self._sp_preset = SpinBox(ptz_card)
        self._sp_preset.setRange(1, 255)
        self._btn_preset_goto = PushButton("调用预置点", ptz_card)
        self._btn_preset_set = PushButton("设为此处", ptz_card)
        self._btn_preset_del = PushButton("删除", ptz_card)
        for b in (self._btn_preset_goto, self._btn_preset_set, self._btn_preset_del):
            b.setEnabled(False)
        self._btn_preset_goto.clicked.connect(lambda: self._preset("goto"))
        self._btn_preset_set.clicked.connect(lambda: self._preset("set"))
        self._btn_preset_del.clicked.connect(lambda: self._preset("del"))
        for w in (self._sp_preset, self._btn_preset_goto, self._btn_preset_set,
                  self._btn_preset_del):
            preset_row.addWidget(w)
        ptz.addLayout(preset_row)
        mid.addWidget(ptz_card, 3)
        root.addLayout(mid, 1)

        # ---- OSD 卡 ----
        osd_row = QHBoxLayout()
        osd_row.setSpacing(10)
        self._card_chn = self._build_osd_card(
            osd_row, "chn", "通道标题", text_hint="叠加文字（相机名称）", has_pos=True)
        self._card_time = self._build_osd_card(
            osd_row, "time", "时间标题", text_hint=None, has_pos=True, has_week=True)
        self._card_custom = self._build_osd_card(
            osd_row, "custom", "自定义文字告示",
            text_hint="告示文字（如：⚠ 8号桌维修中）", has_pos=True)
        root.addLayout(osd_row)

        self._btn_conn.clicked.connect(self._connect)
        self._btn_disc.clicked.connect(self._disconnect)
        self._btn_play.clicked.connect(self._start_preview)
        self._btn_stop_play.clicked.connect(self._stop_preview)
        self._btn_snap.clicked.connect(self._snap)
        for btn in self._iter_osd_buttons():
            btn.setEnabled(False)
        self._set_ptz_enabled(False)  # 初始置灰（能力位连接后才解禁）
        QApplication.instance().aboutToQuit.connect(self._teardown)

    # ---------- OSD 卡构建 ----------

    def _build_osd_card(self, row: QHBoxLayout, key: str, title: str,
                        text_hint: str | None, has_pos: bool,
                        has_week: bool = False) -> dict:
        card = CardWidget(self)
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 10)
        v.addWidget(BodyLabel(title, card))
        edits: dict[str, LineEdit] = {}
        if text_hint:
            ed = LineEdit(card)
            ed.setPlaceholderText(text_hint)
            v.addWidget(ed)
            edits["text"] = ed
        chk_row = QHBoxLayout()
        show_chk = CheckBox("叠加显示", card)
        show_chk.setChecked(True)
        chk_row.addWidget(show_chk)
        week_chk = CheckBox("显示周", card) if has_week else None
        if week_chk is not None:
            chk_row.addWidget(week_chk)
        chk_row.addStretch(1)
        v.addLayout(chk_row)
        if has_pos:
            pos_row = QHBoxLayout()
            ex = LineEdit(card)
            ex.setPlaceholderText("区域 X（0-8191）")
            ey = LineEdit(card)
            ey.setPlaceholderText("区域 Y")
            pos_row.addWidget(ex)
            pos_row.addWidget(ey)
            edits["x"], edits["y"] = ex, ey
            v.addLayout(pos_row)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_get = PushButton("获取", card)
        btn_set = PrimaryPushButton("设置", card)
        btn_row.addWidget(btn_get)
        btn_row.addWidget(btn_set)
        v.addLayout(btn_row)
        row.addWidget(card, 1)
        btn_get.clicked.connect(lambda _=False, k=key: self._osd_get(k))
        btn_set.clicked.connect(lambda _=False, k=key: self._osd_set(k))
        return {"key": key, "edits": edits, "show": show_chk, "week": week_chk,
                "get": btn_get, "set": btn_set}

    def _iter_osd_buttons(self):
        for meta in (self._card_chn, self._card_time, self._card_custom):
            yield meta["get"]
            yield meta["set"]

    # ---------- 连接 / 断开 ----------

    def _connect(self):
        addr = self._ed_addr.text().strip()
        user = self._ed_user.text().strip() or "admin"
        pwd = self._ed_pwd.text()
        if not addr:
            _toast(self, "请填写设备地址")
            return
        self._btn_conn.setEnabled(False)
        self._lb_state.setText("连接中…")

        def work():
            client = dahua_sdk.connect(addr, user, pwd)
            return client, client.ptz_supported()

        self._pool.run(work, self._on_connected, self._on_conn_failed)

    def _on_connected(self, result):
        client, ptz_ok = result
        self._client = client
        self._btn_disc.setEnabled(True)
        self._btn_play.setEnabled(True)
        self._btn_snap.setEnabled(True)
        for btn in self._iter_osd_buttons():
            btn.setEnabled(True)
        for b in (self._btn_preset_goto, self._btn_preset_set, self._btn_preset_del):
            b.setEnabled(ptz_ok)
        self._set_ptz_enabled(ptz_ok)
        self._lb_state.setText(
            f"● {client.serial or client.host}（通道 {client.chan_num}）"
            f"{' · PT机型' if ptz_ok else ' · 固定镜头'}")
        _toast(self, f"连接成功：{client.serial}")
        self._osd_get("chn")
        self._osd_get("time")

    def _on_conn_failed(self, msg):
        self._btn_conn.setEnabled(True)
        self._lb_state.setText("✕ 连接失败")
        _toast(self, msg, error=True, duration=4000)

    def _disconnect(self):
        self._stop_preview()
        client, self._client = self._client, None
        if client:
            self._pool.run(client.close, lambda _r: None, lambda _m: None)
        self._btn_conn.setEnabled(True)
        self._btn_disc.setEnabled(False)
        self._btn_play.setEnabled(False)
        self._btn_snap.setEnabled(False)
        for btn in self._iter_osd_buttons():
            btn.setEnabled(False)
        for b in (self._btn_preset_goto, self._btn_preset_set, self._btn_preset_del):
            b.setEnabled(False)
        self._set_ptz_enabled(False)
        self._lb_state.setText("未连接")

    def _set_ptz_enabled(self, on: bool):
        for btn in self._ptz_btns.values():
            btn.setEnabled(on)
        for btn in self._zf_btns:
            btn.setEnabled(on)
        self._ptz_note.setVisible(not on)

    # ---------- 预览 / 抓图 ----------

    def _start_preview(self):
        if not self._client:
            return
        # SDK 需要 HWND 直渲染：强制原生窗口
        self._preview.setAttribute(Qt.WA_DontCreateNativeAncestors, True)
        self._preview.setAttribute(Qt.WA_NativeWindow, True)
        hwnd = int(self._preview.winId())
        self._btn_play.setEnabled(False)
        client = self._client

        def work():
            client.realplay_start(hwnd, channel=0, sub_stream=True)

        self._pool.run(work, lambda _r: self._preview_on(),
                       lambda m: (self._btn_play.setEnabled(True),
                                  _toast(self, m, error=True, duration=3500)))

    def _preview_on(self):
        self._btn_play.setEnabled(False)
        self._btn_stop_play.setEnabled(True)
        self._lb_pv_hint.hide()
        _toast(self, "预览已开启（辅码流）", duration=1800)

    def _stop_preview(self):
        if not self._client:
            return
        self._pool.run(self._client.realplay_stop,
                       lambda _r: self._preview_off(),
                       lambda _m: self._preview_off())

    def _preview_off(self):
        self._btn_play.setEnabled(True)
        self._btn_stop_play.setEnabled(False)
        self._lb_pv_hint.show()

    def _snap(self):
        if not self._client:
            return
        snap_dir = os.path.join(ROOT, "logs", "snap")
        os.makedirs(snap_dir, exist_ok=True)
        name = self._client.serial or "cam"
        path = os.path.join(snap_dir,
                            f"dahua_{name}_{datetime.now():%Y%m%d_%H%M%S}.jpg")
        self._btn_snap.setEnabled(False)
        client = self._client

        def work():
            return client.snap_to_file(path)

        self._pool.run(work, self._snap_done,
                       lambda m: (self._btn_snap.setEnabled(True),
                                  _toast(self, m, error=True, duration=3500)))

    def _snap_done(self, path: str):
        self._btn_snap.setEnabled(True)
        self._lb_snap.setText(f"已存：{path}")
        _toast(self, "抓图成功")

    # ---------- 云台 / 预置点 ----------

    def _ptz_cmd(self, command: str, stop: bool):
        if not self._client or not command:
            return
        speed = self._sp_speed.value()
        client = self._client

        def work():
            client.ptz(command, speed=speed, stop=stop)

        # 云台指令高频：失败静默落日志（按钮已按能力位过滤）
        self._pool.run(work, lambda _r: None,
                       lambda m: logger.info("PTZ %s: %s", command, m))

    def _preset(self, action: str):
        if not self._client:
            return
        index = self._sp_preset.value()
        client = self._client

        def work():
            client.ptz_preset(action, index)

        self._pool.run(work,
                       lambda _r: _toast(self, f"预置点 {action} #{index} 成功",
                                         duration=1800),
                       lambda m: _toast(self, m, error=True, duration=3500))

    # ---------- OSD 读写 ----------

    def _osd_get(self, key: str):
        if not self._client:
            return
        client = self._client

        def work():
            if key == "chn":
                return client.osd_channel_title_read(0)
            if key == "time":
                return client.osd_time_title(0)
            return {}

        def done(result):
            meta = self._card_chn if key == "chn" else self._card_time
            if result.get("name"):
                meta["edits"]["text"].setText(result["name"])
            meta["show"].setChecked(bool(result.get("show")))
            if "show_week" in result and meta.get("week"):
                meta["week"].setChecked(bool(result["show_week"]))
            x, y = result.get("pos", (0, 0))
            meta["edits"]["x"].setText(str(x))
            meta["edits"]["y"].setText(str(y))
            _toast(self, "OSD 配置已读取", duration=1500)

        self._pool.run(work, done,
                       lambda m: _toast(self, m, error=True, duration=3500))

    def _osd_set(self, key: str):
        if not self._client:
            return
        meta = self._card_chn if key == "chn" else self._card_time
        show = meta["show"].isChecked()
        week = meta["week"].isChecked() if meta.get("week") else None
        try:
            pos = (max(0, int(meta["edits"]["x"].text() or 0)),
                   max(0, int(meta["edits"]["y"].text() or 0)))
        except ValueError:
            pos = (0, 0)
        text = meta["edits"].get("text")
        text = text.text().strip() if text else ""
        client = self._client

        def work():
            if key == "chn":
                client.osd_channel_title(0, text=text or None, show=show, pos=pos)
            elif key == "time":
                client.osd_time_title(0, show=show, show_week=week, pos=pos)
            else:
                client.osd_custom_text(0, text or "", show, pos)

        self._pool.run(work,
                       lambda _r: _toast(self, "OSD 设置成功", duration=1800),
                       lambda m: _toast(self, m, error=True, duration=3500))

    # ---------- 收尾 ----------

    def _teardown(self):
        self._pool.wait_all()
        if self._client:
            self._client.close()
