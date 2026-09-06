# -*- coding: utf-8 -*-
"""S2 前置验证：QTableWidget.sortItems 是否把 cellWidget 随行移动（PySide6 6.11）"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem, QWidget, QVBoxLayout, QPushButton
from PySide6.QtCore import Qt

app = QApplication([])
t = QTableWidget()
t.setColumnCount(3)
t.setRowCount(5)
widgets = {}
for r in range(5):
    it0 = QTableWidgetItem()
    it0.setData(Qt.ItemDataRole.UserRole, 100 + r)
    t.setItem(r, 0, it0)
    t.setItem(r, 1, QTableWidgetItem(f"item-{r}"))
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.addWidget(QPushButton(f"btn-{r}"))
    t.setCellWidget(r, 2, w)
    widgets[100 + r] = w

t.sortItems(1, Qt.SortOrder.AscendingOrder)  # 文本升序 → 行序反转（item-0..4 已是升序，用降序更直观）
t.sortItems(1, Qt.SortOrder.DescendingOrder)

ok_move = True
for r in range(5):
    anchor = t.item(r, 0).data(Qt.ItemDataRole.UserRole)
    w_now = t.cellWidget(r, 2)
    if w_now is not widgets[anchor]:
        ok_move = False
        print(f"row {r}: anchor={anchor} widget mismatch")
# 校验勾选状态随行移动
t.item(0, 0).setCheckState(Qt.CheckState.Checked)
anchor0 = t.item(0, 0).data(Qt.ItemDataRole.UserRole)
t.sortItems(1, Qt.SortOrder.AscendingOrder)
found = None
for r in range(5):
    if t.item(r, 0).data(Qt.ItemDataRole.UserRole) == anchor0:
        found = t.item(r, 0).checkState() == Qt.CheckState.Checked
print("cellWidget 随行移动:", "OK" if ok_move else "FAIL")
print("勾选状态随行移动:", "OK" if found else "FAIL")
sys.stdout.flush()
os._exit(0)
