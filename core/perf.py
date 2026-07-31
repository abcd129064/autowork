# -*- coding: utf-8 -*-
"""性能选项管理模块 —— 细粒度运行时开关，切换即时生效，无需重启。

settings.json 字段：
  - "perf_acrylic":   true/false  亚克力磨砂效果（截屏→高斯模糊，核显开销大）
  - "perf_animation": true/false  菜单弹出动画

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


def _settings_path() -> str:
    from core.app_paths import get_app_dir
    return os.path.join(get_app_dir(), "settings.json")


def _load_perf_settings():
    """从 settings.json 加载性能选项（首次调用时执行，含旧字段迁移）"""
    global _acrylic_enabled, _animation_enabled
    acrylic, animation = True, True
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
    except Exception:
        pass
    _acrylic_enabled = acrylic
    _animation_enabled = animation


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
    global _acrylic_enabled, _animation_enabled
    _acrylic_enabled = None
    _animation_enabled = None
