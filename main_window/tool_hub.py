# -*- coding: utf-8 -*-
"""工具页 ToolHub（二期，2026-09-07）——「设置-工具」4 条动作行升级为独立工作区

设计稿：design/tools_page_v2.html
形态（2026-09-07 反馈修订）：与运维/售后/跑视频同风格——横排 Pivot 二级切换
（无图标），四工作区堆叠在下方：
    单杆视频 │ 端口占用 │ 上传清单 │ 视频与日志批量整理

复用原则（后端零改动）：
  - 单杆视频 → workers.single_video_worker.SingleVideoWorker（参数校验复刻
    SingleVideoDialog.collect_params 同一套规则；路径记忆键不变）
  - 端口占用 → 真实 socket 监听（TCP listen / UDP bind），表格化管理
  - 上传清单 → 扫描 {videos_dir}/upload，勾选打包走 ZipUploadWorker(files=白名单)
  - 批量整理 → NewLogWorker + 整理产物 ZipUploadWorker（zip_prefix=newlog）

busy 守卫：Worker 引用统一挂主窗口属性（_single_video_worker/_newlog_worker/
_newlog_upload_worker），与菜单栏弹窗入口互斥，防止双开。
"""
import os
import re
import random
import socket
import shutil
from datetime import datetime

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QFrame, QScrollArea, QFileDialog, QHeaderView,
                               QTableWidgetItem, QAbstractItemView, QSizePolicy,
                               QSplitter)
from qfluentwidgets import (TitleLabel, CaptionLabel, BodyLabel, CardWidget,
                            LineEdit, PushButton, PrimaryPushButton, ComboBox,
                            CompactSpinBox, TextEdit, TableWidget,
                            CheckBox, ProgressBar, FluentIcon, EditableComboBox,
                            ToolButton, MessageBox)

from core.spin_fit_patch import FittedSpinBox
from main_window.pivot_page import PivotPage
from windows.single_video_dialog import (
    _DEFAULT_SESSION_CODE, _DEFAULT_FORMAT, _DEFAULT_AVATAR_0,
    _DEFAULT_AVATAR_1, _DEFAULT_USER_0, _DEFAULT_USER_1,
    _DEFAULT_PENDING_ROOT, _DEFAULT_VIDEOS_ROOT, _random_session_suffix)
from workers.newlog_worker import NewLogWorker
from workers.single_video_worker import SingleVideoWorker
from workers.collect_worker import ZipUploadWorker


# 语义色（对齐 core/design_tokens.py：success/warning/danger/info）
_C_SUCCESS = QColor(0x1a, 0x9e, 0x6c)
_C_DANGER = QColor(0xcf, 0x44, 0x52)
_C_ACCENT = QColor(0x00, 0x83, 0x8f)
_C_MUTED = QColor(0x6b, 0x72, 0x80)
# 单元格着色统一走 setForeground(QColor)，不走 QSS：qfw 主题切换会周期性重合并
# 样式表，只有逐单元格设置的前景色不会被下一次轮询冲掉。


def _term_font():
    """等宽字体，供日志视图与端口、大小这类需要按位对齐的列使用

    Consolas 没有中文字形，实际显示时中文会回退到系统字体；这里设 Monospace
    风格提示，回退时优先挑等宽中文字体，否则日志里的中文会把整列宽度顶歪。
    """
    mono = QFont("Consolas")
    mono.setStyleHint(QFont.Monospace)
    return mono


def _make_terminal(parent, height=200):
    """黑底终端风格输出区（设计稿：TextEdit 黑底，等宽字体）

    height=None → 不设固定高度、垂直 Expanding（占满剩余空间）。
    """
    tv = TextEdit(parent)
    tv.setReadOnly(True)
    if height is not None:
        tv.setFixedHeight(height)
    else:
        tv.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        tv.setMinimumHeight(180)
    tv.setFont(_term_font())
    tv.setStyleSheet(
        "TextEdit{background:#1b1e23;color:#d4d8de;border:1px solid #2b2f36;"
        "border-radius:6px;padding:6px 8px;}")
    return tv


def _make_vsplitter(parent, top_widget, bottom_widget):
    """上下两块之间插入可拖动分隔条（2026-09-19 反馈：拖动改高度）

    外观**对齐主界面列表之间的间隔**：工作台 4 列的 splitter 就是默认 QSplitter
    + handleWidth=2（渲染出来约 4px 的细缝，不做任何配色），这里照抄同一套，
    不再自造粗色条；鼠标移到缝上会变成上下调整光标。
    - 上块保持自身高度，下块（输出区）吃掉窗口多余高度，不改原有比例；
    - childrenCollapsible=False：两块都不会被拖没，最短高度各自的最小尺寸兜底。
    """
    sp = QSplitter(Qt.Orientation.Vertical, parent)
    sp.setChildrenCollapsible(False)
    sp.setHandleWidth(2)
    sp.addWidget(top_widget)
    sp.addWidget(bottom_widget)
    sp.setStretchFactor(0, 0)
    sp.setStretchFactor(1, 1)
    return sp


