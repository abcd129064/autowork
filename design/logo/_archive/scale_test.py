# -*- coding: utf-8 -*-
"""生成 16/24/32/48/64 真实像素尺寸测试图（浅色/深色底各一版）"""
import os
from PIL import Image, ImageDraw

OUT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(OUT, "autowork_logo_1024.png")

def sheet(bg_color, name, text_color):
    master = Image.open(SRC).convert("RGBA")
    sizes = [16, 24, 32, 48, 64, 128]
    pad, gap = 28, 24
    h = 128 + pad * 2 + 30
    w = pad * 2 + sum(sizes) + gap * (len(sizes) - 1)
    sheet_img = Image.new("RGBA", (w, h), bg_color)
    d = ImageDraw.Draw(sheet_img)
    x = pad
    for s in sizes:
        im = master.resize((s, s), Image.LANCZOS)
        y = pad + (128 - s) // 2
        sheet_img.alpha_composite(im, (x, y))
        d.text((x, pad + 128 + 8), f"{s}px", fill=text_color)
        x += s + gap
    sheet_img.convert("RGB").save(os.path.join(OUT, name))
    print("saved", name)

sheet((243, 243, 243, 255), "scale_test_light.png", (60, 60, 60))
sheet((32, 32, 32, 255), "scale_test_dark.png", (220, 220, 220))
