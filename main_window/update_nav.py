# -*- coding: utf-8 -*-
"""导航栏更新按钮（2026-09-20 需求：设置图标上方显示下载状态）

状态机（由 UpdateMixin 驱动）：
  hidden       无更新/未开始 → 不显示
  found        发现新版本（含「稍后再说」后保留）→ UPDATE 云图标
  downloading  后台下载中 → DOWNLOAD 图标，tooltip 带进度百分比
  ready        下载完成待安装 → SYNC 图标，点击弹安装确认
  error        下载失败 → 回 found 态（tooltip 带原因），可重试
"""

from qfluentwidgets import NavigationPushButton, FluentIcon

_STATE_ICONS = {
    "found": FluentIcon.UPDATE,
    "downloading": FluentIcon.DOWNLOAD,
    "ready": FluentIcon.SYNC,
    "error": FluentIcon.UPDATE,
}


class UpdateNavButton(NavigationPushButton):
    """更新状态图标按钮（不可选中路由，纯动作按钮）"""

    def __init__(self, parent=None):
        super().__init__(FluentIcon.UPDATE, "更新", isSelectable=False,
                         parent=parent)
        self.setObjectName("updateNavButton")
        self.setVisible(False)
        self._state = "hidden"

    @property
    def state(self) -> str:
        return self._state

    def set_state(self, state: str, tooltip: str = ""):
        """切状态；state=hidden 时隐藏按钮"""
        if state == "hidden":
            self._state = "hidden"
            self.setVisible(False)
            return
        icon = _STATE_ICONS.get(state, FluentIcon.UPDATE)
        if state != self._state:
            self.setIcon(icon)
        self._state = state
        self.setToolTip(tooltip or "更新")
        self.setVisible(True)

    def showEvent(self, e):
        """Qt 坑：控件从未显示过时，构造里的 setVisible(False) 不算
        「显式隐藏」，父导航栏首次 show() 会把它一起带显——这里按状态
        强制保持隐藏，保证 hidden 态（无更新）永远不打扰用户。"""
        super().showEvent(e)
        if self._state == "hidden" and self.isVisible():
            self.setVisible(False)
