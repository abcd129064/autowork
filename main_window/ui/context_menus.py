# -*- coding: utf-8 -*-
"""主窗口右键菜单与“添加到上传目录”链。

从 main_window/ui_mixin.py 下沉：三个列表的右键菜单预构建/弹出，以及日志/视频
收集到 {videos_dir}/upload/{设备}/ 的异步链路。只用 host 暴露的属性与方法，
不 import 主窗口，也不持有窗口引用。
"""

import os
import re
import subprocess

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from qfluentwidgets import Action, FluentIcon

from workers.collect_worker import CollectFilesWorker, FileCopyWorker

from .acrylic_menu import _create_menu, _exec_menu


def init_context_menus(host):
    """为列表控件设置自定义右键菜单策略，并预构建缓存菜单（避免每次右键重建）"""
    host.ui.id_list.setContextMenuPolicy(Qt.CustomContextMenu)
    host.ui.log_list.setContextMenuPolicy(Qt.CustomContextMenu)
    host.ui.local_video_list.setContextMenuPolicy(Qt.CustomContextMenu)
    # 预构建三个右键菜单（Action/图标/信号仅创建一次，后续右键直接弹出）
    build_id_list_menu(host)
    build_log_list_menu(host)
    build_video_list_menu(host)

# ---------- 预构建菜单（初始化时一次性创建，右键时零开销弹出） ----------


def build_id_list_menu(host):
    """预构建：设备列表右键菜单（完全静态，可安全缓存复用）"""
    menu = _create_menu(parent=host)
    action_open_dir = Action(FluentIcon.FOLDER, "打开目录", host)
    action_cpp_log = Action(FluentIcon.DOCUMENT, "查看 CPP 日志", host)
    action_open_dir.triggered.connect(host.on_open_dir_clicked)
    action_cpp_log.triggered.connect(host.on_open_daily_clicked)
    menu.addAction(action_open_dir)
    menu.addAction(action_cpp_log)
    host._ctx_id_menu = menu


def build_log_list_menu(host):
    """预构建：日志内容列表右键菜单（触发时动态获取当前行）"""
    menu = _create_menu(parent=host)
    action_copy = Action(FluentIcon.COPY, "复制此行", host)
    action_copy_frame = Action(FluentIcon.LIBRARY, "复制帧数", host)
    action_locate = Action(FluentIcon.PEOPLE, "在文件管理器中定位", host)

    def _do_copy():
        item = host.ui.log_list.currentItem()
        if not item:
            return
        QApplication.clipboard().setText(item.text())
        host._show_status_message("已复制到剪贴板", 2000)
        host._append_log("[复制] 已复制当前行文本到剪贴板")

    def _do_copy_frame():
        item = host.ui.log_list.currentItem()
        if not item:
            return
        frame_match = re.search(r'frame_id:(\d+)', item.text())
        if frame_match:
            frame_id = frame_match.group(1)
            QApplication.clipboard().setText(frame_id)
            host._show_status_message(f"帧数 {frame_id} 已复制到剪贴板", 2000)
            host._append_log(f"[复制] 帧数 {frame_id} 已复制到剪贴板")
        else:
            host._append_log("[提示] 当前行未找到 frame_id")

    def _do_locate():
        if host._current_log_path and os.path.exists(host._current_log_path):
            subprocess.run(['explorer', '/select,', host._current_log_path])
        else:
            host._append_log("[提示] 无法定位日志文件")

    action_add_upload_log = Action(FluentIcon.SEND, "添加到上传目录", host)
    action_add_upload_log.triggered.connect(
        lambda: add_file_to_upload(host, host._current_log_path))

    action_copy.triggered.connect(_do_copy)
    action_copy_frame.triggered.connect(_do_copy_frame)
    action_locate.triggered.connect(_do_locate)
    menu.addAction(action_copy)
    menu.addAction(action_copy_frame)
    menu.addSeparator()
    menu.addAction(action_locate)
    menu.addAction(action_add_upload_log)
    host._ctx_log_menu = menu


