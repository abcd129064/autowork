# -*- coding: utf-8 -*-
"""读 color_dialog.py 主 ColorDialog 类 __init__"""
import io
import glob

p = glob.glob(r"C:\Users\shen_zhe\miniconda3\Lib\site-packages\qfluentwidgets\components\dialog_box\color_dialog.py")[0]
s = io.open(p, encoding="utf-8").read().splitlines()
start = None
for i, l in enumerate(s):
    if l.strip().startswith("class ColorDialog"):
        start = i
        break
print("class ColorDialog at line", start + 1)
for k in range(start, min(start + 70, len(s))):
    print(f"{k+1:3}: {s[k]}")
