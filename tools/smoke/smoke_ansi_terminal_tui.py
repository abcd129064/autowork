# -*- coding: utf-8 -*-
"""SSH 终端全屏应用（nano / vim / less 风格流）离屏渲染冒烟。

背景：2026-10-04 用户反馈「项目 SSH 里进任何编辑器 / CLI 的全屏界面，UI 就错乱」，
截图里 nano 首屏 30 行正文只剩 1 行、每行多一个字面 B、光标方块显示成 ``&nbsp;``。
根因是自写解析器不认 ncurses 的行定位序列（``\\E[<n>d`` 等）与 ``sgr0`` 的
``\\E(B`` 字符集设计符；修复见 ``core/vt_screen.py`` 与
``windows/remote_session/ansi_terminal.py``，设计说明见
``docs/终端全屏应用渲染修复.md``。

本脚本用**真实会话日志里的 nano 首屏文本**（logs/ssh_sessions/20261004_201221_*.log）
按 xterm-256color terminfo 的发射方式重建字节流，灌进真实控件离屏渲染，断言：
  1. 正文逐行落位（不许再叠成一行）；
  2. 全屏无字面 ``B``（``\\E(B`` 不许漏成可见字符）；
  3. HTML 里不许出现被二次转义的 ``&amp;nbsp;``（光标方块曾渲染成字面 &nbsp;）；
  4. status 行居中、两行快捷键键与标签齐全；
  5. 滚动区域 + 插行/删行（vim/less 滚动）后的内容位移正确；
  6. 控件尺寸变化会发出 grid_resized（供上层 resize_pty 同步远端 PTY）。
并截图到 tools/_scratch/ 作为验收证据。

并截图到 tools/_scratch/ 作为验收证据。

注意：本机 Qt offscreen 环境拿不到字体目录（QFontDatabase 报 "Cannot find font directory"），
grab() 出来的 PNG 是豆腐块——它只能证明**结构**（逐行落位、行高一致、光标方块位置），
不能用来验收字形/对齐；文本断言才是这里的判定依据。

运行（推荐全依赖解释器；E:\\ANACONDA 无 qfluentwidgets 时会走直载回退）：
    QT_QPA_PLATFORM=offscreen <python> tools/smoke/smoke_ansi_terminal_tui.py
退出码：0 通过 / 1 失败。
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# ---- Qt DLL 引导（照抄 docs/Qt内联引导说明.md，必须在 import PySide6 之前）----
import importlib.util as _iu  # noqa: E402

_qt_handles = []
try:
    _spec = _iu.find_spec("PySide6")
    if _spec is not None:
        for _d in (list(_spec.submodule_search_locations or []) +
                   [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")]):
            if _d and os.path.isdir(_d):
                try:
                    _qt_handles.append(os.add_dll_directory(_d))
                except OSError:
                    pass
        if _spec.submodule_search_locations:
            _pkg = list(_spec.submodule_search_locations)[0]
            os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(_pkg, "plugins"))
            os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH",
                                  os.path.join(_pkg, "plugins", "platforms"))
except Exception:
    pass

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

OUT_DIR = os.path.join(PROJECT_ROOT, "tools", "_scratch")
FAILURES = []


def check(cond, message):
    if cond:
        print(f"  [通过] {message}")
    else:
        print(f"  [失败] {message}")
        FAILURES.append(message)


def load_widget():
    """优先走包导入（真实运行路径）；缺 qfluentwidgets 时直载模块文件。"""
    try:
        from windows.remote_session.ansi_terminal import ANSITerminalWidget
        return ANSITerminalWidget, "包导入"
    except ModuleNotFoundError as exc:
        path = os.path.join(PROJECT_ROOT, "windows", "remote_session", "ansi_terminal.py")
        spec = _iu.spec_from_file_location("ansi_terminal_smoke", path)
        mod = _iu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.ANSITerminalWidget, f"直载（{exc}）"


# ── 真实会话日志里的 nano 首屏文本（一字不改） ──────────────────────────
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
    """nano 首屏：clear + 备用屏幕 + 滚动区 + VPA 逐行定位 + sgr0/反显。"""
    sgr0 = "\x1b(B\x1b[m"
    title = "  GNU nano 2.3.1" + " " * 25 + "文件： frpc_proxies.toml"
    out = [f"\x1b[?1049h\x1b[22;0;0t\x1b[1;{rows}r\x1b[{rows};1H\x1b[H\x1b[2J\x1b[?1h\x1b=",
           "\x1b[7m" + title.ljust(cols - 1) + sgr0]
    for i, line in enumerate(NANO_FILE_LINES):
        out.append(f"\r\x1b[{2 + i}d{sgr0 if i == 0 else ''}{line}\x1b[K")
    status = "[ 已读取51 行 ]"
    out.append(f"\r\x1b[{rows - 2}d\x1b[{(cols - len(status)) // 2 + 1}G{sgr0}{status}\x1b[K")
    for r, entries in enumerate(SHORTCUT_ROWS):
        out.append(f"\r\x1b[{rows - 1 + r}d")
        for col, (key, label) in enumerate(entries):
            out.append(f"\x1b[{col * 13 + 1}G{key}{sgr0}{label}")
        out.append("\x1b[K")
    return "".join(out) + f"\r\x1b[{rows}d"


def build_vim_header(rows=24, cols=80):
    """vim/less 风格首屏：备用屏幕 + 滚动区（1..rows-1）+ 前 5 行正文。"""
    out = [f"\x1b[?1049h\x1b[1;{rows - 1}r\x1b[H\x1b[2J"]
    for i in range(1, 6):
        out.append(f"\x1b[{i};1Hline {i}\x1b[K")
    return "".join(out)


def main() -> int:
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout

    app = QApplication.instance() or QApplication([])
    try:
        import core.acrylic_patch  # noqa: F401  （qfluentwidgets 导入前的前置补丁）
    except Exception:
        pass
    widget_cls, how = load_widget()
    print(f"# ANSITerminalWidget 加载方式：{how}")
    os.makedirs(OUT_DIR, exist_ok=True)

    sizes = []
    widget = widget_cls()
    widget.grid_resized.connect(lambda c, r: sizes.append((c, r)))
    widget.set_grid_size(120, 40)
    widget.set_input_enabled(True)

    print("\n## 场景 1：nano 首屏（用户截图那次会话的真实文本）")
    stream = build_nano_stream()
    for i in range(0, len(stream), 97):        # 按非对齐块喂入，模拟 recv 边界
        widget.write_output(stream[i:i + 97])
    widget._render()
    rows = widget.screen_text()

    check(rows[0].startswith("  GNU nano 2.3.1"), f"标题行正确：{rows[0][:34]!r}")
    check("文件： frpc_proxies.toml" in rows[0], "文件名落在标题行")
    missing = [i for i, text in enumerate(NANO_FILE_LINES) if rows[1 + i] != text]
    check(not missing, f"正文 30 行逐行落位（塌行行号：{missing}）")
    check(rows[37].strip() == "[ 已读取51 行 ]",
          f"status 行居中：{rows[37].strip()!r}")
    check("求助" in rows[38] and "^G" in rows[38], "快捷键行 1 键与标签齐全")
    check("拼写检查" in rows[39] and "^T" in rows[39], "快捷键行 2 键与标签齐全")
    joined = "".join(rows)
    check("B" not in joined, "全屏没有 sgr0(\\E(B) 漏出的字面 B")
    check("&nbsp;" not in joined, "屏幕文本里没有字面 &nbsp;（光标方块旧 bug）")
    html = widget.toHtml()
    check("&amp;nbsp;" not in html, "HTML 里没有被二次转义的 &amp;nbsp;")
    check("background-color:#00ff00" in html, "光标方块已渲染")
    check(len(rows) == 40, f"渲染行数 = 网格行数 40（实际 {len(rows)}）")
    widget.grab().save(os.path.join(OUT_DIR, "smoke_ansi_nano.png"))

    print("\n## 场景 2：vim/less 风格滚动（滚动区 + 插行，分步断言）")
    rows_n, cols_n = 24, 80
    widget2 = widget_cls()
    widget2.set_grid_size(cols_n, rows_n)
    widget2.set_input_enabled(True)
    widget2.write_output(build_vim_header(rows_n, cols_n))
    widget2._render()
    step1 = widget2.screen_text()
    check(step1[0] == "line 1" and step1[4] == "line 5", "首屏 5 行落位")

    # 光标到滚动区底 + 换行 → 区内整体上滚，新行落在区底；区外不动
    widget2.write_output(f"\x1b[{rows_n - 1};1H\nline 6\x1b[K")
    widget2._render()
    step2 = widget2.screen_text()
    check(step2[0] == "line 2" and step2[3] == "line 5" and step2[4] == "",
          f"区内上滚后行位移正确：{step2[0]!r}..{step2[3]!r}，第 5 行为空")
    check(step2[22] == "line 6", "新行写在滚动区底（区外行不受影响）")

    # 顶部插行 → 光标行（区首）之后整体下移一行，区底内容被挤出
    widget2.write_output("\x1b[1;1H\x1b[Lline 0\x1b[K")
    widget2._render()
    step3 = widget2.screen_text()
    check(step3[0] == "line 0" and step3[1] == "line 2", "顶部插行后整体下移")
    check(step3[4] == "line 5" and step3[5] == "", "插行挤掉区底 'line 6'")

    widget2.write_output("".join(f"\x1b[{r};1H~" for r in range(7, rows_n + 1)))
    widget2.write_output(f"\x1b[{rows_n};1H\x1b[7m-- INSERT --\x1b[0m\x1b[K")
    widget2._render()
    rows2 = widget2.screen_text()
    check(rows2[6].startswith("~"), f"空行标记 ~ 渲染正常：{rows2[6]!r}")
    check("-- INSERT --" in rows2[23], "反显状态行正常")
    check("B" not in "".join(rows2), "滚动场景无字面 B")
    widget2.grab().save(os.path.join(OUT_DIR, "smoke_ansi_vim.png"))

    print("\n## 场景 3：网格尺寸同步（供 resize_pty 用）")
    sizes.clear()
    holder = QWidget()
    holder.resize(1000, 700)
    layout = QVBoxLayout(holder)
    live = widget_cls()
    live.grid_resized.connect(lambda c, r: sizes.append((c, r)))
    layout.addWidget(live)
    holder.show()
    app.processEvents()
    first = live.grid_size()
    holder.resize(1400, 900)
    app.processEvents()
    second = live.grid_size()
    check(sizes and first[0] >= 20 and first[1] >= 4,
          f"控件尺寸 → 网格尺寸：{first}（发出 {len(sizes)} 次 grid_resized）")
    check(second != first, f"放大窗口后网格跟着变：{first} → {second}")
    check(second[0] <= 200 and second[1] <= 100, "网格尺寸在合理范围内")
    # 网格必须放得下整屏：否则全屏应用最后一行被裁（备用屏幕又没有滚动条可滚）
    cols, rows = live.grid_size()
    for r in range(rows):
        live.write_output(f"\x1b[{r + 1};1Hrow{r}")
    live._render()
    app.processEvents()
    doc_h = live.document().size().height()
    vp_h = live.viewport().height()
    check(doc_h <= vp_h and live.verticalScrollBar().maximum() == 0,
          f"整屏放得下不裁行（{cols}x{rows}：文档高 {doc_h:.0f} ≤ 视口 {vp_h}）")
    holder.close()

    print()
    if FAILURES:
        print(f"结论：失败 {len(FAILURES)} 项")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("结论：全部通过（截图见 tools/_scratch/smoke_ansi_nano.png、smoke_ansi_vim.png）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
