# -*- coding: utf-8 -*-
"""清理 tests 目录下所有 _tmp_ 临时探针/截图文件"""
import os, glob

tests = r"C:\Users\shen_zhe\Desktop\autowork\tests"
removed = []
for pat in ("_tmp_*.py", "_tmp_*.png"):
    for p in glob.glob(os.path.join(tests, pat)):
        try:
            os.remove(p)
            removed.append(os.path.basename(p))
        except OSError as e:
            print("FAIL:", os.path.basename(p), e)
print("removed", len(removed))
for n in sorted(removed):
    print(" -", n)
