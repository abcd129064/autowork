# -*- coding: utf-8 -*-
"""画质提升 Hub —— 工具页「画质提升」页签的五子页容器（纯 ffmpeg 滤镜链）

设计稿 design/video_enhance_tool.html。命令拼装、滤镜链、进度解析全在
windows.tools.video_enhance（纯 stdlib，可离线单测），子进程与线程收尾在
workers.video_enhance_worker，本文件只做界面与状态编排。

五子页共享**同一份参数字典**（VideoEnhanceHub.params），任何一处改动都经
paramsChanged 广播刷新。不做副本是刻意的：滤镜页显示的 -vf 串和编码页显示的
完整命令必须是真跑的那一条，两份状态迟早漂移，而漂移的代价是用户看着 A 调出来
的结果其实是 B 跑出来的。

弹出按钮沿用基类那颗 btn_popout（不另建）：hub_popout 是按属性名隐藏的，
另建一颗会永远留在弹出窗口里，变成「窗口里再弹一层」（同 windows/worklog/hub.py）。
"""
import os
import time
from contextlib import contextmanager
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QFileDialog, QHeaderView, QTableWidgetItem,
                               QAbstractItemView, QLabel, QSizePolicy,
                               QApplication)
from qfluentwidgets import (TitleLabel, CaptionLabel, BodyLabel, StrongBodyLabel,
                            CardWidget, LineEdit, PushButton, PrimaryPushButton,
                            ComboBox, CompactSpinBox, TableWidget, ProgressBar,
                            FluentIcon, ToolButton, MessageBox, Slider,
                            SwitchButton, CheckBox)

from core.design_tokens import TYPE_SCALE_PX
from main_window.pivot_page import PivotPage
from main_window.tool_hub import (_C_ACCENT, _C_DANGER, _C_MUTED, _C_SUCCESS,
                                  _fmt_size, _make_scroll, _make_terminal,
                                  _make_vsplitter, _term_font, _transparent)
from windows.tools import video_enhance as ve
from workers.video_enhance_worker import VideoEnhanceWorker


# busy 守卫属性名：挂在窗口上（不是页面上），内嵌视图与弹出窗口才能互斥。
# 四个名字都要登记进 main_window/hub_popout.py 的 _BUSY_ATTRS 与
# main_window/shutdown.py 的关窗清理清单，漏一个就会双开 ffmpeg 抢 CPU。
W_ENHANCE = "_ve_enhance_worker"
W_PROBE = "_ve_probe_worker"
W_ENV = "_ve_env_worker"
W_CMP = "_ve_cmp_worker"
WORKER_ATTRS = (W_ENHANCE, W_PROBE, W_ENV, W_CMP)

# 环境检测页要核对的滤镜：只列 UI 真会拼进 -vf 的那些，ffmpeg 上千个滤镜
# 全列出来等于没列。psnr/ssim/libvmaf 归「质量对比」页用，一并在这里报。
WANTED_FILTERS = ("hqdn3d", "atadenoise", "vaguedenoiser", "nlmeans", "bm3d",
                  "cas", "unsharp", "eq", "scale", "minterpolate", "format",
                  "psnr", "ssim", "libvmaf")

# 预设覆盖到的键集合：手动改了其中任何一个就要重新判定「当前还算哪个预设」
_PRESET_KEYS = frozenset()
for _spec in ve.PRESETS.values():
    _PRESET_KEYS |= set(_spec["params"])

VIDEO_FILTER = ("视频文件 (*.mp4 *.mkv *.avi *.mov *.flv *.ts *.webm *.m4v);;"
                "所有文件 (*.*)")

# 任务状态 → (文案, 颜色)；文案直接进表格「状态」列，不再二次翻译
_STATES = {
    "queued": ("排队中", _C_MUTED),
    "probing": ("探测中…", _C_MUTED),
    "ready": ("就绪", _C_ACCENT),
    "running": ("增强中", _C_ACCENT),
    "done": ("已完成", _C_SUCCESS),
    "skipped": ("已跳过：输出已存在", _C_MUTED),
    "failed": ("失败", _C_DANGER),
}


# ==================== 通用小控件 ====================

def _fmt_dur(sec):
    """秒 → mm:ss（超过一小时补 h:），表格里时长与耗时共用"""
    sec = int(sec or 0)
    if sec >= 3600:
        return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _paint(label, text, color):
    """给状态文字上色：qfw 标签走 setTextColor（内部是 CustomStyleSheet），
    直接 setStyleSheet 会被主题切换轮询冲掉（同 tool_hub.VideoRepairWork._paint）"""
    label.setText(text)
    try:
        label.setTextColor(color, color.lighter(135))
    except Exception:
        pass


def _page_head(parent, title, sub):
    """稿 78-80：20px/700 标题 + 13px 副标题，五子页共用同一套页头规格"""
    head = QWidget(parent)
    _transparent(head)
    v = QVBoxLayout(head)
    v.setContentsMargins(28, 20, 28, 6)
    v.setSpacing(3)
    t = TitleLabel(title, head)
    f = QFont(t.font())
    f.setPixelSize(TYPE_SCALE_PX["2xl"])
    f.setWeight(QFont.Bold)          # qfw TitleLabel 默认 DemiBold，稿要 700
    t.setFont(f)
    v.addWidget(t)
    s = CaptionLabel(sub, head)
    sf = QFont(s.font())
    sf.setPixelSize(TYPE_SCALE_PX["base"])
    s.setFont(sf)
    s.setWordWrap(True)
    v.addWidget(s)
    return head


def _card(parent, title, sub=""):
    """卡片 + 卡头（稿 43-47），返回 (卡片, 内容 VBox, 卡头右端 HBox)"""
    card = CardWidget(parent)
    v = QVBoxLayout(card)
    v.setContentsMargins(16, 14, 16, 14)
    v.setSpacing(10)
    h = QHBoxLayout()
    h.setSpacing(8)
    h.addWidget(StrongBodyLabel(title, card))
    if sub:
        s = CaptionLabel(sub, card)
        s.setWordWrap(True)
        h.addWidget(s, 1)
    else:
        h.addStretch(1)
    right = QHBoxLayout()
    right.setSpacing(6)
    h.addLayout(right)
    v.addLayout(h)
    return card, v, right


def _field(parent, label):
    """稿 89 ``.fld``：96px 标签列 + 控件区，返回已放好标签的 HBox"""
    row = QHBoxLayout()
    row.setSpacing(8)
    lab = BodyLabel(label, parent)
    lab.setFixedWidth(96)
    row.addWidget(lab)
    return row


def _hint(parent, text):
    h = CaptionLabel(text, parent)
    h.setWordWrap(True)
    return h


def _combo(parent, pairs, width=None):
    """带 userData 的下拉框；pairs = [(值, 显示文本)]

    userData 必须走关键字：qfw ComboBox 重写过 addItem，第二个位置参数会被
    当成 icon 而不是数据（本仓踩过，见 memory/qfluentwidgets-combobox-additem-userdata）。
    """
    c = ComboBox(parent)
    for value, text in pairs:
        c.addItem(text, userData=value)
    if width:
        c.setFixedWidth(width)
    return c


def _combo_set(c, value):
    """按 userData 选中；找不到就退回第 0 项（旧配置里的值可能已下线）"""
    i = c.findData(value)
    c.setCurrentIndex(i if i >= 0 else 0)


def _combo_val(c, fallback):
    v = c.currentData()
    return fallback if v is None else v


class _FSlider(QWidget):
    """定标滑块 + 数值回显

    qfw Slider 只吃整数，浮点参数统一按 1/step 定标后取整。setValue 掐掉信号：
    刷新界面时若让它回吐 valueChanged，就会 paramsChanged → refresh → setValue
    转成死循环。
    """

    valueChanged = Signal(float)

    def __init__(self, lo, hi, step, value=0.0, parent=None):
        super().__init__(parent)
        self._step = float(step)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.slider = Slider(Qt.Horizontal, self)
        self.slider.setRange(int(round(lo / self._step)), int(round(hi / self._step)))
        self.lbl = CaptionLabel("", self)
        self.lbl.setMinimumWidth(46)
        self.lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        h.addWidget(self.slider, 1)
        h.addWidget(self.lbl)
        self.slider.valueChanged.connect(self._on_slide)
        self.setValue(value)

    def _on_slide(self, iv):
        v = round(iv * self._step, 4)
        self.lbl.setText(f"{v:.2f}")
        self.valueChanged.emit(v)

    def value(self):
        return round(self.slider.value() * self._step, 4)

    def setValue(self, v):
        iv = int(round(float(v or 0) / self._step))
        self.slider.blockSignals(True)
        self.slider.setValue(iv)
        self.slider.blockSignals(False)
        self.lbl.setText(f"{iv * self._step:.2f}")


class _StatStrip(QWidget):
    """稿 30 ``.stat-strip``：一排等宽小卡，每张 [数字, 说明]"""

    def __init__(self, labels, parent=None):
        super().__init__(parent)
        _transparent(self)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.nums = []
        for text in labels:
            c = CardWidget(self)
            v = QVBoxLayout(c)
            v.setContentsMargins(12, 8, 12, 8)
            v.setSpacing(2)
            n = TitleLabel("0", c)
            nf = QFont(n.font())
            nf.setPixelSize(TYPE_SCALE_PX["xl"])
            n.setFont(nf)
            n.setAlignment(Qt.AlignHCenter)
            v.addWidget(n)
            cap = CaptionLabel(text, c)
            cap.setAlignment(Qt.AlignHCenter)
            cap.setWordWrap(True)
            v.addWidget(cap)
            row.addWidget(c, 1)
            self.nums.append(n)

    def set(self, idx, text, color=None):
        """改第 idx 格的数字；color=None 表示用默认前景色（不着色）"""
        lab = self.nums[idx]
        lab.setText(str(text))
        if color is not None:
            try:
                lab.setTextColor(color, color.lighter(135))
            except Exception:
                pass


