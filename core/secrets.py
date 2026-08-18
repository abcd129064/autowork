# -*- coding: utf-8 -*-
"""settings.json 敏感字段 DPAPI 加密存储

方案：
- Windows DPAPI（ctypes 调 crypt32.CryptProtectData / CryptUnprotectData），
  密文与当前 Windows 用户账户绑定，换用户/机器后无法解密
- 加密结果以 base64 字符串存储，带 "enc:" 前缀标记，便于识别是否已加密
- 解密失败 / 非 Windows 环境一律降级处理，绝不抛异常崩溃：
  * 加密不可用 → 保持明文原样返回（功能不受影响）
  * 解密失败（密文损坏/换机器换用户）→ 返回空串，引导用户重新输入

敏感 key 清单（顶层）：ssh_pass / upload_pass / xtcp_secret_key
嵌套敏感路径：frpc_server.auth_token、api_credentials.api{1,2}.password
敏感字典（全部值加密）：ai_api_keys（各 AI 厂商的 API Key）
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import sys

# 加密标记前缀：settings.json 中带此前缀的值视为已加密
ENC_PREFIX = "enc:"

# 顶层敏感 key（密码/token 类）
SENSITIVE_KEYS = ("ssh_pass", "upload_pass", "xtcp_secret_key")

# 嵌套敏感路径：(顶层key, 子key...)
NESTED_SENSITIVE_PATHS = (
    ("frpc_server", "auth_token"),
    ("api_credentials", "api1", "password"),
    ("api_credentials", "api2", "password"),
    ("mysql_sync", "password"),
)

# 敏感字典：顶层 key -> 字典内全部字符串值逐一加解密（子键动态，如厂商 id）
SENSITIVE_DICT_KEYS = ("ai_api_keys",)

_IS_WINDOWS = sys.platform == "win32"


# ==================== DPAPI 原语 ====================

if _IS_WINDOWS:
    import ctypes.wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    def _blob_from_bytes(data: bytes) -> "_DATA_BLOB":
        buf = ctypes.create_string_buffer(data, len(data))
        return _DATA_BLOB(len(data), buf)

    def _dpapi_call(func, data: bytes) -> bytes | None:
        """调用 CryptProtectData/CryptUnprotectData，失败返回 None"""
        try:
            blob_in = _blob_from_bytes(data)
            blob_out = _DATA_BLOB()
            if func(ctypes.byref(blob_in), None, None, None, None, 0,
                    ctypes.byref(blob_out)):
                try:
                    return ctypes.string_at(blob_out.pbData, blob_out.cbData)
                finally:
                    # DPAPI 返回的缓冲区由系统分配，调用方必须 LocalFree，
                    # 否则每次加解密都漏一块内存，长期运行会累积放大
                    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        except Exception:
            pass
        return None

    def _dpapi_encrypt(data: bytes) -> bytes | None:
        return _dpapi_call(ctypes.windll.crypt32.CryptProtectData, data)

    def _dpapi_decrypt(data: bytes) -> bytes | None:
        return _dpapi_call(ctypes.windll.crypt32.CryptUnprotectData, data)
else:
    def _dpapi_encrypt(data: bytes) -> bytes | None:
        return None

    def _dpapi_decrypt(data: bytes) -> bytes | None:
        return None


def dpapi_available() -> bool:
    """DPAPI 是否可用（仅 Windows 环境可用）"""
    return _IS_WINDOWS


# ==================== 单值加解密 ====================

def encrypt_secret(value):
    """加密单个敏感值，返回 "enc:base64" 字符串

    降级策略（原样返回，不崩溃）：
    - 空值 / 非字符串
    - 已带 enc: 前缀（幂等）
    - DPAPI 不可用或加密失败
    """
    if not isinstance(value, str) or not value:
        return value
    if value.startswith(ENC_PREFIX):
        # 已带前缀直接返回，保证重复加密幂等：否则密文会层层嵌套，
        # 解密一次后仍残留 "enc:" 前缀，用户看到的密码就是脏数据
        return value
    raw = _dpapi_encrypt(value.encode("utf-8"))
    if raw is None:
        return value
    return ENC_PREFIX + base64.b64encode(raw).decode("ascii")


def decrypt_secret(value):
    """解密单个敏感值

    - 无 enc: 前缀（明文/其他类型）→ 原样返回
    - 密文损坏或 DPAPI 解密失败（换用户/换机器）→ 返回空串，
      界面上表现为空密码，引导用户重新输入
    """
    if not isinstance(value, str) or not value.startswith(ENC_PREFIX):
        return value
    try:
        raw = base64.b64decode(value[len(ENC_PREFIX):], validate=True)
    except Exception:
        return ""
    plain = _dpapi_decrypt(raw)
    if plain is None:
        return ""
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError:
        return ""


# ==================== 整个 settings 字典加解密 ====================

def _transform_nested(settings: dict, path, convert):
    """按嵌套路径转换值；沿途逐级复制字典，保证不修改入参的深层对象

    settings 本身必须已是调用方的副本；路径中间节点非 dict 时静默跳过。
    """
    top = path[0]
    if not isinstance(settings.get(top), dict):
        return
    node = dict(settings[top])
    settings[top] = node
    for key in path[1:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            return
        child = dict(child)
        node[key] = child
        node = child
    last = path[-1]
    if last in node:
        node[last] = convert(node[last])


def _transform_dict_values(settings: dict, key, convert):
    """将 settings[key] 字典中的全部字符串值逐一转换（不修改入参深层对象）"""
    child = settings.get(key)
    if not isinstance(child, dict):
        return
    new_child = {}
    for k, v in child.items():
        new_child[k] = convert(v) if isinstance(v, str) else v
    settings[key] = new_child


def encrypt_settings(settings: dict) -> dict:
    """返回敏感字段已加密的副本（不修改入参），非敏感字段原样保留"""
    if not isinstance(settings, dict):
        return settings
    result = dict(settings)
    for key in SENSITIVE_KEYS:
        if key in result:
            result[key] = encrypt_secret(result[key])
    for path in NESTED_SENSITIVE_PATHS:
        _transform_nested(result, path, encrypt_secret)
    for key in SENSITIVE_DICT_KEYS:
        _transform_dict_values(result, key, encrypt_secret)
    return result


def decrypt_settings(settings: dict) -> dict:
    """返回敏感字段已解密的副本（不修改入参），解密失败降级为空串"""
    if not isinstance(settings, dict):
        return settings
    result = dict(settings)
    for key in SENSITIVE_KEYS:
        if key in result:
            result[key] = decrypt_secret(result[key])
    for path in NESTED_SENSITIVE_PATHS:
        _transform_nested(result, path, decrypt_secret)
    for key in SENSITIVE_DICT_KEYS:
        _transform_dict_values(result, key, decrypt_secret)
    return result


def has_plaintext_secret(settings: dict) -> bool:
    """检测 settings 中是否存在未加密的敏感值（用于自动迁移判断）"""
    if not isinstance(settings, dict):
        return False

    def _is_plaintext(v):
        return isinstance(v, str) and v and not v.startswith(ENC_PREFIX)

    for key in SENSITIVE_KEYS:
        if _is_plaintext(settings.get(key)):
            return True
    for path in NESTED_SENSITIVE_PATHS:
        node = settings
        for key in path[:-1]:
            node = node.get(key) if isinstance(node, dict) else None
            if node is None:
                break
        else:
            if isinstance(node, dict) and _is_plaintext(node.get(path[-1])):
                return True
    for key in SENSITIVE_DICT_KEYS:
        child = settings.get(key)
        if isinstance(child, dict) and any(
                _is_plaintext(v) for v in child.values()):
            return True
    return False


# ==================== 启动时自动迁移 ====================

def migrate_settings_file(path: str | None = None) -> bool:
    """启动时自动迁移：检测到明文敏感字段 → 加密回写一次（用户无感）

    幂等：已加密文件不会重复处理；DPAPI 不可用时跳过（保留明文，不崩溃）。
    返回是否实际发生了迁移写入。
    """
    if not dpapi_available():
        return False
    if path is None:
        from core.app_paths import get_app_dir
        path = os.path.join(get_app_dir(), "settings.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    if not isinstance(settings, dict) or not has_plaintext_secret(settings):
        # 用「是否还有明文敏感字段」当迁移标记：首次加密回写后此检查不再命中，
        # 天然防止每次启动重复迁移，不需要额外落盘标记文件
        return False
    try:
        encrypted = encrypt_settings(settings)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(encrypted, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False
