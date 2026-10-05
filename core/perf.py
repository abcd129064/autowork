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


# ==================== 表格滚动位块搬移修复（2026-10-06） ====================

_table_blit_enabled: "bool | None" = None


def is_table_blit_enabled() -> bool:
    """表格滚动「位块搬移」修复开关（perf 域 perf_table_scroll_blit_v2，默认开）

    第二轮实现（2026-10-06，已按「viewport 真正不透明」形态重做）——
    第一版只设 WA_OpaquePaintEvent 而不画底，真机上表格滚动整片叠影：
    Qt 不再代擦背景，而本项目表格底色本来是透明的（qfw QSS 把
    QTableView / ::item 背景设为 transparent、单元格还有 margin=2 的透明
    缝隙、行底色只有 hover/选中/隔行才画），于是搬移留在下面的旧像素永远
    没人补。

    本版把「画满 viewport」显式承担下来：
    1. 先从**表格背后实测取色**（`_sample_table_bg`：渲染父级 1×1 像素），
       取不到全不透明色就**不开启**（功能静默不生效，绝不留下没人画底的
       viewport）；
    2. `paintEvent` 包装里每次绘制前用该颜色填满 e.rect()，再交原实现画
       格子——整屏首绘、滚动新露出条带、hover 局部都走同一条路径；
    3. 再配「预留悬浮滚动条条带」，才拿到位块搬移。

    验收口径（本轮补齐）：**像素等价** —— 同一状态下「搬移渲染」与
    「强制整视口重绘」的结果逐字节一致（tests/test_table_blit_patch.py），
    绘制面积判据只作机制在位证据，不再是验收依据。

    键名用 v2：第一版曾把 perf_table_scroll_blit 置 true（那版会叠影），
    换键可保证旧值不会以任何方式影响新实现（旧键已不再读取）。
    """
    global _table_blit_enabled
    if _table_blit_enabled is None:
        v = True
        try:
            data = app_settings.get_domain("perf")
            if "perf_table_scroll_blit_v2" in data:
                v = str(data["perf_table_scroll_blit_v2"]).lower() not in (
                    "false", "0", "no", "")
        except Exception:
            pass
        _table_blit_enabled = bool(v)
    return _table_blit_enabled


def set_table_blit_enabled(enabled: bool):
    """设置位块搬移修复并持久化；已打开的表格即时生效"""
    global _table_blit_enabled
    _table_blit_enabled = bool(enabled)
    _persist("perf_table_scroll_blit_v2", _table_blit_enabled)
    if not _table_blit_enabled:
        # 关闭时清掉标定缓存，避免下次开启用旧主题/旧宿主的颜色
        try:
            from PySide6.QtWidgets import QApplication, QTableView
            for w in QApplication.topLevelWidgets():
                for t in w.findChildren(QTableView):
                    t._perf_blit_bg = None
        except Exception:
            pass
    apply_table_blit_globally()


def _blit_strip_width(table, orient: str = "vertical") -> int:
    """给 qfw 悬浮滚动条预留的条带宽度（该方向不可滚动 / 无悬浮条 → 0）

    两个方向的悬浮条都会压住 viewport 并挡掉位块搬移：竖向压右缘，
    横向压下缘（列宽超出视口时出现）。各自按「该方向滚动条是否可滚动」
    决定要不要留条带。
    """
    try:
        vertical = (orient == "vertical")
        sb = (table.verticalScrollBar() if vertical
              else table.horizontalScrollBar())
        if sb is None or sb.maximum() <= sb.minimum():
            return 0
        dlg = getattr(table, "scrollDelagate", None)
        if dlg is None:
            return 0
        bar = getattr(dlg, "vScrollBar" if vertical else "hScrollBar", None)
        # 竖向条取宽、横向条取高；构造期尚未布局时会拿到退化值，夹到 12
        size = (int(bar.width()) if vertical else int(bar.height())) \
            if bar is not None else 0
        if size <= 0 or size > 64:
            size = 12
        return size + 1
    except Exception:
        return 0


def _platform_name() -> str:
    """当前 Qt 平台名（独立成函数便于离线回归注入）"""
    try:
        from PySide6.QtGui import QGuiApplication
        return QGuiApplication.platformName() or ""
    except Exception:
        return ""


def _screen_pixel(global_pt):
    """抓屏幕上某一点的 1×1 像素（真实观感：主题色 / Mica 背景 / 业务 QSS）

    这是唯一能拿到「用户真正看到的颜色」的途径 —— 渲染父级只能拿到父级
    自己的 palette 色，而本项目页面是「透明叠透明」，palette 色与实际观感
    无关（2026-10-06 真机实测：暗色主题表格被填成纯黑、浅色主题填成灰白）。
    取不到（offscreen / 抓屏失败 / 窗口不可见）返回 None。
    """
    try:
        from PySide6.QtGui import QColor, QGuiApplication
        scr = QGuiApplication.screenAt(global_pt) or QGuiApplication.primaryScreen()
        if scr is None:
            return None
        pm = scr.grabWindow(0, global_pt.x(), global_pt.y(), 1, 1)
        if pm is None or pm.isNull():
            return None
        img = pm.toImage()
        if img.isNull() or img.width() < 1 or img.height() < 1:
            return None
        c = QColor(img.pixel(0, 0))
        return c if (c.isValid() and c.alpha() == 255) else None
    except Exception:
        return None


