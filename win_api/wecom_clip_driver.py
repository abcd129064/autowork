# -*- coding: utf-8 -*-
"""企业微信 PC 端聊天记录采集驱动（Win32 输入注入 + 剪贴板）

背景：用户是售后群群主但没有企微超管权限，开不了官方「会话内容存档」。
本模块走的是**不接触后台、不破解客户端**的路线：只模拟"人本来就会做的操作"——
在企微窗口里按住左键向上拖动选中消息、按 PageUp 翻页、再动一下鼠标让选区
扩展到新露出的消息、Ctrl+C 复制。剪贴板文本自带发送人与时间（格式见
core/wecom_clip.py 的 docstring），所以不需要 OCR。

**只用 Python 标准库 ctypes**（SendInput / 剪贴板 / 窗口枚举），刻意不引入
pyautogui / pywinauto / pywin32 —— 本仓库 AGENTS.md §3.2 禁止 agent 擅自装包，
而且这几个包对"发送真实输入事件"并没有额外好处。

边界（刻意的，不要越界）：
  * 不注入进程、不 hook、不读进程内存、不解密本地数据库、不调非公开协议。
  * 只发鼠标/键盘事件 + 读系统剪贴板 —— 与真人操作不可区分。
  * 采集期间**独占桌面**，企微窗口会被最大化并抢焦点。

与``core/wecom_clip.py``的分工：本模块只负责"把消息复制到剪贴板并切分批次"，
返回未经解析的原始文本；文本 → 结构化消息 → 归档库由 core.wecom_clip 负责。
这样 win_api 层不依赖解析规则，解析规则也能脱离桌面单独测试。

用法（真实调用需要通过 tools/smoke 的采集入口，或自行调用 collect_stream）：

    from win_api import wecom_clip_driver as drv
    drv.set_dpi_aware()
    win = drv.find_windows("wxwork.exe")[0]
    run = drv.collect_stream(win["hwnd"], drag_px=560, pages=40, log=print)
    print(run.chars)       # 逐轮字符数，用来判断有没有翻到头

注意：真机调用会独占桌面，且**必须脱离文件沙箱**运行（受限令牌下
SetCursorPos/SendInput 会被静默忽略：SendInput 返回成功但光标不动）。
"""
import ctypes
import os
import time
from ctypes import wintypes
from typing import Callable, List, Optional, Tuple

#: 目标进程名（企业微信 PC 端）
TARGET_PROC = "wxwork.exe"

# use_last_error=True 才有独立的错误码副本：Win32 调用之间会互相覆盖 LastError，
# 不显式开启的话，报错信息里拿到的往往是上一条无关调用的错误码。
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --- 函数原型 ---------------------------------------------------------------
# ctypes 默认把返回值当 32 位 int，**指针型返回值会被悄悄截断**。read_clipboard
# 最初就死在这里：OSError: access violation reading 0x000000007F37F820 —— 
# GlobalLock 返回的 64 位指针被截成了 32 位。凡返回值是 HANDLE/HWND/指针的，
# 以及参数里含 HWND 的，都必须显式声明。
user32.GetForegroundWindow.restype = wintypes.HWND
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = ctypes.c_void_p
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetActiveWindow.argtypes = [wintypes.HWND]
user32.SetFocus.argtypes = [wintypes.HWND]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.c_void_p]
user32.SetWindowPlacement.argtypes = [wintypes.HWND, ctypes.c_void_p]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]

# ShowWindow 的 nCmdShow 取值；不用 wintypes 常量是因为它没有导出这两个。
SW_MAXIMIZE = 3
SW_RESTORE = 9

VK_PRIOR = 0x21     # PageUp
VK_CONTROL = 0x11
VK_ESCAPE = 0x1B
VK_C = 0x43

# SendInput 的 INPUT.type 判别值，决定联合体里哪个成员有效。
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120

CF_UNICODETEXT = 13
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTunion(ctypes.Union):
    # 联合体成员顺序与 Win32 头文件一致：布局一旦错位，SendInput 仍返回成功，
    # 但事件内容是垃圾（这正是最难受的一类静默失败）。
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTunion)]


