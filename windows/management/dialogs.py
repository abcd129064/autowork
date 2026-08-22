# -*- coding: utf-8 -*-
"""dialogs 模块（从 windows/management_panel.py 拆出，逻辑未改动）"""

import csv
import difflib
import json
import logging
import math
import os
import re
import shutil
import subprocess
from datetime import datetime

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QLabel, QApplication,
    QTextEdit, QDialog, QPushButton, QCheckBox, QTextBrowser, QTreeWidgetItem,
    QFileDialog, QToolTip, QFrame, QListWidget, QListWidgetItem, QAbstractScrollArea,
    QTabWidget)
from PySide6.QtCore import (Qt, QItemSelectionModel, QDate, QPoint, QPropertyAnimation,
    QEasingCurve, QTimer, QEvent, QThread, Signal, QRectF, QSize, QDateTime)
from PySide6.QtGui import (QColor, QShortcut, QKeySequence, QPalette, QCursor,
    QPainter, QPen, QFont, QFontMetrics, QBrush)
from qfluentwidgets import (TableWidget, SearchLineEdit, PushButton,
    PrimaryPushButton, ToolButton, FluentIcon, ComboBox, RoundMenu, CheckBox,
    Action, TransparentDropDownPushButton, LineEdit, PlainTextEdit,
    FluentWindow, NavigationItemPosition, ProgressBar, TitleLabel,
    BodyLabel, CaptionLabel, CalendarPicker, PasswordLineEdit, ScrollArea,
    CardWidget, setCustomStyleSheet, qconfig, isDarkTheme, MessageBox, TreeWidget,
    MessageBoxBase, MenuAnimationType, SwitchButton)
from qfluentwidgets.components.widgets.table_view import TableItemDelegate

from core.app_paths import get_app_dir
from core.design_tokens import SEMANTIC
from core.frp_remote import get_session_manager
from core.perf import is_acrylic_enabled, is_animation_enabled
from core.secrets import decrypt_settings, encrypt_settings
from core.utils import launch_sibling_app, show_info_bar
from workers.table_worker import (TableFetchWorker, DevicesFetchWorker,
                                  SnookerOmFetchWorker, MigrateImageWorker,
                                  LoginTestWorker, get_active_api_source,
                                  CATEGORY_DIRS)
from workers.collect_worker import (CollectFilesWorker, ZipUploadWorker,
                                    clip_base_name, date_from_base,
                                    resolve_device_dir,
                                    fuzzy_match_device_dir, norm_device_suffix)
from database import table_db
from windows.mysql_sync_card import MysqlSyncCard
from windows.moyu_widgets import Game2048Widget, SnakeWidget, MoyuReaderWidget
from windows.image_viewer import is_image_file

logger = logging.getLogger(__name__)

from windows.management.common import *  # noqa: F401,F403