def _sample_table_bg(table):
    """取表格「背后」的实底颜色（用于把我们自己画满 viewport）

    真实平台：在表格四边各取一个**紧贴表格外侧**的全局点，抓屏幕 1×1 像素。
    多点必须互相一致（至少 2 个有效且完全相同）才采信 —— 任何单点都可能落在
    别的控件/边缘上。抓不到或互相矛盾 → 返回 None（功能静默不生效）。

    offscreen 平台没有真实屏幕，退化为「渲染父级 1×1」（**仅供离线回归**）：
    真实平台绝不走这条退化路径，因为它拿的是父级 palette 色，与本项目
    「透明叠透明」的实际观感不符。
    """
    try:
        from PySide6.QtCore import QPoint, QRect, QSize
        from PySide6.QtGui import QColor, QGuiApplication
        if not table.isVisible():
            return _blit_reason(table, "表格/窗口未显示")
        geo = table.rect()
        offscreen = "offscreen" in _platform_name()
        if not offscreen:
            win = table.window()
            if win is not None and win.isMinimized():
                return _blit_reason(table, "窗口最小化")
            mid = geo.center()
            pts = [QPoint(mid.x(), geo.top() - 6),
                   QPoint(mid.x(), geo.bottom() + 6),
                   QPoint(geo.left() - 6, mid.y()),
                   QPoint(geo.right() + 6, mid.y())]
            uniq = []
            n_valid = 0
            for local in pts:
                c = _screen_pixel(table.mapToGlobal(local))
                if c is None:
                    continue
                n_valid += 1
                if c not in uniq:
                    uniq.append(c)
            # 至少两个有效采样且颜色完全一致才采信：单点可能落在别的控件上，
            # 多点不一致说明表格周围不是纯色（或有窗口遮挡）→ 不标定。
            if n_valid >= 2 and len(uniq) == 1:
                return uniq[0]
            return _blit_reason(
                table,
                f"表格四周取不到一致的实底颜色（有效采样 {n_valid}/4，"
                f"需 ≥6px 纯色间隙且无遮挡）")
        # ---- offscreen 退化路径（仅回归测试）----
        p = table.parentWidget()
        if p is None:
            return _blit_reason(table, "无父控件，取不到底色")
        rect = QRect(table.mapTo(p, QPoint(0, 0)), table.size())
        mid = rect.center()
        for pt in (QPoint(mid.x(), rect.top() - 4),
                   QPoint(mid.x(), rect.bottom() + 4),
                   QPoint(rect.left() - 4, mid.y()),
                   QPoint(rect.right() + 4, mid.y())):
            if rect.contains(pt) or not p.rect().contains(pt):
                continue
            pm = p.grab(QRect(pt, QSize(1, 1)))
            if pm.isNull():
                continue
            c = QColor(pm.toImage().pixel(0, 0))
            if c.isValid() and c.alpha() == 255:
                return c
        return _blit_reason(table, "offscreen 退化路径取不到底色（仅回归用）")
    except Exception:
        pass
    return _blit_reason(table, "取色异常")


def _make_blit_filter(table):
    """造一个「绘制前先填实底」的 viewport 事件过滤器（QObject 延迟导入）

    用事件过滤器而不是包一层 TableBase.paintEvent：过滤器一定先于 paintEvent
    执行（无论哪个子类实现了 paintEvent）。若走 paintEvent 包装，任何自带
    paintEvent 的子类都会绕过填底，而它的 viewport 仍被标为不透明 → 又变成
    第一版那种「没人画底的不透明 viewport」= 叠影。
    """
    from PySide6.QtCore import QEvent, QObject, Qt
    from PySide6.QtGui import QPainter

    class _BlitPaintFilter(QObject):
        def __init__(self, table):
            super().__init__(table)
            self.table = table

        def eventFilter(self, obj, ev):
            if ev.type() == QEvent.Type.Paint:
                try:
                    t = self.table
                    vp = t.viewport()
                    col = getattr(t, "_perf_blit_bg", None)
                    if (vp is not None and col is not None
                            and vp.testAttribute(
                                Qt.WidgetAttribute.WA_OpaquePaintEvent)):
                        painter = QPainter(vp)
                        try:
                            painter.fillRect(ev.rect(), col)
                        finally:
                            painter.end()
                except Exception:
                    pass
            return False

    return _BlitPaintFilter(table)


def _schedule_blit_bg(table):
    """延后标定底色（必须在事件循环里做：paint 期间 grab 父级会递归重入）"""
    if getattr(table, "_perf_blit_bg_pending", False):
        return
    table._perf_blit_bg_pending = True

    def _do():
        try:
            table._perf_blit_bg_pending = False
            if not is_table_blit_enabled():
                return
            col = _sample_table_bg(table)
            if col is None:
                # 取不到 → 保持关闭，等下次重布局再试（原因已记录，供状态行）
                _set_blit_state(
                    table, "no_bg",
                    getattr(table, "_perf_blit_reason", "") or "取色失败")
                return
            table._perf_blit_bg = col
            apply_table_blit(table)
            table.viewport().update()
        except Exception:
            pass

    try:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, _do)
    except Exception:
        table._perf_blit_bg_pending = False


# ---- 位块搬移可观测性（2026-10-07）----
# 起因：位块搬移取不到底色时是**静默不生效**（失败安全设计），用户与排查者
# 都无法回答「我这张表现在到底走没走位块搬移」，只能看滚动是否还卡。这里给
# 每张表打一个可读状态 + 一次状态转移日志，并提供存活表格的汇总口径。
_BLIT_STATE_LABEL = {
    "applied": "已生效（位块搬移）",
    "no_bg": "未生效：未取到底色",
    "hidden": "未生效：表格/窗口未显示",
    "off": "未生效：加速已关闭",
}


def _blit_reason(table, text):
    """记下「这张表为什么没生效」的原因（只做记录，不参与判定）"""
    try:
        table._perf_blit_reason = text
    except Exception:
        pass
    return None


def _set_blit_state(table, state, reason=""):
    """更新单表位块搬移状态（只在状态**变化**时写日志，避免重布局刷屏）"""
    try:
        prev = getattr(table, "_perf_blit_state", None)
        table._perf_blit_state = state
        table._perf_blit_reason = reason
        if prev != state:
            label = _BLIT_STATE_LABEL.get(state, state)
            _log(f"[perf] 表格滚动加速：{type(table).__name__} → {label}"
                 + (f"（{reason}）" if reason else ""))
    except Exception:
        pass


