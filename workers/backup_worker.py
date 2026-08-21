# -*- coding: utf-8 -*-
"""周备份后台 Worker（MySQL → SQLite 兜底基线刷新）

异步执行 fallback_backup.maybe_backup，避免阻塞 UI。
信号：
- progress(str)：阶段进度
- result(bool, str, int)：完成（ok, message, count）
"""

from PySide6.QtCore import QThread, Signal

from database import fallback_backup


class BackupWorker(QThread):
    """异步周备份：到期才真正备份，未到期快速返回"""

    progress = Signal(str)
    result = Signal(bool, str, int)

    def run(self):
        try:
            ok, msg, n = fallback_backup.maybe_backup(
                progress_cb=self.progress.emit)
            self.result.emit(ok, msg, n)
        except Exception as e:
            self.result.emit(False, f"备份异常: {type(e).__name__}: {e}", 0)
