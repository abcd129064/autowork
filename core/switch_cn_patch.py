# -*- coding: utf-8 -*-
"""SwitchButton 状态文本中文化补丁。

qfluentwidgets SwitchButton 默认状态文本为英文 "On"/"Off"：源码
switch_button.py 的 _updateText 在 checked 翻转时用 onText/offText
覆盖 label 文本——逐实例 setOnText/setOffText 需改全项目十余处调用点。

本补丁 patch SwitchButton.__init__，构造后统一写入中文「开/关」，
对所有直接实例化处（含未来新增）一次性生效（幂等）。

新版 qfluentwidgets 的 SwitchButton.__init__ 是 singledispatchmethod
描述符（按第一个构造参数的类型分派 parent/text 两个重载），因此补丁
必须保留描述符语义、按实例重新绑定后再调用，见下方 _orig 取法。

注意：patch 会在构造后覆盖初始 text（_updateText 按 checked 态刷新），
因此需要语义文本的开关（如「命中时弹通知」）应改用旁置标签承载语义，
SwitchButton 自身只作状态指示。

用法：main.py 在 qfluentwidgets 导入完成后调用（与 patch_table_hover_repaint
同区，幂等）：
    from core.switch_cn_patch import patch_switch_cn_text
    patch_switch_cn_text()
"""

_patched = False


def patch_switch_cn_text():
    """SwitchButton 状态文本统一改为中文「开/关」（幂等，重复调用无害）"""
    global _patched
    if _patched:
        return
    # 函数内导入：本模块必须可在 qfluentwidgets 导入前被 main.py 顶部
    # 引用而不提前触发 qfw 初始化（acrylic_patch 的 sys.modules 注入
    # 必须先于 qfw 完成）
    from qfluentwidgets.components.widgets.switch_button import SwitchButton

    # 取描述符本体，不能写 SwitchButton.__init__：类属性访问会触发
    # __get__(None, cls) 得到 obj=None 的闭包，补丁再手动把 self 作为第一个
    # 位置参数传进去，singledispatch 的分派键就成了实例自身的类型，永远命中
    # 默认的 parent 重载且实参整体错位——SwitchButton("", card) 里的 "" 落到
    # parent 上，QWidget.__init__ 直接 ValueError。
    _orig = SwitchButton.__dict__["__init__"]

    def _cn_init(self, *args, **kwargs):
        # 按实例绑定后 args 只剩用户实参，分派键才是第一个构造参数；
        # 旧版描述符若是普通函数，__get__ 同样给出绑定方法，两种实现都兼容
        _orig.__get__(self, type(self))(*args, **kwargs)
        # setOnText 内部触发 _updateText → label 按 checked 态刷新为中文
        self.setOnText("开")
        self.setOffText("关")

    SwitchButton.__init__ = _cn_init
    _patched = True
