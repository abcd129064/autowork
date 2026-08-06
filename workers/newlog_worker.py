# -*- coding: utf-8 -*-
"""NewLog 批量整理 Worker（Task #40 / C8）

将根目录 CLI 脚本 NewLog.py 收编进 GUI：后台线程运行 NewLog.main，
通过自定义 logging.Handler 捕获 NewLog 模块日志，逐行转发到 GUI。

注意：NewLog 依赖 openpyxl，import 延迟到 run() 内执行并带
ImportError 兜底（PyInstaller modulegraph 仍会追踪函数体内的
import，打包产物正常包含 NewLog 与 openpyxl）。
"""

import logging
import traceback

from PySide6.QtCore import QThread, Signal


class _LineSignalHandler(logging.Handler):
    """将 logging 记录格式化后逐行转发为信号（挂到 NewLog 模块 logger 上）"""

    def __init__(self, emit_line):
        super().__init__(level=logging.INFO)
        self._emit = emit_line
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        try:
            self._emit(self.format(record))
        except Exception:
            pass


class NewLogWorker(QThread):
    """后台运行 NewLog.main 的 Worker。

    Signals:
        line(str): 进度输出（NewLog 日志逐行转发）
        finished_ok(str): 成功完成，携带输出结果目录路径
        error(str): 失败/错误信息（详细过程已先经 line 输出）
    """

    line = Signal(str)
    finished_ok = Signal(str)
    error = Signal(str)

    def __init__(self, target_name, parent=None):
        super().__init__(parent)
        self.target_name = str(target_name or "").strip()

    def run(self):
        # NewLog 模块级 logger（__name__ == "NewLog"），挂临时 Handler 捕获输出
        newlog_logger = logging.getLogger("NewLog")
        handler = _LineSignalHandler(self.line.emit)
        old_level = newlog_logger.level
        newlog_logger.addHandler(handler)
        newlog_logger.setLevel(logging.INFO)
        try:
            try:
                import NewLog
            except ImportError as e:
                self.error.emit(f"无法加载 NewLog 模块（请确认已安装 openpyxl）: {e}")
                return

            out_path = NewLog.main(target_name=self.target_name)
            if out_path:
                self.finished_ok.emit(str(out_path))
            else:
                self.error.emit("整理未完成：Excel 不存在或未找到匹配署名的记录（详见上方输出）")
        except Exception as e:
            self.error.emit(f"整理任务异常: {e}")
            self.line.emit(traceback.format_exc())
        finally:
            newlog_logger.removeHandler(handler)
            newlog_logger.setLevel(old_level)
