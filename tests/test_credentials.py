# -*- coding: utf-8 -*-
"""凭据来源模型单测（P1-8 阶段 1）

覆盖 ``core/credentials.py`` 的三组契约：

1. 键映射与拆分不被放宽（设备 ssh_* ↔ 直连 tcp_ssh_*，且必须与
   ``core/app_settings.KEY_DOMAIN`` / ``core/secrets.SENSITIVE_KEYS`` 一致）；
2. 「不记住」构造上不可能落盘（补丁里不含密码键、不含密码明文）；
3. 解析优先级 表单 > 本次会话 > 已保存，且会话凭据不产生任何设置键。

纯逻辑用例，不需要 Qt/qfluentwidgets，基线解释器可直接运行。
"""
import pytest

from core import app_settings as _as
from core import credentials as cred
from core import secrets as _sec


# ==================== 键映射 ====================


def test_kind_keys_map_to_split_settings_keys():
    assert cred.credential_keys(cred.KIND_DEVICE) == ("ssh_user", "ssh_pass")
    assert cred.credential_keys(cred.KIND_DIRECT) == ("tcp_ssh_user", "tcp_ssh_pass")


def test_unknown_kind_raises_value_error():
    with pytest.raises(ValueError):
        cred.credential_keys("nope")
    with pytest.raises(ValueError):
        cred.credential_patch("nope", "u", "p", True)
    with pytest.raises(ValueError):
        cred.clear_patch("nope")


def test_credential_keys_stay_in_sync_with_settings_and_secrets():
    """凭据键必须落在 credentials 域，且密码键必须在 DPAPI 敏感清单里"""
    for kind in (cred.KIND_DEVICE, cred.KIND_DIRECT):
        user_key, pass_key = cred.credential_keys(kind)
        assert _as.domain_of(user_key) == "credentials"
        assert _as.domain_of(pass_key) == "credentials"
        assert pass_key in _sec.SENSITIVE_KEYS
        # 用户名不是敏感值（与 2026-10-04 拆分时的判定一致）
        assert user_key not in _sec.SENSITIVE_KEYS


# ==================== 写盘补丁 ====================


@pytest.mark.parametrize("kind", [cred.KIND_DEVICE, cred.KIND_DIRECT])
def test_patch_with_remember_writes_both_keys(kind):
    user_key, pass_key = cred.credential_keys(kind)
    patch = cred.credential_patch(kind, "  newbv  ", "devpw", remember=True)
    assert patch == {user_key: "newbv", pass_key: "devpw"}


@pytest.mark.parametrize("kind", [cred.KIND_DEVICE, cred.KIND_DIRECT])
def test_patch_without_remember_never_contains_password(kind):
    """核心不变量：取消「记住密码」后补丁里不能有任何形式的密码"""
    user_key, pass_key = cred.credential_keys(kind)
    patch = cred.credential_patch(kind, "newbv", "devpw", remember=False)
    assert pass_key not in patch
    assert "devpw" not in patch.values()
    assert patch == {user_key: "newbv"}


def test_patch_skips_empty_fields():
    """非空才覆盖：空账号空密码不产生任何键（保持既有行为）"""
    assert cred.credential_patch(cred.KIND_DEVICE, "", "", True) == {}
    assert cred.credential_patch(cred.KIND_DEVICE, "   ", "", True) == {}
    # 只有密码时也只写密码键
    assert cred.credential_patch(cred.KIND_DEVICE, "", "pw", True) == {"ssh_pass": "pw"}


def test_patch_of_one_kind_never_touches_the_other():
    device = cred.credential_patch(cred.KIND_DEVICE, "newbv", "devpw", True)
    direct = cred.credential_patch(cred.KIND_DIRECT, "root", "srvpw", True)
    assert set(device) == {"ssh_user", "ssh_pass"}
    assert set(direct) == {"tcp_ssh_user", "tcp_ssh_pass"}
    assert not set(device) & set(direct)


def test_clear_patch_empties_password_by_default_and_both_on_request():
    assert cred.clear_patch(cred.KIND_DEVICE) == {"ssh_pass": ""}
    assert cred.clear_patch(cred.KIND_DIRECT) == {"tcp_ssh_pass": ""}
    assert cred.clear_patch(cred.KIND_DEVICE, include_username=True) == {
        "ssh_user": "", "ssh_pass": ""}
    assert cred.clear_patch(cred.KIND_DIRECT, include_username=True) == {
        "tcp_ssh_user": "", "tcp_ssh_pass": ""}
    # 清零补丁绝不碰另一侧的键
    assert "tcp_ssh_pass" not in cred.clear_patch(cred.KIND_DEVICE, include_username=True)
    # 空串与「未配置」等价：加密降级原样返回，读取侧按假值处理
    assert _sec.encrypt_secret("") == ""
    assert _sec.decrypt_secret("") == ""


# ==================== 来源解析 ====================


def test_resolve_prefers_form_then_session_then_saved():
    store = cred.SessionCredentialStore()
    store.set(cred.KIND_DEVICE, "sess_user", "sess_pw")
    saved = {"ssh_user": "saved_user", "ssh_pass": "saved_pw"}

    # 表单齐 → 表单
    got = cred.resolve_credentials(cred.KIND_DEVICE, form_username="form_user",
                                   form_password="form_pw", settings=saved,
                                   session=store)
    assert (got.username, got.password, got.source) == (
        "form_user", "form_pw", cred.SOURCE_FORM)

    # 表单空 → 会话
    got = cred.resolve_credentials(cred.KIND_DEVICE, settings=saved, session=store)
    assert (got.username, got.password, got.source) == (
        "sess_user", "sess_pw", cred.SOURCE_SESSION)

    # 表单与会话都空 → 已保存
    got = cred.resolve_credentials(cred.KIND_DEVICE, settings=saved)
    assert (got.username, got.password, got.source) == (
        "saved_user", "saved_pw", cred.SOURCE_SAVED)


