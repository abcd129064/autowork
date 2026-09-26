# -*- coding: utf-8 -*-
"""性能选项管理模块 —— 细粒度运行时开关，切换即时生效，无需重启。

settings.json 字段：
  - "perf_acrylic":   true/false  亚克力磨砂效果（截屏→高斯模糊，核显开销大）
  - "perf_animation": true/false  菜单弹出动画（面板可经
      perf_animation_aftersale / perf_animation_video 单独覆盖）
  - "perf_table_smooth": true/false  TableWidget 平滑滚动动画（大表格逐帧
      重绘卡顿，默认 false=关闭走原生滚动；设置面板可开启）

兼容旧字段 "performance_mode": true → 自动迁移为两项均关闭。

用法：
    from core.perf import is_acrylic_enabled, is_animation_enabled
    # 弹出菜单/绘制时动态检查，开关切换后下一次弹出即生效
"""

from core import app_settings

# 模块级运行时状态（None = 尚未从配置门面加载）
_acrylic_enabled: bool | None = None
_animation_enabled: bool | None = None
_table_smooth_enabled: bool | None = None
_lean_delegate_enabled: bool | None = None

# 表格平滑滚动的「面板级覆盖」：面板开关单独影响各自面板，未设置则回退全局。
# key 为面板标识，value 为 settings.json 字段名。
_PANEL_TABLE_KEYS = {
    "aftersale": "perf_table_smooth_aftersale",   # 售后面板（windows/aftersale）
    "video":     "perf_table_smooth_video",       # 跑视频面板（windows/run_video）
    "management": "perf_table_smooth_management", # 管理面板（windows/management，4 处表格）
    "remote":    "perf_table_smooth_remote",      # 远程会话（windows/remote_session，2 处表格）
}
_panel_table_overrides: dict | None = None  # {panel: bool}，None=尚未加载

# 弹出动画的「面板级覆盖」：与表格平滑滚动同模型（未设置回退全局）。
_PANEL_ANIMATION_KEYS = {
    "aftersale": "perf_animation_aftersale",   # 售后面板（windows/aftersale）
    "video":     "perf_animation_video",       # 跑视频面板（windows/run_video）
}
_panel_animation_overrides: dict | None = None  # {panel: bool}，None=尚未加载

# 面板窗口类名 → 面板标识（菜单动画/弹窗动画中央补丁按父链识别所属面板用）
_PANEL_WINDOW_CLASSES = {
    "AftersalePanelWindow": "aftersale",
    "LedgerPanelWindow": "video",
    "ManagementPanelWindow": "management",
    "TunnelPanelWindow": "remote",
    "ConnDiagPanel": "remote",
}


def _load_perf_settings():
    """从配置门面 perf 域加载性能选项（首次调用时执行，含旧字段迁移）"""
    global _acrylic_enabled, _animation_enabled, _table_smooth_enabled
    global _lean_delegate_enabled
    acrylic, animation, table_smooth = True, True, False
    try:
        data = app_settings.get_domain("perf")
        if "perf_acrylic" in data:
            acrylic = bool(data["perf_acrylic"])
        elif data.get("performance_mode") in (True, "true"):
            acrylic = False  # 旧字段迁移
        if "perf_animation" in data:
            animation = bool(data["perf_animation"])
        elif data.get("performance_mode") in (True, "true"):
            animation = False  # 旧字段迁移
        if "perf_table_smooth" in data:
            table_smooth = bool(data["perf_table_smooth"])
        # 轻量表格委托（P0-1）：默认开启，关闭即回退库自带 delegate
        _lean = data.get("perf_lean_delegate", True)
        _lean = _lean if isinstance(_lean, bool) else str(_lean) != "false"
    except Exception:
        _lean = True
    _acrylic_enabled = acrylic
    _animation_enabled = animation
    _table_smooth_enabled = table_smooth
    _lean_delegate_enabled = _lean


def is_acrylic_enabled() -> bool:
    """亚克力效果是否启用（运行时即时读取）"""
    if _acrylic_enabled is None:
        _load_perf_settings()
    return _acrylic_enabled


def is_animation_enabled() -> bool:
    """菜单弹出动画是否启用（运行时即时读取）"""
    if _animation_enabled is None:
        _load_perf_settings()
    return _animation_enabled


def is_table_smooth_scroll_enabled() -> bool:
    """TableWidget 平滑滚动动画是否启用（默认关闭：大表格逐帧重绘卡顿）"""
    if _table_smooth_enabled is None:
        _load_perf_settings()
    return _table_smooth_enabled


def set_table_smooth_scroll_enabled(enabled: bool):
    """设置表格平滑滚动开关并立即持久化到 settings.json（即时生效）"""
    global _table_smooth_enabled
    _table_smooth_enabled = bool(enabled)
    _persist("perf_table_smooth", _table_smooth_enabled)


# ---------------- 面板级覆盖（单独影响各自面板，未设置回退全局） ----------------

def _load_panel_table_overrides():
    """从配置门面 perf 域加载各面板的平滑滚动覆盖值（未设置的面板不在 dict 中）"""
    global _panel_table_overrides
    ov = {}
    try:
        data = app_settings.get_domain("perf")
        for panel, key in _PANEL_TABLE_KEYS.items():
            if key in data:
                ov[panel] = bool(data[key])
    except Exception:
        pass
    _panel_table_overrides = ov


