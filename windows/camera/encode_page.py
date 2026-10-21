# -*- coding: utf-8 -*-
"""相机面板 ·「编码设置」页（v6 两列版，宽屏右侧自动拆两列 → 三列）。

结构（设计稿 design/encode_settings_v6_three.html / v8）：
- 左列：上=预览卡（固定），下=主码流/辅码流（内部滚动）
- 右列：图像属性（9601 镜像/翻转/旋转）+ P2 占位卡片（独立滚动）
宽屏（≥1900px）右侧卡片自动分两列，总成三列；窄屏回落单列。

数据源：core/dahua_sdk（ENCODE_VIDEO=1100 / CFG_VIDEO_IMAGE_CTRL=9601）。
设备调用复用相机工具页登录会话与 _SerialCaller 串行队列。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QScrollArea, QSizePolicy, QSlider, QVBoxLayout,
                               QWidget)
from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, CheckBox,
                            ComboBox, FluentIcon, PrimaryPushButton,
                            PushButton, SpinBox)

from core.utils import show_info_bar
from windows.camera.camera_page import _PreviewCanvas

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_COMPRESSIONS = [("H.264", 7), ("H.265", 8), ("MJPG", 5), ("MPEG4", 0)]
_BITRATE_CONTROLS = [("VBR 可变码率", 1), ("CBR 固定码率", 0)]
_RESOLUTIONS = [("1080P", 1920, 1080), ("720P", 1280, 720),
                ("D1", 704, 576), ("360P", 640, 360), ("CIF", 352, 288)]
_BITRATE_PRESETS = [512, 1024, 2048, 3072, 4096, 6144, 8192]
_IFRAME_PRESETS = [25, 50, 100]
_QUALITY_LABELS = {1: "10%", 2: "30%", 3: "50%", 4: "60%", 5: "80%", 6: "100%"}
_CUSTOM_RES = "__custom__"
_ROTATIONS = [("不旋转", 0), ("顺时针 90°", 1), ("逆时针 90°", 2)]
_WIDE_THRESHOLD = 1900   # 宽于此值右侧卡片拆两列（窗口总宽）


def _toast(widget, msg, error=False, duration=3000):
    show_info_bar(msg, message_type="error" if error else "success",
                  duration=duration, parent=widget)


class SpinBox4(QWidget):
    """宽/高 小输入组（带范围）"""

    def __init__(self, parent, lo, hi):
        super().__init__(parent)
        v = QHBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.sp = SpinBox(self)
        self.sp.setRange(lo, hi)
        self.sp.setMinimumWidth(58)
        v.addWidget(self.sp)

    def setValue(self, val):
        self.sp.setValue(int(val))

    def value(self):
        return self.sp.value()


class EncodePage(QWidget):
    """编码设置：预览 + 主/辅码流 + 图像属性（镜像/翻转/旋转）"""

    def __init__(self, win=None, cam_page=None, parent=None):
        super().__init__(parent)
        self._win = win
        self._cam = cam_page    # DahuaCameraWork：共享登录会话与串行执行器
        self._cards: dict[int, dict] = {}
        self._right_cols = 0
        self.setObjectName("encodePage")

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 8)
        root.setSpacing(6)

        # ---- 顶栏：通道 + 状态 + 读取全部 ----
        top = QHBoxLayout()
        top.addWidget(BodyLabel("通道", self))
        self._cmb_ch = ComboBox(self)
        self._cmb_ch.addItem("1")
        self._cmb_ch.setFixedWidth(70)
        top.addWidget(self._cmb_ch)
        self._lb_state = CaptionLabel("未连接（请先在「相机工具」页连接设备）", self)
        self._lb_state.setStyleSheet("color:#9aa1b5;")
        top.addSpacing(8)
        top.addWidget(self._lb_state)
        top.addStretch(1)
        self._btn_read = PushButton(FluentIcon.DOWNLOAD, "读取全部", self)
        self._btn_read.clicked.connect(self._on_read)
        top.addWidget(self._btn_read)
        root.addLayout(top)

        # ---- 两列主体 ----
        blocks = QHBoxLayout()
        blocks.setSpacing(10)

        # 左列：上预览（固定）/ 下码流（滚动）
        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(self._build_preview_card())
        self._stream_scroll = QScrollArea(self)
        self._stream_scroll.setWidgetResizable(True)
        host = QWidget(self._stream_scroll)
        sv = QVBoxLayout(host)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(8)
        self._cards[1] = self._build_stream_card(sv, "主码流", 1)
        self._cards[4] = self._build_stream_card(sv, "辅码流1", 4)
        sv.addStretch(1)
        self._stream_scroll.setWidget(host)
        # 视口默认不透明，会把卡片衬成实心黑并挡掉窗口 Mica；setWidget()
        # 之后必须补视口样式（本项目统一做法，见 main_window/hub_pages.py）
        self._stream_scroll.setFrameShape(QFrame.NoFrame)
        self._stream_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._stream_scroll.viewport().setAutoFillBackground(False)
        self._stream_scroll.setStyleSheet(
            "QScrollArea{border:none;background:transparent;}")
        self._stream_scroll.viewport().setStyleSheet("background:transparent;")
        left.addWidget(self._stream_scroll, 1)
        blocks.addLayout(left, 3)

        # 右列：图像属性 + P2 占位（独立滚动，宽屏拆两列）
        self._right_scroll = QScrollArea(self)
        self._right_scroll.setWidgetResizable(True)
        self._right_host = QWidget(self._right_scroll)
        self._right_grid = QGridLayout(self._right_host)
        self._right_grid.setContentsMargins(0, 0, 0, 0)
        self._right_grid.setHorizontalSpacing(8)
        self._right_grid.setVerticalSpacing(8)
        self._right_scroll.setWidget(self._right_host)
        self._right_scroll.setFrameShape(QFrame.NoFrame)
        self._right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._right_scroll.viewport().setAutoFillBackground(False)
        self._right_scroll.setStyleSheet(
            "QScrollArea{border:none;background:transparent;}")
        self._right_scroll.viewport().setStyleSheet("background:transparent;")
        blocks.addWidget(self._right_scroll, 2)
        root.addLayout(blocks, 1)

        self._right_cards = [self._build_image_card()]
        for title, hint in (
                ("曝光", "P2 · VIDEOIN_EXPOSURE 1306/1308"),
                ("背光", "P2 · VIDEOIN_BACKLIGHT 1309"),
                ("白平衡", "P2 · 结构已有，枚举待导出"),
                ("日夜", "P2 · CFG_VIDEOIN_DAYNIGHT_INFO"),
                ("补光", "P2 · VIDEOIN_LIGHTING_SCHEME"),
                ("除雾", "P2 · VIDEOIN_DEFOG 1312"),
                ("音频", "P2 · AUDIOIN_VOLUME 1202 + 编码"),
                ("聚焦信息", "P3 · 只读")):
            self._right_cards.append(self._build_stub_card(title, hint))
        self._reflow_right(self.width())

    # ---------- 预览卡 ----------

    def _build_preview_card(self) -> QWidget:
        card = CardWidget(self)
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(BodyLabel("预览画面", card))
        self._lb_pv_meta = CaptionLabel("辅码流 · 未连接", card)
        self._lb_pv_meta.setStyleSheet("color:#9aa1b5;")
        head.addWidget(self._lb_pv_meta)
        head.addStretch(1)
        v.addLayout(head)
        self._preview = _PreviewCanvas(card)
        self._preview.setMinimumHeight(150)
        v.addWidget(self._preview, 1)
        bar = QHBoxLayout()
        self._btn_pv_start = PrimaryPushButton(FluentIcon.PLAY, "开始预览", card)
        self._btn_pv_stop = PushButton(FluentIcon.PAUSE, "停止", card)
        self._btn_pv_stop.setEnabled(False)
        self._btn_pv_snap = PushButton(FluentIcon.CAMERA, "抓图存档", card)
        self._btn_pv_snap.setEnabled(False)
        for w in (self._btn_pv_start, self._btn_pv_stop, self._btn_pv_snap):
            bar.addWidget(w)
        bar.addStretch(1)
        v.addLayout(bar)
        self._btn_pv_start.clicked.connect(self._start_preview)
        self._btn_pv_stop.clicked.connect(self._stop_preview)
        self._btn_pv_snap.clicked.connect(self._snap)
        return card

    # ---------- 码流卡 ----------

    def _build_stream_card(self, parent_layout, title: str, fmt: int) -> dict:
        card = CardWidget(self)
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(BodyLabel(title, card))
        badge = CaptionLabel(f"1100 · {fmt}", card)
        badge.setStyleSheet("color:#4f8cff;font-size:11px;")
        badge.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        head.addWidget(badge)
        head.addStretch(1)
        v.addLayout(head)

        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(6)

        def lab(text):
            lb = CaptionLabel(text, card)
            lb.setStyleSheet("color:#9aa1b5;")
            lb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            return lb

        cmb_brc = ComboBox(card)
        for t, d in _BITRATE_CONTROLS:
            cmb_brc.addItem(t, userData=d)
        g.addWidget(lab("码流控制"), 0, 0)
        g.addWidget(cmb_brc, 0, 1)
        cmb_comp = ComboBox(card)
        for t, d in _COMPRESSIONS:
            cmb_comp.addItem(t, userData=d)
        g.addWidget(lab("编码模式"), 0, 2)
        g.addWidget(cmb_comp, 0, 3)

        sld_fps = QSlider(Qt.Horizontal, card)
        sld_fps.setRange(1, 50)
        lb_fps = CaptionLabel("25 fps", card)
        lb_fps.setMinimumWidth(52)
        sld_fps.valueChanged.connect(lambda val, lb=lb_fps: lb.setText(f"{val} fps"))
        g.addWidget(lab("帧率"), 1, 0)
        g.addWidget(sld_fps, 1, 1)
        g.addWidget(lb_fps, 1, 2)

        cmb_res = ComboBox(card)
        for name, w, h in _RESOLUTIONS:
            cmb_res.addItem(f"{name}（{w}×{h}）", userData=(w, h))
        cmb_res.addItem("自定义…", userData=_CUSTOM_RES)
        cust = QWidget(card)
        ch = QHBoxLayout(cust)
        ch.setContentsMargins(0, 0, 0, 0)
        ch.setSpacing(4)
        sp_w = SpinBox4(cust, 176, 4096)
        sp_h = SpinBox4(cust, 144, 4096)
        ch.addWidget(sp_w)
        ch.addWidget(QLabel("×", cust))
        ch.addWidget(sp_h)
        ch.addStretch(1)
        cust.hide()
        cmb_res.currentIndexChanged.connect(
            lambda _i, c=cust, cb=cmb_res:
            c.setVisible(cb.currentData() == _CUSTOM_RES))
        wrap = QWidget(card)
        wv = QVBoxLayout(wrap)
        wv.setContentsMargins(0, 0, 0, 0)
        wv.setSpacing(3)
        wv.addWidget(cmb_res)
        wv.addWidget(cust)
        g.addWidget(lab("分辨率"), 1, 3)
        g.addWidget(wrap, 1, 4)

        sld_q = QSlider(Qt.Horizontal, card)
        sld_q.setRange(1, 6)
        lb_q = CaptionLabel("80%", card)
        lb_q.setMinimumWidth(40)
        sld_q.valueChanged.connect(
            lambda val, lb=lb_q: lb.setText(_QUALITY_LABELS.get(val, str(val))))
        sld_q.setValue(5)
        g.addWidget(lab("图像质量"), 2, 0)
        g.addWidget(sld_q, 2, 1)
        g.addWidget(lb_q, 2, 2)

        cmb_bitrate = ComboBox(card)
        for b in _BITRATE_PRESETS:
            cmb_bitrate.addItem(f"{b} KB/s", userData=b)
        g.addWidget(lab("码率上限"), 2, 3)
        g.addWidget(cmb_bitrate, 2, 4)

        cmb_iframe = ComboBox(card)
        for iv in _IFRAME_PRESETS:
            cmb_iframe.addItem(str(iv), userData=iv)
        g.addWidget(lab("I 帧间隔"), 3, 0)
        g.addWidget(cmb_iframe, 3, 1)

        for c in range(5):
            g.setColumnStretch(c, 1 if c in (1, 3, 4) else 0)
        v.addLayout(g)

        btns = QHBoxLayout()
        btns.addStretch(1)
        btn_read = PushButton("读取", card)
        btn_apply = PrimaryPushButton("应用", card)
        btn_read.clicked.connect(self._on_read)
        btn_apply.clicked.connect(self._on_apply)
        btns.addWidget(btn_read)
        btns.addWidget(btn_apply)
        v.addLayout(btns)

        parent_layout.addWidget(card)
        return {"fmt": fmt, "cmb_brc": cmb_brc, "cmb_comp": cmb_comp,
                "sld_fps": sld_fps, "cmb_res": cmb_res, "sp_w": sp_w,
                "sp_h": sp_h, "sld_q": sld_q, "cmb_bitrate": cmb_bitrate,
                "cmb_iframe": cmb_iframe}

    # ---------- 图像属性卡 ----------

    def _build_image_card(self) -> QWidget:
        card = CardWidget(self)
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(BodyLabel("图像属性", card))
        badge = CaptionLabel("9601", card)
        badge.setStyleSheet("color:#4f8cff;font-size:11px;")
        badge.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        head.addWidget(badge)
        head.addStretch(1)
        v.addLayout(head)

        self._chk_mirror = CheckBox("镜像（左右）", card)
        self._chk_flip = CheckBox("翻转（上下 180°）", card)
        row = QHBoxLayout()
        row.addWidget(self._chk_mirror)
        row.addWidget(self._chk_flip)
        row.addStretch(1)
        v.addLayout(row)

        rrow = QHBoxLayout()
        rrow.addWidget(CaptionLabel("旋转", card))
        self._cmb_rotate = ComboBox(card)
        for t, d in _ROTATIONS:
            self._cmb_rotate.addItem(t, userData=d)
        rrow.addWidget(self._cmb_rotate)
        rrow.addStretch(1)
        v.addLayout(rrow)

        note = CaptionLabel("色彩/锐度/降噪待真机探针（P2）", card)
        note.setStyleSheet("color:#9aa1b5;font-size:11px;")
        note.setWordWrap(True)
        v.addWidget(note)

        btns = QHBoxLayout()
        btns.addStretch(1)
        btn_read = PushButton("读取", card)
        btn_apply = PrimaryPushButton("应用", card)
        btn_read.clicked.connect(self._on_read_image)
        btn_apply.clicked.connect(self._on_apply_image)
        btns.addWidget(btn_read)
        btns.addWidget(btn_apply)
        v.addLayout(btns)
        return card

    def _build_stub_card(self, title: str, hint: str) -> QWidget:
        card = CardWidget(self)
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(4)
        h = QHBoxLayout()
        h.addWidget(BodyLabel(title, card))
        badge = CaptionLabel(hint, card)
        badge.setStyleSheet("color:#e0a855;font-size:11px;")
        h.addWidget(badge)
        h.addStretch(1)
        v.addLayout(h)
        tip = CaptionLabel("规划中：真机探针通过后接入，当前不可用", card)
        tip.setStyleSheet("color:#9aa1b5;font-size:11px;")
        v.addWidget(tip)
        card.setEnabled(False)
        return card

    # ---------- 布局重排（2/3 列自适应） ----------

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._reflow_right(self.width())

    def _reflow_right(self, width: int):
        """宽屏（≥1900）右侧卡片拆两列 → 总三列；否则单列。"""
        cols = 2 if width >= _WIDE_THRESHOLD else 1
        if cols == self._right_cols:
            return
        self._right_cols = cols
        for card in self._right_cards:
            self._right_grid.removeWidget(card)
        for i, card in enumerate(self._right_cards):
            self._right_grid.addWidget(card, i // cols, i % cols)
        for c in range(2):
            self._right_grid.setColumnStretch(c, 1 if c < cols else 0)
        rows = (len(self._right_cards) + cols - 1) // cols
        for r in range(rows):
            self._right_grid.setRowStretch(r, 0)
        self._right_grid.setRowStretch(rows, 1)

    # ---------- 会话 ----------

    def _client(self):
        return self._cam._client if self._cam else None

    def _submit(self, fn, on_ok, on_err):
        if self._cam is not None:
            self._cam._serial.submit(fn, on_ok, on_err)
        else:
            on_err("相机工具页未初始化")

    # ---------- 预览 / 抓图 ----------

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        # 相机工具页 hideEvent 已停流；本页显示时接手同一登录会话起辅码流
        if self._client() and not self._preview.playing:
            self._start_preview()

    def hideEvent(self, event):  # noqa: N802
        super().hideEvent(event)
        if self._preview.playing:
            self._preview.set_playing(False)  # 先铺暗底，防切回闪旧帧
            self._stop_preview()

    def _start_preview(self):
        if not self._client():
            _toast(self, "请先在「相机工具」页连接设备", duration=3000)
            return
        self._preview.setAttribute(Qt.WA_DontCreateNativeAncestors, True)
        self._preview.setAttribute(Qt.WA_NativeWindow, True)
        hwnd = int(self._preview.winId())
        client = self._client()
        self._btn_pv_start.setEnabled(False)
        self._btn_pv_stop.setEnabled(True)

        def work():
            client.realplay_start(hwnd, channel=0, sub_stream=True)

        self._submit(
            work,
            lambda _r: (self._preview.set_playing(True),
                        self._lb_pv_meta.setText("辅码流 · 渲染中"),
                        self._btn_pv_snap.setEnabled(True)),
            lambda m: (self._btn_pv_start.setEnabled(True),
                       self._btn_pv_stop.setEnabled(False),
                       _toast(self, m, error=True, duration=3500)))

    def _stop_preview(self):
        if not self._client():
            return
        self._btn_pv_start.setEnabled(True)
        self._btn_pv_stop.setEnabled(False)
        self._submit(self._client().realplay_stop,
                     lambda _r: (self._preview.set_playing(False),
                                 self._lb_pv_meta.setText("辅码流 · 已停止")),
                     lambda _m: self._preview.set_playing(False))

    def _snap(self):
        if not self._client():
            return
        snap_dir = os.path.join(ROOT, "logs", "snap")
        os.makedirs(snap_dir, exist_ok=True)
        name = self._client().serial or "cam"
        path = os.path.join(
            snap_dir, f"dahua_{name}_{datetime.now():%Y%m%d_%H%M%S}.jpg")
        self._btn_pv_snap.setEnabled(False)
        client = self._client()

        def work():
            return client.snap_to_file(path)

        self._submit(work,
                     lambda p: (self._btn_pv_snap.setEnabled(True),
                                _toast(self, f"抓图成功：{p}")),
                     lambda m: (self._btn_pv_snap.setEnabled(True),
                                _toast(self, m, error=True, duration=3500)))

    # ---------- 码流读写 ----------

    def _on_read(self):
        c = self._client()
        if not c:
            _toast(self, "请先在「相机工具」页连接设备", duration=3000)
            return

        def work():
            return c.encode_video(1), c.encode_video(4)

        self._submit(work, self._fill_all, lambda m: _toast(self, m, True, 3500))

    def _fill_all(self, pair):
        main_info, extra_info = pair
        self._fill_card(self._cards[1], main_info)
        self._fill_card(self._cards[4], extra_info)
        chan = int(getattr(self._client(), "chan_num", 1) or 1)
        if self._cmb_ch.count() != chan:
            self._cmb_ch.clear()
            for i in range(1, chan + 1):
                self._cmb_ch.addItem(str(i))
        self._lb_state.setText("已读取（改完点各卡片「应用」）")
        self._lb_state.setStyleSheet("color:#70d49c;")

    def _fill_card(self, card: dict, info: dict):
        _select(card["cmb_brc"], info["bitrate_control"])
        _select(card["cmb_comp"], info["compression"])
        card["sld_fps"].setValue(int(round(info["framerate"])))
        w, h = int(info["width"]), int(info["height"])
        cmb = card["cmb_res"]
        custom_idx = cmb.count() - 1
        match = next((i for i in range(custom_idx)
                      if cmb.itemData(i) == (w, h)), None)
        if match is not None:
            cmb.setCurrentIndex(match)
            card["sp_w"].parentWidget().hide()
        else:
            cmb.setItemText(custom_idx, f"{w}×{h}（自定义）")
            cmb.setCurrentIndex(custom_idx)
            card["sp_w"].setValue(w)
            card["sp_h"].setValue(h)
            card["sp_w"].parentWidget().show()
        card["sld_q"].setValue(int(info["image_quality"]))
        _select_or_insert(card["cmb_bitrate"], info["bitrate"], " KB/s")
        _select_or_insert(card["cmb_iframe"], info["iframe_interval"], "")

    def _on_apply(self):
        c = self._client()
        if not c:
            _toast(self, "请先在「相机工具」页连接设备", duration=3000)
            return

        def card_params(card: dict) -> dict:
            w, h = _resolved_resolution(card)
            return dict(compression=card["cmb_comp"].currentData(),
                        width=w, height=h,
                        framerate=card["sld_fps"].value(),
                        bitrate_control=card["cmb_brc"].currentData(),
                        bitrate=card["cmb_bitrate"].currentData(),
                        iframe_interval=card["cmb_iframe"].currentData(),
                        image_quality=card["sld_q"].value())

        p_main = card_params(self._cards[1])
        p_extra = card_params(self._cards[4])

        def work():
            m = c.set_encode_video(1, **p_main)
            e = c.set_encode_video(4, **p_extra)
            return m, e

        self._submit(work, self._fill_all,
                     lambda m: _toast(self, m, True, 3500))

    # ---------- 图像属性读写 ----------

    def _on_read_image(self):
        c = self._client()
        if not c:
            _toast(self, "请先在「相机工具」页连接设备", duration=3000)
            return
        self._submit(c.image_ctrl, self._fill_image,
                     lambda m: _toast(self, m, True, 3500))

    def _fill_image(self, info: dict, quiet: bool = False):
        self._chk_mirror.setChecked(bool(info.get("mirror")))
        self._chk_flip.setChecked(bool(info.get("flip")))
        _select(self._cmb_rotate, int(info.get("rotate", 0)))
        self._chk_mirror.setEnabled(bool(info.get("supports_mirror", True)))
        self._chk_flip.setEnabled(bool(info.get("supports_flip", True)))
        if not quiet:
            _toast(self, "图像属性已读取", duration=1500)

    def _on_apply_image(self):
        c = self._client()
        if not c:
            _toast(self, "请先在「相机工具」页连接设备", duration=3000)
            return
        req = (self._chk_mirror.isChecked(), self._chk_flip.isChecked(),
               self._cmb_rotate.currentData())

        def work():
            return c.set_image_ctrl(mirror=req[0], flip=req[1], rotate=req[2])

        def done(info: dict):
            # 4238 固件实测 9601 写入不落库（读回仍是原值）——写后比对，
            # 未生效时明确提示，不假装成功
            applied = (bool(info.get("mirror")) == req[0]
                       and bool(info.get("flip")) == req[1]
                       and int(info.get("rotate", 0)) == int(req[2]))
            self._fill_image(info, quiet=True)
            if applied:
                _toast(self, "图像属性已应用", duration=1800)
            else:
                _toast(self, "该固件未接受图像属性写入（读回未生效）",
                       duration=4000)

        self._submit(work, done, lambda m: _toast(self, m, True, 3500))


def _resolved_resolution(card: dict):
    cmb = card["cmb_res"]
    data = cmb.currentData()
    if data == _CUSTOM_RES:
        return card["sp_w"].value(), card["sp_h"].value()
    return data


def _select(combo: ComboBox, data):
    for i in range(combo.count()):
        if combo.itemData(i) == data:
            combo.setCurrentIndex(i)
            return
    combo.setCurrentIndex(0)


def _select_or_insert(combo: ComboBox, value, suffix: str):
    for i in range(combo.count()):
        if combo.itemData(i) == value:
            combo.setCurrentIndex(i)
            return
    combo.addItem(f"{value}{suffix}", userData=value)
    combo.setCurrentIndex(combo.count() - 1)
