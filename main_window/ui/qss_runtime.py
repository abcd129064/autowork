# -*- coding: utf-8 -*-
"""窗口 QSS 运行时原语（无 UIMixin 依赖）。

从 main_window.ui_mixin 下沉；调用方传入显式 widget，模块本身不持有窗口状态。
业务 QSS 必须挂到 stackedWidget（不要挂窗口）以保住 DWM 云母，原因见
apply_business_qss 的 docstring；reset_qss_compose_trees 负责清理 qfw 的
StyleSheetCompose 嵌套膨胀。
"""

import os

from qfluentwidgets import (FluentStyleSheet, qconfig,
                            setCustomStyleSheet)
from qfluentwidgets.common.style_sheet import (styleSheetManager,
    StyleSheetCompose, CustomStyleSheet, getStyleSheet)

from core.app_paths import get_resource_dir


def load_qss(theme_name):
    """从 styles/ 目录加载 QSS；找不到返回空串，并把青色锚点替换为强调色。"""
    qss_path = os.path.join(get_resource_dir(), 'styles', f'{theme_name}.qss')
    try:
        with open(qss_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except Exception:
        return ''
    from core.theme_qss import substitute_accent
    return substitute_accent(text)


def apply_business_qss(window):
    """把业务 QSS 挂到 window.stackedWidget（云母修复，2026-09-07）。

    真机洋红壁纸对照实证：任何窗口级 setStyleSheet 都会永久改变原生窗口
    表面的合成格式、杀死 DWM 云母渲染（不可逆：清空/DWM 重设/hide+show/
    setWindowFlags 重建均无法恢复）。所有业务页面均位于 stackedWidget
    之下，故业务 QSS 挂到 stackedWidget，子控件样式效果与原先一致。

    必须走 qfw 的 CustomStyleSheet 机制：setCustomStyleSheet 把业务 qss 写入
    widget 的 light/darkCustomQss 动态属性，渲染结果 = FLUENT_WINDOW qss +
    业务 qss 合并。此后 qfw 主题切换/updateStyleSheet/dirty-qss 重设的全部
    路径都渲染合并表，业务 qss 永不被覆盖。

    幂等守卫写在 window._business_qss_applied：重复调用会经
    DynamicPropertyChange 每次向合并源追加嵌套（实测每次切主题 +2 层：
    17039 → 23906 → 32255 → 45846 字符），纯浪费且拖慢样式解析。
    """
    if getattr(window, "_business_qss_applied", False):
        return False
    setCustomStyleSheet(window.stackedWidget,
                        load_qss('light'), load_qss('dark'))
    window._business_qss_applied = True
    return True


def reset_qss_compose_trees():
    """主题切换后重置 qfw 样式合并源树，消除嵌套膨胀（2026-09-07）。

    qfw updateStyleSheet 对每个注册控件执行 setStyleSheet(widget, file)，
    内部 register(reset=True) 走 widgets[widget] = Compose([file,
    CustomStyleSheet(widget)])，而 file 本身就是上一轮的旧树——旧树被整体
    包进新树且 Compose 去重只挡「同一对象」，挡不住新包出来的层。setTheme
    与 setThemeColor(lazy=True) 各触发一次全量轮询，每切一次主题每个控件
    +1~2 层，qss 字符串随切换次数线性膨胀（stackedWidget 实测 17039 →
    23906 → 32255 → 45846 字符），且无法自行收敛。

    这里逐控件下钻到最内层 Compose，按其第一个孩子的类型把树重建为干净
    结构（通常 Compose([FluentStyleSheet, CustomStyleSheet])）。
    CustomStyleSheet 动态读取 widget 的 light/darkCustomQss 属性，
    setCustomStyleSheet 写入的业务 qss 自动保留；树已干净时重建为等价
    结构，幂等无害。仅当控件已销毁（RuntimeError）时跳过。
    """
    manager = styleSheetManager
    theme = qconfig.theme
    for widget in list(manager.widgets):
        try:
            source = manager.source(widget)
            # 下钻：外层 Compose 的第一个孩子仍是 Compose 则继续深入
            while (isinstance(source, StyleSheetCompose) and source.sources
                   and isinstance(source.sources[0], StyleSheetCompose)):
                source = source.sources[0]
            if not (isinstance(source, StyleSheetCompose) and source.sources):
                continue
            head = source.sources[0]
            if isinstance(head, FluentStyleSheet):
                clean = StyleSheetCompose([head, CustomStyleSheet(widget)])
            elif isinstance(head, CustomStyleSheet):
                # 纯 CustomStyleSheet 树（从未被库 apply 过原生源）：
                # 动态属性即全部内容，单源重建即可
                clean = StyleSheetCompose([CustomStyleSheet(widget)])
            else:
                continue
            manager.widgets[widget] = clean
            widget.setStyleSheet(getStyleSheet(clean, theme))
        except RuntimeError:
            continue


def enforce_toolbar_radio_height(ui):
    """工具栏 RadioButton 固定 32px 行高（与按钮中线对齐）。

    使用 setCustomStyleSheet 注册到主题管理器，setTheme 后自动重应用，
    不会被库内部 QSS (min/max-height: 24px) 覆盖。
    """
    qss = "QRadioButton { min-height: 32px; max-height: 32px; }"
    for rb in (ui.input_frame_before, ui.input_frame_set, ui.input_frame_custom):
        setCustomStyleSheet(rb, qss, qss)


def enforce_toolbar_button_height(ui):
    """工具栏 qfluentwidgets 按钮/输入控件固定 32px 行高。

    qfw 全局按钮 QSS 的 min-height 会在 polish 时把 minimumHeight 覆盖为 35，
    而 setupUi 里 setFixedHeight(32) 只设了 maximumHeight=32，形成
    min(35)>max(32) 矛盾，sizeHint=35 被 FlowLayout 按 sizeHint 拉伸，
    按钮底部 1-2px 露出背景色。setTheme/polish 完成后重新 setFixedHeight(32)，
    min=max=32、geometry 被 clamp 到 32，与同行的 32px 控件完全平齐。
    """
    for name in ("write_table", "btn_write_table", "choose_exe",
                 "open_daily", "start", "pause_btn", "start_three_btn",
                 "btn_aftersale", "table_panel_btn", "p2p_btn",
                 "date", "input_frame"):
        w = getattr(ui, name, None)
        if w is None:
            continue
        try:
            w.setFixedHeight(32)
        except Exception:
            pass