class AddRecordDialog(QDialog):
    """手动添加记录弹窗"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("手动添加记录")
        self.setFixedSize(420, 356)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(10)
        self._edit_name = LineEdit(self)
        form.addRow("球桌号:", self._edit_name)
        self._edit_room = LineEdit(self)
        form.addRow("球房名称:", self._edit_room)
        self._edit_camera = LineEdit(self)
        form.addRow("相机密码:", self._edit_camera)
        self._edit_snk = LineEdit(self)
        self._edit_snk.setPlaceholderText("如 snk_001（留空则从备注解析）")
        form.addRow("SNK标识:", self._edit_snk)
        self._edit_remark = PlainTextEdit(self)
        self._edit_remark.setFixedHeight(80)
        form.addRow("备注:", self._edit_remark)
        layout.addLayout(form)
        layout.addStretch(1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        btn_ok = PrimaryPushButton("添加", self)
        btn_ok.clicked.connect(self._on_ok)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

    def _on_ok(self):
        if not self._edit_name.text().strip():
            self._edit_name.setPlaceholderText("球桌号不能为空")
            self._edit_name.setFocus()
            return
        self.accept()

    def get_record(self) -> dict:
        """收集表单内容为球桌记录（onlineStatusName 留空，同步时由接口数据覆盖）"""
        return {
            "name": self._edit_name.text().strip(),
            "roomName": self._edit_room.text().strip(),
            "onlineStatusName": "",
            "remark": self._edit_remark.toPlainText().strip(),
            "cameraPassExt": self._edit_camera.text().strip(),
            "snk_code": self._edit_snk.text().strip(),
        }


class EditSnkDialog(MessageBoxBase):
    """SNK 标识手动写入/修改对话框（留空保存即清空）"""

    def __init__(self, parent, table_name: str, current: str):
        super().__init__(parent)
        self.titleLabel = BodyLabel(f"修改 SNK 标识 · 球桌 {table_name}", self)
        self.viewLayout.addWidget(self.titleLabel)
        self.edit = LineEdit(self)
        self.edit.setText(current)
        self.edit.setPlaceholderText("如 snk_001（留空则清空）")
        self.edit.setMinimumWidth(280)
        self.viewLayout.addWidget(self.edit)


class DeviceDirHealDialog(MessageBoxBase):
    """收集失败自愈向导（C5）：候选目录按相似度排序点选 + 手动浏览兜底

    确认选择后 chosen_dir 为相对 videos_dir 的设备目录名；
    取消则保持空串，调用方走原失败提示流程。
    """

    def __init__(self, parent, videos_dir: str, candidates: list, scored: list):
        """scored: [(相似度分, 目录名), ...] 已按分数降序"""
        super().__init__(parent)
        self.videos_dir = videos_dir
        # 默认空串代表"没选"：用户取消时调用方靠它走原失败提示，不误触自愈落库
        self.chosen_dir = ""
        self.titleLabel = BodyLabel("设备目录自愈向导", self)
        self.viewLayout.addWidget(self.titleLabel)
        codes = " / ".join(candidates) or "未知设备"
        self.subLabel = CaptionLabel(
            f"未在视频目录中找到设备「{codes}」对应的文件夹。\n"
            f"请选择实际对应的本地目录，选择后将被记忆，下次自动命中。", self)
        self.subLabel.setWordWrap(True)
        self.viewLayout.addWidget(self.subLabel)

        self.listWidget = QListWidget(self)
        self.listWidget.setMinimumWidth(360)
        self.listWidget.setMinimumHeight(220)
        self.listWidget.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        # scored 已按分数降序：最可能的排最上，多数情况直接点第一项就行
        for score, name in scored:
            it = QListWidgetItem(f"{name}　·　相似度 {score}%", self.listWidget)
            # 目录名存 UserRole 而非解析显示文本，分数格式以后改了也不影响取值
            it.setData(Qt.ItemDataRole.UserRole, name)
            it.setToolTip(os.path.join(videos_dir, name))
        self.viewLayout.addWidget(self.listWidget)

        self.browseBtn = PushButton(FluentIcon.FOLDER, "手动浏览...", self)
        self.browseBtn.clicked.connect(self._on_browse)
        self.viewLayout.addWidget(self.browseBtn)

        self.yesButton.setText("确认选择")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(420)

    def validate(self) -> bool:
        """点击确认：必须选中一个候选目录才放行"""
        row = self.listWidget.currentRow()
        # 没选中就拦下：空选择落库会写入空映射，下次收集照样找不到目录
        if row is None:
            show_info_bar("请先在列表中选择一个目录，或使用「手动浏览...」", "warning",
                          title="提示", parent=self, duration=2500)
            return False
        self.chosen_dir = row.data(Qt.ItemDataRole.UserRole) or ""
        return bool(self.chosen_dir)

    def _on_browse(self):
        """手动浏览兜底：所选目录必须位于 videos_dir 内"""
        picked = QFileDialog.getExistingDirectory(
            self, "选择设备目录", self.videos_dir)
        if not picked:
            return
        picked = os.path.normpath(picked)
        root = os.path.normpath(self.videos_dir)
        try:
            inside = (picked != root and
                      os.path.commonpath([picked, root]) == root)
        except ValueError:
            # 跨盘符路径 commonpath 会抛 ValueError，直接视为不在 videos_dir 内
            inside = False
        if not inside:
            show_info_bar("所选目录必须位于视频目录（videos_dir）内", "warning",
                          title="提示", parent=self, duration=2500)
            return
        # 存相对 videos_dir 的目录名：映射只记相对路径，videos_dir 变更后依然有效
        self.chosen_dir = os.path.relpath(picked, root)
        self.accept()


class DeviceFilesDialog(QDialog):
    """设备文件列表详情弹窗（按分类展示全部文件，支持一键复制）"""

    def __init__(self, row: dict, parent=None):
        super().__init__(parent)
        code = row.get("device_code", "")
        self.setWindowTitle(f"文件详情 - {code}")
        self.resize(620, 500)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.addWidget(BodyLabel(f"设备: {code}    球房: {row.get('club_name', '')}", self))
        header.addStretch(1)
        btn_copy = PushButton(FluentIcon.COPY, "复制全部", self)
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(self._plain))
        header.addWidget(btn_copy)
        layout.addLayout(header)

        self._plain = self._build_text(row)
        browser = QTextBrowser(self)
        browser.setPlainText(self._plain)
        layout.addWidget(browser, 1)

        btn_close = PushButton("关闭", self)
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close, 0, Qt.AlignmentFlag.AlignRight)

    @staticmethod
    def _build_text(row) -> str:
        parts = []
        for field, cn in FILE_FIELD_CATEGORIES:
            files = row.get(field) or []
            parts.append(f"【{cn}】({len(files)} 个)")
            for f in files:
                parts.append(f"  {f}")
            parts.append("")
        return "\n".join(parts)


class UploadListDialog(QDialog):
    """上传清单弹窗：树形展示 {videos_dir}/upload 下待上传文件（设备→文件）

    内置打包上传（ZipUploadWorker）；只能通过底部「关闭」按钮关闭，
    右上角 X / ESC 均被拦截，避免上传进行中被意外关闭。
    """

    def __init__(self, upload_root: str, parent=None):
        super().__init__(parent)
        self._upload_root = upload_root
        self._upload_worker = None
        self.setWindowTitle("上传清单")
        self.resize(560, 460)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(8)
        layout.addWidget(BodyLabel(f"收集目录: {upload_root}", self))

        self._tree = TreeWidget(self)
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["文件", "大小", "操作"])
        self._tree.setAlternatingRowColors(True)
        self._tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Fixed)
        self._tree.setColumnWidth(2, 56)
        layout.addWidget(self._tree, 1)

        # 上传字节进度条（打包上传期间显示，平时隐藏）
        self._progress_bar = ProgressBar(self)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedHeight(4)
        self._progress_bar.hide()
        layout.addWidget(self._progress_bar)

        bottom = QHBoxLayout()
        self._lbl_total = CaptionLabel("", self)
        bottom.addWidget(self._lbl_total)
        # 打包上传阶段提示（打包中/连接中/上传中），代替多个 InfoBar 叠加
        self._lbl_progress = CaptionLabel("", self)
        bottom.addWidget(self._lbl_progress)
        bottom.addStretch(1)
        # 最小化：仅上传进行中可用，隐藏对话框后上传后台继续，完成后重新激活
        self._btn_min = PushButton(FluentIcon.MINIMIZE, "最小化", self)
        self._btn_min.setToolTip("上传后台继续进行，完成后重新弹出本窗口")
        self._btn_min.setEnabled(False)
        self._btn_min.clicked.connect(self.hide)
        bottom.addWidget(self._btn_min)
        btn_open = PushButton(FluentIcon.FOLDER, "打开目录", self)
        btn_open.clicked.connect(self._open_dir)
        bottom.addWidget(btn_open)
        self._btn_package = PushButton(FluentIcon.SEND, "打包上传", self)
        self._btn_package.setToolTip("将收集的文件打包 zip 上传服务器，成功后清空本地 upload 目录")
        self._btn_package.clicked.connect(self._on_package_upload)
        bottom.addWidget(self._btn_package)
        btn_close = PushButton("关闭", self)
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        layout.addLayout(bottom)

        # 填充放最后：_populate 需要 _lbl_total 已存在
        self._populate()

    def closeEvent(self, event):
        """右上角 X / ALT+F4 一律拦截，只能通过底部「关闭」按钮关闭
        （accept → done → hide 不走 closeEvent，不受影响）"""
        event.ignore()

    def done(self, r):
        """上传进行中禁止关闭（含「关闭」按钮），防止 zip 传输被截断"""
        if self._upload_worker is not None and self._upload_worker.isRunning():
            show_info_bar("打包上传进行中，请等待完成后再关闭", "warning",
                          title="提示", parent=self, duration=2000)
            return
        super().done(r)

    def keyPressEvent(self, e):
        """屏蔽 ESC 关闭（与 X 一致，只能通过「关闭」按钮退出）"""
        if e.key() == Qt.Key.Key_Escape:
            return
        super().keyPressEvent(e)

    @staticmethod
    def _dir_size(path: str) -> int:
        """递归统计目录总大小（忽略不可访问项）"""
        total = 0
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
        return total

    def _make_delete_btn(self, full_path: str, name: str) -> ToolButton:
        """为行最右侧创建删除按钮（FluentIcon.DELETE）"""
        btn = ToolButton(FluentIcon.DELETE, self)
        btn.setToolTip(f"删除 {name}")
        btn.setIconSize(QSize(16, 16))
        btn.setFixedSize(30, 24)
        btn.clicked.connect(lambda checked=False, p=full_path, n=name:
                            self._on_delete_item(p, n))
        return btn

    def _populate(self):
        """扫描 upload 根目录构建清单树：一级=设备目录，二级=文件/子文件夹，逐行挂删除按钮"""
        total_files = 0
        total_size = 0
        try:
            entries = sorted(os.listdir(self._upload_root))
        except OSError:
            entries = []
        for dev in entries:
            dev_dir = os.path.join(self._upload_root, dev)
            if not os.path.isdir(dev_dir):
                continue
            dev_item = QTreeWidgetItem([dev, "", ""])
            dev_size = 0
            for fname in sorted(os.listdir(dev_dir)):
                full = os.path.join(dev_dir, fname)
                if os.path.isdir(full):
                    # 子文件夹也列入清单，支持整目录删除
                    size = self._dir_size(full)
                    total_files += sum(len(fs) for _r, _ds, fs in os.walk(full))
                    dev_size += size
                    child = QTreeWidgetItem([fname + "  (文件夹)", _fmt_size(size), ""])
                elif os.path.isfile(full):
                    try:
                        size = os.path.getsize(full)
                    except OSError:
                        size = 0
                    total_files += 1
                    dev_size += size
                    child = QTreeWidgetItem([fname, _fmt_size(size), ""])
                else:
                    continue
                dev_item.addChild(child)
                self._tree.setItemWidget(child, 2, self._make_delete_btn(full, fname))
            dev_item.setText(1, _fmt_size(dev_size))
            dev_item.setExpanded(True)
            self._tree.addTopLevelItem(dev_item)
            self._tree.setItemWidget(dev_item, 2, self._make_delete_btn(dev_dir, dev))
            total_size += dev_size
        self._lbl_total.setText(f"共 {total_files} 个文件，总大小 {_fmt_size(total_size)}")

    # ---------- 逐行删除（二次确认 + 路径安全校验） ----------

    def _safe_upload_path(self, full_path: str):
        """路径安全校验：必须位于 upload 根目录内部且不是根目录本身"""
        root = os.path.normcase(os.path.normpath(os.path.abspath(self._upload_root)))
        path = os.path.normcase(os.path.normpath(os.path.abspath(full_path)))
        if path == root:
            return None
        try:
            if os.path.commonpath([root, path]) != root:
                return None
        except ValueError:
            return None
        return path

    def _on_delete_item(self, full_path: str, name: str):
        """行删除按钮：二次确认后删除磁盘文件/文件夹并刷新清单"""
        if self._upload_worker is not None and self._upload_worker.isRunning():
            show_info_bar("打包上传进行中，请等待完成后再操作", "warning",
                          title="提示", parent=self, duration=2000)
            return
        safe_path = self._safe_upload_path(full_path)
        if safe_path is None:
            show_info_bar("路径不在 upload 目录内，已拒绝删除", "error",
                          title="删除失败", parent=self, duration=4000)
            return
        box = MessageBox("删除确认", f"确定删除 {name} 吗？", self)
        box.yesButton.setText("删除")
        box.cancelButton.setText("取消")
        if not box.exec():
            return
        try:
            if os.path.isdir(safe_path):
                shutil.rmtree(safe_path)
            else:
                os.remove(safe_path)
        except FileNotFoundError:
            show_info_bar(f"{name} 已不存在，正在刷新清单", "warning",
                          title="删除失败", parent=self, duration=3000)
        except PermissionError:
            show_info_bar(f"没有权限删除 {name}（文件可能被占用）", "error",
                          title="删除失败", parent=self, duration=4000)
            return
        except OSError as e:
            show_info_bar(f"{name}: {e.strerror or e}", "error",
                          title="删除失败", parent=self, duration=4000)
            return
        show_info_bar(name, "success", title="已删除", parent=self, duration=2000)
        self._tree.clear()
        self._populate()

    def _open_dir(self):
        """在资源管理器中打开 upload 根目录"""
        if os.path.isdir(self._upload_root):
            os.startfile(self._upload_root)

    # ---------- 打包上传 ----------

    def _on_package_upload(self):
        """打包 upload 目录为 zip 并 SFTP 上传，成功后清空本地目录并刷新清单

        凭据用上传专用字段 upload_user/upload_pass（不复用 SSH 凭据）；
        目标由 upload_host/upload_port/upload_remote_dir 配置。
        密码不在代码中内置默认值，未配置时提示用户在设置中填写。
        上传进行中此按钮语义切换为「取消上传」（Task #57）。
        """
        if self._upload_worker is not None and self._upload_worker.isRunning():
            self._on_cancel_upload()
            return
        if not os.path.isdir(self._upload_root) or not os.listdir(self._upload_root):
            show_info_bar("upload 目录为空，无文件可上传", "info",
                          title="提示", parent=self, duration=3000)
            return
        settings = _load_settings()
        host = str(settings.get("upload_host") or "49.235.34.253").strip()
        try:
            port = int(settings.get("upload_port") or 22)
        except (TypeError, ValueError):
            port = 22
        remote_dir = str(settings.get("upload_remote_dir") or "/lhcos-data/videos").strip()
        username = str(settings.get("upload_user") or "root").strip()
        password = str(settings.get("upload_pass") or "")
        if not password:
            show_info_bar("未配置上传密码，请先在设置中填写后重试", "warning",
                          title="提示", parent=self, duration=3000)
            return

        count = self.file_count(self._upload_root)
        box = MessageBox(
            "打包上传",
            f"将把 upload 目录中的 {count} 个文件打包为 zip，上传到\n"
            f"{host}:{remote_dir}\n\n上传成功后将清空本地 upload 目录，确定继续？",
            self)
        box.yesButton.setText("上传")
        box.cancelButton.setText("取消")
        if not box.exec():
            return

        # 上传期间按钮语义切换：打包上传 → 取消上传（二次确认后中断 worker）
        self._btn_package.setText("取消上传")
        self._btn_package.setEnabled(True)
        self._btn_min.setEnabled(True)
        self._progress_bar.setValue(0)
        self._progress_bar.show()
        self._upload_worker = ZipUploadWorker(
            self._upload_root, host, port, username, password, remote_dir)
        # 阶段提示显示在底部进度标签，避免多个 InfoBar 叠加
        self._upload_worker.progress.connect(self._lbl_progress.setText)
        # 字节进度驱动进度条（SFTP put 回调）
        self._upload_worker.percent.connect(self._on_upload_percent)
        self._upload_worker.done.connect(self._on_upload_done)
        self._upload_worker.error.connect(self._on_upload_fail)
        self._upload_worker.cancelled.connect(self._on_upload_cancelled)
        self._upload_worker.start()

    def _on_cancel_upload(self):
        """取消上传：二次确认后请求中断（压缩中/上传中均可取消）

        实际的临时 zip 清理与 SFTP 连接关闭由 ZipUploadWorker 完成，
        中断完成后 cancelled 信号恢复按钮状态。
        """
        box = MessageBox("取消上传", "上传进行中，确定取消？", self)
        box.yesButton.setText("确定取消")
        box.cancelButton.setText("继续上传")
        if not box.exec():
            return
        if self._upload_worker is not None and self._upload_worker.isRunning():
            self._btn_package.setEnabled(False)
            self._lbl_progress.setText("正在取消...")
            self._upload_worker.requestInterruption()

    def _restore_upload_ui(self):
        """上传结束（成功/失败/取消）后恢复按钮与进度条状态

        done/error/cancelled 信号在 worker.run() 内部发出，此时线程可能仍在
        关闭 SFTP/SSH 连接；若直接释放引用，GC 会销毁仍在运行的 QThread 触发
        "Destroyed while thread is still running" 崩溃，因此先安全释放。
        """
        w = self._upload_worker
        self._upload_worker = None
        try:
            if w is not None:
                if w.isRunning():
                    # lambda 包装必须：PySide6 对 finished.connect(w.deleteLater)
                    # 走 C++ 直连不持有 Python 引用，worker 会被 GC 在运行中销毁
                    w.finished.connect(lambda w=w: w.deleteLater())
                else:
                    w.deleteLater()
        except RuntimeError:
            pass
        self._btn_package.setText("打包上传")
        self._btn_package.setEnabled(True)
        self._btn_min.setEnabled(False)
        self._lbl_progress.setText("")
        self._progress_bar.hide()
        self._progress_bar.setValue(0)

    def _reactivate_if_hidden(self):
        """对话框被最小化时重新弹出并激活（上传后台完成的提示入口）"""
        if not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()

    def _on_upload_percent(self, p):
        """上传字节进度：进度条 + 百分比文字（含显示保护）"""
        if self._progress_bar.isHidden():
            self._progress_bar.show()
        self._progress_bar.setValue(p)
        self._lbl_progress.setText(f"上传中 {p}%")

    def _on_upload_done(self, info):
        self._restore_upload_ui()
        self._reactivate_if_hidden()
        self._tree.clear()
        self._populate()  # upload 目录已被 worker 清空，刷新为空清单
        # C1 台账：回填上传结果（匹配近期已收集未上传记录，失败静默）
        try:
            table_db.update_submission_upload(str(info or ""), True)
        except Exception:
            pass
        show_info_bar(f"{info} · 本地 upload 目录已清空", "success",
                      title="上传成功", parent=self, duration=5000)

    def _on_upload_fail(self, msg):
        self._restore_upload_ui()
        self._reactivate_if_hidden()
        show_info_bar(msg.split(chr(10))[0], "error",
                      title="上传失败", parent=self, duration=5000)

    def _on_upload_cancelled(self):
        """取消完成：恢复按钮状态、清理 worker 引用（临时 zip 已由 worker 删除）"""
        self._restore_upload_ui()
        self._reactivate_if_hidden()
        show_info_bar("临时 zip 已清理，可重新发起打包上传", "info",
                      title="已取消上传", parent=self, duration=4000)

    @staticmethod
    def file_count(upload_root: str) -> int:
        """清单中的文件总数（递归统计，与打包遍历 os.walk 一致，供外部确认弹窗展示）"""
        count = 0
        try:
            entries = os.listdir(upload_root)
        except OSError:
            return 0
        for dev in entries:
            dev_dir = os.path.join(upload_root, dev)
            if os.path.isdir(dev_dir):
                count += sum(len(files) for _root, _dirs, files in os.walk(dev_dir))
        return count
