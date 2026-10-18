# -*- coding: utf-8 -*-
"""外观服务：主题应用、全局字体、系统主题轮询与布局切换。

从 main_window.ui_mixin 下沉（G13/G14/G16/G17）。所有函数只通过 host 的属性/
方法工作，不 import 主窗口；UIMixin 保留同名薄委托，外部回调与 monkeypatch 点
（_system_is_dark / _effective_is_dark / _parse_theme_color）不变。
"""

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import setTheme, setThemeColor, Theme, setFontFamilies

from core.design_tokens import pt_to_px


def apply_theme_color(host):
    """从 settings.json 加载强调色到内存（_apply_theme 时 setThemeColor）。"""
    host._theme_color = host._parse_theme_color(host._load_settings())


def apply_global_font(host):
    """统一应用全局字体（族 + 字号）到所有顶层窗口。"""
    settings = host._load_settings()
    family = settings.get("font_family", None)
    size = settings.get("font_size", None)
    if not family and not size:
        return
    if family:
        setFontFamilies([family], save=False)
    app_font = QFont()
    if family:
        app_font.setFamilies([family])
    else:
        app_font.setFamilies(QApplication.font().families())
    if size:
        pixel_size = pt_to_px(size)
    else:
        cur_px = QApplication.font().pixelSize()
        pixel_size = cur_px if cur_px > 0 else 14
    app_font.setPixelSize(pixel_size)
    QApplication.setFont(app_font)
    for window in QApplication.topLevelWidgets():
        if not isinstance(window, QWidget):
            continue
        set_widget_font_recursive(window, app_font)
        QWidget.update(window)


def set_widget_font_recursive(widget, app_font):
    """递归给顶层窗口及其全部子控件套字体并刷新。"""
    widget.setFont(app_font)
    for child in widget.findChildren(QWidget):
        child.setFont(app_font)
        # 用基类实现：qfw 的 MenuActionListWidget 等把 update 覆写成
        # update(self, index)，直接 child.update() 会 TypeError。
        QWidget.update(child)


def apply_theme(host):
    """按 settings.json 的 theme_mode 应用 Fluent 主题 + 业务 QSS。"""
    settings = host._load_settings()
    is_dark = host._effective_is_dark(settings)

    # 2026-09-07 教训留档：setTheme 勿传 lazy=True——qfw lazy 分支对
    # 不可见控件 register(reset=True) 重建合并树 + dirty-qss 延迟到
    # Paint 才重应用，与本项目 _reset_qss_compose_trees 交互异常（真机
    # 不可见页白底白字 10s+）。本轮「主题切换时长优化」（lazy/冻结重绘/
    # Mica 提前重组）经用户决定整体回退；方案与教训详见
    # .workbuddy/memory/2026-09-07.md，二期如需再做先读后再动
    setTheme(Theme.DARK if is_dark else Theme.LIGHT)
    setThemeColor(host._parse_theme_color(settings), lazy=True)
    QApplication.styleHints().setColorScheme(
        Qt.ColorScheme.Dark if is_dark else Qt.ColorScheme.Light)

    # 云母修复（2026-09-07）：业务 QSS 禁止挂在 FluentWindow 窗口自身。
    # 真机洋红壁纸对照实证：窗口级 setStyleSheet 会永久改变原生窗口
    # 表面的合成格式（不可逆——setStyleSheet("")、DWM setMicaEffect 重设、
    # hide+show、setWindowFlags 重建均无法恢复），云母从此被纯色表面盖死。
    # 全部业务页面都位于 stackedWidget 之下，故把 QSS 挂到 stackedWidget，
    # 子控件样式效果与原先完全一致。
    host._apply_business_qss()
    # 修复 qfw 合并源嵌套膨胀（2026-09-07）：setTheme 与 setThemeColor 各触发
    # 一次 updateStyleSheet 全量轮询，其 setStyleSheet(widget, file) 走
    # register(reset=True) 把旧合并树整体包进新树——每切一次主题每个注册
    # 控件 +1~2 层（stackedWidget 实测 17039→45846 字符、3→9 层）。
    # 切换完成后统一重置为干净树；singleShot(0) 兜底 lazy 轮询异步排队的情况。
    host._reset_qss_compose_trees()
    QTimer.singleShot(0, host._reset_qss_compose_trees)
    if not is_dark:
        host.style().unpolish(host)
        host.style().polish(host)
        for w in host.findChildren(QWidget):
            w.update()
    host._apply_global_font()
    # setTheme 会重置控件内部 QSS，重新强制工具栏单选按钮行高
    host._enforce_toolbar_radio_height()
    # qfluentwidgets 全局按钮 QSS 的 min-height 在 show polish 时才把 minimumHeight 覆盖回 35/45；
    # 构造时 _apply_theme 执行尚早，故用 singleShot(0) 排队到事件循环，在 show/主题重 polish 之后
    # 再强制按钮 32px，min=max=32、geometry 被 clamp 到 32，底部不再露 1-2px 背景带。
    QTimer.singleShot(0, host._enforce_toolbar_button_height)


def init_system_theme_monitor(host):
    """初始化系统主题变化轮询。"""
    host._last_applied_dark = None
    host._theme_poll_timer = QTimer(host)
    host._theme_poll_timer.setInterval(2000)
    host._theme_poll_timer.timeout.connect(host._poll_system_theme)
    host._sync_theme_polling()


def sync_theme_polling(host):
    """按主题模式启停轮询：auto 才轮询，手动模式停表。"""
    settings = host._load_settings()
    if host._get_theme_mode(settings) == "auto":
        host._last_applied_dark = host._effective_is_dark(settings)
        if not host._theme_poll_timer.isActive():
            host._theme_poll_timer.start()
    else:
        host._theme_poll_timer.stop()


def poll_system_theme(host):
    """轮询系统深浅色：变化时自动重应用主题并记日志。"""
    try:
        sys_dark = host._system_is_dark()
        if sys_dark != host._last_applied_dark:
            host._last_applied_dark = sys_dark
            host._apply_theme()
            actual = "深色" if sys_dark else "浅色"
            host._append_log(f"[主题] 检测到系统主题变化，已自动切换为{actual}")
    except KeyboardInterrupt:
        pass


def on_theme_selected(host, action):
    """主题子菜单互斥选择。"""
    if action == host._act_theme_dark:
        mode = "dark"
    elif action == host._act_theme_light:
        mode = "light"
    else:
        mode = "auto"
    host._save_settings({"theme_mode": mode})
    host._apply_theme()
    host._sync_theme_polling()
    if mode == "dark":
        host._append_log("[主题] 已切换为深色主题")
    elif mode == "light":
        host._append_log("[主题] 已切换为浅色主题")
    else:
        actual = "深色" if host._system_is_dark() else "浅色"
        host._append_log(f"[主题] 已切换为跟随系统（当前系统为{actual}）")


def on_layout_selected(host, action):
    """布局子菜单互斥选择。"""
    is_classic = (action == host._act_layout_classic)
    host._save_settings({"classic_layout": is_classic})
    host.ui.switch_layout(classic=is_classic)
    host._apply_theme()
    layout_name = "经典布局" if is_classic else "面板布局"
    host._append_log(f"[布局] 已切换为{layout_name}")


def apply_layout(host):
    """从 settings.json 加载并应用布局偏好。"""
    settings = host._load_settings()
    classic = settings.get("classic_layout", True)
    if classic:
        host.ui.switch_layout(classic=True)
