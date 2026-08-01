# -*- coding: utf-8 -*-
"""球桌数据 API 异步请求 Worker（独立模块，与原有业务代码解耦）"""

import requests
from PySide6.QtCore import QThread, Signal

# API 基础配置
BASE_URL = "https://wechat2-billiard.newbv.cn/prod-api/api/billiardtable/listext"
DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "AutoWork/1.0",
    "version": "1.4.0",
}


class TableFetchWorker(QThread):
    """异步拉取全量球桌数据（pageSize=1000 一次拉完，写入本地库）

    Signals:
        result_ready(list): 全量数据列表
        error(str): 错误信息
    """
    result_ready = Signal(list)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

    def run(self):
        try:
            params = {
                "pageNo": 1,
                "pageSize": 1000,
                "roomName": "", "roomInfo": "", "roomStatus": "",
                "calculated": "", "sales": "", "code": "",
                "name": "", "deviceVersion": "", "tableType": "",
                "contractStatus": "", "feeStatus": "",
                "startTime": "", "endTime": "",
                "billingStartDate": "", "billingEndDate": "",
                "contractEndStartDate": "", "contractEndEndDate": "",
                "startLastPlay": "", "endLastPlay": "",
                "status": "", "unlinedays": "", "nomatchdays": "",
                "onlineStatus": "",
            }
            resp = requests.get(BASE_URL, params=params,
                                headers=DEFAULT_HEADERS, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 200:
                self.error.emit(f"接口返回错误: {data.get('msg', '未知错误')}")
                return
            inner = data.get("data") or {}
            rows = inner.get("lists") or []
            self.result_ready.emit(rows)
        except requests.exceptions.Timeout:
            self.error.emit("请求超时（20秒），请检查网络后重试")
        except requests.exceptions.ConnectionError:
            self.error.emit("网络连接失败，请检查网络")
        except Exception as e:
            self.error.emit(f"获取数据失败: {e}")
