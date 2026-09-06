# -*- coding: utf-8 -*-
"""
AutoWork Fluent Logo v2 —— M365 双层叠构风格（参考 Excel 图标构图语言）
构图：大圆角面板（浅蓝渐变）+ 右侧深脊 + 右上亮色折角 + 左下深蓝字母徽章(白 A)
输出：1024/512/256/48/32/16 PNG + 多尺寸 ICO + 缩放测试图
"""
import os
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

OUT = os.path.dirname(os.path.abspath(__file__))
S = 8192


def px(v):
    return v / 100.0 * S


# ---------------------------------------------------------------- 几何（0-100 坐标）
SPINE = (57, 11, 87, 89, 9)     # 右侧深脊
SHEET = (13, 11, 63, 89, 9)     # 主面板
TAB   = (57, 11, 87, 41, 9)     # 右上折角
BADGE = (9, 51, 47, 89, 10)     # 左下字母徽章

# 蓝色 Fluent 四档明度阶（映射参考图的绿系层级：折角最亮>面板>深脊>徽章最深）
C_SPINE = ((22, 96, 166), (7, 55, 108))      # #1660A6 -> #07376C 深脊
C_SHEET = ((134, 201, 242), (58, 148, 218))  # #86C9F2 -> #3A94DA 主面板
C_TAB   = ((214, 240, 252), (146, 208, 246)) # #D6F0FC -> #92D0F6 折角（最亮）
C_BADGE = ((15, 98, 172), (5, 52, 104))      # #0F62AC -> #053468 徽章（最深）
SHADOW_RGB = (6, 40, 74)

# 字母 A 几何（与 v1 相同的母题，缩放进徽章）
APEX_Y, BASE_Y = 21.0, 79.0
LEG_W_TOP, LEG_W_BASE = 11.0, 16.5
LEFT_TOP_X, RIGHT_TOP_X = 44.5, 55.5
LEFT_BASE_CX, RIGHT_BASE_CX = 24.5, 75.5
BAR_Y0, BAR_Y1 = 57.0, 68.0
BAR_INSET = -2.0


def grad_img(w, h, c_top, c_bot, diag=True):
    yy, xx = np.mgrid[0:h, 0:w]
    t = ((xx + yy) / (w + h - 2.0)) if diag else (yy / max(h - 1, 1))
    t = t[..., None].astype("float32")
    a = (np.array(c_top, "float32")[None, None, :] * (1 - t)
         + np.array(c_bot, "float32")[None, None, :] * t)
    return Image.fromarray(a.astype("uint8"), "RGB").convert("RGBA")


def put_shape(img, box, colors, diag=True):
    x0, y0, x1, y1, r = box
    X0, Y0, X1, Y1 = int(px(x0)), int(px(y0)), int(px(x1)), int(px(y1))
    w, h = X1 - X0, Y1 - Y0
    g = grad_img(w, h, *colors, diag=diag)
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=int(px(r)), fill=255)
    g.putalpha(m)
    img.alpha_composite(g, (X0, Y0))


def put_shadow(img, box, dx=1.1, dy=1.7, blur=1.5, alpha=0.34):
    """形状剪影向右下偏移，仅落在已绘制像素上（透明背景不挂影）"""
    x0, y0, x1, y1, r = box
    sil = Image.new("L", img.size, 0)
    d = ImageDraw.Draw(sil)
    d.rounded_rectangle([px(x0 + dx), px(y0 + dy), px(x1 + dx), px(y1 + dy)],
                        radius=int(px(r)), fill=int(255 * alpha))
    sil = sil.filter(ImageFilter.GaussianBlur(px(blur)))
    a = img.split()[3]
    sil = Image.composite(sil, Image.new("L", sil.size, 0), a)
    sh = Image.new("RGBA", img.size, SHADOW_RGB + (0,))
    sh.putalpha(sil)
    img.alpha_composite(sh)


