# -*- coding: utf-8 -*-
"""读 color_dialog.py 源码，定位 NoneType.width 来源"""
import io
import glob

p = glob.glob(r"C:\Users\shen_zhe\miniconda3\Lib\site-packages\qfluentwidgets\components\dialog_box\color_dialog.py")[0]
s = io.open(p, encoding="utf-8").read().splitlines()
for i, l in enumerate(s[:100], 1):
    if any(k in l for k in ("def __init__", "width", "height", "self.", "qconfig", "QColor", "QScreen")):
        print(f"{i:3}: {l}")
