# -*- coding: utf-8 -*-
"""AutoWork 入口文件 - 所有业务逻辑已拆分到模块化包中：
    core/         - 路径、日志、工具函数
    win_api/      - Windows ctypes 声明
    workers/      - QThread Worker 类
    windows/      - SFTP/SSH/RDP 独立窗口
    main_window/  - MainWindow 主窗口（Mixin 拆分）
    styles/       - QSS 主题样式文件
"""

import sys
import os
import json
import threading
import traceback

# ---- Qt 运行时引导 ----
# 场景：conda base 激活后，PATH 会注入 conda 自带的 Qt DLL（qtbase/qtwebengine 等，位于
# <conda>\Library\bin），它们与 PySide6 自带的 Qt 二进制冲突，导致 Qt 平台插件加载失败
# （qt.qpa.plugin / DLL load failed）。必须在 import PySide6 之前用 os.add_dll_directory
# 固定 PySide6 自身的 Qt 搜索路径，并让其使用自带的插件目录。
import os as _os
import importlib.util as _qt_iu
_qt_handles = []
try:
    _qt_spec = _qt_iu.find_spec('PySide6')
    if _qt_spec is not None:
        _qt_locs = list(getattr(_qt_spec, 'submodule_search_locations', None) or [])
        if _qt_locs:
            _qt_pkg = _qt_locs[0]
            for _d in (_qt_pkg, _os.path.dirname(_qt_pkg),
                       _os.path.join(_os.environ.get('SystemRoot', r'C:\Windows'), 'System32')):
                if _os.path.isdir(_d):
                    try:
                        _qt_handles.append(_os.add_dll_directory(_d))
                    except OSError:
                        pass
            _os.environ['QT_PLUGIN_PATH'] = _os.path.join(_qt_pkg, 'plugins')
            _os.environ.setdefault('QT_QPA_PLATFORM_PLUGIN_PATH',
                                   _os.path.join(_qt_pkg, 'plugins', 'platforms'))
except Exception:
    pass

# 【关键】在 qfluentwidgets 导入前注入亚克力 PIL 补丁（打包环境无 numpy/scipy 时生效）
import core.acrylic_patch  # noqa: F401

from PySide6.QtWidgets import QApplication, QSplashScreen
from PySide6.QtCore import Qt, QRect, qInstallMessageHandler
from PySide6.QtGui import QFont, QIcon, QColor, QPainter, QPixmap
from qfluentwidgets import setTheme, setThemeColor, Theme, setFontFamilies

# 中央拦截菜单弹出动画：按「面板覆盖→全局」生效值降级（含库内硬编码的
# ComboBox 下拉），开关切换后下一次弹出即生效（幂等，重复调用无害）
from core.perf import (patch_menu_animation, patch_dialog_animation,
                       patch_table_hover_repaint,
                       patch_lean_table_delegate)
patch_menu_animation()
# 中央拦截 MessageBoxBase 弹窗淡入/淡出（QGraphicsOpacityEffect 整窗离屏
# 渲染是「双击打开面板」低帧/卡顿主因）：动画关闭时直接显示，秒开无渐变
patch_dialog_animation()
# 中央拦截 TableWidget hover 重绘：鼠标扫过行只重绘新旧两行条带（替代
# 库默认整视口重绘），滚轮滚动 + 鼠标移动叠加场景掉帧显著减少（幂等）
patch_table_hover_repaint()
# 表格委托换轻量实现（P0-1）：保留库的 hover/圆角/自绘勾选框等全部视觉，
# 只把每格文本绘制从 QTextLayout 排版换成 drawText + 省略号缓存。实测滚动
# 耗时 -49.4%（售后 60 行 × 13 列：11.9 → 6.0 ms/帧），全局表格自动生效（幂等）
patch_lean_table_delegate()
# SwitchButton 状态文本统一中文「开/关」（库默认 "On"/"Off"，patch
# __init__ 对全项目所有直接实例化处一次生效，幂等）
from core.switch_cn_patch import patch_switch_cn_text
patch_switch_cn_text()

from core.app_paths import get_resource_dir
from core.conn_logger import conn_logger, qt_message_handler
from core.design_tokens import pt_to_px
from main_window import MainWindow


def _make_splash(app, is_dark):
    """启动闪屏：遮住主窗口首帧布局/样式预热过程，避免用户看到半成品界面

    主窗口 show 后第一帧 FlowLayout 工具栏尚未完成布局、qfw polish 与
    按钮高度强制（singleShot(0)）也未执行，直接暴露会出现控件错位/半样式
    的闪乱帧。闪屏在预热期间常驻前台，预热完成后随主窗口首帧一起撤掉。
    """
    try:
        pm = QPixmap(420, 150)
        pm.fill(QColor("#202124") if is_dark else QColor("#ffffff"))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        icon = app.windowIcon()
        if not icon.isNull():
            icon.paint(p, QRect(24, 24, 44, 44))
        p.setPen(QColor("#e8ebef") if is_dark else QColor("#202124"))
        f = QFont(app.font())
        f.setPixelSize(20)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRect(84, 26, 320, 30), Qt.AlignmentFlag.AlignLeft
                   | Qt.AlignmentFlag.AlignVCenter, "AutoWork")
        f.setPixelSize(12)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor("#9aa0a6"))
        p.drawText(QRect(84, 62, 320, 22), Qt.AlignmentFlag.AlignLeft
                   | Qt.AlignmentFlag.AlignVCenter, "正在启动…")
        p.end()
        sp = QSplashScreen(pm)
        sp.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        return sp
    except Exception:
        return None


