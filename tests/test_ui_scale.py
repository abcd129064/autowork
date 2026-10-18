# -*- coding: utf-8 -*-
"""core.ui_scale.apply_dpi_scale 的纯逻辑单测（无 Qt、无真实注册表）。

覆盖：100% 不设环境变量；非 100% 写 QT_SCALE_FACTOR；系统缩放 ≥150%
触发叠加告警；探测失败不阻断、仍写环境变量；settings 异常静默。
"""

import core.perf as perf
import core.ui_scale as ui_scale


def _clean_env(monkeypatch):
    monkeypatch.delenv("QT_SCALE_FACTOR", raising=False)


def test_scale_100_leaves_env_unset(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setattr(perf, "detect_system_scale_percent", lambda: 150)
    calls = []
    monkeypatch.setattr(perf, "set_dpi_stack_warning",
                        lambda *a: calls.append(a))

    ui_scale.apply_dpi_scale({"dpi_scale": 100})

    assert "QT_SCALE_FACTOR" not in __import__("os").environ
    assert calls == []


def test_scale_150_sets_factor_and_warns_on_150_system(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setattr(perf, "detect_system_scale_percent", lambda: 150)
    calls = []
    monkeypatch.setattr(perf, "set_dpi_stack_warning",
                        lambda *a: calls.append(a))

    ui_scale.apply_dpi_scale({"dpi_scale": 150})

    import os
    assert os.environ["QT_SCALE_FACTOR"] == "1.5"
    assert calls == [(150, 150)]


def test_scale_125_does_not_warn_below_150_system(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setattr(perf, "detect_system_scale_percent", lambda: 149)
    calls = []
    monkeypatch.setattr(perf, "set_dpi_stack_warning",
                        lambda *a: calls.append(a))

    ui_scale.apply_dpi_scale({"dpi_scale": 125})

    import os
    assert os.environ["QT_SCALE_FACTOR"] == "1.25"
    assert calls == []


def test_detect_failure_still_sets_factor(monkeypatch):
    _clean_env(monkeypatch)

    def boom():
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(perf, "detect_system_scale_percent", boom)

    ui_scale.apply_dpi_scale({"dpi_scale": 175})

    import os
    assert os.environ["QT_SCALE_FACTOR"] == "1.75"


def test_bad_settings_is_silent(monkeypatch):
    _clean_env(monkeypatch)

    ui_scale.apply_dpi_scale(None)

    import os
    assert "QT_SCALE_FACTOR" not in os.environ
