# -*- coding: utf-8 -*-
"""摸鱼中心控件集合：2048 / 贪吃蛇 / 扫雷 / 小说阅读器

供 management_panel.py 的 GamePage 组装使用。四个玩法各自独立成控件，
共用一个页签容器；切走时由 GamePage 调 auto_pause/auto_resume 停表，
避免隐藏页的定时器继续空转。

状态持久化放在独立的 moyu_state.json，不写 settings.json。settings_mixin
会对 settings.json 做全量回写，外部写入会被下一次保存覆盖；摸鱼数据
丢失也不该牵连主业务配置。落盘用「先写 .tmp 再 os.replace」，进程中途
被杀不会留下半截 JSON。

贪吃蛇/扫雷的分难度成绩以 {难度: 成绩} 字典存（best_snake / best_mines），
旧版单值 int 自动归入当时的默认难度，读取端兼容两种形态。

小说阅读器网页抓取：站点规则/通用 XPath → trafilatura → html.parser
三级提取链，自动解析上一章/下一章与章节列表（目录页），支持续读。
网络请求放在 QThread 里，主线程只接收结果，抓取期间界面不会卡死。
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

from PySide6.QtCore import QPoint, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QFont,
                           QIntValidator, QKeySequence, QPainter, QPen,
                           QPolygon, QShortcut)
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout,
                               QListWidget, QListWidgetItem, QSizePolicy,
                               QStackedWidget, QTextBrowser, QVBoxLayout,
                               QWidget)
from qfluentwidgets import (BodyLabel, CaptionLabel, ComboBox, FluentIcon,
                            LineEdit, MessageBoxBase, PlainTextEdit,
                            PushButton, TitleLabel, ToolButton)

from core.app_paths import get_app_dir
from core.utils import show_info_bar

logger = logging.getLogger(__name__)


# ==================== 摸鱼状态持久化 ====================

def _state_path():
    """摸鱼状态文件路径（独立于 settings.json，防配置缓存全量回写覆盖）"""
    return os.path.join(get_app_dir(), "moyu_state.json")


def load_moyu_state() -> dict:
    """读摸鱼状态，文件缺失/损坏返回空字典

    这是启动路径的一部分，任何异常都只能降级为「没有数据」，不能让
    摸鱼功能把主窗口拦在启动阶段。
    """
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_moyu_state(patch: dict):
    """读-改-写合并落盘，失败静默，摸鱼状态丢失不影响主业务

    各玩法只提交自己那几个 key，先读后写才不会把别的玩法进度冲掉。
    临时文件加 os.replace 是为了原子替换，写到一半断电不会留下残缺 JSON。
    """
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


# ---------- 分难度成绩（同 key 下以 {难度: 成绩} 存一张表） ----------

def _level_bests(name: str, legacy_level: str) -> dict:
    """读分难度成绩表；旧版单值 int 视为 legacy_level 的历史纪录

    贪吃蛇改版前只有一档速度，best_snake 是裸整数；直接当字典用会
    AttributeError，故两种形态在此收敛为 {难度: 成绩}。
    """
    raw = load_moyu_state().get(name)
    table = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                table[str(k)] = int(v)
            except (TypeError, ValueError):
                continue
    elif isinstance(raw, (int, float)) and raw > 0:
        # 旧版只有一个速度档，裸整数就是当时默认档的纪录
        table[legacy_level] = int(raw)
    return table


def _submit_level_best(name: str, level: str, value: int, *, minimize: bool,
                       legacy_level: str) -> bool:
    """刷新单难度成绩并落盘，破纪录返回 True（旧单值一并升格为表）"""
    table = _level_bests(name, legacy_level)
    old = table.get(level)
    # 用时取小、得分取大；没纪录时 None 也算破纪录，0 秒同样是有效成绩
    if old is None or (value < old if minimize else value > old):
        table[level] = value
        save_moyu_state({name: table})
        return True
    return False


def _saved_level(key: str, levels: dict, default: str) -> str:
    """读上次选择的难度；值已不在配置表里时回退默认档

    难度表可能在版本间增删档位，落盘值先校验再使用，避免 ComboBox
    找不到对应索引而抛异常。
    """
    saved = str(load_moyu_state().get(key) or "")
    return saved if saved in levels else default


def _load_custom_mines() -> tuple:
    """读上次自定义规格 (列, 行, 雷数)；缺省/损坏回退默认值"""
    raw = load_moyu_state().get("mines_custom")
    # 旧值可能是任意 JSON 类型，解包失败就按默认规格处理
    try:
        cols, rows, mines = (int(v) for v in raw)
    except (TypeError, ValueError):
        return _MINES_LEVELS["custom"][1:]
    return (cols, rows, mines)


def _grid_geom(avail_w, avail_h, cols, rows, cell_min, cell_max, margin=6):
    """按可用宽高求格子边长与棋盘左上角，返回 (cell, x0, y0, w, h)

    边长受三重约束：塞得进可用区、不低于 cell_min（保证可看可点）、
    不高于 cell_max（大屏上不至于糊成巨型方块）。整块棋盘在控件内居中，
    绘制与鼠标命中测试共用这一套坐标，避免两处算式漂移。
    """
    # 先压到 cell_max 再抬到 cell_min：空间不足时宁可让棋盘顶出可用区，
    # 也不缩到点不准的尺寸
    cell = min((avail_w - margin * 2) // cols,
               (avail_h - margin * 2) // rows, cell_max)
    cell = max(cell, cell_min)
    w, h = cell * cols, cell * rows
    return (cell, max(0, (avail_w - w) // 2),
            max(0, (avail_h - h) // 2), w, h)


def _draw_overlay(p, x, y, w, h, title, subtitle):
    """棋盘通用遮罩层：半透明黑底 + 居中标题/副标题（贪吃蛇与扫雷共用）

    字号随盘面高度收放，窗口压矮时不至于把标题顶出遮罩。
    """
    p.setBrush(QBrush(QColor(0, 0, 0, 150)))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRect(x, y, w + 1, h + 1)
    p.setPen(QPen(QColor("#e5e7eb")))
    f1 = QFont("Segoe UI", 10, QFont.Weight.Bold)
    f1.setPixelSize(max(18, min(28, h // 12)))
    p.setFont(f1)
    p.drawText(x, y + h // 2 - 44, w + 1, 34,
               Qt.AlignmentFlag.AlignCenter, title)
    f2 = QFont("Segoe UI", 10)
    f2.setPixelSize(max(11, min(14, h // 24)))
    p.setFont(f2)
    p.setPen(QPen(QColor("#9ca3af")))
    p.drawText(x, y + h // 2 + 2, w + 1, 22,
               Qt.AlignmentFlag.AlignCenter, subtitle)


# ==================== 2048 ====================

# 方块数值 → (底色, 字色)。0 不画字，所以字色留空；超过 2048 的方块
# 由绘制处回退到深色底，不必在这里穷举
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
    """2048 棋盘：QPainter 绘制 + 键盘操作 + 合并逻辑

    必须拿 StrongFocus，否则方向键会被外层滚动容器当成滚屏操作吃掉。
    """

    SIZE = 4
    score_changed = Signal(int)  # 本次合并新增分数

    def __init__(self, parent=None):
        """初始化空盘：棋盘内容全部交给 reset 生成，避免两处初始化漂移"""
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # 420 是 4x4 布局的下限，再小单格不足 90px，四位数会挤出格子
        self.setMinimumSize(420, 420)
        self._grid = [[0] * self.SIZE for _ in range(self.SIZE)]
        self._over = False
        self._won = False       # 曾达成 2048
        self._won_cont = False  # 达成后选择继续
        # reset 内部已经落子并 update，这里不再重复初始化
        self.reset()

    # ---------- 游戏逻辑 ----------

    def reset(self):
        """重开一局：清盘并预置两个方块

        R 键与「重新开始」按钮共用这一入口，胜利/结束遮罩的状态也在这里
        一并清掉。
        """
        self._grid = [[0] * self.SIZE for _ in range(self.SIZE)]
        self._over = False
        self._won = False
        self._won_cont = False
        self._spawn()
        self._spawn()
        self.update()

    def _spawn(self):
        """随机空格生成新方块（90% 出 2，10% 出 4）

        4 的比例比原版低：高比例会让开局期望分虚高，节奏也更容易崩。
        """
        empty = [(r, c) for r in range(self.SIZE) for c in range(self.SIZE)
                 if self._grid[r][c] == 0]
        if not empty:
            return
        r, c = random.choice(empty)
        # 4 的出现率压到 10%，否则开局期望分虚高，节奏也更容易崩
        self._grid[r][c] = 4 if random.random() < 0.1 else 2

    @staticmethod
    def _merge_line(line, gained_box):
        """向左压缩合并一行，gained_box[0] 累加得分

        gained_box 用单元素列表是为了让静态方法能就地回传得分，免得多造
        一个返回值。每个方块一步内只合并一次，[2,2,2,2] 得到 [4,4] 而非 [8]。
        """
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
        """四方向移动：行/列归一为向左压缩合并，返回是否发生移动

        右/下方向先把序列反转，复用同一套向左逻辑，合并优先级才不会反。
        """
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
        """是否还能移动：有空格或任意相邻同值即未终局

        只查右/下两个方向即可覆盖全部四邻：左/上相邻的情况会被对方的
        右/下检查命中，省掉一半比较。
        """
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
        """键盘入口：R 重开优先于一切状态，其次是移动与胜利遮罩确认

        判断顺序就是优先级，先处理 over/won 会把 R 键吞掉。
        """
        # R 在任何状态下都可用，包括结算遮罩显示时
        key = e.key()
        if key == Qt.Key.Key_R:
            self.reset()
            return
        if self._over:
            return
        # 胜利遮罩期间只认「继续」键，避免误触直接改盘
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
            # 只有真的发生移动才落新块，否则按一下方向键就白送一个方块
            self._spawn()
            if not self._won and any(v >= 2048 for row in self._grid
                                     for v in row):
                self._won = True
            if not self._moves_available():
                self._over = True
            self.update()

    # ---------- 绘制 ----------

    def paintEvent(self, _e):
        """绘制棋盘、方块与结算遮罩

        所有坐标由 margin/gap/cell 现算，窗口缩放时格子等比变化；
        数字字号单独按位数调整，四位数才塞得进同一格。
        """
        p = QPainter(self)
        # 圆角与数字在 125% 缩放下锯齿明显，抗锯齿必须开
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 棋盘取短边做正方形，宽高不等时居中留白，不拉伸格子
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
                # 超过 2048 的方块走默认深色，不为此在色表里无限补值
                bg, fg = _TILE_COLORS.get(v, ("#3c3a32", "#f9f6f2"))
                p.setBrush(QBrush(QColor(bg)))
                p.drawRoundedRect(int(cx), int(cy), int(cell), int(cell),
                                  6, 6)
                if v:
                    p.setPen(QPen(QColor(fg)))
                    f = QFont("Segoe UI", 10, QFont.Weight.Bold)
                    digits = len(str(v))
                    # 位数越多字越小，四位数挤进同一格才不会溢出
                    f.setPixelSize(int(cell * (0.42 if digits <= 2
                                               else 0.34 if digits == 3
                                               else 0.27)))
                    p.setFont(f)
                    p.drawText(int(cx), int(cy), int(cell), int(cell),
                               Qt.AlignmentFlag.AlignCenter, str(v))

        # 覆盖层：胜利 / 游戏结束
        overlay = None
        if self._won and not self._won_cont:
            # 胜利用金色、结束用白色，两种遮罩一眼可分
            overlay = ("达成 2048！", "回车继续游戏 · R 重新开始")
        elif self._over:
            overlay = ("游戏结束", "按 R 重新开始")
        if overlay:
            p.setPen(Qt.PenStyle.NoPen)
            # 结束用半透明白、胜利用金色：浅色盘面上两种遮罩都能看清
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
    """2048 游戏：信息栏（得分/最高分/重开）+ 棋盘

    视图片签切进来时由 GamePage 调 setFocus，焦点必须落到棋盘，否则
    方向键没有接收者。
    """

    def __init__(self, parent=None):
        """信息栏在上、棋盘占满剩余高度；最高分直接读持久化值"""
        super().__init__(parent)
        self._score = 0
        # 历史最高分是裸整数；读坏了按 0 处理，不让坏值带崩整页
        self._best = int(load_moyu_state().get("best_2048") or 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        # 信息栏固定高度，剩余空间全部交给棋盘
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
        """页签切入时由 GamePage 调用，把键盘焦点转给棋盘

        容器本身不接收按键，必须显式转交一次。
        """
        super().setFocus(reason)
        self._board.setFocus(reason)

    def _on_score(self, gained):
        """累加得分，刷新最高分并落盘

        只在刷新纪录时写文件；得分每步都变，落盘次数因此被压到很少。
        """
        self._score += gained
        self._lbl_score.setText(f"得分：{self._score}")
        if self._score > self._best:
            self._best = self._score
            self._lbl_best.setText(f"最高分：{self._best}")
            save_moyu_state({"best_2048": self._best})

    def _restart(self):
        """重新开始：重开棋盘并清零得分"""
        # 得分计数在控件侧，棋盘 reset 不会动它，必须在这里同步清零
        self._board.reset()
        self._score = 0
        self._lbl_score.setText("得分：0")
        self._board.setFocus()


# ==================== 贪吃蛇 ====================

# 难度 → 走格节奏与计分：step 起步间隔ms，accel 每吃一个提速 ms，
# floor 间隔下限（防止后期失控不可玩），gain 单食得分（难度越高得分越高）。
# accel 为 0 即定速档，简单难度不加速，给手生的人留出反应时间。
_SNAKE_LEVELS = {
    "easy": {"label": "简单", "step": 170, "accel": 0, "floor": 170, "gain": 5},
    "normal": {"label": "普通", "step": 120, "accel": 2, "floor": 70, "gain": 10},
    "hard": {"label": "困难", "step": 85, "accel": 2, "floor": 50, "gain": 20},
}
# 旧版只有一个速度档，历史纪录统一归入普通档，迁移时不丢数据
_SNAKE_DEFAULT_LEVEL = "normal"


class _SnakeBoard(QWidget):
    """贪吃蛇棋盘：QTimer 驱动 + QPainter 绘制，难度决定走格节奏

    格子边长按可用宽高现算（见 _geom），面板给多大盘面就铺多大，
    不再钉死 22px 导致高窗口下半页空白。定时器以 self 为父对象，
    控件销毁时随之停掉，不需要额外析构处理。
    """

    COLS, ROWS = 24, 17
    CELL_MIN, CELL_MAX = 14, 30   # 边长区间：小面板保得住、大屏不失控
    score_changed = Signal(int)
    state_changed = Signal(str)  # idle / running / paused / over

    def __init__(self, parent=None, level=_SNAKE_DEFAULT_LEVEL):
        """按难度建盘：格子尺寸交给 _grid_geom，蛇身与食物由 _reset 生成

        难度只决定速度与初始长度，不改变盘面尺寸，切档无需重建控件。
        """
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # 只钉最小占位，多余空间由父布局的 stretch 交给本控件
        self.setMinimumSize(self.COLS * self.CELL_MIN + 2,
                            self.ROWS * self.CELL_MIN + 2)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self._level = level if level in _SNAKE_LEVELS else _SNAKE_DEFAULT_LEVEL
        self._timer = QTimer(self)
        self._timer.setInterval(_SNAKE_LEVELS[self._level]["step"])
        self._timer.timeout.connect(self._tick)
        self._queue = deque()          # 待执行转向，最多缓存两拍
        self._resume_on_show = False   # 因切页导致的暂停，切回时恢复
        self._reset()

    # ---------- 状态机 ----------

    def _reset(self):
        """重置蛇身/方向/食物与节奏，回到待开始态

        蛇身用 deque 且头在左端，走格只需 appendleft/pop，代价恒为 O(1)。
        """
        self._timer.stop()
        self._step = _SNAKE_LEVELS[self._level]["step"]
        self._timer.setInterval(self._step)
        cy = self.ROWS // 2
        self._snake = deque([(8, cy), (7, cy), (6, cy)])  # 头在前
        self._dir = (1, 0)
        self._queue.clear()
        self._state = "idle"
        self._food = self._spawn_food()

    def _spawn_food(self):
        """在非蛇身格随机放食物；占满全场返回 None

        None 是「没有空格」的合法状态，调用方据此判定通关，不按异常处理。
        """
        occupied = set(self._snake)
        free = [(x, y) for x in range(self.COLS) for y in range(self.ROWS)
                if (x, y) not in occupied]
        return random.choice(free) if free else None

    def state(self):
        """当前状态机状态（idle/running/paused/over）"""
        return self._state

    def level(self):
        """当前难度 key（easy/normal/hard）"""
        return self._level

    def set_level(self, level):
        """切难度：换节奏并回到待开始态，避开低难度续刷高难度纪录

        直接续玩会把当前长度带进新档，同一局分数在两档之间不可比。
        """
        if level not in _SNAKE_LEVELS or level == self._level:
            return
        self._level = level
        self._reset()
        self.state_changed.emit(self._state)
        self.update()

    def start(self):
        """开始/重开游戏并启动走格定时器

        暂停态不走这里，恢复走 resume；这里只处理开局与终局重开。
        """
        if self._state in ("idle", "over"):
            self._reset()
        self._state = "running"
        self._timer.start()
        self.state_changed.emit(self._state)
        self.update()

    def restart(self):
        """强制重开一局：运行中点「重开」也应清盘（只清分数会接着长蛇玩）"""
        self._reset()
        self._state = "running"
        self._timer.start()
        self.state_changed.emit(self._state)
        self.update()

    # 暂停只停表，蛇身与食物原样保留，恢复后沿用原节奏
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
        # 每拍只消费一个转向，连续按键不会在同一拍内拐两次弯
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
        # 新头先入队；吃到食物时不弹尾，等效于长一格
        self._snake.appendleft((nx, ny))
        if eating:
            self.score_changed.emit(_SNAKE_LEVELS[self._level]["gain"])
            self._speed_up()
            self._food = self._spawn_food()
            # 棋盘被蛇占满即通关；没有单独的 win 态，复用 over 遮罩
            if self._food is None:
                self._die()
                return
        else:
            self._snake.pop()
        self.update()

    def _speed_up(self):
        """吃食物按难度设定提速（accel=0 即定速档，floor 为上限速度）

        间隔不能无限减小，否则高分局会在几帧内撞墙，玩家来不及反应。
        """
        cfg = _SNAKE_LEVELS[self._level]
        if not cfg["accel"] or self._step <= cfg["floor"]:
            return
        self._step = max(cfg["floor"], self._step - cfg["accel"])
        self._timer.setInterval(self._step)

    def _die(self):
        """判死：停表并切 over 态

        死后必须立刻停表：继续走秒会让结算界面的用时一直增长。
        """
        self._state = "over"
        self._timer.stop()
        self.state_changed.emit(self._state)
        self.update()

    def _push_dir(self, d):
        """方向入队：忽略重复与 180° 反向，队列最深 2（兼顾手感与可预测性）

        反向判断以队尾而不是当前方向为准，否则连按「上左下」仍可能在一拍
        内掉头撞到自己。
        """
        last = self._queue[-1] if self._queue else self._dir
        if d == last:
            return
        if d[0] + last[0] == 0 and d[1] + last[1] == 0:
            return  # 禁止 180° 反向
        if len(self._queue) < 2:
            self._queue.append(d)

    # ---------- 事件 ----------

    def keyPressEvent(self, e):
        """键盘入口：方向键/WASD 转向，回车/空格在待开始或结束时开局

        转向键在 idle/over 下先开局再入队，省掉「先按开始」这一步。
        """
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
        # 方向键兼作开局/恢复：待开始或已结束时直接开新局
        if self._state in ("idle", "over"):
            self.start()
        if self._state == "paused":
            self.resume()
        self._push_dir(d)

    def hideEvent(self, e):
        """页面不可见时停表，避免后台空转

        只登记「因隐藏而暂停」，这样手动暂停的局在切回时不会被误恢复。
        """
        super().hideEvent(e)
        if self._state == "running":
            self._resume_on_show = True
            self.pause()

    def showEvent(self, e):
        super().showEvent(e)
        if self._resume_on_show:
            # 只恢复因隐藏而暂停的局，用户手动暂停的不会被误恢复
            self._resume_on_show = False
            self.resume()

    # ---------- 绘制 ----------

    def _geom(self):
        """当前尺寸下的 (格边长, 盘左上x, 盘左上y, 盘宽, 盘高)"""
        return _grid_geom(self.width(), self.height(), self.COLS, self.ROWS,
                          self.CELL_MIN, self.CELL_MAX)

    def paintEvent(self, _e):
        """绘制网格、食物、蛇身与状态遮罩

        与 _geom 共用坐标换算，窗口缩放后命中测试不会与画面错位；
        食物与蛇身按格边长等比内缩，大小屏观感一致。
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 与 _geom 共用同一套坐标，鼠标命中测试才不会与画面错位
        cell, x0, y0, w, h = self._geom()
        p.setPen(QPen(QColor("#232a35"), 1))
        p.setBrush(QBrush(QColor("#10141a")))
        p.drawRect(x0, y0, w + 1, h + 1)
        # 淡网格
        p.setPen(QPen(QColor(255, 255, 255, 10), 1))
        for i in range(1, self.COLS):
            gx = x0 + i * cell
            p.drawLine(gx, y0, gx, y0 + h)
        for i in range(1, self.ROWS):
            gy = y0 + i * cell
            p.drawLine(x0, gy, x0 + w, gy)
        # 食物与蛇身的内缩/圆角按边长等比，否则大格下看着挤成一团
        inset = max(3, cell // 5)
        if self._food:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor("#ff6b6b")))
            fx, fy = self._food
            p.drawEllipse(x0 + fx * cell + inset, y0 + fy * cell + inset,
                          cell - inset * 2, cell - inset * 2)
        # 蛇身
        pad = max(1, cell // 11)
        radius = max(3, cell // 6)
        for i, (x, y) in enumerate(self._snake):
            color = "#86efac" if i == 0 else "#4ade80"
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(color)))
            p.drawRoundedRect(x0 + x * cell + pad, y0 + y * cell + pad,
                              cell - pad * 2, cell - pad * 2, radius, radius)
        # 覆盖层：idle/paused/over 各一句提示，running 时不挡视野
        overlay = {
            "idle": ("贪吃蛇", "方向键 / WASD 开始并控制方向"),
            "paused": ("已暂停", "点击「继续」或方向键恢复"),
            "over": ("游戏结束", "按「开始」或方向键重新开始"),
        }.get(self._state)
        if overlay:
            _draw_overlay(p, x0, y0, w, h, overlay[0], overlay[1])