def table_blit_status() -> dict:
    """汇总当前**存活**表格的位块搬移状态（设置对话框状态行 / 排查用）

    判定以运行期事实为准（viewport 的 WA_OpaquePaintEvent + 标定出的底色
    颜色），不看记账字段 —— 这样任何绕过 apply_table_blit 的路径都能被发现。
    遍历顶层窗口现场找表格，不缓存引用（表格随页面创建销毁，缓存会拖住
    已关闭的表格）。
    """
    out = {"enabled": is_table_blit_enabled(), "total": 0, "applied": 0,
           "no_bg": 0, "hidden": 0, "off": 0, "other": 0}
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QTableView
        seen = set()
        for w in QApplication.topLevelWidgets():
            try:
                tables = w.findChildren(QTableView)
            except Exception:
                continue
            for t in tables:
                if id(t) in seen:
                    continue
                seen.add(id(t))
                out["total"] += 1
                if not out["enabled"]:
                    out["off"] += 1
                    continue
                vp = t.viewport()
                opaque = bool(vp is not None and vp.testAttribute(
                    Qt.WidgetAttribute.WA_OpaquePaintEvent))
                if opaque and getattr(t, "_perf_blit_bg", None) is not None:
                    out["applied"] += 1
                elif getattr(t, "_perf_blit_bg", None) is None:
                    reason = str(getattr(t, "_perf_blit_reason", "") or "")
                    if "未显示" in reason or "最小化" in reason:
                        out["hidden"] += 1
                    else:
                        out["no_bg"] += 1
                else:
                    out["other"] += 1
    except Exception:
        pass
    return out


def table_blit_status_text() -> str:
    """把 table_blit_status 汇总成一行中文（性能选项对话框状态行）"""
    try:
        s = table_blit_status()
        if not s["enabled"]:
            return "表格滚动加速：已关闭（perf_table_scroll_blit_v2=false）"
        parts = [f"已生效 {s['applied']}/{s['total']} 张"]
        if s["no_bg"]:
            parts.append(f"未取到底色 {s['no_bg']} 张")
        if s["hidden"]:
            parts.append(f"未显示 {s['hidden']} 张")
        if s["other"]:
            parts.append(f"其他 {s['other']} 张")
        txt = "表格滚动加速：" + "，".join(parts)
        if s["no_bg"]:
            txt += "（取到底色才生效：表格四周需有 ≥6px 纯色间隙，有遮挡/" \
                   "浮动面板压边则不生效）"
        return txt
    except Exception:
        return "表格滚动加速：状态读取失败"


def apply_table_blit(table):
    """把位块搬移修复应用到单个表格（幂等，可反复调用）

    三个条件必须同时满足，缺一无效（2026-10-06 逐条实测）：
    1. **viewport 真正不透明**：`WA_OpaquePaintEvent=True` 之外，还必须已经
       标定出「表格背后的实底颜色」（`_sample_table_bg`）——`_make_blit_filter`
       装的 viewport 事件过滤器会在每次绘制前把暴露区域填满。第一版只设属性、
       没画底，真机上表格滚动整片叠影（旧像素没人擦）；
    2. viewport 右侧/底部预留悬浮滚动条条带 —— qfw 的 SmoothScrollBar 是
       表格子控件且压在 viewport 边缘，被压住的 viewport 同样拿不到搬移；
    3. 条带必须在每次重布局后重新预留（QTableView::updateGeometries 会重算
       viewport margins 覆盖手工值）。

    取不到底色时**不设**不透明（功能静默不生效），绝不留下没人画底的 viewport。
    """
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QAbstractScrollArea
        vp = table.viewport()
        if vp is None:
            return
        on = is_table_blit_enabled()
        bg = getattr(table, "_perf_blit_bg", None) if on else None
        if on and bg is None:
            _schedule_blit_bg(table)
        # 填底过滤器：装上就一直挂着（未开启时它什么都不做）
        if getattr(table, "_perf_blit_filter", None) is None:
            flt = _make_blit_filter(table)
            table._perf_blit_filter = flt
            vp.installEventFilter(flt)
        vp.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent,
                        bool(on and bg is not None))
        on = bool(on and bg is not None)
        want_r = _blit_strip_width(table, "vertical") if on else 0
        want_b = _blit_strip_width(table, "horizontal") if on else 0
        mg = table.viewportMargins()
        if mg.right() != want_r or mg.bottom() != want_b:
            QAbstractScrollArea.setViewportMargins(
                table, mg.left(), mg.top(), want_r, want_b)
        # 表头同步收窄/收短，避免表头钻到悬浮滚动条下面（表头由
        # QTableView::updateGeometries 先按旧 viewport 摆位）
        hh = (table.horizontalHeader()
              if hasattr(table, "horizontalHeader") else None)
        if hh is not None and hh.width() != vp.width():
            hh.resize(vp.width(), hh.height())
        vh = (table.verticalHeader()
              if hasattr(table, "verticalHeader") else None)
        if vh is not None and vh.height() != vp.height():
            vh.resize(vh.width(), vp.height())
        # 可观测性：状态 + 失败原因（只在转移时写日志）
        if bg is None:
            if is_table_blit_enabled():
                _set_blit_state(
                    table, "no_bg",
                    getattr(table, "_perf_blit_reason", "") or "尚未标定")
            else:
                _set_blit_state(table, "off", "总开关关闭")
        else:
            _set_blit_state(table, "applied", "")
    except Exception:
        pass


def apply_table_blit_globally():
    """按当前开关刷新所有已存在表格（设置页切换后立即生效）"""
    try:
        from PySide6.QtWidgets import QApplication, QTableView
        for w in QApplication.topLevelWidgets():
            for t in w.findChildren(QTableView):
                apply_table_blit(t)
    except Exception:
        pass


