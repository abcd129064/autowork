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
import time
from datetime import datetime

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QGridLayout, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem,
                               QScrollArea, QSizePolicy, QVBoxLayout,
                               QWidget)
from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, CheckBox,
                            FluentIcon, LineEdit, PasswordLineEdit,
                            PrimaryPushButton, PushButton, SearchLineEdit,
                            SpinBox, ToolButton)

from core import dahua_sdk
from core.dahua_sdk import DahuaClient
from core.frps_admin import get_frps_client
from core.utils import show_info_bar

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _toast(widget: QWidget, msg: str, error: bool = False, duration: int = 2500):
    show_info_bar(msg, message_type="error" if error else "success",
                  duration=duration, parent=widget)


# ---------------------------------------------------------------- 线程助手


class _SerialCaller(QThread):
    """设备 SDK 调用专用**单线程串行执行器**（FIFO）。

    ⚠ 同一登录会话上的配置请求（GetConfig/SetConfig/NewDevConfig）并发会
    串协议：隧道下表现为 "Protocol error it may result from network
    timeout"（2026-10-07 真机）。所以连接后的自动 get chn + get time、
    用户点「设置」等全部在本线程排队执行，而不是各开 QThread 并发。
    ok/err 回调经 _done 信号回主线程，UI 触控安全。
    """

    _done = Signal(object)
    PACING_S = 0.25  # 相邻设备调用最小间距（防隧道协议超时，配合 core 层重试）

    def __init__(self, parent=None):
        super().__init__(parent)
        import queue
        self._q: "queue.Queue" = queue.Queue()
        self._done.connect(self._dispatch)  # 队列连接：回调回主线程
        self.start()

    def submit(self, fn, on_ok, on_err):
        self._q.put((fn, on_ok, on_err))

    def run(self):  # noqa: D102
        while True:
            item = self._q.get()
            if item is None:  # stop 哨兵
                return
            fn, on_ok, on_err = item
            try:
                rec = (on_ok, on_err, True, fn())
            except Exception as e:  # noqa: BLE001 统一转 UI 错误提示
                logger.warning("相机调用失败: %s", e, exc_info=True)
                rec = (on_ok, on_err, False, str(e))
            self._done.emit(rec)
            time.sleep(self.PACING_S)  # 隧道下配置请求须留间距，背靠背会协议超时

    def _dispatch(self, rec):
        on_ok, on_err, ok, payload = rec
        if ok:
            on_ok(payload)
        else:
            on_err(payload)

    def stop(self, ms: int = 5000) -> None:
        self._q.put(None)
        self.wait(ms)


# ---------------------------------------------------------------- 页面


class _PreviewCanvas(QWidget):
    """SDK 直渲 HWND 的画布：播放时 Qt 零绘制，非播放时主动填暗底。

    根因：若预览区是普通 QWidget/QLabel（哪怕带 WA_NativeWindow），
    任何 Qt 重绘（InfoBar、状态标签、resize、主题刷新）都会先用背景色
    填掉 SDK 画好的帧，下一帧视频再盖回来 → 时不时闪一下。
    - WA_PaintOnScreen：绕过 Qt backing store，不与 SDK 抢同一 HWND 的绘制权；
    - 播放中 paintEvent 置空：重绘事件什么都不画，绘制权全在 SDK；
    - 非播放时 paintEvent 主动填暗底：WA_PaintOnScreen 下 Qt 从不清屏，
      HWND 表面会残留其它窗口像素垃圾（2026-10-07 用户截图：工作台页残影），
      停止预览后最后一帧也会永久冻结——所以按 playing 门控。
    """

    BG = "#10141c"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_PaintOnScreen, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.playing = False

    def set_playing(self, on: bool):
        """切换播放态；停止时主动重绘一次，擦掉冻结的最后一帧"""
        self.playing = bool(on)
        if not self.playing:
            self.update()

    def paintEvent(self, event):  # noqa: N802
        if self.playing:
            return  # 播放中：绘制权全在 SDK，Qt 一个像素都不画
        from PySide6.QtGui import QPainter, QColor
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(self.BG))
        p.end()



