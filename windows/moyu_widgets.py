# -*- coding: utf-8 -*-
"""摸鱼中心控件集合：2048 / 小说阅读器

供 management_panel.py 的 GamePage 组装使用。状态（最高分、字号、
滚动进度、上次 txt 路径/章节 URL）持久化到独立的 moyu_state.json，不写
settings.json——规避既有配置缓存覆盖问题（settings_mixin 会对
settings.json 做全量回写）。

小说阅读器网页抓取：站点规则/通用 XPath → trafilatura → html.parser
三级提取链，自动解析上一章/下一章与章节列表（目录页），支持续读。
"""

import base64
import json
import logging
import os
import random
import re
from collections import deque
from datetime import datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QFont,
                           QKeySequence, QPainter, QPen, QShortcut)
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout,
                               QListWidget, QListWidgetItem, QStackedWidget,
                               QTextBrowser, QVBoxLayout, QWidget)
from qfluentwidgets import (BodyLabel, CaptionLabel, FluentIcon,
                            LineEdit, PlainTextEdit, PushButton, ToolButton)

from core.app_paths import get_app_dir
from core.utils import show_info_bar

logger = logging.getLogger(__name__)


# ==================== 摸鱼状态持久化 ====================

def _state_path():
    """摸鱼状态文件路径（独立于 settings.json，防配置缓存全量回写覆盖）"""
    return os.path.join(get_app_dir(), "moyu_state.json")


def load_moyu_state() -> dict:
    """读摸鱼状态，文件缺失/损坏返回空字典"""
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_moyu_state(patch: dict):
    """读-改-写合并落盘，失败静默（摸鱼状态丢失不影响主业务）"""
    data = load_moyu_state()
    data.update(patch)
    path = _state_path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        logger.debug("moyu_state.json 保存失败", exc_info=True)


# ==================== 2048 ====================

_TILE_COLORS = {
    0: ("#cdc1b4", None),
    2: ("#eee4da", "#776e65"),
    4: ("#ede0c8", "#776e65"),
    8: ("#f2b179", "#f9f6f2"),
    16: ("#f59563", "#f9f6f2"),
    32: ("#f67c5f", "#f9f6f2"),
    64: ("#f65e3b", "#f9f6f2"),
    128: ("#edcf72", "#f9f6f2"),
    256: ("#edcc61", "#f9f6f2"),
    512: ("#edc850", "#f9f6f2"),
    1024: ("#edc53f", "#f9f6f2"),
    2048: ("#edc22e", "#f9f6f2"),
}


class _Board2048(QWidget):
    """2048 棋盘：QPainter 绘制 + 键盘操作 + 合并逻辑"""

    SIZE = 4
    score_changed = Signal(int)  # 本次合并新增分数

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(420, 420)
        self._grid = [[0] * self.SIZE for _ in range(self.SIZE)]
        self._over = False
        self._won = False       # 曾达成 2048
        self._won_cont = False  # 达成后选择继续
        self.reset()

    # ---------- 游戏逻辑 ----------

    def reset(self):
        """重开一局：清盘并预置两个方块"""
        self._grid = [[0] * self.SIZE for _ in range(self.SIZE)]
        self._over = False
        self._won = False
        self._won_cont = False
        self._spawn()
        self._spawn()
        self.update()

    def _spawn(self):
        """随机空格生成新方块（90% 出 2，10% 出 4）"""
        empty = [(r, c) for r in range(self.SIZE) for c in range(self.SIZE)
                 if self._grid[r][c] == 0]
        if not empty:
            return
        r, c = random.choice(empty)
        self._grid[r][c] = 4 if random.random() < 0.1 else 2

    @staticmethod
    def _merge_line(line, gained_box):
        """向左压缩合并一行，gained_box[0] 累加得分"""
        vals = [v for v in line if v]
        out = []
        i = 0
        while i < len(vals):
            if i + 1 < len(vals) and vals[i] == vals[i + 1]:
                out.append(vals[i] * 2)
                gained_box[0] += vals[i] * 2
                i += 2
            else:
                out.append(vals[i])
                i += 1
        return out + [0] * (4 - len(out))

    def _move(self, direction):
        """四方向移动：行/列归一为向左压缩合并，返回是否发生移动"""
        g = self._grid
        moved = False
        box = [0]
        if direction in ("left", "right"):
            for r in range(self.SIZE):
                line = g[r] if direction == "left" else list(reversed(g[r]))
                new = self._merge_line(line, box)
                if direction == "right":
                    new = list(reversed(new))
                if new != g[r]:
                    moved = True
                g[r] = new
        else:
            for c in range(self.SIZE):
                col = [g[r][c] for r in range(self.SIZE)]
                if direction == "down":
                    col = list(reversed(col))
                new = self._merge_line(col, box)
                if direction == "down":
                    new = list(reversed(new))
                if new != [g[r][c] for r in range(self.SIZE)]:
                    moved = True
                for r in range(self.SIZE):
                    g[r][c] = new[r]
        if moved and box[0]:
            self.score_changed.emit(box[0])
        return moved

    def _moves_available(self):
        """是否还能移动：有空格或任意相邻同值即未终局"""
        g = self._grid
        for r in range(self.SIZE):
            for c in range(self.SIZE):
                if g[r][c] == 0:
                    return True
                if c + 1 < self.SIZE and g[r][c] == g[r][c + 1]:
                    return True
                if r + 1 < self.SIZE and g[r][c] == g[r + 1][c]:
                    return True
        return False

    # ---------- 事件 ----------

    def keyPressEvent(self, e):
        key = e.key()
        if key == Qt.Key.Key_R:
            self.reset()
            return
        if self._over:
            return
        if self._won and not self._won_cont:
            # 达成 2048 提示覆盖层：回车/空格继续游戏
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter,
                       Qt.Key.Key_Space):
                self._won_cont = True
                self.update()
            return
        move = {
            Qt.Key.Key_Left: "left", Qt.Key.Key_A: "left",
            Qt.Key.Key_Right: "right", Qt.Key.Key_D: "right",
            Qt.Key.Key_Up: "up", Qt.Key.Key_W: "up",
            Qt.Key.Key_Down: "down", Qt.Key.Key_S: "down",
        }.get(key)
        if not move:
            super().keyPressEvent(e)
            return
        if self._move(move):
            self._spawn()
            if not self._won and any(v >= 2048 for row in self._grid
                                     for v in row):
                self._won = True
            if not self._moves_available():
                self._over = True
            self.update()

    # ---------- 绘制 ----------

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        margin = 12
        side = min(self.width(), self.height()) - margin * 2
        if side <= 0:
            return
        x0 = (self.width() - side) // 2
        y0 = (self.height() - side) // 2
        gap = 10
        cell = (side - gap * (self.SIZE + 1)) / self.SIZE

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#bbada0")))
        p.drawRoundedRect(x0, y0, side, side, 10, 10)

        for r in range(self.SIZE):
            for c in range(self.SIZE):
                v = self._grid[r][c]
                cx = x0 + gap + c * (cell + gap)
                cy = y0 + gap + r * (cell + gap)
                bg, fg = _TILE_COLORS.get(v, ("#3c3a32", "#f9f6f2"))
                p.setBrush(QBrush(QColor(bg)))
                p.drawRoundedRect(int(cx), int(cy), int(cell), int(cell),
                                  6, 6)
                if v:
                    p.setPen(QPen(QColor(fg)))
                    f = QFont("Segoe UI", 10, QFont.Weight.Bold)
                    digits = len(str(v))
                    f.setPixelSize(int(cell * (0.42 if digits <= 2
                                               else 0.34 if digits == 3
                                               else 0.27)))
                    p.setFont(f)
                    p.drawText(int(cx), int(cy), int(cell), int(cell),
                               Qt.AlignmentFlag.AlignCenter, str(v))

        # 覆盖层：胜利 / 游戏结束
        overlay = None
        if self._won and not self._won_cont:
            overlay = ("达成 2048！", "回车继续游戏 · R 重新开始")
        elif self._over:
            overlay = ("游戏结束", "按 R 重新开始")
        if overlay:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(255, 255, 255, 160)
                              if self._over else QColor(237, 194, 46, 190)))
            p.drawRoundedRect(x0, y0, side, side, 10, 10)
            p.setPen(QPen(QColor("#776e65")))
            f1 = QFont("Segoe UI", 10, QFont.Weight.Bold)
            f1.setPixelSize(int(side * 0.09))
            p.setFont(f1)
            p.drawText(x0, y0 + int(side * 0.30), side, int(side * 0.14),
                       Qt.AlignmentFlag.AlignCenter, overlay[0])
            f2 = QFont("Segoe UI", 10)
            f2.setPixelSize(int(side * 0.045))
            p.setFont(f2)
            p.drawText(x0, y0 + int(side * 0.48), side, int(side * 0.10),
                       Qt.AlignmentFlag.AlignCenter, overlay[1])