def get_table_smooth(panel: str | None = None) -> bool:
    """生效的表格平滑滚动：指定面板优先读其覆盖值，未设置回退全局开关"""
    if _table_smooth_enabled is None:
        _load_perf_settings()
    if panel:
        if _panel_table_overrides is None:
            _load_panel_table_overrides()
        if panel in _panel_table_overrides:
            return _panel_table_overrides[panel]
    return _table_smooth_enabled


def set_table_smooth(panel: str | None, enabled: bool | None):
    """设置面板级（panel 非空）或全局（panel=None）平滑滚动开关并持久化

    2026-09-07 语义扩展：panel 非空且 enabled=None → 清除该面板覆盖
    （运行时缓存与落盘键一并移除，回到跟随全局），供设置页「全部面板」
    主控联动使用（勾选/取消主控时清空覆盖，消除主控关而子项开的无意义组合）
    """
    global _panel_table_overrides
    if panel:
        if _panel_table_overrides is None:
            _load_panel_table_overrides()
        if enabled is None:
            _panel_table_overrides.pop(panel, None)
            key = _PANEL_TABLE_KEYS.get(panel)
            if key:
                try:
                    app_settings.remove(key)
                except Exception:
                    pass
            return
        enabled = bool(enabled)
        _panel_table_overrides[panel] = enabled
        key = _PANEL_TABLE_KEYS.get(panel)
        if key:
            _persist(key, enabled)
    else:
        set_table_smooth_scroll_enabled(bool(enabled))


def apply_table_smooth_mode(table, panel: str | None = None):
    """把当前生效的平滑滚动设置应用到 TableWidget（开=LINEAR / 关=NO_SMOOTH，即时）"""
    from qfluentwidgets import SmoothMode
    mode = SmoothMode.LINEAR if get_table_smooth(panel) else SmoothMode.NO_SMOOTH
    try:
        dlg = table.scrollDelagate
        dlg.verticalSmoothScroll.setSmoothMode(mode)
        hs = (getattr(dlg, "horizonSmoothScroll", None)
              or getattr(dlg, "horizontalSmoothScroll", None))
        if hs is not None:
            hs.setSmoothMode(mode)
    except Exception:
        pass


def apply_table_smooth_globally():
    """全局开关变更后刷新所有已打开窗口的表格滚动模式（各自按 覆盖→全局 生效）

    统一入口（按优先级逐个尝试）：
    1. 顶层窗口.records_page._apply_smooth_mode() —— 售后/跑视频面板
    2. 顶层窗口._apply_table_smooth_all() —— 管理面板窗口（遍历各子页表格）
    3. 顶层窗口._apply_smooth_mode() —— 隧道/连接诊断等独立窗口（单表格）
    """
    from PySide6.QtWidgets import QApplication
    for w in QApplication.topLevelWidgets():
        rp = getattr(w, "records_page", None)
        if rp is not None and hasattr(rp, "_apply_smooth_mode"):
            try:
                rp._apply_smooth_mode()
                continue
            except Exception:
                pass
        fn = getattr(w, "_apply_table_smooth_all", None)
        if fn is not None:
            try:
                fn()
                continue
            except Exception:
                pass
        fn2 = getattr(w, "_apply_smooth_mode", None)
        if fn2 is not None:
            try:
                fn2()
            except Exception:
                pass


def _load_panel_animation_overrides():
    """从配置门面 perf 域加载各面板的动画覆盖值（未设置的面板不在 dict 中）"""
    global _panel_animation_overrides
    ov = {}
    try:
        data = app_settings.get_domain("perf")
        for panel, key in _PANEL_ANIMATION_KEYS.items():
            if key in data:
                ov[panel] = bool(data[key])
    except Exception:
        pass
    _panel_animation_overrides = ov


def get_animation(panel: str | None = None) -> bool:
    """生效的弹出动画：指定面板优先读其覆盖值，未设置回退全局开关"""
    if _animation_enabled is None:
        _load_perf_settings()
    if panel:
        if _panel_animation_overrides is None:
            _load_panel_animation_overrides()
        if panel in _panel_animation_overrides:
            return _panel_animation_overrides[panel]
    return _animation_enabled


def set_animation(panel: str | None, enabled: bool):
    """设置面板级（panel 非空）或全局（panel=None）动画开关并持久化"""
    global _panel_animation_overrides
    enabled = bool(enabled)
    if panel:
        if _panel_animation_overrides is None:
            _load_panel_animation_overrides()
        _panel_animation_overrides[panel] = enabled
        key = _PANEL_ANIMATION_KEYS.get(panel)
        if key:
            _persist(key, enabled)
    else:
        set_animation_enabled(enabled)


def _window_panel_key(widget) -> str | None:
    """沿父链向上找所属面板窗口（主界面/其它窗口返回 None 走全局）"""
    w = widget
    while w is not None:
        panel = _PANEL_WINDOW_CLASSES.get(type(w).__name__)
        if panel:
            return panel
        w = w.parentWidget() if hasattr(w, "parentWidget") else None
    return None


