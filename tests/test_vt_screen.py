# -*- coding: utf-8 -*-
"""``core.vt_screen``（VT100/xterm 屏幕模型）离线单测。

回归目标（2026-10-04 用户反馈「SSH 里进任何编辑器 / CLI 全屏界面 UI 就错乱」）：
1. 行定位必须支持 ncurses 真正在用的 ``\\E[<n>d``（VPA）等序列，否则多行叠成一行——
   真实案例：nano 首屏发来 30 行正文，旧实现只显示出 1 行；
2. ``\\E(0`` / ``\\E(B`` 字符集设计符必须整体消费，不能把第三个字节 ``B`` 落到屏幕上；
3. 终止字节不是字母的 CSI（``\\E[1P`` / ``\\E[@`` / ``\\E[2X``）必须正常收尾，
   旧实现会一直吃到 64 字节，把后面的正文一起吞掉。

运行：E:\\ANACONDA\\python.exe -m pytest tests/test_vt_screen.py -q
"""
from core.vt_screen import COLORS, DEFAULT_ATTRS, VTScreen


def make(rows=6, cols=20, data=""):
    screen = VTScreen(cols=cols, rows=rows)
    if data:
        screen.feed(data)
    return screen


def lines(screen):
    return [screen.row_text(i) for i in range(screen.total_rows())]


# ── 1. 行定位 ────────────────────────────────────────────────────────────

def test_vpa_moves_cursor_to_row():
    """\\E[<n>d（VPA）是 ncurses 纯纵向移动的首选序列，必须支持。"""
    screen = make(data="A\r\x1b[3dB")
    assert lines(screen)[:3] == ["A", "", "B"]
    assert screen.cursor_row == 2


def test_vpa_and_cup_agree():
    vpa = make(data="A\r\x1b[3dB")
    cup = make(data="A\r\x1b[3;1HB")
    assert lines(vpa) == lines(cup)
    assert (vpa.cursor_row, vpa.cursor_col) == (cup.cursor_row, cup.cursor_col)


def test_vpa_default_and_zero_param():
    assert make(data="X\x1b[d").cursor_row == 0      # 无参数 → 第 1 行
    assert make(data="X\x1b[0d").cursor_row == 0     # 0 等价默认
    assert make(data="X\x1b[99d").cursor_row == 5    # 越界夹住


def test_nel_and_cnl_move_to_column_zero():
    nel = make(data="ab\x1bEX")
    assert lines(nel)[1] == "X" and nel.cursor_col == 1
    cnl = make(data="ab\x1b[2EX")
    assert lines(cnl)[2] == "X"


def test_relative_moves_and_clamping():
    screen = make(rows=4, cols=10, data="\x1b[2;3HX")
    assert (screen.cursor_row, screen.cursor_col) == (1, 3)
    screen.feed("\x1b[9A\x1b[9D")
    assert (screen.cursor_row, screen.cursor_col) == (0, 0)
    screen.feed("\x1b[9B\x1b[9C")
    assert (screen.cursor_row, screen.cursor_col) == (3, 9)


# ── 2. CSI 收尾与字符集设计符 ────────────────────────────────────────────

def test_non_alpha_final_byte_terminates_csi():
    """\\E[1P / \\E[@ / \\E[2X 的终止字节不是字母，不能被当成未结束序列。"""
    for seq in ("\x1b[1P", "\x1b[1@", "\x1b[2X"):
        screen = make(cols=10, data=seq + "XYZ")
        assert screen.row_text(0) == "XYZ", seq
        assert screen.row_text(0)[:3] == "XYZ"


def test_charset_designator_is_consumed_without_leaking():
    """sgr0 = \\E(B\\E[m：旧实现把 'B' 当普通字符画了出来（用户截图每行一个 B）。"""
    assert make(data="\x1b(Babc").row_text(0) == "abc"
    assert make(data="\x1b(B\x1b[mabc").row_text(0) == "abc"


def test_acs_line_drawing_characters():
    """\\E(0 之后的 q/x/l/k 要画成线框字符，而不是字母。"""
    screen = make(cols=10, data="\x1b(0lqk\x1b(B abc")
    assert screen.row_text(0) == "┌─┐ abc"


