# -*- coding: utf-8 -*-
"""放大 spacing=10 后 row2底(物理204)->列表顶(物理228) 交界"""
import cv2
img = cv2.imread(r"C:\Users\shen_zhe\Desktop\autowork\tests\_ft_full.png")
print("shape:", img.shape)
y0, y1 = 198, 248
crop = img[y0:y1, 40:1500]
crop = cv2.resize(crop, None, fx=1.0, fy=6.0, interpolation=cv2.INTER_NEAREST)
cv2.imwrite(r"C:\Users\shen_zhe\Desktop\autowork\tests\_ft_zoom.png", crop)
# 两列采样
for x in (700, 1200):
    print(f"=== col x={x} ===")
    for y in range(200, 240):
        px = img[y, x]
        lum = int(int(px[0])*0.114 + int(px[1])*0.587 + int(px[2])*0.299)
        print(f"  physY={y:3d} logY={y/1.5:6.1f} bgr={px} lum={lum:5.0f}")
print("saved")