def _menu_panel_key(menu) -> str | None:
    """沿菜单父链向上找所属面板窗口（主界面/其它窗口返回 None 走全局）"""
    return _window_panel_key(menu)


def patch_table_hover_repaint():
    """中央拦截 TableBase hover 重绘：鼠标扫过行时只重绘新旧两行条带（幂等）

    背景：qfluentwidgets TableBase._setHoverRow 在 hover 行变化时调用
    viewport().update()（无参=整视口重绘）。2K 分辨率 + 每页 50 行内嵌
    cellWidget（售后/跑视频操作列）时，鼠标每划过一行就整表重绘一次，
    与滚轮滚动的重绘叠加后是低配机掉帧的主因之一。改为仅 update 新旧
    两行的水平条带（行高固定 36/40px），重绘面积约降至 1/25；
    hover 高亮效果不变（delegate.paint 按 hoverRow 判断，逐格裁剪绘制）。
    """
    try:
        from qfluentwidgets.components.widgets.table_view import TableBase
    except Exception:
        return
    if getattr(TableBase, "_perf_hover_patched", False):
        return

    def _setHoverRow(self, row):
        old = self.delegate.hoverRow
        if old == row:
            return
        self.delegate.setHoverRow(row)
        vp = self.viewport()
        total = self.model().rowCount()
        for r in (old, row):
            if r is None or not 0 <= r < total:
                continue
            h = self.rowHeight(r)
            if h <= 0:
                continue
            y = self.rowViewportPosition(r)
            vp.update(0, y, vp.width(), h)

    TableBase._setHoverRow = _setHoverRow
    TableBase._perf_hover_patched = True


# ==================== 轻量表格委托（P0-1） ====================

def is_lean_delegate_enabled() -> bool:
    """是否启用轻量表格委托（默认开启；关闭即回退 qfluentwidgets 自带委托）"""
    if _lean_delegate_enabled is None:
        _load_perf_settings()
    return bool(_lean_delegate_enabled)


def set_lean_delegate_enabled(enabled: bool):
    """设置轻量委托开关并持久化；已打开的表格一并切换（即时生效）"""
    global _lean_delegate_enabled
    _lean_delegate_enabled = bool(enabled)
    _persist("perf_lean_delegate", _lean_delegate_enabled)
    apply_lean_delegate_globally()


