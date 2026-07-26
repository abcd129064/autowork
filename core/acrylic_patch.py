# -*- coding: utf-8 -*-
"""亚克力效果 PIL 替代补丁。

qfluentwidgets 的 gaussianBlur 依赖 numpy + scipy（conda 下还需 Intel MKL DLL ~245MB），
PyInstaller 打包时若排除 numpy/scipy 则亚克力失效（isAcrylicAvailable=False）。

本模块在 numpy/scipy 不可用时，向 sys.modules 注入一个基于 PIL 的
qfluentwidgets.common.image_utils 替代实现，使亚克力磨砂效果在打包后仍正常工作。

用法：在 main.py 最顶部（qfluentwidgets 导入之前）执行：
    import core.acrylic_patch  # noqa: F401
"""

import sys
import types
import importlib.util


def _apply_patch():
    """检测 numpy/scipy 是否可用，不可用则注入 PIL 替代实现。

    使用 importlib.util.find_spec() 而非直接 import 来探测 numpy，
    避免触发 qfluentwidgets.common 包初始化链导致 acrylic_label.py
    提前将 isAcrylicAvailable 设为 False。
    """
    # find_spec 只查找模块是否存在，不执行任何导入（零副作用）
    if importlib.util.find_spec('numpy') is not None \
            and importlib.util.find_spec('scipy') is not None:
        return  # 开发环境（numpy/scipy 可用），使用真实实现，无需补丁

    # ---- 以下为打包环境（numpy/scipy 被 excludes 排除）----
    from PIL import Image, ImageFilter, ImageEnhance
    from PIL.ImageQt import fromqpixmap
    from PySide6.QtGui import QImage, QPixmap

    def gaussianBlur(image, blurRadius=18, brightFactor=1, blurPicSize=None):
        """PIL 实现的高斯模糊，接口与 qfluentwidgets.common.image_utils.gaussianBlur 一致。"""
        if isinstance(image, str) and not image.startswith(':'):
            pil_img = Image.open(image)
        elif isinstance(image, QPixmap):
            pil_img = fromqpixmap(image)
        elif isinstance(image, QImage):
            pil_img = fromqpixmap(QPixmap.fromImage(image))
        else:
            pil_img = fromqpixmap(QPixmap(image))

        if blurPicSize:
            w, h = pil_img.size
            ratio = min(blurPicSize[0] / w, blurPicSize[1] / h)
            if ratio < 1:
                pil_img = pil_img.resize((int(w * ratio), int(h * ratio)))

        # 转为 RGB（与原始 scipy 实现行为一致：仅处理 3 通道）
        pil_img = pil_img.convert('RGB')

        # PIL GaussianBlur 的 radius 参数等价于 scipy gaussian_filter 的 sigma
        pil_img = pil_img.filter(ImageFilter.GaussianBlur(radius=blurRadius))

        if brightFactor != 1:
            pil_img = ImageEnhance.Brightness(pil_img).enhance(brightFactor)

        # PIL Image → QPixmap
        data = pil_img.tobytes('raw', 'RGB')
        w, h = pil_img.size
        qimg = QImage(data, w, h, 3 * w, QImage.Format_RGB888)
        return QPixmap.fromImage(qimg)

    # 构造替代模块并注入 sys.modules（必须在 qfluentwidgets 导入前完成）
    mod = types.ModuleType('qfluentwidgets.common.image_utils')
    mod.gaussianBlur = gaussianBlur
    mod.__doc__ = 'PIL-based replacement (numpy/scipy unavailable)'
    sys.modules['qfluentwidgets.common.image_utils'] = mod


_apply_patch()
