# -*- coding: utf-8 -*-
"""表格滚动位块搬移补丁（B1 patch 层第六片）。

从 core/perf.py 下沉：TableBase.__init__ / updateGeometries 后调用
apply_table_blit；主题切换时清空已标定底色并重新应用。apply_blit 由
core.perf 注入，保留 apply_table_blit 的 monkeypatch 点。
"""


def apply_table_scroll_blit_patch(apply_blit) -> None:
    """安装表格滚动位块搬移补丁（幂等）。"""
    try:
        from PySide6.QtGui import QPainter  # noqa: F401
        from PySide6.QtWidgets import QTableView
        from qfluentwidgets.components.widgets.table_view import TableBase
    except Exception:
        return
    if getattr(TableBase, "_perf_blit_patched", False):
        return

    orig_init = TableBase.__init__
    orig_update = QTableView.updateGeometries

    def _init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        apply_blit(self)

    def _update_geometries(self):
        orig_update(self)
        apply_blit(self)

    TableBase.__init__ = _init
    TableBase.updateGeometries = _update_geometries
    TableBase._perf_blit_patched = True

    def _on_theme_changed(*_args):
        try:
            from PySide6.QtWidgets import QApplication, QTableView as _TV
            for w in QApplication.topLevelWidgets():
                for t in w.findChildren(_TV):
                    t._perf_blit_bg = None
                    apply_blit(t)
        except Exception:
            pass

    try:
        from qfluentwidgets.common.config import qconfig
        qconfig.themeChangedFinished.connect(_on_theme_changed)
    except Exception:
        pass
