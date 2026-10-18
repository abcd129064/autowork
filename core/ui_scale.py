# -*- coding: utf-8 -*-
"""启动期 DPI 缩放策略（无 Qt 依赖，可在 QApplication 创建前调用）。

从 main_window.ui_mixin.UIMixin.apply_dpi_scale 原样下沉：
- 写 QT_SCALE_FACTOR（仅非 100% 时）；
- 应用内缩放 × 系统缩放叠加告警（系统 ≥150% 时），只告警不阻断。
"""

import os


def apply_dpi_scale(settings):
    """按合并配置 dict 应用 DPI 缩放；任何异常都静默，不阻断启动。"""
    try:
        scale = settings.get("dpi_scale", 100)
        if scale != 100:
            os.environ["QT_SCALE_FACTOR"] = str(scale / 100.0)
            # P1-2（2026-09-25）叠加告警：系统缩放 ≥150% 时应用内缩放与之
            # 相乘（双 150% → dpr 2.25 → 物理像素 ×5），可把任何机器推入
            # 「未响应」量级（docs/大屏与超高DPI渲染性能调查报告 §4.2）。
            # 策略=告警不阻断：注册表探测系统缩放（pre-Qt 无副作用），
            # 命中则落连接日志 + 供设置页「界面缩放」行内提示。
            try:
                from core.perf import (detect_system_scale_percent,
                                       set_dpi_stack_warning)
                sys_pct = detect_system_scale_percent()
                if sys_pct and sys_pct >= 150:
                    set_dpi_stack_warning(int(scale), sys_pct)
            except Exception:
                pass
    except Exception:
        pass
