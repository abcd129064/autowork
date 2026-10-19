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
        """在构造期就接好结果槽，避免调用方漏连导致提示静默丢失。"""
        super().__init__(parent)
        self.result.connect(self._on_result)

    def run(self):
        """在线程里执行合并，任何异常都转成 result 信号返回。

        QThread.run 里抛出的异常不会传回创建者，界面会一直等不到结果，
        所以必须在这里兜住并 emit 失败，而不是让它冒泡。
        """
        try:
            ok, msg, n = merge_back.merge_back(progress_cb=self.progress.emit)
            self.result.emit(ok, msg, n)
        except Exception as e:
            self.result.emit(False, f"合并异常: {type(e).__name__}: {e}", 0)

    @staticmethod
    def _on_result(ok, msg, count):
        """合并完成：找到 MainWindow 弹提示；找不到落盘日志。

        不保存 main_window 引用的原因：worker 可能由非主线程的
        _trigger_merge_back 创建，那时主窗口未必存在；按能力（有没有
        _show_info_bar）遍历顶层窗口取用，既不阻止窗口回收也不造成循环引用。
        写成静态方法是有意的：槽不读任何实例状态，也就不必为它保活 worker 对象。
        """
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
        # 弹窗只是尽力而为：主窗口可能正在退出，碰控件会抛异常，
        # 不能让它吃掉后面的落盘降级
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

