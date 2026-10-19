# -*- coding: utf-8 -*-
"""VT100 / xterm 屏幕模型（纯逻辑，零 Qt 依赖，可离线单测）。

为什么单独抽这一层
------------------
远端 Shell 里的全屏应用（nano / vim / less / top）都是 ncurses 程序：它们不靠换行符
排版，而是每画一行都发一条「行定位」CSI 序列，收尾再用 ``sgr0 = \\E(B\\E[m`` 复位字符集。
旧实现（windows/remote_session/ansi_terminal.py）把解析和渲染揉在 QTextEdit 上，
只认 ``H``/``A``-``D``/``G``/``J``/``K`` 等少数序列，结果：

* ``\\E[<n>d``（VPA，ncurses 做纯纵向移动最常用的序列）、``\\E[<n>E``（CNL）、
  ``\\E[L``/``\\E[M``（插行/删行）、``\\E[<n>S``/``\\E[<n>T``（滚屏）、
  ``\\E[<n>;<m>r``（滚动区域）、``\\E[<n>X``/``P``/``@``（擦/删/插字符）全部被当成
  未知序列丢弃 → 该换行的没换，多行叠成一行（实测：nano 首屏 30 行正文只剩 1 行）；
* ``\\E(`` 只吃掉两个字节，紧跟的 ``B``/``0`` 落成普通可见字符 → 整屏每处属性复位
  都多出一个字面 ``B``（用户截图里每行都带一个 B）；
* 没有固定网格：行可以无限长、可无限增长，光标定位到 40 行以外也不受限。

本模块把「屏幕语义」完整实现，坐标一律 0 基（ANSI 参数在解析处换算）。
屏幕 = ``rows`` 行 × ``cols`` 列单元格；主屏滚出顶部的行进 scrollback（仅主屏），
备用屏幕（1049/47/1047）没有回滚。上层只做两件事：喂字符串、把行画成 HTML。

宽字符支持
-----------------------
真实终端里 CJK / emoji 占 **两列**（wcwidth），中文界面（nano 的中文快捷键、
中文文件名、``ls`` 输出）全靠这一点对齐。本模型用一个「字符格 + 续格哨兵」表示宽字符：
宽字符占 2 个格（第二格为 ``_WIDE_SENTINEL``），于是「格下标 == 列号」这一点保持成立，
光标、EL/DCH/ICH/ECH、渲染都不需要额外的列换算。
"""

import unicodedata
from collections import deque
from urllib.parse import unquote as _url_unquote

# ── 标准终端 16 色调色板 ────────────────────────────────────────────────
COLORS = [
    "#000000", "#cd3131", "#0dbc79", "#e5e510", "#2472c8", "#bc3fbc",
    "#11a8cd", "#e5e5e5", "#666666", "#f14c4c", "#23d18b", "#f5f543",
    "#3b8eea", "#d670d6", "#29b8db", "#ffffff",
]
# 下标即语义：0-7 暗色、8-15 亮色，SGR 30-37 / 90-97 直接按此下标取色，
# 因此不能按视觉亮度重新排序

DEFAULT_FG = "default"
DEFAULT_BG = "default"
# (fg, bg, bold, underline, reverse) —— 不可变，便于按值比较做 run 合并
DEFAULT_ATTRS = (DEFAULT_FG, DEFAULT_BG, False, False, False)

# DEC Special Graphics（ACS）：``\E(0`` 之后这些 ASCII 要画成线框字符
# 表必须完整：缺一个键，ncurses 画框时就会漏出原字母（q 变 q 而不是 ─）
ACS_MAP = {
    "`": "◆", "a": "▒", "b": "␉", "c": "␌", "d": "␍", "e": "␊", "f": "°",
    "g": "±", "h": "␤", "i": "␋", "j": "┘", "k": "┐", "l": "┌", "m": "└",
    "n": "┼", "o": "⎺", "p": "⎻", "q": "─", "r": "⎼", "s": "⎽", "t": "├",
    "u": "┤", "v": "┴", "w": "┬", "x": "│", "y": "≤", "z": "≥", "|": "≠",
    "{": "π", "}": "£", "~": "·", "_": " ",
}

# 制表位每 8 列一个，与 xterm 默认一致；改小会让 ls -l 之类对齐输出错位
_TAB_WIDTH = 8
# OSC 缓冲上限：要放得下 OSC 7 的长路径，同时仍是有界缓冲
_MAX_OSC = 1024
# OSC 7 的路径长度上限：超过视为异常上报（防畸形数据把状态条撑爆）
_MAX_OSC_PATH = 1024
# CSI 参数上限：畸形输入（长串分号）不能把参数缓冲撑成无界字符串
_MAX_CSI = 64
# 宽字符的续格哨兵：C0 里的 NUL 不会出现在正常终端输出里，
# 用它做标记可以让 _freeze/_thaw（回滚行的压缩表示）原样保真列结构。
_WIDE_SENTINEL = "\x00"
# 公开别名：渲染层（ansi_terminal）需要识别续格
WIDE_SENTINEL = _WIDE_SENTINEL


