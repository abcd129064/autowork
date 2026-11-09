# -*- coding: utf-8 -*-
"""SwitchButton 中文补丁 × qfluentwidgets 重载构造器 的 offscreen 冒烟。

复现：新版 qfluentwidgets 的 SwitchButton.__init__ 是 singledispatchmethod
描述符（按第一个构造参数的类型分派 parent / text 两个重载）。core 的中文补丁
若把它当普通函数取用（`SwitchButton.__init__`）再把 self 作第一个位置参数转发，
分派键就变成实例自身类型，永远命中 parent 重载且实参整体错位一位——
`SwitchButton("", card)` 的 "" 落到 parent 上，QWidget.__init__ 直接
ValueError，主窗口在 工具页→视频增强→FilterPage 构造开关时崩掉。

本脚本验证修复后的补丁在几种构造写法下都不错位，并留一段旧写法对照，
证明失败模式确实是描述符绑定问题而非环境。

运行（需 PySide6 + qfluentwidgets 全依赖解释器）：
    QT_QPA_PLATFORM=offscreen python tools/smoke/smoke_switch_cn_overload.py
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# ---- Qt DLL 引导（同 windows/aftersale_panel.py 顶部，规避 conda Qt 冲突） ----
import importlib.util as _iu
try:
    _spec = _iu.find_spec('PySide6')
    if _spec is not None:
        for _d in (list(_spec.submodule_search_locations or []) +
                   [os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                                 'System32')]):
            if _d and os.path.isdir(_d):
                try:
                    os.add_dll_directory(_d)
                except OSError:
                    pass
except Exception:
    pass

from PySide6.QtWidgets import QApplication, QWidget    # noqa: E402
from qfluentwidgets import SwitchButton                 # noqa: E402
from qfluentwidgets.common.overload import singledispatchmethod  # noqa: E402

from core.switch_cn_patch import patch_switch_cn_text   # noqa: E402

# 必须在任何 patch 之前抓原始描述符：patch 会把同名属性换成补丁函数
_RAW_INIT = SwitchButton.__dict__["__init__"]

app = QApplication([])

# ---------- 1) 旧写法对照：确认失败模式确实是描述符绑定 ----------
if isinstance(_RAW_INIT, singledispatchmethod):
    broken = _RAW_INIT.__get__(None, SwitchButton)   # 等价于旧代码里的 SwitchButton.__init__
    try:
        # 模拟旧 _cn_init：把实例当作第一个位置参数转给已解绑的闭包
        broken(SwitchButton.__new__(SwitchButton), "", QWidget())
        print("[1] 旧写法未复现异常：库实现可能已变更，请复核补丁绑定方式")
    except ValueError as e:
        print(f"[1] 旧写法复现 ValueError：{str(e).splitlines()[0]}")
else:
    print(f"[1] 旧写法跳过：当前库的 __init__ 是 {type(_RAW_INIT).__name__}，不是描述符")

# ---------- 2) 修复后：各构造写法都要正确分派 ----------
patch_switch_cn_text()
patch_switch_cn_text()          # 幂等复跑，确认不会被二次包裹
first = SwitchButton.__dict__["__init__"]
patch_switch_cn_text()
assert SwitchButton.__dict__["__init__"] is first, "补丁被重复包裹"

card = QWidget()
cases = {
    "SwitchButton()": (lambda: SwitchButton(), None),
    "SwitchButton(parent)": (lambda: SwitchButton(card), card),
    'SwitchButton("", parent)': (lambda: SwitchButton("", card), card),
    'SwitchButton("开", parent)': (lambda: SwitchButton("开", card), card),
    'SwitchButton(text=, parent=)': (lambda: SwitchButton(text="开", parent=card), card),
}

for name, (factory, expect_parent) in cases.items():
    sw = factory()
    # 断言 parent 归属比断言文本更能定位问题：错位时 parent 会拿到 str 并抛错
    assert sw.parent() is expect_parent, \
        f"{name} parent 归属错误：{sw.parent()!r} != {expect_parent!r}"
    sw.setChecked(True)
    on_text = sw.getText()
    sw.setChecked(False)
    off_text = sw.getText()
    # 语义文本由调用方随后覆盖（如视频增强页把开关文本清空、语义交给旁置标签），
    # 补丁只保证「翻转后拿得到中文默认值」这条链路不断
    assert on_text == "开" and off_text == "关", \
        f"{name} 状态文本未中文化：on={on_text!r} off={off_text!r}"
    print(f"[2] {name}: parent_ok on={on_text!r} off={off_text!r}")

print("SMOKE_OK forms=%d cn_text=开/关" % len(cases))
sys.stdout.flush()   # 下面走 os._exit，不 flush 重定向到文件时缓冲区里的判定行会全丢

# offscreen 收尾：直接退出解释器时 Qt 静态析构偶发 0xC0000005
import gc                                                        # noqa: E402
card.close()
del card, sw
gc.collect()
os._exit(0)
