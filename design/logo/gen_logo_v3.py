# -*- coding: utf-8 -*-
"""
AutoWork Fluent Logo v3 —— 炫彩蓝渐变版
构图沿用 v2 的 M365 双层叠构（主面板 + 右上折角 + 深脊 + 字母徽章），
色板升级为青→蓝→紫罗兰的炫彩色相位移渐变（Copilot 系蓝紫气质），仍保持四档明度阶。
"""
import os
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

OUT = os.path.dirname(os.path.abspath(__file__))
S = 8192


def px(v):
    return v / 100.0 * S


# ---------------------------------------------------------------- 几何（与 v2 相同）
SPINE = (57, 11, 87, 89, 9)
SHEET = (13, 11, 63, 89, 9)
TAB   = (57, 11, 87, 41, 9)
BADGE = (9, 51, 47, 89, 10)

# 炫彩蓝：每档做"色相位移"渐变（起点色 -> 终点色），整体青→蓝→紫罗兰流动
C_SPINE = ((56, 84, 224), (74, 42, 176))     # 深脊：宝蓝 -> 靛紫 #3854E0 -> #4A2AB0
C_SHEET = ((64, 200, 255), (48, 108, 240))   # 主面板：亮青 -> 蓝 #40C8FF -> #306CF0
C_TAB   = ((150, 236, 255), (106, 168, 255)) # 折角：冰青 -> 天蓝 #96ECFF -> #6AA8FF（最亮）
C_BADGE = ((76, 64, 232), (30, 40, 150))     # 徽章：紫罗兰 -> 深蓝 #4C40E8 -> #1E2896
SHADOW_RGB = (24, 16, 74)

APEX_Y, BASE_Y = 21.0, 79.0
LEG_W_TOP, LEG_W_BASE = 11.0, 16.5
LEFT_TOP_X, RIGHT_TOP_X = 44.5, 55.5
LEFT_BASE_CX, RIGHT_BASE_CX = 24.5, 75.5
BAR_Y0, BAR_Y1 = 57.0, 68.0
BAR_INSET = -2.0


def grad_img3(w, h, c_top, c_bot, mode="diag"):
    """HSV 空间插值（保留炫彩色相位移，避免 RGB 直线插值发灰），手写向量化无第三方依赖"""
    yy, xx = np.mgrid[0:h, 0:w]
    if mode == "diag":
        t = (xx + yy) / (w + h - 2.0)
    elif mode == "v":
        t = yy / max(h - 1, 1)
    else:
        t = xx / max(w - 1, 1) * 0.65 + yy / max(h - 1, 1) * 0.35
    t = np.clip(t, 0, 1).astype("float32")
    import colorsys
    h1, s1, v1 = colorsys.rgb_to_hsv(*[c / 255.0 for c in c_top])
    h2, s2, v2 = colorsys.rgb_to_hsv(*[c / 255.0 for c in c_bot])
    if abs(h2 - h1) > 0.5:
        if h2 > h1:
            h1 += 1.0
        else:
            h2 += 1.0
    hh = np.mod(h1 + (h2 - h1) * t, 1.0)
    ss = s1 + (s2 - s1) * t
    vv = v1 + (v2 - v1) * t
    i = np.floor(hh * 6.0).astype("int32") % 6
    f = hh * 6.0 - np.floor(hh * 6.0)
    p = vv * (1 - ss)
    q = vv * (1 - f * ss)
    r_ = vv * (1 - (1 - f) * ss)
    z = np.zeros_like(t)
    o = np.ones_like(t)
    r = np.where(i == 0, vv, np.where(i == 1, q, np.where(i == 2, p, np.where(i == 3, p, np.where(i == 4, r_, vv)))))
    g = np.where(i == 0, r_, np.where(i == 1, vv, np.where(i == 2, vv, np.where(i == 3, q, np.where(i == 4, p, p)))))
    b = np.where(i == 0, p, np.where(i == 1, p, np.where(i == 2, r_, np.where(i == 3, vv, np.where(i == 4, vv, q)))))
    arr = np.stack([r, g, b], axis=-1)
    return Image.fromarray((arr * 255).astype("uint8"), "RGB").convert("RGBA")


def put_shape(img, box, colors, mode="diag"):
    x0, y0, x1, y1, r = box
    X0, Y0, X1, Y1 = int(px(x0)), int(px(y0)), int(px(x1)), int(px(y1))
    w, h = X1 - X0, Y1 - Y0
    g = grad_img3(w, h, *colors, mode=mode)
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=int(px(r)), fill=255)
    g.putalpha(m)
    img.alpha_composite(g, (X0, Y0))


def put_shadow(img, box, dx=1.1, dy=1.7, blur=1.5, alpha=0.34):
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
    bx0, by0, bx1, by1, _ = badge
    cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
    mw = (RIGHT_BASE_CX + LEG_W_BASE / 2) - (LEFT_BASE_CX - LEG_W_BASE / 2)
    mh = BASE_Y - APEX_Y
    s = min(((bx1 - bx0) - 2 * pad_x) / mw, ((by1 - by0) - 2 * pad_y) / mh)
    layer = Image.new("L", img.size, 0)
    d = ImageDraw.Draw(layer)
    for poly in mark_polys():
        pts = [(px(cx + (x - 50.0) * s), px(cy + (y - (APEX_Y + BASE_Y) / 2) * s))
               for x, y in poly]
        d.polygon(pts, fill=255)
    white = Image.new("RGBA", img.size, (255, 255, 255, 0))
    white.putalpha(layer)
    img.alpha_composite(white)


def build():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    put_shape(img, SPINE, C_SPINE, mode="diag")
    put_shadow(img, SHEET)
    put_shape(img, SHEET, C_SHEET, mode="diag")     # 青 -> 蓝 对角流动
    put_shadow(img, TAB, dx=0.8, dy=1.2, blur=1.2, alpha=0.30)
    put_shape(img, TAB, C_TAB, mode="diag")
    put_shadow(img, BADGE, dx=1.3, dy=1.9, blur=1.8, alpha=0.40)
    put_shape(img, BADGE, C_BADGE, mode="diag")     # 紫罗兰 -> 深蓝
    put_letter(img, BADGE)
    return img


def main():
    print("rendering v3 master...")
    master = build()
    outs = {}
    for t in [1024, 512, 256, 48, 32, 16]:
        im = master.resize((t, t), Image.LANCZOS)
        im.save(os.path.join(OUT, f"autowork_logo3_{t}.png"))
        outs[t] = im
        print("saved autowork_logo3_%d.png" % t)
    outs[256].save(os.path.join(OUT, "autowork_logo3.ico"), format="ICO",
                   sizes=[(16, 16), (32, 32), (48, 48), (256, 256)],
                   append_images=[outs[16], outs[32], outs[48]])
    print("saved autowork_logo3.ico")

    for bg, name, tc in [((243, 243, 243, 255), "scale_test3_light.png", (60, 60, 60)),
                         ((32, 32, 32, 255), "scale_test3_dark.png", (220, 220, 220))]:
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
