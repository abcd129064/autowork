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
    """从 settings.json 加载强调色到内存（_apply_theme 时 setThemeColor）。

    强调色只在这里读一次缓存，真正生效是 apply_theme 里统一 setThemeColor。
    """
    host._theme_color = host._parse_theme_color(host._load_settings())


def apply_global_font(host):
    """统一应用全局字体（族 + 字号）到所有顶层窗口。

    两项都没配就保持 qfw 按 DPI 自算的默认字体，不去覆盖。
    """
    settings = host._load_settings()
    family = settings.get("font_family", None)
    size = settings.get("font_size", None)
    # 两项都没配就整段跳过：用空配置覆盖 qfw 按 DPI 算好的默认字体只会更糟。
    if not family and not size:
        return
    if family:
        # save=False：字体真源只认 settings.json，不让 qfw 顺手再写一份自己的配置。
        setFontFamilies([family], save=False)
    app_font = QFont()
    if family:
        app_font.setFamilies([family])
    else:
        app_font.setFamilies(QApplication.font().families())
    if size:
        # 令牌里存的是 pt，Qt 的 setPixelSize 要 px，必须过一次 pt_to_px。
        pixel_size = pt_to_px(size)
    else:
        # pixelSize 为 -1 表示该字体是按 pt 设置的，此时退回 14 兜底。
        cur_px = QApplication.font().pixelSize()
        pixel_size = cur_px if cur_px > 0 else 14
    app_font.setPixelSize(pixel_size)
    # setFont 只对之后新建的控件生效，已存在的窗口靠下面的递归补。
    QApplication.setFont(app_font)
    # QApplication.topLevelWidgets 里混着非 QWidget 对象，先过滤再套字体。
    for window in QApplication.topLevelWidgets():
        if not isinstance(window, QWidget):
            continue
        set_widget_font_recursive(window, app_font)
        # setFont 不会自己触发重排，qfw 控件缓存了旧度量，必须显式刷一次。
        QWidget.update(window)


def set_widget_font_recursive(widget, app_font):
    """递归给顶层窗口及其全部子控件套字体并刷新。"""
    widget.setFont(app_font)
    # findChildren 已递归到孙子层，不必逐层下钻。
    for child in widget.findChildren(QWidget):
        child.setFont(app_font)
        # 用基类实现：qfw 的 MenuActionListWidget 等把 update 覆写成
        # update(self, index)，直接 child.update() 会 TypeError。
        QWidget.update(child)


def apply_theme(host):
    """按 settings.json 的 theme_mode 应用 Fluent 主题 + 业务 QSS。

    顺序不能乱：主题 → 业务 QSS → 合并树归零 → 字号 → 按钮高度。
    """
    settings = host._load_settings()
    # 用 effective 而不是 system：手动模式下要按用户选择，不能跟着系统跑。
    is_dark = host._effective_is_dark(settings)

    # setTheme 禁传 lazy=True：qfw 的 lazy 分支把不可见控件的 QSS 重建推迟到 Paint，
    # 与 _reset_qss_compose_trees 冲突（真机不可见页白底白字 10s+），该时长优化已整体回退。
    setTheme(Theme.DARK if is_dark else Theme.LIGHT)
    setThemeColor(host._parse_theme_color(settings), lazy=True)
    # setColorScheme 只管原生标题栏、滚动条这些非自绘区域，qfw 的 QSS 覆盖不到它们。
    QApplication.styleHints().setColorScheme(
        Qt.ColorScheme.Dark if is_dark else Qt.ColorScheme.Light)

    # 业务 QSS 挂 stackedWidget 而不挂窗口自身：窗口级 setStyleSheet 会永久改写原生表面的
    # 合成格式，云母被纯色盖死且不可逆，setStyleSheet("") 也救不回来。
    host._apply_business_qss()
    # setTheme/setThemeColor 各触发一次全量 updateStyleSheet，其 register(reset=True) 把旧
    # 合并树整棵包进新树——每切一次主题控件 +1~2 层（实测 stackedWidget 17039→45846 字符、3→9 层）。
    host._reset_qss_compose_trees()
    # lazy 轮询的 reset 是排队执行的，会晚于上一行，故再用 0ms 兜一次。
    QTimer.singleShot(0, host._reset_qss_compose_trees)
    # 切回浅色时 QSS 没变但调色板变了，要 unpolish/polish 才会重新取色。
    if not is_dark:
        host.style().unpolish(host)
        host.style().polish(host)
        for w in host.findChildren(QWidget):
            w.update()
    # 字体必须放在主题之后：主题切换会重置控件的字体相关 QSS。
    host._apply_global_font()
    # setTheme 会重置控件内部 QSS，重新强制工具栏单选按钮行高
    host._enforce_toolbar_radio_height()
    # 构造期 _apply_theme 早于 show，qfw 在 show polish 时才把按钮 min-height 覆盖回 35/45，
    # 所以排队到事件循环末尾再压回 32px：min=max，geometry 被 clamp，底部不再露背景带。
    QTimer.singleShot(0, host._enforce_toolbar_button_height)