class _ImagePane(QWidget):
    """抽帧对比的一格（稿 268-275）：标题行 + 等比缩放的图

    自己在 resizeEvent 里重算缩放：QLabel.setScaledContents(True) 无视
    KeepAspectRatio 会把图拉变形，台球桌变椭圆就看不出锐化到底做没做。
    """

    def __init__(self, title, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        h = QHBoxLayout()
        h.setSpacing(8)
        h.addWidget(StrongBodyLabel(title, self))
        self.lbl_meta = CaptionLabel("", self)
        h.addWidget(self.lbl_meta, 1)
        v.addLayout(h)
        self.view = QLabel(self)
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setMinimumHeight(220)
        self.view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.view.setStyleSheet(
            "QLabel{background:#1b1e23;border:1px solid #2b2f36;"
            "border-radius:6px;color:#6b7280;}")
        self.view.setText("尚未抽帧")
        v.addWidget(self.view, 1)
        self._pix = QPixmap()

    def set_image(self, path, meta=""):
        self.lbl_meta.setText(meta)
        self._pix = QPixmap(path) if path else QPixmap()
        if self._pix.isNull():
            self.view.setPixmap(QPixmap())
            self.view.setText("抽帧失败" if path else "尚未抽帧")
        else:
            self.view.setText("")
            self._rescale()

    def _rescale(self):
        if self._pix.isNull():
            return
        box = self.view.size()
        if box.width() < 32 or box.height() < 32:
            return
        self.view.setPixmap(self._pix.scaled(box, Qt.KeepAspectRatio,
                                             Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()


# ==================== 子页外壳 ====================

class VePage(QWidget):
    """子页通用外壳：页头 + 由子类决定「滚动」还是「splitter」

    刻意不在此定义 detach_workers：PivotPage 广播时先找页面钩子、找不到才扫
    ``*_worker`` 后缀属性，基类给个空实现会让持有线程的子页漏掉兜底扫描
    （同 windows/worklog/common.WorklogPage 的口径）。本 Hub 的 worker 全挂在
    窗口属性上，由 VideoEnhanceHub.detach_workers 统一中断。
    """

    def __init__(self, hub, title, sub, parent=None):
        super().__init__(parent)
        _transparent(self)
        self._hub = hub
        self._syncing = False
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(0, 0, 0, 0)
        self.outer.setSpacing(0)
        self.outer.addWidget(_page_head(self, title, sub))

    @contextmanager
    def _quiet(self):
        """刷新期间掐断回写：控件 setValue 触发的信号只改界面，不再写回 params"""
        self._syncing = True
        try:
            yield
        finally:
            self._syncing = False

    def _set(self, key, value):
        if not self._syncing:
            self._hub.set_param(key, value)

    def add_scrolled_body(self):
        """建一个带滚动的 body 挂进 outer，返回 (body, 内容布局)"""
        body = QWidget(self)
        _transparent(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(28, 12, 28, 24)
        lay.setSpacing(12)
        self.outer.addWidget(_make_scroll(body, self), 1)
        return body, lay


# ==================== 子页 1：增强任务 ====================

class EnhanceTaskPage(VePage):
    """增强任务：文件队列 + 统计条 + 批量运行 + ffmpeg 终端"""

    COLS = ("", "文件", "分辨率", "帧率", "时长", "大小", "状态", "进度")

    def __init__(self, hub, parent=None):
        super().__init__(hub, "增强任务",
                        "批量添加录像，按「滤镜调参 / 编码输出」的当前参数逐个跑 ffmpeg；"
                        "单条失败只标红该行，不中断整批", parent)
        self.setObjectName("veTaskPage")
        body = QWidget(self)
        _transparent(body)
        bl = QVBoxLayout(body)
        bl.setContentsMargins(28, 12, 28, 20)
        bl.setSpacing(12)

        self.strip = _StatStrip(["队列中", "进行中", "已完成", "失败", "总进度"], body)
        bl.addWidget(self.strip)

        card, cv, right = _card(body, "任务列表",
                               "勾选的行才会被跑；「开始增强」期间参数改动不影响已启动的批次")
        self.btn_add = PushButton(FluentIcon.FOLDER_ADD, "添加文件", card)
        self.btn_add.clicked.connect(self._on_add_files)
        right.addWidget(self.btn_add)
        self.btn_add_dir = PushButton(FluentIcon.FOLDER, "添加目录", card)
        self.btn_add_dir.clicked.connect(self._on_add_dir)
        right.addWidget(self.btn_add_dir)
        self.btn_check_all = PushButton(FluentIcon.CHECKBOX, "全选/全不选", card)
        self.btn_check_all.clicked.connect(self._on_toggle_all)
        right.addWidget(self.btn_check_all)
        self.btn_drop = PushButton(FluentIcon.DELETE, "移除所选", card)
        self.btn_drop.clicked.connect(self._on_drop_checked)
        right.addWidget(self.btn_drop)
        self.btn_clear = PushButton(FluentIcon.CLOSE, "清空", card)
        self.btn_clear.clicked.connect(self._on_clear)
        right.addWidget(self.btn_clear)

        self.table = TableWidget(card)
        self.table.setColumnCount(len(self.COLS))
        self.table.setHorizontalHeaderLabels(list(self.COLS))
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        hh = self.table.horizontalHeader()
        # 列宽：勾选固定、文件名吃满剩余、其余按内容自适应（同 UploadListWork 口径）
        for col, mode in ((0, QHeaderView.ResizeMode.Fixed),
                          (1, QHeaderView.ResizeMode.Stretch),
                          (7, QHeaderView.ResizeMode.Fixed)):
            hh.setSectionResizeMode(col, mode)
        for col in (2, 3, 4, 5, 6):
            hh.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        hh.setStretchLastSection(False)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(7, 150)
        self.table.setMinimumHeight(220)
        # qfw TableItemDelegate 对 CheckStateRole 原生绘制 Fluent 勾选框，零子控件
        self.table.itemChanged.connect(self._on_item_changed)
        cv.addWidget(self.table, 1)

        self.lbl_warn = _hint(card, "")
        self.lbl_warn.hide()
        cv.addWidget(self.lbl_warn)

        self.progress = ProgressBar(card)
        self.progress.setRange(0, 100)
        self.progress.setFixedHeight(4)
        self.progress.hide()
        cv.addWidget(self.progress)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.btn_start = PrimaryPushButton(FluentIcon.PLAY, "开始增强", card)
        self.btn_start.clicked.connect(self._on_start)
        bottom.addWidget(self.btn_start)
        self.btn_stop = PushButton(FluentIcon.CLOSE, "停止", card)
        self.btn_stop.setToolTip("中断当前 ffmpeg 子进程并删除半成品输出")
        self.btn_stop.clicked.connect(self._on_stop)
        bottom.addWidget(self.btn_stop)
        bottom.addStretch(1)
        self.btn_open_out = PushButton(FluentIcon.FOLDER, "打开输出目录", card)
        self.btn_open_out.clicked.connect(self._on_open_out)
        bottom.addWidget(self.btn_open_out)
        self.btn_copy_cmd = PushButton(FluentIcon.COPY, "复制命令行", card)
        self.btn_copy_cmd.setToolTip("把选中行的完整 ffmpeg 命令复制到剪贴板；没有选中时用第一行")
        self.btn_copy_cmd.clicked.connect(self._on_copy_cmd)
        bottom.addWidget(self.btn_copy_cmd)
        cv.addLayout(bottom)

        out_card = CardWidget(body)
        ol = QVBoxLayout(out_card)
        ol.setContentsMargins(16, 14, 16, 14)
        ol.setSpacing(8)
        ol.addWidget(CaptionLabel("ffmpeg 输出:", out_card))
        # height=None：终端吃满卡片剩余高度，与单杆视频/批量整理同一套手感
        self.log_view = _make_terminal(out_card, None)
        ol.addWidget(self.log_view, 1)

        self.splitter = _make_vsplitter(body, card, out_card)
        bl.addWidget(self.splitter, 1)
        self.outer.addWidget(body, 1)

        hub.paramsChanged.connect(self.refresh)
        # stateChanged 单独接 _sync_buttons：预警文案只由 params 决定，但开始按钮
        # 还要看 ffmpeg 定位成没成、探测/增强 worker 在不在跑。少接这一条，用户
        # 去「环境检测」把 ffmpeg 点通了回来，开始按钮仍然是灰的。
        hub.stateChanged.connect(self._sync_buttons)
        self.rebuild()
        self.refresh()

    # ---------- 表格 ----------

    def rebuild(self):
        """按 hub.rows 全量重建表格

        全量而不是增量 diff：队列通常几十条，重建比维护行号映射更不容易错位。
        重建期间屏蔽 itemChanged，否则每行 setCheckState 都会触发一次全表扫描。
        """
        rows = self._hub.rows
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(len(rows))
            for i, r in enumerate(rows):
                chk = QTableWidgetItem()
                chk.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                chk.setCheckState(Qt.CheckState.Checked if r["checked"]
                                  else Qt.CheckState.Unchecked)
                self.table.setItem(i, 0, chk)
                name = QTableWidgetItem(os.path.basename(r["src"]))
                name.setToolTip(r["src"])
                name.setFont(_term_font())
                self.table.setItem(i, 1, name)
                for col in range(2, len(self.COLS)):
                    it = QTableWidgetItem("")
                    if col in (3, 4, 5, 7):
                        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.table.setItem(i, col, it)
                self._fill_row(i, r)
        finally:
            self.table.blockSignals(False)
        self._refresh_stats()

    def _fill_row(self, i, r):
        """把一行的探测结果与运行状态写进单元格（不重建行，只改内容）"""
        info = r.get("info") or {}
        ok = bool(info.get("ok"))
        cells = {
            2: f"{info.get('width')}x{info.get('height')}" if ok else "—",
            3: f"{info.get('fps'):.2f}" if ok and info.get("fps") else "—",
            4: _fmt_dur(info.get("duration")) if ok else "—",
            5: _fmt_size(info.get("size") or (os.path.getsize(r["src"])
                                               if os.path.isfile(r["src"]) else 0)),
        }
        for col, text in cells.items():
            it = self.table.item(i, col)
            if it is not None:
                it.setText(text)
                it.setFont(_term_font())

        text, color = _STATES.get(r["state"], (r["state"], _C_MUTED))
        if r["state"] == "failed" and r.get("reason"):
            text = f"失败：{r['reason']}"
        st = self.table.item(i, 6)
        if st is not None:
            st.setText(text)
            st.setToolTip(r.get("reason") or "")
            st.setForeground(color)

        pg = self.table.item(i, 7)
        if pg is not None:
            if r["state"] == "running":
                pg.setText(f"{r['pct']}% · {r['speed']:.1f}x")
            elif r["state"] == "done":
                pg.setText(f"100% · 用时 {_fmt_dur(r['elapsed'])}")
            elif r["state"] == "failed":
                pg.setText(r.get("reason") or "失败")
            elif r["state"] == "skipped":
                pg.setText("输出已存在")
            else:
                pg.setText("—")
            pg.setToolTip(r.get("dst") or "")
            pg.setForeground(_C_SUCCESS if r["state"] == "done"
                             else (_C_DANGER if r["state"] == "failed" else _C_MUTED))

    def refresh_row(self, i):
        if 0 <= i < self.table.rowCount():
            self._fill_row(i, self._hub.rows[i])

    def _refresh_stats(self):
        rows = self._hub.rows
        queued = sum(1 for r in rows if r["state"] in ("queued", "ready", "probing"))
        running = sum(1 for r in rows if r["state"] == "running")
        done = sum(1 for r in rows if r["state"] in ("done", "skipped"))
        failed = sum(1 for r in rows if r["state"] == "failed")
        # r["pct"] 已是 0..100，再乘 100 会把单条 16% 显示成总进度 1600%
        pct = sum(r["pct"] for r in rows) // len(rows) if rows else 0
        self.strip.set(0, queued, _C_ACCENT if queued else None)
        self.strip.set(1, running, _C_ACCENT if running else None)
        self.strip.set(2, done, _C_SUCCESS if done else None)
        self.strip.set(3, failed, _C_DANGER if failed else None)
        self.strip.set(4, f"{pct}%" if rows else "—", _C_ACCENT if pct else None)

    def _on_item_changed(self, item):
        """勾选列翻转 → 写回 hub.rows（其余列不可编辑，不会走到这里）"""
        if item.column() != 0 or self._syncing:
            return
        i = item.row()
        if 0 <= i < len(self._hub.rows):
            self._hub.rows[i]["checked"] = (
                item.checkState() == Qt.CheckState.Checked)
        self._refresh_stats()

    def refresh(self):
        """参数变了：只重算耗时预警与开始按钮，表格内容不动"""
        p = self._hub.params
        warn = []
        if p.get("interp_on"):
            warn.append("已开启补帧：运动补偿插帧在 1080p 上约 0.1x 实时，"
                        "10 分钟视频要跑 100 分钟；且高速小球容易插帧撕裂")
        if p.get("scale_on") and p.get("scale_mode") == "ratio" \
                and float(p.get("scale_ratio") or 0) >= 2:
            warn.append(f"放大 {p.get('scale_ratio')}x：像素数按平方增长，"
                        "编码耗时约为原分辨率的 4 倍")
        if p.get("rate_mode") == "size":
            spec = ve.ENCODERS.get(p.get("encoder"), {})
            if not spec.get("two_pass"):
                warn.append(f"{p.get('encoder')} 不支持两遍编码，"
                            "「目标体积」会退化为单遍定码率，实际体积会有偏差")
        self.lbl_warn.setText("⚠ " + "；".join(warn))
        self.lbl_warn.setVisible(bool(warn))
        self._sync_buttons()

    def _sync_buttons(self):
        busy = self._hub.worker_running(W_ENHANCE)
        probing = self._hub.worker_running(W_PROBE)
        has_rows = bool(self._hub.rows)
        checked = any(r["checked"] for r in self._hub.rows)
        # 增强中不放开开始按钮：同一个输出文件被两个 ffmpeg 同时写会互相截断
        self.btn_start.setEnabled(has_rows and checked and not busy and not probing
                                  and self._hub.ready())
        self.btn_stop.setEnabled(busy)
        for w in (self.btn_add, self.btn_add_dir, self.btn_drop, self.btn_clear,
                  self.btn_check_all):
            w.setEnabled(not busy)
        self.btn_open_out.setEnabled(has_rows)
        self.btn_copy_cmd.setEnabled(has_rows)

    def append_line(self, text):
        """往终端追加一行；worker 的 line 信号直连这里"""
        self.log_view.append(str(text))

    # ---------- 添加 / 移除 ----------

    def _on_add_files(self):
        self._hub.pick_paths("选择要增强的视频", self._add_paths, mode="files",
                             filt=VIDEO_FILTER)

    def _on_add_dir(self):
        self._hub.pick_paths("选择视频所在目录，会递归收集常见视频后缀",
                             self._add_dir, mode="dir")

    def _add_dir(self, paths):
        """目录 → 递归收集视频文件（右键整理会按日期建子目录，只扫一层会漏）"""
        exts = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".ts", ".webm", ".m4v")
        found = []
        for d in paths:
            for cur, _dirs, files in os.walk(d):
                for name in sorted(files):
                    if name.lower().endswith(exts):
                        found.append(os.path.join(cur, name))
        if not found:
            self._hub.info("该目录下没有找到常见后缀的视频文件", "warning")
            return
        self._add_paths(found)

    def _add_paths(self, paths):
        """去重后追加进队列，并起后台 ffprobe 填分辨率/帧率/时长

        探测必须离开 UI 线程：ffprobe 探一个几百 MB 的录像要几十毫秒，
        几十条一起加会把整个 FluentWindow 冻住。
        """
        have = {r["src"] for r in self._hub.rows}
        added = []
        for p in paths or []:
            p = os.path.normpath(p)
            if p in have or not os.path.isfile(p):
                continue
            have.add(p)
            added.append({"src": p, "info": None, "state": "queued",
                          "reason": "", "dst": "", "pct": 0, "speed": 0.0,
                          "elapsed": 0.0, "checked": True})
        if not added:
            self._hub.info("没有新增文件：都是重复添加，或文件已不存在", "info")
            return
        first = len(self._hub.rows)
        self._hub.rows.extend(added)
        self._hub.last_dir = os.path.dirname(added[0]["src"])
        self.rebuild()
        self.append_line(f"▶ 已加入 {len(added)} 个文件，开始探测…")
        self._hub.start(W_PROBE, "probe", {
            "items": [r["src"] for r in added],
            "ffprobe": self._hub.ffprobe,
        }, [("probed", self._on_probed), ("finished_ok", self._on_probe_done)],
            row_map=list(range(first, first + len(added))))

    def _on_probed(self, i, info):
        """探测结果逐条回传（不攒到最后：大目录要几十秒，干等没有反馈）"""
        row = self._hub.row_of(W_PROBE, i)
        if row is None:
            return
        self._hub.rows[row]["info"] = info or {}
        self._hub.rows[row]["state"] = "ready" if (info or {}).get("ok") else "failed"
        self._hub.rows[row]["reason"] = "" if (info or {}).get("ok") \
            else (info or {}).get("reason", "")
        self.refresh_row(row)
        self._refresh_stats()

    def _on_probe_done(self, _summary):
        self._hub.release(W_PROBE)
        self._sync_buttons()
        self._refresh_stats()

    def _on_toggle_all(self):
        target = not all(r["checked"] for r in self._hub.rows) if self._hub.rows else True
        self.table.blockSignals(True)
        try:
            for i, r in enumerate(self._hub.rows):
                r["checked"] = target
                it = self.table.item(i, 0)
                if it is not None:
                    it.setCheckState(Qt.CheckState.Checked if target
                                     else Qt.CheckState.Unchecked)
        finally:
            self.table.blockSignals(False)
        self._refresh_stats()

    def _on_drop_checked(self):
        keep = [r for r in self._hub.rows if not r["checked"]]
        if len(keep) == len(self._hub.rows):
            self._hub.info("先勾选要移除的行", "info")
            return
        self._hub.rows[:] = keep
        self.rebuild()

    def _on_clear(self):
        if not self._hub.rows:
            return
        box = MessageBox("清空任务列表", "确定移除全部行？不会删除任何已生成的输出文件。", self)
        box.yesButton.setText("清空")
        box.cancelButton.setText("取消")
        if box.exec():
            self._hub.rows[:] = []
            self.rebuild()

    # ---------- 运行 ----------

    def _on_start(self):
        """开始增强：busy 守卫 → 落配置 → 置运行态 → 拉起线程"""
        hub = self._hub
        if hub.worker_running(W_ENHANCE):
            hub.info("增强任务正在运行中，请等待完成或先停止", "warning")
            return
        if not hub.ready():
            hub.info(hub.ffmpeg_reason(), "error", duration=5000)
            hub.switchTo(hub.env_page)
            return
        run_rows = [i for i, r in enumerate(hub.rows) if r["checked"]]
        if not run_rows:
            hub.info("没有勾选任何行", "warning")
            return
        for i in run_rows:
            hub.rows[i].update(state="queued", reason="", dst="", pct=0,
                               speed=0.0, elapsed=0.0)
            hub.rows[i].pop("_t0", None)   # 上一轮的起跑时刻不能带进这一轮
        self.rebuild()
        # 先落配置再起线程：跑失败了也能保住用户刚调好的一整套参数
        hub.save_now()
        p = dict(hub.params)
        self.progress.setValue(0)
        self.progress.show()
        self.append_line("")
        self.append_line(f"▶ 开始批量增强：{len(run_rows)} 个文件 · "
                         f"{ve.summarize_params(p)}")
        hub.start(W_ENHANCE, "enhance", {
            "items": [hub.rows[i]["src"] for i in run_rows],
            "infos": [hub.rows[i]["info"] for i in run_rows],
            "params": p,
            "ffmpeg": hub.ffmpeg,
            "ffprobe": hub.ffprobe,
            "videos_dir": hub.videos_dir,
        }, [("progress", self._on_progress), ("item_done", self._on_item_done),
            ("item_failed", self._on_item_failed),
            ("finished_ok", self._on_batch_done)], row_map=run_rows)
        self._sync_buttons()
        hub.log(f"[画质提升] 开始批量增强 {len(run_rows)} 个文件：{ve.summarize_params(p)}")

    def _on_progress(self, i, pct, speed):
        row = self._hub.row_of(W_ENHANCE, i)
        if row is None:
            return
        r = self._hub.rows[row]
        # 单条耗时从「这一条真正开始编码」算起。批量 worker 是顺序跑的，
        # 拿批次启动时刻当基准会把前面几条的时间也算进后面的 elapsed。
        r.setdefault("_t0", time.time())
        r["state"], r["pct"], r["speed"] = "running", int(pct), float(speed)
        self.refresh_row(row)
        self.progress.setValue(int(pct))
        self._refresh_stats()

    def _on_item_done(self, i, src, dst):
        """dst 为空串 = 「输出已存在」被跳过，不是失败（worker 的第三态）"""
        row = self._hub.row_of(W_ENHANCE, i)
        if row is None:
            return
        r = self._hub.rows[row]
        r["state"] = "done" if dst else "skipped"
        r["dst"] = dst
        r["pct"] = 100
        r["reason"] = ""
        # 跳过的行从没收到 progress，没有 _t0 → 耗时就是 0，符合事实
        r["elapsed"] = round(time.time() - r["_t0"], 1) if "_t0" in r else 0.0
        self.refresh_row(row)
        self._refresh_stats()
        if dst:
            self._hub.add_history(r)

    def _on_item_failed(self, i, reason):
        row = self._hub.row_of(W_ENHANCE, i)
        if row is None:
            return
        r = self._hub.rows[row]
        r.update(state="failed", reason=str(reason), pct=0)
        r["elapsed"] = round(time.time() - r["_t0"], 1) if "_t0" in r else 0.0
        self.refresh_row(row)
        self._refresh_stats()

    def _on_batch_done(self, summary):
        s = summary or {}
        self._hub.release(W_ENHANCE)
        self.progress.hide()
        self._sync_buttons()
        self._refresh_stats()
        self._hub.log(f"[画质提升] 批量结束：成功 {s.get('ok', 0)} · "
                      f"跳过 {s.get('skipped', 0)} · 失败 {s.get('failed', 0)}")
        if s.get("failed"):
            self._hub.info(f"批量结束，{s['failed']} 条失败，详见终端", "warning",
                           duration=6000)
        else:
            self._hub.info(f"批量增强完成：成功 {s.get('ok', 0)} · "
                           f"跳过 {s.get('skipped', 0)}", "success", duration=5000)

    def _on_stop(self):
        """二次确认后中断；ffmpeg 在子进程边界响应，先禁用按钮防重入"""
        w = self._hub.worker(W_ENHANCE)
        if w is None or not w.isRunning():
            return
        box = MessageBox("停止增强", "正在跑的任务会被中断，已生成的半成品输出会被删除。确定停止？", self)
        box.yesButton.setText("确定停止")
        box.cancelButton.setText("继续")
        if not box.exec():
            return
        if not w.isRunning():
            return
        self.append_line("… 正在停止，终止 ffmpeg 子进程...")
        self.btn_stop.setEnabled(False)
        w.requestInterruption()

    # ---------- 产物 ----------

    def selected_row(self):
        """当前选中行号；没选中就用第一条（复制命令/打开目录都要有个落点）

        不带下划线：编码页也要调它来预览命令，是跨页公开接口。
        """
        for i in self.table.selectionModel().selectedRows():
            return i.row()
        return 0 if self._hub.rows else None

    def _on_open_out(self):
        i = self.selected_row()
        r = self._hub.rows[i] if i is not None and i < len(self._hub.rows) else None
        dst = (r or {}).get("dst") or ""
        folder = os.path.dirname(dst) if dst else ve.output_dir(
            self._hub.params, (r or {}).get("src", ""), self._hub.videos_dir)
        if folder and os.path.isdir(folder):
            try:
                os.startfile(folder)
            except Exception:
                pass
        else:
            self._hub.info(f"输出目录还不存在：{folder or '未确定'}", "info")

    def _on_copy_cmd(self):
        i = self.selected_row()
        if i is None:
            return
        cmd = self._hub.preview_command(i)
        if not cmd:
            return
        QApplication.clipboard().setText(cmd)
        self._hub.info("完整 ffmpeg 命令已复制到剪贴板", "success")
        self.append_line(f"$ {cmd}")


# ==================== 子页 2：滤镜调参 ====================

class FilterPage(VePage):
    """滤镜调参：预设 + 去噪 / 锐化 / 放大 / 色彩 / 补帧 + -vf 实时预览"""

    _RATIOS = [(1.5, "1.5x"), (2.0, "2x"), (3.0, "3x"), (4.0, "4x")]
    _INTERP_MODES = [("mci", "mci"), ("blend", "blend")]

    def __init__(self, hub, parent=None):
        super().__init__(hub, "滤镜调参",
                        "滤镜固定顺序：去噪 → 放大 → 锐化 → 色彩 → 补帧。"
                        "先降噪再放大，避免把噪声一起放大；锐化放在放大之后，锐的是插值边缘。",
                        parent)
        self.setObjectName("veFilterPage")
        body, lay = self.add_scrolled_body()

        # ---- 预设 ----
        card, cv, right = _card(body, "预设", "选预设会覆盖下方全部开关与数值；再手动改则自动切到「自定义」")
        self.lbl_preset = CaptionLabel("", card)
        right.addWidget(self.lbl_preset)
        row = QHBoxLayout()
        row.setSpacing(8)
        for name, spec in ve.PRESETS.items():
            b = PushButton(spec["label"], card)
            b.setToolTip(spec["desc"])
            b.clicked.connect(lambda _=False, n=name: self._on_preset(n))
            row.addWidget(b)
        row.addStretch(1)
        cv.addLayout(row)
        lay.addWidget(card)

        # ---- 去噪 / 锐化（并排两卡） ----
        pair = QHBoxLayout()
        pair.setSpacing(12)
        pair.addWidget(self._build_denoise(body), 1)
        pair.addWidget(self._build_sharpen(body), 1)
        lay.addLayout(pair)

        # ---- 放大 / 色彩 ----
        pair2 = QHBoxLayout()
        pair2.setSpacing(12)
        pair2.addWidget(self._build_scale(body), 1)
        pair2.addWidget(self._build_color(body), 1)
        lay.addLayout(pair2)

        lay.addWidget(self._build_interp(body))
        lay.addWidget(self._build_preview(body))
        lay.addStretch(1)

        hub.paramsChanged.connect(self.refresh)
        hub.stateChanged.connect(self.refresh)
        self.refresh()

    def _on_preset(self, name):
        """整套覆盖参数：走 hub.apply_preset 而不是逐个 set_param，
        否则中途会广播出十几份「半套预设」的中间态给预览行"""
        self._hub.apply_preset(name)
        self._hub.info(f"已套用预设：{ve.PRESETS[name]['label']}", "success")

    def _switch(self, card, key):
        sw = SwitchButton("", card)
        sw.setChecked(bool(self._hub.params.get(key)))
        sw.checkedChanged.connect(lambda on, k=key: self._set(k, bool(on)))
        sw.setOnText("")
        sw.setOffText("")
        return sw

    def _build_denoise(self, parent):
        card, cv, right = _card(parent, "去噪")
        self.sw_denoise = self._switch(card, "denoise_on")
        right.addWidget(self.sw_denoise)

        r = _field(card, "算法")
        self.cmb_denoise = _combo(card, ve.DENOISE_ALGOS)
        self.cmb_denoise.currentIndexChanged.connect(
            lambda _i: self._set("denoise_algo",
                                 _combo_val(self.cmb_denoise, "hqdn3d")))
        r.addWidget(self.cmb_denoise, 1)
        cv.addLayout(r)
        cv.addWidget(_hint(card, "nlmeans / bm3d 质量高但极慢；"
                                 "bm3d 需要 -filter_complex，本批处理链路不支持，选中会在开始时直接报错"))

        self.sld_luma = _FSlider(0, 5, 0.1, parent=card)
        self.sld_luma.valueChanged.connect(lambda v: self._set("denoise_luma", v))
        self.sld_chroma = _FSlider(0, 5, 0.1, parent=card)
        self.sld_chroma.valueChanged.connect(lambda v: self._set("denoise_chroma", v))
        self.sld_temporal = _FSlider(0, 20, 0.5, parent=card)
        self.sld_temporal.valueChanged.connect(
            lambda v: self._set("denoise_temporal", v))
        for label, w in (("亮度强度", self.sld_luma), ("色度强度", self.sld_chroma),
                         ("时序强度", self.sld_temporal)):
            r = _field(card, label)
            r.addWidget(w, 1)
            cv.addLayout(r)
        cv.addWidget(_hint(card, "时序强度过高会拖影，快速移动的球会糊尾"))
        self._denoise_widgets = (self.cmb_denoise, self.sld_luma, self.sld_chroma,
                                 self.sld_temporal)
        return card

    def _build_sharpen(self, parent):
        card, cv, right = _card(parent, "锐化")
        self.sw_sharpen = self._switch(card, "sharpen_on")
        right.addWidget(self.sw_sharpen)

        r = _field(card, "算法")
        self.cmb_sharpen = _combo(card, ve.SHARPEN_ALGOS)
        self.cmb_sharpen.currentIndexChanged.connect(
            lambda _i: self._set("sharpen_algo",
                                 _combo_val(self.cmb_sharpen, "cas")))
        r.addWidget(self.cmb_sharpen, 1)
        cv.addLayout(r)

        # cas 的 strength 上限是 1，量程给到 2 会让 ffmpeg 拒绝整条链
        self.sld_sharpen = _FSlider(0, 1, 0.05, parent=card)
        self.sld_sharpen.valueChanged.connect(lambda v: self._set("sharpen_amount", v))
        r = _field(card, "强度")
        r.addWidget(self.sld_sharpen, 1)
        cv.addLayout(r)
        self.lbl_sharpen_hint = _hint(card, "")
        cv.addWidget(self.lbl_sharpen_hint)

        r = _field(card, "USM 矩阵")
        self.spin_lx = CompactSpinBox(card)
        self.spin_lx.setRange(3, 23)
        self.spin_lx.valueChanged.connect(lambda v: self._set("usm_lx", int(v)))
        self.spin_ly = CompactSpinBox(card)
        self.spin_ly.setRange(3, 23)
        self.spin_ly.valueChanged.connect(lambda v: self._set("usm_ly", int(v)))
        self.sld_usm = _FSlider(0, 3, 0.05, parent=card)
        self.sld_usm.valueChanged.connect(lambda v: self._set("usm_amount", v))
        r.addWidget(self.spin_lx)
        r.addWidget(self.spin_ly)
        r.addWidget(self.sld_usm, 1)
        cv.addLayout(r)
        cv.addWidget(_hint(card, "两个整数是 unsharp 的 luma_msize_x/y，只有 unsharp 会读；"
                                 "滑块是强度，cas 用 sharpen_amount、unsharp 用 usm_amount"))
        cv.addWidget(_hint(card, "⚠ cas 强度上限 1，再锐只能换 unsharp+cas 靠 USM 加量；"
                                 "接近 1 时暗部易出振铃与颗粒放大，建议配合去噪一起用"))
        self._sharpen_widgets = (self.cmb_sharpen, self.sld_sharpen, self.spin_lx,
                                 self.spin_ly, self.sld_usm)
        return card

    def _build_scale(self, parent):
        card, cv, right = _card(parent, "放大 / 分辨率")
        self.sw_scale = self._switch(card, "scale_on")
        right.addWidget(self.sw_scale)

        r = _field(card, "目标")
        self.cmb_scale_mode = _combo(card, [("ratio", "按倍率"), ("height", "指定高度")], 150)
        self.cmb_scale_mode.currentIndexChanged.connect(
            lambda _i: self._set("scale_mode",
                                 _combo_val(self.cmb_scale_mode, "height")))
        r.addWidget(self.cmb_scale_mode)
        r.addStretch(1)
        cv.addLayout(r)

        r = _field(card, "倍率")
        self.cmb_ratio = _combo(card, self._RATIOS, 200)
        self.cmb_ratio.currentIndexChanged.connect(
            lambda _i: self._set("scale_ratio",
                                 float(_combo_val(self.cmb_ratio, 1.5))))
        r.addWidget(self.cmb_ratio)
        r.addStretch(1)
        cv.addLayout(r)

        r = _field(card, "输出高度")
        self.spin_height = CompactSpinBox(card)
        self.spin_height.setRange(240, 4320)
        self.spin_height.setSingleStep(2)
        self.spin_height.valueChanged.connect(
            lambda v: self._set("scale_height", int(v)))
        r.addWidget(self.spin_height)
        r.addWidget(_hint(card, "宽度按 scale=-2:H 自动取偶"))
        r.addStretch(1)
        cv.addLayout(r)

        r = _field(card, "插值算法")
        self.cmb_scale_algo = _combo(card, ve.SCALE_ALGOS)
        self.cmb_scale_algo.currentIndexChanged.connect(
            lambda _i: self._set("scale_algo",
                                 _combo_val(self.cmb_scale_algo, "lanczos")))
        r.addWidget(self.cmb_scale_algo, 1)
        cv.addLayout(r)
        self.lbl_scale_out = _hint(card, "")
        cv.addWidget(self.lbl_scale_out)
        self._scale_widgets = (self.cmb_scale_mode, self.cmb_ratio, self.spin_height,
                               self.cmb_scale_algo)
        return card

    def _build_color(self, parent):
        card, cv, right = _card(parent, "色彩 eq")
        self.sw_color = self._switch(card, "color_on")
        right.addWidget(self.sw_color)
        self.sld_contrast = _FSlider(0.5, 2.0, 0.01, 1.0, card)
        self.sld_contrast.valueChanged.connect(lambda v: self._set("contrast", v))
        self.sld_saturation = _FSlider(0.0, 3.0, 0.01, 1.0, card)
        self.sld_saturation.valueChanged.connect(lambda v: self._set("saturation", v))
        self.sld_brightness = _FSlider(-0.5, 0.5, 0.01, 0.0, card)
        self.sld_brightness.valueChanged.connect(lambda v: self._set("brightness", v))
        self.sld_gamma = _FSlider(0.1, 3.0, 0.01, 1.0, card)
        self.sld_gamma.valueChanged.connect(lambda v: self._set("gamma", v))
        for label, w in (("对比度", self.sld_contrast), ("饱和度", self.sld_saturation),
                         ("亮度", self.sld_brightness), ("伽马", self.sld_gamma)):
            r = _field(card, label)
            r.addWidget(w, 1)
            cv.addLayout(r)
        cv.addWidget(_hint(card, "台球录像常见偏灰，对比度 +0.03~0.06、饱和度 +0.05~0.10 "
                                 "通常就够；再高会失真"))
        self._color_widgets = (self.sld_contrast, self.sld_saturation,
                               self.sld_brightness, self.sld_gamma)
        return card

    def _build_interp(self, parent):
        card, cv, right = _card(parent, "补帧 minterpolate")
        self.sw_interp = self._switch(card, "interp_on")
        right.addWidget(self.sw_interp)
        r = QHBoxLayout()
        r.setSpacing(8)
        r.addWidget(BodyLabel("目标帧率", card))
        self.spin_fps = CompactSpinBox(card)
        self.spin_fps.setRange(12, 240)
        self.spin_fps.valueChanged.connect(lambda v: self._set("interp_fps", int(v)))
        r.addWidget(self.spin_fps)
        r.addWidget(BodyLabel("模式", card))
        self.cmb_interp = _combo(card, self._INTERP_MODES, 260)
        self.cmb_interp.currentIndexChanged.connect(
            lambda _i: self._set("interp_mode",
                                 _combo_val(self.cmb_interp, "mci")))
        r.addWidget(self.cmb_interp)
        r.addStretch(1)
        cv.addLayout(r)
        cv.addWidget(_hint(card, "⚠ 运动补偿插帧在 1080p 上大约 0.1x 实时速度，"
                                 "10 分钟视频要跑 100 分钟；且台球这种高速小球容易出现"
                                 "插帧撕裂。默认关闭，开启前请确认时长可接受。"))
        self._interp_widgets = (self.spin_fps, self.cmb_interp)
        return card

    def _build_preview(self, parent):
        card, cv, right = _card(parent, "滤镜链实时预览",
                               "参数一动即重算，这就是最终传给 ffmpeg 的 -vf 值")
        self.btn_copy_vf = PushButton(FluentIcon.COPY, "复制", card)
        self.btn_copy_vf.clicked.connect(self._on_copy_vf)
        right.addWidget(self.btn_copy_vf)
        self.btn_frame = PushButton(FluentIcon.PHOTO, "抽一帧预览", card)
        self.btn_frame.setToolTip("在指定时间点上各跑一次单帧 ffmpeg，一份取原帧、一份过完"
                                  "滤镜链，秒级出图，结果并排显示在「质量对比」子页")
        self.btn_frame.clicked.connect(self._on_preview)
        right.addWidget(self.btn_frame)

        self.vf_edit = LineEdit(card)
        self.vf_edit.setReadOnly(True)
        self.vf_edit.setFont(_term_font())
        self.vf_edit.setPlaceholderText("—")
        cv.addWidget(self.vf_edit)

        r = QHBoxLayout()
        r.setSpacing(8)
        r.addWidget(BodyLabel("预览时间点", card))
        self.spin_t = CompactSpinBox(card)
        self.spin_t.setRange(0, 86400)
        self.spin_t.setSuffix(" s")
        self.spin_t.setValue(5)
        r.addWidget(self.spin_t)
        self.lbl_cost = _hint(card, "")
        r.addWidget(self.lbl_cost, 1)
        cv.addLayout(r)
        return card

    def _on_copy_vf(self):
        QApplication.clipboard().setText(self.vf_edit.text())
        self._hub.info("-vf 值已复制到剪贴板", "success")

    def _on_preview(self):
        """抽一帧过链预览：不进队列、不写历史，纯调参辅助"""
        hub = self._hub
        if hub.worker_running(W_CMP):
            hub.info("质量对比线程正在忙，请稍等", "warning")
            return
        if not hub.ready():
            hub.info(hub.ffmpeg_reason(), "error", duration=5000)
            return
        src = hub.first_ok_source()
        if not src:
            hub.info("先在「增强任务」里添加至少一个能探测成功的视频", "warning")
            return
        info = hub.info_of(src)
        hub.start(W_CMP, "preview", {
            "src": src, "params": dict(hub.params), "info": info,
            "t": float(self.spin_t.value()),
            "ffmpeg": hub.ffmpeg, "ffprobe": hub.ffprobe,
        }, [("frames_ready", hub.compare_page.set_frames)])
        hub.task_page.append_line(f"▶ 抽帧预览 t={self.spin_t.value()}s: "
                                  f"{os.path.basename(src)}")
        hub.info("预览生成中，通常几秒…", "info")
        hub.switchTo(hub.compare_page)

    def refresh(self):
        p = self._hub.params
        with self._quiet():
            spec = ve.PRESETS.get(p.get("preset"))
            self.lbl_preset.setText(f"当前：{spec['label'] if spec else '自定义'}")
            self.sw_denoise.setChecked(bool(p.get("denoise_on")))
            _combo_set(self.cmb_denoise, p.get("denoise_algo"))
            self.sld_luma.setValue(p.get("denoise_luma", 0))
            self.sld_chroma.setValue(p.get("denoise_chroma", 0))
            self.sld_temporal.setValue(p.get("denoise_temporal", 0))
            self.sw_sharpen.setChecked(bool(p.get("sharpen_on")))
            _combo_set(self.cmb_sharpen, p.get("sharpen_algo"))
            self.sld_sharpen.setValue(p.get("sharpen_amount", 0))
            self.sld_usm.setValue(p.get("usm_amount", 0))
            self.spin_lx.setValue(int(p.get("usm_lx") or 5))
            self.spin_ly.setValue(int(p.get("usm_ly") or 5))
            self.sw_scale.setChecked(bool(p.get("scale_on")))
            _combo_set(self.cmb_scale_mode, p.get("scale_mode"))
            _combo_set(self.cmb_ratio, float(p.get("scale_ratio") or 1.5))
            self.spin_height.setValue(int(p.get("scale_height") or 1080))
            _combo_set(self.cmb_scale_algo, p.get("scale_algo"))
            self.sw_color.setChecked(bool(p.get("color_on")))
            self.sld_contrast.setValue(p.get("contrast", 1))
            self.sld_saturation.setValue(p.get("saturation", 1))
            self.sld_brightness.setValue(p.get("brightness", 0))
            self.sld_gamma.setValue(p.get("gamma", 1))
            self.sw_interp.setChecked(bool(p.get("interp_on")))
            self.spin_fps.setValue(int(p.get("interp_fps") or 60))
            _combo_set(self.cmb_interp, p.get("interp_mode"))

        # 开关决定整组控件能不能动：关掉的组留着可编辑会让人以为改了会生效
        for on, widgets in ((p.get("denoise_on"), self._denoise_widgets),
                            (p.get("sharpen_on"), self._sharpen_widgets),
                            (p.get("scale_on"), self._scale_widgets),
                            (p.get("color_on"), self._color_widgets),
                            (p.get("interp_on"), self._interp_widgets)):
            for w in widgets:
                w.setEnabled(bool(on))
        # 三种算法各有生效字段：cas 只看 sharpen_amount，unsharp 只看 usm_*，
        # unsharp+cas 两组都生效。判定必须由两个标志位组合出来——只按
        # 「字符串里有没有 cas」判，两级算法会被说成只有 cas 生效，
        # 用户拖 usm 滑块时界面上没有任何东西解释它为什么在动。
        algo = str(p.get("sharpen_algo") or "cas")
        has_usm, has_cas = "unsharp" in algo, "cas" in algo
        live = ([f"unsharp 的 usm_amount 与 {p.get('usm_lx')}×{p.get('usm_ly')} 矩阵"]
                if has_usm else []) + (["cas 的 sharpen_amount"] if has_cas else [])
        self.lbl_sharpen_hint.setText("当前生效：" + "、".join(live))
        on = bool(p.get("sharpen_on"))
        for w in (self.spin_lx, self.spin_ly, self.sld_usm):
            w.setEnabled(on and has_usm)
        self.sld_sharpen.setEnabled(on and has_cas)
        self.cmb_ratio.setEnabled(bool(p.get("scale_on"))
                                  and p.get("scale_mode") == "ratio")
        self.spin_height.setEnabled(bool(p.get("scale_on"))
                                    and p.get("scale_mode") == "height")

        src = self._hub.first_ok_source()
        info = self._hub.info_of(src) if src else None
        try:
            chain = ve.build_filter_chain(dict(p), info)
        except ve.EnhanceError as e:
            chain = f"无法生成滤镜链：{e}"
        self.vf_edit.setText(f'-vf "{chain}"')
        # 耗时的量级只由「是否放大 / 是否补帧」决定，给个数比什么都不说强
        cost = []
        if p.get("interp_on"):
            cost.append("补帧 ≈ 0.1x 实时")
        if p.get("scale_on"):
            h = ve.target_height(dict(p), info or {})
            src_h = int((info or {}).get("height") or 0)
            if src_h and h:
                cost.append(f"放大到 {h}p，像素数 ×{(h / src_h) ** 2:.1f}")
        self.lbl_cost.setText("预估：" + "，".join(cost) if cost else
                              "预估：≈ 1.4x 实时，即不放大也不补帧")
        self.btn_frame.setEnabled(self._hub.ready() and bool(src))


# ==================== 子页 3：编码输出 ====================

class EncodePage(VePage):
    """编码输出：编码器 / 码率模式 / 音频容器 / 输出命名 + 完整命令预览"""

    _PIX_FMTS = [("yuv420p", "yuv420p"),
                 ("yuv422p", "yuv422p"),
                 ("yuv444p", "yuv444p")]
    _OUT_MODES = [("source", "源文件同目录"), ("videos", "设置里的 videos_dir"),
                  ("custom", "自定义目录")]
    _ON_EXIST = [("skip", "跳过"), ("overwrite", "覆盖"), ("rename", "加序号")]

    def __init__(self, hub, parent=None):
        super().__init__(hub, "编码输出", "编码器、码率模式、音频与容器、输出位置与命名", parent)
        self.setObjectName("veEncodePage")
        body, lay = self.add_scrolled_body()

        pair = QHBoxLayout()
        pair.setSpacing(12)
        pair.addWidget(self._build_encoder(body), 1)
        pair.addWidget(self._build_rate(body), 1)
        lay.addLayout(pair)

        pair2 = QHBoxLayout()
        pair2.setSpacing(12)
        pair2.addWidget(self._build_audio(body), 1)
        pair2.addWidget(self._build_output(body), 1)
        lay.addLayout(pair2)

        lay.addWidget(self._build_cmd(body))
        lay.addStretch(1)

        hub.paramsChanged.connect(self.refresh)
        hub.stateChanged.connect(self.refresh)
        self.refresh()

    def _build_encoder(self, parent):
        card, cv, _right = _card(parent, "视频编码器",
                                "可用项按「环境检测」的探测结果动态置灰")
        r = _field(card, "编码器")
        self.cmb_encoder = _combo(card, [(k, v["label"]) for k, v in ve.ENCODERS.items()])
        self.cmb_encoder.currentIndexChanged.connect(self._on_encoder)
        r.addWidget(self.cmb_encoder, 1)
        cv.addLayout(r)

        r = _field(card, "preset")
        self.cmb_preset = _combo(card, [])
        self.cmb_preset.currentIndexChanged.connect(
            lambda _i: self._set("codec_preset",
                                 str(_combo_val(self.cmb_preset, ""))))
        r.addWidget(self.cmb_preset, 1)
        cv.addLayout(r)
        self.lbl_preset_hint = _hint(card, "")
        cv.addWidget(self.lbl_preset_hint)

        r = _field(card, "码率模式")
        self.cmb_rate = _combo(card, [("crf", "CRF"),
                                      ("size", "目标体积")], 240)
        self.cmb_rate.currentIndexChanged.connect(
            lambda _i: self._set("rate_mode", _combo_val(self.cmb_rate, "crf")))
        r.addWidget(self.cmb_rate)
        r.addStretch(1)
        cv.addLayout(r)
        self.lbl_enc_state = _hint(card, "")
        cv.addWidget(self.lbl_enc_state)
        return card

    def _on_encoder(self, _i):
        """换编码器要重建 preset 列表：x264 是 ultrafast…veryslow，nvenc 是 p1…p7，
        传错名字 ffmpeg 直接拒绝启动"""
        name = _combo_val(self.cmb_encoder, "libx264")
        spec = ve.ENCODERS.get(name, {})
        cur = self.cmb_preset.currentData()
        self.cmb_preset.clear()
        for p in spec.get("presets", []):
            self.cmb_preset.addItem(str(p), userData=str(p))
        _combo_set(self.cmb_preset, cur if cur in (spec.get("presets") or []) else "")
        self._set("encoder", name)

    def _build_rate(self, parent):
        card, cv, right = _card(parent, "质量 / 体积")
        self.lbl_rate_tag = CaptionLabel("", card)
        right.addWidget(self.lbl_rate_tag)

        self.sld_crf = _FSlider(0, 51, 1, 18, card)
        self.sld_crf.valueChanged.connect(lambda v: self._set("crf", int(v)))
        r = _field(card, "CRF / CQ")
        r.addWidget(self.sld_crf, 1)
        cv.addLayout(r)
        self.lbl_crf_hint = _hint(card, "")
        cv.addWidget(self.lbl_crf_hint)

        r = _field(card, "目标体积")
        self.spin_mb = CompactSpinBox(card)
        self.spin_mb.setRange(1, 100000)
        self.spin_mb.setSuffix(" MB")
        self.spin_mb.valueChanged.connect(lambda v: self._set("target_mb", int(v)))
        r.addWidget(self.spin_mb)
        self.lbl_kbps = _hint(card, "")
        r.addWidget(self.lbl_kbps, 1)
        cv.addLayout(r)
        cv.addWidget(_hint(card, "码率算式 kbps = MB × 8192 × 0.98 / 时长；"
                                 "第一遍 -an -f null 只统计不产出，第二遍带音频。"
                                 "两种模式互斥，未启用的那一组置灰。"))
        return card

    def _build_audio(self, parent):
        card, cv, _right = _card(parent, "音频与容器")
        r = _field(card, "音频")
        self.cmb_audio = _combo(card, ve.AUDIO_MODES)
        self.cmb_audio.currentIndexChanged.connect(
            lambda _i: self._set("audio", _combo_val(self.cmb_audio, "copy")))
        r.addWidget(self.cmb_audio, 1)
        cv.addLayout(r)

        r = _field(card, "音频码率")
        self.spin_ab = CompactSpinBox(card)
        self.spin_ab.setRange(32, 512)
        self.spin_ab.setSingleStep(32)
        self.spin_ab.setSuffix(" kbps")
        self.spin_ab.valueChanged.connect(lambda v: self._set("audio_bitrate", int(v)))
        r.addWidget(self.spin_ab)
        r.addWidget(_hint(card, "仅 AAC 重编码生效"))
        r.addStretch(1)
        cv.addLayout(r)

        r = _field(card, "容器")
        self.cmb_container = _combo(card, ve.CONTAINERS, 260)
        self.cmb_container.currentIndexChanged.connect(
            lambda _i: self._set("container", _combo_val(self.cmb_container, "mp4")))
        r.addWidget(self.cmb_container)
        self.sw_faststart = SwitchButton("", card)
        self.sw_faststart.setOnText("")
        self.sw_faststart.setOffText("")
        self.sw_faststart.setToolTip("+faststart：把 moov 挪到文件头，便于网络边下边播")
        self.sw_faststart.checkedChanged.connect(
            lambda on: self._set("faststart", bool(on)))
        r.addWidget(self.sw_faststart)
        r.addWidget(_hint(card, "+faststart，仅对 mp4 生效"))
        r.addStretch(1)
        cv.addLayout(r)

        r = _field(card, "像素格式")
        self.cmb_pix = _combo(card, self._PIX_FMTS)
        self.cmb_pix.currentIndexChanged.connect(
            lambda _i: self._set("pix_fmt", _combo_val(self.cmb_pix, "yuv420p")))
        r.addWidget(self.cmb_pix, 1)
        cv.addLayout(r)
        cv.addWidget(_hint(card, "源为 10bit/422 时强制转 8bit 420，否则很多播放器黑屏"))
        return card

    def _build_output(self, parent):
        card, cv, _right = _card(parent, "输出位置与命名")
        r = _field(card, "输出目录")
        self.cmb_out_mode = _combo(card, self._OUT_MODES, 200)
        self.cmb_out_mode.currentIndexChanged.connect(
            lambda _i: self._set("out_mode", _combo_val(self.cmb_out_mode, "source")))
        r.addWidget(self.cmb_out_mode)
        self.btn_pick_out = ToolButton(FluentIcon.FOLDER, card)
        self.btn_pick_out.setToolTip("浏览自定义输出目录")
        self.btn_pick_out.clicked.connect(self._on_pick_out)
        r.addWidget(self.btn_pick_out)
        r.addStretch(1)
        cv.addLayout(r)

        r = _field(card, "自定义路径")
        self.out_dir_edit = LineEdit(card)
        self.out_dir_edit.setPlaceholderText("out_mode=自定义 时生效")
        self.out_dir_edit.setFont(_term_font())
        self.out_dir_edit.textChanged.connect(
            lambda t: self._set("out_dir", t.strip()))
        r.addWidget(self.out_dir_edit, 1)
        cv.addLayout(r)

        r = _field(card, "命名模板")
        self.tpl_edit = LineEdit(card)
        self.tpl_edit.setFont(_term_font())
        self.tpl_edit.textChanged.connect(lambda t: self._set("name_tpl", t))
        r.addWidget(self.tpl_edit, 1)
        cv.addLayout(r)
        cv.addWidget(_hint(card, "可用占位符：" + " ".join(ve.NAME_PLACEHOLDERS)
                                 + "；{suffix} 在放大生效时自动为 _1080p 这样的标记"))
        self.lbl_tpl_out = _hint(card, "")
        self.lbl_tpl_out.setFont(_term_font())
        cv.addWidget(self.lbl_tpl_out)

        r = _field(card, "同名已存在")
        self.cmb_exist = _combo(card, self._ON_EXIST, 150)
        self.cmb_exist.currentIndexChanged.connect(
            lambda _i: self._set("on_exist", _combo_val(self.cmb_exist, "skip")))
        r.addWidget(self.cmb_exist)
        r.addStretch(1)
        cv.addLayout(r)
        cv.addWidget(_hint(card, "输出路径若与源文件本身重合，引擎会强制加序号让开——"
                                 "ffmpeg 会一边读一边截断自己的输入，源片会直接毁掉"))
        return card

    def _on_pick_out(self):
        self._hub.pick_paths("选择输出目录", self._on_out_dir, mode="dir")

    def _on_out_dir(self, paths):
        if paths:
            self.out_dir_edit.setText(paths[0])      # textChanged 会写回 params
            _combo_set(self.cmb_out_mode, "custom")
            self._set("out_mode", "custom")

    def _build_cmd(self, parent):
        card, cv, right = _card(parent, "最终命令预览",
                               "取任务列表里选中行的真实路径拼出来，没选中就用第一行")
        self.btn_copy = PushButton(FluentIcon.COPY, "复制完整命令", card)
        self.btn_copy.clicked.connect(self._on_copy)
        right.addWidget(self.btn_copy)
        self.cmd_view = _make_terminal(card, None)
        self.cmd_view.setMinimumHeight(140)
        self.cmd_view.setMaximumHeight(240)
        cv.addWidget(self.cmd_view)
        cv.addWidget(_hint(card, "-progress pipe:1 -nostats 是进度解析的关键：ffmpeg 会输出"
                                 "机器可读的 out_time_ms= / frame= / speed= 键值行，"
                                 "用 out_time_ms/1e6/总时长 算百分比，比正则刮 time= 稳"))
        return card

    def _on_copy(self):
        text = self.cmd_view.toPlainText()
        if text.strip():
            QApplication.clipboard().setText(text)
            self._hub.info("完整命令已复制到剪贴板", "success")

    def refresh(self):
        hub = self._hub
        p = hub.params
        caps = hub.caps
        tested = (caps or {}).get("tests") or {}
        compiled = set((caps or {}).get("encoders") or [])
        with self._quiet():
            self.cmb_encoder.clear()
            for name, spec in ve.ENCODERS.items():
                # 探测过且明确失败 → 置灰但保留在列表里，让用户看得到「为什么不能用」
                bad = name in tested and not tested[name][0]
                text = spec["label"] + (" · 不可用" if bad else "")
                self.cmb_encoder.addItem(text, userData=name)
                # qfw 的 ComboBox 是 QPushButton 不是 QComboBox，没有 view()/model()，
                # 置灰只能走它自己的 setItemEnabled；原因文案没有单项 tooltip 可挂，
                # 统一在下面 lbl_enc_state 里报当前选中项的失败原因
                self.cmb_encoder.setItemEnabled(self.cmb_encoder.count() - 1, not bad)
            _combo_set(self.cmb_encoder, p.get("encoder"))
            self._rebuild_presets(p)
            _combo_set(self.cmb_rate, p.get("rate_mode"))
            self.sld_crf.setValue(p.get("crf", 18))
            self.spin_mb.setValue(int(p.get("target_mb") or 100))
            _combo_set(self.cmb_audio, p.get("audio"))
            self.spin_ab.setValue(int(p.get("audio_bitrate") or 192))
            _combo_set(self.cmb_container, p.get("container"))
            self.sw_faststart.setChecked(bool(p.get("faststart")))
            _combo_set(self.cmb_pix, p.get("pix_fmt"))
            _combo_set(self.cmb_out_mode, p.get("out_mode"))
            self.out_dir_edit.setText(str(p.get("out_dir") or ""))
            self.tpl_edit.setText(str(p.get("name_tpl") or ""))
            _combo_set(self.cmb_exist, p.get("on_exist"))

        enc = p.get("encoder") or "libx264"
        spec = ve.ENCODERS.get(enc, {})
        self.lbl_preset_hint.setText(
            f"合法档位：{', '.join(str(x) for x in spec.get('presets', []))}"
            + ("｜x264/x265 每上一档约慢 2 倍、同 CRF 体积小 5~10%" if not spec.get("hw")
               else "｜nvenc 用 p1…p7，p7 最慢最好"))
        self.lbl_crf_hint.setText(spec.get("crf_hint", ""))
        is_size = p.get("rate_mode") == "size"
        _paint(self.lbl_rate_tag, "目标体积生效中" if is_size else "CRF 生效中",
               _C_ACCENT)
        self.sld_crf.setEnabled(not is_size)
        self.spin_mb.setEnabled(is_size)
        self.sw_faststart.setEnabled(p.get("container") == "mp4")
        self.spin_ab.setEnabled(p.get("audio") == "aac")
        self.btn_pick_out.setEnabled(p.get("out_mode") == "custom")
        self.out_dir_edit.setEnabled(p.get("out_mode") == "custom")
        if not compiled:
            self.lbl_enc_state.setText("尚未探测环境：编码器可用性未知，"
                                       "到「环境检测」点一次「重新探测」才会置灰不可用项")
        elif enc in tested and not tested[enc][0]:
            self.lbl_enc_state.setText(f"✘ {enc} 在这台机器上跑不通：{tested[enc][1]}")
        else:
            self.lbl_enc_state.setText(f"✔ {enc} 可用" if compiled else "")

        i = hub.task_page.selected_row()
        cmd = hub.preview_command(i if i is not None else 0, multiline=True)
        self.cmd_view.setPlainText(cmd or "任务列表为空，先在「增强任务」里添加文件")

        src = hub.first_ok_source()
        info = hub.info_of(src) if src else None
        try:
            dst = ve.resolve_output_path(src or "<源文件>", dict(p), info,
                                         hub.videos_dir) or "<按当前策略会被跳过>"
        except Exception:
            dst = "<输出文件>"
        self.lbl_tpl_out.setText(f"示例输出：{dst}")
        dur = float((info or {}).get("duration") or 0)
        kbps = ve.target_bitrate_kbps(p.get("target_mb"), dur) if dur else 0
        self.lbl_kbps.setText(f"≈ {kbps} kbps，按 {_fmt_dur(dur)} 时长算" if kbps
                              else "选中一条已探测成功的任务后才能算出码率")

    def _rebuild_presets(self, p):
        spec = ve.ENCODERS.get(p.get("encoder") or "libx264", {})
        cur = self.cmb_preset.currentData()
        self.cmb_preset.clear()
        for name in spec.get("presets", []):
            self.cmb_preset.addItem(str(name), userData=str(name))
        legal = [str(x) for x in spec.get("presets", [])]
        _combo_set(self.cmb_preset, str(cur) if str(cur) in legal
                   else str(p.get("codec_preset") or ""))


# ==================== 子页 4：环境检测 ====================

class EnvPage(VePage):
    """环境检测：ffmpeg / ffprobe 定位（设置项 + PATH 兜底）与能力探测"""

    def __init__(self, hub, parent=None):
        super().__init__(hub, "环境检测",
                        "ffmpeg / ffprobe 可执行文件定位与能力探测 —— "
                        "本页任何一项失败，「增强任务」的开始按钮就置灰", parent)
        self.setObjectName("veEnvPage")
        body, lay = self.add_scrolled_body()

        self.lbl_status = BodyLabel("", body)
        self.lbl_status.setWordWrap(True)
        lay.addWidget(self.lbl_status)

        lay.addWidget(self._build_paths(body))

        pair = QHBoxLayout()
        pair.setSpacing(12)
        pair.addWidget(self._build_version(body), 1)
        pair.addWidget(self._build_encoders(body), 1)
        lay.addLayout(pair)

        lay.addWidget(self._build_filters(body))
        lay.addWidget(self._build_degrade(body))
        lay.addStretch(1)

        hub.stateChanged.connect(self.refresh)
        hub.paramsChanged.connect(self.refresh)
        self.refresh()

    def _build_paths(self, parent):
        card, cv, right = _card(parent, "可执行文件路径",
                               "留空 = 用 shutil.which 从 PATH 探测；填了 = 以填写值为准")
        self.btn_redetect = PrimaryPushButton(FluentIcon.SYNC, "重新探测", card)
        self.btn_redetect.clicked.connect(self._on_redetect)
        right.addWidget(self.btn_redetect)
        self.btn_save_paths = PushButton(FluentIcon.SAVE_AS, "写入设置", card)
        self.btn_save_paths.setToolTip("把当前两个路径固化进 config/paths.json，"
                                       "换 PATH 后也不会失效")
        self.btn_save_paths.clicked.connect(self._on_save_paths)
        right.addWidget(self.btn_save_paths)

        for key, label in (("ffmpeg", "ffmpeg"), ("ffprobe", "ffprobe")):
            r = _field(card, label)
            edit = LineEdit(card)
            edit.setFont(_term_font())
            edit.setPlaceholderText("留空则从 PATH 探测")
            setattr(self, f"{key}_edit", edit)
            r.addWidget(edit, 1)
            btn = ToolButton(FluentIcon.FOLDER, card)
            btn.setToolTip(f"浏览 {label}.exe")
            btn.clicked.connect(lambda _=False, k=key: self._on_pick_exe(k))
            r.addWidget(btn)
            tag = CaptionLabel("", card)
            setattr(self, f"{key}_tag", tag)
            r.addWidget(tag)
            cv.addLayout(r)

        self.lbl_source = _hint(card, "")
        cv.addWidget(self.lbl_source)
        cv.addWidget(_hint(card, "同一份路径也在「设置 → 应用配置 → 路径配置」里出现 "
                                 "ffmpeg 和 ffprobe 两行，两处编辑的是同一个 paths.json 键，"
                                 "改哪边都生效。本页的「浏览」是快捷入口，不是第二份配置。"))
        return card

    def _on_pick_exe(self, key):
        self._hub.pick_paths(f"选择 {key}.exe",
                             lambda paths, k=key: self._on_exe_picked(k, paths),
                             mode="file", filt="可执行文件 (*.exe);;所有文件 (*.*)")

    def _on_exe_picked(self, key, paths):
        if paths:
            getattr(self, f"{key}_edit").setText(paths[0])
            self._on_redetect()

    def _on_save_paths(self):
        ff = self.ffmpeg_edit.text().strip()
        fp = self.ffprobe_edit.text().strip()
        self._hub.save({"ffmpeg_path": ff, "ffprobe_path": fp})
        self._hub.info("已写入 config/paths.json；留空表示走 PATH 探测", "success")

    def _on_redetect(self):
        """改路径 → 重新定位 → 后台跑一次能力探测

        探测全程 < 1.5s，但仍在后台线程跑：ffmpeg -encoders / -filters 的输出
        有几千行，放主线程会看到界面顿一下。
        """
        hub = self._hub
        ff = self.ffmpeg_edit.text().strip()
        fp = self.ffprobe_edit.text().strip()
        if ff and not os.path.isfile(ff):
            hub.info(f"ffmpeg 路径不存在：{ff}", "error", duration=5000)
            return
        if fp and not os.path.isfile(fp):
            hub.info(f"ffprobe 路径不存在：{fp}", "error", duration=5000)
            return
        if hub.worker_running(W_ENV):
            return
        hub.relocate({"ffmpeg_path": ff, "ffprobe_path": fp})
        if not hub.ffmpeg:
            hub.refresh_env()
            return
        _paint(self.lbl_status, "探测中…", _C_MUTED)
        hub.start(W_ENV, "env", {
            "ffmpeg": hub.ffmpeg, "ffprobe": hub.ffprobe,
            "encoders": list(ve.ENCODERS),
        }, [("env_ready", hub.on_env_ready)])

    def _build_version(self, parent):
        card, cv, _right = _card(parent, "版本与构建")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        self._ver_labels = {}
        for row, key in enumerate(("version", "build", "vmaf", "ffmpeg")):
            grid.addWidget(CaptionLabel(
                {"version": "ffmpeg 版本", "build": "构建配置",
                 "vmaf": "质量评估", "ffmpeg": "实际路径"}[key], card), row, 0)
            lab = BodyLabel("—", card)
            lab.setWordWrap(True)
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lab.setFont(_term_font())
            grid.addWidget(lab, row, 1)
            self._ver_labels[key] = lab
        grid.setColumnStretch(1, 1)
        cv.addLayout(grid)
        return card

    def _build_encoders(self, parent):
        card, cv, _right = _card(parent, "可用编码器", "解析 ffmpeg -encoders 后逐个真编一帧")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        self._enc_labels = {}
        for i, name in enumerate(ve.ENCODERS):
            lab = CaptionLabel(f"○ {name}", card)
            grid.addWidget(lab, i // 2, i % 2)
            self._enc_labels[name] = lab
        cv.addLayout(grid)
        cv.addWidget(_hint(card, "注意：编码器编译进来 ≠ 能跑。*_nvenc 还要有 NVIDIA 驱动，"
                                 "所以每个都真编一帧黑场才算数，单次不到 200ms；"
                                 "失败的那一项会在「编码输出」里置灰并带上 stderr 摘要"))
        return card

    def _build_filters(self, parent):
        card, cv, _right = _card(parent, "可用滤镜",
                                "解析 ffmpeg -filters；本页结果决定「滤镜调参」里哪些选项真的能跑")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        self._filter_labels = {}
        for i, name in enumerate(WANTED_FILTERS):
            lab = CaptionLabel(f"○ {name}", card)
            grid.addWidget(lab, i // 5, i % 5)
            self._filter_labels[name] = lab
        cv.addLayout(grid)
        cv.addWidget(_hint(card, "探测结果只缓存在内存、不写 settings，避免与真实环境脱节；"
                                 "只在三种时机重跑：打开本页、点「重新探测」、ffmpeg 路径被修改"))
        return card

    def _build_degrade(self, parent):
        card, cv, _right = _card(parent, "缺失时的降级表现")
        cv.addWidget(_hint(card, "✘ ffmpeg 未找到：「增强任务」的开始按钮置灰，本页页顶常驻红色提示，"
                                 "并给出两条出路 —— ① 到本页填可执行文件路径；"
                                 "② 到「设置 → 路径配置」填 ffmpeg 行。"
                                 "本工具不会自动下载、不会自动装包，这是 AGENTS.md §3.2 的硬约束。"))
        cv.addWidget(_hint(card, "⚠ ffmpeg 找到但缺 libvmaf：不影响增强主流程，"
                                 "只把「质量对比」子页的 VMAF 选项置灰，PSNR / SSIM 仍可用 —— "
                                 "这两个是 ffmpeg 内建，不依赖外部库。"))
        return card

    def refresh(self):
        hub = self._hub
        with self._quiet():
            settings = hub.settings()
            self.ffmpeg_edit.setText(str(settings.get("ffmpeg_path") or ""))
            self.ffprobe_edit.setText(str(settings.get("ffprobe_path") or ""))
        caps = hub.caps or {}
        if not hub.ffmpeg or not hub.ffprobe:
            _paint(self.lbl_status, f"✘ {hub.ffmpeg_reason()}", _C_DANGER)
        elif not caps.get("ok"):
            _paint(self.lbl_status,
                   f"✘ ffmpeg 探测失败：{caps.get('reason') or '尚未探测'}", _C_DANGER)
        else:
            _paint(self.lbl_status,
                   f"✔ 环境就绪。ffmpeg {caps.get('version', '')} · "
                   f"编码器 {len(caps.get('encoders') or [])} 个 · "
                   f"滤镜 {len(caps.get('filters') or [])} 个 · "
                   f"libvmaf {'可用' if caps.get('has_vmaf') else '不可用'}",
                   _C_SUCCESS)

        ff_set = bool(str(settings.get("ffmpeg_path") or "").strip())
        self.lbl_source.setText(
            ("来源：设置项 config/paths.json" if ff_set else
             "来源：PATH 探测 —— 当前值尚未写入设置，点「写入设置」可固化，"
             "避免换 PATH 后失效"))
        _paint(self.ffmpeg_tag, os.path.basename(hub.ffmpeg) if hub.ffmpeg else "未定位",
               _C_SUCCESS if hub.ffmpeg else _C_DANGER)
        _paint(self.ffprobe_tag, os.path.basename(hub.ffprobe) if hub.ffprobe else "未定位",
               _C_SUCCESS if hub.ffprobe else _C_DANGER)

        self._ver_labels["ffmpeg"].setText(hub.ffmpeg or "—")
        self._ver_labels["version"].setText(caps.get("version") or "—")
        build = caps.get("build") or ""
        self._ver_labels["build"].setText(build[:600] or "—")
        self._ver_labels["vmaf"].setText(
            "libvmaf 已启用，「质量对比」可用 VMAF" if caps.get("has_vmaf")
            else "libvmaf 未启用，VMAF 不可用，PSNR / SSIM 仍可用")

        filters = set(caps.get("filters") or [])
        for name, lab in self._filter_labels.items():
            if not caps.get("ok"):
                lab.setText(f"○ {name}")
            elif name in filters or (name == "libvmaf" and caps.get("has_vmaf")):
                _paint(lab, f"✓ {name}", _C_SUCCESS)
            else:
                _paint(lab, f"✗ {name}", _C_DANGER)

        tests = caps.get("tests") or {}
        compiled = set(caps.get("encoders") or [])
        for name, lab in self._enc_labels.items():
            if name in tests:
                ok, why = tests[name]
                _paint(lab, f"{'✓' if ok else '✗'} {name}", _C_SUCCESS if ok else _C_DANGER)
                lab.setToolTip(why or "")
            elif name in compiled:
                _paint(lab, f"○ {name} · 未试编", _C_MUTED)
            else:
                _paint(lab, f"✗ {name}", _C_DANGER)
                lab.setToolTip("这个 ffmpeg 构建里没有编译该编码器")
        self.btn_redetect.setEnabled(not hub.worker_running(W_ENV))


# ==================== 子页 5：质量对比 ====================

class ComparePage(VePage):
    """质量对比：VMAF / PSNR / SSIM 客观评分 + 抽帧左右并排 + 历史留档"""

    HIST_COLS = ("时间", "源 → 输出", "参数摘要", "VMAF", "体积", "耗时")

    def __init__(self, hub, parent=None):
        super().__init__(hub, "质量对比",
                        "对一对「源 / 输出」文件跑客观指标与抽帧对比；"
                        "也可直接从历史记录里挑一行", parent)
        self.setObjectName("veComparePage")
        body, lay = self.add_scrolled_body()

        card, cv, right = _card(body, "对比对象")
        self.btn_run = PrimaryPushButton(FluentIcon.PLAY, "开始评测", card)
        self.btn_run.clicked.connect(self._on_metric)
        right.addWidget(self.btn_run)
        self.btn_frames = PushButton(FluentIcon.PHOTO, "抽帧对比", card)
        self.btn_frames.clicked.connect(self._on_frames)
        right.addWidget(self.btn_frames)
        self.btn_stop = PushButton(FluentIcon.CLOSE, "停止", card)
        self.btn_stop.clicked.connect(self._on_stop)
        right.addWidget(self.btn_stop)

        for key, label in (("src", "源文件"), ("dst", "输出文件")):
            r = _field(card, label)
            edit = LineEdit(card)
            edit.setFont(_term_font())
            edit.setPlaceholderText(f"选择{label}")
            setattr(self, f"{key}_edit", edit)
            r.addWidget(edit, 1)
            btn = ToolButton(FluentIcon.FOLDER, card)
            btn.setToolTip(f"浏览{label}")
            btn.clicked.connect(lambda _=False, k=key: self._on_pick(k))
            r.addWidget(btn)
            cv.addLayout(r)

        r = _field(card, "抽帧时间点")
        self.spin_t = CompactSpinBox(card)
        self.spin_t.setRange(0, 86400)
        self.spin_t.setSuffix(" s")
        self.spin_t.setValue(5)
        r.addWidget(self.spin_t)
        self.chk_vmaf = CheckBox("VMAF", card)
        self.chk_vmaf.setChecked(True)
        self.chk_psnr = CheckBox("PSNR", card)
        self.chk_psnr.setChecked(True)
        self.chk_ssim = CheckBox("SSIM", card)
        self.chk_ssim.setChecked(True)
        for c in (self.chk_vmaf, self.chk_psnr, self.chk_ssim):
            r.addWidget(c)
        r.addStretch(1)
        cv.addLayout(r)
        # _hint 是纯 CaptionLabel，不解析 markdown，强调只能靠措辞不能靠 **
        cv.addWidget(_hint(card, "锐化类滤镜会降低 VMAF / PSNR —— 它们改变了像素、"
                                 "偏离了参考源，而这类指标衡量的是「与源的接近程度」，"
                                 "不是「好不好看」。所以本页把客观分与主观抽帧并排展示，"
                                 "不把 VMAF 当唯一结论。"))
        lay.addWidget(card)

        self.strip = _StatStrip(["VMAF", "PSNR dB", "SSIM", "体积 源→输出", "评测状态"], body)
        lay.addWidget(self.strip)

        card2, c2, _r2 = _card(body, "抽帧对比", "同一时间点各跑一次单帧 ffmpeg 输出 PNG")
        panes = QHBoxLayout()
        panes.setSpacing(12)
        self.pane_src = _ImagePane("源 · 处理前", card2)
        self.pane_dst = _ImagePane("输出 · 处理后", card2)
        panes.addWidget(self.pane_src, 1)
        panes.addWidget(self.pane_dst, 1)
        c2.addLayout(panes)
        self.lbl_png = _hint(card2, "")
        c2.addWidget(self.lbl_png)
        lay.addWidget(card2)

        card3, c3, right3 = _card(body, "运行输出", "评测命令与 ffmpeg 逐行输出")
        self.btn_clear_log = PushButton(FluentIcon.DELETE, "清空", card3)
        self.btn_clear_log.clicked.connect(lambda: self.log_view.clear())
        right3.addWidget(self.btn_clear_log)
        self.log_view = _make_terminal(card3, 180)
        c3.addWidget(self.log_view)
        lay.addWidget(card3)

        card4, c4, right4 = _card(body, "历史记录", "仅本地留档在 config/misc.json，不参与任何同步")
        self.btn_hist_clear = PushButton(FluentIcon.DELETE, "清空", card4)
        self.btn_hist_clear.clicked.connect(self._on_clear_history)
        right4.addWidget(self.btn_hist_clear)
        self.hist = TableWidget(card4)
        self.hist.setColumnCount(len(self.HIST_COLS))
        self.hist.setHorizontalHeaderLabels(list(self.HIST_COLS))
        self.hist.setAlternatingRowColors(True)
        self.hist.verticalHeader().setVisible(False)
        self.hist.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.hist.setSelectionBehavior(QAbstractItemView.SelectRows)
        hh = self.hist.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in (0, 2, 3, 4, 5):
            hh.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        hh.setStretchLastSection(False)
        self.hist.setMinimumHeight(160)
        # 双击把该行填回「对比对象」，省得手抄两个长路径
        self.hist.cellDoubleClicked.connect(self._on_hist_double)
        c4.addWidget(self.hist)
        c4.addWidget(_hint(card4, f"上限 {ve.HISTORY_LIMIT} 条环形裁剪；"
                                  "不含任何凭据字段，无需 DPAPI"))
        lay.addWidget(card4)
        lay.addStretch(1)

        hub.stateChanged.connect(self.refresh)
        self.reload_history()
        self.refresh()

    def _on_pick(self, key):
        self._hub.pick_paths(f"选择{key}", lambda paths, k=key: self._picked(k, paths),
                             mode="file", filt=VIDEO_FILTER)

    def _picked(self, key, paths):
        if paths:
            getattr(self, f"{key}_edit").setText(paths[0])

    def _kinds(self):
        kinds = []
        if self.chk_vmaf.isChecked():
            kinds.append("vmaf")
        if self.chk_psnr.isChecked():
            kinds.append("psnr")
        if self.chk_ssim.isChecked():
            kinds.append("ssim")
        return kinds

    def _guard(self):
        hub = self._hub
        if hub.worker_running(W_CMP):
            hub.info("质量对比线程正在忙，请等待完成或先停止", "warning")
            return False
        if not hub.ready():
            hub.info(hub.ffmpeg_reason(), "error", duration=5000)
            return False
        return True

    def _on_metric(self):
        hub = self._hub
        if not self._guard():
            return
        src, dst = self.src_edit.text().strip(), self.dst_edit.text().strip()
        if not src or not dst:
            hub.info("源文件与输出文件都要填", "warning")
            return
        for p in (src, dst):
            if not os.path.isfile(p):
                hub.info(f"文件不存在：{p}", "error", duration=5000)
                return
        kinds = self._kinds()
        if not kinds:
            hub.info("至少勾选一个指标", "warning")
            return
        if "vmaf" in kinds and not (hub.caps or {}).get("has_vmaf", True):
            hub.info("这个 ffmpeg 没有编译 libvmaf，已自动去掉 VMAF", "warning")
            kinds = [k for k in kinds if k != "vmaf"]
            self.chk_vmaf.setChecked(False)
            if not kinds:
                return
        self.strip.set(4, "评测中…", _C_ACCENT)
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.log_view.append(f"▶ 开始评测 {', '.join(kinds)}")
        hub.start(W_CMP, "metric", {
            "src": src, "dst": dst, "kinds": kinds,
            "ffmpeg": hub.ffmpeg, "ffprobe": hub.ffprobe,
        }, [("metric_ready", self._on_metric_ready)])

    def _on_metric_ready(self, result):
        hub = self._hub
        hub.release(W_CMP)
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        r = result or {}
        scores = r.get("scores") or {}
        reasons = r.get("reasons") or {}
        for idx, kind in enumerate(("vmaf", "psnr", "ssim")):
            v = scores.get(kind)
            if v is None:
                self.strip.set(idx, "—", _C_MUTED)
            else:
                self.strip.set(idx, f"{v:.4f}" if kind != "vmaf" else f"{v:.2f}",
                               _C_SUCCESS if (kind == "vmaf" and v >= 93) or
                               (kind == "psnr" and v >= 40) or
                               (kind == "ssim" and v >= 0.98) else _C_ACCENT)
        src, dst = r.get("src") or "", r.get("dst") or ""
        sizes = [os.path.getsize(p) for p in (src, dst) if os.path.isfile(p)]
        self.strip.set(3, f"{_fmt_size(sizes[0])} → {_fmt_size(sizes[1])}"
                       if len(sizes) == 2 else "—", None)
        bad = [f"{k}: {v}" for k, v in reasons.items() if v]
        _paint(self.strip.nums[4], "有失败项" if bad else "完成",
               _C_DANGER if bad else _C_SUCCESS)
        for line in bad:
            self.log_view.append(f"✘ {line}")
        # 评测出的分顺手补进历史里最近一条同源记录，免得两张表各说各话
        hub.merge_scores(src, dst, scores)
        self.reload_history()
        if not bad:
            hub.info("评测完成", "success")

    def _on_frames(self):
        hub = self._hub
        if not self._guard():
            return
        src, dst = self.src_edit.text().strip(), self.dst_edit.text().strip()
        if not src or not dst:
            hub.info("源文件与输出文件都要填", "warning")
            return
        hub.start(W_CMP, "frame", {
            "src": src, "dst": dst, "t": float(self.spin_t.value()),
            "ffmpeg": hub.ffmpeg, "ffprobe": hub.ffprobe,
        }, [("frames_ready", self.set_frames)])

    def set_frames(self, result):
        """两张 PNG 落地 → 并排显示（滤镜页的「抽一帧预览」也走这里）"""
        hub = self._hub
        hub.release(W_CMP)
        r = result or {}
        src_png, dst_png = r.get("src_png") or "", r.get("dst_png") or ""
        self.pane_src.set_image(src_png, os.path.basename(src_png))
        self.pane_dst.set_image(dst_png, os.path.basename(dst_png))
        bits = [f"t={_fmt_dur(r.get('t'))}"]
        if r.get("reason"):
            bits.append(f"⚠ {r['reason']}")
        bits.append(f"PNG 落 {os.path.dirname(src_png or dst_png) or '临时目录'}")
        self.lbl_png.setText(" · ".join(bits))
        hub.task_page.append_line(
            f"▶ 抽帧完成：{os.path.basename(src_png)} / {os.path.basename(dst_png)}"
            + (f" · {r['reason']}" if r.get("reason") else ""))

    def _on_stop(self):
        w = self._hub.worker(W_CMP)
        if w is not None and w.isRunning():
            self.btn_stop.setEnabled(False)
            self.log_view.append("… 正在停止评测…")
            w.requestInterruption()

    # ---------- 历史 ----------

    def reload_history(self):
        history = self._hub.history()
        self.hist.blockSignals(True)
        try:
            self.hist.setRowCount(len(history))
            for i, h in enumerate(history):
                cells = (h.get("time", ""),
                         f"{os.path.basename(h.get('src', ''))} → "
                         f"{os.path.basename(h.get('dst', ''))}",
                         h.get("summary", ""),
                         f"{h['vmaf']:.2f}" if isinstance(h.get("vmaf"), float) else "未评测",
                         f"{_fmt_size(h.get('size_in', 0))} → {_fmt_size(h.get('size_out', 0))}",
                         _fmt_dur(h.get("elapsed")))
                for col, text in enumerate(cells):
                    it = QTableWidgetItem(str(text))
                    it.setFont(_term_font())
                    it.setToolTip(f"{h.get('src', '')}\n→ {h.get('dst', '')}")
                    if col == 3:
                        v = h.get("vmaf")
                        it.setForeground(_C_SUCCESS if isinstance(v, float) and v >= 93
                                         else (_C_MUTED if not isinstance(v, float)
                                               else _C_ACCENT))
                    if col in (3, 4, 5):
                        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.hist.setItem(i, col, it)
        finally:
            self.hist.blockSignals(False)

    def _on_hist_double(self, row, _col):
        h = self._hub.history()
        if 0 <= row < len(h):
            self.src_edit.setText(h[row].get("src", ""))
            self.dst_edit.setText(h[row].get("dst", ""))
            self._hub.info("已填回对比对象，可直接开始评测", "info")

    def _on_clear_history(self):
        if not self._hub.history():
            return
        box = MessageBox("清空历史记录", f"确定删除全部 {len(self._hub.history())} 条本地留档？", self)
        box.yesButton.setText("清空")
        box.cancelButton.setText("取消")
        if box.exec():
            self._hub.save({"enhance_history": []})
            self.reload_history()

    def refresh(self):
        has_vmaf = (self._hub.caps or {}).get("has_vmaf", True)
        self.chk_vmaf.setEnabled(has_vmaf)
        if not has_vmaf:
            self.chk_vmaf.setChecked(False)
            self.chk_vmaf.setToolTip("这个 ffmpeg 没有编译 libvmaf")
        busy = self._hub.worker_running(W_CMP)
        self.btn_run.setEnabled(not busy and self._hub.ready())
        self.btn_frames.setEnabled(not busy and self._hub.ready())
        self.btn_stop.setEnabled(busy)


# ==================== Hub 容器 ====================

class VideoEnhanceHub(PivotPage):
    """画质提升面板：五子页 Pivot 容器（objectName=videoEnhanceHub）

    本类是五子页之间唯一的中介：参数字典、探测缓存、任务队列、ffmpeg 定位与
    worker 生命周期都在这里，子页之间不互相持引用（任务页要读滤镜页的参数、
    滤镜页的预览要写对比页的图，全部经本类转发）。
    """

    paramsChanged = Signal()      # params 里任何一键变了
    stateChanged = Signal()       # ffmpeg 定位 / 能力探测 / 任务队列变了

    def __init__(self, win=None, parent=None):
        super().__init__(parent)
        self.setObjectName("videoEnhanceHub")
        self._win = win if win is not None else parent
        self.rows = []            # 任务队列（唯一真源，表格是它的视图）
        self.caps = {}            # 环境检测结果缓存（刻意不落 settings）
        self.params = ve.default_params()
        self.last_dir = ""
        self._file_dlg = None     # 非模态选择器引用，出作用域会被 GC 掉窗口
        self._row_maps = {}       # worker 属性名 → [表格行号]，信号里的 i 经它翻译
        self._t0 = {}             # worker 属性名 → 启动时刻，用来算单条耗时

        s = self.settings()
        self.params.update({k: v for k, v in (s.get("enhance_params") or {}).items()
                            if k in self.params})
        self.last_dir = str(s.get("enhance_last_dir") or "")
        self.ffmpeg, self.ffprobe = ve.locate_ffmpeg(s)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(1200)
        self._save_timer.timeout.connect(self._save_params)

        # 内层 Pivot 行尾放弹出按钮（稿 387：不浮动到最右，避免与外层 ToolHub
        # 那颗重叠）。沿用基类那一颗 btn_popout，只是把它从浮动改成进布局。
        self._inline_popout()

        self.task_page = EnhanceTaskPage(self, self)
        self.filter_page = FilterPage(self, self)
        self.encode_page = EncodePage(self, self)
        self.env_page = EnvPage(self, self)
        self.compare_page = ComparePage(self, self)

        # 注册顺序即页签顺序（稿 389-394，不可调）；文本必须与
        # main_window.open_hub_popout 里 nav_icons["videoEnhanceHub"] 的键逐字一致，
        # 少一个就静默退回「内嵌 Pivot」形态
        self.addPage(self.task_page, "增强任务")
        self.addPage(self.filter_page, "滤镜调参")
        self.addPage(self.encode_page, "编码输出")
        self.addPage(self.env_page, "环境检测")
        self.addPage(self.compare_page, "质量对比")
        self.lock_pivot_width()
        self.switchTo(self.task_page)

        self.lbl_popout_hint.setText("弹出工作台")
        self.paramsChanged.emit()
        self.stateChanged.emit()

    # ---------- 弹出按钮：浮动 → 内层 Pivot 行尾 ----------

    def _inline_popout(self):
        """把基类那颗浮动的弹出按钮改由 Pivot 行布局定位

        外层 ToolHub 的 btn_popout 浮动在页面最右上角（y=10），内层若也浮动，
        两颗按钮在纵向上只差几像素，窗口一窄就会叠在一起。
        """
        row = self.layout().itemAt(0).layout()
        # 基类给 pivot_row 设了 AlignLeft|AlignTop，那会让整行只占 sizeHint 宽度，
        # addStretch 顶不出去；清掉对齐后行才铺满，Pivot 自身仍靠左
        row.setAlignment(Qt.Alignment())
        row.addStretch(1)
        row.addWidget(self.lbl_popout_hint, 0, Qt.AlignVCenter)
        row.addWidget(self.btn_popout, 0, Qt.AlignVCenter)
        row.setContentsMargins(24, 10, 16, 0)

    def _sync_popout_hint(self):
        """覆盖基类：按钮由布局定位，这里只让提示文本跟随按钮显隐"""
        self.lbl_popout_hint.setVisible(self.btn_popout.isVisible())

    def _reposition_popout_btn(self):
        """覆盖基类的 move() 定位：按钮在布局里，手动挪会打架"""

    # ---------- 宿主代理（内嵌时 _win 是主窗口，弹出时是 HubPopoutWindow） ----------

    def settings(self):
        fn = getattr(self._win, "_load_settings", None)
        return dict(fn()) if fn is not None else {}

    def save(self, data):
        fn = getattr(self._win, "_save_settings", None)
        if fn is not None:
            fn(dict(data))

    def info(self, message, kind="info", duration=2500):
        fn = getattr(self._win, "_show_info_bar", None)
        if fn is not None:
            fn(str(message), kind, duration=duration)

    def log(self, text):
        fn = getattr(self._win, "_append_log", None)
        if fn is not None:
            fn(text)

    @property
    def videos_dir(self):
        return getattr(self._win, "videos_dir", "") or ""

    # ---------- 参数 ----------

    def set_param(self, key, value):
        if self.params.get(key) == value:
            return
        self.params[key] = value
        if key != "preset":
            self._recheck_preset()
        # 防抖落盘：滑块每移动一像素都会进来一次，逐次写 config 会把磁盘打满
        self._save_timer.start()
        self.paramsChanged.emit()

    def apply_preset(self, name):
        """整套套用预设，只广播一次（逐个 set_param 会吐出十几份半套中间态）"""
        merged = ve.apply_preset(self.params, name)
        merged.update({k: v for k, v in merged.items()
                       if k not in _PRESET_KEYS})
        self.params.update(merged)
        self.params["preset"] = name
        self._save_timer.start()
        self.paramsChanged.emit()

    def _recheck_preset(self):
        """手动改过任何被预设覆盖的键后，重新判定当前还属于哪个预设

        判定口径是「与某个预设的完整展开逐项相等」，不是「改过就算自定义」：
        用户把滑块推走又推回来，预设名应该恢复而不是永远停在自定义。
        """
        for name in ve.PRESETS:
            ref = ve.apply_preset(ve.default_params(), name)
            if all(self.params.get(k) == ref[k] for k in _PRESET_KEYS):
                self.params["preset"] = name
                return
        self.params["preset"] = "custom"

    def _save_params(self):
        self.save({"enhance_params": dict(self.params),
                   "enhance_last_dir": self.last_dir})

    def save_now(self):
        """立刻落盘（起线程前调）：跑失败了也要保住用户刚调好的一整套参数"""
        self._save_timer.stop()
        self._save_params()

    def history(self):
        h = self.settings().get("enhance_history")
        return list(h) if isinstance(h, list) else []

    def add_history(self, row):
        """一条成功的增强 → 一条历史（环形裁剪在引擎里做，这里只管拼记录）"""
        src, dst = row["src"], row.get("dst") or ""
        info = row.get("info") or {}
        entry = {
            "time": datetime.now().strftime("%m-%d %H:%M"),
            "src": src, "dst": dst,
            "summary": ve.summarize_params(self.params),
            "size_in": int(info.get("size") or 0),
            "size_out": int(os.path.getsize(dst)) if os.path.isfile(dst) else 0,
            "elapsed": round(float(row.get("elapsed") or 0), 1),
            "vmaf": None, "psnr": None, "ssim": None,
        }
        self.save({"enhance_history": ve.append_history(self.history(), entry)})
        self.compare_page.reload_history()

    def merge_scores(self, src, dst, scores):
        """把评测分补进历史里最近一条同源记录（没有就单独补一条）"""
        scores = scores or {}
        history = self.history()
        for h in history:
            if h.get("src") == src and h.get("dst") == dst:
                for k, v in scores.items():
                    if isinstance(v, float):
                        h[k] = v
                self.save({"enhance_history": history})
                return
        if not (src and dst):
            return
        entry = {"time": datetime.now().strftime("%m-%d %H:%M"), "src": src,
                 "dst": dst, "summary": "仅评测，非本工具产出",
                 "size_in": os.path.getsize(src) if os.path.isfile(src) else 0,
                 "size_out": os.path.getsize(dst) if os.path.isfile(dst) else 0,
                 "elapsed": 0, "vmaf": None, "psnr": None, "ssim": None}
        for k, v in scores.items():
            if isinstance(v, float):
                entry[k] = v
        self.save({"enhance_history": ve.append_history(history, entry)})

    # ---------- 环境 ----------

    def relocate(self, override=None):
        """按（可能被临时覆盖的）设置重新定位 ffmpeg / ffprobe"""
        s = self.settings()
        if override:
            s.update(override)
        self.ffmpeg, self.ffprobe = ve.locate_ffmpeg(s)
        if override:
            # 只是本页试填的路径，用户点「写入设置」才真正落盘
            self.caps = {}
        self.stateChanged.emit()

    def ready(self):
        return bool(self.ffmpeg and self.ffprobe)

    def ffmpeg_reason(self):
        """给不出 ffmpeg 时的人话原因（「填了但不存在」和「没填」要分开说）"""
        s = self.settings()
        missing = []
        for key, exe, path in (("ffmpeg_path", "ffmpeg", self.ffmpeg),
                               ("ffprobe_path", "ffprobe", self.ffprobe)):
            if path:
                continue
            v = (s.get(key) or "").strip()
            missing.append(f"{exe} 路径填了但文件不存在：{v}" if v
                           else f"{exe} 没填且 PATH 里也找不到")
        return "；".join(missing) + "；到「环境检测」页填路径，或把它加进 PATH"

    def on_env_ready(self, result):
        self.release(W_ENV)
        self.caps = result or {}
        self.stateChanged.emit()
        self.task_page.append_line(
            f"[环境] ffmpeg {self.caps.get('version', '')} · "
            f"编码器 {len(self.caps.get('encoders') or [])} 个 · "
            f"libvmaf {'可用' if self.caps.get('has_vmaf') else '不可用'}")
        # 置灰不等于改选择：用户之前挑的编码器探测失败时，params 里还是它，
        # 直接开跑会在第一个文件上失败。这里只提示，不替他偷偷换编码器。
        t = (self.caps.get("tests") or {}).get(self.params.get("encoder"))
        if t is not None and not t[0]:
            self.info(f"当前编码器 {self.params.get('encoder')} 在这台机器上跑不通，"
                      f"请到「编码输出」换一个；原因：{t[1]}", "warning", duration=6000)

    def refresh_env(self):
        self.caps = {}
        self.stateChanged.emit()

    # ---------- 队列 ----------

    def first_ok_source(self):
        """第一条探测成功的源；滤镜页的 -vf 预览与编码页的示例输出都要一个真实分辨率"""
        for r in self.rows:
            if (r.get("info") or {}).get("ok"):
                return r["src"]
        return ""

    def info_of(self, src):
        for r in self.rows:
            if r["src"] == src:
                return r.get("info")
        return None

    def preview_command(self, row_index, multiline=False):
        """拼一条给人看的完整命令；两遍编码时把两遍都列出来"""
        if not (0 <= (row_index or 0) < len(self.rows)):
            return ""
        r = self.rows[row_index]
        src, info = r["src"], r.get("info") or {}
        try:
            dst = ve.resolve_output_path(src, self.params, info, self.videos_dir)
            if dst is None:
                stem = os.path.splitext(os.path.basename(src))[0]
                dst = f"<按「{self.params.get('on_exist')}」策略会被跳过：{stem}>"
            cmds = ve.build_two_pass_commands(src, dst, dict(self.params), info,
                                              self.ffmpeg or "ffmpeg")
        except ve.EnhanceError as e:
            return f"# 无法拼出命令：{e}"
        out = []
        for i, cmd in enumerate(cmds):
            if len(cmds) > 1:
                out.append(f"# 第 {i + 1} 遍")
            out.append(_render_cmd(cmd, multiline))
        return "\n".join(out)

    # ---------- worker ----------

    def worker(self, attr):
        return getattr(self._win, attr, None)

    def worker_running(self, attr):
        w = self.worker(attr)
        return w is not None and w.isRunning()

    def start(self, attr, stage, params, hooks, row_map=None):
        """起一个后台 Worker 并挂到窗口属性上做 busy 守卫

        挂窗口而不是挂页面：内嵌视图与弹出窗口是同一个 Hub 类的两个实例，
        守卫必须共享，否则两边能各起一个 ffmpeg 抢 CPU。
        row_map 把 worker 信号里的列表下标翻译成表格行号（只跑勾选行时两者不等）。
        """
        if self.worker_running(attr):
            return None
        w = VideoEnhanceWorker(stage, params)
        setattr(self._win, attr, w)
        self._row_maps[attr] = list(row_map) if row_map else None
        self._t0[attr] = time.time()
        sink = self.compare_page.log_view.append if stage in ("metric", "frame") \
            else self.task_page.append_line
        w.line.connect(sink)
        for name, slot in hooks:
            getattr(w, name).connect(slot)
        w.cancelled.connect(lambda a=attr: self._on_cancelled(a))
        w.error.connect(lambda msg, a=attr: self._on_error(a, msg))
        # lambda 绑 w 防闭包晚绑定；运行中的 QThread 交给 _release_worker_safe 延迟销毁
        w.finished.connect(lambda w=w: self._release_safe(w))
        w.start()
        return w

    def row_of(self, attr, i):
        """worker 信号里的下标 → 表格行号；越界返回 None（队列已被清空）"""
        mapping = self._row_maps.get(attr)
        row = i if mapping is None else (mapping[i] if i < len(mapping) else None)
        if row is None or not (0 <= row < len(self.rows)):
            return None
        return row

    def release(self, attr):
        """摘掉窗口上的引用并回收线程，否则下一次会被旧引用挡住"""
        w = self.worker(attr)
        setattr(self._win, attr, None)
        self._row_maps.pop(attr, None)
        self._release_safe(w)
        self.stateChanged.emit()

    def _release_safe(self, w):
        fn = getattr(self._win, "_release_worker_safe", None)
        if w is not None and fn is not None:
            fn(w)

    def _on_cancelled(self, attr):
        self.release(attr)
        self.task_page.progress.hide()
        self.task_page.append_line("✘ 已取消，半成品输出已删除")
        self.log("[画质提升] 任务已取消")
        self.info("已取消", "info", duration=3000)

    def _on_error(self, attr, msg):
        self.release(attr)
        self.task_page.progress.hide()
        self.task_page.append_line(f"✘ {msg}")
        self.log(f"[画质提升] 失败：{msg}")
        self.info(str(msg).split(chr(10))[0], "error", duration=5000)

    def detach_workers(self):
        """容器关闭时中断所有后台线程（PivotPage.detach_workers / 弹出窗 closeEvent 会调）

        worker 引用挂在窗口上，页面的 *_worker 属性扫描扫不到；不主动中断的话，
        窗口关闭后 ffmpeg 子进程仍在后台跑满 CPU。
        """
        for attr in WORKER_ATTRS:
            w = self.worker(attr)
            if w is not None:
                try:
                    w.requestInterruption()
                except RuntimeError:
                    pass

    # ---------- 文件选择（非模态：exec() 会开自己的事件循环冻住整个窗口） ----------

    def pick_paths(self, title, on_done, mode="files", filt=""):
        """弹出非模态选择器，结果统一以 list 回给 on_done"""
        if self._file_dlg is not None:
            return          # 非模态对话框允许重复点按钮，这里挡掉第二次打开
        modes = {"files": QFileDialog.FileMode.ExistingFiles,
                 "file": QFileDialog.FileMode.ExistingFile,
                 "dir": QFileDialog.FileMode.Directory}
        start = self.last_dir if os.path.isdir(self.last_dir) else ""
        dlg = QFileDialog(self, title, start, filt)
        dlg.setFileMode(modes.get(mode, modes["files"]))
        if mode == "dir":
            dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
        dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen
                          if mode != "save" else QFileDialog.AcceptMode.AcceptSave)
        dlg.filesSelected.connect(lambda paths, cb=on_done: cb(list(paths)))
        dlg.finished.connect(self._on_picker_done)
        # 关闭即销毁：否则每次浏览都漏一个挂在本页下的隐藏 QFileDialog
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        self._file_dlg = dlg
        dlg.open()

    def _on_picker_done(self, _result=0):
        self._file_dlg = None


def _render_cmd(cmd, multiline=False):
    """argv → 可复制的命令行；multiline 按参数分组换行，便于在预览卡里读"""
    if not multiline:
        return " ".join(_shell_quote(x) for x in cmd)
    lines, cur = [], []
    # 换行点选在语义边界上（输入 / 滤镜 / 视频编码 / 音频 / 输出），
    # 按固定宽度折行会把一个参数劈成两半
    breaks = {"-i", "-vf", "-c:v", "-c:a", "-movflags", "-pass"}
    for tok in cmd:
        if tok in breaks and cur:
            lines.append(" ".join(cur))
            cur = []
        cur.append(_shell_quote(tok))
    if cur:
        lines.append(" ".join(cur))
    return " \\\n  ".join(lines)


def _shell_quote(tok):
    """含空格或引号的参数加双引号，其余原样（Windows 命令行口径）"""
    t = str(tok)
    if t and not any(c in t for c in ' "\t'):
        return t
    return '"' + t.replace('"', '\\"') + '"'