# 仅用于焦点诊断：键盘事件发给前台窗口，但真正接收的是它内部的焦点子窗口，
# 只有拿到 hwndFocus 才能判断 PageUp 到底有没有落到消息列表上。
class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT)]


class WINDOWPLACEMENT(ctypes.Structure):
    """用于采集结束后把企微窗口恢复成原来的位置/最大化状态。"""
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT),
                ("showCmd", wintypes.UINT), ("ptMinPosition", wintypes.POINT),
                ("ptMaxPosition", wintypes.POINT), ("rcNormalPosition", wintypes.RECT)]


# SendInput 最常见的静默失败原因就是结构体大小算错；x64 下必须是 40 字节。
assert ctypes.sizeof(INPUT) == 40, "INPUT 大小应为 40，实际 {}".format(ctypes.sizeof(INPUT))

# 注意：不要给 SendInput 声明 argtypes —— POINTER(INPUT) 会拒绝 byref(INPUT 数组)
# （ctypes.ArgumentError: expected LP_INPUT instance instead of pointer to INPUT_Array_1）。
user32.SendInput.restype = wintypes.UINT


def _send(*inputs: INPUT) -> None:
    """一次性提交若干输入事件；部分成功也算失败。

    SendInput 会把数组里的所有事件当作一个原子批次，返回值小于 n 说明整批被
    拒绝（通常是 UIPI 或会话隔离），此时继续采集只会得到重复的旧剪贴板内容，
    所以直接抛错，把问题暴露在第一轮而不是攒成一堆空批次。
    """
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    sent = user32.SendInput(n, ctypes.byref(arr), ctypes.sizeof(INPUT))
    if sent != n:
        raise OSError("SendInput 只发出 {}/{} 个事件，err={}".format(
            sent, n, ctypes.get_last_error()))


# dwExtraInfo 留空（None）即可：它只用于让应用区分注入事件，本模块不关心。
def _mouse(flags: int, x: int = 0, y: int = 0) -> INPUT:
    return INPUT(type=INPUT_MOUSE, u=_INPUTunion(mi=MOUSEINPUT(
        dx=x, dy=y, mouseData=0, dwFlags=flags, time=0, dwExtraInfo=None)))


def _key(vk: int, up: bool = False) -> INPUT:
    """构造一个键盘事件；wScan 留 0 表示按虚拟键码下发，不依赖键盘布局。"""
    return INPUT(type=INPUT_KEYBOARD, u=_INPUTunion(ki=KEYBDINPUT(
        wVk=vk, wScan=0, dwFlags=(KEYEVENTF_KEYUP if up else 0),
        time=0, dwExtraInfo=None)))


def screen_size() -> Tuple[int, int]:
    """主屏像素尺寸；不取 GetSystemMetrics(78/79)（虚拟屏幕），因为拖拽坐标
    全部是基于主屏原点的窗口矩形算出来的。"""
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def to_absolute(x: int, y: int) -> Tuple[int, int]:
    """屏幕像素 → SendInput 的 0..65535 归一化坐标。

    除数是 sw-1 而不是 sw：65535 对应屏幕最右一列像素，用 sw 会让落点系统性
    偏左半个像素比，多次拖拽累积后选区会差一整行。max(...,1) 只为防 0 除。
    """
    sw, sh = screen_size()
    return int(round(x * 65535.0 / max(sw - 1, 1))), int(round(y * 65535.0 / max(sh - 1, 1)))


def mouse_move(x: int, y: int) -> None:
    # 绝对坐标移动：相对移动（dx/dy）会受鼠标加速曲线影响，同样一段位移在不同
    # 机器上落点不同，选区长度就没法按像素复现。
    ax, ay = to_absolute(x, y)
    _send(_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, ax, ay))


# 按下与释放分开导出，是为了让 collect_stream 能在"按住左键翻页"和"异常中断
# 时兜底松开"两种场景里各取所需。
def mouse_down() -> None:
    _send(_mouse(MOUSEEVENTF_LEFTDOWN))


