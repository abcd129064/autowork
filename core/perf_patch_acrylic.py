# -*- coding: utf-8 -*-
"""亚克力模糊降采样补丁（B1 patch 层第二片）。

从 core/perf.py 下沉：AcrylicBrush.blurPicSize 未显式设置时 clamp 到
(450,450)，避免主线程全分辨率高斯模糊。不依赖 core.perf / 配置。

只改采样尺寸不改模糊算法：模糊结果最终由 Qt 缩放回控件大小，450 已接近
视觉饱和，再往上加像素只是把成本线性堆在主线程上。
"""


def apply_acrylic_downsample_patch() -> None:
    """安装 AcrylicBrush.setImage 降采样补丁（幂等）。

    亚克力背景模糊走的是主线程高斯模糊，成本随像素数平方增长。
    原实 blurPicSize 为 None 时按原图分辨率计算，一张 4K 截图就足以
    卡住界面；这里强制降到 450x450 再模糊，视觉上仍是柔化背景。
    """
    try:
        from qfluentwidgets.components.widgets.acrylic_label import AcrylicBrush
    except Exception:
        # 组件路径在库升级后可能变化，拿不到就不打补丁，不影响功能
        return
    # 标记写在类上：模块可能被不同入口重复导入，实例级标记挡不住二次包裹
    if getattr(AcrylicBrush, "_perf_blur_patched", False):
        return
    # 先抓住原函数再绑定新实现，否则闭包里的自引用会一路递归到栈溢出
    orig_set_image = AcrylicBrush.setImage

    def _setImage(self, image):
        try:
            # 只补 None：调用方显式设过尺寸说明它已权衡过画质与开销，覆盖会改掉别人的决定
            if self.blurPicSize is None:
                self.blurPicSize = (450, 450)
        except Exception:
            # 实例被 C++ 侧提前析构时属性访问会抛，忽略即可
            pass
        # 尺寸就位后仍走原实现：缓存键与重绘时机都由它决定，自己重写必然出视觉回归
        return orig_set_image(self, image)

    AcrylicBrush.setImage = _setImage
    # 置位必须在替换之后，赋值失败时不能留下「已打补丁」的假标记
    AcrylicBrush._perf_blur_patched = True
