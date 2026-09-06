# -*- coding: utf-8 -*-
"""
AutoWork Fluent Design Logo Generator
-------------------------------------
母题：抽象字母 A —— 双斜笔画 + 负空间横杠（从参考图标继承的"圆内留白"事实）
构图：圆角方形容器（Windows 11 应用图标壳）+ 居中标记
色板：#0078D4 -> #50B6E8 蓝渐变 + 白色标记 + 柔和层影（Fluent 层次）
输出：1024/512/256/48/32/16 PNG（透明背景）+ 多尺寸 ICO
"""
import math
import os
from PIL import Image, ImageDraw, ImageFilter

OUT = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 参数（0-100 设计坐标）
S = 8192                      # 超采样边长（1024 的 8 倍，保证曲线与斜边绝对平滑）
RADIUS = 22.5                 # 容器圆角半径（占边长百分比，Fluent 风格）
CORNER_R = 0.225 * S          # 像素值

# Fluent 蓝渐变（对角：左上浅 -> 右下深，模拟 Win11 图标光感）
GRAD_TOP = (96, 188, 240)     # 接近 #50B6E8，稍提亮
GRAD_BOTTOM = (0, 96, 176)    # 接近 #0078D4，稍压深
AXIS_COLOR = (13, 120, 196)   # 中轴色 #0D78C4（用于渐变插值参考）

# 字母 A 几何（0-100 坐标，footprint 约 56 x 50，居中偏上）
APEX_Y = 21.0                 # 顶点 y
BASE_Y = 79.0                 # 底边 y
LEG_W_TOP = 11.0              # 斜笔画顶部宽度
LEG_W_BASE = 16.5             # 斜笔画底部宽度（微喇叭，视觉稳定）
LEFT_TOP_X = 44.5             # 左笔画顶点中心 x
RIGHT_TOP_X = 55.5            # 右笔画顶点中心 x
LEFT_BASE_CX = 24.5           # 左笔画底边中心 x
RIGHT_BASE_CX = 75.5          # 右笔画底边中心 x
BAR_Y0, BAR_Y1 = 57.0, 68.0   # 横杠（负空间）y 区间
BAR_INSET = -2.0              # 负值：横杠两端嵌入斜笔画，连成闭合 A


def px(v):
    return v / 100.0 * S


def rounded_square_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def diagonal_gradient(size, c_top, c_bottom):
    """左上 -> 右下对角渐变"""
    g = Image.new("RGB", (2, 2))
    # 先做竖向渐变再旋转 45 度采样：直接逐像素太慢，用 numpy
    import numpy as np
    yy, xx = np.mgrid[0:size, 0:size]
    t = (xx + yy) / (2.0 * (size - 1))       # 0 at top-left, 1 at bottom-right
    t = t[..., None].astype("float32")
    top = np.array(c_top, dtype="float32")
    bot = np.array(c_bottom, dtype="float32")
    arr = top[None, None, :] * (1 - t) + bot[None, None, :] * t
    return Image.fromarray(arr.astype("uint8"), "RGB")


def build_logo(size_px=S):
    scale = size_px / S
    img = Image.new("RGBA", (size_px, size_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ---- 1. 容器：圆角方形 + 对角蓝渐变
    mask = rounded_square_mask(size_px, CORNER_R * scale)
    grad = diagonal_gradient(size_px, GRAD_TOP, GRAD_BOTTOM).convert("RGBA")
    img.paste(grad, (0, 0), mask)

    # ---- 2. Fluent 层次：顶部玻璃高光（沿圆角上缘的柔和白色渐变带）
    gloss = Image.new("L", (size_px, size_px), 0)
    gd = ImageDraw.Draw(gloss)
    gd.rounded_rectangle([0, 0, size_px - 1, size_px - 1],
                         radius=CORNER_R * scale, fill=255)
    fade = Image.new("L", (size_px, size_px), 0)
    fd = ImageDraw.Draw(fade)
    band_h = int(size_px * 0.30)
    for y in range(band_h):
        a = int(26 * (1 - y / band_h) ** 2)   # 上缘最亮，二次衰减
        fd.line([(0, y), (size_px, y)], fill=a)
    gloss = Image.composite(fade, Image.new("L", (size_px, size_px), 0), gloss)
    white = Image.new("RGBA", (size_px, size_px), (255, 255, 255, 0))
    white.putalpha(gloss)
    img.alpha_composite(white)

    # ---- 3. 标记阴影层（柔和深蓝，向下偏移，Fluent 轻微层次）
    shadow = Image.new("L", (size_px, size_px), 0)
    sd = ImageDraw.Draw(shadow)
    off = 1.6 * scale * S / 100 * 1.0
    draw_mark(sd, scale, dx=0.9, dy=1.7, color=255)
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=0.011 * size_px))
    sh_rgba = Image.new("RGBA", (size_px, size_px), (4, 42, 79, 0))
    sh_rgba.putalpha(Image.eval(shadow, lambda v: int(v * 0.55)))
    img.alpha_composite(Image.composite(sh_rgba, Image.new("RGBA", sh_rgba.size, (0, 0, 0, 0)), mask))

    # ---- 4. 字母 A 主体（白色，横杠为负空间）
    draw_mark(draw, scale, dx=0, dy=0, color=None, on_img=img, mask=mask)
    return img


