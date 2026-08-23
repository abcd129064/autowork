# -*- coding: utf-8 -*-
"""探测 QtMultimedia 可用性 + 视频文件是否存在 + 媒体播放组件构造"""
import importlib
import os
import glob

print("=== QtMultimedia 模块探测 ===")
for modname in ("PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets"):
    try:
        m = importlib.import_module(modname)
        print(f"  import {modname} OK")
    except Exception as e:
        print(f"  import {modname} FAIL: {type(e).__name__}: {e}")

print("\n=== 关键类探测 ===")
from PySide6 import QtMultimedia, QtMultimediaWidgets
print("  QMediaPlayer:", hasattr(QtMultimedia, "QMediaPlayer"))
print("  QMediaContent:", hasattr(QtMultimedia, "QMediaContent"))
print("  QMediaDevices:", hasattr(QtMultimedia, "QMediaDevices"))
print("  QAudioOutput:", hasattr(QtMultimedia, "QAudioOutput"))
print("  QVideoWidget :", hasattr(QtMultimediaWidgets, "QVideoWidget"))
print("  QVideoSink   :", hasattr(QtMultimedia, "QVideoSink"))

# 可用后端
try:
    print("  mime types:", QtMultimedia.QMediaPlayer().supportedAudioCodecs() and "codecs found" or "no codecs")
except Exception as e:
    print("  QMediaPlayer construct:", e)

print("\n=== 项目内视频文件 ===")
roots = [r"C:\Users\shen_zhe\Desktop\autowork"]
pat = ("*.mp4", "*.mkv", "*.avi", "*.mov", "*.wmv", "*.flv", "*.webm", "*.ts", "*.mp3", "*.wav", "*.flac", "*.m4a")
for root in roots:
    for fmt in pat:
        for p in glob.glob(os.path.join(root, "**", fmt), recursive=True):
            print("  ", p)

print("\n注意：QMediaPlayer 需要 QtMultimedia 后端（如 Windows 的 WMF）。")
