# -*- coding: utf-8 -*-
"""Windows DLL 函数声明：进程挂起/恢复、显示设置、窗口嵌入（mstsc）

集中声明 ctypes 原型而不是散在调用点：句柄一律按 c_void_p 传，若用默认的
c_int 返回类型，64 位下句柄会被截成 32 位，SetParent/SuspendThread 会随机
失败且不报错。本模块只在 Windows 上导入。
"""

import sys
import ctypes

# 每个函数在导入期就绑定成 WINFUNCTYPE 对象，调用点不再走 ctypes 的属性查找；
# 参数类型也在模块加载时暴露，而不是等真机跑到才发现声明写错
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
# DwmSetWindowAttribute 用于给嵌入窗口设暗色边框/圆角，HWND 按指针原样传
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
# 调用方必须把 DEVMODE.dmSize 填成结构体大小，否则系统按旧版结构解析，
# 后面的分辨率字段全部读成 0

# 显示设置常量
ENUM_CURRENT_SETTINGS = 0xFFFFFFFF   # (DWORD)-1：当前生效的显示模式
CDS_UPDATEREGISTRY = 0x1             # 写入注册表（持久化）
CDS_FULLSCREEN = 0x4                 # 全屏模式切换
DISP_CHANGE_SUCCESSFUL = 0        # 只有返回 0 才算切换成功，负值是失败码
DM_BITSPERPEL = 0x40000
# dmFields 的四个位：不置位时 ChangeDisplaySettingsW 会忽略对应字段，
# 表现为「设置了分辨率但屏幕没变」
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

GWL_STYLE = -16              # SetWindowLongW 取「窗口样式」的索引
WS_CAPTION = 0x00C00000      # 标题栏位掩码：嵌入前清掉，免得 mstsc 自带标题条
WS_THICKFRAME = 0x00040000   # 可拖边框位：留着会让用户拖拽撕掉父子窗口关系
# 回调对象必须活到 EnumWindows 返回：临时构造的 WINFUNCTYPE 一旦被 GC，
# 系统侧持有的裸函数指针就指向已释放内存
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
    """Windows RECT 结构（GetWindowRect 用）

    字段是 LONG 不是 int；多显示器时窗口坐标可能为负（副屏在主屏左侧），
    用无符号类型会得到天文数字。
    """
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
    """获取窗口类名（失败返回空字符串）

    窗口可能在枚举与取类名之间被销毁，这条路径服务于 mstsc 嵌入判断，
    不能因为一个失效 HWND 抛异常把整个查找流程打断。
    """
    try:
        buf = ctypes.create_unicode_buffer(256)
        if _user32.GetClassNameW(hwnd, buf, 256):
            return buf.value
    except Exception:
        pass
    return ''

def _get_window_title(hwnd, max_len=128):
    """获取窗口标题（失败返回空字符串）——只进诊断日志，不参与选择"""
    try:
        buf = ctypes.create_unicode_buffer(max_len)
        _user32.GetWindowTextW(hwnd, buf, max_len)
        return buf.value
    except Exception:
        return ''

def get_process_name(pid):
    """按 PID 获取进程名（小写，失败返回空字符串）"""
    try:
        # 快照句柄用完必须 CloseHandle，否则每次查询都漏一个句柄，长跑必泄漏
        snap = _CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == ctypes.c_void_p(-1).value:
            return ''
        pe = _PROCESSENTRY32W()
        # dwSize 必须先填结构体大小，否则 Process32FirstW 直接返回 FALSE 查不到任何进程
        pe.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        name = ''
        if _Process32FirstW(snap, ctypes.byref(pe)):
            while True:
                if pe.th32ProcessID == pid:
                    name = pe.szExeFile.lower()
                    break
                if not _Process32NextW(snap, ctypes.byref(pe)):
                    break
        # 注意：中途异常会跳过这里，快照句柄会漏关，好在单次泄漏量小
        _CloseHandle(snap)
        return name
    except Exception:
        return ''