class Game2048Widget(QWidget):
    """2048 游戏：信息栏（得分/最高分/重开）+ 棋盘"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._score = 0
        self._best = int(load_moyu_state().get("best_2048") or 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        self._lbl_score = BodyLabel("得分：0", self)
        self._lbl_best = BodyLabel(f"最高分：{self._best}", self)
        bar.addWidget(self._lbl_score)
        bar.addSpacing(16)
        bar.addWidget(self._lbl_best)
        bar.addStretch(1)
        hint = CaptionLabel("方向键 / WASD 移动 · R 重开", self)
        hint.setStyleSheet("color: #8a8f98;")
        bar.addWidget(hint)
        bar.addSpacing(12)
        self._btn_restart = PushButton("重新开始", self)
        self._btn_restart.clicked.connect(self._restart)
        bar.addWidget(self._btn_restart)
        layout.addLayout(bar)

        self._board = _Board2048(self)
        self._board.score_changed.connect(self._on_score)
        layout.addWidget(self._board, 1)

    def setFocus(self, reason=Qt.FocusReason.OtherFocusReason):
        """页签切入时由 GamePage 调用，把键盘焦点转给棋盘"""
        super().setFocus(reason)
        self._board.setFocus(reason)

    def _on_score(self, gained):
        """累加得分，刷新最高分并落盘"""
        self._score += gained
        self._lbl_score.setText(f"得分：{self._score}")
        if self._score > self._best:
            self._best = self._score
            self._lbl_best.setText(f"最高分：{self._best}")
            save_moyu_state({"best_2048": self._best})

    def _restart(self):
        """重新开始：重开棋盘并清零得分"""
        self._board.reset()
        self._score = 0
        self._lbl_score.setText("得分：0")
        self._board.setFocus()


# ==================== 贪吃蛇 ====================

class _SnakeBoard(QWidget):
    """贪吃蛇棋盘：QTimer 驱动 + QPainter 绘制"""

    COLS, ROWS, CELL = 24, 17, 22
    score_changed = Signal(int)
    state_changed = Signal(str)  # idle / running / paused / over

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(self.COLS * self.CELL + 2,
                          self.ROWS * self.CELL + 2)
        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._tick)
        self._queue = deque()
        self._resume_on_show = False
        self._reset()

    # ---------- 状态机 ----------

    def _reset(self):
        """重置蛇身/方向/食物，回到待开始态"""
        cy = self.ROWS // 2
        self._snake = deque([(8, cy), (7, cy), (6, cy)])  # 头在前
        self._dir = (1, 0)
        self._queue.clear()
        self._state = "idle"
        self._food = self._spawn_food()

    def _spawn_food(self):
        """在非蛇身格随机放食物；占满全场返回 None"""
        occupied = set(self._snake)
        free = [(x, y) for x in range(self.COLS) for y in range(self.ROWS)
                if (x, y) not in occupied]
        return random.choice(free) if free else None

    def state(self):
        """当前状态机状态（idle/running/paused/over）"""
        return self._state

    def start(self):
        """开始/重开游戏并启动走格定时器"""
        if self._state in ("idle", "over"):
            self._reset()
        self._state = "running"
        self._timer.start()
        self.state_changed.emit(self._state)
        self.update()

    def pause(self):
        """运行中暂停（停表保状态）"""
        if self._state == "running":
            self._state = "paused"
            self._timer.stop()
            self.state_changed.emit(self._state)
            self.update()

    def resume(self):
        """从暂停恢复走格"""
        if self._state == "paused":
            self._state = "running"
            self._timer.start()
            self.state_changed.emit(self._state)
            self.update()

    # ---------- 游戏逻辑 ----------

    def _tick(self):
        """单步走格：消费方向队列，撞墙/撞自己判死；吃到食物加分并换食物"""
        if self._queue:
            self._dir = self._queue.popleft()
        hx, hy = self._snake[0]
        nx, ny = hx + self._dir[0], hy + self._dir[1]
        eating = self._food is not None and (nx, ny) == self._food
        # 撞墙
        if not (0 <= nx < self.COLS and 0 <= ny < self.ROWS):
            self._die()
            return
        # 撞自己（不吃时尾格会让出，允许蛇头进入）
        body = set(self._snake) if eating else set(list(self._snake)[:-1])
        if (nx, ny) in body:
            self._die()
            return
        self._snake.appendleft((nx, ny))
        if eating:
            self.score_changed.emit(10)
            self._food = self._spawn_food()
            if self._food is None:  # 占满全场，胜利结束
                self._die()
                return
        else:
            self._snake.pop()
        self.update()

    def _die(self):
        """判死：停表并切 over 态"""
        self._state = "over"
        self._timer.stop()
        self.state_changed.emit(self._state)
        self.update()

    def _push_dir(self, d):
        """方向入队：忽略重复与 180° 反向，队列最深 2（兼顾手感与可预测性）"""
        last = self._queue[-1] if self._queue else self._dir
        if d == last:
            return
        if d[0] + last[0] == 0 and d[1] + last[1] == 0:
            return  # 禁止 180° 反向
        if len(self._queue) < 2:
            self._queue.append(d)

    # ---------- 事件 ----------

    def keyPressEvent(self, e):
        dirs = {
            Qt.Key.Key_Up: (0, -1), Qt.Key.Key_W: (0, -1),
            Qt.Key.Key_Down: (0, 1), Qt.Key.Key_S: (0, 1),
            Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_A: (-1, 0),
            Qt.Key.Key_Right: (1, 0), Qt.Key.Key_D: (1, 0),
        }
        d = dirs.get(e.key())
        if d is None:
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Space) \
                    and self._state in ("idle", "over"):
                self.start()
                return
            super().keyPressEvent(e)
            return
        if self._state in ("idle", "over"):
            self.start()
        if self._state == "paused":
            self.resume()
        self._push_dir(d)

    def hideEvent(self, e):
        """页面不可见时停表，避免后台空转"""
        super().hideEvent(e)
        if self._state == "running":
            self._resume_on_show = True
            self.pause()

    def showEvent(self, e):
        super().showEvent(e)
        if self._resume_on_show:
            self._resume_on_show = False
            self.resume()

    # ---------- 绘制 ----------

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.COLS * self.CELL
        h = self.ROWS * self.CELL
        p.setPen(QPen(QColor("#232a35"), 1))
        p.setBrush(QBrush(QColor("#10141a")))
        p.drawRect(0, 0, w + 1, h + 1)
        # 淡网格
        p.setPen(QPen(QColor(255, 255, 255, 10), 1))
        for x in range(1, self.COLS):
            p.drawLine(x * self.CELL, 0, x * self.CELL, h)
        for y in range(1, self.ROWS):
            p.drawLine(0, y * self.CELL, w, y * self.CELL)
        # 食物
        if self._food:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor("#ff6b6b")))
            fx, fy = self._food
            p.drawEllipse(fx * self.CELL + 5, fy * self.CELL + 5,
                          self.CELL - 10, self.CELL - 10)
        # 蛇身
        for i, (x, y) in enumerate(self._snake):
            color = "#86efac" if i == 0 else "#4ade80"
            p.setBrush(QBrush(QColor(color)))
            p.drawRoundedRect(x * self.CELL + 2, y * self.CELL + 2,
                              self.CELL - 4, self.CELL - 4, 4, 4)
        # 覆盖层
        overlay = {
            "idle": ("贪吃蛇", "方向键 / WASD 开始并控制方向"),
            "paused": ("已暂停", "点击「继续」或方向键恢复"),
            "over": ("游戏结束", "按「开始」或方向键重新开始"),
        }.get(self._state)
        if overlay:
            p.setBrush(QBrush(QColor(0, 0, 0, 150)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRect(0, 0, w + 1, h + 1)
            p.setPen(QPen(QColor("#e5e7eb")))
            f1 = QFont("Segoe UI", 10, QFont.Weight.Bold)
            f1.setPixelSize(28)
            p.setFont(f1)
            p.drawText(0, h // 2 - 44, w + 1, 34,
                       Qt.AlignmentFlag.AlignCenter, overlay[0])
            f2 = QFont("Segoe UI", 10)
            f2.setPixelSize(14)
            p.setFont(f2)
            p.setPen(QPen(QColor("#9ca3af")))
            p.drawText(0, h // 2 + 2, w + 1, 22,
                       Qt.AlignmentFlag.AlignCenter, overlay[1])


class SnakeWidget(QWidget):
    """贪吃蛇：信息栏（得分/最高分/控制按钮）+ 棋盘"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._score = 0
        self._best = int(load_moyu_state().get("best_snake") or 0)
        self._auto_paused = False  # 页签切走导致的暂停，切回自动恢复

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        self._lbl_score = BodyLabel("得分：0", self)
        self._lbl_best = BodyLabel(f"最高分：{self._best}", self)
        bar.addWidget(self._lbl_score)
        bar.addSpacing(16)
        bar.addWidget(self._lbl_best)
        bar.addStretch(1)
        self._btn_toggle = PushButton("开始", self)
        self._btn_toggle.clicked.connect(self._toggle)
        self._btn_restart = PushButton("重开", self)
        self._btn_restart.clicked.connect(self._restart)
        bar.addWidget(self._btn_toggle)
        bar.addWidget(self._btn_restart)
        layout.addLayout(bar)

        self._board = _SnakeBoard(self)
        self._board.score_changed.connect(self._on_score)
        self._board.state_changed.connect(self._on_state)
        layout.addWidget(self._board, 0, Qt.AlignmentFlag.AlignHCenter)

    def setFocus(self, reason=Qt.FocusReason.OtherFocusReason):
        super().setFocus(reason)
        self._board.setFocus(reason)

    def _toggle(self):
        """开始/暂停/继续三态切换按钮"""
        s = self._board.state()
        if s == "running":
            self._board.pause()
        elif s == "paused":
            self._board.resume()
        else:
            self._board.start()

    def _restart(self):
        """重开：清零得分并直接开局"""
        self._score = 0
        self._lbl_score.setText("得分：0")
        self._board.start()
        self._board.setFocus()

    def _on_score(self, gained):
        """吃食物加分，刷新最高分并落盘"""
        self._score += gained
        self._lbl_score.setText(f"得分：{self._score}")
        if self._score > self._best:
            self._best = self._score
            self._lbl_best.setText(f"最高分：{self._best}")
            save_moyu_state({"best_snake": self._best})

    def _on_state(self, state):
        """状态机变化 → 切换按钮文案（开始/暂停/继续）"""
        self._btn_toggle.setText(
            {"running": "暂停", "paused": "继续"}.get(state, "开始"))

    # ---------- 页签切换联动 ----------

    def auto_pause(self):
        """页签切走：运行中则暂停并记录，供切回恢复"""
        if self._board.state() == "running":
            self._auto_paused = True
            self._board.pause()

    def auto_resume(self):
        """页签切回：自动恢复被 auto_pause 暂停的局"""
        if self._auto_paused:
            self._auto_paused = False
            self._board.resume()


