# -*- coding: utf-8 -*-
import os, glob
base = r"C:\Users\shen_zhe\Desktop\autowork\tests"
removed = 0
for pat in ("_ft_probe.py", "_ft_zoom.py", "_ft_settings.py", "_ft_full.png", "_ft_zoom.png"):
    p = os.path.join(base, pat)
    if os.path.exists(p):
        os.remove(p); removed += 1
print("removed", removed)
