# -*- coding: utf-8 -*-
"""SSH 终端（invoke_shell 交互式 PTY + ANSI 虚拟终端 + 直接键盘输入）

体验与 Windows Terminal / Xshell 一致：
- 直接在终端区域打字，shell 回显
- Tab 命令/路径补全
- 上下键命令历史
- Ctrl+C/D/L 等控制键
- ANSI 彩色输出正确渲染
- nano/vim 全屏应用（备用屏幕切换）
- 会话记录器：终端输出与用户命令同步落盘 logs/ssh_sessions/
- 断线重连：断开后顶部显示重连条，一键重建连接并开启新会话日志
- 常用命令条：命令列表存 settings.json，支持增删管理与可选自动执行

安全关闭策略（规避 C 层 Use-After-Free 崩溃）。

paramiko 的 Channel/Transport 是 C 层扩展对象，主线程 close() 与 reader 线程
正在执行的 recv() 并发时，Python 层的引用计数保护不了已释放的 C 结构，进程会
直接崩且没有异常栈。因此关闭顺序被固定成三步：channel 设 0.1s recv 超时，让
reader 能在有限时间内察觉 stop 标志；shutdown() 只置标志并 join reader，主线程
绝不碰 channel；reader 退出后才关 channel 与 transport，此时已无并发访问者。
三步顺序颠倒或跳步，都会把这个崩溃重新引回来。

架构：
- SSHTerminalPanel(QWidget)：可嵌入标签页的核心面板
- SSHTerminalWindow(QDialog)：独立窗口薄壳（向后兼容）
"""

import codecs
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import threading
from datetime import datetime

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QWidget, QFrame,
                               QLabel, QListWidget)
from PySide6.QtGui import QAction
from PySide6.QtCore import QTimer, Qt, Signal
from qfluentwidgets import (PushButton, PrimaryPushButton, DropDownPushButton,
                            TransparentToolButton, LineEdit, FluentIcon, MessageBox,
                            RoundMenu, BodyLabel)

from core.app_paths import get_app_dir
from core.conn_logger import conn_logger
from core.theme_qss import apply_window_qss
from core.utils import safe_close_transport, cleanup_log_dir, show_info_bar
from workers.network_workers import SSHConnectWorker
from windows.remote_session.ansi_terminal import ANSITerminalWidget
from windows.remote_session.forensic_report import ForensicWorker, get_forensic_dir

# 模块级强引用集合：QThread 的 Python 包装对象一旦被 GC 回收，底层线程却还在跑，
# Qt 会以 "QThread: Destroyed while thread is still running" 直接 abort 进程。
# 面板关闭时把仍在运行的 worker 挂到这里，线程 finished 后再自行摘除。
_pending_workers: set = set()


def _safe_release_worker(w):
    """把 worker 挂到模块级强引用集合，等线程自然结束后摘除并 deleteLater

    调用点都是「窗口即将销毁」或「任务刚启动」的时刻：此时面板对 worker 的
    引用随时可能随面板一起失效。没有这个集合兜底，垃圾回收可能在线程运行中
    就回收包装对象，导致进程 abort。finished 由 worker 自己的线程在退出后发出，
    lambda 里同时完成摘除与 deleteLater，不留悬挂对象。
    """
    _pending_workers.add(w)
    w.finished.connect(lambda: (_pending_workers.discard(w), w.deleteLater()))


# ─── 配置门面读写（常用命令条配置） ─────────────────────────────────

# 常用命令默认占位示例（配置门面无 ssh_commands 键时使用）
DEFAULT_SSH_COMMANDS = ["top", "df -h", "journalctl -n 50"]


def _load_settings() -> dict:
    """读取配置门面的合并视图；任何异常都退化成空字典

    调用点只关心「有没有配过」，拿不到配置就用默认值，因此这里不向上抛：
    配置域文件损坏或权限不足时，常用命令条应继续用默认项工作，而不是让整个
    终端面板初始化失败。
    """
    try:
        from core import app_settings
        return app_settings.get_merged()
    except Exception:
        return {}


def _save_settings(data: dict):
    """按键逐个合并写入配置门面，不做整表覆盖

    走 app_settings.set 而不是自己写 JSON 文件，是因为配置门面负责按 key 路由
    到不同域文件、并对敏感字段加密；整表写回会把其他域的内容一起冲掉。
    写失败静默忽略：常用命令条是便利功能，不该因磁盘只读而阻断会话。
    """
    try:
        from core import app_settings
        for k, v in data.items():
            app_settings.set(k, v)
    except Exception:
        pass


# ─── 会话日志工具 ─────────────────────────────────────────────────────────

def get_session_log_dir() -> str:
    """返回 SSH 会话日志目录 {app_dir}/logs/ssh_sessions

    刻意与 conn_logger 的 logs/ 分开放：连接日志是排障用的短文本，会话日志是
    整段终端录像，量级差两个数量级，混在一层会让清理策略互相误伤。
    每次现算而不缓存，是因为 app_dir 在便携版与安装版之间可能变化。
    """
    return os.path.join(get_app_dir(), "logs", "ssh_sessions")


# ANSI 转义序列剥离正则：会话日志只留纯文本，否则内容搜不到、编辑器里也只是
# 一串 ^[ 控制字符。
# 四条分支缺一不可。CSI 的终止字节是 0x40-0x7E 而不是字母，只匹配 [A-Za-z]
# 会漏掉 \E[1P、\E[@、\E[2~ 这类序列；字符集设计符 \E(0 / \E(B 是三字节，
# 必须单独匹配，否则 \E(B 只会剩下一个落单的 "B" 混进日志——nano 界面里那行
# 莫名其妙的 B 就是这么来的。
_ANSI_RE = re.compile(
    r'\x1b\[[0-9;?<>=]*[ -/]*[@-~]'          # CSI 序列（参数 + 中间字节 + 终止字节）
    r'|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?'  # OSC 序列
    r'|\x1b[()*+\-./][0-9A-Za-z]'           # 字符集设计符（\E(0 \E(B \E)0 ...）
    r'|\x1b[ -~]'                            # 其他两字节转义（\E= \E> \E7 ...）
)


def _strip_ansi(text: str) -> str:
    """剥离 ANSI 转义序列，返回可直接落盘与检索的纯文本

    只剥离、不做换行归一化：调用方更清楚当前是全屏应用还是普通 shell，
    两者的 \r 处理方式正好相反（见 _write_session）。
    """
    return _ANSI_RE.sub('', text)


# 取证按钮提示文案只留一份：初始化与取证结束后恢复按钮都从这里取，
# 两处各写一段长文案，迟早会漂移成一个说 A、一个说 B。
_FORENSIC_BTN_TIP = (
    "后台运行预置诊断命令组（含 dmesg/journalctl/syslog 系统错误日志）"
    "并汇总会话/连接日志、设备状态，调用 AI 大模型分析生成故障取证报告"
    "（厂商可在设置面板「AI 分析」页配置，仅连接建立后可用；运行中点击可取消）"
)


