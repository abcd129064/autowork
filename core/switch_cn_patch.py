# -*- coding: utf-8 -*-
"""SwitchButton 状态文本中文化补丁。

qfluentwidgets SwitchButton 默认状态文本为英文 "On"/"Off"：源码
switch_button.py 的 _updateText 在 checked 翻转时用 onText/offText
覆盖 label 文本——逐实例 setOnText/setOffText 需改全项目十余处调用点。

本补丁 patch SwitchButton.__init__，构造后统一写入中文「开/关」，
对所有直接实例化处（含未来新增）一次性生效（幂等）。

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

    _orig_init = SwitchButton.__init__

    def _cn_init(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        # setOnText 内部触发 _updateText → label 按 checked 态刷新为中文
        self.setOnText("开")
        self.setOffText("关")

    SwitchButton.__init__ = _cn_init
    _patched = True
