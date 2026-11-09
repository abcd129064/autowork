# -*- coding: utf-8 -*-
"""工作留痕面板的共享词汇：分类/状态/成员配色 + 日期口径 + 页签外壳

五个页签与录入对话框共用同一套「分类名 → 语义色」「状态名 → 语义色」
「日期 → 中文星期 / 查询区间」映射。集中在此的理由：任何一处漂移都会让
表格标签、时间轴圆点、思维导图分支、统计环图四套颜色对不上号。

颜色一律从 core.design_tokens 取（accent 走运行时主题色），业务代码里
不出现 hex，深浅主题才都能成立。

本模块顶层**不 import qfluentwidgets**：分类/状态/日期这套词汇是纯逻辑，
保持可在无 qfw 的解释器上单测（qfw 只在取主题色的两个函数内延迟导入）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QDate, QEvent, QRectF, QTimer, Signal
from PySide6.QtGui import (QColor, QPainter, QPainterPath, QPen, QFont,
                           QFontMetrics)
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
                               QFrame, QSizePolicy, QLabel)

from core.design_tokens import (SEMANTIC, SEMANTIC_EXT, GRAY_LIGHT, GRAY_DARK,
                                NEUTRAL_TRACK, DEFAULT_GAP, RADIUS, SPACE,
                                TYPE_SCALE_PX, FONT_WEIGHTS, darken, with_alpha)
# 分类名与状态名的唯一来源在数据层：完成率的 SQL 只认「已完成」这个字面量，
# 两边各写一份就会在改文案的那天静默对不上
from database.worklog_db import CATEGORIES, STATUSES  # noqa: F401

# ==================== 分类 / 状态 ====================

# 分类 → 色键（稿 348-356：开发=主色、运维=琥珀、文档=紫、测试=蓝、
# 沟通=黄绿、学习/其他=石板灰）
CATEGORY_COLOR = {
    "开发": "accent",
    "运维": "warning",
    "文档": "violet",
    "测试": "info",
    "沟通": "lime",
    "学习": "neutral",
    "其他": "neutral",
}

# 用户自加分类的配色轮转板：不含 accent（那是主色/「开发」的专座），
# 取名首字符码点求和取模而非 hash() —— 后者带 PYTHONHASHSEED 随机化，
# 同一个分类每次启动都会换颜色
_CUSTOM_COLOR_KEYS = ("violet", "lime", "info", "warning", "success",
                      "neutral")

# 状态与稿 358-362 一致；「已完成」是进度拖到 100% 时自动落的状态
STATUS_COLOR = {
    "已完成": "success",
    "进行中": "info",
    "未开始": "neutral",
    "异常": "danger",
}

# 标签软背景的透明度（稿 .tag 的 rgba(...,.12)）
TAG_SOFT_ALPHA = 0.12

# 成员配色板：accent_dark 由当前主题色压暗 20% 得到（稿 490 的 #0097A7
# 即 #00BCD4 压暗约两成），其余取语义色扩展板；按 member_id 取模而非按
# 名字排序 —— 新增成员不会把老成员的颜色整体挤位。
_MEMBER_KEYS = ("accent_dark", "info", "success", "warning",
                "violet", "lime", "neutral")


def color_hex(key: str) -> str:
    """色键 → hex：accent 跟随运行时主题色，其余查语义色板

    theme_qss 顶层 import qfluentwidgets，这里延迟到调用时再取，
    保持本模块在无 qfw 的解释器上也能 import（分类/日期词汇可离线单测）。
    """
    if key in ("accent", "accent_dark"):
        from core.theme_qss import current_accent_hex
        accent = current_accent_hex()
        return accent if key == "accent" else darken(accent, 0.20)
    return SEMANTIC_EXT.get(key) or SEMANTIC.get(key) or SEMANTIC["neutral"]


def color_q(key: str, alpha: float = 1.0) -> QColor:
    """色键 → QColor（alpha 0~1，供自绘的软背景/半透明描边）"""
    c = QColor(color_hex(key))
    if alpha < 1.0:
        c.setAlphaF(alpha)
    return c


# 主色软背景的透明度（稿 --accent-soft 的 rgba(0,188,212,.15)）
ACCENT_SOFT_ALPHA = 0.15
# 汇总卡图标块的圆角（稿 .scard .ic 的 border-radius:10px）
ICON_BLOCK_RADIUS = 10


def accent_press() -> str:
    """主色压暗三成（稿 --accent-press #00838F ≈ #00BCD4 压暗 30%）

    主题色能在设置页改，写死 hex 会让改色之后的分组行文字、链接色、
    百分比数字与主色脱钩。
    """
    from core.theme_qss import current_accent_hex
    return darken(current_accent_hex(), 0.30)


def accent_soft(alpha: float = ACCENT_SOFT_ALPHA) -> str:
    """主色软背景串（稿 --accent-soft），供 QSS 使用"""
    from core.theme_qss import current_accent_hex
    return with_alpha(current_accent_hex(), alpha)


def soft_q(hex_color: str, alpha: float = TAG_SOFT_ALPHA) -> QColor:
    """半透明软底的 QColor（自绘用）

    ``with_alpha`` 产出的 rgba() 串只给 QSS：QColor 不认这种写法，拿它构造
    得到的是无效色（画出来一块黑），自绘必须走 setAlphaF。
    """
    c = QColor(hex_color)
    c.setAlphaF(alpha)
    return c


def accent_soft_q(alpha: float = ACCENT_SOFT_ALPHA) -> QColor:
    """--accent-soft 的 QColor 形态（分组带底色等自绘场景）"""
    from core.theme_qss import current_accent_hex
    return soft_q(current_accent_hex(), alpha)


def track_hex() -> str:
    """中性轨道色：迷你进度条底槽 / 环图底环（稿 #eceff3）"""
    from qfluentwidgets import isDarkTheme
    return NEUTRAL_TRACK["dark" if isDarkTheme() else "light"]


