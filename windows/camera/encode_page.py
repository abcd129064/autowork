# -*- coding: utf-8 -*-
"""相机面板 ·「编码配置」子页 v2（2026-10-07 设计稿 encode_page_v2.html）

对齐 ConfigTool「编码配置」字段布局：通道 → 主码流卡 → 辅码流卡 → 读取/应用。
数据源 core/dahua_sdk.py::encode_video / set_encode_video（GetConfig/SetConfig
ENCODE_VIDEO=1100，RMW 只改暴露字段）。设备会话复用相机工具页的登录
（DahuaCameraWork._client + _SerialCaller，串行节流防隧道协议超时）。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QSlider,
                               QVBoxLayout, QWidget)
from qfluentwidgets import (BodyLabel, CardWidget, ComboBox, FluentIcon,
                            PrimaryPushButton, PushButton)

from core.utils import show_info_bar  # noqa: F401  （_toast 内部按需引用）

logger = logging.getLogger(__name__)

# 与 SDK 枚举对齐：CFG_VIDEO_COMPRESSION / EM_CFG_BITRATE_CONTROL
_COMPRESSIONS = [("H.264", 7), ("H.265", 8), ("MJPG", 5), ("MPEG4", 0)]
_BITRATE_CONTROLS = [("VBR 可变码率", 1), ("CBR 固定码率", 0)]
_RESOLUTIONS = [("1080P", 1920, 1080), ("720P", 1280, 720),
                ("D1", 704, 576), ("360P", 640, 360), ("CIF", 352, 288)]
_BITRATE_PRESETS = [512, 1024, 2048, 3072, 4096, 6144, 8192]
_IFRAME_PRESETS = [25, 50, 100]
_QUALITY_LABELS = {1: "10%", 2: "30%", 3: "50%", 4: "60%", 5: "80%", 6: "100%"}
_CUSTOM_RES = "__custom__"


def _toast(widget, msg, error=False, duration=3000):
    show_info_bar(msg, message_type="error" if error else "success",
                  duration=duration, parent=widget)


class EncodePage(QWidget):
    """编码配置：主/辅码流的分辨率、帧率、码率、码率类型、图像质量、I 帧间隔"""

    def __init__(self, win=None, cam_page=None, parent=None):
        super().__init__(parent)
        self._win = win
        self._cam = cam_page  # DahuaCameraWork：共享登录会话与串行执行器
        self.setObjectName("encodePage")
        self._cards: dict[int, dict] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(10)

        # ---- 通道 ----
        ch = QHBoxLayout()
        ch.addWidget(BodyLabel("通道", self))
        self._cmb_ch = ComboBox(self)
        self._cmb_ch.addItem("1")
        ch.addWidget(self._cmb_ch)
        self._lb_state = BodyLabel("未连接（请先在「相机工具」页连接设备）", self)
        self._lb_state.setStyleSheet("color:#9aa1b5;")
        ch.addSpacing(12)
        ch.addWidget(self._lb_state)
        ch.addStretch(1)
        root.addLayout(ch)

        # ---- 主 / 辅码流卡片 ----
        self._cards[1] = self._build_stream_card("主码流", 1)
        self._cards[4] = self._build_stream_card("辅码流1", 4)

        # ---- 底部按钮 ----
        foot = QHBoxLayout()
        foot.addStretch(1)
        self._btn_read = PushButton(FluentIcon.DOWNLOAD, "读取", self)
        self._btn_apply = PrimaryPushButton(FluentIcon.SAVE, "应用到设备", self)
        foot.addWidget(self._btn_read)
        foot.addWidget(self._btn_apply)
        root.addLayout(foot)
        root.addStretch(1)

        self._btn_read.clicked.connect(self._on_read)
        self._btn_apply.clicked.connect(self._on_apply)

    # ---------- 卡片构建 ----------

    def _build_stream_card(self, title: str, fmt: int) -> dict:
        card = CardWidget(self)
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(BodyLabel(title, card))
        badge = BodyLabel(f"GetConfig/SetConfig(1100) · emFormatType={fmt}", card)
        badge.setStyleSheet("color:#4f8cff;font-size:11px;")
        head.addWidget(badge)
        head.addStretch(1)
        v.addLayout(head)

        g = QGridLayout()
        g.setHorizontalSpacing(20)
        g.setVerticalSpacing(8)

        def lab(text):
            lb = BodyLabel(text, card)
            lb.setStyleSheet("color:#9aa1b5;")
            lb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            return lb

        # 码流控制
        cmb_brc = ComboBox(card)
        for t, d in _BITRATE_CONTROLS:
            cmb_brc.addItem(t, userData=d)
        g.addWidget(lab("码流控制"), 0, 0)
        g.addWidget(cmb_brc, 0, 1)

        # 编码模式
        cmb_comp = ComboBox(card)
        for t, d in _COMPRESSIONS:
            cmb_comp.addItem(t, userData=d)
        g.addWidget(lab("编码模式"), 0, 2)
        g.addWidget(cmb_comp, 0, 3)

        # 帧率滑条
        sld_fps = QSlider(Qt.Horizontal, card)
        sld_fps.setRange(1, 50)
        lb_fps = BodyLabel("25 fps", card)
        lb_fps.setMinimumWidth(56)
        sld_fps.valueChanged.connect(lambda v, lb=lb_fps: lb.setText(f"{v} fps"))
        g.addWidget(lab("帧率"), 1, 0)
        g.addWidget(sld_fps, 1, 1)
        g.addWidget(lb_fps, 1, 2)

        # 分辨率（预设 + 自定义宽高）
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
            lambda _i, c=cust, cb=cmb_res: c.setVisible(cb.currentData() == _CUSTOM_RES))
        wrap = QWidget(card)
        wv = QVBoxLayout(wrap)
        wv.setContentsMargins(0, 0, 0, 0)
        wv.setSpacing(4)
        wv.addWidget(cmb_res)
        wv.addWidget(cust)
        g.addWidget(lab("分辨率"), 1, 2)
        g.addWidget(wrap, 1, 3)

        # 图像质量滑条（1-6 档）
        sld_q = QSlider(Qt.Horizontal, card)
        sld_q.setRange(1, 6)
        lb_q = BodyLabel("80%", card)
        lb_q.setMinimumWidth(44)
        sld_q.valueChanged.connect(
            lambda val, lb=lb_q: lb.setText(_QUALITY_LABELS.get(val, str(val))))
        sld_q.setValue(5)
        g.addWidget(lab("图像质量"), 2, 0)
        g.addWidget(sld_q, 2, 1)
        g.addWidget(lb_q, 2, 2)

        # 码率上限
        cmb_bitrate = ComboBox(card)
        for b in _BITRATE_PRESETS:
            cmb_bitrate.addItem(f"{b} KB/s", userData=b)
        g.addWidget(lab("码率上限"), 2, 3)
        g.addWidget(cmb_bitrate, 2, 4)

        # I 帧间隔
        cmb_iframe = ComboBox(card)
        for iv in _IFRAME_PRESETS:
            cmb_iframe.addItem(str(iv), userData=iv)
        g.addWidget(lab("I 帧间隔"), 3, 0)
        g.addWidget(cmb_iframe, 3, 1)

        for c in range(5):
            g.setColumnStretch(c, 1 if c in (1, 3) else 0)
        v.addLayout(g)
        self.layout().addWidget(card)

        return {"fmt": fmt, "cmb_brc": cmb_brc, "cmb_comp": cmb_comp,
                "sld_fps": sld_fps, "cmb_res": cmb_res, "sp_w": sp_w,
                "sp_h": sp_h, "sld_q": sld_q, "cmb_bitrate": cmb_bitrate,
                "cmb_iframe": cmb_iframe}

    # ---------- 会话 ----------

    def _client(self):
        return self._cam._client if self._cam else None

    def _submit(self, fn, on_ok, on_err):
        if self._cam is not None:
            self._cam._serial.submit(fn, on_ok, on_err)
        else:
            on_err("相机工具页未初始化")

    # ---------- 读取 / 应用 ----------

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
        # 通道号回填（登录回包通道数）
        chan = int(getattr(self._client(), "chan_num", 1) or 1)
        if self._cmb_ch.count() != chan:
            self._cmb_ch.clear()
            for i in range(1, chan + 1):
                self._cmb_ch.addItem(str(i))
        self._lb_state.setText("已读取（应用前可修改卡片内参数）")
        self._lb_state.setStyleSheet("color:#70d49c;")

    def _fill_card(self, card: dict, info: dict):
        _select(card["cmb_brc"], info["bitrate_control"])
        _select(card["cmb_comp"], info["compression"])
        card["sld_fps"].setValue(int(round(info["framerate"])))
        w, h = int(info["width"]), int(info["height"])
        cmb = card["cmb_res"]
        custom_idx = cmb.count() - 1  # 尾项固定为"自定义…"
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


class SpinBox4(QWidget):
    """宽/高 小输入组（带范围）——占位用 qfw SpinBox 包一层保持风格一致"""

    def __init__(self, parent, lo, hi):
        super().__init__(parent)
        from qfluentwidgets import SpinBox
        v = QHBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.sp = SpinBox(self)
        self.sp.setRange(lo, hi)
        self.sp.setMinimumWidth(72)
        v.addWidget(self.sp)

    def setValue(self, val):
        self.sp.setValue(int(val))

    def value(self):
        return self.sp.value()


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
