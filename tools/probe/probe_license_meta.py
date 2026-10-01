"""探针：从已安装分发的 METADATA 读出依赖的真实许可证标识与版本（只读）。

背景：LICENSE/NOTICE 与 licenses/INDEX.md 里的许可证名称必须来自上游 METADATA，不能凭记忆写。
用法：C:\\Users\\shen_zhe\\miniconda3\\python.exe tools/probe/probe_license_meta.py
产物：stdout（UTF-8），不写文件
"""
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from importlib import metadata

WANTED = [
    "PySide6", "PySide6-Fluent-Widgets", "paramiko", "bcrypt", "requests", "urllib3",
    "openai", "trafilatura", "openpyxl", "opencv-python-headless", "opencv-python",
    "numpy", "pandas", "pygwalker", "Pillow", "pymysql", "fastapi", "uvicorn",
    "lxml", "darkdetect", "PySide6-Essentials", "shiboken6",
]

for name in WANTED:
    try:
        d = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        print(f"{name}\tNOT INSTALLED")
        continue
    fields = []
    for key in ("License", "License-Expression", "Classifier"):
        try:
            values = d.metadata.get_all(key) or []
        except Exception:
            values = []
        for v in values:
            if key == "Classifier" and "License" not in v:
                continue
            fields.append(v.strip().splitlines()[0])
    seen = []
    for v in fields:
        if v and v not in seen:
            seen.append(v)
    print(f"{name}\t{d.version}\t{' | '.join(seen) or '(no license field)'}")