# ==================== 小说阅读器 ====================

class _TextExtractor(HTMLParser):
    """html.parser 提取纯文本：剔除 script/style，块级标签转换行

    仅作为 trafilatura 提取失败/结果过短时的兜底方案。
    """

    _SKIP = {"script", "style", "noscript", "head", "template", "iframe"}
    _BREAK = {"p", "br", "div", "li", "tr", "ul", "ol", "table", "section",
              "article", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BREAK:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self._BREAK:
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self._parts.append(data)

    def text(self):
        """提取结果：拼接各片段后压缩空行"""
        return _compress_lines("".join(self._parts))


def _compress_lines(text: str) -> str:
    """压缩文本：去行首尾空白、合并连续空行为单个空行（各提取路径共用）"""
    lines = []
    for raw in text.splitlines():
        lines.append(" ".join(raw.split()))
    out, blank = [], False
    for s in lines:
        if s:
            out.append(s)
            blank = False
        elif not blank:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def _extract_web_text(html: str) -> str:
    """网页正文提取：优先 trafilatura（自动剔除导航/广告/页脚，精度更高）

    失败或结果过短时回退 _TextExtractor，保证旧行为兜底、不引入新风险。
    """
    try:
        from trafilatura import extract
        text = extract(html, output_format="txt", favor_precision=True,
                       include_links=False, include_images=False,
                       include_tables=False, deduplicate=True)
        if text and len(text) >= 50:
            return text
    except Exception:
        pass
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text()


# ==================== 小说网页解析（站点规则 + 章节导航） ====================

# 站点规则：域名关键字 → 正文容器 XPath（legado 书源规则简化版）。
# 命中则优先精准提取；全部未命中回退通用提取链（trafilatura → _TextExtractor）。
_SITE_RULES = [
    ("biquge", ["//*[@id='content']", "//*[@id='chaptercontent']"]),
    ("xbiquge", ["//*[@id='content']", "//*[@id='chaptercontent']"]),
    ("bqg", ["//*[@id='content']", "//*[contains(@class,'read-content')]"]),
    ("bqxs", ["//*[@id='content']"]),
]

# 通用正文容器 XPath：不依赖站点规则，按序尝试，首个 ≥50 字者采纳
_GENERIC_CONTENT_XPATHS = [
    "//*[@id='content']",
    "//*[@id='chaptercontent']",
    "//*[@id='booktext']",
    "//*[contains(@class,'read-content')]",
    "//*[contains(@class,'chapter-content')]",
    "//*[contains(@class,'txtnav')]",
]

# 块级标签：节点转段落文本时换行分隔
_BLOCK_TAGS = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4",
               "h5", "h6", "blockquote", "section", "article"}

