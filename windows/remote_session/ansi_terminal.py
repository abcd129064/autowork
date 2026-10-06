# -*- coding: utf-8 -*-
"""轻量级 ANSI 虚拟终端控件（支持键盘直接输入）

屏幕语义（光标定位、滚动区域、擦除、插入/删除行列、备用屏幕、SGR 颜色、
``\\E(0``/``\\E(B`` 线框字符集、自动换行）全部由 ``core.vt_screen.VTScreen``
实现——那一层零 Qt 依赖、可离线单测；本文件只做四件事：

1. 键盘输入 → ``key_input`` 信号（复制/粘贴、IME、功能键映射）；
2. 把屏幕网格渲染成 QTextEdit 的 HTML（增量行缓存 + 50ms 合并，~20fps）；
3. 按控件实际尺寸算出 cols/rows，``grid_resized`` 通知上层同步远端 PTY；
4. 终端观感：固定深色底、等宽字体、光标方块。

历史教训（2026-10-04，用户截图）：旧实现把解析和渲染揉在一起，只认
``H``/``A``-``D``/``G``/``J``/``K`` 等少数序列，漏掉 ncurses 最常用的
``\\E[<n>d``（VPA）等行定位序列，nano 首屏 30 行正文塌成 1 行；``sgr0`` 里的
``\\E(B`` 还漏成字面 ``B``；光标方块渲染成字面 ``&nbsp;``。参见
docs/终端全屏应用渲染修复.md。
"""

from PySide6.QtWidgets import QTextEdit, QApplication, QMenu
from PySide6.QtGui import (QFont, QFontMetricsF, QKeyEvent, QAction,
                           QTextCursor, QTextDocument)
from PySide6.QtCore import Qt, Signal, QTimer

from core.vt_screen import (DEFAULT_ATTRS, DEFAULT_BG, DEFAULT_FG, VTScreen,
                            WIDE_SENTINEL)

# 终端底色：深浅主题都不跟随（终端观感是功能需求，非主题样式，勿删）
_BG = "#1e1e1e"
# 默认前景（VTScreen 里 "default" 是哨兵值，这里换回真实颜色）
_FG = "#e5e5e5"
_CURSOR_STYLE = f"background-color:#00ff00;color:{_BG};"
# 与 QFont("Consolas", 10) 配套；line-height 必须与 _metrics() 的行高换算一致
_PRE_STYLE = ("margin:0; font-family:Consolas,'Courier New',monospace; "
              "font-size:10pt; line-height:1.3;")
_LINE_HEIGHT = 1.3
_DEFAULT_COLS = 120
_DEFAULT_ROWS = 40
_MIN_COLS, _MIN_ROWS = 20, 4


def _escape(text: str) -> str:
    """HTML 转义 + 空格转 &nbsp;（先转义再换空格，避免把 &nbsp; 二次转义）"""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.replace(" ", "&nbsp;")


def _wrap_span(text: str, attrs) -> str:
    """把已转义文本包进带样式的 span（attrs 为 VTScreen 的不可变元组）"""
    if attrs is None or attrs == DEFAULT_ATTRS:
        return f'<span style="color:{_FG}">{text}</span>'
    fg, bg, bold, underline, reverse = attrs
    fg = _FG if fg == DEFAULT_FG else fg
    bg = _BG if bg == DEFAULT_BG else bg
    if reverse:  # 反显：前后景互换（nano 标题栏/快捷键栏靠它区分）
        fg, bg = bg, fg
    styles = [f'color:{fg}']
    if bg != _BG:
        styles.append(f'background-color:{bg}')
    if bold:
        styles.append('font-weight:bold')
    if underline:
        styles.append('text-decoration:underline')
    return f'<span style="{";".join(styles)}">{text}</span>'