def px_font(px, weight: int = None) -> QFont:
    """按令牌刻度取字体

    走 qfw 的 getFont 而不是裸 QFont：统一设置页会 QApplication.setFont
    换字体族，裸构造出来的字体不跟着变。Qt 的 setPixelSize 只收整数，
    稿里的 10.5/12.5/13.5 一律四舍五入（→ 11/13/14）。
    """
    from qfluentwidgets import getFont
    return getFont(int(float(px) + 0.5),
                   QFont.Weight(int(weight or FONT_WEIGHTS["regular"])))


def tabular(font: QFont) -> QFont:
    """等宽数字（稿 .num / .v 的 font-variant-numeric:tabular-nums）

    Qt 6.7 才有 QFont.setFeature，低版本静默跳过：数字对不齐只是观感问题，
    不值得为它加版本分支。
    """
    try:
        font.setFeature(QFont.Feature.TabularFigures)
    except Exception:
        pass
    return font


def icon_pixmap(icon, hex_color: str, size: int):
    """FluentIcon → 指定色的 QPixmap（汇总卡图标块用）

    稿 310/312/323 画的是 emoji（⏱ 📋 👥），依赖系统 emoji 字体回退，
    换台机器就是豆腐块；SVG 图标上色后主题色跟着走，也不违反「不得手绘
    图标文件」这条约束。
    """
    from qfluentwidgets import Theme
    return icon.icon(Theme.AUTO, QColor(hex_color)).pixmap(size, size)


def category_color_key(name) -> str:
    """分类名 → 色键；内置项查表，用户自加的分类按名字稳定轮转"""
    key = CATEGORY_COLOR.get((name or "").strip())
    if key:
        return key
    if not name:
        return "neutral"
    return _CUSTOM_COLOR_KEYS[sum(ord(c) for c in str(name))
                              % len(_CUSTOM_COLOR_KEYS)]


def category_color(name) -> str:
    return color_hex(category_color_key(name))


def status_color(name) -> str:
    return color_hex(STATUS_COLOR.get(name or "", "neutral"))


def member_color(member_id) -> str:
    return color_hex(_MEMBER_KEYS[int(member_id or 0) % len(_MEMBER_KEYS)])


def tag_colors(color_key: str):
    """语义标签的 (前景, 软底) 一对 QColor（稿 162-170 的 .tag 各档）

    返回 QColor 而非色串：唯一消费方是委托自绘，而 QColor 不认 with_alpha
    的 rgba() 串。主色那一档前景用压暗色、软底 .15，其余前景即语义色、
    软底 .12；稿对 warn 单独写了 .14，差 2% 透明度不值得为它开分支。
    """
    if color_key in ("accent", "accent_dark"):
        return QColor(accent_press()), accent_soft_q()
    hexv = color_hex(color_key)
    return QColor(hexv), soft_q(hexv)


