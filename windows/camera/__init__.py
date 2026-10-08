# -*- coding: utf-8 -*-
"""相机面板（面板级导航，2026-10-07 从工具页签升级）

结构：CameraHub(PivotPage) → 相机工具（DahuaCameraWork）。
实现细节在 camera_page.py；本包对外只暴露 CameraHub / DahuaCameraWork。
"""
from windows.camera.camera_hub import CameraHub
from windows.camera.camera_page import DahuaCameraWork
from windows.camera.encode_page import EncodePage

__all__ = ["CameraHub", "DahuaCameraWork", "EncodePage"]
