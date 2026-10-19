# -*- coding: utf-8 -*-
"""表格 hover 条带重绘补丁（B1 patch 层第三片）。

从 core/perf.py 下沉：拦截 qfluentwidgets TableBase._setHoverRow，只重绘
hover 新旧两行条带，避免整视口重绘。不依赖 core.perf / 配置。
"""


def apply_table_hover_patch() -> None:
    """安装 TableBase hover 条带重绘补丁（幂等）。"""
    try:
        from qfluentwidgets.components.widgets.table_view import TableBase
    except Exception:
        return
    if getattr(TableBase, "_perf_hover_patched", False):
        return

    def _setHoverRow(self, row):
        old = self.delegate.hoverRow
        if old == row:
            return
        self.delegate.setHoverRow(row)
        vp = self.viewport()
        total = self.model().rowCount()
        for r in (old, row):
            if r is None or not 0 <= r < total:
                continue
            h = self.rowHeight(r)
            if h <= 0:
                continue
            y = self.rowViewportPosition(r)
            vp.update(0, y, vp.width(), h)

    TableBase._setHoverRow = _setHoverRow
    TableBase._perf_hover_patched = True
