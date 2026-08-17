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

# 【关键】在 qfluentwidgets 导入前注入亚克力 PIL 补丁（打包环境无 numpy/scipy 时生效）
import core.acrylic_patch  # noqa: F401

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, qInstallMessageHandler
from PySide6.QtGui import QFont, QIcon
from qfluentwidgets import setTheme, setThemeColor, Theme, setFontFamilies

from core.app_paths import get_app_dir, get_resource_dir
from core.conn_logger import conn_logger, qt_message_handler
from main_window import MainWindow


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
    settings_path = os.path.join(get_app_dir(), "settings.json")

    # 启动时自动迁移：明文敏感字段（密码/token）一次性 DPAPI 加密回写，用户无感
    try:
        from core.secrets import migrate_settings_file
        migrate_settings_file(settings_path)
    except Exception:
        pass

    MainWindow.apply_dpi_scale(settings_path)

    app = QApplication(sys.argv)

    # 安装 Qt 消息处理器：qFatal/critical/warning 落盘
    qInstallMessageHandler(qt_message_handler)

    # 设置应用程序样式
    app.setStyle("Fusion")

    # 设置应用图标（窗口标题栏/任务栏，.ico 内含多尺寸）
    _icon_path = os.path.join(get_resource_dir(), "app_icon.ico")
    if os.path.isfile(_icon_path):
        app.setWindowIcon(QIcon(_icon_path))

    # 【关键】在创建任何 Fluent 控件之前设定主题
    try:
        with open(settings_path, 'r', encoding='utf-8') as f:
            _settings = json.load(f)
    except Exception:
        _settings = {}
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
                _px = max(12, int(int(_sz) * 4 / 3))
            else:
                _cur_px = app.font().pixelSize()
                _px = _cur_px if _cur_px > 0 else 14
            _app_font.setPixelSize(_px)
            app.setFont(_app_font)
    except Exception:
        pass

    # 创建并显示主窗口
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