def category_tag(name):
    """分类名 → 标签胶囊配色（委托 ROLE_TAG 的载荷）"""
    return tag_colors(category_color_key(name))


def status_tag(name):
    """状态名 → 标签胶囊配色；未登记的状态按中性灰，不猜"""
    return tag_colors(STATUS_COLOR.get((name or "").strip(), "neutral"))


def text_color_tier(tier: int = 1):
    """正文三级文字色（稿 :root 的 --text/--text2/--text3），返回 (浅, 深)

    qfw 的 setTextColor(lightColor, darkColor) 需要两份，主题切换自动生效。
    """
    key = {1: "text_primary", 2: "text_secondary",
           3: "text_disabled"}[tier]
    return QColor(GRAY_LIGHT[key]), QColor(GRAY_DARK[key])


def tier_color(tier: int = 1) -> QColor:
    """三级文字色的自绘形态：只返回当前主题那一份

    ``text_color_tier`` 是给 setTextColor 的一对色，QPainter.setPen 拿它
    没有意义 —— 画的时候必须已经知道主题是深是浅。
    """
    from qfluentwidgets import isDarkTheme
    light, dark = text_color_tier(tier)
    return dark if isDarkTheme() else light


# ==================== 日期口径 ====================

WEEKDAY_ZH = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def iso(d: QDate) -> str:
    """库内存的日期串统一 'YYYY-MM-DD'，字典序即时间序（免 date()/strftime）"""
    return d.toString("yyyy-MM-dd")


def parse_iso(text: str) -> QDate:
    return QDate.fromString(str(text or "")[:10], "yyyy-MM-dd")


def weekday_zh(d: QDate) -> str:
    """QDate.dayOfWeek() 周一=1 … 周日=7"""
    return WEEKDAY_ZH[max(0, min(6, d.dayOfWeek() - 1))]


def day_label(d: QDate) -> str:
    """分组行 / 时间轴日头的中文日期：「11月6日」"""
    return f"{d.month()}月{d.day()}日"


def fmt_hours(value) -> str:
    """工时展示：整数不带小数点（稿 310 的 21.5 与 3 两种写法都要能出）"""
    try:
        num = float(value or 0)
    except (TypeError, ValueError):
        return "0"
    return str(int(num)) if num == int(num) else ("%.1f" % num)


def week_scope_zh(d: QDate) -> str:
    """锚点日的周归属小字（稿 416 日期框里的「本周」）

    只给本周/上周两档固定文案，再早的周写周一日期：翻到上个月还显示
    「本周」就是撒谎，而完整 ISO 周号对中文用户没有可读性。
    """
    monday = d.addDays(-(d.dayOfWeek() - 1))
    now = QDate.currentDate()
    offset = now.addDays(-(now.dayOfWeek() - 1)).daysTo(monday)
    if offset == 0:
        return "本周"
    if offset == -7:
        return "上周"
    return "%s 那周" % day_label(monday)


# 周期区间（日/周/月的起止）不在这里算：那是查询条件，归
# database/worklog_db.py（纯 datetime、可离线单测）。UI 只传「周期键 + 锚点日期」。


# ==================== 页签外壳 ====================

class WorklogPage(QWidget):
    """页签通用外壳：稿 84 ``.pv-body`` 的 padding（上16 左右28 下24）+ 独立滚动

    子页往 ``self.lay`` 里堆内容。不在此定义 ``detach_workers``：
    PivotPage 广播时先找页面钩子、找不到才扫 ``_worker`` 后缀属性，
    基类给个空实现会让持有线程的子页漏掉兜底扫描。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; }")
        outer.addWidget(self.scroll)

        self.body = QWidget(self.scroll)
        self.scroll.setWidget(self.body)
        # setWidget() 会重建 viewport，透明设置必须在其后补一次
        # （否则深色主题下视口是一块黑底，同 PivotPage.CardPage 的坑）
        self.scroll.viewport().setStyleSheet("background: transparent;")

        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(28, 16, 28, 24)
        self.lay.setSpacing(DEFAULT_GAP)


class Hairline(QWidget):
    """1px 分隔线，颜色跟随主题的 --border 令牌（稿 77 Pivot 下划线 / 671 按钮栏上沿）

    不用 QFrame.HLine：它取 style 的默认分隔色，浅色下偏深、深色下几乎看不见，
    与令牌灰阶对不上。自绘一次即可，主题切换时靠 qconfig.themeChanged 重画。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        from qfluentwidgets import qconfig
        qconfig.themeChanged.connect(self.update)

    def paintEvent(self, _event):
        from qfluentwidgets import isDarkTheme
        tokens = GRAY_DARK if isDarkTheme() else GRAY_LIGHT
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(tokens["border"]))