def mouse_up() -> None:
    _send(_mouse(MOUSEEVENTF_LEFTUP))


def wheel(notches: int = 1, up: bool = True, per: float = 0.03) -> None:
    """滚轮。一格 = WHEEL_DELTA(120)，正数 = 向远离用户的方向（内容向上走 = 看更早的）。

    一格一发而不是一次发多格：企微的滚动带惯性/动画，整批灌进去会被合并成一次，
    实际滚动距离远小于预期，翻页就会漏消息。
    """
    step = WHEEL_DELTA if up else -WHEEL_DELTA
    for _ in range(abs(notches)):
        data = step & 0xFFFFFFFF
        _send(INPUT(type=INPUT_MOUSE, u=_INPUTunion(mi=MOUSEINPUT(
            dx=0, dy=0, mouseData=data, dwFlags=MOUSEEVENTF_WHEEL,
            time=0, dwExtraInfo=None))))
        time.sleep(per)


def advance_up(x: int, y: int, notches: int) -> None:
    """把鼠标放到 (x, y)（必须在消息区里）然后向上滚 —— 让列表露出更早的消息。

    先移动再滚：滚轮事件发给鼠标下方的窗口，光标停在上一次的拖拽终点时，
    滚动可能落到侧边栏或输入框上，表现为"滚了但消息区没动"。
    """
    mouse_move(x, y)
    time.sleep(0.15)
    wheel(notches, up=True)


def key_tap(vk: int, hold_ms: int = 15) -> None:
    """敲一下按键（默认按住 15ms 再松）。"""
    # 按下与抬起之间必须留出间隔：部分应用在同一个消息循环里同时收到 down/up
    # 会把它当成误触而丢弃，PageUp 就"按了没反应"。
    _send(_key(vk))
    time.sleep(hold_ms / 1000.0)
    _send(_key(vk, up=True))


def ctrl_c() -> None:
    # 手工发四个事件而非 keybd_event：后者已废弃，也进不了 SendInput 的原子批次。
    # 顺序必须是 Ctrl↓ C↓ C↑ Ctrl↑，先松 C 再松 Ctrl，否则会残留出组合键。
    _send(_key(VK_CONTROL))
    time.sleep(0.02)
    _send(_key(VK_C))
    time.sleep(0.02)
    _send(_key(VK_C, up=True))
    time.sleep(0.02)
    _send(_key(VK_CONTROL, up=True))


def esc_pressed() -> bool:
    """Esc 是否处于按下状态（每轮采集开始时查一次，用作人工急停）。

    用 GetAsyncKeyState 而不是注册全局热键：采集期间本进程没有前台窗口，
    RegisterHotKey 在不抢焦点的情况下收不到消息。
    """
    return bool(user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)


def cursor_pos() -> Tuple[int, int]:
    """当前鼠标像素坐标；采集结束后靠它把光标放回原处。"""
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def set_dpi_aware() -> str:
    """必须**先**开 DPI 感知，否则 125%/150% 缩放下窗口矩形与真实像素不一致，拖拽会偏。"""
    try:
        # PER_MONITOR_AWARE_V2 = -4；Win10 1703 之前没有这个 API，取属性会抛
        # AttributeError，落到下面的 shcore 分支。
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except AttributeError:
        pass
    # 第二档：Win8.1 的 SetProcessDpiAwareness(2) = per-monitor。仍然失败就
    # 只能返回 system-default，调用方据此知道坐标可能有偏差。
    try:
        shcore = ctypes.WinDLL("shcore")
        if shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except OSError:
        pass
    return "system-default"


# ---------------------------------------------------------------- 窗口

def _proc_name(pid: int) -> str:
    """按 pid 取可执行文件名；权限不足时返回空串而不是抛异常。

    枚举窗口时会碰到大量系统进程（UWP 宿主等），对它们 OpenProcess 必然失败，
    这里必须容错，否则一次枚举就被一个无关进程打断。
    """
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
    finally:
        kernel32.CloseHandle(h)
    return ""