def patch_lean_table_delegate():
    """新建的 qfluentwidgets 表格自动挂轻量委托（幂等，启动时调用一次）

    背景：库 ``TableBase.__init__`` 里硬编码 ``setItemDelegate(
    TableItemDelegate(self))``，无法从外部预置；这里在原始 __init__ 之后
    换成 ``LeanTableDelegate``（其子类，保留全部视觉与能力）。
    """
    try:
        from qfluentwidgets.components.widgets.table_view import TableBase
    except Exception:
        return
    if getattr(TableBase, "_perf_lean_patched", False):
        return

    _orig_init = TableBase.__init__

    def _init(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        try:
            if is_lean_delegate_enabled():
                from core.lean_table_delegate import LeanTableDelegate
                self.setItemDelegate(LeanTableDelegate(self))
        except Exception:
            pass

    TableBase.__init__ = _init
    TableBase._perf_lean_patched = True


def apply_lean_delegate_globally():
    """按当前开关刷新所有已存在表格的委托（设置页切换后立即生效）"""
    try:
        from PySide6.QtWidgets import (QApplication, QTableView,
                                       QTableWidget)
        from qfluentwidgets.components.widgets.table_view import (
            TableBase, TableItemDelegate)
        from core.lean_table_delegate import LeanTableDelegate
    except Exception:
        return

    enabled = is_lean_delegate_enabled()
    try:  # 操作列文字链接委托（core.ops_link_delegate）需随开关换形态
        from core.ops_link_delegate import rebuild_ops_delegate
    except Exception:
        rebuild_ops_delegate = None
    seen = set()
    tables = []
    for w in QApplication.topLevelWidgets():
        for cls in (QTableWidget, QTableView):
            for t in w.findChildren(cls):
                if id(t) not in seen and isinstance(t, TableBase):
                    seen.add(id(t))
                    tables.append(t)

    for t in tables:
        try:
            # 装了操作链接委托的表：由该模块按新开关重建同族委托后跳过，
            # 否则下面的通用重建会把链接绘制顶掉（表现为操作列变空白）
            if rebuild_ops_delegate is not None and rebuild_ops_delegate(t):
                t.viewport().update()
                continue
            is_lean = isinstance(t.delegate, LeanTableDelegate)
            if enabled and not is_lean:
                t.setItemDelegate(LeanTableDelegate(t))
            elif not enabled and is_lean:
                t.setItemDelegate(TableItemDelegate(t))
            t.viewport().update()
        except Exception:
            pass


# ==================== 大屏/超高 DPI 自动降级（2026-09-25 P0） ====================
# 背景：docs/大屏与超高DPI渲染性能调查报告2026-09-25.md —— qfw 四条逐帧
# 整窗渲染路径（弹窗 opacity 动画 / 页面切换 / 菜单 setMask / 亚克力模糊）
# 的每帧成本 ≈ 线性于「物理像素数 = 逻辑尺寸 × 屏幕 DPR²」，829 万像素
# （4K@150% 全屏）时弹窗单帧 ≥48ms，真机核显再放大 → 事件泵饥饿「未响应」。
# 策略：阈值以下保持库原生体验，阈值以上自动降级保可用性。
# 阈值可配（perf 域 `perf_dpi_degrade_pixels`，默认 600 万，0 = 关闭自动降级），
# 为 P1-4 设置页「大屏性能模式」一键项预留接口。

_dpi_degrade_pixels: int | None = None  # None = 尚未从配置门面加载

# 默认阈值：600 万物理像素 ≈ 4K@150% 全屏（829 万，C 场景）触发降级；
# 2K@100% 与 2K@150% 笔记本全屏（369 万，A/B 场景）不受影响
_DEFAULT_DEGRADE_PIXELS = 6_000_000


def get_dpi_degrade_pixels() -> int:
    """自动降级阈值（整窗物理像素数；0 = 关闭）"""
    global _dpi_degrade_pixels
    if _dpi_degrade_pixels is None:
        v = _DEFAULT_DEGRADE_PIXELS
        try:
            data = app_settings.get_domain("perf")
            if "perf_dpi_degrade_pixels" in data:
                v = int(data["perf_dpi_degrade_pixels"])
        except Exception:
            pass
        _dpi_degrade_pixels = max(0, v)
    return _dpi_degrade_pixels


# ---- P1-4：大屏性能模式一键项（手动覆盖自动判定） ----
_bigscreen_mode: bool | None = None  # None = 尚未从配置门面加载


def is_bigscreen_mode() -> bool:
    """「大屏性能模式」（P1-4）：True = 无视像素阈值强制全量降级

    与阈值的组合语义：开启 → 弹窗/切换/菜单动画一律降级（阈值不参与
    判定）；关闭 → 回到自动判定（默认 600 万，0=关闭）。切换即时生效
    （所有判定都是运行时读取），供设置页一键开关使用。
    """
    global _bigscreen_mode
    if _bigscreen_mode is None:
        v = False
        try:
            data = app_settings.get_domain("perf")
            if "perf_bigscreen_mode" in data:
                v = str(data["perf_bigscreen_mode"]).lower() not in (
                    "false", "0", "no", "")
        except Exception:
            pass
        _bigscreen_mode = bool(v)
    return _bigscreen_mode


def set_bigscreen_mode(enabled: bool):
    """设置大屏性能模式并立即持久化（即时生效，无需重启）"""
    global _bigscreen_mode
    _bigscreen_mode = bool(enabled)
    _persist("perf_bigscreen_mode", _bigscreen_mode)


# ---- P1-1：弹窗淡入淡出实现选择 ----
_dialog_fade_mode: str | None = None


def get_dialog_fade_mode() -> str:
    """弹窗淡入淡出实现（P1-1）：

    - 'card'（默认）：只给中央卡片挂 QGraphicsOpacityEffect 淡入/淡出，
      遮罩即时出现（与 WinUI ContentDialog 同款行为），离屏面积从
      整窗（829 万像素级）缩到卡片（~25 万像素级），每帧成本降一个
      量级；淡入结束后恢复卡片阴影 effect（Qt 单控件仅允许一个
      graphicsEffect，淡入期间与阴影互斥）。
    - 'opacity'：回退库原生整窗 QGraphicsOpacityEffect 路径（逃生门，
      改 perf 域 perf_dialog_fade_mode 即可，无需改码）。

    实现注记（2026-09-25 实测）：windowOpacity 属性动画方案不可行——
    qfw MaskDialogBase 在 __init__ 里 setWindowFlags(Qt.FramelessWindowHint)
    整组替换窗口标志，丢失 Qt.Dialog 位，弹窗实际是父窗内的**子部件
    覆盖层**（isWindow()==False、无 windowHandle），setWindowOpacity
    对其无效。
    """
    global _dialog_fade_mode
    if _dialog_fade_mode is None:
        v = "card"
        try:
            data = app_settings.get_domain("perf")
            if data.get("perf_dialog_fade_mode") == "opacity":
                v = "opacity"
        except Exception:
            pass
        _dialog_fade_mode = v
    return _dialog_fade_mode


# ---- P1-2：应用内缩放与系统缩放叠加告警 ----
_dpi_stack_warning: str | None = None


def detect_system_scale_percent() -> "int | None":
    """读取系统缩放百分比（必须在 QApplication 创建**前**调用，无副作用）

    取注册表 HKCU\\Control Panel\\Desktop\\WindowMetrics\\AppliedDPI
    （96 = 100%）。为何不用 ctypes GetDpiForSystem：本函数在 QApplication
    创建前被调用，进程尚未 DPI-aware，GetDpiForSystem 恒返回 96；
    注册表读取无此问题。局限：主显示器口径，不感知副屏 per-monitor
    覆盖（告警用途足够）。失败 / 非 Windows 返回 None。
    """
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Control Panel\Desktop\WindowMetrics") as k:
            v, _t = winreg.QueryValueEx(k, "AppliedDPI")
        return int(v) * 100 // 96
    except Exception:
        return None


def set_dpi_stack_warning(app_scale: int, sys_scale: int):
    """记录「应用内缩放 × 系统缩放」叠加告警（P1-2，告警不阻断）

    双 150% → 实际 dpr 2.25 → 物理像素 ×5，可把任何机器推入
    「未响应」量级（docs/大屏与超高DPI渲染性能调查报告2026-09-25.md §4.2）。
    告警信息落连接日志 + 供设置页「界面缩放」行内提示（get_dpi_stack_warning）。
    """
    global _dpi_stack_warning
    _dpi_stack_warning = (
        f"检测到系统缩放 {sys_scale}% 与应用内缩放 {app_scale}% 叠加"
        f"（实际 {round(sys_scale * app_scale / 100)}%），渲染负担成倍增加，"
        f"大屏场景建议将应用内缩放改回 100%")
    _log(f"[perf] {_dpi_stack_warning}")


def get_dpi_stack_warning() -> "str | None":
    """读取「应用内缩放 × 系统缩放」叠加告警（未命中返回 None）

    供设置页「界面缩放」行内提示消费（hub_pages._group_app 的 dpi 行）。
    """
    return _dpi_stack_warning


def _screen_dpr(widget) -> float:
    """widget 所在屏幕的设备像素比（含 QT_SCALE_FACTOR 与系统缩放的合成值）"""
    try:
        scr = widget.screen()
        if scr is not None:
            dpr = scr.devicePixelRatio()
            if dpr and dpr > 0:
                return float(dpr)
    except Exception:
        pass
    return 1.0


def window_physical_pixels(widget) -> int:
    """widget 所属顶层窗口的物理像素数（逻辑尺寸 × DPR²）"""
    try:
        win = widget.window() if widget is not None else None
        if win is None:
            return 0
        dpr = _screen_dpr(win)
        return int(win.width() * win.height() * dpr * dpr)
    except Exception:
        return 0


def screen_physical_pixels(widget) -> int:
    """widget 所在屏幕的物理像素数（菜单等小窗自身的像素数不代表
    渲染预算，取整屏口径）"""
    try:
        scr = widget.screen()
    except Exception:
        scr = None
    if scr is None:
        try:
            from PySide6.QtWidgets import QApplication
            scr = QApplication.primaryScreen()
        except Exception:
            return 0
    if scr is None:
        return 0
    try:
        g = scr.geometry()
        dpr = scr.devicePixelRatio() or 1.0
        return int(g.width() * g.height() * dpr * dpr)
    except Exception:
        return 0


def _over_dpi_degrade(widget, use_screen: bool = False) -> bool:
    """是否应降级（异常一律按未超处理，绝不误降级）

    优先级：大屏性能模式（P1-4，开启即强制降级）> 像素阈值
    （perf_dpi_degrade_pixels，默认 600 万，0=关闭）。
    """
    try:
        if is_bigscreen_mode():
            return True
        limit = get_dpi_degrade_pixels()
        if limit <= 0:
            return False
        px = (screen_physical_pixels(widget) if use_screen
              else window_physical_pixels(widget))
        return px >= limit
    except Exception:
        return False


def patch_dialog_animation():
    """中央拦截 MaskDialogBase 淡入/淡出动画（幂等）

    背景：MessageBoxBase（含项目内 6 个弹窗：编辑售后/球桌/设备/署名统计/
    单视频等）继承 MaskDialogBase，打开/关闭时用 QGraphicsOpacityEffect +
    QPropertyAnimation 做**整窗**透明度渐变（根挂 effect，遮罩+卡片全走
    离屏光栅），实测 829 万像素下单帧 ≥48ms（真机核显再放大到数百 ms），
    是场景 C「未响应」的主源。三层策略（按优先级）：

    1. 动画开关（面板覆盖→全局）为关 → 直显（秒开无渐变）；
    2. 整窗物理像素 ≥ perf_dpi_degrade_pixels（默认 600 万，P0-1）→ 直显
       + 卡片 DropShadowEffect 半径 60 → 30；
    3. 其余：'card' 淡入淡出（P1-1，默认）——effect 只挂中央卡片（离屏
       面积从整窗缩到卡片），遮罩即时出现（WinUI ContentDialog 同款
       行为），淡入结束恢复卡片阴影。逃生门：perf 域
       perf_dialog_fade_mode='opacity' 回退库整窗 effect 路径。

    注：qfw 弹窗是父窗内的子部件覆盖层（非独立窗口，见
    get_dialog_fade_mode 实现注记），windowOpacity 方案不适用。
    """
    try:
        from PySide6.QtCore import QEasingCurve, QPropertyAnimation
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import (QDialog, QGraphicsOpacityEffect)
        from qfluentwidgets.components.dialog_box.mask_dialog_base import (
            MaskDialogBase)
    except Exception:
        return
    if getattr(MaskDialogBase, "_perf_dialog_patched", False):
        return
    _orig_show = MaskDialogBase.showEvent
    _orig_done = MaskDialogBase.done

    def _restore_shadow(self, blur_radius):
        """恢复卡片阴影 effect（淡入/淡出结束后调用；Qt 单控件单 effect）"""
        try:
            self.setShadowEffect(blur_radius, (0, 10), QColor(0, 0, 0, 100))
        except Exception:
            pass

    def _stop_fade(self):
        """停掉在途卡片淡入/淡出动画（快速连点时 show/done 互斥不残留
        半透明卡片）

        ⚠️ Qt 语义：stop() 中途打断**不触发** finished（仅在自然播完时
        发，见 Qt 源码 QAbstractAnimationPrivate::setState 的 endTime
        判定；PySide6 offscreen 下已实证：stop() 不发、
        setCurrentTime(duration) 同步发）。因此所有调用方在
        _stop_fade 之后必须自行重建/清理 effect，本函数不做任何收尾。"""
        ani = getattr(self, "_perf_fade_ani", None)
        if ani is not None:
            try:
                ani.stop()
            except Exception:
                pass
        try:
            self._perf_fade_ani = None
        except Exception:
            pass

    def _show(self, e):
        try:
            degrade = _over_dpi_degrade(self)
            if not get_animation(_window_panel_key(self)) or degrade:
                if degrade:
                    try:  # P0-1：大屏阴影降半径（公开 API，重建 effect）
                        self.setShadowEffect(30, (0, 10), QColor(0, 0, 0, 100))
                    except Exception:
                        pass
                _stop_fade(self)
                self.setGraphicsEffect(None)
                self.widget.setGraphicsEffect(None)
                _restore_shadow(self, 30 if degrade else 60)
                super(MaskDialogBase, self).showEvent(e)
                return
            if get_dialog_fade_mode() == "card":
                _stop_fade(self)
                try:
                    self.setGraphicsEffect(None)  # 根不挂 effect（整窗离屏源）
                    _restore_shadow(self, 60)  # 先复位，替换为淡入 effect
                    eff = QGraphicsOpacityEffect(self.widget)
                    eff.setOpacity(0)
                    self.widget.setGraphicsEffect(eff)
                    ani = QPropertyAnimation(eff, b"opacity", self.widget)
                    ani.setStartValue(0)
                    ani.setEndValue(1)
                    ani.setDuration(200)
                    ani.setEasingCurve(QEasingCurve.InSine)
                    ani.finished.connect(lambda: _restore_shadow(self, 60))
                    self._perf_fade_ani = ani
                    ani.start()
                    super(MaskDialogBase, self).showEvent(e)
                    return
                except Exception:
                    _restore_shadow(self, 60)
        except Exception:
            pass
        _orig_show(self, e)

    def _done(self, code):
        try:
            if not get_animation(_window_panel_key(self)) or _over_dpi_degrade(self):
                _stop_fade(self)
                self.setGraphicsEffect(None)
                self.widget.setGraphicsEffect(None)
                QDialog.done(self, code)
                return
            if get_dialog_fade_mode() == "card":
                _stop_fade(self)
                try:
                    self.setGraphicsEffect(None)
                    eff = QGraphicsOpacityEffect(self.widget)
                    eff.setOpacity(1)
                    self.widget.setGraphicsEffect(eff)
                    ani = QPropertyAnimation(eff, b"opacity", self.widget)
                    ani.setStartValue(1)
                    ani.setEndValue(0)
                    ani.setDuration(100)

                    def _finish(code=code):
                        try:
                            self.setGraphicsEffect(None)
                            self.widget.setGraphicsEffect(None)
                        except Exception:
                            pass
                        QDialog.done(self, code)

                    ani.finished.connect(_finish)
                    self._perf_fade_ani = ani
                    ani.start()
                    return
                except Exception:
                    pass
        except Exception:
            pass
        _orig_done(self, code)

    MaskDialogBase.showEvent = _show
    MaskDialogBase.done = _done
    MaskDialogBase._perf_dialog_patched = True


def patch_menu_animation():
    """中央拦截菜单弹出动画：降级与 FADE 映射（幂等）

    qfluentwidgets 所有菜单动画（右键 RoundMenu、ComboBox/EditableComboBox
    下拉、LineEdit 编辑菜单等）都汇聚到 MenuAnimationManager.make()；
    库内 ComboBox 下拉硬编码 DROP_DOWN/PULL_UP，单靠调用点传参无法覆盖，
    故在此统一拦截。三条规则（按优先级）：

    1. 动画开关（面板覆盖→全局）为关 → 降级 NONE；
    2. 超像素阈值（P0-3，按「所在屏幕物理像素」口径——菜单自身是小窗）
       → 降级 NONE；
    3. 其余：DROP_DOWN/PULL_UP → FADE_IN_DROP_DOWN/FADE_IN_PULL_UP
       （P1-3，2026-09-25）。原动画每帧 setMask（真机 SetWindowRgn，
       DWM 对分层窗逐帧重组）+ 半高滑动；FADE_IN_* 是库自带的
       windowOpacity 淡入 + 8px 短滑动，纯合成层成本，视觉更顺。
    """
    try:
        from qfluentwidgets.components.widgets.menu import (
            MenuAnimationManager, MenuAnimationType)
    except Exception:
        return
    if getattr(MenuAnimationManager, "_perf_patched", False):
        return
    _orig_make = MenuAnimationManager.make.__func__
    _fade_map = {
        MenuAnimationType.DROP_DOWN: MenuAnimationType.FADE_IN_DROP_DOWN,
        MenuAnimationType.PULL_UP: MenuAnimationType.FADE_IN_PULL_UP,
    }

    @classmethod
    def _make(cls, menu, aniType):
        try:
            if aniType != MenuAnimationType.NONE:
                if not get_animation(_menu_panel_key(menu)) or \
                        _over_dpi_degrade(menu, use_screen=True):
                    aniType = MenuAnimationType.NONE
                elif aniType in _fade_map:
                    aniType = _fade_map[aniType]
        except Exception:
            pass
        return _orig_make(cls, menu, aniType)

    MenuAnimationManager.make = _make
    MenuAnimationManager._perf_patched = True


def patch_switch_animation():
    """大屏自动直切 PopUpAniStackedWidget 页面切换动画（P0-3，幂等）

    FluentWindow 系（主窗口/三个面板窗）的页面切换走 PopUpAniStackedWidget
    （250ms 滑入），超阈值时改为直切——降级路径与库自带开关完全一致
    （stacked_widget.py `not self.isAnimationEnabled` 分支，直接
    super().setCurrentIndex）。生产代码全仓只用 popOut=False（单页滑入），
    故无需区分 popOut 形态。阈值判定取「所属顶层窗口物理像素」。
    """
    try:
        from PySide6.QtWidgets import QStackedWidget
        from qfluentwidgets.components.widgets.stacked_widget import (
            PopUpAniStackedWidget)
    except Exception:
        return
    if getattr(PopUpAniStackedWidget, "_perf_switch_patched", False):
        return
    _orig_set_index = PopUpAniStackedWidget.setCurrentIndex

    def _setCurrentIndex(self, index, *args, **kwargs):
        try:
            if _over_dpi_degrade(self):
                return QStackedWidget.setCurrentIndex(self, index)
        except Exception:
            pass
        return _orig_set_index(self, index, *args, **kwargs)

    PopUpAniStackedWidget.setCurrentIndex = _setCurrentIndex
    PopUpAniStackedWidget._perf_switch_patched = True


def patch_acrylic_downsample():
    """亚克力模糊强制降采样（P0-2，幂等，启动时调用一次）

    导航 hover 展开时 NavigationPanel.expand() → AcrylicBrush.grabImage →
    setImage → gaussianBlur(blurPicSize=None)，主线程**全分辨率**高斯模糊：
    4K 级 PIL 实现单次 160ms（开发环境 scipy 路径 1090ms）同步阻塞。
    qfw 两套实现（scipy/PIL 补丁）都支持 blurPicSize 先降采样再模糊，
    且库内 BlurCoverThread 默认就传 (450,450)——模糊图缩放回放视觉无感。
    这里在 blurPicSize 未显式设置（=None 全分辨率）时统一 clamp 到
    (450,450)，4K 级模糊降到数 ms。
    """
    try:
        from qfluentwidgets.components.widgets.acrylic_label import AcrylicBrush
    except Exception:
        return
    if getattr(AcrylicBrush, "_perf_blur_patched", False):
        return
    _orig_set_image = AcrylicBrush.setImage

    def _setImage(self, image):
        try:
            if self.blurPicSize is None:
                self.blurPicSize = (450, 450)
        except Exception:
            pass
        return _orig_set_image(self, image)

    AcrylicBrush.setImage = _setImage
    AcrylicBrush._perf_blur_patched = True


def set_acrylic_enabled(enabled: bool):
    """设置亚克力开关并立即持久化到 settings.json（即时生效，无需重启）"""
    global _acrylic_enabled
    _acrylic_enabled = bool(enabled)
    _persist("perf_acrylic", _acrylic_enabled)


def set_animation_enabled(enabled: bool):
    """设置动画开关并立即持久化到 settings.json（即时生效，无需重启）"""
    global _animation_enabled
    _animation_enabled = bool(enabled)
    _persist("perf_animation", _animation_enabled)


def _persist(key: str, value: bool):
    """将单个性能选项写入配置门面 perf 域（其余字段不受影响）"""
    try:
        app_settings.set(key, value)
        # 迁移完成后移除旧字段，避免歧义
        app_settings.remove("performance_mode")
    except Exception:
        pass


# ==================== Mica 云母环境兜底（2026-09-24） ====================
# 背景：qfluentwidgets 的 FluentWindow 全系在构造时自动 setMicaEffectEnabled(True)
# 并把窗口背景置为全透明（_normalBackgroundColor → QColor(0,0,0,0)）。但 DWM
# 在以下场景会**静默不渲染** backdrop（DWMWA_SYSTEMBACKDROP_TYPE /
# ACCENT_ENABLE_HOSTBACKDROP 调用不报错、也没效果）：
#   1. RDP 远程会话 —— Mica/Acrylic 在远程桌面里明确不支持；
#   2. 系统「透明效果」关闭（设置>个性化>颜色；省电模式会自动关它）。
# 结果就是"同一份产物，有的 Win11 有云母有的没有"——且没渲染的窗口只剩
# 透明背景 + DwmExtendFrameIntoClientArea 的裸框架，观感生硬。
# 兜底策略：启动时探测一次，命中即中央短路 setMicaEffectEnabled，
# 窗口自动回退 qfw 纯主题色背景（对齐 Win10 的既有回退形态）。

_SM_REMOTESESSION = 0x1000  # GetSystemMetrics：非零=当前在 RDP 会话中
_TRANSPARENCY_KEY = (r"SOFTWARE\Microsoft\Windows\CurrentVersion"
                     r"\Themes\Personalize")


def _is_remote_session() -> bool:
    """当前是否处于 RDP/远程桌面会话（探测失败按否处理）"""
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetSystemMetrics(_SM_REMOTESESSION))
    except Exception:
        return False


