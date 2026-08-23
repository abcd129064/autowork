# -*- coding: utf-8 -*-
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication, QMainWindow
app = QApplication(sys.argv)
from autowork_with_table import Ui_MainWindow

win = QMainWindow()
ui = Ui_MainWindow()
ui.setupUi(win)
win.resize(1440, 900)
win.show()
app.processEvents()

# 注意：row1_right/row2_right 是 QWidget，需通过其 layout 遍历子控件（修复旧脚本
# 直接对 QWidget 调 itemAt 的损坏写法）；布局调整后两组内容已对调。
r1w = [ui.row1_right_layout.itemAt(i).widget()
       for i in range(ui.row1_right_layout.count())]
names1 = [w.objectName() for w in r1w if w]
assert names1 == ["time_label", "db_status_label"], names1

r2w = [ui.row2_right_layout.itemAt(i).widget()
       for i in range(ui.row2_right_layout.count())]
names2 = [w.objectName() for w in r2w if w]
assert names2 == ["btn_aftersale", "table_panel_btn", "p2p_btn"], names2
assert ui.time_label.text() == "--:--:--"
assert ui.db_status_label.text() == "数据库: 检查中"

# 第一行：左侧 Flow 内容右缘不得侵入右侧信息组（对调后右侧为时间/数据库标签）
r1_left = ui.horizontalLayout
left_flow_right = max(
    w.geometry().right()
    for i in range(r1_left.count())
    if (w := r1_left.itemAt(i).widget()) is not None)
r1r_left = ui.row1_right.mapTo(ui.row1_container,
                               ui.row1_right.rect().topLeft()).x()
assert left_flow_right < r1r_left, (left_flow_right, r1r_left)

# 第一行左侧：刷新/日期/◀▶/打开目录/写入表格 = 6（写入表格紧跟打开目录）
left1 = [ui.horizontalLayout.itemAt(i).widget().objectName()
         for i in range(ui.horizontalLayout.count())]
assert left1 == ["flush", "date", "date_prev", "date_next", "write_table",
                 "btn_write_table"], left1

print("RIGHT_GROUP_OK", names1, "|", names2)
