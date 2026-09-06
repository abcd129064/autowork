# -*- coding: utf-8 -*-
"""
AutoWork Fluent Logo v5 —— Word 式横向色相场版
构图与层间衔接机制完全沿用 v4（全局色相场 + 接触阴影 + 缘光），
唯一变化：色相场从"左上→右下对角"改为 Word 式——
横向 紫罗兰(左) → 蓝(中) → 青(右)，纵向向下沉入深蓝。
v4 仍为第一选择，本版供对比。
"""
import os
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

OUT = os.path.dirname(os.path.abspath(__file__))
S = 8192


def px(v):
    return v / 100.0 * S


# ---------------------------------------------------------------- 几何（同 v4）
SPINE = (57, 11, 87, 89, 9)
SHEET = (13, 11, 63, 89, 9)
TAB   = (57, 11, 87, 41, 9)
BADGE = (9, 51, 47, 89, 10)
SHADOW_RGB = (30, 14, 66)

# Word 式色相场：横向 t=gx 紫→蓝→青；纵向 gy 向下沉蓝并压暗
HX_T = [0.0, 0.55, 1.0]
HX_H = [0.745, 0.615, 0.515]     # 紫罗兰 -> 蓝 -> 青
HX_S = [0.720, 0.780, 0.600]
HX_V = [0.880, 0.950, 0.960]
DEPTH_H = 0.620                  # 底部目标色相（正蓝）
DEPTH_W = 0.55                   # 底部色相回拉权重
DEPTH_DARK = 0.26                # 底部压暗幅度

LAYER = {
    "tab":   dict(dv=+0.12, ds=-0.10),
    "sheet": dict(dv=0.0,   ds=0.0),
    "spine": dict(dv=-0.08, ds=+0.04),
    "badge": dict(dv=-0.22, ds=+0.08),
}

APEX_Y, BASE_Y = 21.0, 79.0
LEG_W_TOP, LEG_W_BASE = 11.0, 16.5
LEFT_TOP_X, RIGHT_TOP_X = 44.5, 55.5
LEFT_BASE_CX, RIGHT_BASE_CX = 24.5, 75.5
BAR_Y0, BAR_Y1 = 57.0, 68.0
BAR_INSET = -2.0


def hsv_to_rgb_vec(h, s, v):
    i = np.floor(h * 6.0).astype("int32") % 6
    f = h * 6.0 - np.floor(h * 6.0)
    p = v * (1 - s)
    q = v * (1 - f * s)
    r_ = v * (1 - (1 - f) * s)
    r = np.select([i == 0, i == 1, i == 2, i == 3, i == 4, i == 5], [v, q, p, p, r_, v])
    g = np.select([i == 0, i == 1, i == 2, i == 3, i == 4, i == 5], [r_, v, v, q, p, p])
    b = np.select([i == 0, i == 1, i == 2, i == 3, i == 4, i == 5], [p, p, r_, v, v, q])
    return np.stack([r, g, b], axis=-1)


def global_field(w, h, gx0, gy0, gx1, gy1, dv, ds):
    yy, xx = np.mgrid[0:h, 0:w].astype("float32")
    gx = gx0 + (gx1 - gx0) * xx / max(w - 1, 1)
    gy = gy0 + (gy1 - gy0) * yy / max(h - 1, 1)
    t = np.clip(gx, 0, 1)
    hh = np.interp(t, HX_T, HX_H)
    ss = np.interp(t, HX_T, HX_S)
    vv = np.interp(t, HX_T, HX_V)
    # 纵向：向下沉入正蓝并压暗（Word 底部观感）
    wd = np.clip(gy, 0, 1) ** 1.25 * DEPTH_W
    hh = hh * (1 - wd) + DEPTH_H * wd
    vv = vv - np.clip(gy, 0, 1) ** 1.4 * DEPTH_DARK
    ss = np.clip(ss + ds, 0, 1)
    vv = np.clip(vv + dv, 0, 1)
    rgb = hsv_to_rgb_vec(hh, ss, vv)
    return Image.fromarray((rgb * 255).astype("uint8"), "RGB").convert("RGBA")


def shape_mask(box, full=True):
    x0, y0, x1, y1, r = box
    if full:
        m = Image.new("L", (S, S), 0)
        ImageDraw.Draw(m).rounded_rectangle([px(x0), px(y0), px(x1), px(y1)],
                                            radius=int(px(r)), fill=255)
        return m
    X0, Y0, X1, Y1 = int(px(x0)), int(px(y0)), int(px(x1)), int(px(y1))
    m = Image.new("L", (X1 - X0, Y1 - Y0), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, X1 - X0 - 1, Y1 - Y0 - 1],
                                        radius=int(px(r)), fill=255)
    return m


