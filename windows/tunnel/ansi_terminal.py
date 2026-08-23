# -*- coding: utf-8 -*-
"""轻量级 ANSI 虚拟终端控件（支持键盘直接输入）

基于 QTextEdit 实现 ANSI 转义序列解析与 HTML 彩色渲染：
- SGR 颜色（前景/背景、粗体、重置）
- 光标移动（上/下/左/右/行首）
- 行擦除 / 屏幕清除
- 回车 / 退格 / Tab 展开
- 备用屏幕切换
- 键盘输入直接转发到远端 shell
"""

from PySide6.QtWidgets import QTextEdit, QApplication, QMenu
from PySide6.QtGui import QFont, QKeyEvent, QAction
from PySide6.QtCore import Qt, Signal, QTimer

# 标准终端 16 色调色板
_COLORS = [
    '#000000',  # 0 black
    '#cd3131',  # 1 red
    '#0dbc79',  # 2 green
    '#e5e510',  # 3 yellow
    '#2472c8',  # 4 blue
    '#bc3fbc',  # 5 magenta
    '#11a8cd',  # 6 cyan
    '#e5e5e5',  # 7 white
    '#666666',  # 8 bright black
    '#f14c4c',  # 9 bright red
    '#23d18b',  # 10 bright green
    '#f5f543',  # 11 bright yellow
    '#3b8eea',  # 12 bright blue
    '#d670d6',  # 13 bright magenta
    '#29b8db',  # 14 bright cyan
    '#ffffff',  # 15 bright white
]

_DEFAULT_FG = '#e5e5e5'
_DEFAULT_BG = '#1e1e1e'

# attrs 元组: (fg, bg, bold, underline) — 不可变，避免 dict 拷贝开销
_DEFAULT_ATTRS = ('default', 'default', False, False)

# 增量渲染开关：设为 False 可回退到全量渲染
_ENABLE_INCREMENTAL_RENDER = True


def _wrap_span(text: str, attrs: tuple | None) -> str:
    """将已转义的文本包裹在带样式的 span 中（attrs 为 tuple）"""
    if attrs and attrs != _DEFAULT_ATTRS:
        styles = []
        fg = attrs[0]
        styles.append(f'color:{fg if fg != "default" else _DEFAULT_FG}')
        bg = attrs[1]
        if bg != 'default':
            styles.append(f'background-color:{bg}')
        if attrs[2]:  # bold
            styles.append('font-weight:bold')
        if attrs[3]:  # underline
            styles.append('text-decoration:underline')
        return f'<span style="{";".join(styles)}">{text}</span>'
    return f'<span style="color:{_DEFAULT_FG}">{text}</span>'