# ==================== 共享控件 ====================

# 卡片内边距（稿 .card 的 padding:16px 20px）
CARD_PADDING = (SPACE[5], SPACE[4], SPACE[5], SPACE[4])
# 汇总卡数值 22px 不在 TYPE_SCALE_PX 刻度上（稿 .scard .v 明确写 22px）：
# 设计稿优先于仓库刻度，就近取 20 或 24 都会与稿差一截
SCARD_VALUE_PX = 22


class CardBox(QWidget):
    """令牌驱动的卡片外壳（稿 .card）：--surface 底 + 1px --border + 圆角 8

    不用 qfw CardWidget：它自带 hover/press 背景动画（静态卡片不需要），
    边框色取自库自己的调色板而非本仓令牌，深浅主题下都对不齐稿的
    #E0E0E0 / #383838。自绘一次，主题切换靠 qconfig.themeChanged 重画。

    ``padding`` 传 0 得到稿 339 那种「贴边表格卡」；``self.lay`` 是卡内布局。
    """

    def __init__(self, padding=CARD_PADDING, gap=DEFAULT_GAP, parent=None,
                 vertical=True):
        super().__init__(parent)
        lay_cls = QVBoxLayout if vertical else QHBoxLayout
        self.lay = lay_cls(self)
        if isinstance(padding, int):
            padding = (padding,) * 4
        elif len(padding) == 2:
            # CSS 两值写法 padding:10px 14px = 上下/左右
            padding = (padding[1], padding[0], padding[1], padding[0])
        self.lay.setContentsMargins(*padding)
        self.lay.setSpacing(gap)
        from qfluentwidgets import qconfig
        qconfig.themeChanged.connect(self.update)

    def paintEvent(self, _event):
        from qfluentwidgets import isDarkTheme
        tokens = GRAY_DARK if isDarkTheme() else GRAY_LIGHT
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                            float(RADIUS["lg"]), float(RADIUS["lg"]))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillPath(path, QColor(tokens["surface"]))
        painter.setPen(QPen(QColor(tokens["border"]), 1.0))
        painter.drawPath(path)


class IconBlock(QLabel):
    """汇总卡左侧图标块（稿 109-114）：38×38 圆角 10 软底 + 17px 上色图标

    fg/bg 允许传可调用对象：跟随主题色的那两张卡要在 themeColorChanged 时
    重新取色，传字符串就锁死在构造那一刻。
    """

    def __init__(self, icon, fg, bg=None, size=38, icon_size=17, parent=None):
        super().__init__(parent)
        self._icon = icon
        self._fg = fg
        self._bg = bg
        self._icon_size = icon_size
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        from qfluentwidgets import qconfig
        qconfig.themeChanged.connect(self.refresh)
        qconfig.themeColorChanged.connect(self.refresh)
        self.refresh()

    def refresh(self):
        fg = self._fg() if callable(self._fg) else self._fg
        bg = self._bg() if callable(self._bg) else self._bg
        if bg is None:
            bg = with_alpha(fg, TAG_SOFT_ALPHA)
        self.setPixmap(icon_pixmap(self._icon, fg, self._icon_size))
        self.setStyleSheet("QLabel { background: %s; border-radius: %dpx; }"
                           % (bg, ICON_BLOCK_RADIUS))