def test_resolve_fills_each_field_independently_and_reports_password_source():
    """表单只填了账号时，密码可以来自已保存值；来源按密码口径报告"""
    saved = {"ssh_user": "old_user", "ssh_pass": "saved_pw"}
    got = cred.resolve_credentials(cred.KIND_DEVICE, form_username="new_user",
                                   settings=saved)
    assert got.username == "new_user"
    assert got.password == "saved_pw"
    assert got.source == cred.SOURCE_SAVED
    assert got.is_complete


def test_resolve_empty_everywhere():
    got = cred.resolve_credentials(cred.KIND_DEVICE, settings={})
    assert (got.username, got.password) == ("", "")
    assert got.source == cred.SOURCE_NONE
    assert not got.is_complete
    assert got.source_text == "未填写"


def test_resolve_does_not_mutate_settings_and_ignores_other_kind_keys():
    saved = {"tcp_ssh_user": "root", "tcp_ssh_pass": "srvpw"}
    before = dict(saved)
    got = cred.resolve_credentials(cred.KIND_DEVICE, settings=saved)
    assert got.source == cred.SOURCE_NONE      # 设备凭据不会去读直连键
    assert saved == before


def test_resolve_tolerates_none_and_non_string_values():
    got = cred.resolve_credentials(cred.KIND_DIRECT,
                                   settings={"tcp_ssh_user": None,
                                             "tcp_ssh_pass": 12345})
    assert got.username == ""
    assert got.password == "12345"
    assert got.source == cred.SOURCE_SAVED


# ==================== 会话凭据表 ====================


def test_session_store_isolates_kinds_and_clears():
    store = cred.SessionCredentialStore()
    store.set(cred.KIND_DEVICE, "dev", "devpw")
    store.set(cred.KIND_DIRECT, "root", "srvpw")
    assert store.get(cred.KIND_DEVICE).password == "devpw"
    assert store.get(cred.KIND_DIRECT).password == "srvpw"
    assert set(store.kinds()) == {cred.KIND_DEVICE, cred.KIND_DIRECT}

    store.clear(cred.KIND_DEVICE)
    assert store.get(cred.KIND_DEVICE) is None
    assert store.get(cred.KIND_DIRECT) is not None
    store.clear_all()
    assert store.kinds() == ()


def test_session_store_rejects_unknown_kind_and_strips_username():
    store = cred.SessionCredentialStore()
    with pytest.raises(ValueError):
        store.set("nope", "u", "p")
    store.set(cred.KIND_DEVICE, "  dev  ", "pw")
    creds = store.get(cred.KIND_DEVICE)
    assert creds.username == "dev"
    assert creds.source == cred.SOURCE_SESSION


def test_session_store_never_writes_settings_keys():
    """会话凭据只活在内存：不产生补丁、不改动传入的 settings"""
    store = cred.SessionCredentialStore()
    saved = {"ssh_user": "saved_user"}
    store.set(cred.KIND_DEVICE, "tmp", "tmp_pw")
    got = cred.resolve_credentials(cred.KIND_DEVICE, settings=saved, session=store)
    assert got.source == cred.SOURCE_SESSION
    assert saved == {"ssh_user": "saved_user"}
    # 勾了「记住」才会把会话里的值带去落盘，且由调用点显式决定
    assert cred.credential_patch(cred.KIND_DEVICE, got.username, got.password,
                                 remember=False) == {"ssh_user": "tmp"}


def test_process_level_session_store_is_shared_and_resettable():
    first = cred.get_session_store()
    assert first is cred.get_session_store()
    first.set(cred.KIND_DEVICE, "a", "b")
    cred.reset_session_store()
    second = cred.get_session_store()
    assert second is not first
    assert second.get(cred.KIND_DEVICE) is None


# ==================== 文案（不得泄露密码） ====================


def test_source_labels_and_persistence_flags():
    assert cred.describe_source(cred.SOURCE_SAVED) == "已保存（DPAPI 加密）"
    assert cred.describe_source(cred.SOURCE_SESSION) == "本次会话（未落盘）"
    assert cred.describe_source("未知来源") == "未填写"
    assert cred.is_persisted_source(cred.SOURCE_SAVED) is True
    assert cred.is_persisted_source(cred.SOURCE_FORM) is False
    assert cred.is_persisted_source(cred.SOURCE_SESSION) is False
    assert cred.kind_label(cred.KIND_DEVICE) == "设备（隧道）"
    assert cred.kind_label(cred.KIND_DIRECT) == "直连主机"
    assert cred.kind_label("other") == "other"


def test_describe_and_hint_never_leak_password():
    creds = cred.Credentials(username="newbv", password="sup3r-secret",
                             source=cred.SOURCE_SESSION)
    text = creds.describe()
    hint = cred.source_hint(cred.KIND_DEVICE, creds)
    assert "sup3r-secret" not in text
    assert "sup3r-secret" not in hint
    assert "newbv" in text
    assert "已设置" in text
    assert "ssh_pass" in hint
    assert "关闭窗口即丢失" in hint
    saved = cred.Credentials(username="newbv", password="pw",
                             source=cred.SOURCE_SAVED)
    assert "重启后仍在" in cred.source_hint(cred.KIND_DIRECT, saved)
