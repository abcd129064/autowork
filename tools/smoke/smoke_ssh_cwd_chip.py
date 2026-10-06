# -*- coding: utf-8 -*-
"""SSH 终端「远端工作目录」chip + 终端→SFTP 反向跳转 offscreen/无网冒烟（P1-2）

验证 SSHTerminalPanel 与 core.vt_screen 的 OSC 7 接线：
  1. 远端没上报时 chip 为空、SFTP 按钮置灰（不假装知道目录）；
  2. shell 上报 OSC 7 → chip 显示目录（home 缩写成 ~）、按钮可用、remote_path 正确；
  3. 标题等其它 OSC 号不改变 chip（只认 7 号）；
  4. 点按钮 → open_sftp_here 带出目录 + 记日志（remote_mixin 的接收侧）；
  5. root 的 home（/root）同样缩写；
  6. 会话恢复路径：start_dir 立刻上 chip（不必等 shell 上报），之后随上报更新；
  7. 进全屏应用（备用屏幕 1049）不丢 cwd。

不联网、起真实连接：构造前把 _connect_ssh 打成空函数（面板用 QTimer.singleShot(100)
调度连接，打成空函数后即使跑了事件循环也不会去连）；设置读写打桩到内存 dict。

运行（需要 qfluentwidgets + paramiko + PySide6 的解释器）：
    set QT_QPA_PLATFORM=offscreen
    C:\\Users\\shen_zhe\\miniconda3\\python.exe tools/smoke/smoke_ssh_cwd_chip.py
"""
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# ---- Qt DLL 引导（同 windows/aftersale_panel.py 顶部，规避 conda Qt 冲突） ----
import importlib.util as _iu                                        # noqa: E402
try:
    _spec = _iu.find_spec('PySide6')
    if _spec is not None:
        for _d in (list(_spec.submodule_search_locations or []) +
                   [os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                                 'System32')]):
            if _d and os.path.isdir(_d):
                try:
                    os.add_dll_directory(_d)
                except OSError:
                    pass
except Exception:
    pass

from PySide6.QtWidgets import QApplication                     # noqa: E402

from core import app_settings                                  # noqa: E402
from windows.remote_session import ssh_terminal as st          # noqa: E402

os.makedirs(os.path.join(PROJECT_ROOT, "tools", "_scratch"), exist_ok=True)
_SHOT = os.path.join(PROJECT_ROOT, "tools", "_scratch", "smoke_ssh_cwd_chip.png")

# ---- 打桩：设置只碰内存；绝不发起真实连接 ----
_STORE = {}
app_settings.get = lambda k, d=None: _STORE.get(k, d)
app_settings.set = lambda k, v: _STORE.__setitem__(k, v)
app_settings.get_merged = lambda: dict(_STORE)
st.SSHTerminalPanel._connect_ssh = lambda self: None      # 构造时的 singleShot 变成空操作

_LOGS = []
_PASS = 0
_FAIL = 0


def _check(label, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [通过] {label}")
    else:
        _FAIL += 1
        print(f"  [失败] {label} {detail}")


def _log(msg):
    _LOGS.append(str(msg))


def _logs_contain(needle):
    return any(needle in (m or "") for m in _LOGS)


def _pump(ms=120):
    """跑一小段事件循环，让渲染定时器把 OSC 7 的变化送出来"""
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.01)


