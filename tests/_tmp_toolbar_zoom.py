# -*- coding: utf-8 -*-
"""放大 row2 按钮底部与下方列表顶部的衔接（按 DPR=1.5 物理像素）"""
import cv2
import numpy as np

img = cv2.imread(r"C:\Users\shen_zhe\Desktop\autowork\tests\_tmp_toolbar_top.png")
print("img shape:", img.shape, " (物理像素)")

# 逻辑: row2_bottom=136, id_list_top=142 ; DPR=1.5 → 物理 204 / 213
# 裁剪物理 y 195-225, x 60-1200 (左半)
y0, y1 = 194, 226
crop = img[y0:y1, 40:1240]
crop = cv2.resize(crop, None, fx=1.0, fy=6.0, interpolation=cv2.INTER_NEAREST)
cv2.imwrite(r"C:\Users\shen_zhe\Desktop\autowork\tests\_tmp_toolbar_zoom.png", crop)

# 采样列 x=900 (物理)，打印 y 物理 194-226
for y in range(194, 226):
    px = img[y, 900]  # BGR
    lum = int(px[0]) * 0.114 + int(px[1]) * 0.587 + int(px[2]) * 0.299
    print(f"physY={y:3d} logY={y/1.5:6.1f} bgr={px} lum={lum:.0f}")
print("saved zoom")