class SnakeWidget(QWidget):
    """贪吃蛇：信息栏（得分/最高分/难度/控制按钮）+ 棋盘

    最高分按难度分档记账（moyu_state.json 的 best_snake）。控件只负责
    信息栏与状态联动，走格规则全部在 _SnakeBoard 内。
    """

    def __init__(self, parent=None):
        """恢复上次难度与该档最高分，走格规则全部由 _SnakeBoard 负责"""
        super().__init__(parent)
        self._level = _saved_level("snake_level", _SNAKE_LEVELS,
                                   _SNAKE_DEFAULT_LEVEL)
        self._score = 0
        self._best = _level_bests("best_snake", _SNAKE_DEFAULT_LEVEL).get(
            self._level, 0)
        # 只有页签切换引起的暂停才自动恢复，手动暂停必须由用户自己继续
        self._auto_paused = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        self._lbl_score = BodyLabel("得分：0", self)
        self._lbl_best = BodyLabel(f"最高分：{self._best}", self)
        bar.addWidget(self._lbl_score)
        bar.addSpacing(16)
        bar.addWidget(self._lbl_best)
        bar.addSpacing(16)
        bar.addWidget(CaptionLabel("难度", self))
        self._combo_level = ComboBox(self)
        self._combo_level.setFixedWidth(88)
        for key, cfg in _SNAKE_LEVELS.items():
            # qfw 的 addItem 第二参是图标，userData 只能关键字传；位置传参
            # 会把字符串当图标用并抛类型错
            self._combo_level.addItem(cfg["label"], userData=key)
        # 先回显再连接，初始化不误触发切换逻辑
        self._combo_level.setCurrentIndex(
            list(_SNAKE_LEVELS).index(self._level))
        self._combo_level.currentIndexChanged.connect(self._on_level)
        bar.addWidget(self._combo_level)
        bar.addStretch(1)
        hint = CaptionLabel("方向键 / WASD 移动 · R 重开 · 难度越高节奏越快",
                            self)
        hint.setStyleSheet("color: #8a8f98;")
        bar.addWidget(hint)
        bar.addSpacing(12)
        self._btn_toggle = PushButton("开始", self)
        self._btn_toggle.clicked.connect(self._toggle)
        self._btn_restart = PushButton("重开", self)
        self._btn_restart.clicked.connect(self._restart)
        bar.addWidget(self._btn_toggle)
        bar.addWidget(self._btn_restart)
        layout.addLayout(bar)

        self._board = _SnakeBoard(self, level=self._level)
        self._board.score_changed.connect(self._on_score)
        self._board.state_changed.connect(self._on_state)
        # 棋盘自己会按可用宽高算格边长，给满 stretch 让它吃掉页高；
        # 不留弹性项的话多余高度会被可拉伸的信息栏子布局吞掉，
        # 标签在拉高的格子里居中——就是信息栏上方那条通栏空白
        layout.addWidget(self._board, 1)

    def setFocus(self, reason=Qt.FocusReason.OtherFocusReason):
        """页签切入时把焦点转给棋盘，保证方向键立即可用"""
        super().setFocus(reason)
        self._board.setFocus(reason)

    def _on_level(self, _index):
        """难度切换：重开棋局、刷新该档最高分并记住选择

        切档必须先清零本局得分，否则低难度攒的分数会带进高难度纪录。
        """
        level = self._combo_level.currentData()
        if not level:
            return
        self._level = level
        self._board.set_level(level)
        self._score = 0
        self._lbl_score.setText("得分：0")
        self._best = _level_bests("best_snake",
                                  _SNAKE_DEFAULT_LEVEL).get(level, 0)
        self._lbl_best.setText(f"最高分：{self._best}")
        save_moyu_state({"snake_level": level})
        self._board.setFocus()

    def _toggle(self):
        """开始/暂停/继续三态切换按钮

        按钮文案由 state_changed 驱动，这里只管动作，避免两处改文字。
        """
        # 按钮文案由 state_changed 驱动，这里不要自己改文字
        s = self._board.state()
        if s == "running":
            self._board.pause()
        elif s == "paused":
            self._board.resume()
        else:
            self._board.start()

    def _restart(self):
        """重开：清零得分并清盘重新开局

        得分必须先归零，否则上一局的分数会带进新一局的高分判定。
        """
        self._score = 0
        self._lbl_score.setText("得分：0")
        self._board.restart()
        self._board.setFocus()

    def _on_score(self, gained):
        """吃食物加分，刷新当前难度最高分并落盘"""
        self._score += gained
        self._lbl_score.setText(f"得分：{self._score}")
        if _submit_level_best("best_snake", self._level, self._score,
                              minimize=False,
                              legacy_level=_SNAKE_DEFAULT_LEVEL):
            self._best = self._score
            self._lbl_best.setText(f"最高分：{self._best}")

    def _on_state(self, state):
        """状态机变化 → 切换按钮文案（开始/暂停/继续）"""
        self._btn_toggle.setText(
            {"running": "暂停", "paused": "继续"}.get(state, "开始"))

    # ---------- 页签切换联动 ----------

    def auto_pause(self):
        """页签切走：运行中则暂停并记录，供切回恢复

        棋盘自己也会在 hideEvent 停表；这里多一层是为了区分「自动暂停」
        与「用户手动暂停」两种来源。
        """
        if self._board.state() == "running":
            self._auto_paused = True
            self._board.pause()

    def auto_resume(self):
        """页签切回：自动恢复被 auto_pause 暂停的局"""
        if self._auto_paused:
            self._auto_paused = False
            self._board.resume()


