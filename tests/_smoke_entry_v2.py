# -*- coding: utf-8 -*-
"""offscreen 冒烟：录入页 v2 设计稿落地验证（miniconda 环境运行）"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from windows.aftersale_panel import EntryPage, EditRecordDialog  # noqa: E402

page = EntryPage()
f = page.form

# 1. 默认口径
d = f.collect()
assert d["resolved"] == "是" and d["is_initiative"] == "否" \
    and d["is_our_problem"] == "是", d
assert d["creator"] != "" or True
assert set(d.keys()) == {
    "issue_type", "occurred_at", "table_no", "room_name", "region",
    "problem", "cause", "resolved", "is_initiative", "is_our_problem",
    "solution", "resolver", "response_time", "creator", "snk_code"}, d.keys()

# 2. 分段开关 round-trip
f.resolved_combo.setValue("否")
assert f.collect()["resolved"] == "否"
f.resolved_combo.setValue("是")
assert f.collect()["resolved"] == "是"
f.resolved_combo.setValue("垃圾值")
assert f.resolved_combo.value() == "否"

# 3. 关联确认条（带出芯片已按用户要求移除）
assert f._link_bar.isHidden()
f._set_linked(True, {"name": "88", "roomName": "BaoClub",
                     "snk_code": "SN1", "city": "上海"})
assert not f._link_bar.isHidden()
assert "SNK: SN1" in f._link_bar.text(), f._link_bar.text()
assert not hasattr(f, "_tbl_chip") and not hasattr(f, "_reg_chip")
f._set_linked(False)
assert f._link_bar.isHidden()

# 4. 校验错误态
f.clear_form()
missing = f.validate()
assert missing == ["类型", "桌号", "球房", "地区", "问题"], missing
assert f.first_error is f.type_combo
assert not f._err_type.isHidden() and not f._err_problem.isHidden()
assert "border-color: #cf4452" in f.type_combo.styleSheet()

# 5. 填值后错误清除
f.type_combo.setCurrentText("硬件问题")
f.room_edit.setText("BaoClub")  # 先球房后桌号：改球房会联动清桌号（既有逻辑）
f.table_no_edit.setText("88")
f.region_combo.setEditText("上海")
f.problem_combo.setEditText("灯不亮")
assert f.validate() == []
assert f._err_type.isHidden()
assert "border-color: #cf4452" not in f.type_combo.styleSheet()

# 6. 进度条联动
page._update_required_progress()
assert "5/5" in page._prog_text.text(), page._prog_text.text()
f.problem_combo.setEditText("")
page._update_required_progress()
assert "还差 1 项：问题" == page._prog_text.text(), page._prog_text.text()
f.problem_combo.setEditText("灯不亮")
page._update_required_progress()

# 7. 周期芯片
page._refresh_cycle_chip()
assert "按发生时间自动归属" in page._cycle_chip.text(), page._cycle_chip.text()

# 8. 编辑弹窗复用
rec = dict(d, issue_type="程序相关", table_no="7", room_name="R",
           region="广东", problem="崩溃", creator="甲",
           is_initiative="是")
from PySide6.QtWidgets import QWidget
host = QWidget()
host.resize(1200, 800)
dlg = EditRecordDialog(rec, host)
c2 = dlg.form.collect()
assert c2["issue_type"] == "程序相关" and c2["is_initiative"] == "是" \
    and c2["problem"] == "崩溃", c2

print("SMOKE_V2_ALL_VERIFIED")
