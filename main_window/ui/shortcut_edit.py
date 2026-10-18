# -*- coding: utf-8 -*-
"""Fluent 风格快捷键录入框（从 ui_mixin.py 迁出）。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from qfluentwidgets import LineEdit


class _ShortcutKeyEdit(LineEdit):
    """Fluent 风格快捷键录入框：点击聚焦后按下目标组合键即记录，替代原生 QKeySequenceEdit。

    Backspace/Delete 清空快捷键；单独按修饰键（Ctrl/Shift/Alt）不记录，等待实际按键。"""

    def __init__(self, sequence="", parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setClearButtonEnabled(False)
        self.setPlaceholderText("点击此处后按下组合键")
        self._sequence = QKeySequence(sequence)
        self.setText(self._sequence.toString())

    def keySequence(self):
        """当前录入的快捷键序列"""
        return self._sequence

    def keyPressEvent(self, e):
        key = e.key()
        # 单独按下修饰键/锁定键时不记录，等待实际按键
        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta,
                   Qt.Key_CapsLock, Qt.Key_NumLock, Qt.Key_ScrollLock):
            return
        # Backspace/Delete 清空快捷键
        if key in (Qt.Key_Backspace, Qt.Key_Delete):
            self._sequence = QKeySequence()
            self.setText("")
            e.accept()
            return
        if key == Qt.Key_unknown:
            return
        self._sequence = QKeySequence(e.modifiers() | Qt.Key(key))
        self.setText(self._sequence.toString())
        e.accept()