def test_g1_charset_designator_does_not_switch_g0():
    """\\E)0 设计的是 G1；把它当 G0 会把后面的纯 ASCII 文本整体画成线框字。"""
    assert make(cols=20, data="\x1b)0file.txt").row_text(0) == "file.txt"


def test_so_si_switch_between_g0_and_g1():
    screen = make(cols=12, data="\x1b)0\x0elq\x0fAB")
    assert screen.row_text(0) == "┌─AB"


# ── 2b. 宽字符（CJK / emoji 占两列） ────────────────────────────────────

def test_wide_char_advances_two_columns():
    screen = make(cols=12, data="\x1b[1;3H中")            # 从第 3 列开始
    cells = screen.row_cells(0)
    assert cells[2][0] == "中" and cells[3][0] == "\x00"  # 第二格是续格哨兵
    assert screen.cursor_col == 4                         # 占两列
    assert len(cells) == 12                               # 行长度恒等于 cols
    assert screen.row_text(0) == "  中"


def test_wide_char_wraps_at_last_column():
    """宽字符放不进最后一列时先换行（xterm 语义）。"""
    screen = make(rows=3, cols=4, data="\x1b[1;4H中")
    assert lines(screen)[:2] == ["", "中"]
    assert screen.cursor_col == 2


def test_wide_char_erase_removes_both_halves():
    assert make(cols=12, data="中文字\x1b[1;2H\x1b[1X").row_text(0) == "  文字"
    assert make(cols=12, data="中文字\x1b[1;1H\x1b[1X").row_text(0) == "  文字"


def test_wide_char_survives_scrollback_roundtrip():
    screen = make(rows=2, cols=8, data="中文\r\nab\r\ncd")
    assert screen.scrollback_rows == 1
    assert screen.row_text(0) == "中文"                    # 回滚行里的宽字符列结构保真
    assert screen.row_text(2) == "cd"


def test_combining_char_attaches_previous_cell():
    screen = make(cols=8, data="e\u0301x")
    assert screen.row_cells(0)[0][0] == "e\u0301"
    assert screen.row_text(0) == "e\u0301x"
    assert screen.cursor_col == 2


# ── 2c. 输入侧模式跟踪（控件按它选键序列 / 是否上报鼠标） ───────────────

def test_input_modes_are_tracked():
    screen = make(cols=10)
    assert (screen.app_cursor_keys, screen.bracketed_paste,
            screen.mouse_mode, screen.mouse_sgr) == (False, False, 0, False)
    screen.feed("\x1b[?1h\x1b[?2004h\x1b[?1000h\x1b[?1006h")
    assert screen.app_cursor_keys and screen.bracketed_paste
    assert screen.mouse_mode == 1000 and screen.mouse_sgr
    screen.feed("\x1b[?1002h")
    assert screen.mouse_mode == 1002                      # 拖动优先于点击
    screen.feed("\x1b[?1000l")
    assert screen.mouse_mode == 1002
    screen.feed("\x1b[?1002l\x1b[?1l\x1b[?2004l\x1b[?1006l")
    assert (screen.app_cursor_keys, screen.bracketed_paste,
            screen.mouse_mode, screen.mouse_sgr) == (False, False, 0, False)


def test_soft_reset_restores_modes_and_region():
    screen = make(rows=6, cols=10, data="\x1b[?7l\x1b[2;4r\x1b(0\x1b[4h\x1b[!p")
    screen.feed("q")                                      # 字符集已回 ASCII
    assert screen.row_text(0) == "q"
    screen.feed("\x1b[3;1H\n")                            # 滚动区已复位 → 普通下移
    assert screen.cursor_row == 3 and screen.scrollback_rows == 0
    screen.feed("\x1b[2;1Habcdef\x1b[4h\x1b[!p\x1b[2;1HZ")
    assert screen.row_text(1) == "Zbcdef"                 # 插入模式也已复位


