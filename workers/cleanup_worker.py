# -*- coding: utf-8 -*-
"""数据保留清理后台 Worker（异步执行，避免阻塞 UI）

信号：
- progress(str)：阶段进度
- result(bool, str, int)：完成（ok, message, deleted_count）
"""

from PySide6.QtCore import QThread, Signal

from database import data_retention


class CleanupWorker(QThread):
    """异步数据保留清理：过期分区删除 + 按大小清理"""

    progress = Signal(str)
    result = Signal(bool, str, int)

    def run(self):
        try:
            ok, msg, n = data_retention.run_cleanup(
                progress_cb=self.progress.emit)
            self.result.emit(ok, msg, n)
        except Exception as e:
            self.result.emit(False,
                             f"数据清理异常: {type(e).__name__}: {e}", 0)