def build_video_list_menu(host):
    """预构建：日志文件列表右键菜单（触发时动态获取当前项）"""
    menu = _create_menu(parent=host)
    action_copy_name = Action(FluentIcon.COPY, "复制视频名", host)
    action_open_video_loc = Action(FluentIcon.FOLDER, "打开视频位置", host)
    action_copy_video_loc = Action(FluentIcon.LINK, "复制视频位置", host)

    def _do_copy_name():
        item = host.ui.local_video_list.currentItem()
        if not item:
            return
        pure_name = os.path.splitext(item.text())[0]
        QApplication.clipboard().setText(pure_name)
        host._show_status_message(f"文件名 {pure_name} 已复制到剪贴板", 2000)
        host._append_log(f"[复制] 文件名 {pure_name} 已复制到剪贴板")

    def _resolve_video_path():
        """根据当前选中的日志文件名推断视频完整路径"""
        item = host.ui.local_video_list.currentItem()
        if not item:
            return None
        log_filename = item.text()
        video_name = os.path.splitext(log_filename)[0] + '.mp4'
        video_path_primary = os.path.join(host.videos_dir, "videos", video_name)
        if os.path.exists(video_path_primary):
            return video_path_primary
        current_device = host.ui.id_list.currentItem()
        if current_device:
            device_code = current_device.text()
            video_path_device = os.path.join(host.videos_dir, device_code, video_name)
            if os.path.exists(video_path_device):
                return video_path_device
        return video_path_primary

    def _do_open_video_loc():
        video_path = _resolve_video_path()
        if not video_path:
            return
        if os.path.exists(video_path):
            subprocess.run(['explorer', '/select,', video_path])
            host._append_log(f"[定位] 已定位视频: {video_path}")
        else:
            video_dir = os.path.dirname(video_path)
            if os.path.exists(video_dir):
                os.startfile(video_dir)
                host._append_log(f"[定位] 视频不存在，已打开目录: {video_dir}")
            else:
                host._append_log(f"[提示] 视频路径不存在: {video_path}")

    def _do_copy_video_loc():
        video_path = _resolve_video_path()
        if not video_path:
            return
        QApplication.clipboard().setText(video_path)
        host._show_status_message("视频路径已复制到剪贴板", 2000)
        host._append_log(f"[复制] 视频路径: {video_path}")

    def _do_add_to_upload():
        """第二列右键：将当前日志文件（及对应视频）添加到上传目录"""
        item = host.ui.local_video_list.currentItem()
        if not item:
            return
        log_filename = item.text()
        device_item = host.ui.id_list.currentItem()
        if not device_item:
            host._show_info_bar("请先选择设备", "warning")
            return
        device_code = device_item.text()
        # 重建完整路径：日期子目录优先，其次设备根目录
        date_str = host._get_selected_date_str()
        full_path = os.path.join(
            host.videos_dir, device_code, date_str, log_filename)
        if not os.path.exists(full_path):
            alt_path = os.path.join(host.videos_dir, device_code, log_filename)
            if os.path.exists(alt_path):
                full_path = alt_path
        add_file_to_upload(host, full_path)

    action_add_upload = Action(FluentIcon.SEND, "添加到上传目录", host)
    action_add_upload.triggered.connect(_do_add_to_upload)

    action_copy_name.triggered.connect(_do_copy_name)
    action_open_video_loc.triggered.connect(_do_open_video_loc)
    action_copy_video_loc.triggered.connect(_do_copy_video_loc)
    menu.addAction(action_copy_name)
    menu.addSeparator()
    menu.addAction(action_open_video_loc)
    menu.addAction(action_copy_video_loc)
    menu.addSeparator()
    menu.addAction(action_add_upload)
    host._ctx_video_menu = menu

# ---------- 右键触发：直接弹出缓存菜单（零构建开销） ----------


def id_list_context_menu(host, pos):
    """设备列表右键菜单（预构建缓存，直接弹出）"""
    _exec_menu(host._ctx_id_menu, host.ui.id_list.mapToGlobal(pos))


def log_list_context_menu(host, pos):
    """日志内容列表右键菜单（预构建缓存，直接弹出）"""
    if host.ui.log_list.currentItem() is None:
        return
    _exec_menu(host._ctx_log_menu, host.ui.log_list.mapToGlobal(pos))


def local_video_list_context_menu(host, pos):
    """日志文件列表右键菜单（预构建缓存，直接弹出）"""
    if host.ui.local_video_list.currentItem() is None:
        return
    _exec_menu(host._ctx_video_menu, host.ui.local_video_list.mapToGlobal(pos))