def test_partial_sequence_across_feeds():
    """转义序列被 recv 边界切断时状态要保留。"""
    screen = make(rows=4, cols=10)
    screen.feed("\x1b[")
    screen.feed("3d")
    screen.feed("X")
    assert screen.row_text(2) == "X"
    screen.feed("\x1b(")
    assert screen.row_text(2) == "X"       # 设计符未完成，不能落字符
    screen.feed("B")
    screen.feed("Y")
    assert screen.row_text(2) == "XY"


def test_osc_sequence_is_swallowed():
    screen = make(cols=20, data="\x1b]0;my title\x07ok")
    assert screen.row_text(0) == "ok"


# ── 3. 擦除 ──────────────────────────────────────────────────────────────

def test_erase_line_modes():
    base = "\x1b[3G"                     # 光标到第 3 列（1 基）
    assert make(cols=10, data="abcdef" + base + "\x1b[0K").row_text(0) == "ab"
    assert make(cols=10, data="abcdef" + base + "\x1b[1K").row_text(0) == "   def"
    assert make(cols=10, data="abcdef" + base + "\x1b[2K").row_text(0) == ""


def test_erase_display_modes():
    fill = "\x1b[1;1Hr0\x1b[2;1Habcdef\x1b[3;1Hr2"
    to_mode0 = make(rows=4, cols=8, data=fill + "\x1b[2;3H\x1b[0J")
    assert lines(to_mode0)[:4] == ["r0", "ab", "", ""]
    to_mode1 = make(rows=4, cols=8, data=fill + "\x1b[2;3H\x1b[1J")
    assert lines(to_mode1)[:4] == ["", "   def", "r2", ""]
    to_mode2 = make(rows=4, cols=8, data=fill + "\x1b[2J")
    assert lines(to_mode2)[:4] == ["", "", "", ""]


def test_erase_chars():
    assert make(cols=10, data="abcdef\x1b[3G\x1b[2X").row_text(0) == "ab  ef"


# ── 4. 行/字符插入删除、滚屏、滚动区域 ──────────────────────────────────

def test_insert_and_delete_lines_inside_region():
    fill = "".join(f"\x1b[{i + 1};1Hr{i}" for i in range(5))
    screen = make(rows=5, cols=8, data=fill + "\x1b[2;4r\x1b[2;1H\x1b[L")
    assert lines(screen)[:5] == ["r0", "", "r1", "r2", "r4"]
    screen.feed("\x1b[M")
    assert lines(screen)[:5] == ["r0", "r1", "r2", "", "r4"]


def test_insert_and_delete_chars():
    assert make(cols=12, data="abcdef\x1b[3G\x1b[2@").row_text(0) == "ab  cdef"
    assert make(cols=12, data="abcdef\x1b[3G\x1b[2P").row_text(0) == "abef"


def test_lf_inside_scroll_region_scrolls_only_region():
    fill = "".join(f"\x1b[{i + 1};1Hr{i}" for i in range(5))
    screen = make(rows=5, cols=8, data=fill + "\x1b[2;4r\x1b[4;1H\n")
    assert lines(screen)[:5] == ["r0", "r2", "r3", "", "r4"]
    assert screen.scrollback_rows == 0          # 区域内滚动不进回滚


def test_full_screen_scroll_pushes_scrollback():
    screen = make(rows=3, cols=8, data="1\r\n2\r\n3\r\n4\r\n5")
    assert lines(screen) == ["1", "2", "3", "4", "5"]
    assert screen.scrollback_rows == 2
    assert screen.row_text(2) == "3"            # 渲染行号跨回滚 + 屏幕


def test_su_and_sd_scroll_region():
    fill = "".join(f"\x1b[{i + 1};1Hr{i}" for i in range(4))
    su = make(rows=4, cols=8, data=fill + "\x1b[2;3r\x1b[S")
    assert lines(su)[:4] == ["r0", "r2", "", "r3"]
    sd = make(rows=4, cols=8, data=fill + "\x1b[2;3r\x1b[T")
    assert lines(sd)[:4] == ["r0", "", "r1", "r3"]


def test_decstbm_resets_cursor_home():
    screen = make(rows=6, cols=8, data="\x1b[3;1H\x1b[2;5r")
    assert (screen.cursor_row, screen.cursor_col) == (0, 0)
    screen.feed("\x1b[r")                       # 复位为整屏
    assert screen.cursor_row == 0


