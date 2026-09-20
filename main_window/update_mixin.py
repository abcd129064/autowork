# -*- coding: utf-8 -*-
"""MainWindow 自动更新 Mixin（S3 客户端编排层）

职责边界：
  - 本 Mixin 只做「UI 编排」：起 worker、弹对话框、InfoBar 提示、拉起 updater、退出
  - 纯逻辑（版本比较/下载/校验/解压/脚本生成/回执）全在 core/updater.py
  - 对话框交互状态机在 windows/update_dialog.py

安全约束（调研 docs/auto_update_research.md 风险清单）：
  1. 仅打包环境（frozen）允许执行安装：dev 环境替换项目目录会毁掉源码树，
     检查/下载仍可跑（便于联调），但「安装并重启」会被拒绝并提示。
  2. 安装发起前写 .update-pending，重启后 consume_update_receipt 消费回执；
     有 pending 无回执 → 判定失败且**绝不重试**（防每次启动重试失败的死循环）。
  3. updater 退出主程序后才动手，config/logs/database 在换位后从旧目录回迁。
"""

import logging
import os
import sys

from PySide6.QtCore import QTimer

from core import updater
from core.app_paths import get_app_dir
from core.utils import show_info_bar

logger = logging.getLogger(__name__)


class UpdateMixin:
    """自动更新：检查 → 下载 → 安装重启 → 回执消费"""

    # ---------- 环境信息 ----------

    def _update_env(self) -> dict:
        """{base_url, app_dir, main_exe, frozen, local_version}"""
        info = updater.get_install_info()
        info["base_url"] = updater.get_update_base_url()
        return info

    # ---------- 检查更新 ----------

    def check_for_update(self, silent: bool = False):
        """检查更新入口（关于页按钮 / 启动自动检查共用）

        silent=True（启动自检）时：无新版本不提示，失败也不弹错误条。
        worker 挂 self._update_check_worker 防被 GC（QThread 提前析构会崩）。
        """
        if getattr(self, "_update_check_worker", None) is not None:
            return   # 已有检查在跑，忽略重复点击
        env = self._update_env()
        about = getattr(self, "about_page", None)
        if about is not None:
            try:
                about.set_checking(True)
            except Exception:
                pass
        if not silent:
            self._show_info_bar("正在检查更新…", "info", duration=1500)
        try:
            from workers.update_worker import UpdateCheckWorker
            w = UpdateCheckWorker(env["base_url"], env["local_version"],
                                  updater.CHANNEL_AUTOWORK, parent=self)
        except Exception as e:
            logger.warning("创建检查更新 worker 失败: %s", e)
            if about is not None:
                about.set_checking(False)
            if not silent:
                self._show_info_bar(f"检查更新失败：{e}", "error")
            return
        w.found.connect(lambda entry: self._on_update_found(entry, silent, env))
        w.up_to_date.connect(lambda v: self._on_update_latest(v, silent))
        w.error.connect(lambda msg: self._on_update_error(msg, silent))
        w.finished.connect(self._on_check_worker_done)
        self._update_check_worker = w
        w.start()

    def _on_check_worker_done(self):
        self._update_check_worker = None
        about = getattr(self, "about_page", None)
        if about is not None:
            try:
                about.set_checking(False)
            except Exception:
                pass

    def _on_update_latest(self, remote_version: str, silent: bool):
        about = getattr(self, "about_page", None)
        if about is not None:
            about.show_check_result(f"已是最新版本 {remote_version}", "success")
        if not silent:
            self._show_info_bar(f"当前已是最新版本 {remote_version}", "success")

    def _on_update_error(self, msg: str, silent: bool):
        about = getattr(self, "about_page", None)
        if about is not None:
            about.show_check_result("检查更新失败（网络或更新源不可达）", "error")
        if not silent:
            self._show_info_bar(f"检查更新失败：{msg}", "error", duration=4000)
        else:
            logger.info("启动自检更新失败（静默）: %s", msg)

    def _on_update_found(self, entry: dict, silent: bool, env: dict):
        remote = entry.get("_remote_version") or entry.get("version") or ""
        about = getattr(self, "about_page", None)
        if about is not None:
            about.show_check_result(f"发现新版本 {remote}", "warning")
        if silent:
            # 启动自检发现新版本（2026-09-20 需求）：弹出更新对话框 + 导航挂
            # 更新图标。「立即更新」→ 关对话框转后台下载（图标显示进度）；
            # 「稍后」→ 图标保留 found 态，随时可点。
            self._update_pending_entry = entry
            self._update_pending_env = env
            self._set_update_nav("found", f"发现新版本 {remote}，点击下载")
            self._open_update_dialog(entry, env)
            return
        self._open_update_dialog(entry, env)

    # ---------- 导航更新按钮（设置图标上方） ----------

    def init_update_nav(self):
        """导航栏「设置」上方插入更新状态按钮（main_window 导航构建后调用）"""
        try:
            from qfluentwidgets import NavigationItemPosition
            from main_window.update_nav import UpdateNavButton
            btn = UpdateNavButton(self.navigationInterface)
            self.navigationInterface.addWidget(
                "updateNavButton", btn, onClick=self._on_update_nav_clicked,
                position=NavigationItemPosition.BOTTOM,
                tooltip="更新")
            self._update_nav_btn = btn
        except Exception as e:
            logger.warning("创建导航更新按钮失败: %s", e)
            self._update_nav_btn = None

    def _set_update_nav(self, state: str, tooltip: str = ""):
        btn = getattr(self, "_update_nav_btn", None)
        if btn is None:
            return
        try:
            btn.set_state(state, tooltip)
        except Exception as e:
            logger.debug("更新导航按钮状态失败: %s", e)

    def _on_update_nav_clicked(self):
        """按当前状态响应：found/error→下载；downloading→看进度；ready→安装"""
        btn = getattr(self, "_update_nav_btn", None)
        if btn is None:
            return
        state = btn.state
        if state in ("found", "error"):
            entry = getattr(self, "_update_pending_entry", None)
            env = getattr(self, "_update_pending_env", None)
            if entry is None:
                # 状态残留（如重启后）：重新检查
                self.check_for_update(silent=False)
                return
            self._start_background_download(entry, env)
        elif state == "downloading":
            self._show_info_bar(
                getattr(self, "_update_progress_text", "正在下载更新…"),
                "info", duration=3000)
        elif state == "ready":
            self._open_install_confirm()

    def _open_install_confirm(self):
        """下载完成后点图标：弹「安装并重启」确认框"""
        entry = getattr(self, "_update_pending_entry", None) or {}
        staging = getattr(self, "_update_staging_dir", "")
        mode = getattr(self, "_update_ready_mode", "full")
        version = str(entry.get("_remote_version")
                      or entry.get("version") or "")
        try:
            from qfluentwidgets import MessageBox
            box = MessageBox("更新已就绪",
                             f"新版本 {version} 已下载完成。\n"
                             "点击「安装并重启」后程序将关闭并自动完成更新。",
                             self)
            box.yesButton.setText("安装并重启")
            box.cancelButton.setText("稍后")
            if box.exec():
                self._install_update(staging, mode, version)
        except Exception as e:
            logger.warning("安装确认框失败: %s", e)
            self._install_update(staging, mode, version)

    # ---------- 后台下载（对话框关闭后由主窗口持有 worker） ----------

    def _start_background_download(self, entry: dict, env: dict):
        if getattr(self, "_update_dl_worker", None) is not None:
            self._show_info_bar("更新包正在下载中…", "info", duration=2000)
            return
        remote = str(entry.get("_remote_version")
                     or entry.get("version") or "")
        try:
            from workers.update_worker import UpdateDownloadWorker
            w = UpdateDownloadWorker(env["base_url"], entry, env["app_dir"],
                                     main_exe=env["main_exe"],
                                     local_version=env["local_version"],
                                     parent=self)
        except Exception as e:
            self._show_info_bar(f"无法启动下载：{e}", "error", duration=5000)
            return
        self._update_dl_worker = w
        self._update_pending_entry = entry
        self._update_pending_env = env
        self._update_progress_text = f"正在下载新版本 {remote}…"
        self._set_update_nav("downloading", self._update_progress_text)
        w.stage.connect(self._on_bg_stage)
        w.progress.connect(self._on_bg_progress)
        w.ready.connect(self._on_bg_ready)
        w.error.connect(self._on_bg_error)
        w.cancelled.connect(self._on_bg_cancelled)
        w.finished.connect(self._on_bg_finished)
        w.start()

    def _on_bg_stage(self, text: str):
        self._update_progress_text = str(text)
        btn = getattr(self, "_update_nav_btn", None)
        if btn is not None and btn.state == "downloading":
            btn.setToolTip(text)

    def _on_bg_progress(self, done: int, total: int):
        try:
            from windows.update_dialog import _human_size
            if total and total > 0:
                pct = int(done * 100 / total)
                text = (f"正在下载更新 {pct}%"
                        f"（{_human_size(done)}/{_human_size(total)}）")
            else:
                text = f"正在下载更新 {_human_size(done)}"
        except Exception:
            text = "正在下载更新…"
        self._update_progress_text = text
        btn = getattr(self, "_update_nav_btn", None)
        if btn is not None and btn.state == "downloading":
            btn.setToolTip(text)

    def _on_bg_ready(self, info: dict):
        entry = getattr(self, "_update_pending_entry", None) or {}
        remote = str(entry.get("_remote_version")
                     or entry.get("version") or "")
        self._update_staging_dir = (info or {}).get("staging_dir") or ""
        self._update_ready_mode = (info or {}).get("mode") or "full"
        self._set_update_nav("ready", f"新版本 {remote} 已就绪，点击安装")
        self._show_info_bar(
            f"新版本 {remote} 下载完成，点击左侧更新图标安装并重启",
            "success", duration=8000)

    def _on_bg_error(self, msg: str):
        entry = getattr(self, "_update_pending_entry", None) or {}
        remote = str(entry.get("_remote_version")
                     or entry.get("version") or "")
        self._set_update_nav("error", f"下载失败：{msg}（点击重试）")
        self._show_info_bar(
            f"更新下载失败：{msg}，可点击左侧更新图标重试", "error",
            duration=6000)
        logger.warning("后台下载更新失败: %s", msg)

    def _on_bg_cancelled(self):
        entry = getattr(self, "_update_pending_entry", None) or {}
        remote = str(entry.get("_remote_version")
                     or entry.get("version") or "")
        self._set_update_nav("found", f"发现新版本 {remote}，点击下载")

    def _on_bg_finished(self):
        self._update_dl_worker = None

    # ---------- 下载 / 安装 ----------

    def _open_update_dialog(self, entry: dict, env: dict):
        try:
            from windows.update_dialog import UpdateDialog
        except Exception as e:
            logger.warning("导入更新对话框失败: %s", e)
            self._show_info_bar(f"更新界面加载失败：{e}", "error")
            return
        dlg = UpdateDialog(self, entry, env["base_url"], env["app_dir"],
                           main_exe=env["main_exe"],
                           local_version=env["local_version"],
                           on_install=self._install_update,
                           on_download=lambda e: self._start_background_download(
                               e, env))
        self._update_dialog = dlg     # 持有引用，避免 exec 期间被 GC
        try:
            dlg.exec()
        finally:
            self._update_dialog = None

    def _install_update(self, staging_dir: str, mode: str, version: str = "") -> bool:
        """拉起外部 updater 并安排主程序退出；返回 True 表示已发起

        dev 环境拒绝安装（保护源码树）；打包环境写 pending → 拉起 vbs → 退出。
        version 由 UpdateDialog 显式传入（不从对话框内部状态反读，解耦）。
        """
        env = self._update_env()
        if not env["frozen"]:
            self._show_info_bar(
                "开发环境不支持自动安装（会覆盖源码目录）。"
                "请在打包版中更新。", "warning", duration=6000)
            return False
        if not staging_dir or not os.path.isdir(staging_dir):
            self._show_info_bar("更新文件已失效，请重新下载", "error")
            return False
        main_exe = env["main_exe"] or os.path.basename(sys.executable)
        remote = str(version or "")
        pending_info = {"version": remote, "mode": mode,
                        "staging_dir": staging_dir,
                        "from_version": env["local_version"]}
        try:
            updater.launch_updater(env["app_dir"], staging_dir, main_exe,
                                   mode=mode, pending_info=pending_info)
        except Exception as e:
            logger.warning("拉起 updater 失败: %s", e, exc_info=True)
            self._show_info_bar(f"启动更新程序失败：{e}", "error", duration=5000)
            return False
        self._show_info_bar("更新程序已启动，AutoWork 即将关闭…", "info",
                            duration=2500)
        # 让 InfoBar 有机会渲染一帧，再退出事件循环（updater 会等本进程消失）
        QTimer.singleShot(600, self._quit_for_update)
        return True

    def _quit_for_update(self):
        """退出主程序，把文件锁让给 updater"""
        try:
            self.close()
        except Exception:
            pass
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.quit()
            else:
                sys.exit(0)
        except Exception:
            os._exit(0)

    # ---------- 启动回执消费 ----------

    def consume_update_receipt_on_startup(self):
        """启动后消费上次更新的回执（成功/失败都要给用户交代）

        由 main.py 在窗口 show 之后 singleShot 调用；dev 环境也会消费，
        保证联调时残留的 pending 标记不会一直挂着。"""
        try:
            receipt = updater.consume_update_receipt(get_app_dir())
        except Exception as e:
            logger.warning("消费更新回执失败: %s", e)
            return None
        if not receipt:
            return None
        ver = receipt.get("version") or ""
        if receipt.get("ok"):
            self._show_info_bar(
                f"已更新到 {ver}" if ver else "更新完成", "success",
                duration=5000)
        else:
            err = receipt.get("error") or "未知原因"
            self._show_info_bar(
                f"上次更新未完成：{err}（当前仍为 {self._update_env()['local_version']}）",
                "warning", duration=8000)
            logger.warning("更新回执失败: %s", err)
        return receipt

    def auto_check_update_on_startup(self):
        """按配置 update_auto_check 决定启动是否静默自检"""
        try:
            from core import app_settings
            enabled = app_settings.get("update_auto_check", True)
        except Exception:
            enabled = True
        if not enabled:
            return
        # 延迟到工作台首帧之后，避免和启动预热/列表加载抢资源
        QTimer.singleShot(4000, lambda: self.check_for_update(silent=True))
