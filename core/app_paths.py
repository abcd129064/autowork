# -*- coding: utf-8 -*-
"""应用程序路径工具（兼容 PyInstaller 打包）"""

import sys
import os


def get_app_dir():
    """获取应用程序所在目录（兼容 PyInstaller 打包）。

    - 开发环境：main.py 所在目录
    - 打包环境（onedir）：sys.executable（.exe）所在目录，而非 __file__
      指向的 _internal/ 解压目录。确保 settings.json、logs/ 等资源
      统一落在 .exe 旁边，与用户预期一致。
    供模块级对象（如 ConnLogger）与 MainWindow 共用，避免路径解析分叉。"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    # 开发环境：本文件位于 core/ 子目录，向上一级即项目根目录
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