def main():
    """启动入口：装异常钩子/敏感配置迁移/主题字体，再创建主窗口进事件循环"""
    # 全局异常钩子：主线程/后台线程未捕获异常先落盘日志，确保崩溃可追踪
    def _global_exception_hook(exc_type, exc_value, exc_tb):
        try:
            conn_logger._write('FATAL', 'MAIN', '未捕获异常（主线程）',
                               error_type=exc_type.__name__,
                               detail=''.join(traceback.format_exception(exc_type, exc_value, exc_tb)))
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    def _thread_exception_hook(args):
        try:
            conn_logger._write('FATAL', 'THREAD',
                               f'未捕获异常（线程 {args.thread.name if args.thread else "?"}）',
                               error_type=args.exc_type.__name__,
                               detail=''.join(traceback.format_exception(
                                   args.exc_type, args.exc_value, args.exc_traceback)))
        except Exception:
            pass

    sys.excepthook = _global_exception_hook
    threading.excepthook = _thread_exception_hook

    # 应用 DPI 缩放（必须在 QApplication 创建前设置环境变量）
    # 启动时自动迁移：旧整文件 settings.json 按域拆分到 config/ + 明文敏感字段 DPAPI 加密，用户无感
    try:
        from core import app_settings
        app_settings.migrate_legacy()
        _settings = app_settings.get_merged()
    except Exception:
        _settings = {}
    MainWindow.apply_dpi_scale(_settings)

    app = QApplication(sys.argv)

    # 安装 Qt 消息处理器：qFatal/critical/warning 落盘
    qInstallMessageHandler(qt_message_handler)

    # 设置应用程序样式
    app.setStyle("Fusion")

    # 设置应用图标（窗口标题栏/任务栏，.ico 内含多尺寸）
    _icon_path = os.path.join(get_resource_dir(), "app_icon.ico")
    if os.path.isfile(_icon_path):
        app.setWindowIcon(QIcon(_icon_path))

    # 【关键】在创建任何 Fluent 控件之前设定主题（_settings 已由配置门面在启动迁移时读取）
    _is_dark = MainWindow._effective_is_dark(_settings)
    setTheme(Theme.DARK if _is_dark else Theme.LIGHT)
    setThemeColor(MainWindow._parse_theme_color(_settings), lazy=True)
    # 锁定 Qt 调色板，禁止 Windows 深色模式向应用注入深色调色板
    QApplication.styleHints().setColorScheme(
        Qt.ColorScheme.Dark if _is_dark else Qt.ColorScheme.Light)

    # 【关键】在创建窗口/控件之前应用用户自定义字体
    try:
        _fam = _settings.get("font_family")
        _sz = _settings.get("font_size")
        if _fam or _sz:
            if _fam:
                setFontFamilies([_fam], save=False)
            _app_font = QFont()
            if _fam:
                _app_font.setFamilies([_fam])
            else:
                _app_font.setFamilies(app.font().families())
            if _sz:
                _px = pt_to_px(_sz)
            else:
                _cur_px = app.font().pixelSize()
                _px = _cur_px if _cur_px > 0 else 14
            _app_font.setPixelSize(_px)
            app.setFont(_app_font)
    except Exception:
        pass

    # 本地售后面板 Web 服务：daemon 线程托管前端静态页 + 反代云端 API，
    # 浏览器访问 http://localhost:8787（settings.json local_web 节点可配置/关闭）
    try:
        from core.local_web_server import start_local_web_server
        _lw = start_local_web_server(_settings)
        if _lw.get("started"):
            print(f"[AutoWork] 本地售后面板: {_lw['url']}")
    except Exception:
        pass

    # 创建并显示主窗口（闪屏遮首帧：offscreen 预热布局/样式后再 reveal）
    splash = _make_splash(app, _is_dark)
    if splash is not None:
        splash.show()
        app.processEvents()
    window = MainWindow()
    # offscreen 预热：强制完成首帧布局（FlowLayout 工具栏/按钮高度强制/
    # qfw polish 的 singleShot(0) 任务），用户不会看到半成品第一帧。
    # ⚠️ WA_DontShowOnScreen 下 show() 已把 isVisible 置 True，撤属性后
    # 再 show() 是空操作（原生窗口不会创建）——必须先 hide() 复位可见态；
    # try/finally 保证预热异常也一定走到真 show，闪屏不会把程序"带走"。
    try:
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        window.show()
        for _ in range(10):
            app.processEvents()
    finally:
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
        window.hide()  # 复位可见态，确保下面的 show() 真正创建原生窗口
    if splash is not None:
        splash.showMessage("正在准备工作台…", Qt.AlignmentFlag.AlignLeft
                           | Qt.AlignmentFlag.AlignBottom,
                           QColor("#9aa0a6"))
        app.processEvents()
    window.show()
    window.raise_()
    window.activateWindow()
    if splash is not None:
        splash.finish(window)

    # 自动更新（S3）：先消费上次安装的回执（成功/失败都给用户交代），
    # 再按配置静默自检新版本。两者都延后到事件循环起来之后，不抢启动资源。
    try:
        from PySide6.QtCore import QTimer as _QTimer
        _QTimer.singleShot(0, window.consume_update_receipt_on_startup)
        _QTimer.singleShot(0, window.auto_check_update_on_startup)
    except Exception:
        pass

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
