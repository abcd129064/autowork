# -*- coding: utf-8 -*-
"""core.app_settings 配置门面回归测试

覆盖：旧 settings.json 迁移分拣、键路由读写、加密域封装、
幂等迁移、get_merged 合并视图、缓存隔离。
隔离方式：monkeypatch core.app_paths.get_app_dir 指向 tmp_path
（app_settings 内部经 app_paths 模块属性调用，patch 可生效）。
"""
import json
import os

import pytest

import core.app_paths
import core.app_settings as fas


@pytest.fixture
def env(monkeypatch, tmp_path):
    """app_dir 隔离 + 门面缓存/迁移标志前后重置"""
    monkeypatch.setattr(core.app_paths, "get_app_dir", lambda: str(tmp_path))
    fas.invalidate_cache()
    fas._migrated = False
    yield tmp_path
    fas.invalidate_cache()
    fas._migrated = False


def _write_legacy(tmp_path, data):
    with open(tmp_path / "settings.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ==================== 迁移分拣 ====================

def test_migrate_sorts_keys_into_domains(env):
    tmp_path = env
    _write_legacy(tmp_path, {
        "aftersale_cycle": {"type": "tue", "span": 7},
        "perf_table_smooth": False,
        "mysql_sync": {"enabled": True, "host": "h"},
        "ssh_pass": "明文密码",
        "theme_color": "#00BCD4",
        "exe_dir": "D:/x",
        "remote_sessions": [{"name": "a"}],
        "web_port": 8069,
        "unknown_custom_key": "v",   # 未登记键 → misc
    })
    assert fas.migrate_legacy() is True

    cfg = tmp_path / "config"
    assert (cfg / "aftersale.json").is_file()
    assert (cfg / "perf.json").is_file()
    assert (cfg / "database.json").is_file()
    assert (cfg / "credentials.json").is_file()
    assert (cfg / "ui.json").is_file()
    assert (cfg / "paths.json").is_file()
    assert (cfg / "remote.json").is_file()
    assert (cfg / "misc.json").is_file()

    assert _read_json(cfg / "aftersale.json")["aftersale_cycle"]["type"] == "tue"
    assert _read_json(cfg / "perf.json")["perf_table_smooth"] is False
    assert _read_json(cfg / "misc.json")["unknown_custom_key"] == "v"

    # 敏感键落盘已加密（enc: 前缀），读取侧透明解密
    raw_cred = _read_json(cfg / "credentials.json")
    assert str(raw_cred["ssh_pass"]).startswith("enc:")
    assert fas.get("ssh_pass") == "明文密码"

    # 旧文件改名 .bak
    assert not (tmp_path / "settings.json").is_file()
    assert (tmp_path / "settings.json.bak").is_file()


def test_migrate_idempotent(env):
    tmp_path = env
    _write_legacy(tmp_path, {"web_port": 8069})
    assert fas.migrate_legacy() is True
    # 第二次：settings.json 已不存在 → no-op
    assert fas.migrate_legacy() is False
    # 手动恢复旧文件再跑：域文件已有值不被覆盖（只补缺键）
    _write_legacy(tmp_path, {"web_port": 9999, "new_key": 1})
    fas._migrated = False
    fas.migrate_legacy()
    assert _read_json(tmp_path / "config" / "misc.json")["web_port"] == 8069
    assert _read_json(tmp_path / "config" / "misc.json")["new_key"] == 1


def test_no_legacy_file_no_migration(env):
    assert fas.migrate_legacy() is False


# ==================== 键路由读写 ====================

def test_set_get_routes_by_key(env):
    fas.set("perf_acrylic", False)
    fas.set("theme_color", "#123456")
    fas.set("ssh_user", "admin")
    assert fas.get("perf_acrylic") is False
    assert fas.get("theme_color") == "#123456"
    assert fas.get("ssh_user") == "admin"
    # 各键落在各自域文件
    assert "perf_acrylic" in _read_json(env / "config" / "perf.json")
    assert "theme_color" in _read_json(env / "config" / "ui.json")
    assert "ssh_user" in _read_json(env / "config" / "credentials.json")


def test_remove_deletes_key(env):
    fas.set("web_port", 1234)
    assert fas.get("web_port") == 1234
    assert fas.remove("web_port") is True
    assert fas.get("web_port", "default") == "default"
    assert "web_port" not in _read_json(env / "config" / "misc.json")


def test_encrypted_domain_roundtrip(env):
    """加密域写入落盘为密文、读取返回明文（mysql_sync.password）"""
    fas.set("mysql_sync", {"enabled": True, "password": "secret123"})
    raw = _read_json(env / "config" / "database.json")
    assert str(raw["mysql_sync"]["password"]).startswith("enc:")
    assert fas.get("mysql_sync")["password"] == "secret123"


def test_get_domain_returns_copy(env):
    fas.set("perf_animation", True)
    d1 = fas.get_domain("perf")
    d1["perf_animation"] = False
    assert fas.get("perf_animation") is True


def test_update_domain_merges(env):
    fas.set("perf_acrylic", True)
    fas.update_domain("perf", {"perf_animation": False})
    assert fas.get("perf_acrylic") is True
    assert fas.get("perf_animation") is False


def test_get_merged_equivalent_to_legacy_file(env):
    legacy = {
        "dpi_scale": 100, "font_size": 11, "theme_color": "#00BCD4",
        "mysql_sync": {"enabled": False}, "ssh_pass": "p",
        "aftersale_cycle": {"type": "tue"},
        "misc_only": 1,
    }
    _write_legacy(env, legacy)
    fas._migrated = False
    fas.migrate_legacy()
    merged = fas.get_merged()
    for k, v in legacy.items():
        assert merged[k] == v, k
    assert merged["ssh_pass"] == "p"  # 合并视图为明文（等同旧读文件+解密）


def test_cache_isolation_between_reload(env, monkeypatch, tmp_path_factory):
    """app_dir 切换后 invalidate_cache 生效：新目录读到新数据，无跨目录串扰"""
    fas.set("web_port", 1111)
    other = tmp_path_factory.mktemp("other")
    monkeypatch.setattr(core.app_paths, "get_app_dir", lambda: str(other))
    fas.invalidate_cache()
    assert fas.get("web_port", "none") == "none"
    fas.set("web_port", 2222)
    assert fas.get("web_port") == 2222
