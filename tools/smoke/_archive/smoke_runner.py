# -*- coding: utf-8 -*-
"""冒烟 runner：子进程跑 smoke，stdout/stderr 落 UTF-8 文件"""
import os
import subprocess
import sys

env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
           PYTHONIOENCODING="utf-8")
targets = sys.argv[1:] or ["tools/smoke_todesk_concurrency.py",
                           "tools/smoke_sync_xqzg_live.py"]
if os.path.exists("tools/_smoke_result.txt"):
    os.remove("tools/_smoke_result.txt")
for t in targets:
    p = subprocess.run([sys.executable, t], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       env=env, cwd=".", timeout=300)
    with open("tools/_smoke_result.txt", "a", encoding="utf-8") as f:
        f.write("===== %s rc=%d =====\n" % (t, p.returncode))
        f.write(p.stdout or "")
        f.write("\n--- stderr ---\n" + (p.stderr or "") + "\n\n")
