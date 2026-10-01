# -*- coding: utf-8 -*-
"""Mica 云母环境兜底回归（2026-09-24）

覆盖 core/perf.py 新增能力：
- mica_block_reason：非 Win32 / Win10 / RDP 会话 / 透明效果关闭 的四类判定
- patch_mica_policy：命中时双层短路（FluentWidget.setMicaEffectEnabled 开关
  路径 + FramelessWindow 基类直调的 WindowsWindowEffect.setMicaEffect 路径）；
  环境支持时保持库原行为；幂等（重复调用不重复 patch）

Windows getwindowsversion 依赖 sys 状态 → 测试统一 monkeypatch 注入假版本；
ctypes/winreg 探测走 perf 模块级小函数（_is_remote_session /
_is_transparency_disabled）注入，不碰真实系统状态。
"""
import sys
import types

import pytest

import core.perf as perf


class _FakeWinVer(types.SimpleNamespace):
    """仿 sys.getwindowsversion() 返回对象（仅需 .build）"""

    def __init__(self, build):
        super().__init__(build=build)


# ==================== mica_block_reason 判定矩阵 ====================

def test_block_reason_non_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert perf.mica_block_reason() == ""          # 非 Windows：不判定为禁用


def test_block_reason_win10(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "getwindowsversion", lambda: _FakeWinVer(19045))
    assert perf.mica_block_reason() == "below-win11"


def test_block_reason_win11_clean(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "getwindowsversion", lambda: _FakeWinVer(22631))
    monkeypatch.setattr(perf, "_is_remote_session", lambda: False)
    monkeypatch.setattr(perf, "_is_transparency_disabled", lambda: False)
    assert perf.mica_block_reason() == ""


def test_block_reason_rdp_session(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "getwindowsversion", lambda: _FakeWinVer(22631))
    monkeypatch.setattr(perf, "_is_remote_session", lambda: True)
    assert perf.mica_block_reason() == "rdp-session"


def test_block_reason_transparency_off(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "getwindowsversion", lambda: _FakeWinVer(22000))
    monkeypatch.setattr(perf, "_is_remote_session", lambda: False)
    monkeypatch.setattr(perf, "_is_transparency_disabled", lambda: True)
    assert perf.mica_block_reason() == "transparency-off"


def test_block_reason_version_probe_fails(monkeypatch):
    def boom():
        raise OSError("simulate failure")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "getwindowsversion", boom)
    assert perf.mica_block_reason() == "unknown-version"


# ==================== patch_mica_policy 双层短路 ====================

@pytest.fixture
def fluent_classes():
    """导入 qfw 双层类，测试后还原被 patch 的方法"""
    from qfluentwidgets.window.fluent_window import FluentWidget
    from qframelesswindow.windows.window_effect import WindowsWindowEffect
    orig_enabled = FluentWidget.__dict__.get("setMicaEffectEnabled")
    orig_effect = WindowsWindowEffect.__dict__.get("setMicaEffect")
    orig_enabled_ref[0] = orig_enabled
    orig_effect_ref[0] = orig_effect
    patched_marker = FluentWidget.__dict__.get("_perf_mica_patched")
    yield FluentWidget, WindowsWindowEffect
    # 还原（patch 挂在实例字典；还原即重赋原函数 / 删除该属性）
    if orig_enabled is not None:
        FluentWidget.setMicaEffectEnabled = orig_enabled
    else:
        delattr(FluentWidget, "setMicaEffectEnabled")
    if orig_effect is not None:
        WindowsWindowEffect.setMicaEffect = orig_effect
    else:
        delattr(WindowsWindowEffect, "setMicaEffect")
    if patched_marker is not None:
        FluentWidget._perf_mica_patched = patched_marker
    else:
        delattr(FluentWidget, "_perf_mica_patched")


orig_enabled_ref = [None]
orig_effect_ref = [None]


def test_patch_blocks_when_env_unsupported(fluent_classes, monkeypatch):
    FluentWidget, WindowsWindowEffect = fluent_classes
    monkeypatch.setattr(perf, "mica_block_reason", lambda: "rdp-session")
    perf.patch_mica_policy()

    assert FluentWidget._perf_mica_patched is True
    # 开关路径被短路：开启请求被静默拒绝（不抛错、不改内部状态）
    class _Win:
        def removeBackgroundEffect(self, hwnd):
            raise AssertionError("拒绝启用时不应触碰 windowEffect")
    w = FluentWidget.__new__(FluentWidget)   # 绕过构造（Qt 对象无法裸建）
    w._isMicaEnabled = False
    w.windowEffect = _Win()
    FluentWidget.setMicaEffectEnabled(w, True)
    assert w._isMicaEnabled is False
    # backdrop 路径被 no-op：调用不产生任何效果也不抛错
    assert WindowsWindowEffect.setMicaEffect(_Win(), 12345, True) is None


def test_patch_noop_when_env_supported(fluent_classes, monkeypatch):
    FluentWidget, WindowsWindowEffect = fluent_classes
    monkeypatch.setattr(perf, "mica_block_reason", lambda: "")
    perf.patch_mica_policy()
    # 环境支持：只挂幂等标记，方法保持库原实现（身份不变）
    assert FluentWidget._perf_mica_patched is True
    assert FluentWidget.setMicaEffectEnabled is orig_enabled_ref[0]
    assert WindowsWindowEffect.setMicaEffect is orig_effect_ref[0]


def test_patch_idempotent(fluent_classes, monkeypatch):
    FluentWidget, WindowsWindowEffect = fluent_classes
    monkeypatch.setattr(perf, "mica_block_reason", lambda: "transparency-off")
    perf.patch_mica_policy()
    patched_enabled = FluentWidget.setMicaEffectEnabled
    perf.patch_mica_policy()                 # 第二次调用：不重复 patch
    assert FluentWidget.setMicaEffectEnabled is patched_enabled