def find_windows(proc: str) -> List[dict]:
    """枚举属于 proc 的可见顶层窗口，按面积从大到小返回。

    只要可见且有标题的窗口：企微会创建一堆 0x0 的隐藏辅助窗口，不过滤就会
    在结果里排出一串没法拖拽的 hwnd。按面积排序则让主窗口排在第一位，
    弹窗（小程序、设置）自然落到后面。
    """
    found: List[dict] = []
    target = proc.lower()

    # 回调返回 True 表示"继续枚举"，返回 False 会中止整个 EnumWindows。
    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = _proc_name(pid.value)
        if name.lower() != target:
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        found.append({
            "hwnd": int(hwnd),
            "title": buf.value,
            "pid": pid.value,
            "proc": name,
            "rect": [rect.left, rect.top, rect.right, rect.bottom],
            "width": rect.right - rect.left,
            "height": rect.bottom - rect.top,
            "foreground": int(hwnd) == int(user32.GetForegroundWindow() or 0),
            "minimized": bool(user32.IsIconic(hwnd)),
        })
        return True

    user32.EnumWindows(cb, 0)
    found.sort(key=lambda w: w["width"] * w["height"], reverse=True)
    return found


def activate(hwnd: int, maximize: bool = True) -> None:
    """把窗口拉到前台并最大化。

    Windows 只允许**前台线程**改前台窗口，所以先把自己 attach 到当前前台线程，
    否则 SetForegroundWindow 会被静默忽略（这是本仓库 RPA 类比实现的高频坑）。
    """
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    my_thread = kernel32.GetCurrentThreadId()
    attached = False
    if fg_thread and fg_thread != my_thread:
        attached = bool(user32.AttachThreadInput(fg_thread, my_thread, True))
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        if maximize:
            user32.ShowWindow(hwnd, SW_MAXIMIZE)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        # 必须解绑：attach 会把两个线程的输入队列临时合并，不解绑会让后续
        # SetForegroundWindow 的权限判断也跟着被别人牵着走。
        if attached:
            user32.AttachThreadInput(fg_thread, my_thread, False)
    time.sleep(0.6)


def window_rect(hwnd: int) -> Tuple[int, int, int, int]:
    """窗口在屏幕坐标系里的 (left, top, right, bottom)。"""
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def focus_info() -> str:
    """当前前台线程的焦点窗口 —— 用来判断键盘事件到底会落到谁身上。

    第一次真机采集里 PageUp 完全没有效果，最可能就是事件没落到消息列表上，
    所以把焦点窗口的类名打出来当证据。
    """
    gti = GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not user32.GetGUIThreadInfo(0, ctypes.byref(gti)):
        return "(GetGUIThreadInfo 失败 err={})".format(ctypes.get_last_error())
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(gti.hwndFocus, cls, 256)
    return "hwndFocus={} class={!r} hwndActive={}".format(
        gti.hwndFocus, cls.value, gti.hwndActive)


def save_placement(hwnd: int) -> WINDOWPLACEMENT:
    """记录窗口当前的最大化/还原状态与还原后的矩形，采集结束原样恢复。"""
    buf = WINDOWPLACEMENT()
    buf.length = ctypes.sizeof(WINDOWPLACEMENT)
    user32.GetWindowPlacement(hwnd, ctypes.byref(buf))
    return buf


def restore_placement(hwnd: int, buf: WINDOWPLACEMENT) -> None:
    """把 save_placement 记下的最大化/还原状态与矩形写回窗口。"""
    # 两个 API 都要求调用方先填 length：它是结构体的版本号，填 0 会被直接拒绝，
    # 而且没有任何报错。
    buf.length = ctypes.sizeof(WINDOWPLACEMENT)
    user32.SetWindowPlacement(hwnd, ctypes.byref(buf))


# ---------------------------------------------------------------- 剪贴板

