# -*- coding: utf-8 -*-
"""表格 hover 条带重绘补丁（B1 patch 层第三片）。

从 core/perf.py 下沉：拦截 qfluentwidgets TableBase._setHoverRow，只重绘
hover 新旧两行条带，避免整视口重绘。不依赖 core.perf / 配置。

之所以在类属性上打补丁而不是子类化：业务表控件在补丁安装之前就已实例化，
换基类只能影响之后新建的对象，已经存在的表拿不到这层加速。
"""


def apply_table_hover_patch() -> None:
    """安装 TableBase hover 条带重绘补丁（幂等）。

    qfluentwidgets 原实现在 hover 行变化时会 update() 整个 viewport，
    大表（数千行）上每次鼠标划过都是一次全视口重绘，掉帧明显。
    这里把重绘范围收缩到「旧行 + 新行」两个水平条带。
    """
    try:
        from qfluentwidgets.components.widgets.table_view import TableBase
    except Exception:
        # qfluentwidgets 缺失或内部结构变了：补丁是可选加速，不装也不算故障
        return
    # 标记挂在类上而不是模块全局：多个入口都会调 apply，类属性才是唯一事实源
    if getattr(TableBase, "_perf_hover_patched", False):
        return

    def _setHoverRow(self, row):
        # 旧行号从 delegate 取而不是自己缓存：leaveEvent 与键盘导航会绕过本函数直接改它
        old = self.delegate.hoverRow
        # 同一行反复 set 只会让 Qt 重新排队一次绘制，鼠标在行内微动时这个分支最常命中
        if old == row:
            return
        self.delegate.setHoverRow(row)
        # 句柄与行数各取一次：下面几次都是同一次重绘内的跨 C++ 边界调用，重取纯属白开销
        vp = self.viewport()
        total = self.model().rowCount()
        # 只刷两行条带：行高可能为 0（自动行高未算出），此时没有可见区域可更新
        for r in (old, row):
            # model 可能在 hover 期间被清空或收缩，行号越界时 rowHeight 的结果没有意义
            if r is None or not 0 <= r < total:
                continue
            h = self.rowHeight(r)
            if h <= 0:
                continue
            y = self.rowViewportPosition(r)
            # update(rect) 而不是 update()：条带之外的像素不进绘制队列
            vp.update(0, y, vp.width(), h)

    # 绑到类而不是逐个实例：每张表共用这一份实现，也省掉 per-instance 包装
    TableBase._setHoverRow = _setHoverRow
    # 置位必须在绑定之后，绑定中途抛异常时不能留下「已打补丁」的假标记
    TableBase._perf_hover_patched = True
