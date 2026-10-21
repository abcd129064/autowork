# -*- coding: utf-8 -*-
"""相机面板 ·「编码设置」页（设计基准：design/encode_settings_v6_three.html）。

三区块（与设计稿逐块对应）：
- 顶栏：通道 + 说明 + 读取全部；
- 左列：上=预览卡（固定），下=主码流 / 辅码流1（区块内滚动）；
- 右列：图像属性(P1) + 曝光/背光/白平衡/日夜模式/补光/除雾/音频(P2) +
  聚焦信息(P3)，独立滚动；宽屏（≥1300 / ≥1850）右列自动分 2 / 3 列。

字段口径：设计稿里出现、但 core/dahua_sdk 尚未开放读写的字段一律建成禁用占位
控件（tooltip 写明缺口在哪），不虚构设备能力；已接线的组保留「读取 / 应用」，
并做写后读回校验。数据源：ENCODE_VIDEO=1100、CFG_VIDEO_IMAGE_CTRL=9601、
1306 曝光、1309 背光、1202+1203 音量、VideoInOptions、VideoColor、Lighting。
设备调用复用「相机工具」页的登录会话与 _SerialCaller 串行队列。
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
                            ComboBox, PrimaryPushButton, PushButton, SpinBox)

from core.utils import show_info_bar
from windows.camera.camera_page import (ROOT, _PreviewCanvas,
                                        _PreviewFullScreen)

logger = logging.getLogger(__name__)

# ---- 设计稿配色（HTML :root 变量）----
_C_SUB = "#9aa1b5"
_C_ACCENT = "#4f8cff"
_C_OK = "#70d49c"
_C_WARN = "#e0a855"
_C_LINE = "#3a4056"

_TOP_NOTE = "全部参数按通道生效；能力位不支持的控件置灰"
_TIP_NA = "设备侧未开放：core/dahua_sdk 暂无该字段的读写接口，按设计稿置灰占位"

# ---- 码流（ENCODE_VIDEO 1100）----
_COMPRESSIONS_MAIN = [("H.264", 7), ("H.265", 8), ("MJPG", 5)]
_COMPRESSIONS_EXTRA = [("H.264", 7), ("H.265", 8)]
_COMPRESSION_NAMES = {7: "H.264", 8: "H.265", 5: "MJPG", 0: "MPEG4"}
_BITRATE_CONTROLS = [("VBR 可变码流", 1), ("CBR 固定码流", 0)]
_BRC_NAMES = {1: "VBR", 0: "CBR"}
_RES_MAIN = [("1080P（1920×1080）", 1920, 1080),
             ("720P（1280×720）", 1280, 720), ("D1（704×576）", 704, 576)]
_RES_EXTRA = [("360P（640×360）", 640, 360),
              ("720P（1280×720）", 1280, 720), ("D1（704×576）", 704, 576)]
_BITRATE_MAIN = [4096, 6144, 8192]
_BITRATE_EXTRA = [512, 1024]
_IFRAME_MAIN = [50, 25, 100]
_IFRAME_EXTRA = [50]
_QUALITY_LABELS = {1: "10%", 2: "30%", 3: "50%", 4: "60%", 5: "80%", 6: "100%"}
_CUSTOM_RES = "__custom__"

# ---- 图像属性（9601）：镜像/翻转/旋转已接线，防抖与色彩见各自注释 ----
_ROTATIONS = [("不旋转", 0), ("顺时针 90°", 1), ("逆时针 90°", 2)]
_COLOR_FIELDS = (("brightness", "亮度"), ("contrast", "对比度"),
                 ("saturation", "饱和度"), ("gamma", "Gamma"))
_TIP_STABLE = ("SDK 9601 的 nStable（0 关闭 / 1 电子 / 2 光学）与 "
               "nStableSensitivity 尚未在 core 开放，待真机验证后接线")
_TIP_SHARP = "VideoInOptions 的锐度 / 3D / 2D 降噪字段尚未导出，置灰占位"

# ---- P2 组：曝光 1306 / 背光 1309 / 白平衡 / 日夜 / 补光 / 除雾 1312 ----
# 曝光模式按 SDK 枚举 NET_EM_EXPOSURE_MODE 的真实档位列出：设计稿写的「手动」
# 在 SDK 里分手档区间(4)/手动固定(6)，笼统标「手动」会把别的档位误当手动下发
_EXPOSURE_MODES = [("自动", 0), ("手动区间", 4), ("手动固定", 6),
                   ("光圈优先", 5), ("快门优先", 8), ("增益优先", 7),
                   ("低噪声", 1), ("防拖影", 2), ("闪光灯匹配", 9)]
# nAntiFlicker：SDK 注释为 0-Outdoor / 1-50Hz / 2-60Hz，设计稿把 0 写作「关闭」
_ANTIFLICKERS = [("50Hz", 1), ("60Hz", 2), ("关闭", 0)]
_TIP_ANTIFLICKER = "SDK nAntiFlicker：0=Outdoor（户外，即不做防闪烁）/ 1=50Hz / 2=60Hz"
_TIP_SHUTTER = "曝光结构体的快门档（dbExposureValue1/2）未在 core 开放"
_BACK_MODES = [("关闭", 1), ("背光补偿", 2), ("宽动态", 3), ("强光抑制", 4)]
_TIP_BLC_REGION = "1309 的 stuBacklightRegion（NET_RECT）未在 core 开放"
_WB_MODES = [("自动", "Auto"), ("手动", "Manual"),
             ("室内", "Indoor"), ("室外", "Outdoor")]
_TIP_WB_GAIN = "VideoInOptions 未导出红 / 蓝增益字段（设计稿标注：枚举待导出）"
_DAYNIGHT_MODES = [("自动", 0), ("彩色", 1), ("黑白", 2)]
_TIP_DAYNIGHT_TYPE = "DAYNIGHT_INFO 的电子 / 机械类型未在 core 开放"
# Lighting.Mode 现有实现按 Auto/Off/Manual 下发；设计稿的白光/红外/智能属灯光
# 类型，枚举串未经真机探针确认，不凭猜测写进可选项（设备回其它值时按原值插入显示）
_LIGHT_MODES = [("自动", "Auto"), ("关闭", "Off"), ("手动", "Manual")]
_TIP_LIGHT_MODE = "Lighting.Mode 现按 Auto / Off / Manual 下发；白光 / 红外 / 智能待真机探针"
_TIP_LIGHT_SENS = "Lighting.Sensitivity 未在 core 开放"
_TIP_DEFOG = "1312 NET_VIDEOIN_DEFOG_INFO（关闭 1 / 自动 2 / 手动 3，强度 0-100）：该固件读取失败，core 未接入"
_TIP_AUDIO_ENC = "音频使能 / 降噪 / 编码 / 采样率属 ENCODE_AUDIO 与音频结构体，core 未开放"

_COLS_2_MIN = 1300   # ≥1300：右侧卡片两列
_COLS_3_MIN = 1850   # ≥1850：右侧卡片三列


def _toast(widget, msg, error=False, duration=3000):
    show_info_bar(msg, message_type="error" if error else "success",
                  duration=duration, parent=widget)


def _opts(*texts):
    """占位下拉的选项表：文本即数据，禁用态永不回写设备"""
    return [(t, t) for t in texts]


def _badge(parent, text: str, kind: str = "p1") -> CaptionLabel:
    """设计稿 .badge 胶囊徽标：kind=p1 蓝 / p2 橙 / p3 灰"""
    color = {"p1": _C_ACCENT, "p2": _C_WARN, "p3": _C_SUB}[kind]
    border = _C_LINE if kind == "p3" else color
    lb = CaptionLabel(text, parent)
    lb.setStyleSheet(f"color:{color};border:1px solid {border};"
                     f"border-radius:9px;padding:0 9px;font-size:11px;")
    # Ignored：徽标文字较长（ENCODE_VIDEO 1100 · emFormatType=1），若按
    # minimumSizeHint 参与布局会把卡片最小宽顶到左列放不下
    lb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    return lb


def _sep(text: str, parent) -> QWidget:
    """设计稿 .sep：两侧细线夹一行小字，分隔「固定区」与「滚动区」"""
    box = QWidget(parent)
    h = QHBoxLayout(box)
    h.setContentsMargins(0, 2, 0, 2)
    h.setSpacing(8)

    def _line():
        w = QWidget(box)
        w.setAttribute(Qt.WA_StyledBackground, True)  # 纯 QWidget 不设则背景不绘
        w.setStyleSheet(f"background:{_C_LINE};")
        w.setFixedHeight(1)
        return w

    lb = CaptionLabel(text, box)
    lb.setStyleSheet(f"color:{_C_SUB};font-size:11px;")
    h.addWidget(_line(), 1)
    h.addWidget(lb)
    h.addWidget(_line(), 1)
    return box


def _subhead(text: str, parent) -> CaptionLabel:
    """设计稿 .subhead：卡片内子分组标题（虚线上边框）"""
    lb = CaptionLabel(text, parent)
    lb.setStyleSheet(f"color:{_C_SUB};font-size:11.5px;"
                     f"border-top:1px dashed {_C_LINE};padding-top:6px;")
    return lb


def _lab(text: str, parent) -> CaptionLabel:
    """设计稿 .lab：右对齐灰色字段名"""
    lb = CaptionLabel(text, parent)
    lb.setStyleSheet(f"color:{_C_SUB};")
    lb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return lb


def _combo(parent, options, min_w: int = 92) -> ComboBox:
    """可压缩下拉。qfw 下拉的 minimumSizeHint 很宽，不设 Ignored 会把卡片最小宽
    顶到 700+，超出左列 520 后内容被裁（10-21 截图回归）"""
    cmb = ComboBox(parent)
    for text, data in options:
        cmb.addItem(text, userData=data)
    cmb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    cmb.setMinimumWidth(min_w)
    return cmb


def _check(parent, text: str, min_w: int = 88) -> CheckBox:
    """可压缩勾选框：同 _combo，避免长文本把卡片最小宽撑爆"""
    cb = CheckBox(text, parent)
    cb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    cb.setMinimumWidth(min_w)
    return cb


def _slider_cell(parent, lo, hi, val, fmt=None):
    """设计稿 .slider（track + .val）。返回 (行容器, 滑块, 数值标签)"""
    row = QWidget(parent)
    hl = QHBoxLayout(row)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(6)
    sld = QSlider(Qt.Horizontal, row)
    sld.setRange(int(lo), int(hi))
    sld.setMinimumWidth(70)
    show = fmt or str
    lb = CaptionLabel(show(int(val)), row)
    lb.setMinimumWidth(36)
    sld.valueChanged.connect(lambda x, l=lb, f=show: l.setText(f(x)))
    sld.setValue(int(val))
    hl.addWidget(sld, 1)
    hl.addWidget(lb)
    return row, sld, lb


def _ph(parent, w, tip: str = _TIP_NA):
    """禁用占位：设计稿里出现但后端未开放的字段，置灰 + tooltip 说明缺口"""
    w.setEnabled(False)
    w.setToolTip(tip)
    return w


def _ph_combo(parent, texts, tip: str = _TIP_NA) -> ComboBox:
    return _ph(parent, _combo(parent, _opts(*texts)), tip)


def _ph_check(parent, text: str, tip: str = _TIP_NA,
              min_w: int = 88) -> CheckBox:
    return _ph(parent, _check(parent, text, min_w), tip)


def _ph_checks(parent, texts, tip: str = _TIP_NA) -> QWidget:
    """同一格里并排多个禁用勾选框（设计稿「类型：电子 / 机械」）。
    两个框共一格，最小宽必须比单框更小，否则这一格会把卡片顶宽"""
    row = QWidget(parent)
    hl = QHBoxLayout(row)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(8)
    for t in texts:
        hl.addWidget(_ph_check(parent, t, tip, min_w=58))
    hl.addStretch(1)
    return _ph(parent, row, tip)


def _ph_slider(parent, lo, hi, val, tip: str = _TIP_NA) -> QWidget:
    row, _s, _l = _slider_cell(parent, lo, hi, val)
    return _ph(parent, row, tip)


def _fill_grid(grid: QGridLayout, parent, cells):
    """按设计稿 .grid2（auto 1fr auto 1fr）两两一行铺「字段名 + 控件」"""
    for i, (label, ctrl) in enumerate(cells):
        r, c = divmod(i, 2)
        grid.addWidget(_lab(label, parent), r, c * 2)
        grid.addWidget(ctrl, r, c * 2 + 1)
    for c in (1, 3):
        grid.setColumnStretch(c, 1)


def _make_scroll(parent) -> QScrollArea:
    """透明视口滚动区。视口默认不透明会把卡片衬成实心黑并挡掉窗口 Mica，
    setWidget() 之后必须补视口样式（本项目统一做法，见 main_window/hub_pages.py）"""
    sc = QScrollArea(parent)
    sc.setWidgetResizable(True)
    sc.setFrameShape(QFrame.NoFrame)
    sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    sc.viewport().setAutoFillBackground(False)
    sc.setStyleSheet("QScrollArea{border:none;background:transparent;}")
    sc.viewport().setStyleSheet("background:transparent;")
    return sc


class SpinBox4(QWidget):
    """宽 / 高 小输入组（带范围）"""

    def __init__(self, parent, lo, hi, width: int = 64):
        super().__init__(parent)
        v = QHBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.sp = SpinBox(self)
        self.sp.setRange(lo, hi)
        self.sp.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.sp.setFixedWidth(width)
        v.addWidget(self.sp)

    def setValue(self, val):
        self.sp.setValue(int(val))

    def value(self):
        return self.sp.value()


class EncodePage(QWidget):
    """编码设置：预览 + 主 / 辅码流 + 图像属性 + P2 / P3 卡片"""

    def __init__(self, win=None, cam_page=None, parent=None):
        super().__init__(parent)
        self._win = win
        self._cam = cam_page    # DahuaCameraWork：共享登录会话与串行执行器
        self._cards: dict[int, dict] = {}
        self._right_cols = 0
        self._fs_win = None
        self._fs_was_playing = False
        # 色彩只有成功读回过才允许回写：否则滑块还是默认值，一把写下去
        # 会把亮度/对比度清成 0（画面全黑）
        self._color_loaded = False
        self.setObjectName("encodePage")
        # 卡片最小宽必须足够小：否则宽窗口切成多列后页面最小宽被撑大，
        # 窗口缩回时页面缩不下去 → 列数锁死 + 左列被挤出裁切（10-21 截图）
        self.setMinimumWidth(0)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 8)
        root.setSpacing(6)

        # ---- 顶栏：通道 + 说明 + 读取全部 ----
        top = QHBoxLayout()
        top.addWidget(BodyLabel("通道", self))
        self._cmb_ch = ComboBox(self)
        self._cmb_ch.addItem("1")
        self._cmb_ch.setFixedWidth(70)
        top.addWidget(self._cmb_ch)
        top.addSpacing(8)
        self._lb_state = CaptionLabel(_TOP_NOTE, self)
        self._lb_state.setStyleSheet(f"color:{_C_SUB};")
        top.addWidget(self._lb_state)
        top.addStretch(1)
        self._btn_read = PushButton("读取全部", self)
        self._btn_read.clicked.connect(lambda _=False: self._on_read())
        top.addWidget(self._btn_read)
        root.addLayout(top)

        # ---- 两列主体 ----
        blocks = QHBoxLayout()
        blocks.setSpacing(10)

        # 左列：上预览（固定）/ 下码流（滚动）
        # 设计稿左列约 480~550px：套容器限最大宽，避免被窗口拉伸后卡片过宽；
        # 最小宽留 470 保证窄窗口下主码流卡（四列网格）不被裁
        left_box = QWidget(self)
        left_box.setMaximumWidth(520)
        left_box.setMinimumWidth(470)
        self._left_box = left_box
        left = QVBoxLayout(left_box)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(6)
        left.addWidget(self._build_preview_card())
        left.addWidget(_sep("左下 · 常用码流参数（固定）", left_box))
        self._stream_scroll = _make_scroll(self)
        host = QWidget(self._stream_scroll)
        sv = QVBoxLayout(host)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(8)
        self._cards[1] = self._build_stream_card(sv, "主码流", 1)
        self._cards[4] = self._build_stream_card(sv, "辅码流1", 4)
        sv.addStretch(1)
        self._stream_scroll.setWidget(host)
        left.addWidget(self._stream_scroll, 1)
        blocks.addWidget(left_box, 0)

        # 右列：图像属性 + P2 / P3 卡片（独立滚动，宽屏拆 2 / 3 列）
        right_box = QWidget(self)
        rv = QVBoxLayout(right_box)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(6)
        rv.addWidget(_sep("右侧 · 其他卡片（独立滚动）", right_box))
        self._right_scroll = _make_scroll(self)
        self._right_host = QWidget(self._right_scroll)
        self._right_grid = QGridLayout(self._right_host)
        self._right_grid.setContentsMargins(0, 0, 0, 0)
        self._right_grid.setHorizontalSpacing(8)
        self._right_grid.setVerticalSpacing(8)
        self._right_scroll.setWidget(self._right_host)
        rv.addWidget(self._right_scroll, 1)
        blocks.addWidget(right_box, 1)
        root.addLayout(blocks, 1)

        # ---- 右列卡片（顺序与设计稿一致）----
        self._grp_exposure = self._build_group_card(
            "曝光", "P2 · 1306/1308", [
                ("combo", "曝光模式", "mode", _EXPOSURE_MODES),
                ("combo", "抗闪", "antiflicker", _ANTIFLICKERS),
                ("slider", "曝光补偿", "compensation", 0, 100),
                ("combo", "快门", None,
                 _opts("1/25", "1/100", "1/10000"), _TIP_SHUTTER),
                ("slider", "增益", "gain", 0, 100)])
        self._grp_backlight = self._build_group_card(
            "背光", "P2 · 1309", [
                ("combo", "背光模式", "mode", _BACK_MODES),
                ("slider", "宽动态等级", "wide_dynamic", 0, 100),
                ("combo", "BLC 区域", None,
                 _opts("全屏", "上", "中", "下"), _TIP_BLC_REGION)])
        self._grp_white = self._build_group_card(
            "白平衡", "P2 · 枚举待导出", [
                ("combo", "模式", "white_balance", _WB_MODES),
                ("slider", "红色增益", None, 0, 100, _TIP_WB_GAIN),
                ("slider", "蓝色增益", None, 0, 100, _TIP_WB_GAIN)])
        self._grp_daynight = self._build_group_card(
            "日夜模式", "P2 · DAYNIGHT_INFO", [
                ("checks", "类型", ("电子", "机械"), _TIP_DAYNIGHT_TYPE),
                ("combo", "切换模式", "day_night", _DAYNIGHT_MODES),
                ("slider", "灵敏度", "daynight_sensitivity", 0, 100),
                ("combo", "延时", "daynight_delay",
                 [("0 s", 0), ("2 s", 2), ("5 s", 5)])])
        self._grp_light = self._build_group_card(
            "补光", "P2 · LIGHTING_SCHEME", [
                ("combo", "灯光模式", "mode", _LIGHT_MODES),
                ("slider", "补光强度", "correction", 0, 100),
                ("combo", "灵敏度", None,
                 _opts("0", "1", "3", "5"), _TIP_LIGHT_SENS)])
        self._grp_defog = self._build_group_card(
            "除雾", "P2 · 1312", [
                ("combo", "除雾模式", None,
                 _opts("关闭", "自动", "手动"), _TIP_DEFOG),
                ("slider", "强度", None, 0, 100, _TIP_DEFOG)], wired=False)
        self._grp_audio = self._build_group_card(
            "音频", "P2 · 1202 + 编码", [
                ("check", "音频使能", None, "开启", _TIP_AUDIO_ENC),
                ("check", "降噪", None, "开启", _TIP_AUDIO_ENC),
                ("slider", "输入音量", "in", 0, 100),
                ("slider", "输出音量", "out", 0, 100),
                ("combo", "音频编码", None,
                 _opts("AAC", "G.711A"), _TIP_AUDIO_ENC),
                ("combo", "采样率", None,
                 _opts("8000", "16000"), _TIP_AUDIO_ENC)])
        self._right_cards = [self._build_image_card(),
                             self._grp_exposure["card"],
                             self._grp_backlight["card"],
                             self._grp_white["card"],
                             self._grp_daynight["card"],
                             self._grp_light["card"],
                             self._grp_defog["card"],
                             self._grp_audio["card"],
                             self._build_focus_card()]
        # 已接线控件的悬停提示补上枚举语义；整卡不可用的除雾把缺口写在卡上
        self._grp_exposure["widgets"]["antiflicker"].setToolTip(_TIP_ANTIFLICKER)
        self._grp_light["widgets"]["mode"].setToolTip(_TIP_LIGHT_MODE)
        self._grp_defog["card"].setToolTip(_TIP_DEFOG)

        self._wire_group(self._grp_exposure,
                         lambda c: c.exposure(),
                         lambda c, v: c.set_exposure(**v))
        self._wire_group(self._grp_backlight,
                         lambda c: c.backlight(),
                         lambda c, v: c.set_backlight(**v))
        self._wire_group(self._grp_white,
                         lambda c: c.video_options(),
                         lambda c, v: c.set_video_options(
                             white_balance=v.get("white_balance")))
        self._wire_group(self._grp_daynight,
                         lambda c: c.video_options(),
                         lambda c, v: c.set_video_options(
                             day_night=v.get("day_night"),
                             daynight_sensitivity=v.get("daynight_sensitivity"),
                             daynight_delay=v.get("daynight_delay")))
        self._wire_group(self._grp_light,
                         lambda c: c.lighting(),
                         lambda c, v: c.set_lighting(**v))
        self._wire_group(self._grp_audio,
                         lambda c: c.audio_volume(),
                         lambda c, v: c.set_audio_volume(vol_in=v.get("in"),
                                                         vol_out=v.get("out")))
        self._reflow_right(self.width())

    # ---------- 预览卡 ----------

    def _build_preview_card(self) -> QWidget:
        """设计稿：标题 + 两枚徽标，画布(flex1.5) 与侧栏(flex1) 并排，
        侧栏内是「码流 / 帧率 / 编码 / 状态」四行实时信息 + 底部四个按钮"""
        card = CardWidget(self)
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(BodyLabel("预览画面", card))
        head.addWidget(_badge(card, "辅码流 · RealPlayEx"))
        head.addWidget(_badge(card, "共享登录会话"))
        head.addStretch(1)
        v.addLayout(head)

        wrap = QHBoxLayout()
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.setSpacing(10)
        self._pv_wrap = wrap        # 全屏时借出画布，退出时按索引还原
        self._preview = _PreviewCanvas(card)
        self._preview.setMinimumSize(200, 200)
        wrap.addWidget(self._preview, 3)

        side = QWidget(card)
        side.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        side.setMinimumWidth(150)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(4)
        info = QGridLayout()
        info.setHorizontalSpacing(8)
        info.setVerticalSpacing(4)
        self._lb_pv_stream = _pv_value(card, "辅码流1 · 未读取")
        self._lb_pv_fps = _pv_value(card, "-")
        self._lb_pv_codec = _pv_value(card, "-")
        self._lb_pv_state = _pv_value(card, "● 未连接")
        for r, (name, val) in enumerate((
                ("码流", self._lb_pv_stream), ("帧率", self._lb_pv_fps),
                ("编码", self._lb_pv_codec), ("状态", self._lb_pv_state))):
            info.addWidget(_lab(name, card), r, 0)
            info.addWidget(val, r, 1)
        info.setColumnStretch(1, 1)
        sv.addLayout(info)
        sv.addStretch(1)            # 设计稿 .pvbar{margin-top:auto}
        bar = QGridLayout()
        bar.setHorizontalSpacing(6)
        bar.setVerticalSpacing(6)
        self._btn_pv_play = PrimaryPushButton("开始预览", side)
        self._btn_pv_stop = PushButton("停止", side)
        self._btn_pv_snap = PushButton("抓图存档", side)
        self._btn_pv_fs = PushButton("全屏", side)
        self._btn_pv_stop.setEnabled(False)
        for i, b in enumerate((self._btn_pv_play, self._btn_pv_stop,
                               self._btn_pv_snap, self._btn_pv_fs)):
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            b.setMinimumWidth(76)
            bar.addWidget(b, i // 2, i % 2)
        for c in (0, 1):
            bar.setColumnStretch(c, 1)
        sv.addLayout(bar)
        wrap.addWidget(side, 2)
        v.addLayout(wrap)

        # 画布是 SDK 直渲的原生 HWND，上面盖不住 Qt 子控件，说明文字只能放画布外
        self._lb_pv_hint = CaptionLabel(
            "预览画布（原生 HWND 直渲）· 切页停流 · 切回延迟 1s 恢复", card)
        self._lb_pv_hint.setStyleSheet(f"color:{_C_SUB};font-size:11px;")
        self._lb_pv_hint.setAlignment(Qt.AlignCenter)
        self._lb_pv_hint.setWordWrap(True)
        v.addWidget(self._lb_pv_hint)

        self._btn_pv_play.clicked.connect(lambda _=False: self._start_preview())
        self._btn_pv_stop.clicked.connect(lambda _=False: self._stop_preview())
        self._btn_pv_snap.clicked.connect(self._on_snap)
        self._btn_pv_fs.clicked.connect(self._on_fullscreen)
        return card

    # ---------- 码流卡 ----------

    def _build_stream_card(self, parent_layout, title: str, fmt: int) -> dict:
        """主码流(emFormatType=1) / 辅码流1(emFormatType=4)。
        两张卡字段集不同（设计稿）：主码流多出码流类型 / 编码策略 / 音频三项，
        且只有主码流带「自定义…」分辨率手输。"""
        main = fmt == 1
        card = CardWidget(self)
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(BodyLabel(title, card))
        head.addWidget(_badge(card, f"ENCODE_VIDEO 1100 · emFormatType={fmt}"
                              if main else f"emFormatType={fmt}"))
        head.addWidget(_badge(card, "P1"))
        head.addStretch(1)
        v.addLayout(head)

        cmb_brc = _combo(card, _BITRATE_CONTROLS)
        cmb_comp = _combo(card, _COMPRESSIONS_MAIN if main
                          else _COMPRESSIONS_EXTRA)
        fps_cell, sld_fps, _fps_lb = _slider_cell(card, 1, 50, 25)
        q_cell, sld_q, _q_lb = _slider_cell(
            card, 1, 6, 5, fmt=lambda x: _QUALITY_LABELS.get(x, str(x)))
        cmb_bitrate = _combo(card, [(f"{b} KB/s", b) for b in
                                    (_BITRATE_MAIN if main else _BITRATE_EXTRA)])
        cmb_iframe = _combo(card, [(str(i), i) for i in
                                   (_IFRAME_MAIN if main else _IFRAME_EXTRA)], 76)
        cmb_res = _combo(card, [(n, (w, h)) for n, w, h in
                                (_RES_MAIN if main else _RES_EXTRA)], 118)
        cmb_res.setToolTip("分辨率预设：1080P=1920×1080 / 720P=1280×720 / "
                           "D1=704×576 / 360P=640×360")
        cust = sp_w = sp_h = None
        res_cell = cmb_res
        if main:
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
            res_cell = QWidget(card)
            rw = QVBoxLayout(res_cell)
            rw.setContentsMargins(0, 0, 0, 0)
            rw.setSpacing(3)
            rw.addWidget(cmb_res)
            rw.addWidget(cust)

        # 字段顺序严格按设计稿 .grid2 的 auto-flow（每行两个）：
        # 码流类型|编码模式 / 编码策略|音频使能 / 码流控制|音频编码 /
        # 帧率|采样率 / 分辨率|I 帧间隔 / 图像质量|码率上限
        if main:
            cells = [
                ("码流类型", _ph_combo(card, ("普通",),
                                       "ENCODE_VIDEO 的 emStreamType 未在 core 开放，固定为普通")),
                ("编码模式", cmb_comp),
                ("编码策略", _ph_combo(card, ("普通",),
                                       "ENCODE_VIDEO 的编码策略字段未在 core 开放，固定为普通")),
                ("音频使能", _ph_check(card, "启用", _TIP_AUDIO_ENC)),
                ("码流控制", cmb_brc),
                ("音频编码", _ph_combo(card, ("AAC", "G.711A"), _TIP_AUDIO_ENC)),
                ("帧率", fps_cell),
                ("采样率", _ph_combo(card, ("8000", "16000"), _TIP_AUDIO_ENC)),
                ("分辨率", res_cell),
                ("I 帧间隔", cmb_iframe),
                ("图像质量", q_cell),
                ("码率上限", cmb_bitrate),
            ]
        else:
            # 辅码流1：码流控制|编码模式 / 帧率|I 帧间隔 /
            #           分辨率|码率上限 / 图像质量
            cells = [("码流控制", cmb_brc), ("编码模式", cmb_comp),
                     ("帧率", fps_cell), ("I 帧间隔", cmb_iframe),
                     ("分辨率", res_cell), ("码率上限", cmb_bitrate),
                     ("图像质量", q_cell)]
        g = QGridLayout()
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(6)
        _fill_grid(g, card, cells)
        v.addLayout(g)

        btns = QHBoxLayout()
        btns.addStretch(1)
        btn_read = PushButton("读取", card)
        btn_apply = PrimaryPushButton("应用主码流" if main else "应用辅码流", card)
        btn_read.clicked.connect(lambda _=False, f=fmt: self._on_read(f))
        btn_apply.clicked.connect(lambda _=False, f=fmt: self._on_apply(f))
        btns.addWidget(btn_read)
        btns.addWidget(btn_apply)
        v.addLayout(btns)

        parent_layout.addWidget(card)
        return {"fmt": fmt, "cmb_brc": cmb_brc, "cmb_comp": cmb_comp,
                "sld_fps": sld_fps, "cmb_res": cmb_res, "sp_w": sp_w,
                "sp_h": sp_h, "custom": cust, "sld_q": sld_q,
                "cmb_bitrate": cmb_bitrate, "cmb_iframe": cmb_iframe}

    # ---------- 图像属性卡 ----------

    def _build_image_card(self) -> QWidget:
        """设计稿三个子分组：镜像/旋转/翻转/防抖、色彩、清晰度/降噪。
        已接线：镜像/翻转/旋转（9601）、亮度/对比度/饱和度/Gamma（VideoColor）；
        防抖、锐度、3D/2D 降噪后端未开放 → 禁用占位。"""
        card = CardWidget(self)
        card.setMinimumWidth(0)
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(BodyLabel("图像属性", card))
        head.addWidget(_badge(card, "CFG_VIDEO_IMAGE_CTRL 9601"))
        head.addWidget(_badge(card, "P1"))
        head.addStretch(1)
        v.addLayout(head)

        # -- 镜像 / 旋转 / 翻转 / 防抖 --
        v.addWidget(_subhead("镜像 / 旋转 / 翻转 / 防抖", card))
        self._chk_mirror = _check(card, "开启（左右）")
        self._chk_flip = _check(card, "开启（上下 180°）")
        self._cmb_rotate = _combo(card, _ROTATIONS)
        g1 = QGridLayout()
        g1.setHorizontalSpacing(8)
        g1.setVerticalSpacing(6)
        _fill_grid(g1, card, [
            ("镜像", self._chk_mirror), ("翻转", self._chk_flip),
            ("旋转", self._cmb_rotate),
            ("防抖", _ph_combo(card, ("关闭", "电子防抖", "光学防抖"), _TIP_STABLE)),
            ("防抖灵敏度", _ph_slider(card, 0, 100, 50, _TIP_STABLE))])
        v.addLayout(g1)

        # -- 色彩（VideoColor JSON，读写分开排队，见 _on_read_image）--
        v.addWidget(_subhead("色彩", card))
        self._clr: dict[str, dict] = {}
        cells = []
        for key, label in _COLOR_FIELDS:
            cell, sld, lb = _slider_cell(card, 0, 100, 50)
            self._clr[key] = {"slider": sld, "label": lb}
            cells.append((label, cell))
        g2 = QGridLayout()
        g2.setHorizontalSpacing(8)
        g2.setVerticalSpacing(6)
        _fill_grid(g2, card, cells)
        v.addLayout(g2)

        # -- 清晰度 / 降噪（后端未导出，整组占位）--
        v.addWidget(_subhead("清晰度 / 降噪", card))
        g3 = QGridLayout()
        g3.setHorizontalSpacing(8)
        g3.setVerticalSpacing(6)
        _fill_grid(g3, card, [
            ("锐度模式", _ph_combo(card, ("自动", "手动"), _TIP_SHARP)),
            ("锐度值", _ph_slider(card, 0, 100, 55, _TIP_SHARP)),
            ("3D 降噪", _ph_check(card, "开启", _TIP_SHARP)),
            ("3D 等级", _ph_combo(card, ("低", "中", "高"), _TIP_SHARP)),
            ("2D 降噪", _ph_check(card, "开启", _TIP_SHARP)),
            ("2D 等级", _ph_combo(card, ("低", "中", "高"), _TIP_SHARP))])
        v.addLayout(g3)

        btns = QHBoxLayout()
        btns.addStretch(1)
        btn_read = PushButton("读取图像属性", card)
        btn_apply = PrimaryPushButton("应用图像属性", card)
        btn_read.clicked.connect(self._on_read_image)
        btn_apply.clicked.connect(self._on_apply_image)
        btns.addWidget(btn_read)
        btns.addWidget(btn_apply)
        v.addLayout(btns)
        return card

    # ---------- 通用分组卡（P2） ----------

    def _build_group_card(self, title: str, badge_text: str, specs: list,
                          wired: bool = True) -> dict:
        """specs 每项：
        ("combo", 名称, key|None, [(text, data)…])
        ("slider", 名称, key|None, lo, hi)
        ("check", 名称, key|None, 文本)
        ("checks", 名称, (文本…), tip)     —— 一格内并排多个勾选框，恒为占位
        key=None 即禁用占位：不进 widgets，读取 / 应用都不会碰它；每项可在末尾
        追加一个 tooltip 字符串说明缺口。
        wired=False 表示该组后端完全不可用，不生成底部「读取 / 应用」。
        返回 {card, widgets, names, read, apply}。"""
        card = CardWidget(self)
        card.setMinimumWidth(0)
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(BodyLabel(title, card))
        head.addWidget(_badge(card, badge_text, "p2"))
        head.addStretch(1)
        v.addLayout(head)

        widgets: dict = {}
        names: dict[str, dict] = {}
        cells = []
        for spec in specs:
            kind, label = spec[0], spec[1]
            if kind == "checks":    # 一格内并排多个勾选框，后端未开放 → 恒占位
                cells.append((label, _ph_checks(card, spec[2], spec[3])))
                continue
            store = None            # 入布局的是控件本体，入 widgets 的可能是包装
            if kind == "combo":
                key, options = spec[2], spec[3]
                tip = spec[4] if len(spec) > 4 else _TIP_NA
                w = _combo(card, options)
                if key is not None:
                    names[key] = {d: t for t, d in options}
            elif kind == "slider":
                key, lo, hi = spec[2], spec[3], spec[4]
                tip = spec[5] if len(spec) > 5 else _TIP_NA
                w, sld, lb = _slider_cell(card, lo, hi, lo)
                store = {"slider": sld, "label": lb}
            else:                                   # "check"
                key, text = spec[2], spec[3]
                tip = spec[4] if len(spec) > 4 else _TIP_NA
                w = _check(card, text)
            if key is None:
                _ph(card, w, tip)
            else:
                widgets[key] = w if store is None else store
            cells.append((label, w))

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        _fill_grid(grid, card, cells)
        v.addLayout(grid)

        btn_read = btn_apply = None
        if wired:
            btns = QHBoxLayout()
            btns.addStretch(1)
            btn_read = PushButton("读取", card)
            btn_apply = PrimaryPushButton("应用", card)
            btns.addWidget(btn_read)
            btns.addWidget(btn_apply)
            v.addLayout(btns)
        return {"card": card, "widgets": widgets, "names": names,
                "read": btn_read, "apply": btn_apply}

    def _wire_group(self, grp: dict, getter, setter):
        grp["read"].clicked.connect(lambda _=False: self._read_group(grp, getter))
        grp["apply"].clicked.connect(
            lambda _=False: self._apply_group(grp, getter, setter))

    def _fill_group(self, grp: dict, info: dict):
        names = grp.get("names", {})
        for key, w in grp["widgets"].items():
            if key not in info or info[key] is None:
                continue
            if isinstance(w, ComboBox):
                # 设备回了选项表以外的值时按原值插入显示，别默默落到第 0 项，
                # 否则用户点「应用」就把设备现值改成了列表首项
                _select_or_insert(w, info[key], _unit_suffix(w),
                                  names=names.get(key))
            elif isinstance(w, CheckBox):
                w.setChecked(bool(info[key]))
            else:
                if key == "gain" and info.get("gain_max"):
                    # 增益上下限由设备给（nGainMin / nGainMax），写死 0-100 会截断真机档位
                    w["slider"].setRange(int(info.get("gain_min") or 0),
                                         int(info["gain_max"]))
                val = int(info[key])
                w["slider"].setValue(val)
                w["label"].setText(str(val))

    def _group_values(self, grp: dict) -> dict:
        out = {}
        for key, w in grp["widgets"].items():
            if isinstance(w, ComboBox):
                out[key] = w.currentData()
            elif isinstance(w, CheckBox):
                out[key] = w.isChecked()
            else:
                out[key] = w["slider"].value()
        return out

    def _read_group(self, grp: dict, getter):
        c = self._client()
        if not c:
            _toast(self, "请先在「相机工具」页连接设备", duration=3000)
            return
        self._submit(lambda: getter(c),
                     lambda info: (self._fill_group(grp, info),
                                   _toast(self, "已读取", duration=1200)),
                     lambda m: _toast(self, m, True, 3500))

    def _apply_group(self, grp: dict, getter, setter):
        """写后读回校验：固件“接受但不落库”的组会明确提示未生效。"""
        c = self._client()
        if not c:
            _toast(self, "请先在「相机工具」页连接设备", duration=3000)
            return
        vals = self._group_values(grp)

        def work():
            setter(c, vals)
            return getter(c)

        def done(info):
            self._fill_group(grp, info)
            bad = [k for k in vals
                   if k in info and str(info[k]) != str(vals[k])]
            if bad:
                _toast(self, "该固件未生效：" + "、".join(bad), True, 4000)
            else:
                _toast(self, "已应用", duration=1500)

        self._submit(work, done, lambda m: _toast(self, m, True, 3500))

    # ---------- 聚焦信息（P3 只读） ----------

    def _build_focus_card(self) -> QWidget:
        card = CardWidget(self)
        card.setMinimumWidth(0)
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(BodyLabel("聚焦信息", card))
        head.addWidget(_badge(card, "P3 只读", "p3"))
        head.addStretch(1)
        v.addLayout(head)
        desc = CaptionLabel(
            "聚焦模式 / 当前焦距 / 变倍位置 / 对焦状态 —— 只读展示。", card)
        desc.setStyleSheet(f"color:{_C_SUB};font-size:12px;")
        desc.setWordWrap(True)
        v.addWidget(desc)
        self._lb_focus = CaptionLabel("未读取", card)
        self._lb_focus.setStyleSheet(f"color:{_C_SUB};font-size:11px;")
        self._lb_focus.setWordWrap(True)
        v.addWidget(self._lb_focus)
        row = QHBoxLayout()
        row.addStretch(1)
        btn = PushButton("读取", card)
        btn.clicked.connect(self._read_focus)
        row.addWidget(btn)
        v.addLayout(row)
        return card

    def _read_focus(self):
        c = self._client()
        if not c:
            _toast(self, "请先在「相机工具」页连接设备", duration=3000)
            return
        self._submit(c.video_options, self._fill_focus, self._toast_err)

    def _fill_focus(self, info: dict):
        self._lb_focus.setText(f"聚焦模式：{info.get('focus_mode', '-')}"
                               f"　（焦距/变倍需 PTZ 能力位，后续接入）")

    # ---------- 布局重排（右列 1/2/3 列自适应） ----------

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        # 这里只做列数重排：分组按钮的信号连接必须只在 __init__ 里做一次，
        # 曾在 resizeEvent 里重复 _wire_group，每缩放一次就多挂一份连接，
        # 点一次「读取」会打出 N 次设备请求
        self._reflow_right(self.width())

    def _reflow_right(self, width: int):
        """右侧卡片列数：<1300 单列 / ≥1300 两列 / ≥1850 三列。
        左列固定约 520px，多余宽度都交给右列，所以右列必须自己分档，
        不然 1400~2000 宽时单列卡片会被拉成大片空白。"""
        if width >= _COLS_3_MIN:
            cols = 3
        elif width >= _COLS_2_MIN:
            cols = 2
        else:
            cols = 1
        if cols == self._right_cols:
            return
        self._right_cols = cols
        for card in self._right_cards:
            self._right_grid.removeWidget(card)
        for i, card in enumerate(self._right_cards):
            self._right_grid.addWidget(card, i // cols, i % cols)
        for c in range(3):
            self._right_grid.setColumnStretch(c, 1 if c < cols else 0)
        rows = (len(self._right_cards) + cols - 1) // cols
        for r in range(rows):
            self._right_grid.setRowStretch(r, 0)
        self._right_grid.setRowStretch(rows, 1)

    # ---------- 会话 ----------

    def _client(self):
        return self._cam._client if self._cam else None

    def _submit(self, fn, on_ok, on_err):
        """所有设备调用走「相机工具」页的串行队列：同一登录会话上并发发
        GetConfig/SetConfig 会串协议（隧道下报 Protocol error）"""
        if self._cam is not None:
            self._cam._serial.submit(fn, on_ok, on_err)
        else:
            on_err("相机工具页未初始化")

    def _toast_err(self, msg):
        _toast(self, msg, True, 3500)

    # ---------- 预览 / 抓图 / 全屏 ----------

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._sync_pv_state()
        # 相机工具页 hideEvent 已停流；本页显示时接手同一登录会话起辅码流
        if self._client() and not self._preview.playing:
            self._start_preview()

    def hideEvent(self, event):  # noqa: N802
        super().hideEvent(event)
        if self._fs_win is not None:
            self._fs_win.close()      # 全屏中切页：先收回画布，避免孤儿窗口
        if self._preview.playing:
            self._preview.set_playing(False)  # 先铺暗底，防切回闪旧帧
            self._stop_preview()

    def _set_pv_state(self, text: str, color: str = _C_SUB):
        """侧栏「状态」行 + 起停按钮互斥 + 画布说明文字隐显"""
        self._lb_pv_state.setText(text)
        self._lb_pv_state.setStyleSheet(f"color:{color};font-size:11.5px;")
        playing = self._preview.playing
        self._btn_pv_play.setEnabled(not playing)
        self._btn_pv_stop.setEnabled(playing)
        self._lb_pv_hint.setVisible(not playing)

    def _sync_pv_state(self):
        if not self._client():
            self._set_pv_state("● 未连接")
        elif self._preview.playing:
            self._set_pv_state("● 渲染中", _C_OK)
        else:
            self._set_pv_state("● 已停止")

    def _start_preview(self):
        c = self._client()
        if not c:
            _toast(self, "请先在「相机工具」页连接设备", duration=3000)
            return
        # SDK 需要 HWND 直渲染：强制原生窗口
        self._preview.setAttribute(Qt.WA_DontCreateNativeAncestors, True)
        self._preview.setAttribute(Qt.WA_NativeWindow, True)
        hwnd = int(self._preview.winId())
        self._btn_pv_play.setEnabled(False)

        def work():
            c.realplay_start(hwnd, channel=0, sub_stream=True)

        self._submit(
            work,
            lambda _r: (self._preview.set_playing(True),
                        self._set_pv_state("● 渲染中", _C_OK)),
            lambda m: (self._preview.set_playing(False),
                       self._set_pv_state("● 起流失败", _C_WARN),
                       _toast(self, m, error=True, duration=3500)))

    def _stop_preview(self):
        if not self._client():
            return
        self._submit(self._client().realplay_stop,
                     lambda _r: (self._preview.set_playing(False),
                                 self._set_pv_state("● 已停止")),
                     lambda _m: (self._preview.set_playing(False),
                                 self._set_pv_state("● 已停止")))

    def _on_snap(self):
        """抓图存档：与「相机工具」页同一落盘目录 logs/snap（已 gitignore）"""
        c = self._client()
        if not c:
            _toast(self, "请先在「相机工具」页连接设备", duration=3000)
            return
        snap_dir = os.path.join(ROOT, "logs", "snap")
        os.makedirs(snap_dir, exist_ok=True)
        path = os.path.join(
            snap_dir,
            f"dahua_{c.serial or 'cam'}_{datetime.now():%Y%m%d_%H%M%S}.jpg")
        self._btn_pv_snap.setEnabled(False)

        def done(p):
            self._btn_pv_snap.setEnabled(True)
            logger.info("编码页抓图已存档：%s", p)
            _toast(self, "抓图已存档", duration=2000)

        def err(m):
            self._btn_pv_snap.setEnabled(True)
            _toast(self, m, error=True, duration=3500)

        self._submit(lambda: c.snap_to_file(path), done, err)

    def _on_fullscreen(self):
        """进入全屏：停流 → 画布借进全屏窗 → 对新 HWND 重起流。
        SDK 只认 HWND 直渲，全屏不是「复制画面」而是搬同一块画布。"""
        if not self._client():
            _toast(self, "请先在「相机工具」页连接设备", duration=3000)
            return
        if self._fs_win is not None:
            return
        was_playing = self._preview.playing
        if was_playing:
            self._stop_preview()
        self._fs_was_playing = was_playing
        self._pv_wrap.removeWidget(self._preview)
        win = _PreviewFullScreen(self._preview)
        win.closed.connect(self._exit_fullscreen)
        self._fs_win = win
        win.showFullScreen()
        win.setFocus()
        self._start_preview()     # stop 已排在前，串行队列保证先后

    def _exit_fullscreen(self):
        """退出全屏（Esc / 双击）：停流 → 画布放回原位 → 恢复进入前的播放态"""
        was = self._fs_was_playing
        if self._preview.playing:
            self._stop_preview()
        self._fs_win = None
        self._pv_wrap.insertWidget(0, self._preview, 3)
        self._preview.set_playing(False)
        self._set_pv_state("● 已停止")
        if was and self._client() and self.isVisible():
            self._start_preview()

    # ---------- 码流读写 ----------

    def _on_read(self, fmt: int | None = None):
        """fmt=None 读主+辅（顶栏「读取全部」），否则只读卡上那一路"""
        c = self._client()
        if not c:
            _toast(self, "请先在「相机工具」页连接设备", duration=3000)
            return
        if fmt is None:
            self._submit(lambda: (c.encode_video(1), c.encode_video(4)),
                         self._fill_all, self._toast_err)
            return

        def done(info):
            self._fill_card(self._cards[fmt], info)
            _toast(self, f"{_STREAM_NAMES[fmt]}已读取", duration=1500)

        self._submit(lambda: c.encode_video(fmt), done, self._toast_err)

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
        self._lb_state.setStyleSheet(f"color:{_C_OK};")

    def _fill_card(self, card: dict, info: dict):
        _select_or_insert(card["cmb_brc"], info["bitrate_control"],
                          names=_BRC_NAMES)
        _select_or_insert(card["cmb_comp"], info["compression"],
                          names=_COMPRESSION_NAMES)
        card["sld_fps"].setValue(int(round(info["framerate"])))
        self._fill_resolution(card, int(info["width"]), int(info["height"]))
        card["sld_q"].setValue(int(info["image_quality"]))
        _select_or_insert(card["cmb_bitrate"], info["bitrate"], " KB/s")
        _select_or_insert(card["cmb_iframe"], info["iframe_interval"])
        if card["fmt"] == 4:
            self._update_pv_info(info)

    def _fill_resolution(self, card: dict, w: int, h: int):
        cmb = card["cmb_res"]
        for i in range(cmb.count()):
            if cmb.itemData(i) == (w, h):
                cmb.setCurrentIndex(i)
                if card["custom"] is not None:
                    card["custom"].hide()
                return
        if card["custom"] is not None:
            # 主码流：落到「自定义…」并回填宽高手输框
            _select(cmb, _CUSTOM_RES)
            card["sp_w"].setValue(w)
            card["sp_h"].setValue(h)
            card["custom"].show()
        else:
            # 辅码流无「自定义…」项：按设备值补一项，保证「应用」能原样回写
            _select_or_insert(cmb, (w, h),
                              names={(w, h): f"{w}×{h}（自定义）"})

    def _update_pv_info(self, info: dict):
        """预览侧栏实时信息：预览走的就是辅码流1，所以取这一路的回值"""
        self._lb_pv_stream.setText(
            f"辅码流1 · {int(info['width'])}×{int(info['height'])}")
        self._lb_pv_fps.setText(f"{int(round(info['framerate']))} fps")
        comp = _COMPRESSION_NAMES.get(int(info["compression"]),
                                      str(info["compression"]))
        brc = _BRC_NAMES.get(int(info["bitrate_control"]),
                             str(info["bitrate_control"]))
        self._lb_pv_codec.setText(f"{comp} / {brc}")

    def _on_apply(self, fmt: int | None = None):
        """fmt=None 写主+辅（保留原「一次应用两路」行为），否则只写该路"""
        c = self._client()
        if not c:
            _toast(self, "请先在「相机工具」页连接设备", duration=3000)
            return
        fmts = (1, 4) if fmt is None else (fmt,)
        params = {f: _card_params(self._cards[f]) for f in fmts}

        def work():
            return tuple(c.set_encode_video(f, **params[f]) for f in fmts)

        if fmt is None:
            # set_encode_video 写后会重读，回显设备真实值比回显入参可信
            self._submit(work, self._fill_all, self._toast_err)
            return

        def done(results):
            self._fill_card(self._cards[fmt], results[0])
            _toast(self, f"{_STREAM_NAMES[fmt]}已应用", duration=1800)

        self._submit(work, done, self._toast_err)

    # ---------- 图像属性读写 ----------

    def _on_read_image(self):
        c = self._client()
        if not c:
            _toast(self, "请先在「相机工具」页连接设备", duration=3000)
            return
        # 9601 与 VideoColor 是两条独立配置：分开排队，任一失败不拖累另一个
        self._submit(c.image_ctrl, self._fill_image, self._toast_err)
        self._submit(c.video_color, self._fill_color,
                     lambda m: _toast(self, f"色彩读取失败：{m}", True, 3500))

    def _fill_image(self, info: dict, quiet: bool = False):
        self._chk_mirror.setChecked(bool(info.get("mirror")))
        self._chk_flip.setChecked(bool(info.get("flip")))
        _select(self._cmb_rotate, int(info.get("rotate", 0)))
        # bMirror / bFlip 是能力位（是否支持）：不支持就置灰，别让用户反复点应用
        self._chk_mirror.setEnabled(bool(info.get("supports_mirror", True)))
        self._chk_flip.setEnabled(bool(info.get("supports_flip", True)))
        if not quiet:
            _toast(self, "图像属性已读取", duration=1500)

    def _fill_color(self, info: dict, quiet: bool = False):
        """VideoColor 回值键为大写（Brightness…），映射到四个滑块"""
        got = False
        for key, w in self._clr.items():
            if info.get(key.capitalize()) is None:
                continue
            val = int(info[key.capitalize()])
            w["slider"].setValue(val)
            w["label"].setText(str(val))
            got = True
        # 只有真正拿到值才开闸回写：否则应用时会把默认 50 当成用户意图写下去
        self._color_loaded = got
        if not quiet:
            _toast(self, "色彩已读取", duration=1500)

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

        self._submit(work, done, self._toast_err)
        if self._color_loaded:
            self._apply_color(c)

    def _apply_color(self, c):
        """色彩回写：只有成功读取过才发（否则滑块还是默认值，会把画面写黑）"""
        vals = {k: w["slider"].value() for k, w in self._clr.items()}

        def work():
            c.set_video_color(**vals)
            return c.video_color()

        def done(info: dict):
            self._fill_color(info, quiet=True)
            bad = [k for k in vals
                   if str(info.get(k.capitalize())) != str(vals[k])]
            if bad:
                labels = dict(_COLOR_FIELDS)
                _toast(self, "该固件未生效：" + "、".join(labels[k] for k in bad),
                       True, 4000)
            else:
                _toast(self, "色彩已应用", duration=1500)

        self._submit(work, done, self._toast_err)


# ---------------------------------------------------------------- 模块级助手

_STREAM_NAMES = {1: "主码流", 4: "辅码流1"}


def _pv_value(parent, text: str) -> CaptionLabel:
    """预览侧栏的值标签（设计稿 .pvside 右列）"""
    lb = CaptionLabel(text, parent)
    lb.setStyleSheet("color:#e6e9f2;font-size:11.5px;")
    lb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    return lb


def _card_params(card: dict) -> dict:
    """卡片控件 → set_encode_video 入参（全字段下发，设备侧 RMW）"""
    w, h = _resolved_resolution(card)
    return dict(compression=card["cmb_comp"].currentData(),
                width=w, height=h,
                framerate=card["sld_fps"].value(),
                bitrate_control=card["cmb_brc"].currentData(),
                bitrate=card["cmb_bitrate"].currentData(),
                iframe_interval=card["cmb_iframe"].currentData(),
                image_quality=card["sld_q"].value())


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


def _select_or_insert(combo: ComboBox, value, suffix: str = "", names=None):
    """选中设备回值；列表里没有就按 names / 原值插一项。
    不能默认落到 index 0：那样用户点「应用」就把设备现值改成了首项"""
    for i in range(combo.count()):
        if combo.itemData(i) == value:
            combo.setCurrentIndex(i)
            return
    text = (names or {}).get(value)
    combo.addItem(f"{text}" if text else f"{value}{suffix}", userData=value)
    combo.setCurrentIndex(combo.count() - 1)


def _unit_suffix(combo: ComboBox) -> str:
    """从现有选项推单位后缀（"0 s"/"2 s" → " s"），插入未知值时沿用同一写法。
    只有全部选项都以同一尾巴结尾才采信，否则返回空串（不往中文枚举上乱贴）"""
    texts = [combo.itemText(i) for i in range(combo.count())]
    if not texts:
        return ""
    tail = texts[0].lstrip("0123456789.")
    return tail if tail and all(t.endswith(tail) for t in texts) else ""