_DIR_KEYS = [  # 九宫格 → SDK_PTZ_ControlType 命令名
    ("LEFTTOP", "↖"), ("UP_CONTROL", "↑"), ("RIGHTTOP", "↗"),
    ("LEFT_CONTROL", "←"), ("", "·"), ("RIGHT_CONTROL", "→"),
    ("LEFTDOWN", "↙"), ("DOWN_CONTROL", "↓"), ("RIGHTDOWN", "↘"),
]

# ---- frps 相机隧道（*_cam 快速选用，2026-10-09） ----
# ⚠ 匹配规则是「包含 _cam」而非后缀：真机名单有 147_cam1 / apex_cam01 /
#   ly_cam15_sdk / jp_cam2_rtsp 等形态（_cam 后还带编号/用途后缀）
_CAM_MARK = "_cam"
_CAM_STATUS = {"online": ("在线", "#70d49c"), "offline": ("离线", "#e05555")}


def collect_cam_proxies(all_proxies: dict) -> list:
    """从 frps all_proxies() {type: [proxy,…]} 提取名称含 _cam 的相机隧道，按名称排序。

    proxy 字段见 core/frps_admin._parse_proxy_list（conf.remotePort =
    frps 侧监听端口）。"""
    out = []
    for proxies in (all_proxies or {}).values():
        for p in proxies or []:
            name = str(p.get("name") or "")
            if _CAM_MARK in name.lower():
                conf = p.get("conf") or {}
                out.append({"name": name, "user": str(p.get("user") or ""),
                            "status": str(p.get("status") or "").lower(),
                            "curConns": int(p.get("curConns") or 0),
                            "remotePort": conf.get("remotePort")})
    out.sort(key=lambda p: p["name"])
    return out


