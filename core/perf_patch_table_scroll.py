# -*- coding: utf-8 -*-
"""表格滚动位块搬移补丁（B1 patch 层第六片）。

从 core/perf.py 下沉：TableBase.__init__ / updateGeometries 后调用
apply_table_blit；主题切换时清空已标定底色并重新应用。apply_blit 由
core.perf 注入，保留 apply_table_blit 的 monkeypatch 点。

补丁打在 TableBase 而不是子类上：表格控件早已散落各处，逐个改继承不现实。
apply_blit 由外部注入也正为此：perf 层留着自己的开关和降级判断，这里只管
「什么时候调用」。
"""


def apply_table_scroll_blit_patch(apply_blit) -> None:
    """安装表格滚动位块搬移补丁（幂等）。

    重复调用靠 _perf_blit_patched 挡住：包装是叠加式的，多装一次就会让每次
    updateGeometries 多调一次 apply_blit，滚动性能反而倒退。
    """
    try:
        from PySide6.QtGui import QPainter  # noqa: F401
        from PySide6.QtWidgets import QTableView
        from qfluentwidgets.components.widgets.table_view import TableBase
    except Exception:
        # 缺 qfluentwidgets 或跑在纯逻辑环境时直接放弃：加速是可选增强，
        # 不能因为它让导入 core.perf 的一方整体失败
        return
    if getattr(TableBase, "_perf_blit_patched", False):
        return

    # 从 QTableView 上取原实现，而不是从 TableBase 的属性取：
    # TableBase 是否覆写它不由我们控制，从基类拿能保证取到未包装的版本
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
        """主题切换后清缓存重标定：底色按旧主题采样，不清会沿用错色。"""
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
        # 主题信号并非所有 qfluentwidgets 版本都有；连不上最多是切主题后
        # 底色旧到重启，不能让这里抛错把已经装好的补丁一起废掉
        pass