def init_system_theme_monitor(host):
    """初始化系统主题变化轮询。"""
    # 先立哨兵：首轮轮询必然与之不等，从而立刻对齐真实系统态。
    host._last_applied_dark = None
    host._theme_poll_timer = QTimer(host)
    # 2s 是折中：再快就是空转耗电，再慢用户能看出主题变化有延迟。
    host._theme_poll_timer.setInterval(2000)
    host._theme_poll_timer.timeout.connect(host._poll_system_theme)
    host._sync_theme_polling()


def sync_theme_polling(host):
    """按主题模式启停轮询：auto 才轮询，手动模式停表。"""
    settings = host._load_settings()
    if host._get_theme_mode(settings) == "auto":
        # 进 auto 先把基准对齐系统态，否则启动后第一个 tick 会误判成「刚变化」再刷一次。
        host._last_applied_dark = host._effective_is_dark(settings)
        if not host._theme_poll_timer.isActive():
            host._theme_poll_timer.start()
    else:
        # 手动模式下停表：每 2s 查一次系统毫无意义。
        host._theme_poll_timer.stop()


def poll_system_theme(host):
    """轮询系统深浅色：变化时自动重应用主题并记日志。

    只在 auto 模式下由定时器调到，手动模式已经停表。
    """
    try:
        sys_dark = host._system_is_dark()
        # 值真变了才重应用：_apply_theme 要重建整棵 QSS 合并树，代价不低。
        if sys_dark != host._last_applied_dark:
            host._last_applied_dark = sys_dark
            host._apply_theme()
            actual = "深色" if sys_dark else "浅色"
            host._append_log(f"[主题] 检测到系统主题变化，已自动切换为{actual}")
    except KeyboardInterrupt:
        # 轮询跑在 GUI 线程，Ctrl-C 会从这里冒出来；吞掉它，别让它打断整个事件循环。
        pass


def on_theme_selected(host, action):
    """主题子菜单互斥选择。"""
    if action == host._act_theme_dark:
        mode = "dark"
    elif action == host._act_theme_light:
        mode = "light"
    else:
        # 既不是深色也不是浅色就是「跟随系统」，菜单里只有这三个互斥项。
        mode = "auto"
    # 写盘后立刻生效；轮询启停也要跟着新模式走，否则手动模式下仍在空转。
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
    # switch_layout 重建页面后 QSS 合并树全部失效，必须重跑 _apply_theme。
    host.ui.switch_layout(classic=is_classic)
    host._apply_theme()
    layout_name = "经典布局" if is_classic else "面板布局"
    host._append_log(f"[布局] 已切换为{layout_name}")


def apply_layout(host):
    """从 settings.json 加载并应用布局偏好。

    只在启动时应用一次；之后用户切换走 on_layout_selected。
    """
    settings = host._load_settings()
    # 老配置里没有这个键，缺省按经典布局，和升级前的观感一致。
    classic = settings.get("classic_layout", True)
    if classic:
        host.ui.switch_layout(classic=True)
