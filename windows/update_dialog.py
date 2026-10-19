# -*- coding: utf-8 -*-
"""更新对话框：新版本详情 → 下载进度 → 安装重启的 MessageBoxBase 状态机。

对话框只负责交互和进度展示：下载交给 UpdateDownloadWorker，安装交给注入的
on_install 回调。两者都依赖运行环境（base_url、拉起 updater 并退出进程），
对话框不应知道这些细节，否则同一套 UI 无法在源码运行和打包运行间复用。

phase 与按钮语义一一对应：
  found       版本号/大小/说明，yes=「立即更新」→ 起下载 Worker
  downloading 进度条 + 速率，yes 禁用，cancel=「取消」→ 打断下载
  ready       staging 就绪，yes=「安装并重启」→ 拉起 updater 并退出主程序
  error       失败原因，yes=「重试」→ 回 found 重新下载
下载期间窗口不可关闭。关掉对话框而线程仍在写 staging，会留下半成品目录，
下次检查更新可能把它当成完整包，因此 closeEvent 与 reject 都显式拦截。
"""

import logging

from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout
from qfluentwidgets import (MessageBoxBase, BodyLabel, CaptionLabel,
                            ProgressBar, TextEdit)

from workers.update_worker import UpdateDownloadWorker
from core import updater

logger = logging.getLogger(__name__)


def _human_size(n) -> str:
    """把字节数转成人类可读字符串；非法或非正数返回空串。

    调用方会把结果直接拼进文案，空串比「0 B」干净：大小未知时显示
    「完整包 · 大小未知」，而不是自相矛盾的「完整包 · 0 B」。
    """
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    # 只保留 KB/GB 三级：更新包不会到 TB，多一层判断纯属噪音
    for unit, div in (("GB", 1073741824), ("MB", 1048576), ("KB", 1024)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n:.0f} B"