class _PreviewFullScreen(QWidget):
    """预览全屏窗：把 _PreviewCanvas 借进独立无边框窗口，Esc / 双击退出。

    SDK 只认 HWND 直渲，所以全屏不是"复制画面"，而是搬同一块画布进新窗、
    对新 HWND 重新 realplay_start；退出时搬回原布局并恢复原播放状态。
    """

    closed = Signal()

    def __init__(self, canvas: QWidget):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setStyleSheet("background:#000;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(canvas)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        self.close()

    def closeEvent(self, event):  # noqa: N802
        self.closed.emit()
        super().closeEvent(event)


class DahuaCameraWork(QWidget):
    """相机工具：登录 → 预览 → 云台（能力位自适应） → OSD 叠加"""

    def __init__(self, win=None, parent=None):
        super().__init__(parent)
        self._win = win
        self._client: DahuaClient | None = None
        self._serial = _SerialCaller(self)
        self.setObjectName("dahuaCameraWork")

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(10)

        # ---- 三列布局：登录卡(含 frps 隧道) | 预览 | 云台（设计稿 camera_tool_frps_v2.html）----
        cols = QHBoxLayout()
        cols.setSpacing(10)

        login_card = CardWidget(self)
        login_card.setFixedWidth(300)
        lv = QVBoxLayout(login_card)
        lv.setContentsMargins(14, 12, 14, 12)
        lv.setSpacing(6)
        lv.addWidget(BodyLabel("设备登录", login_card))

        def _field(label_text: str, widget: QWidget):
            box = QWidget(login_card)
            fv = QVBoxLayout(box)
            fv.setContentsMargins(0, 0, 0, 0)
            fv.setSpacing(2)
            cap = CaptionLabel(label_text, box)
            cap.setStyleSheet("color:#9aa1b5;")
            fv.addWidget(cap)
            fv.addWidget(widget)
            lv.addWidget(box)

        self._ed_addr = LineEdit(login_card)
        self._ed_addr.setPlaceholderText("设备地址，支持隧道 host:port")
        _field("服务器地址", self._ed_addr)
        self._ed_user = LineEdit(login_card)
        self._ed_user.setText("admin")
        _field("账号", self._ed_user)
        self._ed_pwd = PasswordLineEdit(login_card)
        _field("密码", self._ed_pwd)
        btns = QHBoxLayout()
        self._btn_conn = PrimaryPushButton(FluentIcon.LINK, "连接", login_card)
        self._btn_disc = PushButton(FluentIcon.CLOSE, "断开", login_card)
        self._btn_disc.setEnabled(False)
        btns.addWidget(self._btn_conn)
        btns.addWidget(self._btn_disc)
        btns.addStretch(1)
        lv.addLayout(btns)
        self._lb_state = BodyLabel("未连接", login_card)
        self._lb_state.setStyleSheet("color:#9aa1b5;")
        lv.addWidget(self._lb_state)

        # ---- frps 相机隧道（登录卡底部，*_cam 快速选用 + 搜索，2026-10-09） ----
        lv.addSpacing(6)
        fh = QHBoxLayout()
        fh.setSpacing(6)
        self._lb_frps = BodyLabel("📡 frps 相机隧道", login_card)
        self._lb_frps_cnt = CaptionLabel("", login_card)
        self._lb_frps_cnt.setStyleSheet("color:#4f8cff;")
        self._btn_frps_refresh = ToolButton(FluentIcon.SYNC, login_card)
        self._btn_frps_refresh.setToolTip("立即拉取 frps 代理名单")
        self._btn_frps_refresh.clicked.connect(self._frps_refresh)
        fh.addWidget(self._lb_frps)
        fh.addWidget(self._lb_frps_cnt)
        fh.addStretch(1)
        fh.addWidget(self._btn_frps_refresh)
        lv.addLayout(fh)
        self._ed_frps_search = SearchLineEdit(login_card)
        self._ed_frps_search.setPlaceholderText("搜索隧道名…")
        self._ed_frps_search.setClearButtonEnabled(True)
        self._ed_frps_search.textChanged.connect(self._frps_rebuild)
        lv.addWidget(self._ed_frps_search)
        # 隧道列表复用售后/远程模块同款候选列表（QListWidget + _style_cand_list，
        # 主题反色与强调色统一由共享工厂维护），点行即「选用」
        from windows.aftersale.common import _style_cand_list
        self._frps_list = QListWidget(login_card)
        self._frps_list.setMinimumHeight(140)
        self._frps_list.setFocusPolicy(Qt.NoFocus)
        _style_cand_list(self._frps_list)
        self._frps_list.itemClicked.connect(self._on_frps_item)
        lv.addWidget(self._frps_list, 1)
        cols.addWidget(login_card)
        # frps 感知自动刷新（定时器在 frps_admin 单例内）→ 隧道列表跟随重建
        get_frps_client().all_proxies_changed.connect(
            lambda _d: self._frps_rebuild())
        self._frps_rebuild()

        # ---- 中区：预览 + 云台 ----
        mid = cols

        preview_card = CardWidget(self)
        pv = QVBoxLayout(preview_card)
        pv.setContentsMargins(12, 10, 12, 12)
        self._preview = _PreviewCanvas(preview_card)
        self._preview.setMinimumSize(560, 315)
        pv.addWidget(self._preview, 1)
        self._pv_layout = pv  # 全屏时借出画布，退出时按索引还原
        self._lb_pv_hint = CaptionLabel(
            "连接后点击「开始预览」", preview_card)
        pv.addWidget(self._lb_pv_hint)
        pbar = QHBoxLayout()
        self._btn_play = PrimaryPushButton(FluentIcon.VIDEO, "开始预览", preview_card)
        self._btn_play.setEnabled(False)
        self._btn_stop_play = PushButton(FluentIcon.PAUSE, "停止", preview_card)
        self._btn_stop_play.setEnabled(False)
        self._btn_snap = PushButton(FluentIcon.CAMERA, "抓图存档", preview_card)
        self._btn_snap.setEnabled(False)
        self._btn_full = PushButton(FluentIcon.FULL_SCREEN, "全屏", preview_card)
        self._btn_full.setEnabled(False)
        self._lb_snap = CaptionLabel("", preview_card)
        for w in (self._btn_play, self._btn_stop_play, self._btn_snap,
                  self._btn_full):
            pbar.addWidget(w)
        pbar.addStretch(1)
        pbar.addWidget(self._lb_snap)
        pv.addLayout(pbar)
        mid.addWidget(preview_card, 5)

        ptz_card = CardWidget(self)
        ptz_card.setMaximumWidth(430)

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
            "方向/预置点仅 PT 机型有效；固定镜头机型此区域置灰。", ptz_card)
        self._ptz_note.setWordWrap(True)
        ptz.addWidget(self._ptz_note)
        ptz.addStretch(1)
        # ---- 预置点列表（设计稿形态：编号徽标 + 名称 + 调用/设为此处/删 + 新增行）----
        self._preset_panel = QWidget(ptz_card)
        pp = QVBoxLayout(self._preset_panel)
        pp.setContentsMargins(0, 4, 0, 0)
        pp.setSpacing(6)
        phead = QHBoxLayout()
        phead.addWidget(BodyLabel("预置点", self._preset_panel))
        phead.addStretch(1)
        phead.addWidget(CaptionLabel("PTZ Preset(SET/GOTO/DEL)", self._preset_panel))
        pp.addLayout(phead)

        self._preset_host = QWidget(self._preset_panel)
        self._preset_v = QVBoxLayout(self._preset_host)
        self._preset_v.setContentsMargins(0, 0, 0, 0)
        self._preset_v.setSpacing(4)
        self._preset_v.addStretch(1)
        scroll = QScrollArea(self._preset_panel)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._preset_host)
        scroll.setFixedHeight(118)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}"
                             "QWidget{background:transparent;}")
        pp.addWidget(scroll)

        self._btn_preset_add = PushButton(FluentIcon.ADD, "新增预置点…",
                                          self._preset_panel)
        self._btn_preset_add.clicked.connect(self._on_preset_add)
        pp.addWidget(self._btn_preset_add)

        self._preset_editor = QWidget(self._preset_panel)
        pe = QHBoxLayout(self._preset_editor)
        pe.setContentsMargins(0, 0, 0, 0)
        self._ed_preset_name = LineEdit(self._preset_editor)
        self._ed_preset_name.setPlaceholderText("预置点名称")
        self._btn_preset_ok = PrimaryPushButton("确定", self._preset_editor)
        self._btn_preset_cancel = PushButton("取消", self._preset_editor)
        pe.addWidget(self._ed_preset_name, 1)
        pe.addWidget(self._btn_preset_ok)
        pe.addWidget(self._btn_preset_cancel)
        self._btn_preset_ok.clicked.connect(self._on_preset_add_confirm)
        self._btn_preset_cancel.clicked.connect(self._on_preset_add_cancel)
        self._preset_editor.hide()
        pp.addWidget(self._preset_editor)

        self._preset_panel.setVisible(False)
        ptz.addWidget(self._preset_panel)
        mid.addWidget(ptz_card, 3)
        root.addLayout(mid, 1)

        # ---- OSD 卡 ----
        osd_row = QHBoxLayout()
        osd_row.setSpacing(10)
        self._card_chn = self._build_osd_card(
            osd_row, "chn", "通道标题", text_hint="叠加文字", has_pos=True,
            has_blends=True)
        self._card_time = self._build_osd_card(
            osd_row, "time", "时间标题", text_hint=None, has_pos=True, has_week=True)
        self._card_custom = self._build_osd_card(
            osd_row, "custom", "自定义文字告示",
            text_hint="告示文字", has_pos=True)
        root.addLayout(osd_row)

        self._btn_conn.clicked.connect(self._connect)
        self._btn_disc.clicked.connect(self._disconnect)
        self._btn_play.clicked.connect(self._start_preview)
        self._btn_stop_play.clicked.connect(self._stop_preview)
        self._btn_snap.clicked.connect(self._snap)
        self._btn_full.clicked.connect(self._on_fullscreen)
        for btn in self._iter_osd_buttons():
            btn.setEnabled(False)
        self._set_ptz_enabled(False)  # 初始置灰（能力位连接后才解禁）
        # 切走再切回：延迟 1s 再起流（用户实测 0.5s 闪烁，等 DWM 重组完成）
        self._resume_pending = False
        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.setInterval(1000)
        self._resume_timer.timeout.connect(self._resume_preview)
        QApplication.instance().aboutToQuit.connect(self._teardown)

    # ---------- OSD 卡构建 ----------

    def _build_osd_card(self, row: QHBoxLayout, key: str, title: str,
                        text_hint: str | None, has_pos: bool,
                        has_week: bool = False,
                        has_blends: bool = False) -> dict:
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
        # 叠加码流：与设计稿一致，主码流/预览码流分别勾选（对应 emOsdBlendType 1/6）
        blend_main = CheckBox("叠加到主码流", card) if has_blends else None
        blend_pv = CheckBox("叠加到预览码流", card) if has_blends else None
        for cb in (blend_main, blend_pv):
            if cb is not None:
                cb.setChecked(True)
                chk_row.addWidget(cb)
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
                "blend_main": blend_main, "blend_pv": blend_pv,
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

        self._serial.submit(work, self._on_connected, self._on_conn_failed)

    def _on_connected(self, result):
        client, ptz_ok = result
        self._client = client
        self._btn_disc.setEnabled(True)
        self._btn_play.setEnabled(True)
        self._btn_snap.setEnabled(True)
        self._btn_full.setEnabled(True)
        for btn in self._iter_osd_buttons():
            btn.setEnabled(True)
        self._set_ptz_enabled(ptz_ok)
        self._lb_state.setText(
            f"● {client.serial or client.host}（通道 {client.chan_num}）"
            f"{' · PT机型' if ptz_ok else ' · 固定镜头'}")
        _toast(self, f"连接成功：{client.serial}")
        self._osd_get("chn")
        self._osd_get("time")
        if ptz_ok:
            self._refresh_presets()

    def _on_conn_failed(self, msg):
        self._btn_conn.setEnabled(True)
        self._lb_state.setText("✕ 连接失败")
        _toast(self, msg, error=True, duration=4000)

    def set_address(self, addr: str):
        """预填登录地址（frps 隧道「选用」一键带参）"""
        self._ed_addr.setText(addr)
        self._ed_pwd.setFocus()

    # ---------- frps 相机隧道 ----------

    def _frps_rebuild(self):
        """按当前搜索词重建隧道列表（数据源 frps 感知缓存）。

        行=隧道；离线/无端口的项置 NoItemFlags 只展示不可选；
        空态用一条不可选提示行，与售后候选列表的空态写法一致。
        """
        lst = self._frps_list
        lst.clear()
        proxies = collect_cam_proxies(get_frps_client().all_proxies())
        kw = self._ed_frps_search.text().strip().lower()
        shown = [p for p in proxies
                 if not kw or kw in p["name"].lower()] if proxies else []
        if not proxies:
            item = QListWidgetItem("暂无 _cam 相机隧道（frps 未配置/不可达）")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            lst.addItem(item)
            self._lb_frps_cnt.setText("")
            return
        self._lb_frps_cnt.setText(
            f"{len(shown)}/{len(proxies)}" if kw else str(len(proxies)))
        if not shown:
            item = QListWidgetItem("无匹配隧道")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            lst.addItem(item)
            return
        for p in shown:
            status, _color = _CAM_STATUS.get(p["status"], ("未知", ""))
            item = QListWidgetItem(
                f"{p['name']}  ·  {status} · {p['remotePort'] or '-'}")
            item.setData(Qt.UserRole, p)
            item.setToolTip(f"隧道: {p['name']}\n状态: {status}\n"
                            f"远程端口: {p['remotePort'] or '-'}\n"
                            f"当前连接数: {p['curConns']}" +
                            (f"\n用户: {p['user']}" if p["user"] else ""))
            if p["status"] != "online" or not p["remotePort"]:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
            lst.addItem(item)

    def _on_frps_item(self, item):
        """点行即选用：把 frps 地址+端口预填进登录卡"""
        p = item.data(Qt.UserRole)
        if p:
            self._frps_use(p)

    def _frps_use(self, p: dict):
        addr = f"{self._frps_host()}:{p['remotePort']}"
        self.set_address(addr)
        logger.info("frps 相机隧道 %s → 选用 %s", p["name"], addr)

    def _frps_refresh(self):
        cl = get_frps_client()
        cl.request_refresh()
        if cl.snapshot().get("state") != "ok":
            _toast(self, "frps 感知未就绪（远程 → frps 代理 页配置凭据）",
                   duration=3500)

    def _frps_host(self) -> str:
        try:
            from core.frp_remote import get_session_manager
            return get_session_manager().frps_server_addr()
        except Exception as e:  # noqa: BLE001
            logger.warning("取 frps 地址失败: %s", e)
            return ""

    def _disconnect(self):
        self._stop_preview()
        client, self._client = self._client, None
        if client:
            self._serial.submit(client.close, lambda _r: None, lambda _m: None)
        self._btn_conn.setEnabled(True)
        self._btn_disc.setEnabled(False)
        self._btn_play.setEnabled(False)
        self._btn_snap.setEnabled(False)
        self._btn_full.setEnabled(False)
        self._resume_pending = False
        self._resume_timer.stop()
        for btn in self._iter_osd_buttons():
            btn.setEnabled(False)
        self._set_ptz_enabled(False)
        self._lb_state.setText("未连接")

    def _set_ptz_enabled(self, on: bool):
        for btn in self._ptz_btns.values():
            btn.setEnabled(on)
        for btn in self._zf_btns:
            btn.setEnabled(on)
        self._ptz_note.setVisible(not on)
        self._preset_panel.setVisible(on)

    # ---------- 预置点列表 ----------

    def _refresh_presets(self):
        if not self._client:
            return
        client = self._client

        def work():
            return client.ptz_presets()

        self._serial.submit(work, self._presets_loaded,
                            lambda m: logger.info("预置点列表读取失败: %s", m))

    def _presets_loaded(self, presets: list):
        # 清空旧行（保留末尾 stretch）
        while self._preset_v.count() > 1:
            item = self._preset_v.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for p in presets:
            self._preset_v.insertWidget(self._preset_v.count() - 1,
                                        self._preset_row(p["index"], p["name"]))

    def _preset_row(self, idx: int, name: str) -> QWidget:
        row = QWidget(self._preset_host)
        row.setStyleSheet("QWidget{background:#232838;border-radius:6px;}")
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 5, 10, 5)
        h.setSpacing(8)
        badge = QLabel(str(idx), row)
        badge.setFixedSize(20, 20)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet("background:#4f8cff;color:#fff;border-radius:10px;"
                            "font-size:11px;font-weight:600;")
        h.addWidget(badge)
        name_lb = BodyLabel(name, row)
        name_lb.setTextInteractionFlags(Qt.NoTextInteraction)
        h.addWidget(name_lb, 1)
        for text, route, color in (("调用", f"goto:{idx}", "#4f8cff"),
                                   ("设为此处", f"set:{idx}", "#4f8cff"),
                                   ("删", f"del:{idx}", "#e05555")):
            link = QLabel(f'<a style="color:{color};text-decoration:none;" '
                          f'href="{route}">{text}</a>', row)
            link.setTextFormat(Qt.RichText)
            link.setCursor(Qt.PointingHandCursor)
            link.linkActivated.connect(self._on_preset_link)
            h.addWidget(link)
        return row

    def _on_preset_link(self, route: str):
        kind, _, s_idx = route.partition(":")
        idx = int(s_idx)
        if not self._client:
            return
        client = self._client

        def work():
            client.ptz_preset(kind, idx)
            return kind

        done = (lambda _r: self._refresh_presets() if kind in ("set", "del")
                else None)
        self._serial.submit(work, done, lambda m: _toast(
            self, m, error=True, duration=3500))

    def _on_preset_add(self):
        self._btn_preset_add.hide()
        self._ed_preset_name.clear()
        self._preset_editor.show()
        self._ed_preset_name.setFocus()

    def _on_preset_add_cancel(self):
        self._preset_editor.hide()
        self._btn_preset_add.show()

    def _on_preset_add_confirm(self):
        if not self._client:
            return
        name = self._ed_preset_name.text().strip()
        client = self._client

        def work():
            existing = {p["index"] for p in client.ptz_presets()}
            idx = next(i for i in range(1, 301) if i not in existing)
            client.ptz_preset("set", idx)  # 保存相机当前位置到新编号
            if name:
                client.set_preset_alias(idx, name)
            return client.ptz_presets()

        self._serial.submit(work, self._presets_loaded,
                            lambda m: _toast(self, m, error=True, duration=3500))
        self._on_preset_add_cancel()

    # ---------- 预览 / 抓图 ----------

    def _start_preview(self, silent: bool = False):
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

        self._serial.submit(work, lambda _r: self._preview_on(silent),
                       lambda m: (self._btn_play.setEnabled(True),
                                  _toast(self, m, error=True, duration=3500)))

    def _preview_on(self, silent: bool = False):
        self._btn_play.setEnabled(False)
        self._btn_stop_play.setEnabled(True)
        self._preview.set_playing(True)  # 播放中：Qt 停止绘制，SDK 独占
        self._lb_pv_hint.hide()
        if not silent:
            _toast(self, "预览已开启（辅码流）", duration=1800)

    def _stop_preview(self):
        if not self._client:
            return
        self._serial.submit(self._client.realplay_stop,
                       lambda _r: self._preview_off(),
                       lambda _m: self._preview_off())

    def _preview_off(self):
        self._btn_play.setEnabled(True)
        self._btn_stop_play.setEnabled(False)
        self._preview.set_playing(False)  # 停止：填暗底，擦掉冻结帧
        self._lb_pv_hint.show()

    # ---------- 页面显隐（防切回闪烁） ----------

    def hideEvent(self, event):  # noqa: N802
        """切走页面：停流并记下待恢复。

        留最后一帧没意义：原生 HWND 再次显示时会被窗口系统重合成黑场/半帧，
        用户看到的 0.5s 闪烁就来自这段时间差。
        """
        super().hideEvent(event)
        if getattr(self, "_fs_win", None) is not None:
            self._fs_win.close()  # 全屏中切页：先收回画布，避免孤儿窗口
        if self._preview.playing:
            self._resume_pending = True
            # 立刻转暗底再异步停流：串行队列有 250ms 节流，等回调才变
            # playing=False 会留出空绘制窗口，正是切回闪烁的来源
            self._preview.set_playing(False)
            self._stop_preview()

    def showEvent(self, event):  # noqa: N802
        """切回页面：先铺暗底，延迟 1s 再起流（等 DWM 合成稳定）。"""
        super().showEvent(event)
        if self._resume_pending:
            self._resume_pending = False
            self._resume_timer.start(1000)

    def _resume_preview(self):
        """延迟恢复预览：期间用户可能已手动点过开始，重复起流要跳过"""
        if self._client and self.isVisible() and not self._preview.playing:
            self._start_preview(silent=True)

    # ---------- 全屏 ----------

    def _on_fullscreen(self):
        """进入全屏：停流 → 画布借进全屏窗 → 对新 HWND 重起流。

        串行队列保证 stop 先于 start 执行；非播放态进入时不自动起流，
        用户在全屏里点「开始预览」同样可用（按钮在全屏窗内不可及，
        所以非播放态进入时直接起流，避免全屏后无处可点）。
        """
        if not self._client:
            _toast(self, "请先连接设备", duration=3000)
            return
        if getattr(self, "_fs_win", None) is not None:
            return
        was_playing = self._preview.playing
        if was_playing:
            self._stop_preview()
        self._fs_was_playing = was_playing
        self._pv_layout.removeWidget(self._preview)
        win = _PreviewFullScreen(self._preview)
        win.closed.connect(self._exit_fullscreen)
        self._fs_win = win
        win.showFullScreen()
        win.setFocus()
        self._start_preview()  # stop 已排前面，新 HWND 起流

    def _exit_fullscreen(self):
        """退出全屏：停流 → 画布放回原布局 → 恢复进入前的播放状态。"""
        was = getattr(self, "_fs_was_playing", False)
        if self._preview.playing:
            self._stop_preview()
        self._fs_win = None
        self._pv_layout.insertWidget(0, self._preview, 1)
        self._preview.set_playing(False)
        if was and self._client:
            if self.isVisible():
                self._start_preview()
            else:
                self._resume_pending = True  # 页面不可见：等切回再恢复

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

        self._serial.submit(work, self._snap_done,
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
        self._serial.submit(work, lambda _r: None,
                       lambda m: logger.info("PTZ %s: %s", command, m))

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
            if "show_main" in result and meta.get("blend_main"):
                meta["blend_main"].setChecked(bool(result["show_main"]))
                meta["blend_pv"].setChecked(bool(result["show_preview"]))
            if "show_week" in result and meta.get("week"):
                meta["week"].setChecked(bool(result["show_week"]))
            x, y = result.get("pos", (0, 0))
            meta["edits"]["x"].setText(str(x))
            meta["edits"]["y"].setText(str(y))
            _toast(self, "OSD 配置已读取", duration=1500)

        self._serial.submit(work, done,
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
        # 叠加码流白名单：未勾选的码流由 core 显式关闭（用户勾选=只在这几路显示）
        blends = None
        if key == "chn" and meta.get("blend_main") is not None:
            blends = []
            if meta["blend_main"].isChecked():
                blends.append(DahuaClient._BLEND_MAIN)
            if meta["blend_pv"].isChecked():
                blends.append(DahuaClient._BLEND_EXTRA1)
        client = self._client

        def work():
            if key == "chn":
                return client.osd_channel_title(0, text=text or None,
                                                show=show, pos=pos,
                                                blends=blends)
            if key == "time":
                client.osd_time_title(0, show=show, show_week=week, pos=pos)
            else:
                client.osd_custom_text(0, text or "", show, pos)
            return None

        def done(result):
            if (isinstance(result, dict) and key == "chn"
                    and blends and DahuaClient._BLEND_EXTRA1 in blends
                    and not result.get("show_preview", True)):
                _toast(self, "该固件不支持叠加到预览码流（仅主码流生效）",
                       duration=4000)
                return
            if isinstance(result, dict) and result.get("name_applied") is False:
                _toast(self, "该固件不支持修改通道名称"
                             "开关/位置已应用", duration=4000)
            else:
                _toast(self, "OSD 设置成功", duration=1800)

        self._serial.submit(work, done,
                       lambda m: _toast(self, m, error=True, duration=3500))

    # ---------- 收尾 ----------

    def _teardown(self):
        self._serial.stop()
        if self._client:
            self._client.close()
