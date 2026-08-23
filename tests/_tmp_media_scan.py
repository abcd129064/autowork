# -*- coding: utf-8 -*-
"""扫描项目媒体资源 + numpy 可用性"""
import os
import glob
import numpy

print("numpy:", numpy.__version__)

root = r"C:\Users\shen_zhe\Desktop\autowork"
video = ("*.mp4", "*.mkv", "*.avi", "*.mov", "*.wmv", "*.flv", "*.webm", "*.ts")
img = ("*.jpg", "*.jpeg", "*.png", "*.bmp")

print("\n=== 视频文件 ===")
found = False
for fmt in video:
    for p in glob.glob(os.path.join(root, "**", fmt), recursive=True):
        print("  ", p, os.path.getsize(p) // 1024, "KB")
        found = True
if not found:
    print("  (无视频文件)")

print("\n=== 图片文件（resource 目录）===")
rp = os.path.join(root, "resource")
for f in sorted(os.listdir(rp)):
    if f.lower().endswith(img):
        print("  ", os.path.join(rp, f), os.path.getsize(os.path.join(rp, f)) // 1024, "KB")
