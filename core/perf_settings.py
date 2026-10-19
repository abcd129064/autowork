# -*- coding: utf-8 -*-
"""性能选项纯解析（B1 第一步）。

从 core/perf.py 下沉：把 app_settings 返回的 perf 域 dict 解析为运行时开关，
以及面板级覆盖的生效值选择。不 import Qt / qfluentwidgets / core.perf；
core.perf 负责读取配置与维护缓存，本模块只做纯函数，便于直接单测。
"""

# 默认值：亚克力/动画开启，表格平滑滚动关闭，轻量 delegate 开启
_DEFAULTS = {
    "acrylic": True,
    "animation": True,
    "table_smooth": False,
    "lean_delegate": True,
}


def default_global_settings() -> dict:
    """返回全局性能开关默认值副本。"""
    return dict(_DEFAULTS)


def resolve_global_settings(data) -> dict:
    """把 perf 域配置 dict 解析为全局开关（含旧 performance_mode 迁移）。

    与旧 _load_perf_settings 行为一致：
    - 新键存在时按 bool 解析；
    - 否则旧 performance_mode=True/'true' 表示关闭亚克力与动画；
    - perf_lean_delegate 缺省 True，非 bool 时按字符串 != 'false' 解析。
    """
    data = data if isinstance(data, dict) else {}
    acrylic, animation, table_smooth = True, True, False
    legacy_off = data.get("performance_mode") in (True, "true")
    if "perf_acrylic" in data:
        acrylic = bool(data["perf_acrylic"])
    elif legacy_off:
        acrylic = False
    if "perf_animation" in data:
        animation = bool(data["perf_animation"])
    elif legacy_off:
        animation = False
    if "perf_table_smooth" in data:
        table_smooth = bool(data["perf_table_smooth"])
    lean = data.get("perf_lean_delegate", True)
    lean = lean if isinstance(lean, bool) else str(lean) != "false"
    return {"acrylic": acrylic, "animation": animation,
            "table_smooth": table_smooth, "lean_delegate": lean}


def panel_overrides(data, keys: dict) -> dict:
    """从 perf 域 dict 提取面板级覆盖：{panel: bool}，未设置的面板不出现。"""
    data = data if isinstance(data, dict) else {}
    return {panel: bool(data[key]) for panel, key in keys.items() if key in data}


def effective_value(global_value, overrides, panel):
    """生效值：指定面板有覆盖则用覆盖，否则回退全局值。"""
    if panel and overrides and panel in overrides:
        return overrides[panel]
    return global_value