def mark_polygons():
    """返回字母 A 的两个斜笔画多边形（0-100 坐标）+ 负空间横杠多边形"""
    # 左笔画：顶点梯形 -> 底边梯形
    left = [
        (LEFT_TOP_X - LEG_W_TOP / 2, APEX_Y),
        (LEFT_TOP_X + LEG_W_TOP / 2, APEX_Y),
        (RIGHT_BASE_CX * 0 + LEFT_BASE_CX + LEG_W_BASE / 2, BASE_Y),
        (LEFT_BASE_CX - LEG_W_BASE / 2, BASE_Y),
    ]
    right = [
        (RIGHT_TOP_X - LEG_W_TOP / 2, APEX_Y),
        (RIGHT_TOP_X + LEG_W_TOP / 2, APEX_Y),
        (RIGHT_BASE_CX + LEG_W_BASE / 2, BASE_Y),
        (RIGHT_BASE_CX - LEG_W_BASE / 2, BASE_Y),
    ]
    # 顶点帽：把两条斜边在顶部缝合成整体 A
    cap = [
        (LEFT_TOP_X - LEG_W_TOP / 2, APEX_Y),
        (RIGHT_TOP_X + LEG_W_TOP / 2, APEX_Y),
        (RIGHT_TOP_X + LEG_W_TOP / 2, APEX_Y + 6.5),
        (LEFT_TOP_X - LEG_W_TOP / 2, APEX_Y + 6.5),
    ]
    # 负空间横杠：两笔画内缘之间、BAR_Y0..BAR_Y1 的梯形
    # 内缘 x 随 y 线性插值
    def inner_x_left(y):
        # 左笔画内缘：从 (LEFT_TOP_X + LEG_W_TOP/2, APEX_Y) 到 (LEFT_BASE_CX + LEG_W_BASE/2, BASE_Y)
        t = (y - APEX_Y) / (BASE_Y - APEX_Y)
        return (LEFT_TOP_X + LEG_W_TOP / 2) * (1 - t) + (LEFT_BASE_CX + LEG_W_BASE / 2) * t

    def inner_x_right(y):
        t = (y - APEX_Y) / (BASE_Y - APEX_Y)
        return (RIGHT_TOP_X - LEG_W_TOP / 2) * (1 - t) + (RIGHT_BASE_CX - LEG_W_BASE / 2) * t

    bar = [
        (inner_x_left(BAR_Y0) + BAR_INSET, BAR_Y0),
        (inner_x_right(BAR_Y0) - BAR_INSET, BAR_Y0),
        (inner_x_right(BAR_Y1) - BAR_INSET, BAR_Y1),
        (inner_x_left(BAR_Y1) + BAR_INSET, BAR_Y1),
    ]
    return [left, right, cap], bar


def draw_mark(draw, scale, dx=0, dy=0, color=255, on_img=None, mask=None):
    polys, bar = mark_polygons()
    if color is not None:  # 阴影通道：画进 L 图
        for p in polys:
            draw.polygon([(px(x + dx) * scale, px(y + dy) * scale) for x, y in p], fill=color)
        return
    # 主体：白色双笔画 + 白色横杠；横杠上方两腿之间的蓝色三角即字腔（负空间），
    # 构成清晰可辨的字母 A。
    layer = Image.new("L", on_img.size, 0)
    ld = ImageDraw.Draw(layer)
    for p in polys:
        ld.polygon([(px(x) * scale, px(y) * scale) for x, y in p], fill=255)
    bx = [(px(x) * scale, px(y) * scale) for x, y in bar]
    ld.polygon(bx, fill=255)
    white = Image.new("RGBA", on_img.size, (255, 255, 255, 0))
    white.putalpha(layer)
    # 标记只允许出现在容器内
    if mask is not None:
        layer = Image.composite(layer, Image.new("L", layer.size, 0), mask)
        white.putalpha(layer)
    on_img.alpha_composite(white)


def downscale(img, target):
    return img.resize((target, target), Image.LANCZOS)


def main():
    print("rendering master at", S, "...")
    master = build_logo(S)

    sizes = [1024, 512, 256, 48, 32, 16]
    outs = {}
    for t in sizes:
        im = downscale(master, t)
        path = os.path.join(OUT, f"autowork_logo_{t}.png")
        im.save(path)
        outs[t] = im
        print("saved", path)

    # 1024 版同时作为主交付文件名
    master_small = downscale(master, 1024)
    master_small.save(os.path.join(OUT, "autowork_logo.png"))

    # ICO：16/32/48/256
    ico_path = os.path.join(OUT, "autowork_logo.ico")
    outs[256].save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (256, 256)],
        append_images=[outs[16], outs[32], outs[48]],
    )
    print("saved", ico_path)

    # 小尺寸专用变体：16/32 用略粗笔画版本会更稳，这里先输出通用版供评审
    print("done.")


if __name__ == "__main__":
    main()
