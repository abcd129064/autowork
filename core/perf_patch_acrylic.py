# -*- coding: utf-8 -*-
"""亚克力模糊降采样补丁（B1 patch 层第二片）。

从 core/perf.py 下沉：AcrylicBrush.blurPicSize 未显式设置时 clamp 到
(450,450)，避免主线程全分辨率高斯模糊。不依赖 core.perf / 配置。
"""


def apply_acrylic_downsample_patch() -> None:
    """安装 AcrylicBrush.setImage 降采样补丁（幂等）。"""
    try:
        from qfluentwidgets.components.widgets.acrylic_label import AcrylicBrush
    except Exception:
        return
    if getattr(AcrylicBrush, "_perf_blur_patched", False):
        return
    orig_set_image = AcrylicBrush.setImage

    def _setImage(self, image):
        try:
            if self.blurPicSize is None:
                self.blurPicSize = (450, 450)
        except Exception:
            pass
        return orig_set_image(self, image)

    AcrylicBrush.setImage = _setImage
    AcrylicBrush._perf_blur_patched = True