class VTScreen:
    """一块 ``cols`` × ``rows`` 的终端屏幕 + 光标 + 属性 + 回滚。

    只做屏幕语义、不碰 Qt：上层喂字符串，再把行取去渲染。坐标统一 0 基，
    ANSI 的 1 基参数在解析层换算掉，避免两种基准在调用链里混用。
    """

    def __init__(self, cols=80, rows=24, max_scrollback=2000):
        # 尺寸下限夹到 1：个别 SSH 服务端在窗口最小化后会上报 0 列，
        # 不夹住会让后续所有按下标取格的地方直接崩
        self._cols = max(1, int(cols))
        self._rows = max(1, int(rows))
        self._max_scrollback = max(0, int(max_scrollback))
        self._revision = 0            # 任何影响渲染的变化都 +1
        self._structure_revision = 0  # 行结构变化（回滚/改尺寸/切屏）：渲染侧需全量重建
        self._dirty_rows = set()      # 屏幕内变脏的行号
        self._init_screen_state()

    # ── 构造与重置 ────────────────────────────────────────────────────

    def _init_screen_state(self):
        """把屏幕、光标与各模式复位到初始态（构造与 RIS 共用一份实现）。

        构造、RIS、clear 三处若各写一份复位逻辑，新增模式时极易漏改其中
        一处，表现为「reset 之后某个模式还活着」。
        """
        self._screen = [self._blank_row() for _ in range(self._rows)]
        self._scrollback = deque()
        self._primary = None          # 备用屏幕期间保存的主屏状态
        self._alt_active = False
        self._cursor_row = 0
        self._cursor_col = 0
        self._wrap_pending = False    # 最后一列写入后挂起，下一个字符才换行
        self._auto_wrap = True
        self._scroll_top = 0
        self._scroll_bottom = self._rows - 1
        self._attrs = DEFAULT_ATTRS
        self._origin_mode = False
        self._insert_mode = False
        # 字符集：G0/G1 各自可能是 ACS(线框) 或 ASCII，GL 决定当前用哪个
        self._g0_acs = False
        self._g1_acs = False
        self._gl = 0
        self._acs = False
        self._cursor_visible = True
        self._saved_cursor = None     # DECSC：光标 + 属性 + 字符集 + 原点模式
        self._tab_stops = self._default_tab_stops()
        self._last_char = None        # REP(\\E[b) 用
        self._charset_kind = "("
        # OSC 7 上报的工作目录（远端 shell 主动发，仅消费不注入）
        self._cwd = ""
        self._cwd_host = ""
        self._osc_overflow = False
        # 输入侧模式（控件要按这些状态选键序列 / 是否上报鼠标）
        self._app_cursor_keys = False   # ?1h  DECCKM：方向键改发 \\EOA..D
        self._bracketed_paste = False   # ?2004h 括号粘贴
        self._mouse_modes = set()       # ?1000/?1002/?1003
        self._mouse_sgr = False         # ?1006h
        self._focus_events = False      # ?1004h
        self._reset_parser_state()
        self._revision += 1
        self._structure_revision += 1
        self._dirty_rows.clear()

    def _reset_parser_state(self):
        """丢弃半截转义序列；不清会让下一段输出接在上次的残缺参数上解析。

        复位发生在 RIS/clear 与尺寸变化之后，此时旧的 _params 已无归属。
        """
        self._state = "normal"
        self._params = ""
        self._inter = ""
        self._osc = []

    def _blank_row(self):
        """一整行空白格；每次返回新列表，共用一份会让改 A 行时连带改 B 行。

        清屏、RIS、改尺寸都靠它生成空行，列表必须是独立的。
        """
        return [(" ", DEFAULT_ATTRS) for _ in range(self._cols)]

    def _default_tab_stops(self):
        """默认制表位：每 8 列一个，不含第 0 列（那本来就是行首）。

        改尺寸后必须重算：旧制表位可能已经落在新列宽之外。
        """
        return set(range(_TAB_WIDTH, self._cols, _TAB_WIDTH))

    def reset(self):
        """RIS：整屏复位（保留尺寸）。

        与 clear 的区别是连输入模式、字符集、滚动区一起回默认。
        """
        self._init_screen_state()

    def clear(self):
        """清屏并清空回滚（供控件的 clear_terminal 使用）。

        与 RIS 的区别：这里保留尺寸和用户已开启的输入模式。用户点「清屏」
        只是想去掉内容，不该顺带把括号粘贴、鼠标上报一起关掉。
        """
        self._screen = [self._blank_row() for _ in range(self._rows)]
        self._scrollback.clear()
        self._scroll_top, self._scroll_bottom = 0, self._rows - 1
        self._wrap_pending = False
        self._set_cursor(0, 0)
        self._touch_structure()

    # ── 只读属性 ──────────────────────────────────────────────────────

    @property
    def cols(self):
        """列数；渲染侧据此决定每行生成多少单元格。

        拖窗口时它和 rows 一起变，渲染侧按需重取而不是缓存旧值。
        """
        return self._cols

    @property
    def rows(self):
        """屏幕行数（不含回滚行）。

        要渲染总行数请用 total_rows()，它把回滚行也算进去。
        """
        return self._rows

    @property
    def cursor_row(self):
        """光标行号（屏幕坐标，0 基，不含回滚偏移）。

        切备用屏幕时会整体换掉，不能跨切屏缓存。
        """
        return self._cursor_row

    @property
    def cursor_col(self):
        """光标列号；宽字符上光标停在其左半格。

        写一个宽字符要前进两列，所以它不等于已写字符数。
        """
        return self._cursor_col

    @property
    def cursor_visible(self):
        """DECTCEM(?25)：为 False 时不画光标方块，但定位逻辑照旧。

        软复位会重新打开，客户端据此隐藏光标时不影响后续输出。
        """
        return self._cursor_visible

    @property
    def alt_screen(self):
        """是否处于备用屏幕（1049/47/1047）；备用屏幕不产回滚。

        渲染侧据此决定要不要把回滚行一起画出来。
        """
        return self._alt_active

    @property
    def revision(self):
        """内容版本号：任何影响渲染的变化都 +1，渲染侧靠它判断要不要重画。

        只读快照，渲染侧比较前后值即可决定是否重绘，不必逐行比对。
        """
        return self._revision

    @property
    def structure_revision(self):
        """行结构版本号（回滚增长 / 改尺寸 / 切屏）。

        与 revision 分开是因为重建代价不同：结构变化要整表重建，普通变脏
        只重画脏行；混用会让每次光标移动都触发全量重排。
        """
        return self._structure_revision

    @property
    def scrollback_rows(self):
        """回滚行数（主屏才有；备用屏幕恒为 0）。

        超过 max_scrollback 时最旧的行被丢弃，不会无限增长。
        """
        return len(self._scrollback)

    @property
    def cwd(self):
        """远端 shell 最近一次上报的工作目录（OSC 7）；未上报过为空串。

        只消费不注入：shell 不发 OSC 7 时这里一直是空串，终端行为不受影响。
        """
        return self._cwd

    @property
    def cwd_host(self):
        """OSC 7 里的主机名部分（多数 shell 发 hostname，用于判断是否本机）。

        空串表示 shell 从未上报，此时不做任何路径联动。
        """
        return self._cwd_host

    @property
    def app_cursor_keys(self):
        """DECCKM(?1h)：方向键/Home/End 是否用应用模式序列（\\EOA..）。

        控件按它决定发 \\EOA 还是 \\E[A，发错时 vim 里方向键会变成字母。
        """
        return self._app_cursor_keys

    @property
    def bracketed_paste(self):
        """?2004h：粘贴是否要用 \\E[200~ ... \\E[201~ 包起来。

        不包时多行粘贴会被当成逐行回车提交，在 vim 里就是灾难。
        """
        return self._bracketed_paste

    @property
    def mouse_mode(self):
        """0=不上报；1000=点击；1002=拖动；1003=移动。

        同时开启多个时取最全的一个，1003 > 1002 > 1000。
        """
        for mode in (1003, 1002, 1000):
            if mode in self._mouse_modes:
                return mode
        return 0

    @property
    def mouse_sgr(self):
        """?1006h：鼠标用 SGR 扩展格式上报。

        老式 X10 格式表达不了大于 223 的坐标，宽屏必须用 SGR。
        """
        return self._mouse_sgr

    @property
    def focus_events(self):
        """?1004h：焦点进出是否上报。

        vim 靠它区分切窗口与真正离开，避免误触发自动保存。
        """
        return self._focus_events

    def total_rows(self):
        """渲染总行数 = 回滚行数 + 屏幕行数（备用屏幕不含回滚）。

        渲染侧按它分配行高，取少会让最后一行被裁掉。
        """
        return len(self._scrollback) + self._rows

    def cursor_display_row(self):
        """光标在渲染坐标系里的行号（总行号）。

        回滚增长时它会跟着变大，不能当成屏幕内行号用。
        """
        return len(self._scrollback) + self._cursor_row

    def dirty_rows(self):
        """本轮变化过的屏幕行号（供渲染侧增量重建）。

        返回副本：调用方遍历时渲染侧可能仍在继续标脏。
        """
        return set(self._dirty_rows)

    def clear_dirty_rows(self):
        """渲染侧画完后清脏行；不清会让下一帧把同一批行再画一遍。

        通常在整帧绘制结束后调用一次，而不是每画一行调一次。
        """
        self._dirty_rows.clear()

    # ── 内容读取（渲染侧用） ──────────────────────────────────────────

    def row_cells(self, index):
        """按渲染行号取单元格列表 [(字符, attrs), ...]。

        越界返回空列表而不是抛 IndexError：resize 与渲染是异步的，渲染侧
        拿着上一帧的行号来取是常态。
        """
        sb = len(self._scrollback)
        if index < 0 or index >= sb + self._rows:
            return []
        if index < sb:
            return self._thaw(self._scrollback[index])
        return self._screen[index - sb]

    def row_text(self, index, strip=True):
        """按渲染行号取纯文本（单测/日志用）；续格哨兵不产生字符。

        strip 默认去掉行尾补白：屏幕行按列宽补满空格，不裁剪会让所有
        断言都被行尾空格干扰。
        """
        cells = self.row_cells(index)
        text = "".join(ch for ch, _ in cells if ch != _WIDE_SENTINEL)
        return text.rstrip() if strip else text

    def row_width(self, index):
        """一行的显示宽度（列数），调试/断言用。

        宽字符占两格，所以它可能大于可见字符个数。
        """
        return len(self.row_cells(index))

    @staticmethod
    def _char_width(ch):
        """0=组合符/控制字符，1=半角，2=全角/宽（CJK、emoji）。

        用 east_asian_width 的 W/F 判定而不是硬编码表：emoji 与新增 CJK
        区段随 Unicode 版本增长，手写表很快过期。
        """
        if not ch or ch == _WIDE_SENTINEL:
            return 0
        if unicodedata.combining(ch):
            return 0
        return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1

    @staticmethod
    def _freeze(row):
        """把一行的单元格压成 (文本, 非默认属性区间)。

        文本里保留 ``_WIDE_SENTINEL``（\\x00）作为宽字符续格的标记，
        所以 ``_thaw`` 能原样还原列结构，回滚行不会丢宽字符的占位。
        """
        text = "".join(ch for ch, _ in row)
        runs = []
        start = None
        cur = DEFAULT_ATTRS
        for i, (_, attrs) in enumerate(row):
            if attrs != cur:
                if cur != DEFAULT_ATTRS:
                    runs.append((start, i, cur))
                start = i if attrs != DEFAULT_ATTRS else None
                cur = attrs
        if cur != DEFAULT_ATTRS:
            runs.append((start, len(row), cur))
        return text, tuple(runs)

    @staticmethod
    def _thaw(frozen):
        """``_freeze`` 的逆操作：还原成 [(字符, attrs), ...]。

        runs 只记录非默认属性区间，其余格靠默认值补齐；这样 2000 行回滚
        只存少量 run，不必每格都带一份 attrs。
        """
        text, runs = frozen
        cells = [(ch, DEFAULT_ATTRS) for ch in text]
        for start, end, attrs in runs:
            for i in range(start, min(end, len(cells))):
                cells[i] = (cells[i][0], attrs)
        return cells

    # ── 尺寸 ──────────────────────────────────────────────────────────

    def resize(self, cols, rows):
        """改网格尺寸：补/裁单元格与行，夹住光标、滚动区、制表位。

        原地改而不是新建实例：渲染侧持有同一个对象，换实例会让脏行、
        revision 全部对不上，等于每次拖窗口都整屏重画。
        """
        cols = max(1, int(cols))
        rows = max(1, int(rows))
        if cols == self._cols and rows == self._rows:
            return
        self._cols, self._rows = cols, rows
        for row in self._screen:
            if len(row) < cols:
                row.extend((" ", DEFAULT_ATTRS) for _ in range(cols - len(row)))
            elif len(row) > cols:
                del row[cols:]
        while len(self._screen) < rows:
            self._screen.append(self._blank_row())
        if len(self._screen) > rows:
            del self._screen[rows:]
        # 变窄后最后一格可能只剩宽字符的左半，补成空格免得渲染成单宽字形
        for row in self._screen:
            if row and self._char_width(row[-1][0]) == 2:
                row[-1] = (" ", DEFAULT_ATTRS)
        self._scroll_top = 0
        self._scroll_bottom = rows - 1
        self._tab_stops = self._default_tab_stops()
        self._wrap_pending = False
        self._cursor_row = min(self._cursor_row, rows - 1)
        self._cursor_col = self._snap_col(self._cursor_row,
                                          min(self._cursor_col, cols - 1))
        self._touch_structure()

    # ── 主入口：喂数据 ────────────────────────────────────────────────

    def feed(self, text):
        """解析并应用一段终端输出（可跨调用切断转义序列，状态保留）。

        逐字符喂入而不是按行/按序列切分：SSH 分包的边界随时可能落在
        转义序列中间，按块解析会漏掉一半序列。
        """
        if not text:
            return
        for ch in text:
            self._feed_char(ch)

    def _feed_char(self, ch):
        """状态机分派；状态存在实例上，所以序列被分包切断也能续上。

        五种状态互斥，用 if/elif 而不是字典是因为分支体都很短。
        """
        state = self._state
        if state == "normal":
            self._handle_normal(ch)
        elif state == "esc":
            self._handle_esc(ch)
        elif state == "csi":
            self._handle_csi_char(ch)
        elif state == "osc":
            self._handle_osc_char(ch)
        elif state == "osc_esc":
            self._handle_osc_esc(ch)
        elif state == "charset":
            self._handle_charset_designator(ch)

    # ── 普通态 / 控制字符 ─────────────────────────────────────────────

    def _handle_normal(self, ch):
        """普通可见字符与控制字符；C0 里只有少数几个有屏幕语义。

        \\n 只下移不清列，\\r 才回行首，这是 xterm 的实际语义。
        """
        code = ord(ch)
        if ch == "\x1b":
            self._state = "esc"
        elif ch == "\n" or ch == "\x0b" or ch == "\x0c":
            self._index()
            self._set_cursor(self._cursor_row, self._cursor_col)  # 换行不清列（xterm 语义）
        elif ch == "\r":
            self._set_cursor(self._cursor_row, 0)
        elif ch == "\b":
            if self._cursor_col > 0:
                self._set_cursor(self._cursor_row, self._cursor_col - 1)
        elif ch == "\t":
            self._tab_forward(1)
        elif ch == "\x0e":                   # SO：切到 G1 字符集
            self._gl = 1
            self._sync_charset()
        elif ch == "\x0f":                   # SI：切回 G0
            self._gl = 0
            self._sync_charset()
        elif ch == "\x07":
            pass  # BEL 忽略（不响铃）
        elif code < 0x20 or code == 0x7F:
            pass  # 其他 C0 / DEL 忽略
        else:
            self._put_char(ch)

    # ── ESC 态 ────────────────────────────────────────────────────────

    def _handle_esc(self, ch):
        """ESC 后首字节决定序列种类；未知序列整体丢弃，绝不落到屏幕上。

        旧实现把未知序列的首字节当可见字符输出，屏幕因此多出字母。
        """
        self._state = "normal"
        if ch == "[":
            self._state = "csi"
            self._params = ""
            self._inter = ""
        elif ch == "]":
            self._state = "osc"
            self._osc = []
        elif ch in "()*+-./":
            self._state = "charset"
            self._charset_kind = ch
        elif ch == "7":                      # DECSC 保存光标
            self._save_cursor()
        elif ch == "8":                      # DECRC 恢复光标
            self._restore_cursor()
        elif ch == "D":                      # IND 下移一行
            self._index()
        elif ch == "E":                      # NEL 下移一行并回到行首
            self._index()
            self._set_cursor(self._cursor_row, 0)
        elif ch == "M":                      # RI 上移一行
            self._reverse_index()
        elif ch == "H":                      # HTS 设置制表位
            self._tab_stops.add(self._cursor_col)
        elif ch == "c":                      # RIS 终端复位
            self.reset()
        elif ch in "=>":                     # 键盘模式，忽略
            pass
        # 其余（含 \\E(B 之外的未知序列）安全忽略

    def _handle_charset_designator(self, ch):
        """``\\E(0``/``\\E(B`` 设计 G0，``\\E)0``/``\\E)B`` 设计 G1；取哪个由 SO/SI 决定。

        注意 ``)`` 是 G1 而不是 G0：旧版把两者混为一谈，遇到 ``\\E)0`` 会把后面的
        纯 ASCII 文本整体画成线框字符（比"不画线框"更难看）。
        未被识别的设计符：只消费，绝不把该字节落到屏幕上（旧实现漏出 'B'）。
        """
        self._state = "normal"
        if self._charset_kind == "(":
            self._g0_acs = (ch == "0")
        elif self._charset_kind == ")":
            self._g1_acs = (ch == "0")
        self._sync_charset()

    def _sync_charset(self):
        """当前生效的字符集是否为 ACS（由 GL 指向的那个集合决定）。

        SO/SI 与设计符都会触发重算，避免两处各存一份 ACS 状态而对不上。
        """
        self._acs = self._g1_acs if self._gl else self._g0_acs

    # ── CSI ───────────────────────────────────────────────────────────

    def _handle_csi_char(self, ch):
        """CSI 内按字节区间分三段：参数字节、中间字节、终止字节。

        只有终止字节到了才算一条完整序列，否则继续累积；这也是分包安全
        的前提，缺一段就提前执行会得到错误的坐标。
        """
        code = ord(ch)
        if 0x30 <= code <= 0x3F:            # 参数字节（含 ? > = 私有前缀）
            self._params += ch
            if len(self._params) > _MAX_CSI:
                self._state = "normal"
            return
        if 0x20 <= code <= 0x2F:            # 中间字节（如 \\E[!p）
            self._inter += ch
            return
        if 0x40 <= code <= 0x7E:            # 终止字节：只有到这里才算一条完整序列
            self._state = "normal"
            try:
                self._dispatch_csi(ch)
            finally:
                self._params = ""
                self._inter = ""
            return
        # 控制字符混进 CSI：按规范执行并继续留在 CSI 态
        if ch == "\x1b":
            self._state = "esc"
        elif ch in ("\n", "\x0b", "\x0c"):
            self._index()
        elif ch == "\r":
            self._set_cursor(self._cursor_row, 0)

    def _int_params(self):
        """把参数字节串转成整数列表：去私有前缀，空段按 0 处理。

        非数字一律当 0 而不是让整条序列失败：畸形输出很常见，整条丢弃
        会让后面的定位全部错位，宽容解析至少能画对一半。
        """
        body = self._params
        if body[:1] in ("?", ">", "="):
            body = body[1:]
        if not body:
            return []
        out = []
        for part in body.replace(":", ";").split(";"):
            try:
                out.append(int(part))
            except ValueError:
                out.append(0)
        return out

    def _dispatch_csi(self, final):
        """按终止字节分派 CSI 功能；私有前缀优先分派给模式设置。

        顺序有讲究：带 ?/>/= 前缀的参数不是普通数值，先拦下来，否则
        "?25" 会被当成数值 25 去执行某个绝对定位。
        """
        priv = self._params[:1] if self._params[:1] in ("?", ">", "=") else ""
        if self._inter:
            if final == "p" and "!" in self._inter:
                self._soft_reset()
            return
        nums = self._int_params()
        if priv == "?":
            self._set_modes(nums, final == "h")
            return
        if priv:
            return  # DEC 私有查询（\\E[>...），忽略

        def p(i, default=1):
            return nums[i] if len(nums) > i and nums[i] else default

        if final in ("H", "f"):                       # CUP / HVP 绝对定位
            self._goto(p(0) - 1, p(1) - 1)
        elif final == "A":                            # CUU
            self._cursor_up(p(0))
        elif final == "B":                            # CUD
            self._cursor_down(p(0))
        elif final == "C":                            # CUF
            self._set_cursor(self._cursor_row, self._cursor_col + p(0))
        elif final == "D":                            # CUB
            self._set_cursor(self._cursor_row, self._cursor_col - p(0))
        elif final == "E":                            # CNL 下移 n 行到行首
            self._cursor_down(p(0))
            self._set_cursor(self._cursor_row, 0)
        elif final == "F":                            # CPL 上移 n 行到行首
            self._cursor_up(p(0))
            self._set_cursor(self._cursor_row, 0)
        elif final in ("G", "`"):                     # CHA / HPA 绝对列
            self._set_cursor(self._cursor_row, p(0) - 1)
        elif final == "d":                            # VPA 绝对行（ncurses 高频使用）
            self._goto(p(0) - 1, self._cursor_col)
        elif final == "e":                            # VPR 相对下移
            self._cursor_down(p(0))
        elif final == "I":                            # CHT 前进 n 个制表位
            self._tab_forward(p(0))
        elif final == "Z":                            # CBT 后退 n 个制表位
            self._tab_backward(p(0))
        elif final == "J":                            # ED 屏幕擦除
            self._erase_display(nums[0] if nums else 0)
        elif final == "K":                            # EL 行擦除
            self._erase_line(nums[0] if nums else 0)
        elif final == "L":                            # IL 插入行
            self._insert_lines(p(0))
        elif final == "M":                            # DL 删除行
            self._delete_lines(p(0))
        elif final == "P":                            # DCH 删除字符
            self._delete_chars(p(0))
        elif final == "@":                            # ICH 插入字符
            self._insert_chars(p(0))
        elif final == "X":                            # ECH 擦除字符
            self._erase_chars(p(0))
        elif final == "S":                            # SU 区域内上滚
            self._scroll_up(p(0))
        elif final == "T":                            # SD 区域内下滚
            self._scroll_down(p(0))
        elif final == "r":                            # DECSTBM 滚动区域
            self._set_scroll_region(nums)
        elif final == "m":                            # SGR
            self._handle_sgr(nums if nums else [0])
        elif final == "s":                            # SCOSC 保存光标
            self._save_cursor()
        elif final == "u":                            # SCORC 恢复光标
            self._restore_cursor()
        elif final == "h":                            # SM 非私有模式（IRM 等）
            self._set_modes(nums, True)
        elif final == "l":
            self._set_modes(nums, False)
        elif final == "b":                            # REP 重复上一字符
            if self._last_char:
                for _ in range(min(p(0), self._cols)):
                    self._put_char(self._last_char)
        elif final == "g":                            # TBC 清制表位
            mode = nums[0] if nums else 0
            if mode == 3:
                self._tab_stops.clear()
            else:
                self._tab_stops.discard(self._cursor_col)
        # 其余（n/c/q/t/_ 等查询与忽略项）安全丢弃

    # ── 光标移动 ──────────────────────────────────────────────────────

    def _goto(self, row, col):
        """绝对定位；原点模式下相对滚动区上边。

        越界由 _set_cursor 统一夹紧，这里不重复判断。
        """
        if self._origin_mode:
            row += self._scroll_top
        self._set_cursor(row, col)

    def _set_cursor(self, row, col, mark=True):
        """定位光标：先夹进屏幕范围，再吸附到字形起点，最后标脏两行。

        mark=False 用于写入路径内部的重定位：那里随后会自己标脏，
        提前标一次只会让同一行入脏集合两次。
        """
        row = max(0, min(self._rows - 1, int(row)))
        col = max(0, min(self._cols - 1, int(col)))
        col = self._snap_col(row, col)     # 不许停在宽字符的右半格上
        if mark and (row != self._cursor_row or col != self._cursor_col):
            self._touch_row(self._cursor_row)
            self._touch_row(row)
        self._cursor_row, self._cursor_col = row, col
        self._wrap_pending = False

    def _snap_col(self, row, col):
        """把列号吸附到宽字符的左半格（真实终端光标也只落在字形起点）。

        只有右半格需要回退，左半格本身就是合法落点。
        """
        cells = self._screen[row]
        if 0 <= col < len(cells) and cells[col][0] == _WIDE_SENTINEL and col > 0:
            return col - 1
        return col

    def _in_region(self):
        """光标是否落在滚动区内；区外时纵向移动受屏幕边界约束而不是区域边界。

        插入/删除行与 CUU/CUD 都靠它决定边界该取哪一个。
        """
        return self._scroll_top <= self._cursor_row <= self._scroll_bottom

    def _cursor_up(self, n):
        """CUU：区内移动不越过滚动区上边（DEC 语义），区外只受屏幕边界约束。

        用 max(1, n) 是因为 ANSI 里参数缺省等价于 1。
        """
        row = self._cursor_row - max(1, n)
        floor = self._scroll_top if self._in_region() else 0
        self._set_cursor(max(floor, row), self._cursor_col)

    def _cursor_down(self, n):
        """CUD：同 CUU，区内不越过滚动区下边。

        参数缺省同样按 1 处理。
        """
        row = self._cursor_row + max(1, n)
        ceil = self._scroll_bottom if self._in_region() else self._rows - 1
        self._set_cursor(min(ceil, row), self._cursor_col)

    def _move_rows(self, delta):
        """纵向相对移动（列不变），两端的行都要重画（光标方块会动）。

        不经过 _set_cursor：纵向移动不改列，无需再做宽字符吸附。
        """
        old = self._cursor_row
        self._cursor_row = max(0, min(self._rows - 1, old + delta))
        self._wrap_pending = False
        self._touch_row(old)
        self._touch_row(self._cursor_row)

    def _index(self):
        """LF / IND：区域内到底就上滚，否则下移一行（列不变）。

        真正滚屏只发生在滚动区下边界，中间位置只是移动光标。
        """
        if self._cursor_row == self._scroll_bottom:
            self._scroll_up(1)
        elif self._cursor_row < self._rows - 1:
            self._move_rows(1)

    def _reverse_index(self):
        """RI 反向索引：在滚动区顶就整体下滚一行，否则光标上移。

        光标在区外时只做普通上移，不会触发滚屏。
        """
        if self._cursor_row == self._scroll_top:
            self._scroll_down(1)
        elif self._cursor_row > 0:
            self._move_rows(-1)

    def _tab_forward(self, n):
        """CHT/TAB：向右找第 n 个制表位，越界停在最后一列。

        没有后续制表位时停在 cols-1 而不是绕回行首：ncurses 依赖这一点
        把长行末尾的内容对齐，绕回会让表格右边界重叠到左侧。
        """
        col = self._cursor_col
        for _ in range(max(1, n)):
            nxt = [s for s in sorted(self._tab_stops) if s > col]
            col = nxt[0] if nxt else self._cols - 1
        self._set_cursor(self._cursor_row, col)

    def _tab_backward(self, n):
        """CBT：向左找第 n 个制表位，没有更前面的就停在行首。

        制表位可能被 TBC 清空，此时一路退到第 0 列。
        """
        col = self._cursor_col
        for _ in range(max(1, n)):
            prev = [s for s in sorted(self._tab_stops) if s < col]
            col = prev[-1] if prev else 0
        self._set_cursor(self._cursor_row, col)

    # ── 擦除 ──────────────────────────────────────────────────────────

    def _erase_line(self, mode):
        row = self._screen[self._cursor_row]
        # mode 1 要连光标位一起擦（DEC 规范），写成 range(col) 会留一个字符，
        # 表现为退格重绘后行尾多一个旧字
        if mode == 0:
            rng = range(self._cursor_col, self._cols)
        elif mode == 1:
            rng = range(0, min(self._cursor_col + 1, self._cols))  # 含光标位（DEC 规范）
        else:
            rng = range(0, self._cols)
        for i in rng:
            self._clear_cell(row, i)
        self._touch_row(self._cursor_row)

    def _erase_display(self, mode):
        """ED 屏幕擦除：mode 2 只擦屏，mode 3 连回滚一起清（结构变化）。

        mode 0/1 只擦一部分，按光标所在行分两段处理。
        """
        if mode in (2, 3):
            for r in range(self._rows):
                self._screen[r] = self._blank_row()
            if mode == 3:
                self._scrollback.clear()
                self._touch_structure()
            self._mark_all_rows()
            return
        if mode == 0:                                   # 光标到屏尾
            self._erase_line(0)
            for r in range(self._cursor_row + 1, self._rows):
                self._screen[r] = self._blank_row()
            self._mark_all_rows()
        elif mode == 1:                                 # 屏首到光标
            for r in range(0, self._cursor_row):
                self._screen[r] = self._blank_row()
            self._erase_line(1)
            self._mark_all_rows()

    def _erase_chars(self, n):
        """ECH：从光标起擦 n 格，光标不动（与 DCH 的区别就在这里）。

        n 超过行尾时夹到列宽，不会越界改到下一行。
        """
        row = self._screen[self._cursor_row]
        end = min(self._cols, self._cursor_col + n)
        for i in range(self._cursor_col, end):
            self._clear_cell(row, i)
        self._touch_row(self._cursor_row)

    def _clear_cell(self, row, i):
        """擦掉一格；若该格是宽字符的一半，连带擦掉另一半（否则留下半个字）。

        擦除必须成对处理，否则剩下的一半会渲染成错位的方块。
        """
        ch = row[i][0]
        if ch == _WIDE_SENTINEL:
            if i > 0:
                row[i - 1] = (" ", DEFAULT_ATTRS)
            row[i] = (" ", DEFAULT_ATTRS)
        elif i + 1 < len(row) and row[i + 1][0] == _WIDE_SENTINEL:
            row[i] = (" ", DEFAULT_ATTRS)
            row[i + 1] = (" ", DEFAULT_ATTRS)
        else:
            row[i] = (" ", DEFAULT_ATTRS)

    # ── 行/字符插入删除与滚屏 ─────────────────────────────────────────

    def _insert_lines(self, n):
        """IL 插入行：只在滚动区内生效，区内末尾的 n 行被挤掉。

        光标在区外时直接忽略：此时没有明确的插入范围，猜一个范围会让
        top/less 刷新时凭空多出空行。
        """
        if not self._in_region():
            return
        top, bottom = self._cursor_row, self._scroll_bottom
        height = bottom - top + 1
        n = max(1, min(n, height))
        region = self._screen[top:bottom + 1]
        self._screen[top:bottom + 1] = ([self._blank_row() for _ in range(n)]
                                        + region[:height - n])
        for r in range(top, bottom + 1):
            self._touch_row(r)

    def _delete_lines(self, n):
        """DL 删除行：区内下方内容整体上移，底部补空白行。

        n 超过区域高度时按区域高度处理，避免切片下标越界。
        """
        if not self._in_region():
            return
        top, bottom = self._cursor_row, self._scroll_bottom
        height = bottom - top + 1
        n = max(1, min(n, height))
        region = self._screen[top:bottom + 1]
        self._screen[top:bottom + 1] = (region[n:] +
                                        [self._blank_row() for _ in range(n)])
        for r in range(top, bottom + 1):
            self._touch_row(r)

    def _insert_chars(self, n):
        """ICH 插入字符：整行右移，超出列宽的部分丢弃（真实终端也不折行）。

        插入后行长度仍等于列宽，多出的字符在行尾被截断。
        """
        row = self._screen[self._cursor_row]
        # 插入点落在宽字符中间时先补平，免得留下半个字
        if 0 < self._cursor_col < self._cols and row[self._cursor_col][0] == _WIDE_SENTINEL:
            row[self._cursor_col - 1] = (" ", DEFAULT_ATTRS)
            row[self._cursor_col] = (" ", DEFAULT_ATTRS)
        n = max(1, min(n, self._cols - self._cursor_col))
        row[self._cursor_col:self._cursor_col] = [(" ", DEFAULT_ATTRS)] * n
        del row[self._cols:]
        self._touch_row(self._cursor_row)

    def _delete_chars(self, n):
        """DCH 删除字符：整行左移并在行尾补空格，光标保持不动。

        补空格而不是缩短列表，才能保持「格下标 == 列号」。
        """
        row = self._screen[self._cursor_row]
        n = max(1, min(n, self._cols - self._cursor_col))
        start, end = self._cursor_col, self._cursor_col + n
        # 与宽字符相交时整字删除，避免删掉一半
        if start > 0 and row[start][0] == _WIDE_SENTINEL:
            start -= 1
        if end < self._cols and row[end][0] == _WIDE_SENTINEL:
            end += 1
        del row[start:end]
        row.extend((" ", DEFAULT_ATTRS) for _ in range(self._cols - len(row)))
        self._touch_row(self._cursor_row)

    def _scroll_up(self, n):
        """区域内上滚 n 行；主屏整屏上滚时滚出的行进 scrollback。

        只有「主屏 + 全屏区域 + 允许回滚」三条同时成立才入回滚：局部滚动区
        是 vim 之类的自绘区域，把它滚出的内容存进回滚会污染命令行历史。
        """
        top, bottom = self._scroll_top, self._scroll_bottom
        height = bottom - top + 1
        n = max(1, min(n, height))
        region = self._screen[top:bottom + 1]
        dropped = region[:n]
        self._screen[top:bottom + 1] = region[n:] + [self._blank_row() for _ in range(n)]
        if (not self._alt_active and top == 0 and bottom == self._rows - 1
                and self._max_scrollback):
            for row in dropped:
                self._scrollback.append(self._freeze(row))
            while len(self._scrollback) > self._max_scrollback:
                self._scrollback.popleft()
            self._touch_structure()
        else:
            for r in range(top, bottom + 1):
                self._touch_row(r)

    def _scroll_down(self, n):
        """SD 区域内下滚：顶部补空白行，底部内容被挤掉（不进回滚）。

        与 SU 相反：这里滚出的内容直接丢弃，不产生回滚。
        """
        top, bottom = self._scroll_top, self._scroll_bottom
        height = bottom - top + 1
        n = max(1, min(n, height))
        region = self._screen[top:bottom + 1]
        self._screen[top:bottom + 1] = ([self._blank_row() for _ in range(n)]
                                        + region[:height - n])
        for r in range(top, bottom + 1):
            self._touch_row(r)

    def _set_scroll_region(self, nums):
        top = (nums[0] if len(nums) > 0 and nums[0] else 1) - 1
        bottom = (nums[1] if len(nums) > 1 and nums[1] else self._rows) - 1
        top = max(0, min(self._rows - 1, top))
        bottom = max(0, min(self._rows - 1, bottom))
        if top >= bottom:
            top, bottom = 0, self._rows - 1
        # 非法区间（top>=bottom）整体退回全屏：ncurses 偶尔发单行区域，
        # 保留它会让后续所有插入/删除行都变成空操作，整屏不再更新
        self._scroll_top, self._scroll_bottom = top, bottom
        # DEC/xterm：设置滚动区后光标回原点（受原点模式影响）
        self._set_cursor(self._scroll_top if self._origin_mode else 0, 0)

    # ── 模式与字符集 ──────────────────────────────────────────────────

    def _set_modes(self, nums, enable):
        """DEC 私有模式开关（?h/?l）；一条序列可带多个模式，逐个应用。

        未知编号直接忽略：不同终端实现的私有模式互不兼容，遇到不认识的
        编号就报错会让半屏内容卡住。
        """
        for m in nums:
            if m == 7:
                self._auto_wrap = enable
            elif m == 25:
                self._cursor_visible = enable
                self._revision += 1
            elif m == 6:
                self._origin_mode = enable
                self._set_cursor(self._scroll_top if enable else 0, 0)
            elif m == 4:
                self._insert_mode = enable
            elif m == 1:
                self._app_cursor_keys = enable
                self._revision += 1
            elif m == 2004:
                self._bracketed_paste = enable
                self._revision += 1
            elif m in (1000, 1002, 1003):
                if enable:
                    self._mouse_modes.add(m)
                else:
                    self._mouse_modes.discard(m)
                self._revision += 1
            elif m == 1006:
                self._mouse_sgr = enable
                self._revision += 1
            elif m == 1004:
                self._focus_events = enable
                self._revision += 1
            elif m == 1048:
                if enable:
                    self._save_cursor()
                else:
                    self._restore_cursor()
            elif m in (47, 1047, 1049):
                if enable:
                    self._enter_alt(save_cursor=(m == 1049))
                else:
                    self._leave_alt(restore_cursor=(m == 1049))
            # ?1(DECCKM)/?5/?12/?2004 等对渲染无影响，忽略

    def _save_cursor(self):
        """DECSC：连字符集与原点模式一起存，不只是坐标。

        nano/vim 的典型套路是「存光标 → 换成线框字符集 → 画框 → 恢复」，
        只存坐标会把线框字符集漏给后续正文。
        """
        self._saved_cursor = (self._cursor_row, self._cursor_col, self._attrs,
                              self._acs, self._origin_mode, self._gl, self._g0_acs,
                              self._g1_acs)

    def _restore_cursor(self):
        """DECRC：没有保存过就静默返回（部分程序会先恢复再保存）。

        恢复时仍要过一遍夹紧与吸附，保存的坐标可能已经超出当前尺寸。
        """
        if not self._saved_cursor:
            return
        (row, col, attrs, acs, origin, gl, g0, g1) = self._saved_cursor
        self._attrs = attrs
        self._acs = acs
        self._origin_mode = origin
        self._gl, self._g0_acs, self._g1_acs = gl, g0, g1
        self._set_cursor(row, col)

    def _enter_alt(self, save_cursor=True):
        """切备用屏幕：保存主屏对象引用，清空屏幕且不继承回滚。

        只有 1049 连光标一起存（vim/top 靠它退出后原位返回）；47/1047 不存，
        这是 xterm 的实际行为，改统一了反而会让部分程序回错位置。
        """
        if self._alt_active:
            return
        if save_cursor:
            self._save_cursor()
        self._primary = (self._screen, self._scrollback, self._cursor_row,
                         self._cursor_col, self._scroll_top, self._scroll_bottom)
        self._screen = [self._blank_row() for _ in range(self._rows)]
        self._scrollback = deque()
        self._scroll_top, self._scroll_bottom = 0, self._rows - 1
        self._alt_active = True
        self._cursor_row = self._cursor_col = 0
        self._wrap_pending = False
        self._touch_structure()

    def _leave_alt(self, restore_cursor=True):
        """退出备用屏幕：整体换回主屏（含其回滚与滚动区）。

        滚动区一并恢复，否则退出全屏应用后滚屏范围还停在旧值。
        """
        if not self._alt_active or self._primary is None:
            return
        (self._screen, self._scrollback, self._cursor_row, self._cursor_col,
         self._scroll_top, self._scroll_bottom) = self._primary
        self._primary = None
        self._alt_active = False
        self._wrap_pending = False
        self._touch_structure()
        if restore_cursor:
            self._restore_cursor()

    # ── 字符写入 ──────────────────────────────────────────────────────

    def _put_char(self, ch):
        """写入一个可见字符：先做字符集映射，再按宽度处理换行与占格。

        wrap_pending 是 xterm 的延迟换行：写满最后一列时光标不立刻跳到
        下一行，否则每写满一行都会平白多出一个空行。
        """
        if self._acs:
            ch = ACS_MAP.get(ch, ch)
        width = self._char_width(ch)
        if width == 0:
            # 组合符附着到前一格；其他零宽/控制字符丢弃
            if unicodedata.combining(ch):
                self._attach_combining(ch)
            return
        if self._wrap_pending and self._auto_wrap:
            self._set_cursor(self._cursor_row, 0, mark=False)
            self._index()
            self._wrap_pending = False
        if width == 2 and self._cursor_col >= self._cols - 1:
            # 宽字符放不下最后一列：xterm 语义是先换行；关自动换行时退回单格
            if self._auto_wrap:
                self._set_cursor(self._cursor_row, 0, mark=False)
                self._index()
                self._wrap_pending = False
            else:
                width = 1
        row = self._screen[self._cursor_row]
        col = self._cursor_col
        if self._insert_mode:
            row.insert(col, (ch, self._attrs))
            if width == 2:
                row.insert(col + 1, (_WIDE_SENTINEL, self._attrs))
            del row[self._cols:]
        else:
            self._clear_wide_at(row, col)
            row[col] = (ch, self._attrs)
            if width == 2 and col + 1 < self._cols:
                row[col + 1] = (_WIDE_SENTINEL, self._attrs)
        self._last_char = ch
        col += width
        if col >= self._cols:
            # 停在最后一列；开自动换行时挂起，下一个字符才换行（xterm 语义）
            self._cursor_col = self._cols - 1
            self._wrap_pending = self._auto_wrap
        else:
            self._cursor_col = col
        self._touch_row(self._cursor_row)

    def _clear_wide_at(self, row, col):
        """写入前清掉会被覆盖的半个宽字符。

        只清另一半、不整格重写：调用方紧接着就要写新字符，这里多写的
        空格会把刚算好的宽度信息盖掉。
        """
        if row[col][0] == _WIDE_SENTINEL and col > 0:
            row[col - 1] = (" ", DEFAULT_ATTRS)
            row[col] = (" ", DEFAULT_ATTRS)
        elif col + 1 < len(row) and row[col + 1][0] == _WIDE_SENTINEL:
            row[col + 1] = (" ", DEFAULT_ATTRS)

    def _attach_combining(self, ch):
        """把组合符并进前一格；光标在行首或前一格是宽字符续格时丢弃。

        丢弃而不是新开一格：组合符单独占格会显示成一个独立的怪符号。
        """
        row = self._screen[self._cursor_row]
        i = self._cursor_col - 1
        if i < 0 or row[i][0] == _WIDE_SENTINEL:
            return
        row[i] = (row[i][0] + ch, row[i][1])
        self._touch_row(self._cursor_row)

    # ── 属性（SGR） ───────────────────────────────────────────────────

    def _soft_reset(self):
        """DECSTR(\\E[!p)：复位属性与大部分模式（滚动区、字符集、自动换行都要回默认）。

        与 RIS 的区别：不清屏、不丢回滚，只复位模式与属性。
        """
        self._attrs = DEFAULT_ATTRS
        self._insert_mode = False
        self._origin_mode = False
        self._cursor_visible = True
        self._auto_wrap = True
        self._wrap_pending = False
        self._g0_acs = False
        self._g1_acs = False
        self._gl = 0
        self._sync_charset()
        self._scroll_top, self._scroll_bottom = 0, self._rows - 1
        self._revision += 1

    def _handle_sgr(self, params):
        """SGR：逐参数累加到局部变量，最后一次性写回 self._attrs。

        38/48 带 5;n 或 2;r;g;b 子参数，必须把游标跳过子参数，否则
        "38;5;196" 里的 5 与 196 会被当成两个独立属性误解析。
        """
        fg, bg, bold, underline, reverse = self._attrs
        i = 0
        while i < len(params):
            p = params[i]
            if p == 0:
                fg, bg, bold, underline, reverse = DEFAULT_ATTRS
            elif p == 1:
                bold = True
            elif p == 22:
                bold = False
            elif p == 4:
                underline = True
            elif p == 24:
                underline = False
            elif p == 7:
                reverse = True
            elif p == 27:
                reverse = False
            elif 30 <= p <= 37:
                fg = COLORS[p - 30]
            elif p == 38:
                if i + 1 < len(params) and params[i + 1] == 5 and i + 2 < len(params):
                    fg = self._color_256(params[i + 2])
                    i += 2
                elif i + 1 < len(params) and params[i + 1] == 2 and i + 4 < len(params):
                    r, g, b = params[i + 2], params[i + 3], params[i + 4]
                    fg = f"#{r:02x}{g:02x}{b:02x}"
                    i += 4
            elif p == 39:
                fg = DEFAULT_FG
            elif 40 <= p <= 47:
                bg = COLORS[p - 40]
            elif p == 48:
                if i + 1 < len(params) and params[i + 1] == 5 and i + 2 < len(params):
                    bg = self._color_256(params[i + 2])
                    i += 2
                elif i + 1 < len(params) and params[i + 1] == 2 and i + 4 < len(params):
                    r, g, b = params[i + 2], params[i + 3], params[i + 4]
                    bg = f"#{r:02x}{g:02x}{b:02x}"
                    i += 4
            elif p == 49:
                bg = DEFAULT_BG
            elif 90 <= p <= 97:
                fg = COLORS[p - 90 + 8]
            elif 100 <= p <= 107:
                bg = COLORS[p - 100 + 8]
            i += 1
        self._attrs = (fg, bg, bold, underline, reverse)

    @staticmethod
    def _color_256(n):
        """256 色索引 → hex：16 基色 + 6×6×6 色立方 + 24 级灰阶三段。

        三个区间公式不同，混用会让 vim 的 256 色主题整体偏色；
        立方段每通道只有 6 档，步长固定 51。
        """
        n = max(0, min(255, int(n)))
        if n < 16:
            return COLORS[n]
        if n < 232:
            n -= 16
            b = (n % 6) * 51
            g = ((n // 6) % 6) * 51
            r = (n // 36) * 51
            return f"#{r:02x}{g:02x}{b:02x}"
        v = 8 + (n - 232) * 10
        return f"#{v:02x}{v:02x}{v:02x}"

    # ── OSC ───────────────────────────────────────────────────────────

    def _handle_osc_char(self, ch):
        """OSC 内容累积；BEL 或 ESC 都表示结束。

        超长时置溢出标记后立即收尾，不再继续累积字节。
        """
        if ch == "\x07":                         # BEL 结束
            self._finish_osc()
            return
        if ch == "\x1b":
            self._osc.append("\x1b")
            self._state = "osc_esc"
            return
        self._osc.append(ch)
        if len(self._osc) > _MAX_OSC:
            # 超长序列按截断丢弃：截断的 OSC 7 会给出错误路径，宁可不认
            self._osc_overflow = True
            self._finish_osc()

    def _handle_osc_esc(self, ch):
        """OSC 内部的 ESC：``\\`` 是 ST 结束，否则退回 OSC 内容。

        ESC 后跟的不是反斜杠，说明是嵌套序列而不是结束符。
        """
        if ch == "\\":
            self._finish_osc()
            return
        self._osc.append(ch)
        self._state = "osc"

    def _finish_osc(self):
        """收尾一条 OSC：溢出过就整条丢弃，半截路径比没有路径更糟。

        只有 OSC 7 有副作用，标题/超链接一类直接消费掉。
        """
        # 标题/超链接等 OSC 对屏幕无影响，仅消费掉（含 sgr0 里的 \\E(B 之外的转义）
        payload = "".join(self._osc).rstrip("\x1b")
        overflow = self._osc_overflow
        self._osc = []
        self._osc_overflow = False
        self._state = "normal"
        if payload and not overflow:
            self._consume_osc(payload)

    def _consume_osc(self, payload):
        """识别有副作用的 OSC；其余（标题/超链接/改色）一律丢弃，屏幕不受影响。

        目前只处理 OSC 7（工作目录上报），用于状态条显示远端 cwd 与
        "在 SFTP 中打开此目录"这类联动。
        """
        if payload.startswith("7;"):
            self._apply_osc_cwd(payload[2:])

    def _apply_osc_cwd(self, uri):
        """``OSC 7 ; file://HOST/PATH ST`` → 记录远端工作目录。

        PATH 是百分号编码，中文目录不解码会让后续「在 SFTP 中打开」找不到
        路径；格式不符的一律忽略，避免把畸形数据写进状态条。
        """
        uri = uri.strip()
        if not uri.startswith("file://"):
            return                                  # 非标准写法（如直接给路径）忽略
        rest = uri[len("file://"):]
        host, sep, path = rest.partition("/")
        if not sep or not path:
            return
        # partition 吃掉了分隔符，这里补回绝对路径的开头
        path = "/" + _url_unquote(path)
        if len(path) > _MAX_OSC_PATH:
            return
        if path == self._cwd and host == self._cwd_host:
            return
        self._cwd = path
        self._cwd_host = host
        # cwd 不影响屏幕内容，但要让渲染层有机会把变化传给状态条
        self._revision += 1

    # ── 脏行/结构记账 ─────────────────────────────────────────────────

    def _touch_row(self, row):
        """标记单行变脏；行号越界只加 revision，不塞进脏行集合。

        越界行号只可能来自 resize 竞态，静默忽略即可。
        """
        if 0 <= row < self._rows:
            self._dirty_rows.add(row)
        self._revision += 1

    def _mark_all_rows(self):
        """整屏标脏（清屏、整屏擦除之后用）。

        清屏后所有行都变了，逐行标脏不如整体标一次。
        """
        self._dirty_rows.update(range(self._rows))
        self._revision += 1

    def _touch_structure(self):
        """结构变化：回滚行数或行数本身变了，脏行集合已无意义，直接清空。

        旧行号已对应不到新内容，增量重建失效，必须整表重建。
        """
        self._structure_revision += 1
        self._revision += 1
        self._dirty_rows.clear()