def _is_transparency_disabled() -> bool:
    """系统「透明效果」是否被关闭（注册表键缺失/读取失败按开启处理）"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            _TRANSPARENCY_KEY) as k:
            v, _t = winreg.QueryValueEx(k, "EnableTransparency")
            return not int(v)
    except Exception:
        return False


def mica_block_reason() -> str:
    """返回禁用 Mica 的原因标识（'' = 环境支持，可启用）

    判定口径与 qfluentwidgets 门禁（sys.getwindowsversion().build ≥ 22000）
    保持一致；在其之上追加两类「DWM 静默不渲染」环境：
    - below-win11 : Win10（build < 22000），库本身就不启用
    - rdp-session : RDP 远程会话，DWM backdrop 明确不支持
    - transparency-off : 系统透明效果关闭（含省电模式自动关闭）
    """
    import sys as _sys
    if _sys.platform != "win32":
        return ""
    try:
        if _sys.getwindowsversion().build < 22000:
            return "below-win11"
    except Exception:
        return "unknown-version"
    if _is_remote_session():
        return "rdp-session"
    if _is_transparency_disabled():
        return "transparency-off"
    return ""


def patch_mica_policy():
    """云母环境兜底（幂等，启动时调用一次）：命中不渲染场景时双层短路，
    所有 FluentWindow 窗口回退纯主题色背景，杜绝「透明背景叠裸框架」
    的半残观感

    双层原因（qfw 开启 Mica 有两条独立通路，缺一不可）：
    1. FluentWidget.__init__ → setMicaEffectEnabled(True) —— 开关路径，
       强制短路后 _isMicaEnabled=False，窗口背景走实色而非全透明；
    2. FluentWidget 的基类 FramelessWindow（Win11 分支）在构造时
       **无条件直调** windowEffect.setMicaEffect() —— 绕过开关的 DWM
       backdrop 路径，必须一并 no-op，否则标题栏透明区仍会漏出 Mica。

    环境支持时完全不 patch（保持库原行为）；探测只做一次，运行期系统
    设置变化不追（透明效果重新打开需重启程序，与 qfw 现状一致）。
    """
    try:
        from qfluentwidgets.window.fluent_window import FluentWidget
        from qframelesswindow.windows.window_effect import WindowsWindowEffect
    except Exception:
        return
    if getattr(FluentWidget, "_perf_mica_patched", False):
        return
    reason = mica_block_reason()
    FluentWidget._perf_mica_patched = True
    if not reason:
        return  # 环境支持：保持库原行为

    def _setMicaEffectEnabled(self, isEnabled: bool):
        if isEnabled:
            return  # 静默拒绝：_isMicaEnabled 保持 False，窗口走纯色背景
        try:
            self.windowEffect.removeBackgroundEffect(self.winId())
        except Exception:
            pass

    def _setMicaEffect(self, hWnd, isDarkMode=False, isAlt=False):
        return  # 静默跳过：不给 DWM 发任何 backdrop 属性（含基类直调路径）

    FluentWidget.setMicaEffectEnabled = _setMicaEffectEnabled
    WindowsWindowEffect.setMicaEffect = _setMicaEffect
    _log(f"[perf] Mica 云母已禁用（{reason}），窗口使用纯色背景")


def _log(message: str):
    """写连接日志（失败静默——性能选项绝不阻塞启动）"""
    try:
        from core.conn_logger import conn_logger
        conn_logger._write("INFO", "PERF", message)
    except Exception:
        pass


# ==================== 向后兼容 ====================

def is_performance_mode() -> bool:
    """兼容旧接口：亚克力关闭即视为性能模式"""
    return not is_acrylic_enabled()


def invalidate_cache():
    """兼容旧接口：重新从 settings.json 加载"""
    global _acrylic_enabled, _animation_enabled, _table_smooth_enabled
    global _panel_table_overrides, _panel_animation_overrides
    global _lean_delegate_enabled, _dpi_degrade_pixels
    global _bigscreen_mode, _dialog_fade_mode
    _acrylic_enabled = None
    _animation_enabled = None
    _table_smooth_enabled = None
    _lean_delegate_enabled = None
    _dpi_degrade_pixels = None
    _bigscreen_mode = None
    _dialog_fade_mode = None
    _panel_table_overrides = None
    _panel_animation_overrides = None
