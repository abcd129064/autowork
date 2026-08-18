# -*- coding: utf-8 -*-
"""售后数据后台 Worker

通用封装：将 aftersale_db / table_db 的同步 DB 操作移到工作线程，
避免阻塞 GUI。finished 信号返回结果，error 信号返回异常信息。
"""

from PySide6.QtCore import QThread, Signal


class AftersaleDBWorker(QThread):
    """后台数据库查询/保存 Worker（通用 fn(*args, **kwargs) 封装）"""

    finished = Signal(object)
    error = Signal(str)

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            result = self.func(*self.args, **self.kwargs)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")