class ANSITerminalWidget(QTextEdit):
    """支持 ANSI 序列解析 + 键盘直接输入的终端控件

    用户按键 → key_input 信号发射对应字节 → 远端 shell 回显 → write_output 渲染
    """

    # 用户键盘输入通过此信号发射（str 为要发送给 shell 的原始字节）
    key_input = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 10))
        self.setStyleSheet(
            "QTextEdit { background-color: #1e1e1e; border: none; }"
        )
        self.setLineWrapMode(QTextEdit.NoWrap)
        # 允许键盘焦点
        self.setFocusPolicy(Qt.StrongFocus)
        # 终端状态
        self._lines: list[list[tuple[str, tuple]]] = [[]]  # 每行: [(text, attrs_tuple), ...]
        self._cursor_row = 0
        self._cursor_col = 0
        self._attrs: tuple = _DEFAULT_ATTRS
        self._saved_cursor = (0, 0)
        # 备用屏幕
        self._alt_lines = None
        self._alt_cursor = None
        # 解析状态
        self._esc_buf = ''
        self._in_esc = False
        self._max_lines = 5000
        # 输入使能（连接成功前禁止键盘输入）
        self._input_enabled = False
        # 渲染合并定时器：将高频 write_output 合并为一次 _render（~20fps）
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(50)  # 50ms ≈ 20fps
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._render)
        self._render_pending = False
        # 增量渲染状态
        self._dirty_rows: set[int] = set()  # 脏行集合
        self._cached_html: str = ''  # 上一次渲染的 HTML 缓存
        self._prev_line_count: int = 0  # 上一次渲染的行数

    # ─── 公开接口 ─────────────────────────────────────────────────────────

    def set_input_enabled(self, enabled: bool):
        """设置是否允许键盘输入（连接成功后启用）"""
        self._input_enabled = enabled

    def write_output(self, data: str):
        """写入终端数据（解析 ANSI 序列，延迟合并渲染）"""
        self._parse(data)
        self._schedule_render()

    def _schedule_render(self):
        """调度渲染：50ms 内的多次 write_output 合并为一次 setHtml"""
        if not self._render_timer.isActive():
            self._render_timer.start()

    def clear_terminal(self):
        """清空终端"""
        self._lines = [[]]
        self._cursor_row = 0
        self._cursor_col = 0
        self._attrs = _DEFAULT_ATTRS
        self._dirty_rows.clear()
        self._cached_html = ''
        self._render()

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

        # ── 功能键 / 方向键 → 转义序列 ──
        seq = self._special_key_seq(key, shift)
        if seq:
            if alt:
                seq = '\x1b' + seq
            self.key_input.emit(seq)
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

    @staticmethod
    def _special_key_seq(key: int, shift: bool) -> str | None:
        """将特殊键映射为终端转义序列"""
        _MAP = {
            Qt.Key_Up: '\x1b[A',
            Qt.Key_Down: '\x1b[B',
            Qt.Key_Right: '\x1b[C',
            Qt.Key_Left: '\x1b[D',
            Qt.Key_Home: '\x1b[H',
            Qt.Key_End: '\x1b[F',
            Qt.Key_Insert: '\x1b[2~',
            Qt.Key_Delete: '\x1b[3~',
            Qt.Key_PageUp: '\x1b[5~',
            Qt.Key_PageDown: '\x1b[6~',
            Qt.Key_F1: '\x1bOP',
            Qt.Key_F2: '\x1bOQ',
            Qt.Key_F3: '\x1bOR',
            Qt.Key_F4: '\x1bOS',
            Qt.Key_F5: '\x1b[15~',
            Qt.Key_F6: '\x1b[17~',
            Qt.Key_F7: '\x1b[18~',
            Qt.Key_F8: '\x1b[19~',
            Qt.Key_F9: '\x1b[20~',
            Qt.Key_F10: '\x1b[21~',
            Qt.Key_F11: '\x1b[23~',
            Qt.Key_F12: '\x1b[24~',
        }
        if key in _MAP:
            return _MAP[key]
        # Enter / Return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            return '\r'
        # Backspace → DEL (0x7f)，与 xterm 行为一致
        if key == Qt.Key_Backspace:
            return '\x7f'
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

    def focusInEvent(self, event):
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)

    def mousePressEvent(self, event):
        """点击终端区域时立即获取键盘焦点"""
        self.setFocus()
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        """右键菜单：复制 / 粘贴（预构建缓存，避免每次右键重建）"""
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
        if text:
            # 将换行符统一为 \r（终端粘贴惯例）
            text = text.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\r')
            self.key_input.emit(text)

    def focusNextPrevChild(self, next_: bool) -> bool:
        """禁止 Tab/Shift+Tab 触发焦点导航，确保 Tab 作为普通按键处理"""
        return False

    # ─── ANSI 解析 ────────────────────────────────────────────────────────

    def _parse(self, data: str):
        i = 0
        n = len(data)
        # 1. 普通态遇到 ESC 进转义态；2. 转义态按首字节分 CSI/OSC/单字节三路；3. 凑齐一条就执行并回到普通态
        while i < n:
            if self._in_esc:
                self._esc_buf += data[i]
                i += 1
                # 判断序列是否完整
                if len(self._esc_buf) == 1:
                    if self._esc_buf[0] == '[':
                        continue  # CSI 开始
                    elif self._esc_buf[0] == ']':
                        continue  # OSC 开始
                    else:
                        # 简单转义 (ESC M 等)，忽略
                        self._in_esc = False
                        self._esc_buf = ''
                        continue
                # 检查 CSI 是否结束
                if self._esc_buf[0] == '[':
                    last = self._esc_buf[-1]
                    if last.isalpha() and last not in '0123456789;':
                        self._dispatch_csi(self._esc_buf[1:])
                        self._in_esc = False
                        self._esc_buf = ''
                    elif len(self._esc_buf) > 64:
                        # 缓冲超 64 字节还没等到终止符，多半是畸形或未结束的序列，直接丢弃防无限吃内存
                        self._in_esc = False
                        self._esc_buf = ''
                    continue
                # OSC: 以 BEL(\x07) 或 ST(ESC\\) 结束
                if self._esc_buf[0] == ']':
                    if '\x07' in self._esc_buf or '\x1b\\' in self._esc_buf:
                        self._in_esc = False
                        self._esc_buf = ''
                    elif len(self._esc_buf) > 256:
                        # OSC 同理，256 字节兜底截断，防止未终止的 OSC 一直累积内存
                        self._in_esc = False
                        self._esc_buf = ''
                    continue
                # 兜底
                self._in_esc = False
                self._esc_buf = ''
            else:
                ch = data[i]
                if ch == '\x1b':
                    self._in_esc = True
                    self._esc_buf = ''
                    i += 1
                elif ch == '\n':
                    # 换行让光标离开旧行又进入新行，两行都要标脏重绘，否则光标残影留在旧行
                    self._dirty_rows.add(self._cursor_row)  # 旧行光标消失
                    self._cursor_row += 1
                    self._cursor_col = 0
                    self._ensure_rows()
                    self._dirty_rows.add(self._cursor_row)  # 新行光标出现
                    i += 1
                elif ch == '\r':
                    self._dirty_rows.add(self._cursor_row)
                    self._cursor_col = 0
                    i += 1
                elif ch == '\b':
                    if self._cursor_col > 0:
                        self._dirty_rows.add(self._cursor_row)
                        self._cursor_col -= 1
                    i += 1
                elif ch == '\t':
                    # Tab 展开到下一个 8 列制表位
                    # 向下取整到 8 的倍数再进位：cursor_col=3 时 Tab 跳到 cursor_col=8（即 1 基的第 4 列跳到第 9 列），和 xterm 一致
                    next_tab = ((self._cursor_col // 8) + 1) * 8
                    self._cursor_col = next_tab
                    i += 1
                elif ch == '\x07':
                    i += 1  # BEL 忽略
                else:
                    self._put_char(ch)
                    i += 1

    def _dispatch_csi(self, seq: str):
        """解析并执行 CSI 序列: params + final_byte"""
        if not seq:
            return
        final = seq[-1]
        params_str = seq[:-1]
        params = []
        if params_str:
            # CSI 参数以 ; 分隔，空槽位一律按 0 处理，ANSI 规范里 0 等价于缺省值
            for p in params_str.split(';'):
                try:
                    params.append(int(p))
                except ValueError:
                    # 混入非法字符就按 0 兜底，避免带病参数把后续逻辑搞崩
                    params.append(0)

        if final == 'm':
            self._handle_sgr(params or [0])
        elif final == 'H' or final == 'f':
            # H/f 的前两个参数是行/列，ANSI 坐标从 1 起算，这里减 1 转内部 0 基并夹住下界
            self._dirty_rows.add(self._cursor_row)
            row = (params[0] if params else 1) - 1
            col = (params[1] if len(params) > 1 else 1) - 1
            self._cursor_row = max(0, row)
            self._cursor_col = max(0, col)
            self._ensure_rows()
            self._dirty_rows.add(self._cursor_row)
        elif final == 'A':
            n = params[0] if params and params[0] else 1
            self._dirty_rows.add(self._cursor_row)
            self._cursor_row = max(0, self._cursor_row - n)
            self._dirty_rows.add(self._cursor_row)
        elif final == 'B':
            self._dirty_rows.add(self._cursor_row)
            n = params[0] if params and params[0] else 1
            self._cursor_row += n
            self._ensure_rows()
            self._dirty_rows.add(self._cursor_row)
        elif final == 'C':
            self._dirty_rows.add(self._cursor_row)
            n = params[0] if params and params[0] else 1
            self._cursor_col += n
        elif final == 'D':
            self._dirty_rows.add(self._cursor_row)
            n = params[0] if params and params[0] else 1
            self._cursor_col = max(0, self._cursor_col - n)
        elif final == 'G':
            self._dirty_rows.add(self._cursor_row)
            col = (params[0] if params else 1) - 1
            self._cursor_col = max(0, col)
        elif final == 'J':
            # 不带参数时 ED/EL 默认按模式 0 处理（擦光标到末尾），不能当清除全屏
            mode = params[0] if params else 0
            self._handle_ed(mode)
        elif final == 'K':
            mode = params[0] if params else 0
            self._handle_el(mode)
        elif final == 's':
            self._saved_cursor = (self._cursor_row, self._cursor_col)
        elif final == 'u':
            self._dirty_rows.add(self._cursor_row)
            self._cursor_row, self._cursor_col = self._saved_cursor
            self._dirty_rows.add(self._cursor_row)
        elif final == 'h':
            self._handle_mode(params, True)
        elif final == 'l':
            self._handle_mode(params, False)
        # 其余序列忽略

    def _handle_sgr(self, params: list):
        """处理 SGR (Select Graphic Rendition) — attrs 为不可变 tuple"""
        fg, bg, bold, underline = self._attrs
        i = 0
        # SGR 允许一条序列带多组参数（如 1;31;42），必须逐个消费，索引 i 按每个参数的占位数推进
        while i < len(params):
            p = params[i]
            if p == 0:
                fg, bg, bold, underline = _DEFAULT_ATTRS
            elif p == 1:
                bold = True
            elif p == 22:
                bold = False
            elif p == 4:
                underline = True
            elif p == 24:
                underline = False
            elif 30 <= p <= 37:
                fg = _COLORS[p - 30]
            elif p == 38:
                # 扩展前景色: 38;5;n 或 38;2;r;g;b
                if i + 1 < len(params) and params[i + 1] == 5:
                    if i + 2 < len(params):
                        fg = self._color_256(params[i + 2])
                        # 38;5;N 共 3 个参数，这里额外跳过 5 和 N，否则 N 会被当成下一个独立 SGR 码误执行
                        i += 2
                elif i + 1 < len(params) and params[i + 1] == 2:
                    if i + 4 < len(params):
                        r, g, b = params[i + 2], params[i + 3], params[i + 4]
                        fg = f'#{r:02x}{g:02x}{b:02x}'
                        # 38;2;r;g;b 共 5 个参数，跳过 2;r;g;b 四个，防 r/g/b 被误读成后续 SGR 码
                        i += 4
            elif p == 39:
                fg = 'default'
            elif 40 <= p <= 47:
                bg = _COLORS[p - 40]
            elif p == 48:
                if i + 1 < len(params) and params[i + 1] == 5:
                    if i + 2 < len(params):
                        bg = self._color_256(params[i + 2])
                        i += 2
                elif i + 1 < len(params) and params[i + 1] == 2:
                    if i + 4 < len(params):
                        r, g, b = params[i + 2], params[i + 3], params[i + 4]
                        bg = f'#{r:02x}{g:02x}{b:02x}'
                        i += 4
            elif p == 49:
                bg = 'default'
            elif 90 <= p <= 97:
                fg = _COLORS[p - 90 + 8]
            elif 100 <= p <= 107:
                bg = _COLORS[p - 100 + 8]
            i += 1
        self._attrs = (fg, bg, bold, underline)

    @staticmethod
    def _color_256(n: int) -> str:
        """将 256 色索引转为 hex"""
        if n < 16:
            return _COLORS[n]
        elif n < 232:
            # 6x6x6 色块
            n -= 16
            # 减 16 后按 6 进制拆：n%6 是蓝分量、(n//6)%6 是绿、n//36 是红，每级乘 51 把 0~5 拉到 0~255
            b = (n % 6) * 51
            g = ((n // 6) % 6) * 51
            r = (n // 36) * 51
            return f'#{r:02x}{g:02x}{b:02x}'
        else:
            # 灰度
            # 232-255 共 24 级线性灰阶，从 8 起步长 10，避免起点纯黑和前景默认黑混淆
            v = 8 + (n - 232) * 10
            return f'#{v:02x}{v:02x}{v:02x}'

    def _handle_ed(self, mode: int):
        """ED - 屏幕擦除"""
        if mode == 2:
            # 模式 2 全屏擦除：重建至少 24 行空屏并把光标归位到左上角
            self._lines = [[] for _ in range(max(self._cursor_row + 1, 24))]
            self._cursor_row = 0
            self._cursor_col = 0
            self._dirty_rows.clear()
        elif mode == 0:
            # 模式 0 擦光标到屏尾：当前行只截留光标前的列，光标之后的行整体丢弃
            # 光标到屏幕末尾
            if self._cursor_row < len(self._lines):
                self._lines[self._cursor_row] = \
                    self._lines[self._cursor_row][:self._cursor_col]
                self._dirty_rows.add(self._cursor_row)
            old_count = len(self._lines)
            self._lines = self._lines[:self._cursor_row + 1]
            if len(self._lines) < old_count:
                self._dirty_rows.clear()  # 行数变了，缓存失效
        elif mode == 1:
            # 模式 1 擦屏首到光标：光标之前的行整行清空，当前行只删光标前的列
            # 屏幕开头到光标
            for r in range(self._cursor_row):
                if self._lines[r]:
                    self._lines[r] = []
                    self._dirty_rows.add(r)
            if self._cursor_row < len(self._lines):
                self._lines[self._cursor_row] = \
                    self._lines[self._cursor_row][self._cursor_col:]
                self._cursor_col = 0
                # 当前行前段被删，列号必须归零，否则后续输出位置整体错位
                self._dirty_rows.add(self._cursor_row)

    def _handle_el(self, mode: int):
        """EL - 行擦除"""
        row = self._cursor_row
        if row >= len(self._lines):
            return
        if mode == 0:
            # EL 模式 0 只擦光标右侧，光标本身位置不动
            self._lines[row] = self._lines[row][:self._cursor_col]
        elif mode == 1:
            # 模式 1 擦光标左侧（不含光标下字符，该字符随切片保留并左移到行首），列号必须跟着归零
            self._lines[row] = self._lines[row][self._cursor_col:]
            self._cursor_col = 0
        elif mode == 2:
            self._lines[row] = []
            self._cursor_col = 0
        self._dirty_rows.add(row)

    def _handle_mode(self, params: list, set_mode: bool):
        """处理 h/l 模式设置（备用屏幕等）"""
        for p in params:
            if p == 1049 or p == 47:
                if set_mode:
                    # 进入备用屏幕
                    self._alt_lines = self._lines
                    self._alt_cursor = (self._cursor_row, self._cursor_col)
                    self._lines = [[]]
                    self._cursor_row = 0
                    self._cursor_col = 0
                    self._dirty_rows.clear()
                    self._cached_html = ''
                else:
                    # 退出备用屏幕
                    if self._alt_lines is not None:
                        self._lines = self._alt_lines
                        self._alt_lines = None
                    if self._alt_cursor is not None:
                        self._cursor_row, self._cursor_col = self._alt_cursor
                        self._alt_cursor = None
                    self._dirty_rows.clear()
                    self._cached_html = ''

    # ─── 字符写入 ─────────────────────────────────────────────────────────

    def _ensure_rows(self):
        while self._cursor_row >= len(self._lines):
            self._lines.append([])
        # 限制总行数
        if len(self._lines) > self._max_lines:
            excess = len(self._lines) - self._max_lines
            self._lines = self._lines[excess:]
            # 裁掉头部 excess 行后，光标行号要同步下移，否则指着已被丢弃的行，后续输出全部错位
            self._cursor_row -= excess
            self._dirty_rows.clear()  # 行裁剪后缓存失效

    def _put_char(self, ch: str):
        self._ensure_rows()
        line = self._lines[self._cursor_row]
        col = self._cursor_col
        attrs = self._attrs  # tuple 不可变，直接引用，无需拷贝
        self._dirty_rows.add(self._cursor_row)
        # 扩展行
        while len(line) <= col:
            line.append((' ', _DEFAULT_ATTRS))
        line[col] = (ch, attrs)
        self._cursor_col = col + 1

    # ─── HTML 渲染 ────────────────────────────────────────────────────────

    def _render(self):
        # 增量渲染：无脏行且行数未变时跳过
        if _ENABLE_INCREMENTAL_RENDER:
            # setHtml 会全量重排文档，滚动时一次全绘比只画脏行慢一个量级，所以先靠脏行集合挡掉无谓重绘
            if not self._dirty_rows and self._prev_line_count == len(self._lines):
                return
            # 有脏行或行数变化 → 重建 HTML（setHtml 是全量的，但跳过无变化场景）
            # 若只有少量脏行且行数不变，仍可优化为局部更新
            # 当前方案：dirty 检查 + 缓存对比
            html = self._build_html()
            if html == self._cached_html:
                return  # 内容未变，跳过 setHtml
            self._cached_html = html
            self._prev_line_count = len(self._lines)
            self._dirty_rows.clear()
        else:
            html = self._build_html()

        scrollbar = self.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 10

        self.setHtml(html)

        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def _build_html(self) -> str:
        parts = [
            '<pre style="margin:0; font-family:Consolas,\'Courier New\',monospace; '
            'font-size:10pt; line-height:1.3;">'
        ]
        for row_idx, line in enumerate(self._lines):
            is_cursor_row = (row_idx == self._cursor_row and self._input_enabled)
            # 只有光标行传光标列用于注入光标方块，其余行传 -1，避免每行都被注入光标
            parts.append(self._row_html(line, self._cursor_col if is_cursor_row else -1))
            parts.append('<br>')
        parts.append('</pre>')
        return ''.join(parts)

    @staticmethod
    def _row_html(line: list, cursor_col: int = -1) -> str:
        """渲染单行为 HTML。cursor_col >= 0 时在该列位置注入绿色方块光标。"""
        if not line and cursor_col < 0:
            return '&nbsp;'

        parts = []
        cur_attrs: tuple | None = None
        buf: list[str] = []
        buf_start_col = 0  # buf 中第一个字符对应的列号
        col_counter = 0    # 当前已处理的列号

        def flush():
            nonlocal cur_attrs, buf, buf_start_col, col_counter
            if not buf:
                return
            # 逐字符输出，在 cursor_col 位置插入光标
            text_parts = []
            for idx, ch in enumerate(buf):
                actual_col = buf_start_col + idx
                if actual_col == cursor_col:
                    # 先输出之前积累的文本
                    pre = ''.join(text_parts)
                    if pre:
                        text_parts_out = pre.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace(' ', '&nbsp;')
                        parts.append(_wrap_span(text_parts_out, cur_attrs))
                        text_parts = []
                    # 注入光标（覆盖当前字符）
                    cursor_ch = ch if ch != ' ' else '&nbsp;'
                    if cursor_ch == ' ':
                        cursor_ch = '&nbsp;'
                    else:
                        cursor_ch = cursor_ch.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    parts.append(
                        f'<span style="background-color:#00ff00;color:#1e1e1e;">{cursor_ch}</span>'
                    )
                else:
                    text_parts.append(ch)
            # 输出剩余文本
            if text_parts:
                pre = ''.join(text_parts)
                pre = pre.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace(' ', '&nbsp;')
                parts.append(_wrap_span(pre, cur_attrs))
            buf = []

        for ch, attrs in line:
            if attrs != cur_attrs:
                flush()
                cur_attrs = attrs
                buf_start_col = col_counter
            buf.append(ch)
            col_counter += 1
        flush()

        # 如果光标在行末之后（行长度 <= cursor_col），追加光标
        if cursor_col >= 0 and cursor_col >= col_counter:
            parts.append(
                '<span style="background-color:#00ff00;color:#1e1e1e;">&nbsp;</span>'
            )

        return ''.join(parts) if parts else '&nbsp;'
