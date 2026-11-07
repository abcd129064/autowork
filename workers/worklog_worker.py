# -*- coding: utf-8 -*-
"""工作留痕后台 Worker（查询/写入统一入口）

不另写一份 QThread 封装：workers/aftersale_worker.py 的 AftersaleDBWorker
已经是通用 ``func(*args, **kwargs)`` 包装（跑视频/运维/远程都在复用），
再抄一份只会多出两处保活集合与两处 run() 需要同步修。

本模块的增量只有一个 ``spawn()``：把 result_ready 与 error 做成两个必填
位置参数，让「漏接 error」在调用点就写不出来 —— 只接 result 的 worker
一旦抛异常就静默失败，界面停在旧数据上且不报错，这类 bug 在售后与
跑视频两处都出现过。

线程归属：调用方（各页签）把返回的 worker 存到 ``_xxx_worker`` 属性上，
PivotPage.detach_workers 靠这个后缀扫描并请求中断；出栈即丢引用会让
线程跑到一半被 GC，触发 QThread: Destroyed while thread is still running。
"""

from workers.aftersale_worker import AftersaleDBWorker as WorklogDBWorker


def spawn(func, on_result, on_error, *args, **kwargs) -> WorklogDBWorker:
    """起一个后台 DB 任务并已连接 result/error 双信号，返回 worker 本体

    代数守卫（丢弃过期响应）由调用方在闭包里带 seq 完成，本函数不掺和：
    worker 只负责把结果原样送出，谁该被丢弃是页面状态机的事。
    """
    worker = WorklogDBWorker(func, *args, **kwargs)
    worker.result_ready.connect(on_result)
    worker.error.connect(on_error)
    worker.start()
    return worker


__all__ = ["WorklogDBWorker", "spawn"]
