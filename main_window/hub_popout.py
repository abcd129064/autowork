# -*- coding: utf-8 -*-
"""HubPopoutWindow —— 通用「弹出面板」独立窗口（2026-09-07 需求）

把无旧版独立窗口对应的 Hub（工具/远程）以独立 FluentWindow 重新打开，
效果对应 FluentWindow 单窗口重构前「主界面点导航弹出子面板」的形态：
新实例化一个 Hub 嵌入窗口作为唯一界面，Pivot 二级切换照常可用。

宿主代理：Hub 工作区代码里的 `self._win` 即本窗口（构造时 parent 传入），
故本窗口必须代理主窗口的宿主接口：
  - _show_info_bar：InfoBar 落在弹出窗口自身（体验更好）
  - _append_log / _load_settings / _save_settings：转发主窗口（日志与
    配置单源，弹窗与内嵌视图改的是同一份 settings 域文件）
  - videos_dir：实时读主窗口属性（设置保存后会更新）
  - busy 守卫属性（_single_video_worker 等）：经 __getattr__/__setattr__
    读写主窗口 —— 弹出窗口与内嵌视图共享互斥，防止双开 worker。
"""
from qfluentwidgets import FluentWindow, FluentIcon

# busy 守卫属性（tool_hub 工作区经 getattr/setattr(win, ...) 读写）
_BUSY_ATTRS = ("_single_video_worker", "_newlog_worker",
               "_newlog_upload_worker")


class HubPopoutWindow(FluentWindow):
    """通用面板弹出窗口：一个 Hub 新实例作为唯一子界面"""

    def __init__(self, hub_cls, main_win, title):
        super().__init__()
        self._main = main_win
        self.setWindowTitle(title)
        self.resize(1450, 860)
        self.setMinimumSize(900, 560)

        self.hub = hub_cls(self)
        # 弹出窗口内不再提供二次弹出
        self.hub.btn_popout.setVisible(False)
        self.addSubInterface(self.hub, FluentIcon.LIBRARY, title)
        try:
            from core.perf import is_acrylic_enabled
            self.navigationInterface.setAcrylicEnabled(is_acrylic_enabled())
        except Exception:
            pass

    # ---------- 宿主代理（与主窗口 settings_mixin / ui 接口同签名） ----------

    def _show_info_bar(self, message, message_type="info", title=None,
                       duration=2500):
        from core.utils import show_info_bar
        show_info_bar(message, message_type=message_type, title=title,
                      duration=duration, parent=self)

    def _append_log(self, text):
        fn = getattr(self._main, "_append_log", None)
        if fn is not None:
            try:
                fn(text)
            except Exception:
                pass

    def _load_settings(self):
        fn = getattr(self._main, "_load_settings", None)
        return fn() if fn is not None else {}

    def _save_settings(self, data):
        fn = getattr(self._main, "_save_settings", None)
        if fn is not None:
            fn(data)

    @property
    def videos_dir(self):
        return getattr(self._main, "videos_dir", "") or ""

    # ---------- busy 守卫转发主窗口（跨窗口互斥） ----------

    def __getattr__(self, name):
        # 仅在常规属性查找失败时进入：busy 属性统一以主窗口为准
        if name in _BUSY_ATTRS:
            return getattr(self._main, name, None)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name in _BUSY_ATTRS:
            setattr(self._main, name, value)
            return
        super().__setattr__(name, value)

    # ---------- 关闭清理：与主窗口 closeEvent 的 Hub 口径一致 ----------

    def closeEvent(self, event):
        try:
            self.hub.detach_workers()
        except Exception:
            pass
        super().closeEvent(event)
