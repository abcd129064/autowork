# -*- coding: utf-8 -*-
"""Windows DLL 函数声明：进程挂起/恢复、显示设置、窗口嵌入（mstsc）"""

import sys
import ctypes

_k32 = ctypes.WinDLL('kernel32')
_OpenProcess = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)(
    ('OpenProcess', _k32))
_CreateToolhelp32Snapshot = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong)(
    ('CreateToolhelp32Snapshot', _k32))
_Thread32First = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(
    ('Thread32First', _k32))
_Thread32Next = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(
    ('Thread32Next', _k32))
_OpenThread = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)(
    ('OpenThread', _k32))
_SuspendThread = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(
    ('SuspendThread', _k32))
_ResumeThread = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(
    ('ResumeThread', _k32))
_CloseHandle = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p)(
    ('CloseHandle', _k32))
_dwm = ctypes.WinDLL('dwmapi')
_DwmSetWindowAttribute = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong)(
    ('DwmSetWindowAttribute', _dwm))

# ===== 显示设置 API（用于三端启动前捕获分辨率 / 关闭后恢复） =====
_user32 = ctypes.WinDLL('user32')

class DEVMODE(ctypes.Structure):
    """Windows DEVMODEW 结构体（220 字节）。
    字段偏移经本机实测校准：用 256 字节缓冲区调用 EnumDisplaySettingsW，
    确认 dmBitsPerPel@168 / dmPelsWidth@172 / dmPelsHeight@176 /
    dmDisplayFlags@180 / dmDisplayFrequency@184。
    关键点：dmFields 与 dmFormName 之间的 union 区域实际占 24 字节（76-99），
    而非文档表面的 16 字节，故需显式填充，否则后续字段整体错位 8 字节。"""
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),    # 0-63
        ("dmSpecVersion", ctypes.c_ushort),        # 64
        ("dmDriverVersion", ctypes.c_ushort),      # 66
        ("dmSize", ctypes.c_ushort),               # 68
        ("dmDriverExtra", ctypes.c_ushort),        # 70
        ("dmFields", ctypes.c_ulong),              # 72
        # union 区域（打印字段组 / 显示字段组），实测占 24 字节（76-99）
        ("dmOrientation", ctypes.c_short),         # 76
        ("dmPaperSize", ctypes.c_short),           # 78
        ("dmPaperLength", ctypes.c_short),         # 80
        ("dmPaperWidth", ctypes.c_short),          # 82
        ("dmScale", ctypes.c_short),               # 84
        ("dmCopies", ctypes.c_short),              # 86
        ("dmDefaultSource", ctypes.c_short),       # 88
        ("dmPrintQuality", ctypes.c_short),        # 90
        ("_union_pad", ctypes.c_byte * 8),         # 92-99 填充至 24 字节
        ("dmFormName", ctypes.c_wchar * 32),       # 100-163
        ("dmLogPixels", ctypes.c_ushort),          # 164
        ("_logpixels_pad", ctypes.c_ushort),       # 166 对齐填充
        ("dmBitsPerPel", ctypes.c_ulong),          # 168
        ("dmPelsWidth", ctypes.c_ulong),           # 172
        ("dmPelsHeight", ctypes.c_ulong),          # 176
        ("dmDisplayFlags", ctypes.c_ulong),        # 180
        ("dmDisplayFrequency", ctypes.c_ulong),    # 184
        ("dmICMMethod", ctypes.c_ulong),           # 188
        ("dmICMIntent", ctypes.c_ulong),           # 192
        ("dmMediaType", ctypes.c_ulong),           # 196
        ("dmDitherType", ctypes.c_ulong),          # 200
        ("dmReserved1", ctypes.c_ulong),           # 204
        ("dmReserved2", ctypes.c_ulong),           # 208
        ("dmPanningWidth", ctypes.c_ulong),        # 212
        ("dmPanningHeight", ctypes.c_ulong),       # 216
    ]  # sizeof = 220

_EnumDisplaySettingsW = ctypes.WINFUNCTYPE(
    ctypes.c_int, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.POINTER(DEVMODE))(
    ('EnumDisplaySettingsW', _user32))