# ==================== 扫雷 ====================

# 难度 → (中文名, 列, 行, 雷数)：终极档 22x16 压到最小格 18px 也只需 396x288，
# 摸鱼面板内放得下；custom 档规格以弹窗输入为准，这里仅是首次默认值
_MINES_LEVELS = {
    "easy": ("初级", 9, 9, 10),
    "normal": ("中级", 12, 12, 26),
    "hard": ("高级", 16, 12, 45),
    "extreme": ("终极", 22, 16, 99),
    "custom": ("自定义", 12, 12, 30),
}
# 自定义棋盘的合法范围。雷数上限要留出至少 10 个安全格，否则首点
# 邻域连同禁区一起被雷占满，开局只能开出一格。
_MINES_CUSTOM_BOUNDS = ((5, 40), (5, 30))   # (列数区间, 行数区间)
_MINES_CUSTOM_SAFE = 10
_MINES_DEFAULT_LEVEL = "easy"
# 格子边长区间：小面板压到下限仍可点，大屏放大到上限即止
_MINES_CELL_MIN, _MINES_CELL_MAX = 18, 42

# 单元格状态：FLAG 是「隐藏 + 用户声称有雷」，所以取消插旗要回到
# HIDDEN，而不是再多一个空状态
_HIDDEN, _OPEN, _FLAG = 0, 1, 2
# 八邻域偏移
_MINES_ADJ = ((-1, -1), (0, -1), (1, -1), (-1, 0),
              (1, 0), (-1, 1), (0, 1), (1, 1))
# 数字 1-8 配色（与贪吃蛇同款深底亮字色系）
_MINES_NUM_COLORS = ("#60a5fa", "#4ade80", "#f87171", "#c084fc",
                     "#fb923c", "#2dd4bf", "#e5e7eb", "#9ca3af")