def _make(username="newbv", start_dir=""):
    panel = st.SSHTerminalPanel("203.0.113.9", 22, username, "pw",
                                log_callback=_log, server_name="snk_001",
                                start_dir=start_dir)
    panel._terminal.set_input_enabled(True)      # 让终端走正常输入路径
    return panel


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    print("== SSH 终端 cwd chip（OSC 7）冒烟 ==")

    # ---- [1] 未上报：chip 空、按钮置灰 ----
    print("-- [1] 远端未上报 OSC 7")
    p1 = _make()
    p1.show()
    _pump()
    _check("chip 初始为空", p1._lbl_cwd.text() == "", repr(p1._lbl_cwd.text()))
    _check("SFTP 按钮置灰", not p1._btn_sftp_here.isEnabled())
    _check("remote_path 为空", p1.remote_path == "", repr(p1.remote_path))

    # ---- [2] 上报 OSC 7 ----
    print("-- [2] shell 上报 OSC 7")
    p1._terminal.write_output("\x1b]7;file://localhost/home/newbv/snooker\x07")
    p1._terminal.write_output("$ ")
    _pump()
    _check("chip 显示 ~/snooker", p1._lbl_cwd.text() == "~/snooker",
           repr(p1._lbl_cwd.text()))
    _check("SFTP 按钮可用", p1._btn_sftp_here.isEnabled())
    _check("tooltip 带完整路径", "/home/newbv/snooker" in p1._lbl_cwd.toolTip())
    _check("remote_path 为绝对路径（会话存档用）",
           p1.remote_path == "/home/newbv/snooker", repr(p1.remote_path))
    _check("目录不上屏（OSC 序列只被消费）",
           "/home/newbv/snooker" not in "".join(
               p1._terminal.screen_text() or ""), "序列漏成了可见文本")

    # ---- [3] 其它 OSC 号不影响 chip ----
    print("-- [3] 标题/超链接等 OSC 不影响 chip")
    before = p1._lbl_cwd.text()
    p1._terminal.write_output("\x1b]0;my title\x07\x1b]8;;http://x/\x07x\x1b]8;;\x07")
    _pump()
    _check("chip 保持不变", p1._lbl_cwd.text() == before, repr(p1._lbl_cwd.text()))

    # ---- [4] 点击 → 反向跳转信号 ----
    print("-- [4] 终端 → SFTP 反向跳转")
    got = []
    p1.open_sftp_here.connect(lambda path: got.append(path))
    p1._btn_sftp_here.click()
    _pump(60)
    _check("open_sftp_here 带出当前目录", got == ["/home/newbv/snooker"], repr(got))
    _check("日志记录跳转", _logs_contain("[SSH] 在 SFTP 中打开: /home/newbv/snooker"),
           repr([m for m in _LOGS if "SFTP" in m][:3]))

    # ---- [5] root 的 home 缩写 ----
    print("-- [5] root 的 /root 也缩写为 ~")
    p5 = _make(username="root")
    p5._terminal.write_output("\x1b]7;file://h/root/tools\x07")
    _pump()
    _check("chip 显示 ~/tools", p5._lbl_cwd.text() == "~/tools", repr(p5._lbl_cwd.text()))

    # ---- [6] 会话恢复：start_dir 立即上 chip，之后随上报更新 ----
    print("-- [6] 会话恢复的起始目录")
    p6 = _make(start_dir="/srv/app")
    _pump()
    _check("chip 立即显示恢复目录", p6._lbl_cwd.text() == "/srv/app",
           repr(p6._lbl_cwd.text()))
    _check("按钮可用", p6._btn_sftp_here.isEnabled())
    _check("remote_path 回退到起始目录", p6.remote_path == "/srv/app", repr(p6.remote_path))
    p6._terminal.write_output("\x1b]7;file://h/opt/logs\x07")
    _pump()
    _check("上报后 chip 跟随更新", p6._lbl_cwd.text() == "/opt/logs",
           repr(p6._lbl_cwd.text()))

    # ---- [7] 全屏应用（备用屏幕）不丢 cwd ----
    print("-- [7] 进 vim/nano（备用屏幕）不丢 cwd")
    p1._terminal.write_output("\x1b[?1049h\x1b[H\x1b[2J")
    _pump()
    _check("备用屏幕中 chip 仍在", p1._lbl_cwd.text() == "~/snooker",
           repr(p1._lbl_cwd.text()))
    _check("remote_path 仍在", p1.remote_path == "/home/newbv/snooker")
    p1._terminal.write_output("\x1b[?1049l")
    _pump()

    # ---- 截图（底部状态条区域） ----
    try:
        p1.resize(900, 420)
        _pump(80)
        p1.grab().save(_SHOT)
    except Exception as e:                                       # pragma: no cover
        print(f"  [提示] 截图失败（不影响断言）：{e}")

    for p in (p1, p5, p6):
        try:
            p.shutdown()
        except Exception as e:
            print(f"  [提示] shutdown 异常（不影响断言）：{e}")

    print(f"\n断言：{_PASS} 通过 / {_FAIL} 失败")
    print(f"截图：{_SHOT}")
    print("SMOKE_OK" if _FAIL == 0 else "SMOKE_FAILED")
    sys.stdout.flush()
    # offscreen 平台退出阶段可能崩（Qt 已知现象），断言已全部完成 → 直接退出
    os._exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    main()
