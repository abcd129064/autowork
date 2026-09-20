# -*- coding: utf-8 -*-
"""自动更新后台 Worker（检查 / 下载解压两段，均不阻塞 UI）

纯逻辑在 core/updater.py（无 Qt 依赖、可单测），本文件只做线程与信号适配。

信号约定：
- UpdateCheckWorker: found(dict) / up_to_date(str) / error(str)
- UpdateDownloadWorker: stage(str) / progress(int, int) / ready(dict)
                        / error(str) / cancelled()

取消语义（沿用项目 DB worker 范式）：调用方只 requestInterruption()，
worker 在下载分片边界自检 isInterruptionRequested 并抛 InterruptedError，
不强行 kill 线程。
"""

import logging

from PySide6.QtCore import QThread, Signal

from core import updater

logger = logging.getLogger(__name__)


class UpdateCheckWorker(QThread):
    """检查更新：拉 latest.json 比对本地版本

    found(entry)   —— 有新版本（entry 含 version/notes/package/_resolved_url）
    up_to_date(v)  —— 已是最新，v 为远端版本号
    error(msg)     —— 网络/格式异常（调用方按「检查失败」提示，不影响使用）
    """

    found = Signal(dict)
    up_to_date = Signal(str)
    error = Signal(str)

    def __init__(self, base_url: str, local_version: str,
                 channel: str = updater.CHANNEL_AUTOWORK, parent=None):
        super().__init__(parent)
        self._base_url = base_url
        self._local = local_version
        self._channel = channel

    def run(self):
        try:
            entry = updater.check_update(self._base_url, self._local,
                                         self._channel)
        except Exception as e:
            logger.warning("检查更新失败: %s", e)
            self.error.emit(f"{type(e).__name__}: {e}")
            return
        if entry is None:
            # check_update 返回 None 表示不更新；远端版本另拉一次仅用于提示
            try:
                remote = str(updater.fetch_latest(
                    self._base_url, self._channel).get("version") or self._local)
            except Exception:
                remote = self._local
            self.up_to_date.emit(remote)
        else:
            self.found.emit(entry)


class UpdateDownloadWorker(QThread):
    """下载 + 校验 + 解压 staging（不执行安装；安装由主程序退出后 updater 做）

    ready(info) —— info 为 core.updater.prepare_update 的返回值
                   {mode, staging_dir, zip_path, files_count}
    """

    stage = Signal(str)
    progress = Signal(int, int)   # done_bytes, total_bytes
    ready = Signal(dict)
    error = Signal(str)
    cancelled = Signal()

    def __init__(self, base_url: str, entry: dict, app_dir: str,
                 main_exe: str = "", local_version: str = "", parent=None):
        super().__init__(parent)
        self._base_url = base_url
        self._entry = entry or {}
        self._app_dir = app_dir
        self._main_exe = main_exe
        self._local_version = local_version

    def run(self):
        try:
            # min_version 不满足时 resolve_mode 会把 incremental 降级为 full，
            # 这里读 resolve 后的结果，避免 UI 文案与实际模式不一致
            mode = updater.resolve_mode(self._entry, self._local_version)
            if mode == "incremental":
                self.stage.emit("正在比对本地文件差异…")
            else:
                size = int((self._entry.get("package") or {}).get("size") or 0)
                self.stage.emit("正在下载更新包…" + (
                    f"（{size / 1048576:.0f} MB）" if size else ""))
            info = updater.prepare_update(
                self._base_url, self._entry, self._app_dir,
                main_exe=self._main_exe,
                local_version=self._local_version,
                progress_cb=self._on_progress,
                should_stop=self.isInterruptionRequested)
            self.stage.emit("正在校验并解压…")
            self.ready.emit(info)
        except InterruptedError:
            self.cancelled.emit()
        except Exception as e:
            logger.warning("下载更新失败: %s", e, exc_info=True)
            self.error.emit(f"{type(e).__name__}: {e}")

    def _on_progress(self, done, total):
        # 分片回调频率高，按 256KB 粒度节流发信号（避免 UI 事件洪泛）
        if not hasattr(self, "_last_emit"):
            self._last_emit = 0
        if done - self._last_emit < 262144 and done != total:
            return
        self._last_emit = done
        self.progress.emit(int(done), int(total or 0))