class RateRing(QWidget):
    """完成率环（稿 314-322）：44×44 ProgressRing 定制色 + 环心 11px/700 百分比

    qfw ProgressRing 能改环色（setCustomBarColor / setCustomBackgroundColor
    各收 light+dark 两个 QColor），但画不了环心文字，也没有 customStyleSheet，
    所以外面套一层 QWidget 放标签。
    """

    def __init__(self, size=44, color_key="success", parent=None):
        super().__init__(parent)
        self._key = color_key
        self.setFixedSize(size, size)
        from qfluentwidgets import CaptionLabel, ProgressRing, qconfig
        self.ring = ProgressRing(self)
        self.ring.setFixedSize(size, size)
        self.ring.move(0, 0)
        self.label = CaptionLabel("", self)
        self.label.setGeometry(0, 0, size, size)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setFont(tabular(px_font(TYPE_SCALE_PX["xs"],
                                           FONT_WEIGHTS["bold"])))
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        qconfig.themeChanged.connect(self._recolor)
        self._recolor()
        self.setValue(0)

    def _colors(self):
        c = QColor(color_hex(self._key))
        return c, c

    def _recolor(self):
        track = QColor(track_hex())
        self.ring.setCustomBackgroundColor(track, track)
        self.ring.setCustomBarColor(*self._colors())
        self.label.setTextColor(*self._colors())

    def setValue(self, pct):
        pct = max(0, min(100, int(round(float(pct or 0)))))
        self.ring.setValue(pct)
        self.label.setText("%d%%" % pct)


class SummaryCard(CardBox):
    """量化汇总卡（稿 309-325 / 526-541）：图标块 + 12px 灰标题 + 22px/700 数值

    页签1 与页签4 四张卡规格完全相同，只在图标与取值上分叉，做成一个类比
    两处复制布局更不容易漂。``lead`` 传 RateRing 即可替掉图标块（完成率卡）。
    """

    def __init__(self, title, lead=None, parent=None):
        super().__init__(padding=(SPACE[4], 14, SPACE[4], 14), gap=SPACE[4],
                         parent=parent, vertical=False)
        self.lay.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        if lead is not None:
            self.lay.addWidget(lead, 0, Qt.AlignmentFlag.AlignVCenter)

        from qfluentwidgets import CaptionLabel, StrongBodyLabel
        col = QWidget(self)
        col_lay = QVBoxLayout(col)
        col_lay.setContentsMargins(0, 0, 0, 0)
        col_lay.setSpacing(2)               # 稿 .v 的 margin-top:2px
        self.k_label = CaptionLabel(title, col)
        self.k_label.setFont(px_font(TYPE_SCALE_PX["sm"]))
        self.k_label.setTextColor(*text_color_tier(2))
        self.k_label.setWordWrap(False)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.v_label = StrongBodyLabel("", col)
        self.v_label.setFont(tabular(px_font(SCARD_VALUE_PX,
                                             FONT_WEIGHTS["bold"])))
        self.u_label = CaptionLabel("", col)
        self.u_label.setFont(px_font(TYPE_SCALE_PX["xs"]))
        self.u_label.setTextColor(*text_color_tier(3))
        # 稿是 <div class="v">21.5 <small>h</small></div>：小字与数值同基线
        row.addWidget(self.v_label, 0, Qt.AlignmentFlag.AlignBaseline)
        row.addWidget(self.u_label, 0, Qt.AlignmentFlag.AlignBaseline)
        row.addStretch(1)
        col_lay.addWidget(self.k_label)
        col_lay.addLayout(row)
        self.lay.addWidget(col, 1)

    def setValue(self, value, unit="", value_color=None):
        self.v_label.setText("" if value is None else str(value))
        self.u_label.setText(unit)
        if value_color is not None:
            c = QColor(value_color)
            self.v_label.setTextColor(c, c)


