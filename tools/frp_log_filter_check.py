# -*- coding: utf-8 -*-
"""验证首页日志工作台「排除 frp 日志」过滤逻辑（离线，无需 Qt 窗口）。

用 stub self 直接调用 MainWindow._append_log 未绑定方法，覆盖：
1. 开关关闭 -> [frpc]/[远程会话] 照常入缓冲
2. 开关开启 -> 两类前缀被丢弃
3. 开关开启 -> 普通日志与 [警告] 等不受影响
4. 缓存尚未建立（构造早期）-> 走 _load_settings 兜底，不崩溃
"""
import sys
import types

sys.path.insert(0, ".")


class FakeTimer:
    def __init__(self):
        self.started = 0

    def isActive(self):
        return self.started > 0

    def start(self):
        self.started += 1


def make_self(exclude, with_cache=True):
    from main_window.main_window import MainWindow
    s = types.SimpleNamespace()
    s._FRP_LOG_PREFIXES = MainWindow._FRP_LOG_PREFIXES
    s._log_batch_buf = []
    s._log_batch_timer = FakeTimer()
    if with_cache:
        s._settings_cache = {"home_log_exclude_frp": exclude}
    else:
        s._settings_cache = {}
        s._load_settings = lambda: {"home_log_exclude_frp": exclude}
    return s


def main():
    from main_window.main_window import MainWindow
    fn = MainWindow._append_log
    cases = [
        ("关闭-frpc入缓冲", make_self(False), "[frpc] start error", True),
        ("关闭-会话入缓冲", make_self(False), "[远程会话] frpc 已退出", True),
        ("开启-frpc丢弃", make_self(True), "[frpc] proxy added", False),
        ("开启-会话丢弃", make_self(True), "[远程会话] 预检拦截", False),
        ("开启-普通日志保留", make_self(True), "视频处理完成", True),
        ("开启-警告保留", make_self(True), "[警告] 保存配置失败", True),
        ("无缓存兜底-丢弃", make_self(True, with_cache=False),
         "[frpc] boot", False),
        ("无缓存兜底-保留", make_self(False, with_cache=False),
         "[frpc] boot", True),
    ]
    fails = 0
    for name, stub, text, expect_in in cases:
        fn(stub, text)
        got = bool(stub._log_batch_buf)
        ok = got == expect_in
        print("%s %s" % ("PASS" if ok else "FAIL", name))
        fails += (not ok)
    print("TOTAL %d/%d PASS" % (len(cases) - fails, len(cases)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