class _MinesBoard(QWidget):
    """扫雷棋盘：左键翻开、右键插旗、双击数字快速展开

    雷在首次翻开时才生成（避开首点及其邻域），保证第一下不踩雷且
    能自动展开一片；计时也从那一刻起跳。格子边长按可用宽高现算，
    绘制与命中测试共用 _geom 一套坐标，盘面缩放后不会点偏。
    """

    time_changed = Signal(int)    # 已用时秒
    left_changed = Signal(int)    # 剩余雷数（雷数 - 旗数）
    state_changed = Signal(str)   # idle / running / paused / over / win

    def __init__(self, parent=None, level=_MINES_DEFAULT_LEVEL):
        """按难度建盘：尺寸只钉最小值，多余空间由父布局 stretch 分配

        雷在首点后才生成，构造阶段只清空状态，不提前布雷。
        """
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._level = level if level in _MINES_LEVELS else _MINES_DEFAULT_LEVEL
        # 元组首项是中文名，棋盘只关心尺寸与雷数
        _, self._cols, self._rows, self._mines = _MINES_LEVELS[self._level]
        # 只钉最小占位，多余空间由父布局的 stretch 交给本控件
        self.setMinimumSize(self._cols * _MINES_CELL_MIN + 2,
                            self._rows * _MINES_CELL_MIN + 2)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)   # 秒表按 1s 走，与界面显示精度一致
        self._timer.timeout.connect(self._on_second)
        self._resume_on_show = False
        self._reset()

    # ---------- 状态机 ----------

    def _reset(self, keep_layout=False):
        """重开一局：清雷清旗，回到等待首点的态

        keep_layout=True（T 同图重试）保留已布的雷与提示数，只清
        翻开/旗标/计时，首点不再重新布雷，雷位与上一局完全一致。
        """
        self._timer.stop()
        self._state = "idle"
        self._seconds = 0
        self._grid = [[_HIDDEN] * self._cols for _ in range(self._rows)]
        if not keep_layout:
            self._numbers = [[0] * self._cols for _ in range(self._rows)]
            self._mineset = set()
            self._planted = False
        self._flags = 0
        self._opened = 0
        self._boom = None
        # 重开即刷新信息栏，信号兜底比每个调用点各自 setText 可靠
        self.time_changed.emit(0)
        self.left_changed.emit(self._mines)
        self.state_changed.emit(self._state)
        self.update()

    def restart(self):
        """重开（按钮 / R 键）：换一份新雷图"""
        # 不带 keep_layout，_planted 清 False，下一次首点会重新布雷
        self._reset()

    def retry(self):
        """同图重试（T 键）：仅踩雷后有效，雷位与提示数不变

        与 R 重开的区别：R 换新局（重新随机布雷），T 保留刚才那份
        雷图再打一遍，适合差一步就通关想验证同一思路的场景。
        """
        # 只有踩雷结束才允许同图重试，胜利后没有「同一局」可复用
        if self._state != "over":
            return
        self._reset(keep_layout=True)

    def state(self):
        """当前状态机状态（idle/running/paused/over/win）"""
        return self._state

    def level(self):
        """当前难度 key（easy/normal/hard/extreme/custom）"""
        return self._level

    def seconds(self):
        """本局已用秒数（胜利计时与破纪录判定用）"""
        return self._seconds

    def set_level(self, level):
        """切难度：换棋盘尺寸与雷数并重开（不保留半局）"""
        if level not in _MINES_LEVELS or level == self._level:
            return
        self._level = level
        _, self._cols, self._rows, self._mines = _MINES_LEVELS[level]
        self.setMinimumSize(self._cols * _MINES_CELL_MIN + 2,
                            self._rows * _MINES_CELL_MIN + 2)
        self._reset()

    def set_custom(self, cols, rows, mines):
        """自定义规格：夹取合法范围后换盘重开（自定义局不计最快纪录）

        夹取兜底在棋盘侧做一份，调用侧弹窗校验只拦明显非法输入。
        """
        (c_lo, c_hi), (r_lo, r_hi) = _MINES_CUSTOM_BOUNDS
        cols = min(max(int(cols), c_lo), c_hi)
        rows = min(max(int(rows), r_lo), r_hi)
        # 三重夹取在棋盘侧再做一遍：调用方可能绕过弹窗直接传参
        mines = min(max(int(mines), 1), cols * rows - _MINES_CUSTOM_SAFE)
        self._level = "custom"
        self._cols, self._rows, self._mines = cols, rows, mines
        self.setMinimumSize(self._cols * _MINES_CELL_MIN + 2,
                            self._rows * _MINES_CELL_MIN + 2)
        self._reset()

    def _begin(self):
        """首次操作起表；暂停中被点击则顺带恢复计时

        扫雷没有「开始」按钮，任何一次有效操作都是开局信号，这样操作
        路径只有一条，不必在按钮与棋盘之间同步状态。
        """
        if self._state == "idle":
            self._state = "running"
            self._timer.start()
            self.state_changed.emit(self._state)
        elif self._state == "paused":
            self.resume()

    def pause(self):
        """运行中暂停（停表保局面）"""
        if self._state == "running":
            self._state = "paused"
            self._timer.stop()
            self.state_changed.emit(self._state)
            self.update()

    def resume(self):
        """从暂停恢复计时"""
        if self._state == "paused":
            self._state = "running"
            self._timer.start()
            self.state_changed.emit(self._state)
            self.update()

    def _on_second(self):
        """秒表 tick：只累加并广播，界面文案由外层标签负责"""
        self._seconds += 1
        self.time_changed.emit(self._seconds)

    # ---------- 游戏逻辑 ----------

    def _in_board(self, x, y):
        """格坐标是否在盘内；洪水与邻域计算都要先过这一关"""
        return 0 <= x < self._cols and 0 <= y < self._rows

    def _plant(self, safe):
        """首点后布雷：避开首点及其邻域，保证开局可展开一片

        禁区最多 9 格；雷数超过可布格数时退化成只避开首点，否则
        random.sample 会直接抛 ValueError。
        """
        banned = {safe}
        banned.update((safe[0] + dx, safe[1] + dy) for dx, dy in _MINES_ADJ)
        cells = [(x, y) for y in range(self._rows) for x in range(self._cols)
                 if (x, y) not in banned]
        if len(cells) < self._mines:  # 雷密到禁区容不下时只避开首点
            cells = [(x, y) for y in range(self._rows) for x in range(self._cols)
                     if (x, y) != safe]
        self._mineset = set(random.sample(cells, self._mines))
        self._numbers = [[self._adj_count(x, y) for x in range(self._cols)]
                         for y in range(self._rows)]
        self._planted = True

    def _adj_count(self, x, y):
        """周雷数：扫雷格子的数字提示"""
        return sum(1 for dx, dy in _MINES_ADJ if (x + dx, y + dy) in self._mineset)

    def _neighbors(self, x, y):
        """八邻域并裁掉盘外坐标；边缘格与角格因此不会越界"""
        return [(x + dx, y + dy) for dx, dy in _MINES_ADJ
                if self._in_board(x + dx, y + dy)]

    def reveal(self, x, y):
        """左键翻开：首点布雷并起表，踩雷判负，否则洪水展开"""
        if self._state in ("over", "win") or not self._in_board(x, y):
            return
        if self._grid[y][x] != _HIDDEN:
            return
        self._begin()
        # 先起表再布雷：首点安全区依赖这次点击坐标，计时也从这次点击算
        if not self._planted:
            self._plant((x, y))
        if (x, y) in self._mineset:
            self._boom = (x, y)
            self._lose()
            return
        self._flood(x, y)
        self._check_win()
        self.update()

    def _flood(self, x, y):
        """从 (x,y) 洪水铺开：数字格止步，0 格继续扩散邻域

        用显式栈而不是递归，40x30 的自定义盘递归会顶到 Python 栈上限。
        """
        stack = deque([(x, y)])
        while stack:
            cx, cy = stack.popleft()
            if self._grid[cy][cx] != _HIDDEN or (cx, cy) in self._mineset:
                continue
            self._grid[cy][cx] = _OPEN
            self._opened += 1
            if self._numbers[cy][cx]:
                continue
            for nx, ny in self._neighbors(cx, cy):
                if self._grid[ny][nx] == _HIDDEN:
                    stack.append((nx, ny))

    def toggle_flag(self, x, y):
        """右键插旗/取消旗：只在未翻开的格上生效"""
        if self._state in ("over", "win") or not self._in_board(x, y):
            return
        st = self._grid[y][x]
        if st == _OPEN:
            return
        self._begin()
        # FLAG 与 HIDDEN 只差一个标记位，翻转时同步维护剩余雷数
        self._grid[y][x] = _HIDDEN if st == _FLAG else _FLAG
        self._flags += -1 if st == _FLAG else 1
        self.left_changed.emit(self._mines - self._flags)
        self.update()

    def chord(self, x, y):
        """双击已翻开的数字：邻旗数吻合时一次展开其余邻格（旗标错会踩雷）"""
        if self._state != "running" or not self._in_board(x, y):
            return
        n = self._numbers[y][x]
        # 空白格没有数字可对，先排除，免得 0 == 旗数时误触展开
        if self._grid[y][x] != _OPEN or not n:
            return
        around = self._neighbors(x, y)
        if sum(1 for nx, ny in around
               if self._grid[ny][nx] == _FLAG) != n:
            return
        for nx, ny in around:
            if self._grid[ny][nx] != _HIDDEN:
                continue
            if (nx, ny) in self._mineset:
                self._boom = (nx, ny)
                self._lose()
                return
            self._flood(nx, ny)
        self._check_win()
        self.update()

    def auto_flag(self):
        """自动标雷（按钮 / A 键）：单格推理标出必然是雷的格子

        规则：已翻开数字格的「未翻开邻格数」恰好等于「剩余雷数提示」
        （数字 - 周围旗数）时，这些邻格必然全是雷，逐一插旗。
        只做一轮全盘扫描（无链式/矩阵推理），标完顺手检查是否收尾胜利；
        已标对的旗不会重复计，返回本轮新插旗数（无可标为 0）。
        """
        # idle 时棋盘还没布雷，任何推理都没有依据，直接返回 0
        if self._state not in ("running", "paused"):
            return 0
        added = 0
        for y in range(self._rows):
            for x in range(self._cols):
                if self._grid[y][x] != _OPEN:
                    continue
                n = self._numbers[y][x]
                if not n:
                    continue
                around = self._neighbors(x, y)
                hidden = [(nx, ny) for nx, ny in around
                          if self._grid[ny][nx] == _HIDDEN]
                flags = sum(1 for nx, ny in around
                            if self._grid[ny][nx] == _FLAG)
                if hidden and n - flags == len(hidden):
                    for nx, ny in hidden:
                        self._grid[ny][nx] = _FLAG
                        added += 1
        if added:
            self._flags += added
            self.left_changed.emit(self._mines - self._flags)
            self._check_win()
            self.update()
        return added

    def _lose(self):
        """踩雷：停表并摊开全盘雷与错旗"""
        # _boom 记录被踩的那颗，绘制时单独标红；其余雷由绘制层按状态摊开
        self._state = "over"
        self._timer.stop()
        self.state_changed.emit(self._state)
        self.update()

    def _check_win(self):
        """翻开格数达总数-雷数即胜利：剩余雷自动补旗收尾

        判胜只看翻开数，不二次校验旗子是否全对；旗只是玩家标记，
        翻完所有安全格就等价于排完雷。
        """
        if self._state in ("over", "win"):
            return
        if self._opened != self._cols * self._rows - self._mines:
            return
        self._state = "win"
        self._timer.stop()
        for y in range(self._rows):
            for x in range(self._cols):
                if self._grid[y][x] == _HIDDEN and (x, y) in self._mineset:
                    self._grid[y][x] = _FLAG
        self._flags = self._mines
        self.left_changed.emit(0)
        self.state_changed.emit(self._state)

    # ---------- 几何与事件 ----------

    def _geom(self):
        """当前尺寸下的 (格边长, 盘左上x, 盘左上y, 盘宽, 盘高)"""
        return _grid_geom(self.width(), self.height(), self._cols, self._rows,
                          _MINES_CELL_MIN, _MINES_CELL_MAX)

    def _hit(self, pos):
        """鼠标坐标 → 格子坐标；落在盘外返回 None"""
        # 先减去棋盘偏移再判边界，点在盘外留白处不能算到边角格
        cell, x0, y0, w, h = self._geom()
        gx, gy = int(pos.x()) - x0, int(pos.y()) - y0
        if not (0 <= gx < w and 0 <= gy < h):
            return None
        return gx // cell, gy // cell

    def keyPressEvent(self, e):
        """快捷键：R 重开、T 同图重试、A 自动标雷，其余交回 Qt 默认处理"""
        if e.key() == Qt.Key.Key_R:
            self._reset()
            return
        if e.key() == Qt.Key.Key_T:
            self.retry()
            return
        if e.key() == Qt.Key.Key_A:
            self.auto_flag()
            return
        super().keyPressEvent(e)

    def mousePressEvent(self, e):
        """左键翻开、右键插旗；中键与侧键直接忽略，不能落进插旗分支"""
        left = e.button() == Qt.MouseButton.LeftButton
        # 中键/侧键直接忽略，不能让「非左键」被当成右键插旗
        if not left and e.button() != Qt.MouseButton.RightButton:
            return
        cell_pos = self._hit(e.position())
        if cell_pos is None:
            return
        if left:
            self.reveal(*cell_pos)
        else:
            self.toggle_flag(*cell_pos)

    def mouseDoubleClickEvent(self, e):
        """双击已开数字快速展开；双击前不会先走一次插旗，故无需防抖"""
        if e.button() != Qt.MouseButton.LeftButton:
            return
        cell_pos = self._hit(e.position())
        if cell_pos is not None:
            self.chord(*cell_pos)

    def hideEvent(self, e):
        """页面不可见时停表，避免后台空跑计时"""
        super().hideEvent(e)
        if self._state == "running":
            self._resume_on_show = True
            self.pause()

    def showEvent(self, e):
        super().showEvent(e)
        if self._resume_on_show:
            # 与贪吃蛇同一语义：只有因隐藏而暂停的局才自动恢复
            self._resume_on_show = False
            self.resume()

    # ---------- 绘制 ----------

    def paintEvent(self, _e):
        """绘制全部格子与状态遮罩；终局时摊开所有雷

        逐格内容交给 _draw_cell，这里只负责盘底、整盘状态与遮罩，
        保证绘制路径只有一条。
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cell, x0, y0, w, h = self._geom()
        p.setPen(QPen(QColor("#232a35"), 1))
        p.setBrush(QBrush(QColor("#10141a")))
        p.drawRect(x0, y0, w + 1, h + 1)
        # 失败时把全盘雷摊开，成功时只亮己方旗标
        dead = self._state == "over"
        for y in range(self._rows):
            for x in range(self._cols):
                self._draw_cell(p, x, y, dead, cell, x0, y0)
        overlay = {
            "idle": ("扫雷", "左键翻开 · 右键插旗 · 首点必定安全"),
            "paused": ("已暂停", "点击棋盘或「继续」恢复计时"),
            "over": ("踩雷了", "按 R 换新局 · 按 T 同图重试"),
            "win": ("排雷成功", f"用时 {self._seconds} 秒 · 按 R 重开"),
        }.get(self._state)
        if overlay:
            _draw_overlay(p, x0, y0, w, h, overlay[0], overlay[1])

    def _draw_cell(self, p, x, y, dead, cell, x0, y0):
        """单格绘制：未开=凸起方块，已开=数字/雷，旗=小旗（错旗画红叉）

        尺寸均按 cell 等比，不再假定 26px 格边长。
        """
        # 每格外缩 1px 做缝，网格线自然显现，不必逐格描边
        px, py = x0 + x * cell + 1, y0 + y * cell + 1
        size = cell - 2
        st = self._grid[y][x]
        is_mine = (x, y) in self._mineset
        if st == _HIDDEN and not (dead and is_mine):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor("#3b4553")))
            p.drawRoundedRect(px, py, size, size, 3, 3)
            return
        # 以下内容一律先铺开格底（雷/错旗同底层）
        p.setPen(QPen(QColor(255, 255, 255, 14), 1))
        p.setBrush(QBrush(QColor("#7f1d1d") if (x, y) == self._boom
                          else QColor("#151a22")))
        p.drawRect(px, py, size, size)
        if st == _FLAG:
            if dead and not is_mine:
                cut = max(4, cell * 27 // 100)   # 错旗红叉的内缩
                p.setPen(QPen(QColor("#f87171"), 2))
                p.drawLine(px + cut, py + cut, px + size - cut, py + size - cut)
                p.drawLine(px + size - cut, py + cut, px + cut, py + size - cut)
            else:
                self._draw_flag(p, px, py, cell, mine=dead)
            return
        if is_mine:
            self._draw_mine(p, px, py, cell)
            return
        n = self._numbers[y][x]
        if n:
            f = QFont("Segoe UI", 10, QFont.Weight.Bold)
            f.setPixelSize(max(11, cell * 15 // 26))
            p.setFont(f)
            p.setPen(QPen(QColor(_MINES_NUM_COLORS[n - 1])))
            p.drawText(px, py, size, size, Qt.AlignmentFlag.AlignCenter, str(n))

    @staticmethod
    def _draw_flag(p, px, py, cell, mine=False):
        """旗子：橙色三角旗面 + 旗杆；终局已标对的雷改用中性灰

        所有尺寸从 cell 推导，格子缩到 18px 时旗面仍能看清。
        """
        cx = px + cell // 2
        p.setPen(QPen(QColor("#cbd5e1"), 2))
        p.drawLine(cx, py + max(4, cell * 6 // 26),
                   cx, py + cell - max(6, cell * 9 // 26))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#9ca3af") if mine else QColor("#f97316")))
        p.drawPolygon(QPolygon([
            QPoint(cx, py + max(3, cell * 4 // 26)),
            QPoint(cx + max(5, cell * 8 // 26), py + max(5, cell * 8 // 26)),
            QPoint(cx, py + max(7, cell * 12 // 26))]))

    @staticmethod
    def _draw_mine(p, px, py, cell):
        """雷：中心圆 + 八向短刺（均按格边长等比）

        坐标全部走整数除法，高 DPI 下线条不会因半像素偏移发虚。
        """
        cx, cy = px + cell // 2, py + cell // 2
        spike = max(5, cell * 9 // 26)
        diag = spike * 7 // 9
        p.setPen(QPen(QColor("#cbd5e1"), 2))
        for dx, dy in ((0, -spike), (0, spike), (-spike, 0), (spike, 0),
                       (-diag, -diag), (diag, -diag), (-diag, diag),
                       (diag, diag)):
            p.drawLine(cx + dx * 3 // 5, cy + dy * 3 // 5, cx + dx, cy + dy)
        ball = max(5, cell * 11 // 26)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#cbd5e1")))
        p.drawEllipse(cx - ball // 2, cy - ball // 2, ball, ball)


class _CustomMinesDialog(MessageBoxBase):
    """自定义棋盘规格弹窗：宽（列）/ 高（行）/ 雷数三栏输入

    确认时经 validate() 收敛为合法整数三元组存入 self.values，
    取消或校验不过时保持 None，调用方据此决定应用或回退原难度。
    """

    def __init__(self, current, parent=None):
        """按上次规格预填三个输入框；取消时 values 保持 None"""
        super().__init__(parent)
        # None 表示用户取消或校验未过；三元组才是可用的规格
        self.values = None
        self.widget.setMinimumWidth(380)
        ttl = TitleLabel("自定义棋盘", self)
        self.viewLayout.addWidget(ttl)
        (c_lo, c_hi), (r_lo, r_hi) = _MINES_CUSTOM_BOUNDS
        tip = CaptionLabel(
            f"宽 {c_lo}~{c_hi} 列，高 {r_lo}~{r_hi} 行，"
            f"雷数不超过「列 × 行 - {_MINES_CUSTOM_SAFE}」", self)
        self.viewLayout.addWidget(tip)
        self._edits = []
        for label, val in zip(("宽（列）", "高（行）", "雷数"), current):
            row = QHBoxLayout()
            cap = CaptionLabel(label, self)
            cap.setFixedWidth(64)
            row.addWidget(cap)
            edit = LineEdit(self)
            edit.setText(str(int(val)))
            # 只挡非数字输入，范围校验留给 validate，便于一次提示全部问题
            edit.setValidator(QIntValidator(0, 9999, edit))
            row.addWidget(edit, 1)
            self.viewLayout.addLayout(row)
            self._edits.append(edit)
        self.yesButton.setText("开始")
        self.cancelButton.setText("取消")

    def validate(self) -> bool:
        """范围校验：不过则弹窗保持打开、不丢输入

        把校验放在确认回调里而不是输入时逐格拦截，是为了让用户一次看到
        完整的规格约束，而不是修一格再撞下一个错。
        """
        try:
            cols, rows, mines = (int(e.text()) for e in self._edits)
        except ValueError:
            show_info_bar("请把宽、高、雷数都填成整数", "warning",
                          title="自定义棋盘", parent=self, duration=2500)
            return False
        (c_lo, c_hi), (r_lo, r_hi) = _MINES_CUSTOM_BOUNDS
        max_mines = cols * rows - _MINES_CUSTOM_SAFE
        if not (c_lo <= cols <= c_hi and r_lo <= rows <= r_hi
                and 1 <= mines <= max_mines):
            show_info_bar(
                f"宽需在 {c_lo}~{c_hi}、高需在 {r_lo}~{r_hi}，"
                f"当前规格下雷数需在 1~{max_mines} 之间", "warning",
                title="自定义棋盘", parent=self, duration=3500)
            return False
        self.values = (cols, rows, mines)
        return True


class MinesweeperWidget(QWidget):
    """扫雷：信息栏（雷数/用时/最快纪录/难度/控制）+ 棋盘

    最快用时按难度分档记账（moyu_state.json 的 best_mines）。自定义局
    不参与纪录，因为不同规格的盘面没有可比性。
    """

    def __init__(self, parent=None):
        """恢复难度/自定义规格/该档纪录，并把三个信号接到信息栏"""
        super().__init__(parent)
        self._level = _saved_level("mines_level", _MINES_LEVELS,
                                   _MINES_DEFAULT_LEVEL)
        self._custom = _load_custom_mines()   # 自定义档 (列, 行, 雷数)
        # None 表示该档暂无纪录；0 秒同样是有效成绩，不能用 falsy 判断
        self._best = _level_bests("best_mines", _MINES_DEFAULT_LEVEL).get(
            self._level)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        # 信息栏从左到右按使用频率排：剩余雷数、用时、纪录、难度、操作
        bar = QHBoxLayout()
        self._lbl_left = BodyLabel(f"剩余雷数：{self._mines_of(self._level)}",
                                   self)
        self._lbl_time = BodyLabel("用时：0 秒", self)
        self._lbl_best = BodyLabel(self._best_text(), self)
        bar.addWidget(self._lbl_left)
        bar.addSpacing(16)
        bar.addWidget(self._lbl_time)
        bar.addSpacing(16)
        bar.addWidget(self._lbl_best)
        bar.addSpacing(16)
        bar.addWidget(CaptionLabel("难度", self))
        self._combo_level = ComboBox(self)
        self._combo_level.setFixedWidth(88)
        for key, cfg in _MINES_LEVELS.items():
            # qfw addItem 第二参是 icon，userData 必须关键字传参
            self._combo_level.addItem(cfg[0], userData=key)
        # 先回显再连接，初始化不误触发切换逻辑
        self._combo_level.setCurrentIndex(
            list(_MINES_LEVELS).index(self._level))
        self._combo_level.currentIndexChanged.connect(self._on_level)
        bar.addWidget(self._combo_level)
        bar.addStretch(1)
        hint = CaptionLabel("左键翻开 · 右键插旗 · 双击数字展开邻域 · R 重开 · T 同图重试 · A 自动标雷", self)
        hint.setStyleSheet("color: #8a8f98;")
        bar.addWidget(hint)
        bar.addSpacing(12)
        self._btn_toggle = PushButton("暂停", self)
        self._btn_toggle.setEnabled(False)
        self._btn_toggle.clicked.connect(self._toggle)
        self._btn_restart = PushButton("重开", self)
        self._btn_restart.clicked.connect(self._restart)
        self._btn_auto = PushButton("自动标雷", self)
        # 说明推理强度，避免用户以为它能解整盘
        self._btn_auto.setToolTip("单格推理插旗：数字格的未开邻格数恰等于剩余雷数提示时全标（也可按 A）")
        self._btn_auto.clicked.connect(self._auto_flag)
        bar.addWidget(self._btn_toggle)
        bar.addWidget(self._btn_auto)
        bar.addWidget(self._btn_restart)
        layout.addLayout(bar)

        self._board = _MinesBoard(self, level=self._level)
        self._board.time_changed.connect(self._on_time)
        self._board.left_changed.connect(self._on_left)
        self._board.state_changed.connect(self._on_state)
        if self._level == "custom":
            # 按上次规格重建棋盘；_reset 会经 left_changed 刷新剩余雷数，
            # 这里不需要再手动改标签
            self._board.set_custom(*self._custom)
        # 同贪吃蛇：棋盘自适配格边长，给满 stretch 把页高交给盘面
        layout.addWidget(self._board, 1)

    def setFocus(self, reason=Qt.FocusReason.OtherFocusReason):
        """页签切入时由 GamePage 调用，把键盘焦点转给棋盘"""
        super().setFocus(reason)
        self._board.setFocus(reason)

    def _mines_of(self, level):
        """档位对应雷数（自定义档用上次输入的规格）"""
        # 自定义档在 _MINES_LEVELS 里的雷数只是占位，真实值在 self._custom
        return self._custom[2] if level == "custom" else _MINES_LEVELS[level][3]

    def _best_text(self):
        """纪录文案：自定义局不计、无纪录、有纪录三种形态"""
        if self._level == "custom":
            return "最快：自定义不计纪录"
        return ("最快：暂无纪录" if self._best is None
                else f"最快：{self._best} 秒")

    def _on_level(self, _index):
        """难度切换：换棋盘重开，刷新该档纪录并记住选择

        自定义档先弹规格弹窗（预填上次输入），取消则把下拉框拨回原难度；
        自定义局不参与最快纪录（不同规格没有可比性）。
        """
        level = self._combo_level.currentData()
        if not level:
            return
        if level == "custom":
            dlg = _CustomMinesDialog(self._custom, self.window())
            dlg.exec()
            if dlg.values is None:
                # 取消时把下拉框拨回原档；必须屏蔽信号，否则又会触发一次
                # 切换逻辑并弹出同一个弹窗
                idx = list(_MINES_LEVELS).index(self._level)
                self._combo_level.blockSignals(True)
                self._combo_level.setCurrentIndex(idx)
                self._combo_level.blockSignals(False)
                return
            self._custom = dlg.values
            self._level = "custom"
            self._board.set_custom(*self._custom)
            save_moyu_state({"mines_level": "custom",
                             "mines_custom": list(self._custom)})
        else:
            self._level = level
            self._board.set_level(level)
            save_moyu_state({"mines_level": level})
        self._best = _level_bests("best_mines",
                                  _MINES_DEFAULT_LEVEL).get(self._level)
        self._lbl_best.setText(self._best_text())
        self._board.setFocus()

    def _toggle(self):
        """暂停/继续：扫雷无“开始”态，首次翻开自动起表

        idle/over/win 下按钮本就禁用，这里不做兜底分支。
        """
        s = self._board.state()
        if s == "running":
            self._board.pause()
        elif s == "paused":
            self._board.resume()

    def _restart(self):
        """重开：换一份新雷图（R 键同效），随后把焦点还给棋盘

        焦点必须还回去，否则 R/T/A 快捷键在点过按钮后失效。
        """
        self._board.restart()
        self._board.setFocus()

    def _auto_flag(self):
        """自动标雷按钮：推理插旗后焦点还给棋盘（保证 R/T/A 继续可用）"""
        self._board.auto_flag()
        self._board.setFocus()

    def _on_time(self, seconds):
        """计时信号 → 信息栏文本；只做展示，不参与纪录判定

        纪录判定直接用棋盘累计的秒数，避免解析界面文本。
        """
        self._lbl_time.setText(f"用时：{seconds} 秒")

    def _on_left(self, left):
        """剩余雷数信号 → 信息栏；旗数变化与胜利补旗都会走这里

        胜利时统一补旗并把剩余数归零，信息栏因此始终是真实值。
        """
        self._lbl_left.setText(f"剩余雷数：{left}")

    def _on_state(self, state):
        """状态机变化：按钮联动 + 胜利时按难度刷新最快纪录（自定义局不计）

        提交的是棋盘计时器累计值，而不是界面文本解析出来的数字。
        """
        self._btn_toggle.setText("继续" if state == "paused" else "暂停")
        self._btn_toggle.setEnabled(state in ("running", "paused"))
        if state == "win" and self._level != "custom":
            used = self._board.seconds()
            if _submit_level_best("best_mines", self._level, used,
                                  minimize=True,
                                  legacy_level=_MINES_DEFAULT_LEVEL):
                self._best = used
                self._lbl_best.setText(self._best_text())

    # ---------- 页签切换联动 ----------

    def auto_pause(self):
        """页签切走：计时中则暂停并记录，供切回恢复"""
        # 棋盘 pause 自带「仅 running 时生效」，这里无需再判状态
        self._board.pause()

    def auto_resume(self):
        """页签切回：恢复被 auto_pause 暂停的局"""
        self._board.resume()


# ==================== 小说阅读器 ====================

class _TextExtractor(HTMLParser):
    """html.parser 提取纯文本：剔除 script/style，块级标签转换行

    仅作为 trafilatura 提取失败/结果过短时的兜底方案。skip_depth 用计数
    而不是布尔，是因为 script 里还可能嵌套模板标签。
    """

    _SKIP = {"script", "style", "noscript", "head", "template", "iframe"}
    _BREAK = {"p", "br", "div", "li", "tr", "ul", "ol", "table", "section",
              "article", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}

    def __init__(self):
        # convert_charrefs 把 &nbsp; 等实体自动转成字符，压缩阶段才能统一处理
        super().__init__(convert_charrefs=True)
        self._parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        # 块级标签只补换行不补空格，否则段首会多出缩进
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BREAK:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        # max(0, ...) 兜住畸形 HTML 里多余的结束标签，计数不能变负
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self._BREAK:
            self._parts.append("\n")

    def handle_data(self, data):
        # 只丢纯空白片段，正文缩进交给 _compress_lines 统一处理
        if not self._skip_depth and data.strip():
            self._parts.append(data)

    def text(self):
        """提取结果：拼接各片段后压缩空行"""
        return _compress_lines("".join(self._parts))


def _compress_lines(text: str) -> str:
    """压缩文本：去行首尾空白、合并连续空行为单个空行（各提取路径共用）

    网页抓下来的文本普遍带大量 &nbsp; 与换行，不压缩会让阅读区出现成片
    空白；合并成单个空行既保留分段感又不显得散。
    """
    lines = []
    for raw in text.splitlines():
        # split() 不带参数按任意空白切分，顺带吃掉制表符与全角空格
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
    trafilatura 在函数内导入：它是可选依赖，缺失时模块本身仍要能加载。
    """
    try:
        from trafilatura import extract
        text = extract(html, output_format="txt", favor_precision=True,
                       include_links=False, include_images=False,
                       include_tables=False, deduplicate=True)
        # 50 字是「看起来像正文」的下限；更短的结果多半是版权声明或错误页
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
# 匹配用子串包含，顺序即优先级：更具体的域名关键字要排在泛化关键字前面
_SITE_RULES = [
    ("biquge", ["//*[@id='content']", "//*[@id='chaptercontent']"]),
    ("xbiquge", ["//*[@id='content']", "//*[@id='chaptercontent']"]),
    ("bqg", ["//*[@id='content']", "//*[contains(@class,'read-content')]"]),
    ("bqxs", ["//*[@id='content']"]),
]

