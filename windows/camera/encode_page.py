# -*- coding: utf-8 -*-
"""相机面板 ·「编码配置」子页 v2（2026-10-07 设计稿 encode_page_v2.html）

对齐 ConfigTool「编码配置」字段布局：通道 → 主码流卡 → 辅码流卡 → 读取/应用。
数据源 core/dahua_sdk.py::encode_video / set_encode_video（GetConfig/SetConfig
ENCODE_VIDEO=1100，RMW 只改暴露字段）。设备会话复用相机工具页的登录
（DahuaCameraWork._client + _SerialCaller，串行节流防隧道协议超时）。

界面只暴露 ConfigTool 里最常调的字段，其余字段在 SetConfig 时由设备保留：
ENCODE_VIDEO 是整段配置，全量回写会把未展示的字段一起覆盖成默认值。
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
# 数值来自 dahua_sdk 的常量表，不能按显示顺序从 0 递增：H.265 是 8、MJPG 是 5。
# 列表顺序按现场使用频率排（H.264 最常用），与数值大小无关。
_COMPRESSIONS = [("H.264", 7), ("H.265", 8), ("MJPG", 5), ("MPEG4", 0)]
# VBR 放第一项只是默认展示顺序；回填走 userData 匹配，不依赖下标
_BITRATE_CONTROLS = [("VBR 可变码率", 1), ("CBR 固定码率", 0)]
# 只列现场常用的 5 档；设备侧其它合法宽高走「自定义」，不做全量枚举
_RESOLUTIONS = [("1080P", 1920, 1080), ("720P", 1280, 720),
                ("D1", 704, 576), ("360P", 640, 360), ("CIF", 352, 288)]
# 码率档位（KB/s），覆盖 512K~8M；设备上已有的其它值读取时会临时插入下拉
_BITRATE_PRESETS = [512, 1024, 2048, 3072, 4096, 6144, 8192]
# I 帧间隔（帧数）：25/50 在 25fps 下约等于 1s/2s 一个关键帧，100 用于低带宽场景
_IFRAME_PRESETS = [25, 50, 100]
# SDK 的 1-6 是画质档位而非等距百分比，按档位乘 20% 会显示错
_QUALITY_LABELS = {1: "10%", 2: "30%", 3: "50%", 4: "60%", 5: "80%", 6: "100%"}
# 自定义项用字符串哨兵而不是 None：ComboBox 未选中时 currentData() 也是 None，
# 拿 None 当哨兵会让「空下拉」和「自定义」无法区分
_CUSTOM_RES = "__custom__"


def _toast(widget, msg, error=False, duration=3000):
    """在当前页顶部弹一条 InfoBar，错误红色、成功绿色。

    不用 QMessageBox：模态弹窗会盖住卡片，用户刚填的参数得先关掉才能看，
    而失败提示往往需要边看边改。
    """
    show_info_bar(msg, message_type="error" if error else "success",
                  duration=duration, parent=widget)


class EncodePage(QWidget):
    """编码配置页：主/辅码流的分辨率、帧率、码率、码率类型、图像质量、I 帧间隔。

    两张固定卡片对应 emFormatType=1（主码流）与 4（辅码流1），这是设备
    ENCODE_VIDEO 配置里仅有的两路；辅码流2 未在本页暴露。
    """

    def __init__(self, win=None, cam_page=None, parent=None):
        super().__init__(parent)
        # 主窗口引用只在需要跨页提示时用；SDK 会话统一从相机工具页借
        self._win = win
        self._cam = cam_page  # DahuaCameraWork：共享登录会话与串行执行器
        # objectName 是 QSS 选择器锚点，改名会静默丢掉 theme_qss 里针对本页的样式
        self.setObjectName("encodePage")
        # 以 emFormatType 为键（1=主码流、4=辅码流1），读取与应用都按这个键取卡片
        self._cards: dict[int, dict] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(10)

        # ---- 通道 ----
        # 通道下拉目前只做展示：读取固定取 1、4 两路，切换通道不会触发重新读取
        ch = QHBoxLayout()
        ch.addWidget(BodyLabel("通道", self))
        self._cmb_ch = ComboBox(self)
        # 未连接时先给一个占位通道，避免空下拉；连上后按登录回包重建
        self._cmb_ch.addItem("1")
        ch.addWidget(self._cmb_ch)
        # 未连接状态常驻标题行而不是弹窗：设备断开后本页可能一直开着
        self._lb_state = BodyLabel("未连接（请先在「相机工具」页连接设备）", self)
        self._lb_state.setStyleSheet("color:#9aa1b5;")
        ch.addSpacing(12)
        ch.addWidget(self._lb_state)
        ch.addStretch(1)
        root.addLayout(ch)

        # ---- 主 / 辅码流卡片 ----
        # 卡片构造时一次建好，读取只回填数值：重建控件会丢掉用户未提交的修改
        self._cards[1] = self._build_stream_card("主码流", 1)
        self._cards[4] = self._build_stream_card("辅码流1", 4)

        # ---- 底部按钮 ----
        # 读取与应用分开：SetConfig 是整段回写，必须由用户确认后再提交
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
        """建一张码流参数卡，返回控件字典供 _fill_card 按 key 回填。

        fmt 是设备侧 emFormatType，应用时原样传给 set_encode_video；返回裸
        dict 而不是自定义类，加减字段时不用同步改类定义。
        """
        # CardWidget 自带圆角与卡片底色，页面本身不再手写容器 QSS
        card = CardWidget(self)
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(BodyLabel(title, card))
        # 角标直接露配置项名与 emFormatType：现场对着 ConfigTool 排查时不用翻代码
        badge = BodyLabel(f"GetConfig/SetConfig(1100) · emFormatType={fmt}", card)
        badge.setStyleSheet("color:#4f8cff;font-size:11px;")
        head.addWidget(badge)
        head.addStretch(1)
        v.addLayout(head)

        # 5 列网格：标签-值-标签-值-值；只有两个值列拉伸，标签列保持自然宽度
        g = QGridLayout()
        g.setHorizontalSpacing(20)
        g.setVerticalSpacing(8)

        def lab(text):
            # 标签统一右对齐浅灰，与 qfluentwidgets 表单的视觉习惯一致
            lb = BodyLabel(text, card)
            lb.setStyleSheet("color:#9aa1b5;")
            lb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            return lb

        # 码流控制
        # 显示文案与 SDK 数值解耦，回填按 userData 匹配：设备读回 0/1 才能对上项
        cmb_brc = ComboBox(card)
        for t, d in _BITRATE_CONTROLS:
            cmb_brc.addItem(t, userData=d)
        g.addWidget(lab("码流控制"), 0, 0)
        g.addWidget(cmb_brc, 0, 1)

        # 编码模式
        # 格式改错会让录像在部分播放器上打不开，列表值必须与 SDK 常量一一对应
        cmb_comp = ComboBox(card)
        for t, d in _COMPRESSIONS:
            cmb_comp.addItem(t, userData=d)
        g.addWidget(lab("编码模式"), 0, 2)
        g.addWidget(cmb_comp, 0, 3)

        # 帧率滑条
        # 上限 50 是设备支持的最高帧率；下限取 1 而不是 0，0 帧在设备端属非法配置
        sld_fps = QSlider(Qt.Horizontal, card)
        sld_fps.setRange(1, 50)
        lb_fps = BodyLabel("25 fps", card)
        # 数值标签定宽：数字位数变化时同一行的控件不会左右抖动
        lb_fps.setMinimumWidth(56)
        # 默认参数把标签绑进闭包：lambda 直接引用外层变量容易在重绑时取错控件
        sld_fps.valueChanged.connect(lambda v, lb=lb_fps: lb.setText(f"{v} fps"))
        g.addWidget(lab("帧率"), 1, 0)
        g.addWidget(sld_fps, 1, 1)
        g.addWidget(lb_fps, 1, 2)

        # 分辨率（预设 + 自定义宽高）
        # 「自定义…」固定作为尾项：_fill_card 用 count()-1 定位它，中间插项会错位
        cmb_res = ComboBox(card)
        for name, w, h in _RESOLUTIONS:
            cmb_res.addItem(f"{name}（{w}×{h}）", userData=(w, h))
        cmb_res.addItem("自定义…", userData=_CUSTOM_RES)
        cust = QWidget(card)
        ch = QHBoxLayout(cust)
        ch.setContentsMargins(0, 0, 0, 0)
        ch.setSpacing(4)
        # 宽高下限按设备最小分辨率（176×144）给，上限 4096 覆盖当前所有机型
        sp_w = SpinBox4(cust, 176, 4096)
        sp_h = SpinBox4(cust, 144, 4096)
        ch.addWidget(sp_w)
        ch.addWidget(QLabel("×", cust))
        ch.addWidget(sp_h)
        ch.addStretch(1)
        # 默认隐藏自定义输入行，选到「自定义…」才展开，免得常驻占一行高度
        cust.hide()
        cmb_res.currentIndexChanged.connect(
            # 默认参数把控件绑进闭包：回调触发时外层局部变量可能已被覆盖
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
        # 先给默认值 5（80%）：读取成功前卡片也得有合理初值，不能停在 0 档
        sld_q = QSlider(Qt.Horizontal, card)
        sld_q.setRange(1, 6)
        lb_q = BodyLabel("80%", card)
        # 定宽：百分比文本长度变化时不挤压滑条
        lb_q.setMinimumWidth(44)
        # 档位到百分比的映射集中在 _QUALITY_LABELS，滑条本身不存百分比
        sld_q.valueChanged.connect(
            lambda val, lb=lb_q: lb.setText(_QUALITY_LABELS.get(val, str(val))))
        sld_q.setValue(5)
        g.addWidget(lab("图像质量"), 2, 0)
        g.addWidget(sld_q, 2, 1)
        g.addWidget(lb_q, 2, 2)

        # 码率上限
        # 按 KB/s 展示，与 ConfigTool 的字段单位一致，界面上不换算成 Mbps
        cmb_bitrate = ComboBox(card)
        for b in _BITRATE_PRESETS:
            cmb_bitrate.addItem(f"{b} KB/s", userData=b)
        g.addWidget(lab("码率上限"), 2, 3)
        g.addWidget(cmb_bitrate, 2, 4)

        # I 帧间隔
        # 间隔越小越容易定位拖动，但码率会上去；预设只给现场验证过的三档
        cmb_iframe = ComboBox(card)
        for iv in _IFRAME_PRESETS:
            cmb_iframe.addItem(str(iv), userData=iv)
        g.addWidget(lab("I 帧间隔"), 3, 0)
        g.addWidget(cmb_iframe, 3, 1)

        # 只让两列值控件拉伸：标签列跟着拉伸会把标签和值拉得很远
        for c in range(5):
            g.setColumnStretch(c, 1 if c in (1, 3) else 0)
        v.addLayout(g)
        self.layout().addWidget(card)

        # 键名与控件用途一一对应，_fill_card / _on_apply 直接按 key 取值
        return {"fmt": fmt, "cmb_brc": cmb_brc, "cmb_comp": cmb_comp,
                "sld_fps": sld_fps, "cmb_res": cmb_res, "sp_w": sp_w,
                "sp_h": sp_h, "sld_q": sld_q, "cmb_bitrate": cmb_bitrate,
                "cmb_iframe": cmb_iframe}

    # ---------- 会话 ----------

    def _client(self):
        """复用相机工具页已登录的 SDK 会话，本页不自己登录。

        设备只允许有限并发会话，另开一份登录会挤掉相机页的预览连接。
        """
        return self._cam._client if self._cam else None

    def _submit(self, fn, on_ok, on_err):
        """把 SDK 调用投进相机页的串行执行器，避免与预览/其它配置并发。

        大华隧道协议对同一会话的并发请求很敏感，直连会偶发读超时；
        _SerialCaller 保证同时只有一个请求在飞，并把回调切回主线程。
        """
        if self._cam is not None:
            self._cam._serial.submit(fn, on_ok, on_err)
        else:
            # 没有相机页就立刻走错误回调，不静默丢请求，否则用户点了按钮没反应
            on_err("相机工具页未初始化")

    # ---------- 读取 / 应用 ----------

    def _on_read(self):
        c = self._client()
        if not c:
            _toast(self, "请先在「相机工具」页连接设备", duration=3000)
            return

        def work():
            # 两路放进同一次 work：一次串行任务读完两张卡，少排一次队
            return c.encode_video(1), c.encode_video(4)

        self._submit(work, self._fill_all, lambda m: _toast(self, m, True, 3500))

    def _fill_all(self, pair):
        """读取与应用共用的成功回调：把设备返回的配置回填到两张卡片。

        应用成功后也走这里，用设备回读值刷界面：SetConfig 可能裁剪参数，
        界面必须显示设备真正生效的值，而不是用户刚提交的值。
        """
        main_info, extra_info = pair
        self._fill_card(self._cards[1], main_info)
        self._fill_card(self._cards[4], extra_info)
        # 通道号回填（登录回包通道数）；换设备后通道数会变，所以整表重建
        chan = int(getattr(self._client(), "chan_num", 1) or 1)
        if self._cmb_ch.count() != chan:
            self._cmb_ch.clear()
            for i in range(1, chan + 1):
                self._cmb_ch.addItem(str(i))
        # 绿色状态行给出「读到了」的明确信号，避免用户重复点读取
        self._lb_state.setText("已读取（应用前可修改卡片内参数）")
        self._lb_state.setStyleSheet("color:#70d49c;")

    def _fill_card(self, card: dict, info: dict):
        """把一路编码配置回填到卡片；分辨率不在预设里时改用「自定义」显示实际值。

        设备侧可能有任意合法宽高（如 1920×1536），下拉里没有对应项：既不能
        显示错的分辨率，也不能为此无限扩充预设列表。
        """
        _select(card["cmb_brc"], info["bitrate_control"])
        _select(card["cmb_comp"], info["compression"])
        # 设备回读帧率是浮点（25.0），滑条只接受整数，四舍五入后再回填
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
            # 改写尾项文案而不是 addItem：反复读取会把下拉越堆越长，也会破坏
            # 「尾项 = 自定义」这个约定
            cmb.setItemText(custom_idx, f"{w}×{h}（自定义）")
            cmb.setCurrentIndex(custom_idx)
            card["sp_w"].setValue(w)
            card["sp_h"].setValue(h)
            card["sp_w"].parentWidget().show()
        card["sld_q"].setValue(int(info["image_quality"]))
        _select_or_insert(card["cmb_bitrate"], info["bitrate"], " KB/s")
        _select_or_insert(card["cmb_iframe"], info["iframe_interval"], "")

    def _on_apply(self):
        """把两张卡片的可见字段写回设备，未展示的字段由设备保留。

        set_encode_video 内部是 RMW：先 GetConfig 再改传入的键，所以这里只能
        传界面上真实存在的字段，多了会覆盖掉别处（如客户端）调过的配置。
        """
        c = self._client()
        if not c:
            _toast(self, "请先在「相机工具」页连接设备", duration=3000)
            return

        # 键名必须与 set_encode_video 的形参一致，改名要两边同步，否则 TypeError
        def card_params(card: dict) -> dict:
            w, h = _resolved_resolution(card)
            return dict(compression=card["cmb_comp"].currentData(),
                        width=w, height=h,
                        framerate=card["sld_fps"].value(),
                        bitrate_control=card["cmb_brc"].currentData(),
                        bitrate=card["cmb_bitrate"].currentData(),
                        iframe_interval=card["cmb_iframe"].currentData(),
                        image_quality=card["sld_q"].value())

        # 先在主线程把两路参数取成快照：提交后回调里再读控件，读到的可能是新值
        p_main = card_params(self._cards[1])
        p_extra = card_params(self._cards[4])

        def work():
            # 主码流先写：辅码流失败时主码流已生效，随后的回读能反映真实状态
            m = c.set_encode_video(1, **p_main)
            e = c.set_encode_video(4, **p_extra)
            return m, e

        self._submit(work, self._fill_all,
                     lambda m: _toast(self, m, True, 3500))


class SpinBox4(QWidget):
    """带范围的宽/高小输入框，用 qfw SpinBox 包一层保持与其它控件同风格。

    单独包一层是为了把 setValue/value 固定成 int 语义：设备侧宽高必须是整数，
    浮点会直接被 SetConfig 拒绝。
    """

    def __init__(self, parent, lo, hi):
        super().__init__(parent)
        # 局部导入：只有自定义分辨率用到它，模块顶层不必依赖具体控件类
        from qfluentwidgets import SpinBox
        v = QHBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.sp = SpinBox(self)
        self.sp.setRange(lo, hi)
        # 4 位数字 + 内边距的宽度，避免 4096 被截断显示成 409
        self.sp.setMinimumWidth(72)
        v.addWidget(self.sp)

    def setValue(self, val):
        self.sp.setValue(int(val))

    def value(self):
        return self.sp.value()


def _resolved_resolution(card: dict):
    """取当前生效分辨率：预设项用 userData 里的 (w, h)，自定义项读输入框。

    自定义输入框隐藏时也可能残留旧值，所以只在 currentData 是哨兵时读它。
    """
    cmb = card["cmb_res"]
    data = cmb.currentData()
    if data == _CUSTOM_RES:
        return card["sp_w"].value(), card["sp_h"].value()
    return data


def _select(combo: ComboBox, data):
    """按 userData 选中枚举下拉项；设备值不在列表里时退回第 0 项。

    枚举型下拉不插新项：未知值插进去，用户选中后提交会被设备拒绝，退回默认
    项至少不会把非法配置写下去。
    """
    for i in range(combo.count()):
        if combo.itemData(i) == data:
            combo.setCurrentIndex(i)
            return
    combo.setCurrentIndex(0)


def _select_or_insert(combo: ComboBox, value, suffix: str):
    """按 userData 选中数值下拉项，没有就把设备值插入并选中。

    码率、I 帧间隔是连续量，设备上可能是预设之外的合法值；先查重再插入，
    否则每读一次设备就多一条重复项。
    """
    for i in range(combo.count()):
        if combo.itemData(i) == value:
            combo.setCurrentIndex(i)
            return
    combo.addItem(f"{value}{suffix}", userData=value)
    combo.setCurrentIndex(combo.count() - 1)
