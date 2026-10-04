# -*- coding: utf-8 -*-
"""ANSITerminalWidget 渲染 / 输入层离线单测（2026-10-05）。

屏幕语义由 tests/test_vt_screen.py 覆盖；本文件补的是**控件层**曾经没有任何
自动化断言的部分（评审结论：渲染层只在冒烟脚本里，没进门禁）：

1. **网格必须放得下**：Qt 富文本里无单位 ``line-height`` 按 font-size 倍数解释，
   与 ``QFontMetricsF.lineSpacing()*1.3`` 不等，按后者算行数会多一行 →
   全屏应用最后一行被裁（nano 丢第二行快捷键）。断言"填满整屏后不需要滚动"。
2. **不许出现二次转义的 ``&amp;nbsp;``**：光标方块曾把它当文本显示出来。
3. **重渲染不许清掉用户选区**：否则 Ctrl+C 从"复制"退化成远端 SIGINT。
4. **宽字符只渲染一次**（占两格，第二格是续格哨兵）。
5. **输入侧模式**：DECCKM 应用光标键、修饰键组合、括号粘贴、SGR 鼠标上报。
6. **网格尺寸契约**：grid_resized 与 grid_size 一致，sync_grid(force) 可在
   invoke_shell 之前把 PTY 尺寸对齐到视口。

运行：E:\\ANACONDA\\python.exe -m pytest tests/test_ansi_terminal_render.py -q
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt                    # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent, QTextCursor     # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

from windows.remote_session.ansi_terminal import ANSITerminalWidget  # noqa: E402

_qapp = None


@pytest.fixture(scope="module")
def qapp():
    global _qapp
    _qapp = QApplication.instance() or QApplication(sys.argv[:1])
    return _qapp


class Env:
    """一个已显示、已定网格、可注入按键的终端控件"""

    def __init__(self, qapp, size, cols=80, rows=24):
        self.holder = QWidget()
        self.holder.resize(*size)
        layout = QVBoxLayout(self.holder)
        layout.setContentsMargins(0, 0, 0, 0)
        self.widget = ANSITerminalWidget()
        layout.addWidget(self.widget)
        self.emitted = []
        self.sizes = []
        self.widget.key_input.connect(self.emitted.append)
        self.widget.grid_resized.connect(lambda c, r: self.sizes.append((c, r)))
        self.holder.show()
        qapp.processEvents()
        self.widget.set_grid_size(cols, rows)
        self.widget.set_input_enabled(True)
        self.widget._render()
        qapp.processEvents()

    def key(self, key, mods=Qt.NoModifier):
        self.emitted.clear()
        self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, mods))
        return self.emitted[-1] if self.emitted else None

    def close(self):
        self.holder.close()


@pytest.fixture
def env(qapp):
    built = Env(qapp, (1000, 700), cols=80, rows=24)
    yield built
    built.close()


# ── 1. 网格放得下（不许裁行） ───────────────────────────────────────────

@pytest.mark.parametrize("size", [(800, 600), (1280, 800), (1920, 1080)])
def test_grid_fits_viewport(qapp, size):
    built = Env(qapp, size)
    try:
        cols, rows = built.widget.grid_size()
        assert (cols, rows) >= (20, 4)
        for r in range(rows):                      # 用绝对定位填满整屏，不触发滚动
            built.widget.write_output(f"\x1b[{r + 1};1Hrow{r}")
        built.widget._render()
        qapp.processEvents()
        doc_h = built.widget.document().size().height()
        vp_h = built.widget.viewport().height()
        assert doc_h <= vp_h, f"{size}: 文档高 {doc_h} 超过视口 {vp_h}，末行会被裁"
        assert built.widget.verticalScrollBar().maximum() == 0, \
            f"{size}: 整屏内容不需要滚动，实际 maximum={built.widget.verticalScrollBar().maximum()}"
        assert built.widget.screen_text()[rows - 1] == f"row{rows - 1}"
    finally:
        built.close()


# ── 2. 渲染产物里的历史 bug ─────────────────────────────────────────────

def test_no_double_escaped_nbsp_in_html(env):
    env.widget.write_output("a b\x1b[1;2H")        # 光标停在空格那一格
    env.widget._render()
    html = env.widget.toHtml()
    assert "&amp;nbsp;" not in html, "光标方块又把 &nbsp; 当文本渲染了"
    assert "background-color:#00ff00" in html, "光标方块没渲染出来"


def test_no_leaked_charset_designator(env):
    env.widget.write_output("\x1b(B\x1b[mhello\x1b(B")
    env.widget._render()
    assert env.widget.screen_text()[0] == "hello"  # 没有漏出字面 B


def test_wide_char_rendered_once(env):
    env.widget.write_output("中文abc")
    env.widget._render()
    html = env.widget.toHtml()
    assert html.count("中") == 1 and html.count("文") == 1
    assert "\x00" not in html                      # 续格哨兵不许出现在 HTML 里
    assert env.widget.screen_text()[0] == "中文abc"


# ── 3. 选区 / 滚动位置要保持 ────────────────────────────────────────────

def test_selection_survives_render(env):
    env.widget.write_output("hello world\r\n")
    env.widget._render()
    cursor = env.widget.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(5, QTextCursor.KeepAnchor)
    env.widget.setTextCursor(cursor)
    assert env.widget.textCursor().hasSelection()

    env.widget.write_output("more output\r\n")     # 远端继续输出
    env.widget._render()
    assert env.widget.textCursor().hasSelection(), "重渲染清掉了选区（Ctrl+C 会变成 SIGINT）"
    assert env.widget.textCursor().selectedText() == "hello"


# ── 4. 输入侧：DECCKM / 修饰键 / 括号粘贴 / 鼠标 ────────────────────────

def test_arrow_keys_follow_decckm(env):
    assert env.key(Qt.Key_Up) == "\x1b[A"
    env.widget.write_output("\x1b[?1h")            # ncurses smkx：应用光标键
    assert env.key(Qt.Key_Up) == "\x1bOA"
    assert env.key(Qt.Key_Home) == "\x1bOH"
    env.widget.write_output("\x1b[?1l")
    assert env.key(Qt.Key_End) == "\x1b[F"


def test_modified_special_keys(env):
    assert env.key(Qt.Key_Up, Qt.ControlModifier) == "\x1b[1;5A"
    assert env.key(Qt.Key_Up, Qt.ShiftModifier) == "\x1b[1;2A"
    assert env.key(Qt.Key_Up, Qt.AltModifier) == "\x1b[1;3A"
    assert env.key(Qt.Key_Delete) == "\x1b[3~"
    assert env.key(Qt.Key_F1) == "\x1bOP"
    assert env.key(Qt.Key_Return) == "\r"
    assert env.key(Qt.Key_Backspace) == "\x7f"
    assert env.key(Qt.Key_Backspace, Qt.ControlModifier) == "\x17"
    assert env.key(Qt.Key_Backtab, Qt.ShiftModifier) == "\x1b[Z"


def test_ctrl_letter_and_interrupt(env):
    assert env.key(Qt.Key_A, Qt.ControlModifier) == "\x01"
    assert env.key(Qt.Key_C, Qt.ControlModifier) == "\x03"     # 无选中 → SIGINT


def test_bracketed_paste(env):
    QApplication.clipboard().setText("line1\nline2")
    env.emitted.clear()
    env.widget._paste_from_clipboard()
    assert env.emitted[-1] == "line1\rline2"
    env.widget.write_output("\x1b[?2004h")         # vim 等开启括号粘贴
    env.emitted.clear()
    env.widget._paste_from_clipboard()
    assert env.emitted[-1] == "\x1b[200~line1\rline2\x1b[201~"


def test_mouse_reporting_sgr(env):
    pos = QPointF(30.0, 40.0)
    env.emitted.clear()
    env.widget.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, pos, pos, Qt.LeftButton,
                                           Qt.LeftButton, Qt.NoModifier))
    assert not env.emitted, "远端没开鼠标上报时不该发序列"
    env.widget.write_output("\x1b[?1000h\x1b[?1006h")
    env.emitted.clear()
    env.widget.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, pos, pos, Qt.LeftButton,
                                           Qt.LeftButton, Qt.NoModifier))
    col, row = env.widget._mouse_cell(pos)
    assert env.emitted[-1] == f"\x1b[<0;{col};{row}M"


# ── 5. 网格尺寸契约 ────────────────────────────────────────────────────

def test_grid_resized_signal_matches_grid(qapp):
    holder = QWidget()
    holder.resize(1000, 700)
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    widget = ANSITerminalWidget()
    sizes = []
    widget.grid_resized.connect(lambda c, r: sizes.append((c, r)))
    layout.addWidget(widget)
    holder.show()
    qapp.processEvents()
    first = widget.grid_size()
    assert sizes and sizes[-1] == first, "grid_resized 必须与 grid_size 一致"
    holder.resize(1400, 900)
    qapp.processEvents()
    assert widget.grid_size() != first, "放大窗口后网格要跟着变"
    assert sizes[-1] == widget.grid_size()
    holder.close()


def test_sync_grid_force_aligns_hidden_widget(qapp):
    """隐藏页签/未显示过的控件也要能显式对齐网格（连接前调用 sync_grid(force)）"""
    widget = ANSITerminalWidget()
    sizes = []
    widget.grid_resized.connect(lambda c, r: sizes.append((c, r)))
    widget.resize(1000, 700)
    widget.sync_grid(force=True)
    cols, rows = widget.grid_size()
    assert (cols, rows) != (120, 40), "force 对齐后不该还是默认 120x40"
    assert sizes and sizes[-1] == (cols, rows)
    assert cols >= 20 and rows >= 4


def test_alt_screen_turns_off_scrollbar(env):
    assert env.widget.alt_screen is False
    env.widget.write_output("\x1b[?1049h\x1b[H\x1b[2J")
    env.widget._render()
    assert env.widget.alt_screen is True
    assert env.widget.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    env.widget.write_output("\x1b[?1049l")
    env.widget._render()
    assert env.widget.alt_screen is False
