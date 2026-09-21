# -*- coding: utf-8 -*-
"""只读盘点：列出指定目录下的条目（类型/大小/修改日期），不修改任何文件。

用法: python tools/inventory.py [目录...] [--deep]
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):   # Windows 控制台默认 GBK，先切 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def dir_size(p):
    total = 0
    for dp, _dn, fn in os.walk(p):
        for f in fn:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except Exception:
                pass
    return total


def list_dir(d):
    rows = []
    try:
        names = sorted(os.listdir(d))
    except Exception as e:
        print(f"  [无法读取] {d}: {e}")
        return
    for name in names:
        p = os.path.join(d, name)
        try:
            isdir = os.path.isdir(p)
            size = dir_size(p) if isdir else os.path.getsize(p)
            mtime = os.path.getmtime(p)
        except Exception:
            continue
        rows.append((isdir, size, mtime, name))
    for isdir, size, mtime, name in sorted(rows, key=lambda r: (not r[0], r[3])):
        kind = "DIR " if isdir else "FILE"
        print(f"  {kind} {human(size):>8}  "
              f"{time.strftime('%Y-%m-%d', time.localtime(mtime))}  {name}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        args = ["."]
    for a in args:
        d = a if os.path.isabs(a) else os.path.join(ROOT, a)
        print(f"\n===== {d} =====")
        list_dir(d)


if __name__ == "__main__":
    main()
