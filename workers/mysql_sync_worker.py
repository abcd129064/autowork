# -*- coding: utf-8 -*-
"""MySQL 连接测试后台 Worker

镜像推送 Worker（MysqlSyncWorker）已随机制 B 下线：当前为 MySQL 主库 +
SQLite 兜底双后端模式，读写直连 MySQL，无 SQLite → MySQL 镜像推送路径。
仅保留 MysqlTestWorker 供连接表单「测试连接」使用。
"""

from PySide6.QtCore import QThread, Signal

from database import mysql_sync


class MysqlTestWorker(QThread):
    """异步测试 MySQL 连接"""

    finished = Signal(bool, str)

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self._cfg = cfg

    def run(self):
        ok, msg = mysql_sync.test_connection(self._cfg)
        self.finished.emit(ok, msg)