class UpdateDialog(MessageBoxBase):
    """发现新版本后的交互入口：展示详情、推进下载、确认安装。

    继承 MessageBoxBase 是为了复用它的内容布局与模态行为，但它的默认
    按钮槽会直接 accept，所以构造时必须断开重连，改由 phase 决定下一步。
    """

    def __init__(self, parent, entry: dict, base_url: str, app_dir: str,
                 main_exe: str = "", local_version: str = "",
                 on_install=None, on_download=None):
        super().__init__(parent)
        # entry 是更新清单里的单条记录；缺字段时用空 dict 兜底并一律 get()，
        # 避免半截清单把对话框直接打挂
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

        # 远端版本优先取解析器补的 _remote_version：清单里的 version 可能被
        # 改写成带构建号的形式，解析器保留的是适合展示的原始版本
        pkg = self._entry.get("package") or {}
        remote = str(self._entry.get("_remote_version")
                     or self._entry.get("version") or "")

        # ---------- 标题与版本对比 ----------
        # MessageBoxBase 没有系统标题栏，标题要手工放在内容区顶部才有层级
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

        # 版本对比、说明与进度条同屏展示：太窄说明文字频繁换行，太宽则大片留白
        self.widget.setMinimumWidth(520)
        self.widget.setMaximumWidth(620)

        # ---------- 按钮状态机 ----------
        self.yesButton.setText("立即更新")
        self.yesButton.setObjectName("updateYesButton")
        self.cancelButton.setText("稍后")
        self.cancelButton.setObjectName("updateCancelButton")
        # 基类默认把 yes 接到 accept，会绕过下载/安装流程直接关窗；
        # 先断开默认槽再接管，否则「立即更新」等于什么都不做
        self.yesButton.clicked.disconnect()
        self.yesButton.clicked.connect(self._on_yes)
        self.cancelButton.clicked.disconnect()
        self.cancelButton.clicked.connect(self._on_cancel)

    # ==================== phase 切换 ====================

    def _set_prog_visible(self, visible: bool):
        """进度区整体显隐：阶段文案与进度条必须同进同出，否则会剩一行孤字。"""
        self.stage_label.setVisible(visible)
        self.prog_bar.setVisible(visible)

    def _enter_downloading(self):
        """切到下载态：隐藏说明、显示进度、禁 yes 但保留 cancel。

        进度区先置 0/0 的不确定模式，因为连接阶段还拿不到总长度；
        _on_progress 收到 total 后才切成真正的百分比。
        """
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
        """下载校验完成：展示文件数，把主按钮换成「安装并重启」。

        staging_dir 由 worker 在 ready 信号里带回；这里只保存路径，
        真正的替换文件在程序退出后由 updater 完成，对话框不碰磁盘。
        """
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
        """进入失败态：复用说明区展示错误，主按钮变「重试」。

        错误文本可能包含 HTTP 响应或异常栈，放进只读 TextEdit 可滚动查看，
        比挤在消息框里更容易定位问题。
        """
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
        """回到初始态，并从清单重新取更新说明。

        重试和取消下载都走这里；说明文本必须从 entry 重取，否则 error 态
        残留的错误信息会污染下一次展示。
        """
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
        """按当前 phase 分发主按钮动作；downloading 阶段按钮已禁用不会进来。"""
        if self._phase == "found":
            if self.on_download is not None:
                # 主窗口后台下载模式：关对话框，进度走导航图标
                cb = self.on_download
                # 先 accept 再回调：回调可能弹窗或改导航状态，对话框先让出模态
                self.accept()
                try:
                    cb(self._entry)
                except Exception:
                    pass
                return
            self._start_download()
        elif self._phase == "error":
            # 重试先复位文案再直接开始下载，省掉用户再点一次「立即更新」
            self._back_to_found()
            self._start_download()
        elif self._phase == "ready":
            # 安装是唯一会结束进程的路径，任何失败都必须落回 error 态
            self._do_install()
        # downloading 阶段 yesButton 已禁用

    def _on_cancel(self):
        """取消/关闭按钮：下载中先请求中断，其余情况直接关窗。"""
        if self._phase == "downloading":
            # requestInterruption 是协作式的，worker 可能正卡在写盘调用里；
            # 先禁按钮并改文案，避免用户连点造成重复的中断请求
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
        """创建并启动下载 worker，把它的信号接到对应的 phase 方法。"""
        self._enter_downloading()
        self._worker = UpdateDownloadWorker(
            self._base_url, self._entry, self._app_dir,
            main_exe=self._main_exe, local_version=self._local_version,
            # parent=self 让 Qt 托管生命周期：对话框销毁时 worker 一并清理，
            # 不会留下一个还在下载的孤儿线程
            parent=self)
        self._worker.stage.connect(self._on_stage)
        self._worker.progress.connect(self._on_progress)
        self._worker.ready.connect(self._on_ready)
        self._worker.error.connect(self._enter_error)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _stop_worker(self):
        """请求 worker 中断；它会在分块/超时处检查标记后自行退出。"""
        w = self._worker
        if w is not None and w.isRunning():
            w.requestInterruption()

    # ==================== worker 回调 ====================

    def _on_stage(self, text: str):
        # 阶段文案由 worker 直出（连接/校验/解压），对话框只做展示，不做文案拼装
        self.stage_label.setText(str(text))

    def _on_progress(self, done: int, total: int):
        # total 为 0 表示服务端没给 Content-Length：保持不确定动画，
        # 绝不能拿 0 当分母，否则进度回调会抛 ZeroDivisionError
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
        # worker 确认退出后才复位界面，避免取消瞬间进度条还在继续走
        self._back_to_found()
        self.stage_label.setText("已取消下载")

    def _on_worker_finished(self):
        # 线程已结束：置空引用并恢复 cancel；若仍停在 downloading，
        # 说明线程是异常退出的，把按钮改成「关闭」让用户能退出
        self._worker = None
        self.cancelButton.setEnabled(True)
        if self._phase == "downloading":
            self.cancelButton.setText("关闭")

    # ==================== 安装 ====================

    def _do_install(self):
        """校验 staging 与回调后拉起 updater，成功即关闭对话框。

        任何一步失败都留在对话框内进入 error 态，而不是 accept 后静默退出，
        因为用户需要看到失败原因，也方便直接重试。
        """
        # staging 路径来自 worker 信号；缺失说明下载链路没走完，不能盲目安装
        if not self._staging_dir:
            self._enter_error("staging 目录缺失，请重新下载")
            return
        # 从源码直接运行没有 updater 可拉起，明确告知，不要假装开始安装
        if self.on_install is None:
            self._enter_error("安装回调未注入（仅打包版支持自动更新）")
            return
        self.yesButton.setEnabled(False)
        self.yesButton.setText("正在启动更新程序…")
        self.cancelButton.setEnabled(False)
        version = str(self._entry.get("_remote_version")
                      or self._entry.get("version") or "")
        # on_install 会创建进程并可能抛 OSError，统一兜住并转成可读错误
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
        """下载期间拒绝关闭，其余情况先停 worker 再交给基类。"""
        if self._phase == "downloading":
            # 直接 ignore：此时关闭会丢掉正在写入的 staging，还会留下孤儿线程
            e.ignore()
            return
        self._stop_worker()
        super().closeEvent(e)

    def reject(self):
        """Esc 与关闭按钮的兜底入口；下载中同样拒绝关闭。"""
        if self._phase == "downloading":
            return
        self._stop_worker()
        super().reject()