# 通用正文容器 XPath：不依赖站点规则，按序尝试，首个 ≥50 字者采纳。
# 顺序按命中率排，content 类 id 在中文书站里最常见。
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

# 章节标题特征：中文数字与阿拉伯数字都要覆盖，数字两侧允许空格
_CHAPTER_RE = re.compile(r"第\s*[0-9零一二三四五六七八九十百千万两]+\s*[章节卷回篇]")
# 翻页链接文本；只认短文本，避免正文里提到「下一页」的普通链接被误判
_NEXT_RE = re.compile(r"下一[章节篇]|下章|下一页|next", re.I)
_PREV_RE = re.compile(r"上一[章节篇]|上章|上一页|prev", re.I)


class _HtmlText:
    """lxml 节点转段落纯文本：剔除 script/style，块级标签换行分隔

    与 _TextExtractor 职责相同，但直接走 lxml 已解析的树，省一次 HTML 解析。
    """

    _SKIP = {"script", "style", "noscript", "head", "template", "iframe"}

    @staticmethod
    def _walk(el, parts):
        """递归收集文本；注释/PI 节点的 tag 不是字符串，先过滤掉"""
        tag = el.tag if isinstance(el.tag, str) else None
        if tag in _HtmlText._SKIP:
            # 跳过内部内容，但保留其后置文本（如 <script>..</script> 后的文字）
            if el.tail and el.tail.strip():
                parts.append(el.tail)
            return
        if tag in _BLOCK_TAGS:
            # 块级标签前后各补一个换行，段落之间才会断开
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
    """归一化域名：去协议/路径/www 前缀，小写

    netloc 里可能带端口，这里不剥离；同站判断只在同一站点内比较，
    端口差异实际不会出现。
    """
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _same_site(url_a: str, url_b: str) -> bool:
    """同站点判断（忽略 www 前缀差异）"""
    return _norm_host(url_a) == _norm_host(url_b)


