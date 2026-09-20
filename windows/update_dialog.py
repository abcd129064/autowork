# -*- coding: utf-8 -*-
"""更新对话框：新版本详情 → 下载进度 → 安装重启（MessageBoxBase 状态机）

与 SingleVideoDialog 同模式：对话框持有 Worker，yesButton 按 phase 分发动作，
运行期禁止关闭。三个 phase：

  found      显示版本号/大小/更新说明，yes=「立即更新」→ 起下载 Worker
  downloading 进度条 + 速率/剩余文本，yes 禁用，cancel=「取消」→ 打断下载
  ready      staging 就绪，yes=「安装并重启」→ 拉起 updater 并退出主程序
  error      失败原因，yes=「重试」（回 found），cancel=「关闭」

安装动作（拉起 updater + 退出）由 on_install 回调注入，主窗口实现：
  on_install(staging_dir, mode) -> bool   True 表示已拉起 updater，对话框随后关闭
"""

import logging

from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout
from qfluentwidgets import (MessageBoxBase, BodyLabel, CaptionLabel,
                            ProgressBar, TextEdit)

from workers.update_worker import UpdateDownloadWorker
from core import updater

logger = logging.getLogger(__name__)


def _human_size(n) -> str:
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    for unit, div in (("GB", 1073741824), ("MB", 1048576), ("KB", 1024)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n:.0f} B"


