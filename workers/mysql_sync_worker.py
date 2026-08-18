# -*- coding: utf-8 -*-
"""MySQL 同步后台 Worker

信号：
- progress(str): 阶段进度（"正在同步 billiard_tables..."）
- success(int, str): 成功（总条数, 耗时描述）
- error(str): 失败信息
"""

import time
from PySide6.QtCore import QThread, Signal

from database import mysql_sync


class MysqlSyncWorker(QThread):
    """异步推送本地 SQLite 数据到远程 MySQL"""

    progress = Signal(str)
    success = Signal(int, str)
    error = Signal(str)

    def __init__(self, table_name: str = None, parent=None, cfg: dict = None):
        """table_name 为空时推送全部表，否则只推指定表

        cfg: 显式连接配置（「立即同步」用表单最新值，避免读旧配置）；
        table_name="aftersale_records" 时走售后业务键 upsert 推送。
        """
        super().__init__(parent)
        self._table_name = table_name
        self._cfg = cfg

    def run(self):
        try:
            t0 = time.time()
            if self._table_name == "aftersale_records":
                ok, msg, count = mysql_sync.push_aftersale(
                    self._cfg, progress_cb=self.progress.emit)
            elif self._table_name:
                ok, msg, count = mysql_sync.push_table(
                    self._table_name, progress_cb=self.progress.emit)
            else:
                ok, msg, count = mysql_sync.push_all(
                    progress_cb=self.progress.emit)
            elapsed = time.time() - t0
            if ok:
                self.success.emit(count, f"{msg}（{elapsed:.1f}s）")
            else:
                self.error.emit(msg)
        except Exception as e:
            self.error.emit(f"同步异常: {type(e).__name__}: {e}")


class MysqlTestWorker(QThread):
    """异步测试 MySQL 连接"""

    finished = Signal(bool, str)

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self._cfg = cfg

    def run(self):
        ok, msg = mysql_sync.test_connection(self._cfg)
        self.finished.emit(ok, msg)
