# -*- coding: utf-8 -*-
"""生成 AutoWork 应用图标。

设计：青色渐变圆角方块背景 + 白色齿轮 + 闪电（自动化/远程工作主题）。
输出：app_icon.png（512x512）、app_icon.ico（多尺寸，供 PyInstaller 与窗口图标使用）。

用法：python make_icon.py
"""
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.abspath(__file__))
SIZE = 512  # 基准绘制尺寸

# 主题渐变色（与 setThemeColor("#00BCD4") 呼应）
COLOR_TOP = (0, 229, 255)      # 亮青色
COLOR_BOTTOM = (0, 131, 143)   # 深青色


def make_gradient(size, top, bottom):
    """生成竖直线性渐变 RGB 图像"""
    try:
        import numpy as np
        t = np.linspace(0.0, 1.0, size, dtype=np.float32).reshape(-1, 1)
        top_arr = np.array(top, dtype=np.float32)
        bot_arr = np.array(bottom, dtype=np.float32)
        arr = top_arr * (1 - t) + bot_arr * t           # (size, 3)
        arr = np.repeat(arr[:, None, :], size, axis=1)   # (size, size, 3)
        return Image.fromarray(arr.astype(np.uint8), 'RGB')
    except ImportError:
        img = Image.new('RGB', (size, size))
        draw = ImageDraw.Draw(img)
        for y in range(size):
            k = y / max(1, size - 1)
            c = tuple(int(top[i] * (1 - k) + bottom[i] * k) for i in range(3))
            draw.line([(0, y), (size, y)], fill=c)
        return img


def draw_gear(layer, cx, cy, r_out, r_teeth, r_hole, n_teeth, fill):
    """在透明图层上绘制齿轮（圆环 + 轮齿 + 中心孔）"""
    draw = ImageDraw.Draw(layer)
    tooth_w = 2 * 3.14159 * r_out / n_teeth * 0.45  # 齿宽（弧长的 45%）
    tooth_h = r_teeth - r_out + r_out * 0.08
    # 单个齿：在正上方绘制圆角矩形，随后旋转复制
    tooth = Image.new('RGBA', layer.size, (0, 0, 0, 0))
    td = ImageDraw.Draw(tooth)
    x0, x1 = cx - tooth_w / 2, cx + tooth_w / 2
    y0, y1 = cy - r_teeth, cy - r_teeth + tooth_h
    td.rounded_rectangle([x0, y0, x1, y1], radius=tooth_w * 0.3, fill=fill)
    for i in range(n_teeth):
        layer.alpha_composite(tooth.rotate(-i * 360 / n_teeth, resample=Image.BICUBIC))
    draw = ImageDraw.Draw(layer)
    # 齿轮主体圆环
    draw.ellipse([cx - r_out, cy - r_out, cx + r_out, cy + r_out], fill=fill)
    # 中心孔（透明）
    draw.ellipse([cx - r_hole, cy - r_hole, cx + r_hole, cy + r_hole], fill=(0, 0, 0, 0))


def draw_bolt(layer, cx, cy, h, fill):
    """绘制闪电图形（高度 h，居中于 cx, cy）"""
    draw = ImageDraw.Draw(layer)
    w = h * 0.62
    pts = [
        (cx + w * 0.10, cy - h * 0.50),
        (cx - w * 0.42, cy + h * 0.10),
        (cx - w * 0.04, cy + h * 0.10),
        (cx - w * 0.18, cy + h * 0.50),
        (cx + w * 0.42, cy - h * 0.12),
        (cx + w * 0.02, cy - h * 0.12),
    ]
    draw.polygon(pts, fill=fill)


def build_icon(size=SIZE):
    """合成完整图标：渐变底 + 圆角遮罩 + 投影齿轮 + 白色齿轮闪电"""
    img = make_gradient(size, COLOR_TOP, COLOR_BOTTOM).convert('RGBA')

    # 圆角遮罩
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * 0.225), fill=255)
    out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)

    c = size / 2
    # 轻微投影，增强层次感
    shadow = Image.new('RGBA', out.size, (0, 0, 0, 0))
    draw_gear(shadow, c + size * 0.008, c + size * 0.018,
              size * 0.30, size * 0.385, size * 0.185, 8, (0, 40, 50, 70))
    out.alpha_composite(shadow)

    # 白色齿轮 + 闪电
    fg = Image.new('RGBA', out.size, (0, 0, 0, 0))
    draw_gear(fg, c, c, size * 0.30, size * 0.385, size * 0.185, 8, (255, 255, 255, 255))
    draw_bolt(fg, c, c, size * 0.30, (255, 255, 255, 255))
    out.alpha_composite(fg)
    return out


def main():
    """生成并落盘 app_icon.png（512）与 app_icon.ico（多尺寸）"""
    icon = build_icon()

    png_path = os.path.join(ROOT, 'app_icon.png')
    icon.save(png_path)
    print(f'[make_icon] 已生成 {png_path}')

    ico_path = os.path.join(ROOT, 'app_icon.ico')
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    icon.save(ico_path, sizes=[(s, s) for s in sizes])
    print(f'[make_icon] 已生成 {ico_path}（尺寸：{sizes}）')


if __name__ == '__main__':
    main()