def find_mstsc_pids():
    """查找系统中所有 mstsc.exe 进程的 PID 列表。

    Windows 11 24H2+ 的 mstsc 可能将会话委托给另一个
    mstsc 子进程（PID 与启动进程不同），此函数用于发现它们。

    快照句柄在正常路径关闭；异常路径会漏一个句柄，故外层整体吞异常返回
    已有结果，宁可少一个句柄也不能让查找失败。
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
    # 白名单给一个远超面积分的常量：面积最大也就几百万，
    # 这样白名单类名永远压过「窗口够大」这条兜底判据
    if cls in RDP_SESSION_CLASSES:
        return 10 ** 9  # 白名单类名无条件胜出（面积分最大不过数千万）
    if cls in RDP_HELPER_CLASSES:
        return 0
    rect = _RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return 0
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    # 640x480 是经验下限：比这小的多半是托盘提示或登录子窗口，
    # 真正的会话画面一定铺得比这大；拿面积当分数是为了在新类名出现时兜底
    if w >= 640 and h >= 480:
        return w * h  # 面积越大越可能是会话窗口
    return 0

def _enum_windows_of_pid(pid):
    """枚举指定进程的所有可见顶层窗口，返回带诊断信息的列表

    只负责收集，不做挑选：评分与排序放在调用方，诊断日志才能打出全部候选，
    定位 Win11 新类名时靠的就是这份完整清单。
    """
    wins = []

    # 回调里逐个吞异常：枚举过程中窗口被销毁是常态，一条失败不该让整个查找
    # 白跑；返回 True 让 EnumWindows 继续走完剩余窗口
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

    # 枚举整体容错：权限不足或回调里漏网的异常不该让调用方崩，
    # 顶多是拿不到候选，由上层按「没找到窗口」处理
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
    # 先拿到所有 mstsc 进程的 PID：Win11 24H2+ 会话可能被委托给子进程，
    # 只认启动进程 PID 会漏掉真正的会话窗口
    mstsc_pids = set(find_mstsc_pids())
    if not mstsc_pids:
        return None
    wins = []

    # 与按 PID 收集是同一套逻辑，差别只在候选来自全部 mstsc 进程；
    # 提前 return True 跳过不可见窗口，少做两次无谓调用
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

    # 与上面同一套容错口径：全局枚举失败的代价只是找不到窗口，
    # 不能把异常抛给正在关闭中的调用方
    try:
        _user32.EnumWindows(_WNDENUMPROC(_enum_cb), 0)
    except Exception:
        pass
    # 没有候选不等于失败：进程可能刚起、窗口还没建出来，
    # 调用方要靠 None 走「再等等」的分支，不是走异常分支
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
    """枚举指定进程的全部线程并逐个执行 thread_action（挂起/恢复句柄函数）

    Windows 没有进程级挂起 API，只能拿线程快照逐个 SuspendThread；
    thread_action 直接接收 ctypes 函数对象（_SuspendThread/_ResumeThread）。
    """
    PROCESS_SUSPEND_RESUME = 0x0800
    THREAD_SUSPEND_RESUME = 0x0002
    TH32CS_SNAPTHREAD = 0x00000004

    # 结构体只在本函数用，就地定义：不污染模块命名空间，也不与系统头里的
    # 同名类型混淆（两者的 dwSize 必须与各自定义一致）
    class THREADENTRY32(ctypes.Structure):
        """Toolhelp 线程快照条目，字段顺序与类型必须和 Win32 定义逐字对齐"""
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ThreadID", ctypes.c_ulong),
            ("th32OwnerProcessID", ctypes.c_ulong),
            ("tpBasePri", ctypes.c_long),
            ("tpDeltaPri", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
        ]

    # 只需要挂起/恢复权限，实际操作都落在逐个线程句柄上
    h_process = _OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
    # 打开失败通常是进程已退出或权限不足；此时进程本来就「挂不起来」，
    # 直接返回失败，不要重试去抢权限
    if not h_process:
        return False
    try:
        # Windows 没有进程级挂起 API，只能枚举线程快照逐个 SuspendThread/ResumeThread
        snap = _CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
        # 坑：c_void_p 返回时 NULL 是 None、INVALID_HANDLE_VALUE 是正整数，
        # 这里 == -1 两者都匹配不到（见 get_process_name 的 c_void_p(-1).value 写法），
        # 快照创建失败时后续 Thread32First/Next 调用也得容错
        if snap == -1:
            return False
        te = THREADENTRY32()
        te.dwSize = ctypes.sizeof(THREADENTRY32)
        if _Thread32First(snap, ctypes.byref(te)):
            while True:
                # 线程快照含全系统所有线程，必须按属主 PID 过滤，否则会挂起别的进程
                if te.th32OwnerProcessID == pid:
                    h_thread = _OpenThread(THREAD_SUSPEND_RESUME, False, te.th32ThreadID)
                    if h_thread:
                        # 不检查返回值：线程可能在快照后已退出，单个线程失败
                        # 不影响其余线程，整体按「已尽力」返回成功
                        thread_action(h_thread)
                        _CloseHandle(h_thread)
                if not _Thread32Next(snap, ctypes.byref(te)):
                    break
        # 快照句柄遍历完必须关，否则每挂起/恢复一次就漏一个句柄
        _CloseHandle(snap)
        return True
    finally:
        _CloseHandle(h_process)

# 挂起必须与恢复一一配对：SuspendThread 是计数式的，多挂一次就得多恢复一次，
# 少恢复一次进程就永远醒不过来，表现为窗口无响应、连切窗都切不走
def win_suspend_process(pid):
    """挂起指定进程的所有线程；必须与 win_resume_process 一一配对。

    SuspendThread 对同一线程是计数而非布尔：多挂一次就得多恢复一次，
    否则进程再也醒不过来（表现为窗口永远无响应）。
    """
    return win_set_process_threads(pid, _SuspendThread)

# 每次调用只抵消一层挂起计数，所以「恢复」也要按同样的次数调用；
# 中途进程已经退出时由 win_set_process_threads 按已尽力返回，不在这里重试
def win_resume_process(pid):
    """恢复指定进程的所有线程（每次调用只抵消一层挂起计数）"""
    return win_set_process_threads(pid, _ResumeThread)