_ChangeDisplaySettingsW = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.POINTER(DEVMODE), ctypes.c_ulong)(
    ('ChangeDisplaySettingsW', _user32))

# 显示设置常量
ENUM_CURRENT_SETTINGS = 0xFFFFFFFF   # (DWORD)-1：当前生效的显示模式
CDS_UPDATEREGISTRY = 0x1             # 写入注册表（持久化）
CDS_FULLSCREEN = 0x4                 # 全屏模式切换
DISP_CHANGE_SUCCESSFUL = 0
DM_BITSPERPEL = 0x40000
DM_PELSWIDTH = 0x80000
DM_PELSHEIGHT = 0x100000
DM_DISPLAYFREQUENCY = 0x400000

# ===== 窗口嵌入 API（用于远程桌面 mstsc.exe 窗口嵌入） =====
_user32.SetParent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_user32.SetParent.restype = ctypes.c_void_p
_user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
_user32.SetWindowLongW.restype = ctypes.c_long
_user32.MoveWindow.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, ctypes.c_bool]
_user32.MoveWindow.restype = ctypes.c_bool
_user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
_user32.IsWindowVisible.restype = ctypes.c_bool
_user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
_user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
_user32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_user32.EnumWindows.restype = ctypes.c_bool
_user32.IsWindow.argtypes = [ctypes.c_void_p]
_user32.IsWindow.restype = ctypes.c_bool

GWL_STYLE = -16
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

# SetParent 专用版本（自动捕获 GetLastError）：
# 必须通过 use_last_error=True 的 user32 实例调用，
# 调用后可用 ctypes.get_last_error() 获取错误码
_user32_err = ctypes.WinDLL('user32', use_last_error=True)
_SetParent_err = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
    ('SetParent', _user32_err))

# 跨进程嵌入辅助 API
_user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
_user32.GetClassNameW.restype = ctypes.c_int
_user32.AttachThreadInput.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_bool]
_user32.AttachThreadInput.restype = ctypes.c_bool
_user32.GetForegroundWindow.argtypes = []
_user32.GetForegroundWindow.restype = ctypes.c_void_p
_k32.GetCurrentThreadId = ctypes.WINFUNCTYPE(ctypes.c_ulong)(('GetCurrentThreadId', _k32))

# mstsc 窗口类名常量
RDP_WINDOW_CLASS = 'TscShellContainerClass'  # 经典会话容器（Win10/早期Win11）
# 已知的 mstsc 会话窗口类名（可安全嵌入）
RDP_SESSION_CLASSES = frozenset({
    'TscShellContainerClass',   # 经典远程桌面客户端主容器
    'RAIL_WINDOW',              # RemoteApp / 部分 Win11 24H2+ 会话窗口
})
# 已知的 mstsc 临时/辅助窗口类名（绝对不能嵌入，会被销毁）
RDP_HELPER_CLASSES = frozenset({
    'BBarWindowClass',          # 连接状态栏（临时，连接后销毁）
    '#32770',                    # 系统通用对话框类：安全警告/凭据输入等弹窗，
                                 # 嵌入后真正的会话画面进不来，绝对不能嵌入
})

class _RECT(ctypes.Structure):
    """Windows RECT 结构（GetWindowRect 用）"""
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]

_user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RECT)]
_user32.GetWindowRect.restype = ctypes.c_bool
_user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
_user32.GetWindowTextW.restype = ctypes.c_int

# 进程快照 API（用于发现所有 mstsc.exe 进程，覆盖 Win11 进程委托场景）
_Process32FirstW = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(
    ('Process32FirstW', _k32))
_Process32NextW = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(
    ('Process32NextW', _k32))
TH32CS_SNAPPROCESS = 0x00000002

