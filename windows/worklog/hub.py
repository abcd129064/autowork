# -*- coding: utf-8 -*-
"""工作留痕 Hub —— 主窗口左侧导航「工作留痕」的五页签容器

设计稿：design/worklog_hub_v1.html（页头、Pivot 规格、页签顺序逐块对齐）
页签：记录量化 │ 时间轴 │ 思维导图 │ 统计汇总 │ 成员与分类

与其余 Hub 的两点差异：
1. **页头在 Hub 层**（稿 292-297）。其他 Hub 的标题画在各子页内部，本稿把
   「工作留痕」+ 副标题画在 Pivot 之上由五页签共享，所以要往 PivotPage 的
   布局里插一个头部行，并把基类那颗浮动弹出按钮改由页头布局定位。
2. **Pivot 项不带图标**（稿 299-303 是纯文本）。图标只给弹出窗口的左侧子导航
   用（main_window.open_hub_popout 的 nav_icons），与 toolHub / remoteHub 同口径。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout

from qfluentwidgets import TitleLabel, CaptionLabel

from core.design_tokens import TYPE_SCALE_PX
from main_window.pivot_page import PivotPage
from windows.worklog.common import Hairline, text_color_tier


class WorkLogHub(PivotPage):
    """工作留痕面板：五页签 Pivot 容器（objectName=worklogHub）

    子页之间只经本类转发，不互相持引用：时间轴点条目 → 记录量化定位、
    成员/分类改动 → 全部页签重查。这样任一页签都能单独替换或弹出。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("worklogHub")
        self._build_head()

        # 子页延迟导入：windows.* 会反向依赖主窗口服务，顶层导入直接成环
        from windows.worklog.records.page import RecordsPage
        from windows.worklog.timeline.page import TimelinePage
        from windows.worklog.mindmap.page import MindmapPage
        from windows.worklog.stats.page import StatsPage
        from windows.worklog.members.page import MembersPage

        self.records_page = RecordsPage(self)
        self.records_page.setObjectName("worklogRecordsPage")
        self.timeline_page = TimelinePage(self)
        self.timeline_page.setObjectName("worklogTimelinePage")
        self.mindmap_page = MindmapPage(self)
        self.mindmap_page.setObjectName("worklogMindmapPage")
        self.stats_page = StatsPage(self)
        self.stats_page.setObjectName("worklogStatsPage")
        self.members_page = MembersPage(self)
        self.members_page.setObjectName("worklogMembersPage")

        # 子页互联（稿 419 / 620）：时间轴点条目落页签1 定位；成员与分类
        # 改动后广播给各页签重拉候选。都经本类转发，子页之间不互相持引用
        self.timeline_page.locate_requested.connect(self._on_locate)
        self.members_page.changed.connect(self._on_options_changed)

        # 注册顺序即页签顺序（稿 299-303，不可调）；lock_pivot_width 固定
        # 切换条宽度，否则各页签字数不同会让 Pivot 在切换时左右抖动
        self.addPage(self.records_page, "记录量化")
        self.addPage(self.timeline_page, "时间轴")
        self.addPage(self.mindmap_page, "思维导图")
        self.addPage(self.stats_page, "统计汇总")
        self.addPage(self.members_page, "成员与分类")
        self.lock_pivot_width()

    # ---------- 页头（稿 292-297） ----------

    def _build_head(self):
        """标题 24px/700 + 副标题 13px(--text3) + 右端弹出按钮 + Pivot 下划线

        弹出按钮沿用基类那一颗（不另建）：hub_popout 隐藏的就是
        ``btn_popout`` / ``lbl_popout_hint`` 这两个对象，另建一颗会永远
        留在弹出窗口里，变成「窗口里再弹一层」。
        """
        lay = self.layout()

        head = QWidget(self)
        row = QHBoxLayout(head)
        row.setContentsMargins(28, 22, 28, 6)   # 稿 73 padding: 22px 28px 6px
        row.setSpacing(14)

        col = QVBoxLayout()
        col.setSpacing(3)                        # 稿 75 margin-top: 3px
        title = TitleLabel("工作留痕", head)
        tf = QFont(title.font())
        tf.setPixelSize(TYPE_SCALE_PX["3xl"])    # 稿 74：24px
        tf.setWeight(QFont.Bold)                 # 稿 74：700（qfw TitleLabel 默认 DemiBold）
        title.setFont(tf)
        col.addWidget(title)

        sub = CaptionLabel("每日工作留痕与量化管理 · 多成员 · 供向上汇报", head)
        sf = QFont(sub.font())
        sf.setPixelSize(TYPE_SCALE_PX["base"])   # 稿 75：13px
        sub.setFont(sf)
        sub.setTextColor(*text_color_tier(3))    # 稿 75：--text3
        col.addWidget(sub)

        row.addLayout(col)
        row.addStretch(1)                        # 稿 76 .popout{margin-left:auto}
        self.lbl_popout_hint.setText("弹出此窗口")
        row.addWidget(self.lbl_popout_hint, 0, Qt.AlignBottom)
        row.addWidget(self.btn_popout, 0, Qt.AlignBottom)

        lay.insertWidget(0, head)
        # 插完页头后布局是 [head, pivot_row, stack]，下划线要落在 Pivot 与
        # 页签体之间（稿 77 .pivot{border-bottom}）
        lay.insertWidget(2, Hairline(self))

    def _sync_popout_hint(self):
        """覆盖基类：按钮由页头布局定位，这里只让提示文本跟随按钮显隐"""
        self.lbl_popout_hint.setVisible(self.btn_popout.isVisible())

    def _reposition_popout_btn(self):
        """覆盖基类的 move() 定位：本 Hub 的弹出按钮在页头布局里，手动挪会打架"""

    # ---------- 子页互联 ----------

    def _on_locate(self, rec):
        """时间轴点条目（稿 419）：先切页签1 再定位，顺序不能反"""
        self.switchTo(self.records_page)
        self.records_page.locate_record(rec)

    def _on_options_changed(self):
        """成员/分类被页签5 改动：各页签的下拉候选与配色都来自这两张表"""
        for page in self.pages():
            hook = getattr(page, "on_options_changed", None)
            if hook:
                hook()
