# -*- coding: utf-8 -*-
"""database.aftersale_settings 纯配置逻辑回归（A3 settings 切片）。

用内存 fake app_settings，不落盘、不连库；aftersale_db 包装函数由既有
tests/test_app_settings.py / test_aftersale_continue_entry.py 覆盖。
"""
from database import aftersale_settings as s


class _FakeSettings:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def remove(self, key):
        self.data.pop(key, None)


def _fake(monkeypatch):
    fake = _FakeSettings()
    monkeypatch.setattr(s, "app_settings", fake)
    return fake


def test_quick_phrases_default_add_and_dedupe(monkeypatch):
    _fake(monkeypatch)
    assert s.load_quick_phrases() == s._QPC_DEFAULT
    out = s.add_quick_phrase("  新问题  ")
    assert out[0] == "新问题"
    assert out.count("新问题") == 1
    s.add_quick_phrase("新问题")
    assert s.load_quick_phrases().count("新问题") == 1
    s.add_quick_phrase("   ")
    assert "   " not in s.load_quick_phrases()


def test_last_people_only_writes_non_empty(monkeypatch):
    fake = _fake(monkeypatch)
    s.save_last_people("", "")
    assert fake.data == {}
    s.save_last_people(" 张三 ", " 李四 ")
    assert s.load_last_creator() == "张三"
    assert s.load_last_resolver() == "李四"
    assert s.load_settings_str("missing") == ""
    assert s.load_settings_str("aftersale_last_creator") == "张三"


def test_remember_occurred_off_clears_value(monkeypatch):
    _fake(monkeypatch)
    assert s.remember_occurred_enabled() is True
    s.save_last_occurred("2026-10-19")
    assert s.load_last_occurred() == "2026-10-19"
    s.set_remember_occurred(False)
    assert s.remember_occurred_enabled() is False
    assert s.load_last_occurred() == ""
    s.set_remember_occurred(True)
    assert s.load_last_occurred() == ""  # 重开不复活旧值


def test_save_last_occurred_ignores_blank(monkeypatch):
    _fake(monkeypatch)
    s.save_last_occurred("   ")
    assert s.load_last_occurred() == ""


def test_auto_refresh_defaults_and_clamps(monkeypatch):
    fake = _fake(monkeypatch)
    assert s.auto_refresh_enabled() is False
    assert s.auto_refresh_interval() == 30
    s.set_auto_refresh(True)
    assert s.auto_refresh_enabled() is True
    s.set_auto_refresh_interval(1)
    assert s.auto_refresh_interval() == 5
    s.set_auto_refresh_interval(300)
    assert s.auto_refresh_interval() == 300
    s.set_auto_refresh_interval("bad")
    assert s.auto_refresh_interval() == 300
    assert fake.data[s._AUTO_REFRESH_INTERVAL_KEY] == 300