# 目录容器 XPath：命中容器内的同站链接视为章节列表
_TOC_CONTAINER_XPATHS = [
    "//*[contains(@class,'section-list')]//a[@href]",
    "//*[contains(@class,'chapter-list')]//a[@href]",
    "//*[contains(@class,'listmain')]//a[@href]",
    "//*[@id='list']//a[@href]",
    "//*[@id='listmain']//a[@href]",
    "//dl[@id='list']//a[@href]",
    "//*[contains(@class,'catalog')]//a[@href]",
]

_CHAPTER_RE = re.compile(r"第\s*[0-9零一二三四五六七八九十百千万两]+\s*[章节卷回篇]")
_NEXT_RE = re.compile(r"下一[章节篇]|下章|下一页|next", re.I)
_PREV_RE = re.compile(r"上一[章节篇]|上章|上一页|prev", re.I)


class _HtmlText:
    """lxml 节点转段落纯文本：剔除 script/style，块级标签换行分隔"""

    _SKIP = {"script", "style", "noscript", "head", "template", "iframe"}

    @staticmethod
    def _walk(el, parts):
        tag = el.tag if isinstance(el.tag, str) else None
        if tag in _HtmlText._SKIP:
            # 跳过内部内容，但保留其后置文本（如 <script>..</script> 后的文字）
            if el.tail and el.tail.strip():
                parts.append(el.tail)
            return
        if tag in _BLOCK_TAGS:
            parts.append("\n")
        if el.text and el.text.strip():
            parts.append(el.text)
        for child in el:
            _HtmlText._walk(child, parts)
        if el.tail and el.tail.strip():
            parts.append(el.tail)

    @classmethod
    def extract(cls, node) -> str:
        """lxml 子树 → 压缩后的段落纯文本"""
        parts = []
        cls._walk(node, parts)
        return _compress_lines("".join(parts))


def _norm_host(url: str) -> str:
    """归一化域名：去协议/路径/www 前缀，小写"""
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _same_site(url_a: str, url_b: str) -> bool:
    """同站点判断（忽略 www 前缀差异）"""
    return _norm_host(url_a) == _norm_host(url_b)


def _extract_by_rules(doc, url: str) -> str:
    """站点规则 + 通用正文容器 XPath 提取；首个 ≥50 字结果即采纳"""
    host = _norm_host(url)
    xpaths = []
    for kw, paths in _SITE_RULES:
        if kw in host:
            xpaths.extend(paths)
    xpaths.extend(_GENERIC_CONTENT_XPATHS)
    seen = set()
    for xp in xpaths:
        if xp in seen:
            continue
        seen.add(xp)
        try:
            for node in doc.xpath(xp):
                text = _HtmlText.extract(node)
                if len(text) >= 50:
                    return text
        except Exception:
            continue
    return ""


def _extract_title(doc) -> str:
    """章节标题：lastread.set（书名/章节名数据）→ h1 → title 逐级降级"""
    js = "".join(doc.xpath("//script/text()"))
    m = re.search(r"lastread\.set\(([^)]+)\)", js)
    if m:
        args = [a.strip().strip("'\"") for a in m.group(1).split(",")]
        if len(args) >= 4 and args[3]:
            return args[3]  # lastread.set(书id, 章id, 书名, 章节名, ...)
    for node in doc.xpath("//h1"):
        t = "".join(node.itertext()).strip()
        if t:
            return t
    for node in doc.xpath("//title"):
        t = "".join(node.itertext()).strip()
        if not t:
            continue
        return re.split(r"[_\-|]", t)[0].strip()
    return ""


