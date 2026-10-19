# -*- coding: utf-8 -*-
"""后台反查 kd 记录分类（B4：从 main_window 内嵌类迁出）。"""

from PySide6.QtCore import QThread, Signal


class _KdStatusQueryWorker(QThread):
    """后台反查 kd 记录分类（C6 正向，需求19）

    原实现主线程同步调 table_db.find_kd_file_status：MySQL 空闲连接被
    服务器断开（wait_timeout / 网络抖动）后，pymysql 查询会阻塞至
    read_timeout(60s) 才抛 2013，界面假死。移入后台线程后即使慢/失败
    也不卡 UI；结果经 done 信号回主线程更新状态栏。
    """
    done = Signal(object)  # info dict；查询失败/未命中传 None

    def __init__(self, device_code, date_str, clip_base, parent=None):
        super().__init__(parent)
        self._device_code = device_code
        self._date_str = date_str
        self._clip_base = clip_base

    def run(self):
        try:
            from database import table_db
            info = table_db.find_kd_file_status(
                self._device_code, self._date_str, self._clip_base)
        except Exception:
            info = None
        self.done.emit(info)
