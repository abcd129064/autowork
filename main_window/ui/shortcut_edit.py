# -*- coding: utf-8 -*-
"""Fluent 风格快捷键录入框（从 ui_mixin.py 迁出）。

不用 QKeySequenceEdit 的原因：它的外观和 Fluent 输入框对不上，而且会把
「Ctrl 按住还没松开」这类中间态记成有效组合。这里自己消费按键，只记录
按下瞬间成立的完整序列。
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from qfluentwidgets import LineEdit


class _ShortcutKeyEdit(LineEdit):
    """Fluent 风格快捷键录入框：点击聚焦后按下目标组合键即记录，替代原生 QKeySequenceEdit。

    Backspace/Delete 清空快捷键；单独按修饰键（Ctrl/Shift/Alt）不记录，等待实际按键。"""

    def __init__(self, sequence="", parent=None):
        """只读 + 自绘按键：显示内容完全由 keyPressEvent 决定。"""
        super().__init__(parent)
        # 只读，避免 LineEdit 自己插入字符：否则按 Ctrl+A 会先出现一个 A。
        # 也不挂清除按钮，Backspace 已被占用为「清空整个快捷键」，两个入口语义打架
        self.setReadOnly(True)
        self.setClearButtonEnabled(False)
        self.setPlaceholderText("点击此处后按下组合键")
        self._sequence = QKeySequence(sequence)
        self.setText(self._sequence.toString())

    def keySequence(self):
        """当前录入的快捷键序列"""
        return self._sequence

    def keyPressEvent(self, e):
        """接管全部按键：不走父类，父类会把组合键当文本插入。"""
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
        # 媒体键、输入法合成键会落到 Key_unknown，硬构造只会得到空序列，
        # 把已录入的组合静默冲掉
        if key == Qt.Key_unknown:
            return
        self._sequence = QKeySequence(e.modifiers() | Qt.Key(key))
        self.setText(self._sequence.toString())
        # 必须 accept：事件不吞掉就会继续冒泡到窗口快捷键，
        # 录 Ctrl+S 的当下就真的把文件保存了一次
        e.accept()
