# -*- coding: utf-8 -*-
"""小游戏重开后得分归零回归：2048 与贪吃蛇。

历史 bug：2048 按 R 键重开、贪吃蛇终局后按方向键/「开始」开新局，都只清
棋盘不清控件侧分数，新局得分从上一局继续累加，还会把累计分误提交为最高分。
修复后棋盘在 reset/_reset 时发射 round_reset，控件监听后统一归零。
"""

import sys

import pytest

from PySide6.QtWidgets import QApplication

import windows.management.moyu_widgets as moyu_widgets
from windows.management.moyu_widgets import Game2048Widget, SnakeWidget


@pytest.fixture(scope="module")
def qapp():
    _qapp = QApplication.instance() or QApplication(sys.argv[:1])
    yield _qapp


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    """摸鱼状态读写全部隔离：不读真实纪录，也不写真实 moyu_state.json"""
    monkeypatch.setattr(moyu_widgets, "load_moyu_state", lambda: {})
    monkeypatch.setattr(moyu_widgets, "save_moyu_state", lambda patch: None)


def test_2048_r_key_reset_clears_score(qapp):
    """2048：R 键路径（board.reset）必须把本局得分清零"""
    w = Game2048Widget()
    # 模拟两步合并得分
    w._board.score_changed.emit(100)
    w._board.score_changed.emit(50)
    assert w._score == 150
    assert "150" in w._lbl_score.text()

    # R 键在 keyPressEvent 里就是调 board.reset()
    w._board.reset()
    assert w._score == 0
    assert w._lbl_score.text() == "得分：0"


def test_2048_restart_button_clears_score(qapp):
    """2048：「重新开始」按钮路径同样清零"""
    w = Game2048Widget()
    w._board.score_changed.emit(88)
    assert w._score == 88
    w._restart()
    assert w._score == 0
    assert w._lbl_score.text() == "得分：0"


def test_snake_new_round_after_over_clears_score(qapp):
    """贪吃蛇：终局后按方向键/「开始」开新局，上一局分数不得带入"""
    w = SnakeWidget()
    w._board.score_changed.emit(10)
    w._board.score_changed.emit(20)
    assert w._score == 30

    # 一局打完进入 over，随后 start() 开新局（= 方向键/开始按钮的真实路径）
    w._board._state = "over"
    w._board.start()
    assert w._score == 0
    assert w._lbl_score.text() == "得分：0"


def test_snake_restart_button_clears_score(qapp):
    """贪吃蛇：「重开」按钮路径清零"""
    w = SnakeWidget()
    w._board.score_changed.emit(10)
    assert w._score == 10
    w._restart()
    assert w._score == 0
    assert w._lbl_score.text() == "得分：0"
