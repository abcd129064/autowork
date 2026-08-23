# -*- coding: utf-8 -*-
"""主界面工具栏冒烟：两行结构（左 FlowLayout + 右固定组）、右侧入口/信息组、无重叠"""
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication, QMainWindow
app = QApplication(sys.argv)

from autowork_with_table import Ui_MainWindow
from core.flow_widgets import FlowToolbarScrollArea

win = QMainWindow()
ui = Ui_MainWindow()
ui.setupUi(win)

assert isinstance(ui.toolbar_scroll, FlowToolbarScrollArea)

# 1. 两行容器结构
assert ui.row1_container is not None and ui.row2_container is not None
assert ui.row1_hbox.__class__.__name__ == "QHBoxLayout"
assert ui.row2_hbox.__class__.__name__ == "QHBoxLayout"

def widgets_of(layout):
    out = []
    for i in range(layout.count()):
        w = layout.itemAt(i).widget()
        if w is not None:
            out.append(w)
    return out

# 2. 第一行左侧：刷新/日期/◀▶/打开目录/写入表格（布局调整：写入表格从第二行移入）
left1 = [w.objectName() for w in widgets_of(ui.horizontalLayout)]
for name in ("flush", "date", "date_prev", "date_next", "write_table",
             "btn_write_table"):
    assert name in left1, (name, left1)
assert len(left1) == 6, left1
# 写入表格紧跟在打开目录右侧（顺序校验）
assert left1.index("btn_write_table") == left1.index("write_table") + 1, left1

# 3. 第一行右侧：时间/数据库状态（布局调整：与第二行入口组对调）
right1 = [w.objectName() for w in widgets_of(ui.row1_right_layout)]
assert right1 == ["time_label", "db_status_label"], right1

# 4. 第二行左侧：程序/帧控/播放（写入表格已移出）
left2 = [w.objectName() for w in widgets_of(ui.horizontalLayout2)]
for name in ("label_2", "choose_exe", "input_frame_before", "input_frame_set",
             "input_frame_custom", "input_frame", "open_daily", "start",
             "pause_btn", "start_three_btn"):
    assert name in left2, (name, left2)
assert "btn_write_table" not in left2, left2
assert len(left2) == 10, left2

# 5. 第二行右侧：售后面板/球桌管理/远程（布局调整：与信息组对调至第二行）
right2 = [w.objectName() for w in widgets_of(ui.row2_right_layout)]
assert right2 == ["btn_aftersale", "table_panel_btn", "p2p_btn"], right2

# 6. 文案与初始值
assert ui.btn_aftersale.text() == "售后面板"
assert ui.time_label.text() == "--:--:--"
assert ui.db_status_label.text() == "数据库: 检查中"

# 7. 展示后：右组贴右、左组在左、无重叠
win.resize(1440, 900)
win.show()
app.processEvents()
r1_left_flow = ui.horizontalLayout
left_flow_right = max(w.geometry().right() for w in widgets_of(r1_left_flow))
r1r_left_global = ui.row1_right.mapTo(ui.row1_container, ui.row1_right.rect().topLeft()).x()
assert left_flow_right < r1r_left_global, (left_flow_right, r1r_left_global)
assert ui.row1_right.geometry().right() <= ui.row1_container.width(), (
    ui.row1_right.geometry(), ui.row1_container.width())
# 右组整体贴右（右侧留白 < 4px）
right_gap = ui.row1_container.width() - ui.row1_right.geometry().right()
assert 0 <= right_gap <= 4, right_gap

print("TOOLBAR_TWO_ROW_OK left1=%d right1=%d left2=%d right2=%d gap=%d" % (
    len(left1), len(right1), len(left2), len(right2), right_gap))