def _extract_by_rules(doc, url: str) -> str:
    """站点规则 + 通用正文容器 XPath 提取；首个 ≥50 字结果即采纳

    站点规则排在通用规则之前，命中专属容器时就不再浪费后续 XPath 查询。
    """
    host = _norm_host(url)
    xpaths = []
    for kw, paths in _SITE_RULES:
        if kw in host:
            xpaths.extend(paths)
    xpaths.extend(_GENERIC_CONTENT_XPATHS)
    # 站点规则与通用规则可能重复，去重避免同一个容器解析两遍
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
    """章节标题：lastread.set（书名/章节名数据）→ h1 → title 逐级降级

    lastread.set 是部分书站写给阅读器记进度用的 JS，参数里带干净的书名与
    章节名，比 title 标签少一堆站点后缀。
    """
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
        # 站点习惯把 title 拼成「章节名_书名」，按分隔符取第一段
        return re.split(r"[_\-|]", t)[0].strip()
    return ""


def _decrypt_writeln_text(html: str) -> str:
    """解密 JS 加密正文（彩虹工具类：document.writeln(xxx.bb('base64'))）

    此类站点把段落 HTML 经 base64 编码后由 JS 写入页面，
    Python 侧直接解码还原明文 HTML 再转纯文本。bb() 只是 base64 的
    马甲，不含真正的加密，所以能直接解。
    """
    parts = []
    pat = re.compile(r"document\.writeln\(\s*[\w.$]+\.bb\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\)")
    for m in pat.finditer(html):
        try:
            parts.append(base64.b64decode(m.group(1))
                         .decode("utf-8", errors="replace"))
        except Exception:
            # 单段解码失败就跳过，其余能解的段落仍然可用
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
    """章节 URL 归一化：取路径末尾数字段（13812008_1.html → 13812008）

    目录链接常带 _1 分页后缀，直接用整串 URL 比较会匹配不上当前章节；
    统一取书号段，既用于高亮当前章，也用于上下章推导。
    """
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

    # 同一 URL 可能被多个容器重复收录，按目标 URL 去重并保序
    def _add_chapter(text, target):
        """收录一条章节：按目标 URL 去重并保留首次出现的顺序

        同一章常同时出现在「最新章节」与「目录」两个容器里，按 URL
        去重比按标题可靠——标题里可能夹着分页后缀。
        """
        if text and target not in seen:
            seen.add(target)
            chapters.append((text, target))

    for xp in _TOC_CONTAINER_XPATHS:
        try:
            nodes = doc.xpath(xp)
        except Exception:
            continue
        # 站外链接一律丢弃，否则目录里会混进广告与友链
        for a in nodes:
            href = (a.get("href") or "").strip()
            text = "".join(a.itertext()).strip()
            if not href or not text:
                continue
            target = urljoin(url, href)
            if _same_site(url, target):
                _add_chapter(text, target)

    # 全页兜底：标题含「第X章/节/卷」的链接（目录容器未命中时）。
    # 阈值 10 是经验值：正常目录远多于 10 条，少于 10 条基本可判定容器没命中。
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
        # 只认短文本，长段落里出现「下一页」多半是正文引用而非导航
        if len(text) <= 16 and _NEXT_RE.search(text):
            next_url = target
        elif len(text) <= 16 and _PREV_RE.search(text):
            prev_url = target

    # 上一章/下一章：pageEvent 键盘事件（key==37/39 → location=变量）。
    # 这类站点不渲染可见翻页链接，只在 JS 里绑定左右方向键，只能解析脚本。
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

    # 章节目录链接（供 worker 自动抓目录页）。含「目录」字样的链接可能是
    # 书详情页或最近更新页，其章节列表不全，需按 URL 层级打分择优。
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
            """给目录候选打分：0=同书目录且非章节页，1=同书，2=无从判断

            书详情页往往仍挂在同书路径下，只能靠「是否 .html」再区分；
            分不出时给 2 分垫底，排序仍稳定。
            """
            # 0 分最优先：同书目录下且不是具体章节页；1 分同书；2 分无从判断
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
    """目录页解析：复用 _parse_nav 的容器逻辑，返回章节列表

    目录页没有正文，nav 里的上下章结果用不上，只取章节列表。
    """
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
    trafilatura → html.parser 兜底。后一级只在前一级拿不到 ≥50 字时
    启动，顺序按「精度从高到低、代价从低到高」排。
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
        # 去重保序：seen.add 返回 None，条件表达式借它做副作用，一行完成
        seen = set()
        result["chapters"] = [c for c in chapters
                               if not (c[1] in seen or seen.add(c[1]))]
    # 前两级拿不到正文才继续降级，避免对已成功的页面重复解析
    if len(result["text"]) < 50:
        result["text"] = _decrypt_writeln_text(html)
    if len(result["text"]) < 50:
        result["text"] = _extract_web_text(html)
    return result