class ANSITerminalWidget(QTextEdit):
    """支持 ANSI 序列解析 + 键盘直接输入的终端控件

    用户按键 → key_input 信号发射对应字节 → 远端 shell 回显 → write_output 渲染
    """

    # 用户键盘输入通过此信号发射（str 为要发送给 shell 的原始字节）
    key_input = Signal(str)
    # 网格尺寸变化 → 上层应同步远端 PTY（paramiko channel.resize_pty）
    grid_resized = Signal(int, int)
    # 远端工作目录变化（shell 上报 OSC 7）→ 状态条显示 / "在 SFTP 中打开此目录"
    cwd_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 10))
        self.setStyleSheet(
            f"QTextEdit {{ background-color: {_BG}; border: none; }}"
        )
        self.setLineWrapMode(QTextEdit.NoWrap)
        # 网格列数按控件宽度算出，理论上永不横向溢出；关掉横向滚动条更省心
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 允许键盘焦点
        self.setFocusPolicy(Qt.StrongFocus)
        # 屏幕模型（纯逻辑）
        self._screen = VTScreen(cols=_DEFAULT_COLS, rows=_DEFAULT_ROWS)
        # 输入使能（连接成功前禁止键盘输入）
        self._input_enabled = False
        # 是否已经真正显示过（未显示时视口尺寸不可信，网格保持默认值）
        self._ever_shown = False
        # 真实行高缓存（见 _measure_line_height：Qt 富文本的 line-height 语义
        # 与 QFontMetricsF.lineSpacing() 不一致，必须实测）
        self._line_px = None
        self._line_sig = None
        # 鼠标移动事件（终端上报拖动/悬停时需要）
        self.setMouseTracking(True)
        # 渲染合并定时器：将高频 write_output 合并为一次 _render（~20fps）
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(50)  # 50ms ≈ 20fps
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._render)
        # 增量渲染状态
        self._row_html_cache: list = []          # 每行 HTML 缓存（按渲染行号）
        self._rendered_revision = -1
        self._rendered_structure = -1
        self._rendered_cursor_row = None
        self._cached_html = ""
        # 上一次上报的远端工作目录（OSC 7）——变化时发 cwd_changed
        self._last_cwd = ""
        # 首帧按当前控件尺寸对齐网格
        self._sync_grid()

    # ─── 公开接口 ─────────────────────────────────────────────────────────

    @property
    def cwd(self):
        """远端 shell 最近上报的工作目录（OSC 7）；未上报过为空串。

        只消费不注入：远端不发 OSC 7 时这里始终是空串。
        """
        return self._screen.cwd

    def set_input_enabled(self, enabled: bool):
        """设置是否允许键盘输入（连接成功后启用）"""
        if self._input_enabled != enabled:
            self._input_enabled = enabled
            self._invalidate_render()

    def write_output(self, data: str):
        """写入终端数据（解析 ANSI 序列，延迟合并渲染）"""
        if not data:
            return
        self._screen.feed(data)
        self._schedule_render()

    def clear_terminal(self):
        """清屏并清空回滚"""
        self._screen.clear()
        self._invalidate_render()
        self._render()

    def grid_size(self):
        """当前网格 (cols, rows)——上层用它设置远端 PTY 尺寸"""
        return self._screen.cols, self._screen.rows

    def set_grid_size(self, cols: int, rows: int):
        """显式设置网格尺寸（面板/冒烟脚本可用；尺寸变化会发 grid_resized）"""
        cols = max(_MIN_COLS, int(cols))
        rows = max(_MIN_ROWS, int(rows))
        if (cols, rows) == (self._screen.cols, self._screen.rows):
            return
        self._screen.resize(cols, rows)
        self._invalidate_render()
        self.grid_resized.emit(cols, rows)
        self._schedule_render()

    def screen_text(self, strip: bool = True):
        """把当前屏幕（含回滚）导出为纯文本，调试/冒烟断言用"""
        return [self._screen.row_text(i, strip) for i in range(self._screen.total_rows())]

    @property
    def alt_screen(self):
        """是否处于备用屏幕（远端全屏应用正在画屏）"""
        return self._screen.alt_screen

    def sync_grid(self, force: bool = False):
        """按当前视口重算网格（连接前调用一次，保证 PTY 尺寸=控件网格）。

        ``force=True`` 时忽略"还没显示过"的判断（面板在 invoke_shell 之前显式对齐）。
        """
        cols, rows = self._grid_for_size()
        if force or self._ever_shown:
            self.set_grid_size(cols, rows)
        elif (cols, rows) != (self._screen.cols, self._screen.rows):
            # 没显示过也允许在"算得出合理尺寸"时对齐（冒烟/离屏场景）
            self.set_grid_size(cols, rows)

    # ─── 网格同步 ─────────────────────────────────────────────────────────

    def _metrics(self):
        """(字符宽, 行高)，单位与控件逻辑像素一致"""
        fm = QFontMetricsF(self.font())
        char_w = fm.horizontalAdvance("M")
        return max(1.0, char_w), max(1.0, fm.lineSpacing() * _LINE_HEIGHT)

    def _measure_line_height(self) -> float:
        """用真实 QTextDocument 量 <pre> 的一行像素高。

        Qt 富文本里无单位 ``line-height:1.3`` 是按 font-size 的倍数解释的，
        与 ``QFontMetricsF.lineSpacing()*1.3`` 相差约 5%——按后者算行数会多出
        一行，视口正好放不下，全屏应用的最后一行（nano 的第二行快捷键）被裁掉，
        而备用屏幕又关掉了滚动条，用户滚动也看不到。以实测高度为准。
        """
        doc = QTextDocument()
        doc.setDocumentMargin(self.document().documentMargin())
        doc.setDefaultFont(self.font())
        doc.setHtml('<pre style="%s">%s</pre>' % (_PRE_STYLE, "<br>".join(["M"] * 4)))
        margin = self.document().documentMargin() * 2
        return max(1.0, (doc.size().height() - margin) / 4.0)

    def _line_height(self) -> float:
        fm = QFontMetricsF(self.font())
        sig = (self.font().family(), round(fm.height(), 2), round(self.devicePixelRatioF(), 3))
        if self._line_px is None or self._line_sig != sig:
            self._line_px = self._measure_line_height()
            self._line_sig = sig
        return self._line_px

    def _grid_for_size(self):
        """按控件尺寸换算网格：列数按字宽、行数按实测行高（宁可少一行也不裁行）

        用控件自身尺寸而不是 ``viewport()``：没显示过的控件（隐藏页签/恢复会话）
        viewport 还没被布局过，会一直停在 Qt 默认的 640x480，于是 ``invoke_shell``
        会以错误尺寸开 PTY——内容按旧尺寸画、控件按新尺寸显示，正是"错位"的来源。
        """
        char_w, _ = self._metrics()
        line_h = self._line_height()
        width = self.size().width()
        height = self.size().height()
        scrollbar = self.verticalScrollBar()
        if scrollbar.isVisible():
            width -= scrollbar.sizeHint().width()
        margin = self.document().documentMargin() * 2 + 4
        cols = max(_MIN_COLS, int((width - margin) / char_w))
        rows = max(_MIN_ROWS, int((height - margin) / line_h))
        return cols, rows

    def _sync_grid(self):
        self.sync_grid(force=False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_grid()

    def showEvent(self, event):
        super().showEvent(event)
        self._ever_shown = True
        self._sync_grid()

    # ─── 键盘输入处理 ─────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        """捕获所有按键，转换为终端字节序列发射到远端 shell"""
        if not self._input_enabled:
            super().keyPressEvent(event)
            return

        key = event.key()
        modifiers = event.modifiers()
        ctrl = bool(modifiers & Qt.ControlModifier)
        alt = bool(modifiers & Qt.AltModifier)
        shift = bool(modifiers & Qt.ShiftModifier)

        # ── 复制/粘贴快捷键（Windows Terminal 惯例） ──
        if ctrl and key == Qt.Key_C:
            # 有选中文本 → 复制；无选中 → 发送 Ctrl+C 中断
            if self.textCursor().hasSelection():
                self.copy()
            else:
                self.key_input.emit('\x03')
            return
        if ctrl and key == Qt.Key_V:
            self._paste_from_clipboard()
            return
        if ctrl and key == Qt.Key_Insert:
            self.copy()
            return
        if shift and key == Qt.Key_Insert:
            self._paste_from_clipboard()
            return

        # ── Ctrl + 字母 → 控制字符 ──
        if ctrl and Qt.Key_A <= key <= Qt.Key_Z:
            ch = chr(key - Qt.Key_A + 1)  # Ctrl+A=\x01 ... Ctrl+Z=\x1a
            self.key_input.emit(ch)
            return

        # ── 功能键 / 方向键（含 Ctrl/Alt/Shift 组合）→ 转义序列 ──
        # 必须排在下面 Ctrl 兜底之前：否则 Ctrl+方向键会被 super() 静默吞掉
        seq = self._special_key_seq(key, modifiers)
        if seq:
            self.key_input.emit(seq)
            return

        # ── Ctrl + 特殊键 ──
        if ctrl:
            if key == Qt.Key_BracketLeft:  # Ctrl+[ = ESC
                self.key_input.emit('\x1b')
                return
            if key == Qt.Key_BracketRight:  # Ctrl+]
                self.key_input.emit('\x1d')
                return
            super().keyPressEvent(event)
            return

        # ── 普通可打印字符 ──
        text = event.text()
        if text:
            if alt:
                text = '\x1b' + text
            self.key_input.emit(text)
            return

        # 其余忽略
        super().keyPressEvent(event)

    # xterm 修饰键编码：mod = 1 + Shift(1) + Alt(2) + Ctrl(4)
    _CURSOR_FINAL = {
        Qt.Key_Up: 'A', Qt.Key_Down: 'B', Qt.Key_Right: 'C', Qt.Key_Left: 'D',
        Qt.Key_Home: 'H', Qt.Key_End: 'F',
    }
    _TILDE_KEYS = {
        Qt.Key_Insert: 2, Qt.Key_Delete: 3, Qt.Key_PageUp: 5, Qt.Key_PageDown: 6,
        Qt.Key_F5: 15, Qt.Key_F6: 17, Qt.Key_F7: 18, Qt.Key_F8: 19,
        Qt.Key_F9: 20, Qt.Key_F10: 21, Qt.Key_F11: 23, Qt.Key_F12: 24,
    }
    _SS3_KEYS = {Qt.Key_F1: 'P', Qt.Key_F2: 'Q', Qt.Key_F3: 'R', Qt.Key_F4: 'S'}

    @staticmethod
    def _mod_code(modifiers) -> int:
        code = 1
        if modifiers & Qt.ShiftModifier:
            code += 1
        if modifiers & Qt.AltModifier:
            code += 2
        if modifiers & Qt.ControlModifier:
            code += 4
        return code

    def _special_key_seq(self, key: int, modifiers) -> str | None:
        """把特殊键映射为终端转义序列（含 DECCKM 应用模式与修饰键组合）"""
        mod = self._mod_code(modifiers)
        plain = (mod == 1)
        if key in self._CURSOR_FINAL:
            final = self._CURSOR_FINAL[key]
            if not plain:
                return f'\x1b[1;{mod}{final}'
            # DECCKM(?1h)：应用模式下方向键/Home/End 走 SS3（ncurses 的 smkx 会开）
            return ('\x1bO' if self._screen.app_cursor_keys else '\x1b[') + final
        if key in self._TILDE_KEYS:
            return (f'\x1b[{self._TILDE_KEYS[key]}~' if plain
                    else f'\x1b[{self._TILDE_KEYS[key]};{mod}~')
        if key in self._SS3_KEYS:
            return (f'\x1bO{self._SS3_KEYS[key]}' if plain
                    else f'\x1b[1;{mod}{self._SS3_KEYS[key]}')
        # Enter / Return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            return '\r'
        # Backspace → DEL (0x7f)，与 xterm 行为一致；Ctrl+Backspace 按 readline 惯例
        if key == Qt.Key_Backspace:
            return '\x17' if (modifiers & Qt.ControlModifier) else '\x7f'
        # Tab
        if key == Qt.Key_Tab:
            return '\t'
        # Shift+Tab (反向 Tab)
        if key == Qt.Key_Backtab:
            return '\x1b[Z'
        # Escape
        if key == Qt.Key_Escape:
            return '\x1b'
        return None

    def inputMethodEvent(self, event):
        """处理 IME 输入法（中文等）"""
        if self._input_enabled:
            commit = event.commitString()
            if commit:
                self.key_input.emit(commit)
            event.accept()
        else:
            super().inputMethodEvent(event)

    def mousePressEvent(self, event):
        """点击终端区域时立即获取键盘焦点；远端开了鼠标上报则把事件发过去"""
        self.setFocus()
        if self._reporting_mouse(event.modifiers()):
            button = {Qt.LeftButton: 0, Qt.MiddleButton: 1, Qt.RightButton: 2}.get(
                event.button(), 0)
            self._send_mouse('M', button, event.position())
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._reporting_mouse(event.modifiers()):
            button = {Qt.LeftButton: 0, Qt.MiddleButton: 1, Qt.RightButton: 2}.get(
                event.button(), 0)
            self._send_mouse('m', button, event.position())
            return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        """拖动/悬停上报：?1002 只在按住时上报，?1003 连悬停也上报"""
        mode = self._screen.mouse_mode
        if self._input_enabled and self._screen.mouse_mode and not (
                event.modifiers() & Qt.ShiftModifier):
            pressed = event.buttons()
            if mode == 1003 or pressed:
                button = 35 if not pressed else 32 + {
                    Qt.LeftButton: 0, Qt.MiddleButton: 1, Qt.RightButton: 2}.get(
                        pressed & (Qt.LeftButton | Qt.MiddleButton | Qt.RightButton), 0)
                self._send_mouse('M', button, event.position())
                return
        super().mouseMoveEvent(event)

    def wheelEvent(self, event):
        if self._reporting_mouse(event.modifiers()):
            code = 64 if event.angleDelta().y() > 0 else 65
            self._send_mouse('M', code, event.position())
            return
        super().wheelEvent(event)

    def focusInEvent(self, event):
        if self._input_enabled and self._screen.focus_events:
            self.key_input.emit('\x1b[I')
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        if self._input_enabled and self._screen.focus_events:
            self.key_input.emit('\x1b[O')
        super().focusOutEvent(event)

    def _reporting_mouse(self, modifiers) -> bool:
        """是否把鼠标事件发给远端（按住 Shift 时留给本地选择/右键菜单）"""
        return bool(self._input_enabled and self._screen.mouse_mode
                    and not (modifiers & Qt.ShiftModifier))

    def _mouse_cell(self, pos):
        """控件坐标 → 终端 (列, 行)，1 基（SGR 鼠标协议要求）"""
        char_w, _ = self._metrics()
        line_h = self._line_height()
        margin = self.document().documentMargin()
        col = int(max(0.0, pos.x() - margin) / char_w) + 1
        row = int(max(0.0, pos.y() - margin) / line_h) + 1
        return min(col, self._screen.cols), min(row, self._screen.rows)

    def _send_mouse(self, kind: str, button: int, pos):
        """按 ?1006(SGR) 或传统 X10 编码上报一次鼠标事件"""
        col, row = self._mouse_cell(pos)
        if self._screen.mouse_sgr:
            self.key_input.emit(f'\x1b[<{button};{col};{row}{kind}')
        else:
            self.key_input.emit('\x1b[M' + chr(32 + button) +
                                chr(32 + min(col, 223)) + chr(32 + min(row, 223)))

    def contextMenuEvent(self, event):
        """右键菜单：复制 / 粘贴（预构建缓存，避免每次右键重建）"""
        if self._reporting_mouse(event.modifiers()):
            return  # 远端应用在用鼠标（vim 等），右键留给远端；本地菜单用 Shift+右键
        if not hasattr(self, '_ctx_menu'):
            self._ctx_menu = QMenu(self)
            self._act_copy = QAction("复制", self._ctx_menu)
            self._act_copy.triggered.connect(self.copy)
            self._ctx_menu.addAction(self._act_copy)
            self._act_paste = QAction("粘贴", self._ctx_menu)
            self._act_paste.triggered.connect(self._paste_from_clipboard)
            self._ctx_menu.addAction(self._act_paste)
        self._act_copy.setEnabled(self.textCursor().hasSelection())
        self._act_paste.setEnabled(bool(QApplication.clipboard().text()))
        self._ctx_menu.exec(event.globalPos())

    def _paste_from_clipboard(self):
        """将剪贴板文本粘贴（发送）到远端 shell"""
        text = QApplication.clipboard().text()
        if not text:
            return
        # 将换行符统一为 \r（终端粘贴惯例）
        text = text.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\r')
        if self._screen.bracketed_paste:
            # ?2004h：vim 等靠它区分"粘贴"与"手打"，否则多行粘贴会逐行自动缩进
            text = '\x1b[200~' + text + '\x1b[201~'
        self.key_input.emit(text)

    # ─── 拖放（P1-5：拖本地文件进终端 = 以括号粘贴规则发送路径） ────────────

    def dragEnterEvent(self, event):
        """拖入本地文件：接受并提示可放置（只拦 urls，纯文本拖放留给基类）"""
        if self._input_enabled and event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event):
        """放置本地文件：路径作为参数发送到远端 shell（不自动回车，用户可见回显后自行执行）"""
        if self._input_enabled and event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls()]
            paths = [p for p in paths if p]
            if paths:
                self._send_paths_to_remote(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _send_paths_to_remote(self, paths):
        """本地路径拼接为命令参数发送（含空白/引号的路径加单引号，bash 语义）

        与 _paste_from_clipboard 相同的括号粘贴规则（?2004h 时包 200~/201~），
        只复用不重写；不追加回车——误拖不会执行任何命令。
        """
        parts = []
        for p in paths:
            if any(c in p for c in " \t'\""):
                p = "'" + p.replace("'", "'\\''") + "'"
            parts.append(p)
        text = ' '.join(parts)
        if self._screen.bracketed_paste:
            text = '\x1b[200~' + text + '\x1b[201~'
        self.key_input.emit(text)

    def focusNextPrevChild(self, next_: bool) -> bool:
        """禁止 Tab/Shift+Tab 触发焦点导航，确保 Tab 作为普通按键处理"""
        return False

    # ─── 渲染 ─────────────────────────────────────────────────────────────

    def _schedule_render(self):
        """调度渲染：50ms 内的多次 write_output 合并为一次 setHtml"""
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _invalidate_render(self):
        """外部状态（输入使能/网格）变化：强制下一帧重建缓存"""
        self._rendered_revision = -1
        self._rendered_structure = -1
        self._rendered_cursor_row = None
        self._row_html_cache = []

    def _render(self):
        screen = self._screen
        if (screen.revision == self._rendered_revision
                and screen.structure_revision == self._rendered_structure):
            return  # 无变化，跳过 setHtml（滚动/高频输出下的主要省时点）

        structure_changed = (screen.structure_revision != self._rendered_structure
                            or len(self._row_html_cache) != screen.total_rows())
        cursor_row = screen.cursor_display_row()
        if structure_changed:
            self._row_html_cache = [None] * screen.total_rows()
            dirty = set(range(screen.total_rows()))
        else:
            offset = screen.scrollback_rows
            dirty = {offset + r for r in screen.dirty_rows()}
            if self._rendered_cursor_row is not None:
                dirty.add(self._rendered_cursor_row)   # 旧光标行要擦掉方块
            dirty.add(cursor_row)
        for idx in dirty:
            if 0 <= idx < len(self._row_html_cache):
                self._row_html_cache[idx] = self._row_html_for(idx, idx == cursor_row)
        screen.clear_dirty_rows()
        self._rendered_revision = screen.revision
        self._rendered_structure = screen.structure_revision
        self._rendered_cursor_row = cursor_row

        # 远端 cwd（OSC 7）：只在变化时通知上层。屏幕模型自身不含 Qt，
        # 所以信号由渲染帧顺带发出（OSC 7 会 bump revision，保证走到这里）。
        if screen.cwd != self._last_cwd:
            self._last_cwd = screen.cwd
            self.cwd_changed.emit(screen.cwd)

        html = '<pre style="%s">%s</pre>' % (
            _PRE_STYLE, '<br>'.join(h or '&nbsp;' for h in self._row_html_cache))

        # 备用屏幕（全屏应用）固定看满屏、不随滚动条漂移；主屏保留回滚跟随。
        # 滚动条策略属于"外观状态"，与内容是否变化无关，必须在缓存比较之前生效，
        # 否则切到备用屏幕但那帧 HTML 恰好与上一帧相同（例如清屏）时策略不会更新。
        alt = screen.alt_screen
        want_policy = Qt.ScrollBarAlwaysOff if alt else Qt.ScrollBarAsNeeded
        if self.verticalScrollBarPolicy() != want_policy:
            self.setVerticalScrollBarPolicy(want_policy)

        if html == self._cached_html:
            return
        self._cached_html = html

        scrollbar = self.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 10
        scroll_value = scrollbar.value()
        # setHtml 会重建整个文档：选区与滚动位置必须自己保存/恢复，
        # 否则远端每来一点输出就清掉用户的选中——Ctrl+C 会从"复制"退化成 SIGINT
        text_cursor = self.textCursor()
        selection = ((text_cursor.selectionStart(), text_cursor.selectionEnd())
                     if text_cursor.hasSelection() else None)

        self.setHtml(html)

        if selection is not None:
            limit = max(1, self.document().characterCount() - 1)
            start, end = min(selection[0], limit), min(selection[1], limit)
            if end > start:
                cursor = self.textCursor()
                cursor.setPosition(start)
                cursor.setPosition(end, QTextCursor.KeepAnchor)
                self.setTextCursor(cursor)
        if alt or at_bottom:
            scrollbar.setValue(scrollbar.maximum())
        else:
            scrollbar.setValue(min(scroll_value, scrollbar.maximum()))

    def _row_html_for(self, index: int, is_cursor_row: bool) -> str:
        cells = self._screen.row_cells(index)
        show_cursor = (is_cursor_row and self._input_enabled
                       and self._screen.cursor_visible)
        return self._row_html(cells, self._screen.cursor_col if show_cursor else -1)

    @staticmethod
    def _row_html(cells, cursor_col: int = -1) -> str:
        """渲染一行；cursor_col >= 0 时在该列画绿色方块光标。

        宽字符占两格（第二格是续格哨兵），渲染时只输出一次字形；
        光标落在宽字符上时方块要盖满两格。
        """
        if not cells:
            return '&nbsp;'
        # 去掉行尾空白（默认空格单元格），否则 120 列的 HTML 全是 &nbsp;
        end = len(cells)
        while end > 0 and cells[end - 1] == (' ', DEFAULT_ATTRS):
            end -= 1
        cells = cells[:end]

        parts = []
        buf: list = []
        run_attrs = DEFAULT_ATTRS
        total = len(cells)

        def flush():
            if buf:
                parts.append(_wrap_span(_escape(''.join(buf)), run_attrs))
                buf.clear()

        i = 0
        while i < total:
            ch, attrs = cells[i]
            wide = (ch not in (' ', WIDE_SENTINEL) and i + 1 < total
                    and cells[i + 1][0] == WIDE_SENTINEL)
            if i == cursor_col:
                flush()
                glyph = '&nbsp;' if ch in (' ', WIDE_SENTINEL) else _escape(ch)
                parts.append(f'<span style="{_CURSOR_STYLE}">{glyph}</span>')
                if wide:
                    parts.append(f'<span style="{_CURSOR_STYLE}">&nbsp;</span>')
                run_attrs = None      # 光标后的文本重新开一个 run
                i += 2 if wide else 1
                continue
            if ch == WIDE_SENTINEL:   # 续格：不产生字符（字形已由左半格输出）
                i += 1
                continue
            if attrs != run_attrs:
                flush()
                run_attrs = attrs
            buf.append(ch)
            i += 2 if wide else 1
        flush()

        if cursor_col >= 0 and cursor_col >= total:
            # 光标在行尾之后：先补齐中间的空格，方块列位才不会左移
            pad = cursor_col - total
            if pad:
                parts.append('&nbsp;' * pad)
            parts.append(f'<span style="{_CURSOR_STYLE}">&nbsp;</span>')
        return ''.join(parts) if parts else '&nbsp;'