# ── 5. 换行、网格边界、备用屏幕 ─────────────────────────────────────────

def test_autowrap_and_wrap_pending():
    screen = make(rows=3, cols=4, data="abcd")
    assert screen.row_text(0) == "abcd" and screen.cursor_col == 3
    screen.feed("e")
    assert lines(screen)[:2] == ["abcd", "e"]


def test_autowrap_disabled_overwrites_last_column():
    screen = make(rows=3, cols=4, data="\x1b[?7labcdef")
    assert screen.row_text(0) == "abcf"


def test_rows_never_grow_past_grid():
    screen = make(rows=3, cols=5, data="abcdefghijkl")
    assert lines(screen) == ["abcde", "fghij", "kl"]
    assert all(len(screen.row_cells(i)) == 5 for i in range(3))


def test_alt_screen_preserves_primary_screen():
    screen = make(rows=4, cols=10, data="hello")
    screen.feed("\x1b[?1049h")
    assert screen.alt_screen and screen.row_text(0) == ""
    screen.feed("alt")
    assert screen.row_text(0) == "alt"
    screen.feed("\x1b[?1049l")
    assert not screen.alt_screen
    assert screen.row_text(0) == "hello"
    assert screen.cursor_col == 5
    assert screen.scrollback_rows == 0


def test_alt_screen_has_no_scrollback():
    screen = make(rows=2, cols=6, data="\x1b[?1049h1\r\n2\r\n3")
    assert screen.scrollback_rows == 0
    assert [screen.row_text(i) for i in range(2)] == ["2", "3"]


def test_resize_pads_and_truncates():
    screen = make(rows=2, cols=4, data="abcd\r\nef")
    screen.resize(8, 4)
    assert screen.cols == 8 and screen.rows == 4
    assert screen.row_text(0) == "abcd"
    assert screen.row_text(1) == "ef"
    screen.resize(3, 1)
    assert screen.cols == 3 and screen.rows == 1
    assert screen.row_text(0) == "abc"
    assert screen.cursor_row == 0


def test_clear_resets_screen_and_scrollback():
    screen = make(rows=3, cols=8, data="1\r\n2\r\n3\r\n4")
    assert screen.scrollback_rows == 1
    screen.clear()
    assert screen.scrollback_rows == 0
    assert lines(screen) == ["", "", ""]
    assert (screen.cursor_row, screen.cursor_col) == (0, 0)


def test_origin_mode_addresses_relative_to_region():
    screen = make(rows=6, cols=8, data="\x1b[3;5r\x1b[?6h\x1b[1;1H")
    assert screen.cursor_row == 2               # 区域内第 1 行 = 绝对第 3 行
    screen.feed("\x1b[?6l")
    assert screen.cursor_row == 0


# ── 6. 属性 ──────────────────────────────────────────────────────────────

def test_sgr_colors_bold_underline_reverse():
    screen = make(cols=10, data="\x1b[1;31;4;7mX")
    ch, attrs = screen.row_cells(0)[0]
    assert ch == "X"
    assert attrs == (COLORS[1], "default", True, True, True)
    screen.feed("\x1b[0mY")
    assert screen.row_cells(0)[1][1] == DEFAULT_ATTRS


def test_sgr_256_and_truecolor():
    screen = make(cols=10, data="\x1b[38;5;196mX\x1b[38;2;10;20;30mY")
    assert screen.row_cells(0)[0][1][0] == "#ff0000"
    assert screen.row_cells(0)[1][1][0] == "#0a141e"


def test_insert_mode_shifts_text():
    screen = make(cols=10, data="abcdef\x1b[4h\x1b[3GZ")
    assert screen.row_text(0) == "abZcdef"


# ── 7. 真实案例：nano 首屏（文本取自用户那次会话的日志） ────────────────

