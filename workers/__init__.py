# -*- coding: utf-8 -*-
"""workers 包：后台 QThread 工作线程。

采用 PEP 562 惰性导出：导入 workers.table_worker 时不再连带导入
network_workers（后者依赖 paramiko）。这样缺少可选依赖的环境仍能
单独使用无关的 worker，也让测试可以按子模块精确收集。
"""

from importlib import import_module

# 旧的包级导出名 → 所属子模块。名字与模块均保持不变，仅把导入时机推迟。
_LAZY_EXPORTS = {
    "TCPWorker": ".network_workers",
    "SFTPListWorker": ".network_workers",
    "SFTPOperationWorker": ".network_workers",
    "SFTPDirTransferWorker": ".network_workers",
    "SFTPConnectWorker": ".network_workers",
    "SSHConnectWorker": ".network_workers",
    "SSHExecWorker": ".network_workers",
    "TableFetchWorker": ".table_worker",
    "SnookerOmFetchWorker": ".table_worker",
    "DevicesFetchWorker": ".table_worker",
    "MysqlTestWorker": ".mysql_sync_worker",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name):
    """按需导入包级导出名（PEP 562）。"""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