class SSHTerminalPanel(QWidget):
    """SSH 终端面板：可嵌入标签页容器，也可被独立窗口包装使用

    核心逻辑是 invoke_shell 拿到交互式 PTY，输出交给 ANSI 终端控件渲染，
    键盘事件原样回写 channel，等价于本地终端。面板不做 PTY 模拟，全屏应用
    由远端按 PTY 尺寸排版，所以尺寸同步是正确性前提而不是装饰。
    所有资源只在 shutdown() 一处释放，容器关标签与独立窗口 closeEvent 都必须
    走它，绕过就会留下 reader 线程与半开的 transport。
    """

    # reader 线程通过此信号将输出安全投递到 GUI 线程
    _output_signal = Signal(str)
    # reader 线程退出或异常 → GUI 线程切断线态的唯一事件源。不能再按显示文本
    # 匹配 '[连接已断开]' 判断：远端 cat 日志或 echo 打出同样一行就会被误判成
    # 断线，用户点重连反而把好端端的会话杀掉。
    _link_lost_signal = Signal()
    # P1-2：把当前远端工作目录交给上层开 SFTP 标签（与 SFTP 的
    # open_terminal_here 方向相反；目录取自 shell 上报的 OSC 7）
    open_sftp_here = Signal(str)

    def __init__(self, host, port, username, password, log_callback=None, parent=None,
                 server_name='', start_dir=''):
        super().__init__(parent)
        # 连接身份整段留在面板上：断线重连要用同一套参数重建连接，
        # 会话日志文件名与状态灯提示也都从这里取。
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._server_name = server_name
        # P1-1：起始目录（SFTP「终端」按钮跳转用；连接建立后注入 cd 命令）
        self._start_dir = str(start_dir or '').strip()
        # P1-2：最近一次上报的远端工作目录（OSC 7）；连接前先显示起始目录
        self._last_cwd = self._start_dir
        # 日志回调允许为空：面板被独立使用时没人接收日志，
        # 空函数兜底比每个调用点都判一次 None 干净。
        self._log = log_callback or (lambda msg: None)
        self._client = None
        self._channel = None
        self._connect_worker = None
        self._reader_thread = None
        # stop_event 是 reader 线程与关闭路径之间唯一的同步手段。不用布尔标志
        # 是因为 reader 无数据时要阻塞等待，Event.wait 能同时做到「等一小会儿」
        # 与「立刻响应停止」。
        self._stop_event = threading.Event()
        self._closing = False
        # 会话日志（会话记录器）：只由 GUI 线程读写。reader 线程只投递信号，
        # 并发被挡在信号边界外，因此这里不需要锁——少一把锁就少一类死锁。
        self._session_file = None
        self._session_path = None
        # 用户输入命令行重组缓冲（写入会话日志带 '> ' 前缀）
        self._cmd_buf = []
        # 转义序列消费状态：0=正常 1=刚见 ESC 2=CSI/OSC 内部 3=三字节序列收尾
        self._cmd_esc = 0
        # PTY 尺寸同步：拖动窗口会连续触发网格变化，每次都调 resize_pty 会让
        # 远端收到一串 SIGWINCH 并反复整屏重绘（表现为拖动时界面抽搐）。
        # 用单次定时器合并成一次下发，并记住已同步尺寸用于去重。
        self._pty_timer = QTimer(self)
        self._pty_timer.setSingleShot(True)
        self._pty_timer.setInterval(120)
        self._pty_timer.timeout.connect(self._apply_pty_size)
        self._pending_pty_size = None
        self._pty_size_sent = None
        # 断线重连状态
        self._disconnected = False
        self._reconnecting = False
        self._auto_run = False
        # 一键取证（D2）后台 worker 引用
        self._forensic_worker = None
        self._init_ui()
        self._refresh_cwd_label()
        self._output_signal.connect(self._on_shell_output)
        self._link_lost_signal.connect(self._on_link_lost)  # P2-4
        QTimer.singleShot(100, self._connect_ssh)

    def focusNextPrevChild(self, next_: bool) -> bool:
        """吃掉 Qt 的 Tab 焦点导航，保证 Tab 只作为终端输入

        QWidget 默认会用 Tab / Shift+Tab 在子控件间移动焦点。不覆盖这个方法，
        用户在终端里按 Tab 想补全命令时，焦点会跳到下方按钮上，补全永远发不
        出去，看起来像是光标消失了。
        """
        return False

    @property
    def tab_title(self) -> str:
        """返回标签页标题，始终带 host:port

        只写服务器名不够用：对同一台机器连开多个标签（不同用户、不同目录）时
        标签无法区分，关错标签的概率很高。服务器名留作前缀便于识别。
        """
        if getattr(self, '_server_name', ''):
            return f"SSH - {self._server_name}（{self._host}:{self._port}）"
        return f"SSH - {self._host}:{self._port}"

    def _target_desc(self) -> str:
        """日志/确认弹窗用的目标描述（server_name 优先）"""
        if getattr(self, '_server_name', ''):
            return self._server_name
        return f"{self._host}:{self._port}"

    def busy_state(self):
        """返回 (busy, 描述)，供容器在关标签或断隧道前确认

        只有取证任务算 busy：它跑在后台线程里，随手关掉面板会让报告中途消失。
        交互式 shell 不算 busy——关标签就等于关会话，那本来就是用户的意图，
        没有会丢失的后台工作。返回二元组而非布尔值，是为了让调用方把描述直接
        放进确认弹窗，不必每个调用点再各写一份文案。
        """
        w = self._forensic_worker
        if w is not None and w.isRunning():
            return True, '取证任务运行中'
        return False, ''

    @property
    def is_connected(self) -> bool:
        """只读连通探针：连接中与已断开都返回 False

        会话恢复流程用它做延迟体检，这里只反映本地已知状态、不主动发包，
        否则每次调用都要等一个网络往返。closing 也算 False：面板正在拆时，
        上层不该再往这个连接上派活。
        """
        return bool(not self._disconnected and not self._closing
                    and self._client is not None)

    @property
    def remote_path(self) -> str:
        """当前远端工作目录，按「实时上报 → 最近一次上报 → 起始目录」回退

        shell 通过 OSC 7 上报 cwd，没配 PROMPT_COMMAND 的远端永远不上报，所以
        回退链必须保留，否则恢复标签时会从 $HOME 开始，而不是用户上次待的目录。
        会话存档（remote_mixin._extract_session_info）正是靠这个值记住「上次在
        哪个目录」，恢复时再作为 start_dir 注入 cd。
        """
        live = ''
        try:
            live = self._terminal.cwd
        except RuntimeError:          # 控件已销毁（标签已关）
            live = ''
        return live or self._last_cwd or self._start_dir

    # ─── 远端工作目录（P1-2，OSC 7） ──────────────────────────────────────

    def _on_cwd_changed(self, path):
        """shell 上报了新的工作目录 → 记下来并刷新状态条 chip

        只缓存到 _last_cwd 而不直接转交上层：控件被销毁后仍然要回答「上次在哪」，
        而 remote_path 的读取可能发生在标签已关、终端控件已 delete 之后。
        """
        self._last_cwd = str(path or '')
        self._refresh_cwd_label()

    def _display_cwd(self) -> str:
        """把 home 目录缩写成 ~，省状态条横向空间

        home 由用户名推断而不是去远端查 $HOME：这里跑在 GUI 线程，任何一次远端
        查询都会卡住界面。root 与普通用户两套规则覆盖绝大多数场景，推断错了
        只会让缩写失效，不影响真实路径。
        """
        cwd = self.remote_path
        if not cwd:
            return ''
        user = str(getattr(self, '_username', '') or '')
        home = '/root' if user == 'root' else (f'/home/{user}' if user else '')
        if home and (cwd == home or cwd.startswith(home + '/')):
            return '~' + cwd[len(home):]
        return cwd

    def _refresh_cwd_label(self):
        """刷新 cwd chip 的文案、提示与按钮可用性

        提示里写了一段 PROMPT_COMMAND 配置示例：远端不上报 OSC 7 时用户只会
        看到一格空白，不给自助修复的方法就只能来问「为什么这里是空的」。
        控件可能已随标签销毁，所以整段包在 try 里静默返回。
        """
        cwd = self.remote_path
        try:
            self._lbl_cwd.setText(self._display_cwd())
            if cwd:
                self._lbl_cwd.setToolTip(f'远端工作目录：{cwd}（点右侧文件夹在 SFTP 中打开）')
            else:
                self._lbl_cwd.setToolTip(
                    '远端工作目录未知：远端 shell 未上报 OSC 7\n'
                    'bash 可在 ~/.bashrc 里加：'
                    'PROMPT_COMMAND=\'printf "\\033]7;file://%s%s\\033\\\\" '
                    '"$HOSTNAME" "$PWD"\'')
            self._btn_sftp_here.setEnabled(bool(cwd))
        except (AttributeError, RuntimeError):
            pass

    def _open_sftp_here(self):
        """把当前远端目录交给上层，由宿主开一个定位到该目录的 SFTP 标签

        面板不自己创建 SFTP 标签：标签管理属于容器职责，面板只发信号，
        这样独立窗口模式下缺少容器也不会出错。
        """
        cwd = self.remote_path
        if not cwd:
            return
        self._log(f"[SSH] 在 SFTP 中打开: {cwd}")
        self.open_sftp_here.emit(cwd)

    # ─── UI ───────────────────────────────────────────────────────────────

    def _init_ui(self):
        """搭出「断线条 + 终端 + 单行工具条」的三段式布局

        工具条刻意只用一行：终端面板的地盘要给终端，按钮每多占一行就少一行
        可见输出。所有按钮一律 NoFocus，否则点完按钮焦点留在按钮上，用户接着
        敲键盘会打不进终端，得先拿鼠标点回终端区域才能继续。
        """
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # 断线通知条：默认隐藏，只在真正断开或连接失败时出现
        # 常驻显示会让「连接中」和「已断开」看起来一样，用户分不清该不该等。
        self._reconnect_bar = QFrame(self)
        self._reconnect_bar.setStyleSheet(
            "QFrame { background-color: rgba(255, 152, 0, 0.15);"
            " border: 1px solid #ff9800; border-radius: 4px; }"
        )
        bar_layout = QHBoxLayout(self._reconnect_bar)
        bar_layout.setContentsMargins(8, 2, 8, 2)
        bar_layout.setSpacing(6)
        self._reconnect_label = QLabel("连接已断开")
        bar_layout.addWidget(self._reconnect_label)
        bar_layout.addStretch()
        self._reconnect_btn = PrimaryPushButton("重新连接")
        self._reconnect_btn.setFocusPolicy(Qt.NoFocus)
        self._reconnect_btn.setFixedWidth(96)
        self._reconnect_btn.clicked.connect(self._reconnect_clicked)
        bar_layout.addWidget(self._reconnect_btn)
        self._reconnect_bar.hide()
        layout.addWidget(self._reconnect_bar)

        # ANSI 终端（既是显示区域也是输入区域）
        self._terminal = ANSITerminalWidget(self)
        self._terminal.key_input.connect(self._on_key_input)
        # 网格尺寸变化 → 同步远端 PTY（全屏应用按 PTY 尺寸排版，尺寸不对就会错位）
        self._terminal.grid_resized.connect(self._on_grid_resized)
        # P1-2：shell 上报 OSC 7（工作目录）→ 更新状态条 chip
        self._terminal.cwd_changed.connect(self._on_cwd_changed)
        layout.addWidget(self._terminal, stretch=1)

        # 底部工具条：从上到下依次是命令、目录、取证、外开工具与状态灯
        # 顺序按使用频率排，最左边是最高频的常用命令。
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)

        # 常用命令条：下拉按钮 + 管理入口
        self._cmd_menu_btn = DropDownPushButton("常用命令")
        self._cmd_menu_btn.setFocusPolicy(Qt.NoFocus)
        btn_layout.addWidget(self._cmd_menu_btn)

        self._manage_cmd_btn = TransparentToolButton(FluentIcon.EDIT, self)
        self._manage_cmd_btn.setToolTip("管理常用命令（增删，保存到 settings.json）")
        self._manage_cmd_btn.setFocusPolicy(Qt.NoFocus)
        self._manage_cmd_btn.clicked.connect(self._manage_commands)
        btn_layout.addWidget(self._manage_cmd_btn)

        btn_layout.addStretch()

        # P1-2：远端工作目录 chip + 一键在同目录开 SFTP（目录由 shell 上报 OSC 7；
        # 远端没配 PROMPT_COMMAND 时 chip 为空、按钮置灰，终端行为不受影响）
        self._lbl_cwd = BodyLabel('')
        self._lbl_cwd.setStyleSheet('color: gray;')
        self._lbl_cwd.setToolTip('远端工作目录（shell 通过 OSC 7 上报）')
        btn_layout.addWidget(self._lbl_cwd)

        self._btn_sftp_here = TransparentToolButton(FluentIcon.FOLDER, self)
        self._btn_sftp_here.setFocusPolicy(Qt.NoFocus)
        self._btn_sftp_here.setToolTip('在 SFTP 中打开此目录')
        self._btn_sftp_here.setEnabled(False)
        self._btn_sftp_here.clicked.connect(self._open_sftp_here)
        btn_layout.addWidget(self._btn_sftp_here)

        self._forensic_btn = PushButton("一键取证")
        self._forensic_btn.setFocusPolicy(Qt.NoFocus)
        self._forensic_btn.setToolTip(_FORENSIC_BTN_TIP)
        self._forensic_btn.setEnabled(False)
        self._forensic_btn.clicked.connect(self._start_forensic)
        btn_layout.addWidget(self._forensic_btn)

        self._session_dir_btn = PushButton("会话记录")
        self._session_dir_btn.setFocusPolicy(Qt.NoFocus)
        self._session_dir_btn.setToolTip("在资源管理器中打开会话日志目录")
        self._session_dir_btn.clicked.connect(self._open_session_dir)
        btn_layout.addWidget(self._session_dir_btn)

        # P2-3：取证报告目录常驻入口（此前只有 InfoBar 上的瞬时动作可打开，
        # 6s 后报告路径无处可寻，只能手动翻 logs/forensic）
        self._forensic_dir_btn = PushButton("取证记录")
        self._forensic_dir_btn.setFocusPolicy(Qt.NoFocus)
        self._forensic_dir_btn.setToolTip("在资源管理器中打开取证报告目录（logs/forensic）")
        self._forensic_dir_btn.clicked.connect(self._open_forensic_dir)
        btn_layout.addWidget(self._forensic_dir_btn)

        # 外开按钮走后门而不是内嵌终端：某些交互（大文件编辑、需要本地打印）
        # 在自家 ANSI 控件里体验差，交给用户惯用的本地终端更省事。
        self._cmd_btn = PushButton("CMD 打开")
        self._cmd_btn.setFocusPolicy(Qt.NoFocus)  # 不抢焦点
        self._cmd_btn.clicked.connect(self._open_in_cmd)
        btn_layout.addWidget(self._cmd_btn)

        self._xshell_btn = PushButton("Xshell 打开")
        self._xshell_btn.setFocusPolicy(Qt.NoFocus)  # 不抢焦点
        self._xshell_btn.clicked.connect(self._open_in_xshell)
        btn_layout.addWidget(self._xshell_btn)

        # P1-7：常驻连接状态灯（读现有状态，不新增探测通道；与 SFTP 面板
        # 同色语义/同周期：绿=已连接、红=已断开、灰=连接中）
        self._lbl_health = BodyLabel('●')
        self._lbl_health.setStyleSheet('color: gray; font-size: 14px;')
        self._lbl_health.setToolTip('连接状态: 未连接')
        btn_layout.addWidget(self._lbl_health)
        # 状态灯轮询：5 秒一次只读内存状态，不发探测包。
        # 频率再高没有意义（灯的颜色不会比人眼反应更快），再低会让断线后
        # 灯色滞后于终端里已经出现的重连条。
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(5000)
        self._health_timer.timeout.connect(self._update_conn_health)
        self._health_timer.start()

        layout.addLayout(btn_layout)

        # 初始化常用命令下拉菜单（从 settings.json 读取）
        self._rebuild_cmd_menu()

    # ─── SSH 连接 ─────────────────────────────────────────────────────────

    def _connect_ssh(self):
        """用 worker 线程异步建连，GUI 线程只等信号

        握手要几百毫秒到数秒，直接在 GUI 线程里调 paramiko 会让整个窗口冻住，
        Windows 还会给标题栏挂上「未响应」。失败由 _on_connect_error 统一处理，
        所以这里不做 try/except——worker 已经把异常转成 error 信号了。
        """
        self._terminal.write_output(f"正在连接 {self._host}:{self._port} ...\r\n")
        worker = SSHConnectWorker(self._host, self._port, self._username, self._password)
        worker.connected.connect(self._on_connected)
        worker.error.connect(self._on_connect_error)
        self._connect_worker = worker
        worker.start()

    def _on_connected(self, client):
        """SSH 连接成功 → 创建交互式 shell"""
        self._client = client
        self._cleanup_connect_worker()
        try:
            # 尺寸权威：开 PTY 之前先把网格对齐到当前视口，再把这个尺寸开给 PTY。
            # 远端 ncurses 全屏应用（nano/vim/less）完全按 PTY 尺寸排版，
            # 写死 120x40 会与控件可视区不符（隐藏页签/恢复会话时会立刻看出错位）。
            self._terminal.sync_grid(force=True)
            cols, rows = self._terminal.grid_size()
            channel = client.invoke_shell(term='xterm-256color', width=cols, height=rows)
            self._pty_size_sent = (cols, rows)
            # 0.1s 超时是关闭流程的前提：reader 每轮 recv 最多阻塞 100ms 就能
            # 回来检查 stop_event，shutdown 才可能在亚秒级完成。设成 None 时
            # recv 会一直挂着，关闭只能靠强杀线程，那正是 C 层崩溃的来源。
            channel.settimeout(0.1)
            self._channel = channel
            # 启动 reader 守护线程
            self._stop_event.clear()
            self._reader_thread = threading.Thread(
                target=self._reader_loop, daemon=True, name='ssh-shell-reader'
            )
            self._reader_thread.start()
            # 输入必须先启用再聚焦：控件在输入关闭时会吞掉按键，
            # 只聚焦不启用的话用户看到光标却打不出字。
            self._terminal.set_input_enabled(True)
            self._terminal.setFocus()
            # 连接成功：隐藏重连条，开启新会话日志文件
            self._disconnected = False
            self._reconnecting = False
            self._cmd_buf.clear()
            self._cmd_esc = 0
            self._reconnect_btn.setEnabled(True)
            self._reconnect_btn.setText("重新连接")
            self._reconnect_bar.hide()
            # 取证按钮在这里统一恢复：它同时被「首次连上」与「重连成功」复用，
            # 重连路径不再单独写一遍，避免文案与可用状态两处不同步。
            self._forensic_btn.setText("一键取证")
            self._forensic_btn.setEnabled(True)
            self._open_session_log()
            # P0-3：连接成功进主窗口日志（此前 SSH 连接事件零日志）
            self._log(f"[SSH] 已连接: {self._target_desc()}")
            self._set_health('green', '已连接')
            # P1-1：起始目录注入——shell 建立后发送 cd（PTY 输入缓冲排队，安全；
            # 经 _feed_session_input 让命令进会话日志，与手打一致）
            if self._start_dir:
                cmd = f"cd {shlex.quote(self._start_dir)}\r"
                try:
                    self._feed_session_input(cmd)
                    channel.send(cmd)
                except Exception:
                    pass
        except Exception as e:
            # 连接成功但拿不到 shell（常见于被 sshd 的 ForceCommand、
            # /etc/nologin 或已满的 MaxSessions 挡住）。仍按断线处理并给出
            # 重连入口，否则用户会停在一个只显示报错的死面板上。
            self._terminal.write_output(f"[错误] 创建交互式 Shell 失败: {e}\r\n")
            conn_logger.exception('SSH', '创建交互式 Shell 失败', exc=e,
                                  host=self._host, port=self._port)
            # Shell 创建失败也进入断线态，提供重连入口
            self._reconnecting = False
            self._disconnected = False
            self._on_link_lost()
            self._reconnect_label.setText("连接异常")

    def _on_connect_error(self, error):
        """SSH 连接失败 → 显示重连条（含初次连接与重连失败）"""
        self._cleanup_connect_worker()
        self._terminal.write_output(f"[连接失败] {error}\r\n")
        # P0-3：连接失败进主窗口日志
        self._log(f"[SSH] 连接失败: {self._target_desc()} - {error}")
        self._reconnecting = False
        self._disconnected = True
        self._forensic_btn.setEnabled(False)
        self._reconnect_label.setText("连接失败")
        self._reconnect_btn.setEnabled(True)
        self._reconnect_btn.setText("重新连接")
        self._reconnect_bar.show()
        self._set_health('red', '连接失败')

    def _cleanup_connect_worker(self):
        """摘掉连接 worker 的非阻塞清理：先 abort，再按运行状态二选一

        线程已退出才能 deleteLater 并立刻释放；仍在运行就必须交给
        _safe_release_worker 挂强引用，不然包装对象先被回收、线程后退出，
        进程会以 abort 收场。先 abort 是为了让正在重试的握手尽快放弃。
        """
        if self._connect_worker is not None:
            w = self._connect_worker
            self._connect_worker = None
            if hasattr(w, 'abort'):
                w.abort()
            if w.isRunning():
                _safe_release_worker(w)
            else:
                w.deleteLater()

    # ─── 交互式 Shell I/O ─────────────────────────────────────────────────

    def _reader_loop(self):
        """后台线程：持续读取 shell 输出并通过信号投递到 GUI 线程

        线程安全全部落在这里：channel 的每次 recv 都只在这个线程里发生，
        主线程（含 shutdown）一律不碰 channel。paramiko 的 channel 不能承受
        「一边 recv 一边 close」，那种写法会让进程直接崩掉且不留栈。
        recv 之所以不会无限阻塞，是因为连接建立时就设了 0.1s 超时。
        """
        channel = self._channel
        # 增量解码：一个中文/emoji 字符可能被 recv 切成两块，
        # 逐块 decode 会把它们变成 U+FFFD（nano 中文界面尤其明显）
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        while not self._stop_event.is_set():
            try:
                if channel.recv_ready():
                    data = channel.recv(65536)
                    # 空包 = 对端已关（sshd 收到 exit 或网络 RST 后 recv 返回 b''）。
                    # 这不是异常，不能当 socket 错误处理，否则日志里会刷假告警。
                    if not data:
                        if not self._closing:
                            self._output_signal.emit("\r\n[连接已断开]\r\n")
                            self._link_lost_signal.emit()  # P2-4：事件驱动断线态
                        break
                    text = decoder.decode(data)
                    if text:
                        self._output_signal.emit(text)
                else:
                    # 无数据可读时用 Event.wait 而不是 time.sleep：两者的区别
                    # 在于关闭时前者能立刻醒来，后者要白等满 50ms 才检查标志，
                    # 连续几百轮就是肉眼可见的关标签卡顿。
                    self._stop_event.wait(0.05)
            except socket.timeout:
                continue
            except OSError:
                # 主动关闭时 paramiko 常抛 EINVAL/EBADF，这类异常不是故障，
                # 面板正在拆就不要再报「连接已断开」，否则用户会看到自己
                # 刚关掉的标签又弹出一条断线提示。
                if not self._closing and not self._stop_event.is_set():
                    self._output_signal.emit("\r\n[连接已断开]\r\n")
                    self._link_lost_signal.emit()  # P2-4
                break
            except Exception:
                if not self._stop_event.is_set():
                    self._output_signal.emit("\r\n[连接异常断开]\r\n")
                    self._link_lost_signal.emit()  # P2-4
                break

    def _on_shell_output(self, text: str):
        """GUI 线程槽：输出同时进终端控件与会话日志

        会话文件只在 GUI 线程写（reader 只投递信号），因此不需要锁；加锁反而
        会把一次 flush 变成潜在的 GUI 卡顿点。断线判定一律交给
        _link_lost_signal：按显示文本匹配 '[连接已断开]' 会被远端 cat 日志或
        echo 里的同样一行骗到，误把好会话判死。
        """
        if self._closing:
            return
        self._terminal.write_output(text)
        self._write_session(text)

    def _on_link_lost(self):
        """连接断开后的单点收口：关日志、停输入、亮重连条

        幂等设计：reader 线程的多个异常分支都会发 _link_lost_signal，
        重复进入时直接返回，避免重连条被反复 show、状态灯被来回刷。
        """
        if self._disconnected:
            return
        self._disconnected = True
        self._close_session_log("断开")
        self._terminal.set_input_enabled(False)
        self._forensic_btn.setEnabled(False)
        self._reconnect_label.setText("连接已断开")
        self._reconnect_btn.setEnabled(True)
        self._reconnect_btn.setText("重新连接")
        self._reconnect_bar.show()
        # P0-3：断开事件进主窗口日志（此前终端断线在主窗口侧完全不可见）
        self._log(f"[SSH] 连接已断开: {self._target_desc()}")
        self._set_health('red', '连接已断开')

    def _on_key_input(self, data: str):
        """键盘输入先喂给日志重组器，再原样转发给远端

        顺序不能反：先 send 的话命令已经在远端执行，万一本地重组抛异常，
        日志里就会永远缺这一行。channel 已关闭时静默丢弃，此时重连条已经
        在提示用户，不必再为每次按键报一次错。
        """
        self._feed_session_input(data)
        if self._channel is None or self._channel.closed:
            return
        try:
            self._channel.send(data)
        except Exception:
            pass

    def _on_grid_resized(self, cols: int, rows: int):
        """终端网格尺寸变化 → 通知远端 PTY 重排（SIGWINCH，带去抖）

        全屏应用（nano/vim/less/top）按 PTY 尺寸决定排版；不同步就会出现
        内容按旧尺寸画、控件按新尺寸显示导致的错位。
        """
        self._pending_pty_size = (cols, rows)
        if not self._pty_timer.isActive():
            self._pty_timer.start()

    def _apply_pty_size(self):
        """去抖到期后下发尺寸，与上次已同步值相同则跳过

        跳过是有意义的：拖拽结束时常会有一次「尺寸其实没变」的尾随回调，
        照发会让远端多做一次整屏重排。失败不重试也不告警——尺寸同步属于
        体验优化，下一次网格变化还会再试一遍。
        """
        size = self._pending_pty_size
        channel = self._channel
        if size is None or channel is None or channel.closed:
            return
        if size == self._pty_size_sent:
            return
        try:
            channel.resize_pty(width=size[0], height=size[1])
            self._pty_size_sent = size
        except Exception:
            pass  # 尺寸同步失败不影响会话，下次变化再试

    # ─── 会话记录器（A4） ─────────────────────────────────────────

    def _open_session_log(self):
        """为本次连接新建会话日志文件，重连即换新文件

        重连不追加到旧文件：一次连接的完整录像比多段混在一个文件里有用得多，
        按时间戳命名还能让用户在目录里直接按会话先后排序。
        文件名里的服务器名要过滤 Windows 非法字符，否则带冒号或斜杠的别名
        会直接让 open() 抛异常，会话日志静默失效。
        """
        self._close_session_log()
        try:
            session_dir = get_session_log_dir()
            os.makedirs(session_dir, exist_ok=True)
            self._cleanup_session_logs(session_dir)
            now = datetime.now()
            tag = self._server_name or self._host
            tag = re.sub(r'[\\/:*?"<>|\s]+', '_', tag).strip('_') or 'ssh'
            path = os.path.join(session_dir, f"{now:%Y%m%d_%H%M%S}_{tag}.log")
            self._session_file = open(path, 'a', encoding='utf-8', errors='replace')
            self._session_path = path
            target = f"{self._username}@{self._host}:{self._port}"
            if self._server_name:
                target += f" ({self._server_name})"
            self._write_session(
                "================ SSH 会话开始 ================\n"
                f"时间 : {now:%Y-%m-%d %H:%M:%S}\n"
                f"目标 : {target}\n\n"
            )
        except Exception as e:
            # 建文件失败（磁盘满/目录只读）不中断会话：终端本身还能正常用，
            # 只是没有录像。清空两个引用让后续写入走空值短路。
            self._session_file = None
            self._session_path = None
            conn_logger.exception('SSH', '创建会话日志文件失败', exc=e,
                                  host=self._host, port=self._port)

    @staticmethod
    def _cleanup_session_logs(session_dir: str):
        """按保留期与份数上限清理历史会话日志

        清理时机放在「新建之前」而不是关机时：会话日志目录在用户机器上，
        程序可能被直接 kill，只有进入时才清理才保证一定会执行到。
        默认 30 天 / 500 份，两个上限分别防时间累积与高频重连的短时堆积；
        清理失败直接放过，绝不能让日志清理挡住用户连服务器。
        """
        try:
            settings = _load_settings()
            max_age = int(settings.get("ssh_session_log_retention_days", 30))
            max_files = int(settings.get("ssh_session_log_max_files", 500))
            removed = cleanup_log_dir(session_dir, max_files=max_files,
                                      max_age_days=max_age, suffix='.log')
            if removed:
                conn_logger.info('SSH', f'会话日志闭环清理: 删除 {removed} 个历史文件')
        except Exception:
            pass  # 清理失败不影响会话

    def _write_session(self, text: str):
        """剥离 ANSI 后追加写会话日志，失败静默降级

        写失败（磁盘满、文件被占用）不冒泡：日志是旁路能力，为了记日志
        把整个终端搞崩是本末倒置。每块都 flush 是因为崩溃或断电后，
        缓冲区里没落盘的正好是断线前那段最关键的输出。
        """
        if self._session_file is None:
            return
        try:
            stripped = _strip_ansi(text)
            # 全屏应用不靠 \n 换行，只用裸 \r 加光标定位，不归一化就是一整行；
            # 普通 shell 反过来要保留 \r（进度条靠它原地刷新），转成换行会刷屏。
            if self._terminal.alt_screen:
                # 全屏应用（nano/vim/top）不靠 \n 换行，只有裸 \r + 定位序列；
                # 不归一化的话整屏日志会挤成一行。普通 shell 保留 \r
                # （进度条靠它原地刷新，不能变成一堆换行）。
                stripped = stripped.replace('\r\n', '\n').replace('\r', '\n')
            self._session_file.write(stripped)
            self._session_file.flush()
        except Exception:
            pass

    def _close_session_log(self, reason: str = "结束"):
        """写结束标记并关闭会话日志，可重复调用

        结束标记让用户一眼看出这次会话是正常结束还是断线（reason 会写进去），
        没有它只能靠最后一行输出猜。幂等是因为关闭流程里会有多处调用：
        断线、重连、面板销毁，任何一处晚到都不该抛异常。
        """
        if self._session_file is None:
            return
        try:
            self._session_file.write(
                f"\n================ 会话{reason} "
                f"{datetime.now():%Y-%m-%d %H:%M:%S} ================\n"
            )
            self._session_file.flush()
            self._session_file.close()
        except Exception:
            pass
        self._session_file = None
        self._session_path = None

    def _feed_session_input(self, data: str):
        """把原始键流重组成「用户最终确认的那一行」，回车时带 '> ' 前缀落盘

        直接记录按键会把退格、方向键和整屏刷新序列一起写进去，日志读起来
        是一团乱码；这里模拟行编辑，只保留最终文本。四个状态构成一个小型
        转义解析机：0 普通字符，1 刚吃到 ESC，2 CSI/OSC 内部，3 三字节序列收尾。
        处理退格/Ctrl+C/Ctrl+U 编辑行为，跳过转义序列字符（方向键/功能键不进日志），
        使日志中的命令行与用户最终确认的内容一致。
        终止判据按 VT 规范：CSI 的终止字节是 0x40-0x7E（不只是字母），
        否则 ``\\EOA``（应用模式方向键）会漏一个 'A' 到日志里。
        """
        buf = self._cmd_buf
        for ch in data:
            # 状态 1：ESC 之后的引导字节决定这个序列还有多长。
            # SS3 与字符集设计符都还要再吃一个字节，只按两字节处理的话
            # 收尾那次会把后续的正常字符一起吃掉。
            if self._cmd_esc == 1:                      # ESC 之后第一个字节
                if ch in '([)*+-./#%O':
                    self._cmd_esc = 3                   # 字符集设计符 / SS3：再吃一字节
                elif ch in '[]':
                    self._cmd_esc = 2                   # CSI / OSC：吃到终止字节
                else:
                    self._cmd_esc = 0                   # 两字节转义，到此为止
                continue
            if self._cmd_esc == 2:                      # CSI / OSC 内部
                if ch == '\x1b':
                    self._cmd_esc = 1
                elif ch == '\x07' or '\x40' <= ch <= '\x7e':
                    self._cmd_esc = 0
                continue
            if self._cmd_esc == 3:                      # 三字节序列收尾
                self._cmd_esc = 0
                continue
            if ch == '\x1b':
                self._cmd_esc = 1
            elif ch == '\r':
                # 只有非空行才落盘：空回车在终端里极高频（连按、误触），
                # 全记下来会把真正执行过的命令行淹掉。
                line = ''.join(buf).strip()
                if line:
                    self._write_session(f"> {line}\n")
                buf.clear()
            elif ch in ('\x7f', '\b'):
                if buf:
                    buf.pop()
            elif ch in ('\x03', '\x15'):  # Ctrl+C / Ctrl+U 清空当前行
                buf.clear()
            elif ch.isprintable():
                buf.append(ch)

    def _open_session_dir(self):
        """在资源管理器中打开会话日志目录"""
        path = get_session_log_dir()
        try:
            os.makedirs(path, exist_ok=True)
            subprocess.Popen(['explorer', path])
        except Exception as e:
            self._log(f"[SSH] 打开会话日志目录失败: {e}")

    # ─── 断线重连（B2） ─────────────────────────────────────────────

    def _reconnect_clicked(self):
        """重连入口：先关掉死连接，再复用首次连接的同一套流程

        顺序固定为「清理 → 建连」，反过来会让新旧 channel 短时共存，
        reader 线程读到的是旧对象。_reconnecting 做闸门，防止用户连点
        触发两条建连流水线抢同一个面板状态。
        """
        if self._reconnecting or self._closing:
            return
        self._reconnecting = True
        self._reconnect_btn.setEnabled(False)
        self._reconnect_btn.setText("重连中...")
        self._terminal.set_input_enabled(False)
        # P0-3：重连动作进主窗口日志
        self._log(f"[SSH] 正在重新连接: {self._target_desc()}")
        self._set_health('gray', '连接中...')
        self._cleanup_dead_connection()
        self._connect_ssh()

    # ─── 连接状态指示（P1-7） ─────────────────────────────────────────────

    def _update_conn_health(self):
        """定时读本地连接状态刷新指示灯，不发探测包

        只读 transport.is_active()，不加心跳：为了一颗灯去发 keepalive
        会在弱网下把本来能恢复的会话判死，代价远大于收益。
        is_active 抛异常时按「未连接」处理——transport 已失效时任何调用都
        可能炸，而灯的真实用途只是提示用户该不该点重连。
        """
        if self._closing:
            return
        if self._client is not None:
            try:
                t = self._client.get_transport()
                active = bool(t is not None and t.is_active())
            except Exception:
                active = False
        else:
            active = False
        if active:
            self._set_health('green', '已连接')
        elif self._disconnected:
            self._set_health('red', '连接已断开')
        else:
            self._set_health('gray', '连接中...')

    def _set_health(self, color, text):
        """设置状态灯颜色与悬停提示

        色值刻意与 SFTP 面板的健康灯取同一组，两处颜色语义必须一致：
        用户会并排看两个面板，一边绿一边红会直接误导判断。
        """
        color_map = {'green': '#4CAF50', 'orange': '#FF9800',
                     'red': '#F44336', 'gray': 'gray'}
        try:
            self._lbl_health.setStyleSheet(
                f'color: {color_map.get(color, "gray")}; font-size: 14px;')
            self._lbl_health.setToolTip(f'连接状态: {text}')
        except Exception:
            pass

    def _cleanup_dead_connection(self):
        """按「停线程 → 关 channel → 关 transport」的顺序拆掉死连接

        第一步必须是 join reader：paramiko 的 channel 不能一边被 recv 一边被
        close，那是 C 层 Use-After-Free 的直接触发条件。join 带 1s 上限，
        是因为 reader 正常情况下 100ms 内就能响应 stop；超时后仍继续往下拆，
        宁可冒一点风险也不能让重连按钮永久卡死。
        """
        self._stop_event.set()
        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)
        self._reader_thread = None
        if self._channel is not None:
            try:
                self._channel.close()
            except Exception:
                pass
            self._channel = None
        if self._client is not None:
            try:
                safe_close_transport(self._client.get_transport())
            except Exception:
                pass
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    # ─── 一键取证（D2） ───────────────────────────────────────────────────

    def _start_forensic(self):
        """启动后台取证；按钮兼作进度显示与取消开关

        同一个按钮承担三种语义（发起、进度、取消）是刻意的：取证只有几分钟，
        再弹一个带进度条和取消键的对话框反而更打断操作。
        前置守卫缺一不可：面板正在拆、已断线、channel 已关闭时启动，都会让
        后台线程拿着废连接去跑十条命令，最终只能超时收场。
        """
        if (self._closing or self._disconnected or self._client is None
                or self._channel is None or self._channel.closed):
            return
        w = self._forensic_worker
        if w is not None and w.isRunning():
            # P2-3：运行中点击 = 取消（当前命令执行完/超时后停止，不生成报告）
            w.cancel()
            self._forensic_btn.setEnabled(False)
            self._forensic_btn.setText("正在取消...")
            self._log(f"[SSH] 已请求取消取证: {self._target_desc()}")
            return
        # 运行中保持可点：禁用会让「取消」这个唯一入口消失，
        # 用户只能等它自己跑完。
        self._forensic_btn.setEnabled(True)
        self._forensic_btn.setText("取证中...")
        self._forensic_btn.setToolTip("点击取消取证")
        # P0-3：取证开始进主窗口日志
        self._log(f"[SSH] 取证开始: {self._target_desc()}")
        worker = ForensicWorker(
            self._client, self._host, self._port, self._username,
            server_name=self._server_name, session_log_path=self._session_path)
        worker.progress.connect(self._on_forensic_progress)
        worker.report_ready.connect(self._on_forensic_done)
        worker.failed.connect(self._on_forensic_failed)
        worker.cancelled.connect(self._on_forensic_cancelled)  # P2-3
        # 先挂强引用再 start：线程启动瞬间就可能结束，晚一步挂引用会撞上
        # 「线程已退出但对象还没被任何地方持有」的窗口。
        self._forensic_worker = worker
        _safe_release_worker(worker)
        worker.start()

    def _on_forensic_progress(self, idx: int, total: int, title: str):
        """取证进度：按钮文字显示 (当前/总数)，保持可点（点击=取消）"""
        self._forensic_btn.setText(f"取证中 {idx}/{total}")

    def _on_forensic_cancelled(self):
        """P2-3：取证被用户取消——恢复按钮（连接存活即可重新发起）"""
        self._restore_forensic_btn()
        self._log(f"[SSH] 取证已取消（未生成报告）: {self._target_desc()}")
        show_info_bar("取证已取消，未生成报告", "warning",
                      title="一键取证", parent=self, duration=4000)

    def _on_forensic_done(self, path: str):
        """取证完成：InfoBar 成功提示 + “打开报告文件”动作"""
        self._restore_forensic_btn()
        self._log(f"[SSH] 取证完成，报告: {path}")
        bar = show_info_bar(f"报告已生成：{os.path.basename(path)}", "success",
                            title="取证完成", parent=self, duration=6000)
        act = QAction("打开报告文件", bar)
        act.triggered.connect(lambda checked=False, p=path: self._reveal_forensic_report(p))
        bar.addAction(act)

    def _on_forensic_failed(self, msg: str):
        """取证失败：恢复按钮并提示错误首行"""
        self._restore_forensic_btn()
        first_line = str(msg or "未知错误").splitlines()[0] if msg else "未知错误"
        self._log(f"[SSH] 取证失败: {first_line}")
        show_info_bar(first_line, "error", title="取证失败", parent=self, duration=5000)

    def _restore_forensic_btn(self):
        """把取证按钮复位成「可再次发起」的状态

        可用性直接由连接状态现算，而不是记一个「上次是否成功」的标志：
        失败后按钮必须可点，用户再点一次就是重试，不需要额外的重试按钮。
        """
        self._forensic_btn.setText("一键取证")
        self._forensic_btn.setToolTip(_FORENSIC_BTN_TIP)
        connected = (self._client is not None and not self._disconnected
                     and not self._closing)
        self._forensic_btn.setEnabled(connected)

    def _open_forensic_dir(self):
        """P2-3：打开取证报告目录（InfoBar 6s 消失后的常驻回访入口）"""
        path = get_forensic_dir()
        try:
            os.makedirs(path, exist_ok=True)
            subprocess.Popen(['explorer', path])
        except Exception as e:
            self._log(f"[SSH] 打开取证报告目录失败: {e}")

    def _reveal_forensic_report(self, path: str):
        """资源管理器中定位并选中报告文件"""
        try:
            subprocess.Popen(['explorer', f'/select,{path}'])
        except Exception as e:
            self._log(f"[SSH] 打开报告文件失败: {e}")

    # ─── 常用命令条（B4） ─────────────────────────────────────────────

    def _rebuild_cmd_menu(self):
        """用配置里的命令列表重建下拉菜单，整棵菜单替换而非增量修改

        配置可能被用户手改成非列表，所以先校验类型再兜底到默认命令，
        否则 for 一个字符串会逐字符生成菜单项。
        lambda 用默认参数绑定当前值：循环变量是延迟求值的，不绑定的话
        所有菜单项点下去都会发送最后一条命令。
        """
        settings = _load_settings()
        commands = settings.get("ssh_commands")
        if not isinstance(commands, list) or not commands:
            commands = list(DEFAULT_SSH_COMMANDS)
        self._auto_run = bool(settings.get("ssh_cmd_auto_run", False))

        menu = RoundMenu("常用命令", self)
        for cmd in commands:
            act = QAction(str(cmd), menu)
            act.triggered.connect(lambda checked=False, c=str(cmd): self._send_command(c))
            menu.addAction(act)
        menu.addSeparator()
        self._auto_run_act = QAction("发送后自动执行（追加回车）", menu)
        self._auto_run_act.setCheckable(True)
        self._auto_run_act.setChecked(self._auto_run)
        self._auto_run_act.toggled.connect(self._toggle_auto_run)
        menu.addAction(self._auto_run_act)
        manage_act = QAction("管理命令...", menu)
        manage_act.triggered.connect(self._manage_commands)
        menu.addAction(manage_act)
        self._cmd_menu_btn.setMenu(menu)

    def _send_command(self, cmd: str):
        """把命令当作键盘输入注入，默认停在命令行上等用户确认

        走 _on_key_input 而不是 channel.send：这样命令会经过日志重组器，
        会话记录里能看到「用户发过什么」，与手打完全一致。
        默认不追加回车，是因为这些命令里有重启服务、删文件之类的高危操作，
        让用户先看一眼再敲回车。
        """
        if self._channel is None or self._channel.closed:
            self._terminal.write_output("[提示] 未连接，无法发送命令\r\n")
            return
        payload = cmd + ('\r' if self._auto_run else '')
        self._on_key_input(payload)
        self._terminal.setFocus()

    def _toggle_auto_run(self, checked: bool):
        """切换「发送后自动执行」开关并立刻落盘

        立即持久化而不是等窗口关闭：终端标签经常被直接关掉，等退出再保存
        等于永远保存不上，下次启动又回到默认值。
        """
        self._auto_run = checked
        _save_settings({"ssh_cmd_auto_run": checked})

    def _manage_commands(self):
        """弹出命令管理对话框，确定后写配置并重建菜单

        只有 Accepted 才写：取消时对话框里的临时增删必须整体丢弃，
        否则用户删错一条想用取消退出，配置却已经被改掉了。
        """
        settings = _load_settings()
        commands = settings.get("ssh_commands")
        if not isinstance(commands, list) or not commands:
            commands = list(DEFAULT_SSH_COMMANDS)
        dlg = SshCommandEditDialog(commands, self)
        if dlg.exec() == QDialog.Accepted:
            _save_settings({"ssh_commands": dlg.commands()})
            self._rebuild_cmd_menu()

    # ─── 外部客户端 ───────────────────────────────────────────────────────

    def _open_in_cmd(self):
        """用系统 OpenSSH 在独立 CMD 窗口打开同一连接

        CREATE_NEW_CONSOLE 不能省：不建新控制台的话，ssh 子进程会继承本进程
        的句柄却没有可交互的终端，连上去立刻退出。
        这里只拼命令行、密码交给 ssh 自己交互输入，绝不把密码写进命令行——
        命令行会留在 cmd 历史与 Windows 进程列表里，进程列表任何本地用户都能查。
        """
        if not shutil.which('ssh'):
            w = MessageBox(
                "未找到 SSH 客户端",
                "系统中未安装 OpenSSH 客户端。\n"
                "请在 Windows 设置 > 应用 > 可选功能 中安装 OpenSSH 客户端。",
                self
            )
            w.yesButton.setText("确定")
            w.cancelButton.hide()
            w.exec()
            return
        cmd = f'ssh -p {self._port} {self._username}@{self._host}'
        try:
            subprocess.Popen(['cmd', '/k', cmd], creationflags=subprocess.CREATE_NEW_CONSOLE)
        except Exception as e:
            conn_logger.exception('SSH', '启动 CMD 终端失败', exc=e,
                                  host=self._host, port=self._port)

    def _open_in_xshell(self):
        """用 Xshell 拉起同一连接，身份信息通过 ssh:// URL 传过去

        URL 里带明文密码是 Xshell 命令行协议本身的限制，无法避免；
        因此这条路径只在用户显式点击时执行，不做任何自动调用。
        which 要大小写各试一次：Windows 下 PATH 里的名字大小写不固定。
        """
        xshell_path = shutil.which('xshell') or shutil.which('Xshell')
        if not xshell_path:
            self._log("[SSH] 未找到 Xshell，请确认已安装并加入系统 PATH")
            return
        xshell_url = f'ssh://{self._username}:{self._password}@{self._host}:{self._port}'
        try:
            subprocess.Popen([xshell_path, '-url', xshell_url])
        except Exception as e:
            self._log(f"[SSH] 启动 Xshell 失败: {e}")

    # ─── 生命周期 ─────────────────────────────────────────────────────────

    def shutdown(self):
        """按固定顺序拆除会话：停轮询、停 reader、关 channel、关 transport

        顺序不能变。reader 线程还在 recv 时 close channel，就是在 C 层
        一边读一边释放同一块结构，进程会直接 abort 且不产生任何异常栈。
        容器关标签与独立窗口 closeEvent 都只能走这里；幂等是为了容错——
        面板可能在断线后已经被自动收口过一次。
        """
        if self._closing:
            return
        self._closing = True
        self._pty_timer.stop()
        # 定时器先停：关到一半还在跑回调，会往已销毁的控件上写样式表。
        if hasattr(self, '_health_timer'):
            self._health_timer.stop()
        self._pending_pty_size = None
        self._pty_size_sent = None
        self._terminal.set_input_enabled(False)
        # 1. 通知 reader 线程退出
        self._stop_event.set()
        # 2. 等 reader 退出。正常 0.5s 内就能结束（recv 超时 100ms 加循环检查），
        #    上限压到 0.8s：超时后同样会继续关 channel，缩短上限只是减少最坏
        #    情况下的界面阻塞，不改变语义——关个标签卡两秒是用户能明显感知的。
        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=0.8)
        self._reader_thread = None
        # 3. reader 已退出，安全关闭 channel（此时无并发访问）
        if self._channel:
            try:
                self._channel.close()
            except Exception:
                pass
            self._channel = None
        # 4. 取证 worker 只置空引用，不等它收尾。连接一断，它跑的命令会立刻
        #    失败退出，硬等两秒只会让关标签更卡；对象生命周期已由
        #    _pending_workers 强引用接管，线程退出后自行 deleteLater。
        self._forensic_worker = None
        # 5. 关闭 SSH client（close+join 等待 transport 后台线程退出）
        if self._client:
            try:
                transport = self._client.get_transport()
                safe_close_transport(transport)
            except Exception:
                pass
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        # 6. 清理 connect worker
        self._cleanup_connect_worker()
        # 7. 关闭会话日志（写入结束标记）
        self._close_session_log("关闭")


