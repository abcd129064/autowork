# -*- coding: utf-8 -*-
"""三列样式变体视觉对比图（调研用）：offscreen 渲染 PNG 上下拼接。"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "windows"  # offscreen 不加载系统字体→中文全方块

from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
from PySide6.QtGui import QFont  # noqa: E402
app.setFont(QFont("Microsoft YaHei", 10))

sys.path.insert(0, os.path.join(PROJECT_ROOT, "tools", "perf"))
import badge_style_probe as probe  # noqa: E402

rows = probe.load_rows(12)
shots = []
for name in ("current", "no_bg", "plain", "merged"):
    t = probe.build_table(name, rows)
    t.resize(760, 430)
    for _ in range(6):
        app.processEvents()
    img = t.grab().toImage()
    shots.append((name, img))
    probe.cleanup(t)

pad = 34
w = max(i.width() for _, i in shots)
h = sum(i.height() + pad for _, i in shots)
canvas = QImage(w, h, QImage.Format.Format_ARGB32)
canvas.fill(0xFF202020)
p = QPainter(canvas)
y = 0
from PySide6.QtGui import QPen, QFont, QColor  # noqa: E402
for name, img in shots:
    p.setPen(QPen(QColor("#e0e0e0")))
    f = QFont(); f.setBold(True); p.setFont(f)
    p.drawText(8, y + 22, f"方案: {name}")
    p.drawImage(0, y + pad, img)
    y += img.height() + pad
p.end()
out = os.path.join(PROJECT_ROOT, "logs", "badge_style_compare.png")
canvas.save(out)
print("SAVED", out)
