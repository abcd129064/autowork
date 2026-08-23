# -*- coding: utf-8 -*-
"""清理 tests 下 toolbar 相关临时探针与截图"""
import os, glob

tests = r"C:\Users\shen_zhe\Desktop\autowork\tests"
removed = []
for pat in ("_tmp_toolbar_*.py", "_tmp_toolbar_*.png"):
    for p in glob.glob(os.path.join(tests, pat)):
        try:
            os.remove(p)
            removed.append(os.path.basename(p))
        except OSError as e:
            print("FAIL:", os.path.basename(p), e)
print("removed", len(removed))
for n in sorted(removed):
    print(" -", n)