class _PROCESSENTRY32W(ctypes.Structure):
    """进程快照条目结构（Toolhelp32，szExeFile 为进程名）"""
    _fields_ = [
        ('dwSize', ctypes.c_ulong),
        ('cntUsage', ctypes.c_ulong),
        ('th32ProcessID', ctypes.c_ulong),
        ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)),
        ('th32ModuleID', ctypes.c_ulong),
        ('cntThreads', ctypes.c_ulong),
        ('th32ParentProcessID', ctypes.c_ulong),
        ('pcPriClassBase', ctypes.c_long),
        ('dwFlags', ctypes.c_ulong),
        ('szExeFile', ctypes.c_wchar * 260),
    ]

def get_window_class_name(hwnd):
    """获取窗口类名（失败返回空字符串）"""
    try:
        buf = ctypes.create_unicode_buffer(256)
        if _user32.GetClassNameW(hwnd, buf, 256):
            return buf.value
    except Exception:
        pass
    return ''

def _get_window_title(hwnd, max_len=128):
    """获取窗口标题（失败返回空字符串）"""
    try:
        buf = ctypes.create_unicode_buffer(max_len)
        _user32.GetWindowTextW(hwnd, buf, max_len)
        return buf.value
    except Exception:
        return ''

def get_process_name(pid):
    """按 PID 获取进程名（小写，失败返回空字符串）"""
    try:
        snap = _CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == ctypes.c_void_p(-1).value:
            return ''
        pe = _PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        name = ''
        if _Process32FirstW(snap, ctypes.byref(pe)):
            while True:
                if pe.th32ProcessID == pid:
                    name = pe.szExeFile.lower()
                    break
                if not _Process32NextW(snap, ctypes.byref(pe)):
                    break
        _CloseHandle(snap)
        return name
    except Exception:
        return ''

def find_mstsc_pids():
    """查找系统中所有 mstsc.exe 进程的 PID 列表。

    Windows 11 24H2+ 的 mstsc 可能将会话委托给另一个
    mstsc 子进程（PID 与启动进程不同），此函数用于发现它们。
    """
    pids = []
    try:
        snap = _CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == ctypes.c_void_p(-1).value:
            return pids
        pe = _PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        if _Process32FirstW(snap, ctypes.byref(pe)):
            while True:
                if pe.szExeFile.lower() == 'mstsc.exe':
                    pids.append(pe.th32ProcessID)
                if not _Process32NextW(snap, ctypes.byref(pe)):
                    break
        _CloseHandle(snap)
    except Exception:
        pass
    return pids

def _score_rdp_window(hwnd, cls):
    """评估窗口是否可能是 RDP 会话窗口（分数越高越可能）。

    策略：白名单类名直接超高分（必须压过任何面积分）；
    黑名单（临时辅助窗口/对话框）0 分；未知类名按窗口面积评分
    ——Win11 25H2 的会话窗口类名可能与经典值不同，但会话窗口
    一定足够大（≥640x480）。
    """
    if cls in RDP_SESSION_CLASSES:
        return 10 ** 9  # 白名单类名无条件胜出（面积分最大不过数千万）
    if cls in RDP_HELPER_CLASSES:
        return 0
    rect = _RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return 0
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w >= 640 and h >= 480:
        return w * h  # 面积越大越可能是会话窗口
    return 0

def _enum_windows_of_pid(pid):
    """枚举指定进程的所有可见顶层窗口，返回带诊断信息的列表"""
    wins = []

    def _enum_cb(hwnd, _lparam):
        try:
            wpid = ctypes.c_ulong()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
            if wpid.value == pid and _user32.IsWindowVisible(hwnd):
                cls = get_window_class_name(hwnd)
                rect = _RECT()
                _user32.GetWindowRect(hwnd, ctypes.byref(rect))
                wins.append({
                    'hwnd': hwnd, 'class': cls,
                    'title': _get_window_title(hwnd),
                    'w': rect.right - rect.left,
                    'h': rect.bottom - rect.top,
                    'score': _score_rdp_window(hwnd, cls),
                })
        except Exception:
            pass
        return True  # 枚举全部

    try:
        _user32.EnumWindows(_WNDENUMPROC(_enum_cb), 0)
    except Exception:
        pass
    return wins

