# -*- coding: utf-8 -*-
"""单杆视频生成 Worker（工具菜单「单杆视频」）

后台线程运行 tools.single_video_tool.generate_json（逐帧渲染为 CPU
密集任务，必须离开 UI 线程），通过自定义 logging.Handler 捕获
"SingleShotVideo" 模块日志逐行转发到 GUI。

注意：tools 模块依赖 cv2/numpy/Pillow，import 延迟到 run() 内执行并带
ImportError 兜底（与 NewLogWorker 同模式，缺失依赖时给出明确提示）。
"""

import logging
import traceback
from PySide6.QtCore import QThread, Signal


class _LineSignalHandler(logging.Handler):
    """将 logging 记录格式化后逐行转发为信号（挂到单杆模块 logger 上）"""

    def __init__(self, emit_line):
        super().__init__(level=logging.INFO)
        self._emit = emit_line
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        try:
            self._emit(self.format(record))
        except Exception:
            pass


class SingleVideoWorker(QThread):
    """后台执行单杆视频生成的 Worker。

    Signals:
        line(str): 进度输出（单杆模块日志逐行转发）
        finished_ok(str): 成功完成，携带生成的视频文件路径
        error(str): 失败/错误信息（详细过程已先经 line 输出）
    """

    line = Signal(str)
    finished_ok = Signal(str)
    error = Signal(str)

    def __init__(self, params: dict, parent=None):
        super().__init__(parent)
        self.params = dict(params)

    def run(self):
        # 单杆模块级 logger（__name__ 为 "SingleShotVideo"），挂临时 Handler 捕获输出
        single_logger = logging.getLogger("SingleShotVideo")
        handler = _LineSignalHandler(self.line.emit)
        old_level = single_logger.level
        single_logger.addHandler(handler)
        single_logger.setLevel(logging.INFO)
        try:
            try:
                from tools.single_video_tool import generate_json
            except ImportError as e:
                self.error.emit(
                    f"无法加载单杆视频模块（请确认已安装 opencv-python、numpy、Pillow）: {e}")
                return

            result = generate_json(**self.params)
            if result and isinstance(result, tuple):
                _, video_path, _ = result
                self.finished_ok.emit(str(video_path))
            else:
                self.error.emit("单杆视频生成失败，详见上方输出")
        except Exception as e:
            self.error.emit(f"单杆视频生成异常: {e}")
            self.line.emit(traceback.format_exc())
        finally:
            single_logger.removeHandler(handler)
            single_logger.setLevel(old_level)