def read_clipboard(retries: int = 12) -> str:
    """读 Unicode 文本。剪贴板经常被别的进程短暂占用，所以必须重试。"""
    # 逐步重试而不是等待某个事件：OpenClipboard 失败说明别的进程正在占用，
    # 这类占用通常只有几十毫秒，累计重试约 1.4 秒足够覆盖。
    for _ in range(retries):
        if user32.OpenClipboard(None):
            try:
                handle = user32.GetClipboardData(CF_UNICODETEXT)
                if not handle:
                    return ""
                # 返回值必须是 64 位指针（见文件头 restype 说明），截断会读崩进程；
                # 解锁之后再访问就是野指针，所以必须先复制成 Python 字符串。
                ptr = kernel32.GlobalLock(handle)
                if not ptr:
                    return ""
                try:
                    return ctypes.wstring_at(ptr)
                finally:
                    kernel32.GlobalUnlock(handle)
            finally:
                user32.CloseClipboard()
        time.sleep(0.12)
    return ""


def beep(times: int = 1, freq: int = 880, dur: int = 180) -> None:
    """提示音：无音频设备（远程桌面、静音服务器）时退回控制台响铃。"""
    try:
        import winsound
        for i in range(times):
            winsound.Beep(freq, dur)
            if i + 1 < times:
                time.sleep(0.18)
    except Exception:
        print("\a", end="", flush=True)


# ---------------------------------------------------------------- 拖选

def drag_select(x: int, y_from: int, y_to: int, steps: int = 24,
                hold: bool = False) -> None:
    """在 (x, y_from) 按下左键，沿直线拖到 (x, y_to)。

    分步移动是必要的：一次跳到底，客户端可能只当成单次点击而不建立选区。

    hold=True 时**不松开**左键 —— 用户手工验证时描述的是"按住鼠标左键，然后按
    PageUp"，所以保留这条路径；默认（hold=False）是"拖选后松开，再靠 PageUp
    让选区自动向更早扩展"，与公开先例一致。两种模式哪条对，靠真机跑一轮的
    逐轮字符数变化来判断。
    """
    # 按下前后的停顿不是保守，是必需的：客户端要等鼠标进入"按下态"才允许
    # 建立选区，紧接着发移动事件会被当成误触过滤掉。
    mouse_move(x, y_from)
    time.sleep(0.25)
    mouse_down()
    time.sleep(0.30)
    for i in range(1, steps + 1):
        y = int(round(y_from + (y_to - y_from) * i / steps))
        mouse_move(x, y)
        time.sleep(0.012)
    # 松手前的停顿让最后一次移动完成选区渲染；松手后的停顿是给客户端把选区
    # 内容写进剪贴板之前的准备时间（后面的 ctrl_c 紧跟着发）。
    time.sleep(0.30)
    if not hold:
        mouse_up()
    time.sleep(0.45)

# ---------------------------------------------------------------- 采集主循环

class CollectRun:
    """一次采集的原始产物（未解析、未落盘）。

    batches 的每一项是 (轮次, 剪贴板原文)：第 0 轮是初始建立的选区，之后每轮
    是翻页/滚动后的选区。相邻批次**应当有重叠**（靠 core.wecom_clip 的 msg_id
    指纹去重），完全没有重叠就说明翻页跨过了整屏、中间有消息被跳过。
    """

    def __init__(self, hwnd: int, rect, dpi: str, screen, start, drag_px: int,
                 mode: str):
        # 连 dpi/screen/start 这些"参数而已"的元数据一起留着：同一份产物目录
        # 换台机器就复现不出来，报告里必须能看出当时的显示器与拖拽起点。

        self.hwnd = hwnd
        self.rect = rect
        self.dpi = dpi
        self.screen = screen
        self.start = start
        self.drag_px = drag_px
        self.mode = mode
        self.batches: List[Tuple[int, str]] = []
        self.aborted: Optional[str] = None

    @property
    def chars(self) -> List[int]:
        """逐轮剪贴板字符数。

        这是判断"有没有翻到头/有没有漏采"最可靠的信号：翻页成功后这个数会跳变，
        不变说明没滚动，变小说明选区被重置成了更短的一段。
        """
        return [len(text) for _idx, text in self.batches]

    def texts(self) -> List[Tuple[int, str]]:
        # 返回副本：调用方（采集脚本/测试）常会对列表排序或切片，直接给内部
        # 引用会让一次排序永久改掉批次的原始顺序。
        return list(self.batches)

    def __repr__(self) -> str:
        return "<CollectRun hwnd={} batches={} chars={}>".format(
            self.hwnd, len(self.batches), self.chars)


