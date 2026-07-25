# -*- coding: utf-8 -*-
"""Windows DLL 函数声明：进程挂起/恢复、显示设置、窗口嵌入（mstsc）"""

import sys
import ctypes

# 仅在 Windows 平台声明，其他平台导出空占位
if sys.platform == 'win32':
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

    def find_window_by_pid(pid):
        """按进程 PID 查找第一个可见的顶层窗口句柄（用于嵌入 mstsc 窗口）"""
        found = []

        def _enum_cb(hwnd, _lparam):
            # 回调运行在 C 栈上，任何异常都必须就地吞掉，
            # 否则异常穿透 EnumWindows 会导致进程崩溃
            try:
                wpid = ctypes.c_ulong()
                _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
                if wpid.value == pid and _user32.IsWindowVisible(hwnd):
                    found.append(hwnd)
                    return False  # 停止枚举
            except Exception:
                pass
            return True

        try:
            _user32.EnumWindows(_WNDENUMPROC(_enum_cb), 0)
        except Exception:
            pass
        return found[0] if found else None

    def win_set_process_threads(pid, thread_action):
        """Windows API: 对指定进程的所有线程执行操作（挂起/恢复）

        参数:
            pid: 目标进程 ID
            thread_action: 对每个线程句柄调用的函数（_SuspendThread 或 _ResumeThread）
        返回:
            bool: 操作是否成功
        """
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
        """Windows API: 挂起指定进程的所有线程"""
        return win_set_process_threads(pid, _SuspendThread)

    def win_resume_process(pid):
        """Windows API: 恢复指定进程的所有线程"""
        return win_set_process_threads(pid, _ResumeThread)

else:
    # 非 Windows 平台占位，避免导入报错
    DEVMODE = None
    find_window_by_pid = None
    win_suspend_process = None
    win_resume_process = None
