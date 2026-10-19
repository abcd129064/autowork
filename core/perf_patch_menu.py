# -*- coding: utf-8 -*-
"""菜单弹出动画降级补丁（B1 patch 层第五片）。

从 core/perf.py 下沉：拦截 MenuAnimationManager.make，按动画开关与像素阈值
降级 NONE；未降级时把 DROP_DOWN/PULL_UP 映射为库自带 FADE_IN_*。
判定回调由 core.perf 注入，保留 get_animation/_over_dpi_degrade monkeypatch 点。
"""


def apply_menu_animation_patch(animation_enabled, over_degrade) -> None:
    """安装菜单动画补丁（幂等）。

    animation_enabled(menu) -> bool：该菜单生效的动画开关；
    over_degrade(menu, use_screen=True) -> bool：像素阈值判定。
    """
    try:
        from qfluentwidgets.components.widgets.menu import (
            MenuAnimationManager, MenuAnimationType)
    except Exception:
        return
    if getattr(MenuAnimationManager, "_perf_patched", False):
        return
    orig_make = MenuAnimationManager.make.__func__
    fade_map = {
        MenuAnimationType.DROP_DOWN: MenuAnimationType.FADE_IN_DROP_DOWN,
        MenuAnimationType.PULL_UP: MenuAnimationType.FADE_IN_PULL_UP,
    }

    @classmethod
    def _make(cls, menu, aniType):
        try:
            if aniType != MenuAnimationType.NONE:
                if not animation_enabled(menu) or                         over_degrade(menu, use_screen=True):
                    aniType = MenuAnimationType.NONE
                elif aniType in fade_map:
                    aniType = fade_map[aniType]
        except Exception:
            pass
        return orig_make(cls, menu, aniType)

    MenuAnimationManager.make = _make
    MenuAnimationManager._perf_patched = True