# logs/ssh_sessions/20261004_201221_49.235.34.253.log 里 nano 首屏真正发过来的正文，
# 顺序与内容一字不改（会话记录器剥掉了 CSI，但文本与 \\E(B 的位置是原始字节）。
NANO_FILE_LINES = [
    'serverAddr = "49.235.34.253"', 'serverPort = 7900', 'auth.method = "token"',
    'auth.token="123"', 'webServer.addr = "0.0.0.0"', 'webServer.port = 7400',
    'webServer.user="admin"', 'webServer.password = "abc123"', '[[proxies]]',
    'name = "nas_mingshi"', 'type = "tcp"', 'localIP = "127.0.0.1"',
    'localPort = 22', 'remotePort = 9990', '[[proxies]]', 'name = "web_mingshi"',
    'type = "tcp"', 'localIP = "127.0.0.1"', 'localPort = 80', 'remotePort = 19990',
    '[[proxies]]', 'name = "mingshi_cam01"', 'type = "tcp"',
    'localIP = "192.168.1.201"', 'localPort = 37777', 'remotePort = 4281',
    '[[proxies]]', 'name = "mingshi_cam02"', 'type = "tcp"',
    'localIP = "192.168.1.202"',
]

SHORTCUT_ROWS = [
    [("^G", " 求助"), ("^O", " 写入"), ("^R", " 读档"), ("^Y", " 上页"),
     ("^K", " 剪切文字"), ("^C", " 游标位置")],
    [("^X", " 离开"), ("^J", " 对齐"), ("^W", " 搜索"), ("^V", " 下页"),
     ("^U", " 还原剪切"), ("^T", " 拼写检查")],
]


def build_nano_stream(rows=40, cols=120):
    """按 xterm-256color terminfo 的发射方式重建 nano 首屏。

    真实序列（Clear/备用屏幕/滚动区）来自 Git 自带 terminfo 的解码结果：
    clear=\\E[H\\E[2J, smcup=\\E[?1049h, csr=\\E[%i%p1%d;%p2%dr, cup=\\E[%i%p1%d;%p2%dH,
    vpa=\\E[%i%p1%dd, el=\\E[K, sgr0=\\E(B\\E[m。
    """
    sgr0 = "\x1b(B\x1b[m"
    title = "  GNU nano 2.3.1" + " " * 25 + "文件： frpc_proxies.toml"
    out = [f"\x1b[?1049h\x1b[22;0;0t\x1b[1;{rows}r\x1b[{rows};1H\x1b[H\x1b[2J\x1b[?1h\x1b=",
           "\x1b[7m" + title.ljust(cols - 1) + sgr0]
    for i, line in enumerate(NANO_FILE_LINES):
        out.append(f"\r\x1b[{2 + i}d{sgr0 if i == 0 else ''}{line}")
        out.append("\x1b[K")
    status = "[ 已读取51 行 ]"
    out.append(f"\r\x1b[{rows - 2}d\x1b[{(cols - len(status)) // 2 + 1}G"
               f"{sgr0}{status}\x1b[K")
    for r, entries in enumerate(SHORTCUT_ROWS):
        out.append(f"\r\x1b[{rows - 1 + r}d")
        for col, (key, label) in enumerate(entries):
            out.append(f"\x1b[{col * 13 + 1}G{key}{sgr0}{label}")
        out.append("\x1b[K")
    out.append(f"\r\x1b[{rows}d")
    return "".join(out)


def test_nano_fullscreen_layout_is_not_collapsed():
    """用户那张截图的回归：30 行正文必须各占一行，且不能漏出字面 B。"""
    screen = VTScreen(cols=120, rows=40)
    stream = build_nano_stream()
    for i in range(0, len(stream), 97):               # 故意按非对齐块喂入
        screen.feed(stream[i:i + 97])
    rendered = lines(screen)

    assert rendered[0].startswith("  GNU nano 2.3.1"), rendered[0]
    assert "文件： frpc_proxies.toml" in rendered[0]
    # 正文 30 行逐行落位（旧实现只会剩下最后一行 + 若干残留字符）
    for i, line in enumerate(NANO_FILE_LINES):
        assert rendered[1 + i] == line, (1 + i, rendered[1 + i])
    # status 行居中
    assert rendered[37].strip() == "[ 已读取51 行 ]"
    # 两行快捷键：键与标签都在，且没有 \\E(B 漏出的 B
    assert "求助" in rendered[38] and "^G" in rendered[38]
    assert "拼写检查" in rendered[39] and "^T" in rendered[39]
    assert "B" not in "".join(rendered), "sgr0 的 \\E(B 又漏成字面 B 了"
    # 光标落在网格内（nano 把光标放在编辑区，这里只要求不越界）
    assert 0 <= screen.cursor_row < 40


