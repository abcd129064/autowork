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

import json
import os

# 模块级运行时状态（None = 尚未从 settings.json 加载）
_acrylic_enabled: bool | None = None
_animation_enabled: bool | None = None
_table_smooth_enabled: bool | None = None

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

# 面板窗口类名 → 面板标识（菜单动画中央补丁按父链识别所属面板用）
_PANEL_WINDOW_CLASSES = {
    "AftersalePanelWindow": "aftersale",
    "LedgerPanelWindow": "video",
}


def _settings_path() -> str:
    from core.app_paths import get_app_dir
    return os.path.join(get_app_dir(), "settings.json")


def _load_perf_settings():
    """从 settings.json 加载性能选项（首次调用时执行，含旧字段迁移）"""
    global _acrylic_enabled, _animation_enabled, _table_smooth_enabled
    acrylic, animation, table_smooth = True, True, False
    try:
        path = _settings_path()
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
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
    except Exception:
        pass
    _acrylic_enabled = acrylic
    _animation_enabled = animation
    _table_smooth_enabled = table_smooth


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
    """从 settings.json 加载各面板的平滑滚动覆盖值（未设置的面板不在 dict 中）"""
    global _panel_table_overrides
    ov = {}
    try:
        path = _settings_path()
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
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


def set_table_smooth(panel: str | None, enabled: bool):
    """设置面板级（panel 非空）或全局（panel=None）平滑滚动开关并持久化"""
    global _panel_table_overrides
    enabled = bool(enabled)
    if panel:
        if _panel_table_overrides is None:
            _load_panel_table_overrides()
        _panel_table_overrides[panel] = enabled
        key = _PANEL_TABLE_KEYS.get(panel)
        if key:
            _persist(key, enabled)
    else:
        set_table_smooth_scroll_enabled(enabled)


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
    """从 settings.json 加载各面板的动画覆盖值（未设置的面板不在 dict 中）"""
    global _panel_animation_overrides
    ov = {}
    try:
        path = _settings_path()
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
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


def _menu_panel_key(menu) -> str | None:
    """沿菜单父链向上找所属面板窗口（主界面/其它窗口返回 None 走全局）"""
    w = menu.parentWidget() if hasattr(menu, "parentWidget") else None
    while w is not None:
        panel = _PANEL_WINDOW_CLASSES.get(type(w).__name__)
        if panel:
            return panel
        w = w.parentWidget()
    return None


def patch_menu_animation():
    """中央拦截：按「面板覆盖→全局」生效值降级菜单弹出动画（幂等）

    qfluentwidgets 所有菜单动画（右键 RoundMenu、ComboBox/EditableComboBox
    下拉、LineEdit 编辑菜单等）都汇聚到 MenuAnimationManager.make()；
    库内 ComboBox 下拉硬编码 DROP_DOWN/PULL_UP，单靠调用点传参无法覆盖，
    故在此统一拦截：动画关闭时降级为 NONE，下一次弹出即生效。
    """
    try:
        from qfluentwidgets.components.widgets.menu import (
            MenuAnimationManager, MenuAnimationType)
    except Exception:
        return
    if getattr(MenuAnimationManager, "_perf_patched", False):
        return
    _orig_make = MenuAnimationManager.make.__func__

    @classmethod
    def _make(cls, menu, aniType):
        try:
            if aniType != MenuAnimationType.NONE and \
                    not get_animation(_menu_panel_key(menu)):
                aniType = MenuAnimationType.NONE
        except Exception:
            pass
        return _orig_make(cls, menu, aniType)

    MenuAnimationManager.make = _make
    MenuAnimationManager._perf_patched = True


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
    """将单个性能选项写入 settings.json（保留其余字段不变）"""
    try:
        path = _settings_path()
        data = {}
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        data[key] = value
        # 迁移完成后移除旧字段，避免歧义
        data.pop("performance_mode", None)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
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
    _acrylic_enabled = None
    _animation_enabled = None
    _table_smooth_enabled = None
    _panel_table_overrides = None
    _panel_animation_overrides = None