class _FetchWorker(QThread):
    """一次性网页抓取 worker：requests 拉取 + 正文/章节导航解析，完成即退

    每次抓取都新建实例、结束后由调用方 deleteLater，QThread 对象不能
    复用；解析失败通过 failed 信号回传，不向主线程抛异常。
    """

    ok = Signal(dict)  # _parse_web_page 结果：正文/标题/上下章/章节列表
    failed = Signal(str)

    # 伪装桌面 Chrome：默认的 python-requests UA 在书站几乎必被 403
    _UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

    def __init__(self, url, parent=None):
        """记录目标 URL；worker 一次性使用，重定向后的地址在 run 内解析

        worker 不持有界面引用，正文与导航全靠信号回传。
        """
        super().__init__(parent)
        self._url = url

    def _headers(self):
        """反爬请求头：UA + 同源 Referer + 中文语言偏好

        部分站点校验 Referer 是否同源，缺了会直接返回 403；语言偏好则
        影响站点返回简体还是繁体版本。
        """
        p = urlparse(self._url)
        return {
            "User-Agent": self._UA,
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                       "image/avif,image/webp,*/*;q=0.8"),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": f"{p.scheme}://{p.netloc}/",
        }

    def _fetch_text(self, url):
        """requests 拉取页面并规范编码，返回 (html, final_url)

        final_url 是重定向后的真实地址，后续上下章推导必须基于它，
        用请求前的 URL 会对不上章节列表。
        """
        import requests
        resp = requests.get(url, headers=self._headers(),
                            timeout=10, allow_redirects=True)
        resp.raise_for_status()
        # 中文站常把响应头写成 ISO-8859-1，按它解码会整页乱码；
        # apparent_encoding 会嗅探字节内容，失败再退回 UTF-8
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            try:
                resp.encoding = resp.apparent_encoding
            except Exception:
                resp.encoding = "utf-8"
        return resp.text, resp.url or url

    def run(self):
        """拉取页面 → 解析正文/导航；章节不足自动补目录页，上下章缺失按目录推导

        所有异常都转成 failed 信号：worker 线程里抛异常只会打印堆栈，
        界面拿不到任何提示。
        """
        try:
            html, final_url = self._fetch_text(self._url)
            result = _parse_web_page(html, final_url)
            # 章节列表不足 10 条时自动抓目录页补齐（目录失败不影响正文阅读）
            if len(result["chapters"]) < 10 and result["toc_url"]:
                try:
                    toc_html, toc_url = self._fetch_text(result["toc_url"])
                    toc_chapters = _parse_toc_page(toc_html, toc_url)
                    # 目录页可能在重定向后换了域名，按书号去重而不是整串 URL
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
            # 上下章推导：在章节列表定位当前章节，缺失的按序补齐。
            # 当前章可能不在列表里（站外跳转/列表不含本章），此时保持
            # 页面自带的上下章不动。
            key = _chapter_key(final_url)
            idx = next((i for i, (_t, u) in enumerate(result["chapters"])
                        if _chapter_key(u) == key), -1)
            if idx >= 0:
                if not result["prev_url"] and idx > 0:
                    result["prev_url"] = result["chapters"][idx - 1][1]
                if not result["next_url"] and idx + 1 < len(result["chapters"]):
                    result["next_url"] = result["chapters"][idx + 1][1]
            # 正文过短基本等于被反爬拦了，明确提示而不是给用户一篇空白
            if len(result["text"]) < 50:
                self.failed.emit("未能提取到有效正文（可能触发站点反爬）")
            else:
                self.ok.emit(result)
        except Exception as e:
            self.failed.emit(str(e) or e.__class__.__name__)