# ── OSC 7：远端工作目录（只消费不注入） ──────────────────────────────────

def test_osc7_bel_terminated():
    """bash/zsh 的 PROMPT_COMMAND 上报格式：OSC 7 ; file://host/path BEL。"""
    screen = make(data="\x1b]7;file://localhost/home/newbv\x07$ ")
    assert screen.cwd == "/home/newbv"
    assert screen.cwd_host == "localhost"
    assert lines(screen)[0] == "$"                     # 序列本身不上屏（行尾空白被 rstrip）


def test_osc7_st_terminated():
    """xterm 风格用 ST(\\E\\\\) 结束，同样要认。"""
    screen = make(data="\x1b]7;file://box/var/log\x1b\\done")
    assert screen.cwd == "/var/log"
    assert lines(screen)[0] == "done"


def test_osc7_percent_decoding():
    """路径里的空格/中文由 shell 百分号编码，消费时要解码回真实路径。"""
    screen = make(data="\x1b]7;file://h/home/my%20dir%2Fsub\x07")
    assert screen.cwd == "/home/my dir/sub"


def test_osc7_updates_to_latest():
    screen = make(data="\x1b]7;file://h/a\x07\x1b]7;file://h/a/b\x07\x1b]7;file://h/b\x07")
    assert screen.cwd == "/b"


def test_osc7_ignores_root_and_empty():
    assert make(data="\x1b]7;file://h/\x07").cwd == ""
    assert make(data="\x1b]7;file://h\x07").cwd == ""
    assert make(data="\x1b]7;\x07").cwd == ""
    assert make(data="\x1b]7;notauri\x07").cwd == ""


def test_osc_other_numbers_are_still_ignored():
    """标题(0/2)、超链接(8)、改色(10/11) 不得影响屏幕，也不得写成 cwd。"""
    screen = make(data="\x1b]0;my title\x07\x1b]8;;http://x/\x07ok\x1b]8;;\x07\x1b]10;#fff\x07")
    assert screen.cwd == ""
    assert "my title" not in "".join(lines(screen))
    assert "http" not in "".join(lines(screen))
    assert lines(screen)[0].startswith("ok")


def test_osc7_survives_clear_and_alt_screen():
    """清屏/进 vim（备用屏幕）不该丢掉 shell 的 cwd。"""
    screen = make(data="\x1b]7;file://h/srv/app\x07")
    screen.clear()
    assert screen.cwd == "/srv/app"
    screen.feed("\x1b[?1049h")
    assert screen.alt_screen and screen.cwd == "/srv/app"
    screen.feed("\x1b[?1049l")
    assert screen.cwd == "/srv/app"


def test_osc7_cleared_by_reset():
    """RIS 整屏复位语义：尺寸保留、上报的 cwd 清空（shell 会重新报）。"""
    screen = make(data="\x1b]7;file://h/tmp\x07")
    screen.reset()
    assert screen.cwd == "" and screen.cwd_host == ""


def test_osc7_overflow_is_dropped_not_truncated():
    """超长 OSC：截断的路径比"没有"更糟，必须整条丢弃。"""
    long_path = "/" + ("d" * 2000)
    screen = make(data=f"\x1b]7;file://h{long_path}\x07")
    assert screen.cwd == ""


def test_osc7_incremental_feed_split_across_chunks():
    """真实 PTY 会把一条 OSC 7 拆到多次 read 里。"""
    screen = VTScreen(cols=40, rows=4)
    for chunk in ("\x1b]7;file://", "h/opt/", "app\x07$"):
        screen.feed(chunk)
    assert screen.cwd == "/opt/app"
    assert lines(screen)[0] == "$"