class TagPill(QWidget):
    """语义标签胶囊（稿 162-170 的 .tag）：软底 + 6px 圆点 + 12px/600 文字

    不用 QLabel 叠 QSS：稿的 ``border-radius:999px`` 与 ``::before`` 圆点
    伪元素 Qt 样式表都不支持，半透明软底还得绕 QColor 不认 rgba() 串的坑，
    自绘一次比拼样式表可靠。配色一律由 category_tag / status_tag /
    tag_colors 给出，本类不认业务名。
    """

    PAD_X = 9          # 稿 .tag 的 padding:2px 9px（竖向由字高 +4 承担）
    DOT = 6            # 稿 .tag::before 的 6px 圆点
    GAP = 5            # 稿 .tag 的 gap:5px

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._text = str(text or "")
        self._fg = QColor(color_hex("neutral"))
        self._bg = soft_q(color_hex("neutral"))
        self._font = px_font(TYPE_SCALE_PX["sm"], FONT_WEIGHTS["semibold"])
        from qfluentwidgets import qconfig
        qconfig.themeChanged.connect(self.update)
        qconfig.themeColorChanged.connect(self.update)
        self._fit()

    def set_tag(self, text, fg: QColor, bg: QColor):
        self._text = str(text or "")
        self._fg = QColor(fg)
        self._bg = QColor(bg)
        self._fit()

    def _fit(self):
        fm = QFontMetrics(self._font)
        w = (self.PAD_X * 2 + self.DOT + self.GAP
             + fm.horizontalAdvance(self._text))
        self.setFixedSize(w, fm.height() + 4)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._bg)
        painter.drawRoundedRect(QRectF(self.rect()),
                                self.height() / 2.0, self.height() / 2.0)
        cy = self.height() / 2.0
        painter.setBrush(self._fg)
        painter.drawEllipse(QRectF(self.PAD_X, cy - self.DOT / 2.0,
                                   self.DOT, self.DOT))
        painter.setPen(QPen(self._fg))
        painter.setFont(self._font)
        painter.drawText(QRectF(self.PAD_X + self.DOT + self.GAP, 0,
                                self.width(), self.height()),
                         Qt.AlignmentFlag.AlignVCenter
                         | Qt.AlignmentFlag.AlignLeft, self._text)


class ColorLabel(QLabel):
    """文字色跟随令牌的正文标签（稿里 ``style="color:var(--x)"`` 的 span）

    QLabel 的 setStyleSheet 写死 hex 后，深/浅切换或改主题色都不会重算，
    所以存取色回调，在 themeChanged / themeColorChanged 时重刷。
    """

    def __init__(self, text, color_provider, px=TYPE_SCALE_PX["sm"],
                 weight=None, parent=None):
        super().__init__(text, parent)
        self._px = px
        self._weight = weight
        self._provider = color_provider
        from qfluentwidgets import qconfig
        qconfig.themeChanged.connect(self._refresh)
        qconfig.themeColorChanged.connect(self._refresh)
        self._refresh()

    def _refresh(self):
        self.setFont(px_font(self._px, self._weight))
        self.setStyleSheet("color: %s;" % self._provider())