def find_rdp_window_by_pid(pid, log=None):
    """按 PID 查找 mstsc 会话窗口（评分制 + 诊断日志）。

    返回该进程得分最高的窗口；所有候选窗口均无有效得分
    （全是辅助窗口或尺寸过小）时返回 None。
    传入 log 回调可输出诊断信息，帮助定位正确的会话窗口类名。
    """
    wins = _enum_windows_of_pid(pid)
    if not wins:
        return None
    wins.sort(key=lambda w: w['score'], reverse=True)
    if log:
        for w in wins:
            log(f"[RDP诊断] pid={pid} hwnd=0x{w['hwnd']:X} "
                f"class={w['class']!r} title={w['title']!r} "
                f"size={w['w']}x{w['h']} score={w['score']}")
    return wins[0]['hwnd'] if wins[0]['score'] > 0 else None

def find_rdp_session_window(log=None):
    """全局查找 mstsc 会话窗口（不依赖启动 PID）。

    Windows 11 24H2+ 的 mstsc 可能将会话委托给另一个
    mstsc 子进程。此函数枚举所有可见顶层窗口，仅保留
    属于 mstsc.exe 进程的窗口，按评分返回最佳候选。
    """
    mstsc_pids = set(find_mstsc_pids())
    if not mstsc_pids:
        return None
    wins = []

    def _enum_cb(hwnd, _lparam):
        try:
            if not _user32.IsWindowVisible(hwnd):
                return True
            wpid = ctypes.c_ulong()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
            if wpid.value not in mstsc_pids:
                return True
            cls = get_window_class_name(hwnd)
            rect = _RECT()
            _user32.GetWindowRect(hwnd, ctypes.byref(rect))
            wins.append({
                'hwnd': hwnd, 'pid': wpid.value, 'class': cls,
                'title': _get_window_title(hwnd),
                'w': rect.right - rect.left,
                'h': rect.bottom - rect.top,
                'score': _score_rdp_window(hwnd, cls),
            })
        except Exception:
            pass
        return True  # 枚举全部

    try:
        _user32.EnumWindows(_WNDENUMPROC(_enum_cb), 0)
    except Exception:
        pass
    if not wins:
        return None
    wins.sort(key=lambda w: w['score'], reverse=True)
    if log:
        for w in wins:
            log(f"[RDP诊断-全局] pid={w['pid']} hwnd=0x{w['hwnd']:X} "
                f"class={w['class']!r} title={w['title']!r} "
                f"size={w['w']}x{w['h']} score={w['score']}")
    return wins[0]['hwnd'] if wins[0]['score'] > 0 else None

def win_set_process_threads(pid, thread_action):
    """枚举指定进程的全部线程并逐个执行 thread_action（挂起/恢复句柄函数），
    返回是否成功"""
    PROCESS_SUSPEND_RESUME = 0x0800
    THREAD_SUSPEND_RESUME = 0x0002
    TH32CS_SNAPTHREAD = 0x00000004

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ThreadID", ctypes.c_ulong),
            ("th32OwnerProcessID", ctypes.c_ulong),
            ("tpBasePri", ctypes.c_long),
            ("tpDeltaPri", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
        ]

    h_process = _OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
    if not h_process:
        return False
    try:
        snap = _CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
        if snap == -1:
            return False
        te = THREADENTRY32()
        te.dwSize = ctypes.sizeof(THREADENTRY32)
        if _Thread32First(snap, ctypes.byref(te)):
            while True:
                if te.th32OwnerProcessID == pid:
                    h_thread = _OpenThread(THREAD_SUSPEND_RESUME, False, te.th32ThreadID)
                    if h_thread:
                        thread_action(h_thread)
                        _CloseHandle(h_thread)
                if not _Thread32Next(snap, ctypes.byref(te)):
                    break
        _CloseHandle(snap)
        return True
    finally:
        _CloseHandle(h_process)

def win_suspend_process(pid):
    """挂起指定进程的所有线程"""
    return win_set_process_threads(pid, _SuspendThread)

def win_resume_process(pid):
    """恢复指定进程的所有线程"""
    return win_set_process_threads(pid, _ResumeThread)
