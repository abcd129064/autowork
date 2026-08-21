# -*- coding: utf-8 -*-
"""售后数据后台 Worker

通用封装：将 aftersale_db / table_db 的同步 DB 操作移到工作线程，
避免阻塞 GUI。result_ready 信号返回结果，error 信号返回异常信息。
"""

from PySide6.QtCore import QThread, Signal

# 运行中的 worker 保活集合：QThread 对象在 run() 结束前保持强引用，
# 防止频繁刷新时旧 worker 被 GC 销毁导致 `QThread: Destroyed while thread is still running` 崩溃
_running = set()


class AftersaleDBWorker(QThread):
    """后台数据库查询/保存 Worker（通用 fn(*args, **kwargs) 封装）"""

    result_ready = Signal(object)  # 查询/保存结果（不能命名为 finished，会遮蔽 Qt 原生 finished）
    error = Signal(str)

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        _running.add(self)  # 保活：线程退出前不被 GC
        # 用 Qt 原生 finished 做清理：它由 Qt 在 run() 返回后（线程已标记结束）发射，
        # Queued 到主线程执行 _release 时销毁是安全的。
        # 注意：PySide6 中 super().finished 会被子类同名信号遮蔽，不能用于此目的
        self.finished.connect(self._release)

    def _release(self):
        """线程已退出，可安全释放保活引用（Queued 到主线程执行）"""
        _running.discard(self)

    def run(self):
        try:
            result = self.func(*self.args, **self.kwargs)
            if self.isInterruptionRequested():
                return  # 已被新的查询取代，丢弃过期结果
            self.result_ready.emit(result)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(f"{type(e).__name__}: {e}")
