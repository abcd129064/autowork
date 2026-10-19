# -*- coding: utf-8 -*-
"""工具页 ·「相机工具」子页：大华相机登录 / 预览 / 云台 / OSD 叠加

设计稿：design/dahua_camera_tool.html v1；可行性证据：
docs/大华相机工具集成调研2026-10-06.md §5.0。

架构约定：
- SDK 交互全部经 core/dahua_sdk.py（Qt-free 单例封装）；
- 所有阻塞调用（登录/云台/OSD 读写/抓图）走 _Call 线程，ok/err 双落点
  （项目 Worker 信号契约：result 与 error 必须都有连接，09-30 教训）；
- SDK 回调不直触控件：预览由 SDK 内部渲染到 HWND，本页零数据回调；
- ok/err 双落点：每个阻塞调用的成功与失败都必须有回调，否则按钮会
  永久停在禁用态，线程侧的兜底见 _SerialCaller.run。

线程模型：界面只负责把任务丢进 _SerialCaller，由它单线程串行调用 SDK；
本页任何方法都不在 GUI 线程里直接碰设备，否则隧道抖动时整窗会假死。
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QApplication, QGridLayout, QHBoxLayout,
                               QLabel, QScrollArea, QVBoxLayout, QWidget)
from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, CheckBox,
                            FluentIcon, LineEdit, PasswordLineEdit,
                            PrimaryPushButton, PushButton, SpinBox)

from core import dahua_sdk
from core.dahua_sdk import DahuaClient
from core.utils import show_info_bar

logger = logging.getLogger(__name__)

# 仓库根：抓图统一落到 logs/snap，camera → windows → 仓库根共上溯三层
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _toast(widget: QWidget, msg: str, error: bool = False, duration: int = 2500):
    """统一的信息条入口。

    错误提示固定用 error 样式并延长驻留，否则失败原因一闪而过，
    用户只看到按钮变灰却不知道发生了什么。
    """
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
        """起线程即开始消费；队列用标准库 queue，免去自己维护锁与条件变量。"""
        super().__init__(parent)
        import queue
        self._q: "queue.Queue" = queue.Queue()
        self._done.connect(self._dispatch)  # 队列连接：回调回主线程
        self.start()

    def submit(self, fn, on_ok, on_err):
        """把一次设备调用排入 FIFO，结果回调在主线程执行。

        只入队不等待，调用点拿不到返回值；成功与失败分别交给两个回调，
        GUI 线程因此不会为等设备响应而卡住。
        """
        # 队列不设上限：任务由用户操作驱动，天然有界，加背压反而会卡住点击
        self._q.put((fn, on_ok, on_err))

    def run(self):  # noqa: D102
        """消费队列直到收到 None 哨兵；单条调用异常不终止线程。"""
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
            # 在工作线程 emit，跨线程信号由 Qt 排队投递到主线程执行
            self._done.emit(rec)
            time.sleep(self.PACING_S)  # 隧道下配置请求须留间距，背靠背会协议超时

    def _dispatch(self, rec):
        """把工作线程的结果转交给对应回调（经队列连接，运行在主线程）。"""
        on_ok, on_err, ok, payload = rec
        if ok:
            on_ok(payload)
        else:
            on_err(payload)

    def stop(self, ms: int = 5000) -> None:
        """投递哨兵并等待线程退出。

        超时即放弃等待：退出流程不能被一次挂住的 SDK 调用无限拖住。
        """
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

    # 比终端底色更深一档：让预览区与卡片背景拉开边界，画面更有"屏幕感"
    BG = "#10141c"

    def __init__(self, parent=None):
        super().__init__(parent)
        # 三个属性缺一不可：少了 PaintOnScreen 会闪，少了 Opaque 会透出下层窗口
        self.setAttribute(Qt.WA_PaintOnScreen, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        # 播放标志只切换绘制分支，不控制 SDK 起停；起停仍由按钮与串行线程负责
        self.playing = False

    def set_playing(self, on: bool):
        """切换播放态；停止时主动重绘一次，擦掉冻结的最后一帧"""
        self.playing = bool(on)
        if not self.playing:
            self.update()

    def paintEvent(self, event):  # noqa: N802
        """按 playing 门控绘制：播放中让权给 SDK，停止时填底擦残影。"""
        if self.playing:
            return  # 播放中：绘制权全在 SDK，Qt 一个像素都不画
        from PySide6.QtGui import QPainter, QColor
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(self.BG))
        p.end()



# 顺序即 3×3 网格的排布；空串是中心占位格，做成禁用按钮而非隐藏
_DIR_KEYS = [  # 九宫格 → SDK_PTZ_ControlType 命令名
    ("LEFTTOP", "↖"), ("UP_CONTROL", "↑"), ("RIGHTTOP", "↗"),
    ("LEFT_CONTROL", "←"), ("", "·"), ("RIGHT_CONTROL", "→"),
    ("LEFTDOWN", "↙"), ("DOWN_CONTROL", "↓"), ("RIGHTDOWN", "↘"),
]


class DahuaCameraWork(QWidget):
    """相机工具：登录 → 预览 → 云台（能力位自适应） → OSD 叠加。

    本页只是编排层：登录、云台、OSD 的 SDK 细节都在 core/dahua_sdk，
    换设备型号或换 SDK 版本时只改 core，不动这里的界面代码。
    """

    def __init__(self, win=None, parent=None):
        """搭出登录条 / 预览 / 云台 / OSD 四块骨架。

        构造期不碰设备：所有按钮先置灰，连接成功后按能力位逐块解禁，
        否则用户会在未登录时点到必然失败的按钮。
        """
        super().__init__(parent)
        # 页面工厂会传入主窗口引用；本页没有跨页跳转，暂不参与逻辑
        self._win = win
        self._client: DahuaClient | None = None
        # 页面级串行器：随本控件销毁；退出时 _teardown 会再兜底 stop 一次
        self._serial = _SerialCaller(self)
        # objectName 供 QSS 定向：相机页的按钮密度与其他工具页不同
        self.setObjectName("dahuaCameraWork")

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(10)

        # ---- 登录条 ----
        # 地址支持 host:port：设备常挂在 frp 隧道后面，只填 IP 连不上
        login_card = CardWidget(self)
        lv = QHBoxLayout(login_card)
        lv.setContentsMargins(14, 10, 14, 10)
        self._ed_addr = LineEdit(login_card)
        self._ed_addr.setPlaceholderText("设备地址，支持隧道 host:port（如 49.235.34.253:4238）")
        # 地址栏给最宽：隧道地址可能带域名与端口，窄了看不全
        self._ed_addr.setFixedWidth(280)
        self._ed_user = LineEdit(login_card)
        # 大华出厂默认账号是 admin；密码不预填，避免把凭据写死进代码
        self._ed_user.setText("admin")
        self._ed_user.setFixedWidth(90)
        self._ed_pwd = PasswordLineEdit(login_card)
        # 密码框定宽：长密码不能把连接按钮挤出可视区
        self._ed_pwd.setFixedWidth(120)
        self._btn_conn = PrimaryPushButton(FluentIcon.LINK, "连接", login_card)
        self._btn_disc = PushButton(FluentIcon.CLOSE, "断开", login_card)
        self._btn_disc.setEnabled(False)
        # 状态文案是唯一的连接态反馈，连接中/成功/失败都必须改它
        self._lb_state = BodyLabel("未连接", login_card)
        for w in (self._ed_addr, self._ed_user, self._ed_pwd,
                  self._btn_conn, self._btn_disc, self._lb_state):
            lv.addWidget(w)
        lv.addStretch(1)
        root.addWidget(login_card)

        # ---- 中区：预览 + 云台 ----
        mid = QHBoxLayout()
        mid.setSpacing(10)

        # 画布最小尺寸按 16:9 给足：更小时 SDK 会自行缩放，字迹发糊
        preview_card = CardWidget(self)
        pv = QVBoxLayout(preview_card)
        pv.setContentsMargins(12, 10, 12, 12)
        self._preview = _PreviewCanvas(preview_card)
        self._preview.setMinimumSize(560, 315)
        pv.addWidget(self._preview, 1)
        # 提示覆盖「未连接 / 已连接未开预览」两种状态，开播后隐藏
        self._lb_pv_hint = CaptionLabel(
            "连接后点击「开始预览」（SDK 直接渲染辅码流到上方画布）", preview_card)
        pv.addWidget(self._lb_pv_hint)
        pbar = QHBoxLayout()
        self._btn_play = PrimaryPushButton(FluentIcon.VIDEO, "开始预览", preview_card)
        self._btn_play.setEnabled(False)
        self._btn_stop_play = PushButton(FluentIcon.PAUSE, "停止", preview_card)
        # 停止键初始禁用：没在播放时点它只会给 SDK 发一次无效请求
        self._btn_stop_play.setEnabled(False)
        self._btn_snap = PushButton(FluentIcon.CAMERA, "抓图存档", preview_card)
        # 抓图要求已登录，故与预览键一起，连接成功后才解禁
        self._btn_snap.setEnabled(False)
        # 抓图结果留在这里而不是只弹 toast：路径需要可复制、可回看
        self._lb_snap = CaptionLabel("", preview_card)
        for w in (self._btn_play, self._btn_stop_play, self._btn_snap):
            pbar.addWidget(w)
        pbar.addStretch(1)
        pbar.addWidget(self._lb_snap)
        pv.addLayout(pbar)
        mid.addWidget(preview_card, 5)

        # 云台卡吃 3 份宽度、预览吃 5 份：窄窗口下方向键仍要点得到
        ptz_card = CardWidget(self)
        ptz = QVBoxLayout(ptz_card)
        ptz.setContentsMargins(12, 10, 12, 12)
        ptz_h = QHBoxLayout()
        ptz_h.addWidget(BodyLabel("速度", ptz_card))
        self._sp_speed = SpinBox(ptz_card)
        # 1-8 是 SDK 的速度档位；超出范围设备会直接拒绝，不在界面留非法值
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
                # 按下发动作、松开发 stop：只发按下的话云台会一直转到撞限位
                btn.pressed.connect(lambda c=cmd: self._ptz_cmd(c, stop=False))
                btn.released.connect(lambda c=cmd: self._ptz_cmd(c, stop=True))
                self._ptz_btns[cmd] = btn
            else:
                # 中心格置灰但仍占位：去掉它九宫格会塌成 8 格，方向感全错
                btn.setEnabled(False)
            grid.addWidget(btn, i // 3, i % 3)
        ptz.addWidget(grid_w, 0, Qt.AlignCenter)

        # 变倍/聚焦/光圈三列并排；按住持续动、松开即停，与方向键同一套逻辑
        zf = QGridLayout()
        zf.setSpacing(6)
        self._zf_btns: list[PushButton] = []
        # 三列共用同一套按下/松开逻辑，复制三段容易出现某列漏绑 stop
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

        # ---- 预置点列表（设计稿形态：编号徽标 + 名称 + 调用/设为此处/删 + 新增行）----
        # 列表限高 + 内部滚动：预置点最多 300 条，任其撑高会把 OSD 卡挤出屏幕
        self._preset_panel = QWidget(ptz_card)
        pp = QVBoxLayout(self._preset_panel)
        pp.setContentsMargins(0, 4, 0, 0)
        pp.setSpacing(6)
        phead = QHBoxLayout()
        phead.addWidget(BodyLabel("预置点", self._preset_panel))
        phead.addStretch(1)
        phead.addWidget(CaptionLabel("PTZ Preset(SET/GOTO/DEL)", self._preset_panel))
        pp.addLayout(phead)

        # 行容器单独一层：滚动区只接受一个 widget，不能直接把布局塞进去
        self._preset_host = QWidget(self._preset_panel)
        self._preset_v = QVBoxLayout(self._preset_host)
        self._preset_v.setContentsMargins(0, 0, 0, 0)
        self._preset_v.setSpacing(4)
        self._preset_v.addStretch(1)
        scroll = QScrollArea(self._preset_panel)
        scroll.setWidgetResizable(True)
        # 必须可伸缩：面板宽度变化时行要跟着拉满，否则右侧留一条空白
        scroll.setWidget(self._preset_host)
        # 固定高度而非随内容撑开：预置点最多 300 条，撑开会把 OSD 卡挤出屏幕
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
        self._ed_preset_name.setPlaceholderText("预置点名称（保存相机当前位置）")
        self._btn_preset_ok = PrimaryPushButton("确定", self._preset_editor)
        self._btn_preset_cancel = PushButton("取消", self._preset_editor)
        pe.addWidget(self._ed_preset_name, 1)
        pe.addWidget(self._btn_preset_ok)
        pe.addWidget(self._btn_preset_cancel)
        self._btn_preset_ok.clicked.connect(self._on_preset_add_confirm)
        self._btn_preset_cancel.clicked.connect(self._on_preset_add_cancel)
        # 内联新增行默认隐藏，点「新增预置点」才出现，省一块常驻输入区
        self._preset_editor.hide()
        pp.addWidget(self._preset_editor)

        self._preset_panel.setVisible(False)
        ptz.addWidget(self._preset_panel)
        mid.addWidget(ptz_card, 3)
        root.addLayout(mid, 1)

        # ---- OSD 卡 ----
        # 三种 OSD 各对应一个 SDK 接口，卡片结构相似但字段不同，故用工厂分别构建
        osd_row = QHBoxLayout()
        osd_row.setSpacing(10)
        # 卡片顺序与 SDK 的三个 OSD 接口一一对应，后续按键名分发，不做位置判断
        self._card_chn = self._build_osd_card(
            osd_row, "chn", "通道标题", text_hint="叠加文字（相机名称）", has_pos=True)
        # 只有时间卡带「显示周」，用构造参数区分比建完再 setVisible 更清楚
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
        # 进程退出不会自动回收设备句柄，挂到 aboutToQuit 上兜底关闭
        QApplication.instance().aboutToQuit.connect(self._teardown)

    # ---------- OSD 卡构建 ----------

    def _build_osd_card(self, row: QHBoxLayout, key: str, title: str,
                        text_hint: str | None, has_pos: bool,
                        has_week: bool = False) -> dict:
        """构建一张 OSD 卡并返回其控件字典。

        chn/time/custom 三张卡结构不同（只有时间卡有「显示周」），
        用同一工厂生成，启停与读写逻辑才能按 key 复用。
        """
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
        # 默认勾选：用户打开卡片多半就是要叠加，默认关闭会多一次点击
        show_chk = CheckBox("叠加显示", card)
        show_chk.setChecked(True)
        chk_row.addWidget(show_chk)
        week_chk = CheckBox("显示周", card) if has_week else None
        if week_chk is not None:
            chk_row.addWidget(week_chk)
        chk_row.addStretch(1)
        v.addLayout(chk_row)
        # 坐标区只在支持的卡出现；给不支持的 OSD 传坐标会被设备忽略或报错
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
        # 用默认参数把 key 绑进闭包；直接引用循环变量会让三张卡都指向最后一个 key
        btn_get.clicked.connect(lambda _=False, k=key: self._osd_get(k))
        btn_set.clicked.connect(lambda _=False, k=key: self._osd_set(k))
        return {"key": key, "edits": edits, "show": show_chk, "week": week_chk,
                "get": btn_get, "set": btn_set}

    def _iter_osd_buttons(self):
        """遍历三张 OSD 卡的获取/设置按钮，统一做启用态切换。"""
        for meta in (self._card_chn, self._card_time, self._card_custom):
            yield meta["get"]
            yield meta["set"]

    # ---------- 连接 / 断开 ----------

    def _connect(self):
        """校验地址后把登录放进串行线程。

        登录会阻塞数秒，放在 GUI 线程会假死；地址为空时直接退出，
        不发请求也不留下「连接中」的中间态。
        """
        addr = self._ed_addr.text().strip()
        user = self._ed_user.text().strip() or "admin"
        pwd = self._ed_pwd.text()
        if not addr:
            _toast(self, "请填写设备地址")
            return
        # 先禁用再提交：登录要数秒，重复点击会往队列里塞多次连接
        self._btn_conn.setEnabled(False)
        self._lb_state.setText("连接中…")

        # 连接与能力探测一次做完，省一次设备往返；两者都在工作线程里跑
        def work():
            client = dahua_sdk.connect(addr, user, pwd)
            return client, client.ptz_supported()

        self._serial.submit(work, self._on_connected, self._on_conn_failed)

    def _on_connected(self, result):
        """登录成功后的统一解禁：按 PT 能力决定云台区是否可交互。

        连接后立即读一次 chn/time 两项 OSD，避免界面先显示空值再跳变。
        """
        client, ptz_ok = result
        # 先落地 client 再解禁按钮：按钮回调可能立刻要用到它
        self._client = client
        self._btn_disc.setEnabled(True)
        self._btn_play.setEnabled(True)
        self._btn_snap.setEnabled(True)
        for btn in self._iter_osd_buttons():
            btn.setEnabled(True)
        self._set_ptz_enabled(ptz_ok)
        self._lb_state.setText(
            f"● {client.serial or client.host}（通道 {client.chan_num}）"
            f"{' · PT机型' if ptz_ok else ' · 固定镜头'}")
        _toast(self, f"连接成功：{client.serial}")
        # 只预读这两项：自定义告示没有默认值，读了也会被用户输入覆盖
        self._osd_get("chn")
        self._osd_get("time")
        if ptz_ok:
            self._refresh_presets()

    def _on_conn_failed(self, msg):
        """失败只回滚连接按钮并提示原始错误，其余控件保持登录前的禁用态。"""
        self._btn_conn.setEnabled(True)
        self._lb_state.setText("✕ 连接失败")
        _toast(self, msg, error=True, duration=4000)

    def _disconnect(self):
        """先停预览再异步关闭设备句柄。

        close 走 SDK，不能在 GUI 线程里同步等；句柄在提交时就要摘掉，
        否则回调期间还能拿到一个已经失效的 client。
        """
        self._stop_preview()
        client, self._client = self._client, None
        if client:
            self._serial.submit(client.close, lambda _r: None, lambda _m: None)
        self._btn_conn.setEnabled(True)
        self._btn_disc.setEnabled(False)
        self._btn_play.setEnabled(False)
        self._btn_snap.setEnabled(False)
        for btn in self._iter_osd_buttons():
            btn.setEnabled(False)
        self._set_ptz_enabled(False)
        self._lb_state.setText("未连接")

    def _set_ptz_enabled(self, on: bool):
        """整块启用/禁用云台与预置点，并让「仅 PT 有效」提示互斥显示。"""
        for btn in self._ptz_btns.values():
            btn.setEnabled(on)
        for btn in self._zf_btns:
            btn.setEnabled(on)
        # 提示与面板互斥：不支持 PT 时整块换成说明文字，避免用户反复点灰按钮
        self._ptz_note.setVisible(not on)
        self._preset_panel.setVisible(on)

    # ---------- 预置点列表 ----------

    def _refresh_presets(self):
        """异步拉取预置点列表；失败只记日志，不打断正在进行的操作。"""
        if not self._client:
            return
        # 捕获当前 client：回调回来时 self._client 可能已经被断开置空
        client = self._client

        def work():
            return client.ptz_presets()

        self._serial.submit(work, self._presets_loaded,
                            lambda m: logger.info("预置点列表读取失败: %s", m))

    def _presets_loaded(self, presets: list):
        """用设备返回的列表整体重建行控件，顺序与设备一致。"""
        # 清空旧行（保留末尾 stretch）；反复取首项，正序删会漏行
        while self._preset_v.count() > 1:
            item = self._preset_v.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for p in presets:
            self._preset_v.insertWidget(self._preset_v.count() - 1,
                                        self._preset_row(p["index"], p["name"]))

    def _preset_row(self, idx: int, name: str) -> QWidget:
        """预置点单行：编号徽标 + 名称 + 调用/设为此处/删三个富文本链接。

        链接用 QLabel 的 href 路由（goto:/set:/del:）由一个槽统一分发，
        免得每行新建三个按钮并各自绑定闭包。
        """
        row = QWidget(self._preset_host)
        # 行底色比卡片略亮，靠色差分出行，比加分隔线更轻
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
        """解析路由并排队执行；set/del 会改变列表，完成后刷新一次。"""
        kind, _, s_idx = route.partition(":")
        # 路由串在构造时已保证是数字；解析失败会抛异常，正好暴露拼装错误
        idx = int(s_idx)
        if not self._client:
            return
        client = self._client

        def work():
            client.ptz_preset(kind, idx)
            return kind

        # goto 不改变列表，省掉一次设备往返
        done = (lambda _r: self._refresh_presets() if kind in ("set", "del")
                else None)
        self._serial.submit(work, done, lambda m: _toast(
            self, m, error=True, duration=3500))

    def _on_preset_add(self):
        """进入新增态：隐藏入口按钮，露出内联输入行并抢占焦点。"""
        self._btn_preset_add.hide()
        self._ed_preset_name.clear()
        self._preset_editor.show()
        self._ed_preset_name.setFocus()

    def _on_preset_add_cancel(self):
        """退出新增态，恢复入口按钮，已输入的文本随之下次清空。"""
        self._preset_editor.hide()
        self._btn_preset_add.show()

    def _on_preset_add_confirm(self):
        """挑一个空闲编号，把相机当前位置存成预置点并写别名。

        先读现有列表再求差集，避免覆盖用户已经用掉的编号；
        编号上限 300 由设备决定，不从界面猜。
        """
        if not self._client:
            return
        name = self._ed_preset_name.text().strip()
        client = self._client

        def work():
            existing = {p["index"] for p in client.ptz_presets()}
            # 1..300 是设备侧预置点上限；取最小空闲号，尽量不碰已有编号
            idx = next(i for i in range(1, 301) if i not in existing)
            client.ptz_preset("set", idx)  # 保存相机当前位置到新编号
            if name:
                client.set_preset_alias(idx, name)
            return client.ptz_presets()

        self._serial.submit(work, self._presets_loaded,
                            lambda m: _toast(self, m, error=True, duration=3500))
        # 立即收起输入行：提交是异步的，等回调再收会让用户以为没点上
        self._on_preset_add_cancel()

    # ---------- 预览 / 抓图 ----------

    def _start_preview(self):
        """把画布提升为原生窗口后启动辅码流预览。

        必须等 winId() 真正创建 HWND 再交给 SDK，否则 SDK 拿到无效句柄，
        表现为「预览已开启但没有画面」。
        """
        if not self._client:
            return
        # SDK 需要 HWND 直渲染：强制原生窗口
        # 不连带父级原生化：否则整个中区都会脱离 Qt 布局，滚动与缩放全乱
        self._preview.setAttribute(Qt.WA_DontCreateNativeAncestors, True)
        self._preview.setAttribute(Qt.WA_NativeWindow, True)
        hwnd = int(self._preview.winId())
        self._btn_play.setEnabled(False)
        client = self._client

        # 走辅码流：主码流分辨率远超控件尺寸，解码与缩放都是白费
        def work():
            client.realplay_start(hwnd, channel=0, sub_stream=True)

        self._serial.submit(work, lambda _r: self._preview_on(),
                       lambda m: (self._btn_play.setEnabled(True),
                                  _toast(self, m, error=True, duration=3500)))

    def _preview_on(self):
        """进入播放态：按钮互换、提示隐藏；画面交给 SDK 独占绘制。"""
        self._btn_play.setEnabled(False)
        self._btn_stop_play.setEnabled(True)
        self._preview.set_playing(True)  # 播放中：Qt 停止绘制，SDK 独占
        self._lb_pv_hint.hide()
        _toast(self, "预览已开启（辅码流）", duration=1800)

    def _stop_preview(self):
        """停止预览；失败也要回到非播放态，否则画布会一直保留冻结帧。"""
        if not self._client:
            return
        # 直接传方法引用而不是 lambda：停止不依赖参数，串行器调用即可
        self._serial.submit(self._client.realplay_stop,
                       lambda _r: self._preview_off(),
                       lambda _m: self._preview_off())

    def _preview_off(self):
        """退出播放态并触发一次重绘，用暗底擦掉上一帧。"""
        self._btn_play.setEnabled(True)
        self._btn_stop_play.setEnabled(False)
        self._preview.set_playing(False)  # 停止：填暗底，擦掉冻结帧
        self._lb_pv_hint.show()

    def _snap(self):
        """抓图到 logs/snap，文件名带设备序列号与秒级时间戳。

        固定落在应用目录下而不是系统临时目录：现场排查时用户需要
        能直接找到这些证据图。
        """
        if not self._client:
            return
        # 每次抓图前确保目录存在：logs/ 可能被用户或清理脚本删掉
        snap_dir = os.path.join(ROOT, "logs", "snap")
        os.makedirs(snap_dir, exist_ok=True)
        # 序列号缺失时用 cam 兜底，时间戳精确到秒仍能保证不重名
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
        """成功后回显完整路径，方便用户直接复制给售后。"""
        # 路径同时留在页面标签上：toast 几秒后消失，用户还要复制给售后
        self._btn_snap.setEnabled(True)
        self._lb_snap.setText(f"已存：{path}")
        _toast(self, "抓图成功")

    # ---------- 云台 / 预置点 ----------

    def _ptz_cmd(self, command: str, stop: bool):
        """按下与松开都发同一条云台命令，仅 stop 标志不同。

        云台是持续动作：只发按下会一直转，所以 released 必须补一条 stop。
        指令高频，失败静默落日志，避免提示淹没界面。
        """
        if not self._client or not command:
            return
        # 速度在提交时快照：排队期间用户改速度，不应影响已排队的指令
        speed = self._sp_speed.value()
        client = self._client

        def work():
            client.ptz(command, speed=speed, stop=stop)

        # 云台指令高频：失败静默落日志（按钮已按能力位过滤）
        self._serial.submit(work, lambda _r: None,
                       lambda m: logger.info("PTZ %s: %s", command, m))

    # ---------- OSD 读写 ----------

    def _osd_get(self, key: str):
        """读取 OSD 配置并回填到对应卡片。

        设备可能不支持某些字段，回填前逐项判空，缺字段就保留界面现状，
        不要用默认值覆盖用户已填内容。
        """
        if not self._client:
            return
        client = self._client

        # 只有 chn/time 有回读接口，自定义告示卡没有对应的读取方法
        def work():
            if key == "chn":
                return client.osd_channel_title_read(0)
            if key == "time":
                return client.osd_time_title(0)
            return {}

        # 回填逐字段判空：缺省字段保留界面现状，不能用默认值覆盖用户输入
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

        self._serial.submit(work, done,
                       lambda m: _toast(self, m, error=True, duration=3500))

    def _osd_set(self, key: str):
        """把卡片上的开关/文字/坐标写回设备。

        坐标非法时按 (0,0) 兜底而不是弹错：输入框允许空串，用户往往
        先点设置再补坐标，此时按左上角保存比中断流程更合理。
        """
        if not self._client:
            return
        # custom 卡没有回读接口，这里只在 chn/time 之间选；写操作再按 key 分发
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
                return client.osd_channel_title(0, text=text or None,
                                                show=show, pos=pos)
            if key == "time":
                client.osd_time_title(0, show=show, show_week=week, pos=pos)
            else:
                client.osd_custom_text(0, text or "", show, pos)
            return None

        def done(result):
            # 老固件拒绝改通道名：正文没变，但开关/位置已生效，提示要分开说
            if isinstance(result, dict) and result.get("name_applied") is False:
                _toast(self, "该固件不支持修改通道名称（已保留设备原名），"
                             "开关/位置已应用", duration=4000)
            else:
                _toast(self, "OSD 设置成功", duration=1800)

        self._serial.submit(work, done,
                       lambda m: _toast(self, m, error=True, duration=3500))

    # ---------- 收尾 ----------

    def _teardown(self):
        """退出前先停串行线程再关设备。

        顺序不能反：线程停稳后才不会有排队的调用去碰已关闭的句柄。
        """
        # 先等队列跑完再关句柄：反过来会让所有排队调用打到已关闭的会话
        self._serial.stop()
        if self._client:
            self._client.close()