def patch_table_scroll_blit():
    """恢复 Qt 滚动「位块搬移」快路径（幂等，启动时调用一次）

    背景（2026-10-06 实测，tools/perf/perf_scroll_latency.py --truth）：
    qfluentwidgets 的 TableBase.__init__ 会给每个表格挂 QSS
    （FluentStyleSheet.TABLE_VIEW），叠加库自绘的悬浮滚动条后，Qt 的
    QWidget::scroll() 位块搬移快路径失效 —— 滚动条每次数值变化都整视口
    重绘：1560x752 viewport（13 列 × 约 20 行 ≈ 260 格）每步光栅 117 万
    px²、11.5ms/步（offscreen scale 1.0；高 DPI 按 dpr² 继续放大）。

    修复：包一层 TableBase.__init__ 做首帧应用，包一层
    QTableView.updateGeometries 保证每次重布局后条带仍然预留
    （QTableView::updateGeometries 会重算 viewport margins 覆盖手工值）。
    实测：绘制面积 1,173,120 → 1,547 px²/步（1/758），每步 11.5 → 1.18ms
    （响应 0.28 + 光栅 0.90），换页 / 改列宽 / resize 后保持。
    """
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPainter
        from PySide6.QtWidgets import QTableView
        from qfluentwidgets.components.widgets.table_view import TableBase
    except Exception:
        return
    if getattr(TableBase, "_perf_blit_patched", False):
        return

    _orig_init = TableBase.__init__
    _orig_update = QTableView.updateGeometries

    def _init(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        apply_table_blit(self)

    def _update_geometries(self):
        _orig_update(self)
        apply_table_blit(self)

    TableBase.__init__ = _init
    TableBase.updateGeometries = _update_geometries
    TableBase._perf_blit_patched = True

    # 主题切换 → 标定出的底色失效（颜色是实测像素，不能跨主题复用）
    def _on_theme_changed(*_args):
        try:
            from PySide6.QtWidgets import QApplication, QTableView as _TV
            for w in QApplication.topLevelWidgets():
                for t in w.findChildren(_TV):
                    t._perf_blit_bg = None
                    apply_table_blit(t)
        except Exception:
            pass

    try:
        from qfluentwidgets.common.config import qconfig
        qconfig.themeChangedFinished.connect(_on_theme_changed)
    except Exception:
        pass


def get_dpi_degrade_pixels() -> int:
    """自动降级阈值（整窗物理像素数；0 = 关闭）

    这里是「显式配置 → 静态默认」，不含自适应层（自适应见
    get_effective_degrade_pixels）。配置里**没写** perf_dpi_degrade_pixels
    时返回静态默认 600 万，并记下「未显式指定」，供自适应层判断能否接管。
    """
    global _dpi_degrade_pixels, _dpi_degrade_pixels_explicit
    if _dpi_degrade_pixels is None:
        v = _DEFAULT_DEGRADE_PIXELS
        explicit = False
        try:
            data = app_settings.get_domain("perf")
            if "perf_dpi_degrade_pixels" in data:
                v = int(data["perf_dpi_degrade_pixels"])
                explicit = True
        except Exception:
            pass
        _dpi_degrade_pixels = max(0, v)
        _dpi_degrade_pixels_explicit = explicit
    return _dpi_degrade_pixels


# ---- 自适应降级阈值（P0-1 follow-up，2026-10-07）----
# 起因：静态 600 万是 2026-09-25 在基准机上验证出的经验口径（见
# docs/大屏与超高DPI渲染性能调查报告2026-09-25.md §3.1），而「每帧渲染
# 成本 ≈ 线性于物理像素」的比例系数是**机器相关**的：核显机 / 老
# CPU 在同样像素数下可以慢数倍。静态阈值对快机器会误降级（白丢动画），
# 对慢机器会漏降级（继续卡）。本层用「本机光栅吞吐相对基准机的倍率」
# 去缩放那个经验口径，判定口径不变（仍是物理像素比较），因此：
#   * 基准机上倍率恒为 1 → 阈值仍是 600 万（零回归）；
#   * 慢 X 倍的机器 → 阈值降到 600万/X（更早降级）；
#   * 快 X 倍的机器 → 阈值升到 600万×X（少打扰）。
# 显式配置（perf_dpi_degrade_pixels）永远优先于本层。
_AUTO_REF_MS_PER_MPX = 0.298   # 基准机实测速率，出处见 measure_raster_rate_ms_per_mpx
_AUTO_RATIO_MIN = 0.25         # 机器倍率夹取（防极端值把阈值推到荒唐区间）
_AUTO_RATIO_MAX = 4.0
_AUTO_LIMIT_MIN = 1_200_000
_AUTO_LIMIT_MAX = 24_000_000
_AUTO_CALIB_PIXELS = 2_000_000
_AUTO_CALIB_WARMUP = 2
_AUTO_CALIB_ROUNDS = 3

_raster_rate = None                    # 本机实测速率缓存（ms/百万像素）
_raster_rate_failed = False            # 标定失败过（不再重试，避免每帧重试）
_auto_limit = None                     # 标定出的等效阈值
_dpi_degrade_pixels_explicit = None    # 配置里是否显式写了 perf_dpi_degrade_pixels


def is_degrade_adaptive_enabled() -> bool:
    """自适应阈值开关（perf 域 perf_dpi_degrade_adaptive，默认开）

    关掉 → 完全退回静态 600 万（或 perf_dpi_degrade_pixels 显式值）。
    """
    try:
        data = app_settings.get_domain("perf")
        if "perf_dpi_degrade_adaptive" in data:
            return str(data["perf_dpi_degrade_adaptive"]).lower() not in (
                "false", "0", "no", "")
    except Exception:
        pass
    return True


def measure_raster_rate_ms_per_mpx():
    """实测本机「整面半透明合成」吞吐（ms/百万像素；失败返回 None）

    工作量与基准机标定时**逐条相同**（2026-10-07 tools/_scratch/
    probe_deg_calib.py 的 W1）：1414×1414 ARGB32 源图（alpha=160）以
    painter.setOpacity(0.5) 混合到同尺寸目标图，热身 2 轮后取 3 轮中位。
    基准机（本仓库开发机，offscreen，QT_SCALE_FACTOR 无关）实测
    0.298 ms/百万像素 —— 即 _AUTO_REF_MS_PER_MPX。

    为什么用它：降级判定真正关心的是「每帧按物理像素线性增长的合成/
    光栅成本」，这条路径与之同阶、平台无关（纯 CPU 光栅，不需要窗口
    可见），单次成本亚毫秒级，可以在首次判定时同步标定。

    局限（知情使用）：它反映 CPU 光栅与内存带宽，反映不了 DWM 合成、
    核显纹理上传等进程外成本；因此只用作**机器之间的相对倍率**，
    绝对口径仍锚定基准机上验证过的 600 万像素。
    """
    global _raster_rate, _raster_rate_failed
    if _raster_rate is not None:
        return _raster_rate
    if _raster_rate_failed:
        return None
    try:
        import math
        import time

        from PySide6.QtGui import QColor, QPainter, QPixmap
        from PySide6.QtWidgets import QApplication
        if QApplication.instance() is None:
            return None
        side = int(math.sqrt(_AUTO_CALIB_PIXELS))
        w = max(1, side)
        h = max(1, _AUTO_CALIB_PIXELS // side)
        src = QPixmap(w, h)
        src.fill(QColor(240, 240, 240, 160))
        dst = QPixmap(w, h)
        dst.fill(QColor(120, 120, 120, 255))
        if src.isNull() or dst.isNull():
            _raster_rate_failed = True
            return None
        samples = []
        for i in range(_AUTO_CALIB_WARMUP + _AUTO_CALIB_ROUNDS):
            t0 = time.perf_counter()
            p = QPainter(dst)
            p.setOpacity(0.5)
            p.drawPixmap(0, 0, src)
            p.end()
            if i >= _AUTO_CALIB_WARMUP:
                samples.append((time.perf_counter() - t0) * 1000.0)
        samples.sort()
        med = samples[len(samples) // 2]
        if med <= 0:
            _raster_rate_failed = True
            return None
        _raster_rate = med / ((w * h) / 1e6)
        return _raster_rate
    except Exception:
        _raster_rate_failed = True
        return None


def get_auto_degrade_pixels():
    """自适应阈值（= 静态默认 × 基准速率/本机速率，夹取后；不可用 None）"""
    global _auto_limit
    if _auto_limit is not None:
        return _auto_limit
    if not is_degrade_adaptive_enabled():
        return None
    rate = measure_raster_rate_ms_per_mpx()
    if rate is None or rate <= 0:
        return None
    ratio = _AUTO_REF_MS_PER_MPX / rate
    ratio = min(_AUTO_RATIO_MAX, max(_AUTO_RATIO_MIN, ratio))
    limit = int(min(_AUTO_LIMIT_MAX,
                    max(_AUTO_LIMIT_MIN, _DEFAULT_DEGRADE_PIXELS * ratio)))
    _auto_limit = limit
    _log(f"[perf] 自适应降级阈值：本机合成速率 {rate:.3f} ms/百万像素"
         f"（基准 {_AUTO_REF_MS_PER_MPX:.3f}，倍率 {ratio:.2f}×）→ 阈值 "
         f"{_DEFAULT_DEGRADE_PIXELS} → {limit} 物理像素")
    return _auto_limit


def get_effective_degrade_pixels() -> int:
    """当前**实际生效**的降级阈值（显式配置 > 自适应标定 > 静态默认）

    0 = 关闭降级（显式配置才可能拿到 0；自适应层夹取下界 120 万）。

    必须先调 get_dpi_degrade_pixels()：只有它会读配置并置
    _dpi_degrade_pixels_explicit —— 否则「用户显式写了值」这件事永远看不到，
    自适应会盖掉用户意图。
    """
    static = get_dpi_degrade_pixels()
    if _dpi_degrade_pixels_explicit:
        return static
    auto = get_auto_degrade_pixels()
    if auto is not None and is_degrade_adaptive_enabled():
        return auto
    return static


def degrade_diagnostics_text() -> str:
    """降级判定诊断摘要（性能选项对话框「渲染加速状态」行）"""
    try:
        limit = get_effective_degrade_pixels()
        if limit <= 0:
            base = "已关闭（配置 perf_dpi_degrade_pixels=0）"
        elif _dpi_degrade_pixels_explicit:
            base = f"{limit / 1e4:.0f} 万像素（配置指定）"
        elif get_auto_degrade_pixels() is not None:
            rate = _raster_rate or 0.0
            base = (f"{limit / 1e4:.0f} 万像素（自适应：本机合成 "
                    f"{rate:.3f} ms/百万像素，基准 {_AUTO_REF_MS_PER_MPX:.3f}）")
        else:
            base = f"{limit / 1e4:.0f} 万像素（静态默认，自适应不可用）"
        if is_bigscreen_mode():
            base += "；大屏性能模式开启（强制降级）"
        return "自动降级阈值：" + base
    except Exception:
        return "自动降级阈值：读取失败"


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
_FADE_MODE_EXPLICIT = ("card", "opacity", "snapshot", "direct")


def get_dialog_fade_mode() -> str:
    """弹窗淡入淡出实现（P1-1 / P0-1 follow-up）：

    - 'auto'（默认）：自动选择 —— 大屏性能模式 → 'direct'；超阈值
      （P0-1）→ 'snapshot'（快照淡入，超阈值机器也保留淡入）；
      其余 → 'card'。
    - 'card'：强制卡片级 QGraphicsOpacityEffect 淡入/淡出（P1-1 原实现），
      遮罩即时出现（与 WinUI ContentDialog 同款行为），离屏面积从
      整窗（829 万像素级）缩到卡片（~25 万像素级），每帧成本降一个
      量级；淡入结束后恢复卡片阴影 effect（Qt 单控件仅允许一个
      graphicsEffect，淡入期间与阴影互斥）。
    - 'snapshot'：强制快照淡入（每帧只剩一次整区半透明混合）。
    - 'direct'：强制直显（P0-1 原降级行为：无渐变 + 卡片阴影半径 30）。
    - 'opacity'：回退库原生整窗 QGraphicsOpacityEffect 路径（逃生门，
      改 perf 域 perf_dialog_fade_mode 即可，无需改码）。

    注：'card'/'opacity'/'direct' 在「大屏模式」或「超阈值」下会被降级
    路径接管（见 resolve_dialog_fade_mode），'snapshot' 在超阈值下仍生效。
    四个值以外的写法（含旧的 'auto' 缺省行为以外的脏值）一律按 'auto'。
    实现注记（2026-09-25 实测）：windowOpacity 属性动画方案不可行——
    qfw MaskDialogBase 在 __init__ 里 setWindowFlags(Qt.FramelessWindowHint)
    整组替换窗口标志，丢失 Qt.Dialog 位，弹窗实际是父窗内的**子部件
    覆盖层**（isWindow()==False、无 windowHandle），setWindowOpacity
    对其无效。
    """
    global _dialog_fade_mode
    if _dialog_fade_mode not in (("auto",) + _FADE_MODE_EXPLICIT):
        v = "auto"
        try:
            data = app_settings.get_domain("perf")
            raw = str(data.get("perf_dialog_fade_mode") or "").strip().lower()
            if raw in _FADE_MODE_EXPLICIT:
                v = raw
        except Exception:
            pass
        _dialog_fade_mode = v
    return _dialog_fade_mode


def resolve_dialog_fade_mode(dlg=None) -> str:
    """解析本次弹窗实际使用的实现（返回 'card'/'opacity'/'snapshot'/'direct'）

    优先级（定稿）：
    1. 大屏性能模式（P1-4 = 一键全量降级）→ 'direct'，压过实现选择；
    2. 超阈值（P0-1 像素降级）→ 显式 'snapshot' 仍是 'snapshot'（唯一
       在降级机器上保留动画的实现：每帧只有一次半透明混合）；'auto'
       在超阈值机器上的落点也是 'snapshot'；其余显式实现选择
       （'card'/'opacity'/'direct'）被降级接管为 'direct'；
    3. 其余：显式四值照还，'auto' → 'card'。

    口径：降级是安全网，优先于实现偏好；要在超阈值机器上强制别的实现，
    请显式关掉阈值（perf 域 perf_dpi_degrade_pixels=0）。第 2 条同时是
    既有回归用例的契约（tests/test_perf_dpi_p1.py 的 fade_on 用例）。
    动画总开关关闭时由调用方短路成直显，本函数不处理。
    """
    mode = get_dialog_fade_mode()
    try:
        if is_bigscreen_mode():
            return "direct"
        if dlg is not None and _over_dpi_degrade(dlg):
            if mode == "snapshot":
                return "snapshot"
            if mode in _FADE_MODE_EXPLICIT:
                return "direct"
            return "snapshot"        # 'auto'：超阈值机器的自动落点（item2 本意）
    except Exception:
        pass
    if mode in _FADE_MODE_EXPLICIT:
        return mode
    return "card"


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
    （显式 perf_dpi_degrade_pixels 优先，缺省按自适应标定的等效阈值，
    再兜底静态 600 万；0=关闭）。
    """
    try:
        if is_bigscreen_mode():
            return True
        limit = get_effective_degrade_pixels()
        if limit <= 0:
            return False
        px = (screen_physical_pixels(widget) if use_screen
              else window_physical_pixels(widget))
        return px >= limit
    except Exception:
        return False


# ---- P0-1 follow-up：超阈值机器的「快照淡入」（2026-10-07）----
# 起因：P0-1 为解决超阈值机器上「整窗 200ms effect 淡入」过重（报告
# §3.1：829 万像素 48.4ms/帧、约 1.8~3.6×）把超阈值弹窗改成**无动画
# 直显**——代价是大屏机器彻底丢掉淡入感。快照淡入：打开瞬间把
# 「卡片 + 阴影」渲染成一张**透明底位图**，随后 200ms 只做「整区 pixmap
# 半透明混合」（自绘，不挂任何 QGraphicsEffect），画面等价而每帧成本从
# 「卡片级 effect 的源图离屏渲染 + 合成」降到一次混合。
#
# 实测（2026-10-07 tools/_scratch/probe_snap_v3.py，offscreen；
# 1600x900@1.0 / 2560x1440@1.5 / @2.0）：快照构建 1.38 / 3.23 / 5.03 ms；
# 13 帧真实淡入的每帧自绘中位 0.06 / 0.95 / 0.20 ms；与「遮罩 + 卡片」
# 参考画面的平均通道差 0.34 / 0.30 / 0.25（最大差集中在 AA 与阴影边缘）。
#
# 两条由实测得出、必须遵守的约束：
# 1) 构建必须在 dialog **没有 graphics effect** 时进行：offscreen 实测
#    dialog 自身挂 effect 时 render/grab 会丢全部子控件（Qt 报
#    QWidgetEffectSourcePrivate::pixmap: Painter not active + QPainter
#    「A paint device can only be painted by one painter at a time」）。
# 2) 只用 RenderFlag.DrawChildren：DrawWindowBackground 对半透明 dialog
#    会填成不透明黑（实测），快照必须自己是透明底。
_SNAPSHOT_MARGIN = 72        # 卡片外扩量：阴影 blur 60 + offset y10 需完整入图
_SNAPSHOT_MIN_ALPHA = 250    # 卡片中心像素低于此 alpha 视为内容不可信
_snapshot_logged = False


try:  # 快照图层是本模块唯一的类定义：Qt 其余部分一律函数内惰性导入，
    # 这里必须在类定义**前**拿到基类；无 Qt 环境退化为 object（不会实例化）。
    from PySide6.QtWidgets import QWidget as _QtWidgetBase
except Exception:  # pragma: no cover
    _QtWidgetBase = object


class _SnapshotOverlay(_QtWidgetBase):
    """快照淡入图层：自绘一张带透明边缘的位图，整区按 t 混合

    刻意**不挂任何 QGraphicsEffect**（挂 effect 等于回到 P0-1 要消除的
    那条通路：每帧额外离屏渲染 + 合成），而是用 painter.setOpacity 在
    一次 drawPixmap 里完成混合。
    """

    def __init__(self, parent, pixmap, rect):
        super().__init__(parent)
        from PySide6.QtCore import Qt as _Qt
        self._pm = pixmap
        self._t = 0.0
        self.setGeometry(rect)
        self.setAttribute(_Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(_Qt.WidgetAttribute.WA_NoSystemBackground, True)

    def set_snapshot_opacity(self, t):
        """设置当前淡入进度（0.0~1.0），只重绘不重排"""
        try:
            self._t = max(0.0, min(1.0, float(t)))
        except Exception:
            self._t = 1.0
        self.update()

    def paintEvent(self, _event):
        try:
            from PySide6.QtGui import QPainter
            p = QPainter(self)
            p.setOpacity(self._t)
            p.drawPixmap(0, 0, self._pm)
            p.end()
        except Exception:
            pass


def _restore_dialog_shadow(dlg, blur_radius):
    """恢复中央卡片的阴影 effect（Qt 单控件仅允许一个 graphicsEffect）"""
    try:
        from PySide6.QtGui import QColor
        dlg.setShadowEffect(int(blur_radius), (0, 10), QColor(0, 0, 0, 100))
    except Exception:
        pass


def _build_dialog_snapshot(dlg, card, margin: int = _SNAPSHOT_MARGIN):
    """把「卡片 + 阴影」渲染成透明底快照位图；返回 (pixmap, rect)

    条件不足或内容不可信时返回 (None, None)（调用方回退直显，绝不留下
    「演一张空图」的画面）。构建期间临时隐藏遮罩、临时显示卡片：
    QWidget.render 只画可见子控件，而淡入期间卡片本身是隐藏的（由快照
    代演）。构建结束后无论成败都恢复两者的可见性。
    """
    try:
        from PySide6.QtCore import QPoint, QRect
        from PySide6.QtGui import QPixmap, QRegion, qAlpha
        from PySide6.QtWidgets import QWidget as _QWidget
        from PySide6.QtCore import Qt as _Qt
        r = QRect(card.geometry()).adjusted(-margin, -margin, margin, margin)
        r = r.intersected(dlg.rect())
        if r.width() < 16 or r.height() < 16:
            return None, None
        try:
            dpr = float(dlg.devicePixelRatioF() or 1.0)
        except Exception:
            dpr = 1.0
        pm = QPixmap(int(round(r.width() * dpr)), int(round(r.height() * dpr)))
        if pm.isNull():
            return None, None
        pm.setDevicePixelRatio(dpr)
        pm.fill(_Qt.GlobalColor.transparent)
        mask = getattr(dlg, "windowMask", None)
        mask_shown = bool(mask is not None and mask.isVisible())
        card_shown = bool(card.isVisible())
        import time
        t0 = time.perf_counter()
        try:
            if mask_shown:
                mask.setVisible(False)
            if not card_shown:
                card.setVisible(True)
            dlg.render(pm, QPoint(0, 0), QRegion(r),
                       _QWidget.RenderFlag.DrawChildren)
        finally:
            if not card_shown:
                card.setVisible(False)
            if mask_shown:
                mask.setVisible(True)
        cost_ms = (time.perf_counter() - t0) * 1000.0
        # 内容可信性：卡片中心必须是不透明像素（抓空了宁可回退直显）
        c = card.geometry().center() - r.topLeft()
        probe = QRect(int(c.x() * dpr), int(c.y() * dpr), 1, 1)
        img = pm.copy(probe).toImage()
        if img.isNull() or qAlpha(img.pixel(0, 0)) < _SNAPSHOT_MIN_ALPHA:
            return None, None
        global _snapshot_logged
        if not _snapshot_logged:
            _snapshot_logged = True
            _log(f"[perf] 快照淡入已启用：快照 {r.width()}x{r.height()} @dpr "
                 f"{dpr:.2f}，构建 {cost_ms:.2f} ms（阈值 "
                 f"{_DEFAULT_DEGRADE_PIXELS} / 自适应口径，见 perf.ini）")
        return pm, r
    except Exception:
        return None, None


def _cleanup_snapshot_fade(dlg, blur_radius=None, show_card: bool = True,
                           overlay=None):
    """收尾：撤掉快照图层、可选恢复阴影/卡片（幂等、可反复调用）

    Qt 的 QAbstractAnimation.stop() 不触发 finished，所以「淡入在途时
    弹窗被 done / 被重新 show」这类路径必须显式调用本函数兜底。
    """
    ov = overlay if overlay is not None else getattr(
        dlg, "_perf_snapshot_overlay", None)
    if ov is not None and getattr(dlg, "_perf_snapshot_overlay", None) is ov:
        try:
            dlg._perf_snapshot_overlay = None
        except Exception:
            pass
    if ov is not None:
        try:
            ov.hide()
            ov.deleteLater()
        except Exception:
            pass
    if blur_radius is not None:
        _restore_dialog_shadow(dlg, blur_radius)
    if show_card:
        try:
            dlg.widget.setVisible(True)
        except Exception:
            pass


def _animate_snapshot_fade(dlg, card, pm, rect, blur_radius):
    """把快照图层从 0 淡到 1（200ms / InSine，与库原生淡入同参数）

    每帧比对卡片几何：布局或尺寸一旦变化，快照与真实卡片就不再对齐，
    立即收尾回到真卡片（宁可动画短一截，也不要错位的画面）。
    """
    from PySide6.QtCore import QEasingCurve, QVariantAnimation
    ov = _SnapshotOverlay(dlg, pm, rect)
    dlg._perf_snapshot_overlay = ov
    card_geo = card.geometry()
    state = {"done": False}

    def _finish(*_a):
        if state["done"]:
            return
        state["done"] = True
        try:
            if getattr(dlg, "_perf_fade_ani", None) is ani:
                dlg._perf_fade_ani = None
        except Exception:
            pass
        _cleanup_snapshot_fade(dlg, blur_radius=blur_radius, overlay=ov)

    ani = QVariantAnimation(dlg)
    ani.setStartValue(0.0)
    ani.setEndValue(1.0)
    ani.setDuration(200)
    try:
        ani.setEasingCurve(QEasingCurve.Type.InSine)
    except Exception:
        pass

    def _on_value(v):
        try:
            ov.set_snapshot_opacity(v)
            if card.geometry() != card_geo:
                ani.stop()
                _finish()
        except Exception:
            try:
                ani.stop()
            except Exception:
                pass
            _finish()

    ani.valueChanged.connect(_on_value)
    ani.finished.connect(_finish)
    dlg._perf_fade_ani = ani
    ov.show()
    ov.raise_()
    ani.start()


def _start_snapshot_fade(dlg, blur_radius) -> bool:
    """准备快照淡入：先藏卡片、再排定懒构建（False = 条件不足，走直显）

    为什么要等下一次事件循环才构建：showEvent 里弹窗还没真正显示完，
    此时渲染未必拿得到最终布局；卡片必须先保持隐藏，否则会先以全不
    透明度闪一帧再被快照接管。
    """
    try:
        card = dlg.widget
    except Exception:
        return False
    if card is None:
        return False
    try:
        card.setVisible(False)
    except Exception:
        return False

    def _build():
        try:
            if not dlg.isVisible():
                _cleanup_snapshot_fade(dlg)
                return
            pm, rect = _build_dialog_snapshot(dlg, card)
            if pm is None:
                _cleanup_snapshot_fade(dlg)
                return
            _animate_snapshot_fade(dlg, card, pm, rect, blur_radius)
        except Exception:
            _cleanup_snapshot_fade(dlg)

    from PySide6.QtCore import QTimer
    QTimer.singleShot(0, _build)
    return True


def patch_dialog_animation():
    """中央拦截 MaskDialogBase 淡入/淡出动画（幂等）

    背景：MessageBoxBase（含项目内 6 个弹窗：编辑售后/球桌/设备/署名统计/
    单视频等）继承 MaskDialogBase，打开/关闭时用 QGraphicsOpacityEffect +
    QPropertyAnimation 做**整窗**透明度渐变（根挂 effect，遮罩+卡片全走
    离屏光栅），实测 829 万像素下单帧 ≥48ms（真机核显再放大到数百 ms），
    是场景 C「未响应」的主源。策略（按优先级）：

    1. 动画开关（面板覆盖→全局）为关 → 直显（秒开无渐变）；
    2. 大屏性能模式（P1-4）开启 → 直显 + 阴影半径 30；
    3. 整窗物理像素 ≥ 生效阈值（显式 perf_dpi_degrade_pixels → 自适应
       标定 → 静态 600 万，P0-1）→ **快照淡入**（P0-1 follow-up，
       2026-10-07；超阈值机器也保留淡入，每帧只剩一次整区半透明混合）
       + 阴影半径 30；
    4. 其余：'card' 淡入淡出（P1-1，默认）——effect 只挂中央卡片（离屏
       面积从整窗缩到卡片），遮罩即时出现（WinUI ContentDialog 同款
       行为），淡入结束恢复卡片阴影。逃生门：perf 域
       perf_dialog_fade_mode 显式取 'card'/'snapshot'/'direct'/'opacity'。

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
        """恢复卡片阴影 effect（淡入/淡出结束后调用；Qt 单控件单 effect）

        具体实现走模块级 _restore_dialog_shadow：快照淡入的收尾也在模块级
        函数里，两处必须一致。
        """
        _restore_dialog_shadow(self, blur_radius)

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
            anim_on = get_animation(_window_panel_key(self))
            degrade = _over_dpi_degrade(self)
            mode = "none" if not anim_on else resolve_dialog_fade_mode(self)
            if mode == "snapshot":
                # P0-1 follow-up：快照淡入（超阈值机器保留淡入）
                blur = 30 if degrade else 60
                _stop_fade(self)
                _cleanup_snapshot_fade(self, show_card=False)
                try:  # 根不挂 effect（整窗离屏源），也不给卡片留淡入 effect
                    self.setGraphicsEffect(None)
                except Exception:
                    pass
                _restore_dialog_shadow(self, blur)
                if _start_snapshot_fade(self, blur):
                    super(MaskDialogBase, self).showEvent(e)
                    return
                mode = "direct"  # 条件不足（无卡片等）→ 直显兜底
            if mode in ("none", "direct"):
                if degrade:
                    try:  # P0-1：大屏阴影降半径（公开 API，重建 effect）
                        self.setShadowEffect(30, (0, 10), QColor(0, 0, 0, 100))
                    except Exception:
                        pass
                _stop_fade(self)
                _cleanup_snapshot_fade(self)
                self.setGraphicsEffect(None)
                self.widget.setGraphicsEffect(None)
                _restore_shadow(self, 30 if degrade else 60)
                super(MaskDialogBase, self).showEvent(e)
                return
            if mode == "card":
                _stop_fade(self)
                _cleanup_snapshot_fade(self)
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
                    _restore_dialog_shadow(self, 60)
        except Exception:
            pass
        _orig_show(self, e)

    def _done(self, code):
        try:
            # 在途快照淡入：Qt 的 stop() 不触发 finished，必须显式收尾
            _cleanup_snapshot_fade(self)
            anim_on = get_animation(_window_panel_key(self))
            mode = "none" if not anim_on else resolve_dialog_fade_mode(self)
            if mode in ("none", "direct", "snapshot"):
                _stop_fade(self)
                self.setGraphicsEffect(None)
                self.widget.setGraphicsEffect(None)
                QDialog.done(self, code)
                return
            if mode == "card":
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
    global _dpi_degrade_pixels_explicit, _auto_limit, _raster_rate
    global _raster_rate_failed, _snapshot_logged
    _acrylic_enabled = None
    _animation_enabled = None
    _table_smooth_enabled = None
    _lean_delegate_enabled = None
    _dpi_degrade_pixels = None
    _bigscreen_mode = None
    _dialog_fade_mode = None
    # 自适应降级阈值：配置可能刚改过（含 perf_dpi_degrade_adaptive），
    # 速率缓存与标定结果一并作废
    _dpi_degrade_pixels_explicit = None
    _auto_limit = None
    _raster_rate = None
    _raster_rate_failed = False
    _snapshot_logged = False
    _panel_table_overrides = None
    _panel_animation_overrides = None