def _decrypt_writeln_text(html: str) -> str:
    """解密 JS 加密正文（彩虹工具类：document.writeln(xxx.bb('base64'))）

    此类站点把段落 HTML 经 base64 编码后由 JS 写入页面，
    Python 侧直接解码还原明文 HTML 再转纯文本。
    """
    parts = []
    pat = re.compile(r"document\.writeln\(\s*[\w.$]+\.bb\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\)")
    for m in pat.finditer(html):
        try:
            parts.append(base64.b64decode(m.group(1))
                         .decode("utf-8", errors="replace"))
        except Exception:
            continue
    if not parts:
        return ""
    try:
        from lxml import html as _lh
        doc = _lh.fromstring("<div>" + "".join(parts) + "</div>")
        text = _HtmlText.extract(doc)
    except Exception:
        text = ""
    return text if len(text) >= 50 else ""


def _chapter_key(url: str) -> str:
    """章节 URL 归一化：取路径末尾数字段（13812008_1.html → 13812008），
    用于目录列表与当前章节匹配、上下章推导。"""
    m = re.search(r"/(\d+)(?:_\d+)?\.html?$", url)
    return m.group(1) if m else url


def _parse_nav(doc, url: str):
    """解析导航：返回 (prev_url, next_url, chapters, toc_url)

    章节列表优先取目录容器（.section-list/#list 等）内的同站链接，
    其次取全页含「第X章」文本的链接；上一章/下一章支持文本链接与
    pageEvent 键盘事件（key==37/39 → location=var）两种模式。
    """
    prev_url = next_url = toc_url = None
    chapters = []
    seen = set()

    def _add_chapter(text, target):
        if text and target not in seen:
            seen.add(target)
            chapters.append((text, target))

    for xp in _TOC_CONTAINER_XPATHS:
        try:
            nodes = doc.xpath(xp)
        except Exception:
            continue
        for a in nodes:
            href = (a.get("href") or "").strip()
            text = "".join(a.itertext()).strip()
            if not href or not text:
                continue
            target = urljoin(url, href)
            if _same_site(url, target):
                _add_chapter(text, target)

    # 全页兜底：标题含「第X章/节/卷」的链接（目录容器未命中时）
    if len(chapters) < 10:
        for a in doc.xpath("//a[@href]"):
            href = (a.get("href") or "").strip()
            text = "".join(a.itertext()).strip()
            if not href or not text:
                continue
            target = urljoin(url, href)
            if not _same_site(url, target):
                continue
            if _CHAPTER_RE.search(text) and len(text) <= 40:
                _add_chapter(text, target)

    # 上一章/下一章：文本链接（下一章/下章/下一页…）
    for a in doc.xpath("//a[@href]"):
        href = (a.get("href") or "").strip()
        text = "".join(a.itertext()).strip()
        if not href or not text:
            continue
        target = urljoin(url, href)
        if not _same_site(url, target):
            continue
        if len(text) <= 16 and _NEXT_RE.search(text):
            next_url = target
        elif len(text) <= 16 and _PREV_RE.search(text):
            prev_url = target

    # 上一章/下一章：pageEvent 键盘事件（key==37/39 → location=变量）
    js = "".join(doc.xpath("//script/text()"))
    m37 = re.search(r"key\s*==\s*37\s*\)\s*(?:window\.)?location\s*=\s*([A-Za-z_]\w*)", js)
    m39 = re.search(r"key\s*==\s*39\s*\)\s*(?:window\.)?location\s*=\s*([A-Za-z_]\w*)", js)
    if m37 and m39:
        vars_map = dict(re.findall(r"var\s+([A-Za-z_]\w*)\s*=\s*'([^']+)'", js))
        for name, val in vars_map.items():
            if not val.endswith(".html"):
                continue
            if name == m37.group(1):
                prev_url = urljoin(url, val)
            elif name == m39.group(1):
                next_url = urljoin(url, val)

    # 章节目录链接（供 worker 自动抓目录页）：
    # 优先文本含「目录/章节列表」且与当前章节同书目录层级者（书详情页/最近更新
    # 页虽含「目录」字样但链接到 /book/ 体系，章节列表不全或乱序，需避开）；
    # 无候选时按 URL 结构推导 /{book}/{chapter}.html → /{book}/
    path = urlparse(url).path
    book_base = urljoin(url, path.rsplit("/", 1)[0] + "/") if "/" in path else ""
    toc_candidates = []
    for a in doc.xpath("//a[@href]"):
        href = (a.get("href") or "").strip()
        text = "".join(a.itertext()).strip()
        if not href or not text:
            continue
        if "目录" in text or "章节列表" in text:
            toc_candidates.append(urljoin(url, href))
    if toc_candidates:
        def _score(u):
            if not book_base:
                return 2
            if u.startswith(book_base) and \
                    not u[len(book_base):].lstrip("/").lower().endswith(".html"):
                return 0
            return 1 if u.startswith(book_base) else 2
        toc_candidates.sort(key=_score)
        toc_url = toc_candidates[0]
    elif book_base and re.search(r"/\d+/\d+(?:_\d+)?\.html?$", path):
        toc_url = book_base
    return prev_url, next_url, chapters, toc_url


def _parse_toc_page(html: str, url: str):
    """目录页解析：复用 _parse_nav 的容器逻辑，返回章节列表"""
    try:
        from lxml import html as _lh
        doc = _lh.fromstring(html)
    except Exception:
        return []
    _prev, _next, chapters, _toc = _parse_nav(doc, url)
    return chapters


def _parse_web_page(html: str, url: str) -> dict:
    """解析小说页面：返回 {url, title, text, prev_url, next_url, chapters, toc_url}

    提取链：站点规则 XPath → 通用 XPath → JS 加密正文解密 →
    trafilatura → html.parser 兜底。
    """
    result = {"url": url, "title": "", "text": "",
              "prev_url": "", "next_url": "", "chapters": [], "toc_url": ""}
    doc = None
    try:
        from lxml import html as _lh
        doc = _lh.fromstring(html)
    except Exception:
        doc = None
    if doc is not None:
        result["title"] = _extract_title(doc)
        result["text"] = _extract_by_rules(doc, url)
        prev, nxt, chapters, toc = _parse_nav(doc, url)
        result["prev_url"] = prev or ""
        result["next_url"] = nxt or ""
        result["toc_url"] = toc or ""
        # 去重保序
        seen = set()
        result["chapters"] = [c for c in chapters
                               if not (c[1] in seen or seen.add(c[1]))]
    if len(result["text"]) < 50:
        result["text"] = _decrypt_writeln_text(html)
    if len(result["text"]) < 50:
        result["text"] = _extract_web_text(html)
    return result