class SshCommandEditDialog(QDialog):
    """常用命令的新增/删除对话框，确认后由调用方写回配置

    对话框自己不改配置：把「编辑」和「落盘」分开，调用方才能在 Accepted
    分支里一次性写入并刷新菜单，避免边编辑边写配置产生中间态。
    """

    def __init__(self, commands, parent=None):
        super().__init__(parent)
        self.setWindowTitle("管理常用命令")
        self.resize(420, 360)
        self._commands = [str(c) for c in commands]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self._list = QListWidget(self)
        self._list.addItems(self._commands)
        layout.addWidget(self._list, stretch=1)

        add_row = QHBoxLayout()
        self._edit = LineEdit(self)
        self._edit.setPlaceholderText("输入新命令，如 systemctl status snooker")
        self._edit.returnPressed.connect(self._add)
        add_row.addWidget(self._edit, stretch=1)
        add_btn = PushButton("添加")
        add_btn.clicked.connect(self._add)
        add_row.addWidget(add_btn)
        layout.addLayout(add_row)

        op_row = QHBoxLayout()
        del_btn = PushButton("删除选中")
        del_btn.clicked.connect(self._remove)
        op_row.addWidget(del_btn)
        op_row.addStretch()
        save_btn = PrimaryPushButton("保存")
        save_btn.clicked.connect(self.accept)
        op_row.addWidget(save_btn)
        layout.addLayout(op_row)

    def _add(self):
        text = self._edit.text().strip()
        if not text:
            return
        self._commands.append(text)
        self._list.addItem(text)
        self._edit.clear()
        self._edit.setFocus()

    def _remove(self):
        # 列表与内部列表按行号同步维护，两边必须一起删；
        # 只 takeItem 不删数据的话，保存时被删的命令会原样回到菜单里。
        row = self._list.currentRow()
        if row >= 0:
            self._list.takeItem(row)
            del self._commands[row]

    def commands(self):
        """返回命令列表的副本，保持用户排定的顺序

        返回副本而不是内部列表：调用方随后会把它交给配置层，
        直接给引用就等于把内部状态暴露出去，之后任何改动都无法追踪。
        """
        return list(self._commands)


class SSHTerminalWindow(QDialog):
    """把终端面板包成独立窗口的兼容外壳

    保留它是为了老调用点不用改：面板本身已经能脱离容器工作，这层只负责
    窗口标题、尺寸与关闭时的资源收口。新代码应直接用面板加标签容器。
    """

    def __init__(self, host, port, username, password, log_callback=None, parent=None,
                 start_dir=''):
        super().__init__(parent)
        apply_window_qss(self)
        self.setWindowTitle(f"SSH 终端 - {host}:{port}")
        self.resize(900, 560)
        self._panel = SSHTerminalPanel(
            host, port, username, password,
            log_callback=log_callback, parent=self, start_dir=start_dir
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._panel)

    def focusNextPrevChild(self, next_: bool) -> bool:
        # 独立窗口里 Tab 同样必须留给终端；外壳不拦截的话，Tab 会把焦点
        # 送出终端控件，命令补全就从这一步开始失效。
        return False

    def closeEvent(self, event):
        # 先拆会话再放行关闭事件：反过来窗口已经没了，告警与确认框
        # 就会挂在一个不存在的父窗口上。
        self._panel.shutdown()
        super().closeEvent(event)
