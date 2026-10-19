# -*- coding: utf-8 -*-
"""主窗口关闭清理（B4）。

从 main_window/main_window.py 的 closeEvent 下沉：Hub 子页 detach、弹出面板、
远程会话、三端进程、TCP/日志/NewLog worker、解码进程与上传收集 worker
按原顺序释放。只通过 host 属性/方法工作；MainWindow.closeEvent 保留
super().closeEvent(event) 与 Qt 事件链。

顺序是关闭路径的硬约束（尤其 _save_remote_sessions 必须在远程窗口 close
之前），修改前先读重构清单与本节注释。
"""

from PySide6.QtCore import QProcess, QThread


def shutdown_main_window(host) -> None:
    """执行主窗口关闭前的资源清理（不调用 super().closeEvent）。"""
    # -1. 先停掉迁入侧边导航的业务子页后台线程
    # 运维/售后/跑视频各 Hub 子页的 worker 挂页面属性上，原先由各
    # FluentWindow 面板的 closeEvent 统一 detach；迁入主窗口后必须在此
    # 补上，否则关闭时线程仍在跑会报 QThread: Destroyed while running
    for hub_name in ("management_hub", "aftersale_hub", "ledger_hub"):
        hub = getattr(host, hub_name, None)
        if hub is not None:
            try:
                hub.detach_workers()
            except Exception:
                pass
    # 弹出面板窗口一并关闭（先于 worker 等待，closeEvent 会各自 detach）
    for win in list(getattr(host, "_hub_popouts", {}).values()):
        try:
            win.close()
        except (RuntimeError, OSError):
            pass
    # 一次性短等待：给所有 worker 200ms 自行收尾。绝不在关闭路径上串行
    # wait（旧实现对每个运行中 worker 各 wait(2000)，是无事件循环线程的
    # 干等，累积出 ~2s 关闭卡顿的根因）
    try:
        QThread.msleep(200)
    except Exception:
        pass

    # 0. 关闭运维管理面板（独立窗口，不随主窗口自动销毁）
    panel = getattr(host, '_table_panel', None)
    if panel is not None:
        host._table_panel = None
        try:
            panel.close()
        except (RuntimeError, OSError):
            pass

    # 1. 关闭统一远程会话中心（单一 frpc 进程 + 全局隧道/会话窗口）
    try:
        from core.frp_remote import get_session_manager
        get_session_manager().shutdown()
    except (RuntimeError, OSError):
        pass

    # 2. 保存远程会话信息（在关闭窗口之前）
    # 必须先落盘再走第 3 步：_save_remote_sessions 是从 win._panels 提取会话的，
    # 而 win.close() 触发的 shutdown_all 会清空面板列表，顺序反了就存不到任何会话
    host._save_remote_sessions()

    # 3. 关闭远程会话标签容器（触发各面板 shutdown：SFTP/SSH/RDP）
    win = getattr(host, '_remote_session_window', None)
    if win is not None:
        host._remote_session_window = None
        try:
            win.close()  # closeEvent → shutdown_all()
        except (RuntimeError, OSError):
            pass

    # 4. 终止正在运行的 SnookerTracking 程序
    rp = getattr(host, 'running_process', None)
    if rp is not None:
        host.running_process = None
        try:
            rp.kill()
            rp.waitForFinished(1000)
        except (RuntimeError, OSError):
            pass

    # 5. 终止三端进程
    for attr in ('_tracking_process', '_backend_process', '_front_process'):
        p = getattr(host, attr, None)
        if p is not None:
            setattr(host, attr, None)
            try:
                if p.state() != QProcess.NotRunning:
                    p.kill()
                    p.waitForFinished(1000)
            except (RuntimeError, OSError):
                pass

    # 6. 清理 TCP worker
    tw = getattr(host, '_tcp_worker', None)
    if tw is not None:
        host._tcp_worker = None
        try:
            if tw.isRunning():
                # lambda 包装必须：PySide6 C++ 直连不持有 Python 引用
                tw.finished.connect(lambda w=tw: w.deleteLater())
            else:
                tw.deleteLater()
        except RuntimeError:
            pass

    # 7. 清理日志加载 worker
    lw = getattr(host, '_log_worker', None)
    if lw is not None:
        host._log_worker = None
        try:
            if lw.isRunning():
                lw.requestInterruption()
                lw.wait(1000)
        except RuntimeError:
            pass

    # 7b. 清理批量整理 worker（NewLog 整理 / 打包上传，中断并短等待）
    for attr in ('_newlog_worker', '_newlog_upload_worker', '_single_video_worker'):
        nw = getattr(host, attr, None)
        if nw is not None:
            setattr(host, attr, None)
            try:
                if nw.isRunning():
                    nw.requestInterruption()
                    nw.wait(1000)
            except (RuntimeError, OSError):
                pass

    # 8. 终止异步解码进程
    dp = getattr(host, '_decode_process', None)
    if dp is not None:
        host._decode_process = None
        try:
            if dp.state() != QProcess.NotRunning:
                dp.kill()
                dp.waitForFinished(1000)
        except (RuntimeError, OSError):
            pass

    # 9. 等待上传收集 worker 结束（文件复制任务，短等待即可）
    for cw in list(getattr(host, '_upload_collect_workers', [])):
        try:
            if cw.isRunning():
                cw.wait(1500)
        except (RuntimeError, OSError):
            pass
    host._upload_collect_workers = []