def _fake_log_text() -> str:
    """生成静态假日志文本，供老板键伪装的「系统日志」视图使用

    固定种子让每次打开都是同一份日志，时间戳又随当前时刻滚动，
    比完全随机更经得起同事多看一眼。
    """
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
    # 起始时间设在 26 分钟前：滚动到底部时最新几条正好贴近当前时刻
    t = datetime.now().replace(second=0, microsecond=0) - timedelta(minutes=26)
    lines = []
    # 96 行足够撑满一屏并留出滚动余量，再多没有视觉收益
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
    """小说阅读器：txt / 粘贴 / 网页抓取 + 字号调节 + 老板键伪装

    老板键把正文整块换成深色假日志，用于工位上快速遮掩；阅读进度与
    字号写进 moyu_state.json，下次打开继续。
    """

    _FONT_MIN, _FONT_MAX = 12, 32

    def __init__(self, parent=None):
        """恢复字号/上次来源/滚动比例，再搭工具栏与正文/伪装双视图"""
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
        # 章节导航要等抓到网页才有目标，初始一律禁用，避免空点击
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
        # 回车与「抓取」按钮走同一入口，URL 校验逻辑只保留一份
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
        # 链接交给我们自己的跳转逻辑，QTextBrowser 自行导航会把当前正文冲掉
        self._browser.setOpenLinks(False)
        # 空面板给操作提示：否则空白视图分不清是没加载还是控件坏了
        self._browser.setPlaceholderText(
            "打开 TXT / 粘贴文本 / 抓取网页开始阅读")
        # 滚动只登记防抖保存，真正落盘在 _flush_save，避免每滚一格写一次盘
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
        # 打开即滚到底部，视觉上与正在实时刷新的日志流一致
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
        # WidgetWithChildrenShortcut：焦点落在正文或按钮里也能触发，
        # 又不会像全局快捷键那样抢别的窗口
        self._boss_sc.setContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._boss_sc.activated.connect(self.toggle_boss)

        # ---- 恢复上次阅读 ----
        self._apply_font()
        # 上次是 txt 就直接同步载入；上次是网页则延迟发请求，
        # 避免启动瞬间界面被网络阻塞
        if self._last_txt and os.path.isfile(self._last_txt):
            self._load_txt(self._last_txt, restore_scroll=True)
        elif self._last_url:
            # 延迟到窗口显示后再自动续读，避免启动即发网络请求阻塞界面
            QTimer.singleShot(300, self._restore_last_url)

    # ---------- 字号 ----------

    def _apply_font(self):
        """把当前字号套到正文样式并同步按钮可用态

        setDefaultStyleSheet 只对之后解析出的 document 生效，所以每次
        setPlainText 之后都要重新调用一次。
        """
        self._browser.document().setDefaultStyleSheet(
            f"body {{ font-size: {self._font_size}pt; line-height: 1.6; }}")
        self._lbl_font.setText(f"{self._font_size}pt")
        self._btn_font_minus.setEnabled(self._font_size > self._FONT_MIN)
        self._btn_font_plus.setEnabled(self._font_size < self._FONT_MAX)

    def _adjust_font(self, delta):
        """字号增减（限制在 MIN~MAX），变更后落盘

        到达边界时直接返回，避免每次点击都写一遍状态文件。
        """
        new = min(max(self._font_size + delta, self._FONT_MIN),
                  self._FONT_MAX)
        if new == self._font_size:
            return
        self._font_size = new
        self._apply_font()
        save_moyu_state({"reader_font_size": self._font_size})

    # ---------- 正文载入 ----------

    def _set_text(self, text, source):
        """正文载入浏览器并更新来源标签

        setPlainText 会重建 document，字号样式必须紧跟其后重套。
        """
        self._browser.setPlainText(text)
        self._apply_font()  # setPlainText 重建 document 后需重新套用字号
        self._lbl_source.setText(source)

    @staticmethod
    def _read_text_file(path):
        """UTF-8 / GBK 自动探测读取

        中文 txt 两种编码都很常见，逐个试解码比猜编码可靠；全部失败时
        用 replace 兜底，宁可个别乱码也不要让阅读面板崩掉。
        """
        with open(path, "rb") as f:
            data = f.read()
        # UTF-8 优先；GB18030 是 GBK 的超集，放最后兜底
        for enc in ("utf-8", "gbk", "gb18030"):
            try:
                return data.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return data.decode("utf-8", errors="replace")

    def _load_txt(self, path, restore_scroll=False):
        """载入 txt：成功后记为最近文件，恢复场景按进度比例回滚

        读取失败只弹提示不抛出：阅读器是娱乐功能，不该因为一个坏文件
        打断整个主流程。
        """
        try:
            text = self._read_text_file(path)
        except OSError as e:
            show_info_bar(f"{os.path.basename(path)}: {e.strerror or e}", "error",
                          title="打开失败", parent=self, duration=4000)
            return
        self._last_txt = path
        self._set_text(text, f"正在阅读：{os.path.basename(path)}")
        # 只记路径不记内容：txt 原文留在磁盘原位置，状态文件保持轻量
        save_moyu_state({"reader_last_txt": path})
        if restore_scroll:
            self._restore_scroll(self._current_ratio)

    def _open_txt(self):
        """文件对话框选 txt（起点为上次打开位置）"""
        # 上次文件所在目录优先，没有可用记录才回落到应用目录
        start = self._last_txt if os.path.isfile(self._last_txt) \
            else get_app_dir()
        path, _ = QFileDialog.getOpenFileName(
            self, "打开文本", start, "文本文件 (*.txt);;所有文件 (*)")
        if path:
            # 换文件必须归零进度，否则新文件会套用上一本的滚动比例
            self._current_ratio = 0.0
            self._load_txt(path)

    def _paste_read(self):
        """粘贴阅读：弹窗贴文本后直接载入

        粘贴内容只存在内存里，关掉面板即丢失，避免往磁盘写来历不明的文本。
        """
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
        """「抓取」按钮：读取输入框 URL 发起加载

        输入框回车与按钮共用一个入口，URL 校验逻辑只保留一份。
        """
        self._load_url(self._url_edit.text().strip())

    def _load_url(self, url, quiet=False):
        """统一加载入口：校验 + 防重复 + 启动 worker（翻页/目录跳转复用）

        quiet 供启动自动续读使用：失败不弹提示，不打断用户进入页面。
        """
        # 同一时刻只允许一个抓取线程，重复点击直接忽略
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
        # 裸域名补 https：书站基本都已上 HTTPS，补 http 反而多吃一次重定向
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        # 抓取期间锁住输入与按钮，防并发 worker；结束后由 _finish_fetch 恢复
        self._url_edit.setText(url)
        self._url_edit.setEnabled(False)
        self._btn_fetch.setEnabled(False)
        self._btn_fetch.setText("抓取中…")
        w = _FetchWorker(url, self)
        self._fetch_worker = w
        # lambda 只做签名适配：信号带 dict，槽只收 data，直连会类型不匹配
        w.ok.connect(lambda data: self._on_fetch_ok(data))
        w.failed.connect(lambda msg, u=url: self._on_fetch_failed(u, msg))
        # lambda 持有 w 引用，防止运行中被 GC 销毁触发 QThread qFatal
        w.finished.connect(lambda w=w: self._finish_fetch(w))
        w.start()

    def _restore_last_url(self):
        """启动自动续读上次章节（失败静默，不影响进入页面）

        quiet=True 时失败不弹提示条，避免每次启动都被网络问题打扰。
        """
        self._load_url(self._last_url, quiet=True)

    def _finish_fetch(self, w):
        """抓取线程结束：恢复输入框/按钮并销毁 worker

        无论成功失败都要走到这里，否则输入框会一直禁用。
        """
        self._fetch_worker = None
        self._url_edit.setEnabled(True)
        self._btn_fetch.setEnabled(True)
        self._btn_fetch.setText("抓取")
        # deleteLater 交给事件循环回收；在 finished 槽里直接 delete 会崩
        w.deleteLater()

    def _on_fetch_ok(self, data):
        """抓取成功：载入正文、更新导航按钮、记最近 URL 并提示字数"""
        # 重定向后的真实 URL 优先，它才是章节推导与续读的锚点
        url = data.get("url") or ""
        text = data.get("text") or ""
        self._current_ratio = 0.0
        title = (data.get("title") or "").strip()
        source = f"{title} · {_norm_host(url)}" if title else f"正在阅读：{url}"
        self._set_text(text, source)
        self._update_nav(data)
        save_moyu_state({"reader_last_url": url})
        # 字数让用户一眼判断是否抓全，被截断时通常远小于预期
        show_info_bar(f"已提取 {len(text)} 字正文", "success",
                      title="抓取成功", parent=self, duration=2500)

    def _update_nav(self, data):
        """根据解析结果更新上一章/下一章/目录按钮状态

        解析不到就禁用按钮而不是隐藏：按钮位置固定，频繁显隐会跳。
        """
        # 解析结果可能是 None，统一收敛成空串，后续 bool() 判断才可靠
        self._prev_url = data.get("prev_url") or ""
        self._next_url = data.get("next_url") or ""
        self._chapters = data.get("chapters") or []
        self._btn_prev.setEnabled(bool(self._prev_url))
        self._btn_next.setEnabled(bool(self._next_url))
        self._btn_toc.setEnabled(bool(self._chapters))

    def _go_prev(self):
        """跳转上一章（解析出的链接）

        没有解析出链接时按钮本就是禁用的，这里不再重复判断状态。
        """
        if self._prev_url:
            self._load_url(self._prev_url)

    def _go_next(self):
        """跳转下一章（解析出的链接）

        与上一章共用 _load_url，翻页时的防重复与状态锁一并生效。
        """
        if self._next_url:
            self._load_url(self._next_url)

    def _show_toc(self):
        """章节目录弹窗：双击跳转，当前章节高亮

        只展示当前页解析出的章节，不再额外请求目录页——目录页的内容
        已由 worker 合并进 _chapters。
        """
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
            # URL 存 UserRole：章节标题可能重复，不能靠文本反查目标
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
        """目录双击：先关弹窗再加载，避免模态窗口挡住新章节的提示条

        加载仍走 _load_url，防重复与状态锁一并生效。
        """
        url = item.data(Qt.ItemDataRole.UserRole) or ""
        dlg.accept()
        if url:
            self._load_url(url)

    def _on_fetch_failed(self, url, msg):
        """抓取失败：错误首行提示 + 「浏览器打开」动作兜底"""
        # duration=0 让提示条常驻，用户点「浏览器打开」或手动关闭后才消失
        first = str(msg).splitlines()[0][:120] if msg else "未知错误"
        bar = show_info_bar(first, "error", title="抓取失败", parent=self, duration=0)
        btn = PushButton("浏览器打开", bar)
        btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        bar.addWidget(btn)
        bar.show()

    # ---------- 老板键 ----------

    def toggle_boss(self):
        """老板键：正文 ⇄ 假日志伪装视图切换（切走前先存进度）

        伪装视图不参与滚动记忆，切回来时按切换前记下的比例恢复。
        """
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
        # span 为 0 表示内容不足一屏，此时按 0 处理，避免除零
        bar = self._browser.verticalScrollBar()
        span = bar.maximum() - bar.minimum()
        return (bar.value() - bar.minimum()) / span if span > 0 else 0.0

    def _restore_scroll(self, ratio):
        """延迟到布局完成后按比例恢复滚动位置

        singleShot(0) 把恢复推迟到本轮事件循环之后，这时布局已完成、
        滚动条 maximum 才是最终值，否则会滚到错误位置。
        """
        # 闭包捕获 ratio：singleShot 只接受无参可调用对象
        def _do():
            bar = self._browser.verticalScrollBar()
            span = bar.maximum() - bar.minimum()
            if span > 0:
                bar.setValue(bar.minimum() + int(ratio * span))
        QTimer.singleShot(0, _do)

    def _schedule_save(self, *_):
        """滚动停止 1.2s 后落盘进度，避免高频写文件

        _save_pending 做防抖：连续滚动只登记一个定时器，期间的事件全部
        忽略，停止后才真正写一次。
        """
        if self._save_pending:
            return
        self._save_pending = True
        QTimer.singleShot(1200, self._flush_save)

    def _flush_save(self):
        """落盘阅读进度（滚动比例 + 字号）

        字号一起写是因为这个入口同时承担「切走前保存」，分成两次写
        只会多一次磁盘 IO。
        """
        self._save_pending = False
        self._current_ratio = round(self._scroll_ratio(), 4)
        save_moyu_state({
            "reader_scroll_ratio": self._current_ratio,
            "reader_font_size": self._font_size,
        })

    def hideEvent(self, e):
        """页签切走 / 面板关闭前保存阅读进度

        停在伪装视图时不保存，避免把假日志的滚动位置写进阅读进度。
        """
        if self._stack.currentIndex() == 0:
            self._flush_save()
        super().hideEvent(e)
