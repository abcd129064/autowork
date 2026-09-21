# -*- coding: utf-8 -*-
"""三列样式对比·放大版：只裁徽章列区域并 2x 放大。"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtGui import (QImage, QPainter, QPen, QColor, QFont,  # noqa: E402
                           QPixmap)

app = QApplication(sys.argv)
app.setFont(QFont("Microsoft YaHei", 10))

sys.path.insert(0, os.path.join(PROJECT_ROOT, "tools", "perf"))
import badge_style_probe as probe  # noqa: E402

# 徽章列 x 区间：勾选36 + 前7列宽(128+128+90+200+200+180+180=1106) = 1142
# 三徽章列 70*3=210 → [1142, 1352]；merged 判定列宽 150 → [1142, 1292]
X0, X1 = 1142, 1352
SCALE = 2

rows = probe.load_rows(14)
shots = []
for name in ("current", "no_bg", "plain", "merged"):
    t = probe.build_table(name, rows)
    t.resize(1420, 480)
    t.horizontalScrollBar().setValue(0)
    for _ in range(6):
        app.processEvents()
    img = t.grab().toImage()
    crop = img.copy(X0 - 10, 0, (X1 - X0) + 20, img.height())
    shots.append((name, crop))
    probe.cleanup(t)

pad = 40
w = max(i.width() for _, i in shots) * SCALE
h = (sum(i.height() for _, i in shots) + pad * len(shots)) * SCALE
canvas = QImage(w, h, QImage.Format.Format_ARGB32)
canvas.fill(QColor("#1e1e1e"))
p = QPainter(canvas)
p.scale(SCALE, SCALE)
y = 0
for name, img in shots:
    p.setPen(QPen(QColor("#e0e0e0")))
    f = QFont("Microsoft YaHei", 11); f.setBold(True); p.setFont(f)
    p.drawText(8, y + 24, f"方案: {name}")
    y += pad
    p.drawImage(0, y, img)
    y += img.height() + 12
p.end()
out = os.path.join(PROJECT_ROOT, "logs", "badge_style_zoom.png")
canvas.save(out)
print("SAVED", out)