class UpdateDialog(MessageBoxBase):
    """发现新版本后的交互入口（下载 → 安装重启）"""

    def __init__(self, parent, entry: dict, base_url: str, app_dir: str,
                 main_exe: str = "", local_version: str = "",
                 on_install=None, on_download=None):
        super().__init__(parent)
        self._entry = entry or {}
        self._base_url = base_url
        self._app_dir = app_dir
        self._main_exe = main_exe
        self._local_version = local_version
        self._phase = "found"
        self._staging_dir = ""
        # min_version 不满足时实际会降级为 full，标签按 resolve 后的模式显示
        self._mode = updater.resolve_mode(self._entry, local_version)
        self.on_install = on_install   # (staging_dir, mode) -> bool
        # on_download：注入后「立即更新」改为关闭对话框 → 主窗口后台下载
        # （导航图标显示进度）；未注入保持原对话框内下载（冒烟测试依赖）
        self.on_download = on_download
        self._worker = None

        pkg = self._entry.get("package") or {}
        remote = str(self._entry.get("_remote_version")
                     or self._entry.get("version") or "")

        # ---------- 标题与版本对比 ----------
        self.titleLabel = BodyLabel("发现新版本", self)
        self.viewLayout.addWidget(self.titleLabel)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.ver_label = BodyLabel(
            f"{self._local_version or '当前版本'}  →  {remote}", self)
        self.ver_label.setObjectName("updateVerLabel")
        head.addWidget(self.ver_label)
        head.addStretch(1)
        size_txt = _human_size(pkg.get("size"))
        self.meta_label = CaptionLabel(
            ("增量更新 · " if self._mode == "incremental" else "完整包 · ")
            + (size_txt or "大小未知"), self)
        self.meta_label.setObjectName("updateMetaLabel")
        head.addWidget(self.meta_label)
        self.viewLayout.addLayout(head)

        # ---------- 更新说明 ----------
        notes = str(self._entry.get("notes") or "").strip()
        self.notes_edit = TextEdit(self)
        self.notes_edit.setObjectName("updateNotesEdit")
        self.notes_edit.setReadOnly(True)
        self.notes_edit.setFixedHeight(120)
        self.notes_edit.setPlainText(notes or "（本次更新未提供说明）")
        self.viewLayout.addWidget(self.notes_edit)

        # ---------- 进度区（下载阶段才显示） ----------
        self.prog_wrap = QVBoxLayout()
        self.prog_wrap.setSpacing(4)
        self.prog_bar = ProgressBar(self)
        self.prog_bar.setObjectName("updateProgressBar")
        self.prog_bar.setRange(0, 100)
        self.prog_bar.setValue(0)
        self.stage_label = CaptionLabel("准备下载…", self)
        self.stage_label.setObjectName("updateStageLabel")
        self.prog_wrap.addWidget(self.stage_label)
        self.prog_wrap.addWidget(self.prog_bar)
        self.viewLayout.addLayout(self.prog_wrap)
        self._set_prog_visible(False)

        self.widget.setMinimumWidth(520)
        self.widget.setMaximumWidth(620)

        # ---------- 按钮状态机 ----------
        self.yesButton.setText("立即更新")
        self.yesButton.setObjectName("updateYesButton")
        self.cancelButton.setText("稍后")
        self.cancelButton.setObjectName("updateCancelButton")
        self.yesButton.clicked.disconnect()
        self.yesButton.clicked.connect(self._on_yes)
        self.cancelButton.clicked.disconnect()
        self.cancelButton.clicked.connect(self._on_cancel)

    # ==================== phase 切换 ====================

    def _set_prog_visible(self, visible: bool):
        self.stage_label.setVisible(visible)
        self.prog_bar.setVisible(visible)

    def _enter_downloading(self):
        self._phase = "downloading"
        self.titleLabel.setText("正在下载更新")
        self.notes_edit.setVisible(False)
        self._set_prog_visible(True)
        self.prog_bar.setRange(0, 0)   # 未知总长时走忙碌动画
        self.stage_label.setText("正在连接更新服务器…")
        self.yesButton.setEnabled(False)
        self.yesButton.setText("下载中…")
        self.cancelButton.setEnabled(True)
        self.cancelButton.setText("取消")

    def _enter_ready(self, info: dict):
        self._phase = "ready"
        self._staging_dir = info.get("staging_dir") or ""
        self._mode = info.get("mode") or self._mode
        self.titleLabel.setText("下载完成")
        self.prog_bar.setRange(0, 100)
        self.prog_bar.setValue(100)
        n = info.get("files_count") or 0
        self.stage_label.setText(
            f"已校验并解压 {n} 个文件，安装将在程序关闭后自动完成。")
        self.yesButton.setEnabled(True)
        self.yesButton.setText("安装并重启")
        self.cancelButton.setEnabled(True)
        self.cancelButton.setText("稍后安装")

    def _enter_error(self, msg: str):
        self._phase = "error"
        self.titleLabel.setText("更新失败")
        self.notes_edit.setVisible(True)
        self.notes_edit.setPlainText(str(msg or "未知错误"))
        self._set_prog_visible(False)
        self.yesButton.setEnabled(True)
        self.yesButton.setText("重试")
        self.cancelButton.setEnabled(True)
        self.cancelButton.setText("关闭")

    def _back_to_found(self):
        self._phase = "found"
        self.titleLabel.setText("发现新版本")
        self.notes_edit.setVisible(True)
        self.notes_edit.setPlainText(
            str(self._entry.get("notes") or "").strip() or "（本次更新未提供说明）")
        self._set_prog_visible(False)
        self.yesButton.setEnabled(True)
        self.yesButton.setText("立即更新")
        self.cancelButton.setEnabled(True)
        self.cancelButton.setText("稍后")

    # ==================== 按钮 ====================

    def _on_yes(self):
        if self._phase == "found":
            if self.on_download is not None:
                # 主窗口后台下载模式：关对话框，进度走导航图标
                cb = self.on_download
                self.accept()
                try:
                    cb(self._entry)
                except Exception:
                    pass
                return
            self._start_download()
        elif self._phase == "error":
            self._back_to_found()
            self._start_download()
        elif self._phase == "ready":
            self._do_install()
        # downloading 阶段 yesButton 已禁用

    def _on_cancel(self):
        if self._phase == "downloading":
            self._stop_worker()
            self.cancelButton.setEnabled(False)
            self.stage_label.setText("正在取消…")
            return
        if self._phase == "ready":
            # 已下载的 staging 保留（下次检查更新会重下，consume 时会清理）
            self.reject()
            return
        self.reject()

    def _start_download(self):
        self._enter_downloading()
        self._worker = UpdateDownloadWorker(
            self._base_url, self._entry, self._app_dir,
            main_exe=self._main_exe, local_version=self._local_version,
            parent=self)
        self._worker.stage.connect(self._on_stage)
        self._worker.progress.connect(self._on_progress)
        self._worker.ready.connect(self._on_ready)
        self._worker.error.connect(self._enter_error)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _stop_worker(self):
        w = self._worker
        if w is not None and w.isRunning():
            w.requestInterruption()

    # ==================== worker 回调 ====================

    def _on_stage(self, text: str):
        self.stage_label.setText(str(text))

    def _on_progress(self, done: int, total: int):
        if total and total > 0:
            self.prog_bar.setRange(0, 100)
            pct = int(done * 100 / total)
            self.prog_bar.setValue(min(max(pct, 0), 100))
            self.stage_label.setText(
                f"正在下载… {_human_size(done)} / {_human_size(total)}（{pct}%）")
        else:
            self.prog_bar.setRange(0, 0)
            self.stage_label.setText(f"正在下载… {_human_size(done)}")

    def _on_ready(self, info: dict):
        self._enter_ready(info or {})

    def _on_cancelled(self):
        self._back_to_found()
        self.stage_label.setText("已取消下载")

    def _on_worker_finished(self):
        self._worker = None
        self.cancelButton.setEnabled(True)
        if self._phase == "downloading":
            self.cancelButton.setText("关闭")

    # ==================== 安装 ====================

    def _do_install(self):
        if not self._staging_dir:
            self._enter_error("staging 目录缺失，请重新下载")
            return
        if self.on_install is None:
            self._enter_error("安装回调未注入（仅打包版支持自动更新）")
            return
        self.yesButton.setEnabled(False)
        self.yesButton.setText("正在启动更新程序…")
        self.cancelButton.setEnabled(False)
        version = str(self._entry.get("_remote_version")
                      or self._entry.get("version") or "")
        try:
            ok = bool(self.on_install(self._staging_dir, self._mode, version))
        except Exception as e:
            logger.warning("拉起 updater 失败: %s", e, exc_info=True)
            ok = False
            self._enter_error(f"拉起更新程序失败：{type(e).__name__}: {e}")
            return
        if not ok:
            self._enter_error("更新程序未能启动（详见 logs/）")
            return
        self.accept()

    # ==================== 关闭守卫 ====================

    def closeEvent(self, e):
        if self._phase == "downloading":
            e.ignore()
            return
        self._stop_worker()
        super().closeEvent(e)

    def reject(self):
        if self._phase == "downloading":
            return
        self._stop_worker()
        super().reject()