def mark_polys():
    left = [(LEFT_TOP_X - LEG_W_TOP / 2, APEX_Y), (LEFT_TOP_X + LEG_W_TOP / 2, APEX_Y),
            (LEFT_BASE_CX + LEG_W_BASE / 2, BASE_Y), (LEFT_BASE_CX - LEG_W_BASE / 2, BASE_Y)]
    right = [(RIGHT_TOP_X - LEG_W_TOP / 2, APEX_Y), (RIGHT_TOP_X + LEG_W_TOP / 2, APEX_Y),
             (RIGHT_BASE_CX + LEG_W_BASE / 2, BASE_Y), (RIGHT_BASE_CX - LEG_W_BASE / 2, BASE_Y)]
    cap = [(LEFT_TOP_X - LEG_W_TOP / 2, APEX_Y), (RIGHT_TOP_X + LEG_W_TOP / 2, APEX_Y),
           (RIGHT_TOP_X + LEG_W_TOP / 2, APEX_Y + 6.5), (LEFT_TOP_X - LEG_W_TOP / 2, APEX_Y + 6.5)]

    def il(y):
        t = (y - APEX_Y) / (BASE_Y - APEX_Y)
        return (LEFT_TOP_X + LEG_W_TOP / 2) * (1 - t) + (LEFT_BASE_CX + LEG_W_BASE / 2) * t

    def ir(y):
        t = (y - APEX_Y) / (BASE_Y - APEX_Y)
        return (RIGHT_TOP_X - LEG_W_TOP / 2) * (1 - t) + (RIGHT_BASE_CX - LEG_W_BASE / 2) * t

    bar = [(il(BAR_Y0) + BAR_INSET, BAR_Y0), (ir(BAR_Y0) - BAR_INSET, BAR_Y0),
           (ir(BAR_Y1) - BAR_INSET, BAR_Y1), (il(BAR_Y1) + BAR_INSET, BAR_Y1)]
    return [left, right, cap, bar]


def put_letter(img, badge, pad_x=7.5, pad_y=7.0):
    """把字母 A 母题等比缩放进徽章中心"""
    bx0, by0, bx1, by1, _ = badge
    cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
    mw, mh = (RIGHT_BASE_CX + LEG_W_BASE / 2) - (LEFT_BASE_CX - LEG_W_BASE / 2), BASE_Y - APEX_Y
    avail_w, avail_h = (bx1 - bx0) - 2 * pad_x, (by1 - by0) - 2 * pad_y
    s = min(avail_w / mw, avail_h / mh)
    layer = Image.new("L", img.size, 0)
    d = ImageDraw.Draw(layer)
    for poly in mark_polys():
        pts = []
        for x, y in poly:
            X = cx + (x - 50.0) * s
            Y = cy + (y - (APEX_Y + BASE_Y) / 2) * s
            pts.append((px(X), px(Y)))
        d.polygon(pts, fill=255)
    white = Image.new("RGBA", img.size, (255, 255, 255, 0))
    white.putalpha(layer)
    img.alpha_composite(white)


def build():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    put_shape(img, SPINE, C_SPINE, diag=False)          # 1 深脊
    put_shadow(img, SHEET)                              # 2 面板投影落在脊上
    put_shape(img, SHEET, C_SHEET, diag=False)          # 3 主面板
    put_shadow(img, TAB, dx=0.8, dy=1.2, blur=1.2, alpha=0.30)
    put_shape(img, TAB, C_TAB, diag=True)               # 4 折角
    put_shadow(img, BADGE, dx=1.3, dy=1.9, blur=1.8, alpha=0.40)
    put_shape(img, BADGE, C_BADGE, diag=True)           # 5 徽章
    put_letter(img, BADGE)                              # 6 白色 A
    return img


def main():
    print("rendering v2 master...")
    master = build()
    outs = {}
    for t in [1024, 512, 256, 48, 32, 16]:
        im = master.resize((t, t), Image.LANCZOS)
        im.save(os.path.join(OUT, f"autowork_logo2_{t}.png"))
        outs[t] = im
        print("saved autowork_logo2_%d.png" % t)
    outs[256].save(os.path.join(OUT, "autowork_logo2.ico"), format="ICO",
                   sizes=[(16, 16), (32, 32), (48, 48), (256, 256)],
                   append_images=[outs[16], outs[32], outs[48]])
    print("saved autowork_logo2.ico")

    # 缩放测试（浅/深底）
    for bg, name, tc in [((243, 243, 243, 255), "scale_test2_light.png", (60, 60, 60)),
                         ((32, 32, 32, 255), "scale_test2_dark.png", (220, 220, 220))]:
        sizes = [16, 24, 32, 48, 64, 128]
        pad, gap = 28, 24
        w = pad * 2 + sum(sizes) + gap * (len(sizes) - 1)
        sheet = Image.new("RGBA", (w, 128 + pad * 2 + 30), bg)
        d = ImageDraw.Draw(sheet)
        x = pad
        for s in sizes:
            im = master.resize((s, s), Image.LANCZOS)
            sheet.alpha_composite(im, (x, pad + (128 - s) // 2))
            d.text((x, pad + 136), f"{s}px", fill=tc)
            x += s + gap
        sheet.convert("RGB").save(os.path.join(OUT, name))
        print("saved", name)
    print("done.")


if __name__ == "__main__":
    main()