class AvatarDot(QLabel):
    """20px 圆形成员头像（稿 172-173）：成员色底 + 白色姓氏首字

    底色首档是主题色压暗，写死 hex 会在改主题色后与表格委托画的头像
    分家，故同 ColorLabel 存回调重刷。
    """

    SIZE = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self._char = ""
        self._member_id = 0
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFont(px_font(10.5, FONT_WEIGHTS["semibold"]))
        from qfluentwidgets import qconfig
        qconfig.themeChanged.connect(self._refresh)
        qconfig.themeColorChanged.connect(self._refresh)
        self._refresh()

    def set_member(self, char, member_id):
        self._char = str(char or "")[:1]
        self._member_id = int(member_id or 0)
        self._refresh()

    def _refresh(self):
        self.setText(self._char)
        self.setStyleSheet(
            "background: %s; color: #ffffff; border-radius: %dpx;"
            % (member_color(self._member_id), self.SIZE // 2))


class DateBox(QWidget):
    """日期选择组（稿 327-328）：CalendarPicker + 内嵌星期小字 + 前后步进键

    星期做成 picker 的子控件浮在右侧（下拉箭头左边）而不是并排一个独立标签：
    稿的 .datebox 把 picker 与两个步进键拼成一条整体边框，中间插标签会把它
    拆断。仓库里没有这个先例（grep 中文星期零命中），属新增。

    ``caption`` 可换浮层文案口径：页签1/对话框要星期（稿 328/651），
    页签2 要周归属（稿 416 的「本周」）。
    """

    dateChanged = Signal(QDate)

    # CalendarPicker 右侧下拉箭头占位（稿 .combo 的 padding-right:30px）
    _ARROW_ZONE = 30

    def __init__(self, parent=None, steps: bool = True, picker_cls=None,
                 caption=None):
        super().__init__(parent)
        self._caption = caption or weekday_zh
        from qfluentwidgets import (CalendarPicker, CaptionLabel, FluentIcon,
                                    ToolButton)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        # 工具栏用 CalendarPicker（按钮态，与稿 .datebox 的下拉框同形），
        # 录入对话框用 ZhDatePicker（稿 649 点名）：两者 getDate/setDate/
        # dateChanged 同签名，星期浮层与步进键的逻辑可以共用
        cls = picker_cls or CalendarPicker
        self.picker = cls(self)
        self.picker.setMinimumWidth(150)
        self.picker.setFixedHeight(33)      # 稿 .combo/.inp 的行高 33
        self.picker.setDate(QDate.currentDate())
        self.picker.dateChanged.connect(self._on_date)
        self.picker.installEventFilter(self)
        row.addWidget(self.picker)

        self.weekday = CaptionLabel("", self.picker)
        self.weekday.setFont(px_font(TYPE_SCALE_PX["sm"]))
        self.weekday.setTextColor(*text_color_tier(3))
        self.weekday.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.weekday.setStyleSheet("background: transparent;")

        self.btn_prev = self.btn_next = None
        if steps:
            # 稿 .step 是 24×33 的 ▲▼（实心三角，不是 chevron）
            self.btn_prev = ToolButton(FluentIcon.CARE_UP_SOLID, self)
            self.btn_next = ToolButton(FluentIcon.CARE_DOWN_SOLID, self)
            for btn in (self.btn_prev, self.btn_next):
                btn.setFixedSize(24, 33)
                row.addWidget(btn)
            self.btn_prev.setToolTip("前一天")
            self.btn_next.setToolTip("后一天")
            self.btn_prev.clicked.connect(lambda _=False: self.step(-1))
            self.btn_next.clicked.connect(lambda _=False: self.step(1))
        self._on_date(self.picker.getDate())

    def _on_date(self, d):
        self.weekday.setText(self._caption(d))
        self._place_weekday()
        self.dateChanged.emit(d)

    def _place_weekday(self):
        fm = self.weekday.fontMetrics()
        w = fm.horizontalAdvance(self.weekday.text()) + 2
        h = fm.height()
        self.weekday.setGeometry(self.picker.width() - self._ARROW_ZONE - w,
                                 (self.picker.height() - h) // 2, w, h)

    def eventFilter(self, obj, ev):
        if obj is self.picker and ev.type() == QEvent.Type.Resize:
            self._place_weekday()
        return super().eventFilter(obj, ev)

    def step(self, days):
        self.setDate(self.date().addDays(int(days)))

    def date(self) -> QDate:
        return self.picker.getDate()

    def setDate(self, d):
        self.picker.setDate(d)


class FlowSpacer(QWidget):
    """FlowLayout 里顶开右侧按钮的弹性占位（稿 334 的 ``<span style="flex:1">``）

    qfw FlowLayout 只认 item.sizeHint()，既不看 sizePolicy 也不给 spacer
    拉伸，工具栏想做成「左边筛选、右边按钮」只能自己量：宿主宽度变了就把
    本行剩余空间吃满，不够就收成 0 让按钮自然换行（稿 .toolbar 的 flex-wrap）。
    """

    MIN_SLACK = SPACE[2]      # 小于 8px 的空隙不值得占位，让按钮贴上去即可

    def __init__(self, layout, parent=None):
        super().__init__(parent)
        self._layout = layout
        self._busy = False
        self._again = False
        self.setFixedSize(0, 1)

    def reflow(self):
        """量一次本行剩余空间，把右侧控件推到右缘（可重入安全）"""
        host = self._layout.parentWidget()
        if host is None or host.width() <= 0:
            return
        if self._busy:
            # 嵌套进来的宽度变化（滚动条弹出/收起）不能丢：丢掉就等于用
            # 外层那一次的旧宽度定稿，控件会停在挤不下的宽度上
            self._again = True
            return
        self._busy = True
        try:
            # 轮数封顶兜底：占位宽度只由宿主宽度决定，正常两轮就收敛
            for _ in range(6):
                self._again = False
                if host.width() > 0:
                    self._measure(self._layout, host)
                if not self._again:
                    break
        finally:
            self._busy = False

    def _measure(self, lay, host):
        m = lay.contentsMargins()
        space = lay.horizontalSpacing()
        # FlowLayout 的换行判据是「item 右缘 > rect.right() - margin.right()」，
        # 这里再让 1px，免得整数取整把最后一个控件挤到下一行
        limit = host.rect().right() - m.right() - 1
        # 先归零再量：上一轮的占位宽度会把本轮的行分布带偏，导致来回震荡
        self.setFixedWidth(0)
        lay.setGeometry(host.rect())
        idx = self._index()
        if idx < 0:
            return
        x = lay.itemAt(idx).geometry().x()
        # 右侧控件按布局序取而不是按几何行取：它们可能正被上一轮过宽的占位
        # 挤在下一行，按行判断会漏掉它们，占位就再也收不回来（死锁）
        trail = [lay.itemAt(i).widget().width()
                 for i in range(idx + 1, lay.count())
                 if lay.itemAt(i).widget() is not None]
        need = sum(trail) + space * len(trail)
        # 剩余太小说明控件本来就贴着右缘，占位只会白添一次重排
        want = max(0, limit - x - need)
        if want < self.MIN_SLACK:
            want = 0
        # setFixedWidth 只标记脏，重排要等下一轮事件；不立刻排版的话，宽度
        # 变化与折行变化会互相追赶（滚动条出现→变窄→折行→更窄）而停在错态
        if want != self.minimumWidth():
            self.setFixedWidth(want)
        lay.setGeometry(host.rect())

    def _index(self) -> int:
        lay = self._layout
        for i in range(lay.count()):
            if lay.itemAt(i).widget() is self:
                return i
        return -1


class FlowToolbar(QWidget):
    """工具栏（稿 .toolbar）：FlowLayout + FlowToolbarScrollArea + 右侧弹性占位

    DESIGN.md §5 规定工具栏一律 FlowLayout + FlowToolbarScrollArea（窄窗自动
    换行、控件不重叠），FlowLayout 又不给 spacer 拉伸，所以「左筛选右按钮」
    由 FlowSpacer 自己量行内剩余宽度实现。
    """

    def __init__(self, parent=None, height_cap=132, spacing=None):
        super().__init__(parent)
        from core.flow_widgets import FlowToolbarScrollArea
        from qfluentwidgets import FlowLayout

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.scroll = FlowToolbarScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setMaximumHeight(height_cap)
        outer.addWidget(self.scroll)

        self.body = QWidget(self.scroll)
        self.scroll.setWidget(self.body)
        self.flow = FlowLayout(self.body, needAni=False)
        self.flow.setContentsMargins(2, 4, 2, 4)
        self.flow.setVerticalSpacing(6)
        if spacing:
            self.flow.setHorizontalSpacing(spacing)
        self.spacer = FlowSpacer(self.flow, self.body)
        self.body.installEventFilter(self)
        # 收敛复核用：父对象是本控件，销毁时跟着走，不会回调到已删对象
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(0)
        self._settle_timer.timeout.connect(self._relayout)

    def eventFilter(self, obj, ev):
        # 挂在 body 上而不是自身：reflow 要用 body 的实时宽度，
        # 自身的 Resize 触发时 body 还没被滚动区域重排完
        if obj is self.body and ev.type() == QEvent.Type.Resize:
            self._settle()
        return super().eventFilter(obj, ev)

    def _settle(self):
        """把「占位宽度 ↔ 折行数 ↔ 滚动条」这条回路排到收敛

        内容宽度由视口决定、视口宽度由滚动条决定、滚动条又由折行数决定：
        一次 resize 里走不完这条链（滚动条显隐要等下一轮布局），当场算完
        还得在事件循环空下来后复核一次，否则会停在谁都不认账的错态上。
        """
        self._relayout()
        self._settle_timer.start()

    def _relayout(self):
        if self.body.width() <= 0:
            return
        self.spacer.reflow()
        h = self.flow.heightForWidth(self.body.width())
        if h > 0 and self.body.height() != h:
            # 内容控件的高度平时由滚动区域按 heightForWidth 反算，但折行数
            # 变化时它收不到通知，旧高度会让内容比视口高而白弹一条滚动条
            self.body.setFixedHeight(h)
        self.scroll.refreshHeight()

    def addWidget(self, widget):
        self.flow.addWidget(widget)

    def addRight(self):
        """稿里 ``<span style="flex:1">`` 的位置：此后的控件靠右（只调一次）"""
        self.flow.addWidget(self.spacer)

