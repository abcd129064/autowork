# -*- coding: utf-8 -*-
"""兜底增量合并回写 Worker（MySQL 恢复后 LWW 合并）

异步执行 merge_back.merge_back，避免阻塞 _get_conn 调用方。
信号：
- progress(str)：阶段进度
- result(bool, str, int)：完成（ok, message, count）

结果提示：worker 可能从非主线程的 _trigger_merge_back 创建，无法直接持有
main_window。result 信号连到 _on_result，通过 QApplication 顶层窗口找到
MainWindow 调 _show_info_bar；找不到则降级 conn_logger 落盘。
"""

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication

from database import merge_back


class MergeBackWorker(QThread):
    """异步合并 SQLite 兜底增量回 MySQL（LWW）"""

    progress = Signal(str)
    result = Signal(bool, str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result.connect(self._on_result)

    def run(self):
        try:
            ok, msg, n = merge_back.merge_back(progress_cb=self.progress.emit)
            self.result.emit(ok, msg, n)
        except Exception as e:
            self.result.emit(False, f"合并异常: {type(e).__name__}: {e}", 0)

    @staticmethod
    def _on_result(ok, msg, count):
        """合并完成：找到 MainWindow 弹提示；找不到落盘日志"""
        if count == 0 and ok:
            return  # 无增量不打扰
        try:
            app = QApplication.instance()
            if app:
                for w in app.topLevelWidgets():
                    if hasattr(w, "_show_info_bar"):
                        if ok:
                            w._show_info_bar(f"兜底数据已合并回 MySQL：{msg}",
                                             "success", duration=3000)
                        else:
                            w._show_info_bar(f"兜底数据合并失败：{msg}",
                                             "warning", duration=4000)
                        return
        except Exception:
            pass
        # 降级：落盘连接日志
        try:
            from core.conn_logger import conn_logger
            if ok:
                conn_logger.info("merge_back", f"合并完成：{msg}")
            else:
                conn_logger.error("merge_back", f"合并失败：{msg}")
        except Exception:
            pass

