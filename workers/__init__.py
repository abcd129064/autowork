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

# __all__ 由映射表派生：手写第二份名单迟早会和 _LAZY_EXPORTS 对不上，
# 届时 from workers import * 会漏名字且不报错
__all__ = list(_LAZY_EXPORTS)


def __getattr__(name):
    """按需导入包级导出名（PEP 562）。

    首次访问某个 worker 时才导入它所在的子模块，因此 import workers 本身
    不会拉起 paramiko。解析到的值写回 globals()，后续访问不再走这里。
    """
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        # 必须抛货真价实的 AttributeError：hasattr / typing / pickle 都靠它
        # 判断「这个包里没有该名字」，换成 ImportError 会被当成包本身损坏
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    # 第二个参数把相对名锚定到本包，否则 ".network_workers" 会按顶层模块解析
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__():
    # 显式补齐 __all__：惰性导出的名字不在 globals() 里，dir() 会漏掉它们，
    # 影响 IDE 补全与 from workers import * 的提示
    return sorted(set(globals()) | set(__all__))
