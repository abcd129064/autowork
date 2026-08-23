# -*- coding: utf-8 -*-
"""检查 PySide6 安装的组件（QtMultimedia 是否属于已装 dist）"""
import os
import glob

import PySide6

print("PySide6 version:", PySide6.__version__)

# site-packages 下所有 PySide6 相关 dist
site = os.path.dirname(os.path.dirname(os.path.abspath(PySide6.__file__)))
print("\n[site-packages 中 PySide6 相关 dist]")
for d in sorted(glob.glob(os.path.join(site, "PySide6*"))):
    print("  ", os.path.basename(d))

# PySide6 包内是否有 QtMultimedia 目录/模块
pkg = os.path.dirname(os.path.abspath(PySide6.__file__))
print("\n[PySide6 包内多媒体模块存在性]")
for sub in ("QtMultimedia", "QtMultimediaWidgets"):
    print(f"  {sub} dir  :", os.path.isdir(os.path.join(pkg, sub)))
    print(f"  {sub} .pyd :", os.path.exists(os.path.join(pkg, sub + ".pyd")))

# 列出 PySide6 包顶层 .pyd（可用的已编译模块）
print("\n[PySide6 顶层已编译模块 *.pyd]")
pyds = [os.path.splitext(os.path.basename(x))[0] for x in glob.glob(os.path.join(pkg, "*.pyd"))]
print("  ", ", ".join(sorted(pyds)))

# cv2 是否可用（备用播放方案）
try:
    import cv2 as _cv
    print("\ncv2 version:", _cv.__version__)
    print("cv2.VideoCapture available:", hasattr(_cv, "VideoCapture"))
except Exception as e:
    print("\ncv2 import FAIL:", e)
