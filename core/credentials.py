# -*- coding: utf-8 -*-
"""SSH 凭据来源模型（P1-8 阶段 1：来源显式化 / 不再静默覆盖 / 清除入口）

背景（用户反馈 2026-10-04 拆分 ssh_* 与 tcp_* 之后仍存在的三个语义缺口）：

1. **来源不可见**：界面上的密码框既可能是「设置里已保存（DPAPI 加密）」的，
   也可能是本次刚敲进去的，两者外观完全一样。用户无法判断「我这次改的会不会
   被记住」「我看到的这个密码是哪来的」。
2. **静默覆盖**：任何一次连接都会把表单里的值写回 ``credentials.json``。用户
   为了试一次临时密码（帮同事排障、临时换账号）而敲进去的值，会在用户毫不知情
   的情况下永久替换掉原密码——下一个连接就再也回不去了。
3. **没有清除入口**：密码一旦存下，只能靠「输入新密码覆盖」，无法显式清空
   （忘记密码/移交设备/怀疑泄露时无处可去）。

本模块把这些语义抽成**零 Qt、零 I/O 的纯逻辑**，供三处调用点共用：

- ``windows/remote_session/remote_hub.py``：XTCP 卡（设备凭据）与 TCP 直连卡
  （直连主机凭据）的「记住密码」开关、来源提示、清除按钮；
- ``windows/remote_session/remote_mixin.py``：远程会话窗口连接前解析凭据；
- ``autowork_with_table.py``：主面板 P2P 表单（与 XTCP 卡同键同源）。

关键不变量（tests/test_credentials.py 逐条守住）：

- ``credential_patch(..., remember=False)`` 的结果里**永远不含**密码键——
  「不记住」不是 UI 上的摆设，而是构造上不可能落盘；
- 每种 kind 只碰自己那对键（设备 ``ssh_user``/``ssh_pass`` ↔ 直连
  ``tcp_ssh_user``/``tcp_ssh_pass``），拆分语义不被本模块放宽；
- 会话凭据只活在 ``SessionCredentialStore`` 里，不产生任何设置键。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Mapping, Optional

# ==================== 凭据种类 ====================

#: 设备凭据（XTCP 隧道 visitor 连进来的那台设备自己的账号，如 newbv）
KIND_DEVICE = "device"
#: 直连主机凭据（TCP 直连时用户直接填的主机账号，常是 frps 服务器的 root）
KIND_DIRECT = "direct"

#: kind → settings 键对（user, pass）。两对键**互不覆盖**是 2026-10-04 的拆分契约。
CREDENTIAL_KEYS: dict[str, tuple[str, str]] = {
    KIND_DEVICE: ("ssh_user", "ssh_pass"),
    KIND_DIRECT: ("tcp_ssh_user", "tcp_ssh_pass"),
}

#: kind → 界面/日志用中文名
KIND_LABELS: dict[str, str] = {
    KIND_DEVICE: "设备（隧道）",
    KIND_DIRECT: "直连主机",
}

# ==================== 来源 ====================

#: 用户此刻在输入框里敲的值
SOURCE_FORM = "form"
#: 本次会话内输入、选择不落盘的值（进程退出即丢失）
SOURCE_SESSION = "session"
#: 从 config/credentials.json 读出的已保存值（DPAPI 加密域）
SOURCE_SAVED = "saved"
#: 四个来源都没有值
SOURCE_NONE = "none"

SOURCE_LABELS: dict[str, str] = {
    SOURCE_FORM: "本次输入",
    SOURCE_SESSION: "本次会话（未落盘）",
    SOURCE_SAVED: "已保存（DPAPI 加密）",
    SOURCE_NONE: "未填写",
}

#: 来源 → 是否已落盘（供界面提示「关闭窗口后是否还在」）
SOURCE_IS_PERSISTED: dict[str, bool] = {
    SOURCE_FORM: False,
    SOURCE_SESSION: False,
    SOURCE_SAVED: True,
    SOURCE_NONE: False,
}


def credential_keys(kind: str) -> tuple[str, str]:
    """返回 kind 对应的 (user_key, pass_key)；未知 kind 抛 ValueError。

    调用点先用本函数取键，避免各处硬编码键名导致拆分契约被绕过。
    """
    try:
        return CREDENTIAL_KEYS[kind]
    except KeyError:
        raise ValueError(f"未知凭据种类: {kind!r}") from None


def kind_label(kind: str) -> str:
    """kind 的中文名（日志/提示用），未知 kind 原样返回"""
    return KIND_LABELS.get(kind, str(kind))


def describe_source(source: str) -> str:
    """来源 → 界面文案；未知来源回退 SOURCE_NONE 文案"""
    return SOURCE_LABELS.get(source, SOURCE_LABELS[SOURCE_NONE])


def is_persisted_source(source: str) -> bool:
    """该来源是否已落盘（决定提示语「重启后仍在 / 关闭即丢」）"""
    return bool(SOURCE_IS_PERSISTED.get(source, False))


# ==================== 解析结果 ====================


@dataclass(frozen=True)
class Credentials:
    """一次连接实际使用的凭据 + 它的来源

    ``source`` 描述的是**密码**的来源（密码是决定「要不要落盘、怎么提示」的那一项；
    用户名不是敏感值，可能比密码来自更靠前的位置）。
    """

    username: str = ""
    password: str = ""
    source: str = SOURCE_NONE

    @property
    def has_user(self) -> bool:
        return bool(self.username)

    @property
    def has_password(self) -> bool:
        return bool(self.password)

    @property
    def is_complete(self) -> bool:
        """账号与密码都齐（可以拿去认证）"""
        return self.has_user and self.has_password

    @property
    def source_text(self) -> str:
        return describe_source(self.source)

    @property
    def source_persisted(self) -> bool:
        return is_persisted_source(self.source)

    def describe(self) -> str:
        """日志用摘要——**绝不含密码明文**"""
        return (f"user={self.username or '(空)'} "
                f"pass={'已设置' if self.has_password else '未设置'} "
                f"来源={self.source_text}")


# ==================== 写盘补丁 ====================


def credential_patch(kind: str, username: str, password: str,
                     remember: bool = True) -> dict[str, str]:
    """构造写回 settings 的补丁（**非空才覆盖**语义保持不变）

    - ``username`` 非空 → 写 user_key（用户名不是敏感值，与「记住密码」无关）；
    - ``password`` 非空 **且** ``remember`` 为真 → 写 pass_key；
    - ``remember=False`` 时结果里不会出现密码键，构造上无法静默落盘；
    - 两者都为空 → 返回 {}（调用点据此判断「没有可保存的内容」）。
    """
    user_key, pass_key = credential_keys(kind)
    data: dict[str, str] = {}
    user = (username or "").strip()
    if user:
        data[user_key] = user
    if password and remember:
        data[pass_key] = password
    return data


def clear_patch(kind: str, *, include_username: bool = False) -> dict[str, str]:
    """清除已保存凭据的补丁（写空串）

    默认**只清密码**：界面上的「清除已保存凭据」清的是「记住的那个密码」，
    账号（如 newbv/root）留着省一次输入；需要连账号一起清时传
    ``include_username=True``。

    ``core/secrets.encrypt_secret("")`` 原样返回空串，读取侧对空值一律视为
    「未配置」（``remote_mixin`` 的 ``if ssh_pass:`` 分支），因此写空串即等于
    清除，不需要新增 delete 接口。
    """
    user_key, pass_key = credential_keys(kind)
    patch = {pass_key: ""}
    if include_username:
        patch[user_key] = ""
    return patch


def resolve_credentials(kind: str, *,
                        form_username: str = "",
                        form_password: str = "",
                        settings: Optional[Mapping[str, Any]] = None,
                        session: "Optional[SessionCredentialStore]" = None
                        ) -> Credentials:
    """按 **表单 > 本次会话 > 已保存设置** 的顺序补齐账号与密码

    逐项（账号、密码独立）取第一个非空来源；``source`` 取密码的来源。
    任何一项都没有时返回空值 + ``SOURCE_NONE``，由调用点决定是提示补全还是
    回退到旧行为。
    """
    user_key, pass_key = credential_keys(kind)
    saved = settings or {}
    sess = session.get(kind) if session is not None else None

    def _pick(form_value: str, sess_value: str, saved_value: Any):
        """返回 (值, 来源)；表单非空优先，其次会话，最后已保存"""
        if form_value:
            return form_value, SOURCE_FORM
        if sess_value:
            return sess_value, SOURCE_SESSION
        text = "" if saved_value is None else str(saved_value)
        if text:
            return text, SOURCE_SAVED
        return "", SOURCE_NONE

    username, _u_src = _pick((form_username or "").strip(),
                             (sess.username if sess else ""),
                             saved.get(user_key, ""))
    password, p_src = _pick((form_password or "").strip(),
                            (sess.password if sess else ""),
                            saved.get(pass_key, ""))
    return Credentials(username=username, password=password, source=p_src)


def source_hint(kind: str, creds: Credentials) -> str:
    """界面提示一句话：凭据来源 + 是否已落盘 + 该 kind 的键名

    例：``凭据来源：已保存（DPAPI 加密）· ssh_pass 已落盘，重启后仍在``
    """
    _user_key, pass_key = credential_keys(kind)
    if creds.source_persisted:
        tail = f"{pass_key} 已落盘，重启后仍在"
    elif creds.source == SOURCE_SESSION:
        tail = f"{pass_key} 未落盘，关闭窗口即丢失"
    else:
        tail = f"{pass_key} 不会写入配置文件"
    return f"凭据来源：{creds.source_text} · {tail}"


# ==================== 本次会话凭据（不落盘） ====================


class SessionCredentialStore:
    """进程内的「本次会话」凭据表（不写任何设置键）

    用途：用户在「记住密码」取消勾选后输入的密码，仍需在本次运行里对后续连接
    生效（否则关掉连接就再也连不上，用户只能重新输入）。按 kind 隔离，设备与
    直连互不影响。读取方（worker 线程）与主线程并发访问，故加锁。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Credentials] = {}

    def set(self, kind: str, username: str, password: str) -> None:
        """记录一次会话内凭据（任一为空时按空处理）"""
        credential_keys(kind)          # 校验 kind，未知则抛 ValueError
        creds = Credentials(username=(username or "").strip(),
                            password=password or "",
                            source=SOURCE_SESSION)
        with self._lock:
            self._data[kind] = creds

    def get(self, kind: str) -> Optional[Credentials]:
        """取会话内凭据；没有则 None（不抛异常，供解析路径直接调用）"""
        with self._lock:
            return self._data.get(kind)

    def clear(self, kind: str) -> None:
        """丢弃某 kind 的会话凭据（清除按钮 / 断开连接时调用）"""
        with self._lock:
            self._data.pop(kind, None)

    def clear_all(self) -> None:
        with self._lock:
            self._data.clear()

    def kinds(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._data.keys())

    def snapshot(self) -> dict[str, Credentials]:
        """副本（日志/测试用；值仍是完整凭据，调用方不得打印 password）"""
        with self._lock:
            return dict(self._data)


_session_store: Optional[SessionCredentialStore] = None
_session_lock = threading.Lock()


def get_session_store() -> SessionCredentialStore:
    """进程级共享的会话凭据表

    三处界面（主面板 / 远程页卡片 / 远程会话窗口）需要看到同一份会话凭据，
    故此处提供惰性单例；测试用 ``reset_session_store()`` 隔离。
    """
    global _session_store
    with _session_lock:
        if _session_store is None:
            _session_store = SessionCredentialStore()
        return _session_store


def reset_session_store() -> None:
    """丢弃进程级单例（测试隔离 / 退出登录场景）"""
    global _session_store
    with _session_lock:
        _session_store = None
