# -*- coding: utf-8 -*-
"""面板/工具导航入口（G8/G11 活代码）。

从 main_window.ui_mixin 下沉的可独立函数：面板入口弹出策略、球桌/售后/
运维/工具页跳转，以及连接诊断单例窗口。所有函数只通过 host 的属性/方法
工作，不 import 主窗口；UIMixin 保留同名薄委托。

G9/G10 的旧弹窗与 NewLog 全链仍是待删死代码，不在本模块。
"""


def panel_entry_popout_enabled(host):
    """工具栏面板入口（跑视频/售后/球桌管理）是否默认弹出独立面板。

    设置项 panel_entry_popout（ui 域，默认 True，2026-09-20）：
    开 = 点入口弹出独立面板窗口（open_hub_popout）；
    关 = 回落为主窗口内导航跳转（switchTo）。
    """
    try:
        return bool(host._load_settings().get("panel_entry_popout", True))
    except Exception:
        return True


def open_table_panel(host):
    """打开球桌管理：默认弹出独立面板；关闭设置时回落主窗口「运维管理」页。"""
    hub = getattr(host, "management_hub", None)
    if hub is None:
        return
    if host._panel_entry_popout_enabled():
        host.open_hub_popout(hub)
    else:
        host.switchTo(hub)


def open_aftersale(host):
    """打开售后面板：默认弹出独立面板；关闭设置时回落主窗口「售后」页。"""
    hub = getattr(host, "aftersale_hub", None)
    if hub is None:
        return
    if host._panel_entry_popout_enabled():
        host.open_hub_popout(hub)
    else:
        host.switchTo(hub)


def open_conn_diag(host):
    """打开连接诊断面板（非模态独立窗口，单例：已打开则激活）。"""
    from windows.remote_session.conn_diag_panel import ConnDiagPanel
    if not hasattr(host, "_conn_diag") or host._conn_diag is None:
        host._conn_diag = ConnDiagPanel()
        host._conn_diag.destroyed.connect(
            lambda: setattr(host, "_conn_diag", None))
    host._conn_diag.show()
    host._conn_diag.raise_()
    host._conn_diag.activateWindow()


def open_tool_hub(host, work=None):
    """工具入口统一跳转二期独立工具页（main_window.tool_hub）。

    work 可为工作区属性名（single_video_work/port_fake_work/
    upload_list_work/newlog_work），缺省保持当前页。
    """
    hub = getattr(host, "tool_hub", None)
    if hub is None:
        return
    host.switchTo(hub)
    if work:
        page = getattr(hub, work, None)
        if page is not None:
            hub.switchTo(page)