def put_layer(img, box, name):
    x0, y0, x1, y1, r = box
    X0, Y0, X1, Y1 = int(px(x0)), int(px(y0)), int(px(x1)), int(px(y1))
    w, h = X1 - X0, Y1 - Y0
    off = LAYER[name]
    g = global_field(w, h, x0 / 100, y0 / 100, x1 / 100, y1 / 100, off["dv"], off["ds"])
    g.putalpha(shape_mask(box, full=False))
    img.alpha_composite(g, (X0, Y0))


def put_contact_shadow(img, caster_box, clip_mask, dx=1.0, dy=0.0, blur=1.4, alpha=0.45):
    x0, y0, x1, y1, r = caster_box
    sil = Image.new("L", (S, S), 0)
    ImageDraw.Draw(sil).rounded_rectangle([px(x0 + dx), px(y0 + dy), px(x1 + dx), px(y1 + dy)],
                                          radius=int(px(r)), fill=int(255 * alpha))
    sil = sil.filter(ImageFilter.GaussianBlur(px(blur)))
    sil = Image.composite(sil, Image.new("L", (S, S), 0), clip_mask)
    sh = Image.new("RGBA", (S, S), SHADOW_RGB + (0,))
    sh.putalpha(sil)
    img.alpha_composite(sh)


def put_rim(img, box, alpha_max=30, width=0.5, fade=0.45):
    x0, y0, x1, y1, r = box
    X0, Y0, X1, Y1 = int(px(x0)), int(px(y0)), int(px(x1)), int(px(y1))
    w, h = X1 - X0, Y1 - Y0
    stroke = Image.new("L", (w, h), 0)
    ImageDraw.Draw(stroke).rounded_rectangle([0, 0, w - 1, h - 1], radius=int(px(r)),
                                             outline=255, width=int(px(width)))
    fade_m = np.linspace(1.0, 0.0, h).astype("float32") ** (1.0 / fade)
    fade_m = np.clip(fade_m, 0, 1).reshape(-1, 1)
    arr = np.asarray(stroke).astype("float32") / 255.0 * fade_m * alpha_max
    rim = np.zeros((h, w, 4), "uint8")
    rim[..., :3] = 255
    rim[..., 3] = arr.astype("uint8")
    rim_img = Image.fromarray(rim, "RGBA")
    full = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    full.alpha_composite(rim_img, (X0, Y0))
    full = Image.composite(full, Image.new("RGBA", (S, S), (0, 0, 0, 0)), shape_mask(box))
    img.alpha_composite(full)


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
    spine_mask = shape_mask(SPINE)
    put_layer(img, SPINE, "spine")
    put_contact_shadow(img, SHEET, spine_mask, dx=1.6, dy=0.4, blur=1.8, alpha=0.5)
    put_layer(img, SHEET, "sheet")
    put_rim(img, SHEET, alpha_max=34)
    put_contact_shadow(img, TAB, img.split()[3], dx=0.5, dy=1.5, blur=1.6, alpha=0.45)
    put_layer(img, TAB, "tab")
    put_rim(img, TAB, alpha_max=40)
    put_contact_shadow(img, BADGE, img.split()[3], dx=1.2, dy=1.8, blur=2.0, alpha=0.5)
    put_layer(img, BADGE, "badge")
    put_letter(img, BADGE)
    put_rim(img, BADGE, alpha_max=22)
    return img


def main():
    print("rendering v5 master...")
    master = build()
    outs = {}
    for t in [1024, 512, 256, 48, 32, 16]:
        im = master.resize((t, t), Image.LANCZOS)
        im.save(os.path.join(OUT, f"autowork_logo5_{t}.png"))
        outs[t] = im
        print("saved autowork_logo5_%d.png" % t)
    outs[256].save(os.path.join(OUT, "autowork_logo5.ico"), format="ICO",
                   sizes=[(16, 16), (32, 32), (48, 48), (256, 256)],
                   append_images=[outs[16], outs[32], outs[48]])
    print("saved autowork_logo5.ico")

    crop = outs[512].crop((256, 40, 512, 300))
    crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    bg = Image.new("RGBA", crop.size, (250, 250, 250, 255))
    bg.alpha_composite(crop)
    bg.convert("RGB").save(os.path.join(OUT, "junction_check5.png"))
    print("saved junction_check5.png")

    for bgc, name, tc in [((243, 243, 243, 255), "scale_test5_light.png", (60, 60, 60)),
                          ((32, 32, 32, 255), "scale_test5_dark.png", (220, 220, 220))]:
        sizes = [16, 24, 32, 48, 64, 128]
        pad, gap = 28, 24
        w = pad * 2 + sum(sizes) + gap * (len(sizes) - 1)
        sheet = Image.new("RGBA", (w, 128 + pad * 2 + 30), bgc)
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