class _FetchWorker(QThread):
    """一次性网页抓取 worker：requests 拉取 + 正文/章节导航解析，完成即退"""

    ok = Signal(dict)  # _parse_web_page 结果：正文/标题/上下章/章节列表
    failed = Signal(str)

    _UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self._url = url

    def _headers(self):
        """反爬请求头：UA + 同源 Referer + 中文语言偏好"""
        p = urlparse(self._url)
        return {
            "User-Agent": self._UA,
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                       "image/avif,image/webp,*/*;q=0.8"),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": f"{p.scheme}://{p.netloc}/",
        }

    def _fetch_text(self, url):
        """requests 拉取页面并规范编码，返回 (html, final_url)（final_url 为重定向后地址）"""
        import requests
        resp = requests.get(url, headers=self._headers(),
                            timeout=10, allow_redirects=True)
        resp.raise_for_status()
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            try:
                resp.encoding = resp.apparent_encoding
            except Exception:
                resp.encoding = "utf-8"
        return resp.text, resp.url or url

    def run(self):
        """拉取页面 → 解析正文/导航；章节不足自动补目录页，上下章缺失按目录推导"""
        try:
            html, final_url = self._fetch_text(self._url)
            result = _parse_web_page(html, final_url)
            # 章节列表不足 10 条时自动抓目录页补齐（目录失败不影响正文阅读）
            if len(result["chapters"]) < 10 and result["toc_url"]:
                try:
                    toc_html, toc_url = self._fetch_text(result["toc_url"])
                    toc_chapters = _parse_toc_page(toc_html, toc_url)
                    merged = list(result["chapters"])
                    seen = {_chapter_key(u) for _t, u in merged}
                    for t, u in toc_chapters:
                        k = _chapter_key(u)
                        if k not in seen:
                            seen.add(k)
                            merged.append((t, u))
                    result["chapters"] = merged
                except Exception:
                    pass
            # 上下章推导：在章节列表定位当前章节，缺失的按序补齐
            key = _chapter_key(final_url)
            idx = next((i for i, (_t, u) in enumerate(result["chapters"])
                        if _chapter_key(u) == key), -1)
            if idx >= 0:
                if not result["prev_url"] and idx > 0:
                    result["prev_url"] = result["chapters"][idx - 1][1]
                if not result["next_url"] and idx + 1 < len(result["chapters"]):
                    result["next_url"] = result["chapters"][idx + 1][1]
            if len(result["text"]) < 50:
                self.failed.emit("未能提取到有效正文（可能触发站点反爬）")
            else:
                self.ok.emit(result)
        except Exception as e:
            self.failed.emit(str(e) or e.__class__.__name__)


def _fake_log_text() -> str:
    """生成静态假日志文本，供老板键伪装的「系统日志」视图使用"""
    rnd = random.Random(20260808)
    hosts = ("collector-01", "collector-02", "edge-gw", "api-node")
    tpls = (
        ("INFO", "heartbeat check ok latency={a}ms"),
        ("INFO", "task sync finished items={b}"),
        ("DEBUG", "cache hit ratio={c}%"),
        ("INFO", "device poll ok online={d}/40"),
        ("WARN", "retry upload chunk seq={b} reason=timeout"),
        ("INFO", "log rotate ok size={e}MB"),
    )
    t = datetime.now().replace(second=0, microsecond=0) - timedelta(minutes=26)
    lines = []
    for _ in range(96):
        lvl, tpl = rnd.choice(tpls)
        msg = tpl.format(a=rnd.randint(3, 45), b=rnd.randint(10, 500),
                         c=rnd.randint(60, 99), d=rnd.randint(30, 40),
                         e=rnd.randint(1, 90))
        lines.append(f"[{t:%Y-%m-%d %H:%M:%S}] [{lvl}] "
                     f"{rnd.choice(hosts)}: {msg}")
        t += timedelta(seconds=rnd.randint(4, 18))
    return "\n".join(lines)