def add_file_to_upload(host, file_path):
    """将指定日志/文本文件添加到上传收集目录

    .log 文件走 CollectFilesWorker：连带收集对应视频、detect.bin、
    CPP 日志（daily_*.txt），缺失项完成后警告提示；
    .txt 等其他文件直接复制文件本身（已存在跳过）。
    """
    if not file_path or not os.path.isfile(file_path):
        host._append_log("[上传] 文件不存在或未被选中，无法添加到上传目录")
        host._show_info_bar("文件不存在，无法添加到上传目录", "warning")
        return
    videos_dir = getattr(host, "videos_dir", "") or ""
    if not videos_dir or not os.path.isdir(videos_dir):
        host._show_info_bar("videos_dir 未配置或目录不存在", "warning")
        return
    device_item = host.ui.id_list.currentItem()
    device_code = device_item.text().strip() if device_item else ""
    if not device_code or not os.path.isdir(os.path.join(videos_dir, device_code)):
        host._show_info_bar(f"设备目录不存在，无法收集: {device_code or '未选择设备'}",
                            "warning")
        return

    fname = os.path.basename(file_path)
    if fname.lower().endswith(".log"):
        base = os.path.splitext(fname)[0]
        worker = CollectFilesWorker(videos_dir, device_code, [base])
        worker.done.connect(
            lambda dev, n, miss, w=worker: on_upload_collect_done(host, dev, n, miss, w))
        worker.error.connect(
            lambda msg: host._show_info_bar(msg.split(chr(10))[0], "error", duration=4000))
        if not hasattr(host, "_upload_collect_workers"):
            host._upload_collect_workers = []
        host._upload_collect_workers.append(worker)
        worker.start()
        host._append_log(f"[上传] 正在收集 {device_code}: {base} 的视频/日志...")
        return

    # .txt 等其他文件：异步复制文件本身到 upload/{设备}/
    upload_dir = os.path.join(videos_dir, "upload", device_code)
    os.makedirs(upload_dir, exist_ok=True)
    dst = os.path.join(upload_dir, fname)
    if os.path.exists(dst):
        host._show_info_bar(f"{fname} 已在上传目录中（跳过）", "info")
        host._append_log(f"[上传] 已存在跳过: {dst}")
    else:
        worker = FileCopyWorker(file_path, dst)
        def _on_copy_ok(w=worker, d=dst, n=fname):
            if hasattr(host, '_file_copy_workers') and w in host._file_copy_workers:
                host._file_copy_workers.remove(w)
            host._show_info_bar(f"{n} 已添加到上传目录", "success")
            host._append_log(f"[上传] 已收集: {d}")
        def _on_copy_err(err, w=worker, n=fname):
            if hasattr(host, '_file_copy_workers') and w in host._file_copy_workers:
                host._file_copy_workers.remove(w)
            host._append_log(f"[上传] 收集失败: {err}")
            host._show_info_bar(f"添加到上传目录失败: {err}", "error", duration=4000)
        worker.copy_finished.connect(_on_copy_ok)
        worker.error.connect(_on_copy_err)
        if not hasattr(host, '_file_copy_workers'):
            host._file_copy_workers = []
        host._file_copy_workers.append(worker)
        worker.start()


def on_upload_collect_done(host, device_id, copied, missing, worker):
    """右键收集完成：复制数 + 缺失项警告（视频/日志未找到时明确提示）"""
    workers = getattr(host, "_upload_collect_workers", [])
    if worker in workers:
        workers.remove(worker)
    if missing:
        shown = ", ".join(missing[:3]) + (" ..." if len(missing) > 3 else "")
        host._append_log(
            f"[上传] {device_id} 收集完成: 复制 {copied} 个，缺失 {len(missing)} 个: {shown}")
        host._show_info_bar(
            f"{device_id}: 复制 {copied} 个，缺失 {len(missing)} 个: {shown}",
            "warning", duration=4000)
    else:
        host._append_log(f"[上传] {device_id} 收集完成: 复制 {copied} 个文件到 upload 目录")
        host._show_info_bar(
            f"{device_id}: {copied} 个文件已添加到上传目录", "success")