def _fmt_size(n: float) -> str:
    """字节数 → 人类可读（与运维面板 common._fmt_size 同规则）"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024


def _make_scroll(body_widget, parent):
    """工作区通用滚动容器（透明背景，与 SettingsHubPage._make_page 同模式）"""
    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    scroll.viewport().setAutoFillBackground(False)
    scroll.setStyleSheet("QScrollArea { background: transparent; }")
    scroll.setWidget(body_widget)
    # setWidget() 会重建视口，透明设置必须在其后补（否则深色主题下视口呈黑块）
    scroll.viewport().setStyleSheet("background: transparent;")
    return scroll


def _transparent(page_widget):
    """工作区页面本体透明（消除卡片外直角底色块，2026-09-07 反馈）：
    普通 QWidget 未注册进 styleSheetManager，widget 级 setStyleSheet 合法
    且不会被 qfw 主题轮询/合并源重置。"""
    # QWidget 只有开着 WA_StyledBackground 才会绘制自身样式表背景，关掉之后
    # setStyleSheet 的 transparent 才真正透出父级卡片底色
    page_widget.setAttribute(Qt.WA_StyledBackground, False)
    page_widget.setAutoFillBackground(False)
    page_widget.setStyleSheet("background: transparent;")


def _pair(grid, row_idx, label0, w0, label1, w1, parent):
    """两列参数网格行（复刻 SingleVideoDialog._pair）

    固定「标签/控件/标签/控件」四列，两个入口的字段顺序才能一致，用户从弹窗
    换到工作区时不用重新找控件位置。
    """
    grid.addWidget(BodyLabel(label0, parent), row_idx, 0)
    grid.addWidget(w0, row_idx, 1)
    grid.addWidget(BodyLabel(label1, parent), row_idx, 2)
    grid.addWidget(w1, row_idx, 3)


def _pick_dir(win, line_edit, start_dir=""):
    """异步选择目录（QFileDialog.open() 非阻塞，防 exec 冻结主线程）"""
    # 起始目录优先用调用方指定值，其次回填输入框现值，二次修改落在同一起点
    dlg = QFileDialog(win, "选择目录", start_dir or line_edit.text().strip())
    dlg.setFileMode(QFileDialog.FileMode.Directory)
    dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
    # 配合局部变量：对话框关闭时自行销毁，否则每次浏览都会漏一个挂在 win 下的
    # 隐藏 QFileDialog，直到主窗口退出才释放
    dlg.setAttribute(Qt.WA_DeleteOnClose)

    def _sel(paths):
        if paths:
            line_edit.setText(paths[0])

    dlg.filesSelected.connect(_sel)
    dlg.open()
    return dlg


# ==================== 工作区 1：单杆视频 ====================

class SingleVideoWork(QWidget):
    """单杆视频工作区：日志选择 + JSON 参数表单 + 终端输出 + 后台 Worker"""

    def __init__(self, win, parent=None):
        """搭出「左表单 + 右日志预览」两栏，并读回上次的路径配置

        控件一次建好而不是切页时懒加载：预览区要在用户选文件之前就能显示
        「未选择日志」，且 Pivot 切页只切显隐，重建会丢掉用户填了一半的表单。
        """
        super().__init__(parent)
        # objectName 是 PivotPage.addPage 的 routeKey 来源，缺失会在注册时直接抛错。
        self.setObjectName("singleVideoWork")
        _transparent(self)
        self._win = win
        self._phase = "idle"          # idle/running/browsing/done
        # 记最近一次输出目录：worker 回传路径前，「打开视频目录」也要有地方可开。
        self._out_dir = ""
        self._file_dlg = None
        settings = win._load_settings()

        body = QWidget(self)
        body.setAutoFillBackground(False)
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 8, 20, 12)
        lay.setSpacing(12)

        card = CardWidget(body)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)

        # 顶部一句话交代用途与耗时特征，让用户在点开始之前就知道这是 CPU 密集任务。
        cl.addWidget(BodyLabel(
            "从 .log 日志解析单杆得分，生成带计分水印的单杆视频（逐帧渲染，CPU 密集）。",
            card))

        # ---------- 表单区：左列参数 + 右列日志预览（2026-09-19 反馈） ----------
        # 左列沿用「控件定宽、尾部空列吃剩余」的既定规范；右侧预览面板吃掉剩下的
        # 宽度、与左列表单等高等宽，用来在生成前核对日志内容（帧区间/进球记录）。
        # 左列控件全部定宽、右列预览吃满剩余宽度：定宽后两行一组的字段才不会随窗口抖动。
        form_row = QHBoxLayout()
        form_row.setSpacing(16)
        left_col = QVBoxLayout()
        left_col.setSpacing(10)
        form_row.addLayout(left_col, 0)
        form_row.addWidget(self._build_log_preview(card), 1)

        # ---------- 日志文件选择（控件定宽，参考跑视频录入表单） ----------
        row = QHBoxLayout()
        row.addWidget(BodyLabel("日志文件:", card))
        self.log_path_edit = LineEdit(card)
        # 只读 + 浏览按钮：路径只能由选择器写入，手输的相对路径在 Worker 侧还要再解析
        self.log_path_edit.setReadOnly(True)
        self.log_path_edit.setPlaceholderText("选择 .log 日志文件")
        self.log_path_edit.setFixedWidth(320)
        row.addWidget(self.log_path_edit)
        self.btn_browse = PushButton(FluentIcon.FOLDER, "浏览", card)
        self.btn_browse.clicked.connect(self._on_browse)
        row.addWidget(self.btn_browse)
        row.addStretch(1)
        left_col.addLayout(row)

        # ---------- 参数表单 ----------
        # JSON 字段区的默认值与 SingleVideoDialog 保持一致：同一份日志从哪个入口
        # 进来，生成结果都应当相同
        left_col.addWidget(CaptionLabel("JSON 字段:", card))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        # 帧号上限按 8 位给，正常对局远到不了；越界帧由 worker 侧按日志长度处理。
        self.spin_start = CompactSpinBox(card)
        self.spin_start.setRange(0, 99_999_999)
        self.spin_start.setValue(0)
        self.spin_start.setFixedWidth(130)
        self.spin_end = CompactSpinBox(card)
        self.spin_end.setRange(0, 99_999_999)
        # 65535 是弹窗沿用下来的默认结束帧，两个入口给同一初值，对比结果时不会误判
        self.spin_end.setValue(65535)
        self.spin_end.setFixedWidth(130)
        _pair(grid, 0, "起始帧:", self.spin_start, "结束帧:", self.spin_end, card)

        # 甲乙方只有 0/1，用 SpinBox 而非下拉：不改鼠标也能用上下键切换。
        self.spin_player = CompactSpinBox(card)
        self.spin_player.setRange(0, 1)
        self.spin_player.setValue(1)
        self.spin_player.setFixedWidth(130)
        # 轮次从 1 起（0 没有意义），999 足够覆盖任何赛制。
        self.spin_round = CompactSpinBox(card)
        self.spin_round.setRange(1, 999)
        self.spin_round.setValue(1)
        self.spin_round.setFixedWidth(130)
        _pair(grid, 1, "甲乙方 (0/1):", self.spin_player, "轮次:",
              self.spin_round, card)

        # 赛制是自由文本，生成时原样写进水印，这里不做枚举校验。
        self.edit_format = LineEdit(card)
        self.edit_format.setText(_DEFAULT_FORMAT)
        self.edit_format.setFixedWidth(130)
        self.edit_date = LineEdit(card)
        # 默认今天：绝大多数场次当天打完当天出片，改日期是少数情况
        self.edit_date.setText(datetime.now().strftime("%Y%m%d"))
        self.edit_date.setFixedWidth(130)
        _pair(grid, 2, "赛制:", self.edit_format, "session_date:",
              self.edit_date, card)

        # session_name 宽 160 够放「第 N 场」，与右侧 session_code 成对出现。
        self.edit_session_name = LineEdit(card)
        self.edit_session_name.setText("第1场")
        self.edit_session_name.setFixedWidth(160)
        self.edit_session_code = LineEdit(card)
        self.edit_session_code.setText(_DEFAULT_SESSION_CODE)
        # 内容自适应宽度（2026-09-07 反馈）：按文本像素宽调整，夹在上下限之间
        self._autosize_session_code()
        self.edit_session_code.textChanged.connect(
            lambda _t: self._autosize_session_code())
        _pair(grid, 3, "session_name:", self.edit_session_name,
              "session_code:", self.edit_session_code, card)

        # 头像路径给 160 宽：够放常见相对路径，超长时输入框内横向滚动而不截断数据。
        self.edit_ava_0 = LineEdit(card)
        self.edit_ava_0.setText(_DEFAULT_AVATAR_0)
        self.edit_ava_0.setFixedWidth(160)
        self.edit_ava_1 = LineEdit(card)
        self.edit_ava_1.setText(_DEFAULT_AVATAR_1)
        self.edit_ava_1.setFixedWidth(160)
        _pair(grid, 4, "选手0头像:", self.edit_ava_0, "选手1头像:",
              self.edit_ava_1, card)

        # 姓名 130 宽：中文姓名六到八字够用，也与上面的数字列对齐。
        self.edit_user_0 = LineEdit(card)
        self.edit_user_0.setText(_DEFAULT_USER_0)
        self.edit_user_0.setFixedWidth(130)
        self.edit_user_1 = LineEdit(card)
        self.edit_user_1.setText(_DEFAULT_USER_1)
        self.edit_user_1.setFixedWidth(130)
        _pair(grid, 5, "选手0姓名:", self.edit_user_0, "选手1姓名:",
              self.edit_user_1, card)

        # 待处理目录 / 视频输出目录：各带浏览按钮（2026-09-07 需求）
        self.edit_pending = LineEdit(card)
        self.edit_pending.setText(
            str(settings.get("single_pending_root") or _DEFAULT_PENDING_ROOT))
        self.edit_pending.setFixedWidth(200)
        # 目录浏览用 ToolButton：纯图标不占文字宽度，两个目录并排才不挤。
        self.btn_pending = ToolButton(FluentIcon.FOLDER_ADD, card)
        self.btn_pending.setToolTip("浏览选择待处理目录")
        self.btn_pending.clicked.connect(
            lambda: _pick_dir(self._win, self.edit_pending))
        self.edit_videos = LineEdit(card)
        self.edit_videos.setText(
            str(settings.get("single_videos_root") or _DEFAULT_VIDEOS_ROOT))
        self.edit_videos.setFixedWidth(200)
        # 两个目录同构：一个指向日志来源，一个指向成品落点，互不覆盖。
        self.btn_videos = ToolButton(FluentIcon.FOLDER_ADD, card)
        self.btn_videos.setToolTip("浏览选择视频输出目录")
        self.btn_videos.clicked.connect(
            lambda: _pick_dir(self._win, self.edit_videos))
        _pair(grid, 6, "待处理目录:", self._dir_cell(self.edit_pending, self.btn_pending, card),
              "视频输出目录:", self._dir_cell(self.edit_videos, self.btn_videos, card), card)
        # 控件定宽：网格整体靠左，尾部空列吃掉左列剩余宽度
        grid.setColumnStretch(4, 1)
        left_col.addLayout(grid)

        # ---------- 操作行 ----------
        btns = QHBoxLayout()
        # 主按钮独占 Primary 样式；开跑后由 _enter_running 转成禁用态防重复提交。
        self.btn_start = PrimaryPushButton(FluentIcon.PLAY, "开始生成", card)
        self.btn_start.clicked.connect(self._on_start)
        btns.addWidget(self.btn_start)
        # 输出目录在拿到真实路径之前不显示，避免用户点到空目录。
        self.btn_open_out = PushButton(FluentIcon.FOLDER, "打开视频目录", card)
        self.btn_open_out.clicked.connect(self._on_open_out)
        self.btn_open_out.hide()
        btns.addWidget(self.btn_open_out)
        btns.addStretch(1)
        left_col.addLayout(btns)

        # 左列表单 + 右列日志预览合成一行
        cl.addLayout(form_row)

        # ---------- 布局（2026-09-07 反馈）：卡铺满整行、卡内控件定宽，
        # 运行输出独立整宽卡片占满剩余空间（不套滚动）；
        # 2026-09-19：参数区与运行输出之间改由 QSplitter 分隔，可上下拖动改高度 ----------
        # 运行输出独立成整宽卡片：日志只读等宽，套滚动会破坏自动滚到底部的行为。
        out_card = CardWidget(self)
        ol = QVBoxLayout(out_card)
        ol.setContentsMargins(16, 14, 16, 14)
        ol.setSpacing(8)
        ol.addWidget(CaptionLabel("运行输出:", out_card))
        # height=None：终端吃满卡片剩余高度，比固定 200 更适合盯长日志。
        self.log_view = _make_terminal(out_card, None)
        ol.addWidget(self.log_view, 1)

        self.splitter = _make_vsplitter(body, card, out_card)
        lay.addWidget(self.splitter, 1)
        # 外层不留边距，卡片间距全部交给 body 的 contentsMargins 控制。
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(body)

        self._refresh_log_preview()      # 初始为「未选择日志」提示态

    # ---------- 右侧日志预览（2026-09-19 反馈：卡右侧空白区） ----------

    _LOG_PREVIEW_MAX_BYTES = 256 * 1024   # 读取上限，防超大日志把界面卡住

    def _build_log_preview(self, parent):
        """右列「日志预览」：与左列表单等高的只读日志视图（样式同运行输出）

    最小宽 300 是为了日志行不至于折得太碎；再窄时等宽字体一行只剩三十来个
    字符，帧号和得分会被拆成两行，反而更难核对。
    """
        box = QWidget(parent)
        box.setMinimumWidth(300)
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(CaptionLabel("日志预览:", box))
        self.log_preview_meta = CaptionLabel("未选择日志文件", box)
        self.log_preview_meta.setMaximumWidth(420)   # 超长文件名不撑破面板
        head.addWidget(self.log_preview_meta)
        head.addStretch(1)
        self.btn_log_refresh = ToolButton(FluentIcon.SYNC, box)
        self.btn_log_refresh.setToolTip("重新读取日志文件")
        self.btn_log_refresh.clicked.connect(self._refresh_log_preview)
        head.addWidget(self.btn_log_refresh)
        v.addLayout(head)

        self.log_preview = _make_terminal(box, None)
        self.log_preview.setPlaceholderText("选择 .log 日志文件后，在此预览内容")
        v.addWidget(self.log_preview, 1)
        return box

    def _refresh_log_preview(self):
        """把所选日志读进预览区（限 _LOG_PREVIEW_MAX_BYTES 字节，超出加截断提示）

        解码用 errors="replace"：日志里常混 GBK 片段，一个坏字节不该让整份预览
        报错。读失败时清空并置灰刷新按钮，免得用户对着旧内容以为读成功了。
        """
        path = self.log_path_edit.text().strip()
        if not path or not os.path.isfile(path):
            self.log_preview_meta.setText("未选择日志文件")
            self.log_preview_meta.setToolTip("")
            self.log_preview.clear()
            self.btn_log_refresh.setEnabled(False)
            return
        self.btn_log_refresh.setEnabled(True)
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                raw = f.read(self._LOG_PREVIEW_MAX_BYTES)
        except OSError as e:
            self.log_preview_meta.setText(f"读取失败: {e}")
            self.log_preview.clear()
            return
        text = raw.decode("utf-8", errors="replace")
        # 读满上限仍短于文件大小即为截断；不提示的话用户会以为日志只有这么长
        if len(raw) < size:
            text += (f"\n\n… 日志较大（{_fmt_size(size)}），"
                     f"仅预览前 {_fmt_size(self._LOG_PREVIEW_MAX_BYTES)}")
        # 面板本身较窄，头部只放「大小 · 行数」，文件名挂悬停提示（左侧字段已显示路径）
        # 按换行符计数后补 1：末行没有换行符，不补的话行数永远少一行
        info = f"{_fmt_size(size)} · {text.count(chr(10)) + 1} 行"
        self.log_preview_meta.setText(info)
        self.log_preview_meta.setToolTip(f"{path}\n{info}")
        # setPlainText 会把光标留在文末，手动滚回顶部才能第一眼看到开头几帧
        self.log_preview.setPlainText(text)
        self.log_preview.verticalScrollBar().setValue(0)   # 预览回到日志开头

    @staticmethod
    def _dir_cell(edit, btn, parent):
        """LineEdit + 浏览按钮组合成一个单元格控件

        输入框拉伸因子 1、按钮 0：单元格变宽时只有路径框变长，图标保持原尺寸。
        """
        w = QWidget(parent)
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        h.addWidget(edit, 1)
        h.addWidget(btn, 0)
        return w

    _CODE_MIN_W = 180
    _CODE_MAX_W = 360
    # 下限保证能看清「8 位日期_序号」，上限防止超长编码把右侧预览挤没；两侧都留
    # 余量，输入过程中宽度变化才不至于每敲一个字符就跳一次。

    def _autosize_session_code(self):
        """session_code 按文本内容自适应宽度（夹在 min/max 之间）

        输入框是定宽布局，编码长度却由用户输入决定。固定 180 会截断长编码，
        固定 360 又会在短编码时浪费一整列宽度，所以按文本实测宽度反推。
        """
        fm = self.edit_session_code.fontMetrics()
        # +44 是内边距与光标的固定占位，fontMetrics 只算字形宽度，不含这部分
        w = fm.horizontalAdvance(self.edit_session_code.text()) + 44
        w = max(self._CODE_MIN_W, min(w, self._CODE_MAX_W))
        # 先夹到上下限再 setFixedWidth：否则空文本时宽度塌到几十像素，光标都看不见
        self.edit_session_code.setFixedWidth(w)

    # ---------- 文件选择（异步 open()，防 exec 冻结主线程） ----------

    # 日志选择走非模态 open()：exec() 会开自己的事件循环，把主窗口连同左侧
    # Pivot 一起冻住，用户在选文件期间连切页都做不到。
    def _on_browse(self):
        """弹出非模态日志选择器，选中结果交给 _on_file_selected

        选择器取消同样会发 finished，所以进入时就锁住开始按钮，由
        _on_picker_finished 统一解锁，避免「取消一次按钮就永久灰掉」。
        """
        if self._file_dlg is not None:
            return
            # 非模态对话框允许重复点击按钮，这里挡掉第二次打开，避免叠出多个选择器
        start_dir = self.edit_pending.text().strip() or _DEFAULT_PENDING_ROOT
        # 默认落到「待处理目录」：日志通常就放在那儿，省掉每次从用户主目录开始翻
        if not os.path.isdir(start_dir):
            start_dir = ""
        self._phase = "browsing"
        # 浏览期间置 browsing 并禁用开始按钮：选择器取消也会回调 finished，靠这个状态恢复
        self.btn_start.setEnabled(False)
        self.btn_browse.setEnabled(False)
        # 过滤器放开「所有文件」：个别日志由别的工具导出，后缀不是 .log
        dlg = QFileDialog(self, "选择日志文件", start_dir,
                          "日志文件 (*.log);;所有文件 (*.*)")
        dlg.setFileMode(QFileDialog.FileMode.ExistingFile)
        dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        dlg.fileSelected.connect(self._on_file_selected)
        dlg.finished.connect(self._on_picker_finished)
        self._file_dlg = dlg
        # 持住引用是必须的：局部变量出作用域后对话框会被 Python GC 掉，窗口闪一下就没了
        dlg.open()

    # 选中后一次做完三件事：回填路径并刷新预览、推断输出视频名、从文件名提取日期。
    # 这些推断都只是填默认值，用户仍然可以逐项改。
    def _on_file_selected(self, path):
        """选中日志后回填路径、刷预览，并从文件名推断视频名与日期

        推断只是默认值，用户仍可逐项改写，因此不弹确认框打断流程。
        """
        if not path:
            return
        self.log_path_edit.setText(path)
        # 先回填再刷新预览：_refresh_log_preview 读的就是这个输入框的文本
        self._refresh_log_preview()      # 右列日志预览随选择联动
        video_name = os.path.basename(path).replace('.log', '.mp4')
        # 输出名默认与日志同名，只换后缀；replace 而非 rsplit，兼容名字里带 .log 的情况
        self.append_line(f"[选择] 日志: {path}")
        self.append_line(f"[选择] 自动推断视频: "
                         f"{os.path.join(self.edit_pending.text().strip(), video_name)}")
        base = os.path.splitext(os.path.basename(path))[0]
        m = re.match(r"^(\d{8})", base)
        # 约定日志名以 8 位日期开头（如 20260919_第1场.log），认不出来就保留表单原值
        if m:
            date = m.group(1)
            self.edit_date.setText(date)
            # 随机生成 session_code 开关（设置 → 工具，2026-09-07）
            if self._random_code_enabled():
            # 随机后缀而不是顺序号：多台机器同时出片时，顺序号会撞车导致视频互相覆盖
                self.edit_session_code.setText(f"{date}_{_random_session_suffix()}")
                self.append_line(f"[选择] 自动识别 session_date={date}，session_code 已随机生成")
            else:
                self.append_line(f"[选择] 自动识别 session_date={date}")

    def _random_code_enabled(self):
        """随机 session_code 开关；缺省开启，重名覆盖比顺序号更难挽回。"""
        return bool(self._win._load_settings().get("single_random_session_code", True))

    def _auto_open_enabled(self):
        """生成后自动打开目录的开关；缺省关闭，批量出片时弹窗会打断节奏。"""
        return bool(self._win._load_settings().get("single_auto_open_dir", False))

    def _on_picker_finished(self, _result):
        """选择器收起后无条件回到 idle；取消选择也走这条路

        必须先清引用再改状态：finished 之后对话框已经关闭，留着引用只会拖住一个死对象。
        """
        self._file_dlg = None
        self._phase = "idle"
        self.btn_start.setEnabled(True)
        self.btn_start.setText("开始生成")
        self.btn_browse.setEnabled(True)

    # ---------- 参数收集（校验规则与 SingleVideoDialog.collect_params 一致） ----------

    def collect_params(self):
        """收集并校验表单，产出 SingleVideoWorker 的构造参数；校验不过返回 None

        键名必须与 SingleVideoWorker 透传给 generate_json 的形参逐字一致，多一个
        或少一个都会在 Worker 线程里抛 TypeError，那里已经拿不到用户能看懂的上下文，
        所以校验与报错都留在本方法。校验顺序按代价从低到高：先看路径，再读文件。
        """
        log_path = self.log_path_edit.text().strip()
        if not log_path or not os.path.isfile(log_path):
            self.append_line("✘ 请先选择有效的日志文件")
            return None
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                log_text = f.read()
        except Exception as e:
            self.append_line(f"✘ 读取日志文件失败: {e}")
            # 这里必须兜住所有异常：日志可能是 GBK、可能是正在被写入的半截文件，
            # 任何一种都不该把整个工具页带崩，只提示这一次读取失败即可
            return None
        video_name = os.path.basename(log_path).replace('.log', '.mp4')
        pending_root = self.edit_pending.text().strip()
        videos_root = self.edit_videos.text().strip()
        # 两个目录都要求已存在，不做自动创建：路径写错时静默建目录比报错更难排查
        if not os.path.isdir(pending_root):
            self.append_line(f"✘ 待处理目录不存在: {pending_root}")
            return None
        if not os.path.isdir(videos_root):
            self.append_line(f"✘ 视频输出目录不存在: {videos_root}")
            return None
        # 返回值就是 Worker 的构造参数，顺序与字段名都不能随意调整
        return {
            "log_text": log_text,
            "start_frame": self.spin_start.value(),
            "end_frame": self.spin_end.value(),
            "player": self.spin_player.value(),
            "session_code": self.edit_session_code.text().strip(),
            "session_date": self.edit_date.text().strip(),
            "video_name": video_name,
            "session_name": self.edit_session_name.text().strip(),
            "round_num": self.spin_round.value(),
            "format_str": self.edit_format.text().strip(),
            "user_ava_0": self.edit_ava_0.text().strip(),
            "user_ava_1": self.edit_ava_1.text().strip(),
            "user_name_0": self.edit_user_0.text().strip(),
            "user_name_1": self.edit_user_1.text().strip(),
            "pending_root": pending_root,
            "videos_root": videos_root,
        }

    # ---------- 生成 ----------

    def _on_start(self):
        """开始生成：busy 守卫、依赖守卫、参数校验，任何一道不过都直接返回

        依赖守卫放在点击时而不是模块顶层：缺 opencv 时整个工具页仍要能用，
        真正要渲染了才提示装包，也不会让一次 import 失败拖垮整页。
        """
        win = self._win
        worker = getattr(win, "_single_video_worker", None)
        # getattr 带默认值：属性首次由本方法创建，主窗口上还不存在
        if worker is not None and worker.isRunning():
            win._show_info_bar("单杆视频正在生成中，请等待完成", "warning")
            return
        try:
            import cv2  # noqa: F401  依赖守卫（与弹窗入口一致）
            import numpy  # noqa: F401
        except ImportError as e:
            win._show_info_bar(
                f"无法加载单杆视频依赖（请确认已安装 opencv-python、numpy）: {e}",
                "error", duration=5000)
            return
        params = self.collect_params()
        if not params:
            # collect_params 已经把失败原因写进输出区，这里只负责中止，不重复提示
            return
        # 记住本次路径配置，下次直接作为默认值
        win._save_settings({"single_pending_root": params["pending_root"],
                           "single_videos_root": params["videos_root"]})
        self._enter_running()
        # 先切运行态再起线程：Worker 可能在 start() 后立刻发信号，界面必须已经就位
        w = SingleVideoWorker(params)
        # 不传 parent：Worker 是 QThread，挂到控件下会随控件销毁而中断，用主窗口属性持有
        win._single_video_worker = w          # 与弹窗入口共享 busy 守卫
        w.line.connect(self.append_line)
        w.finished_ok.connect(self._on_finished)
        w.error.connect(self._on_failed)
        w.finished.connect(lambda w=w: win._release_worker_safe(w))
        w.start()
        win._append_log(f"[单杆] 开始生成单杆视频: {params['video_name']}")

    def _on_finished(self, video_path):
        """生成成功：先摘共享引用、再回收线程、最后刷新界面

        顺序不能反。界面重绘期间用户可能再点一次开始，此时若还挂着正在析构的
        worker，新任务会被 busy 守卫判成「正在运行」而白白卡住。
        """
        win = self._win
        w = getattr(win, "_single_video_worker", None)
        win._single_video_worker = None
        if w is not None:
            win._release_worker_safe(w)
        self.append_line("")
        self.append_line(f"✔ 单杆视频生成完成: {video_path}")
        self._phase = "done"
        self._out_dir = os.path.dirname(video_path) if video_path else ""
        # 记住输出目录，「打开视频目录」按钮只认它；空串表示这次没产出文件
        self.btn_start.setEnabled(True)
        self.btn_start.setText("开始生成")
        self.btn_browse.setEnabled(True)
        self.btn_open_out.setVisible(bool(self._out_dir))
        # 只有真拿到文件路径才亮出打开按钮，避免点开一个不存在的目录
        win._append_log(f"[单杆] 单杆视频生成完成: {video_path}")
        win._show_info_bar("单杆视频生成完成，可点击「打开视频目录」查看",
                           "success", duration=4000)
        # 生成后自动打开所在目录（设置 → 工具，2026-09-07）
        # isdir 再判一次：Worker 报告成功并不保证目录还在，用户可能中途清理过
        if self._auto_open_enabled() and self._out_dir and os.path.isdir(self._out_dir):
            try:
                os.startfile(self._out_dir)
                self.append_line("▶ 已自动打开视频所在目录")
            except Exception:
                pass

    def _on_failed(self, msg):
        """生成失败：与成功回调同一套收尾顺序，状态落到 idle 而不是 done

        idle 而非 done，用户才能改完参数直接重试；done 会让按钮停在完成态。
        """
        win = self._win
        w = getattr(win, "_single_video_worker", None)
        win._single_video_worker = None
        if w is not None:
            win._release_worker_safe(w)
        self.append_line("")
        self.append_line(f"✘ {msg}")
        self._phase = "idle"
        self.btn_start.setEnabled(True)
        self.btn_start.setText("开始生成")
        self.btn_browse.setEnabled(True)
        win._append_log(f"[单杆] 单杆视频生成失败: {msg}")
        win._show_info_bar(msg, "error", duration=4000)

    def _enter_running(self):
        """统一切到运行态；三处调用点各写一遍很容易漏掉某个按钮的禁用。"""
        self._phase = "running"
        self.btn_start.setEnabled(False)
        self.btn_start.setText("生成中...")
        self.btn_browse.setEnabled(False)
        # 上一轮的输出目录已经过期，先收起按钮，成功回调里再按新路径决定是否显示
        self.btn_open_out.hide()

    def _on_open_out(self):
        """打开最近一次输出目录；目录被移走时静默忽略，不弹错误框。"""
        if self._out_dir and os.path.isdir(self._out_dir):
            try:
                os.startfile(self._out_dir)
            except Exception:
                pass

    def append_line(self, text):
        """往运行输出追加一行；Worker 的 line 信号直连这里。"""
        self.log_view.append(text)


# ==================== 工作区 2：端口占用 ====================

# 这里占用的是真实 socket，不是伪造的表格状态：只有真 bind 上去，netstat 才会
# 出现对应条目，才能用来验证「某个端口被占时程序表现如何」这类场景。
class PortFakeWork(QWidget):
    """端口占用工作区（2026-09-07 按设计稿改表格）：
    自定义端口/协议/绑定地址 → 真实 socket 监听 → 表格管理，全部释放/逐行释放。
    离开页面自动释放（对齐原「关闭弹窗自动释放」语义）。
    """

    _RANDOM_MIN, _RANDOM_MAX = 20000, 60000
    # 随机区间避开 1024 以下的系统端口与 3306/3389 这类常见服务端口，
    # 减少「随机出来的端口正好有服务在跑」导致的占用失败。

    def __init__(self, win, parent=None):
        """端口占用工作区的骨架：参数行 + 结果表 + 统计行

        表格行与 _sockets 靠三元组对应，页面隐藏或关闭时由 hideEvent/closeEvent
        兜底释放，不能指望用户记得点「全部释放」。
        """
        super().__init__(parent)
        self.setObjectName("portFakeWork")
        _transparent(self)
        self._win = win
        # key 必须带上绑定地址：0.0.0.0:8080 与 127.0.0.1:8080 是两个可以共存的监听
        self._sockets = {}   # key=(port, proto, addr) -> socket

        body = QWidget(self)
        body.setAutoFillBackground(False)
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 8, 20, 12)
        lay.setSpacing(12)

        card = CardWidget(body)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(12)

        cl.addWidget(BodyLabel(
            "点击「占用」后端口被真实监听，netstat -ano 可见 LISTENING，模拟本机有服务在监听；"
            "支持多端口同时占用，离开本页自动全部释放。", card))

        # 参数行：端口 / 协议 / 绑定地址 / 随机 / 占用 / 全部释放
        # 参数行整体横排、尾部 addStretch 收口，窗口变宽时控件不会被拉开
        prow = QHBoxLayout()
        prow.addWidget(BodyLabel("端口:", card))
        # FittedSpinBox 宽度跟内容走：端口从 3 位变 5 位时不会跳版。
        self.spin_port = FittedSpinBox(card)
        self.spin_port.setRange(1, 65535)
        # 端口 0 表示「让系统随便挑」，这里不提供该语义：用户填 0 会被 bind 成一个
        # 未知端口，表格里显示的数字与实际监听的对不上
        self.spin_port.setValue(self._random_port())
        # 默认就填一个空闲端口：多数使用场景是「随便占一个」，让用户少点一次随机
        prow.addWidget(self.spin_port)
        prow.addSpacing(10)
        prow.addWidget(BodyLabel("协议:", card))
        self.combo_proto = ComboBox(card)
        self.combo_proto.addItems(["TCP", "UDP"])
        # 只提供 TCP/UDP：ICMP 需要 raw socket，Windows 上非管理员直接没有权限
        self.combo_proto.setFixedWidth(90)
        prow.addWidget(self.combo_proto)
        prow.addSpacing(10)
        prow.addWidget(BodyLabel("绑定地址:", card))
        # 地址可编辑是为了绑定指定网卡地址，排查「只监听回环」与「监听全部网卡」的差异
        self.combo_addr = EditableComboBox(card)
        self.combo_addr.addItems(["0.0.0.0", "127.0.0.1"])
        self.combo_addr.setCurrentText("0.0.0.0")
        self.combo_addr.setFixedWidth(140)
        prow.addWidget(self.combo_addr)
        prow.addStretch(1)
        btn_random = PushButton(FluentIcon.SYNC, "随机端口", card)
        btn_random.clicked.connect(lambda: self.spin_port.setValue(self._random_port()))
        # 每次点击都重新探测，而不是复用上一次的结果：中间可能已被别的程序占用
        prow.addWidget(btn_random)
        # 占用是主操作给 Primary；释放默认禁用，没有占用时点它无事可做。
        self.btn_occupy = PrimaryPushButton(FluentIcon.CONNECT, "占用", card)
        self.btn_occupy.clicked.connect(self._occupy)
        prow.addWidget(self.btn_occupy)
        self.btn_release = PushButton(FluentIcon.DELETE, "全部释放", card)
        self.btn_release.setEnabled(False)
        self.btn_release.clicked.connect(lambda: self._release_all(notify=True))
        prow.addWidget(self.btn_release)
        cl.addLayout(prow)

        # 表格：端口 / 协议 / 状态 / 绑定地址 / 操作
        # 五列固定，行 key 与 _sockets 的 (port, proto, addr) 一一对应。
        self.table = TableWidget(card)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["端口", "协议", "状态", "绑定地址", "操作"])
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        # 协议列内容固定三个字符，按内容给宽即可；其余列拉伸吸收窗口宽度变化
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(4, 96)
        self.table.setMinimumHeight(220)
        # 给一个下限：空表时若完全跟随内容收缩，页面会在占用第一行时整体跳一下
        cl.addWidget(self.table, 1)

        self.lbl_stat = CaptionLabel("未占用任何端口", card)
        # 统计行同时承担空态提示，省掉一个单独的占位控件
        cl.addWidget(self.lbl_stat)

        lay.addWidget(card, 1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(body)

    # ---------- 端口操作 ----------

    def _random_port(self):
        """随机挑一个当前能 bind 的端口，供输入框预填与「随机端口」按钮使用

        只探测不 listen，探测完立刻 close：真正的占用由 _occupy 重新 bind，
        两次之间仍可能被别的进程抢走，属于可接受竞态。
        """
        for _ in range(20):
            # 试 20 次足够了：区间内有两万个端口，连撞 20 次说明系统端口分配很紧张
            port = random.randint(self._RANDOM_MIN, self._RANDOM_MAX)
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.bind(("0.0.0.0", port))
                s.close()
                return port
                # 探测用 0.0.0.0：只有在所有网卡上都能绑，才算真正空闲
            except OSError:
                s.close()
        return random.randint(self._RANDOM_MIN, self._RANDOM_MAX)
        # 探测全失败时仍然返回一个随机值：让 _occupy 去报出真实错误，
        # 比在这里静默失败、用户点了没反应要好

    def _occupy(self):
        """真正 bind 并占用端口：建 socket → 复用 → bind → TCP 补 listen → 落表

        任何一步抛错都先 close 再提示，绝不把半初始化的 socket 留在字典里，
        否则「释放」按钮会拿着一个永远关不掉的句柄。
        """
        port = self.spin_port.value()
        proto = self.combo_proto.currentText().strip() or "TCP"
        addr = self.combo_addr.currentText().strip() or "0.0.0.0"
        key = (port, proto, addr)
        # 同一端口不同协议、不同绑定地址互不冲突，所以判重要用三元组而不是端口号
        if key in self._sockets:
        # 重复占用直接拒绝：再 bind 一次会失败，但错误信息不如这句直白
            self._win._show_info_bar(f"端口 {port} ({proto} {addr}) 已在占用中",
                                     "warning", duration=2000)
            return
        sock_type = socket.SOCK_STREAM if proto == "TCP" else socket.SOCK_DGRAM
        # 协议到 socket 类型的映射只有这两支，Combo 已经保证不会出现第三种取值
        s = socket.socket(socket.AF_INET, sock_type)
        status = ""
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # 允许立刻重绑处于 TIME_WAIT 的端口，否则刚释放的端口要等一阵才能再占用
            s.bind((addr, port))
            if proto == "TCP":
                s.listen(5)
                # backlog 取 5 即可：这里不处理连接，listen 只是为了让状态进入 LISTENING
                s.setblocking(False)
                status = "LISTENING"
            else:
                s.setblocking(False)
                status = "BOUND"
        except OSError as e:
            s.close()
            self._win._show_info_bar(f"端口 {port} ({proto}) 占用失败：{e.strerror or e}",
                                     "error", title="失败", duration=3000)
            return
        self._sockets[key] = s
        # 成功之后才登记，保证字典里的每个 socket 都是可用的
        self._add_row(key, status)
        # 一旦有占用就允许释放，避免用户先占用成功、按钮却还是灰的
        self.btn_release.setEnabled(True)
        self._update_stat()
        self._win._show_info_bar(f"端口 {port} ({proto} {addr}) 已占用（{status}）",
                                 "success", title="占用成功", duration=2500)

    def _add_row(self, key, status):
        """按 (port, proto, addr) 追加一行，操作列放「释放」按钮

        行号不保证与字典顺序一致（删除后会重排），释放时靠三元组文本回查，
        因此这里不需要把行号记进字典。
        """
        port, proto, addr = key
        r = self.table.rowCount()
        self.table.insertRow(r)
        it = QTableWidgetItem(str(port))
        it.setFont(_term_font())
        self.table.setItem(r, 0, it)
        self.table.setItem(r, 1, QTableWidgetItem(proto))
        st = QTableWidgetItem(status)
        st.setForeground(_C_SUCCESS)
        self.table.setItem(r, 2, st)
        at = QTableWidgetItem(addr)
        at.setFont(_term_font())
        self.table.setItem(r, 3, at)
        btn = PushButton("释放", self.table)
        btn.setFixedHeight(26)
        btn.clicked.connect(lambda checked=False, k=key: self._release_one(k))
        # clicked 会带一个 checked 位置参数，必须用默认参数吞掉并同时绑定 key，
        # 否则释放的是「当前循环最后一行」而不是按钮所在行
        self.table.setCellWidget(r, 4, btn)

    def _release_one(self, key):
        """释放单个端口：先摘字典再关 socket，close 抛错也不留悬空条目

        关 socket 失败只影响内核回收时机，状态必须已经清掉，否则界面与字典不一致。
        """
        s = self._sockets.pop(key, None)
        if s is not None:
            try:
                s.close()
            except OSError:
                pass
        self._remove_row(key)
        self._update_stat()
        if not self._sockets:
            self.btn_release.setEnabled(False)

    def _remove_row(self, key):
        """按三元组文本找行删除；行已被删或表格重建过时静默返回

        逐列判 None 是因为 removeRow 之后 item 会短暂失效，直接取 text() 会崩。
        """
        port, proto, addr = key
        for r in range(self.table.rowCount()):
            it0 = self.table.item(r, 0)
            if (it0 and it0.text() == str(port)
                    and self.table.item(r, 1) and self.table.item(r, 1).text() == proto
                    and self.table.item(r, 3) and self.table.item(r, 3).text() == addr):
                self.table.removeRow(r)
                return

    def _release_all(self, notify=True):
        """释放全部端口并清空表格；notify=False 供离开页面时静默清理

        离开页面时用户已经看不到本页，再弹「已释放全部端口」只会打扰下一屏操作。
        """
        for key in list(self._sockets):
        # 先拷贝 key 列表：循环体里 pop 会改变字典大小，直接迭代会抛 RuntimeError
            s = self._sockets.pop(key, None)
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        self.table.setRowCount(0)
        self.btn_release.setEnabled(False)
        self._update_stat()
        if notify:
            self._win._show_info_bar("已释放全部端口", "success",
                                     title="释放成功", duration=2000)

    def _update_stat(self):
        """按 _sockets 现有条目刷新统计行，不额外维护计数器以免两处状态打架。"""
        n = len(self._sockets)
        if n == 0:
            self.lbl_stat.setText("未占用任何端口")
            return
        tcp = sum(1 for (_p, pr, _a) in self._sockets if pr == "TCP")
        # UDP 用总数相减而不是再扫一遍：两个分支的判定条件互补，结果必然一致
        udp = n - tcp
        self.lbl_stat.setText(f"已占用 {n} 个端口 · TCP {tcp} · UDP {udp}")

    def hideEvent(self, e):
        """切走页面即释放全部端口

        旧版是弹窗，关窗即释放；升级成常驻页面后必须显式补上这条路径，
        否则端口会被一直占着，直到整个程序退出。
        """
        super().hideEvent(e)
        self._release_all(notify=False)

    def closeEvent(self, e):
        """程序退出时补一次释放；窗口直接销毁时不会走 hideEvent，这里兜底。"""
        self._release_all(notify=False)
        super().closeEvent(e)


# ==================== 工作区 3：上传清单 ====================

class UploadListWork(QWidget):
    """上传清单工作区（2026-09-07 按设计稿改复选框列表）：
    扫描 {videos_dir}/upload 下文件 → 复选框/文件名/类型/大小 表格 + 表头全选
    → 勾选打包上传（ZipUploadWorker files 白名单，仅删已上传文件）。
    """

    def __init__(self, win, parent=None):
        """搭出清单表格与底部操作条，并立刻扫一次 upload 目录

        构造时就 refresh：本页可能不是首屏，但用户切回来必须看到当前磁盘状态，
        而不是上次离开时的快照。
        """
        super().__init__(parent)
        self.setObjectName("uploadListWork")
        _transparent(self)
        self._win = win
        self._worker = None
        self._rows = []          # [(abs_path, size)] 与表格行同序

        body = QWidget(self)
        body.setAutoFillBackground(False)
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 8, 20, 12)
        lay.setSpacing(12)

        card = CardWidget(body)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)

        # 路径只回显不缓存：videos_dir 可在设置里改，切页重扫比监听配置信号省事。
        self.lbl_root = BodyLabel("收集目录: —", card)
        cl.addWidget(self.lbl_root)

        # 表格：复选框 / 文件名 / 类型 / 大小
        # 四列固定，列序是 _add_row / _row_checked / _checked_files 的共同契约。
        self.table = TableWidget(card)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["", "文件名", "类型", "大小"])
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # 列宽（2026-09-07 反馈）：文件名列 Stretch 吃满剩余宽度→表格铺满卡片，
        # 文字保持左对齐；复选固定 40，类型/大小按内容自适应，末列不重复拉伸
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setStretchLastSection(False)
        self.table.setColumnWidth(0, 40)
        self.table.setMinimumHeight(260)
        cl.addWidget(self.table, 1)
        # 表头第 0 列放全选复选框（常开三态：部分勾选时显示半选）
        # 复选框只能浮在表头上：header 不支持嵌入控件，位置由 _position_header_checkbox 手动摆。
        self.chk_all = CheckBox("", self.table.horizontalHeader())
        self.chk_all.setTristate(True)  # 仅为回显半选；用户单击由 clicked 接管
        self._syncing = False           # 程序同步勾选态时抑制 clicked/itemChanged 递归
        self.chk_all.clicked.connect(self._on_header_clicked)
        # 勾选列=可勾选 item（P0 2026-09-25）：qfw TableItemDelegate 对
        # CheckStateRole 原生绘制 Fluent 勾选框，零子控件（health_page 同款范式）
        self.table.itemChanged.connect(self._on_item_changed)

        # 进度条常驻但默认隐藏，只在打包上传期间出现；4px 高度平时也不占版面。
        self.progress_bar = ProgressBar(card)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.hide()
        cl.addWidget(self.progress_bar)

        bottom = QHBoxLayout()
        # 底部一排：统计 → 已选 → 拉伸 → 刷新/打开/删除/上传，主操作在最右。
        self.lbl_total = CaptionLabel("", card)
        bottom.addWidget(self.lbl_total)
        self.lbl_progress = CaptionLabel("", card)
        bottom.addWidget(self.lbl_progress)
        bottom.addStretch(1)
        btn_refresh = PushButton(FluentIcon.SYNC, "刷新", card)
        # 刷新不加确认：扫描只读，连点最多多扫一遍目录。
        btn_refresh.clicked.connect(self.refresh)
        bottom.addWidget(btn_refresh)
        btn_open = PushButton(FluentIcon.FOLDER, "打开目录", card)
        btn_open.clicked.connect(self._open_dir)
        bottom.addWidget(btn_open)
        self.btn_delete = PushButton(FluentIcon.DELETE, "删除所选", card)
        self.btn_delete.setToolTip("删除勾选的文件（弹出二次确认，仅删 upload 目录内文件）")
        # 删除是这里唯一不可撤销的操作，文案与 tooltip 都写死范围（仅 upload 目录内）。
        self.btn_delete.clicked.connect(self._on_delete_checked)
        bottom.addWidget(self.btn_delete)
        self.btn_package = PrimaryPushButton(FluentIcon.SEND, "打包上传", card)
        self.btn_package.setToolTip(
            "将勾选的文件打包 zip 上传服务器，成功后删除已上传文件")
        # 同一个按钮先后承担「打包上传」和「取消上传」，分流在 _on_package_upload 首行。
        self.btn_package.clicked.connect(self._on_package_upload)
        bottom.addWidget(self.btn_package)
        cl.addLayout(bottom)

        lay.addWidget(card, 1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(body)

        self.refresh()

    # ---------- 清单扫描 ----------

    def _upload_root(self):
        """返回 {videos_dir}/upload 绝对路径；videos_dir 未配置时返回空串

        不退回当前目录：空串让各调用点显式走「未配置」分支，避免误扫仓库根。
        """
        videos_dir = getattr(self._win, "videos_dir", "") or ""
        return os.path.join(videos_dir, "upload") if videos_dir else ""

    @staticmethod
    def _type_of(name):
        """按扩展名归类到视频/日志/清单/其他；未知扩展名归「其他」而不是报错

        归类只影响显示与着色，不参与上传、删除的白名单判定，所以没有必要为
        一个认不出的后缀打断整次扫描。
        """
        ext = os.path.splitext(name)[1].lower()
        if ext in (".mp4", ".avi", ".mkv", ".mov"):
            return "视频"
        if ext in (".log", ".txt"):
            return "日志"
        if ext in (".json",):
            return "清单"
        return "其他"

    def refresh(self):
        """扫描 upload 目录（递归）构建复选框表格

        全量重建而不是增量 diff：文件数通常几百以内，增量方案更容易在删除或
        改名后漏行。单个文件取大小失败只记 0 字节，不让一个坏文件废掉整页。
        """
        # 全量重建：文件数通常几百以内，比增量 diff 更不容易漏行或错位。
        self.table.setRowCount(0)
        self._rows = []
        root = self._upload_root()
        # videos_dir 为空时不能退回当前目录，否则会把仓库根扫进清单。
        if not root:
            self.lbl_root.setText("收集目录: —（请先在设置 → 应用配置 中配置视频/日志目录）")
            self.lbl_total.setText("")
            return
        self.lbl_root.setText(f"收集目录: {root}")
        if not os.path.isdir(root):
            self.lbl_total.setText("暂无待上传文件，请先右键日志文件→添加到上传目录")
            return
        total_size = 0
        # 重建期间屏蔽 itemChanged：否则每行 setCheckState 都会触发
        # _on_row_checked → _sync_check_all 全表扫描，放大成 O(n²)
        self.table.blockSignals(True)
        try:
            # 递归是必须的：右键「添加到上传目录」会按日期建子目录，只扫一层会漏
            for cur, _dirs, files in os.walk(root):
                # 按名字排序而不是用目录枚举顺序，刷新前后行序才稳定，勾选不会乱跳
                for name in sorted(files):
                    full = os.path.join(cur, name)
                    try:
                        size = os.path.getsize(full)
                    except OSError:
                        size = 0
                    total_size += size
                    rel = os.path.relpath(full, root)
                    self._add_row(full, rel, self._type_of(name), size)
        finally:
            self.table.blockSignals(False)
        self.lbl_total.setText(
            f"共 {len(self._rows)} 个文件，总大小 {_fmt_size(total_size)}")
        self._sync_check_all()
        self._position_header_checkbox()

    def _add_row(self, full, rel, typ, size):
        """插入一行：勾选 item / 相对路径 / 类型 / 大小，列序与表头一致"""
        r = self.table.rowCount()
        self.table.insertRow(r)
        self._rows.append((full, size))
        # 勾选列改可勾选 item（P0 2026-09-25）：原 CheckBox+QWidget 容器
        # 每行 2 个真控件，QSS 装配 + setCellWidget 布局随文件数线性放大
        # （实测 1000 个 qfw 控件 ≈ 760ms，tools/perf/perf_ops_links_vs_buttons.py）；
        # item 方案零子控件，重建只付 setItem 成本
        it = QTableWidgetItem()
        it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        it.setCheckState(Qt.CheckState.Unchecked)
        self.table.setItem(r, 0, it)
        ft = QTableWidgetItem(rel)
        ft.setFont(_term_font())
        # 显示相对路径、tooltip 存绝对路径，子目录里的同名文件只能靠 tooltip 区分。
        ft.setToolTip(full)
        self.table.setItem(r, 1, ft)
        tt = QTableWidgetItem(typ)
        # 只有视频用强调色；日志与清单同色，整列才不至于花成调色板。
        tt.setForeground(_C_ACCENT if typ == "视频" else _C_MUTED)
        self.table.setItem(r, 2, tt)
        st = QTableWidgetItem(_fmt_size(size))
        # 大小右对齐：数字位对齐后，扫一眼就能比出量级差异。
        st.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        st.setFont(_term_font())
        self.table.setItem(r, 3, st)

    def _row_checked(self, r):
        """行是否勾选；item 缺失（重建瞬态）按未勾选处理

        setRowCount 之后、setItem 之前会有一段空窗，返回 None 时当作未勾选，
        调用方就不用每处都判空。
        """
        it = self.table.item(r, 0)
        return it is not None and it.checkState() == Qt.CheckState.Checked

    def _on_item_changed(self, item):
        """勾选列 item 翻转（用户点击 / 程序 setCheckState）→ 走原行勾选链路

        itemChanged 对每一列都会发，只有第 0 列与勾选有关，其余列直接忽略，
        否则改个文件名也会触发全表勾选统计。
        """
        if item.column() == 0:
            self._on_row_checked()

    def _on_row_checked(self):
        """行复选框变化：回显表头三态 + 更新已选统计（程序同步期间跳过）"""
        if self._syncing:
            return
        self._sync_check_all()

    def _on_header_clicked(self):
        """表头复选框单击接管（2026-09-07 反馈修复）：

        三态 QCheckBox 单击默认按 未选→半选→全选 循环——空选/半选时点一次
        只到半选，需再点一次才全选。改为按各行实际勾选态决定动作：
        未全选 → 全选；已全选 → 全不选（半选仅作程序回显，用户点击不产生）。
        """
        # 上传中锁住选择：worker 手里是启动瞬间的 files 白名单，此时改勾选只会让界面与实际上传不符
        if self._worker is not None and self._worker.isRunning():
            return
        rows = self.table.rowCount()
        if rows == 0:
            return
        target = not all(self._row_checked(r) for r in range(rows))
        state = Qt.CheckState.Checked if target else Qt.CheckState.Unchecked
        self._syncing = True
        try:
            for r in range(rows):
                self.table.item(r, 0).setCheckState(state)
        finally:
            self._syncing = False
        self._sync_check_all()

    def _sync_check_all(self):
        """按各行勾选态回显表头全选框（全选/部分/未选）

        rows 为 0 与 n 为 0 合并成未选态：空表上半选没有意义，表头也不该显示
        一条横线让人以为有隐藏的勾选。
        """
        rows = self.table.rowCount()
        n = sum(1 for r in range(rows) if self._row_checked(r))
        # 回显本身会触发 toggled，屏蔽信号避免再绕回 _on_header_clicked 形成回环
        self.chk_all.blockSignals(True)
        if rows == 0 or n == 0:
            self.chk_all.setCheckState(Qt.CheckState.Unchecked)
        elif n == rows:
            self.chk_all.setCheckState(Qt.CheckState.Checked)
        else:
            self.chk_all.setCheckState(Qt.CheckState.PartiallyChecked)
        self.chk_all.blockSignals(False)
        self._update_sel_count()

    def _update_sel_count(self):
        """刷新「已选 N 个 · 大小」文案，未勾选时退回文件总数

        求和上界取 min(total, len(_rows))：重建瞬间表格行数会短暂多于行数据，
        直接按下标取 size 会 IndexError。
        """
        total = self.table.rowCount()
        n = sum(1 for r in range(total) if self._row_checked(r))
        sel_size = sum(self._rows[r][1] for r in range(min(total, len(self._rows)))
                       if self._row_checked(r))
        # 未勾选时显示总数而不是「已选 0 个」，空态文案才不至于长期占位。
        base = f"共 {total} 个文件" if total else "暂无待上传文件"
        self.lbl_progress.setText(
            f"已选 {n} 个 · {_fmt_size(sel_size)}" if n else base)

    def resizeEvent(self, e):
        """窗口尺寸变化后重摆表头复选框；它浮在 header 上，表头一移位就会飘到别列。"""
        super().resizeEvent(e)
        self._position_header_checkbox()

    def showEvent(self, e):
        """每次切回本页重扫一次：文件可能是别的页面刚写进 upload 目录的。"""
        super().showEvent(e)
        self.refresh()
        self._position_header_checkbox()

    def _position_header_checkbox(self):
        """把全选复选框摆到表头第 0 列正中

        raise_() 是必须的：复选框作为表格子控件会被表头重绘盖住，抬到最上层才看得见。
        try/except 兜住表头尚未创建时的调用（构造期 refresh 就会走到这里）。
        """
        try:
            hdr = self.table.horizontalHeader()
            x0 = hdr.sectionPosition(0)
            w = hdr.sectionSize(0)
            sz = self.chk_all.sizeHint()
            self.chk_all.setGeometry(
                x0 + max(0, (w - sz.width()) // 2),
                (hdr.height() - sz.height()) // 2,
                sz.width(), sz.height())
            self.chk_all.raise_()
        except Exception:
            pass

    def _open_dir(self):
        """用系统文件管理器打开收集目录；目录不存在时静默忽略

        Windows 对不存在的路径调 startfile 会弹系统级错误框，这个按钮只是
        方便操作，没必要为它多一个报错弹窗。
        """
        root = self._upload_root()
        if root and os.path.isdir(root):
            os.startfile(root)

    # ---------- 打包上传（勾选白名单） ----------

    @staticmethod
    def _upload_target(settings):
        """从设置读上传目标，返回 (host, port, remote_dir, user, password)

        除密码外都给了默认值，缺配置时仍指向约定服务器；密码没有合理默认值，必须显式配置。
        键名逐字对应 upload_*；拼错不会报错，只会静默回落到这里的默认值，所以
        缺字段时先从设置键名查起。端口非法同样回落 22，不在启动路径上抛异常。
        """
        host = str(settings.get("upload_host") or "49.235.34.253").strip()
        try:
            port = int(settings.get("upload_port") or 22)
        except (TypeError, ValueError):
            port = 22
        remote_dir = str(settings.get("upload_remote_dir") or "/lhcos-data/videos").strip()
        username = str(settings.get("upload_user") or "root").strip()
        password = str(settings.get("upload_pass") or "")
        return host, port, remote_dir, username, password

    def _checked_files(self):
        """按行序取勾选文件的绝对路径，上传白名单与删除共用这一份结果

        同一份结果给两个入口用，删除与上传才不会对「哪些文件算勾选」有分歧。
        下标同时推进 _rows，两者按构造顺序严格平行。
        """
        out = []
        for r in range(self.table.rowCount()):
            if self._row_checked(r) and r < len(self._rows):
                out.append(self._rows[r][0])
        return out

    # ---------- 删除所选（二次确认 + 路径安全校验） ----------

    def _safe_upload_path(self, full_path):
        """路径必须位于 upload 根目录内部（防越界删除）

        normcase 抹平大小写与分隔符再比较：Windows 路径大小写不敏感，直接比
        字符串会漏掉写法不同的同一目录，删错文件没有撤销机会。
        """
        root = self._upload_root()
        if not root:
            return None
        root = os.path.normcase(os.path.normpath(os.path.abspath(root)))
        path = os.path.normcase(os.path.normpath(os.path.abspath(full_path)))
        # 根目录本身不是文件，命中说明传参有问题，直接拒绝而不是放行。
        if path == root:
            return None
        try:
            # commonpath 比 startswith 可靠：upload2 不会被误判成 upload 的子路径。
            if os.path.commonpath([root, path]) != root:
                return None
        except ValueError:
            return None
        return path

    def _on_delete_checked(self):
        """删除勾选文件：二次确认弹窗 → 逐个删除（仅 upload 目录内）→ 刷新清单

        上传在跑时直接拒绝：worker 的 cleanup_after_done 会删同一批文件，两边
        同时动手会让上传中途报「文件不存在」，失败原因还很难看出来。
        """
        if self._worker is not None and self._worker.isRunning():
            self._win._show_info_bar("打包上传进行中，请等待完成后再删除",
                                     "warning", duration=2000)
            return
        # 先快照勾选再弹窗：确认框是模态的，期间用户仍可能改表格勾选。
        files = self._checked_files()
        if not files:
            self._win._show_info_bar("请先勾选要删除的文件", "info", duration=3000)
            return
        names = "、".join(os.path.basename(f) for f in files[:5])
        if len(files) > 5:
            names += f" 等 {len(files)} 个"
        box = MessageBox("删除确认",
                         f"确定删除勾选的 {len(files)} 个文件吗？\n{names}\n\n"
                         "该操作不可撤销，仅删除 upload 目录内的文件。", self)
        box.yesButton.setText("删除")
        box.cancelButton.setText("取消")
        # exec() 返回 True 表示点了 yesButton（这里被改写成「删除」）。
        if not box.exec():
            return
        # 逐个删除并计数：单个失败不该中断整批，最后按成功/失败分开报数。
        ok = fail = 0
        for full in files:
            # 删前再校验一次路径，防止弹窗期间 upload 目录被改到别处。
            safe = self._safe_upload_path(full)
            if safe is None:
                fail += 1
                continue
            try:
                if os.path.isfile(safe):
                    os.remove(safe)
                    ok += 1
                else:
                    fail += 1
            except PermissionError:
                fail += 1
            except OSError:
                fail += 1
        self.refresh()
        if fail:
            self._win._show_info_bar(
                f"已删除 {ok} 个，{fail} 个失败（无权限或已不存在）",
                "warning", title="删除完成", duration=4000)
        else:
            self._win._show_info_bar(f"已删除 {ok} 个文件", "success",
                                     title="删除成功", duration=2500)

    def _on_package_upload(self):
        """打包上传入口：空闲时启动 ZipUploadWorker，进行中则转成取消请求

        同一个按钮承担启动与取消，分流放在首行：worker 在跑就转取消请求，否则
        才走启动分支，避免为一个「取消」再占一个按钮位置。
        """
        if self._worker is not None and self._worker.isRunning():
            self._on_cancel_upload()
            return
        # 未配置目录、没有勾选都提前拦住，避免把空 zip 传到服务器。
        root = self._upload_root()
        if not root:
            self._win._show_info_bar("videos_dir 未配置，请先在设置中配置视频/日志目录",
                                     "warning", duration=3000)
            return
        files = self._checked_files()
        if not files:
            self._win._show_info_bar("请先勾选要上传的文件", "info", duration=3000)
            return
        host, port, remote_dir, username, password = self._upload_target(
            self._win._load_settings())
        # 空密码会让 Paramiko 卡在认证重试，必须前置提示而不是等 worker 报错。
        if not password:
            self._win._show_info_bar(
                "未配置上传密码，请先在设置 → 应用配置 → 收集与上传中填写后重试",
                "warning", duration=4000)
            return
        box = MessageBox(
            "打包上传",
            f"将把勾选的 {len(files)} 个文件打包为 zip，上传到\n{host}:{remote_dir}\n\n"
            "上传成功后将删除这些已上传文件，确定继续？", self)
        box.yesButton.setText("上传")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        # 文案翻转就是状态提示：按钮此刻的语义是「取消」，不是再传一次。
        self.btn_package.setText("取消上传")
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        # files 白名单 + cleanup_after_done：只删真正上传成功的文件，没勾选的留在本地。
        worker = ZipUploadWorker(root, host, port, username, password, remote_dir,
                                 files=files, cleanup_after_done=True)
        self._worker = worker
        worker.progress.connect(self.lbl_progress.setText)
        worker.percent.connect(self._on_percent)
        worker.done.connect(self._on_done)
        worker.error.connect(self._on_fail)
        worker.cancelled.connect(self._restore_ui)
        worker.start()
        self._win._append_log(f"[上传清单] 开始打包上传 {len(files)} 个文件")

    def _on_cancel_upload(self):
        """二次确认后发中断请求；worker 在下一个分块边界退出并回 cancelled

        确认框是模态的，等待期间 worker 可能自己跑完并复位按钮，所以拿到 yes
        之后还要再查一次 isRunning。
        """
        box = MessageBox("取消上传", "上传进行中，确定取消？", self)
        box.yesButton.setText("确定取消")
        box.cancelButton.setText("继续上传")
        if not box.exec():
            return
        if self._worker is not None and self._worker.isRunning():
            self.btn_package.setEnabled(False)
            self.lbl_progress.setText("正在取消...")
            self._worker.requestInterruption()

    def _on_percent(self, p):
        """进度回调；进度条常驻隐藏，只有真开始传了才亮出来，空闲时不占版面。"""
        if self.progress_bar.isHidden():
            self.progress_bar.show()
        self.progress_bar.setValue(p)
        self.lbl_progress.setText(f"上传中 {p}%")

    def _restore_ui(self):
        """把按钮、进度条恢复成空闲态，并回收 worker 线程

        中断请求发出后线程可能还在跑，直接 deleteLater 会触发 QThread 运行中被销毁。
        """
        w = self._worker
        self._worker = None
        try:
            if w is not None:
                if w.isRunning():
                    w.finished.connect(lambda w=w: w.deleteLater())
                else:
                    w.deleteLater()
        except RuntimeError:
            pass
        self.btn_package.setText("打包上传")
        self.btn_package.setEnabled(True)
        self.lbl_progress.setText("")
        self.progress_bar.hide()
        self.progress_bar.setValue(0)

    def _on_done(self, info):
        """上传成功：恢复 UI 后重扫清单，worker 已把上传过的文件从本地删掉"""
        self._restore_ui()
        self.refresh()   # 已上传文件被 worker 删除 → 刷新清单
        self._win._show_info_bar(f"{info} · 已上传文件已从本地删除", "success",
                                 duration=5000)

    def _on_fail(self, msg):
        """上传失败：info bar 只放首行，完整报错留给运行输出与日志"""
        self._restore_ui()
        self._win._show_info_bar(msg.split(chr(10))[0], "error",
                                 title="上传失败", duration=5000)


# ==================== 工作区 4：视频与日志批量整理 ====================

class NewLogWork(QWidget):
    """批量整理工作区：署名筛选 → NewLogWorker 归类 → 打包上传（两段式）"""

    def __init__(self, win, parent=None):
        """两段式工作区：整理参数 + 运行输出 + 进度条，状态机初值 idle

        整理与上传共用一个主按钮，_phase 决定按钮此刻的语义；两个 worker 各自
        挂在主窗口属性上，跨页切换时任务不会因为页面被隐藏而中断。
        """
        super().__init__(parent)
        self.setObjectName("newlogWork")
        _transparent(self)
        self._win = win
        self._phase = "idle"
        self._out_path = ""
        # 预填上次署名：同一批视频常分几次整理，切页回来不用重敲。
        default_name = str(win._load_settings().get("newlog_target_name", "") or "")

        body = QWidget(self)
        body.setAutoFillBackground(False)
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 8, 20, 12)
        lay.setSpacing(12)

        card = CardWidget(body)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)

        cl.addWidget(BodyLabel(
            "按 Excel「署名」列筛选记录，批量归类对应视频与日志；完成后可一键打包上传服务器。",
            card))

        row = QHBoxLayout()
        row.addWidget(BodyLabel("筛选署名:", card))
        self.target_edit = LineEdit(card)
        self.target_edit.setText(default_name)
        self.target_edit.setPlaceholderText("按 Excel 中「署名」列筛选")
        self.target_edit.setFixedWidth(320)
        row.addWidget(self.target_edit)
        row.addStretch(1)
        cl.addLayout(row)

        btns = QHBoxLayout()
        self.btn_go = PrimaryPushButton(FluentIcon.SEND, "开始整理", card)
        self.btn_go.clicked.connect(self._on_go)
        btns.addWidget(self.btn_go)
        self.btn_open_out = PushButton(FluentIcon.FOLDER, "打开结果目录", card)
        self.btn_open_out.clicked.connect(self._on_open_out)
        # 结果目录要整理成功才存在，先藏起来，_on_organized 里再显示。
        self.btn_open_out.hide()
        btns.addWidget(self.btn_open_out)
        btns.addStretch(1)
        cl.addLayout(btns)

        out_card = CardWidget(body)
        ol = QVBoxLayout(out_card)
        ol.setContentsMargins(16, 14, 16, 14)
        ol.setSpacing(8)
        ol.addWidget(CaptionLabel("运行输出:", out_card))
        # height=None 让终端吃满卡片剩余高度，与单杆视频页保持同一套手感。
        self.log_view = _make_terminal(out_card, None)
        ol.addWidget(self.log_view, 1)

        self.progress_bar = ProgressBar(out_card)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.hide()
        ol.addWidget(self.progress_bar)

        # 与单杆视频页同构：参数区 / 运行输出之间可上下拖动改高度
        self.splitter = _make_vsplitter(body, card, out_card)
        lay.addWidget(self.splitter, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(body)

    def _on_go(self):
        """唯一主按钮的三态分派：空闲→整理，已整理→上传，上传中→取消

        organized 态直接进上传而不重跑整理：整理要扫全盘视频，重复点按钮不该
        重复付这个代价，需要重整理时改署名即可。
        """
        if self._phase == "uploading":
            self._cancel_upload()
        elif self._phase == "organized":
            self._start_upload()
        elif self._phase == "idle":
            self._start_organize()

    def _start_organize(self):
        """启动整理任务：依赖守卫 → 参数校验 → 置 running 态 → 拉起 NewLogWorker

        两个 worker 守卫分别对应整理与上传，二者共用一个按钮，不能同时跑。
        """
        win = self._win
        if self._phase == "organizing":
            return
        worker = getattr(win, "_newlog_worker", None)
        if worker is not None and worker.isRunning():
            win._show_info_bar("整理任务正在运行中，请等待完成", "warning")
            return
        up = getattr(win, "_newlog_upload_worker", None)
        if up is not None and up.isRunning():
            win._show_info_bar("打包上传进行中，请等待完成或先取消", "warning")
            return
        try:
            # newlog 顶层 import openpyxl，缺依赖要在 UI 线程里就报出来，而不是在 worker 里崩。
            from windows.tools import newlog  # noqa: F401  依赖守卫（newlog 顶层 import openpyxl）
        except ImportError as e:
            win._show_info_bar(f"无法加载 newlog 模块（请确认已安装 openpyxl）: {e}",
                               "error", duration=5000)
            return
        target = self.target_edit.text().strip()
        if not target:
            win._show_info_bar("请输入筛选署名", "warning")
            return
        # 先落配置再起线程：worker 失败也能保住用户刚输入的署名。
        win._save_settings({"newlog_target_name": target})
        self._phase = "organizing"
        self.target_edit.setEnabled(False)
        self.btn_go.setEnabled(False)
        self.btn_go.setText("整理中...")
        w = NewLogWorker(target)
        win._newlog_worker = w
        w.line.connect(self.append_line)
        w.finished_ok.connect(self._on_organized)
        w.error.connect(self._on_organize_failed)
        # lambda 绑 w 防闭包晚绑定；运行中的 QThread 交给 _release_worker_safe 延迟销毁。
        w.finished.connect(lambda w=w: win._release_worker_safe(w))
        w.start()
        win._append_log(f"[整理] 开始视频/日志批量整理（署名：{target}）")

    def _on_organized(self, out_path):
        """整理完成：记下输出目录，主按钮从「整理中」翻成「打包上传」

        输出目录先落到 _out_path 再显示按钮：按钮的点击处理只认这个字段，
        提前显示会让用户点开一个还没赋值的路径。
        """
        win = self._win
        w = getattr(win, "_newlog_worker", None)
        # 先摘共享引用再回收：_start_organize 的 inRunning 守卫读的正是这个属性
        win._newlog_worker = None
        if w is not None:
            win._release_worker_safe(w)
        self.append_line("")
        self.append_line(f"✔ 整理完成，输出目录: {out_path}")
        self._out_path = out_path
        self._phase = "organized"
        self.target_edit.setEnabled(True)
        self.btn_go.setEnabled(True)
        self.btn_go.setText("打包上传")
        self.btn_open_out.show()
        win._append_log(f"[整理] 视频/日志批量整理完成: {out_path}")
        win._show_info_bar("整理完成，可点击「打包上传」上传服务器",
                           "success", duration=4000)

    def _on_organize_failed(self, msg):
        """整理失败：退回 idle 态，保留署名输入，用户改完可以直接重试"""
        win = self._win
        w = getattr(win, "_newlog_worker", None)
        win._newlog_worker = None
        if w is not None:
            win._release_worker_safe(w)
        self.append_line("")
        self.append_line(f"✘ {msg}")
        self._phase = "idle"
        self.target_edit.setEnabled(True)
        self.btn_go.setEnabled(True)
        self.btn_go.setText("开始整理")
        win._append_log(f"[整理] 视频/日志批量整理失败: {msg}")
        win._show_info_bar(msg, "error", duration=4000)

    def _start_upload(self):
        """打包上传整理产物：输出目录必须非空，临时 zip 落 upload 目录但不进清单

        cleanup_after_done 关掉，因为这里的产物是整理结果本身，删掉等于毁掉本地成果；
        remove_zip_after_done 打开，临时 zip 不留在 upload 里占地方。
        """
        win = self._win
        running = getattr(win, "_newlog_upload_worker", None)
        if running is not None and running.isRunning():
            win._show_info_bar("已有上传进行中，请稍候", "warning")
            return
        # 目录被移走或整理结果为空时上传没有意义，先拦掉再提示。
        if not self._out_path or not os.path.isdir(self._out_path) \
                or not os.listdir(self._out_path):
            win._show_info_bar("整理输出目录为空，无法打包上传", "warning")
            return
        videos_dir = getattr(win, "videos_dir", "") or ""
        if not videos_dir or not os.path.isdir(videos_dir):
            win._show_info_bar(
                "videos_dir 未配置或目录不存在，请先在设置中配置视频/日志目录",
                "warning", duration=4000)
            return
        host, port, remote_dir, username, password = UploadListWork._upload_target(
            win._load_settings())
        if not password:
            self.append_line("✘ 未配置上传密码（upload_pass），请在设置中配置")
            win._show_info_bar(
                "未配置上传密码，请先在设置 → 应用配置 → 收集与上传中填写后重试",
                "warning", duration=4000)
            return
        # zip 落在 upload 目录：复用同一份目标机配置与磁盘空间，也方便用户自己找。
        upload_root = os.path.join(videos_dir, "upload")
        worker = ZipUploadWorker(
            upload_root, host, port, username, password, remote_dir,
            content_root=self._out_path, zip_prefix="newlog", zip_dir=upload_root,
            cleanup_after_done=False, remove_zip_after_done=True)
        win._newlog_upload_worker = worker
        # 先接线再 start：反过来会丢掉启动瞬间就发出的 error/done。
        worker.progress.connect(self.append_line)
        worker.percent.connect(self._on_upload_percent)
        worker.done.connect(self._on_upload_done)
        worker.error.connect(self._on_upload_fail)
        worker.cancelled.connect(self._on_upload_cancelled)
        self._phase = "uploading"
        # 上传期间锁住署名：改了只会影响下一次整理，留着可编辑反而让人以为本次生效
        self.target_edit.setEnabled(False)
        self.btn_go.setText("取消上传")
        # 先亮进度条再 start：worker 可能在第一块就发 percent，晚显示会看到进度条突然蹦出来
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.append_line("")
        self.append_line(f"▶ 开始打包上传 → {host}:{remote_dir}")
        worker.start()
        win._append_log(f"[整理] 开始打包上传整理产物: {os.path.basename(self._out_path)}")

    def _cancel_upload(self):
        """二次确认后中断上传；分块上传只在块边界响应中断，先禁用按钮防重入"""
        win = self._win
        worker = getattr(win, "_newlog_upload_worker", None)
        if worker is None or not worker.isRunning():
            return
        box = MessageBox("取消上传", "上传进行中，确定取消？", self)
        box.yesButton.setText("确定取消")
        box.cancelButton.setText("继续上传")
        if not box.exec():
            return
        if not worker.isRunning():
            return
        self.append_line("… 正在取消上传...")
        # 先禁用再请求中断：分块上传要等当前块传完才响应，这段时间再点一次只会重复弹确认框
        self.btn_go.setEnabled(False)
        worker.requestInterruption()

    def _release_upload_worker(self):
        """摘掉 win 上的上传 worker 引用并回收线程，否则下一次上传会被旧引用挡住

        _release_worker_safe 只看线程状态，引用不清会让下一次上传或下一次整理
        被这个已经结束的对象挡住。
        """
        win = self._win
        w = getattr(win, "_newlog_upload_worker", None)
        win._newlog_upload_worker = None
        if w is not None:
            win._release_worker_safe(w)

    def _on_upload_percent(self, p):
        """上传进度回调；进度条平时隐藏，收到第一块进度才显示，空闲时不占版面。"""
        if self.progress_bar.isHidden():
            self.progress_bar.show()
        self.progress_bar.setValue(p)

    def _after_upload(self, line_msg):
        """成功/失败/取消三种结局共用的收尾：回收 worker → 复位按钮与进度条

        统一回到 organized 态，用户可以改署名重新整理，也可以直接再传一次。
        """
        self._release_upload_worker()
        self.append_line(line_msg)
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        self.btn_go.setEnabled(True)
        # 回 organized 而不是 idle：_out_path 还在原位，用户可以立刻重传，不必再整理一遍
        self._phase = "organized"
        self.target_edit.setEnabled(True)
        self.btn_go.setText("打包上传")

    def _on_upload_done(self, info):
        """上传成功：临时 zip 已按 remove_zip_after_done 删除，提示里写明免得用户去找"""
        self._after_upload(f"✔ 上传成功: {info}（本地临时 zip 已清理）")
        self._win._append_log(f"[整理] 打包上传成功: {info}")
        self._win._show_info_bar(f"打包上传成功: {info}", "success", duration=5000)

    def _on_upload_fail(self, msg):
        """上传失败：与成功/取消共用收尾，只把结局文案换成失败与原因。"""
        self._after_upload(f"✘ 上传失败: {msg}")
        self._win._append_log(f"[整理] 打包上传失败: {msg}")
        self._win._show_info_bar(msg.split(chr(10))[0], "error", duration=4000)

    def _on_upload_cancelled(self):
        """用户取消上传：worker 在分块边界退出后回到这里，临时 zip 已按配置清理。"""
        self._after_upload("✘ 上传已取消（临时 zip 已清理）")
        self._win._append_log("[整理] 打包上传已取消（临时 zip 已清理）")
        self._win._show_info_bar("上传已取消，临时 zip 已清理", "info", duration=4000)

    def _on_open_out(self):
        """打开整理结果目录；目录被移动或删除时静默失败"""
        if self._out_path and os.path.isdir(self._out_path):
            try:
                os.startfile(self._out_path)
            except Exception:
                pass

    def append_line(self, text):
        """往运行输出追加一行；worker 的 line 信号直连这里"""
        self.log_view.append(text)


# ==================== ToolHub 容器 ====================

class ToolHub(PivotPage):
    """工具页：横排 Pivot 二级切换 + 五工作区堆叠（与运维/售后/跑视频同风格）"""

    def __init__(self, parent=None):
        """建四个工作区并按导航顺序注册到 Pivot，默认停在单杆视频页

        工作区常驻不销毁，切页只做显隐，所以占用端口的页面能靠 hideEvent 收尾。
        """
        super().__init__(parent)
        self.setObjectName("toolHub")
        self._win = parent

        # 四个工作区一次建好：Pivot 切页只做显隐，不重建控件，切回来状态还在。
        self.single_video_work = SingleVideoWork(self._win, self)
        self.port_fake_work = PortFakeWork(self._win, self)
        self.upload_list_work = UploadListWork(self._win, self)
        self.newlog_work = NewLogWork(self._win, self)
        # 相机工具 2026-10-07 升级为面板级「相机」（windows/camera/，见 main_window）

        # 文案即 Pivot 标签，顺序与设计稿一致：单杆视频 → 端口 → 上传 → 整理，按链路走。
        self.addPage(self.single_video_work, "单杆视频")
        self.addPage(self.port_fake_work, "端口占用")
        self.addPage(self.upload_list_work, "上传清单")
        self.addPage(self.newlog_work, "视频与日志批量整理")
        # 锁宽：否则切页时 Pivot 条会随当前页文字长度抖动。
        self.lock_pivot_width()
        # 默认落在工作区 1，避免首次进入是一片空白。
        self.switchTo(self.single_video_work)
