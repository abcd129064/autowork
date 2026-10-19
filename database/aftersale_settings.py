# -*- coding: utf-8 -*-
"""售后面板设置读写（A3 settings 切片）。

从 database/aftersale_db.py 下沉：常用句、上次填写人/解决人、记住上次
发生日期、自动刷新开关与间隔。只依赖 core.app_settings，不连库、不碰 Qt；
aftersale_db 保留同名包装函数，外部调用与配置键不变。

只依赖 app_settings 是刻意的：设置读写要能在无 GUI 的脚本与单测里直接使用，
引入 Qt 或数据库连接会把这些调用方挡在门外。
"""

from core import app_settings

# ==================== 常用句 ====================

_QPC_KEY = "aftersale_quick_phrases"
# 缺省常用句覆盖售后群最高频的几类报障，首次使用即可点选，无需先手工录入
_QPC_DEFAULT = [
    "主机没有开机", "遥控器没反应", "程序没了", "不能扫码",
    "识别不了", "记分牌显示不出来",
]


def load_quick_phrases() -> list:
    """读取常用句列表（config/aftersale.json 持久化）。"""
    # 类型不对就回落到缺省句：手改 JSON 很容易把数组写成字符串，直接迭代会
    # 让下拉框出现单字候选项，这种静默错误比抛异常更难排查
    v = app_settings.get(_QPC_KEY)
    if isinstance(v, list):
        # 过滤空白项：手改配置后常留下空串，直接进下拉会出现空白候选项
        return [str(x) for x in v if str(x).strip()]
    return list(_QPC_DEFAULT)


def add_quick_phrase(text: str) -> list:
    """新增常用句，写回配置，返回更新后的列表。"""
    phrases = load_quick_phrases()
    t = str(text or "").strip()
    if t and t not in phrases:
        # 新句插到最前：最近用过的排前面，减少滚动查找
        phrases.insert(0, t)
    app_settings.set(_QPC_KEY, phrases)
    return phrases


# ==================== 上次填写记忆 ====================

_LAST_CREATOR_KEY = "aftersale_last_creator"
_LAST_RESOLVER_KEY = "aftersale_last_resolver"


def load_settings_str(key: str) -> str:
    """读取配置中指定字符串键（非空字符串返回，否则空串）。"""
    v = app_settings.get(key)
    if isinstance(v, str) and v.strip():
        return v.strip()
    return ""


def load_last_creator() -> str:
    """上次填写的填写人（config/aftersale.json，无则空串）。"""
    return load_settings_str(_LAST_CREATOR_KEY)


def load_last_resolver() -> str:
    """上次填写的解决人（config/aftersale.json，无则空串）。"""
    return load_settings_str(_LAST_RESOLVER_KEY)


def save_last_people(creator: str, resolver: str) -> None:
    """记住本次填写的填写人/解决人，供下次打开面板恢复；空值不写入。

    只写非空的一侧：用户清空一个输入框通常只是想换另一个，两侧一起覆盖
    会把另一个已有记忆顺手抹掉。
    """
    creator = (creator or "").strip()
    resolver = (resolver or "").strip()
    if not creator and not resolver:
        return
    # 只写非空的一侧：清空其中一个输入框时不应把另一个已有记忆也抹掉
    if creator:
        app_settings.set(_LAST_CREATOR_KEY, creator)
    if resolver:
        app_settings.set(_LAST_RESOLVER_KEY, resolver)


# ==================== 记住上次发生日期 ====================

_REMEMBER_OCCURRED_KEY = "aftersale_remember_occurred"
_LAST_OCCURRED_KEY = "aftersale_last_occurred"


def remember_occurred_enabled() -> bool:
    """「记住上次发生日期」开关（config/aftersale.json，缺省开启）。"""
    return bool(app_settings.get(_REMEMBER_OCCURRED_KEY, True))


def load_last_occurred() -> str:
    """上次新增记录的发生日期（yyyy-MM-dd）；开关关闭或无记录返回空串。"""
    if not remember_occurred_enabled():
        return ""
    return load_settings_str(_LAST_OCCURRED_KEY)


def save_last_occurred(occurred: str) -> None:
    """记住本次新增记录的发生日期（开关关闭 / 空值时不写）。"""
    occurred = (occurred or "").strip()
    if not occurred or not remember_occurred_enabled():
        return
    app_settings.set(_LAST_OCCURRED_KEY, occurred)


def set_remember_occurred(enabled: bool) -> None:
    """写「记住上次发生日期」开关；关闭时清除已记住日期。"""
    app_settings.set(_REMEMBER_OCCURRED_KEY, bool(enabled))
    if not enabled:
        # 关闭即清记忆：否则重新开启时会突然带出一个很久以前的日期
        app_settings.remove(_LAST_OCCURRED_KEY)


# ==================== 记录自动刷新 ====================

_AUTO_REFRESH_KEY = "aftersale_auto_refresh"
_AUTO_REFRESH_INTERVAL_KEY = "aftersale_auto_refresh_interval"


def auto_refresh_enabled() -> bool:
    """「自动刷新记录」开关（config/aftersale.json，缺省关闭）。"""
    return bool(app_settings.get(_AUTO_REFRESH_KEY, False))


def auto_refresh_interval() -> int:
    """自动刷新间隔秒数（缺省 30；仅接受 ≥5 的整数）。"""
    # 落盘值可能被手改成 1，写入端的钳制管不到已经写进配置的数字，这里再钳一次
    try:
        v = int(app_settings.get(_AUTO_REFRESH_INTERVAL_KEY, 30))
    except (TypeError, ValueError):
        return 30
    return v if v >= 5 else 30


def set_auto_refresh(enabled: bool) -> None:
    """写「自动刷新记录」开关。"""
    app_settings.set(_AUTO_REFRESH_KEY, bool(enabled))


def set_auto_refresh_interval(seconds: int) -> None:
    """写自动刷新间隔秒数（<5 秒的入参按 5 秒下限钳制；非法入参不写）。

    非法入参直接忽略而不是回落默认值：调用方多是输入框信号，空串或负数是
    编辑过程中的瞬时状态，写默认值会覆盖用户原本的设置。
    """
    try:
        v = int(seconds)
    except (TypeError, ValueError):
        return
    # 5 秒下限是硬约束：间隔再短会让刷新线程持续压在数据库上，
    # 面板自身也会因为反复重查而卡顿
    app_settings.set(_AUTO_REFRESH_INTERVAL_KEY, max(5, v))