def collect_stream(hwnd: int, *, drag_px: int = 0, pages: int = 40,
                   mode: str = "pageup", notches: int = 4, jiggle: int = 14,
                   start: Optional[Tuple[int, int]] = None,
                   hover_delay: float = 6.0, page_wait: float = 0.7,
                   copy_wait: float = 0.45, stable: int = 3, hold: bool = False,
                   pre_delay: float = 0.0, maximize: bool = True,
                   log: Optional[Callable[[str], None]] = None) -> CollectRun:
    """把 hwnd 指向的会话窗口里的消息逐屏复制出来。

    调用前请自行 set_dpi_aware()（否则多屏不同缩放下窗口矩形与真实像素不一致，
    拖拽会偏）。返回 CollectRun；调用期间独占桌面，结束后会恢复窗口与鼠标位置。

    - drag_px：每屏向上拖拽的像素数；传 0 表示按"最大化后窗口高的 55%"自动算。
    - start：拖拽起点（消息列表底部中央的像素坐标）；None 表示蜂鸣后读鼠标位置。
    - mode：pageup = 按住左键 + PageUp + 微移鼠标；scroll = 滚轮翻页后重新拖选。
    - stable：连续多少轮剪贴板内容不变就认为翻到了最早一条；调小会在偶发
      的空复制上提前收工，调大只是多跑几轮空转，所以默认给 3 而不是 1。
    - hover_delay / page_wait / copy_wait：三处等待分别对应人工放置鼠标、
      客户端翻页渲染、复制到剪贴板；在慢机器上需要放开。
    - 任何时候按 Esc 会在当前轮结束后停下（run.aborted 记录原因）。
    """
    emit = log or (lambda _msg: None)
    dpi = set_dpi_aware()
    screen = screen_size()
    left, top, right, bottom = window_rect(hwnd)
    run = CollectRun(hwnd, (left, top, right, bottom), dpi, screen, start,
                     drag_px or default_drag_px(hwnd), mode)

    # 采集会最大化并抢占前台，四个快照是复原的唯一依据：前台窗口、光标位置、
    # 窗口还原态、采集前的剪贴板内容（判断第 0 轮是否压根没复制成功）。
    prev_fg = int(user32.GetForegroundWindow() or 0)
    prev_cursor = cursor_pos()
    prev_place = save_placement(hwnd)
    clipboard_before = read_clipboard()

    def _finish(reason: Optional[str]) -> CollectRun:
        # 每一步单独 try：恢复动作是"尽力而为"，任何一步失败都不能拦住后面的
        # 恢复。尤其鼠标左键——漏了 mouse_up 会把整个系统拖进拖拽态。
        run.aborted = reason
        try:
            mouse_up()
        except Exception:
            pass
        try:
            mouse_move(*prev_cursor)
        except Exception:
            pass
        try:
            restore_placement(hwnd, prev_place)
        except Exception:
            pass
        try:
            if prev_fg and prev_fg != hwnd:
                activate(prev_fg, maximize=False)
        except Exception:
            pass
        return run

    try:
        # 倒计时+蜂鸣是给人留出切换窗口的时间：一旦开始注入输入事件，桌面就
        # 被本进程接管，用户再想点什么都点不动了。
        if pre_delay > 0:
            emit(">>> 从现在起 {} 秒后接管桌面。请立刻切到目标群并滚到最新一条消息。"
                 .format(int(pre_delay)))
            beep(1, 520, 250)
            time.sleep(pre_delay)

        activate(hwnd, maximize=maximize)
        left, top, right, bottom = window_rect(hwnd)
        run.rect = (left, top, right, bottom)
        if not drag_px:
            # 必须在最大化之后再算，否则拖拽长度会按最大化前的小窗口算
            run.drag_px = default_drag_px(hwnd)
            emit("最大化后窗口 = {}x{} @({}, {})，拖拽长度调整为 {}px".format(
                right - left, bottom - top, left, top, run.drag_px))

        if start is None:
            emit("蜂鸣后 {} 秒内把鼠标移到目标群消息列表的底部中央…".format(hover_delay))
            beep(3)
            time.sleep(hover_delay)
            sx, sy = cursor_pos()
            emit("拖拽起点 = ({}, {})".format(sx, sy))
        else:
            sx, sy = start
            mouse_move(sx, sy)
            time.sleep(0.4)
        run.start = (sx, sy)

        # 第 0 轮：建立选区
        drag_select(sx, sy, sy - run.drag_px, hold=hold)
        time.sleep(0.5)
        ctrl_c()
        time.sleep(copy_wait)
        text = read_clipboard()
        previous = text
        run.batches.append((0, text))
        emit("第 0 轮（建立选区）：{} 字符".format(len(text)))
        # 第 0 轮的空剪贴板是"起点错误"的强信号（拖到了空白区或侧边栏），
        # 必须立刻提示，否则后面每一轮都会拿到同样的空文本，白跑几十轮。
        if not text.strip():
            emit("  ⚠ 剪贴板为空 —— 拖拽起点可能没落在消息文本上。")
        elif text == clipboard_before:
            emit("  ⚠ 剪贴板内容和采集前完全一样（可能是刚才手工复制过同一段，未必是失败）。")
        emit("焦点窗口：" + focus_info())

        band_top = sy - run.drag_px
        band_mid = (sy + band_top) // 2
        n_stable = 0
        reason = None
        for rnd in range(1, pages + 1):
            if esc_pressed():
                reason = "用户按了 Esc（第 {} 轮）".format(rnd)
                break
            if mode == "pageup":
                key_tap(VK_PRIOR)
                time.sleep(page_wait)
                if hold:
                    # 用户实测：PageUp 之后必须再"按住左键移动一下"，选区才会
                    # 扩展到新露出的消息（只按 PageUp 不动鼠标，选区会一直冻住）。
                    # 每轮落在略微不同的 y 上，保证坐标真的变了。
                    off = jiggle if (rnd % 2) else -jiggle
                    mouse_move(sx, band_top + off)
                    time.sleep(page_wait)
            else:
                # scroll 模式：滚轮往上翻一段，再**重新拖选**同一段
                advance_up(sx, band_mid, notches)
                time.sleep(page_wait)
                drag_select(sx, band_top, sy)
                time.sleep(0.2)
            ctrl_c()
            time.sleep(copy_wait)
            text = read_clipboard()
            # 用"连续多轮完全不变"判定终点，而不是判断某些关键词：消息正文
            # 千变万化，只有剪贴板停止变化是唯一稳定的收敛条件。
            if text == previous:
                n_stable += 1
            else:
                n_stable = 0
            run.batches.append((rnd, text))
            emit("第 {} 轮：{} 字符（{:+d}）连续不变 {}".format(
                rnd, len(text), len(text) - len(previous), n_stable))
            previous = text
            if n_stable >= stable:
                emit("连续 {} 轮剪贴板内容不变，认为已到最早一条。".format(n_stable))
                break
        else:
            # for 的 else 只在未 break 时执行：说明 stable 判据一直没满足，
            # 是翻页上限把循环停下来的，上限可能要调大。
            reason = "达到翻页上限 {}".format(pages)
        return _finish(reason)
    except BaseException:
        # 捕获 BaseException 是为了 KeyboardInterrupt 也能复原窗口和鼠标；
        # 复原完原样抛出，不吞掉中断信号。
        _finish("异常中断")
        raise