class MoyuReaderWidget(QWidget):
    """小说阅读器：txt / 粘贴 / 网页抓取 + 字号调节 + 老板键伪装"""

    _FONT_MIN, _FONT_MAX = 12, 32

    def __init__(self, parent=None):
        super().__init__(parent)
        st = load_moyu_state()
        self._font_size = min(max(int(st.get("reader_font_size") or 16),
                                  self._FONT_MIN), self._FONT_MAX)
        self._last_txt = str(st.get("reader_last_txt") or "")
        self._last_url = str(st.get("reader_last_url") or "")
        self._fetch_worker = None
        self._save_pending = False
        self._current_ratio = float(st.get("reader_scroll_ratio") or 0)
        self._prev_url = ""
        self._next_url = ""
        self._chapters = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(6)

        # ---- 工具栏 ----
        bar = QHBoxLayout()
        self._btn_open = PushButton("打开 TXT", self)
        self._btn_open.clicked.connect(self._open_txt)
        self._btn_paste = PushButton("粘贴阅读", self)
        self._btn_paste.clicked.connect(self._paste_read)
        self._btn_prev = PushButton(FluentIcon.UP, "上一章", self)
        self._btn_prev.setEnabled(False)
        self._btn_prev.setToolTip("跳转到上一章（自动解析）")
        self._btn_prev.clicked.connect(self._go_prev)
        self._btn_next = PushButton(FluentIcon.DOWN, "下一章", self)
        self._btn_next.setEnabled(False)
        self._btn_next.setToolTip("跳转到下一章（自动解析）")
        self._btn_next.clicked.connect(self._go_next)
        self._btn_toc = PushButton(FluentIcon.LIBRARY, "目录", self)
        self._btn_toc.setEnabled(False)
        self._btn_toc.setToolTip("当前页解析到的章节列表（目录页最佳）")
        self._btn_toc.clicked.connect(self._show_toc)
        self._btn_font_minus = ToolButton(FluentIcon.REMOVE, self)
        self._btn_font_minus.setToolTip("缩小字号")
        self._btn_font_minus.clicked.connect(lambda: self._adjust_font(-1))
        self._btn_font_plus = ToolButton(FluentIcon.ADD, self)
        self._btn_font_plus.setToolTip("放大字号")
        self._btn_font_plus.clicked.connect(lambda: self._adjust_font(1))
        self._lbl_font = BodyLabel(f"{self._font_size}pt", self)
        bar.addWidget(self._btn_open)
        bar.addWidget(self._btn_paste)
        bar.addWidget(self._btn_prev)
        bar.addWidget(self._btn_next)
        bar.addWidget(self._btn_toc)
        bar.addSpacing(12)
        bar.addWidget(self._btn_font_minus)
        bar.addWidget(self._lbl_font)
        bar.addWidget(self._btn_font_plus)
        bar.addStretch(1)
        self._lbl_source = CaptionLabel("", self)
        self._lbl_source.setStyleSheet("color: #8a8f98;")
        bar.addWidget(self._lbl_source)
        bar.addSpacing(12)
        self._btn_boss = PushButton(FluentIcon.HIDE, "老板键", self)
        self._btn_boss.setToolTip("切换伪装视图（Ctrl+`）")
        self._btn_boss.clicked.connect(self.toggle_boss)
        bar.addWidget(self._btn_boss)
        layout.addLayout(bar)

        # ---- 网页抓取行 ----
        url_row = QHBoxLayout()
        self._url_edit = LineEdit(self)
        self._url_edit.setPlaceholderText(
            "输入小说章节/目录页 URL，回车或点「抓取」；自动解析上下章与目录")
        self._url_edit.returnPressed.connect(self._fetch_web)
        self._btn_fetch = PushButton("抓取", self)
        self._btn_fetch.clicked.connect(self._fetch_web)
        url_row.addWidget(self._url_edit, 1)
        url_row.addWidget(self._btn_fetch)
        layout.addLayout(url_row)

        # ---- 正文 / 伪装视图（QStackedWidget） ----
        self._stack = QStackedWidget(self)

        self._browser = QTextBrowser(self._stack)
        self._browser.setReadOnly(True)
        self._browser.setOpenLinks(False)
        self._browser.setPlaceholderText(
            "打开 TXT / 粘贴文本 / 抓取网页开始阅读")
        self._browser.verticalScrollBar().valueChanged.connect(
            self._schedule_save)
        self._stack.addWidget(self._browser)

        boss_view = QWidget(self._stack)
        bv = QVBoxLayout(boss_view)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(4)
        bv_title = BodyLabel("系统日志（实时）", boss_view)
        bv_cap = CaptionLabel("自动采集 · 滚动刷新", boss_view)
        bv_cap.setStyleSheet("color: #8a8f98;")
        self._boss_log = QTextBrowser(boss_view)
        self._boss_log.setReadOnly(True)
        self._boss_log.setFont(QFont("Consolas", 10))
        self._boss_log.setStyleSheet(
            "QTextBrowser { background: #0b1015; color: #9fb3c8; "
            "border: none; }")
        self._boss_log.setPlainText(_fake_log_text())
        self._boss_log.verticalScrollBar().setValue(
            self._boss_log.verticalScrollBar().maximum())
        bv.addWidget(bv_title)
        bv.addWidget(bv_cap)
        bv.addWidget(self._boss_log, 1)
        self._stack.addWidget(boss_view)

        layout.addWidget(self._stack, 1)

        # ---- 老板键快捷键 ----
        self._boss_sc = QShortcut(QKeySequence("Ctrl+`"), self)
        self._boss_sc.setContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._boss_sc.activated.connect(self.toggle_boss)

        # ---- 恢复上次阅读 ----
        self._apply_font()
        if self._last_txt and os.path.isfile(self._last_txt):
            self._load_txt(self._last_txt, restore_scroll=True)
        elif self._last_url:
            # 延迟到窗口显示后再自动续读，避免启动即发网络请求阻塞界面
            QTimer.singleShot(300, self._restore_last_url)

    # ---------- 字号 ----------

    def _apply_font(self):
        """把当前字号套到正文样式并同步按钮可用态"""
        self._browser.document().setDefaultStyleSheet(
            f"body {{ font-size: {self._font_size}pt; line-height: 1.6; }}")
        self._lbl_font.setText(f"{self._font_size}pt")
        self._btn_font_minus.setEnabled(self._font_size > self._FONT_MIN)
        self._btn_font_plus.setEnabled(self._font_size < self._FONT_MAX)

    def _adjust_font(self, delta):
        """字号增减（限制在 MIN~MAX），变更后落盘"""
        new = min(max(self._font_size + delta, self._FONT_MIN),
                  self._FONT_MAX)
        if new == self._font_size:
            return
        self._font_size = new
        self._apply_font()
        save_moyu_state({"reader_font_size": self._font_size})

    # ---------- 正文载入 ----------

    def _set_text(self, text, source):
        """正文载入浏览器并更新来源标签"""
        self._browser.setPlainText(text)
        self._apply_font()  # setPlainText 重建 document 后需重新套用字号
        self._lbl_source.setText(source)

    @staticmethod
    def _read_text_file(path):
        """UTF-8 / GBK 自动探测读取"""
        with open(path, "rb") as f:
            data = f.read()
        for enc in ("utf-8", "gbk", "gb18030"):
            try:
                return data.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return data.decode("utf-8", errors="replace")

    def _load_txt(self, path, restore_scroll=False):
        """载入 txt：成功后记为最近文件，恢复场景按进度比例回滚"""
        try:
            text = self._read_text_file(path)
        except OSError as e:
            show_info_bar(f"{os.path.basename(path)}: {e.strerror or e}", "error",
                          title="打开失败", parent=self, duration=4000)
            return
        self._last_txt = path
        self._set_text(text, f"正在阅读：{os.path.basename(path)}")
        save_moyu_state({"reader_last_txt": path})
        if restore_scroll:
            self._restore_scroll(self._current_ratio)

    def _open_txt(self):
        """文件对话框选 txt（起点为上次打开位置）"""
        start = self._last_txt if os.path.isfile(self._last_txt) \
            else get_app_dir()
        path, _ = QFileDialog.getOpenFileName(
            self, "打开文本", start, "文本文件 (*.txt);;所有文件 (*)")
        if path:
            self._current_ratio = 0.0
            self._load_txt(path)

    def _paste_read(self):
        """粘贴阅读：弹窗贴文本后直接载入"""
        dlg = QDialog(self)
        dlg.setWindowTitle("粘贴阅读")
        dlg.resize(560, 420)
        v = QVBoxLayout(dlg)
        edit = PlainTextEdit(dlg)
        edit.setPlaceholderText("粘贴小说文本后点击「开始阅读」")
        v.addWidget(edit)
        h = QHBoxLayout()
        h.addStretch(1)
        btn_ok = PushButton("开始阅读", dlg)
        btn_cancel = PushButton("取消", dlg)
        h.addWidget(btn_ok)
        h.addWidget(btn_cancel)
        v.addLayout(h)
        btn_ok.clicked.connect(dlg.accept)
        btn_cancel.clicked.connect(dlg.reject)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        text = edit.toPlainText().strip()
        if not text:
            return
        self._current_ratio = 0.0
        self._set_text(text, "正在阅读：粘贴内容")

    # ---------- 网页抓取 ----------

    def _fetch_web(self):
        """「抓取」按钮：读取输入框 URL 发起加载"""
        self._load_url(self._url_edit.text().strip())

    def _load_url(self, url, quiet=False):
        """统一加载入口：校验 + 防重复 + 启动 worker（翻页/目录跳转复用）"""
        if self._fetch_worker is not None and self._fetch_worker.isRunning():
            if not quiet:
                show_info_bar("正在抓取中，请稍候", "warning",
                              title="提示", parent=self, duration=2000)
            return
        url = (url or "").strip()
        if not url:
            if not quiet:
                show_info_bar("请先输入网页 URL", "warning",
                              title="提示", parent=self, duration=2000)
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        self._url_edit.setText(url)
        self._url_edit.setEnabled(False)
        self._btn_fetch.setEnabled(False)
        self._btn_fetch.setText("抓取中…")
        w = _FetchWorker(url, self)
        self._fetch_worker = w
        w.ok.connect(lambda data: self._on_fetch_ok(data))
        w.failed.connect(lambda msg, u=url: self._on_fetch_failed(u, msg))
        # lambda 持有 w 引用，防止运行中被 GC 销毁触发 QThread qFatal
        w.finished.connect(lambda w=w: self._finish_fetch(w))
        w.start()

    def _restore_last_url(self):
        """启动自动续读上次章节（失败静默，不影响进入页面）"""
        self._load_url(self._last_url, quiet=True)

    def _finish_fetch(self, w):
        """抓取线程结束：恢复输入框/按钮并销毁 worker"""
        self._fetch_worker = None
        self._url_edit.setEnabled(True)
        self._btn_fetch.setEnabled(True)
        self._btn_fetch.setText("抓取")
        w.deleteLater()

    def _on_fetch_ok(self, data):
        """抓取成功：载入正文、更新导航按钮、记最近 URL 并提示字数"""
        url = data.get("url") or ""
        text = data.get("text") or ""
        self._current_ratio = 0.0
        title = (data.get("title") or "").strip()
        source = f"{title} · {_norm_host(url)}" if title else f"正在阅读：{url}"
        self._set_text(text, source)
        self._update_nav(data)
        save_moyu_state({"reader_last_url": url})
        show_info_bar(f"已提取 {len(text)} 字正文", "success",
                      title="抓取成功", parent=self, duration=2500)

    def _update_nav(self, data):
        """根据解析结果更新上一章/下一章/目录按钮状态"""
        self._prev_url = data.get("prev_url") or ""
        self._next_url = data.get("next_url") or ""
        self._chapters = data.get("chapters") or []
        self._btn_prev.setEnabled(bool(self._prev_url))
        self._btn_next.setEnabled(bool(self._next_url))
        self._btn_toc.setEnabled(bool(self._chapters))

    def _go_prev(self):
        """跳转上一章（解析出的链接）"""
        if self._prev_url:
            self._load_url(self._prev_url)

    def _go_next(self):
        """跳转下一章"""
        if self._next_url:
            self._load_url(self._next_url)

    def _show_toc(self):
        """章节目录弹窗：双击跳转，当前章节高亮"""
        if not self._chapters:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("章节目录")
        dlg.resize(420, 560)
        v = QVBoxLayout(dlg)
        lst = QListWidget(dlg)
        current = self._url_edit.text().strip()
        cur_key = _chapter_key(current)
        for idx, (title, url) in enumerate(self._chapters):
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, url)
            lst.addItem(item)
            if cur_key and _chapter_key(url) == cur_key:
                lst.setCurrentRow(idx)
        v.addWidget(lst, 1)
        h = QHBoxLayout()
        h.addStretch(1)
        btn_close = PushButton("关闭", dlg)
        h.addWidget(btn_close)
        v.addLayout(h)
        btn_close.clicked.connect(dlg.reject)
        lst.itemDoubleClicked.connect(lambda it: self._jump_from_toc(it, dlg))
        dlg.exec()

    def _jump_from_toc(self, item, dlg):
        """目录双击：关弹窗并加载选中章节"""
        url = item.data(Qt.ItemDataRole.UserRole) or ""
        dlg.accept()
        if url:
            self._load_url(url)

    def _on_fetch_failed(self, url, msg):
        """抓取失败：错误首行提示 + 「浏览器打开」动作兜底"""
        first = str(msg).splitlines()[0][:120] if msg else "未知错误"
        bar = show_info_bar(first, "error", title="抓取失败", parent=self, duration=0)
        btn = PushButton("浏览器打开", bar)
        btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        bar.addWidget(btn)
        bar.show()

    # ---------- 老板键 ----------

    def toggle_boss(self):
        """老板键：正文 ⇄ 假日志伪装视图切换（切走前先存进度）"""
        if self._stack.currentIndex() == 0:
            self._flush_save()  # 切走前保存阅读进度
            self._stack.setCurrentIndex(1)
            self._boss_log.setFocus()
        else:
            self._stack.setCurrentIndex(0)
            self._restore_scroll(self._current_ratio)
            self._browser.setFocus()

    # ---------- 滚动进度 ----------

    def _scroll_ratio(self):
        """当前滚动位置比例（0~1，用于跨会话续读）"""
        bar = self._browser.verticalScrollBar()
        span = bar.maximum() - bar.minimum()
        return (bar.value() - bar.minimum()) / span if span > 0 else 0.0

    def _restore_scroll(self, ratio):
        """延迟到布局完成后按比例恢复滚动位置"""
        def _do():
            bar = self._browser.verticalScrollBar()
            span = bar.maximum() - bar.minimum()
            if span > 0:
                bar.setValue(bar.minimum() + int(ratio * span))
        QTimer.singleShot(0, _do)

    def _schedule_save(self, *_):
        """滚动停止 1.2s 后落盘进度，避免高频写文件"""
        if self._save_pending:
            return
        self._save_pending = True
        QTimer.singleShot(1200, self._flush_save)

    def _flush_save(self):
        """落盘阅读进度（滚动比例 + 字号）"""
        self._save_pending = False
        self._current_ratio = round(self._scroll_ratio(), 4)
        save_moyu_state({
            "reader_scroll_ratio": self._current_ratio,
            "reader_font_size": self._font_size,
        })

    def hideEvent(self, e):
        """页签切走 / 面板关闭前保存阅读进度"""
        if self._stack.currentIndex() == 0:
            self._flush_save()
        super().hideEvent(e)
